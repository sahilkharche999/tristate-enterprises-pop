"""Budget line auto-mapping tests (Phase 4.3 tasks 108-111)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.budget_line_mapping_service import (
    BudgetLineKey,
    MappingSuggestion,
    approve_mapping,
    bulk_approve_mappings,
    carry_forward_mappings_across_setups,
    diff_budget_lines_against_mappings,
    lookup_saved_mappings,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('A', 10)")
    pid = conn.execute("SELECT id FROM properties").fetchone()[0]
    conn.execute(
        "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
        "VALUES (?, 'fixed', 'fixed', 'approved')",
        (pid,),
    )
    yield conn
    conn.close()


def _ids(db):
    pid = db.execute("SELECT id FROM properties").fetchone()[0]
    sid = db.execute("SELECT id FROM assessment_setups").fetchone()[0]
    return pid, sid


class TestApproveMapping:
    def test_inserts_new_mapping(self, db):
        pid, sid = _ids(db)
        key = BudgetLineKey(
            normalized_label="management fee", section="ops", category="operating",
            fund_type="operating", account_code="50050",
        )
        approve_mapping(
            property_id=pid, assessment_setup_id=sid, line_key=key,
            pool_key="operating", approved_by="ops", connection=db,
        )
        row = db.execute(
            "SELECT pool_key, approved_by FROM budget_line_pool_mappings"
        ).fetchone()
        assert row == ("operating", "ops")

    def test_upsert_existing_mapping_replaces_pool(self, db):
        pid, sid = _ids(db)
        key = BudgetLineKey(
            normalized_label="mgmt", section="ops", category="operating",
            fund_type="operating",
        )
        approve_mapping(
            property_id=pid, assessment_setup_id=sid, line_key=key,
            pool_key="pool_a", approved_by="op1", connection=db,
        )
        approve_mapping(
            property_id=pid, assessment_setup_id=sid, line_key=key,
            pool_key="pool_b", approved_by="op2", connection=db,
        )
        rows = db.execute(
            "SELECT pool_key FROM budget_line_pool_mappings"
        ).fetchall()
        assert rows == [("pool_b",)]


class TestDiffBudgetLines:
    def test_routes_to_saved_mapping_first(self, db):
        pid, sid = _ids(db)
        key = BudgetLineKey(
            normalized_label="management fee", section="ops", category="operating",
            fund_type="operating",
        )
        approve_mapping(
            property_id=pid, assessment_setup_id=sid, line_key=key,
            pool_key="operating", approved_by="ops", connection=db,
        )
        saved = lookup_saved_mappings(
            property_id=pid, assessment_setup_id=sid, connection=db,
        )
        statuses = diff_budget_lines_against_mappings(
            budget_lines=[{
                "normalized_label": "management fee", "label": "Management Fee",
                "section": "ops", "category": "operating",
                "fund_type": "operating", "amount": 6000.0,
            }],
            saved_mappings=saved,
            pool_suggestions={},
        )
        assert len(statuses) == 1
        assert statuses[0].status == "mapped"
        assert statuses[0].saved_pool_key == "operating"

    def test_routes_to_ai_suggestion_when_unmapped(self, db):
        statuses = diff_budget_lines_against_mappings(
            budget_lines=[{
                "normalized_label": "elevator maintenance",
                "label": "Elevator Maintenance",
                "section": "ops", "category": "operating",
                "fund_type": "operating", "amount": 12000.0,
            }],
            saved_mappings={},
            pool_suggestions={
                "elevator maintenance": [
                    MappingSuggestion(
                        pool_key="elevator", pool_name="Elevator",
                        confidence=0.92, reason="DRE included it in elevator pool",
                    ),
                ],
            },
        )
        assert statuses[0].status == "suggested"
        assert statuses[0].suggestions[0].pool_key == "elevator"

    def test_marks_unmapped_when_no_saved_and_no_suggestion(self, db):
        statuses = diff_budget_lines_against_mappings(
            budget_lines=[{
                "normalized_label": "mystery line",
                "label": "Mystery", "section": "ops",
                "category": "operating", "fund_type": "operating",
                "amount": 100.0,
            }],
            saved_mappings={},
            pool_suggestions={},
        )
        assert statuses[0].status == "unmapped"


class TestBulkApprove:
    def test_saves_many_in_one_call(self, db):
        pid, sid = _ids(db)
        approvals = [
            (BudgetLineKey(
                normalized_label=f"line {i}", section="ops",
                category="operating", fund_type="operating",
            ), "operating")
            for i in range(5)
        ]
        count = bulk_approve_mappings(
            property_id=pid, assessment_setup_id=sid,
            approvals=approvals, approved_by="ops", connection=db,
        )
        assert count == 5
        total = db.execute(
            "SELECT COUNT(*) FROM budget_line_pool_mappings"
        ).fetchone()[0]
        assert total == 5


class TestCarryForward:
    def test_copies_mappings_to_new_setup(self, db):
        pid, sid = _ids(db)
        # Saved mapping under old setup
        approve_mapping(
            property_id=pid, assessment_setup_id=sid,
            line_key=BudgetLineKey(
                normalized_label="mgmt", section="ops",
                category="operating", fund_type="operating",
            ),
            pool_key="operating", approved_by="ops", connection=db,
        )
        # Create a new setup (supersession)
        db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (?, 'grouped', 'grouped', 'approved')",
            (pid,),
        )
        new_sid = db.execute(
            "SELECT id FROM assessment_setups WHERE setup_type='grouped'"
        ).fetchone()[0]

        carried = carry_forward_mappings_across_setups(
            property_id=pid, old_setup_id=sid,
            new_setup_id=new_sid, connection=db,
        )
        assert carried == 1
        # New setup has the mapping
        rows = db.execute(
            "SELECT pool_key FROM budget_line_pool_mappings "
            "WHERE assessment_setup_id = ?",
            (new_sid,),
        ).fetchall()
        assert rows == [("operating",)]
