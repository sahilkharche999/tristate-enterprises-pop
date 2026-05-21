"""Tests for ``package_specs.resolver.resolve``.

The DRE-driven resolver returns the universal ``STANDARD_PACKAGE_SPEC``
for any property that exists in the ``properties`` table. Per-HOA static
data is enriched at compile time from the ``hoa_settings`` /
``properties`` rows; the spec returned here only carries the template
chain plus stamped ``hoa_id`` + ``fiscal_year``. ``UnsupportedHOAError``
only fires when the property row is missing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.disclosure_package.package_specs import (
    OLD_MILL_2026,
    STANDARD_PACKAGE_SPEC,
    UnsupportedHOAError,
    resolve,
    template_for_setup_type,
)


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()


class TestResolveStandard:
    def test_resolves_for_any_hoa(self, db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT INTO properties (name, units, hoa_code) "
            "VALUES ('Old Mill Homeowners Association', 279, '10')"
        )
        property_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()

        spec = resolve(property_id, 2026, connection=db)
        assert spec.hoa_id == property_id
        assert spec.fiscal_year == 2026
        assert len(spec.entries) == len(STANDARD_PACKAGE_SPEC.entries)

    def test_resolves_arbitrary_hoa_name(self, db: sqlite3.Connection) -> None:
        # The DRE-driven resolver returns the universal spec for any
        # HOA — no more name/code gating.
        db.execute(
            "INSERT INTO properties (name, units) "
            "VALUES ('Some Other HOA', 50)"
        )
        property_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()

        spec = resolve(property_id, 2027, connection=db)
        assert spec.hoa_id == property_id
        assert spec.fiscal_year == 2027

    def test_resolver_does_not_mutate_literal(self, db: sqlite3.Connection) -> None:
        # Different property_id calls must not leak into the sentinel literal.
        db.execute("INSERT INTO properties (name, hoa_code) VALUES ('Old Mill', '10')")
        pid_a = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO properties (name, hoa_code) VALUES ('Old Mill Annex', '10b')")
        pid_b = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()

        spec_a = resolve(pid_a, 2026, connection=db)
        spec_b = resolve(pid_b, 2030, connection=db)

        assert spec_a.hoa_id == pid_a
        assert spec_b.hoa_id == pid_b
        # The original literal still has its sentinel values (0/0).
        assert STANDARD_PACKAGE_SPEC.hoa_id == 0
        assert STANDARD_PACKAGE_SPEC.fiscal_year == 0
        # Backward-compat alias points at the same literal.
        assert OLD_MILL_2026 is STANDARD_PACKAGE_SPEC


class TestTemplateForSetupType:
    def test_fixed_maps_to_universal_template(self) -> None:
        assert template_for_setup_type("fixed") == "assessment_schedule/universal.html"

    def test_grouped_maps_to_universal_template(self) -> None:
        assert template_for_setup_type("grouped") == "assessment_schedule/universal.html"

    def test_per_unit_maps_to_universal_template(self) -> None:
        assert template_for_setup_type("per_unit") == "assessment_schedule/universal.html"

    def test_unknown_setup_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown setup_type"):
            template_for_setup_type("not_a_setup_type")


class TestResolveUnsupported:
    def test_missing_property_raises(self, db: sqlite3.Connection) -> None:
        with pytest.raises(UnsupportedHOAError) as ctx:
            resolve(99999, 2026, connection=db)
        assert ctx.value.property_id == 99999
        assert ctx.value.hoa_name is None
