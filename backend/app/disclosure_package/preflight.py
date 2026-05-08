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
  * package_spec.appendices

Gate evaluation order is deterministic (above). When multiple gates fail,
errors are returned in declaration order so the UI can render a stable list.

Threat T-11-05 (path traversal): the appendices_root file-existence check
joins trusted spec.entries[*].file (PackageSpec literal — not user input)
under the caller-provided root via Path joining only. No user-controlled
path component reaches the filesystem here.
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
    StaticAppendix,
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
        appendices_root: filesystem path to look up static appendix files.
            When None, skips the file-existence check (unit-test mode +
            keeps the function pure when callers want to defer the FS
            check to a later stage).
    """
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

    # 5. Static appendix files present on disk (only when appendices_root
    #    is provided; unit tests pass None to keep the check pure).
    if appendices_root is not None:
        for entry in spec.entries:
            if isinstance(entry, StaticAppendix):
                appendix_path = appendices_root / entry.file
                if not appendix_path.exists():
                    errors.append(PreflightError(
                        field_path="package_spec.appendices",
                        message=f"Static appendix file not found: {entry.file}",
                        severity="blocking",
                    ))

    return errors
