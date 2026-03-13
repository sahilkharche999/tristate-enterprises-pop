"""Unit tests for confidence score calculation."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_implementation.pipeline.confidence import calculate_confidence


class TestCalculateConfidence:
    def test_strong_cbr_match(self):
        """CBR sim >= 0.95 → C = min(0.99, cbr_sim)."""
        c = calculate_confidence(cbr_sim=0.97, ml_baseline=None, llm_suggestion=0.05)
        assert c == pytest.approx(0.97, abs=0.001)

    def test_strong_cbr_capped_at_99(self):
        """CBR sim = 1.0 → C = 0.99 (capped)."""
        c = calculate_confidence(cbr_sim=1.0, ml_baseline=None, llm_suggestion=0.05)
        assert c == pytest.approx(0.99, abs=0.001)

    def test_ml_llm_agreement(self):
        """ML and LLM closely agree → high confidence."""
        c = calculate_confidence(cbr_sim=None, ml_baseline=0.05, llm_suggestion=0.055)
        assert c > 0.80

    def test_ml_llm_divergence(self):
        """ML and LLM diverge significantly → lower confidence."""
        c = calculate_confidence(cbr_sim=None, ml_baseline=0.20, llm_suggestion=-0.10)
        # C = 0.90 - (2.0 × |0.20 - (-0.10)|) = 0.90 - 0.60 = 0.30
        assert c == pytest.approx(0.30, abs=0.01)

    def test_cold_start(self):
        """No CBR/ML → use LLM self-reported confidence."""
        c = calculate_confidence(cbr_sim=None, ml_baseline=None, llm_suggestion=0.05, llm_confidence=0.65)
        assert c == pytest.approx(0.65, abs=0.01)

    def test_minimum_clamp(self):
        """Result never below 0.30."""
        c = calculate_confidence(cbr_sim=None, ml_baseline=0.30, llm_suggestion=-0.30)
        assert c >= 0.30

    def test_maximum_clamp(self):
        """Result never above 0.99."""
        c = calculate_confidence(cbr_sim=None, ml_baseline=None, llm_suggestion=0.0, llm_confidence=1.0)
        assert c <= 0.99
