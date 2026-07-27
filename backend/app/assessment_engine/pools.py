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


def resolve_ownership_weight_form(
    recipients: Sequence[RecipientReference],
) -> tuple[str, Decimal, Decimal, list[str]]:
    """Decide how ``ownership_percent`` should weight each recipient.

    ``ownership_percent`` is always a fraction (after promotion normalize).
    Two valid meanings exist for **group** recipients:

    - ``recipient_share``: the value is the whole group's share of the HOA
      (``Σ ownership_percent ≈ 1``). Weight = pct.
    - ``per_unit_interest``: the value is one unit's undivided interest
      (``Σ ownership_percent × unit_count ≈ 1``), as printed on many DREs /
      assessment schedules (e.g. Sharon Ridge 1.78% / 2.42% / 2.74%).
      Weight = pct × unit_count so the allocator returns **group totals**.

    For unit recipients ``unit_count`` is 1, so both forms coincide.

    Preference order when both sums are near 1: ``recipient_share`` (do not
    multiply by unit_count — that would over-allocate group-total data).

    Returns ``(form, bare_sum, weighted_sum, warnings)``.
    """
    bare_sum = Decimal("0")
    weighted_sum = Decimal("0")
    for r in recipients:
        if r.ownership_percent is None:
            raise ValueError(
                f"recipient {r.ref_type}:{r.ref_id} ({r.label}) is in an "
                "ownership_percentage pool but has no ownership_percent "
                "recorded; DRE extraction or operator entry must supply it"
            )
        bare_sum += r.ownership_percent
        weighted_sum += r.ownership_percent * Decimal(int(r.unit_count or 1))

    bare_drift = abs(bare_sum - Decimal("1"))
    weighted_drift = abs(weighted_sum - Decimal("1"))
    warnings: list[str] = []

    if bare_drift <= OWNERSHIP_PERCENT_TOLERANCE:
        form = "recipient_share"
    elif weighted_drift <= OWNERSHIP_PERCENT_TOLERANCE:
        form = "per_unit_interest"
        warnings.append(
            f"ownership_percent treated as per-unit interest "
            f"(Σ pct={bare_sum}, Σ pct×unit_count={weighted_sum}); "
            "group weights = ownership_percent × unit_count"
        )
    else:
        # Prefer the closer sum; default to recipient_share when tied so
        # legacy unit-grain / group-total behaviour is preserved.
        if weighted_drift < bare_drift:
            form = "per_unit_interest"
        else:
            form = "recipient_share"
        chosen_sum = weighted_sum if form == "per_unit_interest" else bare_sum
        chosen_drift = weighted_drift if form == "per_unit_interest" else bare_drift
        warnings.append(
            f"ownership_percent values in pool sum to bare={bare_sum} "
            f"weighted={weighted_sum} (chosen form={form}, sum={chosen_sum}, "
            f"drift {chosen_drift} > tolerance {OWNERSHIP_PERCENT_TOLERANCE}); "
            "DRE values preserved verbatim"
        )
    return form, bare_sum, weighted_sum, warnings


def ownership_weight_sum_is_valid(
    recipients: Sequence[RecipientReference],
) -> tuple[bool, Decimal, Decimal, str]:
    """Return whether ownership weights close to 100% under either form.

    Used by special-assessment preflight so per-unit-interest groups are not
    false-blocked when bare ``Σ ownership_percent`` is only a few percent.
    """
    form, bare_sum, weighted_sum, _warnings = resolve_ownership_weight_form(recipients)
    if form == "per_unit_interest":
        ok = abs(weighted_sum - Decimal("1")) <= OWNERSHIP_PERCENT_TOLERANCE
        return ok, bare_sum, weighted_sum, form
    ok = abs(bare_sum - Decimal("1")) <= OWNERSHIP_PERCENT_TOLERANCE
    return ok, bare_sum, weighted_sum, form


def ownership_percentage_allocation(
    pool_total: Decimal,
    recipients: Sequence[RecipientReference],
) -> tuple[dict[RecipientKey, Decimal], list[str]]:
    """Ownership-weighted split returning **per-recipient dollars**.

    For unit recipients, that is the unit's monthly/annual share.
    For group recipients, that is the **group total** (all units in the
    group), matching equal/sqft group semantics so the schedule matrix can
    safely show per-unit = group_total ÷ unit_count.

    Weight form is auto-detected via :func:`resolve_ownership_weight_form`.

    Returns ``(allocations, warnings)``.
    """
    _require_decimal("pool_total", pool_total)

    form, _bare_sum, _weighted_sum, form_warnings = resolve_ownership_weight_form(
        recipients
    )
    warnings = list(form_warnings)

    out: dict[RecipientKey, Decimal] = {}
    for r in recipients:
        # resolve_ownership_weight_form already validated non-None percents
        pct = r.ownership_percent
        assert pct is not None
        if form == "per_unit_interest":
            weight = pct * Decimal(int(r.unit_count or 1))
        else:
            weight = pct
        out[(r.ref_type, r.ref_id)] = pool_total * weight

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
