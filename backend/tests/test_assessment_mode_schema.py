from pathlib import Path

from sqlalchemy import create_engine

from app.ai_implementation import database as database_module
from app.ai_implementation.db import session as session_module


def test_legacy_assessment_mode_columns_backfill_conservatively(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-assessment-mode.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(
            """
            CREATE TABLE properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                units INTEGER,
                fiscal_year_start_month INTEGER DEFAULT 1,
                default_assessment_setup_id INTEGER
            );
            INSERT INTO properties (id, name, units, default_assessment_setup_id)
            VALUES
                (1, 'Variable HOA', 20, NULL),
                (2, 'Fixed HOA', 10, NULL),
                (3, 'Ambiguous HOA', 15, NULL);

            CREATE TABLE hoa_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL UNIQUE,
                approved_monthly_assessment_per_unit REAL
            );
            INSERT INTO hoa_settings (property_id, approved_monthly_assessment_per_unit)
            VALUES
                (2, 605.00);

            CREATE TABLE assessment_setups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                status TEXT NOT NULL
            );
            INSERT INTO assessment_setups (id, property_id, status)
            VALUES (7, 1, 'approved');

            CREATE TABLE budget_uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                document_role TEXT,
                original_filename TEXT
            );
            INSERT INTO budget_uploads (property_id, document_role, original_filename)
            VALUES
                (1, 'budget_source', 'variable-upload.xlsx'),
                (2, 'budget_source', 'fixed-upload.xlsx'),
                (3, 'budget_source', 'ambiguous-upload.xlsx');

            CREATE TABLE budget_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                status TEXT,
                line_items_json TEXT
            );
            INSERT INTO budget_drafts (property_id, status, line_items_json)
            VALUES
                (1, 'active', '[]'),
                (2, 'active', '[]'),
                (3, 'active', '[]');

            CREATE TABLE budget_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                version_number INTEGER,
                version_code TEXT
            );
            INSERT INTO budget_versions (property_id, version_number, version_code)
            VALUES
                (1, 1, 'V1'),
                (2, 1, 'V1'),
                (3, 1, 'V1');
            """
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(session_module, "engine", engine)

    database_module.ensure_property_columns()
    database_module.ensure_budget_upload_columns()
    database_module.ensure_budget_draft_columns()
    database_module.ensure_budget_version_columns()

    verify_conn = engine.raw_connection()
    try:
        property_modes = verify_conn.execute(
            "SELECT id, assessment_mode FROM properties ORDER BY id"
        ).fetchall()
        assert property_modes == [
            (1, "variable"),
            (2, "fixed"),
            (3, "variable"),
        ]

        upload_modes = verify_conn.execute(
            "SELECT property_id, assessment_mode FROM budget_uploads ORDER BY property_id"
        ).fetchall()
        assert upload_modes == [
            (1, "variable"),
            (2, "fixed"),
            (3, "variable"),
        ]

        draft_modes = verify_conn.execute(
            "SELECT property_id, assessment_mode FROM budget_drafts ORDER BY property_id"
        ).fetchall()
        assert draft_modes == [
            (1, "variable"),
            (2, "fixed"),
            (3, "variable"),
        ]

        version_modes = verify_conn.execute(
            "SELECT property_id, assessment_mode FROM budget_versions ORDER BY property_id"
        ).fetchall()
        assert version_modes == [
            (1, "variable"),
            (2, "fixed"),
            (3, "variable"),
        ]
    finally:
        verify_conn.close()


def test_budget_upload_backfill_survives_when_properties_column_is_added_later(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "legacy-startup-order.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(
            """
            CREATE TABLE properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                units INTEGER,
                fiscal_year_start_month INTEGER DEFAULT 1,
                default_assessment_setup_id INTEGER
            );
            INSERT INTO properties (id, name, units)
            VALUES
                (1, 'Fixed HOA', 10),
                (2, 'Variable HOA', 20);

            CREATE TABLE hoa_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL UNIQUE,
                approved_monthly_assessment_per_unit REAL
            );
            INSERT INTO hoa_settings (property_id, approved_monthly_assessment_per_unit)
            VALUES (1, 605.00);

            CREATE TABLE assessment_setups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                status TEXT NOT NULL
            );
            INSERT INTO assessment_setups (property_id, status)
            VALUES (2, 'approved');

            CREATE TABLE budget_uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                document_role TEXT,
                original_filename TEXT
            );
            INSERT INTO budget_uploads (property_id, document_role, original_filename)
            VALUES
                (1, 'budget_source', 'fixed.xlsx'),
                (2, 'budget_source', 'variable.xlsx');
            """
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(session_module, "engine", engine)

    # Mirrors brownfield startup order from init_db(): budget columns may be
    # repaired before the properties table gets its new assessment_mode column.
    database_module.ensure_budget_upload_columns()
    database_module.ensure_property_columns()

    verify_conn = engine.raw_connection()
    try:
        upload_modes = verify_conn.execute(
            "SELECT property_id, assessment_mode FROM budget_uploads ORDER BY property_id"
        ).fetchall()
        assert upload_modes == [
            (1, "fixed"),
            (2, "variable"),
        ]
        property_modes = verify_conn.execute(
            "SELECT id, assessment_mode FROM properties ORDER BY id"
        ).fetchall()
        assert property_modes == [
            (1, "fixed"),
            (2, "variable"),
        ]
    finally:
        verify_conn.close()
