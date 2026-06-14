from __future__ import annotations

from decimal import Decimal

from app.disclosure_package.reconciliation import (
    ReserveFundingPlanRow,
    normalize_packet_archetype,
    resolve_assessment_presentation_facts,
    parse_optional_decimal_setting,
    resolve_assessment_facts,
    resolve_reserve_funding_facts,
    normalize_reserve_funding_source,
)
from app.disclosure_package.schemas import LineItem


def test_parse_optional_decimal_setting_distinguishes_blank_from_zero() -> None:
    assert parse_optional_decimal_setting(None) is None
    assert parse_optional_decimal_setting("") is None
    assert parse_optional_decimal_setting("  ") is None
    assert parse_optional_decimal_setting(0) == Decimal("0")
    assert parse_optional_decimal_setting("0") == Decimal("0")
    assert parse_optional_decimal_setting("0.00") == Decimal("0.00")


def test_normalize_reserve_funding_source_preserves_legacy_component_name() -> None:
    assert normalize_reserve_funding_source(None) == "auto"
    assert normalize_reserve_funding_source("") == "auto"
    assert normalize_reserve_funding_source("reserve_study_provision") == "component_annual_provision"
    assert normalize_reserve_funding_source("manual") == "manual"
    assert normalize_reserve_funding_source("budget_allocation_line") == "budget_reserve_contribution"


def test_reserve_funding_prefers_manual_amount_when_source_manual() -> None:
    facts = resolve_reserve_funding_facts(
        funding_source="manual",
        manual_annual_amount=Decimal("850998"),
        budget_line_items=[
            LineItem(label="Monthly Contribution to Reserve", amount=Decimal("824414")),
        ],
        reserve_funding_plan_rows=[
            ReserveFundingPlanRow(year=2026, annual_contribution=Decimal("900000"))
        ],
        component_annual_provision=Decimal("558381"),
        units=279,
        fiscal_year=2026,
    )

    assert facts.source == "manual"
    assert facts.annual_contribution == Decimal("850998")
    assert facts.monthly_total == Decimal("70916.50")
    assert facts.monthly_per_unit == Decimal("254.18")
    assert facts.budget_annual_contribution == Decimal("824414")
    assert facts.study_recommended_annual_contribution == Decimal("900000")
    assert facts.component_annual_provision == Decimal("558381")


def test_reserve_funding_auto_prefers_budget_contribution_over_component_provision() -> None:
    facts = resolve_reserve_funding_facts(
        funding_source=None,
        manual_annual_amount=None,
        budget_line_items=[
            LineItem(label="Monthly Contribution to Reserve", amount=Decimal("824414")),
            LineItem(label="Reserve Interest", amount=Decimal("1200"), is_revenue=True),
        ],
        reserve_funding_plan_rows=[
            ReserveFundingPlanRow(year=2026, annual_contribution=Decimal("850998"))
        ],
        component_annual_provision=Decimal("558381"),
        units=279,
        fiscal_year=2026,
    )

    assert facts.source == "budget_reserve_contribution"
    assert facts.annual_contribution == Decimal("824414")
    assert facts.monthly_total == Decimal("68701.17")
    assert facts.monthly_per_unit == Decimal("246.24")
    assert not any("component annual provision" in warning for warning in facts.warnings)


def test_reserve_funding_uses_study_cash_flow_when_selected() -> None:
    facts = resolve_reserve_funding_facts(
        funding_source="reserve_study_cash_flow",
        manual_annual_amount=None,
        budget_line_items=[
            LineItem(label="Monthly Contribution to Reserve", amount=Decimal("824414")),
        ],
        reserve_funding_plan_rows=[
            ReserveFundingPlanRow(year=2025, annual_contribution=Decimal("834312")),
            ReserveFundingPlanRow(year=2026, annual_contribution=Decimal("850998")),
        ],
        component_annual_provision=Decimal("558381"),
        units=279,
        fiscal_year=2026,
    )

    assert facts.source == "reserve_study_cash_flow"
    assert facts.annual_contribution == Decimal("850998")
    assert facts.monthly_total == Decimal("70916.50")


def test_reserve_funding_component_fallback_is_explicit_warning() -> None:
    facts = resolve_reserve_funding_facts(
        funding_source=None,
        manual_annual_amount=None,
        budget_line_items=[],
        reserve_funding_plan_rows=[],
        component_annual_provision=Decimal("558381"),
        units=279,
        fiscal_year=2026,
    )

    assert facts.source == "component_annual_provision"
    assert facts.annual_contribution == Decimal("558381")
    assert any("component annual provision" in warning for warning in facts.warnings)


def test_assessment_override_computes_approved_annual_revenue_and_warning() -> None:
    facts = resolve_assessment_facts(
        budget_line_items=[
            LineItem(label="Member assessments", amount=Decimal("2025540"), is_revenue=True)
        ],
        approved_monthly_assessment_per_unit=Decimal("606.97"),
        units=279,
    )

    assert facts.source == "manual_monthly_override"
    assert facts.uploaded_annual_assessment_revenue == Decimal("2025540")
    assert facts.approved_annual_assessment_revenue == Decimal("2032135.56")
    assert facts.monthly_assessment_per_unit_current == Decimal("606.97")
    assert facts.revenue_mismatch == Decimal("6595.56")
    assert any("differs" in warning for warning in facts.warnings)


def test_normalize_packet_archetype_defaults_and_accepts_legacy_values() -> None:
    assert normalize_packet_archetype(None) == "dual-fund"
    assert normalize_packet_archetype("") == "dual-fund"
    assert normalize_packet_archetype("dual_fund") == "dual-fund"
    assert normalize_packet_archetype("dual-fund") == "dual-fund"
    assert normalize_packet_archetype("reserve_only") == "reserve-only"
    assert normalize_packet_archetype("reserve-only") == "reserve-only"


def test_assessment_presentation_facts_for_fixed_summary() -> None:
    facts = resolve_assessment_presentation_facts(
        recipient_grain="summary",
        monthly_assessment_per_unit_current=Decimal("605.00"),
        monthly_assessment_per_unit_prior=Decimal("590.00"),
    )

    assert facts.mode == "fixed"
    assert facts.assessments_vary is False
    assert facts.should_show_single_monthly_amount is True
    assert facts.assessment_change_phrase == "will increase to"


def test_assessment_presentation_facts_for_variable_matrix() -> None:
    facts = resolve_assessment_presentation_facts(
        recipient_grain="group",
        monthly_assessment_per_unit_current=Decimal("605.00"),
        monthly_assessment_per_unit_prior=Decimal("590.00"),
    )

    assert facts.mode == "variable"
    assert facts.assessments_vary is True
    assert facts.should_show_single_monthly_amount is False
    assert facts.assessment_change_phrase == "assessments vary by ownership interest"
