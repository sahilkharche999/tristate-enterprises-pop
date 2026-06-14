from __future__ import annotations

import json


def _line_items() -> list[dict[str, object]]:
    return [
        {
            "line_item_key": "5010",
            "account_code": "5010",
            "label": "Elevators",
            "normalized_label": "elevators",
            "section": "expense",
            "category": "operating",
            "fund_type": "operating",
            "annual_budget": 2400.0,
            "projection": 2500.0,
        },
        {
            "line_item_key": "5015",
            "account_code": "5015",
            "label": "Elevator Service",
            "normalized_label": "elevator service",
            "section": "expense",
            "category": "operating",
            "fund_type": "operating",
            "annual_budget": 1800.0,
            "projection": 1900.0,
        },
    ]


def _identity(account_code: str, label: str, line_item_key: str) -> dict[str, str]:
    return {
        "account_code": account_code,
        "label": label,
        "normalized_label": " ".join(label.lower().split()),
        "line_item_key": line_item_key,
        "section": "expense",
        "category": "operating",
        "fund_type": "operating",
    }


def _seed_active_draft(db_session) -> int:
    raw = db_session.connection().connection
    raw.execute("DELETE FROM budget_drafts WHERE property_id = 1")
    raw.execute(
        """
        INSERT INTO assessment_setups (property_id, setup_type, display_mode, status)
        VALUES (1, 'fixed', 'fixed', 'approved')
        """
    )
    setup_id = raw.execute(
        "SELECT id FROM assessment_setups WHERE property_id = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    raw.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = 1",
        (setup_id,),
    )
    raw.execute(
        """
        INSERT INTO budget_drafts (
            property_id, status, line_items_json, version_int, actor_name
        )
        VALUES (1, 'active', ?, 0, 'tester')
        """,
        (json.dumps(_line_items(), sort_keys=True),),
    )
    draft_id = raw.execute(
        "SELECT id FROM budget_drafts WHERE property_id = 1"
    ).fetchone()[0]
    db_session.commit()
    return int(draft_id)


def test_budget_gl_merge_commit_list_and_unmerge_api(client, db_session):
    _seed_active_draft(db_session)

    missing_header = client.post(
        "/hoa/1/budget/merges",
        json={
            "primary": _identity("5010", "Elevators", "5010"),
            "secondary": _identity("5015", "Elevator Service", "5015"),
            "source": "manual",
        },
    )
    assert missing_header.status_code == 428

    committed = client.post(
        "/hoa/1/budget/merges",
        headers={"If-Match": '"0"'},
        json={
            "primary": _identity("5010", "Elevators", "5010"),
            "secondary": _identity("5015", "Elevator Service", "5015"),
            "source": "manual",
        },
    )
    assert committed.status_code == 200, committed.text
    payload = committed.json()
    assert payload["application"]["status"] == "applied"
    assert payload["draft_version"] == 1

    listed = client.get("/hoa/1/budget/merges")
    assert listed.status_code == 200
    assert listed.json()[0]["application_status"] == "applied"

    raw = db_session.connection().connection
    raw.execute(
        """
        INSERT INTO budget_versions (
            property_id, source_draft_id, version_number, version_code, stage,
            line_items_json, total_income, total_expense, net_operating_income,
            fiscal_year_start_month, fiscal_year_end_month,
            created_by_name, actor_name
        )
        VALUES (
            1, 1, 1, 'V1', 'Interim', ?, 0, 4200, -4200,
            1, 12, 'tester', 'tester'
        )
        """,
        (json.dumps(_line_items(), sort_keys=True),),
    )
    version_id = raw.execute(
        "SELECT id FROM budget_versions WHERE property_id = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    db_session.commit()

    finalized = client.patch(
        f"/hoa/1/versions/{version_id}",
        json={"stage": "Final"},
    )
    assert finalized.status_code == 200, finalized.text

    unmerge_after_final = client.post(
        f"/hoa/1/budget/merges/applications/{payload['application']['id']}/unmerge",
        headers={"If-Match": '"1"'},
    )
    assert unmerge_after_final.status_code == 409



def test_budget_gl_merge_suggest_api_returns_local_fallback(client, db_session):
    _seed_active_draft(db_session)

    response = client.post("/hoa/1/budget/merges/suggest")

    assert response.status_code == 200, response.text
    suggestions = response.json()
    assert suggestions[0]["primary_account_code"] == "5010"
    assert suggestions[0]["secondary_account_code"] == "5015"
    assert suggestions[0]["local_only"] is True
