"""SQLite database connection, schema initialization, and write serialization."""
import sqlite3
import threading
import logging
from pathlib import Path
from .config import settings

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

SCHEMA_SQL = """
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

CREATE INDEX IF NOT EXISTS idx_feedback_account ON feedback_cases(account_code, property_id);
CREATE INDEX IF NOT EXISTS idx_feedback_temporal ON feedback_cases(created_at);
CREATE INDEX IF NOT EXISTS idx_runs_property ON suggestion_runs(property_id, created_at);
"""

# Partial index syntax not supported in all SQLite versions, so use a standard index
TRAINING_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_feedback_training ON feedback_cases(user_decision, created_at);
"""


def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection with WAL mode and row factory."""
    db_path = Path(settings.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Initialize database schema. Called on application startup."""
    logger.info("Initializing AI pipeline database...")
    conn = get_db()
    try:
        conn.executescript(SCHEMA_SQL)
        try:
            conn.executescript(TRAINING_INDEX_SQL)
        except sqlite3.OperationalError:
            pass  # Index may already exist
        conn.commit()
        logger.info("Database schema initialized.")
    finally:
        conn.close()


def write_lock():
    """Return the write serialization lock for use in run_in_threadpool callers."""
    return _write_lock
