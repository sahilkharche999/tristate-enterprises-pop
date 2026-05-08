"""Disclosure-package spec registry.

Phase 11 ships only the Old Mill 2026 spec (CONTEXT D-04). Phase 12+ adds
new HOAs by adding new spec modules under this package and registering them
in `SPECS`.
"""
from __future__ import annotations

from .old_mill import OLD_MILL_2026

SPECS = {"old_mill": OLD_MILL_2026}

__all__ = ["SPECS", "OLD_MILL_2026"]
