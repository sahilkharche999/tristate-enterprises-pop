"""CatBoost ML model: training, inference, and activation management."""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..config import settings
from ..db.models import FeedbackCase, DECIDED_STATUSES, FEATURE_COLUMNS, MAX_PCT_CHANGE
from ..models.schemas import EnrichedLineItem

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).parent.parent / "data" / "catboost_model"
_META_PATH = _MODEL_DIR / "meta.json"


def should_activate(session: Session) -> bool:
    """Return True if CatBoost should run. Disabled during client testing phase.
    To re-enable: set CATBOOST_ENABLED=true in environment variables."""
    if not settings.CATBOOST_ENABLED:
        return False
    count = session.scalar(
        select(func.count()).select_from(FeedbackCase)
        .where(FeedbackCase.user_decision.in_(DECIDED_STATUSES))
    )
    return (count or 0) >= settings.CATBOOST_MIN_CASES


def load_training_data(session: Session) -> tuple:
    """Load training features and target from SQLite.

    Returns (features_array, targets_array, feature_names).
    """
    stmt = (
        select(FeedbackCase)
        .where(FeedbackCase.user_decision.in_(DECIDED_STATUSES))
        .where(FeedbackCase.user_final_pct_change.isnot(None))
        .where(FeedbackCase.created_at > func.datetime("now", "-24 months"))
    )
    cases = session.execute(stmt).scalars().all()

    if not cases:
        return np.empty((0, 10)), np.empty(0), FEATURE_COLUMNS

    # Column order matches FEATURE_COLUMNS (same as CBR and feature_engineering)
    features = np.array([[
        c.account_level_1 or 0, c.account_level_2 or 0, c.account_level_3 or 0,
        c.adjusted_pct_diff or 0.0, c.adjusted_coverage_ratio or 0.0,
        c.seasonality_index or 0.0, c.normalized_annual_budget or 0.0,
        float(c.is_income or 0), float(c.is_reserve or 0), float(c.is_admin or 0),
    ] for c in cases], dtype=float)

    targets = np.array([c.user_final_pct_change for c in cases], dtype=float)

    return features, targets, FEATURE_COLUMNS


def train_model(session: Session) -> Optional[object]:
    """Train CatBoost model and save to disk."""
    try:
        from catboost import CatBoostRegressor
        from sklearn.model_selection import KFold
    except ImportError:
        logger.error("CatBoost/sklearn not installed")
        return None

    features, targets, feature_names = load_training_data(session)
    if features.shape[0] < settings.CATBOOST_MIN_CASES:
        logger.warning(f"Not enough cases to train: {features.shape[0]} < {settings.CATBOOST_MIN_CASES}")
        return None

    logger.info(f"Training CatBoost on {features.shape[0]} cases...")

    cat_features = [0, 1, 2]

    model = CatBoostRegressor(
        iterations=200, depth=4, learning_rate=0.05,
        loss_function="RMSE", cat_features=cat_features,
        verbose=0, allow_writing_files=False, task_type="CPU",
    )

    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = []
    for train_idx, val_idx in kf.split(features):
        fold_model = CatBoostRegressor(
            iterations=200, depth=4, learning_rate=0.05,
            loss_function="RMSE", cat_features=cat_features,
            verbose=0, allow_writing_files=False, task_type="CPU",
        )
        fold_model.fit(features[train_idx], targets[train_idx])
        preds = fold_model.predict(features[val_idx])
        rmse = float(np.sqrt(np.mean((preds - targets[val_idx]) ** 2)))
        cv_scores.append(rmse)

    avg_rmse = float(np.mean(cv_scores))
    logger.info(f"CV RMSE: {avg_rmse:.4f}")

    model.fit(features, targets)

    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model.save_model(str(_MODEL_DIR / "model.cbm"))

    meta = {
        "timestamp": datetime.utcnow().isoformat(),
        "rmse": avg_rmse,
        "row_count": int(features.shape[0]),
        "feature_names": feature_names,
    }
    _META_PATH.write_text(json.dumps(meta, indent=2))
    logger.info(f"CatBoost model saved to {_MODEL_DIR}")

    return model


def load_model() -> Optional[object]:
    """Load saved CatBoost model from disk. Returns None if not found."""
    try:
        from catboost import CatBoostRegressor
    except ImportError:
        logger.error("CatBoost not installed")
        return None

    model_path = _MODEL_DIR / "model.cbm"
    if not model_path.exists():
        return None

    model = CatBoostRegressor()
    model.load_model(str(model_path))
    return model


def predict(model, enriched_items: list[EnrichedLineItem]) -> dict[int, float]:
    """Run CatBoost inference on non-readOnly items."""
    active_items = [item for item in enriched_items if not item.read_only]
    if not active_items:
        return {}

    # Column order matches FEATURE_COLUMNS (same as training data)
    features = np.array([
        [
            item.account_level_1, item.account_level_2, item.account_level_3,
            item.adjusted_pct_diff, item.adjusted_coverage_ratio,
            item.seasonality_index, item.normalized_annual_budget,
            float(item.is_income), float(item.is_reserve), float(item.is_admin),
        ]
        for item in active_items
    ], dtype=float)

    try:
        predictions = model.predict(features)
        predictions = np.clip(predictions, -MAX_PCT_CHANGE, MAX_PCT_CHANGE)
        return {item.account_code: float(pred) for item, pred in zip(active_items, predictions)}
    except Exception as e:
        logger.error(f"CatBoost inference failed: {e}")
        return {}


async def train_async() -> None:
    """Background model training. Opens its own session (not request-scoped)."""
    def _run() -> None:
        from ..db.session import SessionLocal
        session = SessionLocal()
        try:
            train_model(session)
        finally:
            session.close()

    logger.info("Starting background CatBoost training...")
    await asyncio.to_thread(_run)
    logger.info("Background CatBoost training complete.")
