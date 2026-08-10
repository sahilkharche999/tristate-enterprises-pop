"""Portfolio readiness summary — status honesty for HOA list cards."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services import portfolio_summary as ps


def test_max_iso_handles_mixed_naive_and_aware_timestamps():
    """Production DB mixes naive SQLite stamps with ISO-Z offsets."""
    winner = ps._max_iso(
        [
            "2026-03-21 10:00:00",
            "2026-08-01T12:00:00+00:00",
            "2026-07-01T08:00:00Z",
            None,
            "",
        ]
    )
    assert winner is not None
    # Aug 1 UTC should win over March / July
    assert "2026-08-01" in winner


def test_parse_ts_normalizes_to_utc_aware():
    naive = ps._parse_ts("2026-01-15 09:30:00")
    aware = ps._parse_ts("2026-01-15T09:30:00Z")
    assert naive is not None and aware is not None
    assert naive.tzinfo is not None
    assert aware.tzinfo is not None
    # Comparable without TypeError
    assert isinstance(naive > aware, bool)
    assert isinstance(aware, datetime)
    assert aware.tzinfo == timezone.utc or aware.utcoffset() is not None


def test_portfolio_status_never_completed_when_needs_action():
    steps = [
        {"id": "budget_draft", "status": "needs_action", "label": "Budget draft", "fix_path": "/hoa/1"},
        {"id": "appendices", "status": "done", "label": "Appendices"},
    ]
    status = ps._portfolio_status_from_steps(
        steps, has_active_draft=False, latest_version_id=None
    )
    assert status != "Completed"
    assert status in {ps.STATUS_NOT_STARTED, ps.STATUS_IN_PROGRESS}


def test_portfolio_status_ready_when_all_done():
    steps = [
        {"id": "budget_draft", "status": "done", "label": "Budget"},
        {"id": "mapping", "status": "not_required", "label": "Mapping"},
        {"id": "settings", "status": "done", "label": "Settings"},
    ]
    status = ps._portfolio_status_from_steps(
        steps, has_active_draft=True, latest_version_id=1
    )
    assert status == ps.STATUS_READY


def test_next_action_prefers_needs_action():
    steps = [
        {"id": "budget_draft", "status": "done", "label": "Budget draft", "fix_path": "/hoa/3"},
        {
            "id": "assessment_mapping",
            "status": "needs_action",
            "label": "Assessment mapping",
            "fix_path": "/hoa/3/assessment-mapping-review",
            "fix_label": "Open mapping review",
        },
    ]
    action = ps._next_action_from_steps(steps, 3)
    assert action is not None
    assert "Assessment mapping" in action["label"]
    assert action["href"] == "/hoa/3/assessment-mapping-review"


def test_list_hoa_includes_portfolio_fields(client):
    response = client.get("/hoa")
    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list)
    if not rows:
        return
    row = rows[0]
    assert "portfolio_status" in row
    assert row["portfolio_status"] != "Completed" or row.get("readiness_pct", 0) == 100
    # Incomplete readiness must never show Completed
    if (row.get("readiness_pct") or 0) < 100 and row.get("readiness_total"):
        assert row["portfolio_status"] != "Completed"
    assert "readiness_pct" in row
    assert "next_action" in row
    assert "has_active_draft" in row
