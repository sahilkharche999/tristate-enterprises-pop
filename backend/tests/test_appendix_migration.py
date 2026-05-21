"""Tests for the legacy → AppendixDocument migration (Phase 5.4 tasks 160 + 166)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.appendix_migration import (
    migrate_directory_to_appendix_documents,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('A', 10)")
    yield conn
    conn.close()


def _pid(db) -> int:
    return db.execute("SELECT id FROM properties").fetchone()[0]


def _make_appendix_dir(tmp_path: Path, names: list[str]) -> Path:
    d = tmp_path / "appendices_legacy"
    d.mkdir()
    for name in names:
        (d / name).write_bytes(b"%PDF-fake")
    return d


class TestMigration:
    def test_inserts_one_row_per_pdf(self, db, tmp_path):
        d = _make_appendix_dir(tmp_path, [
            "election_rules.pdf",
            "bylaws.pdf",
            "rules_and_regs.pdf",
        ])
        count = migrate_directory_to_appendix_documents(
            property_id=_pid(db), appendix_dir=d, connection=db,
        )
        assert count == 3
        rows = db.execute(
            "SELECT display_title, cadence, needs_cadence_review "
            "FROM appendix_documents ORDER BY default_display_order"
        ).fetchall()
        assert [r[0] for r in rows] == [
            "Bylaws", "Election Rules", "Rules And Regs",
        ]
        # All default to persistent + needs_cadence_review
        assert all(r[1] == "persistent" for r in rows)
        assert all(r[2] == 1 for r in rows)

    def test_insurance_gets_annual_cadence_suggested(self, db, tmp_path):
        d = _make_appendix_dir(tmp_path, [
            "2026 Insurance Disclosure.pdf",
            "election_rules.pdf",
        ])
        count = migrate_directory_to_appendix_documents(
            property_id=_pid(db), appendix_dir=d,
            fiscal_year_hint=2026, connection=db,
        )
        assert count == 2
        ins_row = db.execute(
            "SELECT cadence, annual_year, valid_through_year, needs_cadence_review "
            "FROM appendix_documents WHERE display_title LIKE '%Insurance%'"
        ).fetchone()
        assert ins_row == ("annual", 2026, 2026, 1)

    def test_idempotent_re_run(self, db, tmp_path):
        d = _make_appendix_dir(tmp_path, ["a.pdf", "b.pdf"])
        migrate_directory_to_appendix_documents(
            property_id=_pid(db), appendix_dir=d, connection=db,
        )
        # Re-running skips existing files
        result = migrate_directory_to_appendix_documents(
            property_id=_pid(db), appendix_dir=d, connection=db,
        )
        assert result == 0
        total = db.execute(
            "SELECT COUNT(*) FROM appendix_documents"
        ).fetchone()[0]
        assert total == 2

    def test_missing_directory_returns_zero(self, db, tmp_path):
        result = migrate_directory_to_appendix_documents(
            property_id=_pid(db),
            appendix_dir=tmp_path / "does-not-exist",
            connection=db,
        )
        assert result == 0
