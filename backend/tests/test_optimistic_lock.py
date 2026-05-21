"""Optimistic-lock helper tests (Phase 5.9 task 180)."""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.optimistic_lock import (
    VersionMismatchError,
    parse_if_match,
    require_if_match,
)
from app.services.annual_package_service import (
    PackageVersionMismatch,
    approve_annual_package,
    create_annual_package,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


class TestParseIfMatch:
    def test_strips_double_quotes(self):
        assert parse_if_match('"42"') == 42

    def test_strips_single_quotes(self):
        assert parse_if_match("'13'") == 13

    def test_unquoted_integer(self):
        assert parse_if_match("7") == 7

    def test_strips_whitespace(self):
        assert parse_if_match('  "5"  ') == 5

    def test_rejects_empty(self):
        with pytest.raises(HTTPException) as ctx:
            parse_if_match("")
        assert ctx.value.status_code == 412

    def test_rejects_quoted_empty(self):
        with pytest.raises(HTTPException) as ctx:
            parse_if_match('""')
        assert ctx.value.status_code == 412

    def test_rejects_non_integer(self):
        with pytest.raises(HTTPException) as ctx:
            parse_if_match('"abc"')
        assert ctx.value.status_code == 412


class TestRequireIfMatch:
    def test_missing_raises_428(self):
        with pytest.raises(HTTPException) as ctx:
            require_if_match(if_match=None)
        assert ctx.value.status_code == 428

    def test_present_returns_int(self):
        assert require_if_match(if_match='"99"') == 99


class TestVersionMismatchError:
    def test_carries_context(self):
        err = VersionMismatchError(
            table="annual_packages", row_id=1, expected=0, actual=2,
        )
        assert err.table == "annual_packages"
        assert err.expected == 0
        assert err.actual == 2
        assert "version mismatch" in str(err)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('A', 5)")
    conn.commit()
    yield conn
    conn.close()


class TestAnnualPackageOptimisticLock:
    def test_correct_version_succeeds(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        pkg = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        # Draft starts at version_int=0
        approved = approve_annual_package(
            property_id=pid, package_id=pkg.package_id,
            approved_assessment_revenue_annual=Decimal("1000"),
            approved_by="op", connection=db,
            expected_version=0,
        )
        assert approved.status == "approved"

    def test_stale_version_raises_mismatch(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        pkg = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        # Bump version_int to simulate concurrent edit
        db.execute(
            "UPDATE annual_packages SET version_int = version_int + 1 WHERE id = ?",
            (pkg.package_id,),
        )
        db.commit()
        with pytest.raises(PackageVersionMismatch) as ctx:
            approve_annual_package(
                property_id=pid, package_id=pkg.package_id,
                approved_assessment_revenue_annual=Decimal("1000"),
                approved_by="op", connection=db,
                expected_version=0,
            )
        assert ctx.value.expected == 0
        assert ctx.value.actual == 1

    def test_none_expected_version_skips_check(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        pkg = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        # Mismatch in DB, but caller didn't supply expected_version → no check
        db.execute(
            "UPDATE annual_packages SET version_int = 99 WHERE id = ?",
            (pkg.package_id,),
        )
        db.commit()
        approved = approve_annual_package(
            property_id=pid, package_id=pkg.package_id,
            approved_assessment_revenue_annual=Decimal("1000"),
            approved_by="op", connection=db,
            expected_version=None,
        )
        assert approved.status == "approved"
