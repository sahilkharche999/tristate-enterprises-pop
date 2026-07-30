"""Reserve component lines default to a 'Reserve Detail' disposition in the
assessment-mapping review so the operator isn't forced to click each one
(July 2026 client ask). Display-only default; an explicit disposition still wins,
and it never touches the regular assessment basis.
"""
import sqlite3

import pytest

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


def test_contribution_line_cannot_be_reserve_detail(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)
    rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_lines=[
            {
                "label": "Reserve - Allocation/Transfer",
                "category": "operating",
                "account_code": "90000",
                "annual_budget": 1000,
            },
        ],
        connection=conn,
    )
    xfer = rows[0]
    assert xfer["row_role"] == "current_year_reserve_contribution_line"
    assert xfer["included_in_regular_basis"] is True
    with pytest.raises(ValueError, match="cannot be marked reserve detail"):
        set_assessment_review_row_disposition(
            property_id=property_id,
            assessment_setup_id=setup_id,
            budget_year=2026,
            budget_draft_id=None,
            row=xfer,
            disposition_state="reserve_detail",
            actor="tester",
            note="",
            connection=conn,
        )


def test_mapping_lookup_survives_empty_section_fund_type(conn: sqlite3.Connection) -> None:
    """Saved mapping with section/fund_type=operating still hits lines with blanks."""
    from app.services.assessment_budget_mapping_rule_service import (
        _lookup_mapping_row,
    )

    mapped_by_key = {
        ("reserve allocation transfer", "operating", "operating", "operating", "90000"): (
            "reserve allocation transfer",
            "operating",
            "operating",
            "operating",
            "90000",
            "reserve_contributions",
            "operator",
            "ready",
        ),
    }
    hit = _lookup_mapping_row(
        mapped_by_key,
        key=("reserve allocation transfer", "", "operating", "", "90000"),
    )
    assert hit is not None
    assert hit[5] == "reserve_contributions"


def test_stale_reserve_detail_disposition_on_contribution_is_ignored(
    conn: sqlite3.Connection,
) -> None:
    """Legacy bad disposition reserve_detail on contribution is coerced to clear."""
    property_id, setup_id = _setup(conn)
    lines = [
        {
            "label": "Reserve - Allocation/Transfer",
            "category": "operating",
            "section": "operating",
            "fund_type": "operating",
            "account_code": "90000",
            "annual_budget": 172496,
        },
    ]
    rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_lines=lines,
        connection=conn,
    )
    xfer = rows[0]
    # Write with full section/fund key shape, then rebuild with blanks (key drift).
    set_assessment_review_row_disposition(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_draft_id=None,
        row=xfer,
        disposition_state="excluded_non_regular",
        actor="tester",
        note="",
        connection=conn,
    )
    # Force a bad historical state via SQL (API now rejects reserve_detail).
    conn.execute(
        """
        UPDATE assessment_review_row_dispositions
           SET disposition_state = 'reserve_detail'
         WHERE property_id = ? AND assessment_setup_id = ?
           AND review_line_key = ?
        """,
        (property_id, setup_id, xfer["line_key"]),
    )
    conn.commit()
    drifted = [
        {
            "label": "Reserve - Allocation/Transfer",
            "category": "operating",
            "section": None,
            "fund_type": None,
            "account_code": "90000",
            "annual_budget": 172496,
        },
    ]
    after = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_lines=drifted,
        connection=conn,
    )[0]
    assert after["row_role"] == "current_year_reserve_contribution_line"
    assert after["current_status"] != "reserve_detail"
    assert after["included_in_regular_basis"] is True


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
