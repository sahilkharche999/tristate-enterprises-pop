"""Fix 1 + Fix 2: dual-fund statement integrity without breaking schedule totals."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.disclosure_package.reconciliation import (
    assessment_split_from_schedule_components,
    build_annual_statement_facts,
    is_interfund_reserve_transfer_line,
    resolve_reserve_liability_facts,
)


def test_interfund_transfer_label_detection() -> None:
    assert is_interfund_reserve_transfer_line("90000 - Reserve - Allocation/Transfer")
    assert is_interfund_reserve_transfer_line("Reserve -Allocation/Transfer")
    assert is_interfund_reserve_transfer_line("Transfer to Reserve")
    assert not is_interfund_reserve_transfer_line("Management Fee")
    assert not is_interfund_reserve_transfer_line("Interest Earned Reserve")


def test_assessment_split_from_schedule_matrix_policy_s() -> None:
    rows = [
        SimpleNamespace(
            component_key="general_operating",
            component_label="Operating Expenses",
            annual_amount=Decimal("268875.09"),
        ),
        SimpleNamespace(
            component_key="reserve_contributions",
            component_label="Reserve Contributions",
            annual_amount=Decimal("103992.51"),
        ),
    ]
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=Decimal("372867.60"),
        fallback_reserve_assessment=Decimal("118132"),
    )
    assert source == "schedule_matrix"
    assert ops + res == Decimal("372867.60")
    assert ops == Decimal("268875.09")
    assert res == Decimal("103992.51")


def test_assessment_split_falls_back_to_settings_when_matrix_empty() -> None:
    ops, res, source = assessment_split_from_schedule_components(
        [],
        total_regular_assessment_revenue=Decimal("372867.60"),
        fallback_reserve_assessment=Decimal("118132"),
    )
    assert source == "settings_funding_fallback"
    assert res == Decimal("118132.00")
    assert ops == Decimal("254735.60")


def test_annual_statement_excludes_transfer_style_other_revenue_from_inflation() -> None:
    """When transfer is excluded from other_rep and ops expenses, totals stay clean."""
    liab = resolve_reserve_liability_facts(
        cash_reserve_balance_eoy_prior=Decimal("560000"),
        total_estimated_liability=Decimal("849319"),
        under_funded_balance_total=Decimal("289319"),
        under_funded_balance_per_unit=Decimal("7233"),
        percent_funded=Decimal("66"),
        annual_replacement_provision=Decimal("90685"),
    )
    facts = build_annual_statement_facts(
        packet_archetype="dual-fund",
        total_regular_assessment_revenue=Decimal("372867.60"),
        reserve_assessment_revenue=Decimal("103992.51"),  # schedule Policy S
        reserve_interest_income=Decimal("5600"),
        reserve_tax_provision=Decimal("1600"),
        other_operating_revenue=Decimal("8100"),
        other_replacement_revenue=Decimal("0"),  # Fix 1: no transfer mirror
        total_operating_expenses=Decimal("273851"),  # Fix 1: no transfer expense
        beginning_balance_operations=Decimal("15000"),
        reserve_liability_facts=liab,
    )
    # No double-count: total revenues = ops assess + other op + res assess + interest
    assert facts.operating_assessment_revenue == Decimal("268875.09")
    assert facts.reserve_assessment_revenue == Decimal("103992.51")
    assert facts.other_replacement_revenue == Decimal("0.00")
    assert facts.total_revenues == (
        Decimal("268875.09") + Decimal("8100") + Decimal("103992.51") + Decimal("5600")
    )
    assert facts.total_expenses_operations == Decimal("273851.00")
    # Ops is not crushed by a $105k transfer
    assert facts.excess_revenues_over_expenses_operations > Decimal("-10000")
