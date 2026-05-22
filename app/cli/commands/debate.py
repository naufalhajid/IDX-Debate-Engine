from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated
from unittest.mock import patch

import typer

from app.cli.ui.console import console


def _normalize_tickers(tickers: list[str]) -> list[str]:
    normalized = [ticker.strip().upper() for ticker in tickers if ticker.strip()]
    if not normalized:
        raise typer.BadParameter("Provide at least one ticker.")
    return normalized


def run_debate_cli(*, tickers: list[str], output_dir: Path) -> None:
    import run_debate

    argv = ["run_debate.py", "--tickers", *tickers, "--output-dir", str(output_dir)]
    with patch.object(sys, "argv", argv):
        asyncio.run(run_debate.main())


def debate_command(
    tickers: Annotated[
        list[str],
        typer.Argument(help="Ticker symbols to debate, e.g. BBRI BBCA TLKM."),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for debate reports."),
    ] = Path("output/debates"),
) -> None:
    """Run the existing AI debate chamber for one or more tickers."""
    normalized = _normalize_tickers(tickers)
    console.print(
        f"[idx.header]Starting debate[/idx.header] tickers={', '.join(normalized)}"
    )
    run_debate_cli(tickers=normalized, output_dir=output_dir)
    console.print(f"[idx.ok]Debate complete.[/idx.ok] Output: [idx.path]{output_dir}[/idx.path]")


app = typer.Typer(help="AI debate chamber commands.")
app.command(name="debate")(debate_command)


__all__ = ["app", "debate_command", "run_debate_cli"]
