"""Pydantic schemas mirroring Prompt 1's output JSON.

These models are the defense-in-depth layer for Gemini output: every
field declared here is enforced at parse time. Extra keys are silently
dropped (so prompts can ship with experimental fields without breaking
validation), and missing required keys produce ``ValidationError`` —
the caller then triggers the single repair-retry path.

Field names match the prompt's JSON keys verbatim. Internal data model
mapping (``setup_type='fixed_equal'`` → ``'fixed'`` etc.) happens in
``adapter.py``, NOT here — keep this layer model-output-shaped.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


_PROMPT_VOCAB_CONFIG = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _coerce_dict_to_string(value: Any) -> Any:
    """Pydantic-permissive: accept either a string, dict, or ``None`` and
    flatten to a single string. Gemini sometimes returns structured objects
    in fields the prompt specs as strings (e.g. ``preparer`` as
    ``{"name": "X", "address": "Y"}`` instead of ``"X — Y"``), and for
    legacy DREs where the preparer / location isn't stated it returns
    ``null``. Both shapes coerce to a string so the strict ``str`` field
    validates without raising — empty string is the same as "not stated".
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        return "; ".join(
            f"{k}: {v}" for k, v in value.items() if v not in (None, "")
        )
    return value


PromptSetupType = Literal[
    "fixed_equal",
    "grouped_category",
    "individual_unit",
    "multi_pool_combination",
    "unknown_needs_review",
]

PromptAllocationMethod = Literal[
    "equal",
    "square_footage",
    "ownership_percentage",
    "category",
    "specified_value",
    "parking_space",
    "custom_factor",
    "unknown",
]

PromptBudgetLineDerivation = Literal[
    "explicit_lines",
    "residual_default",
    "formula_only",
    "unknown",
]

PromptBudgetLineAssessmentType = Literal[
    "equal_base",
    "prorated_variable",
    "square_footage",
    "ownership_percent",
    "exemption_credit",
    "subsidy_credit",
    "pass_through",
    "reserve_component",
    "excluded_or_informational",
    "unknown_needs_review",
]


class DocumentMetadata(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    association_name: str = ""
    document_title: str = ""
    dre_file_number: str = ""
    document_date: str = ""
    preparer: str = ""
    location: str = ""
    total_units: Optional[int] = None
    confidence: float = 0.0
    source_pages: list[int] = Field(default_factory=list)

    # Permissive: Gemini sometimes returns structured objects for
    # ``preparer`` even though the prompt asks for a string.
    @field_validator("preparer", "location", "document_title", mode="before")
    @classmethod
    def _flatten_dict_to_string(cls, value: Any) -> Any:
        return _coerce_dict_to_string(value)


class PageInventoryEntry(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    page_number: int
    page_type: str
    confidence: float = 0.0
    notes: str = ""


class AssessmentSetupBlock(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    setup_type: PromptSetupType
    display_mode: str = ""
    summary: str = ""
    requires_dre_for_future_years: bool = True
    confidence: float = 0.0
    source_pages: list[int] = Field(default_factory=list)


class GroupRow(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    group_id: str = ""
    label: str = ""
    unit_count: Optional[int] = None
    average_square_feet: Optional[Decimal] = None
    ownership_percent: Optional[Decimal] = None
    factor: Optional[Decimal] = None
    source_page: Optional[int] = None
    confidence: float = 0.0


class UnitPoolFactor(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    pool_key: str = ""
    factor_value: Optional[Decimal] = None
    factor_label: str = ""
    factor_type: str = "raw_factor"
    source_page: Optional[int] = None


class UnitRow(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    unit_number: str = ""
    square_feet: Optional[Decimal] = None
    ownership_percent: Optional[Decimal] = None
    category: str = ""
    residential_commercial_flag: str = ""
    parking_flag: str = ""
    source_page: Optional[int] = None
    confidence: float = 0.0
    pool_factors: list[UnitPoolFactor] = Field(default_factory=list)


class UnitStructure(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    unit_count: Optional[int] = None
    group_count: Optional[int] = None
    groups: list[GroupRow] = Field(default_factory=list)
    units: list[UnitRow] = Field(default_factory=list)


class AllocationPoolBlock(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    pool_key: str
    parent_pool_key: str = ""
    pool_name: str = ""
    annual_amount: Optional[Decimal] = None
    monthly_amount: Optional[Decimal] = None
    allocation_method: PromptAllocationMethod
    recipient_scope: str = ""
    denominator_label: str = ""
    denominator_value: Optional[Decimal] = None
    denominator_source: Literal["dre_shown", "calculated", "unknown"] = "unknown"
    included_budget_lines: list[str] = Field(default_factory=list)
    excluded_budget_lines: list[str] = Field(default_factory=list)
    budget_line_derivation: PromptBudgetLineDerivation = "unknown"
    residual_after_pool_keys: list[str] = Field(default_factory=list)
    residual_exclusions: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    confidence: float = 0.0


class FormulaBlock(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    formula_name: str = ""
    formula_expression: str = ""
    example_from_dre: str = ""
    source_page: Optional[int] = None
    confidence: float = 0.0


class ReserveSetupBlock(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    reserve_contribution: Optional[Decimal] = None
    reserve_beginning_balance: Optional[Decimal] = None
    inflation_assumption: Optional[Decimal] = None
    interest_assumption: Optional[Decimal] = None
    allocation_method: str = ""
    source_pages: list[int] = Field(default_factory=list)
    confidence: float = 0.0


class BudgetLineMappingEvidence(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    account_code: Optional[str] = None
    source_label: str = ""
    parent_category: str = ""
    assessment_pool_key: str = ""
    assessment_type: PromptBudgetLineAssessmentType = "unknown_needs_review"
    match_confidence: float = 0.0
    review_required: bool = False
    review_reason: str = ""
    source_page: Optional[int] = None
    source_evidence_text: str = ""


class ValidationCheck(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    check_name: str = ""
    status: Literal["pass", "fail", "warning", "not_applicable"]
    details: str = ""
    source_pages: list[int] = Field(default_factory=list)


class HumanReviewQuestion(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    question: str = ""
    reason: str = ""
    source_pages: list[int] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"] = "medium"


class RecommendedSavedSetup(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    assessment_setup_type: str = ""
    display_mode: str = ""
    required_manual_fields: list[str] = Field(default_factory=list)
    required_budget_line_mappings: list[str] = Field(default_factory=list)
    notes: str = ""

    # Permissive: Gemini sometimes returns mapping entries as objects
    # ``{"budget_line": "Paint", "pool": "variable_costs"}`` instead of
    # the prompt-specified string "Paint -> variable_costs". Flatten to
    # the operator-readable form so the schema still validates.
    @field_validator(
        "required_manual_fields", "required_budget_line_mappings", mode="before",
    )
    @classmethod
    def _flatten_list_of_dicts(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        flattened = []
        for item in value:
            if isinstance(item, dict):
                # Common shapes: {budget_line, pool} or {field, reason}
                if "budget_line" in item and "pool" in item:
                    flattened.append(f"{item['budget_line']} -> {item['pool']}")
                elif "field" in item:
                    flattened.append(str(item["field"]))
                else:
                    flattened.append(
                        "; ".join(f"{k}: {v}" for k, v in item.items() if v)
                    )
            else:
                flattened.append(item)
        return flattened


class DRESetupExtraction(BaseModel):
    """Top-level Prompt 1 output. The defense-in-depth layer validates
    every Gemini response into this shape before any downstream code
    consumes it.
    """

    model_config = _PROMPT_VOCAB_CONFIG

    document_metadata: DocumentMetadata
    page_inventory: list[PageInventoryEntry] = Field(default_factory=list)
    assessment_setup: AssessmentSetupBlock
    unit_structure: UnitStructure = Field(default_factory=UnitStructure)
    allocation_pools: list[AllocationPoolBlock] = Field(default_factory=list)
    formulas: list[FormulaBlock] = Field(default_factory=list)
    reserve_setup: Optional[ReserveSetupBlock] = None
    budget_line_mapping_evidence: list[BudgetLineMappingEvidence] = Field(
        default_factory=list
    )
    validation_checks: list[ValidationCheck] = Field(default_factory=list)
    human_review_questions: list[HumanReviewQuestion] = Field(default_factory=list)
    recommended_saved_setup: Optional[RecommendedSavedSetup] = None


# -- Prompt 2 output -------------------------------------------------------


class MappedBudgetLine(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    budget_line_id: str = ""
    description: str = ""
    amount_annual: Optional[Decimal] = None
    suggested_pool_key: str = ""
    mapping_confidence: float = 0.0
    reason: str = ""


class UnmappedBudgetLine(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    budget_line_id: str = ""
    description: str = ""
    amount_annual: Optional[Decimal] = None
    reason: str = ""
    suggested_question: str = ""


class PoolTotalRow(BaseModel):
    model_config = _PROMPT_VOCAB_CONFIG

    pool_key: str
    annual_total: Optional[Decimal] = None
    monthly_total: Optional[Decimal] = None


class BudgetPoolMapping(BaseModel):
    """Top-level Prompt 2 output."""

    model_config = _PROMPT_VOCAB_CONFIG

    mapped_budget_lines: list[MappedBudgetLine] = Field(default_factory=list)
    unmapped_lines: list[UnmappedBudgetLine] = Field(default_factory=list)
    pool_totals: list[PoolTotalRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
