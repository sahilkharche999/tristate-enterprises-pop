"""DRE extraction-run approval endpoint (Phase 4.2 + 5.9)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..services.dre_approval_service import (
    DREApprovalResponse,
    ExtractionRunAlreadyPromoted,
    ExtractionRunNotApprovable,
    ExtractionRunNotFound,
    SetupTypeLiteral,
    approve_extraction_run,
)


router = APIRouter(tags=["DRE Approval"])


class DREApprovalRequest(BaseModel):
    setup_type: SetupTypeLiteral


def _actor_email(actor: dict) -> str:
    return str(actor.get("email") or actor.get("name") or "unknown")


@router.post(
    "/hoa/{hoa_id}/dre/extraction-runs/{run_id}/approve",
    response_model=DREApprovalResponse,
)
def approve_dre_extraction_run(
    hoa_id: int,
    run_id: int,
    payload: DREApprovalRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> DREApprovalResponse:
    """Promote a reviewed DRE extraction run into a live AssessmentSetup.

    Concurrent-approve protection: a second click on a run whose
    ``promoted_at`` is already set returns ``409 Conflict``. Rejected
    runs return ``400``. Missing runs return ``404``.
    """
    raw_conn = session.connection().connection
    try:
        return approve_extraction_run(
            property_id=hoa_id,
            extraction_run_id=run_id,
            setup_type=payload.setup_type,
            reviewed_by=_actor_email(current_user),
            connection=raw_conn,
        )
    except ExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExtractionRunAlreadyPromoted as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExtractionRunNotApprovable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
