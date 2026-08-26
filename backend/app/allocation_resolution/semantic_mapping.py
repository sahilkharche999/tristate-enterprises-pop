"""Semantic matching of governing-document categories to budget lines."""

from __future__ import annotations

from typing import Literal

from app.services.assessment_budget_mapping_rule_service import (
    _semantic_label_tokens,
    normalize_budget_label,
)


MatchKind = Literal["exact", "partial", "combined", "missing", "unrelated"]


def classify_label_match(category: str, line_label: str) -> MatchKind:
    """Classify how a declared category relates to a source budget line.

    A narrow category such as ``gas`` is a *combined* match against
    ``Electricity & Gas``. That is not an automatic assignment — the
    operator still confirms the whole source amount, and may optionally
    split it.
    """
    cat = normalize_budget_label(category)
    line = normalize_budget_label(line_label)
    if not cat or not line:
        return "missing" if cat else "unrelated"

    cat_tokens = _semantic_label_tokens(cat)
    line_tokens = _semantic_label_tokens(line)
    if not cat_tokens:
        return "unrelated"
    if not line_tokens:
        return "missing"
    if cat_tokens == line_tokens or cat == line:
        return "exact"
    if cat_tokens < line_tokens:
        return "combined"
    if line_tokens < cat_tokens:
        return "partial"
    if cat_tokens & line_tokens:
        return "partial"
    return "unrelated"


def is_automatic_full_line_match(category: str, line_label: str) -> bool:
    """True only when the category may consume the entire source line."""
    return classify_label_match(category, line_label) == "exact"
