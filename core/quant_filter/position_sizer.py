"""Position sizing utilities for IHSG swing-trade candidates."""

from __future__ import annotations

from math import floor
from typing import Any

from core.execution_regime import execution_regime_from_payload
from core.idx_market_params import ARB_LOWER_LIMIT, LOT_SIZE
from utils.logger_config import logger
from utils.trade_math import compute_trailing_stop


BUY_COMMISSION = 0.0015
SELL_COMMISSION = 0.0025
PPH_FINAL = 0.001

_DEFAULT_KELLY_FRACTION = 0.5

#: V4.4 gap-risk stress: consecutive ARB (Auto Rejection Bawah / limit-down)
#: days assumed for the worst-case exit scenario, i.e. the position cannot be
#: sold at stop_loss and instead gaps through N consecutive -15% floors before
#: liquidity returns. Decided informational-only permanently (2026-07-06, see
#: docs/research/over_engineering_remediation_checklist.md item 1.4): displayed
#: via gap_stress_loss_pct/gap_stress_loss_rp (core/quant_filter/reporting.py)
#: as a risk-awareness figure, deliberately not wired into lot-size enforcement.
#: No realized ARB-gap incident has yet justified adding that enforcement layer.
GAP_RISK_STRESS_ARB_DAYS = 2


def compute_kelly_fraction(
    win_prob: float,
    rr: float,
    kelly_fraction: float = _DEFAULT_KELLY_FRACTION,
) -> float:
    """Half-Kelly position fraction for a swing trade setup.

    Kelly formula: f* = (p × b − q) / b
      p = win_prob (CIO confidence as proxy)
      b = rr (reward-to-risk ratio)
      q = 1 − p

    Returns portfolio fraction in [0, 1]. Returns 0.0 for negative-EV setups.

    Args:
        win_prob:      Win probability estimate (e.g., CIO confidence 0.60–0.85).
        rr:            Reward-to-risk ratio (e.g., 2.0).
        kelly_fraction: Scale applied to full Kelly (default 0.5 = half-Kelly).
    """
    if rr <= 0 or win_prob <= 0 or win_prob >= 1:
        return 0.0
    q = 1.0 - win_prob
    f_full = (win_prob * rr - q) / rr
    return max(0.0, round(f_full * kelly_fraction, 4))


#: Public: reused by core/portfolio_guard.py as a position-size proxy for
#: portfolio-heat weighting (V4.3) — the actual sized allocation differs after
#: Kelly/RR/regime adjustments below, but this base table is the best cheap
#: estimate available at TradeOutcome-write time, before position_sizer runs.
RATING_BASE_ALLOCATION = {
    "STRONG_BUY": 0.30,
    "BUY": 0.20,
    "HOLD": 0.10,
}

_RATING_WEIGHT = {
    "STRONG_BUY": 1.35,
    "BUY": 1.00,
    "HOLD": 0.45,
}

_RATING_PRIORITY = {
    "STRONG_BUY": 0,
    "BUY": 1,
    "HOLD": 2,
}


def _to_float(value: Any, default: float | None = 0.0) -> float | None:
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


def _normalise_pct(value: Any, default: float) -> float:
    pct = _to_float(value, default)
    if pct > 1.0:
        pct = pct / 100.0
    return max(0.0, min(pct, 1.0))


def _parse_expected_return_pct(candidate: dict, entry_price: float) -> float:
    # Prefer recomputing from target vs the SAME entry basis used to value the
    # position (entry_high worst-case fill when available), so that
    # expected_return_rp = position_value * pct shares one price basis. The
    # verdict's expected_return string uses the entry-midpoint basis and would
    # overstate the Rp return of a position valued at entry_high.
    target_price = _to_float(candidate.get("target_price"))
    if entry_price > 0 and target_price > entry_price:
        return ((target_price - entry_price) / entry_price) * 100

    value = candidate.get("expected_return")
    if isinstance(value, str):
        cleaned = value.strip().replace("%", "").replace("+", "")
        parsed = _to_float(cleaned, None)
        if parsed is not None:
            return parsed
    parsed = _to_float(value, None)
    if parsed is not None:
        return parsed * 100 if abs(parsed) <= 1.0 else parsed
    return 0.0


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
    position["allocation_pct"] = (
        position_value / total_capital if total_capital > 0 else 0.0
    )
    position["max_loss_rp"] = shares * risk_per_share
    position["max_drawdown_pct"] = (
        position["max_loss_rp"] / total_capital if total_capital > 0 else 0.0
    )
    position["expected_return_rp"] = position_value * (
        position.get("expected_return_pct", 0.0) / 100
    )
    position["total_cost_est"] = buy_cost + sell_cost_est

    # V4.4: gap-risk stress, informational only. Worst case of (a) the nominal
    # stop distance or (b) N consecutive ARB days from entry — never below (a),
    # since a stress scenario should never look better than the nominal case.
    gap_stress_floor_price = position["entry_price"] * (
        (1 - ARB_LOWER_LIMIT) ** GAP_RISK_STRESS_ARB_DAYS
    )
    gap_stress_risk_per_share = max(
        risk_per_share, position["entry_price"] - gap_stress_floor_price
    )
    position["gap_stress_loss_rp"] = shares * gap_stress_risk_per_share
    position["gap_stress_loss_pct"] = (
        position["gap_stress_loss_rp"] / total_capital if total_capital > 0 else 0.0
    )


def _can_add_lot(
    position: dict,
    *,
    total_capital: float,
    max_loss_budget: float,
    allocation_cap: float,
) -> bool:
    proposed_lot = position["lot"] + 1
    shares = proposed_lot * LOT_SIZE
    proposed_value = shares * position["entry_price"]
    proposed_risk = shares * (position["entry_price"] - position["stop_loss"])
    proposed_allocation = proposed_value / total_capital if total_capital > 0 else 0.0
    return proposed_allocation <= allocation_cap and proposed_risk <= max_loss_budget


def _weighted_average_expected_return(positions: list[dict]) -> float:
    total_value = sum(p.get("position_value", 0.0) for p in positions)
    if total_value <= 0:
        values = [
            p["expected_return_pct"]
            for p in positions
            if p.get("expected_return_pct") is not None
        ]
        return sum(values) / len(values) if values else 0.0
    return (
        sum(
            p.get("position_value", 0.0) * p.get("expected_return_pct", 0.0)
            for p in positions
        )
        / total_value
    )


def _market_condition_score(positions: list[dict], max_positions: int) -> float:
    if not positions:
        return 0.0
    avg_confidence = sum(p.get("confidence", 0.0) for p in positions) / len(positions)
    avg_rr_score = sum(
        min(max(p.get("rr_ratio", 0.0) / 2.5, 0.0), 1.0) for p in positions
    ) / len(positions)
    breadth_score = min(len(positions) / max(max_positions, 1), 1.0)
    score = (0.45 * avg_confidence) + (0.35 * avg_rr_score) + (0.20 * breadth_score)
    return round(max(0.0, min(score, 1.0)), 2)


def _deployment_scenario_comparison(
    *,
    total_capital: float,
    max_loss_pct: float,
    positions: list[dict],
    actual_deployment_pct: float,
) -> dict:
    avg_trade_return_pct = _weighted_average_expected_return(positions)
    deploy_pct = actual_deployment_pct
    expected_return_rp = total_capital * deploy_pct * (avg_trade_return_pct / 100)
    max_drawdown_rp = total_capital * deploy_pct * max_loss_pct
    deploy_pct_display = round(deploy_pct * 100, 1)
    return {
        "deploy_now": {
            "deployment_pct": deploy_pct_display,
            "expected_return_on_deployed_pct": round(avg_trade_return_pct, 2),
            "expected_return_portfolio_pct": round(
                deploy_pct * avg_trade_return_pct, 2
            ),
            "expected_return_rp": round(expected_return_rp, 0),
            "max_drawdown_portfolio_pct": round(deploy_pct * max_loss_pct * 100, 2),
            "max_drawdown_rp": round(max_drawdown_rp, 0),
        },
        "wait_for_confirmation": {
            "deployment_pct": 0.0,
            "expected_return_portfolio_pct": 0.0,
            "expected_return_rp": 0.0,
            "max_drawdown_portfolio_pct": 0.0,
            "max_drawdown_rp": 0.0,
            "tradeoff": (
                "Cash protects capital while waiting for cleaner entry, but gives up "
                f"roughly Rp {expected_return_rp:,.0f} if the current "
                f"{deploy_pct_display}% deployment scenario works."
            ),
        },
    }


def _allocation_reasoning(
    *,
    target_deployment_pct: float,
    actual_deployment_pct: float,
    positions: list[dict],
    eligible_count: int,
    max_positions: int,
    max_loss_pct: float,
) -> dict:
    target_pct_display = round(target_deployment_pct * 100, 1)
    actual_pct_display = round(actual_deployment_pct * 100, 1)
    gap_pct = max(target_deployment_pct - actual_deployment_pct, 0.0)
    market_score = _market_condition_score(positions, max_positions)
    max_feasible_pct = min(0.95, len(positions) * (1 / max(max_positions, 1)))

    risk_factors: list[str] = []
    if actual_deployment_pct < target_deployment_pct:
        risk_factors.append(
            "lot size 100 saham dan harga entry membatasi penambahan posisi tanpa oversizing"
        )
    if eligible_count < max_positions:
        risk_factors.append(
            f"hanya {eligible_count} kandidat yang lolos rating dan risk/reward untuk sizing"
        )
    if max_feasible_pct < target_deployment_pct:
        risk_factors.append(
            f"cap per posisi {100 / max(max_positions, 1):.1f}% membuat feasible deployment maksimum sekitar {max_feasible_pct * 100:.1f}%"
        )
    risk_factors.append(
        f"max drawdown per posisi dibatasi {max_loss_pct * 100:.1f}% sehingga lot tidak dipaksa melewati stop-risk budget"
    )
    if market_score < 0.60:
        risk_factors.append(
            f"market_condition_score {market_score:.2f} belum cukup ideal untuk full target deployment"
        )

    if actual_deployment_pct < 0.30:
        while len(risk_factors) < 3:
            risk_factors.append(
                "cash lebih baik daripada entry paksa karena sinyal kandidat belum cukup seragam"
            )

    if gap_pct > 0:
        gap_explanation = (
            f"Actual deployment {actual_pct_display:.1f}% masih {gap_pct * 100:.1f} percentage points "
            f"di bawah target {target_pct_display:.1f}% karena sizing menghormati lot IHSG, "
            "cap per posisi, dan budget risiko stop-loss."
        )
    else:
        gap_explanation = (
            f"Actual deployment {actual_pct_display:.1f}% sudah berada di area target normal "
            f"{target_pct_display:.1f}%."
        )

    if actual_deployment_pct < 0.30:
        recommendation = (
            "Pertahankan cash dominan sampai minimal tiga alasan pembatas di atas membaik; "
            "hindari mengejar 60% deployment dengan entry yang belum terkonfirmasi."
        )
    elif actual_deployment_pct < 0.40:
        recommendation = (
            "Boleh tambah posisi hanya pada pullback ke entry range atau jika ada kandidat baru "
            "dengan R/R lebih bersih."
        )
    else:
        recommendation = (
            "Deployment sudah cukup aktif untuk swing trade moderat; tambah exposure hanya "
            "jika sinyal teknikal menguat tanpa memperbesar drawdown per posisi."
        )

    return {
        "target_deployment_pct": target_pct_display,
        "actual_deployment_pct": actual_pct_display,
        "gap_explanation": gap_explanation,
        "risk_factors_limiting": risk_factors,
        "market_condition_score": market_score,
        "recommendation": recommendation,
    }


def calculate_positions(candidates: list[dict], user_config: dict) -> dict:
    """Calculate lot-sized IHSG positions from CIO verdict candidates."""
    total_capital = _to_float(user_config.get("total_capital"))
    max_loss_pct = _to_float(user_config.get("max_loss_pct"))
    max_positions = _to_int(user_config.get("max_positions"))

    if total_capital <= 0:
        raise ValueError("total_capital must be greater than 0.")
    if not (0 < max_loss_pct <= 0.10):
        raise ValueError(
            "max_loss_pct must be greater than 0 and less than or equal to 0.10."
        )
    if max_positions <= 0:
        raise ValueError("max_positions must be greater than 0.")

    regime_params = user_config.get("regime_params") or {}
    _regime_max_concurrent = _to_int(regime_params.get("max_concurrent_positions", 0))
    _regime_max_pct = _to_float(regime_params.get("max_position_pct"), None)
    _regime_label = str(regime_params.get("label", "")).upper() or "N/A"
    if _regime_max_concurrent > 0:
        max_positions = min(max_positions, _regime_max_concurrent)

    allocation_cap = 1 / max_positions
    target_deployment_pct = _normalise_pct(
        user_config.get("target_deployment_pct"),
        0.65,
    )
    target_deployment_pct = min(max(target_deployment_pct, 0.40), 0.70)

    # T+2: cash from recently sold positions is not yet settled.
    # Callers can pass unsettled_capital in user_config to prevent double-counting.
    unsettled_capital = _to_float(user_config.get("unsettled_capital", 0.0))
    effective_capital = max(total_capital - unsettled_capital, 0.0)

    max_loss_budget = total_capital * max_loss_pct
    max_deployed = effective_capital * 0.95
    desired_deployed = min(effective_capital * target_deployment_pct, max_deployed)
    eligible: list[dict] = []
    positions: list[dict] = []

    for candidate in candidates:
        rating = str(candidate.get("rating", "")).upper()
        if rating not in {"STRONG_BUY", "BUY"}:
            continue

        base_allocation = RATING_BASE_ALLOCATION.get(rating)
        if base_allocation is None:
            continue

        ticker = str(candidate.get("ticker", "")).upper()
        confidence = max(0.0, min(_to_float(candidate.get("confidence")), 1.0))
        current_price = _to_float(candidate.get("current_price"))
        stop_loss = _to_float(candidate.get("stop_loss"))
        rr_ratio = _to_float(candidate.get("rr_ratio"))
        entry_high = _to_float(candidate.get("entry_high"), None)

        # Worst-case fill basis: size risk from entry_high (top of the entry
        # zone) when available, matching the canonical R/R convention, so the
        # stop-risk budget still holds if the fill lands at the top of the zone.
        entry_price = (
            entry_high if entry_high is not None and entry_high > 0 else current_price
        )
        risk_per_share = entry_price - stop_loss
        if not ticker or current_price <= 0 or entry_price <= 0 or risk_per_share <= 0:
            continue

        expected_return_pct = _parse_expected_return_pct(candidate, entry_price)
        weight = (
            _RATING_WEIGHT.get(rating, 0.0)
            * max(confidence, 0.10)
            * max(min(rr_ratio, 3.0), 0.50)
        )
        kelly_f = compute_kelly_fraction(confidence, rr_ratio)
        atr14 = _to_float(candidate.get("atr14") or candidate.get("ATR (14)"), None)
        execution_regime = (
            execution_regime_from_payload(candidate)
            or (_regime_label if _regime_label != "N/A" else "UNKNOWN")
        )
        eligible.append(
            {
                "ticker": ticker,
                "rating": rating,
                "confidence": confidence,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "rr_ratio": rr_ratio,
                "risk_per_share": risk_per_share,
                "expected_return_pct": expected_return_pct,
                "weight": weight,
                "kelly_fraction": kelly_f,
                "atr14": atr14,
                "execution_regime": execution_regime,
            }
        )

    total_weight = sum(item["weight"] for item in eligible) or 1.0
    per_position_risk_budget = max_loss_budget / max_positions
    if _regime_max_pct is not None and _regime_max_pct > 0:
        per_position_risk_budget = min(
            per_position_risk_budget, total_capital * _regime_max_pct
        )

    for item in eligible:
        allocation_pct = min(
            target_deployment_pct * (item["weight"] / total_weight), allocation_cap
        )
        capital_allocated = total_capital * allocation_pct
        lot_from_risk = floor(
            per_position_risk_budget / (item["risk_per_share"] * LOT_SIZE)
        )
        lot_from_alloc = floor(capital_allocated / (item["entry_price"] * LOT_SIZE))
        final_lot = min(lot_from_risk, lot_from_alloc)
        if final_lot < 1:
            continue

        position = {
            "ticker": item["ticker"],
            "rating": item["rating"],
            "confidence": item["confidence"],
            "lot": final_lot,
            "shares": 0,
            "position_value": 0.0,
            "allocation_pct": 0.0,
            "target_allocation_pct": allocation_pct,
            "kelly_fraction": item["kelly_fraction"],
            "max_loss_rp": 0.0,
            "max_drawdown_pct": 0.0,
            "gap_stress_loss_rp": 0.0,
            "gap_stress_loss_pct": 0.0,
            "expected_return_pct": item["expected_return_pct"],
            "expected_return_rp": 0.0,
            "total_cost_est": 0.0,
            "entry_price": item["entry_price"],
            "stop_loss": item["stop_loss"],
            "rr_ratio": item["rr_ratio"],
            "execution_regime": item["execution_regime"],
        }
        _recompute_position(position, total_capital)

        # Task 8: Attach trailing stop when ATR is available
        if item["atr14"] and item["atr14"] > 0:
            trail = compute_trailing_stop(
                item["entry_price"], item["atr14"], item["execution_regime"]
            )
            position["trailing_stop_pct"] = trail["trailing_stop_pct"]
            position["trailing_stop_trigger_pct"] = trail["trailing_stop_trigger_pct"]

        positions.append(position)

    positions.sort(key=_position_sort_key)
    positions = positions[:max_positions]

    total_deployed = sum(p["position_value"] for p in positions)
    while total_deployed < desired_deployed:
        added = False
        for position in sorted(positions, key=_position_sort_key):
            if not _can_add_lot(
                position,
                total_capital=total_capital,
                max_loss_budget=per_position_risk_budget,
                allocation_cap=allocation_cap,
            ):
                continue
            added_value = position["entry_price"] * LOT_SIZE
            if total_deployed + added_value > max_deployed:
                continue
            position["lot"] += 1
            _recompute_position(position, total_capital)
            total_deployed += added_value
            added = True
            if total_deployed >= desired_deployed:
                break
        if not added:
            break

    while sum(p["position_value"] for p in positions) > max_deployed and positions:
        lowest_priority = max(
            range(len(positions)), key=lambda i: _position_sort_key(positions[i])
        )
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
        "target_deployment_pct": target_deployment_pct,
        "total_positions": len(positions),
        "total_cost_est": total_cost_est,
        "regime_label": _regime_label,
        "regime_max_position_pct": _regime_max_pct,
    }

    if summary["total_deployed"] > user_config["total_capital"]:
        logger.error(
            f"[Sizing] BUG: total_deployed {summary['total_deployed']:,.0f} "
            f"> total_capital {user_config['total_capital']:,.0f}. "
            f"Portfolio guard gagal."
        )
        raise ValueError(
            "Position sizing menghasilkan deployed > capital. Cek lot calculation."
        )

    return {
        "positions": positions,
        "summary": summary,
        "allocation_reasoning": _allocation_reasoning(
            target_deployment_pct=target_deployment_pct,
            actual_deployment_pct=summary["deployed_pct"],
            positions=positions,
            eligible_count=len(eligible),
            max_positions=max_positions,
            max_loss_pct=max_loss_pct,
        ),
        "deployment_scenario_comparison": _deployment_scenario_comparison(
            total_capital=total_capital,
            max_loss_pct=max_loss_pct,
            positions=positions,
            actual_deployment_pct=summary["deployed_pct"],
        ),
    }


__all__ = [
    "calculate_positions",
    "compute_kelly_fraction",
    "RATING_BASE_ALLOCATION",
    "GAP_RISK_STRESS_ARB_DAYS",
]
