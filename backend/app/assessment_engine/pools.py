"""Pool-level allocation math.

Each allocator is unit-agnostic Decimal arithmetic — the caller passes
a pool total (annual or monthly, the function doesn't care) and gets
back per-recipient shares in the same unit. The engine main loop is
responsible for the annual→monthly conversion at the pool boundary
and for filtering recipients by ``pool.recipient_scope`` before
invoking these functions.

All four allocators preserve full Decimal precision. Rounding is a
recipient-total concern (handled one level up), never a pool-level
concern — rounding pools separately would compound rounding error.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

from .errors import EngineSetupError
from .schemas import RecipientReference


OWNERSHIP_PERCENT_TOLERANCE = Decimal("0.001")

RecipientKey = tuple[str, int]


class MissingSpecifiedValue(EngineSetupError):
    """Raised when a ``specified_value`` pool lacks an
    ``AssessmentUnitPoolAllocation`` row for a recipient in its scope.

    Preflight surfaces this so the operator fills the gap in the
    Review Workbench before package generation.
    """

    def __init__(self, unit_id: int, pool_key: str) -> None:
        self.unit_id = unit_id
        self.pool_key = pool_key
        super().__init__(
            f"Missing specified value for unit {unit_id} in pool '{pool_key}'"
        )


def _require_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(
            f"{name} must be Decimal, got {type(value).__name__} "
            f"({value!r}); float arithmetic is forbidden in the engine"
        )
    return value


def equal_allocation(pool_total: Decimal, recipient_count: int) -> Decimal:
    """Equal split: every recipient in scope receives ``pool_total / count``.

    Used by Old Mill (single pool over 279 units) and Esprit Park's
    base-assessment pool. The full Decimal precision is preserved;
    cents-rounding happens at the recipient-total step.
    """
    _require_decimal("pool_total", pool_total)
    if not isinstance(recipient_count, int):
        raise TypeError(
            f"recipient_count must be int, got {type(recipient_count).__name__}"
        )
    if recipient_count <= 0:
        raise ValueError(
            f"recipient_count must be > 0, got {recipient_count}"
        )
    return pool_total / Decimal(recipient_count)


def square_footage_allocation(
    pool_total: Decimal,
    denominator: Decimal,
    recipients: Sequence[RecipientReference],
) -> dict[RecipientKey, Decimal]:
    """Sqft-weighted split: ``factor × recipient.square_feet`` per recipient.

    ``denominator`` is the DRE-frozen total square footage — passed in
    verbatim, NEVER recomputed from the recipient list. The caller is
    responsible for emitting ``DenominatorMismatchWarning`` if a
    recomputation would yield a different value; this function trusts
    the denominator it is given.

    For ``group`` recipients, ``.square_feet`` is the avg per unit;
    the returned value is the per-unit share. The caller multiplies
    by ``recipient.unit_count`` to get the group total. For ``unit``
    recipients, ``.square_feet`` is the unit's own sqft and the
    returned value is the unit's share directly.
    """
    _require_decimal("pool_total", pool_total)
    _require_decimal("denominator", denominator)
    if denominator <= 0:
        raise ValueError(
            f"denominator must be > 0, got {denominator}; DRE values "
            "cannot be zero for sqft-weighted pools"
        )

    factor = pool_total / denominator
    out: dict[RecipientKey, Decimal] = {}
    for r in recipients:
        if r.square_feet is None:
            raise ValueError(
                f"recipient {r.ref_type}:{r.ref_id} ({r.label}) is in a "
                "square_footage pool but has no square_feet recorded; "
                "DRE extraction or operator entry must supply it"
            )
        out[(r.ref_type, r.ref_id)] = r.square_feet * factor
    return out


def ownership_percentage_allocation(
    pool_total: Decimal,
    recipients: Sequence[RecipientReference],
) -> tuple[dict[RecipientKey, Decimal], list[str]]:
    """Ownership-weighted split: ``pool_total × recipient.ownership_percent``.

    DRE-recorded percentages are used verbatim regardless of whether
    they sum cleanly to 1.0. If the sum drifts by more than
    ``OWNERSHIP_PERCENT_TOLERANCE`` (0.001), a non-blocking warning
    string is appended to the returned warnings list.

    Returns ``(allocations, warnings)``.
    """
    _require_decimal("pool_total", pool_total)

    out: dict[RecipientKey, Decimal] = {}
    pct_sum = Decimal("0")
    for r in recipients:
        if r.ownership_percent is None:
            raise ValueError(
                f"recipient {r.ref_type}:{r.ref_id} ({r.label}) is in an "
                "ownership_percentage pool but has no ownership_percent "
                "recorded; DRE extraction or operator entry must supply it"
            )
        out[(r.ref_type, r.ref_id)] = pool_total * r.ownership_percent
        pct_sum += r.ownership_percent

    warnings: list[str] = []
    drift = abs(pct_sum - Decimal("1"))
    if drift > OWNERSHIP_PERCENT_TOLERANCE:
        warnings.append(
            f"ownership_percent values in pool sum to {pct_sum} "
            f"(drift {drift} > tolerance {OWNERSHIP_PERCENT_TOLERANCE}); "
            "DRE values preserved verbatim"
        )
    return out, warnings


def specified_value_allocation(
    pool_key: str,
    recipients: Sequence[RecipientReference],
    lookup: Mapping[tuple[int, str], Decimal],
) -> dict[RecipientKey, Decimal]:
    """Per-unit dollar lookup: each (unit, pool_key) returns the row's
    ``AssessmentUnitPoolAllocation.specified_monthly_amount``.

    The lookup mapping must be pre-fetched by the caller (engine main)
    from the AssessmentUnitPoolAllocation table — same code path for
    ``source='dre'`` and ``source='manual'`` rows; provenance is a
    separate concern handled at the audit-log level.

    Group recipients are rejected — ``specified_value`` is a per-unit
    concept; grouped HOAs do not use this allocation method.

    Raises ``MissingSpecifiedValue`` if any unit recipient lacks a row.
    """
    out: dict[RecipientKey, Decimal] = {}
    for r in recipients:
        if r.ref_type != "unit":
            raise ValueError(
                f"specified_value pool received {r.ref_type} recipient "
                f"{r.ref_id} ({r.label}); this allocation method is "
                "per-unit only (groups use equal or sqft)"
            )
        key = (r.ref_id, pool_key)
        if key not in lookup:
            raise MissingSpecifiedValue(unit_id=r.ref_id, pool_key=pool_key)
        out[(r.ref_type, r.ref_id)] = lookup[key]
    return out
