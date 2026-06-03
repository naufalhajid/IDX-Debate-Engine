from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from app.cli.ui.console import console
from app.cli.ui.tables import build_sector_distribution_table, build_sector_members_table

SECTOR_ALIASES = {
    "banking": "bank",
    "banks": "bank",
    "telco": "tech",
    "telecom": "tech",
}


def run_sector_build() -> None:
    import build_sector_cache

    build_sector_cache.main()


def load_sector_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise typer.BadParameter(f"Sector cache not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"Sector cache must be a JSON object: {path}")
    return payload


def build_command(ctx: typer.Context = typer.Context) -> None:
    """Build sector_cache.json using the existing yfinance builder."""
    verbose = (ctx.obj or {}).get("verbose", False)
    if verbose:
        console.print("[idx.header]Building sector cache[/idx.header]")
        run_sector_build()
    else:
        with console.status("[idx.header]Fetching sector data from yfinance...[/idx.header]"):
            run_sector_build()
    console.print("[idx.ok]Sector cache built.[/idx.ok]")


def list_command(
    cache_file: Annotated[
        Path,
        typer.Option("--cache-file", help="Path to sector_cache.json."),
    ] = Path("output/sector_cache.json"),
) -> None:
    """List sector distribution from the current cache."""
    cache = load_sector_cache(cache_file)

    if cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        age_h = (datetime.now() - mtime).total_seconds() / 3600
        console.print(
            f"[idx.muted]{len(cache)} tickers  |  {cache_file}  |  "
            f"updated {mtime:%Y-%m-%d %H:%M} ({age_h:.1f}h ago)[/idx.muted]"
        )

    console.print(build_sector_distribution_table(cache))


def show_command(
    sector: Annotated[str, typer.Argument(help="Sector key to show.")],
    cache_file: Annotated[
        Path,
        typer.Option("--cache-file", help="Path to sector_cache.json."),
    ] = Path("output/sector_cache.json"),
) -> None:
    """Show cached tickers in one sector."""
    sector_key = SECTOR_ALIASES.get(sector.strip().lower(), sector.strip().lower())
    cache = load_sector_cache(cache_file)
    members: list[tuple[str, dict[str, Any] | str]] = []
    for ticker, payload in sorted(cache.items()):
        cached_sector = (
            str(payload.get("sector") or "default")
            if isinstance(payload, dict)
            else str(payload or "default")
        )
        if cached_sector.lower() == sector_key:
            members.append((ticker, payload))

    if not members:
        console.print(f"[idx.warn]No tickers found for sector:[/idx.warn] {sector_key}")
        aliases = [f"'{k}' -> '{v}'" for k, v in SECTOR_ALIASES.items()]
        console.print(f"[idx.muted]Known aliases: {', '.join(aliases)}[/idx.muted]")
        return

    if sector_key != sector.strip().lower():
        console.print(
            f"[idx.muted]Alias resolved: '{sector}' -> '{sector_key}'[/idx.muted]"
        )
    console.print(
        f"[idx.header]{len(members)} tickers[/idx.header] in [idx.ticker]{sector_key}[/idx.ticker]"
    )
    console.print(build_sector_members_table(sector_key, members))


app = typer.Typer(help="Sector cache commands.", no_args_is_help=True)
app.command(name="build")(build_command)
app.command(name="list")(list_command)
app.command(name="show")(show_command)


__all__ = [
    "app",
    "build_command",
    "list_command",
    "load_sector_cache",
    "run_sector_build",
    "show_command",
]
