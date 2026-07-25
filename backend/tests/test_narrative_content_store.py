"""add-full-document-editor: narrative content store + layered resolution.

Covers spec requirement "Firm-level and per-HOA override layers": resolution
precedence, reset falling back exactly one layer, a new HOA inheriting firm
content with no operator action, and rejection of unknown / computed
document ids.

Baselines are stubbed here so these tests exercise resolution mechanics
independently of the shipped prose (baseline content is covered by the
per-document conversion tests).
"""
from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import text as sql_text

from app.services import narrative_content as nc
from app.services.boilerplate_variables import UnknownBoilerplateToken


def _with_required_blocks(monkeypatch, **overrides: frozenset) -> None:
    """Swap DOCUMENT_REGISTRY for one with the given required-block sets.

    NarrativeDocument is frozen, so this replaces the entries rather than
    mutating them.
    """
    patched = {
        doc_id: dataclasses.replace(
            doc, required_blocks=overrides.get(doc_id, frozenset())
        )
        for doc_id, doc in nc.DOCUMENT_REGISTRY.items()
    }
    monkeypatch.setattr(nc, "DOCUMENT_REGISTRY", patched)


@pytest.fixture(autouse=True)
def stub_baselines(monkeypatch):
    """Every document resolves to a recognizable baseline string."""
    monkeypatch.setattr(
        nc, "baseline_html", lambda doc_id: f"<p>BASELINE {doc_id}</p>"
    )


@pytest.fixture(autouse=True)
def no_required_blocks(monkeypatch):
    """Required-block enforcement has its own tests; keep these focused."""
    _with_required_blocks(monkeypatch)


HOA_ID = 10
OTHER_HOA_ID = 11


# ── registry ────────────────────────────────────────────────────────────────


def test_registry_is_in_package_order():
    ids = nc.document_ids()
    assert ids[0] == "cover_letter"
    assert ids.index("note_1_3") < ids.index("note_4_5") < ids.index("note_8")
    assert ids[-1] == "thirty_year_compilation"


def test_registry_covers_the_spec_document_list():
    assert set(nc.document_ids()) == {
        "cover_letter", "note_1_3", "note_4_5", "note_6", "note_7", "note_8",
        "compilation_report", "thirty_year_compilation", "insurance_cover",
        "annual_budget_cover", "forecasted_title", "reserve_schedule_title",
        "thirty_year_title", "budget_toc",
    }


@pytest.mark.parametrize(
    "computed_doc",
    [
        "forecasted_income_statement",
        "pro_forma_disclosure_summary",
        "reserve_component_schedule",
        "major_component_schedule",
        "thirty_year_cash_flow_panel",
    ],
)
def test_computed_documents_are_not_in_the_registry(computed_doc):
    with pytest.raises(nc.UnknownNarrativeDocument):
        nc.require_document(computed_doc)


def test_unknown_document_rejected(session):
    with pytest.raises(nc.UnknownNarrativeDocument):
        nc.resolve_document(session, "no_such_doc", HOA_ID)


# ── resolution precedence ───────────────────────────────────────────────────


def test_unedited_document_resolves_to_baseline(session):
    assert nc.resolve_document(session, "note_7", HOA_ID) == "<p>BASELINE note_7</p>"
    assert nc.effective_scope(session, "note_7", HOA_ID) == "baseline"


def test_firm_override_applies_to_every_hoa(session):
    nc.save_document(session, "compilation_report", "firm", None, "<p>Firm text</p>")
    for hoa_id in (HOA_ID, OTHER_HOA_ID, None):
        assert nc.resolve_document(session, "compilation_report", hoa_id) == (
            "<p>Firm text</p>"
        )


def test_hoa_override_wins_over_firm(session):
    nc.save_document(session, "cover_letter", "firm", None, "<p>Firm letter</p>")
    nc.save_document(session, "cover_letter", "hoa", HOA_ID, "<p>Old Mill letter</p>")

    assert nc.resolve_document(session, "cover_letter", HOA_ID) == (
        "<p>Old Mill letter</p>"
    )
    assert nc.resolve_document(session, "cover_letter", OTHER_HOA_ID) == (
        "<p>Firm letter</p>"
    )
    assert nc.effective_scope(session, "cover_letter", HOA_ID) == "hoa"
    assert nc.effective_scope(session, "cover_letter", OTHER_HOA_ID) == "firm"


def test_new_hoa_inherits_firm_content_with_no_operator_action(session):
    nc.save_document(session, "note_1_3", "firm", None, "<p>Firm note 1-3</p>")
    brand_new_hoa = 999
    assert nc.resolve_document(session, "note_1_3", brand_new_hoa) == (
        "<p>Firm note 1-3</p>"
    )


# ── reset ───────────────────────────────────────────────────────────────────


def test_reset_hoa_falls_back_to_firm(session):
    nc.save_document(session, "note_7", "firm", None, "<p>Firm 7</p>")
    nc.save_document(session, "note_7", "hoa", HOA_ID, "<p>HOA 7</p>")

    assert nc.reset_document(session, "note_7", "hoa", HOA_ID) is True
    assert nc.resolve_document(session, "note_7", HOA_ID) == "<p>Firm 7</p>"


def test_reset_hoa_falls_back_to_baseline_when_no_firm_row(session):
    nc.save_document(session, "note_7", "hoa", HOA_ID, "<p>HOA 7</p>")
    nc.reset_document(session, "note_7", "hoa", HOA_ID)
    assert nc.resolve_document(session, "note_7", HOA_ID) == "<p>BASELINE note_7</p>"


def test_reset_firm_leaves_hoa_override_intact(session):
    nc.save_document(session, "note_8", "firm", None, "<p>Firm 8</p>")
    nc.save_document(session, "note_8", "hoa", HOA_ID, "<p>HOA 8</p>")

    nc.reset_document(session, "note_8", "firm", None)
    assert nc.resolve_document(session, "note_8", HOA_ID) == "<p>HOA 8</p>"
    assert nc.resolve_document(session, "note_8", OTHER_HOA_ID) == (
        "<p>BASELINE note_8</p>"
    )


def test_reset_of_absent_row_is_a_no_op(session):
    assert nc.reset_document(session, "note_6", "firm", None) is False


# ── writes ──────────────────────────────────────────────────────────────────


def test_save_is_an_upsert_not_a_duplicate(session):
    nc.save_document(session, "note_7", "hoa", HOA_ID, "<p>first</p>")
    nc.save_document(session, "note_7", "hoa", HOA_ID, "<p>second</p>")

    rows = session.execute(
        sql_text(
            "SELECT COUNT(*) FROM narrative_overrides "
            "WHERE scope='hoa' AND scope_id=:sid AND document_id='note_7'"
        ),
        {"sid": HOA_ID},
    ).scalar()
    assert rows == 1
    assert nc.resolve_document(session, "note_7", HOA_ID) == "<p>second</p>"


def test_save_sanitizes_before_storing(session):
    nc.save_document(
        session, "note_7", "hoa", HOA_ID, "<p>ok<script>alert(1)</script></p>"
    )
    stored = nc.resolve_document(session, "note_7", HOA_ID)
    assert "script" not in stored


def test_save_rejects_unknown_token(session):
    with pytest.raises(UnknownBoilerplateToken):
        nc.save_document(
            session, "note_7", "hoa", HOA_ID, '<p><span data-var="hao_name"></span></p>'
        )


def test_save_rejects_unknown_document(session):
    with pytest.raises(nc.UnknownNarrativeDocument):
        nc.save_document(session, "forecasted_income_statement", "firm", None, "<p>x</p>")


@pytest.mark.parametrize(
    "scope, scope_id",
    [
        ("firm", 10),      # firm rows must not carry a scope_id
        ("hoa", None),     # HOA rows must carry one
        ("global", None),  # not a scope at all
    ],
)
def test_save_rejects_malformed_scope(session, scope, scope_id):
    with pytest.raises(nc.UnknownNarrativeScope):
        nc.save_document(session, "note_7", scope, scope_id, "<p>x</p>")


def test_delete_hoa_overrides_removes_only_that_hoa(session):
    nc.save_document(session, "note_7", "hoa", HOA_ID, "<p>a</p>")
    nc.save_document(session, "note_8", "hoa", HOA_ID, "<p>b</p>")
    nc.save_document(session, "note_7", "hoa", OTHER_HOA_ID, "<p>c</p>")
    nc.save_document(session, "note_7", "firm", None, "<p>firm</p>")

    assert nc.delete_hoa_overrides(session, HOA_ID) == 2
    assert nc.resolve_document(session, "note_7", HOA_ID) == "<p>firm</p>"
    assert nc.resolve_document(session, "note_7", OTHER_HOA_ID) == "<p>c</p>"


# ── required blocks ─────────────────────────────────────────────────────────


def test_missing_required_block_rejected(session, monkeypatch):
    _with_required_blocks(
        monkeypatch, cover_letter=frozenset({"special_assessment_disclosure"})
    )
    with pytest.raises(nc.MissingRequiredBlock) as excinfo:
        nc.save_document(session, "cover_letter", "hoa", HOA_ID, "<p>no chip here</p>")
    assert "special_assessment_disclosure" in str(excinfo.value)


def test_required_block_present_accepted(session, monkeypatch):
    _with_required_blocks(
        monkeypatch, cover_letter=frozenset({"special_assessment_disclosure"})
    )
    nc.save_document(
        session,
        "cover_letter",
        "hoa",
        HOA_ID,
        '<p>Dear Homeowner</p><div data-block="special_assessment_disclosure"></div>',
    )
    assert "special_assessment_disclosure" in nc.resolve_document(
        session, "cover_letter", HOA_ID
    )


# ── resolve_all / API shape ─────────────────────────────────────────────────


def test_resolve_all_always_carries_every_document(session):
    resolved = nc.resolve_all(session, HOA_ID)
    assert set(resolved) == set(nc.DOCUMENT_REGISTRY)
    assert all(value for value in resolved.values())


def test_documents_for_api_interleaves_computed_placeholders(session):
    nc.save_document(session, "note_7", "hoa", HOA_ID, "<p>mine</p>")
    rows = nc.documents_for_api(session, HOA_ID)

    kinds = [row["kind"] for row in rows]
    assert "computed" in kinds and "editable" in kinds

    by_id = {row["id"]: row for row in rows}
    assert by_id["note_7"]["effective_scope"] == "hoa"
    assert by_id["note_7"]["html"] == "<p>mine</p>"
    assert by_id["cover_letter"]["effective_scope"] == "baseline"

    # The §5570 form appears as a read-only card, never as an editable doc.
    form = by_id["pro_forma_disclosure_summary.html"]
    assert form["kind"] == "computed"
    assert "html" not in form


def test_for_render_snapshot_branch_ignores_live_content(session):
    nc.save_document(session, "note_7", "hoa", HOA_ID, "<p>live edit</p>")
    frozen = {"note_7": "<p>as finalized</p>"}

    rendered = nc.for_render(
        use_snapshots=True, frozen=frozen, session=session, hoa_id=HOA_ID
    )
    assert rendered["note_7"] == "<p>as finalized</p>"
    # Documents absent from an older snapshot fall back to baseline, not live.
    assert rendered["note_8"] == "<p>BASELINE note_8</p>"


def test_for_render_live_branch_uses_current_content(session):
    nc.save_document(session, "note_7", "hoa", HOA_ID, "<p>live edit</p>")
    rendered = nc.for_render(
        use_snapshots=False, frozen=None, session=session, hoa_id=HOA_ID
    )
    assert rendered["note_7"] == "<p>live edit</p>"
