"""Pydantic input contracts for the Phase 11 disclosure-package compiler.

CONTEXT D-03/D-04: this module is the *only* compiler-side contract; adapters
in `adapters.py` translate from existing service shapes (BudgetHistoryRecord,
ReserveStudyExtraction, …) into these types. The compiler never imports
service-specific types directly — that boundary is what lets Phase 12+ swap
in new HOAs without touching schemas or formulas.

CONTEXT D-06 / threat T-11-04: every currency field is `Decimal`, NEVER float.
Pydantic v2 coerces str/int/float inputs at the schema boundary, which is the
single chokepoint for keeping float arithmetic out of the calc engine.

Plan 11-06 added the API-facing DTOs at the bottom of the file:
    * `DisclosurePackageJobResponse` — GET /status response shape.
    * `GenerateDisclosurePackageRequest` — POST /generate request shape.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────────────
# Input data DTOs (mirror existing service shapes via adapters.py)
# ─────────────────────────────────────────────────────────────────────────────


class LineItem(BaseModel):
    """One row from the parsed budget. Tolerant of extra Phase 7 metadata fields."""

    # extra=allow because BudgetHistoryRecord line items carry extra columns
    # (account_code, percent_change, …) that the adapter does not strip.
    model_config = ConfigDict(extra="allow")

    label: str
    amount: Decimal
    section: Optional[str] = None
    category: Optional[str] = None
    is_reserve: bool = False
    is_revenue: bool = False
    read_only: bool = False


class BudgetDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_items: List[LineItem] = Field(min_length=1)


class ReserveStudyComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_item: str = Field(min_length=1)
    useful_life: int = Field(ge=1)
    remaining_life: int = Field(ge=0)
    replacement_cost: Decimal = Field(ge=Decimal("0"))
    year_new: Optional[int] = Field(default=None, ge=1900, le=3000)


class ReserveFundingPlanRow(BaseModel):
    """One fiscal-year row from a reserve-study cash-flow funding plan."""

    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=1900, le=3000)
    beginning_balance: Optional[Decimal] = None
    annual_contribution: Optional[Decimal] = None
    monthly_per_unit: Optional[Decimal] = None
    interest_income: Optional[Decimal] = None
    reserve_expenditures: Optional[Decimal] = None
    ending_balance: Optional[Decimal] = None
    fully_funded_balance: Optional[Decimal] = None
    percent_funded: Optional[Decimal] = None
    source_page: Optional[int] = Field(default=None, ge=1)

    def consistency_warnings(self, *, units: int) -> list[str]:
        """Return soft validation warnings for extracted funding-plan math."""
        warnings: list[str] = []
        if (
            self.annual_contribution is not None
            and self.monthly_per_unit is not None
            and units > 0
        ):
            if self.annual_contribution > 0 and self.monthly_per_unit > 0:
                implied_units = (
                    self.annual_contribution / self.monthly_per_unit / Decimal(12)
                )
                nearest_units = implied_units.quantize(Decimal("1"))
                if abs(implied_units - nearest_units) <= Decimal("0.05") and int(
                    nearest_units
                ) != units:
                    warnings.append(
                        f"Reserve funding row {self.year} implies "
                        f"{int(nearest_units)} reserve-study units from annual "
                        "contribution and monthly per-unit amount, but HOA "
                        f"assessment unit count is {units}."
                    )
            expected = (
                self.annual_contribution / Decimal(units) / Decimal(12)
            ).quantize(Decimal("0.01"))
            if expected != self.monthly_per_unit.quantize(Decimal("0.01")):
                warnings.append(
                    f"Reserve funding row {self.year} monthly per-unit amount "
                    "does not match annual contribution divided by units and 12."
                )
        if (
            self.beginning_balance is not None
            and self.annual_contribution is not None
            and self.interest_income is not None
            and self.reserve_expenditures is not None
            and self.ending_balance is not None
        ):
            expected_ending = (
                self.beginning_balance
                + self.annual_contribution
                + self.interest_income
                - self.reserve_expenditures
            ).quantize(Decimal("1"))
            if expected_ending != self.ending_balance.quantize(Decimal("1")):
                warnings.append(
                    f"Reserve funding row {self.year} ending balance does not "
                    "match beginning balance plus contribution and interest "
                    "minus reserve expenditures."
                )
        if (
            self.ending_balance is not None
            and self.fully_funded_balance is not None
            and self.percent_funded is not None
            and self.fully_funded_balance > 0
        ):
            expected_percent = (
                self.ending_balance / self.fully_funded_balance * Decimal("100")
            ).quantize(Decimal("1"))
            if expected_percent != self.percent_funded.quantize(Decimal("1")):
                warnings.append(
                    f"Reserve funding row {self.year} percent funded does not "
                    "match ending balance divided by fully funded balance."
                )
        return warnings


class ReserveStudySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_date: str
    components: List[ReserveStudyComponent] = Field(default_factory=list)
    funding_plan_rows: List[ReserveFundingPlanRow] = Field(default_factory=list)


class HOAMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hoa_id: int
    name: str
    units: int = Field(ge=1)
    fiscal_year_start_month: int = Field(ge=1, le=12)
    fiscal_year_end_month: int = Field(ge=1, le=12)
    tax_id: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    entity_type: Optional[str] = None
    incorporation_year: Optional[int] = None


# ─────────────────────────────────────────────────────────────────────────────
# Package manifest types
# ─────────────────────────────────────────────────────────────────────────────


class GeneratedPage(BaseModel):
    """An entry in `PackageSpec.entries` rendered by the WeasyPrint pipeline."""

    kind: Literal["generated"] = "generated"
    template: str
    page_count_hint: int = Field(ge=1)


class StaticAppendix(BaseModel):
    """An entry in `PackageSpec.entries` merged in from a pre-built PDF."""

    kind: Literal["static"] = "static"
    file: str
    page_count_hint: int = Field(ge=1)


# Plain Union (not discriminated) so list[PackageEntry] in PackageSpec accepts
# either kind by structural shape. Pydantic v2 picks the right model from the
# `kind` literal during validation; we keep the alias for typing only.
PackageEntry = Union[GeneratedPage, StaticAppendix]


class HOAStaticData(BaseModel):
    """Per-HOA hardcoded values that are NOT in the runtime data schemas (D-04).

    Phase 12+ moves these into a database-backed admin form; Phase 11 hardcodes
    them in `package_specs/old_mill.py`.
    """

    model_config = ConfigDict(extra="forbid")

    hoa_legal_name: str
    address_line_1: str
    address_line_2: str
    city: str
    state: str
    zip: str
    management_company: str
    management_company_address: str
    cpa_firm_name: str
    cpa_firm_address: str
    reserve_study_expert_name: str
    assessment_model: Literal["flat", "percentage", "sqft", "category", "hybrid"] = "flat"
    monthly_assessment_per_unit_current: Decimal
    monthly_assessment_per_unit_prior: Decimal
    reserve_cash_balance_eoy_prior: Decimal
    bank_cd_balance_for_interest: Decimal
    income_tax_provision_estimate: Decimal
    interest_rate_after_tax: Decimal
    replacement_cost_increase_rate: Decimal
    # (year_band_start, year_band_end, annual_rate)
    assessment_increase_schedule: List[Tuple[int, int, Decimal]]
    letter_date: str
    letter_signed_by: str
    fund_balance_boy_operations: Decimal = Decimal("0")


class PackageSpec(BaseModel):
    """A jurisdiction-agnostic disclosure-package manifest.

    Phase 11: only `OLD_MILL_2026` is implemented. Phase 12+ adds new HOAs by
    adding new spec modules under `package_specs/`.
    """

    model_config = ConfigDict(extra="forbid")

    hoa_id: int
    fiscal_year: int
    jurisdiction: Literal["california"] = "california"
    static_data: HOAStaticData
    entries: List[PackageEntry] = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# Preflight + audit types
# ─────────────────────────────────────────────────────────────────────────────


class PreflightError(BaseModel):
    field_path: str
    message: str
    severity: Literal["blocking", "warning"] = "blocking"
    code: Optional[str] = None
    affected_value: Optional[Any] = None
    suggested_fix: Optional[str] = None


class FormulaCall(BaseModel):
    """One entry in the per-render audit log (CONTEXT D-07).

    `inputs` and `output` are stored as JSON-serializable values so the log
    survives a `.model_dump_json()` / `model_validate_json()` round-trip
    without losing Decimal precision (Decimals are stringified at log time).
    """

    formula_id: str
    version: int
    inputs: dict[str, Any]
    output: Any
    computed_at: str


class AuditLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_snapshot: dict[str, Any]
    formula_calls: List[FormulaCall]
    started_at: str
    completed_at: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# API-facing DTOs (Plan 11-06)
# ─────────────────────────────────────────────────────────────────────────────


class DisclosurePackageJobResponse(BaseModel):
    """Response shape for GET /api/disclosure-package/{job_id}/status.

    The router constructs this from a `DisclosurePackageJob` ORM row via
    `model_validate(job)` (CONfigDict.from_attributes). Internal-only fields
    (`output_path`, `audit_path`) are surfaced because Plan 11-07's UI uses
    their presence to decide whether to enable the Download button — they
    are not URLs themselves.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    property_id: int
    fiscal_year: int
    status: Literal["pending", "running", "completed", "failed"]
    stage: Optional[str] = None
    error_message: Optional[str] = None
    output_path: Optional[str] = None
    audit_path: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class GenerateDisclosurePackageRequest(BaseModel):
    """Request body for POST /api/disclosure-package/generate."""

    model_config = ConfigDict(extra="forbid")

    hoa_id: int = Field(ge=1)
    fiscal_year: int = Field(ge=1900, le=3000)
