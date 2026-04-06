import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings
from app.ai_implementation.pipeline.document_extraction_provider import (
    DocumentPromptContext,
    RenderedPage,
    VisionStatementExtractor,
)
from app.models.financial_document_extraction import (
    ExtractedFinancialStatement,
    ExtractedStatementLineItem,
)
from app.services.financial_document_router import choose_financial_document_route


def test_route_xlsx_to_excel_deterministic():
    route = choose_financial_document_route("hoa-income-statement.xlsx")
    assert route.path == "excel_deterministic"
    assert route.family == "known_clean_excel_workbook"
    assert route.requires_review_on_low_confidence is True


def test_route_xls_to_excel_deterministic():
    route = choose_financial_document_route("legacy-income-statement.xls")
    assert route.path == "excel_deterministic"
    assert route.family == "known_clean_excel_workbook"


def test_route_shifted_header_xlsx_stays_deterministic_first():
    route = choose_financial_document_route("vendor-shifted-header-income.xlsx")
    assert route.path == "excel_deterministic"
    assert route.family == "variant_excel_workbook"
    assert route.requires_review_on_low_confidence is True


def test_route_pdf_to_vlm_first():
    route = choose_financial_document_route("income-statement.pdf")
    assert route.path == "pdf_vlm"
    assert route.family == "pdf_visual_document"


def test_route_unknown_layout_prefers_vlm_safe_path():
    route = choose_financial_document_route(
        "mystery-upload.bin",
        content_type="application/octet-stream",
    )
    assert route.path == "pdf_vlm"
    assert route.is_supported is False
    assert route.requires_review_on_low_confidence is True


def test_extracted_statement_schema_requires_line_item_label():
    with pytest.raises(ValidationError):
        ExtractedStatementLineItem(label="")


def test_extracted_statement_schema_accepts_budget_fields():
    statement = ExtractedFinancialStatement(
        document_family="known_clean_excel_workbook",
        report_type="income_statement",
        line_items=[
            {
                "account_code_text": "40000",
                "label": "Assessment Income",
                "current_actual": 1000.0,
                "ytd_actual": 12000.0,
                "annual_budget": 15000.0,
                "page_number": 1,
                "evidence": {"row": 12},
            },
            {
                "account_code_text": "50100",
                "label": "Management Fee",
                "section_kind": "operating",
                "ytd_actual": 18000.0,
                "annual_budget": 24000.0,
                "page_number": 1,
            },
        ],
        confidence=0.88,
    )
    assert statement.line_items[0].label == "Assessment Income"
    assert statement.line_items[0].annual_budget == 15000.0
    assert statement.line_items[0].evidence == {"row": 12}


def test_provider_interface_is_importable_without_groq_specifics():
    assert VisionStatementExtractor is not None
    page = RenderedPage(page_number=1, image_path="/tmp/page-1.png")
    context = DocumentPromptContext(filename="statement.pdf", route_family="pdf_visual_document")
    assert page.page_number == 1
    assert context.filename == "statement.pdf"


def test_config_exposes_separate_document_vlm_settings():
    assert hasattr(settings, "DOCUMENT_VLM_PROVIDER")
    assert hasattr(settings, "DOCUMENT_VLM_MODEL")
    assert hasattr(settings, "DOCUMENT_VLM_ENABLED")
    assert hasattr(settings, "DOCUMENT_VLM_MAX_PAGES")
