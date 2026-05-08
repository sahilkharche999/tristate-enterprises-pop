from app.ai_implementation.db.models import Property
from app.services import hoa_settings_service


def test_get_or_create_returns_default_row_for_new_hoa(session):
    prop = Property(name="X", units=5, hoa_code="X")
    session.add(prop); session.flush()

    s = hoa_settings_service.get_or_create(session, hoa_id=prop.id)
    assert s.property_id == prop.id
    assert s.replacement_cost_increase_rate == 0.03
    assert s.management_company is None  # operator hasn't set it yet


def test_update_persists_fields(session):
    prop = Property(name="Y", units=10, hoa_code="Y")
    session.add(prop); session.flush()
    hoa_settings_service.get_or_create(session, hoa_id=prop.id)

    s = hoa_settings_service.update(
        session, hoa_id=prop.id,
        payload={"management_company": "Acme", "reserve_cash_balance_eoy_prior": 1234.5},
    )
    assert s.management_company == "Acme"
    assert s.reserve_cash_balance_eoy_prior == 1234.5
