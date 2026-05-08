"""Adapter layer between existing services and disclosure_package schemas.

CONTEXT D-03: the compiler MUST NOT import BudgetHistoryRecord,
ExtractedReserveStudyDocument, or Property directly. All cross-boundary
translation happens here. Phase 12+ adds new HOAs without touching this
file (only new package_specs/).

CONTEXT D-06 / threat T-11-04: every currency value is routed through
``_to_decimal`` (Decimal(str(value))) — this is the single chokepoint
that prevents float arithmetic from leaking into the calc engine.
RESEARCH Pitfall 2: Decimal(605.0) gives a binary-float artifact;
Decimal(str(605.0)) gives Decimal("605.0").

RESEARCH Pitfall 3: this adapter MUST NOT re-classify Phase 7 line
items. The `section`, `category`, `is_reserve`, `is_revenue`, and
`read_only` flags pass through unchanged.

RESEARCH Risk #3: Phase 10's reserve-study row schema is still in
flight. The reserve-study adapter accepts duck-typed objects (attribute
or dict access) so it tolerates schema evolution without a ripple
update across the boundary.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .schemas import (
    BudgetDraft,
    HOAMetadata,
    LineItem,
    ReserveStudyComponent,
    ReserveStudySnapshot,
)


def _to_decimal(value: Any) -> Decimal:
    """Coerce int/float/str/Decimal/None to Decimal via string round-trip.

    Pitfall 2 (RESEARCH): float arithmetic causes $0.01 drift. We never let
    a Python float reach the formula layer — Decimal(str(value)) preserves
    the shown precision (e.g., 605.0 -> Decimal("605.0"), not
    Decimal("605.0000000000000071...")).
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from either an object attribute or a dict key.

    Lets the reserve-study adapter accept SimpleNamespace, Pydantic models,
    SQLAlchemy rows, OR plain dicts without a ripple update if Phase 10
    changes its row class (RESEARCH Risk #3).
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def from_budget_history_record(record: Any) -> BudgetDraft:
    """Translate a Phase 4 BudgetHistoryRecord (or BudgetDraftPayload) → BudgetDraft.

    Trusts Phase 7 section-aware classification metadata on each line item
    (RESEARCH Pitfall 3 — do NOT re-classify). The source ``line_items`` is
    list[dict[str, Any]]; this adapter coerces money fields and shape-validates.

    Raises:
        ValueError: when ``line_items`` is missing or empty.
    """
    raw_items = _attr_or_key(record, "line_items")
    if not raw_items:
        raise ValueError(
            "budget_draft.line_items must contain at least one item"
        )
    items: list[LineItem] = []
    for raw in raw_items:
        # raw is expected to be a dict (Phase 4/7 line item shape) but we
        # accept attribute access too for forward compatibility.
        items.append(LineItem(
            label=str(_attr_or_key(raw, "label", "")),
            amount=_to_decimal(_attr_or_key(raw, "amount")),
            section=_attr_or_key(raw, "section"),
            category=_attr_or_key(raw, "category"),
            is_reserve=bool(_attr_or_key(raw, "is_reserve", False)),
            is_revenue=bool(_attr_or_key(raw, "is_revenue", False)),
            read_only=bool(_attr_or_key(raw, "read_only", False)),
        ))
    return BudgetDraft(line_items=items)


def from_reserve_study_extraction(document: Any) -> ReserveStudySnapshot:
    """Translate Phase 10's ExtractedReserveStudyDocument → ReserveStudySnapshot.

    Skips rows where ``useful_life`` is None or 0 (the formula DAG cannot
    consume them — division by zero in ``year_replacement_provision``).
    Phase 10's row schema may evolve (RESEARCH Risk #3), so this adapter
    accepts duck-typed objects (attribute OR dict access).

    Rows missing ``remaining_life`` or ``replacement_cost`` are also
    skipped — they are likely header rows or extraction artifacts.
    """
    study_date = _attr_or_key(document, "study_date") or ""
    raw_rows = _attr_or_key(document, "rows") or []
    components: list[ReserveStudyComponent] = []
    for raw in raw_rows:
        useful_life = _attr_or_key(raw, "useful_life")
        if useful_life in (None, 0):
            continue
        remaining_life = _attr_or_key(raw, "remaining_life")
        replacement_cost = _attr_or_key(raw, "replacement_cost")
        if remaining_life is None or replacement_cost is None:
            continue
        line_item = _attr_or_key(raw, "line_item")
        year_new = _attr_or_key(raw, "year_new")
        components.append(ReserveStudyComponent(
            line_item=str(line_item or "(unnamed)"),
            useful_life=int(useful_life),
            remaining_life=int(remaining_life),
            replacement_cost=_to_decimal(replacement_cost),
            year_new=year_new,
        ))
    return ReserveStudySnapshot(study_date=str(study_date), components=components)


def from_hoa_record(property_row: Any) -> HOAMetadata:
    """Translate a Property ORM row → HOAMetadata.

    Raises:
        ValueError: when ``units`` is None or non-positive (an HOA with
            no units cannot be assessed; the formula DAG would divide by
            zero in per-unit calculations).
    """
    units = _attr_or_key(property_row, "units")
    if not units or units <= 0:
        raise ValueError("hoa_metadata.units must be a positive integer")
    return HOAMetadata(
        hoa_id=int(_attr_or_key(property_row, "id")),
        name=str(_attr_or_key(property_row, "name", "")),
        units=int(units),
        fiscal_year_start_month=int(
            _attr_or_key(property_row, "fiscal_year_start_month", 1) or 1
        ),
        fiscal_year_end_month=int(
            _attr_or_key(property_row, "fiscal_year_end_month", 12) or 12
        ),
        tax_id=_attr_or_key(property_row, "tax_id"),
    )
