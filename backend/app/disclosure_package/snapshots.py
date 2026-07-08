"""Annual-package finalization snapshots (Phase 4.8).

When a package transitions ``status -> finalized``, all four input
sources are serialized into JSON columns on ``annual_packages`` so a
later re-render uses the same inputs even if live state has drifted.
The serializer enforces deterministic output (``sort_keys=True``,
``separators=(',', ':')``) so two serializations of the same data
produce byte-equal text — without this, the re-render test
``test_finalized_package_byte_equal`` fails on whitespace differences
even when the underlying data is unchanged.

The four snapshots persisted on ``annual_packages``:

- ``assessment_setup_snapshot_json`` — the AssessmentSetup row + its
  AllocationPool/Group/Unit children at finalization.
- ``budget_snapshot_json`` — the BudgetDraft.line_items at finalization
  including the Phase 1.4 audit fields (``source_column``,
  ``source_page_or_cell``).
- ``reserve_snapshot_json`` — the reserve study rows used.
- ``appendix_manifest_snapshot_json`` — the resolved manifest (with
  per-package overrides applied) at finalization.

Compile logic SHOULD use these snapshots when ``status='finalized'``
and live data otherwise. The transition is atomic — all four columns
plus ``finalized_at`` are written in one UPDATE.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional


def _json_default(value: Any) -> Any:
    """Coerce Decimals to strings (precision-preserving) and dates to ISO."""
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON-serializable; "
        "snapshot serializer needs an explicit coercion rule."
    )


def serialize_snapshot(value: Any) -> str:
    """Serialize a snapshot payload deterministically.

    All four snapshot serializers (assessment_setup, budget, reserve,
    appendix_manifest) use this so byte-equal output is guaranteed:

    - ``sort_keys=True`` — dict key order doesn't leak in
    - ``separators=(",", ":")`` — no whitespace
    - Decimal → string — full precision preserved
    - dates → ISO format

    The result is a UTF-8-ready string ready for the ``annual_packages``
    JSON columns.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
        ensure_ascii=False,
    )


class SnapshotWriteConflict(RuntimeError):
    """The freeze UPDATE matched no row — a concurrent writer bumped
    ``version_int`` (or changed status) between the caller's read and
    this write. The caller maps this to its optimistic-lock error
    (HTTP 409). Closes the finalize half of review finding H4: two
    concurrent finalizes can no longer both freeze."""


def freeze_package_snapshots(
    *,
    package_id: int,
    assessment_setup: Any,
    budget: Any,
    reserve: Any,
    appendix_manifest: Any,
    compile_context: Any = None,
    assessment_mode: str = "variable",
    expected_version_int: Optional[int] = None,
    connection: sqlite3.Connection,
) -> None:
    """Atomic UPDATE: write all five snapshots + ``finalized_at`` +
    transition ``status`` to ``'finalized'`` in a single statement.

    ``expected_version_int`` puts the optimistic-lock compare into the SQL
    predicate (``AND version_int = ?``); a zero-row UPDATE raises
    :class:`SnapshotWriteConflict` and commits nothing, so concurrent
    finalizes cannot both win. ``None`` preserves the legacy unguarded
    write for callers that manage their own locking.

    Pre-conditions (package exists, finalizable status, preflight clean,
    payloads assembled server-side) are enforced by the caller
    (``annual_package_service.finalize_annual_package``).
    """
    assessment_setup_snapshot = assessment_setup
    if isinstance(assessment_setup, dict):
        assessment_setup_snapshot = {
            **assessment_setup,
            "assessment_mode": assessment_mode,
        }
    snapshots = {
        "assessment_setup_snapshot_json": serialize_snapshot(assessment_setup_snapshot),
        "budget_snapshot_json": serialize_snapshot(budget),
        "reserve_snapshot_json": serialize_snapshot(reserve),
        "appendix_manifest_snapshot_json": serialize_snapshot(appendix_manifest),
        "compile_context_snapshot_json": (
            serialize_snapshot(compile_context) if compile_context is not None else None
        ),
    }
    finalized_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    version_guard = "" if expected_version_int is None else " AND version_int = ?"
    params: list[Any] = [
        snapshots["assessment_setup_snapshot_json"],
        snapshots["budget_snapshot_json"],
        snapshots["reserve_snapshot_json"],
        snapshots["appendix_manifest_snapshot_json"],
        snapshots["compile_context_snapshot_json"],
        finalized_at,
        package_id,
    ]
    if expected_version_int is not None:
        params.append(expected_version_int)

    cursor = connection.execute(
        f"""
        UPDATE annual_packages
           SET assessment_setup_snapshot_json = ?,
               budget_snapshot_json = ?,
               reserve_snapshot_json = ?,
               appendix_manifest_snapshot_json = ?,
               compile_context_snapshot_json = ?,
               finalized_at = ?,
               status = 'finalized',
               version_int = version_int + 1
         WHERE id = ?{version_guard}
        """,
        params,
    )
    if cursor.rowcount == 0:
        connection.rollback()
        raise SnapshotWriteConflict(
            f"freeze_package_snapshots: annual_packages id={package_id} "
            f"was not updated (expected version_int={expected_version_int}); "
            "a concurrent write won."
        )
    connection.commit()


def load_package_snapshots(
    *,
    package_id: int,
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    """Read the four snapshots back out as Python data (dict/list/etc.).

    Returns a dict with keys ``assessment_setup``, ``budget``,
    ``reserve``, ``appendix_manifest``, ``finalized_at``. ``None`` for
    any snapshot column that hasn't been frozen yet (typically because
    the package is still in ``draft`` / ``approved``).
    """
    row = connection.execute(
        """
        SELECT assessment_setup_snapshot_json, budget_snapshot_json,
               reserve_snapshot_json, appendix_manifest_snapshot_json,
               compile_context_snapshot_json,
               finalized_at, status
          FROM annual_packages
         WHERE id = ?
        """,
        (package_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"annual_packages id={package_id} not found")

    def _load(value):
        return json.loads(value) if value else None

    return {
        "assessment_setup": _load(row[0]),
        "budget": _load(row[1]),
        "reserve": _load(row[2]),
        "appendix_manifest": _load(row[3]),
        "compile_context": _load(row[4]),
        "finalized_at": row[5],
        "status": row[6],
    }
