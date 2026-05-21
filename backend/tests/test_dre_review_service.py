"""DRE Review Workbench audit-writer tests (Phase 4.6 + 4.7).

Backend-only — no UI yet. Verifies the service helpers append the
correct rows and the field-source bootstrap walks an extraction
payload without writing zero-page citations.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.dre_review_service import (
    list_field_sources,
    list_review_edits,
    record_entity_sources_from_extraction,
    record_field_source,
    record_review_edit,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('Test', 10)")
    pid = conn.execute("SELECT id FROM properties").fetchone()[0]
    conn.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status) "
        "VALUES (?, 'dre/1/x.pdf', 'x.pdf', 'active')",
        (pid,),
    )
    doc_id = conn.execute("SELECT id FROM dre_documents").fetchone()[0]
    conn.execute(
        "INSERT INTO dre_extraction_runs "
        "(dre_document_id, property_id, model_name, prompt_version, "
        "prompt_sha256, status) "
        "VALUES (?, ?, 'g', '1', 's', 'succeeded')",
        (doc_id, pid),
    )
    conn.commit()
    yield conn
    conn.close()


def _rid(db: sqlite3.Connection) -> int:
    return db.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]


class TestRecordReviewEdit:
    def test_appends_audit_row(self, db):
        rid = _rid(db)
        resp = record_review_edit(
            dre_extraction_run_id=rid,
            field_path="assessment_setup.setup_type",
            old_value="grouped_category",
            new_value="per_unit",
            reason="Operator confirmed per-unit layout in DRE.",
            edited_by="ops@example.com",
            connection=db,
        )
        assert resp.edit_id > 0
        assert resp.field_path == "assessment_setup.setup_type"
        assert resp.old_value == "grouped_category"
        assert resp.new_value == "per_unit"
        assert resp.reason.startswith("Operator confirmed")
        assert resp.edited_by == "ops@example.com"

    def test_stringifies_decimal_values(self, db):
        from decimal import Decimal
        rid = _rid(db)
        resp = record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[0].denominator_value",
            old_value=Decimal("10000"),
            new_value=Decimal("10250.50"),
            connection=db,
        )
        assert resp.old_value == "10000"
        assert resp.new_value == "10250.50"

    def test_stringifies_compound_values(self, db):
        rid = _rid(db)
        resp = record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[0].included_budget_lines",
            old_value=["A", "B"],
            new_value=["A", "C"],
            connection=db,
        )
        assert resp.old_value == '["A","B"]'
        assert resp.new_value == '["A","C"]'

    def test_list_returns_oldest_first(self, db):
        rid = _rid(db)
        record_review_edit(
            dre_extraction_run_id=rid, field_path="a", old_value="1", new_value="2",
            connection=db,
        )
        record_review_edit(
            dre_extraction_run_id=rid, field_path="b", old_value="3", new_value="4",
            connection=db,
        )
        rows = list_review_edits(dre_extraction_run_id=rid, connection=db)
        assert [r.field_path for r in rows] == ["a", "b"]


class TestRecordFieldSource:
    def test_appends_source_row(self, db):
        rid = _rid(db)
        resp = record_field_source(
            dre_extraction_run_id=rid,
            entity_type="pool", entity_id="operating",
            field_name="denominator_value",
            page_number=7,
            bounding_box={"x": 100, "y": 200, "w": 50, "h": 20},
            confidence=0.88,
            connection=db,
        )
        assert resp.source_id > 0
        assert resp.entity_type == "pool"
        assert resp.entity_id == "operating"
        assert resp.field_name == "denominator_value"
        assert resp.page_number == 7
        assert resp.bounding_box_json == '{"h":20,"w":50,"x":100,"y":200}'
        assert resp.confidence == 0.88

    def test_rejects_zero_page_number(self, db):
        rid = _rid(db)
        with pytest.raises(ValueError, match="page_number must be"):
            record_field_source(
                dre_extraction_run_id=rid,
                entity_type="pool", entity_id="x",
                field_name="denominator_value",
                page_number=0,
                connection=db,
            )

    def test_list_filters_by_entity_type(self, db):
        rid = _rid(db)
        record_field_source(
            dre_extraction_run_id=rid, entity_type="pool", entity_id="a",
            field_name="x", page_number=1, connection=db,
        )
        record_field_source(
            dre_extraction_run_id=rid, entity_type="group", entity_id="g1",
            field_name="y", page_number=2, connection=db,
        )
        pool_rows = list_field_sources(
            dre_extraction_run_id=rid, entity_type="pool", connection=db,
        )
        assert len(pool_rows) == 1
        assert pool_rows[0].entity_id == "a"


class TestEntitySourcesFromExtraction:
    def test_walks_groups_and_high_risk_fields(self, db):
        rid = _rid(db)
        payload = {
            "document_metadata": {"source_pages": [1]},
            "assessment_setup": {"source_pages": [2]},
            "unit_structure": {
                "groups": [
                    {
                        "group_id": "g1",
                        "label": "Townhomes",
                        "unit_count": 50,
                        "average_square_feet": "1200",
                        "ownership_percent": "0.55",
                        "source_page": 4,
                    },
                ],
                "units": [],
            },
            "allocation_pools": [
                {
                    "pool_key": "operating",
                    "denominator_value": "10000",
                    "allocation_method": "square_footage",
                    "annual_amount": "120000",
                    "source_pages": [6, 7],
                },
            ],
        }
        written = record_entity_sources_from_extraction(
            dre_extraction_run_id=rid,
            extraction_payload=payload,
            connection=db,
        )
        # 1 doc_meta + 1 setup + 1 group entity + 3 group high-risk +
        # 2 pool entity + 3 pool high-risk = 11
        assert written == 11

        all_rows = list_field_sources(dre_extraction_run_id=rid, connection=db)
        assert len(all_rows) == 11

        # Pool high-risk field row exists with the primary (first) source page
        pool_high_risk = [
            r for r in all_rows
            if r.entity_type == "pool" and r.field_name == "denominator_value"
        ]
        assert len(pool_high_risk) == 1
        assert pool_high_risk[0].page_number == 6

    def test_skips_missing_pages(self, db):
        rid = _rid(db)
        payload = {
            "document_metadata": {"source_pages": []},  # empty → no row
            "assessment_setup": {"source_pages": [3]},
            "unit_structure": {
                "groups": [
                    {"group_id": "g1", "label": "G", "unit_count": 5},  # no source_page → no row
                ],
                "units": [],
            },
            "allocation_pools": [],
        }
        written = record_entity_sources_from_extraction(
            dre_extraction_run_id=rid,
            extraction_payload=payload,
            connection=db,
        )
        assert written == 1  # only the assessment_setup entity row
