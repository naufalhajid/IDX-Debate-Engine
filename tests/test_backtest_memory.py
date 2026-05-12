from pathlib import Path

from core.backtest_memory import BacktestMemory, TradeOutcome


def _outcome(
    *,
    ticker: str = "BBCA",
    verdict_rating: str = "BUY",
    outcome: str = "win",
    exit_price: float | None = 1100,
    pnl_pct: float | None = None,
    confidence_at_entry: float | None = 0.8,
) -> TradeOutcome:
    return TradeOutcome(
        run_id="run-1",
        ticker=ticker,
        verdict_rating=verdict_rating,
        entry_price=1000,
        exit_price=exit_price,
        target_price=1100,
        stop_loss=950,
        entry_date="2026-05-01",
        exit_date="2026-05-10" if exit_price is not None else None,
        outcome=outcome,
        pnl_pct=pnl_pct,
        hit_target=outcome == "win",
        hit_stop=outcome == "loss",
        confidence_at_entry=confidence_at_entry,
        notes="Synthetic test outcome.",
    )


def test_record_and_query_by_ticker_returns_correct_records(tmp_path: Path) -> None:
    memory = BacktestMemory(tmp_path / "backtest_memory.jsonl")
    memory.record(_outcome(ticker="BBCA"))
    memory.record(_outcome(ticker="BBRI"))

    results = memory.query(ticker="BBCA")

    assert len(results) == 1
    assert results[0].ticker == "BBCA"
    assert results[0].pnl_pct == 10


def test_query_by_verdict_rating_filters_correctly(tmp_path: Path) -> None:
    memory = BacktestMemory(tmp_path / "backtest_memory.jsonl")
    memory.record(_outcome(ticker="BBCA", verdict_rating="BUY"))
    memory.record(_outcome(ticker="TLKM", verdict_rating="AVOID"))

    results = memory.query(verdict_rating="avoid")

    assert len(results) == 1
    assert results[0].ticker == "TLKM"
    assert results[0].verdict_rating == "AVOID"


def test_summary_stats_win_rate_calculated_correctly(tmp_path: Path) -> None:
    memory = BacktestMemory(tmp_path / "backtest_memory.jsonl")
    memory.record(_outcome(ticker="BBCA", outcome="win", exit_price=1100))
    memory.record(_outcome(ticker="BBCA", outcome="loss", exit_price=950))
    memory.record(
        _outcome(
            ticker="BBCA",
            outcome="open",
            exit_price=None,
            confidence_at_entry=None,
        )
    )

    stats = memory.summary_stats(ticker="BBCA")

    assert stats["total"] == 3
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["open"] == 1
    assert stats["win_rate"] == 0.5
    assert stats["avg_pnl_pct"] == 2.5
    assert stats["avg_confidence"] == 0.8


def test_summary_stats_on_empty_store_returns_zeros(tmp_path: Path) -> None:
    memory = BacktestMemory(tmp_path / "backtest_memory.jsonl")

    stats = memory.summary_stats()

    assert stats == {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "open": 0,
        "win_rate": 0.0,
        "avg_pnl_pct": None,
        "avg_confidence": None,
    }
