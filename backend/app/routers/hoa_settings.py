"""GET / PUT /hoa/{hoa_id}/settings/disclosure for the disclosure-package config."""
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..ai_implementation.db.models import Property
from ..auth.dependencies import get_current_user
from ..services import hoa_settings_service

router = APIRouter(prefix="/hoa", tags=["HOA Settings"])


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "property_id": row.property_id,
        "management_company": row.management_company,
        "management_company_address": row.management_company_address,
        "management_company_phone": row.management_company_phone,
        "management_company_fax": row.management_company_fax,
        "management_company_web": row.management_company_web,
        "cpa_firm_name": row.cpa_firm_name,
        "cpa_firm_address": row.cpa_firm_address,
        "reserve_study_expert_name": row.reserve_study_expert_name,
        "reserve_cash_balance_eoy_prior": row.reserve_cash_balance_eoy_prior,
        "fund_balance_boy_operations": row.fund_balance_boy_operations,
        "monthly_assessment_per_unit_prior": row.monthly_assessment_per_unit_prior,
        "interest_rate_after_tax": row.interest_rate_after_tax,
        "replacement_cost_increase_rate": row.replacement_cost_increase_rate,
        "assessment_increase_schedule_json": row.assessment_increase_schedule_json,
        "letter_signed_by": row.letter_signed_by,
    }


@router.get("/{hoa_id}/settings/disclosure")
async def get_disclosure_settings(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    row = hoa_settings_service.get_or_create(session, hoa_id=hoa_id)
    return _row_to_dict(row)


@router.put("/{hoa_id}/settings/disclosure")
async def put_disclosure_settings(
    hoa_id: int,
    payload: dict = Body(...),
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001
):
    if not session.query(Property).filter_by(id=hoa_id).one_or_none():
        raise HTTPException(status_code=404, detail=f"HOA not found: {hoa_id}")
    try:
        row = hoa_settings_service.update(session, hoa_id=hoa_id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _row_to_dict(row)
