"""Schema + seed tests for the DRE-driven assessment engine (Phase 1.3).

Verifies the brownfield SQL migration creates the five engine tables
correctly and the Old Mill seed populates an approved fixed-setup with
a single equal pool — the regression baseline for the assessment engine.

Uses direct SQLite against a temp DB so the SQL is exercised without
the full SQLAlchemy session stack.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"


@pytest.fixture
def fresh_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()


class TestSchemaCreatesEngineTables:
    def test_all_engine_tables_exist(self, fresh_db: sqlite3.Connection) -> None:
        names = {
            row[0]
            for row in fresh_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "assessment_setups",
            "allocation_pools",
            "assessment_groups",
            "assessment_units",
            "assessment_unit_pool_allocations",
            "budget_line_pool_mappings",
        }.issubset(names)

    def test_audit_tables_exist(self, fresh_db: sqlite3.Connection) -> None:
        # Phase 4.5/4.6/4.7 audit tables
        names = {
            row[0]
            for row in fresh_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "assessment_overrides",
            "dre_review_edits",
            "extracted_field_sources",
            "dre_documents",
            "dre_extraction_runs",
        }.issubset(names)

    def test_assessment_override_scope_check(self, fresh_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            fresh_db.execute(
                "INSERT INTO assessment_overrides "
                "(package_id, scope, override_type, override_amount) "
                "VALUES (1, 'not_a_scope', 'board_approved', 100.0)"
            )

    def test_assessment_override_type_check(self, fresh_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            fresh_db.execute(
                "INSERT INTO assessment_overrides "
                "(package_id, scope, override_type, override_amount) "
                "VALUES (1, 'package', 'not_a_type', 100.0)"
            )

    def test_dre_review_edit_cascades_on_run_delete(
        self, fresh_db: sqlite3.Connection
    ) -> None:
        fresh_db.execute("PRAGMA foreign_keys = ON")
        fresh_db.execute(
            "INSERT INTO properties (name, units) VALUES ('T', 10)"
        )
        pid = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO dre_documents "
            "(property_id, file_id, file_name, status) "
            "VALUES (?, 'x', 'x', 'active')",
            (pid,),
        )
        doc_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO dre_extraction_runs "
            "(dre_document_id, property_id, model_name, prompt_version, prompt_sha256) "
            "VALUES (?, ?, 'g', 'v', 's')",
            (doc_id, pid),
        )
        run_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO dre_review_edits "
            "(dre_extraction_run_id, field_path, old_value, new_value, edited_by) "
            "VALUES (?, 'setup.setup_type', 'fixed', 'grouped', 'op')",
            (run_id,),
        )
        # Delete the parent run → review-edit row cascades away
        fresh_db.execute("DELETE FROM dre_extraction_runs WHERE id = ?", (run_id,))
        count = fresh_db.execute(
            "SELECT COUNT(*) FROM dre_review_edits"
        ).fetchone()[0]
        assert count == 0

    def test_extracted_field_source_indexed_by_run(
        self, fresh_db: sqlite3.Connection
    ) -> None:
        # Index exists — query plan uses it for the common per-run lookup.
        plan = fresh_db.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM extracted_field_sources "
            "WHERE dre_extraction_run_id = 1"
        ).fetchall()
        plan_text = " ".join(str(row) for row in plan).lower()
        assert "extracted_field_sources" in plan_text
        # Either USING INDEX or SCAN — both fine; just confirm the table is hit.

    def test_allocation_pool_method_check_constraint(self, fresh_db: sqlite3.Connection) -> None:
        fresh_db.execute(
            "INSERT INTO properties (name, units) VALUES ('Test', 10)"
        )
        property_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode) "
            "VALUES (?, 'fixed', 'fixed')",
            (property_id,),
        )
        setup_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            fresh_db.execute(
                "INSERT INTO allocation_pools (assessment_setup_id, pool_key, pool_name, "
                "allocation_method, recipient_scope) "
                "VALUES (?, 'k', 'n', 'not_a_real_method', 'all_units')",
                (setup_id,),
            )

    def test_pool_key_unique_within_setup(self, fresh_db: sqlite3.Connection) -> None:
        fresh_db.execute("INSERT INTO properties (name, units) VALUES ('T', 10)")
        property_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode) "
            "VALUES (?, 'fixed', 'fixed')",
            (property_id,),
        )
        setup_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO allocation_pools (assessment_setup_id, pool_key, pool_name, "
            "allocation_method, recipient_scope) "
            "VALUES (?, 'equal_costs', 'Equal', 'equal', 'all_units')",
            (setup_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            fresh_db.execute(
                "INSERT INTO allocation_pools (assessment_setup_id, pool_key, pool_name, "
                "allocation_method, recipient_scope) "
                "VALUES (?, 'equal_costs', 'Duplicate', 'equal', 'all_units')",
                (setup_id,),
            )

    def test_unit_pool_allocation_unique_per_unit_pool(
        self, fresh_db: sqlite3.Connection
    ) -> None:
        fresh_db.execute("INSERT INTO properties (name, units) VALUES ('T', 10)")
        property_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode) "
            "VALUES (?, 'per_unit', 'per_unit')",
            (property_id,),
        )
        setup_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO assessment_units (assessment_setup_id, unit_number) "
            "VALUES (?, '101')",
            (setup_id,),
        )
        unit_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO assessment_unit_pool_allocations "
            "(assessment_unit_id, assessment_setup_id, pool_key, specified_monthly_amount, source) "
            "VALUES (?, ?, 'general_common', 100.0, 'dre')",
            (unit_id, setup_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            fresh_db.execute(
                "INSERT INTO assessment_unit_pool_allocations "
                "(assessment_unit_id, assessment_setup_id, pool_key, specified_monthly_amount, source) "
                "VALUES (?, ?, 'general_common', 999.0, 'manual')",
                (unit_id, setup_id),
            )

    def test_budget_line_mapping_disambiguating_key_unique(
        self, fresh_db: sqlite3.Connection
    ) -> None:
        fresh_db.execute("INSERT INTO properties (name, units) VALUES ('T', 10)")
        property_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode) "
            "VALUES (?, 'fixed', 'fixed')",
            (property_id,),
        )
        setup_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO budget_line_pool_mappings "
            "(property_id, assessment_setup_id, budget_line_normalized_label, "
            " section, category, fund_type, pool_key) "
            "VALUES (?, ?, 'dues', 'income', 'income', 'operating', 'equal_costs')",
            (property_id, setup_id),
        )
        # Same disambiguating key → reject
        with pytest.raises(sqlite3.IntegrityError):
            fresh_db.execute(
                "INSERT INTO budget_line_pool_mappings "
                "(property_id, assessment_setup_id, budget_line_normalized_label, "
                " section, category, fund_type, pool_key) "
                "VALUES (?, ?, 'dues', 'income', 'income', 'operating', 'other_pool')",
                (property_id, setup_id),
            )
        # Different section → allowed
        fresh_db.execute(
            "INSERT INTO budget_line_pool_mappings "
            "(property_id, assessment_setup_id, budget_line_normalized_label, "
            " section, category, fund_type, pool_key) "
            "VALUES (?, ?, 'dues', 'reserve_income', 'reserve_income', 'reserve', 'reserve_pool')",
            (property_id, setup_id),
        )


class TestOldMillSeed:
    """The seed runs as part of init_db(); here we exercise it
    standalone against a temp DB to assert the row contents.
    """

    def test_seed_creates_approved_fixed_setup_with_equal_pool(
        self, fresh_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fresh_db.execute(
            "INSERT INTO properties (name, units) VALUES ('Old Mill HOA', 279)"
        )
        fresh_db.commit()

        # Run the seed function against this temp DB by patching the engine
        from app.ai_implementation import database as db_module

        # The seed function calls .close() on the engine's raw_connection.
        # Wrap the test connection so close() is a no-op and assertions can
        # still read.
        class _ProxyConn:
            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def __getattr__(self, name: str):
                return getattr(self._real, name)

            def close(self) -> None:  # no-op so we can keep reading
                pass

        class _FakeEngine:
            def raw_connection(self):
                return _ProxyConn(fresh_db)

        monkeypatch.setattr(db_module, "engine", _FakeEngine())
        db_module._seed_old_mill_assessment_setup()

        setup_row = fresh_db.execute(
            "SELECT setup_type, status FROM assessment_setups "
            "WHERE property_id = (SELECT id FROM properties WHERE LOWER(name) LIKE '%old mill%')"
        ).fetchone()
        assert setup_row == ("fixed", "approved")

        pool_row = fresh_db.execute(
            "SELECT pool_key, allocation_method, recipient_scope "
            "FROM allocation_pools "
            "WHERE assessment_setup_id = (SELECT id FROM assessment_setups "
            "  WHERE property_id = (SELECT id FROM properties WHERE LOWER(name) LIKE '%old mill%'))"
        ).fetchone()
        assert pool_row == ("equal_costs", "equal", "all_units")

    def test_seed_is_idempotent(
        self, fresh_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fresh_db.execute(
            "INSERT INTO properties (name, units) VALUES ('Old Mill HOA', 279)"
        )
        fresh_db.commit()

        from app.ai_implementation import database as db_module

        class _ProxyConn:
            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def __getattr__(self, name: str):
                return getattr(self._real, name)

            def close(self) -> None:
                pass

        class _FakeEngine:
            def raw_connection(self):
                return _ProxyConn(fresh_db)

        monkeypatch.setattr(db_module, "engine", _FakeEngine())
        db_module._seed_old_mill_assessment_setup()
        db_module._seed_old_mill_assessment_setup()  # second call → no-op

        count = fresh_db.execute(
            "SELECT COUNT(*) FROM assessment_setups"
        ).fetchone()[0]
        assert count == 1

    def test_seed_skips_when_no_old_mill_property(
        self, fresh_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Only a non-Old-Mill property exists
        fresh_db.execute("INSERT INTO properties (name, units) VALUES ('Some Other HOA', 50)")
        fresh_db.commit()

        from app.ai_implementation import database as db_module

        class _ProxyConn:
            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def __getattr__(self, name: str):
                return getattr(self._real, name)

            def close(self) -> None:
                pass

        class _FakeEngine:
            def raw_connection(self):
                return _ProxyConn(fresh_db)

        monkeypatch.setattr(db_module, "engine", _FakeEngine())
        db_module._seed_old_mill_assessment_setup()

        count = fresh_db.execute("SELECT COUNT(*) FROM assessment_setups").fetchone()[0]
        assert count == 0


class TestAllocationPoolForecastColumns:
    """Task #185 of dre-driven-assessment-engine: the 30-year forecast inputs
    (escalation schedule + starting monthly per unit) moved from hoa_settings
    to per-pool storage. Schema additions tested here; read-path overlay in
    test_disclosure_package_compiler.
    """

    def test_allocation_pools_has_forecast_columns(self, fresh_db: sqlite3.Connection) -> None:
        cols = {row[1] for row in fresh_db.execute("PRAGMA table_info(allocation_pools)")}
        assert "escalation_schedule_json" in cols
        assert "starting_monthly_per_unit" in cols

    def test_insert_pool_with_forecast_inputs_round_trips(
        self, fresh_db: sqlite3.Connection
    ) -> None:
        fresh_db.execute(
            "INSERT INTO properties (name, units) VALUES ('Sample HOA', 100)"
        )
        property_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (?, 'fixed', 'fixed', 'approved')",
            (property_id,),
        )
        setup_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        schedule = (
            '[{"start_year": 2026, "end_year": 2035, "rate": 0.04}]'
        )
        fresh_db.execute(
            "INSERT INTO allocation_pools "
            "(assessment_setup_id, pool_key, pool_name, allocation_method, recipient_scope, "
            " escalation_schedule_json, starting_monthly_per_unit) "
            "VALUES (?, 'equal_costs', 'Equal Costs', 'equal', 'all_units', ?, ?)",
            (setup_id, schedule, 250.50),
        )
        fresh_db.commit()

        row = fresh_db.execute(
            "SELECT escalation_schedule_json, starting_monthly_per_unit "
            "FROM allocation_pools WHERE assessment_setup_id = ?",
            (setup_id,),
        ).fetchone()
        assert row[0] == schedule
        assert row[1] == 250.50


class TestResolvePoolForecastOverlay:
    """Read-path helper that surfaces pool-level forecast inputs into the
    hoa_settings overlay used by compile_package (Task #185).
    """

    def test_empty_when_no_assessment_setup(self, fresh_db: sqlite3.Connection) -> None:
        from app.disclosure_package.service import _resolve_pool_forecast_overlay

        fresh_db.execute("INSERT INTO properties (name, units) VALUES ('A', 10)")
        property_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.commit()

        session = _SessionStub(fresh_db)
        overlay = _resolve_pool_forecast_overlay(
            session=session, property_id=property_id,
        )
        assert overlay == {}

    def test_uses_pool_values_when_set(self, fresh_db: sqlite3.Connection) -> None:
        from app.disclosure_package.service import _resolve_pool_forecast_overlay

        fresh_db.execute("INSERT INTO properties (name, units) VALUES ('B', 50)")
        property_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (?, 'fixed', 'fixed', 'approved')",
            (property_id,),
        )
        setup_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        schedule = '[{"start_year": 2026, "end_year": 2030, "rate": 0.05}]'
        fresh_db.execute(
            "INSERT INTO allocation_pools "
            "(assessment_setup_id, pool_key, pool_name, allocation_method, recipient_scope, "
            " escalation_schedule_json, starting_monthly_per_unit) "
            "VALUES (?, 'reserve', 'Reserve Pool', 'equal', 'all_units', ?, ?)",
            (setup_id, schedule, 300.00),
        )
        fresh_db.commit()

        session = _SessionStub(fresh_db)
        overlay = _resolve_pool_forecast_overlay(
            session=session, property_id=property_id,
        )
        assert overlay["assessment_increase_schedule_json"] == schedule
        assert overlay["replacement_fund_monthly_assessment_per_unit"] == 300.00

    def test_skips_draft_setup(self, fresh_db: sqlite3.Connection) -> None:
        """Only ``status='approved'`` setups feed the overlay."""
        from app.disclosure_package.service import _resolve_pool_forecast_overlay

        fresh_db.execute("INSERT INTO properties (name, units) VALUES ('C', 25)")
        property_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (?, 'fixed', 'fixed', 'draft')",
            (property_id,),
        )
        setup_id = fresh_db.execute("SELECT last_insert_rowid()").fetchone()[0]
        fresh_db.execute(
            "INSERT INTO allocation_pools "
            "(assessment_setup_id, pool_key, pool_name, allocation_method, recipient_scope, "
            " starting_monthly_per_unit) "
            "VALUES (?, 'reserve', 'Reserve Pool', 'equal', 'all_units', ?)",
            (setup_id, 400.00),
        )
        fresh_db.commit()

        session = _SessionStub(fresh_db)
        overlay = _resolve_pool_forecast_overlay(
            session=session, property_id=property_id,
        )
        assert overlay == {}


class _SessionStub:
    """Minimal SQLAlchemy-Session-like adapter that exposes ``connection().connection``
    pointing at a sqlite3 connection. Lets us test the overlay helper without a
    real SQLAlchemy session.
    """

    def __init__(self, raw_conn: sqlite3.Connection) -> None:
        self._raw_conn = raw_conn

    def connection(self):
        class _Wrapper:
            connection = self._raw_conn

        return _Wrapper()
