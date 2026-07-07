"""Arm B (single-agent + risk_governor gate) — validasi wiring TANPA LLM.

Membangun SingleAgentVerdict sintetis dan memastikan gate deterministik
(core/risk_governor.evaluate_risk) terpasang benar di atas output single-agent
yang selama ini di-bypass (lihat services/single_agent_gated.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.risk_governor import RiskDecision
from services.single_agent_analyzer import SingleAgentVerdict
from services.single_agent_gated import gate_single_agent_verdict, is_gated_buy


def _verdict(**overrides) -> SingleAgentVerdict:
    base = dict(
        ticker="BBCA",
        rating="BUY",
        confidence=0.75,
        fair_value=1300.0,
        current_price=1000.0,
        entry_price_range="960 - 1000",
        target_price=1150.0,   # >> current 1000, R/R sehat
        stop_loss=920.0,
        risk_reward_ratio=2.5,
        reasoning="clean swing setup",
        key_risks=["r1"],
        key_catalysts=["c1"],
        model_used="test-flash",
        generated_at=datetime.now(timezone.utc).isoformat(),
        run_id="test",
        data_sources=["yfinance"],
    )
    base.update(overrides)
    return SingleAgentVerdict(**base)


def test_gate_returns_riskdecision_type():
    decision = gate_single_agent_verdict(_verdict())
    assert isinstance(decision, RiskDecision)
    assert decision.ticker == "BBCA"


def test_non_buy_never_gated_buy():
    # Rating selain BUY tak pernah jadi Arm-B BUY, apa pun gate-nya.
    assert is_gated_buy(_verdict(rating="AVOID")) is False
    assert is_gated_buy(_verdict(rating="HOLD")) is False


def test_clean_high_rr_buy_not_rr_rejected():
    # R/R 2.5 yang sehat TIDAK boleh ditolak karena rr_too_low.
    decision = gate_single_agent_verdict(_verdict())
    print("\n[clean BUY] status=", decision.status, "codes=", decision.reason_codes)
    assert "rr_too_low" not in decision.reason_codes
    assert "rr_implausible" not in decision.reason_codes


def test_upside_exhausted_buy_rejected():
    # Target <= harga sekarang => tak ada upside => gate menolak => bukan Arm-B BUY.
    decision = gate_single_agent_verdict(
        _verdict(target_price=980.0, entry_price_range="960 - 1000")
    )
    assert decision.sizing_allowed is False
    assert is_gated_buy(_verdict(target_price=980.0)) is False


def test_invalid_entry_range_rejected():
    # Entry range tak terparse => invalid_entry_range => reject.
    decision = gate_single_agent_verdict(_verdict(entry_price_range="n/a"))
    assert decision.sizing_allowed is False
    assert "invalid_entry_range" in decision.reason_codes
