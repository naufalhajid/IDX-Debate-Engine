"""Unit tests for core/backtester/ modules. No live network calls."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest

from core.backtest_memory import TradeOutcome
from core.backtester.signal_loader import (
    _allowed_ratings,
    _parse_entry_high,
    _parse_signal_date,
    build_existing_run_ids,
    scan_debate_dir,
    signals_to_outcomes,
)
from core.backtester.metrics_calculator import (
    _compute_by_regime,
    _compute_deflated_sharpe,
    _compute_open_by_age,
    _compute_sharpe,
    _confidence_tier_key,
    _parse_regime_from_notes,
    calculate_deflated_sharpe_ratio,
    compute_deflated_sharpe_ratio,
    compute_metrics,
)
from core.backtest_outcome_evaluator import PriceBar, evaluate_trade_outcome


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_debate_json(
    ticker: str,
    rating: str = "BUY",
    confidence: float = 0.75,
    entry_price_range: str = "4080 - 4210",
    target_price: float = 4940.0,
    stop_loss: float = 3960.0,
    current_price: float = 4200.0,
) -> dict:
    return {
        "ticker": ticker,
        "verdict": {
            "rating": rating,
            "confidence": confidence,
            "entry_price_range": entry_price_range,
            "target_price": target_price,
            "stop_loss": stop_loss,
            "current_price": current_price,
        },
        "metadata": {"batch_timestamp": "20260610_161217"},
    }


def _write_debate(tmp_path: Path, ticker: str, folder: str, data: dict) -> Path:
    ticker_dir = tmp_path / ticker
    version_dir = ticker_dir / folder
    version_dir.mkdir(parents=True)
    json_path = version_dir / f"{ticker}_debate.json"
    json_path.write_text(json.dumps(data), encoding="utf-8")
    return json_path


def _make_outcome(
    ticker: str = "BBRI",
    run_id: str = "20260610_161217",
    outcome: str = "open",
    pnl_pct: float | None = None,
    confidence: float | None = 0.75,
    holding_period_days: int | None = None,
    notes: str = "test",
    entry_date: str = "2026-06-10",
) -> TradeOutcome:
    return TradeOutcome(
        run_id=run_id,
        ticker=ticker,
        verdict_rating="BUY",
        entry_price=4210.0,
        exit_price=None,
        target_price=4940.0,
        stop_loss=3960.0,
        entry_date=entry_date,
        exit_date=None,
        outcome=outcome,  # type: ignore[arg-type]
        pnl_pct=pnl_pct,
        hit_target=None,
        hit_stop=None,
        confidence_at_entry=confidence,
        notes=notes,
        holding_period_days=holding_period_days,
    )


# ─── scan_debate_dir ──────────────────────────────────────────────────────────


def test_scan_finds_buy_not_hold(tmp_path):
    _write_debate(tmp_path, "BBRI", "v20260610_161217", _make_debate_json("BBRI", rating="BUY"))
    _write_debate(tmp_path, "BMRI", "v20260610_161217", _make_debate_json("BMRI", rating="HOLD"))

    signals = scan_debate_dir(tmp_path)
    tickers = [s.ticker for s in signals]
    assert "BBRI" in tickers
    assert "BMRI" not in tickers


def test_min_rating_strong_buy_filters_buy(tmp_path):
    _write_debate(tmp_path, "BBRI", "v20260610_161217", _make_debate_json("BBRI", rating="BUY"))
    _write_debate(tmp_path, "TLKM", "v20260610_161217", _make_debate_json("TLKM", rating="STRONG_BUY"))

    signals = scan_debate_dir(tmp_path, min_rating="STRONG_BUY")
    tickers = [s.ticker for s in signals]
    assert "TLKM" in tickers
    assert "BBRI" not in tickers


def test_deduplication_keeps_latest_version(tmp_path):
    _write_debate(tmp_path, "BBRI", "v20260610_100000", _make_debate_json("BBRI", confidence=0.60))
    _write_debate(tmp_path, "BBRI", "v20260610_180000", _make_debate_json("BBRI", confidence=0.90))

    signals = scan_debate_dir(tmp_path)
    assert len(signals) == 1
    assert signals[0].confidence == pytest.approx(0.90)


def test_skips_null_target_price(tmp_path):
    data = _make_debate_json("BBRI")
    data["verdict"]["target_price"] = None
    _write_debate(tmp_path, "BBRI", "v20260610_161217", data)
    assert scan_debate_dir(tmp_path) == []


def test_skips_null_stop_loss(tmp_path):
    data = _make_debate_json("BBRI")
    data["verdict"]["stop_loss"] = None
    _write_debate(tmp_path, "BBRI", "v20260610_161217", data)
    assert scan_debate_dir(tmp_path) == []


def test_from_date_filter_excludes_older(tmp_path):
    _write_debate(tmp_path, "BBRI", "v20260601_000000", _make_debate_json("BBRI"))
    _write_debate(tmp_path, "BMRI", "v20260610_000000", _make_debate_json("BMRI"))

    signals = scan_debate_dir(tmp_path, from_date=date(2026, 6, 5))
    tickers = [s.ticker for s in signals]
    assert "BMRI" in tickers
    assert "BBRI" not in tickers


def test_ticker_filter(tmp_path):
    _write_debate(tmp_path, "BBRI", "v20260610_161217", _make_debate_json("BBRI"))
    _write_debate(tmp_path, "BMRI", "v20260610_161217", _make_debate_json("BMRI"))

    signals = scan_debate_dir(tmp_path, tickers=["BBRI"])
    assert all(s.ticker == "BBRI" for s in signals)


# ─── _parse_signal_date ───────────────────────────────────────────────────────


def test_parse_signal_date_standard():
    assert _parse_signal_date("v20260612_211545") == date(2026, 6, 12)


def test_parse_signal_date_invalid():
    with pytest.raises(ValueError):
        _parse_signal_date("not_a_version_folder")


# ─── _parse_entry_high ────────────────────────────────────────────────────────


def test_parse_entry_high_standard():
    assert _parse_entry_high("4080 - 4210", None) == pytest.approx(4210.0)


def test_parse_entry_high_indonesian_dots():
    assert _parse_entry_high("4.080 - 4.210", None) == pytest.approx(4210.0)


def test_parse_entry_high_em_dash():
    assert _parse_entry_high("4080 – 4210", None) == pytest.approx(4210.0)


def test_parse_entry_high_fallback_to_current_price():
    assert _parse_entry_high(None, 4200.0) == pytest.approx(4200.0)


def test_parse_entry_high_none_when_both_missing():
    assert _parse_entry_high(None, None) is None


# ─── signals_to_outcomes ──────────────────────────────────────────────────────


def test_signals_to_outcomes_sets_outcome_open(tmp_path):
    _write_debate(tmp_path, "BBRI", "v20260610_161217", _make_debate_json("BBRI"))
    signals = scan_debate_dir(tmp_path)
    outcomes = signals_to_outcomes(signals, existing_run_ids=set())
    assert all(o.outcome == "open" for o in outcomes)


def test_signals_to_outcomes_idempotency(tmp_path):
    _write_debate(tmp_path, "BBRI", "v20260610_161217", _make_debate_json("BBRI"))
    signals = scan_debate_dir(tmp_path)
    existing = {("BBRI", "20260610_161217")}
    outcomes = signals_to_outcomes(signals, existing_run_ids=existing)
    assert len(outcomes) == 0


def test_build_existing_run_ids():
    records = [
        _make_outcome("BBRI", run_id="20260610_161217"),
        _make_outcome("BMRI", run_id="20260611_090000"),
    ]
    ids = build_existing_run_ids(records)
    assert ("BBRI", "20260610_161217") in ids
    assert ("BMRI", "20260611_090000") in ids


# ─── _allowed_ratings ─────────────────────────────────────────────────────────


def test_allowed_ratings_buy_includes_strong_buy():
    allowed = _allowed_ratings("BUY")
    assert "BUY" in allowed and "STRONG_BUY" in allowed


def test_allowed_ratings_strong_buy_excludes_buy():
    allowed = _allowed_ratings("STRONG_BUY")
    assert "STRONG_BUY" in allowed and "BUY" not in allowed


# ─── _compute_sharpe ──────────────────────────────────────────────────────────


def test_compute_sharpe_none_for_single_record():
    assert _compute_sharpe([5.0]) is None


def test_compute_sharpe_none_when_std_zero():
    assert _compute_sharpe([5.0, 5.0, 5.0]) is None


def test_compute_sharpe_correct_formula():
    pnl = [10.0, 20.0]
    mean = 15.0
    std = math.sqrt(((10 - 15) ** 2 + (20 - 15) ** 2) / 1)
    # default fallback: _IDX_SWING_AVG_HOLD_DAYS = 10
    expected = (mean / std) * math.sqrt(252 / 10)
    assert _compute_sharpe(pnl) == pytest.approx(expected, rel=1e-6)


def test_compute_sharpe_uses_actual_holding_days():
    pnl = [10.0, 20.0]
    mean = 15.0
    std = math.sqrt(((10 - 15) ** 2 + (20 - 15) ** 2) / 1)
    expected = (mean / std) * math.sqrt(252 / 7)
    assert _compute_sharpe(pnl, avg_holding_days=7.0) == pytest.approx(expected, rel=1e-6)


def test_compute_sharpe_falls_back_to_default_when_hold_none():
    pnl = [10.0, 20.0]
    assert _compute_sharpe(pnl, avg_holding_days=None) == pytest.approx(
        _compute_sharpe(pnl), rel=1e-9
    )


# ─── _confidence_tier_key ─────────────────────────────────────────────────────


def test_compute_deflated_sharpe_ratio_returns_probability_metrics():
    pnl = [2.0, 3.0, -1.0, 4.0, 1.5, 2.5, -0.5, 3.5]

    result = compute_deflated_sharpe_ratio(pnl, benchmark_sr=0.0, n_trials=1)

    assert result is not None
    assert 0.0 <= result["deflated_sr"] <= 1.0
    assert 0.0 <= result["probabilistic_sr"] <= 1.0
    assert result["n_observations"] == len(pnl)


def test_deflated_sharpe_penalizes_multiple_trials():
    pnl = [2.0, 3.0, -1.0, 4.0, 1.5, 2.5, -0.5, 3.5]

    single = compute_deflated_sharpe_ratio(pnl, benchmark_sr=0.0, n_trials=1)
    many = compute_deflated_sharpe_ratio(pnl, benchmark_sr=0.0, n_trials=20)

    assert single is not None and many is not None
    assert many["deflated_sr"] <= single["deflated_sr"]


def test_calculate_deflated_sharpe_ratio_accepts_frequency():
    result = calculate_deflated_sharpe_ratio(
        [2.0, 3.0, -1.0, 4.0, 1.5, 2.5],
        benchmark_sr=0.0,
        n_trials=1,
        freq=25,
    )

    assert result is not None
    assert result["n_trials"] == 1


def test_compute_deflated_sharpe_wrapper_returns_probability():
    value = _compute_deflated_sharpe([2.0, 3.0, -1.0, 4.0, 1.5, 2.5])

    assert value is not None
    assert 0.0 <= value <= 1.0


def test_confidence_tier_high():
    assert _confidence_tier_key(0.85) == "high"
    assert _confidence_tier_key(0.80) == "high"


def test_confidence_tier_medium():
    assert _confidence_tier_key(0.70) == "medium"
    assert _confidence_tier_key(0.60) == "medium"


def test_confidence_tier_low():
    assert _confidence_tier_key(0.50) == "low"
    assert _confidence_tier_key(None) == "low"


# ─── compute_metrics ──────────────────────────────────────────────────────────


def test_compute_metrics_empty():
    m = compute_metrics([])
    assert m.total_trades == 0
    assert m.wins == 0
    assert m.win_rate is None
    assert m.sharpe_ratio is None
    assert m.best_trade is None


def test_compute_metrics_win_rate():
    records = [
        _make_outcome(outcome="win", pnl_pct=10.0),
        _make_outcome(outcome="win", pnl_pct=5.0),
        _make_outcome(outcome="loss", pnl_pct=-3.0),
    ]
    m = compute_metrics(records)
    assert m.wins == 2
    assert m.losses == 1
    assert m.win_rate == pytest.approx(2 / 3)


def test_compute_metrics_best_worst_trade():
    r1 = _make_outcome(ticker="BBRI", outcome="win", pnl_pct=15.0)
    r2 = _make_outcome(ticker="BMRI", run_id="20260611_000000", outcome="loss", pnl_pct=-8.0)
    m = compute_metrics([r1, r2])
    assert m.best_trade is not None and m.best_trade.ticker == "BBRI"
    assert m.worst_trade is not None and m.worst_trade.ticker == "BMRI"


def test_compute_metrics_per_ticker():
    records = [
        _make_outcome("BBRI", outcome="win", pnl_pct=10.0),
        _make_outcome("BBRI", run_id="20260611_000000", outcome="loss", pnl_pct=-3.0),
        _make_outcome("BMRI", outcome="win", pnl_pct=5.0),
    ]
    m = compute_metrics(records)
    assert "BBRI" in m.by_ticker and "BMRI" in m.by_ticker
    assert m.by_ticker["BBRI"]["wins"] == 1
    assert m.by_ticker["BBRI"]["losses"] == 1
    assert m.by_ticker["BMRI"]["win_rate"] == pytest.approx(1.0)


def test_compute_metrics_confidence_tiers():
    records = [
        _make_outcome(confidence=0.90, outcome="win", pnl_pct=10.0),
        _make_outcome(confidence=0.70, outcome="loss", pnl_pct=-3.0),
        _make_outcome(confidence=0.40, outcome="win", pnl_pct=5.0),
    ]
    m = compute_metrics(records)
    tiers = {t.label: t for t in m.by_confidence_tier}
    assert tiers["High (>=80%)"].total == 1
    assert tiers["Medium (60-80%)"].total == 1
    assert tiers["Low (<60%)"].total == 1


def test_compute_metrics_open_not_in_win_rate():
    records = [
        _make_outcome(outcome="win", pnl_pct=10.0),
        _make_outcome(run_id="20260611_000000", outcome="open"),
        _make_outcome(run_id="20260612_000000", outcome="timeout_flat", pnl_pct=0.5),
    ]
    m = compute_metrics(records)
    assert m.open_trades == 1
    assert m.timeout_flat == 1
    assert m.win_rate == pytest.approx(1.0)


# ─── Entry trigger check (evaluate_trade_outcome) ─────────────────────────────


def test_entry_check_triggers_on_first_touch():
    record = _make_outcome(entry_date="2026-06-01")
    # entry_price=4210, bar.low=4200 triggers
    bars = [
        PriceBar(trade_date=date(2026, 6, 2), high=4300.0, low=4200.0, close=4250.0),
        PriceBar(trade_date=date(2026, 6, 3), high=5000.0, low=4300.0, close=4900.0),
    ]
    result = evaluate_trade_outcome(record, bars, horizon_trading_days=65, entry_check=True)
    assert result is not None
    assert result.outcome == "win"


def test_entry_check_no_trigger_returns_none():
    record = _make_outcome(entry_date="2026-06-01")
    # entry_price=4210, all bars stay above — never touches
    bars = [
        PriceBar(trade_date=date(2026, 6, 2), high=4500.0, low=4300.0, close=4400.0),
        PriceBar(trade_date=date(2026, 6, 3), high=4600.0, low=4350.0, close=4500.0),
    ]
    # With only 2 bars (< horizon) entry not triggered and horizon not elapsed
    result = evaluate_trade_outcome(record, bars, horizon_trading_days=65, entry_check=True)
    assert result is None


def test_entry_check_disabled_does_not_require_trigger():
    record = _make_outcome(entry_date="2026-06-01")
    bars = [
        PriceBar(trade_date=date(2026, 6, 2), high=4500.0, low=4300.0, close=4400.0),
    ]
    # entry_check=False: doesn't check trigger, only 1 bar so insufficient horizon
    result = evaluate_trade_outcome(record, bars, horizon_trading_days=65, entry_check=False)
    assert result is None  # insufficient bars, but no entry trigger error


# ─── _parse_regime_from_notes ─────────────────────────────────────────────────


def test_parse_regime_from_notes_normal():
    assert _parse_regime_from_notes("regime=NORMAL;loaded_by=backtester_v1") == "NORMAL"


def test_parse_regime_from_notes_defensive():
    assert _parse_regime_from_notes("regime=DEFENSIVE;loaded_by=backtester_v1") == "DEFENSIVE"


def test_parse_regime_from_notes_unknown_on_missing():
    assert _parse_regime_from_notes("loaded_by=backtester_v1") == "UNKNOWN"
    assert _parse_regime_from_notes(None) == "UNKNOWN"
    assert _parse_regime_from_notes("") == "UNKNOWN"


# ─── _compute_by_regime ───────────────────────────────────────────────────────


def test_compute_by_regime_groups_correctly():
    records = [
        _make_outcome(outcome="win", pnl_pct=10.0, notes="regime=NORMAL;loaded_by=backtester_v1"),
        _make_outcome(run_id="20260611_000000", outcome="loss", pnl_pct=-5.0, notes="regime=NORMAL;loaded_by=backtester_v1"),
        _make_outcome(run_id="20260612_000000", outcome="win", pnl_pct=8.0, notes="regime=DEFENSIVE;loaded_by=backtester_v1"),
    ]
    by_regime = _compute_by_regime(records)
    assert "NORMAL" in by_regime and "DEFENSIVE" in by_regime
    assert by_regime["NORMAL"]["total"] == 2
    assert by_regime["NORMAL"]["wins"] == 1
    assert by_regime["DEFENSIVE"]["total"] == 1
    assert by_regime["DEFENSIVE"]["win_rate"] == pytest.approx(1.0)


def test_compute_by_regime_unknown_fallback():
    records = [_make_outcome(outcome="win", pnl_pct=5.0, notes="no_regime_here")]
    by_regime = _compute_by_regime(records)
    assert "UNKNOWN" in by_regime
    assert by_regime["UNKNOWN"]["total"] == 1


# ─── _compute_open_by_age ─────────────────────────────────────────────────────


def test_compute_open_by_age_buckets():
    today = date.today()

    def _days_ago(n: int) -> str:
        from datetime import timedelta
        return (today - timedelta(days=n)).isoformat()

    records = [
        _make_outcome(run_id="r1", outcome="open", entry_date=_days_ago(3)),   # <7d
        _make_outcome(run_id="r2", outcome="open", entry_date=_days_ago(15)),  # 7-30d
        _make_outcome(run_id="r3", outcome="open", entry_date=_days_ago(45)),  # >30d
        _make_outcome(run_id="r4", outcome="win", pnl_pct=5.0),                # closed, ignored
    ]
    buckets = _compute_open_by_age(records)
    assert buckets["<7d"] == 1
    assert buckets["7-30d"] == 1
    assert buckets[">30d"] == 1


def test_compute_open_by_age_empty_when_no_open():
    records = [_make_outcome(outcome="win", pnl_pct=5.0)]
    buckets = _compute_open_by_age(records)
    assert buckets == {"<7d": 0, "7-30d": 0, ">30d": 0}


# ─── regime tags in signals_to_outcomes ──────────────────────────────────────


def test_signals_to_outcomes_includes_regime_in_notes(tmp_path):
    data = _make_debate_json("BBRI")
    data["metadata"]["regime"] = "DEFENSIVE"
    _write_debate(tmp_path, "BBRI", "v20260610_161217", data)

    signals = scan_debate_dir(tmp_path)
    outcomes = signals_to_outcomes(signals, existing_run_ids=set())
    assert len(outcomes) == 1
    assert "regime=DEFENSIVE" in (outcomes[0].notes or "")


def test_signals_to_outcomes_unknown_regime_when_missing(tmp_path):
    _write_debate(tmp_path, "BBRI", "v20260610_161217", _make_debate_json("BBRI"))
    signals = scan_debate_dir(tmp_path)
    outcomes = signals_to_outcomes(signals, existing_run_ids=set())
    assert "regime=UNKNOWN" in (outcomes[0].notes or "")
