from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from app.cli.mode_utils import normalize_screener_mode
from app.cli.ui.console import console
from app.cli.ui.tables import build_filter_results_table


def run_filter(
    *,
    top: int,
    input_file: Path | None,
    output_dir: Path,
    mode: str = "momentum",
) -> object:
    from core.quant_filter.config import CONFIG
    from core.quant_filter.pipeline import run_pipeline

    cfg = dict(CONFIG)
    cfg["top_n"] = top
    cfg["output_dir"] = str(output_dir)
    cfg["screener_mode"] = normalize_screener_mode(mode)
    if input_file is not None:
        cfg["input_file"] = str(input_file)
    return run_pipeline(cfg)


def filter_command(
    mode_arg: Annotated[
        str | None,
        typer.Argument(
            help="Optional strategy alias: momentum, mom, trend, mean-reversion, or mr."
        ),
    ] = None,
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
    mode: Annotated[
        str | None,
        typer.Option(
            "--mode",
            help="Strategy: 'momentum' (trend-following, default) or "
            "'mean-reversion' (oversold pullbacks in an uptrend).",
        ),
    ] = None,
    ctx: typer.Context = typer.Context,
) -> None:
    """Screen top swing-trade candidates from IHSG stocks using quant signals."""
    from core.quant_filter.config import CONFIG

    verbose = (ctx.obj or {}).get("verbose", False)
    scratch_dir = str(CONFIG.get("scratch_dir", "scratch"))
    option_mode = normalize_screener_mode(mode) if mode is not None else None
    positional_mode = (
        normalize_screener_mode(mode_arg) if mode_arg is not None else None
    )
    if option_mode is not None and positional_mode is not None:
        if option_mode != positional_mode:
            console.print(
                "[idx.error]positional mode conflicts with --mode; "
                "choose one strategy.[/idx.error]"
            )
            raise typer.Exit(code=2)
    norm_mode = positional_mode or option_mode or "momentum"

    if verbose:
        console.print(
            f"[idx.header]Running quantitative filter[/idx.header] "
            f"top={top} mode={norm_mode}"
        )
        df = run_filter(
            top=top, input_file=input_file, output_dir=output_dir, mode=norm_mode
        )
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
                df = run_filter(
                    top=top,
                    input_file=input_file,
                    output_dir=output_dir,
                    mode=norm_mode,
                )

    if df is None or df.empty:
        import json as _json

        watchlist_path = output_dir / "watchlist_candidates.json"
        watchlist: list[dict] = []
        if watchlist_path.exists():
            try:
                watchlist = _json.loads(watchlist_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        if watchlist:
            _regime = watchlist[0].get("regime", "?")
            _floor = watchlist[0].get("score_floor", "?")
            console.print(
                f"[idx.warn]No candidates passed all filters.[/idx.warn] "
                f"[idx.muted](regime={_regime}, score floor={_floor})[/idx.muted]"
            )
            console.print("\n[idx.header]Watchlist (belum trigger):[/idx.header]")
            for _w in watchlist[:5]:
                console.print(
                    f"  [idx.ticker]{_w['Ticker']}[/idx.ticker]"
                    f"  score=[bold]{_w.get('Composite Score', 0):.1f}[/bold]"
                    f"  [{_w.get('Weekly Trend', '?')}]"
                )
            console.print(
                "\n[idx.muted]→ Jalankan ulang jika IHSG volatility turun atau score naik.[/idx.muted]"
            )
        else:
            console.print("[idx.warn]No candidates passed all filters.[/idx.warn]")
        return

    console.print(build_filter_results_table(df, top_n=top))
    json_path = output_dir / "top10_candidates.json"
    console.print(
        f"\n[idx.ok]Top {len(df)} candidates[/idx.ok]  ->  "
        f"[idx.path]{json_path}[/idx.path]  "
        f"[idx.muted]| report: {scratch_dir}/report.md[/idx.muted]"
    )


app = typer.Typer(help="Quantitative screening commands.")
app.command(name="filter")(filter_command)


__all__ = ["app", "filter_command", "run_filter"]
