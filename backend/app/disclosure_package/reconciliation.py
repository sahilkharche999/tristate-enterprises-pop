"""Disclosure reconciliation helpers.

These helpers keep financial source-selection rules out of templates. The
compiler can then render one resolved set of facts instead of re-deciding
which budget, reserve-study, or settings value should win on each page.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .schemas import LineItem, ReserveFundingPlanRow


ReserveFundingSource = Literal[
    "auto",
    "manual",
    "budget_reserve_contribution",
    "reserve_study_cash_flow",
    "component_annual_provision",
    "missing",
]

PacketArchetype = Literal["dual-fund", "reserve-only"]
AssessmentPresentationMode = Literal["fixed", "variable"]


class ReserveFundingFacts(BaseModel):
    """Resolved current-year reserve funding facts for the PDF."""

    model_config = ConfigDict(extra="forbid")

    annual_contribution: Decimal
    monthly_total: Decimal
    monthly_per_unit: Decimal
    source: ReserveFundingSource
    source_label: str
    budget_annual_contribution: Optional[Decimal] = None
    budget_source_label: Optional[str] = None
    study_recommended_annual_contribution: Optional[Decimal] = None
    component_annual_provision: Optional[Decimal] = None
    warnings: list[str] = Field(default_factory=list)


class PacketArchetypeFacts(BaseModel):
    """Resolved accountant-style packet family for financial pages."""

    model_config = ConfigDict(extra="forbid")

    archetype: PacketArchetype
    renders_operations_fund: bool
    renders_replacement_fund: bool = True
    source: str


AssessmentSource = Literal[
    "manual_monthly_override",
    "budget_assessment_revenue",
    "missing",
]


class AssessmentFacts(BaseModel):
    """Resolved regular assessment facts for the current disclosure year."""

    model_config = ConfigDict(extra="forbid")

    uploaded_annual_assessment_revenue: Decimal
    approved_annual_assessment_revenue: Decimal
    monthly_assessment_per_unit_current: Decimal
    source: AssessmentSource
    revenue_mismatch: Decimal
    warnings: list[str] = Field(default_factory=list)


class AssessmentPresentationFacts(BaseModel):
    """Homeowner-facing wording mode derived from approved assessment shape."""

    model_config = ConfigDict(extra="forbid")

    mode: AssessmentPresentationMode
    assessments_vary: bool
    should_show_single_monthly_amount: bool
    assessment_change_phrase: str
    schedule_reference_text: str


class ReserveLiabilityFacts(BaseModel):
    """Canonical reserve liability facts shared by notes and statements."""

    model_config = ConfigDict(extra="forbid")

    cash_reserve_balance_eoy_prior: Decimal
    total_estimated_liability: Decimal
    under_funded_balance_total: Decimal
    under_funded_balance_per_unit: Decimal
    percent_funded: Decimal
    annual_replacement_provision: Decimal


ReserveInterestSource = Literal[
    "manual_override",
    "budget_interest_income",
    "reserve_study_interest_income",
    "missing",
]

ReserveTaxSource = Literal[
    "manual_override",
    "derived_from_interest",
    "missing",
]


class ReserveInterestTaxFacts(BaseModel):
    """Resolved reserve interest income and reserve tax provision."""

    model_config = ConfigDict(extra="forbid")

    reserve_interest_income: Decimal
    reserve_tax_provision: Decimal
    interest_source: ReserveInterestSource
    tax_source: ReserveTaxSource
    warnings: list[str] = Field(default_factory=list)


class AnnualStatementFacts(BaseModel):
    """Canonical current-year statement facts for accountant-style pages."""

    model_config = ConfigDict(extra="forbid")

    packet_archetype: PacketArchetype
    operating_assessment_revenue: Decimal
    reserve_assessment_revenue: Decimal
    reserve_interest_income: Decimal
    other_operating_revenue: Decimal
    other_replacement_revenue: Decimal
    replacement_provision_expense: Decimal
    reserve_tax_provision: Decimal
    total_revenues_operations: Decimal
    total_revenues_replacement: Decimal
    total_revenues: Decimal
    total_expenses_operations: Decimal
    total_expenses_replacement: Decimal
    total_expenses: Decimal
    excess_revenues_over_expenses_operations: Decimal
    excess_revenues_over_expenses_replacement: Decimal
    beginning_balance_operations: Decimal
    beginning_balance_replacement: Decimal
    ending_balance_operations: Decimal
    ending_balance_replacement: Decimal
    ending_balance_total: Decimal


_BUDGET_RESERVE_RE = re.compile(
    r"("
    r"monthly\s+contribution\s+to\s+reserve"
    r"|contribution\s+to\s+reserve"
    r"|reserve\s+contribution"
    r"|reserve\s+funding"
    r"|reserve\s+allocation"
    r"|allocation\s+to\s+reserve"
    r"|transfer\s+to\s+reserve"
    r"|reserve\s+transfer"
    r"|replacement\s+fund\s+contribution"
    r")",
    re.IGNORECASE,
)


def parse_optional_decimal_setting(value: object) -> Optional[Decimal]:
    """Parse an optional numeric setting.

    Blank values mean "not supplied"; numeric zero is a real supplied value.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return Decimal(str(value).strip() if isinstance(value, str) else str(value))
    except (InvalidOperation, ValueError):
        return None


def normalize_reserve_funding_source(value: object) -> ReserveFundingSource:
    """Normalize legacy UI/source values to internal resolver names."""
    raw = str(value or "").strip().lower()
    if not raw:
        return "auto"
    mapping: dict[str, ReserveFundingSource] = {
        "auto": "auto",
        "manual": "manual",
        "budget_allocation_line": "budget_reserve_contribution",
        "budget_reserve_contribution": "budget_reserve_contribution",
        "reserve_study_cash_flow": "reserve_study_cash_flow",
        "reserve_study_provision": "component_annual_provision",
        "component_annual_provision": "component_annual_provision",
    }
    return mapping.get(raw, "auto")


def normalize_packet_archetype(value: object) -> PacketArchetype:
    """Normalize storage/UI variants to canonical packet archetype names."""
    raw = str(value or "").strip().lower()
    if not raw:
        return "dual-fund"
    mapping: dict[str, PacketArchetype] = {
        "dual_fund": "dual-fund",
        "dual-fund": "dual-fund",
        "dualfund": "dual-fund",
        "reserve_only": "reserve-only",
        "reserve-only": "reserve-only",
        "reserveonly": "reserve-only",
    }
    return mapping.get(raw, "dual-fund")


def resolve_packet_archetype_facts(
    *,
    packet_archetype_setting: object,
) -> PacketArchetypeFacts:
    archetype = normalize_packet_archetype(packet_archetype_setting)
    return PacketArchetypeFacts(
        archetype=archetype,
        renders_operations_fund=archetype == "dual-fund",
        renders_replacement_fund=True,
        source="hoa_settings" if str(packet_archetype_setting or "").strip() else "default",
    )


def find_budget_reserve_contribution(
    line_items: Sequence[LineItem],
) -> tuple[Optional[Decimal], Optional[str]]:
    """Find the board-approved reserve contribution in budget rows."""
    matches: list[tuple[Decimal, str]] = []
    for item in line_items:
        label = (item.label or "").strip()
        if not label:
            continue
        if not _BUDGET_RESERVE_RE.search(label):
            continue
        matches.append((Decimal(item.amount or 0), label))
    if not matches:
        return None, None
    amount = sum((m[0] for m in matches), Decimal("0"))
    label = "; ".join(m[1] for m in matches)
    return amount, label


def _study_contribution_for_year(
    rows: Sequence[ReserveFundingPlanRow],
    fiscal_year: int,
) -> Optional[Decimal]:
    for row in rows:
        if row.year == fiscal_year and row.annual_contribution is not None:
            return Decimal(row.annual_contribution)
    return None


def _monthly_total(annual: Decimal) -> Decimal:
    return (annual / Decimal(12)).quantize(Decimal("0.01"))


def _monthly_per_unit(annual: Decimal, units: int) -> Decimal:
    if units <= 0:
        return Decimal("0.00")
    return (annual / Decimal(units) / Decimal(12)).quantize(Decimal("0.01"))


def _current_year_reserve_study_interest_income(
    rows: Sequence[ReserveFundingPlanRow],
    fiscal_year: int,
) -> Optional[Decimal]:
    for row in rows:
        if row.year == fiscal_year and row.interest_income is not None:
            return Decimal(row.interest_income)
    return None


def resolve_assessment_presentation_facts(
    *,
    recipient_grain: Optional[str],
    monthly_assessment_per_unit_current: Decimal,
    monthly_assessment_per_unit_prior: Optional[Decimal],
) -> AssessmentPresentationFacts:
    """Resolve fixed-vs-variable homeowner wording from matrix presentation."""
    grain = (recipient_grain or "").strip().lower()
    if grain in {"group", "unit"}:
        return AssessmentPresentationFacts(
            mode="variable",
            assessments_vary=True,
            should_show_single_monthly_amount=False,
            assessment_change_phrase="assessments vary by ownership interest",
            schedule_reference_text="assessment schedule included in this package",
        )

    current = Decimal(monthly_assessment_per_unit_current or 0).quantize(Decimal("0.01"))
    prior = (
        Decimal(monthly_assessment_per_unit_prior).quantize(Decimal("0.01"))
        if monthly_assessment_per_unit_prior is not None
        else None
    )
    if prior is None or current == Decimal("0.00"):
        phrase = "is"
    elif current == prior:
        phrase = "will remain the same at"
    elif current > prior:
        phrase = "will increase to"
    else:
        phrase = "will decrease to"

    return AssessmentPresentationFacts(
        mode="fixed",
        assessments_vary=False,
        should_show_single_monthly_amount=True,
        assessment_change_phrase=phrase,
        schedule_reference_text="assessment schedule included in this package",
    )


def resolve_reserve_funding_facts(
    *,
    funding_source: object,
    manual_annual_amount: object,
    budget_line_items: Sequence[LineItem],
    reserve_funding_plan_rows: Sequence[ReserveFundingPlanRow],
    component_annual_provision: Decimal,
    units: int,
    fiscal_year: int,
) -> ReserveFundingFacts:
    """Resolve current-year reserve funding from all available sources."""
    source = normalize_reserve_funding_source(funding_source)
    manual = parse_optional_decimal_setting(manual_annual_amount)
    budget, budget_label = find_budget_reserve_contribution(budget_line_items)
    study = _study_contribution_for_year(reserve_funding_plan_rows, fiscal_year)
    component = Decimal(component_annual_provision or 0)
    warnings: list[str] = []

    selected_source: ReserveFundingSource
    selected_amount: Decimal
    source_label: str

    if source == "manual" and manual is not None:
        selected_source = "manual"
        selected_amount = manual
        source_label = "operator-entered manual reserve funding amount"
    elif source == "budget_reserve_contribution" and budget is not None:
        selected_source = "budget_reserve_contribution"
        selected_amount = budget
        source_label = f"approved budget reserve contribution ({budget_label})"
    elif source == "reserve_study_cash_flow" and study is not None:
        selected_source = "reserve_study_cash_flow"
        selected_amount = study
        source_label = f"reserve study cash-flow funding plan for {fiscal_year}"
    elif source == "component_annual_provision" and component > 0:
        selected_source = "component_annual_provision"
        selected_amount = component
        source_label = "reserve component annual provision fallback"
        warnings.append(
            "Reserve funding fell back to component annual provision; verify this "
            "is intended because it may differ from the board-approved budget or "
            "reserve study cash-flow recommendation."
        )
    elif budget is not None:
        selected_source = "budget_reserve_contribution"
        selected_amount = budget
        source_label = f"approved budget reserve contribution ({budget_label})"
    elif study is not None:
        selected_source = "reserve_study_cash_flow"
        selected_amount = study
        source_label = f"reserve study cash-flow funding plan for {fiscal_year}"
    elif component > 0:
        selected_source = "component_annual_provision"
        selected_amount = component
        source_label = "reserve component annual provision fallback"
        warnings.append(
            "Reserve funding fell back to component annual provision; verify this "
            "is intended because it may differ from the board-approved budget or "
            "reserve study cash-flow recommendation."
        )
    else:
        selected_source = "missing"
        selected_amount = Decimal("0")
        source_label = "missing reserve funding source"
        warnings.append(
            "Reserve funding could not be resolved from settings, budget, reserve "
            "study cash-flow rows, or component annual provision."
        )

    if manual is not None and selected_source != "manual" and manual != selected_amount:
        warnings.append("Manual reserve funding amount differs from selected funding source.")
    if budget is not None and selected_source != "budget_reserve_contribution" and budget != selected_amount:
        warnings.append("Budget reserve contribution differs from selected funding source.")
    if study is not None and selected_source != "reserve_study_cash_flow" and study != selected_amount:
        warnings.append("Reserve study cash-flow contribution differs from selected funding source.")

    return ReserveFundingFacts(
        annual_contribution=selected_amount,
        monthly_total=_monthly_total(selected_amount),
        monthly_per_unit=_monthly_per_unit(selected_amount, units),
        source=selected_source,
        source_label=source_label,
        budget_annual_contribution=budget,
        budget_source_label=budget_label,
        study_recommended_annual_contribution=study,
        component_annual_provision=component,
        warnings=warnings,
    )


def resolve_reserve_interest_tax_facts(
    *,
    reserve_interest_income_override: object,
    income_tax_provision_override: object,
    budget_line_items: Sequence[LineItem],
    reserve_funding_plan_rows: Sequence[ReserveFundingPlanRow],
    fiscal_year: int,
) -> ReserveInterestTaxFacts:
    """Resolve reserve interest income + reserve tax provision with overrides."""
    interest_override = parse_optional_decimal_setting(reserve_interest_income_override)
    tax_override = parse_optional_decimal_setting(income_tax_provision_override)
    budget_interest = sum(
        (
            Decimal(item.amount or 0)
            for item in budget_line_items
            if item.is_revenue and item.label and "interest" in item.label.lower()
        ),
        Decimal("0"),
    )
    study_interest = _current_year_reserve_study_interest_income(
        reserve_funding_plan_rows,
        fiscal_year,
    )
    warnings: list[str] = []

    if interest_override is not None:
        interest = interest_override
        interest_source: ReserveInterestSource = "manual_override"
    elif budget_interest != Decimal("0"):
        interest = budget_interest
        interest_source = "budget_interest_income"
    elif study_interest is not None:
        interest = study_interest
        interest_source = "reserve_study_interest_income"
    else:
        interest = Decimal("0")
        interest_source = "missing"
        warnings.append(
            "Reserve interest income could not be resolved from override, budget interest lines, "
            "or reserve-study funding-plan interest."
        )

    if tax_override is not None:
        tax = tax_override.quantize(Decimal("1"))
        tax_source: ReserveTaxSource = "manual_override"
    elif interest_source != "missing":
        tax = (Decimal(interest) * Decimal("0.30")).quantize(Decimal("1"))
        tax_source = "derived_from_interest"
    else:
        tax = Decimal("0")
        tax_source = "missing"
        warnings.append(
            "Reserve income tax provision could not be resolved because reserve interest income is missing "
            "and no operator override was provided."
        )

    return ReserveInterestTaxFacts(
        reserve_interest_income=Decimal(interest).quantize(Decimal("0.01")),
        reserve_tax_provision=tax,
        interest_source=interest_source,
        tax_source=tax_source,
        warnings=warnings,
    )


def resolve_assessment_facts(
    *,
    budget_line_items: Sequence[LineItem],
    approved_monthly_assessment_per_unit: object,
    units: int,
) -> AssessmentFacts:
    """Resolve regular assessment facts and detect override mismatches."""
    uploaded = sum(
        (
            Decimal(item.amount or 0)
            for item in budget_line_items
            if item.is_revenue and item.label and "assessment" in item.label.lower()
        ),
        Decimal("0"),
    )
    override = parse_optional_decimal_setting(approved_monthly_assessment_per_unit)
    warnings: list[str] = []

    if override is not None:
        monthly = override.quantize(Decimal("0.01"))
        approved = (monthly * Decimal(units) * Decimal(12)).quantize(Decimal("0.01"))
        source: AssessmentSource = "manual_monthly_override"
    elif uploaded > 0 and units > 0:
        monthly = (uploaded / Decimal(units) / Decimal(12)).quantize(Decimal("0.01"))
        approved = uploaded
        source = "budget_assessment_revenue"
    else:
        monthly = Decimal("0.00")
        approved = Decimal("0")
        source = "missing"
        warnings.append(
            "Monthly assessment could not be resolved from approved override or "
            "budget assessment revenue."
        )

    mismatch = (approved - uploaded).quantize(Decimal("0.01"))
    if uploaded > 0 and abs(mismatch) > Decimal("1.00"):
        warnings.append(
            "Approved monthly assessment revenue differs from uploaded budget "
            f"assessment revenue by ${mismatch:,.2f}."
        )

    return AssessmentFacts(
        uploaded_annual_assessment_revenue=uploaded,
        approved_annual_assessment_revenue=approved,
        monthly_assessment_per_unit_current=monthly,
        source=source,
        revenue_mismatch=mismatch,
        warnings=warnings,
    )


def resolve_reserve_liability_facts(
    *,
    cash_reserve_balance_eoy_prior: Decimal,
    total_estimated_liability: Decimal,
    under_funded_balance_total: Decimal,
    under_funded_balance_per_unit: Decimal,
    percent_funded: Decimal,
    annual_replacement_provision: Decimal,
) -> ReserveLiabilityFacts:
    """Freeze reserve liability outputs into one canonical structure."""
    return ReserveLiabilityFacts(
        cash_reserve_balance_eoy_prior=Decimal(cash_reserve_balance_eoy_prior or 0),
        total_estimated_liability=Decimal(total_estimated_liability or 0),
        under_funded_balance_total=Decimal(under_funded_balance_total or 0),
        under_funded_balance_per_unit=Decimal(under_funded_balance_per_unit or 0),
        percent_funded=Decimal(percent_funded or 0),
        annual_replacement_provision=Decimal(annual_replacement_provision or 0),
    )


def build_annual_statement_facts(
    *,
    packet_archetype: PacketArchetype,
    total_regular_assessment_revenue: Decimal,
    reserve_assessment_revenue: Decimal,
    reserve_interest_income: Decimal,
    reserve_tax_provision: Decimal,
    other_operating_revenue: Decimal,
    other_replacement_revenue: Decimal,
    total_operating_expenses: Decimal,
    beginning_balance_operations: Decimal,
    reserve_liability_facts: ReserveLiabilityFacts,
) -> AnnualStatementFacts:
    """Build canonical dual-fund or reserve-only statement totals."""
    regular_assessment_total = Decimal(total_regular_assessment_revenue or 0).quantize(Decimal("0.01"))
    reserve_assessment = Decimal(reserve_assessment_revenue or 0).quantize(Decimal("0.01"))
    reserve_interest = Decimal(reserve_interest_income or 0).quantize(Decimal("0.01"))
    reserve_tax = Decimal(reserve_tax_provision or 0).quantize(Decimal("0.01"))
    other_op = Decimal(other_operating_revenue or 0).quantize(Decimal("0.01"))
    other_rep = Decimal(other_replacement_revenue or 0).quantize(Decimal("0.01"))
    total_op_expenses = Decimal(total_operating_expenses or 0).quantize(Decimal("0.01"))
    replacement_provision = Decimal(
        reserve_liability_facts.annual_replacement_provision or 0
    ).quantize(Decimal("0.01"))

    if packet_archetype == "dual-fund":
        operating_assessment = max(
            regular_assessment_total - reserve_assessment,
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        total_rev_op = (operating_assessment + other_op).quantize(Decimal("0.01"))
        total_exp_op = total_op_expenses
        excess_op = (total_rev_op - total_exp_op).quantize(Decimal("0.01"))
        begin_op = Decimal(beginning_balance_operations or 0).quantize(Decimal("0.01"))
        end_op = (begin_op + excess_op).quantize(Decimal("0.01"))
    else:
        operating_assessment = Decimal("0.00")
        total_rev_op = Decimal("0.00")
        total_exp_op = Decimal("0.00")
        excess_op = Decimal("0.00")
        begin_op = Decimal("0.00")
        end_op = Decimal("0.00")

    total_rev_rep = (
        reserve_assessment + reserve_interest + other_rep
    ).quantize(Decimal("0.01"))
    total_exp_rep = (replacement_provision + reserve_tax).quantize(Decimal("0.01"))
    excess_rep = (total_rev_rep - total_exp_rep).quantize(Decimal("0.01"))
    begin_rep = (
        reserve_liability_facts.cash_reserve_balance_eoy_prior
        - reserve_liability_facts.total_estimated_liability
    ).quantize(Decimal("0.01"))
    end_rep = (begin_rep + excess_rep).quantize(Decimal("0.01"))

    total_revenues = (total_rev_op + total_rev_rep).quantize(Decimal("0.01"))
    total_expenses = (total_exp_op + total_exp_rep).quantize(Decimal("0.01"))

    return AnnualStatementFacts(
        packet_archetype=packet_archetype,
        operating_assessment_revenue=operating_assessment,
        reserve_assessment_revenue=reserve_assessment,
        reserve_interest_income=reserve_interest,
        other_operating_revenue=other_op,
        other_replacement_revenue=other_rep,
        replacement_provision_expense=replacement_provision,
        reserve_tax_provision=reserve_tax,
        total_revenues_operations=total_rev_op,
        total_revenues_replacement=total_rev_rep,
        total_revenues=total_revenues,
        total_expenses_operations=total_exp_op,
        total_expenses_replacement=total_exp_rep,
        total_expenses=total_expenses,
        excess_revenues_over_expenses_operations=excess_op,
        excess_revenues_over_expenses_replacement=excess_rep,
        beginning_balance_operations=begin_op,
        beginning_balance_replacement=begin_rep,
        ending_balance_operations=end_op,
        ending_balance_replacement=end_rep,
        ending_balance_total=(end_op + end_rep).quantize(Decimal("0.01")),
    )
