"""Forward return label builder — no lookahead guarantee."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRANSACTION_COST = 0.0070  # 0.15+0.25+0.10+0.10+0.10 pct (round-trip)
TAU_H: dict[int, float] = {5: 0.010, 10: 0.015, 20: 0.025}
_LOG_COST = math.log(1 + TRANSACTION_COST)


def build_labels(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Add label columns to df and remove raw forward price columns.

    Required input columns: 'close', f'close_t{horizon}'.
    Optional: f'high_t{k}' and f'low_t{k}' for k in 1..horizon (path-dependent labels).

    Added columns:
      r_net_h        = ln(close_t+h / close_t) - ln(1 + TRANSACTION_COST)
      y_up           = 1 if r_net_h > TAU_H[horizon]
      y_target_hit   = 1 if max intraperiod return >= tau before stop hit (terminal fallback)
      y_stop_hit     = 1 if min intraperiod return <= -tau before target hit (terminal fallback)
      sigma_realized = sqrt(252/h * sum(r_t+k^2, k=1..h))  -- None when intraperiod returns unavailable

    Intentional v1 disable: tau_target == tau_stop → both path labels = 0.
    These columns are retained for schema compatibility but are not classifier
    targets until differentiated barriers receive separate calibration.
    Removes all close_t+{h} columns after computing labels (no-lookahead guarantee).
    """
    fwd_col = f"close_t{horizon}"
    if fwd_col not in df.columns:
        raise ValueError(f"Missing forward price column: {fwd_col}")

    close = df["close"]
    fwd_close = df[fwd_col]

    df = df.copy()
    df["r_net_h"] = np.log(fwd_close / close) - _LOG_COST

    tau = TAU_H.get(horizon, 0.015)
    df["y_up"] = (df["r_net_h"] > tau).astype(int)

    df = _build_path_labels(df, horizon, tau)
    df = _build_realized_vol(df, horizon)

    # Drop all forward price columns — no lookahead
    fwd_cols = [c for c in df.columns if c.startswith("close_t")]
    df = df.drop(columns=fwd_cols, errors="ignore")

    return df


def _build_path_labels(df: pd.DataFrame, horizon: int, tau: float) -> pd.DataFrame:
    """Build y_target_hit / y_stop_hit.

    Uses path-dependent high/low when available; falls back to terminal return.
    Intentional v1 disable: symmetric barriers leave both labels at zero.
    ForecastingService uses return/volatility probabilities instead of these
    uncalibrated path heads.
    """
    df["y_target_hit"] = 0
    df["y_stop_hit"] = 0

    # Tie rule: equal thresholds → skip all rows
    tau_target = tau
    tau_stop = tau
    if abs(tau_target - tau_stop) < 1e-9:
        return df

    high_cols = [f"high_t{k}" for k in range(1, horizon + 1)]
    low_cols = [f"low_t{k}" for k in range(1, horizon + 1)]
    has_path = all(c in df.columns for c in high_cols + low_cols)

    for i in range(len(df)):
        row = df.iloc[i]
        close_val = float(row["close"])
        if close_val <= 0:
            continue

        if has_path:
            target_price = close_val * (1 + tau_target)
            stop_price = close_val * (1 - tau_stop)

            # First-touch: scan bars left to right
            target_hit_bar = None
            stop_hit_bar = None
            for k in range(1, horizon + 1):
                h_val = row.get(f"high_t{k}", float("nan"))
                l_val = row.get(f"low_t{k}", float("nan"))
                if target_hit_bar is None and not math.isnan(h_val) and h_val >= target_price:
                    target_hit_bar = k
                if stop_hit_bar is None and not math.isnan(l_val) and l_val <= stop_price:
                    stop_hit_bar = k

            if target_hit_bar is not None and stop_hit_bar is None:
                df.iloc[i, df.columns.get_loc("y_target_hit")] = 1
            elif stop_hit_bar is not None and target_hit_bar is None:
                df.iloc[i, df.columns.get_loc("y_stop_hit")] = 1
            # Both hit (or both None): conservative → leave both 0
        else:
            # Terminal approximation
            r_net = float(row.get("r_net_h", 0.0))
            log_target = math.log(1 + tau_target) - _LOG_COST
            log_stop = -(math.log(1 + tau_stop) + _LOG_COST)
            if r_net > log_target:
                df.iloc[i, df.columns.get_loc("y_target_hit")] = 1
            elif r_net < log_stop:
                df.iloc[i, df.columns.get_loc("y_stop_hit")] = 1

    return df


def _build_realized_vol(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Annualized realized volatility over the forward horizon."""
    ret_cols = [f"ret_t{k}" for k in range(1, horizon + 1)]
    if not all(c in df.columns for c in ret_cols):
        df["sigma_realized"] = None
        return df

    def _rv(row: pd.Series) -> float | None:
        rets = [float(row[c]) for c in ret_cols if not math.isnan(float(row.get(c, float("nan"))))]
        if len(rets) < 2:
            return None
        return math.sqrt(252.0 / horizon * sum(r * r for r in rets))

    df["sigma_realized"] = df.apply(_rv, axis=1)
    return df
