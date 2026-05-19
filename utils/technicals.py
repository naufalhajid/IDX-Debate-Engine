"""
utils/technicals.py — Shared technical analysis utilities for IHSG stock analysis.

Provides deterministic, Python-computed indicators so that LLM agents
never need to calculate them — they only interpret.
"""

import math
import pandas as pd


def compute_rsi(data: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI using Exponential Moving Average.

    The canonical formula uses EMA with alpha = 1/window (Wilder's smoothing),
    not SMA. This matches the RSI displayed on TradingView, Stockbit, etc.
    """
    diff = data.diff(1).dropna()
    gain = diff.where(diff > 0, 0.0)
    loss = -diff.where(diff < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> pd.Series:
    """Average True Range — volatility measure for stop-loss sizing."""
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window).mean()


def snap_to_tick(price: float) -> float:
    """Round price to the nearest valid IHSG tick size.

    IHSG price fraction table (BEI regulation):
        < Rp 200         → Rp 1
        Rp 200 – Rp 500  → Rp 2
        Rp 500 – Rp 2000 → Rp 5
        Rp 2000 – Rp 5000 → Rp 10
        > Rp 5000         → Rp 25
    """
    if price is None or math.isnan(price):
        return 0.0
    if price <= 0:
        return 0.0
    if price < 200:
        return float(round(price))
    elif price < 500:
        return float(round(price / 2) * 2)
    elif price < 2000:
        return float(round(price / 5) * 5)
    elif price < 5000:
        return float(round(price / 10) * 10)
    else:
        return float(round(price / 25) * 25)
