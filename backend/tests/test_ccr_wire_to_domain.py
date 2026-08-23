"""CCR wire-schema to domain preservation tests."""

from __future__ import annotations

from app.governing_doc_extraction.wire_schemas import (
    WireCCRAllocationPool,
    WireCCRAssessmentSetupBlock,
    WireCCRDocumentMetadata,
    WireCCRPageInventoryEntry,
    WireCCRPolicyExtraction,
    WireCCRUnitStructure,
)
from app.governing_doc_extraction.wire_to_domain import to_domain


def test_independent_wire_billing_fields_are_required() -> None:
    fields = WireCCRAllocationPool.model_fields

    assert fields["billing_cadence"].is_required()
    assert fields["amount_availability"].is_required()


def _wire_extraction(*pools: WireCCRAllocationPool) -> WireCCRPolicyExtraction:
    return WireCCRPolicyExtraction(
        document_metadata=WireCCRDocumentMetadata(
            association_name="Generic Association",
            document_title="Declaration",
            recording_reference=None,
            document_date=None,
            total_units=2,
            confidence=0.95,
            source_pages=[1],
        ),
        page_inventory=[
            WireCCRPageInventoryEntry(
                page_number=1,
                page_type="assessment/allocation provisions",
                confidence=0.95,
                notes="allocation rule",
            )
        ],
        assessment_setup=WireCCRAssessmentSetupBlock(
            setup_type="multi_pool_combination",
            default_basis="equal",
            summary="Multiple allocation contexts apply.",
            requires_dre_for_future_years=True,
            confidence=0.95,
            source_pages=[1],
            declared_contexts=[
                "regular_operating",
                "special_assessment",
                "cost_center",
            ],
        ),
        unit_structure=WireCCRUnitStructure(
            unit_count=2,
            per_unit_factors_available=False,
            factor_exhibit_reference=None,
            units=[],
        ),
        allocation_pools=list(pools),
        reserve_policy=None,
        validation_checks=[],
        human_review_questions=[],
    )


def _pool(
    *,
    key: str,
    context: str,
    billing: str,
    cadence: str = "recurring",
    amount_availability: str = "known",
    basis: str = "custom_factor",
    scope: str = "all_units",
) -> WireCCRAllocationPool:
    return WireCCRAllocationPool(
        pool_key=key,
        pool_name=key,
        allocation_basis=basis,
        allocation_context=context,
        billing_treatment=billing,
        billing_cadence=cadence,
        amount_availability=amount_availability,
        recipient_scope=scope,
        denominator_label="external schedule",
        denominator_source="exhibit_reference",
        expense_categories=["documented category"],
        is_residual_base=False,
        residual_after_pool_keys=[],
        source_pages=[1],
        confidence=0.95,
    )


def test_to_domain_preserves_context_and_derives_special_assessment_kind() -> None:
    extraction = to_domain(
        _wire_extraction(
            _pool(
                key="structural_repair",
                context="special_assessment",
                billing="separate_one_time",
                cadence="one_time",
                basis="square_footage",
            )
        )
    )

    assert extraction.assessment_setup.declared_contexts == [
        "regular_operating",
        "special_assessment",
        "cost_center",
    ]
    pool = extraction.allocation_pools[0]
    assert pool.allocation_context == "special_assessment"
    assert pool.billing_treatment == "separate_one_time"
    assert pool.pool_kind == "separately_billed_special_assessment"


def test_to_domain_preserves_pending_cost_center_without_expanding_scope() -> None:
    extraction = to_domain(
        _wire_extraction(
            _pool(
                key="parking_cost_center",
                context="cost_center",
                billing="operator_amount_pending",
                cadence="recurring",
                amount_availability="operator_pending",
                basis="specified_value",
                scope="units_with_appurtenant_parking",
            )
        )
    )

    pool = extraction.allocation_pools[0]
    assert pool.allocation_context == "cost_center"
    assert pool.billing_treatment == "operator_amount_pending"
    assert pool.billing_cadence == "recurring"
    assert pool.amount_availability == "operator_pending"
    assert pool.recipient_scope == "units_with_appurtenant_parking"
    assert pool.pool_kind == ""


def test_to_domain_separates_one_time_cadence_from_external_amount() -> None:
    extraction = to_domain(
        _wire_extraction(
            _pool(
                key="structural_repair",
                context="special_assessment",
                billing="separate_one_time",
                cadence="one_time",
                amount_availability="external_schedule",
                basis="square_footage",
            )
        )
    )

    pool = extraction.allocation_pools[0]
    assert pool.billing_cadence == "one_time"
    assert pool.amount_availability == "external_schedule"
    assert pool.pool_kind == "separately_billed_special_assessment"


def test_to_domain_ignores_contradictory_legacy_billing_treatment() -> None:
    extraction = to_domain(
        _wire_extraction(
            _pool(
                key="structural_repair",
                context="special_assessment",
                billing="recurring",
                cadence="one_time",
                amount_availability="operator_pending",
                basis="square_footage",
            )
        )
    )

    pool = extraction.allocation_pools[0]
    assert pool.billing_treatment == "operator_amount_pending"
    assert pool.billing_cadence == "one_time"
    assert pool.amount_availability == "operator_pending"
