"""LLM-first PDF extraction with schema enforcement and deterministic validation.

Uses pdfplumber text extraction + Gemini (hybrid text + image mode) for single-call extraction.
"""
from __future__ import annotations

import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Optional, Any

from pydantic import ValidationError

from ..config import settings
from ..models.financial_document_extraction import (
    DocumentExtractionFailure,
    ExtractedFinancialStatement,
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


def render_pdf_pages(path: str, max_pages: Optional[int] = None) -> list[RenderedPage]:
    """Render PDF pages to PNG bytes for the hybrid ingestion path.

    Kept for backward compatibility with tests and the VisionStatementExtractor
    protocol.
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
    try:
        for page_number, page in enumerate(document, start=1):
            if page_number > page_limit:
                break
            pixmap = page.get_pixmap()
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


_SYSTEM_PROMPT = (
    "You are a financial document parser. You receive the text content of an HOA "
    "income statement extracted from a PDF. The text preserves the original column "
    "layout using tabs and spaces.\n\n"
    "Your job is to extract EVERY detail line item into structured JSON.\n\n"
    "RULES:\n"
    "- Extract ALL detail rows. A typical statement has 30-120 line items.\n"
    "- Do NOT skip any rows. Do NOT summarize.\n"
    "- SKIP rows that start with 'Total' — these are subtotals, not detail items.\n"
    "- SKIP section header rows that have no numeric values.\n"
    "- Numbers in parentheses like (1,234.56) are NEGATIVE.\n"
    "- Dashes '-' or blanks mean 0 or null.\n"
    "- Dollar signs '$' are just formatting — ignore them.\n"
    "- Determine section_kind from the EXACT section header the row appears under:\n"
    "  'income' = Operating Income (all income/revenue items)\n"
    "  'operating' = Operating Expenses (utilities, landscape, common area, general & admin, etc.)\n"
    "  'reserve_income' = Reserve Funding / Reserve Contributions / Reserve Income\n"
    "    (items like Fitness Room/Gym, Gates, Generators, Residential Reserve Funding, Interest, Paint, Restoration)\n"
    "  'reserve_expense' = Reserve Expenses (items under a section explicitly labeled 'Reserve Expenses')\n"
    "  IMPORTANT: 'Reserve Funding' is reserve_income, NOT reserve_expense.\n"
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
    "Return ONLY the JSON object. No markdown, no explanation."
)


def _split_pages(pdf_text: str) -> list[str]:
    """Split PDF text into per-page chunks based on '--- Page N ---' markers."""
    parts = re.split(r"--- Page \d+ ---\n?", pdf_text)
    # Filter empty parts
    return [p.strip() for p in parts if p.strip()]


async def _extract_full_document(
    pdf_path: str,
    prompt_context: DocumentPromptContext,
    schema: type[ExtractedFinancialStatement],
    max_pages: int,
) -> Optional[ExtractedFinancialStatement]:
    """Extract full document via single Gemini call with hybrid text+image content.

    Per D-11: sends both pdfplumber text (exact numerics) and page images (visual context).
    Per D-12: one API call for the entire document instead of per-page splitting.
    """
    from ..ai_implementation.pipeline.llm_client import call_llm_vision

    pdf_text = _extract_pdf_text_table(pdf_path, max_pages=max_pages)
    rendered_pages = render_pdf_pages(pdf_path, max_pages=max_pages)

    # Build multimodal content: text first, then all page images
    user_content_parts = []
    user_content_parts.append({
        "type": "text",
        "text": (
            f"Filename: {prompt_context.filename}\n"
            f"Notes: {' | '.join(prompt_context.notes) if prompt_context.notes else 'none'}\n\n"
            f"DOCUMENT TEXT (all pages):\n{pdf_text}\n\n"
            "Extract every detail line item. Do not stop early."
        ),
    })
    for page in rendered_pages:
        if page.content is not None:
            user_content_parts.append({
                "type": "image",
                "data": page.content,
                "mime_type": page.mime_type,
            })

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content_parts},
    ]

    # Higher timeout for full-document processing (was 60s per page)
    return await call_llm_vision(messages, schema, temperature=0.0, timeout=90.0)


async def extract_pdf_statement(
    path: str,
    provider: Optional[VisionStatementExtractor] = None,
    *,
    max_pages: Optional[int] = None,
) -> ExtractedFinancialStatement | DocumentExtractionFailure:
    """Extract a canonical financial statement from a PDF.

    Primary path: pdfplumber text extraction + Gemini hybrid text+image model (single call).
    Falls back to vision path if a custom provider is supplied.
    """
    prompt_context = DocumentPromptContext(
        filename=Path(path).name,
        route_family="pdf_visual_document",
    )

    last_validation_issues: list[dict[str, Any]] = []

    for attempt in range(2):
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
            if attempt == 0:
                prompt_context = deepcopy(prompt_context)
                prompt_context.notes.append(f"Previous attempt failed schema validation: {exc}")
                continue
            return DocumentExtractionFailure(
                code="schema_validation_failed",
                message="Structured PDF extraction could not satisfy the canonical schema.",
                details={"error": str(exc)},
            )
        except Exception as exc:
            return DocumentExtractionFailure(
                code="provider_error",
                message="The vision extraction provider failed before returning a statement.",
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
