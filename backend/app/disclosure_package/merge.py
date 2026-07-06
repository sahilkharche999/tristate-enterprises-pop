"""Merge generated PDFs with static appendix PDFs (CONTEXT D-11, D-12).

pypdf for in-process merge; qpdf as last-mile structural validator (REQ-D11-007).
Atomic file writes via temp+rename pattern (analog:
``backend/app/services/budget_history_service.py::_write_atomic_bytes``).

Public surface:
    merge_pdfs(pdf_paths, output_path) -> None
        Concatenate an ordered list of PDFs into one output PDF.
        Empty list raises ValueError; missing source raises FileNotFoundError
        with the offending path in the message (REQ-D11-008).

    qpdf_check(path) -> None
        Run ``qpdf --check {path}``; raise RuntimeError on non-zero return
        code or missing binary. Tests on systems without qpdf should use
        the ``qpdf_required`` pytest fixture (RESEARCH Pitfall 4).

    write_atomic_bytes(destination, payload) -> None
        Atomic write via temp+rename. A partial write is never visible at
        the destination path.

    stamp_absolute_page_numbers(pdf_path) -> None
        Overwrite the footer page-number slot on every page of an
        already-merged PDF with its true absolute position ("N of Total"),
        replacing the per-section ``counter(page)`` value each standalone
        template rendered (which resets to 1 every section, since each
        section is a separate PDF before merge — see _shared.css). Must
        run AFTER the final merge, since it needs the real total page
        count.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

QPDF_TIMEOUT_SECONDS = 30

# Mirrors the @page geometry in templates/standard/_shared.css. Only the
# bottom-right footer box matters here — the overlay page's background is
# transparent everywhere else (verified: WeasyPrint doesn't paint a
# background unless one is styled), so pypdf's merge_page composites it
# over the real page's content without hiding anything.
_PAGE_NUMBER_OVERLAY_CSS = """
@page {
    size: Letter;
    margin: 1.4in 0.65in 1.1in 0.65in;
    @bottom-right {
        content: counter(page) " of " counter(pages);
        font-family: "DejaVu Sans Condensed", Tahoma, "Liberation Sans", sans-serif;
        font-size: 8pt;
        color: #555;
    }
}
html, body { margin: 0; padding: 0; }
.pg { page-break-after: always; }
.pg:last-child { page-break-after: avoid; }
"""


def stamp_absolute_page_numbers(pdf_path: Path) -> None:
    """Overlay the true absolute page number onto every page of ``pdf_path``.

    Renders one WeasyPrint document with as many blank pages as the target
    PDF, using CSS ``counter(page)``/``counter(pages)`` (valid here because
    it's a single document, unlike the per-template render pipeline where
    each section is its own standalone PDF). Each blank overlay page is
    then merged onto the corresponding real page via ``pypdf``.
    """
    from pypdf import PdfReader, PdfWriter
    from weasyprint import HTML

    pdf_path = Path(pdf_path)
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    if total_pages == 0:
        return

    overlay_html = (
        "<html><head><style>"
        + _PAGE_NUMBER_OVERLAY_CSS
        + "</style></head><body>"
        + '<div class="pg"></div>' * total_pages
        + "</body></html>"
    )
    overlay_bytes = HTML(string=overlay_html).write_pdf()
    overlay_reader = PdfReader(BytesIO(overlay_bytes))
    if len(overlay_reader.pages) != total_pages:
        raise RuntimeError(
            f"Page-number overlay produced {len(overlay_reader.pages)} pages, "
            f"expected {total_pages}; refusing to stamp a mismatched overlay."
        )

    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        page.merge_page(overlay_reader.pages[i])
        writer.add_page(page)

    with tempfile.NamedTemporaryFile(
        dir=pdf_path.parent,
        delete=False,
        suffix=".pdf",
    ) as tmp:
        writer.write(tmp)
        tmp_path = Path(tmp.name)
    tmp_path.replace(pdf_path)


def merge_pdfs(pdf_paths: list[Path], output_path: Path) -> None:
    """Merge a list of PDF files into one output PDF in the given order.

    Args:
        pdf_paths: ordered list of source PDFs (generated first, then
            appendices in spec.entries order).
        output_path: destination for the merged file.

    Raises:
        ValueError: if ``pdf_paths`` is empty.
        FileNotFoundError: if any source path is missing (REQ-D11-008). The
            offending path is included in the message so the preflight UI can
            surface it.
    """
    if not pdf_paths:
        raise ValueError("merge_pdfs requires at least one PDF path")

    # Validate ALL inputs up front so the failure mode is "raise immediately"
    # rather than "open writer, append n-1 files, then explode" — the latter
    # would leak a temp file in the output directory.
    for p in pdf_paths:
        if not Path(p).exists():
            raise FileNotFoundError(f"Source PDF not found for merge: {p}")

    from pypdf import PdfWriter

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    writer = PdfWriter()
    try:
        for p in pdf_paths:
            writer.append(str(p))
        # Write to a temp file in the destination directory then atomically
        # rename (analog: budget_history_service._write_atomic_bytes).
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            delete=False,
            suffix=".pdf",
        ) as tmp:
            writer.write(tmp)
            tmp_path = Path(tmp.name)
        tmp_path.replace(output_path)
    finally:
        writer.close()


def qpdf_check(path: Path) -> None:
    """Run ``qpdf --check {path}``; raise RuntimeError on non-zero return code.

    Implements REQ-D11-007 (last-mile structural validator). Per RESEARCH
    Pitfall 4 callers running on systems without the qpdf binary should use
    the ``qpdf_required`` pytest fixture which skips dependent tests cleanly.

    Args:
        path: path to a PDF file on disk.

    Raises:
        RuntimeError: if qpdf is not installed, or if qpdf returns a non-zero
            exit code (the stderr is included in the message).
    """
    if shutil.which("qpdf") is None:
        raise RuntimeError(
            "qpdf binary not found; install via apt-get install qpdf "
            "(Dockerfile already does this)."
        )
    result = subprocess.run(
        ["qpdf", "--check", str(path)],
        capture_output=True,
        check=False,
        timeout=QPDF_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"qpdf check failed for {path}: returncode={result.returncode} "
            f"stderr={result.stderr.decode(errors='replace')}"
        )


def write_atomic_bytes(destination: Path, payload: bytes) -> None:
    """Atomic write via temp+rename.

    Mirrors ``budget_history_service._write_atomic_bytes:141-146``: write to a
    temp file inside the destination directory, then ``os.replace`` to swap
    in the final name. A partial write is never visible at ``destination``;
    the temp file is what fails on disk-full / permission errors.

    Args:
        destination: final path the bytes should land at.
        payload: bytes to write.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        tmp_path = Path(handle.name)
    try:
        tmp_path.replace(destination)
    except Exception:
        # If the rename failed, clean up the temp file rather than leaving a
        # partial-content sibling next to the destination. The destination
        # itself was untouched (rename is atomic; either it lands or it does
        # not).
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


__all__ = [
    "merge_pdfs",
    "qpdf_check",
    "write_atomic_bytes",
    "stamp_absolute_page_numbers",
    "QPDF_TIMEOUT_SECONDS",
]
