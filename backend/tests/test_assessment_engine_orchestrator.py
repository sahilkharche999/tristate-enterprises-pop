"""End-to-end orchestrator tests for the assessment engine.

Covers Phase 2.4 (orchestrator) + Phase 2.5 (pattern fixtures). Each
test loads a setup-shaped fixture inline (no HOA-name branching in the
engine) and asserts the spec-mandated invariants:

- Pattern A: pure fixed (Old Mill), 1 equal pool, N units
- Pattern B: grouped (Esprit Park), 2 pools (equal + sqft), K groups
- Pattern C: per-unit multi-pool (800 High), P pools mixed scope
- Reconciliation: Σ(annual_total) − approved_revenue = rounding_delta_annual
- Unmapped budget line → NeedsHumanReview
- Overrides: package / group / unit / pool scope all apply correctly
- Idempotency: same inputs → byte-equal outputs
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.assessment_engine import (
    AssessmentOverride,
    BudgetLineInput,
    CalcInput,
    PoolDefinition,
    RecipientReference,
    RecipientSet,
)
from app.assessment_engine.engine import run
from app.assessment_engine.errors import NeedsHumanReview
from app.assessment_engine.schemas import BudgetLineMappingInput


# -- helpers ----------------------------------------------------------------


def _line(
    line_id: int,
    label: str,
    amount: str,
    *,
    section: str = "operating",
    category: str = "operating",
    fund_type: str = "operating",
    account_code: str | None = None,
) -> BudgetLineInput:
    return BudgetLineInput(
        line_id=line_id,
        normalized_label=label,
        section=section,
        category=category,  # type: ignore[arg-type]
        fund_type=fund_type,  # type: ignore[arg-type]
        account_code=account_code,
        amount=Decimal(amount),
    )


def _mapping(
    label: str,
    pool_key: str,
    *,
    section: str = "operating",
    category: str = "operating",
    fund_type: str = "operating",
    account_code: str | None = None,
    active: bool = True,
) -> BudgetLineMappingInput:
    return BudgetLineMappingInput(
        budget_line_normalized_label=label,
        section=section,
        category=category,  # type: ignore[arg-type]
        fund_type=fund_type,  # type: ignore[arg-type]
        account_code=account_code,
        pool_key=pool_key,
        active=active,
    )


def _pool(
    pool_id: int,
    key: str,
    method: str,
    *,
    scope: str = "all_units",
    denominator: str | None = None,
    order: int = 0,
) -> PoolDefinition:
    return PoolDefinition(
        pool_id=pool_id,
        pool_key=key,
        pool_name=key.replace("_", " ").title(),
        allocation_method=method,  # type: ignore[arg-type]
        recipient_scope=scope,  # type: ignore[arg-type]
        denominator_value=Decimal(denominator) if denominator is not None else None,
        display_order=order,
    )


# -- Pattern A: pure fixed (Old Mill regression baseline) ------------------


class TestPatternAFixed:
    """279 units × $605/mo × 12 = $2,025,540 annual; single equal pool."""

    def _build(self, *, units: int = 279, monthly_per_unit: str = "605") -> CalcInput:
        annual = Decimal(monthly_per_unit) * Decimal(units) * Decimal(12)
        recipients = [
            RecipientReference(ref_type="unit", ref_id=i, label=f"U{i}", unit_count=1)
            for i in range(1, units + 1)
        ]
        return CalcInput(
            setup_type="fixed",
            pools=[_pool(1, "equal_costs", "equal")],
            recipient_set=RecipientSet(recipients=recipients),
            budget_lines=[_line(1, "dues", str(annual), category="income")],
            mappings=[_mapping("dues", "equal_costs", category="income")],
            approved_assessment_revenue_annual=annual,
        )

    def test_all_units_equal(self) -> None:
        result = run(self._build())
        monthlies = {t.rounded_monthly_total for t in result.recipient_totals}
        assert monthlies == {Decimal("605.00")}
        assert len(result.recipient_totals) == 279

    def test_reconciles_exactly(self) -> None:
        result = run(self._build())
        assert result.rounding_delta_annual == Decimal("0")
        assert result.rounding_delta_monthly == Decimal("0")
        assert result.rounding_delta_percent == Decimal("0")

    def test_annual_total_per_unit(self) -> None:
        result = run(self._build())
        # Each unit: 605 × 12 = 7260 (unit_count = 1)
        annuals = {t.annual_total for t in result.recipient_totals}
        assert annuals == {Decimal("7260.00")}

    def test_pool_sum_equals_approved(self) -> None:
        result = run(self._build())
        assert result.pool_sum_annual == Decimal("2025540")


# -- Pattern B: grouped (Esprit Park-style) --------------------------------


class TestPatternBGrouped:
    """Two pools: equal base + sqft variable; 3 groups for compactness."""

    def _build(self, *, base_monthly_per_unit: str = "200") -> CalcInput:
        # 3 groups, varying unit_count and avg_sqft. DRE denominator =
        # Σ(avg_sqft × unit_count) = 1000×10 + 1500×5 + 2000×5 = 27500
        groups = [
            RecipientReference(
                ref_type="group",
                ref_id=1,
                label="G1",
                unit_count=10,
                square_feet=Decimal("1000"),
            ),
            RecipientReference(
                ref_type="group",
                ref_id=2,
                label="G2",
                unit_count=5,
                square_feet=Decimal("1500"),
            ),
            RecipientReference(
                ref_type="group",
                ref_id=3,
                label="G3",
                unit_count=5,
                square_feet=Decimal("2000"),
            ),
        ]
        total_units = 20
        denominator_sqft = Decimal("27500")
        # Base pool: $200/mo × 20 units × 12 = $48,000 annual
        base_annual = Decimal(base_monthly_per_unit) * Decimal(total_units) * Decimal(12)
        # Variable pool: $30,000 annual operating costs
        variable_annual = Decimal("30000")
        approved = base_annual + variable_annual
        return CalcInput(
            setup_type="grouped",
            pools=[
                _pool(1, "equal_base", "equal", order=1),
                _pool(
                    2,
                    "variable_costs",
                    "square_footage",
                    denominator=str(denominator_sqft),
                    order=2,
                ),
            ],
            recipient_set=RecipientSet(recipients=groups),
            budget_lines=[
                _line(1, "base_assessment_income", str(base_annual), category="income"),
                _line(2, "operating_costs", str(variable_annual)),
            ],
            mappings=[
                _mapping("base_assessment_income", "equal_base", category="income"),
                _mapping("operating_costs", "variable_costs"),
            ],
            approved_assessment_revenue_annual=approved,
        )

    def test_returns_one_row_per_group(self) -> None:
        result = run(self._build())
        assert len(result.recipient_totals) == 3
        assert {t.recipient_ref.ref_id for t in result.recipient_totals} == {1, 2, 3}

    def test_per_unit_pay_rate_scales_with_avg_sqft(self) -> None:
        # Per-group monthly = equal_base + sqft_pool_share. Per-unit pay rate
        # (= per_group_monthly / unit_count) should be strictly higher for
        # groups with higher avg_sqft, because the sqft pool weights by area
        # while the equal pool spreads evenly across groups.
        result = run(self._build())
        by_group = {t.recipient_ref.ref_id: t for t in result.recipient_totals}
        per_unit = {
            gid: t.rounded_monthly_total / Decimal(t.recipient_ref.unit_count)
            for gid, t in by_group.items()
        }
        # G1 avg_sqft=1000, G2=1500, G3=2000
        assert per_unit[1] < per_unit[2] < per_unit[3]

    def test_equal_base_pool_divides_by_total_units_not_group_count(self) -> None:
        result = run(self._build(base_monthly_per_unit="200"))
        equal_rows = [
            row for row in result.pool_allocations
            if row.pool_key == "equal_base"
        ]
        per_unit_base = {
            row.recipient_ref.ref_id: (
                row.unrounded_component_monthly / Decimal(row.recipient_ref.unit_count)
            )
            for row in equal_rows
        }
        assert set(per_unit_base.values()) == {Decimal("200")}

    def test_sum_of_annuals_reconciles_within_rounding(self) -> None:
        result = run(self._build())
        total = sum((t.annual_total for t in result.recipient_totals), start=Decimal("0"))
        # |delta| should be small (recipient-level rounding only; no compounding)
        assert abs(total - Decimal("78000")) < Decimal("1.00")


class TestPatternBRevenueShiftInvariant:
    """Same setup, two different budget mixes with the SAME total approved
    revenue. Per-group amounts shift, but the total sum is preserved within
    rounding tolerance.
    """

    def _build_with_mix(self, base_annual: Decimal, variable_annual: Decimal) -> CalcInput:
        groups = [
            RecipientReference(
                ref_type="group", ref_id=1, label="G1",
                unit_count=10, square_feet=Decimal("1000"),
            ),
            RecipientReference(
                ref_type="group", ref_id=2, label="G2",
                unit_count=5, square_feet=Decimal("1500"),
            ),
            RecipientReference(
                ref_type="group", ref_id=3, label="G3",
                unit_count=5, square_feet=Decimal("2000"),
            ),
        ]
        return CalcInput(
            setup_type="grouped",
            pools=[
                _pool(1, "equal_base", "equal", order=1),
                _pool(
                    2, "variable_costs", "square_footage",
                    denominator="27500", order=2,
                ),
            ],
            recipient_set=RecipientSet(recipients=groups),
            budget_lines=[
                _line(1, "base", str(base_annual), category="income"),
                _line(2, "variable", str(variable_annual)),
            ],
            mappings=[
                _mapping("base", "equal_base", category="income"),
                _mapping("variable", "variable_costs"),
            ],
            approved_assessment_revenue_annual=base_annual + variable_annual,
        )

    def test_constant_total_with_shifted_mix(self) -> None:
        # Mix A: base 60k + variable 18k = 78k
        a = run(self._build_with_mix(Decimal("60000"), Decimal("18000")))
        # Mix B: base 30k + variable 48k = 78k (variable-heavy)
        b = run(self._build_with_mix(Decimal("30000"), Decimal("48000")))

        # The core revenue-shift invariant: mix changes individual amounts,
        # but the package total is preserved within recipient-rounding tolerance.
        a_by_id = {t.recipient_ref.ref_id: t.rounded_monthly_total for t in a.recipient_totals}
        b_by_id = {t.recipient_ref.ref_id: t.rounded_monthly_total for t in b.recipient_totals}
        # At least one group's per-group amount must differ between mixes
        assert any(a_by_id[g] != b_by_id[g] for g in a_by_id)

        # Total annual revenue is preserved within recipient-level rounding tolerance
        a_total = sum((t.annual_total for t in a.recipient_totals), start=Decimal("0"))
        b_total = sum((t.annual_total for t in b.recipient_totals), start=Decimal("0"))
        assert abs(a_total - b_total) < Decimal("1.00")
        assert abs(a_total - Decimal("78000")) < Decimal("1.00")


class TestPatternCIncompleteDRE:
    """Per-unit setup where one unit had a zero/missing value in the DRE and
    the operator filled it via Review Workbench (creating an
    AssessmentUnitPoolAllocation row with source='manual'). The engine
    consumes that row identically to a source='dre' row.
    """

    def test_manual_filled_unit_flows_through(self) -> None:
        units = [
            RecipientReference(ref_type="unit", ref_id=1, label="DRE-extracted"),
            RecipientReference(ref_type="unit", ref_id=2, label="Operator-filled"),
        ]
        # In the DB this would be: (1, 'p') source='dre', (2, 'p') source='manual'.
        # The lookup is identical from the engine's perspective.
        lookup: dict[tuple[int, str], Decimal] = {
            (1, "p"): Decimal("100"),
            (2, "p"): Decimal("125"),
        }
        ci = CalcInput(
            setup_type="per_unit",
            pools=[_pool(1, "p", "specified_value")],
            recipient_set=RecipientSet(recipients=units),
            budget_lines=[_line(1, "dues", "2700", category="income")],
            mappings=[_mapping("dues", "p", category="income")],
            approved_assessment_revenue_annual=Decimal("2700"),
            specified_value_lookup=lookup,
        )
        result = run(ci)
        by_unit = {t.recipient_ref.ref_id: t.rounded_monthly_total for t in result.recipient_totals}
        assert by_unit[1] == Decimal("100.00")
        assert by_unit[2] == Decimal("125.00")
        # Sum: (100 + 125) × 12 = 2700, matches approved
        assert result.rounding_delta_annual == Decimal("0.00")


class TestDenominatorMismatch:
    """Grouped sqft pool where the DRE-frozen denominator disagrees with the
    sum of current recipient (avg_sqft × unit_count). Engine MUST use the
    frozen value and emit a non-blocking warning.
    """

    def test_warning_emitted_when_denominator_drifts(self) -> None:
        # Current sum = 10×1000 + 5×1500 + 5×2000 = 27500
        # DRE-frozen denominator = 30000 (stale)
        groups = [
            RecipientReference(
                ref_type="group", ref_id=1, label="G1",
                unit_count=10, square_feet=Decimal("1000"),
            ),
            RecipientReference(
                ref_type="group", ref_id=2, label="G2",
                unit_count=5, square_feet=Decimal("1500"),
            ),
            RecipientReference(
                ref_type="group", ref_id=3, label="G3",
                unit_count=5, square_feet=Decimal("2000"),
            ),
        ]
        ci = CalcInput(
            setup_type="grouped",
            pools=[
                _pool(1, "variable", "square_footage", denominator="30000"),
            ],
            recipient_set=RecipientSet(recipients=groups),
            budget_lines=[_line(1, "var", "30000")],
            mappings=[_mapping("var", "variable")],
            approved_assessment_revenue_annual=Decimal("30000"),
        )
        result = run(ci)
        # Warning emitted, but engine still produced results
        assert any("DenominatorMismatchWarning" in w for w in result.warnings)
        assert any("30000" in w for w in result.warnings)
        assert any("27500" in w for w in result.warnings)
        # Math used the FROZEN denominator (30000), not the recomputed (27500)
        # factor = 30000/12 / 30000 = 0.08333...
        # G1 (1000 sqft × 10 units = 10000): 10000 × 0.08333 = 833.33 per-group monthly
        by_group = {t.recipient_ref.ref_id: t.rounded_monthly_total for t in result.recipient_totals}
        assert by_group[1] == Decimal("833.33")

    def test_no_warning_when_denominator_matches(self) -> None:
        groups = [
            RecipientReference(
                ref_type="group", ref_id=1, label="G1",
                unit_count=10, square_feet=Decimal("1000"),
            ),
        ]
        ci = CalcInput(
            setup_type="grouped",
            pools=[_pool(1, "v", "square_footage", denominator="10000")],
            recipient_set=RecipientSet(recipients=groups),
            budget_lines=[_line(1, "v", "12000")],
            mappings=[_mapping("v", "v")],
            approved_assessment_revenue_annual=Decimal("12000"),
        )
        result = run(ci)
        assert not any("DenominatorMismatchWarning" in w for w in result.warnings)


# -- Pattern: grouped ownership (Sharon Ridge-style per-unit interest) -----


class TestPatternGroupedOwnershipPerUnitInterest:
    """Grouped setup where ownership_percent is per-unit undivided interest.

    Σ pct = 0.0694 but Σ pct×unit_count = 1.0. Engine must emit group totals
    so matrix per-unit = group_total ÷ unit_count matches client schedule.
    """

    def _build(self) -> CalcInput:
        groups = [
            RecipientReference(
                ref_type="group",
                ref_id=1,
                label="Unit Type A",
                unit_count=7,
                ownership_percent=Decimal("0.0178"),
            ),
            RecipientReference(
                ref_type="group",
                ref_id=2,
                label="Unit Type B",
                unit_count=9,
                ownership_percent=Decimal("0.0242"),
            ),
            RecipientReference(
                ref_type="group",
                ref_id=3,
                label="Unit Type C",
                unit_count=24,
                ownership_percent=Decimal("0.0274"),
            ),
        ]
        annual = Decimal("372867.60")
        return CalcInput(
            setup_type="grouped",
            pools=[_pool(1, "general_operating", "ownership_percentage", order=1)],
            recipient_set=RecipientSet(recipients=groups),
            budget_lines=[_line(1, "operating_costs", str(annual))],
            mappings=[_mapping("operating_costs", "general_operating")],
            approved_assessment_revenue_annual=annual,
        )

    def test_per_unit_dues_match_ownership_times_monthly_pool(self) -> None:
        result = run(self._build())
        by_id = {t.recipient_ref.ref_id: t for t in result.recipient_totals}
        # Group totals / unit_count = client 2025 PUPM
        assert (by_id[1].rounded_monthly_total / Decimal(7)).quantize(
            Decimal("0.01")
        ) == Decimal("553.09")
        assert (by_id[2].rounded_monthly_total / Decimal(9)).quantize(
            Decimal("0.01")
        ) == Decimal("751.95")
        assert (by_id[3].rounded_monthly_total / Decimal(24)).quantize(
            Decimal("0.01")
        ) == Decimal("851.38")

    def test_annual_totals_reconcile_to_approved_revenue(self) -> None:
        result = run(self._build())
        total = sum((t.annual_total for t in result.recipient_totals), start=Decimal("0"))
        assert abs(total - Decimal("372867.60")) < Decimal("1.00")


# -- Pattern C: per-unit multi-pool (800 High-style) -----------------------


class TestPatternCPerUnit:
    """3 pools (general, residential, parking) over 4 units (mixed cats)."""

    def _build(self) -> CalcInput:
        units = [
            RecipientReference(
                ref_type="unit",
                ref_id=1,
                label="101",
                category="residential",
                parking_spaces=1,
            ),
            RecipientReference(
                ref_type="unit",
                ref_id=2,
                label="102",
                category="residential",
                parking_spaces=0,
            ),
            RecipientReference(
                ref_type="unit",
                ref_id=3,
                label="C1",
                category="commercial",
                parking_spaces=2,
            ),
            RecipientReference(
                ref_type="unit",
                ref_id=4,
                label="C2",
                category="commercial",
                parking_spaces=0,
            ),
        ]
        lookup: dict[tuple[int, str], Decimal] = {
            (1, "general_common"): Decimal("100"),
            (2, "general_common"): Decimal("100"),
            (3, "general_common"): Decimal("200"),
            (4, "general_common"): Decimal("200"),
            (1, "residential_common"): Decimal("50"),
            (2, "residential_common"): Decimal("50"),
            (1, "parking"): Decimal("25"),
            (3, "parking"): Decimal("50"),
        }
        # Monthly per unit by pool (from lookup); annual = monthly × 12
        # general: 600 × 12 = 7200; residential: 100 × 12 = 1200; parking: 75 × 12 = 900
        return CalcInput(
            setup_type="per_unit",
            pools=[
                _pool(1, "general_common", "specified_value", scope="all_units", order=1),
                _pool(2, "residential_common", "specified_value", scope="residential_only", order=2),
                _pool(3, "parking", "specified_value", scope="parking_users", order=3),
            ],
            recipient_set=RecipientSet(recipients=units),
            budget_lines=[
                _line(1, "gc", "7200", category="income"),
                _line(2, "rc", "1200", category="income"),
                _line(3, "pk", "900", category="income"),
            ],
            mappings=[
                _mapping("gc", "general_common", category="income"),
                _mapping("rc", "residential_common", category="income"),
                _mapping("pk", "parking", category="income"),
            ],
            approved_assessment_revenue_annual=Decimal("9300"),
            specified_value_lookup=lookup,
        )

    def test_each_unit_total_sums_applicable_pools(self) -> None:
        result = run(self._build())
        by_unit = {t.recipient_ref.ref_id: t for t in result.recipient_totals}

        # Unit 1: 100 + 50 + 25 = 175
        assert by_unit[1].rounded_monthly_total == Decimal("175.00")
        # Unit 2: 100 + 50 = 150 (no parking)
        assert by_unit[2].rounded_monthly_total == Decimal("150.00")
        # Unit 3: 200 + 50 = 250 (commercial, has parking, no residential pool)
        assert by_unit[3].rounded_monthly_total == Decimal("250.00")
        # Unit 4: 200 = 200 (commercial, no parking, no residential pool)
        assert by_unit[4].rounded_monthly_total == Decimal("200.00")

    def test_pool_allocations_rows_only_for_applicable_recipients(self) -> None:
        result = run(self._build())
        general_rows = [r for r in result.pool_allocations if r.pool_key == "general_common"]
        residential_rows = [r for r in result.pool_allocations if r.pool_key == "residential_common"]
        parking_rows = [r for r in result.pool_allocations if r.pool_key == "parking"]

        assert len(general_rows) == 4
        assert len(residential_rows) == 2
        assert {r.recipient_ref.ref_id for r in residential_rows} == {1, 2}
        assert len(parking_rows) == 2
        assert {r.recipient_ref.ref_id for r in parking_rows} == {1, 3}

    def test_reconciliation_exact(self) -> None:
        result = run(self._build())
        # Σ unit annual = (175 + 150 + 250 + 200) × 12 = 775 × 12 = 9300
        assert result.rounding_delta_annual == Decimal("0.00")


# -- Routing errors --------------------------------------------------------


class TestRoutingErrors:
    def test_unmapped_line_raises_needs_human_review(self) -> None:
        ci = CalcInput(
            setup_type="fixed",
            pools=[_pool(1, "equal", "equal")],
            recipient_set=RecipientSet(
                recipients=[RecipientReference(ref_type="unit", ref_id=1, label="U")]
            ),
            budget_lines=[_line(1, "mystery_line", "100", category="income")],
            mappings=[],  # nothing maps it
            approved_assessment_revenue_annual=Decimal("100"),
        )
        with pytest.raises(NeedsHumanReview):
            run(ci)

    def test_inactive_mapping_doesnt_satisfy_line(self) -> None:
        ci = CalcInput(
            setup_type="fixed",
            pools=[_pool(1, "equal", "equal")],
            recipient_set=RecipientSet(
                recipients=[RecipientReference(ref_type="unit", ref_id=1, label="U")]
            ),
            budget_lines=[_line(1, "dues", "100", category="income")],
            mappings=[_mapping("dues", "equal", category="income", active=False)],
            approved_assessment_revenue_annual=Decimal("100"),
        )
        with pytest.raises(NeedsHumanReview):
            run(ci)


# -- Override application --------------------------------------------------


class TestOverrides:
    def _baseline(self) -> CalcInput:
        # 2 units, equal pool with $100/mo each = $2,400 annual
        units = [
            RecipientReference(ref_type="unit", ref_id=1, label="U1"),
            RecipientReference(ref_type="unit", ref_id=2, label="U2"),
        ]
        return CalcInput(
            setup_type="fixed",
            pools=[_pool(1, "equal", "equal")],
            recipient_set=RecipientSet(recipients=units),
            budget_lines=[_line(1, "dues", "2400", category="income")],
            mappings=[_mapping("dues", "equal", category="income")],
            approved_assessment_revenue_annual=Decimal("2400"),
        )

    def test_baseline_each_unit_is_100(self) -> None:
        result = run(self._baseline())
        monthlies = {t.rounded_monthly_total for t in result.recipient_totals}
        assert monthlies == {Decimal("100.00")}

    def test_package_override_replaces_every_recipient(self) -> None:
        ci = self._baseline()
        ci.overrides = [
            AssessmentOverride(
                scope="package",
                override_type="board_approved",
                override_monthly_amount=Decimal("125"),
            )
        ]
        result = run(ci)
        monthlies = {t.rounded_monthly_total for t in result.recipient_totals}
        assert monthlies == {Decimal("125.00")}

    def test_unit_override_targets_one_unit(self) -> None:
        ci = self._baseline()
        ci.overrides = [
            AssessmentOverride(
                scope="unit",
                scope_ref_id=2,
                override_type="manual_correction",
                override_monthly_amount=Decimal("175"),
            )
        ]
        result = run(ci)
        by_unit = {t.recipient_ref.ref_id: t.rounded_monthly_total for t in result.recipient_totals}
        assert by_unit[1] == Decimal("100.00")
        assert by_unit[2] == Decimal("175.00")

    def test_pool_override_replaces_pool_components(self) -> None:
        ci = self._baseline()
        ci.overrides = [
            AssessmentOverride(
                scope="pool",
                scope_ref_id=1,
                override_type="board_approved",
                override_monthly_amount=Decimal("150"),
            )
        ]
        result = run(ci)
        for row in result.pool_allocations:
            assert row.source == "override"
            assert row.unrounded_component_monthly == Decimal("150")
        monthlies = {t.rounded_monthly_total for t in result.recipient_totals}
        assert monthlies == {Decimal("150.00")}


# -- Idempotency -----------------------------------------------------------


class TestIdempotency:
    def test_same_input_same_output(self) -> None:
        ci = CalcInput(
            setup_type="fixed",
            pools=[_pool(1, "equal", "equal")],
            recipient_set=RecipientSet(
                recipients=[
                    RecipientReference(ref_type="unit", ref_id=i, label=f"U{i}")
                    for i in range(1, 11)
                ]
            ),
            budget_lines=[_line(1, "dues", "12000", category="income")],
            mappings=[_mapping("dues", "equal", category="income")],
            approved_assessment_revenue_annual=Decimal("12000"),
        )
        result_a = run(ci)
        result_b = run(ci)
        assert result_a.model_dump() == result_b.model_dump()


# -- Reconciliation precision ----------------------------------------------


class TestReconciliation:
    def test_delta_equals_sum_minus_approved_exactly(self) -> None:
        # 7 units, $100 dues — not evenly divisible by 7
        units = [
            RecipientReference(ref_type="unit", ref_id=i, label=f"U{i}")
            for i in range(1, 8)
        ]
        ci = CalcInput(
            setup_type="fixed",
            pools=[_pool(1, "equal", "equal")],
            recipient_set=RecipientSet(recipients=units),
            budget_lines=[_line(1, "dues", "1200", category="income")],
            mappings=[_mapping("dues", "equal", category="income")],
            approved_assessment_revenue_annual=Decimal("1200"),
        )
        result = run(ci)
        # Each unit ≈ 1200/7/12 = $14.2857.../mo, rounds to $14.29
        # Annual per unit = 14.29 × 12 = 171.48; × 7 = 1200.36; delta = 0.36
        total = sum(
            (t.annual_total for t in result.recipient_totals), start=Decimal("0")
        )
        assert result.rounding_delta_annual == total - Decimal("1200")
        # bounded: |delta| < $1.00 × recipient_count
        assert abs(result.rounding_delta_annual) < Decimal("7.00")
