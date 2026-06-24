"""
Tests for utils/dynamic_atr.py — GARCH(1,1) dynamic ATR.

Dummy data uses t(df=5) returns to simulate IDX fat tails and
volatility clustering. All tests are deterministic via seeded RNG.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.dynamic_atr import (
    DynamicATRResult,
    _classic_atr,
    calculate_dynamic_atr,
    compute_dynamic_atr_full,
)


@pytest.fixture
def idx_like_prices() -> pd.Series:
    """252-bar IDX-like close prices: fat tails (t df=5), volatility clustering."""
    rng = np.random.default_rng(42)
    returns = pd.Series(rng.standard_t(df=5, size=252) * 0.02)
    return (1 + returns).cumprod() * 5000


@pytest.fixture
def short_prices() -> pd.Series:
    """Only 30 bars — below the 60-bar minimum for GARCH."""
    return pd.Series(np.linspace(4800.0, 5200.0, 30))


@pytest.fixture
def constant_prices() -> pd.Series:
    """100 bars of constant price — zero variance, GARCH cannot fit."""
    return pd.Series([5000.0] * 100)


# ── basic correctness ──────────────────────────────────────────────────────────

def test_returns_positive_float(idx_like_prices):
    result = calculate_dynamic_atr(idx_like_prices)
    assert isinstance(result, float)
    assert result > 0


def test_garch_differs_from_classic(idx_like_prices):
    garch = calculate_dynamic_atr(idx_like_prices, use_garch=True)
    classic = calculate_dynamic_atr(idx_like_prices, use_garch=False)
    # GARCH ATR should differ from Wilder close-to-close ATR
    assert abs(garch - classic) > 1e-6, (
        f"GARCH ATR ({garch:.4f}) unexpectedly equals classic ATR ({classic:.4f})"
    )


def test_both_below_10pct_of_price(idx_like_prices):
    last_price = float(idx_like_prices.iloc[-1])
    threshold = 0.10 * last_price
    garch = calculate_dynamic_atr(idx_like_prices, use_garch=True)
    classic = calculate_dynamic_atr(idx_like_prices, use_garch=False)
    assert garch < threshold, f"GARCH ATR {garch:.2f} >= 10% of price {last_price:.2f}"
    assert classic < threshold, f"Classic ATR {classic:.2f} >= 10% of price {last_price:.2f}"


# ── fallback guards ────────────────────────────────────────────────────────────

def test_fallback_on_short_data_returns_classic(short_prices):
    garch_result = calculate_dynamic_atr(short_prices, use_garch=True)
    classic_result = _classic_atr(short_prices, 14)
    assert garch_result == pytest.approx(classic_result, rel=1e-6)


def test_fallback_on_short_data_method(short_prices):
    full = compute_dynamic_atr_full(short_prices, use_garch=True)
    assert full.method == "classic_fallback"
    assert full.fallback_reason == "insufficient_data"


def test_fallback_on_constant_prices_no_crash(constant_prices):
    result = calculate_dynamic_atr(constant_prices, use_garch=True)
    assert isinstance(result, float)
    assert result >= 0


def test_cap_at_3x_classic_if_triggered():
    # Extreme fat tails (df=2) force GARCH variance to potentially explode
    rng = np.random.default_rng(0)
    returns = pd.Series(rng.standard_t(df=2, size=300) * 0.05)
    prices = (1 + returns.clip(-0.99, None)).cumprod() * 1000

    classic = _classic_atr(prices, 14)
    full = compute_dynamic_atr_full(prices, period=14, use_garch=True)

    if full.fallback_reason == "variance_cap_applied":
        assert full.value <= 3.0 * classic + 1e-8


# ── use_garch=False path ───────────────────────────────────────────────────────

def test_use_garch_false_returns_classic_exactly(idx_like_prices):
    result = calculate_dynamic_atr(idx_like_prices, period=14, use_garch=False)
    expected = _classic_atr(idx_like_prices, 14)
    assert result == pytest.approx(expected, rel=1e-9)


def test_use_garch_false_full_result_method(idx_like_prices):
    full = compute_dynamic_atr_full(idx_like_prices, use_garch=False)
    assert full.method == "classic"
    assert full.alpha is None
    assert full.beta is None


# ── full diagnostic result ─────────────────────────────────────────────────────

def test_full_result_garch_params_on_convergence(idx_like_prices):
    full = compute_dynamic_atr_full(idx_like_prices, use_garch=True)
    if full.method == "garch":
        assert full.alpha is not None
        assert full.beta is not None
        assert full.persistence is not None
        assert 0.0 < full.persistence < 1.0


def test_full_result_is_dataclass_instance(idx_like_prices):
    full = compute_dynamic_atr_full(idx_like_prices)
    assert isinstance(full, DynamicATRResult)
    assert isinstance(full.value, float)
    assert full.method in {"garch", "classic_fallback", "classic"}


def test_tgarch_model_reports_asymmetric_metadata(idx_like_prices):
    full = compute_dynamic_atr_full(idx_like_prices, model_type="tgarch")
    assert isinstance(full, DynamicATRResult)
    assert full.model_type == "tgarch"
    assert full.method in {"tgarch", "classic_fallback"}
    if full.method == "tgarch":
        assert full.gamma is not None
        assert full.aic is not None


# ── period scaling ─────────────────────────────────────────────────────────────

def test_period_1_smaller_than_period_14(idx_like_prices):
    """period=1 (daily) should produce smaller ATR than period=14 (14-day horizon)."""
    garch_daily = calculate_dynamic_atr(idx_like_prices, period=1, use_garch=True)
    garch_14d = calculate_dynamic_atr(idx_like_prices, period=14, use_garch=True)
    # sqrt(14) ≈ 3.74× scaling; daily should be smaller
    # (or equal if both fell back to classic, where period only affects smoothing)
    assert garch_daily <= garch_14d + 1e-6  # allow tiny float error
