"""Unit tests for CC&R allocation-policy coherence gate."""

from __future__ import annotations

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
from app.governing_doc_extraction.coherence import (
    IncoherentCcrExtraction,
    apply_coherence_to_extraction,
    assess_allocation_coherence,
    assert_ccr_allocation_coherent,
)


def _extraction(
    *,
    setup_type: str,
    pools: list[AllocationPoolBlock],
    units: list[UnitRow] | None = None,
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
        ),
        unit_structure=UnitStructure(
            unit_count=len(units or []),
            units=list(units or []),
        ),
        allocation_pools=list(pools),
    )


def _pool(method: str, key: str = "pool_a") -> AllocationPoolBlock:
    return AllocationPoolBlock(
        pool_key=key,
        pool_name=key,
        allocation_method=method,  # type: ignore[arg-type]
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
