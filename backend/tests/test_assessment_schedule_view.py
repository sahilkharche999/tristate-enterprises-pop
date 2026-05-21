"""Tests for the assessment-schedule view builder (Phase 5.2).

Verifies that a ``CalcResultSet`` is reshaped into the template
context dict each of the three skeletons (``fixed.html``,
``grouped.html``, ``per_unit.html``) consumes.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.assessment_engine import (
    BudgetLineInput,
    CalcInput,
    PoolDefinition,
    RecipientReference,
    RecipientSet,
)
from app.assessment_engine.engine import run
from app.assessment_engine.schemas import BudgetLineMappingInput
from app.disclosure_package.assessment_schedule_view import (
    build_assessment_schedule_view,
    build_fixed_view,
    build_grouped_view,
    build_per_unit_view,
)


class TestFixedView:
    def test_summary_row_data(self) -> None:
        # 4 units × $100/mo = $4,800 annual; one equal pool
        units = [
            RecipientReference(ref_type="unit", ref_id=i, label=f"U{i}", unit_count=1)
            for i in range(1, 5)
        ]
        ci = CalcInput(
            setup_type="fixed",
            pools=[PoolDefinition(
                pool_id=1, pool_key="equal", pool_name="Equal",
                allocation_method="equal", recipient_scope="all_units",
            )],
            recipient_set=RecipientSet(recipients=units),
            budget_lines=[BudgetLineInput(
                line_id=1, normalized_label="dues",
                section="income", category="income",
                fund_type="operating", amount=Decimal("4800"),
            )],
            mappings=[BudgetLineMappingInput(
                budget_line_normalized_label="dues",
                section="income", category="income",
                fund_type="operating", pool_key="equal",
            )],
            approved_assessment_revenue_annual=Decimal("4800"),
        )
        result = run(ci)
        view = build_fixed_view(result, hoa_name="Old Mill", fiscal_year=2026)
        assert view["hoa"]["name"] == "Old Mill"
        assert view["fiscal_year"] == 2026
        assert view["unit_count"] == 4
        assert view["monthly_assessment_per_unit"] == Decimal("100.00")
        assert view["annual_assessment_per_unit"] == Decimal("1200.00")
        assert view["total_annual_revenue"] == Decimal("4800.00")

    def test_empty_recipient_totals(self) -> None:
        from app.assessment_engine import CalcResultSet
        empty = CalcResultSet(
            pool_allocations=[],
            recipient_totals=[],
            rounding_delta_annual=Decimal("0"),
            rounding_delta_monthly=Decimal("0"),
            rounding_delta_percent=Decimal("0"),
            pool_sum_annual=Decimal("0"),
        )
        view = build_fixed_view(empty, hoa_name="X", fiscal_year=2026)
        assert view["unit_count"] == 0
        assert view["monthly_assessment_per_unit"] == Decimal("0")
        assert view["total_annual_revenue"] == Decimal("0")


class TestGroupedView:
    def test_groups_table_with_base_plus_variable(self) -> None:
        groups = [
            RecipientReference(
                ref_type="group", ref_id=1, label="G1",
                unit_count=10, square_feet=Decimal("1000"),
            ),
            RecipientReference(
                ref_type="group", ref_id=2, label="G2",
                unit_count=5, square_feet=Decimal("2000"),
            ),
        ]
        ci = CalcInput(
            setup_type="grouped",
            pools=[
                PoolDefinition(
                    pool_id=1, pool_key="equal_base", pool_name="Base",
                    allocation_method="equal", recipient_scope="all_units",
                    display_order=1,
                ),
                PoolDefinition(
                    pool_id=2, pool_key="variable_costs", pool_name="Variable",
                    allocation_method="square_footage",
                    recipient_scope="all_units",
                    denominator_value=Decimal("20000"),
                    display_order=2,
                ),
            ],
            recipient_set=RecipientSet(recipients=groups),
            budget_lines=[
                BudgetLineInput(
                    line_id=1, normalized_label="base",
                    section="income", category="income",
                    fund_type="operating", amount=Decimal("36000"),
                ),
                BudgetLineInput(
                    line_id=2, normalized_label="variable",
                    section="operating", category="operating",
                    fund_type="operating", amount=Decimal("24000"),
                ),
            ],
            mappings=[
                BudgetLineMappingInput(
                    budget_line_normalized_label="base",
                    section="income", category="income",
                    fund_type="operating", pool_key="equal_base",
                ),
                BudgetLineMappingInput(
                    budget_line_normalized_label="variable",
                    section="operating", category="operating",
                    fund_type="operating", pool_key="variable_costs",
                ),
            ],
            approved_assessment_revenue_annual=Decimal("60000"),
        )
        result = run(ci)
        view = build_grouped_view(result, hoa_name="Esprit", fiscal_year=2026)
        assert view["hoa"]["name"] == "Esprit"
        assert len(view["groups"]) == 2
        g1, g2 = view["groups"]
        # Equal base is charged per unit, not per group row:
        # (36000/12)/15 units = 200.
        assert g1["base_monthly_per_unit"] == Decimal("200")
        assert g2["base_monthly_per_unit"] == Decimal("200")
        # Variable component is displayed as monthly per unit/type.
        assert g1["variable_monthly_per_unit"] == Decimal("100")
        assert g2["variable_monthly_per_unit"] == Decimal("200")
        # Per-unit monthly = per-unit base + per-unit variable.
        assert g1["total_monthly_per_unit"] == g1["base_monthly_per_unit"] + g1["variable_monthly_per_unit"]
        # Unit counts preserved
        assert g1["unit_count"] == 10
        assert g2["unit_count"] == 5

    def test_skips_unit_recipients(self) -> None:
        # Mixed: 1 group + 1 unit (rare but possible). Grouped view
        # only surfaces group rows.
        recipients = [
            RecipientReference(
                ref_type="group", ref_id=1, label="G1",
                unit_count=5, square_feet=Decimal("1000"),
            ),
            RecipientReference(
                ref_type="unit", ref_id=99, label="ExtraUnit",
                unit_count=1, square_feet=Decimal("500"),
            ),
        ]
        ci = CalcInput(
            setup_type="grouped",
            pools=[PoolDefinition(
                pool_id=1, pool_key="equal_base", pool_name="B",
                allocation_method="equal", recipient_scope="all_units",
            )],
            recipient_set=RecipientSet(recipients=recipients),
            budget_lines=[BudgetLineInput(
                line_id=1, normalized_label="b", section="income",
                category="income", fund_type="operating", amount=Decimal("12000"),
            )],
            mappings=[BudgetLineMappingInput(
                budget_line_normalized_label="b", section="income",
                category="income", fund_type="operating", pool_key="equal_base",
            )],
            approved_assessment_revenue_annual=Decimal("12000"),
        )
        result = run(ci)
        view = build_grouped_view(result, hoa_name="X", fiscal_year=2026)
        assert len(view["groups"]) == 1
        assert view["groups"][0]["group_name"] == "G1"


class TestPerUnitView:
    def test_per_unit_table_with_pool_components(self) -> None:
        units = [
            RecipientReference(
                ref_type="unit", ref_id=1, label="101",
                unit_count=1, category="residential",
                square_feet=Decimal("1000"), parking_spaces=1,
            ),
            RecipientReference(
                ref_type="unit", ref_id=2, label="C1",
                unit_count=1, category="commercial",
                square_feet=Decimal("3000"), parking_spaces=0,
            ),
        ]
        pools = [
            PoolDefinition(
                pool_id=1, pool_key="general_common", pool_name="General",
                allocation_method="specified_value", recipient_scope="all_units",
                display_order=1,
            ),
            PoolDefinition(
                pool_id=2, pool_key="parking", pool_name="Parking",
                allocation_method="specified_value", recipient_scope="parking_users",
                display_order=2,
            ),
        ]
        ci = CalcInput(
            setup_type="per_unit",
            pools=pools,
            recipient_set=RecipientSet(recipients=units),
            budget_lines=[
                BudgetLineInput(
                    line_id=1, normalized_label="gc",
                    section="income", category="income",
                    fund_type="operating", amount=Decimal("3600"),
                ),
                BudgetLineInput(
                    line_id=2, normalized_label="pk",
                    section="income", category="income",
                    fund_type="operating", amount=Decimal("300"),
                ),
            ],
            mappings=[
                BudgetLineMappingInput(
                    budget_line_normalized_label="gc",
                    section="income", category="income",
                    fund_type="operating", pool_key="general_common",
                ),
                BudgetLineMappingInput(
                    budget_line_normalized_label="pk",
                    section="income", category="income",
                    fund_type="operating", pool_key="parking",
                ),
            ],
            approved_assessment_revenue_annual=Decimal("3900"),
            specified_value_lookup={
                (1, "general_common"): Decimal("100"),
                (2, "general_common"): Decimal("200"),
                (1, "parking"): Decimal("25"),
            },
        )
        result = run(ci)
        view = build_per_unit_view(
            result, hoa_name="800 High", fiscal_year=2026,
            pool_definitions=pools,
        )
        assert view["hoa"]["name"] == "800 High"
        # Columns sorted by display_order
        assert [p["pool_key"] for p in view["pool_columns"]] == [
            "general_common", "parking",
        ]
        assert len(view["units"]) == 2
        # Unit 1: general 100 + parking 25 = 125
        unit_101 = next(u for u in view["units"] if u["unit_number"] == "101")
        assert unit_101["components"]["general_common"] == Decimal("100")
        assert unit_101["components"]["parking"] == Decimal("25")
        assert unit_101["total_monthly"] == Decimal("125.00")
        # Unit 2: general 200 only (commercial doesn't have parking)
        unit_c1 = next(u for u in view["units"] if u["unit_number"] == "C1")
        assert unit_c1["components"]["general_common"] == Decimal("200")
        assert "parking" not in unit_c1["components"]
        assert unit_c1["total_monthly"] == Decimal("200.00")
        # Total annual revenue across both units
        assert view["total_annual_revenue"] == Decimal("3900.00")


class TestDispatcher:
    def test_routes_by_setup_type(self) -> None:
        from app.assessment_engine import CalcResultSet
        empty = CalcResultSet(
            pool_allocations=[],
            recipient_totals=[],
            rounding_delta_annual=Decimal("0"),
            rounding_delta_monthly=Decimal("0"),
            rounding_delta_percent=Decimal("0"),
            pool_sum_annual=Decimal("0"),
        )
        fixed = build_assessment_schedule_view(
            empty, setup_type="fixed", hoa_name="X", fiscal_year=2026
        )
        grouped = build_assessment_schedule_view(
            empty, setup_type="grouped", hoa_name="X", fiscal_year=2026
        )
        per_unit = build_assessment_schedule_view(
            empty, setup_type="per_unit", hoa_name="X", fiscal_year=2026,
            pool_definitions=[],
        )
        # Each returns a different shape — verify via the discriminating keys
        assert "unit_count" in fixed
        assert "groups" in grouped
        assert "units" in per_unit

    def test_unknown_setup_type_raises(self) -> None:
        from app.assessment_engine import CalcResultSet
        empty = CalcResultSet(
            pool_allocations=[],
            recipient_totals=[],
            rounding_delta_annual=Decimal("0"),
            rounding_delta_monthly=Decimal("0"),
            rounding_delta_percent=Decimal("0"),
            pool_sum_annual=Decimal("0"),
        )
        with pytest.raises(ValueError, match="Unknown setup_type"):
            build_assessment_schedule_view(
                empty, setup_type="weird",  # type: ignore[arg-type]
                hoa_name="X", fiscal_year=2026,
            )
