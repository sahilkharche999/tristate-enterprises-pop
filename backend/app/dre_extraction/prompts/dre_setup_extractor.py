"""Prompt 1 — DRE Visual Assessment Setup Extractor.

Source of truth: ``dre_setup_extractor.txt`` next to this file, which
is a verbatim copy of the Prompt 1 block in
``openspec/changes/dre-driven-assessment-engine/prompts.md``.

Module-level constants:
- ``PROMPT_TEXT`` — full prompt as a string
- ``PROMPT_SHA256`` — hex digest, recorded on every ``DREExtractionRun``
  for prompt-version auditing
- ``PROMPT_VERSION`` — bumped manually when the .txt file changes
"""

from __future__ import annotations

import hashlib
from pathlib import Path


_PROMPT_FILE = Path(__file__).with_suffix(".txt")
PROMPT_TEXT: str = _PROMPT_FILE.read_text(encoding="utf-8")
PROMPT_SHA256: str = hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()
PROMPT_VERSION: str = "2.4.0"
