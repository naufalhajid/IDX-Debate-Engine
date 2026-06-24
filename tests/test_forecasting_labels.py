"""Unit tests for core/forecasting/labels.py — no-lookahead + label correctness."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from core.forecasting.labels import (
    TRANSACTION_COST,
    TAU_H,
    build_labels,
)


def _make_df(n: int = 50, horizon: int = 10) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 1000.0 * np.cumprod(1 + rng.normal(0, 0.01, n))
    df = pd.DataFrame({"close": close})
    df[f"close_t{horizon}"] = df["close"].shift(-horizon)
    return df.dropna().reset_index(drop=True)


def test_build_labels_removes_forward_close_columns():
    df = _make_df(horizon=10)
    assert "close_t10" in df.columns

    result = build_labels(df, horizon=10)

    fwd_cols = [c for c in result.columns if c.startswith("close_t")]
    assert fwd_cols == [], f"Forward price column(s) still present: {fwd_cols}"


def test_tie_rule_both_labels_zero_when_tau_equal():
    """tau_target == tau_stop (symmetric TAU_H) → y_target_hit = y_stop_hit = 0."""
    df = _make_df(horizon=10)
    result = build_labels(df, horizon=10)

    assert (result["y_target_hit"] == 0).all()
    assert (result["y_stop_hit"] == 0).all()


def test_transaction_cost_formula():
    """0.0070 = 0.0015 + 0.0025 + 0.0010 + 0.0010 + 0.0010."""
    components = 0.0015 + 0.0025 + 0.0010 + 0.0010 + 0.0010
    assert math.isclose(TRANSACTION_COST, components, rel_tol=1e-9)


def test_r_net_h_deducts_transaction_cost():
    close = np.array([1000.0] * 20)
    fwd = np.array([1050.0] * 10 + [np.nan] * 10)
    df = pd.DataFrame({"close": close, "close_t10": fwd}).dropna().reset_index(drop=True)

    result = build_labels(df, horizon=10)

    expected = math.log(1050.0 / 1000.0) - math.log(1 + TRANSACTION_COST)
    assert all(math.isclose(float(v), expected, rel_tol=1e-6) for v in result["r_net_h"])


def test_tau_h_thresholds():
    assert TAU_H[5] == pytest.approx(0.010)
    assert TAU_H[10] == pytest.approx(0.015)
    assert TAU_H[20] == pytest.approx(0.025)


def test_y_up_above_threshold():
    n, tau, cost = 10, TAU_H[10], math.log(1 + TRANSACTION_COST)
    close = np.full(n, 1000.0)
    fwd = np.full(n, 1000.0 * math.exp(tau + cost + 0.001))
    df = pd.DataFrame({"close": close, "close_t10": fwd})
    assert (build_labels(df, horizon=10)["y_up"] == 1).all()


def test_y_up_below_threshold():
    n, tau, cost = 10, TAU_H[10], math.log(1 + TRANSACTION_COST)
    close = np.full(n, 1000.0)
    fwd = np.full(n, 1000.0 * math.exp(tau + cost - 0.001))
    df = pd.DataFrame({"close": close, "close_t10": fwd})
    assert (build_labels(df, horizon=10)["y_up"] == 0).all()
