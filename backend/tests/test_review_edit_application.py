"""Tests for applying Review Workbench edits at promotion time (Group 1 of
the make-dre-data-editable change).

Covers apply_review_edits_to_extraction (path resolution, latest-wins,
type coercion, unresolvable-path fail-loud) plus the integrated path
through approve_extraction_run / approve_ccr_extraction_run.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.dre_extraction.promotion import (
    EditedEntityFailedToPromote,
    UnresolvableReviewEdit,
    apply_review_edits_to_extraction,
    entity_keys_touched_by_edits,
    parse_extraction_payload,
    populate_setup_children,
)
from app.services.ccr_approval_service import approve_ccr_extraction_run
from app.services.dre_approval_service import approve_extraction_run
from app.services.dre_review_service import record_review_edit


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('Test', 10)")
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status) "
        "VALUES (?, 'dre/1/x.pdf', 'x.pdf', 'active')",
        (pid,),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO dre_extraction_runs "
        "(dre_document_id, property_id, model_name, prompt_version, prompt_sha256, status) "
        "VALUES (?, ?, 'gemini-flash-latest', '1.0.0', 'abc123', 'succeeded')",
        (doc_id, pid),
    )
    conn.commit()
    yield conn
    conn.close()


def _ids(db: sqlite3.Connection) -> tuple[int, int]:
    pid = db.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]
    rid = db.execute("SELECT id FROM dre_extraction_runs LIMIT 1").fetchone()[0]
    return pid, rid


def _payload_with_one_pool(
    *,
    allocation_method: str = "equal",
    denominator_value: str = "10",
    variable_flag: bool = False,
) -> dict:
    return {
        "document_metadata": {"association_name": "Test HOA"},
        "page_inventory": [],
        "assessment_setup": {
            "setup_type": "fixed_equal",
            "display_mode": "",
            "summary": "",
            "requires_dre_for_future_years": True,
            "confidence": 0.9,
            "source_pages": [1],
        },
        "unit_structure": {
            "unit_count": 10,
            "group_count": 0,
            "groups": [],
            "units": [],
        },
        "allocation_pools": [
            {
                "pool_key": "operating",
                "pool_name": "Operating Expenses",
                "annual_amount": "120000",
                "allocation_method": allocation_method,
                "recipient_scope": "all_units",
                "denominator_label": "units",
                "denominator_value": denominator_value,
                "denominator_source": "dre_shown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "budget_line_derivation": "unknown",
                "residual_after_pool_keys": [],
                "residual_exclusions": [],
                "source_pages": [1],
                "confidence": 0.9,
                "variable_flag": variable_flag,
            }
        ],
        "formulas": [],
        "reserve_setup": None,
        "validation_checks": [],
        "human_review_questions": [],
        "recommended_saved_setup": None,
    }


class TestApplyReviewEditsToExtraction:
    def test_single_edit_applied(self) -> None:
        extraction = parse_extraction_payload(json.dumps(_payload_with_one_pool()))
        assert extraction is not None

        class _Edit:
            field_path = "allocation_pools[0].denominator_value"
            new_value = "25"

        patched = apply_review_edits_to_extraction(extraction, [_Edit()])
        assert patched.allocation_pools[0].denominator_value == Decimal("25")
        # Original extraction is untouched (deep copy).
        assert extraction.allocation_pools[0].denominator_value == Decimal("10")

    def test_latest_of_multiple_edits_to_same_path_wins(self) -> None:
        extraction = parse_extraction_payload(json.dumps(_payload_with_one_pool()))

        class _Edit:
            def __init__(self, value):
                self.field_path = "allocation_pools[0].denominator_value"
                self.new_value = value

        patched = apply_review_edits_to_extraction(
            extraction, [_Edit("25"), _Edit("30")]
        )
        assert patched.allocation_pools[0].denominator_value == Decimal("30")

    def test_no_edits_returns_extraction_unchanged(self) -> None:
        extraction = parse_extraction_payload(json.dumps(_payload_with_one_pool()))
        patched = apply_review_edits_to_extraction(extraction, [])
        assert patched is extraction

    def test_unresolvable_path_raises(self) -> None:
        extraction = parse_extraction_payload(json.dumps(_payload_with_one_pool()))

        class _Edit:
            field_path = "allocation_pools[3].denominator_value"
            new_value = "25"

        with pytest.raises(UnresolvableReviewEdit) as ctx:
            apply_review_edits_to_extraction(extraction, [_Edit()])
        assert "allocation_pools[3].denominator_value" in ctx.value.unresolvable_field_paths

    def test_bool_edit_round_trips(self) -> None:
        extraction = parse_extraction_payload(json.dumps(_payload_with_one_pool()))

        class _Edit:
            field_path = "allocation_pools[0].variable_flag"
            new_value = "True"

        patched = apply_review_edits_to_extraction(extraction, [_Edit()])
        assert patched.allocation_pools[0].variable_flag is True

    def test_decimal_edit_round_trips(self) -> None:
        extraction = parse_extraction_payload(json.dumps(_payload_with_one_pool()))

        class _Edit:
            field_path = "allocation_pools[0].annual_amount"
            new_value = "150000.50"

        patched = apply_review_edits_to_extraction(extraction, [_Edit()])
        assert patched.allocation_pools[0].annual_amount == Decimal("150000.50")


class TestEditedEntityLandingFailure:
    def test_edited_unmappable_allocation_method_raises(self, db: sqlite3.Connection) -> None:
        extraction = parse_extraction_payload(
            json.dumps(_payload_with_one_pool(allocation_method="equal"))
        )

        class _Edit:
            field_path = "allocation_pools[0].allocation_method"
            new_value = "unknown"

        patched = apply_review_edits_to_extraction(extraction, [_Edit()])
        edited_keys = entity_keys_touched_by_edits(patched, [_Edit.field_path])
        assert edited_keys == frozenset({"pool:operating"})

        db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (1, 'fixed', 'fixed', 'draft')"
        )
        setup_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()

        with pytest.raises(EditedEntityFailedToPromote) as ctx:
            populate_setup_children(
                setup_id=setup_id,
                setup_type="fixed",
                extraction=patched,
                connection=db,
                edited_entity_keys=edited_keys,
            )
        assert "pool:operating" in ctx.value.entity_refs

    def test_unedited_unmappable_allocation_method_is_best_effort_skipped(
        self, db: sqlite3.Connection
    ) -> None:
        # Same bad allocation_method, but never touched by an operator edit —
        # existing best-effort skip behavior must be preserved.
        extraction = parse_extraction_payload(
            json.dumps(_payload_with_one_pool(allocation_method="unknown"))
        )
        db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (1, 'fixed', 'fixed', 'draft')"
        )
        setup_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()

        counts = populate_setup_children(
            setup_id=setup_id,
            setup_type="fixed",
            extraction=extraction,
            connection=db,
            edited_entity_keys=frozenset(),
        )
        assert counts["pools"] == 0


class TestApproveExtractionRunAppliesEdits:
    def test_review_edit_changes_promoted_pool(self, db: sqlite3.Connection) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(_payload_with_one_pool(denominator_value="10")), rid),
        )
        db.commit()

        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[0].denominator_value",
            old_value=Decimal("10"),
            new_value=Decimal("42"),
            reason="corrected from source doc",
            edited_by="ops@example.com",
            connection=db,
        )

        resp = approve_extraction_run(
            property_id=pid,
            extraction_run_id=rid,
            setup_type="fixed",
            reviewed_by="ops@example.com",
            connection=db,
        )

        row = db.execute(
            "SELECT denominator_value FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'operating'",
            (resp.promoted_setup_id,),
        ).fetchone()
        # denominator_value has NUMERIC column affinity — SQLite stores the
        # inserted text "42" as an integer.
        assert row[0] == 42

    def test_recipient_scope_edit_changes_promoted_pool(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(_payload_with_one_pool()), rid),
        )
        db.commit()

        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[0].recipient_scope",
            old_value="all_units",
            new_value="residential_only",
            connection=db,
        )

        resp = approve_extraction_run(
            property_id=pid,
            extraction_run_id=rid,
            setup_type="fixed",
            reviewed_by="ops@example.com",
            connection=db,
        )

        row = db.execute(
            "SELECT recipient_scope FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'operating'",
            (resp.promoted_setup_id,),
        ).fetchone()
        assert row[0] == "residential_only"

    def test_denominator_label_edit_changes_promoted_pool(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(_payload_with_one_pool()), rid),
        )
        db.commit()

        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[0].denominator_label",
            old_value="units",
            new_value="Total Livable Square Footage",
            connection=db,
        )

        resp = approve_extraction_run(
            property_id=pid,
            extraction_run_id=rid,
            setup_type="fixed",
            reviewed_by="ops@example.com",
            connection=db,
        )

        row = db.execute(
            "SELECT denominator_label FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'operating'",
            (resp.promoted_setup_id,),
        ).fetchone()
        assert row[0] == "Total Livable Square Footage"

    def test_variable_flag_edit_changes_promoted_pool(
        self, db: sqlite3.Connection
    ) -> None:
        """The proposal's headline bug: variable_flag is the one field with
        a live Review Workbench edit control today, but it has zero effect
        on promotion because _insert_pool hardcoded the column to 0.
        """
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(_payload_with_one_pool(variable_flag=False)), rid),
        )
        db.commit()

        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[0].variable_flag",
            old_value=False,
            new_value=True,
            connection=db,
        )

        resp = approve_extraction_run(
            property_id=pid,
            extraction_run_id=rid,
            setup_type="fixed",
            reviewed_by="ops@example.com",
            connection=db,
        )

        row = db.execute(
            "SELECT variable_flag FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'operating'",
            (resp.promoted_setup_id,),
        ).fetchone()
        assert row[0] == 1

    def test_unresolvable_edit_blocks_promotion_entirely(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(_payload_with_one_pool()), rid),
        )
        db.commit()

        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[9].denominator_value",
            old_value=None,
            new_value=Decimal("42"),
            connection=db,
        )

        with pytest.raises(UnresolvableReviewEdit):
            approve_extraction_run(
                property_id=pid,
                extraction_run_id=rid,
                setup_type="fixed",
                reviewed_by="ops@example.com",
                connection=db,
            )

        assert db.execute(
            "SELECT COUNT(*) FROM assessment_setups WHERE property_id = ?", (pid,)
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT promoted_at FROM dre_extraction_runs WHERE id = ?", (rid,)
        ).fetchone()[0] is None


class TestCCRLayeringOrder:
    def test_review_edit_then_operator_factor_merge(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        payload = {
            "document_metadata": {"association_name": "Test CC&R"},
            "page_inventory": [],
            "assessment_setup": {
                "setup_type": "individual_unit",
                "display_mode": "",
                "summary": "",
                "requires_dre_for_future_years": True,
                "confidence": 0.9,
                "source_pages": [1],
            },
            "unit_structure": {
                "unit_count": 1,
                "group_count": 0,
                "groups": [],
                "units": [
                    {
                        "unit_number": "101",
                        "square_feet": "1000",
                        "ownership_percent": None,
                        "category": "residential",
                        "residential_commercial_flag": "residential",
                        "parking_flag": "",
                        "source_page": 1,
                        "confidence": 0.9,
                        "pool_factors": [],
                    }
                ],
            },
            "allocation_pools": [
                {
                    "pool_key": "operating",
                    "pool_name": "Operating",
                    "annual_amount": "12000",
                    "allocation_method": "square_footage",
                    "recipient_scope": "all_units",
                    "denominator_label": "sqft",
                    "denominator_value": "1000",
                    "denominator_source": "dre_shown",
                    "included_budget_lines": [],
                    "excluded_budget_lines": [],
                    "budget_line_derivation": "unknown",
                    "residual_after_pool_keys": [],
                    "residual_exclusions": [],
                    "source_pages": [1],
                    "confidence": 0.9,
                }
            ],
            "formulas": [],
            "reserve_setup": None,
            "validation_checks": [],
            "human_review_questions": [],
            "recommended_saved_setup": None,
        }
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(payload), rid),
        )
        db.commit()

        # Review edit corrects unit 101's square footage.
        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="unit_structure.units[0].square_feet",
            old_value=Decimal("1000"),
            new_value=Decimal("1200"),
            connection=db,
        )
        # Operator factor adds ownership_percent for the same unit (CC&R
        # per-unit-factor mechanism, separate from review edits).
        db.execute(
            "UPDATE dre_extraction_runs SET operator_unit_factors_json = ? WHERE id = ?",
            (json.dumps({"101": {"ownership_percent": "50"}}), rid),
        )
        db.commit()

        resp = approve_ccr_extraction_run(
            property_id=pid,
            extraction_run_id=rid,
            setup_type="per_unit",
            reviewed_by="ops@example.com",
            connection=db,
        )

        row = db.execute(
            "SELECT square_feet, ownership_percent FROM assessment_units "
            "WHERE assessment_setup_id = ? AND unit_number = '101'",
            (resp.promoted_setup_id,),
        ).fetchone()
        # square_feet / ownership_percent have NUMERIC column affinity.
        assert row[0] == 1200
        assert row[1] == 50
