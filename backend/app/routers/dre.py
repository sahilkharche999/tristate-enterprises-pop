"""DRE upload + extraction-trigger endpoints (Phase 3.1 + extraction-trigger gap).

The router is intentionally thin — it streams the uploaded bytes,
delegates to ``dre_upload_service`` for the business logic, and maps
service exceptions to HTTP errors. Auth is applied per-route via the
shared ``get_current_user`` dependency.

``POST /hoa/{id}/dre/documents/{document_id}/extract`` schedules a
background extraction job for an already-uploaded DRE document. The
endpoint returns 202 with the document_id immediately; the UI polls
``GET /hoa/{id}/dre/extraction-runs`` to discover the new run when
Gemini finishes.
"""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..services.dre_extraction_service import (
    DREExtractionPreconditionError,
    lookup_dre_document,
    run_extraction_job,
)
from ..services.dre_upload_service import (
    DREUploadResponse,
    PropertyNotFound,
    upload_dre_document,
)


router = APIRouter(tags=["DRE"])


class DREExtractionScheduledResponse(BaseModel):
    document_id: int
    status: str = "scheduled"


def _actor_email(actor: dict) -> str:
    return str(actor.get("email") or actor.get("name") or "unknown")


@router.post("/hoa/{hoa_id}/dre/upload", response_model=DREUploadResponse)
async def upload_dre(
    hoa_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> DREUploadResponse:
    """Upload a DRE PDF and create a new ``dre_documents`` row.

    Marks any prior ``active`` document for this HOA as
    ``superseded`` automatically.
    """
    file_bytes = await file.read()
    raw_conn = session.connection().connection
    try:
        return upload_dre_document(
            property_id=hoa_id,
            file_bytes=file_bytes,
            original_filename=file.filename or "dre.pdf",
            uploaded_by=_actor_email(current_user),
            connection=raw_conn,
        )
    except PropertyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/hoa/{hoa_id}/dre/documents/{document_id}/extract",
    response_model=DREExtractionScheduledResponse,
    status_code=202,
)
def trigger_dre_extraction(
    hoa_id: int,
    document_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> DREExtractionScheduledResponse:
    """Schedule a background Gemini extraction for an uploaded DRE document.

    Returns 202 immediately; the UI polls
    ``GET /hoa/{id}/dre/extraction-runs`` to surface the new run when
    extraction completes. Gemini calls typically take ~30–90 seconds.

    Pre-conditions checked synchronously:
    * The ``dre_documents`` row exists and belongs to ``hoa_id``.
    * The stored PDF file is still on disk.
    Both raise 404 / 400 before the background task is scheduled.
    """
    _ = current_user  # auth via dependency; identity not needed in the task
    raw_conn = session.connection().connection
    try:
        file_id, _file_name = lookup_dre_document(
            property_id=hoa_id,
            dre_document_id=document_id,
            connection=raw_conn,
        )
    except DREExtractionPreconditionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    background_tasks.add_task(
        run_extraction_job,
        property_id=hoa_id,
        dre_document_id=document_id,
        file_id=file_id,
    )
    return DREExtractionScheduledResponse(document_id=document_id)
