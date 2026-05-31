"""Operational telemetry aggregation for batch analysis runs."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.settings import settings
from utils.logger_config import logger


DEFAULT_PATH = settings.ops_telemetry_path
_VERDICT_ORDER = ("BUY", "HOLD", "AVOID")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TickerMetric(BaseModel):
    """Operational metrics collected for one ticker in one batch run."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    run_id: str
    status: Literal["success", "timeout", "failed", "skipped"]
    verdict_rating: str | None = None
    confidence: float | None = None
    debate_rounds: int = 0
    duration_seconds: float = 0.0
    flash_calls: int = 0
    pro_calls: int = 0
    rag_chunks_selected: int = 0
    rag_chunks_considered: int = 0
    rag_token_estimate: int = 0
    provider_errors: list[str] = Field(default_factory=list)
    has_stale_data: bool = False
    timestamp: str = Field(default_factory=_utc_now_iso)


class BatchReport(BaseModel):
    """Aggregated operational dashboard for a batch run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    batch_timestamp: str
    total_tickers: int
    succeeded: int
    failed: int
    timed_out: int
    skipped: int
    success_rate: float
    verdict_breakdown: dict[str, int]
    avg_confidence: float | None
    avg_debate_rounds: float
    avg_duration_seconds: float
    total_flash_calls: int
    total_pro_calls: int
    estimated_flash_tokens: int
    estimated_pro_tokens: int
    rag_avg_chunks_selected: float
    rag_avg_chunks_considered: float
    rag_avg_efficiency_pct: float
    avg_rag_token_estimate: float = 0.0
    provider_error_summary: dict[str, int]
    tickers_with_stale_data: list[str]
    longest_run: str | None
    longest_run_seconds: float
    ticker_metrics: list[TickerMetric]


class OpsTelemetry:
    """Collect, persist, and summarize batch operational telemetry."""

    def __init__(self, storage_path: str | Path = DEFAULT_PATH) -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._metrics: list[TickerMetric] = []

    def record_ticker(self, metric: TickerMetric) -> None:
        """Record one ticker metric without risking caller failure."""
        try:
            self._metrics.append(metric)
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)

    def build_batch_report(self, run_id: str, batch_timestamp: str) -> BatchReport:
        """Aggregate all in-memory ticker metrics for one batch run."""
        metrics = [metric for metric in self._metrics if metric.run_id == run_id]
        total_tickers = len(metrics)
        succeeded = sum(metric.status == "success" for metric in metrics)
        failed = sum(metric.status == "failed" for metric in metrics)
        timed_out = sum(metric.status == "timeout" for metric in metrics)
        skipped = sum(metric.status == "skipped" for metric in metrics)

        confidences = [
            metric.confidence for metric in metrics if metric.confidence is not None
        ]
        longest_metric = max(metrics, key=lambda metric: metric.duration_seconds, default=None)
        total_flash_calls = sum(metric.flash_calls for metric in metrics)
        total_pro_calls = sum(metric.pro_calls for metric in metrics)
        rag_efficiencies = [
            metric.rag_chunks_selected / metric.rag_chunks_considered * 100
            for metric in metrics
            if metric.rag_chunks_considered > 0
        ]
        rag_token_estimates = [
            metric.rag_token_estimate
            for metric in metrics
            if metric.status == "success"
        ]
        provider_errors = [
            error for metric in metrics for error in metric.provider_errors
        ]

        return BatchReport(
            run_id=run_id,
            batch_timestamp=batch_timestamp,
            total_tickers=total_tickers,
            succeeded=succeeded,
            failed=failed,
            timed_out=timed_out,
            skipped=skipped,
            success_rate=succeeded / total_tickers if total_tickers else 0.0,
            verdict_breakdown=_ordered_breakdown(
                metric.verdict_rating for metric in metrics if metric.verdict_rating
            ),
            avg_confidence=mean(confidences) if confidences else None,
            avg_debate_rounds=_mean_or_zero(metric.debate_rounds for metric in metrics),
            avg_duration_seconds=_mean_or_zero(
                metric.duration_seconds for metric in metrics
            ),
            total_flash_calls=total_flash_calls,
            total_pro_calls=total_pro_calls,
            estimated_flash_tokens=total_flash_calls * 800,
            estimated_pro_tokens=total_pro_calls * 2000,
            rag_avg_chunks_selected=_mean_or_zero(
                metric.rag_chunks_selected for metric in metrics
            ),
            rag_avg_chunks_considered=_mean_or_zero(
                metric.rag_chunks_considered for metric in metrics
            ),
            rag_avg_efficiency_pct=mean(rag_efficiencies) if rag_efficiencies else 0.0,
            avg_rag_token_estimate=(
                mean(rag_token_estimates) if rag_token_estimates else 0.0
            ),
            provider_error_summary=dict(Counter(provider_errors)),
            tickers_with_stale_data=[
                metric.ticker for metric in metrics if metric.has_stale_data
            ],
            longest_run=longest_metric.ticker if longest_metric else None,
            longest_run_seconds=(
                longest_metric.duration_seconds if longest_metric else 0.0
            ),
            ticker_metrics=metrics,
        )

    def log_report(self, report: BatchReport) -> None:
        """Append a batch report to JSONL storage without risking caller failure."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.storage_path.open("a", encoding="utf-8") as file:
                file.write(report.model_dump_json())
                file.write("\n")
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)

    def format_report(self, report: BatchReport) -> str:
        """Render a human-readable telemetry dashboard."""
        confidence = (
            f"{report.avg_confidence:.0%}"
            if report.avg_confidence is not None
            else "-"
        )
        total_tokens = report.estimated_flash_tokens + report.estimated_pro_tokens
        saving_note = _format_rag_saving_note(
            report.rag_avg_chunks_selected,
            report.rag_avg_chunks_considered,
        )
        longest_run = report.longest_run or "none"

        return "\n".join(
            [
                "╔══════════════════════════════════════════╗",
                f"║  OPS TELEMETRY: {report.run_id:<24}║",
                "╚══════════════════════════════════════════╝",
                "",
                "BATCH SUMMARY",
                "─────────────",
                (
                    f"Tickers   : {report.total_tickers} "
                    f"({report.succeeded} ok / {report.failed} fail / "
                    f"{report.timed_out} timeout / {report.skipped} skip)"
                ),
                f"Success   : {report.success_rate:.0%}",
                f"Run ID    : {report.run_id}",
                f"Timestamp : {report.batch_timestamp}",
                "",
                "VERDICTS",
                "────────",
                _format_verdict_breakdown(report.verdict_breakdown),
                f"Avg confidence : {confidence}",
                f"Avg rounds     : {report.avg_debate_rounds:.1f}",
                f"Avg duration   : {report.avg_duration_seconds:.0f}s",
                (
                    f"Longest run    : {longest_run} "
                    f"({report.longest_run_seconds:.0f}s)"
                ),
                "",
                "TOKEN SPEND (estimated)",
                "───────────────────────",
                (
                    f"Flash calls : {report.total_flash_calls} "
                    f"(~{report.estimated_flash_tokens:,} tokens)"
                ),
                (
                    f"Pro calls   : {report.total_pro_calls} "
                    f"(~{report.estimated_pro_tokens:,} tokens)"
                ),
                f"Total est.  : ~{total_tokens:,} tokens",
                "",
                "RAG EFFICIENCY",
                "──────────────",
                (
                    f"Avg chunks     : {report.rag_avg_chunks_selected:.1f} selected / "
                    f"{report.rag_avg_chunks_considered:.1f} considered"
                ),
                (
                    f"Avg RAG tokens : ~{report.avg_rag_token_estimate:.0f} tokens"
                ),
                f"Context saving : {saving_note}",
                "",
                "PROVIDER HEALTH",
                "───────────────",
                f"Errors : {report.provider_error_summary or 'none'}",
                f"Stale  : {report.tickers_with_stale_data or 'none'}",
                "",
                "PER-TICKER",
                "──────────",
                _format_ticker_metrics(report.ticker_metrics),
            ]
        )

    def clear(self) -> None:
        """Reset in-memory metrics for tests."""
        self._metrics = []


def _mean_or_zero(values: Iterable[float | int]) -> float:
    materialized = list(values)
    return mean(materialized) if materialized else 0.0


def _ordered_breakdown(ratings: Iterable[str]) -> dict[str, int]:
    counts = Counter(ratings)
    ordered = {rating: counts[rating] for rating in _VERDICT_ORDER if counts[rating]}
    for rating in sorted(counts):
        if rating not in ordered:
            ordered[rating] = counts[rating]
    return ordered


def _format_verdict_breakdown(verdict_breakdown: dict[str, int]) -> str:
    if not verdict_breakdown:
        return "  none"
    return "\n".join(
        f"  {rating:<8} {count}" for rating, count in verdict_breakdown.items()
    )


def _format_rag_saving_note(selected: float, considered: float) -> str:
    if considered == 0:
        return "no RAG data"
    if selected == considered:
        return "all chunks relevant — no filtering needed"
    pct = (1 - selected / considered) * 100
    return f"{pct:.0f}% chunks filtered out"


def _format_ticker_metrics(metrics: list[TickerMetric]) -> str:
    if not metrics:
        return "  none"
    return "\n".join(_format_ticker_metric(metric) for metric in metrics)


def _format_ticker_metric(metric: TickerMetric) -> str:
    confidence = metric.confidence if metric.confidence is not None else 0.0
    return (
        f"  {metric.ticker:<6} {metric.status:<8} "
        f"{metric.verdict_rating or '-':<5} conf={confidence:.0%}  "
        f"{metric.duration_seconds:.0f}s R{metric.debate_rounds}  "
        f"RAG:{metric.rag_chunks_selected}/{metric.rag_chunks_considered}"
    )


def _load_reports(storage_path: Path) -> list[BatchReport]:
    if not storage_path.exists():
        return []

    reports: list[BatchReport] = []
    for line in storage_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            reports.append(BatchReport.model_validate_json(line))
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
            continue
    return reports


def _select_report(
    reports: list[BatchReport], run_id: str | None
) -> BatchReport | None:
    if run_id is None:
        return reports[-1] if reports else None

    for report in reversed(reports):
        if report.run_id == run_id:
            return report
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print an ops telemetry report.")
    parser.add_argument("--run-id", help="Batch run ID to print.")
    parser.add_argument(
        "--storage-path",
        type=Path,
        default=DEFAULT_PATH,
        help="Telemetry JSONL storage path.",
    )
    args = parser.parse_args(argv)

    telemetry = OpsTelemetry(args.storage_path)
    report = _select_report(_load_reports(args.storage_path), args.run_id)
    if report is None:
        message = (
            f"No telemetry report found for run_id={args.run_id}."
            if args.run_id
            else "No telemetry reports found."
        )
        print(message, file=sys.stderr)
        return 1

    print(telemetry.format_report(report))
    return 0


DEFAULT_TELEMETRY = OpsTelemetry()


if __name__ == "__main__":
    raise SystemExit(main())
