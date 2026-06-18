"""
utils/technicals.py — Shared technical analysis utilities for IHSG stock analysis.

Provides deterministic, Python-computed indicators so that LLM agents
never need to calculate them — they only interpret.
"""

import math
import pandas as pd

# Stop-loss ATR multiplier keyed on core.regime.RegimeType ("DEFENSIVE", "RECOVERY",
# "HIGH", "NORMAL", "LOW"). Values are deliberately unchanged from the pre-fix
# behavior (DEFENSIVE=3.0, everything else fell through to the 2.5 default) — this
# only makes the existing fallback explicit and correct for the real regime
# taxonomy. Differentiating RECOVERY/HIGH/LOW from NORMAL is a separate,
# evidence-gated calibration decision, not bundled here.
REGIME_ATR_STOP_MULTIPLIER: dict[str, float] = {
    "LOW": 2.5,
    "NORMAL": 2.5,
    "HIGH": 2.5,
    "RECOVERY": 2.5,
    "DEFENSIVE": 3.0,
}
REGIME_ATR_STOP_MULTIPLIER_DEFAULT: float = 2.5


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


def compute_swing_low(low: pd.Series, window: int = 20) -> float:
    """Minimum of the low series over the last `window` bars — structural support proxy.

    Used as the anchor for stop-loss placement. Taking the minimum (most conservative)
    ensures the stop sits below all recent lows, not just near a moving average.
    """
    tail = low.tail(window) if len(low) >= window else low
    return float(tail.min())


# ── Task 1: MACD ─────────────────────────────────────────────────────────────

def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict:
    """MACD line, signal line, histogram, and 4-state histogram classification.

    Uses adjust=False (Wilder-compatible) consistent with compute_rsi().
    States: POSITIVE_EXPANDING | POSITIVE_SHRINKING |
            NEGATIVE_EXPANDING | NEGATIVE_SHRINKING | INSUFFICIENT_DATA
    """
    if len(close) < slow + signal:
        return {
            "macd_line": None,
            "signal_line": None,
            "histogram": None,
            "histogram_state": "INSUFFICIENT_DATA",
        }

    ema_fast  = close.ewm(span=fast, adjust=False).mean()
    ema_slow  = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_ln = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_ln

    hist_now  = histogram.iloc[-1]
    hist_prev = histogram.iloc[-2]

    if hist_now > 0 and hist_now > hist_prev:
        state = "POSITIVE_EXPANDING"
    elif hist_now > 0 and hist_now <= hist_prev:
        state = "POSITIVE_SHRINKING"
    elif hist_now < 0 and hist_now < hist_prev:
        state = "NEGATIVE_EXPANDING"
    else:
        state = "NEGATIVE_SHRINKING"

    return {
        "macd_line":       round(float(macd_line.iloc[-1]), 4),
        "signal_line":     round(float(signal_ln.iloc[-1]), 4),
        "histogram":       round(float(hist_now), 4),
        "histogram_state": state,
    }


# ── Task 3: Candlestick Pattern Detector ─────────────────────────────────────

def detect_candlestick_pattern(
    ohlcv_df: pd.DataFrame,
    lookback: int = 2,
) -> dict:
    """Detect single- and two-candle reversal/continuation patterns.

    Requires columns: Open, High, Low, Close (capitalized, yfinance format).
    lookback is accepted for API symmetry but detection always uses the last 2 bars.
    """
    if len(ohlcv_df) < 2:
        return {"last_candle_pattern": "INSUFFICIENT_DATA", "pattern_type": "NONE"}

    df = ohlcv_df.tail(2).copy()
    o0, h0, l0, c0 = (
        float(df["Open"].iloc[-1]),
        float(df["High"].iloc[-1]),
        float(df["Low"].iloc[-1]),
        float(df["Close"].iloc[-1]),
    )
    o1, c1 = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])

    body0        = abs(c0 - o0)
    upper_wick0  = h0 - max(o0, c0)
    lower_wick0  = min(o0, c0) - l0
    total_range0 = h0 - l0

    if total_range0 == 0:
        return {"last_candle_pattern": "doji", "pattern_type": "NEUTRAL"}

    body_ratio0 = body0 / total_range0

    if lower_wick0 > 2 * body0 and upper_wick0 < 0.3 * total_range0 and body_ratio0 < 0.4:
        return {"last_candle_pattern": "hammer", "pattern_type": "BULLISH_REVERSAL"}

    if upper_wick0 > 2 * body0 and lower_wick0 < 0.3 * total_range0 and body_ratio0 < 0.4:
        return {"last_candle_pattern": "shooting_star", "pattern_type": "BEARISH_REVERSAL"}

    if body_ratio0 < 0.05:
        return {"last_candle_pattern": "doji", "pattern_type": "NEUTRAL"}

    if c0 > o0 and body_ratio0 > 0.8:
        return {"last_candle_pattern": "bullish_marubozu", "pattern_type": "BULLISH_CONTINUATION"}

    if c0 < o0 and body_ratio0 > 0.8:
        return {"last_candle_pattern": "bearish_marubozu", "pattern_type": "BEARISH_CONTINUATION"}

    # Two-candle patterns
    if c1 < o1 and c0 > o0 and o0 < c1 and c0 > o1:
        return {"last_candle_pattern": "bullish_engulfing", "pattern_type": "BULLISH_REVERSAL"}

    if c1 > o1 and c0 < o0 and o0 > c1 and c0 < o1:
        return {"last_candle_pattern": "bearish_engulfing", "pattern_type": "BEARISH_REVERSAL"}

    return {"last_candle_pattern": "no_pattern", "pattern_type": "NEUTRAL"}


# ── Task 4: Bollinger Band + Squeeze Detector ────────────────────────────────

def compute_bollinger(
    close: pd.Series,
    period: int = 20,
    std_mult: float = 2.0,
    squeeze_lookback: int = 50,
) -> dict:
    """Bollinger Bands with squeeze detection.

    bb_squeeze = True when current band width is below the 20th percentile
    of the last squeeze_lookback bars — signals volatility contraction.
    """
    if len(close) < period:
        return {
            "bb_upper": None, "bb_lower": None, "bb_middle": None,
            "bb_width": None, "bb_squeeze": False,
            "bb_position": "INSUFFICIENT_DATA",
        }

    rolling_mean = close.rolling(period).mean()
    rolling_std  = close.rolling(period).std()
    bb_upper = rolling_mean + (std_mult * rolling_std)
    bb_lower = rolling_mean - (std_mult * rolling_std)

    curr_upper  = float(bb_upper.iloc[-1])
    curr_lower  = float(bb_lower.iloc[-1])
    curr_middle = float(rolling_mean.iloc[-1])
    curr_price  = float(close.iloc[-1])
    curr_width  = (curr_upper - curr_lower) / curr_middle

    width_series = (bb_upper - bb_lower) / rolling_mean
    available    = width_series.dropna().tail(squeeze_lookback)
    is_squeeze   = bool(
        len(available) >= 20
        and curr_width < float(available.quantile(0.20))
    )

    if curr_price >= curr_upper:
        position = "ABOVE_UPPER"
    elif curr_price <= curr_lower:
        position = "BELOW_LOWER"
    elif curr_price > curr_middle:
        position = "UPPER_HALF"
    else:
        position = "LOWER_HALF"

    return {
        "bb_upper":    round(curr_upper, 2),
        "bb_lower":    round(curr_lower, 2),
        "bb_middle":   round(curr_middle, 2),
        "bb_width":    round(curr_width * 100, 2),  # as percentage
        "bb_squeeze":  is_squeeze,
        "bb_position": position,
    }


# ── Task 5: RSI Divergence Detector ──────────────────────────────────────────

def detect_rsi_divergence(
    close: pd.Series,
    rsi: pd.Series,
    lookback: int = 20,
    min_pivot_separation: int = 5,
) -> dict:
    """Detect bullish (price LL, RSI HL) and bearish (price HH, RSI LH) divergence.

    Requires ~15+ bars with two pivot points separated by at least
    min_pivot_separation candles for reliable detection.
    """
    if len(close) < lookback or len(rsi) < lookback:
        return {"rsi_divergence": "NONE", "divergence_strength": None}

    price_segment = close.tail(lookback).reset_index(drop=True)
    rsi_segment   = rsi.tail(lookback).reset_index(drop=True)

    def find_local_lows(series: pd.Series, window: int = 3) -> list[int]:
        return [
            i for i in range(window, len(series) - window)
            if series.iloc[i] == series.iloc[i - window: i + window + 1].min()
        ]

    def find_local_highs(series: pd.Series, window: int = 3) -> list[int]:
        return [
            i for i in range(window, len(series) - window)
            if series.iloc[i] == series.iloc[i - window: i + window + 1].max()
        ]

    price_lows  = find_local_lows(price_segment)
    price_highs = find_local_highs(price_segment)

    # Bullish divergence: price makes lower low, RSI makes higher low
    if len(price_lows) >= 2:
        i1, i2 = price_lows[-2], price_lows[-1]
        if (i2 - i1) >= min_pivot_separation:
            if (
                price_segment.iloc[i2] < price_segment.iloc[i1]
                and rsi_segment.iloc[i2] > rsi_segment.iloc[i1]
            ):
                return {
                    "rsi_divergence":     "BULLISH",
                    "divergence_strength": round(
                        float(rsi_segment.iloc[i2] - rsi_segment.iloc[i1]), 2
                    ),
                }

    # Bearish divergence: price makes higher high, RSI makes lower high
    if len(price_highs) >= 2:
        i1, i2 = price_highs[-2], price_highs[-1]
        if (i2 - i1) >= min_pivot_separation:
            if (
                price_segment.iloc[i2] > price_segment.iloc[i1]
                and rsi_segment.iloc[i2] < rsi_segment.iloc[i1]
            ):
                return {
                    "rsi_divergence":     "BEARISH",
                    "divergence_strength": round(
                        float(rsi_segment.iloc[i1] - rsi_segment.iloc[i2]), 2
                    ),
                }

    return {"rsi_divergence": "NONE", "divergence_strength": None}


# ── Task 12: Gap Analysis + Inside Bar / NR7 ─────────────────────────────────

def detect_gap(ohlcv_df: pd.DataFrame, min_gap_pct: float = 0.015) -> dict:
    """Classify the opening gap between today's open and yesterday's close/range.

    GAP_UP/DOWN = open is outside the prior candle's High/Low range.
    PARTIAL_GAP = open is inside prior range but above/below prior close by > min_gap_pct.
    """
    if len(ohlcv_df) < 2:
        return {"gap_type": "NONE", "gap_pct": 0.0}

    today_open = float(ohlcv_df["Open"].iloc[-1])
    prev_close = float(ohlcv_df["Close"].iloc[-2])
    prev_high  = float(ohlcv_df["High"].iloc[-2])
    prev_low   = float(ohlcv_df["Low"].iloc[-2])

    gap_pct = (today_open - prev_close) / prev_close

    if today_open > prev_high and gap_pct > min_gap_pct:
        gap_type = "GAP_UP"
    elif today_open < prev_low and gap_pct < -min_gap_pct:
        gap_type = "GAP_DOWN"
    elif gap_pct > min_gap_pct:
        gap_type = "PARTIAL_GAP_UP"
    elif gap_pct < -min_gap_pct:
        gap_type = "PARTIAL_GAP_DOWN"
    else:
        gap_type = "NONE"

    return {"gap_type": gap_type, "gap_pct": round(gap_pct * 100, 2)}


def detect_volatility_compression(ohlcv_df: pd.DataFrame, nr_days: int = 7) -> dict:
    """Detect volatility compression patterns: NR7 (narrowest range in 7 days) and inside bar.

    NR7 and inside bar signal energy accumulation — breakout imminent.
    Wait for volume confirmation before entry.
    """
    if len(ohlcv_df) < nr_days:
        return {"compression_type": "INSUFFICIENT_DATA", "range_pct": None, "is_inside_bar": False, "is_nr7": False}

    today     = ohlcv_df.iloc[-1]
    yesterday = ohlcv_df.iloc[-2]
    today_range     = float(today["High"] - today["Low"])
    today_range_pct = today_range / float(today["Close"])

    last_7_ranges = (ohlcv_df["High"] - ohlcv_df["Low"]).tail(nr_days)
    is_nr7    = bool(today_range == float(last_7_ranges.min()))
    is_inside = bool(
        float(today["High"]) < float(yesterday["High"])
        and float(today["Low"]) > float(yesterday["Low"])
    )

    if is_nr7:
        ctype = "NR7"
    elif is_inside:
        ctype = "INSIDE_BAR"
    else:
        ctype = "NONE"

    return {
        "compression_type": ctype,
        "range_pct":        round(today_range_pct * 100, 2),
        "is_inside_bar":    is_inside,
        "is_nr7":           is_nr7,
    }


def validate_ohlcv(
    df: "pd.DataFrame | None",
    ticker: str = "",
    min_rows: int = 30,
) -> tuple[bool, str]:
    """Return (True, '') if df is a usable OHLCV DataFrame, else (False, reason).

    Checks that matter beyond 'not empty': all-NaN close (price feed gap) and
    all-zero volume (possible suspended/FCA stock) are silent failures without this.
    """
    if df is None:
        return False, f"[{ticker}] OHLCV is None"
    if not isinstance(df, pd.DataFrame) or df.empty:
        return False, f"[{ticker}] OHLCV is empty"
    required = {"High", "Low", "Close", "Volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        return False, f"[{ticker}] missing columns: {', '.join(missing)}"
    if len(df) < min_rows:
        return False, f"[{ticker}] only {len(df)} rows (< {min_rows})"
    if df["Close"].isna().all():
        return False, f"[{ticker}] Close is all-NaN (price feed gap)"
    if (df["Volume"].fillna(0) == 0).all():
        return False, f"[{ticker}] Volume is all-zero (possible suspended/FCA)"
    return True, ""
