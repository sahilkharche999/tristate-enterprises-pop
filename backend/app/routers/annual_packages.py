"""AnnualPackage REST endpoints (Phase 4.8)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..optimistic_lock import require_if_match
from ..services.annual_package_service import (
    AnnualPackageNotFound,
    AnnualPackageResponse,
    FinalizeBlockedByPreflight,
    InvalidPackageStateTransition,
    PackageVersionMismatch,
    approve_annual_package,
    create_annual_package,
    finalize_annual_package,
    get_annual_package,
    list_annual_packages,
)


router = APIRouter(tags=["Annual Packages"])


class CreatePackageRequest(BaseModel):
    budget_year: int
    fiscal_year: int
    assessment_setup_id: Optional[int] = None
    regen_of_package_id: Optional[int] = None


class ApprovePackageRequest(BaseModel):
    approved_assessment_revenue_annual: Decimal


class FinalizePackageRequest(BaseModel):
    """Finalize request body (C2 — fix-critical-disclosure-integrity).

    Snapshot content is now assembled SERVER-SIDE from canonical DB state
    (``disclosure_package.service.assemble_finalize_snapshots``); the client
    cannot influence the frozen record. The legacy four payload fields are
    accepted-and-IGNORED so an older frontend keeps working during rollout —
    they are never read, never persisted.
    """

    assessment_setup: Any = None
    budget: Any = None
    reserve: Any = None
    appendix_manifest: Any = None


def _actor_email(actor: dict) -> str:
    return str(actor.get("email") or actor.get("name") or "unknown")


@router.get(
    "/hoa/{hoa_id}/annual-packages", response_model=list[AnnualPackageResponse]
)
def list_hoa_packages(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> list[AnnualPackageResponse]:
    return list_annual_packages(
        property_id=hoa_id, connection=session.connection().connection,
    )


@router.post(
    "/hoa/{hoa_id}/annual-packages", response_model=AnnualPackageResponse
)
def create_hoa_package(
    hoa_id: int,
    payload: CreatePackageRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> AnnualPackageResponse:
    return create_annual_package(
        property_id=hoa_id,
        budget_year=payload.budget_year,
        fiscal_year=payload.fiscal_year,
        assessment_setup_id=payload.assessment_setup_id,
        regen_of_package_id=payload.regen_of_package_id,
        connection=session.connection().connection,
    )


@router.get(
    "/hoa/{hoa_id}/annual-packages/{package_id}",
    response_model=AnnualPackageResponse,
)
def get_hoa_package(
    hoa_id: int,
    package_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> AnnualPackageResponse:
    try:
        return get_annual_package(
            property_id=hoa_id, package_id=package_id,
            connection=session.connection().connection,
        )
    except AnnualPackageNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/hoa/{hoa_id}/annual-packages/{package_id}/approve",
    response_model=AnnualPackageResponse,
)
def approve_hoa_package(
    hoa_id: int,
    package_id: int,
    payload: ApprovePackageRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
    if_match: int = Depends(require_if_match),
) -> AnnualPackageResponse:
    try:
        return approve_annual_package(
            property_id=hoa_id, package_id=package_id,
            approved_assessment_revenue_annual=payload.approved_assessment_revenue_annual,
            approved_by=_actor_email(current_user),
            connection=session.connection().connection,
            expected_version=if_match,
        )
    except AnnualPackageNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PackageVersionMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidPackageStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/hoa/{hoa_id}/annual-packages/{package_id}/finalize",
    response_model=AnnualPackageResponse,
)
def finalize_hoa_package(
    hoa_id: int,
    package_id: int,
    payload: Optional[FinalizePackageRequest] = None,  # legacy body ignored
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
    if_match: int = Depends(require_if_match),
) -> AnnualPackageResponse:
    """Freeze all five snapshot JSONs and transition to finalized (C2/C3).

    Snapshot content is assembled server-side from canonical DB state, and
    the blocking preflight gate (§5550 reserve-study age, §5570 special
    assessments, appendix cadence, specified-value placeholders) runs
    before anything is frozen — a failing gate returns HTTP 422 with the
    field paths and writes nothing. Any legacy request body is ignored.
    After this call the package is immutable; re-renders load from the
    frozen snapshots, not live state.
    """
    _ = payload  # legacy clients still send the retired four-field body
    from ..disclosure_package import service as dp_service

    def _blocking_preflight(fiscal_year: int) -> list:
        blocking, _warnings = dp_service.run_preflight(
            session, hoa_id, fiscal_year,
        )
        return blocking

    def _assemble(fiscal_year: int) -> dict:
        return dp_service.assemble_finalize_snapshots(
            session,
            hoa_id=hoa_id,
            fiscal_year=fiscal_year,
            package_id=package_id,
        )

    try:
        return finalize_annual_package(
            property_id=hoa_id, package_id=package_id,
            connection=session.connection().connection,
            preflight=_blocking_preflight,
            assemble=_assemble,
            expected_version=if_match,
        )
    except AnnualPackageNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PackageVersionMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidPackageStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FinalizeBlockedByPreflight as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "field_paths": exc.field_paths,
            },
        ) from exc
