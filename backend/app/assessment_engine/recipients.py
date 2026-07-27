"""Recipient resolution: filter a RecipientSet by a pool's scope.

The engine invokes this once per pool. The output is the actual list of
recipients that participate in that pool's allocation — input to the
pool math allocators.

Setup-type (grouped vs per-unit) is encoded in the recipient itself
(``ref_type``); this resolver does not branch on setup_type.

For grouped HOAs, recipients carry ``unit_count``. Equal and square-footage
allocators expand per-unit shares to **group totals** via ``× unit_count``.
Ownership allocation resolves weight form inside
``ownership_percentage_allocation`` (recipient_share vs per_unit_interest)
and also returns group totals for groups. For per-unit HOAs,
``unit_count`` is 1.
"""

from __future__ import annotations

from typing import Optional, Sequence, get_args

from .models import RecipientScope
from .schemas import RecipientReference, RecipientSet


_VALID_SCOPES: frozenset[str] = frozenset(get_args(RecipientScope))


class UnsupportedRecipientScope(ValueError):
    """Raised when a pool carries a scope value the engine doesn't know."""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        super().__init__(
            f"Unsupported recipient_scope '{scope}'; expected one of "
            f"{sorted(_VALID_SCOPES)}"
        )


def resolve_recipients(
    recipient_set: RecipientSet,
    scope: RecipientScope,
    *,
    custom_unit_ids: Optional[Sequence[int]] = None,
) -> list[RecipientReference]:
    """Return the recipients that participate in a pool with the given scope.

    - ``all_units``: every recipient (groups for grouped HOAs, units for
      per-unit HOAs).
    - ``residential_only`` / ``commercial_only``: per-unit only; filters
      by ``category``.
    - ``parking_users``: per-unit only; recipients with ``parking_spaces > 0``.
    - ``custom_unit_list``: per-unit only; recipients whose ``ref_id`` is
      in ``custom_unit_ids``. Caller must supply the list; unknown ids
      are silently skipped (the operator may delete a unit between setups).

    Input order is preserved in output.
    """
    if scope not in _VALID_SCOPES:
        raise UnsupportedRecipientScope(scope)

    recipients = recipient_set.recipients

    if scope == "all_units":
        return list(recipients)

    if scope == "residential_only":
        return [r for r in recipients if r.category == "residential"]

    if scope == "commercial_only":
        return [r for r in recipients if r.category == "commercial"]

    if scope == "parking_users":
        return [r for r in recipients if r.parking_spaces > 0]

    if scope == "custom_unit_list":
        if custom_unit_ids is None:
            raise ValueError(
                "custom_unit_list scope requires custom_unit_ids argument"
            )
        wanted = set(custom_unit_ids)
        return [r for r in recipients if r.ref_id in wanted]

    # Unreachable — _VALID_SCOPES check above guards every branch
    raise UnsupportedRecipientScope(scope)  # pragma: no cover
