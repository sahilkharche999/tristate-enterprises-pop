"""Universal ownership-% degrade rule (add-variable-special-assessments follow-up).

Ownership % is load-bearing only when a pool allocates by it. The matrix builder
must:
  - keep an ambiguous ownership column BLOCKING when a pool uses ownership %
    (dollars depend on it), and
  - gracefully DROP a decorative ambiguous ownership column when no pool uses it
    (e.g. a phased/merged HOA whose per-increment percentages total ~200%),
    rather than hard-blocking the PDF.

Covers the full safety matrix via the decision helper.
"""
from decimal import Decimal

import pytest

from app.assessment_engine.percent_form import AmbiguousPercentColumn
from app.disclosure_package.assessment_schedule_matrix import _ownership_divisor_or_drop


CLEAN_FRACTION = [Decimal("0.5"), Decimal("0.5")]          # sums to 1.0
CLEAN_POINTS = [Decimal("50"), Decimal("50")]              # sums to 100
AMBIGUOUS = [Decimal("0.0347")] * 58 + [Decimal("0.0")] * 0  # sums to ~2.0, all <1


def _sum(vals):
    return sum(vals, Decimal("0"))


def test_ownership_used_clean_resolves_normally():
    divisor, dropped = _ownership_divisor_or_drop(
        CLEAN_FRACTION, column_label="c", forced_form="unknown", ownership_used=True,
    )
    assert dropped is False
    assert divisor == Decimal("1")


def test_ownership_used_ambiguous_still_raises():
    # Load-bearing ownership: ambiguity MUST block — operator has to resolve it.
    ambiguous = [Decimal("0.0347")] * 58  # sum ≈ 2.01, all ≤ 1, neither band
    assert Decimal("1.02") < _sum(ambiguous) < Decimal("98")
    with pytest.raises(AmbiguousPercentColumn):
        _ownership_divisor_or_drop(
            ambiguous, column_label="c", forced_form="unknown", ownership_used=True,
        )


def test_ownership_unused_clean_resolves_normally():
    # Decorative but unambiguous: unchanged, still resolves + displays.
    divisor, dropped = _ownership_divisor_or_drop(
        CLEAN_POINTS, column_label="c", forced_form="unknown", ownership_used=False,
    )
    assert dropped is False
    assert divisor == Decimal("100")


def test_ownership_unused_ambiguous_drops_instead_of_blocking():
    # The Two-Worlds case: no pool uses ownership, column is ambiguous (~200%).
    ambiguous = [Decimal("0.0347")] * 58
    divisor, dropped = _ownership_divisor_or_drop(
        ambiguous, column_label="c", forced_form="unknown", ownership_used=False,
    )
    assert dropped is True
    assert divisor is None


def test_forced_form_short_circuits_regardless():
    # An explicit operator decision always wins, used or not.
    divisor, dropped = _ownership_divisor_or_drop(
        [Decimal("0.0347")] * 58, column_label="c", forced_form="fraction",
        ownership_used=False,
    )
    assert dropped is False
    assert divisor == Decimal("1")
