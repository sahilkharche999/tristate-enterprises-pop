"""Database initialization and backward-compatible shims.

Schema source of truth: schema.sql (run on startup).
All new code should use db.session.get_session() instead of get_db().
"""
import contextlib
import logging
import sqlite3
from pathlib import Path

from .config import settings
from .db.session import engine

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


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
