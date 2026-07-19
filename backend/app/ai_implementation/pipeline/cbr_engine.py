"""Case-Based Reasoning engine implementing the 4R cycle.

Retrieve → Reuse → Revise → Retain
"""
import logging
import math
from datetime import datetime
from typing import Optional

import numpy as np
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ...config import settings
from ..db.models import FeedbackCase, DECIDED_STATUSES, FEATURE_COLUMNS
from ..models.schemas import EnrichedLineItem

logger = logging.getLogger(__name__)


def retrieve_cases(session: Session) -> tuple[np.ndarray, list[dict]]:
    """Load accepted/modified cases from SQLite within 24-month window.

    Returns:
        (feature_matrix, case_rows) where feature_matrix is shape (n, 10)
        and case_rows has the metadata including user_final_pct_change and created_at.
    """
    stmt = (
        select(
            FeedbackCase.account_level_1, FeedbackCase.account_level_2,
            FeedbackCase.account_level_3, FeedbackCase.adjusted_pct_diff,
            FeedbackCase.adjusted_coverage_ratio, FeedbackCase.seasonality_index,
            FeedbackCase.normalized_annual_budget, FeedbackCase.is_income,
            FeedbackCase.is_reserve, FeedbackCase.is_admin,
            FeedbackCase.user_final_pct_change, FeedbackCase.created_at, FeedbackCase.id,
        )
        .where(FeedbackCase.user_decision.in_(DECIDED_STATUSES))
        .where(FeedbackCase.created_at > func.datetime("now", "-24 months"))
        .order_by(FeedbackCase.created_at.desc())
        .limit(1000)
    )
    rows_raw = session.execute(stmt).all()

    if not rows_raw:
        return np.empty((0, 10)), []

    rows = [
        {
            "account_level_1": r[0], "account_level_2": r[1], "account_level_3": r[2],
            "adjusted_pct_diff": r[3], "adjusted_coverage_ratio": r[4],
            "seasonality_index": r[5], "normalized_annual_budget": r[6],
            "is_income": r[7], "is_reserve": r[8], "is_admin": r[9],
            "user_final_pct_change": r[10], "created_at": r[11], "id": r[12],
        }
        for r in rows_raw
    ]

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
    """Min-max normalize to [0, 1] range per feature.

    Zero-range columns become zeros (sklearn MinMaxScaler parity), not NaN.
    """
    if matrix.shape[0] == 0:
        return matrix
    col_min = matrix.min(axis=0)
    col_max = matrix.max(axis=0)
    denom = col_max - col_min
    # Avoid divide-by-zero: zero-range features → all zeros after normalize
    safe_denom = np.where(denom == 0, 1.0, denom)
    normalized = (matrix - col_min) / safe_denom
    normalized = np.where(denom == 0, 0.0, normalized)
    return normalized


def compute_similarity(
    current_vector: np.ndarray,
    case_matrix: np.ndarray,
) -> np.ndarray:
    """Compute cosine similarity between current vector and all case vectors."""
    if case_matrix.shape[0] == 0:
        return np.array([])

    combined = np.vstack([current_vector.reshape(1, -1), case_matrix])
    normalized = _normalize_matrix(combined)

    current_norm = normalized[0]
    cases_norm = normalized[1:]

    current_magnitude = np.linalg.norm(current_norm)
    if current_magnitude < 1e-9:
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
    """Apply exponential temporal decay: effective_sim = sim × e^(-α × age_months)."""
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
    case_matrix: np.ndarray,
    case_rows: list[dict],
) -> tuple[Optional[float], Optional[float]]:
    """Find CBR anchor for a line item using pre-loaded cases.

    Two-gate approach:
    1. Quality gate: raw similarity >= CBR_THRESHOLD.
    2. Recency gate: among qualifying candidates, pick highest decayed similarity.

    Returns (anchor_pct, raw_similarity) or (None, None).
    """
    from ..pipeline.feature_engineering import build_feature_vector

    if case_matrix.shape[0] == 0:
        return None, None

    current_vector = build_feature_vector(item)
    raw_similarities = compute_similarity(current_vector, case_matrix)

    qualifying_mask = raw_similarities >= settings.CBR_THRESHOLD
    if not np.any(qualifying_mask):
        return None, None

    qualifying_indices = np.where(qualifying_mask)[0]
    qualifying_sims = raw_similarities[qualifying_indices]
    qualifying_rows = [case_rows[i] for i in qualifying_indices]
    qualifying_timestamps = [r["created_at"] for r in qualifying_rows]

    decayed = apply_temporal_decay(qualifying_sims, qualifying_timestamps)
    best_local_idx = int(np.argmax(decayed))

    anchor_pct = qualifying_rows[best_local_idx]["user_final_pct_change"]
    best_sim = float(qualifying_sims[best_local_idx])
    return anchor_pct, best_sim


def retain_feedback(
    case_id: int,
    decision: str,
    final_pct: float,
    note: str,
    session: Session,
) -> None:
    """UPDATE a feedback_cases row with the user's final decision (Retain step).

    NOTE: Caller is responsible for committing the session.
    """
    case = session.get(FeedbackCase, case_id)
    if case:
        case.user_decision = decision
        case.user_final_pct_change = final_pct
        case.user_note = note
    logger.debug(f"Retained case {case_id}: {decision} @ {final_pct:.3f}")
