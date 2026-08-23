"""Generalized CCR context-coverage and promotion regression tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.dre_extraction.promotion import (
    derive_ccr_pool_treatments,
    parse_extraction_payload,
    populate_setup_children,
)
from app.governing_doc_extraction.coherence import (
    IncoherentCcrExtraction,
    assess_allocation_coherence,
    assert_ccr_allocation_coherent,
)
from app.governing_doc_extraction.wire_to_domain import to_domain
from tests.support.ccr_rule_coverage_fixture import (
    GENERIC_CCR_CONTEXTS,
    generic_ccr_wire_extraction,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


def _resolved_extraction():
    extraction = to_domain(generic_ccr_wire_extraction())
    cost_center = next(
        pool
        for pool in extraction.allocation_pools
        if pool.allocation_context == "cost_center"
    )
    resolved_cost_center = cost_center.model_copy(
        update={
            "allocation_method": "equal",
            "recipient_scope": "custom_unit_list",
            "selected_unit_numbers": ["A"],
            "participant_unit_numbers": ["A"],
        }
    )
    return extraction.model_copy(
        update={
            "allocation_pools": [
                resolved_cost_center
                if pool.pool_key == cost_center.pool_key
                else pool
                for pool in extraction.allocation_pools
            ]
        }
    )


def test_generic_fixture_declares_and_covers_each_context() -> None:
    extraction = to_domain(generic_ccr_wire_extraction())

    assert extraction.assessment_setup.declared_contexts == GENERIC_CCR_CONTEXTS
    assert {
        pool.allocation_context for pool in extraction.allocation_pools
    } == set(GENERIC_CCR_CONTEXTS)
    assert len(extraction.unit_structure.units) == 3
    pools = {pool.pool_key: pool for pool in extraction.allocation_pools}
    assert pools["equal_base"].allocation_method == "equal"
    assert pools["external_schedule_exception"].allocation_method == "custom_factor"
    assert pools["reserve_contribution"].allocation_method == "custom_factor"
    assert pools["reserve_contribution"].billing_cadence == "recurring"
    assert pools["parking_cost_center"].allocation_context == "cost_center"
    assert pools["parking_cost_center"].billing_cadence == "recurring"
    assert pools["structural_repair"].billing_cadence == "one_time"
    assert pools["equal_base"].residual_after_pool_keys == [
        "external_schedule_exception",
        "reserve_contribution",
        "parking_cost_center",
    ]

    finding = assess_allocation_coherence(extraction)

    assert finding.is_incoherent
    assert any("cost-center" in reason for reason in finding.reasons)
    with pytest.raises(IncoherentCcrExtraction):
        assert_ccr_allocation_coherent(extraction)


def test_generic_fixture_promotes_after_cost_center_review() -> None:
    extraction = _resolved_extraction()

    assert_ccr_allocation_coherent(extraction)
    round_tripped = parse_extraction_payload(
        json.dumps(extraction.model_dump(mode="json"))
    )
    assert round_tripped is not None
    round_tripped = derive_ccr_pool_treatments(round_tripped)

    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_PATH.read_text())
    connection.execute("INSERT INTO properties (name, units) VALUES ('Generic', 3)")
    property_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        "INSERT INTO assessment_setups "
        "(property_id, setup_type, display_mode, status) "
        "VALUES (?, 'per_unit', 'per_unit', 'draft')",
        (property_id,),
    )
    setup_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]

    counts = populate_setup_children(
        setup_id=setup_id,
        setup_type="per_unit",
        extraction=round_tripped,
        connection=connection,
    )

    assert counts["units"] == 3
    structural = connection.execute(
        "SELECT pool_kind FROM allocation_pools "
        "WHERE assessment_setup_id = ? AND pool_key = 'structural_repair'",
        (setup_id,),
    ).fetchone()
    assert structural == ("separately_billed_special_assessment",)
    connection.close()
