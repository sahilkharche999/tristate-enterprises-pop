"""pool_kind is written through promotion (not a live UPDATE) and preserved
across supersession (add-variable-special-assessments, task 1.3/1.5)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services.dre_approval_service import approve_extraction_run
from app.services.dre_review_service import record_review_edit

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"


def _parsed_json() -> str:
    return json.dumps({
        "document_metadata": {"association_name": "Test", "total_units": 10, "source_pages": [1]},
        "assessment_setup": {"setup_type": "grouped_category", "display_mode": "grouped", "source_pages": [1]},
        "unit_structure": {"unit_count": 10, "group_count": 0, "groups": [], "units": []},
        "allocation_pools": [{
            "pool_key": "roof_levy", "parent_pool_key": "", "pool_name": "Roof Levy 2027",
            "annual_amount": "10000", "monthly_amount": "833", "allocation_method": "equal",
            "recipient_scope": "all_units", "denominator_label": "units", "denominator_value": "10",
            "denominator_source": "dre_shown", "included_budget_lines": [], "excluded_budget_lines": [],
            "budget_line_derivation": "explicit_lines", "residual_after_pool_keys": [],
            "residual_exclusions": [], "source_pages": [1], "confidence": 0.9,
        }],
        "formulas": [], "validation_checks": [], "human_review_questions": [],
    })


def _new_run(conn: sqlite3.Connection, pid: int, doc_id: int) -> int:
    conn.execute(
        "INSERT INTO dre_extraction_runs "
        "(dre_document_id, property_id, model_name, prompt_version, prompt_sha256, status, parsed_json) "
        "VALUES (?, ?, 'gemini-flash-latest', '1.0.0', 'abc', 'succeeded', ?)",
        (doc_id, pid, _parsed_json()),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('Test', 10)")
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status) "
        "VALUES (?, 'dre/1/x.pdf', 'x.pdf', 'active')", (pid,),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    yield conn
    conn.close()


def _pool_kind(conn, setup_id, pool_key="roof_levy"):
    return conn.execute(
        "SELECT pool_kind FROM allocation_pools WHERE assessment_setup_id = ? AND pool_key = ?",
        (setup_id, pool_key),
    ).fetchone()[0]


def test_review_edit_marks_pool_special_through_promotion(db: sqlite3.Connection):
    pid = db.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]
    doc_id = db.execute("SELECT id FROM dre_documents LIMIT 1").fetchone()[0]
    rid = _new_run(db, pid, doc_id)
    # Operator marks the pool special via the same review-edit path as any field.
    record_review_edit(
        dre_extraction_run_id=rid,
        field_path="allocation_pools[0].pool_kind",
        old_value="",
        new_value="separately_billed_special_assessment",
        connection=db,
    )
    resp = approve_extraction_run(
        property_id=pid, extraction_run_id=rid, setup_type="grouped",
        reviewed_by="op", connection=db,
    )
    db.commit()
    assert _pool_kind(db, resp.promoted_setup_id) == "separately_billed_special_assessment"


def test_pool_kind_preserved_across_reextraction(db: sqlite3.Connection):
    pid = db.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]
    doc_id = db.execute("SELECT id FROM dre_documents LIMIT 1").fetchone()[0]

    rid1 = _new_run(db, pid, doc_id)
    record_review_edit(
        dre_extraction_run_id=rid1, field_path="allocation_pools[0].pool_kind",
        old_value="", new_value="separately_billed_special_assessment", connection=db,
    )
    approve_extraction_run(property_id=pid, extraction_run_id=rid1, setup_type="grouped",
                           reviewed_by="op", connection=db)
    db.commit()

    # A fresh extraction run with NO edit — same pool_key — must inherit the mark.
    rid2 = _new_run(db, pid, doc_id)
    resp2 = approve_extraction_run(property_id=pid, extraction_run_id=rid2, setup_type="grouped",
                                   reviewed_by="op", connection=db)
    db.commit()
    assert _pool_kind(db, resp2.promoted_setup_id) == "separately_billed_special_assessment"


def test_unmarked_pool_stays_regular(db: sqlite3.Connection):
    pid = db.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]
    doc_id = db.execute("SELECT id FROM dre_documents LIMIT 1").fetchone()[0]
    rid = _new_run(db, pid, doc_id)
    resp = approve_extraction_run(property_id=pid, extraction_run_id=rid, setup_type="grouped",
                                  reviewed_by="op", connection=db)
    db.commit()
    assert _pool_kind(db, resp.promoted_setup_id) is None
