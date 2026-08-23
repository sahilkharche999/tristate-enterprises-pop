"""DRE Review Workbench backend services (Phase 4.6 + 4.7).

Two append-only audit writers:

* :func:`record_review_edit` — every change made in the Review Workbench
  (denominator edit, variable-line flag toggle, setup_type swap, etc.)
  writes a ``dre_review_edits`` row capturing field_path, old_value,
  new_value, reason, edited_by. Internal audit only — not rendered into
  the homeowner-visible PDF.

* :func:`record_field_source` — when the extraction pipeline parses a
  Gemini response, every high-risk field plus every entity-level
  citation produces an ``extracted_field_sources`` row pointing at the
  source page (and optionally a bounding box). The Review Workbench
  reads these back to overlay source images beside extracted values.

Both functions are connection-injected so they compose cleanly inside
the existing extraction-pipeline and approval transactions.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel

from app.dre_extraction.promotion import (
    STRUCTURAL_OPERATION_FIELD_PATH,
    InvalidStructuralOperation,
    PoolStructuralOperation,
    StaleStructuralOperation,
    apply_review_edits_to_extraction,
    parse_extraction_payload,
    parse_pool_structural_operation,
)


HIGH_RISK_FIELDS = frozenset(
    [
        "denominator_value",
        "allocation_method",
        "unit_count",
        "variable_flag",
        "ownership_percent",
        "annual_amount",
        "monthly_amount",
        "specified_monthly_amount",
    ]
)


class DREReviewEditResponse(BaseModel):
    """JSON shape returned by record_review_edit."""

    edit_id: int
    dre_extraction_run_id: int
    field_path: str
    old_value: Optional[str]
    new_value: Optional[str]
    reason: Optional[str]
    edited_by: Optional[str]
    edited_at: str


class ExtractedFieldSourceResponse(BaseModel):
    """JSON shape returned by record_field_source."""

    source_id: int
    dre_extraction_run_id: int
    entity_type: str
    entity_id: Optional[str]
    field_name: str
    page_number: int
    bounding_box_json: Optional[str]
    confidence: Optional[float]


class StructuralOperationReasonRequired(ValueError):
    """A structural correction must carry an operator audit reason."""


def _stringify_value(value: Any) -> Optional[str]:
    """Coerce arbitrary edit values to text for the audit row.

    Decimals/booleans/lists/dicts all get JSON-encoded so a later diff
    against ``new_value`` is just string compare.
    """
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def record_review_edit(
    *,
    dre_extraction_run_id: int,
    field_path: str,
    old_value: Any,
    new_value: Any,
    reason: Optional[str] = None,
    edited_by: Optional[str] = None,
    connection: sqlite3.Connection,
) -> DREReviewEditResponse:
    """Append a ``dre_review_edits`` row. Caller commits.

    ``field_path`` is a dotted/bracketed path into the extracted JSON
    (e.g. ``allocation_pools[0].denominator_value`` or
    ``assessment_setup.setup_type``). The Review Workbench keeps these
    paths stable so the edit history is replayable.
    """
    cur = connection.execute(
        """
        INSERT INTO dre_review_edits (
            dre_extraction_run_id, field_path, old_value, new_value,
            reason, edited_by
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            dre_extraction_run_id,
            field_path,
            _stringify_value(old_value),
            _stringify_value(new_value),
            reason,
            edited_by,
        ),
    )
    edit_id = cur.lastrowid
    if edit_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for dre_review_edits")
    connection.commit()
    row = connection.execute(
        "SELECT id, dre_extraction_run_id, field_path, old_value, new_value, "
        "reason, edited_by, edited_at FROM dre_review_edits WHERE id = ?",
        (edit_id,),
    ).fetchone()
    return DREReviewEditResponse(
        edit_id=row[0],
        dre_extraction_run_id=row[1],
        field_path=row[2],
        old_value=row[3],
        new_value=row[4],
        reason=row[5],
        edited_by=row[6],
        edited_at=row[7],
    )


def record_structural_operation(
    *,
    dre_extraction_run_id: int,
    operation: PoolStructuralOperation | dict[str, Any],
    reason: Optional[str] = None,
    edited_by: Optional[str] = None,
    connection: sqlite3.Connection,
) -> DREReviewEditResponse:
    """Validate and atomically append a pool operation.

    A caller-owned transaction is preserved. Otherwise ``BEGIN IMMEDIATE``
    serializes base-version validation, full-log replay, and insertion.
    """
    if reason is None or not reason.strip():
        raise StructuralOperationReasonRequired(
            "Explain why this category correction is needed."
        )
    owns_transaction = not connection.in_transaction
    savepoint = "dre_structural_operation"
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute(f"SAVEPOINT {savepoint}")
    try:
        typed = parse_pool_structural_operation(operation)
        run_row = connection.execute(
            "SELECT parsed_json FROM dre_extraction_runs WHERE id = ?",
            (dre_extraction_run_id,),
        ).fetchone()
        extraction = (
            parse_extraction_payload(run_row[0])
            if run_row is not None
            else None
        )
        if extraction is None:
            raise InvalidStructuralOperation(
                "The immutable extraction cannot be replayed.", []
            )

        edits = list_review_edits(
            dre_extraction_run_id=dre_extraction_run_id,
            connection=connection,
        )
        candidate_value = json.dumps(
            typed.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        candidate = SimpleNamespace(
            field_path=STRUCTURAL_OPERATION_FIELD_PATH,
            new_value=candidate_value,
        )
        apply_review_edits_to_extraction(extraction, [*edits, candidate])

        current_version = sum(
            edit.field_path == STRUCTURAL_OPERATION_FIELD_PATH for edit in edits
        )
        cur = connection.execute(
            """
            INSERT INTO dre_review_edits (
                dre_extraction_run_id, field_path, old_value, new_value,
                reason, edited_by
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                dre_extraction_run_id,
                STRUCTURAL_OPERATION_FIELD_PATH,
                _stringify_value({"version": current_version}),
                candidate_value,
                reason,
                edited_by,
            ),
        )
        edit_id = cur.lastrowid
        if edit_id is None:
            raise RuntimeError(
                "sqlite did not return a lastrowid for dre_review_edits"
            )
        row = connection.execute(
            "SELECT id, dre_extraction_run_id, field_path, old_value, new_value, "
            "reason, edited_by, edited_at FROM dre_review_edits WHERE id = ?",
            (edit_id,),
        ).fetchone()
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        return DREReviewEditResponse(
            edit_id=row[0],
            dre_extraction_run_id=row[1],
            field_path=row[2],
            old_value=row[3],
            new_value=row[4],
            reason=row[5],
            edited_by=row[6],
            edited_at=row[7],
        )
    except Exception:
        if owns_transaction:
            connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def list_review_edits(
    *,
    dre_extraction_run_id: int,
    connection: sqlite3.Connection,
) -> list[DREReviewEditResponse]:
    """Return the audit history for a run, oldest first."""
    rows = connection.execute(
        "SELECT id, dre_extraction_run_id, field_path, old_value, new_value, "
        "reason, edited_by, edited_at FROM dre_review_edits "
        "WHERE dre_extraction_run_id = ? "
        "ORDER BY edited_at, id",
        (dre_extraction_run_id,),
    ).fetchall()
    return [
        DREReviewEditResponse(
            edit_id=r[0],
            dre_extraction_run_id=r[1],
            field_path=r[2],
            old_value=r[3],
            new_value=r[4],
            reason=r[5],
            edited_by=r[6],
            edited_at=r[7],
        )
        for r in rows
    ]


def record_field_source(
    *,
    dre_extraction_run_id: int,
    entity_type: str,
    entity_id: Optional[str],
    field_name: str,
    page_number: int,
    bounding_box: Optional[dict] = None,
    confidence: Optional[float] = None,
    connection: sqlite3.Connection,
) -> ExtractedFieldSourceResponse:
    """Append an ``extracted_field_sources`` row. Caller commits.

    Called by the extraction pipeline AND by the approval flow when it
    promotes entities from parsed_json into AssessmentSetup children.
    ``page_number`` is required: a citation pointing nowhere is
    indistinguishable from no citation, so we reject zero/negative.
    """
    if page_number < 1:
        raise ValueError(
            f"page_number must be >= 1; got {page_number} for "
            f"{entity_type}.{field_name}"
        )
    bbox_json = (
        json.dumps(bounding_box, sort_keys=True, separators=(",", ":"))
        if bounding_box is not None
        else None
    )
    cur = connection.execute(
        """
        INSERT INTO extracted_field_sources (
            dre_extraction_run_id, entity_type, entity_id, field_name,
            page_number, bounding_box_json, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dre_extraction_run_id, entity_type, entity_id, field_name,
            page_number, bbox_json, confidence,
        ),
    )
    source_id = cur.lastrowid
    if source_id is None:
        raise RuntimeError("sqlite did not return a lastrowid for extracted_field_sources")
    connection.commit()
    return ExtractedFieldSourceResponse(
        source_id=source_id,
        dre_extraction_run_id=dre_extraction_run_id,
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        page_number=page_number,
        bounding_box_json=bbox_json,
        confidence=confidence,
    )


def record_entity_sources_from_extraction(
    *,
    dre_extraction_run_id: int,
    extraction_payload: dict,
    connection: sqlite3.Connection,
) -> int:
    """Walk a parsed extraction payload + emit one entity-level
    extracted_field_sources row per entity that has a citation, plus
    one per high-risk leaf field (see HIGH_RISK_FIELDS).

    Returns the number of rows written. Callers use this once per
    successful Gemini response to bootstrap the field-source table.
    """
    written = 0

    def _emit(entity_type, entity_id, field_name, page):
        nonlocal written
        if page is None or page < 1:
            return
        record_field_source(
            dre_extraction_run_id=dre_extraction_run_id,
            entity_type=entity_type, entity_id=entity_id,
            field_name=field_name, page_number=page,
            connection=connection,
        )
        written += 1

    doc_meta = extraction_payload.get("document_metadata") or {}
    for page in doc_meta.get("source_pages") or []:
        _emit("document_metadata", None, "_entity", page)


    setup = extraction_payload.get("assessment_setup") or {}
    for page in setup.get("source_pages") or []:
        _emit("assessment_setup", None, "_entity", page)


    unit_struct = extraction_payload.get("unit_structure") or {}
    for idx, group in enumerate(unit_struct.get("groups") or []):
        gid = group.get("group_id") or str(idx)
        page = group.get("source_page")
        _emit("group", gid, "_entity", page)
        # High-risk fields on groups
        for key in ("unit_count", "average_square_feet", "ownership_percent"):
            if key in group and group[key] is not None and page is not None:
                _emit("group", gid, key, page)

    for idx, unit in enumerate(unit_struct.get("units") or []):
        uid = unit.get("unit_number") or str(idx)
        page = unit.get("source_page")
        _emit("unit", uid, "_entity", page)
        for key in ("ownership_percent",):
            if key in unit and unit[key] is not None and page is not None:
                _emit("unit", uid, key, page)

    for pool in extraction_payload.get("allocation_pools") or []:
        pkey = pool.get("pool_key")
        for page in pool.get("source_pages") or []:
            _emit("pool", pkey, "_entity", page)
        # High-risk fields on pools: include the first source page if any
        pages = pool.get("source_pages") or []
        primary_page = pages[0] if pages else None
        if primary_page is not None:
            for key in (
                "denominator_value", "allocation_method",
                "annual_amount", "monthly_amount",
            ):
                if pool.get(key) is not None:
                    _emit("pool", pkey, key, primary_page)

    for idx, formula in enumerate(extraction_payload.get("formulas") or []):
        fid = formula.get("formula_name") or str(idx)
        page = formula.get("source_page")
        _emit("formula", fid, "_entity", page)

    reserve = extraction_payload.get("reserve_setup") or {}
    if reserve:
        for page in reserve.get("source_pages") or []:
            _emit("reserve_setup", None, "_entity", page)

    return written


def list_field_sources(
    *,
    dre_extraction_run_id: int,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    connection: sqlite3.Connection,
) -> list[ExtractedFieldSourceResponse]:
    """Return all field sources for a run, optionally filtered by entity."""
    where_clauses = ["dre_extraction_run_id = ?"]
    params: list[Any] = [dre_extraction_run_id]
    if entity_type is not None:
        where_clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id is not None:
        where_clauses.append("entity_id = ?")
        params.append(entity_id)
    rows = connection.execute(
        "SELECT id, dre_extraction_run_id, entity_type, entity_id, "
        "field_name, page_number, bounding_box_json, confidence "
        "FROM extracted_field_sources "
        f"WHERE {' AND '.join(where_clauses)} "
        "ORDER BY id",
        tuple(params),
    ).fetchall()
    return [
        ExtractedFieldSourceResponse(
            source_id=r[0],
            dre_extraction_run_id=r[1],
            entity_type=r[2],
            entity_id=r[3],
            field_name=r[4],
            page_number=r[5],
            bounding_box_json=r[6],
            confidence=r[7],
        )
        for r in rows
    ]


__all__ = [
    "DREReviewEditResponse",
    "ExtractedFieldSourceResponse",
    "HIGH_RISK_FIELDS",
    "list_field_sources",
    "list_review_edits",
    "record_entity_sources_from_extraction",
    "record_field_source",
    "record_review_edit",
    "record_structural_operation",
    "StructuralOperationReasonRequired",
    "StaleStructuralOperation",
]
