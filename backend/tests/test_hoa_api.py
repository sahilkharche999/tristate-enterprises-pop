from app.ai_implementation import database as database_module


def test_list_hoas(client):
    response = client.get("/hoa")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 9
    assert payload[0]["hoa_code"] == "1"
    # fiscal_year_end_month auto-derived from start_month=4 → end=3 (March)
    assert payload[0]["fiscal_year_end_month"] == 3


def test_get_hoa_detail(client):
    response = client.get("/hoa/2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "July Heights HOA"
    assert payload["city"] == "Oakland"
    assert payload["workflow_status"] == "In Progress"
    assert payload["reserve_inflation_rate"] == 0.0


def test_update_hoa(client):
    update_payload = {
        "hoa_code": "401A",
        "name": "401 HOA Updated",
        "tax_id": "11-1111111",
        "units": 52,
        "reserve_inflation_rate": 0.04,
        "fiscal_year_start_month": 2,
    }

    update_response = client.put("/hoa/9", json=update_payload)
    assert update_response.status_code == 200

    refreshed_response = client.get("/hoa/9")
    assert refreshed_response.status_code == 200
    payload = refreshed_response.json()

    assert payload["hoa_code"] == "401A"
    assert payload["name"] == "401 HOA Updated"
    assert payload["tax_id"] == "11-1111111"
    assert payload["units"] == 52
    assert payload["reserve_inflation_rate"] == 0.04
    assert payload["fiscal_year_start_month"] == 2
    # Auto-derived: start=2 (Feb) → end=1 (Jan)
    assert payload["fiscal_year_end_month"] == 1


def test_create_hoa(client):
    create_payload = {
        "name": "New Harbor HOA",
        "units": 88,
        "fiscal_year_start_month": 5,
    }

    create_response = client.post("/hoa", json=create_payload)
    assert create_response.status_code == 200

    payload = create_response.json()
    assert payload["name"] == "New Harbor HOA"
    assert payload["units"] == 88
    assert payload["fiscal_year_start_month"] == 5
    # Auto-derived: start=5 (May) → end=4 (April)
    assert payload["fiscal_year_end_month"] == 4
    assert payload["hoa_code"] == ""
    assert payload["tax_id"] == ""
    assert payload["reserve_inflation_rate"] == 0.0
    assert payload["city"] == ""
    assert payload["workflow_status"] == "Not Started"

    list_response = client.get("/hoa")
    assert list_response.status_code == 200
    hoas = list_response.json()
    created = next((hoa for hoa in hoas if hoa["name"] == "New Harbor HOA"), None)
    assert created is not None
    assert created["portfolio_year"] is not None


def test_create_hoa_duplicate_name_returns_conflict(client):
    create_payload = {
        "name": "Esprit park",
        "units": 120,
        "fiscal_year_start_month": 1,
    }

    response = client.post("/hoa", json=create_payload)

    assert response.status_code == 409
    assert response.json()["detail"] == "HOA code or name already exists"


def test_hoa_requests_do_not_run_migrations(client, monkeypatch):
    def fail_if_called():
        raise AssertionError("ensure_property_columns should not run during HOA requests")

    monkeypatch.setattr(database_module, "ensure_property_columns", fail_if_called)

    list_response = client.get("/hoa")
    assert list_response.status_code == 200

    detail_response = client.get("/hoa/1")
    assert detail_response.status_code == 200

    update_response = client.put(
        "/hoa/1",
        json={
            "name": "Esprit Park Owners Association",
            "fiscal_year_start_month": 4,
        },
    )
    assert update_response.status_code == 200
    # Verify auto-derived: start=4 (Apr) → end=3 (Mar)
    assert update_response.json()["fiscal_year_end_month"] == 3

    create_response = client.post(
        "/hoa",
        json={
            "name": "Migration Safe HOA",
            "units": 42,
            "fiscal_year_start_month": 1,
        },
    )
    assert create_response.status_code == 200
    # Verify auto-derived: start=1 (Jan) → end=12 (Dec)
    assert create_response.json()["fiscal_year_end_month"] == 12
