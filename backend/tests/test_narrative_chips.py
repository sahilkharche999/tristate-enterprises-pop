"""add-full-document-editor: value chips and block chips.

Covers the spec requirement "Value chips and block chips resolve at compile":
escaping, optional clauses that resolve empty, each block chip's data-driven
variants, and the anti-SSTI guarantee that operator content never reaches
template evaluation.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import boilerplate_variables as bv


# ── fixtures ────────────────────────────────────────────────────────────────


def _hoa(**over):
    base = dict(
        name="Old Mill",
        city="Los Altos",
        state="CA",
        units=48,
        entity_type="non-profit mutual benefit corporation",
        incorporation_year=1979,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _computed(**over):
    base = {
        "monthly_assessment_per_unit_current": Decimal("425.00"),
        "monthly_replacement_contribution_total": Decimal("8100.50"),
        "monthly_replacement_contribution_per_unit_2026": Decimal("168.76"),
        "reserve_funding_source_label": "the adopted reserve study provision",
        "income_tax_provision": Decimal("312"),
        "special_assessments": [],
        "outstanding_loan": None,
        "presentation_facts": {
            "assessments_vary": False,
            "assessment_change_phrase": "increases to",
        },
        "packet_archetype_facts": {"archetype": "dual-fund"},
        "reserve_funding_facts": {},
        "reserve_liability_facts": {
            "cash_reserve_balance_eoy_prior": Decimal("410000"),
            "total_estimated_liability": Decimal("980000"),
            "under_funded_balance_total": Decimal("570000"),
            "under_funded_balance_per_unit": Decimal("11875"),
            "percent_funded": "41.8",
        },
    }
    base.update(over)
    return base


def _settings(**over):
    base = {
        "management_company": "Tri-State Enterprises",
        "management_company_address": "123 Main St",
        "cpa_firm_name": "Bergstrom & Co LLP",
        "cpa_firm_address": "9 Ledger Way\nSuite 4",
        "reserve_study_expert_name": "Reserve Analysts Inc.",
        "reserve_funding_plan_date": "March 3, 2026",
        "accountant_report_date": "February 2, 2026",
        "letter_signed_by": "Board of Directors",
        "replacement_cost_increase_rate": 0.03,
        "interest_rate_after_tax": 0.018,
    }
    base.update(over)
    return base


def _var_map(**kwargs):
    params = dict(
        hoa=_hoa(),
        fiscal_year=2026,
        hoa_settings=_settings(),
        computed=_computed(),
        today="Friday July 25, 2026",
        reserve_study_snapshot=SimpleNamespace(study_date="January 15, 2026"),
    )
    params.update(kwargs)
    return bv.build_var_map(**params)


def _block_map(**kwargs):
    params = dict(fiscal_year=2026, computed=_computed())
    params.update(kwargs)
    return bv.build_block_map(**params)


# ── value chips ─────────────────────────────────────────────────────────────


def test_value_chip_is_escaped():
    var_map = _var_map(hoa=_hoa(name="Smith & Jones HOA"))
    out = bv.resolve('<p><span data-var="hoa_name"></span></p>', var_map)
    assert out == "<p>Smith &amp; Jones HOA</p>"


def test_value_chip_cannot_inject_markup():
    var_map = _var_map(hoa=_hoa(name="<script>alert(1)</script>"))
    out = bv.resolve('<p><span data-var="hoa_name"></span></p>', var_map)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_money_and_percent_chips_match_template_formatting():
    var_map = _var_map()
    assert var_map["reserve_monthly_contribution"] == "$8,100.50"
    assert var_map["cash_reserve_balance"] == "$410,000"
    assert var_map["total_estimated_liability"] == "$980,000"
    assert var_map["under_funded_balance"] == "$570,000"
    assert var_map["percent_funded"] == "41.8%"
    assert var_map["replacement_cost_increase_rate"] == "3%"
    assert var_map["interest_rate_after_tax"] == "1.8%"
    assert var_map["income_tax_provision"] == "$312"


def test_unit_word_agrees_with_count():
    assert _var_map(hoa=_hoa(units=1))["hoa_units_word"] == "unit"
    assert _var_map(hoa=_hoa(units=48))["hoa_units_word"] == "units"


def test_cpa_name_variants():
    var_map = _var_map()
    assert var_map["cpa_firm_name"] == "Bergstrom & Co LLP"
    assert var_map["cpa_firm_name_short"] == "Bergstrom & Co"
    assert var_map["cpa_firm_name_upper"] == "BERGSTROM & CO LLP"


# ── optional clauses resolve empty ──────────────────────────────────────────


def test_incorporation_clause_present_and_absent():
    assert _var_map()["incorporation_clause"] == ", created in 1979"
    assert _var_map(hoa=_hoa(incorporation_year=None))["incorporation_clause"] == ""


def test_optional_clause_resolves_to_empty_string_in_a_sentence():
    var_map = _var_map(hoa=_hoa(incorporation_year=None))
    out = bv.resolve(
        '<p>organized under California law<span data-var="incorporation_clause">'
        "</span>, and is responsible for maintenance.</p>",
        var_map,
    )
    assert out == (
        "<p>organized under California law, and is responsible for maintenance.</p>"
    )


def test_reserve_study_clauses_present_and_absent():
    var_map = _var_map()
    assert var_map["reserve_study_date_clause"] == " dated January 15, 2026"
    assert var_map["reserve_funding_plan_clause"] == (
        ", with a funding plan dated March 3, 2026"
    )

    bare = _var_map(
        reserve_study_snapshot=SimpleNamespace(study_date=None),
        hoa_settings=_settings(reserve_funding_plan_date=None, reserve_study_date=None),
    )
    assert bare["reserve_study_date_clause"] == ""
    assert bare["reserve_funding_plan_clause"] == ""


def test_assessment_line_varies_by_recipient_grain():
    flat = _var_map()
    assert "The monthly assessment per unit for 2026 increases to $425.00." == (
        flat["assessment_line"]
    )

    varied = _var_map(matrix=SimpleNamespace(recipient_grain="unit"))
    assert "vary by ownership interest" in varied["assessment_line"]


# ── block chips ─────────────────────────────────────────────────────────────


def test_special_assessment_none_wording():
    html = _block_map()["special_assessment_disclosure"]
    assert "does not anticipate" in html
    assert "2026 calendar year" in html


def test_special_assessment_approved_scheduled_wording():
    computed = _computed(
        special_assessments=[
            {
                "status": "approved_scheduled",
                "label": "Roof replacement",
                "total_amount": Decimal("120000"),
                "due_date": "June 1, 2026",
                "included_in_regular_monthly": False,
            }
        ]
    )
    html = _block_map(computed=computed)["special_assessment_disclosure"]
    assert "has been approved and scheduled" in html
    assert "Roof replacement" in html
    assert "$120,000.00 total, allocated equally across units" in html
    assert "due June 1, 2026" in html
    assert "billed separately" in html


def test_special_assessment_variable_allocation_points_at_schedule():
    computed = _computed(
        special_assessments=[
            {
                "status": "approved_scheduled",
                "label": "Seismic retrofit",
                "total_amount": Decimal("500000"),
                "is_variable_allocation": True,
            }
        ]
    )
    html = _block_map(computed=computed)["special_assessment_disclosure"]
    assert "allocated per the assessment schedule" in html


def test_special_assessment_disclosure_only_wording():
    computed = _computed(
        special_assessments=[
            {"status": "possible_disclosure_only", "label": "Possible elevator work"}
        ]
    )
    html = _block_map(computed=computed)["special_assessment_disclosure"]
    assert "discloses the following possible special assessment" in html
    assert "formal Board approval and notice required" in html


def test_special_assessment_label_is_escaped():
    computed = _computed(
        special_assessments=[
            {
                "status": "approved_scheduled",
                "label": "<img src=x onerror=alert(1)>",
                "total_amount": Decimal("1"),
            }
        ]
    )
    html = _block_map(computed=computed)["special_assessment_disclosure"]
    assert "<img" not in html
    assert "&lt;img" in html


def test_outstanding_loan_absent_wording():
    html = _block_map()["outstanding_loan_note"]
    assert "no outstanding current or projected loan balance" in html
    assert "December 31, 2025" in html


def test_outstanding_loan_present_wording():
    computed = _computed(
        outstanding_loan={
            "balance": "250000",
            "lender": "First Federal",
            "original_amount": "400000",
            "interest_rate": 0.0525,
            "payoff_date": "December 2031",
            "purpose": "Roof replacement",
        }
    )
    html = _block_map(computed=computed)["outstanding_loan_note"]
    assert "$250,000.00" in html
    assert "with First Federal" in html
    assert "$400,000.00" in html
    assert "5.25%" in html
    assert "December 2031" in html
    assert "&sect; 5505" in html


def test_assessment_basis_sentence_both_branches():
    """A value chip, not a block chip: it sits mid-sentence inside a <p>,
    where a block carrier would be invalid markup."""
    flat = _var_map()["assessment_basis_sentence"]
    assert "The current monthly assessment per unit is increases to $425.00." == flat

    varied = _var_map(matrix=SimpleNamespace(recipient_grain="group"))[
        "assessment_basis_sentence"
    ]
    assert "vary by ownership interest" in varied


def test_contribution_increase_schedule_row_per_step():
    static_data = SimpleNamespace(
        assessment_increase_schedule=[(2026, 2030, 0.05), (2031, 2040, 0.03)]
    )
    html = _block_map(static_data=static_data)["contribution_increase_schedule"]
    assert html.count("<tr>") == 3  # header + two data rows
    assert "2026 &ndash; 2030" in html
    assert "5.0%" in html
    assert "3.0%" in html


def test_contribution_increase_schedule_empty_still_renders_a_table():
    html = _block_map(static_data=SimpleNamespace(assessment_increase_schedule=[]))[
        "contribution_increase_schedule"
    ]
    assert "<table" in html
    assert "Annual Contribution Increase" in html


def test_reserve_only_chips_empty_for_dual_fund():
    blocks = _block_map()
    assert blocks["reserve_only_note"] == ""
    assert blocks["reserve_only_assumption"] == ""


def test_reserve_only_chips_populated_for_reserve_only_packet():
    computed = _computed(packet_archetype_facts={"archetype": "reserve-only"})
    blocks = _block_map(computed=computed)
    assert blocks["reserve_only_note"].startswith("<p>")
    assert blocks["reserve_only_assumption"].startswith("<li>")


def test_significant_assumptions_variance_both_branches():
    flat = _block_map()["significant_assumptions_variance"]
    assert "$425.00 per unit per month" in flat

    varied = _block_map(matrix=SimpleNamespace(recipient_grain="unit"))[
        "significant_assumptions_variance"
    ]
    assert "vary by ownership interest" in varied


def test_appendix_toc_rows():
    html = _block_map(
        appendix_toc_entries=[
            {"title": "Insurance Certificate", "page": 41},
            {"title": "Collection Policy & Rules", "page": 44},
        ]
    )["appendix_toc_rows"]
    assert html.count("<li>") == 2
    assert "Insurance Certificate" in html
    assert "Collection Policy &amp; Rules" in html  # escaped
    assert ">41<" in html


def test_toc_page_chips_resolve_from_page_numbers():
    var_map = _var_map(toc_page_numbers={"note_7.html": 23})
    assert var_map["page_note_7"] == "23"
    # Pass 1 renders before page numbers exist; an em-dash placeholder keeps
    # the row's width stable so pass-1 pagination matches pass 2.
    assert var_map["page_note_8"] == "—"


# ── carriers, resolution, and the trust boundary ────────────────────────────


def test_block_chip_carried_by_li_is_replaced_wholesale():
    block_map = _block_map()
    out = bv.resolve(
        '<ol><li>4950(b): minutes.</li>'
        '<li data-block="special_assessment_disclosure"></li></ol>',
        _var_map(),
        block_map,
    )
    assert "<li>5300:" in out
    assert "data-block" not in out


def test_empty_block_chip_removes_its_carrier_entirely():
    out = bv.resolve(
        '<ul><li>Assumption one</li>'
        '<li data-block="reserve_only_assumption"></li></ul>',
        _var_map(),
        _block_map(),
    )
    assert out == "<ul><li>Assumption one</li></ul>"


def test_block_chip_carried_by_div():
    out = bv.resolve(
        '<div data-block="outstanding_loan_note"></div>', _var_map(), _block_map()
    )
    assert out.startswith("<p>There is no outstanding")


def test_chips_inside_table_cells_resolve():
    out = bv.resolve(
        "<table><tbody><tr><td>Percent funded</td>"
        '<td><span data-var="percent_funded"></span></td></tr></tbody></table>',
        _var_map(),
    )
    assert "<td>41.8%</td>" in out


def test_operator_edits_around_a_chip_do_not_change_how_it_resolves():
    """Spec: 'Operator restructures a table, numbers stay live'."""
    edited = (
        "<table><tbody>"
        "<tr><td>Reserve cash on hand (renamed by operator)</td>"
        '<td><span data-var="cash_reserve_balance"></span></td></tr>'
        "<tr><td>Operator-added row</td><td>see note</td></tr>"
        "<tr><td>Percent funded</td>"
        '<td><span data-var="percent_funded"></span></td></tr>'
        "</tbody></table>"
    )
    out = bv.resolve(edited, _var_map())
    assert "Reserve cash on hand (renamed by operator)" in out
    assert "Operator-added row" in out
    assert "$410,000" in out
    assert "41.8%" in out


def test_deleted_chip_leaves_static_text():
    """Spec: an operator may type a literal over a chip; it just stops being live."""
    out = bv.resolve("<table><tbody><tr><td>$999,999</td></tr></tbody></table>", _var_map())
    assert out == "<table><tbody><tr><td>$999,999</td></tr></tbody></table>"


def test_jinja_expression_in_a_chip_name_is_inert():
    html = '<p><span data-var="{{ 7*7 }}"></span></p>'
    assert bv.find_unknown_tokens(html) == ["{{ 7*7 }}"]
    with pytest.raises(bv.UnresolvedBoilerplateToken):
        bv.resolve(html, _var_map())


def test_unknown_block_name_reported_and_refused():
    html = '<div data-block="pay_me_bitcoin"></div>'
    assert bv.find_unknown_tokens(html) == ["pay_me_bitcoin"]
    with pytest.raises(bv.UnresolvedBoilerplateToken):
        bv.resolve(html, _var_map(), _block_map())


def test_find_unknown_tokens_accepts_every_catalogued_chip():
    html = "".join(f'<span data-var="{name}"></span>' for name in bv.TOKEN_CATALOG)
    html += "".join(f'<div data-block="{name}"></div>' for name in bv.BLOCK_CATALOG)
    assert bv.find_unknown_tokens(html) == []


def test_every_catalogued_chip_has_a_resolved_value():
    """A catalog entry with no builder would raise at compile, not at save."""
    var_map = _var_map()
    missing = sorted(set(bv.TOKEN_CATALOG) - set(var_map))
    assert missing == []

    block_map = _block_map()
    missing_blocks = sorted(set(bv.BLOCK_CATALOG) - set(block_map))
    assert missing_blocks == []
