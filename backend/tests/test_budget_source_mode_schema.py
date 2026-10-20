from sqlalchemy import create_engine

from app.ai_implementation import database as database_module
from app.ai_implementation.db import session as session_module


def test_legacy_source_mode_columns_backfill_to_income_statement(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-source-mode.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(
            """
            CREATE TABLE budget_uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_role TEXT,
                original_filename TEXT
            );
            INSERT INTO budget_uploads (document_role, original_filename)
            VALUES ('budget_source', 'legacy-upload.xlsx');

            CREATE TABLE budget_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT,
                line_items_json TEXT
            );
            INSERT INTO budget_drafts (status, line_items_json)
            VALUES ('active', '[]');

            CREATE TABLE budget_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_number INTEGER,
                version_code TEXT
            );
            INSERT INTO budget_versions (version_number, version_code)
            VALUES (1, 'V1');
            """
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(session_module, "engine", engine)

    database_module.ensure_budget_upload_columns()
    database_module.ensure_budget_draft_columns()
    database_module.ensure_budget_version_columns()

    verify_conn = engine.raw_connection()
    try:
        assert verify_conn.execute("SELECT source_mode FROM budget_uploads").fetchone()[0] == "income_statement"
        assert verify_conn.execute("SELECT source_mode FROM budget_drafts").fetchone()[0] == "income_statement"
        assert verify_conn.execute("SELECT source_mode FROM budget_versions").fetchone()[0] == "income_statement"
    finally:
        verify_conn.close()
