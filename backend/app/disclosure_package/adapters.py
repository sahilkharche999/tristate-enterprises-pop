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
    ReserveFundingPlanRow,
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


# Category → (is_revenue, is_reserve) lookup. Mirrors
# services.budget_history_service.SECTION_KINDS ('income', 'operating',
# 'reserve_income', 'reserve_expense'). When the upstream extractor stores
# a category but not the boolean flags, we derive them here so the
# disclosure compiler doesn't have to know about the budget vocabulary.
_CATEGORY_TO_FLAGS: dict[str, tuple[bool, bool]] = {
    "income": (True, False),            # operating revenue
    "operating": (False, False),        # operating expense
    "reserve_income": (True, True),     # reserve revenue
    "reserve_expense": (False, True),   # reserve expense
}


def from_budget_history_record(record: Any) -> BudgetDraft:
    """Translate a Phase 4 BudgetHistoryRecord (or BudgetDraftPayload) → BudgetDraft.

    Trusts Phase 7 section-aware classification metadata on each line item
    (RESEARCH Pitfall 3 — do NOT re-classify the *category*; we only derive
    is_revenue/is_reserve from it when the explicit booleans are absent).
    The source ``line_items`` is list[dict[str, Any]]; this adapter coerces
    money fields and shape-validates.

    Money field resolution (priority order):
        1. proposed_amount — when the operator has explicitly approved a
           proposed 2026 number distinct from the prior annual budget.
        2. annual_budget — the canonical forward-looking budget value the
           UI surfaces in the "Annual Budget" / "Proposed Change" columns.
        3. amount — legacy synonym, kept for the existing direct-payload tests.

    Classification resolution:
        - is_revenue / is_reserve are read from explicit booleans when present.
        - Otherwise derived from ``category`` via _CATEGORY_TO_FLAGS so that
          rows produced by the budget extractor (which sets only category)
          land in the correct revenue / expense / operating / replacement
          bucket downstream.

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
        # Money: try proposed → annual_budget → amount, first non-None wins.
        money_value: Any = None
        for field in ("proposed_amount", "annual_budget", "amount"):
            candidate = _attr_or_key(raw, field)
            if candidate is not None:
                money_value = candidate
                break

        explicit_revenue = _attr_or_key(raw, "is_revenue")
        explicit_reserve = _attr_or_key(raw, "is_reserve")
        category = _attr_or_key(raw, "category")
        derived_revenue, derived_reserve = _CATEGORY_TO_FLAGS.get(
            (category or "").strip().lower(), (False, False)
        )

        items.append(LineItem(
            label=str(_attr_or_key(raw, "label", "")),
            amount=_to_decimal(money_value),
            section=_attr_or_key(raw, "section"),
            category=category,
            is_reserve=(
                bool(explicit_reserve) if explicit_reserve is not None
                else derived_reserve
            ),
            is_revenue=(
                bool(explicit_revenue) if explicit_revenue is not None
                else derived_revenue
            ),
            read_only=bool(_attr_or_key(raw, "read_only", False)),
            # Preserve account_code (LineItem is extra="allow") so the render's
            # assessment-mapping review-match keys off the SAME account_code the
            # operator mappings were stored with. When the source statement has
            # no account codes this is None on both sides, so the match is
            # unaffected; when codes exist, dropping it here silently orphaned
            # every mapping and blocked the render.
            account_code=_attr_or_key(raw, "account_code"),
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
    raw_funding_rows = _attr_or_key(document, "funding_plan_rows") or []
    components: list[ReserveStudyComponent] = []
    funding_plan_rows: list[ReserveFundingPlanRow] = []
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
    for raw in raw_funding_rows:
        year = _attr_or_key(raw, "year")
        if year is None:
            continue
        funding_plan_rows.append(ReserveFundingPlanRow(
            year=int(year),
            beginning_balance=_to_decimal(_attr_or_key(raw, "beginning_balance"))
            if _attr_or_key(raw, "beginning_balance") is not None else None,
            annual_contribution=_to_decimal(_attr_or_key(raw, "annual_contribution"))
            if _attr_or_key(raw, "annual_contribution") is not None else None,
            monthly_per_unit=_to_decimal(_attr_or_key(raw, "monthly_per_unit"))
            if _attr_or_key(raw, "monthly_per_unit") is not None else None,
            interest_income=_to_decimal(_attr_or_key(raw, "interest_income"))
            if _attr_or_key(raw, "interest_income") is not None else None,
            reserve_expenditures=_to_decimal(_attr_or_key(raw, "reserve_expenditures"))
            if _attr_or_key(raw, "reserve_expenditures") is not None else None,
            ending_balance=_to_decimal(_attr_or_key(raw, "ending_balance"))
            if _attr_or_key(raw, "ending_balance") is not None else None,
            fully_funded_balance=_to_decimal(_attr_or_key(raw, "fully_funded_balance"))
            if _attr_or_key(raw, "fully_funded_balance") is not None else None,
            percent_funded=_to_decimal(_attr_or_key(raw, "percent_funded"))
            if _attr_or_key(raw, "percent_funded") is not None else None,
            source_page=_attr_or_key(raw, "source_page"),
        ))
    return ReserveStudySnapshot(
        study_date=str(study_date),
        components=components,
        funding_plan_rows=funding_plan_rows,
    )


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
        city=_attr_or_key(property_row, "city"),
        state=_attr_or_key(property_row, "state"),
        entity_type=_attr_or_key(property_row, "entity_type"),
        incorporation_year=_attr_or_key(property_row, "incorporation_year"),
    )
