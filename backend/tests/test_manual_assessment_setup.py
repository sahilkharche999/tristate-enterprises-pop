"""Tests for manual assessment setup entry (Group 4 of the
make-dre-data-editable change): a property with no DRE/CC&R extraction
run at all can still get a live AssessmentSetup, via the identical
review/approve pipeline every other run uses.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.ccr_approval_service import MissingUnitFactors, approve_ccr_extraction_run
from app.services.dre_approval_service import approve_extraction_run
from app.services.manual_assessment_setup_service import (
    ManualGroupEntry,
    ManualPoolEntry,
    ManualUnitEntry,
    PropertyNotFound,
    create_manual_extraction_run,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('No DRE HOA', 0)")
    conn.commit()
    yield conn
    conn.close()


def _pid(db: sqlite3.Connection) -> int:
    return db.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]


class TestCreateManualExtractionRun:
    def test_unknown_property_raises(self, db: sqlite3.Connection) -> None:
        with pytest.raises(PropertyNotFound):
            create_manual_extraction_run(
                property_id=99999,
                setup_type="fixed_equal",
                pools=[
                    ManualPoolEntry(
                        pool_key="operating", allocation_method="equal",
                        annual_amount=Decimal("1000"),
                    )
                ],
                groups=[],
                units=[],
                created_by="ops@example.com",
                connection=db,
            )

    def test_creates_placeholder_document_and_run(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        resp = create_manual_extraction_run(
            property_id=pid,
            setup_type="fixed_equal",
            pools=[
                ManualPoolEntry(
                    pool_key="operating", allocation_method="equal",
                    annual_amount=Decimal("120000"),
                )
            ],
            groups=[],
            units=[],
            created_by="ops@example.com",
            connection=db,
        )
        assert resp.property_id == pid
        doc_row = db.execute(
            "SELECT document_type, status FROM dre_documents WHERE id = ?",
            (resp.dre_document_id,),
        ).fetchone()
        assert doc_row == ("dre", "active")

        run_row = db.execute(
            "SELECT model_name, status, review_status, parsed_json IS NOT NULL "
            "FROM dre_extraction_runs WHERE id = ?",
            (resp.extraction_run_id,),
        ).fetchone()
        assert run_row[0] == "manual"
        assert run_row[1] == "succeeded"
        assert run_row[2] == "pending"
        assert run_row[3] == 1


class TestManualEntryPromotesThroughStandardPipeline:
    def test_fixed_setup_promotes_successfully(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        resp = create_manual_extraction_run(
            property_id=pid,
            setup_type="fixed_equal",
            pools=[
                ManualPoolEntry(
                    pool_key="operating", allocation_method="equal",
                    annual_amount=Decimal("120000"),
                )
            ],
            groups=[],
            units=[],
            created_by="ops@example.com",
            connection=db,
        )

        approval = approve_extraction_run(
            property_id=pid, extraction_run_id=resp.extraction_run_id,
            setup_type="fixed", reviewed_by="ops@example.com", connection=db,
        )

        pool_row = db.execute(
            "SELECT allocation_method FROM allocation_pools "
            "WHERE assessment_setup_id = ? AND pool_key = 'operating'",
            (approval.promoted_setup_id,),
        ).fetchone()
        assert pool_row[0] == "equal"

    def test_grouped_setup_promotes_successfully(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        resp = create_manual_extraction_run(
            property_id=pid,
            setup_type="grouped_category",
            pools=[
                ManualPoolEntry(
                    pool_key="operating", allocation_method="equal",
                    annual_amount=Decimal("120000"),
                )
            ],
            groups=[
                ManualGroupEntry(group_id="residential", label="Residential", unit_count=8),
                ManualGroupEntry(group_id="commercial", label="Commercial", unit_count=2),
            ],
            units=[],
            created_by="ops@example.com",
            connection=db,
        )

        approval = approve_extraction_run(
            property_id=pid, extraction_run_id=resp.extraction_run_id,
            setup_type="grouped", reviewed_by="ops@example.com", connection=db,
        )
        rows = db.execute(
            "SELECT group_name, unit_count FROM assessment_groups "
            "WHERE assessment_setup_id = ? ORDER BY display_order",
            (approval.promoted_setup_id,),
        ).fetchall()
        assert rows == [("Residential", 8), ("Commercial", 2)]

    def test_per_unit_setup_promotes_successfully(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        resp = create_manual_extraction_run(
            property_id=pid,
            setup_type="individual_unit",
            pools=[
                ManualPoolEntry(
                    pool_key="operating", allocation_method="square_footage",
                    annual_amount=Decimal("12000"),
                    denominator_value=Decimal("2000"),
                )
            ],
            groups=[],
            units=[
                ManualUnitEntry(unit_number="101", square_feet=Decimal("1000")),
                ManualUnitEntry(unit_number="102", square_feet=Decimal("1000")),
            ],
            created_by="ops@example.com",
            connection=db,
        )

        approval = approve_extraction_run(
            property_id=pid, extraction_run_id=resp.extraction_run_id,
            setup_type="per_unit", reviewed_by="ops@example.com", connection=db,
        )
        rows = db.execute(
            "SELECT unit_number, square_feet FROM assessment_units "
            "WHERE assessment_setup_id = ? ORDER BY unit_number",
            (approval.promoted_setup_id,),
        ).fetchall()
        assert rows == [("101", 1000), ("102", 1000)]

    def test_incomplete_proportional_pool_blocked_like_ccr(
        self, db: sqlite3.Connection
    ) -> None:
        """A manual per_unit setup declaring a proportional allocation
        method but missing per-unit factors must be blocked exactly like a
        CC&R run with the same gap — the operator can still forget to
        enter square footage for a unit."""
        pid = _pid(db)
        resp = create_manual_extraction_run(
            property_id=pid,
            setup_type="individual_unit",
            pools=[
                ManualPoolEntry(
                    pool_key="operating", allocation_method="square_footage",
                    annual_amount=Decimal("12000"),
                )
            ],
            groups=[],
            units=[],  # no per-unit data entered at all
            created_by="ops@example.com",
            connection=db,
        )

        with pytest.raises(MissingUnitFactors):
            approve_ccr_extraction_run(
                property_id=pid, extraction_run_id=resp.extraction_run_id,
                setup_type="per_unit", reviewed_by="ops@example.com", connection=db,
            )

    def test_incomplete_proportional_pool_blocked_via_plain_dre_endpoint(
        self, db: sqlite3.Connection
    ) -> None:
        """A manual run is tagged document_type='dre', so its natural route
        is the plain DRE approve endpoint (not the CC&R one) — the guard
        must fire there too, not just when the caller happens to pick the
        CC&R-specific function.
        """
        pid = _pid(db)
        resp = create_manual_extraction_run(
            property_id=pid,
            setup_type="individual_unit",
            pools=[
                ManualPoolEntry(
                    pool_key="operating", allocation_method="square_footage",
                    annual_amount=Decimal("12000"),
                )
            ],
            groups=[],
            units=[],
            created_by="ops@example.com",
            connection=db,
        )

        with pytest.raises(MissingUnitFactors):
            approve_extraction_run(
                property_id=pid, extraction_run_id=resp.extraction_run_id,
                setup_type="per_unit", reviewed_by="ops@example.com", connection=db,
            )
        assert db.execute(
            "SELECT COUNT(*) FROM assessment_setups WHERE property_id = ?", (pid,)
        ).fetchone()[0] == 0
