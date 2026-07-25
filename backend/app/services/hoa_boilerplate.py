"""Retired three-slot cover-letter overrides — read path only.

`add-full-document-editor` replaced these slots with full narrative documents
(`narrative_content.py`). The API, the compile context, and the templates no
longer reference them.

What survives, and why: `hoa_settings.boilerplate_overrides_json` is kept as
the rollback path for one release (see design.md's Migration Plan), and the
one-time migration in `database.migrate_legacy_boilerplate_slots` reads it
through `parse_overrides_json` to compose each HOA's saved wording into a
`cover_letter` override. Once that release ships, this module and the column
can both go.

`REFERENCE_MAX_BYTES` is unrelated to the slots — it bounds the workbench's
reference-PDF upload, which is still a live feature.
"""
from __future__ import annotations

import json
from typing import Optional

# The retired slots, in the order they appeared in the letter. Retained so the
# migration can find stored values; nothing writes these any more.
SLOT_REGISTRY: dict[str, str] = {
    "cover_letter_intro": "Cover letter intro",
    "enclosed_documents_list": "Enclosed documents list",
    "cover_letter_closing": "Cover letter closing",
}

REFERENCE_MAX_BYTES = 25 * 1024 * 1024  # same order as disclosure appendices


def empty_boilerplate() -> dict[str, Optional[str]]:
    """All registry keys present; values None."""
    return {slot: None for slot in SLOT_REGISTRY}


def parse_overrides_json(raw: Optional[str]) -> dict[str, Optional[str]]:
    """Parse stored JSON into a full registry dict. Unknown keys ignored on read.

    Legacy alias: rows written before the slot registry expanded stored the
    intro under ``cover_letter_body``. Map it onto ``cover_letter_intro`` so
    the migration finds those rows too (an explicit ``cover_letter_intro``
    key, if present, always wins).
    """
    out = empty_boilerplate()
    if not raw or not str(raw).strip():
        return out
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return out
    if not isinstance(data, dict):
        return out
    if "cover_letter_body" in data and "cover_letter_intro" not in data:
        data = dict(data)
        data["cover_letter_intro"] = data["cover_letter_body"]
    for key, value in data.items():
        if key not in SLOT_REGISTRY:
            continue
        if value is None:
            out[key] = None
            continue
        text = str(value).strip()
        out[key] = text if text else None
    return out
