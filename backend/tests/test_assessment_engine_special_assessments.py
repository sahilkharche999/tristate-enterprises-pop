"""Special-assessment engine integration tests (Phase 4.4).

The engine applies special assessments per the matrix in
``specs/assessment-engine/spec.md`` §"Special Assessment Engine
Behavior Matrix":

| status               | included | Engine action                                 |
|----------------------|----------|-----------------------------------------------|
| none / missing       | -        | Ignore                                        |
| approved_scheduled   | true     | Append pseudo-pool row per recipient          |
| approved_scheduled   | false    | Renderer event: separate_disclosure_block     |
| possible_disclosure  | -        | Renderer event: disclosure_language           |

Each matrix row is covered by an explicit scenario.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.assessment_engine import (
    BudgetLineInput,
    CalcInput,
    PoolDefinition,
    RecipientReference,
    RecipientSet,
    SpecialAssessmentInput,
)
from app.assessment_engine.engine import run
from app.assessment_engine.schemas import BudgetLineMappingInput


def _baseline() -> CalcInput:
    """5 units, 1 equal pool, $100/mo each → $6,000 annual."""
    units = [
        RecipientReference(ref_type="unit", ref_id=i, label=f"U{i}", unit_count=1)
        for i in range(1, 6)
    ]
    return CalcInput(
        setup_type="fixed",
        pools=[
            PoolDefinition(
                pool_id=1, pool_key="equal", pool_name="Equal",
                allocation_method="equal", recipient_scope="all_units",
            ),
        ],
        recipient_set=RecipientSet(recipients=units),
        budget_lines=[
            BudgetLineInput(
                line_id=1, normalized_label="dues",
                section="income", category="income",
                fund_type="operating", amount=Decimal("6000"),
            ),
        ],
        mappings=[
            BudgetLineMappingInput(
                budget_line_normalized_label="dues",
                section="income", category="income",
                fund_type="operating", pool_key="equal",
            ),
        ],
        approved_assessment_revenue_annual=Decimal("6000"),
    )


class TestStatusNoneOrMissing:
    def test_none_status_ignored(self) -> None:
        ci = _baseline()
        ci.special_assessments = [
            SpecialAssessmentInput(status="none", amount_per_unit=Decimal("999")),
        ]
        result = run(ci)
        # Pool allocations contain only the 5 equal-pool rows; no SA rows
        assert all(r.pool_key == "equal" for r in result.pool_allocations)
        assert result.special_assessment_events == []


class TestApprovedScheduledIncluded:
    def test_appends_one_pseudo_pool_row_per_recipient(self) -> None:
        ci = _baseline()
        ci.special_assessments = [
            SpecialAssessmentInput(
                status="approved_scheduled",
                included_in_regular_monthly=True,
                amount_per_unit=Decimal("50"),
                label="Roof SA 2026",
            ),
        ]
        result = run(ci)
        sa_rows = [r for r in result.pool_allocations if r.pool_key == "special_assessment"]
        # 5 units → 5 pseudo-pool rows (NOT N units × N pools)
        assert len(sa_rows) == 5
        assert all(r.pool_id is None for r in sa_rows)
        assert all(r.source == "special_assessment" for r in sa_rows)
        assert all(r.unrounded_component_monthly == Decimal("50") for r in sa_rows)

    def test_contributes_to_reconciliation(self) -> None:
        ci = _baseline()
        ci.special_assessments = [
            SpecialAssessmentInput(
                status="approved_scheduled",
                included_in_regular_monthly=True,
                amount_per_unit=Decimal("50"),
            ),
        ]
        # Increase approved revenue to include the SA: regular $6000 + SA (5×50×12=3000) = 9000
        ci.approved_assessment_revenue_annual = Decimal("9000")
        result = run(ci)
        # Each unit now pays 100 + 50 = 150/mo; annual per unit = 1800; total = 9000
        for t in result.recipient_totals:
            assert t.rounded_monthly_total == Decimal("150.00")
            assert t.annual_total == Decimal("1800.00")
        assert result.rounding_delta_annual == Decimal("0.00")

    def test_no_renderer_event_when_included(self) -> None:
        ci = _baseline()
        ci.special_assessments = [
            SpecialAssessmentInput(
                status="approved_scheduled",
                included_in_regular_monthly=True,
                amount_per_unit=Decimal("50"),
            ),
        ]
        ci.approved_assessment_revenue_annual = Decimal("9000")
        result = run(ci)
        # Included = no separate renderer event
        assert result.special_assessment_events == []

    def test_recipient_scope_filters_applicable_recipients(self) -> None:
        # 4 units: 2 residential, 2 commercial; SA only applies to residential
        units = [
            RecipientReference(
                ref_type="unit", ref_id=1, label="R1",
                unit_count=1, category="residential",
            ),
            RecipientReference(
                ref_type="unit", ref_id=2, label="R2",
                unit_count=1, category="residential",
            ),
            RecipientReference(
                ref_type="unit", ref_id=3, label="C1",
                unit_count=1, category="commercial",
            ),
            RecipientReference(
                ref_type="unit", ref_id=4, label="C2",
                unit_count=1, category="commercial",
            ),
        ]
        ci = CalcInput(
            setup_type="fixed",
            pools=[
                PoolDefinition(
                    pool_id=1, pool_key="equal", pool_name="Equal",
                    allocation_method="equal", recipient_scope="all_units",
                ),
            ],
            recipient_set=RecipientSet(recipients=units),
            budget_lines=[
                BudgetLineInput(
                    line_id=1, normalized_label="dues",
                    section="income", category="income",
                    fund_type="operating", amount=Decimal("4800"),
                ),
            ],
            mappings=[
                BudgetLineMappingInput(
                    budget_line_normalized_label="dues",
                    section="income", category="income",
                    fund_type="operating", pool_key="equal",
                ),
            ],
            approved_assessment_revenue_annual=Decimal("4800") + Decimal("1200"),
            special_assessments=[
                SpecialAssessmentInput(
                    status="approved_scheduled",
                    included_in_regular_monthly=True,
                    amount_per_unit=Decimal("50"),
                    recipient_scope="residential_only",
                ),
            ],
        )
        result = run(ci)
        sa_rows = [r for r in result.pool_allocations if r.pool_key == "special_assessment"]
        assert len(sa_rows) == 2
        sa_unit_ids = {r.recipient_ref.ref_id for r in sa_rows}
        assert sa_unit_ids == {1, 2}


class TestApprovedScheduledNotIncluded:
    def test_emits_separate_disclosure_block_event_only(self) -> None:
        ci = _baseline()
        ci.special_assessments = [
            SpecialAssessmentInput(
                status="approved_scheduled",
                included_in_regular_monthly=False,
                amount_per_unit=Decimal("200"),
                label="Special roof assessment",
                due_date="2026-09-01",
            ),
        ]
        result = run(ci)
        # No pseudo-pool rows
        assert not any(
            r.pool_key == "special_assessment" for r in result.pool_allocations
        )
        # One renderer event
        assert len(result.special_assessment_events) == 1
        ev = result.special_assessment_events[0]
        assert ev.kind == "separate_disclosure_block"
        assert ev.amount_per_unit == Decimal("200")
        assert ev.due_date == "2026-09-01"
        assert ev.label == "Special roof assessment"

    def test_excluded_from_reconciliation(self) -> None:
        ci = _baseline()
        ci.special_assessments = [
            SpecialAssessmentInput(
                status="approved_scheduled",
                included_in_regular_monthly=False,
                amount_per_unit=Decimal("999"),  # huge but excluded
            ),
        ]
        result = run(ci)
        # Each unit pays 100/mo regular; SA does NOT inflate reconciliation
        for t in result.recipient_totals:
            assert t.rounded_monthly_total == Decimal("100.00")
        assert result.rounding_delta_annual == Decimal("0.00")


class TestPossibleDisclosureOnly:
    def test_emits_disclosure_language_event(self) -> None:
        ci = _baseline()
        ci.special_assessments = [
            SpecialAssessmentInput(
                status="possible_disclosure_only",
                amount_per_unit=Decimal("0"),
                display_language=(
                    "The Board may impose a special assessment to fund "
                    "extraordinary repairs."
                ),
                label="Possible SA",
            ),
        ]
        result = run(ci)
        assert not any(
            r.pool_key == "special_assessment" for r in result.pool_allocations
        )
        assert len(result.special_assessment_events) == 1
        ev = result.special_assessment_events[0]
        assert ev.kind == "disclosure_language"
        assert "extraordinary repairs" in (ev.display_language or "")

    def test_no_financial_effect(self) -> None:
        ci = _baseline()
        ci.special_assessments = [
            SpecialAssessmentInput(
                status="possible_disclosure_only",
                display_language="An assessment may be levied...",
            ),
        ]
        result = run(ci)
        for t in result.recipient_totals:
            assert t.rounded_monthly_total == Decimal("100.00")
        assert result.rounding_delta_annual == Decimal("0.00")


class TestMultipleSpecialAssessments:
    def test_mix_of_kinds_handled_independently(self) -> None:
        ci = _baseline()
        ci.special_assessments = [
            SpecialAssessmentInput(
                status="approved_scheduled",
                included_in_regular_monthly=True,
                amount_per_unit=Decimal("20"),
                label="Included",
            ),
            SpecialAssessmentInput(
                status="approved_scheduled",
                included_in_regular_monthly=False,
                amount_per_unit=Decimal("300"),
                label="Separate",
                due_date="2026-09-01",
            ),
            SpecialAssessmentInput(
                status="possible_disclosure_only",
                display_language="May happen.",
                label="Possible",
            ),
        ]
        # Adjust approved revenue for the included SA: regular 6000 + 5×20×12=1200 = 7200
        ci.approved_assessment_revenue_annual = Decimal("7200")
        result = run(ci)
        # 5 pseudo-pool rows from the included SA
        sa_rows = [r for r in result.pool_allocations if r.pool_key == "special_assessment"]
        assert len(sa_rows) == 5
        # 2 renderer events (separate + disclosure language)
        kinds = sorted(ev.kind for ev in result.special_assessment_events)
        assert kinds == ["disclosure_language", "separate_disclosure_block"]
        # Reconciliation balances against the augmented revenue
        assert result.rounding_delta_annual == Decimal("0.00")
