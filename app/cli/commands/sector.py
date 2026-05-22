from __future__ import annotations

import json
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


def build_command() -> None:
    """Build sector_cache.json using the existing yfinance builder."""
    console.print("[idx.header]Building sector cache[/idx.header]")
    run_sector_build()


def list_command(
    cache_file: Annotated[
        Path,
        typer.Option("--cache-file", help="Path to sector_cache.json."),
    ] = Path("output/sector_cache.json"),
) -> None:
    """List sector distribution from the current cache."""
    cache = load_sector_cache(cache_file)
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
        return
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
