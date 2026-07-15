"""V1.3: regression coverage for DatasetBuilder multi-horizon momentum features.

Scope is intentionally narrow — return_5d/10d/20d and price_above_ma20
(Priority 6 / Phase 2.3 of docs/research/forecasting_research.md). RSI/ATR/
regime/fundamentals join are a separate, pre-existing coverage gap.
"""
from datetime import date

import pandas as pd
import pytest

import core.forecasting.dataset as dataset_module
from core.forecasting.dataset import DatasetBuilder
from utils.market_snapshot import build_market_snapshot
from utils.ticker import InvalidIDXTicker


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


def test_injected_market_snapshot_prevents_second_ticker_download(monkeypatch):
    index = pd.date_range("2024-01-02", periods=400, freq="B")
    raw = pd.DataFrame(
        {
            "Open": [1000.0 + i for i in range(len(index))],
            "High": [1020.0 + i for i in range(len(index))],
            "Low": [990.0 + i for i in range(len(index))],
            "Close": [1010.0 + i for i in range(len(index))],
            "Volume": [1_000_000.0] * len(index),
        },
        index=index,
    )
    snapshot = build_market_snapshot(
        "BBCA",
        raw,
        requested_start=index[0].date(),
        requested_end=index[-1].date(),
    )

    def _unexpected_download(*_args, **_kwargs):
        raise AssertionError("ticker OHLCV must come from the injected snapshot")

    monkeypatch.setattr(dataset_module, "_download_ohlcv", _unexpected_download)
    monkeypatch.setattr(
        dataset_module,
        "_fill_fundamentals",
        lambda frame, ticker: None,
    )

    result = DatasetBuilder()._build_ticker(
        "BBCA",
        index[0].date(),
        index[-1].date(),
        (5,),
        None,
        snapshot=snapshot,
    )

    assert result is not None
    assert len(result) == 395
    assert float(result["close"].iloc[-1]) == pytest.approx(
        float(raw["Close"].iloc[-6])
    )


def test_build_rejects_invalid_ticker_before_shared_market_download(monkeypatch):
    def unexpected_ihsg_download(*_args, **_kwargs):
        pytest.fail("invalid ticker reached shared market-data download")

    monkeypatch.setattr(
        dataset_module,
        "_compute_ihsg_regimes",
        unexpected_ihsg_download,
    )

    with pytest.raises(InvalidIDXTicker):
        DatasetBuilder().build(
            ["../escape"],
            date(2025, 1, 1),
            date(2026, 7, 13),
        )


def test_direct_ohlcv_download_rejects_invalid_ticker_before_provider(monkeypatch):
    def unexpected_provider():
        pytest.fail("invalid ticker reached yfinance provider creation")

    monkeypatch.setattr(dataset_module, "_get_yf", unexpected_provider)

    with pytest.raises(InvalidIDXTicker):
        dataset_module._download_ohlcv(
            "../escape",
            date(2025, 1, 1),
            date(2026, 7, 13),
        )
