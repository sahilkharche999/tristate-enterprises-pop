"""Cross-HOA golden compile fixtures for disclosure-financial-scenario.

These exercise `_compute_all` / PackageScenario — not WeasyPrint — so they
run on machines without libgobject. Do not invent Missouri cash figures
($35,000 / $104,009) here; cash stays operator-dated or $0.
"""
from __future__ import annotations

from decimal import Decimal

from app.disclosure_package.compiler import _compute_all
from app.disclosure_package.package_specs.standard import OLD_MILL_2026
from app.disclosure_package.reconciliation import (
    PoolLineFundTotals,
    assessment_split_from_schedule_components,
)
from app.disclosure_package.scenario import build_package_scenario
from app.disclosure_package.schemas import (
    BudgetDraft,
    HOAMetadata,
    LineItem,
    ReserveStudyComponent,
    ReserveStudySnapshot,
)
from tests.support.missouri_allocation_fixture import (
    MISSOURI_BUDGET_LINES,
    MISSOURI_ELECTRICITY_EQUAL_SLICE,
    MISSOURI_LEVY_VARIABLE_SLICES,
)


def _hoa(*, units: int = 9, name: str = "Golden HOA") -> HOAMetadata:
    return HOAMetadata(
        hoa_id=1,
        name=name,
        units=units,
        fiscal_year_start_month=1,
        fiscal_year_end_month=12,
    )


def _dated_settings(cash: str = "0") -> dict:
    return {
        "reserve_cash_balance_eoy_prior": cash,
        "reserve_cash_as_of_date": "2025-12-31",
        "reserve_funding_source": "auto",
    }


def test_golden_equal_share_no_exceptions() -> None:
    budget = BudgetDraft(
        line_items=[
            LineItem(label="Assessment Income", amount=Decimal("120000"), is_revenue=True),
            LineItem(label="Reserve - Allocation/Transfer", amount=Decimal("24000")),
            LineItem(label="Management", amount=Decimal("36000")),
        ]
    )
    snapshot = ReserveStudySnapshot(
        study_date="January 2025",
        components=[
            ReserveStudyComponent(
                line_item="Roof",
                useful_life=20,
                remaining_life=10,
                replacement_cost=Decimal("200000"),
            )
        ],
    )
    scenario = build_package_scenario(
        fiscal_year=2026,
        units=10,
        budget_line_items=budget.line_items,
        reserve_snapshot=snapshot,
        settings=_dated_settings(),
    )
    assert scenario.adopted_contribution.value == Decimal("24000")
    computed = _compute_all(
        spec=OLD_MILL_2026.model_copy(update={"fiscal_year": 2026}),
        budget_draft=budget,
        reserve_snapshot=snapshot,
        hoa_metadata=_hoa(units=10),
        effective_hoa_settings=_dated_settings(),
    )["computed"]
    assert Decimal(str(computed["package_scenario"]["adopted_contribution"]["value"])) == Decimal("24000")
    assert computed["thirty_year_funding_plan"][0]["annual_contribution"] == 24000


def test_golden_missouri_ownership_frozen_exceptions() -> None:
    transfer = Decimal("31935")
    provision = Decimal("39659")
    budget = BudgetDraft(
        line_items=[
            LineItem(
                label=str(row["label"]),
                amount=Decimal(str(row["annual_amount"])),
                is_revenue=row["label"] == "Assessment Income",
            )
            for row in [
                {"label": "Assessment Income", "annual_amount": Decimal("104458")},
                *MISSOURI_BUDGET_LINES,
            ]
        ]
    )
    snapshot = ReserveStudySnapshot(
        study_date="January 2025",
        components=[
            ReserveStudyComponent(
                line_item="Painting",
                useful_life=10,
                remaining_life=5,
                replacement_cost=provision,
            )
        ],
    )
    scenario = build_package_scenario(
        fiscal_year=2025,
        units=9,
        budget_line_items=budget.line_items,
        reserve_snapshot=snapshot,
        settings=_dated_settings("0"),
    )
    assert scenario.adopted_contribution.value == transfer
    assert scenario.adopted_contribution.value != provision
    painting = MISSOURI_LEVY_VARIABLE_SLICES["painting reserve"]
    assert painting == Decimal("7181")
    # Operator-dated cash is required before a live Missouri regenerate.
    assert "35000" not in str(scenario.opening_reserve_cash.value)
    assert "104009" not in str(scenario.opening_reserve_cash.value)


def test_golden_combined_utility_line_must_be_sliced() -> None:
    combined = next(
        row for row in MISSOURI_BUDGET_LINES if row["label"] == "Electricity & Gas"
    )
    assert combined["annual_amount"] == Decimal("16800")
    gas = MISSOURI_LEVY_VARIABLE_SLICES["gas"]
    assert gas + MISSOURI_ELECTRICITY_EQUAL_SLICE == Decimal("16800")
    rows = [
        type("R", (), {"component_key": "gas", "component_label": "Gas", "annual_amount": gas})(),
        type(
            "R",
            (),
            {
                "component_key": "electric",
                "component_label": "Electric",
                "annual_amount": MISSOURI_ELECTRICITY_EQUAL_SLICE,
            },
        )(),
    ]
    totals = {
        "gas": PoolLineFundTotals(operating_mapped=gas, reserve_mapped=Decimal("0")),
        "electric": PoolLineFundTotals(
            operating_mapped=MISSOURI_ELECTRICITY_EQUAL_SLICE,
            reserve_mapped=Decimal("0"),
        ),
    }
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=Decimal("16800"),
        fallback_reserve_assessment=Decimal("0"),
        pool_line_fund_totals=totals,
    )
    assert ops == Decimal("16800.00")
    assert res == Decimal("0.00")
    assert source in {"schedule_matrix_line_fund", "settings_funding_fallback_no_reserve_pool"}


def test_golden_reserve_only_packet_archetype() -> None:
    budget = BudgetDraft(
        line_items=[
            LineItem(label="Reserve Interest", amount=Decimal("500"), is_revenue=True, is_reserve=True),
            LineItem(label="Reserve - Allocation/Transfer", amount=Decimal("12000")),
        ]
    )
    snapshot = ReserveStudySnapshot(study_date="January 2025", components=[])
    computed = _compute_all(
        spec=OLD_MILL_2026.model_copy(update={"fiscal_year": 2026}),
        budget_draft=budget,
        reserve_snapshot=snapshot,
        hoa_metadata=_hoa(units=4),
        effective_hoa_settings={
            **_dated_settings(),
            "financial_packet_archetype": "reserve-only",
        },
    )["computed"]
    assert computed["packet_archetype_facts"]["archetype"] == "reserve-only"
    scenario = build_package_scenario(
        fiscal_year=2026,
        units=4,
        budget_line_items=budget.line_items,
        reserve_snapshot=snapshot,
        settings={**_dated_settings(), "financial_packet_archetype": "reserve-only"},
    )
    assert scenario.adopted_contribution.value == Decimal("12000")
