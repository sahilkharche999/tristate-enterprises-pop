"""AnnualPackage lifecycle tests (Phase 4.8)."""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.annual_package_service import (
    AnnualPackageNotFound,
    InvalidPackageStateTransition,
    approve_annual_package,
    create_annual_package,
    finalize_annual_package,
    get_annual_package,
    list_annual_packages,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('Test', 10)")
    conn.commit()
    yield conn
    conn.close()


def _pid(db: sqlite3.Connection) -> int:
    return db.execute("SELECT id FROM properties").fetchone()[0]


class TestCreate:
    def test_creates_draft(self, db: sqlite3.Connection) -> None:
        r = create_annual_package(
            property_id=_pid(db), budget_year=2026, fiscal_year=2026,
            connection=db,
        )
        assert r.status == "draft"
        assert r.fiscal_year == 2026
        assert r.approved_at is None
        assert r.finalized_at is None
        assert r.version_int == 0

    def test_regen_links_to_predecessor(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        original = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026,
            connection=db,
        )
        regen = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026,
            regen_of_package_id=original.package_id,
            connection=db,
        )
        assert regen.regen_of_package_id == original.package_id


class TestApprove:
    def test_draft_to_approved(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        draft = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        approved = approve_annual_package(
            property_id=pid, package_id=draft.package_id,
            approved_assessment_revenue_annual=Decimal("100000"),
            approved_by="ops@example.com",
            connection=db,
        )
        assert approved.status == "approved"
        assert approved.approved_assessment_revenue_annual == Decimal("100000")
        assert approved.approved_by == "ops@example.com"
        assert approved.approved_at is not None
        assert approved.version_int == draft.version_int + 1

    def test_cannot_approve_finalized(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        draft = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        approve_annual_package(
            property_id=pid, package_id=draft.package_id,
            approved_assessment_revenue_annual=Decimal("1000"),
            approved_by="op", connection=db,
        )
        finalize_annual_package(
            property_id=pid, package_id=draft.package_id,
            assessment_setup={}, budget={}, reserve={}, appendix_manifest={},
            connection=db,
        )
        with pytest.raises(InvalidPackageStateTransition):
            approve_annual_package(
                property_id=pid, package_id=draft.package_id,
                approved_assessment_revenue_annual=Decimal("2000"),
                approved_by="op", connection=db,
            )


class TestFinalize:
    def test_approved_to_finalized_freezes_snapshots(
        self, db: sqlite3.Connection
    ) -> None:
        pid = _pid(db)
        draft = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        approve_annual_package(
            property_id=pid, package_id=draft.package_id,
            approved_assessment_revenue_annual=Decimal("60000"),
            approved_by="op", connection=db,
        )
        final = finalize_annual_package(
            property_id=pid, package_id=draft.package_id,
            assessment_setup={"setup_type": "fixed"},
            budget={"line_items": [{"label": "Dues", "amount": Decimal("60000")}]},
            reserve={"components": []},
            appendix_manifest={"appendices": []},
            connection=db,
        )
        assert final.status == "finalized"
        assert final.finalized_at is not None
        # Snapshots persisted on the row
        row = db.execute(
            "SELECT assessment_setup_snapshot_json, budget_snapshot_json "
            "FROM annual_packages WHERE id = ?",
            (draft.package_id,),
        ).fetchone()
        assert row[0] is not None and "fixed" in row[0]
        assert row[1] is not None and "Dues" in row[1]

    def test_cannot_finalize_draft(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        draft = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        with pytest.raises(InvalidPackageStateTransition):
            finalize_annual_package(
                property_id=pid, package_id=draft.package_id,
                assessment_setup={}, budget={}, reserve={}, appendix_manifest={},
                connection=db,
            )


class TestListAndGet:
    def test_list_returns_newest_first(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        a = create_annual_package(
            property_id=pid, budget_year=2025, fiscal_year=2025, connection=db,
        )
        b = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        rows = list_annual_packages(property_id=pid, connection=db)
        assert [r.package_id for r in rows] == [b.package_id, a.package_id]

    def test_get_missing_raises(self, db: sqlite3.Connection) -> None:
        with pytest.raises(AnnualPackageNotFound):
            get_annual_package(
                property_id=_pid(db), package_id=99999, connection=db,
            )

    def test_get_wrong_property_raises(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        pkg = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        db.execute("INSERT INTO properties (name, units) VALUES ('Other', 5)")
        other_pid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        with pytest.raises(AnnualPackageNotFound):
            get_annual_package(
                property_id=other_pid, package_id=pkg.package_id, connection=db,
            )
