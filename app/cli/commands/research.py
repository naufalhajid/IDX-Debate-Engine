from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel

from app.cli.commands.pipeline import _build_orchestrator_argv
from app.cli.mode_utils import (
    RETIRED_PIPELINE_MODES,
    format_screener_mode,
    is_pipeline_mode_token,
    is_screener_mode_token,
    normalize_pipeline_mode,
    normalize_screener_mode,
)
from app.cli.ui.console import console


def _resolve_research_compare_tokens(
    *,
    extra_args: list[str],
    screener_mode: str | None,
) -> tuple[str, tuple[str, ...]]:
    selected_screener_mode = (
        normalize_screener_mode(screener_mode)
        if screener_mode is not None
        else "momentum"
    )
    positional_screener_mode: str | None = None
    positional_tickers: list[str] = []

    for token in extra_args:
        cleaned = token.strip().lower().replace("-", "_")
        if cleaned in RETIRED_PIPELINE_MODES:
            normalize_pipeline_mode(token)
        if is_pipeline_mode_token(token):
            raise typer.BadParameter(
                "`idx research compare` already selects comparison mode; "
                "pass only a screener mode or ticker symbols."
            )
        if is_screener_mode_token(token):
            token_screener_mode = normalize_screener_mode(token)
            if (
                screener_mode is not None
                and token_screener_mode != selected_screener_mode
            ):
                raise typer.BadParameter(
                    "positional screener mode conflicts with --screener-mode"
                )
            if (
                positional_screener_mode is not None
                and token_screener_mode != positional_screener_mode
            ):
                raise typer.BadParameter("multiple screener modes were provided")
            positional_screener_mode = token_screener_mode
            selected_screener_mode = token_screener_mode
        else:
            positional_tickers.append(token)

    return selected_screener_mode, tuple(positional_tickers)


def run_research_compare_cli(
    *,
    dry_run: bool,
    output_dir: Path,
    tickers: tuple[str, ...],
    skip_scraping: bool,
    no_interactive: bool,
    screener_mode: str,
    verbose: bool,
    portfolio_loss_pct: float | None = None,
) -> None:
    import orchestrator

    argv = _build_orchestrator_argv(
        dry_run=dry_run,
        output_dir=output_dir,
        tickers=tickers,
        skip_scraping=skip_scraping,
        no_interactive=no_interactive,
        mode="compare",
        screener_mode=screener_mode,
        verbose=verbose,
        portfolio_loss_pct=portfolio_loss_pct,
    )
    orchestrator._run_cli(argv)


def compare_command(
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
            "--output-dir", help="Directory for comparison artifacts and reports."
        ),
    ] = Path("output"),
    tickers: Annotated[
        list[str] | None,
        typer.Option(
            "--tickers",
            help="Override quant filter and compare only these tickers.",
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
    screener_mode: Annotated[
        str | None,
        typer.Option(
            "--screener-mode",
            help="Quant-filter strategy: 'momentum' (default) or 'mean-reversion'.",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Enable verbose orchestrator logging."),
    ] = False,
    portfolio_loss_pct: Annotated[
        float | None,
        typer.Option(
            "--portfolio-loss-pct",
            help=(
                "Today's realized portfolio loss as a positive percentage "
                "(e.g. 3.5 = -3.5%%)."
            ),
        ),
    ] = None,
) -> None:
    """Explicit research comparison: single-agent baseline vs production debate."""
    selected_screener_mode, positional_tickers = _resolve_research_compare_tokens(
        extra_args=list(ctx.args),
        screener_mode=screener_mode,
    )
    root_ctx = ctx.find_root()
    global_verbose = bool(((root_ctx.obj or {}) if root_ctx else {}).get("verbose"))
    selected_tickers = tuple(
        ticker.strip().upper()
        for ticker in tuple(tickers or ()) + positional_tickers
        if ticker.strip()
    )
    ticker_label = (
        ", ".join(selected_tickers) if selected_tickers else "(from quant filter)"
    )
    flags = [
        label
        for enabled, label in (
            (dry_run, "dry-run"),
            (skip_scraping, "skip-scraping"),
            (no_interactive, "no-interactive"),
        )
        if enabled
    ]
    flags_line = (
        f"\n[idx.label]Flags:[/idx.label]    [idx.muted]{', '.join(flags)}[/idx.muted]"
        if flags
        else ""
    )

    console.print(
        Panel(
            "[idx.label]Research Mode:[/idx.label] [idx.highlight]compare[/idx.highlight]\n"
            f"[idx.label]Screener:[/idx.label]      [idx.highlight]{format_screener_mode(selected_screener_mode)}[/idx.highlight]\n"
            f"[idx.label]Tickers:[/idx.label]       {ticker_label}"
            + flags_line,
            title="[idx.header]IDX Research[/idx.header]",
            border_style="idx.header",
            expand=False,
        )
    )

    run_research_compare_cli(
        dry_run=dry_run,
        output_dir=output_dir,
        tickers=selected_tickers,
        skip_scraping=skip_scraping,
        no_interactive=no_interactive,
        screener_mode=selected_screener_mode,
        verbose=verbose or global_verbose,
        portfolio_loss_pct=portfolio_loss_pct,
    )

    console.print(
        f"\n[idx.ok]Research comparison complete.[/idx.ok]  "
        f"[idx.path]{output_dir / 'comparison_report.md'}[/idx.path]"
    )


app = typer.Typer(help="Research and evaluation commands.")
app.command(name="compare", context_settings={"allow_extra_args": True})(
    compare_command
)


__all__ = ["app", "compare_command", "run_research_compare_cli"]
