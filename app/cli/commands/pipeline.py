from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel

from app.cli.ui.console import console
from app.cli.ui.tables import build_verdict_summary_table


def run_pipeline_cli(
    *,
    dry_run: bool,
    output_dir: Path,
    tickers: tuple[str, ...],
    skip_scraping: bool,
    no_interactive: bool,
    mode: str,
    screener_mode: str,
    verbose: bool,
) -> None:
    import orchestrator

    argv = [
        "--output-dir",
        str(output_dir),
        "--mode",
        mode,
        "--screener-mode",
        screener_mode,
    ]
    if dry_run:
        argv.append("--dry-run")
    if skip_scraping:
        argv.append("--skip-scraping")
    if no_interactive:
        argv.append("--no-interactive")
    if verbose:
        argv.append("--verbose")
    if tickers:
        argv.append("--tickers")
        argv.extend(ticker.strip().upper() for ticker in tickers if ticker.strip())
    orchestrator._run_cli(argv)


def pipeline_command(
    ctx: typer.Context,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Simulate run without writing backtest records or the markdown report.",
        ),
    ] = False,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir", help="Directory for pipeline artifacts and reports."
        ),
    ] = Path("output"),
    tickers: Annotated[
        list[str] | None,
        typer.Option(
            "--tickers",
            help="Override quant filter — debate only these tickers. Example: BBCA BMRI ADMR",
        ),
    ] = None,
    skip_scraping: Annotated[
        bool,
        typer.Option(
            "--skip-scraping",
            help="Skip data fetch and reuse cached JSON from last run (faster).",
        ),
    ] = False,
    no_interactive: Annotated[
        bool,
        typer.Option(
            "--no-interactive",
            help="Run without interactive prompts, for CI or scripted use.",
        ),
    ] = False,
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="Pipeline mode: multi (default, all tickers), single, or compare.",
        ),
    ] = "multi",
    screener_mode: Annotated[
        str,
        typer.Option(
            "--screener-mode",
            help="Quant-filter strategy: 'momentum' (default) or 'mean-reversion' "
            "(oversold pullbacks). Mean-reversion forces a fresh screener run.",
        ),
    ] = "momentum",
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Enable verbose orchestrator logging."),
    ] = False,
) -> None:
    """Full automated pipeline: quant filter + AI debate + risk gate + TOP_3 report."""
    selected_mode = mode.lower().strip()
    if selected_mode not in {"multi", "single", "compare"}:
        raise typer.BadParameter("mode must be one of: multi, single, compare")

    selected_screener_mode = mode_str = screener_mode.lower().strip().replace("-", "_")
    if mode_str not in {"momentum", "mean_reversion"}:
        raise typer.BadParameter(
            "screener-mode must be one of: momentum, mean-reversion"
        )

    root_ctx = ctx.find_root()
    global_verbose = bool(((root_ctx.obj or {}) if root_ctx else {}).get("verbose"))
    selected_tickers = tuple(
        ticker.strip().upper()
        for ticker in tuple(tickers or ()) + tuple(ctx.args)
        if ticker.strip()
    )

    # Build active flags label for pre-flight panel
    flags: list[str] = []
    if dry_run:
        flags.append("dry-run")
    if skip_scraping:
        flags.append("skip-scraping")
    if no_interactive:
        flags.append("no-interactive")

    ticker_label = (
        ", ".join(selected_tickers) if selected_tickers else "(from quant filter)"
    )
    flags_line = (
        f"\n[idx.label]Flags:[/idx.label]    [idx.muted]{', '.join(flags)}[/idx.muted]"
        if flags
        else ""
    )

    console.print(
        Panel(
            f"[idx.label]Mode:[/idx.label]     [idx.highlight]{selected_mode}[/idx.highlight]\n"
            f"[idx.label]Tickers:[/idx.label]  {ticker_label}" + flags_line,
            title="[idx.header]IDX Pipeline[/idx.header]",
            border_style="idx.header",
            expand=False,
        )
    )

    run_pipeline_cli(
        dry_run=dry_run,
        output_dir=output_dir,
        tickers=selected_tickers,
        skip_scraping=skip_scraping,
        no_interactive=no_interactive,
        mode=selected_mode,
        screener_mode=selected_screener_mode,
        verbose=verbose or global_verbose,
    )

    # Post-pipeline: show verdict summary from batch results
    batch_file = output_dir / "full_batch_results.json"
    if batch_file.exists():
        try:
            results = json.loads(batch_file.read_text(encoding="utf-8"))
            if isinstance(results, list) and results:
                console.print(build_verdict_summary_table(results))
        except Exception:
            pass

    report = output_dir / "TOP_3_SWING_TRADES.md"
    console.print(
        f"\n[idx.ok]Pipeline complete.[/idx.ok]  [idx.path]{report}[/idx.path]"
    )


app = typer.Typer(help="End-to-end orchestration commands.")
app.command(name="pipeline")(pipeline_command)


__all__ = ["app", "pipeline_command", "run_pipeline_cli"]
