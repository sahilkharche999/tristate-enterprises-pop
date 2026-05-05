"""Database initialization and backward-compatible shims.

Schema source of truth: schema.sql (run on startup).
All new code should use db.session.get_session() instead of get_db().
"""
import contextlib
import logging
import sqlite3
from pathlib import Path
from typing import Iterable

from .config import settings
from .db.session import engine

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_PROPERTY_COLUMN_DEFINITIONS: dict[str, str] = {
    "hoa_code": "TEXT",
    "tax_id": "TEXT",
    "reserve_inflation_rate": "REAL DEFAULT 0",
    "fiscal_year_end_month": "INTEGER DEFAULT 12",
    "city": "TEXT",
    "portfolio_year": "INTEGER",
    "workflow_status": "TEXT DEFAULT 'Not Started'",
}

_BUDGET_DRAFT_COLUMN_DEFINITIONS: dict[str, str] = {
    "enriched_storage_key": "TEXT",
    "reserve_inflation_rate": "REAL DEFAULT 0",
    "reserve_inflation_note": "TEXT",
}

_BUDGET_VERSION_COLUMN_DEFINITIONS: dict[str, str] = {
    "reserve_inflation_rate": "REAL DEFAULT 0",
    "reserve_inflation_note": "TEXT",
}


def _iter_missing_property_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(properties)").fetchall()
    }
    for column_name, column_sql in _PROPERTY_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_property_columns() -> None:
    """Brownfield migration path for Phase 1 HOA settings columns."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_property_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing properties.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE properties ADD COLUMN {column_name} {column_sql}")

        raw_conn.execute(
            """
            UPDATE properties
               SET fiscal_year_end_month = CASE
                     WHEN fiscal_year_end_month IS NULL
                     THEN ((COALESCE(fiscal_year_start_month, 1) + 10) % 12) + 1
                     ELSE fiscal_year_end_month
                   END,
                   workflow_status = COALESCE(NULLIF(TRIM(workflow_status), ''), 'Not Started')
            """
        )
        raw_conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_properties_hoa_code ON properties(hoa_code)")
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_budget_draft_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(budget_drafts)").fetchall()
    }
    for column_name, column_sql in _BUDGET_DRAFT_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_budget_draft_columns() -> None:
    """Brownfield migration path for draft artifact persistence columns."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_budget_draft_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing budget_drafts.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE budget_drafts ADD COLUMN {column_name} {column_sql}")
        raw_conn.commit()
    finally:
        raw_conn.close()


def _iter_missing_budget_version_columns(raw_conn: sqlite3.Connection) -> Iterable[tuple[str, str]]:
    existing_columns = {
        row[1]
        for row in raw_conn.execute("PRAGMA table_info(budget_versions)").fetchall()
    }
    for column_name, column_sql in _BUDGET_VERSION_COLUMN_DEFINITIONS.items():
        if column_name not in existing_columns:
            yield column_name, column_sql


def ensure_budget_version_columns() -> None:
    """Brownfield migration path for version reserve inflation columns."""
    raw_conn = engine.raw_connection()
    try:
        missing_columns = list(_iter_missing_budget_version_columns(raw_conn))
        for column_name, column_sql in missing_columns:
            logger.info("Adding missing budget_versions.%s column", column_name)
            raw_conn.execute(f"ALTER TABLE budget_versions ADD COLUMN {column_name} {column_sql}")
        raw_conn.commit()
    finally:
        raw_conn.close()


def init_db() -> None:
    """Execute schema.sql to create tables. Safe to call on existing DB."""
    logger.info("Initializing database from schema.sql...")
    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(_SCHEMA_PATH.read_text())
        raw_conn.commit()
        logger.info("Database schema initialized.")
    finally:
        raw_conn.close()
    ensure_property_columns()
    ensure_budget_draft_columns()
    ensure_budget_version_columns()


# ── Backward-compatible shims (used by seed script) ──

def get_db() -> sqlite3.Connection:
    """DEPRECATED: Returns raw sqlite3 connection for seed script compatibility.
    New code should use db.get_session via Depends() instead."""
    db_path = Path(settings.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextlib.contextmanager
def write_lock():
    """DEPRECATED: No-op. SQLAlchemy session handles transactions."""
    yield
