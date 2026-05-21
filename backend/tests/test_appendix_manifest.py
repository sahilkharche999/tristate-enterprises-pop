"""Tests for the compile-time appendix manifest resolver (Phase 5.4 task 159).

Verifies that:
- With no per-package overrides, the resolver falls back to every
  active include_by_default=1 appendix for the property, ordered.
- With per-package overrides present, the junction rows ARE the
  manifest (defaults are not merged in).
- ``included=0`` junction rows are filtered out.
- ``override_display_title`` and the junction ``display_order`` override
  the defaults.
- Retired appendices that ARE referenced by an override still surface
  (prior-year packages stay reproducible).
- Cross-property isolation: junction rows for a different property's
  package don't leak.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.disclosure_package.appendix_manifest import (
    ResolvedAppendix,
    resolve_appendix_manifest,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('Test HOA', 10)")
    conn.commit()
    yield conn
    conn.close()


def _pid(db: sqlite3.Connection) -> int:
    return db.execute("SELECT id FROM properties").fetchone()[0]


def _insert_appendix(
    db: sqlite3.Connection,
    *,
    property_id: int,
    file_name: str,
    display_title: str,
    default_display_order: int = 0,
    include_by_default: int = 1,
    status: str = "active",
) -> int:
    cur = db.execute(
        """
        INSERT INTO appendix_documents (
            property_id, file_id, file_name, display_title,
            default_display_order, include_by_default, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            property_id,
            f"appendices/{property_id}/{file_name}",
            file_name,
            display_title,
            default_display_order,
            include_by_default,
            status,
        ),
    )
    db.commit()
    return cur.lastrowid


def _insert_package(db: sqlite3.Connection, *, property_id: int) -> int:
    cur = db.execute(
        "INSERT INTO annual_packages (property_id, budget_year, fiscal_year, status) "
        "VALUES (?, 2026, 2026, 'draft')",
        (property_id,),
    )
    db.commit()
    return cur.lastrowid


def _insert_junction(
    db: sqlite3.Connection,
    *,
    package_id: int,
    appendix_id: int,
    display_order: int = 0,
    included: int = 1,
    override_display_title: str | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO annual_package_appendices (
            package_id, appendix_id, display_order, included, override_display_title
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (package_id, appendix_id, display_order, included, override_display_title),
    )
    db.commit()


class TestFallbackToDefaults:
    def test_no_junction_returns_include_by_default_set(self, db):
        pid = _pid(db)
        a = _insert_appendix(
            db, property_id=pid, file_name="rules.pdf",
            display_title="Election Rules", default_display_order=2,
        )
        b = _insert_appendix(
            db, property_id=pid, file_name="bylaws.pdf",
            display_title="Bylaws", default_display_order=1,
        )
        pkg = _insert_package(db, property_id=pid)

        result = resolve_appendix_manifest(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert [r.appendix_id for r in result] == [b, a]
        assert all(r.source == "default" for r in result)
        assert result[0].display_title == "Bylaws"
        assert result[0].display_order == 1

    def test_exclude_by_default_rows(self, db):
        pid = _pid(db)
        included = _insert_appendix(
            db, property_id=pid, file_name="x.pdf", display_title="X",
        )
        _insert_appendix(
            db, property_id=pid, file_name="y.pdf", display_title="Y",
            include_by_default=0,
        )
        pkg = _insert_package(db, property_id=pid)

        result = resolve_appendix_manifest(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert [r.appendix_id for r in result] == [included]

    def test_exclude_retired_rows(self, db):
        pid = _pid(db)
        active = _insert_appendix(
            db, property_id=pid, file_name="a.pdf", display_title="A",
        )
        _insert_appendix(
            db, property_id=pid, file_name="r.pdf", display_title="R",
            status="retired",
        )
        pkg = _insert_package(db, property_id=pid)

        result = resolve_appendix_manifest(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert [r.appendix_id for r in result] == [active]

    def test_preview_path_package_id_none(self, db):
        pid = _pid(db)
        a = _insert_appendix(
            db, property_id=pid, file_name="x.pdf", display_title="X",
        )
        result = resolve_appendix_manifest(
            property_id=pid, package_id=None, connection=db,
        )
        assert [r.appendix_id for r in result] == [a]


class TestPerPackageOverrides:
    def test_junction_rows_are_the_manifest(self, db):
        pid = _pid(db)
        # Two defaults; junction rows pick only one and add reorder
        a = _insert_appendix(
            db, property_id=pid, file_name="a.pdf", display_title="A",
            default_display_order=1,
        )
        b = _insert_appendix(
            db, property_id=pid, file_name="b.pdf", display_title="B",
            default_display_order=2,
        )
        pkg = _insert_package(db, property_id=pid)
        # operator reverses the order via junction rows
        _insert_junction(db, package_id=pkg, appendix_id=a, display_order=20)
        _insert_junction(db, package_id=pkg, appendix_id=b, display_order=10)

        result = resolve_appendix_manifest(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert [r.appendix_id for r in result] == [b, a]
        assert all(r.source == "override" for r in result)

    def test_included_zero_row_is_dropped(self, db):
        pid = _pid(db)
        a = _insert_appendix(
            db, property_id=pid, file_name="a.pdf", display_title="A",
        )
        b = _insert_appendix(
            db, property_id=pid, file_name="b.pdf", display_title="B",
        )
        pkg = _insert_package(db, property_id=pid)
        _insert_junction(db, package_id=pkg, appendix_id=a, included=1)
        _insert_junction(db, package_id=pkg, appendix_id=b, included=0)

        result = resolve_appendix_manifest(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert [r.appendix_id for r in result] == [a]

    def test_override_display_title_wins(self, db):
        pid = _pid(db)
        a = _insert_appendix(
            db, property_id=pid, file_name="a.pdf", display_title="Default Title",
        )
        pkg = _insert_package(db, property_id=pid)
        _insert_junction(
            db, package_id=pkg, appendix_id=a,
            override_display_title="Custom Title For 2026",
        )

        result = resolve_appendix_manifest(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert result[0].display_title == "Custom Title For 2026"

    def test_override_display_title_null_falls_back_to_default(self, db):
        pid = _pid(db)
        a = _insert_appendix(
            db, property_id=pid, file_name="a.pdf", display_title="Default Title",
        )
        pkg = _insert_package(db, property_id=pid)
        _insert_junction(db, package_id=pkg, appendix_id=a)

        result = resolve_appendix_manifest(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert result[0].display_title == "Default Title"

    def test_retired_appendix_referenced_by_override_still_renders(self, db):
        """Prior-year package must keep rendering even after appendix retired."""
        pid = _pid(db)
        a = _insert_appendix(
            db, property_id=pid, file_name="old.pdf", display_title="Old Insurance",
            status="retired",
        )
        pkg = _insert_package(db, property_id=pid)
        _insert_junction(db, package_id=pkg, appendix_id=a, display_order=0)

        result = resolve_appendix_manifest(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert [r.appendix_id for r in result] == [a]


class TestIsolation:
    def test_other_property_package_does_not_leak(self, db):
        pid = _pid(db)
        a = _insert_appendix(
            db, property_id=pid, file_name="a.pdf", display_title="A",
        )
        # second property with its own appendix + package + junction
        db.execute("INSERT INTO properties (name, units) VALUES ('Other HOA', 5)")
        other_pid = db.execute(
            "SELECT id FROM properties WHERE name = 'Other HOA'"
        ).fetchone()[0]
        other_a = _insert_appendix(
            db, property_id=other_pid, file_name="o.pdf", display_title="O",
        )
        other_pkg = _insert_package(db, property_id=other_pid)
        _insert_junction(db, package_id=other_pkg, appendix_id=other_a)

        # request manifest for the FIRST property; should NOT see the
        # second property's override (which would also wrongly suppress
        # the include_by_default fallback).
        result = resolve_appendix_manifest(
            property_id=pid, package_id=other_pkg, connection=db,
        )
        assert [r.appendix_id for r in result] == [a]
        assert result[0].source == "default"

    def test_deterministic_order_when_same_display_order(self, db):
        pid = _pid(db)
        a = _insert_appendix(
            db, property_id=pid, file_name="a.pdf", display_title="A",
        )
        b = _insert_appendix(
            db, property_id=pid, file_name="b.pdf", display_title="B",
        )
        pkg = _insert_package(db, property_id=pid)
        _insert_junction(db, package_id=pkg, appendix_id=b, display_order=5)
        _insert_junction(db, package_id=pkg, appendix_id=a, display_order=5)

        result = resolve_appendix_manifest(
            property_id=pid, package_id=pkg, connection=db,
        )
        # Same display_order → tie-broken by appendix_id ascending
        assert [r.appendix_id for r in result] == [a, b]
