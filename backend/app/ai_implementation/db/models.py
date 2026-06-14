"""SQLAlchemy ORM models — mirrors schema.sql (which is the source of truth).

These models are for querying/inserting only. Tables are created by schema.sql.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, Text, Float, ForeignKey, text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Shared constants ─────────────────────────────────────────────────────────

DECIDED_STATUSES = ["accepted", "modified"]
MAX_PCT_CHANGE = 0.30

FEATURE_COLUMNS = [
    "account_level_1", "account_level_2", "account_level_3",
    "adjusted_pct_diff", "adjusted_coverage_ratio",
    "seasonality_index", "normalized_annual_budget",
    "is_income", "is_reserve", "is_admin",
]

_CREATED_AT_DEFAULT = text("datetime('now')")

# Draft states are explicit to keep the one-active-draft behavior auditable.
BUDGET_DRAFT_ACTIVE = "active"
BUDGET_DRAFT_SUPERSEDED = "superseded"
BUDGET_DRAFT_GENERATED = "generated"

# Version stages are locked business terms for the sync-history workflow.
BUDGET_VERSION_STAGE_INTERIM = "Interim"
BUDGET_VERSION_STAGE_FINAL = "Final"

# Disclosure-package job lifecycle (Phase 11). The router exposes job_id as TEXT
# (uuid4) so we never leak rate-of-creation via a sequential surrogate.
DISCLOSURE_JOB_PENDING = "pending"
DISCLOSURE_JOB_RUNNING = "running"
DISCLOSURE_JOB_COMPLETED = "completed"
DISCLOSURE_JOB_FAILED = "failed"

# Per-render stage constants. Match the worker pipeline order in plan 11-06+.
DISCLOSURE_STAGE_VALIDATING = "validating"
DISCLOSURE_STAGE_COMPUTING = "computing"
DISCLOSURE_STAGE_RENDERING = "rendering"
DISCLOSURE_STAGE_MERGING = "merging"
DISCLOSURE_STAGE_VERIFYING = "verifying"


# ── Models ───────────────────────────────────────────────────────────────────

class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False, unique=True)
    hoa_code = Column(Text)
    tax_id = Column(Text)
    units = Column(Integer)
    reserve_inflation_rate = Column(Float, nullable=False, default=0.0)
    fiscal_year_start_month = Column(Integer, default=1)
    fiscal_year_end_month = Column(Integer, default=12)
    city = Column(Text)
    # HOA-permanent facts (drifting-puzzling-grove refactor)
    state = Column(Text, default="CA")
    entity_type = Column(Text)
    incorporation_year = Column(Integer)
    portfolio_year = Column(Integer)
    workflow_status = Column(Text, default="Not Started")
    assessment_mode = Column(Text, nullable=False, default="variable")
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    runs = relationship("SuggestionRun", back_populates="property", lazy="raise")
    cases = relationship("FeedbackCase", back_populates="property", lazy="raise")


class HOASettings(Base):
    __tablename__ = "hoa_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(
        Integer,
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    management_company = Column(Text)
    management_company_address = Column(Text)
    management_company_phone = Column(Text)
    management_company_fax = Column(Text)
    management_company_web = Column(Text)
    cpa_firm_name = Column(Text)
    cpa_firm_address = Column(Text)
    reserve_study_expert_name = Column(Text)
    reserve_study_date = Column(Text)
    # Priority-A disclosure inputs (drifting-puzzling-grove) — operator can
    # override values that would otherwise be derived from the budget draft
    # or reserve study, and supply data the templates need but can't compute.
    approved_monthly_assessment_per_unit = Column(Float)
    financial_packet_archetype = Column(Text, default="dual-fund")
    reserve_interest_income_override = Column(Float)
    income_tax_provision_override = Column(Float)
    reserve_funding_source = Column(Text, default="reserve_study_provision")
    reserve_funding_manual_amount = Column(Float)
    special_assessments_json = Column(Text, default="[]")
    additional_assessments_needed_json = Column(Text, default="[]")
    outstanding_loan_json = Column(Text)  # nullable: null = no loan
    # Phase 1 boilerplate-gap fields (drifting-puzzling-grove). Letter +
    # accountant + funding-plan dates are distinct from the rendering date;
    # signed-by-title pairs with letter_signed_by for the 2-line signature
    # block; hoa_state/entity_type/incorporation_year fill in Note 1.
    letter_date = Column(Text)
    letter_signed_by_title = Column(Text)
    accountant_report_date = Column(Text)
    reserve_funding_plan_date = Column(Text)
    hoa_state = Column(Text, default="CA")
    hoa_entity_type = Column(Text)
    hoa_incorporation_year = Column(Integer)
    reserve_cash_balance_eoy_prior = Column(Float, default=0.0)
    fund_balance_boy_operations = Column(Float, default=0.0)
    monthly_assessment_per_unit_prior = Column(Float, default=0.0)
    interest_rate_after_tax = Column(Float, default=0.0)
    replacement_cost_increase_rate = Column(Float, default=0.03)
    assessment_increase_schedule_json = Column(Text, default="[]")
    # 30-year reserve funding study (drifting-puzzling-grove rebuild).
    # Separate per-unit monthly assessment dedicated to the replacement fund,
    # distinct from the operations monthly assessment.
    replacement_fund_monthly_assessment_per_unit = Column(Float)
    # Operator-entered deferrals: list of {year, amount}. Usually [].
    board_deferrals_json = Column(Text, default="[]")
    letter_signed_by = Column(Text, default="Board of Directors")
    updated_at = Column(Text, server_default=_CREATED_AT_DEFAULT)


class SuggestionRun(Base):
    __tablename__ = "suggestion_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    source = Column(Text, nullable=False)
    total_annual_budget = Column(Float)
    total_ytd_actuals = Column(Float)
    pct_year_elapsed = Column(Float)
    statement_month = Column(Integer)
    fiscal_year = Column(Integer)
    growth_factor = Column(Float)
    executive_summary = Column(Text)
    coherence_score = Column(Text)
    total_budget_impact = Column(Text)
    flagged_items_json = Column(Text)
    latency_ms = Column(Integer)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    property = relationship("Property", back_populates="runs", lazy="raise")
    cases = relationship("FeedbackCase", back_populates="run", lazy="raise")


class FeedbackCase(Base):
    __tablename__ = "feedback_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("suggestion_runs.id"), nullable=False)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    account_code = Column(Integer, nullable=False)
    account_name = Column(Text, nullable=False)
    label = Column(Text, nullable=False)
    category = Column(Text)
    account_level_1 = Column(Integer)
    account_level_2 = Column(Integer)
    account_level_3 = Column(Integer)
    is_income = Column(Integer, default=0)
    is_reserve = Column(Integer, default=0)
    is_admin = Column(Integer, default=0)
    annual_budget = Column(Float, nullable=False)
    ytd_actual = Column(Float, nullable=False)
    projection = Column(Float)
    pct_diff = Column(Float)
    coverage_ratio = Column(Float)
    adjusted_pct_diff = Column(Float)
    adjusted_coverage_ratio = Column(Float)
    seasonality_index = Column(Float)
    normalized_annual_budget = Column(Float)
    cbr_anchor_pct = Column(Float)
    cbr_similarity = Column(Float)
    ml_baseline_pct = Column(Float)
    ai_suggested_pct_change = Column(Float)
    ai_reason = Column(Text)
    ai_confidence = Column(Float)
    revised_by_pass2 = Column(Integer, default=0)
    user_decision = Column(Text, default="pending")
    user_final_pct_change = Column(Float)
    user_note = Column(Text)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    run = relationship("SuggestionRun", back_populates="cases", lazy="raise")
    property = relationship("Property", back_populates="cases", lazy="raise")


class SOPRule(Base):
    __tablename__ = "sop_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_text = Column(Text, nullable=False)
    active = Column(Integer, default=1)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False, unique=True)
    name = Column(Text, nullable=False)
    hashed_password = Column(Text, nullable=False)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(Text, primary_key=True)
    value_text = Column(Text)
    updated_at = Column(Text, server_default=_CREATED_AT_DEFAULT)


class BudgetUpload(Base):
    __tablename__ = "budget_uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    document_role = Column(Text, nullable=False, default="budget_source")
    source_mode = Column(Text, nullable=False, default="income_statement")
    assessment_mode = Column(Text, nullable=False, default="variable")
    original_filename = Column(Text, nullable=False)
    storage_key = Column(Text, nullable=False, unique=True)
    content_type = Column(Text)
    byte_size = Column(Integer)
    sha256 = Column(Text, nullable=False)
    enrichment_status = Column(Text, nullable=False)
    line_items_json = Column(Text)
    budget_preview_json = Column(Text)
    statement_month = Column(Integer)
    growth_factor = Column(Float)
    growth_factor_note = Column(Text)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"))
    uploaded_by_name = Column(Text, nullable=False)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    property = relationship("Property", lazy="raise")
    uploaded_by = relationship("User", lazy="raise")


class BudgetDraft(Base):
    __tablename__ = "budget_drafts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    source_upload_id = Column(Integer, ForeignKey("budget_uploads.id"))
    reserve_study_upload_id = Column(Integer, ForeignKey("budget_uploads.id"))
    reopened_from_version_id = Column(Integer, ForeignKey("budget_versions.id"))
    source_mode = Column(Text, nullable=False, default="income_statement")
    assessment_mode = Column(Text, nullable=False, default="variable")
    status = Column(Text, nullable=False, default=BUDGET_DRAFT_ACTIVE)
    line_items_json = Column(Text, nullable=False)
    reserve_study_rows_json = Column(Text)
    reserve_study_warnings_json = Column(Text)
    reserve_study_status = Column(Text, nullable=False, default="none")
    global_note = Column(Text)
    statement_month = Column(Integer)
    growth_factor = Column(Float)
    growth_factor_note = Column(Text)
    reserve_inflation_rate = Column(Float, nullable=False, default=0.0)
    reserve_inflation_note = Column(Text)
    budget_preview_json = Column(Text)
    enriched_storage_key = Column(Text)
    version_int = Column(Integer, nullable=False, default=0)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    updated_by_user_id = Column(Integer, ForeignKey("users.id"))
    actor_name = Column(Text, nullable=False)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)
    updated_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    property = relationship("Property", lazy="raise")
    source_upload = relationship("BudgetUpload", foreign_keys=[source_upload_id], lazy="raise")
    reserve_study_upload = relationship("BudgetUpload", foreign_keys=[reserve_study_upload_id], lazy="raise")
    reopened_from_version = relationship(
        "BudgetVersion",
        foreign_keys=[reopened_from_version_id],
        lazy="raise",
    )
    created_by = relationship("User", foreign_keys=[created_by_user_id], lazy="raise")
    updated_by = relationship("User", foreign_keys=[updated_by_user_id], lazy="raise")


class BudgetVersion(Base):
    __tablename__ = "budget_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    source_upload_id = Column(Integer, ForeignKey("budget_uploads.id"))
    source_draft_id = Column(Integer, ForeignKey("budget_drafts.id"))
    reopened_from_version_id = Column(Integer, ForeignKey("budget_versions.id"))
    source_mode = Column(Text, nullable=False, default="income_statement")
    assessment_mode = Column(Text, nullable=False, default="variable")
    storage_key = Column(Text)
    output_storage_key = Column(Text)
    version_number = Column(Integer, nullable=False)
    version_code = Column(Text, nullable=False)
    stage = Column(Text, nullable=False, default=BUDGET_VERSION_STAGE_INTERIM)
    label = Column(Text)
    summary_note = Column(Text)
    line_items_json = Column(Text, nullable=False)
    budget_preview_json = Column(Text)
    total_income = Column(Float, nullable=False)
    total_expense = Column(Float, nullable=False)
    net_operating_income = Column(Float, nullable=False)
    growth_factor = Column(Float)
    growth_factor_note = Column(Text)
    reserve_inflation_rate = Column(Float, nullable=False, default=0.0)
    reserve_inflation_note = Column(Text)
    statement_month = Column(Integer)
    fiscal_year_start_month = Column(Integer, nullable=False)
    fiscal_year_end_month = Column(Integer, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_by_name = Column(Text, nullable=False)
    actor_name = Column(Text, nullable=False)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    property = relationship("Property", lazy="raise")
    source_upload = relationship("BudgetUpload", foreign_keys=[source_upload_id], lazy="raise")
    source_draft = relationship("BudgetDraft", foreign_keys=[source_draft_id], lazy="raise")
    reopened_from_version = relationship(
        "BudgetVersion",
        remote_side=[id],
        foreign_keys=[reopened_from_version_id],
        lazy="raise",
    )
    created_by = relationship("User", foreign_keys=[created_by_user_id], lazy="raise")


class BudgetNote(Base):
    __tablename__ = "budget_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    upload_id = Column(Integer, ForeignKey("budget_uploads.id"))
    draft_id = Column(Integer, ForeignKey("budget_drafts.id"))
    version_id = Column(Integer, ForeignKey("budget_versions.id"))
    note_scope = Column(Text, nullable=False)
    line_item_key = Column(Text)
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_by_name = Column(Text, nullable=False)
    actor_name = Column(Text, nullable=False)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    property = relationship("Property", lazy="raise")
    upload = relationship("BudgetUpload", lazy="raise")
    draft = relationship("BudgetDraft", lazy="raise")
    version = relationship("BudgetVersion", lazy="raise")
    created_by = relationship("User", lazy="raise")


class BudgetAuditEvent(Base):
    __tablename__ = "budget_audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    upload_id = Column(Integer, ForeignKey("budget_uploads.id"))
    draft_id = Column(Integer, ForeignKey("budget_drafts.id"))
    version_id = Column(Integer, ForeignKey("budget_versions.id"))
    note_id = Column(Integer, ForeignKey("budget_notes.id"))
    event_type = Column(Text, nullable=False)
    summary = Column(Text, nullable=False)
    actor_user_id = Column(Integer, ForeignKey("users.id"))
    actor_name = Column(Text, nullable=False)
    payload_json = Column(Text)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)

    property = relationship("Property", lazy="raise")
    upload = relationship("BudgetUpload", lazy="raise")
    draft = relationship("BudgetDraft", lazy="raise")
    version = relationship("BudgetVersion", lazy="raise")
    note = relationship("BudgetNote", lazy="raise")
    actor = relationship("User", lazy="raise")


class DisclosurePackageJob(Base):
    """Phase 11 disclosure-package render job.

    The id is a TEXT uuid4 string, NOT an autoincrement integer, because the
    router exposes job_id in URLs and we don't want sequential ids leaking
    creation rate. Threat model T-11-01: the property_id FK + ON DELETE CASCADE
    is the anchor for the ownership-check that plan 11-06's router will enforce
    via Depends(get_current_user) before returning a job row.

    Threat model T-11-06 (regenerate race): for MVP we accept the
    `(property_id, fiscal_year, status='running')` race per Phase 11's
    single-user assumption; plan 11-06's service does SELECT-then-INSERT in a
    transaction. Full unique-constraint enforcement is deferred.
    """

    __tablename__ = "disclosure_package_jobs"

    id = Column(Text, primary_key=True)
    property_id = Column(
        Integer,
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )
    fiscal_year = Column(Integer, nullable=False)
    status = Column(Text, nullable=False, default=DISCLOSURE_JOB_PENDING)
    stage = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    output_path = Column(Text, nullable=True)
    audit_path = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(Text, server_default=_CREATED_AT_DEFAULT)
    completed_at = Column(Text, nullable=True)

    property = relationship("Property", lazy="raise")
