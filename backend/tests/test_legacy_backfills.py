"""Tests for legacy budget-line backfill (Phase 1.4 tasks 21 + 22)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.ai_implementation.legacy_backfills import (
    LEGACY_PROMOTION_MARKER,
    backfill_legacy_budget_audit_fields,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('A', 5)")
    pid = conn.execute("SELECT id FROM properties").fetchone()[0]
    conn.execute(
        "INSERT INTO budget_drafts "
        "(property_id, status, actor_name, line_items_json) "
        "VALUES (?, 'active', 'test', ?)",
        (
            pid,
            json.dumps([
                {"label": "Dues", "amount": 60000},  # legacy: missing audit fields
                {"label": "Insurance", "amount": 5000},
            ]),
        ),
    )
    conn.commit()
    yield conn
    conn.close()


def _draft_lines(db) -> list[dict]:
    row = db.execute("SELECT line_items_json FROM budget_drafts LIMIT 1").fetchone()
    return json.loads(row[0])


class TestBackfill:
    def test_stamps_legacy_marker_on_missing_source_column(self, db):
        updated = backfill_legacy_budget_audit_fields(db)
        assert updated == 1
        lines = _draft_lines(db)
        for li in lines:
            assert li["source_column"] == LEGACY_PROMOTION_MARKER
            assert li["source_page_or_cell"] == "legacy"

    def test_does_not_overwrite_existing_audit_fields(self, db):
        # Stamp one line with real audit data manually
        db.execute(
            "UPDATE budget_drafts SET line_items_json = ?",
            (
                json.dumps([
                    {"label": "Dues", "amount": 60000,
                     "source_column": "annual_budget",
                     "source_page_or_cell": "B14"},
                    {"label": "Insurance", "amount": 5000},  # still legacy
                ]),
            ),
        )
        db.commit()
        updated = backfill_legacy_budget_audit_fields(db)
        # 1 row updated; the second line gets the legacy stamp but the first
        # keeps its real audit data
        assert updated == 1
        lines = _draft_lines(db)
        assert lines[0]["source_column"] == "annual_budget"
        assert lines[0]["source_page_or_cell"] == "B14"
        assert lines[1]["source_column"] == LEGACY_PROMOTION_MARKER

    def test_idempotent_when_re_run(self, db):
        backfill_legacy_budget_audit_fields(db)
        # second run is a no-op (no draft rows need backfill)
        assert backfill_legacy_budget_audit_fields(db) == 0

    def test_handles_unparseable_line_items_json(self, db):
        db.execute("UPDATE budget_drafts SET line_items_json = ?", ("{not-json",))
        db.commit()
        # Doesn't raise — logs a warning and skips
        result = backfill_legacy_budget_audit_fields(db)
        assert result == 0


class TestBackfillEquivalence:
    """Task 22: backfill leaves the engine consuming the same values.

    The engine consumes ``line.amount`` directly; the audit fields are
    never read by the engine (only by the audit log / review UI).
    Backfill therefore MUST NOT change the ``amount`` field.
    """

    def test_amount_unchanged_after_backfill(self, db):
        original = _draft_lines(db)
        backfill_legacy_budget_audit_fields(db)
        updated = _draft_lines(db)
        # Same labels, same amounts; only audit fields changed
        assert [li["label"] for li in original] == [li["label"] for li in updated]
        assert [li["amount"] for li in original] == [li["amount"] for li in updated]
