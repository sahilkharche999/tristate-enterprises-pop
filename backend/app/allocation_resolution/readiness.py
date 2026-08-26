"""Ordered allocation-readiness gates and structured issue codes."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.services.assessment_budget_mapping_rule_service import normalize_budget_label

from .enforcement import enforcement_level, should_block_final
from .schemas import (
    CURRENCY_TOLERANCE,
    ReadinessIssue,
    ReadinessReport,
)
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

_AMBIGUOUS_DECLARED = frozenset(
    {"custom_factor", "external_schedule", "unknown", "category"}
)


def _line_amount(line: dict[str, Any]) -> Decimal:
    for field in (
        "assessment_mapping_amount",
        "proposed_amount",
        "proposedAmount",
        "annual_budget",
        "projection",
        "amount",
    ):
        value = line.get(field)
        if value not in (None, ""):
            try:
                return Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                continue
    return Decimal("0")


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
    approved_slices = [item for item in slices if item.status == "approved"]
    decisions = list_category_decisions(
        connection, assessment_setup_id=assessment_setup_id
    )
    decision_index = {
        (d.pool_key, normalize_budget_label(d.category)): d for d in decisions
    }
    fix_path, fix_label = _hoa_fix(property_id)

    # 1. declared-rule resolution
    unresolved = [
        r
        for r in resolutions
        if r.status != "approved"
        and (
            r.declared_method in _AMBIGUOUS_DECLARED
            or r.resolved_method is None
        )
    ]
    missing_method = [
        r
        for r in resolutions
        if r.status in {"unresolved", "draft"} and r.resolved_method is None
    ]
    for rec in unresolved:
        if rec.referenced_schedule.schedule_type and not rec.referenced_schedule.available:
            issues.append(ReadinessIssue(
                code="referenced_schedule_missing",
                message=(
                    f"Assessment category {rec.pool_key!r} references "
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
                f"Assessment category {rec.pool_key!r} declared {rec.declared_method} "
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
                and sl.status == "approved"
                for sl in approved_slices
            )
            if decision and decision.decision in {"zero", "not_applicable", "mapped"}:
                continue
            if slice_hit:
                continue
            category_issues += 1
            issues.append(ReadinessIssue(
                code="required_category_unmapped",
                message=(
                    f"Required category {category!r} on assessment category {rec.pool_key!r} "
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
    # A governing-doc category can be a named part of a combined budget
    # description (gas ⊂ Electricity & Gas). That is not a required dollar
    # split — the operator assigns the whole source amount. Incomplete
    # operator-started splits are covered by slice_reconciliation.
    gates.append({"id": "combined_lines", "ok": True, "count": 0})

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
                        f"Assessment category {rec.pool_key!r} resolved as {rec.resolved_method} "
                        "but has no recipient factor snapshot."
                    ),
                    target=f"factors:{rec.pool_key}",
                    fix_path=fix_path,
                    fix_label=fix_label,
                    details={"pool_key": rec.pool_key},
                ))
                continue
            setup_row = connection.execute(
                "SELECT setup_type FROM assessment_setups WHERE id = ?",
                (assessment_setup_id,),
            ).fetchone()
            pool_row = connection.execute(
                "SELECT recipient_scope FROM allocation_pools "
                "WHERE assessment_setup_id = ? AND pool_key = ?",
                (assessment_setup_id, rec.pool_key),
            ).fetchone()
            scope = str(pool_row[0] or "all_units") if pool_row else "all_units"
            snapshot_keys = {str(key) for key in recipients}
            missing_recipients: list[str] = []
            if scope != "custom_unit_list" and setup_row:
                if str(setup_row[0]) == "grouped":
                    expected_rows = connection.execute(
                        "SELECT group_name FROM assessment_groups "
                        "WHERE assessment_setup_id = ?",
                        (assessment_setup_id,),
                    ).fetchall()
                    expected_keys = [
                        (str(row[0]), str(row[0]))
                        for row in expected_rows
                    ]
                else:
                    expected_rows = connection.execute(
                        "SELECT id, unit_number, category, parking_spaces "
                        "FROM assessment_units WHERE assessment_setup_id = ?",
                        (assessment_setup_id,),
                    ).fetchall()
                    expected_keys = [
                        (str(unit_id), str(unit_number))
                        for unit_id, unit_number, category, parking_spaces in expected_rows
                        if (
                            scope == "all_units"
                            or (
                                scope == "residential_only"
                                and str(category or "").lower() == "residential"
                            )
                            or (
                                scope == "commercial_only"
                                and str(category or "").lower() == "commercial"
                            )
                            or (
                                scope == "parking_users"
                                and int(parking_spaces or 0) > 0
                            )
                        )
                    ]
                missing_recipients = sorted(
                    unit_number
                    for unit_id, unit_number in expected_keys
                    if unit_id not in snapshot_keys and unit_number not in snapshot_keys
                )
            if missing_recipients:
                factor_issues += 1
                issues.append(ReadinessIssue(
                    code="invalid_factor_set",
                    message=(
                        f"Assessment category {rec.pool_key!r} factor snapshot is missing "
                        f"recipient(s): {', '.join(missing_recipients[:8])}."
                    ),
                    target=f"factors:{rec.pool_key}",
                    fix_path=fix_path,
                    fix_label=fix_label,
                    details={
                        "pool_key": rec.pool_key,
                        "missing_recipients": missing_recipients,
                    },
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
                            f"Assessment category {rec.pool_key!r} square-footage factors "
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
                            f"Assessment category {rec.pool_key!r} ownership factors sum to {total}, "
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
    grouped: dict[tuple[str, Optional[str]], list] = {}
    for sl in approved_slices:
        grouped.setdefault(
            (sl.source_line_normalized_label, sl.source_line_account_code),
            [],
        ).append(sl)
    for (label, account_code), group in grouped.items():
        residual = validate_slice_sum(group[0].source_annual_amount, [s.slice_annual_amount for s in group])
        active_matches = [
            line
            for line in budget_lines
            if normalize_budget_label(
                str(line.get("label") or line.get("normalized_label") or "")
            ) == label
            and (
                account_code in (None, "")
                or str(line.get("account_code") or "") == str(account_code)
            )
        ]
        source_mismatch = (
            not active_matches
            or any(
                _line_amount(line) != group[0].source_annual_amount
                for line in active_matches
            )
        )
        if residual != Decimal("0") or source_mismatch:
            slice_issues += 1
            issues.append(ReadinessIssue(
                code="slice_reconciliation_failed",
                message=(
                    f"Slices for {label!r} do not match the active source budget "
                    f"amount {group[0].source_annual_amount} (delta {residual})."
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
                "Residual/default assessment categories cannot absorb unresolved exception "
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
        r.source in {"promotion", "operator"}
        and (
            r.status != "approved"
            or (
                r.resolved_method in {"square_footage", "ownership_percentage"}
                and not r.factor_snapshot.recipients
            )
        )
        for r in resolutions
    )
    block_final = should_block_final(
        has_blocking_issues=bool(blocking),
        has_new_unresolved=has_new_unresolved,
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
        ready_for_final=not block_final,
        preview_available=preview_ok or any_draft_method,
        enforcement=enforcement_level(),
        has_new_unresolved=has_new_unresolved,
        issues=issues,
        gates=gates,
    )


def readiness_blocks_final(report: ReadinessReport) -> bool:
    if report.ready_for_final:
        return False
    return should_block_final(
        has_blocking_issues=any(i.severity == "blocking" for i in report.issues),
        has_new_unresolved=report.has_new_unresolved,
    )
