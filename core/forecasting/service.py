"""ForecastingService — public entry point for the IDX forecasting layer."""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal

import numpy as np

from core.forecasting.dataset import DatasetBuilder
from core.forecasting.labels import TRANSACTION_COST, TAU_H, build_labels
from core.forecasting.models.naive import NaiveModel
from core.forecasting.models.tgarch import TGARCHForecaster
from core.forecasting.schemas import ForecastReport, ModelVote, ValidationSummary
from core.forecasting.validation import validate_model, walk_forward_splits

if TYPE_CHECKING:
    from schemas.debate import CIOVerdict

logger = logging.getLogger(__name__)

_DEFAULT_HORIZONS: tuple[int, ...] = (5, 10, 20)
_HISTORY_DAYS: int = 500


class ForecastingService:
    """Predict forward-looking ForecastReport for a given ticker.

    Graceful fallback: if ML models are unavailable (ImportError), falls back
    to TGARCH + Naive only, setting data_quality_flags accordingly.
    """

    def __init__(self) -> None:
        self._dataset_builder = DatasetBuilder()
        self._tgarch = TGARCHForecaster()
        self._naive = NaiveModel()

    def predict(
        self,
        ticker: str,
        as_of: date | None = None,
        horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
        mode: Literal["ensemble", "tgarch", "naive"] = "ensemble",
        cio_verdict: "CIOVerdict | None" = None,
    ) -> ForecastReport:
        """Produce a ForecastReport for ticker.

        If cio_verdict is provided, uses target_price and stop_loss to compute
        path-weighted p_target / p_stop via log-normal terminal approximation.
        Otherwise, uses TAU_H thresholds symmetrically.
        """
        as_of = as_of or date.today()
        flags: list[str] = []

        # --- 1. Fetch data ---
        end = as_of
        start = end - timedelta(days=_HISTORY_DAYS)
        horizon = min(horizons)  # use shortest horizon as primary

        try:
            dataset = self._dataset_builder.build([ticker], start, end, horizons=(horizon,))
        except Exception as e:
            return _error_report(ticker, as_of, horizon, [f"dataset_error:{type(e).__name__}"])

        if dataset.empty or len(dataset) < 30:
            return _error_report(ticker, as_of, horizon, ["insufficient_data"])

        # OCF flag
        if dataset["ocf_price_pct"].isna().all():
            flags.append("ocf_missing")

        # --- 2. Build labels ---
        try:
            labeled = build_labels(dataset.reset_index(level="ticker", drop=True), horizon)
        except Exception as e:
            return _error_report(ticker, as_of, horizon, [f"label_error:{type(e).__name__}"])

        labeled = labeled.dropna(subset=["r_net_h"])

        # --- 3. TGARCH volatility forecast ---
        close = dataset.reset_index(level="ticker", drop=True)["close"]
        import pandas as pd
        close_series = pd.Series(close.values, index=close.index)

        tgarch_sigmas, vol_fallback = self._tgarch.predict_volatility(close_series, horizon)
        if vol_fallback:
            flags.append("tgarch_fallback")

        sigma_forecast = tgarch_sigmas[0] if tgarch_sigmas else None

        # --- 4. Expected return from naive model ---
        feature_cols = [c for c in labeled.columns if c not in {"r_net_h", "y_up", "y_target_hit", "y_stop_hit", "sigma_realized"}]
        X = labeled[feature_cols].select_dtypes(include=[np.number]).fillna(0)
        y = labeled["r_net_h"].fillna(0)

        try:
            self._naive.fit(X, y)
            r_hat_arr = self._naive.predict(X.tail(1) if len(X) > 0 else X)
            r_hat_net = float(r_hat_arr[0]) if len(r_hat_arr) > 0 else None
        except Exception as e:
            logger.warning("[ForecastSvc] naive predict failed for %s: %s", ticker, e)
            r_hat_net = None
            flags.append("naive_predict_failed")

        # --- 5. Compute p_target / p_stop ---
        p_target, p_stop = _compute_probs(
            r_hat=r_hat_net,
            sigma=sigma_forecast,
            horizon=horizon,
            cio_verdict=cio_verdict,
            close=float(close.iloc[-1]) if len(close) > 0 else None,
        )

        # --- 6. Expected value ---
        ev = _compute_ev(p_target, p_stop, cio_verdict, close, horizon)

        # --- 7. Walk-forward validation (quick, optional) ---
        validation: ValidationSummary | None = None
        if len(labeled) >= 60 and mode == "ensemble":
            try:
                splits = walk_forward_splits(labeled, n_splits=3, test_size_days=30)
                if splits:
                    validation = validate_model(self._naive, splits, horizon)
                    flags.append(f"validation_status:{validation.status}")
            except Exception as e:
                logger.warning("[ForecastSvc] walk-forward validation failed for %s: %s", ticker, e)
                flags.append("validation_failed")

        # --- 8. Ensemble weights ---
        naive_vote = ModelVote(
            model_name="naive",
            r_hat_net=r_hat_net,
            p_target=p_target,
            p_stop=p_stop,
            volatility_forecast=sigma_forecast,
            weight=1.0,
            validation_passed=validation.status in ("production", "research_only") if validation else False,
            ic=validation.ic_mean if validation else None,
            brier_target=validation.brier if validation else None,
        )
        tgarch_vote = ModelVote(
            model_name="tgarch",
            r_hat_net=None,
            p_target=p_target,
            p_stop=p_stop,
            volatility_forecast=sigma_forecast,
            weight=0.0 if vol_fallback else 0.5,
            validation_passed=not vol_fallback,
        )

        # --- 9. Decision ---
        decision = _make_decision(p_target, p_stop, ev, r_hat_net)

        # --- 10. Confidence ---
        confidence = _compute_confidence(p_target, p_stop, ev, validation)

        return ForecastReport(
            ticker=ticker.upper(),
            as_of=as_of,
            horizon_days=horizon,
            expected_return_net=r_hat_net,
            p_target=p_target,
            p_stop=p_stop,
            volatility_forecast=sigma_forecast,
            expected_value=ev,
            decision=decision,
            confidence=confidence,
            model_votes=[naive_vote, tgarch_vote],
            validation_summary=validation,
            data_quality_flags=flags,
            volatility_fallback=vol_fallback,
        )


def _compute_probs(
    r_hat: float | None,
    sigma: float | None,
    horizon: int,
    cio_verdict: "CIOVerdict | None",
    close: float | None,
) -> tuple[float | None, float | None]:
    """Log-normal terminal probability approximation for target/stop hit."""
    if sigma is None or sigma <= 0:
        return None, None

    h_frac = horizon / 252.0
    drift_h = (r_hat or 0.0) * h_frac
    sigma_h = sigma * math.sqrt(h_frac)

    if sigma_h < 1e-10:
        return None, None

    # Determine target/stop thresholds
    if cio_verdict is not None and close is not None and close > 0:
        target = cio_verdict.target_price
        stop = cio_verdict.stop_loss
        if target and stop and target > 0 and stop > 0 and stop < close < target:
            G = (target - close) / close  # fractional gain to target
            L = (close - stop) / close    # fractional loss to stop
        else:
            tau = TAU_H.get(horizon, 0.015)
            G, L = tau, tau
    else:
        tau = TAU_H.get(horizon, 0.015)
        G, L = tau, tau

    if G <= 0 or L <= 0:
        return None, None

    try:
        from scipy.stats import norm as _norm  # noqa: PLC0415
        ln_target = math.log(1 + G)
        ln_stop = math.log(1 - L) if L < 1 else float("-inf")

        p_target = float(1 - _norm.cdf((ln_target - drift_h) / sigma_h))
        p_stop = float(_norm.cdf((ln_stop - drift_h) / sigma_h)) if math.isfinite(ln_stop) else 0.0

        p_target = max(0.0, min(1.0, p_target))
        p_stop = max(0.0, min(1.0, p_stop))
        return p_target, p_stop
    except Exception as e:
        logger.warning("[ForecastSvc] _compute_probs failed: %s", e)
        return None, None


def _compute_ev(
    p_target: float | None,
    p_stop: float | None,
    cio_verdict: "CIOVerdict | None",
    close,
    horizon: int,
) -> float | None:
    if p_target is None or p_stop is None:
        return None

    # G and L from verdict when available
    close_val = float(close.iloc[-1]) if hasattr(close, "iloc") else (float(close) if close else None)
    G = L = TAU_H.get(horizon, 0.015)

    if cio_verdict is not None and close_val is not None and close_val > 0:
        if cio_verdict.target_price and cio_verdict.stop_loss:
            if cio_verdict.stop_loss < close_val < cio_verdict.target_price:
                G = (cio_verdict.target_price - close_val) / close_val
                L = (close_val - cio_verdict.stop_loss) / close_val

    cost = TRANSACTION_COST
    ev = p_target * G - p_stop * L - cost
    return float(ev)


def _make_decision(
    p_target: float | None,
    p_stop: float | None,
    ev: float | None,
    r_hat_net: float | None,
) -> Literal["BUY", "WATCH", "AVOID"]:
    if any(v is None for v in [p_target, p_stop, ev, r_hat_net]):
        return "AVOID"

    buy = (
        p_target >= 0.55  # type: ignore[operator]
        and (p_target - p_stop) >= 0.15  # type: ignore[operator]
        and ev >= 0.02  # type: ignore[operator]
        and r_hat_net >= 0.015  # type: ignore[operator]
    )
    if buy:
        return "BUY"

    if ev > 0:
        return "WATCH"

    return "AVOID"


def _compute_confidence(
    p_target: float | None,
    p_stop: float | None,
    ev: float | None,
    validation: ValidationSummary | None,
) -> float | None:
    if p_target is None:
        return None
    base = max(0.0, min(1.0, p_target - (p_stop or 0.0)))
    if validation and validation.ic_mean is not None:
        ic_boost = max(0.0, min(0.1, validation.ic_mean))
        base = min(1.0, base + ic_boost)
    return round(base, 4)


def _error_report(
    ticker: str,
    as_of: date,
    horizon: int,
    flags: list[str],
) -> ForecastReport:
    return ForecastReport(
        ticker=ticker.upper(),
        as_of=as_of,
        horizon_days=horizon,
        data_quality_flags=flags,
        decision="AVOID",
    )
