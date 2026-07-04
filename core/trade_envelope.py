"""Deterministic trade-envelope computation for IDX swing setups."""

from __future__ import annotations

from typing import Any

from core.settings import settings
from utils.technicals import (
    REGIME_ATR_STOP_MULTIPLIER,
    REGIME_ATR_STOP_MULTIPLIER_DEFAULT,
    snap_to_tick,
)
from utils.trade_math import LARGE_CAP_RR_MINIMUM, calculate_rr


class TradeEnvelopeService:
    """Compute Python-owned entry, target, stop, and R/R geometry."""

    #: Max target return (from entry_high) for a 5-20 trading-day swing, applied
    #: with or without a fair-value anchor. Resistance-based targets can run to
    #: a recent pre-crash high and the FV anchor itself can sit far above spot;
    #: both inflate R/R past anything tradeable.
    MAX_TARGET_RETURN = 0.10

    #: Sector-aware swing caps. Mining and property are cyclical with wider
    #: legitimate swings; banks are low-volatility and rarely move >10% in a swing.
    SECTOR_MAX_TARGET: dict[str, float] = {
        "mining": 0.20,
        "consumer": 0.12,
        "property": 0.15,
        "bank": 0.10,
        "default": 0.10,
    }

    #: Raw IDX sector keys (from quant_filter config) to SECTOR_MAX_TARGET bucket.
    SECTOR_ALIAS: dict[str, str] = {
        "energy": "mining",
        "basic_materials": "mining",
        "consumer_staples": "consumer",
        "consumer_disc": "consumer",
        "finance_nonbank": "bank",
    }

    @staticmethod
    def tick_size_for_price(price: float) -> float:
        """Return the IHSG tick size for a price level."""
        try:
            price = float(price)
        except (TypeError, ValueError):
            return 1.0
        if price < 200:
            return 1.0
        if price < 500:
            return 2.0
        if price < 2000:
            return 5.0
        if price < 5000:
            return 10.0
        return 25.0

    @classmethod
    def next_tick_above(cls, price: float) -> float:
        """Smallest snapped IHSG price strictly above the provided level."""
        try:
            base = float(price)
        except (TypeError, ValueError):
            base = 0.0
        if base <= 0:
            return 1.0

        candidate = base
        for _ in range(10):
            candidate = snap_to_tick(candidate + cls.tick_size_for_price(candidate))
            if candidate > base:
                return candidate
        return base + max(cls.tick_size_for_price(base), 1.0)

    @classmethod
    def previous_tick_below(cls, price: float) -> float:
        """Largest snapped IHSG price strictly below the provided level."""
        try:
            base = float(price)
        except (TypeError, ValueError):
            return 0.0
        if base <= 0:
            return 0.0

        candidate = base
        for _ in range(10):
            candidate = snap_to_tick(candidate - cls.tick_size_for_price(candidate))
            if 0 < candidate < base:
                return candidate
            if candidate <= 0:
                break
        return max(base - max(cls.tick_size_for_price(base), 1.0), 0.0)

    @classmethod
    def max_target_return(cls, sector: str | None) -> float:
        if not sector:
            return cls.MAX_TARGET_RETURN
        resolved = cls.SECTOR_ALIAS.get(sector.lower(), sector.lower())
        return cls.SECTOR_MAX_TARGET.get(resolved, cls.MAX_TARGET_RETURN)

    @classmethod
    def compute(
        cls,
        current_price: float,
        fair_value: float,
        tech: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute entry/target/stop in Python. Prices are snapped to IHSG ticks."""
        sma20 = tech.get("sma20", current_price)
        ma50 = tech.get("ma50")
        sector = tech.get("sector")
        atr14 = tech.get("atr14", 0)
        rsi14 = tech.get("rsi14")
        return_5d = tech.get("return_5d_pct")

        # Gate failures do not return early: level computation continues so a
        # rejected setup still carries a full hypothetical envelope for the
        # counterfactual watchlist ledger. The first gate hit owns reason_code.
        rejections: list[tuple[str, str]] = []

        # Momentum confirmation (F12): in momentum mode (RSI > 40) the pullback must
        # have stabilised - require flat-to-positive 5-day return before entry.
        # Skipped when RSI <= 40 (mean-reversion setups) where the oversold level
        # itself is the entry signal and a negative recent return is expected.
        if rsi14 is not None and return_5d is not None:
            if rsi14 > 40.0 and return_5d < 0.0:
                rejections.append(
                    (
                        "no_momentum_confirmation",
                        (
                            f"no_momentum_confirmation: return_5d {return_5d:.1f}%"
                            f" < 0 at RSI {rsi14:.1f}"
                        ),
                    )
                )

        # Entry zone: near MA50 support (pullback entry for swing)
        if ma50 and ma50 > 0 and current_price > 0:
            entry_low = snap_to_tick(min(ma50, current_price * 0.97))
            entry_high = snap_to_tick(min(ma50 * 1.02, current_price))
        else:
            entry_low = snap_to_tick(current_price * 0.97)
            entry_high = snap_to_tick(current_price)

        # Ensure entry_low < entry_high
        if entry_low >= entry_high:
            entry_low = snap_to_tick(current_price * 0.96)
            entry_high = snap_to_tick(current_price)
        if entry_low >= entry_high:
            entry_high = entry_low + max(snap_to_tick(entry_low * 0.02), 10)
        if entry_low >= entry_high:
            entry_high = cls.next_tick_above(entry_low)

        entry_mid = (entry_low + entry_high) / 2

        # Stop loss with buffer and hard floor - ATR multiplier scaled by market regime.
        regime_key = str(tech.get("regime", "NORMAL")).upper()
        k_atr = REGIME_ATR_STOP_MULTIPLIER.get(
            regime_key,
            REGIME_ATR_STOP_MULTIPLIER_DEFAULT,
        )

        if atr14 > 0 and sma20 > 0:
            swing_low = min(
                tech.get("low_20d", current_price * 0.95),
                tech.get("low_50d", current_price * 0.95),
            )
            structural_stop = swing_low - (0.5 * atr14)
            atr_stop = current_price - (k_atr * atr14)
            stop = max(structural_stop, atr_stop)

            hard_floor = current_price * (1 - settings.TRADE_ENVELOPE_MAX_STOP_LOSS_PCT)
            stop = snap_to_tick(max(stop, hard_floor))
        else:
            stop = snap_to_tick(entry_mid * 0.96)

        # Guarantee stop < entry_low with a minimum 1-tick margin.
        if stop >= entry_low:
            stop = snap_to_tick(entry_low * 0.96)
        if stop >= entry_low:
            stop = cls.previous_tick_below(entry_low)

        # 3-tier noise gate:
        #   < HARD_MULTIPLIER * ATR   -> hard reject (caller returns HOLD 0.40)
        #   HARD-CLEAN                -> conditional (proceed, flag stop_near_noise)
        #   >= CLEAN_MULTIPLIER * ATR -> clean setup
        stop_near_noise = False
        if atr14 > 0:
            stop_distance = entry_high - stop
            hard_floor_atr = settings.TRADE_ENVELOPE_HARD_NOISE_ATR_MULTIPLIER * atr14
            clean_floor_atr = settings.TRADE_ENVELOPE_CLEAN_NOISE_ATR_MULTIPLIER * atr14
            if stop_distance < hard_floor_atr:
                rejections.append(
                    (
                        "stop_inside_noise",
                        (
                            f"stop_inside_noise: gap {stop_distance:.0f}"
                            f" < {settings.TRADE_ENVELOPE_HARD_NOISE_ATR_MULTIPLIER:.1f}xATR"
                            f" {hard_floor_atr:.0f}"
                        ),
                    )
                )
            stop_near_noise = stop_distance < clean_floor_atr

        # Floor: minimal 4% from entry for worthwhile swing.
        min_target = entry_mid * 1.04

        high_20d = tech.get("high_20d", 0)
        high_50d = tech.get("high_50d", 0)
        high_52w = tech.get("52w_high", 0)

        # Target: resistance-first. Collect candidates above entry_high, pick
        # the nearest, then let the swing cap and R/R gate below finalise.
        # If no resistance qualifies, fall back to the 2.0x R/R seed.
        risk_from_entry_high = entry_high - stop
        resistance_candidates: list[tuple[float, str]] = []
        if high_20d > current_price:
            resistance_candidates.append((high_20d, "Resistance 20-Day"))
        if high_50d > current_price:
            resistance_candidates.append((high_50d, "Resistance 50-Day"))
        if high_52w > current_price and high_52w <= current_price * 1.30:
            resistance_candidates.append((high_52w, "Resistance 52-Week"))

        if resistance_candidates:
            nearest_resistance, target_basis = min(
                resistance_candidates,
                key=lambda x: x[0],
            )
            target_candidate = max(
                snap_to_tick(nearest_resistance),
                snap_to_tick(min_target),
            )
        else:
            rr_target = entry_high + (risk_from_entry_high * 2.0)
            target_candidate = max(rr_target, min_target)
            target_basis = "Minimum R/R"

        target = snap_to_tick(target_candidate)

        # Ceiling: realistic swing cap. Resistance levels, especially a recent
        # pre-crash high, push the target far above price and inflate R/R.
        capped = snap_to_tick(entry_high * (1 + cls.max_target_return(sector)))
        if 0 < capped < target:
            target = capped
            target_basis += " (Swing Cap)"

        if target <= entry_high:
            rejections.append(
                (
                    "target_collapsed",
                    (
                        f"target_collapsed: target {target} ≤ entry_high {entry_high}"
                        f" after ceiling(s): {target_basis}"
                    ),
                )
            )

        # Compute display percentages from entry_mid, but canonical R/R from entry_high.
        gain_pct = ((target - entry_mid) / entry_mid) * 100 if entry_mid > 0 else 0
        loss_pct = (
            ((entry_mid - stop) / entry_mid) * 100
            if entry_mid > 0 and entry_mid > stop
            else 0
        )
        rr_ratio = calculate_rr(entry_high, target, stop)

        # Reject below the absolute R/R floor so bad setups don't propagate
        # silently. Non-large-cap stocks face a higher threshold (1.5x) in the
        # governor; this catches the worst cases early.
        if rr_ratio < LARGE_CAP_RR_MINIMUM:
            rejections.append(
                (
                    "rr_too_low",
                    (
                        f"rr_too_low: R/R {rr_ratio:.2f} < {LARGE_CAP_RR_MINIMUM}"
                        f" (target {target}, entry_high {entry_high}, stop {stop})"
                    ),
                )
            )

        if rejections:
            reason_code, reason = rejections[0]
            return {
                "rejected": True,
                "reason_code": reason_code,
                "reason": reason,
                # As-computed levels, NOT tradeable: a collapsed target or
                # sub-floor R/R is recorded exactly as the gates saw it so the
                # watchlist ledger can later score rejected setups.
                "hypothetical_envelope": {
                    "entry_low": entry_low,
                    "entry_high": entry_high,
                    "target_price": target,
                    "target_basis": target_basis,
                    "stop_loss": stop,
                    "risk_reward_ratio": rr_ratio,
                },
            }

        return {
            "entry_low": entry_low,
            "entry_high": entry_high,
            "entry_mid": round(entry_mid, 0),
            "target_price": target,
            "target_basis": target_basis,
            "stop_loss": stop,
            "expected_return_pct": round(gain_pct, 1),
            "max_risk_pct": round(loss_pct, 1),
            "risk_reward_ratio": rr_ratio,
            "fair_value": fair_value if (fair_value and fair_value > 0) else None,
            "atr14": atr14,
            "stop_near_noise": stop_near_noise,
        }


__all__ = ["TradeEnvelopeService"]
