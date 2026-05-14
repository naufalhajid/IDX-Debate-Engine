from core.risk_governor import evaluate_risk


def _candidate(**overrides):
    payload = {
        "ticker": "BBCA",
        "verdict": {
            "ticker": "BBCA",
            "current_price": 1000,
            "entry_price_range": "950 - 1050",
            "target_price": 1150,
            "stop_loss": 930,
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


def test_stop_loss_at_or_above_current_price_rejects() -> None:
    decision = evaluate_risk(_candidate(verdict={"stop_loss": 1000}))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "invalid_stop_loss" in decision.reason_codes


def test_current_price_below_entry_low_is_watchlist_only() -> None:
    decision = evaluate_risk(_candidate(verdict={"current_price": 900, "stop_loss": 850}))

    assert decision.status == "watchlist_only"
    assert decision.sizing_allowed is False
    assert "price_below_entry_range" in decision.reason_codes
