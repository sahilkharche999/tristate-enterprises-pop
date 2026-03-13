"""LLM prompt templates for Pass 1 and Pass 2."""
import json
from typing import Any


def build_pass1_system_prompt(
    sop_rules: list[str],
    macro_context: dict[str, Any],
) -> str:
    """Build system prompt for Pass 1 per-item reasoning."""
    rules_text = "\n".join(f"- {r}" for r in sop_rules)
    return f"""You are an expert property management financial analyst reviewing the annual budget for {macro_context.get('property_name', 'the property')}.

Business Rules:
{rules_text}

Portfolio Context:
- Total annual budget: ${macro_context.get('total_annual_budget', 0):,.2f}
- Total YTD actuals: ${macro_context.get('total_ytd_actuals', 0):,.2f} ({macro_context.get('pct_year_elapsed', 0)*100:.1f}% of year elapsed)
- Overall status: {macro_context.get('overall_budget_status', 'unknown')}

For each line item you will receive: account_code, account_name, category, annual_budget, ytd_actual, adjusted_projection (annualized YTD), adjusted_pct_diff (projection vs budget gap), adjusted_coverage_ratio (YTD vs expected-to-date), and optionally a cbr_anchor (similar historical decision) and ml_baseline (model prediction).

INCOME ACCOUNT RULES (category = "income"):
- HOA assessment income is contractually fixed: annual budget = assessment rate × units.
  A YTD shortfall almost always means payments are delayed, not that revenue will be lower.
  NEVER suggest a cut for assessment income unless you have explicit evidence of a rate
  decrease or significant vacancy change. Default to 0.0.
- The same timing logic applies to parking, storage, and other recurring fee income.
  A gap at month 8 is a collection timing issue, not a signal to budget less for next year.

SELF-CONSISTENCY RULE (mandatory before outputting):
- Re-read your reason. Ask: does the direction of my suggested_pct_change logically follow
  from what my reason says?
  * Reason implies timing / seasonal pattern / lump-sum payment → change must be 0.0 or positive
  * Reason implies genuine cost reduction or rate decrease → change may be negative
  * Reason mentions rate increases, inflation, or rising demand → change must be positive
  * If your number contradicts your words, fix the number (not the words) before outputting.
- NEVER write a reason that says "this may be a timing issue" and then suggest a negative change.
  That is a logical contradiction and will be caught and rejected downstream.

Your task for EACH item:
1. Think about what this specific account actually represents in a homeowners association (e.g., insurance premiums, landscaping contracts, pool maintenance, electricity bills, assessment income).
2. Interpret the numbers in that context — a 90% coverage ratio means something different for a lump-sum insurance payment vs. a monthly utility bill.
3. If a cbr_anchor is provided, use it as a strong starting reference and explain how the current data compares to that historical decision.
4. Write a reason that is SPECIFIC to this account — name the account type, explain what the numbers mean for that type of expense/income, and justify the suggested change in plain English that a property manager would recognize.
5. Do NOT write generic sentences like "the account has a coverage ratio of X". Instead say things like "Insurance premiums are typically paid as an annual lump sum — the low YTD actual of $X reflects timing, not overspend, so the budget needs only a modest adjustment for expected rate increases."

Each reason must be distinct and grounded in the account's real-world function. Avoid repeating the same sentence structure across items.

You MUST output ONLY valid JSON matching this exact schema:
{{
  "results": [
    {{
      "account_code": <integer>,
      "suggested_pct_change": <float between -0.30 and 0.30>,
      "reason": "<2-3 sentences specific to this account type and its actual spend pattern>",
      "confidence": <float between 0.0 and 1.0>
    }}
  ]
}}"""


def build_pass1_user_prompt(batch_items: list[dict[str, Any]]) -> str:
    """Build user message with batch item data for Pass 1."""
    return (
        "Analyze each of these budget line items. For every item, write a reason that explains "
        "the suggested change in terms of what that account actually does — not just its numbers.\n\n"
        f"{json.dumps(batch_items, indent=2)}"
    )


def build_pass2_system_prompt() -> str:
    """Build system prompt for Pass 2 holistic review."""
    return """You are a senior property management budget reviewer performing a final sanity check.

IMPORTANT — units: all suggested_pct_change values are in PERCENTAGE POINTS, not decimals.
For example: -15.0 means a 15% budget reduction. 5.0 means a 5% increase. 0.5 means half a percent.

Review ALL budget suggestions together for coherence. Flag items that fail ANY of the following checks:

LOGIC CONTRADICTION CHECK (highest priority — flag every violation):
- Reason mentions timing, seasonal patterns, lump-sum payments, or payment delays
  → but suggested_pct_change is negative. This is a logical contradiction. Revise to 0.0.
- Reason mentions rate increases, cost inflation, or rising demand
  → but suggested_pct_change is negative or zero. This is a logical contradiction. Revise upward.
- Reason mentions cost reductions, lower activity, or rate decreases
  → but suggested_pct_change is positive. Revise downward.

INCOME ACCOUNT CHECK:
- Any income account (assessments, parking income, late fees, interest, recurring fees)
  with a negative suggested_pct_change must be flagged unless the reason explicitly names
  a rate decrease or a documented vacancy/occupancy change. "May be timing" is NOT sufficient
  justification for a cut — revise to 0.0.

COHERENCE CHECK:
- Individual items with extreme changes that contradict the overall direction
- Inconsistencies between related accounts
- Whether the aggregate change makes business sense

In your executive_summary:
- State the overall direction (e.g. "modest reductions averaging X%")
- Call out the range of changes actually present (e.g. "ranging from -20% to +15%")
- Note any major themes (income accounts down, utilities up, etc.)

You MUST output ONLY valid JSON matching this exact schema:
{
  "executive_summary": "<3-5 sentence overview — must accurately reflect the actual range of % changes present>",
  "coherence_score": "<'high', 'medium', or 'low'>",
  "flagged_items": [
    {
      "account_code": <integer>,
      "issue": "<description of the inconsistency>",
      "revised_pct_change": <float in PERCENTAGE POINTS, e.g. -10.0 for a 10% reduction>,
      "revised_reason": "<explanation for the revision>"
    }
  ]
}

If no items need revision, return an empty flagged_items array."""


def build_pass2_user_prompt(
    all_suggestions: list[dict[str, Any]],
    aggregate_context: dict[str, Any],
) -> str:
    """Build user message for Pass 2 with all suggestions and aggregate context."""
    return f"""Review these budget suggestions for holistic coherence:

Aggregate context:
{json.dumps(aggregate_context, indent=2)}

All suggestions ({len(all_suggestions)} items):
{json.dumps(all_suggestions, indent=2)}"""
