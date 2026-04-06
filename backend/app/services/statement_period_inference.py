"""Shared helpers for inferring statement period and growth factor from PDFs."""
from __future__ import annotations

from typing import Optional

from ..generate_budget import (
    _elapsed_months_from_fiscal_start,
    _extract_month_from_text,
    _months_covered_from_date_tokens,
)


def infer_growth_factor_from_statement_period(
    statement_period: str | None,
    fiscal_year_start_month: int,
) -> tuple[float, int, str] | None:
    if not statement_period:
        return None

    covered = _months_covered_from_date_tokens(statement_period)
    month = _extract_month_from_text(statement_period)
    if month:
        detected_months = _elapsed_months_from_fiscal_start(month, fiscal_year_start_month)
        return 12.0 / detected_months, detected_months, "statement_period"
    if covered and covered > 1:
        return 12.0 / covered, covered, "statement_period"
    return None


def select_statement_period_hint(text: Optional[str]) -> str | None:
    if not text:
        return None

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    window = lines[:8]

    for line in window:
        covered = _months_covered_from_date_tokens(line)
        if covered and covered > 1:
            return line

    for line in window:
        if _extract_month_from_text(line):
            return line
    return None


def extract_pdf_statement_period_hint(path: str) -> str | None:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return None

    try:
        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                return None
            text = (pdf.pages[0].extract_text() or "").strip()
    except Exception:
        return None

    return select_statement_period_hint(text)
