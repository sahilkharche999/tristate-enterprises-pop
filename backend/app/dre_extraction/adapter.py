"""Adapter: Prompt-vocab enums → internal data model enums.

The prompts use prompt-facing names (``fixed_equal``,
``multi_pool_combination``, ``parking_space``, etc.); the internal data
model uses the engine's canonical enums (``fixed``, ``per_unit``,
``equal`` + ``recipient_scope='parking_users'``, etc.). Mapping happens
at extraction time so the rest of the codebase sees only canonical
values.

Tables match ``openspec/changes/.../prompts.md`` §"Enum mapping" exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.assessment_engine import (
    AllocationMethod,
    RecipientScope,
    SetupType,
)

from .schemas import PromptAllocationMethod, PromptSetupType


# -- setup_type ------------------------------------------------------------


@dataclass(frozen=True)
class SetupTypeMapping:
    """Result of mapping Prompt 1's ``setup_type`` to internal enum.

    ``internal_setup_type`` is ``None`` when the prompt returned
    ``unknown_needs_review`` — the engine refuses to compute against
    that; the operator must pick a value in the Review Workbench
    before promotion to live.
    """

    internal_setup_type: Optional[SetupType]
    needs_review: bool
    review_note: Optional[str] = None


_SETUP_TYPE_TABLE: dict[str, SetupTypeMapping] = {
    "fixed_equal": SetupTypeMapping(internal_setup_type="fixed", needs_review=False),
    "grouped_category": SetupTypeMapping(
        internal_setup_type="grouped", needs_review=False
    ),
    "individual_unit": SetupTypeMapping(
        internal_setup_type="per_unit", needs_review=False
    ),
    "multi_pool_combination": SetupTypeMapping(
        internal_setup_type="per_unit",
        needs_review=True,
        review_note=(
            "Prompt classified multi_pool_combination → per_unit by default. "
            "Operator may switch display_mode to grouped in the Review "
            "Workbench when the multi-pool structure organizes around groups."
        ),
    ),
    "unknown_needs_review": SetupTypeMapping(
        internal_setup_type=None,
        needs_review=True,
        review_note="Prompt could not classify setup_type; operator must pick.",
    ),
}


def map_setup_type(prompt_value: PromptSetupType) -> SetupTypeMapping:
    return _SETUP_TYPE_TABLE[prompt_value]


# -- allocation_method -----------------------------------------------------


@dataclass(frozen=True)
class AllocationMethodMapping:
    """Result of mapping Prompt 1's ``allocation_method`` to internal enum.

    Some prompt values are "syntactic sugar" that collapse to an
    internal method plus a scope/denominator hint:

    - ``parking_space`` → ``equal`` over ``parking_users`` scope
    - ``custom_factor`` → ``square_footage`` with manual denominator
    - ``category`` → ``ownership_percentage`` (category share encoded as pct)

    ``needs_review=True`` means the operator must confirm before live.
    """

    internal_method: Optional[AllocationMethod]
    forced_scope: Optional[RecipientScope] = None
    forced_denominator_source: Optional[str] = None
    needs_review: bool = False
    review_note: Optional[str] = None


_ALLOCATION_METHOD_TABLE: dict[str, AllocationMethodMapping] = {
    "equal": AllocationMethodMapping(internal_method="equal"),
    "square_footage": AllocationMethodMapping(internal_method="square_footage"),
    "ownership_percentage": AllocationMethodMapping(
        internal_method="ownership_percentage"
    ),
    "category": AllocationMethodMapping(
        internal_method="ownership_percentage",
        review_note=(
            "Prompt emitted 'category' which collapses to ownership_percentage "
            "(category-share encoded as category-level pct)."
        ),
    ),
    "specified_value": AllocationMethodMapping(internal_method="specified_value"),
    "parking_space": AllocationMethodMapping(
        internal_method="equal",
        forced_scope="parking_users",
        review_note="Prompt emitted 'parking_space' → equal allocation over parking_users scope.",
    ),
    "custom_factor": AllocationMethodMapping(
        internal_method="square_footage",
        forced_denominator_source="manual",
        needs_review=True,
        review_note=(
            "Prompt emitted 'custom_factor' → square_footage with "
            "denominator_source='manual'; operator must verify denominator."
        ),
    ),
    "unknown": AllocationMethodMapping(
        internal_method=None,
        needs_review=True,
        review_note="Method unclear; Review Workbench must surface 'method unclear'.",
    ),
}


def map_allocation_method(prompt_value: PromptAllocationMethod) -> AllocationMethodMapping:
    return _ALLOCATION_METHOD_TABLE[prompt_value]
