"""DRE page rendering tests (Phase 3.2).

Each test builds a tiny synthetic PDF via PyMuPDF, runs it through the
DRE renderer, and verifies the output. No external corpus needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")  # PyMuPDF; skip whole module if unavailable

from app.dre_extraction import (
    DRE_RENDER_DPI,
    build_contact_sheet_pdf,
    render_dre_pages,
)


def _synth_pdf(path: Path, page_count: int) -> Path:
    """Build a synthetic PDF with ``page_count`` labelled pages."""
    doc = fitz.open()
    for i in range(page_count):
        page = doc.new_page(width=612, height=792)
        page.insert_text(fitz.Point(50, 100), f"PAGE {i + 1}", fontsize=24)
    doc.save(str(path))
    doc.close()
    return path


class TestRenderDREPages:
    def test_renders_all_pages_by_default(self, tmp_path: Path) -> None:
        pdf = _synth_pdf(tmp_path / "doc.pdf", page_count=10)
        pages = render_dre_pages(str(pdf))
        # All 10 pages rendered (no implicit max-pages cap)
        assert len(pages) == 10
        assert [p.page_number for p in pages] == list(range(1, 11))

    def test_respects_explicit_max_pages(self, tmp_path: Path) -> None:
        pdf = _synth_pdf(tmp_path / "doc.pdf", page_count=10)
        pages = render_dre_pages(str(pdf), max_pages=3)
        assert len(pages) == 3
        assert [p.page_number for p in pages] == [1, 2, 3]

    def test_default_dpi_is_150(self) -> None:
        assert DRE_RENDER_DPI == 150

    def test_output_is_png(self, tmp_path: Path) -> None:
        pdf = _synth_pdf(tmp_path / "doc.pdf", page_count=1)
        pages = render_dre_pages(str(pdf))
        # PNG magic bytes
        assert pages[0].mime_type == "image/png"
        assert pages[0].content.startswith(b"\x89PNG\r\n")

    def test_lower_dpi_renders_smaller_image(self, tmp_path: Path) -> None:
        pdf = _synth_pdf(tmp_path / "doc.pdf", page_count=1)
        small = render_dre_pages(str(pdf), dpi=72)
        big = render_dre_pages(str(pdf), dpi=300)
        assert len(small[0].content) < len(big[0].content)


class TestContactSheet:
    def test_empty_input_returns_empty_bytes(self) -> None:
        assert build_contact_sheet_pdf([]) == b""

    def test_sheet_count_matches_grid_packing(self, tmp_path: Path) -> None:
        # 6 thumbnails per sheet (2 cols × 3 rows); 7 source pages → 2 sheets
        pdf = _synth_pdf(tmp_path / "doc.pdf", page_count=7)
        pages = render_dre_pages(str(pdf))
        sheet_bytes = build_contact_sheet_pdf(pages)
        sheet_doc = fitz.open(stream=sheet_bytes, filetype="pdf")
        try:
            assert sheet_doc.page_count == 2
        finally:
            sheet_doc.close()

    def test_single_sheet_when_pages_fit(self, tmp_path: Path) -> None:
        pdf = _synth_pdf(tmp_path / "doc.pdf", page_count=4)
        pages = render_dre_pages(str(pdf))
        sheet_bytes = build_contact_sheet_pdf(pages)
        sheet_doc = fitz.open(stream=sheet_bytes, filetype="pdf")
        try:
            assert sheet_doc.page_count == 1
        finally:
            sheet_doc.close()

    def test_caption_includes_source_page_number(self, tmp_path: Path) -> None:
        pdf = _synth_pdf(tmp_path / "doc.pdf", page_count=2)
        pages = render_dre_pages(str(pdf))
        sheet_bytes = build_contact_sheet_pdf(pages)
        sheet_doc = fitz.open(stream=sheet_bytes, filetype="pdf")
        try:
            text = sheet_doc[0].get_text()
            assert "Page 1" in text
            assert "Page 2" in text
        finally:
            sheet_doc.close()
