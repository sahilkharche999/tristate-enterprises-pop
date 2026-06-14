from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.disclosure_package.snapshots import load_package_snapshots
from app.services.annual_package_service import (
    approve_annual_package,
    create_annual_package,
    finalize_annual_package,
    list_annual_packages,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(
        "INSERT INTO properties (id, name, units, assessment_mode) VALUES (1, 'A', 5, 'variable')"
    )
    conn.commit()
    yield conn
    conn.close()


_FINALIZE_INPUTS = {
    "assessment_setup": {"setup_type": "grouped"},
    "budget": {"line_items": [{"label": "Dues", "amount": "6000"}]},
    "reserve": {"components": []},
    "appendix_manifest": {"appendices": []},
}


def test_non_finalized_package_becomes_recheck_required_after_mode_change(
    db: sqlite3.Connection,
):
    create_annual_package(
        property_id=1,
        budget_year=2026,
        fiscal_year=2026,
        connection=db,
    )

    db.execute("UPDATE properties SET assessment_mode = 'fixed' WHERE id = 1")
    db.commit()

    packages = list_annual_packages(property_id=1, connection=db)
    assert packages[0].assessment_mode == "variable"
    assert packages[0].live_assessment_mode == "fixed"
    assert packages[0].package_impact == "recheck_required"
    assert "assessment mode" in (packages[0].package_impact_reason or "").lower()


def test_finalized_package_requires_regeneration_after_later_mode_change(
    db: sqlite3.Connection,
):
    package = create_annual_package(
        property_id=1,
        budget_year=2026,
        fiscal_year=2026,
        connection=db,
    )
    approve_annual_package(
        property_id=1,
        package_id=package.package_id,
        approved_assessment_revenue_annual=Decimal("6000"),
        approved_by="ops",
        connection=db,
    )
    finalize_annual_package(
        property_id=1,
        package_id=package.package_id,
        connection=db,
        **_FINALIZE_INPUTS,
    )

    db.execute("UPDATE properties SET assessment_mode = 'fixed' WHERE id = 1")
    db.commit()

    packages = list_annual_packages(property_id=1, connection=db)
    assert packages[0].status == "finalized"
    assert packages[0].assessment_mode == "variable"
    assert packages[0].live_assessment_mode == "fixed"
    assert packages[0].package_impact == "regeneration_required"

    snapshots = load_package_snapshots(package_id=package.package_id, connection=db)
    assert snapshots["status"] == "finalized"
    assert snapshots["assessment_setup"]["assessment_mode"] == "variable"


def test_finalize_syncs_live_assessment_mode_and_regen_draft_uses_new_mode(
    db: sqlite3.Connection,
):
    package = create_annual_package(
        property_id=1,
        budget_year=2026,
        fiscal_year=2026,
        connection=db,
    )
    approve_annual_package(
        property_id=1,
        package_id=package.package_id,
        approved_assessment_revenue_annual=Decimal("6000"),
        approved_by="ops",
        connection=db,
    )

    db.execute("UPDATE properties SET assessment_mode = 'fixed' WHERE id = 1")
    db.commit()

    finalized = finalize_annual_package(
        property_id=1,
        package_id=package.package_id,
        connection=db,
        **_FINALIZE_INPUTS,
    )
    assert finalized.assessment_mode == "fixed"
    assert finalized.live_assessment_mode == "fixed"
    assert finalized.package_impact == "none"

    db.execute("UPDATE properties SET assessment_mode = 'variable' WHERE id = 1")
    db.commit()

    regen = create_annual_package(
        property_id=1,
        budget_year=2026,
        fiscal_year=2026,
        regen_of_package_id=package.package_id,
        connection=db,
    )
    assert regen.regen_of_package_id == package.package_id
    assert regen.assessment_mode == "variable"
    assert regen.live_assessment_mode == "variable"
