"""Model base class for forecasting models."""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from core.forecasting.schemas import ModelVote


class ModelBase(ABC):
    """Abstract base for all forecasting models."""

    name: str = "base"
    is_experimental: bool = False

    @abstractmethod
    def fit(self, X: pd.DataFrame, y: pd.Series) -> None: ...

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> np.ndarray: ...

    def make_vote(
        self,
        r_hat_net: float | None,
        p_target: float | None,
        p_stop: float | None,
        volatility_forecast: float | None,
        weight: float = 0.0,
        validation_passed: bool = False,
        ic: float | None = None,
        brier_target: float | None = None,
    ) -> ModelVote:
        return ModelVote(
            model_name=self.name,
            r_hat_net=r_hat_net,
            p_target=p_target,
            p_stop=p_stop,
            volatility_forecast=volatility_forecast,
            weight=weight,
            validation_passed=validation_passed,
            ic=ic,
            brier_target=brier_target,
        )
