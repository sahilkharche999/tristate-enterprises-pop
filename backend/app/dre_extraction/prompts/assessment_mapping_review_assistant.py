"""Prompt 3 — Assessment Mapping Review AI Assistant.

Source of truth: ``assessment_mapping_review_assistant.txt`` next to
this file.

Module-level constants:
- ``PROMPT_TEXT`` — full prompt as a string
- ``PROMPT_SHA256`` — hex digest, recorded in API audit payloads
- ``PROMPT_VERSION`` — bumped manually when the .txt file changes
"""

from __future__ import annotations

import hashlib
from pathlib import Path


_PROMPT_FILE = Path(__file__).with_suffix(".txt")
PROMPT_TEXT: str = _PROMPT_FILE.read_text(encoding="utf-8")
PROMPT_SHA256: str = hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()
PROMPT_VERSION: str = "1.0.0"
