"""Legal-document page-classification for CC&R / governing-document extraction.

Reuses the batching and filter mechanism from
``dre_extraction.page_classification`` (same PageBatch, classify_pages,
merge_inventory, filter_relevant_pages helpers) but with a governing-
document label set focused on assessment-allocation provisions.

Only pages tagged with an assessment-relevant label are forwarded to the
extraction call — essential because CC&Rs routinely run 90+ pages.
"""

from __future__ import annotations

from app.dre_extraction.page_classification import (  # re-export for convenience
    SINGLE_CALL_PAGE_THRESHOLD,
    DEFAULT_BATCH_SIZE,
    PageBatch,
    ClassificationResult,
    split_pages_into_batches,
    merge_inventory,
    filter_relevant_pages,
    classify_pages,
)

# Legal-document page labels the Step-1 call must choose from.
# Assessment-allocation-relevant labels are those the extraction call needs;
# everything else is filtered out to keep the extraction call compact.
CCR_PAGE_TYPE_LABELS: list[str] = [
    # Assessment-relevant — forwarded to extraction
    "assessment/allocation provisions",  # the apportionment article (e.g. §4.5)
    "special assessment provisions",     # §4.4-style voting / cap clauses
    "definitions",                       # defined terms referenced by allocation clauses
    "exhibit/percentage-interest table", # Exhibit B, interest schedule, sq-ft table
    # Non-assessment — filtered out
    "use restrictions",
    "maintenance responsibilities",
    "condominium plan/floor plan",
    "governance/voting",
    "insurance provisions",
    "enforcement/dispute resolution",
    "signature/notary",
    "table of contents/index",
    "recitals/preamble",
    "blank/irrelevant",
]

# Page types that carry extraction signal for the CC&R allocation policy.
CCR_EXTRACTION_RELEVANT_PAGE_TYPES: frozenset[str] = frozenset(
    {
        "assessment/allocation provisions",
        "special assessment provisions",
        "definitions",
        "exhibit/percentage-interest table",
    }
)


__all__ = [
    "SINGLE_CALL_PAGE_THRESHOLD",
    "DEFAULT_BATCH_SIZE",
    "PageBatch",
    "ClassificationResult",
    "CCR_PAGE_TYPE_LABELS",
    "CCR_EXTRACTION_RELEVANT_PAGE_TYPES",
    "split_pages_into_batches",
    "merge_inventory",
    "filter_relevant_pages",
    "classify_pages",
]
