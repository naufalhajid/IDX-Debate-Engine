from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from services.trade_setup import build_trade_setup_snapshot


def _history(rows: int) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=rows, freq="B")
    close = pd.Series([100.0 + (i * 0.2) for i in range(rows)], index=index)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=index,
    )


def _accepted_envelope(
    current_price: float,
    fair_value: float | None,
    tech: dict,
) -> dict:
    del fair_value, tech
    return {
        "entry_low": current_price * 0.97,
        "entry_high": current_price,
        "entry_mid": current_price * 0.985,
        "target_price": current_price * 1.10,
        "target_basis": "test",
        "stop_loss": current_price * 0.94,
        "risk_reward_ratio": 2.0,
        "atr14": 2.0,
        "stop_near_noise": False,
    }


def _snapshot(
    *,
    rows: int,
    envelope_calculator=_accepted_envelope,
    market_data: dict | None = None,
) -> dict:
    payload = {"history": _history(rows), "info": {}}
    payload.update(market_data or {})
    return build_trade_setup_snapshot(
        ticker="TEST",
        market_data=payload,
        current_price=100.0,
        execution_regime="SIDEWAYS",
        sector="bank",
        technical_indicators={
            "current_price": 100.0,
            "atr14": 2.0,
            "low_20d": 95.0,
            "rsi14": 55.0,
            "return_5d_pct": 1.0,
        },
        preflight={"status": "clean"},
        envelope_calculator=envelope_calculator,
    )


def test_three_bar_recent_ipo_is_insufficient_data() -> None:
    snapshot = _snapshot(
        rows=3,
        market_data={
            "info": {
                "firstTradeDateEpochUtc": int(
                    datetime.now(timezone.utc).timestamp()
                )
            }
        },
    )

    assert snapshot["status"] == "INSUFFICIENT_DATA"
    assert snapshot["reason_code"] == "recent_listing_short_history"
    assert snapshot["history"]["complete_bars"] == 3
    assert snapshot["recent_listing"] is True
    assert snapshot["debate_eligible"] is False


def test_one_bar_without_listing_proof_is_not_called_ipo() -> None:
    snapshot = _snapshot(rows=1)

    assert snapshot["status"] == "INSUFFICIENT_DATA"
    assert snapshot["reason_code"] == "insufficient_short_history"
    assert snapshot["recent_listing"] is False


def test_provider_error_is_distinct_from_recent_listing() -> None:
    snapshot = build_trade_setup_snapshot(
        ticker="TEST",
        market_data={"history": None, "history_error": "TimeoutError"},
        current_price=100.0,
        execution_regime="SIDEWAYS",
        sector="bank",
        technical_indicators=None,
        preflight={"status": "skip", "reason": "no_technical_data"},
        envelope_calculator=_accepted_envelope,
    )

    assert snapshot["status"] == "INSUFFICIENT_DATA"
    assert snapshot["reason_code"] == "provider_history_error"
    assert snapshot["recent_listing"] is False


def test_empty_history_without_listing_proof_is_provider_unavailable() -> None:
    snapshot = build_trade_setup_snapshot(
        ticker="TEST",
        market_data={"history": None},
        current_price=100.0,
        execution_regime="SIDEWAYS",
        sector="bank",
        technical_indicators=None,
        preflight={"status": "skip", "reason": "no_technical_data"},
        envelope_calculator=_accepted_envelope,
    )

    assert snapshot["status"] == "INSUFFICIENT_DATA"
    assert snapshot["reason_code"] == "provider_history_unavailable"
    assert snapshot["recent_listing"] is False


def test_empty_history_with_recent_listing_is_classified_as_recent_listing() -> None:
    snapshot = build_trade_setup_snapshot(
        ticker="TEST",
        market_data={
            "history": None,
            "info": {
                "firstTradeDateEpochUtc": int(
                    datetime.now(timezone.utc).timestamp()
                )
            },
        },
        current_price=100.0,
        execution_regime="SIDEWAYS",
        sector="bank",
        technical_indicators=None,
        preflight={"status": "skip", "reason": "no_technical_data"},
        envelope_calculator=_accepted_envelope,
    )

    assert snapshot["status"] == "INSUFFICIENT_DATA"
    assert snapshot["reason_code"] == "recent_listing_short_history"
    assert snapshot["recent_listing"] is True


@pytest.mark.parametrize("rows", [60, 120, 249])
def test_sixty_to_249_bars_fail_ma200_execution_contract(rows: int) -> None:
    snapshot = _snapshot(rows=rows)

    assert snapshot["status"] == "INSUFFICIENT_DATA"
    assert snapshot["reason_code"] == "insufficient_ma200_history"
    assert snapshot["history"]["complete_bars"] == rows


@pytest.mark.parametrize(
    ("envelope_reason", "expected_status"),
    [
        ("no_momentum_confirmation", "NO_MOMENTUM"),
        ("rr_too_low", "RR_TOO_LOW"),
        ("target_collapsed", "RR_TOO_LOW"),
        ("stop_inside_noise", "STOP_INSIDE_NOISE"),
    ],
)
def test_envelope_reason_maps_to_explicit_terminal_status(
    envelope_reason: str,
    expected_status: str,
) -> None:
    def rejected(current_price, fair_value, tech):
        del current_price, fair_value, tech
        return {
            "rejected": True,
            "reason_code": envelope_reason,
            "reason": envelope_reason,
            "hypothetical_envelope": {
                "entry_low": 95.0,
                "entry_high": 100.0,
                "target_price": 105.0,
                "stop_loss": 90.0,
                "risk_reward_ratio": 0.5,
            },
        }

    snapshot = _snapshot(rows=250, envelope_calculator=rejected)

    assert snapshot["status"] == expected_status
    assert snapshot["reason_code"] == envelope_reason
    assert snapshot["debate_eligible"] is False
    assert snapshot["hypothetical_envelope"]["risk_reward_ratio"] == 0.5


def test_only_executable_status_is_debate_eligible() -> None:
    snapshot = _snapshot(rows=250)

    assert snapshot["status"] == "EXECUTABLE"
    assert snapshot["debate_eligible"] is True
    assert snapshot["envelope"]["entry_high"] == 100.0


def test_price_above_entry_range_is_wait_for_pullback() -> None:
    def pullback(current_price, fair_value, tech):
        del fair_value, tech
        return {
            "entry_low": current_price * 0.90,
            "entry_high": current_price * 0.95,
            "entry_mid": current_price * 0.925,
            "target_price": current_price * 1.05,
            "target_basis": "test",
            "stop_loss": current_price * 0.85,
            "risk_reward_ratio": 1.0,
            "atr14": 2.0,
            "stop_near_noise": False,
        }

    snapshot = _snapshot(rows=250, envelope_calculator=pullback)

    assert snapshot["status"] == "WAIT_FOR_PULLBACK"
    assert snapshot["reason_code"] == "price_above_entry_range"
    assert snapshot["debate_eligible"] is False


def test_preflight_noise_reject_has_priority_over_short_history() -> None:
    snapshot = build_trade_setup_snapshot(
        ticker="TEST",
        market_data={"history": _history(3)},
        current_price=100.0,
        execution_regime="SIDEWAYS",
        sector="bank",
        technical_indicators={},
        preflight={"status": "reject", "reason": "preflight_noise"},
        envelope_calculator=_accepted_envelope,
    )

    assert snapshot["status"] == "STOP_INSIDE_NOISE"
    assert snapshot["reason_code"] == "preflight_noise_reject"
