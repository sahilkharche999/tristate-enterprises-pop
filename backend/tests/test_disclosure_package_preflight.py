"""Tests for backend/app/disclosure_package/preflight.py (Phase 11 plan 03 Task 2).

RED → GREEN: these tests are authored before preflight.py exists. They pin
the field-path contract from CONTEXT D-19 and UI-SPEC §9.3 — the
`DisclosurePreflightChecklist` UI component reads these strings verbatim
to render the row labels.

Render is gated on zero blocking errors. Field paths MUST stay stable:
- budget_draft.line_items
- reserve_study_snapshot.components
- hoa_metadata.fiscal_year_end_month
- reserve_cash_balance.amount
- package_spec.appendices

Pure-function: I/O is limited to the optional appendices_root file-existence
check (test 8 confirms None skips the FS).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _valid_budget():
    from app.disclosure_package.schemas import BudgetDraft, LineItem
    return BudgetDraft(line_items=[
        LineItem(label="X", amount=Decimal("100.00"), is_revenue=True, section="Income"),
    ])


def _valid_reserve_snapshot():
    from app.disclosure_package.schemas import (
        ReserveStudyComponent,
        ReserveStudySnapshot,
    )
    return ReserveStudySnapshot(
        study_date="September 2025",
        components=[
            ReserveStudyComponent(
                line_item="Roof",
                useful_life=25,
                remaining_life=10,
                replacement_cost=Decimal("500000.00"),
            ),
        ],
    )


def _valid_hoa_metadata(fiscal_year_end_month: int = 12):
    from app.disclosure_package.schemas import HOAMetadata
    return HOAMetadata(
        hoa_id=1,
        name="Old Mill Homeowners Association",
        units=279,
        fiscal_year_start_month=1,
        fiscal_year_end_month=fiscal_year_end_month,
    )


def _spec_with_cash_balance(amount: Decimal):
    """Build a minimal PackageSpec with a controllable reserve_cash_balance_eoy_prior.

    We import the OLD_MILL_2026 spec and rebuild it with a tweaked static_data
    so we don't need to redeclare every field.
    """
    from app.disclosure_package.package_specs import OLD_MILL_2026
    from app.disclosure_package.schemas import PackageSpec, HOAStaticData

    new_static = OLD_MILL_2026.static_data.model_copy(
        update={"reserve_cash_balance_eoy_prior": amount}
    )
    return PackageSpec(
        hoa_id=OLD_MILL_2026.hoa_id,
        fiscal_year=OLD_MILL_2026.fiscal_year,
        jurisdiction=OLD_MILL_2026.jurisdiction,
        static_data=new_static,
        entries=OLD_MILL_2026.entries,
    )


# ── Test 1: All inputs valid → empty error list ───────────────────────────────


def test_validate_inputs_returns_empty_when_all_valid():
    from app.disclosure_package.preflight import validate_inputs
    from app.disclosure_package.package_specs import OLD_MILL_2026

    errors = validate_inputs(
        spec=OLD_MILL_2026,
        budget_draft=_valid_budget(),
        reserve_snapshot=_valid_reserve_snapshot(),
        hoa_metadata=_valid_hoa_metadata(),
        appendices_root=None,
    )
    assert errors == []


# ── Test 2: Empty budget line_items → blocking error with correct field_path ─


def test_validate_inputs_flags_empty_budget_line_items():
    """REQ-D11-005: render is gated on at least one budget line item."""
    from app.disclosure_package.preflight import validate_inputs
    from app.disclosure_package.package_specs import OLD_MILL_2026
    from app.disclosure_package.schemas import BudgetDraft, LineItem

    # Pydantic BudgetDraft enforces min_length=1, so we bypass model
    # construction and pass an empty BudgetDraft via the schema-internal
    # "construct" escape hatch — preflight must still catch it defensively
    # in case an upstream caller built the object without validation.
    empty_budget = BudgetDraft.model_construct(line_items=[])

    errors = validate_inputs(
        spec=OLD_MILL_2026,
        budget_draft=empty_budget,
        reserve_snapshot=_valid_reserve_snapshot(),
        hoa_metadata=_valid_hoa_metadata(),
        appendices_root=None,
    )
    paths = [e.field_path for e in errors]
    assert "budget_draft.line_items" in paths
    relevant = [e for e in errors if e.field_path == "budget_draft.line_items"]
    assert relevant[0].severity == "blocking"


# ── Test 3: Empty reserve study components → blocking error ───────────────────


def test_validate_inputs_flags_empty_reserve_components():
    from app.disclosure_package.preflight import validate_inputs
    from app.disclosure_package.package_specs import OLD_MILL_2026
    from app.disclosure_package.schemas import ReserveStudySnapshot

    empty_snap = ReserveStudySnapshot.model_construct(
        study_date="September 2025", components=[]
    )

    errors = validate_inputs(
        spec=OLD_MILL_2026,
        budget_draft=_valid_budget(),
        reserve_snapshot=empty_snap,
        hoa_metadata=_valid_hoa_metadata(),
        appendices_root=None,
    )
    paths = [e.field_path for e in errors]
    assert "reserve_study_snapshot.components" in paths


# ── Test 4: fiscal_year_end_month out of 1-12 range → blocking error ──────────


def test_validate_inputs_flags_invalid_fiscal_year_end_month():
    from app.disclosure_package.preflight import validate_inputs
    from app.disclosure_package.package_specs import OLD_MILL_2026
    from app.disclosure_package.schemas import HOAMetadata

    # 0 is below the valid range; use model_construct to bypass Pydantic's
    # ge=1, le=12 constraint, simulating an upstream caller that built
    # the object without validation.
    bad_meta = HOAMetadata.model_construct(
        hoa_id=1,
        name="X",
        units=279,
        fiscal_year_start_month=1,
        fiscal_year_end_month=0,
    )
    errors = validate_inputs(
        spec=OLD_MILL_2026,
        budget_draft=_valid_budget(),
        reserve_snapshot=_valid_reserve_snapshot(),
        hoa_metadata=bad_meta,
        appendices_root=None,
    )
    paths = [e.field_path for e in errors]
    assert "hoa_metadata.fiscal_year_end_month" in paths


# ── Test 5: reserve_cash_balance.amount <= 0 → blocking error (REQ-D11-004) ──


def test_validate_inputs_flags_zero_or_negative_reserve_cash_balance():
    from app.disclosure_package.preflight import validate_inputs

    spec = _spec_with_cash_balance(Decimal("0"))
    errors = validate_inputs(
        spec=spec,
        budget_draft=_valid_budget(),
        reserve_snapshot=_valid_reserve_snapshot(),
        hoa_metadata=_valid_hoa_metadata(),
        appendices_root=None,
    )
    paths = [e.field_path for e in errors]
    assert "reserve_cash_balance.amount" in paths


# ── Test 6: missing static appendix files do NOT block preflight ──────────────


def test_validate_inputs_does_not_block_on_missing_static_appendices(tmp_path: Path):
    """Missing appendix files are intentionally not preflight-blocking — the
    compiler skips them and continues. This keeps generation working when
    the operator hasn't extracted the full Old Mill manifest, and lets them
    drop ad-hoc PDFs in without updating the spec.
    """
    from app.disclosure_package.preflight import validate_inputs
    from app.disclosure_package.package_specs import OLD_MILL_2026

    errors = validate_inputs(
        spec=OLD_MILL_2026,
        budget_draft=_valid_budget(),
        reserve_snapshot=_valid_reserve_snapshot(),
        hoa_metadata=_valid_hoa_metadata(),
        appendices_root=tmp_path,
    )
    paths = [e.field_path for e in errors]
    assert "package_spec.appendices" not in paths


# ── Test 7: multiple gates fail → all errors returned in deterministic order ──


def test_validate_inputs_returns_multiple_errors_in_order():
    """Order: budget → reserve_study → hoa_metadata → cash_balance → appendices."""
    from app.disclosure_package.preflight import validate_inputs
    from app.disclosure_package.schemas import (
        BudgetDraft,
        HOAMetadata,
        ReserveStudySnapshot,
    )

    spec = _spec_with_cash_balance(Decimal("0"))
    empty_budget = BudgetDraft.model_construct(line_items=[])
    empty_snap = ReserveStudySnapshot.model_construct(
        study_date="X", components=[]
    )
    bad_meta = HOAMetadata.model_construct(
        hoa_id=1, name="X", units=279,
        fiscal_year_start_month=1, fiscal_year_end_month=13,
    )

    errors = validate_inputs(
        spec=spec,
        budget_draft=empty_budget,
        reserve_snapshot=empty_snap,
        hoa_metadata=bad_meta,
        appendices_root=None,  # skip filesystem
    )
    paths = [e.field_path for e in errors]
    # All four non-FS gates should fire and appear in declaration order.
    expected_order = [
        "budget_draft.line_items",
        "reserve_study_snapshot.components",
        "hoa_metadata.fiscal_year_end_month",
        "reserve_cash_balance.amount",
    ]
    # Filter to only the expected paths (in case impl emits extras), and
    # assert the declared order is preserved.
    filtered = [p for p in paths if p in expected_order]
    assert filtered == expected_order


# ── Test 8: appendices_root=None skips filesystem (pure-function unit mode) ──


def test_validate_inputs_skips_filesystem_when_appendices_root_is_none():
    """Test 8 from plan: validate_inputs is pure (no I/O except the
    appendices file-existence check); appendices_root=None skips it.
    """
    from app.disclosure_package.preflight import validate_inputs
    from app.disclosure_package.package_specs import OLD_MILL_2026

    # Even though OLD_MILL_2026 references many StaticAppendix files that
    # don't exist anywhere, appendices_root=None must NOT emit any
    # 'package_spec.appendices' error.
    errors = validate_inputs(
        spec=OLD_MILL_2026,
        budget_draft=_valid_budget(),
        reserve_snapshot=_valid_reserve_snapshot(),
        hoa_metadata=_valid_hoa_metadata(),
        appendices_root=None,
    )
    paths = [e.field_path for e in errors]
    assert "package_spec.appendices" not in paths
