"""Aggregate backtest metrics from realized TradeOutcome records."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from core.backtest_memory import TradeOutcome


@dataclass
class TierMetrics:
    label: str
    total: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None
    avg_pnl_pct: float | None = None


@dataclass
class BacktestMetrics:
    total_trades: int
    wins: int
    losses: int
    open_trades: int
    timeout_flat: int
    win_rate: float | None
    avg_pnl_pct: float | None
    avg_holding_days: float | None
    sharpe_ratio: float | None
    best_trade: TradeOutcome | None
    worst_trade: TradeOutcome | None
    deflated_sr: float | None = None  # DSR per Bailey & Lopez de Prado (2014)
    by_ticker: dict[str, dict] = field(default_factory=dict)
    by_confidence_tier: list[TierMetrics] = field(default_factory=list)
    by_regime: dict[str, dict] = field(default_factory=dict)
    open_by_age: dict[str, int] = field(default_factory=dict)


def compute_metrics(
    records: list[TradeOutcome],
    *,
    ticker: str | None = None,
) -> BacktestMetrics:
    """Compute aggregate BacktestMetrics from a list of TradeOutcome records."""
    if ticker:
        records = [r for r in records if r.ticker.upper() == ticker.upper()]

    total = len(records)
    wins = sum(1 for r in records if r.outcome == "win")
    losses = sum(1 for r in records if r.outcome == "loss")
    open_trades = sum(1 for r in records if r.outcome == "open")
    timeout_flat = sum(1 for r in records if r.outcome == "timeout_flat")

    closed = [r for r in records if r.outcome in {"win", "loss", "breakeven"}]
    win_rate = wins / len(closed) if closed else None

    pnl_values = [r.pnl_pct for r in records if r.pnl_pct is not None]
    avg_pnl_pct = sum(pnl_values) / len(pnl_values) if pnl_values else None

    holding_days = [r.holding_period_days for r in records if r.holding_period_days is not None]
    avg_holding_days = sum(holding_days) / len(holding_days) if holding_days else None

    sharpe_ratio = _compute_sharpe(pnl_values, avg_holding_days)
    deflated_sr = _compute_deflated_sharpe(pnl_values, avg_holding_days)

    scored = [r for r in records if r.pnl_pct is not None]
    best_trade = max(scored, key=lambda r: r.pnl_pct) if scored else None  # type: ignore[arg-type]
    worst_trade = min(scored, key=lambda r: r.pnl_pct) if scored else None  # type: ignore[arg-type]

    tickers = {r.ticker for r in records}
    by_ticker = {}
    for t in sorted(tickers):
        t_records = [r for r in records if r.ticker == t]
        t_wins = sum(1 for r in t_records if r.outcome == "win")
        t_losses = sum(1 for r in t_records if r.outcome == "loss")
        t_closed = [r for r in t_records if r.outcome in {"win", "loss", "breakeven"}]
        t_pnl = [r.pnl_pct for r in t_records if r.pnl_pct is not None]
        by_ticker[t] = {
            "total": len(t_records),
            "wins": t_wins,
            "losses": t_losses,
            "win_rate": t_wins / len(t_closed) if t_closed else None,
            "avg_pnl_pct": sum(t_pnl) / len(t_pnl) if t_pnl else None,
        }

    by_confidence_tier = _compute_tiers(records)
    by_regime = _compute_by_regime(records)
    open_by_age = _compute_open_by_age(records)

    return BacktestMetrics(
        total_trades=total,
        wins=wins,
        losses=losses,
        open_trades=open_trades,
        timeout_flat=timeout_flat,
        win_rate=win_rate,
        avg_pnl_pct=avg_pnl_pct,
        avg_holding_days=avg_holding_days,
        sharpe_ratio=sharpe_ratio,
        deflated_sr=deflated_sr,
        best_trade=best_trade,
        worst_trade=worst_trade,
        by_ticker=by_ticker,
        by_confidence_tier=by_confidence_tier,
        by_regime=by_regime,
        open_by_age=open_by_age,
    )


_IDX_SWING_AVG_HOLD_DAYS = 10  # conservative fallback for IDX swing trades


def _compute_sharpe(
    pnl_values: list[float],
    avg_holding_days: float | None = None,
) -> float | None:
    """Annualized Sharpe ratio using per-trade pnl_pct.

    Annualizes by trades-per-year = 252 / avg_hold, where avg_hold is the
    actual average holding period in trading days. Falls back to
    _IDX_SWING_AVG_HOLD_DAYS when not provided.
    Returns None when n < 2 or std == 0.
    """
    if len(pnl_values) < 2:
        return None
    mean = sum(pnl_values) / len(pnl_values)
    variance = sum((x - mean) ** 2 for x in pnl_values) / (len(pnl_values) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    avg_hold = avg_holding_days if (avg_holding_days and avg_holding_days >= 1) else _IDX_SWING_AVG_HOLD_DAYS
    return (mean / std) * math.sqrt(252 / avg_hold)


def _compute_deflated_sharpe(
    pnl_values: list[float],
    avg_holding_days: float | None = None,
) -> float | None:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014) on per-trade returns.

    Uses n_trials=1 (single strategy, no parameter search), so deflated_sr equals
    the Probabilistic SR — the probability the true SR exceeds 0.5 (swing benchmark).
    Requires at least 4 trades.
    """
    if len(pnl_values) < 4:
        return None
    avg_hold = (
        avg_holding_days if (avg_holding_days and avg_holding_days >= 1) else _IDX_SWING_AVG_HOLD_DAYS
    )
    freq = int(round(252.0 / avg_hold))
    try:
        from src.evaluation.backtest_metrics import calculate_deflated_sharpe_ratio

        result = calculate_deflated_sharpe_ratio(
            np.array(pnl_values, dtype=float),
            benchmark_sr=0.5,
            n_trials=1,
            freq=freq,
        )
        return result["deflated_sr"]
    except Exception:
        return None


def _compute_tiers(records: list[TradeOutcome]) -> list[TierMetrics]:
    tier_map = {
        "high": TierMetrics(label="High (>=80%)"),
        "medium": TierMetrics(label="Medium (60-80%)"),
        "low": TierMetrics(label="Low (<60%)"),
    }

    for r in records:
        bucket = _confidence_tier_key(r.confidence_at_entry)
        tier = tier_map[bucket]
        tier.total += 1
        if r.outcome == "win":
            tier.wins += 1
        elif r.outcome == "loss":
            tier.losses += 1

    for bucket, tier in tier_map.items():
        closed = tier.wins + tier.losses
        pnl = [
            r.pnl_pct
            for r in records
            if r.pnl_pct is not None and _confidence_tier_key(r.confidence_at_entry) == bucket
        ]
        tier.win_rate = tier.wins / closed if closed else None
        tier.avg_pnl_pct = sum(pnl) / len(pnl) if pnl else None

    return list(tier_map.values())


def _confidence_tier_key(confidence: float | None) -> str:
    if confidence is None:
        return "low"
    if confidence >= 0.80:
        return "high"
    if confidence >= 0.60:
        return "medium"
    return "low"


def _parse_regime_from_notes(notes: str | None) -> str:
    if not notes:
        return "UNKNOWN"
    match = re.search(r"regime=(\w+)", notes)
    return match.group(1) if match else "UNKNOWN"


def _compute_by_regime(records: list[TradeOutcome]) -> dict[str, dict]:
    buckets: dict[str, list[TradeOutcome]] = {}
    for r in records:
        regime = _parse_regime_from_notes(r.notes)
        buckets.setdefault(regime, []).append(r)

    result: dict[str, dict] = {}
    for regime, recs in sorted(buckets.items()):
        r_wins = sum(1 for r in recs if r.outcome == "win")
        r_losses = sum(1 for r in recs if r.outcome == "loss")
        r_closed = [r for r in recs if r.outcome in {"win", "loss", "breakeven"}]
        r_pnl = [r.pnl_pct for r in recs if r.pnl_pct is not None]
        result[regime] = {
            "total": len(recs),
            "wins": r_wins,
            "losses": r_losses,
            "win_rate": r_wins / len(r_closed) if r_closed else None,
            "avg_pnl_pct": sum(r_pnl) / len(r_pnl) if r_pnl else None,
        }
    return result


def _compute_open_by_age(records: list[TradeOutcome]) -> dict[str, int]:
    today = date.today()
    buckets: dict[str, int] = {"<7d": 0, "7-30d": 0, ">30d": 0}
    for r in records:
        if r.outcome != "open":
            continue
        try:
            entry = date.fromisoformat(r.entry_date[:10])
        except Exception:
            continue
        age = (today - entry).days
        if age < 7:
            buckets["<7d"] += 1
        elif age <= 30:
            buckets["7-30d"] += 1
        else:
            buckets[">30d"] += 1
    return buckets
