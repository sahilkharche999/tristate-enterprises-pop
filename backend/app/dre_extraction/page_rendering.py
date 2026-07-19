"""DRE page rendering helpers (Phase 3.2).

Thin wrapper around the existing ``render_pdf_pages`` in
``pdf_vlm_extractor`` that supplies DRE-specific defaults — 150 DPI per
the change spec (higher than the hybrid 72-DPI default; needed for
dense DRE schedules with small print).

The page-rendering itself is the same PyMuPDF code path used by
``reserve_study_extractor.py`` and the budget VLM extractor; we share
it to avoid drift in the rendering primitives.
"""

from __future__ import annotations

from typing import Optional

from app.ai_implementation.pipeline.document_extraction_provider import RenderedPage
from app.services.pdf_vlm_extractor import render_pdf_pages


DRE_RENDER_DPI: int = 150


def render_dre_pages(
    path: str,
    *,
    max_pages: Optional[int] = None,
    dpi: int = DRE_RENDER_DPI,
) -> list[RenderedPage]:
    """Render a DRE PDF to PNG pages at the DRE-default DPI.

    Wraps the shared ``render_pdf_pages`` so the DRE extraction pipeline
    doesn't import directly from the budget VLM extractor. ``dpi``
    overrides the 150-DPI default when needed.

    Unlike the budget VLM path (which caps at ``DOCUMENT_VLM_MAX_PAGES``
    pages because the budget statements are short), DREs can be 200+
    pages and the pipeline batches them via ``page_classification``.
    When ``max_pages`` is None, we render every page in the document by
    looking up the real page count first.
    """
    if max_pages is None:
        try:
            import fitz  # type: ignore

            doc = fitz.open(path)
            try:
                max_pages = doc.page_count
            finally:
                doc.close()
        except ImportError as exc:
            raise RuntimeError(
                "PDF rendering requires PyMuPDF (fitz). Install pymupdf to enable the DRE path."
            ) from exc
    return render_pdf_pages(path, max_pages=max_pages, dpi=dpi)
