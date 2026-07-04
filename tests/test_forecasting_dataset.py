"""V1.3: regression coverage for DatasetBuilder multi-horizon momentum features.

Scope is intentionally narrow — return_5d/10d/20d and price_above_ma20
(Priority 6 / Phase 2.3 of docs/research/forecasting_research.md). RSI/ATR/
regime/fundamentals join are a separate, pre-existing coverage gap.
"""
import pandas as pd
import pytest

from core.forecasting.dataset import DatasetBuilder


def _linear_close_frame(n: int = 30, start: float = 100.0, step: float = 2.0) -> pd.DataFrame:
    return pd.DataFrame({"close": [start + step * i for i in range(n)]})


def test_multi_horizon_returns_match_pct_change_lag():
    df = DatasetBuilder()._add_technicals(_linear_close_frame())

    assert df["return_5d"].iloc[25] == pytest.approx(10 / 140)
    assert df["return_10d"].iloc[25] == pytest.approx(20 / 130)
    assert df["return_20d"].iloc[25] == pytest.approx(40 / 110)


def test_multi_horizon_returns_are_nan_before_lag_window():
    df = DatasetBuilder()._add_technicals(_linear_close_frame())

    assert df["return_5d"].iloc[:5].isna().all()
    assert df["return_10d"].iloc[:10].isna().all()
    assert df["return_20d"].iloc[:20].isna().all()


def test_price_above_ma20_true_when_close_exceeds_rolling_mean():
    df = DatasetBuilder()._add_technicals(_linear_close_frame())

    # rolling(20).mean() at index 25 spans indices 6..25; for a linear series
    # that mean is (close[6]+close[25])/2 = 131, and close[25] = 150 > 131.
    assert df["price_above_ma20"].iloc[25] == 1


def test_price_above_ma20_false_on_flat_series():
    flat = pd.DataFrame({"close": [100.0] * 30})
    df = DatasetBuilder()._add_technicals(flat)

    assert (df["price_above_ma20"] == 0).all()


def test_price_above_ma20_is_integer_dtype():
    df = DatasetBuilder()._add_technicals(_linear_close_frame())

    assert df["price_above_ma20"].dtype.kind in "iu"
