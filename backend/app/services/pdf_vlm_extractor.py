"""LLM-first PDF extraction with schema enforcement and deterministic validation.

Uses pdfplumber text extraction + Gemini (hybrid text + image mode) for single-call extraction.
"""
from __future__ import annotations

import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Optional, Any

from pydantic import BaseModel, Field, ValidationError

from ..config import settings
from ..models.financial_document_extraction import (
    DocumentExtractionFailure,
    ExtractedFinancialStatement,
    ExtractedFinancialStatementPage,
)
from ..ai_implementation.pipeline.document_extraction_provider import (
    DocumentPromptContext,
    RenderedPage,
    VisionStatementExtractor,
)
from .financial_statement_validation import (
    derive_statement_confidence,
    has_blocking_validation_issues,
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
_BUDGET_VISION_TIMEOUT_SECONDS = 240.0


class StatementPageCandidate(BaseModel):
    page: int = Field(ge=1)
    classification: str = Field(default="unknown")
    selected: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class StatementPageSelection(BaseModel):
    selected_pages: list[int] = Field(default_factory=list)
    candidates: list[StatementPageCandidate] = Field(default_factory=list)
    rejected_pages: list[StatementPageCandidate] = Field(default_factory=list)


class StatementPagesNotFound(RuntimeError):
    def __init__(self, selection: StatementPageSelection):
        super().__init__(
            "No income statement pages with annual budget columns were found in this PDF package."
        )
        self.selection = selection


_PAGE_SELECTION_SYSTEM_PROMPT = """You are classifying pages from a scanned HOA financial PDF package.

Goal:
Find the page numbers that should be sent to a financial-statement line-item extractor for budget drafting.

Select pages ONLY if they are income statement / revenues and expenses pages that contain detail line items. This includes operating statement pages and reserve income/expense statement pages when they are part of the same financial statement package.

A page SHOULD be selected when:
- The title or header indicates an operating statement, such as:
  - Statement of Revenues and Expenses
  - Income and Expense to Budget
  - Operating Income and Expense
  - Statement of Operating Revenues and Expenses
  - Budget Comparison
  - Income Statement
- The page contains detail line items with account codes or account labels.
- The page has budget-related columns, especially:
  - Annual Budget
  - Budget
  - Current Period Budget
  - Year To Date Budget
- The page is part of a continued operating statement split across multiple pages.
- The page is a reserve income/expense statement page with detail line items, such as:
  - Reserve Income
  - Reserve Expense
  - Reserve Fund Activity
  - Reserve Income and Expense to Budget

A strong preference rule:
- Prefer the detailed contiguous income statement block that appears to be the source statement for the period.
- Prefer pages with detail rows over grouped or presentation-style summaries.
- Account-code or GL-style rows are a strong signal that a page is a detailed source statement, but this is a preferred signal, not a hard requirement. Some valid source statements use descriptive line labels without account codes.
- If both of these appear in the same PDF package, prefer the detailed source-statement block and reject the later restatement block:
  - detailed source rows such as account-code, GL-style, or line-by-line operating/revenue/expense rows
  - summarized restatement rows such as grouped client-style labels, accountant summary pages, disclosure-style summaries, forecasted statements, or pro forma presentations
- Select the continuation pages of the chosen detailed block, including a reserve income/expense tail when it is clearly part of that same statement package.

A page SHOULD NOT be selected when it is:
- Cover letter, title page, table of contents, notes, assumptions, compilation report
- Assessment schedule or assessments per unit/month
- Reserve study schedule or component schedule
- Insurance disclosure
- Balance sheet
- AP aging
- GL trial balance
- Check register or check images
- Invoice backup
- Bank statement
- Any page with only totals and no detail line items
- Any page whose main purpose is per-unit assessments rather than income/expense budget drafting
- Later forecasted, pro forma, accountant summary, board summary, or disclosure summary pages that restate the same economics in grouped/client-style labels instead of source detail rows

Important:
- Do not select pages merely because they contain dollar amounts.
- Do not select an assessment schedule even if it has monthly assessment or annual total columns.
- Prefer statement pages with an Annual Budget column, but do not reject a reserve income/expense continuation page only because its Annual Budget column is blank.
- If there are multiple consecutive pages of the same operating or reserve statement, select all of them.
- If a reserve income/expense statement appears separately, select it and classify it as reserve_statement.
- Do not select reserve study component schedules; classify those as reserve_study_schedule and reject them.
- Do not combine detailed source rows and summarized restatement rows into the same financial line-item list.
- When duplicate economics appear in multiple formats within one package, choose one coherent page block: the most detailed contiguous source statement.

Return JSON only:

{
  "selected_pages": [28, 29],
  "candidates": [
    {
      "page": 28,
      "classification": "operating_statement",
      "selected": true,
      "confidence": 0.96,
      "reason": "Statement of Revenues and Expenses with Annual Budget column and detail line items"
    }
  ],
  "rejected_pages": [
    {
      "page": 24,
      "classification": "assessment_schedule",
      "selected": false,
      "reason": "Assessments per unit per month, not an income statement"
    }
  ]
}"""

_PROFORMA_PAGE_SELECTION_SYSTEM_PROMPT = """You are classifying pages from an HOA pro forma / final budget PDF package.

Goal:
Find the page numbers that should be sent to a financial-statement extractor for pro forma / final budget drafting.

Select pages ONLY if they are spreadsheet-export operating/cash-flow budget pages with detail rows. Prefer pages with:
- Titles like OPERATING AND CASH FLOW STATEMENT, PRO FORMA OPERATING AND CASH FLOW STATEMENT, or similar
- Jan-Dec month grids
- Income: / Expense: sections
- Annual columns such as Budget, Proposed, Final, Approved, or Annual Budget

Reject pages that are:
- Cover pages, notes, assumptions, reserve-study schedules, assessments-per-unit schedules, balance sheets
- Summary-only pages without detail rows
- Ratio or dashboard pages where most columns are percentages instead of money
- Later presentation summaries that restate the same budget in grouped labels instead of detail rows

When multiple candidate pages exist, choose the most detailed contiguous operating/cash-flow statement block.

Return JSON only using the same schema as the default page-selection prompt."""


def _is_proforma_mode(prompt_context: DocumentPromptContext) -> bool:
    return (prompt_context.source_mode or "").strip().lower() == "proforma_final_budget"


def _page_selection_prompt(prompt_context: DocumentPromptContext) -> str:
    return (
        _PROFORMA_PAGE_SELECTION_SYSTEM_PROMPT
        if _is_proforma_mode(prompt_context)
        else _PAGE_SELECTION_SYSTEM_PROMPT
    )


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

    Kept for backward compatibility with tests and the VisionStatementExtractor
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


def _should_run_page_selection(pdf_path: str) -> bool:
    return _get_pdf_page_count(pdf_path) > settings.DOCUMENT_VLM_PAGE_SELECTION_THRESHOLD


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


def _render_page_selection_images(pdf_path: str, max_pages: int) -> list[RenderedPage]:
    return render_pdf_pages(pdf_path, max_pages=max_pages, dpi=_HYBRID_RENDER_DPI)


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


async def _select_income_statement_pages(
    pdf_path: str,
    prompt_context: DocumentPromptContext,
    *,
    max_pages: int,
) -> StatementPageSelection:
    from ..ai_implementation.pipeline.llm_client import call_llm_vision

    rendered_pages = _render_page_selection_images(pdf_path, max_pages=max_pages)
    user_content_parts: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Filename: {prompt_context.filename}\n"
                f"Rendered page count: {len(rendered_pages)}\n\n"
                "Classify these pages and identify the operating income statement pages."
            ),
        }
    ]
    for page in rendered_pages:
        user_content_parts.append(
            {
                "type": "text",
                "text": f"PDF page {page.page_number}",
            }
        )
        if page.content is not None:
            user_content_parts.append(
                {
                    "type": "image",
                    "data": page.content,
                    "mime_type": page.mime_type,
                }
            )

    result = await call_llm_vision(
        [
            {"role": "system", "content": _page_selection_prompt(prompt_context)},
            {"role": "user", "content": user_content_parts},
        ],
        StatementPageSelection,
        temperature=0.0,
        timeout=120.0,
    )
    if isinstance(result, StatementPageSelection):
        return result
    return StatementPageSelection()


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
    '  "totals": []\n'
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
    '  "totals": []\n'
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


def _is_reserve_statement_page(page_text: str) -> bool:
    """Detect if a page belongs to a separate Reserve statement (not the Operating statement).

    Strategy: inspect the page header area (first ~500 chars, which typically
    contains the statement title and date range). A page is a reserve
    statement if:

    1. No operating-statement marker appears in the header (early-exit guard
       against false positives on operating pages that happen to mention
       "Reserve Contribution" as a section).
    2. A reserve-statement title pattern appears in the header.

    This used to require the exact phrase "income statement" or
    "fund statement" to appear alongside "reserve", which missed common
    real-world variants like:
        "Reserve Income and Expense to Budget"   (Esprit Park)
        "Reserve Fund Activity"
        "Reserve Funding Schedule"
        "Reserve Study Summary"
    When those pages were misclassified as operating, the operating-page
    prompt would dutifully classify their items as 'income' or 'operating'
    (per the strict rule we added), inflating the operating INCOME total
    with reserve-income rows and leaving reserve-expense rows at zero.
    """
    header_area = page_text[:500].lower()

    # Step 1 — operating markers disqualify. These are strings that only
    # appear on operating statements, never on reserve-only pages.
    operating_markers = (
        "operating income",       # "Operating Income Statement", "Operating Income & Expense"
        "operating statement",
        "operating expense",      # some PDFs title the operating side this way
        "statement of operating",
    )
    if any(marker in header_area for marker in operating_markers):
        return False

    # Step 2 — reserve title markers. Any of these in the header area means
    # the page is a reserve statement. All are generic HOA-accounting title
    # fragments, not PDF-specific — they describe the kind of report, not a
    # specific HOA's naming convention.
    reserve_title_markers = (
        "reserve income",         # "Reserve Income and Expense to Budget", "Reserve Income Statement"
        "reserve fund",           # "Reserve Fund Statement", "Reserve Fund Activity"
        "reserve statement",
        "reserve activity",
        "reserve budget",
        "reserve study",
        "reserve funding",
    )
    if any(marker in header_area for marker in reserve_title_markers):
        return True

    # Step 3 — legacy fallback: "reserve" + generic "statement" keyword.
    # Catches rare title formats like "Income Statement / Reserve" that
    # don't match the title markers above but still clearly name a reserve
    # report (e.g., Mathilda's page 4 header).
    if "reserve" in header_area and ("income statement" in header_area or "fund statement" in header_area):
        return True

    return False


def _is_reserve_statement_classification(classification: str) -> bool:
    normalized = (classification or "").strip().lower()
    return (
        "reserve" in normalized
        and "study" not in normalized
        and "schedule" not in normalized
        and "component" not in normalized
    )


async def _extract_single_page(
    page_num: int,
    page_text: str,
    page_image: Optional[RenderedPage],
    prompt_context: DocumentPromptContext,
    is_reserve_page: bool,
    *,
    no_text_layer: bool = False,
    response_schema: type[ExtractedFinancialStatementPage] = ExtractedFinancialStatementPage,
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

    system_prompt = _operating_prompt(prompt_context, is_reserve_page)
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
    import asyncio

    page_count = _get_pdf_page_count(pdf_path)
    page_selection: StatementPageSelection | None = None
    selected_pages: list[int] | None = None
    selected_page_classifications: dict[int, str] = {}
    if page_count > settings.DOCUMENT_VLM_PAGE_SELECTION_THRESHOLD:
        page_selection = await _select_income_statement_pages(
            pdf_path,
            prompt_context,
            max_pages=min(page_count, settings.DOCUMENT_VLM_PAGE_SELECTION_MAX_PAGES),
        )
        selected_pages = sorted(
            {
                int(page)
                for page in page_selection.selected_pages
                if 1 <= int(page) <= page_count
            }
        )
        if not selected_pages:
            raise StatementPagesNotFound(page_selection)
        selected_page_classifications = {
            int(candidate.page): candidate.classification
            for candidate in page_selection.candidates
            if candidate.selected and 1 <= int(candidate.page) <= page_count
        }

    # Try the hybrid path first. If pdfplumber reports no text layer we fall
    # through to vision-only; any other exception bubbles up unchanged.
    no_text_layer = False
    try:
        if selected_pages is not None:
            pdf_text = _extract_pdf_text_table_for_pages(pdf_path, selected_pages)
        else:
            pdf_text = _extract_pdf_text_table(pdf_path, max_pages=max_pages)
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
    if selected_pages is not None:
        rendered_pages = render_pdf_pages_for_numbers(pdf_path, selected_pages, dpi=render_dpi)
    else:
        rendered_pages = render_pdf_pages(pdf_path, max_pages=max_pages, dpi=render_dpi)
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
    extraction_metadata: dict[str, Any] = {}
    if page_selection is not None:
        extraction_metadata["page_selection_used"] = True
        extraction_metadata["selected_pages"] = selected_pages or []
        extraction_metadata["selection_candidates"] = [
            candidate.model_dump() for candidate in page_selection.candidates
        ]
    else:
        extraction_metadata["page_selection_used"] = False
    if no_text_layer:
        extraction_metadata["used_vision_only_fallback"] = True
        extraction_metadata["fallback_reason"] = "no_text_layer"
        extraction_metadata["render_dpi"] = render_dpi

    images_by_page = {page.page_number: page for page in rendered_pages}

    # If only 1 page, use simpler single-call path
    if len(page_entries) == 1:
        page_num, page_text = page_entries[0]
        # Scanned single-page PDFs default to the operating prompt. We can't
        # classify reserve vs operating without text, and the operating prompt
        # covers the vast majority of real HOA statements. If a reserve-only
        # scan ever shows up in production, we'll need vision-only reserve
        # detection as a follow-up.
        if no_text_layer:
            is_reserve = _is_reserve_statement_classification(
                selected_page_classifications.get(page_num, "")
            )
        else:
            is_reserve = _is_reserve_statement_page(page_text)
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
            report_type="income_statement",
            statement_period=page_result.statement_period,
            line_items=page_result.line_items,
            totals=[],
            validation_issues=[],
            confidence=0.0,
            extraction_metadata=extraction_metadata,
        )

    # Tag each page as operating or reserve. In scanned mode we can't inspect
    # text for reserve markers — every page is classified as operating.
    page_infos = []
    for page_num, text in page_entries:
        image = images_by_page.get(page_num)
        if no_text_layer:
            is_reserve = _is_reserve_statement_classification(
                selected_page_classifications.get(page_num, "")
            )
        else:
            is_reserve = _is_reserve_statement_page(text)
        page_infos.append((page_num, text, image, is_reserve))

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
    statement_period = None

    for result in results:
        if hasattr(result, "line_items"):
            all_line_items.extend(result.line_items)
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

    # Deduplicate by (account_code_text, label) — keep the one with more numeric fields
    seen: dict[tuple, Any] = {}
    for item in all_line_items:
        key = (item.account_code_text, item.label)
        if key in seen:
            existing = seen[key]
            new_count = sum(1 for v in [item.ytd_actual, item.annual_budget, item.current_actual] if v is not None)
            old_count = sum(1 for v in [existing.ytd_actual, existing.annual_budget, existing.current_actual] if v is not None)
            if new_count > old_count:
                seen[key] = item
        else:
            seen[key] = item

    deduped_items = list(seen.values())

    logger.info("Merged %d items from %d pages (%d after dedup)", len(all_line_items), len(page_infos), len(deduped_items))

    return ExtractedFinancialStatement(
        document_family="pdf_visual_document",
        report_type="income_statement",
        statement_period=statement_period,
        line_items=deduped_items,
        totals=[],
        validation_issues=[],
        confidence=0.0,
        extraction_metadata=extraction_metadata,
    )


async def extract_pdf_statement(
    path: str,
    provider: Optional[VisionStatementExtractor] = None,
    *,
    max_pages: Optional[int] = None,
    source_mode: str = "income_statement",
) -> ExtractedFinancialStatement | DocumentExtractionFailure:
    """Extract a canonical financial statement from a PDF.

    Primary path: pdfplumber text extraction + Gemini hybrid text+image model (single call).
    Falls back to vision path if a custom provider is supplied.
    """
    prompt_context = DocumentPromptContext(
        filename=Path(path).name,
        route_family="pdf_visual_document",
        source_mode=source_mode,
    )

    last_validation_issues: list[dict[str, Any]] = []

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
        except StatementPagesNotFound as exc:
            return DocumentExtractionFailure(
                code="statement_pages_not_found",
                message=(
                    "No income statement pages with annual budget columns were found "
                    "in this PDF package."
                ),
                details={
                    "selected_pages": exc.selection.selected_pages,
                    "candidates": [candidate.model_dump() for candidate in exc.selection.candidates],
                    "rejected_pages": [
                        candidate.model_dump() for candidate in exc.selection.rejected_pages
                    ],
                },
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
