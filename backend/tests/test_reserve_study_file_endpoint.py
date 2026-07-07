"""Tests for GET /hoa/{hoa_id}/budget/uploads/{upload_id}/file.

Streams the raw uploaded reserve-study PDF bytes; backs the reserve study
table's "Compare with PDF" split view (change:
add-reserve-study-pdf-compare-view). Mirrors the cross-HOA/404/auth test
shape already established in test_dre_document_file_endpoint.py for the
sibling DRE file-serving route.
"""

from __future__ import annotations

from io import BytesIO

import pytest

from app.models.reserve_study_extraction import (
    ExtractedReserveStudyDocument,
    ExtractedReserveStudyRow,
)

PDF_BYTES = b"%PDF-1.4\n%minimal stub\n%%EOF\n"


def _fake_reserve_doc_success() -> ExtractedReserveStudyDocument:
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


def _create_draft(client, hoa_id: int) -> int:
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
    return resp.json()["draft"]["id"]


def _upload_reserve_study(client, monkeypatch, hoa_id: int, draft_id: int) -> int:
    async def _fake_extract_success(path: str):
        return _fake_reserve_doc_success()

    monkeypatch.setattr(
        "app.services.budget_history_service.extract_reserve_study",
        _fake_extract_success,
    )

    resp = client.post(
        f"/hoa/{hoa_id}/budget/drafts/{draft_id}/reserve-study/upload",
        files={"reserve_study_file": ("reserve-study.pdf", PDF_BYTES, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    upload_id = resp.json()["reserve_study_upload_id"]
    assert upload_id is not None
    return upload_id


@pytest.fixture
def reserve_study_upload(client, budget_history_test_harness, monkeypatch):
    hoa_id = 1
    draft_id = _create_draft(client, hoa_id)
    upload_id = _upload_reserve_study(client, monkeypatch, hoa_id, draft_id)
    return hoa_id, upload_id


def test_get_reserve_study_file_returns_pdf_bytes(client, reserve_study_upload):
    hoa_id, upload_id = reserve_study_upload

    response = client.get(f"/hoa/{hoa_id}/budget/uploads/{upload_id}/file")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == PDF_BYTES


def test_get_reserve_study_file_404_when_upload_missing(client, budget_history_test_harness):
    response = client.get("/hoa/1/budget/uploads/99999/file")

    assert response.status_code == 404


def test_get_reserve_study_file_404_when_upload_belongs_to_other_hoa(
    client, db_session, reserve_study_upload
):
    """Cross-HOA isolation: upload created under HOA 1 -> fetched via a different HOA -> 404."""
    from app.ai_implementation.db.models import Property

    _hoa_id, upload_id = reserve_study_upload

    other = Property(name="Different HOA", units=20, hoa_code="X3")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    response = client.get(f"/hoa/{other.id}/budget/uploads/{upload_id}/file")

    assert response.status_code == 404


def test_get_reserve_study_file_404_when_role_is_budget_source(
    client, db_session, budget_history_test_harness
):
    """A budget_source-role upload must not be servable via the reserve-study file route."""
    hoa_id = 1
    draft_id = _create_draft(client, hoa_id)

    from app.services.budget_history_service import _get_draft

    draft = _get_draft(db_session, hoa_id, draft_id)
    budget_source_upload_id = draft.source_upload_id
    assert budget_source_upload_id is not None

    response = client.get(f"/hoa/{hoa_id}/budget/uploads/{budget_source_upload_id}/file")

    assert response.status_code == 404


def test_get_reserve_study_file_404_when_file_missing_from_disk(
    client, db_session, reserve_study_upload
):
    hoa_id, upload_id = reserve_study_upload

    from app.ai_implementation.db.models import BudgetUpload
    from app.services.budget_history_service import _budget_storage_path

    upload = db_session.get(BudgetUpload, upload_id)
    _budget_storage_path(upload.storage_key).unlink()

    response = client.get(f"/hoa/{hoa_id}/budget/uploads/{upload_id}/file")

    assert response.status_code == 404


def test_get_reserve_study_file_requires_auth(client, reserve_study_upload):
    hoa_id, upload_id = reserve_study_upload

    response = client.get(
        f"/hoa/{hoa_id}/budget/uploads/{upload_id}/file",
        headers={"Authorization": ""},
    )

    assert response.status_code in (401, 403)
