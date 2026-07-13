"""Image-first reserve-study extraction using reserve-study-specific PDF helpers."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from math import isclose
from pathlib import Path
from typing import Any, Optional

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field

from ..ai_implementation.pipeline.document_extraction_provider import DocumentPromptContext, RenderedPage
from ..ai_implementation.pipeline.llm_client import call_llm_vision
from ..models.financial_document_extraction import DocumentExtractionFailure
from ..models.reserve_study_extraction import (
    ExtractedReserveStudyDocument,
    ExtractedReserveStudyPage,
    ExtractedReserveStudyRow,
    ReserveStudyDiscoveryResult,
    ReserveStudyPageClassification,
    ReserveStudyPageRole,
    ReserveStudyPageSpan,
)
from .pdf_vlm_extractor import (
    _extract_pdf_text_table,
    _is_scanned_pdf_error,
    _split_pages,
    render_pdf_pages,
)

logger = logging.getLogger(__name__)

_TARGET_UI_FIELDS = {
    "line_item",
    "quantity",
    "useful_life",
    "remaining_life",
    "replacement_cost",
}
_PRIMARY_UI_FIELDS = {
    "line_item",
    "useful_life",
    "remaining_life",
    "replacement_cost",
}
_OPTIONAL_UI_FIELDS = _TARGET_UI_FIELDS - _PRIMARY_UI_FIELDS

_DISCOVERY_SYSTEM_PROMPT = (
    "You are identifying reserve-study table pages inside an HOA reserve-study PDF.\n"
    "Classify EVERY page provided in this request.\n"
    "- reserve_table: page contains component rows or continuation rows that can populate our reserve-study UI fields: "
    "line_item, useful_life, remaining_life, quantity, replacement_cost.\n"
    "- reserve_context: page is directly adjacent supporting context for the same component schedule, but not the rows themselves.\n"
    "- unrelated: page is boilerplate, cover, insurance, disclosures, or non-table content.\n"
    "Reserve-study table pages usually contain headers like Useful Life, Remaining Life, Quantity, "
    "Replacement Cost, Current Cost, Future Cost, Component Inventory, or line-item replacement schedules.\n"
    "Forecasts, funding summaries, percent funded pages, cash-flow projections, assumptions, disclosures, narrative pages, "
    "and reserve fund income statements are NOT reserve_table pages.\n"
    "Use vendor-agnostic judgment. Do not rely on exact report titles.\n"
    "For each page, also return:\n"
    "- ui_fields_present: which of these UI fields are visibly present on the page: line_item, quantity, useful_life, remaining_life, replacement_cost. "
    "Only mark useful_life or remaining_life as present when their NUMERIC VALUES are visible on this page — not when only the column header appears, "
    "and not when only the component name is shown without an aligned numeric column for that field. Quantity is helpful but optional.\n"
    "- is_primary_ui_table: true only if this page belongs to the main editable component schedule that best fits the reserve-study UI\n"
    "- same_table_as_previous: true if this page is a continuation of the same table as the previous page\n"
    "- same_table_as_next: true if this page continues onto the next page as the same table\n"
    "- table_title_hint: the visible table/report title if present, otherwise null\n"
    "- is_tabular_schedule: true only when the page is a compact row-and-column schedule with repeated aligned headers and multiple components represented as rows\n"
    "- is_component_detail_appendix: true when the page is a component-detail appendix with one or a few components shown as narrative/detail blocks, often with comments, history, location, best case, or worst case text\n"
    "- adds_new_component_rows: true only when this page introduces additional component rows that are needed for extraction, not just repeated versions of the same rows\n"
    "- is_duplicate_component_repeat_page: true when this page mostly repeats the same component names from an adjacent page and mainly changes forecast years, totals, liabilities, or other derived columns instead of adding new component rows\n"
    "- is_year_provision_or_liability_schedule: true when the page is mainly about current estimated liability, annual replacement provision, reserve cash balance, or a budget-year funding summary rather than the core reserve-study plan\n"
    "If multiple reserve-related tables exist, prefer the sequence that most directly supports editing component rows with the UI fields above. "
    "Pages showing the same components for funding analysis, significance analysis, tax/accounting summaries, long-range projections, or other derived "
    "reports should not be marked as the primary UI table when a more direct component schedule exists.\n"
    "The primary extraction target is the dense tabular schedule, not the component detail appendix. "
    "Do not mark narrative/detail appendix pages as the primary UI table when a compact tabular schedule exists.\n"
    "A schedule centered on annual replacement provision or estimated liability should lose to a reserve-study plan schedule when both describe the same components.\n"
    "When a compact anchor page already contains the editable component columns, pages that repeat the same components only to show different year bands or derived columns should be marked as duplicate component repeat pages.\n"
    "Year-organized cash-flow or expenditure schedules are NOT the primary reserve-table target, "
    "regardless of the vendor's wording. These pages share the same structure across vendors:\n"
    "  - the page is fundamentally organized by year (rows or banded sections keyed to a year, not to a component);\n"
    "  - it has only one or two numeric columns (typically a single expenditure or cash-flow value per row);\n"
    "  - the same component appears multiple times across different years, or year labels appear as section headers;\n"
    "  - useful life and remaining life columns are absent, or the columns exist but the numeric cells are empty for these rows.\n"
    "Examples of titles vendors use for this kind of page include (non-exhaustive, vendor-agnostic — match on structure, not exact text): "
    "Annual Expenditure Detail, Cash Flow Detail, Cash Flow Summary, Cash Flow Funding Plan, Yearly Cash Flow, Year-by-Year Expenditures, "
    "Funding Analysis, Reserve Cash Flow, 30-Year Cash Flow, Annual Funding Plan. Section headers within these pages often read like "
    "'Replacement Year YYYY', 'Total for YYYY', 'No Replacement in YYYY', 'YYYY Expenditures', or just a bare year. "
    "When a page matches this structure, mark it is_year_provision_or_liability_schedule=true AND is_primary_ui_table=false, "
    "even when component names are visible.\n"
    "The primary reserve-table is the one where each component appears EXACTLY ONCE with its lifecycle columns "
    "(useful life, remaining life, replacement cost), regardless of the vendor's label for that table — "
    "common labels include Component Funding Summary, Component Inventory, Component Inventory Detail Report, "
    "Component Details, Component Inventory and Analysis, or simply Reserve Component Schedule.\n"
    "Use the rendered page images as the source of truth for this classification.\n"
    "Return one classification entry per page number in the input, preserving page numbers exactly.\n"
    "Return only the structured classification."
)

_EXTRACTION_SYSTEM_PROMPT = (
    "You are extracting reserve-study component rows from ONE reserve-study table page.\n"
    "Return both reserve-study table header rows and component rows in visible order.\n"
    "For header rows, set row_type='header', put the visible header text in line_item, and leave numeric/value fields null.\n"
    "For component rows, set row_type='item' and use these fields when they are explicitly visible: line_item, useful_life, remaining_life, quantity, replacement_cost, year_new, year_replacement_provision, estimated_liability.\n"
    "Also return page-level metadata when explicitly visible: study_date, study_year, applicable_fiscal_year, reference_year, first_forecast_year.\n"
    "Only extract values that are explicitly visible on the page image or in the supplied page text.\n"
    "Do not calculate, infer, or derive missing values from other fields or outside knowledge.\n"
    "Be flexible about visible header wording and synonyms across all output fields when the page clearly shows the same concept.\n"
    "Examples of acceptable visible synonyms include: Description, Component, Component Name, Item, or Asset for line_item; Useful Life, Est. Life, Expect. Life, Life, or Total Life for useful_life; Remaining Life, Rem. Life, Remaining Useful Life, or RUL for remaining_life; Quantity, Qty, Item Quan., Units, or Count for quantity; Future Cost, Current Cost, Current Repl. Cost, Estimated Cost, Total Cost, or similar reserve-cost headers for replacement_cost; Year New, Install Year, or New Year for year_new; Year Rplc. Prov., Annual Provision, Replacement Provision, or similar yearly provision wording for year_replacement_provision; Est. Liab., Estimated Liability, Liability, or Fully Liability when that liability value is explicitly shown for the component.\n"
    "Use only the value explicitly shown under the visible column that matches the concept best.\n"
    "Do not combine multiple columns, do not reinterpret dates or years as direct field values, and do not convert one field into another through arithmetic.\n"
    "Do not compute remaining_life from current year, study year, applicable fiscal year, YEAR NEW, EXPECTED LIFE, or any similar columns unless a remaining-life value is explicitly shown.\n"
    "Do not compute year_replacement_provision or estimated_liability unless those values are explicitly printed on the page.\n"
    "Keep rows even when some values are blank or unclear.\n"
    "Do not summarize. Do not drop incomplete rows.\n"
    "Use null for blank cells. Keep warnings concise.\n"
    "Return only structured JSON."
)

_DISCOVERY_BATCH_SIZE = 10
_DISCOVERY_RENDER_DPI = 72
_VISION_ONLY_EXTRACTION_DPI = 200
_RESERVE_VISION_TIMEOUT_SECONDS = 60.0


class _ReserveStudyBatchClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classifications: list[ReserveStudyPageClassification] = Field(default_factory=list)


_WHOLE_DOLLAR = Decimal("1")


def _round_half_even_whole_dollars(value: float) -> int:
    return int(Decimal(str(value)).quantize(_WHOLE_DOLLAR, rounding=ROUND_HALF_EVEN))


def _resolve_reference_year(
    *,
    explicit_reference_year: Optional[int],
    first_forecast_year: Optional[int],
    applicable_fiscal_year: Optional[int],
    study_year: Optional[int],
) -> Optional[int]:
    if explicit_reference_year is not None:
        return explicit_reference_year
    if first_forecast_year is not None:
        return first_forecast_year - 1
    if applicable_fiscal_year is not None:
        return applicable_fiscal_year - 1
    return study_year


def _canonicalize_single_reserve_row(
    row: ExtractedReserveStudyRow,
    *,
    reference_year: Optional[int],
) -> ExtractedReserveStudyRow:
    if row.row_type == "header":
        return row.model_copy(
            update={
                "useful_life": None,
                "remaining_life": None,
                "quantity": None,
                "replacement_cost": None,
                "year_new": None,
                "reference_year": None,
                "year_replacement_provision": None,
                "estimated_liability": None,
                "flags": [],
            }
        )

    normalized_remaining_life = row.remaining_life
    normalized_reference_year = row.reference_year or reference_year

    if (
        normalized_remaining_life is None
        and row.year_new is not None
        and normalized_reference_year is not None
        and row.useful_life is not None
    ):
        # A component's age cannot be negative. When YEAR NEW is at or after the
        # reference year (a brand-new or future-scheduled component, e.g. a
        # sealing/repair item placed in service next cycle), the component has
        # not aged yet, so its remaining life is its full useful life — never
        # more. Flooring age at 0 keeps remaining_life within [0, useful_life];
        # without it, a future YEAR NEW yields remaining_life > useful_life,
        # which is logically impossible and corrupts the downstream
        # estimated_liability ((useful_life - remaining_life) / useful_life).
        age = max(normalized_reference_year - row.year_new, 0)
        normalized_remaining_life = max(row.useful_life - age, 0)

    year_replacement_provision = row.year_replacement_provision
    if (
        year_replacement_provision is None
        and row.replacement_cost is not None
        and row.useful_life not in (None, 0)
    ):
        year_replacement_provision = _round_half_even_whole_dollars(row.replacement_cost / row.useful_life)

    # H11: a Gemini-supplied remaining_life greater than useful_life is
    # logically impossible and makes the liability formula
    # ((useful_life - remaining_life) / useful_life) go NEGATIVE. The model's
    # ge=0 guard only fires on validated fields, and model_copy below skips
    # re-validation — so without this check a negative liability would be
    # stored and silently offset other components' sum. Never compute a
    # liability from inconsistent lifecycle values; flag the row for operator
    # review instead. (The derived-remaining-life path above is already
    # clamped to [0, useful_life], so this only guards Gemini-supplied values.)
    lifecycle_inconsistent = (
        row.useful_life is not None
        and normalized_remaining_life is not None
        and normalized_remaining_life > row.useful_life
    )

    estimated_liability = row.estimated_liability
    if (
        estimated_liability is None
        and row.replacement_cost is not None
        and row.useful_life not in (None, 0)
        and normalized_remaining_life is not None
        and not lifecycle_inconsistent
    ):
        estimated_liability = _round_half_even_whole_dollars(
            row.replacement_cost * ((row.useful_life - normalized_remaining_life) / row.useful_life)
        )

    extra_flags = list(row.flags)
    if lifecycle_inconsistent and "lifecycle_inconsistent" not in extra_flags:
        extra_flags.append("lifecycle_inconsistent")

    normalized = row.model_copy(
        update={
            "remaining_life": normalized_remaining_life,
            "reference_year": normalized_reference_year,
            "year_replacement_provision": year_replacement_provision,
            "estimated_liability": estimated_liability,
            "flags": extra_flags,
        }
    )
    return _apply_row_flags(normalized)


def _make_unique_reserve_row_id(base_row_id: str, used_row_ids: set[str]) -> str:
    base = str(base_row_id or "reserve-row").strip() or "reserve-row"
    candidate = base
    suffix = 2
    while candidate in used_row_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_row_ids.add(candidate)
    return candidate


def _ensure_unique_reserve_row_ids(
    rows: list[ExtractedReserveStudyRow],
) -> list[ExtractedReserveStudyRow]:
    used_row_ids: set[str] = set()
    unique_rows: list[ExtractedReserveStudyRow] = []
    for row in rows:
        unique_row_id = _make_unique_reserve_row_id(row.row_id, used_row_ids)
        if unique_row_id == row.row_id:
            unique_rows.append(row)
        else:
            unique_rows.append(row.model_copy(update={"row_id": unique_row_id}))
    return unique_rows


def canonicalize_reserve_study_rows(
    rows: list[ExtractedReserveStudyRow],
    *,
    explicit_reference_year: Optional[int] = None,
    first_forecast_year: Optional[int] = None,
    applicable_fiscal_year: Optional[int] = None,
    study_year: Optional[int] = None,
) -> tuple[list[ExtractedReserveStudyRow], Optional[int]]:
    resolved_reference_year = _resolve_reference_year(
        explicit_reference_year=explicit_reference_year,
        first_forecast_year=first_forecast_year,
        applicable_fiscal_year=applicable_fiscal_year,
        study_year=study_year,
    )
    canonical_rows = [
        _canonicalize_single_reserve_row(row, reference_year=resolved_reference_year)
        for row in rows
    ]
    return (
        _ensure_unique_reserve_row_ids(canonical_rows),
        resolved_reference_year,
    )


def _make_unique_reserve_row_dict(
    row: dict[str, Any],
    used_row_ids: set[str],
) -> dict[str, Any]:
    unique_row_id = _make_unique_reserve_row_id(str(row.get("row_id") or "reserve-row"), used_row_ids)
    if unique_row_id == row.get("row_id"):
        return row
    return {**row, "row_id": unique_row_id}


def canonicalize_reserve_study_row_dicts(
    rows: list[dict[str, Any]],
    *,
    explicit_reference_year: Optional[int] = None,
    first_forecast_year: Optional[int] = None,
    applicable_fiscal_year: Optional[int] = None,
    study_year: Optional[int] = None,
) -> tuple[list[dict[str, Any]], Optional[int]]:
    resolved_reference_year = _resolve_reference_year(
        explicit_reference_year=explicit_reference_year,
        first_forecast_year=first_forecast_year,
        applicable_fiscal_year=applicable_fiscal_year,
        study_year=study_year,
    )
    used_row_ids: set[str] = set()
    normalized_rows: list[dict[str, Any]] = []

    for row in rows:
        try:
            parsed_row = ExtractedReserveStudyRow.model_validate(row)
        except Exception:
            normalized_rows.append(_make_unique_reserve_row_dict(row, used_row_ids))
            continue

        canonical_row = _canonicalize_single_reserve_row(
            parsed_row,
            reference_year=resolved_reference_year,
        )
        unique_row_id = _make_unique_reserve_row_id(canonical_row.row_id, used_row_ids)
        if unique_row_id != canonical_row.row_id:
            canonical_row = canonical_row.model_copy(update={"row_id": unique_row_id})
        normalized_rows.append(canonical_row.model_dump(mode="json"))

    return normalized_rows, resolved_reference_year


@dataclass
class _PreparedReserveStudyPage:
    page_number: int
    page_image: Optional[RenderedPage]


@dataclass
class _PreparedReserveStudyDocument:
    prompt_context: DocumentPromptContext
    pages: list[_PreparedReserveStudyPage]
    extraction_metadata: dict[str, object]


@dataclass
class _ReserveStudySequence:
    items: list[ReserveStudyPageClassification]

    @property
    def start_page(self) -> int:
        return self.items[0].page_number

    @property
    def end_page(self) -> int:
        return self.items[-1].page_number

    @property
    def relevant_pages(self) -> list[ReserveStudyPageClassification]:
        return [
            item
            for item in self.items
            if item.role in {ReserveStudyPageRole.RESERVE_TABLE, ReserveStudyPageRole.RESERVE_CONTEXT}
        ]

    @property
    def table_pages(self) -> list[ReserveStudyPageClassification]:
        return [
            item
            for item in self.items
            if item.role == ReserveStudyPageRole.RESERVE_TABLE
        ]


def _get_pdf_page_count(path: str) -> int:
    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


def _merge_page_spans(classifications: list[ReserveStudyPageClassification]) -> list[ReserveStudyPageSpan]:
    relevant = [
        item
        for item in sorted(classifications, key=lambda item: item.page_number)
        if item.role in {ReserveStudyPageRole.RESERVE_TABLE, ReserveStudyPageRole.RESERVE_CONTEXT}
    ]
    if not relevant:
        return []

    spans: list[ReserveStudyPageSpan] = []
    start = relevant[0].page_number
    end = relevant[0].page_number
    confidences = [relevant[0].confidence]
    roles = [relevant[0].role]

    for item in relevant[1:]:
        if item.page_number == end + 1:
            end = item.page_number
            confidences.append(item.confidence)
            roles.append(item.role)
            continue

        if ReserveStudyPageRole.RESERVE_TABLE in roles:
            spans.append(
                ReserveStudyPageSpan(
                    start_page=start,
                    end_page=end,
                    confidence=sum(confidences) / len(confidences),
                )
            )
        start = item.page_number
        end = item.page_number
        confidences = [item.confidence]
        roles = [item.role]

    if ReserveStudyPageRole.RESERVE_TABLE in roles:
        spans.append(
            ReserveStudyPageSpan(
                start_page=start,
                end_page=end,
                confidence=sum(confidences) / len(confidences),
            )
        )
    return spans


def _promote_context_pages_between_tables(
    classifications: list[ReserveStudyPageClassification],
) -> list[ReserveStudyPageClassification]:
    if not classifications:
        return []

    ordered = sorted(classifications, key=lambda item: item.page_number)
    roles_by_page = {item.page_number: item.role for item in ordered}
    promoted_pages: set[int] = set()

    table_pages = [item.page_number for item in ordered if item.role == ReserveStudyPageRole.RESERVE_TABLE]
    for left_page, right_page in zip(table_pages, table_pages[1:]):
        if right_page - left_page <= 1:
            continue
        between_pages = range(left_page + 1, right_page)
        between_roles = [roles_by_page.get(page_number) for page_number in between_pages]
        if between_roles and all(role == ReserveStudyPageRole.RESERVE_CONTEXT for role in between_roles):
            promoted_pages.update(between_pages)

    return [
        item.model_copy(update={"role": ReserveStudyPageRole.RESERVE_TABLE})
        if item.page_number in promoted_pages
        else item
        for item in ordered
    ]


def _has_sequence_metadata(classifications: list[ReserveStudyPageClassification]) -> bool:
    return any(
        item.ui_fields_present
        or item.is_primary_ui_table
        or item.is_tabular_schedule
        or item.is_component_detail_appendix
        or item.adds_new_component_rows
        or item.is_duplicate_component_repeat_page
        or item.is_year_provision_or_liability_schedule
        or item.same_table_as_previous
        or item.same_table_as_next
        or item.table_title_hint
        for item in classifications
    )


def _build_relevant_sequences(
    classifications: list[ReserveStudyPageClassification],
) -> list[_ReserveStudySequence]:
    relevant = [
        item
        for item in sorted(classifications, key=lambda item: item.page_number)
        if item.role in {ReserveStudyPageRole.RESERVE_TABLE, ReserveStudyPageRole.RESERVE_CONTEXT}
    ]
    if not relevant:
        return []

    sequences: list[_ReserveStudySequence] = []
    current_items = [relevant[0]]

    for previous, current in zip(relevant, relevant[1:]):
        is_adjacent = current.page_number == previous.page_number + 1
        is_linked = (
            previous.same_table_as_next
            or current.same_table_as_previous
            or previous.role == ReserveStudyPageRole.RESERVE_CONTEXT
            or current.role == ReserveStudyPageRole.RESERVE_CONTEXT
        )
        if is_adjacent and is_linked:
            current_items.append(current)
            continue
        sequences.append(_ReserveStudySequence(items=current_items))
        current_items = [current]

    sequences.append(_ReserveStudySequence(items=current_items))
    return sequences


def _sequence_field_coverage(
    pages: list[ReserveStudyPageClassification],
) -> tuple[set[str], set[str]]:
    target_fields = {
        field_name
        for item in pages
        for field_name in item.ui_fields_present
        if field_name in _TARGET_UI_FIELDS
    }
    primary_fields = target_fields & _PRIMARY_UI_FIELDS
    optional_fields = target_fields & _OPTIONAL_UI_FIELDS
    return primary_fields, optional_fields


def _sequence_score(sequence: _ReserveStudySequence) -> tuple[int, int, int, int, int, int, int, float, int]:
    tabular_pages = sum(1 for item in sequence.table_pages if item.is_tabular_schedule)
    primary_fields, optional_fields = _sequence_field_coverage(sequence.table_pages)
    primary_pages = sum(1 for item in sequence.table_pages if item.is_primary_ui_table)
    appendix_pages = sum(1 for item in sequence.relevant_pages if item.is_component_detail_appendix)
    provision_liability_pages = sum(
        1 for item in sequence.relevant_pages if item.is_year_provision_or_liability_schedule
    )
    avg_confidence = sum(item.confidence for item in sequence.items) / len(sequence.items)
    # Highest priority: NO year-provision / annual-schedule / liability pages
    # in the span. A clean component-summary span (3 pages) must outrank a
    # longer annual-expenditure span (8 pages), regardless of column coverage
    # or page count. Without this gate, the longer schedule wins on len(items).
    provision_clean = 1 if provision_liability_pages == 0 else 0
    return (
        provision_clean,
        tabular_pages,
        len(primary_fields),
        len(optional_fields),
        primary_pages,
        -appendix_pages,
        -provision_liability_pages,  # tiebreaker between "dirty" sequences
        avg_confidence,
        len(sequence.items),
    )


def _trim_sequence_to_anchor_range(sequence: _ReserveStudySequence) -> _ReserveStudySequence:
    if len(sequence.items) <= 1:
        return sequence

    essential_table_pages = [
        item
        for item in sequence.table_pages
        if item.adds_new_component_rows or not item.is_duplicate_component_repeat_page
    ]
    if not essential_table_pages:
        return sequence

    start_page = essential_table_pages[0].page_number
    end_page = essential_table_pages[-1].page_number
    trimmed_items = [
        item
        for item in sequence.items
        if start_page <= item.page_number <= end_page
    ]
    return _trim_redundant_trailing_pages(_ReserveStudySequence(items=trimmed_items))


def _trim_redundant_trailing_pages(sequence: _ReserveStudySequence) -> _ReserveStudySequence:
    items = list(sequence.items)
    if len(items) <= 1:
        return sequence

    first_table_page = next((item for item in items if item.role == ReserveStudyPageRole.RESERVE_TABLE), None)
    if first_table_page is None:
        return sequence

    anchor_primary_fields = set(first_table_page.ui_fields_present) & _PRIMARY_UI_FIELDS
    if not anchor_primary_fields:
        return sequence

    current_sequence = _ReserveStudySequence(items=items)
    current_primary_fields, _ = _sequence_field_coverage(current_sequence.table_pages)
    if current_primary_fields != anchor_primary_fields:
        return sequence

    while len(current_sequence.items) > 1:
        last_item = current_sequence.items[-1]
        if last_item.role != ReserveStudyPageRole.RESERVE_TABLE:
            current_sequence = _ReserveStudySequence(items=current_sequence.items[:-1])
            continue

        last_primary_fields = set(last_item.ui_fields_present) & _PRIMARY_UI_FIELDS
        if len(last_primary_fields) >= len(anchor_primary_fields):
            break

        candidate_sequence = _ReserveStudySequence(items=current_sequence.items[:-1])
        candidate_primary_fields, _ = _sequence_field_coverage(candidate_sequence.table_pages)
        if candidate_primary_fields != current_primary_fields:
            break
        current_sequence = candidate_sequence

    return current_sequence


def _retain_best_sequence(
    classifications: list[ReserveStudyPageClassification],
) -> tuple[list[ReserveStudyPageClassification], list[_ReserveStudySequence], Optional[_ReserveStudySequence]]:
    if not classifications or not _has_sequence_metadata(classifications):
        return sorted(classifications, key=lambda item: item.page_number), _build_relevant_sequences(classifications), None

    sequences = [
        sequence
        for sequence in _build_relevant_sequences(classifications)
        if sequence.table_pages
    ]
    if not sequences:
        return sorted(classifications, key=lambda item: item.page_number), sequences, None

    tabular_sequences = [
        sequence
        for sequence in sequences
        if any(item.is_tabular_schedule for item in sequence.table_pages)
    ]
    candidate_sequences = tabular_sequences or sequences

    winning_sequence = max(
        candidate_sequences,
        key=lambda sequence: (_sequence_score(sequence), -sequence.start_page),
    )
    winning_sequence = _trim_sequence_to_anchor_range(winning_sequence)
    winning_pages = {item.page_number for item in winning_sequence.items}
    filtered = [
        item
        if item.page_number in winning_pages
        else item.model_copy(
            update={
                "role": ReserveStudyPageRole.UNRELATED,
                "confidence": 0.0,
                "reasons": [*item.reasons, "discarded_non_primary_sequence"],
                "is_primary_ui_table": False,
                "same_table_as_previous": False,
                "same_table_as_next": False,
                "adds_new_component_rows": False,
                "is_duplicate_component_repeat_page": False,
                "is_year_provision_or_liability_schedule": False,
            }
        )
        for item in sorted(classifications, key=lambda item: item.page_number)
    ]
    return filtered, sequences, winning_sequence


async def _classify_page_batch(
    *,
    pages: list[_PreparedReserveStudyPage],
    prompt_context: DocumentPromptContext,
) -> list[ReserveStudyPageClassification]:
    prompt_sections: list[str] = [
        f"Filename: {prompt_context.filename}",
        "Classify these pages from the rendered images only.",
    ]
    user_content: list[dict[str, object]] = []
    expected_page_numbers = [page.page_number for page in pages]

    for page in pages:
        prompt_sections.append(
            f"Page: {page.page_number}\nUse the image for this page to decide whether it contains reserve-study component rows for the UI fields."
        )
        if page.page_image is not None and page.page_image.content is not None:
            user_content.append(
                {
                    "type": "text",
                    "text": f"Image for Page: {page.page_number}",
                }
            )
            user_content.append(
                {
                    "type": "image",
                    "data": page.page_image.content,
                    "mime_type": page.page_image.mime_type,
                }
            )

    user_content.insert(0, {"type": "text", "text": "\n\n".join(prompt_sections)})
    result = await call_llm_vision(
        [
            {"role": "system", "content": _DISCOVERY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        _ReserveStudyBatchClassification,
        temperature=0.0,
        timeout=_RESERVE_VISION_TIMEOUT_SECONDS,
    )
    if result is None:
        return [
            ReserveStudyPageClassification(
                page_number=page_number,
                role=ReserveStudyPageRole.UNRELATED,
                confidence=0.0,
                reasons=["classifier returned no result"],
            )
            for page_number in expected_page_numbers
        ]

    classifications_by_page = {
        item.page_number: item
        for item in result.classifications
    }
    normalized: list[ReserveStudyPageClassification] = []
    for page_number in expected_page_numbers:
        item = classifications_by_page.get(page_number)
        if item is None:
            normalized.append(
                ReserveStudyPageClassification(
                    page_number=page_number,
                    role=ReserveStudyPageRole.UNRELATED,
                    confidence=0.0,
                    reasons=["classifier omitted page from batch response"],
                )
            )
            continue
        normalized_item = item if item.page_number == page_number else item.model_copy(update={"page_number": page_number})
        if normalized_item.is_component_detail_appendix and normalized_item.is_primary_ui_table:
            normalized_item = normalized_item.model_copy(update={"is_primary_ui_table": False})
        if not normalized_item.is_tabular_schedule and normalized_item.is_primary_ui_table:
            normalized_item = normalized_item.model_copy(update={"is_primary_ui_table": False})
        if normalized_item.is_duplicate_component_repeat_page and normalized_item.adds_new_component_rows:
            normalized_item = normalized_item.model_copy(update={"is_duplicate_component_repeat_page": False})
        if normalized_item.is_year_provision_or_liability_schedule and normalized_item.is_duplicate_component_repeat_page:
            normalized_item = normalized_item.model_copy(update={"is_duplicate_component_repeat_page": False})
        normalized.append(normalized_item)
    return normalized


async def _extract_reserve_page(
    *,
    page_number: int,
    page_text: str,
    page_image: Optional[RenderedPage],
    prompt_context: DocumentPromptContext,
    text_is_empty: bool,
) -> Optional[ExtractedReserveStudyPage]:
    if text_is_empty:
        prompt_body = (
            f"Filename: {prompt_context.filename}\n"
            f"Page: {page_number}\n\n"
            "No extracted OCR/text was available for this page. Extract reserve-study rows from the image only."
        )
    else:
        prompt_body = (
            f"Filename: {prompt_context.filename}\n"
            f"Page: {page_number}\n\n"
            f"PAGE TEXT:\n{page_text}"
        )

    user_content = [{"type": "text", "text": prompt_body}]
    if page_image is not None and page_image.content is not None:
        user_content.append(
            {"type": "image", "data": page_image.content, "mime_type": page_image.mime_type}
        )
    return await call_llm_vision(
        [
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        ExtractedReserveStudyPage,
        temperature=0.0,
        timeout=_RESERVE_VISION_TIMEOUT_SECONDS,
    )


def _apply_row_flags(row: ExtractedReserveStudyRow) -> ExtractedReserveStudyRow:
    flags = [
        flag
        for flag in dict.fromkeys(row.flags)
        if flag
        not in {
            "missing_useful_life",
            "missing_remaining_life",
            "missing_replacement_cost",
            "missing_year_replacement_provision",
            "missing_estimated_liability",
        }
    ]
    if row.useful_life is None:
        flags.append("missing_useful_life")
    if row.remaining_life is None:
        flags.append("missing_remaining_life")
    if row.replacement_cost is None:
        flags.append("missing_replacement_cost")
    if row.year_replacement_provision is None:
        flags.append("missing_year_replacement_provision")
    if row.estimated_liability is None:
        flags.append("missing_estimated_liability")
    return row.model_copy(update={"flags": flags})


def _normalize_reserve_line_item(line_item: str) -> str:
    return " ".join(line_item.casefold().split())


def _row_value_count(row: ExtractedReserveStudyRow) -> int:
    return sum(
        1
        for value in (
            row.useful_life,
            row.remaining_life,
            row.quantity,
            row.replacement_cost,
            row.year_new,
            row.reference_year,
            row.year_replacement_provision,
            row.estimated_liability,
        )
        if value is not None
    )


def _normalize_quantity_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = " ".join(value.casefold().split())
    return normalized or None


def _rows_are_merge_compatible(existing: ExtractedReserveStudyRow, candidate: ExtractedReserveStudyRow) -> bool:
    if _normalize_reserve_line_item(existing.line_item) != _normalize_reserve_line_item(candidate.line_item):
        return False

    int_fields = (
        "useful_life",
        "remaining_life",
        "year_new",
        "reference_year",
        "year_replacement_provision",
        "estimated_liability",
    )

    for field_name in int_fields:
        existing_value = getattr(existing, field_name)
        candidate_value = getattr(candidate, field_name)
        if existing_value is not None and candidate_value is not None and existing_value != candidate_value:
            return False

    existing_quantity = _normalize_quantity_value(existing.quantity)
    candidate_quantity = _normalize_quantity_value(candidate.quantity)
    if existing_quantity is not None and candidate_quantity is not None and existing_quantity != candidate_quantity:
        return False

    existing_cost = existing.replacement_cost
    candidate_cost = candidate.replacement_cost
    if (
        existing_cost is not None
        and candidate_cost is not None
        and not isclose(existing_cost, candidate_cost, rel_tol=0.0, abs_tol=0.01)
    ):
        return False

    return True


def _merge_reserve_rows(existing: ExtractedReserveStudyRow, candidate: ExtractedReserveStudyRow) -> ExtractedReserveStudyRow:
    existing_count = _row_value_count(existing)
    candidate_count = _row_value_count(candidate)
    existing_source_page = existing.source_page or 10**9
    candidate_source_page = candidate.source_page or 10**9

    if candidate_count > existing_count or (candidate_count == existing_count and candidate_source_page < existing_source_page):
        primary = candidate
        secondary = existing
    else:
        primary = existing
        secondary = candidate

    earliest_source_page = min(
        page_number
        for page_number in (existing.source_page, candidate.source_page)
        if page_number is not None
    ) if (existing.source_page is not None or candidate.source_page is not None) else None

    return primary.model_copy(
        update={
            "useful_life": primary.useful_life if primary.useful_life is not None else secondary.useful_life,
            "remaining_life": primary.remaining_life if primary.remaining_life is not None else secondary.remaining_life,
            "quantity": primary.quantity if primary.quantity is not None else secondary.quantity,
            "replacement_cost": primary.replacement_cost if primary.replacement_cost is not None else secondary.replacement_cost,
            "year_new": primary.year_new if primary.year_new is not None else secondary.year_new,
            "reference_year": primary.reference_year if primary.reference_year is not None else secondary.reference_year,
            "year_replacement_provision": (
                primary.year_replacement_provision
                if primary.year_replacement_provision is not None
                else secondary.year_replacement_provision
            ),
            "estimated_liability": (
                primary.estimated_liability
                if primary.estimated_liability is not None
                else secondary.estimated_liability
            ),
            "source_page": earliest_source_page,
            "flags": list(dict.fromkeys([*primary.flags, *secondary.flags])),
        }
    )


# Deterministic noise patterns from year-organized cash-flow / expenditure
# schedules. These are vendor-agnostic patterns observed across multiple
# reserve-study vendors (CIRMS, Association Reserves, SMA, Siena/Pinnacle,
# etc.). When the LLM mis-picks one of those sections, these rows leak in
# as fake components. We drop them by line_item before dedupe.
#
# This regex is a belt-and-suspenders fallback for known vendor fingerprints;
# the vendor-agnostic check is the "no lifecycle data" rule in
# _drop_annual_schedule_noise (no useful_life + no remaining_life + no
# replacement_cost = not a real component, regardless of label text).
_ANNUAL_SCHEDULE_NOISE_RE = re.compile(
    r"^\s*("
    # Year-banded section headers (any vendor).
    r"replacement\s+year\b"
    r"|total\s+for\b"
    r"|no\s+replacement\b"
    r"|expenditures?\s+(for|in)\s+\d{4}\b"
    r"|\d{4}\s+expenditures?\b"
    r"|year\s*[-:]?\s*\d{4}\b"
    # Column-header rows that landed in the item stream.
    r"|description\s+expenditures?\b"
    r"|grand\s+total\b"
    # Cash-flow / funding-plan section labels.
    r"|annual\s+expenditure"
    r"|cash\s+flow\s+(detail|summary|funding)"
    r"|(yearly|annual)\s+cash\s+flow"
    r"|year\s*-?\s*by\s*-?\s*year"
    r"|funding\s+(analysis|plan|summary)\s*$"
    # Misc. trailing markers.
    r"|continued\.{0,3}\s*$"
    r"|subtotal\b"
    r"|page\s+\d"
    r")",
    re.IGNORECASE,
)


def _drop_annual_schedule_noise(
    rows: list[ExtractedReserveStudyRow],
) -> tuple[list[ExtractedReserveStudyRow], int]:
    """Drop rows that cannot be real reserve components.

    H11 (CLAUDE.md rule 1 — meaning over appearance): a row is a REAL
    component only if it carries genuine lifecycle data — ``useful_life`` or
    ``remaining_life``. A ``replacement_cost`` alone does NOT make it a
    component: yearly schedule totals ("Total for 2031") also carry a cost.
    So the label-pattern regex may only drop a row that is NOT a real
    component; a genuine component is NEVER dropped on label text alone (e.g.
    a real component whose name happens to contain "subtotal"), and such a
    row is kept with a ``suspicious_label`` flag for operator review.

    Drop order per row:
      1. No data at all (no useful_life, remaining_life, or replacement_cost)
         → not renderable, drop.
      2. Not a real component (only a cost / no lifecycle) AND a noise label
         → schedule total, drop.
      3. Real component with a noise label → keep + flag ``suspicious_label``.

    Header rows (``row_type == 'header'``) are preserved — they're rendered as
    visual breaks and don't claim to be components.
    """
    kept: list[ExtractedReserveStudyRow] = []
    dropped = 0
    for row in rows:
        if row.row_type == "header":
            kept.append(row)
            continue
        has_any_data = not (
            row.useful_life is None
            and row.remaining_life is None
            and row.replacement_cost is None
        )
        is_real_component = (
            row.useful_life is not None or row.remaining_life is not None
        )
        label = (row.line_item or "").strip()
        label_is_noise = bool(label and _ANNUAL_SCHEDULE_NOISE_RE.match(label))

        if not has_any_data:
            dropped += 1
            continue

        if label_is_noise and not is_real_component:
            # A cost with no lifecycle + a noise label = a schedule total.
            dropped += 1
            continue

        if label_is_noise:
            # Genuine component with a suspicious label — never drop real data
            # on appearance; keep and flag it for operator review.
            flags = list(row.flags)
            if "suspicious_label" not in flags:
                flags.append("suspicious_label")
            kept.append(row.model_copy(update={"flags": flags}))
            continue

        kept.append(row)
    return kept, dropped


def _dedupe_reserve_rows(rows: list[ExtractedReserveStudyRow]) -> tuple[list[ExtractedReserveStudyRow], int]:
    deduped: list[ExtractedReserveStudyRow] = []
    duplicates_merged = 0

    for row in rows:
        if row.row_type == "header":
            deduped.append(row)
            continue

        merged = False
        for index, existing in enumerate(deduped):
            if existing.row_type == "header":
                continue
            if not _rows_are_merge_compatible(existing, row):
                continue
            deduped[index] = _merge_reserve_rows(existing, row)
            duplicates_merged += 1
            merged = True
            break
        if not merged:
            deduped.append(row)

    return deduped, duplicates_merged


def _extract_text_from_pdf_page(page: pdfplumber.page.Page) -> str:
    words = page.extract_words(x_tolerance=3, y_tolerance=3) or []

    lines_by_y: dict[int, list[dict[str, object]]] = {}
    for word in words:
        y_key = round(float(word["top"]) / 2) * 2
        lines_by_y.setdefault(y_key, []).append(word)

    page_lines: list[str] = []
    for y_key in sorted(lines_by_y.keys()):
        line_words = sorted(lines_by_y[y_key], key=lambda item: float(item["x0"]))
        if len(line_words) == 1 and len(str(line_words[0]["text"]).strip()) <= 1:
            continue

        parts: list[str] = []
        prev_x1 = 0.0
        for word in line_words:
            x0 = float(word["x0"])
            gap = x0 - prev_x1
            if gap > 30 and parts:
                parts.append("\t")
            elif gap > 1.5 and parts:
                parts.append(" ")
            parts.append(str(word["text"]))
            prev_x1 = float(word["x1"])

        line_text = "".join(parts).strip()
        if line_text:
            page_lines.append(line_text)

    return "\n".join(page_lines)


def _extract_reserve_study_page_texts_for_pages(
    path: str,
    page_numbers: list[int],
) -> dict[int, str]:
    requested_pages = sorted(set(page_numbers))
    if not requested_pages:
        return {}

    if not Path(path).exists():
        try:
            page_texts = _split_pages(_extract_pdf_text_table(path, max_pages=max(requested_pages)))
        except ValueError as exc:
            if not _is_scanned_pdf_error(exc):
                raise
            return {page_number: "" for page_number in requested_pages}
        return {
            page_number: page_texts[page_number - 1] if page_number - 1 < len(page_texts) else ""
            for page_number in requested_pages
        }

    page_texts: dict[int, str] = {}
    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        for page_number in requested_pages:
            if page_number < 1 or page_number > total_pages:
                page_texts[page_number] = ""
                continue
            page_texts[page_number] = _extract_text_from_pdf_page(pdf.pages[page_number - 1])
    return page_texts


def _render_reserve_study_page_subset(
    path: str,
    page_numbers: list[int],
    *,
    dpi: int,
) -> dict[int, RenderedPage]:
    if not page_numbers or not Path(path).exists():
        return {}
    try:
        import fitz  # type: ignore
    except ImportError:
        logger.warning("PyMuPDF unavailable; skipping reserve-study high-DPI rerender for pages=%s", page_numbers)
        return {}

    requested_pages = sorted(set(page_numbers))
    rendered_pages: dict[int, RenderedPage] = {}
    document = fitz.open(path)
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale) if scale != 1.0 else None
    try:
        for page_number in requested_pages:
            if page_number < 1 or page_number > len(document):
                continue
            page = document[page_number - 1]
            pixmap = page.get_pixmap(matrix=matrix) if matrix is not None else page.get_pixmap()
            rendered_pages[page_number] = RenderedPage(
                page_number=page_number,
                mime_type="image/png",
                content=pixmap.tobytes("png"),
            )
    finally:
        document.close()
    return rendered_pages


def _prepared_document(
    path: str,
    *,
    max_pages: Optional[int],
) -> _PreparedReserveStudyDocument:
    prompt_context = DocumentPromptContext(
        filename=Path(path).name,
        route_family="pdf_visual_document",
    )
    if max_pages is not None:
        page_limit = max_pages
    elif Path(path).exists():
        page_limit = _get_pdf_page_count(path)
    else:
        # Test helpers often monkeypatch text/render extraction against a synthetic
        # path that does not exist on disk. In that case, keep the old "effectively
        # uncapped" behavior without forcing tests to create a real PDF.
        page_limit = 1000

    extraction_metadata: dict[str, object] = {
        "discovery_mode": "image_first",
        "discovery_batch_size": _DISCOVERY_BATCH_SIZE,
    }
    render_max_pages = page_limit if Path(path).exists() else max_pages
    rendered_pages = render_pdf_pages(
        path,
        max_pages=render_max_pages,
        dpi=_DISCOVERY_RENDER_DPI,
    )
    pages = [
        _PreparedReserveStudyPage(
            page_number=rendered_page.page_number,
            page_image=rendered_page,
        )
        for rendered_page in rendered_pages
    ]
    extraction_metadata["rendered_page_count"] = len(rendered_pages)
    logger.info(
        "reserve-study prepared: file=%s rendered_pages=%d discovery_mode=%s batch_size=%d",
        prompt_context.filename,
        extraction_metadata["rendered_page_count"],
        extraction_metadata["discovery_mode"],
        extraction_metadata["discovery_batch_size"],
    )
    return _PreparedReserveStudyDocument(
        prompt_context=prompt_context,
        pages=pages,
        extraction_metadata=extraction_metadata,
    )


async def _discover_from_prepared(
    prepared: _PreparedReserveStudyDocument,
) -> ReserveStudyDiscoveryResult:
    candidate_pages = [page.page_number for page in prepared.pages]
    pages_by_number = {page.page_number: page for page in prepared.pages}
    candidate_chunks = [
        candidate_pages[index:index + _DISCOVERY_BATCH_SIZE]
        for index in range(0, len(candidate_pages), _DISCOVERY_BATCH_SIZE)
    ]
    classification_batches = await asyncio.gather(
        *[
            _classify_page_batch(
                pages=[pages_by_number[page_number] for page_number in chunk],
                prompt_context=prepared.prompt_context,
            )
            for chunk in candidate_chunks
        ]
    )
    classifications = [
        item
        for batch in classification_batches
        for item in batch
    ]
    classifications = _promote_context_pages_between_tables(classifications)
    classifications, sequences, winning_sequence = _retain_best_sequence(classifications)
    spans = _merge_page_spans(classifications)
    winning_pages = [item.page_number for item in winning_sequence.items] if winning_sequence is not None else []
    logger.info(
        "reserve-study discovery: file=%s candidate_pages=%d classified_pages=%d batches=%d sequences=%s winning_pages=%s spans=%s",
        prepared.prompt_context.filename,
        len(candidate_pages),
        len(classifications),
        len(candidate_chunks),
        [(sequence.start_page, sequence.end_page, _sequence_score(sequence)) for sequence in sequences],
        winning_pages,
        [(span.start_page, span.end_page) for span in spans],
    )
    confidence = (
        sum(item.confidence for item in spans) / len(spans)
        if spans else 0.0
    )
    return ReserveStudyDiscoveryResult(
        classifications=classifications,
        page_spans=spans,
        confidence=confidence,
        extraction_metadata={
            **prepared.extraction_metadata,
            "classified_page_count": len(classifications),
            "discovery_batch_count": len(candidate_chunks),
            "winning_sequence_pages": winning_pages,
        },
    )


async def discover_reserve_study_pages(
    path: str,
    *,
    max_pages: Optional[int] = None,
) -> ReserveStudyDiscoveryResult:
    prepared = _prepared_document(path, max_pages=max_pages)
    return await _discover_from_prepared(prepared)


async def extract_reserve_study(
    path: str,
    *,
    max_pages: Optional[int] = None,
) -> ExtractedReserveStudyDocument | DocumentExtractionFailure:
    prepared = _prepared_document(path, max_pages=max_pages)
    discovery = await _discover_from_prepared(prepared)
    if not discovery.page_spans:
        return DocumentExtractionFailure(
            code="reserve_pages_not_found",
            message="We could not find the reserve-study tables in this PDF.",
            details={"classifications": [item.model_dump() for item in discovery.classifications]},
        )

    selected_page_numbers = sorted(
        {
            classification.page_number
            for classification in discovery.classifications
            if classification.role == ReserveStudyPageRole.RESERVE_TABLE
        }
    )
    selected_page_texts = _extract_reserve_study_page_texts_for_pages(path, selected_page_numbers)
    ocr_text_pages = [
        page_number
        for page_number in selected_page_numbers
        if selected_page_texts.get(page_number, "").strip()
    ]
    ocr_fallback_pages = [
        page_number
        for page_number in selected_page_numbers
        if not selected_page_texts.get(page_number, "").strip()
    ]
    high_dpi_fallback_images = _render_reserve_study_page_subset(
        path,
        ocr_fallback_pages,
        dpi=_VISION_ONLY_EXTRACTION_DPI,
    )
    logger.info(
        "reserve-study extraction OCR: file=%s selected_pages=%s ocr_text_pages=%s ocr_fallback_pages=%s",
        prepared.prompt_context.filename,
        selected_page_numbers,
        ocr_text_pages,
        ocr_fallback_pages,
    )
    pages_by_number = {page.page_number: page for page in prepared.pages}
    extracted_pages = await asyncio.gather(
        *[
            _extract_reserve_page(
                page_number=page_number,
                page_text=selected_page_texts.get(page_number, ""),
                page_image=high_dpi_fallback_images.get(page_number, pages_by_number[page_number].page_image),
                prompt_context=prepared.prompt_context,
                text_is_empty=not selected_page_texts.get(page_number, "").strip(),
            )
            for page_number in selected_page_numbers
        ]
    )

    rows: list[ExtractedReserveStudyRow] = []
    warnings: list[str] = []
    study_date = None
    study_year = None
    applicable_fiscal_year = None
    reference_year = None
    first_forecast_year = None
    confidences: list[float] = []

    for page_number, extracted_page in zip(selected_page_numbers, extracted_pages):
        if extracted_page is None:
            warnings.append(f"Page {page_number} could not be extracted and needs review.")
            continue
        if study_date is None and extracted_page.study_date:
            study_date = extracted_page.study_date
        if study_year is None and extracted_page.study_year is not None:
            study_year = extracted_page.study_year
        if applicable_fiscal_year is None and extracted_page.applicable_fiscal_year is not None:
            applicable_fiscal_year = extracted_page.applicable_fiscal_year
        if reference_year is None and extracted_page.reference_year is not None:
            reference_year = extracted_page.reference_year
        if first_forecast_year is None and extracted_page.first_forecast_year is not None:
            first_forecast_year = extracted_page.first_forecast_year
        warnings.extend(extracted_page.warnings)
        confidences.append(extracted_page.confidence)
        for row_index, row in enumerate(extracted_page.rows, start=1):
            normalized = row.model_copy(
                update={
                    "row_id": row.row_id or f"reserve-{page_number}-{row_index}",
                    "source_page": row.source_page or page_number,
                }
            )
            rows.append(normalized)

    if not rows:
        return DocumentExtractionFailure(
            code="reserve_rows_not_found",
            message="Reserve-study pages were found, but no component rows could be extracted.",
            details={"page_spans": [item.model_dump() for item in discovery.page_spans]},
        )

    rows, noise_dropped = _drop_annual_schedule_noise(rows)
    rows, duplicates_merged = _dedupe_reserve_rows(rows)
    rows, reference_year = canonicalize_reserve_study_rows(
        rows,
        explicit_reference_year=reference_year,
        first_forecast_year=first_forecast_year,
        applicable_fiscal_year=applicable_fiscal_year,
        study_year=study_year,
    )
    unique_warnings = list(dict.fromkeys(warnings))
    if noise_dropped:
        unique_warnings.append(
            f"Dropped {noise_dropped} non-component row(s) (annual-schedule headers, year totals, "
            f"or rows with no usable lifecycle data)."
        )
    if duplicates_merged:
        unique_warnings.append(
            f"Merged {duplicates_merged} duplicate reserve-study row(s) detected across extracted pages."
        )
    confidence_inputs = [discovery.confidence, *confidences]
    confidence = sum(confidence_inputs) / len(confidence_inputs) if confidence_inputs else 0.0

    return ExtractedReserveStudyDocument(
        study_date=study_date,
        study_year=study_year,
        applicable_fiscal_year=applicable_fiscal_year,
        reference_year=reference_year,
        classifications=discovery.classifications,
        page_spans=discovery.page_spans,
        rows=rows,
        warnings=unique_warnings,
        confidence=confidence,
        extraction_metadata={
            **discovery.extraction_metadata,
            "ocr_attempted_pages": selected_page_numbers,
            "ocr_text_pages": ocr_text_pages,
            "ocr_fallback_pages": ocr_fallback_pages,
            "duplicates_merged": duplicates_merged,
        },
    )
