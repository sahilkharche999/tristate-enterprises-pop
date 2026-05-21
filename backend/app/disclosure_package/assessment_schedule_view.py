"""Assessment-schedule template view builder (Phase 5.2).

Converts a ``CalcResultSet`` plus minimal recipient metadata into the
context dict the three assessment-schedule templates expect:
``fixed.html``, ``grouped.html``, ``per_unit.html``.

The view builder is responsible for re-shaping the engine's flat
result lists into per-recipient + per-pool tables. The templates
themselves stay simple Jinja loops; if the template-layer change
later wants extra columns, this is the one place to add them.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from app.assessment_engine import (
    CalcResultSet,
    PoolDefinition,
    SetupType,
)


def _zero() -> Decimal:
    return Decimal("0")


def build_fixed_view(
    result: CalcResultSet,
    *,
    hoa_name: str,
    fiscal_year: int,
) -> dict[str, Any]:
    """View dict for ``assessment_schedule/fixed.html``.

    Every recipient pays the same monthly amount, so the template
    renders one summary row. ``total_annual_revenue`` is the sum of
    per-recipient annual totals (rounding-tolerant).
    """
    monthlies = {t.rounded_monthly_total for t in result.recipient_totals}
    if not monthlies:
        monthly_per_unit = _zero()
    else:
        # In a true fixed-pattern HOA all monthlies are identical; if
        # they differ slightly due to override edits the template can
        # surface the most common value.
        monthly_per_unit = next(iter(monthlies))

    total_annual = sum(
        (t.annual_total for t in result.recipient_totals),
        start=_zero(),
    )
    annual_per_unit = monthly_per_unit * Decimal("12")
    return {
        "hoa": {"name": hoa_name},
        "fiscal_year": fiscal_year,
        "unit_count": len(result.recipient_totals),
        "monthly_assessment_per_unit": monthly_per_unit,
        "annual_assessment_per_unit": annual_per_unit,
        "total_annual_revenue": total_annual,
    }


def build_grouped_view(
    result: CalcResultSet,
    *,
    hoa_name: str,
    fiscal_year: int,
    base_pool_key: str = "equal_base",
    variable_pool_key: str = "variable_costs",
) -> dict[str, Any]:
    """View dict for ``assessment_schedule/grouped.html``.

    Splits each group's components into a "base" (the equal pool) and
    "variable" (the sqft pool), per the Esprit Park pattern. Pool keys
    are configurable so non-Old-Mill grouped HOAs can name them
    differently — defaults are the conventional ``equal_base`` and
    ``variable_costs``.
    """
    components_by_recipient: dict[int, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(_zero)
    )
    for row in result.pool_allocations:
        if row.recipient_ref.ref_type != "group":
            continue
        components_by_recipient[row.recipient_ref.ref_id][row.pool_key] += (
            row.unrounded_component_monthly
        )

    groups: list[dict[str, Any]] = []
    for total in result.recipient_totals:
        if total.recipient_ref.ref_type != "group":
            continue
        comps = components_by_recipient[total.recipient_ref.ref_id]
        base = comps.get(base_pool_key, _zero())
        variable = comps.get(variable_pool_key, _zero())
        unit_count = Decimal(total.recipient_ref.unit_count or 1)
        groups.append({
            "group_name": total.recipient_ref.label,
            "unit_count": total.recipient_ref.unit_count,
            "average_square_feet": total.recipient_ref.square_feet or _zero(),
            "base_monthly_per_unit": base / unit_count,
            "variable_monthly_per_unit": variable / unit_count,
            "total_monthly_per_unit": total.rounded_monthly_total / unit_count,
            "group_annual_total": total.annual_total,
        })

    total_annual = sum(
        (t.annual_total for t in result.recipient_totals if t.recipient_ref.ref_type == "group"),
        start=_zero(),
    )
    return {
        "hoa": {"name": hoa_name},
        "fiscal_year": fiscal_year,
        "groups": groups,
        "total_annual_revenue": total_annual,
    }


def build_per_unit_view(
    result: CalcResultSet,
    *,
    hoa_name: str,
    fiscal_year: int,
    pool_definitions: list[PoolDefinition],
) -> dict[str, Any]:
    """View dict for ``assessment_schedule/per_unit.html``.

    ``pool_definitions`` provides the column order + display names + the
    ``include_in_pdf`` flag (operator-controlled per-pool visibility).
    Each unit row carries a ``components`` dict mapping ``pool_key`` →
    monthly amount, plus ``total_monthly`` and ``annual_total``.
    """
    pool_columns = [
        {
            "pool_key": p.pool_key,
            "pool_name": p.pool_name,
            "include_in_pdf": p.include_in_pdf,
        }
        for p in sorted(pool_definitions, key=lambda d: d.display_order)
    ]

    components_by_recipient: dict[int, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(_zero)
    )
    for row in result.pool_allocations:
        if row.recipient_ref.ref_type != "unit":
            continue
        components_by_recipient[row.recipient_ref.ref_id][row.pool_key] += (
            row.unrounded_component_monthly
        )

    units: list[dict[str, Any]] = []
    for total in result.recipient_totals:
        if total.recipient_ref.ref_type != "unit":
            continue
        units.append({
            "unit_number": total.recipient_ref.label,
            "category": total.recipient_ref.category or "",
            "square_feet": total.recipient_ref.square_feet or _zero(),
            "components": dict(components_by_recipient[total.recipient_ref.ref_id]),
            "total_monthly": total.rounded_monthly_total,
            "annual_total": total.annual_total,
        })

    total_annual = sum(
        (t.annual_total for t in result.recipient_totals if t.recipient_ref.ref_type == "unit"),
        start=_zero(),
    )
    return {
        "hoa": {"name": hoa_name},
        "fiscal_year": fiscal_year,
        "pool_columns": pool_columns,
        "units": units,
        "total_annual_revenue": total_annual,
    }


def build_assessment_schedule_view(
    result: CalcResultSet,
    *,
    setup_type: SetupType,
    hoa_name: str,
    fiscal_year: int,
    pool_definitions: list[PoolDefinition] | None = None,
) -> dict[str, Any]:
    """Dispatch to the right view builder based on ``setup_type``.

    The compiler picks the template via ``template_for_setup_type``;
    this helper picks the matching context shape. Templates and
    contexts stay decoupled — the compiler can wire either one without
    knowing the other.
    """
    if setup_type == "fixed":
        return build_fixed_view(result, hoa_name=hoa_name, fiscal_year=fiscal_year)
    if setup_type == "grouped":
        return build_grouped_view(result, hoa_name=hoa_name, fiscal_year=fiscal_year)
    if setup_type == "per_unit":
        return build_per_unit_view(
            result,
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            pool_definitions=pool_definitions or [],
        )
    raise ValueError(f"Unknown setup_type {setup_type!r}")
