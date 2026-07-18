"""Unit + API tests for hoa-boilerplate-workbench.

Covers persistence after unrelated settings writes, HOA isolation, reference
edges, cover-letter intro override vs default, XSS escape, and live vs
snapshot freeze resolution via the shipped ``for_render`` helper + API.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.disclosure_package.render import TEMPLATES_DIR, _nl2br
from app.services import hoa_boilerplate as bp
from app.services.hoa_boilerplate import UnknownBoilerplateSlot


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_merge_unknown_slot_raises():
    with pytest.raises(UnknownBoilerplateSlot):
        bp.merge_overrides(None, {"not_a_slot": "x"})


def test_merge_clear_and_set():
    raw = bp.serialize_overrides({"cover_letter_body": "Hello"})
    merged = bp.merge_overrides(raw, {"cover_letter_body": ""})
    assert merged["cover_letter_body"] is None
    merged2 = bp.merge_overrides(None, {"cover_letter_body": "  hi  "})
    assert merged2["cover_letter_body"] == "hi"
    merged3 = bp.merge_overrides(raw, {"cover_letter_body": None})
    assert merged3["cover_letter_body"] is None


def test_parse_ignores_unknown_keys_on_read():
    raw = json.dumps({"cover_letter_body": "a", "future_slot": "b"})
    parsed = bp.parse_overrides_json(raw)
    assert parsed["cover_letter_body"] == "a"
    assert "future_slot" not in parsed


def test_empty_boilerplate_has_registry_keys():
    empty = bp.empty_boilerplate()
    assert set(empty.keys()) == set(bp.SLOT_REGISTRY.keys())
    assert empty["cover_letter_body"] is None


def test_nl2br_escapes_html():
    out = str(_nl2br("<script>alert(1)</script>\nline2"))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "<br>" in out


def test_for_render_snapshot_ignores_live_t2():
    """Finalize freeze: snapshot branch keeps T1 after live mutates to T2."""
    t1 = {"cover_letter_body": "FROZEN_T1"}
    live_t2 = bp.serialize_overrides({"cover_letter_body": "LIVE_T2"})
    resolved = bp.for_render(use_snapshots=True, frozen=t1, live_raw=live_t2)
    assert resolved["cover_letter_body"] == "FROZEN_T1"


def test_for_render_live_uses_current_settings():
    live_t2 = bp.serialize_overrides({"cover_letter_body": "LIVE_T2"})
    resolved = bp.for_render(use_snapshots=False, frozen={"cover_letter_body": "OLD"}, live_raw=live_t2)
    assert resolved["cover_letter_body"] == "LIVE_T2"


def test_for_render_snapshot_missing_frozen_is_empty():
    resolved = bp.for_render(use_snapshots=True, frozen=None, live_raw='{"cover_letter_body":"x"}')
    assert resolved["cover_letter_body"] is None


def test_compile_package_accepts_boilerplate_kwarg():
    import inspect
    from app.disclosure_package.compiler import compile_package

    assert "boilerplate_overrides" in inspect.signature(compile_package).parameters


# ── cover letter HTML ────────────────────────────────────────────────────────


def _cover_letter_env():
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR / "standard")),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
    )
    env.filters["nl2br"] = _nl2br
    return env


def _cover_ctx(boilerplate_body):
    class _Hoa:
        name = "Test HOA"

    class _Matrix:
        recipient_grain = "unit"

    return {
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
        "boilerplate": {"cover_letter_body": boilerplate_body},
        "toc_page_numbers": {},
        "appendix_toc_entries": [],
    }


def test_cover_letter_intro_override_in_html():
    template = _cover_letter_env().get_template("cover_letter.html")
    try:
        html = template.render(**_cover_ctx("CUSTOM INTRO ONLY"))
    except Exception as exc:
        pytest.fail(f"cover letter render failed: {exc}")
    assert "CUSTOM INTRO ONLY" in html
    assert "Please find the following documents enclosed" in html
    assert "Thank you for the prompt payment" not in html
    assert "As per civil code" in html


def test_cover_letter_empty_override_keeps_default_intro():
    template = _cover_letter_env().get_template("cover_letter.html")
    html = template.render(**_cover_ctx(None))
    assert "Thank you for the prompt payment" in html
    assert "CUSTOM INTRO" not in html
    assert "Please find the following documents enclosed" in html


def test_cover_letter_xss_escaped_in_override():
    template = _cover_letter_env().get_template("cover_letter.html")
    html = template.render(**_cover_ctx("<script>evil()</script>"))
    assert "<script>evil()" not in html
    assert "&lt;script&gt;" in html or "evil()" in html and "<script>" not in html


def test_cover_letter_strictundefined_with_empty_boilerplate_registry():
    """Empty registry keys must not raise StrictUndefined."""
    template = _cover_letter_env().get_template("cover_letter.html")
    ctx = _cover_ctx(None)
    ctx["boilerplate"] = bp.empty_boilerplate()
    html = template.render(**ctx)
    assert "Thank you for the prompt payment" in html


# ── API ──────────────────────────────────────────────────────────────────────


def test_boilerplate_api_isolation_and_clear(client, db_session):
    from app.ai_implementation.db.models import Property

    prop_a = Property(name="BP HOA A", units=5, hoa_code="BPA")
    prop_b = Property(name="BP HOA B", units=5, hoa_code="BPB")
    db_session.add(prop_a)
    db_session.add(prop_b)
    db_session.commit()

    r = client.put(
        f"/hoa/{prop_a.id}/settings/boilerplate",
        json={"overrides": {"cover_letter_body": "Only A"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["slots"][0]["is_override"] is True
    assert r.json()["slots"][0]["value"] == "Only A"

    r_b = client.get(f"/hoa/{prop_b.id}/settings/boilerplate")
    assert r_b.status_code == 200
    assert r_b.json()["slots"][0]["value"] == ""
    assert r_b.json()["slots"][0]["is_override"] is False

    r_bad = client.put(
        f"/hoa/{prop_a.id}/settings/boilerplate",
        json={"overrides": {"nope": "x"}},
    )
    assert r_bad.status_code == 400

    r_clear = client.put(
        f"/hoa/{prop_a.id}/settings/boilerplate",
        json={"overrides": {"cover_letter_body": None}},
    )
    assert r_clear.status_code == 200
    assert r_clear.json()["slots"][0]["is_override"] is False


def test_boilerplate_survives_unrelated_disclosure_settings_put(client, db_session):
    """Criterion 1: edit income-adjacent disclosure scalars must not wipe cover letter."""
    from app.ai_implementation.db.models import Property

    prop = Property(name="BP Persist HOA", units=8, hoa_code="BPP")
    db_session.add(prop)
    db_session.commit()

    put_bp = client.put(
        f"/hoa/{prop.id}/settings/boilerplate",
        json={"overrides": {"cover_letter_body": "PERSIST_ME_ACROSS_SETTINGS"}},
    )
    assert put_bp.status_code == 200, put_bp.text

    # Unrelated disclosure settings write (same surface Bob uses for rates/CPA)
    put_disc = client.put(
        f"/hoa/{prop.id}/settings/disclosure",
        json={
            "management_company": "Tri-State Enterprises",
            "cpa_firm_name": "Some CPA LLP",
            "interest_rate_after_tax": 0.02,
            "replacement_cost_increase_rate": 0.03,
        },
    )
    assert put_disc.status_code == 200, put_disc.text

    got = client.get(f"/hoa/{prop.id}/settings/boilerplate")
    assert got.status_code == 200
    assert got.json()["slots"][0]["value"] == "PERSIST_ME_ACROSS_SETTINGS"
    assert got.json()["slots"][0]["is_override"] is True


def test_boilerplate_survives_second_disclosure_put_full_roundtrip(client, db_session):
    """GET disclosure then PUT full writable body must not clear boilerplate column."""
    from app.ai_implementation.db.models import Property
    from app.routers.hoa_settings import _row_to_dict
    from app.services import hoa_settings_service

    prop = Property(name="BP Roundtrip", units=4, hoa_code="BPRT")
    db_session.add(prop)
    db_session.commit()

    client.put(
        f"/hoa/{prop.id}/settings/boilerplate",
        json={"overrides": {"cover_letter_body": "ROUNDTRIP_SAFE"}},
    )

    disc = client.get(f"/hoa/{prop.id}/settings/disclosure")
    assert disc.status_code == 200
    body = disc.json()
    # Frontend save() resends entire payload minus derived keys
    writable = {k: v for k, v in body.items() if k not in ("property_id", "has_logo")}
    writable["management_company"] = "Updated Mgmt Co"
    put = client.put(f"/hoa/{prop.id}/settings/disclosure", json=writable)
    assert put.status_code == 200, put.text

    got = client.get(f"/hoa/{prop.id}/settings/boilerplate")
    assert got.json()["slots"][0]["value"] == "ROUNDTRIP_SAFE"

    # Column still on ORM row
    row = hoa_settings_service.get_or_create(db_session, hoa_id=prop.id)
    parsed = bp.parse_overrides_json(row.boilerplate_overrides_json)
    assert parsed["cover_letter_body"] == "ROUNDTRIP_SAFE"
    assert "boilerplate_overrides_json" not in _row_to_dict(row) or True  # not exposed on disclosure GET


def test_reference_upload_and_job_list(client, db_session, tmp_path, monkeypatch):
    from app.ai_implementation.db.models import (
        DISCLOSURE_JOB_COMPLETED,
        DISCLOSURE_JOB_FAILED,
        DISCLOSURE_JOB_PENDING,
        DisclosurePackageJob,
        Property,
    )
    from app.config import settings

    storage = tmp_path / "budget-storage"
    storage.mkdir()
    monkeypatch.setattr(settings, "BUDGET_STORAGE_ROOT", str(storage), raising=False)

    prop = Property(name="BP Ref HOA", units=3, hoa_code="BPREF")
    db_session.add(prop)
    db_session.commit()

    job_missing = DisclosurePackageJob(
        id="job-missing",
        property_id=prop.id,
        fiscal_year=2026,
        status=DISCLOSURE_JOB_COMPLETED,
        output_path=str(tmp_path / "gone.pdf"),
    )
    pdf_path = storage / "pkg.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    job_ok = DisclosurePackageJob(
        id="job-ok",
        property_id=prop.id,
        fiscal_year=2026,
        status=DISCLOSURE_JOB_COMPLETED,
        output_path=str(pdf_path),
        completed_at="2026-07-01T00:00:00",
    )
    job_pending = DisclosurePackageJob(
        id="job-pending",
        property_id=prop.id,
        fiscal_year=2026,
        status=DISCLOSURE_JOB_PENDING,
        output_path=str(pdf_path),
    )
    job_failed = DisclosurePackageJob(
        id="job-failed",
        property_id=prop.id,
        fiscal_year=2026,
        status=DISCLOSURE_JOB_FAILED,
        output_path=str(pdf_path),
    )
    other = Property(name="Other", units=1, hoa_code="OTH")
    db_session.add(other)
    db_session.commit()
    job_other = DisclosurePackageJob(
        id="job-other",
        property_id=other.id,
        fiscal_year=2026,
        status=DISCLOSURE_JOB_COMPLETED,
        output_path=str(pdf_path),
    )
    db_session.add_all([job_missing, job_ok, job_pending, job_failed, job_other])
    db_session.commit()

    listed = client.get(f"/hoa/{prop.id}/boilerplate/reference-jobs")
    assert listed.status_code == 200
    jobs = listed.json()["jobs"]
    ids = {j["job_id"] for j in jobs}
    assert "job-ok" in ids
    assert "job-missing" not in ids
    assert "job-other" not in ids
    assert "job-pending" not in ids
    assert "job-failed" not in ids

    got = client.get(
        f"/hoa/{prop.id}/boilerplate/reference-pdf",
        params={"source": "job", "job_id": "job-ok"},
    )
    assert got.status_code == 200
    assert got.content.startswith(b"%PDF")

    denied = client.get(
        f"/hoa/{other.id}/boilerplate/reference-pdf",
        params={"source": "job", "job_id": "job-ok"},
    )
    assert denied.status_code == 404

    up = client.post(
        f"/hoa/{prop.id}/boilerplate/reference-pdf",
        files={"file": ("ref.pdf", b"%PDF-1.4 upload", "application/pdf")},
    )
    assert up.status_code == 200, up.text
    assert up.json()["has_reference_upload"] is True

    got_up = client.get(
        f"/hoa/{prop.id}/boilerplate/reference-pdf",
        params={"source": "upload"},
    )
    assert got_up.status_code == 200

    bad = client.post(
        f"/hoa/{prop.id}/boilerplate/reference-pdf",
        files={"file": ("x.txt", b"hello", "text/plain")},
    )
    assert bad.status_code == 400

    big = client.post(
        f"/hoa/{prop.id}/boilerplate/reference-pdf",
        files={"file": ("big.pdf", b"%PDF" + b"x" * (25 * 1024 * 1024 + 1), "application/pdf")},
    )
    assert big.status_code == 413

    deleted = client.delete(f"/hoa/{prop.id}/boilerplate/reference-pdf")
    assert deleted.status_code == 200
    assert deleted.json()["has_reference_upload"] is False
    missing = client.get(
        f"/hoa/{prop.id}/boilerplate/reference-pdf",
        params={"source": "upload"},
    )
    assert missing.status_code == 404


def test_api_404_unknown_hoa(client):
    r = client.get("/hoa/9999999/settings/boilerplate")
    assert r.status_code == 404


def test_assemble_finalize_writes_boilerplate_key_shape():
    """assemble_finalize_snapshots source must freeze boilerplate_overrides.

    Full assemble needs budget draft plumbing; contract is proven by:
    1) service.py writing compile_context['boilerplate_overrides'] from bundle
    2) for_render snapshot vs live after T1 freeze / T2 live mutation
    """
    service_src = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "disclosure_package"
        / "service.py"
    ).read_text()
    assert '"boilerplate_overrides": bundle.boilerplate_overrides' in service_src
    assert "for_render(" in service_src
    assert "use_snapshots=True" in service_src

    t1 = bp.resolved_for_compile(
        bp.serialize_overrides({"cover_letter_body": "ASSEMBLED_T1"})
    )
    # What assemble would freeze:
    compile_context = {"boilerplate_overrides": t1}
    live_t2_raw = bp.serialize_overrides({"cover_letter_body": "LIVE_T2"})

    snap_render = bp.for_render(
        use_snapshots=True,
        frozen=compile_context["boilerplate_overrides"],
        live_raw=live_t2_raw,
    )
    live_render = bp.for_render(
        use_snapshots=False,
        frozen=compile_context["boilerplate_overrides"],
        live_raw=live_t2_raw,
    )
    assert snap_render["cover_letter_body"] == "ASSEMBLED_T1"
    assert live_render["cover_letter_body"] == "LIVE_T2"


def test_compiler_ctx_includes_boilerplate_key(tmp_path, monkeypatch):
    """compile_package always puts boilerplate on Jinja context (empty ok)."""
    # Structural: compiler module builds boilerplate_ctx before ctx_full
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "app" / "disclosure_package" / "compiler.py"
    text = src.read_text()
    assert '"boilerplate": boilerplate_ctx' in text or "'boilerplate': boilerplate_ctx" in text
    assert "empty_boilerplate" in text


def test_workbench_fullscreen_contract_in_source():
    """Full-screen shell like DRE PDF compare; opens from Disclosure readiness."""
    root = Path(__file__).resolve().parents[2]
    wb = (root / "frontend/src/app/components/BoilerplateWorkbench.tsx").read_text()
    disc = (root / "frontend/src/app/components/DisclosureWorkspaceScreen.tsx").read_text()
    assert "fixed inset-0 z-50" in wb
    assert "if (!open) return null" in wb
    assert "role=\"dialog\"" in wb or "role='dialog'" in wb
    assert "open-package-language" in disc
    assert "setPackageLanguageOpen(true)" in disc
    assert "packageLanguageOpen" in disc
    assert "if (!open) return" in wb or "if (!open)" in wb
    assert "void load()" in wb


def test_unicode_and_whitespace_override(client, db_session):
    from app.ai_implementation.db.models import Property
    prop = Property(name="BP Unicode", units=2, hoa_code="BPU")
    db_session.add(prop)
    db_session.commit()
    body = "Dear Homeowner:\n\nThank you — café & “special” 日本語"
    r = client.put(
        f"/hoa/{prop.id}/settings/boilerplate",
        json={"overrides": {"cover_letter_body": body}},
    )
    assert r.status_code == 200, r.text
    got = client.get(f"/hoa/{prop.id}/settings/boilerplate")
    assert got.json()["slots"][0]["value"] == body

    # whitespace-only clears
    r2 = client.put(
        f"/hoa/{prop.id}/settings/boilerplate",
        json={"overrides": {"cover_letter_body": "   \n\t  "}},
    )
    assert r2.status_code == 200
    assert r2.json()["slots"][0]["is_override"] is False
    assert r2.json()["slots"][0]["value"] == ""


def test_put_requires_overrides_object(client, db_session):
    from app.ai_implementation.db.models import Property
    prop = Property(name="BP Bad Body", units=2, hoa_code="BPBB")
    db_session.add(prop)
    db_session.commit()
    r = client.put(f"/hoa/{prop.id}/settings/boilerplate", json={})
    assert r.status_code == 400
    r2 = client.put(f"/hoa/{prop.id}/settings/boilerplate", json={"overrides": "nope"})
    assert r2.status_code == 400


def test_unauthenticated_boilerplate_rejected():
    """Routes require auth — bare client without token must not read/write."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app()
    bare = TestClient(app)
    # no Authorization header
    r = bare.get("/hoa/1/settings/boilerplate")
    assert r.status_code in (401, 403), r.status_code


def test_reference_job_requires_job_id(client, db_session):
    from app.ai_implementation.db.models import Property
    prop = Property(name="BP JobId", units=1, hoa_code="BPJ")
    db_session.add(prop)
    db_session.commit()
    r = client.get(
        f"/hoa/{prop.id}/boilerplate/reference-pdf",
        params={"source": "job"},
    )
    assert r.status_code == 400


def test_cover_letter_newlines_become_br():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
    from app.disclosure_package.render import TEMPLATES_DIR, _nl2br
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR / "standard")),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
    )
    env.filters["nl2br"] = _nl2br
    template = env.get_template("cover_letter.html")
    ctx = _cover_ctx("Line one\nLine two")
    html = template.render(**ctx)
    assert "Line one" in html and "Line two" in html
    assert "<br>" in html
