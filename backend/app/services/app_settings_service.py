"""Helpers for app-level settings that should not live on a single HOA row."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..ai_implementation.db import AppSetting
from ..models.app_settings import AppSettingsPayload

_GLOBAL_RESERVE_INFLATION_KEY = "global_reserve_inflation_rate"


def get_global_reserve_inflation_rate(session: Session) -> float:
    row = session.get(AppSetting, _GLOBAL_RESERVE_INFLATION_KEY)
    if row is None or row.value_text in (None, ""):
        return 0.0
    try:
        value = float(row.value_text)
    except (TypeError, ValueError):
        return 0.0
    return value if value >= 0 else 0.0


def get_app_settings(session: Session) -> AppSettingsPayload:
    return AppSettingsPayload(
        global_reserve_inflation_rate=get_global_reserve_inflation_rate(session),
    )


def update_app_settings(session: Session, payload: AppSettingsPayload) -> AppSettingsPayload:
    row = session.get(AppSetting, _GLOBAL_RESERVE_INFLATION_KEY)
    if row is None:
        row = AppSetting(key=_GLOBAL_RESERVE_INFLATION_KEY)
        session.add(row)
    row.value_text = str(payload.global_reserve_inflation_rate)
    session.commit()
    session.refresh(row)
    return get_app_settings(session)
