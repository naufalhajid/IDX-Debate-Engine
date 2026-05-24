from __future__ import annotations

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
) -> None:
    import orchestrator

    if output_dir.name.lower() == "debates":
        base_dir = output_dir.parent
    else:
        base_dir = output_dir

    argv = ["--tickers", *tickers, "--output-dir", str(base_dir)]
    if verbose:
        argv.append("--verbose")
    orchestrator._run_cli(argv)


def debate_command(
    ctx: typer.Context,
    tickers: Annotated[
        list[str],
        typer.Argument(help="Ticker symbols to debate, e.g. BBRI BBCA TLKM."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for debate reports."),
    ] = Path("output/debates"),
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Show raw Loguru logs in addition to Rich output."),
    ] = False,
) -> None:
    """Run the existing AI debate chamber for one or more tickers."""
    normalized = _normalize_tickers(tickers)
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
    )
    console.print(
        "[idx.ok]Debate selesai.[/idx.ok] "
        f"Output: [idx.path]{output_dir}[/idx.path]"
    )


app = typer.Typer(help="AI debate chamber commands.")
app.command(name="debate")(debate_command)


__all__ = ["app", "debate_command", "run_debate_cli"]
