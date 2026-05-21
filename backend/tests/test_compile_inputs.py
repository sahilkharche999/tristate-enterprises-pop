"""Tests for the compile-side input resolver (Phase 4.8 tasks 135 + 159).

Verifies the snapshot-vs-live branch + the appendix path resolution
that the compile flow uses to assemble the merge order.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.disclosure_package.compile_inputs import (
    compile_input_summary,
    resolve_compile_appendix_paths,
    should_use_snapshots,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> sqlite3.Connection:
    # Re-point BUDGET_STORAGE_ROOT to tmp_path so appendix_file_path
    # resolves to a writable location for the file-exists assertion.
    from app.config import settings as _settings

    monkeypatch.setattr(
        _settings, "BUDGET_STORAGE_ROOT", str(tmp_path / "storage"),
    )
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('Test', 10)")
    conn.commit()
    yield conn
    conn.close()


def _pid(db: sqlite3.Connection) -> int:
    return db.execute("SELECT id FROM properties").fetchone()[0]


def _insert_package(
    db: sqlite3.Connection, *, property_id: int,
    status: str = "draft",
    snapshots_set: bool = False,
) -> int:
    snap_args = (
        ('{"a":1}', '{"b":2}', '{"r":3}', '{"m":4}')
        if snapshots_set
        else (None, None, None, None)
    )
    cur = db.execute(
        """
        INSERT INTO annual_packages (
            property_id, budget_year, fiscal_year, status,
            assessment_setup_snapshot_json, budget_snapshot_json,
            reserve_snapshot_json, appendix_manifest_snapshot_json
        ) VALUES (?, 2026, 2026, ?, ?, ?, ?, ?)
        """,
        (property_id, status, *snap_args),
    )
    db.commit()
    return cur.lastrowid


def _insert_appendix_with_file(
    db: sqlite3.Connection,
    *,
    property_id: int,
    file_name: str,
    display_title: str,
    tmp_storage: Path,
    write_file: bool = True,
) -> tuple[int, str]:
    relative = f"appendices/{property_id}/{file_name}"
    file_id = relative
    cur = db.execute(
        """
        INSERT INTO appendix_documents (
            property_id, file_id, file_name, display_title,
            include_by_default, status
        ) VALUES (?, ?, ?, ?, 1, 'active')
        """,
        (property_id, file_id, file_name, display_title),
    )
    db.commit()
    if write_file:
        target = tmp_storage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-fake")
    return cur.lastrowid, file_id


class TestShouldUseSnapshots:
    def test_no_package_id_returns_false(self, db):
        assert should_use_snapshots(package_id=None, connection=db) is False

    def test_missing_package_returns_false(self, db):
        assert should_use_snapshots(package_id=9999, connection=db) is False

    def test_draft_package_returns_false(self, db):
        pid = _pid(db)
        pkg = _insert_package(db, property_id=pid, status="draft")
        assert should_use_snapshots(package_id=pkg, connection=db) is False

    def test_approved_package_returns_false(self, db):
        pid = _pid(db)
        pkg = _insert_package(db, property_id=pid, status="approved")
        assert should_use_snapshots(package_id=pkg, connection=db) is False

    def test_finalized_with_snapshots_returns_true(self, db):
        pid = _pid(db)
        pkg = _insert_package(
            db, property_id=pid, status="finalized", snapshots_set=True,
        )
        assert should_use_snapshots(package_id=pkg, connection=db) is True

    def test_finalized_without_snapshots_returns_false(self, db):
        """Should not happen in practice but defends against
        partially-finalized rows."""
        pid = _pid(db)
        pkg = _insert_package(db, property_id=pid, status="finalized")
        # Snapshots all null
        assert should_use_snapshots(package_id=pkg, connection=db) is False


class TestResolveCompileAppendixPaths:
    def test_returns_paths_for_existing_files(self, db, tmp_path):
        pid = _pid(db)
        pkg = _insert_package(db, property_id=pid, status="draft")
        _insert_appendix_with_file(
            db, property_id=pid, file_name="bylaws.pdf",
            display_title="Bylaws", tmp_storage=tmp_path / "storage",
        )
        paths = resolve_compile_appendix_paths(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert len(paths) == 1
        assert paths[0].name == "bylaws.pdf"
        assert paths[0].exists()

    def test_missing_files_skipped(self, db, tmp_path):
        pid = _pid(db)
        pkg = _insert_package(db, property_id=pid, status="draft")
        # Two appendix rows, only one has the file on disk
        _insert_appendix_with_file(
            db, property_id=pid, file_name="present.pdf",
            display_title="Present", tmp_storage=tmp_path / "storage",
            write_file=True,
        )
        _insert_appendix_with_file(
            db, property_id=pid, file_name="missing.pdf",
            display_title="Missing", tmp_storage=tmp_path / "storage",
            write_file=False,
        )
        paths = resolve_compile_appendix_paths(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert [p.name for p in paths] == ["present.pdf"]


class TestCompileInputSummary:
    def test_summary_has_expected_keys(self, db, tmp_path):
        pid = _pid(db)
        pkg = _insert_package(
            db, property_id=pid, status="finalized", snapshots_set=True,
        )
        _insert_appendix_with_file(
            db, property_id=pid, file_name="x.pdf",
            display_title="X", tmp_storage=tmp_path / "storage",
        )
        summary = compile_input_summary(
            property_id=pid, package_id=pkg, connection=db,
        )
        assert summary["package_id"] == pkg
        assert summary["use_snapshots"] is True
        assert summary["appendix_count"] == 1
        assert summary["appendix_sources"] == ["default"]
        assert summary["appendix_titles"] == ["X"]
