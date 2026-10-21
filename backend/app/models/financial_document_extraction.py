"""Canonical contracts for multi-format financial document extraction."""
from __future__ import annotations

from typing import Any, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from ..services.income_statement_parser import SECTION_KINDS

# What an income row is. Gemini reads it from the document; the disclosure
# package takes regular_assessment rows as the regular assessment instead of
# guessing from the label (parking, commercial and special lines carry the
# word "assessment" too).
IncomeRoleWire = Literal[
    "regular_assessment", "special_assessment", "other_assessment", "interest", "other_income"
]
INCOME_ROLE_RULES = (
    "income_role, for income rows only (null on expense rows):\n"
    "- regular_assessment: the regular dues every owner pays, e.g. 'Assessments', "
    "'Assessment Income', 'Monthly Dues', 'Regular Assessments', and parts of them such as "
    "operating/reserve or pro-rata/equal assessments.\n"
    "- special_assessment: a special assessment.\n"
    "- other_assessment: a charge named assessment that is not the regular dues, e.g. "
    "'Parking Garage Assessment Income', 'Parking assessments', 'Commercial assessment income', "
    "storage assessments.\n"
    "- interest: interest earned.\n"
    "- other_income: anything else, e.g. late fees, rentals, reimbursements, laundry, "
    "'Parking Space Income'."
)


def _known_income_role(value: Any) -> Any:
    # An unexpected tag must not fail the page; the package falls back to the label.
    return value if value in get_args(IncomeRoleWire) else None


class FinancialDocumentRoute(BaseModel):
    """Centralized route decision for uploaded financial documents."""

    path: Literal["excel_deterministic", "pdf_vlm"]
    family: Optional[str] = None
    reason: str
    is_supported: bool = True
    requires_review_on_low_confidence: bool = True


class ExtractedStatementLineItem(BaseModel):
    """Canonical extracted line item shared by deterministic and VLM paths."""

    account_code_text: Optional[str] = None
    label: str = Field(min_length=1)
    section_label: Optional[str] = None
    section_kind: Optional[str] = None
    current_actual: Optional[float] = None
    current_budget: Optional[float] = None
    current_variance: Optional[float] = None
    ytd_actual: Optional[float] = None
    ytd_budget: Optional[float] = None
    ytd_variance: Optional[float] = None
    annual_budget: Optional[float] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    evidence: Optional[dict[str, Any]] = None
    # Kept out of the schema Gemini reads PDF pages with: asking for it there
    # slowed scanned pages past their timeout. PDFs get it from one separate
    # call (IncomeRowRoles); Excel answers it in ExcelSourceLine.
    income_role: SkipJsonSchema[Optional[IncomeRoleWire]] = None

    _income_role_is_known = field_validator("income_role", mode="before")(_known_income_role)

    @field_validator("section_kind", mode="before")
    @classmethod
    def _normalize_section_kind(cls, v: Any) -> Optional[str]:
        """Catch section_kind drift at the API boundary.

        Accepts one of SECTION_KINDS or None. Silently coerces legacy
        3-value "reserve" → None for transitional compatibility. Raises
        ValueError on any other string so bad Gemini outputs fail fast.
        """
        if v is None or v in SECTION_KINDS:
            return v
        if v == "reserve":  # legacy 3-value taxonomy
            return None
        raise ValueError(
            f"section_kind must be one of {SECTION_KINDS} or null, got {v!r}"
        )


class ExtractedFinancialStatement(BaseModel):
    """Canonical extracted statement shape before downstream normalization.

    A valid financial statement extraction must contain a meaningful number of
    line items. Single-row or empty extractions indicate a failed parse, not a
    real statement.
    """

    model_config = ConfigDict(extra="forbid")

    document_family: str = Field(min_length=1)
    report_type: str = Field(min_length=1)
    statement_period: Optional[str] = None
    line_items: list[ExtractedStatementLineItem] = Field(
        default_factory=list,
        min_length=2,
        description="Every detail row from the statement. Must include all visible line items, not a summary.",
    )
    totals: list[dict[str, Any]] = Field(default_factory=list)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Free-form metadata about HOW this extraction was produced. Populated
    # by extractors that take a degraded path (e.g. vision-only fallback for
    # scanned PDFs) so that downstream code can surface a quality warning to
    # the end user. NOT used by the validator — purely informational.
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedFinancialStatementPage(BaseModel):
    """Per-page extraction result — no min_length on line_items.

    Used during parallel per-page extraction where a single page may have
    0 or 1 items. The min_length=2 check runs on the merged result instead.
    """

    model_config = ConfigDict(extra="forbid")

    document_family: str = Field(min_length=1)
    report_type: str = Field(min_length=1)
    statement_period: Optional[str] = None
    line_items: list[ExtractedStatementLineItem] = Field(default_factory=list)
    totals: list[dict[str, Any]] = Field(default_factory=list)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DocumentExtractionFailure(BaseModel):
    """Structured failure contract for review-needed document extraction."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Gemini wire schemas for budget-source uploads. Per the structured-output
# contract (CLAUDE.md): every field required, Optional[X] for nullables, no
# defaults. Field descriptions are sent to Gemini with the schema.
# ---------------------------------------------------------------------------

SectionKindWire = Literal["income", "operating", "reserve_income", "reserve_expense"]
BudgetSourceKind = Literal["income_statement", "final_budget", "other"]


class WireModel(BaseModel):
    """Base for Gemini answers. Despite the schema's "required", Gemini leaves
    out null fields of nested objects now and then (seen on the VM key,
    2026-10-21); read a missing key as null instead of rejecting the answer.
    A missing non-nullable value (a row's label) still fails validation."""

    @model_validator(mode="before")
    @classmethod
    def _missing_keys_are_null(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {name: None for name in cls.model_fields} | data
        return data


class StatedTotal(WireModel):
    """A total row printed on the statement, used to check the detail rows."""

    label: str
    section_kind: Optional[SectionKindWire] = Field(
        description="The section whose rows this total adds up; null for totals spanning sections (net income, total expenses incl. reserves).",
    )
    source_row: Optional[int] = Field(description="Worksheet row, or PDF page number, of the total.")
    annual_budget: Optional[float]
    ytd_actual: Optional[float]


class DisclosureFact(WireModel):
    printed: Optional[str] = Field(description="The value exactly as printed.")
    number: Optional[float] = Field(description="Numeric value; percentages as numbers like 2.5.")
    applies_to_year: Optional[int] = Field(description="The fiscal year the value is for.")
    as_of_date: Optional[str] = Field(description="YYYY-MM-DD, for balances.")
    page: Optional[int] = Field(description="PDF page number or worksheet row where it is printed.")
    quote: Optional[str] = Field(description="The printed words around the value, at most 20 words.")


class AssessmentIncrease(WireModel):
    start_year: Optional[int]
    end_year: Optional[int]
    rate_percent: Optional[float] = Field(description="Annual increase in percent, e.g. 2 or 1.5.")


class DisclosureFacts(WireModel):
    """Facts an HOA disclosure package states; never computed.

    Every fact is an object (printed/number null when not printed): a nullable
    object let Gemini answer with a bare value, which failed the whole reading.
    """

    budget_fiscal_year: Optional[int] = Field(description="The fiscal year this budget or package is for.")
    monthly_assessment_per_unit: DisclosureFact = Field(
        description="Regular assessment per unit per month for the budget year (if amounts vary by unit, the average or most common amount, saying so in quote).",
    )
    monthly_assessment_per_unit_prior_year: DisclosureFact = Field(
        description="The same figure for the year before the budget year.",
    )
    reserve_portion_of_monthly_assessment: DisclosureFact = Field(
        description="The part of the monthly assessment per unit that goes to reserves, for the budget year.",
    )
    reserve_balance_start_of_budget_year: DisclosureFact = Field(
        description="Reserve fund cash projected or reported at the start of the budget year (end of the prior year).",
    )
    percent_funded: DisclosureFact
    reserve_study_preparer: DisclosureFact = Field(description="Company that prepared the reserve study.")
    reserve_study_date: DisclosureFact
    cpa_firm_name: DisclosureFact = Field(description="Accountants who compiled the forecast or report.")
    reserve_interest_rate: DisclosureFact = Field(description="Assumed interest / earnings rate on reserve funds.")
    reserve_interest_rate_basis: Optional[Literal["before_tax", "after_tax"]] = Field(
        description="Whether that rate is stated before or after tax; null if not stated.",
    )
    planned_assessment_increases: list[AssessmentIncrease] = Field(
        description="The funding plan's future assessment increases by year range, as printed; empty if none.",
    )
    outstanding_loan_balance: DisclosureFact = Field(
        description="Outstanding loan balance; null when the package says there is no loan or does not mention one.",
    )
    outstanding_loan_lender: Optional[str]

    @field_validator(
        "monthly_assessment_per_unit", "monthly_assessment_per_unit_prior_year",
        "reserve_portion_of_monthly_assessment", "reserve_balance_start_of_budget_year",
        "percent_funded", "reserve_study_preparer", "reserve_study_date", "cpa_firm_name",
        "reserve_interest_rate", "outstanding_loan_balance",
        mode="before",
    )
    @classmethod
    def _bare_value_is_the_printed_value(cls, value: Any) -> Any:
        # Gemini sometimes answers a fact with the bare value (2260449.87) or
        # null instead of the object; keep what it said rather than failing
        # the whole reading.
        if isinstance(value, dict):
            return value
        empty = {"printed": None, "number": None, "applies_to_year": None, "as_of_date": None,
                 "page": None, "quote": None}
        if isinstance(value, (int, float, str)) and not isinstance(value, bool):
            return {**empty, "printed": str(value), "number": value if isinstance(value, (int, float)) else None}
        return empty

    @field_validator("planned_assessment_increases", mode="before")
    @classmethod
    def _increases_must_be_a_list(cls, value: Any) -> Any:
        return value if isinstance(value, list) else []


class ExcelSourceLine(WireModel):
    label: str = Field(description="Exactly as written in the cell, keeping spelling, case and any account-number prefix.")
    account_code_text: Optional[str]
    section_kind: Optional[SectionKindWire]
    source_sheet: Optional[str]
    source_row: Optional[int]
    current_actual: Optional[float]
    current_budget: Optional[float]
    ytd_actual: Optional[float]
    ytd_budget: Optional[float]
    annual_budget: Optional[float]
    income_role: Optional[IncomeRoleWire] = Field(description=INCOME_ROLE_RULES)

    _income_role_is_known = field_validator("income_role", mode="before")(_known_income_role)


class IncomeRowRole(WireModel):
    row: int = Field(description="The row's number in the list.")
    income_role: Optional[IncomeRoleWire] = Field(description=INCOME_ROLE_RULES)

    _income_role_is_known = field_validator("income_role", mode="before")(_known_income_role)


class IncomeRowRoles(WireModel):
    """Gemini's roles for the income rows of a statement read from a PDF."""

    rows: list[IncomeRowRole]


class ExcelSourceExtraction(WireModel):
    """What Gemini returns for an uploaded budget-source workbook."""

    document_kind: BudgetSourceKind
    kind_evidence: str
    association_name: Optional[str]
    primary_sheet: Optional[str]
    statement_period: Optional[str]
    period_end: Optional[str] = Field(description="YYYY-MM-DD, last date covered by the actual / year-to-date columns; null for a budget.")
    months_covered: Optional[int] = Field(description="Months in the year-to-date columns; null for a budget.")
    fiscal_year: Optional[int]
    line_items: list[ExcelSourceLine]
    stated_totals: list[StatedTotal]
    facts: Optional[DisclosureFacts] = Field(description="Always an object; each fact's printed/number are null when not printed.")

    @field_validator("facts", mode="before")
    @classmethod
    def _unreadable_facts_are_dropped(cls, value: Any) -> Any:
        # Facts are a bonus: a malformed facts block must not lose the budget lines.
        if value is None:
            return None
        try:
            return DisclosureFacts.model_validate(value)
        except ValidationError:
            return None
