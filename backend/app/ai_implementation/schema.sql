PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS properties (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL UNIQUE,
    hoa_code                TEXT,
    tax_id                  TEXT,
    units                   INTEGER,
    reserve_inflation_rate  REAL DEFAULT 0,
    fiscal_year_start_month INTEGER DEFAULT 1,
    fiscal_year_end_month   INTEGER DEFAULT 12,
    city                    TEXT,
    portfolio_year          INTEGER,
    workflow_status         TEXT DEFAULT 'Not Started',
    tenant_id               INTEGER NOT NULL DEFAULT 1,
    default_assessment_setup_id INTEGER,
    assessment_mode         TEXT NOT NULL DEFAULT 'variable'
                            CHECK(assessment_mode IN ('fixed', 'variable')),
    state                   TEXT DEFAULT 'CA',
    entity_type             TEXT,
    incorporation_year      INTEGER,
    created_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS suggestion_runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id          INTEGER NOT NULL REFERENCES properties(id),
    source               TEXT NOT NULL CHECK(source IN (
                             'live','historical_import','casebase_seed','feedback_seed'
                         )),
    total_annual_budget  REAL,
    total_ytd_actuals    REAL,
    pct_year_elapsed     REAL,
    statement_month      INTEGER,
    fiscal_year          INTEGER,
    growth_factor        REAL,
    executive_summary    TEXT,
    coherence_score      TEXT CHECK(coherence_score IN ('high','medium','low')),
    total_budget_impact  TEXT,
    flagged_items_json   TEXT,
    latency_ms           INTEGER,
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feedback_cases (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                   INTEGER NOT NULL REFERENCES suggestion_runs(id),
    property_id              INTEGER NOT NULL REFERENCES properties(id),
    account_code             INTEGER NOT NULL,
    account_name             TEXT NOT NULL,
    label                    TEXT NOT NULL,
    category                 TEXT,
    account_level_1          INTEGER,
    account_level_2          INTEGER,
    account_level_3          INTEGER,
    is_income                INTEGER DEFAULT 0,
    is_reserve               INTEGER DEFAULT 0,
    is_admin                 INTEGER DEFAULT 0,
    annual_budget            REAL NOT NULL,
    ytd_actual               REAL NOT NULL,
    projection               REAL,
    pct_diff                 REAL,
    coverage_ratio           REAL,
    adjusted_pct_diff        REAL,
    adjusted_coverage_ratio  REAL,
    seasonality_index        REAL,
    normalized_annual_budget REAL,
    cbr_anchor_pct           REAL,
    cbr_similarity           REAL,
    ml_baseline_pct          REAL,
    ai_suggested_pct_change  REAL,
    ai_reason                TEXT,
    ai_confidence            REAL,
    revised_by_pass2         INTEGER DEFAULT 0,
    user_decision            TEXT DEFAULT 'pending'
                             CHECK(user_decision IN ('accepted','modified','rejected','pending')),
    user_final_pct_change    REAL,
    user_note                TEXT,
    created_at               TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sop_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_text  TEXT NOT NULL,
    active     INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name            TEXT NOT NULL,
    hashed_password TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value_text TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS budget_uploads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id         INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    document_role       TEXT NOT NULL DEFAULT 'budget_source'
                        CHECK(document_role IN ('budget_source', 'reserve_study')),
    source_mode         TEXT NOT NULL DEFAULT 'income_statement'
                        CHECK(source_mode IN ('income_statement', 'proforma_final_budget')),
    assessment_mode     TEXT NOT NULL DEFAULT 'variable'
                        CHECK(assessment_mode IN ('fixed', 'variable')),
    original_filename   TEXT NOT NULL,
    storage_key         TEXT NOT NULL UNIQUE,
    content_type        TEXT,
    byte_size           INTEGER,
    sha256              TEXT NOT NULL,
    enrichment_status   TEXT NOT NULL CHECK(enrichment_status IN ('completed', 'failed')),
    line_items_json     TEXT,
    budget_preview_json TEXT,
    statement_month     INTEGER,
    growth_factor       REAL,
    growth_factor_note  TEXT,
    uploaded_by_user_id INTEGER REFERENCES users(id),
    uploaded_by_name    TEXT NOT NULL,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS budget_drafts (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id              INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    source_upload_id         INTEGER REFERENCES budget_uploads(id) ON DELETE SET NULL,
    reserve_study_upload_id  INTEGER REFERENCES budget_uploads(id) ON DELETE SET NULL,
    reopened_from_version_id INTEGER REFERENCES budget_versions(id) ON DELETE SET NULL,
    source_mode              TEXT NOT NULL DEFAULT 'income_statement'
                             CHECK(source_mode IN ('income_statement', 'proforma_final_budget')),
    assessment_mode          TEXT NOT NULL DEFAULT 'variable'
                             CHECK(assessment_mode IN ('fixed', 'variable')),
    status                   TEXT NOT NULL CHECK(status IN ('active', 'superseded', 'generated')),
    line_items_json          TEXT NOT NULL,
    reserve_study_rows_json  TEXT,
    reserve_study_warnings_json TEXT,
    reserve_study_status     TEXT DEFAULT 'none'
                             CHECK(reserve_study_status IN ('none', 'pending', 'completed', 'review_required', 'failed')),
    global_note              TEXT,
    statement_month          INTEGER,
    growth_factor            REAL,
    growth_factor_note       TEXT,
    reserve_inflation_rate   REAL DEFAULT 0,
    reserve_inflation_note   TEXT,
    budget_preview_json      TEXT,
    enriched_storage_key     TEXT,
    version_int              INTEGER NOT NULL DEFAULT 0,
    created_by_user_id       INTEGER REFERENCES users(id),
    updated_by_user_id       INTEGER REFERENCES users(id),
    actor_name               TEXT NOT NULL,
    created_at               TEXT DEFAULT (datetime('now')),
    updated_at               TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS budget_versions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id              INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    source_upload_id         INTEGER REFERENCES budget_uploads(id) ON DELETE SET NULL,
    source_draft_id          INTEGER REFERENCES budget_drafts(id) ON DELETE SET NULL,
    reopened_from_version_id INTEGER REFERENCES budget_versions(id) ON DELETE SET NULL,
    source_mode              TEXT NOT NULL DEFAULT 'income_statement'
                             CHECK(source_mode IN ('income_statement', 'proforma_final_budget')),
    assessment_mode          TEXT NOT NULL DEFAULT 'variable'
                             CHECK(assessment_mode IN ('fixed', 'variable')),
    storage_key              TEXT,
    output_storage_key       TEXT,
    version_number           INTEGER NOT NULL,
    version_code             TEXT NOT NULL,
    stage                    TEXT NOT NULL CHECK(stage IN ('Interim', 'Final')),
    label                    TEXT,
    summary_note             TEXT,
    line_items_json          TEXT NOT NULL,
    budget_preview_json      TEXT,
    total_income             REAL NOT NULL,
    total_expense            REAL NOT NULL,
    net_operating_income     REAL NOT NULL,
    growth_factor            REAL,
    growth_factor_note       TEXT,
    reserve_inflation_rate   REAL DEFAULT 0,
    reserve_inflation_note   TEXT,
    statement_month          INTEGER,
    fiscal_year_start_month  INTEGER NOT NULL,
    fiscal_year_end_month    INTEGER NOT NULL,
    created_by_user_id       INTEGER REFERENCES users(id),
    created_by_name          TEXT NOT NULL,
    actor_name               TEXT NOT NULL,
    created_at               TEXT DEFAULT (datetime('now')),
    UNIQUE(property_id, version_number),
    UNIQUE(property_id, version_code)
);

CREATE TABLE IF NOT EXISTS budget_notes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id        INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    upload_id          INTEGER REFERENCES budget_uploads(id) ON DELETE SET NULL,
    draft_id           INTEGER REFERENCES budget_drafts(id) ON DELETE SET NULL,
    version_id         INTEGER REFERENCES budget_versions(id) ON DELETE SET NULL,
    note_scope         TEXT NOT NULL CHECK(note_scope IN ('global', 'line_item')),
    line_item_key      TEXT,
    title              TEXT NOT NULL,
    body               TEXT NOT NULL,
    created_by_user_id INTEGER REFERENCES users(id),
    created_by_name    TEXT NOT NULL,
    actor_name         TEXT NOT NULL,
    created_at         TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS budget_audit_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id        INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    upload_id          INTEGER REFERENCES budget_uploads(id) ON DELETE SET NULL,
    draft_id           INTEGER REFERENCES budget_drafts(id) ON DELETE SET NULL,
    version_id         INTEGER REFERENCES budget_versions(id) ON DELETE SET NULL,
    note_id            INTEGER REFERENCES budget_notes(id) ON DELETE SET NULL,
    event_type         TEXT NOT NULL,
    summary            TEXT NOT NULL,
    actor_user_id      INTEGER REFERENCES users(id),
    actor_name         TEXT NOT NULL,
    payload_json       TEXT,
    created_at         TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS budget_line_merges (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id                  INTEGER NOT NULL DEFAULT 1,
    property_id                INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    primary_account_code       TEXT,
    primary_label              TEXT NOT NULL,
    primary_normalized_label   TEXT NOT NULL,
    secondary_account_code     TEXT,
    secondary_label            TEXT NOT NULL,
    secondary_normalized_label TEXT NOT NULL,
    status                     TEXT NOT NULL DEFAULT 'active'
                               CHECK(status IN ('active', 'disabled')),
    decision_source            TEXT NOT NULL
                               CHECK(decision_source IN ('manual', 'gemini_suggestion', 'system')),
    actor                      TEXT NOT NULL,
    created_at                 TEXT NOT NULL DEFAULT (datetime('now')),
    disabled_at                TEXT,
    updated_at                 TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_line_merges_active_rule
    ON budget_line_merges(
        tenant_id, property_id,
        COALESCE(primary_account_code, ''), primary_normalized_label,
        COALESCE(secondary_account_code, ''), secondary_normalized_label
    )
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_budget_line_merges_property_status
    ON budget_line_merges(tenant_id, property_id, status);

CREATE TABLE IF NOT EXISTS budget_line_merge_applications (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id                  INTEGER NOT NULL DEFAULT 1,
    merge_id                   INTEGER NOT NULL REFERENCES budget_line_merges(id) ON DELETE CASCADE,
    property_id                INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    budget_draft_id            INTEGER NOT NULL REFERENCES budget_drafts(id) ON DELETE CASCADE,
    assessment_setup_id        INTEGER REFERENCES assessment_setups(id) ON DELETE SET NULL,
    source                     TEXT NOT NULL
                               CHECK(source IN ('manual', 'gemini_suggestion', 'auto_applied')),
    status                     TEXT NOT NULL DEFAULT 'applied'
                               CHECK(status IN ('applied', 'unmerged', 'finalized')),
    match_strategy             TEXT,
    before_snapshot_json       TEXT NOT NULL,
    after_snapshot_json        TEXT NOT NULL,
    side_effect_snapshot_json  TEXT NOT NULL,
    actor                      TEXT NOT NULL,
    created_at                 TEXT NOT NULL DEFAULT (datetime('now')),
    unmerged_at                TEXT,
    finalized_at               TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_line_merge_applications_applied
    ON budget_line_merge_applications(tenant_id, budget_draft_id, merge_id)
    WHERE status = 'applied';

CREATE INDEX IF NOT EXISTS idx_budget_line_merge_applications_property_status
    ON budget_line_merge_applications(tenant_id, property_id, status);

CREATE INDEX IF NOT EXISTS idx_budget_line_merge_applications_draft_status
    ON budget_line_merge_applications(tenant_id, budget_draft_id, status);

CREATE TABLE IF NOT EXISTS disclosure_package_jobs (
    id              TEXT PRIMARY KEY,
    property_id     INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    fiscal_year     INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
    stage           TEXT,
    error_message   TEXT,
    output_path     TEXT,
    audit_path      TEXT,
    created_by_user_id INTEGER REFERENCES users(id),
    -- C1: links a render job to its target annual package so the compile
    -- takes the frozen-snapshot branch for finalized packages. NULL =
    -- ad-hoc live render.
    annual_package_id INTEGER REFERENCES annual_packages(id),
    created_at      TEXT DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_disclosure_jobs_property
    ON disclosure_package_jobs(property_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hoa_settings (
    id                                  INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id                         INTEGER NOT NULL UNIQUE
                                        REFERENCES properties(id) ON DELETE CASCADE,
    management_company                  TEXT,
    management_company_address          TEXT,
    management_company_phone            TEXT,
    management_company_fax              TEXT,
    management_company_web              TEXT,
    cpa_firm_name                       TEXT,
    cpa_firm_address                    TEXT,
    reserve_study_expert_name           TEXT,
    reserve_study_date                  TEXT,
    approved_monthly_assessment_per_unit REAL,
    financial_packet_archetype          TEXT DEFAULT 'dual-fund',
    reserve_interest_income_override    REAL,
    income_tax_provision_override       REAL,
    reserve_funding_source              TEXT DEFAULT 'reserve_study_provision',
    reserve_funding_manual_amount       REAL,
    special_assessments_json            TEXT DEFAULT '[]',
    additional_assessments_needed_json  TEXT DEFAULT '[]',
    outstanding_loan_json               TEXT,
    letter_date                         TEXT,
    letter_signed_by_title              TEXT,
    accountant_report_date              TEXT,
    reserve_funding_plan_date           TEXT,
    hoa_state                           TEXT DEFAULT 'CA',
    hoa_entity_type                     TEXT,
    hoa_incorporation_year              INTEGER,
    reserve_cash_balance_eoy_prior      REAL DEFAULT 0,
    fund_balance_boy_operations         REAL DEFAULT 0,
    monthly_assessment_per_unit_prior   REAL DEFAULT 0,
    interest_rate_after_tax             REAL DEFAULT 0,
    replacement_cost_increase_rate      REAL DEFAULT 0.03,
    assessment_increase_schedule_json   TEXT,
    replacement_fund_monthly_assessment_per_unit REAL,
    board_deferrals_json                TEXT DEFAULT '[]',
    letter_signed_by                    TEXT DEFAULT 'Board of Directors',
    tenant_id                           INTEGER NOT NULL DEFAULT 1,
    version_int                         INTEGER NOT NULL DEFAULT 0,
    logo_filename                       TEXT,
    letterhead_logo_mode                TEXT NOT NULL DEFAULT 'logo_and_text',
    boilerplate_overrides_json          TEXT,
    boilerplate_reference_filename      TEXT,
    updated_at                          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hoa_settings_property ON hoa_settings(property_id);

-- add-full-document-editor: operator-edited narrative document bodies.
-- Two override layers over the repo baseline shipped in
-- app/disclosure_package/content/standard/<document_id>.html; resolution is
-- HOA row → firm row → baseline, and "reset to default" is a row DELETE.
--
-- scope='firm'  → scope_id IS NULL (applies to every HOA; the system is
--                 single-tenant, so "firm" is a global layer, not a tenant)
-- scope='hoa'   → scope_id = properties.id
--
-- No FK on scope_id: the column is polymorphic across scopes, so a property
-- delete would not cascade here. There is no property-delete path today;
-- narrative_content.delete_hoa_overrides() exists for whoever adds one.
CREATE TABLE IF NOT EXISTS narrative_overrides (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scope        TEXT NOT NULL CHECK (scope IN ('firm', 'hoa')),
    scope_id     INTEGER,
    document_id  TEXT NOT NULL,
    body_html    TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by   TEXT,
    CHECK ((scope = 'firm' AND scope_id IS NULL)
        OR (scope = 'hoa'  AND scope_id IS NOT NULL))
);

-- UNIQUE(scope, scope_id, document_id) as two partial indexes: SQLite treats
-- NULLs as distinct in a plain UNIQUE index, so a firm-scope uniqueness
-- constraint over a NULL scope_id would not actually constrain anything.
CREATE UNIQUE INDEX IF NOT EXISTS idx_narrative_overrides_firm
    ON narrative_overrides(document_id)
    WHERE scope = 'firm';
CREATE UNIQUE INDEX IF NOT EXISTS idx_narrative_overrides_hoa
    ON narrative_overrides(scope_id, document_id)
    WHERE scope = 'hoa';

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_properties_hoa_code ON properties(hoa_code);
CREATE INDEX IF NOT EXISTS idx_feedback_account ON feedback_cases(account_code, property_id);
CREATE INDEX IF NOT EXISTS idx_feedback_temporal ON feedback_cases(created_at);
CREATE INDEX IF NOT EXISTS idx_runs_property ON suggestion_runs(property_id, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_training ON feedback_cases(user_decision, created_at);
CREATE INDEX IF NOT EXISTS idx_budget_uploads_property ON budget_uploads(property_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_uploads_storage_key ON budget_uploads(storage_key);
CREATE INDEX IF NOT EXISTS idx_budget_drafts_property ON budget_drafts(property_id, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_drafts_active_property
    ON budget_drafts(property_id)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_budget_versions_property ON budget_versions(property_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_budget_notes_property ON budget_notes(property_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_budget_audit_events_property ON budget_audit_events(property_id, created_at DESC);

-- ── DRE-driven assessment engine tables (dre-driven-assessment-engine) ──
-- One row per HOA per supersession. Only status='approved' rows are
-- consumed by the assessment engine. Drafts live alongside until the
-- operator approves them via the Review Workbench.
CREATE TABLE IF NOT EXISTS assessment_setups (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id                INTEGER NOT NULL DEFAULT 1,
    property_id              INTEGER NOT NULL
                             REFERENCES properties(id) ON DELETE CASCADE,
    source_dre_document_id   INTEGER,
    setup_type               TEXT NOT NULL CHECK (setup_type IN ('fixed','grouped','per_unit')),
    display_mode             TEXT NOT NULL CHECK (display_mode IN ('fixed','grouped','per_unit')),
    effective_from           TEXT,
    reviewed_by              TEXT,
    reviewed_at              TEXT,
    approved_at              TEXT,
    status                   TEXT NOT NULL DEFAULT 'draft'
                             CHECK (status IN ('draft','approved','superseded')),
    allocation_readiness_status TEXT NOT NULL DEFAULT 'ok'
                             CHECK (allocation_readiness_status IN ('ok','needs_review')),
    version_int              INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_assessment_setups_property
    ON assessment_setups(property_id, status);

-- Allocation pool: one bucket of assessment dollars allocated by a
-- single method. pool_key is stable across setup supersessions so
-- BudgetLinePoolMapping survives setup rewrites.
CREATE TABLE IF NOT EXISTS allocation_pools (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_setup_id  INTEGER NOT NULL
                         REFERENCES assessment_setups(id) ON DELETE CASCADE,
    pool_key             TEXT NOT NULL,
    pool_name            TEXT NOT NULL,
    denominator_label    TEXT,
    declared_allocation_method TEXT,
    allocation_method    TEXT NOT NULL CHECK (allocation_method IN (
                             'equal','square_footage','ownership_percentage','specified_value','unresolved'
                         )),
    recipient_scope      TEXT NOT NULL CHECK (recipient_scope IN (
                             'all_units','residential_only','commercial_only',
                             'parking_users','custom_unit_list'
                         )),
    denominator_source   TEXT NOT NULL DEFAULT 'dre_value'
                         CHECK (denominator_source IN ('dre_value','calculated','manual')),
    denominator_value    NUMERIC,
    variable_flag        INTEGER NOT NULL DEFAULT 0,
    display_order        INTEGER NOT NULL DEFAULT 0,
    include_in_pdf       INTEGER NOT NULL DEFAULT 1,
    budget_line_derivation TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (budget_line_derivation IN (
                             'explicit_lines','residual_default','formula_only','unknown'
                         )),
    residual_after_pool_keys_json TEXT NOT NULL DEFAULT '[]',
    residual_exclusions_json      TEXT NOT NULL DEFAULT '[]',
    derivation_evidence_json      TEXT,
    -- 30-year forecast inputs migrated from hoa_settings (Task #185 of
    -- dre-driven-assessment-engine). When set on a pool, these supersede
    -- the deprecated hoa_settings columns of the same purpose.
    escalation_schedule_json     TEXT DEFAULT '[]',
    starting_monthly_per_unit    REAL,
    -- Structural classifier (add-variable-special-assessments). NULL = ordinary
    -- pool; 'separately_billed_special_assessment' = a special assessment the
    -- engine allocates as a one-time total, out of monthly dues. Never inferred
    -- from pool_name (which is AI-generated and varies per HOA).
    pool_kind                    TEXT,
    UNIQUE (assessment_setup_id, pool_key)
);

CREATE INDEX IF NOT EXISTS idx_allocation_pools_setup
    ON allocation_pools(assessment_setup_id);

-- AssessmentGroup: used by grouped HOAs (Esprit Park-style); unused
-- for fixed and per-unit setups.
CREATE TABLE IF NOT EXISTS assessment_groups (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_setup_id  INTEGER NOT NULL
                         REFERENCES assessment_setups(id) ON DELETE CASCADE,
    group_name           TEXT NOT NULL,
    unit_count           INTEGER NOT NULL,
    average_square_feet  NUMERIC,
    ownership_percent    NUMERIC,
    dre_factor           NUMERIC,
    display_order        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_assessment_groups_setup
    ON assessment_groups(assessment_setup_id);

-- AssessmentUnit: per-unit HOAs (800 High-style); also unused for
-- fixed HOAs. specified_monthly_amount is a CACHED total of the
-- unit's AssessmentUnitPoolAllocation rows; consumers should compute
-- from child rows when present.
CREATE TABLE IF NOT EXISTS assessment_units (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_setup_id      INTEGER NOT NULL
                             REFERENCES assessment_setups(id) ON DELETE CASCADE,
    unit_number              TEXT NOT NULL,
    square_feet              NUMERIC,
    ownership_percent        NUMERIC,
    category                 TEXT CHECK (category IN ('residential','commercial','mixed')),
    parking_spaces           INTEGER NOT NULL DEFAULT 0,
    custom_factor            NUMERIC,
    specified_monthly_amount NUMERIC,
    source                   TEXT NOT NULL DEFAULT 'dre'
                             CHECK (source IN ('dre','manual')),
    UNIQUE (assessment_setup_id, unit_number)
);

CREATE INDEX IF NOT EXISTS idx_assessment_units_setup
    ON assessment_units(assessment_setup_id);

-- AssessmentUnitPoolAllocation: multi-pool per-unit specified values
-- (e.g. 800 High needs one row per unit per specified_value pool with
-- its own monthly amount and provenance). The engine consumes these
-- directly when computing specified_value pool allocations.
CREATE TABLE IF NOT EXISTS assessment_unit_pool_allocations (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_unit_id       INTEGER NOT NULL
                             REFERENCES assessment_units(id) ON DELETE CASCADE,
    assessment_setup_id      INTEGER NOT NULL
                             REFERENCES assessment_setups(id) ON DELETE CASCADE,
    pool_key                 TEXT NOT NULL,
    pool_id                  INTEGER REFERENCES allocation_pools(id) ON DELETE SET NULL,
    specified_monthly_amount NUMERIC NOT NULL,
    source                   TEXT NOT NULL DEFAULT 'dre'
                             -- 'dre': extracted from the document; 'manual':
                             -- operator-entered; 'equal_split_placeholder':
                             -- auto-generated equal split awaiting operator
                             -- resolution (blocks package generation, C7).
                             CHECK (source IN ('dre','manual','equal_split_placeholder')),
    source_page              INTEGER,
    notes                    TEXT,
    UNIQUE (assessment_unit_id, pool_key)
);

CREATE INDEX IF NOT EXISTS idx_assessment_unit_pool_alloc_setup
    ON assessment_unit_pool_allocations(assessment_setup_id);

-- BudgetLinePoolMapping: AI-suggested + human-approved routing of
-- budget lines to pools. Disambiguating key is
-- (property_id, assessment_setup_id, normalized_label, section,
--  category, fund_type, account_code) plus active=1.
CREATE TABLE IF NOT EXISTS budget_line_pool_mappings (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id                   INTEGER NOT NULL
                                  REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id           INTEGER NOT NULL
                                  REFERENCES assessment_setups(id) ON DELETE CASCADE,
    budget_line_normalized_label  TEXT NOT NULL,
    section                       TEXT NOT NULL,
    category                      TEXT NOT NULL CHECK (category IN (
                                      'income','operating','reserve_income','reserve_expense'
                                  )),
    fund_type                     TEXT NOT NULL CHECK (fund_type IN ('operating','reserve')),
    account_code                  TEXT,
    pool_key                      TEXT NOT NULL,
    pool_id                       INTEGER REFERENCES allocation_pools(id) ON DELETE SET NULL,
    source_rule_id                INTEGER REFERENCES assessment_budget_mapping_rules(id) ON DELETE SET NULL,
    mapping_source                TEXT NOT NULL DEFAULT 'operator'
                                  CHECK (mapping_source IN (
                                      'operator','account_code',
                                      'normalized_label','dre_included_label',
                                      'remainder','residual_default',
                                      'alias'
                                  )),
    match_method                  TEXT,
    approval_status               TEXT NOT NULL DEFAULT 'approved'
                                  CHECK (approval_status IN (
                                      'auto_approved','suggested','approved',
                                      'rejected','disabled'
                                  )),
    review_state                  TEXT NOT NULL DEFAULT 'ready'
                                  CHECK (review_state IN (
                                      'ready','pending_review','conflict',
                                      'disabled','rejected','stale'
                                  )),
    budget_line_amount            NUMERIC,
    approved_by                   TEXT,
    approved_at                   TEXT,
    active                        INTEGER NOT NULL DEFAULT 1
);

-- NULL account_code must collide with NULL account_code (SQLite's
-- default UNIQUE treats NULLs as distinct). Use COALESCE so two rows
-- with the same disambiguating key but no account_code conflict.
CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_line_pool_mappings_disambig
    ON budget_line_pool_mappings(
        property_id, assessment_setup_id, budget_line_normalized_label,
        section, category, fund_type, COALESCE(account_code, '')
    );

CREATE INDEX IF NOT EXISTS idx_budget_line_pool_mappings_setup
    ON budget_line_pool_mappings(assessment_setup_id, active);

CREATE TABLE IF NOT EXISTS allocation_resolutions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id                 INTEGER NOT NULL
                                REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id         INTEGER NOT NULL
                                REFERENCES assessment_setups(id) ON DELETE CASCADE,
    pool_key                    TEXT NOT NULL,
    version_int                 INTEGER NOT NULL DEFAULT 1,
    status                      TEXT NOT NULL DEFAULT 'unresolved'
                                CHECK (status IN ('unresolved','draft','approved','superseded')),
    declared_method             TEXT NOT NULL,
    declared_denominator_label  TEXT,
    referenced_schedule_type    TEXT,
    referenced_schedule_name    TEXT,
    included_categories_json    TEXT NOT NULL DEFAULT '[]',
    excluded_categories_json    TEXT NOT NULL DEFAULT '[]',
    source_pages_json           TEXT NOT NULL DEFAULT '[]',
    source_evidence_text        TEXT,
    resolved_method             TEXT,
    denominator_value           NUMERIC,
    denominator_source          TEXT,
    factor_snapshot_json        TEXT NOT NULL DEFAULT '{}',
    evidence_document_id        INTEGER,
    prior_schedule_package_id   INTEGER,
    reason                      TEXT,
    created_by                  TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    approved_by                 TEXT,
    approved_at                 TEXT,
    source                      TEXT NOT NULL DEFAULT 'promotion'
                                CHECK (source IN ('promotion','operator','migration')),
    UNIQUE (assessment_setup_id, pool_key, version_int)
);

CREATE INDEX IF NOT EXISTS idx_allocation_resolutions_setup
    ON allocation_resolutions(assessment_setup_id, pool_key, status);

CREATE TABLE IF NOT EXISTS budget_line_allocation_slices (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id                     INTEGER NOT NULL
                                    REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id             INTEGER NOT NULL
                                    REFERENCES assessment_setups(id) ON DELETE CASCADE,
    source_line_normalized_label    TEXT NOT NULL,
    source_line_key                  TEXT,
    source_line_account_code        TEXT,
    source_annual_amount            NUMERIC NOT NULL,
    slice_annual_amount             NUMERIC NOT NULL,
    slice_percent                   NUMERIC,
    pool_key                        TEXT NOT NULL,
    semantic_category               TEXT NOT NULL DEFAULT '',
    status                          TEXT NOT NULL DEFAULT 'draft'
                                    CHECK (status IN ('draft','approved','superseded')),
    evidence_text                   TEXT,
    reason                          TEXT,
    created_by                      TEXT,
    created_at                      TEXT NOT NULL DEFAULT (datetime('now')),
    approved_by                     TEXT,
    approved_at                     TEXT
);

CREATE INDEX IF NOT EXISTS idx_budget_line_allocation_slices_setup
    ON budget_line_allocation_slices(assessment_setup_id, source_line_normalized_label, status);

CREATE TABLE IF NOT EXISTS allocation_category_decisions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id             INTEGER NOT NULL
                            REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id     INTEGER NOT NULL
                            REFERENCES assessment_setups(id) ON DELETE CASCADE,
    pool_key                TEXT NOT NULL,
    category                TEXT NOT NULL,
    decision                TEXT NOT NULL
                            CHECK (decision IN ('mapped','zero','not_applicable')),
    mapped_amount           NUMERIC,
    evidence_text           TEXT,
    reason                  TEXT,
    created_by              TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (assessment_setup_id, pool_key, category)
);

CREATE TABLE IF NOT EXISTS assessment_mapping_aliases (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id               INTEGER NOT NULL
                              REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id       INTEGER NOT NULL
                              REFERENCES assessment_setups(id) ON DELETE CASCADE,
    pool_key                  TEXT NOT NULL,
    dre_label                 TEXT NOT NULL,
    normalized_dre_label      TEXT NOT NULL,
    budget_label              TEXT NOT NULL,
    normalized_budget_label   TEXT NOT NULL,
    account_code              TEXT,
    approval_status           TEXT NOT NULL DEFAULT 'approved'
                              CHECK (approval_status IN (
                                  'approved','revoked','disabled'
                              )),
    active                    INTEGER NOT NULL DEFAULT 1,
    decided_by                TEXT NOT NULL,
    decided_at                TEXT NOT NULL DEFAULT (datetime('now')),
    note                      TEXT,
    created_at                TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_assessment_mapping_aliases_scope
    ON assessment_mapping_aliases(
        property_id, assessment_setup_id, pool_key,
        normalized_dre_label, normalized_budget_label,
        COALESCE(account_code, '')
    );

-- ── DRE document vault (Phase 3.1) ──
-- One row per uploaded DRE PDF; new uploads supersede prior ones via
-- supersedes_id. The runtime stores files under
-- BUDGET_STORAGE_ROOT/dre/<property_id>/ — file_id is the relative path.
CREATE TABLE IF NOT EXISTS dre_documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    property_id     INTEGER NOT NULL
                    REFERENCES properties(id) ON DELETE CASCADE,
    file_id         TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    page_count      INTEGER,
    supersedes_id   INTEGER REFERENCES dre_documents(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','superseded','retired')),
    uploaded_by     TEXT,
    uploaded_at     TEXT NOT NULL DEFAULT (datetime('now')),
    -- Discriminates DRE setup documents from CC&R / governing documents that
    -- share this table. CC&R rows are tagged 'ccr'; the brownfield migration
    -- backfills existing rows to 'dre'. DRE endpoints filter on 'dre',
    -- CC&R endpoints on 'ccr'. See ensure_dre_documents_columns().
    document_type   TEXT NOT NULL DEFAULT 'dre'
);

CREATE INDEX IF NOT EXISTS idx_dre_documents_property
    ON dre_documents(property_id, status);

-- DRE extraction run: one row per Gemini extraction attempt. Mirrors
-- the in-memory DREExtractionRunRecord (backend/app/dre_extraction).
-- review_status='promoted' means the operator approved the draft and
-- promoted_setup_id was assigned the resulting AssessmentSetup row.
CREATE TABLE IF NOT EXISTS dre_extraction_runs (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    dre_document_id            INTEGER NOT NULL
                               REFERENCES dre_documents(id) ON DELETE CASCADE,
    property_id                INTEGER NOT NULL
                               REFERENCES properties(id) ON DELETE CASCADE,
    started_at                 TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at               TEXT,
    model_name                 TEXT NOT NULL,
    prompt_version             TEXT NOT NULL,
    prompt_sha256              TEXT NOT NULL,
    raw_model_output           TEXT,
    parsed_json                TEXT,
    schema_validation_errors   TEXT NOT NULL DEFAULT '[]',
    repair_attempt_count       INTEGER NOT NULL DEFAULT 0,
    citation_audit_json        TEXT NOT NULL DEFAULT '[]',
    low_confidence_flags_json  TEXT NOT NULL DEFAULT '[]',
    validation_warnings_json   TEXT NOT NULL DEFAULT '[]',
    job_status                 TEXT NOT NULL DEFAULT 'completed'
                               CHECK (job_status IN ('queued','running','completed','failed')),
    status                     TEXT NOT NULL DEFAULT 'failed'
                               CHECK (status IN ('succeeded','extraction_partial','failed')),
    review_status              TEXT NOT NULL DEFAULT 'pending'
                               CHECK (review_status IN ('pending','approved','rejected','promoted')),
    promoted_setup_id          INTEGER REFERENCES assessment_setups(id) ON DELETE SET NULL,
    promoted_at                TEXT,
    reviewed_by                TEXT,
    error_message              TEXT NOT NULL DEFAULT '',
    -- SHA-256 of WireDRESetupExtraction.model_json_schema() at the time
    -- of this extraction run. Surfaces wire-schema drift independently of
    -- prompt drift; empty string means a pre-structured-output run.
    wire_schema_sha256         TEXT NOT NULL DEFAULT '',
    -- Audit-trail capture for the Gemini call itself. ``model_name`` above
    -- records what we ASKED for (often an alias like 'gemini-flash-latest');
    -- ``model_version_resolved`` records what Gemini's response.model_version
    -- actually resolved to, so when the alias rotates the audit row remains
    -- unambiguous. finish_reason captures STOP / MAX_TOKENS / SAFETY / etc.
    -- output_tokens_used is the candidates_token_count from usage_metadata.
    model_version_resolved     TEXT NOT NULL DEFAULT '',
    finish_reason              TEXT NOT NULL DEFAULT '',
    output_tokens_used         INTEGER NOT NULL DEFAULT 0,
    -- Discriminates DRE extraction runs from CC&R / governing-document runs
    -- that share this table (mirrors dre_documents.document_type).
    document_type              TEXT NOT NULL DEFAULT 'dre',
    -- Operator-entered per-unit allocation factors (square footage / ownership
    -- percentage) for CC&R promotion, keyed by unit number. CC&Rs reference
    -- allocation bases but rarely carry machine-readable per-unit data, so the
    -- operator supplies it before promotion. Empty object for DRE runs.
    operator_unit_factors_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_dre_extraction_runs_doc
    ON dre_extraction_runs(dre_document_id);
CREATE INDEX IF NOT EXISTS idx_dre_extraction_runs_property
    ON dre_extraction_runs(property_id, review_status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dre_extraction_runs_active_doc
    ON dre_extraction_runs(dre_document_id)
    WHERE job_status IN ('queued', 'running');

-- Reusable rules derived from approved DRE setup evidence. These are
-- stable, setup-level rules; annual budget_line_pool_mappings are
-- materialized from them for each budget draft/year.
CREATE TABLE IF NOT EXISTS assessment_budget_mapping_rules (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id                   INTEGER NOT NULL
                                  REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id           INTEGER NOT NULL
                                  REFERENCES assessment_setups(id) ON DELETE CASCADE,
    pool_key                      TEXT NOT NULL,
    pool_id                       INTEGER REFERENCES allocation_pools(id) ON DELETE SET NULL,
    match_label                   TEXT,
    normalized_label              TEXT,
    account_code                  TEXT,
    match_type                    TEXT NOT NULL CHECK (match_type IN (
                                      'exact_label','normalized_label',
                                      'account_code','category','remainder'
                                  )),
    rule_source                   TEXT NOT NULL CHECK (rule_source IN (
                                      'dre_included_budget_line','operator',
                                      'carried_forward','system_remainder',
                                      'dre_mapping_evidence'
                                  )),
    approval_status               TEXT NOT NULL DEFAULT 'suggested'
                                  CHECK (approval_status IN (
                                      'auto_approved','suggested','approved',
                                      'rejected','disabled'
                                  )),
    review_state                  TEXT NOT NULL DEFAULT 'pending_review'
                                  CHECK (review_state IN (
                                      'ready','pending_review','conflict',
                                      'disabled','rejected'
                                  )),
    active                        INTEGER NOT NULL DEFAULT 1,
    source_dre_extraction_run_id  INTEGER REFERENCES dre_extraction_runs(id) ON DELETE SET NULL,
    source_pages_json             TEXT NOT NULL DEFAULT '[]',
    confidence                    NUMERIC,
    source_parent_category        TEXT,
    assessment_type               TEXT NOT NULL DEFAULT 'unknown_needs_review'
                                  CHECK (assessment_type IN (
                                      'equal_base','prorated_variable',
                                      'square_footage','ownership_percent',
                                      'exemption_credit','subsidy_credit',
                                      'pass_through','reserve_component',
                                      'excluded_or_informational',
                                      'unknown_needs_review'
                                  )),
    review_required               INTEGER NOT NULL DEFAULT 0,
    review_reason                 TEXT NOT NULL DEFAULT '',
    source_evidence_text          TEXT NOT NULL DEFAULT '',
    budget_line_derivation        TEXT NOT NULL DEFAULT 'unknown'
                                  CHECK (budget_line_derivation IN (
                                      'explicit_lines','residual_default',
                                      'formula_only','unknown'
                                  )),
    residual_after_pool_keys_json TEXT NOT NULL DEFAULT '[]',
    residual_exclusions_json      TEXT NOT NULL DEFAULT '[]',
    structural_signature          TEXT,
    created_at                    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_assessment_budget_mapping_rules_setup
    ON assessment_budget_mapping_rules(assessment_setup_id, active, approval_status);

CREATE INDEX IF NOT EXISTS idx_assessment_budget_mapping_rules_property
    ON assessment_budget_mapping_rules(property_id, active);

CREATE UNIQUE INDEX IF NOT EXISTS uq_assessment_budget_mapping_rules_identity
    ON assessment_budget_mapping_rules(
        property_id, assessment_setup_id, pool_key, match_type,
        COALESCE(normalized_label, ''), COALESCE(account_code, '')
    );

-- Current-year applicability for temporary or condition-based DRE
-- exemptions such as Civil Code / DRE Regulation 2792.16(c).
CREATE TABLE IF NOT EXISTS assessment_exemption_decisions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id           INTEGER NOT NULL
                          REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id   INTEGER NOT NULL
                          REFERENCES assessment_setups(id) ON DELETE CASCADE,
    budget_year           INTEGER,
    budget_draft_id       INTEGER REFERENCES budget_drafts(id) ON DELETE CASCADE,
    pool_key              TEXT NOT NULL,
    exemption_state       TEXT NOT NULL DEFAULT 'pending_review'
                          CHECK (exemption_state IN (
                              'pending_review','active','inactive'
                          )),
    operator_decision     TEXT,
    decided_by            TEXT,
    decided_at            TEXT,
    notes                 TEXT,
    evidence_ref_json     TEXT NOT NULL DEFAULT '[]',
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (budget_year IS NOT NULL OR budget_draft_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_assessment_exemption_decisions_scope
    ON assessment_exemption_decisions(
        property_id, assessment_setup_id, pool_key,
        COALESCE(budget_year, -1), COALESCE(budget_draft_id, -1)
    );

CREATE INDEX IF NOT EXISTS idx_assessment_exemption_decisions_setup
    ON assessment_exemption_decisions(assessment_setup_id, exemption_state);

CREATE TABLE IF NOT EXISTS assessment_review_row_dispositions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id           INTEGER NOT NULL
                          REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id   INTEGER NOT NULL
                          REFERENCES assessment_setups(id) ON DELETE CASCADE,
    budget_year           INTEGER,
    budget_draft_id       INTEGER REFERENCES budget_drafts(id) ON DELETE CASCADE,
    review_line_key       TEXT NOT NULL,
    normalized_label      TEXT NOT NULL DEFAULT '',
    line_label            TEXT NOT NULL DEFAULT '',
    row_role              TEXT NOT NULL DEFAULT 'unknown_needs_review',
    disposition_state     TEXT NOT NULL DEFAULT 'clear'
                          CHECK (disposition_state IN (
                              'excluded_non_regular','reserve_detail',
                              'pending_split','clear'
                          )),
    decided_by            TEXT,
    decided_at            TEXT,
    notes                 TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (budget_year IS NOT NULL OR budget_draft_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_assessment_review_row_dispositions_scope
    ON assessment_review_row_dispositions(
        property_id, assessment_setup_id, review_line_key,
        COALESCE(budget_year, -1), COALESCE(budget_draft_id, -1)
    );

CREATE INDEX IF NOT EXISTS idx_assessment_review_row_dispositions_setup
    ON assessment_review_row_dispositions(
        assessment_setup_id, disposition_state
    );

CREATE TABLE IF NOT EXISTS assessment_review_row_audit_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id           INTEGER NOT NULL
                          REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id   INTEGER NOT NULL
                          REFERENCES assessment_setups(id) ON DELETE CASCADE,
    budget_year           INTEGER,
    budget_draft_id       INTEGER REFERENCES budget_drafts(id) ON DELETE CASCADE,
    review_line_key       TEXT NOT NULL,
    normalized_label      TEXT NOT NULL DEFAULT '',
    line_label            TEXT NOT NULL DEFAULT '',
    change_type           TEXT NOT NULL
                          CHECK (change_type IN ('assignment','disposition')),
    previous_value        TEXT,
    new_value             TEXT,
    pool_key              TEXT,
    actor                 TEXT NOT NULL,
    reason                TEXT,
    source                TEXT NOT NULL DEFAULT 'operator',
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_assessment_review_row_audit_events_scope
    ON assessment_review_row_audit_events(
        assessment_setup_id, review_line_key, created_at
    );

-- ── Assessment override (Phase 4.5) ──
-- Operator-applied adjustments that win over engine-calculated math.
-- Applied AFTER recipient summation+rounding; original_calculated_amount
-- is preserved for audit. Internal-only; never rendered in the
-- homeowner PDF.
CREATE TABLE IF NOT EXISTS assessment_overrides (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id                  INTEGER NOT NULL,
    scope                       TEXT NOT NULL
                                CHECK (scope IN ('package','group','unit','pool')),
    scope_ref_id                INTEGER,
    override_type               TEXT NOT NULL
                                CHECK (override_type IN (
                                    'board_approved','rounding',
                                    'manual_correction','spreadsheet'
                                )),
    original_calculated_amount  NUMERIC,
    override_amount             NUMERIC NOT NULL,
    delta                       NUMERIC,
    reason                      TEXT,
    approved_by                 TEXT,
    approved_at                 TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_assessment_overrides_package
    ON assessment_overrides(package_id, scope);

-- ── DRE review edit log (Phase 4.6) ──
-- One row per change made in the Review Workbench. Captures the path,
-- old/new values, and who/when. Internal audit only.
CREATE TABLE IF NOT EXISTS dre_review_edits (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    dre_extraction_run_id       INTEGER NOT NULL
                                REFERENCES dre_extraction_runs(id) ON DELETE CASCADE,
    field_path                  TEXT NOT NULL,
    old_value                   TEXT,
    new_value                   TEXT,
    reason                      TEXT,
    edited_by                   TEXT,
    edited_at                   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dre_review_edits_run
    ON dre_review_edits(dre_extraction_run_id, edited_at);

-- ── Per-field source evidence (Phase 4.7) ──
-- For high-risk fields (denominator_value, allocation_method, unit_count,
-- variable_flag, ownership_percent, assessment amounts), the extraction
-- pipeline records a field-level source pointer beyond the entity-level
-- citation. Other leaf fields may inherit from the containing entity.
CREATE TABLE IF NOT EXISTS extracted_field_sources (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    dre_extraction_run_id       INTEGER NOT NULL
                                REFERENCES dre_extraction_runs(id) ON DELETE CASCADE,
    entity_type                 TEXT NOT NULL,
    entity_id                   TEXT,
    field_name                  TEXT NOT NULL,
    page_number                 INTEGER NOT NULL,
    bounding_box_json           TEXT,
    confidence                  NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_extracted_field_sources_run
    ON extracted_field_sources(dre_extraction_run_id, entity_type);

-- ── Annual disclosure package (Phase 4.8 + 5.8 + 5.9) ──
-- Finalization snapshots: when a package transitions to 'finalized',
-- the four *_snapshot_json columns are frozen in a single transaction
-- so a future re-render uses the same inputs even if live state has
-- drifted. ``regen_of_package_id`` points at the original package for
-- regeneration workflows; the new row gets its own finalization.
--
-- ``approved_assessment_revenue_annual`` is the operator-approved
-- target the engine reconciles against. ``tenant_id`` is reserved
-- for v2 multi-tenant rollout (all rows default to 1 in v1).
-- ``version_int`` is the optimistic-lock counter for concurrent edit
-- isolation; clients send If-Match on PUT/PATCH and the backend 409s
-- on mismatch.
CREATE TABLE IF NOT EXISTS annual_packages (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id                       INTEGER NOT NULL DEFAULT 1,
    property_id                     INTEGER NOT NULL
                                    REFERENCES properties(id) ON DELETE CASCADE,
    assessment_setup_id             INTEGER REFERENCES assessment_setups(id) ON DELETE SET NULL,
    assessment_mode                 TEXT NOT NULL DEFAULT 'variable'
                                    CHECK (assessment_mode IN ('fixed', 'variable')),
    budget_year                     INTEGER NOT NULL,
    fiscal_year                     INTEGER NOT NULL,
    letter_date                     TEXT,
    effective_date                  TEXT,
    approved_assessment_revenue_annual NUMERIC,
    approved_by                     TEXT,
    approved_at                     TEXT,
    status                          TEXT NOT NULL DEFAULT 'draft'
                                    CHECK (status IN (
                                        'draft','preflight_failed','approved',
                                        'rendered','finalized'
                                    )),
    rounding_delta_annual           NUMERIC,
    rounding_delta_monthly          NUMERIC,
    rounding_delta_percent          NUMERIC,
    -- Finalization snapshots (Phase 4.8). Frozen at status → finalized.
    -- Serialize with sort_keys=True for byte-equal re-renders.
    assessment_setup_snapshot_json  TEXT,
    budget_snapshot_json            TEXT,
    reserve_snapshot_json           TEXT,
    appendix_manifest_snapshot_json TEXT,
    -- Fifth snapshot (C2/C1): {assessment_matrix, hoa_metadata,
    -- hoa_settings_overrides, assessment_revenue_annual} so a finalized
    -- render reads NO live state beyond the snapshot columns.
    compile_context_snapshot_json   TEXT,
    finalized_at                    TEXT,
    regen_of_package_id             INTEGER REFERENCES annual_packages(id) ON DELETE SET NULL,
    version_int                     INTEGER NOT NULL DEFAULT 0,
    created_at                      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (property_id, fiscal_year, regen_of_package_id)
);

CREATE INDEX IF NOT EXISTS idx_annual_packages_property
    ON annual_packages(property_id, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_annual_packages_status
    ON annual_packages(status);

-- ── Persistent appendix manifest (Phase 5.4 + 5.6) ──
-- Per-HOA appendix vault: operator uploads once; every annual package
-- inherits. Each year the operator can override (exclude, reorder,
-- swap a one-off) via the AnnualPackageAppendix junction without
-- losing the saved row. ``cadence`` controls re-use:
--   persistent — same file used every year (ADR/IDR policy, election rules)
--   annual     — fresh upload required for the package year (insurance disclosures)
--   one_time   — used for one package only (legal exhibits)
-- ``needs_cadence_review`` is set during migration when the cadence
-- can't be inferred confidently; the Settings UI shows a "Review
-- cadence" badge until the operator confirms.
CREATE TABLE IF NOT EXISTS appendix_documents (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id                INTEGER NOT NULL DEFAULT 1,
    property_id              INTEGER NOT NULL
                             REFERENCES properties(id) ON DELETE CASCADE,
    file_id                  TEXT NOT NULL,
    file_name                TEXT NOT NULL,
    display_title            TEXT NOT NULL,
    default_display_order    INTEGER NOT NULL DEFAULT 0,
    required_flag            INTEGER NOT NULL DEFAULT 0,
    include_by_default       INTEGER NOT NULL DEFAULT 1,
    cadence                  TEXT NOT NULL DEFAULT 'persistent'
                             CHECK (cadence IN ('persistent','annual','one_time')),
    annual_year              INTEGER,
    valid_through_year       INTEGER,
    needs_cadence_review     INTEGER NOT NULL DEFAULT 0,
    status                   TEXT NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active','retired')),
    uploaded_by              TEXT,
    uploaded_at              TEXT NOT NULL DEFAULT (datetime('now')),
    version_int              INTEGER NOT NULL DEFAULT 0,
    -- NULL | 'insurance' — placed after insurance disclosure cover in the package
    package_role             TEXT
);

CREATE INDEX IF NOT EXISTS idx_appendix_documents_property
    ON appendix_documents(property_id, status);
CREATE INDEX IF NOT EXISTS idx_appendix_documents_cadence
    ON appendix_documents(cadence, annual_year);

-- Junction table: per-package overrides of the inherited manifest.
-- If no rows exist for a package, the compile picks up every
-- ``appendix_documents.include_by_default = 1`` row for the HOA.
CREATE TABLE IF NOT EXISTS annual_package_appendices (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id               INTEGER NOT NULL
                             REFERENCES annual_packages(id) ON DELETE CASCADE,
    appendix_id              INTEGER NOT NULL
                             REFERENCES appendix_documents(id) ON DELETE CASCADE,
    display_order            INTEGER NOT NULL DEFAULT 0,
    included                 INTEGER NOT NULL DEFAULT 1,
    override_display_title   TEXT,
    UNIQUE (package_id, appendix_id)
);

CREATE INDEX IF NOT EXISTS idx_annual_package_appendices_package
    ON annual_package_appendices(package_id, display_order);
