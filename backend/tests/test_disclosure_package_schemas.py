"""Tests for backend/app/disclosure_package/schemas.py (Phase 11 plan 02 Task 1).

RED → GREEN: these tests are written before schemas.py exists. They assert the
shape contract from RESEARCH § "Package Manifest" and CONTEXT D-04. All currency
fields MUST coerce to Decimal (not float) per CONTEXT D-06 and the threat model
T-11-04 (float arithmetic drift).
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError


# ── Test 1: BudgetDraft accepts list[dict]; rejects extra fields ──────────────
def test_budget_draft_accepts_line_items_and_rejects_extras():
    from app.disclosure_package.schemas import BudgetDraft

    bd = BudgetDraft.model_validate({
        "line_items": [
            {
                "label": "Regular Assessment",
                "amount": "1000.00",
                "section": "Income",
                "category": "income",
                "is_reserve": False,
                "is_revenue": True,
            }
        ]
    })
    assert len(bd.line_items) == 1
    assert bd.line_items[0].amount == Decimal("1000.00")

    # extra=forbid at the BudgetDraft level
    with pytest.raises(ValidationError):
        BudgetDraft.model_validate({
            "line_items": [{"label": "X", "amount": "1.00"}],
            "unexpected_field": "boom",
        })


# ── Test 2: ReserveStudySnapshot uses typed components ────────────────────────
def test_reserve_study_snapshot_components_are_typed():
    from app.disclosure_package.schemas import (
        ReserveStudyComponent,
        ReserveStudySnapshot,
    )

    snap = ReserveStudySnapshot(
        study_date="September 2025",
        components=[
            ReserveStudyComponent(
                line_item="Roof",
                useful_life=25,
                remaining_life=10,
                replacement_cost=Decimal("500000"),
                year_new=2010,
            )
        ],
    )
    assert snap.components[0].replacement_cost == Decimal("500000")
    # Income-statement-only PDFs may not provide reserve-study rows.
    empty = ReserveStudySnapshot(study_date="2025", components=[])
    assert empty.components == []


# ── Test 3: Currency fields coerce str/int → Decimal, never float ─────────────
def test_decimal_coercion_currency_fields():
    from app.disclosure_package.schemas import HOAStaticData

    # str input
    static = HOAStaticData(
        hoa_legal_name="X",
        address_line_1="a",
        address_line_2="b",
        city="c",
        state="CA",
        zip="00000",
        management_company="m",
        management_company_address="ma",
        cpa_firm_name="cpa",
        cpa_firm_address="cpa_addr",
        reserve_study_expert_name="rse",
        monthly_assessment_per_unit_current="605.00",
        monthly_assessment_per_unit_prior="605.00",
        reserve_cash_balance_eoy_prior="2600000",
        bank_cd_balance_for_interest=Decimal("3611111.11"),
        income_tax_provision_estimate=18200,
        interest_rate_after_tax=Decimal("0.018"),
        replacement_cost_increase_rate=Decimal("0.03"),
        assessment_increase_schedule=[],
        letter_date="2025-11-18",
        letter_signed_by="Board",
    )
    assert isinstance(static.monthly_assessment_per_unit_current, Decimal)
    assert static.monthly_assessment_per_unit_current == Decimal("605.00")
    assert isinstance(static.reserve_cash_balance_eoy_prior, Decimal)
    assert static.reserve_cash_balance_eoy_prior == Decimal("2600000")
    assert isinstance(static.income_tax_provision_estimate, Decimal)
    assert static.income_tax_provision_estimate == Decimal("18200")


# ── Test 4: HOAStaticData constructs with the Old Mill values ─────────────────
def test_hoa_static_data_old_mill_values_construct():
    from app.disclosure_package.schemas import HOAStaticData

    static = HOAStaticData(
        hoa_legal_name="Old Mill Homeowners Association",
        address_line_1="c/o Tri-State Enterprises, Inc.",
        address_line_2="2133 Leghorn Street",
        city="Mountain View",
        state="CA",
        zip="94043",
        management_company="Tri-State Enterprises, Inc.",
        management_company_address="2133 Leghorn Street, Mountain View, CA 94043",
        cpa_firm_name="Levy, Erlanger & Company LLP",
        cpa_firm_address="100 Montgomery Street, Suite 715, San Francisco, California 94104",
        reserve_study_expert_name="SMA Reserves of San Jose",
        assessment_model="flat",
        monthly_assessment_per_unit_current=Decimal("605.00"),
        monthly_assessment_per_unit_prior=Decimal("605.00"),
        reserve_cash_balance_eoy_prior=Decimal("2600000.00"),
        bank_cd_balance_for_interest=Decimal("3611111.11"),
        income_tax_provision_estimate=Decimal("18200.00"),
        interest_rate_after_tax=Decimal("0.018"),
        replacement_cost_increase_rate=Decimal("0.03"),
        assessment_increase_schedule=[
            (2026, 2035, Decimal("0.03")),
            (2036, 2045, Decimal("0.03")),
            (2046, 2055, Decimal("0.00")),
        ],
        letter_date="Tuesday November 18, 2025",
        letter_signed_by="Board of Directors",
    )
    assert static.assessment_model == "flat"
    assert len(static.assessment_increase_schedule) == 3
    assert static.assessment_increase_schedule[0][2] == Decimal("0.03")
    # default fund_balance_boy_operations is Decimal("0")
    assert static.fund_balance_boy_operations == Decimal("0")


# ── Test 5: PackageEntry is a discriminated union on `kind` ───────────────────
def test_package_entry_discriminated_union():
    from app.disclosure_package.schemas import GeneratedPage, StaticAppendix

    gen = GeneratedPage(template="cover_letter.html", page_count_hint=2)
    assert gen.kind == "generated"
    static = StaticAppendix(file="insurance_certificate.pdf", page_count_hint=3)
    assert static.kind == "static"

    # page_count_hint must be >= 1
    with pytest.raises(ValidationError):
        GeneratedPage(template="x.html", page_count_hint=0)


# ── Test 6: PreflightError shape ──────────────────────────────────────────────
def test_preflight_error_shape():
    from app.disclosure_package.schemas import PreflightError

    err = PreflightError(field_path="budget_draft.line_items", message="empty")
    assert err.field_path == "budget_draft.line_items"
    assert err.message == "empty"
    assert err.severity == "blocking"


# ── Test 7: Old Mill 2026 input fixture validates ─────────────────────────────
def test_old_mill_2026_inputs_fixture_validates():
    from app.disclosure_package.schemas import (
        BudgetDraft,
        HOAMetadata,
        ReserveStudySnapshot,
    )

    fixture_path = (
        Path(__file__).parent / "fixtures" / "old_mill_2026_inputs.json"
    )
    data = json.loads(fixture_path.read_text())

    bd = BudgetDraft.model_validate(data["budget_draft"])
    assert len(bd.line_items) == 9
    # All amounts coerced to Decimal
    for li in bd.line_items:
        assert isinstance(li.amount, Decimal)

    rss = ReserveStudySnapshot.model_validate(data["reserve_study_snapshot"])
    assert len(rss.components) == 3
    assert all(isinstance(c.replacement_cost, Decimal) for c in rss.components)

    meta = HOAMetadata.model_validate(data["hoa_metadata"])
    assert meta.units == 279
    assert meta.fiscal_year_start_month == 1
