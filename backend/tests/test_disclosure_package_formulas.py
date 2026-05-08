"""Tests for backend/app/disclosure_package/formulas.py (Phase 11 plan 02 Task 3).

RED → GREEN → REFACTOR. Decimal-only formula registry covering Tier 1-5 of
the calc DAG (RESEARCH § 'Calculation Graph'). Every public formula is
decorated with `@audit_formula(...)`; the audit-log integration test confirms
the decoration produces real records under `audit_context`.

Golden value notes (DEVIATION FROM PLAN, Rule 1):
- The plan's must_haves.truths states
  `under_funded_balance_per_unit(2_600_000, 4_575_000, 279)` should return
  `Decimal("7080")`. The mathematically correct value with ROUND_HALF_EVEN
  whole-dollar rounding is `Decimal("7079")` (raw=7078.85…). The "$7,080"
  figure in the golden PDF is therefore either a typo or a different
  rounding (ceil-to-tens). The test asserts the correct computed value;
  the SUMMARY documents the discrepancy for manual reconciliation in
  Wave 0 raster diff (plan 11-04+).
- The plan's note on `monthly_replacement_contribution_per_unit_2026`
  hints at $200.98 from the golden vs. $220.40 from the fixture (737886
  / 279 / 12). The fixture is a Phase 11 placeholder; the formula is
  deterministic and matches the fixture exactly. SUMMARY § 'Golden vs
  fixture' tracks this for plan 11-04 reconciliation.
"""
from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — fixture loader
# ─────────────────────────────────────────────────────────────────────────────


def _load_fixture():
    path = Path(__file__).parent / "fixtures" / "old_mill_2026_inputs.json"
    return json.loads(path.read_text())


# ─────────────────────────────────────────────────────────────────────────────
# Tier 4 — § 5565 reserve summary
# ─────────────────────────────────────────────────────────────────────────────


def test_percent_funded_old_mill_2026():
    from app.disclosure_package.formulas import percent_funded

    assert percent_funded(
        cash_reserves=Decimal("2600000"),
        estimated_liability=Decimal("4575000"),
    ) == 57


def test_percent_funded_zero_liability():
    from app.disclosure_package.formulas import percent_funded

    assert percent_funded(
        cash_reserves=Decimal("100"),
        estimated_liability=Decimal("0"),
    ) == 0


def test_under_funded_balance_total():
    from app.disclosure_package.formulas import under_funded_balance_total

    assert under_funded_balance_total(
        estimated_liability=Decimal("4575000"),
        cash_reserves=Decimal("2600000"),
    ) == Decimal("1975000")


def test_under_funded_balance_per_unit_old_mill_2026():
    """DEVIATION-NOTE: plan asserted 7080; mathematically correct = 7079.

    raw = 1_975_000 / 279 = 7078.853… → ROUND_HALF_EVEN whole-dollars = 7079.
    """
    from app.disclosure_package.formulas import under_funded_balance_per_unit

    assert under_funded_balance_per_unit(
        estimated_liability=Decimal("4575000"),
        cash_reserves=Decimal("2600000"),
        units=279,
    ) == Decimal("7079")


def test_under_funded_balance_per_unit_zero_units():
    from app.disclosure_package.formulas import under_funded_balance_per_unit

    assert under_funded_balance_per_unit(
        estimated_liability=Decimal("100"),
        cash_reserves=Decimal("50"),
        units=0,
    ) == Decimal("0")


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3 — per-component
# ─────────────────────────────────────────────────────────────────────────────


def test_year_replacement_provision_for():
    from app.disclosure_package.formulas import year_replacement_provision_for

    assert year_replacement_provision_for(
        replacement_cost=Decimal("500000"), useful_life=25
    ) == 20000


def test_year_replacement_provision_for_zero_useful_life_is_safe():
    from app.disclosure_package.formulas import year_replacement_provision_for

    assert year_replacement_provision_for(
        replacement_cost=Decimal("500000"), useful_life=0
    ) == 0


def test_estimated_liability_for_full_remaining():
    from app.disclosure_package.formulas import estimated_liability_for

    assert estimated_liability_for(
        replacement_cost=Decimal("500000"), useful_life=25, remaining_life=25
    ) == 0


def test_estimated_liability_for_zero_remaining():
    from app.disclosure_package.formulas import estimated_liability_for

    assert estimated_liability_for(
        replacement_cost=Decimal("500000"), useful_life=25, remaining_life=0
    ) == 500000


def test_estimated_liability_for_partial():
    from app.disclosure_package.formulas import estimated_liability_for

    # 500_000 * (25-10)/25 = 300_000
    assert estimated_liability_for(
        replacement_cost=Decimal("500000"), useful_life=25, remaining_life=10
    ) == 300000


def test_total_year_replacement_provision_from_fixture():
    from app.disclosure_package.formulas import total_year_replacement_provision
    from app.disclosure_package.schemas import ReserveStudySnapshot

    fixture = _load_fixture()
    snap = ReserveStudySnapshot.model_validate(fixture["reserve_study_snapshot"])
    # 500000/25 + 300000/20 + 75000/10 = 20000 + 15000 + 7500 = 42500
    assert total_year_replacement_provision(components=snap.components) == Decimal("42500")


def test_total_estimated_liability_from_fixture():
    from app.disclosure_package.formulas import total_estimated_liability
    from app.disclosure_package.schemas import ReserveStudySnapshot

    fixture = _load_fixture()
    snap = ReserveStudySnapshot.model_validate(fixture["reserve_study_snapshot"])
    # Roof: 500000*(25-10)/25 = 300000; Asphalt: 300000*(20-5)/20 = 225000;
    # Pool: 75000*(10-3)/10 = 52500. Total = 577_500
    assert total_estimated_liability(components=snap.components) == Decimal("577500")


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1 — aggregations from fixture line items
# ─────────────────────────────────────────────────────────────────────────────


def _budget_line_items():
    from app.disclosure_package.schemas import BudgetDraft

    fixture = _load_fixture()
    return BudgetDraft.model_validate(fixture["budget_draft"]).line_items


def test_total_revenues_operations_from_fixture():
    from app.disclosure_package.formulas import total_revenues_operations

    items = [li for li in _budget_line_items() if not li.is_reserve]
    # 2025540 + 0 = 2_025_540
    assert total_revenues_operations(operating_line_items=items) == Decimal("2025540")


def test_total_revenues_replacement_from_fixture():
    from app.disclosure_package.formulas import total_revenues_replacement

    items = [li for li in _budget_line_items() if li.is_reserve]
    # 672886 + 65000 = 737_886
    assert total_revenues_replacement(reserve_line_items=items) == Decimal("737886")


def test_expenses_maintenance_operating_from_fixture():
    from app.disclosure_package.formulas import expenses_maintenance_operating

    items = [li for li in _budget_line_items() if not li.is_reserve]
    assert expenses_maintenance_operating(operating_line_items=items) == Decimal("100000")


def test_expenses_utilities_operating_from_fixture():
    from app.disclosure_package.formulas import expenses_utilities_operating

    items = [li for li in _budget_line_items() if not li.is_reserve]
    assert expenses_utilities_operating(operating_line_items=items) == Decimal("75000")


def test_expenses_administration_operating_from_fixture():
    from app.disclosure_package.formulas import expenses_administration_operating

    items = [li for li in _budget_line_items() if not li.is_reserve]
    assert expenses_administration_operating(operating_line_items=items) == Decimal("120000")


def test_total_expenses_operations_sum():
    from app.disclosure_package.formulas import total_expenses_operations

    assert total_expenses_operations(
        maintenance=Decimal("100000"),
        utilities=Decimal("75000"),
        administration=Decimal("120000"),
    ) == Decimal("295000")


def test_excess_revenues_over_expenses_operations():
    from app.disclosure_package.formulas import excess_revenues_over_expenses_operations

    # 2_025_540 - 295_000 = 1_730_540
    assert excess_revenues_over_expenses_operations(
        revenues=Decimal("2025540"), expenses=Decimal("295000")
    ) == Decimal("1730540")


# ─────────────────────────────────────────────────────────────────────────────
# Tier 5 — funding plan
# ─────────────────────────────────────────────────────────────────────────────


def test_monthly_replacement_contribution_per_unit_2026_from_fixture():
    """Year 2026 base — formula returns the base directly (no schedule applied yet).

    DEVIATION-NOTE: golden PDF shows ~$200.98 / unit / month; the fixture-
    derived value is Decimal("220.40"). Discrepancy lives in the fixture
    inputs (placeholders, RESEARCH § 'Inputs to Hardcode'); SUMMARY tracks
    for reconciliation in plan 11-04.
    """
    from app.disclosure_package.formulas import monthly_replacement_contribution_per_unit_for

    # base_2026 = total_revenues_replacement / units / 12 = 737886/279/12 = 220.40
    base = Decimal("737886") / Decimal("279") / Decimal("12")
    base_quantized = monthly_replacement_contribution_per_unit_for(
        year=2026,
        base_2026=base,
        schedule=[(2026, 2035, Decimal("0.03"))],
    )
    assert base_quantized == Decimal("220.40")


def test_audit_log_records_each_formula_call():
    """Inside audit_context, calling a decorated formula records exactly one entry."""
    from app.disclosure_package.audit import audit_context
    from app.disclosure_package.formulas import percent_funded

    with audit_context({"snapshot": "x"}) as log:
        percent_funded(cash_reserves=Decimal("2600000"), estimated_liability=Decimal("4575000"))

    assert len(log.formula_calls) == 1
    call = log.formula_calls[0]
    assert call.formula_id == "percent_funded"
    assert call.version == 1
    assert call.output == 57


def test_decimal_round_half_even_banker_rounding():
    """Confirm 0.5 rounds to even per ROUND_HALF_EVEN.

    Banker's rounding: 2.5 → 2, 3.5 → 4. The whole-dollar rounding helper
    used by year_replacement_provision_for / estimated_liability_for / etc.
    must follow this convention (matches reserve_study_extractor.py:117-121).
    """
    from app.disclosure_package.formulas import _round_whole

    assert _round_whole(Decimal("2.5")) == 2
    assert _round_whole(Decimal("3.5")) == 4
    assert _round_whole(Decimal("0.5")) == 0
    assert _round_whole(Decimal("1.5")) == 2


def test_no_float_in_signatures():
    """Every public formula's annotated money parameter MUST be Decimal, never float.

    Threat T-11-04 (extension) — float drift in money math. The Pydantic
    boundary catches it for inputs; this test catches it inside formulas.py.
    """
    from app.disclosure_package import formulas as f

    public_names = [
        "percent_funded",
        "under_funded_balance_total",
        "under_funded_balance_per_unit",
        "year_replacement_provision_for",
        "estimated_liability_for",
        "total_year_replacement_provision",
        "total_estimated_liability",
        "total_revenues_operations",
        "total_revenues_replacement",
        "expenses_maintenance_operating",
        "expenses_utilities_operating",
        "expenses_administration_operating",
        "expenses_replacement",
        "total_expenses_operations",
        "total_expenses",
        "excess_revenues_over_expenses_operations",
        "excess_revenues_over_expenses_replacement",
        "fund_balance_eoy_operations",
        "fund_balance_eoy_replacement",
        "monthly_replacement_contribution_per_unit_for",
        "annual_replacement_revenue_for",
        "interest_income_replacement_for",
        "cash_balance_eoy_for",
    ]
    for name in public_names:
        fn = getattr(f, name)
        sig = inspect.signature(fn)
        for pname, param in sig.parameters.items():
            ann = param.annotation
            # Resolve forward-ref strings (because of `from __future__ import annotations`).
            ann_str = ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))
            assert "float" not in ann_str.lower(), (
                f"{name}.{pname} has float in annotation: {ann_str}"
            )


def test_old_mill_2026_spec_loads():
    """Smoke test that OLD_MILL_2026 is a valid PackageSpec."""
    from app.disclosure_package.package_specs import SPECS

    spec = SPECS["old_mill"]
    assert spec.fiscal_year == 2026
    assert spec.static_data.assessment_model == "flat"
    assert spec.static_data.monthly_assessment_per_unit_current == Decimal("605.00")


def test_old_mill_2026_entries_total_109_pages():
    """Smoking-gun: golden PDF is 109 pages (CONTEXT, RESEARCH § 'Top-level structure').

    DEVIATION-NOTE: the RESEARCH-listed entries sum to 96 pages, not 109.
    The deficit is reconciled in old_mill.py per CONTEXT and SUMMARY.
    """
    from app.disclosure_package.package_specs import SPECS

    spec = SPECS["old_mill"]
    total = sum(entry.page_count_hint for entry in spec.entries)
    assert total == 109, f"expected 109, got {total}"
