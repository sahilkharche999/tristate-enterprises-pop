"""add-full-document-editor: preflight gates over narrative documents.

Covers the spec requirement "Unknown tokens and missing required blocks fail
loudly". Both gates exist because the editor cannot be the only line of
defense: content can arrive from a snapshot frozen before a gate existed or
from a direct DB edit, and a deleted block chip is a legitimate editor action
whose consequence only shows up at compile time.
"""
from __future__ import annotations

import pytest

from app.disclosure_package.preflight import check_narrative_documents
from app.services import narrative_content as nc


def _baselines(**overrides: str) -> dict[str, str]:
    docs = {doc_id: nc.baseline_html(doc_id) for doc_id in nc.document_ids()}
    docs.update(overrides)
    return docs


def test_shipped_baselines_pass_preflight_clean():
    assert check_narrative_documents(_baselines()) == []


def test_none_and_empty_are_no_ops():
    assert check_narrative_documents(None) == []
    assert check_narrative_documents({}) == []


# ── unknown chips ───────────────────────────────────────────────────────────


def test_unknown_token_blocks_and_names_token_and_document():
    errors = check_narrative_documents(
        _baselines(note_7='<p><span data-var="hao_name"></span></p>')
    )
    assert len(errors) >= 1
    err = next(e for e in errors if e.code == "unknown_boilerplate_token")
    assert err.severity == "blocking"
    assert err.field_path == "narrative.note_7"
    assert "hao_name" in err.message
    assert "Note 7" in err.message  # the document, by its operator-facing label


def test_unknown_block_name_blocks():
    errors = check_narrative_documents(
        _baselines(note_1_3='<div data-block="pay_me_bitcoin"></div>')
    )
    assert any("pay_me_bitcoin" in e.message for e in errors)
    assert all(e.severity == "blocking" for e in errors)


def test_jinja_expression_blocks_rather_than_evaluating():
    errors = check_narrative_documents(
        _baselines(note_1_3='<p><span data-var="{{ 7*7 }}"></span></p>')
    )
    assert any("7*7" in e.message for e in errors)


# ── required blocks ─────────────────────────────────────────────────────────


def test_deleted_5300_disclosure_blocks_finalize():
    errors = check_narrative_documents(
        _baselines(cover_letter="<p>Dear Homeowner, that's all.</p>")
    )
    err = next(e for e in errors if e.code == "missing_required_block")
    assert err.severity == "blocking"
    assert err.field_path == "narrative.cover_letter"
    assert "special_assessment_disclosure" in err.message
    assert "Cover letter" in err.message


@pytest.mark.parametrize(
    "doc_id, block",
    [
        ("cover_letter", "special_assessment_disclosure"),
        ("note_6", "contribution_increase_schedule"),
        ("note_7", "significant_assumptions_variance"),
        ("note_8", "outstanding_loan_note"),
        ("budget_toc", "appendix_toc_rows"),
        ("budget_toc", "package_toc_rows"),
    ],
)
def test_every_required_block_is_enforced(doc_id, block):
    errors = check_narrative_documents(_baselines(**{doc_id: "<p>gutted</p>"}))
    assert any(
        e.code == "missing_required_block" and block in e.message for e in errors
    )


def test_required_block_survives_heavy_operator_restructuring():
    """Deleting prose around a required chip is fine; only the chip matters."""
    rebuilt = (
        "<h2>Our Letter</h2><p>Totally different wording.</p>"
        '<ol><li data-block="special_assessment_disclosure"></li></ol>'
    )
    errors = check_narrative_documents(_baselines(cover_letter=rebuilt))
    assert [e for e in errors if e.code == "missing_required_block"] == []


def test_block_chip_on_either_carrier_counts_as_present():
    for carrier in ("div", "li"):
        html = f'<{carrier} data-block="outstanding_loan_note"></{carrier}>'
        errors = check_narrative_documents(_baselines(note_8=html))
        assert [e for e in errors if e.code == "missing_required_block"] == [], carrier
