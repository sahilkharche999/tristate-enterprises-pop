"""AnnualPackage REST endpoints (Phase 4.8)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..optimistic_lock import optional_if_match
from ..services.annual_package_service import (
    AnnualPackageNotFound,
    AnnualPackageResponse,
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
    """Snapshots captured at finalization — the four inputs the
    rendered package depends on. Caller (typically the compile job)
    assembles these from live state before calling finalize.
    """

    assessment_setup: Any
    budget: Any
    reserve: Any
    appendix_manifest: Any


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
    if_match: Optional[int] = Depends(optional_if_match),
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
    payload: FinalizePackageRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
    if_match: Optional[int] = Depends(optional_if_match),
) -> AnnualPackageResponse:
    """Freeze all four snapshot JSONs and transition to finalized.

    Caller is the compile job that has already gathered the live
    state into the four payload fields. After this call the package
    is immutable — re-renders load from snapshots, not live state.
    """
    try:
        return finalize_annual_package(
            property_id=hoa_id, package_id=package_id,
            assessment_setup=payload.assessment_setup,
            budget=payload.budget,
            reserve=payload.reserve,
            appendix_manifest=payload.appendix_manifest,
            connection=session.connection().connection,
            expected_version=if_match,
        )
    except AnnualPackageNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PackageVersionMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidPackageStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
