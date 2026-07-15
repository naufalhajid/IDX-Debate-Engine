from schemas.debate import CIOVerdict


def test_cio_buy_is_waitlist_until_risk_and_sizing_complete() -> None:
    verdict = CIOVerdict(
        ticker="BBCA",
        rating="BUY",
        confidence=0.72,
        current_price=1000,
        entry_price_range="950 - 1050",
        target_price=1290,
        stop_loss=930,
    )

    assert verdict.model_rating == "BUY"
    assert verdict.decision_source == "cio"
    assert verdict.model_confidence == 0.72
    assert verdict.policy_confidence is None
    assert verdict.execution_status == "WAITLIST"
    assert verdict.to_trade_card()["actionable"] is False


def test_preflight_placeholder_confidence_is_not_model_confidence() -> None:
    verdict = CIOVerdict(
        ticker="LSIP",
        rating="HOLD",
        confidence=0.40,
        reason_codes=["rr_too_low"],
    )

    assert verdict.decision_source == "preflight"
    assert verdict.model_rating is None
    assert verdict.model_confidence is None
    assert verdict.policy_confidence == 1.0
    assert verdict.execution_status == "NO_TRADE"


def test_preflight_risk_flag_survives_schema_validation() -> None:
    verdict = CIOVerdict(
        ticker="ERAA",
        rating="HOLD",
        confidence=0.40,
        risk_flags=["PREFLIGHT_NOISE_REJECT"],
    )

    dumped = verdict.model_dump()
    assert dumped["risk_flags"] == ["PREFLIGHT_NOISE_REJECT"]
    assert dumped["decision_source"] == "preflight"
    assert dumped["model_confidence"] is None


def test_no_technical_data_maps_to_insufficient_data() -> None:
    verdict = CIOVerdict(
        ticker="BACH",
        rating="HOLD",
        confidence=0.40,
        reason_codes=["no_technical_data"],
    )

    assert verdict.execution_status == "INSUFFICIENT_DATA"
    assert verdict.decision_source == "preflight"
    assert verdict.model_confidence is None


def test_schema_policy_downgrade_preserves_original_model_opinion() -> None:
    verdict = CIOVerdict(
        ticker="BBCA",
        rating="BUY",
        confidence=0.75,
        current_price=1000,
        entry_price_range="1000 - 1010",
        target_price=1035,
        stop_loss=950,
    )

    assert verdict.rating == "HOLD"
    assert verdict.model_rating == "BUY"
    assert verdict.model_confidence == 0.75
    assert verdict.decision_source == "risk_guard"
    assert verdict.policy_confidence == 1.0
    assert verdict.execution_status == "WAITLIST"


def test_avoid_preserves_model_confidence_but_is_not_actionable() -> None:
    verdict = CIOVerdict(
        ticker="BBCA",
        rating="AVOID",
        confidence=0.77,
    )

    card = verdict.to_trade_card()
    assert verdict.model_rating == "AVOID"
    assert verdict.model_confidence == 0.77
    assert verdict.execution_status == "AVOID"
    assert card["actionable"] is False

