from typing import Any

from core.orchestrator.legacy import _record_backtest_memory


class RecordingMemory:
    """Test double that records backtest writes without touching disk."""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def record(self, outcome: Any) -> None:
        """Capture a backtest outcome passed by the orchestrator."""
        self.records.append(outcome)


def test_record_skips_insufficient_data_without_writing() -> None:
    memory = RecordingMemory()
    result = {
        "ticker": "AMRT",
        "verdict": {
            "rating": "INSUFFICIENT_DATA",
            "confidence": 0.13,
            "entry_price_range": None,
            "target_price": None,
            "stop_loss": None,
        },
    }

    _record_backtest_memory(result=result, run_id="run-test", memory=memory)

    assert memory.records == []


def test_record_writes_valid_result() -> None:
    memory = RecordingMemory()
    result = {
        "ticker": "INDF",
        "verdict": {
            "rating": "BUY",
            "confidence": 0.61,
            "entry_price_range": "6600 - 6775",
            "target_price": 7400,
            "stop_loss": 6350,
        },
    }

    _record_backtest_memory(result=result, run_id="run-test", memory=memory)

    assert len(memory.records) == 1
    record = memory.records[0]
    assert record.run_id == "run-test"
    assert record.ticker == "INDF"
    assert record.verdict_rating == "BUY"
    assert record.entry_price == 6600
    assert record.target_price == 7400
    assert record.stop_loss == 6350
