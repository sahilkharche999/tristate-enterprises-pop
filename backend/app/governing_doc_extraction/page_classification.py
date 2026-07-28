"""Legal-document page-classification for CC&R / governing-document extraction.

Reuses the batching and filter mechanism from
``dre_extraction.page_classification`` (same PageBatch, classify_pages,
merge_inventory, filter_relevant_pages helpers) but with a governing-
document label set focused on assessment-allocation provisions.

Only pages tagged with an assessment-relevant label are forwarded to the
extraction call — essential because CC&Rs routinely run 90+ pages.
"""

from __future__ import annotations

from typing import Sequence

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
from app.dre_extraction.schemas import PageInventoryEntry

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

# Neighbor expansion seeds only assessment-family pages (not definitions or
# exhibits). Assessment articles routinely continue across a page break; if
# the continuation is mislabeled, ±1 re-admits it without bloating extract
# from long definition runs or condo-plan neighbors of exhibit tables.
CCR_NEIGHBOR_SEED_PAGE_TYPES: frozenset[str] = frozenset(
    {
        "assessment/allocation provisions",
        "special assessment provisions",
    }
)


def expand_relevant_pages_with_neighbors(
    inventory: Sequence[PageInventoryEntry],
    relevant_page_numbers: Sequence[int],
    *,
    page_count: int,
    seed_types: frozenset[str] = CCR_NEIGHBOR_SEED_PAGE_TYPES,
    radius: int = 1,
) -> list[int]:
    """Union relevant pages with ±radius neighbors of assessment-family pages.

    Pure / HOA-agnostic. Does not invent pages outside ``1..page_count``.
    Definitions and exhibit pages stay in the set when already relevant but
    do not seed neighbor expansion.
    """
    if page_count < 0:
        raise ValueError(f"page_count must be >= 0, got {page_count}")
    if radius < 0:
        raise ValueError(f"radius must be >= 0, got {radius}")

    expanded: set[int] = {
        int(p) for p in relevant_page_numbers if 1 <= int(p) <= page_count
    }
    seed_lower = {t.strip().lower() for t in seed_types}
    for entry in inventory:
        page_type = (entry.page_type or "").strip().lower()
        if page_type not in seed_lower:
            continue
        p = int(entry.page_number)
        for n in range(p - radius, p + radius + 1):
            if 1 <= n <= page_count:
                expanded.add(n)
    return sorted(expanded)


__all__ = [
    "SINGLE_CALL_PAGE_THRESHOLD",
    "DEFAULT_BATCH_SIZE",
    "PageBatch",
    "ClassificationResult",
    "CCR_PAGE_TYPE_LABELS",
    "CCR_EXTRACTION_RELEVANT_PAGE_TYPES",
    "CCR_NEIGHBOR_SEED_PAGE_TYPES",
    "expand_relevant_pages_with_neighbors",
    "split_pages_into_batches",
    "merge_inventory",
    "filter_relevant_pages",
    "classify_pages",
]
