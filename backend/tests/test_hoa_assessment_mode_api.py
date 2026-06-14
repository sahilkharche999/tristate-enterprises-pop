def test_update_hoa_round_trips_assessment_mode(client):
    response = client.put(
        "/hoa/9",
        json={
            "hoa_code": "401A",
            "name": "401 HOA Updated",
            "tax_id": "11-1111111",
            "units": 52,
            "fiscal_year_start_month": 2,
            "assessment_mode": "fixed",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["assessment_mode"] == "fixed"

    refreshed = client.get("/hoa/9")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["assessment_mode"] == "fixed"
