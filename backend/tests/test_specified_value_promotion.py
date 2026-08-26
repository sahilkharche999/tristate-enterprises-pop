"""C7 — specified-value promotion (`specified-value-promotion` spec).

Extraction captures real per-unit specified values as ``pool_factors``
entries with ``factor_type='dollar_amount'``. Promotion must use them when
they pass the 0.5% sum test against a pool total; anything ambiguous falls
back to an equal split explicitly tagged ``source='equal_split_placeholder'``
that blocks package generation until the operator resolves it. An equal
split must never be written as ``source='dre'`` again.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.disclosure_package.preflight import check_specified_value_placeholders
from app.dre_extraction.promotion import (
    EQUAL_SPLIT_PLACEHOLDER_SOURCE,
    parse_extraction_payload,
    populate_setup_children,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()


def _seed(db: sqlite3.Connection) -> tuple[int, int]:
    db.execute("INSERT INTO properties (name, units) VALUES ('Test', 10)")
    pid = db.execute("SELECT id FROM properties").fetchone()[0]
    db.execute(
        "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
        "VALUES (?, 'per_unit', 'per_unit', 'draft')",
        (pid,),
    )
    setup_id = db.execute("SELECT id FROM assessment_setups").fetchone()[0]
    db.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = ?",
        (setup_id, pid),
    )
    db.commit()
    return pid, setup_id


def _payload(*, pool: dict, units: list[dict]) -> str:
    return json.dumps(
        {
            "document_metadata": {"association_name": "Test HOA"},
            "page_inventory": [],
            "assessment_setup": {
                "setup_type": "individual_unit",
                "display_mode": "",
                "summary": "",
                "requires_dre_for_future_years": True,
                "confidence": 0.9,
                "source_pages": [1],
            },
            "unit_structure": {
                "unit_count": len(units),
                "group_count": 0,
                "groups": [],
                "units": units,
            },
            "allocation_pools": [
                {
                    "pool_key": "capital_contribution",
                    "pool_name": "Capital Contribution",
                    "allocation_method": "specified_value",
                    "recipient_scope": "all_units",
                    "denominator_source": "unknown",
                    "included_budget_lines": [],
                    "excluded_budget_lines": [],
                    "source_pages": [],
                    **pool,
                }
            ],
            "formulas": [],
            "reserve_setup": None,
            "validation_checks": [],
            "human_review_questions": [],
            "recommended_saved_setup": None,
        }
    )


def _unit(number: str, dollar: str | None) -> dict:
    unit = {"unit_number": number}
    if dollar is not None:
        unit["pool_factors"] = [
            {
                "pool_key": "capital_contribution",
                "factor_value": dollar,
                "factor_label": "Monthly Capital",
                "factor_type": "dollar_amount",
            }
        ]
    return unit


def _promote(db, setup_id, payload, edited_entity_keys=frozenset()):
    ext = parse_extraction_payload(payload)
    return populate_setup_children(
        setup_id=setup_id,
        setup_type="per_unit",
        extraction=ext,
        connection=db,
        edited_entity_keys=edited_entity_keys,
    )


def _allocation_rows(db, setup_id):
    return db.execute(
        "SELECT au.unit_number, aupa.specified_monthly_amount, aupa.source "
        "FROM assessment_unit_pool_allocations aupa "
        "JOIN assessment_units au ON au.id = aupa.assessment_unit_id "
        "WHERE aupa.assessment_setup_id = ? ORDER BY au.unit_number",
        (setup_id,),
    ).fetchall()


class TestDollarFactorPromotion:
    def test_monthly_form_promotes_verbatim_as_dre(self, db):
        _, setup_id = _seed(db)
        # 145 + 210 = 355 == pool monthly total → monthly form
        payload = _payload(
            pool={"annual_amount": "4260", "monthly_amount": "355"},
            units=[_unit("101", "145"), _unit("102", "210")],
        )
        counts = _promote(db, setup_id, payload)
        rows = _allocation_rows(db, setup_id)
        assert [(r[0], Decimal(r[1]), r[2]) for r in rows] == [
            ("101", Decimal("145.00"), "dre"),
            ("102", Decimal("210.00"), "dre"),
        ]
        assert "specified_value_placeholders" not in counts

    def test_monthly_form_detected_via_annual_total_alone(self, db):
        _, setup_id = _seed(db)
        # No monthly total printed; 355×12 == 4260 annual → monthly form
        payload = _payload(
            pool={"annual_amount": "4260"},
            units=[_unit("101", "145"), _unit("102", "210")],
        )
        _promote(db, setup_id, payload)
        rows = _allocation_rows(db, setup_id)
        assert [Decimal(r[1]) for r in rows] == [Decimal("145.00"), Decimal("210.00")]
        assert {r[2] for r in rows} == {"dre"}

    def test_annual_form_divided_by_twelve(self, db):
        _, setup_id = _seed(db)
        # 1740 + 2520 = 4260 == pool annual total → annual form, ÷12
        payload = _payload(
            pool={"annual_amount": "4260"},
            units=[_unit("101", "1740"), _unit("102", "2520")],
        )
        _promote(db, setup_id, payload)
        rows = _allocation_rows(db, setup_id)
        assert [Decimal(r[1]) for r in rows] == [Decimal("145.00"), Decimal("210.00")]
        assert {r[2] for r in rows} == {"dre"}

    def test_operator_edited_unit_promotes_as_operator(self, db):
        _, setup_id = _seed(db)
        payload = _payload(
            pool={"monthly_amount": "355", "annual_amount": "4260"},
            units=[_unit("101", "145"), _unit("102", "210")],
        )
        _promote(db, setup_id, payload, edited_entity_keys=frozenset({"unit:102"}))
        rows = _allocation_rows(db, setup_id)
        assert rows[0][2] == "dre"
        assert rows[1][2] == "manual"


class TestPlaceholderFallback:
    def test_no_factors_falls_back_to_tagged_placeholder(self, db):
        _, setup_id = _seed(db)
        payload = _payload(
            pool={"annual_amount": "24000"},
            units=[_unit("101", None), _unit("102", None)],
        )
        counts = _promote(db, setup_id, payload)
        rows = _allocation_rows(db, setup_id)
        assert [Decimal(r[1]) for r in rows] == [Decimal("1000.00")] * 2
        assert {r[2] for r in rows} == {EQUAL_SPLIT_PLACEHOLDER_SOURCE}
        placeholders = counts["specified_value_placeholders"]
        assert placeholders[0]["pool_key"] == "capital_contribution"
        assert "no per-unit dollar_amount factors" in placeholders[0]["reason"]

    def test_partial_coverage_never_mixes(self, db):
        _, setup_id = _seed(db)
        payload = _payload(
            pool={"annual_amount": "24000", "monthly_amount": "2000"},
            units=[_unit("101", "145"), _unit("102", None)],
        )
        counts = _promote(db, setup_id, payload)
        rows = _allocation_rows(db, setup_id)
        # every row is a placeholder — extracted and synthetic never mix
        assert {r[2] for r in rows} == {EQUAL_SPLIT_PLACEHOLDER_SOURCE}
        assert "1/2 units" in counts["specified_value_placeholders"][0]["reason"]

    def test_failed_sum_test_falls_back(self, db):
        _, setup_id = _seed(db)
        # Factors sum to 355; pool totals say 9999/500 — matches neither.
        payload = _payload(
            pool={"annual_amount": "9999", "monthly_amount": "500"},
            units=[_unit("101", "145"), _unit("102", "210")],
        )
        counts = _promote(db, setup_id, payload)
        rows = _allocation_rows(db, setup_id)
        assert {r[2] for r in rows} == {EQUAL_SPLIT_PLACEHOLDER_SOURCE}
        assert "matches neither" in counts["specified_value_placeholders"][0]["reason"]

    def test_equal_split_is_never_tagged_dre(self, db):
        # The C7 provenance guarantee for post-change promotions.
        _, setup_id = _seed(db)
        payload = _payload(
            pool={"annual_amount": "24000"},
            units=[_unit("101", None), _unit("102", None)],
        )
        _promote(db, setup_id, payload)
        dre_rows = db.execute(
            "SELECT COUNT(*) FROM assessment_unit_pool_allocations "
            "WHERE assessment_setup_id = ? AND source = 'dre'",
            (setup_id,),
        ).fetchone()[0]
        assert dre_rows == 0


class TestPlaceholderPreflightGate:
    def test_placeholders_block(self, db):
        pid, setup_id = _seed(db)
        payload = _payload(
            pool={"annual_amount": "24000"},
            units=[_unit("101", None), _unit("102", None)],
        )
        _promote(db, setup_id, payload)
        errors = check_specified_value_placeholders(property_id=pid, connection=db)
        assert len(errors) == 1
        assert errors[0].severity == "blocking"
        assert errors[0].code == "specified_value_placeholder"
        assert "capital_contribution" in errors[0].message
        assert "2 unit(s)" in errors[0].message

    def test_operator_resolution_clears_gate(self, db):
        pid, setup_id = _seed(db)
        payload = _payload(
            pool={"annual_amount": "24000"},
            units=[_unit("101", None), _unit("102", None)],
        )
        _promote(db, setup_id, payload)
        db.execute(
            "UPDATE assessment_unit_pool_allocations SET source = 'manual' "
            "WHERE assessment_setup_id = ?",
            (setup_id,),
        )
        assert check_specified_value_placeholders(property_id=pid, connection=db) == []

    def test_real_dollar_factors_never_trip_gate(self, db):
        pid, setup_id = _seed(db)
        payload = _payload(
            pool={"monthly_amount": "355", "annual_amount": "4260"},
            units=[_unit("101", "145"), _unit("102", "210")],
        )
        _promote(db, setup_id, payload)
        assert check_specified_value_placeholders(property_id=pid, connection=db) == []

    def test_gate_scopes_to_default_setup_only(self, db):
        pid, setup_id = _seed(db)
        # a superseded setup with placeholders must not block
        db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (?, 'per_unit', 'per_unit', 'superseded')",
            (pid,),
        )
        old_setup = db.execute(
            "SELECT MAX(id) FROM assessment_setups"
        ).fetchone()[0]
        payload = _payload(
            pool={"annual_amount": "24000"},
            units=[_unit("101", None)],
        )
        _promote(db, old_setup, payload)
        assert check_specified_value_placeholders(property_id=pid, connection=db) == []


class TestSourceCheckMigration:
    """The C7 CHECK-widening table rebuild (database.rebuild_aupa_source_check)."""

    OLD_DDL = """
        CREATE TABLE assessment_unit_pool_allocations (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_unit_id       INTEGER NOT NULL
                                     REFERENCES assessment_units(id) ON DELETE CASCADE,
            assessment_setup_id      INTEGER NOT NULL
                                     REFERENCES assessment_setups(id) ON DELETE CASCADE,
            pool_key                 TEXT NOT NULL,
            pool_id                  INTEGER REFERENCES allocation_pools(id) ON DELETE SET NULL,
            specified_monthly_amount NUMERIC NOT NULL,
            source                   TEXT NOT NULL DEFAULT 'dre'
                                     CHECK (source IN ('dre','manual')),
            source_page              INTEGER,
            notes                    TEXT,
            UNIQUE (assessment_unit_id, pool_key)
        )
    """

    def _old_shape_db(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "old.db"))
        conn.execute("PRAGMA foreign_keys = ON")
        # Full schema first, then swap in the pre-change table shape.
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute("DROP TABLE assessment_unit_pool_allocations")
        conn.execute(self.OLD_DDL)
        conn.execute("INSERT INTO properties (name, units) VALUES ('Legacy', 5)")
        pid = conn.execute("SELECT id FROM properties").fetchone()[0]
        conn.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (?, 'per_unit', 'per_unit', 'approved')",
            (pid,),
        )
        setup_id = conn.execute("SELECT id FROM assessment_setups").fetchone()[0]
        conn.execute(
            "INSERT INTO assessment_units (assessment_setup_id, unit_number, source) "
            "VALUES (?, '101', 'dre')",
            (setup_id,),
        )
        unit_id = conn.execute("SELECT id FROM assessment_units").fetchone()[0]
        conn.execute(
            "INSERT INTO assessment_unit_pool_allocations "
            "(assessment_unit_id, assessment_setup_id, pool_key, "
            " specified_monthly_amount, source, notes) "
            "VALUES (?, ?, 'pool_a', '123.45', 'manual', 'keep me')",
            (unit_id, setup_id),
        )
        conn.commit()
        return conn

    def test_rebuild_preserves_rows_and_widens_check(self, tmp_path):
        from app.ai_implementation.database import rebuild_aupa_source_check

        conn = self._old_shape_db(tmp_path)
        assert rebuild_aupa_source_check(conn) is True
        # existing row survives (NUMERIC affinity may return float —
        # compare value, not storage class)
        row = conn.execute(
            "SELECT pool_key, specified_monthly_amount, source, notes "
            "FROM assessment_unit_pool_allocations"
        ).fetchone()
        assert (row[0], Decimal(str(row[1])), row[2], row[3]) == (
            "pool_a", Decimal("123.45"), "manual", "keep me",
        )
        # the new value is now accepted
        unit_id = conn.execute("SELECT id FROM assessment_units").fetchone()[0]
        setup_id = conn.execute("SELECT id FROM assessment_setups").fetchone()[0]
        conn.execute(
            "INSERT INTO assessment_unit_pool_allocations "
            "(assessment_unit_id, assessment_setup_id, pool_key, "
            " specified_monthly_amount, source) "
            "VALUES (?, ?, 'pool_b', '1.00', 'equal_split_placeholder')",
            (unit_id, setup_id),
        )
        conn.close()

    def test_rebuild_is_run_twice_safe(self, tmp_path):
        from app.ai_implementation.database import rebuild_aupa_source_check

        conn = self._old_shape_db(tmp_path)
        assert rebuild_aupa_source_check(conn) is True
        assert rebuild_aupa_source_check(conn) is False  # second run no-ops
        assert conn.execute(
            "SELECT COUNT(*) FROM assessment_unit_pool_allocations"
        ).fetchone()[0] == 1
        conn.close()

    def test_new_schema_db_is_untouched(self, db):
        from app.ai_implementation.database import rebuild_aupa_source_check

        assert rebuild_aupa_source_check(db) is False


def test_documented_zero_monthly_without_named_homes_is_valid() -> None:
    from app.dre_extraction.promotion import validate_specified_value_pools

    extraction = parse_extraction_payload(
        _payload(
            pool={
                "recipient_scope": "parking_users",
                "selected_unit_numbers": [],
                "annual_amount": None,
                "monthly_amount": "0",
                "amount_availability": "known",
            },
            units=[
                {"unit_number": "201", "parking_flag": ""},
                {"unit_number": "202", "parking_flag": ""},
            ],
        )
    )
    assert extraction is not None
    validations = validate_specified_value_pools(extraction)
    assert validations["capital_contribution"].valid is True
