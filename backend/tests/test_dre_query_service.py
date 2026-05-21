"""DRE read-side query service tests (Phase 4.1)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services.dre_query_service import (
    DREExtractionRunNotFound,
    get_extraction_run,
    list_dre_documents,
    list_extraction_runs,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('A', 10)")
    yield conn
    conn.close()


def _seed_doc_and_run(db, pid: int, *, parsed_json: dict | None = None):
    db.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status, page_count) "
        "VALUES (?, 'dre/1/x.pdf', 'x.pdf', 'active', 18)",
        (pid,),
    )
    doc_id = db.execute("SELECT id FROM dre_documents").fetchone()[0]
    db.execute(
        "INSERT INTO dre_extraction_runs "
        "(dre_document_id, property_id, model_name, prompt_version, prompt_sha256, "
        "status, parsed_json) VALUES (?, ?, 'g-flash', '1.0', 'sha', 'succeeded', ?)",
        (doc_id, pid, json.dumps(parsed_json) if parsed_json else None),
    )
    rid = db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
    db.commit()
    return doc_id, rid


class TestListDREDocuments:
    def test_returns_property_documents_ordered_newest_first(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        _seed_doc_and_run(db, pid)
        rows = list_dre_documents(property_id=pid, connection=db)
        assert len(rows) == 1
        assert rows[0].property_id == pid
        assert rows[0].file_name == "x.pdf"
        assert rows[0].page_count == 18

    def test_excludes_other_property_docs(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        _seed_doc_and_run(db, pid)
        db.execute("INSERT INTO properties (name, units) VALUES ('B', 5)")
        other_pid = db.execute(
            "SELECT id FROM properties WHERE name='B'"
        ).fetchone()[0]
        _seed_doc_and_run(db, other_pid)
        rows = list_dre_documents(property_id=pid, connection=db)
        assert len(rows) == 1


class TestListExtractionRuns:
    def test_returns_runs_for_property(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        _, rid = _seed_doc_and_run(db, pid)
        rows = list_extraction_runs(property_id=pid, connection=db)
        assert len(rows) == 1
        assert rows[0].extraction_run_id == rid
        assert rows[0].review_status == "pending"


class TestGetExtractionRun:
    def test_returns_full_detail_with_parsed_json(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        _, rid = _seed_doc_and_run(db, pid, parsed_json={"foo": "bar"})
        detail = get_extraction_run(
            property_id=pid, extraction_run_id=rid, connection=db,
        )
        assert detail.extraction_run_id == rid
        assert detail.parsed_json == {"foo": "bar"}

    def test_returns_none_for_missing_parsed_json(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        _, rid = _seed_doc_and_run(db, pid)
        detail = get_extraction_run(
            property_id=pid, extraction_run_id=rid, connection=db,
        )
        assert detail.parsed_json is None

    def test_raises_not_found_for_missing_run(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        with pytest.raises(DREExtractionRunNotFound):
            get_extraction_run(
                property_id=pid, extraction_run_id=9999, connection=db,
            )

    def test_raises_not_found_for_wrong_property(self, db):
        pid = db.execute("SELECT id FROM properties").fetchone()[0]
        _, rid = _seed_doc_and_run(db, pid)
        db.execute("INSERT INTO properties (name, units) VALUES ('B', 5)")
        other_pid = db.execute(
            "SELECT id FROM properties WHERE name='B'"
        ).fetchone()[0]
        with pytest.raises(DREExtractionRunNotFound):
            get_extraction_run(
                property_id=other_pid, extraction_run_id=rid, connection=db,
            )
