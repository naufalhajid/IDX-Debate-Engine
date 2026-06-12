"""
core/portfolio_guard.py — Portfolio-level risk gates for the IDX swing-trade pipeline.

Two guards:
  1. Heat cap: sum of (entry-stop)/entry across all open trade records <= MAX_PORTFOLIO_HEAT
  2. Drawdown kill-switch: avg PnL% of closed trades in last 30 days > -MAX_30D_DRAWDOWN

Both guards read from the existing backtest_memory.jsonl — no new state files.

Limitation: stop_distance_pct is a proxy for per-trade risk; it ignores actual position
size (not stored in TradeOutcome). Accurate heat accounting requires adding position_size_pct
to TradeOutcome — deferred to P3.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_PORTFOLIO_HEAT = 0.06   # 6% total open risk (sum of stop-distance pcts)
MAX_30D_DRAWDOWN   = 0.15   # 15% avg realized loss in last 30 days triggers kill-switch


def compute_portfolio_heat(backtest_path: Path) -> float:
    """Sum of (entry_price - stop_loss) / entry_price across all 'open' trade records.

    Returns 0.0 if the file does not exist or no open records are present.
    """
    if not backtest_path.exists():
        return 0.0
    total = 0.0
    for line in backtest_path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("outcome") != "open":
            continue
        entry = float(r.get("entry_price") or 0)
        stop  = float(r.get("stop_loss") or 0)
        if entry > 0 and stop > 0:
            total += (entry - stop) / entry
    return total


def compute_30d_drawdown(backtest_path: Path) -> float:
    """Average pnl_pct of trades closed in the last 30 calendar days.

    Returns 0.0 if no qualifying records exist (treated as no drawdown).
    Negative value means net loss over the period.
    """
    if not backtest_path.exists():
        return 0.0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    pnls: list[float] = []
    for line in backtest_path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("outcome") in ("open", None):
            continue
        exit_date = r.get("exit_date") or ""
        if exit_date >= cutoff:
            pnl = r.get("pnl_pct")
            if pnl is not None:
                pnls.append(float(pnl))
    return sum(pnls) / len(pnls) if pnls else 0.0


def check_portfolio_allows_new_entry(
    backtest_path: Path,
    new_stop_dist_pct: float,
) -> tuple[bool, str]:
    """Return (allowed, reason_code) before recording a new BUY/STRONG_BUY trade.

    Args:
        backtest_path: Path to backtest_memory.jsonl
        new_stop_dist_pct: (entry - stop) / entry for the new trade being considered

    Returns:
        (True, "ok") if both guards pass
        (False, reason_code) if either guard fires
    """
    heat = compute_portfolio_heat(backtest_path)
    if heat + new_stop_dist_pct > MAX_PORTFOLIO_HEAT:
        return False, (
            f"portfolio_heat: {heat:.1%} open + {new_stop_dist_pct:.1%} new"
            f" > {MAX_PORTFOLIO_HEAT:.0%} cap"
        )
    drawdown = compute_30d_drawdown(backtest_path)
    if drawdown < -MAX_30D_DRAWDOWN:
        return False, (
            f"drawdown_kill_switch: 30d avg pnl {drawdown:.1%} < -{MAX_30D_DRAWDOWN:.0%}"
        )
    return True, "ok"
