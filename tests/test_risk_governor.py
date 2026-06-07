from core.risk_governor import evaluate_risk


def _candidate(**overrides):
    payload = {
        "ticker": "BBCA",
        "verdict": {
            "ticker": "BBCA",
            "rating": "BUY",
            "confidence": 0.75,
            "current_price": 1000,
            "entry_price_range": "950 - 1050",
            "target_price": 1150,
            "stop_loss": 930,
            "is_overvalued": False,
            "risk_reward_ratio": 2.0,
        },
    }
    verdict_overrides = overrides.pop("verdict", None)
    if verdict_overrides:
        payload["verdict"].update(verdict_overrides)
    payload.update(overrides)
    return payload


def test_current_price_inside_entry_range_is_deployable() -> None:
    decision = evaluate_risk(_candidate())

    assert decision.status == "deployable"
    assert decision.sizing_allowed is True
    assert decision.reason_codes == ["price_inside_entry_range"]


def test_current_price_inside_entry_range_stays_deployable_in_normal_regime() -> None:
    decision = evaluate_risk(
        _candidate(
            market_regime={
                "regime": "NORMAL",
                "defensive_triggered": False,
                "reasons": [],
            }
        )
    )

    assert decision.status == "deployable"
    assert decision.sizing_allowed is True
    assert "market_regime_defensive" not in decision.reason_codes


def test_soft_overvalued_inside_range_stays_deployable() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "current_price": 1040,
                "fair_value": 1000,
                "fair_value_base": 1000,
                "fair_value_high": 1150,
                "risk_overvalued": False,
                "is_overvalued": False,
            }
        )
    )

    assert decision.status == "deployable"
    assert decision.sizing_allowed is True
    assert "overvalued" not in decision.reason_codes


def test_explicit_risk_overvalued_false_overrides_legacy_true() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "current_price": 1040,
                "fair_value": 1000,
                "fair_value_high": 1150,
                "risk_overvalued": False,
                "is_overvalued": True,
            }
        )
    )

    assert decision.status == "deployable"
    assert "overvalued" not in decision.reason_codes


def test_explicit_risk_overvalued_rejects_even_if_legacy_flag_absent() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "current_price": 1200,
                "fair_value": 1000,
                "fair_value_high": 1150,
                "risk_overvalued": True,
                "is_overvalued": False,
                "target_price": 1300,
            }
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "overvalued" in decision.reason_codes


def test_defensive_regime_downgrades_deployable_buy_to_watchlist() -> None:
    decision = evaluate_risk(
        _candidate(
            market_regime={
                "regime": "DEFENSIVE",
                "defensive_triggered": True,
                "reasons": ["weekly_return_below_threshold"],
            }
        )
    )

    assert decision.status == "watchlist_only"
    assert decision.sizing_allowed is False
    assert "market_regime_defensive" in decision.reason_codes
    assert "price_inside_entry_range" in decision.reason_codes


def test_current_price_above_entry_high_waits_for_pullback() -> None:
    decision = evaluate_risk(
        _candidate(verdict={"current_price": 1100, "target_price": 1200})
    )

    assert decision.status == "wait_for_pullback"
    assert decision.sizing_allowed is False
    assert "price_above_entry_range" in decision.reason_codes


def test_target_at_or_below_current_price_rejects() -> None:
    decision = evaluate_risk(
        _candidate(verdict={"current_price": 1100, "target_price": 1100})
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "upside_exhausted" in decision.reason_codes


def test_invalid_or_missing_entry_range_rejects() -> None:
    decision = evaluate_risk(_candidate(verdict={"entry_price_range": None}))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "invalid_entry_range" in decision.reason_codes


def test_defensive_regime_keeps_invalid_setup_rejected() -> None:
    decision = evaluate_risk(
        _candidate(
            market_regime={"regime": "DEFENSIVE"},
            verdict={"entry_price_range": None},
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "invalid_entry_range" in decision.reason_codes
    assert "market_regime_defensive" not in decision.reason_codes


def test_stop_loss_at_or_above_current_price_rejects() -> None:
    decision = evaluate_risk(_candidate(verdict={"stop_loss": 1000}))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "invalid_stop_loss" in decision.reason_codes


def test_current_price_below_entry_low_is_watchlist_only() -> None:
    decision = evaluate_risk(
        _candidate(verdict={"current_price": 900, "stop_loss": 850})
    )

    assert decision.status == "watchlist_only"
    assert decision.sizing_allowed is False
    assert "price_below_entry_range" in decision.reason_codes


def test_avoid_verdict_rejects_even_inside_entry_range() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "rating": "AVOID",
                "confidence": 0.22,
                "is_overvalued": True,
                "risk_reward_ratio": 0.56,
                "weighted_reasoning": (
                    "Absence of technical indicators (INSUFFICIENT_DATA)."
                ),
            }
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "rating_not_buyable" in decision.reason_codes
    assert "low_confidence" in decision.reason_codes
    assert "overvalued" in decision.reason_codes
    assert "rr_too_low" in decision.reason_codes
    assert "insufficient_technical_data" in decision.reason_codes


def test_defensive_regime_keeps_avoid_rejected_not_watchlist() -> None:
    decision = evaluate_risk(
        _candidate(
            market_regime={"regime": "DEFENSIVE"},
            verdict={
                "rating": "AVOID",
                "confidence": 0.22,
                "is_overvalued": True,
                "risk_reward_ratio": 0.56,
            },
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "rating_not_buyable" in decision.reason_codes
    assert "market_regime_defensive" not in decision.reason_codes


def test_large_cap_rr_above_tier_threshold_is_not_too_low() -> None:
    decision = evaluate_risk(
        _candidate(
            ticker="BBRI",
            metadata={"market_cap_idr": 400_000_000_000_000},
            verdict={
                "ticker": "BBRI",
                "rating": "AVOID",
                "confidence": 0.25,
                "risk_reward_ratio": 1.38,
                "weighted_reasoning": "Counter-trend bounce below MA200.",
            },
        )
    )

    assert decision.status == "reject"
    assert "rating_not_buyable" in decision.reason_codes
    assert "low_confidence" in decision.reason_codes
    assert "counter_trend_setup" in decision.reason_codes
    assert "rr_too_low" not in decision.reason_codes


def test_default_tier_rr_below_default_threshold_is_too_low() -> None:
    decision = evaluate_risk(
        _candidate(
            ticker="CYBR",
            metadata={"market_cap_idr": 5_000_000_000_000},
            verdict={
                "ticker": "CYBR",
                "rating": "AVOID",
                "confidence": 0.25,
                "risk_reward_ratio": 1.38,
            },
        )
    )

    assert decision.status == "reject"
    assert "rr_too_low" in decision.reason_codes


def test_hold_low_confidence_inside_entry_is_conditional_not_sized() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "rating": "HOLD",
                "confidence": 0.41,
                "weighted_reasoning": "Counter-trend bounce below MA200.",
            }
        )
    )

    assert decision.status == "conditional_deployable"
    assert decision.sizing_allowed is False
    assert "rating_hold" in decision.reason_codes
    assert "low_confidence" in decision.reason_codes
    assert "counter_trend_setup" in decision.reason_codes
    assert "price_inside_entry_range" in decision.reason_codes


def test_sentiment_insufficient_data_does_not_reject_when_technicals_exist() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "weighted_reasoning": (
                    "Technicals are constructive, but sentiment is INSUFFICIENT_DATA."
                )
            }
        )
    )

    assert decision.status == "deployable"
    assert "insufficient_technical_data" not in decision.reason_codes
