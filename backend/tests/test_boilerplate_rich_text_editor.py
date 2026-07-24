"""add-boilerplate-rich-text-editor: sanitize / variable-token / rendering scenarios.

Registry mechanics, save/load, legacy-alias, and cover-letter override
plumbing are covered by test_hoa_boilerplate.py. This file covers the
scenarios specific to the rich-text-editor spec
(specs/boilerplate-rich-text-editor/spec.md): allowlist sanitization edge
cases, the variable-token resolver (including the anti-SSTI guarantee),
preflight's unknown-token gate, and that formatting actually reaches
rendered output unescaped.
"""
from __future__ import annotations

import pytest

from app.disclosure_package.preflight import check_boilerplate_tokens
from app.disclosure_package.render import _build_env
from app.services import boilerplate_sanitize as sanitize
from app.services import boilerplate_variables as bv
from app.services import hoa_boilerplate as bp
from app.services.hoa_boilerplate import UnknownBoilerplateToken


# ── sanitization edge cases (8.x) ───────────────────────────────────────────


def test_allowed_formatting_survives_sanitize():
    out = sanitize.sanitize_slot_html(
        "<p><strong>Dear</strong> Homeowner</p><ul><li>one</li></ul>"
    )
    assert out == "<p><strong>Dear</strong> Homeowner</p><ul><li>one</li></ul>"


def test_script_tag_stripped_text_preserved():
    out = sanitize.sanitize_slot_html("<p>hi<script>alert(1)</script></p>")
    assert "<script>" not in out
    assert "alert(1)" not in out
    assert out == "<p>hi</p>"


def test_onclick_and_style_attributes_stripped():
    out = sanitize.sanitize_slot_html('<p onclick="x" style="color:red">t</p>')
    assert "onclick" not in out
    assert "style" not in out
    assert out == "<p>t</p>"


def test_link_and_image_markup_stripped():
    href_out = sanitize.sanitize_slot_html('<a href="http://evil.com">click</a>')
    assert "<a" not in href_out
    assert "href" not in href_out
    assert "click" in href_out  # text content preserved

    img_out = sanitize.sanitize_slot_html('<img src="http://evil.com/x.png">')
    assert "<img" not in img_out
    assert "src" not in img_out
    assert "evil.com" not in img_out


def test_legacy_plain_text_passes_through_byte_for_byte():
    """No tags at all → not run through nh3 (avoids corrupting/re-escaping
    a bare & or < on every subsequent save)."""
    text = 'Thank you — café & "special" 日本語'
    assert sanitize.sanitize_slot_html(text) == text


def test_indent_class_survives_sanitize():
    out = sanitize.sanitize_slot_html('<p class="indent-2">Indented</p>')
    assert 'class="indent-2"' in out


# ── variable-token resolution (9.x) ─────────────────────────────────────────


class _FakeHoa:
    name = "Two Worlds & Co"


def _computed(assessments_vary: bool):
    return {
        "presentation_facts": {
            "assessments_vary": assessments_vary,
            "assessment_change_phrase": "remains",
        },
        "assessment_change_phrase": "remains",
        "monthly_assessment_per_unit_current": "350.50",
        "monthly_replacement_contribution_total": "1200",
    }


def test_known_token_resolves_to_hoa_name():
    var_map = bv.build_var_map(
        hoa=_FakeHoa(), fiscal_year=2026, hoa_settings={}, computed=_computed(False)
    )
    resolved = bv.resolve('<p><span data-var="hoa_name"></span></p>', var_map)
    assert "Two Worlds &amp; Co" in resolved


def test_assessment_line_varies_by_recipient_grain():
    class _GroupMatrix:
        recipient_grain = "group"

    var_map = bv.build_var_map(
        hoa=_FakeHoa(),
        fiscal_year=2026,
        hoa_settings={},
        computed=_computed(False),
        matrix=_GroupMatrix(),
    )
    assert "vary by ownership interest" in var_map["assessment_line"]


def test_assessment_line_single_figure_when_not_varying():
    var_map = bv.build_var_map(
        hoa=_FakeHoa(), fiscal_year=2026, hoa_settings={}, computed=_computed(False)
    )
    assert "$350.50" in var_map["assessment_line"]
    assert "vary" not in var_map["assessment_line"]


def test_resolved_value_is_html_escaped():
    var_map = {"hoa_name": "<b>Evil</b> & Co"}
    resolved = bv.resolve('<span data-var="hoa_name"></span>', var_map)
    assert "<b>" not in resolved
    assert "&lt;b&gt;Evil&lt;/b&gt; &amp; Co" in resolved


def test_unknown_token_name_never_evaluated_as_template():
    """SSTI guard: a token whose name looks like a Jinja expression must
    never be evaluated — it is routed to the unknown-token path instead."""
    html = '<span data-var="{{ 7*7 }}"></span>'
    assert bv.find_unknown_tokens(html) == ["{{ 7*7 }}"]
    with pytest.raises(bv.UnresolvedBoilerplateToken):
        bv.resolve(html, {"hoa_name": "x"})


def test_find_unknown_tokens_ignores_known_names():
    html = '<span data-var="hoa_name"></span><span data-var="typo_name"></span>'
    assert bv.find_unknown_tokens(html) == ["typo_name"]


# ── preflight gate (9.5) ─────────────────────────────────────────────────────


def test_check_boilerplate_tokens_blocks_unknown_token():
    errors = check_boilerplate_tokens(
        {"cover_letter_intro": '<span data-var="hao_name"></span>'}
    )
    assert len(errors) == 1
    assert errors[0].severity == "blocking"
    assert "hao_name" in errors[0].message
    assert errors[0].field_path == "boilerplate.cover_letter_intro"


def test_check_boilerplate_tokens_passes_known_tokens():
    errors = check_boilerplate_tokens(
        {"cover_letter_intro": '<span data-var="hoa_name"></span>'}
    )
    assert errors == []


def test_check_boilerplate_tokens_handles_empty_and_none():
    assert check_boilerplate_tokens(None) == []
    assert check_boilerplate_tokens({}) == []
    assert check_boilerplate_tokens({"cover_letter_intro": None}) == []


# ── PUT endpoint rejects unknown token (9.6) ────────────────────────────────


def test_put_boilerplate_unknown_token_returns_400(client, db_session):
    from app.ai_implementation.db.models import Property

    prop = Property(name="BP Token HOA", units=3, hoa_code="BPTOK")
    db_session.add(prop)
    db_session.commit()

    r = client.put(
        f"/hoa/{prop.id}/settings/boilerplate",
        json={"overrides": {"cover_letter_intro": '<span data-var="hao_name"></span>'}},
    )
    assert r.status_code == 400
    assert "hao_name" in r.json()["detail"]

    # Nothing was persisted.
    got = client.get(f"/hoa/{prop.id}/settings/boilerplate")
    assert got.json()["slots"][0]["is_override"] is False


def test_put_boilerplate_known_token_accepted(client, db_session):
    from app.ai_implementation.db.models import Property

    prop = Property(name="BP Token HOA 2", units=3, hoa_code="BPTOK2")
    db_session.add(prop)
    db_session.commit()

    r = client.put(
        f"/hoa/{prop.id}/settings/boilerplate",
        json={"overrides": {"cover_letter_intro": '<p>Dear <span data-var="hoa_name"></span></p>'}},
    )
    assert r.status_code == 200, r.text
    assert 'data-var="hoa_name"' in r.json()["slots"][0]["value"]


def test_get_boilerplate_settings_returns_variable_catalog(client, db_session):
    from app.ai_implementation.db.models import Property

    prop = Property(name="BP Catalog HOA", units=2, hoa_code="BPCAT")
    db_session.add(prop)
    db_session.commit()

    got = client.get(f"/hoa/{prop.id}/settings/boilerplate")
    assert got.status_code == 200
    variable_ids = {v["id"] for v in got.json()["variables"]}
    assert "hoa_name" in variable_ids
    assert "assessment_line" in variable_ids


# ── rendered formatting is unescaped (10.4) ─────────────────────────────────


def test_formatting_appears_unescaped_in_rendered_html():
    env = _build_env("standard")
    template = env.get_template("cover_letter.html")

    class _Hoa:
        name = "Test HOA"

    class _Matrix:
        recipient_grain = "unit"

    sanitized_intro = bp.merge_overrides(
        None,
        {"cover_letter_intro": "<p><strong>Bold intro</strong></p><ul><li>Item one</li></ul>"},
    )["cover_letter_intro"]

    ctx = {
        "hoa": _Hoa(),
        "fiscal_year": 2026,
        "today": "Monday January 1, 2026",
        "hoa_settings": {
            "letter_date": "01/01/2026",
            "cpa_firm_name": "Test CPA LLP",
            "letter_signed_by": "Board",
            "letter_signed_by_title": None,
            "management_company": "Mgmt",
            "management_company_address": "1 Main",
            "management_company_phone": None,
            "management_company_fax": None,
            "management_company_web": None,
        },
        "hoa_logo_data_uri": None,
        "matrix": _Matrix(),
        "computed": {
            "presentation_facts": None,
            "assessment_change_phrase": "will be",
            "monthly_assessment_per_unit_current": 100.0,
            "monthly_replacement_contribution_total": 50.0,
            "special_assessments": [],
        },
        "boilerplate": {
            "cover_letter_intro": sanitized_intro,
            "enclosed_documents_list": None,
            "cover_letter_closing": None,
        },
        "toc_page_numbers": {},
        "appendix_toc_entries": [],
    }
    html = template.render(**ctx)
    assert "<strong>Bold intro</strong>" in html
    assert "<li>Item one</li>" in html
    assert "&lt;strong&gt;" not in html


# ── legal/statutory blocks are never operator-editable (10.5) ──────────────


def test_special_assessment_and_civil_code_blocks_not_in_slot_registry():
    forbidden_substrings = ("5300", "civil_code", "special_assessment")
    for slot_id in bp.SLOT_REGISTRY:
        lowered = slot_id.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, (
                f"slot {slot_id!r} must not target the statutory/§5300 blocks"
            )


def test_civil_code_and_5300_language_always_present_regardless_of_overrides():
    """Even with all 3 slots overridden, the template-owned legal blocks
    (§5300 machine, civil-code citation list) still render verbatim."""
    env = _build_env("standard")
    template = env.get_template("cover_letter.html")

    class _Hoa:
        name = "Test HOA"

    class _Matrix:
        recipient_grain = "unit"

    ctx = {
        "hoa": _Hoa(),
        "fiscal_year": 2026,
        "today": "Monday January 1, 2026",
        "hoa_settings": {
            "letter_date": "01/01/2026",
            "cpa_firm_name": "Test CPA LLP",
            "letter_signed_by": "Board",
            "letter_signed_by_title": None,
            "management_company": "Mgmt",
            "management_company_address": "1 Main",
            "management_company_phone": None,
            "management_company_fax": None,
            "management_company_web": None,
        },
        "hoa_logo_data_uri": None,
        "matrix": _Matrix(),
        "computed": {
            "presentation_facts": None,
            "assessment_change_phrase": "will be",
            "monthly_assessment_per_unit_current": 100.0,
            "monthly_replacement_contribution_total": 50.0,
            "special_assessments": [],
        },
        "boilerplate": {
            "cover_letter_intro": "Custom intro override",
            "enclosed_documents_list": "<ol><li>Custom doc</li></ol>",
            "cover_letter_closing": "Custom closing override",
        },
        "toc_page_numbers": {},
        "appendix_toc_entries": [],
    }
    html = template.render(**ctx)
    assert "5300" in html
    assert "4950(b)" in html
    assert "does not anticipate" in html  # default §5300 no-SA wording


# ── determinism of the resolver itself (11.1 support) ───────────────────────


def test_build_var_map_and_resolve_are_pure_and_deterministic():
    """Same frozen inputs -> byte-identical resolved output, twice in a row.

    compile_package's overall byte-equal re-render guarantee is covered by
    test_disclosure_package_compiler.py; this proves the boilerplate-token
    resolution step it depends on introduces no hidden state / nondeterminism.
    """
    html = '<p>Dear <span data-var="hoa_name"></span>, effective <span data-var="effective_date"></span>.</p>'
    computed = _computed(False)

    run_1 = bv.resolve(
        html,
        bv.build_var_map(hoa=_FakeHoa(), fiscal_year=2026, hoa_settings={}, computed=computed),
    )
    run_2 = bv.resolve(
        html,
        bv.build_var_map(hoa=_FakeHoa(), fiscal_year=2026, hoa_settings={}, computed=computed),
    )
    assert run_1 == run_2
