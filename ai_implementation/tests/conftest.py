"""Shared fixtures for AI pipeline tests."""
import sqlite3
import pytest
from pathlib import Path
import sys

# Ensure ai_implementation is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_implementation.database import init_db, get_db


@pytest.fixture
def db():
    """In-memory SQLite database with initialized schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Run schema from database.py
    from ai_implementation.database import SCHEMA_SQL, TRAINING_INDEX_SQL
    conn.executescript(SCHEMA_SQL)
    try:
        conn.executescript(TRAINING_INDEX_SQL)
    except sqlite3.OperationalError:
        pass

    # Create a property
    conn.execute("INSERT INTO properties (id, name) VALUES (1, 'Test Property')")
    # Create a suggestion run
    conn.execute("""
        INSERT INTO suggestion_runs (id, property_id, source, statement_month)
        VALUES (1, 1, 'casebase_seed', 8)
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def seeded_db(db):
    """Database with sample feedback_cases."""
    cases = [
        (60000, "Electricity & Gas", 43200.0, 27285.97, -0.094, 6, 600, 60000, 0, 0, 0),
        (80000, "Landscaping", 24000.0, 1200.0, 0.05, 8, 800, 80000, 0, 0, 0),
        (41170, "Regular Assessments", 360000.0, 240000.0, 0.24, 4, 411, 41170, 1, 0, 0),
        (55000, "Insurance", 32000.0, 16000.0, -0.15, 5, 550, 55000, 0, 0, 1),
        (91000, "Roof Reserve Study", 24000.0, 0.0, 0.0, 9, 910, 91000, 0, 1, 0),
    ]
    for i, (ac, name, budget, ytd, pct, l1, l2, l3, is_inc, is_res, is_adm) in enumerate(cases):
        db.execute("""
            INSERT INTO feedback_cases (
                run_id, property_id, account_code, account_name, label, category,
                account_level_1, account_level_2, account_level_3,
                is_income, is_reserve, is_admin,
                annual_budget, ytd_actual,
                adjusted_pct_diff, adjusted_coverage_ratio,
                seasonality_index, normalized_annual_budget,
                user_final_pct_change, user_decision
            ) VALUES (1, 1, ?, ?, ?, 'operating', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.667, 0.5, ?, 'accepted')
        """, (ac, name, f"{ac} - {name}", l1, l2, l3, is_inc, is_res, is_adm, budget, ytd, pct, pct, pct))
    db.commit()
    return db
