"""Classify existing setups for brownfield allocation-resolution migration."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal, Optional

from .schemas import AllocationResolutionRecord, FactorSnapshot, ResolutionEvidence
from .service import create_resolution, infer_referenced_schedule, list_current_resolutions


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
        classification = classify_pool(
            declared_method=declared,
            promoted_method=promoted,
            has_resolution_evidence=False,
            finalized_snapshot=False,
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
    for item in report:
        setup_id = int(item["assessment_setup_id"])
        pool_key = str(item["pool_key"])
        existing = list_current_resolutions(connection, assessment_setup_id=setup_id)
        if any(r.pool_key == pool_key for r in existing):
            continue
        declared = item.get("declared_method") or "unknown"
        promoted = item["promoted_method"]
        classification = item["classification"]
        denom_row = connection.execute(
            "SELECT denominator_label FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = ?",
            (setup_id, pool_key),
        ).fetchone()
        denom = str(denom_row[0] or "") if denom_row else ""
        unresolved = classification == "needs_review"
        create_resolution(
            connection,
            AllocationResolutionRecord(
                property_id=int(item["property_id"]),
                assessment_setup_id=setup_id,
                pool_key=pool_key,
                version_int=1,
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
                factor_snapshot=FactorSnapshot(method=None if unresolved else promoted),
                source="migration",
                created_by="migration",
                approved_by=None if unresolved else "migration",
                approved_at=None if unresolved else "now",
            ),
        )
        if unresolved:
            try:
                connection.execute(
                    """
                    UPDATE assessment_setups
                       SET allocation_readiness_status = 'needs_review'
                     WHERE id = ?
                    """,
                    (setup_id,),
                )
            except sqlite3.OperationalError:
                pass
    connection.commit()
    return report
