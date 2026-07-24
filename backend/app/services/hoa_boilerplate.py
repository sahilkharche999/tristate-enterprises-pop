"""Per-HOA boilerplate override helpers (hoa-boilerplate-workbench).

Rich-text-editor slots: cover_letter_intro, enclosed_documents_list,
cover_letter_closing. Extend SLOT_REGISTRY when adding slots. The §5300
special-assessment disclosure and civil-code citation list are intentionally
NOT in this registry — they stay template-owned (see cover_letter.html).
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from . import boilerplate_sanitize, boilerplate_variables

# Human labels for the workbench UI. Add a slot here + template branch to extend.
SLOT_REGISTRY: dict[str, str] = {
    "cover_letter_intro": "Cover letter intro",
    "enclosed_documents_list": "Enclosed documents list",
    "cover_letter_closing": "Cover letter closing",
}

REFERENCE_MAX_BYTES = 25 * 1024 * 1024  # same order as disclosure appendices


class UnknownBoilerplateSlot(ValueError):
    """Raised when a write payload includes a slot id outside SLOT_REGISTRY."""


class UnknownBoilerplateToken(ValueError):
    """Raised when a slot's HTML references a data-var token outside TOKEN_CATALOG."""


def empty_boilerplate() -> dict[str, Optional[str]]:
    """All registry keys present (StrictUndefined-safe); values None."""
    return {slot: None for slot in SLOT_REGISTRY}


def parse_overrides_json(raw: Optional[str]) -> dict[str, Optional[str]]:
    """Parse stored JSON into a full registry dict. Unknown keys ignored on read.

    Legacy alias: rows written before the slot registry expanded stored the
    intro under ``cover_letter_body``. Map it onto ``cover_letter_intro`` so
    those rows keep rendering unchanged (an explicit ``cover_letter_intro``
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


def merge_overrides(
    existing_raw: Optional[str],
    updates: Mapping[str, Any],
) -> dict[str, Optional[str]]:
    """Merge write payload into existing overrides.

    - Unknown keys → UnknownBoilerplateSlot
    - null or "" → clear that slot
    - other values → nh3-sanitize (allowlist: bold/lists/indent + variable
      chips), reject unknown `data-var` tokens (UnknownBoilerplateToken),
      then store the sanitized HTML.
    """
    current = parse_overrides_json(existing_raw)
    for key, value in updates.items():
        if key not in SLOT_REGISTRY:
            raise UnknownBoilerplateSlot(f"Unknown boilerplate slot: {key!r}")
        if value is None:
            current[key] = None
            continue
        text = str(value).strip()
        if not text:
            current[key] = None
            continue
        sanitized = boilerplate_sanitize.sanitize_slot_html(text)
        unknown = boilerplate_variables.find_unknown_tokens(sanitized)
        if unknown:
            raise UnknownBoilerplateToken(
                f"Unknown variable token(s) in slot {key!r}: {', '.join(unknown)}"
            )
        current[key] = sanitized
    return current


def serialize_overrides(overrides: Mapping[str, Optional[str]]) -> str:
    """Persist only non-empty slots (compact JSON)."""
    compact = {
        k: v for k, v in overrides.items() if k in SLOT_REGISTRY and v
    }
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def slots_for_api(overrides: Mapping[str, Optional[str]]) -> list[dict[str, Any]]:
    """Shape for GET /settings/boilerplate."""
    return [
        {
            "id": slot_id,
            "label": label,
            "value": overrides.get(slot_id) or "",
            "is_override": bool(overrides.get(slot_id)),
        }
        for slot_id, label in SLOT_REGISTRY.items()
    ]


def resolved_for_compile(raw: Optional[str]) -> dict[str, Optional[str]]:
    """Full registry dict for Jinja context + finalize freeze."""
    return parse_overrides_json(raw)


def for_render(
    *,
    use_snapshots: bool,
    frozen: Optional[Mapping[str, Any]],
    live_raw: Optional[str],
) -> dict[str, Optional[str]]:
    """Resolve boilerplate for a package render.

    Snapshot branch: use frozen ``compile_context["boilerplate_overrides"]`` only
    (ignore live HOASettings). Live branch: parse ``live_raw`` from settings.
    """
    if use_snapshots:
        out = empty_boilerplate()
        if isinstance(frozen, dict):
            for key in out:
                val = frozen.get(key)
                if val not in (None, ""):
                    out[key] = str(val)
        return out
    return resolved_for_compile(live_raw)
