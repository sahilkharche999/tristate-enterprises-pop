"""HOA-agnostic CCR rule-coverage fixture."""

from __future__ import annotations

from app.governing_doc_extraction.wire_schemas import (
    WireCCRAllocationPool,
    WireCCRAssessmentSetupBlock,
    WireCCRDocumentMetadata,
    WireCCRPageInventoryEntry,
    WireCCRPolicyExtraction,
    WireCCRReservePolicy,
    WireCCRUnitFactor,
    WireCCRUnitStructure,
)


GENERIC_CCR_CONTEXTS = [
    "regular_operating",
    "reserve_contribution",
    "special_assessment",
    "cost_center",
]


def generic_ccr_wire_extraction() -> WireCCRPolicyExtraction:
    """Return a multi-context policy with one intentionally unresolved cost center."""
    return WireCCRPolicyExtraction(
        document_metadata=WireCCRDocumentMetadata(
            association_name="Generic Association",
            document_title="Declaration of Covenants",
            recording_reference=None,
            document_date=None,
            total_units=3,
            confidence=0.95,
            source_pages=[2, 3, 4],
        ),
        page_inventory=[
            WireCCRPageInventoryEntry(
                page_number=2,
                page_type="assessment/allocation provisions",
                confidence=0.95,
                notes="regular allocation article",
            ),
            WireCCRPageInventoryEntry(
                page_number=3,
                page_type="special assessment provisions",
                confidence=0.95,
                notes="repair levy article",
            ),
            WireCCRPageInventoryEntry(
                page_number=4,
                page_type="exhibit/percentage-interest table",
                confidence=0.95,
                notes="unit factors",
            ),
        ],
        assessment_setup=WireCCRAssessmentSetupBlock(
            setup_type="multi_pool_combination",
            default_basis="equal",
            summary="Regular, reserve, special-assessment, and cost-center rules apply.",
            requires_dre_for_future_years=True,
            confidence=0.95,
            source_pages=[2, 3, 4],
            declared_contexts=list(GENERIC_CCR_CONTEXTS),
        ),
        unit_structure=WireCCRUnitStructure(
            unit_count=3,
            per_unit_factors_available=True,
            factor_exhibit_reference="Exhibit A",
            units=[
                WireCCRUnitFactor(
                    unit_number=str(unit),
                    square_feet=square_feet,
                    ownership_percent=ownership_percent,
                    source_page=4,
                    confidence=0.95,
                )
                for unit, square_feet, ownership_percent in (
                    ("A", 1000, 25),
                    ("B", 1500, 37.5),
                    ("C", 1500, 37.5),
                )
            ],
        ),
        allocation_pools=[
            WireCCRAllocationPool(
                pool_key="equal_base",
                pool_name="Regular Equal Base",
                allocation_basis="equal",
                allocation_context="regular_operating",
                billing_treatment="recurring",
                recipient_scope="all_units",
                denominator_label="all units",
                denominator_source="calculated",
                expense_categories=[],
                is_residual_base=True,
                residual_after_pool_keys=[
                    "external_schedule_exception",
                    "reserve_contribution",
                    "structural_repair",
                    "limited_benefit_cost_center",
                ],
                source_pages=[2],
                confidence=0.95,
            ),
            WireCCRAllocationPool(
                pool_key="external_schedule_exception",
                pool_name="External Schedule Exception",
                allocation_basis="custom_factor",
                allocation_context="regular_operating",
                billing_treatment="operator_amount_pending",
                recipient_scope="all_units",
                denominator_label="external operating schedule",
                denominator_source="exhibit_reference",
                expense_categories=["documented exception category"],
                is_residual_base=False,
                residual_after_pool_keys=[],
                source_pages=[2],
                confidence=0.95,
            ),
            WireCCRAllocationPool(
                pool_key="reserve_contribution",
                pool_name="Reserve Contribution",
                allocation_basis="ownership_percentage",
                allocation_context="reserve_contribution",
                billing_treatment="recurring",
                recipient_scope="all_units",
                denominator_label="sum of ownership percentages",
                denominator_source="exhibit_reference",
                expense_categories=["reserve contribution"],
                is_residual_base=False,
                residual_after_pool_keys=[],
                source_pages=[2],
                confidence=0.95,
            ),
            WireCCRAllocationPool(
                pool_key="structural_repair",
                pool_name="Structural Repair Levy",
                allocation_basis="square_footage",
                allocation_context="special_assessment",
                billing_treatment="separate_one_time",
                recipient_scope="all_units",
                denominator_label="total square footage",
                denominator_source="exhibit_reference",
                expense_categories=["structural repair"],
                is_residual_base=False,
                residual_after_pool_keys=[],
                source_pages=[3],
                confidence=0.95,
            ),
            WireCCRAllocationPool(
                pool_key="limited_benefit_cost_center",
                pool_name="Limited Benefit Facility",
                allocation_basis="unknown",
                allocation_context="cost_center",
                billing_treatment="operator_amount_pending",
                recipient_scope="",
                denominator_label=None,
                denominator_source="unknown",
                expense_categories=["limited benefit facility"],
                is_residual_base=False,
                residual_after_pool_keys=[],
                source_pages=[2],
                confidence=0.75,
            ),
        ],
        reserve_policy=WireCCRReservePolicy(
            funded_through_regular_assessment=False,
            separate_reserve_basis="ownership percentage",
            source_pages=[2],
            confidence=0.95,
        ),
        validation_checks=[],
        human_review_questions=[],
    )
