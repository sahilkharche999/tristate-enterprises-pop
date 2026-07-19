"""DRE extraction pipeline.

One-time-per-HOA setup ingestion: PDF DREs → Gemini extraction → draft
``AssessmentSetup`` row → operator review/approve → live setup.

This package owns the prompt text, prompt-vocab schemas, enum mapping
to the canonical engine model, defense-in-depth validation, and the
page-classification batching used for large DREs. The DB persistence
side (``DREDocument``, ``DREExtractionRun``) is wired up by Phase 1.3
of the change.
"""

from .adapter import (
    AllocationMethodMapping,
    map_allocation_method,
)
from .page_classification import (
    DEFAULT_BATCH_SIZE,
    EXTRACTION_RELEVANT_PAGE_TYPES,
    SINGLE_CALL_PAGE_THRESHOLD,
    ClassificationResult,
    PageBatch,
    classify_pages,
    filter_relevant_pages,
    merge_inventory,
    split_pages_into_batches,
)
from .page_rendering import (
    DRE_RENDER_DPI,
    render_dre_pages,
)
from .schemas import (
    AllocationPoolBlock,
    AssessmentSetupBlock,
    BudgetLineMappingEvidence,
    DRESetupExtraction,
    DocumentMetadata,
    FormulaBlock,
    GroupRow,
    HumanReviewQuestion,
    PageInventoryEntry,
    PromptAllocationMethod,
    PromptSetupType,
    RecommendedSavedSetup,
    ReserveSetupBlock,
    UnitRow,
    UnitStructure,
    ValidationCheck,
)
from .pipeline import (
    DREExtractionRunRecord,
    RunStatus,
    run_dre_extraction,
)
from .validation import (
    LOW_CONFIDENCE_THRESHOLD,
    CitationAudit,
    ExtractionParseError,
    LowConfidenceFlag,
    ParseResult,
    audit_entity_citations,
    collect_low_confidence_flags,
    collect_validation_warnings,
    parse_extraction_response,
)

__all__ = [
    "AllocationMethodMapping",
    "AllocationPoolBlock",
    "AssessmentSetupBlock",
    "BudgetLineMappingEvidence",
    "CitationAudit",
    "ClassificationResult",
    "DEFAULT_BATCH_SIZE",
    "DRE_RENDER_DPI",
    "DRESetupExtraction",
    "DocumentMetadata",
    "EXTRACTION_RELEVANT_PAGE_TYPES",
    "ExtractionParseError",
    "FormulaBlock",
    "GroupRow",
    "HumanReviewQuestion",
    "LOW_CONFIDENCE_THRESHOLD",
    "LowConfidenceFlag",
    "PageBatch",
    "PageInventoryEntry",
    "ParseResult",
    "PromptAllocationMethod",
    "PromptSetupType",
    "RecommendedSavedSetup",
    "ReserveSetupBlock",
    "SINGLE_CALL_PAGE_THRESHOLD",
    "UnitRow",
    "UnitStructure",
    "ValidationCheck",
    "audit_entity_citations",
    "classify_pages",
    "collect_low_confidence_flags",
    "collect_validation_warnings",
    "filter_relevant_pages",
    "map_allocation_method",
    "merge_inventory",
    "parse_extraction_response",
    "render_dre_pages",
    "split_pages_into_batches",
]
