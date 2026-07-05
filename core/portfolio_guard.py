"""
core/portfolio_guard.py — Portfolio-level risk gates for the IDX swing-trade pipeline.

Two guards:
  1. Heat cap: sum of position_size_pct * (entry-stop)/entry across all open trade
     records <= MAX_PORTFOLIO_HEAT — i.e. total % of capital at risk if every open
     stop were hit simultaneously.
  2. Drawdown kill-switch: avg PnL% of closed trades in last 30 days > -MAX_30D_DRAWDOWN

Both guards read from the existing backtest_memory.jsonl — no new state files.

position_size_pct (V4.3): records written before this change (or any record where
the caller cannot supply a real size) have position_size_pct=None, which is treated
as weight 1.0 — i.e. the old, size-agnostic behavior. This is intentionally the more
conservative fallback (unknown size assumed worst-case full allocation), matching the
fail-safe philosophy of the liquidity gate's LIQUIDITY_GATE_FAIL_CLOSED (V4.7).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 1.3% total open risk, i.e. 6% rescaled by an assumed realistic BUY/STRONG_BUY size
# mix (~22% avg allocation, see core.quant_filter.position_sizer.RATING_BASE_ALLOCATION).
# The pre-V4.3 cap (0.06) summed raw stop-distance pcts as if every open position were
# sized at 100% of capital; weighting by real position size shrinks the raw sum by
# roughly that same factor, so rescaling the cap preserves today's real-world blocking
# point (~3 concurrent typical BUY positions) instead of silently loosening the gate.
MAX_PORTFOLIO_HEAT = 0.013
MAX_30D_DRAWDOWN = 15.0  # 15% avg realized loss in last 30 days triggers kill-switch
# NOTE: pnl_pct is stored as percent (e.g. -5.0 for 5% loss)


def _load_records(backtest_path: Path) -> list[dict]:
    """Read and parse all JSONL records from the backtest file (single I/O pass)."""
    if not backtest_path.exists():
        return []
    records: list[dict] = []
    for line in backtest_path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _position_weight(record: dict) -> float:
    """Position-size weight for a heat contribution; unknown size => 1.0 (worst-case)."""
    size_pct = record.get("position_size_pct")
    return float(size_pct) if size_pct is not None else 1.0


def _heat_from_records(records: list[dict]) -> float:
    total = 0.0
    for r in records:
        if r.get("outcome") != "open":
            continue
        entry = float(r.get("entry_price") or 0)
        stop = float(r.get("stop_loss") or 0)
        if entry > 0 and stop > 0:
            stop_dist_pct = (entry - stop) / entry
            total += stop_dist_pct * _position_weight(r)
    return total


def _drawdown_from_records(records: list[dict]) -> float:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    pnls: list[float] = []
    for r in records:
        if r.get("outcome") in ("open", None):
            continue
        exit_date = r.get("exit_date") or ""
        if exit_date >= cutoff:
            pnl = r.get("pnl_pct")
            if pnl is not None:
                pnls.append(float(pnl))
    return sum(pnls) / len(pnls) if pnls else 0.0


def compute_portfolio_heat(backtest_path: Path) -> float:
    """Sum of position_size_pct * (entry_price - stop_loss) / entry_price across all
    'open' trade records (position_size_pct defaults to 1.0 when absent).

    Returns 0.0 if the file does not exist or no open records are present.
    """
    return _heat_from_records(_load_records(backtest_path))


def compute_30d_drawdown(backtest_path: Path) -> float:
    """Average pnl_pct of trades closed in the last 30 calendar days.

    pnl_pct is stored as percent (e.g. -5.0 for a 5% loss).
    Returns 0.0 if no qualifying records exist (treated as no drawdown).
    Negative value means net loss over the period.
    """
    return _drawdown_from_records(_load_records(backtest_path))


def check_portfolio_allows_new_entry(
    backtest_path: Path,
    new_stop_dist_pct: float,
    new_position_size_pct: float | None = None,
) -> tuple[bool, str]:
    """Return (allowed, reason_code) before recording a new BUY/STRONG_BUY trade.

    Reads backtest_memory.jsonl exactly once for both checks.

    Args:
        backtest_path: Path to backtest_memory.jsonl
        new_stop_dist_pct: (entry - stop) / entry for the new trade being considered
        new_position_size_pct: Fraction of capital the new trade would allocate
            (e.g. RATING_BASE_ALLOCATION lookup by rating). None => weight 1.0,
            the same worst-case-full-allocation fallback used for stored records
            without position_size_pct.

    Returns:
        (True, "ok") if both guards pass
        (False, reason_code) if either guard fires
    """
    records = _load_records(backtest_path)

    heat = _heat_from_records(records)
    new_weight = new_position_size_pct if new_position_size_pct is not None else 1.0
    new_contribution = new_stop_dist_pct * new_weight
    if heat + new_contribution > MAX_PORTFOLIO_HEAT:
        return False, (
            f"portfolio_heat: {heat:.2%} open + {new_contribution:.2%} new"
            f" > {MAX_PORTFOLIO_HEAT:.2%} cap"
        )

    drawdown = _drawdown_from_records(records)
    if drawdown < -MAX_30D_DRAWDOWN:
        return False, (
            f"drawdown_kill_switch: 30d avg pnl {drawdown:.1f}% < -{MAX_30D_DRAWDOWN:.0f}%"
        )

    return True, "ok"
