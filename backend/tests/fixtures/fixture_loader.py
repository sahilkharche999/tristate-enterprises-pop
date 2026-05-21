"""Fixture loader for operator-verified expected values.

Every fixture file under ``backend/tests/fixtures/dre_expected/``,
``dre_extraction_runs/``, ``budgets/``, and ``expected_calc/`` SHALL
include a ``_meta`` header with at minimum:

    {
        "_meta": {
            "source_document": "DRE/<filename>.pdf",
            "source_pages": [14, 15],
            "operator_verified_by": "name or email",
            "verified_at": "ISO-8601 timestamp",
            "notes": "free text"
        },
        "data": { ... actual fixture content ... }
    }

``load_fixture`` enforces ``_meta.operator_verified_by`` non-empty to
prevent tests from silently encoding AI-only values as ground truth.
When prompts or models update, re-extract sample DREs, diff against
fixture, operator re-confirms, fixture is rewritten — never overwrite
without an operator name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FIXTURE_ROOT = Path(__file__).parent


class FixtureMetadataError(ValueError):
    """Raised when a fixture is missing required ``_meta`` fields."""


@dataclass
class FixtureMeta:
    source_document: str
    source_pages: list[int]
    operator_verified_by: str
    verified_at: str
    notes: str = ""


@dataclass
class LoadedFixture:
    """Result of loading a fixture: ``meta`` plus the actual ``data`` payload."""

    meta: FixtureMeta
    data: Any
    path: Path


def load_fixture(relative_path: str) -> LoadedFixture:
    """Load a JSON fixture relative to ``backend/tests/fixtures/``.

    Raises ``FixtureMetadataError`` if ``_meta.operator_verified_by`` is
    missing/empty — this is the safety check that keeps unverified AI
    output from becoming silent ground truth.
    """
    path = FIXTURE_ROOT / relative_path
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {path}")

    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)

    if not isinstance(payload, dict) or "_meta" not in payload or "data" not in payload:
        raise FixtureMetadataError(
            f"Fixture {path} must be a dict with '_meta' and 'data' keys"
        )

    meta = payload["_meta"]
    operator = (meta.get("operator_verified_by") or "").strip()
    if not operator:
        raise FixtureMetadataError(
            f"Fixture {path} has empty _meta.operator_verified_by; refusing "
            "to load — unverified AI extractions must not be used as ground "
            "truth. Have an operator review and sign off, then set the field."
        )

    return LoadedFixture(
        meta=FixtureMeta(
            source_document=meta.get("source_document", ""),
            source_pages=list(meta.get("source_pages", [])),
            operator_verified_by=operator,
            verified_at=meta.get("verified_at", ""),
            notes=meta.get("notes", ""),
        ),
        data=payload["data"],
        path=path,
    )
