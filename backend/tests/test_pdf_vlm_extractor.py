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
    ExtractedFinancialStatementPage,
)
from app.services.financial_statement_validation import validate_extracted_statement
from app.services.pdf_vlm_extractor import (
    _extract_full_document,
    _is_reserve_statement_page,
    _is_scanned_pdf_error,
    StatementPageCandidate,
    StatementPageSelection,
    extract_pdf_statement,
)
from app.ai_implementation.pipeline.document_extraction_provider import DocumentPromptContext


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


class TestIsReserveStatementPage:
    """Regression coverage for reserve-page detection across real-world title variants.

    When a reserve page is misclassified as operating, the operating prompt
    dutifully re-categorizes reserve items as 'income'/'operating', inflating
    the operating INCOME total with reserve contributions and losing the
    reserve-expense rows. Every title variant that ever shipped in a real HOA
    PDF should have a positive case here.
    """

    # ── positive cases — these pages SHOULD be detected as reserve ──

    def test_esprit_park_reserve_income_and_expense_to_budget(self):
        """Regression: Esprit Park's page 3 title was silently treated as operating."""
        page_text = (
            "Esprit Park HOA\n"
            "Reserve Income and Expense to Budget\n"
            "For the Nine Months Ending September 30, 2024\n"
            "\n"
            "INCOME\n"
            "Reserve Income ...\n"
            "Interest Earned Reserve ...\n"
        )
        assert _is_reserve_statement_page(page_text) is True

    def test_reserve_fund_activity_title(self):
        page_text = "Crestview HOA\nReserve Fund Activity\nFiscal Year 2024\n\n"
        assert _is_reserve_statement_page(page_text) is True

    def test_reserve_fund_statement_title(self):
        page_text = "Palo Alto Redwoods\nReserve Fund Statement\nJanuary 31, 2025\n"
        assert _is_reserve_statement_page(page_text) is True

    def test_reserve_statement_title(self):
        page_text = "Reserve Statement\nAs of 12/31/2024\n"
        assert _is_reserve_statement_page(page_text) is True

    def test_reserve_funding_schedule_title(self):
        page_text = "Reserve Funding Schedule\n2025 Fiscal Year\n"
        assert _is_reserve_statement_page(page_text) is True

    def test_reserve_study_summary_title(self):
        page_text = "Reserve Study Summary\nComponent Analysis\n"
        assert _is_reserve_statement_page(page_text) is True

    def test_reserve_budget_comparison_title(self):
        page_text = "Reserve Budget vs Actual\nYear to Date\n"
        assert _is_reserve_statement_page(page_text) is True

    def test_legacy_income_statement_reserve_header(self):
        """Backward compatibility with the original header-style detection."""
        page_text = "Mathilda HOA\nIncome Statement\nReserve\n\n"
        assert _is_reserve_statement_page(page_text) is True

    # ── negative cases — these pages must NOT be detected as reserve ──

    def test_operating_income_statement_is_not_reserve(self):
        page_text = "Floribunda HOA\nOperating Income Statement\nFor the Nine Months\n"
        assert _is_reserve_statement_page(page_text) is False

    def test_operating_statement_with_allocation_to_reserves_section(self):
        """An operating statement that happens to mention reserves in a section
        header (e.g. 'Allocation to Reserves') must stay classified as operating."""
        page_text = (
            "Crestview HOA\n"
            "Operating Income and Expense\n"
            "ASSESSMENT INCOME\n"
            "Regular Assessments ...\n"
            "ALLOCATION TO RESERVES\n"
            "Reserve - Allocation/Transfer ...\n"
        )
        assert _is_reserve_statement_page(page_text) is False

    def test_operating_expense_statement_is_not_reserve(self):
        page_text = "HOA Name\nOperating Expense Statement\n"
        assert _is_reserve_statement_page(page_text) is False

    def test_generic_income_statement_without_reserve_keyword_is_not_reserve(self):
        page_text = "HOA Name\nIncome Statement\nASSESSMENT INCOME\n"
        assert _is_reserve_statement_page(page_text) is False

    def test_empty_page_is_not_reserve(self):
        assert _is_reserve_statement_page("") is False

    def test_page_mentioning_reserve_deep_in_body_is_not_reserve(self):
        """The 'reserve' mention is past the header window (first 500 chars)."""
        body = "Operating Income Statement\n" + ("Regular Assessments line\n" * 30) + "Reserve Income footnote"
        assert _is_reserve_statement_page(body) is False


# ── Scanned-PDF vision-only fallback ─────────────────────────────────────────


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

        def _raise_no_text_layer(path, max_pages=None):
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
            "app.services.pdf_vlm_extractor._extract_pdf_text_table",
            _raise_no_text_layer,
        )
        render_call_kwargs: list[dict] = []

        def _capture_render(path, max_pages=None, *, dpi=72):
            render_call_kwargs.append({"path": path, "max_pages": max_pages, "dpi": dpi})
            return fake_pages

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor.render_pdf_pages",
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
            "app.services.pdf_vlm_extractor._extract_pdf_text_table",
            lambda path, max_pages=None: "--- Page 1 ---\nLine 1 100\nLine 2 200\n--- Page 2 ---\nLine 3 300\n",
        )
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._get_pdf_page_count",
            lambda path: 2,
        )

        render_call_kwargs: list[dict] = []

        def _capture_render(path, max_pages=None, *, dpi=72):
            render_call_kwargs.append({"dpi": dpi})
            return [
                RenderedPage(page_number=1, mime_type="image/png", content=b"p1"),
                RenderedPage(page_number=2, mime_type="image/png", content=b"p2"),
            ]

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor.render_pdf_pages",
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

        def _raise_generic(path, max_pages=None):
            raise ValueError("max_pages must be positive")

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_pdf_text_table",
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
                )
            )


class TestIncomeStatementPageSelection:
    """Large mixed PDF packages need page selection before detail extraction."""

    def test_small_pdf_skips_page_selection(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._get_pdf_page_count",
            lambda path: 10,
        )
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_pdf_text_table",
            lambda path, max_pages=None: "--- Page 1 ---\nIncome Statement\nAssessment Income 100\n",
        )
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor.render_pdf_pages",
            lambda path, max_pages=None, *, dpi=72: [
                RenderedPage(page_number=1, mime_type="image/png", content=b"page-1")
            ],
        )

        async def _selector_should_not_run(*args, **kwargs):
            raise AssertionError("small PDFs must not run page selection")

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._select_income_statement_pages",
            _selector_should_not_run,
        )

        async def _fake_extract_single_page(
            page_num, page_text, page_image, prompt_context, is_reserve_page, *, no_text_layer=False,
        ):
            assert page_num == 1
            assert page_text
            return ExtractedFinancialStatementPage.model_validate(
                {
                    "document_family": "pdf_visual_document",
                    "report_type": "income_statement",
                    "line_items": [
                        {
                            "label": "Assessment Income",
                            "section_kind": "income",
                            "ytd_actual": 100.0,
                            "annual_budget": 200.0,
                            "page_number": page_num,
                        },
                        {
                            "label": "Management Fee",
                            "section_kind": "operating",
                            "ytd_actual": 50.0,
                            "annual_budget": 100.0,
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
                str(tmp_path / "small.pdf"),
                DocumentPromptContext(filename="small.pdf", route_family="pdf_visual_document"),
                ExtractedFinancialStatement,
                max_pages=6,
            )
        )

        assert isinstance(result, ExtractedFinancialStatement)
        assert result.extraction_metadata.get("page_selection_used") is False

    def test_large_scanned_pdf_extracts_only_selected_original_pages(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._get_pdf_page_count",
            lambda path: 64,
        )

        def _raise_no_text_layer(path, max_pages=None):
            raise ValueError(
                "PDF has no text layer (scanned). Upload text-based PDF or Excel."
            )

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_pdf_text_table",
            _raise_no_text_layer,
        )
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_pdf_text_table_for_pages",
            lambda path, selected_pages: _raise_no_text_layer(path),
        )
        selection = StatementPageSelection(
            selected_pages=[28, 29, 30],
            candidates=[
                StatementPageCandidate(
                    page=28,
                    classification="operating_statement",
                    selected=True,
                    confidence=0.96,
                    reason="Statement of Revenues and Expenses with Annual Budget column",
                ),
                StatementPageCandidate(
                    page=29,
                    classification="operating_statement",
                    selected=True,
                    confidence=0.95,
                    reason="Continued operating statement with Annual Budget column",
                ),
                StatementPageCandidate(
                    page=30,
                    classification="reserve_statement",
                    selected=True,
                    confidence=0.94,
                    reason="Reserve income and expense page from the same statement package",
                ),
            ],
            rejected_pages=[
                StatementPageCandidate(
                    page=24,
                    classification="assessment_schedule",
                    selected=False,
                    confidence=0.98,
                    reason="Assessments per unit, not an income statement",
                )
            ],
        )

        async def _fake_select(path, prompt_context, *, max_pages):
            assert max_pages == 64
            return selection

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._select_income_statement_pages",
            _fake_select,
        )
        rendered_selected_pages = [
            RenderedPage(page_number=28, mime_type="image/png", content=b"page-28"),
            RenderedPage(page_number=29, mime_type="image/png", content=b"page-29"),
            RenderedPage(page_number=30, mime_type="image/png", content=b"page-30"),
        ]

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor.render_pdf_pages_for_numbers",
            lambda path, page_numbers, *, dpi=72: rendered_selected_pages,
        )

        captured_page_numbers: list[int] = []
        captured_reserve_flags: dict[int, bool] = {}

        async def _fake_extract_single_page(
            page_num, page_text, page_image, prompt_context, is_reserve_page, *, no_text_layer=False,
        ):
            captured_page_numbers.append(page_num)
            captured_reserve_flags[page_num] = is_reserve_page
            assert no_text_layer is True
            assert page_text == ""
            assert page_image.page_number == page_num
            return ExtractedFinancialStatementPage.model_validate(
                {
                    "document_family": "pdf_visual_document",
                    "report_type": "income_statement",
                    "line_items": [
                        {
                            "account_code_text": f"4{page_num}",
                            "label": f"Selected Page {page_num}",
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

        result = asyncio.run(
            _extract_full_document(
                str(tmp_path / "large-package.pdf"),
                DocumentPromptContext(filename="large-package.pdf", route_family="pdf_visual_document"),
                ExtractedFinancialStatement,
                max_pages=6,
            )
        )

        assert isinstance(result, ExtractedFinancialStatement)
        assert captured_page_numbers == [28, 29, 30]
        assert captured_reserve_flags == {28: False, 29: False, 30: True}
        assert [item.page_number for item in result.line_items] == [28, 29, 30]
        assert result.extraction_metadata["page_selection_used"] is True
        assert result.extraction_metadata["selected_pages"] == [28, 29, 30]

    def test_large_pdf_with_no_selected_statement_pages_returns_clear_failure(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._get_pdf_page_count",
            lambda path: 64,
        )
        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._extract_pdf_text_table",
            lambda path, max_pages=None: "--- Page 1 ---\nCover Page\n",
        )

        async def _fake_select(path, prompt_context, *, max_pages):
            return StatementPageSelection(
                selected_pages=[],
                candidates=[],
                rejected_pages=[
                    StatementPageCandidate(
                        page=1,
                        classification="cover_page",
                        selected=False,
                        confidence=0.9,
                        reason="Cover page",
                    )
                ],
            )

        monkeypatch.setattr(
            "app.services.pdf_vlm_extractor._select_income_statement_pages",
            _fake_select,
        )

        result = asyncio.run(extract_pdf_statement(str(tmp_path / "large-package.pdf")))

        assert isinstance(result, DocumentExtractionFailure)
        assert result.code == "statement_pages_not_found"
        assert "No income statement pages" in result.message
