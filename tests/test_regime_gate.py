import pytest

import core.regime_gate as regime_gate
from core.execution_regime import resolve_execution_regime


@pytest.mark.asyncio
async def test_gate_resolves_defensive_vs_sideways_to_one_authority(
    monkeypatch,
) -> None:
    async def fake_detect() -> dict:
        return {"label": "SIDEWAYS", "confidence": 0.9467}

    monkeypatch.setattr(regime_gate, "detect_hmm_regime", fake_detect)
    result = await regime_gate.regime_gate_node(
        {
            "metadata": {
                "rule_regime_snapshot": {
                    "regime": "DEFENSIVE",
                    "volatility_regime": "HIGH",
                    "reasons": ["weekly_return_below_threshold"],
                }
            }
        }
    )

    assert result["hmm_regime"]["label"] == "SIDEWAYS"
    assert result["trend_regime"]["label"] == "SIDEWAYS"
    assert result["execution_regime"] == "DEFENSIVE"
    assert result["execution_regime_reason"] == "rule_based_defensive_override"
    assert result["trading_params"]["consensus_threshold"] == 0.80
    assert result["trading_params"]["max_position_pct"] == 0.005
    assert result["trading_params"]["max_concurrent_positions"] == 1
    assert "regime" not in result


@pytest.mark.asyncio
async def test_gate_reuses_precomputed_batch_context_without_redetection(
    monkeypatch,
) -> None:
    async def unexpected_detect() -> dict:
        raise AssertionError("HMM must not be re-detected per ticker")

    monkeypatch.setattr(regime_gate, "detect_hmm_regime", unexpected_detect)
    hmm = {"label": "SIDEWAYS", "confidence": 0.9467}
    context = resolve_execution_regime(
        rule_snapshot={
            "regime": "DEFENSIVE",
            "volatility_regime": "HIGH",
        },
        hmm_state=hmm,
    )

    result = await regime_gate.regime_gate_node(
        {"regime_context": context, "hmm_regime": hmm}
    )

    assert result["regime_context"] == context
    assert result["execution_regime"] == "DEFENSIVE"
    assert result["trend_regime"]["label"] == "SIDEWAYS"
