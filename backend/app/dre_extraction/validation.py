"""Defense-in-depth checks on Gemini extraction output.

Even though Prompt 1 instructs the model to follow these rules, the
extraction handler enforces them programmatically — never trust a
generative model with a money-affecting promise. Each check returns
structured warnings/errors that ``DREExtractionRun.warnings_json`` and
the Review Workbench consume.

Rules implemented:

1. **JSON parses + schema validates** — handled by
   ``parse_extraction_response`` (single repair retry; persistent
   failure marks the run as failed).
2. **Entity-level page citation** — every extracted entity (document
   metadata, group rows, unit rows, allocation pools, formulas,
   reserve setup) must cite at least one source page. Missing citations
   flag the run as ``extraction_partial`` rather than ``succeeded``.
3. **Low-confidence flagging** — fields with ``confidence < 0.7``
   surface in the Review Workbench requiring operator confirmation.
4. **DRE value preservation** — ``validation_checks[].status == 'fail'``
   never triggers auto-correction; the DRE row's value is kept verbatim
   and a warning is emitted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from pydantic import BaseModel, ValidationError

from . import wire_to_domain
from .schemas import DRESetupExtraction


LOW_CONFIDENCE_THRESHOLD: float = 0.7


# -- 1. JSON parse + schema validation -------------------------------------


@dataclass
class ParseResult:
    """Outcome of a single parse attempt."""

    extraction: Optional[DRESetupExtraction]
    raw_text: str
    parsed_json: Optional[dict]
    schema_validation_errors: list[str]
    repair_attempts: int

    @property
    def succeeded(self) -> bool:
        return self.extraction is not None


class ExtractionParseError(Exception):
    """Raised after the single repair retry fails. ``DREExtractionRun.status``
    is set to ``failed`` and the operator sees a pages-need-review screen.
    """

    def __init__(self, parse_result: ParseResult) -> None:
        self.parse_result = parse_result
        super().__init__(
            f"Extraction parse failed after {parse_result.repair_attempts} "
            "attempt(s); see parse_result.schema_validation_errors."
        )


def parse_extraction_response(
    raw_text: str,
    *,
    repair_callback: Optional[Callable[[str, list[str]], str]] = None,
    wire_parsed: Optional[Any] = None,
    wire_schema: Optional[type[BaseModel]] = None,
    wire_to_domain_fn: Optional[Callable[[Any], DRESetupExtraction]] = None,
) -> ParseResult:
    """Parse a Gemini response into a ``DRESetupExtraction``.

    When ``wire_parsed`` is supplied (the structured-output happy
    path), the function takes a fast path: the typed wire instance is
    converted to the domain shape via the supplied adapter (or the DRE
    adapter by default) and no JSON re-parse + Pydantic re-validation
    happens. This is the byte-for-byte equivalent of the SDK's
    ``response.parsed`` value, so the result is guaranteed to match
    ``raw_text``.

    CCR callers may supply ``wire_schema`` and ``wire_to_domain_fn``.
    Their fallback and repair responses are then validated against the
    CCR wire schema instead of being permissively parsed as DRE domain
    JSON.

    When ``wire_parsed`` is ``None`` (legacy callers, or the rare
    case where the SDK fails to parse a structured response), the
    function falls back to the text-parsing path: JSON-decode +
    Pydantic validation, with a single repair retry via
    ``repair_callback`` if either step fails.

    Raises ``ExtractionParseError`` if both attempts fail.
    """
    adapter = wire_to_domain_fn or wire_to_domain.to_domain
    if wire_parsed is not None:
        domain = adapter(wire_parsed)
        return ParseResult(
            extraction=domain,
            raw_text=raw_text,
            parsed_json=domain.model_dump(mode="json"),
            schema_validation_errors=[],
            repair_attempts=0,
        )

    result = _attempt_parse(
        raw_text,
        repair_attempts=0,
        wire_schema=wire_schema,
        wire_to_domain_fn=wire_to_domain_fn,
    )
    if result.succeeded:
        return result

    if repair_callback is None:
        raise ExtractionParseError(result)

    repaired_text = repair_callback(raw_text, result.schema_validation_errors)
    result = _attempt_parse(
        repaired_text,
        repair_attempts=1,
        wire_schema=wire_schema,
        wire_to_domain_fn=wire_to_domain_fn,
    )
    if result.succeeded:
        return result

    raise ExtractionParseError(result)


def _attempt_parse(
    raw_text: str,
    *,
    repair_attempts: int,
    wire_schema: Optional[type[BaseModel]] = None,
    wire_to_domain_fn: Optional[Callable[[Any], DRESetupExtraction]] = None,
) -> ParseResult:
    errors: list[str] = []
    parsed: Optional[dict] = None
    extraction: Optional[DRESetupExtraction] = None

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        errors.append(f"JSONDecodeError: {exc}")
        return ParseResult(
            extraction=None,
            raw_text=raw_text,
            parsed_json=None,
            schema_validation_errors=errors,
            repair_attempts=repair_attempts,
        )

    try:
        if wire_schema is None:
            extraction = DRESetupExtraction.model_validate(parsed)
        else:
            if wire_to_domain_fn is None:
                raise TypeError(
                    "wire_to_domain_fn is required when wire_schema is supplied"
                )
            wire = wire_schema.model_validate(parsed)
            extraction = wire_to_domain_fn(wire)
    except (TypeError, ValueError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            errors.extend(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            )
        else:
            errors.append(f"{type(exc).__name__}: {exc}")

    return ParseResult(
        extraction=extraction,
        raw_text=raw_text,
        parsed_json=parsed,
        schema_validation_errors=errors,
        repair_attempts=repair_attempts,
    )


# -- 2. Entity-level page citation -----------------------------------------


@dataclass
class CitationAudit:
    """Per-entity missing-citation report. Per prompts.md §"Universal rules"
    rule #2, a run with any missing entity-level citation is marked
    ``extraction_partial`` (not rejected outright)."""

    missing_citations: list[str] = field(default_factory=list)

    @property
    def is_partial(self) -> bool:
        return bool(self.missing_citations)


def audit_entity_citations(extraction: DRESetupExtraction) -> CitationAudit:
    """Check that every extracted entity cites at least one source page.

    Entity-level requirement (per prompts.md):
    - ``document_metadata.source_pages`` non-empty
    - each ``unit_structure.groups[i].source_page`` set
    - each ``unit_structure.units[i].source_page`` set
    - each ``allocation_pools[i].source_pages`` non-empty
    - each ``formulas[i].source_page`` set
    - ``reserve_setup.source_pages`` non-empty (only if reserve_setup present)
    """
    audit = CitationAudit()

    if not extraction.document_metadata.source_pages:
        audit.missing_citations.append("document_metadata.source_pages")

    for i, g in enumerate(extraction.unit_structure.groups):
        if g.source_page is None:
            audit.missing_citations.append(
                f"unit_structure.groups[{i}] ({g.label or g.group_id}).source_page"
            )

    for i, u in enumerate(extraction.unit_structure.units):
        if u.source_page is None:
            audit.missing_citations.append(
                f"unit_structure.units[{i}] ({u.unit_number}).source_page"
            )

    for i, p in enumerate(extraction.allocation_pools):
        if not p.source_pages:
            audit.missing_citations.append(
                f"allocation_pools[{i}] ({p.pool_key}).source_pages"
            )

    for i, f in enumerate(extraction.formulas):
        if f.source_page is None:
            audit.missing_citations.append(
                f"formulas[{i}] ({f.formula_name}).source_page"
            )

    if extraction.reserve_setup and not extraction.reserve_setup.source_pages:
        audit.missing_citations.append("reserve_setup.source_pages")

    return audit


# -- 3. Low-confidence flagging --------------------------------------------


@dataclass
class LowConfidenceFlag:
    """One field flagged for operator confirmation."""

    path: str
    confidence: float


def collect_low_confidence_flags(
    extraction: DRESetupExtraction,
    *,
    threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> list[LowConfidenceFlag]:
    """Surface every entity whose ``confidence`` is below ``threshold``.

    The Review Workbench shows these with an explicit "low-confidence"
    badge; the operator must confirm or correct each one before
    approving the draft.
    """
    flags: list[LowConfidenceFlag] = []

    if extraction.document_metadata.confidence < threshold:
        flags.append(LowConfidenceFlag(
            path="document_metadata",
            confidence=extraction.document_metadata.confidence,
        ))

    if extraction.assessment_setup.confidence < threshold:
        flags.append(LowConfidenceFlag(
            path="assessment_setup",
            confidence=extraction.assessment_setup.confidence,
        ))

    for i, g in enumerate(extraction.unit_structure.groups):
        if g.confidence < threshold:
            flags.append(LowConfidenceFlag(
                path=f"unit_structure.groups[{i}] ({g.label or g.group_id})",
                confidence=g.confidence,
            ))

    for i, u in enumerate(extraction.unit_structure.units):
        if u.confidence < threshold:
            flags.append(LowConfidenceFlag(
                path=f"unit_structure.units[{i}] ({u.unit_number})",
                confidence=u.confidence,
            ))

    for i, p in enumerate(extraction.allocation_pools):
        if p.confidence < threshold:
            flags.append(LowConfidenceFlag(
                path=f"allocation_pools[{i}] ({p.pool_key})",
                confidence=p.confidence,
            ))

    for i, f in enumerate(extraction.formulas):
        if f.confidence < threshold:
            flags.append(LowConfidenceFlag(
                path=f"formulas[{i}] ({f.formula_name})",
                confidence=f.confidence,
            ))

    if extraction.reserve_setup and extraction.reserve_setup.confidence < threshold:
        flags.append(LowConfidenceFlag(
            path="reserve_setup",
            confidence=extraction.reserve_setup.confidence,
        ))

    return flags


# -- 4. DRE value preservation on validation failure -----------------------


def collect_validation_warnings(extraction: DRESetupExtraction) -> list[str]:
    """Emit one warning string per ``validation_checks`` entry whose
    status is ``fail``. The engine NEVER auto-corrects; the DRE value
    flows through unchanged with a warning record for audit.
    """
    return [
        f"validation_check '{c.check_name}' failed: {c.details} "
        f"(pages: {c.source_pages or []})"
        for c in extraction.validation_checks
        if c.status == "fail"
    ]
