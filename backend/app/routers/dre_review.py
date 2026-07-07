"""Read-side DRE Review Workbench endpoints (Phase 4.1).

Lists DRE documents + extraction runs for an HOA, returns one run's
detailed parsed_json / citations / warnings for the Review Workbench
UI. Mutating endpoints (record edit, approve) live in their own
modules.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..dre_extraction.storage import dre_file_exists, dre_file_path
from ..services.dre_query_service import (
    DREDocumentNotFound,
    DREDocumentResponse,
    DREExtractionRunDetail,
    DREExtractionRunListItem,
    DREExtractionRunNotFound,
    get_dre_document,
    get_extraction_run,
    list_dre_documents,
    list_extraction_runs,
)
from ..services.dre_review_service import (
    DREReviewEditResponse,
    ExtractedFieldSourceResponse,
    list_field_sources,
    list_review_edits,
    record_review_edit,
)


router = APIRouter(tags=["DRE Review"])


@router.get(
    "/hoa/{hoa_id}/dre/documents",
    response_model=list[DREDocumentResponse],
)
def list_hoa_dre_documents(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> list[DREDocumentResponse]:
    return list_dre_documents(
        property_id=hoa_id, connection=session.connection().connection,
    )


@router.get(
    "/hoa/{hoa_id}/dre/extraction-runs",
    response_model=list[DREExtractionRunListItem],
)
def list_hoa_extraction_runs(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> list[DREExtractionRunListItem]:
    return list_extraction_runs(
        property_id=hoa_id, connection=session.connection().connection,
    )


@router.get("/hoa/{hoa_id}/dre/documents/{document_id}/file")
def get_hoa_dre_document_file(
    hoa_id: int,
    document_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
) -> FileResponse:
    """Stream the raw uploaded DRE/CC&R PDF for the Review Workbench's
    "Compare with PDF" split view (add-dre-review-pdf-compare-view).
    """
    raw_conn = session.connection().connection
    try:
        doc = get_dre_document(
            property_id=hoa_id, document_id=document_id, connection=raw_conn,
        )
    except DREDocumentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not dre_file_exists(doc.file_id):
        raise HTTPException(status_code=404, detail="Document file not found on disk")
    return FileResponse(
        path=dre_file_path(doc.file_id),
        filename=doc.file_name,
        media_type="application/pdf",
    )


@router.get(
    "/hoa/{hoa_id}/dre/extraction-runs/{run_id}",
    response_model=DREExtractionRunDetail,
)
def get_hoa_extraction_run(
    hoa_id: int,
    run_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> DREExtractionRunDetail:
    try:
        return get_extraction_run(
            property_id=hoa_id, extraction_run_id=run_id,
            connection=session.connection().connection,
        )
    except DREExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/hoa/{hoa_id}/dre/extraction-runs/{run_id}/edits",
    response_model=list[DREReviewEditResponse],
)
def get_run_edit_history(
    hoa_id: int,
    run_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> list[DREReviewEditResponse]:
    """Return chronological list of Review Workbench edits for a run.

    Verifies the run belongs to the HOA first (returns 404 otherwise).
    """
    raw_conn = session.connection().connection
    try:
        get_extraction_run(
            property_id=hoa_id, extraction_run_id=run_id, connection=raw_conn,
        )
    except DREExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return list_review_edits(dre_extraction_run_id=run_id, connection=raw_conn)


@router.get(
    "/hoa/{hoa_id}/dre/extraction-runs/{run_id}/field-sources",
    response_model=list[ExtractedFieldSourceResponse],
)
def get_run_field_sources(
    hoa_id: int,
    run_id: int,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> list[ExtractedFieldSourceResponse]:
    raw_conn = session.connection().connection
    try:
        get_extraction_run(
            property_id=hoa_id, extraction_run_id=run_id, connection=raw_conn,
        )
    except DREExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return list_field_sources(
        dre_extraction_run_id=run_id,
        entity_type=entity_type,
        entity_id=entity_id,
        connection=raw_conn,
    )


class RecordEditRequest(BaseModel):
    """Body for POST /hoa/{id}/dre/extraction-runs/{run_id}/edits."""
    field_path: str
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    reason: Optional[str] = None


@router.post(
    "/hoa/{hoa_id}/dre/extraction-runs/{run_id}/edits",
    response_model=DREReviewEditResponse,
)
def post_record_edit(
    hoa_id: int,
    run_id: int,
    payload: RecordEditRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> DREReviewEditResponse:
    """Append a row to ``dre_review_edits`` for this run.

    The frontend Review Workbench calls this every time the operator
    changes an extracted value.
    """
    raw_conn = session.connection().connection
    try:
        get_extraction_run(
            property_id=hoa_id, extraction_run_id=run_id, connection=raw_conn,
        )
    except DREExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    actor = current_user.get("email") or current_user.get("name") or "unknown"
    return record_review_edit(
        dre_extraction_run_id=run_id,
        field_path=payload.field_path,
        old_value=payload.old_value,
        new_value=payload.new_value,
        reason=payload.reason,
        edited_by=str(actor),
        connection=raw_conn,
    )
