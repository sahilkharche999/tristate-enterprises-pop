"""Manual assessment setup entry endpoint — for a property with no
DRE/CC&R extraction run on file.

Creates a synthetic ``dre_extraction_runs`` row from operator-entered
pools/groups/units; the caller then approves it through the existing
DRE or CC&R approve endpoints exactly like any other run (see
``manual_assessment_setup_service`` for why there's no separate
promotion path).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..dre_extraction.schemas import PromptSetupType
from ..services.manual_assessment_setup_service import (
    ManualExtractionRunResponse,
    ManualGroupEntry,
    ManualPoolEntry,
    ManualUnitEntry,
    PropertyNotFound,
    create_manual_extraction_run,
)


router = APIRouter(tags=["Manual Assessment Setup"])


def _actor_email(actor: dict) -> str:
    return str(actor.get("email") or actor.get("name") or "unknown")


class ManualSetupEntryRequest(BaseModel):
    setup_type: PromptSetupType
    pools: list[ManualPoolEntry]
    groups: list[ManualGroupEntry] = []
    units: list[ManualUnitEntry] = []


@router.post(
    "/hoa/{hoa_id}/assessment-setup/manual",
    response_model=ManualExtractionRunResponse,
    status_code=201,
)
def create_manual_setup_entry(
    hoa_id: int,
    payload: ManualSetupEntryRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> ManualExtractionRunResponse:
    """Create a manually-entered extraction run for a property with no
    DRE/CC&R document on file.

    Returns the new ``extraction_run_id``; the operator reviews it in the
    same Review Workbench and approves it via the existing DRE/CC&R
    approve endpoints (use the CC&R endpoint for a ``per_unit`` setup to
    get the proportional-pool missing-factor guard).
    """
    raw_conn = session.connection().connection
    try:
        return create_manual_extraction_run(
            property_id=hoa_id,
            setup_type=payload.setup_type,
            pools=payload.pools,
            groups=payload.groups,
            units=payload.units,
            created_by=_actor_email(current_user),
            connection=raw_conn,
        )
    except PropertyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
