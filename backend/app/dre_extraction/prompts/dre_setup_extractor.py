"""Prompt 1 — DRE Visual Assessment Setup Extractor.

Source of truth: ``dre_setup_extractor.txt`` next to this file.
"""

from __future__ import annotations

from ._load import load_prompt_sidecar

PROMPT_TEXT, PROMPT_SHA256, PROMPT_VERSION = load_prompt_sidecar(
    __file__, version="2.4.0"
)
