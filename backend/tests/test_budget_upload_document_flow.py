from pathlib import Path

from app.ai_implementation.db import Property
from app.models.budget_history import BudgetDraftPayload
from app.models.financial_document_extraction import DocumentExtractionFailure, ExtractedFinancialStatement
from app.models.reserve_study_extraction import ExtractedReserveStudyDocument, ExtractedReserveStudyRow


def _upload_document(client, filename: str, content_type: str):
    return client.post(
        "/hoa/1/budget/upload",
        files={"file": (filename, b"placeholder bytes", content_type)},
    )


def _upload_bundle(
    client,
    *,
    budget_filename: str,
    budget_content_type: str,
    reserve_filename: str,
    reserve_content_type: str,
):
    return client.post(
        "/hoa/1/budget/upload-bundle",
        files={
            "budget_file": (budget_filename, b"budget placeholder bytes", budget_content_type),
            "reserve_study_file": (reserve_filename, b"reserve placeholder bytes", reserve_content_type),
        },
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
    assert "was not accepted as an income statement" in payload["review_reason"]
    assert any("Expected budget source format" in warning for warning in payload["warnings"])


def test_bundle_upload_returns_separate_budget_and_reserve_statuses(
    client,
    budget_history_test_harness,
):
    response = _upload_bundle(
        client,
        budget_filename="income-statement.xlsx",
        budget_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        reserve_filename="reserve-study.pdf",
        reserve_content_type="application/pdf",
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["draft"]["status"] == "active"
    assert payload["budget_source"]["filename"] == "income-statement.xlsx"
    assert payload["budget_source"]["status"] == "completed"
    assert payload["reserve_study"]["filename"] == "reserve-study.pdf"
    assert payload["reserve_study"]["status"] in {"pending", "completed", "review_required"}
    assert payload["can_continue_with_budget_only"] is False


def test_bundle_upload_budget_review_required_explains_expected_income_statement_format(
    client,
    budget_history_test_harness,
    monkeypatch,
):
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

    response = _upload_bundle(
        client,
        budget_filename="401 Esprit Park 2025 Budget .xlsx",
        budget_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        reserve_filename="reserve-study.pdf",
        reserve_content_type="application/pdf",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["budget_source"]["status"] == "review_required"
    assert "was not accepted as an income statement" in payload["budget_source"]["review_reason"]
    assert any("Expected budget source format" in warning for warning in payload["budget_source"]["warnings"])
    assert any("Income Statement Esprit Park Aug 2025.xlsx" in warning for warning in payload["budget_source"]["warnings"])


def test_bundle_upload_can_offer_continue_with_budget_only_when_reserve_fails(
    client,
    budget_history_test_harness,
):
    response = _upload_bundle(
        client,
        budget_filename="income-statement.xlsx",
        budget_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        reserve_filename="reserve-study.txt",
        reserve_content_type="text/plain",
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["draft"]["status"] == "active"
    assert payload["budget_source"]["status"] == "completed"
    assert payload["reserve_study"]["status"] == "failed"
    assert payload["can_continue_with_budget_only"] is True
    assert payload["reserve_study"]["review_reason"]


def test_bundle_upload_preserves_successful_reserve_upload_when_budget_fails(
    client,
    budget_history_test_harness,
):
    response = _upload_bundle(
        client,
        budget_filename="income-statement.txt",
        budget_content_type="text/plain",
        reserve_filename="reserve-study.pdf",
        reserve_content_type="application/pdf",
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["draft"] is None
    assert payload["budget_source"]["status"] == "failed"
    assert payload["reserve_study"]["filename"] == "reserve-study.pdf"
    assert payload["reserve_study"]["status"] in {"pending", "completed", "review_required"}
    assert payload["can_continue_with_reserve_study_only"] is True


def test_draft_payload_includes_reserve_study_rows_and_review_status():
    payload = BudgetDraftPayload.model_validate(
        {
            "id": 101,
            "status": "active",
            "source_upload_id": 44,
            "line_items": [],
            "reserve_inflation_rate": 0.03,
            "reserve_study_status": "pending",
            "reserve_study_rows": [
                {
                    "row_id": "row-1",
                    "line_item": "Roof",
                    "useful_life": 20,
                    "remaining_life": 2,
                    "quantity": 1,
                    "replacement_cost": 250000.0,
                    "source_page": 19,
                    "flags": [],
                }
            ],
            "reserve_study_warnings": ["Missing study year"],
        }
    )

    assert payload.reserve_study_status == "pending"
    assert payload.reserve_study_rows[0]["line_item"] == "Roof"
    assert payload.reserve_study_rows[0]["remaining_life"] == 2
    assert payload.reserve_study_warnings == ["Missing study year"]


def test_global_reserve_inflation_comes_from_app_settings_not_property(client, db_session):
    hoa = db_session.get(Property, 1)
    assert hoa is not None
    hoa.reserve_inflation_rate = 0.01
    db_session.commit()

    update_response = client.put(
        "/app-settings",
        json={"global_reserve_inflation_rate": 0.03},
    )
    assert update_response.status_code == 200

    hoa_response = client.get("/hoa/1")
    assert hoa_response.status_code == 200
    payload = hoa_response.json()

    assert payload["reserve_inflation_rate"] == 0.03


def test_bundle_upload_stores_reserve_rows_when_reserve_file_succeeds(
    client,
    budget_history_test_harness,
    monkeypatch,
):
    async def _fake_extract_reserve_study(path: str):
        return ExtractedReserveStudyDocument(
            study_year=2024,
            page_spans=[{"start_page": 20, "end_page": 21, "confidence": 0.93}],
            rows=[
                ExtractedReserveStudyRow(
                    row_id="roof-1",
                    line_item="Roof",
                    useful_life=20,
                    remaining_life=1,
                    quantity=1,
                    replacement_cost=250000.0,
                    source_page=20,
                    flags=[],
                )
            ],
            warnings=["Study year inferred from cover page"],
            confidence=0.88,
        )

    monkeypatch.setattr(
        "app.services.budget_history_service.extract_reserve_study",
        _fake_extract_reserve_study,
    )

    response = _upload_bundle(
        client,
        budget_filename="income-statement.xlsx",
        budget_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        reserve_filename="reserve-study.pdf",
        reserve_content_type="application/pdf",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reserve_study"]["status"] == "review_required"
    assert payload["draft"]["reserve_study_status"] == "review_required"
    assert payload["draft"]["reserve_study_rows"][0]["line_item"] == "Roof"
    assert payload["draft"]["reserve_study_warnings"] == ["Study year inferred from cover page"]


def test_reopen_generated_version_preserves_reserve_study_state(
    client,
    budget_history_test_harness,
):
    upload_response = _upload_document(
        client,
        "income-statement.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    reserve_save = client.patch(
        f"/hoa/1/budget/drafts/{draft['id']}/reserve-study",
        json={
            "rows": [
                {
                    "row_id": "roof-1",
                    "line_item": "Roof",
                    "useful_life": 20,
                    "remaining_life": 1,
                    "quantity": 1,
                    "replacement_cost": 250000.0,
                    "source_page": 20,
                    "flags": [],
                }
            ],
            "warnings": ["Study year inferred from cover page"],
        },
    )
    assert reserve_save.status_code == 200
    saved_draft = reserve_save.json()
    assert saved_draft["reserve_study_rows"][0]["line_item"] == "Roof"
    assert saved_draft["reserve_study_status"] == "review_required"

    generate_response = client.post(
        "/hoa/1/budget/generate",
        json={
            "draft_id": draft["id"],
            "line_items": draft["line_items"],
            "global_note": "Generated with reserve study state",
        },
    )
    assert generate_response.status_code == 200
    version = generate_response.json()["version"]

    reopen_response = client.post(f"/hoa/1/versions/{version['id']}/reopen")
    assert reopen_response.status_code == 200
    reopened_draft = reopen_response.json()["draft"]

    assert reopened_draft["reserve_study_rows"][0]["line_item"] == "Roof"
    assert reopened_draft["reserve_study_warnings"] == ["Study year inferred from cover page"]
    assert reopened_draft["reserve_study_status"] == "review_required"


def test_save_reserve_study_rows_returns_derived_reserve_columns(
    client,
    budget_history_test_harness,
):
    upload_response = _upload_document(
        client,
        "income-statement.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    reserve_save = client.patch(
        f"/hoa/1/budget/drafts/{draft['id']}/reserve-study",
        json={
            "rows": [
                {
                    "row_id": "lighting-1",
                    "line_item": "Lighting - Exterior",
                    "useful_life": 20,
                    "remaining_life": 10,
                    "quantity": "1",
                    "replacement_cost": 1529.0,
                    "source_page": 20,
                    "flags": [],
                }
            ],
            "warnings": [],
        },
    )
    assert reserve_save.status_code == 200
    saved_draft = reserve_save.json()

    assert saved_draft["reserve_study_rows"][0]["year_replacement_provision"] == 76
    assert saved_draft["reserve_study_rows"][0]["estimated_liability"] == 764


def test_save_reserve_study_rows_preserves_headers_and_applies_only_items(
    client,
    budget_history_test_harness,
):
    upload_response = _upload_document(
        client,
        "income-statement.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    reserve_save = client.patch(
        f"/hoa/1/budget/drafts/{draft['id']}/reserve-study",
        json={
            "rows": [
                {
                    "row_id": "header-1",
                    "row_type": "header",
                    "line_item": "Exterior Components",
                    "flags": ["missing_replacement_cost"],
                },
                {
                    "row_id": "roof-1",
                    "line_item": "Roof",
                    "useful_life": 20,
                    "remaining_life": 1,
                    "quantity": "1",
                    "replacement_cost": 100000.0,
                    "flags": [],
                },
            ],
            "warnings": [],
        },
    )
    assert reserve_save.status_code == 200
    saved_draft = reserve_save.json()

    assert saved_draft["reserve_study_rows"][0]["row_type"] == "header"
    assert saved_draft["reserve_study_rows"][0]["line_item"] == "Exterior Components"
    assert saved_draft["reserve_study_rows"][0]["flags"] == []

    apply_response = client.post(f"/hoa/1/budget/drafts/{draft['id']}/reserve-study/apply")
    assert apply_response.status_code == 200
    applied = apply_response.json()

    assert applied["applied_count"] == 1
    applied_names = [item.get("name") or item.get("label") for item in applied["draft"]["line_items"]]
    assert "Exterior Components" not in applied_names
    assert "Roof" in applied_names
