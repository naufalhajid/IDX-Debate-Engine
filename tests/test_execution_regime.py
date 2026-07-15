from core.execution_regime import (
    execution_regime_from_payload,
    execution_params_for,
    resolve_execution_regime,
)


def test_rule_defensive_overrides_hmm_sideways() -> None:
    context = resolve_execution_regime(
        rule_snapshot={
            "regime": "DEFENSIVE",
            "volatility_regime": "HIGH",
            "reasons": ["weekly_return_below_threshold"],
        },
        hmm_state={"label": "SIDEWAYS", "confidence": 0.9467},
    )

    assert context["trend_regime"]["label"] == "SIDEWAYS"
    assert context["volatility_regime"] == "HIGH"
    assert context["execution_regime"] == "DEFENSIVE"
    assert context["execution_regime_reason"] == "rule_based_defensive_override"
    assert context["execution_policy_profile"] == "BEAR_STRESS"
    assert context["execution_params"]["consensus_threshold"] == 0.80
    assert context["execution_params"]["max_position_pct"] == 0.005
    assert context["execution_params"]["max_concurrent_positions"] == 1


def test_normal_and_hmm_bull_resolve_to_bull() -> None:
    context = resolve_execution_regime(
        rule_snapshot={
            "regime": "NORMAL",
            "volatility_regime": "NORMAL",
        },
        hmm_state={"label": "BULL", "confidence": 0.88},
    )

    assert context["execution_regime"] == "BULL"
    assert context["execution_regime_reason"] == "detectors_agree_bull"


def test_high_volatility_prevents_bull_upgrade() -> None:
    context = resolve_execution_regime(
        rule_snapshot={
            "regime": "HIGH",
            "volatility_regime": "HIGH",
        },
        hmm_state={"label": "BULL", "confidence": 0.90},
    )

    assert context["execution_regime"] == "SIDEWAYS"
    assert context["execution_regime_reason"] == "rule_or_volatility_caution"


def test_hmm_bear_stress_resolves_to_defensive() -> None:
    context = resolve_execution_regime(
        rule_snapshot={
            "regime": "NORMAL",
            "volatility_regime": "NORMAL",
        },
        hmm_state={"label": "BEAR_STRESS", "confidence": 0.91},
    )

    assert context["execution_regime"] == "DEFENSIVE"
    assert context["execution_regime_reason"] == "hmm_bear_stress_override"


def test_missing_both_regime_sources_fails_closed() -> None:
    context = resolve_execution_regime(rule_snapshot=None, hmm_state=None)

    assert context["execution_regime"] == "UNKNOWN"
    assert context["execution_params"]["trading_allowed"] is False
    assert context["operational_params"]["top_n_selection"] == 0


def test_defensive_execution_uses_bear_stress_policy_profile() -> None:
    params = execution_params_for("DEFENSIVE")

    assert params["consensus_threshold"] == 0.80
    assert params["max_position_pct"] == 0.005
    assert params["max_concurrent_positions"] == 1


def test_explicit_execution_regime_has_precedence_over_all_legacy_labels() -> None:
    payload = {
        "execution_regime": "SIDEWAYS",
        "regime_context": {"execution_regime": "DEFENSIVE"},
        "metadata": {
            "execution_regime": "BULL",
            "regime": "DEFENSIVE",
        },
        "regime": {"label": "BEAR_STRESS"},
    }

    assert execution_regime_from_payload(payload) == "SIDEWAYS"


def test_invalid_explicit_regime_does_not_create_new_execution_authority() -> None:
    payload = {
        "execution_regime": "NORMAL",
        "regime_context": {"execution_regime": "DEFENSIVE"},
    }

    assert execution_regime_from_payload(payload) == "DEFENSIVE"
