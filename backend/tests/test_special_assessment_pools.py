"""Pool-based special assessments (add-variable-special-assessments).

A pool marked ``pool_kind='separately_billed_special_assessment'`` is allocated
ONCE (a one-time lump, not annualized) across recipients by its basis, kept out
of monthly dues and reconciliation, and surfaced on
``CalcResultSet.special_assessment_allocations``.
"""
from decimal import Decimal

from app.assessment_engine.engine import run, SPECIAL_ASSESSMENT_POOL_KIND
from app.assessment_engine.schemas import (
    BudgetLineInput,
    BudgetLineMappingInput,
    CalcInput,
    PoolDefinition,
    RecipientReference,
    RecipientSet,
)


def _two_units():
    return [
        RecipientReference(ref_type="unit", ref_id=1, label="Unit 1", unit_count=1, square_feet=Decimal("700")),
        RecipientReference(ref_type="unit", ref_id=2, label="Unit 2", unit_count=1, square_feet=Decimal("300")),
    ]


def _regular_pool():
    return PoolDefinition(
        pool_id=1, pool_key="equal_costs", pool_name="Monthly",
        allocation_method="equal", recipient_scope="all_units", display_order=0,
    )


def _regular_line_and_map(amount="12000"):
    line = BudgetLineInput(
        line_id=1, normalized_label="assessment income", section="income",
        category="income", fund_type="operating", account_code="40000", amount=Decimal(amount),
    )
    mapping = BudgetLineMappingInput(
        budget_line_normalized_label="assessment income", section="income",
        category="income", fund_type="operating", account_code="40000",
        pool_key="equal_costs", active=True,
    )
    return line, mapping


def _special_pool(method="square_footage"):
    return PoolDefinition(
        pool_id=2, pool_key="sa_roof", pool_name="Special Assessment Roof",
        allocation_method=method, recipient_scope="all_units",
        denominator_value=Decimal("1000"), display_order=1,
        pool_kind=SPECIAL_ASSESSMENT_POOL_KIND,
    )


def _special_line_and_map(amount="120000"):
    line = BudgetLineInput(
        line_id=2, normalized_label="__sa_roof", section="special_assessment",
        category="operating", fund_type="operating", account_code=None, amount=Decimal(amount),
    )
    mapping = BudgetLineMappingInput(
        budget_line_normalized_label="__sa_roof", section="special_assessment",
        category="operating", fund_type="operating", account_code=None,
        pool_key="sa_roof", active=True,
    )
    return line, mapping


def test_special_pool_allocated_by_sqft_out_of_monthly_dues():
    rline, rmap = _regular_line_and_map()
    sline, smap = _special_line_and_map("120000")
    res = run(CalcInput(
        setup_type="per_unit", pools=[_regular_pool(), _special_pool()],
        recipient_set=RecipientSet(recipients=_two_units()),
        budget_lines=[rline, sline], mappings=[rmap, smap],
        approved_assessment_revenue_annual=Decimal("12000"),
    ))

    # Monthly dues come from the equal pool only: 12000/12/2 = 500 each.
    monthly = {t.recipient_ref.ref_id: t.rounded_monthly_total for t in res.recipient_totals}
    assert monthly == {1: Decimal("500.00"), 2: Decimal("500.00")}

    # Special pool excluded from the reconciliation diagnostic.
    assert res.pool_sum_annual == Decimal("12000")

    # One-time allocation by square footage: 700/1000 and 300/1000 of 120000.
    assert len(res.special_assessment_allocations) == 1
    sa = res.special_assessment_allocations[0]
    assert sa.total == Decimal("120000")
    assert sa.allocation_method == "square_footage"
    shares = {e.recipient_ref.ref_id: e.amount for e in sa.entries}
    assert shares == {1: Decimal("84000"), 2: Decimal("36000")}


def test_special_pool_does_not_touch_monthly_when_regular_absent():
    # Only a special pool: monthly dues are zero, allocation still produced.
    sline, smap = _special_line_and_map("10000")
    res = run(CalcInput(
        setup_type="per_unit", pools=[_special_pool(method="equal")],
        recipient_set=RecipientSet(recipients=_two_units()),
        budget_lines=[sline], mappings=[smap],
        approved_assessment_revenue_annual=Decimal("0"),
    ))
    assert res.pool_sum_annual == Decimal("0")
    assert all(t.rounded_monthly_total == Decimal("0.00") for t in res.recipient_totals)
    sa = res.special_assessment_allocations[0]
    shares = {e.recipient_ref.ref_id: e.amount for e in sa.entries}
    assert shares == {1: Decimal("5000"), 2: Decimal("5000")}  # equal split of 10000


def test_regular_only_run_has_no_special_allocations():
    rline, rmap = _regular_line_and_map()
    res = run(CalcInput(
        setup_type="fixed", pools=[_regular_pool()],
        recipient_set=RecipientSet(recipients=_two_units()),
        budget_lines=[rline], mappings=[rmap],
        approved_assessment_revenue_annual=Decimal("12000"),
    ))
    assert res.special_assessment_allocations == []
