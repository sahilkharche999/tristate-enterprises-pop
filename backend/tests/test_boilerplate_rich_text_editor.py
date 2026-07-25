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

from app.disclosure_package.render import _build_env
from app.services import boilerplate_sanitize as sanitize
from app.services import boilerplate_variables as bv
from app.services import hoa_boilerplate as bp
from app.services.boilerplate_variables import UnknownBoilerplateToken


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


# ── rendered formatting is unescaped (10.4) ─────────────────────────────────
#
# add-full-document-editor moved the cover letter from three carved-out slots
# to one editable narrative document. The two guarantees below are unchanged
# in substance: operator formatting reaches the PDF as real markup, and the
# statutory blocks stay system-owned no matter what the operator writes
# around them.


def _cover_ctx(body_html=None):
    from app.services import narrative_content as nc

    class _Hoa:
        name = "Test HOA"
        city = "San Jose"
        state = "CA"
        units = 10
        entity_type = None
        incorporation_year = None

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
        "boilerplate": bp.empty_boilerplate(),
        "toc_page_numbers": {},
        "appendix_toc_entries": [],
    }
    ctx["narrative"] = nc.resolve_for_context(
        ctx, {"cover_letter": body_html} if body_html else None
    )
    return ctx


def test_formatting_appears_unescaped_in_rendered_html():
    sanitized = sanitize.sanitize_slot_html(
        "<p><strong>Bold intro</strong></p><ul><li>Item one</li></ul>"
    )
    html = _build_env("standard").get_template("cover_letter.html").render(
        **_cover_ctx(sanitized)
    )
    assert "<strong>Bold intro</strong>" in html
    assert "<li>Item one</li>" in html
    assert "&lt;strong&gt;" not in html


# ── legal/statutory blocks are never operator-editable (10.5) ──────────────


def test_statutory_wording_is_a_block_chip_not_editable_prose():
    """The §5300 disclosure is system-generated, so it lives in BLOCK_CATALOG
    (resolved from data) and never in the value catalog operators type into."""
    assert "special_assessment_disclosure" in bv.BLOCK_CATALOG
    assert "special_assessment_disclosure" not in bv.TOKEN_CATALOG


def test_5300_wording_comes_from_data_not_from_operator_prose():
    """The operator owns every word around the §5300 chip; the chip's own
    wording is produced from the special-assessment data regardless."""
    html = _build_env("standard").get_template("cover_letter.html").render(
        **_cover_ctx(
            "<p>Completely rewritten letter.</p>"
            '<ol class="disclosure-list">'
            '<li data-block="special_assessment_disclosure"></li></ol>'
        )
    )
    assert "Completely rewritten letter." in html
    assert "5300" in html
    assert "does not anticipate" in html  # no-SA wording, from data


def test_deleting_the_5300_block_is_refused_at_save():
    """The §5300 block is deletable in the editor by design, so the
    guarantee that it survives lives in validation, not in the template."""
    from app.services import narrative_content as nc

    with pytest.raises(nc.MissingRequiredBlock):
        nc.validate_document_html("cover_letter", "<p>No disclosure here.</p>")


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
