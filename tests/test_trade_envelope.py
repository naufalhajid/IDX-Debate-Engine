from __future__ import annotations

import pytest

from core.trade_envelope import TradeEnvelopeService
from services.debate_chamber import DebateChamber


def _chamber() -> DebateChamber:
    return object.__new__(DebateChamber)


@pytest.mark.parametrize(
    ("current_price", "fair_value", "tech"),
    [
        (
            127.0,
            423.0,
            {"ma50": 140.0, "sma20": 133.0, "atr14": 5.0},
        ),
        (
            127.0,
            423.0,
            {"ma50": 140.0, "sma20": 133.0, "atr14": 5.0, "high_20d": 130.0},
        ),
        (
            1000.0,
            1100.0,
            {"regime": "NORMAL", "atr14": 600.0, "sma20": 500.0},
        ),
        (
            1000.0,
            1100.0,
            {
                "ma50": 1000.0,
                "sma20": 980.0,
                "atr14": 20.0,
                "rsi14": 55.0,
                "return_5d_pct": -3.0,
            },
        ),
    ],
)
def test_debate_chamber_trade_envelope_wrapper_matches_service(
    current_price: float,
    fair_value: float,
    tech: dict,
) -> None:
    chamber = _chamber()

    assert chamber._compute_trade_envelope(
        current_price,
        fair_value,
        dict(tech),
    ) == TradeEnvelopeService.compute(
        current_price=current_price,
        fair_value=fair_value,
        tech=dict(tech),
    )


def test_trade_envelope_service_preserves_sector_swing_cap() -> None:
    tech = {"sma20": 10000.0}

    default = TradeEnvelopeService.compute(10000.0, 0.0, dict(tech))
    mining = TradeEnvelopeService.compute(
        10000.0,
        0.0,
        {**tech, "sector": "mining"},
    )

    assert mining["target_price"] > default["target_price"]
    assert mining["target_price"] <= mining["entry_high"] * 1.21
    assert "(Swing Cap)" in default["target_basis"]


def test_trade_envelope_service_rr_rejection_keeps_hypothetical_levels() -> None:
    envelope = TradeEnvelopeService.compute(
        current_price=127.0,
        fair_value=423.0,
        tech={"ma50": 140.0, "sma20": 133.0, "atr14": 5.0, "high_20d": 130.0},
    )

    assert envelope["rejected"] is True
    assert envelope["reason_code"] == "rr_too_low"
    hypo = envelope["hypothetical_envelope"]
    assert hypo["target_price"] == 130
    assert hypo["stop_loss"] < hypo["entry_low"] < hypo["entry_high"]
    assert hypo["risk_reward_ratio"] < 1.4


def test_trade_envelope_service_accepted_setup_has_no_hypothetical_envelope() -> None:
    envelope = TradeEnvelopeService.compute(
        current_price=1000.0,
        fair_value=1100.0,
        tech={
            "ma50": 1000.0,
            "sma20": 980.0,
            "atr14": 20.0,
            "rsi14": 55.0,
            "return_5d_pct": 1.5,
        },
    )

    assert not envelope.get("rejected")
    assert "hypothetical_envelope" not in envelope
