"""Unit tests for the ``wire_to_domain`` adapter.

Cover the three responsibilities of the adapter:

* Coerce JSON numerics to ``Decimal`` (printed form preserved via
  ``Decimal(str(x))``) — every one of the 7 Decimal field families.
* Normalize ``None`` → ``""`` for the legacy text-field-coercion path.
* Apply defaults the wire schema cannot carry (empty lists, ``0.0``
  confidences, ``True`` for ``requires_dre_for_future_years``).

Plus a full Esprit-Park-shaped round trip to confirm the top-level
``to_domain`` doesn't drop or reshape any field.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.dre_extraction.schemas import (
    AllocationPoolBlock,
    DRESetupExtraction,
    DocumentMetadata,
)
from app.dre_extraction.wire_schemas import (
    WireAllocationPoolBlock,
    WireAssessmentSetupBlock,
    WireDocumentMetadata,
    WireDRESetupExtraction,
    WireFormulaBlock,
    WireGroupRow,
    WireHumanReviewQuestion,
    WirePageInventoryEntry,
    WireRecommendedSavedSetup,
    WireReserveSetupBlock,
    WireUnitPoolFactor,
    WireUnitRow,
    WireUnitStructure,
    WireValidationCheck,
)
from app.dre_extraction.wire_to_domain import to_domain


def _empty_wire() -> WireDRESetupExtraction:
    """A wire object with every nullable field set to ``None`` and lists empty."""
    return WireDRESetupExtraction(
        document_metadata=WireDocumentMetadata(
            association_name=None,
            document_title=None,
            dre_file_number=None,
            document_date=None,
            preparer=None,
            location=None,
            total_units=None,
            confidence=None,
            source_pages=None,
        ),
        page_inventory=[],
        assessment_setup=WireAssessmentSetupBlock(
            setup_type="unknown_needs_review",
            display_mode=None,
            summary=None,
            requires_dre_for_future_years=None,
            confidence=None,
            source_pages=None,
        ),
        unit_structure=WireUnitStructure(
            unit_count=None,
            group_count=None,
            groups=None,
            units=None,
        ),
        allocation_pools=[],
        formulas=[],
        reserve_setup=None,
        validation_checks=[],
        human_review_questions=[],
        recommended_saved_setup=None,
    )


class TestTextFieldCoercion:
    def test_doc_meta_none_text_fields_become_empty_string(self) -> None:
        domain = to_domain(_empty_wire())
        assert domain.document_metadata.association_name == ""
        assert domain.document_metadata.document_title == ""
        assert domain.document_metadata.dre_file_number == ""
        assert domain.document_metadata.document_date == ""
        assert domain.document_metadata.preparer == ""
        assert domain.document_metadata.location == ""

    def test_setup_none_text_fields_become_empty_string(self) -> None:
        domain = to_domain(_empty_wire())
        assert domain.assessment_setup.display_mode == ""
        assert domain.assessment_setup.summary == ""

    def test_real_text_passes_through_unchanged(self) -> None:
        wire = _empty_wire()
        wire.document_metadata.preparer = "Acme Property Services, Inc."
        wire.document_metadata.location = "Mountain View, CA"
        domain = to_domain(wire)
        assert domain.document_metadata.preparer == "Acme Property Services, Inc."
        assert domain.document_metadata.location == "Mountain View, CA"


class TestDecimalCoercion:
    def test_group_decimal_fields_preserve_printed_form(self) -> None:
        wire = _empty_wire()
        wire.unit_structure.groups = [
            WireGroupRow(
                group_id="A",
                label="Plan I",
                unit_count=10,
                average_square_feet=1234.56,
                ownership_percent=12.345,
                factor=1.15,
                source_page=4,
                confidence=0.95,
            )
        ]
        domain = to_domain(wire)
        g = domain.unit_structure.groups[0]
        assert g.average_square_feet == Decimal("1234.56")
        assert g.ownership_percent == Decimal("12.345")
        assert g.factor == Decimal("1.15")

    def test_unit_decimal_fields_preserve_printed_form(self) -> None:
        wire = _empty_wire()
        wire.unit_structure.units = [
            WireUnitRow(
                unit_number="101",
                square_feet=987.65,
                ownership_percent=1.234,
                category="plan_i",
                residential_commercial_flag="residential",
                parking_flag="no",
                source_page=8,
                confidence=0.9,
                pool_factors=None,
            )
        ]
        domain = to_domain(wire)
        u = domain.unit_structure.units[0]
        assert u.square_feet == Decimal("987.65")
        assert u.ownership_percent == Decimal("1.234")

    def test_pool_decimal_fields_preserve_printed_form(self) -> None:
        wire = _empty_wire()
        wire.allocation_pools = [
            WireAllocationPoolBlock(
                pool_key="general_common",
                parent_pool_key=None,
                pool_name="General Common",
                annual_amount=12345.67,
                monthly_amount=1028.81,
                allocation_method="square_footage",
                recipient_scope="all_units",
                denominator_label="Total Sq Ft",
                denominator_value=24680.0,
                denominator_source="dre_shown",
                included_budget_lines=None,
                excluded_budget_lines=None,
                source_pages=[5],
                confidence=0.92,
            )
        ]
        domain = to_domain(wire)
        p = domain.allocation_pools[0]
        assert p.annual_amount == Decimal("12345.67")
        assert p.monthly_amount == Decimal("1028.81")
        assert p.denominator_value == Decimal("24680.0")

    def test_reserve_decimal_fields_preserve_printed_form(self) -> None:
        wire = _empty_wire()
        wire.reserve_setup = WireReserveSetupBlock(
            reserve_contribution=18000.00,
            reserve_beginning_balance=42000.50,
            inflation_assumption=3.0,
            interest_assumption=1.5,
            allocation_method="proportional",
            source_pages=[7],
            confidence=0.88,
        )
        domain = to_domain(wire)
        r = domain.reserve_setup
        assert r is not None
        assert r.reserve_contribution == Decimal("18000.00")
        assert r.reserve_beginning_balance == Decimal("42000.50")
        assert r.inflation_assumption == Decimal("3.0")
        assert r.interest_assumption == Decimal("1.5")

    def test_none_decimal_stays_none(self) -> None:
        domain = to_domain(_empty_wire())
        # unit_structure default-empty has no rows; reserve_setup is None.
        assert domain.reserve_setup is None


class TestListAndDefaultCoercion:
    def test_none_lists_become_empty(self) -> None:
        wire = _empty_wire()
        wire.document_metadata.source_pages = None
        wire.unit_structure.groups = None
        wire.unit_structure.units = None
        domain = to_domain(wire)
        assert domain.document_metadata.source_pages == []
        assert domain.unit_structure.groups == []
        assert domain.unit_structure.units == []
        assert domain.allocation_pools == []
        assert domain.formulas == []
        assert domain.validation_checks == []
        assert domain.human_review_questions == []

    def test_confidence_none_becomes_zero(self) -> None:
        domain = to_domain(_empty_wire())
        assert domain.document_metadata.confidence == 0.0
        assert domain.assessment_setup.confidence == 0.0

    def test_requires_dre_for_future_years_defaults_to_true(self) -> None:
        """Domain default is ``True``; wire ``None`` must map to ``True``."""
        domain = to_domain(_empty_wire())
        assert domain.assessment_setup.requires_dre_for_future_years is True

    def test_requires_dre_for_future_years_false_passes_through(self) -> None:
        wire = _empty_wire()
        wire.assessment_setup.requires_dre_for_future_years = False
        domain = to_domain(wire)
        assert domain.assessment_setup.requires_dre_for_future_years is False


class TestOptionalSubObjects:
    def test_recommended_saved_setup_none_stays_none(self) -> None:
        domain = to_domain(_empty_wire())
        assert domain.recommended_saved_setup is None

    def test_recommended_saved_setup_present_converts(self) -> None:
        wire = _empty_wire()
        wire.recommended_saved_setup = WireRecommendedSavedSetup(
            assessment_setup_type="grouped_category",
            display_mode="grouped",
            required_manual_fields=["board_approved_amount"],
            required_budget_line_mappings=["Insurance -> general_common"],
            notes=None,
        )
        domain = to_domain(wire)
        r = domain.recommended_saved_setup
        assert r is not None
        assert r.assessment_setup_type == "grouped_category"
        assert r.notes == ""
        assert r.required_manual_fields == ["board_approved_amount"]


class TestFullRoundTrip:
    def test_esprit_park_shaped_wire_round_trips(self) -> None:
        wire = WireDRESetupExtraction(
            document_metadata=WireDocumentMetadata(
                association_name="Esprit Park Townhomes",
                document_title="DRE Budget Worksheet",
                dre_file_number="123456",
                document_date="10/16/2013",
                preparer="Centific Property Services",
                location="South San Francisco, CA",
                total_units=74,
                confidence=0.98,
                source_pages=[1, 2],
            ),
            page_inventory=[
                WirePageInventoryEntry(
                    page_number=1, page_type="cover", confidence=1.0, notes="DRE cover"
                ),
                WirePageInventoryEntry(
                    page_number=2,
                    page_type="unit_summary",
                    confidence=None,
                    notes=None,
                ),
            ],
            assessment_setup=WireAssessmentSetupBlock(
                setup_type="grouped_category",
                display_mode="grouped",
                summary="74 townhomes in 3 plans (I/II/III)",
                requires_dre_for_future_years=False,
                confidence=0.93,
                source_pages=[2, 6],
            ),
            unit_structure=WireUnitStructure(
                unit_count=74,
                group_count=3,
                groups=[
                    WireGroupRow(
                        group_id="I",
                        label="Plan I",
                        unit_count=20,
                        average_square_feet=1400,
                        ownership_percent=1.35,
                        factor=Decimal("1.0"),
                        source_page=6,
                        confidence=0.95,
                    ),
                ],
                units=None,
            ),
            allocation_pools=[
                WireAllocationPoolBlock(
                    pool_key="general_common",
                    parent_pool_key=None,
                    pool_name="General Common",
                    annual_amount=200000.0,
                    monthly_amount=None,
                    allocation_method="ownership_percentage",
                    recipient_scope="all_units",
                    denominator_label="Ownership %",
                    denominator_value=100.0,
                    denominator_source="dre_shown",
                    included_budget_lines=["Insurance", "Landscaping"],
                    excluded_budget_lines=None,
                    source_pages=[5],
                    confidence=0.91,
                ),
            ],
            formulas=[
                WireFormulaBlock(
                    formula_name="monthly_per_unit",
                    formula_expression="annual_pool / 12 / unit_count",
                    example_from_dre="$200,000 / 12 / 74 ≈ $225.23",
                    source_page=5,
                    confidence=0.9,
                ),
            ],
            reserve_setup=WireReserveSetupBlock(
                reserve_contribution=24000.0,
                reserve_beginning_balance=80000.0,
                inflation_assumption=3.0,
                interest_assumption=1.0,
                allocation_method="proportional",
                source_pages=[7, 8],
                confidence=0.88,
            ),
            validation_checks=[
                WireValidationCheck(
                    check_name="ownership_percent_sums_to_100",
                    status="pass",
                    details="Sum = 100.00%",
                    source_pages=[6],
                ),
            ],
            human_review_questions=[
                WireHumanReviewQuestion(
                    question="Confirm Plan III unit count",
                    reason="OCR ambiguity on page 6",
                    source_pages=[6],
                    severity="medium",
                ),
            ],
            recommended_saved_setup=WireRecommendedSavedSetup(
                assessment_setup_type="grouped_category",
                display_mode="grouped",
                required_manual_fields=None,
                required_budget_line_mappings=None,
                notes="Onboarding straightforward",
            ),
        )
        domain = to_domain(wire)
        assert isinstance(domain, DRESetupExtraction)
        assert isinstance(domain.document_metadata, DocumentMetadata)
        assert domain.document_metadata.association_name == "Esprit Park Townhomes"
        assert domain.document_metadata.total_units == 74
        assert len(domain.page_inventory) == 2
        assert domain.page_inventory[1].confidence == 0.0  # None → 0.0
        assert domain.page_inventory[1].notes == ""  # None → ""
        assert domain.assessment_setup.setup_type == "grouped_category"
        assert domain.assessment_setup.requires_dre_for_future_years is False
        assert isinstance(domain.allocation_pools[0], AllocationPoolBlock)
        assert domain.allocation_pools[0].annual_amount == Decimal("200000.0")
        assert domain.allocation_pools[0].excluded_budget_lines == []
        assert domain.unit_structure.groups[0].factor == Decimal("1.0")
        assert domain.reserve_setup is not None
        assert domain.reserve_setup.reserve_contribution == Decimal("24000.0")
        assert domain.recommended_saved_setup is not None
        assert domain.recommended_saved_setup.required_manual_fields == []


class TestUnitPoolFactorCoercion:
    """v2.2 — per-unit per-pool factors on multi-pool DREs."""

    def test_none_pool_factors_becomes_empty_list(self) -> None:
        wire = _empty_wire()
        wire.unit_structure.units = [
            WireUnitRow(
                unit_number="101",
                square_feet=Decimal("1234.56"),
                ownership_percent=None,
                category="commercial",
                residential_commercial_flag="commercial",
                parking_flag="no",
                source_page=4,
                confidence=0.95,
                pool_factors=None,
            )
        ]
        domain = to_domain(wire)
        assert domain.unit_structure.units[0].pool_factors == []

    def test_pool_factors_preserve_printed_decimal_form(self) -> None:
        wire = _empty_wire()
        wire.unit_structure.units = [
            WireUnitRow(
                unit_number="201",
                square_feet=Decimal("1500"),
                ownership_percent=None,
                category="residential",
                residential_commercial_flag="residential",
                parking_flag="yes",
                source_page=5,
                confidence=0.9,
                pool_factors=[
                    WireUnitPoolFactor(
                        pool_key="general_common_prorated",
                        factor_value=1.23456,
                        factor_label="General Assessment Interest",
                        factor_type="percent",
                        source_page=5,
                    ),
                    WireUnitPoolFactor(
                        pool_key="residential_common_prorated",
                        factor_value=2.34567,
                        factor_label="Residential Assessment Interest",
                        factor_type="percent",
                        source_page=5,
                    ),
                ],
            )
        ]
        domain = to_domain(wire)
        factors = domain.unit_structure.units[0].pool_factors
        assert len(factors) == 2
        # Decimal coercion preserves printed precision.
        assert factors[0].factor_value == Decimal("1.23456")
        assert factors[1].factor_value == Decimal("2.34567")
        assert factors[0].pool_key == "general_common_prorated"
        assert factors[0].factor_label == "General Assessment Interest"
        assert factors[0].factor_type == "percent"
        assert factors[0].source_page == 5

    def test_dollar_amount_factor_type_passes_through(self) -> None:
        """For specified_value special-line columns (e.g. unfunded
        liability) the factor_type is dollar_amount."""
        wire = _empty_wire()
        wire.unit_structure.units = [
            WireUnitRow(
                unit_number="301",
                square_feet=None,
                ownership_percent=None,
                category=None,
                residential_commercial_flag=None,
                parking_flag=None,
                source_page=7,
                confidence=None,
                pool_factors=[
                    WireUnitPoolFactor(
                        pool_key="unfunded_liability",
                        factor_value=Decimal("42.50"),
                        factor_label="Unfunded Liability",
                        factor_type="dollar_amount",
                        source_page=7,
                    ),
                ],
            )
        ]
        domain = to_domain(wire)
        factor = domain.unit_structure.units[0].pool_factors[0]
        assert factor.factor_type == "dollar_amount"
        assert factor.factor_value == Decimal("42.50")

    def test_factor_label_none_becomes_empty_string(self) -> None:
        wire = _empty_wire()
        wire.unit_structure.units = [
            WireUnitRow(
                unit_number="401",
                square_feet=None,
                ownership_percent=None,
                category=None,
                residential_commercial_flag=None,
                parking_flag=None,
                source_page=8,
                confidence=None,
                pool_factors=[
                    WireUnitPoolFactor(
                        pool_key="example_prorated",
                        factor_value=Decimal("1.0"),
                        factor_label=None,
                        factor_type="raw_factor",
                        source_page=8,
                    ),
                ],
            )
        ]
        domain = to_domain(wire)
        assert domain.unit_structure.units[0].pool_factors[0].factor_label == ""


class TestParentPoolKey:
    """v2.2 — parent_pool_key threads through the adapter."""

    def test_parent_pool_key_none_becomes_empty_string(self) -> None:
        domain = to_domain(_empty_wire())
        # _empty_wire has no pools, but the field default applies via the
        # adapter when a pool is present without parent_pool_key set.
        # Build one inline:
        wire = _empty_wire()
        wire.allocation_pools = [
            WireAllocationPoolBlock(
                pool_key="top_level_pool",
                parent_pool_key=None,
                pool_name="Top Level",
                annual_amount=Decimal("1000"),
                monthly_amount=None,
                allocation_method="equal",
                recipient_scope="all_units",
                denominator_label=None,
                denominator_value=None,
                denominator_source="unknown",
                included_budget_lines=None,
                excluded_budget_lines=None,
                source_pages=[1],
                confidence=1.0,
            )
        ]
        domain = to_domain(wire)
        assert domain.allocation_pools[0].parent_pool_key == ""

    def test_parent_pool_key_string_carries_through(self) -> None:
        wire = _empty_wire()
        wire.allocation_pools = [
            WireAllocationPoolBlock(
                pool_key="general_common_prorated",
                parent_pool_key="general_common",
                pool_name="General Common (prorated)",
                annual_amount=Decimal("50000"),
                monthly_amount=None,
                allocation_method="ownership_percentage",
                recipient_scope="all_units",
                denominator_label="Ownership %",
                denominator_value=Decimal("100.0"),
                denominator_source="dre_shown",
                included_budget_lines=None,
                excluded_budget_lines=None,
                source_pages=[2],
                confidence=0.95,
            ),
            WireAllocationPoolBlock(
                pool_key="general_common_equal",
                parent_pool_key="general_common",
                pool_name="General Common (equal)",
                annual_amount=Decimal("25000"),
                monthly_amount=None,
                allocation_method="equal",
                recipient_scope="all_units",
                denominator_label="Total Units",
                denominator_value=Decimal("40"),
                denominator_source="dre_shown",
                included_budget_lines=None,
                excluded_budget_lines=None,
                source_pages=[2],
                confidence=0.95,
            ),
        ]
        domain = to_domain(wire)
        assert domain.allocation_pools[0].parent_pool_key == "general_common"
        assert domain.allocation_pools[1].parent_pool_key == "general_common"
        assert domain.allocation_pools[0].pool_key == "general_common_prorated"
        assert domain.allocation_pools[1].pool_key == "general_common_equal"
