"""Position sizing utilities for IHSG swing-trade candidates."""

from __future__ import annotations

from math import floor
from typing import Any

from utils.logger_config import logger


LOT_SIZE = 100
BUY_COMMISSION = 0.0015
SELL_COMMISSION = 0.0025
PPH_FINAL = 0.001

_RATING_BASE_ALLOCATION = {
    "STRONG_BUY": 0.30,
    "BUY": 0.20,
    "HOLD": 0.10,
}

_RATING_PRIORITY = {
    "STRONG_BUY": 0,
    "BUY": 1,
    "HOLD": 2,
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _position_sort_key(position: dict) -> tuple[int, float, float]:
    return (
        _RATING_PRIORITY.get(position["rating"], 99),
        -position["confidence"],
        -position["rr_ratio"],
    )


def _recompute_position(position: dict, total_capital: float) -> None:
    shares = position["lot"] * LOT_SIZE
    position_value = shares * position["entry_price"]
    buy_cost = position_value * BUY_COMMISSION
    sell_cost_est = position_value * (SELL_COMMISSION + PPH_FINAL)
    risk_per_share = position["entry_price"] - position["stop_loss"]

    position["shares"] = shares
    position["position_value"] = position_value
    position["allocation_pct"] = position_value / total_capital if total_capital > 0 else 0.0
    position["max_loss_rp"] = shares * risk_per_share
    position["total_cost_est"] = buy_cost + sell_cost_est


def calculate_positions(candidates: list[dict], user_config: dict) -> dict:
    """Calculate lot-sized IHSG positions from CIO verdict candidates."""
    assert user_config["total_capital"] > 0, "total_capital tidak valid"
    assert 0 < user_config["max_loss_pct"] <= 0.10, "max_loss_pct tidak valid"

    total_capital = _to_float(user_config.get("total_capital"))
    max_loss_pct = _to_float(user_config.get("max_loss_pct"))
    max_positions = _to_int(user_config.get("max_positions"))

    if total_capital <= 0:
        raise ValueError("total_capital must be greater than 0.")
    if max_loss_pct <= 0:
        raise ValueError("max_loss_pct must be greater than 0.")
    if max_positions <= 0:
        raise ValueError("max_positions must be greater than 0.")

    max_loss_budget = total_capital * max_loss_pct
    allocation_cap = 1 / max_positions
    positions: list[dict] = []

    for candidate in candidates:
        rating = str(candidate.get("rating", "")).upper()
        if rating == "AVOID":
            continue

        base_allocation = _RATING_BASE_ALLOCATION.get(rating)
        if base_allocation is None:
            continue

        ticker = str(candidate.get("ticker", "")).upper()
        confidence = max(0.0, min(_to_float(candidate.get("confidence")), 1.0))
        current_price = _to_float(candidate.get("current_price"))
        stop_loss = _to_float(candidate.get("stop_loss"))
        rr_ratio = _to_float(candidate.get("rr_ratio"))

        risk_per_share = current_price - stop_loss
        if not ticker or current_price <= 0 or risk_per_share <= 0:
            continue

        allocation_pct = min(base_allocation * confidence, allocation_cap)
        capital_allocated = total_capital * allocation_pct
        lot_from_risk = floor(max_loss_budget / (risk_per_share * LOT_SIZE))
        lot_from_alloc = floor(capital_allocated / (current_price * LOT_SIZE))
        final_lot = min(lot_from_risk, lot_from_alloc)
        if final_lot < 1:
            continue

        position = {
            "ticker": ticker,
            "rating": rating,
            "confidence": confidence,
            "lot": final_lot,
            "shares": 0,
            "position_value": 0.0,
            "allocation_pct": 0.0,
            "max_loss_rp": 0.0,
            "total_cost_est": 0.0,
            "entry_price": current_price,
            "stop_loss": stop_loss,
            "rr_ratio": rr_ratio,
        }
        _recompute_position(position, total_capital)
        positions.append(position)

    positions.sort(key=_position_sort_key)
    positions = positions[:max_positions]

    max_deployed = total_capital * 0.95
    while sum(p["position_value"] for p in positions) > max_deployed and positions:
        lowest_priority = max(range(len(positions)), key=lambda i: _position_sort_key(positions[i]))
        positions[lowest_priority]["lot"] -= 1
        if positions[lowest_priority]["lot"] < 1:
            positions.pop(lowest_priority)
        else:
            _recompute_position(positions[lowest_priority], total_capital)

    positions.sort(key=_position_sort_key)
    total_deployed = sum(p["position_value"] for p in positions)
    total_cost_est = sum(p["total_cost_est"] for p in positions)
    remaining_cash = total_capital - total_deployed
    summary = {
        "total_capital": total_capital,
        "total_deployed": total_deployed,
        "remaining_cash": remaining_cash,
        "deployed_pct": total_deployed / total_capital if total_capital > 0 else 0.0,
        "total_positions": len(positions),
        "total_cost_est": total_cost_est,
    }

    if summary["total_deployed"] > user_config["total_capital"]:
        logger.error(
            f"[Sizing] BUG: total_deployed {summary['total_deployed']:,.0f} "
            f"> total_capital {user_config['total_capital']:,.0f}. "
            f"Portfolio guard gagal."
        )
        raise ValueError("Position sizing menghasilkan deployed > capital. Cek lot calculation.")

    return {
        "positions": positions,
        "summary": summary,
    }


__all__ = ["calculate_positions"]
