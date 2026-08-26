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
)

from .schemas import PromptAllocationMethod


# -- allocation_method -----------------------------------------------------


@dataclass(frozen=True)
class AllocationMethodMapping:
    """Result of mapping Prompt 1's ``allocation_method`` to internal enum.

    Some prompt values are "syntactic sugar" that collapse to an
    internal method plus a scope/denominator hint:

    - ``parking_space`` → ``equal`` over ``parking_users`` scope
    - ``custom_factor`` → unresolved (never square_footage)
    - ``category`` → ``ownership_percentage`` (category share encoded as pct)

    ``needs_review=True`` means the operator must confirm before live.
    ``promote_as_unresolved=True`` writes ``allocation_method='unresolved'``
    and an allocation-resolution record instead of guessing an engine method.
    """

    internal_method: Optional[AllocationMethod]
    forced_scope: Optional[RecipientScope] = None
    forced_denominator_source: Optional[str] = None
    needs_review: bool = False
    review_note: Optional[str] = None
    promote_as_unresolved: bool = False


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
        internal_method=None,
        needs_review=True,
        promote_as_unresolved=True,
        review_note=(
            "Prompt emitted 'custom_factor': an external or DRE-referenced "
            "schedule. Resolve to ownership_percentage when every participant "
            "has ownership percent; never infer square_footage."
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
