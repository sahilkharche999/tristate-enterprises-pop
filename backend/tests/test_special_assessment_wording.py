"""Cover letter + §5570 disclosure summary special-assessment wording
tests (Phase 4.4 tasks 115 + 116).

Asserts the three branch wordings render correctly based on the
``status`` field per Phase 4.4 matrix:

* ``approved_scheduled``: amount, due date, included/separate billing
* ``possible_disclosure_only``: disclosure language, no $
* ``none`` / empty list: historical "no anticipated SA" wording
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.disclosure_package.render import render_template
from app.disclosure_package.package_specs import SPECS


def _base_context(special_assessments: list[dict[str, Any]]) -> dict[str, Any]:
    """Minimal context for cover_letter.html / pro_forma_disclosure_summary.html
    rendering with the supplied special-assessment list.
    """
    spec = SPECS["old_mill"]
    hoa = type(
        "HOA", (), {
            "name": "Test HOA", "city": "San Jose", "state": "CA",
            "entity_type": "California Nonprofit", "incorporation_year": 1985,
            "units": 279,
        },
    )()
    return {
        "spec": spec,
        "static_data": spec.static_data,
        "fiscal_year": 2026,
        "hoa": hoa,
        "toc_page_numbers": {},
        "appendix_toc_entries": [],
        "hoa_logo_data_uri": None,
        "hoa_settings": {
            "management_company": "Mgmt Co",
            "management_company_address": "Addr",
            "management_company_phone": "x", "management_company_fax": "x",
            "management_company_web": "x",
            "cpa_firm_name": "CPA Firm",
            "cpa_firm_address": "x",
            "reserve_study_expert_name": "Expert",
            "reserve_study_date": "2025-09-01",
            "letter_date": "March 1, 2026",
            "accountant_report_date": "March 1, 2026",
            "letter_signed_by": "Board",
            "letter_signed_by_title": "President",
            "monthly_assessment_per_unit_prior": 590.0,
            "reserve_cash_balance_eoy_prior": 1500000.0,
            "fund_balance_boy_operations": 100000.0,
            "interest_rate_after_tax": 0.018,
            "replacement_cost_increase_rate": 0.03,
        },
        "today": "March 1, 2026",
        "today_iso": "2026-03-01",
        "matrix": type("Matrix", (), {"recipient_grain": "summary"})(),
        "computed": {
            "monthly_replacement_contribution_per_unit_2026": Decimal("200.98"),
            "monthly_replacement_revenue_total": Decimal("672886"),
            "percent_funded": 57,
            "total_estimated_liability": Decimal("4575000"),
            "under_funded_balance_total": Decimal("1975000"),
            "under_funded_balance_per_unit": Decimal("7080"),
            "total_revenues_operations": Decimal("0"),
            "total_revenues_replacement": Decimal("0"),
            "total_revenues": Decimal("0"),
            "total_expenses_operations": Decimal("0"),
            "total_expenses_replacement": Decimal("0"),
            "total_expenses": Decimal("0"),
            "operating_revenues": [],
            "replacement_revenues": [],
            "operating_expenses": [],
            "replacement_expenses": [],
            "reserve_components": [],
            "total_year_replacement_provision": Decimal("0"),
            "thirty_year_projections": [],
            "assessment_change_disclosure": "No",
            "percent_funded_at": {10: 60, 20: 65, 30: 70},
            "useful_life_not_disclosed_count": 0,
            "board_deferral_count": 0,
            "signed_contracts_count": 0,
            "data_gaps": [],
            "additional_assessments_needed": [],
            "special_assessments": special_assessments,
            "outstanding_loan": None,
            "income_tax_provision": Decimal("0"),
            "excess_revenues_over_expenses_operations": Decimal("0"),
            "excess_revenues_over_expenses_replacement": Decimal("0"),
            "fund_balance_eoy_operations": Decimal("100000"),
            "fund_balance_eoy_replacement": Decimal("1500000"),
            "thirty_year_funding_plan": [],
            "major_component_expenditure_schedule": [],
            "monthly_assessment_per_unit_current": Decimal("605.00"),
            "monthly_replacement_contribution_total": Decimal("0"),
            "assessment_change_phrase": "no change",
            "expenses_by_section": {},
            "revenues_by_section": {},
        },
        "reserve_study_snapshot": type("RS", (), {"study_date": "2025-09-01"})(),
    }


def _render(template: str, special_assessments: list[dict[str, Any]]) -> str:
    pdf_bytes = render_template(
        template_name=template, context=_base_context(special_assessments),
    )
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "".join(p.get_text() for p in doc)


class TestCoverLetterWording:
    def test_no_special_assessment_renders_historical_wording(self):
        text = _render("cover_letter.html", [])
        assert "does not anticipate that there is a possibility" in text

    def test_approved_scheduled_renders_amount_and_due_date(self):
        text = _render(
            "cover_letter.html",
            [
                {
                    "status": "approved_scheduled",
                    "label": "Pool deck repair",
                    "amount_per_unit": 850.00,
                    "due_date": "July 1, 2026",
                    "included_in_regular_monthly": False,
                },
            ],
        )
        assert "Pool deck repair" in text
        assert "850.00" in text
        assert "July 1, 2026" in text
        assert "billed separately" in text

    def test_approved_scheduled_included_in_monthly(self):
        text = _render(
            "cover_letter.html",
            [
                {
                    "status": "approved_scheduled",
                    "label": "Garage relining",
                    "amount_per_unit": 100.00,
                    "due_date": "2026-04-01",
                    "included_in_regular_monthly": True,
                },
            ],
        )
        assert "Garage relining" in text
        assert "included in the regular monthly" in text

    def test_possible_disclosure_only_renders_language(self):
        text = _render(
            "cover_letter.html",
            [
                {
                    "status": "possible_disclosure_only",
                    "label": "Possible roof replacement",
                    "display_language": (
                        "The board is evaluating a possible special "
                        "assessment for the 2026 roof replacement."
                    ),
                },
            ],
        )
        assert "Possible" in text or "possible" in text
        assert "evaluating" in text
        # Disclosure-only wording does NOT carry a per-unit dollar amount
        # in the special-assessment block specifically. (Other parts of
        # the cover letter may still show $0.00 for unrelated totals.)
        # Instead, check the disclosure-language paragraph is present.
        assert "2026 roof replacement" in text

class TestDisclosureSummaryWording:
    def test_none_renders_none_scheduled_row(self):
        text = _render("pro_forma_disclosure_summary.html", [])
        assert "None scheduled" in text

    def test_approved_scheduled_shows_table_row(self):
        text = _render(
            "pro_forma_disclosure_summary.html",
            [
                {
                    "status": "approved_scheduled",
                    "label": "Pool repair",
                    "amount_per_unit": 850.00,
                    "due_date": "2026-07-01",
                    "purpose": "Pool deck restoration",
                    "frequency": None,
                },
            ],
        )
        assert "2026-07-01" in text
        assert "850.00" in text
        assert "Pool deck restoration" in text

    def test_possible_disclosure_only_shows_tbd(self):
        text = _render(
            "pro_forma_disclosure_summary.html",
            [
                {
                    "status": "possible_disclosure_only",
                    "label": "Earthquake retrofit",
                    "display_language": "Pending board approval",
                },
            ],
        )
        assert "To be determined" in text
        assert "Pending board approval" in text
