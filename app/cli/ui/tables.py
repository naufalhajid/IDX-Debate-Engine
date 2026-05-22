from __future__ import annotations

from collections import Counter
from typing import Any

from rich.table import Table


def build_sector_distribution_table(cache: dict[str, Any]) -> Table:
    """Build a compact sector distribution table from sector_cache.json payloads."""
    dist: Counter[str] = Counter()
    for value in cache.values():
        if isinstance(value, dict):
            sector = str(value.get("sector") or "default")
        else:
            sector = str(value or "default")
        dist[sector] += 1

    table = Table(title="Sector Cache")
    table.add_column("Sector", style="idx.header")
    table.add_column("Tickers", justify="right")
    for sector, count in sorted(dist.items(), key=lambda item: (-item[1], item[0])):
        table.add_row(sector, str(count))
    return table


def build_sector_members_table(
    sector: str,
    members: list[tuple[str, dict[str, Any] | str]],
) -> Table:
    table = Table(title=f"Sector: {sector}")
    table.add_column("Ticker", style="idx.ticker")
    table.add_column("Yahoo Sector")
    table.add_column("Yahoo Industry")
    for ticker, payload in members:
        if isinstance(payload, dict):
            table.add_row(
                ticker,
                str(payload.get("yf_sector") or "-"),
                str(payload.get("yf_industry") or "-"),
            )
        else:
            table.add_row(ticker, "-", "-")
    return table
