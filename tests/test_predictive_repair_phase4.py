"""Regression tests for Phase 4 shadow-only gate recalibration."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
import pytest

from schemas.debate import SignalPacket
from services.debate_chamber import DebateChamber
from services.signal_packet import (
    apply_setup_outcome,
    build_raw_signal_packet,
    finalize_execution_state,
)
from services.trade_setup import build_trade_setup_snapshot


def _history(rows: int = 260, *, start: float = 900.0, end: float = 1_100.0) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-02", periods=rows)
    close = np.linspace(start, end, rows)
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


def _base_packet() -> dict[str, Any]:
    return SignalPacket(
        signal_lean="BULLISH_SETUP",
        chart_strength="BULLISH",
        volume_confirmation=True,
        forecast_state="NOT_EVALUATED",
    ).model_dump(mode="json")


def _momentum_tech(**overrides: Any) -> dict[str, Any]:
    tech: dict[str, Any] = {
        "current_price": 1_000.0,
        "ma50": 980.0,
        "sma20": 990.0,
        "ema20": 1_010.0,
        "atr14": 20.0,
        "rsi14": 55.0,
        "return_5d_pct": -0.4,
        "return_1d_pct": -0.2,
        "volume_surge_ratio": 0.9,
        "ma50_rising": True,
    }
    tech.update(overrides)
    return tech


def test_hard_momentum_rejection_requires_three_percent_breakdown_below_ema20() -> None:
    chamber = object.__new__(DebateChamber)

    envelope = chamber._compute_trade_envelope(
        1_000.0,
        1_200.0,
        _momentum_tech(return_5d_pct=-3.0),
    )

    assert envelope["rejected"] is True
    assert envelope["reason_code"] == "momentum_breakdown"

    packet = apply_setup_outcome(
        _base_packet(),
        {"status": "NO_MOMENTUM", "reason_code": "momentum_breakdown"},
    )
    assert packet["execution_eligible"] is False
    assert packet["required_entry_trigger"] is None


def test_hard_momentum_breakdown_has_no_low_rsi_bypass() -> None:
    chamber = object.__new__(DebateChamber)

    envelope = chamber._compute_trade_envelope(
        1_000.0,
        1_200.0,
        _momentum_tech(
            rsi14=35.0,
            return_5d_pct=-10.0,
            ema20=1_010.0,
            return_1d_pct=1.0,
            volume_surge_ratio=1.5,
        ),
    )

    assert envelope["rejected"] is True
    assert envelope["reason_code"] == "momentum_breakdown"
    assert envelope["hypothetical_envelope"]["momentum_recalibration_state"] == (
        "HARD_REJECT"
    )


def test_shallow_negative_pullback_waits_instead_of_hard_rejection() -> None:
    chamber = object.__new__(DebateChamber)

    envelope = chamber._compute_trade_envelope(
        1_000.0,
        1_200.0,
        _momentum_tech(return_5d_pct=-0.4),
    )

    assert not envelope.get("rejected")
    assert envelope["wait_for_confirmation"] is True
    assert envelope["momentum_recalibration_state"] == "WAIT_FOR_CONFIRMATION"
    assert "close >= EMA20" in envelope["confirmation_trigger"]
    assert "return_1d > 0" in envelope["confirmation_trigger"]
    assert "volume_ratio >= 1.0" in envelope["confirmation_trigger"]


def test_confirmed_negative_pullback_remains_shadow_only_end_to_end() -> None:
    chamber = object.__new__(DebateChamber)
    tech = _momentum_tech(
        ema20=990.0,
        return_5d_pct=-0.4,
        return_1d_pct=0.5,
        volume_surge_ratio=1.2,
    )

    envelope = chamber._compute_trade_envelope(
        1_000.0,
        1_200.0,
        tech,
    )

    assert not envelope.get("rejected")
    assert envelope.get("wait_for_confirmation") is not True
    assert envelope["momentum_recalibration_state"] == "CONFIRMED"

    snapshot = build_trade_setup_snapshot(
        ticker="TAPG",
        market_data={"history": _history()},
        current_price=1_000.0,
        execution_regime="SIDEWAYS",
        sector="consumer_staples",
        technical_indicators=tech,
        preflight={"status": "clean"},
        envelope_calculator=lambda *_args: envelope,
        signal_packet=build_raw_signal_packet(technical_indicators=tech),
    )

    assert snapshot["status"] == "SHADOW_ONLY"
    assert snapshot["reason_code"] == "shadow_only_momentum_recalibration"
    assert snapshot["debate_eligible"] is False
    assert snapshot["signal_packet"]["execution_eligible"] is False
    assert "live entry authorization remains disabled" in snapshot["reason"]
    assert "explicit approval" in snapshot["signal_packet"]["required_entry_trigger"]

    terminal = DebateChamber._terminal_trade_setup_result(
        {
            "ticker": "TAPG",
            "current_price": 1_000.0,
            "metadata": {},
        },
        snapshot,
    )
    verdict = json.loads(terminal["final_verdict"])
    assert verdict["rating"] == "HOLD"
    assert verdict["entry_price_range"] is None
    assert verdict["target_price"] is None
    assert verdict["stop_loss"] is None
    assert terminal["metadata"]["llm_calls"] == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"ema20": 1_010.0, "return_1d_pct": 0.5, "volume_surge_ratio": 1.2},
        {"ema20": 990.0, "return_1d_pct": 0.0, "volume_surge_ratio": 1.2},
        {"ema20": 990.0, "return_1d_pct": 0.5, "volume_surge_ratio": 0.99},
    ],
)
def test_each_pullback_confirmation_condition_is_required(
    overrides: dict[str, Any],
) -> None:
    chamber = object.__new__(DebateChamber)

    envelope = chamber._compute_trade_envelope(
        1_000.0,
        1_200.0,
        _momentum_tech(**overrides),
    )

    assert not envelope.get("rejected")
    assert envelope["wait_for_confirmation"] is True


def test_three_percent_decline_above_ema_can_confirm_without_hard_reject() -> None:
    chamber = object.__new__(DebateChamber)

    envelope = chamber._compute_trade_envelope(
        1_000.0,
        1_200.0,
        _momentum_tech(
            ema20=990.0,
            return_5d_pct=-3.0,
            return_1d_pct=0.5,
            volume_surge_ratio=1.2,
        ),
    )

    assert not envelope.get("rejected")
    assert envelope.get("wait_for_confirmation") is not True
    assert envelope["momentum_recalibration_state"] == "CONFIRMED"


def test_shallow_pullback_reaches_signal_packet_as_neutral_chart_wait() -> None:
    chamber = object.__new__(DebateChamber)
    tech = _momentum_tech(return_5d_pct=-0.4)
    raw_packet = build_raw_signal_packet(
        technical_indicators=tech,
        candidate_context={"Composite Score": 72.0},
    )
    envelope = chamber._compute_trade_envelope(1_000.0, 1_200.0, tech)

    snapshot = build_trade_setup_snapshot(
        ticker="TAPG",
        market_data={"history": _history()},
        current_price=1_000.0,
        execution_regime="SIDEWAYS",
        sector="consumer_staples",
        technical_indicators=tech,
        preflight={"status": "clean"},
        envelope_calculator=lambda *_args: envelope,
        signal_packet=raw_packet,
    )

    assert snapshot["status"] == "WAIT_FOR_CONFIRMATION"
    assert snapshot["debate_eligible"] is False
    assert snapshot["signal_packet"]["signal_lean"] == "BULLISH_SETUP"
    assert snapshot["signal_packet"]["chart_strength"] == "NEUTRAL"
    assert snapshot["signal_packet"]["execution_eligible"] is False
    assert "close >= EMA20" in snapshot["signal_packet"]["required_entry_trigger"]


def test_extended_ma50_candidate_is_measured_but_not_execution_authorized() -> None:
    chamber = object.__new__(DebateChamber)
    tech = {
        "current_price": 1_070.0,
        "ma50": 1_000.0,
        "sma20": 1_040.0,
        "ema20": 1_050.0,
        "atr14": 20.0,
        "rsi14": 65.0,
        "return_5d_pct": 2.0,
        "return_1d_pct": 0.8,
        "volume_surge_ratio": 1.5,
        "ma50_rising": True,
    }
    raw_packet = build_raw_signal_packet(technical_indicators=tech)

    snapshot = build_trade_setup_snapshot(
        ticker="TAPG",
        market_data={"history": _history(end=1_070.0)},
        current_price=1_070.0,
        execution_regime="SIDEWAYS",
        sector="mining",
        technical_indicators=tech,
        preflight={"status": "clean"},
        envelope_calculator=lambda price, fair_value, envelope_tech: (
            chamber._compute_trade_envelope(price, fair_value, envelope_tech)
        ),
        signal_packet=raw_packet,
    )

    assert raw_packet["chart_strength"] == "BULLISH"
    assert snapshot["status"] == "WAIT_FOR_PULLBACK"
    assert snapshot["debate_eligible"] is False
    assert snapshot["envelope"]["entry_high"] == pytest.approx(1_020.0)
    assert snapshot["signal_packet"]["execution_eligible"] is False


def test_extended_ma50_shadow_measurement_does_not_inherit_standard_entry_bounds() -> None:
    packet = build_raw_signal_packet(
        technical_indicators={
            "current_price": 1_070.0,
            "ma50": 1_000.0,
            "ema20": 1_080.0,
            "rsi14": 40.0,
            "return_5d_pct": -0.4,
            "volume_surge_ratio": 1.5,
            "ma50_rising": True,
        }
    )

    assert packet["chart_strength"] == "BULLISH"
    assert packet["signal_lean"] == "BULLISH_SETUP"


@pytest.mark.parametrize(
    "overrides",
    [
        {"current_price": 1_090.0},
        {"volume_surge_ratio": 3.0},
        {"ma50_rising": False},
        {"rsi14": 71.0},
    ],
)
def test_extended_ma50_measurement_requires_all_shadow_conditions(
    overrides: dict[str, Any],
) -> None:
    tech: dict[str, Any] = {
        "current_price": 1_070.0,
        "ma50": 1_000.0,
        "ema20": 1_050.0,
        "rsi14": 65.0,
        "return_5d_pct": 2.0,
        "volume_surge_ratio": 1.5,
        "ma50_rising": True,
    }
    tech.update(overrides)

    packet = build_raw_signal_packet(technical_indicators=tech)

    assert packet["chart_strength"] == "NEUTRAL"


def test_technical_snapshot_exposes_return_1d_and_rising_ma50() -> None:
    indicators = DebateChamber._compute_technical_indicators(_history())

    assert indicators is not None
    assert indicators["return_1d_pct"] > 0
    assert indicators["ma50_rising"] is True


def test_rr_rejection_surfaces_entry_max_without_changing_execution_status() -> None:
    snapshot = {
        "status": "RR_TOO_LOW",
        "reason_code": "rr_too_low",
        "hypothetical_envelope": {
            "entry_high": 610.0,
            "target_price": 670.0,
            "stop_loss": 570.0,
            "risk_reward_ratio": 1.5,
            "required_rr": 2.0,
        },
    }

    packet = apply_setup_outcome(_base_packet(), snapshot)

    assert packet["execution_eligible"] is False
    assert packet["execution_rejection_reason"] == "rr_too_low"
    assert "R/R-only" in packet["required_entry_trigger"]
    assert "at or below Rp 600" in packet["required_entry_trigger"]
    assert "theoretical maximum Rp 603.33" in packet["required_entry_trigger"]
    assert "2.00x" in packet["required_entry_trigger"]
    assert "all execution gates" in packet["required_entry_trigger"]
    assert 610.0 > (670.0 + 2.0 * 570.0) / 3.0

    finalized = finalize_execution_state(
        packet,
        actionable=False,
        reason_codes=["rr_too_low"],
    )
    assert finalized["required_entry_trigger"] == packet["required_entry_trigger"]
    assert finalized["execution_eligible"] is False


@pytest.mark.parametrize(
    ("ticker", "proposed_entry", "target", "stop", "expected_tick"),
    [
        ("ELSA", 610.0, 670.0, 570.0, "600"),
        ("AKRA", 1_340.0, 1_375.0, 1_220.0, "1,270"),
        ("ERAA", 354.0, 366.0, 332.0, "342"),
        ("BMRI", 4_160.0, 4_570.0, 3_820.0, "4,070"),
        ("LSIP", 1_285.0, 1_335.0, 1_200.0, "1,245"),
    ],
)
def test_july15_low_rr_fixtures_remain_rejected_with_actionable_threshold(
    ticker: str,
    proposed_entry: float,
    target: float,
    stop: float,
    expected_tick: str,
) -> None:
    theoretical_max = (target + 2.0 * stop) / 3.0
    snapshot = {
        "ticker": ticker,
        "status": "RR_TOO_LOW",
        "reason_code": "rr_too_low",
        "debate_eligible": False,
        "hypothetical_envelope": {
            "entry_high": proposed_entry,
            "target_price": target,
            "stop_loss": stop,
            "required_rr": 2.0,
        },
    }

    packet = apply_setup_outcome(_base_packet(), snapshot)

    assert proposed_entry > theoretical_max
    assert snapshot["debate_eligible"] is False
    assert packet["execution_eligible"] is False
    assert packet["execution_rejection_reason"] == "rr_too_low"
    assert f"at or below Rp {expected_tick}" in packet["required_entry_trigger"]
    assert "all execution gates must be rechecked" in packet["required_entry_trigger"]


def test_rr_entry_trigger_uses_dynamic_countertrend_requirement() -> None:
    snapshot = {
        "status": "RR_TOO_LOW",
        "reason_code": "rr_too_low",
        "hypothetical_envelope": {
            "target_price": 670.0,
            "stop_loss": 570.0,
            "required_rr": 2.5,
        },
    }

    packet = apply_setup_outcome(_base_packet(), snapshot)

    assert "2.50x" in packet["required_entry_trigger"]
    assert "theoretical maximum Rp 598.57" in packet["required_entry_trigger"]
    assert "at or below Rp 595" in packet["required_entry_trigger"]
    assert packet["execution_eligible"] is False


@pytest.mark.parametrize(
    "hypothetical",
    [
        {},
        {"target_price": 670.0, "stop_loss": 570.0},
        {"target_price": 500.0, "stop_loss": 570.0, "required_rr": 2.0},
    ],
)
def test_rr_entry_trigger_fails_closed_for_invalid_geometry(
    hypothetical: dict[str, Any],
) -> None:
    snapshot = {
        "status": "RR_TOO_LOW",
        "reason_code": "rr_too_low",
        "hypothetical_envelope": hypothetical,
    }

    packet = apply_setup_outcome(_base_packet(), snapshot)

    assert packet["required_entry_trigger"] is None
    assert packet["execution_eligible"] is False
