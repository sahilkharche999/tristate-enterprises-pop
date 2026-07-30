"""Assessment mapping review endpoints."""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..ai_implementation.db import get_session
from ..auth.dependencies import get_current_user
from ..services.assessment_budget_mapping_rule_service import (
    assign_assessment_review_row_pool,
    build_assessment_mapping_review_blockers,
    build_assessment_mapping_review_rows,
    build_assessment_mapping_review_summary,
    build_line_review_items,
    classify_budget_lines_for_mapping,
    materialize_budget_line_pool_mappings,
    normalize_budget_label,
    record_scoped_alias,
    select_assessment_mapping_amount,
    set_assessment_review_row_disposition,
    set_exemption_decision_state,
)
from ..services.assessment_mapping_ai_review_service import (
    SafeToStageDecision,
    analyze_assessment_mapping_review,
)


router = APIRouter(tags=["Assessment Mapping Review"])


class RuleDecisionRequest(BaseModel):
    note: str = ""


class RuleEditRequest(BaseModel):
    pool_key: str
    match_label: Optional[str] = None
    account_code: Optional[str] = None
    match_type: Literal["exact_label", "normalized_label", "account_code", "category", "remainder"]
    note: str = ""


class AliasRequest(BaseModel):
    pool_key: str
    dre_label: str
    budget_label: str
    account_code: Optional[str] = None
    note: str = ""


class ApproveLineSuggestionRequest(BaseModel):
    rule_id: int
    line_label: str
    normalized_label: str
    section: str
    category: str
    fund_type: str
    account_code: Optional[str] = None
    note: str = ""


class ExemptionDecisionRequest(BaseModel):
    exemption_state: Literal["active", "inactive", "pending_review"]
    budget_year: Optional[int] = None
    budget_draft_id: Optional[int] = None
    note: str = ""

    @model_validator(mode="after")
    def require_note_for_final_state(self) -> "ExemptionDecisionRequest":
        if self.exemption_state in {"active", "inactive"} and not self.note.strip():
            raise ValueError("note is required for active/inactive exemption decisions")
        return self


class AnalyzeMappingReviewRequest(BaseModel):
    pass


class ApplySafeAnalysisRequest(BaseModel):
    safe_to_stage: list[SafeToStageDecision] = Field(default_factory=list)


class AssignReviewRowRequest(BaseModel):
    line_key: str
    pool_key: str
    note: str = ""


class ReviewRowDispositionRequest(BaseModel):
    line_key: str
    disposition_state: Literal[
        "excluded_non_regular",
        "reserve_detail",
        "pending_split",
        "clear",
    ]
    note: str = ""


def _json_loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _setup_id_for_hoa(raw_conn, hoa_id: int) -> int:
    row = raw_conn.execute(
        "SELECT default_assessment_setup_id FROM properties WHERE id = ?",
        (hoa_id,),
    ).fetchone()
    setup_id = row[0] if row else None
    if setup_id:
        return int(setup_id)
    setup_row = raw_conn.execute(
        """
        SELECT id
          FROM assessment_setups
         WHERE property_id = ?
           AND status = 'approved'
         ORDER BY id DESC
         LIMIT 1
        """,
        (hoa_id,),
    ).fetchone()
    if not setup_row:
        raise HTTPException(status_code=404, detail="Approved assessment setup not found")
    return int(setup_row[0])


def _budget_year_for_hoa(raw_conn, hoa_id: int) -> Optional[int]:
    row = raw_conn.execute(
        "SELECT portfolio_year FROM properties WHERE id = ?",
        (hoa_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _active_draft(raw_conn, hoa_id: int):
    row = raw_conn.execute(
        """
        SELECT id, line_items_json
          FROM budget_drafts
         WHERE property_id = ?
           AND status = 'active'
         ORDER BY updated_at DESC, id DESC
         LIMIT 1
        """,
        (hoa_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Active budget draft not found")
    return row


def _mapping_category(raw_category: object) -> str:
    category = str(raw_category or "").lower()
    if category == "income":
        return "income"
    if category == "reserve_income":
        return "reserve_income"
    if category in {"reserve", "reserve_expense"}:
        return "reserve_expense"
    return "operating"


def _fund_type(category: str) -> str:
    return "reserve" if category in {"reserve_income", "reserve_expense"} else "operating"


def _line_item_to_mapping_line(item: dict[str, Any]) -> dict[str, Any]:
    label = str(item.get("label") or item.get("line_item_key") or "")
    category = _mapping_category(item.get("category"))
    account_code = item.get("account_code")
    amount, source_column_used = select_assessment_mapping_amount(item)
    return {
        "label": label,
        "normalized_label": normalize_budget_label(label),
        "section": str((item.get("raw") or {}).get("section") or category),
        "category": category,
        "fund_type": _fund_type(category),
        "account_code": str(account_code) if account_code not in (None, "") else None,
        "annual_budget": item.get("annual_budget"),
        "proposed_amount": (
            item.get("proposed_amount")
            if item.get("proposed_amount") is not None
            else item.get("proposedAmount")
        ),
        "projection": item.get("projection"),
        "assessment_mapping_amount": float(amount) if amount is not None else None,
        "source_column_used": source_column_used,
        "amount": float(amount) if amount is not None else None,
        "reserve_group": item.get("reserve_group") or item.get("reserveGroup"),
        "active": not bool(item.get("inactive")),
    }


def _budget_lines_for_active_draft(raw_conn, hoa_id: int) -> tuple[int, list[dict[str, Any]]]:
    draft = _active_draft(raw_conn, hoa_id)
    line_items = _json_loads(draft[1], [])
    return int(draft[0]), [
        _line_item_to_mapping_line(item)
        for item in line_items
        if isinstance(item, dict)
    ]


def _review_scope(raw_conn, hoa_id: int) -> tuple[int, Optional[int], list[dict[str, Any]]]:
    draft_id, budget_lines = _budget_lines_for_active_draft(raw_conn, hoa_id)
    return draft_id, _budget_year_for_hoa(raw_conn, hoa_id), budget_lines


def _review_rows_for_active_draft(
    *,
    raw_conn,
    hoa_id: int,
    setup_id: int,
) -> tuple[int, Optional[int], list[dict[str, Any]], list[dict[str, Any]]]:
    draft_id, budget_year, budget_lines = _review_scope(raw_conn, hoa_id)
    review_rows = build_assessment_mapping_review_rows(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_lines=budget_lines,
        budget_year=budget_year,
        budget_draft_id=draft_id,
        connection=raw_conn,
    )
    return draft_id, budget_year, budget_lines, review_rows


def _actor(current_user: dict) -> str:
    return str(current_user.get("email") or current_user.get("name") or "unknown")


def _active_budget_lines_by_key(raw_conn, hoa_id: int) -> tuple[int, dict[tuple[str, str, str, str, Optional[str]], dict[str, Any]]]:
    draft_id, budget_lines = _budget_lines_for_active_draft(raw_conn, hoa_id)
    return draft_id, {
        (
            normalize_budget_label(str(line.get("normalized_label") or line.get("label") or "")),
            str(line.get("section") or ""),
            str(line.get("category") or ""),
            str(line.get("fund_type") or ""),
            str(line.get("account_code")) if line.get("account_code") not in (None, "") else None,
        ): line
        for line in budget_lines
    }


def _review_row_by_line_key(
    *,
    raw_conn,
    hoa_id: int,
    setup_id: int,
    line_key: str,
) -> tuple[int, Optional[int], dict[str, Any]]:
    draft_id, budget_year, _budget_lines, review_rows = _review_rows_for_active_draft(
        raw_conn=raw_conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
    )
    row = next((item for item in review_rows if item["line_key"] == line_key), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Review row not found")
    return draft_id, budget_year, row


def _approve_line_suggestion_action(
    *,
    raw_conn,
    hoa_id: int,
    setup_id: int,
    rule_id: int,
    line_label: str,
    normalized_label: str,
    section: str,
    category: str,
    fund_type: str,
    account_code: Optional[str],
    note: str,
    actor: str,
) -> dict[str, Any]:
    _draft_id, canonical_budget_lines = _active_budget_lines_by_key(raw_conn, hoa_id)
    line_key = (
        normalized_label,
        section,
        category,
        fund_type,
        account_code,
    )
    line = canonical_budget_lines.get(line_key)
    if line is None:
        raise HTTPException(status_code=404, detail="Budget line not found in active draft")

    rule = raw_conn.execute(
        """
        SELECT pool_key, match_label, account_code
          FROM assessment_budget_mapping_rules
         WHERE id = ?
           AND property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
        """,
        (rule_id, hoa_id, setup_id),
    ).fetchone()
    if rule is None:
        raise HTTPException(status_code=404, detail="Mapping rule not found")

    raw_conn.execute(
        """
        UPDATE assessment_budget_mapping_rules
           SET approval_status = 'approved',
               review_state = 'ready',
               updated_at = datetime('now')
         WHERE id = ?
        """,
        (rule_id,),
    )
    alias_id = record_scoped_alias(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        pool_key=str(rule[0]),
        dre_label=str(rule[1] or normalized_label),
        budget_label=line_label,
        account_code=account_code or (str(rule[2]) if rule[2] not in (None, "") else None),
        actor=actor,
        note=note,
        connection=raw_conn,
        commit=False,
    )
    cur = raw_conn.execute(
        """
        INSERT INTO budget_line_pool_mappings (
            property_id, assessment_setup_id,
            budget_line_normalized_label, section, category, fund_type,
            account_code, pool_key, source_rule_id, mapping_source,
            match_method, approval_status, review_state, budget_line_amount,
            approved_by, approved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'alias',
                  'approved_alias', 'approved', 'ready', ?, ?, datetime('now'))
        ON CONFLICT(property_id, assessment_setup_id,
                    budget_line_normalized_label, section, category,
                    fund_type, COALESCE(account_code, ''))
        DO UPDATE SET pool_key = excluded.pool_key,
                      source_rule_id = excluded.source_rule_id,
                      mapping_source = excluded.mapping_source,
                      match_method = excluded.match_method,
                      approval_status = excluded.approval_status,
                      review_state = excluded.review_state,
                      budget_line_amount = excluded.budget_line_amount,
                      approved_by = excluded.approved_by,
                      approved_at = excluded.approved_at,
                      active = 1
        """,
        (
            hoa_id,
            setup_id,
            normalized_label,
            section,
            category,
            fund_type,
            account_code,
            str(rule[0]),
            rule_id,
            line.get("amount"),
            actor,
        ),
    )
    return {
        "rule_id": rule_id,
        "alias_id": alias_id,
        "alias_created": alias_id > 0,
        "mapping_created": cur.rowcount > 0,
        "approval_status": "approved",
        "review_state": "ready",
        "pool_key": str(rule[0]),
    }


def _approve_residual_rule_action(
    *,
    raw_conn,
    hoa_id: int,
    setup_id: int,
    pool_key: Optional[str] = None,
) -> int:
    params: list[Any] = [hoa_id, setup_id]
    pool_clause = ""
    if pool_key:
        pool_clause = " AND pool_key = ?"
        params.append(pool_key)
    cur = raw_conn.execute(
        f"""
        UPDATE assessment_budget_mapping_rules
           SET approval_status = 'approved',
               review_state = 'ready',
               updated_at = datetime('now')
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND match_type = 'remainder'
           AND active = 1
           {pool_clause}
        """,
        tuple(params),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Residual rule not found")
    return cur.rowcount


def _residual_preview(raw_conn, hoa_id: int, setup_id: int, budget_lines: list[dict[str, Any]]) -> dict[str, Any]:
    residual_rules = raw_conn.execute(
        """
        SELECT id, pool_key, approval_status, review_state
          FROM assessment_budget_mapping_rules
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
           AND match_type = 'remainder'
        """,
        (hoa_id, setup_id),
    ).fetchall()
    if not residual_rules:
        return {"candidate_lines": [], "excluded_lines": [], "unresolved_lines": []}
    draft_id, budget_year, _budget_lines, review_rows = _review_rows_for_active_draft(
        raw_conn=raw_conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
    )
    del draft_id, budget_year, budget_lines
    candidate_lines = []
    excluded_lines = []
    unresolved_lines = []
    for item in review_rows:
        payload = {
            "line_label": item["line_label"],
            "amount": item["assessment_mapping_amount"],
            "row_role": item["row_role"],
            "eligibility": item["eligibility"],
            "reason": item["reason"],
        }
        if item["included_in_regular_basis"] and not item["current_pool_key"]:
            candidate_lines.append(payload)
        if item["included_in_regular_basis"] and not item["current_pool_key"]:
            unresolved_lines.append(payload)
        elif not item["included_in_regular_basis"]:
            unresolved_lines.append(payload)
            excluded_lines.append(payload)
    return {
        "rules": [
            {
                "id": int(row[0]),
                "pool_key": row[1],
                "approval_status": row[2],
                "review_state": row[3],
            }
            for row in residual_rules
        ],
        "candidate_lines": candidate_lines,
        "excluded_lines": excluded_lines,
        "unresolved_lines": unresolved_lines,
    }


@router.get("/hoa/{hoa_id}/assessment-mapping-review")
def get_assessment_mapping_review(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    draft_id, budget_year, budget_lines, review_rows = _review_rows_for_active_draft(
        raw_conn=raw_conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
    )
    classification = classify_budget_lines_for_mapping(budget_lines)
    line_review_items = build_line_review_items(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_lines=budget_lines,
        connection=raw_conn,
    )
    eligibility_groups: dict[str, list[dict[str, Any]]] = {}
    for item in classification.classifications:
        eligibility_groups.setdefault(item.eligibility, []).append({
            "line_label": item.line_label,
            "amount": float(item.amount) if item.amount is not None else None,
            "requires_mapping": item.requires_mapping,
            "reason": item.reason,
            "canonical": item.canonical,
        })
    unresolved = sum(1 for item in review_rows if item["current_status"] != "mapped")

    rules = raw_conn.execute(
        """
        SELECT id, pool_key, match_label, normalized_label, account_code,
               match_type, rule_source, approval_status, review_state,
               confidence, budget_line_derivation, source_parent_category,
               assessment_type, review_required, review_reason,
               source_evidence_text
          FROM assessment_budget_mapping_rules
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
         ORDER BY pool_key, id
        """,
        (hoa_id, setup_id),
    ).fetchall()
    aliases = raw_conn.execute(
        """
        SELECT id, pool_key, dre_label, budget_label, account_code,
               approval_status, decided_by, decided_at, note
          FROM assessment_mapping_aliases
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
         ORDER BY pool_key, id
        """,
        (hoa_id, setup_id),
    ).fetchall()
    pools = raw_conn.execute(
        """
        SELECT pool_key, pool_name, allocation_method, recipient_scope,
               budget_line_derivation
          FROM allocation_pools
         WHERE assessment_setup_id = ?
         ORDER BY display_order, id
        """,
        (setup_id,),
    ).fetchall()
    existing_mappings = raw_conn.execute(
        """
        SELECT budget_line_normalized_label, section, category, fund_type,
               account_code, pool_key, mapping_source, review_state,
               budget_line_amount
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
         ORDER BY budget_line_normalized_label
        """,
        (hoa_id, setup_id),
    ).fetchall()
    exemptions = raw_conn.execute(
        """
        SELECT pool_key, exemption_state, budget_year, budget_draft_id,
               decided_by, decided_at, notes
          FROM assessment_exemption_decisions
         WHERE property_id = ?
           AND assessment_setup_id = ?
         ORDER BY pool_key
        """,
        (hoa_id, setup_id),
    ).fetchall()
    reconciliation_summary = build_assessment_mapping_review_summary(review_rows)
    blockers = build_assessment_mapping_review_blockers(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        review_rows=review_rows,
        connection=raw_conn,
    )
    mapped_total = float(reconciliation_summary["mapped_regular_total"])
    assessable_total = float(reconciliation_summary["target_regular_assessment_basis"])
    reconciliation_passed = not bool(reconciliation_summary["reconciliation_failures"])
    return {
        "property_id": hoa_id,
        "assessment_setup_id": setup_id,
        "budget_year": budget_year,
        "budget_draft_id": draft_id,
        "pools": [
            {
                "pool_key": row[0],
                "pool_name": row[1],
                "allocation_method": row[2],
                "recipient_scope": row[3],
                "budget_line_derivation": row[4],
            }
            for row in pools
        ],
        "rules": [
            {
                "id": int(row[0]),
                "pool_key": row[1],
                "match_label": row[2],
                "normalized_label": row[3],
                "account_code": row[4],
                "match_type": row[5],
                "rule_source": row[6],
                "approval_status": row[7],
                "review_state": row[8],
                "confidence": row[9],
                "budget_line_derivation": row[10],
                "source_parent_category": row[11],
                "assessment_type": row[12],
                "review_required": bool(row[13]),
                "review_reason": row[14],
                "source_evidence_text": row[15],
            }
            for row in rules
        ],
        "aliases": [
            {
                "id": int(row[0]),
                "pool_key": row[1],
                "dre_label": row[2],
                "budget_label": row[3],
                "account_code": row[4],
                "approval_status": row[5],
                "decided_by": row[6],
                "decided_at": row[7],
                "note": row[8],
            }
            for row in aliases
        ],
        "existing_mappings": [
            {
                "budget_line_normalized_label": row[0],
                "section": row[1],
                "category": row[2],
                "fund_type": row[3],
                "account_code": row[4],
                "pool_key": row[5],
                "mapping_source": row[6],
                "review_state": row[7],
                "budget_line_amount": row[8],
            }
            for row in existing_mappings
        ],
        "exemption_decisions": [
            {
                "pool_key": row[0],
                "exemption_state": row[1],
                "budget_year": row[2],
                "budget_draft_id": row[3],
                "decided_by": row[4],
                "decided_at": row[5],
                "notes": row[6],
            }
            for row in exemptions
        ],
        "residual_preview": _residual_preview(raw_conn, hoa_id, setup_id, budget_lines),
        "reconciliation_status": {
            "mapped_pool_total": mapped_total,
            "assessment_target": assessable_total,
            "passed": reconciliation_passed,
            "failures": list(reconciliation_summary["reconciliation_failures"]),
        },
        "reconciliation_summary": reconciliation_summary,
        "mapping_review_blockers": blockers,
        "review_rows": review_rows,
        "eligibility_groups": eligibility_groups,
        "line_review_items": line_review_items,
        "duplicate_conflicts": [
            {
                "normalized_label": conflict.normalized_label,
                "line_labels": conflict.line_labels,
                "amounts": [float(amount) for amount in conflict.amounts],
            }
            for conflict in classification.duplicate_conflicts
        ],
        "progress": {"unresolved_count": unresolved},
    }


@router.post("/hoa/{hoa_id}/assessment-mapping-review/rules/{rule_id}/approve")
def approve_mapping_rule(
    hoa_id: int,
    rule_id: int,
    payload: RuleDecisionRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    cur = raw_conn.execute(
        """
        UPDATE assessment_budget_mapping_rules
           SET approval_status = 'approved',
               review_state = 'ready',
               updated_at = datetime('now')
         WHERE id = ?
           AND property_id = ?
           AND assessment_setup_id = ?
        """,
        (rule_id, hoa_id, setup_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Mapping rule not found")
    session.commit()
    return {"id": rule_id, "approval_status": "approved", "review_state": "ready"}


def _set_rule_state(raw_conn, *, hoa_id: int, setup_id: int, rule_id: int, approval_status: str, review_state: str) -> None:
    cur = raw_conn.execute(
        """
        UPDATE assessment_budget_mapping_rules
           SET approval_status = ?,
               review_state = ?,
               updated_at = datetime('now')
         WHERE id = ?
           AND property_id = ?
           AND assessment_setup_id = ?
        """,
        (approval_status, review_state, rule_id, hoa_id, setup_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Mapping rule not found")


@router.post("/hoa/{hoa_id}/assessment-mapping-review/rules/{rule_id}/reject")
def reject_mapping_rule(
    hoa_id: int,
    rule_id: int,
    payload: RuleDecisionRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    _set_rule_state(
        raw_conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
        rule_id=rule_id,
        approval_status="rejected",
        review_state="rejected",
    )
    session.commit()
    return {"id": rule_id, "approval_status": "rejected", "review_state": "rejected"}


@router.post("/hoa/{hoa_id}/assessment-mapping-review/rules/{rule_id}/disable")
def disable_mapping_rule(
    hoa_id: int,
    rule_id: int,
    payload: RuleDecisionRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    _set_rule_state(
        raw_conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
        rule_id=rule_id,
        approval_status="disabled",
        review_state="disabled",
    )
    session.commit()
    return {"id": rule_id, "approval_status": "disabled", "review_state": "disabled"}


@router.patch("/hoa/{hoa_id}/assessment-mapping-review/rules/{rule_id}")
def edit_mapping_rule(
    hoa_id: int,
    rule_id: int,
    payload: RuleEditRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    normalized = normalize_budget_label(payload.match_label or "")
    cur = raw_conn.execute(
        """
        UPDATE assessment_budget_mapping_rules
           SET pool_key = ?,
               match_label = ?,
               normalized_label = ?,
               account_code = ?,
               match_type = ?,
               rule_source = 'operator',
               approval_status = 'suggested',
               review_state = 'pending_review',
               updated_at = datetime('now')
         WHERE id = ?
           AND property_id = ?
           AND assessment_setup_id = ?
        """,
        (
            payload.pool_key,
            payload.match_label,
            normalized or None,
            payload.account_code,
            payload.match_type,
            rule_id,
            hoa_id,
            setup_id,
        ),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Mapping rule not found")
    session.commit()
    return {
        "id": rule_id,
        "pool_key": payload.pool_key,
        "normalized_label": normalized or None,
        "approval_status": "suggested",
        "review_state": "pending_review",
    }


@router.post("/hoa/{hoa_id}/assessment-mapping-review/aliases")
def create_mapping_alias(
    hoa_id: int,
    payload: AliasRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    alias_id = record_scoped_alias(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        pool_key=payload.pool_key,
        dre_label=payload.dre_label,
        budget_label=payload.budget_label,
        account_code=payload.account_code,
        actor=_actor(current_user),
        note=payload.note,
        connection=raw_conn,
        commit=False,
    )
    session.commit()
    return {"id": alias_id, "approval_status": "approved"}


@router.post("/hoa/{hoa_id}/assessment-mapping-review/rows/assign")
def assign_review_row(
    hoa_id: int,
    payload: AssignReviewRowRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    draft_id, budget_year, row = _review_row_by_line_key(
        raw_conn=raw_conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
        line_key=payload.line_key,
    )
    result = assign_assessment_review_row_pool(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_year=budget_year,
        budget_draft_id=draft_id,
        row=row,
        pool_key=payload.pool_key,
        actor=_actor(current_user),
        note=payload.note,
        connection=raw_conn,
        commit=False,
    )
    session.commit()
    return result


@router.post("/hoa/{hoa_id}/assessment-mapping-review/rows/disposition")
def set_review_row_disposition(
    hoa_id: int,
    payload: ReviewRowDispositionRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    draft_id, budget_year, row = _review_row_by_line_key(
        raw_conn=raw_conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
        line_key=payload.line_key,
    )
    try:
        result = set_assessment_review_row_disposition(
            property_id=hoa_id,
            assessment_setup_id=setup_id,
            budget_year=budget_year,
            budget_draft_id=draft_id,
            row=row,
            disposition_state=payload.disposition_state,
            actor=_actor(current_user),
            note=payload.note,
            connection=raw_conn,
            commit=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return result


@router.post("/hoa/{hoa_id}/assessment-mapping-review/lines/approve")
def approve_line_suggestion(
    hoa_id: int,
    payload: ApproveLineSuggestionRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    result = _approve_line_suggestion_action(
        raw_conn=raw_conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
        rule_id=payload.rule_id,
        line_label=payload.line_label,
        normalized_label=payload.normalized_label,
        section=payload.section,
        category=payload.category,
        fund_type=payload.fund_type,
        account_code=payload.account_code,
        note=payload.note,
        actor=_actor(current_user),
    )
    session.commit()
    return result


@router.post("/hoa/{hoa_id}/assessment-mapping-review/aliases/{alias_id}/revoke")
def revoke_mapping_alias(
    hoa_id: int,
    alias_id: int,
    payload: RuleDecisionRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    cur = raw_conn.execute(
        """
        UPDATE assessment_mapping_aliases
           SET approval_status = 'revoked',
               active = 0,
               updated_at = datetime('now')
         WHERE id = ?
           AND property_id = ?
           AND assessment_setup_id = ?
        """,
        (alias_id, hoa_id, setup_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Mapping alias not found")
    session.commit()
    return {"id": alias_id, "approval_status": "revoked"}


@router.get("/hoa/{hoa_id}/assessment-mapping-review/residual/preview")
def get_residual_preview(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    _draft_id, budget_lines = _budget_lines_for_active_draft(raw_conn, hoa_id)
    return _residual_preview(raw_conn, hoa_id, setup_id, budget_lines)


@router.post("/hoa/{hoa_id}/assessment-mapping-review/residual/approve")
def approve_residual_rule(
    hoa_id: int,
    payload: RuleDecisionRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    updated_count = _approve_residual_rule_action(
        raw_conn=raw_conn,
        hoa_id=hoa_id,
        setup_id=setup_id,
    )
    session.commit()
    return {"approval_status": "approved", "review_state": "ready", "updated_count": updated_count}


@router.post("/hoa/{hoa_id}/assessment-mapping-review/analyze")
def analyze_mapping_review(
    hoa_id: int,
    payload: AnalyzeMappingReviewRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    _draft_id, budget_lines = _budget_lines_for_active_draft(raw_conn, hoa_id)
    result = analyze_assessment_mapping_review(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_lines=budget_lines,
        connection=raw_conn,
    )
    return result.model_dump(mode="json")


@router.post("/hoa/{hoa_id}/assessment-mapping-review/analyze/apply-safe")
def apply_safe_ai_mapping_review(
    hoa_id: int,
    payload: ApplySafeAnalysisRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    actor = _actor(current_user)
    applied_safe_count = 0
    alias_count = 0
    mapping_count = 0
    residual_rule_approved_count = 0
    skipped: list[dict[str, Any]] = []

    for item in payload.safe_to_stage:
        try:
            if item.action_kind == "approve_line_suggestion":
                rule_id = next(
                    (
                        ref.rule_id
                        for ref in item.evidence_refs
                        if ref.rule_id is not None
                    ),
                    None,
                )
                if rule_id is None:
                    skipped.append(
                        {
                            "line_label": item.line_label,
                            "reason": "missing_rule_id",
                        }
                    )
                    continue
                result = _approve_line_suggestion_action(
                    raw_conn=raw_conn,
                    hoa_id=hoa_id,
                    setup_id=setup_id,
                    rule_id=int(rule_id),
                    line_label=item.line_label,
                    normalized_label=item.normalized_label,
                    section=item.section,
                    category=item.category,
                    fund_type=item.fund_type,
                    account_code=item.account_code,
                    note="AI safe-stage approval",
                    actor=actor,
                )
                applied_safe_count += 1
                alias_count += 1 if result["alias_created"] else 0
                mapping_count += 1 if result["mapping_created"] else 0
                continue
            if item.action_kind == "approve_residual_rule":
                residual_rule_approved_count += _approve_residual_rule_action(
                    raw_conn=raw_conn,
                    hoa_id=hoa_id,
                    setup_id=setup_id,
                    pool_key=item.suggested_pool_key or None,
                )
                applied_safe_count += 1
                continue
            skipped.append(
                {
                    "line_label": item.line_label,
                    "reason": f"unsupported_action_kind:{item.action_kind}",
                }
            )
        except HTTPException as exc:
            skipped.append(
                {
                    "line_label": item.line_label,
                    "reason": str(exc.detail),
                }
            )

    session.commit()
    return {
        "assessment_setup_id": setup_id,
        "applied_safe_count": applied_safe_count,
        "alias_count": alias_count,
        "mapping_count": mapping_count,
        "residual_rule_approved_count": residual_rule_approved_count,
        "skipped": skipped,
    }


@router.post("/hoa/{hoa_id}/assessment-mapping-review/exemptions/{pool_key}")
def set_exemption_decision(
    hoa_id: int,
    pool_key: str,
    payload: ExemptionDecisionRequest,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    count = set_exemption_decision_state(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_year=payload.budget_year,
        budget_draft_id=payload.budget_draft_id,
        pool_key=pool_key,
        exemption_state=payload.exemption_state,
        decided_by=_actor(current_user),
        notes=payload.note,
        connection=raw_conn,
        commit=False,
    )
    if count == 0:
        raise HTTPException(status_code=404, detail="Exemption decision not found")
    session.commit()
    return {"pool_key": pool_key, "exemption_state": payload.exemption_state}


@router.post("/hoa/{hoa_id}/assessment-mapping-review/apply")
def apply_mapping_review(
    hoa_id: int,
    session: Session = Depends(get_session),
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    raw_conn = session.connection().connection
    setup_id = _setup_id_for_hoa(raw_conn, hoa_id)
    _draft_id, budget_lines = _budget_lines_for_active_draft(raw_conn, hoa_id)
    counts = materialize_budget_line_pool_mappings(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_lines=budget_lines,
        connection=raw_conn,
        commit=False,
    )
    review_rows = build_assessment_mapping_review_rows(
        property_id=hoa_id,
        assessment_setup_id=setup_id,
        budget_lines=budget_lines,
        connection=raw_conn,
    )
    mapped_rows = raw_conn.execute(
        """
        SELECT budget_line_normalized_label, section, category, fund_type,
               account_code, pool_key, mapping_source, review_state
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
        """,
        (hoa_id, setup_id),
    ).fetchall()
    mapped_by_key = {
        (str(row[0]), str(row[1]), str(row[2]), str(row[3]), row[4]): row
        for row in mapped_rows
    }
    line_results = []
    for item in review_rows:
        row = mapped_by_key.get(
            (
                item["normalized_label"],
                item["section"],
                item["category"],
                item["fund_type"],
                item["account_code"],
            )
        )
        line_results.append({
            "line_key": item["line_key"],
            "line_label": item["line_label"],
            "eligibility": item["eligibility"],
            "requires_mapping": item["included_in_regular_basis"],
            "status": "mapped" if row else item["status"],
            "pool_key": row[5] if row else None,
            "mapping_source": row[6] if row else None,
            "reason": item["reason"],
        })
    session.commit()
    return {"assessment_setup_id": setup_id, "counts": counts, "line_results": line_results}
