from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from app.cli.ui.console import console


def run_pipeline_cli(
    *,
    dry_run: bool,
    output_dir: Path,
    tickers: tuple[str, ...],
    skip_scraping: bool,
    no_interactive: bool,
    mode: str,
    verbose: bool,
) -> None:
    import orchestrator

    argv = ["--output-dir", str(output_dir), "--mode", mode]
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
        typer.Option("--dry-run", help="Use mock debate results; no Gemini calls."),
    ] = False,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for pipeline artifacts."),
    ] = Path("output"),
    tickers: Annotated[
        list[str] | None,
        typer.Option(
            "--tickers",
            help=(
                "Override candidates with ticker symbols. Accepts "
                "`--tickers BBRI BBCA` for compatibility."
            ),
        ),
    ] = None,
    skip_scraping: Annotated[
        bool,
        typer.Option("--skip-scraping", help="Skip pre-pipeline scraping."),
    ] = False,
    no_interactive: Annotated[
        bool,
        typer.Option("--no-interactive", help="Run without Rich prompts."),
    ] = False,
    mode: Annotated[
        str,
        typer.Option("--mode", help="Orchestrator mode: multi, single, or compare."),
    ] = "multi",
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Enable verbose orchestrator logging."),
    ] = False,
) -> None:
    """Run the end-to-end swing-trade orchestration pipeline."""
    selected_mode = mode.lower().strip()
    if selected_mode not in {"multi", "single", "compare"}:
        raise typer.BadParameter("mode must be one of: multi, single, compare")
    root_ctx = ctx.find_root()
    global_verbose = bool(((root_ctx.obj or {}) if root_ctx else {}).get("verbose"))
    selected_tickers = tuple(
        ticker.strip().upper()
        for ticker in tuple(tickers or ()) + tuple(ctx.args)
        if ticker.strip()
    )
    console.print(f"[idx.header]Starting pipeline[/idx.header] mode={selected_mode}")
    run_pipeline_cli(
        dry_run=dry_run,
        output_dir=output_dir,
        tickers=selected_tickers,
        skip_scraping=skip_scraping,
        no_interactive=no_interactive,
        mode=selected_mode,
        verbose=verbose or global_verbose,
    )


app = typer.Typer(help="End-to-end orchestration commands.")
app.command(name="pipeline")(pipeline_command)


__all__ = ["app", "pipeline_command", "run_pipeline_cli"]
