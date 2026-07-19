"""Tests for post-promotion correction without a new extraction run
(Group 3 of the make-dre-data-editable change).

Covers reopen_and_repromote / reopen_and_repromote_ccr_run: supersedes
the prior setup, carries forward budget mappings, updates
default_assessment_setup_id, and lists affected draft packages.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.dre_extraction.promotion import UnresolvableReviewEdit
from app.services.ccr_approval_service import (
    MissingUnitFactors,
    approve_ccr_extraction_run,
    reopen_and_repromote_ccr_run,
    save_operator_unit_factors,
    CCRUnitFactor,
)
from app.services.dre_approval_service import (
    ExtractionRunNotFound,
    ExtractionRunNotPromoted,
    approve_extraction_run,
    reopen_and_repromote,
)
from tests.support.budget_line_mapping_seed import seed_budget_line_mapping
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


def _payload_with_one_pool(denominator_value: str = "10") -> dict:
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
        "unit_structure": {"unit_count": 10, "group_count": 0, "groups": [], "units": []},
        "allocation_pools": [
            {
                "pool_key": "operating",
                "pool_name": "Operating Expenses",
                "annual_amount": "120000",
                "allocation_method": "equal",
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
            }
        ],
        "formulas": [],
        "reserve_setup": None,
        "validation_checks": [],
        "human_review_questions": [],
        "recommended_saved_setup": None,
    }


class TestReopenAndRepromoteDRE:
    def test_never_promoted_raises(self, db: sqlite3.Connection) -> None:
        pid, rid = _ids(db)
        with pytest.raises(ExtractionRunNotPromoted):
            reopen_and_repromote(
                property_id=pid, extraction_run_id=rid,
                setup_type="fixed", reviewed_by="ops", connection=db,
            )

    def test_missing_run_raises(self, db: sqlite3.Connection) -> None:
        pid, _ = _ids(db)
        with pytest.raises(ExtractionRunNotFound):
            reopen_and_repromote(
                property_id=pid, extraction_run_id=99999,
                setup_type="fixed", reviewed_by="ops", connection=db,
            )

    def test_supersedes_prior_setup_and_applies_new_edit(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(_payload_with_one_pool()), rid),
        )
        db.commit()

        first = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="ops1", connection=db,
        )
        original_setup_id = first.promoted_setup_id

        # Add a correction after the original promotion.
        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[0].denominator_value",
            old_value=Decimal("10"),
            new_value=Decimal("99"),
            connection=db,
        )

        second = reopen_and_repromote(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="ops2", connection=db,
        )

        assert second.promoted_setup_id != original_setup_id
        assert second.superseded_setup_id == original_setup_id

        statuses = dict(
            db.execute(
                "SELECT id, status FROM assessment_setups WHERE property_id = ?", (pid,)
            ).fetchall()
        )
        assert statuses[original_setup_id] == "superseded"
        assert statuses[second.promoted_setup_id] == "approved"

        pool_row = db.execute(
            "SELECT denominator_value FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'operating'",
            (second.promoted_setup_id,),
        ).fetchone()
        assert pool_row[0] == 99

        default_setup = db.execute(
            "SELECT default_assessment_setup_id FROM properties WHERE id = ?", (pid,)
        ).fetchone()[0]
        assert default_setup == second.promoted_setup_id

    def test_carries_forward_budget_line_mappings(self, db: sqlite3.Connection) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(_payload_with_one_pool()), rid),
        )
        db.commit()

        first = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="ops1", connection=db,
        )
        seed_budget_line_mapping(
            connection=db,
            property_id=pid,
            assessment_setup_id=first.promoted_setup_id,
            normalized_label="insurance",
            section="operating",
            category="operating",
            fund_type="operating",
            pool_key="operating",
            approved_by="ops@example.com",
        )

        second = reopen_and_repromote(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="ops2", connection=db,
        )

        rows = db.execute(
            "SELECT budget_line_normalized_label, pool_key FROM budget_line_pool_mappings "
            "WHERE property_id = ? AND assessment_setup_id = ?",
            (pid, second.promoted_setup_id),
        ).fetchall()
        assert rows == [("insurance", "operating")]

    def test_lists_affected_draft_packages(self, db: sqlite3.Connection) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(_payload_with_one_pool()), rid),
        )
        db.commit()

        first = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="ops1", connection=db,
        )
        db.execute(
            "INSERT INTO annual_packages "
            "(property_id, assessment_setup_id, budget_year, fiscal_year, status) "
            "VALUES (?, ?, 2026, 2026, 'draft')",
            (pid, first.promoted_setup_id),
        )
        draft_package_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        # A finalized package should NOT show up in the affected list.
        db.execute(
            "INSERT INTO annual_packages "
            "(property_id, assessment_setup_id, budget_year, fiscal_year, status) "
            "VALUES (?, ?, 2025, 2025, 'finalized')",
            (pid, first.promoted_setup_id),
        )
        db.commit()

        second = reopen_and_repromote(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="ops2", connection=db,
        )

        assert second.affected_draft_package_ids == [draft_package_id]

    def test_unresolvable_edit_blocks_repromotion(self, db: sqlite3.Connection) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(_payload_with_one_pool()), rid),
        )
        db.commit()
        approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="ops1", connection=db,
        )
        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[9].denominator_value",
            old_value=None,
            new_value=Decimal("1"),
            connection=db,
        )
        with pytest.raises(UnresolvableReviewEdit):
            reopen_and_repromote(
                property_id=pid, extraction_run_id=rid,
                setup_type="fixed", reviewed_by="ops2", connection=db,
            )


class TestReopenAndRepromoteCCR:
    def _payload_with_unit(self) -> dict:
        return {
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

    def test_repromote_reenforces_missing_unit_factors_guard(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        # No per-unit data at all. First promotion as 'fixed' bypasses the
        # per_unit-only missing-factors check entirely.
        payload = self._payload_with_unit()
        payload["unit_structure"]["units"] = []
        payload["unit_structure"]["unit_count"] = 0
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(payload), rid),
        )
        db.commit()
        approve_ccr_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="ops1", connection=db,
        )

        # Operator decides this should actually be a per_unit setup and
        # reopens — the guard must re-run on repromotion, not just replay
        # whatever the original (non-per_unit) promotion skipped.
        with pytest.raises(MissingUnitFactors):
            reopen_and_repromote_ccr_run(
                property_id=pid, extraction_run_id=rid,
                setup_type="per_unit", reviewed_by="ops2", connection=db,
            )

    def test_repromote_layers_operator_factors_over_review_edits(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (json.dumps(self._payload_with_unit()), rid),
        )
        db.commit()
        first = approve_ccr_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="per_unit", reviewed_by="ops1", connection=db,
        )

        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="unit_structure.units[0].square_feet",
            old_value=Decimal("1000"),
            new_value=Decimal("1500"),
            connection=db,
        )
        save_operator_unit_factors(
            extraction_run_id=rid, property_id=pid,
            factors=[CCRUnitFactor(unit_number="101", ownership_percent=Decimal("75"))],
            connection=db,
        )
        db.commit()

        second = reopen_and_repromote_ccr_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="per_unit", reviewed_by="ops2", connection=db,
        )
        assert second.superseded_setup_id == first.promoted_setup_id

        row = db.execute(
            "SELECT square_feet, ownership_percent FROM assessment_units "
            "WHERE assessment_setup_id = ? AND unit_number = '101'",
            (second.promoted_setup_id,),
        ).fetchone()
        assert row[0] == 1500
        assert row[1] == 75
