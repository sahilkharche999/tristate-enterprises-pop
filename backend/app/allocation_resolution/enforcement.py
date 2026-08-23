"""Feature-flag helpers for allocation-resolution final-PDF blocking."""

from __future__ import annotations

from app.config import settings

from .schemas import EnforcementLevel


VALID_LEVELS = frozenset({"off", "new_governing_docs", "all_final_packages"})


def enforcement_level() -> EnforcementLevel:
    raw = str(getattr(settings, "ALLOCATION_RESOLUTION_ENFORCEMENT", "") or "").strip()
    if raw in VALID_LEVELS:
        return raw  # type: ignore[return-value]
    return "new_governing_docs"


def should_block_final(*, has_blocking_issues: bool, has_new_unresolved: bool) -> bool:
    """Return whether final generation is blocked for this property.

    ``new_governing_docs`` only blocks unresolved records created by the
    new promotion path (source='promotion'), not brownfield migration flags.
    ``all_final_packages`` blocks any blocking readiness issue.
    """
    if not has_blocking_issues:
        return False
    level = enforcement_level()
    if level == "off":
        return False
    if level == "all_final_packages":
        return True
    return has_new_unresolved
