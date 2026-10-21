"""LLM-first PDF extraction with schema enforcement and deterministic validation.

Uses pdfplumber text extraction + Gemini (hybrid text + image mode) for single-call extraction.
"""
from __future__ import annotations

import asyncio
import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Any

from pydantic import BaseModel, Field, ValidationError

from ..config import settings
from ..models.financial_document_extraction import (
    INCOME_ROLE_RULES,
    DisclosureFacts,
    DocumentExtractionFailure,
    ExtractedFinancialStatement,
    ExtractedFinancialStatementPage,
    StatedTotal,
    WireModel,
)
from ..ai_implementation.pipeline.document_extraction_provider import (
    DocumentPromptContext,
    RenderedPage,
)
from .financial_statement_validation import (
    derive_statement_confidence,
    has_blocking_validation_issues,
    totals_for_validation,
    validate_extracted_statement,
)

logger = logging.getLogger(__name__)


# PyMuPDF renders at 72 DPI by default. That's fine as a visual reference
# when we also ship the pdfplumber text (hybrid path), but the scanned-PDF
# fallback has no text to ship and must rely on the image alone. 72 DPI is
# too low for Gemini to read fine-print numeric columns (confirmed on
# Cummins Park: labels extracted correctly, every numeric field returned
# as null). Rendering at 200 DPI is the sweet spot — good enough for dense
# financial tables, ~7.7x the byte size of 72 DPI per page (still well
# under Gemini's per-image limits for a 6-page statement).
_HYBRID_RENDER_DPI = 72
_SCANNED_FALLBACK_RENDER_DPI = 200
_PROFORMA_SOURCE_MODE = "proforma_final_budget"
_INCOME_STATEMENT_SOURCE_MODE = "income_statement"
# Per Gemini call: busy answers (429/5xx) are retried after 4, 8 and 16 s, a
# timeout only once, so classification + pages stay under the 600 s proxy limit.
_BUDGET_VISION_TIMEOUT_SECONDS = 120.0
_GEMINI_RETRY_DELAYS = [4.0, 8.0, 16.0]
_GEMINI_TIMEOUT_RETRIES = 1
_CLASSIFY_BATCH_PAGES = 40
_MAX_PDF_PAGES = 300
_MAX_STATEMENT_PAGES = 8
_MAX_FACT_PAGES = 10


PageType = Literal[
    "income_statement", "reserve_income_statement", "operating_budget", "budget_summary",
    "balance_sheet", "reserve_study", "assessment_schedule", "letter_or_notes", "other",
]


class PageClassification(WireModel):
    page: int
    page_type: PageType
    page_heading: Optional[str] = Field(description="The page's main heading as printed.")
    year_or_period: Optional[str] = Field(description="The fiscal year or period printed on the page.")
    has_detail_rows: Optional[bool] = Field(description="True when the page lists individual line items with amounts.")
    has_disclosure_facts: Optional[bool] = Field(
        description=(
            "True when the page states any of: assessment per unit per month, reserve fund balance, "
            "percent funded, reserve study preparer or date, the accountants, interest or inflation "
            "assumptions, planned assessment increases, loans."
        ),
    )


class PageClassificationBatch(WireModel):
    pages: list[PageClassification]


class StatementPageWithTotals(ExtractedFinancialStatementPage):
    """Per-page extraction that also returns the page's printed totals."""

    totals: list[StatedTotal] = Field(default_factory=list)


class NotABudgetSource(RuntimeError):
    def __init__(self, pages: list[PageClassification]):
        super().__init__("No income statement or budget pages were found in this PDF.")
        self.pages = pages


_PAGE_CLASSIFICATION_SYSTEM_PROMPT = """You classify every page image of one PDF that an HOA manager uploaded as the starting point for a budget. Return one entry per page shown, using the page number printed before each image.

page_type:
- income_statement: actual results for a past period with detail rows (Statement of Revenues and Expenses, Income and Expense to Budget, Budget Comparison, Income Statement): actual, current month or year-to-date columns, usually beside budget or annual budget columns.
- reserve_income_statement: the reserve fund's income/expense statement in that same kind of actual-vs-budget report.
- operating_budget: a budget for a fiscal year with detail rows and no actual results: pro forma operating budget, operating and cash flow statement (often Jan-Dec monthly columns), proposed/approved/final budget, forecasted statement of revenues and expenses.
- budget_summary: a page restating a budget in a few grouped lines (disclosure summaries, assessment and reserve funding disclosure summary, charts).
- balance_sheet; reserve_study (component inventories, funding plans, reserve cash-flow projections); assessment_schedule (dues per unit); letter_or_notes (cover letters, notices, policies, notes, compilation reports, contents pages); other.

page_heading: the page's main heading as printed. year_or_period: the fiscal year or period printed on it. has_detail_rows: true when the page lists individual line items with amounts. has_disclosure_facts: see the schema."""

_FACTS_SYSTEM_PROMPT = """You read pages from an HOA annual budget / disclosure package and return the facts in the schema, only when the pages state them. Never compute or guess: if a fact is not printed, return null for it. Each fact gives the value as printed, its number, the fiscal year it applies to, the as-of date for balances (YYYY-MM-DD, also when it is only named in the label, e.g. 'as of 12/31/26' -> 2026-12-31), the PDF page, and a short quote. Standard disclosure questions that only mention the possibility of a special assessment are not facts."""


@dataclass
class PageChoice:
    """Which pages to extract and how to read them."""

    source_mode: str
    pages: list[int]
    reserve_pages: set[int]
    note: str
    fact_pages: list[int]


def _page_ranges(pages: list[int]) -> str:
    """[3, 4, 5, 9] -> "3–5, 9"."""
    runs: list[list[int]] = []
    for page in sorted(set(pages)):
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)


def _choose_pages(
    pages: list[PageClassification],
    kind_override: Optional[str] = None,
) -> Optional[PageChoice]:
    """Pick the statement or budget block from the classified pages.

    A detailed operating budget wins over income statements (a disclosure
    package may carry both); with no budget or statement pages the file is
    not a budget source. ``kind_override`` (the operator's choice) picks the
    kind; its pages are used when Gemini found any, otherwise the other kind's.
    """
    budget = [p.page for p in pages if p.page_type == "operating_budget" and p.has_detail_rows]
    statement = [
        p.page for p in pages
        if p.page_type in ("income_statement", "reserve_income_statement") and p.has_detail_rows
    ]
    if kind_override == _PROFORMA_SOURCE_MODE:
        source_mode, pool = kind_override, budget or statement
    elif kind_override == _INCOME_STATEMENT_SOURCE_MODE:
        source_mode, pool = kind_override, statement or budget
    elif budget:
        source_mode, pool = _PROFORMA_SOURCE_MODE, budget
    else:
        source_mode, pool = _INCOME_STATEMENT_SOURCE_MODE, statement
    if not pool:
        return None

    if source_mode == _PROFORMA_SOURCE_MODE:
        # One contiguous block: two budget presentations (e.g. the manager's
        # monthly grid and the CPA's forecast) must not be mixed.
        chosen = [pool[0]]
        for page in pool[1:]:
            if page != chosen[-1] + 1:
                break
            chosen.append(page)
    else:
        chosen = list(pool)
    chosen = chosen[:_MAX_STATEMENT_PAGES]

    by_page = {p.page: p for p in pages}
    first = by_page[chosen[0]]
    kind_label = "a final budget" if source_mode == _PROFORMA_SOURCE_MODE else "an income statement"
    note = (
        f"Read as {kind_label} from {'page' if len(chosen) == 1 else 'pages'} "
        f"{_page_ranges(chosen)} of {len(pages)}"
    )
    if first.page_heading:
        note += f" ({' '.join(first.page_heading.split())[:90]})"
    note += "."
    others = [page for page in (budget + statement) if page not in chosen]
    if others:
        note += f" Also found statement or budget pages at {_page_ranges(others)}, not used."
    return PageChoice(
        source_mode=source_mode,
        pages=chosen,
        reserve_pages={p for p in chosen if by_page[p].page_type == "reserve_income_statement"},
        note=note,
        fact_pages=[p.page for p in pages if p.has_disclosure_facts][:_MAX_FACT_PAGES],
    )


def _is_proforma_mode(prompt_context: DocumentPromptContext) -> bool:
    return (prompt_context.source_mode or "").strip().lower() == "proforma_final_budget"


def _operating_prompt(prompt_context: DocumentPromptContext, is_reserve_page: bool) -> str:
    if is_reserve_page:
        return _PAGE_SYSTEM_PROMPT_RESERVE
    return (
        _PAGE_SYSTEM_PROMPT_PROFORMA_OPERATING
        if _is_proforma_mode(prompt_context)
        else _PAGE_SYSTEM_PROMPT_OPERATING
    )


def render_pdf_pages(
    path: str,
    max_pages: Optional[int] = None,
    *,
    dpi: int = _HYBRID_RENDER_DPI,
) -> list[RenderedPage]:
    """Render PDF pages to PNG bytes for the hybrid ingestion path.

    Kept for backward compatibility with tests and the Any
    protocol. The default ``dpi=72`` preserves existing behaviour for the
    hybrid text+image path; the scanned-PDF fallback in ``_extract_full_document``
    passes ``dpi=_SCANNED_FALLBACK_RENDER_DPI`` (200) so Gemini can actually
    read numeric columns from the image alone.
    """
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF rendering requires PyMuPDF (fitz). Install pymupdf to enable the VLM PDF path."
        ) from exc

    document = fitz.open(path)
    page_limit = max_pages or settings.DOCUMENT_VLM_MAX_PAGES
    rendered_pages: list[RenderedPage] = []
    # PyMuPDF's native page coordinate space is 72 DPI — any other target DPI
    # is applied via a uniform scale matrix (zoom = target / 72).
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale) if scale != 1.0 else None
    try:
        for page_number, page in enumerate(document, start=1):
            if page_number > page_limit:
                break
            pixmap = page.get_pixmap(matrix=matrix) if matrix is not None else page.get_pixmap()
            rendered_pages.append(
                RenderedPage(
                    page_number=page_number,
                    mime_type="image/png",
                    content=pixmap.tobytes("png"),
                )
            )
    finally:
        document.close()

    return rendered_pages


def _get_pdf_page_count(pdf_path: str) -> int:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF inspection requires PyMuPDF (fitz). Install pymupdf to enable the VLM PDF path."
        ) from exc

    document = fitz.open(pdf_path)
    try:
        return int(document.page_count)
    finally:
        document.close()


def render_pdf_pages_for_numbers(
    path: str,
    page_numbers: list[int],
    *,
    dpi: int = _HYBRID_RENDER_DPI,
) -> list[RenderedPage]:
    """Render explicit 1-based PDF pages and preserve original page numbers."""
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF rendering requires PyMuPDF (fitz). Install pymupdf to enable the VLM PDF path."
        ) from exc

    document = fitz.open(path)
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale) if scale != 1.0 else None
    rendered_pages: list[RenderedPage] = []
    try:
        for page_number in page_numbers:
            if page_number < 1 or page_number > document.page_count:
                continue
            page = document[page_number - 1]
            pixmap = page.get_pixmap(matrix=matrix) if matrix is not None else page.get_pixmap()
            rendered_pages.append(
                RenderedPage(
                    page_number=page_number,
                    mime_type="image/png",
                    content=pixmap.tobytes("png"),
                )
            )
    finally:
        document.close()
    return rendered_pages


def _parse_currency_text(value: str) -> Optional[float]:
    text = value.strip().replace(",", "").replace("$", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if text.endswith(")"):
        text = text[:-1]
    try:
        parsed = float(text)
    except ValueError:
        return None
    return -parsed if negative else parsed


def _log_schema_validation_failure_sample(
    raw_result: Any,
    *,
    path: str,
    source_mode: str,
    attempt: int,
) -> None:
    line_items = getattr(raw_result, "line_items", None)
    if not isinstance(line_items, list):
        return

    sample_labels = [
        getattr(item, "label", None)
        for item in line_items[:5]
        if getattr(item, "label", None)
    ]
    logger.info(
        "PDF schema validation failed: file=%s source_mode=%s attempt=%d rows=%d sample_labels=%s",
        Path(path).name,
        source_mode,
        attempt,
        len(line_items),
        sample_labels,
    )


def _normalize_visible_row_label(raw_label: str) -> str:
    label = " ".join(str(raw_label or "").replace("\t", " ").split())
    if " " not in label:
        label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", label)
    return label.rstrip(":").strip()


def _extract_visible_row_hints(
    path: str,
    *,
    exclude_labels: list[str] | None = None,
    limit: int = 6,
) -> list[str]:
    try:
        pdf_text = _extract_pdf_text_table(path, max_pages=settings.DOCUMENT_VLM_MAX_PAGES)
    except Exception:
        return []

    excluded = {
        _normalize_visible_row_label(label).lower()
        for label in (exclude_labels or [])
        if _normalize_visible_row_label(label)
    }
    skip_prefixes = (
        "total",
        "subtotal",
        "net ",
        "cumulative",
        "income",
        "expense",
        "operating and cash flow statement",
        "one time special assessment",
        "no increase in dues",
        "old mill homeowners association",
    )

    hints: list[str] = []
    seen: set[str] = set()
    for raw_line in pdf_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--- Page "):
            continue
        if "$" not in line:
            continue

        label_source = line.split("\t", 1)[0]
        label_source = label_source.split("$", 1)[0]
        label = _normalize_visible_row_label(label_source)
        if not label:
            continue

        label_lower = label.lower()
        if label_lower in excluded or label_lower in seen:
            continue
        if any(label_lower.startswith(prefix) for prefix in skip_prefixes):
            continue

        seen.add(label_lower)
        hints.append(label)
        if len(hints) >= limit:
            break

    return hints


def _build_schema_retry_note(
    raw_result: Any,
    exc: ValidationError,
    *,
    path: str | None = None,
    source_mode: str | None = None,
) -> str:
    visible_hints: list[str] = []
    if path and (source_mode or "").strip().lower() == _PROFORMA_SOURCE_MODE:
        visible_hints = _extract_visible_row_hints(path, limit=6)
    hint_text = ""
    if visible_hints:
        hint_text = (
            " Visible labeled money rows include: "
            + ", ".join(visible_hints)
            + "."
        )

    if isinstance(raw_result, dict):
        line_items = raw_result.get("line_items")
    else:
        line_items = getattr(raw_result, "line_items", None)
    if isinstance(line_items, list) and len(line_items) == 1:
        first_item = line_items[0]
        if isinstance(first_item, dict):
            first_label = first_item.get("label") or "unknown"
        else:
            first_label = getattr(first_item, "label", None) or "unknown"
        if path and (source_mode or "").strip().lower() == _PROFORMA_SOURCE_MODE:
            visible_hints = _extract_visible_row_hints(
                path,
                exclude_labels=[first_label],
                limit=6,
            )
            if visible_hints:
                hint_text = (
                    " Visible labeled money rows include: "
                    + ", ".join(visible_hints)
                    + "."
                )
        return (
            "Previous attempt returned only 1 line item "
            f"({first_label}). This page has more visible labeled money rows. "
            f"{hint_text}"
            "Re-read the full page, scan both income and expense sections, and return every visible detail row. "
            "section_kind must use only income, operating, reserve_income, reserve_expense, or null."
        )
    return (
        f"Previous attempt failed schema validation: {exc}"
        f"{hint_text}"
        " Re-read the full page and return every visible detail row."
    )


async def _classify_pages(pdf_path: str, filename: str) -> list[PageClassification]:
    """Classify every page in batches run at the same time (72 DPI images)."""
    from ..ai_implementation.pipeline.llm_client import call_llm_vision

    page_count = _get_pdf_page_count(pdf_path)

    async def classify(first: int, last: int) -> list[PageClassification]:
        rendered = await asyncio.to_thread(
            render_pdf_pages_for_numbers, pdf_path, list(range(first, last + 1)), dpi=_HYBRID_RENDER_DPI
        )
        parts: list[dict] = [
            {"type": "text", "text": f"Filename: {filename}\nPages {first}-{last} of {page_count}."}
        ]
        for page in rendered:
            parts.append({"type": "text", "text": f"PDF page {page.page_number}"})
            if page.content is not None:
                parts.append({"type": "image", "data": page.content, "mime_type": page.mime_type})
        result = await call_llm_vision(
            [
                {"role": "system", "content": _PAGE_CLASSIFICATION_SYSTEM_PROMPT},
                {"role": "user", "content": parts},
            ],
            PageClassificationBatch,
            temperature=0.0,
            timeout=_BUDGET_VISION_TIMEOUT_SECONDS,
            thinking_budget=0,
            backoff_delays=_GEMINI_RETRY_DELAYS,
            timeout_retries=_GEMINI_TIMEOUT_RETRIES,
        )
        if result is None:
            raise RuntimeError(f"Gemini could not classify pages {first}-{last}.")
        return [page for page in result.pages if first <= page.page <= last]

    batches = await asyncio.gather(
        *(
            classify(start, min(start + _CLASSIFY_BATCH_PAGES - 1, page_count))
            for start in range(1, page_count + 1, _CLASSIFY_BATCH_PAGES)
        )
    )
    return sorted((page for batch in batches for page in batch), key=lambda page: page.page)


async def _extract_disclosure_facts(
    pdf_path: str,
    pages: list[int],
    filename: str,
) -> Optional[DisclosureFacts]:
    """Read disclosure facts from the pages the classifier flagged."""
    from ..ai_implementation.pipeline.llm_client import call_llm_vision

    def build_parts() -> list[dict]:
        import fitz  # type: ignore

        document = fitz.open(pdf_path)
        try:
            parts: list[dict] = [{"type": "text", "text": f"Filename: {filename}"}]
            for number in pages:
                page = document[number - 1]
                text = page.get_text().strip()
                has_text = len(text) > 30
                parts.append({
                    "type": "text",
                    "text": f"PDF page {number}" + (f"\nTEXT:\n{text[:6000]}" if has_text else " (scanned image)"),
                })
                scale = (110 if has_text else 160) / 72.0
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                parts.append({"type": "image", "data": pixmap.tobytes("png"), "mime_type": "image/png"})
            return parts
        finally:
            document.close()

    parts = await asyncio.to_thread(build_parts)
    return await call_llm_vision(
        [
            {"role": "system", "content": _FACTS_SYSTEM_PROMPT},
            {"role": "user", "content": parts},
        ],
        DisclosureFacts,
        temperature=0.0,
        timeout=_BUDGET_VISION_TIMEOUT_SECONDS,
        thinking_budget=0,
        backoff_delays=_GEMINI_RETRY_DELAYS,
        timeout_retries=_GEMINI_TIMEOUT_RETRIES,
    )


def _extract_pdf_text_table(path: str, max_pages: int = 6) -> str:
    """Extract PDF content as a structured text table using pdfplumber.

    Returns a formatted string with one line per visual row, preserving
    column alignment via tab separation. This gives the LLM exact numeric
    values (no OCR errors) while preserving spatial structure.
    """
    import pdfplumber

    all_text_parts: list[str] = []

    with pdfplumber.open(path) as pdf:
        for pg_num, page in enumerate(pdf.pages[:max_pages], start=1):
            words = page.extract_words(x_tolerance=3, y_tolerance=3)

            if pg_num == 1 and len(words) < 10:
                raise ValueError(
                    "PDF has no text layer (scanned). Upload text-based PDF or Excel."
                )

            # Group words into visual lines by y-coordinate
            lines_by_y: dict[int, list] = {}
            for w in words:
                y_key = round(w["top"] / 2) * 2
                lines_by_y.setdefault(y_key, []).append(w)

            page_lines: list[str] = []
            for y_key in sorted(lines_by_y.keys()):
                line_words = sorted(lines_by_y[y_key], key=lambda w: w["x0"])
                # Skip single-character artifacts (watermarks)
                if len(line_words) == 1 and len(line_words[0]["text"].strip()) <= 1:
                    continue

                # Build tab-separated columns based on x0 gaps
                parts: list[str] = []
                prev_x1 = 0.0
                for w in line_words:
                    gap = w["x0"] - prev_x1
                    if gap > 30 and parts:
                        parts.append("\t")
                    elif gap > 1.5 and parts:
                        parts.append(" ")
                    parts.append(w["text"])
                    prev_x1 = w["x1"]

                line_text = "".join(parts).strip()
                if line_text:
                    page_lines.append(line_text)

            if page_lines:
                all_text_parts.append(f"--- Page {pg_num} ---")
                all_text_parts.extend(page_lines)

    return "\n".join(all_text_parts)


def _extract_pdf_text_table_for_pages(path: str, selected_pages: list[int]) -> str:
    import pdfplumber

    wanted = {int(page) for page in selected_pages if int(page) > 0}
    all_text_parts: list[str] = []
    total_words = 0

    with pdfplumber.open(path) as pdf:
        for pg_num, page in enumerate(pdf.pages, start=1):
            if pg_num not in wanted:
                continue
            words = page.extract_words(x_tolerance=3, y_tolerance=3)
            total_words += len(words)

            lines_by_y: dict[int, list] = {}
            for w in words:
                y_key = round(w["top"] / 2) * 2
                lines_by_y.setdefault(y_key, []).append(w)

            page_lines: list[str] = []
            for y_key in sorted(lines_by_y.keys()):
                line_words = sorted(lines_by_y[y_key], key=lambda w: w["x0"])
                if len(line_words) == 1 and len(line_words[0]["text"].strip()) <= 1:
                    continue

                parts: list[str] = []
                prev_x1 = 0.0
                for w in line_words:
                    gap = w["x0"] - prev_x1
                    if gap > 30 and parts:
                        parts.append("\t")
                    elif gap > 1.5 and parts:
                        parts.append(" ")
                    parts.append(w["text"])
                    prev_x1 = w["x1"]

                line_text = "".join(parts).strip()
                if line_text:
                    page_lines.append(line_text)

            if page_lines:
                all_text_parts.append(f"--- Page {pg_num} ---")
                all_text_parts.extend(page_lines)

    if total_words < 10:
        raise ValueError(
            "PDF has no text layer (scanned). Upload text-based PDF or Excel."
        )

    return "\n".join(all_text_parts)


_TOTALS_INSTRUCTION = (
    "\n\nTOTALS: put every printed total row on this page (Total Income, Total Operating "
    "Expenses, Total Reserve Expenses, Net Income, ...) in totals, never in line_items. For each: "
    "label; section_kind = the section whose detail rows it adds up (income, operating, "
    "reserve_income or reserve_expense; null when it spans sections, such as net income or total "
    "expenses including reserves); source_row = this page number; annual_budget and ytd_actual as printed."
)

_PAGE_SYSTEM_PROMPT_OPERATING = (
    "You are a financial document parser. You receive the text content of ONE PAGE "
    "of an HOA income statement extracted from a PDF, plus an image of that page. "
    "The text preserves the original column layout using tabs and spaces.\n\n"
    "Your job is to extract EVERY detail line item from THIS PAGE into structured JSON.\n\n"
    "RULES:\n"
    "- Extract ALL detail rows from this page. Do NOT skip any. Do NOT summarize.\n"
    "- CRITICAL: SKIP any row whose label starts with 'Total' or 'TOTAL' or 'Subtotal' — these are\n"
    "  section subtotals and grand totals, NEVER extract them as line items. Examples to skip:\n"
    "  'Total Operating Income', 'Total Operating Expenses', 'TOTAL EXPENSES: UTILITIES',\n"
    "  'Total Reserve Contributions', 'Total Reserve Funding', 'TOTAL INCOME', 'TOTAL EXPENSES',\n"
    "  'Total Landscaping', 'Total Administration', 'Subtotal Income', 'Net Income'.\n"
    "- CRITICAL: SKIP any section header that reads like 'OPERATING EXPENSES Total',\n"
    "  'RESERVE CONTRIBUTIONS Total', or similar section-level totals. These are aggregations,\n"
    "  NOT individual line items. Extracting them duplicates the data.\n"
    "- SKIP pure section header rows that have no numeric values (e.g. 'OPERATING INCOME',\n"
    "  'EXPENSES', 'ASSESSMENT INCOME', 'EXPENSES: UTILITIES'). Use these ONLY to set section_kind.\n"
    "- Numbers in parentheses like (1,234.56) are NEGATIVE.\n"
    "- Dashes '-' or blanks mean 0 or null.\n"
    "- Dollar signs '$' are just formatting — ignore them.\n"
    "- Parse section headings carefully. Common section headings include:\n"
    "  Income side: 'OPERATING INCOME', 'ASSESSMENT INCOME', 'Income', 'INCOME',\n"
    "    'Regular Assessments', 'Other Income'\n"
    "  Expense side: 'OPERATING EXPENSES', 'EXPENSES', 'Expense', 'Expenses',\n"
    "    'EXPENSES: LANDSCAPING', 'EXPENSES: UTILITIES', 'EXPENSES: POOL',\n"
    "    'EXPENSES: GENERAL MAINTENANCE & REPAIRS', 'EXPENSES: INSURANCE',\n"
    "    'EXPENSES: ADMINISTRATION', 'EXPENSES: TAX', 'Repair & Maintenance',\n"
    "    'Utilities', 'Landscape', 'Administration', 'Insurance',\n"
    "    'ALLOCATION TO RESERVES', 'Allocation to Reserves', 'Transfer to Reserves',\n"
    "    'Reserve - Allocation/Transfer', 'Reserve Transfer', 'Reserve Funding'\n"
    "- Determine section_kind from the EXACT section header the row appears under:\n"
    "  'income' = Operating Income (all income/revenue items on the operating statement)\n"
    "  'operating' = Operating Expenses (utilities, landscape, common area, G&A, etc.),\n"
    "    AND any line appearing under 'Allocation to Reserves', 'Transfer to Reserves',\n"
    "    'Reserve - Allocation/Transfer', 'Reserve Funding', or similar operating-statement\n"
    "    sections. These are operating-side expense transfers and belong to 'operating'.\n"
    "  'reserve_income' = DO NOT USE on operating-statement pages.\n"
    "  'reserve_expense' = DO NOT USE on operating-statement pages.\n"
    "  CRITICAL RULE: THIS PAGE IS THE OPERATING STATEMENT. Every line item on this page\n"
    "  is either 'income' or 'operating'. Never 'reserve_income' and never 'reserve_expense'\n"
    "  on this page — those kinds are reserved ONLY for standalone Reserve statement pages,\n"
    "  which are handled by a different prompt. Even if a line's label contains the word\n"
    "  'reserve' (e.g. 'Reserve - Allocation/Transfer', 'Reserve Funding', 'Reserve Transfer'),\n"
    "  classify it as 'operating' because it is an operating-side transfer out to the\n"
    "  reserve account — not reserve income.\n"
    "  IMPORTANT: 'General and Administrative' is operating, NOT reserve.\n\n"
    "Return a JSON object with these fields:\n"
    '  "document_family": "pdf_visual_document"\n'
    '  "report_type": "income_statement"\n'
    '  "statement_period": "MM/DD/YYYY to MM/DD/YYYY" or null\n'
    '  "line_items": [ ... ]\n'
    '  "totals": [see TOTALS below]\n'
    '  "validation_issues": []\n'
    '  "confidence": 0.0\n\n'
    "Each line_items entry:\n"
    '  "account_code_text": "300000-00" or null\n'
    '  "label": "Member Assessments"\n'
    '  "section_label": "OPERATING INCOME"\n'
    '  "section_kind": "income"\n'
    '  "current_actual": 36410.58 or null\n'
    '  "current_budget": 36411.0 or null\n'
    '  "current_variance": -0.42 or null\n'
    '  "ytd_actual": 327695.22 or null\n'
    '  "ytd_budget": 327699.0 or null\n'
    '  "ytd_variance": -3.78 or null\n'
    '  "annual_budget": 436932.0 or null\n'
    '  "income_role": "regular_assessment" or null\n'
    '  "page_number": 1\n\n'
    "PAGE-BASED CLASSIFICATION PRINCIPLE:\n"
    "  Your classification must be driven by which STATEMENT this page belongs to,\n"
    "  NOT by pattern-matching on any specific label or value. On an operating\n"
    "  statement page, any transfer line to a reserve fund is an operating-side\n"
    "  expense transfer, regardless of the exact phrasing used by the accounting\n"
    "  software (could be 'Allocation to Reserves', 'Transfer to Reserves',\n"
    "  'Reserve Funding', 'Reserve Contribution', 'Reserve Transfer', or any\n"
    "  variation). All such lines → section_kind = 'operating'.\n\n"
    "Return ONLY the JSON object. No markdown, no explanation."
)

_PAGE_SYSTEM_PROMPT_PROFORMA_OPERATING = (
    "You are extracting line items from one HOA pro forma / final budget statement page.\n\n"
    "Return JSON only using the provided schema. Extract every visible detail line from both income and expense sections.\n\n"
    "RULES:\n"
    "- If multiple labeled money rows are visible, return all of them.\n"
    "- Do not collapse the table into one row.\n"
    "- Do not collapse the table or page into one row. Do not stop after the first row.\n"
    "- The minimum expected output is multiple detail rows whenever multiple labels with money values are visible.\n"
    "- Returning only the first visible income row is invalid when more labeled money rows are visible.\n"
    "- Scan both income and expense sections before finishing the page.\n"
    "- Return one line_items object per visible detail row, not one object per section or per page.\n"
    "- Skip totals, subtotals, net income rows, cumulative cash flow rows, and pure section headers.\n"
    "- Do not require account codes. Descriptive labels without account codes are valid detail rows.\n"
    "- If a label repeats on the right side before percent or annual columns, keep only the left or main label.\n"
    "- Numbers in parentheses are negative. Dashes or blanks are null.\n\n"
    "COLUMN RULES:\n"
    "- Expect layout variation. Do not assume fixed column numbers; column positions vary by template.\n"
    "- Use visible header meaning plus spatial alignment to identify annual budget.\n"
    "- Annual budget synonyms include: Approved Budget, Board Approved, Final Budget, Adopted Budget, Proposed Budget, "
    "2025 Budget, Next Year Budget, Annual Budget, Total Budget, Budget Total, Annualized Budget, Annualized, "
    "Projection, Forecast, and FY Budget.\n"
    "- Promote annual budget with this precedence:\n"
    "  final/approved/adopted/board approved > proposed/forecast/projection > annual/total/FY budget fallback.\n"
    "- If heading text is unclear but the page has Jan-Dec columns plus right-side annual-like money columns, use the "
    "rightmost annual-like money column as annual_budget unless it is clearly a percent column.\n"
    "- Record the promoted annual source in evidence.source_column using one of: final_budget, proposed_amount, annual_budget.\n"
    "- Monthly Jan-Dec values are contextual. They can inform current_actual or ytd_actual, but they are NOT the promoted annual budget amount.\n\n"
    "SECTION KIND RULES:\n"
    "- section_kind must be exactly one of: income, operating, reserve_income, reserve_expense, or null.\n"
    "- Never use revenue or expense as section_kind values.\n"
    "- Revenue-like, assessment, dues, and income rows -> income.\n"
    "- Normal operating expense rows -> operating.\n"
    "- Reserve interest rows -> reserve_income.\n"
    "- Reserve contribution, reserve transfer, transfer to reserve, allocation to reserve, and reserve funding rows -> reserve_expense.\n\n"
    "Return ONLY the JSON object. No markdown, no explanation."
)

_PAGE_SYSTEM_PROMPT_RESERVE = (
    "You are a financial document parser. You receive the text content of ONE PAGE "
    "from a RESERVE FUND statement within an HOA financial PDF, plus an image of that page. "
    "The text preserves the original column layout using tabs and spaces.\n\n"
    "This page is from a separate Reserve statement (not the Operating statement).\n\n"
    "Your job is to extract EVERY detail line item from THIS PAGE into structured JSON.\n\n"
    "RULES:\n"
    "- Extract ALL detail rows. Do NOT skip any. Do NOT summarize.\n"
    "- CRITICAL: SKIP any row whose label starts with 'Total' or 'TOTAL' or 'Subtotal' — these\n"
    "  are section subtotals and grand totals, NEVER extract them as line items. Examples to skip:\n"
    "  'Total Reserve Income', 'Total Reserve Expenses', 'TOTAL RESERVE: MECHANICAL/ELECTRICAL',\n"
    "  'TOTAL INCOME', 'TOTAL EXPENSES', 'Net Increase', 'Reserve NET INCREASE (DECREASE)'.\n"
    "- CRITICAL: SKIP any row that represents the statement-level total or section aggregate\n"
    "  (e.g. 'RESERVE CONTRIBUTIONS Total', 'OPERATING EXPENSES Total').\n"
    "- SKIP pure section header rows that have no numeric values (e.g. 'INCOME', 'EXPENSES',\n"
    "  'ASSESSMENT INCOME', 'Reserve'). Use these ONLY to understand section context.\n"
    "- Numbers in parentheses like (1,234.56) are NEGATIVE.\n"
    "- Dashes '-' or blanks mean 0 or null.\n"
    "- Dollar signs '$' are just formatting — ignore them.\n"
    "- Classify ALL income items on this page as section_kind='reserve_income'\n"
    "- Classify ALL expense items on this page as section_kind='reserve_expense'\n\n"
    "Return a JSON object with these fields:\n"
    '  "document_family": "pdf_visual_document"\n'
    '  "report_type": "income_statement"\n'
    '  "statement_period": null\n'
    '  "line_items": [ ... ]\n'
    '  "totals": [see TOTALS below]\n'
    '  "validation_issues": []\n'
    '  "confidence": 0.0\n\n'
    "Each line_items entry:\n"
    '  "account_code_text": "300000-00" or null\n'
    '  "label": "Interest Income"\n'
    '  "section_label": "RESERVE INCOME"\n'
    '  "section_kind": "reserve_income" or "reserve_expense"\n'
    '  "current_actual": 36410.58 or null\n'
    '  "current_budget": 36411.0 or null\n'
    '  "current_variance": -0.42 or null\n'
    '  "ytd_actual": 327695.22 or null\n'
    '  "ytd_budget": 327699.0 or null\n'
    '  "ytd_variance": -3.78 or null\n'
    '  "annual_budget": 436932.0 or null\n'
    '  "income_role": "interest" or null\n'
    '  "page_number": 1\n\n'
    "Return ONLY the JSON object. No markdown, no explanation."
)


def _split_pages(pdf_text: str) -> list[str]:
    """Split PDF text into per-page chunks based on '--- Page N ---' markers."""
    parts = re.split(r"--- Page \d+ ---\n?", pdf_text)
    return [p.strip() for p in parts if p.strip()]


def _split_pages_with_numbers(pdf_text: str) -> list[tuple[int, str]]:
    """Split PDF text and retain the original 1-based PDF page numbers."""
    pattern = re.compile(r"--- Page (\d+) ---\n?")
    matches = list(pattern.finditer(pdf_text))
    if not matches:
        stripped = pdf_text.strip()
        return [(1, stripped)] if stripped else []

    pages: list[tuple[int, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(pdf_text)
        page_text = pdf_text[start:end].strip()
        if page_text:
            pages.append((int(match.group(1)), page_text))
    return pages


async def _extract_single_page(
    page_num: int,
    page_text: str,
    page_image: Optional[RenderedPage],
    prompt_context: DocumentPromptContext,
    is_reserve_page: bool,
    *,
    no_text_layer: bool = False,
    response_schema: type[ExtractedFinancialStatementPage] = StatementPageWithTotals,
) -> Optional[ExtractedFinancialStatementPage]:
    """Extract line items from a single page via Gemini hybrid call.

    When ``no_text_layer=True`` (scanned PDF fallback), the prompt drops the
    "DOCUMENT TEXT" block entirely and tells the model to read everything from
    the image. This is a narrow fallback that only kicks in when pdfplumber
    could not find any text layer in the source PDF — normal text-based PDFs
    still go through the hybrid text+image path.
    """
    import asyncio
    from ..ai_implementation.pipeline.llm_client import call_llm_vision

    system_prompt = (
        _operating_prompt(prompt_context, is_reserve_page) + _TOTALS_INSTRUCTION + "\n\n" + INCOME_ROLE_RULES
    )
    page_type = "Reserve statement" if is_reserve_page else "Operating statement"
    notes_line = " | ".join(prompt_context.notes) if prompt_context.notes else "none"

    if no_text_layer:
        prompt_body = (
            f"Filename: {prompt_context.filename}\n"
            f"Page: {page_num} ({page_type})\n"
            f"Notes: {notes_line}\n\n"
            "NO TEXT LAYER — This PDF is a scanned or image-based document. "
            "No text extraction is available. Read EVERY line item, section "
            "header, label, and numeric value directly from the image. "
            "Numbers in parentheses like (1,234.56) are NEGATIVE.\n\n"
            "AGGRESSIVE NUMERIC EXTRACTION (CRITICAL):\n"
            "- If you can read a number from the page, you MUST include it in the JSON.\n"
            "- 'null' is reserved ONLY for cells that are visibly blank or contain a dash '-'.\n"
            "- '0.00', '$0.00', or '0' is the literal value 0.0 — NOT null.\n"
            "- Returning null for every numeric column is the wrong answer. If the page\n"
            "  is unreadable, return an empty line_items list instead. Never return rows\n"
            "  with all-null numerics.\n\n"
            "COLUMN MAPPING BY MEANING (column headers may NOT match schema names):\n"
            "  Examples of real column headers and where they go:\n"
            "  * 'Jan 09', 'Current Month', 'MTD', 'Period Actual', 'Actual',\n"
            "    'YTD Actual', 'YTD', 'Year to Date', or any single actual column → ytd_actual\n"
            "  * 'Budget' (when it's the only budget column), 'Annual Budget',\n"
            "    'FY Budget', 'Total Budget' → annual_budget\n"
            "  * '$ Over Budget', 'Variance', 'Diff', 'Over/(Under)' → ytd_variance\n"
            "  * '% of Budget', 'Pct', percent columns → SKIP (no schema slot)\n"
            "  * If the report has BOTH a period actual AND a YTD actual column,\n"
            "    use current_actual for the period and ytd_actual for the YTD.\n"
            "  * If a row has only ONE numeric value, place it in ytd_actual.\n"
            "  Choose the schema field that best matches the column's MEANING in the\n"
            "  context of the report — never leave a number unmapped.\n\n"
            "Extract every detail line item from this page."
        )
    else:
        prompt_body = (
            f"Filename: {prompt_context.filename}\n"
            f"Page: {page_num} ({page_type})\n"
            f"Notes: {notes_line}\n\n"
            f"DOCUMENT TEXT (page {page_num}):\n{page_text}\n\n"
            "Extract every detail line item from this page."
        )

    user_content_parts: list[dict] = [{"type": "text", "text": prompt_body}]
    if page_image is not None and page_image.content is not None:
        user_content_parts.append({
            "type": "image",
            "data": page_image.content,
            "mime_type": page_image.mime_type,
        })

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content_parts},
    ]

    result = await call_llm_vision(
        messages,
        response_schema,
        temperature=0.0,
        timeout=_BUDGET_VISION_TIMEOUT_SECONDS,
        backoff_delays=_GEMINI_RETRY_DELAYS,
        timeout_retries=_GEMINI_TIMEOUT_RETRIES,
    )

    # Stamp page_number on all extracted items
    if result is not None and hasattr(result, "line_items"):
        for item in result.line_items:
            item.page_number = page_num

    return result


def _is_scanned_pdf_error(exc: BaseException) -> bool:
    """Return True if the given exception is the 'no text layer' signal from
    ``_extract_pdf_text_table``. We match on the specific message substring
    rather than the exception type alone so generic pdfplumber errors
    (corrupted PDF, permission issues, etc.) continue to fail loudly instead
    of silently falling back to the vision-only path."""
    return isinstance(exc, ValueError) and "no text layer" in str(exc).lower()


async def _extract_full_document(
    pdf_path: str,
    prompt_context: DocumentPromptContext,
    schema: type[ExtractedFinancialStatement],
    max_pages: int,
    *,
    choice: Optional[PageChoice] = None,
) -> Optional[ExtractedFinancialStatement | dict[str, Any]]:
    """Extract full document via parallel per-page Gemini calls.

    Fires one Gemini call per page concurrently, then merges results.
    Reserve statement pages are tagged so items get reserve_expense/reserve_income classification.

    Scanned-PDF fallback: if pdfplumber reports no text layer (e.g. Cummins
    Park, which is a pure raster scan), we switch this single call to a
    vision-only path — render the pages with PyMuPDF, send the images to
    Gemini without any accompanying text, and let the model read everything
    from the pixels. This fallback is narrow: it ONLY triggers on the specific
    "no text layer" ValueError from ``_extract_pdf_text_table``; text-based
    PDFs still use the hybrid text+image path.
    """
    if choice is None:
        pages = await _classify_pages(pdf_path, prompt_context.filename)
        choice = _choose_pages(pages, prompt_context.source_mode)
        if choice is None:
            raise NotABudgetSource(pages)
    selected_pages: list[int] = list(choice.pages)

    # Try the hybrid path first. If pdfplumber reports no text layer we fall
    # through to vision-only; any other exception bubbles up unchanged.
    no_text_layer = False
    try:
        pdf_text = _extract_pdf_text_table_for_pages(pdf_path, selected_pages)
        page_entries = _split_pages_with_numbers(pdf_text)
    except ValueError as exc:
        if not _is_scanned_pdf_error(exc):
            raise
        no_text_layer = True
        page_entries = []
        logger.warning(
            "Scanned PDF fallback engaged for %s: %s. Switching to vision-only "
            "extraction (no text layer). Hybrid text+image mode remains the "
            "default for all other PDFs.",
            Path(pdf_path).name,
            exc,
        )

    # Scanned PDFs need high-resolution rendering because the image is the
    # ONLY source of numerics (no pdfplumber text to fall back on). Normal
    # hybrid PDFs stay at the default 72 DPI to keep payloads small — their
    # numerics come from the text block anyway.
    render_dpi = _SCANNED_FALLBACK_RENDER_DPI if no_text_layer else _HYBRID_RENDER_DPI
    rendered_pages = render_pdf_pages_for_numbers(pdf_path, selected_pages, dpi=render_dpi)
    if no_text_layer:
        # In scanned mode we have no text to split — use the rendered page
        # count as the authoritative page count and pass empty strings so the
        # per-page prompt drops the DOCUMENT TEXT block.
        if not rendered_pages:
            raise RuntimeError(
                "Scanned PDF fallback failed: no pages could be rendered by PyMuPDF. "
                "The file may be corrupted or password-protected."
            )
        page_entries = [(page.page_number, "") for page in rendered_pages]

    # Metadata about HOW this extraction was produced. Surfaced to the
    # end user via the upload response so the frontend can show a quality
    # warning when we took the degraded vision-only path.
    extraction_metadata: dict[str, Any] = {
        "page_selection_used": True,
        "selected_pages": selected_pages,
        "pages_used": selected_pages,
        "pages_note": choice.note,
    }
    if no_text_layer:
        extraction_metadata["used_vision_only_fallback"] = True
        extraction_metadata["fallback_reason"] = "no_text_layer"
        extraction_metadata["render_dpi"] = render_dpi

    images_by_page = {page.page_number: page for page in rendered_pages}
    report_type = "final_budget" if choice.source_mode == _PROFORMA_SOURCE_MODE else "income_statement"

    # If only 1 page, use simpler single-call path
    if len(page_entries) == 1:
        page_num, page_text = page_entries[0]
        # Scanned single-page PDFs default to the operating prompt. We can't
        # classify reserve vs operating without text, and the operating prompt
        # covers the vast majority of real HOA statements. If a reserve-only
        # scan ever shows up in production, we'll need vision-only reserve
        # detection as a follow-up.
        is_reserve = page_num in choice.reserve_pages
        page_result = await _extract_single_page(
            page_num,
            page_text,
            images_by_page.get(page_num),
            prompt_context, is_reserve,
            no_text_layer=no_text_layer,
        )
        if page_result is None or not page_result.line_items:
            return None
        return ExtractedFinancialStatement(
            document_family="pdf_visual_document",
            report_type=report_type,
            statement_period=page_result.statement_period,
            line_items=page_result.line_items,
            totals=totals_for_validation(list(getattr(page_result, "totals", None) or [])),
            validation_issues=[],
            confidence=0.0,
            extraction_metadata=extraction_metadata,
        )

    # Tag each page as operating or reserve. In scanned mode we can't inspect
    # text for reserve markers — every page is classified as operating.
    page_infos = []
    for page_num, text in page_entries:
        image = images_by_page.get(page_num)
        page_infos.append((page_num, text, image, page_num in choice.reserve_pages))

    logger.info(
        "Parallel extraction: %d pages (%d operating, %d reserve)%s",
        len(page_infos),
        sum(1 for _, _, _, r in page_infos if not r),
        sum(1 for _, _, _, r in page_infos if r),
        " [scanned-PDF vision-only fallback]" if no_text_layer else "",
    )

    # Fire all pages in parallel
    tasks = [
        _extract_single_page(pn, pt, pi, prompt_context, ir, no_text_layer=no_text_layer)
        for pn, pt, pi, ir in page_infos
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # STRICT MODE: refuse to return a partial extraction.
    #
    # Previous behavior merged results and returned whatever pages succeeded,
    # silently dropping failed pages. That produced drafts that looked real
    # but were missing ~half the statement, and downstream had no way to tell.
    #
    # New behavior: if ANY page returned an exception or None (even after the
    # retry loop inside call_llm_vision), refuse to return a merged result.
    # The caller (extract_pdf_statement) will surface this as a clean
    # review-required failure so the user knows to re-upload.
    failed_pages: list[tuple[int, str]] = []
    for i, result in enumerate(results):
        page_num = page_infos[i][0]
        if isinstance(result, Exception):
            failed_pages.append((page_num, f"exception: {result}"))
        elif result is None:
            failed_pages.append((page_num, "returned None (exhausted retries in call_llm_vision)"))

    if failed_pages:
        failure_summary = "; ".join(f"page {pn}: {reason}" for pn, reason in failed_pages)
        logger.error(
            "Strict mode: refusing partial extraction. %d of %d page(s) failed — %s",
            len(failed_pages),
            len(page_infos),
            failure_summary,
        )
        # Raise rather than return None so the caller's error path surfaces
        # the specific page-failure reason to the user (via the
        # DocumentExtractionFailure.details["error"] field propagated by
        # extract_pdf_statement's generic Exception handler).
        raise RuntimeError(
            f"PDF extraction failed: {len(failed_pages)} of {len(page_infos)} "
            f"page(s) could not be extracted — {failure_summary}. "
            "Upload was refused to prevent a partial/incomplete statement."
        )

    # All pages succeeded — merge results
    all_line_items = []
    printed_totals: list[Any] = []
    statement_period = None

    for result in results:
        if hasattr(result, "line_items"):
            all_line_items.extend(result.line_items)
        printed_totals.extend(getattr(result, "totals", None) or [])
        if not statement_period and hasattr(result, "statement_period") and result.statement_period:
            statement_period = result.statement_period

    if not all_line_items:
        # Every page succeeded structurally but returned zero line items —
        # still a failure, just a different one. Treat as "nothing extractable".
        logger.error("Strict mode: all %d pages returned successfully but no line items were extracted", len(page_infos))
        raise RuntimeError(
            f"PDF extraction failed: all {len(page_infos)} page(s) returned successfully "
            "but contained zero line items. The document may be scanned, corrupted, or "
            "formatted in an unsupported layout."
        )

    # Drop only exact repeats (a row carried onto the next page). Rows that
    # share a label but sit in different sections or carry different amounts
    # are distinct lines (e.g. "Interest Income" in operating and reserve).
    seen: dict[tuple, Any] = {}
    for item in all_line_items:
        key = (
            item.account_code_text, item.label, item.section_kind,
            item.current_actual, item.ytd_actual, item.annual_budget,
        )
        seen.setdefault(key, item)

    deduped_items = list(seen.values())

    logger.info("Merged %d items from %d pages (%d after dedup)", len(all_line_items), len(page_infos), len(deduped_items))

    return ExtractedFinancialStatement(
        document_family="pdf_visual_document",
        report_type=report_type,
        statement_period=statement_period,
        line_items=deduped_items,
        totals=totals_for_validation(printed_totals),
        validation_issues=[],
        confidence=0.0,
        extraction_metadata=extraction_metadata,
    )


def _page_types_summary(pages: list[PageClassification]) -> str:
    by_type: dict[str, list[int]] = {}
    for page in pages:
        by_type.setdefault(page.page_type.replace("_", " "), []).append(page.page)
    return "; ".join(f"{kind} p{_page_ranges(numbers)}" for kind, numbers in by_type.items())


async def _facts_or_none(task: Optional[asyncio.Task]) -> Optional[dict[str, Any]]:
    """Facts are a bonus: a failed facts call never fails the upload."""
    if task is None:
        return None
    try:
        facts = await task
    except Exception as exc:
        logger.warning("Disclosure facts extraction failed: %s", exc)
        return None
    return facts.model_dump() if facts is not None else None


async def extract_pdf_statement(
    path: str,
    provider: Optional[Any] = None,
    *,
    max_pages: Optional[int] = None,
    source_mode: Optional[str] = None,
    filename: Optional[str] = None,
) -> ExtractedFinancialStatement | DocumentExtractionFailure:
    """Extract a canonical financial statement from any budget-source PDF.

    Gemini classifies every page first; the chosen block decides whether the
    PDF is read as an income statement or a final budget (``report_type``),
    unless ``source_mode`` overrides it. Pages that state disclosure facts are
    read at the same time as the statement pages. A custom ``provider``
    (tests) skips classification and reads the first pages as before.
    """
    prompt_context = DocumentPromptContext(
        filename=filename or Path(path).name,
        route_family="pdf_visual_document",
        source_mode=source_mode,
    )

    choice: Optional[PageChoice] = None
    facts_task: Optional[asyncio.Task] = None
    if provider is None:
        try:
            page_count = _get_pdf_page_count(path)
            if page_count > _MAX_PDF_PAGES:
                return DocumentExtractionFailure(
                    code="pdf_too_long",
                    message=(
                        f"This PDF has {page_count} pages; the limit is {_MAX_PDF_PAGES}. "
                        "Upload the budget or statement pages on their own."
                    ),
                    details={"page_count": page_count},
                )
            pages = await _classify_pages(path, prompt_context.filename)
        except Exception as exc:
            return DocumentExtractionFailure(
                code="provider_error",
                message=f"Gemini could not read the pages of this PDF: {exc}",
                details={"error": str(exc)},
            )
        choice = _choose_pages(pages, source_mode)
        fact_pages = (
            [page.page for page in pages if page.has_disclosure_facts][:_MAX_FACT_PAGES]
            if choice is None
            else choice.fact_pages
        )
        if fact_pages:
            facts_task = asyncio.create_task(
                _extract_disclosure_facts(path, fact_pages, prompt_context.filename)
            )
        if choice is None:
            return DocumentExtractionFailure(
                code="not_a_budget_source",
                message=(
                    "This PDF has no income statement or budget pages "
                    f"(found: {_page_types_summary(pages)}). Upload an income statement or a final budget."
                ),
                details={
                    "page_types": [page.model_dump() for page in pages],
                    "disclosure_facts": await _facts_or_none(facts_task),
                },
            )
        prompt_context.source_mode = choice.source_mode

    last_validation_issues: list[dict[str, Any]] = []
    try:

        for attempt in range(2):
            raw_result: Any = None
            try:
                if provider is not None:
                    # Custom provider (tests) — use vision path
                    rendered_pages = render_pdf_pages(path, max_pages=max_pages or settings.DOCUMENT_VLM_MAX_PAGES)
                    raw_result = await provider.extract_statement(
                        rendered_pages,
                        schema=ExtractedFinancialStatement,
                        prompt_context=prompt_context,
                    )
                else:
                    # Default path: hybrid text+image single-call extraction
                    raw_result = await _extract_full_document(
                        path, prompt_context, ExtractedFinancialStatement,
                        max_pages=max_pages or settings.DOCUMENT_VLM_MAX_PAGES,
                        choice=choice,
                    )
                    if raw_result is None:
                        raise RuntimeError("Gemini extraction returned no structured result.")

                statement = (
                    raw_result
                    if isinstance(raw_result, ExtractedFinancialStatement)
                    else ExtractedFinancialStatement.model_validate(raw_result)
                )
            except ValidationError as exc:
                _log_schema_validation_failure_sample(
                    raw_result,
                    path=path,
                    source_mode=source_mode,
                    attempt=attempt + 1,
                )
                if attempt == 0:
                    prompt_context = deepcopy(prompt_context)
                    prompt_context.notes.append(
                        _build_schema_retry_note(
                            raw_result,
                            exc,
                            path=path,
                            source_mode=source_mode,
                        )
                    )
                    continue
                return DocumentExtractionFailure(
                    code="schema_validation_failed",
                    message="Structured PDF extraction could not satisfy the canonical schema.",
                    details={"error": str(exc)},
                )
            except NotABudgetSource as exc:
                return DocumentExtractionFailure(
                    code="not_a_budget_source",
                    message=(
                        "This PDF has no income statement or budget pages "
                        f"(found: {_page_types_summary(exc.pages)})."
                    ),
                    details={"page_types": [page.model_dump() for page in exc.pages]},
                )
            except Exception as exc:
                # Include the exception message in the surfaced error so the user
                # sees which page(s) failed (e.g. "page 1: returned None (exhausted
                # retries)") rather than a generic "provider_error". This is the
                # path hit by the strict-mode guard in _extract_full_document when
                # any per-page Gemini call fails after all retries.
                return DocumentExtractionFailure(
                    code="provider_error",
                    message=f"The vision extraction provider failed: {exc}",
                    details={"error": str(exc)},
                )

            issues = validate_extracted_statement(statement)
            derived_confidence = derive_statement_confidence(statement, issues)
            enriched_statement = statement.model_copy(
                update={
                    "validation_issues": issues,
                    "confidence": derived_confidence,
                }
            )
            if not has_blocking_validation_issues(issues):
                enriched_statement.extraction_metadata["disclosure_facts"] = await _facts_or_none(facts_task)
                facts_task = None
                return enriched_statement

            last_validation_issues = issues
            if attempt == 0:
                prompt_context = deepcopy(prompt_context)
                prompt_context.notes.append(f"Validation issues from previous attempt: {issues}")
                continue

            return DocumentExtractionFailure(
                code="validation_failed",
                message="Structured PDF extraction failed deterministic validation checks.",
                details={"validation_issues": issues},
            )

        return DocumentExtractionFailure(
            code="validation_failed",
            message="Structured PDF extraction failed deterministic validation checks.",
            details={"validation_issues": last_validation_issues},
        )
    finally:
        if facts_task is not None:
            facts_task.cancel()
