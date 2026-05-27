from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.orchestrator.legacy import (
    MIN_CONFIDENCE_FOR_SETUP,
    SetupCoherenceError,
    apply_extreme_overvaluation_flag,
    apply_minimum_confidence_gate,
    apply_setup_coherence_gate,
    generate_top3_report,
    sync_metric_aliases,
    validate_setup_coherence,
)
from services.debate_chamber import apply_staleness_penalty


def _result(confidence: float = 0.13) -> dict:
    return {
        "ticker": "AMRT",
        "verdict": {
            "ticker": "AMRT",
            "rating": "BUY",
            "confidence": confidence,
            "current_price": 1000,
            "fair_value": 1200,
            "entry_price_range": "950 - 1000",
            "target_price": 1150,
            "stop_loss": 900,
            "risk_reward_ratio": 1.5,
        },
        "conviction_score": 0.5,
        "metadata": {},
    }


def test_minimum_confidence_gate_skips_setup_generation() -> None:
    called = False
    result = _result(confidence=(MIN_CONFIDENCE_FOR_SETUP - 1) / 100)

    def generate_setup() -> None:
        nonlocal called
        called = True

    skipped = apply_minimum_confidence_gate("AMRT", result, generate_setup)

    assert skipped is True
    assert called is False
    assert result["verdict"]["rating"] == "INSUFFICIENT_DATA"
    assert result["verdict"]["action"] == "SKIP"
    assert result["verdict"]["entry_price_range"] is None
    assert result["verdict"]["target_price"] is None
    assert result["verdict"]["stop_loss"] is None
    assert result["verdict"]["risk_reward_ratio"] is None
    assert result["risk_governor"]["status"] == "reject"
    assert result["sizing"] == "Skip — confidence below threshold"
    assert "confidence_24pct_below_minimum" in result["reasons"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"current_price": 100, "entry_low": 95, "entry_high": 100, "target": 100, "stop": 90},
            "target (100) does not exceed top of entry range (100)",
        ),
        (
            {"current_price": 100, "entry_low": 95, "entry_high": 100, "target": 120, "stop": 95},
            "stop (95) is not below bottom of entry range (95)",
        ),
        (
            {"current_price": 112, "entry_low": 95, "entry_high": 100, "target": 120, "stop": 90},
            "current price (112) is more than 10% above entry range top (100)",
        ),
        (
            {"current_price": 100, "entry_low": 95, "entry_high": 100, "target": 112, "stop": 90},
            "R/R (1.20x) below minimum threshold of 1.5x",
        ),
    ],
)
def test_validate_setup_coherence_conditions(kwargs: dict, message: str) -> None:
    with pytest.raises(SetupCoherenceError, match=re.escape(message)):
        validate_setup_coherence("TEST", **kwargs)


def test_apply_setup_coherence_gate_removes_gula_like_setup() -> None:
    result = {
        "ticker": "GULA",
        "verdict": {
            "ticker": "GULA",
            "rating": "BUY",
            "confidence": 0.62,
            "current_price": 500,
            "entry_price_range": "360 - 366",
            "target_price": 368,
            "stop_loss": 346,
            "risk_reward_ratio": 1.8,
        },
    }

    rejected = apply_setup_coherence_gate("GULA", result)

    assert rejected is True
    assert result["verdict"]["rating"] == "AVOID"
    assert result["verdict"]["entry_price_range"] is None
    assert result["verdict"]["target_price"] is None
    assert result["verdict"]["stop_loss"] is None
    assert result["risk_governor"]["status"] == "reject"
    assert any("more than 10% above entry range" in reason for reason in result["reasons"])


@pytest.mark.parametrize(
    ("age_hours", "expected"),
    [(24, 0.80), (48, 0.68), (72, 0.56), (100, 0.56)],
)
def test_apply_staleness_penalty_boundaries(age_hours: int, expected: float) -> None:
    assert apply_staleness_penalty(0.80, age_hours) == pytest.approx(expected)


def test_extreme_overvaluation_flag_adds_reason_and_note() -> None:
    result = {
        "ticker": "CYBR",
        "verdict": {
            "ticker": "CYBR",
            "current_price": 1200,
            "fair_value": 100,
            "rating": "HOLD",
            "confidence": 0.5,
        },
    }

    flagged = apply_extreme_overvaluation_flag("CYBR", result)

    assert flagged is True
    assert "EXTREME_OVERVALUATION" in result["flags"]
    assert "EXTREME_OVERVALUATION" in result["reasons"]
    assert "fair_value_model_may_not_apply" in result["reasons"]
    assert "price/FV ratio 12.0x" in result["note"]


def test_metric_aliases_and_report_labels_are_unambiguous(tmp_path: Path) -> None:
    entry = _result(confidence=0.61)
    entry["ticker"] = "INDF"
    entry["verdict"]["ticker"] = "INDF"
    entry["conviction_score"] = 0.50
    entry["trade_conviction"] = 0.50
    entry["risk_governor"] = {
        "status": "deployable",
        "sizing_allowed": True,
        "message": "ok",
    }
    sync_metric_aliases(entry)

    assert entry["model_confidence"] == pytest.approx(0.61)
    assert entry["trade_conviction"] == pytest.approx(0.50)

    report = generate_top3_report(
        [entry],
        [entry],
        path=tmp_path / "TOP_3_SWING_TRADES.md",
    )

    assert "| **Model Confidence** | 61% |" in report
    assert "| **Trade Conviction** | 50.00% |" in report
    assert "Model Confidence" in report
    assert "Trade Conviction" in report
