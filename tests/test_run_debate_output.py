import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import run_debate
from run_debate import _attach_risk_governor, _debate_one, _ledger_call


class FakeDebateChamber:
    market_regime = {
        "regime": "DEFENSIVE",
        "volatility_regime": "HIGH",
        "reasons": ["market_drawdown"],
    }
    hmm_regime = {"label": "SIDEWAYS", "confidence": 0.94}
    regime_context = {
        "rule_based_regime": "DEFENSIVE",
        "rule_based_reasons": ["market_drawdown"],
        "trend_regime": {
            "label": "SIDEWAYS",
            "confidence": 0.94,
            "source": "hmm",
        },
        "volatility_regime": "HIGH",
        "execution_regime": "DEFENSIVE",
        "execution_regime_reason": "rule_based_defensive_override",
        "execution_policy_profile": "BEAR_STRESS",
        "execution_params": {"consensus_threshold": 0.8},
        "operational_params": {"top_n_selection": 0},
    }

    async def run(self, ticker: str, current_price: float = 0.0, sector: str = "") -> dict:
        return {
            "ticker": ticker,
            "final_verdict": json.dumps(
                {"ticker": ticker, "rating": "BUY", "confidence": 0.72}
            ),
            "round_count": 1,
            "debate_history": [
                SimpleNamespace(
                    role="bull",
                    content="Momentum remains constructive.",
                    round_num=1,
                    position="BUY",
                    confidence=0.72,
                ),
            ],
            "raw_data": "synthetic test data",
            "metadata": {"source": "fake-chamber"},
            "consensus_reached": True,
            "consensus_method": "voting",
            "dissenting_agents": ["bear"],
            "agent_votes": [
                {
                    "agent": "bull",
                    "position": "BUY",
                    "confidence": 0.72,
                    "supporting_winner": True,
                },
                {
                    "agent": "bear",
                    "position": "AVOID",
                    "confidence": 0.41,
                    "supporting_winner": False,
                },
            ],
            "consensus_winner": {
                "agent": "bull",
                "position": "BUY",
                "confidence": 0.72,
            },
            "error": None,
        }


def test_run_debate_writes_timestamped_and_legacy_outputs(tmp_path: Path) -> None:
    timestamp = "20260512_101530"
    generated_at = "2026-05-12T10:15:30+07:00"

    ok = asyncio.run(
        _debate_one(
            ticker="BBCA",
            chamber=FakeDebateChamber(),
            output_dir=tmp_path,
            run_timestamp=timestamp,
            generated_at=generated_at,
        )
    )

    assert ok is True

    versioned_file = tmp_path / "BBCA" / f"v{timestamp}" / "BBCA_debate.json"
    latest_file = tmp_path / "BBCA" / "latest_debate.json"
    legacy_file = tmp_path / "BBCA_debate.json"

    assert versioned_file.exists()
    assert latest_file.exists()
    assert legacy_file.exists()

    payload = json.loads(versioned_file.read_text(encoding="utf-8"))
    assert payload["ticker"] == "BBCA"
    assert payload["metadata"]["source"] == "fake-chamber"
    assert payload["metadata"]["batch_timestamp"] == timestamp
    assert payload["metadata"]["run_timestamp"] == timestamp
    assert payload["metadata"]["generated_at"] == generated_at
    assert payload["metadata"]["versioned_output"] is True
    assert payload["consensus_reached"] is True
    assert payload["consensus_method"] == "voting"
    assert payload["dissenting_agents"] == ["bear"]
    assert payload["agent_votes"][0]["agent"] == "bull"
    assert payload["consensus_winner"]["agent"] == "bull"
    assert payload["debate_history"][0]["position"] == "BUY"
    assert payload["debate_history"][0]["confidence"] == 0.72
    assert payload["rule_regime_snapshot"]["regime"] == "DEFENSIVE"
    assert payload["regime_context"] == FakeDebateChamber.regime_context
    assert payload["hmm_regime"]["label"] == "SIDEWAYS"
    assert payload["trend_regime"]["source"] == "hmm"
    assert payload["volatility_regime"] == "HIGH"
    assert payload["execution_regime"] == "DEFENSIVE"
    assert (
        payload["execution_regime_reason"] == "rule_based_defensive_override"
    )
    assert payload["trading_params"] == {"consensus_threshold": 0.8}
    assert "regime" not in payload["metadata"]
    assert json.loads(latest_file.read_text(encoding="utf-8")) == payload
    assert json.loads(legacy_file.read_text(encoding="utf-8")) == payload


def test_run_debate_ledger_call_accepts_action_payload() -> None:
    captured: dict[str, str] = {}

    def fake_planner_decision(**kwargs):
        captured.update(kwargs)

    _ledger_call(
        "planner decision",
        fake_planner_decision,
        action="RETRY",
        stage="DEBATE",
    )

    assert captured == {"action": "RETRY", "stage": "DEBATE"}


def test_standalone_risk_entry_uses_canonical_execution_regime(
    monkeypatch,
) -> None:
    captured: dict = {}

    def fake_annotate_risk(entry: dict):
        captured.update(entry)
        decision = SimpleNamespace(
            ticker="BBCA",
            status="HOLD",
            reason_codes=["defensive_regime"],
            sizing_allowed=False,
            model_dump=lambda: {"status": "HOLD"},
        )
        entry["risk_governor"] = decision.model_dump()
        return decision

    monkeypatch.setattr(run_debate, "annotate_risk", fake_annotate_risk)
    context = FakeDebateChamber.regime_context
    report = {
        "verdict": {"ticker": "BBCA", "rating": "BUY"},
        "rule_regime_snapshot": FakeDebateChamber.market_regime,
        "regime_context": context,
        "hmm_regime": FakeDebateChamber.hmm_regime,
        "trend_regime": context["trend_regime"],
        "volatility_regime": "HIGH",
        "execution_regime": "DEFENSIVE",
        "execution_regime_reason": "rule_based_defensive_override",
        "trading_params": context["execution_params"],
    }
    result = {"metadata": {"source": "test"}}

    _attach_risk_governor(
        ticker="BBCA", run_id="20260713_000000", report=report, result=result
    )

    assert captured["execution_regime"] == "DEFENSIVE"
    assert captured["regime_context"] == context
    assert captured["hmm_regime"]["label"] == "SIDEWAYS"
    assert captured["rule_regime_snapshot"]["regime"] == "DEFENSIVE"
    assert captured["risk_context"]["execution_regime"] == "DEFENSIVE"
    assert "market_regime" not in captured
