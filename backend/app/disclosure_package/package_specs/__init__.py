"""Disclosure-package spec registry.

The DRE-driven assessment engine uses one universal template chain
(``STANDARD_PACKAGE_SPEC``) for every HOA. Per-HOA variance lives in
DB tables (properties, hoa_settings, assessment_setups, allocation_pools,
reserve_study_extractions, appendix_documents) — never in code.

``OLD_MILL_2026`` is retained as a backward-compat alias of
``STANDARD_PACKAGE_SPEC`` while older callers migrate.
"""
from __future__ import annotations

from .resolver import UnsupportedHOAError, resolve, template_for_setup_type
from .standard import OLD_MILL_2026, STANDARD_PACKAGE_SPEC

SPECS = {
    "standard": STANDARD_PACKAGE_SPEC,
    "old_mill": STANDARD_PACKAGE_SPEC,
}

__all__ = [
    "SPECS",
    "OLD_MILL_2026",
    "STANDARD_PACKAGE_SPEC",
    "UnsupportedHOAError",
    "resolve",
    "template_for_setup_type",
]
