from __future__ import annotations

import copy
import json
import logging
from unittest.mock import mock_open

import pandas as pd
import pytest

from core.quant_filter import pipeline
from core.quant_filter.config import CONFIG
from utils.technicals import snap_to_tick


def _flat_exdate(*_args, **_kwargs) -> dict:
    return {"risk_tier": "LOW", "ex_date": None, "source": "test"}


def _market_frame() -> pd.DataFrame:
    close = pd.Series([100 + (20 * i / 59) for i in range(60)], dtype=float)
    return pd.DataFrame(
        {
            "Close": close,
            "Volume": pd.Series([1_000_000.0] * 60),
            "High": close + 2,
            "Low": close - 2,
        }
    )


def _analysis_cfg() -> dict:
    cfg = copy.deepcopy(CONFIG)
    cfg.update(
        {
            "min_adt_20d": 0,
            "min_rs_vs_ihsg_1m": 0.0,
        }
    )
    return cfg


def _analysis_row(**overrides) -> pd.Series:
    payload = {
        "Ticker": "TEST",
        "Sector": "default",
        "Sector_Label": "Lain-lain",
        "Debt to Equity Ratio (Quarter)": 0.5,
        "Val_Score": 10.0,
        "Prof_Score": 5.0,
        "Valuation_Gap_Pct": 10.0,
        "Current Price to Book Value": 1.0,
        "PBV_Sector_Pctile": 0.5,
        "Graham_Number": 180.0,
        "Graham_Bear": 160.0,
        "Graham_Bull": 200.0,
        "graham_fv_capped": False,
        "Return on Equity (TTM)": 0.15,
        "Piotroski F-Score": 8,
        "Altman Z-Score (Modified)": 2.5,
    }
    payload.update(overrides)
    return pd.Series(payload)


def _stub_indicators(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "_resolve_exdate", _flat_exdate)
    monkeypatch.setattr(
        pipeline,
        "compute_rsi",
        lambda close: pd.Series([50.0] * len(close), index=close.index),
    )
    monkeypatch.setattr(
        pipeline,
        "compute_atr",
        lambda high, low, close: pd.Series([5.0] * len(close), index=close.index),
    )


def test_analyze_ticker_applies_piotroski_bonus_and_penalty(monkeypatch):
    """Verifies Piotroski F-Score changes the deterministic composite score."""
    _stub_indicators(monkeypatch)
    cfg = _analysis_cfg()
    logger = logging.getLogger("test.quant_filter.piotroski")

    strong = pipeline._analyze_ticker(
        _analysis_row(**{"Piotroski F-Score": 8}),
        _market_frame(),
        cfg,
        logger,
    )
    weak = pipeline._analyze_ticker(
        _analysis_row(**{"Piotroski F-Score": 4}),
        _market_frame(),
        cfg,
        logger,
    )

    assert strong is not None
    assert weak is not None
    assert 0 <= strong["Composite Score"] <= 100
    assert strong["Composite Score"] == pytest.approx(weak["Composite Score"] + 10)
    assert "F-Score Kuat (8/9)" in strong["Entry Strategy"]
    assert "F-Score Lemah (4/9)" in weak["Entry Strategy"]


def test_analyze_ticker_preserves_altman_z_score(monkeypatch):
    """Verifies the ticker analyzer carries Altman Z-Score into its result."""
    _stub_indicators(monkeypatch)

    result = pipeline._analyze_ticker(
        _analysis_row(**{"Altman Z-Score (Modified)": 0.9}),
        _market_frame(),
        _analysis_cfg(),
        logging.getLogger("test.quant_filter.altman"),
    )

    assert result is not None
    assert result["Altman Z-Score"] == pytest.approx(0.9)


def test_build_sector_map_resolves_cache_hardcode_keyword_and_default(monkeypatch):
    """Verifies sector resolution priority across cache, hardcode, keyword, and fallback."""
    cache_json = json.dumps({"CACH": {"sector": "tech"}})
    monkeypatch.setattr(pipeline.os.path, "exists", lambda _path: True)
    monkeypatch.setattr("builtins.open", mock_open(read_data=cache_json))

    result = pipeline._build_sector_map(
        tickers=["CACH", "BBCA", "KEYW", "ZZZZ"],
        names={
            "CACH": "Cached Company",
            "BBCA": "Bank Central Asia",
            "KEYW": "PT Digital Teknologi Nusantara",
            "ZZZZ": "PT Random Holdings",
        },
        cache_file="fake_sector_cache.json",
        logger=logging.getLogger("test.quant_filter.sector"),
    )

    assert result == {
        "CACH": "tech",
        "BBCA": "bank",
        "KEYW": "tech",
        "ZZZZ": "default",
    }


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (197.4, 197.0),
        (203.2, 204.0),
        (1234.0, 1235.0),
        (3456.0, 3460.0),
        (5123.0, 5125.0),
        (0.0, 0.0),
        (-10.0, 0.0),
        (float("nan"), 0.0),
    ],
)
def test_snap_to_tick_uses_ihsg_price_fraction_ranges(price, expected):
    """Verifies IHSG tick snapping across supported price bands and invalid values."""
    assert snap_to_tick(price) == expected
