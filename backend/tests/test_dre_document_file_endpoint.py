"""Tests for GET /hoa/{hoa_id}/dre/documents/{document_id}/file.

Streams the raw uploaded DRE/CC&R PDF bytes; backs the Review Workbench
"Compare with PDF" split view (change: add-dre-review-pdf-compare-view).
Mirrors the cross-HOA/404/auth test shape already established in
``test_dre_extraction_trigger_api.py`` for the sibling
``/dre/documents/{document_id}/extract`` route.
"""

from __future__ import annotations

from io import BytesIO

PDF_BYTES = b"%PDF-1.4\n%minimal stub\n%%EOF\n"


def _seeded_property_id(db_session) -> int:
    from app.ai_implementation.db.models import Property

    row = db_session.query(Property).first()
    assert row is not None, "seed should produce at least one property"
    return row.id


def _upload_dre(client, hoa_id: int) -> int:
    response = client.post(
        f"/hoa/{hoa_id}/dre/upload",
        files={"file": ("test_dre.pdf", BytesIO(PDF_BYTES), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["dre_document_id"]


def test_get_document_file_returns_pdf_bytes(client, db_session):
    hoa_id = _seeded_property_id(db_session)
    document_id = _upload_dre(client, hoa_id)

    response = client.get(f"/hoa/{hoa_id}/dre/documents/{document_id}/file")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == PDF_BYTES


def test_get_document_file_404_when_document_missing(client, db_session):
    hoa_id = _seeded_property_id(db_session)

    response = client.get(f"/hoa/{hoa_id}/dre/documents/99999/file")

    assert response.status_code == 404


def test_get_document_file_404_when_document_belongs_to_other_hoa(client, db_session):
    """Cross-HOA isolation: doc uploaded under HOA A -> fetched via HOA B -> 404."""
    from app.ai_implementation.db.models import Property

    hoa_a = _seeded_property_id(db_session)
    other = Property(name="Different HOA", units=20, hoa_code="X2")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    document_id = _upload_dre(client, hoa_a)

    response = client.get(f"/hoa/{other.id}/dre/documents/{document_id}/file")

    assert response.status_code == 404


def test_get_document_file_404_when_file_missing_from_disk(client, db_session):
    hoa_id = _seeded_property_id(db_session)
    document_id = _upload_dre(client, hoa_id)

    from app.dre_extraction.storage import dre_file_path

    raw_conn = db_session.connection().connection
    file_id = raw_conn.execute(
        "SELECT file_id FROM dre_documents WHERE id = ?", (document_id,),
    ).fetchone()[0]
    dre_file_path(file_id).unlink()

    response = client.get(f"/hoa/{hoa_id}/dre/documents/{document_id}/file")

    assert response.status_code == 404


def test_get_document_file_requires_auth(client, db_session):
    hoa_id = _seeded_property_id(db_session)
    document_id = _upload_dre(client, hoa_id)

    response = client.get(
        f"/hoa/{hoa_id}/dre/documents/{document_id}/file",
        headers={"Authorization": ""},
    )

    assert response.status_code in (401, 403)
