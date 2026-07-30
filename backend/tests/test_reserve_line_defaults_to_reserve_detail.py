"""Reserve component lines default to a 'Reserve Detail' disposition in the
assessment-mapping review so the operator isn't forced to click each one
(July 2026 client ask). Display-only default; an explicit disposition still wins,
and it never touches the regular assessment basis.
"""
import sqlite3

from app.services.assessment_budget_mapping_rule_service import (
    build_assessment_mapping_review_rows,
    set_assessment_review_row_disposition,
)
from tests.test_assessment_budget_mapping_rule_service import _setup, conn  # noqa: F401


def _rows(conn, property_id, setup_id):
    rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_lines=[
            {"label": "Roof Replacement", "category": "reserve_expense",
             "fund_type": "reserve", "amount": 5000},
            {"label": "Insurance", "category": "operating",
             "fund_type": "operating", "amount": 500},
        ],
        connection=conn,
    )
    return {r["line_label"]: r for r in rows}


def test_reserve_component_line_defaults_to_reserve_detail(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)
    by_label = _rows(conn, property_id, setup_id)

    roof = by_label["Roof Replacement"]
    assert roof["row_role"] == "reserve_component_detail"
    assert roof["current_status"] == "reserve_detail"
    # A regular operating line is unaffected by the reserve default.
    assert by_label["Insurance"]["current_status"] != "reserve_detail"


def test_explicit_disposition_overrides_reserve_default(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)
    by_label = _rows(conn, property_id, setup_id)
    roof = by_label["Roof Replacement"]

    # Operator explicitly excludes it -> the default no longer applies.
    set_assessment_review_row_disposition(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_draft_id=None,
        row=roof,
        disposition_state="excluded_non_regular",
        actor="tester",
        note="",
        connection=conn,
    )
    after = _rows(conn, property_id, setup_id)["Roof Replacement"]
    assert after["current_status"] == "excluded_non_regular"


def test_clear_on_default_reserve_detail_opts_into_schedule_basis(
    conn: sqlite3.Connection,
) -> None:
    """Clear must un-default reserve detail (not re-apply the same default)."""
    property_id, setup_id = _setup(conn)
    roof = _rows(conn, property_id, setup_id)["Roof Replacement"]
    assert roof["current_status"] == "reserve_detail"
    assert roof["included_in_regular_basis"] is False
    assert roof.get("has_explicit_disposition") is False

    set_assessment_review_row_disposition(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_draft_id=None,
        row=roof,
        disposition_state="clear",
        actor="tester",
        note="operator clear",
        connection=conn,
    )
    after = _rows(conn, property_id, setup_id)["Roof Replacement"]
    assert after.get("has_explicit_disposition") is True
    assert after["disposition_state"] == "clear"
    # No longer stuck on default reserve_detail.
    assert after["current_status"] != "reserve_detail"
    assert after["included_in_regular_basis"] is True
    # Assignable path: needs pool assignment (suggested/unresolved/needs_disposition).
    assert after["current_status"] in {
        "suggested",
        "unresolved",
        "needs_disposition",
    }


def test_clear_then_reserve_detail_returns_outside_basis(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)
    roof = _rows(conn, property_id, setup_id)["Roof Replacement"]
    set_assessment_review_row_disposition(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_draft_id=None,
        row=roof,
        disposition_state="clear",
        actor="tester",
        note="",
        connection=conn,
    )
    set_assessment_review_row_disposition(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_draft_id=None,
        row=roof,
        disposition_state="reserve_detail",
        actor="tester",
        note="",
        connection=conn,
    )
    after = _rows(conn, property_id, setup_id)["Roof Replacement"]
    assert after["current_status"] == "reserve_detail"
    assert after["included_in_regular_basis"] is False
