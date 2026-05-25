from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from app.cli.ui.console import console


def _normalize_tickers(tickers: list[str]) -> list[str]:
    normalized = [ticker.strip().upper() for ticker in tickers if ticker.strip()]
    if not normalized:
        raise typer.BadParameter("Provide at least one ticker.")
    return normalized


def run_debate_cli(
    *,
    tickers: list[str],
    output_dir: Path,
    verbose: bool = False,
    details: bool = True,
) -> None:
    import run_debate

    argv = ["--tickers", *tickers, "--output-dir", str(output_dir)]
    if verbose:
        argv.append("--verbose")
    if not details:
        argv.append("--no-details")
    asyncio.run(run_debate.main(argv))


def debate_command(
    ctx: typer.Context,
    tickers: Annotated[
        list[str] | None,
        typer.Argument(help="Ticker symbols to debate, e.g. BBRI BBCA TLKM."),
    ] = None,
    ticker_options: Annotated[
        list[str] | None,
        typer.Option(
            "--tickers",
            "--ticker",
            help=(
                "Ticker symbols to debate. Supports "
                "`--tickers BBRI BBCA` for compatibility."
            ),
        ),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for debate reports."),
    ] = Path("output/debates"),
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Show raw Loguru logs in addition to Rich output."),
    ] = False,
    details: Annotated[
        bool,
        typer.Option("--details/--no-details", help="Show detailed debate panels on console."),
    ] = True,
) -> None:
    """Run the existing AI debate chamber for one or more tickers."""
    normalized = _normalize_tickers(
        list(ticker_options or []) + list(tickers or []) + list(ctx.args)
    )
    root_ctx = ctx.find_root()
    global_verbose = bool(((root_ctx.obj or {}) if root_ctx else {}).get("verbose"))
    selected_verbose = verbose or global_verbose
    console.print(
        "[idx.header]Memulai debate[/idx.header] "
        f"tickers={', '.join(normalized)}"
    )
    run_debate_cli(
        tickers=normalized,
        output_dir=output_dir,
        verbose=selected_verbose,
        details=details,
    )
    console.print(
        "[idx.ok]Debate selesai.[/idx.ok] "
        f"Output: [idx.path]{output_dir}[/idx.path]"
    )


app = typer.Typer(help="AI debate chamber commands.")
app.command(name="debate", context_settings={"allow_extra_args": True})(debate_command)


__all__ = ["app", "debate_command", "run_debate_cli"]
