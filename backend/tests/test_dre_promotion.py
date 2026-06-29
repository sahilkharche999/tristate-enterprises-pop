"""Tests for the extraction → setup-children snapshot (Phase 4.2 task 105).

Verifies that ``promote_extraction_to_setup`` reads a stored
``parsed_json`` blob and writes AllocationPool / AssessmentGroup /
AssessmentUnit / AssessmentUnitPoolAllocation rows alongside the
existing AssessmentSetup row.

Also covers the integrated path through ``approve_extraction_run``
including the new ``default_assessment_setup_id`` write on properties.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.dre_extraction.promotion import (
    parse_extraction_payload,
    populate_setup_children,
    promote_extraction_to_setup,
)
from app.services.dre_approval_service import approve_extraction_run


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


def _make_extraction_payload(
    *,
    setup_type: str = "grouped_category",
    pools: list[dict] | None = None,
    groups: list[dict] | None = None,
    units: list[dict] | None = None,
) -> dict:
    return {
        "document_metadata": {"association_name": "Test HOA"},
        "page_inventory": [],
        "assessment_setup": {
            "setup_type": setup_type,
            "display_mode": "",
            "summary": "",
            "requires_dre_for_future_years": True,
            "confidence": 0.9,
            "source_pages": [1],
        },
        "unit_structure": {
            "unit_count": len(units or []) or (groups or [{}])[0].get("unit_count", 0),
            "group_count": len(groups or []),
            "groups": groups or [],
            "units": units or [],
        },
        "allocation_pools": pools or [],
        "formulas": [],
        "reserve_setup": None,
        "validation_checks": [],
        "human_review_questions": [],
        "recommended_saved_setup": None,
    }


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()


def _seed_property_and_setup(db: sqlite3.Connection) -> tuple[int, int]:
    db.execute("INSERT INTO properties (name, units) VALUES ('Test', 10)")
    pid = db.execute("SELECT id FROM properties").fetchone()[0]
    db.execute(
        "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
        "VALUES (?, 'grouped', 'grouped', 'draft')",
        (pid,),
    )
    setup_id = db.execute("SELECT id FROM assessment_setups").fetchone()[0]
    db.commit()
    return pid, setup_id


class TestParseExtractionPayload:
    def test_returns_none_on_missing_text(self):
        assert parse_extraction_payload(None) is None
        assert parse_extraction_payload("") is None

    def test_returns_none_on_invalid_json(self):
        assert parse_extraction_payload("not-json{") is None

    def test_returns_none_on_schema_violation(self):
        # missing required assessment_setup block
        assert parse_extraction_payload(json.dumps({"foo": "bar"})) is None

    def test_parses_minimal_valid_payload(self):
        payload = _make_extraction_payload(setup_type="fixed_equal")
        parsed = parse_extraction_payload(json.dumps(payload))
        assert parsed is not None
        assert parsed.assessment_setup.setup_type == "fixed_equal"


class TestPopulatePools:
    def test_inserts_pool_with_mapped_method(self, db):
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            pools=[{
                "pool_key": "operating",
                "pool_name": "Operating Expenses",
                "annual_amount": "120000",
                "allocation_method": "square_footage",
                "recipient_scope": "all_units",
                "denominator_label": "total_sqft",
                "denominator_value": "10000",
                "denominator_source": "dre_shown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "source_pages": [3],
                "confidence": 0.95,
            }],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        counts = populate_setup_children(
            setup_id=setup_id, setup_type="grouped",
            extraction=ext, connection=db,
        )
        assert counts["pools"] == 1
        row = db.execute(
            "SELECT pool_key, allocation_method, recipient_scope, "
            "denominator_source, denominator_value "
            "FROM allocation_pools WHERE assessment_setup_id = ?",
            (setup_id,),
        ).fetchone()
        assert row[0] == "operating"
        assert row[1] == "square_footage"
        assert row[2] == "all_units"
        assert row[3] == "dre_value"  # mapped from 'dre_shown'
        assert Decimal(str(row[4])) == Decimal("10000")

    def test_parking_space_method_collapses_to_equal_parking_scope(self, db):
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            pools=[{
                "pool_key": "parking",
                "pool_name": "Parking Maintenance",
                "annual_amount": "12000",
                "allocation_method": "parking_space",
                "recipient_scope": "ignored_by_adapter",
                "denominator_source": "unknown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "source_pages": [5],
                "confidence": 0.7,
            }],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        populate_setup_children(
            setup_id=setup_id, setup_type="per_unit",
            extraction=ext, connection=db,
        )
        row = db.execute(
            "SELECT allocation_method, recipient_scope "
            "FROM allocation_pools WHERE assessment_setup_id = ?",
            (setup_id,),
        ).fetchone()
        assert row == ("equal", "parking_users")

    def test_unknown_method_skipped(self, db):
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            pools=[{
                "pool_key": "mystery",
                "pool_name": "?",
                "allocation_method": "unknown",
                "recipient_scope": "all_units",
                "denominator_source": "unknown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "source_pages": [],
            }],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        counts = populate_setup_children(
            setup_id=setup_id, setup_type="grouped",
            extraction=ext, connection=db,
        )
        assert counts["pools"] == 0
        row_count = db.execute(
            "SELECT COUNT(*) FROM allocation_pools "
            "WHERE assessment_setup_id = ?",
            (setup_id,),
        ).fetchone()[0]
        assert row_count == 0


class TestPopulateGroups:
    def test_inserts_groups_for_grouped_setup_only(self, db):
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            setup_type="grouped_category",
            groups=[
                {"group_id": "g1", "label": "Townhomes",
                 "unit_count": 50, "average_square_feet": "1200", "confidence": 0.9},
                {"group_id": "g2", "label": "Lofts",
                 "unit_count": 30, "average_square_feet": "800", "confidence": 0.9},
            ],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        counts = populate_setup_children(
            setup_id=setup_id, setup_type="grouped",
            extraction=ext, connection=db,
        )
        assert counts["groups"] == 2
        rows = db.execute(
            "SELECT group_name, unit_count "
            "FROM assessment_groups WHERE assessment_setup_id = ? "
            "ORDER BY display_order",
            (setup_id,),
        ).fetchall()
        assert rows == [("Townhomes", 50), ("Lofts", 30)]

    def test_groups_skipped_for_per_unit_setup(self, db):
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            groups=[{"label": "Townhomes", "unit_count": 50}],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        counts = populate_setup_children(
            setup_id=setup_id, setup_type="per_unit",
            extraction=ext, connection=db,
        )
        assert counts["groups"] == 0

    def test_group_with_missing_unit_count_skipped(self, db):
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            groups=[
                {"label": "Valid", "unit_count": 10},
                {"label": "Invalid"},  # no unit_count
            ],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        counts = populate_setup_children(
            setup_id=setup_id, setup_type="grouped",
            extraction=ext, connection=db,
        )
        assert counts["groups"] == 1


class TestPopulateUnits:
    def test_inserts_units_for_per_unit_setup(self, db):
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            units=[
                {"unit_number": "101", "square_feet": "850",
                 "ownership_percent": "1.5", "category": "residential",
                 "parking_flag": "2 spaces"},
                {"unit_number": "201", "square_feet": "1200",
                 "category": "commercial", "parking_flag": ""},
            ],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        counts = populate_setup_children(
            setup_id=setup_id, setup_type="per_unit",
            extraction=ext, connection=db,
        )
        assert counts["units"] == 2
        rows = db.execute(
            "SELECT unit_number, category, parking_spaces "
            "FROM assessment_units WHERE assessment_setup_id = ? "
            "ORDER BY unit_number",
            (setup_id,),
        ).fetchall()
        assert rows[0] == ("101", "residential", 2)
        assert rows[1] == ("201", "commercial", 0)

    def test_units_skipped_for_grouped_setup(self, db):
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            units=[{"unit_number": "101"}],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        counts = populate_setup_children(
            setup_id=setup_id, setup_type="grouped",
            extraction=ext, connection=db,
        )
        assert counts["units"] == 0


class TestProportionalPoolNormalization:
    def test_square_footage_pool_without_sqft_falls_back_to_ownership_percentage(self, db):
        # Los Altos case: CC&R prose says "in proportion to square footage",
        # but Exhibit B carries percentage interests, not square feet. The
        # pool must allocate by ownership_percentage so the engine can compute
        # it (otherwise: UnsupportedAllocationMethod -> package render 500).
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            pools=[{
                "pool_key": "sqft_proportional_exceptions",
                "pool_name": "Square Footage Proportional Exceptions",
                "annual_amount": None,
                "allocation_method": "square_footage",
                "recipient_scope": "all_units",
                "denominator_label": "total square footage of all units",
                "denominator_value": None,
                "denominator_source": "unknown",
                "included_budget_lines": ["insurance"],
                "excluded_budget_lines": [],
                "source_pages": [16],
                "confidence": 0.9,
            }],
            units=[
                {"unit_number": "101", "ownership_percent": "13.15"},
                {"unit_number": "102", "ownership_percent": "12.02"},
            ],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        populate_setup_children(
            setup_id=setup_id, setup_type="per_unit",
            extraction=ext, connection=db,
        )
        method = db.execute(
            "SELECT allocation_method FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'sqft_proportional_exceptions'",
            (setup_id,),
        ).fetchone()[0]
        assert method == "ownership_percentage"

    def test_square_footage_pool_with_real_sqft_is_untouched(self, db):
        # A setup with genuine per-unit square footage must keep square_footage.
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            pools=[{
                "pool_key": "sqft_pool",
                "pool_name": "Sqft Pool",
                "annual_amount": None,
                "allocation_method": "square_footage",
                "recipient_scope": "all_units",
                "denominator_label": "total sqft",
                "denominator_value": "2050",
                "denominator_source": "dre_shown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "source_pages": [3],
                "confidence": 0.95,
            }],
            units=[
                {"unit_number": "101", "square_feet": "850", "ownership_percent": "1.5"},
                {"unit_number": "102", "square_feet": "1200"},
            ],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        populate_setup_children(
            setup_id=setup_id, setup_type="per_unit",
            extraction=ext, connection=db,
        )
        method = db.execute(
            "SELECT allocation_method FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'sqft_pool'",
            (setup_id,),
        ).fetchone()[0]
        assert method == "square_footage"


class TestSpecifiedValueAllocations:
    def test_per_unit_specified_value_pool_creates_unit_allocations(self, db):
        pid, setup_id = _seed_property_and_setup(db)
        payload = _make_extraction_payload(
            pools=[{
                "pool_key": "high_floor_specified",
                "pool_name": "High Floor Specified Dues",
                "annual_amount": "24000",
                "allocation_method": "specified_value",
                "recipient_scope": "residential_only",
                "denominator_source": "unknown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "source_pages": [],
            }],
            units=[
                {"unit_number": "101"},
                {"unit_number": "201"},
            ],
        )
        ext = parse_extraction_payload(json.dumps(payload))
        populate_setup_children(
            setup_id=setup_id, setup_type="per_unit",
            extraction=ext, connection=db,
        )
        rows = db.execute(
            "SELECT pool_key, specified_monthly_amount "
            "FROM assessment_unit_pool_allocations "
            "WHERE assessment_setup_id = ? "
            "ORDER BY id",
            (setup_id,),
        ).fetchall()
        # 24000 / 12 / 2 = 1000 monthly per unit
        assert len(rows) == 2
        for r in rows:
            assert r[0] == "high_floor_specified"
            assert Decimal(str(r[1])) == Decimal("1000.00")


class TestApprovalIntegration:
    def test_approval_populates_children_and_sets_default(self, tmp_path):
        # Seed DB with a property + dre_document + extraction run with parsed_json
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute("INSERT INTO properties (name, units) VALUES ('A', 10)")
        pid = conn.execute("SELECT id FROM properties").fetchone()[0]
        conn.execute(
            "INSERT INTO dre_documents (property_id, file_id, file_name, status) "
            "VALUES (?, 'dre/1/x.pdf', 'x.pdf', 'active')",
            (pid,),
        )
        doc_id = conn.execute("SELECT id FROM dre_documents").fetchone()[0]
        payload = _make_extraction_payload(
            pools=[{
                "pool_key": "operating",
                "pool_name": "Operating",
                "allocation_method": "equal",
                "recipient_scope": "all_units",
                "denominator_source": "unknown",
                "included_budget_lines": [],
                "excluded_budget_lines": [],
                "source_pages": [],
            }],
            groups=[{"label": "All", "unit_count": 10}],
        )
        conn.execute(
            "INSERT INTO dre_extraction_runs "
            "(dre_document_id, property_id, model_name, prompt_version, "
            "prompt_sha256, parsed_json, status) "
            "VALUES (?, ?, 'g', '1', 's', ?, 'succeeded')",
            (doc_id, pid, json.dumps(payload)),
        )
        rid = conn.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
        conn.commit()

        resp = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="grouped", reviewed_by="op", connection=conn,
        )
        assert resp.snapshot_counts == {
            "pools": 1, "groups": 1, "units": 0, "unit_pool_allocations": 0,
        }
        # default_assessment_setup_id set on properties
        default_id = conn.execute(
            "SELECT default_assessment_setup_id FROM properties WHERE id = ?",
            (pid,),
        ).fetchone()[0]
        assert default_id == resp.promoted_setup_id
        # Pool + Group rows committed
        pool_count = conn.execute(
            "SELECT COUNT(*) FROM allocation_pools WHERE assessment_setup_id = ?",
            (resp.promoted_setup_id,),
        ).fetchone()[0]
        group_count = conn.execute(
            "SELECT COUNT(*) FROM assessment_groups WHERE assessment_setup_id = ?",
            (resp.promoted_setup_id,),
        ).fetchone()[0]
        assert pool_count == 1
        assert group_count == 1
        conn.close()

    def test_approval_with_no_parsed_json_still_succeeds(self, tmp_path):
        """Existing rows without parsed_json must still approve cleanly
        (existing 9 tests in test_dre_approval_service.py exercise this).
        Snapshot counts are all zero.
        """
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute("INSERT INTO properties (name, units) VALUES ('A', 5)")
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
        rid = conn.execute("SELECT id FROM dre_extraction_runs").fetchone()[0]
        conn.commit()

        resp = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="fixed", reviewed_by="op", connection=conn,
        )
        assert resp.snapshot_counts == {
            "pools": 0, "groups": 0, "units": 0, "unit_pool_allocations": 0,
        }
        # default_assessment_setup_id is still set (the side-effect doesn't
        # require parsed_json)
        default_id = conn.execute(
            "SELECT default_assessment_setup_id FROM properties WHERE id = ?",
            (pid,),
        ).fetchone()[0]
        assert default_id == resp.promoted_setup_id
        conn.close()
