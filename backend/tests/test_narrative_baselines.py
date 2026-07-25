"""add-full-document-editor: the shipped baselines lint clean (design.md D5).

The dangerous failure mode is silence. nh3 deletes a disallowed tag without
raising, so a baseline containing a tag the content model omits would lose
that markup the first time an operator opened the document and hit save —
quietly, in a legal disclosure. Likewise a baseline referencing a chip name
outside the catalogs would only fail at compile, long after the edit.

These tests fail at the source instead: every shipped baseline must survive
its own sanitizer, reference only known chips, and carry the blocks its
document declares required.
"""
from __future__ import annotations

import re

import pytest

from app.services import boilerplate_sanitize as sanitize
from app.services import boilerplate_variables as bv
from app.services import narrative_content as nc

DOC_IDS = nc.document_ids()

_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)")


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_baseline_file_exists(doc_id):
    assert nc.DOCUMENT_REGISTRY[doc_id].baseline_path.is_file()


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_baseline_survives_sanitize_unchanged(doc_id):
    """The round trip an operator's first save performs."""
    baseline = nc.baseline_html(doc_id)
    assert sanitize.sanitize_slot_html(baseline) == baseline


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_baseline_uses_only_content_model_tags(doc_id):
    used = set(_TAG_RE.findall(nc.baseline_html(doc_id)))
    disallowed = sorted(used - set(sanitize.CONTENT_MODEL_TAGS))
    assert disallowed == [], f"{doc_id} uses tags outside the content model"


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_baseline_references_only_known_chips(doc_id):
    assert bv.find_unknown_tokens(nc.baseline_html(doc_id)) == []


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_baseline_carries_its_required_blocks(doc_id):
    doc = nc.DOCUMENT_REGISTRY[doc_id]
    present = nc.blocks_present(nc.baseline_html(doc_id))
    assert doc.required_blocks <= present


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_baseline_carries_no_inline_style_or_jinja(doc_id):
    """Both would be silently destroyed: `style=` by nh3, `{{ }}` by nothing.

    Narrative bodies are never template-evaluated, so a leftover Jinja
    expression would render literally in a legal document.
    """
    baseline = nc.baseline_html(doc_id)
    assert "style=" not in baseline
    assert "{{" not in baseline
    assert "{%" not in baseline


@pytest.mark.parametrize("doc_id", DOC_IDS)
def test_baseline_saves_through_the_real_write_path(session, doc_id):
    """Baseline content is valid operator input — reset/re-save is lossless."""
    baseline = nc.baseline_html(doc_id)
    stored = nc.save_document(session, doc_id, "firm", None, baseline)
    assert stored == baseline
    assert nc.resolve_document(session, doc_id, 10) == baseline


def test_every_editable_template_is_a_shell():
    """No narrative template may keep a second copy of its own content."""
    from app.disclosure_package.render import TEMPLATES_DIR

    for doc in nc.DOCUMENT_REGISTRY.values():
        source = (TEMPLATES_DIR / "standard" / doc.template).read_text()
        assert f"narrative.{doc.doc_id}" in source, doc.template
        # A shell is: extends, title, comment, content block. Anything with
        # conditional logic left in it is content living in two places.
        assert "{% if" not in source, f"{doc.template} still branches on data"
        assert "{% for" not in source, f"{doc.template} still loops over data"
