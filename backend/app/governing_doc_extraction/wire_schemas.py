"""Gemini-facing wire schemas for CC&R / governing-document extraction.

Same design contract as ``dre_extraction.wire_schemas``:
  1. Every field is required at the model level; nullables use Optional[X].
  2. No Field(default=...) or ConfigDict(extra="forbid") — both rejected by SDK.
  3. No @field_validator — normalization lives in wire_to_domain.

The top-level schema is WireCCRPolicyExtraction.  The Step-1 page
classification reuses WirePageInventoryEntry and WirePageInventoryBatch
from the DRE wire schema (same shape, different label set).

WIRE_SCHEMA_SHA256 records the byte-stable hash of the JSON Schema
representation so audit can detect schema drift independently of prompt drift.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


# Re-export the Step-1 shapes from DRE wire schemas — identical structure.
from app.dre_extraction.wire_schemas import (
    WirePageInventoryEntry,
    WirePageInventoryBatch,
    WireValidationCheck,
    WireHumanReviewQuestion,
)


# ── CC&R-specific top-level shapes ────────────────────────────────────────


class WireCCRDocumentMetadata(BaseModel):
    """Governing-document header metadata."""

    association_name: Optional[str] = Field(
        description="HOA / association name from the title page or recitals."
    )
    document_title: Optional[str] = Field(
        description=(
            "Title of the document as printed "
            "(e.g. 'Declaration of Covenants, Conditions & Restrictions')."
        )
    )
    recording_reference: Optional[str] = Field(
        description="Recorder's document number, book/page, or instrument number if present."
    )
    document_date: Optional[str] = Field(
        description="Effective or recording date as printed (any format)."
    )
    total_units: Optional[int] = Field(
        description="Total unit count if stated in the document; null otherwise."
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in the extracted metadata."
    )
    source_pages: Optional[list[int]] = Field(
        description="Page numbers used to derive this metadata. MUST contain at least one entry."
    )


CCRSetupType = Literal[
    "fixed_equal",
    "grouped_category",
    "individual_unit",
    "multi_pool_combination",
    "unknown_needs_review",
]

CCRAllocationBasis = Literal[
    "equal",
    "square_footage",
    "ownership_percentage",
    "specified_value",
    "custom_factor",
    "unknown",
]


class WireCCRAssessmentSetupBlock(BaseModel):
    """Structural pattern of the allocation policy."""

    setup_type: CCRSetupType = Field(
        description=(
            "The structural pattern the allocation policy follows. "
            "'fixed_equal': all costs levied equally among all units with no exceptions. "
            "'multi_pool_combination': a default equal/base pool plus one or more "
            "exception pools with a different basis (e.g. square-footage proportional). "
            "'grouped_category': costs vary by named owner group or unit type. "
            "'individual_unit': each unit has its own schedule in the document. "
            "'unknown_needs_review': cannot determine from the document."
        )
    )
    default_basis: CCRAllocationBasis = Field(
        description=(
            "The allocation basis that applies to all costs unless an exception overrides it. "
            "'equal': every owner pays the same share. "
            "'ownership_percentage': share equals the recorded ownership interest. "
            "'square_footage': share is proportional to unit square footage. "
            "'specified_value': each unit has its own flat amount. "
            "'custom_factor': basis is a DRE-defined factor not covered above. "
            "'unknown': cannot determine from the document."
        )
    )
    summary: Optional[str] = Field(
        description="One- or two-sentence operator-readable summary of the allocation policy."
    )
    requires_dre_for_future_years: Optional[bool] = Field(
        description=(
            "True if the document defers specific dollar amounts or denominators to the "
            "DRE, board resolution, or an exhibit not included — meaning per-unit amounts "
            "cannot be calculated from this document alone."
        )
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in the setup_type and default_basis classification."
    )
    source_pages: Optional[list[int]] = Field(
        description="Pages that establish the allocation structure. MUST contain at least one."
    )


class WireCCRUnitFactor(BaseModel):
    """One per-unit allocation factor read from a percentage-interest / exhibit table.

    Populated ONLY when the per-unit table is actually present and legible in the
    classified pages (e.g. an 'exhibit/percentage-interest table' page). Never
    invented from floor-plan drawings or a referenced-but-absent exhibit.
    """

    unit_number: str = Field(
        description="Unit identifier exactly as printed in the table (e.g. '101', 'A', '12-B')."
    )
    square_feet: Optional[float] = Field(
        description=(
            "Per-unit living-area square footage if the table states it as a number; "
            "null if the table only gives a percentage or omits square footage."
        )
    )
    ownership_percent: Optional[float] = Field(
        description=(
            "Per-unit allocation/ownership percentage as printed (e.g. 13.15 for "
            "'13.15%'); null if the table gives only square footage. A percentage "
            "interest computed from square footage IS a valid proportional factor."
        )
    )
    source_page: Optional[int] = Field(
        description="The page number of the table this row was read from."
    )
    confidence: Optional[float] = Field(
        description=(
            "0.0–1.0 confidence that this unit's factor values were read "
            "correctly. Use >= 0.9 for a clearly printed, legible table row; "
            "< 0.7 only when the cell is faded, handwritten, or ambiguous. "
            "Do not report 0.0 for a row you read confidently."
        )
    )


class WireCCRUnitStructure(BaseModel):
    """Unit count and any per-unit factor data machine-readable from the document."""

    unit_count: Optional[int] = Field(
        description="Total unit count if stated in the document."
    )
    per_unit_factors_available: Optional[bool] = Field(
        description=(
            "True if the document (or an included exhibit) contains machine-readable "
            "per-unit allocation factors (square footage, ownership percentage). "
            "False when factors are only in floor-plan drawings or an absent exhibit."
        )
    )
    factor_exhibit_reference: Optional[str] = Field(
        description=(
            "Verbatim reference to the exhibit or schedule that contains per-unit "
            "factors (e.g. 'Exhibit B', 'Schedule of Percentage Interests'). "
            "Null if no such cross-reference exists."
        )
    )
    units: list[WireCCRUnitFactor] = Field(
        description=(
            "One row per unit when a per-unit allocation/percentage-interest table "
            "is PRESENT and legible in the classified pages — extract every row "
            "verbatim. Empty list when no such table is present (factors only "
            "referenced, in floor plans, or in an absent exhibit). Never invent rows."
        )
    )


class WireCCRAllocationPool(BaseModel):
    """One allocation pool derived from the governing document's policy prose."""

    pool_key: str = Field(
        description=(
            "Stable snake_case key for this pool derived from its economic PURPOSE "
            "(e.g. 'equal_base', 'sqft_proportional_exceptions', 'reserve_equal'). "
            "Must not contain HOA names, section numbers, or any value specific to "
            "a particular governing document."
        )
    )
    pool_name: Optional[str] = Field(
        description="Short operator-readable name (e.g. 'Equal Base Pool')."
    )
    allocation_basis: CCRAllocationBasis = Field(
        description="The basis used to divide costs in this pool among participating units."
    )
    recipient_scope: Optional[str] = Field(
        description=(
            "Who pays this pool: 'all_units', 'residential_only', 'commercial_only', "
            "or a free-text description when the scope is more nuanced."
        )
    )
    denominator_label: Optional[str] = Field(
        description=(
            "Label for the denominator (e.g. 'total square footage of all units', "
            "'sum of ownership percentages'). Null when not stated or deferred to exhibit."
        )
    )
    denominator_source: Literal["document_stated", "exhibit_reference", "calculated", "unknown"] = Field(
        description=(
            "'document_stated': the denominator value is printed in the document text. "
            "'exhibit_reference': the document defers the denominator to an exhibit. "
            "'calculated': implied from stated unit counts / sq footages. "
            "'unknown': unclear."
        )
    )
    expense_categories: Optional[list[str]] = Field(
        description=(
            "Expense categories or purposes the document explicitly names for this pool, "
            "verbatim from the text. Empty list for a residual/base pool that covers "
            "everything not claimed by exception pools."
        )
    )
    is_residual_base: Optional[bool] = Field(
        description=(
            "True when this pool is the default 'all costs not in an exception pool'. "
            "False for exception pools with explicit expense categories."
        )
    )
    residual_after_pool_keys: Optional[list[str]] = Field(
        description=(
            "For residual base pools, the pool_keys of exception pools that must be "
            "satisfied first. Empty for non-residual pools."
        )
    )
    source_pages: Optional[list[int]] = Field(
        description="Pages where this pool's rule appears. MUST contain at least one entry."
    )
    confidence: Optional[float] = Field(
        description="0.0–1.0 confidence in this pool's extraction."
    )


class WireCCRReservePolicy(BaseModel):
    """How reserve contributions are funded according to the governing document."""

    funded_through_regular_assessment: Optional[bool] = Field(
        description=(
            "True if the document states reserves are funded as part of the regular "
            "assessment using the same allocation policy."
        )
    )
    separate_reserve_basis: Optional[str] = Field(
        description=(
            "If reserves have a different allocation basis than operating costs, "
            "describe it here. Null when reserves follow the regular assessment policy."
        )
    )
    source_pages: Optional[list[int]] = Field(
        description="Pages where reserve funding is addressed."
    )
    confidence: Optional[float] = Field(
        description=(
            "0.0–1.0 confidence in the reserve-funding policy extraction. "
            "Use < 0.7 only when the reserve provisions are faded, ambiguous, "
            "or split across pages you could not fully read."
        )
    )


class WireCCRPolicyExtraction(BaseModel):
    """Top-level CC&R extraction response shape.

    Field order is chain-of-thought-friendly: metadata first (anchor),
    then page inventory (classification output), then policy interpretation
    (setup, units, pools), then reserve policy, then quality signals.
    """

    document_metadata: WireCCRDocumentMetadata
    page_inventory: list[WirePageInventoryEntry]
    assessment_setup: WireCCRAssessmentSetupBlock
    unit_structure: WireCCRUnitStructure
    allocation_pools: list[WireCCRAllocationPool]
    reserve_policy: Optional[WireCCRReservePolicy]
    validation_checks: list[WireValidationCheck]
    human_review_questions: list[WireHumanReviewQuestion]


CCR_WIRE_SCHEMA_SHA256: str = hashlib.sha256(
    json.dumps(
        WireCCRPolicyExtraction.model_json_schema(), sort_keys=True
    ).encode("utf-8")
).hexdigest()


__all__ = [
    "WirePageInventoryEntry",
    "WirePageInventoryBatch",
    "WireCCRDocumentMetadata",
    "WireCCRAssessmentSetupBlock",
    "WireCCRUnitFactor",
    "WireCCRUnitStructure",
    "WireCCRAllocationPool",
    "WireCCRReservePolicy",
    "WireValidationCheck",
    "WireHumanReviewQuestion",
    "WireCCRPolicyExtraction",
    "CCR_WIRE_SCHEMA_SHA256",
]
