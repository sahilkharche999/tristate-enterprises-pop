"""Engine override audit-log tests (Phase 4.5 task 121).

Verifies that ``CalcResultSet.applied_overrides`` captures the right
audit entries per scope, including original_calculated_monthly and
delta_monthly so the operator UI can show before/after.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.assessment_engine.engine import run
from app.assessment_engine.schemas import (
    AssessmentOverride,
    BudgetLineInput,
    BudgetLineMappingInput,
    CalcInput,
    PoolDefinition,
    RecipientReference,
    RecipientSet,
)


def _calc_input_for_fixed_setup(overrides=None) -> CalcInput:
    """A minimal fixed-pattern CalcInput: 1 pool, 3 units, equal allocation."""
    recipients = [
        RecipientReference(ref_type="unit", ref_id=i + 1, label=f"Unit {i+1}")
        for i in range(3)
    ]
    return CalcInput(
        setup_type="fixed",
        pools=[
            PoolDefinition(
                pool_id=10, pool_key="operating",
                pool_name="Operating",
                allocation_method="equal",
                recipient_scope="all_units",
                display_order=0,
            ),
        ],
        recipient_set=RecipientSet(recipients=recipients),
        budget_lines=[
            BudgetLineInput(
                line_id=1, normalized_label="dues", section="income",
                category="income", fund_type="operating",
                amount=Decimal("3600"),
            ),
        ],
        mappings=[
            BudgetLineMappingInput(
                budget_line_normalized_label="dues", section="income",
                category="income", fund_type="operating",
                pool_key="operating",
            ),
        ],
        approved_assessment_revenue_annual=Decimal("3600"),
        overrides=overrides or [],
    )


class TestNoOverrides:
    def test_empty_applied_overrides_when_none_supplied(self):
        result = run(_calc_input_for_fixed_setup())
        assert result.applied_overrides == []


class TestPackageOverride:
    def test_package_override_captured_once(self):
        ov = AssessmentOverride(
            scope="package",
            override_type="board_approved",
            override_monthly_amount=Decimal("150.00"),
            reason="Board set $150/unit for all units",
            approved_by="board@hoa",
        )
        result = run(_calc_input_for_fixed_setup(overrides=[ov]))
        # Package override fires per recipient but only one audit entry
        assert len(result.applied_overrides) == 1
        entry = result.applied_overrides[0]
        assert entry.scope == "package"
        assert entry.override_monthly == Decimal("150.00")
        assert entry.override_type == "board_approved"
        assert entry.reason.startswith("Board set")
        assert entry.approved_by == "board@hoa"
        # Every recipient now has $150 monthly + 1800 annual
        for t in result.recipient_totals:
            assert t.rounded_monthly_total == Decimal("150.00")


class TestUnitOverride:
    def test_unit_override_captures_original_and_delta(self):
        ov = AssessmentOverride(
            scope="unit",
            scope_ref_id=1,  # only Unit 1
            override_type="manual_correction",
            override_monthly_amount=Decimal("200.00"),
            reason="Tenant agreement requires $200",
            approved_by="ops",
        )
        result = run(_calc_input_for_fixed_setup(overrides=[ov]))
        # Engine calculated $100/unit (3600/12/3). Override puts unit 1 at $200.
        assert len(result.applied_overrides) == 1
        entry = result.applied_overrides[0]
        assert entry.scope == "unit"
        assert entry.scope_ref_id == 1
        assert entry.original_calculated_monthly == Decimal("100.00")
        assert entry.override_monthly == Decimal("200.00")
        assert entry.delta_monthly == Decimal("100.00")


class TestPoolOverride:
    def test_pool_override_captures_pre_override_sum(self):
        ov = AssessmentOverride(
            scope="pool",
            scope_ref_id=10,  # pool_id of "operating"
            override_type="manual_correction",
            override_monthly_amount=Decimal("50.00"),
            reason="Pool-level zeroing for one cycle",
            approved_by="ops",
        )
        result = run(_calc_input_for_fixed_setup(overrides=[ov]))
        # Engine had $100/unit × 3 = $300 monthly pool total before override.
        # Each recipient component is rewritten to $50.
        assert len(result.applied_overrides) == 1
        entry = result.applied_overrides[0]
        assert entry.scope == "pool"
        assert entry.scope_ref_id == 10
        assert entry.original_calculated_monthly == Decimal("300.00")
        assert entry.override_monthly == Decimal("50.00")
        assert entry.delta_monthly == Decimal("-150.00")


def _grouped_calc_input(overrides=None) -> CalcInput:
    """Two GROUP recipients, 5 units each (total_units=10, recipient_count=2).

    Guards M1: the pool-override delta must scale by recipient COUNT, not by
    the summed ``unit_count``. Per-unit fixtures can't tell the two apart
    because there unit_count==1 for every recipient.
    """
    recipients = [
        RecipientReference(
            ref_type="group", ref_id=i + 1, label=f"Group {i+1}", unit_count=5
        )
        for i in range(2)
    ]
    return CalcInput(
        setup_type="fixed",
        pools=[
            PoolDefinition(
                pool_id=10, pool_key="operating", pool_name="Operating",
                allocation_method="equal", recipient_scope="all_units",
                display_order=0,
            ),
        ],
        recipient_set=RecipientSet(recipients=recipients),
        budget_lines=[
            BudgetLineInput(
                line_id=1, normalized_label="dues", section="income",
                category="income", fund_type="operating",
                amount=Decimal("3600"),
            ),
        ],
        mappings=[
            BudgetLineMappingInput(
                budget_line_normalized_label="dues", section="income",
                category="income", fund_type="operating",
                pool_key="operating",
            ),
        ],
        approved_assessment_revenue_annual=Decimal("3600"),
        overrides=overrides or [],
    )


class TestPoolOverrideGroupedGrain:
    def test_delta_scales_by_recipient_count_not_unit_count(self):
        ov = AssessmentOverride(
            scope="pool",
            scope_ref_id=10,
            override_type="manual_correction",
            override_monthly_amount=Decimal("50.00"),
            reason="Grouped pool override",
            approved_by="ops",
        )
        result = run(_grouped_calc_input(overrides=[ov]))
        assert len(result.applied_overrides) == 1
        entry = result.applied_overrides[0]
        original = entry.original_calculated_monthly
        # Correct pool-wide delta: override * recipient_count(2) - original.
        assert entry.delta_monthly == entry.override_monthly * 2 - original
        # The pre-fix bug multiplied by total_units(10); that value must NOT
        # be what we report.
        assert entry.delta_monthly != entry.override_monthly * 10 - original


class TestNeverEntersHomeownerPDF:
    """Sanity check: applied_overrides is engine-output-only, not
    rendered into pool_allocations or recipient_totals shapes that
    homeowner templates consume.
    """

    def test_pool_allocations_unchanged_shape(self):
        ov = AssessmentOverride(
            scope="package", override_type="board_approved",
            override_monthly_amount=Decimal("123"),
        )
        result = run(_calc_input_for_fixed_setup(overrides=[ov]))
        # pool_allocations don't carry any override audit fields
        for row in result.pool_allocations:
            assert not hasattr(row, "applied_override")
        # recipient_totals don't carry audit fields either
        for t in result.recipient_totals:
            assert not hasattr(t, "applied_override")
        # The audit is on the result envelope ONLY
        assert hasattr(result, "applied_overrides")


class TestMissingRecipientPhantom:
    """Test that overrides on non-existent recipients produce no phantom audit entries (eliminates
    stale/deleted recipient noise in audit logs)."""

    def test_no_audit_created_for_nonexistent_unit(self):
        ov = AssessmentOverride(
            scope="unit",
            scope_ref_id=99,  # non-existent in this setup
            override_type="manual_correction",
            override_monthly_amount=Decimal("200.00"),
            reason="Should skip phantom",
            approved_by="ops",
        )
        result = run(_calc_input_for_fixed_setup(overrides=[ov]))
        assert len(result.applied_overrides) == 0
        # Override itself is skipped since recipient doesn't exist; no error or audit noise.
