from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from app.cli.ui.console import console
from app.cli.ui.tables import build_filter_results_table


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
    ctx: typer.Context = typer.Context,
) -> None:
    """Run the quantitative swing-trade screener."""
    from core.quant_filter.config import CONFIG

    verbose = (ctx.obj or {}).get("verbose", False)
    scratch_dir = str(CONFIG.get("scratch_dir", "scratch"))

    if verbose:
        console.print(f"[idx.header]Running quantitative filter[/idx.header] top={top}")
        df = run_filter(top=top, input_file=input_file, output_dir=output_dir)
    else:
        from app.cli.ui.progress import quiet_filter_pipeline

        status_obj = console.status(
            "[idx.header]Screening IHSG universe...[/idx.header]"
        )
        with status_obj:
            with quiet_filter_pipeline(
                scratch_dir,
                lambda msg: status_obj.update(f"[idx.header]{msg}[/idx.header]"),
            ):
                df = run_filter(top=top, input_file=input_file, output_dir=output_dir)

    if df is None or df.empty:
        console.print("[idx.warn]No candidates passed all filters.[/idx.warn]")
        return

    console.print(build_filter_results_table(df, top_n=top))
    json_path = output_dir / "top10_candidates.json"
    console.print(
        f"\n[idx.ok]Top {len(df)} candidates[/idx.ok]  →  "
        f"[idx.path]{json_path}[/idx.path]  "
        f"[idx.muted]| report: {scratch_dir}/report.md[/idx.muted]"
    )


app = typer.Typer(help="Quantitative screening commands.")
app.command(name="filter")(filter_command)


__all__ = ["app", "filter_command", "run_filter"]
