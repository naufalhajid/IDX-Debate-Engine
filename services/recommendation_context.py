"""Project gate outcomes into an informative, non-authoritative recommendation.

The execution decision remains the only source of trade authority.  This module
turns already-observed gate evidence into a versioned explanation contract; it
never changes thresholds, ratings, ranking, or sizing.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from schemas.debate import (
    GateMetric,
    HypotheticalSetup,
    RecommendationBlocker,
    RecommendationContext,
)


NEAR_MISS_MAX_SHORTFALL = 0.10

_DATA_CODES = {
    "candidate_intake_invalid",
    "empty_data",
    "insufficient_data",
    "insufficient_liquidity_data",
    "insufficient_ma200_history",
    "insufficient_short_history",
    "insufficient_technical_data",
    "invalid_ticker",
    "liquidity_data_unavailable",
    "llm_budget_capacity_exhausted",
    "missing_current_price",
    "missing_frozen_ticker_snapshot",
    "missing_stop_loss",
    "missing_target_price",
    "no_technical_data",
    "provider_error",
    "provider_history_error",
    "provider_history_unavailable",
    "recent_listing_short_history",
    "risk_decision_missing",
    "technical_indicator_calculation_failed",
}
_WAIT_CODES = {
    "price_above_entry_range",
    "price_below_entry_range",
    "wait_for_momentum_confirmation",
}
_SOFT_CODES = {
    *_WAIT_CODES,
    "exdate_cap65",
    "low_liquidity",
    "market_regime_defensive",
    "position_sizing_pending",
    "rr_too_low",
}
_HARD_CODES = {
    "ara_entry_risk_high",
    "exdate_imminent",
    "insufficient_liquidity",
    "invalid_entry_range",
    "invalid_stop_loss",
    "momentum_breakdown",
    "overvalued",
    "preflight_noise_reject",
    "rating_not_buyable",
    "rr_implausible",
    "stop_inside_noise",
    "target_collapsed",
    "upside_exhausted",
}
_NON_BLOCKING_CODES = {
    "counter_trend_setup",
    "fv_unmeasurable",
    "historically_expensive",
    "overvalued_momentum_exempt",
    "price_inside_entry_range",
    "rating_hold",
    "t2_hold_warning",
    "target_beyond_ara_reach",
}


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _metric(
    name: str,
    observed: Any,
    threshold: Any,
    comparator: str,
    unit: str | None = None,
    *,
    absolute_gap: Any = None,
    percentage_gap: Any = None,
) -> GateMetric:
    observed_number = _finite(observed)
    threshold_number = _finite(threshold)
    gap = _finite(absolute_gap)
    normalized = _finite(percentage_gap)
    if gap is None and observed_number is not None and threshold_number is not None:
        if comparator in {">=", ">"} and observed_number < threshold_number:
            gap = threshold_number - observed_number
        elif comparator in {"<=", "<"} and observed_number > threshold_number:
            gap = observed_number - threshold_number
    if (
        normalized is None
        and gap is not None
        and threshold_number is not None
        and abs(threshold_number) > 0
    ):
        normalized = gap / abs(threshold_number)
    return GateMetric(
        name=name,
        observed=observed,
        threshold=threshold,
        comparator=comparator,
        unit=unit,
        absolute_gap=gap,
        percentage_gap=normalized,
    )


def _blocker(
    gate_id: str,
    reason_code: str,
    hard_or_soft: str,
    provenance: str,
    *,
    observations: list[GateMetric] | None = None,
    detail: str | None = None,
    trigger: str | None = None,
) -> RecommendationBlocker:
    return RecommendationBlocker(
        gate_id=gate_id,
        reason_code=reason_code,
        hard_or_soft=hard_or_soft,
        observations=observations or [],
        provenance=provenance,
        detail=detail,
        next_observable_trigger=trigger,
    )


def _source_blockers(snapshot: Mapping[str, Any]) -> list[RecommendationBlocker]:
    blockers: list[RecommendationBlocker] = []
    for raw in _sequence(snapshot.get("gate_diagnostics")):
        if not isinstance(raw, Mapping):
            continue
        try:
            blockers.append(RecommendationBlocker.model_validate(dict(raw)))
        except Exception:
            # A malformed diagnostic must not make a report claim a value that
            # was never recorded. The status-specific fallback below remains.
            continue
    return blockers


def _upsert_blocker(
    blockers: list[RecommendationBlocker],
    blocker: RecommendationBlocker,
) -> None:
    for index, current in enumerate(blockers):
        if current.gate_id == blocker.gate_id:
            # Gate-owner diagnostics carry the authoritative observed values;
            # the projector may still enrich the missing next trigger.
            if (
                current.next_observable_trigger is None
                and blocker.next_observable_trigger is not None
            ):
                current.next_observable_trigger = blocker.next_observable_trigger
            blockers[index] = current
            return
    blockers.append(blocker)


def _hypothetical_setup(
    snapshot: Mapping[str, Any],
    *,
    status: str,
) -> HypotheticalSetup | None:
    if status == "EXECUTABLE":
        return None
    source_name = "trade_setup_snapshot.hypothetical_envelope"
    envelope = _mapping(snapshot.get("hypothetical_envelope"))
    if not envelope and status in {
        "WAIT_FOR_PULLBACK",
        "WAIT_FOR_CONFIRMATION",
        "SHADOW_ONLY",
    }:
        envelope = _mapping(snapshot.get("envelope"))
        source_name = "trade_setup_snapshot.envelope"
    if not envelope:
        return None
    return HypotheticalSetup(
        entry_low=_finite(envelope.get("entry_low")),
        entry_high=_finite(envelope.get("entry_high")),
        target_price=_finite(envelope.get("target_price")),
        target_basis=(
            str(envelope.get("target_basis"))
            if envelope.get("target_basis") is not None
            else None
        ),
        stop_loss=_finite(envelope.get("stop_loss")),
        risk_reward_ratio=_finite(envelope.get("risk_reward_ratio")),
        required_rr=_finite(envelope.get("required_rr")),
        provenance=source_name,
    )


def _rr_blocker(
    snapshot: Mapping[str, Any],
    signal_packet: Mapping[str, Any],
) -> RecommendationBlocker:
    envelope = _mapping(snapshot.get("hypothetical_envelope"))
    observed = _finite(envelope.get("risk_reward_ratio"))
    required = _finite(envelope.get("required_rr"))
    trigger = str(signal_packet.get("required_entry_trigger") or "").strip() or None
    if trigger is None:
        from services.signal_packet import build_rr_entry_trigger

        trigger = build_rr_entry_trigger(dict(snapshot))
    observations = []
    if observed is not None or required is not None:
        observations.append(
            _metric("risk_reward_ratio", observed, required, ">=", "x")
        )
    return _blocker(
        "risk_reward_floor",
        "rr_too_low",
        "SOFT",
        "services.debate_chamber.DebateChamber._compute_trade_envelope",
        observations=observations,
        detail=str(snapshot.get("reason") or "").strip() or None,
        trigger=trigger,
    )


def _target_blocker(snapshot: Mapping[str, Any]) -> RecommendationBlocker:
    envelope = _mapping(snapshot.get("hypothetical_envelope"))
    return _blocker(
        "target_geometry",
        "target_collapsed",
        "HARD",
        "services.debate_chamber.DebateChamber._compute_trade_envelope",
        observations=[
            _metric(
                "target_price",
                _finite(envelope.get("target_price")),
                _finite(envelope.get("entry_high")),
                ">",
                "IDR",
            )
        ],
        detail=str(snapshot.get("reason") or "").strip() or None,
    )


def _noise_blocker(snapshot: Mapping[str, Any]) -> RecommendationBlocker:
    preflight = _mapping(snapshot.get("preflight"))
    observed = _finite(preflight.get("surrogate_gap"))
    threshold = _finite(preflight.get("hard_floor"))
    provenance = "services.debate_chamber.DebateChamber._run_tradeability_preflight"
    metric_name = "price_to_swing_low_gap"
    if observed is None:
        envelope = _mapping(snapshot.get("hypothetical_envelope"))
        entry_high = _finite(envelope.get("entry_high"))
        stop = _finite(envelope.get("stop_loss"))
        atr = _finite(envelope.get("atr14"))
        multiplier = _finite(envelope.get("hard_noise_atr_multiplier"))
        observed = entry_high - stop if entry_high is not None and stop is not None else None
        threshold = (
            atr * multiplier
            if atr is not None and multiplier is not None
            else threshold
        )
        provenance = "services.debate_chamber.DebateChamber._compute_trade_envelope"
        metric_name = "stop_distance"
    observations = []
    if observed is not None or threshold is not None:
        observations.append(_metric(metric_name, observed, threshold, ">=", "IDR"))
    return _blocker(
        "stop_noise_floor",
        str(snapshot.get("reason_code") or "stop_inside_noise"),
        "HARD",
        provenance,
        observations=observations,
        detail=str(snapshot.get("reason") or "").strip() or None,
    )


def _momentum_blocker(snapshot: Mapping[str, Any]) -> RecommendationBlocker:
    tech = _mapping(snapshot.get("technical_indicators"))
    current = _finite(tech.get("current_price"))
    ema20 = _finite(tech.get("ema20"))
    return_5d = _finite(tech.get("return_5d_pct"))
    observations = [
        _metric("return_5d", return_5d, -3.0, ">", "%"),
        _metric("close_vs_ema20", current, ema20, ">=", "IDR"),
    ]
    return _blocker(
        "momentum_breakdown",
        "momentum_breakdown",
        "HARD",
        "services.debate_chamber.DebateChamber._compute_trade_envelope",
        observations=observations,
        detail=str(snapshot.get("reason") or "").strip() or None,
        trigger=(
            "Wait until five-day return is above -3% or close is at/above EMA20; "
            "then recompute every execution gate."
        ),
    )


def _confirmation_blocker(
    snapshot: Mapping[str, Any],
    signal_packet: Mapping[str, Any],
) -> RecommendationBlocker:
    tech = _mapping(snapshot.get("technical_indicators"))
    current = _finite(tech.get("current_price"))
    ema20 = _finite(tech.get("ema20"))
    return_1d = _finite(tech.get("return_1d_pct"))
    volume = _finite(tech.get("volume_surge_ratio"))
    observations: list[GateMetric] = []
    if current is None or ema20 is None or current < ema20:
        observations.append(_metric("close_vs_ema20", current, ema20, ">=", "IDR"))
    if return_1d is None or return_1d <= 0:
        observations.append(_metric("return_1d", return_1d, 0.0, ">", "%"))
    if volume is None or volume < 1.0:
        observations.append(_metric("volume_ratio", volume, 1.0, ">=", "x"))
    trigger = str(signal_packet.get("required_entry_trigger") or "").strip() or (
        "Wait until close >= EMA20, return_1d > 0, and volume_ratio >= 1.0; "
        "then recompute every execution gate."
    )
    return _blocker(
        "momentum_confirmation",
        str(snapshot.get("reason_code") or "wait_for_momentum_confirmation"),
        "SOFT",
        "services.debate_chamber.DebateChamber._compute_trade_envelope",
        observations=observations,
        detail=str(snapshot.get("reason") or "").strip() or None,
        trigger=trigger,
    )


def _price_position_blocker(
    snapshot: Mapping[str, Any],
    signal_packet: Mapping[str, Any],
) -> RecommendationBlocker:
    tech = _mapping(snapshot.get("technical_indicators"))
    envelope = _mapping(snapshot.get("envelope"))
    current = _finite(tech.get("current_price"))
    entry_high = _finite(envelope.get("entry_high"))
    trigger = str(signal_packet.get("required_entry_trigger") or "").strip() or None
    return _blocker(
        "entry_range_position",
        "price_above_entry_range",
        "SOFT",
        "services.trade_setup.build_trade_setup_snapshot",
        observations=[
            _metric("current_price", current, entry_high, "<=", "IDR")
        ],
        detail=str(snapshot.get("reason") or "").strip() or None,
        trigger=trigger,
    )


def _price_below_position_blocker(
    snapshot: Mapping[str, Any],
    signal_packet: Mapping[str, Any],
) -> RecommendationBlocker:
    tech = _mapping(snapshot.get("technical_indicators"))
    envelope = _mapping(snapshot.get("envelope"))
    current = _finite(tech.get("current_price"))
    entry_low = _finite(envelope.get("entry_low"))
    trigger = str(signal_packet.get("required_entry_trigger") or "").strip() or None
    return _blocker(
        "entry_range_position",
        "price_below_entry_range",
        "SOFT",
        "services.trade_setup.build_trade_setup_snapshot",
        observations=[
            _metric("current_price", current, entry_low, ">=", "IDR")
        ],
        detail=str(snapshot.get("reason") or "").strip() or None,
        trigger=trigger,
    )


def _history_blocker(snapshot: Mapping[str, Any]) -> RecommendationBlocker:
    reason_code = str(snapshot.get("reason_code") or "insufficient_data")
    history = _mapping(snapshot.get("history"))
    observed = _finite(history.get("complete_bars"))
    threshold = None
    if reason_code == "insufficient_short_history":
        threshold = _finite(snapshot.get("minimum_short_bars"))
    elif reason_code in {"insufficient_ma200_history", "recent_listing_short_history"}:
        threshold = _finite(snapshot.get("minimum_execution_bars"))
    observations = []
    if observed is not None or threshold is not None:
        observations.append(
            _metric("complete_ohlcv_bars", observed, threshold, ">=", "bars")
        )
    return _blocker(
        "ohlcv_history_completeness",
        reason_code,
        "DATA",
        "services.trade_setup.build_trade_setup_snapshot",
        observations=observations,
        detail=str(snapshot.get("reason") or "").strip() or None,
        trigger="Wait for sufficient complete point-in-time OHLCV bars, then recompute.",
    )


def _generic_setup_blocker(snapshot: Mapping[str, Any]) -> RecommendationBlocker:
    reason_code = str(snapshot.get("reason_code") or "trade_setup_rejected")
    blocker_class = (
        "DATA"
        if reason_code in _DATA_CODES
        else "SOFT"
        if reason_code in _SOFT_CODES
        else "HARD"
    )
    return _blocker(
        reason_code,
        reason_code,
        blocker_class,
        "services.trade_setup.build_trade_setup_snapshot",
        detail=str(snapshot.get("reason") or "").strip() or None,
    )


def _rr_is_near_miss(
    snapshot: Mapping[str, Any],
    blockers: Sequence[RecommendationBlocker],
) -> bool:
    failures = _sequence(snapshot.get("gate_failures"))
    if len(failures) != 1:
        # Legacy artifacts did not record every failure. Without proof that R/R
        # was the only failed gate, fail closed to SINGLE_GATE_REJECT.
        return False
    if len(blockers) != 1 or blockers[0].gate_id != "risk_reward_floor":
        return False
    metrics = blockers[0].observations
    if len(metrics) != 1 or metrics[0].percentage_gap is None:
        return False
    return metrics[0].percentage_gap <= NEAR_MISS_MAX_SHORTFALL


def _evidence_quality(
    state: str | None,
    blockers: Sequence[RecommendationBlocker],
) -> str:
    if state == "DATA_INSUFFICIENT":
        return "MISSING"
    if state == "QUALIFIED":
        return "COMPLETE"
    if not blockers:
        return "UNKNOWN"
    for blocker in blockers:
        if not blocker.observations:
            return "DEGRADED"
        if any(metric.observed is None or metric.threshold is None for metric in blocker.observations):
            return "DEGRADED"
    return "COMPLETE"


def build_setup_recommendation_context(
    snapshot: Mapping[str, Any],
    *,
    signal_packet: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a pre-risk explanation from an immutable trade-setup snapshot."""

    signal = _mapping(signal_packet or snapshot.get("signal_packet"))
    status = str(snapshot.get("status") or "INSUFFICIENT_DATA").upper()
    reason_code = str(snapshot.get("reason_code") or "").strip()
    blockers = _source_blockers(snapshot)

    if status == "WAIT_FOR_PULLBACK":
        _upsert_blocker(blockers, _price_position_blocker(snapshot, signal))
        state = "WAIT_TRIGGER"
    elif status == "WAIT_FOR_CONFIRMATION":
        _upsert_blocker(blockers, _confirmation_blocker(snapshot, signal))
        state = "WAIT_TRIGGER"
    elif status == "SHADOW_ONLY":
        _upsert_blocker(
            blockers,
            _blocker(
                "shadow_promotion_gate",
                reason_code or "shadow_only",
                "SOFT",
                "services.trade_setup.PHASE4_MOMENTUM_RECALIBRATION_SHADOW_ONLY",
                observations=[
                    _metric(
                        "promotion_status",
                        "SHADOW_ONLY",
                        "VALIDATED_AND_APPROVED",
                        "==",
                    )
                ],
                detail=str(snapshot.get("reason") or "").strip() or None,
            ),
        )
        state = "HARD_REJECT"
    elif status == "NO_MOMENTUM" and reason_code == "momentum_breakdown":
        _upsert_blocker(blockers, _momentum_blocker(snapshot))
        state = "HARD_REJECT"
    elif (
        status == "NO_MOMENTUM"
        and reason_code == "price_below_entry_range"
        and bool(_mapping(snapshot.get("envelope")))
        and bool(str(signal.get("required_entry_trigger") or "").strip())
    ):
        _upsert_blocker(blockers, _price_below_position_blocker(snapshot, signal))
        state = "WAIT_TRIGGER"
    elif status == "NO_MOMENTUM":
        _upsert_blocker(blockers, _generic_setup_blocker(snapshot))
        state = "SINGLE_GATE_REJECT"
    elif status == "RR_TOO_LOW" and reason_code == "rr_too_low":
        _upsert_blocker(blockers, _rr_blocker(snapshot, signal))
        state = (
            "NEAR_MISS"
            if _rr_is_near_miss(snapshot, blockers)
            else "SINGLE_GATE_REJECT"
        )
    elif status == "RR_TOO_LOW" and reason_code == "target_collapsed":
        _upsert_blocker(blockers, _target_blocker(snapshot))
        state = "HARD_REJECT"
    elif status == "STOP_INSIDE_NOISE":
        _upsert_blocker(blockers, _noise_blocker(snapshot))
        state = "HARD_REJECT"
    elif status == "INSUFFICIENT_DATA":
        _upsert_blocker(blockers, _history_blocker(snapshot))
        state = "DATA_INSUFFICIENT"
    elif status == "EXECUTABLE":
        state = None
    else:
        _upsert_blocker(blockers, _generic_setup_blocker(snapshot))
        state = "DATA_INSUFFICIENT" if reason_code in _DATA_CODES else "HARD_REJECT"

    actionability = (
        "PENDING"
        if status == "EXECUTABLE"
        else "ABSTAIN"
        if state == "DATA_INSUFFICIENT"
        else "REJECT"
    )
    next_trigger = next(
        (
            blocker.next_observable_trigger
            for blocker in blockers
            if blocker.next_observable_trigger
        ),
        None,
    )
    context = RecommendationContext(
        recommendation_state=state,
        full_pipeline_evaluated=False,
        classification_basis="trade_setup_snapshot",
        actionability=actionability,
        execution_eligible=None if status == "EXECUTABLE" else False,
        sizing_allowed=False,
        opportunity_rank_eligible=state in {"WAIT_TRIGGER", "NEAR_MISS"},
        decision_source="trade_setup",
        blockers=blockers,
        hypothetical_setup=_hypothetical_setup(snapshot, status=status),
        next_observable_trigger=next_trigger,
        evidence_quality=_evidence_quality(state, blockers),
    )
    return context.model_dump(mode="json")


def build_terminal_recommendation_context(
    *,
    reason_code: str,
    reason: str,
    signal_packet: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a context for a terminal policy result without a setup snapshot."""

    blocker_class = (
        "DATA"
        if reason_code in _DATA_CODES
        else "SOFT"
        if reason_code in _WAIT_CODES
        else "HARD"
    )
    state = "DATA_INSUFFICIENT" if blocker_class == "DATA" else "HARD_REJECT"
    signal = _mapping(signal_packet)
    trigger = str(signal.get("required_entry_trigger") or "").strip() or None
    blocker = _blocker(
        reason_code,
        reason_code,
        blocker_class,
        "core.orchestrator.legacy._pre_cio_terminal_result",
        detail=reason,
        trigger=trigger,
    )
    return RecommendationContext(
        recommendation_state=state,
        full_pipeline_evaluated=False,
        classification_basis="terminal_pre_cio_policy",
        actionability="ABSTAIN" if blocker_class == "DATA" else "REJECT",
        execution_eligible=False,
        sizing_allowed=False,
        opportunity_rank_eligible=False,
        decision_source="preflight",
        blockers=[blocker],
        next_observable_trigger=trigger,
        evidence_quality="MISSING" if blocker_class == "DATA" else "DEGRADED",
    ).model_dump(mode="json")


def _decision_rr_blocker(decision: Mapping[str, Any]) -> RecommendationBlocker:
    return _blocker(
        "risk_reward_floor",
        "rr_too_low",
        "SOFT",
        "app.api.result_adapter.build_execution_decision",
        observations=[
            _metric(
                "risk_reward_ratio",
                _finite(decision.get("risk_reward")),
                _finite(decision.get("required_rr")),
                ">=",
                "x",
            )
        ],
    )


def _decision_blocker(
    code: str,
    decision: Mapping[str, Any],
) -> RecommendationBlocker:
    if code == "rr_too_low":
        return _decision_rr_blocker(decision)
    if code == "low_confidence":
        return _blocker(
            "minimum_model_confidence",
            code,
            "HARD",
            "core.risk_governor.MIN_BUYABLE_CONFIDENCE",
            observations=[
                _metric(
                    "model_confidence",
                    _finite(decision.get("model_confidence")),
                    0.60,
                    ">=",
                    "probability-like score (uncalibrated)",
                )
            ],
        )
    if code == "liquidity_data_unavailable":
        return _blocker(
            "liquidity_capacity",
            code,
            "DATA",
            "core.risk_governor.assess_buyability",
            observations=[
                _metric("average_daily_turnover", None, "measurable", "is")
            ],
            trigger="Provide point-in-time average volume/turnover, then rerun sizing.",
        )
    blocker_class = (
        "DATA"
        if code in _DATA_CODES
        else "SOFT"
        if code in _SOFT_CODES
        else "HARD"
    )
    trigger = None
    if code == "price_above_entry_range":
        trigger = "Wait until price is inside the recorded entry range, then recompute."
    elif code == "market_regime_defensive":
        trigger = "Wait for the conservative regime resolver to permit deployment."
    return _blocker(
        code,
        code,
        blocker_class,
        "core.risk_governor.assess_buyability",
        trigger=trigger,
    )


def finalize_recommendation_context(
    context: Mapping[str, Any] | None,
    *,
    decision: Mapping[str, Any],
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the canonical post-risk decision without changing that decision."""

    if context:
        model = RecommendationContext.model_validate(dict(context))
    else:
        metadata = _mapping(_mapping(result).get("metadata"))
        snapshot = _mapping(metadata.get("trade_setup_snapshot"))
        signal = _mapping(metadata.get("signal_packet"))
        if snapshot:
            model = RecommendationContext.model_validate(
                build_setup_recommendation_context(snapshot, signal_packet=signal)
            )
        else:
            model = RecommendationContext()

    actionable = bool(decision.get("actionable"))
    execution_status = str(decision.get("execution_status") or "").upper()
    model.decision_source = str(decision.get("decision_source") or "risk_guard")
    result_metadata = _mapping(_mapping(result).get("metadata"))
    model.full_pipeline_evaluated = bool(
        int(result_metadata.get("llm_calls") or 0) > 0
        or model.decision_source == "cio"
    )
    model.classification_basis = "canonical_execution_decision"
    if actionable:
        model.recommendation_state = "QUALIFIED"
        model.actionability = "PASS"
        model.execution_eligible = True
        model.sizing_allowed = True
        model.opportunity_rank_eligible = True
        model.blockers = []
        model.hypothetical_setup = None
        model.next_observable_trigger = None
        model.evidence_quality = "COMPLETE"
        return RecommendationContext.model_validate(model).model_dump(mode="json")

    model.execution_eligible = False
    model.sizing_allowed = False
    reason_codes = [
        str(value).strip().lower()
        for value in _sequence(decision.get("reason_codes"))
        if str(value).strip()
    ]
    if model.recommendation_state is None:
        for code in reason_codes:
            if code in _NON_BLOCKING_CODES:
                continue
            _upsert_blocker(model.blockers, _decision_blocker(code, decision))

        if execution_status == "INSUFFICIENT_DATA" or any(
            code in _DATA_CODES for code in reason_codes
        ):
            model.recommendation_state = "DATA_INSUFFICIENT"
        elif any(code in _WAIT_CODES for code in reason_codes):
            model.recommendation_state = "WAIT_TRIGGER"
        elif (
            len(model.blockers) == 1
            and model.blockers[0].gate_id == "risk_reward_floor"
            and model.blockers[0].observations
            and model.blockers[0].observations[0].percentage_gap is not None
            and model.blockers[0].observations[0].percentage_gap
            <= NEAR_MISS_MAX_SHORTFALL
        ):
            # A post-risk artifact may not contain the preflight multi-failure
            # ledger. Only classify near-miss when this is the sole blocker in
            # the final canonical decision.
            model.recommendation_state = "NEAR_MISS"
        elif len(model.blockers) == 1 and model.blockers[0].hard_or_soft == "SOFT":
            model.recommendation_state = "SINGLE_GATE_REJECT"
        else:
            model.recommendation_state = "HARD_REJECT"

    if model.recommendation_state == "DATA_INSUFFICIENT":
        model.actionability = "ABSTAIN"
    else:
        model.actionability = "REJECT"
    model.opportunity_rank_eligible = model.recommendation_state in {
        "WAIT_TRIGGER",
        "NEAR_MISS",
    }
    if model.hypothetical_setup is None and result:
        verdict = _mapping(result.get("verdict"))
        if any(
            _finite(verdict.get(key)) is not None
            for key in ("target_price", "stop_loss", "risk_reward_ratio")
        ):
            entry_low = _finite(decision.get("entry_low"))
            entry_high = _finite(decision.get("entry_high"))
            model.hypothetical_setup = HypotheticalSetup(
                entry_low=entry_low,
                entry_high=entry_high,
                target_price=_finite(decision.get("target_price")),
                stop_loss=_finite(decision.get("stop_loss")),
                risk_reward_ratio=_finite(decision.get("risk_reward")),
                required_rr=_finite(decision.get("required_rr")),
                provenance="canonical_execution_decision.rejected_levels",
            )
    model.next_observable_trigger = next(
        (
            blocker.next_observable_trigger
            for blocker in model.blockers
            if blocker.next_observable_trigger
        ),
        model.next_observable_trigger,
    )
    model.evidence_quality = _evidence_quality(
        model.recommendation_state,
        model.blockers,
    )
    return RecommendationContext.model_validate(model).model_dump(mode="json")


def project_recommendation_context(
    result: Mapping[str, Any],
    *,
    decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure API/report projection for legacy and current result artifacts."""

    metadata = _mapping(result.get("metadata"))
    current = result.get("recommendation_context")
    if not isinstance(current, Mapping):
        current = metadata.get("recommendation_context")
    context = dict(current) if isinstance(current, Mapping) else None
    resolved_decision = decision
    if resolved_decision is None:
        raw = result.get("execution_decision")
        resolved_decision = dict(raw) if isinstance(raw, Mapping) else None
    if resolved_decision is None:
        resolved_decision = {
            "actionable": bool(result.get("actionable")),
            "execution_status": result.get("execution_status"),
            "decision_source": result.get("decision_source"),
            "reason_codes": result.get("reason_codes") or [],
            "model_confidence": result.get("model_confidence"),
        }
    return finalize_recommendation_context(
        context,
        decision=resolved_decision,
        result=result,
    )


__all__ = [
    "NEAR_MISS_MAX_SHORTFALL",
    "build_setup_recommendation_context",
    "build_terminal_recommendation_context",
    "finalize_recommendation_context",
    "project_recommendation_context",
]
