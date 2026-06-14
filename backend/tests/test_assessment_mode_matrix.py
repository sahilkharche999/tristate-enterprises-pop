from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.disclosure_package.assessment_schedule_matrix import (
    build_matrix_for_assessment_mode,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(
        "INSERT INTO properties (id, name, units, assessment_mode) VALUES (1, 'Test HOA', 10, 'variable')"
    )
    conn.commit()
    yield conn
    conn.close()


def _budget_draft(amount: str = "12000") -> SimpleNamespace:
    return SimpleNamespace(
        line_items=[
            {
                "label": "Assessment Income",
                "section": "income",
                "category": "income",
                "fund_type": "operating",
                "annual_budget": float(amount),
                "amount": float(amount),
                "account_code": "40000",
            }
        ]
    )


def test_fixed_mode_builds_synthetic_equal_allocation_summary(db: sqlite3.Connection):
    matrix = build_matrix_for_assessment_mode(
        connection=db,
        property_id=1,
        fiscal_year=2026,
        budget_draft=_budget_draft("12000"),
        hoa_name="Fixed HOA",
        unit_count=10,
        approved_assessment_revenue_annual=Decimal("12000"),
        assessment_mode="fixed",
    )

    assert matrix.recipient_grain == "summary"
    assert matrix.preflight_issues == []
    summary_row = matrix.rows[0]
    assert summary_row.recipient_label == "All Units"
    assert summary_row.unit_count == 10
    assert summary_row.total_monthly_per_recipient == Decimal("100.00")
    assert summary_row.total_annual_revenue == Decimal("12000")


def test_fixed_mode_blocks_when_unit_count_missing(db: sqlite3.Connection):
    matrix = build_matrix_for_assessment_mode(
        connection=db,
        property_id=1,
        fiscal_year=2026,
        budget_draft=_budget_draft("12000"),
        hoa_name="Fixed HOA",
        unit_count=0,
        approved_assessment_revenue_annual=Decimal("12000"),
        assessment_mode="fixed",
    )

    assert any(issue.severity == "blocking" for issue in matrix.preflight_issues)
    assert any(issue.field_path == "hoa_metadata.units" for issue in matrix.preflight_issues)
    assert "unit count" in matrix.preflight_issues[0].message.lower()


def test_variable_mode_without_dre_directs_operator_to_upload_dre(
    db: sqlite3.Connection,
):
    matrix = build_matrix_for_assessment_mode(
        connection=db,
        property_id=1,
        fiscal_year=2026,
        budget_draft=_budget_draft("12000"),
        hoa_name="Variable HOA",
        unit_count=10,
        approved_assessment_revenue_annual=Decimal("12000"),
        assessment_mode="variable",
    )

    assert any(issue.severity == "blocking" for issue in matrix.preflight_issues)
    message = matrix.preflight_issues[0].message.lower()
    assert "dre" in message
    assert "upload" in message


def test_variable_mode_with_uploaded_dre_directs_operator_to_review(
    db: sqlite3.Connection,
):
    db.execute(
        """
        INSERT INTO dre_documents (property_id, file_id, file_name, status)
        VALUES (1, 'dre/1/test.pdf', 'test.pdf', 'active')
        """
    )
    db.commit()

    matrix = build_matrix_for_assessment_mode(
        connection=db,
        property_id=1,
        fiscal_year=2026,
        budget_draft=_budget_draft("12000"),
        hoa_name="Variable HOA",
        unit_count=10,
        approved_assessment_revenue_annual=Decimal("12000"),
        assessment_mode="variable",
    )

    assert any(issue.severity == "blocking" for issue in matrix.preflight_issues)
    message = matrix.preflight_issues[0].message.lower()
    assert "dre" in message
    assert "review" in message
