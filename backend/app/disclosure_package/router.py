"""FastAPI router for disclosure-package generation (CONTEXT D-16).

Endpoints:
    POST /api/disclosure-package/generate           → 202 {id, status, ...}
    GET  /api/disclosure-package/{job_id}/status    → 200 DisclosurePackageJobResponse
    GET  /api/disclosure-package/{job_id}/download  → 200 application/pdf
    GET  /api/disclosure-package/{job_id}/audit     → 200 application/json

All endpoints require auth (T-11-02, REQ-D11-017). Job access is
ownership-checked (T-11-01) — cross-user reads return 404, NOT 403.

Filename for download: ``{hoa-slug}-{fiscal_year}-disclosure-package.pdf``
where ``hoa-slug`` is derived from the job's HOA name (UI-SPEC OQ-7).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_201_CREATED,
    HTTP_202_ACCEPTED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_410_GONE,
    HTTP_413_CONTENT_TOO_LARGE,
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


def _download_filename(session: Session, job) -> str:
    """Build the download filename from the job's HOA name and fiscal year.

    Derives the HOA slug from the property record (job.property_id) rather
    than hardcoding a single HOA — every HOA gets a correctly-named file.
    Falls back to a generic slug when the property can't be resolved.
    """
    hoa_name = ""
    try:
        prop = session.get(Property, job.property_id)
        if prop is not None:
            hoa_name = (prop.name or "").strip()
    except Exception:  # pragma: no cover - defensive; name is cosmetic
        hoa_name = ""

    slug = re.sub(r"[^A-Za-z0-9]+", "-", hoa_name).strip("-").lower()
    if not slug:
        slug = "hoa"
    return f"{slug}-{job.fiscal_year}-disclosure-package.pdf"


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
    filename = _download_filename(session, job)
    return FileResponse(
        path=str(pdf_path),
        filename=filename,
        media_type="application/pdf",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Per-HOA static-appendix uploads
#
# These endpoints let an authenticated user manage the PDFs that get appended
# after the generated pages of a disclosure package. Files live under
# BUDGET_STORAGE_ROOT/disclosure-package-appendices/{hoa_id}/ and survive across
# generation jobs. The compiler appends every PDF it finds in this directory at
# generate time (sorted by filename). T-11-05 path-traversal mitigations live in
# service._sanitize_appendix_filename.
# ─────────────────────────────────────────────────────────────────────────────

# Appendix uploads cap. Cohesive with WeasyPrint render budget (CONTEXT D-09);
# anything bigger than 25 MB is almost certainly a non-PDF mistake.
_APPENDIX_MAX_BYTES = 25 * 1024 * 1024


def _ensure_hoa_supported(session: Session, hoa_id: int) -> Property:
    property_row = (
        session.query(Property)
        .filter(Property.id == hoa_id)
        .one_or_none()
    )
    if property_row is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail=f"HOA not found: {hoa_id}"
        )
    if not dp_service.is_supported_hoa(property_row.name):
        raise HTTPException(
            status_code=HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Disclosure-package appendices are not yet available for "
                f"{property_row.name}"
            ),
        )
    return property_row


@router.get("/hoa/{hoa_id}/appendices")
async def list_hoa_appendices(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001 — auth gate
) -> JSONResponse:
    """List uploaded static appendices for an HOA, sorted by filename."""
    _ensure_hoa_supported(session, hoa_id)
    return JSONResponse(content={"items": dp_service.list_appendices(hoa_id)})


@router.post("/hoa/{hoa_id}/appendices", status_code=HTTP_201_CREATED)
async def upload_hoa_appendix(
    hoa_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001 — auth gate
) -> JSONResponse:
    """Upload a single PDF appendix for an HOA. Replaces by filename."""
    _ensure_hoa_supported(session, hoa_id)
    content = await file.read()
    if len(content) > _APPENDIX_MAX_BYTES:
        raise HTTPException(
            status_code=HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Appendix exceeds {_APPENDIX_MAX_BYTES} bytes",
        )
    try:
        entry = dp_service.save_appendix(
            hoa_id, filename=file.filename or "", content=content
        )
    except ValueError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc))
    return JSONResponse(status_code=HTTP_201_CREATED, content=entry)


@router.delete(
    "/hoa/{hoa_id}/appendices/{filename}", status_code=HTTP_204_NO_CONTENT
)
async def delete_hoa_appendix(
    hoa_id: int,
    filename: str,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001 — auth gate
):
    """Delete a previously uploaded appendix. 404 if it doesn't exist."""
    _ensure_hoa_supported(session, hoa_id)
    try:
        deleted = dp_service.delete_appendix(hoa_id, filename)
    except ValueError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc))
    if not deleted:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND, detail=f"Appendix not found: {filename}"
        )
    return JSONResponse(status_code=HTTP_204_NO_CONTENT, content=None)


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
