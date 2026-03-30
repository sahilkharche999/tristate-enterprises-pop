"""Query and update helpers for Phase 1 HOA settings."""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_implementation.db import Property
from ..models.hoa import HOACreateRequest, HOADetail, HOAListItem, HOAUpdateRequest


def _hoa_payload(property_row: Property) -> dict[str, object]:
    return {
        "id": property_row.id,
        "hoa_code": property_row.hoa_code or "",
        "name": property_row.name,
        "tax_id": property_row.tax_id or "",
        "units": property_row.units or 0,
        "reserve_inflation_rate": property_row.reserve_inflation_rate or 0.0,
        "fiscal_year_start_month": property_row.fiscal_year_start_month or 1,
        "fiscal_year_end_month": property_row.fiscal_year_end_month or 12,
        "city": property_row.city or "",
        "portfolio_year": property_row.portfolio_year,
        "workflow_status": property_row.workflow_status or "Not Started",
    }


def _serialize_hoa(property_row: Property) -> HOADetail:
    return HOADetail(
        **_hoa_payload(property_row),
        created_at=property_row.created_at,
    )


def list_hoas(session: Session) -> List[HOAListItem]:
    rows = session.scalars(select(Property).order_by(Property.id)).all()
    return [HOAListItem(**_hoa_payload(row)) for row in rows]


def get_hoa(session: Session, hoa_id: int) -> Optional[HOADetail]:
    row = session.get(Property, hoa_id)
    if row is None:
        return None
    return _serialize_hoa(row)


def create_hoa(session: Session, payload: HOACreateRequest) -> HOADetail:
    normalized_name = payload.name.strip()
    existing = session.scalar(select(Property.id).where(Property.name == normalized_name))
    if existing is not None:
        raise ValueError("duplicate_hoa_name")

    row = Property(
        name=normalized_name,
        units=payload.units,
        reserve_inflation_rate=0.0,
        fiscal_year_start_month=payload.fiscal_year_start_month,
        fiscal_year_end_month=payload.fiscal_year_end_month,
        city="",
        portfolio_year=datetime.now().year,
        workflow_status="Not Started",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _serialize_hoa(row)


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
    if payload.reserve_inflation_rate is not None:
        row.reserve_inflation_rate = payload.reserve_inflation_rate
    row.fiscal_year_start_month = payload.fiscal_year_start_month
    row.fiscal_year_end_month = payload.fiscal_year_end_month
    session.commit()
    session.refresh(row)
    return _serialize_hoa(row)
