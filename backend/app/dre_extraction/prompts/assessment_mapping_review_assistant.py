"""Assessment mapping review assistant prompt."""

from __future__ import annotations

from ._load import load_prompt_sidecar

PROMPT_TEXT, PROMPT_SHA256, PROMPT_VERSION = load_prompt_sidecar(
    __file__, version="1.0.0"
)
