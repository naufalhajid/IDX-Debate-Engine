"""
utils/dynamic_atr.py — GARCH(1,1)-based dynamic ATR for IDX swing-trade sizing.

Replaces the classic Wilder ATR with a GARCH-conditional volatility estimate that
adapts to current IDX market regime (heteroskedasticity, fat tails, leverage effect).

GARCH(1,1) formula:
    σ²_t = ω + α × ε²_{t-1} + β × σ²_{t-1}

IDX-calibrated priors (JISEBI 2025, IHSG 2018-2024):
    α ≈ 0.10–0.15 (shock response), β ≈ 0.80–0.85 (persistence), α+β < 1

ATR conversion:
    GARCH_ATR = σ_t × P_t × √period
      σ_t    = conditional daily vol (decimal, from GARCH output ÷ 100)
      P_t    = last close price (Rupiah)
      √period = time-horizon scaling

For daily stop-loss sizing (replacing ATR-14), call with period=1.
Default period=14 gives the 14-day forward range horizon.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

_MIN_BARS_FOR_GARCH: int = 60
_GARCH_CAP_MULTIPLIER: float = 3.0


@dataclass(frozen=True)
class DynamicATRResult:
    value: float
    method: str
    fallback_reason: str | None = None
    alpha: float | None = None
    beta: float | None = None
    persistence: float | None = None


def calculate_dynamic_atr(
    prices: pd.Series,
    period: int = 14,
    use_garch: bool = True,
    fit_window: int = 120,
) -> float:
    """GARCH(1,1) dynamic ATR — returns a single float (Rupiah).

    For daily stop-loss sizing equivalent to ATR-14, use period=1.
    Default period=14 is a 14-day horizon estimate for standalone risk calcs.

    fit_window: number of most-recent bars used for GARCH fitting. Keeps GARCH
    reactive to the current regime even when prices has a longer warm-up history.

    Fallback to Wilder close-to-close ATR when:
    - len(prices) < 60 (insufficient GARCH training data)
    - GARCH fit does not converge
    - GARCH non-stationary (α+β ≥ 1)
    - Conditional variance explodes (> 3× classic ATR, then caps at 3×)
    """
    if not use_garch:
        return _classic_atr(prices, period)
    return _compute_dynamic_atr(prices, period, fit_window).value


def compute_dynamic_atr_full(
    prices: pd.Series,
    period: int = 14,
    use_garch: bool = True,
    fit_window: int = 120,
) -> DynamicATRResult:
    """Same as calculate_dynamic_atr but returns full diagnostic DynamicATRResult."""
    if not use_garch:
        return DynamicATRResult(value=_classic_atr(prices, period), method="classic")
    return _compute_dynamic_atr(prices, period, fit_window)


# ── internals ─────────────────────────────────────────────────────────────────

def _compute_dynamic_atr(prices: pd.Series, period: int, fit_window: int) -> DynamicATRResult:
    classic_atr = _classic_atr(prices, period)

    if len(prices) < _MIN_BARS_FOR_GARCH:
        logger.debug(
            f"[DynATR] Insufficient data ({len(prices)} < {_MIN_BARS_FOR_GARCH}), "
            "using classic ATR"
        )
        return DynamicATRResult(
            value=classic_atr,
            method="classic_fallback",
            fallback_reason="insufficient_data",
        )

    try:
        garch_result = _garch_atr(prices, period, fit_window)
    except Exception as exc:
        logger.warning(f"[DynATR] GARCH exception: {type(exc).__name__}: {exc} — classic fallback")
        return DynamicATRResult(
            value=classic_atr,
            method="classic_fallback",
            fallback_reason=f"garch_exception:{type(exc).__name__}",
        )

    if garch_result is None:
        return DynamicATRResult(
            value=classic_atr,
            method="classic_fallback",
            fallback_reason="garch_non_convergence",
        )

    garch_value, alpha, beta = garch_result
    persistence = alpha + beta

    cap = _GARCH_CAP_MULTIPLIER * classic_atr
    if garch_value > cap:
        logger.warning(
            f"[DynATR] GARCH ATR {garch_value:.2f} > 3× classic {classic_atr:.2f} — capping"
        )
        return DynamicATRResult(
            value=cap,
            method="garch",
            fallback_reason="variance_cap_applied",
            alpha=alpha,
            beta=beta,
            persistence=persistence,
        )

    return DynamicATRResult(
        value=garch_value,
        method="garch",
        alpha=alpha,
        beta=beta,
        persistence=persistence,
    )


def _garch_atr(prices: pd.Series, period: int, fit_window: int) -> tuple[float, float, float] | None:
    """Fit GARCH(1,1) on log-returns. Returns (atr_rp, alpha, beta) or None on failure."""
    try:
        from arch import arch_model
    except ImportError:
        logger.warning("[DynATR] 'arch' library not installed — run: uv add arch>=6.0.0")
        return None

    log_returns = np.log(prices / prices.shift(1)).dropna() * 100  # scale to % for stability
    log_returns = log_returns.replace([np.inf, -np.inf], np.nan).dropna()
    log_returns = log_returns.tail(fit_window)  # restrict to recent regime; keeps GARCH reactive

    if len(log_returns) < _MIN_BARS_FOR_GARCH or log_returns.std() < 1e-10:
        return None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = arch_model(
            log_returns,
            vol="GARCH",
            p=1,
            q=1,
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
            return None

    if result.convergence_flag != 0:
        logger.debug("[DynATR] GARCH did not converge")
        return None

    if not math.isfinite(result.loglikelihood):
        return None

    try:
        alpha = float(result.params["alpha[1]"])
        beta = float(result.params["beta[1]"])
    except KeyError:
        return None

    # Stationarity check: α + β < 1
    if not (alpha >= 0 and beta >= 0 and (alpha + beta) < 1.0):
        logger.warning(
            f"[DynATR] GARCH non-stationary: α={alpha:.4f}, β={beta:.4f}, α+β={alpha+beta:.4f}"
        )
        return None

    cond_vol_pct = float(result.conditional_volatility.iloc[-1])  # in %
    cond_vol = cond_vol_pct / 100  # daily vol as decimal fraction

    last_price = float(prices.dropna().iloc[-1])
    if not (last_price > 0 and math.isfinite(cond_vol) and cond_vol > 0):
        return None

    garch_atr = cond_vol * last_price * math.sqrt(period)

    if not math.isfinite(garch_atr) or garch_atr <= 0:
        return None

    return garch_atr, alpha, beta


def _classic_atr(prices: pd.Series, period: int) -> float:
    """Close-to-close Wilder ATR approximation (single Series, no H/L needed).

    Uses |Δclose| as True Range proxy. For OHLCV data with a proper High/Low
    series, prefer utils.technicals.compute_atr().
    """
    if len(prices) < 2:
        return 0.0

    tr = prices.diff().abs()
    atr_series = tr.ewm(
        alpha=1 / max(period, 1),
        min_periods=min(period, len(prices) - 1),
        adjust=False,
    ).mean()
    val = float(atr_series.iloc[-1])

    if not math.isfinite(val) or val <= 0:
        return float(prices.std()) if prices.std() > 0 else 0.0

    return val
