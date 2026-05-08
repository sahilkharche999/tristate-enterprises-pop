"""Pure-function calc registry for disclosure-package generation (CONTEXT D-05).

Each formula:
  * Has typed inputs (Decimal money / int counts) and a single typed return.
  * Has no I/O, no global state, no side effects beyond audit-log emission.
  * Is decorated with `@audit_formula(name=…, version=…)` so each top-level
    invocation is recorded in the per-render audit log (CONTEXT D-07,
    threat T-11-04).
  * Money values are Decimal, NEVER float (CONTEXT D-06).

Don't Hand-Roll (RESEARCH § 'Don't Hand-Roll'): the whole-dollar rounding
helper here mirrors `reserve_study_extractor.py:117-121` so the Phase 11
disclosure package displays the same numbers users see in the Phase 10
reserve-study UI. ROUND_HALF_EVEN is the policy.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
from typing import Sequence

from .audit import audit_formula
from .schemas import LineItem, ReserveStudyComponent


_WHOLE_DOLLAR = Decimal("1")
_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")


def _round_whole(value: Decimal) -> int:
    """Half-even round to whole dollars; mirrors reserve_study_extractor."""
    return int(Decimal(value).quantize(_WHOLE_DOLLAR, rounding=ROUND_HALF_EVEN))


# ─────────────────────────────────────────────────────────────────────────────
# Tier 4 — § 5565 reserve summary
# ─────────────────────────────────────────────────────────────────────────────


@audit_formula(name="percent_funded", version=1)
def percent_funded(*, cash_reserves: Decimal, estimated_liability: Decimal) -> int:
    """Cash reserves as a whole-percent share of estimated liability.

    Returns: int 0-100. Rounding: half-even to whole percent.
    Source: California Civil Code §5565 (reserve disclosure summary).
    """
    if estimated_liability == 0:
        return 0
    return _round_whole(cash_reserves / estimated_liability * _HUNDRED)


@audit_formula(name="under_funded_balance_total", version=1)
def under_funded_balance_total(
    *, estimated_liability: Decimal, cash_reserves: Decimal
) -> Decimal:
    """Difference between estimated liability and cash reserves (§5565)."""
    return estimated_liability - cash_reserves


@audit_formula(name="under_funded_balance_per_unit", version=1)
def under_funded_balance_per_unit(
    *,
    estimated_liability: Decimal,
    cash_reserves: Decimal,
    units: int,
) -> Decimal:
    """Per-unit under-funded balance, half-even rounded to whole dollars (§5565)."""
    if units <= 0:
        return Decimal("0")
    raw = (estimated_liability - cash_reserves) / Decimal(units)
    return Decimal(_round_whole(raw))


# ─────────────────────────────────────────────────────────────────────────────
# Tier 3 — per-component
# ─────────────────────────────────────────────────────────────────────────────


@audit_formula(name="year_replacement_provision_for", version=1)
def year_replacement_provision_for(
    *, replacement_cost: Decimal, useful_life: int
) -> int:
    """Per-component yearly provision (whole-dollar half-even).

    Mirrors reserve_study_extractor.py policy so the disclosure-package
    schedule displays the same per-row provisions as the reserve-study UI.
    """
    if useful_life <= 0:
        return 0
    return _round_whole(replacement_cost / Decimal(useful_life))


@audit_formula(name="estimated_liability_for", version=1)
def estimated_liability_for(
    *,
    replacement_cost: Decimal,
    useful_life: int,
    remaining_life: int,
) -> int:
    """Accumulated liability at end of prior year for one component.

    Formula: cost * (useful_life - remaining_life) / useful_life.
    Whole-dollar half-even. Clamps remaining_life to useful_life.
    """
    if useful_life <= 0:
        return 0
    if remaining_life > useful_life:
        remaining_life = useful_life
    return _round_whole(
        replacement_cost * Decimal(useful_life - remaining_life) / Decimal(useful_life)
    )


@audit_formula(name="total_year_replacement_provision", version=1)
def total_year_replacement_provision(
    *, components: Sequence[ReserveStudyComponent]
) -> Decimal:
    """Sum of per-component yearly provisions (Decimal, whole dollars)."""
    return Decimal(
        sum(
            year_replacement_provision_for(
                replacement_cost=c.replacement_cost, useful_life=c.useful_life
            )
            for c in components
        )
    )


@audit_formula(name="total_estimated_liability", version=1)
def total_estimated_liability(
    *, components: Sequence[ReserveStudyComponent]
) -> Decimal:
    """Sum of per-component estimated liabilities (Decimal, whole dollars)."""
    return Decimal(
        sum(
            estimated_liability_for(
                replacement_cost=c.replacement_cost,
                useful_life=c.useful_life,
                remaining_life=c.remaining_life,
            )
            for c in components
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1 — aggregations
# ─────────────────────────────────────────────────────────────────────────────


@audit_formula(name="total_revenues_operations", version=1)
def total_revenues_operations(
    *, operating_line_items: Sequence[LineItem]
) -> Decimal:
    """Sum of operating-fund revenue line items (annual)."""
    return sum(
        (li.amount for li in operating_line_items if li.is_revenue),
        Decimal("0"),
    )


@audit_formula(name="total_revenues_replacement", version=1)
def total_revenues_replacement(
    *, reserve_line_items: Sequence[LineItem]
) -> Decimal:
    """Sum of replacement-fund revenue line items (annual)."""
    return sum(
        (li.amount for li in reserve_line_items if li.is_revenue),
        Decimal("0"),
    )


@audit_formula(name="expenses_maintenance_operating", version=1)
def expenses_maintenance_operating(
    *, operating_line_items: Sequence[LineItem]
) -> Decimal:
    """Sum of section='Maintenance and operations' non-revenue items."""
    return sum(
        (
            li.amount
            for li in operating_line_items
            if not li.is_revenue and li.section == "Maintenance and operations"
        ),
        Decimal("0"),
    )


@audit_formula(name="expenses_utilities_operating", version=1)
def expenses_utilities_operating(
    *, operating_line_items: Sequence[LineItem]
) -> Decimal:
    """Sum of section='Utilities' non-revenue items."""
    return sum(
        (
            li.amount
            for li in operating_line_items
            if not li.is_revenue and li.section == "Utilities"
        ),
        Decimal("0"),
    )


@audit_formula(name="expenses_administration_operating", version=1)
def expenses_administration_operating(
    *, operating_line_items: Sequence[LineItem]
) -> Decimal:
    """Sum of section='Administration' non-revenue items."""
    return sum(
        (
            li.amount
            for li in operating_line_items
            if not li.is_revenue and li.section == "Administration"
        ),
        Decimal("0"),
    )


@audit_formula(name="expenses_replacement", version=1)
def expenses_replacement(*, reserve_line_items: Sequence[LineItem]) -> Decimal:
    """Sum of replacement-fund non-revenue items."""
    return sum(
        (li.amount for li in reserve_line_items if not li.is_revenue),
        Decimal("0"),
    )


@audit_formula(name="total_expenses_operations", version=1)
def total_expenses_operations(
    *, maintenance: Decimal, utilities: Decimal, administration: Decimal
) -> Decimal:
    """Sum of the three operating-expense categories."""
    return maintenance + utilities + administration


@audit_formula(name="total_expenses", version=1)
def total_expenses(*, operations: Decimal, replacement: Decimal) -> Decimal:
    """Sum of operating + replacement expenses."""
    return operations + replacement


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 — net values
# ─────────────────────────────────────────────────────────────────────────────


@audit_formula(name="excess_revenues_over_expenses_operations", version=1)
def excess_revenues_over_expenses_operations(
    *, revenues: Decimal, expenses: Decimal
) -> Decimal:
    """Operating-fund net (revenues − expenses)."""
    return revenues - expenses


@audit_formula(name="excess_revenues_over_expenses_replacement", version=1)
def excess_revenues_over_expenses_replacement(
    *, revenues: Decimal, expenses: Decimal
) -> Decimal:
    """Replacement-fund net (revenues − expenses)."""
    return revenues - expenses


@audit_formula(name="fund_balance_eoy_operations", version=1)
def fund_balance_eoy_operations(
    *, beginning_balance: Decimal, excess: Decimal
) -> Decimal:
    """Operating fund-balance end-of-year = beginning + excess."""
    return beginning_balance + excess


@audit_formula(name="fund_balance_eoy_replacement", version=1)
def fund_balance_eoy_replacement(
    *, cash_balance_eoy_prior: Decimal, excess: Decimal
) -> Decimal:
    """Replacement fund-balance end-of-year = prior cash + excess."""
    return cash_balance_eoy_prior + excess


# ─────────────────────────────────────────────────────────────────────────────
# Tier 5 — funding-plan trajectory
# ─────────────────────────────────────────────────────────────────────────────


@audit_formula(name="monthly_replacement_contribution_per_unit_for", version=1)
def monthly_replacement_contribution_per_unit_for(
    *,
    year: int,
    base_2026: Decimal,
    schedule: Sequence,
) -> Decimal:
    """Piecewise monthly $/unit replacement contribution for `year`.

    Year ≤ 2026 → returns `base_2026` quantized to cents.
    Year > 2026 → cumulatively applies the annual rate from the matching
    band in `schedule` (a sequence of (start_year, end_year, rate)).
    Years outside any band carry forward unchanged.
    """
    if year <= 2026:
        return Decimal(base_2026).quantize(_CENT, rounding=ROUND_HALF_EVEN)
    value = Decimal(base_2026)
    for y in range(2027, year + 1):
        rate = next(
            (r for (start, end, r) in schedule if start <= y <= end),
            Decimal("0"),
        )
        value = value * (Decimal("1") + rate)
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


@audit_formula(name="annual_replacement_revenue_for", version=1)
def annual_replacement_revenue_for(
    *,
    year: int,
    units: int,
    base_2026: Decimal,
    schedule: Sequence,
) -> Decimal:
    """Annual replacement-fund revenue for `year` (Decimal, whole dollars)."""
    monthly = monthly_replacement_contribution_per_unit_for(
        year=year, base_2026=base_2026, schedule=schedule
    )
    return (monthly * Decimal(units) * Decimal(12)).quantize(
        _WHOLE_DOLLAR, rounding=ROUND_HALF_EVEN
    )


@audit_formula(name="interest_income_replacement_for", version=1)
def interest_income_replacement_for(
    *, cash_balance_boy: Decimal, rate_after_tax: Decimal
) -> Decimal:
    """Interest-income estimate on bank/CD balance, whole dollars half-even."""
    return (cash_balance_boy * rate_after_tax).quantize(
        _WHOLE_DOLLAR, rounding=ROUND_HALF_EVEN
    )


@audit_formula(name="cash_balance_eoy_for", version=1)
def cash_balance_eoy_for(
    *,
    boy: Decimal,
    replacement_revenue: Decimal,
    interest_income: Decimal,
    disbursements: Decimal,
) -> Decimal:
    """Replacement-fund cash balance end-of-year (Decimal, whole dollars)."""
    return boy + replacement_revenue + interest_income - disbursements
