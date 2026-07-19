"""Persistence tests for DRE extraction runs + documents (Phase 3.1)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.dre_extraction import DRESetupExtraction, run_dre_extraction
from app.dre_extraction.page_classification import PageBatch
from app.dre_extraction.persistence import (
    insert_dre_document,
    save_extraction_run,
)
from app.dre_extraction.schemas import PageInventoryEntry


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(
        "INSERT INTO properties (name, units, hoa_code) "
        "VALUES ('Old Mill Test', 279, '10')"
    )
    yield conn
    conn.close()


def _property_id(db: sqlite3.Connection) -> int:
    return db.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]


class TestInsertDREDocument:
    def test_first_upload_is_active(self, db: sqlite3.Connection) -> None:
        pid = _property_id(db)
        doc_id = insert_dre_document(
            property_id=pid,
            file_id=f"dre/{pid}/orig.pdf",
            file_name="orig.pdf",
            page_count=42,
            uploaded_by="ops@example.com",
            connection=db,
        )
        db.commit()
        row = db.execute(
            "SELECT status, file_name, page_count, supersedes_id "
            "FROM dre_documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        assert row == ("active", "orig.pdf", 42, None)

    def test_second_upload_supersedes_first(self, db: sqlite3.Connection) -> None:
        pid = _property_id(db)
        first = insert_dre_document(
            property_id=pid, file_id="a.pdf", file_name="a.pdf",
            page_count=10, uploaded_by="u1", connection=db,
        )
        db.commit()
        second = insert_dre_document(
            property_id=pid, file_id="b.pdf", file_name="b.pdf",
            page_count=12, uploaded_by="u1", connection=db,
        )
        db.commit()

        statuses = dict(
            db.execute(
                "SELECT id, status FROM dre_documents WHERE property_id = ?",
                (pid,),
            ).fetchall()
        )
        assert statuses[first] == "superseded"
        assert statuses[second] == "active"
        # Cross-reference set
        new_supersedes = db.execute(
            "SELECT supersedes_id FROM dre_documents WHERE id = ?", (second,),
        ).fetchone()[0]
        assert new_supersedes == first


class TestSaveExtractionRun:
    def _run_pipeline(self) -> "DREExtractionRunRecord":  # type: ignore[name-defined]
        def classify(batch: PageBatch) -> list[PageInventoryEntry]:
            return [
                PageInventoryEntry(page_number=p, page_type="unit summary")
                for p in batch.page_numbers
            ]

        def extract(pages: list[int]):
            raw = json.dumps({
                "document_metadata": {
                    "association_name": "Old Mill",
                    "confidence": 0.95,
                    "source_pages": [1],
                },
                "assessment_setup": {
                    "setup_type": "fixed_equal",
                    "confidence": 0.9,
                    "source_pages": [2],
                },
                "allocation_pools": [
                    {
                        "pool_key": "equal_costs",
                        "allocation_method": "equal",
                        "source_pages": [3],
                        "confidence": 0.92,
                    }
                ],
            })
            return raw, None, {}

        return run_dre_extraction(
            page_count=10,
            classify_pages_callback=classify,
            extract_setup_callback=extract,
            model_name="gemini-flash-latest",
        )

    def test_run_persisted_with_full_audit_trail(self, db: sqlite3.Connection) -> None:
        pid = _property_id(db)
        doc_id = insert_dre_document(
            property_id=pid, file_id="x.pdf", file_name="x.pdf",
            page_count=10, uploaded_by="u", connection=db,
        )
        record = self._run_pipeline()
        run_id = save_extraction_run(
            record, property_id=pid, dre_document_id=doc_id, connection=db
        )
        db.commit()

        row = db.execute(
            "SELECT model_name, prompt_version, prompt_sha256, "
            "       parsed_json, schema_validation_errors, "
            "       repair_attempt_count, status, review_status "
            "FROM dre_extraction_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        assert row[0] == "gemini-flash-latest"
        assert row[1] == record.prompt_version
        assert row[2] == record.prompt_sha256
        # parsed_json round-trips through JSON
        parsed = json.loads(row[3])
        assert parsed["document_metadata"]["association_name"] == "Old Mill"
        # No schema errors
        assert json.loads(row[4]) == []
        assert row[5] == 0  # zero repair attempts
        assert row[6] == "succeeded"  # status
        assert row[7] == "pending"  # review_status

    def test_promoted_run_links_to_assessment_setup(self, db: sqlite3.Connection) -> None:
        pid = _property_id(db)
        doc_id = insert_dre_document(
            property_id=pid, file_id="x.pdf", file_name="x.pdf",
            page_count=10, uploaded_by="u", connection=db,
        )
        record = self._run_pipeline()
        run_id = save_extraction_run(
            record, property_id=pid, dre_document_id=doc_id, connection=db
        )
        # Operator approves → AssessmentSetup created → run marked promoted
        cur = db.execute(
            "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
            "VALUES (?, 'fixed', 'fixed', 'approved')",
            (pid,),
        )
        setup_id = cur.lastrowid
        db.execute(
            "UPDATE dre_extraction_runs SET review_status='promoted', promoted_setup_id=? "
            "WHERE id=?",
            (setup_id, run_id),
        )
        db.commit()

        row = db.execute(
            "SELECT review_status, promoted_setup_id FROM dre_extraction_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        assert row == ("promoted", setup_id)
