"""DRE approval service tests (Phase 4.2 + 5.9)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services.dre_approval_service import (
    ExtractionRunAlreadyPromoted,
    ExtractionRunNotApprovable,
    ExtractionRunNotFound,
    approve_extraction_run,
)
from tests.support.budget_line_mapping_seed import seed_budget_line_mapping


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
    # Create a DRE document + run for the property
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


def _parsed_json_with_pool_hints() -> str:
    return json.dumps(
        {
            "document_metadata": {
                "association_name": "Test",
                "total_units": 20,
                "source_pages": [1],
            },
            "assessment_setup": {
                "setup_type": "grouped_category",
                "display_mode": "grouped",
                "source_pages": [6],
            },
            "unit_structure": {
                "unit_count": 20,
                "group_count": 0,
                "groups": [],
                "units": [],
            },
            "allocation_pools": [
                {
                    "pool_key": "total_budget_prorated",
                    "parent_pool_key": "total_budget",
                    "pool_name": "Prorated",
                    "annual_amount": "24642",
                    "monthly_amount": "2054",
                    "allocation_method": "square_footage",
                    "recipient_scope": "all_units",
                    "denominator_label": "Total livable square footage",
                    "denominator_value": "25462",
                    "denominator_source": "dre_shown",
                    "included_budget_lines": ["Insurance", "Domestic Water"],
                    "excluded_budget_lines": [],
                    "budget_line_derivation": "explicit_lines",
                    "residual_after_pool_keys": [],
                    "residual_exclusions": [],
                    "source_pages": [6],
                    "confidence": 0.95,
                },
                {
                    "pool_key": "total_budget_equal",
                    "parent_pool_key": "total_budget",
                    "pool_name": "Equal",
                    "annual_amount": "102451",
                    "monthly_amount": "8538",
                    "allocation_method": "equal",
                    "recipient_scope": "all_units",
                    "denominator_label": "units",
                    "denominator_value": "20",
                    "denominator_source": "dre_shown",
                    "included_budget_lines": [],
                    "excluded_budget_lines": [],
                    "budget_line_derivation": "residual_default",
                    "residual_after_pool_keys": ["total_budget_prorated"],
                    "residual_exclusions": ["income_only"],
                    "source_pages": [6],
                    "confidence": 0.95,
                },
            ],
            "formulas": [],
            "validation_checks": [],
            "human_review_questions": [],
        }
    )


class TestApproveSuccess:
    def test_creates_assessment_setup_and_marks_run_promoted(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        resp = approve_extraction_run(
            property_id=pid,
            extraction_run_id=rid,
            setup_type="grouped",
            reviewed_by="ops@example.com",
            connection=db,
        )
        assert resp.extraction_run_id == rid
        assert resp.promoted_setup_id > 0
        assert resp.setup_type == "grouped"
        assert resp.promoted_at  # non-empty timestamp
        assert resp.reviewed_by == "ops@example.com"

        # AssessmentSetup row is approved with correct setup_type
        setup_row = db.execute(
            "SELECT setup_type, display_mode, status FROM assessment_setups "
            "WHERE id = ?",
            (resp.promoted_setup_id,),
        ).fetchone()
        assert setup_row == ("grouped", "grouped", "approved")

        # Extraction run is marked promoted with promoted_at set
        run_row = db.execute(
            "SELECT review_status, promoted_setup_id, promoted_at, reviewed_by "
            "FROM dre_extraction_runs WHERE id = ?",
            (rid,),
        ).fetchone()
        assert run_row[0] == "promoted"
        assert run_row[1] == resp.promoted_setup_id
        assert run_row[2] is not None
        assert run_row[3] == "ops@example.com"

    def test_supersedes_prior_approved_setup(self, db: sqlite3.Connection) -> None:
        pid, rid = _ids(db)
        # Pre-seed an already-approved setup that should be superseded
        db.execute(
            "INSERT INTO assessment_setups "
            "(property_id, setup_type, display_mode, status, approved_at) "
            "VALUES (?, 'fixed', 'fixed', 'approved', datetime('now'))",
            (pid,),
        )
        prior_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()

        resp = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="per_unit", reviewed_by="op",
            connection=db,
        )
        prior_status = db.execute(
            "SELECT status FROM assessment_setups WHERE id = ?", (prior_id,),
        ).fetchone()[0]
        new_status = db.execute(
            "SELECT status FROM assessment_setups WHERE id = ?",
            (resp.promoted_setup_id,),
        ).fetchone()[0]
        assert prior_status == "superseded"
        assert new_status == "approved"

    def test_carries_forward_budget_line_mappings_from_prior_setup(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        db.execute(
            "INSERT INTO assessment_setups "
            "(property_id, setup_type, display_mode, status, approved_at) "
            "VALUES (?, 'grouped', 'grouped', 'approved', datetime('now'))",
            (pid,),
        )
        prior_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        seed_budget_line_mapping(
            connection=db,
            property_id=pid,
            assessment_setup_id=prior_id,
            normalized_label="insurance",
            section="operating",
            category="operating",
            fund_type="operating",
            pool_key="variable_costs",
            approved_by="ops@example.com",
        )

        resp = approve_extraction_run(
            property_id=pid,
            extraction_run_id=rid,
            setup_type="grouped",
            reviewed_by="ops@example.com",
            connection=db,
        )

        rows = db.execute(
            """
            SELECT budget_line_normalized_label, pool_key
              FROM budget_line_pool_mappings
             WHERE property_id = ? AND assessment_setup_id = ?
            """,
            (pid, resp.promoted_setup_id),
        ).fetchall()
        assert rows == [("insurance", "variable_costs")]

    def test_approval_creates_reusable_mapping_rules_from_parsed_json(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (_parsed_json_with_pool_hints(), rid),
        )
        db.commit()

        resp = approve_extraction_run(
            property_id=pid,
            extraction_run_id=rid,
            setup_type="grouped",
            reviewed_by="ops@example.com",
            connection=db,
        )

        rows = db.execute(
            """
            SELECT pool_key, match_label, match_type, rule_source,
                   budget_line_derivation, residual_after_pool_keys_json
              FROM assessment_budget_mapping_rules
             WHERE assessment_setup_id = ?
             ORDER BY pool_key, match_label
            """,
            (resp.promoted_setup_id,),
        ).fetchall()
        assert rows[0][:5] == (
            "total_budget_equal",
            None,
            "remainder",
            "system_remainder",
            "residual_default",
        )
        assert json.loads(rows[0][5]) == ["total_budget_prorated"]
        assert rows[1][:5] == (
            "total_budget_prorated",
            "Domestic Water",
            "exact_label",
            "dre_included_budget_line",
            "explicit_lines",
        )
        assert rows[2][:5] == (
            "total_budget_prorated",
            "Insurance",
            "exact_label",
            "dre_included_budget_line",
            "explicit_lines",
        )

    def test_approval_preserves_residual_derivation_on_live_pools(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET parsed_json = ? WHERE id = ?",
            (_parsed_json_with_pool_hints(), rid),
        )
        db.commit()

        resp = approve_extraction_run(
            property_id=pid,
            extraction_run_id=rid,
            setup_type="grouped",
            reviewed_by="ops@example.com",
            connection=db,
        )

        row = db.execute(
            """
            SELECT budget_line_derivation, residual_after_pool_keys_json,
                   residual_exclusions_json
              FROM allocation_pools
             WHERE assessment_setup_id = ?
               AND pool_key = 'total_budget_equal'
            """,
            (resp.promoted_setup_id,),
        ).fetchone()
        assert row[0] == "residual_default"
        assert json.loads(row[1]) == ["total_budget_prorated"]
        assert json.loads(row[2]) == ["income_only"]


class TestConcurrentApproveProtection:
    def test_second_call_raises_already_promoted(self, db: sqlite3.Connection) -> None:
        pid, rid = _ids(db)
        approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="op1", connection=db,
        )
        with pytest.raises(ExtractionRunAlreadyPromoted) as ctx:
            approve_extraction_run(
                property_id=pid, extraction_run_id=rid,
                setup_type="fixed", reviewed_by="op2", connection=db,
            )
        assert ctx.value.extraction_run_id == rid
        assert ctx.value.promoted_setup_id is not None

    def test_second_call_does_not_create_duplicate_setup(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="op1", connection=db,
        )
        try:
            approve_extraction_run(
                property_id=pid, extraction_run_id=rid,
                setup_type="fixed", reviewed_by="op2", connection=db,
            )
        except ExtractionRunAlreadyPromoted:
            pass
        # Only one approved setup exists for the property
        approved_count = db.execute(
            "SELECT COUNT(*) FROM assessment_setups "
            "WHERE property_id = ? AND status = 'approved'",
            (pid,),
        ).fetchone()[0]
        assert approved_count == 1


class TestErrorCases:
    def test_missing_run_raises_not_found(self, db: sqlite3.Connection) -> None:
        pid, _ = _ids(db)
        with pytest.raises(ExtractionRunNotFound):
            approve_extraction_run(
                property_id=pid, extraction_run_id=99999,
                setup_type="fixed", reviewed_by="op", connection=db,
            )

    def test_run_for_wrong_property_raises_not_found(
        self, db: sqlite3.Connection
    ) -> None:
        _, rid = _ids(db)
        db.execute("INSERT INTO properties (name, units) VALUES ('Other', 5)")
        wrong_pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        with pytest.raises(ExtractionRunNotFound):
            approve_extraction_run(
                property_id=wrong_pid, extraction_run_id=rid,
                setup_type="fixed", reviewed_by="op", connection=db,
            )

    def test_rejected_run_raises_not_approvable(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        db.execute(
            "UPDATE dre_extraction_runs SET review_status = 'rejected' WHERE id = ?",
            (rid,),
        )
        db.commit()
        with pytest.raises(ExtractionRunNotApprovable):
            approve_extraction_run(
                property_id=pid, extraction_run_id=rid,
                setup_type="fixed", reviewed_by="op", connection=db,
            )

    def test_invalid_setup_type_raises_value_error(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        with pytest.raises(ValueError, match="Unknown setup_type"):
            approve_extraction_run(
                property_id=pid, extraction_run_id=rid,
                setup_type="weird",  # type: ignore[arg-type]
                reviewed_by="op", connection=db,
            )

    def test_promoted_setup_links_to_source_dre_document(
        self, db: sqlite3.Connection
    ) -> None:
        pid, rid = _ids(db)
        resp = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="op", connection=db,
        )
        # source_dre_document_id matches the run's dre_document_id
        run_doc_id = db.execute(
            "SELECT dre_document_id FROM dre_extraction_runs WHERE id = ?",
            (rid,),
        ).fetchone()[0]
        setup_doc_id = db.execute(
            "SELECT source_dre_document_id FROM assessment_setups WHERE id = ?",
            (resp.promoted_setup_id,),
        ).fetchone()[0]
        assert setup_doc_id == run_doc_id
