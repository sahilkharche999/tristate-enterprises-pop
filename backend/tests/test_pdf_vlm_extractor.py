import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai_implementation.pipeline.document_extraction_provider import (
    DocumentPromptContext,
    RenderedPage,
)
from app.models.financial_document_extraction import (
    DocumentExtractionFailure,
    ExtractedFinancialStatement,
)
from app.services.financial_statement_validation import validate_extracted_statement
from app.services.pdf_vlm_extractor import extract_pdf_statement


class StubProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def extract_statement(self, pages, *, schema, prompt_context):
        self.calls.append(
            {
                "pages": pages,
                "schema": schema,
                "prompt_context": prompt_context,
            }
        )
        return self.responses.pop(0)


def _valid_statement(*, family="pdf_visual_document", confidence=0.9):
    return {
        "document_family": family,
        "report_type": "income_statement",
        "line_items": [
            {
                "account_code_text": "40000",
                "label": "Assessment Income",
                "section_kind": "income",
                "ytd_actual": 125000.0,
                "annual_budget": 150000.0,
                "page_number": 1,
            },
            {
                "account_code_text": "50050",
                "label": "Management Fee",
                "section_kind": "operating",
                "ytd_actual": 32000.0,
                "annual_budget": 40000.0,
                "page_number": 1,
            },
        ],
        "totals": [],
        "validation_issues": [],
        "confidence": confidence,
    }


def _all_zero_statement():
    return {
        "document_family": "pdf_visual_document",
        "report_type": "income_statement",
        "line_items": [
            {
                "account_code_text": "40000",
                "label": "Assessment Income",
                "section_kind": "income",
                "ytd_actual": 0.0,
                "annual_budget": 0.0,
                "page_number": 1,
            },
            {
                "account_code_text": "50050",
                "label": "Management Fee",
                "section_kind": "operating",
                "ytd_actual": 0.0,
                "annual_budget": 0.0,
                "page_number": 1,
            },
        ],
        "totals": [],
        "validation_issues": [],
        "confidence": 0.65,
    }


def test_extract_pdf_renders_pages_before_provider_call(monkeypatch, tmp_path):
    provider = StubProvider([_valid_statement()])
    pages = [RenderedPage(page_number=1, image_path=str(tmp_path / "page-1.png"))]

    monkeypatch.setattr(
        "app.services.pdf_vlm_extractor.render_pdf_pages",
        lambda path, max_pages=None: pages,
    )

    result = asyncio.run(extract_pdf_statement(str(tmp_path / "statement.pdf"), provider=provider))

    assert isinstance(result, ExtractedFinancialStatement)
    assert provider.calls
    assert provider.calls[0]["pages"] == pages
    assert provider.calls[0]["schema"] is ExtractedFinancialStatement


def test_extract_pdf_uses_canonical_schema_validation(monkeypatch, tmp_path):
    provider = StubProvider(
        [
            {
                "document_family": "pdf_visual_document",
                "report_type": "income_statement",
                "line_items": [{"label": ""}],
                "confidence": 0.5,
            },
            {
                "document_family": "pdf_visual_document",
                "report_type": "income_statement",
                "line_items": [{"label": ""}],
                "confidence": 0.5,
            },
        ]
    )
    monkeypatch.setattr(
        "app.services.pdf_vlm_extractor.render_pdf_pages",
        lambda path, max_pages=None: [RenderedPage(page_number=1, image_path=str(tmp_path / "page-1.png"))],
    )

    result = asyncio.run(extract_pdf_statement(str(tmp_path / "statement.pdf"), provider=provider))

    assert isinstance(result, DocumentExtractionFailure)
    assert result.code == "schema_validation_failed"


def test_extract_pdf_retries_once_after_validation_feedback(monkeypatch, tmp_path):
    provider = StubProvider([_all_zero_statement(), _valid_statement()])
    monkeypatch.setattr(
        "app.services.pdf_vlm_extractor.render_pdf_pages",
        lambda path, max_pages=None: [RenderedPage(page_number=1, image_path=str(tmp_path / "page-1.png"))],
    )

    result = asyncio.run(extract_pdf_statement(str(tmp_path / "statement.pdf"), provider=provider))

    assert isinstance(result, ExtractedFinancialStatement)
    assert len(provider.calls) == 2
    assert provider.calls[1]["prompt_context"].notes
    assert result.confidence > 0.0


def test_extract_pdf_returns_failure_on_second_validation_error(monkeypatch, tmp_path):
    provider = StubProvider([_all_zero_statement(), _all_zero_statement()])
    monkeypatch.setattr(
        "app.services.pdf_vlm_extractor.render_pdf_pages",
        lambda path, max_pages=None: [RenderedPage(page_number=1, image_path=str(tmp_path / "page-1.png"))],
    )

    result = asyncio.run(extract_pdf_statement(str(tmp_path / "statement.pdf"), provider=provider))

    assert isinstance(result, DocumentExtractionFailure)
    assert result.code == "validation_failed"


def test_validate_statement_flags_missing_required_numeric_coverage():
    issues = validate_extracted_statement(ExtractedFinancialStatement.model_validate(_all_zero_statement()))
    assert any(issue["code"] == "missing_numeric_coverage" for issue in issues)


def test_validate_statement_flags_duplicate_line_items():
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "pdf_visual_document",
            "report_type": "income_statement",
            "line_items": [
                {"account_code_text": "50050", "label": "Management Fee", "annual_budget": 40000.0},
                {"account_code_text": "50050", "label": "Management Fee", "annual_budget": 40000.0},
            ],
            "confidence": 0.8,
        }
    )
    issues = validate_extracted_statement(statement)
    assert any(issue["code"] == "duplicate_line_item" for issue in issues)


def test_validate_statement_uses_subtotal_reconciliation_when_totals_exist():
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "pdf_visual_document",
            "report_type": "income_statement",
            "line_items": [
                {"label": "Assessment Income", "section_kind": "income", "annual_budget": 100.0},
                {"label": "Late Fee Income", "section_kind": "income", "annual_budget": 25.0},
            ],
            "totals": [{"section_kind": "income", "amount": 200.0}],
            "confidence": 0.9,
        }
    )
    issues = validate_extracted_statement(statement)
    assert any(issue["code"] == "subtotal_mismatch" for issue in issues)


def test_extract_pdf_accepts_scanned_or_text_pdf_through_same_vision_path(monkeypatch, tmp_path):
    provider = StubProvider([_valid_statement(), _valid_statement()])
    monkeypatch.setattr(
        "app.services.pdf_vlm_extractor.render_pdf_pages",
        lambda path, max_pages=None: [RenderedPage(page_number=1, image_path=str(tmp_path / (os.path.basename(path) + ".png")))],
    )

    scanned = asyncio.run(extract_pdf_statement(str(tmp_path / "scanned.pdf"), provider=provider))
    text_pdf = asyncio.run(extract_pdf_statement(str(tmp_path / "text.pdf"), provider=provider))

    assert isinstance(scanned, ExtractedFinancialStatement)
    assert isinstance(text_pdf, ExtractedFinancialStatement)
    assert len(provider.calls) == 2


def test_validate_statement_can_be_reused_by_deterministic_excel_outputs():
    statement = ExtractedFinancialStatement.model_validate(_valid_statement(family="known_clean_excel_workbook"))
    issues = validate_extracted_statement(statement)
    assert issues == []


def test_extract_pdf_derives_local_confidence_instead_of_trusting_model(monkeypatch, tmp_path):
    provider = StubProvider([_valid_statement(confidence=0.0)])
    monkeypatch.setattr(
        "app.services.pdf_vlm_extractor.render_pdf_pages",
        lambda path, max_pages=None: [RenderedPage(page_number=1, image_path=str(tmp_path / "page-1.png"))],
    )

    result = asyncio.run(extract_pdf_statement(str(tmp_path / "statement.pdf"), provider=provider))

    assert isinstance(result, ExtractedFinancialStatement)
    assert result.confidence > 0.0
