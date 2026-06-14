from __future__ import annotations

# ruff: noqa: E402

import warnings

_original_showwarning = warnings.showwarning


def _custom_showwarning(message, category, filename, lineno, file=None, line=None):
    if "allowed_objects" in str(message) or "LangChain" in category.__name__:
        return
    _original_showwarning(message, category, filename, lineno, file, line)


warnings.showwarning = _custom_showwarning

from importlib import metadata
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table

from app.cli.commands import debate, filter, pipeline, scan, sector, model, auth, backtest
from app.cli.ui.console import console


_DEBATE_EPILOG = (
    "Examples:\n\n"
    "  idx debate BBCA BMRI TLKM\n"
    "  idx debate --tickers BBCA BMRI --output-dir output/custom\n"
    "  idx --verbose debate BBCA\n"
)

_PIPELINE_EPILOG = (
    "Examples:\n\n"
    "  idx pipeline                           # auto-select from quant filter\n"
    "  idx pipeline mr                        # mean-reversion screener\n"
    "  idx pipeline single mr BBCA            # single-agent debate for BBCA\n"
    "  idx pipeline choose                    # interactive mode selector\n"
    "  idx pipeline --tickers BBCA BMRI ADMR  # specific tickers only\n"
    "  idx pipeline --dry-run                 # simulate without writing records\n"
    "  idx pipeline --skip-scraping           # reuse cached data (faster)\n"
)


def _version_callback(value: bool) -> None:
    if not value:
        return
    try:
        version = metadata.version("idx-fundamental")
    except metadata.PackageNotFoundError:
        version = "0.1.0"
    console.print(f"idx {version}")
    raise typer.Exit()


def _print_workflow_panel() -> None:
    body = Table.grid(padding=(0, 2))
    body.add_column(style="bold cyan", no_wrap=True)
    body.add_column()
    body.add_row("1. idx filter", "Screen top IHSG candidates using quant signals")
    body.add_row("2. idx debate", "AI multi-agent debate for specific tickers")
    body.add_row("3. idx pipeline", "Full automated run — filter + debate + risk gate")
    body.add_row("", "")
    body.add_row(
        "[dim]idx auth[/dim]",
        "[dim]Authenticate providers (run once before first use)[/dim]",
    )
    body.add_row(
        "[dim]idx model[/dim]", "[dim]Switch LLM provider and model variants[/dim]"
    )
    body.add_row(
        "[dim]idx backtest[/dim]",
        "[dim]Score historical CIO verdicts against actual IDX prices[/dim]",
    )
    body.add_row(
        "[dim]idx scan[/dim]", "[dim]Refresh IDX stock data from providers (ETL)[/dim]"
    )
    body.add_row("", "")
    body.add_row(
        "[dim]Tip:[/dim]",
        "[dim]Run  idx <command> --help  for options and examples[/dim]",
    )
    console.print(
        Panel(
            body,
            title="[bold]IDX Fundamental Analysis[/bold]",
            subtitle="[dim]Quant Scouting  ->  Multi-Agent Debate  ->  CIO Verdict[/dim]",
            border_style="cyan",
            expand=False,
        )
    )


app = typer.Typer(
    name="idx",
    help="IDX Swing Trade Analysis — AI-powered multi-agent debate engine.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the idx CLI version and exit.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Enable verbose CLI mode where supported."),
    ] = False,
) -> None:
    """IDX Swing Trade Analysis — AI-powered multi-agent debate engine."""
    ctx.obj = {"verbose": verbose}
    if ctx.invoked_subcommand is None:
        _print_workflow_panel()
        raise typer.Exit()


app.command(name="scan")(scan.scan_command)
app.command(name="filter")(filter.filter_command)
app.command(name="debate", epilog=_DEBATE_EPILOG)(debate.debate_command)
app.command(
    name="pipeline",
    context_settings={"allow_extra_args": True},
    epilog=_PIPELINE_EPILOG,
)(pipeline.pipeline_command)
app.command(name="model")(model.model_command)
app.command(name="backtest")(backtest.backtest_command)
app.add_typer(sector.app, name="sector")
app.add_typer(auth.app, name="auth")

if __name__ == "__main__":
    app()
