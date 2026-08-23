"""Manual (pool-free) variable special assessments (add-variable-special-assessments).

The operator types a total + allocation basis in §5570; the system allocates it
across the HOA's EXISTING units — no pool required. Covers the pool-free engine
primitive and the matrix builder's manual-allocation helper + its blocking guards
(missing per-unit basis data; ownership that doesn't sum to 100% — the compulsory-
ownership case).
"""
from decimal import Decimal

from app.assessment_engine.engine import _allocate_special_assessment
from app.assessment_engine.schemas import RecipientReference
from app.disclosure_package.assessment_schedule_matrix import (
    _manual_special_assessment_allocations,
    manual_special_key,
)


def _unit(ref_id, sqft=None, own=None):
    return RecipientReference(
        ref_type="unit", ref_id=ref_id, label=f"Unit {ref_id}", unit_count=1,
        square_feet=Decimal(str(sqft)) if sqft is not None else None,
        ownership_percent=Decimal(str(own)) if own is not None else None,
    )


# --- pool-free engine primitive ---------------------------------------------

def test_allocate_equal_pool_free():
    recips = [_unit(1), _unit(2), _unit(3), _unit(4)]
    shares, _w = _allocate_special_assessment(Decimal("1000"), recips, pool=None, method="equal")
    assert all(v == Decimal("250") for v in shares.values())
    assert sum(shares.values()) == Decimal("1000")


def test_allocate_ownership_pool_free_los_altos_shape():
    # Los Altos shape: ownership present (sums to 1.0), NO square footage.
    recips = [_unit(1, own="0.5"), _unit(2, own="0.3"), _unit(3, own="0.2")]
    shares, _w = _allocate_special_assessment(Decimal("10000"), recips, pool=None, method="ownership_percentage")
    assert shares[("unit", 1)] == Decimal("5000")
    assert shares[("unit", 2)] == Decimal("3000")
    assert shares[("unit", 3)] == Decimal("2000")
    assert sum(shares.values()) == Decimal("10000")


def test_allocate_sqft_pool_free_with_explicit_denominator():
    recips = [_unit(1, sqft="700"), _unit(2, sqft="300")]
    denom = sum(r.square_feet for r in recips)  # 1000
    shares, _w = _allocate_special_assessment(
        Decimal("120000"), recips, pool=None, method="square_footage", denominator=denom,
    )
    assert shares[("unit", 1)] == Decimal("84000")
    assert shares[("unit", 2)] == Decimal("36000")
    assert sum(shares.values()) == Decimal("120000")


# --- matrix builder manual-allocation helper --------------------------------

def _entry(**kw):
    base = {"total_amount": 120000, "recipient_scope": "all_units", "label": "Roof"}
    base.update(kw)
    return base


def test_manual_equal_allocation_produced():
    recips = [_unit(1), _unit(2)]
    allocs, issues = _manual_special_assessment_allocations(
        [_entry(allocation_basis="equal", total_amount=1000)], recips,
    )
    assert issues == []
    assert len(allocs) == 1
    assert allocs[0].pool_key == manual_special_key(0)
    assert allocs[0].total == Decimal("1000")
    assert sum(e.amount for e in allocs[0].entries) == Decimal("1000")


def test_manual_sqft_allocation_produced():
    recips = [_unit(1, sqft="700"), _unit(2, sqft="300")]
    allocs, issues = _manual_special_assessment_allocations(
        [_entry(allocation_basis="square_footage")], recips,
    )
    assert issues == []
    shares = {e.recipient_ref.ref_id: e.amount for e in allocs[0].entries}
    assert shares == {1: Decimal("84000"), 2: Decimal("36000")}


def test_manual_sqft_without_sqft_data_blocks_no_raise():
    # Los Altos: no per-unit square footage. Choosing sqft basis must BLOCK, not raise.
    recips = [_unit(1, own="0.5"), _unit(2, own="0.5")]
    allocs, issues = _manual_special_assessment_allocations(
        [_entry(allocation_basis="square_footage")], recips,
    )
    assert allocs == []
    assert len(issues) == 1
    assert issues[0].severity == "blocking"
    assert "square footage" in issues[0].message.lower()


def test_manual_ownership_summing_to_200_blocks():
    # Two-Worlds-shape: ownership sums to 2.0. Compulsory ownership → BLOCK.
    recips = [_unit(1, own="0.5"), _unit(2, own="0.5"), _unit(3, own="0.5"), _unit(4, own="0.5")]
    allocs, issues = _manual_special_assessment_allocations(
        [_entry(allocation_basis="ownership_percentage")], recips,
    )
    assert allocs == []
    assert len(issues) == 1
    assert issues[0].severity == "blocking"
    assert "100%" in issues[0].message or "sum" in issues[0].message.lower()


def test_manual_ownership_clean_allocates():
    recips = [_unit(1, own="0.6"), _unit(2, own="0.4")]
    allocs, issues = _manual_special_assessment_allocations(
        [_entry(allocation_basis="ownership_percentage", total_amount=1000)], recips,
    )
    assert issues == []
    shares = {e.recipient_ref.ref_id: e.amount for e in allocs[0].entries}
    assert shares == {1: Decimal("600"), 2: Decimal("400")}


def test_pool_linked_entry_is_not_manual():
    recips = [_unit(1), _unit(2)]
    allocs, issues = _manual_special_assessment_allocations(
        [_entry(allocation_basis="equal", pool_key="sa_roof")], recips,
    )
    assert allocs == [] and issues == []  # pool_key wins → handled elsewhere
