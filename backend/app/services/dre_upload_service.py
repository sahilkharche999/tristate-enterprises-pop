"""DRE upload service (Phase 3.1).

Glue between the DRE FastAPI router, the SQLite ``dre_documents``
table, and the on-disk storage helpers in ``dre_extraction.storage``.

Flow for one upload:

    1. Verify the property exists.
    2. Determine page count from the uploaded bytes (best-effort —
       falls back to ``None`` if PyMuPDF can't open the file).
    3. Insert a ``dre_documents`` row using the persistence helper,
       which also marks any prior active document for the property as
       ``superseded``.
    4. Persist the bytes to disk using the row id as a filename prefix.
    5. Update the row's ``file_id`` to point at the saved file.

The service is sync because all underlying primitives are sync; the
FastAPI route awaits ``UploadFile.read()`` itself and hands the bytes
in.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from pydantic import BaseModel

from app.dre_extraction import storage
from app.dre_extraction.persistence import insert_dre_document


class DREUploadResponse(BaseModel):
    """JSON shape returned by the upload route."""

    dre_document_id: int
    property_id: int
    file_id: str
    file_name: str
    page_count: Optional[int]
    status: str


class PropertyNotFound(LookupError):
    """Raised when ``property_id`` doesn't exist in the database."""


def _detect_page_count(file_bytes: bytes) -> Optional[int]:
    try:
        import fitz  # type: ignore
    except ImportError:
        return None
    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            return doc.page_count
    except Exception:
        return None


def mark_approved_setups_stale_for_new_document(
    connection: sqlite3.Connection,
    *,
    property_id: int,
    document_type: str,
    new_document_id: int,
) -> int:
    """Mark approved setups stale when their source doc is no longer active.

    Only setups whose source document has the same ``document_type``
    (``dre`` or ``ccr``) are marked. Other governing-document types stay
    approved until their own source is replaced.
    """
    cols = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(assessment_setups)").fetchall()
    }
    if "source_document_stale" not in cols:
        return 0
    cursor = connection.execute(
        """
        UPDATE assessment_setups
           SET source_document_stale = 1,
               updated_at = datetime('now')
         WHERE property_id = ?
           AND status = 'approved'
           AND COALESCE(source_document_stale, 0) = 0
           AND source_dre_document_id IS NOT NULL
           AND source_dre_document_id != ?
           AND EXISTS (
               SELECT 1 FROM dre_documents d
                WHERE d.id = assessment_setups.source_dre_document_id
                  AND COALESCE(d.document_type, 'dre') = ?
           )
        """,
        (property_id, new_document_id, document_type),
    )
    return int(cursor.rowcount or 0)


def upload_dre_document(
    *,
    property_id: int,
    file_bytes: bytes,
    original_filename: str,
    uploaded_by: Optional[str],
    connection: sqlite3.Connection,
    document_type: str = "dre",
) -> DREUploadResponse:
    """Save a DRE upload to disk + record it in the DB.

    Caller (the router) is responsible for the SQLAlchemy session
    boundary and final commit. This function commits its own writes
    via the supplied raw connection so the upload is atomic from the
    caller's perspective: either the row + file both exist, or both
    are absent.

    Raises:
        PropertyNotFound: when ``property_id`` doesn't match a row.
    """
    property_row = connection.execute(
        "SELECT id FROM properties WHERE id = ?",
        (property_id,),
    ).fetchone()
    if property_row is None:
        raise PropertyNotFound(f"property_id={property_id} does not exist")

    page_count = _detect_page_count(file_bytes)

    # Insert the row first so we get an id; the id prefixes the on-disk
    # filename so concurrent uploads for one HOA don't collide.
    dre_document_id = insert_dre_document(
        property_id=property_id,
        file_id="pending",  # placeholder; updated after save
        file_name=original_filename,
        page_count=page_count,
        uploaded_by=uploaded_by,
        connection=connection,
    )

    file_id = storage.save_dre_file(
        property_id=property_id,
        file_bytes=file_bytes,
        original_filename=original_filename,
        dre_document_id=dre_document_id,
    )

    connection.execute(
        "UPDATE dre_documents SET file_id = ? WHERE id = ?",
        (file_id, dre_document_id),
    )
    normalized_type = "ccr" if str(document_type).strip().lower() == "ccr" else "dre"
    if normalized_type == "ccr":
        connection.execute(
            "UPDATE dre_documents SET document_type = 'ccr' WHERE id = ?",
            (dre_document_id,),
        )
    mark_approved_setups_stale_for_new_document(
        connection,
        property_id=property_id,
        document_type=normalized_type,
        new_document_id=dre_document_id,
    )
    connection.commit()

    return DREUploadResponse(
        dre_document_id=dre_document_id,
        property_id=property_id,
        file_id=file_id,
        file_name=original_filename,
        page_count=page_count,
        status="active",
    )
