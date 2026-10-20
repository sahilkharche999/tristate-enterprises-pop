import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fitz

from app.ai_implementation.pipeline.document_extraction_provider import (
    DocumentPromptContext,
    RenderedPage,
)
from app.models.financial_document_extraction import (
    DocumentExtractionFailure,
    ExtractedFinancialStatement,
    ExtractedFinancialStatementPage,
)
from app.services.financial_statement_validation import validate_extracted_statement
import pytest

from app.services.pdf_vlm_extractor import (
    _PAGE_CLASSIFICATION_SYSTEM_PROMPT,
    _PAGE_SYSTEM_PROMPT_PROFORMA_OPERATING,
    _choose_pages,
    _extract_single_page,
    _extract_full_document,
    _is_scanned_pdf_error,
    PageChoice,
    PageClassification,
    extract_pdf_statement,
)
from app.ai_implementation.pipeline.document_extraction_provider import DocumentPromptContext


def _page(number, page_type="income_statement", *, detail=True, facts=False, heading=None):
    return PageClassification(
        page=number,
        page_type=page_type,
        page_heading=heading,
        year_or_period=None,
        has_detail_rows=detail,
        has_disclosure_facts=facts,
    )


def _choice(pages, source_mode="income_statement", reserve=()):
    return PageChoice(source_mode=source_mode, pages=list(pages), reserve_pages=set(reserve), note="", fact_pages=[])


@pytest.fixture(autouse=True)
def _every_page_is_a_statement(monkeypatch):
    """Tests below exercise extraction, not classification: treat every page
    as an income-statement page unless a test patches _classify_pages itself."""

    async def _classify(path, filename):
        from app.services.pdf_vlm_extractor import _get_pdf_page_count

        return [_page(number) for number in range(1, _get_pdf_page_count(path) + 1)]

    monkeypatch.setattr("app.services.pdf_vlm_extractor._classify_pages", _classify)


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


def _write_old_mill_style_budget_pdf(path):
    doc = fitz.open()
    try:
        page = doc.new_page(width=792, height=612)
        rows = [
            (42, ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Total", "Budget"]),
            (58, ["Income:"]),
            (74, ["Assessments", "$100)", "$100)", "$100)", "$100)", "$100)", "$100)", "$100)", "$100)", "$100)", "$100)", "$100)", "$100)", "Assessments", "90.00%", "$1,200.00)"]),
            (90, ["Parking Garage Assessment Income", "$50)", "$50)", "$50)", "$50)", "$50)", "$50)", "$50)", "$50)", "$50)", "$50)", "$50)", "$50)", "Parking Garage Assessment Income", "10.00%", "$600.00)"]),
            (106, ["TOTAL OPERATING INCOME", "$150)", "$150)", "$150)", "$150)", "$150)", "$150)", "$150)", "$150)", "$150)", "$150)", "$150)", "$150)", "TOTAL OPERATING INCOME", "100.00%", "$1,800.00)"]),
            (130, ["Expense:"]),
            (146, ["Management Service", "$25)", "$25)", "$25)", "$25)", "$25)", "$25)", "$25)", "$25)", "$25)", "$25)", "$25)", "$25)", "Management Service", "50.00%", "$300.00)"]),
            (162, ["Legal & Accounting", "$10)", "$10)", "$10)", "$10)", "$10)", "$10)", "$10)", "$10)", "$10)", "$10)", "$10)", "$10)", "Legal & Accounting", "50.00%", "$120.00)"]),
        ]
        x_positions = [14, 144, 181, 217, 253, 286, 326, 359, 396, 428, 465, 499, 533, 570, 706, 740]
        for y, values in rows:
            for x, value in zip(x_positions, values):
                page.insert_text((x, y), value, fontsize=7)
        doc.save(path)
    finally:
        doc.close()


def test_page_classification_prompt_names_every_page_type():
    prompt = _PAGE_CLASSIFICATION_SYSTEM_PROMPT
    for page_type in ("income_statement", "reserve_income_statement", "operating_budget", "budget_summary",
                      "balance_sheet", "reserve_study", "assessment_schedule", "letter_or_notes"):
        assert page_type in prompt


def test_choose_pages_prefers_the_budget_block_in_a_disclosure_package():
    # 115-page Old Mill 2025 package, as Gemini classified it on 2026-10-20.
    pages = [_page(n, "letter_or_notes", detail=False) for n in range(1, 116)]
    for n in (25, 26, 27, 28, 115):
        pages[n - 1] = _page(n, "budget_summary", detail=n in (25, 27), facts=True)
    for n in (32, 33):
        pages[n - 1] = _page(n, "operating_budget", heading="FORECASTED STATEMENT OF REVENUES")

    choice = _choose_pages(pages)

    assert choice.source_mode == "proforma_final_budget"
    assert choice.pages == [32, 33]
    assert choice.fact_pages == [25, 26, 27, 28, 115]
    assert "pages 32–33 of 115" in choice.note


def test_choose_pages_reads_income_statement_and_reserve_tail_from_financials():
    pages = [_page(1, "balance_sheet"), _page(2, "balance_sheet"), _page(3), _page(4),
             _page(5, "reserve_income_statement")]

    choice = _choose_pages(pages)

    assert choice.source_mode == "income_statement"
    assert choice.pages == [3, 4, 5]
    assert choice.reserve_pages == {5}


def test_choose_pages_returns_none_for_a_reserve_component_list():
    assert _choose_pages([_page(1, "reserve_study")]) is None


def test_choose_pages_keeps_one_budget_block_and_notes_the_other():
    pages = [_page(1, "letter_or_notes", detail=False), _page(2, "operating_budget"),
             _page(3, "operating_budget"), _page(4, "letter_or_notes", detail=False), _page(5, "operating_budget")]

    choice = _choose_pages(pages)

    assert choice.pages == [2, 3]
    assert "Also found statement or budget pages at 5" in choice.note


def test_choose_pages_operator_override_picks_the_kind():
    pages = [_page(1), _page(2)]

    choice = _choose_pages(pages, "proforma_final_budget")

    assert choice.source_mode == "proforma_final_budget"
    assert choice.pages == [1, 2]


def test_extract_single_page_uses_proforma_prompt_family_only_in_proforma_mode(monkeypatch):
    captured_system_prompts: list[str] = []
    captured_timeouts: list[float] = []

    async def _fake_call_llm_vision(messages, schema, temperature=0.0, timeout=120.0, **kwargs):
        captured_system_prompts.append(str(messages[0]["content"]))
        captured_timeouts.append(timeout)
        return ExtractedFinancialStatementPage.model_validate(
            {
                "document_family": "pdf_visual_document",
                "report_type": "income_statement",
                "line_items": [
                    {
                        "label": "Assessments",
                        "section_kind": "income",
                        "annual_budget": 100.0,
                        "page_number": 1,
                    }
                ],
                "totals": [],
                "validation_issues": [],
                "confidence": 0.0,
            }
        )

    monkeypatch.setattr(
        "app.ai_implementation.pipeline.llm_client.call_llm_vision",
        _fake_call_llm_vision,
    )

    proforma_context = DocumentPromptContext(
        filename="budget.pdf",
        route_family="pdf_visual_document",
        source_mode="proforma_final_budget",
    )
    income_context = DocumentPromptContext(
        filename="budget.pdf",
        route_family="pdf_visual_document",
        source_mode="income_statement",
    )

    asyncio.run(
        _extract_single_page(
            1,
            "Income:\nAssessments\nExpense:\nInsurance",
            None,
            proforma_context,
            False,
        )
    )
    asyncio.run(
        _extract_single_page(
            1,
            "Operating Income\nAssessment Income\nOperating Expenses\nInsurance",
            None,
            income_context,
            False,
        )
    )

    assert len(captured_system_prompts) == 2
    assert "pro forma / final budget" in captured_system_prompts[0].lower()
    assert "pro forma / final budget" not in captured_system_prompts[1].lower()
    assert captured_timeouts == [120.0, 120.0]


def test_proforma_prompt_handles_variable_headings_and_column_positions():
    prompt = _PAGE_SYSTEM_PROMPT_PROFORMA_OPERATING.lower()

    assert "do not assume fixed column numbers" in prompt
    assert "column positions vary by template" in prompt
    assert "approved budget" in prompt
    assert "board approved" in prompt
    assert "2025 budget" in prompt
    assert "next year budget" in prompt
    assert "annualized" in prompt
    assert "rightmost annual-like money column" in prompt
    assert "do not collapse the table into one row" in prompt
    assert "minimum expected output is multiple detail rows" in prompt
    assert "returning only the first visible income row is invalid" in prompt
    assert "scan both income and expense sections before finishing" in prompt


def test_proforma_prompt_avoids_first_row_single_item_anchor():
    prompt = _PAGE_SYSTEM_PROMPT_PROFORMA_OPERATING.lower()

    assert '"label":' not in prompt
    assert "never use revenue or expense as section_kind values" in prompt


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


def test_extract_pdf_proforma_mode_returns_failure_when_gemini_returns_one_row(monkeypatch, tmp_path):
    pdf_path = tmp_path / "old-mill-budget.pdf"
    _write_old_mill_style_budget_pdf(pdf_path)

    async def _bad_single_row(*args, **kwargs):
        return ExtractedFinancialStatementPage.model_validate(
            {
                "document_family": "pdf_visual_document",
                "report_type": "income_statement",
                "line_items": [
                    {
                        "label": "Assessments",
                        "section_kind": "income",
                        "annual_budget": 1200.0,
                        "page_number": 1,
                    }
                ],
                "totals": [],
                "validation_issues": [],
                "confidence": 0.0,
            }
        )

    monkeypatch.setattr(
        "app.ai_implementation.pipeline.llm_client.call_llm_vision",
        _bad_single_row,
    )

    result = asyncio.run(
        extract_pdf_statement(str(pdf_path), source_mode="proforma_final_budget")
    )

    assert isinstance(result, DocumentExtractionFailure)
    assert result.code == "schema_validation_failed"


def test_extract_pdf_proforma_schema_retry_note_calls_out_single_row(monkeypatch, tmp_path):
    provider = StubProvider(
        [
            {
                "document_family": "pdf_visual_document",
                "report_type": "income_statement",
                "line_items": [
                    {
                        "label": "Assessments",
                        "section_kind": "income",
                        "annual_budget": 1200.0,
                        "page_number": 1,
                    }
                ],
                "totals": [],
                "validation_issues": [],
                "confidence": 0.0,
            },
            _valid_statement(),
        ]
    )
    monkeypatch.setattr(
        "app.services.pdf_vlm_extractor.render_pdf_pages",
        lambda path, max_pages=None: [RenderedPage(page_number=1, image_path=str(tmp_path / "page-1.png"))],
    )

    result = asyncio.run(
        extract_pdf_statement(
            str(tmp_path / "statement.pdf"),
            provider=provider,
            source_mode="proforma_final_budget",
        )
    )

    assert isinstance(result, ExtractedFinancialStatement)
    assert len(provider.calls) == 2
    assert provider.calls[1]["prompt_context"].notes
    assert "returned only 1 line item" in provider.calls[1]["prompt_context"].notes[-1].lower()
    assert "assessments" in provider.calls[1]["prompt_context"].notes[-1].lower()


def test_extract_pdf_proforma_retry_note_includes_visible_row_hints(monkeypatch, tmp_path):
    pdf_path = tmp_path / "old-mill-budget.pdf"
    _write_old_mill_style_budget_pdf(pdf_path)

    captured_user_prompts: list[str] = []

    async def _fake_call_llm_vision(messages, schema, temperature=0.0, timeout=120.0, **kwargs):
        captured_user_prompts.append(str(messages[1]["content"][0]["text"]))
        if len(captured_user_prompts) == 1:
            return ExtractedFinancialStatementPage.model_validate(
                {
                    "document_family": "pdf_visual_document",
                    "report_type": "income_statement",
                    "line_items": [
                        {
                            "label": "Assessments",
                            "section_kind": "income",
                            "annual_budget": 1200.0,
                            "page_number": 1,
                        }
                    ],
                    "totals": [],
                    "validation_issues": [],
                    "confidence": 0.0,
                }
            )

        return ExtractedFinancialStatementPage.model_validate(
            {
                "document_family": "pdf_visual_document",
                "report_type": "income_statement",
                "line_items": [
                    {
                        "label": "Assessments",
                        "section_kind": "income",
                        "annual_budget": 1200.0,
                        "page_number": 1,
                    },
                    {
                        "label": "Management Service",
                        "section_kind": "operating",
                        "annual_budget": 300.0,
                        "page_number": 1,
                    },
                ],
                "totals": [],
                "validation_issues": [],
                "confidence": 0.0,
            }
        )

    monkeypatch.setattr(
        "app.ai_implementation.pipeline.llm_client.call_llm_vision",
        _fake_call_llm_vision,
    )

    result = asyncio.run(
        extract_pdf_statement(str(pdf_path), source_mode="proforma_final_budget")
    )

    assert isinstance(result, ExtractedFinancialStatement)
    assert len(captured_user_prompts) == 2
    assert "visible labeled money rows include" in captured_user_prompts[1].lower()
    assert "Parking Garage Assessment Income" in captured_user_prompts[1]
    assert "Management Service" in captured_user_prompts[1]


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


def test_validate_statement_rejects_parses_with_no_annual_budget_coverage():
    """Cummings Park regression: a statement that has actual values but no
    annual budget column for any row must be REJECTED, not silently turned
    into a useless draft. The whole point of the upload flow is to suggest
    next year's annual budget — without annual_budget data the draft is
    empty in the only column that matters."""
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "pdf_visual_document",
            "report_type": "income_statement",
            "line_items": [
                {
                    "label": "Bank Interest",
                    "section_kind": "income",
                    "ytd_actual": 1.43,
                    "ytd_budget": None,
                    "annual_budget": None,
                },
                {
                    "label": "Monthly Assessments",
                    "section_kind": "income",
                    "ytd_actual": 30089.94,
                    "ytd_budget": 12840.08,
                    "annual_budget": None,
                },
                {
                    "label": "Building Maintenance",
                    "section_kind": "operating",
                    "ytd_actual": 0.0,
                    "ytd_budget": 208.33,
                    "annual_budget": None,
                },
            ],
            "confidence": 0.9,
        }
    )
    issues = validate_extracted_statement(statement)
    assert any(issue["code"] == "missing_annual_budget_coverage" for issue in issues), (
        "parses with all-null annual_budget must be rejected so the user "
        "doesn't end up with a useless draft"
    )
    # Confirm the new check is severity=error so has_blocking_validation_issues
    # actually returns True for it.
    from app.services.financial_statement_validation import has_blocking_validation_issues
    assert has_blocking_validation_issues(issues)


def test_validate_statement_passes_when_annual_budget_coverage_is_high_enough():
    """A normal text-based PDF with annual_budget populated for every row
    must NOT trip the new annual_budget coverage check."""
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "pdf_visual_document",
            "report_type": "income_statement",
            "line_items": [
                {"label": "Assessment Income", "section_kind": "income", "annual_budget": 150000.0, "ytd_actual": 100000.0},
                {"label": "Management Fee", "section_kind": "operating", "annual_budget": 40000.0, "ytd_actual": 30000.0},
                {"label": "Landscape", "section_kind": "operating", "annual_budget": 25000.0, "ytd_actual": 18000.0},
            ],
            "confidence": 0.9,
        }
    )
    issues = validate_extracted_statement(statement)
    assert not any(issue["code"] == "missing_annual_budget_coverage" for issue in issues)


def test_validate_statement_passes_when_minority_of_rows_lack_annual_budget():
    """If most rows have annual_budget but a few don't, the parse is still
    usable. Only the all-or-nothing case (Cummings Park) should be rejected.
    Threshold is 30% — anything at or above that passes."""
    line_items = []
    # 7 rows WITH annual_budget, 3 rows WITHOUT — coverage = 70% > 30%
    for i in range(7):
        line_items.append(
            {
                "label": f"Has Budget {i}",
                "section_kind": "operating",
                "annual_budget": 1000.0 * (i + 1),
                "ytd_actual": 500.0 * (i + 1),
            }
        )
    for i in range(3):
        line_items.append(
            {
                "label": f"No Budget {i}",
                "section_kind": "operating",
                "annual_budget": None,
                "ytd_actual": 200.0,
            }
        )
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "pdf_visual_document",
            "report_type": "income_statement",
            "line_items": line_items,
            "confidence": 0.9,
        }
    )
    issues = validate_extracted_statement(statement)
    assert not any(issue["code"] == "missing_annual_budget_coverage" for issue in issues)


def test_extract_pdf_derives_local_confidence_instead_of_trusting_model(monkeypatch, tmp_path):
    provider = StubProvider([_valid_statement(confidence=0.0)])
    monkeypatch.setattr(
        "app.services.pdf_vlm_extractor.render_pdf_pages",
        lambda path, max_pages=None: [RenderedPage(page_number=1, image_path=str(tmp_path / "page-1.png"))],
    )

    result = asyncio.run(extract_pdf_statement(str(tmp_path / "statement.pdf"), provider=provider))

    assert isinstance(result, ExtractedFinancialStatement)
    assert result.confidence > 0.0


# ── _is_reserve_statement_page — title pattern coverage ──────────────────────


class TestScannedPdfFallback:
    """Regression coverage for the vision-only fallback path.

    When pdfplumber reports no text layer (e.g. Cummins Park, which is a
    pure raster scan), the hybrid path must fall through to a vision-only
    extraction. Normal text-based PDFs must be unaffected.
    """

    def test_is_scanned_pdf_error_matches_no_text_layer_value_error(self):
        err = ValueError("PDF has no text layer (scanned). Upload text-based PDF or Excel.")
        assert _is_scanned_pdf_error(err) is True

    def test_is_scanned_pdf_error_rejects_unrelated_value_error(self):
        err = ValueError("max_pages must be positive")
        assert _is_scanned_pdf_error(err) is False

    def test_is_scanned_pdf_error_rejects_other_exception_types(self):
        assert _is_scanned_pdf_error(RuntimeError("no text layer")) is False
        assert _is_scanned_pdf_error(TypeError("bad arg")) is False

    def test_extract_full_document_falls_back_to_vision_only_on_scanned_pdf(
        self, monkeypatch, tmp_path
    ):
        """The full-document path must catch the 'no text layer' signal,
        render pages anyway, and invoke _extract_single_page with
        no_text_layer=True and empty text.
        """

        def _raise_no_text_layer(path, selected_pages):
            raise ValueError(
                "PDF has no text layer (scanned). Upload text-based PDF or Excel."
            )

        fake_pages = [
            RenderedPage(page_number=1, mime_type="image/png", content=b"\x89PNG-page-1"),
            RenderedPage(page_number=2, mime_type="image/png", content=b"\x89PNG-page-2"),
        ]

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._get_pdf_page_count",
            lambda path: 2,
        )
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_pdf_text_table_for_pages",
            _raise_no_text_layer,
        )
        render_call_kwargs: list[dict] = []

        def _capture_render(path, page_numbers, *, dpi=72):
            render_call_kwargs.append({"path": path, "pages": page_numbers, "dpi": dpi})
            return fake_pages

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor.render_pdf_pages_for_numbers",
            _capture_render,
        )

        captured_calls: list[dict] = []

        async def _fake_extract_single_page(
            page_num,
            page_text,
            page_image,
            prompt_context,
            is_reserve_page,
            *,
            no_text_layer=False,
        ):
            captured_calls.append(
                {
                    "page_num": page_num,
                    "page_text": page_text,
                    "page_image": page_image,
                    "is_reserve_page": is_reserve_page,
                    "no_text_layer": no_text_layer,
                }
            )
            # _extract_single_page returns a per-page schema with no
            # min_length constraint, so 1 item per stub is valid here.
            return ExtractedFinancialStatementPage.model_validate(
                {
                    "document_family": "pdf_visual_document",
                    "report_type": "income_statement",
                    "line_items": [
                        {
                            "account_code_text": f"4000{page_num}",
                            "label": f"Scanned Item Page {page_num}",
                            "section_kind": "operating",
                            "ytd_actual": 100.0 * page_num,
                            "annual_budget": 200.0 * page_num,
                            "page_number": page_num,
                        }
                    ],
                    "totals": [],
                    "validation_issues": [],
                    "confidence": 0.0,
                }
            )

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_single_page",
            _fake_extract_single_page,
        )

        result = asyncio.run(
            _extract_full_document(
                str(tmp_path / "cummins_park_scanned.pdf"),
                DocumentPromptContext(filename="cummins_park_scanned.pdf", route_family="pdf_visual_document"),
                ExtractedFinancialStatement,
                max_pages=6,
                choice=_choice([1, 2]),
            )
        )

        assert isinstance(result, ExtractedFinancialStatement)
        assert len(captured_calls) == 2, "one call per rendered page"
        for call in captured_calls:
            assert call["no_text_layer"] is True, "fallback must propagate no_text_layer=True"
            assert call["page_text"] == "", "scanned fallback must send empty page_text"
            assert call["is_reserve_page"] is False, "scanned pages default to operating"
            assert call["page_image"] is not None, "rendered image must still be attached"
        assert len(result.line_items) == 2

        # Scanned fallback must render at 200 DPI, not the 72 DPI default —
        # Gemini cannot read fine-print numeric columns at 72 DPI when it
        # has no text block to fall back on. Regression for the Cummins Park
        # "all-null numerics" failure.
        assert len(render_call_kwargs) == 1
        assert render_call_kwargs[0]["dpi"] == 200

    def test_extract_full_document_uses_default_72_dpi_for_hybrid_text_pdf(
        self, monkeypatch, tmp_path
    ):
        """The normal text-based PDF path must keep the 72 DPI default. High
        DPI rendering would needlessly bloat payloads for PDFs whose numerics
        come from the pdfplumber text block anyway.
        """

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_pdf_text_table_for_pages",
            lambda path, selected_pages: "--- Page 1 ---\nLine 1 100\nLine 2 200\n--- Page 2 ---\nLine 3 300\n",
        )
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._get_pdf_page_count",
            lambda path: 2,
        )

        render_call_kwargs: list[dict] = []

        def _capture_render(path, page_numbers, *, dpi=72):
            render_call_kwargs.append({"dpi": dpi})
            return [
                RenderedPage(page_number=1, mime_type="image/png", content=b"p1"),
                RenderedPage(page_number=2, mime_type="image/png", content=b"p2"),
            ]

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor.render_pdf_pages_for_numbers",
            _capture_render,
        )

        async def _fake_extract_single_page(
            page_num, page_text, page_image, prompt_context, is_reserve_page, *, no_text_layer=False,
        ):
            assert no_text_layer is False, "hybrid path must never set no_text_layer=True"
            return ExtractedFinancialStatementPage.model_validate(
                {
                    "document_family": "pdf_visual_document",
                    "report_type": "income_statement",
                    "line_items": [
                        {
                            "label": f"Item {page_num}",
                            "section_kind": "operating",
                            "ytd_actual": 100.0,
                            "annual_budget": 200.0,
                            "page_number": page_num,
                        }
                    ],
                    "totals": [],
                    "validation_issues": [],
                    "confidence": 0.0,
                }
            )

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_single_page",
            _fake_extract_single_page,
        )

        asyncio.run(
            _extract_full_document(
                str(tmp_path / "normal.pdf"),
                DocumentPromptContext(filename="normal.pdf", route_family="pdf_visual_document"),
                ExtractedFinancialStatement,
                max_pages=6,
                choice=_choice([1, 2]),
            )
        )

        assert len(render_call_kwargs) == 1
        assert render_call_kwargs[0]["dpi"] == 72, (
            "text-based PDFs must keep the 72 DPI default — only the scanned "
            "fallback should bump to 200 DPI"
        )

    def test_render_pdf_pages_applies_matrix_for_non_default_dpi(self, monkeypatch):
        """Low-level guarantee: render_pdf_pages must pass a fitz.Matrix
        scale to get_pixmap when dpi != 72, and omit it for the default.
        """
        from app.services import pdf_vlm_extractor

        captured_matrix_calls: list = []

        class _FakePixmap:
            def tobytes(self, fmt):
                return b"fake-png"

        class _FakePage:
            def get_pixmap(self, matrix=None):
                captured_matrix_calls.append(matrix)
                return _FakePixmap()

        class _FakeDoc:
            def __init__(self, pages):
                self._pages = pages

            def __iter__(self):
                return iter(self._pages)

            def close(self):
                pass

        class _FakeFitz:
            Matrix = __import__("types").SimpleNamespace
            # Use a callable that records its args as a tuple-like object so
            # the test can inspect scale values.
            def __init__(self):
                pass

            def open(self, path):
                return _FakeDoc([_FakePage(), _FakePage()])

        fake_fitz_module = type(
            "fake_fitz",
            (),
            {
                "open": staticmethod(lambda p: _FakeDoc([_FakePage(), _FakePage()])),
                "Matrix": lambda x, y: ("matrix", x, y),
            },
        )
        monkeypatch.setitem(__import__("sys").modules, "fitz", fake_fitz_module)

        # Default DPI → no matrix passed
        pdf_vlm_extractor.render_pdf_pages("/tmp/ignored.pdf", max_pages=2)
        assert captured_matrix_calls[-2:] == [None, None]

        captured_matrix_calls.clear()

        # 200 DPI → matrix with scale 200/72 ≈ 2.777…
        pdf_vlm_extractor.render_pdf_pages("/tmp/ignored.pdf", max_pages=2, dpi=200)
        assert len(captured_matrix_calls) == 2
        for m in captured_matrix_calls:
            assert m is not None, "matrix must be passed when dpi != 72"
            assert m[0] == "matrix"
            assert abs(m[1] - 200 / 72) < 1e-6
            assert abs(m[2] - 200 / 72) < 1e-6

    def test_extract_full_document_does_not_fall_back_for_generic_value_error(
        self, monkeypatch, tmp_path
    ):
        """Unrelated ValueErrors from pdfplumber must bubble up, not silently
        engage the vision-only fallback."""

        def _raise_generic(path, selected_pages):
            raise ValueError("max_pages must be positive")

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_pdf_text_table_for_pages",
            _raise_generic,
        )
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._get_pdf_page_count",
            lambda path: 2,
        )

        import pytest

        with pytest.raises(ValueError, match="max_pages must be positive"):
            asyncio.run(
                _extract_full_document(
                    str(tmp_path / "broken.pdf"),
                    DocumentPromptContext(filename="broken.pdf", route_family="pdf_visual_document"),
                    ExtractedFinancialStatement,
                    max_pages=6,
                    choice=_choice([1, 2]),
                )
            )


class TestPageClassificationFlow:
    """Every PDF is classified; only the chosen pages are extracted."""

    def test_large_scanned_pdf_extracts_only_chosen_pages(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.services.pdf_vlm_extractor._get_pdf_page_count", lambda path: 64)

        async def _classify(path, filename):
            pages = [_page(n, "letter_or_notes", detail=False) for n in range(1, 65)]
            pages[27], pages[28] = _page(28), _page(29)
            pages[29] = _page(30, "reserve_income_statement")
            return pages

        monkeypatch.setattr("app.services.pdf_vlm_extractor._classify_pages", _classify)
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_pdf_text_table_for_pages",
            lambda path, selected_pages: (_ for _ in ()).throw(
                ValueError("PDF has no text layer (scanned). Upload text-based PDF or Excel.")
            ),
        )
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor.render_pdf_pages_for_numbers",
            lambda path, page_numbers, *, dpi=72: [
                RenderedPage(page_number=n, mime_type="image/png", content=b"png") for n in page_numbers
            ],
        )
        seen: dict[int, bool] = {}

        async def _fake_extract_single_page(
            page_num, page_text, page_image, prompt_context, is_reserve_page, *, no_text_layer=False,
        ):
            seen[page_num] = is_reserve_page
            return ExtractedFinancialStatementPage.model_validate(
                {
                    "document_family": "pdf_visual_document",
                    "report_type": "income_statement",
                    "line_items": [
                        {"label": f"Row {page_num}", "section_kind": "reserve_expense" if is_reserve_page else "operating",
                         "ytd_actual": 10.0 * page_num, "annual_budget": 20.0 * page_num, "page_number": page_num},
                    ],
                }
            )

        monkeypatch.setattr("app.services.pdf_vlm_extractor._extract_single_page", _fake_extract_single_page)

        result = asyncio.run(extract_pdf_statement(str(tmp_path / "package.pdf")))

        assert isinstance(result, ExtractedFinancialStatement), result
        assert seen == {28: False, 29: False, 30: True}
        assert result.report_type == "income_statement"
        assert result.extraction_metadata["selected_pages"] == [28, 29, 30]
        assert "pages 28–30 of 64" in result.extraction_metadata["pages_note"]

    def test_pdf_without_statement_or_budget_pages_is_not_a_budget_source(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.services.pdf_vlm_extractor._get_pdf_page_count", lambda path: 1)

        async def _classify(path, filename):
            return [_page(1, "reserve_study")]

        monkeypatch.setattr("app.services.pdf_vlm_extractor._classify_pages", _classify)

        result = asyncio.run(extract_pdf_statement(str(tmp_path / "component-list.pdf")))

        assert isinstance(result, DocumentExtractionFailure)
        assert result.code == "not_a_budget_source"
        assert "reserve study p1" in result.message

    def test_pdf_longer_than_the_limit_is_refused_before_any_gemini_call(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.services.pdf_vlm_extractor._get_pdf_page_count", lambda path: 301)

        async def _classify(path, filename):
            raise AssertionError("must not classify")

        monkeypatch.setattr("app.services.pdf_vlm_extractor._classify_pages", _classify)

        result = asyncio.run(extract_pdf_statement(str(tmp_path / "huge.pdf")))

        assert result.code == "pdf_too_long"
