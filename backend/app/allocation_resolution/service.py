"""CRUD for allocation-resolution records, slices, and category decisions."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from .schemas import (
    CURRENCY_TOLERANCE,
    AllocationResolutionRecord,
    BudgetLineSlice,
    CanonicalAllocationMethod,
    CategoryCoverageDecision,
    FactorSnapshot,
    ReferencedSchedule,
    ResolutionEvidence,
)


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _loads_list(raw: Any) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _loads_dict(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decimal_or_none(raw: Any) -> Optional[Decimal]:
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def infer_referenced_schedule(
    declared_method: str,
    denominator_label: str,
) -> ReferencedSchedule:
    label = (denominator_label or "").strip()
    text = label.lower()
    if declared_method in {"custom_factor", "external_schedule"} or "dre" in text or "proration" in text:
        schedule_type = "dre_operating_budget" if ("dre" in text or "proration" in text) else "external_schedule"
        return ReferencedSchedule(
            schedule_type=schedule_type,
            schedule_name=label or "external schedule",
            available=False,
        )
    return ReferencedSchedule()


def _row_dict(row: Any, description: Optional[tuple] = None) -> dict:
    if row is None:
        return {}
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, dict):
        return row
    if description:
        return {col[0]: row[idx] for idx, col in enumerate(description)}
    return dict(row)


def _row_to_resolution(row: Any, description: Optional[tuple] = None) -> AllocationResolutionRecord:
    row = _row_dict(row, description)
    snapshot_raw = _loads_dict(row.get("factor_snapshot_json"))
    recipients = {
        str(k): Decimal(str(v))
        for k, v in (snapshot_raw.get("recipients") or {}).items()
        if v not in (None, "")
    }
    return AllocationResolutionRecord(
        id=row.get("id"),
        property_id=int(row["property_id"]),
        assessment_setup_id=int(row["assessment_setup_id"]),
        pool_key=str(row["pool_key"]),
        version_int=int(row.get("version_int") or 1),
        status=row.get("status") or "unresolved",
        declared_method=row.get("declared_method") or "unknown",
        declared_denominator_label=row.get("declared_denominator_label") or "",
        referenced_schedule=ReferencedSchedule(
            schedule_type=row.get("referenced_schedule_type"),
            schedule_name=row.get("referenced_schedule_name"),
            available=bool(row.get("evidence_document_id") or row.get("prior_schedule_package_id")),
            document_id=row.get("evidence_document_id"),
            prior_package_id=row.get("prior_schedule_package_id"),
        ),
        included_categories=_loads_list(row.get("included_categories_json")),
        excluded_categories=_loads_list(row.get("excluded_categories_json")),
        evidence=ResolutionEvidence(
            source_pages=[int(p) for p in _loads_list(row.get("source_pages_json")) if str(p).isdigit() or isinstance(p, int)],
            source_text=row.get("source_evidence_text") or "",
            reason=row.get("reason") or "",
            document_id=row.get("evidence_document_id"),
            prior_package_id=row.get("prior_schedule_package_id"),
        ),
        resolved_method=row.get("resolved_method") or None,
        factor_snapshot=FactorSnapshot(
            method=row.get("resolved_method") or snapshot_raw.get("method"),
            denominator_value=_decimal_or_none(row.get("denominator_value") or snapshot_raw.get("denominator_value")),
            denominator_source=row.get("denominator_source") or snapshot_raw.get("denominator_source"),
            recipients=recipients,
        ),
        source=row.get("source") or "promotion",
        created_by=row.get("created_by") or "",
        created_at=row.get("created_at"),
        approved_by=row.get("approved_by"),
        approved_at=row.get("approved_at"),
    )


def current_resolution(
    connection: sqlite3.Connection,
    *,
    assessment_setup_id: int,
    pool_key: str,
) -> Optional[AllocationResolutionRecord]:
    cur = connection.execute(
        """
        SELECT * FROM allocation_resolutions
         WHERE assessment_setup_id = ? AND pool_key = ?
           AND status IN ('unresolved', 'draft', 'approved')
         ORDER BY CASE status WHEN 'approved' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,
                  version_int DESC, id DESC
         LIMIT 1
        """,
        (assessment_setup_id, pool_key),
    )
    row = cur.fetchone()
    return _row_to_resolution(row, cur.description) if row else None


def list_current_resolutions(
    connection: sqlite3.Connection,
    *,
    assessment_setup_id: int,
) -> list[AllocationResolutionRecord]:
    cur = connection.execute(
        """
        SELECT * FROM allocation_resolutions
         WHERE assessment_setup_id = ?
           AND status IN ('unresolved', 'draft', 'approved')
         ORDER BY pool_key, version_int DESC, id DESC
        """,
        (assessment_setup_id,),
    )
    rows = cur.fetchall()
    seen: set[str] = set()
    out: list[AllocationResolutionRecord] = []
    for row in rows:
        rec = _row_to_resolution(row, cur.description)
        if rec.pool_key in seen:
            continue
        seen.add(rec.pool_key)
        out.append(rec)
    return out


def _next_version(connection: sqlite3.Connection, setup_id: int, pool_key: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version_int), 0) FROM allocation_resolutions "
        "WHERE assessment_setup_id = ? AND pool_key = ?",
        (setup_id, pool_key),
    ).fetchone()
    return int(row[0] or 0) + 1


def create_resolution(
    connection: sqlite3.Connection,
    record: AllocationResolutionRecord,
    *,
    commit: bool = True,
) -> AllocationResolutionRecord:
    version = record.version_int or _next_version(
        connection, record.assessment_setup_id, record.pool_key
    )
    schedule = record.referenced_schedule
    snapshot = record.factor_snapshot
    cur = connection.execute(
        """
        INSERT INTO allocation_resolutions (
            property_id, assessment_setup_id, pool_key, version_int, status,
            declared_method, declared_denominator_label,
            referenced_schedule_type, referenced_schedule_name,
            included_categories_json, excluded_categories_json,
            source_pages_json, source_evidence_text, resolved_method,
            denominator_value, denominator_source, factor_snapshot_json,
            evidence_document_id, prior_schedule_package_id, reason,
            created_by, approved_by, approved_at, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.property_id,
            record.assessment_setup_id,
            record.pool_key,
            version,
            record.status,
            record.declared_method,
            record.declared_denominator_label,
            schedule.schedule_type,
            schedule.schedule_name,
            _dumps(record.included_categories),
            _dumps(record.excluded_categories),
            _dumps(record.evidence.source_pages),
            record.evidence.source_text,
            record.resolved_method,
            str(snapshot.denominator_value) if snapshot.denominator_value is not None else None,
            snapshot.denominator_source,
            _dumps({
                "method": snapshot.method or record.resolved_method,
                "denominator_value": (
                    str(snapshot.denominator_value) if snapshot.denominator_value is not None else None
                ),
                "denominator_source": snapshot.denominator_source,
                "recipients": {k: str(v) for k, v in snapshot.recipients.items()},
            }),
            record.evidence.document_id,
            record.evidence.prior_package_id,
            record.evidence.reason,
            record.created_by,
            record.approved_by,
            record.approved_at,
            record.source,
        ),
    )
    if commit:
        connection.commit()
    loaded = current_resolution(
        connection,
        assessment_setup_id=record.assessment_setup_id,
        pool_key=record.pool_key,
    )
    if loaded is None:
        record.id = cur.lastrowid
        record.version_int = version
        return record
    return loaded


def supersede_open_resolutions(
    connection: sqlite3.Connection,
    *,
    assessment_setup_id: int,
    pool_key: str,
) -> None:
    connection.execute(
        """
        UPDATE allocation_resolutions
           SET status = 'superseded'
         WHERE assessment_setup_id = ? AND pool_key = ?
           AND status IN ('unresolved', 'draft', 'approved')
        """,
        (assessment_setup_id, pool_key),
    )


def save_draft_resolution(
    connection: sqlite3.Connection,
    *,
    property_id: int,
    assessment_setup_id: int,
    pool_key: str,
    declared_method: str,
    resolved_method: Optional[CanonicalAllocationMethod],
    factor_snapshot: Optional[FactorSnapshot] = None,
    referenced_schedule: Optional[ReferencedSchedule] = None,
    included_categories: Optional[list[str]] = None,
    excluded_categories: Optional[list[str]] = None,
    evidence: Optional[ResolutionEvidence] = None,
    declared_denominator_label: str = "",
    actor: str = "",
    source: str = "operator",
) -> AllocationResolutionRecord:
    existing = current_resolution(
        connection, assessment_setup_id=assessment_setup_id, pool_key=pool_key
    )
    supersede_open_resolutions(
        connection, assessment_setup_id=assessment_setup_id, pool_key=pool_key
    )
    base_categories = included_categories
    if base_categories is None:
        base_categories = existing.included_categories if existing else []
    excl = excluded_categories
    if excl is None:
        excl = existing.excluded_categories if existing else []
    ev = evidence or (existing.evidence if existing else ResolutionEvidence())
    sched = referenced_schedule or (existing.referenced_schedule if existing else ReferencedSchedule())
    snap = factor_snapshot or FactorSnapshot(method=resolved_method)
    return create_resolution(
        connection,
        AllocationResolutionRecord(
            property_id=property_id,
            assessment_setup_id=assessment_setup_id,
            pool_key=pool_key,
            version_int=_next_version(connection, assessment_setup_id, pool_key),
            status="draft",
            declared_method=declared_method,  # type: ignore[arg-type]
            declared_denominator_label=declared_denominator_label
            or (existing.declared_denominator_label if existing else ""),
            referenced_schedule=sched,
            included_categories=base_categories,
            excluded_categories=excl,
            evidence=ev,
            resolved_method=resolved_method,
            factor_snapshot=snap,
            source=source,  # type: ignore[arg-type]
            created_by=actor,
        ),
    )


def approve_resolution(
    connection: sqlite3.Connection,
    *,
    property_id: int,
    assessment_setup_id: int,
    pool_key: str,
    resolved_method: CanonicalAllocationMethod,
    factor_snapshot: FactorSnapshot,
    evidence: ResolutionEvidence,
    actor: str,
    declared_method: Optional[str] = None,
    declared_denominator_label: str = "",
    referenced_schedule: Optional[ReferencedSchedule] = None,
    included_categories: Optional[list[str]] = None,
    apply_to_pool: bool = True,
) -> AllocationResolutionRecord:
    if resolved_method != "equal" and not factor_snapshot.recipients:
        raise ValueError(
            f"Approved {resolved_method} resolution requires recipient factors."
        )
    if resolved_method == "square_footage" and factor_snapshot.denominator_value is None:
        raise ValueError("Square-footage approval requires a denominator.")
    existing = current_resolution(
        connection, assessment_setup_id=assessment_setup_id, pool_key=pool_key
    )
    supersede_open_resolutions(
        connection, assessment_setup_id=assessment_setup_id, pool_key=pool_key
    )
    rec = create_resolution(
        connection,
        AllocationResolutionRecord(
            property_id=property_id,
            assessment_setup_id=assessment_setup_id,
            pool_key=pool_key,
            version_int=_next_version(connection, assessment_setup_id, pool_key),
            status="approved",
            declared_method=(declared_method or (existing.declared_method if existing else "unknown")),  # type: ignore[arg-type]
            declared_denominator_label=declared_denominator_label
            or (existing.declared_denominator_label if existing else ""),
            referenced_schedule=referenced_schedule
            or (existing.referenced_schedule if existing else ReferencedSchedule()),
            included_categories=included_categories
            if included_categories is not None
            else (existing.included_categories if existing else []),
            excluded_categories=existing.excluded_categories if existing else [],
            evidence=evidence,
            resolved_method=resolved_method,
            factor_snapshot=factor_snapshot,
            source="operator",
            created_by=actor,
            approved_by=actor,
            approved_at="now",
        ),
    )
    if apply_to_pool:
        connection.execute(
            """
            UPDATE allocation_pools
               SET allocation_method = ?,
                   denominator_value = ?,
                   denominator_source = COALESCE(?, denominator_source)
             WHERE assessment_setup_id = ? AND pool_key = ?
            """,
            (
                resolved_method,
                str(factor_snapshot.denominator_value)
                if factor_snapshot.denominator_value is not None
                else None,
                factor_snapshot.denominator_source,
                assessment_setup_id,
                pool_key,
            ),
        )
        connection.commit()
    return rec


def list_slices(
    connection: sqlite3.Connection,
    *,
    assessment_setup_id: int,
    source_line_normalized_label: Optional[str] = None,
    source_line_key: Optional[str] = None,
    source_line_account_code: Optional[str] = None,
    statuses: tuple[str, ...] = ("draft", "approved"),
) -> list[BudgetLineSlice]:
    if not statuses:
        return []
    columns = _slice_table_columns(connection)
    placeholders = ", ".join("?" for _ in statuses)
    sql = """
        SELECT * FROM budget_line_allocation_slices
         WHERE assessment_setup_id = ? AND status IN (
    """ + placeholders + ")"
    params: list[Any] = [assessment_setup_id, *statuses]
    if source_line_normalized_label:
        sql += " AND source_line_normalized_label = ?"
        params.append(source_line_normalized_label)
    if source_line_key is not None and "source_line_key" in columns:
        sql += " AND COALESCE(source_line_key, '') = COALESCE(?, '')"
        params.append(source_line_key)
    if source_line_account_code is not None:
        sql += " AND COALESCE(source_line_account_code, '') = COALESCE(?, '')"
        params.append(source_line_account_code)
    sql += " ORDER BY id"
    cur = connection.execute(sql, params)
    return [_row_to_slice(_row_dict(row, cur.description)) for row in cur.fetchall()]


def _slice_table_columns(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(budget_line_allocation_slices)"
        ).fetchall()
    }


def _row_to_slice(row: sqlite3.Row | dict) -> BudgetLineSlice:
    data = dict(row)
    return BudgetLineSlice(
        id=data.get("id"),
        property_id=int(data["property_id"]),
        assessment_setup_id=int(data["assessment_setup_id"]),
        source_line_normalized_label=str(data["source_line_normalized_label"]),
        source_line_key=data.get("source_line_key"),
        source_line_account_code=data.get("source_line_account_code"),
        source_annual_amount=Decimal(str(data["source_annual_amount"])),
        slice_annual_amount=Decimal(str(data["slice_annual_amount"])),
        slice_percent=_decimal_or_none(data.get("slice_percent")),
        pool_key=str(data["pool_key"]),
        semantic_category=str(data.get("semantic_category") or ""),
        status=data.get("status") or "draft",
        evidence_text=data.get("evidence_text") or "",
        reason=data.get("reason") or "",
        created_by=data.get("created_by") or "",
        created_at=data.get("created_at"),
        approved_by=data.get("approved_by"),
        approved_at=data.get("approved_at"),
    )


def validate_slice_sum(
    source_annual_amount: Decimal,
    slice_amounts: list[Decimal],
) -> Decimal:
    """Return residual (source - slices). Zero within currency tolerance is balanced."""
    residual = source_annual_amount - sum(slice_amounts, start=Decimal("0"))
    if abs(residual) <= CURRENCY_TOLERANCE:
        return Decimal("0")
    return residual


def upsert_slices_for_line(
    connection: sqlite3.Connection,
    *,
    property_id: int,
    assessment_setup_id: int,
    source_line_normalized_label: str,
    source_line_key: Optional[str] = None,
    source_line_account_code: Optional[str] = None,
    source_annual_amount: Decimal,
    slices: list[dict[str, Any]],
    actor: str,
    replace: bool = True,
    valid_pool_keys: Optional[set[str]] = None,
    commit: bool = True,
) -> list[BudgetLineSlice]:
    if not str(source_line_normalized_label or "").strip():
        raise ValueError("A source budget line is required.")
    setup_row = connection.execute(
        "SELECT property_id FROM assessment_setups WHERE id = ?",
        (assessment_setup_id,),
    ).fetchone()
    if setup_row is not None and int(setup_row[0]) != int(property_id):
        raise ValueError("The assessment setup does not belong to this HOA.")
    if not source_annual_amount.is_finite():
        raise ValueError("Source annual amount must be finite.")
    if source_annual_amount < 0:
        raise ValueError("Source annual amount cannot be negative.")
    if len(slices) < 2:
        raise ValueError("A split must contain at least two slices.")
    if any(not isinstance(item, dict) for item in slices):
        raise ValueError("Each split entry must be an object.")
    try:
        amounts = [Decimal(str(item["slice_annual_amount"])) for item in slices]
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Each slice must have a valid annual amount.") from exc
    if any(not amount.is_finite() for amount in amounts):
        raise ValueError("Slice amounts must be finite numbers.")
    if any(amount < 0 for amount in amounts):
        raise ValueError("Slice amounts cannot be negative.")
    destinations = [str(item.get("pool_key") or "").strip() for item in slices]
    if any(not destination for destination in destinations):
        raise ValueError("Each slice must have an assessment category destination.")
    if any(not str(item.get("semantic_category") or "").strip() for item in slices):
        raise ValueError("Each slice must name the governing-document category.")
    if valid_pool_keys is not None:
        unknown = sorted(set(destinations) - set(valid_pool_keys))
        if unknown:
            raise ValueError(
                f"Assessment category {unknown[0]!r} is not available in this setup."
            )
    residual = validate_slice_sum(source_annual_amount, amounts)
    if residual != Decimal("0"):
        raise ValueError(
            f"Slices for {source_line_normalized_label!r} differ from source "
            f"{source_annual_amount} by {residual}"
        )
    slice_columns = _slice_table_columns(connection)
    if replace:
        source_key_filter = (
            "AND COALESCE(source_line_key, '') = COALESCE(?, '')"
            if "source_line_key" in slice_columns
            else ""
        )
        replace_params: list[Any] = [
            assessment_setup_id,
            source_line_normalized_label,
        ]
        if "source_line_key" in slice_columns:
            replace_params.append(source_line_key)
        replace_params.append(source_line_account_code)
        connection.execute(
            f"""
            UPDATE budget_line_allocation_slices
               SET status = 'superseded'
             WHERE assessment_setup_id = ?
               AND source_line_normalized_label = ?
               {source_key_filter}
               AND COALESCE(source_line_account_code, '') = COALESCE(?, '')
               AND status IN ('draft', 'approved')
            """,
            replace_params,
        )
    created: list[BudgetLineSlice] = []
    for item in slices:
        amount = Decimal(str(item["slice_annual_amount"]))
        pct = (
            (amount / source_annual_amount)
            if source_annual_amount != 0
            else Decimal("0")
        )
        if "source_line_key" in slice_columns:
            insert_columns = (
                "property_id, assessment_setup_id, "
                "source_line_normalized_label, source_line_key, "
                "source_line_account_code, source_annual_amount, "
                "slice_annual_amount, slice_percent, pool_key, "
                "semantic_category, status, evidence_text, reason, created_by"
            )
            insert_values = (
                property_id,
                assessment_setup_id,
                source_line_normalized_label,
                source_line_key,
                source_line_account_code,
                str(source_annual_amount),
                str(amount),
                str(pct),
                destinations[len(created)],
                str(item.get("semantic_category") or ""),
                str(item.get("evidence_text") or ""),
                str(item.get("reason") or ""),
                actor,
            )
        else:
            insert_columns = (
                "property_id, assessment_setup_id, "
                "source_line_normalized_label, source_line_account_code, "
                "source_annual_amount, slice_annual_amount, slice_percent, "
                "pool_key, semantic_category, status, evidence_text, reason, created_by"
            )
            insert_values = (
                property_id,
                assessment_setup_id,
                source_line_normalized_label,
                source_line_account_code,
                str(source_annual_amount),
                str(amount),
                str(pct),
                destinations[len(created)],
                str(item.get("semantic_category") or ""),
                str(item.get("evidence_text") or ""),
                str(item.get("reason") or ""),
                actor,
            )
        cur = connection.execute(
            f"""
            INSERT INTO budget_line_allocation_slices ({insert_columns})
            VALUES (
                {", ".join("?" for _ in insert_values[:-3])},
                'draft', ?, ?, ?
            )
            """,
            insert_values,
        )
        created.append(
            BudgetLineSlice(
                id=cur.lastrowid,
                property_id=property_id,
                assessment_setup_id=assessment_setup_id,
                source_line_normalized_label=source_line_normalized_label,
                source_line_key=source_line_key,
                source_line_account_code=source_line_account_code,
                source_annual_amount=source_annual_amount,
                slice_annual_amount=amount,
                slice_percent=pct,
                pool_key=str(item["pool_key"]),
                semantic_category=str(item.get("semantic_category") or ""),
                status="draft",
                evidence_text=str(item.get("evidence_text") or ""),
                reason=str(item.get("reason") or ""),
                created_by=actor,
            )
        )
    if commit:
        connection.commit()
    return created


def approve_slices_for_line(
    connection: sqlite3.Connection,
    *,
    assessment_setup_id: int,
    source_line_normalized_label: str,
    source_line_key: Optional[str] = None,
    source_line_account_code: Optional[str] = None,
    actor: str,
    source_annual_amount: Optional[Decimal] = None,
) -> list[BudgetLineSlice]:
    """Approve a previously validated draft split for final assessment math."""
    drafts = list_slices(
        connection,
        assessment_setup_id=assessment_setup_id,
        source_line_normalized_label=source_line_normalized_label,
        source_line_key=source_line_key,
        source_line_account_code=source_line_account_code,
        statuses=("draft",),
    )
    saved_source_amount = drafts[0].source_annual_amount if drafts else None
    expected_source_amount = (
        source_annual_amount
        if source_annual_amount is not None
        else saved_source_amount
    )
    if (
        expected_source_amount is None
        or len(drafts) < 2
        or any(item.source_annual_amount != expected_source_amount for item in drafts)
        or any(
            not item.semantic_category.strip() or not item.pool_key.strip()
            for item in drafts
        )
        or validate_slice_sum(
            expected_source_amount,
            [item.slice_annual_amount for item in drafts],
        ) != Decimal("0")
        or (
            source_annual_amount is not None
            and saved_source_amount != source_annual_amount
        )
    ):
        raise ValueError(
            "Saved split no longer matches the active budget amount. "
            "Refresh the mapping review and save it again."
        )
    slice_columns = _slice_table_columns(connection)
    source_key_filter = (
        "AND COALESCE(source_line_key, '') = COALESCE(?, '')"
        if "source_line_key" in slice_columns
        else ""
    )
    approve_params: list[Any] = [
        actor,
        assessment_setup_id,
        source_line_normalized_label,
    ]
    if "source_line_key" in slice_columns:
        approve_params.append(source_line_key)
    approve_params.append(source_line_account_code)
    cur = connection.execute(
        f"""
        UPDATE budget_line_allocation_slices
           SET status = 'approved',
               approved_by = ?,
               approved_at = datetime('now')
         WHERE assessment_setup_id = ?
           AND source_line_normalized_label = ?
           {source_key_filter}
           AND COALESCE(source_line_account_code, '') = COALESCE(?, '')
           AND status = 'draft'
        """,
        approve_params,
    )
    if cur.rowcount == 0:
        raise ValueError("No draft split found for this budget line.")
    connection.commit()
    return list_slices(
        connection,
        assessment_setup_id=assessment_setup_id,
        source_line_normalized_label=source_line_normalized_label,
        source_line_key=source_line_key,
        source_line_account_code=source_line_account_code,
    )


def delete_slices_for_line(
    connection: sqlite3.Connection,
    *,
    assessment_setup_id: int,
    source_line_normalized_label: str,
    source_line_key: Optional[str] = None,
    source_line_account_code: Optional[str] = None,
) -> None:
    slice_columns = _slice_table_columns(connection)
    source_key_filter = (
        "AND COALESCE(source_line_key, '') = COALESCE(?, '')"
        if "source_line_key" in slice_columns
        else ""
    )
    delete_params: list[Any] = [
        assessment_setup_id,
        source_line_normalized_label,
    ]
    if "source_line_key" in slice_columns:
        delete_params.append(source_line_key)
    delete_params.append(source_line_account_code)
    connection.execute(
        f"""
        UPDATE budget_line_allocation_slices
           SET status = 'superseded'
         WHERE assessment_setup_id = ?
           AND source_line_normalized_label = ?
           {source_key_filter}
           AND COALESCE(source_line_account_code, '') = COALESCE(?, '')
           AND status IN ('draft', 'approved')
        """,
        delete_params,
    )
    connection.commit()


def upsert_category_decision(
    connection: sqlite3.Connection,
    decision: CategoryCoverageDecision,
) -> CategoryCoverageDecision:
    connection.execute(
        """
        INSERT INTO allocation_category_decisions (
            property_id, assessment_setup_id, pool_key, category, decision,
            mapped_amount, evidence_text, reason, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(assessment_setup_id, pool_key, category) DO UPDATE SET
            decision = excluded.decision,
            mapped_amount = excluded.mapped_amount,
            evidence_text = excluded.evidence_text,
            reason = excluded.reason,
            created_by = excluded.created_by
        """,
        (
            decision.property_id,
            decision.assessment_setup_id,
            decision.pool_key,
            decision.category,
            decision.decision,
            str(decision.mapped_amount) if decision.mapped_amount is not None else None,
            decision.evidence_text,
            decision.reason,
            decision.created_by,
        ),
    )
    connection.commit()
    return decision


def list_category_decisions(
    connection: sqlite3.Connection,
    *,
    assessment_setup_id: int,
) -> list[CategoryCoverageDecision]:
    cur = connection.execute(
        "SELECT * FROM allocation_category_decisions WHERE assessment_setup_id = ?",
        (assessment_setup_id,),
    )
    out: list[CategoryCoverageDecision] = []
    for row in cur.fetchall():
        data = _row_dict(row, cur.description)
        out.append(
            CategoryCoverageDecision(
                id=data.get("id"),
                property_id=int(data["property_id"]),
                assessment_setup_id=int(data["assessment_setup_id"]),
                pool_key=str(data["pool_key"]),
                category=str(data["category"]),
                decision=data["decision"],
                mapped_amount=_decimal_or_none(data.get("mapped_amount")),
                evidence_text=data.get("evidence_text") or "",
                reason=data.get("reason") or "",
                created_by=data.get("created_by") or "",
                created_at=data.get("created_at"),
            )
        )
    return out


def seed_resolution_from_promotion(
    connection: sqlite3.Connection,
    *,
    property_id: int,
    assessment_setup_id: int,
    pool_key: str,
    declared_method: str,
    resolved_method: Optional[str],
    unresolved: bool,
    denominator_label: str,
    included_categories: list[str],
    excluded_categories: list[str],
    source_pages: list[int],
    source_text: str = "",
    denominator_value: Optional[Decimal] = None,
    denominator_source: Optional[str] = None,
) -> AllocationResolutionRecord:
    schedule = infer_referenced_schedule(declared_method, denominator_label)
    status = "unresolved" if unresolved else "approved"
    return create_resolution(
        connection,
        AllocationResolutionRecord(
            property_id=property_id,
            assessment_setup_id=assessment_setup_id,
            pool_key=pool_key,
            version_int=1,
            status=status,
            declared_method=declared_method if declared_method in {
                "equal", "square_footage", "ownership_percentage",
                "specified_value", "custom_factor", "external_schedule", "unknown",
            } else "unknown",
            declared_denominator_label=denominator_label,
            referenced_schedule=schedule,
            included_categories=included_categories,
            excluded_categories=excluded_categories,
            evidence=ResolutionEvidence(
                source_pages=source_pages,
                source_text=source_text,
            ),
            resolved_method=None if unresolved else resolved_method,  # type: ignore[arg-type]
            factor_snapshot=FactorSnapshot(
                method=None if unresolved else resolved_method,  # type: ignore[arg-type]
                denominator_value=None if unresolved else denominator_value,
                denominator_source=None if unresolved else denominator_source,
            ),
            source="promotion",
            created_by="promotion",
            approved_by=None if unresolved else "promotion",
            approved_at=None if unresolved else "now",
        ),
        commit=False,
    )


def freeze_resolution_snapshot(
    connection: sqlite3.Connection,
    *,
    assessment_setup_id: int,
) -> dict[str, Any]:
    """JSON-ready freeze of approved resolutions, slices, and category decisions."""
    resolutions = [
        rec.model_dump(mode="json")
        for rec in list_current_resolutions(
            connection,
            assessment_setup_id=assessment_setup_id,
        )
        if rec.status == "approved"
    ]
    slices = [
        sl.model_dump(mode="json")
        for sl in list_slices(
            connection,
            assessment_setup_id=assessment_setup_id,
            statuses=("approved",),
        )
    ]
    decisions = [
        d.model_dump(mode="json")
        for d in list_category_decisions(connection, assessment_setup_id=assessment_setup_id)
    ]
    return {
        "assessment_setup_id": assessment_setup_id,
        "resolutions": resolutions,
        "slices": slices,
        "category_decisions": decisions,
    }
