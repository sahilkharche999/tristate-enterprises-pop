"""HOASettings ORM smoke test — exercises round-trip persistence."""
from decimal import Decimal

from app.ai_implementation.db.models import HOASettings, Property


def test_hoa_settings_round_trip(session):
    """Save a fully-populated settings row and read it back unchanged."""
    prop = Property(name="Test HOA", units=10, hoa_code="T1")
    session.add(prop)
    session.flush()

    settings = HOASettings(
        property_id=prop.id,
        management_company="Acme Mgmt",
        management_company_address="1 Main St",
        management_company_phone="555-0100",
        management_company_fax="555-0101",
        management_company_web="acme.example",
        cpa_firm_name="Acme CPA",
        cpa_firm_address="2 Main St",
        reserve_study_expert_name="Acme Reserves",
        reserve_cash_balance_eoy_prior=2_500_000.0,
        fund_balance_boy_operations=0.0,
        monthly_assessment_per_unit_prior=605.0,
        interest_rate_after_tax=0.018,
        replacement_cost_increase_rate=0.03,
        assessment_increase_schedule_json='[[2026,2035,0.03]]',
        letter_signed_by="Board of Directors",
    )
    session.add(settings)
    session.commit()

    refetched = session.query(HOASettings).filter_by(property_id=prop.id).one()
    assert refetched.management_company == "Acme Mgmt"
    assert refetched.reserve_cash_balance_eoy_prior == 2_500_000.0
    assert refetched.assessment_increase_schedule_json == '[[2026,2035,0.03]]'
