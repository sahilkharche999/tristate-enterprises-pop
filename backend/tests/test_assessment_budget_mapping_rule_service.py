from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.dre_extraction.schemas import (
    AllocationPoolBlock,
    AssessmentSetupBlock,
    BudgetLineMappingEvidence,
    DRESetupExtraction,
    DocumentMetadata,
    UnitStructure,
)
from app.services.assessment_budget_mapping_rule_service import (
    BudgetLineEligibility,
    backfill_rules_for_promoted_extraction_run,
    build_assessment_mapping_review_rows,
    build_line_review_items,
    classify_budget_lines_for_mapping,
    carry_forward_reusable_mapping_rules_across_setups,
    derive_rules_from_dre_extraction,
    get_mapping_reconciliation,
    ensure_exemption_decisions_from_dre_extraction,
    is_remainder_eligible_budget_line,
    materialize_budget_line_pool_mappings,
    normalize_budget_label,
    record_scoped_alias,
    set_exemption_decision_state,
)


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "rules.db"
    connection = sqlite3.connect(str(db_path))
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_PATH.read_text())
    yield connection
    connection.close()


def _setup(conn: sqlite3.Connection) -> tuple[int, int]:
    conn.execute("INSERT INTO properties (name, units) VALUES ('Rule Test', 20)")
    property_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO dre_documents
            (id, property_id, file_id, file_name, status)
        VALUES
            (11, ?, 'dre/rule-test.pdf', 'rule-test.pdf', 'active')
        """,
        (property_id,),
    )
    conn.execute(
        """
        INSERT INTO dre_extraction_runs
            (id, dre_document_id, property_id, model_name,
             prompt_version, prompt_sha256, status)
        VALUES
            (11, 11, ?, 'gemini-test', 'test', 'sha', 'succeeded')
        """,
        (property_id,),
    )
    conn.execute(
        """
        INSERT INTO assessment_setups
            (property_id, setup_type, display_mode, status)
        VALUES
            (?, 'grouped', 'grouped', 'approved')
        """,
        (property_id,),
    )
    setup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES
            (?, 'total_budget_prorated', 'Prorated', 'square_footage',
             'all_units', 'explicit_lines')
        """,
        (setup_id,),
    )
    return property_id, setup_id


def _extraction() -> DRESetupExtraction:
    return DRESetupExtraction(
        document_metadata=DocumentMetadata(
            association_name="Rule Test",
            total_units=20,
            source_pages=[1],
        ),
        assessment_setup=AssessmentSetupBlock(
            setup_type="grouped_category",
            display_mode="grouped",
            source_pages=[6],
        ),
        unit_structure=UnitStructure(unit_count=20),
        allocation_pools=[
            AllocationPoolBlock(
                pool_key="total_budget_prorated",
                parent_pool_key="total_budget",
                pool_name="Prorated",
                annual_amount=Decimal("24642"),
                monthly_amount=Decimal("2054"),
                allocation_method="square_footage",
                recipient_scope="all_units",
                denominator_label="Total square feet",
                denominator_value=Decimal("25462"),
                denominator_source="dre_shown",
                included_budget_lines=["Insurance", "Domestic Water"],
                source_pages=[6],
                confidence=0.95,
                budget_line_derivation="explicit_lines",
            )
        ],
    )


def _residual_extraction() -> DRESetupExtraction:
    extraction = _extraction()
    extraction.allocation_pools.append(
        AllocationPoolBlock(
            pool_key="total_budget_equal",
            parent_pool_key="total_budget",
            pool_name="Total Budget - Equal Component",
            annual_amount=Decimal("102451"),
            monthly_amount=Decimal("8538"),
            allocation_method="equal",
            recipient_scope="all_units",
            denominator_label="units",
            denominator_value=Decimal("20"),
            denominator_source="dre_shown",
            included_budget_lines=[],
            source_pages=[6],
            confidence=0.95,
            budget_line_derivation="residual_default",
            residual_after_pool_keys=["total_budget_prorated", "exempted_costs"],
            residual_exclusions=["income_only", "pass_through"],
        )
    )
    return extraction


def _mapping_evidence_extraction(
    *,
    source_label: str = "Insurance",
    parent_category: str = "Insurance",
    assessment_type: str = "prorated_variable",
    review_required: bool = False,
    review_reason: str = "",
    account_code: str | None = None,
    pool_key: str = "total_budget_prorated",
) -> DRESetupExtraction:
    extraction = _extraction()
    extraction.budget_line_mapping_evidence = [
        BudgetLineMappingEvidence(
            account_code=account_code,
            source_label=source_label,
            parent_category=parent_category,
            assessment_pool_key=pool_key,
            assessment_type=assessment_type,
            match_confidence=0.95,
            review_required=review_required,
            review_reason=review_reason,
            source_page=6,
            source_evidence_text=f"{source_label} belongs to {pool_key}.",
        )
    ]
    return extraction


def test_normalize_budget_label_is_stable_for_matching() -> None:
    assert normalize_budget_label("  Domestic   Water (if common) ") == (
        "domestic water if common"
    )


def test_derives_reusable_rules_from_dre_included_budget_lines(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)

    count = derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=_extraction(),
        connection=conn,
    )

    assert count == 2
    rows = conn.execute(
        """
        SELECT pool_key, match_label, normalized_label, match_type,
               rule_source, approval_status, review_state,
               source_dre_extraction_run_id, source_pages_json,
               confidence, budget_line_derivation
          FROM assessment_budget_mapping_rules
         ORDER BY match_label
        """
    ).fetchall()
    assert rows[0][0] == "total_budget_prorated"
    assert rows[0][1] == "Domestic Water"
    assert rows[0][2] == "domestic water"
    assert rows[0][3] == "exact_label"
    assert rows[0][4] == "dre_included_budget_line"
    assert rows[0][5] == "suggested"
    assert rows[0][6] == "pending_review"
    assert rows[0][7] == 11
    assert json.loads(rows[0][8]) == [6]
    assert rows[0][9] == 0.95
    assert rows[0][10] == "explicit_lines"


def test_rule_derivation_is_idempotent(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)

    first_count = derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=_extraction(),
        connection=conn,
    )
    second_count = derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=_extraction(),
        connection=conn,
    )

    stored_count = conn.execute(
        "SELECT COUNT(*) FROM assessment_budget_mapping_rules"
    ).fetchone()[0]
    assert first_count == 2
    assert second_count == 0
    assert stored_count == 2


def test_residual_default_pool_creates_remainder_rule(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES
            (?, 'total_budget_equal', 'Equal', 'equal',
             'all_units', 'residual_default')
        """,
        (setup_id,),
    )

    count = derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=_residual_extraction(),
        connection=conn,
    )

    assert count == 3
    row = conn.execute(
        """
        SELECT match_type, rule_source, approval_status, review_state,
               budget_line_derivation, residual_after_pool_keys_json,
               residual_exclusions_json
          FROM assessment_budget_mapping_rules
         WHERE pool_key = 'total_budget_equal'
        """
    ).fetchone()
    assert row[0] == "remainder"
    assert row[1] == "system_remainder"
    assert row[2] == "suggested"
    assert row[3] == "pending_review"
    assert row[4] == "residual_default"
    assert json.loads(row[5]) == ["total_budget_prorated", "exempted_costs"]
    assert json.loads(row[6]) == ["income_only", "pass_through"]


def test_empty_included_lines_without_residual_metadata_does_not_create_rule(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    extraction = _extraction()
    extraction.allocation_pools.append(
        AllocationPoolBlock(
            pool_key="unknown_equal",
            pool_name="Unknown Equal",
            allocation_method="equal",
            recipient_scope="all_units",
            denominator_source="unknown",
            included_budget_lines=[],
            source_pages=[7],
            confidence=0.4,
            budget_line_derivation="unknown",
        )
    )

    count = derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=extraction,
        connection=conn,
    )

    assert count == 2
    unknown_count = conn.execute(
        """
        SELECT COUNT(*)
          FROM assessment_budget_mapping_rules
         WHERE pool_key = 'unknown_equal'
        """
    ).fetchone()[0]
    assert unknown_count == 0


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ({"label": "Assessment Income", "category": "income", "amount": 100}, False),
        ({"label": "Water Reimbursement", "category": "operating", "amount": 100}, False),
        ({"label": "Special Assessment", "category": "operating", "amount": 100}, False),
        ({"label": "Zero Line", "category": "operating", "amount": 0}, False),
        ({"label": "Inactive Line", "category": "operating", "amount": 100, "active": False}, False),
        ({"label": "Reserve Contribution", "category": "reserve_expense", "amount": 100}, False),
        ({"label": "Management", "category": "operating", "amount": 100}, True),
    ],
)
def test_remainder_eligibility_filter(line: dict, expected: bool) -> None:
    assert (
        is_remainder_eligible_budget_line(
            line,
            already_mapped_normalized_labels={"already mapped"},
        )
        is expected
    )


def test_remainder_eligibility_excludes_already_mapped_line() -> None:
    assert not is_remainder_eligible_budget_line(
        {"label": "Already Mapped", "category": "operating", "amount": 100},
        already_mapped_normalized_labels={"already mapped"},
    )


def test_classifies_budget_lines_and_duplicate_conflicts() -> None:
    result = classify_budget_lines_for_mapping([
        {"label": "Assessment Revenue", "category": "income", "amount": 1200},
        {"label": "Late Fee Income", "category": "income", "amount": 50},
        {"label": "Water Reimbursement", "category": "operating", "amount": 100},
        {"label": "Pass Through Electric", "category": "operating", "amount": 200},
        {"label": "Inactive", "category": "operating", "amount": 1, "active": False},
        {"label": "Zero", "category": "operating", "amount": 0},
        {"label": "Insurance", "category": "operating", "amount": 1000},
        {"label": "Insurance", "category": "operating", "amount": 1000},
        {"label": "Landscaping", "category": "operating", "amount": 700},
        {"label": "Landscaping", "category": "operating", "amount": 800},
        {"label": "", "category": "operating", "amount": 12},
    ])

    by_label = {item.line_label: item for item in result.classifications}
    assert by_label["Assessment Revenue"].eligibility == "assessment_revenue_tieout"
    assert by_label["Late Fee Income"].eligibility == "late_fee_income"
    assert by_label["Water Reimbursement"].eligibility == "reimbursement"
    assert by_label["Pass Through Electric"].eligibility == "pass_through"
    assert by_label["Inactive"].eligibility == "inactive"
    assert by_label["Zero"].eligibility == "zero_or_blank"
    canonical_insurance = [
        item for item in result.classifications
        if item.line_label == "Insurance" and item.canonical
    ][0]
    assert canonical_insurance.eligibility == "assessable_expense"
    assert by_label[""].eligibility == "unknown"

    duplicate_items = [
        item for item in result.classifications
        if item.eligibility == "duplicate_raw_or_normalized"
    ]
    assert len(duplicate_items) == 1
    assert duplicate_items[0].line_label == "Insurance"
    assert result.duplicate_conflicts[0].normalized_label == "landscaping"


def test_non_blocking_lines_are_not_counted_unmatched(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {"label": "Assessment Revenue", "category": "income", "fund_type": "operating", "amount": 1000},
            {"label": "Insurance", "category": "operating", "fund_type": "operating", "amount": 500},
        ],
        connection=conn,
    )

    assert result["non_blocking"] == 1
    assert result["unmatched"] == 1


def test_duplicate_rows_map_only_one_canonical_copy(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, normalized_label,
             match_type, rule_source, approval_status, review_state)
        VALUES
            (?, ?, 'total_budget_prorated', 'insurance',
             'normalized_label', 'operator', 'approved', 'ready')
        """,
        (property_id, setup_id),
    )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {"label": "Insurance", "category": "operating", "fund_type": "operating", "amount": 1000},
            {"label": "Insurance", "category": "operating", "fund_type": "operating", "amount": 1000},
        ],
        connection=conn,
    )

    assert result["auto_approved"] == 1
    assert result["non_blocking"] == 1
    assert conn.execute("SELECT COUNT(*) FROM budget_line_pool_mappings").fetchone()[0] == 1


def test_raw_and_client_style_labels_are_canonicalized_as_one_budget_line() -> None:
    result = classify_budget_lines_for_mapping([
        {
            "label": "55000 - General Insurance",
            "category": "operating",
            "fund_type": "operating",
            "account_code": "55000",
            "amount": 15000,
        },
        {
            "label": "Insurance",
            "category": "operating",
            "fund_type": "operating",
            "amount": 15000,
        },
    ])

    canonical = [item for item in result.classifications if item.canonical]
    assert len(canonical) == 1
    assert canonical[0].line_label == "Insurance"
    assert canonical[0].line_key[4] == "55000"


def test_approved_scoped_alias_precedes_exact_label_rule(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES
            (?, 'alias_pool', 'Alias Pool', 'equal', 'all_units', 'explicit_lines')
        """,
        (setup_id,),
    )
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, normalized_label,
             match_type, rule_source, approval_status, review_state)
        VALUES
            (?, ?, 'total_budget_prorated', 'general insurance',
             'normalized_label', 'operator', 'approved', 'ready')
        """,
        (property_id, setup_id),
    )
    record_scoped_alias(
        property_id=property_id,
        assessment_setup_id=setup_id,
        pool_key="alias_pool",
        dre_label="Insurance",
        budget_label="General Insurance",
        account_code=None,
        actor="ops@example.com",
        note="DRE short label.",
        connection=conn,
    )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "General Insurance",
                "category": "operating",
                "fund_type": "operating",
                "amount": 1200,
            }
        ],
        connection=conn,
    )

    row = conn.execute(
        "SELECT pool_key, mapping_source, match_method FROM budget_line_pool_mappings"
    ).fetchone()
    assert result["auto_approved"] == 1
    assert row == ("alias_pool", "alias", "approved_alias")


def test_materialization_marks_stale_auto_rows_when_budget_line_disappears(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO budget_line_pool_mappings
             (property_id, assessment_setup_id, budget_line_normalized_label,
             section, category, fund_type, pool_key, mapping_source,
             source_rule_id)
        VALUES
            (?, ?, 'old insurance', 'operating', 'operating', 'operating',
             'total_budget_prorated', 'normalized_label', NULL)
        """,
        (property_id, setup_id),
    )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "New Insurance",
                "section": "operating",
                "category": "operating",
                "fund_type": "operating",
                "amount": 1200,
            }
        ],
        connection=conn,
    )

    row = conn.execute(
        """
        SELECT active, review_state
          FROM budget_line_pool_mappings
         WHERE budget_line_normalized_label = 'old insurance'
        """
    ).fetchone()
    assert result["stale"] == 1
    assert row == (0, "stale")


def test_reconciliation_reports_pass_and_fail(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO budget_line_pool_mappings
            (property_id, assessment_setup_id, budget_line_normalized_label,
             section, category, fund_type, pool_key, budget_line_amount)
        VALUES
            (?, ?, 'insurance', 'operating', 'operating', 'operating',
             'total_budget_prorated', 1000)
        """,
        (property_id, setup_id),
    )

    passed = get_mapping_reconciliation(
        property_id=property_id,
        assessment_setup_id=setup_id,
        assessment_target=1000,
        selected_budget_source_total=1300,
        excluded_total=200,
        offset_total=100,
        schedule_annual_total=1000,
        connection=conn,
    )
    failed = get_mapping_reconciliation(
        property_id=property_id,
        assessment_setup_id=setup_id,
        assessment_target=900,
        selected_budget_source_total=1300,
        excluded_total=0,
        offset_total=0,
        schedule_annual_total=800,
        connection=conn,
    )

    assert passed.passed is True
    assert failed.passed is False
    assert failed.failures == [
        "mapped_pool_total_mismatch",
        "schedule_total_mismatch",
        "budget_source_total_mismatch",
    ]


def test_exemption_pool_defaults_to_budget_year_pending_review(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    extraction = _extraction()
    extraction.allocation_pools.append(
        AllocationPoolBlock(
            pool_key="exempted_costs",
            pool_name="DRE Regulation 2792.16(c) Exemptions",
            annual_amount=Decimal("-4800"),
            monthly_amount=Decimal("-400"),
            allocation_method="equal",
            recipient_scope="all_units",
            denominator_source="dre_shown",
            included_budget_lines=[
                "DRE Regulation 2792.16(c) - Exemptions or Unaccepted Common Area"
            ],
            source_pages=[3, 5, 6],
            confidence=0.95,
            budget_line_derivation="explicit_lines",
        )
    )

    count = ensure_exemption_decisions_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_draft_id=None,
        extraction=extraction,
        connection=conn,
    )

    assert count == 1
    row = conn.execute(
        """
        SELECT pool_key, exemption_state, evidence_ref_json
          FROM assessment_exemption_decisions
        """
    ).fetchone()
    assert row[0] == "exempted_costs"
    assert row[1] == "pending_review"
    assert json.loads(row[2]) == {"source_pages": [3, 5, 6]}


def test_exemption_decision_state_can_be_set_active_or_inactive(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO assessment_exemption_decisions
            (property_id, assessment_setup_id, budget_year, pool_key, exemption_state)
        VALUES
            (?, ?, 2026, 'exempted_costs', 'pending_review')
        """,
        (property_id, setup_id),
    )

    set_exemption_decision_state(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_year=2026,
        budget_draft_id=None,
        pool_key="exempted_costs",
        exemption_state="inactive",
        decided_by="operator@example.com",
        notes="Common area accepted.",
        connection=conn,
    )

    row = conn.execute(
        """
        SELECT exemption_state, decided_by, notes
          FROM assessment_exemption_decisions
         WHERE pool_key = 'exempted_costs'
        """
    ).fetchone()
    assert row[0] == "inactive"
    assert row[1] == "operator@example.com"
    assert row[2] == "Common area accepted."


def test_carry_forward_reusable_rules_preserves_approved_when_structure_matches(
    conn: sqlite3.Connection,
) -> None:
    property_id, old_setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_label,
             normalized_label, match_type, rule_source, approval_status,
             review_state, budget_line_derivation)
        VALUES
            (?, ?, 'total_budget_prorated', 'Insurance', 'insurance',
             'exact_label', 'operator', 'approved', 'ready', 'explicit_lines')
        """,
        (property_id, old_setup_id),
    )
    conn.execute(
        """
        INSERT INTO assessment_setups
            (property_id, setup_type, display_mode, status)
        VALUES
            (?, 'grouped', 'grouped', 'approved')
        """,
        (property_id,),
    )
    new_setup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, denominator_source, budget_line_derivation)
        VALUES
            (?, 'total_budget_prorated', 'Prorated', 'square_footage',
             'all_units', 'dre_value', 'explicit_lines')
        """,
        (new_setup_id,),
    )

    count = carry_forward_reusable_mapping_rules_across_setups(
        property_id=property_id,
        old_setup_id=old_setup_id,
        new_setup_id=new_setup_id,
        connection=conn,
    )

    row = conn.execute(
        """
        SELECT approval_status, review_state, rule_source
          FROM assessment_budget_mapping_rules
         WHERE assessment_setup_id = ?
        """,
        (new_setup_id,),
    ).fetchone()
    assert count == 1
    assert row == ("approved", "ready", "carried_forward")


def test_carry_forward_reusable_rules_requires_structural_compatibility(
    conn: sqlite3.Connection,
) -> None:
    property_id, old_setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_label,
             normalized_label, match_type, rule_source, approval_status,
             review_state, budget_line_derivation)
        VALUES
            (?, ?, 'total_budget_prorated', 'Insurance', 'insurance',
             'exact_label', 'operator', 'approved', 'ready', 'explicit_lines')
        """,
        (property_id, old_setup_id),
    )
    conn.execute(
        """
        INSERT INTO assessment_setups
            (property_id, setup_type, display_mode, status)
        VALUES
            (?, 'grouped', 'grouped', 'approved')
        """,
        (property_id,),
    )
    new_setup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, denominator_source, budget_line_derivation)
        VALUES
            (?, 'total_budget_prorated', 'Prorated', 'equal',
             'all_units', 'dre_value', 'explicit_lines')
        """,
        (new_setup_id,),
    )

    carry_forward_reusable_mapping_rules_across_setups(
        property_id=property_id,
        old_setup_id=old_setup_id,
        new_setup_id=new_setup_id,
        connection=conn,
    )

    row = conn.execute(
        """
        SELECT approval_status, review_state
          FROM assessment_budget_mapping_rules
         WHERE assessment_setup_id = ?
        """,
        (new_setup_id,),
    ).fetchone()
    assert row == ("suggested", "pending_review")


def test_backfill_rules_for_promoted_run_uses_stored_parsed_json(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        UPDATE dre_extraction_runs
           SET parsed_json = ?, review_status = 'promoted',
               promoted_setup_id = ?
         WHERE id = 11
        """,
        (_extraction().model_dump_json(), setup_id),
    )

    count = backfill_rules_for_promoted_extraction_run(
        extraction_run_id=11,
        connection=conn,
    )

    assert count == 2


def test_materializes_exact_account_code_match(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, account_code,
             match_type, rule_source, approval_status, review_state)
        VALUES
            (?, ?, 'total_budget_prorated', '6100', 'account_code',
             'operator', 'approved', 'ready')
        """,
        (property_id, setup_id),
    )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Insurance",
                "normalized_label": "insurance",
                "section": "expenses",
                "category": "operating",
                "fund_type": "operating",
                "account_code": "6100",
                "amount": 1200,
            }
        ],
        connection=conn,
    )

    row = conn.execute(
        """
        SELECT pool_key, mapping_source, match_method, source_rule_id,
               budget_line_amount
          FROM budget_line_pool_mappings
        """
    ).fetchone()
    assert result["auto_approved"] == 1
    assert row[0] == "total_budget_prorated"
    assert row[1] == "account_code"
    assert row[2] == "account_code"
    assert row[3] is not None
    assert row[4] == 1200


def test_materializes_exact_normalized_label_only_when_rule_is_approved(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_label,
             normalized_label, match_type, rule_source, approval_status,
             review_state)
        VALUES
            (?, ?, 'total_budget_prorated', 'Insurance', 'insurance',
             'normalized_label', 'operator', 'approved', 'ready')
        """,
        (property_id, setup_id),
    )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Insurance",
                "section": "expenses",
                "category": "operating",
                "fund_type": "operating",
                "amount": 1200,
            }
        ],
        connection=conn,
    )

    assert result["auto_approved"] == 1
    assert conn.execute("SELECT COUNT(*) FROM budget_line_pool_mappings").fetchone()[0] == 1


def test_manual_mapping_overrides_materialized_candidate(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO budget_line_pool_mappings
            (property_id, assessment_setup_id, budget_line_normalized_label,
             section, category, fund_type, pool_key, mapping_source)
        VALUES
            (?, ?, 'insurance', 'expenses', 'operating', 'operating',
             'manual_pool', 'operator')
        """,
        (property_id, setup_id),
    )
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, normalized_label,
             match_type, rule_source, approval_status, review_state)
        VALUES
            (?, ?, 'total_budget_prorated', 'insurance',
             'normalized_label', 'operator', 'approved', 'ready')
        """,
        (property_id, setup_id),
    )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Insurance",
                "section": "expenses",
                "category": "operating",
                "fund_type": "operating",
                "amount": 1200,
            }
        ],
        connection=conn,
    )

    row = conn.execute("SELECT pool_key FROM budget_line_pool_mappings").fetchone()
    assert result["manual_preserved"] == 1
    assert row[0] == "manual_pool"


def test_ambiguous_materialization_requires_review(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)
    for pool_key in ("pool_a", "pool_b"):
        conn.execute(
            """
            INSERT INTO assessment_budget_mapping_rules
                (property_id, assessment_setup_id, pool_key, normalized_label,
                 match_type, rule_source, approval_status, review_state)
            VALUES
                (?, ?, ?, 'water', 'normalized_label',
                 'operator', 'approved', 'ready')
            """,
            (property_id, setup_id, pool_key),
        )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Water",
                "section": "expenses",
                "category": "operating",
                "fund_type": "operating",
                "amount": 500,
            }
        ],
        connection=conn,
    )

    assert result["conflict"] == 1
    assert conn.execute("SELECT COUNT(*) FROM budget_line_pool_mappings").fetchone()[0] == 0


def test_unmatched_budget_line_remains_unmapped(conn: sqlite3.Connection) -> None:
    property_id, setup_id = _setup(conn)

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Management",
                "section": "expenses",
                "category": "operating",
                "fund_type": "operating",
                "amount": 500,
            }
        ],
        connection=conn,
    )

    assert result["unmatched"] == 1
    assert conn.execute("SELECT COUNT(*) FROM budget_line_pool_mappings").fetchone()[0] == 0


def test_residual_materialization_claims_eligible_unclaimed_lines_after_explicit(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, normalized_label,
             match_type, rule_source, approval_status, review_state)
        VALUES
            (?, ?, 'total_budget_prorated', 'insurance',
             'normalized_label', 'operator', 'approved', 'ready')
        """,
        (property_id, setup_id),
    )
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_type,
             rule_source, approval_status, review_state,
             budget_line_derivation)
        VALUES
            (?, ?, 'total_budget_equal', 'remainder', 'system_remainder',
             'approved', 'ready', 'residual_default')
        """,
        (property_id, setup_id),
    )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Insurance",
                "section": "expenses",
                "category": "operating",
                "fund_type": "operating",
                "amount": 1200,
            },
            {
                "label": "Management",
                "section": "expenses",
                "category": "operating",
                "fund_type": "operating",
                "amount": 500,
            },
        ],
        connection=conn,
    )

    rows = conn.execute(
        """
        SELECT budget_line_normalized_label, pool_key, mapping_source
          FROM budget_line_pool_mappings
         ORDER BY budget_line_normalized_label
        """
    ).fetchall()
    assert result["auto_approved"] == 2
    assert rows == [
        ("insurance", "total_budget_prorated", "normalized_label"),
        ("management", "total_budget_equal", "residual_default"),
    ]


def test_dre_included_label_match_is_suggested_until_rule_approved(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_label,
             normalized_label, match_type, rule_source, approval_status,
             review_state)
        VALUES
            (?, ?, 'total_budget_prorated', 'Insurance', 'insurance',
             'exact_label', 'dre_included_budget_line',
             'suggested', 'pending_review')
        """,
        (property_id, setup_id),
    )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Insurance",
                "section": "expenses",
                "category": "operating",
                "fund_type": "operating",
                "amount": 1200,
            }
        ],
        connection=conn,
    )

    assert result["suggested"] == 1
    assert conn.execute("SELECT COUNT(*) FROM budget_line_pool_mappings").fetchone()[0] == 0


def test_negative_credit_amount_preserves_sign_when_materialized(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.execute(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, account_code,
             match_type, rule_source, approval_status, review_state)
        VALUES
            (?, ?, 'exempted_costs', '7999', 'account_code',
             'operator', 'approved', 'ready')
        """,
        (property_id, setup_id),
    )

    materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "DRE Exemption",
                "normalized_label": "dre exemption",
                "section": "expenses",
                "category": "operating",
                "fund_type": "operating",
                "account_code": "7999",
                "amount": -4800,
            }
        ],
        connection=conn,
    )

    amount = conn.execute(
        "SELECT budget_line_amount FROM budget_line_pool_mappings"
    ).fetchone()[0]
    assert amount == -4800


def test_structured_mapping_evidence_persists_richer_rule_metadata(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)

    count = derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=_mapping_evidence_extraction(
            source_label="Insurance",
            parent_category="Insurance",
            assessment_type="prorated_variable",
            review_required=False,
            account_code="93008",
        ),
        connection=conn,
    )

    row = conn.execute(
        """
        SELECT match_label, normalized_label, account_code, rule_source,
               source_parent_category, assessment_type, review_required,
               review_reason, source_evidence_text
          FROM assessment_budget_mapping_rules
         WHERE rule_source = 'dre_mapping_evidence'
        """
    ).fetchone()

    assert count == 3
    assert row == (
        "Insurance",
        "insurance",
        "93008",
        "dre_mapping_evidence",
        "Insurance",
        "prorated_variable",
        0,
        "",
        "Insurance belongs to total_budget_prorated.",
    )


def test_review_rows_use_selected_final_proposed_amount_basis_and_source(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)

    rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Insurance",
                "section": "operating",
                "category": "operating",
                "fund_type": "operating",
                "annual_budget": 1000,
                "proposed_amount": 1250,
            },
            {
                "label": "Pool Service",
                "section": "operating",
                "category": "operating",
                "fund_type": "operating",
                "annual_budget": 900,
            },
        ],
        connection=conn,
    )

    assert [row["line_label"] for row in rows] == ["Insurance", "Pool Service"]
    assert rows[0]["assessment_mapping_amount"] == 1250.0
    assert rows[0]["source_column_used"] == "proposed_amount"
    assert rows[0]["amount"] == 1250.0
    assert rows[1]["assessment_mapping_amount"] == 900.0
    assert rows[1]["source_column_used"] == "annual_budget"


def test_review_rows_assign_stable_identity_and_filter_non_review_rows(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)

    base_lines = [
        {
            "label": "Insurance",
            "section": "operating",
            "category": "operating",
            "fund_type": "operating",
            "annual_budget": 1000,
            "proposedAmount": 1250,
        },
        {
            "label": "Reserve - Allocation/Transfer",
            "section": "operating",
            "category": "operating",
            "fund_type": "operating",
            "annual_budget": 600,
        },
        {
            "label": "Roof",
            "section": "reserve",
            "category": "reserve_expense",
            "fund_type": "reserve",
            "annual_budget": 500,
        },
        {
            "label": "Assessment Revenue",
            "section": "income",
            "category": "income",
            "fund_type": "operating",
            "annual_budget": 2350,
        },
        {
            "label": "Total Operating Expenses",
            "section": "operating",
            "category": "operating",
            "fund_type": "operating",
            "annual_budget": 2350,
        },
    ]

    rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=base_lines,
        connection=conn,
    )
    refreshed_rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=list(reversed(base_lines)),
        connection=conn,
    )

    by_label = {row["line_label"]: row for row in rows}
    refreshed_by_label = {row["line_label"]: row for row in refreshed_rows}

    assert set(by_label) == {
        "Insurance",
        "Reserve - Allocation/Transfer",
        "Roof",
    }
    assert by_label["Insurance"]["row_role"] == "current_year_operating_budget_line"
    assert by_label["Insurance"]["included_in_regular_basis"] is True
    assert by_label["Reserve - Allocation/Transfer"]["row_role"] == "current_year_reserve_contribution_line"
    assert by_label["Reserve - Allocation/Transfer"]["included_in_regular_basis"] is True
    assert by_label["Roof"]["row_role"] == "reserve_component_detail"
    assert by_label["Roof"]["included_in_regular_basis"] is False
    assert by_label["Insurance"]["line_key"] == refreshed_by_label["Insurance"]["line_key"]
    assert by_label["Reserve - Allocation/Transfer"]["line_key"] == refreshed_by_label["Reserve - Allocation/Transfer"]["line_key"]
    assert by_label["Roof"]["line_key"] == refreshed_by_label["Roof"]["line_key"]


def test_line_review_items_rank_safe_suggestion_for_renamed_operating_line(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=_mapping_evidence_extraction(),
        connection=conn,
    )

    items = build_line_review_items(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "General Insurance",
                "section": "operating",
                "category": "operating",
                "fund_type": "operating",
                "amount": 15000,
            }
        ],
        connection=conn,
    )

    assert len(items) == 1
    assert items[0]["status"] == "suggested"
    assert items[0]["line_label"] == "General Insurance"
    assert items[0]["candidates"][0]["decision_level"] == "safe_suggestion"
    assert items[0]["candidates"][0]["pool_key"] == "total_budget_prorated"


def test_line_review_items_mark_water_mapping_as_review_required(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=_mapping_evidence_extraction(
            source_label="Water, Domestic",
            parent_category="Utility",
            review_required=True,
            review_reason="Current-year lines may combine sewer or pass-through billing.",
        ),
        connection=conn,
    )

    items = build_line_review_items(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Water & Sewer",
                "section": "operating",
                "category": "operating",
                "fund_type": "operating",
                "amount": 9000,
            }
        ],
        connection=conn,
    )

    assert items[0]["candidates"][0]["decision_level"] == "review_required_suggestion"
    assert items[0]["candidates"][0]["review_reason"] == (
        "Current-year lines may combine sewer or pass-through billing."
    )


def test_line_review_items_do_not_surface_exemption_credit_as_regular_mapping(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    derive_rules_from_dre_extraction(
        property_id=property_id,
        assessment_setup_id=setup_id,
        source_dre_extraction_run_id=11,
        extraction=_mapping_evidence_extraction(
            source_label="Landscape Exemption",
            parent_category="Landscaping",
            assessment_type="exemption_credit",
            review_required=True,
            review_reason="2792.16(c) applicability must be confirmed annually.",
        ),
        connection=conn,
    )

    items = build_line_review_items(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Landscape Maintenance",
                "section": "operating",
                "category": "operating",
                "fund_type": "operating",
                "amount": 3000,
            }
        ],
        connection=conn,
    )

    assert items[0]["status"] == "unresolved"
    assert items[0]["candidates"] == []


def test_materialize_filters_non_regular_review_rows_from_regular_mapping(
    conn: sqlite3.Connection,
) -> None:
    property_id, setup_id = _setup(conn)
    conn.executemany(
        """
        INSERT INTO assessment_budget_mapping_rules
            (property_id, assessment_setup_id, pool_key, match_label,
             normalized_label, match_type, rule_source, approval_status,
             review_state)
        VALUES
            (?, ?, 'total_budget_prorated', ?, ?, 'normalized_label',
             'operator', 'approved', 'ready')
        """,
        [
            (property_id, setup_id, "Insurance", "insurance"),
            (property_id, setup_id, "Roof", "roof"),
            (
                property_id,
                setup_id,
                "Reserve - Allocation/Transfer",
                "reserve allocation transfer",
            ),
        ],
    )

    result = materialize_budget_line_pool_mappings(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=[
            {
                "label": "Insurance",
                "section": "operating",
                "category": "operating",
                "fund_type": "operating",
                "annual_budget": 1000,
                "proposed_amount": 1250,
            },
            {
                "label": "Roof",
                "section": "reserve",
                "category": "reserve_expense",
                "fund_type": "reserve",
                "annual_budget": 500,
            },
            {
                "label": "Reserve - Allocation/Transfer",
                "section": "operating",
                "category": "operating",
                "fund_type": "operating",
                "annual_budget": 600,
            },
        ],
        connection=conn,
    )

    rows = conn.execute(
        """
        SELECT budget_line_normalized_label, budget_line_amount
          FROM budget_line_pool_mappings
         ORDER BY budget_line_normalized_label
        """
    ).fetchall()

    # Insurance + reserve contribution are schedule-basis; roof component is not.
    assert result["auto_approved"] == 2
    assert result["non_blocking"] == 1
    assert rows == [
        ("insurance", 1250.0),
        ("reserve allocation transfer", 600.0),
    ]
