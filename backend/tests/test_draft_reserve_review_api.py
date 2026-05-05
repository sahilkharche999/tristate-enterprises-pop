import pytest


def test_draft_reserve_review_uses_draft_rows_without_baseline_compare(
    client,
    budget_compare_seed,
):
    response = client.post(
        f"/hoa/9/budget/drafts/{budget_compare_seed['draft_id']}/reserve-review",
        json={
            "line_items": [
                {
                    "line_item_key": "55000",
                    "account_code": 55000,
                    "category": "operating",
                    "label": "55000 - General Insurance",
                    "annual_budget": 186593.4,
                    "percent_change": 0.0,
                    "read_only": False,
                },
                {
                    "line_item_key": "91432",
                    "account_code": 91432,
                    "category": "reserve",
                    "label": "91432 - Circulation Pump 5 H.P.",
                    "annual_budget": 114458.4,
                    "percent_change": 0.0,
                    "read_only": True,
                    "reserve_group": "component",
                    "raw": {"section": "Reserve Expenses (Per Reserve Study)"},
                },
                {
                    "line_item_key": "93620",
                    "account_code": 93620,
                    "category": "reserve",
                    "label": "93620 - LED Exit Lights",
                    "annual_budget": 0.0,
                    "percent_change": 0.0,
                    "read_only": True,
                    "reserve_group": "component",
                    "raw": {"section": "Reserve Expenses (Per Reserve Study)"},
                },
                {
                    "line_item_key": "90000",
                    "account_code": 90000,
                    "category": "reserve",
                    "label": "90000 - Reserve - Allocation/Transfer",
                    "annual_budget": 24000.0,
                    "percent_change": 0.0,
                    "read_only": True,
                    "reserve_group": "transfer",
                    "raw": {"section": "Allocation to Reserves"},
                },
                {
                    "line_item_key": "45000",
                    "account_code": 45000,
                    "category": "reserve",
                    "label": "45000 - Reserve Income",
                    "annual_budget": 12000.0,
                    "percent_change": 0.0,
                    "read_only": True,
                    "reserve_group": "income",
                    "raw": {"section": "Reserve Income"},
                },
            ],
            "reserve_inflation_rate": 0.4,
        },
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["draft_id"] == budget_compare_seed["draft_id"]
    assert payload["reserve_inflation_rate"] == pytest.approx(0.4)

    summary = payload["reserve_review_summary"]
    assert summary["baseline_amount"] == pytest.approx(114458.4)
    assert summary["inflation_adjusted_amount"] == pytest.approx(160241.76)
    assert summary["impact_amount"] == pytest.approx(45783.36)
    assert summary["eligible_component_count"] == 2

    labels = [row["label"] for row in payload["reserve_component_rows"]]
    assert labels == [
        "91432 - Circulation Pump 5 H.P.",
        "93620 - LED Exit Lights",
    ]

    first_row = payload["reserve_component_rows"][0]
    assert first_row["baseline_amount"] == pytest.approx(114458.4)
    assert first_row["inflation_adjusted_amount"] == pytest.approx(160241.76)
    assert first_row["impact_amount"] == pytest.approx(45783.36)

    second_row = payload["reserve_component_rows"][1]
    assert second_row["baseline_amount"] == pytest.approx(0.0)
    assert second_row["inflation_adjusted_amount"] == pytest.approx(0.0)
    assert second_row["impact_amount"] == pytest.approx(0.0)


def test_draft_reserve_review_preserves_saved_reserve_metadata_for_zero_budget_components(
    client,
    budget_compare_seed,
):
    line_items = [
        {
            "line_item_key": "91432",
            "account_code": 91432,
            "category": "reserve",
            "label": "91432 - Circulation Pump 5 H.P.",
            "annual_budget": 114458.4,
            "percent_change": 0.0,
            "read_only": True,
            "reserve_group": "component",
            "raw": {"section": "Reserve Expenses (Per Reserve Study)"},
        },
        {
            "line_item_key": "93620",
            "account_code": 93620,
            "category": "reserve",
            "label": "93620 - LED Exit Lights",
            "annual_budget": 0.0,
            "percent_change": 0.0,
            "read_only": True,
            "reserve_group": "component",
            "raw": {"section": "Reserve Expenses (Per Reserve Study)"},
        },
    ]

    save_response = client.patch(
        "/hoa/9/budget/draft",
        json={
            "draft_id": budget_compare_seed["draft_id"],
            "line_items": line_items,
            "global_note": "Preserve reserve metadata",
            "statement_month": 8,
            "growth_factor": 1.25,
            "growth_factor_note": "stub:1",
            "reserve_inflation_rate": 0.2,
            "reserve_inflation_note": "Client wants reserve review at the draft level.",
        },
    )

    assert save_response.status_code == 200

    review_response = client.post(
        f"/hoa/9/budget/drafts/{budget_compare_seed['draft_id']}/reserve-review",
        json={
            "line_items": line_items,
            "reserve_inflation_rate": 0.2,
        },
    )

    assert review_response.status_code == 200
    payload = review_response.json()
    labels = [row["label"] for row in payload["reserve_component_rows"]]
    assert labels == [
        "91432 - Circulation Pump 5 H.P.",
        "93620 - LED Exit Lights",
    ]
