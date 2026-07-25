"""add-full-document-editor: finalized packages re-render identically.

Spec: "Byte-equal re-render". The finalize snapshot freezes the *layered*
narrative bodies, so a re-render after the operator edits firm or HOA content
must reproduce the finalized document exactly.

These assert on the rendered HTML rather than the PDF bytes: PDF output
carries a creation timestamp and object-ordering noise from WeasyPrint, so
HTML equality is both the stricter and the more diagnosable check for what
this change actually controls. PDF-level page-count/sha invariants are
covered by test_disclosure_package_compiler.py.
"""
from __future__ import annotations

from app.disclosure_package.render import _build_env
from app.services import narrative_content as nc

HOA_ID = 10


def _ctx():
    class _Hoa:
        name = "Determinism HOA"
        city = "San Jose"
        state = "CA"
        units = 12
        entity_type = None
        incorporation_year = 1990

    class _Matrix:
        recipient_grain = "unit"

    return {
        "hoa": _Hoa(),
        "fiscal_year": 2026,
        "today": "Monday January 1, 2026",
        "hoa_settings": {
            "letter_date": "01/01/2026",
            "cpa_firm_name": "Test CPA LLP",
            "cpa_firm_address": "1 Ledger Way",
            "letter_signed_by": "Board",
            "letter_signed_by_title": None,
            "management_company": "Mgmt",
            "management_company_address": "1 Main",
            "management_company_phone": None,
            "management_company_fax": None,
            "management_company_web": None,
            "reserve_study_expert_name": "Expert Inc.",
            "interest_rate_after_tax": 0.018,
            "replacement_cost_increase_rate": 0.03,
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
        "boilerplate": {},
        "toc_page_numbers": {},
        "appendix_toc_entries": [],
    }


def _render(doc_id: str, bodies) -> str:
    ctx = _ctx()
    ctx["narrative"] = nc.resolve_for_context(ctx, bodies)
    template = nc.DOCUMENT_REGISTRY[doc_id].template
    return _build_env("standard").get_template(template).render(**ctx)


def test_resolution_is_pure_same_inputs_same_bytes():
    bodies = {doc_id: nc.baseline_html(doc_id) for doc_id in nc.document_ids()}
    assert _render("cover_letter", bodies) == _render("cover_letter", bodies)


def test_finalized_render_ignores_later_firm_and_hoa_edits(session):
    finalized_body = (
        "<p>As finalized.</p>"
        '<ol class="disclosure-list">'
        '<li data-block="special_assessment_disclosure"></li></ol>'
    )
    nc.save_document(session, "cover_letter", "hoa", HOA_ID, finalized_body)

    frozen = nc.for_render(
        use_snapshots=False, frozen=None, session=session, hoa_id=HOA_ID
    )
    before = _render("cover_letter", frozen)

    # The operator keeps working after finalize — at both scopes.
    nc.save_document(
        session,
        "cover_letter",
        "hoa",
        HOA_ID,
        '<p>Edited after finalize.</p><ol><li data-block="special_assessment_disclosure">'
        "</li></ol>",
    )
    nc.save_document(
        session,
        "cover_letter",
        "firm",
        None,
        '<p>Firm-wide rewrite.</p><ol><li data-block="special_assessment_disclosure">'
        "</li></ol>",
    )

    replayed = nc.for_render(use_snapshots=True, frozen=frozen)
    after = _render("cover_letter", replayed)

    assert after == before
    assert "As finalized." in after
    assert "Edited after finalize." not in after
    assert "Firm-wide rewrite." not in after


def test_live_render_does_pick_up_edits(session):
    """The mirror of the above: without a snapshot, edits take effect."""
    nc.save_document(
        session,
        "note_7",
        "firm",
        None,
        '<p>New firm assumptions.</p><ul><li data-block='
        '"significant_assumptions_variance"></li></ul>',
    )
    live = nc.for_render(
        use_snapshots=False, frozen=None, session=session, hoa_id=HOA_ID
    )
    assert "New firm assumptions." in _render("note_7", live)


def test_snapshot_missing_a_document_falls_back_to_baseline_not_live(session):
    """An older snapshot predating a document must not silently pull in
    whatever the operator has written since."""
    nc.save_document(session, "note_8", "firm", None, "<p>Written later.</p>"
                     '<div data-block="outstanding_loan_note"></div>')
    replayed = nc.for_render(use_snapshots=True, frozen={"cover_letter": "<p>x</p>"})
    assert "Written later." not in replayed["note_8"]
    assert replayed["note_8"] == nc.baseline_html("note_8")
