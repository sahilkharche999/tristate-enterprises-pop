"""Pool math allocation tests (Phase 2.2 of dre-driven-assessment-engine).

Each allocator is unit-agnostic Decimal arithmetic. The engine main loop
converts annual budget totals to monthly at the pool boundary before
invoking; the allocator itself doesn't care whether the dollars passed
in are annual or monthly. Tests use round numbers and reproduce the
two flagship scenarios from the spec (Old Mill equal, Esprit Park
sqft) plus negative paths.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.assessment_engine import RecipientReference
from app.assessment_engine.pools import (
    MissingSpecifiedValue,
    equal_allocation,
    ownership_percentage_allocation,
    specified_value_allocation,
    square_footage_allocation,
)


# -- equal_allocation -------------------------------------------------------


class TestEqualAllocation:
    def test_simple_divide(self) -> None:
        assert equal_allocation(Decimal("12000"), 4) == Decimal("3000")

    def test_old_mill_monthly_pool(self) -> None:
        # 279 units × $605/mo → $168,795/mo pool, /279 = $605
        per = equal_allocation(Decimal("168795"), 279)
        assert per == Decimal("605")

    def test_esprit_park_base_pool_monthly(self) -> None:
        # 130 units × $227.10/mo → $29,523/mo pool, /130 = $227.10
        per = equal_allocation(Decimal("29523"), 130)
        assert per == Decimal("227.10")

    def test_preserves_full_decimal_precision(self) -> None:
        # 100 / 3 — no premature rounding
        per = equal_allocation(Decimal("100"), 3)
        # Result must have more than 2 decimal places (rounding is a
        # recipient-total concern, not a pool concern)
        assert per > Decimal("33.33")
        assert per < Decimal("33.34")

    def test_zero_count_raises(self) -> None:
        with pytest.raises(ValueError, match="recipient_count"):
            equal_allocation(Decimal("100"), 0)

    def test_negative_count_raises(self) -> None:
        with pytest.raises(ValueError, match="recipient_count"):
            equal_allocation(Decimal("100"), -1)


# -- square_footage_allocation ---------------------------------------------


def _unit(ref_id: int, label: str, sqft: str) -> RecipientReference:
    return RecipientReference(
        ref_type="unit",
        ref_id=ref_id,
        label=label,
        unit_count=1,
        square_feet=Decimal(sqft),
    )


def _group(ref_id: int, label: str, avg_sqft: str, unit_count: int) -> RecipientReference:
    return RecipientReference(
        ref_type="group",
        ref_id=ref_id,
        label=label,
        unit_count=unit_count,
        square_feet=Decimal(avg_sqft),
    )


class TestSquareFootageAllocation:
    def test_round_number_split(self) -> None:
        pool_total = Decimal("100")
        denominator = Decimal("1000")
        recipients = [_unit(1, "U1", "200"), _unit(2, "U2", "800")]
        result = square_footage_allocation(pool_total, denominator, recipients)
        assert result[("unit", 1)] == Decimal("20")
        assert result[("unit", 2)] == Decimal("80")

    def test_dre_frozen_denominator_not_recomputed(self) -> None:
        # Even though recipients' summed sqft (1500) differs from the DRE
        # denominator (157536), the function uses the denominator verbatim.
        pool_total = Decimal("15753.6")
        denominator = Decimal("157536")  # DRE-frozen
        recipients = [_unit(1, "U1", "500"), _unit(2, "U2", "1000")]
        result = square_footage_allocation(pool_total, denominator, recipients)
        # factor = 15753.6 / 157536 = 0.1
        assert result[("unit", 1)] == Decimal("50.0")
        assert result[("unit", 2)] == Decimal("100.0")

    def test_group_recipient_returns_per_unit_share(self) -> None:
        # For groups, .square_feet is avg per unit. Function returns
        # per-unit share; caller multiplies by unit_count.
        pool_total = Decimal("12000")
        denominator = Decimal("60000")
        recipients = [_group(1, "G1", avg_sqft="1500", unit_count=10)]
        result = square_footage_allocation(pool_total, denominator, recipients)
        # factor = 12000 / 60000 = 0.2; per_unit = 1500 * 0.2 = 300
        assert result[("group", 1)] == Decimal("300.00")

    def test_zero_denominator_raises(self) -> None:
        with pytest.raises(ValueError, match="denominator"):
            square_footage_allocation(Decimal("100"), Decimal("0"), [_unit(1, "U", "10")])

    def test_missing_square_feet_raises(self) -> None:
        rec = RecipientReference(ref_type="unit", ref_id=1, label="U", unit_count=1)
        with pytest.raises(ValueError, match="square_feet"):
            square_footage_allocation(Decimal("100"), Decimal("1000"), [rec])


# -- ownership_percentage_allocation ---------------------------------------


def _unit_pct(ref_id: int, label: str, pct: str) -> RecipientReference:
    return RecipientReference(
        ref_type="unit",
        ref_id=ref_id,
        label=label,
        unit_count=1,
        ownership_percent=Decimal(pct),
    )


class TestOwnershipPercentageAllocation:
    def test_clean_percentages(self) -> None:
        pool_total = Decimal("12000")
        recipients = [
            _unit_pct(1, "U1", "0.25"),
            _unit_pct(2, "U2", "0.25"),
            _unit_pct(3, "U3", "0.50"),
        ]
        allocations, warnings = ownership_percentage_allocation(pool_total, recipients)
        assert allocations[("unit", 1)] == Decimal("3000.00")
        assert allocations[("unit", 2)] == Decimal("3000.00")
        assert allocations[("unit", 3)] == Decimal("6000.00")
        assert warnings == []

    def test_warning_when_sum_drifts_beyond_tolerance(self) -> None:
        # Sum = 0.9985 (drift of 0.0015, > 0.001 tolerance)
        pool_total = Decimal("10000")
        recipients = [
            _unit_pct(1, "U1", "0.4985"),
            _unit_pct(2, "U2", "0.5000"),
        ]
        allocations, warnings = ownership_percentage_allocation(pool_total, recipients)
        assert len(warnings) == 1
        assert "ownership_percent" in warnings[0].lower() or "0.9985" in warnings[0]
        # DRE values used verbatim regardless
        assert allocations[("unit", 1)] == Decimal("4985.0000")

    def test_no_warning_within_tolerance(self) -> None:
        # Sum = 0.9995 — within 0.001 tolerance
        pool_total = Decimal("10000")
        recipients = [
            _unit_pct(1, "U1", "0.4995"),
            _unit_pct(2, "U2", "0.5000"),
        ]
        _, warnings = ownership_percentage_allocation(pool_total, recipients)
        assert warnings == []

    def test_missing_ownership_percent_raises(self) -> None:
        rec = RecipientReference(ref_type="unit", ref_id=1, label="U", unit_count=1)
        with pytest.raises(ValueError, match="ownership_percent"):
            ownership_percentage_allocation(Decimal("100"), [rec])


# -- specified_value_allocation --------------------------------------------


class TestSpecifiedValueAllocation:
    def test_lookup_returns_per_unit_specified_value(self) -> None:
        recipients = [
            RecipientReference(ref_type="unit", ref_id=312, label="312", unit_count=1),
            RecipientReference(ref_type="unit", ref_id=313, label="313", unit_count=1),
        ]
        lookup = {
            (312, "general_common"): Decimal("250.00"),
            (313, "general_common"): Decimal("275.50"),
        }
        result = specified_value_allocation("general_common", recipients, lookup)
        assert result[("unit", 312)] == Decimal("250.00")
        assert result[("unit", 313)] == Decimal("275.50")

    def test_missing_row_raises_with_unit_and_pool_key(self) -> None:
        recipients = [
            RecipientReference(ref_type="unit", ref_id=312, label="312", unit_count=1),
        ]
        lookup: dict[tuple[int, str], Decimal] = {}
        with pytest.raises(MissingSpecifiedValue) as ctx:
            specified_value_allocation("general_common", recipients, lookup)
        assert ctx.value.unit_id == 312
        assert ctx.value.pool_key == "general_common"

    def test_dre_and_manual_consumed_identically(self) -> None:
        # The allocator itself doesn't inspect source — caller does.
        # Same lookup, same answer; provenance is a separate concern.
        recipients = [
            RecipientReference(ref_type="unit", ref_id=1, label="A", unit_count=1),
            RecipientReference(ref_type="unit", ref_id=2, label="B", unit_count=1),
        ]
        lookup = {(1, "p"): Decimal("100"), (2, "p"): Decimal("100")}
        result = specified_value_allocation("p", recipients, lookup)
        assert result[("unit", 1)] == result[("unit", 2)] == Decimal("100")

    def test_group_recipient_rejected(self) -> None:
        # specified_value is a per-unit concept; groups should not appear
        recipients = [
            RecipientReference(ref_type="group", ref_id=1, label="G", unit_count=10),
        ]
        with pytest.raises(ValueError, match="group|per-unit|specified_value"):
            specified_value_allocation("p", recipients, {})


# -- numeric discipline -----------------------------------------------------


class TestDecimalDiscipline:
    """Per spec §Decimal Numeric Discipline: all engine arithmetic uses
    Decimal — never float. Catches a regression where float sneaks in via
    division or constants.
    """

    def test_equal_allocation_rejects_float(self) -> None:
        with pytest.raises(TypeError):
            equal_allocation(12000.0, 4)  # type: ignore[arg-type]

    def test_square_footage_rejects_float_pool_total(self) -> None:
        with pytest.raises(TypeError):
            square_footage_allocation(
                100.0,  # type: ignore[arg-type]
                Decimal("1000"),
                [_unit(1, "U", "100")],
            )
