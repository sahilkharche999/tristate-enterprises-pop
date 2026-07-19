"""carry_forward_mappings_across_setups tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.budget_line_mapping_service import carry_forward_mappings_across_setups
from tests.support.budget_line_mapping_seed import (
    lookup_saved_mappings,
    seed_budget_line_mapping,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('P', 10)")
    pid = conn.execute("SELECT id FROM properties").fetchone()[0]
    conn.execute(
        """
        INSERT INTO assessment_setups (
            property_id, setup_type, display_mode, status
        ) VALUES (?, 'fixed', 'fixed', 'approved')
        """,
        (pid,),
    )
    old = conn.execute("SELECT id FROM assessment_setups").fetchone()[0]
    conn.execute(
        """
        INSERT INTO assessment_setups (
            property_id, setup_type, display_mode, status
        ) VALUES (?, 'fixed', 'fixed', 'draft')
        """,
        (pid,),
    )
    new = conn.execute(
        "SELECT id FROM assessment_setups ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    conn.commit()
    yield conn, pid, old, new
    conn.close()


def test_carry_forward_copies_mappings(db):
    conn, pid, old, new = db
    seed_budget_line_mapping(
        connection=conn,
        property_id=pid,
        assessment_setup_id=old,
        normalized_label="insurance",
        pool_key="operating",
        section="expense",
        category="operating",
        fund_type="operating",
    )
    n = carry_forward_mappings_across_setups(
        property_id=pid,
        old_setup_id=old,
        new_setup_id=new,
        connection=conn,
    )
    assert n == 1
    saved = lookup_saved_mappings(
        property_id=pid, assessment_setup_id=new, connection=conn,
    )
    assert ("insurance", "expense", "operating", "operating", None) in saved
    assert saved[("insurance", "expense", "operating", "operating", None)] == "operating"
