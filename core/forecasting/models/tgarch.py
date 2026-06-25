"""TGARCH multi-step volatility forecaster."""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd

from core.forecasting.models import ModelBase

_MIN_BARS: int = 60
_MAX_PERSISTENCE: float = 1.0
_DIVERGENCE_MULTIPLIER: float = 3.0


class TGARCHForecaster(ModelBase):
    """Multi-step TGARCH(1,1) volatility forecast.

    Replicates the arch_model fitting from utils/dynamic_atr.py but adds
    .forecast(horizon=h, reindex=False) for multi-step variance paths.
    """

    name = "tgarch"

    def __init__(self, model_type: str = "tgarch", fit_window: int = 250) -> None:
        self._model_type = "tgarch" if model_type.lower() in {"tgarch", "tarch", "gjr"} else "garch"
        self._fit_window = fit_window

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        pass  # stateless: fits on each predict call

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.zeros(len(X))

    def predict_volatility(
        self,
        returns: pd.Series,
        horizon: int,
    ) -> tuple[list[float], bool]:
        """Return (sigma_forecasts_annualized, fallback_used).

        sigma_forecasts_annualized: list of annualized daily sigma for each of
        the next `horizon` trading days. Length == horizon.

        Fallback conditions → classic rolling std + volatility_fallback=True:
          - persistence (α + β + γ/2) >= 1.0
          - forecast variance diverges (> _DIVERGENCE_MULTIPLIER × last in-sample vol)
          - fit non-convergence
          - ImportError for arch library
        """
        try:
            from arch import arch_model  # noqa: PLC0415
        except ImportError:
            return self._classic_fallback(returns, horizon), True

        log_returns = np.log(returns / returns.shift(1)).dropna() * 100
        log_returns = log_returns.replace([np.inf, -np.inf], np.nan).dropna()
        log_returns = log_returns.tail(self._fit_window)

        if len(log_returns) < _MIN_BARS or float(log_returns.std()) < 1e-10:
            return self._classic_fallback(returns, horizon), True

        use_tgarch = self._model_type == "tgarch"

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = arch_model(
                log_returns,
                vol="GARCH",
                p=1,
                o=1 if use_tgarch else 0,
                q=1,
                power=1.0 if use_tgarch else 2.0,
                dist="normal",
                mean="Zero",
            )
            try:
                result = model.fit(
                    disp="off",
                    show_warning=False,
                    options={"maxiter": 200, "ftol": 1e-8},
                )
            except Exception:
                return self._classic_fallback(returns, horizon), True

        if result.convergence_flag != 0 or not math.isfinite(result.loglikelihood):
            return self._classic_fallback(returns, horizon), True

        try:
            alpha = float(result.params["alpha[1]"])
            beta = float(result.params["beta[1]"])
        except KeyError:
            return self._classic_fallback(returns, horizon), True

        gamma = float(result.params.get("gamma[1]", 0.0)) if use_tgarch else 0.0
        persistence = alpha + beta + gamma / 2.0

        if not (alpha >= 0 and beta >= 0 and persistence < _MAX_PERSISTENCE):
            return self._classic_fallback(returns, horizon), True

        last_insample_vol_pct = float(result.conditional_volatility.iloc[-1])

        try:
            forecast = result.forecast(horizon=horizon, reindex=False)
            variance_path = forecast.variance.iloc[-1].values  # shape (horizon,)
        except Exception:
            return self._classic_fallback(returns, horizon), True

        # Divergence check: any forecast variance > 3× last in-sample
        cap = _DIVERGENCE_MULTIPLIER * (last_insample_vol_pct ** 2)
        if any(v > cap for v in variance_path):
            return self._classic_fallback(returns, horizon), True

        # Convert from % variance to annualized sigma: sqrt(252 * v_%) / 100
        sigma_list = [math.sqrt(252.0 * max(v, 0.0)) / 100.0 for v in variance_path]

        return sigma_list, False

    def _classic_fallback(self, returns: pd.Series, horizon: int) -> list[float]:
        """Rolling std fallback: annualize over window=20."""
        pct_returns = returns.pct_change().dropna()
        if len(pct_returns) < 2:
            return [0.0] * horizon
        daily_std = float(pct_returns.tail(20).std())
        annual_sigma = daily_std * math.sqrt(252.0)
        return [annual_sigma] * horizon
