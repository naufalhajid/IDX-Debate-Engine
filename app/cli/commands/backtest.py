"""CLI command for backtesting historical CIO verdict signals."""

from __future__ import annotations

import warnings
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from app.cli.ui.console import console
from core.idx_market_params import SWING_MAX_EXECUTION_HORIZON_DAYS


def evaluate_open_records(*, debates_dir: Path, horizon_days: int):
    """Evaluate open BacktestMemory records and persist resolved outcomes."""
    from core.backtest_outcome_evaluator import evaluate_memory

    with console.status("[idx.header]Evaluating open BacktestMemory records...[/idx.header]"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return evaluate_memory(
                debates_dir=debates_dir,
                write=True,
                horizon_trading_days=horizon_days,
            )


def print_evaluation_summary(summary) -> None:
    console.print(
        "[idx.ok]Backtest memory evaluation complete.[/idx.ok] "
        f"[idx.muted]total={summary.total_records}, "
        f"eligible={summary.eligible_records}, "
        f"updated={summary.updated_records}, "
        f"skipped={summary.skipped_records}, "
        f"unchanged={summary.unchanged_records}[/idx.muted]"
    )
    if summary.backup_path:
        console.print(f"[idx.muted]Backup: {summary.backup_path}[/idx.muted]")


def backtest_command(
    action: Annotated[
        str | None,
        typer.Argument(
            help="Optional maintenance action. Use 'evaluate-open' to score open records."
        ),
    ] = None,
    from_date: Annotated[
        str | None,
        typer.Option("--from", help="Only signals from this date onward (YYYYMMDD)."),
    ] = None,
    tickers: Annotated[
        list[str] | None,
        typer.Option("--tickers", help="Filter to specific tickers (e.g. BBRI BMRI)."),
    ] = None,
    min_rating: Annotated[
        str,
        typer.Option("--min-rating", help="Minimum rating to include: BUY or STRONG_BUY."),
    ] = "BUY",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Simulate without writing to BacktestMemory."),
    ] = False,
    output: Annotated[
        str,
        typer.Option("--output", help="Output format: table or md."),
    ] = "table",
    output_file: Annotated[
        Path | None,
        typer.Option("--output-file", help="Save Markdown report to this file."),
    ] = None,
    debates_dir: Annotated[
        Path,
        typer.Option("--debates-dir", help="Override debates directory."),
    ] = Path("output/debates"),
    horizon_days: Annotated[
        int,
        typer.Option("--horizon-days", help="Max trading days per trade (default 20 ~= normal swing cap)."),
    ] = SWING_MAX_EXECUTION_HORIZON_DAYS,
    no_entry_check: Annotated[
        bool,
        typer.Option("--no-entry-check", help="Disable limit-order entry trigger (count trade from signal date)."),
    ] = False,
) -> None:
    """Score historical CIO verdicts against actual IDX prices."""
    if action:
        normalized_action = action.strip().lower().replace("_", "-")
        if normalized_action != "evaluate-open":
            console.print(
                f"[idx.error]Unknown backtest action '{action}'. "
                "Use 'evaluate-open' or run 'idx backtest --help'.[/idx.error]"
            )
            raise typer.Exit(code=2)
        summary = evaluate_open_records(
            debates_dir=debates_dir,
            horizon_days=horizon_days,
        )
        print_evaluation_summary(summary)
        return

    from core.backtest_memory import BacktestMemory
    from core.backtester.signal_loader import (
        build_existing_run_ids,
        scan_debate_dir,
        signals_to_outcomes,
    )
    from core.backtester.trade_simulator import run_backtest_simulation
    from core.backtester.metrics_calculator import compute_metrics
    from core.backtester.report_generator import print_report

    parsed_from: date | None = None
    if from_date:
        try:
            parsed_from = date(
                int(from_date[:4]),
                int(from_date[4:6]),
                int(from_date[6:8]),
            )
        except (ValueError, IndexError):
            console.print(f"[idx.error]Invalid --from date '{from_date}'. Expected YYYYMMDD.[/idx.error]")
            raise typer.Exit(code=2)

    if not debates_dir.exists():
        console.print(f"[idx.error]Debates directory not found: {debates_dir}[/idx.error]")
        raise typer.Exit(code=1)

    memory = BacktestMemory()

    with console.status("[idx.header]Loading signals from debate history...[/idx.header]"):
        signals = scan_debate_dir(
            debates_dir,
            min_rating=min_rating,
            from_date=parsed_from,
            tickers=list(tickers) if tickers else None,
        )

    if not signals:
        console.print("[idx.warn]No eligible signals found in debates directory.[/idx.warn]")
        raise typer.Exit()

    console.print(
        f"[idx.ok]Found {len(signals)} signal(s)[/idx.ok] "
        f"[idx.muted]({min_rating}+ rating)[/idx.muted]"
    )

    existing = build_existing_run_ids(memory.all_records())
    new_outcomes = signals_to_outcomes(signals, existing)

    new_count = len(new_outcomes)
    skip_count = len(signals) - new_count
    if skip_count:
        console.print(f"[idx.muted]Skipping {skip_count} already-recorded signal(s).[/idx.muted]")
    if dry_run and new_count:
        console.print(f"[idx.warn]Dry run: {new_count} signal(s) would be written.[/idx.warn]")
    elif new_count:
        console.print(f"[idx.ok]Writing {new_count} new signal(s) to BacktestMemory.[/idx.ok]")

    with console.status("[idx.header]Evaluating trades against price data...[/idx.header]"):
        summary = run_backtest_simulation(
            new_outcomes,
            memory=memory,
            dry_run=dry_run,
            horizon_trading_days=horizon_days,
            debates_dir=debates_dir,
            entry_check=not no_entry_check,
        )

    console.print(
        f"[idx.muted]Evaluated {summary.eligible_records} open record(s): "
        f"{summary.updated_records} resolved, {summary.skipped_records} skipped.[/idx.muted]"
    )

    all_records = memory.all_records()
    metrics = compute_metrics(all_records)

    if metrics.total_trades == 0:
        console.print("[idx.warn]No records in BacktestMemory yet.[/idx.warn]")
        raise typer.Exit()

    print_report(
        metrics,
        output_format=output,
        output_file=output_file,
    )


app = typer.Typer(help="Backtest historical CIO verdict signals.")
app.command(name="backtest")(backtest_command)

__all__ = ["app", "backtest_command", "evaluate_open_records"]
