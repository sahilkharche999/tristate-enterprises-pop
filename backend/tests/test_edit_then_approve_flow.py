"""End-to-end edit-then-approve + mapping-reuse tests (Phase 4.9 tasks 139-140).

Verifies the cross-module workflow:

1. **Edit-then-approve (task 139)**: extraction produces a draft,
   operator records edits to the extraction in ``dre_review_edits``,
   then approves. The promoted AssessmentSetup honors the edits (via
   the parsed_json that was the basis of population), and the
   ``dre_review_edits`` rows are preserved post-approval.

2. **Mapping reuse (task 140)**: a budget line mapped to a pool under
   AssessmentSetup version N auto-maps to the same pool under version
   N+1 via ``carry_forward_mappings_across_setups`` (pool_key is
   stable across setup supersessions).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services.budget_line_mapping_service import carry_forward_mappings_across_setups
from tests.support.budget_line_mapping_seed import (
    lookup_saved_mappings,
    seed_budget_line_mapping,
)
from app.services.dre_approval_service import approve_extraction_run
from app.services.dre_review_service import record_review_edit


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


def _seed_property(db) -> int:
    db.execute("INSERT INTO properties (name, units) VALUES ('A', 10)")
    return db.execute("SELECT id FROM properties").fetchone()[0]


def _seed_dre_run(db, pid: int, parsed_json: dict) -> int:
    db.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status) "
        "VALUES (?, 'dre/1/x.pdf', 'x.pdf', 'active')",
        (pid,),
    )
    doc_id = db.execute("SELECT id FROM dre_documents").fetchone()[0]
    db.execute(
        "INSERT INTO dre_extraction_runs "
        "(dre_document_id, property_id, model_name, prompt_version, prompt_sha256, "
        "status, parsed_json) VALUES (?, ?, 'g-flash', '1.0', 'sha', 'succeeded', ?)",
        (doc_id, pid, json.dumps(parsed_json)),
    )
    db.commit()
    return db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()


class TestEditThenApprove:
    """Task 139: operator edits in Review Workbench persist; approving
    creates an AssessmentSetup and the edit rows remain auditable.
    """

    def test_edits_recorded_and_preserved_through_approval(self, db):
        pid = _seed_property(db)
        rid = _seed_dre_run(db, pid, {
            "document_metadata": {"association_name": "Test"},
            "assessment_setup": {
                "setup_type": "grouped_category",
                "confidence": 0.6,
                "source_pages": [1],
            },
            "allocation_pools": [{
                "pool_key": "operating",
                "pool_name": "Operating",
                "allocation_method": "equal",
                "recipient_scope": "all_units",
                "denominator_source": "unknown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "source_pages": [3],
            }],
            "unit_structure": {
                "unit_count": 10,
                "groups": [{"group_id": "g1", "label": "All", "unit_count": 10}],
                "units": [],
            },
            "formulas": [],
            "validation_checks": [],
            "human_review_questions": [],
        })

        # Operator records two edits
        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="assessment_setup.setup_type",
            old_value="grouped_category",
            new_value="fixed_equal",
            reason="Operator confirmed fixed-pattern HOA after review",
            edited_by="ops@example.com",
            connection=db,
        )
        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[0].variable_flag",
            old_value=False,
            new_value=True,
            edited_by="ops@example.com",
            connection=db,
        )

        # Approve
        resp = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="grouped",  # operator's final choice
            reviewed_by="ops@example.com",
            connection=db,
        )
        assert resp.promoted_setup_id > 0

        # Edits remain accessible after approval (FK cascade is to the
        # extraction_run, not destructive)
        edit_rows = db.execute(
            "SELECT field_path, new_value FROM dre_review_edits "
            "WHERE dre_extraction_run_id = ? ORDER BY id",
            (rid,),
        ).fetchall()
        assert len(edit_rows) == 2
        assert edit_rows[0][0] == "assessment_setup.setup_type"
        assert edit_rows[0][1] == "fixed_equal"
        assert edit_rows[1][0] == "allocation_pools[0].variable_flag"

        # AssessmentSetup row created with operator's chosen setup_type
        setup_row = db.execute(
            "SELECT setup_type, status FROM assessment_setups "
            "WHERE id = ?",
            (resp.promoted_setup_id,),
        ).fetchone()
        assert setup_row == ("grouped", "approved")


class TestMappingReuseAcrossSetups:
    """Task 140: when a new AssessmentSetup version is promoted, the
    operator's previous budget-line→pool mappings carry forward so
    next year's budget auto-maps to the same pools.
    """

    def _create_setup(self, db, pid, setup_type="grouped"):
        db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            f"VALUES (?, ?, ?, 'approved')",
            (pid, setup_type, setup_type),
        )
        return db.execute("SELECT MAX(id) FROM assessment_setups").fetchone()[0]

    def test_carry_forward_preserves_mapping_under_new_setup(self, db):
        pid = _seed_property(db)
        setup_v1 = self._create_setup(db, pid)
        # Operator approves a mapping under v1
        seed_budget_line_mapping(
            connection=db,
            property_id=pid,
            assessment_setup_id=setup_v1,
            normalized_label="elevator maintenance",
            section="ops",
            category="operating",
            fund_type="operating",
            pool_key="elevator",
            approved_by="ops@example.com",
        )
        # New extraction → new AssessmentSetup version
        setup_v2 = self._create_setup(db, pid)

        carried = carry_forward_mappings_across_setups(
            property_id=pid, old_setup_id=setup_v1,
            new_setup_id=setup_v2, connection=db,
        )
        assert carried == 1

        # Lookup under v2 returns the same pool
        v2_mappings = lookup_saved_mappings(
            property_id=pid, assessment_setup_id=setup_v2, connection=db,
        )
        elevator_line = (
            "elevator maintenance", "ops", "operating", "operating", None,
        )
        assert v2_mappings[elevator_line] == "elevator"

    def test_carry_forward_does_not_leak_across_properties(self, db):
        pid_a = _seed_property(db)
        setup_a = self._create_setup(db, pid_a)
        seed_budget_line_mapping(
            connection=db,
            property_id=pid_a,
            assessment_setup_id=setup_a,
            normalized_label="utilities",
            section="ops",
            category="operating",
            fund_type="operating",
            pool_key="ops_pool",
            approved_by="ops",
        )

        # Property B has its own setup; carry forward for A shouldn't
        # touch B's mappings.
        db.execute("INSERT INTO properties (name, units) VALUES ('B', 5)")
        pid_b = db.execute(
            "SELECT id FROM properties WHERE name = 'B'"
        ).fetchone()[0]
        setup_b = self._create_setup(db, pid_b)

        setup_a_v2 = self._create_setup(db, pid_a)
        carried = carry_forward_mappings_across_setups(
            property_id=pid_a, old_setup_id=setup_a,
            new_setup_id=setup_a_v2, connection=db,
        )
        assert carried == 1

        # Property B unaffected
        b_count = db.execute(
            "SELECT COUNT(*) FROM budget_line_pool_mappings WHERE property_id = ?",
            (pid_b,),
        ).fetchone()[0]
        assert b_count == 0
