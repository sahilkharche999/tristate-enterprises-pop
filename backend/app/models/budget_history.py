"""Pydantic contracts for the HOA-scoped budget history workflow."""
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..assessment_mode import AssessmentMode, ASSESSMENT_MODE_VARIABLE

JsonObject = dict[str, Any]
BudgetSourceMode = Literal["income_statement", "proforma_final_budget"]


class BudgetTimelineEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    summary: str
    actor_name: str
    occurred_at: str
    related_upload_id: Optional[int] = None
    related_draft_id: Optional[int] = None
    related_version_id: Optional[int] = None
    related_note_id: Optional[int] = None
    file_name: Optional[str] = None
    version_code: Optional[str] = None
    payload: Optional[JsonObject] = None


class BudgetVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_number: int
    version_code: str
    stage: str
    label: Optional[str] = None
    summary_note: Optional[str] = None
    total_income: float
    total_expense: float
    net_operating_income: float
    growth_factor: Optional[float] = None
    growth_factor_note: Optional[str] = None
    reserve_inflation_rate: float = 0.0
    reserve_inflation_note: Optional[str] = None
    statement_month: Optional[int] = None
    created_at: str
    created_by_name: str
    source_draft_id: Optional[int] = None
    output_storage_key: Optional[str] = None
    source_upload_filename: Optional[str] = None
    source_mode: BudgetSourceMode = "income_statement"
    assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE


class BudgetVersionDetail(BudgetVersionSummary):
    source_upload_id: Optional[int] = None
    source_draft_id: Optional[int] = None
    reopened_from_version_id: Optional[int] = None
    fiscal_year_start_month: int
    fiscal_year_end_month: int
    line_items: list[JsonObject] = Field(default_factory=list)
    budget_preview: Optional[JsonObject] = None


class BudgetNoteRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    note_scope: str
    line_item_key: Optional[str] = None
    title: str
    body: str
    created_at: str
    created_by_name: str
    upload_id: Optional[int] = None
    draft_id: Optional[int] = None
    version_id: Optional[int] = None


class BudgetDraftPayload(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    source_upload_id: Optional[int] = None
    reserve_study_upload_id: Optional[int] = None
    reopened_from_version_id: Optional[int] = None
    line_items: list[JsonObject] = Field(default_factory=list)
    reserve_study_status: str = "none"
    reserve_study_rows: list[JsonObject] = Field(default_factory=list)
    reserve_study_warnings: list[str] = Field(default_factory=list)
    global_note: Optional[str] = None
    statement_month: Optional[int] = None
    growth_factor: Optional[float] = None
    growth_factor_note: Optional[str] = None
    reserve_inflation_rate: float = 0.0
    reserve_inflation_note: Optional[str] = None
    version_int: int = 0
    updated_at: Optional[str] = None
    upload_filename: Optional[str] = None
    enriched_file_available: bool = False
    source_mode: BudgetSourceMode = "income_statement"
    assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE


class BudgetDraftSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    source_upload_id: Optional[int] = None
    reserve_study_upload_id: Optional[int] = None
    source_upload_filename: Optional[str] = None
    reopened_from_version_id: Optional[int] = None
    reopened_from_version_code: Optional[str] = None
    reserve_inflation_rate: float = 0.0
    reserve_inflation_note: Optional[str] = None
    version_int: int = 0
    reserve_study_status: str = "none"
    updated_at: Optional[str] = None
    actor_name: str
    enriched_file_available: bool = False
    source_mode: BudgetSourceMode = "income_statement"
    assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE


class BudgetHistoryResponse(BaseModel):
    active_draft: Optional[BudgetDraftPayload] = None
    drafts: list[BudgetDraftSummary] = Field(default_factory=list)
    timeline: list[BudgetTimelineEvent] = Field(default_factory=list)
    versions: list[BudgetVersionSummary] = Field(default_factory=list)
    notes: list[BudgetNoteRecord] = Field(default_factory=list)


class BudgetGlIdentityPayload(BaseModel):
    account_code: Optional[str]
    label: str
    normalized_label: Optional[str]
    line_item_key: Optional[str]
    section: str
    category: str
    fund_type: str


class BudgetGlMergeCommitRequest(BaseModel):
    primary: BudgetGlIdentityPayload
    secondary: BudgetGlIdentityPayload
    source: Literal["manual", "gemini_suggestion"]


class BudgetGlMergeApplicationPayload(BaseModel):
    id: int
    merge_id: int
    property_id: int
    budget_draft_id: int
    assessment_setup_id: Optional[int]
    source: str
    status: str
    match_strategy: Optional[str]


class BudgetGlMergeCommitResponse(BaseModel):
    merge_id: int
    application: BudgetGlMergeApplicationPayload
    draft_version: int


class BudgetGlMergeListItem(BaseModel):
    id: int
    property_id: int
    primary_account_code: Optional[str]
    primary_label: str
    primary_normalized_label: str
    secondary_account_code: Optional[str]
    secondary_label: str
    secondary_normalized_label: str
    status: str
    application_id: Optional[int]
    application_status: Optional[str]
    source: Optional[str]


class BudgetGlMergeSuggestionPayload(BaseModel):
    primary_account_code: Optional[str]
    secondary_account_code: Optional[str]
    primary_label: str
    secondary_label: str
    primary_normalized_label: str
    secondary_normalized_label: str
    confidence: float
    reason: str
    local_only: bool
    wire_schema_sha256: str


class ExtractionQualityWarning(BaseModel):
    """Plain-language warning shown to non-technical users when an upload
    succeeded but the extraction took a degraded path."""

    code: str  # machine-readable identifier (e.g. "scanned_pdf_vision_only")
    title: str
    body: str
    severity: str = "warning"  # "warning" | "info"


class ExtractionDebugInfo(BaseModel):
    """Technical extraction details shown when an upload needs review."""

    code: str
    message: str
    details: JsonObject = Field(default_factory=dict)


class BudgetUploadResponse(BaseModel):
    upload_id: int
    draft: Optional[BudgetDraftPayload] = None
    timeline_event: BudgetTimelineEvent
    warnings: list[str] = Field(default_factory=list)
    review_required: bool = False
    review_reason: Optional[str] = None
    debug_info: Optional[ExtractionDebugInfo] = None
    # Set when the extractor took a degraded path (e.g. scanned-PDF
    # vision-only fallback). The frontend renders this as a one-shot
    # dismissible dialog telling the user to double-check the numbers.
    # None means the upload used the normal high-confidence path; no
    # quality warning is needed.
    extraction_quality_warning: Optional[ExtractionQualityWarning] = None


class BundleFileStatus(BaseModel):
    upload_id: Optional[int] = None
    filename: Optional[str] = None
    status: Literal["completed", "review_required", "failed", "pending"] = "pending"
    warnings: list[str] = Field(default_factory=list)
    review_reason: Optional[str] = None
    debug_info: Optional[ExtractionDebugInfo] = None


class BudgetBundleUploadResponse(BaseModel):
    draft: Optional[BudgetDraftPayload] = None
    budget_source: BundleFileStatus
    reserve_study: BundleFileStatus
    can_continue_with_budget_only: bool = False
    can_continue_with_reserve_study_only: bool = False


class BudgetReserveStudySaveRequest(BaseModel):
    rows: list[JsonObject] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BudgetReserveStudyApplyResponse(BaseModel):
    draft: BudgetDraftPayload
    applied_count: int = 0
    message: str


class BudgetDraftSaveRequest(BaseModel):
    draft_id: int
    line_items: list[JsonObject] = Field(default_factory=list)
    global_note: Optional[str] = None
    statement_month: Optional[int] = Field(default=None, ge=1, le=12)
    growth_factor: Optional[float] = None
    growth_factor_note: Optional[str] = None
    reserve_inflation_rate: Optional[float] = None
    reserve_inflation_note: Optional[str] = None


class BudgetDraftSaveResponse(BaseModel):
    draft: BudgetDraftPayload
    timeline_event: Optional[BudgetTimelineEvent] = None


class BudgetNoteSaveRequest(BaseModel):
    draft_id: int
    version_id: Optional[int] = None
    note_scope: str
    line_item_key: Optional[str] = None
    title: str
    body: str


class BudgetNoteSaveResponse(BaseModel):
    note: BudgetNoteRecord
    timeline_event: BudgetTimelineEvent


class BudgetGenerateRequest(BaseModel):
    draft_id: int
    line_items: list[JsonObject] = Field(default_factory=list)
    global_note: Optional[str] = None


class BudgetUploadRequest(BaseModel):
    source_mode: BudgetSourceMode = "income_statement"
    assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE


class BudgetBundleUploadRequest(BaseModel):
    source_mode: BudgetSourceMode = "income_statement"
    assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE


class BudgetGenerateResponse(BaseModel):
    draft: BudgetDraftPayload
    version: BudgetVersionDetail
    timeline_event: BudgetTimelineEvent


class BudgetDraftCompareBaselineOption(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_code: str
    stage: str
    label: Optional[str] = None
    created_at: str
    summary_note: Optional[str] = None
    is_recommended: bool


class BudgetDraftCompareOptionsResponse(BaseModel):
    draft_id: int
    recommended_baseline_version_id: Optional[int] = None
    recommended_baseline_reason: Optional[str] = None
    baseline_versions: list[BudgetDraftCompareBaselineOption] = Field(default_factory=list)
    reserve_inflation_rate: float = 0.0
    reserve_inflation_note: Optional[str] = None


class BudgetDraftCompareRequest(BaseModel):
    baseline_version_id: int
    changed_only: bool = True


class BudgetDraftReserveReviewRequest(BaseModel):
    line_items: list[JsonObject] = Field(default_factory=list)
    reserve_inflation_rate: float = 0.0


class BudgetDraftCompareSectionSummary(BaseModel):
    baseline_amount: float
    inflation_adjusted_baseline_amount: float
    current_amount: float
    delta_amount: float
    delta_percent: Optional[float] = None


class BudgetDraftChangeSummary(BaseModel):
    baseline_amount: float
    current_amount: float
    delta_amount: float
    delta_percent: Optional[float] = None


class BudgetReserveReviewSummary(BaseModel):
    baseline_amount: float
    inflation_adjusted_amount: float
    impact_amount: float
    eligible_component_count: int


class BudgetDraftCompareRow(BaseModel):
    line_item_key: str
    label: str
    category: str
    reserve_group: Optional[str] = None
    is_reserve: bool
    reserve_inflation_eligible: bool
    changed: bool
    baseline_amount: float
    inflation_adjusted_baseline_amount: float
    current_amount: float
    delta_amount: float
    delta_percent: Optional[float] = None
    note_title: Optional[str] = None
    note_body: Optional[str] = None


class BudgetReserveComponentRow(BaseModel):
    line_item_key: str
    label: str
    baseline_amount: float
    inflation_adjusted_amount: float
    impact_amount: float


class BudgetDraftCompareResponse(BaseModel):
    draft_id: int
    baseline_version_id: int
    changed_only: bool
    draft_compare_summary: BudgetDraftChangeSummary
    draft_compare_rows: list[BudgetDraftCompareRow] = Field(default_factory=list)
    reserve_review_summary: BudgetReserveReviewSummary
    reserve_component_rows: list[BudgetReserveComponentRow] = Field(default_factory=list)


class BudgetDraftReserveReviewResponse(BaseModel):
    draft_id: int
    reserve_inflation_rate: float
    reserve_review_summary: BudgetReserveReviewSummary
    reserve_component_rows: list[BudgetReserveComponentRow] = Field(default_factory=list)


class BudgetVersionCompareCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_code: str
    stage: str
    label: Optional[str] = None
    summary_note: Optional[str] = None
    created_at: str
    created_by_name: str
    source_upload_filename: Optional[str] = None
    total_income: float
    total_expense: float
    net_operating_income: float
    growth_factor: Optional[float] = None
    growth_factor_note: Optional[str] = None
    statement_month: Optional[int] = None
    fiscal_year_start_month: int
    fiscal_year_end_month: int
    source_mode: BudgetSourceMode = "income_statement"
    assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE


class BudgetVersionCompareResponse(BaseModel):
    versions: list[BudgetVersionCompareCard] = Field(default_factory=list)


class BudgetVersionReopenResponse(BaseModel):
    draft: BudgetDraftPayload
    timeline_event: BudgetTimelineEvent


class BudgetVersionMetadataUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Optional[Literal["Interim", "Final"]] = None
    label: Optional[str] = None
    summary_note: Optional[str] = None


class BudgetVersionMetadataUpdateResponse(BaseModel):
    version: BudgetVersionDetail
    timeline_events: list[BudgetTimelineEvent] = Field(default_factory=list)
