"""Column-level percent-form resolution (C8).

DREs print ownership/interest shares in two forms: decimal fractions that
sum to ~1.0 (``0.1315`` = 13.15%) or percentage points that sum to ~100
(``13.15`` = 13.15%). The old per-value guess (``value > 1 → points``)
misreads any points-form column whose individual values are below 1 —
e.g. a 150-unit HOA printing ``0.667`` (points) is read as the fraction
66.7%, a ~100× over-assessment.

The correct signal is the COLUMN SUM, not any single value:

- ``0.98 ≤ sum ≤ 1.02``  → fraction form (divide by 1)
- ``98 ≤ sum ≤ 102``     → points form (divide by 100)
- anything else          → genuinely ambiguous → raise, never guess

The bands absorb per-row print rounding (well under 2% drift in practice)
and cannot overlap. Shared by promotion (write path), the assessment-
schedule matrix (read path, which must also handle legacy verbatim-stored
rows), and the payload pool-factor path — one rule everywhere.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence


FRACTION_BAND = (Decimal("0.98"), Decimal("1.02"))
POINTS_BAND = (Decimal("98"), Decimal("102"))

FRACTION_DIVISOR = Decimal("1")
POINTS_DIVISOR = Decimal("100")


class AmbiguousPercentColumn(ValueError):
    """A percent column's sum lands outside both accept bands.

    ``ValueError`` subclass on purpose: the assessment-schedule matrix
    already degrades any ``ValueError`` from engine-input construction into
    the operator-review fallback matrix, and the approval routers map it to
    HTTP 422 — ambiguity must surface for a human, never render a guess.
    """

    def __init__(
        self,
        *,
        column_label: str,
        total: Decimal,
        sample_values: Sequence[Decimal],
    ) -> None:
        self.column_label = column_label
        self.total = total
        self.sample_values = list(sample_values)[:5]
        samples = ", ".join(str(v) for v in self.sample_values)
        super().__init__(
            f"Percent column {column_label!r} sums to {total}, which is "
            f"neither fraction form (≈1.0) nor points form (≈100). "
            f"Sample values: [{samples}]. Confirm the column's form "
            "(fraction or points) in the Review Workbench before "
            "proceeding — the engine will not guess."
        )


def resolve_percent_divisor(
    values: Sequence[Optional[Decimal]],
    *,
    column_label: str = "ownership_percent",
    forced_form: str = "unknown",
) -> Optional[Decimal]:
    """Resolve a percent column's divisor from its sum.

    Returns ``Decimal(1)`` (fraction form), ``Decimal(100)`` (points form),
    or ``None`` when the column carries no values at all (nothing to
    normalize). Raises :class:`AmbiguousPercentColumn` when the sum lands
    outside both bands and no ``forced_form`` is supplied.

    ``forced_form`` — ``'fraction'`` or ``'points'`` — is the operator's
    audited review decision (``unit_structure.ownership_percent_form``);
    it short-circuits the sum test entirely.
    """
    if forced_form == "fraction":
        return FRACTION_DIVISOR
    if forced_form == "points":
        return POINTS_DIVISOR

    present = [v for v in values if v is not None]
    if not present:
        return None
    total = sum(present, start=Decimal("0"))
    if FRACTION_BAND[0] <= total <= FRACTION_BAND[1]:
        return FRACTION_DIVISOR
    if POINTS_BAND[0] <= total <= POINTS_BAND[1]:
        return POINTS_DIVISOR
    # A fraction share can never exceed 1.0 by definition, so any value
    # above the band proves the column is points form — even when the
    # column is partial (a subset of units) and its sum lands nowhere
    # near 100. Applied column-wide for consistency.
    if any(v > FRACTION_BAND[1] for v in present):
        return POINTS_DIVISOR
    # All values ≤ 1 with a sub-1 sum: a valid sub-scope / partial
    # fraction column (e.g. a commercial-only pool whose shares sum to
    # the commercial portion of the building). Use verbatim — the
    # engine's sum-drift warning still fires downstream if it matters.
    if total < FRACTION_BAND[0]:
        return FRACTION_DIVISOR
    # Remaining: every value ≤ 1 but the sum is above the fraction band
    # and below the points band — could be a partial points column of
    # sub-1% shares OR a corrupt fraction column. Genuinely ambiguous.
    raise AmbiguousPercentColumn(
        column_label=column_label, total=total, sample_values=present,
    )


def normalize_percent_value(
    value: Optional[Decimal],
    divisor: Optional[Decimal],
) -> Optional[Decimal]:
    """Apply a resolved divisor to one value. None-safe on both sides."""
    if value is None or divisor is None:
        return value
    return value / divisor
