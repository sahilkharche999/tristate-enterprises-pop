"""CC&R extraction prompt loader and hash constants."""

from __future__ import annotations

import hashlib
from pathlib import Path

_PROMPT_PATH = Path(__file__).parent / "ccr_policy_extractor.txt"

CCR_POLICY_EXTRACTOR_PROMPT: str = _PROMPT_PATH.read_text(encoding="utf-8")

CCR_POLICY_EXTRACTOR_PROMPT_VERSION: str = "1.3"

CCR_POLICY_EXTRACTOR_PROMPT_SHA256: str = hashlib.sha256(
    CCR_POLICY_EXTRACTOR_PROMPT.encode("utf-8")
).hexdigest()

__all__ = [
    "CCR_POLICY_EXTRACTOR_PROMPT",
    "CCR_POLICY_EXTRACTOR_PROMPT_VERSION",
    "CCR_POLICY_EXTRACTOR_PROMPT_SHA256",
]
