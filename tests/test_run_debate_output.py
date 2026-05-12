import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from run_debate import _debate_one


class FakeDebateChamber:
    async def run(self, ticker: str) -> dict:
        return {
            "ticker": ticker,
            "final_verdict": json.dumps({"ticker": ticker, "rating": "BUY", "confidence": 0.72}),
            "round_count": 1,
            "debate_history": [
                SimpleNamespace(role="bull", content="Momentum remains constructive.", round_num=1),
            ],
            "raw_data": "synthetic test data",
            "metadata": {"source": "fake-chamber"},
            "error": None,
        }


@pytest.mark.asyncio
async def test_run_debate_writes_timestamped_and_legacy_outputs(tmp_path: Path) -> None:
    timestamp = "20260512_101530"
    generated_at = "2026-05-12T10:15:30+07:00"

    ok = await _debate_one(
        ticker="BBCA",
        chamber=FakeDebateChamber(),
        output_dir=tmp_path,
        run_timestamp=timestamp,
        generated_at=generated_at,
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
    assert json.loads(latest_file.read_text(encoding="utf-8")) == payload
    assert json.loads(legacy_file.read_text(encoding="utf-8")) == payload
