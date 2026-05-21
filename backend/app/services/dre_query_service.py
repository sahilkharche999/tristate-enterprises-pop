"""DRE document + extraction-run read-side service (Phase 4 — Review Workbench).

Powers the Review Workbench UI: list extraction runs for an HOA, fetch a
single run's parsed_json plus its citations + review-edit history.

All read-only. No mutation here — edits go through
``dre_review_service.record_review_edit`` and the approval flow through
``dre_approval_service.approve_extraction_run``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from pydantic import BaseModel


class DREDocumentResponse(BaseModel):
    document_id: int
    property_id: int
    file_id: str
    file_name: str
    page_count: Optional[int]
    status: str
    uploaded_by: Optional[str]
    uploaded_at: str
    supersedes_id: Optional[int]


class DREExtractionRunListItem(BaseModel):
    extraction_run_id: int
    dre_document_id: int
    property_id: int
    status: str
    review_status: str
    promoted_setup_id: Optional[int]
    promoted_at: Optional[str]
    completed_at: Optional[str]
    model_name: Optional[str]
    prompt_version: Optional[str]
    repair_attempt_count: int


class DREExtractionRunDetail(BaseModel):
    extraction_run_id: int
    dre_document_id: int
    property_id: int
    status: str
    review_status: str
    promoted_setup_id: Optional[int]
    promoted_at: Optional[str]
    completed_at: Optional[str]
    model_name: Optional[str]
    prompt_version: Optional[str]
    prompt_sha256: Optional[str]
    repair_attempt_count: int
    parsed_json: Optional[dict]
    citation_audit: Optional[list]
    low_confidence_flags: Optional[list]
    validation_warnings: Optional[list]
    schema_validation_errors: Optional[list]


class DREExtractionRunNotFound(LookupError):
    """Raised when the run isn't found for the property."""


def list_dre_documents(
    *, property_id: int, connection: sqlite3.Connection,
) -> list[DREDocumentResponse]:
    rows = connection.execute(
        """
        SELECT id, property_id, file_id, file_name, page_count,
               status, uploaded_by, uploaded_at, supersedes_id
          FROM dre_documents
         WHERE property_id = ?
         ORDER BY uploaded_at DESC
        """,
        (property_id,),
    ).fetchall()
    return [
        DREDocumentResponse(
            document_id=r[0], property_id=r[1], file_id=r[2], file_name=r[3],
            page_count=r[4], status=r[5], uploaded_by=r[6], uploaded_at=r[7],
            supersedes_id=r[8],
        )
        for r in rows
    ]


def list_extraction_runs(
    *, property_id: int, connection: sqlite3.Connection,
) -> list[DREExtractionRunListItem]:
    rows = connection.execute(
        """
        SELECT id, dre_document_id, property_id, status, review_status,
               promoted_setup_id, promoted_at, completed_at,
               model_name, prompt_version, repair_attempt_count
          FROM dre_extraction_runs
         WHERE property_id = ?
         ORDER BY completed_at DESC
        """,
        (property_id,),
    ).fetchall()
    return [
        DREExtractionRunListItem(
            extraction_run_id=r[0], dre_document_id=r[1], property_id=r[2],
            status=r[3], review_status=r[4],
            promoted_setup_id=r[5], promoted_at=r[6], completed_at=r[7],
            model_name=r[8], prompt_version=r[9], repair_attempt_count=r[10],
        )
        for r in rows
    ]


def _safe_json(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def get_extraction_run(
    *, property_id: int, extraction_run_id: int,
    connection: sqlite3.Connection,
) -> DREExtractionRunDetail:
    row = connection.execute(
        """
        SELECT id, dre_document_id, property_id, status, review_status,
               promoted_setup_id, promoted_at, completed_at,
               model_name, prompt_version, prompt_sha256,
               repair_attempt_count, parsed_json,
               citation_audit_json, low_confidence_flags_json,
               validation_warnings_json, schema_validation_errors
          FROM dre_extraction_runs
         WHERE id = ? AND property_id = ?
        """,
        (extraction_run_id, property_id),
    ).fetchone()
    if row is None:
        raise DREExtractionRunNotFound(
            f"extraction_run_id={extraction_run_id} "
            f"not found for property_id={property_id}"
        )
    return DREExtractionRunDetail(
        extraction_run_id=row[0],
        dre_document_id=row[1],
        property_id=row[2],
        status=row[3],
        review_status=row[4],
        promoted_setup_id=row[5],
        promoted_at=row[6],
        completed_at=row[7],
        model_name=row[8],
        prompt_version=row[9],
        prompt_sha256=row[10],
        repair_attempt_count=row[11],
        parsed_json=_safe_json(row[12]),
        citation_audit=_safe_json(row[13]),
        low_confidence_flags=_safe_json(row[14]),
        validation_warnings=_safe_json(row[15]),
        schema_validation_errors=_safe_json(row[16]),
    )


__all__ = [
    "DREDocumentResponse",
    "DREExtractionRunDetail",
    "DREExtractionRunListItem",
    "DREExtractionRunNotFound",
    "get_extraction_run",
    "list_dre_documents",
    "list_extraction_runs",
]
