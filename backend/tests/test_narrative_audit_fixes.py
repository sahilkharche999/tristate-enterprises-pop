"""Regressions for the audit findings on add-full-document-editor.

Each test here pins a defect that shipped and was then fixed, so it cannot
return quietly:

* **H1** — the narrative map was resolved before the appendix TOC rows existed,
  so pass 1 rendered a TOC missing those rows. Page offsets are computed from
  pass-1 page counts, so a TOC that later grew a page shifted every downstream
  page number with only a log warning.
* **M1** — the render date was never frozen, so re-rendering a finalized
  package on another day rewrote Notes 1 and 7 ("information available as
  of …").
* **M2** — narrative bodies were trusted verbatim at compile.
* **M3** — a block chip carrying content would not match the resolver and
  would reach the PDF as raw ``data-block`` markup.
* **M6** — a multi-document save ran as N independent requests, so a failure
  midway could leave the firm defaults half-rewritten.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text as sql_text

from app.ai_implementation.db.models import Property
from app.services import boilerplate_variables as bv
from app.services import narrative_content as nc

CHIP_5300 = '<li data-block="special_assessment_disclosure"></li>'


# ── H1: pass 1 must already know the appendix rows ──────────────────────────


def test_appendix_toc_rows_resolve_when_entries_are_seeded():
    block_map = bv.build_block_map(
        fiscal_year=2026,
        computed={},
        appendix_toc_entries=[
            {"title": "Insurance Certificate", "page": "—"},
            {"title": "Collection Policy", "page": "—"},
        ],
    )
    assert block_map["appendix_toc_rows"].count("<li>") == 2


def test_compiler_resolves_narrative_after_appendix_entries_are_seeded():
    """The ordering itself is the fix — assert it at the source.

    Resolving inside the `ctx_full` literal (which is what shipped) put the
    narrative map before the appendix seeding, so `appendix_toc_rows` baked in
    an empty list for pass 1.
    """
    import inspect

    from app.disclosure_package import compiler

    source = inspect.getsource(compiler.compile_package)
    seed_at = source.index('ctx_full["appendix_toc_entries"] = [')
    resolve_at = source.index('ctx_full["narrative"] = _resolve_narrative(')
    assert seed_at < resolve_at, (
        "narrative must resolve after appendix_toc_entries is seeded, or pass 1 "
        "renders a TOC with no appendix rows"
    )


# ── M1: the render date is frozen ───────────────────────────────────────────


def test_compile_package_accepts_a_frozen_render_date():
    import inspect

    from app.disclosure_package.compiler import compile_package

    assert "render_date" in inspect.signature(compile_package).parameters


def test_today_chip_uses_the_frozen_date():
    var_map = bv.build_var_map(
        hoa=None,
        fiscal_year=2026,
        hoa_settings={},
        computed={},
        today="Tuesday March 3, 2026",
    )
    assert var_map["today"] == "Tuesday March 3, 2026"
    # letter_date falls back to it rather than to a second clock read.
    assert var_map["letter_date"] == "Tuesday March 3, 2026"


def test_finalize_snapshot_carries_render_date():
    import inspect

    from app.disclosure_package import service

    source = inspect.getsource(service)
    assert '"render_date"' in source


# ── M2: content is re-sanitized at compile ──────────────────────────────────


def test_compile_sanitizes_narrative_before_resolution():
    import inspect

    from app.disclosure_package import compiler

    source = inspect.getsource(compiler.compile_package)
    sanitize_at = source.index("sanitize_slot_html(body)")
    resolve_at = source.index("def _resolve_narrative")
    assert sanitize_at < resolve_at, (
        "sanitize must run before chip resolution — block chips emit trusted "
        "system HTML the operator allowlist would strip"
    )


# ── M3: block chips must be empty placeholders ──────────────────────────────


def test_empty_block_carrier_is_accepted():
    assert bv.find_non_empty_blocks('<div data-block="reserve_only_note"></div>') == []
    assert bv.find_non_empty_blocks(CHIP_5300) == []


def test_non_empty_block_carrier_is_reported():
    html = '<div data-block="reserve_only_note"><div>smuggled</div></div>'
    assert bv.find_non_empty_blocks(html) == ["reserve_only_note"]


def test_non_empty_block_carrier_rejected_at_save():
    with pytest.raises(bv.UnknownBoilerplateToken) as excinfo:
        nc.validate_document_html(
            "note_8",
            '<h2>Note 8</h2><div data-block="outstanding_loan_note">text</div>',
        )
    assert "empty placeholders" in str(excinfo.value)


def test_unknown_name_in_a_malformed_carrier_is_still_reported():
    """It must not slip past both the catalog check and the resolver."""
    assert bv.find_unknown_tokens('<div data-block="bogus">x</div>') == ["bogus"]


def test_resolver_never_matches_a_non_empty_carrier():
    """The tightened pattern is what makes the save-time rule necessary."""
    assert bv.BLOCK_CARRIER_RE.search('<div data-block="x">content</div>') is None
    assert bv.BLOCK_CARRIER_RE.search('<div data-block="x"></div>') is not None


# ── M6: multi-document save is atomic ───────────────────────────────────────


@pytest.fixture
def hoa(db_session):
    prop = Property(name="Bulk HOA", units=5, hoa_code="BULK")
    db_session.add(prop)
    db_session.commit()
    return prop.id


def _firm_rows(db_session) -> set:
    return {
        row[0]
        for row in db_session.execute(
            sql_text("SELECT document_id FROM narrative_overrides WHERE scope='firm'")
        ).fetchall()
    }


def test_bulk_save_writes_every_document(client, db_session, hoa):
    r = client.put(
        f"/hoa/{hoa}/documents?scope=firm",
        json={
            "documents": {
                "note_7": '<p>Seven.</p><ul><li data-block="significant_assumptions_variance"></li></ul>',
                "compilation_report": "<p>Report.</p>",
            }
        },
    )
    assert r.status_code == 200, r.text
    assert _firm_rows(db_session) == {"note_7", "compilation_report"}


def test_bulk_save_rolls_back_entirely_when_one_document_is_invalid(
    client, db_session, hoa
):
    """The whole point: a bad document late in the batch must not leave the
    earlier ones committed at firm scope, visible to every HOA."""
    r = client.put(
        f"/hoa/{hoa}/documents?scope=firm",
        json={
            "documents": {
                "compilation_report": "<p>Valid, and first.</p>",
                # Missing its required §5300 block.
                "cover_letter": "<p>Invalid, and second.</p>",
            }
        },
    )
    assert r.status_code == 400
    assert "special_assessment_disclosure" in r.json()["detail"]
    assert _firm_rows(db_session) == set()


def test_bulk_save_rejects_unknown_document_without_writing(client, db_session, hoa):
    r = client.put(
        f"/hoa/{hoa}/documents?scope=firm",
        json={
            "documents": {
                "compilation_report": "<p>Valid.</p>",
                "forecasted_income_statement": "<p>Not editable.</p>",
            }
        },
    )
    assert r.status_code == 400
    assert _firm_rows(db_session) == set()


def test_bulk_save_requires_a_documents_object(client, hoa):
    assert client.put(f"/hoa/{hoa}/documents?scope=firm", json={}).status_code == 400
    assert (
        client.put(
            f"/hoa/{hoa}/documents?scope=firm", json={"documents": {}}
        ).status_code
        == 400
    )


def test_bulk_save_honors_scope(client, db_session, hoa):
    client.put(
        f"/hoa/{hoa}/documents?scope=hoa",
        json={"documents": {"compilation_report": "<p>Just this HOA.</p>"}},
    )
    assert _firm_rows(db_session) == set()
    assert nc.effective_scope(db_session, "compilation_report", hoa) == "hoa"
