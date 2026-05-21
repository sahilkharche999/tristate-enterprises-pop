"""DRE page rendering helpers (Phase 3.2).

Thin wrapper around the existing ``render_pdf_pages`` in
``pdf_vlm_extractor`` that supplies DRE-specific defaults — 150 DPI per
the change spec (higher than the hybrid 72-DPI default; needed for
dense DRE schedules with small print) and a contact-sheet builder for
offline admin review.

The page-rendering itself is the same PyMuPDF code path used by
``reserve_study_extractor.py`` and the budget VLM extractor; we share
it to avoid drift in the rendering primitives.
"""

from __future__ import annotations

from typing import Optional

from app.ai_implementation.pipeline.document_extraction_provider import RenderedPage
from app.services.pdf_vlm_extractor import render_pdf_pages


DRE_RENDER_DPI: int = 150

# Contact-sheet layout — 2 columns × 3 rows = 6 thumbnails per page.
CONTACT_SHEET_COLUMNS: int = 2
CONTACT_SHEET_ROWS: int = 3
CONTACT_SHEET_MARGIN_PT: float = 36  # 0.5 inch
CONTACT_SHEET_GAP_PT: float = 18     # 0.25 inch gap between thumbnails


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


def build_contact_sheet_pdf(
    rendered_pages: list[RenderedPage],
    *,
    columns: int = CONTACT_SHEET_COLUMNS,
    rows: int = CONTACT_SHEET_ROWS,
) -> bytes:
    """Lay out rendered pages as a multi-page contact sheet PDF.

    Each contact-sheet page contains ``columns × rows`` thumbnails with
    the source page number printed above each. Used by the admin
    Review Workbench to give the operator a one-glance view of every
    page in a freshly-uploaded DRE.

    Returns the contact-sheet PDF as bytes (US-Letter, portrait).
    """
    if not rendered_pages:
        return b""

    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Contact-sheet generation requires PyMuPDF (fitz). "
            "Install pymupdf to enable DRE admin-review output."
        ) from exc

    per_sheet = columns * rows
    if per_sheet < 1:
        raise ValueError(f"columns × rows must be >= 1, got {columns}×{rows}")

    sheet = fitz.open()
    # US Letter at 72 DPI — 612 × 792 points
    page_width, page_height = 612.0, 792.0
    cell_w = (
        page_width - 2 * CONTACT_SHEET_MARGIN_PT - (columns - 1) * CONTACT_SHEET_GAP_PT
    ) / columns
    cell_h = (
        page_height - 2 * CONTACT_SHEET_MARGIN_PT - (rows - 1) * CONTACT_SHEET_GAP_PT
    ) / rows

    try:
        for sheet_index in range(0, len(rendered_pages), per_sheet):
            slice_ = rendered_pages[sheet_index : sheet_index + per_sheet]
            sheet_page = sheet.new_page(width=page_width, height=page_height)
            for cell_index, rendered in enumerate(slice_):
                col = cell_index % columns
                row = cell_index // columns
                x0 = CONTACT_SHEET_MARGIN_PT + col * (cell_w + CONTACT_SHEET_GAP_PT)
                y0 = CONTACT_SHEET_MARGIN_PT + row * (cell_h + CONTACT_SHEET_GAP_PT)
                rect = fitz.Rect(x0, y0 + 14, x0 + cell_w, y0 + cell_h)
                sheet_page.insert_image(rect, stream=rendered.content, keep_proportion=True)
                # Page-number caption above each thumbnail
                sheet_page.insert_text(
                    fitz.Point(x0, y0 + 10),
                    f"Page {rendered.page_number}",
                    fontsize=9,
                )
        pdf_bytes = sheet.tobytes()
    finally:
        sheet.close()

    return pdf_bytes
