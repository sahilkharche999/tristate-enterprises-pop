"""API tests for /hoa/{hoa_id}/settings/disclosure.

Pattern: the `client` fixture already attaches a Bearer token for the seeded
test user (see conftest.py), and `db_session` opens a Session against the SAME
SQLite DB that the FastAPI app uses. So creating a Property via `db_session`
lets the router find it.
"""
from __future__ import annotations


def test_get_returns_defaults_for_new_hoa(client, db_session):
    from app.ai_implementation.db.models import Property
    prop = Property(name="API HOA", units=5, hoa_code="A1")
    db_session.add(prop); db_session.commit()

    r = client.get(f"/hoa/{prop.id}/settings/disclosure")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["property_id"] == prop.id
    assert body["replacement_cost_increase_rate"] == 0.03
    assert body["management_company"] is None


def test_put_updates_fields(client, db_session):
    from app.ai_implementation.db.models import Property
    prop = Property(name="API HOA 2", units=5, hoa_code="A2")
    db_session.add(prop); db_session.commit()

    r = client.put(
        f"/hoa/{prop.id}/settings/disclosure",
        json={"management_company": "Beta Mgmt", "reserve_cash_balance_eoy_prior": 1_500_000},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["management_company"] == "Beta Mgmt"
    assert body["reserve_cash_balance_eoy_prior"] == 1_500_000


def test_put_rejects_unknown_field_with_400(client, db_session):
    from app.ai_implementation.db.models import Property
    prop = Property(name="API HOA 3", units=5, hoa_code="A3")
    db_session.add(prop); db_session.commit()

    r = client.put(
        f"/hoa/{prop.id}/settings/disclosure",
        json={"not_a_real_field": "oops"},
    )
    assert r.status_code == 400, r.text


def test_get_unknown_hoa_returns_404(client):
    r = client.get("/hoa/9999999/settings/disclosure")
    assert r.status_code == 404, r.text
