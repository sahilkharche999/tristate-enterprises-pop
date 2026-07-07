"""Tests for GET /hoa/{hoa_id}/budget/uploads/{upload_id}/income-statement-file.

Streams the raw uploaded income-statement PDF bytes; backs the Enriched tab's
"Compare with source" split view (change: add-income-statement-pdf-compare-view).
Mirrors the cross-HOA/404/auth test shape already established in
test_reserve_study_file_endpoint.py for the sibling reserve-study file-serving
route.
"""

from __future__ import annotations

from app.models.financial_document_extraction import ExtractedFinancialStatement

PDF_BYTES = b"%PDF-1.4\n%minimal stub\n%%EOF\n"


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


def _upload_pdf_income_statement(client, monkeypatch, tmp_path, hoa_id: int = 1) -> int:
    async def _fake_extract(path: str):
        return _valid_pdf_statement()

    def _fake_normalize(statement):
        output_path = tmp_path / "normalized.pdf.xlsx"
        output_path.write_bytes(b"normalized workbook")
        return str(output_path)

    monkeypatch.setattr("app.services.budget_history_service.extract_pdf_statement", _fake_extract)
    monkeypatch.setattr(
        "app.services.budget_history_service.build_normalized_statement_workbook", _fake_normalize
    )

    response = client.post(
        f"/hoa/{hoa_id}/budget/upload",
        data={"source_mode": "income_statement"},
        files={"file": ("income-statement.pdf", PDF_BYTES, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    upload_id = payload["upload_id"]
    assert upload_id is not None
    return upload_id


def test_get_income_statement_file_returns_pdf_bytes(
    client, budget_history_test_harness, monkeypatch, tmp_path
):
    upload_id = _upload_pdf_income_statement(client, monkeypatch, tmp_path)

    response = client.get(f"/hoa/1/budget/uploads/{upload_id}/income-statement-file")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == PDF_BYTES


def test_get_income_statement_file_404_when_upload_missing(client, budget_history_test_harness):
    response = client.get("/hoa/1/budget/uploads/99999/income-statement-file")

    assert response.status_code == 404


def test_get_income_statement_file_404_when_upload_belongs_to_other_hoa(
    client, db_session, budget_history_test_harness, monkeypatch, tmp_path
):
    """Cross-HOA isolation: upload created under HOA 1 -> fetched via a different HOA -> 404."""
    from app.ai_implementation.db.models import Property

    upload_id = _upload_pdf_income_statement(client, monkeypatch, tmp_path)

    other = Property(name="Different HOA", units=20, hoa_code="X3")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    response = client.get(f"/hoa/{other.id}/budget/uploads/{upload_id}/income-statement-file")

    assert response.status_code == 404


def test_get_income_statement_file_404_when_role_is_reserve_study(
    client, db_session, budget_history_test_harness, monkeypatch
):
    """A reserve_study-role upload must not be servable via the income-statement file route."""
    from app.models.reserve_study_extraction import (
        ExtractedReserveStudyDocument,
        ExtractedReserveStudyRow,
    )

    hoa_id = 1
    resp = client.post(
        f"/hoa/{hoa_id}/budget/upload",
        data={"source_mode": "income_statement"},
        files={
            "file": (
                "income.xlsx",
                b"fake bytes",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    draft_id = resp.json()["draft"]["id"]

    async def _fake_extract_reserve(path: str):
        return ExtractedReserveStudyDocument(
            study_year=2025,
            page_spans=[{"start_page": 1, "end_page": 1, "confidence": 0.9}],
            rows=[
                ExtractedReserveStudyRow(
                    row_id="roof-1",
                    line_item="Roof Replacement",
                    useful_life=25,
                    remaining_life=10,
                    quantity=1,
                    replacement_cost=300000.0,
                    source_page=1,
                    flags=[],
                )
            ],
            warnings=[],
            confidence=0.95,
        )

    monkeypatch.setattr(
        "app.services.budget_history_service.extract_reserve_study", _fake_extract_reserve
    )
    reserve_resp = client.post(
        f"/hoa/{hoa_id}/budget/drafts/{draft_id}/reserve-study/upload",
        files={"reserve_study_file": ("reserve-study.pdf", PDF_BYTES, "application/pdf")},
    )
    assert reserve_resp.status_code == 200, reserve_resp.text
    reserve_upload_id = reserve_resp.json()["reserve_study_upload_id"]

    response = client.get(f"/hoa/{hoa_id}/budget/uploads/{reserve_upload_id}/income-statement-file")

    assert response.status_code == 404


def test_get_income_statement_file_404_when_file_missing_from_disk(
    client, db_session, budget_history_test_harness, monkeypatch, tmp_path
):
    upload_id = _upload_pdf_income_statement(client, monkeypatch, tmp_path)

    from app.ai_implementation.db.models import BudgetUpload
    from app.services.budget_history_service import _budget_storage_path

    upload = db_session.get(BudgetUpload, upload_id)
    _budget_storage_path(upload.storage_key).unlink()

    response = client.get(f"/hoa/1/budget/uploads/{upload_id}/income-statement-file")

    assert response.status_code == 404


def test_get_income_statement_file_requires_auth(
    client, budget_history_test_harness, monkeypatch, tmp_path
):
    upload_id = _upload_pdf_income_statement(client, monkeypatch, tmp_path)

    response = client.get(
        f"/hoa/1/budget/uploads/{upload_id}/income-statement-file",
        headers={"Authorization": ""},
    )

    assert response.status_code in (401, 403)


def test_income_statement_file_media_type_falls_back_to_router_when_content_type_missing(
    client, db_session, budget_history_test_harness, monkeypatch, tmp_path
):
    upload_id = _upload_pdf_income_statement(client, monkeypatch, tmp_path)

    from app.ai_implementation.db.models import BudgetUpload

    upload = db_session.get(BudgetUpload, upload_id)
    upload.content_type = None
    db_session.commit()

    response = client.get(f"/hoa/1/budget/uploads/{upload_id}/income-statement-file")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
