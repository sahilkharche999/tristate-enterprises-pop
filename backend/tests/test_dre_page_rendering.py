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


