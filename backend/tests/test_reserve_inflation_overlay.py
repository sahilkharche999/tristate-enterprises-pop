import pytest

from app.services import budget_history_service


def test_overlay_filters_only_funded_reserve_rows():
    overlay_fn = getattr(budget_history_service, "_apply_reserve_inflation_overlay")
    rows = [
        {
            "line_item_key": "91432",
            "label": "91432 - Circulation Pump 5 H.P.",
            "category": "reserve",
            "reserve_group": "component",
            "is_reserve": True,
            "baseline_amount": 109008.0,
            "current_amount": 114458.4,
        },
        {
            "line_item_key": "90000",
            "label": "90000 - Reserve - Allocation/Transfer",
            "category": "reserve",
            "reserve_group": "transfer",
            "is_reserve": True,
            "baseline_amount": 24000.0,
            "current_amount": 24000.0,
        },
        {
            "line_item_key": "45000",
            "label": "45000 - Reserve Income",
            "category": "reserve",
            "reserve_group": "income",
            "is_reserve": True,
            "baseline_amount": 12000.0,
            "current_amount": 12000.0,
        },
        {
            "line_item_key": "47000",
            "label": "47000 - Interest Earned Reserve",
            "category": "reserve",
            "reserve_group": "income",
            "is_reserve": True,
            "baseline_amount": 8600.0,
            "current_amount": 8600.0,
        },
        {
            "line_item_key": "93620",
            "label": "93620 - Roof",
            "category": "reserve",
            "reserve_group": "component",
            "is_reserve": True,
            "baseline_amount": 0.0,
            "current_amount": 0.0,
        },
    ]

    overlaid_rows = overlay_fn(rows, reserve_inflation_rate=0.05)

    funded_component_row = next(
        row for row in overlaid_rows if row["label"] == "91432 - Circulation Pump 5 H.P."
    )
    assert funded_component_row["reserve_inflation_eligible"] is True
    assert funded_component_row["inflation_adjusted_baseline_amount"] == pytest.approx(114458.4)

    reserve_transfer_row = next(
        row for row in overlaid_rows if row["label"] == "90000 - Reserve - Allocation/Transfer"
    )
    assert reserve_transfer_row["reserve_inflation_eligible"] is False
    assert reserve_transfer_row["inflation_adjusted_baseline_amount"] == pytest.approx(24000.0)

    reserve_income_row = next(row for row in overlaid_rows if row["label"] == "45000 - Reserve Income")
    assert reserve_income_row["reserve_inflation_eligible"] is False
    assert reserve_income_row["inflation_adjusted_baseline_amount"] == pytest.approx(12000.0)

    reserve_interest_row = next(
        row for row in overlaid_rows if row["label"] == "47000 - Interest Earned Reserve"
    )
    assert reserve_interest_row["reserve_inflation_eligible"] is False
    assert reserve_interest_row["inflation_adjusted_baseline_amount"] == pytest.approx(8600.0)

    zero_budget_row = next(row for row in overlaid_rows if row["label"] == "93620 - Roof")
    assert zero_budget_row["reserve_inflation_eligible"] is False
    assert zero_budget_row["inflation_adjusted_baseline_amount"] == pytest.approx(0.0)
