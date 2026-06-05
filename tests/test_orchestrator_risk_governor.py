import importlib
import sys
import types

sys.modules.setdefault("yfinance", types.SimpleNamespace())
sys.modules.setdefault("undetected_chromedriver", types.SimpleNamespace())

legacy = importlib.import_module("core.orchestrator.legacy")
_annotate_risk_governor = legacy._annotate_risk_governor
_build_sizing_candidates = legacy._build_sizing_candidates


def _entry(
    ticker: str, *, current_price: float, entry_range: str, target: float
) -> dict:
    return {
        "ticker": ticker,
        "verdict": {
            "ticker": ticker,
            "rating": "BUY",
            "confidence": 0.75,
            "current_price": current_price,
            "entry_price_range": entry_range,
            "target_price": target,
            "stop_loss": current_price * 0.9,
            "risk_reward_ratio": 2.0,
            "expected_return": "+10.0%",
        },
    }


def test_non_deployable_top_pick_is_annotated_but_not_sized() -> None:
    top_n = [_entry("BBCA", current_price=1100, entry_range="950 - 1050", target=1200)]

    _annotate_risk_governor(top_n)
    candidates = _build_sizing_candidates(top_n)

    assert top_n[0]["risk_governor"]["status"] == "wait_for_pullback"
    assert top_n[0]["risk_governor"]["sizing_allowed"] is False
    assert candidates == []


def test_deployable_top_pick_keeps_legacy_sizing_candidate_shape() -> None:
    top_n = [_entry("BBRI", current_price=1000, entry_range="950 - 1050", target=1150)]

    _annotate_risk_governor(top_n)
    candidates = _build_sizing_candidates(top_n)

    assert top_n[0]["risk_governor"]["status"] == "deployable"
    assert candidates == [
        {
            "ticker": "BBRI",
            "current_price": 1000,
            "stop_loss": 900.0,
            "rating": "BUY",
            "confidence": 0.75,
            "rr_ratio": 2.0,
            "target_price": 1150,
            "expected_return": "+10.0%",
        }
    ]


def test_conditional_top_pick_is_not_sized() -> None:
    top_n = [_entry("MYOR", current_price=1000, entry_range="950 - 1050", target=1150)]
    top_n[0]["verdict"].update(
        {
            "rating": "HOLD",
            "confidence": 0.41,
            "weighted_reasoning": "Counter-trend bounce below MA200.",
        }
    )

    _annotate_risk_governor(top_n)
    candidates = _build_sizing_candidates(top_n)

    assert top_n[0]["risk_governor"]["status"] == "conditional_deployable"
    assert top_n[0]["risk_governor"]["sizing_allowed"] is False
    assert candidates == []
