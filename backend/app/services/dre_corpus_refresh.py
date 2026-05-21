"""Periodic DRE-corpus refresh process documentation (Phase 3 task 92).

When the Gemini Vision prompt is updated, or when Google ships a new
``gemini-flash`` model version, the operator must re-extract the test
corpus and confirm the output still matches the operator-verified
ground truth. This module documents the workflow + provides the
helpers a future refresh CLI would invoke.

## The refresh workflow

1. **Bump the prompt SHA**: edit ``dre_extraction/prompts/dre_setup_extractor.txt``
   (any change re-hashes the SHA via the module-load hash). Increment
   ``PROMPT_VERSION`` in ``prompts/dre_setup_extractor.py`` so the
   ``dre_extraction_runs.prompt_version`` audit field reflects the bump.

2. **Re-extract every fixture DRE**: invoke ``run_dre_extraction`` against
   each PDF in ``tests/fixtures/dre_expected/*/source.pdf``. Each call
   writes a new ``DREExtractionRun`` row with the updated prompt SHA.

3. **Diff against the fixture's ground-truth JSON**: for every entity
   the fixture covers (assessment_setup, allocation_pools, etc.), diff
   the freshly-extracted value against ``tests/fixtures/dre_expected/
   <hoa>/expected.json``.

4. **Operator sign-off**: any diff requires the operator to view the
   source page in the Review Workbench, confirm the new value is
   correct, and either:
     - Accept the new value → update the fixture's expected.json AND
       bump its ``_meta.operator_verified_at``.
     - Reject the new value → revert the prompt edit (it produced a
       regression).

5. **Re-record SHA in operator audit**: ``_meta.operator_verified_by``
   + ``_meta.operator_verified_at`` + ``_meta.operator_verified_prompt_sha256``
   are stamped on every fixture file. ``fixture_loader.py`` rejects
   fixtures without these audit fields so a silent SHA bump can't
   silently update fixtures without sign-off.

The actual periodic-refresh CLI is intentionally NOT auto-running —
the user-facing copy ("re-extract sample DREs, diff against fixture,
operator re-confirms before fixture is updated") is operator-driven by
design. The helpers below give a refresh script the building blocks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FixtureDiff:
    """One field that differs between expected and freshly-extracted."""

    fixture_name: str
    field_path: str
    expected: object
    actual: object


def _walk_diffs(
    expected: object, actual: object, path: str = "",
) -> list[tuple[str, object, object]]:
    """Recursively diff two JSON-shaped values. Returns
    [(path, expected, actual), ...] for every leaf that differs.
    """
    out: list[tuple[str, object, object]] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        keys = sorted(set(expected.keys()) | set(actual.keys()))
        for k in keys:
            out.extend(_walk_diffs(expected.get(k), actual.get(k), f"{path}.{k}" if path else k))
    elif isinstance(expected, list) and isinstance(actual, list):
        for i in range(max(len(expected), len(actual))):
            e_i = expected[i] if i < len(expected) else None
            a_i = actual[i] if i < len(actual) else None
            out.extend(_walk_diffs(e_i, a_i, f"{path}[{i}]"))
    elif expected != actual:
        out.append((path or "<root>", expected, actual))
    return out


def diff_extraction_against_fixture(
    *,
    fixture_name: str,
    expected_json_path: Path,
    actual_extraction_json: dict,
) -> list[FixtureDiff]:
    """Return field-level diffs between a fixture's expected output and
    a freshly-extracted run. Empty list = no diff = prompt safe to ship.
    """
    expected = json.loads(expected_json_path.read_text(encoding="utf-8"))
    # Strip metadata audit envelope so we only diff the payload
    expected_payload = {k: v for k, v in expected.items() if not k.startswith("_")}

    raw_diffs = _walk_diffs(expected_payload, actual_extraction_json)
    return [
        FixtureDiff(
            fixture_name=fixture_name,
            field_path=path,
            expected=e,
            actual=a,
        )
        for path, e, a in raw_diffs
    ]


def fixture_audit_envelope(fixture_path: Path) -> Optional[dict]:
    """Return the ``_meta`` block from a fixture file.

    Used by the refresh CLI to verify operator sign-off is current.
    Returns None when ``_meta`` is missing (signals the fixture has
    never been operator-verified and cannot be trusted as ground truth).
    """
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload.get("_meta")


__all__ = [
    "FixtureDiff",
    "diff_extraction_against_fixture",
    "fixture_audit_envelope",
]
