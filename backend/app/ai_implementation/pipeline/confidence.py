"""Confidence score calculation for pipeline suggestions."""
from typing import Optional


def calculate_confidence(
    cbr_sim: Optional[float],
    ml_baseline: Optional[float],
    llm_suggestion: float,
    llm_confidence: float = 0.65,
) -> float:
    """Calculate final confidence score.

    Case 1: Strong CBR match (sim >= 0.95) → C = min(0.99, cbr_sim)
    Case 2: ML + LLM synthesis → C = 0.90 - (2.0 × |P_base - P_llm|)
    Case 3: Cold start (no CBR/ML) → use LLM's self-reported confidence

    Result clamped to [0.30, 0.99].
    """
    if cbr_sim is not None and cbr_sim >= 0.95:
        # Case 1: Strong CBR match
        confidence = min(0.99, cbr_sim)
    elif ml_baseline is not None:
        # Case 2: ML + LLM synthesis
        divergence = abs(ml_baseline - llm_suggestion)
        confidence = 0.90 - (2.0 * divergence)
    else:
        # Case 3: Cold start — use LLM's self-reported confidence
        confidence = llm_confidence

    return max(0.30, min(0.99, confidence))
