"""Render BacktestMetrics as Rich tables or Markdown reports."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from app.cli.ui.console import console as default_console
from core.backtester.metrics_calculator import BacktestMetrics


def generate_rich_table(metrics: BacktestMetrics) -> Table:
    """Build a Rich Table summarizing backtest performance."""
    table = Table(
        title="[idx.header]IDX Backtest Performance[/idx.header]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Metric", style="bold", no_wrap=True)
    table.add_column("Value", justify="right")

    win_rate_str = f"{metrics.win_rate * 100:.1f}%" if metrics.win_rate is not None else "N/A"
    avg_pnl_str = f"{metrics.avg_pnl_pct:+.2f}%" if metrics.avg_pnl_pct is not None else "N/A"
    sharpe_str = f"{metrics.sharpe_ratio:.2f}" if metrics.sharpe_ratio is not None else "N/A"
    hold_str = f"{metrics.avg_holding_days:.0f} days" if metrics.avg_holding_days is not None else "N/A"

    table.add_row("Total Trades", str(metrics.total_trades))
    table.add_row("Wins", f"[idx.ok]{metrics.wins}[/idx.ok]")
    table.add_row("Losses", f"[idx.error]{metrics.losses}[/idx.error]")
    table.add_row("Open", f"[idx.muted]{metrics.open_trades}[/idx.muted]")
    table.add_row("Timeout / Flat", str(metrics.timeout_flat))
    table.add_row("Win Rate", f"[idx.ok]{win_rate_str}[/idx.ok]" if metrics.win_rate else win_rate_str)
    table.add_row("Avg PnL", avg_pnl_str)
    table.add_row("Avg Holding", hold_str)
    table.add_row("Sharpe (proxy)", sharpe_str)

    return table


def _per_ticker_table(metrics: BacktestMetrics) -> Table:
    table = Table(
        title="[idx.section]Per-Ticker Breakdown[/idx.section]",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Ticker", style="idx.ticker")
    table.add_column("Trades", justify="right")
    table.add_column("W", justify="right", style="idx.ok")
    table.add_column("L", justify="right", style="idx.error")
    table.add_column("Win %", justify="right")
    table.add_column("Avg PnL", justify="right")

    for ticker, stats in sorted(
        metrics.by_ticker.items(),
        key=lambda kv: kv[1].get("win_rate") or 0,
        reverse=True,
    ):
        wr = stats.get("win_rate")
        pnl = stats.get("avg_pnl_pct")
        table.add_row(
            ticker,
            str(stats["total"]),
            str(stats["wins"]),
            str(stats["losses"]),
            f"{wr * 100:.0f}%" if wr is not None else "N/A",
            f"{pnl:+.1f}%" if pnl is not None else "N/A",
        )
    return table


def _confidence_tier_table(metrics: BacktestMetrics) -> Table:
    table = Table(
        title="[idx.section]By Confidence Tier[/idx.section]",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Tier")
    table.add_column("Trades", justify="right")
    table.add_column("Win %", justify="right")
    table.add_column("Avg PnL", justify="right")

    for tier in metrics.by_confidence_tier:
        wr = f"{tier.win_rate * 100:.0f}%" if tier.win_rate is not None else "N/A"
        pnl = f"{tier.avg_pnl_pct:+.1f}%" if tier.avg_pnl_pct is not None else "N/A"
        table.add_row(tier.label, str(tier.total), wr, pnl)

    return table


def generate_markdown_report(
    metrics: BacktestMetrics,
    *,
    title: str = "IDX Backtest Report",
    generated_at: str | None = None,
) -> str:
    ts = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        f"# {title}",
        f"> Generated: {ts}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total Trades | {metrics.total_trades} |",
        f"| Wins | {metrics.wins} |",
        f"| Losses | {metrics.losses} |",
        f"| Open | {metrics.open_trades} |",
        f"| Timeout / Flat | {metrics.timeout_flat} |",
        f"| Win Rate | {f'{metrics.win_rate * 100:.1f}%' if metrics.win_rate is not None else 'N/A'} |",
        f"| Avg PnL | {f'{metrics.avg_pnl_pct:+.2f}%' if metrics.avg_pnl_pct is not None else 'N/A'} |",
        f"| Avg Holding | {f'{metrics.avg_holding_days:.0f} days' if metrics.avg_holding_days is not None else 'N/A'} |",
        f"| Sharpe (proxy) | {f'{metrics.sharpe_ratio:.2f}' if metrics.sharpe_ratio is not None else 'N/A'} |",
        "",
    ]

    if metrics.by_ticker:
        lines += [
            "## Per-Ticker",
            "",
            "| Ticker | Trades | Wins | Losses | Win % | Avg PnL |",
            "|---|---|---|---|---|---|",
        ]
        for ticker, stats in sorted(
            metrics.by_ticker.items(),
            key=lambda kv: kv[1].get("win_rate") or 0,
            reverse=True,
        ):
            wr = stats.get("win_rate")
            pnl = stats.get("avg_pnl_pct")
            lines.append(
                f"| {ticker} | {stats['total']} | {stats['wins']} | {stats['losses']} "
                f"| {f'{wr * 100:.0f}%' if wr is not None else 'N/A'} "
                f"| {f'{pnl:+.1f}%' if pnl is not None else 'N/A'} |"
            )
        lines.append("")

    if metrics.by_confidence_tier:
        lines += [
            "## By Confidence Tier",
            "",
            "| Tier | Trades | Win % | Avg PnL |",
            "|---|---|---|---|",
        ]
        for tier in metrics.by_confidence_tier:
            wr = f"{tier.win_rate * 100:.0f}%" if tier.win_rate is not None else "N/A"
            pnl = f"{tier.avg_pnl_pct:+.1f}%" if tier.avg_pnl_pct is not None else "N/A"
            lines.append(f"| {tier.label} | {tier.total} | {wr} | {pnl} |")
        lines.append("")

    if metrics.by_regime:
        lines += [
            "## By Market Regime",
            "",
            "| Regime | Trades | Wins | Losses | Win % | Avg PnL |",
            "|---|---|---|---|---|---|",
        ]
        for regime, stats in sorted(metrics.by_regime.items()):
            wr = stats.get("win_rate")
            pnl = stats.get("avg_pnl_pct")
            lines.append(
                f"| {regime} | {stats['total']} | {stats['wins']} | {stats['losses']} "
                f"| {f'{wr * 100:.0f}%' if wr is not None else 'N/A'} "
                f"| {f'{pnl:+.1f}%' if pnl is not None else 'N/A'} |"
            )
        lines.append("")

    if metrics.open_trades > 0 and metrics.open_by_age:
        lines += [
            "## Open Trades by Age",
            "",
            "| Age Bucket | Count |",
            "|---|---|",
        ]
        for bucket in ("<7d", "7-30d", ">30d"):
            lines.append(f"| {bucket} | {metrics.open_by_age.get(bucket, 0)} |")
        lines.append("")

    best = metrics.best_trade
    worst = metrics.worst_trade
    if best or worst:
        lines.append("## Notable Trades")
        lines.append("")
        if best:
            lines.append(
                f"**Best:** {best.ticker} on {best.entry_date}  "
                f"-> {f'{best.pnl_pct:+.1f}%' if best.pnl_pct is not None else 'N/A'} "
                f"({best.outcome})"
            )
        if worst:
            lines.append(
                f"**Worst:** {worst.ticker} on {worst.entry_date}  "
                f"-> {f'{worst.pnl_pct:+.1f}%' if worst.pnl_pct is not None else 'N/A'} "
                f"({worst.outcome})"
            )
        lines.append("")

    lines += [
        "---",
        "_Sharpe ratio uses per-trade total returns as a proxy (not daily returns). "
        "Timeout outcome uses the horizon close price vs entry price._",
    ]

    return "\n".join(lines)


def _by_regime_table(metrics: BacktestMetrics) -> Table:
    table = Table(
        title="[idx.section]By Market Regime[/idx.section]",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Regime")
    table.add_column("Trades", justify="right")
    table.add_column("W", justify="right", style="idx.ok")
    table.add_column("L", justify="right", style="idx.error")
    table.add_column("Win %", justify="right")
    table.add_column("Avg PnL", justify="right")

    for regime, stats in sorted(metrics.by_regime.items()):
        wr = stats.get("win_rate")
        pnl = stats.get("avg_pnl_pct")
        table.add_row(
            regime,
            str(stats["total"]),
            str(stats["wins"]),
            str(stats["losses"]),
            f"{wr * 100:.0f}%" if wr is not None else "N/A",
            f"{pnl:+.1f}%" if pnl is not None else "N/A",
        )
    return table


def _open_by_age_table(metrics: BacktestMetrics) -> Table:
    table = Table(
        title="[idx.section]Open Trades by Age[/idx.section]",
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Age Bucket")
    table.add_column("Count", justify="right")

    for bucket in ("<7d", "7-30d", ">30d"):
        count = metrics.open_by_age.get(bucket, 0)
        table.add_row(bucket, str(count))
    return table


def print_report(
    metrics: BacktestMetrics,
    *,
    output_format: str = "table",
    output_file: Path | None = None,
    console: Console | None = None,
) -> None:
    """Print report to console and optionally save to file."""
    con = console or default_console

    if output_format == "md":
        md = generate_markdown_report(metrics)
        if output_file:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(md, encoding="utf-8")
            con.print(f"[idx.ok]Report saved to[/idx.ok] [idx.path]{output_file}[/idx.path]")
        else:
            con.print(md)
        return

    con.print(generate_rich_table(metrics))
    if metrics.by_ticker:
        con.print(_per_ticker_table(metrics))
    if metrics.by_confidence_tier:
        con.print(_confidence_tier_table(metrics))
    if metrics.by_regime:
        con.print(_by_regime_table(metrics))
    if metrics.open_trades > 0:
        con.print(_open_by_age_table(metrics))

    if metrics.best_trade:
        b = metrics.best_trade
        con.print(
            f"\n[idx.ok]Best trade:[/idx.ok] [idx.ticker]{b.ticker}[/idx.ticker] "
            f"{b.entry_date} -> "
            f"[idx.ok]{f'{b.pnl_pct:+.1f}%' if b.pnl_pct is not None else 'N/A'}[/idx.ok] ({b.outcome})"
        )
    if metrics.worst_trade:
        w = metrics.worst_trade
        con.print(
            f"[idx.error]Worst trade:[/idx.error] [idx.ticker]{w.ticker}[/idx.ticker] "
            f"{w.entry_date} -> "
            f"[idx.error]{f'{w.pnl_pct:+.1f}%' if w.pnl_pct is not None else 'N/A'}[/idx.error] ({w.outcome})"
        )

    if output_file:
        md = generate_markdown_report(metrics)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(md, encoding="utf-8")
        con.print(f"\n[idx.ok]Report saved to[/idx.ok] [idx.path]{output_file}[/idx.path]")
