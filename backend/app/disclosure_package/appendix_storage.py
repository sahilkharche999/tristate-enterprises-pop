"""Per-HOA appendix-file storage (Phase 5.4).

Appendices are stored under
``BUDGET_STORAGE_ROOT/appendices/<property_id>/`` on the same
persistent volume as DRE PDFs and budget uploads. The
``appendix_documents.file_id`` column stores the relative path so
the DB row resolves to a file regardless of where the storage root
is mounted.

Mirrors the DRE-side pattern in ``dre_extraction/storage.py`` —
sanitized filenames, atomic writes, row-id prefix to avoid collisions
when an operator uploads multiple appendices for one HOA.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional


_FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _storage_root() -> Path:
    from app.config import settings  # local import; matches dre_extraction.storage

    root = Path(settings.BUDGET_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sanitize_filename(name: str) -> str:
    stem = Path(name).name
    cleaned = _FILENAME_SANITIZE_RE.sub("_", stem).strip("._")
    return cleaned or "appendix.pdf"


def _write_atomic_bytes(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    temp_path.replace(destination)


def save_appendix_file(
    *,
    property_id: int,
    file_bytes: bytes,
    original_filename: str,
    appendix_id: int,
) -> str:
    """Persist appendix PDF bytes to disk; return the storage-root-relative
    ``file_id`` recorded on the ``appendix_documents`` row.

    The id encodes ``appendices/<property_id>/<appendix_id>_<safename>``
    so concurrent uploads for one HOA don't collide and a database
    backup makes the row ↔ file mapping trivially recoverable.
    """
    sanitized = _sanitize_filename(original_filename)
    relative = (
        Path("appendices")
        / str(int(property_id))
        / f"{int(appendix_id)}_{sanitized}"
    )
    absolute = _storage_root() / relative
    _write_atomic_bytes(absolute, file_bytes)
    return str(relative)


def appendix_file_path(file_id: str) -> Path:
    """Resolve a stored ``appendix_documents.file_id`` to an absolute path."""
    return _storage_root() / file_id


def appendix_file_exists(file_id: Optional[str]) -> bool:
    return bool(file_id and appendix_file_path(file_id).exists())


def delete_appendix_file(file_id: str) -> bool:
    """Remove a stored appendix file. Returns True if a file was removed."""
    path = appendix_file_path(file_id)
    if not path.exists():
        return False
    path.unlink()
    return True
