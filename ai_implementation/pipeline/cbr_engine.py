"""Case-Based Reasoning engine implementing the 4R cycle.

Retrieve → Reuse → Revise → Retain
"""
import logging
import math
import sqlite3
from datetime import datetime
from typing import Optional

import numpy as np
from sklearn.preprocessing import MinMaxScaler

from ..config import settings
from ..models.schemas import EnrichedLineItem

logger = logging.getLogger(__name__)


def retrieve_cases(db: sqlite3.Connection) -> tuple[np.ndarray, list[dict]]:
    """Load accepted/modified cases from SQLite within 24-month window.

    Returns:
        (feature_matrix, case_rows) where feature_matrix is shape (n, 10)
        and case_rows has the metadata including user_final_pct_change and created_at.
    """
    cursor = db.execute("""
        SELECT account_level_1, account_level_2, account_level_3,
               adjusted_pct_diff, adjusted_coverage_ratio,
               seasonality_index, normalized_annual_budget,
               is_income, is_reserve, is_admin,
               user_final_pct_change, created_at, id
        FROM feedback_cases
        WHERE user_decision IN ('accepted', 'modified')
          AND created_at > datetime('now', '-24 months')
        ORDER BY created_at DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]

    if not rows:
        return np.empty((0, 10)), []

    feature_matrix = np.array([
        [
            r["account_level_1"] or 0,
            r["account_level_2"] or 0,
            r["account_level_3"] or 0,
            r["adjusted_pct_diff"] or 0.0,
            r["adjusted_coverage_ratio"] or 0.0,
            r["seasonality_index"] or 0.0,
            r["normalized_annual_budget"] or 0.0,
            float(r["is_income"] or 0),
            float(r["is_reserve"] or 0),
            float(r["is_admin"] or 0),
        ]
        for r in rows
    ], dtype=float)

    return feature_matrix, rows


def _normalize_matrix(matrix: np.ndarray) -> np.ndarray:
    """Min-max normalize to [0, 1] range per feature."""
    if matrix.shape[0] == 0:
        return matrix
    scaler = MinMaxScaler()
    return scaler.fit_transform(matrix)


def compute_similarity(
    current_vector: np.ndarray,
    case_matrix: np.ndarray,
) -> np.ndarray:
    """Compute cosine similarity between current vector and all case vectors.

    Returns array of similarity scores in [0, 1] range.
    """
    if case_matrix.shape[0] == 0:
        return np.array([])

    # Stack current + cases for joint normalization
    combined = np.vstack([current_vector.reshape(1, -1), case_matrix])
    normalized = _normalize_matrix(combined)

    current_norm = normalized[0]
    cases_norm = normalized[1:]

    # Cosine similarity
    current_magnitude = np.linalg.norm(current_norm)
    if current_magnitude < 1e-9:
        # After joint normalization, a zero current vector means every feature
        # had zero variance across the combined set (current + cases).  This
        # happens when the current item and *all* cases are feature-identical.
        # In that situation each case is a perfect match (similarity = 1.0),
        # except for cases that still differ in the original space — detect
        # those by comparing pre-normalization vectors directly.
        case_magnitudes = np.linalg.norm(cases_norm, axis=1)
        return np.where(case_magnitudes < 1e-9, 1.0, 0.0)

    case_magnitudes = np.linalg.norm(cases_norm, axis=1)
    case_magnitudes = np.where(case_magnitudes < 1e-9, 1e-10, case_magnitudes)

    similarities = np.dot(cases_norm, current_norm) / (case_magnitudes * current_magnitude)
    return np.clip(similarities, 0.0, 1.0)


def apply_temporal_decay(
    similarities: np.ndarray,
    timestamps: list[str],
    alpha: float = 0.02,
) -> np.ndarray:
    """Apply exponential temporal decay: effective_sim = sim × e^(-α × age_months).

    α=0.02 means a 12-month-old case is reduced by ~21% (e^(-0.24) ≈ 0.786).
    """
    if len(similarities) == 0:
        return similarities

    now = datetime.utcnow()
    decayed = np.zeros_like(similarities)

    for i, (sim, ts_str) in enumerate(zip(similarities, timestamps)):
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", ""))
            age_months = (now - ts).days / 30.44
        except Exception:
            age_months = 0.0
        decay_factor = math.exp(-alpha * age_months)
        decayed[i] = sim * decay_factor

    return decayed


def find_anchor(
    item: EnrichedLineItem,
    db: sqlite3.Connection,
) -> tuple[Optional[float], Optional[float]]:
    """Find CBR anchor for a line item.

    Two-gate approach:
    1. Quality gate: raw similarity >= CBR_THRESHOLD (preserves original quality bar).
    2. Recency gate: among qualifying candidates, pick highest decayed similarity.

    Returns (anchor_pct, raw_similarity) or (None, None).
    """
    from ..pipeline.feature_engineering import build_feature_vector

    case_matrix, case_rows = retrieve_cases(db)
    if case_matrix.shape[0] == 0:
        return None, None

    current_vector = build_feature_vector(item)
    raw_similarities = compute_similarity(current_vector, case_matrix)

    # Gate 1: quality — only consider cases whose raw similarity meets the threshold
    qualifying_mask = raw_similarities >= settings.CBR_THRESHOLD
    if not np.any(qualifying_mask):
        return None, None

    qualifying_indices = np.where(qualifying_mask)[0]
    qualifying_sims = raw_similarities[qualifying_indices]
    qualifying_rows = [case_rows[i] for i in qualifying_indices]
    qualifying_timestamps = [r["created_at"] for r in qualifying_rows]

    # Gate 2: recency — among qualifying candidates, prefer the most recent
    decayed = apply_temporal_decay(qualifying_sims, qualifying_timestamps)
    best_local_idx = int(np.argmax(decayed))

    anchor_pct = qualifying_rows[best_local_idx]["user_final_pct_change"]
    best_sim = float(qualifying_sims[best_local_idx])  # report raw sim, not decayed
    return anchor_pct, best_sim


def revise_anchor(
    anchor: float,
    current_item: EnrichedLineItem,
    matched_item_row: dict,
) -> float:
    """Revise CBR anchor based on feature deltas between current and matched case.

    revision = anchor + (0.3 × delta_pct_diff) + (0.2 × delta_coverage)
    """
    delta_pct_diff = (current_item.adjusted_pct_diff or 0.0) - (matched_item_row.get("adjusted_pct_diff") or 0.0)
    delta_coverage = (current_item.adjusted_coverage_ratio or 0.0) - (matched_item_row.get("adjusted_coverage_ratio") or 0.0)

    revised = anchor + (0.3 * delta_pct_diff) + (0.2 * delta_coverage)
    return max(-0.30, min(0.30, revised))


def retain_feedback(
    case_id: int,
    decision: str,
    final_pct: float,
    note: str,
    db: sqlite3.Connection,
) -> None:
    """UPDATE a feedback_cases row with the user's final decision (Retain step)."""
    from ..database import write_lock
    with write_lock():
        db.execute(
            """UPDATE feedback_cases
               SET user_decision = ?, user_final_pct_change = ?, user_note = ?
               WHERE id = ?""",
            (decision, final_pct, note, case_id),
        )
        db.commit()
    logger.debug(f"Retained case {case_id}: {decision} @ {final_pct:.3f}")
