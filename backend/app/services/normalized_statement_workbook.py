"""Adapters from canonical extracted statements into workbook-shaped artifacts."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from openpyxl import Workbook

from ..models.financial_document_extraction import ExtractedFinancialStatement


# Maps Gemini's section_kind values to header strings that the downstream
# parser's _match_section_header() recognizes via prefix matching against
# _SECTION_TRANSITIONS in income_statement_parser.py.
# Gemini is instructed to return exactly these 4 section_kind values.
_SECTION_HEADER_LABELS = {
    "income":          "Operating Income",      # matches "operating income" prefix
    "operating":       "Operating Expenses",    # matches "operating expense" prefix
    "reserve_income":  "Reserve Contribution",  # matches "reserve contribution" prefix
    "reserve_expense": "Reserve Expenses",      # matches "reserve expenses" prefix
}

# Preferred display order for sections in the workbook
_SECTION_ORDER = ["income", "operating", "reserve_income", "reserve_expense"]


def build_normalized_statement_workbook(
    statement: ExtractedFinancialStatement,
    output_path: Optional[str] = None,
) -> str:
    """Write a normalized workbook that downstream budget steps can consume.

    Groups line items by section_kind and inserts recognized section header
    rows between groups. The header rows have text in col A and empty col B
    which matches the downstream parser's state-machine pattern for section
    transitions, ensuring each line item is classified into the correct
    section (income / operating / reserve_income / reserve_expense).
    """

    target_path = output_path
    if target_path is None:
        fd, target_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Income Statement"
    worksheet.append(
        [
            "Section",
            "Account Code",
            "Label",
            "Current Actual",
            "Current Budget",
            "Current Variance",
            "YTD Actual",
            "YTD Budget",
            "YTD Variance",
            "Annual Budget",
        ]
    )

    # Group items by section_kind, defaulting unknown/null to "operating"
    grouped: dict[str, list] = {}
    for item in statement.line_items:
        section_kind = item.section_kind if item.section_kind in _SECTION_HEADER_LABELS else "operating"
        grouped.setdefault(section_kind, []).append(item)

    # Write sections in defined order so Operating Income appears before
    # Operating Expenses, Reserve Contributions, then Reserve Expenses.
    ordered_sections = [s for s in _SECTION_ORDER if s in grouped]

    for section_kind in ordered_sections:
        items = grouped[section_kind]
        header_label = _SECTION_HEADER_LABELS[section_kind]

        # Section header row: col A has the recognized header, col B is empty.
        # This matches the parser's `col_a AND NOT col_b` section-header pattern.
        worksheet.append([header_label, None, None, None, None, None, None, None, None, None])

        for item in items:
            # CRITICAL: Write the bucket's canonical section_kind (one of 4 SECTION_KINDS)
            # into col A, NOT item.section_label (Gemini's free-text like
            # "ASSESSMENT INCOME"). Downstream `_infer_category` recognizes canonical
            # values directly and skips prefix matching; free-text labels like
            # "ASSESSMENT INCOME" do not match the parser's narrow prefix list and
            # fall through to account-code fallback → all rows mis-classified as
            # "operating". Using the loop variable keeps the row's category
            # consistent with its bucket under all conditions.
            worksheet.append(
                [
                    section_kind,
                    item.account_code_text,
                    item.label,
                    item.current_actual,
                    item.current_budget,
                    item.current_variance,
                    item.ytd_actual,
                    item.ytd_budget,
                    item.ytd_variance,
                    item.annual_budget,
                ]
            )

    workbook.save(target_path)
    workbook.close()
    return str(Path(target_path))
