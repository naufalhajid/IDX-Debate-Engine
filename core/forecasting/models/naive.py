"""Naive sector-mean baseline model."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.forecasting.models import ModelBase


class NaiveModel(ModelBase):
    """Sector-mean forward return baseline.

    All other models must beat this in IC, RMSE, Brier, and EV before entering
    production ensemble.
    """

    name = "naive"

    def __init__(self) -> None:
        self._sector_means: dict[str, float] = {}
        self._global_mean: float = 0.0

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        if "sector" in X.columns:
            for sector, group in X.groupby("sector"):
                mask = group.index
                vals = y.loc[mask].dropna()
                if len(vals) > 0:
                    self._sector_means[str(sector)] = float(vals.mean())
        self._global_mean = float(y.dropna().mean()) if len(y.dropna()) > 0 else 0.0

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        preds = np.full(len(X), self._global_mean)
        if "sector" in X.columns:
            for i, (_, row) in enumerate(X.iterrows()):
                sector = str(row.get("sector", ""))
                if sector in self._sector_means:
                    preds[i] = self._sector_means[sector]
        return preds
