"""Queryable JSONL execution ledger for causal pipeline tracing."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from core.settings import settings
from utils.logger_config import logger


DEFAULT_PATH = settings.execution_ledger_path


class EventType(str, Enum):
    """Structured event categories recorded by the execution ledger."""

    BATCH_START = "BATCH_START"
    BATCH_END = "BATCH_END"
    TICKER_START = "TICKER_START"
    TICKER_END = "TICKER_END"
    STAGE_START = "STAGE_START"
    STAGE_SUCCESS = "STAGE_SUCCESS"
    STAGE_FAILURE = "STAGE_FAILURE"
    STAGE_RETRY = "STAGE_RETRY"
    STAGE_SKIP = "STAGE_SKIP"
    STAGE_PARTIAL = "STAGE_PARTIAL"
    PLANNER_DECISION = "PLANNER_DECISION"
    PROVIDER_CHECK = "PROVIDER_CHECK"
    ARTIFACT_WRITE = "ARTIFACT_WRITE"
    ARTIFACT_VALIDATE = "ARTIFACT_VALIDATE"
    RISK_CHECK = "RISK_CHECK"
    CONSISTENCY_CHECK = "CONSISTENCY_CHECK"
    ESCALATION = "ESCALATION"


class EventSeverity(str, Enum):
    """Operator severity for one ledger event."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def _event_id() -> str:
    return uuid4().hex[:8]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LedgerEvent(BaseModel):
    """Single append-only ledger event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_event_id)
    run_id: str
    ticker: str | None = None
    stage: str | None = None
    event_type: EventType
    severity: EventSeverity
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None
    attempt: int = Field(default=0, ge=0)
    timestamp: str = Field(default_factory=_utc_now_iso)


class LedgerQuery(BaseModel):
    """Filter criteria for querying ledger events."""

    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    ticker: str | None = None
    stage: str | None = None
    event_type: EventType | None = None
    severity: EventSeverity | None = None
    since: str | None = None


class RunTrace(BaseModel):
    """Derived causal trace for a run or one ticker inside a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    ticker: str | None = None
    events: list[LedgerEvent]
    total_events: int
    error_count: int
    warning_count: int
    stages_completed: list[str]
    stages_failed: list[str]
    stages_retried: list[str]
    stages_skipped: list[str]
    first_event_at: str
    last_event_at: str
    duration_seconds: float | None


class ExecutionLedger:
    """Append and query causal execution events."""

    def __init__(self, storage_path: str | Path = DEFAULT_PATH):
        self.storage_path = Path(storage_path)
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)

    def emit(self, event: LedgerEvent) -> None:
        """Append one event to JSONL without ever crashing the caller."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.storage_path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json())
                handle.write("\n")
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
            return

    def batch_start(self, run_id: str, ticker_count: int, tickers: list[str]) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=None,
                stage=None,
                event_type=EventType.BATCH_START,
                severity=EventSeverity.INFO,
                message="Batch started",
                detail={"ticker_count": ticker_count, "tickers": list(tickers)},
            )
        )

    def batch_end(
        self,
        run_id: str,
        succeeded: int,
        failed: int,
        duration_seconds: float,
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=None,
                stage=None,
                event_type=EventType.BATCH_END,
                severity=EventSeverity.INFO,
                message="Batch ended",
                detail={
                    "succeeded": succeeded,
                    "failed": failed,
                    "duration_seconds": duration_seconds,
                },
            )
        )

    def ticker_start(self, run_id: str, ticker: str) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=None,
                event_type=EventType.TICKER_START,
                severity=EventSeverity.INFO,
                message=f"Ticker {ticker} started",
            )
        )

    def ticker_end(
        self,
        run_id: str,
        ticker: str,
        status: str,
        verdict: str | None,
        duration_seconds: float,
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=None,
                event_type=EventType.TICKER_END,
                severity=(
                    EventSeverity.INFO
                    if status == "success"
                    else EventSeverity.WARNING
                ),
                message=f"Ticker {ticker} ended with {status}",
                detail={
                    "status": status,
                    "verdict": verdict,
                    "duration_seconds": duration_seconds,
                },
            )
        )

    def stage_start(
        self,
        run_id: str,
        ticker: str | None,
        stage: str,
        attempt: int = 0,
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=stage,
                event_type=EventType.STAGE_START,
                severity=EventSeverity.INFO,
                message=f"Stage {stage} started",
                attempt=attempt,
            )
        )

    def stage_success(
        self,
        run_id: str,
        ticker: str | None,
        stage: str,
        duration_ms: int,
        detail: dict | None = None,
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=stage,
                event_type=EventType.STAGE_SUCCESS,
                severity=EventSeverity.INFO,
                message=f"Stage {stage} succeeded",
                detail=detail or {},
                duration_ms=duration_ms,
            )
        )

    def stage_failure(
        self,
        run_id: str,
        ticker: str | None,
        stage: str,
        error_code: str,
        message: str,
        attempt: int,
        duration_ms: int,
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=stage,
                event_type=EventType.STAGE_FAILURE,
                severity=EventSeverity.ERROR,
                message=message,
                detail={"error_code": error_code, "message": message},
                duration_ms=duration_ms,
                attempt=attempt,
            )
        )

    def stage_retry(
        self,
        run_id: str,
        ticker: str | None,
        stage: str,
        attempt: int,
        reason: str,
        delay_seconds: int,
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=stage,
                event_type=EventType.STAGE_RETRY,
                severity=EventSeverity.WARNING,
                message=f"Stage {stage} retry scheduled",
                detail={"reason": reason, "delay_seconds": delay_seconds},
                attempt=attempt,
            )
        )

    def stage_skip(
        self,
        run_id: str,
        ticker: str | None,
        stage: str,
        reason: str,
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=stage,
                event_type=EventType.STAGE_SKIP,
                severity=EventSeverity.WARNING,
                message=f"Stage {stage} skipped",
                detail={"reason": reason},
            )
        )

    def stage_partial(
        self,
        run_id: str,
        ticker: str | None,
        stage: str,
        reason: str,
        confidence_penalty: float,
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=stage,
                event_type=EventType.STAGE_PARTIAL,
                severity=EventSeverity.WARNING,
                message=f"Stage {stage} proceeded with partial data",
                detail={
                    "reason": reason,
                    "confidence_penalty": confidence_penalty,
                },
            )
        )

    def planner_decision(
        self,
        run_id: str,
        ticker: str | None,
        stage: str,
        action: str,
        reason: str,
        attempt: int,
    ) -> None:
        severity = EventSeverity.INFO
        if action == "ABORT_BATCH":
            severity = EventSeverity.CRITICAL
        elif action in {"SKIP_TICKER", "ESCALATE"}:
            severity = EventSeverity.WARNING
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=stage,
                event_type=EventType.PLANNER_DECISION,
                severity=severity,
                message=f"Planner decided {action}",
                detail={"action": action, "reason": reason},
                attempt=attempt,
            )
        )

    def risk_check(
        self,
        run_id: str,
        ticker: str,
        original_rating: str,
        final_rating: str,
        was_modified: bool,
        violations: list[str],
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=None,
                event_type=EventType.RISK_CHECK,
                severity=(
                    EventSeverity.WARNING if was_modified else EventSeverity.INFO
                ),
                message=f"Risk check for {ticker}",
                detail={
                    "original": original_rating,
                    "final": final_rating,
                    "violations": list(violations),
                },
            )
        )

    def escalation(
        self,
        run_id: str,
        ticker: str | None,
        stage: str,
        message: str,
    ) -> None:
        self.emit(
            self._event(
                run_id=run_id,
                ticker=ticker,
                stage=stage,
                event_type=EventType.ESCALATION,
                severity=EventSeverity.CRITICAL,
                message=message,
            )
        )

    def query(self, q: LedgerQuery) -> list[LedgerEvent]:
        """Return matching events sorted by timestamp ascending."""
        events = [event for event in self._read_all() if _matches(event, q)]
        return sorted(events, key=lambda event: event.timestamp)

    def get_run_trace(self, run_id: str, ticker: str | None = None) -> RunTrace:
        """Build a derived causal trace for a run or ticker."""
        events = self.query(LedgerQuery(run_id=run_id, ticker=ticker))
        first_event_at = events[0].timestamp if events else ""
        last_event_at = events[-1].timestamp if events else ""
        duration_seconds = _duration_seconds(first_event_at, last_event_at)

        return RunTrace(
            run_id=run_id,
            ticker=ticker,
            events=events,
            total_events=len(events),
            error_count=sum(
                event.severity in {EventSeverity.ERROR, EventSeverity.CRITICAL}
                for event in events
            ),
            warning_count=sum(
                event.severity is EventSeverity.WARNING for event in events
            ),
            stages_completed=_stages_for(events, EventType.STAGE_SUCCESS),
            stages_failed=_stages_for(events, EventType.STAGE_FAILURE),
            stages_retried=_stages_for(events, EventType.STAGE_RETRY),
            stages_skipped=_stages_for(events, EventType.STAGE_SKIP),
            first_event_at=first_event_at,
            last_event_at=last_event_at,
            duration_seconds=duration_seconds,
        )

    def format_trace(self, trace: RunTrace) -> str:
        """Render a human-readable causal trace."""
        duration = (
            f"{trace.duration_seconds:.0f}s"
            if trace.duration_seconds is not None
            else "-"
        )
        lines = [
            "╔══════════════════════════════════════════╗",
            f"║  EXECUTION TRACE: {trace.run_id:<22}║",
            f"║  Ticker: {trace.ticker or 'BATCH':<31}║",
            "╚══════════════════════════════════════════╝",
            "",
            f"Duration : {duration}",
            (
                f"Events   : {trace.total_events} "
                f"({trace.error_count} errors, {trace.warning_count} warnings)"
            ),
            "",
            "STAGE SUMMARY",
            "─────────────",
            f"Completed : {_format_stage_list(trace.stages_completed)}",
            f"Failed    : {_format_stage_list(trace.stages_failed)}",
            f"Retried   : {_format_stage_list(trace.stages_retried)}",
            f"Skipped   : {_format_stage_list(trace.stages_skipped)}",
            "",
            "EVENT LOG",
            "─────────",
        ]
        for event in trace.events:
            icon = _SEVERITY_ICONS[event.severity]
            stage = event.stage or "-"
            timestamp = event.timestamp[11:19] if len(event.timestamp) >= 19 else ""
            lines.append(
                f"{icon} {timestamp} [{event.event_type.value}] "
                f"{stage:<25} {event.message[:80]}"
            )
        return "\n".join(lines)

    def diff_runs(
        self,
        run_id_a: str,
        run_id_b: str,
        ticker: str | None = None,
    ) -> dict[str, Any]:
        """Compare two run traces for the same ticker or batch."""
        trace_a = self.get_run_trace(run_id_a, ticker)
        trace_b = self.get_run_trace(run_id_b, ticker)
        stages_a = _all_stages(trace_a.events)
        stages_b = _all_stages(trace_b.events)
        duration_a = trace_a.duration_seconds or 0.0
        duration_b = trace_b.duration_seconds or 0.0

        return {
            "stages_only_in_a": sorted(stages_a - stages_b),
            "stages_only_in_b": sorted(stages_b - stages_a),
            "retries_a": _event_count(trace_a.events, EventType.STAGE_RETRY),
            "retries_b": _event_count(trace_b.events, EventType.STAGE_RETRY),
            "errors_a": trace_a.error_count,
            "errors_b": trace_b.error_count,
            "verdict_changed": _latest_verdict(trace_a.events)
            != _latest_verdict(trace_b.events),
            "duration_delta_seconds": duration_b - duration_a,
        }

    def _event(
        self,
        *,
        run_id: str,
        ticker: str | None,
        stage: str | None,
        event_type: EventType,
        severity: EventSeverity,
        message: str,
        detail: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        attempt: int = 0,
    ) -> LedgerEvent:
        return LedgerEvent(
            run_id=run_id,
            ticker=ticker,
            stage=stage,
            event_type=event_type,
            severity=severity,
            message=message,
            detail=detail or {},
            duration_ms=duration_ms,
            attempt=attempt,
        )

    def _read_all(self) -> list[LedgerEvent]:
        if not self.storage_path.exists():
            return []
        events: list[LedgerEvent] = []
        try:
            lines = self.storage_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(LedgerEvent.model_validate_json(line))
            except Exception as exc:
                logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
                continue
        return events


_SEVERITY_ICONS = {
    EventSeverity.INFO: "✅",
    EventSeverity.WARNING: "⚠️",
    EventSeverity.ERROR: "❌",
    EventSeverity.CRITICAL: "🚨",
}


def _matches(event: LedgerEvent, query: LedgerQuery) -> bool:
    if query.run_id is not None and event.run_id != query.run_id:
        return False
    if query.ticker is not None and event.ticker != query.ticker:
        return False
    if query.stage is not None and event.stage != query.stage:
        return False
    if query.event_type is not None and event.event_type is not query.event_type:
        return False
    if query.severity is not None and event.severity is not query.severity:
        return False
    if query.since is not None and not _at_or_after(event.timestamp, query.since):
        return False
    return True


def _at_or_after(timestamp: str, since: str) -> bool:
    parsed_timestamp = _parse_time(timestamp)
    parsed_since = _parse_time(since)
    if parsed_timestamp is not None and parsed_since is not None:
        return parsed_timestamp >= parsed_since
    return timestamp >= since


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_seconds(first: str, last: str) -> float | None:
    first_dt = _parse_time(first)
    last_dt = _parse_time(last)
    if first_dt is None or last_dt is None:
        return None
    return (last_dt - first_dt).total_seconds()


def _stages_for(events: list[LedgerEvent], event_type: EventType) -> list[str]:
    return _unique_ordered(
        event.stage
        for event in events
        if event.event_type is event_type and event.stage is not None
    )


def _unique_ordered(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _format_stage_list(stages: list[str]) -> str:
    return ", ".join(stages) if stages else "none"


def _all_stages(events: list[LedgerEvent]) -> set[str]:
    return {event.stage for event in events if event.stage is not None}


def _event_count(events: list[LedgerEvent], event_type: EventType) -> int:
    return sum(event.event_type is event_type for event in events)


def _latest_verdict(events: list[LedgerEvent]) -> str | None:
    for event in reversed(events):
        if event.event_type is EventType.TICKER_END:
            verdict = event.detail.get("verdict")
            return str(verdict) if verdict is not None else None
    return None


DEFAULT_LEDGER = ExecutionLedger()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query execution ledger traces.")
    parser.add_argument("--run-id", help="Run ID to format.")
    parser.add_argument("--ticker", help="Optional ticker to filter.")
    parser.add_argument(
        "--diff",
        nargs=2,
        metavar=("RUN_ID_A", "RUN_ID_B"),
        help="Diff two run IDs.",
    )
    parser.add_argument(
        "--path",
        default=str(DEFAULT_PATH),
        help="Path to execution ledger JSONL storage.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    ledger = ExecutionLedger(args.path)
    if args.diff:
        print(
            json.dumps(
                ledger.diff_runs(args.diff[0], args.diff[1], ticker=args.ticker),
                indent=2,
            )
        )
        return 0
    if not args.run_id:
        print("--run-id is required unless --diff is provided", file=sys.stderr)
        return 2
    trace = ledger.get_run_trace(args.run_id, ticker=args.ticker)
    print(ledger.format_trace(trace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
