"""Pydantic input contracts for the Phase 11 disclosure-package compiler.

CONTEXT D-03/D-04: this module is the *only* compiler-side contract; adapters
in `adapters.py` translate from existing service shapes (BudgetHistoryRecord,
ReserveStudyExtraction, …) into these types. The compiler never imports
service-specific types directly — that boundary is what lets Phase 12+ swap
in new HOAs without touching schemas or formulas.

CONTEXT D-06 / threat T-11-04: every currency field is `Decimal`, NEVER float.
Pydantic v2 coerces str/int/float inputs at the schema boundary, which is the
single chokepoint for keeping float arithmetic out of the calc engine.
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


class ReserveStudySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_date: str
    components: List[ReserveStudyComponent] = Field(min_length=1)


class HOAMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hoa_id: int
    name: str
    units: int = Field(ge=1)
    fiscal_year_start_month: int = Field(ge=1, le=12)
    fiscal_year_end_month: int = Field(ge=1, le=12)
    tax_id: Optional[str] = None


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
