"""On-disk storage for HOA boilerplate reference PDFs (upload source)."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

_FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class UnsupportedReferenceFileType(ValueError):
    """Raised when an uploaded reference is not a PDF."""


class ReferenceFileTooLarge(ValueError):
    """Raised when an uploaded reference exceeds the size cap."""


def _storage_root() -> Path:
    from app.config import settings

    root = Path(settings.BUDGET_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _subdir(property_id: int) -> Path:
    return _storage_root() / "hoa-boilerplate-reference" / str(int(property_id))


def save_reference_pdf(
    *,
    property_id: int,
    file_bytes: bytes,
    original_filename: str,
    max_bytes: int,
) -> str:
    if len(file_bytes) > max_bytes:
        raise ReferenceFileTooLarge(
            f"Reference PDF exceeds max size of {max_bytes} bytes"
        )
    name = Path(original_filename or "reference.pdf").name
    cleaned = _FILENAME_SANITIZE_RE.sub("_", name).strip("._") or "reference.pdf"
    if Path(cleaned).suffix.lower() != ".pdf":
        raise UnsupportedReferenceFileType(
            f"Unsupported reference file type; only PDF allowed (got {cleaned!r})"
        )
    # Fixed name so re-upload replaces rather than orphans files.
    relative = Path("hoa-boilerplate-reference") / str(int(property_id)) / "reference.pdf"
    absolute = _storage_root() / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=absolute.parent, delete=False) as handle:
        handle.write(file_bytes)
        temp_path = Path(handle.name)
    temp_path.replace(absolute)
    return str(relative)


def reference_path(relative_filename: str) -> Path:
    return _storage_root() / relative_filename


def reference_exists(relative_filename: Optional[str]) -> bool:
    return bool(relative_filename and reference_path(relative_filename).exists())


def delete_reference(relative_filename: str) -> bool:
    path = reference_path(relative_filename)
    if not path.exists():
        return False
    path.unlink()
    return True
