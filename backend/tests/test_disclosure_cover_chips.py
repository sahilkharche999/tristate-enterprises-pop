"""Cover chips: annual reserve contribution and fiscal-year-end dates."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services import boilerplate_variables as bv


def _hoa(**over):
    base = dict(
        name="Chip HOA",
        city="Oakland",
        state="CA",
        units=9,
        entity_type=None,
        incorporation_year=None,
        fiscal_year_end_month=12,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_reserve_annual_contribution_is_twelve_times_monthly():
    var_map = bv.build_var_map(
        hoa=_hoa(),
        fiscal_year=2026,
        hoa_settings={},
        computed={
            "reserve_funding_facts": {
                "monthly_total": Decimal("8100.50"),
                "monthly_per_unit": Decimal("900.06"),
            }
        },
    )
    assert var_map["reserve_monthly_contribution"] == "$8,100.50"
    assert var_map["reserve_annual_contribution"] == "$97,206.00"


def test_reserve_annual_prefers_facts_annual_contribution():
    var_map = bv.build_var_map(
        hoa=_hoa(),
        fiscal_year=2026,
        hoa_settings={},
        computed={
            "reserve_funding_facts": {
                "monthly_total": Decimal("100.00"),
                "annual_contribution": Decimal("1200.03"),
            }
        },
    )
    assert var_map["reserve_annual_contribution"] == "$1,200.03"


def test_fiscal_year_end_dates_use_property_month():
    var_map = bv.build_var_map(
        hoa=_hoa(fiscal_year_end_month=6),
        fiscal_year=2026,
        hoa_settings={},
        computed={},
    )
    assert var_map["fiscal_year_end_date"] == "June 30, 2026"
    assert var_map["prior_fiscal_year_end_date"] == "June 30, 2025"


def test_fiscal_year_end_defaults_to_december_31():
    var_map = bv.build_var_map(
        hoa=_hoa(fiscal_year_end_month=None),
        fiscal_year=2026,
        hoa_settings={},
        computed={},
    )
    assert var_map["fiscal_year_end_date"] == "December 31, 2026"
    assert var_map["prior_fiscal_year_end_date"] == "December 31, 2025"


def test_leap_year_february_end():
    var_map = bv.build_var_map(
        hoa=_hoa(fiscal_year_end_month=2),
        fiscal_year=2024,
        hoa_settings={},
        computed={},
    )
    assert var_map["fiscal_year_end_date"] == "February 29, 2024"
    assert var_map["prior_fiscal_year_end_date"] == "February 28, 2023"


def test_percent_funded_still_comes_from_cash_over_liability():
    var_map = bv.build_var_map(
        hoa=_hoa(),
        fiscal_year=2026,
        hoa_settings={},
        computed={
            "reserve_liability_facts": {
                "cash_reserve_balance_eoy_prior": Decimal("410000"),
                "total_estimated_liability": Decimal("980000"),
                "percent_funded": "41.8",
            }
        },
    )
    assert var_map["percent_funded"] == "41.8%"
    assert var_map["cash_reserve_balance"] == "$410,000"
