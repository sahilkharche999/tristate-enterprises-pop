"""SQLAlchemy engine and session factory for SQLite."""
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from ..config import settings

# Ensure the data directory exists
_db_path = Path(settings.DB_PATH)
_db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    """Enable WAL mode and foreign keys on every new connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session():
    """FastAPI dependency: yields a SQLAlchemy session, auto-closes."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
