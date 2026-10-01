"""PackageScenario: one dated fiscal-year fact set for every disclosure page."""
from __future__ import annotations

from decimal import Decimal

from app.disclosure_package.schemas import (
    LineItem,
    ReserveStudyComponent,
    ReserveStudySnapshot,
)
from app.disclosure_package.scenario import build_package_scenario


MISSOURI_TRANSFER = Decimal("31935")
MISSOURI_PROVISION = Decimal("39659")
MISSOURI_UNDATED_CASH = Decimal("360000")


def _missouri_lines() -> list[LineItem]:
    return [
        LineItem(
            label="Assessment Income",
            amount=Decimal("104458"),
            is_revenue=True,
            section="Operating Income",
        ),
        LineItem(
            label="Reserve - Allocation/Transfer",
            amount=MISSOURI_TRANSFER,
            section="Operating Expense",
        ),
        LineItem(
            label="Management",
            amount=Decimal("18000"),
            section="Administration",
        ),
    ]


def _missouri_snapshot() -> ReserveStudySnapshot:
    # Component provision ≈ $39,659 when rolled from typical Missouri items.
    # One synthetic component is enough for the scenario contract.
    cost = MISSOURI_PROVISION * Decimal("25")
    return ReserveStudySnapshot(
        study_date="January 2025",
        components=[
            ReserveStudyComponent(
                line_item="Roofing",
                useful_life=25,
                remaining_life=10,
                replacement_cost=cost,
                year_new=2010,
            ),
        ],
        funding_plan_rows=[],
    )


def test_missouri_untouched_settings_print_budget_transfer_not_provision() -> None:
    snapshot = _missouri_snapshot()
    scenario = build_package_scenario(
        fiscal_year=2025,
        units=9,
        budget_line_items=_missouri_lines(),
        reserve_snapshot=snapshot,
        settings={
            "reserve_funding_source": "auto",
            "reserve_cash_balance_eoy_prior": MISSOURI_UNDATED_CASH,
        },
    )

    assert scenario.adopted_contribution.value == MISSOURI_TRANSFER
    assert scenario.funding_facts.source == "budget_reserve_contribution"
    assert scenario.component_annual_provision is not None
    assert scenario.component_annual_provision.value != MISSOURI_TRANSFER
    assert scenario.opening_reserve_cash.is_dated is False
    assert any("undated" in reason.lower() for reason in scenario.blocking_reasons)


def test_missouri_empty_settings_default_to_income_statement_transfer() -> None:
    scenario = build_package_scenario(
        fiscal_year=2025,
        units=9,
        budget_line_items=_missouri_lines(),
        reserve_snapshot=_missouri_snapshot(),
        settings={},
    )
    assert scenario.adopted_contribution.value == MISSOURI_TRANSFER
    assert scenario.adopted_contribution.is_overwrite is False


def test_operator_provision_overwrite_is_labeled() -> None:
    scenario = build_package_scenario(
        fiscal_year=2025,
        units=9,
        budget_line_items=_missouri_lines(),
        reserve_snapshot=_missouri_snapshot(),
        settings={
            "reserve_funding_source": "reserve_study_provision",
            "reserve_funding_overwrite_reason": "Board kept component provision",
        },
    )
    assert scenario.adopted_contribution.value == scenario.funding_facts.component_annual_provision
    assert scenario.adopted_contribution.is_overwrite is True
    assert scenario.overwrite_reason
    assert "overwrite" in scenario.adopted_contribution.source_label.lower() or scenario.adopted_contribution.is_overwrite


def test_levy_adopted_overrides_replace_study_liability_and_provision() -> None:
    scenario = build_package_scenario(
        fiscal_year=2026,
        units=9,
        budget_line_items=_missouri_lines(),
        reserve_snapshot=_missouri_snapshot(),
        settings={
            "reserve_funding_source": "manual",
            "reserve_funding_manual_amount": Decimal("29340"),
            "reserve_funding_overwrite_reason": "2026 Levy Erlanger adopted pro forma",
            "reserve_liability_override": Decimal("340000"),
            "annual_replacement_provision_override": Decimal("42000"),
            "reserve_interest_income_override": Decimal("4500"),
            "income_tax_provision_override": Decimal("1300"),
            "reserve_cash_balance_eoy_prior": MISSOURI_UNDATED_CASH,
            "reserve_cash_as_of_date": "2025-12-31",
        },
    )
    assert scenario.adopted_contribution.value == Decimal("29340")
    assert scenario.fully_funded_liability.value == Decimal("340000")
    assert scenario.component_annual_provision is not None
    assert scenario.component_annual_provision.value == Decimal("42000")
    assert scenario.reserve_interest.value == Decimal("4500.00")
    assert scenario.opening_reserve_cash.value == MISSOURI_UNDATED_CASH
    assert scenario.opening_reserve_cash.is_dated is True
