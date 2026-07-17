"""Recommendation explanation must stay informative and non-authoritative."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from app.api.result_adapter import build_execution_decision, normalize_result
from core.orchestrator.legacy import (
    _finalize_execution_decisions,
    _pre_cio_terminal_result,
)
from core.artifact_validator import _validate_recommendation_contexts
from schemas.debate import RecommendationContext
from services.recommendation_context import (
    build_setup_recommendation_context,
    finalize_recommendation_context,
    project_recommendation_context,
)
from services.report_formatter import MarkdownFormatter


def _signal(trigger: str | None = None) -> dict[str, Any]:
    return {"required_entry_trigger": trigger}


def _rr_snapshot(
    observed: float,
    required: float = 2.0,
    *,
    failure_count: int | None = 1,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "status": "RR_TOO_LOW",
        "reason_code": "rr_too_low",
        "reason": f"R/R {observed:.2f} below {required:.2f}",
        "technical_data_status": "COMPLETE",
        "preflight": {"status": "clean"},
        "technical_indicators": {"current_price": 1_770.0},
        "hypothetical_envelope": {
            "entry_low": 1_715.0,
            "entry_high": 1_770.0,
            "target_price": 1_980.0,
            "stop_loss": 1_630.0,
            "risk_reward_ratio": observed,
            "required_rr": required,
        },
    }
    if failure_count is not None:
        snapshot["gate_failures"] = [
            {"reason_code": "rr_too_low", "detail": "fixture"}
            for _ in range(failure_count)
        ]
    return snapshot


@pytest.mark.parametrize(
    ("observed", "expected_state", "expected_gap"),
    [
        (1.90, "NEAR_MISS", 0.05),
        (1.80, "NEAR_MISS", 0.10),
        (1.50, "SINGLE_GATE_REJECT", 0.25),
    ],
)
def test_rr_near_miss_band_is_presentation_only(
    observed: float,
    expected_state: str,
    expected_gap: float,
) -> None:
    context = build_setup_recommendation_context(
        _rr_snapshot(observed),
        signal_packet=_signal("Wait for a lower entry and recompute all gates."),
    )

    assert context["recommendation_state"] == expected_state
    assert context["actionability"] == "REJECT"
    assert context["execution_eligible"] is False
    assert context["sizing_allowed"] is False
    assert context["display_only"] is True
    assert context["hypothetical_setup"]["explicitly_non_executable"] is True
    metric = context["blockers"][0]["observations"][0]
    assert metric["absolute_gap"] == pytest.approx(2.0 - observed)
    assert metric["percentage_gap"] == pytest.approx(expected_gap)


def test_rr_near_miss_requires_proof_of_exactly_one_failure() -> None:
    multi = build_setup_recommendation_context(
        _rr_snapshot(1.90, failure_count=2),
        signal_packet=_signal(),
    )
    legacy = build_setup_recommendation_context(
        _rr_snapshot(1.90, failure_count=None),
        signal_packet=_signal(),
    )

    assert multi["recommendation_state"] == "SINGLE_GATE_REJECT"
    assert legacy["recommendation_state"] == "SINGLE_GATE_REJECT"


def test_wait_for_pullback_has_exact_non_executable_trigger() -> None:
    snapshot = {
        "status": "WAIT_FOR_PULLBACK",
        "reason_code": "price_above_entry_range",
        "reason": "Current price is above entry.",
        "technical_indicators": {"current_price": 1_100.0},
        "envelope": {
            "entry_low": 1_000.0,
            "entry_high": 1_050.0,
            "target_price": 1_200.0,
            "stop_loss": 950.0,
            "risk_reward_ratio": 1.5,
            "required_rr": 2.0,
        },
    }

    context = build_setup_recommendation_context(
        snapshot,
        signal_packet=_signal("Wait for price at or below Rp 1,050."),
    )

    assert context["recommendation_state"] == "WAIT_TRIGGER"
    assert context["opportunity_rank_eligible"] is True
    metric = context["blockers"][0]["observations"][0]
    assert metric["observed"] == 1_100.0
    assert metric["threshold"] == 1_050.0
    assert metric["absolute_gap"] == 50.0
    assert context["next_observable_trigger"].endswith("Rp 1,050.")


def test_confirmation_wait_lists_each_unmet_component() -> None:
    snapshot = {
        "status": "WAIT_FOR_CONFIRMATION",
        "reason_code": "wait_for_momentum_confirmation",
        "technical_indicators": {
            "current_price": 990.0,
            "ema20": 1_000.0,
            "return_1d_pct": -0.5,
            "volume_surge_ratio": 0.8,
        },
        "envelope": {
            "entry_low": 980.0,
            "entry_high": 1_000.0,
            "target_price": 1_100.0,
            "stop_loss": 940.0,
        },
    }

    context = build_setup_recommendation_context(snapshot, signal_packet=_signal())

    assert context["recommendation_state"] == "WAIT_TRIGGER"
    names = {
        metric["name"]
        for metric in context["blockers"][0]["observations"]
    }
    assert names == {"close_vs_ema20", "return_1d", "volume_ratio"}


@pytest.mark.parametrize(
    ("status", "reason_code", "expected_state"),
    [
        ("SHADOW_ONLY", "shadow_only_momentum_recalibration", "HARD_REJECT"),
        ("NO_MOMENTUM", "momentum_breakdown", "HARD_REJECT"),
        ("UNKNOWN_REJECT", "unexpected_gate", "HARD_REJECT"),
    ],
)
def test_non_promotable_setup_states_fail_closed(
    status: str,
    reason_code: str,
    expected_state: str,
) -> None:
    snapshot = {
        "status": status,
        "reason_code": reason_code,
        "technical_indicators": {
            "current_price": 950.0,
            "ema20": 1_000.0,
            "return_5d_pct": -4.0,
        },
        "envelope": {},
    }

    context = build_setup_recommendation_context(snapshot, signal_packet=_signal())

    assert context["recommendation_state"] == expected_state
    assert context["opportunity_rank_eligible"] is False
    assert context["sizing_allowed"] is False


def test_insufficient_history_is_abstention_with_observed_bar_count() -> None:
    snapshot = {
        "status": "INSUFFICIENT_DATA",
        "reason_code": "insufficient_ma200_history",
        "reason": "Only 120 complete bars.",
        "history": {"complete_bars": 120},
        "minimum_execution_bars": 250,
    }

    context = build_setup_recommendation_context(snapshot, signal_packet=_signal())

    assert context["recommendation_state"] == "DATA_INSUFFICIENT"
    assert context["actionability"] == "ABSTAIN"
    metric = context["blockers"][0]["observations"][0]
    assert metric["observed"] == 120.0
    assert metric["threshold"] == 250.0
    assert metric["absolute_gap"] == 130.0


def test_bare_waitlist_is_not_promoted_to_wait_trigger() -> None:
    setup_context = build_setup_recommendation_context(
        {"status": "EXECUTABLE"},
        signal_packet=_signal(),
    )

    finalized = finalize_recommendation_context(
        setup_context,
        decision={
            "actionable": False,
            "execution_status": "WAITLIST",
            "decision_source": "risk_guard",
            "reason_codes": [],
        },
    )

    assert finalized["recommendation_state"] == "HARD_REJECT"
    assert finalized["opportunity_rank_eligible"] is False


def test_defensive_hold_is_single_gate_reject_not_trigger_watchlist() -> None:
    setup_context = build_setup_recommendation_context(
        {"status": "EXECUTABLE"},
        signal_packet=_signal(),
    )

    finalized = finalize_recommendation_context(
        setup_context,
        decision={
            "actionable": False,
            "execution_status": "NO_TRADE",
            "decision_source": "risk_guard",
            "reason_codes": ["market_regime_defensive"],
        },
    )

    assert finalized["recommendation_state"] == "SINGLE_GATE_REJECT"
    assert finalized["opportunity_rank_eligible"] is False


def test_actionable_finalization_is_only_path_to_qualified() -> None:
    setup_context = build_setup_recommendation_context(
        {"status": "EXECUTABLE"},
        signal_packet=_signal(),
    )

    finalized = finalize_recommendation_context(
        setup_context,
        decision={
            "actionable": True,
            "execution_status": "EXECUTABLE_BUY",
            "decision_source": "risk_guard",
            "reason_codes": [],
        },
    )

    assert finalized["recommendation_state"] == "QUALIFIED"
    assert finalized["actionability"] == "PASS"
    assert finalized["execution_eligible"] is True
    assert finalized["sizing_allowed"] is True
    assert finalized["blockers"] == []
    assert finalized["hypothetical_setup"] is None


def test_schema_rejects_actionable_hypothetical_setup() -> None:
    with pytest.raises(ValidationError, match="blocker-free QUALIFIED"):
        RecommendationContext.model_validate(
            {
                "recommendation_state": "QUALIFIED",
                "actionability": "PASS",
                "execution_eligible": True,
                "sizing_allowed": True,
                "hypothetical_setup": {
                    "entry_low": 100.0,
                    "explicitly_non_executable": True,
                    "provenance": "fixture",
                },
            }
        )


def test_artifact_validator_detects_context_drift_and_decision_mismatch() -> None:
    context = finalize_recommendation_context(
        build_setup_recommendation_context(
            {"status": "EXECUTABLE"},
            signal_packet=_signal(),
        ),
        decision={
            "actionable": True,
            "execution_status": "EXECUTABLE_BUY",
            "decision_source": "risk_guard",
            "reason_codes": [],
        },
    )
    entry = {
        "recommendation_context": context,
        "metadata": {"recommendation_context": deepcopy(context)},
        "recommendation_state": "QUALIFIED",
        "execution_decision": {
            "actionable": True,
            "execution_status": "EXECUTABLE_BUY",
            "decision_source": "risk_guard",
        },
    }
    errors: list[str] = []

    _validate_recommendation_contexts({"BBCA": entry}, errors)
    assert errors == []

    entry["metadata"]["recommendation_context"]["evidence_quality"] = "DEGRADED"
    _validate_recommendation_contexts({"BBCA": entry}, errors)
    assert any("recommendation_context_drift" in error for error in errors)


def test_projection_does_not_mutate_canonical_decision() -> None:
    result = {
        "ticker": "BBCA",
        "status": "success",
        "verdict": {
            "ticker": "BBCA",
            "rating": "HOLD",
            "confidence": 0.7,
            "entry_price_range": "100 - 105",
            "target_price": 120.0,
            "stop_loss": 95.0,
            "risk_reward_ratio": 1.0,
            "execution_horizon_days": 10,
        },
        "risk_governor": {
            "status": "reject",
            "sizing_allowed": False,
            "reason_codes": ["rr_too_low"],
        },
    }
    decision = build_execution_decision(result)
    frozen = deepcopy(decision)

    project_recommendation_context(result, decision=decision)

    assert decision == frozen


def test_pre_cio_result_has_api_report_and_persistence_parity() -> None:
    result = _pre_cio_terminal_result(
        {"Ticker": "MYOR", "Current Price": 1_770.0},
        reason_code="exdate_imminent",
        reason="Ex-date within seven days.",
    )
    _finalize_execution_decisions([result])

    normalized = normalize_result(result)
    report = MarkdownFormatter().generate_ticker_report(result)

    assert normalized["recommendation_state"] == "HARD_REJECT"
    assert normalized["recommendation_context"] == result["recommendation_context"]
    assert "Model Opinion** | NOT_EVALUATED" in report
    assert "Deterministic Preflight (zero LLM calls)" in report
    assert "No LLM debate or CIO model opinion was produced" in report
    assert "**No executable trade plan.**" in report


def test_rr_report_shows_exact_gap_and_non_executable_geometry() -> None:
    setup = _rr_snapshot(1.50)
    context = build_setup_recommendation_context(
        setup,
        signal_packet=_signal(
            "R/R-only entry trigger: wait for price at or below Rp 1,745; "
            "all execution gates must be rechecked."
        ),
    )
    result = {
        "ticker": "MYOR",
        "execution_decision": {
            "execution_status": "NO_TRADE",
            "decision_source": "preflight",
            "actionable": False,
            "model_rating": None,
            "model_confidence": None,
        },
        "metadata": {
            "llm_calls": 0,
            "recommendation_context": context,
            "trade_setup_snapshot": setup,
        },
        "verdict": {
            "ticker": "MYOR",
            "rating": "HOLD",
            "current_price": 1_770.0,
            "fair_value_status": "NOT_EVALUATED_PREFLIGHT",
            "summary": "R/R rejected before debate.",
        },
        "risk_governor": {"status": "reject", "sizing_allowed": False},
    }

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "SINGLE_GATE_REJECT" in report
    assert "1.50x" in report
    assert ">= 2.00x" in report
    assert "0.50x / 25.0%" in report
    assert "Hypothetical Setup — NOT EXECUTABLE" in report
    assert "| **Sizing Allowed** | **NO** |" in report
    assert "Rp 1,745" in report
