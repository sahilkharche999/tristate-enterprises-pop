"""LLM Pass 1: Per-item budget suggestion via 3 concurrent Groq batches."""
import asyncio
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import SOPRule
from ..models.schemas import EnrichedLineItem, LLMPass1Result
from ..models.prompts import build_pass1_system_prompt, build_pass1_user_prompt
from ..pipeline.groq_client import call_groq
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Pass1BatchResult(BaseModel):
    results: list[LLMPass1Result]


def split_into_batches(items: list[EnrichedLineItem], n: int = 3) -> list[list[EnrichedLineItem]]:
    """Split items into n roughly equal batches."""
    if not items:
        return [[] for _ in range(n)]
    size = max(1, len(items) // n)
    batches = []
    for i in range(n):
        start = i * size
        end = start + size if i < n - 1 else len(items)
        if start < len(items):
            batches.append(items[start:end])
        else:
            batches.append([])
    return batches


def _item_to_dict(
    item: EnrichedLineItem,
    cbr_results: dict[int, tuple[Optional[float], Optional[float]]],
    ml_results: dict[int, float],
) -> dict[str, Any]:
    """Serialize enriched item with CBR/ML context for LLM prompt."""
    cbr_anchor, cbr_sim = cbr_results.get(item.account_code, (None, None))
    ml_baseline = ml_results.get(item.account_code)
    return {
        "account_code": item.account_code,
        "account_name": item.account_name,
        "category": item.category,
        "annual_budget": item.annual_budget,
        "ytd_actual": item.ytd_actual,
        "adjusted_projection": item.adjusted_projection,
        "adjusted_pct_diff": round(item.adjusted_pct_diff, 4),
        "adjusted_coverage_ratio": round(item.adjusted_coverage_ratio, 4),
        "cbr_anchor": cbr_anchor,
        "ml_baseline": ml_baseline,
    }


def _fallback_result(item: EnrichedLineItem, cbr_results: dict, ml_results: dict) -> LLMPass1Result:
    """Return a fallback suggestion when Groq is unavailable."""
    cbr_anchor, cbr_sim = cbr_results.get(item.account_code, (None, None))
    ml_baseline = ml_results.get(item.account_code)
    pct = cbr_anchor if cbr_anchor is not None else (ml_baseline if ml_baseline is not None else 0.0)

    coverage = item.adjusted_coverage_ratio
    variance_pct = item.adjusted_pct_diff * 100
    if cbr_anchor is not None and cbr_sim is not None:
        reason = (
            f"{item.account_name} is tracking at {coverage:.0%} of expected YTD spend "
            f"(projected {variance_pct:+.1f}% vs budget). "
            f"A similar historical case recommended {cbr_anchor*100:+.1f}% — "
            f"this suggestion follows that precedent (similarity {cbr_sim:.2f})."
        )
    elif abs(variance_pct) < 5:
        reason = (
            f"{item.account_name} is on track at {coverage:.0%} of expected YTD spend "
            f"with only a {variance_pct:+.1f}% projected variance. "
            f"A minimal adjustment keeps the budget aligned with current spending."
        )
    elif variance_pct < 0:
        reason = (
            f"{item.account_name} is running {abs(variance_pct):.1f}% below budget on an annualized basis "
            f"(coverage ratio {coverage:.2f}). "
            f"A reduction reflects the lower-than-expected spend rate observed this year."
        )
    else:
        reason = (
            f"{item.account_name} is tracking {variance_pct:.1f}% above budget on an annualized basis "
            f"(coverage ratio {coverage:.2f}). "
            f"An increase is warranted to reflect the higher observed spend rate."
        )

    return LLMPass1Result(
        account_code=item.account_code,
        suggested_pct_change=max(-0.30, min(0.30, pct)),
        reason=reason,
        confidence=0.40,
    )


async def process_batch(
    batch: list[EnrichedLineItem],
    macro_context: dict[str, Any],
    sop_rules: list[str],
    cbr_results: dict[int, tuple[Optional[float], Optional[float]]],
    ml_results: dict[int, float],
) -> list[LLMPass1Result]:
    """Process a single batch of items through Groq Pass 1."""
    if not batch:
        return []

    items_data = [_item_to_dict(item, cbr_results, ml_results) for item in batch]
    system_msg = build_pass1_system_prompt(sop_rules, macro_context)
    user_msg = build_pass1_user_prompt(items_data)

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    result = await call_groq(messages, Pass1BatchResult)

    if result is None:
        logger.warning(f"Groq Pass 1 failed for batch of {len(batch)} items, using fallback")
        return [_fallback_result(item, cbr_results, ml_results) for item in batch]

    result_map = {r.account_code: r for r in result.results}
    final = []
    for item in batch:
        if item.account_code in result_map:
            final.append(result_map[item.account_code])
        else:
            final.append(_fallback_result(item, cbr_results, ml_results))

    return final


async def run_pass1(
    enriched_items: list[EnrichedLineItem],
    cbr_results: dict[int, tuple[Optional[float], Optional[float]]],
    ml_results: dict[int, float],
    macro_context: dict[str, Any],
    session: Session,
) -> list[LLMPass1Result]:
    """Run Pass 1: 3 async batches via asyncio.gather().

    Loads SOP rules from database, dispatches 3 concurrent Groq calls.
    """
    sop_rules = list(session.scalars(
        select(SOPRule.rule_text).where(SOPRule.active == 1).order_by(SOPRule.id)
    ))

    active_items = [item for item in enriched_items if not item.read_only]

    if not active_items:
        return []

    batches = split_into_batches(active_items, n=3)
    logger.info(f"Pass 1: {len(active_items)} items split into {len(batches)} batches")

    tasks = [
        process_batch(batch, macro_context, sop_rules, cbr_results, ml_results)
        for batch in batches
    ]
    batch_results = await asyncio.gather(*tasks)

    all_results = []
    for batch_result in batch_results:
        all_results.extend(batch_result)

    logger.info(f"Pass 1 complete: {len(all_results)} suggestions generated")
    return all_results
