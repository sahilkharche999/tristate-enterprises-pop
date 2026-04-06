"""Adapters from canonical extracted statements into workbook-shaped artifacts."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from openpyxl import Workbook

from ..models.financial_document_extraction import ExtractedFinancialStatement


def build_normalized_statement_workbook(
    statement: ExtractedFinancialStatement,
    output_path: Optional[str] = None,
) -> str:
    """Write a normalized workbook that downstream budget steps can consume."""

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

    for item in statement.line_items:
        worksheet.append(
            [
                item.section_label or item.section_kind,
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
