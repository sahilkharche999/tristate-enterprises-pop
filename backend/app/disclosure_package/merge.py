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
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

QPDF_TIMEOUT_SECONDS = 30


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


__all__ = ["merge_pdfs", "qpdf_check", "write_atomic_bytes", "QPDF_TIMEOUT_SECONDS"]
