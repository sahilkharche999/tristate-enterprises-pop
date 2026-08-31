"""Packed notes keep-together + TOC/page-number chrome."""
from __future__ import annotations

from pathlib import Path

from app.disclosure_package.merge import _PAGE_NUMBER_OVERLAY_CSS

TEMPLATES = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "disclosure_package"
    / "templates"
    / "standard"
)


def test_overlay_page_number_is_bottom_center():
    assert "@bottom-center" in _PAGE_NUMBER_OVERLAY_CSS
    assert "@bottom-right" not in _PAGE_NUMBER_OVERLAY_CSS
    assert "padding-bottom: 0.3in" in _PAGE_NUMBER_OVERLAY_CSS


def test_toc_page_column_is_right_aligned():
    css = (TEMPLATES / "_shared.css").read_text()
    assert "font-variant-numeric: tabular-nums" in css
    assert ".toc-page" in css
    assert "text-align: right" in css


def test_notes_packed_template_wraps_each_note():
    html = (TEMPLATES / "notes_packed.html").read_text()
    for key in (
        "narrative.note_1_3",
        "narrative.note_4_5",
        "narrative.note_6",
        "narrative.note_7",
        "narrative.note_8",
    ):
        assert key in html
    assert html.count("note-keep") == 5
    css = (TEMPLATES / "_shared.css").read_text()
    assert "page-break-inside: avoid" in css
    assert ".note-keep" in css


def test_two_short_notes_share_a_page():
    import fitz
    import pytest

    try:
        from weasyprint import HTML  # noqa: F401
    except OSError:
        pytest.skip("WeasyPrint system libraries are not available in this environment")

    from app.disclosure_package.render import render_template
    from tests.test_disclosure_package_render import _build_context

    ctx = _build_context()
    ctx["narrative"] = {
        "note_1_3": "<h2>Note 1 — The Association</h2><p>Short.</p>",
        "note_4_5": "<h2>Note 4 — Revenues</h2><p>Also short.</p>",
        "note_6": "<h2>Note 6 — Funding Plan</h2><p>Short.</p>",
        "note_7": "<h2>Note 7 — Assumptions</h2><p>Short.</p>",
        "note_8": "<h2>Note 8 — Loans</h2><p>Short.</p>",
    }
    pdf_bytes = render_template(template_name="notes_packed.html", context=ctx)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert doc.page_count == 1
        text = doc[0].get_text()
        assert "Note 1 — The Association" in text
        assert "Note 8 — Loans" in text
    finally:
        doc.close()


def test_budget_toc_baseline_uses_package_toc_rows():
    content = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "disclosure_package"
        / "content"
        / "standard"
        / "budget_toc.html"
    ).read_text()
    assert 'data-block="package_toc_rows"' in content
    assert 'data-block="appendix_toc_rows"' in content
    assert "page_pro_forma_disclosure_summary" not in content
