"""Shared prompt sidecar loader for DRE prompt modules."""

from __future__ import annotations

import hashlib
from pathlib import Path


def load_prompt_sidecar(module_file: str, *, version: str) -> tuple[str, str, str]:
    """Load ``Path(module_file).with_suffix('.txt')`` and return (text, sha256, version).

    ``version`` is caller-supplied and must be bumped manually when the
    sidecar text changes (audit trail is independent of content hash).
    """
    prompt_file = Path(module_file).with_suffix(".txt")
    text = prompt_file.read_text(encoding="utf-8")
    sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, sha256, version
