"""Page-classification batching for large DREs.

Prompt 1 has a Step-1-only "page classification" phase: every page is
tagged with a ``page_type`` (cover/unit summary/proration schedule/
…etc.). For DREs ≤ 50 pages we send the full prompt in one call; for
larger DREs we batch Step-1 across multiple calls (10–20 pages each),
merge the resulting ``page_inventory``, filter to extraction-relevant
pages, and then run the full prompt on the filtered subset.

This module provides pure-function batching helpers; the Gemini call
itself is injected via callback so this module is unit-testable
without an API key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .schemas import PageInventoryEntry


SINGLE_CALL_PAGE_THRESHOLD: int = 50
DEFAULT_BATCH_SIZE: int = 15

# Page types that carry extraction signal. Everything else can be
# filtered out before the full extraction call to keep input compact.
EXTRACTION_RELEVANT_PAGE_TYPES: frozenset[str] = frozenset(
    {
        "unit summary",
        "annual operating budget",
        "budget assumptions",
        "proration schedule",
        "assessment schedule",
        "budget components",
        "cost sharing fee schedule",
        "parking schedule",
        "residential/common/commercial pool schedule",
        "reserve study",
        "reserve worksheet",
        "projected expenditures",
        "projected balances",
        "component quantification",
        "state calculation/exhibit",
        "subdivision identification",
        # Prompt v2.1: new pool-bearing / pool-anchor page types.
        # Adding them here ensures classify-tagged pages reach the
        # extract call instead of being silently filtered out.
        "supplemental budget",
        "commercial common budget",
        "limited common budget",
        "master association budget",
        "per-pool reserve study",
        "per-unit assessment schedule",
        # Prompt v2.2: component-extraction anchor pages.
        "proportionate budget components",
        "per-unit factor table",
    }
)


@dataclass
class PageBatch:
    """One batch of page numbers to send to Gemini in a Step-1 call."""

    page_numbers: list[int]

    def __len__(self) -> int:  # convenience
        return len(self.page_numbers)


@dataclass
class ClassificationResult:
    """Merged ``page_inventory`` plus the filtered subset of pages that
    should feed the full extraction call."""

    full_inventory: list[PageInventoryEntry] = field(default_factory=list)
    relevant_page_numbers: list[int] = field(default_factory=list)


def split_pages_into_batches(
    page_count: int,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[PageBatch]:
    """Split a page count into Step-1 batches.

    Returns a single batch covering all pages when below the single-call
    threshold (the caller can then skip the batching path entirely).
    """
    if page_count <= 0:
        return []
    if page_count <= SINGLE_CALL_PAGE_THRESHOLD:
        return [PageBatch(page_numbers=list(range(1, page_count + 1)))]
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    batches: list[PageBatch] = []
    start = 1
    while start <= page_count:
        end = min(start + batch_size - 1, page_count)
        batches.append(PageBatch(page_numbers=list(range(start, end + 1))))
        start = end + 1
    return batches


def merge_inventory(
    batch_inventories: Sequence[Sequence[PageInventoryEntry]],
) -> list[PageInventoryEntry]:
    """Merge per-batch ``page_inventory`` lists into one ordered list.

    Later batches don't overwrite earlier entries for the same page —
    Step-1 batches are non-overlapping by construction. Output is
    sorted by ``page_number`` for stability.
    """
    by_page: dict[int, PageInventoryEntry] = {}
    for batch in batch_inventories:
        for entry in batch:
            if entry.page_number not in by_page:
                by_page[entry.page_number] = entry
    return sorted(by_page.values(), key=lambda e: e.page_number)


def filter_relevant_pages(
    inventory: Sequence[PageInventoryEntry],
    *,
    relevant_types: frozenset[str] = EXTRACTION_RELEVANT_PAGE_TYPES,
) -> list[int]:
    """Return page numbers whose ``page_type`` is extraction-relevant.

    Page-type matching is case-insensitive and trimmed; everything else
    (cover pages, blanks, signatures, …) is dropped from the filtered
    subset so the full-extraction call sees only meaningful content.
    """
    relevant_lower = {t.lower() for t in relevant_types}
    return [
        e.page_number
        for e in inventory
        if e.page_type.strip().lower() in relevant_lower
    ]


def classify_pages(
    page_count: int,
    classify_batch_callback: Callable[[PageBatch], list[PageInventoryEntry]],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    relevant_types: Optional[frozenset[str]] = None,
) -> ClassificationResult:
    """Drive the Step-1 classification across batches.

    ``classify_batch_callback`` is the function that sends one batch to
    Gemini and returns its ``page_inventory`` slice. Injecting it keeps
    this module pure for unit testing.
    """
    batches = split_pages_into_batches(page_count, batch_size=batch_size)
    batch_inventories = [classify_batch_callback(b) for b in batches]
    merged = merge_inventory(batch_inventories)
    rel_pages = filter_relevant_pages(
        merged,
        relevant_types=relevant_types or EXTRACTION_RELEVANT_PAGE_TYPES,
    )
    return ClassificationResult(
        full_inventory=merged,
        relevant_page_numbers=rel_pages,
    )
