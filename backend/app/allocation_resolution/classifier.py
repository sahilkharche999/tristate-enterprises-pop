"""Classify existing setups for brownfield allocation-resolution migration."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import Any, Literal, Optional

from .schemas import AllocationResolutionRecord, FactorSnapshot, ResolutionEvidence
from .service import (
    _next_version,
    create_resolution,
    current_resolution,
    infer_referenced_schedule,
    list_current_resolutions,
)


Classification = Literal["approved_backfill", "needs_review", "leave_finalized"]

_EXPLICIT = frozenset({"equal", "square_footage", "ownership_percentage", "specified_value"})
_AMBIGUOUS = frozenset({"custom_factor", "external_schedule", "unknown", "category"})


def _load_json(raw: Any) -> Any:
    if not raw:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def declared_method_from_extraction(parsed_json: Any, pool_key: str) -> Optional[str]:
    payload = _load_json(parsed_json)
    if not isinstance(payload, dict):
        return None
    pools = payload.get("allocation_pools") or []
    if not isinstance(pools, list):
        # CC&R live extract sometimes nests under summary.pools
        summary = payload.get("summary") or {}
        pools = summary.get("pools") or []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        if str(pool.get("pool_key") or "") == pool_key:
            return str(pool.get("allocation_method") or "") or None
    return None


def classify_pool(
    *,
    declared_method: Optional[str],
    promoted_method: str,
    has_resolution_evidence: bool,
    finalized_snapshot: bool,
) -> Classification:
    if finalized_snapshot:
        return "leave_finalized"
    declared = (declared_method or "").strip()
    promoted = (promoted_method or "").strip()
    if declared in _EXPLICIT and declared == promoted:
        return "approved_backfill"
    if declared in _AMBIGUOUS and promoted in _EXPLICIT and not has_resolution_evidence:
        return "needs_review"
    if declared in _AMBIGUOUS and promoted in _EXPLICIT and has_resolution_evidence:
        return "approved_backfill"
    if not declared:
        return "needs_review"
    if declared in _EXPLICIT and declared != promoted and not has_resolution_evidence:
        return "needs_review"
    if declared in _EXPLICIT:
        return "approved_backfill"
    return "needs_review"


def collect_migration_report(
    connection: sqlite3.Connection,
    *,
    property_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Dry-run classifier over active (non-superseded) setups."""
    sql = """
        SELECT s.id, s.property_id, s.status, s.source_dre_document_id,
               p.pool_key, p.pool_name, p.allocation_method, p.denominator_label,
               p.declared_allocation_method
          FROM assessment_setups s
          JOIN allocation_pools p ON p.assessment_setup_id = s.id
         WHERE s.status IN ('draft', 'approved')
    """
    params: list[Any] = []
    if property_id is not None:
        sql += " AND s.property_id = ?"
        params.append(property_id)
    sql += " ORDER BY s.property_id, s.id, p.display_order, p.pool_key"
    cur = connection.execute(sql, params)
    rows = cur.fetchall()
    has_resolution_table = connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'allocation_resolutions'"
    ).fetchone() is not None
    report: list[dict[str, Any]] = []
    for row in rows:
        data = {col[0]: row[idx] for idx, col in enumerate(cur.description)}
        setup_id = int(data["id"])
        pid = int(data["property_id"])
        pool_key = str(data["pool_key"])
        promoted = str(data["allocation_method"] or "")
        declared = data.get("declared_allocation_method") or None
        if not declared:
            declared = _declared_from_linked_extraction(
                connection, source_document_id=data.get("source_dre_document_id"),
                pool_key=pool_key,
            )
        finalized = _setup_pinned_by_finalized_package(connection, setup_id)
        existing = (
            current_resolution(
                connection,
                assessment_setup_id=setup_id,
                pool_key=pool_key,
            )
            if has_resolution_table
            else None
        )
        has_resolution_evidence = bool(
            existing
            and existing.source == "operator"
            and existing.status == "approved"
            and (
                existing.evidence.source_pages
                or existing.evidence.source_text
                or existing.evidence.reason
                or existing.evidence.document_id is not None
                or existing.evidence.prior_package_id is not None
            )
        )
        classification = classify_pool(
            declared_method=declared,
            promoted_method=promoted,
            has_resolution_evidence=has_resolution_evidence,
            finalized_snapshot=finalized,
        )
        report.append({
            "property_id": pid,
            "assessment_setup_id": setup_id,
            "pool_key": pool_key,
            "pool_name": data.get("pool_name"),
            "declared_method": declared,
            "promoted_method": promoted,
            "classification": classification,
            "pinned_by_finalized_package": finalized,
        })
    return report


def _declared_from_linked_extraction(
    connection: sqlite3.Connection,
    *,
    source_document_id: Any,
    pool_key: str,
) -> Optional[str]:
    if source_document_id in (None, ""):
        return None
    row = connection.execute(
        """
        SELECT parsed_json FROM dre_extraction_runs
         WHERE dre_document_id = ?
         ORDER BY id DESC LIMIT 1
        """,
        (int(source_document_id),),
    ).fetchone()
    if not row:
        return None
    return declared_method_from_extraction(row[0], pool_key)


def _setup_pinned_by_finalized_package(connection: sqlite3.Connection, setup_id: int) -> bool:
    try:
        row = connection.execute(
            "SELECT 1 FROM annual_packages "
            "WHERE assessment_setup_id = ? AND status = 'finalized' LIMIT 1",
            (setup_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _factor_snapshot_for_setup(
    connection: sqlite3.Connection,
    *,
    setup_id: int,
    pool_key: str,
    method: str,
) -> Optional[FactorSnapshot]:
    """Seed only factors that are complete enough to survive migration."""
    setup = connection.execute(
        "SELECT setup_type FROM assessment_setups WHERE id = ?",
        (setup_id,),
    ).fetchone()
    if setup is None:
        return None
    if method == "specified_value":
        rows = connection.execute(
            """
            SELECT u.unit_number, a.specified_monthly_amount
              FROM assessment_unit_pool_allocations a
              JOIN assessment_units u ON u.id = a.assessment_unit_id
             WHERE a.assessment_setup_id = ? AND a.pool_key = ?
            """,
            (setup_id, pool_key),
        ).fetchall()
        factors = {str(row[0]): Decimal(str(row[1])) for row in rows}
        expected = connection.execute(
            """
            SELECT COUNT(*)
              FROM assessment_units
             WHERE assessment_setup_id = ?
            """,
            (setup_id,),
        ).fetchone()[0]
        return (
            FactorSnapshot(method=method, recipients=factors)
            if factors and len(factors) == int(expected or 0)
            else None
        )

    if str(setup[0]) == "grouped":
        rows = connection.execute(
            """
            SELECT group_name, unit_count, average_square_feet, ownership_percent
              FROM assessment_groups
             WHERE assessment_setup_id = ?
            """,
            (setup_id,),
        ).fetchall()
        if not rows:
            return None
        if method == "square_footage":
            factors = {
                str(row[0]): Decimal(str(row[2])) * Decimal(str(row[1] or 1))
                for row in rows
                if row[2] not in (None, "")
            }
        else:
            raw = {
                str(row[0]): Decimal(str(row[3]))
                for row in rows
                if row[3] not in (None, "")
            }
            weighted = {
                str(row[0]): Decimal(str(row[3])) * Decimal(str(row[1] or 1))
                for row in rows
                if row[3] not in (None, "")
            }
            raw_sum = sum(raw.values(), start=Decimal("0"))
            weighted_sum = sum(weighted.values(), start=Decimal("0"))
            factors = weighted if abs(weighted_sum - 1) < abs(raw_sum - 1) else raw
    else:
        rows = connection.execute(
            """
            SELECT unit_number, square_feet, ownership_percent
              FROM assessment_units
             WHERE assessment_setup_id = ?
            """,
            (setup_id,),
        ).fetchall()
        if not rows:
            return None
        index = 1 if method == "square_footage" else 2
        factors = {
            str(row[0]): Decimal(str(row[index]))
            for row in rows
            if row[index] not in (None, "")
        }
    if len(factors) != len(rows):
        return None
    denominator = (
        sum(factors.values(), start=Decimal("0"))
        if method == "square_footage"
        else None
    )
    return FactorSnapshot(
        method=method,
        denominator_value=denominator,
        denominator_source="migration" if denominator is not None else None,
        recipients=factors,
    )


def apply_migration(
    connection: sqlite3.Connection,
    *,
    dry_run: bool = True,
    property_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Backfill explicit matches; mark ambiguous active setups for review.

    Historical finalized package snapshots are not rewritten.
    """
    report = collect_migration_report(connection, property_id=property_id)
    if dry_run:
        return report
    setup_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(assessment_setups)"
        ).fetchall()
    }
    for item in report:
        setup_id = int(item["assessment_setup_id"])
        pool_key = str(item["pool_key"])
        existing = list_current_resolutions(connection, assessment_setup_id=setup_id)
        if any(r.pool_key == pool_key for r in existing):
            continue
        declared = item.get("declared_method") or "unknown"
        promoted = item["promoted_method"]
        classification = item["classification"]
        if classification == "leave_finalized":
            continue
        denom_row = connection.execute(
            "SELECT denominator_label FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = ?",
            (setup_id, pool_key),
        ).fetchone()
        denom = str(denom_row[0] or "") if denom_row else ""
        factor_snapshot = (
            _factor_snapshot_for_setup(
                connection,
                setup_id=setup_id,
                pool_key=pool_key,
                method=promoted,
            )
            if promoted in {"square_footage", "ownership_percentage", "specified_value"}
            else FactorSnapshot(method=promoted)
        )
        unresolved = classification == "needs_review" or (
            promoted in {"square_footage", "ownership_percentage", "specified_value"}
            and factor_snapshot is None
        )
        if unresolved:
            factor_snapshot = FactorSnapshot(method=None)
        create_resolution(
            connection,
            AllocationResolutionRecord(
                property_id=int(item["property_id"]),
                assessment_setup_id=setup_id,
                pool_key=pool_key,
                version_int=_next_version(connection, setup_id, pool_key),
                status="unresolved" if unresolved else "approved",
                declared_method=declared if declared in {
                    "equal", "square_footage", "ownership_percentage",
                    "specified_value", "custom_factor", "external_schedule", "unknown",
                } else "unknown",
                declared_denominator_label=denom,
                referenced_schedule=infer_referenced_schedule(str(declared), denom),
                evidence=ResolutionEvidence(
                    reason="brownfield migration classifier",
                ),
                resolved_method=None if unresolved else promoted,
                factor_snapshot=factor_snapshot,
                source="migration",
                created_by="migration",
                approved_by=None if unresolved else "migration",
                approved_at=None if unresolved else "now",
            ),
        )
    if "allocation_readiness_status" in setup_columns:
        for setup_id in {int(item["assessment_setup_id"]) for item in report}:
            needs_review = connection.execute(
                """
                SELECT 1
                  FROM allocation_resolutions
                 WHERE assessment_setup_id = ?
                   AND status IN ('unresolved', 'draft')
                 LIMIT 1
                """,
                (setup_id,),
            ).fetchone() is not None
            connection.execute(
                """
                UPDATE assessment_setups
                   SET allocation_readiness_status = ?
                 WHERE id = ?
                """,
                ("needs_review" if needs_review else "ok", setup_id),
            )
    connection.commit()
    return report
