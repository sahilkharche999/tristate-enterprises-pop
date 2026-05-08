"""Preflight gate (CONTEXT D-19).

Returns ``list[PreflightError]`` with stable field paths matching UI-SPEC §9.3.
Render is gated on zero blocking errors. Errors map cleanly to the
``DisclosurePreflightChecklist`` UI rows; the field_path strings are the
verbatim row keys the frontend reads.

Field path contract (REQ-D11-004, REQ-D11-005):
  * budget_draft.line_items
  * reserve_study_snapshot.components
  * hoa_metadata.fiscal_year_end_month
  * reserve_cash_balance.amount

Gate evaluation order is deterministic (above). When multiple gates fail,
errors are returned in declaration order so the UI can render a stable list.

Static appendix files are intentionally NOT preflight-blocking. Whatever PDFs
exist in the appendices directory at compile time are merged in; missing
spec entries are skipped (compiler.py logs a warning). This keeps local-dev
generation working without the full Old Mill legal-review extraction, and
lets operators drop ad-hoc appendix PDFs in without updating the PackageSpec.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .schemas import (
    BudgetDraft,
    HOAMetadata,
    PackageSpec,
    PreflightError,
    ReserveStudySnapshot,
)


def validate_inputs(
    *,
    spec: PackageSpec,
    budget_draft: BudgetDraft,
    reserve_snapshot: ReserveStudySnapshot,
    hoa_metadata: HOAMetadata,
    appendices_root: Optional[Path] = None,
) -> list[PreflightError]:
    """Return the list of blocking + warning errors. Empty list = ready to render.

    Args:
        spec: PackageSpec for the target HOA (provides static_data and entries).
        budget_draft: BudgetDraft with operating + reserve line items.
        reserve_snapshot: ReserveStudySnapshot from Phase 10 extractor.
        hoa_metadata: HOAMetadata from properties table.
        appendices_root: accepted for backward compatibility; no longer used.
            Static appendix existence is no longer a preflight concern —
            see compiler.py for the merge-time skip-and-warn behaviour.
    """
    del appendices_root  # appendix existence is checked at merge time, not here

    errors: list[PreflightError] = []

    # 1. Budget line items present (REQ-D11-005)
    if not budget_draft.line_items:
        errors.append(PreflightError(
            field_path="budget_draft.line_items",
            message="At least one line item is required",
            severity="blocking",
        ))

    # 2. Reserve study components present
    if not reserve_snapshot.components:
        errors.append(PreflightError(
            field_path="reserve_study_snapshot.components",
            message="Reserve study has no components — at least one is required",
            severity="blocking",
        ))

    # 3. HOA fiscal year end month set + valid (1-12)
    fy_end = hoa_metadata.fiscal_year_end_month
    if fy_end is None or not (1 <= fy_end <= 12):
        errors.append(PreflightError(
            field_path="hoa_metadata.fiscal_year_end_month",
            message="HOA fiscal_year_end_month must be 1-12",
            severity="blocking",
        ))

    # 4. Reserve cash balance set (REQ-D11-004; for Phase 11 this comes from
    #    spec.static_data — Phase 12+ moves it into a database-backed admin
    #    form and the field_path stays the same).
    if spec.static_data.reserve_cash_balance_eoy_prior <= 0:
        errors.append(PreflightError(
            field_path="reserve_cash_balance.amount",
            message="Reserve cash balance for end of prior year must be > 0",
            severity="blocking",
        ))

    return errors
