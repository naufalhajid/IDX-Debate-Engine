import pytest

import app.api.result_adapter as result_adapter_module
from app.api.result_adapter import (
    build_execution_decision,
    normalize_debate_state,
    normalize_result,
)
from utils.ticker import InvalidIDXTicker, canonicalize_result_identity


def test_result_identity_rejects_cross_stock_nested_payload() -> None:
    with pytest.raises(InvalidIDXTicker, match="Conflicting result ticker"):
        canonicalize_result_identity(
            {
                "ticker": "BBCA",
                "verdict": {"ticker": "BMRI", "rating": "BUY"},
            }
        )


def test_result_identity_canonicalizes_all_present_aliases() -> None:
    normalized = canonicalize_result_identity(
        {
            "ticker": "bbca.jk",
            "verdict": {"ticker": "BBCA", "rating": "HOLD"},
            "risk_governor": {"ticker": "bbca.jk", "status": "reject"},
            "execution_decision": {"ticker": "bbca"},
        }
    )

    assert normalized["ticker"] == "BBCA"
    assert normalized["verdict"]["ticker"] == "BBCA"
    assert normalized["risk_governor"]["ticker"] == "BBCA"
    assert normalized["execution_decision"]["ticker"] == "BBCA"


def test_normalize_result_rejects_invalid_identity_before_rr_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        result_adapter_module,
        "get_required_rr_resolution",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid ticker reached R/R configuration resolution"
        ),
    )

    with pytest.raises(InvalidIDXTicker):
        normalize_result(
            {
                "ticker": "../escape",
                "verdict": {"rating": "HOLD"},
            }
        )


def test_normalize_result_preserves_missing_fair_value_as_none() -> None:
    result = normalize_result(
        {
            "ticker": "BBCA",
            "verdict": {
                "ticker": "BBCA",
                "rating": "HOLD",
                "fair_value": None,
            },
            "risk_governor": {},
            "metadata": {"run_id": "20260713_000000"},
        }
    )

    assert result["scout_metrics"]["fundamental"]["fair_value"] is None
    assert result["fair_value_status"] is None
    assert result["scout_metrics"]["fundamental"]["fair_value_status"] is None


def test_normalize_result_preserves_explicit_preflight_fair_value_status() -> None:
    result = normalize_result(
        {
            "ticker": "MAPA",
            "verdict": {
                "ticker": "MAPA",
                "rating": "HOLD",
                "fair_value": None,
                "fair_value_status": "NOT_EVALUATED_PREFLIGHT",
            },
            "risk_governor": {"entry_low": None, "entry_high": None},
            "metadata": {"run_id": "20260716_141220"},
        }
    )

    assert result["fair_value_status"] == "NOT_EVALUATED_PREFLIGHT"
    assert (
        result["scout_metrics"]["fundamental"]["fair_value_status"]
        == "NOT_EVALUATED_PREFLIGHT"
    )
    assert result["scout_metrics"]["technical"]["entry"] == "-"


def test_normalize_result_preserves_explicit_execution_regime_contract() -> None:
    regime_context = {
        "execution_regime": "DEFENSIVE",
        "execution_regime_reason": "rule_based_defensive_override",
        "volatility_regime": "HIGH",
        "trend_regime": {
            "label": "SIDEWAYS",
            "confidence": 0.94,
            "source": "hmm",
        },
        "execution_params": {"consensus_threshold": 0.8},
    }
    result = normalize_result(
        {
            "ticker": "BBCA",
            "verdict": {"ticker": "BBCA", "rating": "HOLD"},
            "metadata": {"run_id": "20260713_000000"},
            "rule_regime_snapshot": {"regime": "DEFENSIVE"},
            "regime_context": regime_context,
            "hmm_regime": {"label": "SIDEWAYS", "confidence": 0.94},
            "trend_regime": regime_context["trend_regime"],
            "volatility_regime": "HIGH",
            "execution_regime": "DEFENSIVE",
            "execution_regime_reason": "rule_based_defensive_override",
            "trading_params": {"consensus_threshold": 0.8},
        }
    )

    assert result["rule_regime_snapshot"] == {"regime": "DEFENSIVE"}
    assert result["regime_context"] == regime_context
    assert result["hmm_regime"]["label"] == "SIDEWAYS"
    assert result["trend_regime"]["source"] == "hmm"
    assert result["volatility_regime"] == "HIGH"
    assert result["execution_regime"] == "DEFENSIVE"
    assert result["execution_regime_reason"] == "rule_based_defensive_override"
    assert result["trading_params"] == {"consensus_threshold": 0.8}


def test_normalize_debate_state_does_not_drop_explicit_regime_fields() -> None:
    result = normalize_debate_state(
        "BMRI",
        {
            "final_verdict": {"ticker": "BMRI", "rating": "HOLD"},
            "metadata": {"run_id": "20260713_000000"},
            "regime_context": {"execution_regime": "SIDEWAYS"},
            "hmm_regime": {"label": "SIDEWAYS", "confidence": 0.8},
            "trend_regime": {"label": "SIDEWAYS", "source": "hmm"},
            "volatility_regime": "NORMAL",
            "execution_regime": "SIDEWAYS",
            "execution_regime_reason": "hmm_sideways",
            "trading_params": {"max_position_pct": 0.01},
        },
    )

    assert result["execution_regime"] == "SIDEWAYS"
    assert result["execution_regime_reason"] == "hmm_sideways"
    assert result["hmm_regime"]["confidence"] == 0.8
    assert result["trading_params"] == {"max_position_pct": 0.01}


def _buy_entry(**overrides):
    payload = {
        "ticker": "BBCA",
        "status": "success",
        "verdict": {
            "ticker": "BBCA",
            "rating": "BUY",
            "confidence": 0.72,
            "current_price": 1000,
            "entry_price_range": "950 - 1050",
            "target_price": 1290,
            "stop_loss": 930,
            "risk_reward_ratio": 2.0,
            "execution_horizon_days": 10,
        },
        "metadata": {"run_id": "20260713_000000"},
    }
    payload.update(overrides)
    return payload


def test_missing_or_failed_result_is_insufficient_and_fail_closed() -> None:
    result = normalize_result(
        {
            "ticker": "FAIL",
            "status": "failed",
            "error": "timeout",
            "verdict": {},
            "metadata": {"run_id": "20260713_000000"},
        }
    )

    assert result["execution_status"] == "INSUFFICIENT_DATA"
    assert result["decision_source"] == "risk_guard"
    assert result["actionable"] is False
    assert result["entry_low"] is None
    assert result["entry_high"] is None
    assert result["target_price"] is None
    assert result["stop_loss"] is None


def test_preflight_hold_placeholder_is_not_model_confidence() -> None:
    result = normalize_result(
        {
            "ticker": "LSIP",
            "status": "success",
            "verdict": {
                "ticker": "LSIP",
                "rating": "HOLD",
                "confidence": 0.40,
                "reason_codes": ["rr_too_low"],
            },
            "risk_governor": {
                "status": "reject",
                "sizing_allowed": False,
                "reason_codes": ["rr_too_low"],
            },
            "metadata": {"run_id": "20260713_000000"},
        }
    )

    assert result["execution_status"] == "NO_TRADE"
    assert result["decision_source"] == "preflight"
    assert result["model_rating"] is None
    assert result["model_confidence"] is None
    assert result["policy_confidence"] == 1.0
    assert result["rating"] == "HOLD"
    assert result["actionable"] is False


def test_no_technical_data_stays_insufficient_data() -> None:
    result = normalize_result(
        {
            "ticker": "BACH",
            "status": "success",
            "verdict": {
                "ticker": "BACH",
                "rating": "HOLD",
                "confidence": 0.40,
            },
            "metadata": {
                "run_id": "20260713_000000",
                "tradeability_preflight": {
                    "status": "skip",
                    "reason": "no_technical_data",
                },
            },
        }
    )

    assert result["execution_status"] == "INSUFFICIENT_DATA"
    assert result["decision_source"] == "preflight"
    assert result["model_confidence"] is None
    assert result["actionable"] is False


def test_avoid_without_risk_payload_is_never_actionable() -> None:
    result = normalize_result(
        {
            "ticker": "BBCA",
            "status": "success",
            "verdict": {
                "ticker": "BBCA",
                "rating": "AVOID",
                "confidence": 0.77,
            },
            "metadata": {"run_id": "20260713_000000"},
        }
    )

    assert result["execution_status"] == "AVOID"
    assert result["decision_source"] == "cio"
    assert result["model_rating"] == "AVOID"
    assert result["model_confidence"] == 0.77
    assert result["actionable"] is False


def test_buy_without_risk_or_sizing_is_waitlist_not_actionable() -> None:
    result = normalize_result(_buy_entry())

    assert result["execution_status"] == "WAITLIST"
    assert result["rating"] == "HOLD"
    assert result["model_rating"] == "BUY"
    assert result["model_confidence"] == 0.72
    assert result["actionable"] is False
    assert "risk_decision_missing" in result["reason_codes"]


def test_risk_deployable_buy_without_position_sizing_remains_waitlist() -> None:
    result = normalize_result(
        _buy_entry(
            risk_governor={
                "status": "deployable",
                "sizing_allowed": True,
                "entry_low": 950,
                "entry_high": 1050,
                "target_price": 1290,
                "stop_loss": 930,
            }
        )
    )

    assert result["execution_status"] == "WAITLIST"
    assert result["actionable"] is False
    assert "position_sizing_pending" in result["reason_codes"]


def test_risk_rejection_overrides_preliminary_cio_waitlist_status() -> None:
    entry = _buy_entry(
        risk_governor={
            "status": "reject",
            "sizing_allowed": False,
            "entry_low": 950,
            "entry_high": 1050,
            "target_price": 1290,
            "stop_loss": 930,
            "reason_codes": ["rr_too_low"],
        }
    )
    entry["verdict"]["decision_source"] = "cio"
    entry["verdict"]["execution_status"] = "WAITLIST"

    result = normalize_result(entry)

    assert result["model_rating"] == "BUY"
    assert result["model_confidence"] == 0.72
    assert result["decision_source"] == "risk_guard"
    assert result["execution_status"] == "NO_TRADE"
    assert result["actionable"] is False


def test_public_decision_builder_recomputes_stale_status_after_risk() -> None:
    entry = _buy_entry(
        execution_decision={
            "decision_source": "cio",
            "execution_status": "WAITLIST",
            "model_rating": "BUY",
            "model_confidence": 0.72,
        },
        risk_governor={
            "status": "reject",
            "sizing_allowed": False,
            "entry_low": 950,
            "entry_high": 1050,
            "target_price": 1290,
            "stop_loss": 930,
            "reason_codes": ["risk_budget_exceeded"],
        },
    )

    decision = build_execution_decision(entry)

    assert decision["decision_source"] == "risk_guard"
    assert decision["execution_status"] == "NO_TRADE"
    assert decision["actionable"] is False
    assert decision["entry_low"] == 950
    assert decision["entry_high"] == 1050


def test_only_complete_lot_sized_buy_is_executable() -> None:
    result = normalize_result(
        _buy_entry(
            risk_governor={
                "status": "deployable",
                "sizing_allowed": True,
                "entry_low": 950,
                "entry_high": 1050,
                "target_price": 1290,
                "stop_loss": 930,
            },
            position_sizing={
                "lot": 2,
                "shares": 200,
                "max_loss_rp": 24_000,
            },
        )
    )

    assert result["execution_status"] == "EXECUTABLE_BUY"
    assert result["rating"] == "BUY"
    assert result["actionable"] is True
    assert result["lot_count"] == 2
    assert result["shares"] == 200
    assert result["shares_per_lot"] == 100
    assert result["max_loss_rp"] == 24_000


def test_defensive_non_large_cap_uses_stricter_canonical_required_rr() -> None:
    entry = _buy_entry(
        ticker="CYBR",
        execution_regime="DEFENSIVE",
        metadata={
            "run_id": "20260713_000000",
            "execution_regime": "DEFENSIVE",
            "market_cap_idr": 5_000_000_000_000,
        },
        risk_governor={
            "status": "deployable",
            "sizing_allowed": True,
            "entry_low": 950,
            "entry_high": 1050,
            "target_price": 1290,
            "stop_loss": 930,
        },
        position_sizing={"lot": 2, "shares": 200, "max_loss_rp": 24_000},
    )
    entry["verdict"]["ticker"] = "CYBR"

    decision = build_execution_decision(entry)

    assert decision["required_rr"] == 2.106
    assert decision["rr_base_minimum"] == 1.62
    assert decision["rr_regime_multiplier"] == 1.3
    assert decision["rr_tier"] == "default"
    assert decision["execution_status"] == "WAITLIST"
    assert decision["actionable"] is False


def test_explicit_executable_status_is_downgraded_when_contract_incomplete() -> None:
    result = normalize_result(
        _buy_entry(
            execution_status="EXECUTABLE_BUY",
            risk_governor={
                "status": "deployable",
                "sizing_allowed": True,
                "entry_low": 950,
                "entry_high": 1050,
                "target_price": 1290,
                "stop_loss": 930,
            },
        )
    )

    assert result["execution_status"] == "WAITLIST"
    assert result["actionable"] is False
    assert "executable_contract_incomplete" in result["reason_codes"]


def test_conviction_does_not_fall_back_to_model_confidence() -> None:
    result = normalize_result(_buy_entry())

    assert result["model_confidence"] == 0.72
    assert result["conviction_score"] == 0


def test_invalid_streamed_verdict_json_is_insufficient_data() -> None:
    result = normalize_debate_state(
        "BBCA",
        {
            "final_verdict": "not-json",
            "metadata": {"run_id": "20260713_000000"},
        },
    )

    assert result["execution_status"] == "INSUFFICIENT_DATA"
    assert result["actionable"] is False
    assert result["model_confidence"] is None
