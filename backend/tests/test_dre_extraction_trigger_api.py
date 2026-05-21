"""Tests for ``POST /hoa/{id}/dre/documents/{doc_id}/extract``.

Verifies the router-side preconditions + that the BackgroundTask is
actually scheduled with the right kwargs. The Gemini call itself is
covered by ``test_dre_live_extraction.py`` (which exercises the same
``run_extraction_job`` function end-to-end against real API).
"""

from __future__ import annotations

from io import BytesIO

import pytest


def _seeded_property_id(db_session) -> int:
    from app.ai_implementation.db.models import Property

    row = db_session.query(Property).first()
    assert row is not None, "seed should produce at least one property"
    return row.id


def _upload_dre(client, hoa_id: int) -> int:
    """Upload a minimal stub PDF and return the new document_id."""
    pdf_bytes = b"%PDF-1.4\n%minimal stub\n%%EOF\n"
    response = client.post(
        f"/hoa/{hoa_id}/dre/upload",
        files={"file": ("test_dre.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["dre_document_id"]


def test_trigger_extraction_returns_202_and_schedules_task(
    client, db_session, monkeypatch
):
    """Happy path: known document → 202 + background task scheduled."""
    from app.routers import dre as dre_router

    hoa_id = _seeded_property_id(db_session)
    document_id = _upload_dre(client, hoa_id)

    captured: dict = {}

    def fake_run_extraction_job(*, property_id, dre_document_id, file_id):
        captured["property_id"] = property_id
        captured["dre_document_id"] = dre_document_id
        captured["file_id"] = file_id

    monkeypatch.setattr(dre_router, "run_extraction_job", fake_run_extraction_job)

    response = client.post(
        f"/hoa/{hoa_id}/dre/documents/{document_id}/extract"
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["document_id"] == document_id
    assert body["status"] == "scheduled"

    # TestClient runs BackgroundTasks synchronously after the response.
    assert captured["property_id"] == hoa_id
    assert captured["dre_document_id"] == document_id
    assert captured["file_id"]  # non-empty


def test_trigger_extraction_404_when_document_missing(client, db_session):
    hoa_id = _seeded_property_id(db_session)

    response = client.post(
        f"/hoa/{hoa_id}/dre/documents/99999/extract"
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_trigger_extraction_404_when_document_belongs_to_other_hoa(
    client, db_session
):
    """Cross-HOA isolation: doc uploaded under HOA A → trigger on HOA B → 404."""
    from app.ai_implementation.db.models import Property

    hoa_a = _seeded_property_id(db_session)
    other = Property(name="Different HOA", units=20, hoa_code="X1")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    document_id = _upload_dre(client, hoa_a)

    response = client.post(
        f"/hoa/{other.id}/dre/documents/{document_id}/extract"
    )
    assert response.status_code == 404
    assert "different hoa" in response.json()["detail"].lower()


def test_trigger_extraction_requires_auth(client, db_session):
    hoa_id = _seeded_property_id(db_session)
    document_id = _upload_dre(client, hoa_id)

    response = client.post(
        f"/hoa/{hoa_id}/dre/documents/{document_id}/extract",
        headers={"Authorization": ""},
    )
    assert response.status_code in (401, 403)
