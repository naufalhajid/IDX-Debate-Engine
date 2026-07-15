"""Canonical execution-regime resolution for the IDX swing pipeline."""

from __future__ import annotations

from typing import Any

from core.idx_market_params import REGIME_RULES


EXECUTION_REGIMES = frozenset({"BULL", "SIDEWAYS", "DEFENSIVE", "UNKNOWN"})

_EXECUTION_POLICY_PROFILE = {
    "BULL": "BULL",
    "SIDEWAYS": "SIDEWAYS",
    "DEFENSIVE": "BEAR_STRESS",
    "UNKNOWN": "UNKNOWN",
}


def _label(value: Any, *keys: str) -> str:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate).strip().upper()
        return ""
    return str(value or "").strip().upper()


def _reasons(rule_snapshot: Any) -> list[str]:
    if not isinstance(rule_snapshot, dict):
        return []
    raw = rule_snapshot.get("reasons")
    if not isinstance(raw, list):
        return []
    return [str(reason) for reason in raw if str(reason).strip()]


def execution_params_for(execution_regime: str) -> dict[str, Any]:
    """Return the sole policy profile allowed to control execution consumers."""
    label = str(execution_regime or "UNKNOWN").strip().upper()
    if label not in EXECUTION_REGIMES:
        label = "UNKNOWN"
    profile = _EXECUTION_POLICY_PROFILE[label]
    return dict(REGIME_RULES[profile])


def operational_params_for(execution_regime: str) -> dict[str, Any]:
    """Map the canonical taxonomy to existing batch-level operational controls."""
    from core.regime import get_regime_params

    label = str(execution_regime or "UNKNOWN").strip().upper()
    legacy_profile = {
        "BULL": "LOW",
        "SIDEWAYS": "HIGH",
        "DEFENSIVE": "DEFENSIVE",
        "UNKNOWN": "DEFENSIVE",
    }.get(label, "DEFENSIVE")
    params = dict(get_regime_params(legacy_profile))
    if label == "UNKNOWN":
        params["top_n_selection"] = 0
    return params


def resolve_execution_regime(
    *,
    rule_snapshot: dict[str, Any] | str | None,
    hmm_state: dict[str, Any] | str | None,
) -> dict[str, Any]:
    """Resolve rule-based and HMM diagnostics into one conservative authority.

    The canonical user-facing taxonomy is BULL/SIDEWAYS/DEFENSIVE/UNKNOWN.
    HMM remains a trend diagnostic and never acts as a second execution regime.
    """
    rule_label = _label(rule_snapshot, "regime", "label")
    volatility = _label(rule_snapshot, "volatility_regime") or "UNKNOWN"
    hmm_label = _label(hmm_state, "label", "regime") or "UNKNOWN"
    hmm_confidence = None
    if isinstance(hmm_state, dict):
        raw_confidence = hmm_state.get("confidence")
        if isinstance(raw_confidence, (int, float)) and not isinstance(
            raw_confidence, bool
        ):
            hmm_confidence = float(raw_confidence)

    rule_present = bool(rule_label)
    hmm_present = bool(hmm_state) and hmm_label != "UNKNOWN"

    if not rule_present and not hmm_present:
        execution = "UNKNOWN"
        reason = "regime_sources_unavailable"
    elif rule_label == "DEFENSIVE":
        execution = "DEFENSIVE"
        reason = (
            "rule_based_defensive_override"
            if hmm_label not in {"BEAR_STRESS", "UNKNOWN"}
            else "rule_based_defensive"
        )
    elif hmm_label == "BEAR_STRESS":
        execution = "DEFENSIVE"
        reason = "hmm_bear_stress_override"
    elif hmm_label == "UNKNOWN" and rule_present:
        execution = "SIDEWAYS"
        reason = "hmm_unavailable_rule_based_caution"
    elif rule_label in {"HIGH", "RECOVERY"} or volatility == "HIGH":
        execution = "SIDEWAYS"
        reason = "rule_or_volatility_caution"
    elif hmm_label == "SIDEWAYS":
        execution = "SIDEWAYS"
        reason = "hmm_sideways"
    elif hmm_label == "BULL" and rule_label in {"NORMAL", "LOW"}:
        execution = "BULL"
        reason = "detectors_agree_bull"
    else:
        execution = "SIDEWAYS"
        reason = "single_source_caution"

    policy_profile = _EXECUTION_POLICY_PROFILE[execution]
    return {
        "rule_based_regime": rule_label or "UNKNOWN",
        "rule_based_reasons": _reasons(rule_snapshot),
        "trend_regime": {
            "label": hmm_label,
            "confidence": hmm_confidence,
            "source": "hmm",
        },
        "volatility_regime": volatility,
        "execution_regime": execution,
        "execution_regime_reason": reason,
        "execution_policy_profile": policy_profile,
        "execution_params": execution_params_for(execution),
        "operational_params": operational_params_for(execution),
    }


def execution_regime_from_payload(payload: Any) -> str:
    """Read the canonical regime from state/result with legacy fallback."""
    if not isinstance(payload, dict):
        return ""
    direct = str(payload.get("execution_regime") or "").strip().upper()
    if direct in EXECUTION_REGIMES:
        return direct
    context = payload.get("regime_context")
    if isinstance(context, dict):
        label = str(context.get("execution_regime") or "").strip().upper()
        if label in EXECUTION_REGIMES:
            return label
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        direct = str(metadata.get("execution_regime") or "").strip().upper()
        if direct in EXECUTION_REGIMES:
            return direct
    return ""
