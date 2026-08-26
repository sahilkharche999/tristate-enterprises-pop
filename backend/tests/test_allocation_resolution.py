"""Governing-document allocation resolution — contracts, promotion, slices, readiness."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.allocation_resolution.classifier import classify_pool, collect_migration_report
from app.allocation_resolution.preview import build_preview_overlay
from app.allocation_resolution.readiness import evaluate_readiness, readiness_blocks_final
from app.allocation_resolution.semantic_mapping import classify_label_match
from app.allocation_resolution.service import (
    approve_resolution,
    approve_slices_for_line,
    list_current_resolutions,
    upsert_category_decision,
    upsert_slices_for_line,
    validate_slice_sum,
)
from app.allocation_resolution.schemas import (
    CategoryCoverageDecision,
    FactorSnapshot,
    ResolutionEvidence,
)
from app.dre_extraction.adapter import map_allocation_method
from app.dre_extraction.promotion import (
    parse_extraction_payload,
    populate_setup_children,
    repair_custom_factor_ownership_resolutions,
)
from tests.support.missouri_allocation_fixture import (
    MISSOURI_LEVY_EQUAL_ANNUAL,
    MISSOURI_LEVY_HOA_ANNUAL,
    MISSOURI_LEVY_MONTHLY_ASSESSMENTS,
    MISSOURI_LEVY_VARIABLE_ANNUAL,
    MISSOURI_TOTAL_SQFT,
    MISSOURI_UNITS,
    missouri_extraction_payload,
    missouri_sqft_monthly_for_unit,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()


def _seed_setup(db: sqlite3.Connection) -> tuple[int, int]:
    db.execute("INSERT INTO properties (name, units) VALUES ('131 Missouri', 9)")
    pid = db.execute("SELECT id FROM properties").fetchone()[0]
    db.execute(
        "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
        "VALUES (?, 'per_unit', 'per_unit', 'draft')",
        (pid,),
    )
    setup_id = db.execute("SELECT id FROM assessment_setups").fetchone()[0]
    db.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = ?",
        (setup_id, pid),
    )
    return int(pid), int(setup_id)


def test_missouri_fixture_has_external_rule_and_levy_totals() -> None:
    payload = missouri_extraction_payload()
    exception = next(
        p for p in payload["allocation_pools"] if p["pool_key"] == "variable_dre_exceptions"
    )
    assert exception["allocation_method"] == "custom_factor"
    assert "DRE" in exception["denominator_label"]
    assert len(payload["unit_structure"]["units"]) == 9
    assert payload["assessment_setup"]["declared_contexts"] == [
        "regular_operating",
        "cost_center",
        "special_assessment",
    ]
    assert any(line["label"] == "Electricity & Gas" for line in [
        {"label": "Electricity & Gas"}
    ])
    assert MISSOURI_LEVY_VARIABLE_ANNUAL + MISSOURI_LEVY_EQUAL_ANNUAL == MISSOURI_LEVY_HOA_ANNUAL
    assert MISSOURI_LEVY_MONTHLY_ASSESSMENTS["201"] == Decimal("1057.20")


def test_characterization_sqft_collapse_changes_recipients_not_hoa_total() -> None:
    """Document the bug: silent sqft allocation preserves HOA total but moves money."""
    levy_201 = MISSOURI_LEVY_MONTHLY_ASSESSMENTS["201"]
    sqft_201 = missouri_sqft_monthly_for_unit("201")
    assert sqft_201 != levy_201
    assert abs(sqft_201 - levy_201) >= Decimal("0.50")
    # HOA-wide dollars are unchanged either way.
    assert MISSOURI_LEVY_HOA_ANNUAL == Decimal("104458")
    assert MISSOURI_TOTAL_SQFT == Decimal("15136")


def test_adapter_custom_factor_does_not_become_square_footage() -> None:
    mapping = map_allocation_method("custom_factor")
    assert mapping.internal_method is None
    assert mapping.promote_as_unresolved is True


def test_missouri_promotion_resolves_custom_factor_to_ownership_percentage(
    db: sqlite3.Connection,
) -> None:
    pid, setup_id = _seed_setup(db)
    ext = parse_extraction_payload(json.dumps(missouri_extraction_payload()))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    row = db.execute(
        "SELECT declared_allocation_method, allocation_method "
        "FROM allocation_pools WHERE assessment_setup_id = ? AND pool_key = 'variable_dre_exceptions'",
        (setup_id,),
    ).fetchone()
    assert row[0] == "custom_factor"
    assert row[1] == "ownership_percentage"
    recs = list_current_resolutions(db, assessment_setup_id=setup_id)
    exception = next(r for r in recs if r.pool_key == "variable_dre_exceptions")
    assert exception.status == "approved"
    assert exception.declared_method == "custom_factor"
    assert exception.resolved_method == "ownership_percentage"
    assert set(exception.factor_snapshot.recipients) == {
        unit["unit_number"] for unit in MISSOURI_UNITS
    }
    parking = db.execute(
        "SELECT allocation_method, recipient_scope FROM allocation_pools "
        "WHERE pool_key = 'parking_cost_center' AND assessment_setup_id = ?",
        (setup_id,),
    ).fetchone()
    structural = db.execute(
        "SELECT allocation_method, pool_kind FROM allocation_pools "
        "WHERE pool_key = 'structural_repair_sa' AND assessment_setup_id = ?",
        (setup_id,),
    ).fetchone()
    assert parking[0] == "square_footage"
    assert parking[1] == "custom_unit_list"
    assert structural[0] == "square_footage"
    assert structural[1] == "separately_billed_special_assessment"
    assert db.execute(
        "SELECT COUNT(*) FROM assessment_units WHERE assessment_setup_id = ?",
        (setup_id,),
    ).fetchone()[0] == 9
    del pid


def test_custom_factor_with_percent_and_sqft_uses_ownership_not_sqft(
    db: sqlite3.Connection,
) -> None:
    pid, setup_id = _seed_setup(db)
    payload = missouri_extraction_payload()
    ext = parse_extraction_payload(json.dumps(payload))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    method, denom = db.execute(
        "SELECT allocation_method, denominator_value FROM allocation_pools "
        "WHERE pool_key = 'variable_dre_exceptions'",
    ).fetchone()
    assert method == "ownership_percentage"
    assert Decimal(str(denom)) != MISSOURI_TOTAL_SQFT
    del pid


def test_custom_factor_without_percent_stays_unresolved_even_with_sqft(
    db: sqlite3.Connection,
) -> None:
    pid, setup_id = _seed_setup(db)
    payload = missouri_extraction_payload()
    for unit in payload["unit_structure"]["units"]:
        unit["ownership_percent"] = None
    ext = parse_extraction_payload(json.dumps(payload))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    row = db.execute(
        "SELECT declared_allocation_method, allocation_method "
        "FROM allocation_pools WHERE pool_key = 'variable_dre_exceptions'",
    ).fetchone()
    assert row[0] == "custom_factor"
    assert row[1] == "unresolved"
    rec = next(
        r for r in list_current_resolutions(db, assessment_setup_id=setup_id)
        if r.pool_key == "variable_dre_exceptions"
    )
    assert rec.status == "unresolved"
    assert rec.resolved_method is None
    structural = db.execute(
        "SELECT allocation_method FROM allocation_pools "
        "WHERE pool_key = 'structural_repair_sa'",
    ).fetchone()[0]
    assert structural == "square_footage"
    del pid


def test_repair_rewrites_legacy_custom_factor_sqft_to_ownership(
    db: sqlite3.Connection,
) -> None:
    pid, setup_id = _seed_setup(db)
    payload = missouri_extraction_payload()
    for unit in payload["unit_structure"]["units"]:
        unit["ownership_percent"] = None
    ext = parse_extraction_payload(json.dumps(payload))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    db.execute(
        "UPDATE allocation_pools SET allocation_method = 'square_footage' "
        "WHERE assessment_setup_id = ? AND pool_key = 'variable_dre_exceptions'",
        (setup_id,),
    )
    db.execute(
        "UPDATE allocation_resolutions SET status = 'approved', "
        "resolved_method = 'square_footage' "
        "WHERE assessment_setup_id = ? AND pool_key = 'variable_dre_exceptions'",
        (setup_id,),
    )
    for unit in MISSOURI_UNITS:
        db.execute(
            "UPDATE assessment_units SET ownership_percent = ? "
            "WHERE assessment_setup_id = ? AND unit_number = ?",
            (unit["ownership_percent"], setup_id, unit["unit_number"]),
        )
    repair_custom_factor_ownership_resolutions(
        setup_id=setup_id,
        extraction=ext,
        connection=db,
    )
    method = db.execute(
        "SELECT allocation_method FROM allocation_pools "
        "WHERE pool_key = 'variable_dre_exceptions'",
    ).fetchone()[0]
    rec = next(
        r for r in list_current_resolutions(db, assessment_setup_id=setup_id)
        if r.pool_key == "variable_dre_exceptions"
    )
    assert method == "ownership_percentage"
    assert rec.resolved_method == "ownership_percentage"
    structural = db.execute(
        "SELECT allocation_method FROM allocation_pools "
        "WHERE pool_key = 'structural_repair_sa'",
    ).fetchone()[0]
    assert structural == "square_footage"
    del pid


def test_explicit_ownership_and_specified_value_promote_approved(db: sqlite3.Connection) -> None:
    pid, setup_id = _seed_setup(db)
    payload = missouri_extraction_payload()
    payload["allocation_pools"] = [
        {
            **payload["allocation_pools"][0],
            "pool_key": "own_pool",
            "allocation_method": "ownership_percentage",
            "budget_line_derivation": "explicit_lines",
        },
        {
            **payload["allocation_pools"][0],
            "pool_key": "spec_pool",
            "allocation_method": "specified_value",
            "budget_line_derivation": "explicit_lines",
        },
    ]
    ext = parse_extraction_payload(json.dumps(payload))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    methods = {
        row[0]: row[1]
        for row in db.execute(
            "SELECT pool_key, allocation_method FROM allocation_pools "
            "WHERE assessment_setup_id = ?",
            (setup_id,),
        )
    }
    assert methods["own_pool"] == "ownership_percentage"
    assert methods["spec_pool"] == "specified_value"
    recs = {r.pool_key: r for r in list_current_resolutions(db, assessment_setup_id=setup_id)}
    assert recs["own_pool"].status == "approved"
    assert recs["spec_pool"].status == "approved"
    del pid


def test_explicit_square_footage_still_fills_denominator(db: sqlite3.Connection) -> None:
    pid, setup_id = _seed_setup(db)
    payload = missouri_extraction_payload()
    for pool in payload["allocation_pools"]:
        if pool["pool_key"] == "variable_dre_exceptions":
            pool["allocation_method"] = "square_footage"
            pool["denominator_label"] = "total square footage"
    ext = parse_extraction_payload(json.dumps(payload))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    row = db.execute(
        "SELECT allocation_method, denominator_value FROM allocation_pools "
        "WHERE pool_key = 'variable_dre_exceptions'",
    ).fetchone()
    assert row[0] == "square_footage"
    assert Decimal(str(row[1])) == MISSOURI_TOTAL_SQFT
    rec = next(
        r for r in list_current_resolutions(db, assessment_setup_id=setup_id)
        if r.pool_key == "variable_dre_exceptions"
    )
    assert rec.status == "approved"
    assert rec.resolved_method == "square_footage"
    del pid


@pytest.mark.parametrize(
    ("category", "line", "kind"),
    [
        ("insurance", "Insurance", "exact"),
        ("gas", "Electricity & Gas", "combined"),
        ("gas and water", "Water", "partial"),
        ("roof reserve", "Management", "unrelated"),
    ],
)
def test_semantic_label_match_kinds(category: str, line: str, kind: str) -> None:
    assert classify_label_match(category, line) == kind


def test_gas_does_not_consume_electricity_and_gas() -> None:
    from app.allocation_resolution.semantic_mapping import is_automatic_full_line_match

    assert is_automatic_full_line_match("gas", "Electricity & Gas") is False
    assert is_automatic_full_line_match("insurance", "Insurance") is True


def test_slice_validation_over_and_under() -> None:
    source = Decimal("16800")
    assert validate_slice_sum(source, [Decimal("5600"), Decimal("11200")]) == Decimal("0")
    assert validate_slice_sum(source, [Decimal("5600")]) == Decimal("11200")
    assert validate_slice_sum(source, [Decimal("5600"), Decimal("12000")]) == Decimal("-800")


def test_slices_and_zero_category_decisions(db: sqlite3.Connection) -> None:
    pid, setup_id = _seed_setup(db)
    created = upsert_slices_for_line(
        db,
        property_id=pid,
        assessment_setup_id=setup_id,
        source_line_normalized_label="electricity gas",
        source_line_account_code=None,
        source_annual_amount=Decimal("16800"),
        slices=[
            {"pool_key": "variable_dre_exceptions", "semantic_category": "gas", "slice_annual_amount": "5600"},
            {"pool_key": "equal_base", "semantic_category": "electricity", "slice_annual_amount": "11200"},
        ],
        actor="tester",
    )
    assert len(created) == 2
    with pytest.raises(ValueError):
        upsert_slices_for_line(
            db,
            property_id=pid,
            assessment_setup_id=setup_id,
            source_line_normalized_label="electricity gas",
            source_line_account_code=None,
            source_annual_amount=Decimal("16800"),
            slices=[{"pool_key": "equal_base", "semantic_category": "gas", "slice_annual_amount": "100"}],
            actor="tester",
        )
    upsert_category_decision(
        db,
        CategoryCoverageDecision(
            property_id=pid,
            assessment_setup_id=setup_id,
            pool_key="variable_dre_exceptions",
            category="parking",
            decision="zero",
            mapped_amount=Decimal("0"),
            reason="No parking cost this year",
            created_by="tester",
        ),
    )


def test_review_rows_expose_combined_line_split_state(db: sqlite3.Connection) -> None:
    pid, setup_id = _seed_setup(db)
    db.executemany(
        """
        INSERT INTO allocation_pools
            (assessment_setup_id, pool_key, pool_name, allocation_method,
             recipient_scope, budget_line_derivation)
        VALUES (?, ?, ?, 'equal', 'all_units', 'explicit_lines')
        """,
        [
            (setup_id, "variable_costs", "Variable Costs"),
            (setup_id, "equal_costs", "Equal Costs"),
        ],
    )
    db.execute(
        """
        INSERT INTO allocation_resolutions (
            property_id, assessment_setup_id, pool_key, version_int, status,
            declared_method, included_categories_json
        ) VALUES (?, ?, 'variable_costs', 1, 'unresolved',
                  'custom_factor', ?)
        """,
        (pid, setup_id, json.dumps(["gas"])),
    )

    from app.services.assessment_budget_mapping_rule_service import (
        build_assessment_mapping_review_rows,
    )

    rows = build_assessment_mapping_review_rows(
        property_id=pid,
        assessment_setup_id=setup_id,
        budget_lines=[{
            "label": "Electricity & Gas",
            "category": "operating",
            "fund_type": "operating",
            "amount": 16800,
        }],
        connection=db,
    )

    row = rows[0]
    # Combined labels stay whole-line assignable. The operator can still
    # open an optional split; the product does not force one.
    assert row["allocation_mode"] == "whole_line"
    assert row["split_status"] == "not_applicable"
    assert row["source_annual_amount"] == 16800.0
    assert row["saved_slices"] == []
    assert "gas" in row["combined_categories"]
    assert {option["pool_key"] for option in row["valid_pool_options"]} == {
        "variable_costs",
        "equal_costs",
    }


def test_slice_service_rejects_invalid_destinations(
    db: sqlite3.Connection,
) -> None:
    pid, setup_id = _seed_setup(db)

    with pytest.raises(ValueError, match="not available"):
        upsert_slices_for_line(
            db,
            property_id=pid,
            assessment_setup_id=setup_id,
            source_line_normalized_label="combined utilities",
            source_line_account_code=None,
            source_annual_amount=Decimal("100"),
            slices=[
                {
                    "pool_key": "missing_pool",
                    "semantic_category": "gas",
                    "slice_annual_amount": "40",
                },
                {
                    "pool_key": "other_pool",
                    "semantic_category": "electricity",
                    "slice_annual_amount": "60",
                },
            ],
            actor="tester",
            valid_pool_keys={"other_pool"},
        )


def test_slice_service_allows_same_destination_for_labeled_slices(
    db: sqlite3.Connection,
) -> None:
    """A combined line can send every slice to one assessment category.

    Operators do this when the source description names more than one charge
    (Electricity & Gas) but last-package math uses one allocation method.
    """
    pid, setup_id = _seed_setup(db)
    created = upsert_slices_for_line(
        db,
        property_id=pid,
        assessment_setup_id=setup_id,
        source_line_normalized_label="electricity gas",
        source_line_account_code=None,
        source_annual_amount=Decimal("16800"),
        slices=[
            {
                "pool_key": "equal_base",
                "semantic_category": "gas",
                "slice_annual_amount": "8400",
            },
            {
                "pool_key": "equal_base",
                "semantic_category": "electricity",
                "slice_annual_amount": "8400",
            },
        ],
        actor="tester",
        valid_pool_keys={"equal_base"},
    )
    assert [item.pool_key for item in created] == ["equal_base", "equal_base"]
    assert [item.slice_annual_amount for item in created] == [
        Decimal("8400"),
        Decimal("8400"),
    ]
    approved = approve_slices_for_line(
        db,
        assessment_setup_id=setup_id,
        source_line_normalized_label="electricity gas",
        actor="tester",
        source_annual_amount=Decimal("16800"),
    )
    assert {item.status for item in approved} == {"approved"}


def test_classifier_explicit_vs_ambiguous_vs_missing_provenance() -> None:
    assert classify_pool(
        declared_method="equal",
        promoted_method="equal",
        has_resolution_evidence=False,
        finalized_snapshot=False,
    ) == "approved_backfill"
    assert classify_pool(
        declared_method="custom_factor",
        promoted_method="square_footage",
        has_resolution_evidence=False,
        finalized_snapshot=False,
    ) == "needs_review"
    assert classify_pool(
        declared_method=None,
        promoted_method="square_footage",
        has_resolution_evidence=False,
        finalized_snapshot=False,
    ) == "needs_review"
    assert classify_pool(
        declared_method="custom_factor",
        promoted_method="square_footage",
        has_resolution_evidence=False,
        finalized_snapshot=True,
    ) == "leave_finalized"


def test_migration_report_does_not_rewrite_finalized(db: sqlite3.Connection) -> None:
    pid, setup_id = _seed_setup(db)
    ext = parse_extraction_payload(json.dumps(missouri_extraction_payload()))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    report = collect_migration_report(db, property_id=pid)
    assert any(
        row["pool_key"] == "variable_dre_exceptions"
        and row["classification"] in {"needs_review", "approved_backfill"}
        for row in report
    )


def test_readiness_blocks_unresolved_custom_factor(db: sqlite3.Connection) -> None:
    pid, setup_id = _seed_setup(db)
    payload = missouri_extraction_payload()
    for unit in payload["unit_structure"]["units"]:
        unit["ownership_percent"] = None
    ext = parse_extraction_payload(json.dumps(payload))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    report = evaluate_readiness(
        db,
        property_id=pid,
        assessment_setup_id=setup_id,
        budget_lines=[{"label": "Electricity & Gas", "annual_amount": "16800"}],
        residual_pool_keys={"equal_base"},
    )
    codes = {i.code for i in report.issues}
    assert "allocation_resolution_required" in codes
    assert "combined_line_requires_split" not in codes
    assert readiness_blocks_final(report) is True
    assert report.ready_for_final is False


def test_missouri_operator_resolution_matches_levy(db: sqlite3.Connection) -> None:
    pid, setup_id = _seed_setup(db)
    ext = parse_extraction_payload(json.dumps(missouri_extraction_payload()))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    for unit in MISSOURI_UNITS:
        db.execute(
            "INSERT OR IGNORE INTO assessment_units "
            "(assessment_setup_id, unit_number, square_feet, ownership_percent) "
            "VALUES (?, ?, ?, ?)",
            (setup_id, unit["unit_number"], unit["square_feet"], unit["ownership_percent"]),
        )
    upsert_slices_for_line(
        db,
        property_id=pid,
        assessment_setup_id=setup_id,
        source_line_normalized_label="electricity gas",
        source_line_account_code=None,
        source_annual_amount=Decimal("16800"),
        slices=[
            {"pool_key": "variable_dre_exceptions", "semantic_category": "gas", "slice_annual_amount": "5600"},
            {"pool_key": "equal_base", "semantic_category": "electricity", "slice_annual_amount": "11200"},
        ],
        actor="tester",
    )
    for category, amount in {
        "insurance": "8340",
        "water": "9000",
        "roof reserve": "1028",
        "painting reserve": "7181",
        "water heater reserve": "718",
        "gas": "5600",
    }.items():
        upsert_category_decision(
            db,
            CategoryCoverageDecision(
                property_id=pid,
                assessment_setup_id=setup_id,
                pool_key="variable_dre_exceptions",
                category=category,
                decision="mapped",
                mapped_amount=Decimal(amount),
                reason="Levy/DRE schedule",
                created_by="tester",
            ),
        )
    recipients = {
        u["unit_number"]: Decimal(u["ownership_percent"]) for u in MISSOURI_UNITS
    }
    approve_resolution(
        db,
        property_id=pid,
        assessment_setup_id=setup_id,
        pool_key="variable_dre_exceptions",
        resolved_method="ownership_percentage",
        factor_snapshot=FactorSnapshot(
            method="ownership_percentage",
            recipients=recipients,
        ),
        evidence=ResolutionEvidence(reason="Matches approved DRE proration schedule"),
        actor="tester",
    )
    overlay = build_preview_overlay(
        db,
        assessment_setup_id=setup_id,
        units=MISSOURI_UNITS,
        pool_annuals={
            "equal_base": MISSOURI_LEVY_EQUAL_ANNUAL - Decimal("11200"),
            "variable_dre_exceptions": MISSOURI_LEVY_VARIABLE_ANNUAL - Decimal("5600"),
        },
    )
    unit_201 = Decimal(overlay["monthly_by_unit"]["201"])
    assert unit_201 == MISSOURI_LEVY_MONTHLY_ASSESSMENTS["201"]
    method = db.execute(
        "SELECT allocation_method FROM allocation_pools "
        "WHERE assessment_setup_id = ? AND pool_key = 'variable_dre_exceptions'",
        (setup_id,),
    ).fetchone()[0]
    assert method == "ownership_percentage"


def test_preview_does_not_mutate_setup(db: sqlite3.Connection) -> None:
    pid, setup_id = _seed_setup(db)
    ext = parse_extraction_payload(json.dumps(missouri_extraction_payload()))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=db,
    )
    before = db.execute(
        "SELECT allocation_method FROM allocation_pools WHERE pool_key = 'variable_dre_exceptions'"
    ).fetchone()[0]
    build_preview_overlay(
        db,
        assessment_setup_id=setup_id,
        units=MISSOURI_UNITS,
        pool_annuals={},
    )
    after = db.execute(
        "SELECT allocation_method FROM allocation_pools WHERE pool_key = 'variable_dre_exceptions'"
    ).fetchone()[0]
    assert before == after == "ownership_percentage"
    del pid


def test_allocation_resolution_api_preview_and_final_gate(client, db_session):
    from app.ai_implementation.db.models import Property

    hoa = Property(name="Resolution API HOA", units=9, hoa_code="RES")
    db_session.add(hoa)
    db_session.commit()
    db_session.refresh(hoa)
    raw = db_session.connection().connection
    raw.execute(
        "INSERT INTO assessment_setups (property_id, setup_type, display_mode, status) "
        "VALUES (?, 'per_unit', 'per_unit', 'approved')",
        (hoa.id,),
    )
    setup_id = raw.execute("SELECT last_insert_rowid()").fetchone()[0]
    raw.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = ?",
        (setup_id, hoa.id),
    )
    ext = parse_extraction_payload(json.dumps(missouri_extraction_payload()))
    populate_setup_children(
        setup_id=setup_id, setup_type="per_unit", extraction=ext, connection=raw,
    )
    raw.commit()

    listed = client.get(f"/hoa/{hoa.id}/allocation-resolution")
    assert listed.status_code == 200
    body = listed.json()
    assert any(
        r["declared_method"] == "custom_factor"
        and r["resolved_method"] == "ownership_percentage"
        and r["status"] == "approved"
        for r in body["resolutions"]
    )

    preview = client.get(f"/hoa/{hoa.id}/allocation-resolution/preview")
    assert preview.status_code == 200
    assert preview.json()["is_final"] is False

    ready = client.get(f"/hoa/{hoa.id}/allocation-resolution/readiness")
    assert ready.status_code == 200
    assert ready.json()["blocks_final"] is False

    draft = client.post(
        f"/hoa/{hoa.id}/allocation-resolution/pools/variable_dre_exceptions/draft",
        json={
            "resolved_method": "ownership_percentage",
            "confirmation": "I confirm ownership percentage",
            "reason": "Matches the DRE proration schedule",
        },
    )
    assert draft.status_code == 200
    assert draft.json()["status"] == "draft"
    preview_after = client.get(f"/hoa/{hoa.id}/allocation-resolution/preview")
    assert preview_after.status_code == 200
    assert preview_after.json()["preview"]["is_final"] is False
