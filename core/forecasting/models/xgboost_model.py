"""XGBoost tabular forecasting model (optional deps: xgboost, scikit-learn)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.forecasting.models import ModelBase

# Label columns that must never appear in the feature matrix.
_LABEL_COLS: frozenset[str] = frozenset(
    {"y_target_hit", "y_stop_hit", "y_up", "r_net_h", "sigma_realized"}
)


class XGBoostForecaster(ModelBase):
    """Three-head XGBoost: return regressor + p_target classifier + p_stop classifier.

    Requires: xgboost >= 2.0.0, scikit-learn >= 1.4.0 (optional dependencies).
    """

    name = "xgboost"

    def __init__(self, n_estimators: int = 200, max_depth: int = 4) -> None:
        self._n_estimators = n_estimators
        self._max_depth = max_depth
        self._reg = None
        self._clf_target = None
        self._clf_stop = None

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
            verbosity=0,
        )

    def _feature_matrix(self, X: pd.DataFrame) -> pd.DataFrame:
        feat_cols = [c for c in X.columns if c not in _LABEL_COLS]
        return X[feat_cols].select_dtypes(include=[np.number]).fillna(0)

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        try:
            from xgboost import XGBClassifier, XGBRegressor  # noqa: PLC0415
        except ImportError as e:
            raise ImportError("xgboost is required for XGBoostForecaster") from e

        # Extract label arrays before building feature matrix to avoid leakage.
        label_target = (
            X["y_target_hit"].fillna(0).astype(int) if "y_target_hit" in X.columns else None
        )
        label_stop = (
            X["y_stop_hit"].fillna(0).astype(int) if "y_stop_hit" in X.columns else None
        )

        X_feat = self._feature_matrix(X)
        params = self._xgb_params()

        self._reg = XGBRegressor(**params)
        self._reg.fit(X_feat, y.fillna(0))

        if label_target is not None:
            self._clf_target = XGBClassifier(**params)
            self._clf_target.fit(X_feat, label_target)

        if label_stop is not None:
            self._clf_stop = XGBClassifier(**params)
            self._clf_stop.fit(X_feat, label_stop)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._reg is None:
            return np.zeros(len(X))
        try:
            return self._reg.predict(self._feature_matrix(X))
        except Exception:
            return np.zeros(len(X))

    def predict_proba_target(self, X: pd.DataFrame) -> np.ndarray | None:
        if self._clf_target is None:
            return None
        try:
            return self._clf_target.predict_proba(self._feature_matrix(X))[:, 1]
        except Exception:
            return None

    def predict_proba_stop(self, X: pd.DataFrame) -> np.ndarray | None:
        if self._clf_stop is None:
            return None
        try:
            return self._clf_stop.predict_proba(self._feature_matrix(X))[:, 1]
        except Exception:
            return None
