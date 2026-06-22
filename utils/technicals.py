"""
utils/technicals.py — Shared technical analysis utilities for IHSG stock analysis.

Provides deterministic, Python-computed indicators so that LLM agents
never need to calculate them — they only interpret.
"""

import math
from datetime import datetime, timedelta, timezone

import pandas as pd

_WIB = timezone(timedelta(hours=7))

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
    return tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()


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
    rolling_std  = close.rolling(period).std(ddof=0)
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


# ── Task 19: Rolling VWAP ─────────────────────────────────────────────────────

def compute_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 20,
) -> dict:
    """Rolling 20-day VWAP (Typical Price × Volume weighted) — institutional price benchmark.

    Uses a rolling window on daily bars because true session VWAP requires intraday ticks.
    A 20-day rolling VWAP is the standard IDX institutional benchmark used for VWAP execution.
    """
    if len(close) < window:
        return {"vwap": None, "vwap_position": "INSUFFICIENT_DATA", "price_to_vwap_pct": None}

    typical = (high + low + close) / 3
    vol_clean = volume.where(volume > 0, other=float("nan"))  # exclude zero-volume bars
    tp_vol = typical * vol_clean

    rolling_tpv = tp_vol.rolling(window).sum()
    rolling_vol = vol_clean.rolling(window).sum()

    vwap_series = rolling_tpv / rolling_vol
    vwap_now = float(vwap_series.iloc[-1])
    price_now = float(close.iloc[-1])

    if not math.isfinite(vwap_now) or vwap_now <= 0:
        return {"vwap": None, "vwap_position": "INSUFFICIENT_DATA", "price_to_vwap_pct": None}

    pct_diff = (price_now - vwap_now) / vwap_now * 100

    if pct_diff > 1.0:
        position = "ABOVE_VWAP"
    elif pct_diff < -1.0:
        position = "BELOW_VWAP"
    else:
        position = "AT_VWAP"

    return {
        "vwap": round(vwap_now, 0),
        "vwap_position": position,
        "price_to_vwap_pct": round(pct_diff, 1),
    }


# ── FV-1: Anchored VWAP ───────────────────────────────────────────────────────

def compute_anchored_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    lookback: int = 60,
    min_bars_after_anchor: int = 5,
) -> dict:
    """VWAP anchored from the most recent swing low within `lookback` bars.

    Unlike rolling VWAP which resets every N bars, anchored VWAP accumulates
    from a structural price event (the swing low). Price below AVWAP means
    all buyers since that swing low are underwater on average — bearish for
    continuation. Price above AVWAP confirms cost-basis support below.

    Returns avwap, avwap_position (ABOVE/AT/BELOW_AVWAP | INSUFFICIENT_DATA),
    price_to_avwap_pct, and anchor_bars_ago (how long since the anchor event).
    """
    _empty: dict = {
        "avwap": None,
        "avwap_position": "INSUFFICIENT_DATA",
        "price_to_avwap_pct": None,
        "anchor_bars_ago": None,
    }
    if len(close) < min_bars_after_anchor + 1:
        return _empty

    # Anchor at the lowest low within the lookback window
    tail_len = min(lookback, len(low))
    window_start = len(low) - tail_len
    anchor_idx = int(low.iloc[window_start:].argmin()) + window_start

    bars_after_anchor = len(close) - 1 - anchor_idx
    if bars_after_anchor < min_bars_after_anchor:
        return _empty

    # Cumulative VWAP from anchor bar to now (inclusive)
    vol_slice = volume.iloc[anchor_idx:].where(
        volume.iloc[anchor_idx:] > 0, other=float("nan")
    )
    typical = (high.iloc[anchor_idx:] + low.iloc[anchor_idx:] + close.iloc[anchor_idx:]) / 3
    cum_tpv = float((typical * vol_slice).sum())
    cum_vol = float(vol_slice.sum())

    if not (math.isfinite(cum_vol) and cum_vol > 0 and math.isfinite(cum_tpv)):
        return _empty

    avwap = cum_tpv / cum_vol
    if not math.isfinite(avwap) or avwap <= 0:
        return _empty

    price_now = float(close.iloc[-1])
    pct_diff = (price_now - avwap) / avwap * 100

    if pct_diff > 1.0:
        position = "ABOVE_AVWAP"
    elif pct_diff < -1.0:
        position = "BELOW_AVWAP"
    else:
        position = "AT_AVWAP"

    return {
        "avwap": round(avwap, 0),
        "avwap_position": position,
        "price_to_avwap_pct": round(pct_diff, 1),
        "anchor_bars_ago": bars_after_anchor,
    }


# ── FV-2: Fibonacci Retracement ───────────────────────────────────────────────

_FIB_RATIOS: dict[str, float] = {
    "23.6": 0.236,
    "38.2": 0.382,
    "50.0": 0.500,
    "61.8": 0.618,
    "78.6": 0.786,
}


def compute_fibonacci_levels(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    lookback: int = 60,
    near_threshold_pct: float = 2.0,
    min_range_pct: float = 3.0,
) -> dict:
    """Fibonacci retracement levels from the most recent swing high and swing low.

    Finds structural swing high (argmax of highs) and swing low (argmin of lows)
    within `lookback` bars. Retracement levels (23.6%, 38.2%, 50%, 61.8%, 78.6%)
    are computed as: level = swing_high - ratio × (swing_high - swing_low).

    In an UPTREND (swing low precedes swing high), these levels are support zones
    where price may bounce during a pullback. In a DOWNTREND (swing high precedes
    swing low), these levels act as overhead resistance during a dead-cat bounce.

    Requires a minimum swing range of `min_range_pct`% to avoid noise signals on
    flat or very narrow price ranges.
    """
    _empty: dict = {
        "fib_swing_low": None,
        "fib_swing_high": None,
        "fib_levels": None,
        "nearest_fib_label": None,
        "nearest_fib_price": None,
        "price_to_nearest_fib_pct": None,
        "fib_context": "INSUFFICIENT_DATA",
        "fib_trend": None,
    }

    if len(close) < max(5, lookback // 2):
        return _empty

    tail_len = min(lookback, len(high))
    window_start = len(high) - tail_len

    idx_high = int(high.iloc[window_start:].argmax()) + window_start
    idx_low = int(low.iloc[window_start:].argmin()) + window_start

    swing_high = float(high.iloc[idx_high])
    swing_low = float(low.iloc[idx_low])

    if swing_low <= 0 or swing_high <= swing_low:
        return _empty

    price_range = swing_high - swing_low
    if price_range / swing_low * 100 < min_range_pct:
        return _empty

    # Standard retracement: 0% = swing_high, 100% = swing_low
    fib_levels = {
        label: round(swing_high - ratio * price_range, 0)
        for label, ratio in _FIB_RATIOS.items()
    }

    price_now = float(close.iloc[-1])
    trend = "UPTREND" if idx_low < idx_high else "DOWNTREND"

    if price_now > swing_high:
        return {
            **_empty,
            "fib_swing_low": round(swing_low, 0),
            "fib_swing_high": round(swing_high, 0),
            "fib_levels": fib_levels,
            "fib_context": "ABOVE_SWING_HIGH",
            "fib_trend": trend,
        }

    if price_now < swing_low:
        return {
            **_empty,
            "fib_swing_low": round(swing_low, 0),
            "fib_swing_high": round(swing_high, 0),
            "fib_levels": fib_levels,
            "fib_context": "BELOW_SWING_LOW",
            "fib_trend": trend,
        }

    # Price is within the swing range — find nearest Fib level
    nearest_label = min(fib_levels, key=lambda lbl: abs(price_now - fib_levels[lbl]))
    nearest_price = fib_levels[nearest_label]
    pct_to_nearest = round((price_now - nearest_price) / nearest_price * 100, 1)

    if abs(pct_to_nearest) <= near_threshold_pct:
        context = f"NEAR_{nearest_label.replace('.', '_')}"
    else:
        context = "BETWEEN_LEVELS"

    return {
        "fib_swing_low": round(swing_low, 0),
        "fib_swing_high": round(swing_high, 0),
        "fib_levels": fib_levels,
        "nearest_fib_label": nearest_label,
        "nearest_fib_price": nearest_price,
        "price_to_nearest_fib_pct": pct_to_nearest,
        "fib_context": context,
        "fib_trend": trend,
    }


# ── Task 25: Bull / Bear Flag Pattern ────────────────────────────────────────

def detect_flag_pattern(
    close: pd.Series,
    volume: pd.Series,
    pole_window: int = 10,
    flag_window: int = 5,
    pole_min_pct: float = 5.0,
) -> dict:
    """Detect bull or bear flag: a strong directional pole followed by tight consolidation.

    BULL FLAG: pole rises ≥ pole_min_pct%, consolidation range < 5% of flag mean.
    BEAR FLAG: pole falls ≥ pole_min_pct%, same consolidation criterion.
    Confidence is HIGH when flag volume is also lower than pole volume (classic pattern).
    Requires pole_window + flag_window bars minimum.
    """
    min_bars = pole_window + flag_window
    if len(close) < min_bars:
        return {"flag_pattern": "NONE", "flag_confidence": "NONE", "pole_pct": None}

    flag_close = close.iloc[-flag_window:]
    pole_close = close.iloc[-(pole_window + flag_window) : -flag_window]

    pole_start = float(pole_close.iloc[0])
    pole_end = float(pole_close.iloc[-1])

    if pole_start <= 0:
        return {"flag_pattern": "NONE", "flag_confidence": "NONE", "pole_pct": None}

    pole_pct = (pole_end - pole_start) / pole_start * 100

    flag_mean = float(flag_close.mean())
    flag_range_pct = (
        (float(flag_close.max()) - float(flag_close.min())) / flag_mean * 100
        if flag_mean > 0
        else 999.0
    )
    is_tight = flag_range_pct < 5.0

    flag_vol_avg = float(volume.iloc[-flag_window:].mean())
    pole_vol_avg = float(volume.iloc[-(pole_window + flag_window) : -flag_window].mean())
    volume_declining = pole_vol_avg > 0 and (flag_vol_avg / pole_vol_avg) < 0.8

    if pole_pct >= pole_min_pct and is_tight:
        confidence = "HIGH" if volume_declining else "MEDIUM"
        return {"flag_pattern": "BULL_FLAG", "flag_confidence": confidence, "pole_pct": round(pole_pct, 1)}

    if pole_pct <= -pole_min_pct and is_tight:
        confidence = "HIGH" if volume_declining else "MEDIUM"
        return {"flag_pattern": "BEAR_FLAG", "flag_confidence": confidence, "pole_pct": round(pole_pct, 1)}

    return {"flag_pattern": "NONE", "flag_confidence": "NONE", "pole_pct": None}


# ── Task 26: IDX Time-of-Day Entry Window ────────────────────────────────────

def get_time_of_day_signal(now: datetime | None = None) -> dict:
    """Return the current IDX session and swing-trade entry advisory (WIB = UTC+7).

    IDX regular session boundaries (Mon–Fri):
      Pre-open  : 08:45–09:00   No fills
      Session 1 : 09:00–11:59   Main morning session
      Break     : 12:00–13:29   No fills
      Session 2 : 13:30–15:49   Afternoon session
      Pre-close : 15:49–16:00   Closing auction
    Note: Friday session 2 ends ~15:00 — this function does not model Friday early close
    because the exact minute varies by week; treat as SUBOPTIMAL after 14:45 Fri.
    """
    if now is None:
        now = datetime.now(_WIB)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_WIB)
    else:
        now = now.astimezone(_WIB)

    hhmm = now.hour * 60 + now.minute
    is_weekend = now.weekday() >= 5

    _PRE_OPEN_START  = 8 * 60 + 45   # 08:45
    _S1_START        = 9 * 60         # 09:00
    _S1_EARLY_END    = 9 * 60 + 30   # 09:30
    _S1_PEAK_END     = 11 * 60        # 11:00
    _S1_END          = 12 * 60        # 12:00
    _S2_START        = 13 * 60 + 30  # 13:30
    _S2_EARLY_END    = 14 * 60        # 14:00
    _S2_LATE_START   = 15 * 60        # 15:00
    _S2_END          = 15 * 60 + 49  # 15:49
    _PRE_CLOSE_END   = 16 * 60        # 16:00

    if is_weekend:
        session, window, rationale = (
            "MARKET_CLOSED",
            "AVOID",
            "Market closed (weekend). Plan entries for Monday open.",
        )
    elif hhmm < _PRE_OPEN_START:
        session, window, rationale = (
            "PRE_MARKET", "AVOID", "Before IDX pre-open. Prepare watchlist — no fills available."
        )
    elif hhmm < _S1_START:
        session, window, rationale = (
            "PRE_OPEN", "AVOID", "Pre-open auction. Indicative prices only — no valid fills."
        )
    elif hhmm < _S1_EARLY_END:
        session, window, rationale = (
            "SESSION_1_OPEN",
            "SUBOPTIMAL",
            "First 30 min: spread elevated, gap direction unconfirmed. Wait for trend to establish.",
        )
    elif hhmm < _S1_PEAK_END:
        session, window, rationale = (
            "SESSION_1",
            "OPTIMAL",
            "Best swing entry window: liquidity high, trend direction established (09:30–11:00).",
        )
    elif hhmm < _S1_END:
        session, window, rationale = (
            "SESSION_1_LATE",
            "SUBOPTIMAL",
            "Approaching lunch. Momentum may stall; prefer limit orders only.",
        )
    elif hhmm < _S2_START:
        session, window, rationale = (
            "BREAK", "AVOID", "Midday break (12:00–13:30). No fills. Review morning action."
        )
    elif hhmm < _S2_EARLY_END:
        session, window, rationale = (
            "SESSION_2_OPEN",
            "SUBOPTIMAL",
            "Session 2 open: confirm morning trend continuation before committing.",
        )
    elif hhmm < _S2_LATE_START:
        session, window, rationale = (
            "SESSION_2",
            "OPTIMAL",
            "Good afternoon window: institutional accumulation visible (14:00–15:00).",
        )
    elif hhmm < _S2_END:
        session, window, rationale = (
            "SESSION_2_CLOSING",
            "AVOID",
            "Closing approach (15:00–15:49): avoid new entries — end-of-day selling risk.",
        )
    elif hhmm < _PRE_CLOSE_END:
        session, window, rationale = (
            "PRE_CLOSE", "AVOID", "Pre-close auction (15:49–16:00). Overnight gap risk elevated."
        )
    else:
        session, window, rationale = (
            "AFTER_CLOSE", "AVOID", "Market closed. Entry tomorrow. Monitor overnight news."
        )

    return {"idx_session": session, "entry_window": window, "entry_rationale": rationale}


def compute_volume_profile(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 60,
    bins: int = 20,
) -> dict:
    """Volume Profile using typical-price bucketing on daily OHLCV bars.

    Each bar's volume is attributed to its typical price (H+L+C)/3 — the standard
    approximation for daily bars where tick-level trade records are unavailable.
    Returns POC (Point of Control), HVN (high-volume nodes), and LVN (low-volume nodes).
    """
    _empty: dict = {
        "poc": None,
        "poc_distance_pct": None,
        "price_vs_poc": "INSUFFICIENT_DATA",
        "hvn_levels": [],
        "lvn_levels": [],
    }
    if len(close) < window:
        return _empty

    high_window = high.iloc[-window:]
    low_window = low.iloc[-window:]
    close_window = close.iloc[-window:]
    volume_window = volume.iloc[-window:]

    typical = (high_window + low_window + close_window) / 3
    vol_clean = volume_window.where(volume_window > 0, other=float("nan"))

    p_min = float(typical.min())
    p_max = float(typical.max())
    if not math.isfinite(p_min) or not math.isfinite(p_max):
        return _empty
    if p_max <= p_min:
        # Zero price range — all bars at the same typical price; POC is that price
        price_now = float(close_window.iloc[-1])
        if not math.isfinite(price_now) or price_now <= 0:
            return _empty
        return {
            "poc": round(price_now, 0),
            "poc_distance_pct": 0.0,
            "price_vs_poc": "AT_POC",
            "hvn_levels": [],
            "lvn_levels": [],
        }

    labels = list(range(bins))
    bucket = pd.cut(typical, bins=bins, labels=labels, include_lowest=True)
    bin_vol = vol_clean.groupby(bucket, observed=False).sum().fillna(0.0)

    valid = bin_vol[bin_vol > 0]
    if valid.empty:
        return _empty

    poc_label = int(bin_vol.idxmax())
    bin_width = (p_max - p_min) / bins
    poc_mid = p_min + (poc_label + 0.5) * bin_width

    price_now = float(close_window.iloc[-1])
    if poc_mid <= 0:
        return _empty
    poc_dist = (price_now - poc_mid) / poc_mid * 100

    if poc_dist > 1.0:
        price_vs_poc = "ABOVE_POC"
    elif poc_dist < -1.0:
        price_vs_poc = "BELOW_POC"
    else:
        price_vs_poc = "AT_POC"

    p70 = float(valid.quantile(0.70))
    hvn_mids = sorted(
        [p_min + (i + 0.5) * bin_width for i in range(bins)
         if i != poc_label and bin_vol.iloc[i] >= p70],
        key=lambda p: abs(p - price_now),
    )[:3]

    p30 = float(valid.quantile(0.30))
    lvn_mids = sorted(
        [p_min + (i + 0.5) * bin_width for i in range(bins)
         if 0 < bin_vol.iloc[i] <= p30],
        key=lambda p: abs(p - price_now),
    )[:2]

    return {
        "poc": round(poc_mid, 0),
        "poc_distance_pct": round(poc_dist, 1),
        "price_vs_poc": price_vs_poc,
        "hvn_levels": [round(p, 0) for p in hvn_mids],
        "lvn_levels": [round(p, 0) for p in lvn_mids],
    }


def compute_52w_range_signal(
    current_price: float,
    high_52w: float,
    low_52w: float,
) -> str | None:
    """Return a label describing where current price sits in its 52-week range.

    Labels: NEAR_52W_HIGH (≥80th pct) / ABOVE_MID (≥55) / BELOW_MID (≥25) / NEAR_52W_LOW (<25).
    Returns None when inputs are invalid or the range collapses to zero.
    """
    if not (current_price > 0 and high_52w > 0 and low_52w > 0):
        return None
    rng = high_52w - low_52w
    if rng <= 0:
        return None
    pct = round((current_price - low_52w) / rng * 100.0, 1)
    if pct >= 80.0:
        label = "NEAR_52W_HIGH"
    elif pct >= 55.0:
        label = "ABOVE_MID"
    elif pct >= 25.0:
        label = "BELOW_MID"
    else:
        label = "NEAR_52W_LOW"
    mid = round((high_52w + low_52w) / 2.0, 0)
    return (
        f"52W RANGE: {label} — persentil ke-{pct:.1f} "
        f"(Low Rp {low_52w:,.0f} – High Rp {high_52w:,.0f}, Mid Rp {mid:,.0f})"
    )
