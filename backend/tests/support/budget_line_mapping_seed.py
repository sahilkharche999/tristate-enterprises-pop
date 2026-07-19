"""Seed budget_line_pool_mappings rows for tests (replaces deleted approve_mapping)."""
from __future__ import annotations

import sqlite3
from typing import Optional


def seed_budget_line_mapping(
    *,
    connection: sqlite3.Connection,
    property_id: int,
    assessment_setup_id: int,
    normalized_label: str,
    pool_key: str,
    section: str = "operating",
    category: str = "operating",
    fund_type: str = "operating",
    account_code: Optional[str] = None,
    approved_by: str = "test",
) -> int:
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
            normalized_label, section, category,
            fund_type, account_code,
            pool_key, approved_by,
        ),
    )
    connection.commit()
    return cur.lastrowid or 0


def lookup_saved_mappings(
    *,
    property_id: int,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> dict[tuple, str]:
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
