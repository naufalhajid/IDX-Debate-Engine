from __future__ import annotations

import json

import pytest

from core.adaptive_planner import (
    MAX_RETRY_ATTEMPTS,
    PARTIAL_DATA_CONFIDENCE_PENALTY,
    RETRY_DELAY_SECONDS,
    AdaptivePlanner,
    PipelineStage,
    PlanAction,
    PlanDecision,
    PlannerContext,
)
from core.failure_taxonomy import ErrorCode, FailureRecord


def _failure(code: ErrorCode, *, retryable: bool | None = None) -> dict[str, object]:
    retryable_codes = {
        ErrorCode.DNS,
        ErrorCode.QUOTA,
        ErrorCode.TIMEOUT,
        ErrorCode.EMPTY_LLM,
    }
    return FailureRecord(
        ticker="BBCA",
        code=code,
        source="unit-test",
        message=code.value,
        retryable=code in retryable_codes if retryable is None else retryable,
    ).model_dump(mode="json")


def _ctx(
    stage: PipelineStage,
    *,
    attempt: int = 0,
    failure_record: dict[str, object] | None = None,
    provider_health: dict[str, object] | None = None,
    batch_failed_count: int = 0,
) -> PlannerContext:
    return PlannerContext(
        ticker="BBCA",
        run_id="run-1",
        stage=stage,
        attempt=attempt,
        failure_record=failure_record,
        provider_health=provider_health,
        observations_count=1,
        batch_failed_count=batch_failed_count,
    )


def test_debate_timeout_attempt_zero_retries() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(PipelineStage.DEBATE, failure_record=_failure(ErrorCode.TIMEOUT))
    )

    assert decision.action is PlanAction.RETRY
    assert decision.retry_delay_seconds == RETRY_DELAY_SECONDS
    assert "Debate timed out" in decision.reason


def test_debate_timeout_at_max_attempts_skips_ticker() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(
            PipelineStage.DEBATE,
            attempt=MAX_RETRY_ATTEMPTS,
            failure_record=_failure(ErrorCode.TIMEOUT),
        )
    )

    assert decision.action is PlanAction.SKIP_TICKER
    assert decision.retry_delay_seconds == 0


def test_sentiment_fetch_failure_proceeds_partial() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(PipelineStage.SENTIMENT_FETCH, failure_record=_failure(ErrorCode.AUTH))
    )

    assert decision.action is PlanAction.PROCEED_PARTIAL
    assert decision.confidence_penalty == PARTIAL_DATA_CONFIDENCE_PENALTY
    assert "Sentiment data unavailable" in decision.context_note


def test_fundamental_auth_failure_skips_ticker() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(PipelineStage.FUNDAMENTAL_FETCH, failure_record=_failure(ErrorCode.AUTH))
    )

    assert decision.action is PlanAction.SKIP_TICKER
    assert "auth/quota" in decision.reason


def test_technical_fetch_failure_proceeds_partial() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(PipelineStage.TECHNICAL_FETCH, failure_record=_failure(ErrorCode.SCHEMA))
    )

    assert decision.action is PlanAction.PROCEED_PARTIAL
    assert decision.confidence_penalty == PARTIAL_DATA_CONFIDENCE_PENALTY
    assert "Technical data unavailable" in decision.context_note


def test_cio_verdict_attempt_zero_retries() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(PipelineStage.CIO_VERDICT, failure_record=_failure(ErrorCode.SCHEMA))
    )

    assert decision.action is PlanAction.RETRY
    assert decision.retry_delay_seconds == RETRY_DELAY_SECONDS


def test_cio_verdict_at_max_attempts_skips_ticker() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(
            PipelineStage.CIO_VERDICT,
            attempt=MAX_RETRY_ATTEMPTS,
            failure_record=_failure(ErrorCode.SCHEMA),
        )
    )

    assert decision.action is PlanAction.SKIP_TICKER


def test_artifact_write_failure_escalates() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(PipelineStage.ARTIFACT_WRITE, failure_record=_failure(ErrorCode.UNKNOWN))
    )

    assert decision.action is PlanAction.ESCALATE
    assert "Artifact write failed" in decision.reason


def test_report_consistency_failure_escalates() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(
            PipelineStage.REPORT_CONSISTENCY,
            failure_record=_failure(ErrorCode.SCHEMA),
        )
    )

    assert decision.action is PlanAction.ESCALATE
    assert "Report consistency check failed" in decision.reason


def test_batch_failed_count_aborts_regardless_of_stage() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(
            PipelineStage.SENTIMENT_FETCH,
            failure_record=_failure(ErrorCode.AUTH),
            batch_failed_count=5,
        )
    )

    assert decision.action is PlanAction.ABORT_BATCH
    assert "Too many failures" in decision.reason


def test_all_providers_down_for_fundamental_fetch_aborts() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(
            PipelineStage.FUNDAMENTAL_FETCH,
            failure_record=_failure(ErrorCode.DNS),
            provider_health={
                "stockbit_ok": False,
                "yfinance_ok": False,
                "failures": ["stockbit:DNS", "yfinance:DNS"],
                "can_proceed": False,
            },
        )
    )

    assert decision.action is PlanAction.ABORT_BATCH
    assert "All providers down" in decision.reason


def test_context_build_failure_proceeds_partial() -> None:
    planner = AdaptivePlanner()
    decision = planner.plan(
        _ctx(PipelineStage.CONTEXT_BUILD, failure_record=_failure(ErrorCode.SCHEMA))
    )

    assert decision.action is PlanAction.PROCEED_PARTIAL
    assert decision.confidence_penalty == 0.05
    assert "Context pack degraded" in decision.context_note


def test_log_decision_writes_one_jsonl_line(tmp_path) -> None:
    planner = AdaptivePlanner(tmp_path / "plan_log.jsonl")
    decision = planner.plan(
        _ctx(PipelineStage.CIO_VERDICT, failure_record=_failure(ErrorCode.SCHEMA))
    )

    planner.log_decision(decision)

    lines = (tmp_path / "plan_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["action"] == PlanAction.RETRY.value


@pytest.mark.parametrize(
    ("action", "icon"),
    [
        (PlanAction.RETRY, "\U0001f504"),
        (PlanAction.SKIP_TICKER, "\u23ed\ufe0f"),
        (PlanAction.PROCEED_PARTIAL, "\u26a0\ufe0f"),
        (PlanAction.FALLBACK, "\U0001f500"),
        (PlanAction.ABORT_BATCH, "\U0001f6d1"),
        (PlanAction.ESCALATE, "\U0001f6a8"),
    ],
)
def test_format_decision_contains_icon_for_each_action(
    action: PlanAction,
    icon: str,
) -> None:
    planner = AdaptivePlanner()
    decision = PlanDecision(
        ticker=None,
        run_id="run-1",
        stage=PipelineStage.DEBATE,
        action=action,
        attempt=0,
        reason="sample",
    )

    formatted = planner.format_decision(decision)

    assert icon in formatted
    assert action.value in formatted
    assert "BATCH" in formatted


def test_summary_stats_counts_by_action(tmp_path) -> None:
    planner = AdaptivePlanner(tmp_path / "plan_log.jsonl")
    for action in (
        PlanAction.RETRY,
        PlanAction.RETRY,
        PlanAction.ESCALATE,
        PlanAction.SKIP_TICKER,
    ):
        planner.log_decision(
            PlanDecision(
                ticker="BBCA",
                run_id="run-1",
                stage=PipelineStage.DEBATE,
                action=action,
                attempt=0,
                reason="sample",
            )
        )
    planner.log_decision(
        PlanDecision(
            ticker="BBCA",
            run_id="other-run",
            stage=PipelineStage.DEBATE,
            action=PlanAction.ABORT_BATCH,
            attempt=0,
            reason="sample",
        )
    )

    stats = planner.summary_stats("run-1")

    assert stats["total_decisions"] == 4
    assert stats["by_action"][PlanAction.RETRY.value] == 2
    assert stats["by_action"][PlanAction.ESCALATE.value] == 1
    assert stats["by_action"][PlanAction.SKIP_TICKER.value] == 1
    assert stats["abort_count"] == 0
    assert stats["escalate_count"] == 1
    assert stats["skip_count"] == 1
