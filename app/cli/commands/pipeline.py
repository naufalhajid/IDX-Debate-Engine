from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel

from app.cli.mode_utils import (
    RETIRED_PIPELINE_MODES,
    format_screener_mode,
    is_pipeline_mode_token,
    is_screener_mode_token,
    normalize_pipeline_mode,
    normalize_screener_mode,
)
from app.cli.ui.console import console
from app.cli.ui.tables import build_verdict_summary_table


def _select_from_menu(
    *,
    title: str,
    choices: list[tuple[str, str, str]],
    default: str,
) -> str:
    console.print(f"\n{title}:")
    for number, value, label in choices:
        marker = " [idx.ok]<- default[/idx.ok]" if value == default else ""
        console.print(f"  [[idx.highlight]{number}[/idx.highlight]] {label}{marker}")

    try:
        choice = input(f"Enter your choice [1-{len(choices)}]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[idx.warn]Cancelled.[/idx.warn]")
        raise typer.Exit()

    if not choice:
        return default
    for number, value, _label in choices:
        if choice == number:
            return value

    console.print("[idx.warn]Invalid choice, using default.[/idx.warn]")
    return default


def _choose_pipeline_modes() -> tuple[str, str]:
    console.print(
        Panel(
            "Choose how the full pipeline should screen and debate candidates.",
            title="[idx.header]IDX Pipeline Mode[/idx.header]",
            border_style="idx.header",
            expand=False,
        )
    )
    selected_mode = _select_from_menu(
        title="Pipeline mode",
        choices=[
            ("1", "multi", "Production multi-agent debate"),
            ("2", "compare", "Research comparison (deprecated here)"),
        ],
        default="multi",
    )
    selected_screener_mode = _select_from_menu(
        title="Screener strategy",
        choices=[
            ("1", "momentum", "Momentum swing"),
            ("2", "mean_reversion", "Mean-reversion swing"),
        ],
        default="momentum",
    )
    return selected_mode, selected_screener_mode


def _resolve_pipeline_tokens(
    *,
    extra_args: list[str],
    mode: str | None,
    screener_mode: str | None,
    no_interactive: bool,
) -> tuple[str, str, tuple[str, ...]]:
    lowered = [token.strip().lower() for token in extra_args]
    if "choose" in lowered:
        if no_interactive:
            console.print(
                "[idx.error]choose cannot be used with --no-interactive[/idx.error]"
            )
            raise typer.Exit(code=2)
        if len(extra_args) > 1:
            raise typer.BadParameter(
                "choose cannot be combined with positional modes or tickers; "
                "use --tickers for ticker overrides."
            )
        if mode is not None or screener_mode is not None:
            raise typer.BadParameter(
                "choose cannot be combined with --mode or --screener-mode"
            )
        selected_mode, selected_screener_mode = _choose_pipeline_modes()
        return selected_mode, selected_screener_mode, ()

    selected_mode = normalize_pipeline_mode(mode) if mode is not None else "multi"
    selected_screener_mode = (
        normalize_screener_mode(screener_mode)
        if screener_mode is not None
        else "momentum"
    )
    positional_mode: str | None = None
    positional_screener_mode: str | None = None
    positional_tickers: list[str] = []

    for token in extra_args:
        cleaned = token.strip().lower().replace("-", "_")
        if cleaned in RETIRED_PIPELINE_MODES:
            normalize_pipeline_mode(token)
        if is_pipeline_mode_token(token):
            token_mode = normalize_pipeline_mode(token)
            if mode is not None and token_mode != selected_mode:
                raise typer.BadParameter(
                    "positional pipeline mode conflicts with --mode"
                )
            if positional_mode is not None and token_mode != positional_mode:
                raise typer.BadParameter("multiple pipeline modes were provided")
            positional_mode = token_mode
            selected_mode = token_mode
        elif is_screener_mode_token(token):
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

    return selected_mode, selected_screener_mode, tuple(positional_tickers)


def _build_orchestrator_argv(
    *,
    dry_run: bool,
    output_dir: Path,
    tickers: tuple[str, ...],
    skip_scraping: bool,
    no_interactive: bool,
    mode: str,
    screener_mode: str,
    verbose: bool,
    portfolio_loss_pct: float | None = None,
) -> list[str]:
    argv = [
        "--output-dir",
        str(output_dir),
        "--mode",
        mode,
        "--screener-mode",
        screener_mode,
    ]
    if mode == "compare":
        argv.append("--research-compare")
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
    if portfolio_loss_pct is not None:
        argv.extend(["--portfolio-loss-pct", str(portfolio_loss_pct)])
    return argv


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
    portfolio_loss_pct: float | None = None,
) -> None:
    import orchestrator

    argv = _build_orchestrator_argv(
        dry_run=dry_run,
        output_dir=output_dir,
        tickers=tickers,
        skip_scraping=skip_scraping,
        no_interactive=no_interactive,
        mode=mode,
        screener_mode=screener_mode,
        verbose=verbose,
        portfolio_loss_pct=portfolio_loss_pct,
    )
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
        str | None,
        typer.Option(
            "--mode",
            help=(
                "Pipeline mode: multi (default). compare is a temporary "
                "deprecated alias for `idx research compare`."
            ),
        ),
    ] = None,
    screener_mode: Annotated[
        str | None,
        typer.Option(
            "--screener-mode",
            help="Quant-filter strategy: 'momentum' (default) or 'mean-reversion' "
            "(oversold pullbacks). Mean-reversion forces a fresh screener run.",
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
                "(e.g. 3.5 = -3.5%%). At >= 3%% the daily-loss circuit breaker "
                "halts all position sizing for this batch."
            ),
        ),
    ] = None,
) -> None:
    """Full automated pipeline: quant filter + AI debate + risk gate + TOP_3 report."""
    selected_mode, selected_screener_mode, positional_tickers = (
        _resolve_pipeline_tokens(
            extra_args=list(ctx.args),
            mode=mode,
            screener_mode=screener_mode,
            no_interactive=no_interactive,
        )
    )

    root_ctx = ctx.find_root()
    global_verbose = bool(((root_ctx.obj or {}) if root_ctx else {}).get("verbose"))
    selected_tickers = tuple(
        ticker.strip().upper()
        for ticker in tuple(tickers or ()) + positional_tickers
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

    # Codex-only panel line: reflect the actual configured efforts instead of
    # claiming "deep", and stay silent for gemini/anthropic providers.
    from core.settings import settings
    from providers.codex_adapter import DEEP_REASONING_MAX_TICKERS

    reasoning_line = ""
    if str(settings.DEFAULT_LLM_PROVIDER or "").lower() == "codex":
        if 0 < len(selected_tickers) <= DEEP_REASONING_MAX_TICKERS:
            reasoning_label = (
                f"on — flash={settings.CODEX_FLASH_REASONING_EFFORT}, "
                f"pro={settings.CODEX_PRO_REASONING_EFFORT} "
                f"(explicit <={DEEP_REASONING_MAX_TICKERS} tickers)"
            )
        else:
            reasoning_label = "off (fast batch)"
        reasoning_line = f"\n[idx.label]Codex Reasoning:[/idx.label] {reasoning_label}"

    flags_line = (
        f"\n[idx.label]Flags:[/idx.label]    [idx.muted]{', '.join(flags)}[/idx.muted]"
        if flags
        else ""
    )

    console.print(
        Panel(
            f"[idx.label]Pipeline Mode:[/idx.label]  [idx.highlight]{selected_mode}[/idx.highlight]\n"
            f"[idx.label]Screener:[/idx.label]       [idx.highlight]{format_screener_mode(selected_screener_mode)}[/idx.highlight]\n"
            f"[idx.label]Tickers:[/idx.label]        {ticker_label}"
            + reasoning_line
            + flags_line,
            title="[idx.header]IDX Pipeline[/idx.header]",
            border_style="idx.header",
            expand=False,
        )
    )
    if selected_mode == "compare":
        console.print(
            "[idx.warn]Deprecated:[/idx.warn] `idx pipeline compare` is a "
            "temporary alias. Use `idx research compare` for comparison runs."
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
        portfolio_loss_pct=portfolio_loss_pct,
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
