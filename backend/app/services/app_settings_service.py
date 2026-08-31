"""Helpers for app-level settings that should not live on a single HOA row."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from app.disclosure_package.section_order import (
    FIRM_SIGNATURE_SETTING_KEY,
    HIDDEN_SETTING_KEY,
    ORDER_SETTING_KEY,
    catalog_for_api,
    resolve_generated_templates,
)
from app.services import signature_storage

from ..ai_implementation.db import AppSetting
from ..models.app_settings import AppSettingsPayload, SectionCatalogItem

_GLOBAL_RESERVE_INFLATION_KEY = "global_reserve_inflation_rate"


def _get_text(session: Session, key: str) -> Optional[str]:
    row = session.get(AppSetting, key)
    if row is None or row.value_text in (None, ""):
        return None
    return row.value_text


def _set_text(session: Session, key: str, value: Optional[str]) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        session.add(row)
    row.value_text = value
    session.flush()


def _get_json_list(session: Session, key: str) -> list[str]:
    raw = _get_text(session, key)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def get_global_reserve_inflation_rate(session: Session) -> float:
    raw = _get_text(session, _GLOBAL_RESERVE_INFLATION_KEY)
    if raw is None:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if value >= 0 else 0.0


def get_firm_signature_filename(session: Session) -> Optional[str]:
    return _get_text(session, FIRM_SIGNATURE_SETTING_KEY)


def set_firm_signature_filename(session: Session, filename: Optional[str]) -> None:
    _set_text(session, FIRM_SIGNATURE_SETTING_KEY, filename)
    session.commit()


def get_app_settings(session: Session) -> AppSettingsPayload:
    saved_order = _get_json_list(session, ORDER_SETTING_KEY)
    hidden = _get_json_list(session, HIDDEN_SETTING_KEY)
    firm_filename = get_firm_signature_filename(session)
    return AppSettingsPayload(
        global_reserve_inflation_rate=get_global_reserve_inflation_rate(session),
        disclosure_section_order=resolve_generated_templates(saved_order, hidden=()),
        disclosure_hidden_sections=[
            key
            for key in hidden
            if key in set(resolve_generated_templates(saved_order, hidden=()))
        ],
        section_catalog=[
            SectionCatalogItem(**item) for item in catalog_for_api(saved_order, hidden)
        ],
        has_firm_signature=signature_storage.signature_exists(firm_filename)
        or bool(signature_storage.find_firm_signature_on_disk()),
    )


def update_app_settings(session: Session, payload: AppSettingsPayload) -> AppSettingsPayload:
    if payload.global_reserve_inflation_rate is not None:
        _set_text(
            session,
            _GLOBAL_RESERVE_INFLATION_KEY,
            str(payload.global_reserve_inflation_rate),
        )
    if payload.disclosure_section_order is not None:
        _set_text(
            session,
            ORDER_SETTING_KEY,
            json.dumps(list(payload.disclosure_section_order)),
        )
    if payload.disclosure_hidden_sections is not None:
        _set_text(
            session,
            HIDDEN_SETTING_KEY,
            json.dumps(list(payload.disclosure_hidden_sections)),
        )
    session.commit()
    return get_app_settings(session)
