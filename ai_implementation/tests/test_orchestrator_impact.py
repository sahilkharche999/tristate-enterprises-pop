"""Test that total_budget_impact reflects Pass 2 revisions."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_implementation.pipeline.llm_pass2 import compute_aggregate
from ai_implementation.models.schemas import LLMPass1Result


def make_result(account_code: int, pct: float) -> LLMPass1Result:
    return LLMPass1Result(
        account_code=account_code,
        account_name=f"Item {account_code}",
        suggested_pct_change=pct,
        reason="test",
        confidence=0.9,
    )


def test_aggregate_uses_final_results():
    """Aggregate from final_results must differ from Pass 1 aggregate when Pass 2 revises an item."""
    pass1 = [make_result(1000, 0.10), make_result(2000, 0.05)]
    final = [make_result(1000, 0.03), make_result(2000, 0.05)]  # item 1000 revised down
    budget_map = {1000: 10000.0, 2000: 10000.0}
    total_budget = 20000.0

    agg_pass1 = compute_aggregate(pass1, total_budget, budget_map)
    agg_final = compute_aggregate(final, total_budget, budget_map)

    assert agg_pass1["total_proposed_change"] != agg_final["total_proposed_change"]
    # final: (0.03 * 10000 + 0.05 * 10000) = 800
    assert agg_final["total_proposed_change"] == pytest.approx(800.0, abs=1.0)
