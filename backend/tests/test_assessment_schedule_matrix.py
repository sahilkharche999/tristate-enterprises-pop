"""Tests for the Universal Assessment Matrix.

These tests use small synthetic fixtures named after the real visual patterns
from the 2026 corpus. The point is not to reproduce every source PDF; it is to
prove the renderer contract can express each pattern without adding another
HOA-specific template.
"""
from __future__ import annotations

from decimal import Decimal
import json
import sqlite3
from types import SimpleNamespace

import fitz

from app.assessment_engine import (
    BudgetLineInput,
    CalcInput,
    PoolDefinition,
    RecipientReference,
    RecipientSet,
)
from app.assessment_engine.engine import run
from app.assessment_engine.schemas import (
    BudgetLineMappingInput,
    CalcResultSet,
    PoolAllocationResult,
    RecipientTotalResult,
    SpecialAssessmentRendererEvent,
)
from app.disclosure_package.assessment_schedule_matrix import (
    EvidenceRef,
    FooterRow,
    build_matrix_from_approved_assessment_setup,
    build_universal_assessment_matrix,
    validate_assessment_matrix_finalization,
)
from app.disclosure_package.package_specs import STANDARD_PACKAGE_SPEC
from app.disclosure_package.render import render_template


def _budget(label: str, amount: str, *, category: str = "income") -> BudgetLineInput:
    return BudgetLineInput(
        line_id=abs(hash(label)) % 100000,
        normalized_label=label,
        section=category,
        category=category,  # type: ignore[arg-type]
        fund_type="operating",
        amount=Decimal(amount),
    )


def _mapping(label: str, pool_key: str, *, category: str = "income") -> BudgetLineMappingInput:
    return BudgetLineMappingInput(
        budget_line_normalized_label=label,
        section=category,
        category=category,  # type: ignore[arg-type]
        fund_type="operating",
        pool_key=pool_key,
    )


def _text_from_matrix_pdf(matrix) -> str:
    pdf_bytes = render_template(
        template_name="assessment_schedule/universal.html",
        context={
            "matrix": matrix,
            "hoa": SimpleNamespace(name=matrix.hoa["name"]),
            "fiscal_year": matrix.fiscal_year,
            "static_data": STANDARD_PACKAGE_SPEC.static_data,
            "hoa_settings": {
                "management_company_address": "",
                "management_company_phone": "",
                "management_company_fax": "",
                "management_company_web": "",
            },
        },
    )
    assert pdf_bytes.startswith(b"%PDF")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _empty_result() -> CalcResultSet:
    return CalcResultSet(
        pool_allocations=[],
        recipient_totals=[],
        rounding_delta_annual=Decimal("0"),
        rounding_delta_monthly=Decimal("0"),
        rounding_delta_percent=Decimal("0"),
        pool_sum_annual=Decimal("0"),
    )


def test_db_builder_blocks_when_current_year_pool_mappings_are_missing() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE assessment_setups (
            id INTEGER PRIMARY KEY,
            property_id INTEGER,
            setup_type TEXT,
            status TEXT,
            approved_at TEXT
        );
        CREATE TABLE dre_extraction_runs (
            id INTEGER PRIMARY KEY,
            property_id INTEGER,
            promoted_setup_id INTEGER,
            parsed_json TEXT
        );
        CREATE TABLE allocation_pools (
            id INTEGER PRIMARY KEY,
            assessment_setup_id INTEGER,
            pool_key TEXT,
            pool_name TEXT,
            allocation_method TEXT,
            recipient_scope TEXT,
            denominator_value NUMERIC,
            include_in_pdf INTEGER,
            display_order INTEGER
        );
        CREATE TABLE assessment_groups (
            id INTEGER PRIMARY KEY,
            assessment_setup_id INTEGER,
            group_name TEXT,
            unit_count INTEGER,
            average_square_feet NUMERIC,
            ownership_percent NUMERIC,
            display_order INTEGER
        );
        CREATE TABLE assessment_units (
            id INTEGER PRIMARY KEY,
            assessment_setup_id INTEGER,
            unit_number TEXT,
            square_feet NUMERIC,
            ownership_percent NUMERIC,
            category TEXT,
            parking_spaces INTEGER
        );
        CREATE TABLE budget_line_pool_mappings (
            budget_line_normalized_label TEXT,
            section TEXT,
            category TEXT,
            fund_type TEXT,
            account_code TEXT,
            pool_key TEXT,
            active INTEGER,
            property_id INTEGER,
            assessment_setup_id INTEGER
        );
        CREATE TABLE assessment_unit_pool_allocations (
            assessment_setup_id INTEGER,
            assessment_unit_id INTEGER,
            pool_key TEXT,
            specified_monthly_amount NUMERIC
        );
        """
    )
    conn.execute(
        """
        INSERT INTO assessment_setups
        (id, property_id, setup_type, status, approved_at)
        VALUES (3, 18, 'per_unit', 'approved', '2026-05-18T12:30:13+00:00')
        """
    )
    conn.executemany(
        """
        INSERT INTO allocation_pools
        (id, assessment_setup_id, pool_key, pool_name, allocation_method,
         recipient_scope, denominator_value, include_in_pdf, display_order)
        VALUES (?, 3, ?, ?, ?, 'all_units', ?, 1, ?)
        """,
        [
            (7, "variable_costs", "Variable Costs", "square_footage", 157536, 1),
            (8, "equal_costs", "Equal Costs", "equal", 142, 2),
        ],
    )
    conn.execute(
        """
        INSERT INTO dre_extraction_runs
        (id, property_id, promoted_setup_id, parsed_json)
        VALUES (7, 18, 3, ?)
        """,
        (
            json.dumps(
                {
                    "assessment_setup": {"source_pages": [14, 15]},
                    "unit_structure": {
                        "groups": [
                            {
                                "label": "Group 1",
                                "unit_count": 41,
                                "average_square_feet": 793,
                                "ownership_percent": "0",
                            }
                        ]
                    },
                }
            ),
        ),
    )

    matrix = build_matrix_from_approved_assessment_setup(
        connection=conn,
        property_id=18,
        fiscal_year=2026,
        budget_draft=SimpleNamespace(line_items=[]),
        hoa_name="ESPIRIT PARK",
        unit_count=142,
        approved_assessment_revenue_annual=Decimal("1148080"),
    )

    assert matrix.recipient_grain == "manual_review"
    assert matrix.preflight_issues[0].severity == "blocking"
    assert "Approved DRE assessment setup is present" in matrix.method_summary.assessment_method
    assert "Budget mapping review required" in matrix.method_summary.display_basis
    assert "Current-year budget lines are not mapped" in matrix.rows[0].missing_basis_reason


def test_db_builder_can_split_generated_assessment_revenue_by_dre_pool_proportions() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE assessment_setups (
            id INTEGER PRIMARY KEY,
            property_id INTEGER,
            setup_type TEXT,
            status TEXT,
            approved_at TEXT
        );
        CREATE TABLE dre_extraction_runs (
            id INTEGER PRIMARY KEY,
            property_id INTEGER,
            promoted_setup_id INTEGER,
            parsed_json TEXT
        );
        CREATE TABLE allocation_pools (
            id INTEGER PRIMARY KEY,
            assessment_setup_id INTEGER,
            pool_key TEXT,
            pool_name TEXT,
            allocation_method TEXT,
            recipient_scope TEXT,
            denominator_value NUMERIC,
            include_in_pdf INTEGER,
            display_order INTEGER
        );
        CREATE TABLE assessment_groups (
            id INTEGER PRIMARY KEY,
            assessment_setup_id INTEGER,
            group_name TEXT,
            unit_count INTEGER,
            average_square_feet NUMERIC,
            ownership_percent NUMERIC,
            display_order INTEGER
        );
        CREATE TABLE assessment_units (
            id INTEGER PRIMARY KEY,
            assessment_setup_id INTEGER,
            unit_number TEXT,
            square_feet NUMERIC,
            ownership_percent NUMERIC,
            category TEXT,
            parking_spaces INTEGER
        );
        CREATE TABLE budget_line_pool_mappings (
            budget_line_normalized_label TEXT,
            section TEXT,
            category TEXT,
            fund_type TEXT,
            account_code TEXT,
            pool_key TEXT,
            active INTEGER,
            property_id INTEGER,
            assessment_setup_id INTEGER
        );
        CREATE TABLE assessment_unit_pool_allocations (
            assessment_setup_id INTEGER,
            assessment_unit_id INTEGER,
            pool_key TEXT,
            specified_monthly_amount NUMERIC
        );
        """
    )
    conn.execute(
        """
        INSERT INTO assessment_setups
        (id, property_id, setup_type, status, approved_at)
        VALUES (4, 18, 'grouped', 'approved', '2026-05-19T17:10:17+00:00')
        """
    )
    conn.executemany(
        """
        INSERT INTO allocation_pools
        (id, assessment_setup_id, pool_key, pool_name, allocation_method,
         recipient_scope, denominator_value, include_in_pdf, display_order)
        VALUES (?, 4, ?, ?, ?, 'all_units', ?, 1, ?)
        """,
        [
            (9, "variable_costs_prorated", "Variable Costs", "square_footage", 157536, 1),
            (10, "equal_costs_equal", "Equal Costs", "equal", 142, 2),
        ],
    )
    conn.execute(
        """
        INSERT INTO assessment_groups
        (id, assessment_setup_id, group_name, unit_count, average_square_feet,
         ownership_percent, display_order)
        VALUES (1, 4, 'Group 1', 142, 1109.408450704225, 0, 1)
        """
    )
    conn.execute(
        """
        INSERT INTO dre_extraction_runs
        (id, property_id, promoted_setup_id, parsed_json)
        VALUES (9, 18, 4, ?)
        """,
        (
            json.dumps(
                {
                    "assessment_setup": {"source_pages": [14]},
                    "allocation_pools": [
                        {
                            "pool_key": "variable_costs_prorated",
                            "annual_amount": "20016",
                            "source_pages": [14],
                        },
                        {
                            "pool_key": "equal_costs_equal",
                            "annual_amount": "386977",
                            "source_pages": [14],
                        },
                    ],
                }
            ),
        ),
    )

    matrix = build_matrix_from_approved_assessment_setup(
        connection=conn,
        property_id=18,
        fiscal_year=2026,
        budget_draft=SimpleNamespace(
            line_items=[
                SimpleNamespace(
                    label="40000 - Assessment Income",
                    amount=Decimal("1148080"),
                    is_revenue=True,
                    is_reserve=False,
                    category="income",
                    section="income",
                    account_code="40000",
                )
            ]
        ),
        hoa_name="ESPIRIT PARK",
        unit_count=142,
        approved_assessment_revenue_annual=Decimal("1148080"),
    )

    assert matrix.recipient_grain == "group"
    assert "square-footage variable component plus an equal/base component" in (
        matrix.method_summary.assessment_method
    )
    assert matrix.method_summary.display_basis == "Grouped schedule."
    assert [col.key for col in matrix.component_columns] == [
        "variable_costs_prorated",
        "equal_costs_equal",
    ]
    assert matrix.preflight_issues == []
    assert matrix.internal_review_notes
    assert "generated assessment revenue" in matrix.internal_review_notes[0].message
    assert matrix.component_summary_rows[0].annual_amount.quantize(
        Decimal("0.01")
    ) == Decimal("56462.81")
    assert matrix.component_summary_rows[1].annual_amount.quantize(
        Decimal("0.01")
    ) == Decimal("1091617.19")


def test_old_mill_fixed_flat_builds_summary_row() -> None:
    units = [
        RecipientReference(ref_type="unit", ref_id=i, label=f"U{i}")
        for i in range(1, 5)
    ]
    pools = [
        PoolDefinition(
            pool_id=1,
            pool_key="regular_assessment",
            pool_name="Regular Assessment",
            allocation_method="equal",
            recipient_scope="all_units",
        )
    ]
    result = run(CalcInput(
        setup_type="fixed",
        pools=pools,
        recipient_set=RecipientSet(recipients=units),
        budget_lines=[_budget("dues", "4800")],
        mappings=[_mapping("dues", "regular_assessment")],
        approved_assessment_revenue_annual=Decimal("4800"),
    ))

    matrix = build_universal_assessment_matrix(
        result,
        setup_type="fixed",
        hoa_name="Old Mill",
        fiscal_year=2026,
        pool_definitions=pools,
        source_pages=[12],
    )

    assert matrix.recipient_grain == "summary"
    assert len(matrix.rows) == 1
    row = matrix.rows[0]
    assert row.recipient_grain == "summary"
    assert row.recipient_label == "All Units"
    assert row.unit_count == 4
    assert row.total_monthly_per_recipient == Decimal("100.00")
    assert row.annual_assessment_per_recipient == Decimal("1200.00")
    assert row.total_annual_revenue == Decimal("4800.00")
    assert [col.label for col in matrix.total_columns] == [
        "Monthly Assessment",
        "Annual Assessment",
        "Total Annual Revenue",
    ]


def test_ryland_grouped_rows_use_per_unit_and_group_budget_semantics() -> None:
    groups = [
        RecipientReference(ref_type="group", ref_id=1, label="Type A", unit_count=10, square_feet=Decimal("1000")),
        RecipientReference(ref_type="group", ref_id=2, label="Type B", unit_count=5, square_feet=Decimal("2000")),
    ]
    pools = [
        PoolDefinition(
            pool_id=1,
            pool_key="equal_base",
            pool_name="Base Assessment",
            allocation_method="equal",
            recipient_scope="all_units",
            display_order=1,
        ),
        PoolDefinition(
            pool_id=2,
            pool_key="variable_costs",
            pool_name="Variable Assessment",
            allocation_method="square_footage",
            recipient_scope="all_units",
            denominator_value=Decimal("20000"),
            display_order=2,
        ),
    ]
    result = run(CalcInput(
        setup_type="grouped",
        pools=pools,
        recipient_set=RecipientSet(recipients=groups),
        budget_lines=[_budget("base", "36000"), _budget("variable", "24000", category="operating")],
        mappings=[_mapping("base", "equal_base"), _mapping("variable", "variable_costs", category="operating")],
        approved_assessment_revenue_annual=Decimal("60000"),
    ))

    matrix = build_universal_assessment_matrix(
        result,
        setup_type="grouped",
        hoa_name="Ryland Mews",
        fiscal_year=2026,
        pool_definitions=pools,
        source_pages=[14],
    )

    assert matrix.recipient_grain == "group"
    assert [col.label for col in matrix.total_columns] == [
        "Monthly Assessment Per Unit/Type",
        "Total Monthly Budget",
        "Annual Total",
    ]
    type_a = matrix.rows[0]
    assert type_a.recipient_grain == "group"
    assert type_a.component_values_monthly_per_recipient["equal_base"] == Decimal("200")
    assert type_a.component_values_monthly_per_recipient["variable_costs"] == Decimal("100")
    assert type_a.total_monthly_per_recipient == Decimal("300.00")
    assert type_a.total_monthly_budget == Decimal("3000.00")
    assert type_a.annual_total == Decimal("36000.00")
    assert matrix.method_summary.display_basis == "Grouped schedule."
    assert "square-footage variable component" in matrix.method_summary.assessment_method


def test_grouped_zero_ownership_percent_does_not_create_percent_column() -> None:
    groups = [
        RecipientReference(
            ref_type="group",
            ref_id=1,
            label="Group 1",
            unit_count=41,
            square_feet=Decimal("793"),
            ownership_percent=Decimal("0"),
        ),
        RecipientReference(
            ref_type="group",
            ref_id=2,
            label="Group 2",
            unit_count=20,
            square_feet=Decimal("834"),
            ownership_percent=Decimal("0"),
        ),
    ]
    pools = [
        PoolDefinition(
            pool_id=1,
            pool_key="variable_costs",
            pool_name="Variable Costs",
            allocation_method="square_footage",
            recipient_scope="all_units",
            denominator_value=Decimal("49293"),
            display_order=1,
        ),
        PoolDefinition(
            pool_id=2,
            pool_key="equal_costs",
            pool_name="Equal Costs",
            allocation_method="equal",
            recipient_scope="all_units",
            denominator_value=Decimal("61"),
            display_order=2,
        ),
    ]
    result = run(CalcInput(
        setup_type="grouped",
        pools=pools,
        recipient_set=RecipientSet(recipients=groups),
        budget_lines=[_budget("variable", "1200"), _budget("equal", "7320")],
        mappings=[_mapping("variable", "variable_costs"), _mapping("equal", "equal_costs")],
        approved_assessment_revenue_annual=Decimal("8520"),
    ))

    matrix = build_universal_assessment_matrix(
        result,
        setup_type="grouped",
        hoa_name="Esprit",
        fiscal_year=2026,
        pool_definitions=pools,
        source_pages=[14],
    )

    assert "percent_of_total" not in [col.key for col in matrix.basis_columns]
    assert matrix.method_summary.display_basis == "Grouped schedule."


def test_hastings_percentage_output_uses_unit_grain_and_percent_basis() -> None:
    units = [
        RecipientReference(ref_type="unit", ref_id=1, label="101", ownership_percent=Decimal("0.40")),
        RecipientReference(ref_type="unit", ref_id=2, label="102", ownership_percent=Decimal("0.60")),
    ]
    pools = [
        PoolDefinition(
            pool_id=1,
            pool_key="ownership_assessment",
            pool_name="Ownership Assessment",
            allocation_method="ownership_percentage",
            recipient_scope="all_units",
            denominator_value=Decimal("100"),
        )
    ]
    result = run(CalcInput(
        setup_type="per_unit",
        pools=pools,
        recipient_set=RecipientSet(recipients=units),
        budget_lines=[_budget("dues", "12000")],
        mappings=[_mapping("dues", "ownership_assessment")],
        approved_assessment_revenue_annual=Decimal("12000"),
    ))

    matrix = build_universal_assessment_matrix(
        result,
        setup_type="per_unit",
        hoa_name="Hastings Square",
        fiscal_year=2026,
        pool_definitions=pools,
        source_pages=[15],
        optional_values_by_recipient={1: {"unfunded_liability": Decimal("1000")}},
    )

    assert matrix.recipient_grain == "unit"
    assert [col.key for col in matrix.basis_columns] == ["percent_of_total"]
    assert [col.key for col in matrix.optional_columns] == ["unfunded_liability"]
    unit_101 = matrix.rows[0]
    assert unit_101.recipient_grain == "unit"
    assert unit_101.component_values_monthly["ownership_assessment"] == Decimal("400")
    assert unit_101.total_monthly_assessment == Decimal("400.00")
    assert unit_101.annual_total == Decimal("4800.00")


def test_1207_indiana_equal_plus_prorata_renders_two_components() -> None:
    units = [
        RecipientReference(ref_type="unit", ref_id=1, label="A", square_feet=Decimal("1000"), ownership_percent=Decimal("0.40")),
        RecipientReference(ref_type="unit", ref_id=2, label="B", square_feet=Decimal("1500"), ownership_percent=Decimal("0.60")),
    ]
    pools = [
        PoolDefinition(
            pool_id=1,
            pool_key="equal_component",
            pool_name="Equal Component",
            allocation_method="equal",
            recipient_scope="all_units",
            display_order=1,
        ),
        PoolDefinition(
            pool_id=2,
            pool_key="prorata_component",
            pool_name="Pro-Rata Component",
            allocation_method="ownership_percentage",
            recipient_scope="all_units",
            denominator_value=Decimal("100"),
            display_order=2,
        ),
    ]
    result = run(CalcInput(
        setup_type="per_unit",
        pools=pools,
        recipient_set=RecipientSet(recipients=units),
        budget_lines=[_budget("equal", "1200"), _budget("prorata", "12000")],
        mappings=[_mapping("equal", "equal_component"), _mapping("prorata", "prorata_component")],
        approved_assessment_revenue_annual=Decimal("13200"),
    ))

    matrix = build_universal_assessment_matrix(
        result,
        setup_type="per_unit",
        hoa_name="1207 Indiana",
        fiscal_year=2026,
        pool_definitions=pools,
        source_pages=[18],
    )

    assert [col.key for col in matrix.component_columns] == [
        "equal_component",
        "prorata_component",
    ]
    assert {col.key for col in matrix.basis_columns} == {"sq_ft", "percent_of_total"}


def test_86_third_square_footage_supports_prior_year_comparison_columns() -> None:
    units = [
        RecipientReference(ref_type="unit", ref_id=1, label="1", square_feet=Decimal("800")),
        RecipientReference(ref_type="unit", ref_id=2, label="2", square_feet=Decimal("1200")),
    ]
    pools = [
        PoolDefinition(
            pool_id=1,
            pool_key="sqft_assessment",
            pool_name="2026 Assessment",
            allocation_method="square_footage",
            recipient_scope="all_units",
            denominator_value=Decimal("2000"),
        )
    ]
    result = run(CalcInput(
        setup_type="per_unit",
        pools=pools,
        recipient_set=RecipientSet(recipients=units),
        budget_lines=[_budget("dues", "24000")],
        mappings=[_mapping("dues", "sqft_assessment")],
        approved_assessment_revenue_annual=Decimal("24000"),
    ))

    matrix = build_universal_assessment_matrix(
        result,
        setup_type="per_unit",
        hoa_name="86 Third Street",
        fiscal_year=2026,
        pool_definitions=pools,
        source_pages=[9],
        optional_values_by_recipient={
            1: {
                "prior_year_assessment": Decimal("700"),
                "current_year_assessment": Decimal("800"),
                "difference": Decimal("100"),
                "percent_change": Decimal("14.29"),
            }
        },
    )

    assert [col.key for col in matrix.basis_columns] == ["sq_ft"]
    assert {col.key for col in matrix.optional_columns} == {
        "prior_year_assessment",
        "current_year_assessment",
        "difference",
        "percent_change",
    }


def test_150_w_edith_tier_category_uses_group_unit_type_basis() -> None:
    groups = [
        RecipientReference(ref_type="group", ref_id=1, label="1 Bedroom", unit_count=8),
        RecipientReference(ref_type="group", ref_id=2, label="2 Bedroom", unit_count=12),
    ]
    pools = [
        PoolDefinition(
            pool_id=1,
            pool_key="tier_assessment",
            pool_name="Monthly Assessment",
            allocation_method="specified_value",
            recipient_scope="all_units",
        )
    ]
    result = CalcResultSet(
        pool_allocations=[
            PoolAllocationResult(
                recipient_ref=groups[0],
                pool_id=1,
                pool_key="tier_assessment",
                unrounded_component_monthly=Decimal("1200"),
            ),
            PoolAllocationResult(
                recipient_ref=groups[1],
                pool_id=1,
                pool_key="tier_assessment",
                unrounded_component_monthly=Decimal("2400"),
            ),
        ],
        recipient_totals=[
            RecipientTotalResult(
                recipient_ref=groups[0],
                raw_monthly_total=Decimal("1200"),
                rounded_monthly_total=Decimal("1200.00"),
                annual_total=Decimal("14400.00"),
                rounding_delta_contribution=Decimal("0"),
            ),
            RecipientTotalResult(
                recipient_ref=groups[1],
                raw_monthly_total=Decimal("2400"),
                rounded_monthly_total=Decimal("2400.00"),
                annual_total=Decimal("28800.00"),
                rounding_delta_contribution=Decimal("0"),
            ),
        ],
        rounding_delta_annual=Decimal("0"),
        rounding_delta_monthly=Decimal("0"),
        rounding_delta_percent=Decimal("0"),
        pool_sum_annual=Decimal("43200"),
    )

    matrix = build_universal_assessment_matrix(
        result,
        setup_type="grouped",
        hoa_name="150 W. Edith",
        fiscal_year=2026,
        pool_definitions=pools,
        source_pages=[4],
    )

    assert "unit_type" in [col.key for col in matrix.basis_columns]
    assert matrix.component_summary_rows[0].annual_amount == "Varies by recipient"
    assert matrix.component_summary_rows[0].monthly_amount == "Varies by recipient"


def test_800_high_multi_pool_parent_child_layout_and_child_mapping_guard() -> None:
    units = [
        RecipientReference(ref_type="unit", ref_id=1, label="R1", square_feet=Decimal("1000"), ownership_percent=Decimal("0.50"), parking_spaces=1),
        RecipientReference(ref_type="unit", ref_id=2, label="R2", square_feet=Decimal("1000"), ownership_percent=Decimal("0.50"), parking_spaces=0),
    ]
    engine_pools = [
        PoolDefinition(pool_id=1, pool_key="general_prorated", pool_name="General Prorated", allocation_method="ownership_percentage", recipient_scope="all_units", denominator_value=Decimal("100"), display_order=1),
        PoolDefinition(pool_id=2, pool_key="general_equal", pool_name="General Equal", allocation_method="equal", recipient_scope="all_units", display_order=2),
        PoolDefinition(pool_id=3, pool_key="residential_prorated", pool_name="Residential Prorated", allocation_method="square_footage", recipient_scope="residential_only", denominator_value=Decimal("2000"), display_order=3),
        PoolDefinition(pool_id=4, pool_key="residential_equal", pool_name="Residential Equal", allocation_method="equal", recipient_scope="residential_only", display_order=4),
        PoolDefinition(pool_id=5, pool_key="parking", pool_name="Parking", allocation_method="specified_value", recipient_scope="parking_users", display_order=5),
    ]
    result = run(CalcInput(
        setup_type="per_unit",
        pools=engine_pools,
        recipient_set=RecipientSet(recipients=units),
        budget_lines=[
            _budget("gp", "1200"),
            _budget("ge", "1200"),
            _budget("rp", "1200"),
            _budget("re", "1200"),
            _budget("pk", "300"),
        ],
        mappings=[
            _mapping("gp", "general_prorated"),
            _mapping("ge", "general_equal"),
            _mapping("rp", "residential_prorated"),
            _mapping("re", "residential_equal"),
            _mapping("pk", "parking"),
        ],
        approved_assessment_revenue_annual=Decimal("5100"),
        specified_value_lookup={(1, "parking"): Decimal("25")},
    ))
    display_pools = [
        SimpleNamespace(**pool.model_dump(), parent_pool_key="general_common", parent_pool_label="General Common", included_budget_lines=["gp"], child_mapping_approved=True)
        if pool.pool_key.startswith("general")
        else SimpleNamespace(**pool.model_dump(), parent_pool_key="residential_common", parent_pool_label="Residential Common", included_budget_lines=["rp"], child_mapping_approved=True)
        if pool.pool_key.startswith("residential")
        else pool
        for pool in engine_pools
    ]

    matrix = build_universal_assessment_matrix(
        result,
        setup_type="per_unit",
        hoa_name="800 High",
        fiscal_year=2026,
        pool_definitions=display_pools,
        source_pages=[22],
    )

    assert matrix.layout_hints.orientation == "landscape"
    assert matrix.layout_hints.split_strategy == "by_component_group"
    assert [group.parent_key for group in matrix.component_column_groups] == [
        "general_common",
        "residential_common",
    ]
    assert matrix.component_columns[0].parent_label == "General Common"

    copied_child = SimpleNamespace(
        pool_key="general_equal",
        pool_name="General Equal",
        allocation_method="equal",
        recipient_scope="all_units",
        include_in_pdf=True,
        display_order=1,
        parent_pool_key="general_common",
        included_budget_lines=["all-parent-lines"],
        child_mapping_status="copied_from_parent",
        child_mapping_approved=False,
    )
    blocked = build_universal_assessment_matrix(
        _empty_result(),
        setup_type="per_unit",
        hoa_name="800 High",
        fiscal_year=2026,
        pool_definitions=[copied_child],
        source_pages=[22],
    )
    assert any("child-level" in issue.message for issue in blocked.preflight_issues)


def test_cambridge_budget_only_falls_back_to_manual_review() -> None:
    matrix = build_universal_assessment_matrix(
        _empty_result(),
        setup_type="fixed",
        hoa_name="Cambridge Plaza",
        fiscal_year=2026,
        approved_visual_basis=False,
        source_pages=[3],
        manual_review_reason="Budget page is visible, but allocation basis is missing.",
    )

    assert matrix.recipient_grain == "manual_review"
    assert not matrix.is_final_renderable
    assert matrix.rows[0].recipient_grain == "manual_review"
    assert "allocation basis is missing" in matrix.preflight_issues[0].message


def test_source_pages_are_internal_unless_explicitly_enabled() -> None:
    matrix = build_universal_assessment_matrix(
        _empty_result(),
        setup_type="fixed",
        hoa_name="Old Mill",
        fiscal_year=2026,
        source_pages=[14],
    )
    hidden_text = _text_from_matrix_pdf(matrix)
    assert "Page 14" not in hidden_text

    visible = matrix.model_copy(
        update={
            "source_pages_visible": True,
            "method_summary": matrix.method_summary.model_copy(update={"render_source_pages": True}),
        }
    )
    visible_text = _text_from_matrix_pdf(visible)
    assert "Page 14" in visible_text


def test_footer_rows_render_supplied_values_without_template_math() -> None:
    matrix = build_universal_assessment_matrix(
        _empty_result(),
        setup_type="fixed",
        hoa_name="Old Mill",
        fiscal_year=2026,
        source_pages=[14],
        footer_rows=[
            FooterRow(
                kind="reconciliation_difference",
                label="Reconciliation Difference",
                values={"total_annual_revenue": Decimal("1.23")},
            )
        ],
    )

    text = _text_from_matrix_pdf(matrix)
    assert "Reconciliation Difference" in text
    assert "1.23" in text


def test_preflight_covers_finalization_blockers() -> None:
    matrix = build_universal_assessment_matrix(
        _empty_result(),
        setup_type="fixed",
        hoa_name="Old Mill",
        fiscal_year=2026,
        evidence_refs=[
            EvidenceRef(
                field="recipient_grain",
                source_type="operator_approval",
                operator_approval_ref="approval-1",
                approved_by_operator=True,
            )
        ],
    )

    errors = validate_assessment_matrix_finalization(
        matrix,
        dre_setup_approved=False,
        required_budget_lines_unmapped=True,
        special_assessment_settings_complete=False,
        unit_count_mismatch_unresolved=True,
    )

    paths = {error.field_path for error in errors}
    assert "assessment_setup.status" in paths
    assert "assessment_schedule.budget_line_mappings" in paths
    assert "hoa_settings.special_assessments_json" in paths
    assert "assessment_schedule.unit_count" in paths


def test_special_assessment_events_map_outside_regular_matrix_totals() -> None:
    result = _empty_result().model_copy(
        update={
            "special_assessment_events": [
                SpecialAssessmentRendererEvent(
                    kind="separate_disclosure_block",
                    label="Roof Special Assessment",
                    amount_per_unit=Decimal("250"),
                    due_date="2026-06-01",
                )
            ]
        }
    )
    matrix = build_universal_assessment_matrix(
        result,
        setup_type="fixed",
        hoa_name="Old Mill",
        fiscal_year=2026,
        source_pages=[14],
    )

    assert matrix.special_assessment_blocks[0].label == "Roof Special Assessment"
    text = _text_from_matrix_pdf(matrix)
    assert "Roof Special Assessment" in text
    assert "250.00" in text
