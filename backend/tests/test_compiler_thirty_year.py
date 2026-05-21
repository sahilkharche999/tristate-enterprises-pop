"""Tests for the rebuilt 30-year reserve funding study outputs
(drifting-puzzling-grove rebuild).

The rebuilt _build_thirty_year_plan returns a dict with three keys:
    - thirty_year_cash_flow              (pivoted matrix dict)
    - major_component_expenditure_schedule (list[dict])
    - thirty_year_funding_plan           (legacy list[dict] for back-compat)

These tests verify:
    - per-component schedule uses CURRENT dollars (no inflation per component cell)
    - replacement events fire at correct offsets {RL, RL+UL, ...}
    - cash-flow aggregate IS inflated
    - assessment schedule escalates the monthly per-unit value year-by-year
    - cash-balance chain holds (begin[k+1] == end[k])
    - legacy back-compat shape is preserved for pro_forma_disclosure_summary.html
"""
from __future__ import annotations
from decimal import Decimal

from app.disclosure_package.compiler import (
    _build_thirty_year_plan,
    _per_component_expenditures,
    _aggregate_expenditures_inflated,
    _bracket_rate_for_year,
)
from app.disclosure_package.package_specs.standard import OLD_MILL_2026
from app.disclosure_package.schemas import HOAMetadata, ReserveStudyComponent


def _hoa() -> HOAMetadata:
    return HOAMetadata(
        hoa_id=1, name="Old Mill Homeowners Association", units=279,
        fiscal_year_start_month=1, fiscal_year_end_month=12,
        city="Mountain View", state="CA",
    )


def _old_mill_schedule() -> list[dict]:
    return [
        {"start_year": 2026, "end_year": 2035, "rate": 0.03},
        {"start_year": 2036, "end_year": 2045, "rate": 0.03},
        {"start_year": 2046, "end_year": 2055, "rate": 0.00},
    ]


# ─── per-component expenditures ─────────────────────────────────────────────


def test_per_component_expenditures_long_life_no_inflation() -> None:
    """UL=20, RL=0, cost=9217 — replacements at year 1 (offset 0) and year 21
    (offset 20). Both cells in CURRENT dollars (9217 each). Total = 18434."""
    c = ReserveStudyComponent(
        line_item="Asphalt/Gravel Roof - Replace",
        useful_life=20, remaining_life=0, replacement_cost=Decimal("9217"),
    )
    rows = _per_component_expenditures([c], fiscal_year_start=2026)
    assert len(rows) == 1
    row = rows[0]
    assert row["line_item"] == "Asphalt/Gravel Roof - Replace"
    assert row["expenditures_by_year"][0] == Decimal("9217")
    assert row["expenditures_by_year"][20] == Decimal("9217")
    nonzero = [i for i, v in enumerate(row["expenditures_by_year"]) if v != 0]
    assert nonzero == [0, 20], nonzero
    assert row["total_expenditures"] == Decimal("18434")


def test_per_component_expenditures_short_life_many_events() -> None:
    """UL=3, RL=3, cost=5985 — replacements at offsets 3, 6, 9, 12, 15, 18, 21, 24, 27.
    Nine events; offset 30 is outside the window."""
    c = ReserveStudyComponent(
        line_item="Garage Cleaning",
        useful_life=3, remaining_life=3, replacement_cost=Decimal("5985"),
    )
    rows = _per_component_expenditures([c], fiscal_year_start=2026)
    nonzero = [i for i, v in enumerate(rows[0]["expenditures_by_year"]) if v != 0]
    assert nonzero == [3, 6, 9, 12, 15, 18, 21, 24, 27], nonzero
    assert rows[0]["total_expenditures"] == Decimal("5985") * 9


def test_per_component_expenditures_one_shot() -> None:
    """UL=30, RL=29, cost=20297 — single replacement at year 30 (offset 29)."""
    c = ReserveStudyComponent(
        line_item="Sauna Refurbish",
        useful_life=30, remaining_life=29, replacement_cost=Decimal("20297"),
    )
    rows = _per_component_expenditures([c], fiscal_year_start=2026)
    nonzero = [i for i, v in enumerate(rows[0]["expenditures_by_year"]) if v != 0]
    assert nonzero == [29]
    assert rows[0]["total_expenditures"] == Decimal("20297")


def test_per_component_expenditures_out_of_window() -> None:
    """RL > 29 — no replacement events; row still present with empty cells, total=0."""
    c = ReserveStudyComponent(
        line_item="Distant Repair",
        useful_life=50, remaining_life=35, replacement_cost=Decimal("131670"),
    )
    rows = _per_component_expenditures([c], fiscal_year_start=2026)
    assert all(v == 0 for v in rows[0]["expenditures_by_year"])
    assert rows[0]["total_expenditures"] == Decimal("0")


# ─── cash-flow aggregate ─────────────────────────────────────────────────────


def test_aggregate_expenditures_is_inflated() -> None:
    """The cash-flow Repair-and-Replacement-Costs row IS inflated, while
    per-component cells are NOT. Verifies the convention split."""
    c = ReserveStudyComponent(
        line_item="Roof", useful_life=20, remaining_life=0,
        replacement_cost=Decimal("10000"),
    )
    per = _per_component_expenditures([c], fiscal_year_start=2026)
    inflated = _aggregate_expenditures_inflated(per, inflation=Decimal("0.03"))
    # Year 1 (offset 0): no inflation factor applied -> $10,000.
    assert inflated[0] == Decimal("10000")
    # Year 21 (offset 20): per-component cell is still 10000 (current $),
    # but aggregate is inflated by 1.03^20 ≈ 1.806 -> ≈ 18,061.
    expected_y21 = (Decimal("10000") * (Decimal("1.03") ** 20)).quantize(Decimal("1"))
    assert inflated[20] == expected_y21
    # Per-component cell unaffected — still 10000 in current dollars.
    assert per[0]["expenditures_by_year"][20] == Decimal("10000")


# ─── bracket lookup ──────────────────────────────────────────────────────────


def test_bracket_rate_lookup_old_mill_schedule() -> None:
    schedule = _old_mill_schedule()
    assert _bracket_rate_for_year(2026, schedule) == Decimal("0.03")
    assert _bracket_rate_for_year(2035, schedule) == Decimal("0.03")
    assert _bracket_rate_for_year(2036, schedule) == Decimal("0.03")
    assert _bracket_rate_for_year(2045, schedule) == Decimal("0.03")
    assert _bracket_rate_for_year(2046, schedule) == Decimal("0")
    assert _bracket_rate_for_year(2055, schedule) == Decimal("0")
    # Outside any bracket -> 0.
    assert _bracket_rate_for_year(2099, schedule) == Decimal("0")


# ─── assessment escalation ───────────────────────────────────────────────────


def test_cash_flow_assessment_escalation_old_mill() -> None:
    """Old Mill seed schedule: monthly per unit starts at $200.98, grows 3%/yr
    through 2045, then holds flat at $352.42 through 2055. These exact values
    are visible in the real Old Mill 2026 PDF's 30-year cash-flow panel."""
    out = _build_thirty_year_plan(
        spec=OLD_MILL_2026,
        hoa_metadata=_hoa(),
        components=[],
        total_estimated_liability=Decimal("0"),
        total_year_replacement_provision=Decimal("0"),
        cash_eoy_prior=Decimal("0"),
        fiscal_year_start=2026,
        inflation_rate=Decimal("0.03"),
        interest_rate=Decimal("0.018"),
        assessment_schedule=_old_mill_schedule(),
        base_replacement_fund_monthly_per_unit=Decimal("200.98"),
        special_assessments=[],
        board_deferrals=[],
    )
    cf = out["thirty_year_cash_flow"]
    monthly = cf["replace_fund_assmnt_per_unit_per_mo"]
    assert monthly[0] == Decimal("200.98")
    assert monthly[1] == Decimal("207.01"), monthly[1]
    assert monthly[10] == Decimal("270.10"), monthly[10]
    assert monthly[20] == Decimal("352.42"), monthly[20]
    # Year 21+ holds flat at the year-20 value (bracket rate = 0).
    assert monthly[29] == monthly[20]


def test_cash_flow_balance_chain_holds() -> None:
    """cash_balance_beginning[k+1] must equal cash_balance_end[k] for all years."""
    out = _build_thirty_year_plan(
        spec=OLD_MILL_2026, hoa_metadata=_hoa(),
        components=[
            ReserveStudyComponent(
                line_item="Roof", useful_life=20, remaining_life=0,
                replacement_cost=Decimal("9217"),
            ),
        ],
        total_estimated_liability=Decimal("6500000"),
        total_year_replacement_provision=Decimal("672886"),
        cash_eoy_prior=Decimal("2600000"),
        fiscal_year_start=2026,
        inflation_rate=Decimal("0.03"),
        interest_rate=Decimal("0.018"),
        assessment_schedule=_old_mill_schedule(),
        base_replacement_fund_monthly_per_unit=Decimal("200.98"),
        special_assessments=[],
        board_deferrals=[],
    )
    cf = out["thirty_year_cash_flow"]
    for k in range(29):
        assert cf["cash_balance_beginning"][k + 1] == cf["cash_balance_end"][k], (
            f"chain broken at offset {k}: "
            f"end[{k}]={cf['cash_balance_end'][k]} "
            f"begin[{k+1}]={cf['cash_balance_beginning'][k+1]}"
        )
    # Year 1 begin matches the seeded reserve cash balance.
    assert cf["cash_balance_beginning"][0] == Decimal("2600000")


def test_cash_flow_year_1_regular_assessments_match_old_mill() -> None:
    """Year 1 regular_assessments = 279 units × $200.98 × 12 = $672,881
    (the real PDF rounds slightly differently; we compute exactly)."""
    out = _build_thirty_year_plan(
        spec=OLD_MILL_2026, hoa_metadata=_hoa(),
        components=[],
        total_estimated_liability=Decimal("0"),
        total_year_replacement_provision=Decimal("0"),
        cash_eoy_prior=Decimal("0"),
        fiscal_year_start=2026,
        inflation_rate=Decimal("0.03"),
        interest_rate=Decimal("0.018"),
        assessment_schedule=_old_mill_schedule(),
        base_replacement_fund_monthly_per_unit=Decimal("200.98"),
        special_assessments=[],
        board_deferrals=[],
    )
    cf = out["thirty_year_cash_flow"]
    expected = (Decimal("279") * Decimal("200.98") * Decimal("12")).quantize(Decimal("1"))
    assert cf["regular_assessments"][0] == expected, (
        f"got {cf['regular_assessments'][0]}, expected {expected}"
    )


# ─── back-compat ─────────────────────────────────────────────────────────────


def test_legacy_thirty_year_funding_plan_back_compat_shape() -> None:
    """pro_forma_disclosure_summary.html:165 iterates thirty_year_funding_plan
    and accesses 8 specific keys per row. Verify they're all present."""
    out = _build_thirty_year_plan(
        spec=OLD_MILL_2026, hoa_metadata=_hoa(),
        components=[
            ReserveStudyComponent(
                line_item="Roof", useful_life=20, remaining_life=0,
                replacement_cost=Decimal("9217"),
            ),
        ],
        total_estimated_liability=Decimal("6500000"),
        total_year_replacement_provision=Decimal("672886"),
        cash_eoy_prior=Decimal("2600000"),
        fiscal_year_start=2026,
        inflation_rate=Decimal("0.03"),
        interest_rate=Decimal("0.018"),
        assessment_schedule=_old_mill_schedule(),
        base_replacement_fund_monthly_per_unit=Decimal("200.98"),
        special_assessments=[],
        board_deferrals=[],
    )
    legacy = out["thirty_year_funding_plan"]
    assert isinstance(legacy, list)
    assert len(legacy) == 30
    expected_keys = {
        "year", "beginning_balance", "annual_contribution", "annual_expenditure",
        "interest", "ending_balance", "estimated_liability", "percent_funded",
    }
    assert set(legacy[0].keys()) == expected_keys
    assert legacy[0]["year"] == 2026
    assert legacy[0]["beginning_balance"] == 2600000


def test_special_assessment_per_unit_flows_into_cash_flow() -> None:
    """Operator-entered special assessment ($500/unit in 2030) shows up in the
    Replace-Fund-Special-Assessment-Per-Unit row and the cash-flow Special-
    Assessments row (×units)."""
    out = _build_thirty_year_plan(
        spec=OLD_MILL_2026, hoa_metadata=_hoa(),
        components=[],
        total_estimated_liability=Decimal("0"),
        total_year_replacement_provision=Decimal("0"),
        cash_eoy_prior=Decimal("0"),
        fiscal_year_start=2026,
        inflation_rate=Decimal("0.03"),
        interest_rate=Decimal("0.018"),
        assessment_schedule=_old_mill_schedule(),
        base_replacement_fund_monthly_per_unit=Decimal("200.98"),
        special_assessments=[{"year": 2030, "per_unit": 500}],
        board_deferrals=[],
    )
    cf = out["thirty_year_cash_flow"]
    # 2030 is offset 4 from 2026.
    assert cf["replace_fund_special_assmnt_per_unit_per_yr"][4] == Decimal("500.00")
    expected_total = Decimal("500") * Decimal("279")
    assert cf["special_assessments_row"][4] == expected_total
    # Other years remain 0.
    assert cf["replace_fund_special_assmnt_per_unit_per_yr"][0] == Decimal("0")
    assert cf["special_assessments_row"][5] == Decimal("0")


def test_board_deferral_reduces_disbursements() -> None:
    """A board-approved deferral subtracts from the year's total cash
    disbursements (matching the Old Mill 'Board approved deferral of
    expenditures' row)."""
    out = _build_thirty_year_plan(
        spec=OLD_MILL_2026, hoa_metadata=_hoa(),
        components=[
            ReserveStudyComponent(
                line_item="Roof", useful_life=20, remaining_life=0,
                replacement_cost=Decimal("100000"),
            ),
        ],
        total_estimated_liability=Decimal("0"),
        total_year_replacement_provision=Decimal("0"),
        cash_eoy_prior=Decimal("0"),
        fiscal_year_start=2026,
        inflation_rate=Decimal("0.03"),
        interest_rate=Decimal("0.018"),
        assessment_schedule=_old_mill_schedule(),
        base_replacement_fund_monthly_per_unit=Decimal("200.98"),
        special_assessments=[],
        board_deferrals=[{"year": 2026, "amount": 20000}],
    )
    cf = out["thirty_year_cash_flow"]
    # Year 1: inflated cost = 100,000 (offset 0 → no inflation).
    assert cf["repair_replacement_costs"][0] == Decimal("100000")
    assert cf["board_approved_deferral"][0] == Decimal("20000")
    # Total disbursements = repair − deferral = 80,000.
    assert cf["total_cash_disbursements"][0] == Decimal("80000")
