from __future__ import annotations

import copy
import json
import logging
from unittest.mock import mock_open

import pandas as pd
import pytest

from core.quant_filter import pipeline
from core.quant_filter.config import CONFIG, SECTOR_MEDIAN_PE, SECTOR_PBV_BENCHMARK
from core.quant_filter.reporting import _build_markdown_report
from utils.technicals import snap_to_tick


def _flat_exdate(*_args, **_kwargs) -> dict:
    return {"risk_tier": "LOW", "ex_date": None, "source": "test"}


def _market_frame() -> pd.DataFrame:
    close = pd.Series([100 + (20 * i / 59) for i in range(60)], dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 1,
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
            "max_atr_pct": 1.0,
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


def _breakout_frame() -> pd.DataFrame:
    """Flat base then a sharp recent breakout, so price sits far above its own
    SMA20 — needed so the ATR-price-anchored stop candidate (which scales with
    regime) dominates the SMA20-anchored candidate (which doesn't), making the
    regime multiplier actually visible in the final stop-loss value."""
    base = [100.0] * 40
    rise = [100 + 6 * i for i in range(1, 21)]  # 106 -> 220 over the last 20 bars
    close = pd.Series(base + rise, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 1,
            "Close": close,
            "Volume": pd.Series([1_000_000.0] * 60),
            "High": close + 2,
            "Low": close - 2,
        }
    )


def _pullback_frame() -> pd.DataFrame:
    """Long uptrend (price well above MA200) with a recent sharp pullback that
    dips the latest price below EMA20 — an oversold-in-uptrend setup."""
    rise = [100 + 60 * i / 54 for i in range(55)]  # 100 -> 160
    drop = [158.0, 150.0, 142.0, 135.0, 130.0]  # last 5 bars pull back below EMA20
    close = pd.Series(rise + drop, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 1,
            "Close": close,
            "Volume": pd.Series([1_000_000.0] * 60),
            "High": close + 2,
            "Low": close - 2,
        }
    )


def test_mean_reversion_mode_selects_oversold_pullback(monkeypatch):
    """An oversold pullback in an uptrend passes mean-reversion but fails momentum."""
    monkeypatch.setattr(pipeline, "_resolve_exdate", _flat_exdate)
    monkeypatch.setattr(
        pipeline,
        "compute_rsi",
        lambda close: pd.Series([32.0] * len(close), index=close.index),
    )
    monkeypatch.setattr(
        pipeline,
        "compute_atr",
        lambda high, low, close: pd.Series([5.0] * len(close), index=close.index),
    )
    logger = logging.getLogger("test.quant_filter.mr")
    frame = _pullback_frame()
    row = _analysis_row()

    mr_cfg = _analysis_cfg()
    mr_cfg["screener_mode"] = "mean_reversion"
    mom_cfg = _analysis_cfg()
    mom_cfg["screener_mode"] = "momentum"

    mr = pipeline._analyze_ticker(row, frame, mr_cfg, logger)
    mom = pipeline._analyze_ticker(row, frame, mom_cfg, logger)

    assert mr is not None  # oversold pullback IS a mean-reversion candidate
    assert "MR Oversold RSI" in mr["Entry Strategy"]
    # A long's stop must sit BELOW entry; the SMA20-anchored stop would be above
    # it here because price is below SMA20 by design.
    assert mr["Stop Loss Level"] < mr["Current Price"]
    assert mom is None  # ... but fails momentum (price below EMA20 trend gate)


def test_momentum_mode_rejects_what_mean_reversion_accepts(monkeypatch):
    """A steady uptrend (price above EMA20) passes momentum but fails mean-reversion."""
    _stub_indicators(monkeypatch)  # RSI stubbed at 50 on a 100->120 uptrend frame
    logger = logging.getLogger("test.quant_filter.mom")
    frame = _market_frame()
    row = _analysis_row()

    mr_cfg = _analysis_cfg()
    mr_cfg["screener_mode"] = "mean_reversion"
    mom_cfg = _analysis_cfg()
    mom_cfg["screener_mode"] = "momentum"

    mom = pipeline._analyze_ticker(row, frame, mom_cfg, logger)
    mr = pipeline._analyze_ticker(row, frame, mr_cfg, logger)

    assert mom is not None  # uptrend IS a momentum candidate
    assert mr is None  # ... but fails mean-reversion (no pullback below EMA20)


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


def test_analyze_ticker_defensive_regime_widens_stop(monkeypatch):
    """DEFENSIVE regime (3.0x ATR) must produce a lower (wider-buffer) stop than NORMAL (2.5x)."""
    _stub_indicators(monkeypatch)
    cfg = _analysis_cfg()
    logger = logging.getLogger("test.quant_filter.regime_stop")

    normal_result = pipeline._analyze_ticker(
        _analysis_row(), _breakout_frame(), cfg, logger, regime="NORMAL"
    )
    defensive_result = pipeline._analyze_ticker(
        _analysis_row(), _breakout_frame(), cfg, logger, regime="DEFENSIVE"
    )

    assert normal_result is not None
    assert defensive_result is not None
    assert defensive_result["Stop Loss Level"] < normal_result["Stop Loss Level"]


def test_analyze_ticker_unknown_regime_label_falls_back_to_default(monkeypatch):
    """An unrecognized regime label falls back to the 2.5x default, not an error."""
    _stub_indicators(monkeypatch)
    cfg = _analysis_cfg()
    logger = logging.getLogger("test.quant_filter.regime_fallback")

    normal_result = pipeline._analyze_ticker(
        _analysis_row(), _market_frame(), cfg, logger, regime="NORMAL"
    )
    unknown_result = pipeline._analyze_ticker(
        _analysis_row(), _market_frame(), cfg, logger, regime="BULLISH"
    )

    assert normal_result is not None
    assert unknown_result is not None
    assert unknown_result["Stop Loss Level"] == normal_result["Stop Loss Level"]


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


def test_price_path_records_missing_ohlcv_columns() -> None:
    data = pd.concat(
        {"TEST.JK": pd.DataFrame({"Close": pd.Series([100.0, 101.0])})},
        axis=1,
    )
    cfg = _analysis_cfg()
    cfg["min_bars"] = 1
    failures: list[dict[str, str]] = []

    result = pipeline._safe_analyze_price_candidate(
        row=_analysis_row(),
        data=data,
        cfg=cfg,
        logger=logging.getLogger("test.quant_filter.price_path"),
        ihsg_close=None,
        ihsg_return_1m=0.0,
        adapter=None,
        failures=failures,
    )

    assert result is None
    assert failures == [
        {
            "ticker": "TEST",
            "stage": "price_columns",
            "reason": "missing OHLCV columns: High, Low, Volume",
        }
    ]


def test_price_path_records_ticker_analysis_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_analysis(*_args, **_kwargs):
        raise KeyError("raw_data")

    monkeypatch.setattr(pipeline, "_analyze_ticker", fail_analysis)
    data = pd.concat({"TEST.JK": _market_frame()}, axis=1)
    failures: list[dict[str, str]] = []

    result = pipeline._safe_analyze_price_candidate(
        row=_analysis_row(),
        data=data,
        cfg=_analysis_cfg(),
        logger=logging.getLogger("test.quant_filter.price_path"),
        ihsg_close=None,
        ihsg_return_1m=0.0,
        adapter=None,
        failures=failures,
    )

    assert result is None
    assert failures[0]["ticker"] == "TEST"
    assert failures[0]["stage"] == "ticker_analysis"
    assert failures[0]["failure_type"] == "KeyError"
    assert "raw_data" in failures[0]["reason"]


def test_markdown_report_shows_pbv_based_for_financial_sector(monkeypatch):
    """_build_markdown_report must not cite Graham FV/gap for bank/finance_nonbank rows."""
    _stub_indicators(monkeypatch)
    cfg = _analysis_cfg()
    logger = logging.getLogger("test.quant_filter.report_financial")

    bank_result = pipeline._analyze_ticker(
        _analysis_row(Sector="bank"), _market_frame(), cfg, logger
    )
    assert bank_result is not None

    report = _build_markdown_report(pd.DataFrame([bank_result]), cfg)

    assert "PBV-based" in report
    assert "terhadap Graham Fair Value" not in report


def test_compute_val_score_non_financial_gap_tiers():
    """Non-financial sectors score Val_Score from the absolute Graham gap tiers."""
    cfg = _analysis_cfg()
    w = cfg["weight_valuation"]

    tier1 = pipeline._compute_val_score(
        pd.Series({"Sector": "default", "Valuation_Gap_Pct": 60.0}), cfg
    )
    tier2 = pipeline._compute_val_score(
        pd.Series({"Sector": "default", "Valuation_Gap_Pct": 25.0}), cfg
    )
    tier3 = pipeline._compute_val_score(
        pd.Series({"Sector": "default", "Valuation_Gap_Pct": 10.0}), cfg
    )
    tier4 = pipeline._compute_val_score(
        pd.Series({"Sector": "default", "Valuation_Gap_Pct": 2.0}), cfg
    )

    assert tier1 == pytest.approx(w * 1.00)
    assert tier2 == pytest.approx(w * 0.70)
    assert tier3 == pytest.approx(w * 0.40)
    assert tier4 == pytest.approx(w * 0.10)


def test_compute_val_score_bank_sector_uses_pbv_relative():
    """Bank/finance_nonbank sectors score Val_Score from PBV vs sector benchmark, not Graham."""
    cfg = _analysis_cfg()
    w = cfg["weight_valuation"]
    fair_lo = SECTOR_PBV_BENCHMARK["bank"]["fair_lo"]

    zero_pbv = pipeline._compute_val_score(
        pd.Series({"Sector": "bank", "Current Price to Book Value": 0.0}), cfg
    )
    very_cheap = pipeline._compute_val_score(
        pd.Series({"Sector": "bank", "Current Price to Book Value": fair_lo * 0.50}), cfg
    )
    cheap = pipeline._compute_val_score(
        pd.Series({"Sector": "bank", "Current Price to Book Value": fair_lo * 0.80}), cfg
    )
    fair = pipeline._compute_val_score(
        pd.Series({"Sector": "bank", "Current Price to Book Value": fair_lo * 0.95}), cfg
    )
    expensive = pipeline._compute_val_score(
        pd.Series({"Sector": "bank", "Current Price to Book Value": fair_lo * 1.50}), cfg
    )

    assert zero_pbv == pytest.approx(w * 0.10)
    assert very_cheap == pytest.approx(w * 1.00)
    assert cheap == pytest.approx(w * 0.70)
    assert fair == pytest.approx(w * 0.40)
    assert expensive == pytest.approx(w * 0.10)


def test_compute_val_score_pe_blend_both_cheap():
    """When both Graham gap and PE gap are tier-1, blended score equals w * 1.0."""
    cfg = _analysis_cfg()
    w = cfg["weight_valuation"]
    # default sector median PE = 14.0; current_pe = 5.0 → pe_gap ~64% → tier1
    row = pd.Series({
        "Sector": "default",
        "Valuation_Gap_Pct": 60.0,
        "Current EPS (TTM)": 2.0,
        "Close Price": 10.0,
    })
    score = pipeline._compute_val_score(row, cfg)
    assert score == pytest.approx(w * (0.70 * 1.0 + 0.30 * 1.0))


def test_compute_val_score_pe_blend_disagreement():
    """Graham tier-1 (cheap vs absolute) but PE tier-4 (expensive vs sector median)."""
    cfg = _analysis_cfg()
    w = cfg["weight_valuation"]
    # current_pe = 200/10 = 20.0; default median 14.0 → pe_gap < 0 → capped at 0 → tier4
    row = pd.Series({
        "Sector": "default",
        "Valuation_Gap_Pct": 60.0,
        "Current EPS (TTM)": 10.0,
        "Close Price": 200.0,
    })
    score = pipeline._compute_val_score(row, cfg)
    assert score == pytest.approx(w * (0.70 * 1.0 + 0.30 * 0.10))


def test_compute_val_score_pe_blend_fallback_no_eps():
    """EPS=0 → PE component unavailable → Graham-only (existing behaviour preserved)."""
    cfg = _analysis_cfg()
    w = cfg["weight_valuation"]
    row = pd.Series({
        "Sector": "default",
        "Valuation_Gap_Pct": 60.0,
        "Current EPS (TTM)": 0.0,
        "Close Price": 500.0,
    })
    score = pipeline._compute_val_score(row, cfg)
    assert score == pytest.approx(w * 1.00)


def test_compute_val_score_pe_uses_sector_specific_median():
    """PE gap is evaluated against the correct sector median, not the default."""
    cfg = _analysis_cfg()
    w = cfg["weight_valuation"]
    # healthcare sector median PE = 22.0; current_pe = 10.0 → gap = 55% → tier1
    row = pd.Series({
        "Sector": "healthcare",
        "Valuation_Gap_Pct": 5.0,      # graham tier3 = 0.40
        "Current EPS (TTM)": 5.0,
        "Close Price": 50.0,            # PE = 10x vs 22x median
    })
    assert SECTOR_MEDIAN_PE["healthcare"] == 22.0  # sanity-check config value
    score = pipeline._compute_val_score(row, cfg)
    # graham_tier=0.40 (gap=5% hits tier3), pe_tier=1.00 (pe_gap ~55% ≥ tier1)
    assert score == pytest.approx(w * (0.70 * 0.40 + 0.30 * 1.00))


def test_compute_prof_score_roe_tiers():
    """Prof_Score follows the absolute ROE tiers; non-positive ROE scores 0."""
    cfg = _analysis_cfg()
    w = cfg["weight_profitability"]

    assert pipeline._compute_prof_score(0.0, cfg) == 0.0
    assert pipeline._compute_prof_score(-0.05, cfg) == 0.0
    assert pipeline._compute_prof_score(0.30, cfg) == pytest.approx(w * 1.00)
    assert pipeline._compute_prof_score(0.20, cfg) == pytest.approx(w * 0.70)
    assert pipeline._compute_prof_score(0.12, cfg) == pytest.approx(w * 0.40)


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("mean_reversion", "mean_reversion"),
        ("mean-reversion", "mean_reversion"),
        ("momentum", "momentum"),
        ("", "momentum"),
        (None, "momentum"),
        ("garbage", "momentum"),
    ],
)
def test_canonical_screener_mode(value, expected):
    """Canonicalizes screener mode to momentum/mean_reversion; unknowns -> momentum."""
    from core.quant_filter.config import canonical_screener_mode

    assert canonical_screener_mode(value) == expected


# ── Task 28: S1-4 field integration + Task 29: validate_ohlcv ────────────────


def test_validate_ohlcv_rejects_none():
    from utils.technicals import validate_ohlcv
    ok, reason = validate_ohlcv(None, "BBCA")
    assert not ok
    assert "None" in reason


def test_validate_ohlcv_rejects_empty_df():
    from utils.technicals import validate_ohlcv
    ok, reason = validate_ohlcv(pd.DataFrame(), "BBCA")
    assert not ok


def test_validate_ohlcv_rejects_too_few_rows():
    from utils.technicals import validate_ohlcv
    df = pd.DataFrame({
        "Close": [100.0] * 5,
        "High": [102.0] * 5,
        "Low": [98.0] * 5,
        "Volume": [1_000_000.0] * 5,
    })
    ok, reason = validate_ohlcv(df, "TEST", min_rows=30)
    assert not ok
    assert "rows" in reason


def test_validate_ohlcv_rejects_all_nan_close():
    from utils.technicals import validate_ohlcv
    df = pd.DataFrame({
        "Close": [float("nan")] * 35,
        "High": [102.0] * 35,
        "Low": [98.0] * 35,
        "Volume": [1_000_000.0] * 35,
    })
    ok, reason = validate_ohlcv(df, "TEST")
    assert not ok
    assert "NaN" in reason


def test_validate_ohlcv_rejects_all_zero_volume():
    from utils.technicals import validate_ohlcv
    df = pd.DataFrame({
        "Close": [100.0] * 35,
        "High": [102.0] * 35,
        "Low": [98.0] * 35,
        "Volume": [0.0] * 35,
    })
    ok, reason = validate_ohlcv(df, "TEST")
    assert not ok
    assert "zero" in reason.lower()


def test_validate_ohlcv_accepts_valid_frame():
    from utils.technicals import validate_ohlcv
    ok, reason = validate_ohlcv(_market_frame(), "TEST")
    assert ok
    assert reason == ""


def test_analyze_ticker_returns_s1_s4_fields(monkeypatch):
    """S1-4 fields (is_lq45, macd, bb, gap, compression) all present in _analyze_ticker output."""
    _stub_indicators(monkeypatch)
    result = pipeline._analyze_ticker(
        _analysis_row(Ticker="BBCA"),
        _market_frame(),
        _analysis_cfg(),
        logging.getLogger("test"),
    )
    assert result is not None
    for field in ("is_lq45", "macd_histogram_state", "bb_position", "gap_type", "compression_type"):
        assert field in result, f"Missing S1-4 field in _analyze_ticker output: {field}"
    assert result["is_lq45"] is True  # BBCA is in the LQ45 list


def test_analyze_ticker_is_lq45_false_for_unknown(monkeypatch):
    _stub_indicators(monkeypatch)
    result = pipeline._analyze_ticker(
        _analysis_row(Ticker="UNKN"),
        _market_frame(),
        _analysis_cfg(),
        logging.getLogger("test"),
    )
    assert result is not None
    assert result["is_lq45"] is False


def test_is_lq45_rendered_in_pack_to_prompt_string():
    """is_lq45 must survive build_context_pack → pack_to_prompt_string (the consumed surface)."""
    from services.context_pack_builder import build_context_pack, pack_to_prompt_string
    raw = {
        "ticker": "BBCA",
        "price": 10000.0,
        "fair_value": 9800.0,
        "is_lq45": True,
        "rr": 2.5,
        "rating": "BUY",
        "confidence": 0.72,
        "entry_low": 9900.0,
        "entry_high": 10050.0,
        "target": 10500.0,
        "stop": 9700.0,
    }
    pack = build_context_pack("BBCA", raw)
    rendered = pack_to_prompt_string(pack)
    assert "is_lq45" in rendered
