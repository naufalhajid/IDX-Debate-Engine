"""
orchestrator.py â€" Automated Pipeline: Quant Scouting â†' Multi-Agent Debate â†' Top 3 Swing Trades.

Execution Pipeline:
  Step 1: Parse top10_candidates.json from run_quant_filter.py, extract tickers,
          exclude critical risks.
  Step 2: Run DebateChamber.run(ticker) for each candidate with bounded concurrency,
          sliding-window rate limiting, and fail-fast budget control.
  Step 3: Score & Rank using Conviction Score = 50% CIO Confidence + 50% R/R Ratio.
  Step 4: Persist full_batch_results.json + TOP_3_SWING_TRADES.md.

Changelog (refactoring dari review sesi):
  - [FIX-1] SafeRateLimiter: lock hanya dipegang saat akses _tokens, sleep selalu
    di luar lock. Menghilangkan race condition lock release/acquire manual.
  - [FIX-2] Monotonic clock (get_event_loop().time()) menggantikan time.time()
    agar sliding window tidak terpengaruh NTP sync atau DST jump.
  - [FIX-3] asyncio.Event abort flag: begitu budget habis, semua task yang belum
    mulai langsung dikembalikan tanpa memproses apapun.
  - [FIX-4] Urutan eksekusi: abort_check â†' rate_limit â†' semaphore â†' abort_check
    â†' budget_charge â†' eksekusi. Budget hanya terpotong tepat sebelum API call.
  - [FIX-5] budget_charged flag lokal per-coroutine: refund hanya terjadi jika
    budget benar-benar sudah di-charge untuk task ini, mencegah over-refund.
  - [FIX-6] CancelledError di-swallow secara eksplisit (intentional deviation dari
    konvensi asyncio) karena cancellation di sini adalah abort sistematis yang
    diharapkan, bukan shutdown eksternal. Didokumentasikan eksplisit.
  - [FIX-7] compute_conviction_score tidak lagi dipanggil dua kali; skor dari
    select_top3 di-reuse di generate_top3_report.
  - [FIX-8] ZoneInfo import dipindah ke top-level modul.
  - [FIX-9] SyntaxError di parse_report diperbaiki (for row in data).
  - [FIX-10] _empty_result di-standardisasi: selalu FAILED, tidak ada dead code.
"""

import argparse
import asyncio
import ast
from contextlib import contextmanager
import json
import math
import os
import random
import re
import shlex
import signal
import subprocess
import sys
import time
import traceback
import warnings
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4


# [FIX-8] Import ZoneInfo di top-level, satu kali, dengan fallback untuk Python < 3.9.
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

# â"€â"€ Rich CLI imports â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
# Rich digunakan untuk menghasilkan tampilan terminal yang lebih modern:
# Panel (bordered boxes), Spinner (live feedback), dan styled markup.
from pydantic import ValidationError
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from tenacity import retry, stop_after_attempt, wait_exponential

from app.cli.ui.console import IDX_THEME
from core.backtest_memory import BacktestMemory, DEFAULT_MEMORY, TradeOutcome
from core.backtest_outcome_evaluator import evaluate_memory
from core.budget import BudgetExhaustedError, get_usage, reset_budget
from core.adaptive_planner import (
    DEFAULT_PLANNER,
    PlannerContext,
    PipelineStage,
    PlanAction,
)
from core.artifact_validator import reconcile_artifacts
from core.candidate_intake import normalize_batch
from core.dependency_validator import (
    DependencyCheckResult,
    check_all_dependencies,
    check_candidates_file,
    maybe_rerun_quant_filter,
    read_candidates_execution_regime,
    read_candidates_screener_mode,
)
from core.execution_ledger import DEFAULT_LEDGER, EventSeverity, EventType, LedgerEvent
from core.execution_regime import (
    execution_regime_from_payload,
    resolve_execution_regime,
)
from core.historical_scorer import (
    apply_ev_adjustment,
    apply_historical_adjustment,
    apply_realized_adjustment,
    compute_historical_win_rate,
    compute_realized_ev,
    compute_realized_win_rate,
    load_debate_history,
    load_realized_outcomes,
)
from core.idx_market_params import SWING_EXECUTION_HORIZON_DAYS, SWING_TIMEFRAME_LABEL
from core.ops_telemetry import DEFAULT_TELEMETRY, TickerMetric
from core.quant_filter.config import canonical_screener_mode
from core.quant_filter.position_sizer import RATING_BASE_ALLOCATION, calculate_positions
from core.quant_filter.reporting import _build_position_summary
from core.portfolio_optimizer import diversify_portfolio
from core.prompt_pack_linter import lint_prompt_pack
from core.provider_health import check_all_providers
from core.regime import (
    detect_market_regime,
    get_regime_params as _get_legacy_regime_params,
)
from core.regime_gate import detect_hmm_regime
from core.report_consistency import check_consistency
from core.risk_governor import RR_IMPLAUSIBLE_CEILING, annotate_risk
from core.settings import settings
from core.comparison_reporter import DEFAULT_REPORTER, ComparisonReporter
from services.debate_prompt_registry import PROMPT_VERSION
from services.fair_value_calculator import (
    _SECTOR_BENCHMARKS_CACHE_PATH,
    _SECTOR_BENCHMARK_MAX_AGE_DAYS,
    refresh_sector_benchmarks as _refresh_sector_benchmarks,
)
from services.macro_refresh import (
    load_cached_macro_rates,
    refresh_macro_rates as _refresh_macro_rates,
)
from services.explainability_auditor import DEFAULT_AUDITOR
from services.news_fetcher import DEFAULT_FETCHER
from services.report_formatter import DEFAULT_MD, MarkdownFormatter, RichFormatter
from services.single_agent_analyzer import SingleAgentAnalyzer
from utils.logger_config import logger
from utils.price_fetcher import fetch_current_price
from utils.ticker import (
    IDX_TICKER_PATTERN,
    InvalidIDXTicker,
    canonicalize_result_identity,
    normalize_idx_ticker,
    normalize_idx_tickers,
    resolve_within_root,
)
from utils.trade_math import (
    calculate_rr,
    get_required_rr_resolution,
)


def _as_debate_message(m):
    from schemas.debate import DebateMessage

    if isinstance(m, dict):
        return DebateMessage(**m)
    return m


# Shared CLI theme; keep this alias for tests/importers that use legacy._CLI_THEME.
_CLI_THEME = IDX_THEME
console = Console(theme=_CLI_THEME, highlight=False)

# Peta rating CIO â†' warna Rich. Digunakan oleh live table dan result summary.
_RATING_STYLE: dict[str, str] = {
    "STRONG_BUY": "bold green",
    "BUY": "cyan",
    "HOLD": "dim",
    "SELL": "red",
    "AVOID": "red",
    "INSUFFICIENT_DATA": "dim",
    "ERROR": "bold red",
    "ABORTED": "dim red",
    "debating": "yellow",
    "queued": "dim",
}

MIN_CONFIDENCE_FOR_SETUP = 25
EXTREME_OVERVALUATION_THRESHOLD = 3.0


def _ensure_utf8_stdout() -> None:
    """Best-effort UTF-8 console output for Windows terminals."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)


def _clean_cli_text(value: Any) -> str:
    """Repair common mojibake sequences that leaked into older CLI strings."""
    text = str(value)
    replacements = {
        "\u00e2\u20ac\u201c": "\u2013",
        "\u00e2\u20ac\u201d": "\u2013",
        "\u00e2\u2020\u2018": "->",
        "\u00c3\u2014": "x",
        "\u00e2\u0153\u2026": "OK",
        "\u00e2\u0161\u00a0\u00ef\u00b8\u008f": "WARNING",
        "\u00f0\u0178\u203a\u2018": "STOP",
        "\u00f0\u0178\u0161\u00a8": "ERROR",
        "\u00e2\u2022\u0090": "=",
        "\u00e2\u201d\u20ac": "-",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def _short_err(msg: str, max_len: int = 60) -> str:
    return msg if len(msg) <= max_len else msg[: max_len - 1] + "…"


def _is_compact_console(con: Console, threshold: int = 140) -> bool:
    try:
        return int(con.size.width) < threshold
    except Exception:
        return True


def _format_cli_pct(value: Any) -> str:
    if value in (None, ""):
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) <= 1.0:
        number *= 100.0
    return f"{number:.0f}%"


def _format_cli_ratio(value: Any) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{float(value):.2f}x"
    except (TypeError, ValueError):
        return str(value)


def _format_evidence_age(value: Any) -> str:
    """Format an evidence-age value for compact terminal and markdown tables."""
    if value in (None, ""):
        return "-"
    try:
        return f"{int(round(float(value)))}h"
    except (TypeError, ValueError):
        return str(value)


def _format_cli_money(value: Any) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"Rp {float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _provider_failure(failures: list[Any], provider: str) -> str:
    provider_lower = provider.lower()
    for failure in failures:
        text = str(failure)
        if provider_lower in text.lower():
            return text
    return str(failures[0]) if failures else "unavailable"


def _split_log_prefix(message: str) -> tuple[str, str]:
    match = re.match(r"^\[([^\]]+)\]\s*(.*)$", message)
    if not match:
        return "Pipeline", message
    return match.group(1), match.group(2)


def _is_retry_event(message: str) -> bool:
    return message.startswith("Retrying ") and " as it raised " in message


def _retry_delay(message: str) -> str:
    match = re.search(r"\bin\s+([0-9]+(?:\.[0-9]+)?)\s+seconds\b", message)
    return match.group(1) if match else "?"


def _retry_reason(message: str) -> str:
    upper = message.upper()
    if "RESOURCE_EXHAUSTED" in upper:
        return "RESOURCE_EXHAUSTED"
    if "RATE_LIMIT" in upper or "TOO_MANY_REQUESTS" in upper or "429" in upper:
        return "RATE_LIMIT"
    raised = message.split(" as it raised ", 1)[-1]
    reason = raised.split(":", 1)[0].strip()
    return _short_err(reason or "retryable error", 32)


def _group_retry_events(
    events: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    grouped: list[tuple[str, str, str]] = []
    index = 0
    while index < len(events):
        status, source, message = events[index]
        if not _is_retry_event(message):
            grouped.append((status, source, message))
            index += 1
            continue

        attempts: list[tuple[str, str, str]] = []
        while index < len(events) and _is_retry_event(events[index][2]):
            attempts.append(events[index])
            index += 1

        last_message = attempts[-1][2]
        retry_source = attempts[-1][1] if attempts[-1][1] != "Pipeline" else "Retry"
        grouped.append(
            (
                "!",
                retry_source,
                (
                    f"⟳ Retrying ({len(attempts)} attempts): "
                    f"{_retry_reason(last_message)} — last delay {_retry_delay(last_message)}s"
                ),
            )
        )
    return grouped


class BatchProgressView:
    """Thread-safe Rich Live table for batch ticker progress."""

    STEPS = ("fetching", "analysis", "risk", "debating", "done")

    def __init__(self, tickers: list[str], con: Console = console) -> None:
        self.con = con
        self._lock = Lock()
        self._live: Live | None = None
        self._rows: dict[str, dict[str, Any]] = {
            ticker: {
                "fetching": "pending",
                "analysis": "pending",
                "risk": "pending",
                "debating": "pending",
                "done": "pending",
                "active": None,
                "rating": "-",
                "confidence": "-",
                "status": "Queued",
                "row_state": "pending",
            }
            for ticker in tickers
        }

    def start(self) -> None:
        with self._lock:
            if self._live is not None:
                return
            self._live = Live(
                self._build_table(),
                console=self.con,
                refresh_per_second=8,
                transient=False,
            )
            self._live.start()

    def stop(self) -> None:
        with self._lock:
            if self._live is None:
                return
            self._live.update(self._build_table(), refresh=True)
            self._live.stop()
            self._live = None

    def is_running(self) -> bool:
        with self._lock:
            return self._live is not None

    def update(self, ticker: str, **changes: Any) -> None:
        normalized = str(ticker).upper()
        if "status" in changes and changes["status"] is not None:
            changes["status"] = _short_err(str(changes["status"]))
        with self._lock:
            row = self._rows.setdefault(
                normalized,
                {
                    "fetching": "pending",
                    "analysis": "pending",
                    "risk": "pending",
                    "debating": "pending",
                    "done": "pending",
                    "active": None,
                    "rating": "-",
                    "confidence": "-",
                    "status": "Queued",
                    "row_state": "pending",
                },
            )
            row.update(changes)
            if self._live is not None:
                self._live.update(self._build_table(), refresh=True)

    def update_from_result(self, result: dict[str, Any]) -> None:
        ticker = str(result.get("ticker") or "UNKNOWN").upper()
        verdict = (
            result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
        )
        risk = (
            result.get("risk_governor")
            if isinstance(result.get("risk_governor"), dict)
            else {}
        )
        error = result.get("error")
        rating = str(verdict.get("rating") or ("ERROR" if error else "-"))
        confidence = _format_cli_pct(extract_model_confidence(verdict))
        row_state = _progress_row_state(result)
        self.update(
            ticker,
            fetching="done",
            analysis="done",
            risk="failed"
            if error
            else "warning"
            if risk.get("sizing_allowed") is False
            else "done",
            debating="failed" if error else "done",
            done="failed" if error else "done",
            active=None,
            rating=rating,
            confidence=confidence,
            status=_live_result_note(result),
            row_state=row_state,
        )

    def mark_failed(self, ticker: str, message: str) -> None:
        self.update(
            ticker,
            fetching="failed",
            analysis="failed",
            risk="failed",
            debating="failed",
            done="failed",
            active=None,
            rating="ERROR",
            confidence="-",
            status=_short_err(str(message)),
            row_state="failed",
        )

    def _build_table(self) -> Table:
        is_compact = _is_compact_console(self.con)
        table = Table(
            title="Live Batch Progress",
            box=box.SIMPLE,
            expand=not is_compact,
            show_edge=False,
            pad_edge=False,
        )
        columns = (
            ("Ticker", "ticker", {"style": "bold", "no_wrap": True, "width": 6}),
            (
                "D" if is_compact else "Data",
                "fetching",
                {"justify": "center", "no_wrap": True},
            ),
            (
                "A" if is_compact else "FA/TA",
                "analysis",
                {"justify": "center", "no_wrap": True},
            ),
            (
                "R" if is_compact else "Risk",
                "risk",
                {"justify": "center", "no_wrap": True},
            ),
            (
                "B" if is_compact else "Debate",
                "debating",
                {"justify": "center", "no_wrap": True},
            ),
            (
                "OK" if is_compact else "Done",
                "done",
                {"justify": "center", "no_wrap": True},
            ),
            (
                "Rating",
                "rating",
                {"justify": "center", "no_wrap": True, "max_width": 10},
            ),
            (
                "Model Conf",
                "confidence",
                {"justify": "right", "no_wrap": True, "width": 10},
            ),
            (
                "Note",
                "status",
                {
                    "overflow": "fold",
                    "max_width": 28 if is_compact else 48,
                    "ratio": 1,
                },
            ),
        )
        for header, _key, options in columns:
            table.add_column(header, **options)
        for ticker, row in self._rows.items():
            style = _progress_row_style(str(row.get("row_state") or "pending"))
            table.add_row(
                ticker,
                self._step_cell(row, "fetching"),
                self._step_cell(row, "analysis"),
                self._step_cell(row, "risk"),
                self._step_cell(row, "debating"),
                self._step_cell(row, "done"),
                Text(
                    str(row.get("rating") or "-"),
                    style=_rating_cell_style(str(row.get("rating") or "")),
                ),
                str(row.get("confidence") or "-"),
                Text(str(row.get("status") or "-")),
                style=style,
            )
        return table

    def _step_cell(self, row: dict[str, Any], step: str) -> Any:
        if row.get("active") == step:
            return Spinner("dots", style="cyan")
        state = row.get(step)
        if state == "done":
            return Text("OK", style="ok")
        if state == "failed":
            return Text("FAIL", style="danger")
        if state == "warning":
            return Text("WARN", style="warn")
        return Text("-", style="muted")


class CliRenderer:
    """Structured Rich presentation boundary for the orchestrator CLI."""

    def __init__(self, con: Console = console) -> None:
        self.con = con
        self.verbose = False
        self.show_details = False
        self.reset_run()

    def reset_run(self) -> None:
        self.warning_count = 0
        self.error_count = 0
        self.batch_error_count = 0
        self.audit_entries: list[tuple[str, str, str]] = []
        self.output_files: list[str] = []
        self.budget_usage: dict[str, Any] | None = None
        self.current_phase: str | None = None
        self.phase_events: list[tuple[str, str, str]] = []
        self.regime_events: list[tuple[str, str, str]] = []
        self.dry_run_events: list[tuple[str, str, str]] = []
        self.rank_events: list[tuple[str, str, str]] = []
        self.portfolio_threshold_note: str | None = None
        self.persistence_events: list[tuple[str, str, str]] = []
        self.lifecycle_events: list[tuple[str, str, str]] = []
        self.pipeline_status: dict[str, tuple[str, str]] = {}
        self.batch_progress: BatchProgressView | None = None
        self.failure_details: dict[str, str] = {}
        self._defer_depth = 0
        self._deferred_records: list[dict[str, Any]] = []
        self._alert_buffer_depth = 0
        self._buffered_alerts: list[tuple[str, str]] = []
        self._single_agent_warning_seen: set[str] = set()

    def start_batch_progress(self, tickers: list[str]) -> None:
        self.batch_progress = BatchProgressView(
            [str(t).upper() for t in tickers], self.con
        )
        self.batch_progress.start()

    def stop_batch_progress(self) -> None:
        if self.batch_progress is None:
            return
        self.batch_progress.stop()

    def live_active(self) -> bool:
        return self.batch_progress is not None and self.batch_progress.is_running()

    def close_batch_progress(self) -> None:
        self.stop_batch_progress()
        self.batch_progress = None

    def update_batch_progress(self, ticker: str, **changes: Any) -> None:
        if self.batch_progress is not None:
            self.batch_progress.update(ticker, **changes)

    def update_batch_progress_from_result(self, result: dict[str, Any]) -> None:
        if self.batch_progress is not None:
            self.batch_progress.update_from_result(result)

    def record_failure_detail(self, ticker: str, detail: str) -> None:
        normalized = str(ticker or "UNKNOWN").upper()
        existing = self.failure_details.get(normalized)
        if existing and detail in existing:
            return
        self.failure_details[normalized] = (
            f"{existing}\n\n{detail}" if existing else detail
        )
        if self.batch_progress is not None:
            self.batch_progress.mark_failed(
                normalized, detail.splitlines()[-1] if detail else "Failed"
            )

    @contextmanager
    def defer_logs(self):
        self._defer_depth += 1
        try:
            yield
        finally:
            self._defer_depth -= 1
            if self._defer_depth == 0 and not self.live_active():
                self.flush_deferred_logs()

    @contextmanager
    def buffer_alerts(self):
        self._alert_buffer_depth += 1
        try:
            yield
        finally:
            self._alert_buffer_depth -= 1

    def flush_buffered_alerts(self) -> None:
        pending = self._buffered_alerts
        self._buffered_alerts = []
        if not pending:
            return
        table = Table(box=box.SIMPLE, expand=True, show_edge=False, pad_edge=False)
        table.add_column("Level", style="bold", no_wrap=True)
        table.add_column("Message", overflow="fold")
        seen: set[tuple[str, str]] = set()
        has_error = False
        dns_counts: dict[str, int] = {}
        dns_has_error = False
        price_missing: set[str] = set()
        price_missing_count = 0
        price_missing_has_error = False
        for kind, message in pending:
            cleaned = _clean_cli_text(str(message)).strip()
            if not cleaned:
                continue
            dns_host = self._dns_failure_host(cleaned)
            if dns_host:
                dns_counts[dns_host] = dns_counts.get(dns_host, 0) + 1
                dns_has_error = dns_has_error or kind == "error"
                continue
            missing_ticker = self._missing_price_ticker(cleaned)
            if missing_ticker:
                price_missing.add(missing_ticker)
                price_missing_count += 1
                price_missing_has_error = price_missing_has_error or kind == "error"
                continue
            key = (kind, cleaned)
            if key in seen:
                continue
            seen.add(key)
            level = "ERROR" if kind == "error" else "WARNING"
            has_error = has_error or kind == "error"
            table.add_row(
                Text(level, style="danger" if kind == "error" else "warn"), cleaned
            )
        if dns_counts:
            level = "ERROR" if dns_has_error else "WARNING"
            has_error = has_error or dns_has_error
            hosts = ", ".join(
                f"{host} x{count}" for host, count in sorted(dns_counts.items())
            )
            key = ("error" if dns_has_error else "warning", f"dns:{hosts}")
            seen.add(key)
            table.add_row(
                Text(level, style="danger" if dns_has_error else "warn"),
                (
                    "Provider DNS/network failures: "
                    f"{hosts}. Check internet/DNS; full request URLs remain in logs."
                ),
            )
        if price_missing:
            tickers = ", ".join(sorted(price_missing))
            level = "ERROR" if price_missing_has_error else "WARNING"
            key = (
                "error" if price_missing_has_error else "warning",
                f"price:{tickers}",
            )
            has_error = has_error or price_missing_has_error
            seen.add(key)
            table.add_row(
                Text(level, style="danger" if price_missing_has_error else "warn"),
                (
                    "Market price data unavailable for "
                    f"{tickers} ({price_missing_count} possibly delisted/no-price event(s)). "
                    "This can indicate provider outage, delisting, or DNS failure."
                ),
            )
        if not seen:
            return
        self.con.print(
            Panel(
                table,
                title="Execution Warnings",
                border_style="red" if has_error else "yellow",
                title_align="left",
            )
        )

    @staticmethod
    def _dns_failure_host(message: str) -> str | None:
        lower = message.lower()
        if not any(
            marker in lower
            for marker in (
                "nameresolutionerror",
                "could not resolve host",
                "failed to resolve",
                "getaddrinfo failed",
            )
        ):
            return None
        patterns = (
            r"host='([^']+)'",
            r'host="([^"]+)"',
            r"Could not resolve host:\s*([^.\s]+(?:\.[^.\s]+)+)",
            r"Failed to resolve '([^']+)'",
            r"Failed to resolve \"([^\"]+)\"",
        )
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" .,:;")
        return "unknown-host"

    @staticmethod
    def _missing_price_ticker(message: str) -> str | None:
        if "possibly delisted; no price data found" not in message.lower():
            return None
        match = re.search(r"\$?([A-Z]{4}(?:\.JK)?)", message)
        if match:
            return match.group(1)
        return "UNKNOWN"

    def has_single_agent_warning(self, ticker: str) -> bool:
        return str(ticker or "").upper() in self._single_agent_warning_seen

    def set_pipeline_status(self, label: str, status: str, detail: str) -> None:
        self.pipeline_status[label] = (status, detail)

    def render_pipeline_status(self) -> None:
        if self.verbose or not self.pipeline_status:
            return
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold")
        table.add_column(justify="center", no_wrap=True)
        table.add_column()
        for label, (status, detail) in self.pipeline_status.items():
            table.add_row(label, Text(status, style=_status_style(status)), detail)
        self.con.print(Panel(table, title="Pipeline Status", border_style="cyan"))

    def handle_log_record(self, record: dict[str, Any]) -> None:
        if self._defer_depth:
            self._deferred_records.append(dict(record))
            return

        level = record["level"].name
        message = _clean_cli_text(record["message"])
        if not message or set(message) <= {"="}:
            return

        if self._capture_non_counted_event(message, level):
            return

        if level == "WARNING" and self._is_duplicate_single_agent_warning(message):
            return

        if level == "WARNING":
            self.warning_count += 1
        elif level in {"ERROR", "CRITICAL"}:
            self.error_count += 1

        if self._capture_structured_event(message, level):
            return

        if level == "WARNING":
            self.render_warning(message)
        elif level in {"ERROR", "CRITICAL"}:
            self.render_error(message)
        elif level == "DEBUG":
            self.render_status(message, style="muted", marker="·")
        else:
            self.render_status(message)

    def flush_deferred_logs(self) -> None:
        pending = self._deferred_records
        self._deferred_records = []
        if not pending:
            return
        for record in pending:
            self.handle_log_record(record)

    def _is_duplicate_single_agent_warning(self, message: str) -> bool:
        prefix, body = _split_log_prefix(message)
        if prefix != "SingleAgent":
            return False
        ticker, separator, _status = body.partition(":")
        if not separator:
            return False
        normalized = ticker.strip().upper()
        if not normalized:
            return False
        if normalized in self._single_agent_warning_seen:
            return True
        self._single_agent_warning_seen.add(normalized)
        return False

    def _capture_structured_event(self, message: str, level: str) -> bool:
        prefix, body = _split_log_prefix(message)

        if _is_retry_event(message):
            self.phase_events.append(("!", "Retry", message))
            return True

        if prefix == "Orchestrator":
            if body.startswith(("Meluncurkan", "Debate summary")):
                self.phase_events.append((self._event_status(level), prefix, body))
                return True
            self.lifecycle_events.append((self._event_status(level), prefix, body))
            if level in {"ERROR", "CRITICAL"}:
                self.render_error(message)
            return True

        if prefix == "Regime":
            self.regime_events.append((self._event_status(level), prefix, body))
            return True

        if prefix == "DryRun":
            self.dry_run_events.append((self._event_status(level), prefix, body))
            self.phase_events.append((self._event_status(level), prefix, body))
            return True

        if prefix == "RiskGovernor" and level == "INFO":
            return True

        if prefix == "Rank":
            self.rank_events.append((self._event_status(level), prefix, body))
            return True

        if prefix in {"Sizing", "Portfolio"} and level == "INFO":
            self.rank_events.append((self._event_status(level), prefix, body))
            return True

        if message.startswith("[Audit]"):
            self._store_audit(message, level)
            if level == "WARNING":
                self.render_warning(message)
            return True

        if message.startswith("[Budget]"):
            self.budget_usage = get_usage()
            return True

        if message.startswith("[ProviderHealth]"):
            payload = message.removeprefix("[ProviderHealth]").strip()
            if payload.startswith("{"):
                self.render_provider_health(_literal_dict(payload))
            return True

        if message.startswith("[Persist]"):
            self._store_output_path(message)
            self.persistence_events.append((self._event_status(level), prefix, body))
            return True

        if message.startswith("[Compare]"):
            self._store_output_path(message)
            self.persistence_events.append((self._event_status(level), prefix, body))
            return True

        if prefix in {"ArtifactValidator", "Telemetry", "ReportConsistency"}:
            self.persistence_events.append((self._event_status(level), prefix, body))
            if level in {"WARNING", "ERROR", "CRITICAL"}:
                self.render_warning(
                    message
                ) if level == "WARNING" else self.render_error(message)
            return True

        return False

    def _capture_non_counted_event(self, message: str, level: str) -> bool:
        try:
            prefix, body = _split_log_prefix(message)
            if prefix == "BacktestEval":
                self.lifecycle_events.append((self._event_status(level), prefix, body))
                return True
            if (
                prefix == "Portfolio"
                and level == "WARNING"
                and body.startswith("Tidak ada kandidat dengan conviction >=")
            ):
                try:
                    threshold = ORCHESTRATOR_CONFIG.get("min_conviction_override")
                    if threshold is None:
                        threshold = settings.PORTFOLIO_MIN_CONVICTION
                    self.portfolio_threshold_note = (
                        "Catatan: Tidak ada kandidat memenuhi conviction "
                        f"threshold (>= {float(threshold):.0%})"
                    )
                except Exception as exc:
                    logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
                    self.portfolio_threshold_note = (
                        "Catatan: Tidak ada kandidat memenuhi conviction threshold"
                    )
                self.rank_events.append((self._event_status(level), prefix, body))
                return True
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
            return False
        return False

    def _store_audit(self, message: str, level: str) -> None:
        body = message.removeprefix("[Audit]").strip()
        ticker, _, summary = body.partition(":")
        self.audit_entries.append(
            (ticker.strip() or "-", summary.strip() or body, level)
        )

    def _store_output_path(self, message: str) -> None:
        candidates = re.findall(
            r"(?:->|saved:)\s*([^\s]+)", message, flags=re.IGNORECASE
        )
        for path in candidates:
            if path not in self.output_files:
                self.output_files.append(path)

    def render_header(
        self,
        *,
        mode: str,
        regime: str = "detecting",
        timestamp: str,
    ) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold")
        table.add_column()
        table.add_row("Mode", mode)
        table.add_row("Regime", regime)
        table.add_row("Timestamp", timestamp)
        self.con.print(
            Panel(
                Group(
                    Text("IDX Fundamental Analysis", style="brand", justify="center"),
                    Text(
                        "Quant Scouting -> Multi-Agent Debate -> CIO Verdict",
                        style="muted",
                        justify="center",
                    ),
                    table,
                ),
                border_style="cyan",
                padding=(1, 3),
            )
        )

    def phase(self, title: str, subtitle: str | None = None) -> None:
        self.flush_phase_events()
        label = title if subtitle is None else f"{title} - {subtitle}"
        self.current_phase = label
        if self.verbose or title in {
            "Pre-flight Checks",
            "Per-Ticker Progress",
            "Final Results",
            "Summary Footer",
        }:
            self.con.print()
            self.con.print(Rule(f"[step]{label}[/step]"))

    def render_status(
        self,
        message: str,
        *,
        marker: str = "•",
        style: str = "white",
    ) -> None:
        prefix, body = _split_log_prefix(message)
        text = Text()
        text.append(f"{marker} ", style=style)
        text.append(prefix, style="step")
        if body:
            text.append(" ")
            text.append(body, style=style)
        status = self._marker_status(marker)
        self.phase_events.append(
            (status, prefix, text.plain.replace(f"{marker} {prefix} ", "", 1))
        )

    def flush_phase_events(self) -> None:
        if not self.phase_events:
            return
        if not self.verbose:
            self.phase_events = []
            return
        unique_events: list[tuple[str, str, str]] = []
        seen_events: set[tuple[str, str]] = set()
        for status, source, message in _group_retry_events(self.phase_events):
            key = (source, message)
            if key in seen_events:
                continue
            seen_events.add(key)
            unique_events.append((status, source, message))
        table = Table(
            box=box.SIMPLE,
            expand=False,
            show_edge=False,
            pad_edge=False,
        )
        table.add_column("Status", justify="center", no_wrap=True)
        table.add_column("Source", style="bold")
        table.add_column("Message")
        for status, source, message in unique_events:
            table.add_row(
                Text(status, style=_status_style(status)), source, Text(message)
            )
        title = f"{self.current_phase or 'Pipeline'} Events"
        self.con.print(Panel(table, title=title, border_style="dim"))
        self.phase_events = []

    def _event_status(self, level: str) -> str:
        if level in {"ERROR", "CRITICAL"}:
            return "FAIL"
        if level == "WARNING":
            return "WARN"
        return "OK"

    def _marker_status(self, marker: str) -> str:
        if marker in {"✓", "OK"}:
            return "OK"
        if marker in {"x", "X", "FAIL", "ERROR"}:
            return "FAIL"
        if marker in {"!", "WARNING", "WARN"}:
            return "WARN"
        if marker in {"·", "•"}:
            return "OK"
        return "OK"

    def render_warning(self, message: str) -> None:
        item = ("warning", message)
        if item not in self._buffered_alerts:
            self._buffered_alerts.append(item)

    def _print_warning(self, message: str) -> None:
        self.con.print(
            Panel(
                Text(message),
                title="Warning",
                border_style="yellow",
                title_align="left",
            )
        )

    def render_error(self, message: str) -> None:
        item = ("error", message)
        if item not in self._buffered_alerts:
            self._buffered_alerts.append(item)

    def _print_error(self, message: str) -> None:
        self.con.print(
            Panel(
                Text(message),
                title="Error",
                border_style="red",
                title_align="left",
            )
        )

    def render_provider_health(self, provider_health: Any) -> None:
        data = provider_health
        if hasattr(provider_health, "model_dump"):
            data = provider_health.model_dump()
        if not isinstance(data, dict):
            self.set_pipeline_status("Provider health", "WARN", _short_err(str(data)))
            if not self.verbose:
                return
            table = Table(box=box.SIMPLE, show_edge=False, pad_edge=False)
            table.add_column("Status", justify="center")
            table.add_column("Message")
            table.add_row("WARN", Text(str(data)))
            self.con.print(Panel(table, title="Provider Health", border_style="yellow"))
            return

        table = Table(
            box=box.SIMPLE,
            expand=False,
            show_edge=False,
            pad_edge=False,
        )
        table.add_column("Provider", style="bold")
        table.add_column("Status")
        table.add_column("Detail")
        stockbit_ok = bool(data.get("stockbit_ok"))
        yfinance_ok = bool(data.get("yfinance_ok"))
        failures = list(data.get("failures") or [])
        can_proceed = bool(data.get("can_proceed"))
        self.set_pipeline_status(
            "Stockbit",
            "OK" if stockbit_ok else "FAIL",
            "OK"
            if stockbit_ok
            else _short_err(_provider_failure(failures, "stockbit")),
        )
        self.set_pipeline_status(
            "yfinance",
            "OK" if yfinance_ok else "FAIL",
            "OK"
            if yfinance_ok
            else _short_err(_provider_failure(failures, "yfinance")),
        )
        if not self.verbose:
            return
        table.add_row(
            "Stockbit", "[ok]OK[/ok]" if stockbit_ok else "[danger]FAIL[/danger]", "-"
        )
        table.add_row(
            "yfinance", "[ok]OK[/ok]" if yfinance_ok else "[danger]FAIL[/danger]", "-"
        )
        table.add_row(
            "Proceed",
            "[ok]YES[/ok]" if can_proceed else "[danger]NO[/danger]",
            f"{len(failures)} failure(s)",
        )
        for failure in failures:
            table.add_row("Failure", "[warn]WARN[/warn]", Text(str(failure)))
        border = (
            "green"
            if can_proceed and not failures
            else "yellow"
            if can_proceed
            else "red"
        )
        self.con.print(Panel(table, title="Provider Health", border_style=border))

    def render_market_regime(
        self,
        *,
        volatility: float | None,
        execution_regime: str,
        regime_context: dict[str, Any],
        regime_params: dict[str, Any],
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        vol_text = "-" if volatility is None else f"vol {volatility * 100:.2f}%"
        override_text = _format_overrides(regime_params)
        context = dict(regime_context or {})
        trend_payload = context.get("trend_regime")
        if isinstance(trend_payload, dict):
            trend_label = str(trend_payload.get("label") or "UNKNOWN")
            trend_confidence = trend_payload.get("confidence")
        else:
            trend_label = str(trend_payload or "UNKNOWN")
            trend_confidence = None
        if isinstance(trend_confidence, (int, float)) and not isinstance(
            trend_confidence, bool
        ):
            trend_text = f"{trend_label} ({float(trend_confidence) * 100:.1f}%)"
        else:
            trend_text = trend_label
        volatility_regime = str(context.get("volatility_regime") or "UNKNOWN")
        rule_based_regime = str(context.get("rule_based_regime") or "UNKNOWN")
        execution_reason = str(context.get("execution_regime_reason") or "unspecified")
        detail = (
            f"{execution_regime} ({vol_text}); trend={trend_text}; "
            f"volatility={volatility_regime}; reason={execution_reason}"
        )
        if regime_params:
            detail = f"{detail}; {override_text}"
        self.set_pipeline_status("Market regime", "OK", detail)
        if not self.verbose:
            self.regime_events = []
            return
        table = Table(box=box.SIMPLE, expand=False, show_edge=False, pad_edge=False)
        table.add_column("Metric", style="bold")
        table.add_column("Value")
        table.add_row(
            "IHSG realized volatility",
            "-"
            if volatility is None
            else f"{volatility:.4f} ({volatility * 100:.2f}%)",
        )
        table.add_row("Execution regime", execution_regime)
        table.add_row("Execution reason", execution_reason)
        table.add_row("Trend regime (diagnostic)", trend_text)
        table.add_row("Volatility regime", volatility_regime)
        table.add_row("Rule-based regime (diagnostic)", rule_based_regime)
        if snapshot:
            weekly_return = snapshot.get("weekly_return")
            table.add_row(
                "IHSG 5d return",
                "-"
                if weekly_return is None
                else f"{float(weekly_return):.4f} ({float(weekly_return) * 100:.2f}%)",
            )
            latest_close = snapshot.get("latest_close")
            table.add_row(
                "IHSG close",
                "-" if latest_close is None else f"{float(latest_close):,.2f}",
            )
            reasons = snapshot.get("reasons")
            if isinstance(reasons, list) and reasons:
                table.add_row("Rule-based reasons", _format_regime_reasons(reasons))
        table.add_row("Overrides", _format_overrides(regime_params))
        diagnostics = [
            message
            for status, _, message in self.regime_events
            if status != "OK"
            or any(
                token in message.lower()
                for token in (
                    "fallback",
                    "gagal",
                    "kosong",
                    "terlalu",
                    "tidak tersedia",
                )
            )
        ]
        if diagnostics:
            notes = "\n".join(diagnostics)
            table.add_row("Diagnostics", Text(notes))
        border = (
            "red"
            if execution_regime in {"DEFENSIVE", "UNKNOWN"}
            else "green"
            if execution_regime == "BULL"
            else "yellow"
        )
        self.con.print(Panel(table, title="Market Regime", border_style=border))
        self.regime_events = []

    def render_ticker_progress_checklist(
        self,
        tickers: list[str],
        results: list[dict[str, Any]],
        *,
        dry_run: bool,
    ) -> None:
        by_ticker = {
            str(result.get("ticker") or "").upper(): result
            for result in results
            if isinstance(result, dict)
        }
        table = Table(
            title="Per-Ticker Progress",
            box=box.SIMPLE,
            expand=False,
            show_edge=False,
            pad_edge=False,
        )
        table.add_column("Ticker", style="bold")
        table.add_column("Step")
        table.add_column("Status", justify="center", no_wrap=True)
        table.add_column("Message")

        for ticker in tickers:
            normalized = str(ticker).upper()
            result = by_ticker.get(normalized, {})
            verdict = (
                result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
            )
            risk = (
                result.get("risk_governor")
                if isinstance(result.get("risk_governor"), dict)
                else {}
            )
            error = result.get("error")
            completed = "FAIL" if error else "OK"
            risk_status = "WARN" if risk.get("sizing_allowed") is False else "OK"
            rows = [
                (
                    "Fetching data",
                    "OK",
                    "Mock context loaded" if dry_run else "Provider context requested",
                ),
                (
                    "Running analysis",
                    "OK",
                    "Mock analysis generated" if dry_run else "Analysis completed",
                ),
                (
                    "Risk validation",
                    risk_status,
                    str(
                        risk.get("message") or risk.get("status") or "No blocking risk"
                    ),
                ),
                (
                    "Debating",
                    completed,
                    "Mock debate result" if dry_run else "Debate chamber completed",
                ),
                (
                    "Completed",
                    completed,
                    str(
                        error
                        or f"{verdict.get('rating', 'UNKNOWN')} at {_format_cli_pct(verdict.get('confidence'))}"
                    ),
                ),
            ]
            for step, status, message in rows:
                table.add_row(
                    normalized,
                    step,
                    Text(status, style=_status_style(status)),
                    Text(message),
                )
        self.con.print(table)
        self.dry_run_events = []

    def render_scoring_summary(
        self,
        *,
        results: list[dict[str, Any]],
        top_n: list[dict[str, Any]],
        sizing_result: dict[str, Any],
    ) -> None:
        if not self.verbose:
            self.rank_events = []
            return
        total = len(results)
        excluded = [
            result
            for result in results
            if (result.get("verdict") or {}).get("rating") in EXCLUDED_RATINGS
        ]
        summary = (
            sizing_result.get("summary", {}) if isinstance(sizing_result, dict) else {}
        )
        table = Table(box=box.SIMPLE, expand=False, show_edge=False, pad_edge=False)
        table.add_column("Metric", style="bold")
        table.add_column("Value")
        table.add_row("Analyzed tickers", str(total))
        table.add_row(
            "Excluded by rating",
            f"{len(excluded)} ({', '.join(_ticker_list(excluded)) or '-'})",
        )
        table.add_row(
            "Selected", f"{len(top_n)} ({', '.join(_ticker_list(top_n)) or '-'})"
        )
        table.add_row("Positions", str(summary.get("total_positions", 0)))
        table.add_row("Deployed", _format_cli_money(summary.get("total_deployed")))
        if "deployed_pct" in summary:
            table.add_row(
                "Deployment pct",
                f"{float(summary.get('deployed_pct') or 0.0) * 100:.1f}%",
            )
        if self.rank_events:
            table.add_row(
                "Diagnostics", "\n".join(message for _, _, message in self.rank_events)
            )
        self.con.print(Panel(table, title="Scoring and Sizing", border_style="cyan"))
        self.rank_events = []

    def render_persistence_table(self, output_files: list[Path]) -> None:  # noqa: ARG002
        if not self.verbose:
            self.persistence_events = []
            return
        table = Table(
            box=box.SIMPLE,
            expand=False,
            show_edge=False,
            pad_edge=False,
        )
        table.add_column("Status", justify="center", no_wrap=True)
        table.add_column("Source", style="bold")
        table.add_column("Message")
        for status, source, message in self.persistence_events:
            table.add_row(
                Text(status, style=_status_style(status)), source, Text(message)
            )
        self.con.print(Panel(table, title="Persistence", border_style="green"))
        self.persistence_events = []

    def render_debate_summaries(self, results: list[dict[str, Any]]) -> None:
        if not results:
            return
        if not self.verbose and not self.show_details and len(results) > 3:
            return
        self.phase("Per-Ticker Detail Panels")
        audit_by_ticker: dict[str, list[str]] = {}
        for ticker, summary, _level in self.audit_entries:
            audit_by_ticker.setdefault(str(ticker).upper(), []).append(summary)
        for result in results:
            ticker = str(result.get("ticker") or "-").upper()
            verdict = (
                result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
            )
            rating = str(verdict.get("rating") or result.get("rating") or "ERROR")
            confidence = _format_cli_pct(verdict.get("confidence"))
            dissenters = (
                result.get("dissenting_agents")
                or verdict.get("dissenting_agents")
                or []
            )
            if not isinstance(dissenters, list):
                dissenters = []
            soft_rules = [
                value
                for value in (
                    result.get("consensus_method"),
                    verdict.get("consensus_method"),
                    "wait_and_see" if verdict.get("wait_and_see") else None,
                )
                if value
            ]
            warnings = self._result_warnings(result, verdict)
            risk = (
                result.get("risk_governor")
                if isinstance(result.get("risk_governor"), dict)
                else {}
            )
            failure_detail = self.failure_details.get(ticker)
            if (
                not self.verbose
                and _progress_row_state(result) == "success"
                and not warnings
                and not failure_detail
            ):
                table = Table.grid(padding=(0, 2))
                table.add_column(style="bold", no_wrap=True)
                table.add_column(no_wrap=True, overflow="ellipsis")
                table.add_row("Rating", rating)
                table.add_row("Confidence", confidence)
                table.add_row(
                    "Audit", "\n".join(audit_by_ticker.get(ticker, [])) or "-"
                )
                table.add_row(
                    "News",
                    (
                        f"{result.get('news_sentiment') or (result.get('metadata') or {}).get('news_overall_sentiment') or '-'} "
                        f"({float(result.get('news_confidence_adjustment') or 0.0):+.2f})"
                    ),
                )
                table.add_row(
                    "Risk governor",
                    str(risk.get("message") or risk.get("status") or "-"),
                )
                self.con.print(
                    Panel(table, title=f"{ticker} Summary", border_style="green")
                )
                self._render_formatter_ticker_panel(result, ticker)
                continue

            table = Table.grid(padding=(0, 2))
            table.add_column(style="bold")
            table.add_column()
            table.add_row("Rounds", str(result.get("debate_rounds") or "-"))
            table.add_row("Dissent", str(len(dissenters)))
            table.add_row("Agents", _format_agents(result))
            table.add_row("Rating", rating)
            table.add_row("Confidence", confidence)
            table.add_row(
                "Soft rules", ", ".join(dict.fromkeys(map(str, soft_rules))) or "-"
            )
            table.add_row("Audit", "\n".join(audit_by_ticker.get(ticker, [])) or "-")
            table.add_row(
                "News",
                (
                    f"{result.get('news_sentiment') or (result.get('metadata') or {}).get('news_overall_sentiment') or '-'} "
                    f"({float(result.get('news_confidence_adjustment') or 0.0):+.2f})"
                ),
            )
            table.add_row(
                "Risk governor",
                str(risk.get("message") or risk.get("status") or "-"),
            )
            if warnings:
                table.add_row("Warnings", Text("\n".join(warnings), style="warn"))
            if result.get("error") or failure_detail:
                table.add_row(
                    "Failure detail",
                    Text(
                        self._failure_detail_text(failure_detail, result.get("error")),
                        style="danger",
                    ),
                )
            border = _detail_panel_border(result)
            self.con.print(
                Panel(
                    table,
                    title=f"{ticker} Detail",
                    border_style=border,
                )
            )
            self._render_formatter_ticker_panel(result, ticker)

    def _render_formatter_ticker_panel(
        self,
        result: dict[str, Any],
        ticker: str,
    ) -> None:
        try:
            formatter = RichFormatter(console=self.con)
            formatter.render_ticker_panel(result)
        except Exception as e:
            logger.warning(f"[Formatter] Rich panel failed for {ticker}: {e}")

    def _failure_detail_text(self, failure_detail: str | None, error: Any) -> str:
        if self.verbose:
            return failure_detail or str(error)
        raw = failure_detail or str(error or "Failed")
        lines = [line.strip() for line in str(raw).splitlines() if line.strip()]
        if not lines:
            return "Failed"
        traceback_lines = [line for line in lines if not line.startswith("File ")]
        return traceback_lines[-1] if traceback_lines else lines[-1]

    def _result_warnings(
        self, result: dict[str, Any], verdict: dict[str, Any]
    ) -> list[str]:
        warnings: list[str] = []
        if result.get("error"):
            warnings.append(str(result["error"]))
        if result.get("rr_warning"):
            warnings.append(str(result["rr_warning"]))
        if result.get("rr_tier_note"):
            warnings.append(str(result["rr_tier_note"]))
        risk = result.get("risk_governor")
        if isinstance(risk, dict) and risk.get("sizing_allowed") is False:
            warnings.append(
                str(risk.get("message") or risk.get("status") or "risk hold")
            )
        critical = verdict.get("critical_risk_factor")
        if critical:
            warnings.append(str(critical))
        return warnings

    def render_final_results_table(
        self,
        results: list[dict[str, Any]],
        top_n: list[dict[str, Any]],
    ) -> None:
        try:
            self.batch_error_count = sum(1 for result in results if result.get("error"))
        except Exception:
            self.batch_error_count = 0
        selected = {str(entry.get("ticker") or "").upper() for entry in top_n}
        is_compact = _is_compact_console(self.con)
        setup_table = Table(
            title="Final Results - Trading Setup",
            box=box.SIMPLE,
            expand=False,
            show_edge=False,
            pad_edge=False,
        )
        setup_table.add_column("Ticker", style="bold", no_wrap=True, width=6)
        setup_table.add_column("Rating", no_wrap=True, max_width=10)
        setup_table.add_column("Model Conf", justify="right", no_wrap=True, width=10)
        setup_table.add_column("Current", justify="right", no_wrap=True, width=10)
        setup_table.add_column(
            "Entry",
            no_wrap=True,
            max_width=16 if is_compact else 20,
        )
        setup_table.add_column("Target", justify="right", no_wrap=True, width=10)
        setup_table.add_column("Stop", justify="right", no_wrap=True, width=10)
        setup_table.add_column("R/R", justify="right", no_wrap=True, width=6)
        setup_table.add_column("Evidence Age", justify="right", no_wrap=True, width=12)

        validation_table = Table(
            title="Final Results - Validation",
            box=box.SIMPLE,
            expand=False,
            show_edge=False,
            pad_edge=False,
        )
        validation_table.add_column("Ticker", style="bold", no_wrap=True, width=6)
        validation_table.add_column("Status", no_wrap=True, max_width=14)
        validation_table.add_column("Action", no_wrap=True, max_width=14)
        validation_table.add_column("Codes", overflow="fold", max_width=28)
        validation_table.add_column("Context", no_wrap=True, max_width=40)

        for result in results:
            ticker = str(result.get("ticker") or "-").upper()
            verdict = (
                result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
            )
            risk = (
                result.get("risk_governor")
                if isinstance(result.get("risk_governor"), dict)
                else {}
            )
            rating = str(
                verdict.get("rating") or ("ERROR" if result.get("error") else "-")
            )
            sizing_text = (
                Text("TOP PICK", style="ok")
                if ticker in selected
                else Text("Excluded", style="muted")
            )
            try:
                sizing_text = _final_selection_label(
                    result, selected=ticker in selected
                )
            except Exception as exc:
                logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
            current_price = _result_current_price(verdict, risk)
            target_price = _parse_price_value(verdict.get("target_price"))
            row_style = _final_row_style(result, ticker in selected)
            setup_table.add_row(
                ticker,
                Text(rating, style=_rating_cell_style(rating)),
                _format_cli_pct(extract_model_confidence(verdict)),
                _format_cli_money(current_price),
                str(verdict.get("entry_price_range") or "-"),
                _format_cli_money(verdict.get("target_price")),
                _format_cli_money(verdict.get("stop_loss")),
                _format_cli_ratio(verdict.get("risk_reward_ratio")),
                _format_evidence_age(result.get("evidence_age_h")),
                style=row_style,
            )
            validation_table.add_row(
                ticker,
                _risk_status_text(str(risk.get("status") or "")),
                sizing_text,
                Text(_validation_reason_codes(risk)),
                Text(
                    _validation_price_context(
                        result, verdict, risk, current_price, target_price
                    )
                ),
                style=row_style,
            )
        self.con.print(setup_table)
        self.con.print(validation_table)

    def render_audit_trail(self) -> None:
        if not self.audit_entries:
            return
        table = Table(
            box=box.SIMPLE,
            expand=False,
            show_edge=False,
            pad_edge=False,
        )
        table.add_column("Ticker", style="bold")
        table.add_column("Audit")
        for ticker, summary, level in self.audit_entries:
            style = "warn" if level == "WARNING" else "white"
            table.add_row(ticker, Text(summary, style=style))
        self.con.print(Panel(table, title="Audit Trail", border_style="cyan"))

    def render_comparison_markdown_as_table(
        self,
        markdown_text: str,
        *,
        path: Path,
        agreement_rate: float | None = None,
    ) -> None:
        rows = _parse_markdown_table(markdown_text)
        summary = _parse_markdown_sections(markdown_text) if self.verbose else []
        comparison_title = "Single-Agent vs Multi-Agent Comparison"
        if agreement_rate is not None and not self.verbose:
            comparison_title = f"{comparison_title} ({agreement_rate:.0%} agree)"
        table = Table(title=comparison_title, box=box.SIMPLE)
        if rows:
            for column in rows[0]:
                column_options: dict[str, Any] = {
                    "style": "bold" if column == "Ticker" else ""
                }
                if column.strip().lower() in {"note", "notes"}:
                    column_options.update(
                        {"no_wrap": True, "overflow": "ellipsis", "max_width": 60}
                    )
                table.add_column(column, **column_options)
            for row in rows[1:]:
                styled_row: list[Any] = []
                for header, value in zip(rows[0], row):
                    if header.lower().startswith("agree"):
                        styled_row.append(Text(value, style=_agreement_style(value)))
                    elif header.strip().lower() in {"note", "notes"}:
                        styled_row.append(Text(_short_err(value)))
                    else:
                        styled_row.append(Text(value))
                table.add_row(*styled_row)
        else:
            table.add_column("Report")
            table.add_row("No markdown table found.")

        headline = [f"Report: {path}"] if self.verbose else []
        if agreement_rate is not None:
            headline.append(f"Agreement rate: {agreement_rate:.0%}")
        if summary:
            headline.extend(summary)
        if self.verbose:
            self.con.print(
                Panel(
                    Group(Text("\n".join(headline)), table),
                    title="Comparison Report",
                    border_style="cyan",
                )
            )
        else:
            self.con.print(table)
        path_text = str(path)
        if path_text not in self.output_files:
            self.output_files.append(path_text)

    def _portfolio_threshold_note(
        self, sizing_result: dict[str, Any] | None
    ) -> str | None:
        try:
            if self.portfolio_threshold_note:
                return self.portfolio_threshold_note
            for _status, source, message in self.rank_events:
                if source == "Portfolio" and str(message).startswith(
                    "Tidak ada kandidat dengan conviction >="
                ):
                    threshold = ORCHESTRATOR_CONFIG.get("min_conviction_override")
                    if threshold is None:
                        threshold = settings.PORTFOLIO_MIN_CONVICTION
                    return (
                        "Catatan: Tidak ada kandidat memenuhi conviction "
                        f"threshold (>= {float(threshold):.0%})"
                    )
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
            return None
        return None

    def render_summary_footer(
        self,
        *,
        started_at: float,
        regime: str,
        sizing_result: dict[str, Any] | None,
        output_files: list[Path],
        corrupt_lines: int = 0,
    ) -> None:
        self.budget_usage = get_usage()
        elapsed = time.monotonic() - started_at
        summary = (sizing_result or {}).get("summary") or {}
        output_file_strings = [str(path) for path in output_files]
        for path in self.output_files:
            if path not in output_file_strings:
                output_file_strings.append(path)

        pro_calls = int(self.budget_usage.get("pro_calls", 0))
        pro_budget = int(self.budget_usage.get("pro_budget", 0))
        flash_calls = int(self.budget_usage.get("flash_calls", 0))
        flash_budget = int(self.budget_usage.get("flash_budget", 0))
        estimated_tokens = pro_calls * 2000 + flash_calls * 800
        portfolio_note = self._portfolio_threshold_note(sizing_result)
        regime_context = ORCHESTRATOR_CONFIG.get("regime_context") or {}
        regime_detail = _market_regime_summary_label(
            regime_context,
            fallback=regime,
        )
        execution_reason = regime_context.get("execution_regime_reason")
        if execution_reason:
            regime_detail = f"{regime_detail} | reason={execution_reason}"

        if self.verbose:
            table = Table(box=box.SIMPLE, expand=False, show_edge=False, pad_edge=False)
            table.add_column("Metric", style="bold")
            table.add_column("Value")
            table.add_row("Pipeline status", "Completed")
            table.add_row("Runtime", f"{elapsed:.1f}s")
            table.add_row(
                "Token budget used", f"~{estimated_tokens:,} estimated tokens"
            )
            table.add_row("Pro usage", f"{pro_calls}/{pro_budget}")
            table.add_row("Flash usage", f"{flash_calls}/{flash_budget}")
            table.add_row("Execution regime", regime_detail)
            table.add_row(
                "Total deployed", _format_cli_money(summary.get("total_deployed"))
            )
            if "deployed_pct" in summary:
                table.add_row(
                    "Deployment pct",
                    f"{float(summary.get('deployed_pct') or 0.0) * 100:.1f}%",
                )
            table.add_row("Warnings", str(self.warning_count))
            if self.batch_error_count:
                table.add_row("Errors", str(self.batch_error_count))
            if corrupt_lines > 0:
                # FIX: ISSUE 4 — Surface audit-log corruption in the summary footer.
                table.add_row(
                    "Audit integrity",
                    f"⚠️  Audit integrity: {corrupt_lines} corrupt line(s) — see audit_corrupt.jsonl",
                )
            if portfolio_note:
                table.add_row("Catatan", portfolio_note)
            output_detail = "\n".join(output_file_strings) or "-"
            table.add_row("Output files", output_detail)
        else:
            table = Table.grid(expand=True, padding=(0, 2))
            table.add_column(style="bold", no_wrap=True)
            table.add_column(no_wrap=True, overflow="ellipsis")
            table.add_column(style="bold", no_wrap=True)
            table.add_column(no_wrap=True, overflow="ellipsis")
            names = [Path(path).name for path in output_file_strings]
            preview = ", ".join(names[:2])
            if len(names) > 2:
                preview = f"{preview}, +{len(names) - 2} more"
            output_detail = f"{len(names)} item(s): {preview}" if names else "-"
            deployed_pct = "-"
            if "deployed_pct" in summary:
                deployed_pct = f"{float(summary.get('deployed_pct') or 0.0) * 100:.1f}%"
            table.add_row("Status", "Completed", "Duration", f"{elapsed:.1f}s")
            table.add_row(
                "Tokens Used",
                f"~{estimated_tokens:,} estimated",
                "API Quota",
                f"Pro {pro_calls}/{pro_budget}; Flash {flash_calls}/{flash_budget}",
            )
            table.add_row(
                "Market Regime",
                regime_detail,
                "Capital Deployed",
                f"{_format_cli_money(summary.get('total_deployed'))} ({deployed_pct})",
            )
            warning_text = f"{self.warning_count} warning(s)"
            if self.batch_error_count:
                warning_text = f"{warning_text}, {self.batch_error_count} error(s)"
            table.add_row("Warnings", warning_text, "Output Files", output_detail)
            if corrupt_lines > 0:
                # FIX: ISSUE 4 — Surface audit-log corruption in the summary footer.
                table.add_row(
                    "Audit Integrity",
                    f"⚠️  Audit integrity: {corrupt_lines} corrupt line(s) — see audit_corrupt.jsonl",
                    "",
                    "",
                )
            if portfolio_note:
                table.add_row("Catatan", portfolio_note, "", "")
        self.con.print(Panel(table, title="Summary Footer", border_style="green"))


class RichLogSink:
    def __init__(self, renderer: CliRenderer) -> None:
        self.renderer = renderer

    def __call__(self, message) -> None:
        self.renderer.handle_log_record(message.record)


def _literal_dict(raw: str) -> dict[str, Any]:
    try:
        value = ast.literal_eval(raw)
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
        return {}
    return value if isinstance(value, dict) else {}


def _parse_markdown_table(markdown_text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def _parse_markdown_sections(markdown_text: str) -> list[str]:
    details: list[str] = []
    in_table = False
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if not stripped:
            in_table = False
            continue
        if stripped.startswith("|"):
            in_table = True
            continue
        if in_table:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            details.append(stripped[2:])
        elif ":" in stripped and not stripped.startswith("##"):
            details.append(stripped)
    return details


def _agreement_style(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"ya", "yes", "agree", "agreed"}:
        return "green"
    if normalized in {"tidak", "no", "disagree", "disagreed"}:
        return "red"
    if any(token in normalized for token in ("partial", "mixed", "needs review")):
        return "yellow"
    return "white"


def _status_style(status: str) -> str:
    normalized = str(status or "").upper()
    if normalized in {"OK", "PASS", "DONE", "✓"}:
        return "ok"
    if normalized in {"WARN", "WARNING", "!"}:
        return "warn"
    if normalized in {"FAIL", "FAILED", "ERROR", "X"}:
        return "danger"
    return "muted"


def _rating_cell_style(rating: str) -> str:
    normalized = str(rating or "").upper()
    if normalized in {"STRONG_BUY", "BUY"}:
        return "bold green"
    if normalized == "HOLD":
        return "bold yellow"
    if normalized in {"SELL", "AVOID", "ERROR", "ABORTED", "FAILED"}:
        return "bold red"
    return "white"


def _progress_row_style(row_state: str) -> str:
    if row_state == "failed":
        return "red"
    if row_state == "warning":
        return "yellow"
    if row_state == "success":
        return "green"
    if row_state == "active":
        return "cyan"
    return "white"


def _progress_row_state(result: dict[str, Any]) -> str:
    if result.get("error") or _result_status(result) in {"failed", "timeout"}:
        return "failed"
    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    rating = str(verdict.get("rating") or "").upper()
    if rating == "HOLD":
        return "warning"
    risk = (
        result.get("risk_governor")
        if isinstance(result.get("risk_governor"), dict)
        else {}
    )
    if risk.get("sizing_allowed") is False:
        return "warning"
    confidence = extract_model_confidence(verdict)
    if confidence is not None and confidence < 0.60:
        return "warning"
    return "success"


def _detail_panel_border(result: dict[str, Any]) -> str:
    state = _progress_row_state(result)
    if state == "failed":
        return "red"
    if state == "warning":
        return "yellow"
    return "green"


def _final_row_style(result: dict[str, Any], selected: bool) -> str:
    if result.get("error"):
        return "red"
    if selected:
        return "green"
    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    if str(verdict.get("rating") or "").upper() == "HOLD":
        return "yellow"
    return "white"


def _result_warning_notes(result: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    rating = str(verdict.get("rating") or "").upper()
    if rating == "HOLD":
        notes.append("Hold/wait-and-see verdict")
    confidence = extract_model_confidence(verdict)
    if confidence is not None and confidence < 0.60:
        notes.append(f"Low confidence: {confidence:.0%}")
    risk = (
        result.get("risk_governor")
        if isinstance(result.get("risk_governor"), dict)
        else {}
    )
    if risk.get("sizing_allowed") is False:
        reason_codes = risk.get("reason_codes")
        if isinstance(reason_codes, list) and reason_codes:
            notes.extend(str(code) for code in reason_codes if str(code))
        else:
            notes.append(
                str(risk.get("message") or risk.get("status") or "Risk governor hold")
            )
    if result.get("rr_warning"):
        notes.append(str(result["rr_warning"]))
    if result.get("rr_tier_note"):
        notes.append(str(result["rr_tier_note"]))
    if result.get("error"):
        notes.append(str(result["error"]))
    return notes


def _format_agents(result: dict[str, Any]) -> str:
    votes = result.get("agent_votes")
    if not isinstance(votes, list) or not votes:
        return "-"
    agents: list[str] = []
    for vote in votes:
        if not isinstance(vote, dict):
            continue
        agent = str(vote.get("agent") or "-")
        position = str(vote.get("position") or "-")
        confidence = vote.get("confidence")
        label = f"{agent}:{position}"
        if confidence is not None:
            label += f"({_format_cli_pct(confidence)})"
        agents.append(label)
    return ", ".join(agents) or "-"


def _exclusion_reason(result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"failed: {result['error']}"
    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    if not verdict:
        return "no verdict"
    rating = str(verdict.get("rating") or "").upper()
    if rating in EXCLUDED_RATINGS:
        return f"rating {rating}"
    risk = (
        result.get("risk_governor")
        if isinstance(result.get("risk_governor"), dict)
        else {}
    )
    if risk.get("sizing_allowed") is False:
        return str(risk.get("status") or "risk hold")
    return "not selected after ranking"


def _result_current_price(
    verdict: dict[str, Any], risk: dict[str, Any]
) -> float | None:
    price = _parse_price_value(verdict.get("current_price"))
    if price is not None:
        return price
    return _parse_price_value(risk.get("current_price"))


def _risk_entry_bounds(
    verdict: dict[str, Any],
    risk: dict[str, Any],
) -> tuple[float | None, float | None]:
    low = _parse_price_value(risk.get("entry_low"))
    high = _parse_price_value(risk.get("entry_high"))
    if low is not None or high is not None:
        return low, high if high is not None else low
    return _parse_entry_bounds(verdict.get("entry_price_range"))


def _format_entry_gap(
    current_price: float | None,
    verdict: dict[str, Any],
    risk: dict[str, Any],
) -> str:
    if current_price is None or current_price <= 0:
        return "-"
    low, high = _risk_entry_bounds(verdict, risk)
    if low is None and high is None:
        return "-"
    low = low if low is not None else high
    high = high if high is not None else low
    if low is None or high is None or low <= 0 or high <= 0:
        return "-"
    if low <= current_price <= high:
        return "inside entry"
    if current_price > high:
        gap = ((current_price - high) / high) * 100.0
        return f"+{gap:.1f}% above entry"
    gap = ((current_price - low) / low) * 100.0
    return f"{gap:.1f}% below entry"


def _format_target_vs_current(
    target_price: float | None,
    current_price: float | None,
) -> str:
    if target_price is None or current_price is None or current_price <= 0:
        return "-"
    delta = ((target_price - current_price) / current_price) * 100.0
    return f"{delta:+.1f}%"


_REASON_LABELS = {
    "rating_hold": "HOLD",
    "hold/wait_and_see_verdict": "HOLD",
    "low_confidence": "low conf",
    "counter_trend_setup": "counter-trend",
    "price_inside_entry_range": "inside entry",
    "price_above_entry_range": "above entry",
    "price_below_entry_range": "below entry",
    "upside_exhausted": "upside exhausted",
    "wait_for_pullback": "wait pullback",
    "watchlist_only": "watchlist",
    "conditional_deployable": "conditional",
    "deployable": "deployable",
    "reject": "reject",
    "market_regime_defensive": "defensive market",
    "preflight_noise_reject": "preflight noise",
    "weekly_return_below_threshold": "IHSG 5d drop",
    "close_below_ma20_ma50_ma200": "below MA20/50/200",
    "ihsg_data_unavailable_fallback_to_volatility": "IHSG data fallback",
    "rr_too_low": "R/R too low",
    "stop_inside_noise": "stop in noise",
    "target_collapsed": "target collapsed",
    "no_momentum_confirmation": "no momentum",
    "wait_for_momentum_confirmation": "wait momentum confirmation",
    "shadow_only_momentum_recalibration": "shadow-only calibration",
}


def _reason_token_label(token: str) -> str:
    normalized = str(token or "").strip()
    key = normalized.lower().replace(" ", "_").replace("-", "_")
    if key in _REASON_LABELS:
        return _REASON_LABELS[key]
    lowered = normalized.lower()
    if lowered.startswith("hold/wait"):
        return "HOLD"
    if lowered.startswith("low confidence"):
        return "low conf"
    if lowered.startswith("rating "):
        return normalized.replace("rating ", "").upper()
    return normalized.replace("_", " ")


def _compact_note(parts: list[str], *, max_items: int = 3, max_len: int = 42) -> str:
    labels: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for raw_token in str(part or "").replace(";", ",").split(","):
            label = _reason_token_label(raw_token)
            if not label or label == "-":
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            labels.append(label)
            if len(labels) >= max_items:
                break
        if len(labels) >= max_items:
            break
    return _short_err(" / ".join(labels) or "-", max_len)


def _validator_reason_full(risk: dict[str, Any]) -> str:
    reason_codes = risk.get("reason_codes")
    if isinstance(reason_codes, list):
        reasons = [str(code) for code in reason_codes if str(code)]
        if reasons:
            return ", ".join(reasons)
    for key in ("status", "message", "error"):
        value = risk.get(key)
        if value:
            return str(value)
    return "-"


_STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "deployable": ("Ready", "idx.bull"),
    "conditional_deployable": ("Conditional", "cyan"),
    "wait_for_pullback": ("Wait", "amber"),
    "watchlist_only": ("Watchlist", "amber"),
    "reject": ("Reject", "idx.bear"),
}

_PRICE_POSITION_CODES = frozenset(
    {
        "price_above_entry_range",
        "price_inside_entry_range",
        "price_below_entry_range",
    }
)


def _risk_status_text(status: str) -> Text:
    label, style = _STATUS_DISPLAY.get(
        str(status).lower(),
        (str(status).replace("_", " ").title(), ""),
    )
    return Text(label, style=style)


def _validation_reason_codes(risk: dict[str, Any]) -> str:
    codes = risk.get("reason_codes")
    if not isinstance(codes, list):
        for key in ("message", "error"):
            v = risk.get(key)
            if v:
                return str(v)[:50]
        return "-"
    non_price = [c for c in codes if c not in _PRICE_POSITION_CODES]
    return ", ".join(_reason_token_label(c) for c in non_price) or "-"


def _validation_price_context(
    result: dict[str, Any],
    verdict: dict[str, Any],
    risk: dict[str, Any],
    current_price: float | None,
    target_price: float | None,
) -> str:
    entry_gap = _format_entry_gap(current_price, verdict, risk)
    target_gap = _format_target_vs_current(target_price, current_price)
    parts = []
    if entry_gap != "-":
        parts.append(entry_gap)
    if target_gap != "-":
        parts.append(f"tgt {target_gap}")
    if result.get("rr_tier_note"):
        parts.append(str(result["rr_tier_note"]))
    return " | ".join(parts) or "-"


def _validator_reason(risk: dict[str, Any]) -> str:
    return _short_err(_validator_reason_full(risk), 60)


def _live_result_note(result: dict[str, Any]) -> str:
    if result.get("error"):
        return _compact_note([str(result.get("error"))], max_items=2, max_len=36)
    warnings = _result_warning_notes(result)
    if not warnings:
        return "OK"
    return _compact_note(warnings, max_items=3, max_len=36)


def _validation_reason_summary(
    result: dict[str, Any],
    verdict: dict[str, Any],
    risk: dict[str, Any],
    current_price: float | None,
    target_price: float | None,
) -> str:
    parts = [_validator_reason_full(risk)]
    entry_gap = _format_entry_gap(current_price, verdict, risk)
    if entry_gap != "-":
        parts.append(entry_gap)
    target_gap = _format_target_vs_current(target_price, current_price)
    if target_gap != "-":
        parts.append(f"target {target_gap}")
    if result.get("rr_tier_note"):
        parts.append(str(result["rr_tier_note"]))
    if result.get("error"):
        parts.append(str(result.get("error")))
    return "; ".join(part for part in parts if part and part != "-") or "-"


def _final_selection_label(result: dict[str, Any], *, selected: bool) -> Text:
    try:
        if result.get("error"):
            return Text("Analysis Error", style="danger")
        verdict = (
            result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
        )
        rating = str(verdict.get("rating") or "").upper()
        risk = (
            result.get("risk_governor")
            if isinstance(result.get("risk_governor"), dict)
            else {}
        )
        risk_status = str(risk.get("status") or "").lower()
        reason_codes = risk.get("reason_codes")
        if isinstance(reason_codes, list) and "market_regime_defensive" in reason_codes:
            return Text("No Sizing", style="warn")
        if risk_status == "conditional_deployable":
            return Text("Conditional", style="warn")
        if rating == "HOLD":
            return Text("Watchlist", style="warn")
        if rating in {"AVOID", "SELL"}:
            return Text("Avoid", style="danger")
        if rating in {"BUY", "STRONG_BUY"}:
            if risk_status == "deployable":
                return Text("Enter Now", style="ok")
            if risk_status == "wait_for_pullback":
                return Text("Watch Entry", style="warn")
            if risk_status == "watchlist_only":
                return Text("Watchlist", style="warn")
            if selected:
                return Text("Enter Now", style="ok")
        if selected:
            return Text("Enter Now", style="ok")
        return Text("Exclude", style="muted")
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
        return Text(_short_err(_exclusion_reason(result)), style="muted")


def _format_overrides(regime_params: dict[str, Any]) -> str:
    if not regime_params:
        return "No overrides applied"
    return ", ".join(f"{key}={value}" for key, value in regime_params.items())


def _format_regime_reasons(reasons: Any) -> str:
    if not isinstance(reasons, list):
        return "-"
    labels = [_reason_token_label(str(reason)) for reason in reasons if str(reason)]
    return ", ".join(labels) or "-"


def _market_regime_summary_label(
    snapshot: Any,
    *,
    fallback: str,
) -> str:
    if not isinstance(snapshot, dict):
        return fallback
    regime = str(snapshot.get("execution_regime") or snapshot.get("regime") or fallback)
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
    reasons = _format_regime_reasons(snapshot.get("reasons"))
    if reasons != "-":
        parts.append(reasons)
    return " | ".join(parts)


def _ticker_list(entries: list[dict[str, Any]]) -> list[str]:
    tickers: list[str] = []
    for entry in entries:
        ticker = entry.get("ticker") or (entry.get("verdict") or {}).get("ticker")
        if ticker:
            tickers.append(str(ticker))
    return tickers


_cli_renderer = CliRenderer()
_rich_log_sink = RichLogSink(_cli_renderer)
_CLI_LOGGING_CONFIGURED = False


def _normalize_log_level(value: str | None, default: str = "INFO") -> str:
    level = str(value or "").strip().upper()
    if not level:
        return default
    try:
        logger.level(level)
    except ValueError:
        return default
    return level


def configure_cli_logging(*, verbose: bool = False) -> None:
    """Route Loguru to files and mirror structured Rich output to the console."""
    global _CLI_LOGGING_CONFIGURED
    _ensure_utf8_stdout()
    _cli_renderer.verbose = verbose
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
    logger.add(_rich_log_sink, level=log_level)
    if verbose:
        logger.add(
            sys.stderr,
            format=settings.LOG_FORMAT,
            level=log_level,
            colorize=True,
        )
    _CLI_LOGGING_CONFIGURED = True


@contextmanager
def _pipeline_file_logging_only():
    """
    Defer structured console log rendering while Rich Live owns the terminal.

    Rich Live/Progress redraws the terminal frequently. Console log sinks can
    corrupt that display, so the debate phase stores structured log events and
    flushes them after the live table closes. File sinks remain active.
    """
    with _cli_renderer.defer_logs():
        yield


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Weights dibaca dari settings (env-configurable) untuk menghindari hardcode.
# ORCHESTRATOR_CONFIG bersifat mutable agar regime override bisa di-apply
# di main() sebelum pipeline dijalankan.


def _active_provider_prefix() -> str:
    provider = str(getattr(settings, "DEFAULT_LLM_PROVIDER", "") or "gemini")
    return provider.strip().upper()


def _provider_env_key(suffix: str) -> str:
    return f"{_active_provider_prefix()}_{suffix}"


def _provider_env_value(suffix: str, fallback_key: str, default: str) -> str:
    provider_value = os.getenv(_provider_env_key(suffix))
    if provider_value is not None and provider_value.strip():
        return provider_value
    return os.getenv(fallback_key, default)


def _provider_env_int(suffix: str, fallback_key: str, default: int) -> int:
    return int(_provider_env_value(suffix, fallback_key, str(default)))


def _provider_env_float(suffix: str, fallback_key: str, default: float) -> float:
    return float(_provider_env_value(suffix, fallback_key, str(default)))


def _provider_specific_env_exists(suffix: str) -> bool:
    value = os.getenv(_provider_env_key(suffix))
    return value is not None and value.strip() != ""


def _runtime_max_concurrent_debates() -> int:
    return _provider_env_int(
        "MAX_CONCURRENT_DEBATES",
        "MAX_CONCURRENT_DEBATES",
        5,
    )


def _runtime_rpm_limit() -> int:
    return _provider_env_int("RPM_LIMIT", "GEMINI_RPM_LIMIT", 30)


def _runtime_batch_delay() -> float:
    return _provider_env_float("BATCH_DELAY_SECONDS", "BATCH_DELAY_SECONDS", 0.2)


ORCHESTRATOR_CONFIG: dict[str, Any] = {
    "conviction_weights": {
        "confidence": settings.CONVICTION_WEIGHT_CONFIDENCE,
        "rr_ratio": settings.CONVICTION_WEIGHT_RR_RATIO,
    },
    "rr_normalization_cap": settings.CONVICTION_RR_NORMALIZATION_CAP,
    "max_concurrent_debates": _runtime_max_concurrent_debates(),
    "excluded_ratings": {"AVOID", "HOLD", "SELL", "INSUFFICIENT_DATA"},
    "top_n_selection": int(os.getenv("TOP_N_SELECTION", "3")),
    "max_price_retry_attempts": int(os.getenv("MAX_PRICE_RETRY_ATTEMPTS", "3")),
    "rpm_limit": _runtime_rpm_limit(),
    "batch_delay": _runtime_batch_delay(),
    # Diisi oleh regime detection di main()
    "min_conviction_override": settings.PORTFOLIO_MIN_CONVICTION,
    "market_regime": None,
    "hmm_regime": None,
    "regime_context": None,
}


def _orchestrator_runtime_defaults() -> dict[str, Any]:
    return {
        "rr_normalization_cap": settings.CONVICTION_RR_NORMALIZATION_CAP,
        "max_concurrent_debates": _runtime_max_concurrent_debates(),
        "top_n_selection": int(os.getenv("TOP_N_SELECTION", "3")),
        "rpm_limit": _runtime_rpm_limit(),
        "batch_delay": _runtime_batch_delay(),
        "min_conviction_override": settings.PORTFOLIO_MIN_CONVICTION,
        "market_regime": None,
        "hmm_regime": None,
        "regime_context": None,
    }


def _reset_orchestrator_runtime_config() -> None:
    """Clear per-run regime overrides before a new orchestrator invocation."""
    ORCHESTRATOR_CONFIG.update(_orchestrator_runtime_defaults())


def _apply_regime_params(regime_params: dict[str, Any]) -> None:
    provider_rpm_pinned = _provider_specific_env_exists("RPM_LIMIT")
    for key in ("top_n_selection", "rpm_limit", "rr_normalization_cap"):
        if key not in regime_params:
            continue
        if key == "rpm_limit" and provider_rpm_pinned:
            logger.info(
                "[Regime] Skipping rpm_limit override because "
                f"{_provider_env_key('RPM_LIMIT')} is set."
            )
            continue
        ORCHESTRATOR_CONFIG[key] = regime_params[key]
    if "min_conviction_override" in regime_params:
        ORCHESTRATOR_CONFIG["min_conviction_override"] = regime_params[
            "min_conviction_override"
        ]


def get_regime_params(regime: str) -> dict[str, Any]:
    """Compatibility facade; new execution code uses regime_context instead."""
    return dict(_get_legacy_regime_params(regime))


OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
JSON_PATH = OUTPUT_DIR / "top10_candidates.json"
FULL_RESULTS_PATH = OUTPUT_DIR / "full_batch_results.json"
MERGED_RESULTS_PATH = OUTPUT_DIR / "merged_batch_results.json"
TOP3_REPORT_PATH = OUTPUT_DIR / "TOP_3_SWING_TRADES.md"

# Shorthand aliases â€" baca dari ORCHESTRATOR_CONFIG agar konsisten dengan
# regime override yang dilakukan di main() sebelum pipeline jalan.
EXCLUDED_RATINGS: set[str] = ORCHESTRATOR_CONFIG["excluded_ratings"]
# TOP_N_SELECTION dan MAX_CONCURRENT_DEBATES dibaca dinamis dari ORCHESTRATOR_CONFIG
# di dalam fungsi agar regime override yang dilakukan di main() terlihat.

# IDX saham biasa: tepat 4 huruf kapital, opsional suffix .JK
# Catatan: warrant/right issue (5 huruf) sengaja dikecualikan dari scope ini.
TICKER_PATTERN = IDX_TICKER_PATTERN
PROMPT_MANIFEST_PATH = "services/debate_prompts/manifest.json"
CLI_TICKERS_OVERRIDE: list[str] | None = None
CLI_MODE: str = "multi"
# Screener strategy used when the orchestrator (re)runs the quant filter:
# "momentum" (default, trend-following) or "mean_reversion" (oversold pullbacks).
CLI_SCREENER_MODE: str = "momentum"


def _ledger_call(operation: str, func, *args, **kwargs) -> None:
    try:
        func(*args, **kwargs)
    except Exception as exc:
        logger.warning(f"[ExecutionLedger] {operation} failed: {exc}")


def _ledger_emit_event(event: LedgerEvent) -> None:
    _ledger_call("ledger event emit", DEFAULT_LEDGER.emit, event)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_prompt_pack_linter() -> bool:
    try:
        lint = lint_prompt_pack(PROMPT_MANIFEST_PATH)
        if not lint.valid:
            for err in lint.errors:
                logger.error(f"[PromptLinter] {err}")
            raise SystemExit(
                "Prompt pack validation failed. Fix errors before running."
            )
        for warning in lint.warnings:
            logger.warning(f"[PromptLinter] {warning}")
        logger.info("[PromptLinter] Prompt pack OK")
        return True
    except SystemExit:
        raise
    except Exception as e:
        logger.warning(f"[PromptLinter] Linter failed: {e}")
        return False


def _ledger_provider_check(run_id: str, provider_health) -> None:
    can_proceed = bool(getattr(provider_health, "can_proceed", False))
    failures = list(getattr(provider_health, "failures", []) or [])
    severity = EventSeverity.INFO
    if not can_proceed:
        severity = EventSeverity.CRITICAL
    elif failures:
        severity = EventSeverity.WARNING
    _ledger_emit_event(
        LedgerEvent(
            event_id=uuid4().hex[:8],
            run_id=run_id,
            ticker=None,
            stage="PROVIDER_HEALTH",
            event_type=EventType.PROVIDER_CHECK,
            severity=severity,
            message="Provider health checked",
            detail={
                "can_proceed": can_proceed,
                "stockbit_ok": bool(getattr(provider_health, "stockbit_ok", False)),
                "yfinance_ok": bool(getattr(provider_health, "yfinance_ok", False)),
                "failures": failures,
            },
            duration_ms=None,
            attempt=0,
            timestamp=_now_iso(),
        )
    )


def _ledger_artifact_write(
    *,
    run_id: str,
    artifact: str,
    path: Path,
    ticker_count: int,
) -> None:
    _ledger_emit_event(
        LedgerEvent(
            event_id=uuid4().hex[:8],
            run_id=run_id,
            ticker=None,
            stage="ARTIFACT_WRITE",
            event_type=EventType.ARTIFACT_WRITE,
            severity=EventSeverity.INFO,
            message=f"Artifact written: {artifact}",
            detail={
                "artifact": artifact,
                "path": str(path),
                "ticker_count": ticker_count,
            },
            duration_ms=None,
            attempt=0,
            timestamp=_now_iso(),
        )
    )


def _plan_orchestrator_decision(
    *,
    ticker: str | None,
    run_id: str,
    stage: PipelineStage,
    attempt: int = 0,
    failure_record: dict[str, Any] | None = None,
    provider_health: dict[str, Any] | None = None,
):
    """Run adaptive planner safely from orchestrator boundary code."""
    try:
        ctx = PlannerContext(
            ticker=ticker,
            run_id=run_id,
            stage=stage,
            attempt=attempt,
            failure_record=failure_record,
            provider_health=provider_health,
            observations_count=0,
            batch_failed_count=0,
        )
        decision = DEFAULT_PLANNER.plan(ctx)
        DEFAULT_PLANNER.log_decision(decision)
        logger.info(f"[Planner] {DEFAULT_PLANNER.format_decision(decision)}")
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


def configure_output_dir(output_dir: Path) -> None:
    """Update module-level output paths from CLI/env configuration."""
    global \
        OUTPUT_DIR, \
        JSON_PATH, \
        FULL_RESULTS_PATH, \
        MERGED_RESULTS_PATH, \
        TOP3_REPORT_PATH
    OUTPUT_DIR = output_dir
    JSON_PATH = OUTPUT_DIR / "top10_candidates.json"
    FULL_RESULTS_PATH = OUTPUT_DIR / "full_batch_results.json"
    MERGED_RESULTS_PATH = OUTPUT_DIR / "merged_batch_results.json"
    TOP3_REPORT_PATH = OUTPUT_DIR / "TOP_3_SWING_TRADES.md"


def _candidate_cache_context_mismatch(
    *,
    cached_mode: str | None,
    requested_mode: str,
    cached_execution_regime: str | None,
    execution_regime: str,
) -> bool:
    """Fail closed when cached candidates were produced under another context."""
    return cached_mode != requested_mode or cached_execution_regime != execution_regime


def _prompt_user_config() -> dict:
    """Tanya input modal, max loss, max posisi ke user via terminal."""
    _ensure_utf8_stdout()
    console.print()
    console.print(
        Panel(
            "IHSG Swing Trade – Position Sizing Setup",
            border_style="cyan",
            expand=False,
        )
    )

    while True:
        try:
            capital_raw = Prompt.ask("Modal total (Rp)", console=console)
            capital = float(capital_raw.replace(",", "").replace(".", ""))
            if capital > 0:
                break
            console.print("[warn]Modal harus lebih dari 0.[/warn]")
        except ValueError:
            console.print("[warn]Masukkan angka tanpa huruf.[/warn]")

    while True:
        try:
            raw = Prompt.ask(
                "Max loss per trade (%, default 2)", default="", console=console
            ).strip()
            max_loss = float(raw) / 100 if raw else 0.02
            if 0 < max_loss <= 0.10:
                break
            console.print("[warn]Max loss harus antara 0.1% - 10%.[/warn]")
        except ValueError:
            console.print("[warn]Masukkan angka, contoh: 2[/warn]")

    while True:
        try:
            raw = Prompt.ask(
                "Max jumlah posisi (default 5)", default="", console=console
            ).strip()
            max_pos = int(raw) if raw else 5
            if 1 <= max_pos <= 20:
                break
            console.print("[warn]Max posisi harus antara 1 - 20.[/warn]")
        except ValueError:
            console.print("[warn]Masukkan angka bulat, contoh: 5[/warn]")

    console.print(
        f"[ok]OK[/ok] Modal: Rp {capital:,.0f} | "
        f"Max loss: {max_loss * 100:.1f}% | Max posisi: {max_pos}"
    )

    return {
        "total_capital": capital,
        "max_loss_pct": max_loss,
        "max_positions": max_pos,
    }


# ---------------------------------------------------------------------------
# [FIX-1, FIX-2] SafeRateLimiter â€" sliding window, lock-safe
# ---------------------------------------------------------------------------


class SafeRateLimiter:
    """
    Sliding window rate limiter yang bebas race condition.

    Prinsip desain:
    - Lock HANYA dipegang saat membaca/menulis _tokens (shared state).
    - Sleep SELALU dilakukan di luar lock sehingga CancelledError tidak
      bisa menyebabkan lock leak.
    - Monotonic clock (get_event_loop().time()) digunakan agar window tidak
      melompat akibat NTP sync atau perubahan DST.
    - Loop while True + re-check setelah sleep menangani thundering herd:
      jika banyak task selesai sleep bersamaan, hanya satu yang mendapat
      slot; sisanya loop ulang dan menghitung wait_time baru.
    """

    def __init__(self, rate_limit: int, period_seconds: float = 60.0) -> None:
        self.rate_limit = rate_limit
        self.period = period_seconds
        self._tokens: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Blok sampai ada slot tersedia. Aman terhadap task cancellation."""
        while True:
            async with self._lock:
                # [FIX-2] now dihitung di dalam lock agar cutoff konsisten
                # dengan isi _tokens yang dibaca di baris berikutnya.
                now = asyncio.get_event_loop().time()
                cutoff = now - self.period

                self._tokens = [t for t in self._tokens if t > cutoff]

                if len(self._tokens) < self.rate_limit:
                    self._tokens.append(now)
                    return  # Slot tersedia â€" keluar, lock dilepas oleh context manager

                # Hitung berapa lama sampai token tertua kadaluarsa.
                # _tokens terurut secara implisit karena selalu di-append dengan
                # timestamp yang monotonically increasing.
                wait_time = self._tokens[0] + self.period - now

            # [FIX-1] Lock sudah dilepas oleh `async with` di atas.
            # Sleep di luar lock â€" CancelledError di sini tidak menyentuh lock.
            if wait_time > 0:
                logger.debug(f"[RateLimiter] Slot penuh, menunggu {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
            # Loop ulang untuk re-check (thundering herd safety)


# ---------------------------------------------------------------------------
# Step 1: Parse JSON
# ---------------------------------------------------------------------------


def validate_ticker(ticker: str) -> bool:
    """
    Validasi format ticker IDX.

    Menerima: "ERAA", "ERAA.JK" (akan di-uppercase sebelum validasi).
    Menolak: string kosong, karakter non-alfabet, panjang selain 4 huruf.
    """
    try:
        normalize_idx_ticker(ticker)
    except (InvalidIDXTicker, TypeError):
        return False
    return True


def _normalize_cli_tickers(tickers: list[str]) -> list[str]:
    return normalize_idx_tickers(tickers)


def _ticker_artifact_path(output_dir: Path, ticker: str, *parts: str) -> Path:
    canonical_ticker = normalize_idx_ticker(ticker)
    return resolve_within_root(output_dir, "debates", canonical_ticker, *parts)


def _load_quant_candidates(json_path: Path = JSON_PATH) -> list[dict]:
    """
    Baca kandidat hasil quant filter sebagai list dict mentah.
    """
    safe_path = resolve_within_root(json_path.parent, json_path.name)
    if not safe_path.exists():
        raise FileNotFoundError(
            f"Candidates tidak ditemukan di {safe_path}. "
            "Jalankan run_quant_filter.py terlebih dahulu."
        )

    data = json.loads(safe_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(
            f"Format candidates tidak valid di {safe_path}: expected list."
        )
    return [row for row in data if isinstance(row, dict)]


def _candidate_ticker(candidate: dict) -> str:
    raw = candidate.get("ticker") or candidate.get("Ticker") or ""
    if not isinstance(raw, str):
        return ""
    try:
        return normalize_idx_ticker(raw)
    except InvalidIDXTicker:
        # Preserve the untrusted value as data so candidate intake can emit an
        # explicit rejection. Filesystem writers revalidate before any I/O.
        return raw.strip()


def _candidate_for_intake(candidate: dict) -> dict:
    """Add common quant-filter aliases before running the strict intake normalizer."""
    return {
        **candidate,
        "ticker": _candidate_ticker(candidate),
        "price": (
            candidate.get("price")
            or candidate.get("Current Price")
            or candidate.get("current_price")
            or candidate.get("last_price")
            or candidate.get("close")
        ),
        "market_cap": candidate.get("market_cap") or candidate.get("Market Cap"),
        "sector": (
            candidate.get("sector")
            or candidate.get("Sektor Key")
            or candidate.get("Sektor")
            or candidate.get("Sector")
        ),
        "source": candidate.get("source") or "quant_filter",
    }


def _candidate_intake_terminal_result(
    candidate: dict[str, Any],
    *,
    error: str,
) -> dict[str, Any]:
    result = _pre_cio_terminal_result(
        candidate,
        reason_code="candidate_intake_invalid",
        reason=f"Candidate intake rejected: {error}",
    )
    result["decision_source"] = "preflight"
    result["execution_status"] = "INSUFFICIENT_DATA"
    result["verdict"]["decision_source"] = "preflight"
    result["verdict"]["execution_status"] = "INSUFFICIENT_DATA"
    metadata = result["metadata"]
    metadata.pop("pre_cio_rejection", None)
    metadata["candidate_intake_rejection"] = {
        "reason_code": "candidate_intake_invalid",
        "reason": error,
        "technical_data_complete": False,
    }
    metadata["artifact_scope"] = "batch_only"
    metadata["raw_ticker"] = str(
        candidate.get("ticker") or candidate.get("Ticker") or ""
    )
    # Untrusted identity is retained only as inert audit data.  Primary/nested
    # ticker fields must never carry report, log, ledger, or path injection.
    result["ticker"] = None
    result["verdict"]["ticker"] = None
    result["risk_governor"]["ticker"] = None
    return result


def _apply_candidate_intake(
    candidates: list[dict],
    rejected_results: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """Validate candidate intake without changing the quant-filter payload shape."""
    normalized, rejected = normalize_batch(
        [_candidate_for_intake(c) for c in candidates]
    )
    for item in rejected:
        rejected_candidate = item.get("candidate", {})
        raw_ticker = (
            rejected_candidate.get("ticker") or rejected_candidate.get("Ticker") or ""
        )
        try:
            ticker = normalize_idx_ticker(str(raw_ticker))
        except InvalidIDXTicker:
            ticker = None
        if ticker is not None:
            decision = _plan_orchestrator_decision(
                ticker=ticker,
                run_id="candidate_intake",
                stage=PipelineStage.CANDIDATE_INTAKE,
            )
            if decision is not None and decision.action is PlanAction.SKIP_TICKER:
                logger.info(f"[CandidateIntake] Planner confirmed skip for {ticker}")
        logger.warning(
            "[CandidateIntake] Rejected {}: {}",
            ticker or "<invalid-ticker>",
            item.get("error"),
        )
        if rejected_results is not None:
            rejected_results.append(
                _candidate_intake_terminal_result(
                    rejected_candidate,
                    error=str(item.get("error") or "invalid candidate payload"),
                )
            )

    if not normalized:
        logger.warning(
            "[CandidateIntake] No candidates normalized; all candidates are "
            "preserved as terminal INSUFFICIENT_DATA outcomes."
        )
        return []

    valid_tickers = {candidate.ticker for candidate in normalized}
    filtered: list[dict] = []
    seen_tickers: set[str] = set()
    for candidate in candidates:
        ticker = _candidate_ticker(candidate)
        if ticker not in valid_tickers or ticker in seen_tickers:
            continue
        canonical_candidate = dict(candidate)
        canonical_candidate["Ticker"] = ticker
        if "ticker" in canonical_candidate:
            canonical_candidate["ticker"] = ticker
        filtered.append(canonical_candidate)
        seen_tickers.add(ticker)
    logger.info(
        f"[CandidateIntake] {len(filtered)} valid candidates, {len(rejected)} rejected."
    )
    return filtered


def _apply_critical_risk_filter(
    candidates: list[dict],
    rejected_results: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """Preserve parser-level critical-risk skips as explicit terminal outcomes."""
    accepted: list[dict] = []
    for candidate in candidates:
        strategy = str(candidate.get("Entry Strategy") or "")
        if "critical risk" not in strategy.lower():
            accepted.append(candidate)
            continue
        ticker = _candidate_ticker(candidate) or "UNKNOWN"
        reason = "Quant filter marked Entry Strategy as critical risk."
        logger.warning(f"[Parser] {ticker} – Critical Risk flag, terminal NO_TRADE")
        if rejected_results is not None:
            rejected_results.append(
                _pre_cio_terminal_result(
                    candidate,
                    reason_code="critical_risk_flag",
                    reason=reason,
                )
            )
    return accepted


def _candidate_exdate_days(candidate: dict) -> int | None:
    raw = (
        candidate.get("exdate_days_remaining")
        if candidate.get("exdate_days_remaining") is not None
        else candidate.get("days_until_exdate")
    )
    if raw is None and isinstance(candidate.get("_exdate_info"), dict):
        raw = candidate["_exdate_info"].get("days_until_exdate")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _candidate_ma200_context(candidate: dict) -> str:
    return str(
        candidate.get("ma200_context") or candidate.get("MA200 Context") or ""
    ).upper()


def _candidate_snapshot_reference(candidate: dict[str, Any]) -> dict[str, str] | None:
    """Return the persisted MarketSnapshot reference carried by a filter row."""
    nested = (
        candidate.get("market_snapshot")
        if isinstance(candidate.get("market_snapshot"), dict)
        else {}
    )
    snapshot_id = candidate.get("snapshot_id") or nested.get("snapshot_id")
    data_hash = candidate.get("data_hash") or nested.get("data_hash")
    snapshot_path = (
        candidate.get("snapshot_path")
        or nested.get("artifact_path")
        or nested.get("snapshot_path")
    )
    requested_end = candidate.get("requested_end") or nested.get("requested_end")
    if not all((snapshot_id, data_hash, snapshot_path)):
        return None
    reference = {
        "snapshot_id": str(snapshot_id),
        "data_hash": str(data_hash),
        "snapshot_path": str(snapshot_path),
    }
    if requested_end:
        reference["requested_end"] = str(requested_end)
    return reference


def _candidate_file_has_snapshot_contract(
    path: Path,
    *,
    expected_requested_end: date | None = None,
) -> bool:
    """Require a cross-process OHLC handoff before reusing candidate cache."""
    from utils.market_snapshot import IDX_TIMEZONE

    try:
        candidates = _load_quant_candidates(path)
    except (FileNotFoundError, ValueError, OSError):
        return False
    expected = expected_requested_end or datetime.now(IDX_TIMEZONE).date()
    return bool(candidates) and all(
        (
            (reference := _candidate_snapshot_reference(candidate)) is not None
            and reference.get("requested_end") == expected.isoformat()
        )
        for candidate in candidates
    )


async def _seed_candidate_market_snapshots(
    candidates: list[dict[str, Any]],
    *,
    output_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Integrity-check filter artifacts and seed the debate process cache.

    Candidate paths are constrained to output_dir. Any missing, corrupt, or
    mismatched artifact fails closed so the debate cannot silently download a
    different OHLC frame than the filter used.
    """
    from utils.market_data_cache import seed_market_snapshots
    from utils.market_snapshot import load_market_snapshot

    snapshots = []
    provenance: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        ticker = _candidate_ticker(candidate) or "UNKNOWN"
        reference = _candidate_snapshot_reference(candidate)
        if reference is None:
            raise ValueError(
                f"{ticker}: candidate is missing snapshot_id/data_hash/snapshot_path"
            )
        raw_path = Path(reference["snapshot_path"])
        resolved_path = resolve_within_root(output_dir, raw_path)
        root = output_dir.resolve()
        snapshot = load_market_snapshot(
            resolved_path,
            expected_snapshot_id=reference["snapshot_id"],
            expected_data_hash=reference["data_hash"],
        )
        if snapshot.ticker != ticker:
            raise ValueError(f"{ticker}: snapshot ticker mismatch ({snapshot.ticker})")
        snapshots.append(snapshot)
        provenance[ticker] = snapshot.provenance(
            artifact_path=str(resolved_path.relative_to(root))
        )

    await seed_market_snapshots(snapshots)
    logger.info(
        "[MarketSnapshot] Seeded {} verified filter snapshots into debate cache.",
        len(snapshots),
    )
    return provenance


def _pre_cio_terminal_result(
    candidate: dict[str, Any],
    *,
    reason_code: str,
    reason: str,
) -> dict[str, Any]:
    ticker = _candidate_ticker(candidate) or "UNKNOWN"
    current_price = candidate.get("Current Price") or candidate.get("current_price")
    market_snapshot = (
        candidate.get("market_snapshot")
        if isinstance(candidate.get("market_snapshot"), dict)
        else {}
    )
    metadata: dict[str, Any] = {
        "pre_cio_rejection": {
            "reason_code": reason_code,
            "reason": reason,
            "technical_data_complete": True,
        },
        "flash_calls": 0,
        "pro_calls": 0,
        "llm_calls": 0,
    }
    from services.signal_packet import (
        build_raw_signal_packet,
        finalize_execution_state,
        set_forecast_state,
    )

    signal_packet = build_raw_signal_packet(
        technical_indicators=None,
        candidate_context=candidate,
    )
    signal_packet = finalize_execution_state(
        signal_packet,
        actionable=False,
        reason_codes=[reason_code],
    )
    metadata["signal_packet"] = set_forecast_state(
        signal_packet,
        "SKIPPED_PRE_CIO",
    )
    if market_snapshot:
        metadata["market_snapshot"] = dict(market_snapshot)
        metadata["snapshot_id"] = market_snapshot.get("snapshot_id")
        metadata["data_hash"] = market_snapshot.get("data_hash")
    return {
        "ticker": ticker,
        "verdict": {
            "ticker": ticker,
            "rating": "HOLD",
            "confidence": 0.0,
            "model_rating": None,
            "model_confidence": None,
            "policy_confidence": 1.0,
            "decision_source": "risk_guard",
            "execution_status": "NO_TRADE",
            "current_price": current_price,
            "entry_price_range": None,
            "target_price": None,
            "stop_loss": None,
            "reason_codes": [reason_code],
            "weighted_reasoning": reason,
            "summary": reason,
        },
        "metadata": metadata,
        "risk_governor": {
            "ticker": ticker,
            "status": "reject",
            "sizing_allowed": False,
            "reason_codes": [reason_code],
            "message": reason,
        },
        "decision_source": "risk_guard",
        "execution_status": "NO_TRADE",
        "reason_codes": [reason_code],
        "debate_rounds": 0,
        "debate_history": [],
        "error": None,
        "status": "success",
    }


def _apply_pre_cio_filters(
    candidates: list[dict],
    regime: str,
    rejected_results: list[dict[str, Any]] | None = None,
) -> list[dict]:
    """
    Hard filter sebelum masuk CIO â€" buang kandidat yang tidak layak
    tanpa membuang LLM token untuk mereka.
    """
    filtered = []
    for c in candidates:
        ticker = _candidate_ticker(c) or str(
            c.get("ticker") or c.get("Ticker") or "UNKNOWN"
        )

        # ExDate hard disqualifier (redundant safety net di Python level)
        exdate_days = _candidate_exdate_days(c)
        if exdate_days is not None and exdate_days <= 7:
            logger.info(f"[PreCIO] {ticker} SKIP – ExDate {exdate_days}d")
            if rejected_results is not None:
                rejected_results.append(
                    _pre_cio_terminal_result(
                        c,
                        reason_code="exdate_imminent",
                        reason=(
                            f"Ex-date dalam {exdate_days} hari; setup tidak "
                            "boleh masuk full debate."
                        ),
                    )
                )
            continue

        # Counter-trend under canonical DEFENSIVE execution is not debateable.
        if regime == "DEFENSIVE" and _candidate_ma200_context(c) == "BELOW":
            logger.info(
                f"[PreCIO] {ticker} SKIP – counter-trend di DEFENSIVE execution regime"
            )
            if rejected_results is not None:
                rejected_results.append(
                    _pre_cio_terminal_result(
                        c,
                        reason_code="counter_trend_defensive",
                        reason=(
                            "Harga berada di bawah MA200 saat execution regime "
                            "DEFENSIVE; setup ditahan sebelum full debate."
                        ),
                    )
                )
            continue

        filtered.append(c)

    return filtered


def parse_report(
    json_path: Path = JSON_PATH, candidates: list[dict] | None = None
) -> list[str]:
    """
    Baca top10_candidates.json dan kembalikan daftar ticker yang valid.

    Mengabaikan ticker dengan flag "critical risk" di kolom Entry Strategy.
    Menghapus duplikat setelah normalisasi uppercase.

    Raises:
        FileNotFoundError: File JSON tidak ditemukan.
        ValueError: Tidak ada ticker valid setelah filtering.
    """
    data = candidates if candidates is not None else _load_quant_candidates(json_path)
    tickers: list[str] = []
    seen: set[str] = set()

    # [FIX-9] `for row in data` â€" syntax error di versi sebelumnya diperbaiki.
    for row in data:
        raw = row.get("Ticker") or row.get("ticker") or ""
        try:
            ticker = normalize_idx_ticker(raw)
        except InvalidIDXTicker:
            ticker = ""

        if not validate_ticker(ticker):
            logger.warning(f"[Parser] Format ticker tidak valid: '{raw}' – dilewati")
            continue

        if ticker in seen:
            logger.debug(f"[Parser] Duplikat: {ticker} – dilewati")
            continue

        if "critical risk" in row.get("Entry Strategy", "").lower():
            logger.warning(f"[Parser] {ticker} – Critical Risk flag, dilewati")
            continue

        seen.add(ticker)
        tickers.append(ticker)

    if not tickers:
        raise ValueError("Tidak ada ticker valid setelah parsing dan filtering.")

    logger.info(f"[Parser] {len(tickers)} ticker diekstrak: {tickers}")
    return tickers


def parse_sector_map(
    json_path: Path = JSON_PATH,
    candidates: list[dict] | None = None,
) -> dict[str, str]:
    """
    Baca sector_key dari top10_candidates.json.

    Mengembalikan dict {ticker: sector_key} untuk portfolio_optimizer.
    Field "Sektor Key" adalah output dari run_quant_filter.py.
    Tidak raise â€" file hilang/corrupt hanya menghasilkan dict kosong.
    """
    if candidates is None and not json_path.exists():
        logger.warning(
            "[Parser] Sector map: file tidak ditemukan, sector_key 'unknown'."
        )
        return {}

    sector_map: dict[str, str] = {}
    data = candidates if candidates is not None else _load_quant_candidates(json_path)
    for row in data:
        raw = row.get("Ticker") or row.get("ticker") or ""
        try:
            ticker = normalize_idx_ticker(raw)
        except InvalidIDXTicker:
            ticker = ""
        if validate_ticker(ticker):
            sector_map[ticker] = str(row.get("Sektor Key", "unknown") or "unknown")

    logger.info(f"[Parser] Sector map: {len(sector_map)} ticker.")
    return sector_map


# ---------------------------------------------------------------------------
# Price fetcher
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(ORCHESTRATOR_CONFIG["max_price_retry_attempts"]),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def fetch_price_with_retry(ticker: str) -> float:
    """
    Ambil harga terkini dengan exponential backoff retry.

    Raises:
        ValueError: Harga 0.0 setelah semua retry habis.
    """
    price = await fetch_current_price(ticker)
    if price == 0.0:
        raise ValueError(f"Harga 0 untuk {ticker}")
    return price


# ---------------------------------------------------------------------------
# Step 2: Batch debate runner
# ---------------------------------------------------------------------------


def _empty_result(
    ticker: str,
    error: str,
    sector_key: str = "unknown",
    *,
    status: str = "failed",
    failure_stage: str | None = None,
    failure_type: str | None = None,
) -> dict:
    """
    Bentuk seragam untuk debate yang gagal atau di-abort.

    [FIX-10] Status default "failed". Pass status="timeout" agar telemetry
    dapat membedakan timeout dari genuine failure.
    """
    regime_context = dict(ORCHESTRATOR_CONFIG.get("regime_context") or {})
    hmm_regime = dict(ORCHESTRATOR_CONFIG.get("hmm_regime") or {})
    metadata: dict[str, Any] = {
        "execution_regime": regime_context.get("execution_regime"),
        "execution_regime_reason": regime_context.get("execution_regime_reason"),
    }
    if failure_stage:
        metadata["failure_stage"] = failure_stage
    if failure_type:
        metadata["failure_type"] = failure_type

    return {
        "ticker": ticker,
        "verdict": {},
        "debate_rounds": 0,
        "consensus_reached": False,
        "consensus_method": None,
        "dissenting_agents": [],
        "agent_votes": [],
        "disagreement_type": None,
        "debate_history": [],
        "raw_data_summary": "",
        "metadata": metadata,
        "regime_context": regime_context,
        "hmm_regime": hmm_regime,
        "trend_regime": regime_context.get("trend_regime"),
        "volatility_regime": regime_context.get("volatility_regime"),
        "execution_regime": regime_context.get("execution_regime"),
        "execution_regime_reason": regime_context.get("execution_regime_reason"),
        "trading_params": regime_context.get("execution_params", {}),
        "error": error,
        "status": status,
        "conviction_score": 0.0,
        "sector_key": sector_key,
    }


def _exception_message(exc: BaseException) -> str:
    text = str(exc).strip()
    if text:
        return text
    if getattr(exc, "args", None):
        return repr(exc.args)
    return type(exc).__name__


def _exception_failure_result(
    ticker: str,
    exc: BaseException,
    *,
    stage: str,
    sector_key: str = "unknown",
    prefix: str | None = None,
) -> dict:
    failure_type = type(exc).__name__
    message = _exception_message(exc)
    error = f"{prefix}: {message}" if prefix else f"{failure_type}: {message}"
    result = _empty_result(
        ticker,
        error,
        sector_key,
        failure_stage=stage,
        failure_type=failure_type,
    )
    result["metadata"]["failure_message"] = message
    return result


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_batch_only_result(result: dict[str, Any]) -> bool:
    return _dict_or_empty(result.get("metadata")).get("artifact_scope") == "batch_only"


def _canonicalize_result_for_artifact(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a persistence-safe result without exposing rejected raw identity.

    Returns None when the result's ticker identity cannot be canonicalized
    (missing or conflicting nested tickers); the caller drops it instead of
    aborting the whole artifact for every other, valid result in the batch.
    """

    if not _is_batch_only_result(result):
        try:
            return canonicalize_result_identity(result)
        except (InvalidIDXTicker, TypeError) as exc:
            logger.warning(
                "[Persist] reason_code=invalid_result_identity ticker={} detail={}",
                result.get("ticker") if isinstance(result, dict) else None,
                exc,
            )
            return None

    sanitized = dict(result)
    sanitized["ticker"] = None
    for key in ("verdict", "risk_governor", "execution_decision"):
        nested = result.get(key)
        if isinstance(nested, dict):
            sanitized[key] = {**nested, "ticker": None}
    return sanitized


def _canonicalize_results_for_artifact(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    canonicalized = (_canonicalize_result_for_artifact(result) for result in results)
    return [result for result in canonicalized if result is not None]


def _result_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    metadata = entry.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        entry["metadata"] = metadata
    return metadata


def _stamp_execution_regime_contract(result: dict[str, Any]) -> None:
    """Apply the batch execution authority to every result before scoring."""
    batch_context = dict(ORCHESTRATOR_CONFIG.get("regime_context") or {})
    result_context = result.get("regime_context")
    if not isinstance(result_context, dict) or not result_context.get(
        "execution_regime"
    ):
        result_context = batch_context
    else:
        result_context = dict(result_context)

    hmm_regime = result.get("hmm_regime")
    if not isinstance(hmm_regime, dict) or not hmm_regime:
        hmm_regime = dict(ORCHESTRATOR_CONFIG.get("hmm_regime") or {})

    result["regime_context"] = result_context
    result["hmm_regime"] = hmm_regime
    if not result.get("trend_regime"):
        result["trend_regime"] = result_context.get("trend_regime")
    if not result.get("volatility_regime"):
        result["volatility_regime"] = result_context.get("volatility_regime")
    if not result.get("execution_regime"):
        result["execution_regime"] = result_context.get("execution_regime")
    if not result.get("execution_regime_reason"):
        result["execution_regime_reason"] = result_context.get(
            "execution_regime_reason"
        )
    if not result.get("trading_params"):
        result["trading_params"] = result_context.get("execution_params", {})

    metadata = _result_metadata(result)
    if not metadata.get("execution_regime"):
        metadata["execution_regime"] = result.get("execution_regime")
    if not metadata.get("execution_regime_reason"):
        metadata["execution_regime_reason"] = result.get("execution_regime_reason")


def _parse_price_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    text = str(value).strip().replace("Rp", "").replace("rp", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "").replace(".", "")
    try:
        parsed = float(text)
        return parsed if math.isfinite(parsed) else None
    except ValueError:
        return None


def _parse_entry_low(entry_price_range: Any) -> float | None:
    entry_low = str(entry_price_range or "").split("-", maxsplit=1)[0].strip()
    return _parse_price_value(entry_low)


def _parse_entry_bounds(entry_price_range: Any) -> tuple[float | None, float | None]:
    try:
        parts = str(entry_price_range or "").replace("–", "-").split("-", maxsplit=1)
        low = _parse_price_value(parts[0].strip()) if parts else None
        high = _parse_price_value(parts[1].strip()) if len(parts) > 1 else low
        return low, high
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
        return None, None


def _coerce_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence):
        return None
    if confidence > 1.0:
        confidence = confidence / 100.0
    return max(0.0, min(confidence, 1.0))


FORECAST_RESEARCH_EV_DOWNWEIGHT = 0.35
FORECAST_FAILURE_REASONS = {
    "NOT_VALIDATED": "forecast_not_validated",
    "VALIDATION_FAILED": "forecast_validation_failed",
    "MODEL_FAILED": "forecast_model_failed",
    "ZERO_WEIGHT": "forecast_zero_weight",
    "UNAVAILABLE": "forecast_unavailable",
}


def _forecast_report_status(report_payload: dict[str, Any]) -> str | None:
    status = str(report_payload.get("forecast_status") or "").strip().upper()
    if status == "READY" or status in FORECAST_FAILURE_REASONS:
        return status
    return None


def _forecast_validation_status(report_payload: dict[str, Any]) -> str:
    validation = report_payload.get("validation_summary")
    if isinstance(validation, dict):
        status = str(validation.get("status") or "").strip().lower()
        if status in {"production", "research_only", "failed"}:
            return status
    flags = report_payload.get("data_quality_flags")
    if isinstance(flags, list):
        for flag in flags:
            text = str(flag).strip().lower()
            if text.startswith("validation_status:"):
                status = text.split(":", maxsplit=1)[1]
                if status in {"production", "research_only", "failed"}:
                    return status
    return "failed"


def _forecast_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _forecast_ranking_ev(
    report_payload: dict[str, Any],
) -> tuple[float | None, float | None, str | None]:
    forecast_status = _forecast_report_status(report_payload)
    if forecast_status is None:
        return None, None, "forecast_status_missing"
    if forecast_status != "READY":
        return None, None, FORECAST_FAILURE_REASONS[forecast_status]

    status = _forecast_validation_status(report_payload)
    ev = _forecast_float(report_payload.get("risk_adjusted_expected_value"))
    if status == "production":
        if ev is None:
            return None, None, "risk_adjusted_ev_missing"
        return ev, None, None
    if status == "research_only":
        if ev is None:
            return None, None, "risk_adjusted_ev_missing"
        return (
            ev * FORECAST_RESEARCH_EV_DOWNWEIGHT,
            FORECAST_RESEARCH_EV_DOWNWEIGHT,
            None,
        )
    return None, None, "validation_failed"


class SetupCoherenceError(ValueError):
    """Raised when a generated trade setup violates basic price geometry."""


def validate_setup_coherence(
    ticker: str,
    current_price: float,
    entry_low: float,
    entry_high: float,
    target: float,
    stop: float,
    yf_info: dict[str, Any] | None = None,
    execution_regime: str | None = None,
) -> None:
    """Raise SetupCoherenceError when a trade setup is not actionable."""
    if target <= entry_high:
        raise SetupCoherenceError(
            f"target ({target}) does not exceed top of entry range ({entry_high})"
        )
    if stop >= entry_low:
        raise SetupCoherenceError(
            f"stop ({stop}) is not below bottom of entry range ({entry_low})"
        )
    if current_price > entry_high * 1.10:
        raise SetupCoherenceError(
            f"current price ({current_price}) is more than 10% above entry range "
            f"top ({entry_high}). Setup is not actionable."
        )
    try:
        rr = calculate_rr(entry_high, target, stop)
    except ValueError as exc:
        raise SetupCoherenceError(
            f"stop ({stop}) is not below bottom of entry range ({entry_low})"
        ) from exc
    rr_resolution = get_required_rr_resolution(
        ticker,
        regime=execution_regime,
        yf_info=yf_info,
    )
    rr_minimum = rr_resolution.required_rr
    if rr < rr_minimum:
        raise SetupCoherenceError(
            f"R/R ({rr:.2f}x) below canonical threshold of {rr_minimum:.3f}x "
            f"({rr_resolution.tier_name} tier; execution regime "
            f"{rr_resolution.execution_regime} x"
            f"{rr_resolution.regime_multiplier:.2f}; user floor "
            f"{rr_resolution.user_execution_floor:.1f}x)"
        )


def extract_model_confidence(verdict: dict[str, Any]) -> float | None:
    """Return CIO model certainty on a 0.0-1.0 scale before R/R weighting."""
    if (
        str(verdict.get("decision_source") or "").lower() == "preflight"
        and verdict.get("model_confidence") is None
    ):
        return None
    return _coerce_confidence(
        verdict.get("model_confidence")
        if verdict.get("model_confidence") is not None
        else verdict.get("confidence")
    )


def _confidence_percent_label(confidence: float) -> str:
    """Format normalized confidence as an integer percent label for reason codes."""
    return str(int(round(confidence * 100)))


def _append_result_reason(result: dict[str, Any], reason: str) -> None:
    """Append a reason code to both top-level and verdict-level reason lists."""
    if not reason:
        return
    reasons = result.setdefault("reasons", [])
    if isinstance(reasons, list) and reason not in reasons:
        reasons.append(reason)
    verdict = result.get("verdict")
    if isinstance(verdict, dict):
        verdict_reasons = verdict.setdefault("reasons", [])
        if isinstance(verdict_reasons, list) and reason not in verdict_reasons:
            verdict_reasons.append(reason)


def _clear_numeric_setup_fields(verdict: dict[str, Any]) -> None:
    """Remove numeric setup levels while preserving the existing verdict payload shape."""
    verdict["entry_price_range"] = None
    verdict["entry_low"] = None
    verdict["entry_high"] = None
    verdict["target_price"] = None
    verdict["stop_loss"] = None
    verdict["risk_reward_ratio"] = None
    verdict["expected_return"] = None


def _set_reject_risk_payload(
    result: dict[str, Any],
    *,
    ticker: str,
    reason: str,
    message: str,
) -> None:
    """Attach a deterministic reject risk payload without invoking risk scoring."""
    result["risk_gov"] = "reject"
    result["risk_governor"] = {
        "ticker": ticker,
        "status": "reject",
        "sizing_allowed": False,
        "reason_codes": [reason],
        "message": message,
        "current_price": None,
        "entry_low": None,
        "entry_high": None,
        "target_price": None,
        "stop_loss": None,
    }


def _extract_rr_yf_info(result: dict[str, Any]) -> dict[str, Any] | None:
    """Return cached yfinance info or a minimal marketCap dict for R/R tiers."""
    market_data = result.get("market_data")
    if isinstance(market_data, dict):
        yf_info = market_data.get("info")
        if isinstance(yf_info, dict) and yf_info:
            return yf_info

    metadata = result.get("metadata")
    if isinstance(metadata, dict):
        for key in ("market_cap_idr", "market_cap", "marketCap"):
            market_cap = metadata.get(key)
            if market_cap not in (None, ""):
                return {"marketCap": market_cap}

    for key in ("market_cap_idr", "market_cap", "marketCap"):
        market_cap = result.get(key)
        if market_cap not in (None, ""):
            return {"marketCap": market_cap}
    return None


def _rr_tier_note(
    ticker: str,
    yf_info: dict[str, Any] | None = None,
    execution_regime: str | None = None,
) -> str:
    """Return the visible canonical R/R requirement and its inputs."""
    resolution = get_required_rr_resolution(
        ticker,
        regime=execution_regime,
        yf_info=yf_info,
    )
    return (
        f"Required R/R: {resolution.required_rr:.3f}x "
        f"(base {resolution.base_rr_minimum:.2f}x, {resolution.tier_label}, "
        f"{resolution.execution_regime} x{resolution.regime_multiplier:.2f}, "
        f"user floor {resolution.user_execution_floor:.1f}x)"
    )


def _annotate_rr_tier(
    result: dict[str, Any],
    ticker: str,
    yf_info: dict[str, Any] | None = None,
    execution_regime: str | None = None,
) -> None:
    """Attach canonical R/R requirement metadata to result and verdict."""
    yf_info = yf_info if yf_info is not None else _extract_rr_yf_info(result)
    execution_regime = execution_regime or execution_regime_from_payload(result) or None
    resolution = get_required_rr_resolution(
        ticker,
        regime=execution_regime,
        yf_info=yf_info,
    )
    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    metadata = (
        result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    )
    tier_payload = {
        "rr_tier": resolution.tier_name,
        "rr_minimum": resolution.required_rr,
        "required_rr": resolution.required_rr,
        "rr_base_minimum": resolution.base_rr_minimum,
        "rr_regime_minimum": resolution.regime_rr_minimum,
        "rr_user_floor": resolution.user_execution_floor,
        "rr_regime": resolution.execution_regime,
        "rr_regime_multiplier": resolution.regime_multiplier,
        "rr_tier_label": resolution.tier_label,
        "rr_tier_source": resolution.tier_source,
        "rr_requirement_source": "max_user_floor_tier_x_regime",
    }
    if resolution.market_cap_idr is not None:
        tier_payload["rr_market_cap_idr"] = resolution.market_cap_idr
    result.update(tier_payload)
    if verdict:
        verdict.update(tier_payload)
    metadata.update(tier_payload)
    result["metadata"] = metadata
    note = _rr_tier_note(
        ticker,
        yf_info=yf_info,
        execution_regime=execution_regime,
    )
    result["rr_tier_note"] = note
    if verdict:
        verdict["rr_tier_note"] = note


def _confidence_gate_should_skip(confidence: float | int) -> bool:
    """Return True when confidence is strictly below the setup threshold."""
    confidence_value = float(confidence)
    confidence_pct = (
        confidence_value * 100 if confidence_value <= 1 else confidence_value
    )
    return confidence_pct < MIN_CONFIDENCE_FOR_SETUP


def apply_minimum_confidence_gate(
    ticker: str,
    result: dict[str, Any],
    setup_generator: Callable[[], Any] | None = None,
) -> bool:
    """Skip setup generation when CIO confidence is below the production floor."""
    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    confidence = extract_model_confidence(verdict)
    if confidence is None:
        raise ValueError(f"{ticker}: confidence is missing and cannot be gated")
    if not _confidence_gate_should_skip(confidence):
        if setup_generator is not None:
            setup_generator()
        return False

    label = _confidence_percent_label(confidence)
    reason = f"confidence_{label}pct_below_minimum"
    logger.warning(
        f"[Gate] {ticker}: confidence {label}% below minimum "
        f"{MIN_CONFIDENCE_FOR_SETUP}%. Skipping setup generation."
    )
    verdict["rating"] = "INSUFFICIENT_DATA"
    verdict["action"] = "SKIP"
    _clear_numeric_setup_fields(verdict)
    result["sizing"] = "Skip — confidence below threshold"
    _append_result_reason(result, reason)
    _set_reject_risk_payload(
        result,
        ticker=ticker,
        reason=reason,
        message="Skip — confidence below threshold",
    )
    return True


def apply_setup_coherence_gate(ticker: str, result: dict[str, Any]) -> bool:
    """Reject incoherent setup levels before risk scoring and output formatting."""
    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    if not verdict or verdict.get("entry_price_range") in (None, ""):
        return False
    current_price = _parse_price_value(verdict.get("current_price"))
    entry_low, entry_high = _parse_entry_bounds(verdict.get("entry_price_range"))
    target = _parse_price_value(verdict.get("target_price"))
    stop = _parse_price_value(verdict.get("stop_loss"))
    if None in (current_price, entry_low, entry_high, target, stop):
        raise SetupCoherenceError(
            f"{ticker}: setup coherence cannot be validated because price fields are missing"
        )
    yf_info = _extract_rr_yf_info(result)
    execution_regime = execution_regime_from_payload(result) or None
    _annotate_rr_tier(
        result,
        ticker,
        yf_info=yf_info,
        execution_regime=execution_regime,
    )
    try:
        validate_setup_coherence(
            ticker,
            float(current_price),
            float(entry_low),
            float(entry_high),
            float(target),
            float(stop),
            yf_info=yf_info,
            execution_regime=execution_regime,
        )
        return False
    except SetupCoherenceError as exc:
        message = str(exc)
        logger.warning(f"[Coherence] {ticker}: {message}")
        verdict["rating"] = "AVOID"
        _clear_numeric_setup_fields(verdict)
        _append_result_reason(result, message)
        _set_reject_risk_payload(
            result,
            ticker=ticker,
            reason="setup_coherence_failed",
            message=message,
        )
        return True


def apply_extreme_overvaluation_flag(ticker: str, result: dict[str, Any]) -> bool:
    """Flag price-to-fair-value ratios where valuation assumptions may not hold."""
    verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
    current_price = _parse_price_value(verdict.get("current_price"))
    fair_value = _parse_price_value(verdict.get("fair_value"))
    if current_price is None or fair_value is None or fair_value <= 0:
        return False
    ratio = current_price / fair_value
    if ratio <= EXTREME_OVERVALUATION_THRESHOLD:
        return False

    flag = "EXTREME_OVERVALUATION"
    note = (
        f"Caution: price/FV ratio {ratio:.1f}x — valuation model assumptions "
        "may not hold for this stock type"
    )
    for container in (result, verdict):
        flags = container.setdefault("flags", [])
        if isinstance(flags, list) and flag not in flags:
            flags.append(flag)
        existing_note = str(container.get("note") or "").strip()
        container["note"] = f"{existing_note} {note}".strip()
    _append_result_reason(result, flag)
    _append_result_reason(result, "fair_value_model_may_not_apply")
    logger.warning(
        f"[FairValue] {ticker}: price/FV = {ratio:.1f}x exceeds threshold "
        f"{EXTREME_OVERVALUATION_THRESHOLD}x. Model reliability uncertain."
    )
    return True


def sync_metric_aliases(entry: dict[str, Any]) -> None:
    """Expose confidence and conviction under explicit, non-confusing names."""
    verdict = entry.get("verdict") if isinstance(entry.get("verdict"), dict) else {}
    model_confidence = extract_model_confidence(verdict)
    if model_confidence is not None:
        verdict["model_confidence"] = model_confidence
        entry["model_confidence"] = model_confidence
    trade_conviction = entry.get("trade_conviction", entry.get("conviction_score", 0.0))
    try:
        trade_conviction = float(trade_conviction or 0.0)
    except (TypeError, ValueError):
        trade_conviction = 0.0
    entry["trade_conviction"] = trade_conviction


def _merge_metadata_reasons(result: dict[str, Any]) -> None:
    """Copy reason metadata from debate nodes into the output reason list."""
    metadata = (
        result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    )
    for reason in metadata.get("reasons") or []:
        _append_result_reason(result, str(reason))
    if "evidence_age_h" in metadata:
        result["evidence_age_h"] = metadata.get("evidence_age_h")


def _result_status(entry: dict[str, Any]) -> str:
    status = str(entry.get("status") or "").strip().lower()
    if status in {"success", "timeout", "failed", "skipped"}:
        return status
    error = str(entry.get("error") or "")
    if error:
        return "timeout" if "timeout" in error.lower() else "failed"
    if entry.get("verdict"):
        return "success"
    return "failed"


# FIX: ISSUE 1 — Suppress unverified fair value in final trade displays.
def _valuation_gap_unverified(entry: dict[str, Any]) -> bool:
    verdict = _dict_or_empty(entry.get("verdict"))
    metadata = _dict_or_empty(entry.get("metadata"))
    return (
        str(verdict.get("valuation_gap") or "").lower() == "unverified"
        or str(entry.get("valuation_gap") or "").lower() == "unverified"
        or str(metadata.get("valuation_gap") or "").lower() == "unverified"
        or bool(metadata.get("fair_value_rejected"))
    )


def _fair_value_status(entry: dict[str, Any]) -> str | None:
    """Return only an explicitly persisted fair-value evaluation status."""
    verdict = _dict_or_empty(entry.get("verdict"))
    status = verdict.get("fair_value_status")
    if status == "NOT_EVALUATED_PREFLIGHT":
        return status
    return None


# FIX: ISSUE 3 — Extract breaking-news headlines for report display.
def _breaking_news_headlines_from_bundle(
    news_bundle: Any, limit: int = 3
) -> list[dict[str, str]]:
    headlines: list[dict[str, str]] = []
    for item in list(getattr(news_bundle, "items", []) or []):
        if not bool(getattr(item, "is_breaking", False)):
            continue
        title = str(getattr(item, "title", "") or "").strip()
        if not title:
            continue
        headlines.append(
            {
                "title": title,
                "source": str(getattr(item, "source", "") or "unknown"),
                "timestamp": str(getattr(item, "published_at", "") or "unknown"),
            }
        )
        if len(headlines) >= limit:
            break
    return headlines


def _attach_news_signal(ticker: str, result: dict[str, Any]) -> None:
    try:
        metadata = _result_metadata(result)
        debate_sentiment = metadata.get("news_overall_sentiment")
        if debate_sentiment not in (None, ""):
            # The debate chamber already evaluated news for this run (LLM scout
            # judgment preferred over the keyword heuristic) and applied its
            # adjustment to the verdict confidence. Re-fetching here used to
            # stamp a second, contradictory signal on the top level (e.g.
            # NEUTRAL/-0.2 vs metadata POSITIVE/+0.05), so mirror the applied
            # values instead and only fetch when the debate produced none.
            try:
                applied_adjustment = float(
                    metadata.get("news_confidence_adjustment") or 0.0
                )
            except (TypeError, ValueError):
                applied_adjustment = 0.0
            result["news_sentiment"] = str(debate_sentiment)
            result["news_confidence_adjustment"] = applied_adjustment
            result["has_breaking_news"] = bool(metadata.get("has_breaking_news"))
            result["breaking_news_headlines"] = list(
                metadata.get("breaking_news_headlines") or []
            )
            return
        news_bundle = DEFAULT_FETCHER.build_bundle(ticker)
        logger.info(
            f"[News] {ticker}: sentiment={news_bundle.overall_sentiment.value} "
            f"adjustment={news_bundle.confidence_adjustment:+.2f}"
        )
        if news_bundle.confidence_adjustment != 0:
            logger.info(
                f"[News] {ticker}: "
                f"adjustment={news_bundle.confidence_adjustment:+.2f} "
                f"({news_bundle.confidence_adjustment_reason})"
            )
        if news_bundle.has_breaking_news:
            logger.warning(f"[News] {ticker}: BREAKING NEWS DETECTED")
        result["news_sentiment"] = news_bundle.overall_sentiment.value
        result["news_confidence_adjustment"] = news_bundle.confidence_adjustment
        # FIX: ISSUE 3 — Carry breaking-news content into persisted outputs.
        result["has_breaking_news"] = news_bundle.has_breaking_news
        result["breaking_news_headlines"] = _breaking_news_headlines_from_bundle(
            news_bundle
        )
        metadata = _result_metadata(result)
        metadata["has_breaking_news"] = news_bundle.has_breaking_news
        metadata["breaking_news_headlines"] = result["breaking_news_headlines"]
    except Exception as e:
        logger.warning(f"[News] {ticker}: fetch failed: {e}")


def _attach_risk_governor_to_result(
    *,
    ticker: str,
    run_id: str,
    result: dict[str, Any],
) -> None:
    try:
        verdict = _dict_or_empty(result.get("verdict"))
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
        ma200_context = (
            raw_data.get("ma200_context")
            or metadata.get("ma200_context")
            or technicals.get("ma200_context")
        )
        risk_entry = {
            "ticker": ticker,
            "verdict": verdict,
            "current_price": verdict.get("current_price"),
            "rule_regime_snapshot": ORCHESTRATOR_CONFIG.get("market_regime"),
            "regime_context": result.get("regime_context")
            or ORCHESTRATOR_CONFIG.get("regime_context"),
            "execution_regime": result.get("execution_regime"),
            "execution_regime_reason": result.get("execution_regime_reason"),
            "raw_data": raw_data,
            "raw_data_summary": result.get("raw_data_summary"),
            "metadata": metadata,
            "technical_indicators": technicals,
            "risk_context": {
                "atr14": atr14,
                "avg_volume": avg_volume,
                "ma200_context": ma200_context,
                "exdate_days": metadata.get("exdate_days"),
                "exdate_tier": metadata.get("exdate_tier", "CLEAR"),
                "sector": result.get("sector_key"),
                "run_id": run_id,
            },
        }
        decision = annotate_risk(risk_entry)
        risk_payload = risk_entry.get("risk_governor", decision.model_dump())
        result["risk_governor"] = risk_payload
        logger.info(
            f"[RiskGovernor] {decision.ticker}: {decision.status} "
            f"({', '.join(decision.reason_codes)})"
        )
    except Exception as e:
        logger.warning(f"[RiskGovernor] evaluation failed for {ticker}: {e}")
        result["risk_governor"] = {"error": str(e)}


def _metadata_int(metadata: dict[str, Any], key: str) -> int:
    try:
        return int(metadata.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _record_ticker_telemetry(
    *,
    ticker: str,
    run_id: str,
    result: dict[str, Any],
) -> None:
    try:
        metadata = _result_metadata(result)
        verdict = _dict_or_empty(result.get("verdict"))
        status = _result_status(result)
        result["status"] = status
        try:
            elapsed = float(metadata.get("duration_seconds", 0) or 0)
        except (TypeError, ValueError):
            elapsed = 0.0
        metric = TickerMetric(
            ticker=ticker,
            run_id=run_id,
            status=status,
            verdict_rating=verdict.get("rating"),
            confidence=extract_model_confidence(verdict),
            debate_rounds=int(result.get("debate_rounds") or 0),
            duration_seconds=elapsed,
            flash_calls=_metadata_int(metadata, "flash_calls"),
            pro_calls=_metadata_int(metadata, "pro_calls"),
            rag_chunks_selected=_metadata_int(metadata, "rag_chunks_selected"),
            rag_chunks_considered=_metadata_int(metadata, "rag_chunks_considered"),
            rag_token_estimate=_metadata_int(metadata, "rag_token_estimate"),
            provider_errors=(
                [result.get("failure_type") or status]
                if status not in {"success"} and result.get("error")
                else []
            ),
            has_stale_data=False,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        DEFAULT_TELEMETRY.record_ticker(metric)
    except Exception as e:
        logger.warning(f"[Telemetry] {ticker}: failed: {e}")


def _set_signal_packet_forecast_state(
    result: dict[str, Any],
    forecast_state: str,
) -> None:
    """Update advisory forecast evidence without changing execution policy."""

    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        return
    packet = metadata.get("signal_packet")
    if not isinstance(packet, dict):
        return
    try:
        from services.signal_packet import set_forecast_state

        metadata["signal_packet"] = set_forecast_state(packet, forecast_state)
    except Exception as exc:
        logger.warning(
            "[SignalPacket] {} forecast-state update failed: {}",
            result.get("ticker"),
            exc,
        )


def _persist_forecast_market_snapshot(snapshot: Any) -> str:
    """Persist one exact market input used by an advisory forecast."""

    from utils.market_snapshot import save_market_snapshot

    snapshot_path = resolve_within_root(
        OUTPUT_DIR,
        "forecast_snapshots",
        f"{snapshot.snapshot_id}.json.gz",
    )
    save_market_snapshot(snapshot, snapshot_path)
    try:
        return snapshot_path.relative_to(OUTPUT_DIR.resolve()).as_posix()
    except ValueError:
        return snapshot_path.as_posix()


async def _inject_forecast_reports(
    results: list[dict],
    *,
    ihsg_snapshot: Any | None = None,
    persist_ihsg_snapshot: bool = True,
) -> None:
    """Enrich successful debate results with a forward-looking ForecastReport.

    Called after _enhance_completed_results so verdict dict is fully populated.
    Graceful: ImportError or any per-ticker exception leaves result unchanged.
    """
    import asyncio

    pending: list[tuple[dict, Any]] = []
    for result in results:
        if isinstance(result, dict):
            pending.append((result, result.pop("_execution_snapshot", None)))

    try:
        from core.forecasting import ForecastingService
        from schemas.debate import CIOVerdict

        service = ForecastingService()
    except Exception as exc:
        logger.warning("[Forecast] Service unavailable for batch: {}", exc)
        for result, _execution_snapshot in pending:
            _set_signal_packet_forecast_state(result, "SERVICE_UNAVAILABLE")
        return

    ihsg_by_as_of: dict[date, tuple[Any, str | None] | None] = {}
    persisted_snapshots: dict[str, Any] = {}

    async def frozen_ihsg_for(execution_snapshot: Any) -> tuple[Any, str | None] | None:
        snapshot_as_of = getattr(execution_snapshot, "last_date", None)
        if not isinstance(snapshot_as_of, date):
            return None
        if snapshot_as_of in ihsg_by_as_of:
            return ihsg_by_as_of[snapshot_as_of]

        benchmark = ihsg_snapshot
        artifact_path: str | None = None
        if benchmark is None:
            try:
                from core.forecasting.dataset import download_ihsg_snapshot

                benchmark = await asyncio.to_thread(
                    download_ihsg_snapshot,
                    as_of=snapshot_as_of,
                )
            except Exception as exc:
                logger.warning(
                    "[Forecast] Frozen IHSG snapshot unavailable for {}: {}",
                    snapshot_as_of,
                    exc,
                )
                ihsg_by_as_of[snapshot_as_of] = None
                return None
        if persist_ihsg_snapshot:
            try:
                artifact_path = await asyncio.to_thread(
                    _persist_forecast_market_snapshot,
                    benchmark,
                )
                persisted_snapshots["IHSG"] = benchmark
            except Exception as exc:
                logger.warning(
                    "[Forecast] IHSG snapshot persistence failed for {}: {}",
                    snapshot_as_of,
                    exc,
                )
                ihsg_by_as_of[snapshot_as_of] = None
                return None
        resolved = (benchmark, artifact_path)
        ihsg_by_as_of[snapshot_as_of] = resolved
        return resolved

    for result, execution_snapshot in pending:
        raw_ticker = str(result.get("ticker") or "")
        if not raw_ticker or _result_status(result) != "success":
            continue
        try:
            ticker = normalize_idx_ticker(raw_ticker)
        except InvalidIDXTicker as exc:
            result["forecast_ev_ignored_reason"] = "invalid_idx_ticker"
            _set_signal_packet_forecast_state(result, "INVALID_TICKER")
            logger.warning("[Forecast] Invalid ticker skipped: {}", exc)
            continue
        metadata = (
            result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        )
        setup = metadata.get("trade_setup_snapshot")
        terminal_preflight = bool(
            isinstance(setup, dict) and setup.get("debate_eligible") is False
        )
        result.pop("forecast_report", None)
        result.pop("forecast_ev_pct", None)
        result.pop("forecast_ev_downweight", None)
        result.pop("forecast_ev_ignored_reason", None)
        if terminal_preflight and execution_snapshot is None:
            result["forecast_ev_ignored_reason"] = (
                "missing_frozen_ticker_snapshot"
            )
            _set_signal_packet_forecast_state(
                result,
                "MISSING_FROZEN_TICKER",
            )
            continue

        execution_provenance: dict[str, Any] | None = None
        execution_artifact_path: str | None = None
        if execution_snapshot is not None:
            execution_provenance = execution_snapshot.provenance()
            recorded_provenance = metadata.get("market_snapshot")
            recorded_provenance = (
                recorded_provenance
                if isinstance(recorded_provenance, dict)
                else {}
            )
            provenance_matches = all(
                str(recorded_provenance.get(field) or "")
                == str(execution_provenance.get(field) or "")
                for field in ("ticker", "snapshot_id", "data_hash", "last_date")
            )
            if not provenance_matches:
                result["forecast_ev_ignored_reason"] = (
                    "ticker_snapshot_provenance_mismatch"
                )
                _set_signal_packet_forecast_state(
                    result,
                    "SNAPSHOT_PROVENANCE_MISMATCH",
                )
                continue
            if persist_ihsg_snapshot:
                try:
                    execution_artifact_path = await asyncio.to_thread(
                        _persist_forecast_market_snapshot,
                        execution_snapshot,
                    )
                    persisted_snapshots[ticker] = execution_snapshot
                except Exception as exc:
                    result["forecast_ev_ignored_reason"] = (
                        "ticker_snapshot_persistence_failed"
                    )
                    _set_signal_packet_forecast_state(
                        result,
                        "SNAPSHOT_PERSISTENCE_FAILED",
                    )
                    logger.warning(
                        "[Forecast] {} snapshot persistence failed: {}",
                        ticker,
                        exc,
                    )
                    continue
            execution_provenance = execution_snapshot.provenance(
                artifact_path=execution_artifact_path
            )

        benchmark: Any | None = None
        benchmark_artifact_path: str | None = None
        if execution_snapshot is not None:
            resolved_benchmark = await frozen_ihsg_for(execution_snapshot)
            if resolved_benchmark is None:
                result.pop("forecast_report", None)
                result.pop("forecast_ev_pct", None)
                result.pop("forecast_ev_downweight", None)
                result["forecast_ev_ignored_reason"] = (
                    "frozen_ihsg_snapshot_unavailable"
                )
                _set_signal_packet_forecast_state(
                    result,
                    "MISSING_FROZEN_IHSG",
                )
                continue
            benchmark, benchmark_artifact_path = resolved_benchmark

        cio = None
        if not terminal_preflight:
            verdict_dict = (
                result.get("verdict")
                if isinstance(result.get("verdict"), dict)
                else {}
            )
            if not verdict_dict:
                continue
            try:
                cio = CIOVerdict.model_validate(verdict_dict)
            except Exception:
                cio = None
        try:
            predict_args = (
                ticker,
                None,
                (10,),
                "ensemble",
                cio,
                execution_snapshot,
            )
            if execution_snapshot is not None:
                report = await asyncio.to_thread(
                    service.predict,
                    *predict_args,
                    ihsg_snapshot=benchmark,
                    frozen_inputs_only=True,
                )
            else:
                report = await asyncio.to_thread(service.predict, *predict_args)
            report_payload = report.model_dump(mode="json")
            report_payload["capture_scope"] = (
                "preflight_terminal_shadow" if terminal_preflight else "standard"
            )
            report_payload["live_authority"] = False
            metadata = (
                result.get("metadata")
                if isinstance(result.get("metadata"), dict)
                else {}
            )
            if execution_provenance is not None:
                report_payload["market_snapshot"] = dict(execution_provenance)
                report_payload["execution_snapshot_id"] = execution_provenance.get(
                    "snapshot_id"
                )
                report_payload["execution_snapshot_hash"] = execution_provenance.get(
                    "data_hash"
                )
            if benchmark is not None:
                benchmark_provenance = benchmark.provenance(
                    artifact_path=benchmark_artifact_path
                )
                report_payload["ihsg_market_snapshot"] = benchmark_provenance
                report_payload["ihsg_snapshot_id"] = benchmark.snapshot_id
                report_payload["ihsg_snapshot_hash"] = benchmark.data_hash
                report_payload["ihsg_feature_as_of"] = (
                    benchmark.last_date.isoformat()
                    if benchmark.last_date is not None
                    else None
                )
            result["forecast_report"] = report_payload
            result.pop("forecast_ev_pct", None)
            result.pop("forecast_ev_downweight", None)
            forecast_status = str(
                report_payload.get("forecast_status") or "UNAVAILABLE"
            )
            if terminal_preflight:
                _set_signal_packet_forecast_state(
                    result,
                    f"SHADOW_{forecast_status}",
                )
                result["forecast_ev_ignored_reason"] = (
                    "preflight_terminal_shadow_only"
                )
                continue

            _set_signal_packet_forecast_state(result, forecast_status)
            result.pop("forecast_ev_ignored_reason", None)
            ranking_ev, downweight, ignored_reason = _forecast_ranking_ev(
                report_payload
            )
            if ignored_reason:
                result["forecast_ev_ignored_reason"] = ignored_reason
            elif ranking_ev is not None:
                result["forecast_ev_pct"] = ranking_ev * 100
                if downweight is not None:
                    result["forecast_ev_downweight"] = downweight
        except Exception as exc:
            result.pop("forecast_report", None)
            result.pop("forecast_ev_pct", None)
            result.pop("forecast_ev_downweight", None)
            result["forecast_ev_ignored_reason"] = "forecast_capture_error"
            _set_signal_packet_forecast_state(result, "ERROR")
            logger.warning(f"[Forecast] {ticker}: {exc}")

    if persist_ihsg_snapshot and persisted_snapshots:
        try:
            from utils.market_snapshot import persist_market_snapshots

            snapshots_dir = resolve_within_root(OUTPUT_DIR, "forecast_snapshots")
            await asyncio.to_thread(
                persist_market_snapshots,
                persisted_snapshots,
                snapshots_dir,
            )
        except Exception as exc:
            logger.warning("[Forecast] Snapshot manifest persistence failed: {}", exc)


def _enhance_completed_results(
    results: list[dict],
    run_id: str,
    *,
    fetch_news: bool = True,
) -> None:
    for result in results:
        try:
            _stamp_execution_regime_contract(result)
            ticker = str(result.get("ticker") or "UNKNOWN").upper()
            status = _result_status(result)
            result["status"] = status
            metadata = (
                result.get("metadata")
                if isinstance(result.get("metadata"), dict)
                else {}
            )
            setup = metadata.get("trade_setup_snapshot")
            setup = setup if isinstance(setup, dict) else {}
            terminal_preflight = bool(
                str(metadata.get("decision_source") or "").lower() == "preflight"
                or (setup and setup.get("debate_eligible") is False)
            )
            if fetch_news and not terminal_preflight:
                _attach_news_signal(ticker, result)
            if status == "success" and result.get("verdict"):
                _merge_metadata_reasons(result)
                if terminal_preflight:
                    verdict = result["verdict"]
                    setup_status = str(
                        setup.get("status")
                        or metadata.get("execution_status")
                        or "INSUFFICIENT_DATA"
                    ).upper()
                    reason_code = str(
                        setup.get("reason_code") or "trade_setup_rejected"
                    )
                    reason = str(
                        setup.get("reason") or "Trade setup is not executable."
                    )
                    execution_status = (
                        "WAITLIST"
                        if setup_status in {"WAIT_FOR_PULLBACK", "WAIT_FOR_CONFIRMATION"}
                        else "INSUFFICIENT_DATA"
                        if setup_status == "INSUFFICIENT_DATA"
                        else "NO_TRADE"
                    )
                    result["decision_source"] = "preflight"
                    result["execution_status"] = execution_status
                    result["model_rating"] = None
                    result["model_confidence"] = None
                    result["policy_confidence"] = 1.0
                    verdict["decision_source"] = "preflight"
                    verdict["execution_status"] = execution_status
                    verdict["model_rating"] = None
                    verdict["model_confidence"] = None
                    verdict["policy_confidence"] = 1.0
                    verdict["reason_codes"] = list(
                        dict.fromkeys(
                            [*(verdict.get("reason_codes") or []), reason_code]
                        )
                    )
                    risk_state = {
                        "WAIT_FOR_PULLBACK": "wait_for_pullback",
                        # RiskStatus has no wait_for_confirmation value. Keep the
                        # exact setup reason while using its canonical non-sizing
                        # watchlist state downstream.
                        "WAIT_FOR_CONFIRMATION": "watchlist_only",
                        "SHADOW_ONLY": "watchlist_only",
                    }.get(setup_status)
                    if risk_state is not None:
                        result["risk_gov"] = risk_state
                        result["risk_governor"] = {
                            "ticker": ticker,
                            "status": risk_state,
                            "sizing_allowed": False,
                            "reason_codes": [reason_code],
                            "message": reason,
                        }
                    else:
                        _set_reject_risk_payload(
                            result,
                            ticker=ticker,
                            reason=reason_code,
                            message=reason,
                        )
                else:
                    risk_locked = apply_minimum_confidence_gate(ticker, result)
                    if not risk_locked:
                        risk_locked = apply_setup_coherence_gate(ticker, result)
                    apply_extreme_overvaluation_flag(ticker, result)
                    sync_metric_aliases(result)
                    if not risk_locked:
                        _attach_risk_governor_to_result(
                            ticker=ticker,
                            run_id=run_id,
                            result=result,
                        )
                    else:
                        logger.info(
                            f"[RiskGovernor] {ticker}: reject "
                            f"({', '.join(result.get('reasons') or [])})"
                        )
            else:
                sync_metric_aliases(result)
            _record_ticker_telemetry(
                ticker=ticker,
                run_id=run_id,
                result=result,
            )
        except Exception as e:
            logger.warning(
                f"[Orchestrator] {result.get('ticker', 'UNKNOWN')} "
                f"postprocess failed: {e}"
            )


def _log_risk_warn_distribution(results: list[dict]) -> None:
    """Summarize how often the live R column will display WARN for the batch."""
    total = 0
    warn_count = 0
    status_counts: dict[str, int] = {}
    for result in results:
        if _result_status(result) != "success":
            continue
        risk = result.get("risk_governor")
        if not isinstance(risk, dict):
            continue
        total += 1
        status = str(risk.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if risk.get("sizing_allowed") is False:
            warn_count += 1
    if total == 0:
        return
    logger.info(
        f"[Risk] {warn_count}/{total} tickers flagged WARN — consider whether "
        f"market regime justifies this distribution; statuses={status_counts}"
    )


def _write_explainability_audit(
    *,
    output_dir: Path,
    ticker: str,
    result: dict[str, Any],
) -> None:
    try:
        packet = DEFAULT_AUDITOR.build_audit_packet(result)
        DEFAULT_AUDITOR.log_packet(packet)
        audit_path = _ticker_artifact_path(output_dir, ticker, "latest_audit.txt")
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            DEFAULT_AUDITOR.format_report(packet),
            encoding="utf-8",
        )
        logger.info(f"[Audit] {ticker}: {packet.one_line_summary}")
    except Exception as e:
        logger.warning(f"[Audit] {ticker}: failed: {e}")


_WATCHLIST_LOG_PATH = Path("output/backtest/watchlist_log.jsonl")


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _backtest_record_exists(
    path: Path,
    ticker: str,
    entry_price: float,
    target_price: float,
    stop_loss: float,
) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                rec.get("ticker") == ticker
                and rec.get("outcome") == "open"
                and abs(float(rec.get("entry_price") or 0) - entry_price) < 1.0
                and abs(float(rec.get("target_price") or 0) - target_price) < 1.0
                and abs(float(rec.get("stop_loss") or 0) - stop_loss) < 1.0
            ):
                return True
    return False


def _record_backtest_memory(
    *,
    result: dict[str, Any],
    run_id: str,
    memory: BacktestMemory = DEFAULT_MEMORY,
) -> None:
    ticker = str(result.get("ticker") or "UNKNOWN").upper()
    try:
        if _result_status(result) != "success":
            return
        verdict = _dict_or_empty(result.get("verdict"))
        verdict_rating = str(verdict.get("rating") or "").strip().upper()
        if not verdict_rating:
            return
        if verdict_rating == "AVOID":
            logger.info(f"[BacktestMemory] {ticker}: skipped AVOID verdict")
            return
        if verdict_rating == "INSUFFICIENT_DATA":
            logger.debug(
                "[BacktestMemory] %s: skipped - INSUFFICIENT_DATA verdict "
                "(setup fields cleared by confidence gate)",
                ticker,
            )
            return
        if verdict_rating == "HOLD":
            _append_jsonl(
                _WATCHLIST_LOG_PATH,
                {
                    "run_id": run_id,
                    "ticker": ticker,
                    "rating": "HOLD",
                    "entry_date": datetime.now(timezone.utc).date().isoformat(),
                    "confidence": _coerce_confidence(verdict.get("confidence")),
                    "entry_price_range": verdict.get("entry_price_range"),
                    "target_price": verdict.get("target_price"),
                    "stop_loss": verdict.get("stop_loss"),
                    "risk_reward_ratio": verdict.get("risk_reward_ratio"),
                    # Counterfactual fields: why the gate said no, and the
                    # non-tradeable levels it computed — lets the weekly runs
                    # accumulate gate-calibration data instead of null rows.
                    "reason_codes": list(verdict.get("reason_codes") or []),
                    "hypothetical_envelope": verdict.get("hypothetical_envelope"),
                },
            )
            logger.info(
                "[BacktestMemory] %s: HOLD logged to watchlist_log (not trade record)",
                ticker,
            )
            return

        from app.api.result_adapter import build_execution_decision

        execution_status = build_execution_decision(result)["execution_status"]
        if execution_status != "EXECUTABLE_BUY":
            logger.info(
                "[BacktestMemory] %s: skipped %s verdict with canonical "
                "execution_status=%s",
                ticker,
                verdict_rating,
                execution_status,
            )
            return

        entry_price = _parse_entry_low(verdict.get("entry_price_range"))
        target_price = _parse_price_value(verdict.get("target_price"))
        stop_loss = _parse_price_value(verdict.get("stop_loss"))
        if entry_price is None or target_price is None or stop_loss is None:
            raise ValueError("missing trade price fields")
        confidence = _coerce_confidence(verdict.get("confidence"))
        _mem_path = getattr(memory, "path", None)
        if _mem_path and _backtest_record_exists(
            _mem_path, ticker, entry_price, target_price, stop_loss
        ):
            logger.info(
                "[BacktestMemory] %s: duplicate open record skipped (dedup)", ticker
            )
            return
        _raw_data = _dict_or_empty(result.get("raw_data"))
        _metadata = _dict_or_empty(result.get("metadata"))
        _technicals = _dict_or_empty(result.get("technical_indicators"))
        # Use explicit None-check so avg_volume_20d=0.0 (genuine zero volume) blocks recording
        _avg_vol = next(
            (
                v
                for v in [
                    _raw_data.get("avg_volume_20d"),
                    _metadata.get("avg_volume_20d"),
                    _technicals.get("avg_volume_20d"),
                ]
                if v is not None
            ),
            None,
        )
        _min_adt = ORCHESTRATOR_CONFIG.get("min_adt_20d", 20_000_000_000)
        _adt_est = float(_avg_vol) * float(entry_price) if _avg_vol is not None else 0.0
        if _avg_vol is not None and _adt_est < _min_adt:
            logger.warning(
                "[BacktestMemory] %s: estimated ADT Rp %.0f < min Rp %.0f"
                " — recording skipped (fill impossible)",
                ticker,
                _adt_est,
                _min_adt,
            )
            return
        # V4.3: base-allocation-by-rating proxy for real position size (position_sizer
        # runs later, only on Top N candidates with real capital — not available here).
        _position_size_pct = RATING_BASE_ALLOCATION.get(verdict_rating)
        if verdict_rating in ("BUY", "STRONG_BUY") and _mem_path:
            from core.portfolio_guard import check_portfolio_allows_new_entry

            _stop_dist_pct = (
                (entry_price - stop_loss) / entry_price if entry_price > 0 else 0.0
            )
            _allowed, _guard_reason = check_portfolio_allows_new_entry(
                Path(_mem_path),
                _stop_dist_pct,
                new_position_size_pct=_position_size_pct,
            )
            if not _allowed:
                logger.warning(
                    f"[PortfolioGuard] {ticker}: entry blocked - {_guard_reason}"
                )
                return
        today = datetime.now(timezone.utc).date().isoformat()
        memory.record(
            TradeOutcome(
                run_id=run_id,
                ticker=ticker,
                verdict_rating=verdict_rating,
                entry_price=entry_price,
                exit_price=None,
                target_price=target_price,
                stop_loss=stop_loss,
                entry_date=today,
                exit_date=None,
                outcome="open",
                pnl_pct=None,
                hit_target=None,
                hit_stop=None,
                confidence_at_entry=confidence,
                notes="auto-recorded at orchestrator completion",
                position_size_pct=_position_size_pct,
            )
        )
    except ValueError as ve:
        if "missing trade price fields" in str(ve):
            logger.debug("[BacktestMemory] %s: skipped - %s", ticker, ve)
        else:
            logger.warning("[BacktestMemory] %s: failed: %s", ticker, ve)
    except Exception as e:
        logger.warning(f"[BacktestMemory] {ticker}: failed: {e}")


def _write_batch_telemetry_report(
    *,
    output_dir: Path,
    run_id: str,
    batch_timestamp: str,
) -> None:
    try:
        report = DEFAULT_TELEMETRY.build_batch_report(
            run_id=run_id,
            batch_timestamp=batch_timestamp,
        )
        DEFAULT_TELEMETRY.log_report(report)
        report_text = DEFAULT_TELEMETRY.format_report(report)
        telemetry_dir = resolve_within_root(output_dir, "telemetry")
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        telemetry_log_path = resolve_within_root(telemetry_dir, "telemetry_log.jsonl")
        latest_report_path = resolve_within_root(
            telemetry_dir, "latest_batch_report.txt"
        )
        run_report_path = resolve_within_root(telemetry_dir, f"{run_id}_report.txt")
        with telemetry_log_path.open("a", encoding="utf-8") as file:
            file.write(report.model_dump_json())
            file.write("\n")
        latest_report_path.write_text(
            report_text,
            encoding="utf-8",
        )
        run_report_path.write_text(
            report_text,
            encoding="utf-8",
        )
        logger.info(f"[Telemetry] Batch report saved for {run_id}")
    except Exception as e:
        logger.warning(f"[Telemetry] Batch report failed: {e}")


def _write_formatter_reports(
    *,
    results: list[dict[str, Any]],
    run_id: str,
    output_dir: Path,
    formatter: MarkdownFormatter = DEFAULT_MD,
) -> None:
    artifact_results = _canonicalize_results_for_artifact(results)
    validated_results: list[tuple[dict[str, Any], str]] = []
    for result in artifact_results:
        if _is_batch_only_result(result):
            continue
        ticker = result["ticker"]
        validated_results.append((result, ticker))
    for result, ticker in validated_results:
        try:
            payload = dict(result)
            metadata = _dict_or_empty(payload.get("metadata"))
            if str(metadata.get("run_id") or "").lower() in {"", "unknown"}:
                metadata = {**metadata, "run_id": run_id}
                payload["metadata"] = metadata
            md_content = formatter.generate_ticker_report(payload)
            md_path = _ticker_artifact_path(output_dir, ticker, "latest_report.md")
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md_content, encoding="utf-8")
            logger.info(f"[Formatter] Markdown report saved: {md_path}")
        except Exception as e:
            logger.warning(f"[Formatter] Markdown failed for {ticker}: {e}")

    batch_md_path = resolve_within_root(output_dir, "latest_batch_report.md")
    try:
        batch_md = formatter.generate_batch_summary(
            results=artifact_results,
            run_id=run_id,
        )
        batch_md_path.write_text(batch_md, encoding="utf-8")
        logger.info(f"[Formatter] Batch summary saved: {batch_md_path}")
    except Exception as e:
        logger.warning(f"[Formatter] Batch summary failed: {e}")


def _check_report_consistency(
    *,
    batch_json_path: Path,
    top3_md_path: Path,
) -> None:
    try:
        consistency = check_consistency(batch_json_path, top3_md_path)
        if consistency.consistent:
            logger.info("[ReportConsistency] passed")
        else:
            for issue in consistency.inconsistencies:
                if issue.severity == "error":
                    logger.error(f"[ReportConsistency] {issue}")
                else:
                    logger.warning(f"[ReportConsistency] {issue}")
    except Exception as e:
        logger.warning(f"[ReportConsistency] failed: {e}")


async def _run_single_debate(
    ticker: str,
    chamber: Any,
    sector: str = "",
    prepared_setup: dict[str, Any] | None = None,
    graham_fv: float | None = None,
) -> dict:
    """
    Jalankan debate untuk satu ticker: chamber.run() owns market-data prefetch â†' validasi schema.

    Retry ada di dalam DebateChamber._invoke_llm (tenacity). Tidak ada retry
    tambahan di sini untuk menghindari efek perkalian (9Ã— worst case).
    """
    from schemas.debate import CIOVerdict

    logger.info(f"[Debate] Mulai: {ticker}")

    # Task I: graham_fv hanya diteruskan saat tersedia, sehingga chamber/test
    # double lama tanpa kwarg ini tetap kompatibel di jalur tanpa kandidat.
    run_kwargs: dict[str, Any] = {}
    if graham_fv is not None:
        run_kwargs["graham_fv"] = graham_fv

    try:
        if prepared_setup is None:
            result = await chamber.run(ticker, sector=sector, **run_kwargs)
        else:
            result = await chamber.run(
                ticker,
                sector=sector,
                prepared_setup=prepared_setup,
                **run_kwargs,
            )
        if result.get("error") is not None:
            error = str(result["error"])
            _guard_status = (result.get("metadata") or {}).get("guard_status", "")
            _is_timeout = _guard_status == "timeout" or any(
                kw in error.lower()
                for kw in ("timeout", "timed out", "deadline exceeded")
            )
            return _empty_result(
                ticker,
                f"Chamber reported error: {error}",
                status="timeout" if _is_timeout else "failed",
                failure_stage="debate_chamber",
                failure_type="Timeout" if _is_timeout else "ChamberReportedError",
            )

        verdict_dict: dict = {}
        if result.get("final_verdict"):
            try:
                verdict_raw = json.loads(result["final_verdict"])
                verdict_dict = CIOVerdict(**verdict_raw).model_dump()
            except ValidationError as e:
                logger.error(f"[Debate] Schema tidak valid untuk {ticker}: {e}")
                return _exception_failure_result(
                    ticker,
                    e,
                    stage="final_verdict_schema",
                    prefix="Schema validation failed",
                )
            except json.JSONDecodeError as e:
                logger.error(f"[Debate] JSON rusak untuk {ticker}: {e}")
                return _exception_failure_result(
                    ticker,
                    e,
                    stage="final_verdict_json",
                    prefix="JSON decode error",
                )

        logger.info(f"[Debate] OK Selesai: {ticker}")
        missing_keys = {"ticker", "round_count", "raw_data"} - set(result)
        if missing_keys:
            logger.warning(
                f"[Debate] {ticker}: chamber state missing {sorted(missing_keys)} — "
                f"using fallbacks instead of failing the ticker."
            )
        disagreement_type = result.get("disagreement_type")
        if disagreement_type:
            logger.info(f"[Debate] {ticker} disagreement_type={disagreement_type}")
        debate_history = [
            _as_debate_message(m) for m in result.get("debate_history", [])
        ]
        metadata = dict(result.get("metadata") or {})
        market_data = (
            result.get("market_data")
            if isinstance(result.get("market_data"), dict)
            else {}
        )
        market_snapshot = market_data.get("market_snapshot")
        execution_snapshot = market_data.get("_market_snapshot_object")
        if isinstance(market_snapshot, dict):
            metadata["market_snapshot"] = dict(market_snapshot)
            metadata["snapshot_id"] = market_snapshot.get("snapshot_id")
            metadata["data_hash"] = market_snapshot.get("data_hash")
        yf_info = _extract_rr_yf_info(result)
        market_cap = (yf_info or {}).get("marketCap")
        if (
            isinstance(market_cap, (int, float))
            and not isinstance(market_cap, bool)
            and market_cap > 0
        ):
            metadata["market_cap_idr"] = int(market_cap)
        return {
            "ticker": result.get("ticker", ticker),
            "verdict": verdict_dict,
            "debate_rounds": result.get("round_count", 0),
            "consensus_reached": result.get("consensus_reached", False),
            "consensus_method": result.get("consensus_method"),
            "dissenting_agents": result.get("dissenting_agents", []),
            "agent_votes": result.get("agent_votes", []),
            "consensus_winner": result.get("consensus_winner"),
            "disagreement_type": disagreement_type,
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
            "raw_data_summary": result.get("raw_data", ""),
            "metadata": metadata,
            "regime_context": result.get("regime_context"),
            "hmm_regime": result.get("hmm_regime"),
            "trend_regime": result.get("trend_regime"),
            "volatility_regime": result.get("volatility_regime"),
            "execution_regime": result.get("execution_regime"),
            "execution_regime_reason": result.get("execution_regime_reason"),
            "trading_params": result.get("trading_params"),
            "_execution_snapshot": execution_snapshot,
            "error": None,
            "status": "success",
            "conviction_score": 0.0,  # Diisi oleh select_top3
        }

    except BudgetExhaustedError as e:
        logger.error(f"[Debate] STOP Budget habis saat debating {ticker}: {e}")
        _cli_renderer.record_failure_detail(ticker, traceback.format_exc())
        return _exception_failure_result(
            ticker,
            e,
            stage="debate_budget",
            prefix="Budget exhausted",
        )


async def run_batch_debates(
    tickers: list[str],
    sector_map: dict[str, str] | None = None,
    abort_event: asyncio.Event | None = None,
    run_id: str | None = None,
    chamber_factory: Callable[[], Any] | None = None,
    candidates_by_ticker: dict[str, dict] | None = None,
    max_executable_debates: int | None = None,
) -> list[dict]:
    # abort_event di-inject dari main() agar signal handler bisa mengaksesnya.
    """
    Jalankan DebateChamber untuk semua ticker dengan kontrol:
    - abort_event: fail-fast begitu budget habis
    - SafeRateLimiter: sliding window RPM
    - Semaphore: batas konkurensi paralel
    - budget_charged flag: refund aman tanpa over-refund

    [FIX-3, FIX-4, FIX-5, FIX-6] Urutan eksekusi per-task:
      1. Cek abort flag (tanpa lock)
      2. Tunggu rate limit (sleep di luar lock)
      3. Tunggu slot semaphore
      4. Cek abort flag lagi (setelah antre)
      5. Charge budget (atomik, di dalam budget_lock)
      6. Eksekusi API
      7. Refund jika CancelledError DAN budget_charged=True
    """
    max_concurrent = ORCHESTRATOR_CONFIG["max_concurrent_debates"]
    rate_limiter = SafeRateLimiter(
        rate_limit=ORCHESTRATOR_CONFIG["rpm_limit"],
        period_seconds=60.0,
    )
    sem = asyncio.Semaphore(max_concurrent)
    preflight_sem = asyncio.Semaphore(max_concurrent)

    # [FIX-3] Gunakan abort_event yang di-inject, atau buat baru jika tidak ada.
    if abort_event is None:
        abort_event = asyncio.Event()

    # [FIX-4] Budget state dengan lock dedikasi.
    budget_state = {"spent": 0}
    budget_lock = asyncio.Lock()

    # Batas budget per-run diambil dari core.budget; fallback ke jumlah ticker.
    try:
        usage = get_usage()
        max_budget = (
            max(0, int(max_executable_debates))
            if max_executable_debates is not None
            else max(
                0,
                int(usage.get("pro_budget", len(tickers)))
                - int(usage.get("pro_calls", 0)),
            )
        )
    except Exception:
        max_budget = (
            max(0, int(max_executable_debates))
            if max_executable_debates is not None
            else len(tickers)
        )

    total_tickers = len(tickers)
    progress_state = {"completed": 0}

    def _set_status(
        ticker: str,
        status: str,
        result: dict | None = None,
        *,
        step: str | None = None,
    ) -> None:
        changes: dict[str, Any] = {"status": step or status, "row_state": "active"}
        if step == "fetching data":
            changes.update(fetching="pending", active="fetching")
        elif step == "running analysis":
            changes.update(fetching="done", analysis="pending", active="analysis")
        elif step == "debating":
            changes.update(
                fetching="done", analysis="done", debating="pending", active="debating"
            )
        elif step == "stopped":
            changes.update(
                fetching="failed",
                analysis="failed",
                risk="failed",
                debating="failed",
                done="failed",
                active=None,
                row_state="failed",
            )
        elif step == "warning":
            changes.update(active=None, row_state="failed")
        if result is not None:
            verdict = result.get("verdict", {}) or {}
            error = result.get("error")
            if error:
                _cli_renderer.update_batch_progress_from_result(result)
            else:
                _cli_renderer.update_batch_progress(
                    ticker,
                    fetching="done",
                    analysis="done",
                    risk="pending",
                    debating="done",
                    done="pending",
                    active="risk",
                    rating=str(verdict.get("rating") or "-"),
                    confidence=_format_cli_pct(verdict.get("confidence")),
                    status="Risk validation pending",
                    row_state="active",
                )
            return
        if status in {"ERROR", "ABORTED"}:
            changes.update(row_state="failed", active=None)
        _cli_renderer.update_batch_progress(ticker, **changes)

    def _advance_progress() -> None:
        if progress_state["completed"] < total_tickers:
            progress_state["completed"] += 1

    async def _guarded(ticker: str) -> dict:
        sector_key = (sector_map or {}).get(ticker, "unknown")
        budget_charged = False
        progress_recorded = False
        started_at = asyncio.get_event_loop().time()

        def _finish_result(result: dict) -> dict:
            try:
                metadata = _result_metadata(result)
                regime_context = dict(
                    result.get("regime_context")
                    or ORCHESTRATOR_CONFIG.get("regime_context")
                    or {}
                )
                hmm_regime = dict(
                    result.get("hmm_regime")
                    or ORCHESTRATOR_CONFIG.get("hmm_regime")
                    or {}
                )
                result["regime_context"] = regime_context
                result["hmm_regime"] = hmm_regime
                if not result.get("trend_regime"):
                    result["trend_regime"] = regime_context.get("trend_regime")
                if not result.get("volatility_regime"):
                    result["volatility_regime"] = regime_context.get(
                        "volatility_regime"
                    )
                if not result.get("execution_regime"):
                    result["execution_regime"] = regime_context.get("execution_regime")
                if not result.get("execution_regime_reason"):
                    result["execution_regime_reason"] = regime_context.get(
                        "execution_regime_reason"
                    )
                if not result.get("trading_params"):
                    result["trading_params"] = regime_context.get(
                        "execution_params", {}
                    )
                metadata.setdefault("execution_regime", result.get("execution_regime"))
                metadata.setdefault(
                    "execution_regime_reason",
                    result.get("execution_regime_reason"),
                )
                if run_id:
                    metadata.setdefault("run_id", run_id)
                candidate = (candidates_by_ticker or {}).get(ticker, {})
                candidate_snapshot = (
                    candidate.get("market_snapshot")
                    if isinstance(candidate.get("market_snapshot"), dict)
                    else {}
                )
                if candidate_snapshot:
                    metadata["market_snapshot"] = dict(candidate_snapshot)
                    metadata["snapshot_id"] = candidate_snapshot.get("snapshot_id")
                    metadata["data_hash"] = candidate_snapshot.get("data_hash")
                metadata["duration_seconds"] = (
                    asyncio.get_event_loop().time() - started_at
                )
            except Exception as e:
                logger.warning(f"[Telemetry] {ticker}: duration capture failed: {e}")
            return result

        try:
            # 1. Cek abort sebelum mulai apapun
            if abort_event.is_set():
                logger.info(f"[{ticker}] Dibatalkan sebelum start (budget habis)")
                _set_status(ticker, "ABORTED", step="stopped")
                return _finish_result(
                    _empty_result(
                        ticker, "Aborted: budget exhausted before start", sector_key
                    )
                )

            # Task I: Graham FV dari kandidat screener, diteruskan ke chamber
            # agar _cio_judge_node dapat merender VALUATION CROSS-CHECK secara
            # real-time (bukan hanya anotasi post-debate di bawah). None di
            # jalur tanpa kandidat; chamber.run() yang mensanitasi NaN/<=0.
            graham_fv: float | None = None
            if candidates_by_ticker:
                _graham_raw = candidates_by_ticker.get(ticker, {}).get(
                    "Est. Fair Value (Graham)"
                )
                try:
                    graham_fv = float(_graham_raw) if _graham_raw else None
                except (TypeError, ValueError):
                    graham_fv = None

            # 2. Build the deterministic setup before rate limiting or budget
            # reservation. Non-executable candidates terminate here with zero
            # LLM calls and still remain present in the batch result.
            prepared_setup: dict[str, Any] | None = None
            _set_status(ticker, "QUEUED", step="fetching data")
            prepare = getattr(chamber, "prepare_trade_setup", None)
            if callable(prepare):
                async with preflight_sem:
                    candidate_context = (candidates_by_ticker or {}).get(ticker)
                    prepare_kwargs: dict[str, Any] = {
                        "current_price": 0.0,
                        "sector": sector_key,
                    }
                    if isinstance(candidate_context, dict) and candidate_context:
                        prepare_kwargs["candidate_context"] = candidate_context
                    prepared_setup = await prepare(ticker, **prepare_kwargs)
                setup_snapshot = prepared_setup.get("trade_setup_snapshot")
                setup_snapshot = (
                    setup_snapshot if isinstance(setup_snapshot, dict) else {}
                )
                if not bool(setup_snapshot.get("debate_eligible")):
                    _set_status(ticker, "PREFLIGHT", step="running analysis")
                    result = await _run_single_debate(
                        ticker,
                        chamber,
                        sector=sector_key,
                        prepared_setup=prepared_setup,
                        graham_fv=graham_fv,
                    )
                    result["sector_key"] = sector_key
                    final_rating = result.get("verdict", {}).get("rating") or (
                        "ERROR" if result.get("error") else "NO_TRADE"
                    )
                    _set_status(ticker, final_rating, result)
                    return _finish_result(result)

            # 3. Only executable candidates consume an LLM rate-limit slot.
            await rate_limiter.acquire()

            # 4. Tunggu slot konkurensi
            async with sem:
                _set_status(ticker, "DEBATING", step="running analysis")
                await asyncio.sleep(ORCHESTRATOR_CONFIG["batch_delay"])

                # 4. Cek abort lagi setelah antre
                if abort_event.is_set():
                    logger.info(f"[{ticker}] Dibatalkan saat antre (budget habis)")
                    _set_status(ticker, "ABORTED", step="stopped")
                    return _finish_result(
                        _empty_result(
                            ticker, "Aborted: budget exhausted in queue", sector_key
                        )
                    )

                # Update status: sedang berdebat.
                _set_status(ticker, "DEBATING", step="debating")

                # 5. Charge budget tepat sebelum eksekusi (atomik)
                async with budget_lock:
                    if budget_state["spent"] >= max_budget:
                        logger.warning(
                            f"[{ticker}] Kapasitas executable debate habis; "
                            "ticker dipertahankan sebagai explicit terminal result"
                        )
                        reason_code = "llm_budget_capacity_exhausted"
                        reason = (
                            "Tidak ada kapasitas LLM tersisa setelah deterministic "
                            "preflight; setup belum memperoleh CIO evaluation."
                        )
                        capacity_result = _pre_cio_terminal_result(
                            {"Ticker": ticker},
                            reason_code=reason_code,
                            reason=reason,
                        )
                        capacity_result["status"] = "skipped"
                        capacity_result["execution_status"] = "INSUFFICIENT_DATA"
                        capacity_result["verdict"]["execution_status"] = (
                            "INSUFFICIENT_DATA"
                        )
                        capacity_result["metadata"].pop("pre_cio_rejection", None)
                        capacity_result["metadata"]["budget_capacity_rejection"] = {
                            "reason_code": reason_code,
                            "estimated_pro_calls_per_ticker": 8,
                        }
                        _set_status(ticker, "SKIPPED", capacity_result)
                        return _finish_result(capacity_result)

                    budget_state["spent"] += 1
                    budget_charged = True  # Set di dalam lock, tepat setelah increment
                    current = budget_state["spent"]

                logger.info(f"[{ticker}] Budget terpakai: {current}/{max_budget}")

                # 6. Eksekusi
                try:
                    result = await _run_single_debate(
                        ticker,
                        chamber,
                        sector=sector_key,
                        prepared_setup=prepared_setup,
                        graham_fv=graham_fv,
                    )

                    # Valuation disagreement: Graham FV (screener) vs debate engine FV.
                    # FIX 4 policy: informational only -- stored on result["valuation_
                    # disagreement"] for audit/JSON visibility, deliberately NOT fed
                    # back into the verdict's confidence or fair_value. See
                    # check_valuation_disagreement()'s docstring (fair_value_calculator.py)
                    # for the empirical rationale (66% of tickers diverge >25% -- too
                    # noisy a signal to gate confidence on).
                    if candidates_by_ticker and not result.get("error"):
                        try:
                            from services.fair_value_calculator import (
                                check_valuation_disagreement,
                            )

                            cand = candidates_by_ticker.get(ticker, {})
                            candidate_snapshot = (
                                cand.get("market_snapshot")
                                if isinstance(cand.get("market_snapshot"), dict)
                                else {}
                            )
                            if candidate_snapshot:
                                result.setdefault("metadata", {})
                                result["metadata"]["market_snapshot"] = dict(
                                    candidate_snapshot
                                )
                                result["metadata"]["snapshot_id"] = (
                                    candidate_snapshot.get("snapshot_id")
                                )
                                result["metadata"]["data_hash"] = (
                                    candidate_snapshot.get("data_hash")
                                )
                            graham_fv = cand.get("Est. Fair Value (Graham)")
                            debate_fv = (result.get("verdict") or {}).get("fair_value")
                            if graham_fv or debate_fv:
                                disagreement = check_valuation_disagreement(
                                    float(graham_fv) if graham_fv else None,
                                    float(debate_fv) if debate_fv else None,
                                )
                                result["valuation_disagreement"] = disagreement
                                if (
                                    disagreement["valuation_disagreement"]
                                    == "SIGNIFICANT"
                                ):
                                    logger.warning(
                                        f"[ValuationGap] {ticker}: "
                                        f"Graham Rp{graham_fv:,.0f} vs "
                                        f"Debate Rp{debate_fv:,.0f} — "
                                        f"selisih {disagreement['disagreement_pct']}%"
                                    )
                        except Exception as _e:
                            logger.debug(f"[ValuationGap] {ticker}: skip — {_e}")

                    # Propagasi BudgetExhaustedError dari dalam chamber
                    if result.get("error") and result["error"].startswith(
                        "Budget exhausted"
                    ):
                        abort_event.set()

                    result["sector_key"] = sector_key

                    # Update status: rating final atau ERROR.
                    final_rating = result.get("verdict", {}).get("rating") or (
                        "ERROR" if result.get("error") else "HOLD"
                    )
                    _set_status(ticker, final_rating, result)
                    return _finish_result(result)

                except BudgetExhaustedError as e:
                    abort_event.set()
                    logger.error(f"[{ticker}] Budget habis dari dalam chamber: {e}")
                    _cli_renderer.record_failure_detail(ticker, traceback.format_exc())
                    _set_status(ticker, "ERROR", step="warning")
                    return _finish_result(
                        _exception_failure_result(
                            ticker,
                            e,
                            stage="debate_budget",
                            sector_key=sector_key,
                            prefix="Budget exhausted",
                        )
                    )

                except Exception as e:
                    logger.exception(f"[{ticker}] Error saat eksekusi: {e}")
                    _cli_renderer.record_failure_detail(ticker, traceback.format_exc())
                    _set_status(ticker, "ERROR", step="warning")
                    return _finish_result(
                        _exception_failure_result(
                            ticker,
                            e,
                            stage="single_debate",
                            sector_key=sector_key,
                        )
                    )

        except asyncio.CancelledError:
            # [FIX-6] INTENTIONAL: CancelledError ditelan secara eksplisit.
            #
            # Mengapa tidak re-raise:
            # - Cancellation di sini SELALU berasal dari abort_event (budget habis
            #   secara sistematis), bukan dari external shutdown atau timeout.
            # - asyncio.gather(return_exceptions=True) sudah menangani apapun
            #   yang lolos, termasuk BaseException.
            # - Menelan CancelledError memungkinkan pipeline tetap menghasilkan
            #   laporan parsial daripada crash total.
            #
            # Trade-off: task.cancelled() akan mengembalikan False untuk task ini.
            # Diterima karena Orchestrator tidak menggunakan task.cancelled()
            # untuk logika apapun â€" abort dideteksi via abort_event.is_set().
            #
            # [FIX-5] Refund hanya jika budget_charged=True untuk task INI.
            # Mencegah over-refund saat banyak task di-cancel bersamaan.
            if budget_charged:
                async with budget_lock:
                    if budget_state["spent"] > 0:
                        budget_state["spent"] -= 1
                        logger.info(
                            f"[{ticker}] Budget di-refund (dibatalkan sebelum eksekusi). "
                            f"Total: {budget_state['spent']}"
                        )
            logger.warning(f"[{ticker}] Task dibatalkan (CancelledError)")
            _cli_renderer.record_failure_detail(
                ticker,
                "Task cancelled by abort event",
            )
            _set_status(ticker, "ABORTED", step="stopped")
            return _finish_result(
                _empty_result(
                    ticker,
                    "Task cancelled by abort event",
                    sector_key,
                    failure_stage="abort_event",
                    failure_type="CancelledError",
                )
            )

        except Exception as e:
            logger.exception(f"[{ticker}] Error tak terduga di _guarded: {e}")
            _cli_renderer.record_failure_detail(ticker, traceback.format_exc())
            _set_status(ticker, "ERROR", step="warning")
            return _finish_result(
                _exception_failure_result(
                    ticker,
                    e,
                    stage="batch_guard",
                    sector_key=sector_key,
                    prefix="Orchestrator error",
                )
            )

        finally:
            if not progress_recorded:
                _advance_progress()
                progress_recorded = True

    # Rich owns the terminal during the debate phase; Loguru writes to file only.
    with _pipeline_file_logging_only():
        logger.info(
            f"[Orchestrator] Meluncurkan {len(tickers)} debate "
            f"(concurrency={max_concurrent}, "
            f"RPM={ORCHESTRATOR_CONFIG['rpm_limit']})"
        )
        if chamber_factory is not None:
            chamber = chamber_factory()
        else:
            from services.debate_chamber import DebateChamber

            chamber = DebateChamber()
        if run_id:
            # FIX: ISSUE 1 — Propagate the batch run_id before RAG evidence IDs are built.
            setattr(chamber, "run_id", run_id)
        setattr(chamber, "market_regime", ORCHESTRATOR_CONFIG.get("market_regime"))
        setattr(chamber, "hmm_regime", ORCHESTRATOR_CONFIG.get("hmm_regime"))
        setattr(
            chamber,
            "regime_context",
            ORCHESTRATOR_CONFIG.get("regime_context"),
        )
        results = await asyncio.gather(
            *[_guarded(t) for t in tickers],
            return_exceptions=True,
        )

    # Konversi BaseException yang lolos semua guard menjadi empty result
    safe_results: list[dict] = []
    for ticker, res in zip(tickers, results):
        if isinstance(res, BaseException):
            logger.error(f"[Orchestrator] ERROR {ticker} lolos semua guard: {res}")
            _cli_renderer.record_failure_detail(
                ticker,
                "".join(traceback.format_exception(type(res), res, res.__traceback__)),
            )
            sector_key = (sector_map or {}).get(ticker, "unknown")
            safe_results.append(
                _exception_failure_result(
                    ticker,
                    res,
                    stage="gather_fallback",
                    sector_key=sector_key,
                )
            )
        else:
            safe_results.append(res)

    usage = get_usage()
    logger.info(
        f"[Budget] Run selesai: "
        f"Pro {usage['pro_calls']}/{usage['pro_budget']}, "
        f"Flash {usage['flash_calls']}/{usage['flash_budget']}"
    )
    return safe_results


# ---------------------------------------------------------------------------
# Step 3: Scoring & ranking
# ---------------------------------------------------------------------------


#: R/R component plateau, as fractions of rr_normalization_cap. At the default
#: cap 5.0 the score rises to 1.0 over R/R 0→3.0 and plateaus on 3.0–4.0.
#: Regime caps (DEFENSIVE/HIGH 4.0, LOW 6.0) shift the plateau proportionally,
#: but the fall always reaches 0.0 at RR_IMPLAUSIBLE_CEILING — the governor's
#: hard-reject line — so the scorer never rewards an R/R the governor refuses
#: to deploy (LOW cap 6.0) and never zeroes an R/R it still accepts
#: (DEFENSIVE/HIGH cap 4.0).
_RR_PEAK_LOW_FRAC = 0.6
_RR_PEAK_HIGH_FRAC = 0.8


def _rr_component_score(rr_ratio: float, rr_cap: float) -> float:
    """
    Tent-shaped R/R score — NOT a monotonic ramp. Past the plateau, a bigger
    R/R means a less trustworthy envelope (stop in the noise band / target
    beyond realistic resistance), not a better trade: INDO's artifact R/R
    22.3x saturated the old `min(rr/cap, 1)` at 1.0 and ranked #1.
    """
    if rr_cap <= 0 or rr_ratio <= 0:
        return 0.0
    if rr_ratio >= RR_IMPLAUSIBLE_CEILING:
        return 0.0
    peak_low = rr_cap * _RR_PEAK_LOW_FRAC
    peak_high = rr_cap * _RR_PEAK_HIGH_FRAC
    if rr_ratio < peak_low:
        return rr_ratio / peak_low
    if rr_ratio <= peak_high:
        return 1.0
    return (RR_IMPLAUSIBLE_CEILING - rr_ratio) / (RR_IMPLAUSIBLE_CEILING - peak_high)


def compute_conviction_score(
    verdict: dict,
    ticker: str | None = None,
    debate_records: list[dict] | None = None,
    realized_outcomes: list[TradeOutcome] | None = None,
    forecast_ev_pct: float | None = None,
) -> tuple[float, str | None]:
    """
    Metric note: this returns trade_conviction, a risk-adjusted score on 0.0-1.0.

    It is intentionally different from model_confidence, which is the CIO's
    model certainty before the R/R component is blended in.

    Hitung Conviction Score = W_confidence Ã— CIO Confidence + W_rr Ã— R/R tent score.

    Weights dibaca dari ORCHESTRATOR_CONFIG (dapat di-override via env vars di settings).
    Komponen R/R memakai tent `_rr_component_score` dengan cap dari
    ORCHESTRATOR_CONFIG['rr_normalization_cap'] (puncak 0.6-0.8 Ã— cap, nol di cap).
    Jika ticker + debate_records disediakan, historical win-rate adjustment diterapkan.
    """
    w_confidence = ORCHESTRATOR_CONFIG["conviction_weights"]["confidence"]
    w_rr = ORCHESTRATOR_CONFIG["conviction_weights"]["rr_ratio"]
    rr_cap = ORCHESTRATOR_CONFIG["rr_normalization_cap"]

    confidence = float(verdict.get("confidence", 0.0) or 0.0)
    if confidence > 1.0:
        confidence = confidence / 100.0
    confidence = max(0.0, min(confidence, 1.0))

    rr_ratio = float(verdict.get("risk_reward_ratio", 0.0) or 0.0)

    warning: str | None = None
    if rr_ratio > 5.0:
        warning = (
            f"R/R {rr_ratio:.1f}x mencurigakan tinggi - "
            "verifikasi stop loss dan target: mungkin stop terlalu sempit "
            "atau target melampaui resistance kuat"
        )
    elif rr_ratio > 3.5:
        warning = (
            f"R/R {rr_ratio:.1f}x - verifikasi stop tidak berada di dalam noise band"
        )

    rr_score = _rr_component_score(rr_ratio, rr_cap)
    base_score = (w_confidence * confidence) + (w_rr * rr_score)

    # Historical adjustment — EV preferred over win rate; n passed for proportional scaling
    if ticker and realized_outcomes is not None:
        ev = compute_realized_ev(ticker, realized_outcomes)
        if ev is not None:
            n_real = sum(
                1
                for r in realized_outcomes
                if r.ticker.upper() == ticker.upper()
                and r.verdict_rating.upper() in {"BUY", "STRONG_BUY"}
                and r.outcome in {"win", "loss", "breakeven"}
                and r.pnl_pct is not None
            )
            base_score = apply_ev_adjustment(base_score, ev, n=n_real)
            return base_score, warning
        # Fall back to win rate when pnl_pct not yet populated
        realized_win_rate = compute_realized_win_rate(ticker, realized_outcomes)
        if realized_win_rate is not None:
            n_wr = sum(
                1
                for r in realized_outcomes
                if r.ticker.upper() == ticker.upper()
                and r.verdict_rating.upper() in {"BUY", "STRONG_BUY"}
                and r.outcome in {"win", "loss"}
            )
            base_score = apply_realized_adjustment(
                base_score, realized_win_rate, n=n_wr
            )
            return base_score, warning

    if ticker and debate_records is not None:
        win_rate = compute_historical_win_rate(ticker, debate_records)
        n_hist = sum(1 for r in debate_records if r.get("ticker") == ticker)
        base_score = apply_historical_adjustment(base_score, win_rate, n=n_hist)

    # Forward EV fallback — only reachable when no realized adjustment was
    # applied for this ticker (realized branches return early above).
    # forecast_ev_pct is already in percent, the same unit as realized pnl_pct
    # and apply_ev_adjustment's +3.0/-2.0 thresholds — do not rescale it.
    if forecast_ev_pct is not None:
        base_score = apply_ev_adjustment(base_score, forecast_ev_pct, n=1)

    return base_score, warning


def select_top_n(
    results: list[dict],
    debate_records: list[dict] | None = None,
    realized_outcomes: list[TradeOutcome] | None = None,
    *,
    require_risk_deployable: bool = False,
) -> list[dict]:
    """
    Rank hasil debate dan kembalikan Top N dengan sector diversification.

    Exclusion: ticker dengan rating AVOID, HOLD, atau SELL dikeluarkan.
    Scoring: historical adjustment diterapkan jika debate_records disediakan.
    Selection: didelegasikan ke diversify_portfolio() untuk sector cap + soft-cap.
    [FIX-7] Skor ditulis ke entry dict; generate_top3_report me-reuse tanpa recalculate.
    """
    top_n_cfg = ORCHESTRATOR_CONFIG["top_n_selection"]
    max_per_sector = settings.PORTFOLIO_MAX_PER_SECTOR
    max_per_cluster = settings.PORTFOLIO_MAX_PER_CLUSTER
    min_conviction = ORCHESTRATOR_CONFIG["min_conviction_override"]

    scorable: list[dict] = []

    for entry in results:
        verdict = entry.get("verdict", {})
        if not verdict:
            logger.info(f"[Rank] Lewati {entry['ticker']} – tidak ada verdict")
            continue

        rating = verdict.get("rating", "AVOID")
        if rating in EXCLUDED_RATINGS:
            logger.info(f"[Rank] Excluded {entry['ticker']} – rating {rating}")
            continue

        # Hard governor rejects (rr_implausible, overvalued, dst.) must not
        # occupy a top_n slot — the same report would otherwise show the entry
        # both ranked and actionability="reject".
        risk = entry.get("risk_governor")
        if require_risk_deployable and not (
            isinstance(risk, dict)
            and risk.get("status") == "deployable"
            and risk.get("sizing_allowed") is True
        ):
            logger.info(
                f"[Rank] Excluded {entry['ticker']} – setup is not risk-deployable"
            )
            continue
        if isinstance(risk, dict) and risk.get("status") == "reject":
            logger.info(
                f"[Rank] Excluded {entry['ticker']} – risk governor reject "
                f"({', '.join(risk.get('reason_codes') or [])})"
            )
            continue

        score, warning = compute_conviction_score(
            verdict,
            ticker=entry.get("ticker"),
            debate_records=debate_records,
            forecast_ev_pct=entry.get("forecast_ev_pct"),
            realized_outcomes=realized_outcomes,
        )
        entry["conviction_score"] = round(score, 4)
        entry["trade_conviction"] = round(score, 4)
        sync_metric_aliases(entry)
        if warning:
            entry["rr_warning"] = warning
        scorable.append(entry)

        logger.debug(
            f"[Rank] {entry['ticker']}: "
            f"confidence={verdict.get('confidence', 0):.2f}, "
            f"R/R={verdict.get('risk_reward_ratio', 0)}, "
            f"conviction={score:.4f}"
        )

    scorable.sort(key=lambda x: x["conviction_score"], reverse=True)

    top_n = diversify_portfolio(
        scorable=scorable,
        top_n=top_n_cfg,
        max_per_sector=max_per_sector,
        min_conviction=min_conviction,
        max_per_cluster=max_per_cluster,
    )

    logger.info(
        f"[Rank] Top {len(top_n)} dipilih: {[t['ticker'] for t in top_n]} "
        f"(dari {len(scorable)} eligible, top_n={top_n_cfg}, "
        f"sector_cap={max_per_sector}, cluster_cap={max_per_cluster}, "
        f"min_conviction={min_conviction:.0%})"
    )
    return top_n


# Backward-compatibility alias â€" deprecate secara bertahap
select_top3 = select_top_n


# ---------------------------------------------------------------------------
# Step 4: Persistence & reporting
# ---------------------------------------------------------------------------


def _safe_direct_artifact_path(path: Path) -> Path:
    """Contain one configured artifact within its declared parent directory."""

    return resolve_within_root(path.parent, path.name)


def _load_results_list(path: Path) -> list[dict[str, Any]]:
    safe_path = _safe_direct_artifact_path(path)
    if not safe_path.exists():
        return []
    try:
        content = safe_path.read_text(encoding="utf-8")
        if not content.strip():
            return []
        loaded = json.loads(content)
    except Exception as e:
        logger.warning(
            f"[Persist] Gagal membaca existing results dari {safe_path}: {e}"
        )
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def save_full_results(results: list[dict], path: Path = FULL_RESULTS_PATH) -> None:
    """Simpan snapshot batch terakhir sebagai JSON tunggal."""
    artifact_results = _canonicalize_results_for_artifact(results)
    safe_path = _safe_direct_artifact_path(path)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    for r in artifact_results:
        if isinstance(r, dict) and "ticker" in r:
            sync_metric_aliases(r)
    safe_path.write_text(
        json.dumps(artifact_results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(
        f"[Persist] Full batch snapshot ({len(artifact_results)} ticker) -> {safe_path}"
    )


def save_merged_results(
    results: list[dict],
    path: Path = MERGED_RESULTS_PATH,
    seed_path: Path = FULL_RESULTS_PATH,
) -> None:
    """Simpan latest ticker state gabungan untuk dashboard dan histori lokal."""
    incoming = [
        canonicalize_result_identity(result)
        for result in results
        if not _is_batch_only_result(result)
    ]
    safe_path = _safe_direct_artifact_path(path)
    safe_seed_path = _safe_direct_artifact_path(seed_path)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    existing_data = _load_results_list(safe_path)
    if not existing_data and safe_seed_path != safe_path:
        existing_data = _load_results_list(safe_seed_path)

    data_dict: dict[str, dict[str, Any]] = {}
    for item in existing_data:
        if _is_batch_only_result(item):
            continue
        try:
            canonical = canonicalize_result_identity(item)
        except (InvalidIDXTicker, TypeError) as exc:
            logger.warning(
                "[Persist] reason_code=invalid_existing_result_identity: {}",
                exc,
            )
            continue
        data_dict[canonical["ticker"]] = canonical
    for result in incoming:
        sync_metric_aliases(result)
        data_dict[result["ticker"]] = result

    merged_results = list(data_dict.values())
    safe_path.write_text(
        json.dumps(merged_results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        f"[Persist] Merged ticker state "
        f"({len(incoming)} new/updated into {len(merged_results)} total) -> {safe_path}"
    )


def save_single_agent_results(
    results: list,
    output_dir: Path = OUTPUT_DIR,
) -> None:
    """Persist standalone single-agent baseline results per ticker."""
    validated_results: list[tuple[Any, str]] = []
    for result in results:
        try:
            validated_results.append((result, normalize_idx_ticker(result.ticker)))
        except InvalidIDXTicker as exc:
            logger.warning(
                "[SingleAgent] reason_code=invalid_ticker ticker={} detail={}",
                result.ticker,
                exc,
            )
    single_dir = resolve_within_root(output_dir, "single_agent")
    single_dir.mkdir(parents=True, exist_ok=True)
    for result, ticker in validated_results:
        path = resolve_within_root(single_dir, f"{ticker}.json")
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        if result.verdict:
            logger.info(
                f"[SingleAgent] {result.ticker}: "
                f"{result.verdict.rating} "
                f"conf={result.verdict.confidence:.0%}"
            )
        elif not _cli_renderer.has_single_agent_warning(result.ticker):
            logger.warning(f"[SingleAgent] {result.ticker}: {result.status}")


def save_individual_debates(results: list[dict], output_dir: Path = OUTPUT_DIR) -> None:
    """
    Simpan setiap hasil debate yang sukses ke folder output/debates/ per ticker.
    Ini digunakan oleh historical_scorer untuk track record jangka panjang.
    """
    eligible = [
        canonicalize_result_identity(entry)
        for entry in results
        if entry.get("verdict")
        and not entry.get("error")
        and _dict_or_empty(entry.get("metadata")).get("artifact_scope") != "batch_only"
    ]
    debates_dir = resolve_within_root(output_dir, "debates")
    debates_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for entry in eligible:
        ticker = entry["ticker"]
        # Gunakan format nama yang konsisten dengan historical_scorer.py
        file_path = resolve_within_root(debates_dir, f"{ticker}_debate.json")

        # Simpan individual file
        file_path.write_text(
            json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        count += 1

    if count > 0:
        logger.info(
            f"[Persist] {count} individual debate records disimpan ke {debates_dir}"
        )


def save_individual_debates_versioned(
    results: list[dict],
    timestamp: str,
    output_dir: Path = OUTPUT_DIR,
    record_backtest_memory: bool = True,
) -> None:
    """
    Simpan hasil debate per ticker sebagai snapshot immutable.

    Format baru: output/debates/{TICKER}/v{timestamp}/{TICKER}_debate.json.
    Untuk backward compatibility, file flat output/debates/{TICKER}_debate.json
    tetap ditulis agar historical_scorer lama tetap membaca latest record.
    """
    eligible = [
        canonicalize_result_identity(entry)
        for entry in results
        if entry.get("verdict")
        and not entry.get("error")
        and _dict_or_empty(entry.get("metadata")).get("artifact_scope") != "batch_only"
    ]
    debates_dir = resolve_within_root(output_dir, "debates")
    debates_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for entry in eligible:
        ticker = entry["ticker"]
        ticker_dir = resolve_within_root(debates_dir, ticker)
        version_dir = resolve_within_root(ticker_dir, f"v{timestamp}")
        version_dir.mkdir(parents=True, exist_ok=True)

        payload = dict(entry)
        payload.setdefault("metadata", {})
        payload["metadata"] = {
            **payload["metadata"],
            "batch_timestamp": timestamp,
            "versioned_output": True,
        }

        version_file = resolve_within_root(version_dir, f"{ticker}_debate.json")
        version_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        latest_file = resolve_within_root(ticker_dir, "latest_debate.json")
        latest_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        legacy_file = resolve_within_root(debates_dir, f"{ticker}_debate.json")
        legacy_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _write_explainability_audit(
            output_dir=output_dir,
            ticker=ticker,
            result=payload,
        )
        if record_backtest_memory:
            _record_backtest_memory(
                result=payload,
                run_id=timestamp,
            )
        count += 1

    if count > 0:
        logger.info(
            f"[Persist] {count} versioned debate records disimpan ke {debates_dir}"
        )


def _latest_debate_path_for_validation(results: list[dict], output_dir: Path) -> Path:
    for entry in results:
        if (
            entry.get("verdict")
            and not entry.get("error")
            and entry.get("ticker")
            and _dict_or_empty(entry.get("metadata")).get("artifact_scope")
            != "batch_only"
        ):
            return _ticker_artifact_path(
                output_dir, entry["ticker"], "latest_debate.json"
            )
    for entry in results:
        if (
            entry.get("ticker")
            and _dict_or_empty(entry.get("metadata")).get("artifact_scope")
            != "batch_only"
        ):
            return _ticker_artifact_path(
                output_dir, entry["ticker"], "latest_debate.json"
            )
    return resolve_within_root(output_dir, "debates", "latest_debate.json")


def _log_artifact_validation(results: list[dict]):
    report = reconcile_artifacts(
        FULL_RESULTS_PATH,
        TOP3_REPORT_PATH,
        _latest_debate_path_for_validation(results, OUTPUT_DIR),
        audit_log_path=OUTPUT_DIR / "audit" / "audit_log.jsonl",
        telemetry_log_path=OUTPUT_DIR / "telemetry" / "telemetry_log.jsonl",
        rag_evidence_log_path=OUTPUT_DIR / "rag_evidence" / "evidence_log.jsonl",
    )
    for item in report.rag_not_applicable:
        logger.info(
            "[ArtifactValidator] surface={} status={} ticker={} run_id={} "
            "terminal_kind={} reason_code={} graph_activity={}",
            "rag_evidence",
            item.status,
            item.ticker,
            item.run_id or "unknown",
            item.terminal_kind,
            item.reason_code,
            item.graph_activity,
        )
    for warning in report.warnings:
        logger.warning(f"[ArtifactValidator] {warning}")
    if report.valid:
        logger.info("[ArtifactValidator] Output artifacts valid.")
    else:
        logger.error(
            f"[ArtifactValidator] Output artifact validation failed: {report.errors}"
        )
    return report


def _build_sizing_candidates(top_n: list[dict]) -> list[dict]:
    """Flatten selected orchestrator entries into position-sizer input records."""
    candidates: list[dict] = []
    for entry in top_n:
        risk = entry.get("risk_governor")
        if not isinstance(risk, dict) or risk.get("sizing_allowed") is not True:
            continue
        verdict = entry.get("verdict") or {}
        if str(verdict.get("rating") or "").upper() not in {
            "STRONG_BUY",
            "BUY",
        }:
            continue
        candidates.append(
            {
                "ticker": entry.get("ticker") or verdict.get("ticker"),
                **(
                    {
                        "regime_context": entry.get("regime_context"),
                        "execution_regime": entry.get("execution_regime"),
                        "execution_regime_reason": entry.get("execution_regime_reason"),
                    }
                    if entry.get("execution_regime")
                    else {}
                ),
                "current_price": verdict.get("current_price"),
                "entry_high": (
                    risk.get("entry_high") if isinstance(risk, dict) else None
                ),
                "stop_loss": verdict.get("stop_loss"),
                "rating": verdict.get("rating"),
                "confidence": verdict.get("confidence"),
                "rr_ratio": verdict.get("risk_reward_ratio"),
                "target_price": verdict.get("target_price"),
                "expected_return": verdict.get("expected_return"),
            }
        )
    return candidates


def _apply_circuit_breaker(top_n: list[dict], portfolio_state: dict) -> bool:
    """Block all sizing when portfolio daily-loss circuit breaker is tripped.

    Returns True if the breaker fired (all entries blocked), False otherwise.
    """
    from core.risk_governor import check_circuit_breaker

    if not check_circuit_breaker(portfolio_state):
        return False

    loss_pct = portfolio_state.get("realized_loss_pct", "?")
    logger.warning(
        "[CircuitBreaker] Daily loss ≥ %.0f%% (realized_loss_pct=%s) — "
        "all sizing halted for this batch.",
        100 * 0.03,
        loss_pct,
    )
    for entry in top_n:
        ticker = entry.get("ticker", "unknown")
        entry["risk_governor"] = {
            "ticker": ticker,
            "status": "reject",
            "sizing_allowed": False,
            "reason_codes": ["circuit_breaker"],
            "message": (
                "Portfolio circuit breaker aktif: realized daily loss "
                "≥ 3%. Sizing diblokir untuk semua ticker pada batch ini."
            ),
        }
    return True


def _annotate_risk_governor(top_n: list[dict]) -> None:
    """Attach deterministic actionability metadata to a standalone entry list.

    Not part of main()'s pipeline (per-result annotation now happens earlier,
    via _attach_risk_governor_to_result inside _enhance_completed_results).
    Kept as a simple, direct entry point onto annotate_risk() for callers and
    tests that only have a bare {ticker, verdict} list and want risk_governor
    attached without building the full _attach_risk_governor_to_result context.
    """
    for entry in top_n:
        entry.setdefault("regime_context", ORCHESTRATOR_CONFIG.get("regime_context"))
        context = entry.get("regime_context")
        if isinstance(context, dict):
            entry.setdefault("execution_regime", context.get("execution_regime"))
            entry.setdefault(
                "execution_regime_reason",
                context.get("execution_regime_reason"),
            )
        try:
            decision = annotate_risk(entry)
            if not decision.sizing_allowed:
                logger.info(
                    f"[RiskGovernor] {decision.ticker}: {decision.status} "
                    f"({', '.join(decision.reason_codes)})"
                )
        except Exception as exc:
            ticker = entry.get("ticker", "unknown")
            logger.error(
                f"[RiskGovernor] {ticker} annotation failed — blocking sizing: {exc}"
            )
            entry["risk_governor"] = {
                "ticker": ticker,
                "status": "reject",
                "sizing_allowed": False,
                "reason_codes": ["governor_error"],
                "message": f"Risk governor gagal ({exc}); sizing diblokir untuk keamanan.",
            }


def _risk_holds(top_n: list[dict]) -> list[dict]:
    holds: list[dict] = []
    for entry in top_n:
        risk = entry.get("risk_governor")
        if isinstance(risk, dict) and risk.get("sizing_allowed") is True:
            continue
        risk_dict = risk if isinstance(risk, dict) else {}
        holds.append(
            {
                "ticker": entry.get("ticker") or risk_dict.get("ticker"),
                "status": risk_dict.get("status"),
                "message": risk_dict.get("message"),
            }
        )
    return holds


def _enrich_sizing_with_risk_holds(sizing_result: dict, top_n: list[dict]) -> None:
    """Expose withheld candidates in allocation reasoning without changing sizing API."""
    holds = _risk_holds(top_n)
    if not holds:
        return
    sizing_result["actionability_holds"] = holds
    reasoning = sizing_result.setdefault("allocation_reasoning", {})
    risk_factors = reasoning.setdefault("risk_factors_limiting", [])
    tickers = ", ".join(str(item["ticker"]) for item in holds if item.get("ticker"))
    risk_factors.append(
        f"{len(holds)} kandidat ({tickers}) ditahan dari sizing karena belum executable pada harga sekarang"
    )


def _attach_sizing_to_results(results: list[dict], sizing_result: dict | None) -> None:
    """Attach position sizing and allocation reasoning to selected ticker records."""
    if not sizing_result:
        return

    positions = {
        str(position.get("ticker", "")).upper(): position
        for position in sizing_result.get("positions", [])
    }
    allocation_reasoning = sizing_result.get("allocation_reasoning")
    scenario_comparison = sizing_result.get("deployment_scenario_comparison")

    for entry in results:
        ticker = str(entry.get("ticker", "")).upper()
        if ticker not in positions:
            continue
        entry["position_sizing"] = positions[ticker]
        entry["allocation_reasoning"] = allocation_reasoning
        entry["deployment_scenario_comparison"] = scenario_comparison


def _finalize_execution_decisions(results: list[dict]) -> None:
    """Stamp one canonical post-risk, post-sizing decision on every result."""
    from app.api.result_adapter import build_execution_decision

    for entry in results:
        if not isinstance(entry, dict):
            continue
        try:
            decision = build_execution_decision(entry)
        except (InvalidIDXTicker, TypeError) as exc:
            logger.warning(
                "[ExecutionDecision] reason_code=invalid_result_identity "
                "ticker={} detail={}",
                entry.get("ticker"),
                exc,
            )
            continue
        entry["execution_decision"] = decision
        metadata = _result_metadata(entry)
        signal_packet = metadata.get("signal_packet")
        if isinstance(signal_packet, dict):
            try:
                from services.signal_packet import finalize_execution_state

                metadata["signal_packet"] = finalize_execution_state(
                    signal_packet,
                    actionable=bool(decision.get("actionable")),
                    reason_codes=decision.get("reason_codes") or [],
                )
            except Exception as exc:
                logger.warning(
                    "[SignalPacket] {} execution-state update failed: {}",
                    entry.get("ticker"),
                    exc,
                )
        for field in (
            "decision_contract_version",
            "decision_source",
            "execution_status",
            "model_rating",
            "model_confidence",
            "policy_confidence",
            "actionable",
            "reason_codes",
        ):
            entry[field] = decision.get(field)
        verdict = entry.get("verdict")
        if isinstance(verdict, dict):
            verdict["decision_source"] = decision.get("decision_source")
            verdict["execution_status"] = decision.get("execution_status")
            verdict["model_rating"] = decision.get("model_rating")
            verdict["model_confidence"] = decision.get("model_confidence")
            verdict["policy_confidence"] = decision.get("policy_confidence")


def build_execution_funnel(results: list[dict]) -> dict[str, Any]:
    """Return auditable counts from candidate intake through lot-sized output."""
    counts = {
        "quant_candidates": len(results),
        "technical_data_complete": 0,
        "trade_envelope_valid": 0,
        "debated": 0,
        "risk_deployable": 0,
        "position_sized": 0,
    }
    status_counts: dict[str, int] = {}
    ticker_outcomes: list[dict[str, Any]] = []
    for entry in results:
        metadata = (
            entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        )
        setup = metadata.get("trade_setup_snapshot")
        setup = setup if isinstance(setup, dict) else {}
        setup_status = str(setup.get("status") or "").upper()
        technical_status = str(setup.get("technical_data_status") or "").upper()
        pre_cio = metadata.get("pre_cio_rejection")
        pre_cio = pre_cio if isinstance(pre_cio, dict) else {}
        if (
            technical_status == "COMPLETE"
            or pre_cio.get("technical_data_complete") is True
        ):
            counts["technical_data_complete"] += 1
        if setup_status in {
            "EXECUTABLE",
            "WAIT_FOR_PULLBACK",
        }:
            counts["trade_envelope_valid"] += 1

        llm_calls = int(metadata.get("llm_calls") or 0)
        if (
            llm_calls > 0
            or int(metadata.get("flash_calls") or 0) > 0
            or int(metadata.get("pro_calls") or 0) > 0
            or int(entry.get("debate_rounds") or 0) > 0
        ):
            counts["debated"] += 1

        risk = (
            entry.get("risk_governor")
            if isinstance(entry.get("risk_governor"), dict)
            else {}
        )
        if risk.get("status") == "deployable" and risk.get("sizing_allowed") is True:
            counts["risk_deployable"] += 1

        position = (
            entry.get("position_sizing")
            if isinstance(entry.get("position_sizing"), dict)
            else {}
        )
        if (
            int(position.get("lot") or 0) >= 1
            and int(position.get("shares") or 0) == int(position.get("lot") or 0) * 100
            and float(position.get("max_loss_rp") or 0.0) > 0
        ):
            counts["position_sized"] += 1

        execution_status = str(
            entry.get("execution_status") or "INSUFFICIENT_DATA"
        ).upper()
        status_counts[execution_status] = status_counts.get(execution_status, 0) + 1
        ticker_outcomes.append(
            {
                "ticker": entry.get("ticker"),
                "trade_setup_status": setup_status or None,
                "execution_status": execution_status,
                "reason_codes": list(entry.get("reason_codes") or []),
                "snapshot_id": metadata.get("snapshot_id"),
                "data_hash": metadata.get("data_hash"),
            }
        )

    return {
        "contract_version": "execution-funnel-v1",
        "counts": counts,
        "execution_status_counts": status_counts,
        "ticker_outcomes": ticker_outcomes,
    }


def save_execution_funnel(
    results: list[dict],
    path: Path,
) -> dict[str, Any]:
    artifact_results = _canonicalize_results_for_artifact(results)
    funnel = build_execution_funnel(artifact_results)
    safe_path = _safe_direct_artifact_path(path)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(
        json.dumps(funnel, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("[ExecutionFunnel] {}", funnel["counts"])
    return funnel


def _dry_run_profile(sector_key: str) -> dict[str, Any]:
    """Return a small sector-aware profile for dry-run mock generation."""
    sector = sector_key.lower()
    if any(
        token in sector
        for token in ("energy", "energi", "oil", "coal", "basic", "material")
    ):
        return {
            "ratings": ["STRONG_BUY", "BUY", "HOLD"],
            "weights": [0.35, 0.45, 0.20],
            "confidence": (0.62, 0.86),
            "target_multiplier": (1.06, 1.12),
            "stop_multiplier": (0.90, 0.95),
            "catalyst": "Sektor komoditas mock diberi upside lebih lebar karena volatilitas siklikal.",
            "risk": "Volatilitas harga komoditas bisa membuat stop-loss lebih cepat tersentuh.",
        }
    if any(token in sector for token in ("bank", "finance", "financial", "finansial")):
        return {
            "ratings": ["STRONG_BUY", "BUY", "HOLD"],
            "weights": [0.25, 0.55, 0.20],
            "confidence": (0.68, 0.92),
            "target_multiplier": (1.04, 1.08),
            "stop_multiplier": (0.94, 0.97),
            "catalyst": "Sektor finansial mock diberi confidence lebih stabil.",
            "risk": "Sensitivity terhadap yield dan kualitas kredit tetap perlu dicek.",
        }
    if any(
        token in sector for token in ("consumer", "konsumen", "health", "healthcare")
    ):
        return {
            "ratings": ["STRONG_BUY", "BUY", "HOLD"],
            "weights": [0.20, 0.50, 0.30],
            "confidence": (0.64, 0.88),
            "target_multiplier": (1.035, 1.075),
            "stop_multiplier": (0.93, 0.97),
            "catalyst": "Sektor defensif mock diasumsikan bergerak lebih moderat.",
            "risk": "Margin dan daya beli masih menjadi risiko validasi utama.",
        }
    return {
        "ratings": ["STRONG_BUY", "BUY", "HOLD"],
        "weights": [0.25, 0.50, 0.25],
        "confidence": (0.62, 0.90),
        "target_multiplier": (1.04, 1.09),
        "stop_multiplier": (0.92, 0.97),
        "catalyst": "Template dry-run umum untuk validasi konfigurasi pipeline.",
        "risk": "Data sintetis tidak merepresentasikan kondisi emiten aktual.",
    }


def _generate_mock_debate_results(
    tickers: list[str],
    sector_map: dict[str, str] | None = None,
) -> list[dict]:
    """Generate deterministic, sector-aware dry-run payloads matching real result shape."""
    from schemas.debate import CIOVerdict

    rng = random.Random(42)
    results: list[dict] = []

    for ticker in tickers:
        sector_key = (sector_map or {}).get(ticker, "unknown")
        profile = _dry_run_profile(sector_key)
        base_price = rng.choice([500, 750, 1000, 1500, 2500, 4000, 6000])
        entry_low = int(base_price * rng.uniform(0.96, 0.99))
        entry_high = int(base_price * rng.uniform(1.00, 1.03))
        target = int(entry_high * rng.uniform(*profile["target_multiplier"]))
        stop_loss = int(entry_low * rng.uniform(*profile["stop_multiplier"]))
        fair_value = int(target * rng.uniform(1.03, 1.18))
        mock_rating = rng.choices(
            profile["ratings"], weights=profile["weights"], k=1
        )[0]

        verdict = CIOVerdict(
            ticker=ticker,
            rating=mock_rating,
            confidence=round(rng.uniform(*profile["confidence"]), 2),
            fair_value=fair_value,
            entry_price_range=f"{entry_low} - {entry_high}",
            target_price=target,
            stop_loss=stop_loss,
            current_price=base_price,
            timeframe=SWING_TIMEFRAME_LABEL,
            weighted_reasoning="Dry-run mock verdict untuk validasi pipeline.",
            critical_risk_factor="Dry-run: bukan rekomendasi investasi aktual.",
            key_catalysts=[
                profile["catalyst"],
                "Risk/reward mock memenuhi ambang awal.",
            ],
            key_risks=[
                profile["risk"],
                "Dry-run memakai data sintetis.",
            ],
            summary=(
                "Ini adalah hasil simulasi dry-run untuk menguji parsing, scoring, "
                "persistensi, dan report generation tanpa API call."
            ),
            consensus_reached=True,
            consensus_method="voting",
            dissenting_agents=(
                ["bear"] if mock_rating in {"BUY", "STRONG_BUY"} else []
            ),
        ).model_dump()
        directional_position = (
            "BUY" if verdict["rating"] in {"BUY", "STRONG_BUY"} else "HOLD"
        )
        bear_position = "AVOID" if directional_position == "BUY" else "HOLD"
        mock_dissenters = ["bear"] if directional_position == "BUY" else []
        verdict["dissenting_agents"] = mock_dissenters

        results.append(
            {
                "ticker": ticker,
                "verdict": verdict,
                "debate_rounds": 3,
                "consensus_reached": True,
                "consensus_method": "voting",
                "dissenting_agents": mock_dissenters,
                "consensus_winner": {
                    "agent": "bull" if directional_position == "BUY" else "chartist",
                    "position": directional_position,
                    "confidence": verdict["confidence"],
                },
                "agent_votes": [
                    {
                        "agent": "chartist",
                        "position": directional_position,
                        "confidence": 0.64,
                        "round": 0,
                    },
                    {
                        "agent": "sentiment_specialist",
                        "position": directional_position,
                        "confidence": 0.55,
                        "round": 0,
                    },
                    {
                        "agent": "bull",
                        "position": directional_position,
                        "confidence": 0.70,
                        "round": 1,
                    },
                    {
                        "agent": "bear",
                        "position": bear_position,
                        "confidence": 0.58,
                        "round": 1,
                    },
                ],
                "debate_history": [
                    {
                        "role": "bull",
                        "content": (
                            "Dry-run bull case: setup teknikal mock mendukung "
                            "entry bertahap.\n\n"
                            f"Position: {directional_position}\n"
                            "Agent Confidence: 0.70"
                        ),
                        "round": 1,
                        "position": directional_position,
                        "confidence": 0.70,
                    },
                    {
                        "role": "bear",
                        "content": (
                            "Dry-run bear case: kualitas data sintetis tidak "
                            "boleh dipakai untuk trading.\n\n"
                            f"Position: {bear_position}\n"
                            "Agent Confidence: 0.58"
                        ),
                        "round": 1,
                        "position": bear_position,
                        "confidence": 0.58,
                    },
                    {
                        "role": "devils_advocate",
                        "content": "Dry-run challenge: konfirmasi ulang semua level harga dengan data live.",
                        "round": 2,
                        "position": "UNKNOWN",
                        "confidence": 0.0,
                    },
                ],
                "raw_data_summary": "DRY_RUN mock data; no provider or Gemini call executed.",
                "error": None,
                "conviction_score": 0.0,
                "sector_key": sector_key,
                "metadata": {
                    "dry_run": True,
                    "mock_profile": sector_key,
                    "fundamental_quality_flag": "PASS",
                    "prompt_version": PROMPT_VERSION,
                    "flash_calls": 0,
                    "pro_calls": 0,
                    "llm_calls": 0,
                    "trade_setup_snapshot": {
                        "version": "dry-run-1.0",
                        "ticker": ticker,
                        "status": "EXECUTABLE",
                        "reason_code": "dry_run_synthetic_setup",
                        "reason": "Synthetic executable setup for dry-run validation.",
                        "debate_eligible": True,
                        "technical_data_status": "COMPLETE",
                    },
                },
            }
        )

    logger.info(f"[DryRun] Generated {len(results)} mock debate results.")
    return results


def get_local_timestamp() -> str:
    """
    Kembalikan timestamp lokal dalam timezone yang dikonfigurasi (default: Asia/Jakarta).

    [FIX-8] ZoneInfo sudah di-import di top-level â€" fungsi ini tidak perlu
    import lokal yang dieksekusi setiap kali dipanggil.
    """
    utc_now = datetime.now(timezone.utc)
    local_tz = ZoneInfo(settings.DATETIME_TIMEZONE)
    return utc_now.astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def _extract_winning_argument(entry: dict) -> str:
    """Ambil argumen Bull terakhir (paling refined) dari history debate."""
    bull_args = []
    for raw in entry.get("debate_history", []):
        try:
            msg = _as_debate_message(raw)
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
            continue
        if msg.role == "bull":
            bull_args.append(msg.content)
    if not bull_args:
        return "Tidak ada argumen bull yang tercatat."
    arg = bull_args[-1]
    return arg[:497] + "..." if len(arg) > 500 else arg


def _extract_devils_warning(entry: dict) -> str:
    """Ambil challenge terakhir dari Devil's Advocate."""
    da_args = []
    for raw in entry.get("debate_history", []):
        try:
            msg = _as_debate_message(raw)
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
            continue
        if msg.role == "devils_advocate":
            da_args.append(msg.content)
    if not da_args:
        return "Tidak ada challenge devil's advocate yang tercatat."
    arg = da_args[-1]
    return arg[:397] + "..." if len(arg) > 400 else arg


def _batch_metadata_value(results: list[dict], key: str) -> str | None:
    for entry in results:
        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        if not isinstance(metadata, dict):
            continue
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _result_snapshot_provenance(result: dict[str, Any]) -> tuple[str, str]:
    metadata = result.get("metadata") if isinstance(result, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    snapshot = metadata.get("market_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    snapshot_id = snapshot.get("snapshot_id") or metadata.get("snapshot_id") or "-"
    data_hash = snapshot.get("data_hash") or metadata.get("data_hash") or "-"
    return str(snapshot_id), str(data_hash)


def _conviction_breakdown_row(
    score: float, model_confidence: float, verdict: dict
) -> str:
    """Markdown table row showing how the conviction score was computed."""
    try:
        w_conf = float(ORCHESTRATOR_CONFIG["conviction_weights"]["confidence"])
        w_rr = float(ORCHESTRATOR_CONFIG["conviction_weights"]["rr_ratio"])
        rr_cap = float(ORCHESTRATOR_CONFIG["rr_normalization_cap"])
        rr = float(verdict.get("risk_reward_ratio") or 0.0)
        rr_norm = _rr_component_score(rr, rr_cap)
        base = w_conf * model_confidence + w_rr * rr_norm
        hist_adj = score - base
        breakdown = (
            f"conf {model_confidence:.0%}x{w_conf:.0%} + "
            f"R/R {rr:.1f}→{rr_norm:.2f}x{w_rr:.0%} = {base:.3f}"
        )
        if abs(hist_adj) > 0.001:
            sign = "+" if hist_adj > 0 else ""
            breakdown += f" ({sign}{hist_adj:.2f} hist adj)"
        return f"| **Score Breakdown** | `{breakdown}` |"
    except Exception:
        return "| **Score Breakdown** | - |"


def _win_rate_row(ticker: str) -> str:
    """Markdown table row showing backtest track record for this ticker."""
    try:
        stats = DEFAULT_MEMORY.summary_stats(ticker)
        total = stats.get("total", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        wr = stats.get("win_rate", 0.0)
        avg_pnl = stats.get("avg_pnl_pct")
        if total >= 3:
            pnl_str = f", avg PnL {avg_pnl:+.1f}%" if avg_pnl is not None else ""
            value = f"{wins}W/{losses}L ({wr:.0%} win rate{pnl_str})"
        elif total > 0:
            value = f"Limited data ({total} record{'s' if total > 1 else ''})"
        else:
            value = "No backtest records yet"
        return f"| **Historical Signal Quality** | {value} |"
    except Exception:
        return "| **Historical Signal Quality** | - |"


def generate_top3_report(
    top_n: list[dict],
    all_results: list[dict],
    path: Path = TOP3_REPORT_PATH,
    sizing_result: dict | None = None,
) -> str:
    """
    Generate laporan Markdown eksekutif untuk Top N swing trade.

    [FIX-7] conviction_score di-reuse dari entry dict yang sudah diisi oleh
    select_top3 â€" tidak ada pemanggilan ulang compute_conviction_score.
    Untuk ticker error (tidak masuk select_top3), skor default 0.0.
    """
    top_n = _canonicalize_results_for_artifact(top_n)
    all_results = _canonicalize_results_for_artifact(all_results)
    path = _safe_direct_artifact_path(path)
    timestamp = get_local_timestamp()
    batch_timestamp = _batch_metadata_value(all_results, "batch_timestamp")
    run_id = _batch_metadata_value(all_results, "run_id")
    total_debated = len(all_results)
    selected_count = len(top_n)
    eligible = sum(
        1
        for r in all_results
        if r.get("verdict", {}).get("rating") not in EXCLUDED_RATINGS
        and r.get("verdict")
    )
    report_regime_context = dict(
        next(
            (
                result.get("regime_context")
                for result in all_results
                if isinstance(result.get("regime_context"), dict)
            ),
            ORCHESTRATOR_CONFIG.get("regime_context") or {},
        )
        or {}
    )
    execution_regime = str(report_regime_context.get("execution_regime") or "UNKNOWN")
    execution_reason = str(
        report_regime_context.get("execution_regime_reason") or "unspecified"
    )
    trend_payload = report_regime_context.get("trend_regime") or {}
    trend_regime = (
        trend_payload.get("label") if isinstance(trend_payload, dict) else trend_payload
    ) or "UNKNOWN"
    volatility_regime = str(report_regime_context.get("volatility_regime") or "UNKNOWN")

    lines: list[str] = [
        f"# TOP {selected_count} HIGH-CONVICTION IHSG SWING TRADES",
        "",
        f"> **Generated**: {timestamp}",
        f"> **Batch Timestamp**: {batch_timestamp or '-'}",
        f"> **Run ID**: {run_id or '-'}",
        f"> **Execution Regime**: {execution_regime}",
        f"> **Execution Regime Reason**: {execution_reason}",
        f"> **Trend Regime (diagnostic)**: {trend_regime}",
        f"> **Volatility Regime (diagnostic)**: {volatility_regime}",
        "> **Pipeline**: Quant Scouting -> Multi-Agent Debate -> CIO Verdict",
        f"> **Stocks Debated**: {total_debated} | **Eligible (BUY/STRONG_BUY)**: {eligible} | **Selected**: {selected_count}",
        "",
        "---",
        "",
    ]
    lines += [
        "## Market Snapshot Provenance",
        "",
        "| Ticker | Snapshot ID | Data Hash |",
        "|---|---|---|",
        *[
            (
                f"| {entry.get('ticker', '-')} | "
                f"{_result_snapshot_provenance(entry)[0]} | "
                f"{_result_snapshot_provenance(entry)[1]} |"
            )
            for entry in all_results
            if isinstance(entry, dict)
        ],
        "",
        "---",
        "",
    ]

    if not top_n:
        lines += [
            f"**Tidak ada saham yang memenuhi syarat untuk Top {ORCHESTRATOR_CONFIG['top_n_selection']}.**",
            "",
            "Tidak ada setup yang mencapai status risk-deployable dan position-sized. "
            "Lihat canonical execution status serta reason codes pada batch report; "
            "NO_TRADE adalah hasil kebijakan yang valid, bukan kegagalan pipeline.",
        ]
        report_text = "\n".join(lines)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report_text, encoding="utf-8")
        return report_text

    for rank, entry in enumerate(top_n, 1):
        v = entry["verdict"]
        ticker = entry["ticker"]
        risk = (
            entry.get("risk_governor")
            if isinstance(entry.get("risk_governor"), dict)
            else {}
        )
        action_status = str(risk.get("status") or "unknown").replace("_", " ").title()
        if "sizing_allowed" in risk:
            sizing_label = "Yes" if risk.get("sizing_allowed") else "No"
        else:
            sizing_label = "Unknown"
        action_message = risk.get("message") or "Risk governor metadata missing."
        # [FIX-7] Reuse skor dari select_top3, bukan hitung ulang
        score = entry.get("trade_conviction", entry.get("conviction_score", 0.0))
        model_confidence = extract_model_confidence(v) or 0.0
        disagreement = entry.get("disagreement_type")
        consensus_method = entry.get("consensus_method") or "unknown"
        dissenting_agents = entry.get("dissenting_agents") or []
        consensus_label = (
            f"Reached ({consensus_method})"
            if entry.get("consensus_reached")
            else f"No ({consensus_method}; {disagreement or 'unknown'})"
        )
        fair_value_status = _fair_value_status(entry)

        lines += [
            f"## #{rank} - {ticker}",
            "",
            "### Final Rating & Confidence",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| **Rating** | `{v.get('rating', 'N/A')}` |",
            f"| **Trade Setup Conviction** | {model_confidence:.0%} |",
            f"| **Trade Conviction** | {score:.2%} |",
            _conviction_breakdown_row(score, model_confidence, v),
            _win_rate_row(ticker),
            f"| **Debate Consensus** | {consensus_label} |",
            f"| **Dissenting Agents** | {', '.join(dissenting_agents) if dissenting_agents else '-'} |",
            f"| **Timeframe** | {v.get('timeframe', SWING_TIMEFRAME_LABEL)} |",
            f"| **Execution Horizon** | {v.get('execution_horizon_days', SWING_EXECUTION_HORIZON_DAYS)} trading days |",
            f"| **Actionability** | {action_status} |",
            f"| **Sizing Allowed** | {sizing_label} |",
            f"| **Actionability Note** | {action_message} |",
            "",
            "### Trade Box",
            "",
            "| Parameter | Level |",
            "|---|---|",
            f"| **Buy Range** | Rp {v.get('entry_price_range', 'N/A')} |",
            (
                f"| **Target Price** | Rp {v['target_price']:,.0f} |"
                if v.get("target_price")
                else "| **Target Price** | N/A |"
            ),
            (
                f"| **Stop Loss** | Rp {v['stop_loss']:,.0f} |"
                if v.get("stop_loss")
                else "| **Stop Loss** | N/A |"
            ),
            *(
                [
                    "| **Fair Value** | N/A |",
                    f"| **Fair Value Status** | {fair_value_status} |",
                ]
                if fair_value_status
                else (
                    []
                    if _valuation_gap_unverified(entry)
                    else [
                        (
                            f"| **Fair Value** | Rp {v['fair_value']:,.0f} |"
                            if v.get("fair_value")
                            else "| **Fair Value** | N/A |"
                        )
                    ]
                )
            ),
            f"| **Expected Return** | {v.get('expected_return', 'N/A')} |",
            f"| **Risk/Reward** | {v.get('risk_reward_ratio', 'N/A')} |",
            "",
            "*Semua harga sudah di-round ke tick IHSG dan dihitung oleh Python.*",
            "",
            "### Winning Argument",
            "",
            f"> {_extract_winning_argument(entry)}",
            "",
            "### Devil's Advocate Warning",
            "",
            f"> {_extract_devils_warning(entry)}",
            "",
            "### CIO Summary",
            "",
            v.get("summary", "Tidak ada summary tersedia."),
            "",
        ]

        if "rr_warning" in entry:
            lines += [f"> **{entry['rr_warning']}**", ""]

        catalysts = v.get("key_catalysts", [])
        risks = v.get("key_risks", [])

        if catalysts:
            lines.append("**Key Catalysts:**")
            lines += [f"- {c}" for c in catalysts]
            lines.append("")

        if risks:
            lines.append("**Key Risks:**")
            lines += [f"- {r}" for r in risks]
            lines.append("")

        lines += ["---", ""]

    position_summary = _build_position_summary(sizing_result)
    if position_summary:
        lines += [position_summary, "", "---", ""]

    actionability_holds = _risk_holds(top_n)
    if actionability_holds:
        lines += [
            "## Actionability Holds",
            "",
            "| Ticker | Status | Reason |",
            "|---|---|---|",
        ]
        for item in actionability_holds:
            status = str(item.get("status") or "-").replace("_", " ")
            lines.append(
                f"| {item.get('ticker', '-')} | {status} | {item.get('message', '-')} |"
            )
        lines += ["", "---", ""]

    # Footer: tabel ringkasan semua ticker
    lines += [
        "## Full Batch Summary",
        "",
        "| Ticker | Rating | Trade Setup Conviction | R/R Ratio | Trade Conviction | Evidence Age | Actionability | Consensus | Method | Dissenting Agents | Disagreement | Status |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    # [FIX-7] Untuk ticker yang sudah masuk select_top3, skor sudah ada di entry.
    # Untuk ticker error/excluded, ambil dari entry atau default 0.0 â€" tidak ada
    # pemanggilan ulang compute_conviction_score.
    selected_tickers = {t["ticker"] for t in top_n}
    sorted_results = sorted(
        all_results, key=lambda x: x.get("conviction_score", 0.0), reverse=True
    )

    for entry in sorted_results:
        v = entry.get("verdict", {})
        ticker = entry["ticker"]
        rating = v.get("rating", "ERROR") if v else "ERROR"
        conf = (extract_model_confidence(v) or 0.0) if v else 0.0
        rr = v.get("risk_reward_ratio", "N/A") if v else "N/A"
        cscore = entry.get("trade_conviction", entry.get("conviction_score", 0.0))
        evidence_age = _format_evidence_age(entry.get("evidence_age_h"))
        consensus = "YES" if entry.get("consensus_reached") else "NO"
        method = entry.get("consensus_method") or "-"
        dissent = ", ".join(entry.get("dissenting_agents") or []) or "-"
        disagreement = entry.get("disagreement_type") or "-"
        risk = (
            entry.get("risk_governor")
            if isinstance(entry.get("risk_governor"), dict)
            else {}
        )
        actionability = str(risk.get("status") or "-").replace("_", " ")

        if entry.get("error"):
            status = "Error"
        elif ticker in selected_tickers:
            status = "Selected"
        elif rating in EXCLUDED_RATINGS:
            status = "Excluded"
        else:
            status = "-"

        rr_str = f"{rr:.2f}" if isinstance(rr, (int, float)) and rr else "N/A"
        lines.append(
            f"| {ticker} | {rating} | {conf:.0%} | {rr_str} | {cscore:.2%} "
            f"| {evidence_age} | {actionability} | {consensus} | {method} | {dissent} | {disagreement} | {status} |"
        )

    lines += [
        "",
        "---",
        f"*Laporan dibuat oleh `orchestrator.py` pada {timestamp}*",
    ]

    report_text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_text, encoding="utf-8")
    logger.info(f"[Persist] Top {len(top_n)} report -> {path}")
    return report_text


# ---------------------------------------------------------------------------
# FV-5: Sector benchmark refresh helper
# ---------------------------------------------------------------------------


def _maybe_refresh_macro_rates() -> None:
    """Refresh macro rates (SBN 10Y yield) if cache is absent or stale. Non-fatal."""
    try:
        cached = load_cached_macro_rates()
        if cached:
            logger.info(
                "[Pipeline] Macro rates cache fresh (SBN 10Y={:.4f}, source={}) — skipping.",
                cached.get("sbn_10y", 0),
                cached.get("source", "?"),
            )
            return
        logger.info("[Pipeline] Refreshing macro rates (SBN 10Y yield)...")
        from services.stockbit_api_client import StockbitApiClient

        try:
            client = StockbitApiClient()
        except Exception:
            client = None
        result = _refresh_macro_rates(stockbit_client=client)
        logger.info(
            "[Pipeline] Macro rates refreshed: SBN 10Y={:.4f} ({})",
            result.get("sbn_10y", 0),
            result.get("source", "?"),
        )
    except Exception as exc:
        logger.warning("[Pipeline] Macro rate refresh skipped (non-fatal): {}", exc)


def _maybe_refresh_sector_benchmarks() -> None:
    """Refresh sector benchmarks if cache is absent or older than the TTL. Non-fatal."""
    try:
        stale = True
        if _SECTOR_BENCHMARKS_CACHE_PATH.exists():
            raw = json.loads(_SECTOR_BENCHMARKS_CACHE_PATH.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(raw.get("updated_at", "1970-01-01"))
            stale = (
                datetime.now(timezone.utc) - updated_at.replace(tzinfo=timezone.utc)
            ).days > _SECTOR_BENCHMARK_MAX_AGE_DAYS
        if not stale:
            logger.info(
                "[Pipeline] Sector benchmarks cache is fresh — skipping refresh."
            )
            return
        logger.info("[Pipeline] Refreshing sector benchmarks (12 IDX sectors)...")
        from services.stockbit_api_client import StockbitApiClient

        try:
            client = StockbitApiClient()
        except Exception as exc:
            logger.warning(
                "[Pipeline] Sector benchmark refresh skipped — client init failed: {}",
                exc,
            )
            return

        def _fetch(ticker: str) -> dict:
            return client.get(
                f"https://exodus.stockbit.com/keystats/ratio/v1/{ticker}?year_limit=10"
            )

        _refresh_sector_benchmarks(_fetch)
        logger.info("[Pipeline] Sector benchmarks refreshed and cached.")
    except Exception as exc:
        logger.warning(
            "[Pipeline] Sector benchmark refresh skipped (non-fatal): {}", exc
        )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


async def main(
    *,
    dry_run: bool = False,
    output_dir: Path = OUTPUT_DIR,
    portfolio_state: dict | None = None,
    user_config: dict | None = None,
    mode: str | None = None,
    screener_mode: str | None = None,
    chamber_factory: Callable[[], Any] | None = None,
    tickers: list[str] | None = None,
    raise_on_error: bool = False,
) -> None:
    """
    Pipeline penuh: Validate -> Regime -> Parse -> Debate -> Rank -> Report.

    Step 0a: Dependency validation -- cek staleness top10_candidates.json.
    Step 0b: Market regime -- override ORCHESTRATOR_CONFIG params.
    Step 1:  Parse tickers + sector_map.
    Step 2:  Run batch debates.
    Step 3:  Score & rank dengan historical records + sector diversification.
    Step 4:  Persist + generate Markdown report.
    """
    ticker_override = normalize_idx_tickers(
        list(tickers or CLI_TICKERS_OVERRIDE or []),
        require_nonempty=False,
    )
    _reset_orchestrator_runtime_config()
    from utils.market_data_cache import clear_run_cache

    await clear_run_cache()
    if not _CLI_LOGGING_CONFIGURED:
        configure_cli_logging(verbose=False)
    _cli_renderer.reset_run()
    started_at = time.monotonic()
    ledger_run_id = datetime.now(ZoneInfo(settings.DATETIME_TIMEZONE)).strftime(
        "%Y%m%d_%H%M%S"
    )
    run_mode = mode or CLI_MODE
    if run_mode not in {"multi", "single", "compare"}:
        raise ValueError(f"Unsupported orchestrator mode: {run_mode}")
    run_screener_mode = canonical_screener_mode(screener_mode or CLI_SCREENER_MODE)
    _cli_renderer.render_header(
        mode=run_mode,
        regime="detecting",
        timestamp=datetime.now(ZoneInfo(settings.DATETIME_TIMEZONE)).strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        ),
    )
    logger.info("[Orchestrator] Memulai IHSG Swing Trade Pipeline")
    logger.info(f"[Orchestrator] Mode: {run_mode}")
    _cli_renderer.phase("Prompt Pack")
    prompt_pack_ok = _run_prompt_pack_linter()
    _cli_renderer.set_pipeline_status(
        "Prompt pack",
        "OK" if prompt_pack_ok else "WARN",
        "OK" if prompt_pack_ok else "linter unavailable",
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            eval_summary = evaluate_memory(write=True)
        if eval_summary.updated_records > 0:
            logger.info(
                f"[BacktestEval] Auto-evaluated "
                f"{eval_summary.updated_records} "
                f"open trade(s) from history"
            )
        else:
            logger.info("[BacktestEval] No open trades to evaluate")
    except Exception as e:
        logger.warning(f"[BacktestEval] Auto-eval failed: {e}")

    reset_budget()
    if user_config is None:
        if ticker_override:
            user_config = {
                "total_capital": 1_000_000.0,
                "max_loss_pct": 0.02,
                "max_positions": 5,
            }
        else:
            user_config = _prompt_user_config()

    _cli_renderer.phase("Pre-flight Checks")
    deps = check_all_dependencies(
        output_dir,
        require_llm=not dry_run,
    )
    _cli._print_dependency_report(deps)
    if not deps.is_valid:
        logger.error("[Dependencies] Blocking issue ditemukan. Pipeline dihentikan.")
        _cli_renderer.flush_buffered_alerts()
        if raise_on_error:
            raise RuntimeError("Dependencies validation failed: blocking issue found.")
        raise SystemExit(1)

    # SB-1: Refresh macro rates (SBN 10Y) + FV-5: sector benchmarks; skip in dry-run.
    # Run in thread to avoid blocking the event loop during HTTP I/O.
    if not dry_run:
        await asyncio.to_thread(_maybe_refresh_macro_rates)
        await asyncio.to_thread(_maybe_refresh_sector_benchmarks)

    # Step 0a: Resolve one canonical execution regime before filter/intake.
    _cli_renderer.phase("Market Regime")
    regime_snapshot = await detect_market_regime()
    regime_snapshot_payload = regime_snapshot.model_dump()
    hmm_regime = await detect_hmm_regime()
    regime_context = resolve_execution_regime(
        rule_snapshot=regime_snapshot_payload,
        hmm_state=hmm_regime,
    )
    ORCHESTRATOR_CONFIG["market_regime"] = regime_snapshot_payload
    ORCHESTRATOR_CONFIG["hmm_regime"] = hmm_regime
    ORCHESTRATOR_CONFIG["regime_context"] = regime_context
    vol = regime_snapshot.volatility
    regime: str = regime_context["execution_regime"]
    regime_params = dict(regime_context["operational_params"])
    logger.info(
        "[Regime] execution={} reason={} -- applying overrides: {}",
        regime,
        regime_context["execution_regime_reason"],
        regime_params,
    )
    _apply_regime_params(regime_params)
    _cli_renderer.render_market_regime(
        volatility=vol,
        execution_regime=regime,
        regime_context=regime_context,
        regime_params=regime_params,
        snapshot=regime_snapshot_payload,
    )

    # Step 0b: Dependency Validation
    _cli_renderer.phase("Candidate Validation")
    if ticker_override:
        logger.info(
            "[CLI] Menggunakan --tickers override; "
            "skip quant filter dan top10_candidates.json."
        )
    else:
        # Reuse candidates only when screener strategy and execution authority
        # match the context that produced the cache.
        cached_mode = read_candidates_screener_mode(JSON_PATH)
        cached_execution_regime = read_candidates_execution_regime(JSON_PATH)
        cached_snapshot_contract_ok = _candidate_file_has_snapshot_contract(JSON_PATH)
        force_rerun = (
            _candidate_cache_context_mismatch(
                cached_mode=cached_mode,
                requested_mode=run_screener_mode,
                cached_execution_regime=cached_execution_regime,
                execution_regime=regime_context["execution_regime"],
            )
            or not cached_snapshot_contract_ok
        )
        validation = check_candidates_file(JSON_PATH, settings.CANDIDATES_MAX_AGE_HOURS)
        if force_rerun or not validation.is_valid:
            if force_rerun or settings.CANDIDATES_AUTO_RERUN:
                if force_rerun:
                    logger.info(
                        "[Validator] candidate cache context mismatch: "
                        f"screener={cached_mode!r}->{run_screener_mode!r}, "
                        f"execution_regime={cached_execution_regime!r}->"
                        f"{regime_context['execution_regime']!r}; "
                        f"snapshot_contract={cached_snapshot_contract_ok}; "
                        "rerun quant filter."
                    )
                else:
                    logger.info(
                        f"[Validator] {validation.message} Auto-rerun quant filter."
                    )
                if not maybe_rerun_quant_filter(
                    output_dir=OUTPUT_DIR,
                    mode=run_screener_mode,
                    execution_regime=regime_context["execution_regime"],
                    execution_regime_reason=regime_context["execution_regime_reason"],
                    trend_regime=(regime_context.get("trend_regime") or {}).get(
                        "label"
                    ),
                    volatility_regime=regime_context.get("volatility_regime"),
                ):
                    revalidation = check_candidates_file(
                        JSON_PATH, settings.CANDIDATES_MAX_AGE_HOURS
                    )
                    if not force_rerun and revalidation.is_valid:
                        logger.warning(
                            f"[Validator] Auto-rerun gagal (OOM/timeout) tapi file "
                            f"masih valid: {revalidation.message}. "
                            "Pipeline dilanjutkan dengan kandidat existing."
                        )
                    else:
                        logger.warning(f"[Validator] {validation.message}")
                        logger.error(
                            "[Validator] Auto-rerun gagal. Pipeline dihentikan."
                        )
                        _cli_renderer.flush_buffered_alerts()
                        if raise_on_error:
                            raise RuntimeError(
                                "Candidate validation auto-rerun failed."
                            )
                        return
            else:
                logger.warning(f"[Validator] {validation.message}")
                logger.error(
                    "[Validator] Set CANDIDATES_AUTO_RERUN=true untuk auto-rerun, "
                    "atau jalankan run_quant_filter.py secara manual."
                )
                _cli_renderer.flush_buffered_alerts()
                if raise_on_error:
                    raise RuntimeError(
                        "Candidate validation failed and CANDIDATES_AUTO_RERUN is false."
                    )
                return
        else:
            logger.info(f"[Validator] {validation.message}")

    # Step 1: Parse
    _cli_renderer.phase("Candidate Intake")
    pre_cio_rejections: list[dict[str, Any]] = []
    try:
        if ticker_override:
            tickers = ticker_override
            sector_map = {ticker: "unknown" for ticker in tickers}
            candidates_by_ticker: dict[str, dict] | None = None
            logger.info(f"[CLI] {len(tickers)} ticker dari --tickers: {tickers}")
        else:
            candidates = _load_quant_candidates(JSON_PATH)
            candidates = _apply_candidate_intake(
                candidates,
                rejected_results=pre_cio_rejections,
            )
            candidates = _apply_pre_cio_filters(
                candidates,
                regime,
                rejected_results=pre_cio_rejections,
            )
            candidates = _apply_critical_risk_filter(
                candidates,
                rejected_results=pre_cio_rejections,
            )
            tickers = parse_report(candidates=candidates) if candidates else []
            sector_map = parse_sector_map(candidates=candidates)
            candidates_by_ticker = {
                _candidate_ticker(c): c
                for c in candidates
                if validate_ticker(_candidate_ticker(c))
            }
            await _seed_candidate_market_snapshots(
                candidates,
                output_dir=OUTPUT_DIR,
            )
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"[Orchestrator] {e}")
        _cli_renderer.flush_buffered_alerts()
        if raise_on_error:
            raise RuntimeError(f"Candidate intake failed: {e}")
        return
    _cli_renderer.set_pipeline_status(
        "Candidates",
        "OK",
        (
            f"{len(tickers)} ticker"
            f"{'' if len(tickers) == 1 else 's'} (--tickers override)"
            if ticker_override
            else f"{len(tickers)} ticker{'' if len(tickers) == 1 else 's'} from candidates"
        ),
    )

    single_results = None
    if run_mode in {"single", "compare"}:
        _cli_renderer.phase("Single-Agent Baseline")
        analyzer = SingleAgentAnalyzer()
        with _cli_renderer.buffer_alerts():
            single_results = await analyzer.analyze_batch(
                tickers=tickers,
                run_id=ledger_run_id,
            )
            save_single_agent_results(single_results, OUTPUT_DIR)
        if run_mode == "single":
            _cli_renderer.render_pipeline_status()
            _cli_renderer.flush_buffered_alerts()
            return

    if not dry_run and tickers:
        _cli_renderer.phase("Provider Health")
        provider_health = await check_all_providers(tickers)
        _cli_renderer.render_provider_health(provider_health)
        logger.info(
            "[ProviderHealth] "
            f"stockbit_ok={provider_health.stockbit_ok} "
            f"yfinance_ok={provider_health.yfinance_ok} "
            f"can_proceed={provider_health.can_proceed} "
            f"failures={len(provider_health.failures)}"
        )
        _ledger_provider_check(ledger_run_id, provider_health)
        for failure in provider_health.failures:
            logger.warning(f"[ProviderHealth] {failure}")
        if not provider_health.can_proceed:
            decision = _plan_orchestrator_decision(
                ticker=None,
                run_id="provider_health",
                stage=PipelineStage.PROVIDER_HEALTH,
                provider_health=provider_health.model_dump(mode="json"),
            )
            if decision is None or decision.action is PlanAction.ABORT_BATCH:
                logger.error(
                    "[ProviderHealth] No price provider available. Pipeline dihentikan."
                )
                _cli_renderer.flush_buffered_alerts()
                if raise_on_error:
                    raise RuntimeError(
                        "No price provider available (provider health check failed)."
                    )
                return
            logger.warning(
                "[ProviderHealth] Planner allowed degraded mode despite provider "
                "health failure; continuing."
            )
    else:
        _cli_renderer.set_pipeline_status("Stockbit", "SKIP", "skipped (dry-run)")
        _cli_renderer.set_pipeline_status("yfinance", "SKIP", "skipped (dry-run)")

    # Step 2: Batch Debates
    _cli_renderer.render_pipeline_status()
    _cli_renderer.phase(
        "Per-Ticker Progress", "fetching data -> running analysis -> debating"
    )
    # abort_event dibuat di sini agar signal handler bisa mengaksesnya sebelum gather.
    abort_event = asyncio.Event()
    _setup_abort_signal(asyncio.get_running_loop(), abort_event)

    # Deterministic preflight must inspect every candidate before any LLM budget
    # reservation. The per-task charge inside run_batch_debates applies only to
    # setups that are actually debate-eligible.
    _PRO_CALLS_PER_TICKER_ESTIMATE = 8  # conservative; tune from telemetry
    _budget_usage = get_usage()
    _remaining_pro = _budget_usage["pro_budget"] - _budget_usage["pro_calls"]
    _max_feasible = _remaining_pro // _PRO_CALLS_PER_TICKER_ESTIMATE
    if _max_feasible <= 0:
        logger.warning(
            "[Orchestrator] Pro budget exhausted ({}/{} calls used); "
            "deterministic preflight will still run and eligible debates will abort.",
            _budget_usage["pro_calls"],
            _budget_usage["pro_budget"],
        )
    elif _max_feasible < len(tickers):
        logger.warning(
            "[Orchestrator] Estimated Pro budget covers {}/{} tickers "
            "({} remaining calls); all candidates still enter deterministic preflight.",
            _max_feasible,
            len(tickers),
            _remaining_pro,
        )

    _cli_renderer.start_batch_progress(tickers)
    try:
        with _cli_renderer.defer_logs():
            if dry_run:
                logger.info(
                    "[DryRun] Melewati run_batch_debates(); memakai mock results."
                )
                for ticker in tickers:
                    _cli_renderer.update_batch_progress(
                        ticker,
                        active="analysis",
                        fetching="done",
                        status="Generating mock analysis",
                        row_state="active",
                    )
                results = _generate_mock_debate_results(tickers, sector_map=sector_map)
            else:
                results = await run_batch_debates(
                    tickers,
                    sector_map=sector_map,
                    abort_event=abort_event,
                    run_id=ledger_run_id,
                    chamber_factory=chamber_factory,
                    candidates_by_ticker=candidates_by_ticker,
                    max_executable_debates=max(0, _max_feasible),
                )
            _enhance_completed_results(results, ledger_run_id, fetch_news=not dry_run)
            if pre_cio_rejections:
                for terminal_result in pre_cio_rejections:
                    _stamp_execution_regime_contract(terminal_result)
                    terminal_metadata = _result_metadata(terminal_result)
                    terminal_metadata["run_id"] = ledger_run_id
                    terminal_metadata["duration_seconds"] = 0.0
                results.extend(pre_cio_rejections)
            await _inject_forecast_reports(results)
            _log_risk_warn_distribution(results)
            for result in results:
                _cli_renderer.update_batch_progress_from_result(result)
            try:
                succeeded = sum(
                    1 for result in results if _result_status(result) == "success"
                )
            except Exception:
                succeeded = 0
            try:
                failed = len(results) - succeeded
                logger.info(
                    f"[Orchestrator] Debate summary: {succeeded} succeeded / {failed} failed"
                )
            except Exception as e:
                logger.warning(f"[Orchestrator] Debate summary failed: {e}")
    except KeyboardInterrupt:
        _cli_renderer.close_batch_progress()
        raise
    finally:
        _cli_renderer.stop_batch_progress()
        _cli_renderer.flush_deferred_logs()

    # Step 3: Score + Rank + Diversify
    _cli_renderer.phase("Scoring and Sizing")
    debate_records = load_debate_history(OUTPUT_DIR)
    realized_outcomes = load_realized_outcomes()
    _portfolio_state = portfolio_state or (user_config or {}).get("portfolio_state", {})
    buy_universe = [
        entry
        for entry in results
        if str((entry.get("verdict") or {}).get("rating") or "").upper()
        in {"BUY", "STRONG_BUY"}
    ]
    if _apply_circuit_breaker(buy_universe, _portfolio_state):
        top_n = []
    else:
        top_n = select_top_n(
            results,
            debate_records=debate_records,
            realized_outcomes=realized_outcomes,
            require_risk_deployable=True,
        )
    sizing_candidates = _build_sizing_candidates(top_n)
    batch_regime_context = dict(ORCHESTRATOR_CONFIG.get("regime_context") or {})
    _regime_tp = next(
        (
            e.get("trading_params")
            for e in top_n
            if isinstance(e.get("trading_params"), dict)
        ),
        batch_regime_context.get("execution_params"),
    )
    _regime_lbl = next(
        (
            str(e.get("execution_regime") or "").upper()
            for e in top_n
            if e.get("execution_regime")
        ),
        str(batch_regime_context.get("execution_regime") or ""),
    )
    if _regime_tp:
        user_config = {
            **user_config,
            "regime_params": {**_regime_tp, "label": _regime_lbl},
        }
    logger.debug(f"[Sizing DEBUG] user_config masuk: {user_config}")
    logger.debug(f"[Sizing DEBUG] jumlah candidates: {len(sizing_candidates)}")
    for c in sizing_candidates:
        logger.debug(
            f"[Sizing DEBUG] {c.get('ticker')} | "
            f"price={c.get('current_price')} | "
            f"stop={c.get('stop_loss')} | "
            f"rating={c.get('rating')} | "
            f"confidence={c.get('confidence')}"
        )
    sizing_result = calculate_positions(sizing_candidates, user_config)
    _enrich_sizing_with_risk_holds(sizing_result, top_n)
    logger.info(
        f"[Sizing] {sizing_result['summary']['total_positions']} posisi | "
        f"Deployed: Rp {sizing_result['summary']['total_deployed']:,.0f} "
        f"({sizing_result['summary']['deployed_pct'] * 100:.1f}%)"
    )
    _attach_sizing_to_results(results, sizing_result)
    _finalize_execution_decisions(results)
    execution_funnel_path = OUTPUT_DIR / "execution_funnel.json"
    save_execution_funnel(results, execution_funnel_path)
    _cli_renderer.render_scoring_summary(
        results=results,
        top_n=top_n,
        sizing_result=sizing_result,
    )

    # Step 4: Persist
    _cli_renderer.phase("Persistence and Reports")
    batch_timestamp = datetime.now(ZoneInfo(settings.DATETIME_TIMEZONE)).strftime(
        "%Y%m%d_%H%M%S"
    )
    for r in results:
        if isinstance(r, dict):
            r.setdefault("metadata", {})
            r["metadata"]["batch_timestamp"] = batch_timestamp
            if str(r["metadata"].get("run_id") or "").lower() in {"", "unknown"}:
                r["metadata"]["run_id"] = ledger_run_id
    save_merged_results(results, MERGED_RESULTS_PATH, seed_path=FULL_RESULTS_PATH)
    save_full_results(results, FULL_RESULTS_PATH)
    _ledger_artifact_write(
        run_id=ledger_run_id,
        artifact="merged_batch_results.json",
        path=MERGED_RESULTS_PATH,
        ticker_count=len(results),
    )
    _ledger_artifact_write(
        run_id=ledger_run_id,
        artifact="full_batch_results.json",
        path=FULL_RESULTS_PATH,
        ticker_count=len(results),
    )
    _ledger_artifact_write(
        run_id=ledger_run_id,
        artifact="execution_funnel.json",
        path=execution_funnel_path,
        ticker_count=len(results),
    )
    save_individual_debates_versioned(
        results,
        timestamp=batch_timestamp,
        output_dir=OUTPUT_DIR,
        record_backtest_memory=not dry_run,
    )
    _write_formatter_reports(
        results=results,
        run_id=ledger_run_id,
        output_dir=OUTPUT_DIR,
    )
    generate_top3_report(top_n, results, TOP3_REPORT_PATH, sizing_result=sizing_result)
    _write_batch_telemetry_report(
        output_dir=OUTPUT_DIR,
        run_id=ledger_run_id,
        batch_timestamp=batch_timestamp,
    )
    artifact_report = _log_artifact_validation(results)
    _check_report_consistency(
        batch_json_path=FULL_RESULTS_PATH,
        top3_md_path=TOP3_REPORT_PATH,
    )
    if run_mode == "compare" and single_results is not None:
        reporter: ComparisonReporter = DEFAULT_REPORTER
        report = reporter.build_comparison(
            single_results=single_results,
            multi_results_path=FULL_RESULTS_PATH,
        )
        comp_path = OUTPUT_DIR / "comparison_report.md"
        reporter.save_report(report, comp_path)
        md = reporter.format_markdown_table(report)
        logger.info(f"[Compare] Agreement rate: {report.agreement_rate:.0%}")
        logger.info(f"[Compare] Report saved: {comp_path}")
        _cli_renderer.render_comparison_markdown_as_table(
            md,
            path=comp_path,
            agreement_rate=report.agreement_rate,
        )
    persistence_outputs = [
        FULL_RESULTS_PATH,
        MERGED_RESULTS_PATH,
        execution_funnel_path,
        TOP3_REPORT_PATH,
        OUTPUT_DIR / "latest_batch_report.md",
        OUTPUT_DIR / "debates",
        OUTPUT_DIR / "telemetry" / "telemetry_log.jsonl",
        OUTPUT_DIR / "telemetry" / "latest_batch_report.txt",
        OUTPUT_DIR / "telemetry" / f"{ledger_run_id}_report.txt",
    ]
    if run_mode == "compare":
        persistence_outputs.extend(
            [
                OUTPUT_DIR / "comparison_report.md",
                OUTPUT_DIR / "comparison_report.json",
            ]
        )
    _cli_renderer.render_persistence_table(persistence_outputs)

    logger.info("[Orchestrator] Pipeline selesai")
    logger.info(f"[Orchestrator] Regime: {regime} | Top N: {len(top_n)}")
    logger.info(f"[Orchestrator] Full results -> {FULL_RESULTS_PATH}")
    logger.info(f"[Orchestrator] Merged ticker state -> {MERGED_RESULTS_PATH}")
    logger.info(f"[Orchestrator] Top {len(top_n)} report -> {TOP3_REPORT_PATH}")

    _cli_renderer.phase("Final Results")
    _cli_renderer.render_debate_summaries(results)
    _cli_renderer.render_final_results_table(results, top_n)

    # Tampilkan error summary dan top picks langsung di terminal (bukan raw markdown).
    _print_error_summary(results)
    if run_mode != "compare":
        _print_top3_summary(top_n, results)

    _cli_renderer.phase("Summary Footer")
    _cli_renderer.render_summary_footer(
        started_at=started_at,
        regime=str(regime),
        sizing_result=sizing_result,
        output_files=persistence_outputs,
        corrupt_lines=getattr(artifact_report, "corrupt_lines", 0),
    )
    _cli_renderer.flush_buffered_alerts()


# ---------------------------------------------------------------------------
# CLI helper functions â€" output terminal yang informatif
# ---------------------------------------------------------------------------


def _setup_abort_signal(
    loop: asyncio.AbstractEventLoop, abort_event: asyncio.Event
) -> None:
    """
    Pasang handler Ctrl+C yang graceful.

    - Ctrl+C pertama: set abort_event agar debate aktif selesai dan partial results disimpan.
    - Ctrl+C kedua: SystemExit(1) untuk force quit.
    - Windows: add_signal_handler tidak tersedia; fallback ke signal.signal().
    """
    count = {"n": 0}

    def _handler() -> None:
        count["n"] += 1
        _cli_renderer.stop_batch_progress()
        if count["n"] == 1:
            console.print(
                "\n[warn]Ctrl+C - menghentikan pipeline setelah debate aktif selesai...[/warn]"
            )
            console.print("  [muted]Tekan Ctrl+C sekali lagi untuk force quit.[/muted]")
            abort_event.set()
        else:
            console.print("[danger]Force quit.[/danger]")
            raise SystemExit(1)

    try:
        # Cara yang benar untuk asyncio (tidak tersedia di Windows).
        loop.add_signal_handler(signal.SIGINT, _handler)
    except NotImplementedError:
        # Windows fallback: signal.signal() bekerja di thread utama.
        import threading

        if threading.current_thread() is threading.main_thread():

            def _win_handler(signum: int, frame: object) -> None:  # noqa: ARG001
                _handler()

            signal.signal(signal.SIGINT, _win_handler)


def _print_error_summary(results: list[dict]) -> None:
    """
    Tampilkan ringkasan error yang actionable setelah pipeline selesai.
    Tidak muncul jika tidak ada error agar terminal tetap ringkas.
    """
    failed = [r for r in results if r.get("error")]
    if not failed:
        return

    console.print()
    console.print(Rule("[warn]Debug Summary[/warn]"))
    for r in failed:
        err = r["error"]
        # Kategorisasi cepat agar user tahu langkah selanjutnya tanpa buka log.
        if "Budget exhausted" in err:
            hint = "naikkan GEMINI_RPM_LIMIT atau kurangi jumlah ticker"
        elif "Schema validation" in err:
            hint = "cek format output DebateChamber; mungkin model LLM berubah"
        elif "Harga 0" in err or "price" in err.lower():
            hint = "cek koneksi price_fetcher atau ticker mungkin sudah delisted"
        elif "Aborted" in err:
            hint = "debate dihentikan oleh abort signal (budget/Ctrl+C)"
        else:
            hint = "lihat file log untuk traceback lengkap"

        console.print(f"  [danger]x[/danger] [bold]{r['ticker']}[/bold] - {err}")
        console.print(f"    [muted]-> {hint}[/muted]")
    console.print()


def _watchlist_rows(results: list[dict]) -> list[dict[str, Any]]:
    try:
        rows: list[dict[str, Any]] = []
        for entry in results:
            if entry.get("error"):
                continue
            verdict = (
                entry.get("verdict") if isinstance(entry.get("verdict"), dict) else {}
            )
            risk = (
                entry.get("risk_governor")
                if isinstance(entry.get("risk_governor"), dict)
                else {}
            )
            rating = str(verdict.get("rating") or "").upper()
            risk_status = str(risk.get("status") or "").lower()
            is_execution_hold = risk.get("sizing_allowed") is False and risk_status in {
                "watchlist_only",
                "conditional_deployable",
                "wait_for_pullback",
            }
            if rating != "HOLD" and not (
                rating in {"BUY", "STRONG_BUY"} and is_execution_hold
            ):
                continue
            low, high = _parse_entry_bounds(verdict.get("entry_price_range"))
            rr = float(verdict.get("risk_reward_ratio") or 0.0)
            reason_codes = (
                risk.get("reason_codes")
                if isinstance(risk.get("reason_codes"), list)
                else []
            )
            non_price = [c for c in reason_codes if c not in _PRICE_POSITION_CODES]
            reason = ", ".join(_reason_token_label(c) for c in non_price[:2]) or "-"
            rows.append(
                {
                    "ticker": str(entry.get("ticker") or "-").upper(),
                    "rating": rating,
                    "confidence": _coerce_confidence(verdict.get("confidence")) or 0.0,
                    "entry_low": low,
                    "entry_high": high,
                    "rr": rr,
                    "target_price": verdict.get("target_price"),
                    "reason": reason,
                }
            )
        return rows
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
        return []


def _strong_watchlist_rows(results: list[dict]) -> list[dict[str, Any]]:
    """Watchlist candidates with R/R >= 2.0 and confidence >= 0.35."""
    rows = [
        row
        for row in _watchlist_rows(results)
        if row.get("rr", 0.0) >= 2.0 and row.get("confidence", 0.0) >= 0.35
    ]
    return sorted(rows, key=lambda r: r.get("rr", 0.0), reverse=True)


def _print_strong_watchlist(strong_rows: list[dict[str, Any]]) -> None:
    """Render a Rich table of candidates withheld from executable sizing."""
    try:
        tbl = Table(
            box=box.SIMPLE,
            expand=False,
            show_edge=False,
            pad_edge=False,
            title=(
                "[amber]Watchlist Candidates[/amber]  -  "
                "No Sizing / Wait for Entry (R/R >= 2.0)"
            ),
        )
        tbl.add_column("Ticker", style="bold", no_wrap=True)
        tbl.add_column("Model", no_wrap=True)
        tbl.add_column("Conf", justify="right", no_wrap=True)
        tbl.add_column("R/R", justify="right", no_wrap=True)
        tbl.add_column("Entry Zone", no_wrap=True)
        tbl.add_column("Target", justify="right", no_wrap=True)
        tbl.add_column("Execution Hold", overflow="fold", max_width=28)
        for row in strong_rows:
            low = row.get("entry_low")
            high = row.get("entry_high")
            target = row.get("target_price")
            if low is not None and high is not None:
                entry_text = f"Rp {float(low):,.0f}-{float(high):,.0f}"
            elif low is not None:
                entry_text = f"Rp {float(low):,.0f}+"
            else:
                entry_text = "N/A"
            target_text = f"Rp {float(target):,.0f}" if target else "N/A"
            tbl.add_row(
                str(row.get("ticker", "-")),
                str(row.get("rating", "-")),
                f"{float(row.get('confidence') or 0):.0%}",
                f"{float(row.get('rr') or 0):.2f}x",
                entry_text,
                target_text,
                str(row.get("reason", "-")),
            )
        console.print()
        console.print(tbl)
    except Exception as exc:
        logger.warning(f"[Formatter] Strong watchlist table failed: {exc}")


def _print_watchlist_summary(watchlist_rows: list[dict[str, Any]]) -> None:
    """Fallback display when no BUY setups exist — kept for legacy call sites."""
    _print_strong_watchlist(watchlist_rows)


def _print_top3_summary(
    top_n: list[dict], all_results: list[dict] | None = None
) -> None:
    """
    Tampilkan ringkasan Top N hasil debate sebagai panel statis.
    """
    if not top_n:
        strong_rows = _strong_watchlist_rows(all_results or [])
        console.print()
        if strong_rows:
            console.print(
                Panel(
                    "[warn]No executable BUY setups. Watchlist candidates below.[/warn]",
                    title="[bold]Top Swing Trade Picks[/bold]",
                    subtitle=f"[muted]{TOP3_REPORT_PATH}[/muted]",
                    border_style="yellow",
                )
            )
            _print_strong_watchlist(strong_rows)
        else:
            console.print(
                Panel(
                    "[warn]No stocks qualify for execution (all HOLD/AVOID/SELL).[/warn]",
                    title="[bold]Top Swing Trade Picks[/bold]",
                    subtitle=f"[muted]{TOP3_REPORT_PATH}[/muted]",
                    border_style="yellow",
                )
            )
        return

    def _price(value: Any) -> str:
        if value in (None, ""):
            return "N/A"
        try:
            return f"Rp {float(value):,.0f}"
        except (TypeError, ValueError):
            return str(value)

    def _ratio(value: Any) -> str:
        if value in (None, ""):
            return "N/A"
        try:
            return f"{float(value):.2f}x"
        except (TypeError, ValueError):
            return str(value)

    table = Table(
        box=box.SIMPLE,
        expand=False,
        show_edge=False,
        pad_edge=False,
    )
    table.add_column("Rank", justify="right")
    table.add_column("Ticker", style="bold")
    table.add_column("Rating")
    table.add_column("Trade Conv", justify="right")
    table.add_column("R/R", justify="right")
    table.add_column("Entry Range")
    table.add_column("Target")
    table.add_column("SL")
    table.add_column("Action")

    for i, entry in enumerate(top_n, 1):
        v = entry.get("verdict", {})
        ticker = entry["ticker"]
        rating = v.get("rating", "N/A")
        score = entry.get("trade_conviction", entry.get("conviction_score", 0.0))
        entry_range = v.get("entry_price_range") or "N/A"
        style = _RATING_STYLE.get(rating, "white")
        risk = (
            entry.get("risk_governor")
            if isinstance(entry.get("risk_governor"), dict)
            else {}
        )
        action = str(risk.get("status") or "-").replace("_", " ")

        table.add_row(
            str(i),
            ticker,
            f"[{style}]{rating}[/{style}]",
            f"{score:.0%}",
            _ratio(v.get("risk_reward_ratio")),
            str(entry_range),
            _price(v.get("target_price")),
            _price(v.get("stop_loss")),
            action,
        )

    console.print()
    console.print(
        Panel(
            table,
            title="[bold]Top Swing Trade Picks[/bold]",
            subtitle=f"[muted]{TOP3_REPORT_PATH}[/muted]",
            border_style="green",
        )
    )

    strong_rows = _strong_watchlist_rows(all_results or [])
    if strong_rows:
        _print_strong_watchlist(strong_rows)


# ---------------------------------------------------------------------------
# Interactive CLI â€" Rich-powered terminal UI
# ---------------------------------------------------------------------------


class InteractiveCLI:
    """
    Antarmuka terminal interaktif berbasis Rich untuk orchestrator pipeline.

    Desain:
    - Setiap elemen interaksi (banner, prompt, umpan balik) dipisahkan ke method
      tersendiri agar mudah dipelihara tanpa mengubah logika utama.
    - console di-inject dari module-level sehingga tema tersentralisasi.
    - Subprocess berjalan di dalam Live spinner agar status proses tetap terlihat jelas.
    """

    # â"€â"€ Teks & konstanta tampilan â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    _BRAND_TITLE = "IDX Fundamental Analysis"
    _BRAND_SUB = "Quant Scouting  ->  Multi-Agent Debate  ->  CIO Verdict"

    def __init__(self, con: Console = console) -> None:
        self.con = con

    # â"€â"€ Private helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

    def _render_banner(self) -> None:
        """Tampilkan banner dan status sistem ringkas."""
        # â"€â"€ Judul produk â"€â"€
        title = Text(self._BRAND_TITLE, style="brand", justify="center")
        sub = Text(self._BRAND_SUB, style="muted", justify="center")
        self.con.print(
            Panel(
                Text.assemble(title, "\n", sub),
                border_style="cyan",
                padding=(1, 4),
                expand=False,
            )
        )

        # â"€â"€ Status candidates file (sinkron, tersedia tanpa async) â"€â"€
        # Regime tidak ditampilkan di sini karena fetch_ihsg_volatility adalah async;
        # regime akan muncul di log main() setelah detection selesai.
        validation = check_candidates_file(JSON_PATH, settings.CANDIDATES_MAX_AGE_HOURS)
        cand_icon = "[ok]OK[/ok]" if validation.is_valid else "[warn]WARN[/warn]"
        cand_msg = validation.message

        self.con.print(
            Panel(
                f"{cand_icon} candidates: [muted]{cand_msg}[/muted]",
                title="[muted]pipeline status[/muted]",
                border_style="dim",
                padding=(0, 2),
                expand=False,
            )
        )

    def _prompt_scraping(self) -> str:
        """
        Tampilkan prompt terminal dan kembalikan pilihan user.

        Pertanyaan: apakah scraping perlu dijalankan?
        - 'y'       -> jalankan scraping (data belum siap)
        - 'n'/Enter -> lewati scraping, asumsikan data sudah siap

        Rich Prompt.ask() dengan choices=["y", "n"] menangani validasi dan
        re-prompt secara internal. while-loop manual tidak diperlukan.
        """
        self.con.print()
        self.con.print(Rule("[step]Persiapan Pipeline[/step]"))
        self.con.print(
            "  [muted]Pipeline memerlukan data hasil scraping yang sudah tersedia di database.[/muted]"
        )
        self.con.print()

        return (
            Prompt.ask(
                "  [prompt]Jalankan scraping data terlebih dahulu?[/prompt]",
                choices=["y", "n"],
                default="n",
                show_choices=True,
                show_default=True,
                console=self.con,
            )
            .strip()
            .lower()
        )

    def _run_scraping(self, scrape_cmd: list[str] | None = None) -> bool:
        """
        Jalankan `main.py -f -o excel` di dalam Live spinner.

        Live spinner memberikan feedback visual bahwa sistem sedang bekerja.
        subprocess.run() bersifat blocking â€" pipeline orchestrator tidak akan
        mulai sampai scraping selesai atau gagal.

        Returns:
            True jika subprocess selesai dengan exit code 0.
            False jika gagal (exit code non-zero atau exception).
        """
        self.con.print()
        command = scrape_cmd or [sys.executable, "main.py", "-f", "-o", "excel"]
        # Spinner ditampilkan selama subprocess berjalan.
        spinner = Spinner(
            "dots", text=Text(" Scraping data saham IDX...", style="step")
        )

        with Live(spinner, console=self.con, refresh_per_second=10):
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        for line in (result.stdout or "").splitlines():
            cleaned = _clean_cli_text(line)
            if cleaned:
                self.con.print(f"  [step][Scraping][/step] {cleaned}")
        for line in (result.stderr or "").splitlines():
            cleaned = _clean_cli_text(line)
            if not cleaned:
                continue
            if "WARNING" in cleaned.upper() or "WARN" in cleaned.upper():
                self.con.print(f"  [warn]WARN [Scraping] {cleaned}[/warn]")
            else:
                self.con.print(f"  [muted][Scraping] {cleaned}[/muted]")

        if result.returncode == 0:
            self.con.print("  [ok]OK  Scraping selesai.[/ok]")
            return True
        else:
            self.con.print(
                f"  [danger]FAIL  Scraping gagal (exit code {result.returncode}). "
                "Periksa output di atas untuk detail.[/danger]"
            )
            return False

    def _print_pipeline_start(self) -> None:
        """Tampilkan garis pemisah sebelum pipeline utama dimulai."""
        self.con.print()
        self.con.print(Rule("[step]Memulai Pipeline Orkestrasi[/step]"))
        self.con.print()

    # â"€â"€ Public entrypoint â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

    def _print_dependency_report(self, deps: DependencyCheckResult) -> None:
        """Tampilkan hasil pemeriksaan awal sebelum pipeline berjalan."""
        show_hint = any((check.hint or "").strip() for check in deps.checks.values())
        table = Table(title="Pre-flight Checks", show_lines=False)
        table.add_column("Check", style="bold")
        table.add_column("Status")
        table.add_column("Message")
        if show_hint:
            table.add_column("Hint", style="muted")

        for check in deps.checks.values():
            status = "[ok]OK[/ok]" if check.is_valid else "[danger]FAIL[/danger]"
            row = [check.name, status, check.message]
            if show_hint:
                row.append(check.hint or "-")
            table.add_row(*row)

        self.con.print(table)
        if deps.blocking_issues:
            self.con.print(
                "[danger]Pipeline tidak dapat dimulai karena ada blocking issue.[/danger]"
            )

    def run(
        self,
        *,
        interactive: bool = True,
        skip_scraping: bool = False,
        scrape_cmd: list[str] | None = None,
    ) -> bool:
        """
        Titik masuk CLI: banner â†' prompt â†' (opsional) scraping â†' pipeline start.

        Returns:
            True jika pipeline boleh dilanjutkan.
            False jika pipeline harus dibatalkan (scraping gagal dan user
            memilih tidak melanjutkan).

        Method ini dipisah dari asyncio.run(main()) agar CLI layer dan
        pipeline layer tetap independen â€" mudah di-test secara terpisah.
        """
        if not interactive:
            return True

        self._render_banner()
        if skip_scraping:
            self.con.print(
                "  [ok]OK  Langkah scraping dilewati melalui `--skip-scraping`.[/ok]"
            )
            self._print_pipeline_start()
            return True

        jawaban = self._prompt_scraping()

        if jawaban == "y":
            # User meminta scraping dijalankan terlebih dahulu.
            scraping_ok = self._run_scraping(scrape_cmd=scrape_cmd)
            if not scraping_ok:
                # Scraping gagal. Tanya apakah pipeline tetap ingin dijalankan
                # dengan data yang mungkin tidak lengkap atau stale.
                self.con.print()
                konfirmasi = (
                    Prompt.ask(
                        "  [warn]Data scraping mungkin tidak lengkap. "
                        "Tetap lanjutkan pipeline?[/warn]",
                        choices=["y", "n"],
                        default="n",
                        show_choices=True,
                        show_default=True,
                        console=self.con,
                    )
                    .strip()
                    .lower()
                )
                if konfirmasi != "y":
                    self.con.print("  [muted]Pipeline dibatalkan oleh user.[/muted]")
                    return False
                self.con.print(
                    "  [warn]WARN Melanjutkan dengan data yang mungkin tidak lengkap.[/warn]"
                )
        else:
            # User memilih 'n' (atau tekan Enter): asumsikan data sudah siap.
            self.con.print(
                "  [ok]OK  Scraping dilewati. Pipeline akan dilanjutkan.[/ok]"
            )

        self._print_pipeline_start()
        return True


# Singleton CLI yang digunakan di __main__.
# Dibuat di sini agar bisa di-mock saat testing.
_cli = InteractiveCLI()


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse orchestrator CLI options while preserving interactive defaults."""
    global CLI_MODE, CLI_TICKERS_OVERRIDE, CLI_SCREENER_MODE

    parser = argparse.ArgumentParser(
        description="Run IDX swing-trade orchestration pipeline.",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Skip Rich banner and prompts; run headless.",
    )
    parser.add_argument(
        "--skip-scraping",
        action="store_true",
        help="Skip the pre-pipeline scraping prompt/step and assume data is ready.",
    )
    parser.add_argument(
        "--scrape-cmd",
        default=None,
        help='Custom scraping command used when interactive scraping is selected, e.g. "python main.py -f -o excel".',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use mock debate results; no Gemini debate calls are made.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Directory for candidates, full results, reports, and debate history.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Tampilkan baris log DEBUG lengkap di stderr, berguna untuk "
            "mendiagnosis error API, rate-limit, atau kegagalan parsing. "
            "Tanpa flag ini, hanya summary terstruktur yang ditampilkan."
        ),
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Tampilkan panel detail hasil debat untuk setiap ticker secara visual di terminal.",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        help=(
            "Override daftar kandidat dengan ticker spesifik, "
            "contoh: --tickers BBCA ADRO TLKM. "
            "Secara otomatis mengaktifkan --no-interactive dan --skip-scraping."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["multi", "single", "compare"],
        default="multi",
        help=(
            "multi: full debate pipeline (default)\n"
            "single: single-agent baseline\n"
            "compare: run both and generate comparison report"
        ),
    )
    parser.add_argument(
        "--screener-mode",
        choices=["momentum", "mean_reversion", "mean-reversion"],
        default="momentum",
        help=(
            "Quant-filter strategy when the pipeline (re)runs the screener:\n"
            "momentum (default, trend-following) or mean-reversion "
            "(oversold pullbacks in an uptrend). Forces a screener rerun."
        ),
    )
    parser.add_argument(
        "--portfolio-loss-pct",
        type=float,
        default=None,
        metavar="PCT",
        help=(
            "Today's realized portfolio loss as a positive percentage "
            "(e.g. 3.5 means -3.5%%). "
            "At >= 3%% the daily-loss circuit breaker halts all position sizing."
        ),
    )
    args = parser.parse_args(argv)
    CLI_TICKERS_OVERRIDE = None
    CLI_MODE = args.mode
    CLI_SCREENER_MODE = canonical_screener_mode(args.screener_mode)
    if args.tickers:
        try:
            args.tickers = _normalize_cli_tickers(args.tickers)
        except ValueError as exc:
            parser.error(str(exc))
        CLI_TICKERS_OVERRIDE = args.tickers
        args.no_interactive = True
        args.skip_scraping = True
        # Informasikan perubahan mode secara eksplisit agar tidak mengejutkan.
        # console tersedia di module level sebelum configure_cli_logging() dipanggil.
        console.print(
            f"  [muted]--tickers: headless mode aktif "
            f"(no-interactive + skip-scraping). "
            f"Ticker: {', '.join(args.tickers)}[/muted]"
        )
    return args


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    _ensure_utf8_stdout()
    args = _parse_cli_args()
    configure_cli_logging(verbose=args.verbose)
    configure_output_dir(Path(args.output_dir))
    scrape_cmd = shlex.split(args.scrape_cmd) if args.scrape_cmd else None

    # Jalankan antarmuka interaktif sebelum pipeline async dimulai.
    # InteractiveCLI.run() bersifat blocking dan sinkron secara sengaja:
    # input user tidak boleh bersaing dengan event loop asyncio.
    pipeline_ok = _cli.run(
        interactive=not args.no_interactive,
        skip_scraping=args.skip_scraping,
        scrape_cmd=scrape_cmd,
    )
    if not pipeline_ok:
        # User membatalkan setelah scraping gagal. Exit dengan kode non-zero
        # agar CI/CD atau skrip wrapper bisa mendeteksi kegagalan.
        sys.exit(1)

    user_config = (
        {"total_capital": 1_000_000.0, "max_loss_pct": 0.02, "max_positions": 5}
        if args.no_interactive
        else None
    )
    asyncio.run(
        main(
            dry_run=args.dry_run,
            output_dir=OUTPUT_DIR,
            user_config=user_config,
        )
    )
