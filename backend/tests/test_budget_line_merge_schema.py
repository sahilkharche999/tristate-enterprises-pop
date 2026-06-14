from sqlalchemy import create_engine, event

from app.ai_implementation import database as database_module
from app.ai_implementation.db import session as session_module


def _column_names(connection, table_name: str) -> set[str]:
    rows = connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _index_names(connection, table_name: str) -> set[str]:
    rows = connection.exec_driver_sql(f"PRAGMA index_list({table_name})").fetchall()
    return {row[1] for row in rows}


def test_schema_creates_budget_line_merge_tables(session):
    connection = session.connection()

    assert "version_int" in _column_names(connection, "budget_drafts")

    merge_columns = _column_names(connection, "budget_line_merges")
    assert {
        "id",
        "tenant_id",
        "property_id",
        "primary_account_code",
        "primary_label",
        "primary_normalized_label",
        "secondary_account_code",
        "secondary_label",
        "secondary_normalized_label",
        "status",
        "decision_source",
        "actor",
        "created_at",
        "disabled_at",
        "updated_at",
    } <= merge_columns

    application_columns = _column_names(connection, "budget_line_merge_applications")
    assert {
        "id",
        "tenant_id",
        "merge_id",
        "property_id",
        "budget_draft_id",
        "assessment_setup_id",
        "source",
        "status",
        "match_strategy",
        "before_snapshot_json",
        "after_snapshot_json",
        "side_effect_snapshot_json",
        "actor",
        "created_at",
        "unmerged_at",
        "finalized_at",
    } <= application_columns

    assert "uq_budget_line_merges_active_rule" in _index_names(
        connection,
        "budget_line_merges",
    )
    assert "uq_budget_line_merge_applications_applied" in _index_names(
        connection,
        "budget_line_merge_applications",
    )


def test_brownfield_ensure_helpers_add_merge_schema(monkeypatch, tmp_path):
    db_path = tmp_path / "brownfield.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    event.listen(engine, "connect", session_module._set_sqlite_pragmas)
    monkeypatch.setattr(database_module, "engine", engine)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE budget_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                line_items_json TEXT NOT NULL,
                actor_name TEXT NOT NULL
            )
            """
        )

    database_module.ensure_budget_draft_columns()
    database_module.ensure_budget_line_merges_columns()
    database_module.ensure_budget_line_merge_applications_columns()

    with engine.connect() as connection:
        assert "version_int" in _column_names(connection, "budget_drafts")
        assert "budget_line_merges" in {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "budget_line_merge_applications" in {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "uq_budget_line_merges_active_rule" in _index_names(
            connection,
            "budget_line_merges",
        )
        assert "uq_budget_line_merge_applications_applied" in _index_names(
            connection,
            "budget_line_merge_applications",
        )


def test_init_db_adds_budget_draft_version_before_later_migration_failure(
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "partial-startup.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    event.listen(engine, "connect", session_module._set_sqlite_pragmas)
    monkeypatch.setattr(database_module, "engine", engine)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                hoa_code TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE budget_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                line_items_json TEXT NOT NULL,
                actor_name TEXT NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE dre_extraction_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dre_document_id INTEGER NOT NULL,
                property_id INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO properties (name, hoa_code) VALUES ('One', 'DUP')"
        )
        connection.exec_driver_sql(
            "INSERT INTO properties (name, hoa_code) VALUES ('Two', 'DUP')"
        )

    try:
        database_module.init_db()
    except Exception:
        pass

    with engine.connect() as connection:
        assert "version_int" in _column_names(connection, "budget_drafts")
        assert "job_status" in _column_names(connection, "dre_extraction_runs")
