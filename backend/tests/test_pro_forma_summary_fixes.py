"""§5570 summary: the reserve cash it prints, and item-4 rows saved from Settings.

Rendered to HTML through the package's Jinja environment, so these run without
WeasyPrint's system libraries.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.disclosure_package.render import _build_env
from app.disclosure_package.service import _reserve_cash_is_zero
from tests.test_special_assessment_wording import _base_context


def _summary_text(ctx: dict) -> str:
    html = _build_env().get_template("pro_forma_disclosure_summary.html").render(**ctx)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_summary_prints_the_years_cash_not_last_years_setting():
    # Old Mill 2027: Settings still holds 2026's $2.6M; the year map holds 2027's cash.
    ctx = _base_context([])
    ctx["hoa_settings"]["reserve_cash_balance_eoy_prior"] = 2600000.0
    ctx["computed"]["estimated_replacement_fund_cash"] = Decimal("2260449.87")

    text = _summary_text(ctx)

    assert "end of the current fiscal year is $2,260,450" in text
    assert "$2,600,000" not in text


def test_item_4_row_saved_from_settings_renders():
    # The Settings form saves due date, amount and purpose; it never writes frequency.
    ctx = _base_context([])
    ctx["computed"]["additional_assessments_needed"] = [
        {"due_date": "2027-01-01", "amount_per_unit": 150, "purpose": "Roof"}
    ]

    assert "2027-01-01 $150.00" in _summary_text(ctx)


def _five_year_row(year: int, liability: int, ending: int, pct: int) -> dict:
    return {"year": year, "estimated_liability": liability, "ending_balance": ending, "percent_funded": pct}


def test_item_7_prints_approved_assessments_and_funding_plan_separately():
    ctx = _base_context([])
    ctx["computed"]["five_year_existing_assessments"] = [_five_year_row(2028, 2878000, 2413000, 84)]
    ctx["computed"]["thirty_year_funding_plan"] = [
        _five_year_row(2028, 2878000, 2428000, 84),
        *[_five_year_row(year, 1, 1, 1) for year in range(2029, 2060)],
    ]

    text = _summary_text(ctx)

    approved = text.index("only assessments already approved")
    plan = text.index("reserve funding plan approved by the association is implemented")
    assert approved < text.index("$2,413,000") < plan < text.index("$2,428,000")
    # The funding-plan table stops at five years.
    assert text.count("Percent Funded") == 2
    assert "2033" not in text


def test_item_7_says_so_when_no_projection_exists():
    assert _summary_text(_base_context([])).count("Five-year projection not available.") == 2


def test_zero_cash_warning_judges_the_cash_the_package_prints():
    assert _reserve_cash_is_zero({"reserve_cash_balance_eoy_prior": 0.0}, 2027, [])
    assert not _reserve_cash_is_zero(
        {
            "reserve_cash_balance_eoy_prior": 0.0,
            "reserve_cash_by_fiscal_year_json": '{"2027": 2260449.87}',
        },
        2027,
        [],
    )
    assert _reserve_cash_is_zero({"reserve_cash_by_fiscal_year_json": '{"2027": 0}'}, 2027, [])
    assert not _reserve_cash_is_zero({}, 2027, [])
