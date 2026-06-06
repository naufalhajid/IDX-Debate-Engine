# ruff: noqa: E402

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from app.cli.ui.console import console as cli_console
from core.adaptive_planner import (
    DEFAULT_PLANNER,
    AdaptivePlanner,
    PlannerContext,
    PipelineStage,
    PlanAction,
)
from core.backtest_memory import DEFAULT_MEMORY, TradeOutcome
from core.execution_ledger import DEFAULT_LEDGER, EventSeverity, EventType, LedgerEvent
from core.ops_telemetry import DEFAULT_TELEMETRY, TickerMetric
from core.prompt_pack_linter import lint_prompt_pack
from core.regime import detect_market_regime
from core.report_consistency import InconsistencyType, check_consistency
from core.risk_governor import annotate_risk
from core.settings import settings
from services.explainability_auditor import DEFAULT_AUDITOR
from services.report_formatter import DEFAULT_MD, RichFormatter
from utils.logger_config import logger

PROMPT_MANIFEST_PATH = (
    Path(__file__).resolve().parent / "services" / "debate_prompts" / "manifest.json"
)
_batch_failed_count: int = 0
_DEBATE_LOGGING_CONFIGURED = False
_MARKET_REGIME_SNAPSHOT: dict[str, Any] | None = None


def _ensure_utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _normalize_log_level(value: str | None, default: str = "INFO") -> str:
    candidate = str(value or "").strip().upper()
    if not candidate:
        return default
    try:
        logger.level(candidate)
    except ValueError:
        return default
    return candidate


def configure_debate_logging(*, verbose: bool = False) -> None:
    """Keep run_debate console output structured unless verbose is requested."""
    global _DEBATE_LOGGING_CONFIGURED
    _ensure_utf8_console()
    logger.remove()
    log_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} {file}:{line} | {message}"
    log_level = _normalize_log_level(settings.LOG_LEVEL)
    logger.add(
        settings.LOG_APP_FILENAME,
        format=log_format,
        level=log_level,
        encoding="utf-8",
        enqueue=True,
    )
    logger.add(
        "pipeline.log",
        format=log_format,
        level=log_level,
        encoding="utf-8",
    )
    if verbose:
        logger.add(
            sys.stderr,
            format=settings.LOG_FORMAT,
            level=log_level,
            colorize=True,
        )
    _DEBATE_LOGGING_CONFIGURED = True


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _ledger_call(operation: str, func, *args, **kwargs) -> None:
    try:
        func(*args, **kwargs)
    except Exception as exc:
        logger.warning(f"[ExecutionLedger] {operation} failed: {exc}")


def _ledger_artifact_write(
    *,
    run_id: str,
    ticker: str,
    latest_path: Path,
) -> None:
    _ledger_call(
        "artifact write event",
        DEFAULT_LEDGER.emit,
        LedgerEvent(
            event_id=uuid4().hex[:8],
            run_id=run_id,
            ticker=ticker,
            stage="ARTIFACT_WRITE",
            event_type=EventType.ARTIFACT_WRITE,
            severity=EventSeverity.INFO,
            message=f"Saved latest_debate.json for {ticker}",
            detail={"path": str(latest_path)},
            duration_ms=None,
            attempt=0,
            timestamp=_now_iso(),
        ),
    )


def _ledger_ticker_end(
    *,
    run_id: str,
    ticker: str,
    status: str,
    verdict: str | None,
    started_at: float,
) -> None:
    _ledger_call(
        "ticker end",
        DEFAULT_LEDGER.ticker_end,
        run_id=run_id,
        ticker=ticker,
        status=status,
        verdict=verdict,
        duration_seconds=perf_counter() - started_at,
    )


def _ledger_debate_verdict(result: dict[str, Any]) -> str | None:
    verdict = result.get("verdict")
    if isinstance(verdict, dict):
        rating = verdict.get("rating")
        return str(rating) if rating is not None else None
    try:
        parsed = json.loads(result.get("final_verdict") or "{}")
    except Exception:
        return None
    if isinstance(parsed, dict):
        rating = parsed.get("rating")
        return str(rating) if rating is not None else None
    return None


def _plan_runtime_decision(
    *,
    ticker: str | None,
    run_id: str,
    stage: PipelineStage,
    attempt: int,
    failure_record: dict[str, Any] | None = None,
    provider_health: dict[str, Any] | None = None,
    observations_count: int = 0,
    batch_failed_count: int | None = None,
    planner: AdaptivePlanner = DEFAULT_PLANNER,
):
    """Run the planner safely without letting planner failures affect runtime."""
    try:
        ctx = PlannerContext(
            ticker=ticker,
            run_id=run_id,
            stage=stage,
            attempt=attempt,
            failure_record=failure_record,
            provider_health=provider_health,
            observations_count=observations_count,
            batch_failed_count=(
                _batch_failed_count
                if batch_failed_count is None
                else batch_failed_count
            ),
        )
        decision = planner.plan(ctx)
        planner.log_decision(decision)
        logger.info(f"[Planner] {planner.format_decision(decision)}")
        _ledger_call(
            "planner decision",
            DEFAULT_LEDGER.planner_decision,
            run_id=run_id,
            ticker=ticker,
            stage=ctx.stage.name,
            action=decision.action.name,
            reason=decision.reason,
            attempt=attempt,
        )
        return decision
    except Exception as exc:
        logger.warning(
            f"[Planner] Failed during {stage.value} planning for "
            f"{ticker or 'BATCH'}; using original behavior: {exc}"
        )
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stock Debate Chamber — Adversarial Multi-Agent Analysis",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        required=False,
        help="List of stock tickers to debate (e.g., BBRI BBCA TLKM)",
    )
    parser.add_argument(
        "--ticker",
        nargs="+",
        dest="ticker_alias",
        help="Alias for --tickers.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output/debates",
        help=(
            "Directory to save debate reports. Each run writes timestamped "
            "snapshots plus latest and legacy flat files (default: output/debates)"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show raw Loguru logs in addition to Rich debate summaries.",
    )
    parser.add_argument(
        "--details",
        dest="details",
        action="store_true",
        default=True,
        help="Show detailed Rich ticker panels on console.",
    )
    parser.add_argument(
        "--no-details",
        dest="details",
        action="store_false",
        help="Skip detailed Rich ticker panels on console.",
    )
    args = parser.parse_args(argv)
    if args.ticker_alias:
        args.tickers = (args.tickers or []) + args.ticker_alias
    if not args.tickers:
        parser.error("one of --tickers or --ticker is required")
    return args


def _get_run_time() -> datetime:
    """Return the run timestamp in the configured local timezone."""
    try:
        return datetime.now(ZoneInfo(settings.DATETIME_TIMEZONE))
    except Exception as exc:
        logger.warning(
            f"[run_debate] Invalid DATETIME_TIMEZONE={settings.DATETIME_TIMEZONE!r}; "
            f"falling back to system local timezone: {exc}"
        )
        return datetime.now().astimezone()


def _as_debate_message(m):
    from schemas.debate import DebateMessage

    if isinstance(m, dict):
        return DebateMessage(**m)
    return m


def _parse_price(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing price")
    cleaned = (
        text.replace("Rp", "")
        .replace("rp", "")
        .replace(",", "")
        .replace(".", "")
        .strip()
    )
    return float(cleaned)


def _parse_entry_low(entry_price_range: Any) -> float:
    entry_low = str(entry_price_range or "").split("-", maxsplit=1)[0].strip()
    return _parse_price(entry_low)


def _record_open_trade_outcome(
    *,
    ticker: str,
    final_verdict: str,
    run_timestamp: str,
    generated_at: str,
) -> None:
    try:
        verdict = json.loads(final_verdict) if final_verdict else {}
        entry_price = _parse_entry_low(verdict.get("entry_price_range"))
        target_price = _parse_price(verdict.get("target_price"))
        stop_loss = _parse_price(verdict.get("stop_loss"))
        confidence = verdict.get("confidence")
        DEFAULT_MEMORY.record(
            TradeOutcome(
                run_id=run_timestamp,
                ticker=ticker,
                verdict_rating=str(verdict.get("rating", "UNKNOWN")),
                entry_price=entry_price,
                exit_price=None,
                target_price=target_price,
                stop_loss=stop_loss,
                entry_date=generated_at.split("T", maxsplit=1)[0],
                exit_date=None,
                outcome="open",
                pnl_pct=None,
                hit_target=None,
                hit_stop=None,
                confidence_at_entry=float(confidence)
                if confidence is not None
                else None,
                notes="auto-recorded at debate completion",
            )
        )
    except Exception as exc:
        logger.warning(f"[BacktestMemory] Failed to record {ticker} outcome: {exc}")


def _artifact_root(output_dir: Path) -> Path:
    return output_dir.parent if output_dir.name.lower() == "debates" else output_dir


def _check_report_consistency_if_available(output_dir: Path) -> None:
    _check_report_consistency_with_planner(output_dir, ticker=None, run_id="unknown")


def _latest_debate_has_risk_governor(output_dir: Path, ticker: str) -> bool:
    try:
        latest_file = output_dir / ticker / "latest_debate.json"
        if not latest_file.exists():
            return False
        payload = json.loads(latest_file.read_text(encoding="utf-8"))
        return isinstance(payload.get("risk_governor"), dict)
    except Exception:
        return False


def _check_report_consistency_with_planner(
    output_dir: Path,
    *,
    ticker: str | None,
    run_id: str,
) -> None:
    artifact_root = _artifact_root(output_dir)
    batch_json_path = artifact_root / "full_batch_results.json"
    top3_md_path = artifact_root / "TOP_3_SWING_TRADES.md"
    if not top3_md_path.exists():
        return
    try:
        report = check_consistency(batch_json_path, top3_md_path)
        if report.consistent:
            logger.info("Report consistency check passed")
            _ledger_call(
                "report consistency success",
                DEFAULT_LEDGER.stage_success,
                run_id=run_id,
                ticker=ticker,
                stage="REPORT_CONSISTENCY",
                duration_ms=0,
                detail={"result": "passed"},
            )
            return
        inconsistencies = [
            item
            for item in report.inconsistencies
            if (ticker is None or item.ticker == ticker)
            and not (
                item.type == InconsistencyType.MISSING_RISK_GOVERNOR
                and _latest_debate_has_risk_governor(output_dir, item.ticker)
            )
        ]
        if not inconsistencies:
            logger.info("Report consistency check passed")
            _ledger_call(
                "report consistency success",
                DEFAULT_LEDGER.stage_success,
                run_id=run_id,
                ticker=ticker,
                stage="REPORT_CONSISTENCY",
                duration_ms=0,
                detail={"result": "passed"},
            )
            return
        decision = _plan_runtime_decision(
            ticker=ticker,
            run_id=run_id,
            stage=PipelineStage.REPORT_CONSISTENCY,
            attempt=0,
        )
        for inconsistency in inconsistencies:
            if decision is not None and decision.action is PlanAction.ESCALATE:
                logger.error(f"[ReportConsistency] {inconsistency.model_dump()}")
            else:
                logger.warning(f"[ReportConsistency] {inconsistency.model_dump()}")
        _ledger_call(
            "report consistency escalation",
            DEFAULT_LEDGER.escalation,
            run_id=run_id,
            ticker=ticker,
            stage="REPORT_CONSISTENCY",
            message=f"{len(inconsistencies)} consistency issues",
        )
    except Exception as exc:
        logger.warning(f"[ReportConsistency] Consistency check failed: {exc}")
        _ledger_call(
            "report consistency exception",
            DEFAULT_LEDGER.escalation,
            run_id=run_id,
            ticker=ticker,
            stage="REPORT_CONSISTENCY",
            message=f"Consistency check failed: {exc}",
        )


def _write_audit_report(
    *,
    output_dir: Path,
    ticker: str,
    run_timestamp: str,
) -> None:
    try:
        ticker_dir = output_dir / ticker
        version_dir = ticker_dir / f"v{run_timestamp}"
        latest_file = ticker_dir / "latest_debate.json"
        debate_json = json.loads(latest_file.read_text(encoding="utf-8"))
        packet = DEFAULT_AUDITOR.build_audit_packet(debate_json)
        DEFAULT_AUDITOR.log_packet(packet)

        audit_report = DEFAULT_AUDITOR.format_report(packet)
        (ticker_dir / "latest_audit.txt").write_text(audit_report, encoding="utf-8")
        (version_dir / "audit.txt").write_text(audit_report, encoding="utf-8")
        logger.info(f"[Audit] {ticker}: {packet.one_line_summary}")
    except Exception as exc:
        logger.warning(f"[Audit] Failed to build audit report for {ticker}: {exc}")


def _write_formatter_report(
    *,
    output_dir: Path,
    ticker: str,
    result: dict[str, Any],
    run_timestamp: str,
    render_details: bool = True,
) -> None:
    payload = dict(result)
    metadata = (
        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    )
    if str(metadata.get("run_id") or "").lower() in {"", "unknown"}:
        metadata = {**metadata, "run_id": run_timestamp}
        payload["metadata"] = metadata
    if render_details:
        try:
            formatter = RichFormatter(console=cli_console)
            formatter.render_ticker_panel(payload)
        except Exception as e:
            logger.warning(f"[Formatter] {ticker}: {e}")

    try:
        md = DEFAULT_MD.generate_ticker_report(payload)
        md_path = output_dir / ticker / "latest_report.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding="utf-8")
        logger.info(f"[Formatter] Report: {md_path}")
    except Exception as e:
        logger.warning(f"[Formatter] MD failed: {e}")


def _status_from_error(error: str | None) -> str:
    if str(error or "").upper() == "TIMEOUT":
        return "timeout"
    if "timeout" in str(error or "").lower():
        return "timeout"
    return "failed"


def _record_ticker_telemetry(
    *,
    ticker: str,
    run_id: str,
    status: str,
    duration_seconds: float,
    report: dict | None = None,
    error: str | None = None,
) -> None:
    try:
        report = report or {}
        verdict = (
            report.get("verdict") if isinstance(report.get("verdict"), dict) else {}
        )
        metadata = (
            report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
        )

        if status == "success":
            metric = TickerMetric(
                ticker=ticker,
                run_id=str(metadata.get("run_id") or run_id),
                status="success",
                verdict_rating=verdict.get("rating"),
                confidence=verdict.get("confidence"),
                debate_rounds=int(report.get("debate_rounds") or 0),
                duration_seconds=duration_seconds,
                flash_calls=int(metadata.get("flash_calls", 0) or 0),
                pro_calls=int(metadata.get("pro_calls", 0) or 0),
                rag_chunks_selected=int(metadata.get("rag_chunks_selected", 0) or 0),
                rag_chunks_considered=int(
                    metadata.get("rag_chunks_considered", 0) or 0
                ),
                rag_token_estimate=int(metadata.get("rag_token_estimate", 0) or 0),
                provider_errors=[],
                has_stale_data=False,
                timestamp=_get_run_time().isoformat(),
            )
        else:
            metric = TickerMetric(
                ticker=ticker,
                run_id=run_id,
                status="timeout" if status == "timeout" else "failed",
                verdict_rating=None,
                confidence=None,
                debate_rounds=0,
                duration_seconds=duration_seconds,
                flash_calls=0,
                pro_calls=0,
                rag_chunks_selected=0,
                rag_chunks_considered=0,
                rag_token_estimate=0,
                provider_errors=[str(error or status)],
                has_stale_data=False,
                timestamp=_get_run_time().isoformat(),
            )
        DEFAULT_TELEMETRY.record_ticker(metric)
    except Exception as exc:
        logger.warning(
            f"[Telemetry] Failed to record ticker metric for {ticker}: {exc}"
        )


def _print_report(report_text: str) -> None:
    try:
        logger.info(str(report_text))
    except UnicodeEncodeError:
        logger.info(str(report_text.encode("ascii", errors="replace").decode("ascii")))


def _write_batch_telemetry_report(
    *,
    output_dir: Path,
    run_id: str,
    run_timestamp: str,
) -> None:
    try:
        report = DEFAULT_TELEMETRY.build_batch_report(
            run_id=run_id,
            batch_timestamp=run_timestamp,
        )
        DEFAULT_TELEMETRY.log_report(report)

        report_text = DEFAULT_TELEMETRY.format_report(report)
        telemetry_dir = _artifact_root(output_dir) / "telemetry"
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        (telemetry_dir / "latest_batch_report.txt").write_text(
            report_text,
            encoding="utf-8",
        )
        (telemetry_dir / f"{run_id}_report.txt").write_text(
            report_text,
            encoding="utf-8",
        )
        _print_report(report_text)
        logger.info(f"[Telemetry] Batch report saved for {run_id}")
    except Exception as exc:
        logger.warning(f"[Telemetry] Failed to build batch report for {run_id}: {exc}")


def _load_batch_debate_rows(
    output_dir: Path, tickers: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        latest_path = output_dir / ticker / "latest_debate.json"
        try:
            if latest_path.exists():
                rows.append(json.loads(latest_path.read_text(encoding="utf-8")))
            else:
                rows.append(
                    {
                        "ticker": ticker,
                        "error": "Debate gagal atau artifact tidak ditemukan.",
                    }
                )
        except Exception as exc:
            rows.append({"ticker": ticker, "error": f"Artifact tidak terbaca: {exc}"})
    return rows


def _render_batch_debate_summary(
    *,
    output_dir: Path,
    tickers: list[str],
    succeeded: int,
    failed: int,
    duration_seconds: float,
) -> None:
    try:
        rows = _load_batch_debate_rows(output_dir, tickers)
        RichFormatter(console=cli_console).render_batch_summary(
            rows,
            succeeded=succeeded,
            failed=failed,
            duration_seconds=duration_seconds,
        )
    except Exception as exc:
        logger.warning(f"[Formatter] Batch summary failed: {exc}")


def _save_timestamped_report(
    report: dict,
    output_dir: Path,
    ticker: str,
    run_timestamp: str,
    generated_at: str,
) -> Path:
    """
    Save a debate report with timestamped history and backward-compatible aliases.

    Layout:
      output/debates/{TICKER}/v{run_timestamp}/{TICKER}_debate.json
      output/debates/{TICKER}/latest_debate.json
      output/debates/{TICKER}_debate.json
    """
    payload = dict(report)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    payload["metadata"] = {
        **metadata,
        "batch_timestamp": run_timestamp,
        "run_timestamp": run_timestamp,
        "generated_at": generated_at,
        "versioned_output": True,
    }

    ticker_dir = output_dir / ticker
    version_dir = ticker_dir / f"v{run_timestamp}"
    version_dir.mkdir(parents=True, exist_ok=True)

    version_file = version_dir / f"{ticker}_debate.json"
    latest_file = ticker_dir / "latest_debate.json"
    legacy_file = output_dir / f"{ticker}_debate.json"

    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    for path in (version_file, latest_file, legacy_file):
        path.write_text(serialized, encoding="utf-8")

    return version_file


async def _run_chamber_with_planner(
    *,
    ticker: str,
    chamber: Any,
    run_timestamp: str,
    started_at: float,
) -> tuple[dict[str, Any] | None, int]:
    """Run chamber.run with planner-backed retry/skip decisions."""
    global _batch_failed_count

    attempt = 0
    while True:
        attempt_started_at = perf_counter()
        _ledger_call(
            "debate stage start",
            DEFAULT_LEDGER.stage_start,
            run_id=run_timestamp,
            ticker=ticker,
            stage="DEBATE",
            attempt=attempt,
        )
        try:
            # TODO: migrate DebateChamber provider internals to DEFAULT_REGISTRY once
            # graph node contracts are split enough for a clean typed-tool swap.
            setattr(chamber, "run_id", run_timestamp)
            result = await chamber.run(ticker)
        except asyncio.CancelledError:
            _ledger_call(
                "debate stage failure",
                DEFAULT_LEDGER.stage_failure,
                run_id=run_timestamp,
                ticker=ticker,
                stage="DEBATE",
                error_code="CancelledError",
                message="CancelledError",
                attempt=attempt,
                duration_ms=int((perf_counter() - attempt_started_at) * 1000),
            )
            decision = _plan_runtime_decision(
                ticker=ticker,
                run_id=run_timestamp,
                stage=PipelineStage.DEBATE,
                attempt=attempt,
                batch_failed_count=_batch_failed_count,
            )
            if decision is not None and decision.action is PlanAction.RETRY:
                _ledger_call(
                    "debate stage retry",
                    DEFAULT_LEDGER.stage_retry,
                    run_id=run_timestamp,
                    ticker=ticker,
                    stage="DEBATE",
                    attempt=attempt,
                    reason=decision.reason,
                    delay_seconds=decision.retry_delay_seconds,
                )
                await asyncio.sleep(decision.retry_delay_seconds)
                attempt += 1
                continue

            logger.error(
                f"[run_debate] {ticker}: CancelledError - connection dropped "
                "or timed out. Skipping to next ticker."
            )
            _batch_failed_count += 1
            _record_ticker_telemetry(
                ticker=ticker,
                run_id=run_timestamp,
                status="timeout",
                duration_seconds=perf_counter() - started_at,
                error="CancelledError",
            )
            return None, attempt
        except Exception as exc:
            _ledger_call(
                "debate stage failure",
                DEFAULT_LEDGER.stage_failure,
                run_id=run_timestamp,
                ticker=ticker,
                stage="DEBATE",
                error_code=type(exc).__name__,
                message=str(exc),
                attempt=attempt,
                duration_ms=int((perf_counter() - attempt_started_at) * 1000),
            )
            decision = _plan_runtime_decision(
                ticker=ticker,
                run_id=run_timestamp,
                stage=PipelineStage.DEBATE,
                attempt=attempt,
                batch_failed_count=_batch_failed_count,
            )
            if decision is not None and decision.action is PlanAction.RETRY:
                _ledger_call(
                    "debate stage retry",
                    DEFAULT_LEDGER.stage_retry,
                    run_id=run_timestamp,
                    ticker=ticker,
                    stage="DEBATE",
                    attempt=attempt,
                    reason=decision.reason,
                    delay_seconds=decision.retry_delay_seconds,
                )
                await asyncio.sleep(decision.retry_delay_seconds)
                attempt += 1
                continue

            logger.error(f"[run_debate] {ticker} failed unexpectedly: {exc}")
            _batch_failed_count += 1
            _record_ticker_telemetry(
                ticker=ticker,
                run_id=run_timestamp,
                status="failed",
                duration_seconds=perf_counter() - started_at,
                error=str(exc),
            )
            return None, attempt

        error = result.get("error")
        if error is None:
            _ledger_call(
                "debate stage success",
                DEFAULT_LEDGER.stage_success,
                run_id=run_timestamp,
                ticker=ticker,
                stage="DEBATE",
                duration_ms=int((perf_counter() - attempt_started_at) * 1000),
                detail={"verdict": _ledger_debate_verdict(result)},
            )
            return result, attempt

        metadata = result.get("metadata")
        guard_status = (
            metadata.get("guard_status") if isinstance(metadata, dict) else None
        )
        status = str(guard_status or _status_from_error(str(error)))
        _ledger_call(
            "debate stage failure",
            DEFAULT_LEDGER.stage_failure,
            run_id=run_timestamp,
            ticker=ticker,
            stage="DEBATE",
            error_code=str(error or status or "UNKNOWN"),
            message=str(error or ""),
            attempt=attempt,
            duration_ms=int((perf_counter() - attempt_started_at) * 1000),
        )
        decision = _plan_runtime_decision(
            ticker=ticker,
            run_id=run_timestamp,
            stage=PipelineStage.DEBATE,
            attempt=attempt,
            batch_failed_count=_batch_failed_count,
        )
        if decision is not None and decision.action is PlanAction.RETRY:
            _ledger_call(
                "debate stage retry",
                DEFAULT_LEDGER.stage_retry,
                run_id=run_timestamp,
                ticker=ticker,
                stage="DEBATE",
                attempt=attempt,
                reason=decision.reason,
                delay_seconds=decision.retry_delay_seconds,
            )
            await asyncio.sleep(decision.retry_delay_seconds)
            attempt += 1
            continue

        logger.error(f"Debate aborted for {ticker}: {error}")
        _batch_failed_count += 1
        _record_ticker_telemetry(
            ticker=ticker,
            run_id=run_timestamp,
            status="timeout" if status == "timeout" else "failed",
            duration_seconds=perf_counter() - started_at,
            error=str(error),
        )
        return None, attempt


def _parse_final_verdict_with_planner(
    *,
    ticker: str,
    run_timestamp: str,
    result: dict[str, Any],
    attempt: int,
    started_at: float,
) -> dict[str, Any] | None:
    """Parse final_verdict with planner-backed fallback for CIO failures."""
    global _batch_failed_count

    try:
        final_verdict = result.get("final_verdict")
        if not final_verdict:
            raise ValueError("Missing final_verdict")
        return json.loads(final_verdict)
    except Exception as exc:
        decision = _plan_runtime_decision(
            ticker=ticker,
            run_id=run_timestamp,
            stage=PipelineStage.CIO_VERDICT,
            attempt=attempt,
            batch_failed_count=_batch_failed_count,
        )
        if decision is not None and decision.action is PlanAction.ESCALATE:
            logger.error(
                f"[Planner] CIO verdict escalation for {ticker}; "
                f"saving partial result: {exc}"
            )
            return {}
        if decision is not None and decision.action is PlanAction.RETRY:
            logger.warning(
                f"[Planner] CIO retry requested for {ticker}, but run_debate "
                "cannot retry CIO in isolation; skipping ticker."
            )
        logger.error(f"[run_debate] CIO verdict unavailable for {ticker}: {exc}")
        _batch_failed_count += 1
        _record_ticker_telemetry(
            ticker=ticker,
            run_id=run_timestamp,
            status="failed",
            duration_seconds=perf_counter() - started_at,
            error=str(exc),
        )
        return None


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _market_regime_summary(snapshot: dict[str, Any] | None) -> str:
    if not isinstance(snapshot, dict):
        return "unavailable"
    regime = str(snapshot.get("regime") or "unknown")
    parts = [regime]
    volatility_regime = snapshot.get("volatility_regime")
    if volatility_regime and str(volatility_regime) != regime:
        parts.append(f"vol={volatility_regime}")
    weekly_return = snapshot.get("weekly_return")
    try:
        if weekly_return is not None:
            parts.append(f"5d {float(weekly_return) * 100:+.1f}%")
    except (TypeError, ValueError):
        pass
    reasons = snapshot.get("reasons")
    if isinstance(reasons, list) and reasons:
        reason_text = ", ".join(str(item).replace("_", " ") for item in reasons[:2])
        parts.append(reason_text)
    return " | ".join(parts)


def _attach_risk_governor(
    *,
    ticker: str,
    run_id: str,
    report: dict[str, Any],
    result: dict[str, Any],
) -> None:
    try:
        verdict = _dict_or_empty(report.get("verdict"))
        metadata = _dict_or_empty(result.get("metadata"))
        raw_data = _dict_or_empty(result.get("raw_data"))
        technicals = _dict_or_empty(result.get("technical_indicators"))
        atr14 = (
            raw_data.get("atr14") or metadata.get("atr14") or technicals.get("atr14")
        )
        avg_volume = (
            raw_data.get("avg_volume_20d")
            or metadata.get("avg_volume_20d")
            or technicals.get("avg_volume_20d")
        )

        risk_entry = {
            "ticker": ticker,
            "verdict": verdict,
            "current_price": verdict.get("current_price"),
            "market_regime": _MARKET_REGIME_SNAPSHOT,
            "risk_context": {
                "atr14": atr14,
                "avg_volume": avg_volume,
                "exdate_days": None,
                "market_regime": _MARKET_REGIME_SNAPSHOT,
                "sector": None,
                "run_id": run_id,
            },
        }
        decision = annotate_risk(risk_entry)
        risk_payload = risk_entry.get("risk_governor", decision.model_dump())
        report["risk_governor"] = risk_payload
        result["risk_governor"] = risk_payload
        logger.info(
            f"[RiskGovernor] {decision.ticker}: {decision.status} "
            f"({', '.join(decision.reason_codes)})"
        )
        _ledger_call(
            "risk check",
            DEFAULT_LEDGER.risk_check,
            run_id=run_id,
            ticker=ticker,
            original_rating=str(verdict.get("rating") or "unknown"),
            final_rating=str(decision.status),
            was_modified=not decision.sizing_allowed,
            violations=list(decision.reason_codes),
        )
    except Exception as exc:
        logger.warning(f"[RiskGovernor] evaluation failed for {ticker}: {exc}")
        fallback = {"error": str(exc)}
        report["risk_governor"] = fallback
        result["risk_governor"] = fallback


async def _debate_one(
    ticker: str,
    chamber: Any,
    output_dir: Path,
    run_timestamp: str,
    generated_at: str,
    render_details: bool = True,
) -> bool:
    """
    Run the full debate pipeline for a single ticker and save the result.

    Returns True on success, False on any failure (so the caller can tally
    how many tickers were processed correctly).

    All exceptions — including asyncio.CancelledError from dropped Gemini
    connections — are caught here so the outer loop always continues to the
    next ticker rather than aborting the entire run.
    """
    global _batch_failed_count

    cli_console.print(
        f"[idx.section]Debate[/idx.section] [idx.ticker]{ticker}[/idx.ticker]"
    )
    logger.info(f"[run_debate] Starting debate for {ticker}")

    started_at = perf_counter()
    _ledger_call(
        "ticker start",
        DEFAULT_LEDGER.ticker_start,
        run_id=run_timestamp,
        ticker=ticker,
    )
    try:
        result, attempt = await _run_chamber_with_planner(
            ticker=ticker,
            chamber=chamber,
            run_timestamp=run_timestamp,
            started_at=started_at,
        )
        if result is None:
            _ledger_ticker_end(
                run_id=run_timestamp,
                ticker=ticker,
                status="failed",
                verdict=None,
                started_at=started_at,
            )
            return False

        debate_history = [
            _as_debate_message(m) for m in result.get("debate_history", [])
        ]
        verdict_payload = _parse_final_verdict_with_planner(
            ticker=ticker,
            run_timestamp=run_timestamp,
            result=result,
            attempt=attempt,
            started_at=started_at,
        )
        if verdict_payload is None:
            _ledger_ticker_end(
                run_id=run_timestamp,
                ticker=ticker,
                status="failed",
                verdict=None,
                started_at=started_at,
            )
            return False

        # Build report dict
        report = {
            "ticker": result["ticker"],
            "verdict": verdict_payload,
            "debate_rounds": result["round_count"],
            "consensus_reached": result.get("consensus_reached", False),
            "consensus_method": result.get("consensus_method"),
            "dissenting_agents": result.get("dissenting_agents", []),
            "agent_votes": result.get("agent_votes", []),
            "consensus_winner": result.get("consensus_winner"),
            "disagreement_type": result.get("disagreement_type"),
            "debate_history": [
                {
                    "role": m.role,
                    "content": m.content,
                    "round": m.round_num,
                    "position": getattr(m, "position", "UNKNOWN"),
                    "confidence": getattr(m, "confidence", None),
                }
                for m in debate_history
            ],
            "raw_data_summary": result["raw_data"],
            "metadata": result.get("metadata", {}),
        }
        _attach_risk_governor(
            ticker=ticker,
            run_id=run_timestamp,
            report=report,
            result=result,
        )

        try:
            report_path = _save_timestamped_report(
                report=report,
                output_dir=output_dir,
                ticker=ticker,
                run_timestamp=run_timestamp,
                generated_at=generated_at,
            )
        except Exception as exc:
            decision = _plan_runtime_decision(
                ticker=ticker,
                run_id=run_timestamp,
                stage=PipelineStage.ARTIFACT_WRITE,
                attempt=attempt,
                batch_failed_count=_batch_failed_count,
            )
            if decision is not None and decision.action is PlanAction.ESCALATE:
                logger.error(f"[Planner] Artifact write escalation for {ticker}: {exc}")
            else:
                logger.error(f"[run_debate] Failed to save report for {ticker}: {exc}")
            _batch_failed_count += 1
            _ledger_ticker_end(
                run_id=run_timestamp,
                ticker=ticker,
                status="failed",
                verdict=None,
                started_at=started_at,
            )
            _record_ticker_telemetry(
                ticker=ticker,
                run_id=run_timestamp,
                status="failed",
                duration_seconds=perf_counter() - started_at,
                error=str(exc),
            )
            return False
        logger.info(f"Timestamped report saved to {report_path}")
        latest_path = output_dir / ticker / "latest_debate.json"
        logger.info(f"Latest report updated at {latest_path}")
        _ledger_artifact_write(
            run_id=run_timestamp,
            ticker=ticker,
            latest_path=latest_path,
        )
        _write_audit_report(
            output_dir=output_dir,
            ticker=ticker,
            run_timestamp=run_timestamp,
        )
        _write_formatter_report(
            output_dir=output_dir,
            ticker=ticker,
            result=report,
            run_timestamp=run_timestamp,
            render_details=render_details,
        )
        _record_ticker_telemetry(
            ticker=ticker,
            run_id=run_timestamp,
            status="success",
            duration_seconds=perf_counter() - started_at,
            report=report,
        )
        _record_open_trade_outcome(
            ticker=ticker,
            final_verdict=result.get("final_verdict", ""),
            run_timestamp=run_timestamp,
            generated_at=generated_at,
        )
        _check_report_consistency_with_planner(
            output_dir,
            ticker=ticker,
            run_id=run_timestamp,
        )
        _ledger_ticker_end(
            run_id=run_timestamp,
            ticker=ticker,
            status="success",
            verdict=str(verdict_payload.get("rating"))
            if verdict_payload.get("rating")
            else None,
            started_at=started_at,
        )
        return True

    except asyncio.CancelledError:
        # Gemini connection dropped / timed out at the httpx layer.
        # debate_chamber wraps this in RuntimeError for tenacity, but in case
        # it ever escapes, catch it here so the loop continues.
        logger.error(
            f"[run_debate] {ticker}: CancelledError - connection dropped or timed out. "
            "Skipping to next ticker."
        )
        _record_ticker_telemetry(
            ticker=ticker,
            run_id=run_timestamp,
            status="timeout",
            duration_seconds=perf_counter() - started_at,
            error="CancelledError",
        )
        _ledger_ticker_end(
            run_id=run_timestamp,
            ticker=ticker,
            status="timeout",
            verdict=None,
            started_at=started_at,
        )
        return False
    except Exception as e:
        logger.error(f"[run_debate] {ticker} failed unexpectedly: {e}")
        _record_ticker_telemetry(
            ticker=ticker,
            run_id=run_timestamp,
            status="failed",
            duration_seconds=perf_counter() - started_at,
            error=str(e),
        )
        _ledger_ticker_end(
            run_id=run_timestamp,
            ticker=ticker,
            status="failed",
            verdict=None,
            started_at=started_at,
        )
        return False


async def main(argv: list[str] | None = None) -> None:
    global _MARKET_REGIME_SNAPSHOT, _batch_failed_count
    _batch_failed_count = 0
    _MARKET_REGIME_SNAPSHOT = None

    args = parse_args(argv)
    configure_debate_logging(verbose=bool(args.verbose))
    lint_report = lint_prompt_pack(str(PROMPT_MANIFEST_PATH))
    for warning in lint_report.warnings:
        logger.warning(f"[PromptPackLinter] {warning}")
    if lint_report.errors:
        logger.error(f"[PromptPackLinter] Prompt pack invalid: {lint_report.errors}")
        cli_console.print(
            "[idx.error]Prompt pack invalid.[/idx.error] "
            "Detail lengkap ada di log file."
        )
        raise SystemExit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_time = _get_run_time()
    run_timestamp = run_time.strftime("%Y%m%d_%H%M%S")
    generated_at = run_time.isoformat()
    logger.info(f"[run_debate] Run timestamp: {run_timestamp} ({generated_at})")
    batch_started_at = perf_counter()
    _ledger_call(
        "batch start",
        DEFAULT_LEDGER.batch_start,
        run_id=run_timestamp,
        ticker_count=len(args.tickers),
        tickers=args.tickers,
    )
    cli_console.print(
        "[idx.header]Debate batch[/idx.header] "
        f"{len(args.tickers)} ticker | output=[idx.path]{output_dir}[/idx.path]"
    )

    try:
        regime_snapshot = await detect_market_regime()
        _MARKET_REGIME_SNAPSHOT = regime_snapshot.model_dump()
        cli_console.print(
            "[idx.section]Market regime[/idx.section] "
            f"{_market_regime_summary(_MARKET_REGIME_SNAPSHOT)}"
        )
    except Exception as exc:
        _MARKET_REGIME_SNAPSHOT = None
        logger.warning(f"[run_debate] Market regime unavailable: {exc}")
        cli_console.print("[idx.section]Market regime[/idx.section] unavailable")

    # LLM instances created once and reused for all tickers
    from services.debate_chamber import DebateChamber

    chamber = DebateChamber()

    succeeded, failed = 0, 0
    for ticker in args.tickers:
        ok = await _debate_one(
            ticker,
            chamber,
            output_dir,
            run_timestamp,
            generated_at,
            render_details=bool(args.details),
        )
        if ok:
            succeeded += 1
        else:
            failed += 1

    logger.info(
        f"All debates complete. OK {succeeded} succeeded / FAIL {failed} failed "
        f"out of {len(args.tickers)} tickers."
    )
    duration_seconds = perf_counter() - batch_started_at
    _render_batch_debate_summary(
        output_dir=output_dir,
        tickers=args.tickers,
        succeeded=succeeded,
        failed=failed,
        duration_seconds=duration_seconds,
    )
    _write_batch_telemetry_report(
        output_dir=output_dir,
        run_id=run_timestamp,
        run_timestamp=run_timestamp,
    )
    _ledger_call(
        "batch end",
        DEFAULT_LEDGER.batch_end,
        run_id=run_timestamp,
        succeeded=succeeded,
        failed=failed,
        duration_seconds=duration_seconds,
    )


if __name__ == "__main__":
    asyncio.run(main())
