"""LLM Pass 2: Holistic sanity check, executive summary, and anomaly flagging."""
import logging
from typing import Any, Optional

from ..models.schemas import LLMPass1Result, LLMPass2Result, LLMPass2FlaggedItem
from ..models.prompts import build_pass2_system_prompt, build_pass2_user_prompt
from ..pipeline.groq_client import call_groq

logger = logging.getLogger(__name__)


def compute_aggregate(
    suggestions: list[LLMPass1Result],
    total_budget: float,
    budget_map: Optional[dict[int, float]] = None,
) -> dict[str, Any]:
    """Compute aggregate budget impact from all Pass 1 suggestions.

    Args:
        suggestions: Pass 1 results with suggested_pct_change as decimals (0.15 = 15%).
        total_budget: Sum of all annual budgets.
        budget_map: account_code → annual_budget for weighted calculation.
                    Falls back to equal-weight average if not provided.
    """
    if not suggestions or total_budget == 0:
        return {
            "total_proposed_change": 0,
            "aggregate_pct_change": 0.0,
            "total_budget": total_budget,
        }

    total_proposed_change = 0.0
    for s in suggestions:
        item_budget = (budget_map or {}).get(s.account_code, total_budget / len(suggestions))
        total_proposed_change += s.suggested_pct_change * item_budget

    aggregate_pct_change = total_proposed_change / total_budget if total_budget else 0.0

    return {
        "total_proposed_change": round(total_proposed_change, 2),
        "aggregate_pct_change": round(aggregate_pct_change, 4),
        "total_budget": total_budget,
        "item_count": len(suggestions),
    }


async def run_pass2(
    all_suggestions: list[LLMPass1Result],
    aggregate: dict[str, Any],
    macro_context: dict[str, Any],
    cbr_results: Optional[dict] = None,
    ml_results: Optional[dict] = None,
) -> Optional[LLMPass2Result]:
    """Run Pass 2: holistic review of all suggestions.

    Returns LLMPass2Result or None on failure (caller uses Pass 1 as-is).
    """
    if not all_suggestions:
        return LLMPass2Result(
            executive_summary="No items to analyze.",
            coherence_score="high",
            flagged_items=[],
        )

    # Convert decimal fractions to percentage points so the LLM can reason in plain English
    # e.g. -0.15 → -15.0  (the LLM sees -15.0% which matches what the UI shows)
    suggestions_data = [
        {
            "account_code": s.account_code,
            "suggested_pct_change": round(s.suggested_pct_change * 100, 2),
            "reason": s.reason,
            "confidence": s.confidence,
        }
        for s in all_suggestions
    ]

    aggregate_context = {**aggregate, **macro_context}
    # aggregate_pct_change is stored as a decimal fraction internally (e.g. -0.000787 = -0.08%)
    # Convert to percentage points so the LLM writes a number consistent with the UI header
    if "aggregate_pct_change" in aggregate_context:
        aggregate_context["aggregate_pct_change"] = round(
            aggregate_context["aggregate_pct_change"] * 100, 3
        )

    system_msg = build_pass2_system_prompt()
    user_msg = build_pass2_user_prompt(suggestions_data, aggregate_context)

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    result = await call_groq(messages, LLMPass2Result)

    if result is None:
        logger.warning("Pass 2 Groq call failed, returning degraded summary")
        return LLMPass2Result(
            executive_summary="AI service unavailable; suggestions based on historical data only.",
            coherence_score="medium",
            flagged_items=[],
        )

    return result


def apply_pass2_revisions(
    pass1_results: list[LLMPass1Result],
    pass2_result: LLMPass2Result,
) -> tuple[list[LLMPass1Result], list[dict]]:
    """Apply Pass 2 revisions to flagged items.

    Returns (updated_results, flagged_items_serialized).
    Items revised by Pass 2 have their suggested_pct_change updated.
    """
    flagged_map = {f.account_code: f for f in pass2_result.flagged_items}
    updated = []

    for suggestion in pass1_results:
        if suggestion.account_code in flagged_map:
            flagged = flagged_map[suggestion.account_code]
            # LLM returns revised_pct_change in percentage points (e.g. -10.0 = -10%)
            # Convert back to decimal fraction for internal use
            revised_decimal = flagged.revised_pct_change / 100.0
            revised_decimal = max(-0.30, min(0.30, revised_decimal))
            original_reason = suggestion.reason
            updated.append(LLMPass1Result(
                account_code=suggestion.account_code,
                suggested_pct_change=revised_decimal,
                reason=f"{original_reason} [Pass 2 revision: {flagged.revised_reason}]",
                confidence=suggestion.confidence * 0.9,
            ))
        else:
            updated.append(suggestion)

    flagged_serialized = [
        {
            "account_code": f.account_code,
            "issue": f.issue,
            "revised_pct_change": f.revised_pct_change,
            "revised_reason": f.revised_reason,
        }
        for f in pass2_result.flagged_items
    ]

    return updated, flagged_serialized
