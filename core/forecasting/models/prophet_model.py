"""Prophet forecaster for trend/seasonality (optional dep: prophet)."""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from core.forecasting.models import ModelBase

logger = logging.getLogger(__name__)


class ProphetForecaster(ModelBase):
    """Facebook Prophet wrapper for volume/liquidity trend.

    Not a primary return model. Requires: prophet >= 1.1.5 (optional dependency).
    """

    name = "prophet"

    def __init__(self) -> None:
        self._model = None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        try:
            from prophet import Prophet  # noqa: PLC0415
        except ImportError as e:
            raise ImportError("prophet is required for ProphetForecaster") from e

        df = pd.DataFrame({"ds": y.index, "y": y.values})
        import logging
        logging.getLogger("prophet").setLevel(logging.ERROR)
        logging.getLogger("cmdstanpy").setLevel(logging.ERROR)

        self._model = Prophet(daily_seasonality=False, weekly_seasonality=False)
        self._model.fit(df)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._model is None:
            return np.zeros(len(X))
        try:
            future = pd.DataFrame({"ds": X.index})
            forecast = self._model.predict(future)
            return forecast["yhat"].values
        except Exception as e:
            logger.warning("[Prophet] predict failed: %s", e)
            return np.zeros(len(X))
