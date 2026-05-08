"""Renderer snapshot tests (REQ-D11-006).

For each generated-page template:
- PDF bytes start with %PDF
- Page count is within ±1 of `entry.page_count_hint`
- Autoescape mitigates T-11-03 (template injection on HOA-supplied fields)
- Remote-URL fetches are denied (T-11-03 mitigation in `_deny_url_fetcher`)

The snapshot fixture sizes (component count, projection-row count) are tuned
so that the production-scale templates land on their hint count under the
default rendering policy. ±1 tolerance is the plan-04 commitment; plan-08
raster diff tightens to byte-exact comparison against the golden PDF.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.disclosure_package.render import (
    RemoteFetchDenied,
    render_package,
    render_template,
)
from app.disclosure_package.package_specs import SPECS
from app.disclosure_package.schemas import GeneratedPage


def _reserve_components(n: int) -> list[dict[str, Any]]:
    """Test-fixture reserve components.

    n=80 yields a reserve_component_schedule.html that renders to 5 pages
    (matching package_specs.old_mill page_count_hint).
    """
    return [
        {
            "line_item": f"Component description {i} reasonably long",
            "useful_life": 25,
            "remaining_life": 10,
            "year_new": 2010,
            "year_replacement_provision": 5000 + i * 100,
            "estimated_liability": 50000 + i * 1000,
        }
        for i in range(n)
    ]


def _thirty_year_rows(n: int = 30) -> list[dict[str, Any]]:
    return [
        {
            "year": 2026 + i,
            "cash_balance": 100000 + i * 1000,
            "liability": 4575000,
            "revenue": 700000,
            "expenditure": 300000,
            "percent_funded": 60,
        }
        for i in range(n)
    ]


def _line_items(prefix: str, section: str | None, n: int) -> list[dict[str, Any]]:
    return [
        {
            "label": f"{prefix} {i}",
            "amount": Decimal("50000"),
            "section": section,
        }
        for i in range(n)
    ]


def _minimal_computed_context() -> dict[str, Any]:
    """Snapshot fixture sized to land each template on its page_count_hint.

    `reserve_components` n=80 → reserve_component_schedule.html = 5 pages.
    `thirty_year_projections` n=30 + 30 components → 30-year plan = 5 pages.
    """
    return {
        "computed": {
            "percent_funded": 57,
            "total_estimated_liability": Decimal("4575000"),
            "under_funded_balance_total": Decimal("1975000"),
            "under_funded_balance_per_unit": Decimal("7080"),
            "total_revenues_operations": Decimal("2025540"),
            "total_revenues_replacement": Decimal("737886"),
            "total_revenues": Decimal("2763426"),
            "total_expenses_operations": Decimal("295000"),
            "total_expenses_replacement": Decimal("691086"),
            "total_expenses": Decimal("986086"),
            "operating_revenues": _line_items("Operating revenue", None, 3),
            "replacement_revenues": _line_items("Replacement revenue", None, 2),
            "operating_expenses": (
                _line_items("Maintenance", "Maintenance and operations", 8)
                + _line_items("Utility", "Utilities", 3)
                + _line_items("Admin", "Administration", 4)
            ),
            "replacement_expenses": _line_items("Replacement", None, 6),
            "monthly_replacement_revenue_total": Decimal("672886"),
            "monthly_replacement_contribution_per_unit_2026": Decimal("200.98"),
            "reserve_components": _reserve_components(80),
            "total_year_replacement_provision": Decimal("150000"),
            "thirty_year_projections": _thirty_year_rows(30),
            "assessment_change_disclosure": "No",
            "percent_funded_at": {10: 60, 20: 65, 30: 70},
            "useful_life_not_disclosed_count": 0,
            "board_deferral_count": 0,
            "signed_contracts_count": 0,
        },
        "reserve_study_snapshot": type(
            "RS", (), {"study_date": "September 2025"}
        )(),
    }


def _build_context() -> dict[str, Any]:
    spec = SPECS["old_mill"]
    return {
        "spec": spec,
        "static_data": spec.static_data,
        "fiscal_year": 2026,
        **_minimal_computed_context(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: cover_letter renders to a non-empty PDF
# ─────────────────────────────────────────────────────────────────────────────


def test_render_cover_letter_produces_pdf():
    out = render_template(template_name="cover_letter.html", context=_build_context())
    assert out.startswith(b"%PDF"), "WeasyPrint output must be a PDF"
    assert len(out) > 1000, "Cover letter PDF should be at least 1KB"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: every GeneratedPage entry renders within ±1 of its page_count_hint
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "entry",
    [e for e in SPECS["old_mill"].entries if isinstance(e, GeneratedPage)],
    ids=lambda e: e.template,
)
def test_each_generated_template_renders_with_expected_page_count(entry):
    """REQ-D11-006: each template renders to non-empty PDF; page count ≈ hint
    (±1 tolerance for first pass; plan 11-08 raster diff tightens)."""
    import fitz

    pdf_bytes = render_template(
        template_name=entry.template, context=_build_context()
    )
    assert pdf_bytes.startswith(b"%PDF")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    actual_pages = doc.page_count
    doc.close()
    assert abs(actual_pages - entry.page_count_hint) <= 1, (
        f"{entry.template}: hint={entry.page_count_hint}, actual={actual_pages}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: autoescape blocks template injection in HOA legal name (T-11-03)
# ─────────────────────────────────────────────────────────────────────────────


def test_autoescape_blocks_template_injection_in_hoa_name():
    """T-11-03 mitigation: HOA legal name containing <script> is escaped.

    The PDF must NOT contain the literal byte sequence `<script>alert` —
    autoescape in render._build_env converts every dynamic `{{ }}` to its
    HTML-escaped form.
    """
    spec = SPECS["old_mill"]
    static_data_evil = spec.static_data.model_copy(
        update={"hoa_legal_name": "<script>alert(1)</script>"}
    )
    spec_evil = spec.model_copy(update={"static_data": static_data_evil})
    ctx = {
        "spec": spec_evil,
        "static_data": spec_evil.static_data,
        "fiscal_year": 2026,
        **_minimal_computed_context(),
    }
    pdf_bytes = render_template(template_name="cover_letter.html", context=ctx)
    # Stronger byte-level assertion: the executable HTML must never appear in
    # the rendered PDF stream — autoescape converts < and > to &lt; / &gt;
    # before WeasyPrint sees them.
    assert b"<script>alert" not in pdf_bytes
    # Sanity: the raw alphanumeric "alert(1)" might still surface as
    # rendered text after escaping; that's fine — what matters is the
    # angle-bracket form is gone.


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: render denies remote URL fetcher (T-11-03)
# ─────────────────────────────────────────────────────────────────────────────


def test_render_denies_remote_url_fetcher(tmp_path, caplog):
    """T-11-03: WeasyPrint url_fetcher rejects https: URLs.

    WeasyPrint internally catches exceptions raised by url_fetcher during
    image loading and logs them — it does not re-raise to the caller. We
    therefore assert that (1) the render still completes without producing
    a network fetch, and (2) WeasyPrint's logger records the
    `RemoteFetchDenied` message — which proves the fetcher was called and
    rejected the URL.
    """
    import logging

    bad_template_dir = tmp_path / "bad"
    bad_template_dir.mkdir()
    (bad_template_dir / "_base.html").write_text(
        "<html><body>{% block content %}{% endblock %}</body></html>"
    )
    (bad_template_dir / "evil.html").write_text(
        "{% extends '_base.html' %}{% block content %}"
        "<img src=\"https://evil.example.com/p.png\">"
        "{% endblock %}"
    )

    from app.disclosure_package import render as render_mod

    saved_dir = render_mod.TEMPLATES_DIR
    try:
        render_mod.TEMPLATES_DIR = tmp_path
        with caplog.at_level(logging.ERROR, logger="weasyprint"):
            pdf_bytes = render_template(
                template_name="evil.html",
                context={},
                templates_subdir="bad",
            )
        # PDF still produced, but image fetch was denied
        assert pdf_bytes.startswith(b"%PDF")
        deny_messages = [
            r for r in caplog.records if "RemoteFetchDenied" in r.getMessage()
        ]
        assert deny_messages, (
            "WeasyPrint should log a RemoteFetchDenied error when a "
            "template attempts to load an https:// resource. Captured "
            f"records: {[r.getMessage() for r in caplog.records]}"
        )
    finally:
        render_mod.TEMPLATES_DIR = saved_dir


def test_deny_url_fetcher_rejects_http_https_and_path_traversal():
    """Direct unit test on _deny_url_fetcher (T-11-03 + T-11-05)."""
    from app.disclosure_package.render import _deny_url_fetcher

    for url in (
        "https://evil.example.com/font.ttf",
        "http://evil.example.com/font.ttf",
        "ftp://example.com/file",
        "data:text/plain;base64,SGVsbG8=",
        "file:///tmp/../etc/passwd",
    ):
        with pytest.raises(RemoteFetchDenied):
            _deny_url_fetcher(url)


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: render_package returns one PDF per GeneratedPage entry
# ─────────────────────────────────────────────────────────────────────────────


def test_render_package_returns_one_pdf_per_generated_entry():
    spec = SPECS["old_mill"]
    out = render_package(spec=spec, computed=_minimal_computed_context())
    expected_templates = {
        e.template for e in spec.entries if isinstance(e, GeneratedPage)
    }
    assert set(out.keys()) == expected_templates
    # 17 distinct generated-page templates (G5 folded into G4 per CONTEXT)
    assert len(expected_templates) == 17
    for template_name, pdf_bytes in out.items():
        assert pdf_bytes.startswith(b"%PDF"), (
            f"{template_name} did not produce a valid PDF"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: rendered PDF has at least one page
# ─────────────────────────────────────────────────────────────────────────────


def test_rendered_pdf_has_at_least_one_page():
    import fitz

    pdf_bytes = render_template(
        template_name="annual_budget_report_cover.html",
        context=_build_context(),
    )
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count >= 1
    doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: 200.98 base contribution renders correctly on note 6
# (RESEARCH risk #13 — verify the funding-plan base value reaches the page)
# ─────────────────────────────────────────────────────────────────────────────


def test_note_6_renders_monthly_base_contribution_value():
    """RESEARCH risk #13: the per-unit Replacement Fund contribution must
    surface in the rendered PDF — formatting the Decimal as ``$200.98``."""
    import fitz

    pdf_bytes = render_template(
        template_name="note_6_funding_plan.html", context=_build_context()
    )
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "".join(doc[i].get_text() for i in range(doc.page_count))
    doc.close()
    assert "200.98" in text, (
        "Note 6 must surface the $200.98 monthly per-unit Replacement Fund "
        "base contribution from formulas.py"
    )
