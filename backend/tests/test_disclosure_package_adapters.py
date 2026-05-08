"""Tests for backend/app/disclosure_package/adapters.py (Phase 11 plan 03 Task 1).

RED → GREEN: these tests are authored before adapters.py exists. They pin the
input-boundary translation contract from CONTEXT D-03 ("compiler never imports
service-specific shapes directly") and threat model T-11-04 (float→Decimal
coercion via string round-trip — RESEARCH Pitfall 2).

Phase 7 metadata flags (section, category, is_reserve, read_only, is_revenue)
MUST pass through unchanged — re-classification is RESEARCH Pitfall 3.

Phase 10's reserve-study row schema may evolve (RESEARCH Risk #3); the
adapter accepts duck-typed objects (attribute or dict access).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest


# ── Test 1: BudgetHistoryRecord → BudgetDraft preserves Decimal money ─────────
def test_from_budget_history_record_returns_decimal_amounts():
    from app.disclosure_package.adapters import from_budget_history_record

    record = {
        "line_items": [
            {
                "label": "Regular Assessment",
                "amount": "1234.56",
                "section": "Income",
                "category": "income",
                "is_reserve": False,
                "is_revenue": True,
                "read_only": False,
            }
        ]
    }
    bd = from_budget_history_record(record)
    assert len(bd.line_items) == 1
    item = bd.line_items[0]
    assert isinstance(item.amount, Decimal)
    assert item.amount == Decimal("1234.56")
    # CRITICAL: must NOT be a float
    assert not isinstance(item.amount, float)


# ── Test 2: Phase 7 classification metadata passes through unchanged ──────────
def test_from_budget_history_record_preserves_phase7_metadata():
    """RESEARCH Pitfall 3: adapters MUST NOT re-classify line items."""
    from app.disclosure_package.adapters import from_budget_history_record

    record = {
        "line_items": [
            {
                "label": "Replacement Reserve Expense",
                "amount": "672886.00",
                "section": "Reserve Expenses",
                "category": "reserve",
                "is_reserve": True,
                "is_revenue": False,
                "read_only": True,
            },
            {
                "label": "Regular Assessment Income",
                "amount": "2025540.00",
                "section": "Income",
                "category": "income",
                "is_reserve": False,
                "is_revenue": True,
                "read_only": False,
            },
        ]
    }
    bd = from_budget_history_record(record)
    assert bd.line_items[0].section == "Reserve Expenses"
    assert bd.line_items[0].category == "reserve"
    assert bd.line_items[0].is_reserve is True
    assert bd.line_items[0].is_revenue is False
    assert bd.line_items[0].read_only is True
    assert bd.line_items[1].section == "Income"
    assert bd.line_items[1].is_revenue is True
    assert bd.line_items[1].is_reserve is False


# ── Test 3: empty line_items raises ValueError ────────────────────────────────
def test_from_budget_history_record_rejects_empty_line_items():
    from app.disclosure_package.adapters import from_budget_history_record

    with pytest.raises(ValueError, match="line_items"):
        from_budget_history_record({"line_items": []})

    with pytest.raises(ValueError, match="line_items"):
        from_budget_history_record({})


# ── Test 4: float input is coerced via Decimal(str()) — Pitfall 2 mitigation ──
def test_from_budget_history_record_coerces_float_via_string_roundtrip():
    """Pitfall 2: Decimal(605.0) yields the binary-float artifact
    `Decimal('605.0')` only if we route through str() first; constructing
    Decimal directly from a float gives `Decimal('605.0000000000000071...')`.
    """
    from app.disclosure_package.adapters import from_budget_history_record

    record = {
        "line_items": [
            {
                "label": "Test",
                "amount": 605.00,  # actual Python float
                "is_revenue": True,
                "section": "Income",
            }
        ]
    }
    bd = from_budget_history_record(record)
    # The string round-trip yields exactly Decimal("605.0"), NOT a long
    # binary-float-artifact tail.
    assert bd.line_items[0].amount == Decimal("605.0")
    assert "0000000" not in str(bd.line_items[0].amount)


# ── Test 5: ExtractedReserveStudyDocument → ReserveStudySnapshot ──────────────
def test_from_reserve_study_extraction_returns_decimal_replacement_costs():
    from app.disclosure_package.adapters import from_reserve_study_extraction

    # Use SimpleNamespace to mimic the Phase-10 ExtractedReserveStudyDocument
    # without importing the model (duck-typed boundary, RESEARCH Risk #3).
    doc = SimpleNamespace(
        study_date="September 2025",
        rows=[
            SimpleNamespace(
                line_item="Roof",
                useful_life=25,
                remaining_life=10,
                replacement_cost=500000.00,
                year_new=2010,
            ),
            SimpleNamespace(
                line_item="Pool Resurfacing",
                useful_life=10,
                remaining_life=3,
                replacement_cost=75000.00,
                year_new=2018,
            ),
        ],
    )
    snap = from_reserve_study_extraction(doc)
    assert snap.study_date == "September 2025"
    assert len(snap.components) == 2
    assert snap.components[0].line_item == "Roof"
    assert snap.components[0].useful_life == 25
    assert snap.components[0].remaining_life == 10
    assert isinstance(snap.components[0].replacement_cost, Decimal)
    assert snap.components[0].replacement_cost == Decimal("500000.0")
    assert snap.components[0].year_new == 2010
    assert snap.components[1].line_item == "Pool Resurfacing"


# ── Test 6: rows with useful_life=None or 0 are skipped (defensive) ───────────
def test_from_reserve_study_extraction_skips_rows_without_useful_life():
    """Reserve-study rows with no useful_life can't enter the formula DAG
    (division-by-zero in year_replacement_provision). Defensive guard.
    """
    from app.disclosure_package.adapters import from_reserve_study_extraction

    doc = SimpleNamespace(
        study_date="September 2025",
        rows=[
            SimpleNamespace(
                line_item="Header Row",
                useful_life=None,
                remaining_life=None,
                replacement_cost=None,
            ),
            SimpleNamespace(
                line_item="Zero-life",
                useful_life=0,
                remaining_life=0,
                replacement_cost=100.0,
            ),
            SimpleNamespace(
                line_item="Valid",
                useful_life=20,
                remaining_life=5,
                replacement_cost=300000.00,
            ),
        ],
    )
    snap = from_reserve_study_extraction(doc)
    # Only the valid row survives.
    assert len(snap.components) == 1
    assert snap.components[0].line_item == "Valid"


# ── Test 7: Property ORM row → HOAMetadata (units >= 1, fiscal months 1-12) ──
def test_from_hoa_record_converts_property_row():
    from app.disclosure_package.adapters import from_hoa_record

    # Mimic a SQLAlchemy ORM row via SimpleNamespace.
    row = SimpleNamespace(
        id=1,
        name="Old Mill Homeowners Association",
        units=279,
        fiscal_year_start_month=1,
        fiscal_year_end_month=12,
        tax_id="00-0000000",
    )
    meta = from_hoa_record(row)
    assert meta.hoa_id == 1
    assert meta.name == "Old Mill Homeowners Association"
    assert meta.units == 279
    assert isinstance(meta.fiscal_year_start_month, int)
    assert 1 <= meta.fiscal_year_start_month <= 12
    assert 1 <= meta.fiscal_year_end_month <= 12
    assert meta.tax_id == "00-0000000"


# ── Test 8: from_hoa_record raises ValueError when units is 0/None ────────────
def test_from_hoa_record_rejects_zero_or_null_units():
    from app.disclosure_package.adapters import from_hoa_record

    zero = SimpleNamespace(
        id=1, name="X", units=0,
        fiscal_year_start_month=1, fiscal_year_end_month=12, tax_id=None,
    )
    with pytest.raises(ValueError, match="units"):
        from_hoa_record(zero)

    null = SimpleNamespace(
        id=1, name="X", units=None,
        fiscal_year_start_month=1, fiscal_year_end_month=12, tax_id=None,
    )
    with pytest.raises(ValueError, match="units"):
        from_hoa_record(null)
