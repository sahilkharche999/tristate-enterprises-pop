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
    SetupTypeMapping,
    map_allocation_method,
    map_setup_type,
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
    build_contact_sheet_pdf,
    render_dre_pages,
)
from .schemas import (
    AllocationPoolBlock,
    AssessmentSetupBlock,
    BudgetPoolMapping,
    DRESetupExtraction,
    DocumentMetadata,
    FormulaBlock,
    GroupRow,
    HumanReviewQuestion,
    MappedBudgetLine,
    PageInventoryEntry,
    PoolTotalRow,
    PromptAllocationMethod,
    PromptSetupType,
    RecommendedSavedSetup,
    ReserveSetupBlock,
    UnitRow,
    UnitStructure,
    UnmappedBudgetLine,
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
    "BudgetPoolMapping",
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
    "MappedBudgetLine",
    "PageBatch",
    "PageInventoryEntry",
    "ParseResult",
    "PoolTotalRow",
    "PromptAllocationMethod",
    "PromptSetupType",
    "RecommendedSavedSetup",
    "ReserveSetupBlock",
    "SINGLE_CALL_PAGE_THRESHOLD",
    "SetupTypeMapping",
    "UnitRow",
    "UnitStructure",
    "UnmappedBudgetLine",
    "ValidationCheck",
    "audit_entity_citations",
    "build_contact_sheet_pdf",
    "classify_pages",
    "collect_low_confidence_flags",
    "collect_validation_warnings",
    "filter_relevant_pages",
    "map_allocation_method",
    "map_setup_type",
    "merge_inventory",
    "parse_extraction_response",
    "render_dre_pages",
    "split_pages_into_batches",
]
