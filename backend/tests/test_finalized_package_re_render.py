"""Re-render-of-finalized-package byte-equal tests (Phase 4.9 tasks 137 + 141).

When a package transitions to ``status='finalized'``, all four snapshot
JSON columns are frozen on the row. A later re-render MUST load from
those frozen snapshots so live edits don't bleed into the previously-
finalized package output.

This test verifies the SNAPSHOT half of the byte-equal contract:
freezing the same payload twice produces byte-identical column values.
The PDF byte-equal half lives in
``test_disclosure_package_raster_diff.py::test_raster_diff_each_generated_page``
which needs the golden PDF on disk.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.disclosure_package.snapshots import (
    freeze_package_snapshots,
    load_package_snapshots,
    serialize_snapshot,
)
from app.services.annual_package_service import (
    approve_annual_package,
    create_annual_package,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('A', 5)")
    yield conn
    conn.close()


def _pid(db) -> int:
    return db.execute("SELECT id FROM properties").fetchone()[0]


_SAMPLE_INPUTS = {
    "assessment_setup": {
        "setup_type": "fixed",
        "pools": [
            {"pool_key": "operating", "annual_total": Decimal("60000")},
        ],
    },
    "budget": {
        "line_items": [
            {
                "label": "Dues", "amount": Decimal("60000"),
                "source_column": "annual_budget",
                "source_page_or_cell": "B14",
            },
        ],
    },
    "reserve": {
        "components": [
            {"line_item": "Roof", "remaining_life": 10, "useful_life": 20},
        ],
    },
    "appendix_manifest": {
        "appendices": [
            {"appendix_id": 1, "display_title": "Election Rules", "order": 0},
        ],
    },
}


class TestSerializerDeterminism:
    def test_serialize_snapshot_byte_equal_across_two_calls(self):
        s1 = serialize_snapshot(_SAMPLE_INPUTS["assessment_setup"])
        s2 = serialize_snapshot(_SAMPLE_INPUTS["assessment_setup"])
        assert s1 == s2

    def test_serialize_snapshot_byte_equal_with_decimal_audit_fields(self):
        """Decimal values + audit fields must serialize identically."""
        s1 = serialize_snapshot(_SAMPLE_INPUTS["budget"])
        s2 = serialize_snapshot(_SAMPLE_INPUTS["budget"])
        assert s1 == s2
        # Decimals are coerced to strings
        assert '"amount":"60000"' in s1
        # Audit fields round-trip
        assert "source_column" in s1
        assert "source_page_or_cell" in s1


class TestFreezeBytEqualReplay:
    """Task 137: freezing the same payload twice produces byte-equal columns."""

    def test_two_freezes_of_same_payload_produce_equal_columns(self, db):
        pid = _pid(db)
        # Create + approve two separate packages with the same inputs
        a = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        b = create_annual_package(
            property_id=pid, budget_year=2027, fiscal_year=2027, connection=db,
        )
        approve_annual_package(
            property_id=pid, package_id=a.package_id,
            approved_assessment_revenue_annual=Decimal("60000"),
            approved_by="ops", connection=db,
        )
        approve_annual_package(
            property_id=pid, package_id=b.package_id,
            approved_assessment_revenue_annual=Decimal("60000"),
            approved_by="ops", connection=db,
        )

        freeze_package_snapshots(
            package_id=a.package_id, connection=db, **_SAMPLE_INPUTS,
        )
        freeze_package_snapshots(
            package_id=b.package_id, connection=db, **_SAMPLE_INPUTS,
        )

        row_a = db.execute(
            "SELECT assessment_setup_snapshot_json, budget_snapshot_json, "
            "reserve_snapshot_json, appendix_manifest_snapshot_json "
            "FROM annual_packages WHERE id = ?",
            (a.package_id,),
        ).fetchone()
        row_b = db.execute(
            "SELECT assessment_setup_snapshot_json, budget_snapshot_json, "
            "reserve_snapshot_json, appendix_manifest_snapshot_json "
            "FROM annual_packages WHERE id = ?",
            (b.package_id,),
        ).fetchone()
        # All four snapshot columns are byte-equal between packages
        for col_a, col_b in zip(row_a, row_b):
            assert col_a == col_b


class TestLiveEditsDoNotBleedIntoFinalizedSnapshots:
    """Task 141: after finalize, editing the live state must NOT change
    what re-renders see — the snapshot is frozen.

    This test exercises the storage-side: write snapshot, mutate
    'live' state (simulated by writing a *different* AssessmentSetup
    row), reload snapshot — the snapshot still matches the frozen
    payload.
    """

    def test_finalized_snapshot_immutable_against_subsequent_writes(self, db):
        pid = _pid(db)
        pkg = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        approve_annual_package(
            property_id=pid, package_id=pkg.package_id,
            approved_assessment_revenue_annual=Decimal("60000"),
            approved_by="ops", connection=db,
        )
        freeze_package_snapshots(
            package_id=pkg.package_id, connection=db, **_SAMPLE_INPUTS,
        )

        loaded_before = load_package_snapshots(
            package_id=pkg.package_id, connection=db,
        )

        # Simulate live-state drift: write some other row that would
        # appear in the live-state path (no UPDATE of the package
        # itself — that would require its own If-Match + state-change)
        db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (?, 'grouped', 'grouped', 'approved')",
            (pid,),
        )
        db.commit()

        loaded_after = load_package_snapshots(
            package_id=pkg.package_id, connection=db,
        )

        # The snapshot payload is unchanged regardless of unrelated
        # live-state writes.
        assert loaded_before["assessment_setup"] == loaded_after["assessment_setup"]
        assert loaded_before["budget"] == loaded_after["budget"]
        assert loaded_before["status"] == "finalized"
