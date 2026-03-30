import json

import pytest

from app.ai_implementation.db import (
    BUDGET_DRAFT_ACTIVE,
    BUDGET_VERSION_STAGE_INTERIM,
    BudgetDraft,
    BudgetVersion,
    Property,
)


def test_save_draft_uses_hoa_reserve_inflation_without_note(
    client,
    db_session,
    budget_compare_seed,
):
    hoa = db_session.get(Property, budget_compare_seed["hoa_id"])
    assert hoa is not None
    hoa.reserve_inflation_rate = 0.05
    db_session.commit()

    response = client.patch(
        "/hoa/9/budget/draft",
        json={
            "draft_id": budget_compare_seed["draft_id"],
            "line_items": budget_compare_seed["draft_line_items"],
            "global_note": "Seeded compare draft",
            "statement_month": 8,
            "growth_factor": 1.25,
            "growth_factor_note": "stub:1",
        },
    )

    assert response.status_code == 200
    assert response.json()["draft"]["reserve_inflation_rate"] == pytest.approx(0.05)
    assert response.json()["draft"]["reserve_inflation_note"] is None


def test_compare_recommends_latest_final_and_accepts_manual_baseline(
    client,
    db_session,
    budget_compare_seed,
):
    options_response = client.get("/hoa/9/budget/compare/options")

    assert options_response.status_code == 200
    options_payload = options_response.json()
    assert options_payload["draft_id"] == budget_compare_seed["draft_id"]
    assert options_payload["recommended_baseline_version_id"] == budget_compare_seed["final_version_id"]
    assert options_payload["recommended_baseline_reason"] == "Latest Final"
    recommended_option = next(
        option for option in options_payload["baseline_versions"] if option["is_recommended"]
    )
    assert recommended_option["id"] == budget_compare_seed["final_version_id"]
    assert recommended_option["version_code"] == "V2"
    assert recommended_option["stage"] == "Final"

    final_version = db_session.get(BudgetVersion, budget_compare_seed["final_version_id"])
    assert final_version is not None
    final_version.stage = BUDGET_VERSION_STAGE_INTERIM
    db_session.commit()

    fallback_response = client.get("/hoa/9/budget/compare/options")

    assert fallback_response.status_code == 200
    fallback_payload = fallback_response.json()
    assert (
        fallback_payload["recommended_baseline_version_id"]
        == budget_compare_seed["latest_generated_version_id"]
    )
    assert fallback_payload["recommended_baseline_reason"] == "Latest generated (no Final version yet)"

    compare_response = client.post(
        f"/hoa/9/budget/drafts/{budget_compare_seed['draft_id']}/compare",
        json={
            "baseline_version_id": budget_compare_seed["latest_generated_version_id"],
            "changed_only": True,
        },
    )

    assert compare_response.status_code == 200
    assert compare_response.json()["baseline_version_id"] == budget_compare_seed["latest_generated_version_id"]


def test_compare_returns_only_non_reserve_draft_rows(client, budget_compare_seed):
    response = client.post(
        f"/hoa/9/budget/drafts/{budget_compare_seed['draft_id']}/compare",
        json={
            "baseline_version_id": budget_compare_seed["final_version_id"],
            "changed_only": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["draft_id"] == budget_compare_seed["draft_id"]
    assert payload["baseline_version_id"] == budget_compare_seed["final_version_id"]
    assert payload["changed_only"] is True

    labels = [row["label"] for row in payload["draft_compare_rows"]]
    assert labels == ["55000 - General Insurance"]

    insurance_row = payload["draft_compare_rows"][0]
    assert insurance_row["line_item_key"] == "55000"
    assert insurance_row["category"] == "operating"
    assert insurance_row["reserve_group"] is None
    assert insurance_row["is_reserve"] is False
    assert insurance_row["reserve_inflation_eligible"] is False
    assert insurance_row["changed"] is True
    assert insurance_row["baseline_amount"] == pytest.approx(177708.0)
    assert insurance_row["inflation_adjusted_baseline_amount"] == pytest.approx(177708.0)
    assert insurance_row["current_amount"] == pytest.approx(186593.4)
    assert insurance_row["delta_amount"] == pytest.approx(8885.4)
    assert insurance_row["delta_percent"] == pytest.approx(5.0)
    assert insurance_row["note_title"] == "Insurance renewal"
    assert "5 percent increase" in insurance_row["note_body"]

    draft_summary = payload["draft_compare_summary"]
    assert draft_summary["baseline_amount"] == pytest.approx(177708.0)
    assert draft_summary["current_amount"] == pytest.approx(186593.4)
    assert draft_summary["delta_amount"] == pytest.approx(8885.4)
    assert draft_summary["delta_percent"] == pytest.approx(5.0)

    assert payload["reserve_component_rows"] == []
    assert payload["reserve_review_summary"] == {
        "baseline_amount": 0.0,
        "inflation_adjusted_amount": 0.0,
        "impact_amount": 0.0,
        "eligible_component_count": 0,
    }


def test_compare_detects_percent_change_differences_when_annual_budget_matches(
    client,
    db_session,
    budget_compare_seed,
):
    draft = db_session.get(BudgetDraft, budget_compare_seed["draft_id"])
    baseline_version = db_session.get(BudgetVersion, budget_compare_seed["final_version_id"])

    assert draft is not None
    assert baseline_version is not None
    assert draft.status == BUDGET_DRAFT_ACTIVE

    shared_annual_budget = 177708.0
    draft_line_items = budget_compare_seed["draft_line_items"]
    baseline_line_items = json.loads(baseline_version.line_items_json)

    draft_line_items[0]["annual_budget"] = shared_annual_budget
    draft_line_items[0]["percent_change"] = 0.0
    baseline_line_items[0]["annual_budget"] = shared_annual_budget
    baseline_line_items[0]["percent_change"] = 5.0

    draft.line_items_json = json.dumps(draft_line_items)
    baseline_version.line_items_json = json.dumps(baseline_line_items)
    db_session.commit()

    response = client.post(
        f"/hoa/9/budget/drafts/{budget_compare_seed['draft_id']}/compare",
        json={
            "baseline_version_id": budget_compare_seed["final_version_id"],
            "changed_only": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    insurance_row = next(
        row for row in payload["draft_compare_rows"] if row["label"] == "55000 - General Insurance"
    )
    assert insurance_row["baseline_amount"] == pytest.approx(186593.4)
    assert insurance_row["current_amount"] == pytest.approx(177708.0)
    assert insurance_row["delta_amount"] == pytest.approx(-8885.4)
    assert insurance_row["delta_percent"] == pytest.approx((-8885.4 / 186593.4) * 100)


def test_reserve_inflation_persists_to_version_and_audit(client, budget_compare_seed):
    update_response = client.put(
        "/hoa/9",
        json={
            "name": "401 HOA",
            "hoa_code": "401",
            "tax_id": "89-0123456",
            "units": 48,
            "reserve_inflation_rate": 0.05,
            "fiscal_year_start_month": 1,
            "fiscal_year_end_month": 12,
        },
    )

    assert update_response.status_code == 200

    save_response = client.patch(
        "/hoa/9/budget/draft",
        json={
            "draft_id": budget_compare_seed["draft_id"],
            "line_items": budget_compare_seed["draft_line_items"],
            "global_note": "Seeded compare draft",
            "statement_month": 8,
            "growth_factor": 1.25,
            "growth_factor_note": "stub:1",
        },
    )

    assert save_response.status_code == 200
    save_payload = save_response.json()
    assert save_payload["draft"]["reserve_inflation_rate"] == pytest.approx(0.05)
    assert save_payload["draft"]["reserve_inflation_note"] is None

    generate_response = client.post(
        "/hoa/9/budget/generate",
        json={
            "draft_id": budget_compare_seed["draft_id"],
            "line_items": budget_compare_seed["draft_line_items"],
            "global_note": "Seeded compare draft",
        },
    )

    assert generate_response.status_code == 200
    generate_payload = generate_response.json()
    assert generate_payload["version"]["reserve_inflation_rate"] == pytest.approx(0.05)
    assert generate_payload["version"]["reserve_inflation_note"] is None

    history_response = client.get("/hoa/9/history")

    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert history_payload["versions"][0]["reserve_inflation_rate"] == pytest.approx(0.05)
    assert history_payload["versions"][0]["reserve_inflation_note"] is None


def test_compare_remains_non_reserve_even_when_hoa_has_reserve_inflation(
    client,
    db_session,
    budget_compare_seed,
):
    hoa = db_session.get(Property, budget_compare_seed["hoa_id"])
    assert hoa is not None
    hoa.reserve_inflation_rate = 0.40
    db_session.commit()

    compare_response = client.post(
        f"/hoa/9/budget/drafts/{budget_compare_seed['draft_id']}/compare",
        json={
            "baseline_version_id": budget_compare_seed["final_version_id"],
            "changed_only": True,
        },
    )

    assert compare_response.status_code == 200
    payload = compare_response.json()
    labels = [row["label"] for row in payload["draft_compare_rows"]]
    assert "91432 - Circulation Pump 5 H.P." not in labels
    assert payload["reserve_component_rows"] == []
    assert payload["reserve_review_summary"] == {
        "baseline_amount": 0.0,
        "inflation_adjusted_amount": 0.0,
        "impact_amount": 0.0,
        "eligible_component_count": 0,
    }
