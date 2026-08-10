"""Offline (temp SQLite) integration for workspace-status-actions-settings-ux.

No live DB, no network, no production data.
"""

from __future__ import annotations

from app.services import portfolio_summary as ps


def test_lying_completed_workflow_status_not_shown_as_ready(client, db_session):
    """P0-1: seeded Completed must not force Ready when package is incomplete."""
    from app.ai_implementation.db.models import Property

    prop = Property(
        name="Sharon Ridge Offline",
        units=12,
        hoa_code="SR-OFF",
        portfolio_year=2026,
        workflow_status="Completed",  # the lie
        assessment_mode="variable",
    )
    db_session.add(prop)
    db_session.commit()

    rows = client.get("/hoa").json()
    row = next(r for r in rows if r["id"] == prop.id)
    assert row["workflow_status"] == "Completed"  # raw column still exists
    assert row["portfolio_status"] != "Completed"
    assert row["portfolio_status"] in {
        ps.STATUS_NOT_STARTED,
        ps.STATUS_IN_PROGRESS,
        ps.STATUS_READY,
    }
    # Incomplete HOA with no budget must not be Ready for package
    if (row.get("readiness_pct") or 0) < 100:
        assert row["portfolio_status"] != ps.STATUS_READY
    assert row.get("next_action") is not None
    assert "label" in row["next_action"]
    assert "href" in row["next_action"]
    assert "has_active_draft" in row
    assert "latest_budget_version_id" in row
    assert "readiness_pct" in row


def test_list_hoas_never_returns_completed_as_portfolio_status(client):
    """Portfolio chip source of truth must never be the word Completed."""
    rows = client.get("/hoa").json()
    assert isinstance(rows, list)
    for row in rows:
        assert row.get("portfolio_status") != "Completed", row


def test_portfolio_year_update_does_not_clear_disclosure_settings(client, db_session):
    """H: year rollover prefill — portfolio_year change must not wipe cash/settings."""
    from app.ai_implementation.db.models import Property

    prop = Property(
        name="Year Stable HOA",
        units=8,
        hoa_code="YS1",
        portfolio_year=2026,
        workflow_status="In Progress",
    )
    db_session.add(prop)
    db_session.commit()

    put = client.put(
        f"/hoa/{prop.id}/settings/disclosure",
        json={
            "management_company": "Stable Mgmt LLC",
            "reserve_cash_balance_eoy_prior": 250000.5,
            "reserve_funding_source": "manual",
            "reserve_funding_manual_amount": 12000,
        },
    )
    assert put.status_code == 200, put.text
    before = put.json()
    assert before["management_company"] == "Stable Mgmt LLC"
    assert before["reserve_cash_balance_eoy_prior"] == 250000.5

    # Roll package year (HOA Database field)
    upd = client.put(
        f"/hoa/{prop.id}",
        json={
            "name": "Year Stable HOA",
            "fiscal_year_start_month": 1,
            "portfolio_year": 2027,
            "units": 8,
        },
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["portfolio_year"] == 2027

    after = client.get(f"/hoa/{prop.id}/settings/disclosure").json()
    assert after["management_company"] == "Stable Mgmt LLC"
    assert after["reserve_cash_balance_eoy_prior"] == 250000.5
    assert after["reserve_funding_source"] == "manual"
    assert after["reserve_funding_manual_amount"] == 12000


def test_preflight_fix_path_includes_disclosure_tab(client, db_session):
    """Deep-link for cash/settings should include progressive tab=money."""
    from app.ai_implementation.db.models import Property
    from app.disclosure_package.service import _attach_fix_links
    from app.disclosure_package.schemas import PreflightError

    prop = Property(name="Fix Link HOA", units=3, hoa_code="FL1", portfolio_year=2026)
    db_session.add(prop)
    db_session.commit()

    errs = _attach_fix_links(
        [
            PreflightError(
                field_path="hoa_settings.reserve_cash_balance_eoy_prior",
                message="Cash missing",
                severity="warning",
            )
        ],
        prop.id,
    )
    assert errs[0].fix_path is not None
    assert "section=disclosure" in errs[0].fix_path
    assert "tab=money" in errs[0].fix_path
    assert "field=reserve_cash_balance_eoy_prior" in errs[0].fix_path


def test_next_action_and_readiness_math_helpers():
    steps = [
        {"id": "budget_draft", "status": "done", "label": "Budget draft", "fix_path": "/hoa/1"},
        {
            "id": "assessment_mapping",
            "status": "needs_action",
            "label": "Assessment mapping",
            "fix_path": "/hoa/1/assessment-mapping-review",
            "fix_label": "Open mapping",
        },
        {"id": "appendices", "status": "not_required", "label": "Appendices"},
    ]
    status = ps._portfolio_status_from_steps(
        steps, has_active_draft=True, latest_version_id=None
    )
    assert status == ps.STATUS_IN_PROGRESS
    action = ps._next_action_from_steps(steps, 1)
    assert action is not None
    assert "Assessment mapping" in action["label"]
    assert action["href"] == "/hoa/1/assessment-mapping-review"

    ready_steps = [
        {"id": "a", "status": "done", "label": "A"},
        {"id": "b", "status": "not_required", "label": "B"},
    ]
    assert (
        ps._portfolio_status_from_steps(
            ready_steps, has_active_draft=True, latest_version_id=9
        )
        == ps.STATUS_READY
    )


def test_list_response_schema_backward_compatible_fields(client):
    """Old clients still get core HOA fields; new fields are additive only."""
    rows = client.get("/hoa").json()
    assert rows
    required_legacy = {
        "id",
        "name",
        "units",
        "fiscal_year_start_month",
        "workflow_status",
        "portfolio_year",
        "assessment_mode",
    }
    for row in rows:
        missing = required_legacy - set(row)
        assert not missing, f"missing legacy fields {missing} in {row.get('name')}"
