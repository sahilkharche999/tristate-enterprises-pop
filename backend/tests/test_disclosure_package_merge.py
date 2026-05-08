"""Plan 11-05 Task 1: merge.py — pypdf merge + qpdf --check + atomic file write.

Behavior (REQ-D11-007, REQ-D11-008):
- merge_pdfs concatenates an ordered list of source PDFs into one output PDF.
- merge_pdfs([]) raises ValueError.
- merge_pdfs([nonexistent]) raises FileNotFoundError naming the missing file.
- qpdf_check raises RuntimeError on any non-zero return code from qpdf.
- qpdf_check is skipped (via qpdf_required fixture) on systems lacking the qpdf binary
  (RESEARCH Pitfall 4: qpdf is a system binary, not a pip package).
- write_atomic_bytes writes via temp+rename so a partial write is never visible at the
  destination path (analog: budget_history_service._write_atomic_bytes:141-146).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import fitz  # PyMuPDF — already in requirements; used here to manufacture test fixtures

from app.disclosure_package.merge import (
    merge_pdfs,
    qpdf_check,
    write_atomic_bytes,
)


def _make_pdf(path: Path, *, page_count: int = 1, label: str = "test") -> Path:
    """Build a minimal valid PDF with `page_count` pages using PyMuPDF.

    Using PyMuPDF (already in requirements.txt for raster diff) keeps these tests
    runnable without WeasyPrint installed locally — relevant on Python 3.9 dev
    machines where weasyprint==68.1 is not available.
    """
    doc = fitz.open()
    try:
        for i in range(page_count):
            page = doc.new_page(width=612, height=792)  # US Letter
            page.insert_text((72, 72), f"{label} page {i + 1}")
        doc.save(str(path))
    finally:
        doc.close()
    return path


def _page_count(path: Path) -> int:
    doc = fitz.open(str(path))
    try:
        return doc.page_count
    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# merge_pdfs
# ─────────────────────────────────────────────────────────────────────────────


def test_merge_pdfs_concatenates_in_order(tmp_path: Path) -> None:
    """Test 1: merge_pdfs([gen, app1, app2]) produces a single PDF whose page
    count is the sum of inputs."""
    a = _make_pdf(tmp_path / "a.pdf", page_count=2, label="A")
    b = _make_pdf(tmp_path / "b.pdf", page_count=3, label="B")
    c = _make_pdf(tmp_path / "c.pdf", page_count=1, label="C")
    out = tmp_path / "merged.pdf"

    merge_pdfs([a, b, c], out)

    assert out.exists()
    assert _page_count(out) == 6


def test_merge_pdfs_single_input_copies_all_pages(tmp_path: Path) -> None:
    """Test 2: merge_pdfs([single_pdf], output) produces a single-PDF copy with
    the same page count."""
    src = _make_pdf(tmp_path / "single.pdf", page_count=4)
    out = tmp_path / "out.pdf"

    merge_pdfs([src], out)

    assert _page_count(out) == 4


def test_merge_pdfs_empty_list_raises_value_error(tmp_path: Path) -> None:
    """Test 3: merge_pdfs([]) raises ValueError."""
    with pytest.raises(ValueError, match="at least one PDF"):
        merge_pdfs([], tmp_path / "out.pdf")


def test_merge_pdfs_missing_file_raises_with_path(tmp_path: Path) -> None:
    """Test 4: merge_pdfs([nonexistent_path], output) raises FileNotFoundError
    with the offending path in the message (REQ-D11-008)."""
    missing = tmp_path / "does_not_exist.pdf"
    with pytest.raises(FileNotFoundError, match=str(missing.name)):
        merge_pdfs([missing], tmp_path / "out.pdf")


def test_merge_pdfs_missing_file_partway_raises(tmp_path: Path) -> None:
    """A valid first file followed by a missing one still surfaces the missing
    path (REQ-D11-008)."""
    a = _make_pdf(tmp_path / "a.pdf", page_count=1)
    missing = tmp_path / "missing.pdf"
    with pytest.raises(FileNotFoundError, match=str(missing.name)):
        merge_pdfs([a, missing], tmp_path / "out.pdf")


# ─────────────────────────────────────────────────────────────────────────────
# qpdf_check
# ─────────────────────────────────────────────────────────────────────────────


def test_qpdf_check_passes_on_valid_pdf(tmp_path: Path, qpdf_required) -> None:
    """Test 5: qpdf_check(valid_pdf) returns None (no exception)."""
    src = _make_pdf(tmp_path / "valid.pdf", page_count=1)
    # Should NOT raise.
    assert qpdf_check(src) is None


def test_qpdf_check_raises_on_corrupt_pdf(tmp_path: Path, qpdf_required) -> None:
    """Test 6: qpdf_check(intentionally_corrupt_pdf) raises RuntimeError with
    stderr included (REQ-D11-007)."""
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4\nthis is not a valid PDF body\n%%EOF\n")
    with pytest.raises(RuntimeError, match="qpdf check failed"):
        qpdf_check(bad)


def test_qpdf_check_raises_when_binary_missing(monkeypatch, tmp_path: Path) -> None:
    """When the qpdf binary is absent, qpdf_check raises a clear RuntimeError
    pointing the operator at the install instructions. This is the production
    behavior; tests on dev machines without qpdf installed should depend on
    the `qpdf_required` fixture (which skips cleanly) rather than relying on
    this branch."""
    import app.disclosure_package.merge as merge_mod

    monkeypatch.setattr(merge_mod.shutil, "which", lambda _name: None)
    src = _make_pdf(tmp_path / "x.pdf", page_count=1)
    with pytest.raises(RuntimeError, match="qpdf binary not found"):
        qpdf_check(src)


# ─────────────────────────────────────────────────────────────────────────────
# write_atomic_bytes
# ─────────────────────────────────────────────────────────────────────────────


def test_write_atomic_bytes_writes_payload(tmp_path: Path) -> None:
    """Test 8a: write_atomic_bytes writes the exact payload to the destination."""
    dest = tmp_path / "output.bin"
    payload = b"hello world"
    write_atomic_bytes(dest, payload)
    assert dest.read_bytes() == payload


def test_write_atomic_bytes_creates_parent_dirs(tmp_path: Path) -> None:
    dest = tmp_path / "deep" / "nested" / "out.bin"
    write_atomic_bytes(dest, b"abc")
    assert dest.read_bytes() == b"abc"


def test_write_atomic_bytes_replaces_existing_file(tmp_path: Path) -> None:
    """Atomic write replaces an existing destination without leaving a temp
    file behind."""
    dest = tmp_path / "replace.bin"
    dest.write_bytes(b"old content")
    write_atomic_bytes(dest, b"new content")
    assert dest.read_bytes() == b"new content"
    # No leftover temp files in the destination directory.
    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert siblings == ["replace.bin"]


def test_write_atomic_bytes_partial_write_invisible(monkeypatch, tmp_path: Path) -> None:
    """Test 8b: if the write fails mid-flight, the destination path never
    contains a partial file (the temp file is what fails, dest is untouched)."""
    import app.disclosure_package.merge as merge_mod

    dest = tmp_path / "dest.bin"
    dest.write_bytes(b"original")

    real_replace = merge_mod.Path.replace

    def boom(self, target):  # type: ignore[no-untyped-def]
        raise OSError("simulated rename failure")

    monkeypatch.setattr(merge_mod.Path, "replace", boom)
    with pytest.raises(OSError, match="simulated rename failure"):
        write_atomic_bytes(dest, b"new payload that never lands")

    # Destination is untouched.
    assert dest.read_bytes() == b"original"

    # Restore for the rest of the test session.
    monkeypatch.setattr(merge_mod.Path, "replace", real_replace)


# ─────────────────────────────────────────────────────────────────────────────
# Integration — merge + qpdf check + atomic write together
# ─────────────────────────────────────────────────────────────────────────────


def test_merge_then_qpdf_check_passes(tmp_path: Path, qpdf_required) -> None:
    """Test 9: After merge_pdfs the output passes qpdf_check (round-trip
    confidence the merge produces a structurally valid PDF). REQ-D11-007."""
    a = _make_pdf(tmp_path / "a.pdf", page_count=2)
    b = _make_pdf(tmp_path / "b.pdf", page_count=3)
    c = _make_pdf(tmp_path / "c.pdf", page_count=1)
    out = tmp_path / "package.pdf"

    merge_pdfs([a, b, c], out)
    qpdf_check(out)  # raises on any structural problem

    assert _page_count(out) == 6
