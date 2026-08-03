"""Appendix-document REST endpoints (Phase 5.4).

CRUD operations on the per-HOA appendix manifest. Thin router that
delegates business logic to ``appendix_service``; auth applied per
endpoint via ``get_current_user``.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..disclosure_package.appendix_storage import appendix_file_path
from ..services.appendix_service import (
    AppendixDocumentResponse,
    AppendixNotFound,
    PropertyNotFound,
    get_appendix,
    list_appendices,
    retire_appendix,
    update_appendix,
    upload_appendix,
)


router = APIRouter(tags=["Appendices"])


def _actor_email(actor: dict) -> str:
    return str(actor.get("email") or actor.get("name") or "unknown")


class AppendixUpdateRequest(BaseModel):
    """Body for PUT /hoa/{hoa_id}/appendices/{appendix_id}.

    Every field is optional — the service applies just the supplied
    keys. ``expected_version`` is required for the optimistic-lock
    check; the frontend gets it from the GET response and sends it
    back on every PUT.
    """

    expected_version: int
    display_title: Optional[str] = None
    default_display_order: Optional[int] = None
    include_by_default: Optional[bool] = None
    cadence: Optional[Literal["persistent", "annual", "one_time"]] = None
    annual_year: Optional[int] = None
    valid_through_year: Optional[int] = None
    required_flag: Optional[bool] = None
    needs_cadence_review: Optional[bool] = None
    # null | "insurance" — omit field to leave unchanged; send null to clear
    package_role: Optional[str] = None
    package_role_set: Optional[bool] = None


@router.get(
    "/hoa/{hoa_id}/appendices", response_model=list[AppendixDocumentResponse]
)
def list_hoa_appendices(
    hoa_id: int,
    include_retired: bool = False,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> list[AppendixDocumentResponse]:
    raw_conn = session.connection().connection
    return list_appendices(
        property_id=hoa_id,
        connection=raw_conn,
        include_retired=include_retired,
    )


@router.post(
    "/hoa/{hoa_id}/appendices", response_model=AppendixDocumentResponse
)
async def upload_hoa_appendix(
    hoa_id: int,
    file: UploadFile = File(...),
    display_title: str = Form(...),
    cadence: Literal["persistent", "annual", "one_time"] = Form("persistent"),
    annual_year: Optional[int] = Form(None),
    valid_through_year: Optional[int] = Form(None),
    required_flag: bool = Form(False),
    include_by_default: bool = Form(True),
    package_role: Optional[str] = Form(None),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> AppendixDocumentResponse:
    file_bytes = await file.read()
    raw_conn = session.connection().connection
    try:
        return upload_appendix(
            property_id=hoa_id,
            file_bytes=file_bytes,
            original_filename=file.filename or "appendix.pdf",
            display_title=display_title,
            cadence=cadence,
            annual_year=annual_year,
            valid_through_year=valid_through_year,
            required_flag=required_flag,
            include_by_default=include_by_default,
            package_role=package_role,
            uploaded_by=_actor_email(current_user),
            connection=raw_conn,
        )
    except PropertyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/hoa/{hoa_id}/appendices/{appendix_id}/download")
def download_hoa_appendix(
    hoa_id: int,
    appendix_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
) -> FileResponse:
    """Download the file for an appendix document."""
    raw_conn = session.connection().connection
    try:
        doc = get_appendix(property_id=hoa_id, appendix_id=appendix_id, connection=raw_conn)
    except AppendixNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = appendix_file_path(doc.file_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Appendix file not found on disk")
    return FileResponse(path=path, filename=doc.file_name, media_type="application/pdf")


@router.put(
    "/hoa/{hoa_id}/appendices/{appendix_id}",
    response_model=AppendixDocumentResponse,
)
def update_hoa_appendix(
    hoa_id: int,
    appendix_id: int,
    payload: AppendixUpdateRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> AppendixDocumentResponse:
    raw_conn = session.connection().connection
    try:
        # Pydantic: package_role in body means set (including null to clear)
        role_set = (
            payload.package_role_set
            if payload.package_role_set is not None
            else "package_role" in payload.model_fields_set
        )
        return update_appendix(
            property_id=hoa_id,
            appendix_id=appendix_id,
            expected_version=payload.expected_version,
            display_title=payload.display_title,
            default_display_order=payload.default_display_order,
            include_by_default=payload.include_by_default,
            cadence=payload.cadence,
            annual_year=payload.annual_year,
            valid_through_year=payload.valid_through_year,
            required_flag=payload.required_flag,
            needs_cadence_review=payload.needs_cadence_review,
            package_role=payload.package_role,
            package_role_set=bool(role_set),
            connection=raw_conn,
        )
    except AppendixNotFound as exc:
        # 409 on version mismatch, 404 otherwise
        if "version mismatch" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete(
    "/hoa/{hoa_id}/appendices/{appendix_id}",
    response_model=AppendixDocumentResponse,
)
def retire_hoa_appendix(
    hoa_id: int,
    appendix_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> AppendixDocumentResponse:
    """Retire (soft-delete) the appendix — prior packages still
    reference the row via ``annual_package_appendices``. A hard delete
    would orphan those, so we mark ``status='retired'`` instead.
    """
    raw_conn = session.connection().connection
    try:
        return retire_appendix(
            property_id=hoa_id,
            appendix_id=appendix_id,
            connection=raw_conn,
        )
    except AppendixNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
