"""Feature engineering for the AI Budget Pipeline.

Transforms raw line items into enriched feature vectors for CBR and CatBoost.
"""
import json
import logging
from pathlib import Path
from typing import Optional
import numpy as np

from ..models.schemas import EnrichedLineItem, LineItemInput

logger = logging.getLogger(__name__)

# Load seasonality weights once at module import
_SEASONALITY_PATH = Path(__file__).parent / "seasonality.json"
_seasonality_weights: dict[str, list[float]] = {}


def _load_seasonality() -> dict[str, list[float]]:
    global _seasonality_weights
    if not _seasonality_weights:
        with open(_SEASONALITY_PATH) as f:
            _seasonality_weights = json.load(f)
    return _seasonality_weights


def encode_account_hierarchy(account_code: int) -> dict:
    """Encode account code into hierarchical features.

    Returns dict with: account_level_1, account_level_2, account_level_3,
    is_income, is_reserve, is_admin
    """
    code_str = str(account_code).zfill(5)
    level_1 = int(code_str[0])
    level_2 = int(code_str[:3])
    level_3 = account_code

    return {
        "account_level_1": level_1,
        "account_level_2": level_2,
        "account_level_3": level_3,
        "is_income": level_1 == 4,
        "is_reserve": level_1 == 9,
        "is_admin": level_1 == 5,
    }


def get_seasonality_index(account_level_2: int, statement_month: int) -> float:
    """Compute cumulative seasonal weight at statement_month.

    Uses mid-quarter linear interpolation:
    cumulative = sum(full prior quarter weights) + (month_in_quarter / 3) × current quarter weight

    Quarter boundaries: Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec
    """
    weights = _load_seasonality()
    key = str(account_level_2)
    quarterly = weights.get(key, weights.get("default", [0.25, 0.25, 0.25, 0.25]))

    # Determine quarter and month within quarter (1-indexed)
    # month 1-3 = Q1 (idx 0), 4-6 = Q2 (idx 1), 7-9 = Q3 (idx 2), 10-12 = Q4 (idx 3)
    quarter_idx = (statement_month - 1) // 3       # 0-based quarter index
    month_in_quarter = ((statement_month - 1) % 3) + 1  # 1, 2, or 3

    # Sum full prior quarters
    cumulative = sum(quarterly[:quarter_idx])
    # Add partial current quarter
    cumulative += (month_in_quarter / 3) * quarterly[quarter_idx]

    return cumulative


def compute_adjusted_features(
    ytd_actual: float,
    annual_budget: float,
    seasonality_index: float,
) -> dict:
    """Compute seasonality-adjusted features."""
    if seasonality_index <= 0:
        return {
            "adjusted_projection": 0.0,
            "adjusted_pct_diff": 0.0,
            "adjusted_coverage_ratio": 0.0,
            "pct_diff": 0.0,
            "coverage_ratio": 0.0,
        }

    adjusted_projection = ytd_actual / seasonality_index

    if annual_budget == 0:
        adjusted_pct_diff = 0.0
        adjusted_coverage_ratio = 0.0
        pct_diff = 0.0
        coverage_ratio = 0.0
    else:
        adjusted_pct_diff = (adjusted_projection - annual_budget) / annual_budget
        adjusted_coverage_ratio = ytd_actual / (annual_budget * seasonality_index)
        pct_diff = (ytd_actual - annual_budget) / annual_budget
        coverage_ratio = ytd_actual / annual_budget

    return {
        "adjusted_projection": adjusted_projection,
        "adjusted_pct_diff": adjusted_pct_diff,
        "adjusted_coverage_ratio": adjusted_coverage_ratio,
        "pct_diff": pct_diff,
        "coverage_ratio": coverage_ratio,
    }


def normalize_budgets(items: list[EnrichedLineItem]) -> list[EnrichedLineItem]:
    """Compute normalized_annual_budget per item (relative to max in run)."""
    budgets = [item.annual_budget for item in items if not item.read_only]
    max_budget = max(budgets) if budgets else 1.0
    if max_budget == 0:
        max_budget = 1.0
    for item in items:
        item.normalized_annual_budget = item.annual_budget / max_budget
    return items


def build_feature_vector(item: EnrichedLineItem) -> np.ndarray:
    """Build 10-dimensional feature vector for CBR/CatBoost.

    Dimensions: [account_level_1, account_level_2, account_level_3,
                 adjusted_pct_diff, adjusted_coverage_ratio,
                 seasonality_index, normalized_annual_budget,
                 is_income, is_reserve, is_admin]
    """
    return np.array([
        item.account_level_1,
        item.account_level_2,
        item.account_level_3,
        item.adjusted_pct_diff,
        item.adjusted_coverage_ratio,
        item.seasonality_index,
        item.normalized_annual_budget,
        float(item.is_income),
        float(item.is_reserve),
        float(item.is_admin),
    ], dtype=float)


def enrich_all_items(
    items: list[LineItemInput],
    statement_month: int,
) -> list[EnrichedLineItem]:
    """Enrich all line items with computed features.

    Reserve study items (account_level_1 == 9) with no annual budget
    are marked read_only and skipped from AI processing. Reserve items
    that have a real budget are processed normally.
    """
    enriched = []
    for item in items:
        hierarchy = encode_account_hierarchy(item.account_code)
        level_2 = hierarchy["account_level_2"]

        seasonality_index = get_seasonality_index(level_2, statement_month)
        is_read_only = hierarchy["is_reserve"] and (item.annual_budget == 0 or item.annual_budget is None)

        if is_read_only:
            enriched.append(EnrichedLineItem(
                **item.model_dump(),
                **hierarchy,
                seasonality_index=seasonality_index,
                read_only=True,
            ))
            continue

        adjusted = compute_adjusted_features(
            ytd_actual=item.ytd_actual,
            annual_budget=item.annual_budget,
            seasonality_index=seasonality_index,
        )

        enriched.append(EnrichedLineItem(
            **item.model_dump(),
            **hierarchy,
            seasonality_index=seasonality_index,
            adjusted_projection=adjusted["adjusted_projection"],
            adjusted_pct_diff=adjusted["adjusted_pct_diff"],
            adjusted_coverage_ratio=adjusted["adjusted_coverage_ratio"],
            pct_diff=adjusted["pct_diff"],
            coverage_ratio=adjusted["coverage_ratio"],
        ))

    enriched = normalize_budgets(enriched)
    return enriched
