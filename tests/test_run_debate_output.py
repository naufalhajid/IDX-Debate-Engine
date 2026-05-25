import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from run_debate import _debate_one


class FakeDebateChamber:
    async def run(self, ticker: str) -> dict:
        return {
            "ticker": ticker,
            "final_verdict": json.dumps({"ticker": ticker, "rating": "BUY", "confidence": 0.72}),
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
    assert json.loads(latest_file.read_text(encoding="utf-8")) == payload
    assert json.loads(legacy_file.read_text(encoding="utf-8")) == payload
