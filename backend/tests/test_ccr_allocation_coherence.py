"""Unit tests for CC&R allocation-policy coherence gate."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.dre_extraction.schemas import (
    AllocationPoolBlock,
    AssessmentSetupBlock,
    DocumentMetadata,
    DRESetupExtraction,
    UnitPoolFactor,
    UnitRow,
    UnitStructure,
)
from app.dre_extraction.promotion import parse_extraction_payload
from app.governing_doc_extraction.coherence import (
    IncoherentCcrExtraction,
    apply_coherence_to_extraction,
    assess_allocation_coherence,
    assert_ccr_allocation_coherent,
)
from tests.support.missouri_allocation_fixture import (
    missouri_run_18_extraction_payload,
)


def _extraction(
    *,
    setup_type: str,
    pools: list[AllocationPoolBlock],
    units: list[UnitRow] | None = None,
    declared_contexts: list[str] | None = None,
) -> DRESetupExtraction:
    return DRESetupExtraction(
        document_metadata=DocumentMetadata(
            association_name="Test HOA",
            total_units=len(units or []) or None,
            confidence=0.9,
            source_pages=[1],
        ),
        assessment_setup=AssessmentSetupBlock(
            setup_type=setup_type,  # type: ignore[arg-type]
            summary="test",
            confidence=0.9,
            source_pages=[1],
            declared_contexts=declared_contexts or [],
        ),
        unit_structure=UnitStructure(
            unit_count=len(units or []),
            units=list(units or []),
        ),
        allocation_pools=list(pools),
    )


def _pool(
    method: str,
    key: str = "pool_a",
    *,
    context: str = "regular_operating",
    billing: str = "recurring",
    cadence: str = "recurring",
    amount_availability: str = "known",
    recipient_scope: str = "",
) -> AllocationPoolBlock:
    return AllocationPoolBlock(
        pool_key=key,
        pool_name=key,
        allocation_method=method,  # type: ignore[arg-type]
        allocation_context=context,  # type: ignore[arg-type]
        billing_treatment=billing,  # type: ignore[arg-type]
        billing_cadence=cadence,  # type: ignore[arg-type]
        amount_availability=amount_availability,  # type: ignore[arg-type]
        recipient_scope=recipient_scope,
        confidence=0.9,
        source_pages=[1],
    )


def _unit(num: str, pct: str = "10") -> UnitRow:
    return UnitRow(
        unit_number=num,
        ownership_percent=Decimal(pct),
        square_feet=Decimal("1000"),
        confidence=0.9,
        source_page=1,
    )


class TestAssessAllocationCoherence:
    def test_missouri_run_18_context_categories_are_coherent(self) -> None:
        extraction = parse_extraction_payload(
            json.dumps(missouri_run_18_extraction_payload())
        )

        assert extraction is not None
        pools = {pool.pool_key: pool for pool in extraction.allocation_pools}
        assert pools["variable_dre_operating"].allocation_method == "custom_factor"
        assert pools["variable_dre_reserves"].allocation_method == "custom_factor"
        assert (
            pools["variable_dre_operating"].allocation_context
            == "regular_operating"
        )
        assert (
            pools["variable_dre_reserves"].allocation_context
            == "reserve_contribution"
        )
        assert not assess_allocation_coherence(extraction).is_incoherent

    def test_missouri_collapse_incoherent(self) -> None:
        ext = _extraction(
            setup_type="individual_unit",
            pools=[_pool("ownership_percentage")],
            units=[_unit("201", "14.5"), _unit("202", "8.6")],
        )
        finding = assess_allocation_coherence(ext)
        assert finding.is_incoherent
        assert any("individual_unit" in r for r in finding.reasons)

    def test_fixed_equal_coherent(self) -> None:
        ext = _extraction(
            setup_type="fixed_equal",
            pools=[_pool("equal")],
            units=[_unit("1")],
        )
        assert not assess_allocation_coherence(ext).is_incoherent

    def test_multi_pool_coherent(self) -> None:
        ext = _extraction(
            setup_type="multi_pool_combination",
            pools=[
                _pool("equal", "equal_base"),
                _pool("square_footage", "exceptions"),
            ],
            units=[_unit("1")],
        )
        assert not assess_allocation_coherence(ext).is_incoherent

    def test_pure_proportional_not_individual_unit_coherent(self) -> None:
        ext = _extraction(
            setup_type="unknown_needs_review",
            pools=[_pool("ownership_percentage")],
            units=[_unit("1"), _unit("2")],
        )
        assert not assess_allocation_coherence(ext).is_incoherent

    def test_multi_pool_type_with_one_pool_incoherent(self) -> None:
        ext = _extraction(
            setup_type="multi_pool_combination",
            pools=[_pool("equal")],
            units=[],
        )
        finding = assess_allocation_coherence(ext)
        assert finding.is_incoherent

    def test_declared_special_context_requires_a_special_pool(self) -> None:
        ext = _extraction(
            setup_type="multi_pool_combination",
            pools=[_pool("equal", "equal_base")],
            declared_contexts=["regular_operating", "special_assessment"],
        )

        finding = assess_allocation_coherence(ext)

        assert finding.is_incoherent
        assert any("special_assessment" in reason for reason in finding.reasons)

    def test_special_pool_requires_separate_one_time_billing(self) -> None:
        ext = _extraction(
            setup_type="multi_pool_combination",
            pools=[
                _pool("equal", "equal_base"),
                _pool(
                    "square_footage",
                    "structural_repair",
                    context="special_assessment",
                    billing="recurring",
                ),
            ],
            declared_contexts=["regular_operating", "special_assessment"],
        )

        finding = assess_allocation_coherence(ext)

        assert finding.is_incoherent
        assert any("one_time" in reason for reason in finding.reasons)

    def test_cost_center_requires_explicit_recipient_scope(self) -> None:
        ext = _extraction(
            setup_type="multi_pool_combination",
            pools=[
                _pool("equal", "equal_base"),
                _pool(
                    "specified_value",
                    "parking_cost_center",
                    context="cost_center",
                    billing="operator_amount_pending",
                ),
            ],
            declared_contexts=["regular_operating", "cost_center"],
        )

        finding = assess_allocation_coherence(ext)

        assert finding.is_incoherent
        assert any("recipient scope" in reason for reason in finding.reasons)

    def test_cost_center_with_unknown_basis_is_unresolved(self) -> None:
        ext = _extraction(
            setup_type="multi_pool_combination",
            pools=[
                _pool("equal", "equal_base"),
                _pool(
                    "unknown",
                    "parking_cost_center",
                    context="cost_center",
                    billing="operator_amount_pending",
                    recipient_scope="units_with_appurtenant_parking",
                ),
            ],
            declared_contexts=["regular_operating", "cost_center"],
        )

        finding = assess_allocation_coherence(ext)

        assert finding.is_incoherent
        assert any("allocation basis" in reason for reason in finding.reasons)

    def test_declared_context_rule_requires_source_pages(self) -> None:
        pool = _pool(
            "square_footage",
            "structural_repair",
            context="special_assessment",
            billing="separate_one_time",
            cadence="one_time",
        ).model_copy(update={"pool_kind": "separately_billed_special_assessment", "source_pages": []})
        ext = _extraction(
            setup_type="multi_pool_combination",
            pools=[_pool("equal", "equal_base"), pool],
            declared_contexts=["regular_operating", "special_assessment"],
        )

        finding = assess_allocation_coherence(ext)

        assert finding.is_incoherent
        assert any("source page" in reason for reason in finding.reasons)

    def test_run_18_residual_only_requires_same_recurring_billing_stream(self) -> None:
        residual = _pool("equal", "equal_base").model_copy(
            update={
                "budget_line_derivation": "residual_default",
                "residual_after_pool_keys": [
                    "operating_exception",
                    "reserve_contribution",
                    "parking_cost_center",
                ],
            }
        )
        ext = _extraction(
            setup_type="multi_pool_combination",
            pools=[
                residual,
                _pool(
                    "custom_factor",
                    "operating_exception",
                    amount_availability="external_schedule",
                ),
                _pool(
                    "custom_factor",
                    "reserve_contribution",
                    context="reserve_contribution",
                    amount_availability="external_schedule",
                ),
                _pool(
                    "custom_factor",
                    "parking_cost_center",
                    context="cost_center",
                    recipient_scope="parking_users",
                    amount_availability="external_schedule",
                ),
                _pool(
                    "custom_factor",
                    "structural_repair",
                    context="special_assessment",
                    billing="separate_one_time",
                    cadence="one_time",
                    amount_availability="operator_pending",
                ).model_copy(
                    update={"pool_kind": "separately_billed_special_assessment"}
                ),
            ],
            declared_contexts=[
                "regular_operating",
                "reserve_contribution",
                "cost_center",
                "special_assessment",
            ],
        )

        finding = assess_allocation_coherence(ext)

        assert not any("structural_repair" in reason for reason in finding.reasons)
        assert not finding.is_incoherent

    @pytest.mark.parametrize(
        "missing_key",
        ["operating_exception", "reserve_contribution", "parking_cost_center"],
    )
    def test_run_18_residual_blocks_missing_recurring_exclusions(
        self,
        missing_key: str,
    ) -> None:
        required = {
            "operating_exception",
            "reserve_contribution",
            "parking_cost_center",
        }
        residual = _pool("equal", "equal_base").model_copy(
            update={
                "budget_line_derivation": "residual_default",
                "residual_after_pool_keys": sorted(required - {missing_key}),
            }
        )
        ext = _extraction(
            setup_type="multi_pool_combination",
            pools=[
                residual,
                _pool("custom_factor", "operating_exception"),
                _pool(
                    "custom_factor",
                    "reserve_contribution",
                    context="reserve_contribution",
                ),
                _pool(
                    "custom_factor",
                    "parking_cost_center",
                    context="cost_center",
                    recipient_scope="parking_users",
                ),
                _pool(
                    "custom_factor",
                    "structural_repair",
                    context="special_assessment",
                    billing="separate_one_time",
                    cadence="one_time",
                ).model_copy(
                    update={"pool_kind": "separately_billed_special_assessment"}
                ),
            ],
        )

        finding = assess_allocation_coherence(ext)

        assert finding.is_incoherent
        assert any(missing_key in reason for reason in finding.reasons)
        assert not any("structural_repair" in reason for reason in finding.reasons)

    def test_proportional_zero_units_coherent_for_this_gate(self) -> None:
        ext = _extraction(
            setup_type="individual_unit",
            pools=[_pool("ownership_percentage")],
            units=[],
        )
        # Without units, Missouri-class clause does not fire.
        assert not assess_allocation_coherence(ext).is_incoherent

    def test_individual_unit_with_dollar_schedule_coherent(self) -> None:
        unit = UnitRow(
            unit_number="1",
            ownership_percent=None,
            confidence=0.9,
            source_page=1,
            pool_factors=[
                UnitPoolFactor(
                    pool_key="dues",
                    factor_value=Decimal("250"),
                    factor_type="dollar_amount",
                )
            ],
        )
        ext = _extraction(
            setup_type="individual_unit",
            pools=[_pool("specified_value")],
            units=[unit],
        )
        assert not assess_allocation_coherence(ext).is_incoherent

    def test_none_extraction_coherent(self) -> None:
        assert not assess_allocation_coherence(None).is_incoherent


class TestApplyAndAssert:
    def test_apply_appends_hrq_once(self) -> None:
        ext = _extraction(
            setup_type="individual_unit",
            pools=[_pool("ownership_percentage")],
            units=[_unit("1")],
        )
        finding = assess_allocation_coherence(ext)
        once = apply_coherence_to_extraction(ext, finding)
        assert len(once.human_review_questions) == 1
        assert once.human_review_questions[0].severity == "high"
        twice = apply_coherence_to_extraction(once, finding)
        assert len(twice.human_review_questions) == 1

    def test_assert_raises(self) -> None:
        ext = _extraction(
            setup_type="individual_unit",
            pools=[_pool("square_footage")],
            units=[_unit("1")],
        )
        with pytest.raises(IncoherentCcrExtraction) as ei:
            assert_ccr_allocation_coherent(ext)
        assert ei.value.reasons

    def test_assert_ok(self) -> None:
        ext = _extraction(
            setup_type="fixed_equal",
            pools=[_pool("equal")],
        )
        assert_ccr_allocation_coherent(ext)
