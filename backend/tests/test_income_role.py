"""Which income rows are the regular assessment.

Gemini tags each income row when it reads a budget; the package takes the
regular_assessment rows as dues. Old drafts carry no tag and keep the old
label test, so their numbers don't move.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.disclosure_package.adapters import from_budget_history_record
from app.disclosure_package.compiler import _compute_all
from app.disclosure_package.package_specs.standard import OLD_MILL_2026
from app.disclosure_package.reconciliation import is_regular_assessment, resolve_assessment_facts
from app.disclosure_package.schemas import (
    BudgetDraft,
    HOAMetadata,
    LineItem,
    ReserveStudyComponent,
    ReserveStudySnapshot,
)

# Old Mill's 2027 final budget (442 Old Mill Budget 2027Final.xlsx).
_OLD_MILL_2027_INCOME = [
    ("Assessments", "2025540", "regular_assessment"),
    ("Parking Garage Assessment Income", "7560", "other_assessment"),
    ("Clubhouse Rental Income", "1810", "other_income"),
]


def _income(tagged: bool) -> list[LineItem]:
    return [
        LineItem(label=label, amount=Decimal(amount), is_revenue=True, income_role=role if tagged else None)
        for label, amount, role in _OLD_MILL_2027_INCOME
    ]


def test_rule_follows_the_tag_and_falls_back_to_the_label():
    assert is_regular_assessment("Monthly Dues", "regular_assessment")
    assert not is_regular_assessment("Parking Garage Assessment Income", "other_assessment")
    assert not is_regular_assessment("Special Assessment", "special_assessment")
    # No tag: today's label test, unchanged.
    assert is_regular_assessment("Assessment Income", None)
    assert is_regular_assessment("Parking Garage Assessment Income", None)
    assert not is_regular_assessment("Monthly Dues", None)


def test_tagged_parking_line_stays_out_of_the_regular_assessment():
    facts = resolve_assessment_facts(
        budget_line_items=_income(tagged=True), approved_monthly_assessment_per_unit=None, units=279
    )
    assert facts.approved_annual_assessment_revenue == Decimal("2025540")
    assert facts.monthly_assessment_per_unit_current == Decimal("605.00")


def test_untagged_lines_keep_todays_numbers():
    facts = resolve_assessment_facts(
        budget_line_items=_income(tagged=False), approved_monthly_assessment_per_unit=None, units=279
    )
    assert facts.approved_annual_assessment_revenue == Decimal("2033100")
    assert facts.monthly_assessment_per_unit_current == Decimal("607.26")


def test_statement_shows_parking_as_other_operating_revenue():
    computed = _compute_all(
        spec=OLD_MILL_2026.model_copy(update={"hoa_id": 10, "fiscal_year": 2027}),
        budget_draft=BudgetDraft(line_items=[
            *_income(tagged=True),
            LineItem(label="Management Service", amount=Decimal("93744")),
        ]),
        reserve_snapshot=ReserveStudySnapshot(
            study_date="September 2026",
            components=[
                ReserveStudyComponent(
                    line_item="Roof", useful_life=20, remaining_life=10, replacement_cost=Decimal("200000")
                )
            ],
        ),
        hoa_metadata=HOAMetadata(
            hoa_id=10, name="Old Mill Homeowners Association", units=279,
            fiscal_year_start_month=1, fiscal_year_end_month=12,
        ),
        effective_hoa_settings={"reserve_cash_by_fiscal_year_json": '{"2027": 2260449.87}'},
    )["computed"]

    statement = computed["annual_statement_facts"]
    assert computed["monthly_assessment_per_unit_current"] == Decimal("605.00")
    assert statement["operating_assessment_revenue"] + statement["reserve_assessment_revenue"] == Decimal("2025540")
    assert statement["other_operating_revenue"] == Decimal("9370")


def test_adapter_carries_the_role_into_the_package():
    draft = from_budget_history_record(SimpleNamespace(line_items=[
        {"label": "Assessments", "category": "income", "annual_budget": 2025540, "income_role": "regular_assessment"},
        {"label": "Management Service", "category": "operating", "annual_budget": 93744},
    ]))
    assert [item.income_role for item in draft.line_items] == ["regular_assessment", None]
