"""
scripts/run_backtest.py — Walk-forward OHLCV replay with Deflated Sharpe reporting.

Extends historical_backtest.py with:
  - Walk-forward OOS windows (252d in-sample / 63d OOS)
  - Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014) per window and aggregate
  - Per-period Markdown report saved to docs/backtest_results/YYYY-MM-DD.md

Usage:
  uv run python scripts/run_backtest.py --tickers BBCA,BMRI,TLKM --years 3
  uv run python scripts/run_backtest.py --tickers BBRI --years 2 --insample 252 --oos 63

Minimum data required: insample_days + 4 × oos_days (e.g. 252 + 252 = 504 trading days ≈ 2yr).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.backtester.metrics_calculator import calculate_deflated_sharpe_ratio
from scripts.historical_backtest import replay_ticker

_BENCHMARK_SR = 0.5  # swing trading threshold


def _date_diff(d1: str, d2: str) -> int:
    try:
        return (datetime.strptime(d2, "%Y-%m-%d") - datetime.strptime(d1, "%Y-%m-%d")).days
    except Exception:
        return 0


def _walk_forward_splits(
    trades,
    *,
    insample_days: int,
    oos_days: int,
) -> list[dict]:
    """
    Split trade list into strict walk-forward (in-sample / OOS) windows.

    Uses trade-count buckets scaled by the observed trades-per-calendar-day ratio,
    which approximates the calendar-based splits accurately for IDX swing frequency.
    """
    closed = [t for t in trades if t.pnl_pct is not None and t.outcome != "open"]
    closed.sort(key=lambda t: t.entry_date)

    if not closed:
        return []

    total_calendar_days = max(1, _date_diff(closed[0].entry_date, closed[-1].entry_date))
    trades_per_day = len(closed) / total_calendar_days
    is_bucket = max(1, round(insample_days * trades_per_day))
    oos_bucket = max(1, round(oos_days * trades_per_day))

    windows = []
    cursor = 0
    while cursor + is_bucket + oos_bucket <= len(closed):
        oos_start = cursor + is_bucket
        oos_end = oos_start + oos_bucket
        oos_trades = closed[oos_start:oos_end]
        oos_returns = np.array([t.pnl_pct for t in oos_trades], dtype=float)

        hold_days = [t.holding_days for t in oos_trades if t.holding_days]
        avg_hold = float(np.mean(hold_days)) if hold_days else 10.0
        freq = max(1, round(252.0 / avg_hold))

        period: dict = {
            "insample_start": closed[cursor].entry_date,
            "insample_end": closed[oos_start - 1].entry_date,
            "oos_start": oos_trades[0].entry_date if oos_trades else None,
            "oos_end": oos_trades[-1].entry_date if oos_trades else None,
            "n_oos_trades": len(oos_trades),
            "oos_avg_pnl_pct": round(float(oos_returns.mean()), 4) if len(oos_returns) else None,
            "win_rate": round(float((oos_returns > 0).mean()), 4) if len(oos_returns) else None,
            "dsr_metrics": None,
        }

        if len(oos_returns) >= 4 and oos_returns.std(ddof=1) > 1e-10:
            period["dsr_metrics"] = calculate_deflated_sharpe_ratio(
                oos_returns,
                benchmark_sr=_BENCHMARK_SR,
                n_trials=1,
                freq=freq,
            )

        windows.append(period)
        cursor += oos_bucket  # strict: advance by OOS size, no overlap

    return windows


def _build_report(
    ticker_results: dict[str, dict],
    *,
    generated_at: str,
    insample_days: int,
    oos_days: int,
) -> str:
    lines = [
        "# IDX Walk-Forward Backtest — Deflated Sharpe Report",
        "",
        f"> Generated: {generated_at}",
        f"> Validation: {insample_days}d in-sample / {oos_days}d OOS (strict walk-forward)",
        "",
        "## Backtest Reporting Standard",
        "",
        "All backtest results are reported using Deflated Sharpe Ratio (DSR).",
        "Reference: Bailey & Lopez de Prado (2014) https://ssrn.com/abstract=2460551",
        "",
        "- **DSR > 0.95** = signal significant after multiple-testing correction — safe to paper-trade",
        "- **DSR < 0.95** = signal not proven, do NOT deploy to paper trading",
        "",
    ]

    for ticker, res in ticker_results.items():
        lines += [
            f"## {ticker}",
            "",
            f"- Total closed trades: {res['total_closed']}",
            f"- Aggregate win rate: {res['aggregate_win_rate']}",
            f"- Aggregate avg PnL: {res['aggregate_avg_pnl']}%",
            f"- Walk-forward OOS windows: {res['n_windows']}",
            "",
        ]

        agg = res.get("aggregate_dsr")
        if agg:
            sig = "**SIGNIFICANT** ✓" if agg["is_significant"] else "NOT SIGNIFICANT ✗"
            lines += [
                "### Aggregate DSR (all OOS periods, n_trials = n_windows)",
                "",
                "| Metric | Value |",
                "|---|---|",
                f"| Annualised Sharpe Ratio | {agg['sharpe_ratio']} |",
                f"| Deflated Sharpe Ratio (DSR) | **{agg['deflated_sr']}** |",
                f"| Min Track Record | {agg['min_track_record_days']} days |",
                f"| n_trials (windows) | {agg['n_trials']} |",
                f"| Verdict | {sig} |",
                "",
            ]
        else:
            lines += [
                "_Insufficient OOS windows for aggregate DSR (need ≥4 windows)._",
                "",
            ]

        if res["windows"]:
            lines += [
                "### Per-OOS-Window Results",
                "",
                "| OOS Period | Trades | Win% | Avg PnL% | SR | DSR | Significant |",
                "|---|---|---|---|---|---|---|",
            ]
            for w in res["windows"]:
                m = w.get("dsr_metrics")
                sr_str = f"{m['sharpe_ratio']:.2f}" if m else "—"
                dsr_str = f"{m['deflated_sr']:.3f}" if m else "—"
                sig_str = ("Yes" if m["is_significant"] else "No") if m else "—"
                wr = f"{w['win_rate']:.0%}" if w["win_rate"] is not None else "—"
                lines.append(
                    f"| {w['oos_start']} → {w['oos_end']} "
                    f"| {w['n_oos_trades']} "
                    f"| {wr} "
                    f"| {w['oos_avg_pnl_pct'] if w['oos_avg_pnl_pct'] is not None else '—'} "
                    f"| {sr_str} | {dsr_str} | {sig_str} |"
                )
            lines.append("")

        lines += ["---", ""]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward backtest with Deflated Sharpe Ratio")
    parser.add_argument("--tickers", required=True, help="Comma-separated IDX tickers, e.g. BBCA,BMRI")
    parser.add_argument("--years", type=int, default=3, help="Years of history (default: 3)")
    parser.add_argument("--insample", type=int, default=252, help="In-sample window in trading days (default: 252)")
    parser.add_argument("--oos", type=int, default=63, help="OOS window in trading days (default: 63)")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    generated_at = date.today().isoformat()
    min_days_needed = args.insample + 4 * args.oos

    print(f"Walk-forward backtest  {args.insample}d IS / {args.oos}d OOS")
    print(f"Min data needed: {min_days_needed} trading days ≈ {min_days_needed / 252:.1f} years")
    print("Reporting: Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014)")
    print()

    ticker_results: dict[str, dict] = {}

    for ticker in tickers:
        print(f"[{ticker}] Downloading {args.years}yr OHLCV...")
        trades = replay_ticker(ticker, args.years)
        closed = [t for t in trades if t.pnl_pct is not None and t.outcome != "open"]
        pnls = [t.pnl_pct for t in closed]

        print(f"  {len(closed)} closed trades over {args.years}yr")

        windows = _walk_forward_splits(trades, insample_days=args.insample, oos_days=args.oos)

        # Aggregate DSR: pool per-window avg returns, use n_windows as n_trials
        oos_window_avgs = np.array(
            [w["oos_avg_pnl_pct"] for w in windows if w["oos_avg_pnl_pct"] is not None],
            dtype=float,
        )

        aggregate_dsr = None
        if len(oos_window_avgs) >= 4 and oos_window_avgs.std(ddof=1) > 1e-10:
            aggregate_dsr = calculate_deflated_sharpe_ratio(
                oos_window_avgs,
                benchmark_sr=_BENCHMARK_SR,
                n_trials=max(1, len(windows)),
            )

        ticker_results[ticker] = {
            "total_closed": len(closed),
            "aggregate_win_rate": f"{sum(1 for t in closed if t.outcome == 'win') / max(len(closed), 1):.1%}",
            "aggregate_avg_pnl": round(sum(pnls) / max(len(pnls), 1), 4) if pnls else None,
            "n_windows": len(windows),
            "windows": windows,
            "aggregate_dsr": aggregate_dsr,
        }

        if aggregate_dsr:
            sig = "SIGNIFICANT" if aggregate_dsr["is_significant"] else "NOT SIGNIFICANT"
            print(
                f"  DSR={aggregate_dsr['deflated_sr']:.3f} | "
                f"SR={aggregate_dsr['sharpe_ratio']:.2f} | "
                f"n_windows={len(windows)} | {sig}"
            )
        else:
            print(f"  Insufficient OOS windows for DSR (have {len(windows)}, need ≥4)")

    out_dir = Path("docs/backtest_results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{generated_at}.md"
    out_path.write_text(
        _build_report(ticker_results, generated_at=generated_at, insample_days=args.insample, oos_days=args.oos),
        encoding="utf-8",
    )
    print(f"\nReport saved: {out_path}")


if __name__ == "__main__":
    main()
