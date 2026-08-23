"""C8 — ownership-percent form normalization (`ownership-percent-normalization` spec).

Percent form is resolved COLUMN-level (by sum, plus the fraction≤1 validity
constraint), never by the retired per-value ``>1`` guess. Promotion stores
normalized fractions; the matrix read path applies the same resolver so
legacy verbatim-stored points rows keep rendering correctly with no
backfill; ambiguity blocks with an operator-facing error instead of a
guessed render.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.assessment_engine.percent_form import (
    AmbiguousPercentColumn,
    FRACTION_DIVISOR,
    POINTS_DIVISOR,
    normalize_percent_value,
    resolve_percent_divisor,
)
from app.dre_extraction.promotion import (
    AmbiguousOwnershipPercentForm,
    parse_extraction_payload,
    populate_setup_children,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


# ---------------------------------------------------------------------------
# The shared resolver
# ---------------------------------------------------------------------------


class TestResolvePercentDivisor:
    def test_fraction_column(self):
        values = [Decimal("0.5"), Decimal("0.3"), Decimal("0.2")]
        assert resolve_percent_divisor(values) == FRACTION_DIVISOR

    def test_points_column(self):
        values = [Decimal("50"), Decimal("30"), Decimal("20")]
        assert resolve_percent_divisor(values) == POINTS_DIVISOR

    def test_the_reviews_100x_case_sub_one_points_column(self):
        # 150 units printing ~0.667 POINTS each (sum ≈ 100). The retired
        # per-value guess kept each as the fraction 0.667 → 66.7% per unit,
        # a ~100× over-assessment. Column sum resolves it correctly.
        values = [Decimal("0.667")] * 150
        assert resolve_percent_divisor(values) == POINTS_DIVISOR

    def test_any_value_above_one_proves_points_even_partial(self):
        # A fraction can never exceed 1.0, so 13.15 proves points form even
        # though this partial two-unit column sums nowhere near 100.
        values = [Decimal("13.15"), Decimal("12.02")]
        assert resolve_percent_divisor(values) == POINTS_DIVISOR

    def test_sub_scope_fraction_column_is_verbatim(self):
        # Commercial-only pool: shares sum to the commercial portion (0.25).
        values = [Decimal("0.15"), Decimal("0.10")]
        assert resolve_percent_divisor(values) == FRACTION_DIVISOR

    def test_ambiguous_all_sub_one_summing_above_band_raises(self):
        # Every value ≤ 1 but the sum (3.4) fits neither interpretation.
        values = [Decimal("0.85")] * 4
        with pytest.raises(AmbiguousPercentColumn) as exc_info:
            resolve_percent_divisor(values, column_label="test_col")
        assert "test_col" in str(exc_info.value)
        assert "3.40" in str(exc_info.value)

    def test_forced_form_short_circuits(self):
        values = [Decimal("0.85")] * 4  # ambiguous without the decision
        assert resolve_percent_divisor(values, forced_form="points") == POINTS_DIVISOR
        assert resolve_percent_divisor(values, forced_form="fraction") == FRACTION_DIVISOR

    def test_empty_column_is_none(self):
        assert resolve_percent_divisor([None, None]) is None
        assert resolve_percent_divisor([]) is None

    def test_normalize_value_is_none_safe(self):
        assert normalize_percent_value(None, POINTS_DIVISOR) is None
        assert normalize_percent_value(Decimal("13.15"), None) == Decimal("13.15")
        assert normalize_percent_value(Decimal("13.15"), POINTS_DIVISOR) == Decimal("0.1315")


# ---------------------------------------------------------------------------
# Promotion write path
# ---------------------------------------------------------------------------


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
    db.commit()
    return pid, setup_id


def _payload(
    *,
    units: list[dict] | None = None,
    groups: list[dict] | None = None,
    allocation_method: str = "ownership_percentage",
    ownership_percent_form: str = "unknown",
) -> str:
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
                "unit_count": len(units or []),
                "group_count": len(groups or []),
                "groups": groups or [],
                "units": units or [],
                "ownership_percent_form": ownership_percent_form,
            },
            "allocation_pools": [
                {
                    "pool_key": "common",
                    "pool_name": "Common",
                    "annual_amount": "12000",
                    "allocation_method": allocation_method,
                    "recipient_scope": "all_units",
                    "denominator_source": "unknown",
                    "included_budget_lines": [],
                    "excluded_budget_lines": [],
                    "source_pages": [],
                }
            ],
            "formulas": [],
            "reserve_setup": None,
            "validation_checks": [],
            "human_review_questions": [],
            "recommended_saved_setup": None,
        }
    )


def _promote(db, setup_id, payload, setup_type="per_unit"):
    ext = parse_extraction_payload(payload)
    return populate_setup_children(
        setup_id=setup_id, setup_type=setup_type,
        extraction=ext, connection=db,
    )


def _stored_percents(db, setup_id):
    return [
        Decimal(str(row[0]))
        for row in db.execute(
            "SELECT ownership_percent FROM assessment_units "
            "WHERE assessment_setup_id = ? ORDER BY unit_number",
            (setup_id,),
        ).fetchall()
    ]


class TestPromotionNormalization:
    def test_points_column_stored_as_fractions(self, db):
        _, setup_id = _seed(db)
        payload = _payload(units=[
            {"unit_number": "101", "ownership_percent": "60"},
            {"unit_number": "102", "ownership_percent": "40"},
        ])
        _promote(db, setup_id, payload)
        assert _stored_percents(db, setup_id) == [Decimal("0.6"), Decimal("0.4")]

    def test_fraction_column_stored_verbatim(self, db):
        _, setup_id = _seed(db)
        payload = _payload(units=[
            {"unit_number": "101", "ownership_percent": "0.6"},
            {"unit_number": "102", "ownership_percent": "0.4"},
        ])
        _promote(db, setup_id, payload)
        assert _stored_percents(db, setup_id) == [Decimal("0.6"), Decimal("0.4")]

    def test_partial_points_column_normalized_via_gt_one_rule(self, db):
        _, setup_id = _seed(db)
        payload = _payload(units=[
            {"unit_number": "101", "ownership_percent": "13.15"},
            {"unit_number": "102", "ownership_percent": "12.02"},
        ])
        _promote(db, setup_id, payload)
        assert _stored_percents(db, setup_id) == [
            Decimal("0.1315"), Decimal("0.1202"),
        ]

    def test_ambiguous_with_ownership_pool_blocks(self, db):
        _, setup_id = _seed(db)
        payload = _payload(units=[
            {"unit_number": f"1{i:02d}", "ownership_percent": "0.85"}
            for i in range(4)
        ])
        with pytest.raises(AmbiguousOwnershipPercentForm) as exc_info:
            _promote(db, setup_id, payload)
        assert exc_info.value.total == Decimal("3.40")
        # nothing promoted
        assert db.execute(
            "SELECT COUNT(*) FROM assessment_units WHERE assessment_setup_id = ?",
            (setup_id,),
        ).fetchone()[0] == 0

    def test_operator_form_decision_unblocks(self, db):
        _, setup_id = _seed(db)
        payload = _payload(
            units=[
                {"unit_number": f"1{i:02d}", "ownership_percent": "0.85"}
                for i in range(4)
            ],
            ownership_percent_form="points",
        )
        _promote(db, setup_id, payload)
        assert _stored_percents(db, setup_id) == [Decimal("0.0085")] * 4

    def test_ambiguous_without_ownership_pool_stores_verbatim(self, db):
        _, setup_id = _seed(db)
        payload = _payload(
            units=[
                {"unit_number": f"1{i:02d}", "ownership_percent": "0.85", "square_feet": "1000"}
                for i in range(4)
            ],
            allocation_method="square_footage",
        )
        _promote(db, setup_id, payload)  # display-only column: no block
        assert _stored_percents(db, setup_id) == [Decimal("0.85")] * 4

    def test_grouped_points_column_normalized(self, db):
        _, setup_id = _seed(db)
        payload = _payload(groups=[
            {"group_id": "a", "label": "Plan A", "unit_count": 5, "ownership_percent": "60"},
            {"group_id": "b", "label": "Plan B", "unit_count": 5, "ownership_percent": "40"},
        ])
        _promote(db, setup_id, payload, setup_type="grouped")
        rows = [
            Decimal(str(r[0]))
            for r in db.execute(
                "SELECT ownership_percent FROM assessment_groups "
                "WHERE assessment_setup_id = ? ORDER BY display_order",
                (setup_id,),
            ).fetchall()
        ]
        assert rows == [Decimal("0.6"), Decimal("0.4")]


# ---------------------------------------------------------------------------
# Matrix read path — legacy verbatim rows + ambiguity degradation
# ---------------------------------------------------------------------------

from types import SimpleNamespace

from app.disclosure_package.assessment_schedule_matrix import (
    build_matrix_from_approved_assessment_setup,
)

_MATRIX_DDL = """
    CREATE TABLE assessment_setups (
        id INTEGER PRIMARY KEY, property_id INTEGER, setup_type TEXT,
        status TEXT, approved_at TEXT
    );
    CREATE TABLE dre_extraction_runs (
        id INTEGER PRIMARY KEY, property_id INTEGER, promoted_setup_id INTEGER,
        parsed_json TEXT, promoted_at TEXT, document_type TEXT
    );
    CREATE TABLE dre_review_edits (
        id INTEGER PRIMARY KEY, dre_extraction_run_id INTEGER, field_path TEXT,
        old_value TEXT, new_value TEXT, reason TEXT, edited_by TEXT, edited_at TEXT
    );
    CREATE TABLE allocation_pools (
        id INTEGER PRIMARY KEY, assessment_setup_id INTEGER, pool_key TEXT,
        pool_name TEXT, allocation_method TEXT, recipient_scope TEXT,
        denominator_value NUMERIC, include_in_pdf INTEGER, display_order INTEGER
    );
    CREATE TABLE assessment_groups (
        id INTEGER PRIMARY KEY, assessment_setup_id INTEGER, group_name TEXT,
        unit_count INTEGER, average_square_feet NUMERIC,
        ownership_percent NUMERIC, display_order INTEGER
    );
    CREATE TABLE assessment_units (
        id INTEGER PRIMARY KEY, assessment_setup_id INTEGER, unit_number TEXT,
        square_feet NUMERIC, ownership_percent NUMERIC, category TEXT,
        parking_spaces INTEGER
    );
    CREATE TABLE budget_line_pool_mappings (
        budget_line_normalized_label TEXT, section TEXT, category TEXT,
        fund_type TEXT, account_code TEXT, pool_key TEXT, active INTEGER,
        property_id INTEGER, assessment_setup_id INTEGER
    );
    CREATE TABLE assessment_unit_pool_allocations (
        assessment_setup_id INTEGER, assessment_unit_id INTEGER,
        pool_key TEXT, specified_monthly_amount NUMERIC
    );
"""


def _matrix_conn(unit_percents: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_MATRIX_DDL)
    conn.execute(
        "INSERT INTO assessment_setups (id, property_id, setup_type, status, approved_at) "
        "VALUES (3, 18, 'per_unit', 'approved', '2026-05-18T12:30:13+00:00')"
    )
    conn.execute(
        "INSERT INTO allocation_pools (id, assessment_setup_id, pool_key, pool_name, "
        "allocation_method, recipient_scope, denominator_value, include_in_pdf, display_order) "
        "VALUES (7, 3, 'common', 'Common Costs', 'ownership_percentage', 'all_units', NULL, 1, 1)"
    )
    for idx, pct in enumerate(unit_percents, start=1):
        conn.execute(
            "INSERT INTO assessment_units (id, assessment_setup_id, unit_number, "
            "square_feet, ownership_percent, category, parking_spaces) "
            "VALUES (?, 3, ?, NULL, ?, NULL, 0)",
            (idx, f"1{idx:02d}", pct),
        )
    # No budget_line_pool_mappings rows: the builder splits the generated
    # assessment revenue by the DRE pool proportions from the payload — the
    # same recipe as test_db_builder_can_split_generated_assessment_revenue.
    conn.execute(
        "INSERT INTO dre_extraction_runs (id, property_id, promoted_setup_id, parsed_json, "
        "promoted_at, document_type) VALUES (7, 18, 3, ?, '2026-05-18T12:30:13+00:00', 'dre')",
        (
            json.dumps(
                {
                    "document_metadata": {},
                    "assessment_setup": {
                        "setup_type": "individual_unit",
                        "source_pages": [1],
                    },
                    "allocation_pools": [
                        {
                            "pool_key": "common",
                            "allocation_method": "ownership_percentage",
                            "annual_amount": "120000",
                            "source_pages": [1],
                        }
                    ],
                }
            ),
        ),
    )
    return conn


def _build(conn: sqlite3.Connection):
    return build_matrix_from_approved_assessment_setup(
        connection=conn,
        property_id=18,
        fiscal_year=2026,
        budget_draft=SimpleNamespace(
            line_items=[
                SimpleNamespace(
                    label="40000 - Assessment Income",
                    amount=Decimal("120000"),
                    is_revenue=True,
                    is_reserve=False,
                    category="income",
                    section="income",
                    account_code="40000",
                )
            ]
        ),
        hoa_name="LEGACY TEST HOA",
        unit_count=3,
        approved_assessment_revenue_annual=Decimal("120000"),
    )


class TestMatrixReadPathLegacyRows:
    def test_legacy_verbatim_points_rows_render_correctly(self):
        # Pre-change promotion stored the printed points values verbatim
        # (60/25/15, sum=100). The read-path resolver must divide by 100 —
        # unit 101 pays 10000/mo × 0.60 = 6000, not 10000 × 60.
        conn = _matrix_conn(["60", "25", "15"])
        matrix = _build(conn)
        assert matrix.recipient_grain == "unit"
        monthly = {
            row.recipient_label: row.total_monthly_assessment
            for row in matrix.rows
        }
        assert monthly["101"] == Decimal("6000.00")
        assert monthly["102"] == Decimal("2500.00")
        assert monthly["103"] == Decimal("1500.00")

    def test_normalized_fraction_rows_render_identically(self):
        # Post-change promotion stores fractions; same rendered dollars.
        conn = _matrix_conn(["0.60", "0.25", "0.15"])
        matrix = _build(conn)
        monthly = {
            row.recipient_label: row.total_monthly_assessment
            for row in matrix.rows
        }
        assert monthly["101"] == Decimal("6000.00")

    def test_ambiguous_db_column_degrades_to_review_not_a_guess(self):
        # All values ≤ 1 but the sum (3.40) fits neither form: the matrix
        # must degrade to the operator-review fallback, never render.
        conn = _matrix_conn(["0.85", "0.85", "0.85", "0.85"])
        matrix = _build(conn)
        assert matrix.recipient_grain == "manual_review"
