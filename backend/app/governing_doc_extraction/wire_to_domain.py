"""Adapter: WireCCRPolicyExtraction → shared DRESetupExtraction domain shape.

The CC&R wire schema is allocation-policy-focused (legal prose → pool rules).
The domain schema is what promote_extraction_to_setup() consumes. Converging
on the shared shape is the integration seam that lets promotion, Review
Workbench, approval, and assessment mapping require zero changes.

Key translation decisions:
  • document_metadata: maps CCR fields to the shared DocumentMetadata, filling
    dre_file_number / preparer / location with "" (not present in CC&Rs).
  • assessment_setup.setup_type: CCR types are a subset of domain types.
  • allocation_pools: CCR allocation_basis → domain allocation_method (same values);
    CCR denominator_source values mapped to domain's "dre_shown" | "calculated" | "unknown".
  • No formulas or budget_line_mapping_evidence in CC&Rs — empty lists.
  • reserve_setup: mapped if present.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from app.dre_extraction.schemas import (
    AllocationPoolBlock,
    AssessmentSetupBlock,
    DRESetupExtraction,
    DocumentMetadata,
    FormulaBlock,
    HumanReviewQuestion,
    PageInventoryEntry,
    ReserveSetupBlock,
    UnitStructure,
    ValidationCheck,
)
from . import wire_schemas as ws


def _text(value: Optional[str]) -> str:
    return value if value is not None else ""


def _confidence(value: Optional[float]) -> float:
    return float(value) if value is not None else 0.0


def _list(value: Optional[list[Any]]) -> list[Any]:
    return list(value) if value is not None else []


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _denominator_source(ccr_source: str) -> str:
    """Map CCR-specific denominator_source values to the domain's three-way enum."""
    mapping = {
        "document_stated": "dre_shown",
        "exhibit_reference": "unknown",
        "calculated": "calculated",
        "unknown": "unknown",
    }
    return mapping.get(ccr_source, "unknown")


def _doc_meta(wire: ws.WireCCRDocumentMetadata) -> DocumentMetadata:
    return DocumentMetadata(
        association_name=_text(wire.association_name),
        document_title=_text(wire.document_title),
        dre_file_number="",
        document_date=_text(wire.document_date),
        preparer="",
        location="",
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


def _setup(wire: ws.WireCCRAssessmentSetupBlock) -> AssessmentSetupBlock:
    requires_dre = (
        wire.requires_dre_for_future_years
        if wire.requires_dre_for_future_years is not None
        else True
    )
    return AssessmentSetupBlock(
        setup_type=wire.setup_type,
        display_mode="",
        summary=_text(wire.summary),
        requires_dre_for_future_years=requires_dre,
        confidence=_confidence(wire.confidence),
        source_pages=_list(wire.source_pages),
    )


def _pool(wire: ws.WireCCRAllocationPool) -> AllocationPoolBlock:
    is_residual = wire.is_residual_base if wire.is_residual_base is not None else False
    derivation = "residual_default" if is_residual else "explicit_lines"

    return AllocationPoolBlock(
        pool_key=wire.pool_key,
        parent_pool_key="",
        pool_name=_text(wire.pool_name),
        annual_amount=None,
        monthly_amount=None,
        allocation_method=wire.allocation_basis,
        recipient_scope=_text(wire.recipient_scope),
        denominator_label=_text(wire.denominator_label),
        denominator_value=None,
        denominator_source=_denominator_source(wire.denominator_source),
        included_budget_lines=_list(wire.expense_categories),
        excluded_budget_lines=[],
        budget_line_derivation=derivation,
        residual_after_pool_keys=_list(wire.residual_after_pool_keys),
        residual_exclusions=[],
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


def to_domain(wire: ws.WireCCRPolicyExtraction) -> DRESetupExtraction:
    """Convert a CC&R wire extraction to the shared DRESetupExtraction domain shape.

    The resulting DRESetupExtraction validates against the same schema
    promote_extraction_to_setup() expects, so promotion works with zero changes.
    """
    reserve_setup: Optional[ReserveSetupBlock] = None
    if wire.reserve_policy is not None:
        rp = wire.reserve_policy
        reserve_setup = ReserveSetupBlock(
            reserve_contribution=None,
            reserve_beginning_balance=None,
            inflation_assumption=None,
            interest_assumption=None,
            allocation_method=_text(rp.separate_reserve_basis),
            source_pages=_list(rp.source_pages),
            confidence=0.0,
        )

    return DRESetupExtraction(
        document_metadata=_doc_meta(wire.document_metadata),
        page_inventory=[_page_entry(e) for e in _list(wire.page_inventory)],
        assessment_setup=_setup(wire.assessment_setup),
        unit_structure=UnitStructure(
            unit_count=wire.unit_structure.unit_count,
            group_count=None,
            groups=[],
            units=[],
        ),
        allocation_pools=[_pool(p) for p in _list(wire.allocation_pools)],
        formulas=[],
        reserve_setup=reserve_setup,
        budget_line_mapping_evidence=[],
        validation_checks=[_check(c) for c in _list(wire.validation_checks)],
        human_review_questions=[
            _question(q) for q in _list(wire.human_review_questions)
        ],
        recommended_saved_setup=None,
    )


__all__ = ["to_domain"]
