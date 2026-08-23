"""Ordered allocation-readiness gates and structured issue codes."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from typing import Any, Optional

from app.services.assessment_budget_mapping_rule_service import normalize_budget_label

from .enforcement import enforcement_level, should_block_final
from .schemas import (
    CURRENCY_TOLERANCE,
    ReadinessIssue,
    ReadinessReport,
)
from .semantic_mapping import classify_label_match
from .service import (
    list_category_decisions,
    list_current_resolutions,
    list_slices,
    validate_slice_sum,
)


READINESS_GATES = (
    "rule_resolution",
    "category_coverage",
    "combined_lines",
    "factor_reconciliation",
    "slice_reconciliation",
    "pool_reconciliation",
    "recipient_totals",
    "approval",
)

_AMBIGUOUS_DECLARED = frozenset({"custom_factor", "external_schedule", "unknown"})


def _hoa_fix(hoa_id: int, target: str = "") -> tuple[str, str]:
    path = f"/hoa/{hoa_id}/assessment-mapping-review"
    if target:
        path = f"{path}#{target}"
    return path, "Resolve allocation issues"


def evaluate_readiness(
    connection: sqlite3.Connection,
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_lines: Optional[list[dict[str, Any]]] = None,
    residual_pool_keys: Optional[set[str]] = None,
    preview_recipient_totals: Optional[dict[str, Decimal]] = None,
    approved_recipient_totals: Optional[dict[str, Decimal]] = None,
    approved_hoa_annual: Optional[Decimal] = None,
) -> ReadinessReport:
    issues: list[ReadinessIssue] = []
    gates: list[dict[str, Any]] = []
    budget_lines = budget_lines or []
    residual_pool_keys = residual_pool_keys or set()
    resolutions = list_current_resolutions(
        connection, assessment_setup_id=assessment_setup_id
    )
    slices = list_slices(connection, assessment_setup_id=assessment_setup_id)
    decisions = list_category_decisions(
        connection, assessment_setup_id=assessment_setup_id
    )
    decision_index = {
        (d.pool_key, normalize_budget_label(d.category)): d for d in decisions
    }
    fix_path, fix_label = _hoa_fix(property_id)

    # 1. declared-rule resolution
    unresolved = [
        r for r in resolutions
        if r.declared_method in _AMBIGUOUS_DECLARED and r.status != "approved"
    ]
    missing_method = [
        r for r in resolutions
        if r.status in {"unresolved", "draft"} and r.declared_method in _AMBIGUOUS_DECLARED
        and r.resolved_method is None
    ]
    for rec in unresolved:
        if rec.referenced_schedule.schedule_type and not rec.referenced_schedule.available:
            issues.append(ReadinessIssue(
                code="referenced_schedule_missing",
                message=(
                    f"Pool {rec.pool_key!r} references "
                    f"{rec.referenced_schedule.schedule_name or 'an external schedule'} "
                    "that is not attached."
                ),
                target=f"pool:{rec.pool_key}",
                fix_path=fix_path,
                fix_label=fix_label,
                details={"pool_key": rec.pool_key},
            ))
        issues.append(ReadinessIssue(
            code="allocation_resolution_required",
            message=(
                f"Pool {rec.pool_key!r} declared {rec.declared_method} "
                f"({rec.declared_denominator_label or 'no denominator label'}) "
                "and has no approved executable method."
            ),
            target=f"pool:{rec.pool_key}",
            fix_path=fix_path,
            fix_label=fix_label,
            details={"pool_key": rec.pool_key, "declared_method": rec.declared_method},
        ))
    gates.append({
        "id": "rule_resolution",
        "ok": not unresolved,
        "count": len(unresolved),
    })

    # 2. required category coverage
    category_issues = 0
    for rec in resolutions:
        if rec.declared_method == "equal" and rec.included_categories == []:
            continue
        for category in rec.included_categories:
            key = (rec.pool_key, normalize_budget_label(category))
            decision = decision_index.get(key)
            slice_hit = any(
                normalize_budget_label(sl.semantic_category) == normalize_budget_label(category)
                and sl.pool_key == rec.pool_key
                for sl in slices
            )
            if decision and decision.decision in {"zero", "not_applicable", "mapped"}:
                continue
            if slice_hit:
                continue
            category_issues += 1
            issues.append(ReadinessIssue(
                code="required_category_unmapped",
                message=(
                    f"Required category {category!r} on pool {rec.pool_key!r} "
                    "is not mapped, documented as $0, or marked not applicable."
                ),
                target=f"category:{rec.pool_key}:{normalize_budget_label(category)}",
                fix_path=fix_path,
                fix_label=fix_label,
                details={"pool_key": rec.pool_key, "category": category},
            ))
    gates.append({
        "id": "category_coverage",
        "ok": category_issues == 0,
        "count": category_issues,
    })

    # 3. combined / partial lines
    combined = 0
    line_by_label = {
        normalize_budget_label(str(line.get("label") or line.get("normalized_label") or "")): line
        for line in budget_lines
    }
    for rec in resolutions:
        for category in rec.included_categories:
            for label, line in line_by_label.items():
                kind = classify_label_match(category, label)
                if kind != "combined":
                    continue
                line_slices = [
                    sl for sl in slices
                    if sl.source_line_normalized_label == label
                ]
                if line_slices:
                    continue
                combined += 1
                issues.append(ReadinessIssue(
                    code="combined_line_requires_split",
                    message=(
                        f"Category {category!r} matches only part of budget line "
                        f"{line.get('label')!r}. Split the line before assigning it."
                    ),
                    target=f"line:{label}",
                    fix_path=fix_path,
                    fix_label=fix_label,
                    details={
                        "category": category,
                        "line_label": line.get("label"),
                        "pool_key": rec.pool_key,
                    },
                ))
    gates.append({"id": "combined_lines", "ok": combined == 0, "count": combined})

    # 4. factor reconciliation
    factor_issues = 0
    for rec in resolutions:
        if rec.status == "unresolved" or rec.resolved_method is None:
            continue
        if rec.resolved_method in {"square_footage", "ownership_percentage"}:
            recipients = rec.factor_snapshot.recipients
            if not recipients:
                factor_issues += 1
                issues.append(ReadinessIssue(
                    code="invalid_factor_set",
                    message=(
                        f"Pool {rec.pool_key!r} resolved as {rec.resolved_method} "
                        "but has no recipient factor snapshot."
                    ),
                    target=f"factors:{rec.pool_key}",
                    fix_path=fix_path,
                    fix_label=fix_label,
                    details={"pool_key": rec.pool_key},
                ))
                continue
            if rec.resolved_method == "square_footage":
                total = sum(recipients.values(), start=Decimal("0"))
                denom = rec.factor_snapshot.denominator_value
                if denom is None or abs(total - denom) > CURRENCY_TOLERANCE:
                    factor_issues += 1
                    issues.append(ReadinessIssue(
                        code="invalid_factor_set",
                        message=(
                            f"Pool {rec.pool_key!r} square-footage factors "
                            f"sum to {total} but denominator is {denom}."
                        ),
                        target=f"factors:{rec.pool_key}",
                        fix_path=fix_path,
                        fix_label=fix_label,
                        details={"pool_key": rec.pool_key, "sum": str(total), "denominator": str(denom)},
                    ))
            if rec.resolved_method == "ownership_percentage":
                total = sum(recipients.values(), start=Decimal("0"))
                if abs(total - Decimal("1")) > Decimal("0.0001") and abs(total - Decimal("100")) > Decimal("0.02"):
                    factor_issues += 1
                    issues.append(ReadinessIssue(
                        code="invalid_factor_set",
                        message=(
                            f"Pool {rec.pool_key!r} ownership factors sum to {total}, "
                            "not 1 or 100."
                        ),
                        target=f"factors:{rec.pool_key}",
                        fix_path=fix_path,
                        fix_label=fix_label,
                        details={"pool_key": rec.pool_key, "sum": str(total)},
                    ))
    gates.append({"id": "factor_reconciliation", "ok": factor_issues == 0, "count": factor_issues})

    # 5. slice-to-source reconciliation
    slice_issues = 0
    grouped: dict[str, list] = {}
    for sl in slices:
        grouped.setdefault(sl.source_line_normalized_label, []).append(sl)
    for label, group in grouped.items():
        residual = validate_slice_sum(group[0].source_annual_amount, [s.slice_annual_amount for s in group])
        if residual != Decimal("0"):
            slice_issues += 1
            issues.append(ReadinessIssue(
                code="slice_reconciliation_failed",
                message=(
                    f"Slices for {label!r} do not sum to the source amount "
                    f"{group[0].source_annual_amount} (delta {residual})."
                ),
                target=f"line:{label}",
                fix_path=fix_path,
                fix_label=fix_label,
                details={"line": label, "delta": str(residual)},
            ))
    gates.append({"id": "slice_reconciliation", "ok": slice_issues == 0, "count": slice_issues})

    # 6. residual must not absorb unresolved exceptions
    pool_issues = 0
    unresolved_exception_categories = []
    for rec in resolutions:
        if rec.pool_key in residual_pool_keys:
            continue
        if rec.declared_method not in _AMBIGUOUS_DECLARED and rec.status == "approved":
            continue
        if rec.status == "approved" and rec.resolved_method:
            continue
        unresolved_exception_categories.extend(
            (rec.pool_key, cat) for cat in rec.included_categories
        )
    if unresolved_exception_categories:
        pool_issues += 1
        issues.append(ReadinessIssue(
            code="pool_reconciliation_failed",
            message=(
                "Residual/default pools cannot absorb unresolved exception "
                "categories. Resolve or document each explicit category first."
            ),
            target="residual",
            fix_path=fix_path,
            fix_label=fix_label,
            details={"categories": [c[1] for c in unresolved_exception_categories]},
        ))
    gates.append({"id": "pool_reconciliation", "ok": pool_issues == 0, "count": pool_issues})

    # 7. recipient totals (optional; used when a preview overlay is supplied)
    recipient_issues = 0
    if preview_recipient_totals is not None and approved_recipient_totals is not None:
        for unit, expected in approved_recipient_totals.items():
            actual = preview_recipient_totals.get(unit)
            if actual is None or abs(actual - expected) > CURRENCY_TOLERANCE:
                recipient_issues += 1
                issues.append(ReadinessIssue(
                    code="recipient_total_mismatch",
                    message=(
                        f"Unit {unit} proposed {actual} does not match "
                        f"the approved comparison total {expected}."
                    ),
                    target=f"unit:{unit}",
                    fix_path=fix_path,
                    fix_label=fix_label,
                    details={"unit": unit, "proposed": str(actual), "expected": str(expected)},
                ))
    if approved_hoa_annual is not None and preview_recipient_totals is not None:
        proposed = sum(preview_recipient_totals.values(), start=Decimal("0"))
        # monthly totals → compare annualized if values look monthly
        annualized = proposed * Decimal("12") if proposed < approved_hoa_annual / Decimal("2") else proposed
        if abs(annualized - approved_hoa_annual) > Decimal("0.12"):
            recipient_issues += 1
            issues.append(ReadinessIssue(
                code="recipient_total_mismatch",
                message=(
                    f"HOA total {annualized} does not match approved revenue "
                    f"{approved_hoa_annual}."
                ),
                target="hoa-total",
                fix_path=fix_path,
                fix_label=fix_label,
                details={"proposed": str(annualized), "approved": str(approved_hoa_annual)},
            ))
    gates.append({"id": "recipient_totals", "ok": recipient_issues == 0, "count": recipient_issues})

    # 8. approval
    pending_approval = [
        r for r in resolutions
        if r.declared_method in _AMBIGUOUS_DECLARED and r.status != "approved"
    ] + missing_method
    # de-dupe by pool
    pending_keys = {r.pool_key for r in pending_approval}
    if pending_keys:
        issues.append(ReadinessIssue(
            code="approval_required",
            message=(
                "Approve the allocation resolution after factors, categories, "
                "and slices reconcile."
            ),
            target="approval",
            fix_path=fix_path,
            fix_label=fix_label,
            details={"pools": sorted(pending_keys)},
        ))
    gates.append({"id": "approval", "ok": not pending_keys, "count": len(pending_keys)})

    blocking = [i for i in issues if i.severity == "blocking"]
    has_new_unresolved = any(
        r.source == "promotion" and r.status != "approved" and r.declared_method in _AMBIGUOUS_DECLARED
        for r in resolutions
    )
    block_final = should_block_final(
        has_blocking_issues=bool(blocking),
        has_new_unresolved=has_new_unresolved or any(
            r.source == "operator" and r.status != "approved" and r.declared_method in _AMBIGUOUS_DECLARED
            for r in resolutions
        ),
    )
    # Draft preview is available when enough data exists to compute without inventing.
    preview_ok = factor_issues == 0 and slice_issues == 0 and all(
        r.resolved_method is not None or r.status == "approved" or r.declared_method == "equal"
        for r in resolutions
        if r.declared_method not in _AMBIGUOUS_DECLARED or r.status == "draft"
    )
    # Incomplete unresolved custom factors: preview may still show declared state.
    any_draft_method = any(
        r.resolved_method is not None for r in resolutions if r.status in {"draft", "approved"}
    )
    return ReadinessReport(
        ready_for_final=not blocking and not pending_keys,
        preview_available=preview_ok or any_draft_method,
        enforcement=enforcement_level(),
        issues=issues,
        gates=gates,
    )


def readiness_blocks_final(report: ReadinessReport) -> bool:
    if report.ready_for_final:
        return False
    has_new = any(
        i.code in {
            "allocation_resolution_required",
            "referenced_schedule_missing",
            "required_category_unmapped",
            "combined_line_requires_split",
            "invalid_factor_set",
            "slice_reconciliation_failed",
            "pool_reconciliation_failed",
            "approval_required",
        }
        for i in report.issues
    )
    return should_block_final(has_blocking_issues=has_new, has_new_unresolved=has_new)
