"""Operator APIs for governing-document allocation resolution."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai_implementation.db import get_session
from app.allocation_resolution.classifier import apply_migration, collect_migration_report
from app.allocation_resolution.preview import build_preview_overlay, candidate_factors_from_units
from app.allocation_resolution.readiness import evaluate_readiness, readiness_blocks_final
from app.allocation_resolution.schemas import (
    CategoryCoverageDecision,
    FactorSnapshot,
    ReferencedSchedule,
    ResolutionEvidence,
)
from app.allocation_resolution.service import (
    approve_resolution,
    delete_slices_for_line,
    freeze_resolution_snapshot,
    list_category_decisions,
    list_current_resolutions,
    list_slices,
    save_draft_resolution,
    upsert_category_decision,
    upsert_slices_for_line,
)
from app.auth.dependencies import get_current_user
from app.services.assessment_budget_mapping_rule_service import normalize_budget_label


router = APIRouter(tags=["Allocation Resolution"])


def _actor(user: dict) -> str:
    return str(user.get("email") or user.get("name") or "operator")


def _conn(session: Session):
    return session.connection().connection


def _active_setup(conn, hoa_id: int) -> int:
    row = conn.execute(
        "SELECT default_assessment_setup_id FROM properties WHERE id = ?",
        (hoa_id,),
    ).fetchone()
    if row and row[0] is not None:
        return int(row[0])
    row = conn.execute(
        """
        SELECT id FROM assessment_setups
         WHERE property_id = ? AND status IN ('approved', 'draft')
         ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, id DESC
         LIMIT 1
        """,
        (hoa_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No assessment setup for this HOA")
    return int(row[0])


def _units(conn, setup_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT unit_number, square_feet, ownership_percent
          FROM assessment_units
         WHERE assessment_setup_id = ?
         ORDER BY unit_number
        """,
        (setup_id,),
    ).fetchall()
    return [
        {
            "unit_number": r[0],
            "square_feet": r[1],
            "ownership_percent": r[2],
        }
        for r in rows
    ]


def _residual_keys(conn, setup_id: int) -> set[str]:
    rows = conn.execute(
        """
        SELECT pool_key FROM allocation_pools
         WHERE assessment_setup_id = ?
           AND budget_line_derivation = 'residual_default'
        """,
        (setup_id,),
    ).fetchall()
    return {str(r[0]) for r in rows}


class ConfirmBasisRequest(BaseModel):
    resolved_method: Literal["equal", "square_footage", "ownership_percentage", "specified_value"]
    confirmation: str = Field(min_length=3)
    reason: str = Field(min_length=3)
    custom_factors: dict[str, str] = Field(default_factory=dict)
    denominator_value: Optional[str] = None
    prior_package_id: Optional[int] = None
    evidence_document_id: Optional[int] = None
    referenced_schedule_type: Optional[str] = None
    referenced_schedule_name: Optional[str] = None


class SliceLineRequest(BaseModel):
    source_line_label: str
    source_line_account_code: Optional[str] = None
    source_annual_amount: str
    slices: list[dict[str, Any]]


class CategoryDecisionRequest(BaseModel):
    pool_key: str
    category: str
    decision: Literal["mapped", "zero", "not_applicable"]
    mapped_amount: Optional[str] = None
    evidence_text: str = ""
    reason: str = Field(min_length=2)


@router.get("/hoa/{hoa_id}/allocation-resolution")
def get_allocation_resolution(
    hoa_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    del user
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    units = _units(conn, setup_id)
    resolutions = list_current_resolutions(conn, assessment_setup_id=setup_id)
    slices = list_slices(conn, assessment_setup_id=setup_id)
    decisions = list_category_decisions(conn, assessment_setup_id=setup_id)
    report = evaluate_readiness(
        conn,
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        residual_pool_keys=_residual_keys(conn, setup_id),
    )
    approved = [
        {"id": r[0], "fiscal_year": r[1], "status": r[2]}
        for r in conn.execute(
            """
            SELECT id, fiscal_year, status FROM annual_packages
             WHERE property_id = ? AND status IN ('approved', 'finalized')
             ORDER BY fiscal_year DESC, id DESC
            """,
            (hoa_id,),
        ).fetchall()
    ]
    return {
        "property_id": hoa_id,
        "assessment_setup_id": setup_id,
        "resolutions": [r.model_dump(mode="json") for r in resolutions],
        "slices": [s.model_dump(mode="json") for s in slices],
        "category_decisions": [d.model_dump(mode="json") for d in decisions],
        "candidate_factors": candidate_factors_from_units(units),
        "units": units,
        "approved_schedules": approved,
        "readiness": report.model_dump(mode="json"),
        "blocks_final": readiness_blocks_final(report),
    }


@router.post("/hoa/{hoa_id}/allocation-resolution/pools/{pool_key}/draft")
def draft_pool_resolution(
    hoa_id: int,
    pool_key: str,
    body: ConfirmBasisRequest,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    current = next(
        (r for r in list_current_resolutions(conn, assessment_setup_id=setup_id) if r.pool_key == pool_key),
        None,
    )
    if current is None:
        raise HTTPException(status_code=404, detail="No resolution record for this pool")
    recipients = {k: Decimal(str(v)) for k, v in body.custom_factors.items() if v not in (None, "")}
    rec = save_draft_resolution(
        conn,
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        pool_key=pool_key,
        declared_method=current.declared_method,
        resolved_method=body.resolved_method,
        factor_snapshot=FactorSnapshot(
            method=body.resolved_method,
            denominator_value=Decimal(body.denominator_value) if body.denominator_value else None,
            denominator_source="manual" if body.custom_factors else "calculated",
            recipients=recipients,
        ),
        referenced_schedule=ReferencedSchedule(
            schedule_type=body.referenced_schedule_type or current.referenced_schedule.schedule_type,
            schedule_name=body.referenced_schedule_name or current.referenced_schedule.schedule_name,
            available=body.evidence_document_id is not None or body.prior_package_id is not None,
            document_id=body.evidence_document_id,
            prior_package_id=body.prior_package_id,
        ),
        evidence=ResolutionEvidence(
            source_pages=current.evidence.source_pages,
            source_text=current.evidence.source_text,
            reason=body.reason,
            document_id=body.evidence_document_id,
            prior_package_id=body.prior_package_id,
        ),
        actor=_actor(user),
    )
    return rec.model_dump(mode="json")


@router.post("/hoa/{hoa_id}/allocation-resolution/pools/{pool_key}/approve")
def approve_pool_resolution(
    hoa_id: int,
    pool_key: str,
    body: ConfirmBasisRequest,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    current = next(
        (r for r in list_current_resolutions(conn, assessment_setup_id=setup_id) if r.pool_key == pool_key),
        None,
    )
    if current is None:
        raise HTTPException(status_code=404, detail="No resolution record for this pool")
    units = _units(conn, setup_id)
    recipients = {k: Decimal(str(v)) for k, v in body.custom_factors.items() if v not in (None, "")}
    if not recipients:
        if body.resolved_method == "ownership_percentage":
            recipients = {
                str(u["unit_number"]): Decimal(str(u["ownership_percent"] or 0))
                for u in units
            }
        elif body.resolved_method == "square_footage":
            recipients = {
                str(u["unit_number"]): Decimal(str(u["square_feet"] or 0))
                for u in units
            }
    denom = Decimal(body.denominator_value) if body.denominator_value else None
    if denom is None and body.resolved_method == "square_footage":
        denom = sum(recipients.values(), start=Decimal("0")) or None
    rec = approve_resolution(
        conn,
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        pool_key=pool_key,
        resolved_method=body.resolved_method,
        factor_snapshot=FactorSnapshot(
            method=body.resolved_method,
            denominator_value=denom,
            denominator_source="manual" if body.custom_factors else "calculated",
            recipients=recipients,
        ),
        evidence=ResolutionEvidence(
            source_pages=current.evidence.source_pages,
            source_text=current.evidence.source_text,
            reason=f"{body.confirmation}: {body.reason}",
            document_id=body.evidence_document_id,
            prior_package_id=body.prior_package_id,
        ),
        actor=_actor(user),
        referenced_schedule=ReferencedSchedule(
            schedule_type=body.referenced_schedule_type or current.referenced_schedule.schedule_type,
            schedule_name=body.referenced_schedule_name or current.referenced_schedule.schedule_name,
            available=True,
            document_id=body.evidence_document_id,
            prior_package_id=body.prior_package_id,
        ),
    )
    return rec.model_dump(mode="json")


@router.post("/hoa/{hoa_id}/allocation-resolution/slices")
def save_slices(
    hoa_id: int,
    body: SliceLineRequest,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    label = normalize_budget_label(body.source_line_label)
    try:
        created = upsert_slices_for_line(
            conn,
            property_id=hoa_id,
            assessment_setup_id=setup_id,
            source_line_normalized_label=label,
            source_line_account_code=body.source_line_account_code,
            source_annual_amount=Decimal(body.source_annual_amount),
            slices=body.slices,
            actor=_actor(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"slices": [s.model_dump(mode="json") for s in created]}


@router.delete("/hoa/{hoa_id}/allocation-resolution/slices")
def remove_slices(
    hoa_id: int,
    source_line_label: str,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, str]:
    del user
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    delete_slices_for_line(
        conn,
        assessment_setup_id=setup_id,
        source_line_normalized_label=normalize_budget_label(source_line_label),
    )
    return {"status": "superseded"}


@router.post("/hoa/{hoa_id}/allocation-resolution/categories")
def save_category_decision(
    hoa_id: int,
    body: CategoryDecisionRequest,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    rec = upsert_category_decision(
        conn,
        CategoryCoverageDecision(
            property_id=hoa_id,
            assessment_setup_id=setup_id,
            pool_key=body.pool_key,
            category=body.category,
            decision=body.decision,
            mapped_amount=Decimal(body.mapped_amount) if body.mapped_amount else None,
            evidence_text=body.evidence_text,
            reason=body.reason,
            created_by=_actor(user),
        ),
    )
    return rec.model_dump(mode="json")


@router.get("/hoa/{hoa_id}/allocation-resolution/preview")
def preview_allocation(
    hoa_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    del user
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    overlay = build_preview_overlay(
        conn,
        assessment_setup_id=setup_id,
        units=_units(conn, setup_id),
        pool_annuals={},
    )
    report = evaluate_readiness(
        conn,
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        residual_pool_keys=_residual_keys(conn, setup_id),
    )
    return {
        "preview": overlay,
        "readiness": report.model_dump(mode="json"),
        "blocks_final": readiness_blocks_final(report),
        "is_final": False,
    }


@router.get("/hoa/{hoa_id}/allocation-resolution/readiness")
def get_readiness(
    hoa_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    del user
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    report = evaluate_readiness(
        conn,
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        residual_pool_keys=_residual_keys(conn, setup_id),
    )
    return {
        "readiness": report.model_dump(mode="json"),
        "blocks_final": readiness_blocks_final(report),
    }


@router.get("/hoa/{hoa_id}/allocation-resolution/migration-report")
def migration_report(
    hoa_id: int,
    apply: bool = False,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    del user
    conn = _conn(session)
    rows = apply_migration(conn, dry_run=not apply, property_id=hoa_id) if apply else collect_migration_report(
        conn, property_id=hoa_id
    )
    return {"rows": rows, "applied": apply}


@router.get("/hoa/{hoa_id}/allocation-resolution/freeze")
def freeze_snapshot(
    hoa_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    del user
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    return freeze_resolution_snapshot(conn, assessment_setup_id=setup_id)
