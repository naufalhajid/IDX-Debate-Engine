"""XGBoost tabular forecasting model (optional deps: xgboost, scikit-learn)."""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from core.forecasting.models import ModelBase

logger = logging.getLogger(__name__)

# Label columns that must never appear in the feature matrix.
_LABEL_COLS: frozenset[str] = frozenset(
    {"y_target_hit", "y_stop_hit", "y_up", "r_net_h", "sigma_realized"}
)


class XGBoostForecaster(ModelBase):
    """Return-only XGBoost regressor for the selected H-day net return.

    Runtime target/stop probabilities are deliberately derived by
    ``ForecastingService`` from this H-day return and the volatility forecast.
    The former classifier heads were removed because their symmetric path
    labels are intentionally disabled and they were never fitted by runtime.

    Requires: xgboost >= 2.0.0, scikit-learn >= 1.4.0 (optional dependencies).
    """

    name = "xgboost"

    def __init__(self, n_estimators: int = 200, max_depth: int = 4) -> None:
        self._n_estimators = n_estimators
        self._max_depth = max_depth
        self._reg = None

    def _xgb_params(self) -> dict:
        return dict(
            n_estimators=self._n_estimators,
            max_depth=self._max_depth,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=1.0,
            min_child_weight=3,
            random_state=0,
            verbosity=0,
        )

    def _feature_matrix(self, X: pd.DataFrame) -> pd.DataFrame:
        feat_cols = [c for c in X.columns if c not in _LABEL_COLS]
        return X[feat_cols].select_dtypes(include=[np.number]).fillna(0)

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        try:
            from xgboost import XGBRegressor  # noqa: PLC0415
        except ImportError as e:
            raise ImportError("xgboost is required for XGBoostForecaster") from e

        X_feat = self._feature_matrix(X)
        params = self._xgb_params()

        self._reg = XGBRegressor(**params)
        self._reg.fit(X_feat, y.fillna(0))

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._reg is None:
            return np.zeros(len(X))
        try:
            return self._reg.predict(self._feature_matrix(X))
        except Exception as e:
            logger.warning("[XGBoost] predict failed: %s", e)
            return np.zeros(len(X))
