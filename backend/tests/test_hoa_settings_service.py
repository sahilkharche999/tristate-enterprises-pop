import json

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
        payload={
            "management_company": "Acme",
            "reserve_cash_balance_eoy_prior": 1234.5,
            "financial_packet_archetype": "reserve-only",
            "reserve_interest_income_override": 22000.0,
        },
    )
    assert s.management_company == "Acme"
    assert s.reserve_cash_balance_eoy_prior == 1234.5
    assert s.financial_packet_archetype == "reserve-only"
    assert s.reserve_interest_income_override == 22000.0


def test_special_assessment_legacy_non_mmddyyyy_due_date_does_not_block_save(session):
    """A pre-existing special assessment with a legacy (pre-format) due_date
    must NOT block saving disclosure settings. The frontend's save() resends
    the whole settings blob (not a diff) on every save, so a hard rejection
    here would block an operator from saving ANY unrelated setting the
    moment one HOA has an old-format due_date already stored — verified
    during implementation, not a hypothetical. Only the frontend's
    date-picker enforces MM/DD/YYYY for entries an operator actually edits."""
    prop = Property(name="Z", units=10, hoa_code="Z")
    session.add(prop); session.flush()
    hoa_settings_service.get_or_create(session, hoa_id=prop.id)

    legacy_payload = json.dumps([
        {"due_date": "2026-07-01", "amount_per_unit": 100, "purpose": "Roof"},
    ])
    s = hoa_settings_service.update(
        session, hoa_id=prop.id,
        payload={"special_assessments_json": legacy_payload},
    )
    assert json.loads(s.special_assessments_json)[0]["due_date"] == "2026-07-01"


def test_special_assessment_well_formed_due_date_round_trips(session):
    prop = Property(name="ZZ", units=10, hoa_code="ZZ")
    session.add(prop); session.flush()
    hoa_settings_service.get_or_create(session, hoa_id=prop.id)

    good_payload = json.dumps([
        {"due_date": "07/01/2026", "amount_per_unit": 100, "purpose": "Roof"},
    ])
    s = hoa_settings_service.update(
        session, hoa_id=prop.id,
        payload={"special_assessments_json": good_payload},
    )
    assert json.loads(s.special_assessments_json)[0]["due_date"] == "07/01/2026"


def test_special_assessment_blank_due_date_is_allowed(session):
    prop = Property(name="ZZZ", units=10, hoa_code="ZZZ")
    session.add(prop); session.flush()
    hoa_settings_service.get_or_create(session, hoa_id=prop.id)

    payload = json.dumps([
        {"due_date": "", "amount_per_unit": 100, "purpose": "Roof"},
    ])
    s = hoa_settings_service.update(
        session, hoa_id=prop.id,
        payload={"special_assessments_json": payload},
    )
    assert json.loads(s.special_assessments_json)[0]["due_date"] == ""


def test_get_response_minus_property_id_round_trips_through_update(session):
    """Regression guard for the has_logo bug: the frontend's save() resends
    whatever GET returned (minus property_id and has_logo, its two derived/
    read-only fields) as the PUT payload. Every other key _row_to_dict emits
    must therefore be in _ALLOWED_FIELDS, or a save of unrelated settings
    breaks for every HOA the moment that key is populated. This mirrors
    _row_to_dict's shape directly (not a hand-picked subset) so a newly added
    derived field would fail here immediately instead of only in production."""
    prop = Property(name="ROUNDTRIP", units=5, hoa_code="ROUNDTRIP")
    session.add(prop); session.flush()
    hoa_settings_service.get_or_create(session, hoa_id=prop.id)

    from app.routers.hoa_settings import _row_to_dict
    row = hoa_settings_service.get_or_create(session, hoa_id=prop.id)
    get_response = _row_to_dict(row)
    writable = {k: v for k, v in get_response.items() if k not in ("property_id", "has_logo")}

    # Must not raise ValueError("Unknown field: ...")
    hoa_settings_service.update(session, hoa_id=prop.id, payload=writable)


def test_legacy_frequency_field_does_not_error(session):
    """Task 5.3: dropping the frequency field from the form must not break
    loading pre-existing entries that still have it stored."""
    prop = Property(name="ZZZZ", units=10, hoa_code="ZZZZ")
    session.add(prop); session.flush()
    hoa_settings_service.get_or_create(session, hoa_id=prop.id)

    legacy_payload = json.dumps([
        {"due_date": "07/01/2026", "amount_per_unit": 100, "frequency": "month", "purpose": "Roof"},
    ])
    s = hoa_settings_service.update(
        session, hoa_id=prop.id,
        payload={"special_assessments_json": legacy_payload},
    )
    stored = json.loads(s.special_assessments_json)[0]
    assert stored["frequency"] == "month"
    assert stored["due_date"] == "07/01/2026"
