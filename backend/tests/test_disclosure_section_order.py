"""Firm-level disclosure packet section order + TOC rows + notes packing."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.disclosure_package.package_specs import STANDARD_PACKAGE_SPEC
from app.disclosure_package.schemas import GeneratedPage, StaticAppendix
from app.disclosure_package import section_order as so
from app.services import boilerplate_variables as bv
from app.services import narrative_content as nc


def test_app_settings_exposes_catalog_and_preserves_inflation(client):
    first = client.put("/app-settings", json={"global_reserve_inflation_rate": 0.04})
    assert first.status_code == 200, first.text
    second = client.put(
        "/app-settings",
        json={"disclosure_section_order": ["cover_letter.html"]},
    )
    assert second.status_code == 200, second.text
    body = client.get("/app-settings").json()
    assert body["global_reserve_inflation_rate"] == 0.04
    assert body["disclosure_section_order"][0] == "cover_letter.html"
    templates = [row["template"] for row in body["section_catalog"]]
    toc = templates.index("annual_budget_report_toc.html")
    assert templates[toc + 1] == "assessment_schedule/universal.html"


def test_default_order_places_assessment_immediately_after_toc():
    templates = so.resolve_generated_templates()
    toc = templates.index("annual_budget_report_toc.html")
    assert templates[toc + 1] == "assessment_schedule/universal.html"
    assert templates[toc + 2] == "pro_forma_disclosure_summary.html"


def test_default_order_packs_notes_into_one_template():
    templates = so.resolve_generated_templates()
    assert "notes_packed.html" in templates
    for legacy in (
        "notes_1_to_3.html",
        "note_4_5.html",
        "note_6_funding_plan.html",
        "note_7.html",
        "note_8.html",
    ):
        assert legacy not in templates


def test_hidden_optional_is_omitted():
    templates = so.resolve_generated_templates(
        hidden=["forecasted_statement_title.html"]
    )
    assert "forecasted_statement_title.html" not in templates
    assert "cover_letter.html" in templates


def test_required_cannot_hide():
    templates = so.resolve_generated_templates(
        hidden=["assessment_schedule/universal.html", "notes_packed.html"]
    )
    assert "assessment_schedule/universal.html" in templates
    assert "notes_packed.html" in templates


def test_unknown_saved_keys_are_dropped():
    templates = so.resolve_generated_templates(
        saved_order=["cover_letter.html", "not_a_real_page.html", "annual_budget_report_toc.html"]
    )
    assert "not_a_real_page.html" not in templates
    assert "cover_letter.html" in templates


def test_new_catalog_keys_are_appended():
    templates = so.resolve_generated_templates(
        saved_order=["cover_letter.html", "annual_budget_report_cover.html"]
    )
    assert templates[0] == "cover_letter.html"
    assert templates[1] == "annual_budget_report_cover.html"
    for key in so.DEFAULT_SECTION_ORDER:
        assert key in templates


def test_legacy_note_templates_collapse_to_packed():
    templates = so.resolve_generated_templates(
        saved_order=[
            "cover_letter.html",
            "notes_1_to_3.html",
            "note_7.html",
            "pro_forma_disclosure_summary.html",
        ]
    )
    assert templates.count("notes_packed.html") == 1
    assert templates.index("notes_packed.html") == 1


def test_apply_to_spec_keeps_static_appendices_at_the_end():
    spec = so.apply_to_spec(
        STANDARD_PACKAGE_SPEC,
        saved_order=["cover_letter.html"],
        hidden=["compilation_report.html"],
    )
    generated = [e.template for e in spec.entries if isinstance(e, GeneratedPage)]
    static = [e for e in spec.entries if isinstance(e, StaticAppendix)]
    assert generated[0] == "cover_letter.html"
    assert "compilation_report.html" not in generated
    assert static
    assert all(isinstance(e, StaticAppendix) for e in spec.entries[-len(static) :])


def test_standard_spec_uses_catalog_default():
    generated = [
        e.template
        for e in STANDARD_PACKAGE_SPEC.entries
        if isinstance(e, GeneratedPage)
    ]
    assert generated == so.DEFAULT_SECTION_ORDER


def test_computed_placeholders_put_assessment_before_5570():
    labels_after_toc = [
        p["template"]
        for p in nc.COMPUTED_PLACEHOLDERS
        if p["after"] == "budget_toc"
    ]
    assert labels_after_toc[0] == "assessment_schedule/universal.html"
    assert labels_after_toc[1] == "pro_forma_disclosure_summary.html"


def test_package_toc_rows_list_assessment_before_5570():
    html = str(
        bv.build_block_map(
            fiscal_year=2026,
            computed={},
            toc_page_numbers={
                "assessment_schedule/universal.html": 4,
                "pro_forma_disclosure_summary.html": 6,
                "notes_packed.html": 12,
            },
        )["package_toc_rows"]
    )
    assess_at = html.index("Assessment Schedule")
    form_at = html.index("Pro Forma Operating Budget")
    assert assess_at < form_at
    assert ">4<" in html
    assert ">6<" in html
    assert html.count("<li>") >= 8
    assert "Note 1 — The Association" in html
    assert html.count(">12<") >= 8
    assert "<p>" not in html


def test_empty_5570_due_date_fills_from_letter_date():
    from app.disclosure_package.compiler import _fill_empty_assessment_due_dates

    filled = _fill_empty_assessment_due_dates(
        [{"due_date": "", "amount_per_unit": 50}],
        {"letter_date": "July 1, 2026"},
    )
    assert filled[0]["due_date"] == "July 1, 2026"
    unchanged = _fill_empty_assessment_due_dates(
        [{"due_date": "03/01/2027", "amount_per_unit": 50}],
        {"letter_date": "July 1, 2026"},
    )
    assert unchanged[0]["due_date"] == "03/01/2027"


def test_package_toc_rows_follow_explicit_package_templates():
    html = str(
        bv.build_block_map(
            fiscal_year=2026,
            computed={},
            package_templates=[
                "cover_letter.html",
                "notes_packed.html",
                "assessment_schedule/universal.html",
            ],
            toc_page_numbers={
                "notes_packed.html": 3,
                "assessment_schedule/universal.html": 9,
            },
        )["package_toc_rows"]
    )
    note_at = html.index("Note 1 — The Association")
    assess_at = html.index("Assessment Schedule")
    assert note_at < assess_at
    assert "Pro Forma Operating Budget" not in html


def test_documents_for_api_follows_saved_firm_order(session):
    from app.ai_implementation.db.models import AppSetting
    from app.disclosure_package.section_order import ORDER_SETTING_KEY

    session.add(
        AppSetting(
            key=ORDER_SETTING_KEY,
            value_text='["notes_packed.html","cover_letter.html"]',
        )
    )
    session.flush()

    rows = nc.documents_for_api(session, None)
    ids = [row["id"] for row in rows]
    assert ids.index("note_1_3") < ids.index("cover_letter")
    assert ids.index("assessment_schedule/universal.html") > ids.index("cover_letter")


def test_note_page_chips_all_point_at_packed_template():
    var_map = bv.build_var_map(
        hoa=SimpleNamespace(
            name="X", city="Y", state="CA", units=2,
            entity_type=None, incorporation_year=None,
            fiscal_year_end_month=12,
        ),
        fiscal_year=2026,
        hoa_settings={},
        computed={},
        toc_page_numbers={"notes_packed.html": 18},
    )
    for token in (
        "page_notes_1_to_3",
        "page_note_4_5",
        "page_note_6",
        "page_note_7",
        "page_note_8",
    ):
        assert var_map[token] == "18"
        assert bv.TOC_PAGE_TOKENS[token] == "notes_packed.html"
