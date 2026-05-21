"""Adapter: ``WireDRESetupExtraction`` → ``DRESetupExtraction``.

The wire schemas (``wire_schemas.py``) are intentionally restrictive
for Gemini's constrained-decoding requirements: every field is
required at the model level (``Optional[X]`` for nullables, no
defaults). The domain schemas (``schemas.py``) are looser at the
Pydantic boundary (default-bearing strings, lists, floats) because
downstream code expects shape-stable, default-populated values.

This adapter is the only bridge between the two layers. It:

1. **Applies the defaults the wire schema cannot carry.** Text fields
   become ``""`` instead of ``None``; list fields become ``[]``;
   booleans take the domain's documented default; floats take ``0.0``.
2. **Coerces JSON numerics to ``Decimal``.** ``Decimal(str(x))`` keeps
   the printed form intact and avoids the binary-float drift you would
   get from ``Decimal(x)``.
3. **Normalizes ``None`` → ``""`` for the legacy text-field path** that
   ``_coerce_dict_to_string`` used to handle. This replaces the
   permissive validator: under structured-output mode, Gemini emits
   either a string or ``null``, never the dict form the validator was
   defensive against.

The adapter is intentionally small and deterministic — every helper is
a pure function so the test surface is just "given wire X, do we get
domain Y".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from .schemas import (
    AllocationPoolBlock,
    AssessmentSetupBlock,
    DRESetupExtraction,
    DocumentMetadata,
    FormulaBlock,
    GroupRow,
    HumanReviewQuestion,
    PageInventoryEntry,
    RecommendedSavedSetup,
    ReserveSetupBlock,
    UnitPoolFactor,
    UnitRow,
    UnitStructure,
    ValidationCheck,
)
from . import wire_schemas as ws


def _text(value: Optional[str]) -> str:
    """``None`` / blank → ``""``; everything else → unchanged string."""
    return value if value is not None else ""


def _confidence(value: Optional[float]) -> float:
    """Domain ``confidence`` is a non-optional ``float`` with a ``0.0`` default."""
    return float(value) if value is not None else 0.0


def _list(value: Optional[list[Any]]) -> list[Any]:
    """``None`` → empty list."""
    return list(value) if value is not None else []


def _decimal(value: Any) -> Optional[Decimal]:
    """Coerce JSON number / string to ``Decimal`` preserving printed form.

    ``Decimal(str(x))`` avoids the binary-float drift you would otherwise
    get from ``Decimal(x)`` when ``x`` is a Python float.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _doc_meta(wire: ws.WireDocumentMetadata) -> DocumentMetadata:
    return DocumentMetadata(
        association_name=_text(wire.association_name),
        document_title=_text(wire.document_title),
        dre_file_number=_text(wire.dre_file_number),
        document_date=_text(wire.document_date),
        preparer=_text(wire.preparer),
        location=_text(wire.location),
        total_units=wire.total_units,
        confidence=_confidence(wire.confidence),
        source_pages=_list(wire.source_pages),
    )


def _page_entry(wire: ws.WirePageInventoryEntry) -> PageInventoryEntry:
    return PageInventoryEntry(
        page_number=wire.page_number,
        page_type=wire.page_type,
        confidence=_confidence(wire.confidence),
        notes=_text(wire.notes),
    )


def _setup(wire: ws.WireAssessmentSetupBlock) -> AssessmentSetupBlock:
    requires_dre = (
        wire.requires_dre_for_future_years
        if wire.requires_dre_for_future_years is not None
        else True
    )
    return AssessmentSetupBlock(
        setup_type=wire.setup_type,
        display_mode=_text(wire.display_mode),
        summary=_text(wire.summary),
        requires_dre_for_future_years=requires_dre,
        confidence=_confidence(wire.confidence),
        source_pages=_list(wire.source_pages),
    )


def _group(wire: ws.WireGroupRow) -> GroupRow:
    return GroupRow(
        group_id=_text(wire.group_id),
        label=_text(wire.label),
        unit_count=wire.unit_count,
        average_square_feet=_decimal(wire.average_square_feet),
        ownership_percent=_decimal(wire.ownership_percent),
        factor=_decimal(wire.factor),
        source_page=wire.source_page,
        confidence=_confidence(wire.confidence),
    )


def _unit_factor(wire: ws.WireUnitPoolFactor) -> UnitPoolFactor:
    return UnitPoolFactor(
        pool_key=_text(wire.pool_key),
        factor_value=_decimal(wire.factor_value),
        factor_label=_text(wire.factor_label),
        factor_type=_text(wire.factor_type) or "raw_factor",
        source_page=wire.source_page,
    )


def _unit(wire: ws.WireUnitRow) -> UnitRow:
    return UnitRow(
        unit_number=_text(wire.unit_number),
        square_feet=_decimal(wire.square_feet),
        ownership_percent=_decimal(wire.ownership_percent),
        category=_text(wire.category),
        residential_commercial_flag=_text(wire.residential_commercial_flag),
        parking_flag=_text(wire.parking_flag),
        source_page=wire.source_page,
        confidence=_confidence(wire.confidence),
        pool_factors=[_unit_factor(f) for f in _list(wire.pool_factors)],
    )


def _unit_structure(wire: ws.WireUnitStructure) -> UnitStructure:
    return UnitStructure(
        unit_count=wire.unit_count,
        group_count=wire.group_count,
        groups=[_group(g) for g in _list(wire.groups)],
        units=[_unit(u) for u in _list(wire.units)],
    )


def _pool(wire: ws.WireAllocationPoolBlock) -> AllocationPoolBlock:
    return AllocationPoolBlock(
        pool_key=wire.pool_key,
        parent_pool_key=_text(wire.parent_pool_key),
        pool_name=_text(wire.pool_name),
        annual_amount=_decimal(wire.annual_amount),
        monthly_amount=_decimal(wire.monthly_amount),
        allocation_method=wire.allocation_method,
        recipient_scope=_text(wire.recipient_scope),
        denominator_label=_text(wire.denominator_label),
        denominator_value=_decimal(wire.denominator_value),
        denominator_source=wire.denominator_source,
        included_budget_lines=_list(wire.included_budget_lines),
        excluded_budget_lines=_list(wire.excluded_budget_lines),
        source_pages=_list(wire.source_pages),
        confidence=_confidence(wire.confidence),
    )


def _formula(wire: ws.WireFormulaBlock) -> FormulaBlock:
    return FormulaBlock(
        formula_name=_text(wire.formula_name),
        formula_expression=_text(wire.formula_expression),
        example_from_dre=_text(wire.example_from_dre),
        source_page=wire.source_page,
        confidence=_confidence(wire.confidence),
    )


def _reserve_setup(wire: ws.WireReserveSetupBlock) -> ReserveSetupBlock:
    return ReserveSetupBlock(
        reserve_contribution=_decimal(wire.reserve_contribution),
        reserve_beginning_balance=_decimal(wire.reserve_beginning_balance),
        inflation_assumption=_decimal(wire.inflation_assumption),
        interest_assumption=_decimal(wire.interest_assumption),
        allocation_method=_text(wire.allocation_method),
        source_pages=_list(wire.source_pages),
        confidence=_confidence(wire.confidence),
    )


def _check(wire: ws.WireValidationCheck) -> ValidationCheck:
    return ValidationCheck(
        check_name=_text(wire.check_name),
        status=wire.status,
        details=_text(wire.details),
        source_pages=_list(wire.source_pages),
    )


def _question(wire: ws.WireHumanReviewQuestion) -> HumanReviewQuestion:
    return HumanReviewQuestion(
        question=_text(wire.question),
        reason=_text(wire.reason),
        source_pages=_list(wire.source_pages),
        severity=wire.severity,
    )


def _recommended(wire: ws.WireRecommendedSavedSetup) -> RecommendedSavedSetup:
    return RecommendedSavedSetup(
        assessment_setup_type=_text(wire.assessment_setup_type),
        display_mode=_text(wire.display_mode),
        required_manual_fields=_list(wire.required_manual_fields),
        required_budget_line_mappings=_list(wire.required_budget_line_mappings),
        notes=_text(wire.notes),
    )


def to_domain(wire: ws.WireDRESetupExtraction) -> DRESetupExtraction:
    """Convert a wire-shaped extraction to the domain shape."""
    return DRESetupExtraction(
        document_metadata=_doc_meta(wire.document_metadata),
        page_inventory=[_page_entry(e) for e in _list(wire.page_inventory)],
        assessment_setup=_setup(wire.assessment_setup),
        unit_structure=_unit_structure(wire.unit_structure),
        allocation_pools=[_pool(p) for p in _list(wire.allocation_pools)],
        formulas=[_formula(f) for f in _list(wire.formulas)],
        reserve_setup=(
            _reserve_setup(wire.reserve_setup) if wire.reserve_setup else None
        ),
        validation_checks=[_check(c) for c in _list(wire.validation_checks)],
        human_review_questions=[
            _question(q) for q in _list(wire.human_review_questions)
        ],
        recommended_saved_setup=(
            _recommended(wire.recommended_saved_setup)
            if wire.recommended_saved_setup
            else None
        ),
    )


__all__ = ["to_domain"]
