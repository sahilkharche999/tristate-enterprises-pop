"""add-full-document-editor: the widened content model (design.md D5).

The failure mode this file guards is silence: nh3 deletes a disallowed tag
without error, so a heading or table that the sanitizer doesn't know about
disappears from a legal document on save. These tests assert every tag in
the content model survives a round trip, and that widening it did not open
the door to scripts, links, images, or inline handlers.
"""
from __future__ import annotations

import pytest

from app.services import boilerplate_sanitize as sanitize


# ── every content-model tag survives ────────────────────────────────────────


def test_headings_survive_sanitize():
    html = "<h1>Cover</h1><h2>Note 4</h2><h3>Detail</h3>"
    assert sanitize.sanitize_slot_html(html) == html


def test_table_survives_sanitize():
    html = (
        "<table><thead><tr><th>Item</th><th>Amount</th></tr></thead>"
        "<tbody><tr><td>Cash reserves</td><td>$1,000.00</td></tr></tbody></table>"
    )
    assert sanitize.sanitize_slot_html(html) == html


def test_table_class_and_spans_survive():
    html = (
        '<table class="totals-row"><tbody><tr>'
        '<td colspan="2" rowspan="1">Total</td>'
        "</tr></tbody></table>"
    )
    out = sanitize.sanitize_slot_html(html)
    assert 'class="totals-row"' in out
    assert 'colspan="2"' in out
    assert 'rowspan="1"' in out


def test_block_chip_div_survives():
    html = '<div data-block="special_assessment_disclosure"></div>'
    assert sanitize.sanitize_slot_html(html) == html


def test_value_chip_span_survives():
    html = '<span data-var="hoa_name"></span>'
    assert sanitize.sanitize_slot_html(html) == html


def test_sup_survives():
    assert sanitize.sanitize_slot_html("<p>5300<sup>1</sup></p>") == (
        "<p>5300<sup>1</sup></p>"
    )


@pytest.mark.parametrize("tag", sorted(sanitize.CONTENT_MODEL_TAGS))
def test_every_content_model_tag_survives(tag):
    """No tag in the model may be silently dropped by the sanitizer."""
    if tag == "br":
        html = "<p>a<br>b</p>"
        assert "<br" in sanitize.sanitize_slot_html(html)
        return
    # Wrap table-internal tags so nh3 sees valid table structure; a bare
    # <td> outside a <table> is dropped as invalid HTML, not by the allowlist.
    wrappers = {
        "thead": "<table>{}</table>",
        "tbody": "<table>{}</table>",
        "tr": "<table><tbody>{}</tbody></table>",
        "th": "<table><tbody><tr>{}</tr></tbody></table>",
        "td": "<table><tbody><tr>{}</tr></tbody></table>",
        "li": "<ul>{}</ul>",
    }
    inner = f"<{tag}>x</{tag}>"
    html = wrappers.get(tag, "{}").format(inner)
    out = sanitize.sanitize_slot_html(html)
    assert f"<{tag}" in out, f"{tag!r} was stripped by the sanitizer"


# ── widening did not weaken the boundary ────────────────────────────────────


@pytest.mark.parametrize(
    "html, forbidden",
    [
        ("<p>hi<script>alert(1)</script></p>", "script"),
        ("<p>hi<style>p{color:red}</style></p>", "style"),
        ('<p><a href="http://evil.test">x</a></p>', "<a"),
        ('<p><img src="http://evil.test/x.png"></p>', "<img"),
        ('<iframe src="http://evil.test"></iframe>', "<iframe"),
    ],
)
def test_dangerous_tags_still_stripped(html, forbidden):
    out = sanitize.sanitize_slot_html(html)
    assert forbidden not in out


@pytest.mark.parametrize(
    "html, attr",
    [
        ('<p onclick="alert(1)">x</p>', "onclick"),
        ('<p style="color:red">x</p>', "style="),
        ('<td onmouseover="x()">c</td>', "onmouseover"),
        ('<h2 id="note-4">Note 4</h2>', "id="),
        ('<div data-block="x" onload="y()"></div>', "onload"),
    ],
)
def test_dangerous_attributes_still_stripped(html, attr):
    out = sanitize.sanitize_slot_html(html)
    assert attr not in out


def test_data_var_only_allowed_on_span():
    """A block chip name smuggled onto a span is not a block chip."""
    out = sanitize.sanitize_slot_html('<span data-block="outstanding_loan_note"></span>')
    assert "data-block" not in out


def test_data_block_only_allowed_on_div():
    out = sanitize.sanitize_slot_html('<div data-var="hoa_name"></div>')
    assert "data-var" not in out


def test_plain_text_still_passes_through_unchanged():
    assert sanitize.sanitize_slot_html("Dear Homeowner & Friends") == (
        "Dear Homeowner & Friends"
    )
