"""ARIMA/SARIMAX forecaster (optional dep: statsmodels)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.forecasting.models import ModelBase

_MIN_BARS: int = 252


class ARIMAForecaster(ModelBase):
    """SARIMAX(1,1,1) wrapper for return forecasting.

    Requires: statsmodels >= 0.14.0 (optional dependency).
    """

    name = "arima"

    def __init__(self, order: tuple[int, int, int] = (1, 1, 1)) -> None:
        self._order = order
        self._result = None

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
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = SARIMAX(series, order=self._order, trend="n")
            self._result = model.fit(disp=False, maxiter=100)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._result is None:
            return np.zeros(len(X))
        try:
            forecast = self._result.forecast(steps=len(X))
            return np.array(forecast)
        except Exception:
            return np.zeros(len(X))
