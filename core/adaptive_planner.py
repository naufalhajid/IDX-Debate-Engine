"""Adaptive failure planner for pipeline stage recovery decisions."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.failure_taxonomy import ErrorCode, FailureAction, FailureRecord, route_failure


MAX_RETRY_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 30
PARTIAL_DATA_CONFIDENCE_PENALTY = 0.15
DEFAULT_PATH = Path("output/planner/plan_log.jsonl")


class PipelineStage(str, Enum):
    """Known pipeline stages that can request recovery planning."""

    PROVIDER_HEALTH = "PROVIDER_HEALTH"
    CANDIDATE_INTAKE = "CANDIDATE_INTAKE"
    FUNDAMENTAL_FETCH = "FUNDAMENTAL_FETCH"
    TECHNICAL_FETCH = "TECHNICAL_FETCH"
    SENTIMENT_FETCH = "SENTIMENT_FETCH"
    CONTEXT_BUILD = "CONTEXT_BUILD"
    DEBATE = "DEBATE"
    CIO_VERDICT = "CIO_VERDICT"
    ARTIFACT_WRITE = "ARTIFACT_WRITE"
    REPORT_CONSISTENCY = "REPORT_CONSISTENCY"


class PlanAction(str, Enum):
    """Planner actions that execution layers can interpret."""

    RETRY = "RETRY"
    SKIP_TICKER = "SKIP_TICKER"
    PROCEED_PARTIAL = "PROCEED_PARTIAL"
    FALLBACK = "FALLBACK"
    ABORT_BATCH = "ABORT_BATCH"
    ESCALATE = "ESCALATE"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanDecision(BaseModel):
    """Traceable decision produced by the adaptive planner."""

    model_config = ConfigDict(extra="forbid")

    ticker: str | None
    run_id: str
    stage: PipelineStage
    action: PlanAction
    attempt: int = Field(ge=0)
    reason: str
    retry_delay_seconds: int = Field(default=0, ge=0)
    confidence_penalty: float = Field(default=0.0, ge=0.0)
    context_note: str = ""
    timestamp: str = Field(default_factory=_utc_now_iso)


class PlannerContext(BaseModel):
    """Inputs required for pure recovery planning."""

    model_config = ConfigDict(extra="forbid")

    ticker: str | None = None
    run_id: str
    stage: PipelineStage
    attempt: int = Field(default=0, ge=0)
    failure_record: dict[str, Any] | None = None
    provider_health: dict[str, Any] | None = None
    observations_count: int = Field(default=0, ge=0)
    batch_failed_count: int = Field(default=0, ge=0)


class AdaptivePlanner:
    """Plan retry, skip, fallback, abort, and escalation decisions."""

    def __init__(self, storage_path: str | Path = DEFAULT_PATH):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def plan(self, ctx: PlannerContext) -> PlanDecision:
        """Return a deterministic decision from the provided context."""
        failure = _failure_from_context(ctx)
        failure_code = failure.code if failure is not None else None

        if self._provider_data_unavailable(ctx):
            return self._decision(
                ctx,
                PlanAction.ABORT_BATCH,
                "All providers down - cannot fetch market data",
            )

        if ctx.batch_failed_count >= 5:
            return self._decision(
                ctx,
                PlanAction.ABORT_BATCH,
                f"Too many failures in batch ({ctx.batch_failed_count}), stopping",
            )

        if ctx.stage is PipelineStage.DEBATE and failure_code is ErrorCode.TIMEOUT:
            if ctx.attempt < MAX_RETRY_ATTEMPTS:
                return self._decision(
                    ctx,
                    PlanAction.RETRY,
                    (
                        "Debate timed out, retrying "
                        f"(attempt {ctx.attempt + 1}/{MAX_RETRY_ATTEMPTS})"
                    ),
                    retry_delay_seconds=RETRY_DELAY_SECONDS,
                )
            return self._decision(
                ctx,
                PlanAction.SKIP_TICKER,
                "Debate timed out after max retries",
            )

        if ctx.stage is PipelineStage.CIO_VERDICT:
            if ctx.attempt < MAX_RETRY_ATTEMPTS:
                return self._decision(
                    ctx,
                    PlanAction.RETRY,
                    "CIO verdict failed, retrying",
                    retry_delay_seconds=RETRY_DELAY_SECONDS,
                )
            return self._decision(
                ctx,
                PlanAction.SKIP_TICKER,
                "CIO verdict failed after max retries",
            )

        if ctx.stage in _DATA_FETCH_STAGES:
            if ctx.stage is PipelineStage.SENTIMENT_FETCH:
                return self._decision(
                    ctx,
                    PlanAction.PROCEED_PARTIAL,
                    "Sentiment fetch failed, proceeding without sentiment data",
                    confidence_penalty=PARTIAL_DATA_CONFIDENCE_PENALTY,
                    context_note=(
                        "Sentiment data unavailable - confidence penalized"
                    ),
                )

            retry_decision = _route_for_retry(failure, ctx.attempt)
            if (
                retry_decision is not None
                and retry_decision.action is FailureAction.RETRY
                and ctx.attempt < MAX_RETRY_ATTEMPTS
            ):
                return self._decision(
                    ctx,
                    PlanAction.RETRY,
                    f"{ctx.stage.value} failed with {failure_code.value}, retrying",
                    retry_delay_seconds=RETRY_DELAY_SECONDS,
                )

            if (
                ctx.stage is PipelineStage.FUNDAMENTAL_FETCH
                and failure_code in {ErrorCode.AUTH, ErrorCode.QUOTA}
            ):
                return self._decision(
                    ctx,
                    PlanAction.SKIP_TICKER,
                    (
                        "Fundamental data provider auth/quota failure - "
                        "cannot value this ticker"
                    ),
                )

            if ctx.stage is PipelineStage.TECHNICAL_FETCH:
                return self._decision(
                    ctx,
                    PlanAction.PROCEED_PARTIAL,
                    "Technical fetch failed, proceeding without technical data",
                    confidence_penalty=PARTIAL_DATA_CONFIDENCE_PENALTY,
                    context_note=(
                        "Technical data unavailable - using fundamental "
                        "analysis only"
                    ),
                )

        if ctx.stage is PipelineStage.CONTEXT_BUILD:
            return self._decision(
                ctx,
                PlanAction.PROCEED_PARTIAL,
                "Context build failed, proceeding with raw data",
                confidence_penalty=0.05,
                context_note="Context pack degraded - using raw data directly",
            )

        if ctx.stage is PipelineStage.ARTIFACT_WRITE:
            return self._decision(
                ctx,
                PlanAction.ESCALATE,
                (
                    "Artifact write failed - debate result may be lost, "
                    "needs manual check"
                ),
            )

        if ctx.stage is PipelineStage.REPORT_CONSISTENCY:
            return self._decision(
                ctx,
                PlanAction.ESCALATE,
                (
                    "Report consistency check failed - markdown and JSON "
                    "may be out of sync"
                ),
            )

        if ctx.stage is PipelineStage.CANDIDATE_INTAKE:
            return self._decision(
                ctx,
                PlanAction.SKIP_TICKER,
                "Candidate intake rejected ticker",
            )

        action = (
            PlanAction.RETRY
            if ctx.attempt < MAX_RETRY_ATTEMPTS
            else PlanAction.SKIP_TICKER
        )
        return self._decision(
            ctx,
            action,
            f"Unhandled failure at {ctx.stage.value}",
            retry_delay_seconds=(
                RETRY_DELAY_SECONDS if action is PlanAction.RETRY else 0
            ),
        )

    def log_decision(self, decision: PlanDecision) -> None:
        """Append a decision to JSONL without crashing the caller."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.storage_path.open("a", encoding="utf-8") as handle:
                handle.write(decision.model_dump_json())
                handle.write("\n")
        except Exception:
            return

    def format_decision(self, decision: PlanDecision) -> str:
        """Format one planner decision for operator-facing logs."""
        icon = _ACTION_ICONS[decision.action]
        ticker = decision.ticker or "BATCH"
        return (
            f"{icon} [{decision.stage.value}] {ticker} \u2192 "
            f"{decision.action.value} (attempt {decision.attempt}): "
            f"{decision.reason}"
        )

    def summary_stats(self, run_id: str) -> dict[str, Any]:
        """Summarize decisions in the JSONL ledger for one run ID."""
        stats: dict[str, Any] = {
            "total_decisions": 0,
            "by_action": {},
            "by_stage": {},
            "abort_count": 0,
            "escalate_count": 0,
            "skip_count": 0,
        }
        for decision in self._iter_decisions():
            if decision.run_id != run_id:
                continue
            stats["total_decisions"] += 1
            _increment(stats["by_action"], decision.action.value)
            _increment(stats["by_stage"], decision.stage.value)
            if decision.action is PlanAction.ABORT_BATCH:
                stats["abort_count"] += 1
            elif decision.action is PlanAction.ESCALATE:
                stats["escalate_count"] += 1
            elif decision.action is PlanAction.SKIP_TICKER:
                stats["skip_count"] += 1
        return stats

    def _provider_data_unavailable(self, ctx: PlannerContext) -> bool:
        return (
            ctx.stage
            in {
                PipelineStage.PROVIDER_HEALTH,
                PipelineStage.FUNDAMENTAL_FETCH,
                PipelineStage.TECHNICAL_FETCH,
            }
            and _provider_can_proceed(ctx.provider_health) is False
        )

    def _decision(
        self,
        ctx: PlannerContext,
        action: PlanAction,
        reason: str,
        *,
        retry_delay_seconds: int = 0,
        confidence_penalty: float = 0.0,
        context_note: str = "",
    ) -> PlanDecision:
        return PlanDecision(
            ticker=ctx.ticker,
            run_id=ctx.run_id,
            stage=ctx.stage,
            action=action,
            attempt=ctx.attempt,
            reason=reason,
            retry_delay_seconds=retry_delay_seconds,
            confidence_penalty=confidence_penalty,
            context_note=context_note,
        )

    def _iter_decisions(self) -> list[PlanDecision]:
        if not self.storage_path.exists():
            return []
        decisions: list[PlanDecision] = []
        for line in self.storage_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                decisions.append(PlanDecision.model_validate_json(line))
            except Exception:
                continue
        return decisions


_DATA_FETCH_STAGES = {
    PipelineStage.FUNDAMENTAL_FETCH,
    PipelineStage.TECHNICAL_FETCH,
    PipelineStage.SENTIMENT_FETCH,
}

_ACTION_ICONS = {
    PlanAction.RETRY: "\U0001f504",
    PlanAction.SKIP_TICKER: "\u23ed\ufe0f",
    PlanAction.PROCEED_PARTIAL: "\u26a0\ufe0f",
    PlanAction.FALLBACK: "\U0001f500",
    PlanAction.ABORT_BATCH: "\U0001f6d1",
    PlanAction.ESCALATE: "\U0001f6a8",
}


def _failure_from_context(ctx: PlannerContext) -> FailureRecord | None:
    if ctx.failure_record is None:
        return None
    data = dict(ctx.failure_record)
    if "error_code" in data and "code" not in data:
        data["code"] = data["error_code"]
    try:
        return FailureRecord.model_validate(data)
    except Exception:
        return None


def _route_for_retry(
    failure: FailureRecord | None,
    attempt: int,
) -> Any | None:
    if failure is None:
        return None
    try:
        return route_failure(
            failure,
            attempt=attempt + 1,
            max_attempts=MAX_RETRY_ATTEMPTS + 1,
        )
    except Exception:
        return None


def _provider_can_proceed(provider_health: dict[str, Any] | None) -> bool | None:
    if provider_health is None:
        return None
    value = provider_health.get("can_proceed")
    return value if isinstance(value, bool) else None


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _latest_run_id(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            return PlanDecision.model_validate_json(line).run_id
        except Exception:
            continue
    return None


DEFAULT_PLANNER = AdaptivePlanner()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize adaptive planner decisions.")
    parser.add_argument("--run-id", help="Run ID to summarize")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_PATH),
        help="Path to planner JSONL log",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    planner = AdaptivePlanner(args.path)
    run_id = args.run_id or _latest_run_id(planner.storage_path) or ""
    print(json.dumps(planner.summary_stats(run_id), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
