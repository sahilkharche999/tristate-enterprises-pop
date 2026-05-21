"""AnnualPackage regeneration flow tests (Phase 4.9 task 142).

After a package is finalized, the operator can spawn a "regeneration" —
a NEW AnnualPackage row that points at the original via
``regen_of_package_id``. The regeneration goes through its own
preflight → approval → finalization cycle and captures its OWN
snapshots; the original package is untouched.

This test verifies the regen workflow:

1. Original package is created, approved, and finalized → snapshots
   frozen + status='finalized'.
2. A regeneration is created with ``regen_of_package_id=original.id``.
3. The original's snapshots remain unchanged.
4. The regeneration goes through its own draft → approved → finalized
   cycle with potentially different snapshots.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.annual_package_service import (
    approve_annual_package,
    create_annual_package,
    finalize_annual_package,
    get_annual_package,
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


_ORIGINAL_INPUTS = {
    "assessment_setup": {"setup_type": "fixed", "monthly_per_unit": "100"},
    "budget": {"line_items": [{"label": "Dues", "amount": "60000"}]},
    "reserve": {"components": []},
    "appendix_manifest": {"appendices": []},
}

_REGEN_INPUTS = {
    "assessment_setup": {"setup_type": "fixed", "monthly_per_unit": "120"},
    "budget": {"line_items": [{"label": "Dues", "amount": "72000"}]},
    "reserve": {"components": []},
    "appendix_manifest": {"appendices": []},
}


class TestRegenerationFlow:
    def test_regen_links_to_original_and_is_independent(self, db):
        pid = _pid(db)

        # 1. Original package: draft → approved → finalized
        original = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        approve_annual_package(
            property_id=pid, package_id=original.package_id,
            approved_assessment_revenue_annual=Decimal("60000"),
            approved_by="ops", connection=db,
        )
        finalize_annual_package(
            property_id=pid, package_id=original.package_id,
            connection=db, **_ORIGINAL_INPUTS,
        )

        original_after_freeze = get_annual_package(
            property_id=pid, package_id=original.package_id, connection=db,
        )
        assert original_after_freeze.status == "finalized"

        # 2. Operator spawns a regeneration
        regen = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026,
            regen_of_package_id=original.package_id,
            connection=db,
        )
        assert regen.regen_of_package_id == original.package_id
        assert regen.status == "draft"
        assert regen.package_id != original.package_id

        # 3. Regen goes through its own approval + finalization with DIFFERENT inputs
        approve_annual_package(
            property_id=pid, package_id=regen.package_id,
            approved_assessment_revenue_annual=Decimal("72000"),
            approved_by="ops", connection=db,
        )
        finalize_annual_package(
            property_id=pid, package_id=regen.package_id,
            connection=db, **_REGEN_INPUTS,
        )

        # 4. Original package's snapshot is UNCHANGED
        original_row = db.execute(
            "SELECT assessment_setup_snapshot_json, budget_snapshot_json, "
            "approved_assessment_revenue_annual "
            "FROM annual_packages WHERE id = ?",
            (original.package_id,),
        ).fetchone()
        regen_row = db.execute(
            "SELECT assessment_setup_snapshot_json, budget_snapshot_json, "
            "approved_assessment_revenue_annual "
            "FROM annual_packages WHERE id = ?",
            (regen.package_id,),
        ).fetchone()

        assert original_row[0] != regen_row[0]  # different setup snapshots
        assert original_row[1] != regen_row[1]  # different budget snapshots
        assert "100" in original_row[0]
        assert "120" in regen_row[0]
        # Approved revenue targets are independent
        assert original_row[2] != regen_row[2]

    def test_regen_cannot_overwrite_original_snapshot(self, db):
        """Finalizing the regen does NOT touch the original row."""
        pid = _pid(db)
        original = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        approve_annual_package(
            property_id=pid, package_id=original.package_id,
            approved_assessment_revenue_annual=Decimal("1000"),
            approved_by="ops", connection=db,
        )
        finalize_annual_package(
            property_id=pid, package_id=original.package_id,
            connection=db, **_ORIGINAL_INPUTS,
        )
        original_version_before = get_annual_package(
            property_id=pid, package_id=original.package_id, connection=db,
        ).version_int

        regen = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026,
            regen_of_package_id=original.package_id, connection=db,
        )
        approve_annual_package(
            property_id=pid, package_id=regen.package_id,
            approved_assessment_revenue_annual=Decimal("2000"),
            approved_by="ops", connection=db,
        )
        finalize_annual_package(
            property_id=pid, package_id=regen.package_id,
            connection=db, **_REGEN_INPUTS,
        )

        original_version_after = get_annual_package(
            property_id=pid, package_id=original.package_id, connection=db,
        ).version_int
        # Original's version_int unchanged by regen's lifecycle
        assert original_version_before == original_version_after
