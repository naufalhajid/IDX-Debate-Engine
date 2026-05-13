import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from core.backtest_memory import DEFAULT_MEMORY, TradeOutcome
from core.ops_telemetry import DEFAULT_TELEMETRY, TickerMetric
from core.prompt_pack_linter import lint_prompt_pack
from core.report_consistency import check_consistency
from core.settings import settings
from services.explainability_auditor import DEFAULT_AUDITOR
from utils.logger_config import logger

load_dotenv()

PROMPT_MANIFEST_PATH = Path(__file__).resolve().parent / "services" / "debate_prompts" / "manifest.json"


def parse_args() -> argparse.Namespace:
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
    args = parser.parse_args()
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
                confidence_at_entry=float(confidence) if confidence is not None else None,
                notes="auto-recorded at debate completion",
            )
        )
    except Exception as exc:
        logger.warning(f"[BacktestMemory] Failed to record {ticker} outcome: {exc}")


def _artifact_root(output_dir: Path) -> Path:
    return output_dir.parent if output_dir.name.lower() == "debates" else output_dir


def _check_report_consistency_if_available(output_dir: Path) -> None:
    artifact_root = _artifact_root(output_dir)
    batch_json_path = artifact_root / "full_batch_results.json"
    top3_md_path = artifact_root / "TOP_3_SWING_TRADES.md"
    if not top3_md_path.exists():
        return
    try:
        report = check_consistency(batch_json_path, top3_md_path)
        if report.consistent:
            logger.info("Report consistency check passed")
            return
        for inconsistency in report.inconsistencies:
            logger.warning(f"[ReportConsistency] {inconsistency.model_dump()}")
    except Exception as exc:
        logger.warning(f"[ReportConsistency] Consistency check failed: {exc}")


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
        verdict = report.get("verdict") if isinstance(report.get("verdict"), dict) else {}
        metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}

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
                rag_chunks_considered=int(metadata.get("rag_chunks_considered", 0) or 0),
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
        logger.warning(f"[Telemetry] Failed to record ticker metric for {ticker}: {exc}")


def _print_report(report_text: str) -> None:
    try:
        print(report_text)
    except UnicodeEncodeError:
        print(report_text.encode("ascii", errors="replace").decode("ascii"))


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


async def _debate_one(
    ticker: str,
    chamber: Any,
    output_dir: Path,
    run_timestamp: str,
    generated_at: str,
) -> bool:
    """
    Run the full debate pipeline for a single ticker and save the result.

    Returns True on success, False on any failure (so the caller can tally
    how many tickers were processed correctly).

    All exceptions — including asyncio.CancelledError from dropped Gemini
    connections — are caught here so the outer loop always continues to the
    next ticker rather than aborting the entire run.
    """
    logger.info(f"{'=' * 60}")
    logger.info(f"Starting debate for: {ticker}")
    logger.info(f"{'=' * 60}")

    started_at = perf_counter()
    try:
        # TODO: migrate DebateChamber provider internals to DEFAULT_REGISTRY once
        # graph node contracts are split enough for a clean typed-tool swap.
        setattr(chamber, "run_id", run_timestamp)
        result = await chamber.run(ticker)

        if result.get("error") is not None:
            logger.error(f"Debate aborted for {ticker}: {result['error']}")
            _record_ticker_telemetry(
                ticker=ticker,
                run_id=run_timestamp,
                status=_status_from_error(result.get("error")),
                duration_seconds=perf_counter() - started_at,
                error=str(result.get("error")),
            )
            return False

        if result.get("error") is not None:
            logger.error(f"Debate aborted for {ticker}: {result['error']}")
            _record_ticker_telemetry(
                ticker=ticker,
                run_id=run_timestamp,
                status=_status_from_error(result.get("error")),
                duration_seconds=perf_counter() - started_at,
                error=str(result.get("error")),
            )
            return False

        debate_history = [
            _as_debate_message(m)
            for m in result.get("debate_history", [])
        ]

        # Build report dict
        report = {
            "ticker": result["ticker"],
            "verdict": json.loads(result["final_verdict"]) if result["final_verdict"] else {},
            "debate_rounds": result["round_count"],
            "debate_history": [
                {
                    "role": m.role,
                    "content": m.content,
                    "round": m.round_num,
                }
                for m in debate_history
            ],
            "raw_data_summary": result["raw_data"],
            "metadata": result.get("metadata", {}),
        }

        report_path = _save_timestamped_report(
            report=report,
            output_dir=output_dir,
            ticker=ticker,
            run_timestamp=run_timestamp,
            generated_at=generated_at,
        )
        logger.info(f"Timestamped report saved to {report_path}")
        logger.info(f"Latest report updated at {output_dir / ticker / 'latest_debate.json'}")
        _write_audit_report(
            output_dir=output_dir,
            ticker=ticker,
            run_timestamp=run_timestamp,
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
        _check_report_consistency_if_available(output_dir)
        return True

    except asyncio.CancelledError:
        # Gemini connection dropped / timed out at the httpx layer.
        # debate_chamber wraps this in RuntimeError for tenacity, but in case
        # it ever escapes, catch it here so the loop continues.
        logger.error(
            f"[run_debate] ⚠️  {ticker}: CancelledError — connection dropped or timed out. "
            "Skipping to next ticker."
        )
        _record_ticker_telemetry(
            ticker=ticker,
            run_id=run_timestamp,
            status="timeout",
            duration_seconds=perf_counter() - started_at,
            error="CancelledError",
        )
        return False
    except Exception as e:
        logger.error(f"[run_debate] 🚨 {ticker} failed unexpectedly: {e}")
        _record_ticker_telemetry(
            ticker=ticker,
            run_id=run_timestamp,
            status="failed",
            duration_seconds=perf_counter() - started_at,
            error=str(e),
        )
        return False


async def main() -> None:
    args = parse_args()
    lint_report = lint_prompt_pack(str(PROMPT_MANIFEST_PATH))
    for warning in lint_report.warnings:
        logger.warning(f"[PromptPackLinter] {warning}")
    if lint_report.errors:
        logger.error(f"[PromptPackLinter] Prompt pack invalid: {lint_report.errors}")
        raise SystemExit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_time = _get_run_time()
    run_timestamp = run_time.strftime("%Y%m%d_%H%M%S")
    generated_at = run_time.isoformat()
    logger.info(f"[run_debate] Run timestamp: {run_timestamp} ({generated_at})")

    # LLM instances created once and reused for all tickers
    from services.debate_chamber import DebateChamber

    chamber = DebateChamber()

    succeeded, failed = 0, 0
    for ticker in args.tickers:
        ok = await _debate_one(ticker, chamber, output_dir, run_timestamp, generated_at)
        if ok:
            succeeded += 1
        else:
            failed += 1

    logger.info(
        f"All debates complete. ✅ {succeeded} succeeded / ❌ {failed} failed "
        f"out of {len(args.tickers)} tickers."
    )
    _write_batch_telemetry_report(
        output_dir=output_dir,
        run_id=run_timestamp,
        run_timestamp=run_timestamp,
    )


if __name__ == "__main__":
    asyncio.run(main())
