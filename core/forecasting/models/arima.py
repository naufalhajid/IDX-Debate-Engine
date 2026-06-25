"""ARIMA/SARIMAX forecaster (optional dep: statsmodels)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.forecasting.models import ModelBase

_MIN_BARS: int = 252
# Candidate orders ranked simplest to most complex.
# (0,1,0) = random walk — often best for equity returns.
_CANDIDATE_ORDERS: tuple[tuple[int, int, int], ...] = (
    (0, 1, 0),
    (1, 1, 0),
    (0, 1, 1),
    (1, 1, 1),
)


class ARIMAForecaster(ModelBase):
    """SARIMAX wrapper for return forecasting with AIC order selection.

    Tries all candidate orders and picks the one with lowest AIC.
    Requires: statsmodels >= 0.14.0 (optional dependency).
    """

    name = "arima"

    def __init__(self) -> None:
        self._result = None
        self._best_order: tuple[int, int, int] | None = None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        try:
            from statsmodels.tsa.statespace.sarimax import SARIMAX  # noqa: PLC0415
        except ImportError as e:
            raise ImportError("statsmodels is required for ARIMAForecaster") from e

        series = y.dropna()
        if len(series) < _MIN_BARS:
            self._result = None
            return

        import warnings
        best_aic = float("inf")
        best_result = None
        best_order = None

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for order in _CANDIDATE_ORDERS:
                try:
                    model = SARIMAX(series, order=order, trend="n")
                    result = model.fit(disp=False, maxiter=100)
                    if result.aic < best_aic:
                        best_aic = result.aic
                        best_result = result
                        best_order = order
                except Exception:
                    continue

        self._result = best_result
        self._best_order = best_order

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._result is None:
            return np.zeros(len(X))
        try:
            forecast = self._result.forecast(steps=len(X))
            return np.array(forecast)
        except Exception:
            return np.zeros(len(X))
