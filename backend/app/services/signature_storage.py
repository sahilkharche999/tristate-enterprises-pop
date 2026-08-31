"""Firm + per-HOA wet-ink signature images for the cover letter closer."""
from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path
from typing import Optional

_FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")

ALLOWED_SIGNATURE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class UnsupportedSignatureFileType(ValueError):
    """Raised when an uploaded signature is not a raster scan we can embed."""


def _storage_root() -> Path:
    from app.config import settings

    root = Path(settings.BUDGET_STORAGE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sanitize_filename(name: str) -> str:
    stem = Path(name).name
    cleaned = _FILENAME_SANITIZE_RE.sub("_", stem).strip("._")
    return cleaned or "signature"


def _write_atomic_bytes(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    temp_path.replace(destination)


def _extension(original_filename: str) -> str:
    sanitized = _sanitize_filename(original_filename)
    extension = Path(sanitized).suffix.lower()
    if extension not in ALLOWED_SIGNATURE_EXTENSIONS:
        raise UnsupportedSignatureFileType(
            f"Unsupported signature file type {extension!r}; allowed: "
            f"{sorted(ALLOWED_SIGNATURE_EXTENSIONS)}"
        )
    return extension


def _replace_stale(directory: Path, keep: Path) -> None:
    for existing_ext in ALLOWED_SIGNATURE_EXTENSIONS:
        stale = directory / f"signature{existing_ext}"
        if stale.exists() and stale != keep:
            stale.unlink()


def save_firm_signature(*, file_bytes: bytes, original_filename: str) -> str:
    extension = _extension(original_filename)
    relative = Path("signatures") / "firm" / f"signature{extension}"
    absolute = _storage_root() / relative
    _replace_stale(absolute.parent, absolute)
    _write_atomic_bytes(absolute, file_bytes)
    return str(relative)


def save_hoa_signature(
    *, property_id: int, file_bytes: bytes, original_filename: str
) -> str:
    extension = _extension(original_filename)
    relative = (
        Path("signatures") / "hoa" / str(int(property_id)) / f"signature{extension}"
    )
    absolute = _storage_root() / relative
    _replace_stale(absolute.parent, absolute)
    _write_atomic_bytes(absolute, file_bytes)
    return str(relative)


def signature_path(filename: str) -> Path:
    return _storage_root() / filename


def signature_exists(filename: Optional[str]) -> bool:
    return bool(filename and signature_path(filename).exists())


def delete_signature(filename: Optional[str]) -> bool:
    if not filename:
        return False
    path = signature_path(filename)
    if not path.exists():
        return False
    path.unlink()
    return True


def find_firm_signature_on_disk() -> Optional[str]:
    for ext in ALLOWED_SIGNATURE_EXTENSIONS:
        relative = Path("signatures") / "firm" / f"signature{ext}"
        if (_storage_root() / relative).exists():
            return str(relative)
    return None


def resolve_signature_filename(
    *,
    hoa_filename: Optional[str] = None,
    firm_filename: Optional[str] = None,
) -> Optional[str]:
    if signature_exists(hoa_filename):
        return hoa_filename
    if signature_exists(firm_filename):
        return firm_filename
    return find_firm_signature_on_disk()


def signature_data_uri(filename: Optional[str]) -> Optional[str]:
    if not signature_exists(filename):
        return None
    path = signature_path(filename)
    mime = _MIME_TYPES.get(path.suffix.lower())
    if mime is None:
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def inject_signature_image(html: str, data_uri: Optional[str]) -> str:
    """Place the scan above signed-by in the closer. No-op when missing."""
    if not data_uri or "letter-signature-image" in html:
        return html
    img = f'<img class="letter-signature-image" src="{data_uri}" alt="" />'
    marker = '<div class="letter-signature">'
    if marker in html:
        return html.replace(marker, f"{marker}{img}", 1)
    return html


__all__ = [
    "ALLOWED_SIGNATURE_EXTENSIONS",
    "UnsupportedSignatureFileType",
    "save_firm_signature",
    "save_hoa_signature",
    "signature_path",
    "signature_exists",
    "delete_signature",
    "find_firm_signature_on_disk",
    "resolve_signature_filename",
    "signature_data_uri",
    "inject_signature_image",
]
