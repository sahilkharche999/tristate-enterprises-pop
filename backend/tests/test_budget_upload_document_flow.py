from pathlib import Path

from app.models.financial_document_extraction import DocumentExtractionFailure, ExtractedFinancialStatement


def _upload_document(client, filename: str, content_type: str):
    return client.post(
        "/hoa/1/budget/upload",
        files={"file": (filename, b"placeholder bytes", content_type)},
    )


def _valid_pdf_statement() -> ExtractedFinancialStatement:
    return ExtractedFinancialStatement.model_validate(
        {
            "document_family": "pdf_visual_document",
            "report_type": "income_statement",
            "line_items": [
                {
                    "account_code_text": "40000",
                    "label": "Assessment Income",
                    "section_kind": "income",
                    "ytd_actual": 120000.0,
                    "annual_budget": 150000.0,
                    "page_number": 1,
                },
                {
                    "account_code_text": "50100-00",
                    "label": "Management Fee",
                    "section_kind": "operating",
                    "ytd_actual": 18000.0,
                    "annual_budget": 24000.0,
                    "page_number": 1,
                },
            ],
            "confidence": 0.9,
        }
    )


def test_upload_known_clean_excel_stays_deterministic(client, budget_history_test_harness, monkeypatch):
    called = {"pdf": 0}

    def _unexpected_pdf(*args, **kwargs):
        called["pdf"] += 1
        raise AssertionError("PDF extractor should not run for known clean Excel")

    monkeypatch.setattr("app.services.budget_history_service.extract_pdf_statement", _unexpected_pdf)

    response = _upload_document(
        client,
        "income-statement.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_required"] is False
    assert payload["draft"]["status"] == "active"
    assert called["pdf"] == 0


def test_upload_pdf_uses_vlm_extractor_before_pipeline(client, budget_history_test_harness, monkeypatch, tmp_path):
    called = {"pdf": 0, "normalize": 0}

    async def _fake_extract(path: str):
        called["pdf"] += 1
        return _valid_pdf_statement()

    def _fake_normalize(statement):
        called["normalize"] += 1
        output_path = tmp_path / "normalized.pdf.xlsx"
        output_path.write_bytes(b"normalized workbook")
        return str(output_path)

    monkeypatch.setattr("app.services.budget_history_service.extract_pdf_statement", _fake_extract)
    monkeypatch.setattr("app.services.budget_history_service.build_normalized_statement_workbook", _fake_normalize)

    response = _upload_document(client, "income-statement.pdf", "application/pdf")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_required"] is False
    assert payload["draft"]["status"] == "active"
    assert called["pdf"] == 1
    assert called["normalize"] == 1


def test_upload_pdf_uses_pdf_header_hint_when_vlm_omits_statement_period(
    client,
    budget_history_test_harness,
    monkeypatch,
    tmp_path,
):
    async def _fake_extract(path: str):
        return ExtractedFinancialStatement.model_validate(
            {
                "document_family": "pdf_visual_document",
                "report_type": "income_statement",
                "statement_period": None,
                "line_items": [
                    {
                        "account_code_text": "30000-00",
                        "label": "Member Assessments",
                        "section_kind": "income",
                        "ytd_actual": 327695.22,
                        "annual_budget": 436932.0,
                        "page_number": 1,
                    },
                    {
                        "account_code_text": "50100-00",
                        "label": "Management Fee",
                        "section_kind": "operating",
                        "ytd_actual": 18000.0,
                        "annual_budget": 24000.0,
                        "page_number": 1,
                    },
                ],
                "totals": [],
                "validation_issues": [],
                "confidence": 0.9,
            }
        )

    def _fake_normalize(statement):
        output_path = tmp_path / "normalized.pdf.xlsx"
        output_path.write_bytes(b"normalized workbook")
        return str(output_path)

    def _unexpected_infer_growth_factor(*args, **kwargs):
        raise AssertionError("workbook inference should not run when PDF header hint is available")

    monkeypatch.setattr("app.services.budget_history_service.extract_pdf_statement", _fake_extract)
    monkeypatch.setattr("app.services.budget_history_service.build_normalized_statement_workbook", _fake_normalize)
    monkeypatch.setattr(
        "app.services.budget_history_service.extract_pdf_statement_period_hint",
        lambda path: "08/21/2025 to 09/20/2025 Page: 1",
    )
    monkeypatch.setattr("app.services.budget_history_service.infer_growth_factor_from_input", _unexpected_infer_growth_factor)

    response = _upload_document(client, "2238 Market.pdf", "application/pdf")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_required"] is False
    assert payload["draft"]["status"] == "active"


def test_upload_pdf_validation_failure_returns_review_required(client, budget_history_test_harness, monkeypatch):
    async def _failed_extract(path: str):
        return DocumentExtractionFailure(
            code="validation_failed",
            message="Structured PDF extraction failed deterministic validation checks.",
            details={},
        )

    monkeypatch.setattr("app.services.budget_history_service.extract_pdf_statement", _failed_extract)

    response = _upload_document(client, "income-statement.pdf", "application/pdf")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_required"] is True
    assert payload["draft"] is None
    assert "failed deterministic validation" in payload["review_reason"].lower()


def test_variant_excel_low_confidence_returns_review_required(client, budget_history_test_harness, monkeypatch):
    def _low_confidence_line_items(table):
        return (
            [
                {
                    "line_item_key": "40000",
                    "account_code": 40000,
                    "label": "Assessment Income",
                    "category": "income",
                    "ytd_actual": 0.0,
                    "annual_budget": 0.0,
                    "projection": 0.0,
                    "percent_change": 0.0,
                    "read_only": False,
                    "raw": {"section": "Operating Income"},
                },
                {
                    "line_item_key": "50100",
                    "account_code": 50100,
                    "label": "Management Fee",
                    "category": "operating",
                    "ytd_actual": 0.0,
                    "annual_budget": 0.0,
                    "projection": 0.0,
                    "percent_change": 0.0,
                    "read_only": False,
                    "raw": {"section": "Operating Expenses"},
                },
            ],
            [],
        )

    monkeypatch.setattr("app.services.budget_history_service._table_to_line_items", _low_confidence_line_items)

    response = _upload_document(
        client,
        "vendor-shifted-header-income.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_required"] is True
    assert payload["draft"] is None
