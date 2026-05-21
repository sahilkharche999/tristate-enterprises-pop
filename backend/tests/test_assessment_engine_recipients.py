"""Recipient resolver tests (Phase 2.3 of dre-driven-assessment-engine).

Filters a ``RecipientSet`` by a pool's ``recipient_scope``. The engine
main loop calls this once per pool before invoking the pool allocator.

Setup-type distinction (group vs unit) lives in the recipient itself
(``ref_type``); the resolver doesn't branch on setup_type.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.assessment_engine import RecipientReference, RecipientSet
from app.assessment_engine.recipients import (
    UnsupportedRecipientScope,
    resolve_recipients,
)


def _u(
    ref_id: int,
    *,
    label: str | None = None,
    category: str | None = None,
    parking: int = 0,
    sqft: str | None = None,
) -> RecipientReference:
    return RecipientReference(
        ref_type="unit",
        ref_id=ref_id,
        label=label or f"Unit {ref_id}",
        unit_count=1,
        category=category,  # type: ignore[arg-type]
        parking_spaces=parking,
        square_feet=Decimal(sqft) if sqft is not None else None,
    )


def _g(ref_id: int, *, unit_count: int, sqft_avg: str | None = None) -> RecipientReference:
    return RecipientReference(
        ref_type="group",
        ref_id=ref_id,
        label=f"Group {ref_id}",
        unit_count=unit_count,
        square_feet=Decimal(sqft_avg) if sqft_avg is not None else None,
    )


class TestAllUnitsScope:
    def test_per_unit_returns_every_unit(self) -> None:
        rs = RecipientSet(
            recipients=[
                _u(1, category="residential"),
                _u(2, category="commercial"),
                _u(3, category="residential"),
            ]
        )
        result = resolve_recipients(rs, "all_units")
        assert [r.ref_id for r in result] == [1, 2, 3]

    def test_grouped_returns_every_group(self) -> None:
        rs = RecipientSet(
            recipients=[
                _g(1, unit_count=10),
                _g(2, unit_count=15),
            ]
        )
        result = resolve_recipients(rs, "all_units")
        assert [r.ref_id for r in result] == [1, 2]
        # grouped recipients carry their unit_count — caller multiplies by it
        assert result[0].unit_count == 10


class TestResidentialOnlyScope:
    def test_filters_by_category(self) -> None:
        rs = RecipientSet(
            recipients=[
                _u(1, category="residential"),
                _u(2, category="commercial"),
                _u(3, category="residential"),
                _u(4, category="mixed"),
            ]
        )
        result = resolve_recipients(rs, "residential_only")
        assert [r.ref_id for r in result] == [1, 3]

    def test_skips_unit_without_category(self) -> None:
        rs = RecipientSet(recipients=[_u(1), _u(2, category="residential")])
        result = resolve_recipients(rs, "residential_only")
        assert [r.ref_id for r in result] == [2]


class TestCommercialOnlyScope:
    def test_filters_by_category(self) -> None:
        rs = RecipientSet(
            recipients=[
                _u(1, category="residential"),
                _u(2, category="commercial"),
                _u(3, category="commercial"),
                _u(4, category="mixed"),
            ]
        )
        result = resolve_recipients(rs, "commercial_only")
        assert [r.ref_id for r in result] == [2, 3]


class TestParkingUsersScope:
    def test_filters_by_parking_count(self) -> None:
        rs = RecipientSet(
            recipients=[
                _u(1, parking=0),
                _u(2, parking=1),
                _u(3, parking=2),
                _u(4, parking=0),
            ]
        )
        result = resolve_recipients(rs, "parking_users")
        assert [r.ref_id for r in result] == [2, 3]

    def test_empty_when_no_parking_spaces(self) -> None:
        rs = RecipientSet(recipients=[_u(1, parking=0)])
        assert resolve_recipients(rs, "parking_users") == []


class TestCustomUnitListScope:
    def test_filters_to_explicit_unit_ids(self) -> None:
        rs = RecipientSet(recipients=[_u(1), _u(2), _u(3), _u(4)])
        result = resolve_recipients(rs, "custom_unit_list", custom_unit_ids=[2, 4])
        assert [r.ref_id for r in result] == [2, 4]

    def test_missing_custom_list_raises(self) -> None:
        rs = RecipientSet(recipients=[_u(1)])
        with pytest.raises(ValueError, match="custom_unit_ids"):
            resolve_recipients(rs, "custom_unit_list")

    def test_ignores_unknown_ids(self) -> None:
        rs = RecipientSet(recipients=[_u(1), _u(2)])
        result = resolve_recipients(rs, "custom_unit_list", custom_unit_ids=[1, 99])
        assert [r.ref_id for r in result] == [1]


class TestPreservesOrder:
    def test_resolver_preserves_input_order(self) -> None:
        rs = RecipientSet(
            recipients=[
                _u(5, category="residential"),
                _u(2, category="residential"),
                _u(8, category="residential"),
            ]
        )
        result = resolve_recipients(rs, "all_units")
        assert [r.ref_id for r in result] == [5, 2, 8]


class TestUnsupportedScope:
    def test_unknown_scope_raises(self) -> None:
        rs = RecipientSet(recipients=[_u(1)])
        with pytest.raises(UnsupportedRecipientScope):
            resolve_recipients(rs, "not_a_real_scope")  # type: ignore[arg-type]
