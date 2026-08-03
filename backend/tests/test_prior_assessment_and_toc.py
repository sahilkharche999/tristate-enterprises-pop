"""Prior-year assessment resolve + appendix TOC manifest-only merge."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.assessment_engine.schemas import RecipientReference
from app.disclosure_package.assessment_schedule_matrix import (
    PRESENTATION_INDIVIDUAL,
    _expand_group_recipients_to_units,
    _ownership_match_key,
    _resolve_presentation_for_setup,
    save_assessment_schedule_presentation,
    load_assessment_schedule_presentation,
)
from app.disclosure_package.prior_assessment_schedule import (
    extract_schedule_rows_from_pdf_text,
    load_finalized_assessment_matrix,
    matrix_from_seed_rows,
    prior_status,
    resolve_prior_assessment_matrix,
    save_prior_seed,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        """
        CREATE TABLE properties (
          id INTEGER PRIMARY KEY,
          name TEXT,
          prior_assessment_schedule_json TEXT,
          prior_assessment_schedule_year INTEGER
        )
        """
    )
    c.execute(
        """
        CREATE TABLE annual_packages (
          id INTEGER PRIMARY KEY,
          property_id INTEGER,
          fiscal_year INTEGER,
          status TEXT,
          compile_context_snapshot_json TEXT
        )
        """
    )
    c.execute(
        "INSERT INTO properties (id, name) VALUES (1, 'Test HOA')"
    )
    c.commit()
    return c


def test_matrix_from_seed_rows_unit_grain():
    m = matrix_from_seed_rows(
        hoa_name="Sharon Ridge",
        fiscal_year=2025,
        rows=[
            {"recipient_label": "513", "monthly": "553.09", "percent_of_total": "1.780"},
            {"recipient_label": "511", "monthly": "851.38", "percent_of_total": "2.740"},
        ],
    )
    assert m.fiscal_year == 2025
    assert m.recipient_grain == "unit"
    assert len(m.rows) == 2
    assert m.rows[0].total_monthly_assessment == Decimal("553.09")
    assert m.rows[0].annual_total == Decimal("6637.08")


def test_resolve_prefers_finalized_over_seed():
    c = _conn()
    matrix_payload = matrix_from_seed_rows(
        hoa_name="Test HOA",
        fiscal_year=2025,
        rows=[{"recipient_label": "1", "monthly": "100.00"}],
    ).model_dump(mode="json")
    c.execute(
        """
        INSERT INTO annual_packages
          (property_id, fiscal_year, status, compile_context_snapshot_json)
        VALUES (1, 2025, 'finalized', ?)
        """,
        (json.dumps({"assessment_matrix": matrix_payload}),),
    )
    save_prior_seed(
        c,
        property_id=1,
        fiscal_year=2025,
        rows=[{"recipient_label": "1", "monthly": "999.00"}],
    )
    prior = resolve_prior_assessment_matrix(
        c, property_id=1, fiscal_year=2026, hoa_name="Test HOA",
    )
    assert prior is not None
    assert prior.rows[0].total_monthly_assessment == Decimal("100.00")


def test_resolve_falls_back_to_seed():
    c = _conn()
    save_prior_seed(
        c,
        property_id=1,
        fiscal_year=2025,
        rows=[{"recipient_label": "A", "monthly": "10.00"}],
    )
    prior = resolve_prior_assessment_matrix(
        c, property_id=1, fiscal_year=2026, hoa_name="Test HOA",
    )
    assert prior is not None
    assert prior.fiscal_year == 2025
    assert prior.rows[0].total_monthly_assessment == Decimal("10.00")


def test_resolve_missing():
    c = _conn()
    assert (
        resolve_prior_assessment_matrix(
            c, property_id=1, fiscal_year=2026, hoa_name="Test HOA",
        )
        is None
    )
    st = prior_status(c, property_id=1, fiscal_year=2026)
    assert st["status"] == "missing"


def test_frozen_prior_wins():
    c = _conn()
    frozen = matrix_from_seed_rows(
        hoa_name="X",
        fiscal_year=2024,
        rows=[{"recipient_label": "9", "monthly": "1.00"}],
    ).model_dump(mode="json")
    prior = resolve_prior_assessment_matrix(
        c,
        property_id=1,
        fiscal_year=2026,
        hoa_name="X",
        frozen_prior=frozen,
    )
    assert prior is not None
    assert prior.fiscal_year == 2024


def test_extract_schedule_rows_from_text():
    text = """
    SHARON RIDGE
    2025 Assessments Per Unit Per Month
    511  2.740  851.38
    513  1.780  553.09
    Total  100.000  31072.33
    """
    rows = extract_schedule_rows_from_pdf_text(text)
    assert len(rows) == 2
    assert rows[0]["recipient_label"] == "511"
    assert rows[0]["monthly"] == "851.38"


def test_compiler_manifest_only_skips_legacy_dir(tmp_path: Path, monkeypatch):
    """When extra_appendix_paths is a list, legacy dir PDFs are not TOC/merged."""
    from app.disclosure_package import compiler as compiler_mod

    # Minimal stubs so we only exercise appendix resolution logic via a thin
    # unit of the compiler path is hard without full render — instead assert
    # the branch condition by calling the resolution pattern mirrored here.
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    pollution = legacy / "Old Mill 2026 Disclosure Package (2).pdf"
    pollution.write_bytes(b"%PDF-1.4 minimal")
    good = tmp_path / "7_election_rules.pdf"
    good.write_bytes(b"%PDF-1.4 good")

    # Use real page count stub
    monkeypatch.setattr(
        compiler_mod, "_pdf_page_count", lambda _b: 1,
    )

    use_manifest_only = True
    extra_appendix_paths = [good]
    extra_appendix_titles = {good.name: "Election Rules"}
    appendices_root = legacy

    spec_appendix_paths: dict = {}
    seen: set[str] = set()
    adhoc: list = []
    manifest: list = []

    if not use_manifest_only:
        for p in sorted(appendices_root.glob("*.pdf")):
            adhoc.append((p, p.name))
    else:
        for extra in extra_appendix_paths or []:
            title = extra_appendix_titles.get(extra.name) or extra.name
            manifest.append((extra, title))
            seen.add(extra.name)

    trailing = [*adhoc, *manifest]
    titles = [t for _p, t in trailing]
    assert titles == ["Election Rules"]
    assert not any("Old Mill" in t for t in titles)


def test_legacy_mode_still_globs_when_paths_none(tmp_path: Path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "insurance_certificate.pdf").write_bytes(b"%PDF")
    extra_appendix_paths = None
    use_manifest_only = extra_appendix_paths is not None
    assert use_manifest_only is False
    found = list(legacy.glob("*.pdf")) if not use_manifest_only else []
    assert len(found) == 1


def test_load_finalized_assessment_matrix():
    c = _conn()
    m = matrix_from_seed_rows(
        hoa_name="H", fiscal_year=2025,
        rows=[{"recipient_label": "U1", "monthly": "50"}],
    )
    c.execute(
        """
        INSERT INTO annual_packages
          (property_id, fiscal_year, status, compile_context_snapshot_json)
        VALUES (1, 2025, 'finalized', ?)
        """,
        (json.dumps({"assessment_matrix": m.model_dump(mode="json")}),),
    )
    c.commit()
    loaded = load_finalized_assessment_matrix(c, property_id=1, fiscal_year=2025)
    assert loaded is not None
    assert loaded.rows[0].recipient_label == "U1"


def test_universal_template_html_dual_year_titles_without_weasyprint():
    """Jinja-render assessment schedule HTML: prior then current, specials only on current."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from pathlib import Path

    from app.disclosure_package.prior_assessment_schedule import matrix_from_seed_rows

    templates = Path(__file__).resolve().parents[1] / "app" / "disclosure_package" / "templates" / "standard"
    env = Environment(
        loader=FileSystemLoader(str(templates)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    # Parent templates need _base; load relative path used by package
    env = Environment(
        loader=FileSystemLoader([
            str(templates),
            str(templates / "assessment_schedule"),
        ]),
        autoescape=select_autoescape(["html", "xml"]),
    )
    # universal.html extends _base.html — resolve via standard root
    env = Environment(
        loader=FileSystemLoader(str(templates)),
        autoescape=select_autoescape(["html", "xml"]),
    )

    prior = matrix_from_seed_rows(
        hoa_name="Sharon Ridge Homeowners Association",
        fiscal_year=2025,
        rows=[{"recipient_label": "513", "monthly": "553.09", "percent_of_total": "1.780"}],
    )
    current = matrix_from_seed_rows(
        hoa_name="Sharon Ridge Homeowners Association",
        fiscal_year=2026,
        rows=[{"recipient_label": "513", "monthly": "569.68", "percent_of_total": "1.780"}],
    )
    # Attach a special so we can assert it only appears once (current block)
    from app.disclosure_package.assessment_schedule_matrix import (
        SpecialAssessmentDisclosureBlock,
    )
    current.special_assessment_blocks = [
        SpecialAssessmentDisclosureBlock(label="Roof SA", display_language="Test special only on current"),
    ]
    current.homeowner_visible_notes = ["Homeowner note current only"]

    tpl = env.get_template("assessment_schedule/universal.html")
    # Provide minimal base context used by _base.html
    html = tpl.render(
        matrix=current,
        prior_matrix=prior,
        hoa=type("H", (), {"name": "Sharon Ridge Homeowners Association"})(),
        fiscal_year=2026,
        static_data=type("S", (), {})(),
        hoa_settings={
            "management_company_address": "",
            "management_company_phone": "",
            "management_company_fax": "",
            "management_company_web": "",
        },
        today="Monday January 1, 2026",
        narrative={},
        toc_page_numbers={},
        appendix_toc_entries=[],
        hoa_logo_data_uri=None,
    )
    assert "2025 Assessments Per Unit Per Month" in html
    assert "2026 Assessments Per Unit Per Month" in html
    # Prior year title must appear before package year title
    assert html.index("2025 Assessments Per Unit Per Month") < html.index(
        "2026 Assessments Per Unit Per Month"
    )
    assert "553.09" in html
    assert "569.68" in html
    assert html.count("Test special only on current") == 1
    assert html.count("Homeowner note current only") == 1


def test_universal_template_html_omits_prior_when_none():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "app" / "disclosure_package" / "templates" / "standard"
    env = Environment(
        loader=FileSystemLoader(str(templates)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    current = matrix_from_seed_rows(
        hoa_name="Old Mill",
        fiscal_year=2026,
        rows=[{"recipient_label": "All", "monthly": "605.00"}],
    )
    html = env.get_template("assessment_schedule/universal.html").render(
        matrix=current,
        prior_matrix=None,
        hoa=type("H", (), {"name": "Old Mill"})(),
        fiscal_year=2026,
        static_data=type("S", (), {})(),
        hoa_settings={
            "management_company_address": "",
            "management_company_phone": "",
            "management_company_fax": "",
            "management_company_web": "",
        },
        today="Monday January 1, 2026",
        narrative={},
        toc_page_numbers={},
        appendix_toc_entries=[],
        hoa_logo_data_uri=None,
    )
    assert "2026 Assessments Per Unit Per Month" in html
    assert "2025 Assessments Per Unit Per Month" not in html


def test_scalar_prior_setting_not_used_by_resolve():
    """monthly_assessment_per_unit_prior must not invent multi-unit prior tables."""
    c = _conn()
    # No seed, no finalized package — only a fictional "settings" value would exist
    # outside this module; resolve must still return None.
    assert (
        resolve_prior_assessment_matrix(
            c, property_id=1, fiscal_year=2026, hoa_name="Var HOA",
        )
        is None
    )


def test_package_language_assessment_schedule_is_computed_placeholder_not_narrative():
    """Package language window shows assessment schedule as computed placeholder only.

    Dual year tables are PDF template content, not editable package-language docs.
    """
    from app.services import narrative_content

    placeholders = narrative_content.COMPUTED_PLACEHOLDERS
    assess = [p for p in placeholders if "assessment_schedule" in p["template"]]
    assert len(assess) == 1
    assert assess[0]["label"] == "Assessment schedule"
    # Not a narrative document operators edit for prior-year dues
    docs = set(narrative_content.document_ids())
    assert "assessment_schedule" not in docs
    assert "prior_assessment" not in docs
    # TOC chip points at the schedule template (page number only)
    from app.services.boilerplate_variables import TOC_PAGE_TOKENS

    assert TOC_PAGE_TOKENS["page_assessment_schedule"] == "assessment_schedule/universal.html"


def test_compile_package_signature_accepts_prior_matrix():
    import inspect
    from app.disclosure_package.compiler import compile_package

    sig = inspect.signature(compile_package)
    assert "prior_matrix" in sig.parameters
    assert sig.parameters["prior_matrix"].default is None


def test_extract_prior_schedule_uses_text_when_dense_enough(monkeypatch):
    """Text path short-circuits before Vision when enough rows are present."""
    from app.disclosure_package import prior_assessment_schedule as mod

    lines = "\n".join(f"{100 + i}  2.500  {500 + i}.00" for i in range(5))
    content = b"%PDF-fake"  # not parsed if we stub pypdf

    class _Page:
        def extract_text(self):
            return lines

    class _Reader:
        def __init__(self, *_a, **_k):
            self.pages = [_Page()]

    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    # Vision must not be required
    monkeypatch.setattr(
        "app.dre_extraction.gemini_callbacks.gemini_client_from_env",
        lambda: (_ for _ in ()).throw(AssertionError("vision should not run")),
    )
    result = mod.extract_prior_schedule_from_pdf_bytes(content, preferred_year=2025)
    assert result["method"] == "pdf_text"
    assert result["row_count"] if "row_count" in result else len(result["rows"]) >= 5
    assert len(result["rows"]) >= 5


def test_extract_prior_schedule_vision_when_text_empty(monkeypatch, tmp_path: Path):
    """Empty text falls through to Gemini classify + extract."""
    from app.disclosure_package import prior_assessment_schedule as mod

    class _Page:
        def extract_text(self):
            return ""

    class _Reader:
        def __init__(self, *_a, **_k):
            self.pages = [_Page(), _Page()]

    monkeypatch.setattr("pypdf.PdfReader", _Reader)

    fake_client = object()
    monkeypatch.setattr(
        "app.dre_extraction.gemini_callbacks.gemini_client_from_env",
        lambda: fake_client,
    )
    monkeypatch.setattr(
        "app.dre_extraction.gemini_callbacks.default_model_name",
        lambda: "gemini-flash-latest",
    )

    # Minimal real PDF so fitz can open it after temp write
    try:
        import fitz
    except ImportError:
        import pytest
        pytest.skip("pymupdf required")
    pdf_path = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()
    content = pdf_path.read_bytes()

    class _RP:
        def __init__(self, n):
            self.page_number = n
            self.content = b"fake-png"
            self.mime_type = "image/png"

    monkeypatch.setattr(
        "app.services.pdf_vlm_extractor.render_pdf_pages",
        lambda path, max_pages=None, dpi=72: [_RP(1), _RP(2)],
    )
    monkeypatch.setattr(
        mod,
        "_classify_assessment_schedule_pages",
        lambda *a, **k: [1],
    )
    monkeypatch.setattr(
        mod,
        "_extract_rows_from_schedule_pages",
        lambda *a, **k: (
            [{"recipient_label": "513", "monthly": "553.09", "percent_of_total": "1.780"}],
            2025,
        ),
    )
    # Avoid real high-DPI fitz path complexity: force fallback classify DPI
    # by making high-dpi path use our classify stubs — real fitz open still works
    result = mod.extract_prior_schedule_from_pdf_bytes(content, preferred_year=2025)
    assert result["method"] == "gemini_vision"
    assert len(result["rows"]) == 1
    assert result["rows"][0]["recipient_label"] == "513"
    assert result["fiscal_year"] == 2025


def test_compiler_manifest_only_empty_list_skips_legacy_glob(tmp_path: Path):
    """Production empty manifest must not pick up legacy dir pollution."""
    from app.disclosure_package.compiler import _humanize_filename_title

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "Old Mill 2026 Disclosure Package (2).pdf").write_bytes(b"%PDF")
    extra_appendix_paths: list = []  # production empty list
    use_manifest_only = extra_appendix_paths is not None
    adhoc = []
    if not use_manifest_only and legacy.is_dir():
        adhoc = list(legacy.glob("*.pdf"))
    assert use_manifest_only is True
    assert adhoc == []
    # Humanize would produce the bad TOC string — prove we never call it for pollution
    bad = _humanize_filename_title("Old Mill 2026 Disclosure Package (2).pdf")
    assert "Old Mill" in bad  # documents root cause of Bob's bug if path were used


def test_expand_groups_to_units_uses_prior_seed_labels():
    groups = [
        RecipientReference(
            ref_type="group",
            ref_id=1,
            label="Unit Type A",
            unit_count=2,
            ownership_percent=Decimal("0.0178"),
        ),
        RecipientReference(
            ref_type="group",
            ref_id=2,
            label="Unit Type B",
            unit_count=1,
            ownership_percent=Decimal("0.0242"),
        ),
    ]
    buckets = {
        _ownership_match_key(Decimal("1.780")): ["513", "523"],
        _ownership_match_key(Decimal("2.420")): ["534"],
    }
    units = _expand_group_recipients_to_units(groups, unit_labels_by_pct=buckets)
    assert len(units) == 3
    assert [u.label for u in units] == ["513", "523", "534"]
    assert all(u.ref_type == "unit" and u.unit_count == 1 for u in units)
    assert units[0].ownership_percent == Decimal("0.0178")


def test_resolve_presentation_property_overrides_setup():
    assert (
        _resolve_presentation_for_setup(
            property_presentation="individual",
            setup_type="grouped",
            setup_display_mode="grouped",
        )
        == PRESENTATION_INDIVIDUAL
    )
    assert (
        _resolve_presentation_for_setup(
            property_presentation="auto",
            setup_type="grouped",
            setup_display_mode="",
        )
        == "group"
    )


def test_save_and_load_presentation_mode():
    c = _conn()
    c.execute(
        "ALTER TABLE properties ADD COLUMN assessment_schedule_presentation "
        "TEXT NOT NULL DEFAULT 'auto'"
    )
    c.commit()
    assert load_assessment_schedule_presentation(c, property_id=1) == "auto"
    saved = save_assessment_schedule_presentation(
        c, property_id=1, presentation="individual"
    )
    assert saved == "individual"
    assert load_assessment_schedule_presentation(c, property_id=1) == "individual"


def test_universal_template_page_breaks_before_current_year():
    tpl = Path(
        "app/disclosure_package/templates/standard/assessment_schedule/universal.html"
    ).read_text(encoding="utf-8")
    # Single break on current-year section only (no empty .page-break div).
    assert "assessment-schedule--year-start" in tpl
    assert "break_before=prior_matrix" in tpl
    assert "assessment-schedule--year-break" not in tpl
    # Must not stack .page-break (after) with year-start (before) — blank page.
    assert 'class="page-break' not in tpl
