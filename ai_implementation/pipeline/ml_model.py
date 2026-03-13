"""CatBoost ML model: training, inference, and activation management."""
import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import settings
from ..models.schemas import EnrichedLineItem

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).parent.parent / "data" / "catboost_model"
_META_PATH = _MODEL_DIR / "meta.json"


def should_activate(db: sqlite3.Connection) -> bool:
    """Return True if there are >= CATBOOST_MIN_CASES accepted/modified cases."""
    row = db.execute(
        "SELECT COUNT(*) FROM feedback_cases WHERE user_decision IN ('accepted', 'modified')"
    ).fetchone()
    count = row[0] if row else 0
    return count >= settings.CATBOOST_MIN_CASES


def load_training_data(db: sqlite3.Connection) -> tuple:
    """Load training features and target from SQLite.

    Returns (features_array, targets_array, feature_names).
    """
    cursor = db.execute("""
        SELECT account_level_1, account_level_2, account_level_3,
               is_income, is_reserve, is_admin,
               adjusted_pct_diff, adjusted_coverage_ratio,
               seasonality_index, normalized_annual_budget,
               user_final_pct_change
        FROM feedback_cases
        WHERE user_decision IN ('accepted', 'modified')
          AND user_final_pct_change IS NOT NULL
          AND created_at > datetime('now', '-24 months')
    """)
    rows = cursor.fetchall()

    if not rows:
        return np.empty((0, 10)), np.empty(0), []

    feature_names = [
        "account_level_1", "account_level_2", "account_level_3",
        "is_income", "is_reserve", "is_admin",
        "adjusted_pct_diff", "adjusted_coverage_ratio",
        "seasonality_index", "normalized_annual_budget",
    ]

    features = np.array([[
        r[0] or 0, r[1] or 0, r[2] or 0,
        float(r[3] or 0), float(r[4] or 0), float(r[5] or 0),
        r[6] or 0.0, r[7] or 0.0, r[8] or 0.0, r[9] or 0.0,
    ] for r in rows], dtype=float)

    targets = np.array([r[10] for r in rows], dtype=float)

    return features, targets, feature_names


def train_model(db: sqlite3.Connection) -> Optional[object]:
    """Train CatBoost model and save to disk.

    Uses k-fold cross-validation. Saves model + meta.json to data/catboost_model/.
    Returns the trained model or None on failure.
    """
    try:
        from catboost import CatBoostRegressor
        from sklearn.model_selection import KFold, cross_val_score
    except ImportError:
        logger.error("CatBoost/sklearn not installed")
        return None

    features, targets, feature_names = load_training_data(db)
    if features.shape[0] < settings.CATBOOST_MIN_CASES:
        logger.warning(f"Not enough cases to train: {features.shape[0]} < {settings.CATBOOST_MIN_CASES}")
        return None

    logger.info(f"Training CatBoost on {features.shape[0]} cases...")

    # Categorical feature indices: account_level_1, 2, 3 (indices 0, 1, 2)
    cat_features = [0, 1, 2]

    model = CatBoostRegressor(
        iterations=200,
        depth=4,
        learning_rate=0.05,
        loss_function="RMSE",
        cat_features=cat_features,
        verbose=0,
        allow_writing_files=False,
        task_type="CPU",
    )

    # K-fold CV for RMSE estimate
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

    # Train final model on all data
    model.fit(features, targets)

    # Save
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
    """Run CatBoost inference on non-readOnly items.

    Returns dict of {account_code: predicted_pct_change}.
    """
    active_items = [item for item in enriched_items if not item.read_only]
    if not active_items:
        return {}

    features = np.array([
        [
            item.account_level_1, item.account_level_2, item.account_level_3,
            float(item.is_income), float(item.is_reserve), float(item.is_admin),
            item.adjusted_pct_diff, item.adjusted_coverage_ratio,
            item.seasonality_index, item.normalized_annual_budget,
        ]
        for item in active_items
    ], dtype=float)

    try:
        predictions = model.predict(features)
        # Clamp predictions to [-0.30, 0.30]
        predictions = np.clip(predictions, -0.30, 0.30)
        return {item.account_code: float(pred) for item, pred in zip(active_items, predictions)}
    except Exception as e:
        logger.error(f"CatBoost inference failed: {e}")
        return {}


async def train_async() -> None:
    """Background model training via asyncio.to_thread.

    Opens its own SQLite connection so it is not tied to the request lifecycle.
    The caller must NOT pass the request-scoped db connection.
    """
    def _run() -> None:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(settings.DB_PATH)
        conn.row_factory = _sqlite3.Row
        try:
            train_model(conn)
        finally:
            conn.close()

    logger.info("Starting background CatBoost training...")
    await asyncio.to_thread(_run)
    logger.info("Background CatBoost training complete.")
