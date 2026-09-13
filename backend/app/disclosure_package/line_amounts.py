"""Single annual-amount resolver for mapping review and disclosure compile."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Tuple


def _get(line: Any, name: str, default: Any = None) -> Any:
    if isinstance(line, dict):
        return line.get(name, default)
    return getattr(line, name, default)


def _decimal_or_none(value: object) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def resolve_canonical_line_amount(line: Any) -> Tuple[Optional[Decimal], str]:
    """Resolve one annual amount with the package-year column precedence.

    Precedence: assessment_mapping_amount → proposed_amount →
    annual_budget × percent_change → projection → amount.
    """
    explicit = _decimal_or_none(_get(line, "assessment_mapping_amount"))
    if explicit is not None:
        return explicit, str(_get(line, "source_column_used") or "assessment_mapping_amount")

    proposed = _decimal_or_none(_get(line, "proposed_amount"))
    if proposed is None:
        proposed = _decimal_or_none(_get(line, "proposedAmount"))
    if proposed is not None:
        return proposed, "proposed_amount"

    annual = _decimal_or_none(_get(line, "annual_budget"))
    if annual is not None:
        pct = _get(line, "percent_change")
        if pct in (None, ""):
            pct = _get(line, "percentChange")
        if pct not in (None, ""):
            pct_dec = _decimal_or_none(pct)
            if pct_dec is not None:
                return (
                    annual * (Decimal("1") + pct_dec / Decimal("100")),
                    "annual_budget_percent_change",
                )
        return annual, "annual_budget"

    projection = _decimal_or_none(_get(line, "projection"))
    if projection is not None:
        return projection, "projection"

    amount = _decimal_or_none(_get(line, "amount"))
    if amount is not None:
        return amount, "amount"
    return None, "none"


def line_amount_column_conflict(line: Any) -> Optional[str]:
    """Return a blocking reason when mapping and disclosure would disagree.

    After both paths share ``resolve_canonical_line_amount`` they pick the
    same column. A stored ``source_column_used`` that disagrees with the
    resolver still needs an operator pick.
    """
    _amount, source = resolve_canonical_line_amount(line)
    stored = _get(line, "source_column_used")
    if stored in (None, "", source):
        return None
    if stored in {source, "proposed_amount", "proposedAmount"} and source == "proposed_amount":
        return None
    return (
        f"Line {(_get(line, 'label') or '').strip() or '(unnamed)'} has "
        f"source_column_used={stored!r} but the package resolver selects {source!r}."
    )
