"""Per-HOA disclosure-package logo storage on disk.

Mirrors ``app/dre_extraction/storage.py``'s pattern (atomic write,
sanitized filename, per-property subdirectory) so logo files live on
the same persistent volume as budget/DRE uploads. ``HOASettings.
logo_filename`` stores the storage-root-relative path, not bytes.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional

_FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Only image types a disclosure-package letterhead can sensibly embed.
ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg"}


class UnsupportedLogoFileType(ValueError):
    """Raised when an uploaded logo file's extension isn't allowed."""


def _storage_root() -> Path:
    from app.config import settings  # local import; see module docstring

    root = Path(settings.BUDGET_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _logo_subdir(property_id: int) -> Path:
    return _storage_root() / "hoa-logos" / str(int(property_id))


def _sanitize_filename(name: str) -> str:
    stem = Path(name).name
    cleaned = _FILENAME_SANITIZE_RE.sub("_", stem).strip("._")
    return cleaned or "logo"


def _write_atomic_bytes(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    temp_path.replace(destination)


def save_hoa_logo(
    *,
    property_id: int,
    file_bytes: bytes,
    original_filename: str,
) -> str:
    """Persist logo bytes to disk; return the storage-root-relative
    filename to record on ``HOASettings.logo_filename``.

    Raises ``UnsupportedLogoFileType`` for extensions outside
    ``ALLOWED_LOGO_EXTENSIONS`` — a disclosure-package template can only
    embed a raster image or inline SVG, not arbitrary uploads.
    """
    sanitized = _sanitize_filename(original_filename)
    extension = Path(sanitized).suffix.lower()
    if extension not in ALLOWED_LOGO_EXTENSIONS:
        raise UnsupportedLogoFileType(
            f"Unsupported logo file type {extension!r}; allowed: "
            f"{sorted(ALLOWED_LOGO_EXTENSIONS)}"
        )
    # Fixed filename per property (not per-upload) so re-uploading replaces
    # the prior logo rather than accumulating orphaned files.
    relative = Path("hoa-logos") / str(int(property_id)) / f"logo{extension}"
    absolute = _storage_root() / relative
    # Remove a previous logo with a different extension so an operator who
    # re-uploads a .png after a .svg doesn't leave the old file dangling.
    for existing_ext in ALLOWED_LOGO_EXTENSIONS:
        stale = _logo_subdir(property_id) / f"logo{existing_ext}"
        if stale.exists() and stale != absolute:
            stale.unlink()
    _write_atomic_bytes(absolute, file_bytes)
    return str(relative)


def hoa_logo_path(logo_filename: str) -> Path:
    """Resolve a stored ``HOASettings.logo_filename`` to an absolute path."""
    return _storage_root() / logo_filename


def hoa_logo_exists(logo_filename: Optional[str]) -> bool:
    return bool(logo_filename and hoa_logo_path(logo_filename).exists())


def delete_hoa_logo(logo_filename: str) -> bool:
    """Remove a stored logo file. Returns True if a file was removed."""
    path = hoa_logo_path(logo_filename)
    if not path.exists():
        return False
    path.unlink()
    return True


__all__ = [
    "ALLOWED_LOGO_EXTENSIONS",
    "UnsupportedLogoFileType",
    "save_hoa_logo",
    "hoa_logo_path",
    "hoa_logo_exists",
    "delete_hoa_logo",
]
