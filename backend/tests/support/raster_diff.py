"""PyMuPDF raster diff between system-generated PDF and golden reference (CONTEXT D-13).

Per-page divergence threshold default 1% (RESEARCH § "Layer 4"). Tightenable
once stable. Image-only golden pages (scanned appendices) are excluded —
this verifier only operates on the 18 generated logical pages mapping to
physical pages 1-30 of the golden.

RESEARCH Pitfall 6: anti-aliasing differences make strict equality fail
even on visually-identical pages. We use perceptual diff: byte-by-byte
comparison of normalized pixmaps with a per-byte tolerance, then computing
the fraction of pixels whose RGB triple differs above that tolerance. The
1% page-level tolerance accommodates AA noise and font hinting differences
between WeasyPrint and the golden's renderer.

Contract:
    raster_diff(*, system_pdf, golden_pdf, page_numbers, output_dir=None,
                dpi=150, tolerance=0.01) -> RasterDiffResult

The function is dependency-light at import time — fitz is imported inside
the call so that test collection / unrelated import paths do not trigger
the PyMuPDF native-library load.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

DEFAULT_DPI = 150
DEFAULT_TOLERANCE = 0.01  # 1% pixel divergence — CONTEXT D-13
# Per-byte tolerance for AA / font hinting differences (RESEARCH Pitfall 6).
# 16 / 255 ≈ 6.3 % per-channel intensity slack; pixels whose R, G, AND B all
# stay within this band count as "matching".
BYTE_TOL = 16


@dataclass
class PageDivergence:
    """Per-page result for a single physical page comparison."""

    page_number: int  # 1-based physical page
    divergence: float  # fraction of pixels that diverge above BYTE_TOL (0..1)
    tolerance: float
    system_pixmap_size: tuple[int, int]
    golden_pixmap_size: tuple[int, int]


@dataclass
class RasterDiffResult:
    """Aggregate result from raster_diff."""

    pages: list[PageDivergence]
    overall_pass: bool


def _open_doc(path: Path):
    """Lazy-import fitz then open the PDF — keeps PyMuPDF off the import path."""
    import fitz  # type: ignore[import-not-found]

    return fitz.open(str(path))


def raster_diff(
    *,
    system_pdf: Path,
    golden_pdf: Path,
    page_numbers: Sequence[int],
    output_dir: Path | None = None,
    dpi: int = DEFAULT_DPI,
    tolerance: float = DEFAULT_TOLERANCE,
) -> RasterDiffResult:
    """Compare ``system_pdf`` against ``golden_pdf`` on the requested pages.

    Args:
        system_pdf: Path to the system-generated PDF (compile_package output).
        golden_pdf: Path to the golden reference (e.g.
            ``2026/Old Mill 2026 budget disclosure.pdf``).
        page_numbers: 1-based physical page numbers to compare. Pages outside
            either document are silently skipped (logged at WARNING).
        output_dir: If set, writes per-page system + golden pixmaps as PNGs
            for human inspection. Created if it does not exist.
        dpi: Rasterization DPI. 150 matches typical print resolution and is
            the de-facto floor for catching layout drift while keeping the
            byte-loop bounded (~2.5 M RGBA bytes per Letter page).
        tolerance: Per-page divergence threshold 0..1 (default 0.01 = 1%).

    Returns:
        RasterDiffResult. ``overall_pass`` is True iff every requested page
        landed at-or-below its tolerance.

    Raises:
        FileNotFoundError: either input PDF is missing.
    """
    import fitz  # type: ignore[import-not-found]  # noqa: F401  (kept for parity / fail-fast import)

    if not Path(system_pdf).exists():
        raise FileNotFoundError(f"system_pdf not found: {system_pdf}")
    if not Path(golden_pdf).exists():
        raise FileNotFoundError(f"golden_pdf not found: {golden_pdf}")

    sys_doc = _open_doc(Path(system_pdf))
    gold_doc = _open_doc(Path(golden_pdf))

    try:
        scale = dpi / 72.0
        matrix = fitz.Matrix(scale, scale)

        pages: list[PageDivergence] = []
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)

        max_page = min(len(sys_doc), len(gold_doc))

        for page_num in page_numbers:
            if page_num < 1 or page_num > max_page:
                logger.warning(
                    "Skipping out-of-range page %d (system=%d, golden=%d)",
                    page_num,
                    len(sys_doc),
                    len(gold_doc),
                )
                continue

            sys_page = sys_doc[page_num - 1]
            gold_page = gold_doc[page_num - 1]
            sys_pix = sys_page.get_pixmap(matrix=matrix)
            gold_pix = gold_page.get_pixmap(matrix=matrix)

            sys_size = (sys_pix.width, sys_pix.height)
            gold_size = (gold_pix.width, gold_pix.height)

            if sys_size != gold_size:
                # Page-size mismatch (e.g., Letter vs A4, or rotation).
                # Treat as 100% divergent — caller can inspect the saved PNGs
                # to decide whether the mismatch is intentional (different
                # paper size) or a layout regression.
                logger.warning(
                    "Page %d size mismatch: system=%dx%d golden=%dx%d",
                    page_num,
                    sys_size[0],
                    sys_size[1],
                    gold_size[0],
                    gold_size[1],
                )
                pages.append(
                    PageDivergence(
                        page_number=page_num,
                        divergence=1.0,
                        tolerance=tolerance,
                        system_pixmap_size=sys_size,
                        golden_pixmap_size=gold_size,
                    )
                )
                if output_dir is not None:
                    sys_pix.save(str(output_dir / f"system_page_{page_num:03d}.png"))
                    gold_pix.save(str(output_dir / f"golden_page_{page_num:03d}.png"))
                continue

            # Perceptual diff: count pixels whose R, G, OR B channel diverges
            # above BYTE_TOL. Alpha is intentionally skipped — WeasyPrint and
            # the golden's renderer disagree on alpha for white backgrounds.
            sys_bytes = sys_pix.samples
            gold_bytes = gold_pix.samples
            n_components = sys_pix.n  # 3 (RGB) or 4 (RGBA)
            total_bytes = len(sys_bytes)
            num_pixels = total_bytes // n_components if n_components else 0

            diff_count = 0
            # The Python loop is acceptable for MVP — runs once per CI parity
            # test, bounded by 30 pages × ~2.5 M pixels = ~75 M iterations.
            # Plan 11-09 may swap in numpy if we adopt it elsewhere.
            if n_components >= 3 and num_pixels:
                for i in range(0, total_bytes, n_components):
                    if (
                        abs(sys_bytes[i] - gold_bytes[i]) > BYTE_TOL
                        or abs(sys_bytes[i + 1] - gold_bytes[i + 1]) > BYTE_TOL
                        or abs(sys_bytes[i + 2] - gold_bytes[i + 2]) > BYTE_TOL
                    ):
                        diff_count += 1

            divergence = (diff_count / num_pixels) if num_pixels else 0.0

            pages.append(
                PageDivergence(
                    page_number=page_num,
                    divergence=divergence,
                    tolerance=tolerance,
                    system_pixmap_size=sys_size,
                    golden_pixmap_size=gold_size,
                )
            )
            if output_dir is not None:
                sys_pix.save(str(output_dir / f"system_page_{page_num:03d}.png"))
                gold_pix.save(str(output_dir / f"golden_page_{page_num:03d}.png"))

        overall_pass = all(p.divergence <= p.tolerance for p in pages) if pages else False
        return RasterDiffResult(pages=pages, overall_pass=overall_pass)
    finally:
        sys_doc.close()
        gold_doc.close()


__all__ = [
    "raster_diff",
    "RasterDiffResult",
    "PageDivergence",
    "DEFAULT_DPI",
    "DEFAULT_TOLERANCE",
    "BYTE_TOL",
]
