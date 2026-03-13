"""Unit tests for feature engineering module."""
import pytest
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_implementation.pipeline.feature_engineering import (
    encode_account_hierarchy,
    get_seasonality_index,
    compute_adjusted_features,
    build_feature_vector,
    enrich_all_items,
)
from ai_implementation.models.schemas import LineItemInput, EnrichedLineItem


class TestEncodeAccountHierarchy:
    def test_electricity_code(self):
        result = encode_account_hierarchy(60000)
        assert result["account_level_1"] == 6
        assert result["account_level_2"] == 600
        assert result["account_level_3"] == 60000
        assert result["is_income"] is False
        assert result["is_reserve"] is False
        assert result["is_admin"] is False

    def test_income_code(self):
        result = encode_account_hierarchy(41170)
        assert result["account_level_1"] == 4
        assert result["is_income"] is True
        assert result["is_reserve"] is False

    def test_reserve_code(self):
        result = encode_account_hierarchy(91000)
        assert result["account_level_1"] == 9
        assert result["is_reserve"] is True

    def test_admin_code(self):
        result = encode_account_hierarchy(55000)
        assert result["account_level_1"] == 5
        assert result["is_admin"] is True

    def test_landscaping_code(self):
        result = encode_account_hierarchy(80000)
        assert result["account_level_1"] == 8
        assert result["account_level_2"] == 800


class TestSeasonalityIndex:
    def test_flat_monthly_all_accounts(self):
        """All accounts now use flat [0.25, 0.25, 0.25, 0.25] per actual HOA statements."""
        # Landscaping (800) at month 1 = 1/3 of Q1 = 0.25/3 ≈ 0.0833
        idx = get_seasonality_index(800, 1)
        assert abs(idx - 0.25 / 3) < 0.001

    def test_utilities_midyear(self):
        """Utilities (600) at month 8 (Aug = Q3 month 2):
        Q1(0.25) + Q2(0.25) + (2/3 * 0.25) = 0.667."""
        idx = get_seasonality_index(600, 8)
        expected = 0.25 + 0.25 + (2 / 3) * 0.25
        assert abs(idx - expected) < 0.001

    def test_reserve_study_flat(self):
        """Reserve study items (910) now use flat seasonality like everything else."""
        idx = get_seasonality_index(910, 8)
        expected = 0.25 + 0.25 + (2 / 3) * 0.25
        assert abs(idx - expected) < 0.001

    def test_default_fallback(self):
        """Unknown account_level_2 uses default [0.25, 0.25, 0.25, 0.25]."""
        idx = get_seasonality_index(999, 6)
        # Month 6 = Q2 month 3: Q1(0.25) + (3/3 * 0.25) = 0.50
        expected = 0.25 + (3 / 3) * 0.25
        assert abs(idx - expected) < 0.001

    def test_year_end(self):
        """Month 12 (Q4 month 3) should give cumulative ≈ 1.0."""
        idx = get_seasonality_index(600, 12)
        assert abs(idx - 1.0) < 0.001


class TestComputeAdjustedFeatures:
    def test_flat_projection_q1(self):
        """With flat seasonality, $3000 YTD at month 1 (index=0.25/3≈0.083) → ~$36,000."""
        seasonality = get_seasonality_index(800, 1)
        result = compute_adjusted_features(3000, 24000.0, seasonality)
        # adjusted_projection = 3000 / (0.25/3) = 36,000
        assert abs(result["adjusted_projection"] - 36000) < 100

    def test_zero_budget(self):
        result = compute_adjusted_features(0, 0, 0.5)
        assert result["adjusted_pct_diff"] == 0.0
        assert result["adjusted_coverage_ratio"] == 0.0

    def test_zero_seasonality(self):
        result = compute_adjusted_features(1000, 24000, 0.0)
        assert result["adjusted_projection"] == 0.0
        assert result["adjusted_pct_diff"] == 0.0


class TestBuildFeatureVector:
    def test_vector_dimension(self):
        item = EnrichedLineItem(
            account_code=60000, account_name="Electricity", label="60000 - Electricity",
            ytd_actual=27000, annual_budget=43200,
            account_level_1=6, account_level_2=600, account_level_3=60000,
            seasonality_index=0.667, adjusted_pct_diff=-0.05, adjusted_coverage_ratio=0.94,
            normalized_annual_budget=0.7,
        )
        vec = build_feature_vector(item)
        assert vec.shape == (10,)

    def test_vector_values(self):
        item = EnrichedLineItem(
            account_code=41170, account_name="Assessments", label="41170 - Assessments",
            ytd_actual=240000, annual_budget=360000,
            account_level_1=4, account_level_2=411, account_level_3=41170,
            is_income=True, is_reserve=False, is_admin=False,
            seasonality_index=0.667, adjusted_pct_diff=0.0, adjusted_coverage_ratio=1.0,
            normalized_annual_budget=1.0,
        )
        vec = build_feature_vector(item)
        assert vec[7] == 1.0  # is_income
        assert vec[8] == 0.0  # is_reserve
        assert vec[9] == 0.0  # is_admin


class TestEnrichAllItems:
    def test_reserve_no_budget_readonly(self):
        """Reserve item with no annual budget → read-only."""
        items = [
            LineItemInput(account_code=91000, account_name="Roof Reserve Study",
                          label="91000 - Roof Reserve Study", ytd_actual=500, annual_budget=0),
        ]
        enriched = enrich_all_items(items, 8)
        assert enriched[0].read_only is True

    def test_reserve_with_budget_not_readonly(self):
        """Reserve item with a real annual budget → NOT read-only (per actual statements)."""
        items = [
            LineItemInput(account_code=91432, account_name="Circulation Pump",
                          label="91432 - Circulation Pump", ytd_actual=72672, annual_budget=109008),
        ]
        enriched = enrich_all_items(items, 8)
        assert enriched[0].read_only is False

    def test_non_reserve_not_readonly(self):
        items = [
            LineItemInput(account_code=60000, account_name="Electricity",
                          label="60000 - Electricity", ytd_actual=27000, annual_budget=43200),
        ]
        enriched = enrich_all_items(items, 8)
        assert enriched[0].read_only is False

    def test_normalized_budget_max_item(self):
        """The item with the highest budget should have normalized_annual_budget = 1.0."""
        items = [
            LineItemInput(account_code=41170, account_name="Assessments",
                          label="41170 - Assessments", ytd_actual=240000, annual_budget=360000),
            LineItemInput(account_code=60000, account_name="Electricity",
                          label="60000 - Electricity", ytd_actual=27000, annual_budget=43200),
        ]
        enriched = enrich_all_items(items, 8)
        max_budget_item = max(enriched, key=lambda x: x.annual_budget)
        assert max_budget_item.normalized_annual_budget == pytest.approx(1.0)
