"""Snapshot ``DREExtractionRun.parsed_json`` into the live setup tables (Task 105).

On approve, the approval service writes an ``assessment_setups`` row. This
module adds the child rows: AllocationPool, AssessmentGroup, AssessmentUnit,
and AssessmentUnitPoolAllocation. Without these, the engine has no recipients
or pools to allocate against — the AssessmentSetup row alone is just a
header.

The mapping is intentionally lossy on the AI-prompt vocabulary:

* ``setup_type`` mapping happens in ``adapter.map_setup_type`` (already
  applied at approval-time by the operator picking a value).
* ``allocation_method`` mapping uses ``adapter.map_allocation_method`` —
  e.g. ``parking_space`` collapses to ``equal`` over ``parking_users``
  scope. We honor ``forced_scope`` even when the prompt also emitted
  ``recipient_scope``, because the prompt's free-text scope can drift.
* Groups vs units: we populate AssessmentGroup rows when the extraction
  produced any group rows AND the chosen setup_type is ``grouped``. We
  populate AssessmentUnit rows when units are present AND setup_type is
  ``per_unit``. For ``fixed`` setups we populate neither (the engine
  fans out across ``properties.units`` at recipient resolution time).
* ``AssessmentUnitPoolAllocation`` is populated from per-pool
  ``annual_amount``/``monthly_amount`` divided across the matching unit
  rows when a ``specified_value`` pool exists — otherwise the engine's
  specified_value allocator can't find a per-unit value at runtime.

The snapshot is **best-effort**: any single bad row logs a warning and is
skipped rather than aborting the whole promotion. The operator's edits in
the Review Workbench (Phase 4, deferred) will eventually correct or
override before promotion, but we honor whatever shape the extraction
produced today so the engine has something to compute against.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from decimal import Decimal
from typing import Any, Iterable, Optional

from .adapter import map_allocation_method
from .schemas import (
    AllocationPoolBlock,
    DRESetupExtraction,
    GroupRow,
    UnitRow,
)


logger = logging.getLogger(__name__)


_VALID_RECIPIENT_SCOPES = {
    "all_units", "residential_only", "commercial_only",
    "parking_users", "custom_unit_list",
}
_VALID_DENOMINATOR_SOURCES = {"dre_value", "calculated", "manual"}


def _coerce_recipient_scope(raw: str) -> str:
    """Normalize prompt-emitted ``recipient_scope`` to internal enum.

    Defaults to ``all_units`` for empty/unrecognized values — the engine
    fans out across every unit then, which is the safest default.
    """
    candidate = (raw or "").strip().lower().replace(" ", "_")
    if candidate in _VALID_RECIPIENT_SCOPES:
        return candidate
    return "all_units"


def _coerce_denominator_source(raw: str) -> str:
    candidate = (raw or "").strip().lower()
    # Prompt vocab: 'dre_shown' | 'calculated' | 'unknown'
    if candidate == "dre_shown":
        return "dre_value"
    if candidate == "calculated":
        return "calculated"
    if candidate in _VALID_DENOMINATOR_SOURCES:
        return candidate
    return "dre_value"


def _insert_pool(
    *,
    setup_id: int,
    pool: AllocationPoolBlock,
    display_order: int,
    connection: sqlite3.Connection,
) -> Optional[int]:
    """Insert one allocation_pools row. Returns pool_id, or None on bad data."""
    mapping = map_allocation_method(pool.allocation_method)
    if mapping.internal_method is None:
        logger.warning(
            "promotion: skipping pool %r — allocation method %r could not be mapped",
            pool.pool_key, pool.allocation_method,
        )
        return None

    scope = mapping.forced_scope or _coerce_recipient_scope(pool.recipient_scope)
    denom_source = (
        mapping.forced_denominator_source
        or _coerce_denominator_source(pool.denominator_source)
    )

    cur = connection.execute(
        """
        INSERT INTO allocation_pools (
            assessment_setup_id, pool_key, pool_name,
            allocation_method, recipient_scope,
            denominator_source, denominator_value,
            variable_flag, display_order, include_in_pdf,
            budget_line_derivation,
            residual_after_pool_keys_json,
            residual_exclusions_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 1, ?, ?, ?)
        """,
        (
            setup_id, pool.pool_key, pool.pool_name or pool.pool_key,
            mapping.internal_method, scope,
            denom_source,
            str(pool.denominator_value) if pool.denominator_value is not None else None,
            display_order,
            pool.budget_line_derivation,
            json.dumps(pool.residual_after_pool_keys),
            json.dumps(pool.residual_exclusions),
        ),
    )
    return cur.lastrowid


def _insert_group(
    *,
    setup_id: int,
    group: GroupRow,
    display_order: int,
    connection: sqlite3.Connection,
) -> Optional[int]:
    """Insert one assessment_groups row. Returns row id, or None on bad data."""
    if group.unit_count is None or group.unit_count <= 0:
        logger.warning(
            "promotion: skipping group %r — unit_count missing/invalid",
            group.group_id or group.label,
        )
        return None
    cur = connection.execute(
        """
        INSERT INTO assessment_groups (
            assessment_setup_id, group_name, unit_count,
            average_square_feet, ownership_percent, dre_factor, display_order
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            setup_id,
            group.label or group.group_id or f"group-{display_order}",
            int(group.unit_count),
            str(group.average_square_feet) if group.average_square_feet is not None else None,
            str(group.ownership_percent) if group.ownership_percent is not None else None,
            str(group.factor) if group.factor is not None else None,
            display_order,
        ),
    )
    return cur.lastrowid


_CATEGORY_MAP = {
    "residential": "residential",
    "commercial": "commercial",
    "mixed": "mixed",
    "mixed_use": "mixed",
    "": None,
}


def _coerce_category(raw_category: str, residential_commercial_flag: str) -> Optional[str]:
    """Map prompt-emitted category to schema enum (residential|commercial|mixed)."""
    candidate = (raw_category or "").strip().lower().replace(" ", "_")
    if candidate in _CATEGORY_MAP:
        mapped = _CATEGORY_MAP[candidate]
        if mapped:
            return mapped
    flag = (residential_commercial_flag or "").strip().lower()
    if flag.startswith("res"):
        return "residential"
    if flag.startswith("com"):
        return "commercial"
    return None


def _parking_count(raw: str) -> int:
    """Parse ``parking_flag`` text → integer space count. Defaults to 0."""
    if not raw:
        return 0
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else 0


def _insert_unit(
    *,
    setup_id: int,
    unit: UnitRow,
    connection: sqlite3.Connection,
) -> Optional[int]:
    if not unit.unit_number:
        logger.warning("promotion: skipping unit — unit_number empty")
        return None
    cur = connection.execute(
        """
        INSERT INTO assessment_units (
            assessment_setup_id, unit_number, square_feet,
            ownership_percent, category, parking_spaces,
            specified_monthly_amount, source
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'dre')
        """,
        (
            setup_id, unit.unit_number,
            str(unit.square_feet) if unit.square_feet is not None else None,
            str(unit.ownership_percent) if unit.ownership_percent is not None else None,
            _coerce_category(unit.category, unit.residential_commercial_flag),
            _parking_count(unit.parking_flag),
        ),
    )
    return cur.lastrowid


def _insert_specified_value_allocations(
    *,
    setup_id: int,
    pool_key: str,
    pool_id: int,
    unit_id_by_number: dict[str, int],
    annual_amount: Optional[Decimal],
    unit_count: int,
    connection: sqlite3.Connection,
) -> None:
    """For each unit, insert one assessment_unit_pool_allocations row.

    The extraction prompt only gives us a pool-level ``annual_amount``;
    the per-unit specified value isn't broken out (that's the operator's
    job in the Review Workbench). As a placeholder we distribute the
    pool's annual evenly across units → monthly = annual / 12 / units.
    The operator will overwrite these in Review before promotion is
    "done" for per-unit setups.
    """
    if not unit_id_by_number or annual_amount is None or unit_count <= 0:
        return
    monthly = (annual_amount / Decimal(12) / Decimal(unit_count)).quantize(
        Decimal("0.01")
    )
    for unit_number, unit_id in unit_id_by_number.items():
        connection.execute(
            """
            INSERT INTO assessment_unit_pool_allocations (
                assessment_unit_id, assessment_setup_id,
                pool_key, pool_id, specified_monthly_amount, source
            ) VALUES (?, ?, ?, ?, ?, 'dre')
            """,
            (unit_id, setup_id, pool_key, pool_id, str(monthly)),
        )


def parse_extraction_payload(
    parsed_json_text: Optional[str],
) -> Optional[DRESetupExtraction]:
    """Parse a stored ``dre_extraction_runs.parsed_json`` blob.

    Returns None when the blob is missing or fails validation — the
    caller should skip child-row population and let the operator fix
    the extraction in the Review Workbench.
    """
    if not parsed_json_text:
        return None
    try:
        payload = json.loads(parsed_json_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("promotion: parsed_json is not valid JSON; skipping snapshot")
        return None
    try:
        return DRESetupExtraction.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError + edge cases
        logger.warning("promotion: parsed_json failed schema validation: %s", exc)
        return None


def populate_setup_children(
    *,
    setup_id: int,
    setup_type: str,
    extraction: DRESetupExtraction,
    connection: sqlite3.Connection,
) -> dict[str, int]:
    """Insert AllocationPool / Group / Unit / UnitPoolAllocation rows.

    Returns a count summary for the audit trail.
    """
    counts = {
        "pools": 0, "groups": 0, "units": 0, "unit_pool_allocations": 0,
    }

    pool_id_by_key: dict[str, int] = {}
    for idx, pool in enumerate(extraction.allocation_pools):
        pool_id = _insert_pool(
            setup_id=setup_id, pool=pool, display_order=idx, connection=connection,
        )
        if pool_id is not None:
            pool_id_by_key[pool.pool_key] = pool_id
            counts["pools"] += 1

    if setup_type == "grouped":
        for idx, group in enumerate(extraction.unit_structure.groups):
            if _insert_group(
                setup_id=setup_id, group=group,
                display_order=idx, connection=connection,
            ) is not None:
                counts["groups"] += 1

    unit_id_by_number: dict[str, int] = {}
    if setup_type == "per_unit":
        for unit in extraction.unit_structure.units:
            unit_id = _insert_unit(
                setup_id=setup_id, unit=unit, connection=connection,
            )
            if unit_id is not None:
                unit_id_by_number[unit.unit_number] = unit_id
                counts["units"] += 1

        # For each specified_value pool, distribute its annual evenly
        # across the inserted unit rows. The operator will overwrite
        # the per-unit specifics in the Review Workbench.
        for pool in extraction.allocation_pools:
            if pool.allocation_method != "specified_value":
                continue
            pool_id = pool_id_by_key.get(pool.pool_key)
            if pool_id is None:
                continue
            before = sum(1 for _ in unit_id_by_number)
            _insert_specified_value_allocations(
                setup_id=setup_id, pool_key=pool.pool_key,
                pool_id=pool_id, unit_id_by_number=unit_id_by_number,
                annual_amount=pool.annual_amount,
                unit_count=len(unit_id_by_number),
                connection=connection,
            )
            counts["unit_pool_allocations"] += before

    return counts


def promote_extraction_to_setup(
    *,
    setup_id: int,
    setup_type: str,
    parsed_json_text: Optional[str],
    connection: sqlite3.Connection,
) -> dict[str, int]:
    """Full snapshot pipeline: parse parsed_json + populate child rows.

    Called inside the approval transaction so the AssessmentSetup row
    and its children are committed atomically.
    """
    extraction = parse_extraction_payload(parsed_json_text)
    if extraction is None:
        return {"pools": 0, "groups": 0, "units": 0, "unit_pool_allocations": 0}
    return populate_setup_children(
        setup_id=setup_id,
        setup_type=setup_type,
        extraction=extraction,
        connection=connection,
    )


__all__ = [
    "parse_extraction_payload",
    "populate_setup_children",
    "promote_extraction_to_setup",
]
