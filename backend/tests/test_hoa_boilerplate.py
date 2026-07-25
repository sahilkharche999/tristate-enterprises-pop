"""The retired three-slot overrides — what's left of them.

`add-full-document-editor` replaced the slots with full narrative documents.
The API, compile context, and templates no longer reference them; only the
read path survives, because `database.migrate_legacy_boilerplate_slots` uses
it to recover wording operators saved under the old model.

So this file covers exactly two things: that the migration can still read a
stored blob (including its legacy alias), and that the cover letter renders
from the new narrative path. Composition of those slots into a `cover_letter`
override is covered by `test_narrative_legacy_migration.py`.
"""
from __future__ import annotations

import pytest

from app.disclosure_package.render import _build_env
from app.services import hoa_boilerplate as bp


# ── the migration's read path ───────────────────────────────────────────────


def test_parse_returns_every_slot_key():
    assert set(bp.parse_overrides_json(None)) == set(bp.SLOT_REGISTRY)


@pytest.mark.parametrize("raw", [None, "", "   ", "not json", "[]", "null"])
def test_unusable_blobs_parse_to_empty(raw):
    assert all(v is None for v in bp.parse_overrides_json(raw).values())


def test_parse_reads_stored_values_and_trims():
    parsed = bp.parse_overrides_json('{"cover_letter_intro": "  Hello  "}')
    assert parsed["cover_letter_intro"] == "Hello"
    assert parsed["cover_letter_closing"] is None


def test_parse_drops_keys_outside_the_registry():
    assert "not_a_slot" not in bp.parse_overrides_json('{"not_a_slot": "x"}')


def test_legacy_cover_letter_body_alias_is_still_readable():
    """Rows predating the slot registry stored the intro under another key —
    the migration must not silently skip them."""
    parsed = bp.parse_overrides_json('{"cover_letter_body": "Legacy text"}')
    assert parsed["cover_letter_intro"] == "Legacy text"


def test_explicit_intro_wins_over_the_legacy_alias():
    parsed = bp.parse_overrides_json(
        '{"cover_letter_body": "old", "cover_letter_intro": "new"}'
    )
    assert parsed["cover_letter_intro"] == "new"


# ── cover letter HTML (now rendered from the narrative path) ────────────────


def _cover_ctx(body_html=None):
    """Render context for cover_letter.html, with `body_html` as the operator's
    edited document (None = the shipped baseline)."""
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
        "toc_page_numbers": {},
        "appendix_toc_entries": [],
    }
    bodies = {"cover_letter": body_html} if body_html else None
    ctx["narrative"] = nc.resolve_for_context(ctx, bodies)
    return ctx


def _render(body_html=None) -> str:
    return (
        _build_env("standard")
        .get_template("cover_letter.html")
        .render(**_cover_ctx(body_html))
    )


def test_cover_letter_override_reaches_rendered_html():
    html = _render(
        "<p>CUSTOM INTRO ONLY</p>"
        '<ol class="disclosure-list">'
        '<li data-block="special_assessment_disclosure"></li></ol>'
    )
    assert "CUSTOM INTRO ONLY" in html
    assert "Thank you for the prompt payment" not in html


def test_cover_letter_without_override_renders_the_shipped_baseline():
    html = _render()
    assert "Thank you for the prompt payment" in html
    assert "Please find the following documents enclosed" in html


def test_cover_letter_xss_escaped_in_override():
    """Sanitization happens at save time, not render time — the `safe_html`
    filter trusts already-sanitized storage (see boilerplate_sanitize)."""
    from app.services import boilerplate_sanitize

    sanitized = boilerplate_sanitize.sanitize_slot_html(
        "<p><script>evil()</script>text</p>"
    )
    assert "<script>" not in sanitized

    html = _render(sanitized)
    assert "evil()" not in html
    assert "text" in html
