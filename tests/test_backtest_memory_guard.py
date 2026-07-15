import json
from typing import Any

from core.orchestrator import legacy
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


def test_record_skips_low_volume_trade() -> None:
    """P2.6: Recording is skipped when estimated ADT is below the screener floor."""
    memory = RecordingMemory()
    result = {
        "ticker": "MICRO",
        "verdict": {
            "rating": "BUY",
            "confidence": 0.70,
            "entry_price_range": "500 - 510",
            "target_price": 600,
            "stop_loss": 470,
            "risk_reward_ratio": 2.25,
            "execution_horizon_days": 10,
        },
        "risk_governor": {"status": "deployable", "sizing_allowed": True},
        "position_sizing": {
            "lot_count": 1,
            "shares": 100,
            "max_loss_rp": 4_000,
        },
        # avg_volume_20d=1000 shares × entry=500 → ADT Rp 500k < Rp 20B threshold
        "raw_data": {"avg_volume_20d": 1000},
    }

    _record_backtest_memory(result=result, run_id="run-vol-test", memory=memory)

    assert memory.records == [], "low-ADT trade should be skipped"


def test_record_writes_valid_result() -> None:
    memory = RecordingMemory()
    result = {
        "ticker": "INDF",
        "verdict": {
            "rating": "BUY",
            "confidence": 0.61,
            "entry_price_range": "6600 - 6775",
            "target_price": 7700,
            "stop_loss": 6350,
            "risk_reward_ratio": 2.2,
            "execution_horizon_days": 10,
        },
        "risk_governor": {"status": "deployable", "sizing_allowed": True},
        "position_sizing": {
            "lot_count": 1,
            "shares": 100,
            "max_loss_rp": 42_500,
        },
    }

    _record_backtest_memory(result=result, run_id="run-test", memory=memory)

    assert len(memory.records) == 1
    record = memory.records[0]
    assert record.run_id == "run-test"
    assert record.ticker == "INDF"
    assert record.verdict_rating == "BUY"
    assert record.entry_price == 6600
    assert record.target_price == 7700
    assert record.stop_loss == 6350


def test_record_skips_model_buy_rejected_by_risk() -> None:
    memory = RecordingMemory()
    result = {
        "ticker": "INDF",
        "verdict": {
            "rating": "BUY",
            "confidence": 0.75,
            "entry_price_range": "6600 - 6775",
            "target_price": 7700,
            "stop_loss": 6350,
            "risk_reward_ratio": 2.2,
            "execution_horizon_days": 10,
        },
        "risk_governor": {
            "status": "reject",
            "sizing_allowed": False,
            "reason_codes": ["rr_too_low"],
        },
    }

    _record_backtest_memory(
        result=result,
        run_id="run-risk-rejected",
        memory=memory,
    )

    assert memory.records == []


def test_hold_watchlist_record_carries_counterfactual_fields(
    tmp_path, monkeypatch
) -> None:
    """Envelope-rejected HOLDs must log reason_codes + hypothetical levels to
    the watchlist ledger instead of an all-null row (gate-calibration data)."""
    log_path = tmp_path / "watchlist_log.jsonl"
    monkeypatch.setattr(legacy, "_WATCHLIST_LOG_PATH", log_path)

    memory = RecordingMemory()
    hypothetical = {
        "entry_low": 970.0,
        "entry_high": 1000.0,
        "target_price": 1100.0,
        "target_basis": "Minimum R/R (Swing Cap)",
        "stop_loss": 920.0,
        "risk_reward_ratio": 1.25,
    }
    result = {
        "ticker": "TOTL",
        "verdict": {
            "rating": "HOLD",
            "confidence": 0.40,
            "entry_price_range": None,
            "target_price": None,
            "stop_loss": None,
            "risk_reward_ratio": None,
            "reason_codes": ["stop_inside_noise"],
            "hypothetical_envelope": hypothetical,
        },
    }

    _record_backtest_memory(result=result, run_id="run-watchlist", memory=memory)

    assert memory.records == []  # HOLD is never a trade record
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["ticker"] == "TOTL"
    assert record["rating"] == "HOLD"
    assert record["reason_codes"] == ["stop_inside_noise"]
    assert record["hypothetical_envelope"] == hypothetical


def test_hold_watchlist_record_defaults_counterfactual_fields_when_absent(
    tmp_path, monkeypatch
) -> None:
    """CIO-decided HOLDs (accepted envelope, no rejection) keep working: the
    counterfactual fields default to empty/None rather than raising."""
    log_path = tmp_path / "watchlist_log.jsonl"
    monkeypatch.setattr(legacy, "_WATCHLIST_LOG_PATH", log_path)

    memory = RecordingMemory()
    result = {
        "ticker": "MAPI",
        "verdict": {
            "rating": "HOLD",
            "confidence": 0.60,
            "entry_price_range": "1385 - 1410",
            "target_price": 1550.0,
            "stop_loss": 1330.0,
            "risk_reward_ratio": 1.75,
        },
    }

    _record_backtest_memory(result=result, run_id="run-watchlist-2", memory=memory)

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["reason_codes"] == []
    assert record["hypothetical_envelope"] is None
    assert record["target_price"] == 1550.0
