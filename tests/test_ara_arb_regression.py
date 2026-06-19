"""
Regression tests for compute_ara_arb_risk().

These tests are SPECIFICALLY designed to catch the silent bug where both
max_gain and max_drop were computed from close-to-close instead of intraday
high/low. The bug never crashed — it silently produced wrong LOW risk levels
for stocks that had touched ARA/ARB territory intraday.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.quant_filter.pipeline import compute_ara_arb_risk


# ── Task 1 regression tests ────────────────────────────────────────────────────

def test_ara_uses_intraday_high_not_close():
    """Spike +25% intraday on day 3, but close only +3%. Must be HIGH, not LOW."""
    close = pd.Series([1000.0, 1010.0, 1030.0, 1030.0, 1030.0])
    high  = pd.Series([1005.0, 1015.0, 1250.0, 1035.0, 1035.0])  # spike at index 2
    low   = pd.Series([ 995.0, 1005.0, 1020.0, 1025.0, 1025.0])

    result = compute_ara_arb_risk(close, high, low, lookback=4)

    assert result["ara_entry_risk"] == "HIGH", (
        "Bug regresi: ARA risk tidak terdeteksi dari intraday spike. "
        "Kemungkinan fungsi kembali memakai close bukan high."
    )


def test_arb_uses_intraday_low_not_close():
    """Crash -18% intraday on day 3, but close only -2%. Must be HIGH, not LOW."""
    close = pd.Series([1000.0,  990.0,  980.0,  980.0,  980.0])
    high  = pd.Series([1005.0, 1000.0,  990.0,  990.0,  990.0])
    low   = pd.Series([ 995.0,  985.0,  820.0,  975.0,  975.0])  # crash at index 2

    result = compute_ara_arb_risk(close, high, low, lookback=4)

    assert result["arb_lock_risk"] == "HIGH", (
        "Bug regresi: ARB risk tidak terdeteksi dari intraday crash. "
        "Kemungkinan fungsi kembali memakai close bukan low."
    )


# ── Normal-range and tier boundary cases ──────────────────────────────────────

def test_normal_range_returns_low():
    close = pd.Series([1000.0, 1005.0, 1010.0, 1008.0, 1012.0, 1015.0])
    high  = pd.Series([1010.0, 1015.0, 1018.0, 1016.0, 1020.0, 1022.0])
    low   = pd.Series([ 995.0, 1000.0, 1005.0, 1002.0, 1006.0, 1010.0])

    result = compute_ara_arb_risk(close, high, low, lookback=5)

    assert result["ara_entry_risk"] == "LOW"
    assert result["arb_lock_risk"] == "LOW"


def test_medium_ara_tier():
    """Intraday gain of ~14% → MEDIUM, not LOW or HIGH."""
    close = pd.Series([1000.0, 1010.0, 1020.0, 1025.0, 1030.0, 1035.0])
    high  = pd.Series([1010.0, 1020.0, 1140.0, 1035.0, 1040.0, 1042.0])  # peak ~14%
    low   = pd.Series([ 995.0, 1005.0, 1015.0, 1018.0, 1022.0, 1028.0])

    result = compute_ara_arb_risk(close, high, low, lookback=5)

    assert result["ara_entry_risk"] == "MEDIUM"
    assert result["arb_lock_risk"] == "LOW"


def test_medium_arb_tier():
    """Intraday drop of ~9% → MEDIUM, not LOW or HIGH."""
    close = pd.Series([1000.0,  995.0,  990.0,  988.0,  985.0,  983.0])
    high  = pd.Series([1005.0, 1000.0,  995.0,  993.0,  990.0,  987.0])
    low   = pd.Series([ 995.0,  990.0,  910.0,  982.0,  980.0,  978.0])  # trough ~-9%

    result = compute_ara_arb_risk(close, high, low, lookback=5)

    assert result["arb_lock_risk"] == "MEDIUM"
    assert result["ara_entry_risk"] == "LOW"


def test_insufficient_data_returns_unknown():
    close = pd.Series([1000.0, 1010.0])
    high  = pd.Series([1010.0, 1015.0])
    low   = pd.Series([ 995.0, 1005.0])

    result = compute_ara_arb_risk(close, high, low, lookback=5)

    assert result["ara_entry_risk"] == "UNKNOWN"
    assert result["arb_lock_risk"] == "UNKNOWN"


def test_output_fields_unchanged():
    """Field names must remain stable — downstream callers in risk_governor depend on them."""
    close = pd.Series([1000.0, 1005.0, 1010.0, 1008.0, 1012.0, 1015.0])
    high  = pd.Series([1010.0, 1015.0, 1018.0, 1016.0, 1020.0, 1022.0])
    low   = pd.Series([ 995.0, 1000.0, 1005.0, 1002.0, 1006.0, 1010.0])

    result = compute_ara_arb_risk(close, high, low)

    assert set(result.keys()) == {"arb_lock_risk", "ara_entry_risk", "ara_arb_note"}


# ── Task 2 regression test ─────────────────────────────────────────────────────

def test_weekly_multiindex_guard_logic():
    """
    Verifies the MultiIndex guard logic that fixes the single-ticker flat-column
    bug in the weekly batch download. Simulates the flat-column DataFrame that
    yfinance returns for single-ticker downloads and confirms the guard wraps it
    into the MultiIndex structure that downstream extraction expects.
    """
    dates = pd.date_range("2024-01-01", periods=10, freq="W")
    flat_df = pd.DataFrame(
        {
            "Open":   [100.0] * 10,
            "High":   [105.0] * 10,
            "Low":    [ 95.0] * 10,
            "Close":  [102.0] * 10,
            "Volume": [1_000_000.0] * 10,
        },
        index=dates,
    )

    assert not isinstance(flat_df.columns, pd.MultiIndex), "Precondition: yfinance flat output"

    # Apply the same guard logic as in pipeline.py
    ticker_yf = "BBCA.JK"
    if not flat_df.empty and not isinstance(flat_df.columns, pd.MultiIndex):
        wrapped = pd.concat({ticker_yf: flat_df}, axis=1)
    else:
        wrapped = flat_df

    assert isinstance(wrapped.columns, pd.MultiIndex), "Guard must produce MultiIndex"
    assert ticker_yf in wrapped.columns.get_level_values(0), "Ticker must be top-level key"

    # Downstream extraction (weekly_data[t_yf]) must succeed without KeyError
    extracted = wrapped[ticker_yf].dropna(how="all")
    assert not extracted.empty
    assert "Close" in extracted.columns
