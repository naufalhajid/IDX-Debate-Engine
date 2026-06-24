"""
src/evaluation/backtest_metrics.py — Academic-grade backtest metrics for IDX strategies.

Implements:
  - Deflated Sharpe Ratio (DSR) — Bailey & Lopez de Prado (2014)
  - IDX-accurate transaction cost model
  - Walk-forward backtester (in-sample/OOS splits)

Reference:
  Bailey, D.H. & Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio."
  https://ssrn.com/abstract=2460551
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

# ── Euler-Mascheroni constant ─────────────────────────────────────────────────
_EULER_GAMMA = 0.5772156649


def calculate_deflated_sharpe_ratio(
    returns: np.ndarray,
    benchmark_sr: float = 0.5,
    n_trials: int = 1,
    freq: int = 252,
) -> dict:
    """
    Deflated Sharpe Ratio following Bailey & Lopez de Prado (2014).

    Answers: "What is the probability this SR is real after correcting for
    multiple testing over n_trials parameter combinations?"

    Args:
        returns: Array of per-period (daily) returns.
        benchmark_sr: Annualised SR threshold to beat. 0.5 is typical for
                      swing strategies; 1.0 for intraday.
        n_trials: Number of parameter combinations already tried before
                  selecting this strategy. n_trials=1 skips deflation (PSR only).
        freq: Trading days per year (252 for IDX).

    Returns:
        dict with keys:
          sharpe_ratio         – annualised SR
          probabilistic_sr     – P(true SR > benchmark_sr) given one trial
          deflated_sr          – PSR adjusted for multiple testing (equals PSR
                                 when n_trials == 1)
          min_track_record_days – minimum days needed for SR to be significant
          is_significant       – True if deflated_sr > 0.95
          n_trials             – passed-through for downstream reporting
    """
    returns = np.asarray(returns, dtype=float)
    T = len(returns)
    if T < 4:
        raise ValueError(f"Need at least 4 observations; got {T}.")

    sr_period = returns.mean() / returns.std(ddof=1)
    sr = float(sr_period * math.sqrt(freq))

    skew = float(stats.skew(returns))
    # scipy kurtosis(fisher=True) returns *excess* kurtosis (γ₄ - 3).
    # Bailey-LdP formula uses (γ₄ - 1)/4 where γ₄ is total kurtosis,
    # so excess_kurt → (excess_kurt + 2) / 4.
    kurt = float(stats.kurtosis(returns, fisher=True))

    def _sigma_sr(sr_hat: float) -> float:
        """Asymptotic std of annualised SR estimate (Bailey-LdP eq. 3)."""
        numerator = 1.0 - skew * sr_hat + (kurt + 2) / 4.0 * sr_hat ** 2
        return math.sqrt(max(numerator, 1e-12) / (T - 1))

    sigma = _sigma_sr(sr)

    # ── Probabilistic SR: P(SR_true > benchmark) ──────────────────────────
    z_psr = (sr - benchmark_sr) / sigma if sigma > 0 else 0.0
    psr = float(stats.norm.cdf(z_psr))

    # ── Minimum track-record length ───────────────────────────────────────────
    # Bailey-LdP eq. 14:  T* = 1 + (z_α × σ̂(SR*) / (SR − SR*))²
    # where SR and SR* are both ANNUALISED; result T* is in trading-day observations.
    sigma_star = _sigma_sr(benchmark_sr)
    if abs(sr - benchmark_sr) < 1e-10:
        min_trl_days = 10**6
    else:
        z_crit = stats.norm.ppf(1 - 0.05 / max(n_trials, 1))
        min_trl_obs = 1.0 + sigma_star ** 2 / (sr - benchmark_sr) ** 2 * z_crit ** 2
        min_trl_days = int(min_trl_obs)

    # ── Deflated SR: correct for multiple testing ──────────────────────────
    # When n_trials == 1 there is no multiple-testing inflation, so DSR = PSR.
    # For n_trials > 1 we subtract the expected maximum SR from n_trials trials.
    if n_trials <= 1:
        deflated_psr = psr
    else:
        # Expected maximum SR over n_trials IID normal draws (Gumbel approximation)
        e_max_sr = (
            (1 - _EULER_GAMMA) * stats.norm.ppf(1 - 1.0 / n_trials)
            + _EULER_GAMMA * stats.norm.ppf(1 - 1.0 / (n_trials * math.e))
        )
        z_dsr = (sr - e_max_sr) / sigma if sigma > 0 else 0.0
        deflated_psr = float(stats.norm.cdf(z_dsr))

    return {
        "sharpe_ratio": round(sr, 4),
        "probabilistic_sr": round(psr, 4),
        "deflated_sr": round(deflated_psr, 4),
        "min_track_record_days": min_trl_days,
        "is_significant": deflated_psr > 0.95,
        "n_trials": n_trials,
    }


def calculate_transaction_cost(
    price: float,
    shares: int,
    action: str = "buy",
    broker_rate: float = 0.0019,
) -> float:
    """
    Realistic IDX round-trip cost model.

    Covers: broker commission, pajak penjualan (0.1% sell-only), and half
    bid-ask spread based on IDX tick-size tiers.

    Args:
        price:       Share price in IDR.
        shares:      Number of shares (minimum 1 lot = 100).
        action:      'buy' or 'sell'.
        broker_rate: One-way broker commission (default 0.19%, competitive
                     online broker). Add-up to 0.28% for conventional brokers.

    Returns:
        Total transaction cost in IDR for this one-way leg.
    """
    gross_value = price * shares

    commission = gross_value * broker_rate

    # Pajak penjualan: 0.1% applied only on sell
    tax = gross_value * 0.001 if action.lower() == "sell" else 0.0

    # Bid-ask spread (half-spread per leg) based on IDX fractional tick tiers
    if price <= 200:
        tick = 1
    elif price <= 500:
        tick = 2
    elif price <= 2_000:
        tick = 5
    elif price <= 5_000:
        tick = 10
    else:
        tick = 25

    spread_cost = tick * 0.5 * shares  # half-spread for one direction

    return commission + tax + spread_cost


def walk_forward_backtest(
    signals_df: "pd.DataFrame",
    prices_df: "pd.DataFrame",
    insample_days: int = 252,
    oos_days: int = 63,
) -> dict:
    """
    Walk-forward OOS validation for IDX swing trading signals.

    Splits the full history into non-overlapping (in-sample, OOS) windows and
    evaluates signal quality on each OOS period to detect over-fit.

    Args:
        signals_df: DataFrame with DatetimeIndex and a 'signal' column
                    (1=buy, 0=hold, -1=sell) plus optional 'ticker' column.
        prices_df:  DataFrame with DatetimeIndex and per-ticker close prices
                    (columns = tickers, or 'close' for single-asset).
        insample_days: Calibration window length in trading days.
        oos_days:     Out-of-sample evaluation window in trading days.

    Returns:
        dict with:
          windows  – list of per-OOS-period dicts (dates, returns, DSR)
          aggregate – pooled DSR across all OOS returns
          n_windows – number of complete OOS periods evaluated
    """
    import pandas as pd  # lazy import — avoid loading pandas for DSR-only callers

    if signals_df.empty or prices_df.empty:
        return {"windows": [], "aggregate": None, "n_windows": 0}

    combined = signals_df.join(prices_df, how="inner").sort_index()
    n = len(combined)

    if n < insample_days + oos_days:
        return {"windows": [], "aggregate": None, "n_windows": 0}

    # Detect the price column (single-asset or first matching column)
    price_col = "close" if "close" in combined.columns else prices_df.columns[0]

    combined["ret"] = combined[price_col].pct_change().fillna(0.0)

    windows = []
    cursor = 0
    while cursor + insample_days + oos_days <= n:
        oos_start = cursor + insample_days
        oos_end = oos_start + oos_days

        oos_slice = combined.iloc[oos_start:oos_end]
        oos_signals = oos_slice["signal"].values
        oos_returns = oos_slice["ret"].values

        # Position return: only hold when signal=1
        strategy_returns = np.where(oos_signals == 1, oos_returns, 0.0)

        oos_dates = oos_slice.index
        period_result: dict = {
            "insample_start": str(combined.index[cursor].date()),
            "insample_end": str(combined.index[oos_start - 1].date()),
            "oos_start": str(oos_dates[0].date()),
            "oos_end": str(oos_dates[-1].date()),
            "n_trades": int((oos_signals == 1).sum()),
            "oos_total_return_pct": round(float(strategy_returns.sum() * 100), 4),
        }

        if strategy_returns.std(ddof=1) > 1e-10:
            metrics = calculate_deflated_sharpe_ratio(
                strategy_returns, benchmark_sr=0.5, n_trials=1
            )
            period_result["metrics"] = metrics
        else:
            period_result["metrics"] = None

        windows.append(period_result)
        cursor += oos_days  # strict walk-forward: no overlap

    all_oos_returns = np.concatenate([
        np.where(
            combined.iloc[
                (i * oos_days + insample_days):(i * oos_days + insample_days + oos_days)
            ]["signal"].values == 1,
            combined.iloc[
                (i * oos_days + insample_days):(i * oos_days + insample_days + oos_days)
            ]["ret"].values,
            0.0,
        )
        for i in range(len(windows))
    ]) if windows else np.array([])

    aggregate = None
    if len(all_oos_returns) > 3 and all_oos_returns.std(ddof=1) > 1e-10:
        aggregate = calculate_deflated_sharpe_ratio(
            all_oos_returns,
            benchmark_sr=0.5,
            n_trials=len(windows),  # each window is one implicit trial
        )

    return {
        "windows": windows,
        "aggregate": aggregate,
        "n_windows": len(windows),
    }
