from __future__ import annotations

import json
from pathlib import Path

from core.execution_ledger import (
    EventSeverity,
    EventType,
    ExecutionLedger,
    LedgerEvent,
    LedgerQuery,
)


def _ledger(tmp_path: Path) -> ExecutionLedger:
    return ExecutionLedger(tmp_path / "execution_ledger.jsonl")


def _read_events(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sample_event(**overrides) -> LedgerEvent:
    payload = {
        "run_id": "run-1",
        "ticker": "BBCA",
        "stage": "DEBATE",
        "event_type": EventType.STAGE_START,
        "severity": EventSeverity.INFO,
        "message": "started",
        "detail": {},
        "attempt": 0,
    }
    payload.update(overrides)
    return LedgerEvent(**payload)


def test_emit_writes_one_jsonl_line_per_event(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.emit(_sample_event())

    rows = _read_events(tmp_path / "execution_ledger.jsonl")
    assert len(rows) == 1
    assert rows[0]["event_type"] == EventType.STAGE_START.value


def test_batch_start_and_end_emit_correct_event_types(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.batch_start("run-1", ticker_count=2, tickers=["BBCA", "BBRI"])
    ledger.batch_end("run-1", succeeded=1, failed=1, duration_seconds=12.5)

    rows = _read_events(tmp_path / "execution_ledger.jsonl")
    assert [row["event_type"] for row in rows] == [
        EventType.BATCH_START.value,
        EventType.BATCH_END.value,
    ]
    assert rows[0]["detail"] == {"ticker_count": 2, "tickers": ["BBCA", "BBRI"]}
    assert rows[1]["detail"]["duration_seconds"] == 12.5


def test_stage_success_emits_duration_ms(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.stage_success("run-1", "BBCA", "DEBATE", duration_ms=123)

    event = ledger.query(LedgerQuery(run_id="run-1"))[0]
    assert event.event_type is EventType.STAGE_SUCCESS
    assert event.duration_ms == 123


def test_stage_failure_emits_error_severity(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.stage_failure(
        "run-1",
        "BBCA",
        "DEBATE",
        error_code="TIMEOUT",
        message="timeout",
        attempt=1,
        duration_ms=5000,
    )

    event = ledger.query(LedgerQuery(run_id="run-1"))[0]
    assert event.event_type is EventType.STAGE_FAILURE
    assert event.severity is EventSeverity.ERROR
    assert event.detail == {"error_code": "TIMEOUT", "message": "timeout"}


def test_stage_retry_emits_warning_severity(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.stage_retry(
        "run-1",
        "BBCA",
        "DEBATE",
        attempt=1,
        reason="timeout",
        delay_seconds=30,
    )

    event = ledger.query(LedgerQuery(run_id="run-1"))[0]
    assert event.event_type is EventType.STAGE_RETRY
    assert event.severity is EventSeverity.WARNING
    assert event.detail["delay_seconds"] == 30


def test_stage_skip_emits_correct_reason(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.stage_skip("run-1", "BBCA", "DEBATE", reason="max retries")

    event = ledger.query(LedgerQuery(run_id="run-1"))[0]
    assert event.event_type is EventType.STAGE_SKIP
    assert event.detail["reason"] == "max retries"


def test_stage_partial_emits_confidence_penalty(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.stage_partial(
        "run-1",
        "BBCA",
        "SENTIMENT_FETCH",
        reason="sentiment unavailable",
        confidence_penalty=0.15,
    )

    event = ledger.query(LedgerQuery(run_id="run-1"))[0]
    assert event.event_type is EventType.STAGE_PARTIAL
    assert event.detail["confidence_penalty"] == 0.15


def test_planner_decision_emits_critical_for_abort_batch(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.planner_decision(
        "run-1",
        None,
        "PROVIDER_HEALTH",
        action="ABORT_BATCH",
        reason="providers down",
        attempt=0,
    )

    event = ledger.query(LedgerQuery(run_id="run-1"))[0]
    assert event.event_type is EventType.PLANNER_DECISION
    assert event.severity is EventSeverity.CRITICAL


def test_risk_check_emits_warning_when_modified(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.risk_check(
        "run-1",
        "BBCA",
        original_rating="BUY",
        final_rating="HOLD",
        was_modified=True,
        violations=["bad rr"],
    )

    event = ledger.query(LedgerQuery(run_id="run-1"))[0]
    assert event.event_type is EventType.RISK_CHECK
    assert event.severity is EventSeverity.WARNING
    assert event.detail["original"] == "BUY"
    assert event.detail["final"] == "HOLD"


def test_escalation_emits_critical_severity(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.escalation("run-1", "BBCA", "ARTIFACT_WRITE", "lost report")

    event = ledger.query(LedgerQuery(run_id="run-1"))[0]
    assert event.event_type is EventType.ESCALATION
    assert event.severity is EventSeverity.CRITICAL


def test_query_filters_by_run_id_correctly(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.emit(_sample_event(run_id="run-1"))
    ledger.emit(_sample_event(run_id="run-2"))

    events = ledger.query(LedgerQuery(run_id="run-2"))

    assert len(events) == 1
    assert events[0].run_id == "run-2"


def test_query_filters_by_ticker_correctly(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.ticker_start("run-1", "BBCA")
    ledger.ticker_start("run-1", "BBRI")

    events = ledger.query(LedgerQuery(run_id="run-1", ticker="BBRI"))

    assert len(events) == 1
    assert events[0].ticker == "BBRI"


def test_query_filters_by_severity_correctly(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.emit(_sample_event(severity=EventSeverity.INFO))
    ledger.emit(_sample_event(severity=EventSeverity.ERROR))

    events = ledger.query(LedgerQuery(severity=EventSeverity.ERROR))

    assert len(events) == 1
    assert events[0].severity is EventSeverity.ERROR


def test_get_run_trace_stages_completed_populated(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.stage_success("run-1", "BBCA", "DEBATE", duration_ms=100)

    trace = ledger.get_run_trace("run-1", ticker="BBCA")

    assert trace.stages_completed == ["DEBATE"]
    assert trace.total_events == 1


def test_get_run_trace_error_count_counts_error_and_critical(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.stage_failure("run-1", "BBCA", "DEBATE", "TIMEOUT", "timeout", 0, 100)
    ledger.escalation("run-1", "BBCA", "ARTIFACT_WRITE", "lost report")

    trace = ledger.get_run_trace("run-1", ticker="BBCA")

    assert trace.error_count == 2


def test_get_run_trace_duration_seconds_calculated(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.emit(
        _sample_event(
            timestamp="2026-05-14T00:00:00+00:00",
            event_type=EventType.TICKER_START,
        )
    )
    ledger.emit(
        _sample_event(
            timestamp="2026-05-14T00:00:05+00:00",
            event_type=EventType.TICKER_END,
        )
    )

    trace = ledger.get_run_trace("run-1", ticker="BBCA")

    assert trace.duration_seconds == 5.0
    assert trace.first_event_at == "2026-05-14T00:00:00+00:00"
    assert trace.last_event_at == "2026-05-14T00:00:05+00:00"


def test_format_trace_contains_execution_trace_header(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.stage_success("run-1", "BBCA", "DEBATE", duration_ms=100)

    output = ledger.format_trace(ledger.get_run_trace("run-1", "BBCA"))

    assert "EXECUTION TRACE" in output
    assert "STAGE SUMMARY" in output


def test_diff_runs_detects_stages_only_in_one_run(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.stage_success("run-a", "BBCA", "FUNDAMENTAL_FETCH", duration_ms=100)
    ledger.stage_success("run-b", "BBCA", "TECHNICAL_FETCH", duration_ms=100)

    diff = ledger.diff_runs("run-a", "run-b", ticker="BBCA")

    assert diff["stages_only_in_a"] == ["FUNDAMENTAL_FETCH"]
    assert diff["stages_only_in_b"] == ["TECHNICAL_FETCH"]


def test_emit_failure_bad_path_does_not_raise_exception(tmp_path: Path) -> None:
    bad_parent = tmp_path / "not_a_directory"
    bad_parent.write_text("file", encoding="utf-8")
    ledger = ExecutionLedger(bad_parent / "ledger.jsonl")

    ledger.emit(_sample_event())


def test_multiple_tickers_in_same_run_are_queryable_independently(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    ledger.ticker_start("run-1", "BBCA")
    ledger.stage_success("run-1", "BBCA", "DEBATE", duration_ms=100)
    ledger.ticker_start("run-1", "BBRI")
    ledger.stage_failure("run-1", "BBRI", "DEBATE", "TIMEOUT", "timeout", 0, 100)

    bbca_trace = ledger.get_run_trace("run-1", ticker="BBCA")
    bbri_trace = ledger.get_run_trace("run-1", ticker="BBRI")

    assert bbca_trace.total_events == 2
    assert bbca_trace.error_count == 0
    assert bbri_trace.total_events == 2
    assert bbri_trace.error_count == 1
