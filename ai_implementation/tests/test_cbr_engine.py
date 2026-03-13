"""Unit tests for CBR engine module."""
import math
import sqlite3
import pytest
import numpy as np
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_implementation.pipeline.cbr_engine import (
    compute_similarity,
    apply_temporal_decay,
    find_anchor,
    retrieve_cases,
)
from ai_implementation.models.schemas import EnrichedLineItem


class TestComputeSimilarity:
    def test_identical_vectors(self):
        vec = np.array([1.0, 2.0, 3.0, 0.5, 0.8, 0.6, 0.7, 1.0, 0.0, 0.0])
        matrix = vec.reshape(1, -1)
        sims = compute_similarity(vec, matrix)
        assert sims[0] == pytest.approx(1.0, abs=0.01)

    def test_empty_matrix(self):
        vec = np.array([1.0, 2.0, 3.0, 0.5, 0.8, 0.6, 0.7, 1.0, 0.0, 0.0])
        sims = compute_similarity(vec, np.empty((0, 10)))
        assert len(sims) == 0

    def test_similarity_range(self):
        vec = np.random.rand(10)
        matrix = np.random.rand(5, 10)
        sims = compute_similarity(vec, matrix)
        assert all(0.0 <= s <= 1.0 for s in sims)


class TestTemporalDecay:
    def test_decay_12_months(self):
        """12-month-old case should be reduced by ~21% (e^(-0.02×12) ≈ 0.786)."""
        old_ts = (datetime.utcnow() - timedelta(days=365)).isoformat()
        sims = np.array([1.0])
        decayed = apply_temporal_decay(sims, [old_ts], alpha=0.02)
        expected = math.exp(-0.02 * 12)
        assert abs(decayed[0] - expected) < 0.05

    def test_recent_case_minimal_decay(self):
        """Very recent case should have minimal decay."""
        recent_ts = datetime.utcnow().isoformat()
        sims = np.array([0.97])
        decayed = apply_temporal_decay(sims, [recent_ts], alpha=0.02)
        assert decayed[0] > 0.95

    def test_empty_input(self):
        result = apply_temporal_decay(np.array([]), [], alpha=0.02)
        assert len(result) == 0


class TestFindAnchor:
    def test_empty_db_returns_none(self, db):
        """Empty database should return (None, None) gracefully."""
        item = EnrichedLineItem(
            account_code=60000, account_name="Electricity", label="60000 - Electricity",
            ytd_actual=27000, annual_budget=43200,
            account_level_1=6, account_level_2=600, account_level_3=60000,
            seasonality_index=0.667, adjusted_pct_diff=-0.05, adjusted_coverage_ratio=0.94,
            normalized_annual_budget=0.7,
        )
        anchor, sim = find_anchor(item, db)
        assert anchor is None
        assert sim is None

    def test_anchor_threshold(self, seeded_db):
        """Anchor should only be returned when similarity >= CBR_THRESHOLD (0.95)."""
        item = EnrichedLineItem(
            account_code=60000, account_name="Electricity", label="60000 - Electricity",
            ytd_actual=27000, annual_budget=43200,
            account_level_1=6, account_level_2=600, account_level_3=60000,
            seasonality_index=0.667, adjusted_pct_diff=-0.05, adjusted_coverage_ratio=0.94,
            normalized_annual_budget=0.7,
        )
        anchor, sim = find_anchor(item, seeded_db)
        # If returned, sim must be >= 0.95
        if anchor is not None:
            assert sim >= 0.95


def test_old_perfect_match_not_discarded(db):
    """A perfect raw match older than 2.56 months must still be returned
    after the fix — previously it would be discarded by the decayed threshold."""
    from datetime import datetime, timedelta

    six_months_ago = (datetime.utcnow() - timedelta(days=183)).isoformat()

    # Insert a case whose features are identical to the query item so raw similarity == 1.0.
    # Use created_at = 6 months ago to exercise the temporal-decay path.
    db.execute("""
        INSERT INTO feedback_cases (
            run_id, property_id, account_code, account_name, label, category,
            account_level_1, account_level_2, account_level_3,
            is_income, is_reserve, is_admin,
            annual_budget, ytd_actual,
            adjusted_pct_diff, adjusted_coverage_ratio,
            seasonality_index, normalized_annual_budget,
            user_final_pct_change, user_decision, created_at
        ) VALUES (1, 1, 60000, 'Electricity & Gas', '60000 - Electricity & Gas', 'operating',
                  6, 600, 60000, 0, 0, 0,
                  43200.0, 27000.0,
                  -0.05, 0.94,
                  0.667, 0.7,
                  -0.05, 'accepted', ?)
    """, (six_months_ago,))
    db.commit()

    item = EnrichedLineItem(
        account_code=60000, account_name="Electricity & Gas",
        label="60000 - Electricity & Gas",
        ytd_actual=27000, annual_budget=43200,
        account_level_1=6, account_level_2=600, account_level_3=60000,
        seasonality_index=0.667, adjusted_pct_diff=-0.05,
        adjusted_coverage_ratio=0.94, normalized_annual_budget=0.7,
    )
    anchor, sim = find_anchor(item, db)
    assert anchor is not None, "6-month-old perfect match should not be discarded"
    assert sim is not None
