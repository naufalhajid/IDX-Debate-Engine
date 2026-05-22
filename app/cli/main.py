from __future__ import annotations

from importlib import metadata
from typing import Annotated

import typer

from app.cli.commands import debate, filter, pipeline, scan, sector, serve
from app.cli.ui.console import console


def _version_callback(value: bool) -> None:
    if not value:
        return
    try:
        version = metadata.version("idx-fundamental")
    except metadata.PackageNotFoundError:
        version = "0.1.0"
    console.print(f"idx {version}")
    raise typer.Exit()


app = typer.Typer(
    name="idx",
    help="Unified CLI for IDX Fundamental Analysis.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)


@app.callback()
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
    """Unified CLI for IDX Fundamental Analysis."""
    ctx.obj = {"verbose": verbose}


app.command(name="scan")(scan.scan_command)
app.command(name="filter")(filter.filter_command)
app.command(name="debate")(debate.debate_command)
app.command(
    name="pipeline",
    context_settings={"allow_extra_args": True},
)(pipeline.pipeline_command)
app.command(name="serve")(serve.serve_command)
app.add_typer(sector.app, name="sector")


if __name__ == "__main__":
    app()
