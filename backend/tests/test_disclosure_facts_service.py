"""Disclosure facts from uploads fill unset HOA settings; set values are only suggested."""
from __future__ import annotations

import itertools
import json

_names = itertools.count(1)


def _fact(printed, number=None, *, year=None, as_of=None, page=3, quote="as printed"):
    return {"printed": printed, "number": number, "applies_to_year": year, "as_of_date": as_of,
            "page": page, "quote": quote}


# What Gemini read from Levy's 2027 Old Mill report (VM probe, 2026-10-20).
LEVY_2027 = {
    "budget_fiscal_year": 2027,
    "monthly_assessment_per_unit": _fact("$605.00", 605.0, year=2027),
    "monthly_assessment_per_unit_prior_year": _fact("$605.00", 605.0, year=2026),
    "reserve_portion_of_monthly_assessment": _fact("$241.61", 241.61, year=2027, page=13),
    "reserve_balance_start_of_budget_year": _fact("$ 2,260,000", 2260000.0, year=2027, as_of="2026-12-31", page=14),
    "percent_funded": _fact("46%", 46.0, year=2027, page=14),
    "reserve_study_preparer": _fact("SMA Reserves of San Jose", page=4),
    "reserve_study_date": _fact("September 2026", as_of="2026-09-01", page=4),
    "cpa_firm_name": _fact("Levy, Erlanger & Company LLP"),
    "reserve_interest_rate": _fact("3%", 3.0, page=16),
    "reserve_interest_rate_basis": "before_tax",
    "planned_assessment_increases": [
        {"start_year": None, "end_year": 2036, "rate_percent": 2.0},
        {"start_year": None, "end_year": 2046, "rate_percent": 1.5},
        {"start_year": None, "end_year": 2056, "rate_percent": 1.0},
    ],
    "outstanding_loan_balance": None,
    "outstanding_loan_lender": None,
}


def _hoa(db_session, *, portfolio_year=2027, **settings):
    from app.ai_implementation.db.models import HOASettings, Property

    n = next(_names)
    prop = Property(name=f"Facts Test HOA {n}", units=279, hoa_code=f"FT{n}", portfolio_year=portfolio_year)
    db_session.add(prop)
    db_session.flush()
    db_session.add(HOASettings(property_id=prop.id, **settings))
    db_session.commit()
    return prop


def _apply(db_session, prop, facts):
    from app.services.disclosure_facts_service import apply_disclosure_facts

    return {f["field"] + (f["label"] if f["field"].startswith("reserve_cash") else ""): f
            for f in apply_disclosure_facts(db_session, hoa=prop, facts=facts, upload_id=24, filename="OML1227PRODFT.pdf")}


def test_package_year_facts_fill_unset_fields(db_session):
    from app.ai_implementation.db.models import HOASettings

    prop = _hoa(db_session)
    findings = _apply(db_session, prop, LEVY_2027)
    row = db_session.query(HOASettings).filter_by(property_id=prop.id).one()

    assert row.approved_monthly_assessment_per_unit == 605.0
    assert row.monthly_assessment_per_unit_prior == 605.0
    assert row.replacement_fund_monthly_assessment_per_unit == 241.61
    assert json.loads(row.reserve_cash_by_fiscal_year_json) == {"2027": 2260000.0}
    assert (row.reserve_cash_balance_eoy_prior, row.reserve_cash_as_of_date) == (2260000.0, "2026-12-31")
    assert json.loads(row.assessment_increase_schedule_json) == [
        {"start_year": 2028, "end_year": 2036, "rate": 0.02},
        {"start_year": 2037, "end_year": 2046, "rate": 0.015},
        {"start_year": 2047, "end_year": 2056, "rate": 0.01},
    ]
    assert row.reserve_study_date == "September 2026"  # printed wording, which the preflight reads
    assert findings["approved_monthly_assessment_per_unit"]["status"] == "applied"
    # Overrides of computed values and the after-tax rate are never filled.
    assert row.percent_funded_override is None
    assert findings["percent_funded_override"]["status"] == "suggested"
    assert findings["interest_rate_after_tax"]["value"] == "3% before tax"
    assert row.version_int == 1
    provenance = json.loads(row.disclosure_facts_json)
    assert provenance["replacement_fund_monthly_assessment_per_unit"]["page"] == 13
    assert provenance["replacement_fund_monthly_assessment_per_unit"]["filename"] == "OML1227PRODFT.pdf"


def test_operator_values_are_kept_and_differences_suggested(db_session):
    from app.ai_implementation.db.models import HOASettings

    prop = _hoa(
        db_session,
        approved_monthly_assessment_per_unit=600.0,
        cpa_firm_name="Levy, Erlanger & Company LLP",
        reserve_study_expert_name="Another Firm",
        reserve_cash_by_fiscal_year_json=json.dumps({"2027": 2600000}),
    )
    findings = _apply(db_session, prop, LEVY_2027)
    row = db_session.query(HOASettings).filter_by(property_id=prop.id).one()

    assert row.approved_monthly_assessment_per_unit == 600.0
    assert findings["approved_monthly_assessment_per_unit"]["status"] == "suggested"
    assert findings["cpa_firm_name"]["status"] == "matches"
    assert row.reserve_study_expert_name == "Another Firm"
    assert findings["reserve_study_expert_name"]["status"] == "suggested"
    assert json.loads(row.reserve_cash_by_fiscal_year_json) == {"2027": 2600000}


def test_last_years_package_only_fills_the_prior_year_assessment(db_session):
    from app.ai_implementation.db.models import HOASettings

    prop = _hoa(db_session, portfolio_year=2028)
    _apply(db_session, prop, LEVY_2027)
    row = db_session.query(HOASettings).filter_by(property_id=prop.id).one()

    assert row.approved_monthly_assessment_per_unit is None
    assert row.monthly_assessment_per_unit_prior == 605.0
    # The 2027 opening balance is still right for 2027, so it goes in the year map
    # without touching the package-year (2028) cash fields.
    assert json.loads(row.reserve_cash_by_fiscal_year_json) == {"2027": 2260000.0}
    assert not row.reserve_cash_balance_eoy_prior


def test_undated_reserve_balance_is_only_suggested(db_session):
    from app.ai_implementation.db.models import HOASettings

    prop = _hoa(db_session)
    facts = {"budget_fiscal_year": 2027,
             "reserve_balance_start_of_budget_year": _fact("$ 2,200,000", 2200000.0, page=27)}
    findings = _apply(db_session, prop, facts)
    row = db_session.query(HOASettings).filter_by(property_id=prop.id).one()

    assert row.reserve_cash_by_fiscal_year_json in (None, "", "{}")
    assert [f["status"] for f in findings.values()] == ["suggested"]


def test_stale_settings_save_is_refused_after_an_upload_filled_fields(client, db_session):
    prop = _hoa(db_session)
    loaded = client.get(f"/hoa/{prop.id}/settings/disclosure").json()
    _apply(db_session, prop, LEVY_2027)
    db_session.commit()  # as create_upload does after applying facts

    stale = client.put(
        f"/hoa/{prop.id}/settings/disclosure",
        json={"letter_signed_by": "Board"},
        headers={"If-Match": str(loaded["version_int"])},
    )
    assert stale.status_code == 412

    fresh = client.get(f"/hoa/{prop.id}/settings/disclosure").json()
    assert fresh["disclosure_facts"]["approved_monthly_assessment_per_unit"]["status"] == "applied"
    saved = client.put(
        f"/hoa/{prop.id}/settings/disclosure",
        json={"letter_signed_by": "Board", "version_int": fresh["version_int"], "disclosure_facts": {}},
        headers={"If-Match": str(fresh["version_int"])},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version_int"] == fresh["version_int"] + 1


def test_an_old_package_only_suggests(db_session):
    from app.ai_implementation.db.models import HOASettings

    prop = _hoa(db_session, portfolio_year=2027)
    old_package = {
        "budget_fiscal_year": 2025,
        "reserve_study_date": _fact("October 2024", as_of="2024-10-01", page=26),
        "planned_assessment_increases": [{"start_year": 2025, "end_year": 2034, "rate_percent": 1.5}],
        "reserve_balance_start_of_budget_year": _fact("2,200,000", 2200000.0, as_of="2024-12-31", page=27),
    }
    findings = _apply(db_session, prop, old_package)
    row = db_session.query(HOASettings).filter_by(property_id=prop.id).one()

    assert row.reserve_study_date is None
    assert findings["reserve_study_date"]["status"] == "suggested"
    assert row.assessment_increase_schedule_json in (None, "[]")
    # Dated cash still lands under its own year (2025), harmless for 2027.
    assert json.loads(row.reserve_cash_by_fiscal_year_json) == {"2025": 2200000.0}


def test_matching_compares_rates_exactly_and_json_by_structure():
    from app.services.disclosure_facts_service import _same

    assert not _same(0.018, 0.02)            # 1.8% vs 2% is a different rate
    assert _same(605.0, 605.004)             # money: within a cent
    # A schedule saved by the form (JSON.stringify) vs the found value.
    assert _same('[{"start_year":2028,"end_year":2036,"rate":0.02}]',
                 [{"start_year": 2028, "end_year": 2036, "rate": 0.02}])
    assert _same('{"balance":5000,"lender":"Bank"}', {"balance": 5000.0, "lender": "Bank"})


def test_reserve_cash_provenance_carries_its_date(db_session):
    from app.ai_implementation.db.models import HOASettings

    prop = _hoa(db_session)
    _apply(db_session, prop, LEVY_2027)
    row = db_session.query(HOASettings).filter_by(property_id=prop.id).one()
    entry = json.loads(row.disclosure_facts_json)["reserve_cash_by_fiscal_year_json:2027"]
    assert (entry["value"], entry["as_of_date"]) == (2260000.0, "2026-12-31")

