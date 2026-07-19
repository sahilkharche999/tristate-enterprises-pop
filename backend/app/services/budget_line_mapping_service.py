"""Budget line → pool mapping carry-forward (Phase 4.3).

Operator mapping lives in assessment mapping review + materialize. This
module only copies saved ``budget_line_pool_mappings`` rows across
AssessmentSetup supersessions (DRE/CCR approve).
"""

from __future__ import annotations

import sqlite3


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
    "carry_forward_mappings_across_setups",
]
