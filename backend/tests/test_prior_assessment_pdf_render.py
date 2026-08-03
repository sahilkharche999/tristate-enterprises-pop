"""WeasyPrint PDF smoke tests for dual prior/current assessment tables.

Skipped automatically when WeasyPrint system libs are unavailable (e.g. bare macOS).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

fitz = pytest.importorskip("fitz")

from app.disclosure_package.assessment_schedule_matrix import SpecialAssessmentDisclosureBlock
from app.disclosure_package.package_specs import STANDARD_PACKAGE_SPEC
from app.disclosure_package.prior_assessment_schedule import matrix_from_seed_rows
from app.disclosure_package.render import render_template


def _weasyprint_available() -> bool:
    try:
        from weasyprint import HTML  # noqa: F401
        # Force native lib load
        HTML(string="<html><body>ok</body></html>").write_pdf()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _weasyprint_available(),
    reason="WeasyPrint system libraries not available on this host",
)


def _ctx(matrix, prior=None):
    return {
        "matrix": matrix,
        "prior_matrix": prior,
        "hoa": SimpleNamespace(name=matrix.hoa["name"]),
        "fiscal_year": matrix.fiscal_year,
        "static_data": STANDARD_PACKAGE_SPEC.static_data,
        "hoa_settings": {
            "management_company_address": "",
            "management_company_phone": "",
            "management_company_fax": "",
            "management_company_web": "",
        },
        "toc_page_numbers": {},
        "appendix_toc_entries": [],
        "today": "Monday January 1, 2026",
        "narrative": {},
        "hoa_logo_data_uri": None,
    }


def test_pdf_contains_both_year_titles_prior_before_current():
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
    current.special_assessment_blocks = [
        SpecialAssessmentDisclosureBlock(
            label="Roof", display_language="SPECIAL_ONLY_CURRENT"
        )
    ]
    pdf = render_template(
        template_name="assessment_schedule/universal.html",
        context=_ctx(current, prior),
    )
    doc = fitz.open(stream=pdf, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    assert "2025 Assessments Per Unit Per Month" in text
    assert "2026 Assessments Per Unit Per Month" in text
    assert text.index("2025 Assessments Per Unit Per Month") < text.index(
        "2026 Assessments Per Unit Per Month"
    )
    assert "553.09" in text.replace(",", "")
    assert "569.68" in text.replace(",", "")
    assert text.count("SPECIAL_ONLY_CURRENT") == 1


def test_pdf_omits_prior_when_absent():
    current = matrix_from_seed_rows(
        hoa_name="Old Mill",
        fiscal_year=2026,
        rows=[{"recipient_label": "All", "monthly": "605.00"}],
    )
    pdf = render_template(
        template_name="assessment_schedule/universal.html",
        context=_ctx(current, None),
    )
    doc = fitz.open(stream=pdf, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    assert "2026" in text
    assert "2025 Assessments Per Unit Per Month" not in text
