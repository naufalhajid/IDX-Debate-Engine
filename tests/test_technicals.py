"""Unit tests for utils/technicals.py — Tasks 19, 20, 25, 26."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from utils.technicals import (
    compute_volume_profile,
    compute_vwap,
    detect_flag_pattern,
    get_time_of_day_signal,
)

_WIB = timezone(timedelta(hours=7))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wib(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_WIB)


def _ohlcv(n: int, close_val: float, vol: float = 1000.0) -> tuple:
    """Return (high, low, close, volume) Series of length n with uniform values."""
    high   = pd.Series([close_val * 1.05] * n)
    low    = pd.Series([close_val * 0.95] * n)
    close  = pd.Series([close_val] * n)
    volume = pd.Series([vol] * n)
    return high, low, close, volume


# ── Task 19: compute_vwap ─────────────────────────────────────────────────────

def test_compute_vwap_insufficient_data_below_window():
    h, l, c, v = _ohlcv(10, 100.0)
    result = compute_vwap(h, l, c, v, window=20)
    assert result["vwap"] is None
    assert result["vwap_position"] == "INSUFFICIENT_DATA"
    assert result["price_to_vwap_pct"] is None


def test_compute_vwap_above_vwap():
    # Uniform bars at 100 → VWAP = 100; last bar close overridden to 105 (+5%)
    h, l, c, v = _ohlcv(25, 100.0)
    c.iloc[-1] = 105.0  # 5% above VWAP
    result = compute_vwap(h, l, c, v)
    assert result["vwap_position"] == "ABOVE_VWAP"
    assert result["price_to_vwap_pct"] is not None and result["price_to_vwap_pct"] > 1.0


def test_compute_vwap_below_vwap():
    h, l, c, v = _ohlcv(25, 100.0)
    c.iloc[-1] = 95.0  # 5% below VWAP
    result = compute_vwap(h, l, c, v)
    assert result["vwap_position"] == "BELOW_VWAP"
    assert result["price_to_vwap_pct"] is not None and result["price_to_vwap_pct"] < -1.0


def test_compute_vwap_at_vwap_within_band():
    h, l, c, v = _ohlcv(25, 100.0)
    # price == VWAP → price_to_vwap_pct == 0 → AT_VWAP
    result = compute_vwap(h, l, c, v)
    assert result["vwap_position"] == "AT_VWAP"
    assert result["price_to_vwap_pct"] == 0.0


def test_compute_vwap_ignores_zero_volume_bars():
    # First 5 bars have zero volume — should not affect VWAP correctness
    h, l, c, v = _ohlcv(25, 100.0)
    v.iloc[:5] = 0.0
    result = compute_vwap(h, l, c, v)
    assert result["vwap"] is not None
    assert result["vwap_position"] == "AT_VWAP"


def test_compute_vwap_returns_rounded_float():
    h, l, c, v = _ohlcv(25, 1234.5)
    result = compute_vwap(h, l, c, v)
    assert isinstance(result["vwap"], float)


# ── Task 25: detect_flag_pattern ─────────────────────────────────────────────

def _flag_series(
    pole_pct: float,
    flag_range_pct: float,
    pole_vol: float = 1000.0,
    flag_vol: float = 700.0,
    pole_window: int = 10,
    flag_window: int = 5,
) -> tuple[pd.Series, pd.Series]:
    """Build close + volume Series exhibiting a pole + flag structure."""
    base = 1000.0
    pole_end = base * (1 + pole_pct / 100)
    flag_mid = pole_end

    pole_prices = [base + (pole_end - base) * i / (pole_window - 1) for i in range(pole_window)]
    half = flag_range_pct / 100 / 2
    flag_prices = [flag_mid * (1 + half if i % 2 == 0 else 1 - half) for i in range(flag_window)]

    close  = pd.Series(pole_prices + flag_prices)
    volume = pd.Series([pole_vol] * pole_window + [flag_vol] * flag_window)
    return close, volume


def test_detect_flag_insufficient_data():
    c = pd.Series([100.0] * 5)
    v = pd.Series([1000.0] * 5)
    result = detect_flag_pattern(c, v)  # needs pole_window+flag_window=15 bars
    assert result["flag_pattern"] == "NONE"
    assert result["flag_confidence"] == "NONE"
    assert result["pole_pct"] is None


def test_detect_bull_flag_high_confidence():
    close, volume = _flag_series(pole_pct=6.0, flag_range_pct=4.0, pole_vol=1000.0, flag_vol=700.0)
    result = detect_flag_pattern(close, volume)
    assert result["flag_pattern"] == "BULL_FLAG"
    assert result["flag_confidence"] == "HIGH"
    assert result["pole_pct"] is not None and result["pole_pct"] > 0


def test_detect_bull_flag_medium_when_volume_not_declining():
    # flag_vol/pole_vol = 0.9 → not declining → MEDIUM
    close, volume = _flag_series(pole_pct=6.0, flag_range_pct=4.0, pole_vol=1000.0, flag_vol=900.0)
    result = detect_flag_pattern(close, volume)
    assert result["flag_pattern"] == "BULL_FLAG"
    assert result["flag_confidence"] == "MEDIUM"


def test_detect_bear_flag_high_confidence():
    close, volume = _flag_series(pole_pct=-6.0, flag_range_pct=4.0, pole_vol=1000.0, flag_vol=700.0)
    result = detect_flag_pattern(close, volume)
    assert result["flag_pattern"] == "BEAR_FLAG"
    assert result["flag_confidence"] == "HIGH"
    assert result["pole_pct"] is not None and result["pole_pct"] < 0


def test_detect_flag_none_when_pole_too_small():
    # 4% pole < 5% minimum → NONE
    close, volume = _flag_series(pole_pct=4.0, flag_range_pct=4.0)
    result = detect_flag_pattern(close, volume)
    assert result["flag_pattern"] == "NONE"


def test_detect_flag_none_when_flag_too_wide():
    # flag range 7% > 5% threshold → not tight → NONE
    close, volume = _flag_series(pole_pct=6.0, flag_range_pct=7.0)
    result = detect_flag_pattern(close, volume)
    assert result["flag_pattern"] == "NONE"


# ── Task 26: get_time_of_day_signal ──────────────────────────────────────────
# Using a known Monday: 2026-06-23

def test_time_of_day_optimal_morning_session():
    now = _wib(2026, 6, 23, 10, 0)  # 10:00 Mon — SESSION_1
    result = get_time_of_day_signal(now)
    assert result["idx_session"] == "SESSION_1"
    assert result["entry_window"] == "OPTIMAL"


def test_time_of_day_suboptimal_session1_open():
    now = _wib(2026, 6, 23, 9, 15)  # 09:15 Mon — first 30 min
    result = get_time_of_day_signal(now)
    assert result["idx_session"] == "SESSION_1_OPEN"
    assert result["entry_window"] == "SUBOPTIMAL"


def test_time_of_day_avoid_midday_break():
    now = _wib(2026, 6, 23, 13, 0)  # 13:00 Mon — break
    result = get_time_of_day_signal(now)
    assert result["idx_session"] == "BREAK"
    assert result["entry_window"] == "AVOID"


def test_time_of_day_optimal_afternoon_session():
    now = _wib(2026, 6, 23, 14, 30)  # 14:30 Mon — SESSION_2
    result = get_time_of_day_signal(now)
    assert result["idx_session"] == "SESSION_2"
    assert result["entry_window"] == "OPTIMAL"


def test_time_of_day_avoid_closing_window():
    now = _wib(2026, 6, 23, 15, 20)  # 15:20 Mon — SESSION_2_CLOSING
    result = get_time_of_day_signal(now)
    assert result["idx_session"] == "SESSION_2_CLOSING"
    assert result["entry_window"] == "AVOID"


def test_time_of_day_avoid_weekend():
    now = _wib(2026, 6, 21, 10, 0)  # Sunday (2026-06-21)
    result = get_time_of_day_signal(now)
    assert result["idx_session"] == "MARKET_CLOSED"
    assert result["entry_window"] == "AVOID"


def test_time_of_day_avoid_after_close():
    now = _wib(2026, 6, 23, 17, 0)  # 17:00 Mon — after close
    result = get_time_of_day_signal(now)
    assert result["idx_session"] == "AFTER_CLOSE"
    assert result["entry_window"] == "AVOID"


def test_time_of_day_accepts_naive_datetime():
    # Naive datetime (no tzinfo) — function attaches _WIB and must not raise
    naive = datetime(2026, 6, 23, 10, 0)
    result = get_time_of_day_signal(naive)
    assert result["entry_window"] in ("OPTIMAL", "SUBOPTIMAL", "AVOID")


def test_time_of_day_result_has_required_keys():
    result = get_time_of_day_signal(_wib(2026, 6, 23, 10, 0))
    assert {"idx_session", "entry_window", "entry_rationale"} <= result.keys()
    assert result["entry_rationale"]  # non-empty string


# ── Task 20: compute_volume_profile ──────────────────────────────────────────

def _vp_series(n: int, prices: list[float], vols: list[float]) -> tuple:
    """Build (high, low, close, volume) with each bar spanning ±2% of its price."""
    c = pd.Series(prices[:n])
    h = c * 1.02
    l = c * 0.98
    v = pd.Series(vols[:n])
    return h, l, c, v


def test_volume_profile_insufficient_data():
    h, l, c, v = _ohlcv(30, 1000.0)
    result = compute_volume_profile(h, l, c, v, window=60)
    assert result["poc"] is None
    assert result["price_vs_poc"] == "INSUFFICIENT_DATA"
    assert result["hvn_levels"] == []
    assert result["lvn_levels"] == []


def test_volume_profile_returns_required_keys():
    h, l, c, v = _ohlcv(60, 1000.0)
    result = compute_volume_profile(h, l, c, v)
    assert {"poc", "poc_distance_pct", "price_vs_poc", "hvn_levels", "lvn_levels"} <= result.keys()


def test_volume_profile_poc_is_at_heavy_volume_zone():
    # 60 bars at 1000 with large volume, then last 10 bars at 1200 with tiny volume
    prices = [1000.0] * 50 + [1200.0] * 10
    vols   = [10_000.0] * 50 + [100.0] * 10
    h, l, c, v = _vp_series(60, prices, vols)
    result = compute_volume_profile(h, l, c, v)
    assert result["poc"] is not None
    # POC should be near 1000, well below current price 1200
    assert result["price_vs_poc"] == "ABOVE_POC"
    assert result["poc_distance_pct"] is not None and result["poc_distance_pct"] > 1.0


def test_volume_profile_above_poc_when_price_above():
    # Uniform bars at 1000; last bar close bumped to 1100 → price above all typical prices
    h, l, c, v = _ohlcv(60, 1000.0)
    c.iloc[-1] = 1100.0
    h.iloc[-1] = 1100.0 * 1.05
    l.iloc[-1] = 1100.0 * 0.95
    result = compute_volume_profile(h, l, c, v)
    assert result["price_vs_poc"] == "ABOVE_POC"


def test_volume_profile_at_poc_uniform_bars():
    # Uniform price bars → all bars hit same bucket → POC ≈ current price
    h, l, c, v = _ohlcv(60, 1000.0)
    result = compute_volume_profile(h, l, c, v)
    # With all bars identical, POC should be at or very near 1000
    assert result["price_vs_poc"] == "AT_POC"
    assert result["poc_distance_pct"] == 0.0 or abs(result["poc_distance_pct"]) <= 1.0


def test_volume_profile_excludes_zero_volume_bars():
    h, l, c, v = _ohlcv(60, 1000.0)
    v.iloc[:10] = 0.0  # first 10 bars have zero volume
    result = compute_volume_profile(h, l, c, v)
    assert result["poc"] is not None  # should still compute from remaining 50 bars


def test_volume_profile_hvn_lvn_are_lists():
    h, l, c, v = _ohlcv(60, 1000.0)
    result = compute_volume_profile(h, l, c, v)
    assert isinstance(result["hvn_levels"], list)
    assert isinstance(result["lvn_levels"], list)
    assert len(result["hvn_levels"]) <= 3
    assert len(result["lvn_levels"]) <= 2
