"""Prompt for GL merge suggestions in budget drafts."""

from __future__ import annotations

import hashlib
from pathlib import Path


_PROMPT_FILE = Path(__file__).with_suffix(".txt")
PROMPT_TEXT: str = _PROMPT_FILE.read_text(encoding="utf-8")
PROMPT_SHA256: str = hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()
PROMPT_VERSION: str = "1.0.0"
