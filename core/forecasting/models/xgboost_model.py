"""XGBoost tabular forecasting model (optional deps: xgboost, scikit-learn)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.forecasting.models import ModelBase


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

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        try:
            from xgboost import XGBClassifier, XGBRegressor  # noqa: PLC0415
        except ImportError as e:
            raise ImportError("xgboost is required for XGBoostForecaster") from e

        X_num = X.select_dtypes(include=[np.number]).fillna(0)

        self._reg = XGBRegressor(
            n_estimators=self._n_estimators,
            max_depth=self._max_depth,
            verbosity=0,
        )
        self._reg.fit(X_num, y.fillna(0))

        if "y_target_hit" in X.columns:
            self._clf_target = XGBClassifier(
                n_estimators=self._n_estimators,
                max_depth=self._max_depth,
                verbosity=0,
            )
            self._clf_target.fit(X_num, X["y_target_hit"].fillna(0).astype(int))

        if "y_stop_hit" in X.columns:
            self._clf_stop = XGBClassifier(
                n_estimators=self._n_estimators,
                max_depth=self._max_depth,
                verbosity=0,
            )
            self._clf_stop.fit(X_num, X["y_stop_hit"].fillna(0).astype(int))

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._reg is None:
            return np.zeros(len(X))
        X_num = X.select_dtypes(include=[np.number]).fillna(0)
        try:
            return self._reg.predict(X_num)
        except Exception:
            return np.zeros(len(X))

    def predict_proba_target(self, X: pd.DataFrame) -> np.ndarray | None:
        if self._clf_target is None:
            return None
        X_num = X.select_dtypes(include=[np.number]).fillna(0)
        try:
            return self._clf_target.predict_proba(X_num)[:, 1]
        except Exception:
            return None

    def predict_proba_stop(self, X: pd.DataFrame) -> np.ndarray | None:
        if self._clf_stop is None:
            return None
        X_num = X.select_dtypes(include=[np.number]).fillna(0)
        try:
            return self._clf_stop.predict_proba(X_num)[:, 1]
        except Exception:
            return None
