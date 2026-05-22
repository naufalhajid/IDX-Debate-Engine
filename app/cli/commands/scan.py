from __future__ import annotations

import asyncio
import sys
from enum import Enum
from typing import Annotated
from unittest.mock import patch

import typer
from rich.panel import Panel

from app.cli.ui.console import console


class ExportFormat(str, Enum):
    excel = "excel"
    spreadsheet = "spreadsheet"


def run_scan(*, full: bool, export: ExportFormat) -> None:
    """Run the legacy ETL entrypoint with CLI-compatible argv mapping."""
    import main as legacy_scan

    argv = ["main.py"]
    if full:
        argv.append("--full-retrieve")
    argv.extend(["--output-format", export.value])

    with patch.object(sys, "argv", argv):
        asyncio.run(legacy_scan.main_async())


def scan_command(
    full: Annotated[
        bool,
        typer.Option("--full", "-f", help="Retrieve the full IDX stock universe."),
    ] = False,
    export: Annotated[
        ExportFormat,
        typer.Option(
            "--export",
            "-e",
            help="Output format used by the existing ETL analyser.",
        ),
    ] = ExportFormat.spreadsheet,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show the planned ETL run without executing it."),
    ] = False,
) -> None:
    """Collect IDX data and build the existing ETL outputs."""
    if dry_run:
        mode = "full universe" if full else "first IDX page"
        console.print(
            Panel.fit(
                f"Mode: {mode}\nExport: {export.value}\nEntrypoint: main.main_async()",
                title="IDX Scan Dry Run",
                border_style="idx.header",
            )
        )
        return

    console.print(f"[idx.header]Starting IDX scan[/idx.header] export={export.value}")
    run_scan(full=full, export=export)
    console.print("[idx.ok]Scan complete.[/idx.ok]")


app = typer.Typer(help="Data collection and ETL commands.")
app.command(name="scan")(scan_command)


__all__ = ["ExportFormat", "app", "run_scan", "scan_command"]
