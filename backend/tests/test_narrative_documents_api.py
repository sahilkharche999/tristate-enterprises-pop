"""add-full-document-editor: the /hoa/{id}/documents endpoints.

Covers the API surface behind the single-document editor: listing in package
order, saving at either scope, resetting one layer, and refusing writes that
would put unrenderable content into a legal document.
"""
from __future__ import annotations

import pytest

from app.ai_implementation.db.models import Property
from app.services import narrative_content as nc

CHIP_5300 = '<li data-block="special_assessment_disclosure"></li>'


@pytest.fixture
def two_hoas(db_session):
    a = Property(name="Docs HOA A", units=5, hoa_code="DOCA")
    b = Property(name="Docs HOA B", units=5, hoa_code="DOCB")
    db_session.add_all([a, b])
    db_session.commit()
    return a.id, b.id


def _docs(payload) -> dict:
    return {d["id"]: d for d in payload["documents"]}


# ── list ────────────────────────────────────────────────────────────────────


def test_list_returns_documents_in_package_order(client, two_hoas):
    hoa_id, _ = two_hoas
    r = client.get(f"/hoa/{hoa_id}/documents")
    assert r.status_code == 200, r.text

    editable = [d["id"] for d in r.json()["documents"] if d["kind"] == "editable"]
    assert editable == nc.document_ids()


def test_list_includes_computed_placeholders_without_html(client, two_hoas):
    hoa_id, _ = two_hoas
    docs = _docs(client.get(f"/hoa/{hoa_id}/documents").json())

    form = docs["pro_forma_disclosure_summary.html"]
    assert form["kind"] == "computed"
    assert "html" not in form
    assert form["page_count_hint"] == 4


def test_list_exposes_chip_catalogs_for_the_editor_pickers(client, two_hoas):
    hoa_id, _ = two_hoas
    body = client.get(f"/hoa/{hoa_id}/documents").json()
    assert any(v["id"] == "hoa_name" for v in body["variables"])
    assert any(b["id"] == "special_assessment_disclosure" for b in body["blocks"])


def test_unedited_documents_report_baseline_scope(client, two_hoas):
    hoa_id, _ = two_hoas
    docs = _docs(client.get(f"/hoa/{hoa_id}/documents").json())
    assert docs["note_7"]["effective_scope"] == "baseline"
    assert docs["note_7"]["html"] == nc.baseline_html("note_7")


def test_unknown_hoa_is_404(client):
    assert client.get("/hoa/999999/documents").status_code == 404


# ── save ────────────────────────────────────────────────────────────────────


def test_save_at_hoa_scope_is_isolated_to_that_hoa(client, two_hoas):
    hoa_a, hoa_b = two_hoas
    r = client.put(
        f"/hoa/{hoa_a}/documents/note_7?scope=hoa",
        json={"html": '<p>Only A.</p><ul><li data-block='
                      '"significant_assumptions_variance"></li></ul>'},
    )
    assert r.status_code == 200, r.text
    assert _docs(r.json())["note_7"]["effective_scope"] == "hoa"

    docs_b = _docs(client.get(f"/hoa/{hoa_b}/documents").json())
    assert "Only A." not in docs_b["note_7"]["html"]
    assert docs_b["note_7"]["effective_scope"] == "baseline"


def test_save_at_firm_scope_applies_to_every_hoa(client, two_hoas):
    hoa_a, hoa_b = two_hoas
    r = client.put(
        f"/hoa/{hoa_a}/documents/compilation_report?scope=firm",
        json={"html": "<p>Firm-wide report wording.</p>"},
    )
    assert r.status_code == 200, r.text

    docs_b = _docs(client.get(f"/hoa/{hoa_b}/documents").json())
    assert "Firm-wide report wording." in docs_b["compilation_report"]["html"]
    assert docs_b["compilation_report"]["effective_scope"] == "firm"


def test_hoa_override_wins_over_firm(client, two_hoas):
    hoa_a, hoa_b = two_hoas
    client.put(
        f"/hoa/{hoa_a}/documents/compilation_report?scope=firm",
        json={"html": "<p>Firm text.</p>"},
    )
    client.put(
        f"/hoa/{hoa_a}/documents/compilation_report?scope=hoa",
        json={"html": "<p>A's own text.</p>"},
    )

    docs_a = _docs(client.get(f"/hoa/{hoa_a}/documents").json())
    docs_b = _docs(client.get(f"/hoa/{hoa_b}/documents").json())
    assert "A's own text." in docs_a["compilation_report"]["html"]
    assert docs_a["compilation_report"]["has_firm_override"] is True
    assert "Firm text." in docs_b["compilation_report"]["html"]


def test_save_sanitizes_and_keeps_tables(client, two_hoas):
    hoa_id, _ = two_hoas
    r = client.put(
        f"/hoa/{hoa_id}/documents/note_4_5?scope=hoa",
        json={
            "html": "<h2>Note 5</h2><script>evil()</script>"
            "<table><tbody><tr><td>Renamed row</td>"
            '<td><span data-var="percent_funded"></span></td></tr></tbody></table>'
        },
    )
    assert r.status_code == 200, r.text
    stored = _docs(r.json())["note_4_5"]["html"]
    assert "<script>" not in stored
    assert "<h2>Note 5</h2>" in stored
    assert "<table>" in stored
    assert 'data-var="percent_funded"' in stored


# ── rejected writes ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "document_id",
    ["forecasted_income_statement", "pro_forma_disclosure_summary", "nope"],
)
def test_computed_or_unknown_document_rejected_with_400(client, two_hoas, document_id):
    hoa_id, _ = two_hoas
    r = client.put(
        f"/hoa/{hoa_id}/documents/{document_id}?scope=hoa", json={"html": "<p>x</p>"}
    )
    assert r.status_code == 400


def test_unknown_token_rejected_with_400_naming_the_token(client, two_hoas):
    hoa_id, _ = two_hoas
    r = client.put(
        f"/hoa/{hoa_id}/documents/note_7?scope=hoa",
        json={"html": '<p><span data-var="hao_name"></span></p>'},
    )
    assert r.status_code == 400
    assert "hao_name" in r.json()["detail"]


def test_unknown_block_rejected_with_400(client, two_hoas):
    hoa_id, _ = two_hoas
    r = client.put(
        f"/hoa/{hoa_id}/documents/note_7?scope=hoa",
        json={"html": '<div data-block="pay_me_bitcoin"></div>'},
    )
    assert r.status_code == 400
    assert "pay_me_bitcoin" in r.json()["detail"]


def test_deleting_a_required_block_rejected_with_400(client, two_hoas):
    hoa_id, _ = two_hoas
    r = client.put(
        f"/hoa/{hoa_id}/documents/cover_letter?scope=hoa",
        json={"html": "<p>Dear Homeowner, that's all.</p>"},
    )
    assert r.status_code == 400
    assert "special_assessment_disclosure" in r.json()["detail"]


def test_bad_scope_rejected_with_400(client, two_hoas):
    hoa_id, _ = two_hoas
    r = client.put(
        f"/hoa/{hoa_id}/documents/note_7?scope=global", json={"html": "<p>x</p>"}
    )
    assert r.status_code == 400


def test_missing_html_rejected_with_400(client, two_hoas):
    hoa_id, _ = two_hoas
    assert client.put(f"/hoa/{hoa_id}/documents/note_7?scope=hoa", json={}).status_code == 400


# ── reset ───────────────────────────────────────────────────────────────────


def test_reset_hoa_falls_back_to_firm(client, two_hoas):
    hoa_id, _ = two_hoas
    client.put(
        f"/hoa/{hoa_id}/documents/compilation_report?scope=firm",
        json={"html": "<p>Firm text.</p>"},
    )
    client.put(
        f"/hoa/{hoa_id}/documents/compilation_report?scope=hoa",
        json={"html": "<p>HOA text.</p>"},
    )

    r = client.delete(f"/hoa/{hoa_id}/documents/compilation_report?scope=hoa")
    assert r.status_code == 200, r.text
    doc = _docs(r.json())["compilation_report"]
    assert doc["effective_scope"] == "firm"
    assert "Firm text." in doc["html"]


def test_reset_firm_falls_back_to_baseline(client, two_hoas):
    hoa_id, _ = two_hoas
    client.put(
        f"/hoa/{hoa_id}/documents/compilation_report?scope=firm",
        json={"html": "<p>Firm text.</p>"},
    )
    r = client.delete(f"/hoa/{hoa_id}/documents/compilation_report?scope=firm")
    doc = _docs(r.json())["compilation_report"]
    assert doc["effective_scope"] == "baseline"
    assert doc["html"] == nc.baseline_html("compilation_report")


def test_reset_of_an_unedited_document_is_a_no_op(client, two_hoas):
    hoa_id, _ = two_hoas
    r = client.delete(f"/hoa/{hoa_id}/documents/note_6?scope=hoa")
    assert r.status_code == 200
    assert _docs(r.json())["note_6"]["effective_scope"] == "baseline"


def test_reset_unknown_document_rejected_with_400(client, two_hoas):
    hoa_id, _ = two_hoas
    assert client.delete(
        f"/hoa/{hoa_id}/documents/nope?scope=hoa"
    ).status_code == 400


# ── the legacy three-slot API is retired ────────────────────────────────────


def test_legacy_boilerplate_endpoints_are_gone(client, two_hoas):
    """The frontend has cut over, so the slot API no longer exists. The
    `boilerplate_overrides_json` column stays as the rollback path, and the
    migration still reads it — see test_narrative_legacy_migration.py."""
    hoa_id, _ = two_hoas
    assert client.get(f"/hoa/{hoa_id}/settings/boilerplate").status_code == 404
    assert (
        client.put(
            f"/hoa/{hoa_id}/settings/boilerplate", json={"overrides": {}}
        ).status_code
        == 404
    )


def test_reference_pdf_upload_still_works(client, two_hoas):
    """Unrelated to the slots — the document editor still offers it."""
    hoa_id, _ = two_hoas
    assert client.get(f"/hoa/{hoa_id}/boilerplate/reference-jobs").status_code == 200
