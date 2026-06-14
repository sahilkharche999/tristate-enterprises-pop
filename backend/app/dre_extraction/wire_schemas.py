"""Gemini-facing "wire" Pydantic schemas for constrained-decoding extraction.

These models are passed as ``response_schema`` on ``GenerateContentConfig``
so Gemini's decoder produces strictly-shaped JSON at generation time. They
deliberately mirror the domain models in ``schemas.py`` but with three
critical differences:

1. **No defaults.** Every field is required at the model level. Nullable
   values use ``Optional[X]`` rather than ``: X = default``. The
   ``google-genai`` SDK rejects schemas containing ``Field(default=...)``
   or ``: type = value`` (python-genai issue #699).
2. **No ``ConfigDict(extra="forbid")``.** Pre-flight smoke test confirmed
   that Gemini's API rejects schemas containing ``additionalProperties:
   false``. Wire schemas use the Pydantic default config.
3. **No ``@field_validator``.** Validators run client-side only and are
   not enforced server-side. Field-level normalization (``None`` → ``""``,
   ``Decimal`` coercion, etc.) lives in the ``wire_to_domain`` adapter.

Enum semantic descriptions live in ``Field(description=...)`` strings on
this side of the boundary. Gemini reads these descriptions during
generation as part of the JSON Schema, which makes the schema (rather
than the prompt) the single source of truth for "what each enum value
means and when to pick it".

The ``wire_to_domain`` adapter is the bridge from these models back to
the domain ``DRESetupExtraction`` family — it applies the defaults the
wire schema cannot carry, coerces JSON numerics to ``Decimal`` (printed
form preserved via ``Decimal(str(x))``), and normalizes ``None`` to
``""`` for the legacy text-field-coercion path that
``_coerce_dict_to_string`` used to handle.

The module-level constant ``WIRE_SCHEMA_SHA256`` is the byte-stable
SHA-256 of the full wire schema's JSON Schema representation. It is
persisted alongside ``prompt_sha256`` on every extraction run so audit
can detect schema-source-of-truth drift independently of prompt drift.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


# -- Step-1 classify wire schemas ------------------------------------------


class WirePageInventoryEntry(BaseModel):
    """One page-classification entry — produced by the Step-1 classify call."""

    page_number: int
    page_type: str = Field(
        description=(
            "A short label for what this page contains. Use values like "
            "'cover/general information', 'unit summary', 'annual operating "
            "budget', 'reserve worksheet', 'proration schedule', "
            "'budget assumptions', 'utility worksheet', or 'other'. Pick "
            "the label that best describes the page's primary content."
        )
    )
    confidence: Optional[float] = Field(
        description=(
            "0.0–1.0 confidence in the classification. Use < 0.7 when the "
            "page is faded, handwritten, ambiguous, or shows mixed content."
        )
    )
    notes: Optional[str] = Field(
        description=(
            "Short free-text observation about the page (≤120 chars). "
            "Helpful for operator review."
        )
    )


class WirePageInventoryBatch(BaseModel):
    """Top-level Step-1 (classify) response shape."""

    page_inventory: list[WirePageInventoryEntry]


# -- Enum aliases ----------------------------------------------------------

WireSetupType = Literal[
    "fixed_equal",
    "grouped_category",
    "individual_unit",
    "multi_pool_combination",
    "unknown_needs_review",
]

WireAllocationMethod = Literal[
    "equal",
    "square_footage",
    "ownership_percentage",
    "category",
    "specified_value",
    "parking_space",
    "custom_factor",
    "unknown",
]

WireBudgetLineDerivation = Literal[
    "explicit_lines",
    "residual_default",
    "formula_only",
    "unknown",
]

WireBudgetLineAssessmentType = Literal[
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

WireUnitFactorType = Literal[
    "percent",
    "square_footage",
    "raw_factor",
    "dollar_amount",
]


# -- Step-2 full-extraction wire schemas -----------------------------------


class WireDocumentMetadata(BaseModel):
    """Mirror of ``DocumentMetadata`` (no validators, no defaults)."""

    association_name: Optional[str] = Field(
        description="The HOA / association name as printed on the DRE cover page."
    )
    document_title: Optional[str] = Field(
        description="Title of the DRE document (e.g. 'Budget Worksheet')."
    )
    dre_file_number: Optional[str] = Field(
        description="The DRE file number from the cover page, if printed."
    )
    document_date: Optional[str] = Field(
        description="Date of the document as printed (any format Gemini sees)."
    )
    preparer: Optional[str] = Field(
        description=(
            "Name of the preparer (a person or firm). If both a name and "
            "an address are visible, return them as a single string. If "
            "the document does not state a preparer, return null."
        )
    )
    location: Optional[str] = Field(
        description=(
            "Full property location as a single human-readable string. "
            "Combine the location components printed on this DRE — any "
            "of: street address, city, county, state, ZIP, tract "
            "name/number, access-road descriptions — into a single "
            "comma-separated string, in the order they appear in this "
            "document. Include only the components present; omit blank "
            "fields rather than emitting placeholders. Return null ONLY "
            "when no location data is present anywhere in the document."
        )
    )
    total_units: Optional[int] = Field(
        description="Total residential + commercial unit count, when stated."
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in the extracted metadata."
    )
    source_pages: Optional[list[int]] = Field(
        description=(
            "The page numbers (1-based) used to derive this metadata. "
            "MUST contain at least one entry."
        )
    )


class WireAssessmentSetupBlock(BaseModel):
    """Mirror of ``AssessmentSetupBlock``."""

    setup_type: WireSetupType = Field(
        description=(
            "Which structural pattern this HOA follows. "
            "'fixed_equal': every unit pays the identical dollar amount. "
            "'grouped_category': units are bucketed into named groups "
            "(by floorplan, building, or similar), one amount per group, "
            "amounts may differ across groups. "
            "'individual_unit': each unit has its own row with a "
            "custom dollar amount, no grouping. "
            "'multi_pool_combination': per-unit amounts come from "
            "multiple pools (general common + parking + residential, "
            "etc) added together — typically seen with commercial "
            "components or parking surcharges. "
            "'unknown_needs_review': cannot determine from the document. "
            "Use ONLY as a last resort."
        )
    )
    display_mode: Optional[str] = Field(
        description=(
            "Short description of how the assessment schedule should "
            "render on the disclosure package (e.g. 'fixed', 'grouped', "
            "'per_unit')."
        )
    )
    summary: Optional[str] = Field(
        description="One- or two-sentence operator-readable summary."
    )
    requires_dre_for_future_years: Optional[bool] = Field(
        description=(
            "True if next year's assessment cannot be calculated without "
            "re-reading the DRE (i.e. fixed_equal HOAs whose monthly "
            "amount is board-set rather than budget-derived)."
        )
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in the setup_type classification."
    )
    source_pages: Optional[list[int]] = Field(
        description=(
            "Page numbers used to derive setup_type. MUST contain at "
            "least one entry."
        )
    )


class WireGroupRow(BaseModel):
    """Mirror of ``GroupRow``."""

    group_id: Optional[str] = Field(
        description="Identifier (e.g. 'A', 'PlanI', 'Townhouse')."
    )
    label: Optional[str] = Field(
        description="Operator-readable label, often the same as group_id."
    )
    unit_count: Optional[int] = Field(
        description="Number of units in this group."
    )
    average_square_feet: Optional[Decimal] = Field(
        description="Average square footage per unit in this group."
    )
    ownership_percent: Optional[Decimal] = Field(
        description="Group's share of ownership (0–100, may be fractional)."
    )
    factor: Optional[Decimal] = Field(
        description="Allocation factor (e.g. 1.0, 1.15) where applicable."
    )
    source_page: Optional[int] = Field(
        description="Page number where this group's row appears. Required."
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in this row's extraction."
    )


class WireUnitPoolFactor(BaseModel):
    """Per-unit factor used by one prorated allocation pool.

    A unit may carry one entry per pool when the DRE has multiple
    prorated columns (e.g. one per-pool assessment-interest column).
    Empty list for DREs that use a single ``ownership_percent`` (the
    common case).
    """

    pool_key: str = Field(
        description=(
            "snake_case key matching the allocation_pool whose math "
            "this factor drives."
        )
    )
    factor_value: Optional[Decimal] = Field(
        description="The per-unit factor value as printed in the DRE."
    )
    factor_label: Optional[str] = Field(
        description=(
            "The DRE's own label for the column the factor comes from, "
            "verbatim (e.g. the printed column header)."
        )
    )
    factor_type: WireUnitFactorType = Field(
        description=(
            "percent: column shows per-unit ownership / interest "
            "values that, across all participating units, sum to "
            "either 1.0 (decimal-fraction form) OR 100 "
            "(percentage-points form). Store the value EXACTLY as "
            "printed — do not convert between forms; the consumer "
            "uses magnitude to display the value as a percentage. "
            "square_footage: column is sqft-based. "
            "raw_factor: dimensionless multiplier (e.g. 1.0, 1.15) "
            "with no % or sqft connotation. "
            "dollar_amount: column shows a flat per-unit dollar amount, "
            "often used for specified_value pools or special-line "
            "columns (unfunded-liability, capital-contribution)."
        )
    )
    source_page: Optional[int] = Field(
        description="Page where this factor value appears. Required."
    )


class WireUnitRow(BaseModel):
    """Mirror of ``UnitRow``."""

    unit_number: Optional[str] = Field(
        description="The unit number / identifier as printed on the DRE."
    )
    square_feet: Optional[Decimal] = Field(
        description="Square footage for this individual unit."
    )
    ownership_percent: Optional[Decimal] = Field(
        description="This unit's ownership share (0–100, may be fractional)."
    )
    category: Optional[str] = Field(
        description=(
            "Free-text category label (e.g. 'plan_i', 'commercial')."
        )
    )
    residential_commercial_flag: Optional[str] = Field(
        description="'residential' or 'commercial' when stated."
    )
    parking_flag: Optional[str] = Field(
        description=(
            "Indicator of whether the row is a parking space or includes "
            "parking; free-text values like 'yes' / 'no' / 'parking'."
        )
    )
    source_page: Optional[int] = Field(
        description="Page number where this unit's row appears. Required."
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in this row's extraction."
    )
    pool_factors: Optional[list[WireUnitPoolFactor]] = Field(
        description=(
            "Per-pool factor values for this unit. Populate this list "
            "when the DRE's per-unit assessment schedule shows multiple "
            "factor columns per unit (one allocation-interest column "
            "per prorated pool, square-footage column for one pool and "
            "ownership column for another, etc.). Empty list when the "
            "DRE uses a single ownership_percent. Do NOT duplicate the "
            "value of ownership_percent into this list — pick one or "
            "the other based on the DRE's actual layout."
        )
    )


class WireUnitStructure(BaseModel):
    """Mirror of ``UnitStructure``."""

    unit_count: Optional[int] = Field(
        description="Total unit count from the unit summary table."
    )
    group_count: Optional[int] = Field(
        description="Number of distinct groups, if the DRE groups units."
    )
    groups: Optional[list[WireGroupRow]] = Field(
        description=(
            "One entry per group (empty list if this is an "
            "individual_unit DRE)."
        )
    )
    units: Optional[list[WireUnitRow]] = Field(
        description=(
            "One entry per unit (empty list when DRE uses groups instead)."
        )
    )


class WireAllocationPoolBlock(BaseModel):
    """Mirror of ``AllocationPoolBlock``."""

    pool_key: str = Field(
        description=(
            "Stable machine-readable key for the pool (snake_case). "
            "For component sub-pools of a larger parent budget section, "
            "use the convention `{parent_key}_{component_method}` "
            "(e.g. `<parent>_prorated`, `<parent>_equal`) and set "
            "parent_pool_key to the shared parent identifier."
        )
    )
    parent_pool_key: Optional[str] = Field(
        description=(
            "When this pool is a component of a larger parent budget "
            "section (e.g. the prorated half of a section that also "
            "has an equal half), set parent_pool_key to the shared "
            "snake_case parent identifier. This lets downstream code "
            "group components back into their parent section without "
            "string-parsing. Leave null for top-level pools that have "
            "no sibling components."
        )
    )
    pool_name: Optional[str] = Field(
        description="Operator-readable pool name (e.g. 'General Common Area')."
    )
    annual_amount: Optional[Decimal] = Field(
        description=(
            "DRE-stated annual budget total for this pool (Decimal). "
            "Use the verbatim DRE value; never auto-correct mismatches."
        )
    )
    monthly_amount: Optional[Decimal] = Field(
        description="DRE-stated monthly budget total for this pool, if printed."
    )
    allocation_method: WireAllocationMethod = Field(
        description=(
            "How costs in this pool are split across units. "
            "'equal': every recipient unit pays the same share. "
            "'square_footage': share is proportional to unit sqft / "
            "total sqft. "
            "'ownership_percentage': share is the DRE-stated ownership %. "
            "'category': share is per the unit's category / group. "
            "'specified_value': each unit has its own per-unit value "
            "explicitly listed in the DRE (no formula). "
            "'parking_space': allocated to parking-space holders only. "
            "'custom_factor': allocation uses a DRE-stated factor that "
            "doesn't match the other methods. "
            "'unknown': allocation method cannot be determined."
        )
    )
    recipient_scope: Optional[str] = Field(
        description=(
            "Who pays this pool (e.g. 'all_units', 'residential_only', "
            "'parking_users'). Free-text — the engine maps to enum at "
            "promotion."
        )
    )
    denominator_label: Optional[str] = Field(
        description=(
            "Label for the denominator used to divide the pool annual "
            "total (e.g. 'Total Sq Ft', '12 spaces', '74 units')."
        )
    )
    denominator_value: Optional[Decimal] = Field(
        description=(
            "DRE-stated denominator value used in the division. Use the "
            "verbatim DRE value even if it disagrees with re-computed "
            "sums. The value MUST match the unit count implied by "
            "recipient_scope — when scope excludes units (residential-"
            "only, commercial-only, parking-only, etc.), the denominator "
            "counts only the included units, NOT total_units."
        )
    )
    denominator_source: Literal["dre_shown", "calculated", "unknown"] = Field(
        description=(
            "'dre_shown': denominator is printed verbatim in the DRE. "
            "'calculated': denominator is implied / computed from other "
            "rows (e.g. sum of unit sqft). "
            "'unknown': method unclear."
        )
    )
    included_budget_lines: Optional[list[str]] = Field(
        description=(
            "Budget-line labels (verbatim) that this pool collects "
            "money to fund."
        )
    )
    excluded_budget_lines: Optional[list[str]] = Field(
        description=(
            "Budget-line labels that this pool explicitly excludes "
            "(rare; usually empty)."
        )
    )
    budget_line_derivation: WireBudgetLineDerivation = Field(
        description=(
            "How this pool's budget-line membership is identified. "
            "explicit_lines: the DRE visibly lists the lines/categories "
            "included in this pool. residual_default: the DRE derives this "
            "pool as remaining/base/equal costs after other pools are "
            "removed from a total. formula_only: the DRE gives an amount or "
            "formula but does not show enough line-level evidence for annual "
            "budget mapping. unknown: the basis is unclear and needs review."
        )
    )
    residual_after_pool_keys: Optional[list[str]] = Field(
        description=(
            "For residual_default pools, list the pool_key values that must "
            "claim explicit/special lines before this residual pool receives "
            "the remaining eligible assessment-funded lines. Null or empty "
            "for non-residual pools."
        )
    )
    residual_exclusions: Optional[list[str]] = Field(
        description=(
            "For residual_default pools, generic categories that must not be "
            "swept into the residual pool without review, such as "
            "income_only, pass_through, reimbursement, special_assessment, "
            "inactive, zero_amount, or already_mapped. Null or empty when "
            "not visible or not applicable."
        )
    )
    source_pages: Optional[list[int]] = Field(
        description=(
            "Page numbers used to derive this pool. MUST contain at "
            "least one entry."
        )
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in this pool's extraction."
    )


class WireFormulaBlock(BaseModel):
    """Mirror of ``FormulaBlock``."""

    formula_name: Optional[str] = Field(
        description="Short name for the formula (e.g. 'variable_assessment')."
    )
    formula_expression: Optional[str] = Field(
        description=(
            "The formula as printed on the DRE, in operator-readable "
            "form (e.g. 'monthly_per_unit = annual_pool / 12 / "
            "unit_count')."
        )
    )
    example_from_dre: Optional[str] = Field(
        description=(
            "A concrete worked example from the DRE document showing "
            "the formula in action."
        )
    )
    source_page: Optional[int] = Field(
        description="Page where this formula is documented. Required."
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in this formula's extraction."
    )


class WireReserveSetupBlock(BaseModel):
    """Mirror of ``ReserveSetupBlock``."""

    reserve_contribution: Optional[Decimal] = Field(
        description="DRE-stated annual reserve contribution amount."
    )
    reserve_beginning_balance: Optional[Decimal] = Field(
        description="DRE-stated reserve starting balance."
    )
    inflation_assumption: Optional[Decimal] = Field(
        description=(
            "DRE-stated annual inflation assumption (e.g. 3.0 for 3%)."
        )
    )
    interest_assumption: Optional[Decimal] = Field(
        description=(
            "DRE-stated annual interest assumption on the reserve "
            "fund (e.g. 1.5 for 1.5%)."
        )
    )
    allocation_method: Optional[str] = Field(
        description=(
            "Free-text describing how reserve contributions are split "
            "across pools / unit groups. When the DRE has per-pool "
            "reserve contributions (each allocation_pool funds its own "
            "reserve components), describe the breakdown here — e.g. "
            "'per-pool: each pool funds its own reserve components'."
        )
    )
    source_pages: Optional[list[int]] = Field(
        description=(
            "Page numbers used to derive reserve setup. MUST contain "
            "at least one entry. When per-pool reserves exist, cite "
            "EVERY page where a per-pool reserve appears."
        )
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in reserve_setup extraction."
    )


class WireBudgetLineMappingEvidence(BaseModel):
    """DRE-visible evidence tying a budget line/category to a pool."""

    account_code: Optional[str] = Field(
        description="Account code printed on the DRE for this line/category, if visible."
    )
    source_label: Optional[str] = Field(
        description="Budget line or category label exactly as printed on the DRE."
    )
    parent_category: Optional[str] = Field(
        description="Parent category exactly as printed, if visible."
    )
    assessment_pool_key: Optional[str] = Field(
        description="pool_key of the allocation pool this evidence supports."
    )
    assessment_type: WireBudgetLineAssessmentType = Field(
        description=(
            "equal_base: regular equal/base pool evidence. "
            "prorated_variable: evidence for a variable/prorated pool. "
            "square_footage: line is tied to a sqft-based pool. "
            "ownership_percent: line is tied to an ownership-% pool. "
            "exemption_credit: exemption/credit, not a regular operating line. "
            "subsidy_credit: developer or subsidy credit, not a regular operating line. "
            "pass_through: reimbursement/pass-through billing. "
            "reserve_component: reserve-only component detail. "
            "excluded_or_informational: not regular operating mapping evidence. "
            "unknown_needs_review: ambiguous; operator must decide."
        )
    )
    match_confidence: Optional[float] = Field(
        description="0.0–1.0 confidence that this DRE evidence supports the pool mapping."
    )
    review_required: Optional[bool] = Field(
        description="True when current-year applicability requires human confirmation."
    )
    review_reason: Optional[str] = Field(
        description="Why this evidence still requires operator review."
    )
    source_page: Optional[int] = Field(
        description="Page where this evidence appears."
    )
    source_evidence_text: Optional[str] = Field(
        description="Short verbatim/paraphrased explanation of what on the page supports this mapping."
    )


class WireValidationCheck(BaseModel):
    """Mirror of ``ValidationCheck``."""

    check_name: Optional[str] = Field(
        description=(
            "Short name for the math check performed (e.g. "
            "'variable_factor_times_sqft_equals_assessment')."
        )
    )
    status: Literal["pass", "fail", "warning", "not_applicable"] = Field(
        description=(
            "'pass': recomputed value matches DRE within tolerance. "
            "'fail': recomputed value disagrees with the DRE — record "
            "the DRE value verbatim and surface the disagreement. "
            "'warning': partial / noisy match. "
            "'not_applicable': check doesn't apply to this DRE setup."
        )
    )
    details: Optional[str] = Field(
        description=(
            "Operator-readable explanation of the check, including the "
            "DRE value vs the recomputed value when they disagree."
        )
    )
    source_pages: Optional[list[int]] = Field(
        description="Page numbers referenced by this check."
    )


class WireHumanReviewQuestion(BaseModel):
    """Mirror of ``HumanReviewQuestion``."""

    question: Optional[str] = Field(
        description="The exact question to ask the human reviewer."
    )
    reason: Optional[str] = Field(
        description="Why this question requires human judgment."
    )
    source_pages: Optional[list[int]] = Field(
        description="Page numbers the reviewer should look at."
    )
    severity: Literal["low", "medium", "high"] = Field(
        description=(
            "'low': cosmetic / nice-to-have clarification. "
            "'medium': must be answered before approval but doesn't "
            "block extraction. "
            "'high': blocks the entire setup — assessment math can't "
            "proceed without an answer."
        )
    )


class WireRecommendedSavedSetup(BaseModel):
    """Mirror of ``RecommendedSavedSetup``."""

    assessment_setup_type: Optional[str] = Field(
        description=(
            "Suggested internal setup_type label (mirrors "
            "WireAssessmentSetupBlock.setup_type)."
        )
    )
    display_mode: Optional[str] = Field(
        description="Suggested display_mode for the disclosure package."
    )
    required_manual_fields: Optional[list[str]] = Field(
        description=(
            "Free-text list of fields the operator must fill in by "
            "hand (one entry per field)."
        )
    )
    required_budget_line_mappings: Optional[list[str]] = Field(
        description=(
            "Free-text list describing required mappings, formatted "
            "as '<budget_line> -> <pool_key>'."
        )
    )
    notes: Optional[str] = Field(
        description=(
            "Free-text notes for the operator about onboarding this "
            "HOA's saved setup."
        )
    )


class WireDRESetupExtraction(BaseModel):
    """Top-level Step-2 (extract) response shape.

    Field declaration order matters for Gemini 2.5+ implicit
    propertyOrdering: metadata first (gives the model an anchor),
    then page_inventory (already known from Step-1 but re-emitted for
    audit), then interpretation (setup_type, units, pools, formulas),
    then derived checks + review questions, then the saved-setup
    recommendation. This sequence is chain-of-thought-friendly: the
    model classifies before it interprets, and interprets before it
    judges.
    """

    document_metadata: WireDocumentMetadata
    page_inventory: list[WirePageInventoryEntry]
    assessment_setup: WireAssessmentSetupBlock
    unit_structure: WireUnitStructure
    allocation_pools: list[WireAllocationPoolBlock]
    formulas: list[WireFormulaBlock]
    reserve_setup: Optional[WireReserveSetupBlock]
    budget_line_mapping_evidence: list[WireBudgetLineMappingEvidence]
    validation_checks: list[WireValidationCheck]
    human_review_questions: list[WireHumanReviewQuestion]
    recommended_saved_setup: Optional[WireRecommendedSavedSetup]


class WireMergeSuggestion(BaseModel):
    """One Gemini-suggested GL merge candidate."""

    primary_account_code: Optional[str] = Field(
        description="Account code of the budget row that should remain."
    )
    secondary_account_code: Optional[str] = Field(
        description="Account code of the budget row that would be hidden."
    )
    confidence: float = Field(
        description="0.0-1.0 confidence that the two rows represent one GL."
    )
    reason: str = Field(
        description="One short operator-facing reason for this suggestion."
    )


class WireMergeSuggestionList(BaseModel):
    """Top-level structured response for GL merge suggestions."""

    suggestions: list[WireMergeSuggestion]


WIRE_SCHEMA_SHA256: str = hashlib.sha256(
    json.dumps(
        WireDRESetupExtraction.model_json_schema(), sort_keys=True
    ).encode("utf-8")
).hexdigest()

WIRE_MERGE_SUGGESTION_SCHEMA_SHA256: str = hashlib.sha256(
    json.dumps(
        WireMergeSuggestionList.model_json_schema(), sort_keys=True
    ).encode("utf-8")
).hexdigest()


__all__ = [
    "WireSetupType",
    "WireAllocationMethod",
    "WireUnitFactorType",
    "WireBudgetLineAssessmentType",
    "WirePageInventoryEntry",
    "WirePageInventoryBatch",
    "WireDocumentMetadata",
    "WireAssessmentSetupBlock",
    "WireGroupRow",
    "WireUnitRow",
    "WireUnitPoolFactor",
    "WireUnitStructure",
    "WireAllocationPoolBlock",
    "WireFormulaBlock",
    "WireReserveSetupBlock",
    "WireBudgetLineMappingEvidence",
    "WireValidationCheck",
    "WireHumanReviewQuestion",
    "WireRecommendedSavedSetup",
    "WireDRESetupExtraction",
    "WireMergeSuggestion",
    "WireMergeSuggestionList",
    "WIRE_SCHEMA_SHA256",
    "WIRE_MERGE_SUGGESTION_SCHEMA_SHA256",
]
