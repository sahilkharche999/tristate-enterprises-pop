"""FastAPI router for disclosure-package generation (CONTEXT D-16).

Endpoints:
    POST /api/disclosure-package/generate           → 202 {id, status, ...}
    GET  /api/disclosure-package/{job_id}/status    → 200 DisclosurePackageJobResponse
    GET  /api/disclosure-package/{job_id}/download  → 200 application/pdf
    GET  /api/disclosure-package/{job_id}/audit     → 200 application/json

All endpoints require auth (T-11-02, REQ-D11-017). Job access is
ownership-checked (T-11-01) — cross-user reads return 404, NOT 403.

Filename for download: ``old-mill-{fiscal_year}-disclosure-package.pdf``
(UI-SPEC OQ-7).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_202_ACCEPTED,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_410_GONE,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_501_NOT_IMPLEMENTED,
)

from ..ai_implementation.db import get_session
from ..ai_implementation.db.models import Property
from ..auth.dependencies import get_current_user
from . import service as dp_service
from .schemas import (
    DisclosurePackageJobResponse,
    GenerateDisclosurePackageRequest,
)

router = APIRouter(prefix="/api/disclosure-package", tags=["Disclosure Package"])
logger = logging.getLogger(__name__)


def _session_factory_from(session: Session):
    """Return a callable that opens fresh sessions for the BackgroundTask.

    BackgroundTasks runs after the request session closes, so we cannot
    reuse `session` directly. We resolve `SessionLocal` lazily so test
    monkeypatching of `app.ai_implementation.db.session.SessionLocal`
    (used by the conftest `client` fixture) is honored at task time.
    """
    from ..ai_implementation.db import session as session_module

    def _factory():
        return session_module.SessionLocal()

    return _factory


@router.post("/generate", status_code=HTTP_202_ACCEPTED)
async def generate_disclosure_package(
    payload: GenerateDisclosurePackageRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Kick off a disclosure-package render. Returns 202 + job_id immediately
    (UI-SPEC §8.1 step 1). The render runs in a BackgroundTask so the HTTP
    request returns in <100 ms regardless of render duration.
    """
    try:
        property_row = (
            session.query(Property)
            .filter(Property.id == payload.hoa_id)
            .one_or_none()
        )
        if property_row is None:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"HOA not found: {payload.hoa_id}",
            )
        if not dp_service.is_supported_hoa(property_row.name):
            # REQ-D11-016: non-Old-Mill returns 501 Not Implemented
            raise HTTPException(
                status_code=HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    "Disclosure package generation is not yet available for "
                    f"{property_row.name}"
                ),
            )

        job = dp_service.create_job(
            session,
            hoa_id=payload.hoa_id,
            fiscal_year=payload.fiscal_year,
            current_user=current_user,
        )

        background_tasks.add_task(
            dp_service.run_render_job,
            job.id,
            payload.hoa_id,
            payload.fiscal_year,
            session_factory=_session_factory_from(session),
        )
        return JSONResponse(
            status_code=HTTP_202_ACCEPTED,
            content={
                "id": job.id,
                "status": job.status,
                "fiscal_year": job.fiscal_year,
                "property_id": job.property_id,
            },
        )
    except HTTPException:
        raise
    except ValueError as exc:
        # T-11-06: a running job already exists for (hoa_id, fiscal_year)
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in generate_disclosure_package")
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        )


@router.get(
    "/{job_id}/status",
    response_model=DisclosurePackageJobResponse,
)
async def get_job_status(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> DisclosurePackageJobResponse:
    """Return current status for a job. Cross-user reads → 404 (T-11-01)."""
    try:
        job = dp_service.assert_ownership(
            session, job_id=job_id, current_user=current_user
        )
    except LookupError:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail=f"Job not found: {job_id}"
        )
    return DisclosurePackageJobResponse.model_validate(job)


@router.get("/{job_id}/download")
async def download_job_pdf(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> FileResponse:
    """Stream the rendered PDF. 409 if job not yet complete; 410 if file
    has been pruned from disk.
    """
    try:
        job = dp_service.assert_ownership(
            session, job_id=job_id, current_user=current_user
        )
    except LookupError:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail=f"Job not found: {job_id}"
        )
    if job.status != "completed" or not job.output_path:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=f"Job is not complete: status={job.status}",
        )
    pdf_path = Path(job.output_path)
    if not pdf_path.exists():
        raise HTTPException(
            status_code=HTTP_410_GONE,
            detail="Output file is no longer available",
        )
    filename = f"old-mill-{job.fiscal_year}-disclosure-package.pdf"
    return FileResponse(
        path=str(pdf_path),
        filename=filename,
        media_type="application/pdf",
    )


@router.get("/{job_id}/audit")
async def get_job_audit(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Return the captured audit.json for a completed job (REQ-D11-011)."""
    try:
        job = dp_service.assert_ownership(
            session, job_id=job_id, current_user=current_user
        )
    except LookupError:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail=f"Job not found: {job_id}"
        )
    if not job.audit_path:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail="Audit log not yet available",
        )
    audit_path = Path(job.audit_path)
    if not audit_path.exists():
        raise HTTPException(
            status_code=HTTP_410_GONE,
            detail="Audit file is no longer available",
        )
    return JSONResponse(content=json.loads(audit_path.read_text()))


__all__ = ["router"]
