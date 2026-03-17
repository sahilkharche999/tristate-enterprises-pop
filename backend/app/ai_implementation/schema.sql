PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS properties (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL UNIQUE,
    units                   INTEGER,
    fiscal_year_start_month INTEGER DEFAULT 1,
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_feedback_account ON feedback_cases(account_code, property_id);
CREATE INDEX IF NOT EXISTS idx_feedback_temporal ON feedback_cases(created_at);
CREATE INDEX IF NOT EXISTS idx_runs_property ON suggestion_runs(property_id, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_training ON feedback_cases(user_decision, created_at);
