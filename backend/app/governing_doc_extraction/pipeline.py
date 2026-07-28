"""CC&R / governing-document extraction orchestrator.

Flow:
    1. Step-1 legal page classification across batches (≤50 pages: one call;
       >50 pages: batched using dre_extraction page_classification helpers).
    2. Merge per-batch inventory; filter to assessment-relevant pages
       using CCR_EXTRACTION_RELEVANT_PAGE_TYPES.
    3. Expand ±1 neighbors of assessment-family pages (union with relevant)
       so multi-page allocation articles are not dropped when mislabeled.
    4. Full extraction call against the expanded page set.
    5. Single repair retry if JSON or schema validation fails.
    6. Citation audit + low-confidence flags + validation-check warnings.
    7. Allocation-coherence soft check (partial + human_review when collapsed).
    8. Assemble CCRExtractionRunRecord with status: succeeded / partial / failed.

Output mirrors DREExtractionRunRecord so persistence (save_extraction_run)
and the Review Workbench consume the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional, Union

from app.dre_extraction.page_classification import PageBatch
from app.dre_extraction.schemas import DRESetupExtraction, PageInventoryEntry
from app.dre_extraction.validation import (
    CitationAudit,
    ExtractionParseError,
    LowConfidenceFlag,
    audit_entity_citations,
    collect_low_confidence_flags,
    collect_validation_warnings,
    parse_extraction_response,
)

from .coherence import (
    apply_coherence_to_extraction,
    assess_allocation_coherence,
)
from .page_classification import (
    CCR_EXTRACTION_RELEVANT_PAGE_TYPES,
    classify_pages,
    expand_relevant_pages_with_neighbors,
)
from .prompts import (
    CCR_POLICY_EXTRACTOR_PROMPT_SHA256,
    CCR_POLICY_EXTRACTOR_PROMPT_VERSION,
)
from .wire_schemas import WireCCRPolicyExtraction
from .wire_to_domain import to_domain


RunStatus = Literal["succeeded", "extraction_partial", "failed"]


@dataclass
class CCRExtractionRunRecord:
    """In-memory representation of one CC&R extraction run.

    Mirrors DREExtractionRunRecord so save_extraction_run() and the
    Review Workbench can treat both uniformly.
    """

    model_name: str
    prompt_version: str
    prompt_sha256: str
    document_type: str = "ccr"

    page_inventory: list[PageInventoryEntry] = field(default_factory=list)
    relevant_page_numbers: list[int] = field(default_factory=list)

    raw_model_output: str = ""
    parsed_json: Optional[dict] = None
    schema_validation_errors: list[str] = field(default_factory=list)
    repair_attempt_count: int = 0

    extraction: Optional[DRESetupExtraction] = None
    citation_audit: Optional[CitationAudit] = None
    low_confidence_flags: list[LowConfidenceFlag] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)

    model_version_resolved: str = ""
    finish_reason: str = ""
    output_tokens_used: int = 0

    status: RunStatus = "failed"


ExtractCallbackResult = tuple[str, Optional[WireCCRPolicyExtraction], dict]


def run_ccr_extraction(
    *,
    page_count: int,
    classify_pages_callback: Callable[[PageBatch], list[PageInventoryEntry]],
    extract_policy_callback: Callable[[list[int]], ExtractCallbackResult],
    repair_callback: Optional[Callable[[str, list[str]], str]] = None,
    model_name: str,
    batch_size: Optional[int] = None,
) -> CCRExtractionRunRecord:
    """Orchestrate one end-to-end CC&R allocation-policy extraction.

    ``classify_pages_callback`` runs Step-1 legal-page classification
    against one PageBatch and returns its page_inventory slice.

    ``extract_policy_callback`` runs the full extraction call against the
    filtered assessment-relevant page numbers and returns
    ``(raw_text, wire_parsed, audit_dict)`` (production path).

    The function never raises — CCRExtractionRunRecord.status reports outcome.
    """
    record = CCRExtractionRunRecord(
        model_name=model_name,
        prompt_version=CCR_POLICY_EXTRACTOR_PROMPT_VERSION,
        prompt_sha256=CCR_POLICY_EXTRACTOR_PROMPT_SHA256,
    )

    # 1–2. Classify and filter to assessment-relevant pages.
    classification_kwargs: dict = {}
    if batch_size is not None:
        classification_kwargs["batch_size"] = batch_size
    classification = classify_pages(
        page_count,
        classify_pages_callback,
        relevant_types=CCR_EXTRACTION_RELEVANT_PAGE_TYPES,
        **classification_kwargs,
    )
    record.page_inventory = classification.full_inventory
    expanded = expand_relevant_pages_with_neighbors(
        classification.full_inventory,
        classification.relevant_page_numbers,
        page_count=page_count,
    )
    record.relevant_page_numbers = expanded

    # 3–4. Full extraction call + parse with single repair retry.
    raw, wire_parsed, audit = extract_policy_callback(expanded)
    if audit is None:
        audit = {}
    record.raw_model_output = raw
    record.model_version_resolved = str(audit.get("model_version", ""))
    record.finish_reason = str(audit.get("finish_reason", ""))
    try:
        record.output_tokens_used = int(audit.get("output_tokens", 0) or 0)
    except (TypeError, ValueError):
        record.output_tokens_used = 0

    # Convert wire to domain before passing to the shared parse path.
    domain_wire_parsed: Optional[DRESetupExtraction] = None
    if wire_parsed is not None:
        try:
            domain_wire_parsed = to_domain(wire_parsed)
        except Exception:
            domain_wire_parsed = None

    try:
        parse_result = parse_extraction_response(
            raw,
            repair_callback=repair_callback,
            wire_parsed=domain_wire_parsed,
        )
    except ExtractionParseError as exc:
        record.parsed_json = exc.parse_result.parsed_json
        record.schema_validation_errors = exc.parse_result.schema_validation_errors
        record.repair_attempt_count = exc.parse_result.repair_attempts
        record.status = "failed"
        return record

    record.parsed_json = parse_result.parsed_json
    record.schema_validation_errors = parse_result.schema_validation_errors
    record.repair_attempt_count = parse_result.repair_attempts
    record.extraction = parse_result.extraction

    assert parse_result.extraction is not None
    record.citation_audit = audit_entity_citations(parse_result.extraction)
    record.low_confidence_flags = collect_low_confidence_flags(parse_result.extraction)
    record.validation_warnings = collect_validation_warnings(parse_result.extraction)

    # Soft coherence gate: flag collapsed multi-method / factor-table misuse.
    # Promote path re-checks after review edits (hard block).
    coherence = assess_allocation_coherence(parse_result.extraction)
    extraction = apply_coherence_to_extraction(parse_result.extraction, coherence)
    record.extraction = extraction
    record.parsed_json = extraction.model_dump(mode="json")

    if coherence.is_incoherent or (
        record.citation_audit is not None and record.citation_audit.is_partial
    ):
        record.status = "extraction_partial"
    else:
        record.status = "succeeded"
    return record
