"""AnnualPackage REST endpoints (Phase 4.8)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
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


# ── Prior-year assessment schedule (year-1 seed + status) ───────────────────


class PriorScheduleRow(BaseModel):
    recipient_label: str
    monthly: str
    percent_of_total: Optional[str] = None


class ConfirmPriorScheduleRequest(BaseModel):
    fiscal_year: int = Field(..., description="Year the amounts applied (usually Y-1)")
    rows: list[PriorScheduleRow]


@router.get("/hoa/{hoa_id}/prior-assessment-schedule")
def get_prior_assessment_schedule_status(
    hoa_id: int,
    fiscal_year: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Status of prior schedule for a package fiscal year (inherited / seeded / missing)."""
    _ = current_user
    from ..disclosure_package.prior_assessment_schedule import (
        load_prior_seed,
        prior_status,
    )

    raw = session.connection().connection
    status = prior_status(raw, property_id=hoa_id, fiscal_year=fiscal_year)
    seed = load_prior_seed(raw, property_id=hoa_id)
    if seed is not None:
        year, rows = seed
        status["seed"] = {"fiscal_year": year, "rows": rows}
    return status


@router.put("/hoa/{hoa_id}/prior-assessment-schedule")
def confirm_prior_assessment_schedule(
    hoa_id: int,
    payload: ConfirmPriorScheduleRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Save operator-confirmed prior schedule rows (after PDF extract or manual entry)."""
    _ = current_user
    from ..disclosure_package.prior_assessment_schedule import save_prior_seed

    if not payload.rows:
        raise HTTPException(status_code=422, detail="At least one schedule row is required")
    rows = [r.model_dump(exclude_none=True) for r in payload.rows]
    raw = session.connection().connection
    save_prior_seed(
        raw,
        property_id=hoa_id,
        fiscal_year=payload.fiscal_year,
        rows=rows,
    )
    return {
        "status": "seeded",
        "prior_fiscal_year": payload.fiscal_year,
        "row_count": len(rows),
    }


@router.post("/hoa/{hoa_id}/prior-assessment-schedule/extract")
async def extract_prior_assessment_schedule(
    hoa_id: int,
    file: UploadFile = File(...),
    fiscal_year: Optional[int] = None,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Extract unit/monthly schedule from a prior final package PDF.

    Tries PDF text first; if sparse (scanned packages like Sharon Ridge),
    uses Gemini Vision (same stack as DRE / income-statement PDF path).

    Returns draft rows for operator review — does NOT save until PUT confirm.
    """
    _ = current_user
    _ = session
    _ = hoa_id
    from ..disclosure_package.prior_assessment_schedule import (
        extract_prior_schedule_from_pdf_bytes,
    )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Empty file")
    preferred = (int(fiscal_year) - 1) if fiscal_year is not None else None
    result = extract_prior_schedule_from_pdf_bytes(
        content,
        preferred_year=preferred,
    )
    rows = result.get("rows") or []
    return {
        "filename": file.filename,
        "row_count": len(rows),
        "rows": rows,
        "needs_confirmation": True,
        "method": result.get("method"),
        "fiscal_year": result.get("fiscal_year"),
        "pages_used": result.get("pages_used") or [],
        "message": result.get("message")
        or (
            "Review and confirm these rows before saving."
            if rows
            else "No unit/monthly rows detected — enter the schedule manually."
        ),
    }


@router.delete("/hoa/{hoa_id}/prior-assessment-schedule")
def delete_prior_assessment_schedule(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict:
    _ = current_user
    from ..disclosure_package.prior_assessment_schedule import clear_prior_seed

    clear_prior_seed(session.connection().connection, property_id=hoa_id)
    return {"status": "cleared"}
