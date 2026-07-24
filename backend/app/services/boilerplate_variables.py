"""Whitelist variable-token catalog + resolver for boilerplate rich-text slots.

Operators insert tokens in the rich-text editor as chips that serialize to
``<span data-var="NAME"></span>``. They are resolved here from the disclosure
compute context at compile time via a fixed whitelist dict — NEVER through
Jinja/``Environment.from_string`` — so operator-authored slot content can
never reach template evaluation. A token whose name isn't in
``TOKEN_CATALOG`` is never rendered literally; it is caught by
``find_unknown_tokens`` at save time and by the preflight gate at compile
time, and ``resolve`` raises rather than emit it if it ever reaches this far.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

from markupsafe import escape

# id -> human label, for the frontend "insert variable" picker.
TOKEN_CATALOG: dict[str, str] = {
    "hoa_name": "HOA name",
    "fiscal_year": "Fiscal year",
    "effective_date": "Budget effective date",
    "assessment_line": "Assessment amount / variance sentence",
    "reserve_monthly_contribution": "Monthly reserve contribution",
    "management_company": "Management company name",
    "signed_by": "Signed by",
}

# Matches an (empty) variable-chip span, tolerant of attribute order and any
# extra attributes the editor's NodeView adds (e.g. contenteditable="false").
_VAR_SPAN_RE = re.compile(
    r'<span\b[^>]*\bdata-var="([^"]*)"[^>]*>.*?</span>',
    re.IGNORECASE | re.DOTALL,
)


class UnresolvedBoilerplateToken(ValueError):
    """Raised by `resolve` when a `data-var` name isn't in TOKEN_CATALOG."""


def _money(value: Any) -> str:
    if value is None:
        return "0.00"
    try:
        return "{:,.2f}".format(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return "0.00"


def build_var_map(
    *,
    hoa: Any,
    fiscal_year: int,
    hoa_settings: Optional[Mapping[str, Any]],
    computed: Mapping[str, Any],
    matrix: Any = None,
) -> dict[str, str]:
    """Build the {token_name: resolved_value} map for one compile pass.

    Sourced from the same facts ``cover_letter.html`` already branches on
    (``matrix.recipient_grain`` / ``presentation_facts.assessments_vary`` /
    ``assessment_change_phrase``) so the resolver never invents a second
    source of truth for whether assessments vary by unit.
    """
    hoa_settings = hoa_settings or {}
    presentation = computed.get("presentation_facts") or {}
    matrix_grain = getattr(matrix, "recipient_grain", None) if matrix is not None else None
    assessments_vary = matrix_grain in ("group", "unit") or bool(
        presentation.get("assessments_vary")
    )

    if assessments_vary:
        assessment_line = (
            f"Monthly assessments for {fiscal_year} vary by ownership interest "
            "and are shown in the assessment schedule included in this package."
        )
    else:
        phrase = (
            presentation.get("assessment_change_phrase")
            or computed.get("assessment_change_phrase")
            or ""
        )
        monthly = computed.get("monthly_assessment_per_unit_current")
        assessment_line = (
            f"The monthly assessment per unit for {fiscal_year} {phrase} "
            f"${_money(monthly)}."
        )

    return {
        "hoa_name": str(getattr(hoa, "name", "") or ""),
        "fiscal_year": str(fiscal_year),
        "effective_date": f"January 1, {fiscal_year}",
        "assessment_line": assessment_line,
        "reserve_monthly_contribution": (
            f"${_money(computed.get('monthly_replacement_contribution_total'))}"
        ),
        "management_company": str(hoa_settings.get("management_company") or ""),
        "signed_by": str(hoa_settings.get("letter_signed_by") or ""),
    }


def find_unknown_tokens(html: Optional[str]) -> list[str]:
    """Names referenced by `data-var` spans that aren't in TOKEN_CATALOG."""
    if not html:
        return []
    names = _VAR_SPAN_RE.findall(html)
    return sorted({name for name in names if name not in TOKEN_CATALOG})


def resolve(html: Optional[str], var_map: Mapping[str, str]) -> Optional[str]:
    """Replace every `data-var` span with its HTML-escaped resolved value."""
    if not html:
        return html

    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in var_map:
            raise UnresolvedBoilerplateToken(
                f"Unknown boilerplate token: {name!r}"
            )
        return str(escape(var_map[name]))

    return _VAR_SPAN_RE.sub(_sub, html)
