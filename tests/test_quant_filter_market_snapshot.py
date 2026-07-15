from __future__ import annotations

import logging
from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from core.quant_filter import pipeline
from core.quant_filter.config import CONFIG
from utils.market_snapshot import IDX_TIMEZONE, build_market_snapshot
from utils.technicals import compute_rsi


def _frame(offset: float = 0.0, rows: int = 400) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-07-10", periods=rows)
    close = pd.Series(range(1000, 1000 + rows), index=index, dtype="float64") + offset
    return pd.DataFrame(
        {
            "Open": close - 2,
            "High": close + 5,
            "Low": close - 5,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=index,
    )


def _indicator_triplet(frame: pd.DataFrame) -> tuple[float, float, float]:
    close = frame["Close"]
    return (
        float(compute_rsi(close).iloc[-1]),
        float(close.ewm(span=20, adjust=False).mean().iloc[-1]),
        float(close.rolling(window=200, min_periods=50).mean().iloc[-1]),
    )


def test_single_and_batch_downloads_produce_identical_indicator_inputs(
    monkeypatch,
) -> None:
    flat = _frame()
    batch = pd.concat({"BBCA.JK": flat, "BMRI.JK": _frame(500)}, axis=1)
    returned = [flat, batch]
    kwargs_seen: list[dict] = []

    def fake_download(_tickers, **kwargs):
        kwargs_seen.append(kwargs)
        return returned.pop(0)

    monkeypatch.setattr(
        pipeline,
        "_get_yfinance",
        lambda: SimpleNamespace(download=fake_download),
    )
    single_snapshots = {}
    batch_snapshots = {}
    logger = logging.getLogger("test.quant.snapshot")

    single = pipeline.download_yf_with_retry(
        ["BBCA.JK"],
        period="legacy",
        retries=1,
        delay=0,
        logger=logger,
        as_of=date(2026, 7, 10),
        snapshot_sink=single_snapshots,
    )
    multi = pipeline.download_yf_with_retry(
        ["BBCA.JK", "BMRI.JK"],
        period="legacy",
        retries=1,
        delay=0,
        logger=logger,
        as_of=date(2026, 7, 10),
        snapshot_sink=batch_snapshots,
    )

    assert single_snapshots["BBCA"].data_hash == batch_snapshots["BBCA"].data_hash
    assert _indicator_triplet(single["BBCA.JK"]) == pytest.approx(
        _indicator_triplet(multi["BBCA.JK"])
    )
    for kwargs in kwargs_seen:
        assert kwargs["start"] == "2024-10-18"
        assert kwargs["end"] == "2026-07-11"
        assert kwargs["interval"] == "1d"
        assert kwargs["auto_adjust"] is True
        assert "period" not in kwargs


def test_safe_candidate_attaches_snapshot_cross_process_provenance(monkeypatch) -> None:
    snapshot = build_market_snapshot(
        "BBCA",
        _frame(),
        requested_start=date(2024, 11, 17),
        requested_end=date(2026, 7, 10),
        min_complete_bars=400,
        now=datetime(2026, 7, 10, 17, tzinfo=IDX_TIMEZONE),
    )
    monkeypatch.setattr(
        pipeline,
        "_analyze_ticker",
        lambda *_args, **_kwargs: {"Ticker": "BBCA", "RSI (14)": 50.0},
    )
    data = pd.concat({"BBCA.JK": snapshot.history}, axis=1)

    result = pipeline._safe_analyze_price_candidate(
        row=pd.Series({"Ticker": "BBCA"}),
        data=data,
        cfg={"min_bars": 60},
        logger=logging.getLogger("test.quant.snapshot.provenance"),
        ihsg_close=None,
        ihsg_return_1m=0.0,
        adapter=None,
        failures=[],
        market_snapshot=snapshot,
        snapshot_artifact_path="market_snapshots/BBCA.json.gz",
    )

    assert result is not None
    assert result["snapshot_id"] == snapshot.snapshot_id
    assert result["data_hash"] == snapshot.data_hash
    assert result["snapshot_path"] == "market_snapshots/BBCA.json.gz"
    assert result["market_snapshot"]["row_count"] == 400


def test_safe_candidate_rejects_snapshot_below_400_before_analysis(monkeypatch) -> None:
    snapshot = build_market_snapshot(
        "BBCA",
        _frame(rows=399),
        requested_start=date(2024, 11, 17),
        requested_end=date(2026, 7, 10),
        min_complete_bars=400,
        now=datetime(2026, 7, 10, 17, tzinfo=IDX_TIMEZONE),
    )
    monkeypatch.setattr(
        pipeline,
        "_analyze_ticker",
        lambda *_args, **_kwargs: pytest.fail("analysis must not run"),
    )
    failures: list[dict[str, str]] = []

    result = pipeline._safe_analyze_price_candidate(
        row=pd.Series({"Ticker": "BBCA"}),
        data=pd.concat({"BBCA.JK": snapshot.history}, axis=1),
        cfg={"min_bars": 60},
        logger=logging.getLogger("test.quant.snapshot.insufficient"),
        ihsg_close=None,
        ihsg_return_1m=0.0,
        adapter=None,
        failures=failures,
        market_snapshot=snapshot,
    )

    assert result is None
    assert failures[0]["stage"] == "price_bars"
    assert "insufficient_complete_bars" in failures[0]["reason"]


def test_quant_config_uses_snapshot_contract_defaults() -> None:
    assert CONFIG["yf_lookback_calendar_days"] == 630
    assert CONFIG["snapshot_min_complete_bars"] == 400
