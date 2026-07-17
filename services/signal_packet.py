"""Build and evolve the advisory signal packet without changing trade gates."""

from __future__ import annotations

import math
from typing import Any

from core.idx_market_params import idx_tick_size
from schemas.debate import SignalPacket


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _finite_number(value)
        if number is not None:
            return number
    return None


def _chart_strength(
    technicals: dict[str, Any],
    candidate: dict[str, Any],
) -> str:
    current_price = _first_number(
        technicals.get("current_price"),
        candidate.get("Current Price"),
    )
    ema20 = _first_number(technicals.get("ema20"), candidate.get("ema20"))
    ma50 = _first_number(
        technicals.get("ma50"),
        candidate.get("SMA 50"),
        candidate.get("sma50"),
    )
    rsi14 = _first_number(
        technicals.get("rsi14"),
        candidate.get("RSI (14)"),
    )
    return_5d = _finite_number(technicals.get("return_5d_pct"))
    volume_ratio = _first_number(
        technicals.get("volume_surge_ratio"),
        candidate.get("vol_surge_ratio"),
    )
    ma50_rising = technicals.get("ma50_rising") is True
    ma200_context = str(
        technicals.get("ma200_context") or candidate.get("ma200_context") or ""
    ).upper()

    if (
        current_price is not None
        and ema20 is not None
        and return_5d is not None
        and current_price < ema20
        and return_5d <= -3.0
    ):
        return "BEARISH"

    # Phase 4 shadow measurement has a deliberately separate MA50 extension.
    # Its exact evidence packet is: 2-8% above a rising MA50, RSI <= 70, and
    # volume ratio 1.2-2.5. It does not authorize entry and does not inherit the
    # standard path's RSI >= 45 or close >= EMA20 requirements.
    if current_price is not None and ma50 is not None and ma50 > 0:
        ma50_distance = current_price / ma50
        if ma50_distance > 1.02:
            if (
                ma50_distance <= 1.08
                and rsi14 is not None
                and rsi14 <= 70.0
                and volume_ratio is not None
                and 1.2 <= volume_ratio <= 2.5
                and ma50_rising
            ):
                return "BULLISH"
            return "NEUTRAL"

    rsi_supports_entry = rsi14 is not None and 45.0 <= rsi14 <= 70.0
    above_short_trend = (
        current_price is not None and ema20 is not None and current_price >= ema20
    )
    above_structural_trend = (
        current_price is not None and ma50 is not None and current_price >= ma50
    ) or ma200_context in {"ABOVE", "CROSSOVER_RECENT"}
    if above_short_trend and above_structural_trend and rsi_supports_entry:
        return "BULLISH"

    if any(
        value is not None for value in (current_price, ema20, ma50, rsi14, return_5d)
    ):
        return "NEUTRAL"
    return "UNKNOWN"


def _has_upstream_signal(candidate: dict[str, Any]) -> bool:
    """Return whether this record carries evidence from the quant candidate seam."""

    return any(
        key in candidate
        for key in (
            "Composite Score",
            "price_return_1m",
            "rs_vs_ihsg_1m",
            "Entry Strategy",
        )
    )


def _fundamental_quality(candidate: dict[str, Any]) -> str:
    piotroski = _first_number(
        candidate.get("Piotroski F-Score"),
        candidate.get("piotroski_f_score"),
    )
    if piotroski is None:
        return "UNKNOWN"
    if piotroski >= 7:
        return "STRONG"
    if piotroski >= 4:
        return "ADEQUATE"
    return "WEAK"


def _valuation_state(candidate: dict[str, Any]) -> str:
    margin_of_safety = _first_number(
        candidate.get("Multi-Method MoS (%)"),
        candidate.get("Valuation Gap (%)"),
    )
    if margin_of_safety is None:
        return "UNKNOWN"
    if margin_of_safety > 0:
        return "UNDERVALUED"
    if margin_of_safety < 0:
        return "OVERVALUED"
    return "FAIR"


def build_raw_signal_packet(
    *,
    technical_indicators: dict[str, Any] | None,
    candidate_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture signal evidence before preflight can terminate the pipeline."""

    technicals = dict(technical_indicators or {})
    candidate = dict(candidate_context or {})
    chart_strength = _chart_strength(technicals, candidate)
    if _has_upstream_signal(candidate) or chart_strength == "BULLISH":
        signal_lean = "BULLISH_SETUP"
    elif chart_strength == "BEARISH":
        signal_lean = "BEARISH"
    elif chart_strength == "NEUTRAL":
        signal_lean = "NEUTRAL"
    else:
        signal_lean = "UNKNOWN"

    relative_strength = _finite_number(candidate.get("rs_vs_ihsg_1m"))
    volume_ratio = _first_number(
        technicals.get("volume_surge_ratio"),
        candidate.get("vol_surge_ratio"),
    )
    packet = SignalPacket(
        signal_lean=signal_lean,
        chart_strength=chart_strength,
        relative_strength=relative_strength,
        volume_confirmation=(volume_ratio >= 1.0 if volume_ratio is not None else None),
        fundamental_quality=_fundamental_quality(candidate),
        valuation_state=_valuation_state(candidate),
        forecast_state="NOT_EVALUATED",
        execution_eligible=None,
        execution_rejection_reason=None,
        required_entry_trigger=None,
    )
    return packet.model_dump(mode="json")


def _rr_entry_trigger(snapshot: dict[str, Any]) -> str | None:
    """Return an advisory, tick-safe R/R threshold without authorizing entry."""

    if str(snapshot.get("reason_code") or "") != "rr_too_low":
        return None
    envelope = snapshot.get("hypothetical_envelope")
    envelope = envelope if isinstance(envelope, dict) else {}
    target = _finite_number(envelope.get("target_price"))
    stop = _finite_number(envelope.get("stop_loss"))
    required_rr = _finite_number(envelope.get("required_rr"))
    if (
        target is None
        or stop is None
        or required_rr is None
        or target <= stop
        or required_rr <= 0
    ):
        return None

    theoretical_max = (target + required_rr * stop) / (1.0 + required_rr)
    if not math.isfinite(theoretical_max) or theoretical_max <= 0:
        return None
    tick = float(idx_tick_size(theoretical_max))
    tick_safe_max = math.floor(theoretical_max / tick) * tick
    return (
        f"R/R-only entry trigger: wait for price at or below Rp {tick_safe_max:,.0f} "
        f"(theoretical maximum Rp {theoretical_max:,.2f}) to satisfy minimum "
        f"{required_rr:.2f}x R/R; all execution gates must be rechecked."
    )


def build_rr_entry_trigger(snapshot: dict[str, Any]) -> str | None:
    """Public, non-authoritative projector for the tick-safe R/R recheck price."""

    return _rr_entry_trigger(snapshot)


def apply_setup_outcome(
    packet: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Overlay only deterministic setup-gate fields on raw signal evidence."""

    model = SignalPacket.model_validate(packet)
    status = str(snapshot.get("status") or "").upper()
    reason_code = str(snapshot.get("reason_code") or "").strip() or None
    if status == "EXECUTABLE":
        model.execution_eligible = None
        model.execution_rejection_reason = None
        model.required_entry_trigger = None
        return model.model_dump(mode="json")

    model.execution_eligible = False
    model.execution_rejection_reason = reason_code or "trade_setup_rejected"
    if status == "WAIT_FOR_PULLBACK":
        envelope = snapshot.get("envelope")
        envelope = envelope if isinstance(envelope, dict) else {}
        entry_high = _finite_number(envelope.get("entry_high"))
        if entry_high is not None:
            model.required_entry_trigger = (
                f"Wait for price at or below Rp {entry_high:,.0f}."
            )
    elif status == "WAIT_FOR_CONFIRMATION":
        envelope = snapshot.get("envelope")
        envelope = envelope if isinstance(envelope, dict) else {}
        model.required_entry_trigger = str(
            envelope.get("confirmation_trigger")
            or (
                "Wait until close >= EMA20, return_1d > 0, and "
                "volume_ratio >= 1.0; then recompute all execution gates."
            )
        )
    elif status == "SHADOW_ONLY":
        model.required_entry_trigger = (
            "Shadow-only momentum calibration: live entry authorization remains "
            "disabled pending separate explicit approval."
        )
    elif status == "NO_MOMENTUM":
        if reason_code == "momentum_breakdown":
            model.required_entry_trigger = None
        elif reason_code == "price_below_entry_range":
            envelope = snapshot.get("envelope")
            envelope = envelope if isinstance(envelope, dict) else {}
            entry_low = _finite_number(envelope.get("entry_low"))
            model.required_entry_trigger = (
                f"Wait for price at or above Rp {entry_low:,.0f} and renewed "
                "momentum confirmation; then recompute all execution gates."
                if entry_low is not None
                else "Wait for price recovery and momentum confirmation."
            )
        else:
            model.required_entry_trigger = "Wait for momentum confirmation."
    elif status == "RR_TOO_LOW":
        model.required_entry_trigger = _rr_entry_trigger(snapshot)
    return model.model_dump(mode="json")


def refresh_snapshot_signal_packet(
    snapshot: dict[str, Any],
    *,
    candidate_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild a snapshot packet when richer frozen candidate context arrives."""

    raw = build_raw_signal_packet(
        technical_indicators=snapshot.get("technical_indicators"),
        candidate_context=candidate_context,
    )
    packet = apply_setup_outcome(raw, snapshot)
    snapshot["signal_packet"] = packet
    return packet


def set_forecast_state(
    packet: dict[str, Any],
    forecast_state: str,
) -> dict[str, Any]:
    model = SignalPacket.model_validate(packet)
    model.forecast_state = str(forecast_state or "NOT_EVALUATED")
    return model.model_dump(mode="json")


def finalize_execution_state(
    packet: dict[str, Any],
    *,
    actionable: bool,
    reason_codes: list[Any] | tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    """Apply the canonical post-risk execution decision to the packet."""

    model = SignalPacket.model_validate(packet)
    model.execution_eligible = bool(actionable)
    if actionable:
        model.execution_rejection_reason = None
    elif not model.execution_rejection_reason:
        codes = [str(code) for code in (reason_codes or []) if str(code)]
        model.execution_rejection_reason = codes[0] if codes else "not_actionable"
    return model.model_dump(mode="json")


__all__ = [
    "apply_setup_outcome",
    "build_raw_signal_packet",
    "build_rr_entry_trigger",
    "finalize_execution_state",
    "refresh_snapshot_signal_packet",
    "set_forecast_state",
]
