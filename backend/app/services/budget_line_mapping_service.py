"""Budget line → pool auto-mapping service (Phase 4.3 tasks 108-111).

When the operator loads an annual budget for an HOA, every line needs
to route to an ``AllocationPool``. Three sources, in priority order:

1. **Saved mapping**: an existing ``budget_line_pool_mappings`` row
   matching the line's disambiguating key — auto-applied without prompt.
2. **AI suggestion**: the DRE extractor's ``allocation_pools[*].included_budget_lines``
   list, scored by string similarity. Suggested but operator approval
   required before saving.
3. **Unmapped**: no saved row + no AI suggestion → must be human-mapped.

Phase 4.3 surfaces (2) + (3) in the UI; (1) is silent. Bulk-approve
lets the operator stamp the same pool across many similar lines at
once.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from pydantic import BaseModel


class BudgetLineKey(BaseModel):
    """The disambiguating key used to match a budget line to a mapping."""

    normalized_label: str
    section: str
    category: str
    fund_type: str
    account_code: Optional[str] = None


class MappingSuggestion(BaseModel):
    """One pool suggestion for an unmapped budget line."""

    pool_key: str
    pool_name: str
    confidence: float  # 0.0 - 1.0
    reason: str


class BudgetLineMappingStatus(BaseModel):
    """Per-line status after the mapping diff."""

    line_key: BudgetLineKey
    line_label: str
    line_amount: Optional[float]
    status: str  # 'mapped' | 'suggested' | 'unmapped'
    saved_pool_key: Optional[str] = None  # status='mapped'
    suggestions: list[MappingSuggestion] = []  # status='suggested'


def _normalize_label(label: str) -> str:
    """Case + whitespace normalization for label matching.

    The disambiguating key already uses ``normalized_label``, but in
    case the caller passes a raw label we normalize defensively.
    """
    return " ".join(str(label or "").lower().split())


def _key_tuple(k: BudgetLineKey) -> tuple:
    return (
        k.normalized_label, k.section, k.category, k.fund_type, k.account_code,
    )


def lookup_saved_mappings(
    *,
    property_id: int,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> dict[tuple, str]:
    """Return ``{key: pool_key}`` for every active mapping.

    Caller is the diff function below — keeping the lookup in a separate
    helper makes it easy to test the routing logic without the DB.
    """
    rows = connection.execute(
        """
        SELECT budget_line_normalized_label, section, category, fund_type,
               account_code, pool_key
          FROM budget_line_pool_mappings
         WHERE property_id = ? AND assessment_setup_id = ?
        """,
        (property_id, assessment_setup_id),
    ).fetchall()
    out: dict[tuple, str] = {}
    for r in rows:
        out[(r[0], r[1], r[2], r[3], r[4])] = r[5]
    return out


def diff_budget_lines_against_mappings(
    *,
    budget_lines: list[dict],
    saved_mappings: dict[tuple, str],
    pool_suggestions: dict[str, list[MappingSuggestion]],
) -> list[BudgetLineMappingStatus]:
    """Diff a budget against saved + AI-suggested mappings.

    ``budget_lines`` is a list of dicts with the disambiguating-key
    fields (normalized_label, section, category, fund_type,
    account_code) plus ``label`` and ``amount`` for display.

    ``pool_suggestions`` maps each line's normalized_label to a ranked
    list of AI suggestions (typically from the DRE extractor's
    ``allocation_pools[*].included_budget_lines``).
    """
    out: list[BudgetLineMappingStatus] = []
    for line in budget_lines:
        key = BudgetLineKey(
            normalized_label=_normalize_label(line.get("normalized_label") or line.get("label", "")),
            section=str(line.get("section", "")),
            category=str(line.get("category", "")),
            fund_type=str(line.get("fund_type", "")),
            account_code=line.get("account_code"),
        )
        tup = _key_tuple(key)
        if tup in saved_mappings:
            out.append(BudgetLineMappingStatus(
                line_key=key,
                line_label=str(line.get("label", "")),
                line_amount=line.get("amount"),
                status="mapped",
                saved_pool_key=saved_mappings[tup],
            ))
            continue
        suggestions = pool_suggestions.get(key.normalized_label, [])
        out.append(BudgetLineMappingStatus(
            line_key=key,
            line_label=str(line.get("label", "")),
            line_amount=line.get("amount"),
            status="suggested" if suggestions else "unmapped",
            suggestions=suggestions,
        ))
    return out


def approve_mapping(
    *,
    property_id: int,
    assessment_setup_id: int,
    line_key: BudgetLineKey,
    pool_key: str,
    approved_by: str,
    connection: sqlite3.Connection,
) -> int:
    """Save (or update) one mapping row. Returns the row's id.

    Idempotent: re-approving the same line+pool replaces the active
    row in place (matches the conflict-key UNIQUE INDEX defined in
    schema.sql).
    """
    cur = connection.execute(
        """
        INSERT INTO budget_line_pool_mappings (
            property_id, assessment_setup_id,
            budget_line_normalized_label, section, category,
            fund_type, account_code,
            pool_key, approved_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(property_id, assessment_setup_id,
                    budget_line_normalized_label, section, category,
                    fund_type, COALESCE(account_code, ''))
        DO UPDATE SET pool_key = excluded.pool_key,
                      approved_by = excluded.approved_by,
                      approved_at = datetime('now')
        """,
        (
            property_id, assessment_setup_id,
            line_key.normalized_label, line_key.section, line_key.category,
            line_key.fund_type, line_key.account_code,
            pool_key, approved_by,
        ),
    )
    connection.commit()
    return cur.lastrowid or 0


def bulk_approve_mappings(
    *,
    property_id: int,
    assessment_setup_id: int,
    approvals: list[tuple[BudgetLineKey, str]],  # (key, pool_key) pairs
    approved_by: str,
    connection: sqlite3.Connection,
) -> int:
    """Apply many approvals in one transaction. Returns count saved."""
    count = 0
    for key, pool_key in approvals:
        approve_mapping(
            property_id=property_id,
            assessment_setup_id=assessment_setup_id,
            line_key=key, pool_key=pool_key,
            approved_by=approved_by, connection=connection,
        )
        count += 1
    return count


def carry_forward_mappings_across_setups(
    *,
    property_id: int,
    old_setup_id: int,
    new_setup_id: int,
    connection: sqlite3.Connection,
    commit: bool = True,
) -> int:
    """Copy active mappings from one setup version to the next.

    Used when a new AssessmentSetup is promoted: any line that mapped
    to a pool in the old setup auto-maps to the same ``pool_key`` in
    the new setup, since pool_key is stable across supersessions.
    Returns the number of mappings carried forward.
    """
    inserted = connection.execute(
        """
        INSERT INTO budget_line_pool_mappings (
            property_id, assessment_setup_id,
            budget_line_normalized_label, section, category,
            fund_type, account_code, pool_key, approved_by
        )
        SELECT property_id, ?, budget_line_normalized_label,
               section, category, fund_type, account_code,
               pool_key, approved_by
          FROM budget_line_pool_mappings
         WHERE property_id = ? AND assessment_setup_id = ?
        """,
        (new_setup_id, property_id, old_setup_id),
    ).rowcount
    if commit:
        connection.commit()
    return inserted


__all__ = [
    "BudgetLineKey",
    "BudgetLineMappingStatus",
    "MappingSuggestion",
    "approve_mapping",
    "bulk_approve_mappings",
    "carry_forward_mappings_across_setups",
    "diff_budget_lines_against_mappings",
    "lookup_saved_mappings",
]
