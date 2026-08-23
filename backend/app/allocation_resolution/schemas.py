"""Domain types for declared allocation rules and operator resolutions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


CURRENCY_TOLERANCE = Decimal("0.01")

DeclaredAllocationMethod = Literal[
    "equal",
    "square_footage",
    "ownership_percentage",
    "specified_value",
    "custom_factor",
    "external_schedule",
    "unknown",
]

CanonicalAllocationMethod = Literal[
    "equal",
    "square_footage",
    "ownership_percentage",
    "specified_value",
]

ResolutionStatus = Literal["unresolved", "draft", "approved", "superseded"]

CategoryDecision = Literal["mapped", "zero", "not_applicable"]

SliceStatus = Literal["draft", "approved", "superseded"]

ReadinessIssueCode = Literal[
    "allocation_resolution_required",
    "required_category_unmapped",
    "combined_line_requires_split",
    "invalid_factor_set",
    "slice_reconciliation_failed",
    "pool_reconciliation_failed",
    "recipient_total_mismatch",
    "approval_required",
    "referenced_schedule_missing",
]

EnforcementLevel = Literal["off", "new_governing_docs", "all_final_packages"]


class ReferencedSchedule(BaseModel):
    """External schedule named by the governing document."""

    model_config = ConfigDict(extra="ignore")

    schedule_type: Optional[str] = None
    schedule_name: Optional[str] = None
    available: bool = False
    document_id: Optional[int] = None
    prior_package_id: Optional[int] = None


class ResolutionEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_pages: list[int] = Field(default_factory=list)
    source_text: str = ""
    reason: str = ""
    document_id: Optional[int] = None
    prior_package_id: Optional[int] = None


class FactorSnapshot(BaseModel):
    """Frozen per-recipient factors used by an approved (or draft) resolution."""

    model_config = ConfigDict(extra="ignore")

    method: Optional[CanonicalAllocationMethod] = None
    denominator_value: Optional[Decimal] = None
    denominator_source: Optional[str] = None
    recipients: dict[str, Decimal] = Field(default_factory=dict)


class AllocationResolutionRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Optional[int] = None
    property_id: int
    assessment_setup_id: int
    pool_key: str
    version_int: int = 1
    status: ResolutionStatus = "unresolved"
    declared_method: DeclaredAllocationMethod
    declared_denominator_label: str = ""
    referenced_schedule: ReferencedSchedule = Field(default_factory=ReferencedSchedule)
    included_categories: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)
    evidence: ResolutionEvidence = Field(default_factory=ResolutionEvidence)
    resolved_method: Optional[CanonicalAllocationMethod] = None
    factor_snapshot: FactorSnapshot = Field(default_factory=FactorSnapshot)
    source: Literal["promotion", "operator", "migration"] = "promotion"
    created_by: str = ""
    created_at: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None


class BudgetLineSlice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Optional[int] = None
    property_id: int
    assessment_setup_id: int
    source_line_normalized_label: str
    source_line_account_code: Optional[str] = None
    source_annual_amount: Decimal
    slice_annual_amount: Decimal
    slice_percent: Optional[Decimal] = None
    pool_key: str
    semantic_category: str
    status: SliceStatus = "draft"
    evidence_text: str = ""
    reason: str = ""
    created_by: str = ""
    created_at: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None


class CategoryCoverageDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Optional[int] = None
    property_id: int
    assessment_setup_id: int
    pool_key: str
    category: str
    decision: CategoryDecision
    mapped_amount: Optional[Decimal] = None
    evidence_text: str = ""
    reason: str = ""
    created_by: str = ""
    created_at: Optional[str] = None


class ReadinessIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: ReadinessIssueCode
    severity: Literal["blocking", "warning"] = "blocking"
    message: str
    target: str = ""
    fix_path: str = ""
    fix_label: str = "Resolve allocation issues"
    details: dict[str, Any] = Field(default_factory=dict)


class ReadinessReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ready_for_final: bool = False
    preview_available: bool = False
    enforcement: EnforcementLevel = "new_governing_docs"
    issues: list[ReadinessIssue] = Field(default_factory=list)
    gates: list[dict[str, Any]] = Field(default_factory=list)
