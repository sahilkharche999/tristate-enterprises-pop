"""Protected app-level settings endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..models.app_settings import AppSettingsPayload
from ..services import app_settings_service

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
