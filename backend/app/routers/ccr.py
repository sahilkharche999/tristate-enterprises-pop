"""CC&R / governing-document upload, extraction, and approval endpoints.

Mirrors the DRE router pattern (dre.py / dre_review.py / dre_approval.py) but
for document_type='ccr'. The review surface reuses the shared DRE Review
Workbench routes (GET /dre/extraction-runs/{id}, edits, field-sources) since
the data shape is identical — the CC&R sub-tab navigates to those routes with
the run id.

New CC&R-specific endpoints:
  POST   /hoa/{id}/ccr/upload                              → upload governing doc PDF
  POST   /hoa/{id}/ccr/documents/{doc_id}/extract          → schedule CC&R extraction
  GET    /hoa/{id}/ccr/documents                           → list CC&R documents
  GET    /hoa/{id}/ccr/extraction-runs                     → list CC&R extraction runs
  POST   /hoa/{id}/ccr/extraction-runs/{run_id}/factors    → save operator per-unit factors
  GET    /hoa/{id}/ccr/extraction-runs/{run_id}/factors    → get current per-unit factors
  POST   /hoa/{id}/ccr/extraction-runs/{run_id}/approve    → promote to AssessmentSetup
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai_implementation.db import get_session
from app.auth.dependencies import get_current_user
from app.services.ccr_approval_service import (
    CCRUnitFactor,
    MissingUnitFactors,
    approve_ccr_extraction_run,
    get_operator_unit_factors,
    save_operator_unit_factors,
)
from app.services.ccr_extraction_service import (
    CCRExtractionPreconditionError,
    lookup_ccr_document,
    run_ccr_extraction_job,
    schedule_ccr_extraction_run,
)
from app.services.dre_approval_service import (
    DREApprovalResponse,
    ExtractionRunAlreadyPromoted,
    ExtractionRunNotApprovable,
    ExtractionRunNotFound,
    SetupTypeLiteral,
)
from app.services.dre_query_service import (
    DREDocumentResponse,
    DREExtractionRunListItem,
    list_ccr_documents,
    list_ccr_extraction_runs,
)
from app.services.dre_upload_service import (
    DREUploadResponse,
    PropertyNotFound,
    upload_dre_document,
)


router = APIRouter(tags=["CC&R"])


def _actor_email(actor: dict) -> str:
    return str(actor.get("email") or actor.get("name") or "unknown")


# ── Upload ─────────────────────────────────────────────────────────────────


@router.post("/hoa/{hoa_id}/ccr/upload", response_model=DREUploadResponse)
async def upload_ccr(
    hoa_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> DREUploadResponse:
    """Upload a CC&R / governing-document PDF and create a new dre_documents row
    with document_type='ccr'.
    """
    file_bytes = await file.read()
    raw_conn = session.connection().connection
    try:
        result = upload_dre_document(
            property_id=hoa_id,
            file_bytes=file_bytes,
            original_filename=file.filename or "governing_doc.pdf",
            uploaded_by=_actor_email(current_user),
            connection=raw_conn,
        )
    except PropertyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Tag the newly-uploaded document as 'ccr'.
    raw_conn.execute(
        "UPDATE dre_documents SET document_type = 'ccr' WHERE id = ?",
        (result.dre_document_id,),
    )
    raw_conn.commit()
    return result


# ── Extraction trigger ──────────────────────────────────────────────────────


class CCRExtractionScheduledResponse(BaseModel):
    document_id: int
    extraction_run_id: int
    job_status: str
    status: str = "scheduled"


@router.post(
    "/hoa/{hoa_id}/ccr/documents/{document_id}/extract",
    response_model=CCRExtractionScheduledResponse,
    status_code=202,
)
def trigger_ccr_extraction(
    hoa_id: int,
    document_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> CCRExtractionScheduledResponse:
    """Schedule a background Gemini extraction for an uploaded CC&R document.

    Returns 202 immediately; the UI polls /hoa/{id}/ccr/extraction-runs.
    """
    _ = current_user
    raw_conn = session.connection().connection
    try:
        file_id, _file_name = lookup_ccr_document(
            property_id=hoa_id,
            document_id=document_id,
            connection=raw_conn,
        )
    except CCRExtractionPreconditionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    scheduled = schedule_ccr_extraction_run(
        property_id=hoa_id,
        document_id=document_id,
        connection=raw_conn,
    )
    if scheduled.status == "scheduled":
        background_tasks.add_task(
            run_ccr_extraction_job,
            run_id=scheduled.extraction_run_id,
            property_id=hoa_id,
            document_id=document_id,
            file_id=file_id,
        )
    return CCRExtractionScheduledResponse(
        document_id=document_id,
        extraction_run_id=scheduled.extraction_run_id,
        job_status=scheduled.job_status,
        status=scheduled.status,
    )


# ── Listings ────────────────────────────────────────────────────────────────


@router.get(
    "/hoa/{hoa_id}/ccr/documents",
    response_model=list[DREDocumentResponse],
)
def list_hoa_ccr_documents(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> list[DREDocumentResponse]:
    return list_ccr_documents(
        property_id=hoa_id, connection=session.connection().connection,
    )


@router.get(
    "/hoa/{hoa_id}/ccr/extraction-runs",
    response_model=list[DREExtractionRunListItem],
)
def list_hoa_ccr_extraction_runs(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> list[DREExtractionRunListItem]:
    return list_ccr_extraction_runs(
        property_id=hoa_id, connection=session.connection().connection,
    )


# ── Per-unit factor editor ───────────────────────────────────────────────────


class UnitFactorEntry(BaseModel):
    unit_number: str
    square_feet: Optional[Decimal] = None
    ownership_percent: Optional[Decimal] = None


class SaveFactorsRequest(BaseModel):
    factors: list[UnitFactorEntry]


class SaveFactorsResponse(BaseModel):
    extraction_run_id: int
    factors_saved: int


@router.post(
    "/hoa/{hoa_id}/ccr/extraction-runs/{run_id}/factors",
    response_model=SaveFactorsResponse,
)
def save_unit_factors(
    hoa_id: int,
    run_id: int,
    payload: SaveFactorsRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> SaveFactorsResponse:
    """Save (overwrite) operator-entered per-unit allocation factors for a CC&R run.

    Accepts the full factor set on each call — partial updates not supported.
    """
    _ = current_user
    raw_conn = session.connection().connection
    try:
        count = save_operator_unit_factors(
            extraction_run_id=run_id,
            property_id=hoa_id,
            factors=[
                CCRUnitFactor(
                    unit_number=f.unit_number,
                    square_feet=f.square_feet,
                    ownership_percent=f.ownership_percent,
                )
                for f in payload.factors
            ],
            connection=raw_conn,
        )
        raw_conn.commit()
    except ExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SaveFactorsResponse(extraction_run_id=run_id, factors_saved=count)


@router.get(
    "/hoa/{hoa_id}/ccr/extraction-runs/{run_id}/factors",
    response_model=dict,
)
def get_unit_factors(
    hoa_id: int,
    run_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Fetch current operator-entered per-unit factors for a CC&R extraction run."""
    _ = current_user
    raw_conn = session.connection().connection
    return get_operator_unit_factors(
        extraction_run_id=run_id, connection=raw_conn
    )


# ── Approve / promote ───────────────────────────────────────────────────────


class CCRApprovalRequest(BaseModel):
    setup_type: SetupTypeLiteral


@router.post(
    "/hoa/{hoa_id}/ccr/extraction-runs/{run_id}/approve",
    response_model=DREApprovalResponse,
)
def approve_ccr_run(
    hoa_id: int,
    run_id: int,
    payload: CCRApprovalRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> DREApprovalResponse:
    """Promote a reviewed CC&R extraction run into a live AssessmentSetup.

    Returns 422 when a proportional pool has no per-unit factors — the operator
    must enter them via POST /factors before re-attempting.
    """
    raw_conn = session.connection().connection
    try:
        return approve_ccr_extraction_run(
            property_id=hoa_id,
            extraction_run_id=run_id,
            setup_type=payload.setup_type,
            reviewed_by=_actor_email(current_user),
            connection=raw_conn,
        )
    except ExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExtractionRunAlreadyPromoted as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExtractionRunNotApprovable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MissingUnitFactors as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "missing_pool_keys": exc.missing_pool_keys,
            },
        ) from exc
