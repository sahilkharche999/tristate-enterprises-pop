"""H1/H2 — engine money-routing integrity.

The engine must never let mapped budget dollars silently disappear:
- H1: a budget line mapped to a pool_key with no PoolDefinition in the
  current setup is reported in ``orphaned_pool_lines`` (not dropped).
- H2: a pool with nonzero mapped dollars but zero resolved recipients is
  reported in ``zero_recipient_pools`` (not skipped with a silent continue).

A clean setup reports neither, and its output bytes are unchanged.
"""
from __future__ import annotations

from decimal import Decimal

from app.assessment_engine import (
    BudgetLineInput,
    CalcInput,
    PoolDefinition,
    RecipientReference,
    RecipientSet,
)
from app.assessment_engine.engine import run
from app.assessment_engine.schemas import BudgetLineMappingInput


def _line(line_id: int, label: str, amount: str, *, category: str = "operating") -> BudgetLineInput:
    return BudgetLineInput(
        line_id=line_id,
        normalized_label=label,
        section="operating",
        category=category,  # type: ignore[arg-type]
        fund_type="operating",
        account_code=None,
        amount=Decimal(amount),
    )


def _mapping(label: str, pool_key: str, *, category: str = "operating") -> BudgetLineMappingInput:
    return BudgetLineMappingInput(
        budget_line_normalized_label=label,
        section="operating",
        category=category,  # type: ignore[arg-type]
        fund_type="operating",
        account_code=None,
        pool_key=pool_key,
        active=True,
    )


def _pool(pool_id: int, key: str, *, scope: str = "all_units") -> PoolDefinition:
    return PoolDefinition(
        pool_id=pool_id,
        pool_key=key,
        pool_name=key.replace("_", " ").title(),
        allocation_method="equal",
        recipient_scope=scope,  # type: ignore[arg-type]
        display_order=pool_id,
    )


def _residential(ref_id: int) -> RecipientReference:
    return RecipientReference(
        ref_type="unit", ref_id=ref_id, label=f"R{ref_id}",
        unit_count=1, category="residential",
    )


def _commercial(ref_id: int) -> RecipientReference:
    return RecipientReference(
        ref_type="unit", ref_id=ref_id, label=f"C{ref_id}",
        unit_count=1, category="commercial",
    )


def test_orphaned_pool_key_reported_not_dropped() -> None:
    """H1: a line mapped to a removed pool_key surfaces in the report with
    its dollars and label, and is NOT counted into pool_sum_annual."""
    calc = CalcInput(
        setup_type="fixed",
        pools=[_pool(1, "equal_costs")],  # note: no "parking" pool
        recipient_set=RecipientSet(recipients=[_residential(1), _residential(2)]),
        budget_lines=[
            _line(1, "dues", "24000", category="income"),
            _line(2, "parking fees", "1200"),
        ],
        mappings=[
            _mapping("dues", "equal_costs", category="income"),
            _mapping("parking fees", "parking"),  # stale pool_key
        ],
        approved_assessment_revenue_annual=Decimal("24000"),
    )
    result = run(calc)

    assert len(result.orphaned_pool_lines) == 1
    orphan = result.orphaned_pool_lines[0]
    assert orphan.pool_key == "parking"
    assert orphan.annual_total == Decimal("1200")
    assert "parking fees" in orphan.contributing_line_labels
    # The orphaned dollars never entered the pool loop / diagnostic sum.
    assert result.pool_sum_annual == Decimal("24000")
    assert result.zero_recipient_pools == []


def test_zero_recipient_pool_reported_not_skipped() -> None:
    """H2: a residential_only pool with dollars in an all-commercial
    building is reported by name/scope/total, not silently skipped."""
    calc = CalcInput(
        setup_type="fixed",
        pools=[
            _pool(1, "shared", scope="all_units"),
            _pool(2, "res_only", scope="residential_only"),
        ],
        recipient_set=RecipientSet(recipients=[_commercial(1), _commercial(2)]),
        budget_lines=[
            _line(1, "shared costs", "12000"),
            _line(2, "residential amenity", "6000"),
        ],
        mappings=[
            _mapping("shared costs", "shared"),
            _mapping("residential amenity", "res_only"),
        ],
        approved_assessment_revenue_annual=Decimal("18000"),
    )
    result = run(calc)

    assert len(result.zero_recipient_pools) == 1
    zr = result.zero_recipient_pools[0]
    assert zr.pool_key == "res_only"
    assert zr.recipient_scope == "residential_only"
    assert zr.annual_total == Decimal("6000")
    assert "residential amenity" in zr.contributing_line_labels
    assert result.orphaned_pool_lines == []


def test_zero_dollar_zero_recipient_pool_is_not_reported() -> None:
    """A zero-recipient pool with no mapped dollars is benign — no report."""
    calc = CalcInput(
        setup_type="fixed",
        pools=[
            _pool(1, "shared", scope="all_units"),
            _pool(2, "res_only", scope="residential_only"),
        ],
        recipient_set=RecipientSet(recipients=[_commercial(1)]),
        budget_lines=[_line(1, "shared costs", "12000")],
        mappings=[_mapping("shared costs", "shared")],
        approved_assessment_revenue_annual=Decimal("12000"),
    )
    result = run(calc)
    assert result.zero_recipient_pools == []
    assert result.orphaned_pool_lines == []


def test_clean_setup_reports_empty_and_output_unchanged() -> None:
    """A clean run reports neither issue and both fields default empty, so
    serialized output is unchanged for setups with no routing problems."""
    calc = CalcInput(
        setup_type="fixed",
        pools=[_pool(1, "equal_costs")],
        recipient_set=RecipientSet(recipients=[_residential(1), _residential(2)]),
        budget_lines=[_line(1, "dues", "24000", category="income")],
        mappings=[_mapping("dues", "equal_costs", category="income")],
        approved_assessment_revenue_annual=Decimal("24000"),
    )
    result = run(calc)
    assert result.orphaned_pool_lines == []
    assert result.zero_recipient_pools == []
    # Idempotent + byte-stable across runs.
    assert run(calc).model_dump() == result.model_dump()
