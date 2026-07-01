"""DRE extraction-run approval endpoint (Phase 4.2 + 5.9)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..dre_extraction.promotion import (
    EditedEntityFailedToPromote,
    MissingUnitFactors,
    UnresolvableReviewEdit,
)
from ..services.dre_approval_service import (
    DREApprovalResponse,
    DREDemotionResponse,
    ExtractionRunAlreadyPromoted,
    ExtractionRunNotApprovable,
    ExtractionRunNotFound,
    ExtractionRunNotPromoted,
    ReopenRepromoteResponse,
    SetupPinnedByFinalizedPackage,
    SetupTypeLiteral,
    approve_extraction_run,
    demote_extraction_run,
    reopen_and_repromote,
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
    except UnresolvableReviewEdit as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "unresolvable_field_paths": exc.unresolvable_field_paths,
            },
        ) from exc
    except EditedEntityFailedToPromote as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "failed_entities": exc.entity_refs},
        ) from exc
    except MissingUnitFactors as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "missing_pool_keys": exc.missing_pool_keys},
        ) from exc


@router.post(
    "/hoa/{hoa_id}/dre/extraction-runs/{run_id}/demote",
    response_model=DREDemotionResponse,
)
def demote_dre_extraction_run(
    hoa_id: int,
    run_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> DREDemotionResponse:
    """Reverse a promotion: unseat a wrongly-promoted DRE extraction run.

    Supersedes the promoted AssessmentSetup, restores the prior one (or
    clears the property's default so a different document can be promoted
    instead), and reverts the run to ``review_status='approved'``. A run
    that was never promoted returns ``400``; a setup pinned by a finalized
    AnnualPackage returns ``409``; a missing run returns ``404``.
    """
    raw_conn = session.connection().connection
    try:
        return demote_extraction_run(
            property_id=hoa_id,
            extraction_run_id=run_id,
            reviewed_by=_actor_email(current_user),
            connection=raw_conn,
        )
    except ExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExtractionRunNotPromoted as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SetupPinnedByFinalizedPackage as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "package_ids": exc.package_ids},
        ) from exc


@router.post(
    "/hoa/{hoa_id}/dre/extraction-runs/{run_id}/repromote",
    response_model=ReopenRepromoteResponse,
)
def repromote_dre_extraction_run(
    hoa_id: int,
    run_id: int,
    payload: DREApprovalRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> ReopenRepromoteResponse:
    """Correct an already-promoted DRE run without a new extraction/upload.

    Re-applies the run's original extraction plus whatever review edits
    exist now, supersedes the current AssessmentSetup, and promotes a
    fresh one. Unlike the plain approve endpoint, this explicitly targets
    an already-promoted run (``ExtractionRunNotPromoted`` returns 400 if
    the run was never promoted in the first place — use approve instead).
    """
    raw_conn = session.connection().connection
    try:
        return reopen_and_repromote(
            property_id=hoa_id,
            extraction_run_id=run_id,
            setup_type=payload.setup_type,
            reviewed_by=_actor_email(current_user),
            connection=raw_conn,
        )
    except ExtractionRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExtractionRunNotPromoted as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExtractionRunNotApprovable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnresolvableReviewEdit as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "unresolvable_field_paths": exc.unresolvable_field_paths,
            },
        ) from exc
    except EditedEntityFailedToPromote as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "failed_entities": exc.entity_refs},
        ) from exc
    except MissingUnitFactors as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "missing_pool_keys": exc.missing_pool_keys},
        ) from exc
