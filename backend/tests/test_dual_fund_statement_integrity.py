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


def test_interfund_transfer_fix1b_reserve_income_mirror() -> None:
    """Fix 1b: 45000 Reserve Income is interfund mirror, not external revenue."""
    assert is_interfund_reserve_transfer_line("Reserve Income")
    assert is_interfund_reserve_transfer_line("45000 - Reserve Income")
    assert is_interfund_reserve_transfer_line(
        "Reserve Income", account_code="45000"
    )
    assert is_interfund_reserve_transfer_line("", account_code="45000")
    assert is_interfund_reserve_transfer_line("", account_code="90000")
    # Interest must never be stripped as a transfer (reported via interest facts).
    assert not is_interfund_reserve_transfer_line("Interest Earned Reserve")
    assert not is_interfund_reserve_transfer_line("Reserve Interest Income")
    assert not is_interfund_reserve_transfer_line(
        "Interest Earned Reserve", account_code="47000"
    )
    # Real operating costs stay expenses.
    assert not is_interfund_reserve_transfer_line("Management Fee")
    assert not is_interfund_reserve_transfer_line("Assessment Income")



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


def test_assessment_split_falls_back_when_matrix_has_no_reserve_pool() -> None:
    """Multi-pool HOAs (equal/sqft) must not zero the P&L reserve column."""
    # 800 High / Two Worlds / Old Mill / LAVS style: schedule has only
    # operating-style pools; dual-fund reserve share comes from settings.
    rows = [
        SimpleNamespace(
            component_key="equal_costs",
            component_label="Equal Costs",
            annual_amount=Decimal("2228094.00"),
        ),
        SimpleNamespace(
            component_key="prorated_pool",
            component_label="Monthly Assessment (Prorated Items Only)",
            annual_amount=Decimal("296873.80"),
        ),
    ]
    total = Decimal("900000.00")
    settings_reserve = Decimal("200000.00")
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=total,
        fallback_reserve_assessment=settings_reserve,
    )
    assert source == "settings_funding_fallback_no_reserve_pool"
    assert res == settings_reserve
    assert ops == Decimal("700000.00")
    assert ops + res == total


def test_assessment_split_falls_back_when_reserve_pool_mapped_at_zero() -> None:
    """Reserve pool exists but $0 mapped → treat as no reserve schedule basis."""
    rows = [
        SimpleNamespace(
            component_key="general_operating",
            component_label="Operating Expenses",
            annual_amount=Decimal("268875.09"),
        ),
        SimpleNamespace(
            component_key="reserve_contributions",
            component_label="Reserve Contributions",
            annual_amount=Decimal("0"),
        ),
    ]
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=Decimal("372867.60"),
        fallback_reserve_assessment=Decimal("118132"),
    )
    assert source == "settings_funding_fallback_no_reserve_pool"
    assert res == Decimal("118132.00")
    assert ops == Decimal("254735.60")


def test_assessment_split_ignores_special_assessment_as_reserve() -> None:
    """Special assessment pools must not count as the regular reserve share."""
    rows = [
        SimpleNamespace(
            component_key="equal_pool",
            component_label="Monthly Assessment",
            annual_amount=Decimal("500000"),
        ),
        SimpleNamespace(
            component_key="traffic_signal_pool",
            component_label="Special Assessment for Traffic Signal",
            annual_amount=Decimal("50000"),
        ),
    ]
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=Decimal("500000"),
        fallback_reserve_assessment=Decimal("120000"),
    )
    assert source == "settings_funding_fallback_no_reserve_pool"
    assert res == Decimal("120000.00")
    assert ops == Decimal("380000.00")


def test_assessment_split_missouri_mixed_pool_name_does_not_steal_replacement() -> None:
    """CCR mixed pool titled '...Specific Reserves...' with only ops lines must
    not paint insurance/utilities into the Replacement Fund column (Option B)."""
    from app.disclosure_package.reconciliation import PoolLineFundTotals

    rows = [
        SimpleNamespace(
            component_key="equal_base",
            component_label="Equal Base Operating Pool",
            annual_amount=Decimal("55352.31"),
        ),
        SimpleNamespace(
            component_key="variable_dre_exceptions",
            component_label="Insurance, Utilities, and Specific Reserves Pool",
            annual_amount=Decimal("49105.53"),
        ),
        SimpleNamespace(
            component_key="parking_cost_center",
            component_label="Parking Cost Center Pool",
            annual_amount=Decimal("0"),
        ),
    ]
    totals = {
        "equal_base": PoolLineFundTotals(
            operating_mapped=Decimal("38483"),
            reserve_mapped=Decimal("0"),
        ),
        "variable_dre_exceptions": PoolLineFundTotals(
            operating_mapped=Decimal("34140"),
            reserve_mapped=Decimal("0"),
        ),
    }
    total = Decimal("104457.84")
    fallback = Decimal("39659.00")
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=total,
        fallback_reserve_assessment=fallback,
        pool_line_fund_totals=totals,
    )
    assert source == "settings_funding_fallback_no_reserve_pool"
    assert res == fallback
    assert ops == (total - fallback).quantize(Decimal("0.01"))
    # Must not use the ~$49k mixed-pool annual as replacement share.
    assert res != Decimal("49105.53")
    assert res < Decimal("45000")


def test_assessment_split_substring_reserve_in_label_without_lines_is_ops() -> None:
    """Bare name containing 'Reserves' is not enough — no allowlist key, no lines."""
    rows = [
        SimpleNamespace(
            component_key="variable_dre_exceptions",
            component_label="Insurance, Utilities, and Specific Reserves Pool",
            annual_amount=Decimal("49105.53"),
        ),
        SimpleNamespace(
            component_key="equal_base",
            component_label="Equal Base Operating Pool",
            annual_amount=Decimal("55352.31"),
        ),
    ]
    total = Decimal("104457.84")
    fallback = Decimal("39659.00")
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=total,
        fallback_reserve_assessment=fallback,
        pool_line_fund_totals=None,
    )
    assert source == "settings_funding_fallback_no_reserve_pool"
    assert res == fallback
    assert ops + res == total


def test_assessment_split_mixed_pool_line_ratio() -> None:
    """When a pool has both ops and reserve mapped lines, split component annual by ratio."""
    from app.disclosure_package.reconciliation import PoolLineFundTotals

    rows = [
        SimpleNamespace(
            component_key="mixed_pool",
            component_label="Mixed Exceptions",
            annual_amount=Decimal("100000.00"),
        ),
    ]
    totals = {
        "mixed_pool": PoolLineFundTotals(
            operating_mapped=Decimal("70000"),
            reserve_mapped=Decimal("30000"),
        ),
    }
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=Decimal("100000.00"),
        fallback_reserve_assessment=Decimal("50000"),
        pool_line_fund_totals=totals,
    )
    assert source in {"schedule_matrix", "schedule_matrix_line_fund"}
    assert ops == Decimal("70000.00")
    assert res == Decimal("30000.00")


def test_assessment_split_interfund_transfer_counts_as_reserve_mapped() -> None:
    """Operating fund_type + Reserve Allocation/Transfer label → replacement share."""
    from app.disclosure_package.reconciliation import (
        PoolLineFundTotals,
        build_pool_line_fund_totals_from_mapped_rows,
        is_line_replacement_share_for_assessment_split,
    )

    assert is_line_replacement_share_for_assessment_split(
        fund_type="operating",
        category="operating",
        label="Reserve - Allocation/Transfer",
        account_code="90000",
    )
    assert not is_line_replacement_share_for_assessment_split(
        fund_type="operating",
        category="operating",
        label="General Insurance",
        account_code="55000",
    )

    totals = build_pool_line_fund_totals_from_mapped_rows(
        [
            {
                "pool_key": "equal_base",
                "amount": Decimal("7200"),
                "fund_type": "operating",
                "category": "operating",
                "label": "Management Service",
                "account_code": "50050",
            },
            {
                "pool_key": "equal_base",
                "amount": Decimal("31935"),
                "fund_type": "operating",
                "category": "operating",
                "label": "Reserve - Allocation/Transfer",
                "account_code": "90000",
            },
        ]
    )
    assert totals["equal_base"].operating_mapped == Decimal("7200")
    assert totals["equal_base"].reserve_mapped == Decimal("31935")

    rows = [
        SimpleNamespace(
            component_key="equal_base",
            component_label="Equal Base Operating Pool",
            annual_amount=Decimal("39135.00"),
        ),
    ]
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=Decimal("39135.00"),
        fallback_reserve_assessment=Decimal("10000"),
        pool_line_fund_totals=totals,
    )
    assert source in {"schedule_matrix", "schedule_matrix_line_fund"}
    assert res == Decimal("31935.00")
    assert ops == Decimal("7200.00")


def test_assessment_split_allowlist_unmapped_reserve_contributions() -> None:
    """formula_only reserve pool with no line totals still counts via strict key."""
    rows = [
        SimpleNamespace(
            component_key="general_operating",
            component_label="Operating Expenses",
            annual_amount=Decimal("80000"),
        ),
        SimpleNamespace(
            component_key="reserve_contributions",
            component_label="Anything With The Word Reserves In A Weird Name",
            annual_amount=Decimal("20000"),
        ),
    ]
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=Decimal("100000"),
        fallback_reserve_assessment=Decimal("99999"),
        pool_line_fund_totals={},
    )
    assert source in {"schedule_matrix", "schedule_matrix_scaled", "schedule_matrix_line_fund"}
    assert res == Decimal("20000.00")
    assert ops == Decimal("80000.00")


def test_assessment_split_sharon_ridge_with_line_fund_totals() -> None:
    """Sharon Ridge still follows schedule when line totals match pool annuals."""
    from app.disclosure_package.reconciliation import PoolLineFundTotals

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
    totals = {
        "general_operating": PoolLineFundTotals(
            operating_mapped=Decimal("268875.09"),
            reserve_mapped=Decimal("0"),
        ),
        "reserve_contributions": PoolLineFundTotals(
            operating_mapped=Decimal("0"),
            reserve_mapped=Decimal("103992.51"),
        ),
    }
    ops, res, source = assessment_split_from_schedule_components(
        rows,
        total_regular_assessment_revenue=Decimal("372867.60"),
        fallback_reserve_assessment=Decimal("118132"),
        pool_line_fund_totals=totals,
    )
    assert source in {"schedule_matrix", "schedule_matrix_line_fund"}
    assert ops + res == Decimal("372867.60")
    assert ops == Decimal("268875.09")
    assert res == Decimal("103992.51")


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
