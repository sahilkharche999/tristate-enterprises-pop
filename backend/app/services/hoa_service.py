"""Query and update helpers for Phase 1 HOA settings."""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..assessment_mode import (
    ASSESSMENT_MODE_VARIABLE,
    normalize_assessment_mode,
)
from ..ai_implementation.db import Property
from ..models.hoa import HOACreateRequest, HOADetail, HOAListItem, HOAUpdateRequest
from . import app_settings_service


def _derive_fiscal_year_end(start_month: int) -> int:
    """A fiscal year is always 12 months: start=4 (April) → end=3 (March)."""
    return ((start_month + 10) % 12) + 1


def _hoa_payload(property_row: Property, *, reserve_inflation_rate: float) -> dict[str, object]:
    return {
        "id": property_row.id,
        "hoa_code": property_row.hoa_code or "",
        "name": property_row.name,
        "tax_id": property_row.tax_id or "",
        "units": property_row.units or 0,
        "reserve_inflation_rate": reserve_inflation_rate,
        "fiscal_year_start_month": property_row.fiscal_year_start_month or 1,
        "fiscal_year_end_month": property_row.fiscal_year_end_month or 12,
        "city": property_row.city or "",
        "portfolio_year": property_row.portfolio_year,
        "workflow_status": property_row.workflow_status or "Not Started",
        "assessment_mode": normalize_assessment_mode(
            getattr(property_row, "assessment_mode", ASSESSMENT_MODE_VARIABLE)
        ),
    }


def _serialize_hoa(property_row: Property, *, reserve_inflation_rate: float) -> HOADetail:
    return HOADetail(
        **_hoa_payload(property_row, reserve_inflation_rate=reserve_inflation_rate),
        created_at=property_row.created_at,
    )


def list_hoas(session: Session) -> List[HOAListItem]:
    reserve_inflation_rate = app_settings_service.get_global_reserve_inflation_rate(session)
    rows = session.scalars(select(Property).order_by(Property.id)).all()
    return [
        HOAListItem(**_hoa_payload(row, reserve_inflation_rate=reserve_inflation_rate))
        for row in rows
    ]


def get_hoa(session: Session, hoa_id: int) -> Optional[HOADetail]:
    row = session.get(Property, hoa_id)
    if row is None:
        return None
    reserve_inflation_rate = app_settings_service.get_global_reserve_inflation_rate(session)
    return _serialize_hoa(row, reserve_inflation_rate=reserve_inflation_rate)


def create_hoa(session: Session, payload: HOACreateRequest) -> HOADetail:
    normalized_name = payload.name.strip()
    existing = session.scalar(select(Property.id).where(Property.name == normalized_name))
    if existing is not None:
        raise ValueError("duplicate_hoa_name")

    package_year = (
        int(payload.portfolio_year)
        if payload.portfolio_year is not None
        else datetime.now().year
    )
    row = Property(
        name=normalized_name,
        units=payload.units if payload.units is not None else 0,
        reserve_inflation_rate=0.0,
        fiscal_year_start_month=payload.fiscal_year_start_month,
        fiscal_year_end_month=_derive_fiscal_year_end(payload.fiscal_year_start_month),
        city=(payload.city or "").strip(),
        portfolio_year=package_year,
        workflow_status="Not Started",
        assessment_mode=normalize_assessment_mode(payload.assessment_mode),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    reserve_inflation_rate = app_settings_service.get_global_reserve_inflation_rate(session)
    return _serialize_hoa(row, reserve_inflation_rate=reserve_inflation_rate)


def update_hoa(session: Session, hoa_id: int, payload: HOAUpdateRequest) -> Optional[HOADetail]:
    row = session.get(Property, hoa_id)
    if row is None:
        return None

    if payload.hoa_code is not None and payload.hoa_code.strip():
        row.hoa_code = payload.hoa_code.strip()
    row.name = payload.name.strip()
    if payload.tax_id is not None and payload.tax_id.strip():
        row.tax_id = payload.tax_id.strip()
    if payload.units is not None:
        row.units = payload.units
    row.fiscal_year_start_month = payload.fiscal_year_start_month
    row.fiscal_year_end_month = _derive_fiscal_year_end(payload.fiscal_year_start_month)
    if payload.city is not None:
        row.city = payload.city.strip()
    if payload.reserve_inflation_rate is not None:
        row.reserve_inflation_rate = payload.reserve_inflation_rate
    if payload.assessment_mode is not None:
        row.assessment_mode = normalize_assessment_mode(payload.assessment_mode)
    if payload.portfolio_year is not None:
        row.portfolio_year = int(payload.portfolio_year)
    session.commit()
    session.refresh(row)
    # Prefer the HOA-saved rate when non-zero so the per-HOA value
    # round-trips through the PUT/GET response; fall back to the global
    # app-level default for HOAs that haven't set one.
    hoa_rate = getattr(row, "reserve_inflation_rate", None) or 0.0
    if hoa_rate > 0.0:
        reserve_inflation_rate = hoa_rate
    else:
        reserve_inflation_rate = app_settings_service.get_global_reserve_inflation_rate(session)
    return _serialize_hoa(row, reserve_inflation_rate=reserve_inflation_rate)
