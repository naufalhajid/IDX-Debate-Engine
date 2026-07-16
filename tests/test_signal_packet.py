"""Regression tests for the pre-execution signal instrumentation packet."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from core.orchestrator.legacy import (
    _finalize_execution_decisions,
    _pre_cio_terminal_result,
    save_full_results,
    save_individual_debates_versioned,
)
from schemas.debate import SignalPacket
from services.debate_chamber import DebateChamber
from services.trade_setup import build_trade_setup_snapshot


SIGNAL_PACKET_FIELDS = {
    "signal_lean",
    "chart_strength",
    "relative_strength",
    "volume_confirmation",
    "fundamental_quality",
    "valuation_state",
    "forecast_state",
    "execution_eligible",
    "execution_rejection_reason",
    "required_entry_trigger",
}


def _history(rows: int = 260) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-02", periods=rows)
    close = np.linspace(900.0, 1_100.0, rows)
    return pd.DataFrame(
        {
            "Open": close - 5.0,
            "High": close + 15.0,
            "Low": close - 15.0,
            "Close": close,
            "Volume": np.full(rows, 2_000_000.0),
        },
        index=index,
    )


def _bullish_technicals() -> dict[str, Any]:
    return {
        "current_price": 1_100.0,
        "sma20": 1_040.0,
        "ema20": 1_050.0,
        "ma50": 1_080.0,
        "ma50_rising": True,
        "ma200": 950.0,
        "ma200_context": "ABOVE",
        "rsi14": 58.0,
        "atr14": 25.0,
        "low_20d": 1_020.0,
        "low_50d": 980.0,
        "high_20d": 1_180.0,
        "high_50d": 1_220.0,
        "52w_high": 1_300.0,
        "volume_surge_ratio": 1.25,
        "return_5d_pct": 2.0,
    }


def _rejected_envelope(reason_code: str) -> dict[str, Any]:
    return {
        "rejected": True,
        "reason_code": reason_code,
        "reason": f"{reason_code}: fixture rejection",
        "hypothetical_envelope": {
            "entry_low": 1_020.0,
            "entry_high": 1_050.0,
            "target_price": 1_150.0,
            "stop_loss": 990.0,
            "risk_reward_ratio": 1.67,
            "required_rr": 2.0,
        },
    }


@pytest.mark.parametrize(
    ("rows", "reason_code", "expected_status"),
    [
        (260, "no_momentum_confirmation", "NO_MOMENTUM"),
        (260, "rr_too_low", "RR_TOO_LOW"),
        (40, "rr_too_low", "INSUFFICIENT_DATA"),
    ],
)
def test_trade_setup_snapshot_contains_signal_packet_before_terminal_gate(
    rows: int,
    reason_code: str,
    expected_status: str,
) -> None:
    def envelope_calculator(*_args: Any) -> dict[str, Any]:
        return _rejected_envelope(reason_code)

    snapshot = build_trade_setup_snapshot(
        ticker="TAPG",
        market_data={"history": _history(rows)},
        current_price=1_100.0,
        execution_regime="SIDEWAYS",
        sector="consumer_staples",
        technical_indicators=_bullish_technicals(),
        preflight={"status": "clean"},
        envelope_calculator=envelope_calculator,
    )

    assert snapshot["status"] == expected_status
    packet = snapshot["signal_packet"]
    assert set(packet) == SIGNAL_PACKET_FIELDS
    assert packet["signal_lean"] == "BULLISH_SETUP"
    assert packet["chart_strength"] == "BULLISH"
    assert packet["forecast_state"] == "NOT_EVALUATED"
    assert packet["execution_eligible"] is False
    assert packet["execution_rejection_reason"] == snapshot["reason_code"]
    if expected_status == "RR_TOO_LOW":
        trigger = packet["required_entry_trigger"]
        assert "R/R-only" in trigger
        assert "at or below Rp 1,040" in trigger
        assert "all execution gates" in trigger


def test_preflight_noise_reject_keeps_signal_and_never_calls_envelope() -> None:
    def envelope_calculator(*_args: Any) -> dict[str, Any]:
        pytest.fail("preflight rejection reached the envelope calculator")

    snapshot = build_trade_setup_snapshot(
        ticker="TAPG",
        market_data={"history": _history()},
        current_price=1_100.0,
        execution_regime="SIDEWAYS",
        sector="consumer_staples",
        technical_indicators=_bullish_technicals(),
        preflight={"status": "reject", "reason": "stop inside ATR noise"},
        envelope_calculator=envelope_calculator,
    )

    assert snapshot["status"] == "STOP_INSIDE_NOISE"
    assert snapshot["signal_packet"]["signal_lean"] == "BULLISH_SETUP"
    assert snapshot["signal_packet"]["execution_eligible"] is False
    assert (
        snapshot["signal_packet"]["execution_rejection_reason"]
        == "preflight_noise_reject"
    )


@pytest.mark.asyncio
async def test_terminal_run_persists_signal_packet_before_preflight_return() -> None:
    snapshot = build_trade_setup_snapshot(
        ticker="TAPG",
        market_data={"history": _history()},
        current_price=1_100.0,
        execution_regime="SIDEWAYS",
        sector="consumer_staples",
        technical_indicators=_bullish_technicals(),
        preflight={"status": "clean"},
        envelope_calculator=lambda *_args: _rejected_envelope(
            "no_momentum_confirmation"
        ),
    )
    prepared = {
        "ticker": "TAPG",
        "current_price": 1_100.0,
        "sector": "consumer_staples",
        "market_data": {"history": _history(), "source": "phase1_fixture"},
        "regime_context": {
            "execution_regime": "SIDEWAYS",
            "execution_regime_reason": "fixture",
            "execution_params": {},
        },
        "hmm_regime": {},
        "rule_regime_snapshot": None,
        "trade_setup_snapshot": snapshot,
        "candidate_context": {
            "Ticker": "TAPG",
            "Current Price": 1_100.0,
            "RSI (14)": 58.0,
            "ema20": 1_050.0,
            "ma200_context": "ABOVE",
            "price_return_1m": 14.23,
            "rs_vs_ihsg_1m": 11.64,
            "vol_surge_ratio": 1.25,
            "Piotroski F-Score": 5,
            "Valuation Gap (%)": -8.9,
        },
    }
    chamber = object.__new__(DebateChamber)
    chamber._llm_call_counts = {}

    result = await chamber.run("TAPG", prepared_setup=prepared)
    metadata_packet = result["metadata"]["signal_packet"]

    assert result["metadata"]["trade_setup_snapshot"]["signal_packet"] == (
        metadata_packet
    )
    assert set(metadata_packet) == SIGNAL_PACKET_FIELDS
    assert metadata_packet["signal_lean"] == "BULLISH_SETUP"
    assert metadata_packet["relative_strength"] == pytest.approx(11.64)
    assert metadata_packet["volume_confirmation"] is True
    assert metadata_packet["fundamental_quality"] == "ADEQUATE"
    assert metadata_packet["valuation_state"] == "OVERVALUED"
    assert metadata_packet["forecast_state"] == "NOT_EVALUATED"
    assert metadata_packet["execution_eligible"] is False
    assert metadata_packet["execution_rejection_reason"] == "no_momentum_confirmation"


def test_signal_packet_schema_is_strict_and_serializable() -> None:
    packet = {
        "signal_lean": "BULLISH_SETUP",
        "chart_strength": "BULLISH",
        "relative_strength": 11.64,
        "volume_confirmation": True,
        "fundamental_quality": "ADEQUATE",
        "valuation_state": "OVERVALUED",
        "forecast_state": "NOT_EVALUATED",
        "execution_eligible": False,
        "execution_rejection_reason": "no_momentum_confirmation",
        "required_entry_trigger": "Wait for momentum confirmation.",
    }

    dumped = SignalPacket.model_validate(packet).model_dump(mode="json")

    assert dumped == packet
    with pytest.raises(ValidationError):
        SignalPacket.model_validate({**packet, "misspelled_field": True})


def test_signal_packet_survives_batch_and_per_ticker_persistence(tmp_path) -> None:
    packet = SignalPacket(
        signal_lean="BULLISH_SETUP",
        chart_strength="BULLISH",
        relative_strength=11.64,
        volume_confirmation=True,
        fundamental_quality="ADEQUATE",
        valuation_state="OVERVALUED",
        forecast_state="SKIPPED_PREFLIGHT",
        execution_eligible=False,
        execution_rejection_reason="rr_too_low",
    ).model_dump(mode="json")
    result = {
        "ticker": "TAPG",
        "verdict": {"ticker": "TAPG", "rating": "HOLD"},
        "metadata": {"signal_packet": packet},
        "error": None,
    }
    batch_path = tmp_path / "full_batch_results.json"

    save_full_results([result], batch_path)
    save_individual_debates_versioned(
        [result],
        "20260715_phase1",
        output_dir=tmp_path,
        record_backtest_memory=False,
    )

    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    latest_path = tmp_path / "debates" / "TAPG" / "latest_debate.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    assert batch[0]["metadata"]["signal_packet"] == packet
    assert latest["metadata"]["signal_packet"] == packet


def test_pre_cio_rejection_still_persists_advisory_signal() -> None:
    result = _pre_cio_terminal_result(
        {
            "Ticker": "TAPG",
            "Current Price": 1_100.0,
            "Composite Score": 72.0,
            "rs_vs_ihsg_1m": 11.64,
            "Piotroski F-Score": 5,
            "Valuation Gap (%)": -8.9,
        },
        reason_code="exdate_imminent",
        reason="Ex-date within seven days.",
    )

    packet = result["metadata"]["signal_packet"]
    assert packet["signal_lean"] == "BULLISH_SETUP"
    assert packet["relative_strength"] == pytest.approx(11.64)
    assert packet["forecast_state"] == "SKIPPED_PRE_CIO"
    assert packet["execution_eligible"] is False
    assert packet["execution_rejection_reason"] == "exdate_imminent"


def test_canonical_execution_decision_only_updates_execution_fields() -> None:
    packet = SignalPacket(
        signal_lean="BULLISH_SETUP",
        chart_strength="BULLISH",
        relative_strength=11.64,
        volume_confirmation=True,
        fundamental_quality="ADEQUATE",
        valuation_state="OVERVALUED",
        forecast_state="READY",
        execution_eligible=None,
    ).model_dump(mode="json")
    raw_fields = {
        key: packet[key]
        for key in (
            "signal_lean",
            "chart_strength",
            "relative_strength",
            "volume_confirmation",
            "fundamental_quality",
            "valuation_state",
            "forecast_state",
        )
    }
    result = {
        "ticker": "TAPG",
        "status": "success",
        "verdict": {
            "ticker": "TAPG",
            "rating": "BUY",
            "confidence": 0.75,
            "entry_price_range": "1020 - 1050",
            "target_price": 1150.0,
            "stop_loss": 990.0,
            "risk_reward_ratio": 1.67,
            "execution_horizon_days": 10,
        },
        "risk_governor": {
            "status": "reject",
            "sizing_allowed": False,
            "reason_codes": ["rr_too_low"],
        },
        "metadata": {"signal_packet": packet},
    }

    _finalize_execution_decisions([result])

    finalized = result["metadata"]["signal_packet"]
    assert {key: finalized[key] for key in raw_fields} == raw_fields
    assert finalized["execution_eligible"] is False
    assert finalized["execution_rejection_reason"] == "rr_too_low"
