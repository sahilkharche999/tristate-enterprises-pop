"""Reserve contribution lines must be schedule-basis and suggest the reserve pool."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.assessment_budget_mapping_rule_service import (
    build_assessment_mapping_review_rows,
    _explicit_rule_requires_missing_specificity,
    _is_generic_reserve_contribution_label,
    _pool_options_for_row_role,
    _preferred_reserve_pool_key,
    _residual_pool_key,
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "db" / "schema.sql"
if not SCHEMA_PATH.exists():
    # Fall back to the same location used by mapping-rule tests.
    SCHEMA_PATH = Path(__file__).resolve().parent / "fixtures" / "mapping_rules_schema.sql"
    if not SCHEMA_PATH.exists():
        from tests import test_assessment_budget_mapping_rule_service as _m
        SCHEMA_PATH = _m.SCHEMA_PATH


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(tmp_path / "contrib.db"))
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_PATH.read_text())
    yield connection
    connection.close()


def _setup(conn: sqlite3.Connection) -> tuple[int, int]:
    from tests.test_assessment_budget_mapping_rule_service import _setup as _shared_setup
    return _shared_setup(conn)


def test_preferred_reserve_pool_key_picks_reserve_contributions() -> None:
    opts = [
        {"pool_key": "general_operating", "pool_name": "Operating Expenses"},
        {"pool_key": "reserve_contributions", "pool_name": "Reserve Contributions"},
    ]
    assert _preferred_reserve_pool_key(opts) == "reserve_contributions"
    contrib_opts = _pool_options_for_row_role(
        "current_year_reserve_contribution_line", opts
    )
    assert [o["pool_key"] for o in contrib_opts] == [
        "general_operating",
        "reserve_contributions",
    ]


def test_contribution_line_is_schedule_basis_and_suggests_reserve_pool(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    # Add the reserve contributions pool alongside the fixture's prorated pool.
    conn.execute(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES
            (?, 'reserve_contributions', 'Reserve Contributions',
             'ownership_percentage', 'all_units', 'explicit_lines')
        """,
        (setup_id,),
    )
    # Rename the fixture pool so contribution options filter to reserve only.
    conn.execute(
        """
        UPDATE allocation_pools
           SET pool_key = 'general_operating',
               pool_name = 'Operating Expenses'
         WHERE assessment_setup_id = ?
           AND pool_key = 'total_budget_prorated'
        """,
        (setup_id,),
    )

    rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2024,
        budget_lines=[
            {
                "label": "Management Fee",
                "category": "operating",
                "fund_type": "operating",
                "amount": 23760,
                "section": "operating",
            },
            {
                "label": "Reserve - Allocation/Transfer",
                "category": "operating",
                "fund_type": "operating",
                "amount": 105917,
                "section": "operating",
            },
            {
                "label": "Landscaping Tree Trimming",
                "category": "reserve_expense",
                "fund_type": "reserve",
                "amount": 9548,
                "section": "reserve",
            },
        ],
        connection=conn,
    )
    by = {r["line_label"]: r for r in rows}
    assert by["Management Fee"]["included_in_regular_basis"] is True
    xfer = by["Reserve - Allocation/Transfer"]
    assert xfer["row_role"] == "current_year_reserve_contribution_line"
    assert xfer["included_in_regular_basis"] is True
    assert xfer["recommended_pool_key"] == "reserve_contributions"
    option_keys = [o["pool_key"] for o in xfer["valid_pool_options"]]
    assert "reserve_contributions" in option_keys
    assert "general_operating" in option_keys
    assert by["Landscaping Tree Trimming"]["included_in_regular_basis"] is False
    assert by["Landscaping Tree Trimming"]["current_status"] == "reserve_detail"


def test_contribution_options_include_equal_and_generic_transfer_defaults_to_it(
    conn: sqlite3.Connection,
) -> None:
    from app.services.assessment_budget_mapping_rule_service import (
        derive_rules_from_dre_extraction,
    )
    from app.dre_extraction.schemas import (
        AllocationPoolBlock,
        AssessmentSetupBlock,
        DRESetupExtraction,
        DocumentMetadata,
        UnitStructure,
    )
    from decimal import Decimal

    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES
            (?, 'equal_base', 'Equal Base Operating', 'equal',
             'all_units', 'residual_default'),
            (?, 'dre_prorated_reserve_exceptions',
             'DRE Prorated Reserve Exceptions', 'ownership_percentage',
             'all_units', 'explicit_lines')
        """,
        (setup_id, setup_id),
    )
    extraction = DRESetupExtraction(
        document_metadata=DocumentMetadata(
            association_name="Rule Test",
            total_units=9,
            source_pages=[1],
        ),
        assessment_setup=AssessmentSetupBlock(
            setup_type="multi_pool_combination",
            display_mode="per_unit",
            source_pages=[6],
        ),
        unit_structure=UnitStructure(unit_count=9),
        allocation_pools=[
            AllocationPoolBlock(
                pool_key="equal_base",
                pool_name="Equal Base Operating",
                allocation_method="equal",
                recipient_scope="all_units",
                included_budget_lines=[],
                source_pages=[6],
                budget_line_derivation="residual_default",
            ),
            AllocationPoolBlock(
                pool_key="dre_prorated_reserve_exceptions",
                pool_name="DRE Prorated Reserve Exceptions",
                allocation_method="ownership_percentage",
                recipient_scope="all_units",
                included_budget_lines=[
                    "reserves for roof, paint and water heaters",
                ],
                source_pages=[6],
                budget_line_derivation="explicit_lines",
            ),
            AllocationPoolBlock(
                pool_key="dre_prorated_operating_exceptions",
                pool_name="DRE Prorated Operating Exceptions",
                allocation_method="ownership_percentage",
                recipient_scope="all_units",
                included_budget_lines=["insurance", "gas and water"],
                source_pages=[6],
                budget_line_derivation="explicit_lines",
            ),
        ],
    )
    derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=extraction,
        connection=conn,
    )
    rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2025,
        budget_lines=[
            {
                "label": "Reserve - Allocation/Transfer",
                "category": "operating",
                "fund_type": "operating",
                "amount": 31935,
                "section": "operating",
            },
            {
                "label": "Trash Collection Service",
                "category": "operating",
                "fund_type": "operating",
                "amount": 7200,
                "section": "operating",
            },
            {
                "label": "Roof Reserve",
                "category": "operating",
                "fund_type": "operating",
                "amount": 1028,
                "section": "operating",
            },
            {
                "label": "Electricity & Gas",
                "category": "operating",
                "fund_type": "operating",
                "amount": 16800,
                "section": "operating",
            },
        ],
        connection=conn,
    )
    by = {r["line_label"]: r for r in rows}
    xfer = by["Reserve - Allocation/Transfer"]
    option_keys = [o["pool_key"] for o in xfer["valid_pool_options"]]
    assert "equal_base" in option_keys
    assert "dre_prorated_reserve_exceptions" in option_keys
    assert xfer["recommended_pool_key"] == "equal_base"
    assert by["Trash Collection Service"]["recommended_pool_key"] == "equal_base"
    assert by["Roof Reserve"]["recommended_pool_key"] == (
        "dre_prorated_reserve_exceptions"
    )
    assert by["Electricity & Gas"]["recommended_pool_key"] == (
        "dre_prorated_operating_exceptions"
    )


def test_generic_transfer_and_specificity_are_vocabulary_free() -> None:
    assert _is_generic_reserve_contribution_label("Reserve - Allocation/Transfer")
    assert _is_generic_reserve_contribution_label("Reserve Contribution")
    assert not _is_generic_reserve_contribution_label("Elevator Reserve")
    assert not _is_generic_reserve_contribution_label("Paving Reserve")
    assert _explicit_rule_requires_missing_specificity(
        "reserves for elevator and paving",
        "Reserve - Allocation/Transfer",
    )
    assert not _explicit_rule_requires_missing_specificity(
        "reserves for elevator and paving",
        "Elevator Reserve",
    )
    assert not _explicit_rule_requires_missing_specificity(
        "reserves",
        "Reserve - Allocation/Transfer",
    )


def test_residual_pool_key_prefers_derivation_not_name() -> None:
    assert (
        _residual_pool_key(
            [
                {
                    "pool_key": "prorated_exceptions",
                    "pool_name": "Named Exceptions",
                    "budget_line_derivation": "explicit_lines",
                    "allocation_method": "ownership_percentage",
                },
                {
                    "pool_key": "common_expense_share",
                    "pool_name": "Common Expense Share",
                    "budget_line_derivation": "residual_default",
                    "allocation_method": "equal",
                },
            ]
        )
        == "common_expense_share"
    )


def test_other_hoa_ccr_exceptions_do_not_steal_generic_transfer(
    conn: sqlite3.Connection,
) -> None:
    """Any setup: residual_default wins for leftover reserve and unmatched lines."""
    from app.services.assessment_budget_mapping_rule_service import (
        derive_rules_from_dre_extraction,
    )
    from app.dre_extraction.schemas import (
        AllocationPoolBlock,
        AssessmentSetupBlock,
        DRESetupExtraction,
        DocumentMetadata,
        UnitStructure,
    )

    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES
            (?, 'common_expense_share', 'Common Expense Share', 'equal',
             'all_units', 'residual_default'),
            (?, 'named_reserve_exceptions',
             'Named Reserve Exceptions', 'ownership_percentage',
             'all_units', 'explicit_lines')
        """,
        (setup_id, setup_id),
    )
    extraction = DRESetupExtraction(
        document_metadata=DocumentMetadata(
            association_name="Rule Test",
            total_units=12,
            source_pages=[1],
        ),
        assessment_setup=AssessmentSetupBlock(
            setup_type="multi_pool_combination",
            display_mode="per_unit",
            source_pages=[3],
        ),
        unit_structure=UnitStructure(unit_count=12),
        allocation_pools=[
            AllocationPoolBlock(
                pool_key="common_expense_share",
                pool_name="Common Expense Share",
                allocation_method="equal",
                recipient_scope="all_units",
                included_budget_lines=[],
                source_pages=[3],
                budget_line_derivation="residual_default",
            ),
            AllocationPoolBlock(
                pool_key="named_reserve_exceptions",
                pool_name="Named Reserve Exceptions",
                allocation_method="ownership_percentage",
                recipient_scope="all_units",
                included_budget_lines=[
                    "reserves for elevator and paving",
                ],
                source_pages=[3],
                budget_line_derivation="explicit_lines",
            ),
        ],
    )
    derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=extraction,
        connection=conn,
    )
    rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_lines=[
            {
                "label": "Reserve - Allocation/Transfer",
                "category": "operating",
                "fund_type": "operating",
                "amount": 48000,
                "section": "operating",
            },
            {
                "label": "Trash Collection Service",
                "category": "operating",
                "fund_type": "operating",
                "amount": 6000,
                "section": "operating",
            },
            {
                "label": "Elevator Reserve",
                "category": "operating",
                "fund_type": "operating",
                "amount": 4100,
                "section": "operating",
            },
        ],
        connection=conn,
    )
    by = {r["line_label"]: r for r in rows}
    xfer = by["Reserve - Allocation/Transfer"]
    option_keys = [o["pool_key"] for o in xfer["valid_pool_options"]]
    assert "common_expense_share" in option_keys
    assert "named_reserve_exceptions" in option_keys
    assert xfer["recommended_pool_key"] == "common_expense_share"
    assert by["Trash Collection Service"]["recommended_pool_key"] == (
        "common_expense_share"
    )
    assert by["Elevator Reserve"]["recommended_pool_key"] == (
        "named_reserve_exceptions"
    )


def test_pool_options_for_contribution_include_every_current_pool() -> None:
    opts = [
        {"pool_key": "equal_base", "pool_name": "Equal Base Operating"},
        {
            "pool_key": "dre_prorated_reserve_exceptions",
            "pool_name": "DRE Prorated Reserve Exceptions",
        },
    ]
    keys = [
        o["pool_key"]
        for o in _pool_options_for_row_role(
            "current_year_reserve_contribution_line", opts
        )
    ]
    assert keys == [
        "equal_base",
        "dre_prorated_reserve_exceptions",
    ]
