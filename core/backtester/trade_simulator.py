"""Thin wrapper over evaluate_memory() that handles write sequencing and dry_run."""

from __future__ import annotations

from pathlib import Path

from core.backtest_memory import BacktestMemory, TradeOutcome
from core.backtest_outcome_evaluator import (
    EvaluationSummary,
    PriceFetcher,
    evaluate_memory,
)
from core.settings import settings


def run_backtest_simulation(
    signals: list[TradeOutcome],
    *,
    memory: BacktestMemory,
    dry_run: bool = False,
    horizon_trading_days: int = 65,
    price_fetcher: PriceFetcher | None = None,
    debates_dir: Path | None = None,
    entry_check: bool = True,
) -> EvaluationSummary:
    """Write new open signals then evaluate all open records against actual prices.

    On dry_run=True, signals are NOT written and evaluation runs in read-only mode
    against the existing memory state.

    entry_check=True (default): only count a trade as active after the price
    bar touches the entry_price (limit order triggered). Eliminates false losses
    from signals where the price gapped past the entry range without filling.
    """
    if not dry_run:
        for outcome in signals:
            memory.record(outcome)

    return evaluate_memory(
        memory_path=memory.path,
        debates_dir=debates_dir or settings.debates_dir,
        write=not dry_run,
        horizon_trading_days=horizon_trading_days,
        price_fetcher=price_fetcher,
        entry_check=entry_check,
    )
