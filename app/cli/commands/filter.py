from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from app.cli.ui.console import console


def run_filter(
    *,
    top: int,
    input_file: Path | None,
    output_dir: Path,
) -> object:
    from core.quant_filter.config import CONFIG
    from core.quant_filter.pipeline import run_pipeline

    cfg = dict(CONFIG)
    cfg["top_n"] = top
    cfg["output_dir"] = str(output_dir)
    if input_file is not None:
        cfg["input_file"] = str(input_file)
    return run_pipeline(cfg)


def filter_command(
    top: Annotated[
        int,
        typer.Option("--top", "-n", min=1, help="Number of candidates to keep."),
    ] = 10,
    input_file: Annotated[
        Path | None,
        typer.Option("--input-file", help="Excel workbook to screen."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Directory for top10_candidates.json."),
    ] = Path("output"),
) -> None:
    """Run the quantitative swing-trade screener."""
    console.print(f"[idx.header]Running quantitative filter[/idx.header] top={top}")
    run_filter(top=top, input_file=input_file, output_dir=output_dir)
    console.print(
        f"[idx.ok]Filter complete.[/idx.ok] Results: [idx.path]{output_dir / 'top10_candidates.json'}[/idx.path]"
    )


app = typer.Typer(help="Quantitative screening commands.")
app.command(name="filter")(filter_command)


__all__ = ["app", "filter_command", "run_filter"]
