"""End-to-end onboarding flow tests (Phase 5.5 tasks 161-164).

Each test exercises the full happy-path data flow from DRE upload (or
no-DRE for fixed-fast-path) through extraction, approval, AssessmentSetup
creation, budget mapping, and AnnualPackage creation.

These are wire-level tests against in-memory SQLite — they don't render
PDFs (that's the raster-diff suite) but they assert every cross-module
hand-off works as documented.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.annual_package_service import (
    approve_annual_package,
    create_annual_package,
)
from app.services.appendix_service import upload_appendix
from app.services.budget_line_mapping_service import carry_forward_mappings_across_setups
from tests.support.budget_line_mapping_seed import (
    lookup_saved_mappings,
    seed_budget_line_mapping,
)
from app.services.dre_approval_service import approve_extraction_run
from app.services.dre_review_service import record_review_edit


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> sqlite3.Connection:
    from app.config import settings as _settings

    monkeypatch.setattr(
        _settings, "BUDGET_STORAGE_ROOT", str(tmp_path / "storage"),
    )
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()


def _seed_hoa(db, name: str, units: int) -> int:
    db.execute(
        "INSERT INTO properties (name, units) VALUES (?, ?)",
        (name, units),
    )
    return db.execute(
        "SELECT id FROM properties WHERE name = ?", (name,),
    ).fetchone()[0]


def _seed_dre_run(db, pid: int, parsed_json: dict) -> int:
    db.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status, page_count) "
        "VALUES (?, ?, 'dre.pdf', 'active', 20)",
        (pid, f"dre/{pid}/dre.pdf"),
    )
    doc_id = db.execute(
        "SELECT id FROM dre_documents WHERE property_id = ?", (pid,),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO dre_extraction_runs "
        "(dre_document_id, property_id, model_name, prompt_version, prompt_sha256, "
        "status, parsed_json) VALUES (?, ?, 'g', '1.0', 'sha', 'succeeded', ?)",
        (doc_id, pid, json.dumps(parsed_json)),
    )
    return db.execute(
        "SELECT id FROM dre_extraction_runs "
        "WHERE dre_document_id = ?",
        (doc_id,),
    ).fetchone()[0]


_GROUPED_DRE = {
    "document_metadata": {"association_name": "Esprit Park", "source_pages": [1]},
    "assessment_setup": {
        "setup_type": "grouped_category",
        "confidence": 0.92,
        "source_pages": [3],
    },
    "allocation_pools": [
        {
            "pool_key": "operating",
            "pool_name": "Operating Expenses",
            "allocation_method": "equal",
            "recipient_scope": "all_units",
            "denominator_source": "unknown",
            "annual_amount": "120000",
            "included_budget_lines": [],
            "excluded_budget_lines": [],
            "source_pages": [4],
        },
        {
            "pool_key": "variable",
            "pool_name": "Variable Pool (sqft)",
            "allocation_method": "square_footage",
            "recipient_scope": "all_units",
            "denominator_source": "dre_shown",
            "denominator_value": "10000",
            "annual_amount": "60000",
            "included_budget_lines": [],
            "excluded_budget_lines": [],
            "source_pages": [5],
        },
    ],
    "unit_structure": {
        "groups": [
            {"group_id": "th", "label": "Townhomes",
             "unit_count": 50, "average_square_feet": "1200"},
            {"group_id": "lf", "label": "Lofts",
             "unit_count": 30, "average_square_feet": "800"},
        ],
        "units": [],
    },
    "formulas": [],
    "validation_checks": [],
    "human_review_questions": [],
}

_PER_UNIT_DRE = {
    "document_metadata": {"association_name": "800 High", "source_pages": [1]},
    "assessment_setup": {
        "setup_type": "individual_unit",
        "confidence": 0.88,
        "source_pages": [3],
    },
    "allocation_pools": [
        {
            "pool_key": "operating",
            "pool_name": "Operating",
            "allocation_method": "equal",
            "recipient_scope": "residential_only",
            "denominator_source": "unknown",
            "included_budget_lines": [],
            "excluded_budget_lines": [],
            "source_pages": [4],
        },
        {
            "pool_key": "commercial_share",
            "pool_name": "Commercial share",
            "allocation_method": "ownership_percentage",
            "recipient_scope": "commercial_only",
            "denominator_source": "unknown",
            "included_budget_lines": [],
            "excluded_budget_lines": [],
            "source_pages": [4],
        },
        {
            "pool_key": "parking",
            "pool_name": "Parking",
            "allocation_method": "parking_space",
            "recipient_scope": "all_units",  # will be forced to parking_users by adapter
            "denominator_source": "unknown",
            "included_budget_lines": [],
            "excluded_budget_lines": [],
            "source_pages": [5],
        },
    ],
    "unit_structure": {
        "groups": [],
        "units": [
            {"unit_number": "101", "square_feet": "850",
             "category": "residential", "parking_flag": "1 space"},
            {"unit_number": "201", "square_feet": "1200",
             "category": "commercial", "parking_flag": ""},
            {"unit_number": "PH", "square_feet": "2500",
             "category": "residential", "parking_flag": "2 spaces"},
        ],
    },
    "formulas": [],
    "validation_checks": [],
    "human_review_questions": [],
}


class TestPatternBGrouped:
    """Task 161: end-to-end for a grouped HOA (Esprit Park style)."""

    def test_grouped_dre_extracts_approves_and_seeds_setup(self, db):
        pid = _seed_hoa(db, "Esprit Park HOA", 80)
        rid = _seed_dre_run(db, pid, _GROUPED_DRE)

        # Operator records one review edit before approving
        record_review_edit(
            dre_extraction_run_id=rid,
            field_path="allocation_pools[1].denominator_value",
            old_value="10000",
            new_value="10000",  # operator confirms verbatim
            reason="DRE-shown 10,000 confirmed against unit table",
            edited_by="ops",
            connection=db,
        )

        resp = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="grouped", reviewed_by="ops", connection=db,
        )

        # Snapshot counts: 2 pools, 2 groups, no units (grouped setup)
        assert resp.snapshot_counts["pools"] == 2
        assert resp.snapshot_counts["groups"] == 2
        assert resp.snapshot_counts["units"] == 0

        # AssessmentSetup row created with setup_type='grouped'
        setup_row = db.execute(
            "SELECT setup_type, status FROM assessment_setups "
            "WHERE id = ?",
            (resp.promoted_setup_id,),
        ).fetchone()
        assert setup_row == ("grouped", "approved")

        # Both pools were inserted
        pool_keys = db.execute(
            "SELECT pool_key FROM allocation_pools WHERE assessment_setup_id = ? "
            "ORDER BY display_order",
            (resp.promoted_setup_id,),
        ).fetchall()
        assert pool_keys == [("operating",), ("variable",)]

        # Variable pool used the DRE-shown denominator (10000) — DRE-preservation rule
        denom = db.execute(
            "SELECT denominator_value FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'variable'",
            (resp.promoted_setup_id,),
        ).fetchone()[0]
        assert Decimal(str(denom)) == Decimal("10000")


class TestPatternCPerUnit:
    """Task 162: end-to-end for a per-unit / multi-pool HOA (800 High)."""

    def test_per_unit_dre_extracts_with_mixed_recipient_scopes(self, db):
        pid = _seed_hoa(db, "800 High HOA", 3)
        rid = _seed_dre_run(db, pid, _PER_UNIT_DRE)

        resp = approve_extraction_run(
            property_id=pid, extraction_run_id=rid,
            setup_type="per_unit", reviewed_by="ops", connection=db,
        )

        assert resp.snapshot_counts["pools"] == 3
        assert resp.snapshot_counts["groups"] == 0
        assert resp.snapshot_counts["units"] == 3

        # Parking pool collapsed via adapter: method=equal, scope=parking_users
        parking = db.execute(
            "SELECT allocation_method, recipient_scope "
            "FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'parking'",
            (resp.promoted_setup_id,),
        ).fetchone()
        assert parking == ("equal", "parking_users")

        # Commercial pool has scope=commercial_only
        commercial = db.execute(
            "SELECT recipient_scope FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'commercial_share'",
            (resp.promoted_setup_id,),
        ).fetchone()
        assert commercial[0] == "commercial_only"

        # Parking spaces inferred from "1 space" / "2 spaces" flags
        parking_spaces = db.execute(
            "SELECT unit_number, parking_spaces FROM assessment_units "
            "WHERE assessment_setup_id = ? "
            "ORDER BY unit_number",
            (resp.promoted_setup_id,),
        ).fetchall()
        # 101: 1 space, 201: 0 (commercial), PH: 2 spaces
        spaces_by_unit = dict(parking_spaces)
        assert spaces_by_unit["101"] == 1
        assert spaces_by_unit["201"] == 0
        assert spaces_by_unit["PH"] == 2


class TestFixedFastPath:
    """Task 163: fixed HOA can be onboarded without a DRE.

    No DRE upload → no extraction → operator goes directly to creating
    the AnnualPackage with the fixed monthly amount they entered.
    """

    def test_fixed_hoa_can_create_package_without_dre(self, db):
        pid = _seed_hoa(db, "Old Mill HOA (fixed)", 279)
        # No DRE row, no extraction run

        # Operator creates a draft package directly
        pkg = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        assert pkg.status == "draft"

        # Operator enters approved revenue from the budget
        approved = approve_annual_package(
            property_id=pid, package_id=pkg.package_id,
            approved_assessment_revenue_annual=Decimal("605") * 12 * 279,
            approved_by="ops", connection=db,
        )
        assert approved.status == "approved"
        assert approved.approved_assessment_revenue_annual == Decimal("605") * 12 * 279


class TestAppendixPersistenceAcrossYears:
    """Task 164: appendix manifest inherited from year N into year N+1."""

    def test_year_n_appendices_visible_in_year_n_plus_1_packages(self, db):
        pid = _seed_hoa(db, "Test HOA", 50)
        # Operator uploads 3 appendices in year N
        for i in range(3):
            upload_appendix(
                property_id=pid,
                file_bytes=b"%PDF-fake",
                original_filename=f"appendix_{i}.pdf",
                display_title=f"Appendix {i}",
                cadence="persistent",
                required_flag=False,
                include_by_default=True,
                uploaded_by="ops",
                connection=db,
            )

        # Year-N+1 package gets the same include_by_default manifest
        from app.disclosure_package.appendix_manifest import resolve_appendix_manifest

        year_n_pkg = create_annual_package(
            property_id=pid, budget_year=2026, fiscal_year=2026, connection=db,
        )
        year_n_plus_1_pkg = create_annual_package(
            property_id=pid, budget_year=2027, fiscal_year=2027, connection=db,
        )

        manifest_n = resolve_appendix_manifest(
            property_id=pid, package_id=year_n_pkg.package_id, connection=db,
        )
        manifest_n1 = resolve_appendix_manifest(
            property_id=pid, package_id=year_n_plus_1_pkg.package_id, connection=db,
        )
        assert len(manifest_n) == 3
        assert len(manifest_n1) == 3
        assert [a.display_title for a in manifest_n] == [
            a.display_title for a in manifest_n1
        ]
