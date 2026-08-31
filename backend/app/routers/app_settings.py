"""Protected app-level settings endpoints."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..models.app_settings import AppSettingsPayload
from ..services import app_settings_service, signature_storage

router = APIRouter(tags=["App Settings"])


@router.get("/app-settings", response_model=AppSettingsPayload)
async def get_app_settings(session: Session = Depends(get_session)) -> AppSettingsPayload:
    return app_settings_service.get_app_settings(session)


@router.put("/app-settings", response_model=AppSettingsPayload)
async def update_app_settings(
    payload: AppSettingsPayload,
    session: Session = Depends(get_session),
) -> AppSettingsPayload:
    return app_settings_service.update_app_settings(session, payload)


@router.post("/app-settings/signature", response_model=AppSettingsPayload)
async def upload_firm_signature(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> AppSettingsPayload:
    file_bytes = await file.read()
    try:
        relative = signature_storage.save_firm_signature(
            file_bytes=file_bytes,
            original_filename=file.filename or "signature.png",
        )
    except signature_storage.UnsupportedSignatureFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    previous = app_settings_service.get_firm_signature_filename(session)
    if previous and previous != relative:
        signature_storage.delete_signature(previous)
    app_settings_service.set_firm_signature_filename(session, relative)
    return app_settings_service.get_app_settings(session)


@router.delete("/app-settings/signature", response_model=AppSettingsPayload)
async def delete_firm_signature(
    session: Session = Depends(get_session),
) -> AppSettingsPayload:
    previous = app_settings_service.get_firm_signature_filename(session)
    signature_storage.delete_signature(previous)
    on_disk = signature_storage.find_firm_signature_on_disk()
    if on_disk:
        signature_storage.delete_signature(on_disk)
    app_settings_service.set_firm_signature_filename(session, None)
    return app_settings_service.get_app_settings(session)


@router.get("/app-settings/signature")
async def get_firm_signature(session: Session = Depends(get_session)) -> FileResponse:
    filename = app_settings_service.get_firm_signature_filename(session)
    chosen = signature_storage.resolve_signature_filename(firm_filename=filename)
    if not chosen:
        raise HTTPException(status_code=404, detail="No firm signature configured")
    return FileResponse(path=signature_storage.signature_path(chosen))
