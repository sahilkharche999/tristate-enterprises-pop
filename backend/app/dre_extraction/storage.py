"""DRE PDF storage on disk (Phase 3.1).

DRE files are stored under ``BUDGET_STORAGE_ROOT/dre/<property_id>/``
to live on the same persistent volume as the budget uploads. The
``DREDocument.file_id`` column stores the relative path under that
root so the same DB row works across local dev (``/tmp/...``) and
production (``/app/app/ai_implementation/data/...``).

Mirrors the storage pattern in ``budget_history_service`` —
atomic-write, sanitized filename, per-property subdirectory — but
isolated here so the DRE pipeline doesn't reach into budget service
internals.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Optional


# Imported lazily inside helpers so this module can be imported in
# contexts where ``app.config.settings`` isn't fully initialised yet
# (e.g. schema-validation tests).
_FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _storage_root() -> Path:
    from app.config import settings  # local import; see module docstring

    root = Path(settings.BUDGET_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _dre_subdir(property_id: int) -> Path:
    return _storage_root() / "dre" / str(int(property_id))


def _sanitize_filename(name: str) -> str:
    """Strip directory components and weird characters. Always returns
    a non-empty string with a usable extension when possible.
    """
    stem = Path(name).name  # drop any leading directories the caller passed
    cleaned = _FILENAME_SANITIZE_RE.sub("_", stem).strip("._")
    return cleaned or "dre.pdf"


def _write_atomic_bytes(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    temp_path.replace(destination)


def save_dre_file(
    *,
    property_id: int,
    file_bytes: bytes,
    original_filename: str,
    dre_document_id: int,
) -> str:
    """Persist DRE PDF bytes to disk; return the storage-root-relative
    ``file_id`` to record on the ``DREDocument`` row.

    The file id encodes ``dre/<property_id>/<dre_document_id>_<safename>``
    so multiple uploads for one property coexist without collision and
    a database backup makes the file ↔ row mapping trivially recoverable.
    """
    sanitized = _sanitize_filename(original_filename)
    relative = Path("dre") / str(int(property_id)) / f"{int(dre_document_id)}_{sanitized}"
    absolute = _storage_root() / relative
    _write_atomic_bytes(absolute, file_bytes)
    return str(relative)


def dre_file_path(file_id: str) -> Path:
    """Resolve a stored ``DREDocument.file_id`` to an absolute path."""
    return _storage_root() / file_id


def dre_file_exists(file_id: Optional[str]) -> bool:
    return bool(file_id and dre_file_path(file_id).exists())


def delete_dre_file(file_id: str) -> bool:
    """Remove a stored DRE file. Returns True if a file was removed."""
    path = dre_file_path(file_id)
    if not path.exists():
        return False
    path.unlink()
    return True
