"""Prompt 2 — Annual Budget → Saved DRE Setup Mapper.

Source of truth: ``budget_pool_mapper.txt`` next to this file, which
is a verbatim copy of the Prompt 2 block in
``openspec/changes/dre-driven-assessment-engine/prompts.md``.

Module-level constants:
- ``PROMPT_TEXT`` — full prompt as a string
- ``PROMPT_SHA256`` — hex digest, recorded on every mapping run
- ``PROMPT_VERSION`` — bumped manually when the .txt file changes
"""

from __future__ import annotations

import hashlib
from pathlib import Path


_PROMPT_FILE = Path(__file__).with_suffix(".txt")
PROMPT_TEXT: str = _PROMPT_FILE.read_text(encoding="utf-8")
PROMPT_SHA256: str = hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()
PROMPT_VERSION: str = "1.0.0"
