"""CCR schema-specific parsing and pipeline conversion tests."""

from __future__ import annotations

import json

import pytest

from app.dre_extraction.page_classification import PageBatch
from app.dre_extraction.promotion import derive_ccr_pool_treatments
from app.governing_doc_extraction.pipeline import run_ccr_extraction
from app.dre_extraction.validation import ExtractionParseError, parse_extraction_response
from app.governing_doc_extraction.wire_schemas import (
    WireCCRAllocationPool,
    WireCCRAssessmentSetupBlock,
    WireCCRDocumentMetadata,
    WireCCRPageInventoryEntry,
    WireCCRPolicyExtraction,
    WireCCRUnitStructure,
)
from app.governing_doc_extraction.wire_to_domain import to_domain


def _wire_extraction() -> WireCCRPolicyExtraction:
    return WireCCRPolicyExtraction(
        document_metadata=WireCCRDocumentMetadata(
            association_name="Generic Association",
            document_title="Declaration",
            recording_reference=None,
            document_date=None,
            total_units=1,
            confidence=0.95,
            source_pages=[1],
        ),
        page_inventory=[
            WireCCRPageInventoryEntry(
                page_number=1,
                page_type="assessment/allocation provisions",
                confidence=0.95,
                notes="allocation",
            )
        ],
        assessment_setup=WireCCRAssessmentSetupBlock(
            setup_type="multi_pool_combination",
            default_basis="equal",
            summary="Regular and special assessment rules apply.",
            requires_dre_for_future_years=True,
            confidence=0.95,
            source_pages=[1],
            declared_contexts=["regular_operating", "special_assessment"],
        ),
        unit_structure=WireCCRUnitStructure(
            unit_count=1,
            per_unit_factors_available=False,
            factor_exhibit_reference=None,
            units=[],
        ),
        allocation_pools=[
            WireCCRAllocationPool(
                pool_key="equal_base",
                pool_name="Equal Base",
                allocation_basis="equal",
                allocation_context="regular_operating",
                billing_treatment="recurring",
                recipient_scope="all_units",
                denominator_label="all units",
                denominator_source="calculated",
                expense_categories=[],
                is_residual_base=True,
                residual_after_pool_keys=["structural_repair"],
                source_pages=[1],
                confidence=0.95,
            ),
            WireCCRAllocationPool(
                pool_key="structural_repair",
                pool_name="Structural Repair",
                allocation_basis="square_footage",
                allocation_context="special_assessment",
                billing_treatment="separate_one_time",
                recipient_scope="all_units",
                denominator_label="total square footage",
                denominator_source="exhibit_reference",
                expense_categories=["structural repair"],
                is_residual_base=False,
                residual_after_pool_keys=[],
                source_pages=[1],
                confidence=0.95,
            )
        ],
        reserve_policy=None,
        validation_checks=[],
        human_review_questions=[],
    )


def test_ccr_fallback_validates_against_ccr_wire_schema() -> None:
    payload = _wire_extraction().model_dump(mode="json")
    del payload["allocation_pools"][0]["allocation_context"]

    with pytest.raises(ExtractionParseError):
        parse_extraction_response(
            json.dumps(payload),
            wire_schema=WireCCRPolicyExtraction,
            wire_to_domain_fn=to_domain,
        )


def test_ccr_repair_response_is_revalidated_with_ccr_wire_schema() -> None:
    invalid_payload = _wire_extraction().model_dump(mode="json")
    del invalid_payload["allocation_pools"][0]["billing_treatment"]
    repaired_payload = json.dumps(_wire_extraction().model_dump(mode="json"))

    result = parse_extraction_response(
        json.dumps(invalid_payload),
        repair_callback=lambda _raw, _errors: repaired_payload,
        wire_schema=WireCCRPolicyExtraction,
        wire_to_domain_fn=to_domain,
    )

    assert result.repair_attempts == 1
    assert result.extraction is not None
    assert result.extraction.allocation_pools[1].billing_treatment == "separate_one_time"


def test_ccr_pipeline_converts_wire_once_and_preserves_special_treatment() -> None:
    wire = _wire_extraction()

    def classify(batch: PageBatch):
        return list(wire.page_inventory)

    def extract(_pages: list[int]):
        return json.dumps(wire.model_dump(mode="json")), wire, {}

    record = run_ccr_extraction(
        page_count=1,
        classify_pages_callback=classify,
        extract_policy_callback=extract,
        model_name="test-model",
    )

    assert record.status == "succeeded"
    assert record.extraction is not None
    pool = next(
        pool
        for pool in record.extraction.allocation_pools
        if pool.pool_key == "structural_repair"
    )
    assert pool.allocation_context == "special_assessment"
    assert pool.billing_treatment == "separate_one_time"
    assert pool.pool_kind == "separately_billed_special_assessment"


def test_promotion_rederives_special_treatment_after_review_edits() -> None:
    extraction = to_domain(_wire_extraction())
    edited_pool = extraction.allocation_pools[1].model_copy(
        update={"pool_kind": ""}
    )
    edited = extraction.model_copy(
        update={"allocation_pools": [extraction.allocation_pools[0], edited_pool]}
    )

    promoted = derive_ccr_pool_treatments(edited)

    assert (
        promoted.allocation_pools[1].pool_kind
        == "separately_billed_special_assessment"
    )


def test_promotion_clears_stale_special_treatment_after_review_edit() -> None:
    extraction = to_domain(_wire_extraction())
    edited_pool = extraction.allocation_pools[1].model_copy(
        update={
            "allocation_context": "regular_operating",
            "billing_treatment": "recurring",
        }
    )
    edited = extraction.model_copy(
        update={"allocation_pools": [extraction.allocation_pools[0], edited_pool]}
    )

    promoted = derive_ccr_pool_treatments(edited)

    assert promoted.allocation_pools[1].pool_kind == ""
