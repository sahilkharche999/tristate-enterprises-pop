"""Brownfield backfills for legacy budget line items (Phase 1.4 task 21).

Legacy ``budget_drafts.line_items_json`` rows predate the Phase 1.4
``source_column`` + ``source_page_or_cell`` audit fields. The Old Mill
convention has always been that the persisted amount is annual, so we
backfill missing ``source_column`` values with the literal
``'legacy_promotion'`` so downstream consumers can tell the difference
between (a) audit fields never recorded and (b) audit fields recorded
with that specific sentinel.

This is run idempotently on application startup via ``init_db`` if the
backfill column is missing entirely; it can also be invoked manually
through ``backfill_legacy_budget_audit_fields(connection)``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Iterable

logger = logging.getLogger(__name__)


LEGACY_PROMOTION_MARKER = "legacy_promotion"


def _line_needs_backfill(line: dict) -> bool:
    """Decide whether a budget-line dict is missing audit fields.

    The fix is additive — we only stamp ``source_column`` when it's
    None/absent. Existing values (real audit data from Phase 1.4
    promotion) are never overwritten.
    """
    if not isinstance(line, dict):
        return False
    return not line.get("source_column")


def _stamp_legacy_audit_fields(line: dict) -> dict:
    """Add the legacy marker + a placeholder ``source_page_or_cell``.

    Returns a new dict; never mutates the original.
    """
    out = dict(line)
    out.setdefault("source_column", LEGACY_PROMOTION_MARKER)
    out.setdefault("source_page_or_cell", "legacy")
    return out


def _backfill_one_draft_row(
    *, draft_id: int, line_items_text: str, connection: sqlite3.Connection,
) -> bool:
    """Update one draft row in place. Returns True if a write happened."""
    try:
        items = json.loads(line_items_text)
    except (TypeError, ValueError):
        logger.warning(
            "legacy_backfill: draft %d has unparseable line_items_json; skipping",
            draft_id,
        )
        return False
    if not isinstance(items, list):
        return False
    needs_write = any(_line_needs_backfill(li) for li in items)
    if not needs_write:
        return False
    stamped = [_stamp_legacy_audit_fields(li) for li in items]
    connection.execute(
        "UPDATE budget_drafts SET line_items_json = ? WHERE id = ?",
        (json.dumps(stamped), draft_id),
    )
    return True


def backfill_legacy_budget_audit_fields(
    connection: sqlite3.Connection,
) -> int:
    """Walk every ``budget_drafts`` row + stamp legacy markers on lines
    that lack ``source_column``.

    Returns the number of draft rows updated. Idempotent: re-running
    against an already-stamped table is a no-op (every line already
    has source_column set).
    """
    rows = connection.execute(
        "SELECT id, line_items_json FROM budget_drafts WHERE line_items_json IS NOT NULL"
    ).fetchall()
    updated = 0
    for row_id, items_text in rows:
        if _backfill_one_draft_row(
            draft_id=row_id, line_items_text=items_text, connection=connection,
        ):
            updated += 1
    if updated:
        connection.commit()
    return updated


__all__ = [
    "LEGACY_PROMOTION_MARKER",
    "backfill_legacy_budget_audit_fields",
]
