"""Assessment schedule (variable mode) is driven by the Assessment Income line,
not the sum of mapped operating expenses (July 2026 client requirement).

Unit tests for the ``_rebase_component_dollars_to_assessment_revenue`` primitive:
the regular pool component dollars are rescaled so they sum EXACTLY to Assessment
Income while the DRE pool split (proportion between pools) is preserved. A 10%
change to Assessment Income moves every pool 10%. Special-assessment pools and
budgets with no Assessment Income are left untouched.
"""
from decimal import Decimal

from app.assessment_engine.schemas import (
    BudgetLineInput,
    BudgetLineMappingInput,
    PoolDefinition,
)
from app.disclosure_package.assessment_schedule_matrix import (
    SPECIAL_ASSESSMENT_POOL_KIND,
    _pool_totals_annual_for_mappings,
    _rebase_component_dollars_to_assessment_revenue,
)


def _line(label, amount, line_id, *, section="operating", category="operating"):
    return BudgetLineInput(
        line_id=line_id,
        normalized_label=label,
        section=section,
        category=category,
        fund_type="operating",
        account_code=None,
        amount=Decimal(str(amount)),
    )


def _map(label, pool_key, *, section="operating", category="operating"):
    return BudgetLineMappingInput(
        budget_line_normalized_label=label,
        section=section,
        category=category,
        fund_type="operating",
        account_code=None,
        pool_key=pool_key,
    )


def _pool(pool_key, order, *, kind=None):
    return PoolDefinition(
        pool_id=order,
        pool_key=pool_key,
        pool_name=pool_key,
        allocation_method="equal",
        recipient_scope="all_units",
        include_in_pdf=True,
        display_order=order,
        pool_kind=kind,
    )


def _totals(lines, mappings):
    return _pool_totals_annual_for_mappings(budget_lines=lines, mappings=mappings)


def test_rebase_scales_to_income_preserving_split():
    # Two pools 30000 : 90000 (1:3) of expenses; income is +10% (132000).
    lines = [_line("mgmt", 30000, 1), _line("landscape", 90000, 2)]
    mappings = [_map("mgmt", "equal_costs"), _map("landscape", "variable_costs")]
    pools = [_pool("equal_costs", 1), _pool("variable_costs", 2)]

    rebased = _rebase_component_dollars_to_assessment_revenue(
        budget_lines=lines, mappings=mappings, pools=pools,
        approved_assessment_revenue_annual=Decimal("132000"),
    )
    assert rebased is not None
    new_lines, new_mappings = rebased
    totals = _totals(new_lines, new_mappings)
    assert sum(totals.values()) == Decimal("132000")            # sums to income
    assert totals["equal_costs"] == Decimal("33000")            # 30000 * 1.1
    assert totals["variable_costs"] == Decimal("99000")         # 90000 * 1.1
    # split ratio preserved (1:3)
    assert totals["variable_costs"] == totals["equal_costs"] * 3


def test_rebase_no_income_keeps_expense_behavior():
    lines = [_line("mgmt", 30000, 1)]
    mappings = [_map("mgmt", "equal_costs")]
    pools = [_pool("equal_costs", 1)]
    assert _rebase_component_dollars_to_assessment_revenue(
        budget_lines=lines, mappings=mappings, pools=pools,
        approved_assessment_revenue_annual=Decimal("0"),
    ) is None


def test_rebase_leaves_special_assessment_pool_untouched():
    # A special-assessment pool's dollars are separately billed and must NOT be
    # scaled to (or counted toward) the regular Assessment Income.
    lines = [_line("mgmt", 40000, 1), _line("roof levy", 100000, 2)]
    mappings = [_map("mgmt", "equal_costs"), _map("roof levy", "sa_roof")]
    pools = [
        _pool("equal_costs", 1),
        _pool("sa_roof", 2, kind=SPECIAL_ASSESSMENT_POOL_KIND),
    ]
    rebased = _rebase_component_dollars_to_assessment_revenue(
        budget_lines=lines, mappings=mappings, pools=pools,
        approved_assessment_revenue_annual=Decimal("44000"),
    )
    assert rebased is not None
    new_lines, new_mappings = rebased
    totals = _totals(new_lines, new_mappings)
    assert totals["equal_costs"] == Decimal("44000")     # regular pool -> income
    assert totals["sa_roof"] == Decimal("100000")        # special pool untouched


def test_rebase_penny_remainder_lands_on_last_pool():
    # 1:1:1 split of an amount not divisible by 3 -> exact sum, remainder on last.
    lines = [_line("a", 100, 1), _line("b", 100, 2), _line("c", 100, 3)]
    mappings = [_map("a", "p1"), _map("b", "p2"), _map("c", "p3")]
    pools = [_pool("p1", 1), _pool("p2", 2), _pool("p3", 3)]
    rebased = _rebase_component_dollars_to_assessment_revenue(
        budget_lines=lines, mappings=mappings, pools=pools,
        approved_assessment_revenue_annual=Decimal("100.00"),
    )
    assert rebased is not None
    totals = _totals(*rebased)
    assert sum(totals.values()) == Decimal("100.00")
    assert totals["p1"] == Decimal("33.33")
    assert totals["p2"] == Decimal("33.33")
    assert totals["p3"] == Decimal("33.34")   # remainder
