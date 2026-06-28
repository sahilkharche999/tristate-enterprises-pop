"""Demote (un-promote) service tests.

Covers the inverse of ``approve_extraction_run``: unseating a wrongly
promoted DRE/CC&R run, restoring the prior setup (or clearing the
property default for a clean switch to another document), the
not-promoted guard, and the finalized-package guard.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services.dre_approval_service import (
    ExtractionRunNotFound,
    ExtractionRunNotPromoted,
    SetupPinnedByFinalizedPackage,
    approve_extraction_run,
    demote_extraction_run,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


def _parsed_json() -> str:
    return json.dumps(
        {
            "document_metadata": {
                "association_name": "Test",
                "total_units": 20,
                "source_pages": [1],
            },
            "assessment_setup": {
                "setup_type": "grouped_category",
                "display_mode": "grouped",
                "source_pages": [6],
            },
            "unit_structure": {
                "unit_count": 20,
                "group_count": 0,
                "groups": [],
                "units": [],
            },
            "allocation_pools": [
                {
                    "pool_key": "total_budget_equal",
                    "parent_pool_key": "total_budget",
                    "pool_name": "Equal",
                    "annual_amount": "102451",
                    "monthly_amount": "8538",
                    "allocation_method": "equal",
                    "recipient_scope": "all_units",
                    "denominator_label": "units",
                    "denominator_value": "20",
                    "denominator_source": "dre_shown",
                    "included_budget_lines": [],
                    "excluded_budget_lines": [],
                    "budget_line_derivation": "residual_default",
                    "residual_after_pool_keys": [],
                    "residual_exclusions": [],
                    "source_pages": [6],
                    "confidence": 0.95,
                }
            ],
            "formulas": [],
            "validation_checks": [],
            "human_review_questions": [],
        }
    )


def _new_run(conn: sqlite3.Connection, pid: int, *, document_type: str = "dre") -> int:
    conn.execute(
        "INSERT INTO dre_documents (property_id, file_id, file_name, status, document_type) "
        "VALUES (?, ?, 'x.pdf', 'active', ?)",
        (pid, f"{document_type}/{pid}/x.pdf", document_type),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO dre_extraction_runs "
        "(dre_document_id, property_id, model_name, prompt_version, prompt_sha256, "
        " status, parsed_json, document_type) "
        "VALUES (?, ?, 'gemini-flash-latest', '1.0.0', 'abc', 'succeeded', ?, ?)",
        (doc_id, pid, _parsed_json(), document_type),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('Test', 10)")
    yield conn
    conn.close()


def _pid(db: sqlite3.Connection) -> int:
    return db.execute("SELECT id FROM properties LIMIT 1").fetchone()[0]


class TestDemoteClearsDefaultWhenNoPrior:
    def test_demote_single_promotion_clears_default(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        rid = _new_run(db, pid)
        promo = approve_extraction_run(
            property_id=pid, extraction_run_id=rid, setup_type="grouped",
            reviewed_by="ops@x.com", connection=db,
        )
        # Pre-demote: property default points at the promoted setup.
        default_before = db.execute(
            "SELECT default_assessment_setup_id FROM properties WHERE id = ?", (pid,)
        ).fetchone()[0]
        assert default_before == promo.promoted_setup_id

        resp = demote_extraction_run(
            property_id=pid, extraction_run_id=rid, reviewed_by="ops@x.com",
            connection=db,
        )

        assert resp.demoted_setup_id == promo.promoted_setup_id
        assert resp.restored_setup_id is None
        assert resp.default_assessment_setup_id is None

        # Setup superseded, run reverted, default cleared.
        setup_status = db.execute(
            "SELECT status FROM assessment_setups WHERE id = ?",
            (promo.promoted_setup_id,),
        ).fetchone()[0]
        assert setup_status == "superseded"

        run = db.execute(
            "SELECT review_status, promoted_setup_id, promoted_at "
            "FROM dre_extraction_runs WHERE id = ?", (rid,),
        ).fetchone()
        assert run == ("approved", None, None)

        default_after = db.execute(
            "SELECT default_assessment_setup_id FROM properties WHERE id = ?", (pid,)
        ).fetchone()[0]
        assert default_after is None


class TestDemoteRestoresPrior:
    def test_demote_restores_the_superseded_prior_setup(
        self, db: sqlite3.Connection
    ) -> None:
        pid = _pid(db)
        # First (correct) promotion.
        rid1 = _new_run(db, pid)
        promo1 = approve_extraction_run(
            property_id=pid, extraction_run_id=rid1, setup_type="grouped",
            reviewed_by="ops@x.com", connection=db,
        )
        # Second (wrong) promotion supersedes the first.
        rid2 = _new_run(db, pid)
        promo2 = approve_extraction_run(
            property_id=pid, extraction_run_id=rid2, setup_type="grouped",
            reviewed_by="ops@x.com", connection=db,
        )
        assert db.execute(
            "SELECT status FROM assessment_setups WHERE id = ?",
            (promo1.promoted_setup_id,),
        ).fetchone()[0] == "superseded"

        # Demote the wrong one → prior restored.
        resp = demote_extraction_run(
            property_id=pid, extraction_run_id=rid2, reviewed_by="ops@x.com",
            connection=db,
        )
        assert resp.demoted_setup_id == promo2.promoted_setup_id
        assert resp.restored_setup_id == promo1.promoted_setup_id
        assert resp.default_assessment_setup_id == promo1.promoted_setup_id

        assert db.execute(
            "SELECT status FROM assessment_setups WHERE id = ?",
            (promo1.promoted_setup_id,),
        ).fetchone()[0] == "approved"
        assert db.execute(
            "SELECT status FROM assessment_setups WHERE id = ?",
            (promo2.promoted_setup_id,),
        ).fetchone()[0] == "superseded"


class TestDemoteGuards:
    def test_demote_unpromoted_run_raises(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        rid = _new_run(db, pid)  # never promoted
        with pytest.raises(ExtractionRunNotPromoted):
            demote_extraction_run(
                property_id=pid, extraction_run_id=rid, reviewed_by="ops@x.com",
                connection=db,
            )

    def test_demote_missing_run_raises(self, db: sqlite3.Connection) -> None:
        pid = _pid(db)
        with pytest.raises(ExtractionRunNotFound):
            demote_extraction_run(
                property_id=pid, extraction_run_id=999999, reviewed_by="ops@x.com",
                connection=db,
            )

    def test_demote_refuses_when_finalized_package_pins_setup(
        self, db: sqlite3.Connection
    ) -> None:
        pid = _pid(db)
        rid = _new_run(db, pid)
        promo = approve_extraction_run(
            property_id=pid, extraction_run_id=rid, setup_type="grouped",
            reviewed_by="ops@x.com", connection=db,
        )
        db.execute(
            "INSERT INTO annual_packages "
            "(property_id, assessment_setup_id, budget_year, fiscal_year, status) "
            "VALUES (?, ?, 2026, 2026, 'finalized')",
            (pid, promo.promoted_setup_id),
        )
        db.commit()
        with pytest.raises(SetupPinnedByFinalizedPackage) as exc:
            demote_extraction_run(
                property_id=pid, extraction_run_id=rid, reviewed_by="ops@x.com",
                connection=db,
            )
        assert promo.promoted_setup_id == exc.value.setup_id

    def test_demote_allows_when_only_draft_package_references_setup(
        self, db: sqlite3.Connection
    ) -> None:
        pid = _pid(db)
        rid = _new_run(db, pid)
        promo = approve_extraction_run(
            property_id=pid, extraction_run_id=rid, setup_type="grouped",
            reviewed_by="ops@x.com", connection=db,
        )
        db.execute(
            "INSERT INTO annual_packages "
            "(property_id, assessment_setup_id, budget_year, fiscal_year, status) "
            "VALUES (?, ?, 2026, 2026, 'draft')",
            (pid, promo.promoted_setup_id),
        )
        db.commit()
        # Draft packages do not block demotion.
        resp = demote_extraction_run(
            property_id=pid, extraction_run_id=rid, reviewed_by="ops@x.com",
            connection=db,
        )
        assert resp.demoted_setup_id == promo.promoted_setup_id
