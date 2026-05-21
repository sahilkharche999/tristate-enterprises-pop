"""Pure-unit tests for ``app.disclosure_package.verify.raster_diff``.

These tests manufacture synthetic PDFs with PyMuPDF and exercise the
comparator in isolation — no golden PDF, no compile_package, no
fixture dependencies. The earlier golden-PDF parity tests (Phase 11
``test_raster_diff_each_generated_page`` / ``test_final_pdf_passes_qpdf_check``
/ ``test_audit_log_contains_every_formula_call``) were removed when the
client confirmed the original 2026 golden PDF was wrong and the
DRE-driven architecture replaced the per-HOA spec literal with a
universal template chain.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.disclosure_package.verify import (
    BYTE_TOL,
    DEFAULT_TOLERANCE,
    PageDivergence,
    RasterDiffResult,
    raster_diff,
)


# ─────────────────────────────────────────────────────────────────────────────
# Pure-unit tests for verify.raster_diff (no golden / no compile_package)
# ─────────────────────────────────────────────────────────────────────────────


def _make_text_pdf(path: Path, lines: list[str], page_count: int = 1) -> None:
    """Manufacture a tiny text PDF with PyMuPDF for unit tests."""
    import fitz

    doc = fitz.open()
    for _ in range(page_count):
        page = doc.new_page(width=612, height=792)  # Letter portrait
        y = 100
        for line in lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 20
    doc.save(str(path))
    doc.close()


def test_raster_diff_identical_pdfs_pass(tmp_path):
    """Two identical PDFs raster-diff to ~0 divergence — far below 1% tolerance."""
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    lines = ["Old Mill Homeowners Association", "Disclosure Package", "FY 2026"]
    _make_text_pdf(pdf_a, lines, page_count=1)
    _make_text_pdf(pdf_b, lines, page_count=1)

    result = raster_diff(
        system_pdf=pdf_a,
        golden_pdf=pdf_b,
        page_numbers=[1],
        tolerance=DEFAULT_TOLERANCE,
    )
    assert isinstance(result, RasterDiffResult)
    assert result.overall_pass is True
    assert len(result.pages) == 1
    assert result.pages[0].page_number == 1
    # Identical inputs should produce ~0 divergence (well under 1%)
    assert result.pages[0].divergence < 0.01


def test_raster_diff_different_pdfs_fail(tmp_path):
    """Visibly-different PDFs exceed 1% divergence."""
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    _make_text_pdf(
        pdf_a,
        ["Old Mill Homeowners Association", "Disclosure Package", "FY 2026"],
        page_count=1,
    )
    # Completely different content — fills the upper half of the page
    # densely enough to easily exceed 1% pixel divergence.
    _make_text_pdf(
        pdf_b,
        [
            "DIFFERENT CONTENT " * 5,
            "DIFFERENT CONTENT " * 5,
            "DIFFERENT CONTENT " * 5,
            "DIFFERENT CONTENT " * 5,
            "DIFFERENT CONTENT " * 5,
        ],
        page_count=1,
    )
    result = raster_diff(
        system_pdf=pdf_a,
        golden_pdf=pdf_b,
        page_numbers=[1],
        tolerance=DEFAULT_TOLERANCE,
    )
    assert result.overall_pass is False
    assert result.pages[0].divergence > DEFAULT_TOLERANCE


def test_raster_diff_writes_debug_pngs(tmp_path):
    """When ``output_dir`` is set, system + golden pixmaps are written as PNGs."""
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    _make_text_pdf(pdf_a, ["A"], 1)
    _make_text_pdf(pdf_b, ["B"], 1)
    out = tmp_path / "diff_pngs"
    raster_diff(
        system_pdf=pdf_a,
        golden_pdf=pdf_b,
        page_numbers=[1],
        output_dir=out,
    )
    assert (out / "system_page_001.png").exists()
    assert (out / "golden_page_001.png").exists()


def test_raster_diff_size_mismatch_marks_one_hundred_percent(tmp_path):
    """A page-size mismatch is a 100% divergence (Letter vs A4 etc.)."""
    import fitz

    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"

    # Letter portrait
    doc_a = fitz.open()
    page = doc_a.new_page(width=612, height=792)
    page.insert_text((72, 100), "Letter", fontsize=12)
    doc_a.save(str(pdf_a))
    doc_a.close()

    # A4 portrait — different size
    doc_b = fitz.open()
    page = doc_b.new_page(width=595, height=842)
    page.insert_text((72, 100), "A4", fontsize=12)
    doc_b.save(str(pdf_b))
    doc_b.close()

    result = raster_diff(
        system_pdf=pdf_a, golden_pdf=pdf_b, page_numbers=[1]
    )
    assert result.overall_pass is False
    assert result.pages[0].divergence == 1.0
    assert result.pages[0].system_pixmap_size != result.pages[0].golden_pixmap_size


def test_raster_diff_missing_inputs_raise(tmp_path):
    """Either input missing → FileNotFoundError with the offending path."""
    real = tmp_path / "real.pdf"
    _make_text_pdf(real, ["x"], 1)
    missing = tmp_path / "missing.pdf"

    with pytest.raises(FileNotFoundError, match="system_pdf"):
        raster_diff(system_pdf=missing, golden_pdf=real, page_numbers=[1])
    with pytest.raises(FileNotFoundError, match="golden_pdf"):
        raster_diff(system_pdf=real, golden_pdf=missing, page_numbers=[1])


def test_raster_diff_per_page_dataclass_shape(tmp_path):
    """PageDivergence carries page_number, divergence, tolerance, sizes."""
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    _make_text_pdf(pdf_a, ["x"], 1)
    _make_text_pdf(pdf_b, ["x"], 1)
    result = raster_diff(system_pdf=pdf_a, golden_pdf=pdf_b, page_numbers=[1])
    page = result.pages[0]
    assert isinstance(page, PageDivergence)
    assert page.page_number == 1
    assert 0.0 <= page.divergence <= 1.0
    assert page.tolerance == DEFAULT_TOLERANCE
    assert isinstance(page.system_pixmap_size, tuple)
    assert len(page.system_pixmap_size) == 2


def test_byte_tol_is_anti_aliasing_friendly():
    """BYTE_TOL is the Pitfall 6 mitigation knob; documented as 16."""
    assert BYTE_TOL == 16
