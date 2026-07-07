"""Regression: special-assessment rows must carry every field the disclosure
templates read, so a basic operator-entered row does not crash the render.

The disclosure templates run under Jinja StrictUndefined — a missing key raises
`UndefinedError` (observed live: cover_letter.html `{{ sa.label }}` on a row that
only had `description`/`amount`). `_normalize_special_assessment_for_render` must
derive the canonical keys from any alias and default the rest.
"""
from __future__ import annotations

from app.disclosure_package.compiler import _normalize_special_assessment_for_render

# Every field the standard templates access on a special-assessment entry.
_REQUIRED_KEYS = {
    "status",
    "label",
    "amount_per_unit",
    "due_date",
    "display_language",
    "purpose",
    "frequency",
    "included_in_regular_monthly",
}


def test_basic_row_with_alias_keys_gets_all_template_fields():
    # The exact live-crash shape: no `label`, no `amount_per_unit`.
    out = _normalize_special_assessment_for_render(
        {"description": "Roof repair", "amount": 500, "due_date": "2026-06-01"}
    )
    assert _REQUIRED_KEYS <= set(out)
    assert out["label"] == "Roof repair"          # derived from `description`
    assert out["amount_per_unit"] == 500.0        # derived from `amount`
    assert out["status"] == "approved_scheduled"  # amount > 0


def test_empty_row_never_missing_a_key():
    out = _normalize_special_assessment_for_render({})
    assert _REQUIRED_KEYS <= set(out)
    assert out["label"] is None
    assert out["amount_per_unit"] == 0.0
    assert out["included_in_regular_monthly"] is False
    assert out["status"] == "none"


def test_non_numeric_amount_does_not_raise():
    out = _normalize_special_assessment_for_render({"amount_per_unit": "not-a-number"})
    assert out["amount_per_unit"] == 0.0


def test_canonical_keys_are_preserved():
    out = _normalize_special_assessment_for_render(
        {
            "label": "Elevator",
            "amount_per_unit": 250.5,
            "due_date": "2026-09-01",
            "frequency": "month",
            "included_in_regular_monthly": True,
        }
    )
    assert out["label"] == "Elevator"
    assert out["amount_per_unit"] == 250.5
    assert out["frequency"] == "month"
    assert out["included_in_regular_monthly"] is True
