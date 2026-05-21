"""End-to-end DRE extraction orchestrator.

Pure function — no DB, no I/O. Callbacks inject the live Gemini calls so
the same code is unit-testable with synthetic stubs. The output
``DREExtractionRunRecord`` is the in-memory shape the Phase 1.3
``DREExtractionRun`` DB row will mirror.

Flow:
    1. Step-1 page classification across batches (≤50 pages: one call;
       >50 pages: batched).
    2. Merge per-batch inventory; filter to extraction-relevant pages.
    3. Full Prompt 1 call against the filtered page set.
    4. Single repair retry if JSON or schema validation fails.
    5. Citation audit + low-confidence flag collection +
       validation_check warnings.
    6. Assemble ``DREExtractionRunRecord`` with status set to
       ``succeeded`` / ``extraction_partial`` / ``failed``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional, Union

from .page_classification import (
    PageBatch,
    classify_pages,
)
from .prompts import (
    DRE_SETUP_EXTRACTOR_PROMPT_SHA256,
    DRE_SETUP_EXTRACTOR_PROMPT_VERSION,
)
from .schemas import (
    DRESetupExtraction,
    PageInventoryEntry,
)
from .validation import (
    CitationAudit,
    ExtractionParseError,
    LowConfidenceFlag,
    audit_entity_citations,
    collect_low_confidence_flags,
    collect_validation_warnings,
    parse_extraction_response,
)
from .wire_schemas import WireDRESetupExtraction


RunStatus = Literal["succeeded", "extraction_partial", "failed"]


@dataclass
class DREExtractionRunRecord:
    """In-memory representation of one DRE extraction run.

    Maps directly to a ``DREExtractionRun`` DB row once Phase 1.3
    creates the table. Fields named after the design.md schema:
    ``model_name``, ``prompt_version``, ``prompt_sha256``,
    ``raw_model_output``, ``parsed_json``, ``schema_validation_errors``,
    ``repair_attempt_count``, plus the validation derivatives.
    """

    model_name: str
    prompt_version: str
    prompt_sha256: str

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

    # Audit metadata captured from the Gemini extract call. ``model_name``
    # above is what the caller asked for (alias like 'gemini-flash-latest');
    # ``model_version_resolved`` is what the API actually returned. Empty
    # strings / zero on legacy callers that don't surface this metadata.
    model_version_resolved: str = ""
    finish_reason: str = ""
    output_tokens_used: int = 0

    status: RunStatus = "failed"


ExtractCallbackResult = Union[
    str,
    tuple[str, Optional[WireDRESetupExtraction]],
    tuple[str, Optional[WireDRESetupExtraction], dict],
]


def run_dre_extraction(
    *,
    page_count: int,
    classify_pages_callback: Callable[[PageBatch], list[PageInventoryEntry]],
    extract_setup_callback: Callable[[list[int]], ExtractCallbackResult],
    repair_callback: Optional[Callable[[str, list[str]], str]] = None,
    model_name: str,
    batch_size: Optional[int] = None,
) -> DREExtractionRunRecord:
    """Orchestrate one end-to-end DRE extraction.

    ``classify_pages_callback`` runs Step-1-only of Prompt 1 against
    one ``PageBatch`` and returns its ``page_inventory`` slice.

    ``extract_setup_callback`` runs the full Prompt 1 against the
    filtered list of extraction-relevant page numbers. Two return
    shapes are accepted for backward compatibility:

    * The production callback (``build_extract_callback``) returns a
      ``(raw_text, parsed_wire_instance)`` tuple, where the parsed
      instance comes from the SDK's ``response.parsed`` after the
      structured-output ``response_schema`` is applied. The pipeline
      uses the parsed instance on the happy path and skips JSON
      reparsing.
    * Legacy callers may still return a plain ``str``; the pipeline
      falls back to the text-parsing path in that case.

    ``repair_callback`` (optional) runs the single repair retry when
    the first response fails to parse or schema-validate. With
    structured output it should rarely fire — but it remains the
    belt-and-suspenders fallback. When omitted, a malformed first
    response marks the run as ``failed`` immediately.

    The function never raises on extraction failure — the
    ``DREExtractionRunRecord.status`` field reports outcome:

    - ``succeeded``: extraction parsed, no entity-level citation gaps
    - ``extraction_partial``: extraction parsed but at least one
      entity (group/pool/etc.) is missing its source-page citation
    - ``failed``: parse/schema validation failed after the single
      repair retry; ``schema_validation_errors`` describes why
    """
    record = DREExtractionRunRecord(
        model_name=model_name,
        prompt_version=DRE_SETUP_EXTRACTOR_PROMPT_VERSION,
        prompt_sha256=DRE_SETUP_EXTRACTOR_PROMPT_SHA256,
    )

    # 1–2. Classify and filter pages.
    classification_kwargs = {}
    if batch_size is not None:
        classification_kwargs["batch_size"] = batch_size
    classification = classify_pages(
        page_count,
        classify_pages_callback,
        **classification_kwargs,
    )
    record.page_inventory = classification.full_inventory
    record.relevant_page_numbers = classification.relevant_page_numbers

    # 3–4. Full extraction call + parse with single repair retry.
    callback_out = extract_setup_callback(classification.relevant_page_numbers)
    audit: dict = {}
    if isinstance(callback_out, tuple):
        if len(callback_out) == 3:
            raw, wire_parsed, audit = callback_out  # type: ignore[misc]
        else:
            raw, wire_parsed = callback_out  # type: ignore[misc]
    else:
        raw, wire_parsed = callback_out, None
    record.raw_model_output = raw
    record.model_version_resolved = str(audit.get("model_version", ""))
    record.finish_reason = str(audit.get("finish_reason", ""))
    try:
        record.output_tokens_used = int(audit.get("output_tokens", 0) or 0)
    except (TypeError, ValueError):
        record.output_tokens_used = 0

    try:
        parse_result = parse_extraction_response(
            raw,
            repair_callback=repair_callback,
            wire_parsed=wire_parsed,
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

    # 5. Citation audit + low-confidence flags + validation warnings.
    assert parse_result.extraction is not None  # parse_result.succeeded guarantees this
    record.citation_audit = audit_entity_citations(parse_result.extraction)
    record.low_confidence_flags = collect_low_confidence_flags(parse_result.extraction)
    record.validation_warnings = collect_validation_warnings(parse_result.extraction)

    # 6. Set status based on citation completeness.
    record.status = (
        "extraction_partial" if record.citation_audit.is_partial else "succeeded"
    )
    return record
