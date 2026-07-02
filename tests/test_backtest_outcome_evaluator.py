from datetime import date, timedelta
from pathlib import Path

import pytest

from core.backtest_memory import BacktestMemory, TradeOutcome
from core.backtest_outcome_evaluator import (
    DEFAULT_HORIZON_TRADING_DAYS,
    PriceBar,
    evaluate_memory,
    evaluate_trade_outcome,
)


def _record(
    *,
    ticker: str = "BBCA",
    run_id: str = "run-1",
    verdict_rating: str = "BUY",
    outcome: str = "open",
) -> TradeOutcome:
    return TradeOutcome(
        run_id=run_id,
        ticker=ticker,
        verdict_rating=verdict_rating,
        entry_price=100.0,
        exit_price=None,
        target_price=110.0,
        stop_loss=95.0,
        entry_date="2026-01-01",
        exit_date=None,
        outcome=outcome,
        pnl_pct=None,
        hit_target=None,
        hit_stop=None,
        confidence_at_entry=0.8,
        notes="test",
    )


def _bar(
    day: int,
    *,
    high: float = 105.0,
    low: float = 99.0,
    close: float = 100.0,
) -> PriceBar:
    return PriceBar(
        trade_date=date(2026, 1, 1) + timedelta(days=day),
        high=high,
        low=low,
        close=close,
    )


def _create_artifact(debates_dir: Path, record: TradeOutcome) -> None:
    ticker = record.ticker.upper()
    artifact = debates_dir / ticker / f"v{record.run_id}" / f"{ticker}_debate.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{}", encoding="utf-8")


def test_default_horizon_is_normal_swing_cap() -> None:
    assert DEFAULT_HORIZON_TRADING_DAYS == 20


def test_target_hit_first_is_win() -> None:
    evaluated = evaluate_trade_outcome(
        _record(),
        [_bar(1, high=108), _bar(2, high=111, low=99, close=110)],
    )

    assert evaluated is not None
    assert evaluated.outcome == "win"
    assert evaluated.hit_target is True
    assert evaluated.hit_stop is False
    assert evaluated.exit_price == 110
    assert evaluated.evaluation_reason == "target_hit"


def test_stop_hit_first_is_loss() -> None:
    evaluated = evaluate_trade_outcome(
        _record(),
        [_bar(1, high=105, low=94, close=95)],
    )

    assert evaluated is not None
    assert evaluated.outcome == "loss"
    assert evaluated.hit_stop is True
    assert evaluated.exit_price == 95
    assert evaluated.evaluation_reason == "stop_hit"


def test_same_day_target_and_stop_is_conservative_loss() -> None:
    evaluated = evaluate_trade_outcome(
        _record(),
        [_bar(1, high=111, low=94, close=100)],
    )

    assert evaluated is not None
    assert evaluated.outcome == "loss"
    assert evaluated.hit_target is True
    assert evaluated.hit_stop is True
    assert evaluated.evaluation_reason == "same_day_target_and_stop"


def test_horizon_close_above_entry_is_win() -> None:
    bars = [_bar(day, high=108, low=99, close=105) for day in range(1, 21)]

    evaluated = evaluate_trade_outcome(_record(), bars, horizon_trading_days=20)

    assert evaluated is not None
    assert evaluated.outcome == "win"
    assert evaluated.exit_price == 105
    assert evaluated.holding_period_days == 20
    assert evaluated.evaluation_reason == "horizon_close_above_entry"


def test_horizon_close_at_or_below_entry_is_loss() -> None:
    bars = [_bar(day, high=100, low=96, close=97) for day in range(1, 21)]

    evaluated = evaluate_trade_outcome(_record(), bars, horizon_trading_days=20)

    assert evaluated is not None
    assert evaluated.outcome == "loss"
    assert evaluated.evaluation_reason == "horizon_close_at_or_below_entry"


def test_insufficient_horizon_remains_unscored() -> None:
    evaluated = evaluate_trade_outcome(
        _record(),
        [_bar(1, high=105, low=99, close=101)],
        horizon_trading_days=20,
    )

    assert evaluated is None


def test_evaluate_memory_skips_records_without_artifact(tmp_path: Path) -> None:
    # HOLD is now in EVALUATED_RATINGS but both records lack debate artifacts → both skipped
    memory_path = tmp_path / "backtest_memory.jsonl"
    debates_dir = tmp_path / "debates"
    memory = BacktestMemory(memory_path)
    hold = _record(verdict_rating="HOLD")
    buy = _record(ticker="TLKM", verdict_rating="BUY")
    memory.record(hold)
    memory.record(buy)

    summary = evaluate_memory(
        memory_path=memory_path,
        debates_dir=debates_dir,
        write=True,
        price_fetcher=lambda *_: pytest.fail("price fetch should not be called"),
    )

    assert summary.updated_records == 0
    assert summary.skipped_records == 2
    assert [record.outcome for record in memory.all_records()] == ["open", "open"]


def test_evaluate_memory_updates_buy_with_matching_artifact(tmp_path: Path) -> None:
    memory_path = tmp_path / "backtest_memory.jsonl"
    debates_dir = tmp_path / "debates"
    memory = BacktestMemory(memory_path)
    record = _record()
    memory.record(record)
    _create_artifact(debates_dir, record)

    summary = evaluate_memory(
        memory_path=memory_path,
        debates_dir=debates_dir,
        write=True,
        price_fetcher=lambda *_: [_bar(1, high=111, low=99, close=110)],
    )

    stored = memory.all_records()
    assert summary.updated_records == 1
    assert summary.backup_path == str(
        memory_path.with_name("backtest_memory.jsonl.bak")
    )
    assert stored[0].outcome == "win"
    assert stored[0].evaluation_method == "hybrid_target_stop_horizon"


def test_evaluate_memory_price_failure_does_not_rewrite_memory(tmp_path: Path) -> None:
    memory_path = tmp_path / "backtest_memory.jsonl"
    debates_dir = tmp_path / "debates"
    memory = BacktestMemory(memory_path)
    record = _record()
    memory.record(record)
    original_text = memory_path.read_text(encoding="utf-8")
    _create_artifact(debates_dir, record)

    summary = evaluate_memory(
        memory_path=memory_path,
        debates_dir=debates_dir,
        write=True,
        price_fetcher=lambda *_: (_ for _ in ()).throw(RuntimeError("network")),
    )

    assert summary.updated_records == 0
    assert memory_path.read_text(encoding="utf-8") == original_text


def test_evaluate_memory_empty_price_data_skips_record(tmp_path: Path) -> None:
    memory_path = tmp_path / "backtest_memory.jsonl"
    debates_dir = tmp_path / "debates"
    memory = BacktestMemory(memory_path)
    record = _record()
    memory.record(record)
    _create_artifact(debates_dir, record)

    summary = evaluate_memory(
        memory_path=memory_path,
        debates_dir=debates_dir,
        write=True,
        price_fetcher=lambda *_: [],
    )

    assert summary.updated_records == 0
    assert summary.details[0].reason == "no_price_data"
