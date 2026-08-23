"""Operator APIs for governing-document allocation resolution."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
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
    approve_slices_for_line,
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
from app.services.assessment_budget_mapping_rule_service import (
    active_budget_lines_for_property,
    build_budget_line_slice_key,
    build_assessment_mapping_review_rows,
    normalize_budget_label,
    resolve_active_assessment_setup_id,
)


router = APIRouter(tags=["Allocation Resolution"])


def _actor(user: dict) -> str:
    return str(user.get("email") or user.get("name") or "operator")


def _conn(session: Session):
    return session.connection().connection


def _active_setup(conn, hoa_id: int) -> int:
    setup_id = resolve_active_assessment_setup_id(conn, property_id=hoa_id)
    if setup_id is None:
        raise HTTPException(status_code=404, detail="No assessment setup for this HOA")
    return setup_id


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


def _factor_recipients(
    conn,
    *,
    setup_id: int,
    method: str,
    pool_key: Optional[str] = None,
) -> dict[str, Decimal]:
    """Load the current setup factors for an operator approval."""
    setup = conn.execute(
        "SELECT setup_type FROM assessment_setups WHERE id = ?",
        (setup_id,),
    ).fetchone()
    if setup is not None and str(setup[0]) == "grouped":
        if method == "specified_value":
            return {}
        rows = conn.execute(
            """
            SELECT group_name, average_square_feet, ownership_percent, unit_count
              FROM assessment_groups
             WHERE assessment_setup_id = ?
            """,
            (setup_id,),
        ).fetchall()
        if method == "square_footage":
            return {
                str(row[0]): Decimal(str(row[1] or 0)) * Decimal(str(row[3] or 1))
                for row in rows
            }
        return {
            str(row[0]): Decimal(str(row[2] or 0)) * Decimal(str(row[3] or 1))
            for row in rows
        }
    if method == "specified_value":
        rows = conn.execute(
            """
            SELECT u.unit_number, a.specified_monthly_amount
              FROM assessment_unit_pool_allocations a
              JOIN assessment_units u ON u.id = a.assessment_unit_id
             WHERE a.assessment_setup_id = ?
               AND a.pool_key = ?
            """,
            (setup_id, pool_key or ""),
        ).fetchall()
        # The pool key is filled by the caller for this method.
        return {str(row[0]): Decimal(str(row[1])) for row in rows}
    rows = conn.execute(
        """
        SELECT unit_number, square_feet, ownership_percent
          FROM assessment_units
         WHERE assessment_setup_id = ?
        """,
        (setup_id,),
    ).fetchall()
    index = 1 if method == "square_footage" else 2
    return {
        str(row[0]): Decimal(str(row[index] or 0))
        for row in rows
    }


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


def _assessment_categories(conn, setup_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT pool_key, pool_name, allocation_method, recipient_scope,
               budget_line_derivation
          FROM allocation_pools
         WHERE assessment_setup_id = ?
         ORDER BY display_order, id
        """,
        (setup_id,),
    ).fetchall()
    return [
        {
            "pool_key": str(row[0]),
            "pool_name": str(row[1]),
            "allocation_method": str(row[2] or ""),
            "recipient_scope": str(row[3] or ""),
            "budget_line_derivation": str(row[4] or ""),
        }
        for row in rows
    ]


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
    line_key: Optional[str] = None
    source_line_label: str
    source_line_account_code: Optional[str] = None
    source_line_section: Optional[str] = None
    source_line_category: Optional[str] = None
    source_line_fund_type: Optional[str] = None
    source_annual_amount: str
    slices: list[dict[str, Any]]


class ApproveSliceLineRequest(BaseModel):
    line_key: Optional[str] = None
    source_line_label: Optional[str] = None
    source_line_account_code: Optional[str] = None


class CategoryDecisionRequest(BaseModel):
    pool_key: str
    category: str
    decision: Literal["mapped", "zero", "not_applicable"]
    mapped_amount: Optional[str] = None
    evidence_text: str = ""
    reason: str = Field(min_length=2)


def _active_slice_context(
    conn,
    *,
    hoa_id: int,
    setup_id: int,
    source_line_label: Optional[str],
    source_line_account_code: Optional[str],
    source_line_section: Optional[str] = None,
    source_line_category: Optional[str] = None,
    source_line_fund_type: Optional[str] = None,
    line_key: Optional[str] = None,
    source_annual_amount: Optional[str] = None,
) -> tuple[int, dict[str, Any], Decimal, Optional[str]]:
    """Resolve a split against the exact active draft line."""
    active_draft_id, budget_lines = active_budget_lines_for_property(
        conn,
        property_id=hoa_id,
    )
    if active_draft_id is None:
        raise HTTPException(status_code=404, detail="Active budget draft not found")
    review_rows = build_assessment_mapping_review_rows(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_lines=budget_lines,
        budget_draft_id=int(active_draft_id),
        connection=conn,
    )
    normalized_label = normalize_budget_label(source_line_label or "")
    candidates = list(review_rows)
    if normalized_label:
        candidates = [
            row for row in candidates
            if str(row.get("normalized_label") or "") == normalized_label
        ]
    if source_line_account_code not in (None, ""):
        candidates = [
            row for row in candidates
            if str(row.get("account_code") or "") == str(source_line_account_code)
        ]
    for field, expected in (
        ("section", source_line_section),
        ("category", source_line_category),
        ("fund_type", source_line_fund_type),
    ):
        if expected not in (None, ""):
            candidates = [row for row in candidates if str(row.get(field)) == str(expected)]
    if line_key:
        candidates = [row for row in candidates if row.get("line_key") == line_key]
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="Source budget line was not found in the active budget draft.",
        )
    if len(candidates) > 1:
        raise HTTPException(
            status_code=422,
            detail="Source budget line is ambiguous; include its line key or account code.",
        )
    row = candidates[0]
    amount = Decimal(str(row.get("source_annual_amount") or 0))
    if source_annual_amount is not None:
        try:
            supplied_amount = Decimal(str(source_annual_amount))
        except (InvalidOperation, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Source annual amount must be a valid number.",
            ) from exc
        if abs(supplied_amount - amount) > Decimal("0.01"):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Source annual amount does not match the active budget "
                    f"({amount}). Refresh the mapping review and try again."
                ),
            )
    return (
        int(active_draft_id),
        row,
        amount,
        (
            str(row["account_code"])
            if row.get("account_code") not in (None, "")
            else None
        ),
    )


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
    _draft_id, active_budget_lines = active_budget_lines_for_property(
        conn,
        property_id=hoa_id,
    )
    report = evaluate_readiness(
        conn,
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_lines=active_budget_lines,
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
        "assessment_categories": _assessment_categories(conn, setup_id),
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
    recipients = {k: Decimal(str(v)) for k, v in body.custom_factors.items() if v not in (None, "")}
    if not recipients:
        recipients = _factor_recipients(
            conn,
            setup_id=setup_id,
            method=body.resolved_method,
            pool_key=pool_key,
        )
    denom = Decimal(body.denominator_value) if body.denominator_value else None
    if denom is None and body.resolved_method == "square_footage":
        denom = sum(recipients.values(), start=Decimal("0")) or None
    try:
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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    _draft_id, row, active_amount, active_account_code = _active_slice_context(
        conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
        source_line_label=body.source_line_label,
        source_line_account_code=body.source_line_account_code,
        source_line_section=body.source_line_section,
        source_line_category=body.source_line_category,
        source_line_fund_type=body.source_line_fund_type,
        line_key=body.line_key,
        source_annual_amount=body.source_annual_amount,
    )
    if row.get("allocation_mode") != "split_required":
        raise HTTPException(
            status_code=422,
            detail="This budget line does not require a category split.",
        )
    label = normalize_budget_label(str(row["normalized_label"]))
    source_line_key = build_budget_line_slice_key(
        normalized_label=label,
        section=str(row.get("section") or ""),
        category=str(row.get("category") or ""),
        fund_type=str(row.get("fund_type") or ""),
        account_code=active_account_code,
    )
    valid_pool_keys = {
        str(option.get("pool_key") or "")
        for option in row.get("valid_pool_options", [])
        if isinstance(option, dict)
    }
    try:
        created = upsert_slices_for_line(
            conn,
            property_id=hoa_id,
            assessment_setup_id=setup_id,
            source_line_normalized_label=label,
            source_line_key=source_line_key,
            source_line_account_code=active_account_code,
            source_annual_amount=active_amount,
            slices=body.slices,
            actor=_actor(user),
            valid_pool_keys=valid_pool_keys,
            commit=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from app.services.assessment_budget_mapping_rule_service import (
        set_assessment_review_row_disposition,
    )

    year_row = conn.execute(
        "SELECT portfolio_year FROM properties WHERE id = ?",
        (hoa_id,),
    ).fetchone()
    budget_year = (
        int(year_row[0])
        if year_row is not None and year_row[0] is not None
        else None
    )
    set_assessment_review_row_disposition(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_year=budget_year,
        budget_draft_id=_draft_id,
        row=row,
        disposition_state="clear",
        actor=_actor(user),
        note="Validated split saved for approval.",
        connection=conn,
        commit=False,
    )
    session.commit()
    return {"slices": [s.model_dump(mode="json") for s in created]}


@router.post("/hoa/{hoa_id}/allocation-resolution/slices/approve")
def approve_slices(
    hoa_id: int,
    body: ApproveSliceLineRequest,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    conn = _conn(session)
    setup_id = _active_setup(conn, hoa_id)
    if not body.source_line_label and not body.line_key:
        raise HTTPException(
            status_code=422,
            detail="Source line label or line key is required.",
        )
    draft_id, row, _amount, account_code = _active_slice_context(
        conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
        source_line_label=body.source_line_label,
        source_line_account_code=body.source_line_account_code,
        line_key=body.line_key,
        source_annual_amount=None,
    )
    del draft_id
    if row.get("allocation_mode") != "split_required":
        raise HTTPException(
            status_code=422,
            detail="This budget line does not require a category split.",
        )
    try:
        approved = approve_slices_for_line(
            conn,
            assessment_setup_id=setup_id,
            source_line_normalized_label=normalize_budget_label(
                str(row["normalized_label"])
            ),
            source_line_key=build_budget_line_slice_key(
                normalized_label=str(row["normalized_label"]),
                section=str(row.get("section") or ""),
                category=str(row.get("category") or ""),
                fund_type=str(row.get("fund_type") or ""),
                account_code=account_code,
            ),
            source_line_account_code=account_code,
            actor=_actor(user),
            source_annual_amount=_amount,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return {"slices": [s.model_dump(mode="json") for s in approved], "status": "approved"}


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
    _draft_id, active_budget_lines = active_budget_lines_for_property(
        conn,
        property_id=hoa_id,
    )
    report = evaluate_readiness(
        conn,
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_lines=active_budget_lines,
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
    _draft_id, active_budget_lines = active_budget_lines_for_property(
        conn,
        property_id=hoa_id,
    )
    report = evaluate_readiness(
        conn,
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_lines=active_budget_lines,
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
