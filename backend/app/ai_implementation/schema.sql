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
    created_at      TEXT DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_disclosure_jobs_property
    ON disclosure_package_jobs(property_id, created_at DESC);

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
