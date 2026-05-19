"""
orchestrator.py â€” Automated Pipeline: Quant Scouting â†’ Multi-Agent Debate â†’ Top 3 Swing Trades.

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
  - [FIX-4] Urutan eksekusi: abort_check â†’ rate_limit â†’ semaphore â†’ abort_check
    â†’ budget_charge â†’ eksekusi. Budget hanya terpotong tepat sebelum API call.
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
import os
import random
import re
import shlex
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4


# [FIX-8] Import ZoneInfo di top-level, satu kali, dengan fallback untuk Python < 3.9.
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

# â”€â”€ Rich CLI imports â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
from rich.theme import Theme
from tenacity import retry, stop_after_attempt, wait_exponential

from core.backtest_memory import BacktestMemory, DEFAULT_MEMORY, TradeOutcome
from core.budget import BudgetExhaustedError, get_usage, reset_budget
from core.adaptive_planner import (
    DEFAULT_PLANNER,
    PlannerContext,
    PipelineStage,
    PlanAction,
)
from core.artifact_validator import validate_artifacts
from core.candidate_intake import normalize_batch
from core.dependency_validator import (
    DependencyCheckResult,
    check_all_dependencies,
    check_candidates_file,
    maybe_rerun_quant_filter,
)
from core.execution_ledger import DEFAULT_LEDGER, EventSeverity, EventType, LedgerEvent
from core.historical_scorer import (
    apply_historical_adjustment,
    compute_historical_win_rate,
    load_debate_history,
)
from core.ops_telemetry import DEFAULT_TELEMETRY, TickerMetric
from core.quant_filter.position_sizer import calculate_positions
from core.quant_filter.reporting import _build_position_summary
from core.portfolio_optimizer import diversify_portfolio
from core.prompt_pack_linter import lint_prompt_pack
from core.provider_health import check_all_providers
from core.regime import (
    RegimeType,
    classify_regime,
    fetch_ihsg_volatility,
    get_regime_params,
)
from core.report_consistency import check_consistency
from core.risk_governor import annotate_risk
from core.settings import settings
from core.comparison_reporter import DEFAULT_REPORTER, ComparisonReporter
from services.debate_prompt_registry import PROMPT_VERSION
from services.explainability_auditor import DEFAULT_AUDITOR
from services.news_fetcher import DEFAULT_FETCHER
from services.single_agent_analyzer import SingleAgentAnalyzer
from utils.logger_config import logger
from utils.price_fetcher import fetch_current_price


def _as_debate_message(m):
    from schemas.debate import DebateMessage

    if isinstance(m, dict):
        return DebateMessage(**m)
    return m


# Tema warna konsisten â€” ubah di sini, berlaku di seluruh CLI.
_CLI_THEME = Theme(
    {
        "brand": "bold cyan",
        "ok": "bold green",
        "warn": "bold yellow",
        "danger": "bold red",
        "muted": "dim white",
        "prompt": "bold white",
        "step": "bold magenta",
        "amber": "yellow",
    }
)
console = Console(theme=_CLI_THEME, highlight=False)

# Peta rating CIO â†’ warna Rich. Digunakan oleh live table dan result summary.
_RATING_STYLE: dict[str, str] = {
    "STRONG_BUY": "bold green",
    "BUY": "cyan",
    "HOLD": "dim",
    "SELL": "red",
    "AVOID": "red",
    "ERROR": "bold red",
    "ABORTED": "dim red",
    "debating": "yellow",
    "queued": "dim",
}


def _ensure_utf8_stdout() -> None:
    """Best-effort UTF-8 console output for Windows terminals."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _clean_cli_text(value: Any) -> str:
    """Repair common mojibake sequences that leaked into older CLI strings."""
    text = str(value)
    replacements = {
        "â€”": "–",
        "â€“": "–",
        "â†’": "->",
        "Ã—": "x",
        "âœ…": "✓",
        "âš ï¸": "WARNING",
        "ðŸ›‘": "STOP",
        "ðŸš¨": "ERROR",
        "â•": "=",
        "â”€": "-",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def _short_err(msg: str, max_len: int = 60) -> str:
    return msg if len(msg) <= max_len else msg[: max_len - 1] + "…"


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
        confidence = _format_cli_pct(verdict.get("confidence"))
        warnings = _result_warning_notes(result)
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
            status=_short_err(str(error or "; ".join(warnings) or "Complete")),
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
        table = Table(
            title="Live Batch Progress",
            box=box.SIMPLE,
            expand=True,
            show_edge=False,
            pad_edge=False,
        )
        for column in (
            "Ticker",
            "Fetching",
            "Analysis",
            "Risk",
            "Debating",
            "Done",
            "Rating",
            "Confidence",
            "Status",
        ):
            justify = "center" if column not in {"Ticker", "Status"} else "left"
            column_options: dict[str, Any] = {"justify": justify, "no_wrap": True}
            if column == "Status":
                column_options.update({"overflow": "ellipsis", "max_width": 60})
            table.add_column(column, **column_options)
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
                Text(_short_err(str(row.get("status") or "-"))),
                style=style,
            )
        return table

    def _step_cell(self, row: dict[str, Any], step: str) -> Any:
        if row.get("active") == step:
            return Spinner("dots", style="cyan")
        state = row.get(step)
        if state == "done":
            return Text("✓", style="ok")
        if state == "failed":
            return Text("✗", style="danger")
        if state == "warning":
            return Text("!", style="warn")
        return Text("–", style="muted")


class CliRenderer:
    """Structured Rich presentation boundary for the orchestrator CLI."""

    def __init__(self, con: Console = console) -> None:
        self.con = con
        self.verbose = False
        self.reset_run()

    def reset_run(self) -> None:
        self.warning_count = 0
        self.error_count = 0
        self.audit_entries: list[tuple[str, str, str]] = []
        self.output_files: list[str] = []
        self.budget_usage: dict[str, Any] | None = None
        self.current_phase: str | None = None
        self.phase_events: list[tuple[str, str, str]] = []
        self.regime_events: list[tuple[str, str, str]] = []
        self.dry_run_events: list[tuple[str, str, str]] = []
        self.rank_events: list[tuple[str, str, str]] = []
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
        for kind, message in pending:
            if kind == "warning":
                self._print_warning(message)
            else:
                self._print_error(message)

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
            return "x"
        if level == "WARNING":
            return "!"
        return "✓"

    def _marker_status(self, marker: str) -> str:
        if marker in {"✓", "OK"}:
            return "✓"
        if marker in {"x", "X"}:
            return "x"
        if marker in {"!", "WARNING"}:
            return "!"
        if marker in {"·", "•"}:
            return "✓"
        return "✓"

    def render_warning(self, message: str) -> None:
        if self._alert_buffer_depth:
            self._buffered_alerts.append(("warning", message))
            return
        self._print_warning(message)

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
        if self._alert_buffer_depth:
            self._buffered_alerts.append(("error", message))
            return
        self._print_error(message)

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
            self.set_pipeline_status("Provider health", "!", _short_err(str(data)))
            if not self.verbose:
                return
            table = Table(box=box.SIMPLE, show_edge=False, pad_edge=False)
            table.add_column("Status", justify="center")
            table.add_column("Message")
            table.add_row("!", Text(str(data)))
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
            "✓" if stockbit_ok else "x",
            "OK" if stockbit_ok else _short_err(_provider_failure(failures, "stockbit")),
        )
        self.set_pipeline_status(
            "yfinance",
            "✓" if yfinance_ok else "x",
            "OK" if yfinance_ok else _short_err(_provider_failure(failures, "yfinance")),
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
        regime: str,
        regime_params: dict[str, Any],
    ) -> None:
        vol_text = "-" if volatility is None else f"vol {volatility * 100:.2f}%"
        override_text = _format_overrides(regime_params)
        detail = f"{regime} ({vol_text})"
        if regime_params:
            detail = f"{detail}; {override_text}"
        self.set_pipeline_status("Market regime", "✓", detail)
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
        table.add_row("Regime", regime)
        table.add_row("Overrides", _format_overrides(regime_params))
        diagnostics = [
            message
            for status, _, message in self.regime_events
            if status != "✓"
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
        border = "red" if regime == "HIGH" else "green" if regime == "LOW" else "cyan"
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
            completed = "x" if error else "✓"
            risk_status = "!" if risk.get("sizing_allowed") is False else "✓"
            rows = [
                (
                    "Fetching data",
                    "✓",
                    "Mock context loaded" if dry_run else "Provider context requested",
                ),
                (
                    "Running analysis",
                    "✓",
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
        if self.verbose:
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
                self.con.print(
                    Panel(table, title=f"{ticker} Summary", border_style="green")
                )
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
        selected = {str(entry.get("ticker") or "").upper() for entry in top_n}
        table = Table(
            title="Final Results",
            box=box.SIMPLE,
            expand=True,
            show_edge=False,
            pad_edge=False,
        )
        for column in (
            "Ticker",
            "Rating",
            "Conf",
            "R/R",
            "Exp.Return",
            "Entry",
            "Target",
            "Stop Loss",
            "Risk Gov",
            "Selected",
        ):
            justify = (
                "right" if column in {"Conf", "R/R", "Target", "Stop Loss"} else "left"
            )
            column_options: dict[str, Any] = {"justify": justify}
            if column == "Selected":
                column_options.update(
                    {"no_wrap": True, "overflow": "ellipsis", "max_width": 60}
                )
            column_options.setdefault("no_wrap", True)
            column_options.setdefault("overflow", "ellipsis")
            table.add_column(column, **column_options)

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
            selected_text = (
                Text("✓ TOP PICK", style="ok")
                if ticker in selected
                else Text.assemble(
                    "– EXCLUDED ",
                    (_short_err(_exclusion_reason(result)), "muted"),
                )
            )
            # TODO: Current verdict schema exposes expected_return as display text;
            # if a future schema separates gross/net return, map that explicit field here.
            table.add_row(
                ticker,
                Text(rating, style=_rating_cell_style(rating)),
                _format_cli_pct(verdict.get("confidence")),
                _format_cli_ratio(verdict.get("risk_reward_ratio")),
                str(verdict.get("expected_return") or "–"),
                str(verdict.get("entry_price_range") or "–"),
                _format_cli_money(verdict.get("target_price")),
                _format_cli_money(verdict.get("stop_loss")),
                str(risk.get("status") or "–"),
                selected_text,
                style=_final_row_style(result, ticker in selected),
            )
        self.con.print(table)

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

    def render_summary_footer(
        self,
        *,
        started_at: float,
        regime: str,
        sizing_result: dict[str, Any] | None,
        output_files: list[Path],
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

        if self.verbose:
            table = Table(
                box=box.SIMPLE, expand=False, show_edge=False, pad_edge=False
            )
            table.add_column("Metric", style="bold")
            table.add_column("Value")
            table.add_row("Pipeline status", "Completed")
            table.add_row("Runtime", f"{elapsed:.1f}s")
            table.add_row("Token budget used", f"~{estimated_tokens:,} estimated tokens")
            table.add_row("Pro usage", f"{pro_calls}/{pro_budget}")
            table.add_row("Flash usage", f"{flash_calls}/{flash_budget}")
            table.add_row("Regime", regime)
            table.add_row(
                "Total deployed", _format_cli_money(summary.get("total_deployed"))
            )
            if "deployed_pct" in summary:
                table.add_row(
                    "Deployment pct",
                    f"{float(summary.get('deployed_pct') or 0.0) * 100:.1f}%",
                )
            table.add_row("Warnings", str(self.warning_count))
            if self.error_count:
                table.add_row("Errors", str(self.error_count))
            output_detail = "\n".join(output_file_strings) or "-"
            table.add_row("Output files", output_detail)
        else:
            table = Table(box=box.SIMPLE, expand=True, show_edge=False, pad_edge=False)
            table.add_column("Metric", style="bold", no_wrap=True)
            table.add_column("Value", no_wrap=True, overflow="ellipsis")
            table.add_column("Metric", style="bold", no_wrap=True)
            table.add_column("Value", no_wrap=True, overflow="ellipsis")
            names = [Path(path).name for path in output_file_strings]
            preview = ", ".join(names[:2])
            if len(names) > 2:
                preview = f"{preview}, +{len(names) - 2} more"
            output_detail = f"{len(names)} item(s): {preview}" if names else "-"
            deployed_pct = "-"
            if "deployed_pct" in summary:
                deployed_pct = f"{float(summary.get('deployed_pct') or 0.0) * 100:.1f}%"
            table.add_row("Status", "Completed", "Runtime", f"{elapsed:.1f}s")
            table.add_row(
                "Token budget",
                f"~{estimated_tokens:,}",
                "Pro / Flash",
                f"{pro_calls}/{pro_budget} | {flash_calls}/{flash_budget}",
            )
            table.add_row(
                "Regime",
                regime,
                "Deployed",
                f"{_format_cli_money(summary.get('total_deployed'))} ({deployed_pct})",
            )
            warning_text = str(self.warning_count)
            if self.error_count:
                warning_text = f"{warning_text} warn / {self.error_count} err"
            table.add_row("Warnings", warning_text, "Output files", output_detail)
        self.con.print(Panel(table, title="Summary Footer", border_style="green"))


class RichLogSink:
    def __init__(self, renderer: CliRenderer) -> None:
        self.renderer = renderer

    def __call__(self, message) -> None:
        self.renderer.handle_log_record(message.record)


def _literal_dict(raw: str) -> dict[str, Any]:
    try:
        value = ast.literal_eval(raw)
    except Exception:
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
    if status == "✓":
        return "ok"
    if status == "!":
        return "warn"
    if status == "x":
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
    confidence = _coerce_confidence(verdict.get("confidence"))
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
    confidence = _coerce_confidence(verdict.get("confidence"))
    if confidence is not None and confidence < 0.60:
        notes.append(f"Low confidence: {confidence:.0%}")
    risk = (
        result.get("risk_governor")
        if isinstance(result.get("risk_governor"), dict)
        else {}
    )
    if risk.get("sizing_allowed") is False:
        notes.append(
            str(risk.get("message") or risk.get("status") or "Risk governor hold")
        )
    if result.get("rr_warning"):
        notes.append(str(result["rr_warning"]))
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


def _format_overrides(regime_params: dict[str, Any]) -> str:
    if not regime_params:
        return "No overrides applied"
    return ", ".join(f"{key}={value}" for key, value in regime_params.items())


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


def configure_cli_logging(*, verbose: bool = False) -> None:
    """Route Loguru to files and mirror structured Rich output to the console."""
    global _CLI_LOGGING_CONFIGURED
    _ensure_utf8_stdout()
    _cli_renderer.verbose = verbose
    logger.remove()
    log_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} {file}:{line} | {message}"
    logger.add(
        settings.LOG_APP_FILENAME,
        format=log_format,
        level=settings.LOG_LEVEL,
        rotation="1 MB",
        retention="10 days",
        compression="zip",
        encoding="utf-8",
    )
    logger.add(
        "pipeline.log",
        format=log_format,
        level=settings.LOG_LEVEL,
        encoding="utf-8",
    )
    logger.add(_rich_log_sink, level=settings.LOG_LEVEL)
    if verbose:
        logger.add(
            sys.stderr,
            format=settings.LOG_FORMAT,
            level=settings.LOG_LEVEL,
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
ORCHESTRATOR_CONFIG: dict[str, Any] = {
    "conviction_weights": {
        "confidence": settings.CONVICTION_WEIGHT_CONFIDENCE,
        "rr_ratio": settings.CONVICTION_WEIGHT_RR_RATIO,
    },
    "rr_normalization_cap": settings.CONVICTION_RR_NORMALIZATION_CAP,
    "max_concurrent_debates": int(os.getenv("MAX_CONCURRENT_DEBATES", "3")),
    "excluded_ratings": {"AVOID", "HOLD", "SELL"},
    "top_n_selection": int(os.getenv("TOP_N_SELECTION", "3")),
    "max_price_retry_attempts": int(os.getenv("MAX_PRICE_RETRY_ATTEMPTS", "3")),
    "rpm_limit": int(os.getenv("GEMINI_RPM_LIMIT", "10")),
    "batch_delay": float(os.getenv("BATCH_DELAY_SECONDS", "0.5")),
    # Diisi oleh regime detection di main()
    "min_conviction_override": settings.PORTFOLIO_MIN_CONVICTION,
}

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
JSON_PATH = OUTPUT_DIR / "top10_candidates.json"
FULL_RESULTS_PATH = OUTPUT_DIR / "full_batch_results.json"
TOP3_REPORT_PATH = OUTPUT_DIR / "TOP_3_SWING_TRADES.md"

# Shorthand aliases â€” baca dari ORCHESTRATOR_CONFIG agar konsisten dengan
# regime override yang dilakukan di main() sebelum pipeline jalan.
EXCLUDED_RATINGS: set[str] = ORCHESTRATOR_CONFIG["excluded_ratings"]
# TOP_N_SELECTION dan MAX_CONCURRENT_DEBATES dibaca dinamis dari ORCHESTRATOR_CONFIG
# di dalam fungsi agar regime override yang dilakukan di main() terlihat.

# IDX saham biasa: tepat 4 huruf kapital, opsional suffix .JK
# Catatan: warrant/right issue (5 huruf) sengaja dikecualikan dari scope ini.
TICKER_PATTERN = re.compile(r"^[A-Z]{4}(?:\.JK)?$")
PROMPT_MANIFEST_PATH = "services/debate_prompts/manifest.json"
CLI_TICKERS_OVERRIDE: list[str] | None = None
CLI_MODE: str = "multi"


def _ledger_call(action: str, func, *args, **kwargs) -> None:
    try:
        func(*args, **kwargs)
    except Exception as exc:
        logger.warning(f"[ExecutionLedger] {action} failed: {exc}")


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
    global OUTPUT_DIR, JSON_PATH, FULL_RESULTS_PATH, TOP3_REPORT_PATH
    OUTPUT_DIR = output_dir
    JSON_PATH = OUTPUT_DIR / "top10_candidates.json"
    FULL_RESULTS_PATH = OUTPUT_DIR / "full_batch_results.json"
    TOP3_REPORT_PATH = OUTPUT_DIR / "TOP_3_SWING_TRADES.md"


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
        f"[ok]✓[/ok] Modal: Rp {capital:,.0f} | "
        f"Max loss: {max_loss * 100:.1f}% | Max posisi: {max_pos}"
    )

    return {
        "total_capital": capital,
        "max_loss_pct": max_loss,
        "max_positions": max_pos,
    }


# ---------------------------------------------------------------------------
# [FIX-1, FIX-2] SafeRateLimiter â€” sliding window, lock-safe
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
                    return  # Slot tersedia â€” keluar, lock dilepas oleh context manager

                # Hitung berapa lama sampai token tertua kadaluarsa.
                # _tokens terurut secara implisit karena selalu di-append dengan
                # timestamp yang monotonically increasing.
                wait_time = self._tokens[0] + self.period - now

            # [FIX-1] Lock sudah dilepas oleh `async with` di atas.
            # Sleep di luar lock â€” CancelledError di sini tidak menyentuh lock.
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
    if not ticker or not isinstance(ticker, str):
        return False
    return bool(TICKER_PATTERN.match(ticker.strip().upper()))


def _normalize_cli_tickers(tickers: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        ticker = str(raw or "").strip().upper()
        if not validate_ticker(ticker):
            raise ValueError(f"Ticker tidak valid: {raw}")
        ticker = ticker.removesuffix(".JK")
        if ticker not in seen:
            seen.add(ticker)
            normalized.append(ticker)
    return normalized


def _load_quant_candidates(json_path: Path = JSON_PATH) -> list[dict]:
    """
    Baca kandidat hasil quant filter sebagai list dict mentah.
    """
    if not json_path.exists():
        raise FileNotFoundError(
            f"Candidates tidak ditemukan di {json_path}. "
            "Jalankan run_quant_filter.py terlebih dahulu."
        )

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(
            f"Format candidates tidak valid di {json_path}: expected list."
        )
    return [row for row in data if isinstance(row, dict)]


def _candidate_ticker(candidate: dict) -> str:
    raw = candidate.get("ticker") or candidate.get("Ticker") or ""
    return raw.strip().upper() if isinstance(raw, str) else ""


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


def _apply_candidate_intake(candidates: list[dict]) -> list[dict]:
    """Validate candidate intake without changing the quant-filter payload shape."""
    normalized, rejected = normalize_batch(
        [_candidate_for_intake(c) for c in candidates]
    )
    for item in rejected:
        rejected_candidate = item.get("candidate", {})
        ticker = (
            rejected_candidate.get("ticker")
            or rejected_candidate.get("Ticker")
            or "UNKNOWN"
        )
        decision = _plan_orchestrator_decision(
            ticker=str(ticker),
            run_id="candidate_intake",
            stage=PipelineStage.CANDIDATE_INTAKE,
        )
        if decision is not None and decision.action is PlanAction.SKIP_TICKER:
            logger.info(f"[CandidateIntake] Planner confirmed skip for {ticker}")
        logger.warning(f"[CandidateIntake] Rejected {ticker}: {item.get('error')}")

    if not normalized:
        logger.warning(
            "[CandidateIntake] No candidates normalized; continuing with raw candidates "
            "for backward compatibility."
        )
        return candidates

    valid_tickers = {candidate.ticker for candidate in normalized}
    filtered = [
        candidate
        for candidate in candidates
        if _candidate_ticker(candidate) in valid_tickers
    ]
    logger.info(
        f"[CandidateIntake] {len(filtered)} valid candidates, {len(rejected)} rejected."
    )
    return filtered


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


def _apply_pre_cio_filters(candidates: list[dict], regime: str) -> list[dict]:
    """
    Hard filter sebelum masuk CIO â€” buang kandidat yang tidak layak
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
            continue

        # Counter-trend di HIGH regime â†’ skip langsung
        # Di NORMAL/LOW regime â†’ biarkan masuk tapi CIO beri penalty
        if regime == "HIGH" and _candidate_ma200_context(c) == "BELOW":
            logger.info(f"[PreCIO] {ticker} SKIP – counter-trend di HIGH regime")
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

    # [FIX-9] `for row in data` â€” syntax error di versi sebelumnya diperbaiki.
    for row in data:
        raw = row.get("Ticker") or row.get("ticker") or ""
        ticker = raw.strip().upper() if raw else ""

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
    Tidak raise â€” file hilang/corrupt hanya menghasilkan dict kosong.
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
        ticker = raw.strip().upper() if raw else ""
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


def _empty_result(ticker: str, error: str, sector_key: str = "unknown") -> dict:
    """
    Bentuk seragam untuk debate yang gagal atau di-abort.

    [FIX-10] Status selalu FAILED â€” fungsi ini tidak pernah dipanggil
    untuk kondisi sukses, jadi tidak ada dead code `else "SUCCESS"`.
    """
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
        "metadata": {},
        "error": error,
        "status": "failed",
        "conviction_score": 0.0,
        "sector_key": sector_key,
    }


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _result_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    metadata = entry.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        entry["metadata"] = metadata
    return metadata


def _parse_price_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace("Rp", "").replace("rp", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "").replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_entry_low(entry_price_range: Any) -> float | None:
    entry_low = str(entry_price_range or "").split("-", maxsplit=1)[0].strip()
    return _parse_price_value(entry_low)


def _coerce_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence > 1.0:
        confidence = confidence / 100.0
    return max(0.0, min(confidence, 1.0))


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


def _attach_news_signal(ticker: str, result: dict[str, Any]) -> None:
    try:
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
        risk_entry = {
            "ticker": ticker,
            "verdict": verdict,
            "current_price": verdict.get("current_price"),
            "risk_context": {
                "atr14": atr14,
                "avg_volume": avg_volume,
                "exdate_days": None,
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
            confidence=_coerce_confidence(verdict.get("confidence")),
            debate_rounds=int(result.get("debate_rounds") or 0),
            duration_seconds=elapsed,
            flash_calls=_metadata_int(metadata, "flash_calls"),
            pro_calls=_metadata_int(metadata, "pro_calls"),
            rag_chunks_selected=_metadata_int(metadata, "rag_chunks_selected"),
            rag_chunks_considered=_metadata_int(metadata, "rag_chunks_considered"),
            rag_token_estimate=_metadata_int(metadata, "rag_token_estimate"),
            provider_errors=[],
            has_stale_data=False,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        DEFAULT_TELEMETRY.record_ticker(metric)
    except Exception as e:
        logger.warning(f"[Telemetry] {ticker}: failed: {e}")


def _enhance_completed_results(
    results: list[dict],
    run_id: str,
    *,
    fetch_news: bool = True,
) -> None:
    for result in results:
        try:
            ticker = str(result.get("ticker") or "UNKNOWN").upper()
            status = _result_status(result)
            result["status"] = status
            if fetch_news:
                _attach_news_signal(ticker, result)
            if status == "success" and result.get("verdict"):
                _attach_risk_governor_to_result(
                    ticker=ticker,
                    run_id=run_id,
                    result=result,
                )
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


def _write_explainability_audit(
    *,
    output_dir: Path,
    ticker: str,
    result: dict[str, Any],
) -> None:
    try:
        packet = DEFAULT_AUDITOR.build_audit_packet(result)
        DEFAULT_AUDITOR.log_packet(packet)
        audit_path = output_dir / "debates" / ticker / "latest_audit.txt"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(
            DEFAULT_AUDITOR.format_report(packet),
            encoding="utf-8",
        )
        logger.info(f"[Audit] {ticker}: {packet.one_line_summary}")
    except Exception as e:
        logger.warning(f"[Audit] {ticker}: failed: {e}")


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

        entry_price = _parse_entry_low(verdict.get("entry_price_range"))
        target_price = _parse_price_value(verdict.get("target_price"))
        stop_loss = _parse_price_value(verdict.get("stop_loss"))
        if entry_price is None or target_price is None or stop_loss is None:
            raise ValueError("missing trade price fields")
        confidence = _coerce_confidence(verdict.get("confidence"))
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
            )
        )
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
        telemetry_dir = output_dir / "telemetry"
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        (telemetry_dir / "latest_batch_report.txt").write_text(
            report_text,
            encoding="utf-8",
        )
        (telemetry_dir / f"{run_id}_report.txt").write_text(
            report_text,
            encoding="utf-8",
        )
        logger.info(f"[Telemetry] Batch report saved for {run_id}")
    except Exception as e:
        logger.warning(f"[Telemetry] Batch report failed: {e}")


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


async def _run_single_debate(ticker: str, chamber: Any) -> dict:
    """
    Jalankan debate untuk satu ticker: chamber.run() owns market-data prefetch â†’ validasi schema.

    Retry ada di dalam DebateChamber._invoke_llm (tenacity). Tidak ada retry
    tambahan di sini untuk menghindari efek perkalian (9Ã— worst case).
    """
    from schemas.debate import CIOVerdict

    logger.info(f"[Debate] Mulai: {ticker}")

    try:
        result = await chamber.run(ticker)
        if result.get("error") is not None:
            raise RuntimeError(result["error"])

        verdict_dict: dict = {}
        if result.get("final_verdict"):
            try:
                verdict_raw = json.loads(result["final_verdict"])
                verdict_dict = CIOVerdict(**verdict_raw).model_dump()
            except ValidationError as e:
                logger.error(f"[Debate] Schema tidak valid untuk {ticker}: {e}")
                return _empty_result(ticker, f"Schema validation failed: {e}")
            except json.JSONDecodeError as e:
                logger.error(f"[Debate] JSON rusak untuk {ticker}: {e}")
                return _empty_result(ticker, f"JSON decode error: {e}")

        logger.info(f"[Debate] ✓ Selesai: {ticker}")
        disagreement_type = result.get("disagreement_type")
        if disagreement_type:
            logger.info(f"[Debate] {ticker} disagreement_type={disagreement_type}")
        debate_history = [
            _as_debate_message(m) for m in result.get("debate_history", [])
        ]
        return {
            "ticker": result["ticker"],
            "verdict": verdict_dict,
            "debate_rounds": result["round_count"],
            "consensus_reached": result.get("consensus_reached", False),
            "consensus_method": result.get("consensus_method"),
            "dissenting_agents": result.get("dissenting_agents", []),
            "agent_votes": result.get("agent_votes", []),
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
            "raw_data_summary": result["raw_data"],
            "metadata": result.get("metadata", {}),
            "error": None,
            "status": "success",
            "conviction_score": 0.0,  # Diisi oleh select_top3
        }

    except BudgetExhaustedError as e:
        logger.error(f"[Debate] STOP Budget habis saat debating {ticker}: {e}")
        _cli_renderer.record_failure_detail(ticker, traceback.format_exc())
        return _empty_result(ticker, f"Budget exhausted: {e}")
    except Exception as e:
        logger.error(f"[Debate] ERROR {ticker} gagal: {e}")
        _cli_renderer.record_failure_detail(ticker, traceback.format_exc())
        return _empty_result(ticker, str(e))


async def run_batch_debates(
    tickers: list[str],
    sector_map: dict[str, str] | None = None,
    abort_event: asyncio.Event | None = None,
    run_id: str | None = None,
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

    # [FIX-3] Gunakan abort_event yang di-inject, atau buat baru jika tidak ada.
    if abort_event is None:
        abort_event = asyncio.Event()

    # [FIX-4] Budget state dengan lock dedikasi.
    budget_state = {"spent": 0}
    budget_lock = asyncio.Lock()

    # Batas budget per-run diambil dari core.budget; fallback ke jumlah ticker.
    try:
        usage = get_usage()
        max_budget = usage.get("pro_budget", len(tickers))
    except Exception:
        max_budget = len(tickers)

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
                if run_id:
                    metadata.setdefault("run_id", run_id)
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

            # 2. Tunggu slot rate limit
            _set_status(ticker, "QUEUED", step="fetching data")
            await rate_limiter.acquire()

            # 3. Tunggu slot konkurensi
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
                        abort_event.set()
                        logger.warning(
                            f"[{ticker}] Budget habis saat charge -- abort ditetapkan"
                        )
                        _set_status(ticker, "ABORTED", step="stopped")
                        return _finish_result(
                            _empty_result(
                                ticker, "Budget exhausted at charge point", sector_key
                            )
                        )

                    budget_state["spent"] += 1
                    budget_charged = True  # Set di dalam lock, tepat setelah increment
                    current = budget_state["spent"]

                logger.info(f"[{ticker}] Budget terpakai: {current}/{max_budget}")

                # 6. Eksekusi
                try:
                    result = await _run_single_debate(ticker, chamber)

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
                        _empty_result(ticker, f"Budget exhausted: {e}", sector_key)
                    )

                except Exception as e:
                    logger.error(f"[{ticker}] Error saat eksekusi: {e}")
                    _cli_renderer.record_failure_detail(ticker, traceback.format_exc())
                    _set_status(ticker, "ERROR", step="warning")
                    return _finish_result(_empty_result(ticker, str(e), sector_key))

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
            # untuk logika apapun â€” abort dideteksi via abort_event.is_set().
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
                _empty_result(ticker, "Task cancelled by abort event", sector_key)
            )

        except Exception as e:
            logger.exception(f"[{ticker}] Error tak terduga di _guarded: {e}")
            _cli_renderer.record_failure_detail(ticker, traceback.format_exc())
            _set_status(ticker, "ERROR", step="warning")
            return _finish_result(
                _empty_result(ticker, f"Orchestrator error: {e}", sector_key)
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
        from services.debate_chamber import DebateChamber

        chamber = DebateChamber()
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
            safe_results.append(_empty_result(ticker, str(res), sector_key))
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


def compute_conviction_score(
    verdict: dict,
    ticker: str | None = None,
    debate_records: list[dict] | None = None,
) -> tuple[float, str | None]:
    """
    Hitung Conviction Score = W_confidence Ã— CIO Confidence + W_rr Ã— Normalized R/R.

    Weights dibaca dari ORCHESTRATOR_CONFIG (dapat di-override via env vars di settings).
    R/R dinormalisasi ke [0, 1] dengan cap dari ORCHESTRATOR_CONFIG['rr_normalization_cap'].
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

    rr_score = min(max(rr_ratio / rr_cap, 0.0), 1.0)
    base_score = (w_confidence * confidence) + (w_rr * rr_score)

    # Historical adjustment â€” hanya jika data tersedia dan cukup
    if ticker and debate_records is not None:
        win_rate = compute_historical_win_rate(ticker, debate_records)
        base_score = apply_historical_adjustment(base_score, win_rate)

    return base_score, warning


def select_top_n(
    results: list[dict],
    debate_records: list[dict] | None = None,
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

        score, warning = compute_conviction_score(
            verdict,
            ticker=entry.get("ticker"),
            debate_records=debate_records,
        )
        entry["conviction_score"] = round(score, 4)
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
    )

    logger.info(
        f"[Rank] Top {len(top_n)} dipilih: {[t['ticker'] for t in top_n]} "
        f"(dari {len(scorable)} eligible, top_n={top_n_cfg}, "
        f"sector_cap={max_per_sector}, min_conviction={min_conviction:.0%})"
    )
    return top_n


# Backward-compatibility alias â€” deprecate secara bertahap
select_top3 = select_top_n


# ---------------------------------------------------------------------------
# Step 4: Persistence & reporting
# ---------------------------------------------------------------------------


def save_full_results(results: list[dict], path: Path = FULL_RESULTS_PATH) -> None:
    """Simpan semua hasil debate sebagai JSON tunggal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"[Persist] Full results -> {path}")


def save_single_agent_results(
    results: list,
    output_dir: Path = OUTPUT_DIR,
) -> None:
    """Persist standalone single-agent baseline results per ticker."""
    single_dir = output_dir / "single_agent"
    single_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        path = single_dir / f"{result.ticker}.json"
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
    debates_dir = output_dir / "debates"
    debates_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for entry in results:
        # Hanya simpan yang punya verdict (berhasil didebat)
        if not entry.get("verdict") or entry.get("error"):
            continue

        ticker = entry["ticker"]
        # Gunakan format nama yang konsisten dengan historical_scorer.py
        file_path = debates_dir / f"{ticker}_debate.json"

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
) -> None:
    """
    Simpan hasil debate per ticker sebagai snapshot immutable.

    Format baru: output/debates/{TICKER}/v{timestamp}/{TICKER}_debate.json.
    Untuk backward compatibility, file flat output/debates/{TICKER}_debate.json
    tetap ditulis agar historical_scorer lama tetap membaca latest record.
    """
    debates_dir = output_dir / "debates"
    debates_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for entry in results:
        if not entry.get("verdict") or entry.get("error"):
            continue

        ticker = entry["ticker"]
        ticker_dir = debates_dir / ticker
        version_dir = ticker_dir / f"v{timestamp}"
        version_dir.mkdir(parents=True, exist_ok=True)

        payload = dict(entry)
        payload.setdefault("metadata", {})
        payload["metadata"] = {
            **payload["metadata"],
            "batch_timestamp": timestamp,
            "versioned_output": True,
        }

        version_file = version_dir / f"{ticker}_debate.json"
        version_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        latest_file = ticker_dir / "latest_debate.json"
        latest_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        legacy_file = debates_dir / f"{ticker}_debate.json"
        legacy_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _write_explainability_audit(
            output_dir=output_dir,
            ticker=ticker,
            result=payload,
        )
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
        if entry.get("verdict") and not entry.get("error") and entry.get("ticker"):
            return output_dir / "debates" / entry["ticker"] / "latest_debate.json"
    for entry in results:
        if entry.get("ticker"):
            return output_dir / "debates" / entry["ticker"] / "latest_debate.json"
    return output_dir / "debates" / "UNKNOWN" / "latest_debate.json"


def _log_artifact_validation(results: list[dict]) -> None:
    report = validate_artifacts(
        FULL_RESULTS_PATH,
        TOP3_REPORT_PATH,
        _latest_debate_path_for_validation(results, OUTPUT_DIR),
    )
    for warning in report.warnings:
        logger.warning(f"[ArtifactValidator] {warning}")
    if report.valid:
        logger.info("[ArtifactValidator] Output artifacts valid.")
    else:
        logger.error(
            f"[ArtifactValidator] Output artifact validation failed: {report.errors}"
        )


def _build_sizing_candidates(top_n: list[dict]) -> list[dict]:
    """Flatten selected orchestrator entries into position-sizer input records."""
    candidates: list[dict] = []
    for entry in top_n:
        risk = entry.get("risk_governor")
        if isinstance(risk, dict) and risk.get("sizing_allowed") is False:
            continue
        verdict = entry.get("verdict") or {}
        candidates.append(
            {
                "ticker": entry.get("ticker") or verdict.get("ticker"),
                "current_price": verdict.get("current_price"),
                "stop_loss": verdict.get("stop_loss"),
                "rating": verdict.get("rating"),
                "confidence": verdict.get("confidence"),
                "rr_ratio": verdict.get("risk_reward_ratio"),
                "target_price": verdict.get("target_price"),
                "expected_return": verdict.get("expected_return"),
            }
        )
    return candidates


def _annotate_risk_governor(top_n: list[dict]) -> None:
    """Attach deterministic actionability metadata before sizing/reporting."""
    for entry in top_n:
        decision = annotate_risk(entry)
        if not decision.sizing_allowed:
            logger.info(
                f"[RiskGovernor] {decision.ticker}: {decision.status} "
                f"({', '.join(decision.reason_codes)})"
            )


def _risk_holds(top_n: list[dict]) -> list[dict]:
    holds: list[dict] = []
    for entry in top_n:
        risk = entry.get("risk_governor")
        if not isinstance(risk, dict) or risk.get("sizing_allowed") is not False:
            continue
        holds.append(
            {
                "ticker": entry.get("ticker") or risk.get("ticker"),
                "status": risk.get("status"),
                "message": risk.get("message"),
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

        verdict = CIOVerdict(
            ticker=ticker,
            rating=rng.choices(profile["ratings"], weights=profile["weights"], k=1)[0],
            confidence=round(rng.uniform(*profile["confidence"]), 2),
            fair_value=fair_value,
            entry_price_range=f"{entry_low} - {entry_high}",
            target_price=target,
            stop_loss=stop_loss,
            current_price=base_price,
            timeframe="1-3 Months",
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
            dissenting_agents=["bear"],
        ).model_dump()

        results.append(
            {
                "ticker": ticker,
                "verdict": verdict,
                "debate_rounds": 3,
                "consensus_reached": True,
                "consensus_method": "voting",
                "dissenting_agents": ["bear"]
                if verdict["rating"] in {"BUY", "STRONG_BUY"}
                else [],
                "agent_votes": [
                    {
                        "agent": "fundamental_scout",
                        "position": "BUY",
                        "confidence": 0.66,
                        "round": 0,
                    },
                    {
                        "agent": "chartist",
                        "position": "BUY",
                        "confidence": 0.64,
                        "round": 0,
                    },
                    {
                        "agent": "sentiment_specialist",
                        "position": "HOLD",
                        "confidence": 0.55,
                        "round": 0,
                    },
                    {
                        "agent": "bull",
                        "position": "BUY",
                        "confidence": 0.70,
                        "round": 1,
                    },
                    {
                        "agent": "bear",
                        "position": "AVOID",
                        "confidence": 0.58,
                        "round": 1,
                    },
                ],
                "debate_history": [
                    {
                        "role": "bull",
                        "content": "Dry-run bull case: setup teknikal mock mendukung entry bertahap.\n\nPosition: BUY\nAgent Confidence: 0.70",
                        "round": 1,
                        "position": "BUY",
                        "confidence": 0.70,
                    },
                    {
                        "role": "bear",
                        "content": "Dry-run bear case: kualitas data sintetis tidak boleh dipakai untuk trading.\n\nPosition: AVOID\nAgent Confidence: 0.58",
                        "round": 1,
                        "position": "AVOID",
                        "confidence": 0.58,
                    },
                    {
                        "role": "devils_advocate",
                        "content": "Dry-run challenge: konfirmasi ulang semua level harga dengan data live.",
                        "round": 2,
                        "position": "HOLD",
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
                    "prompt_version": PROMPT_VERSION,
                },
            }
        )

    logger.info(f"[DryRun] Generated {len(results)} mock debate results.")
    return results


def get_local_timestamp() -> str:
    """
    Kembalikan timestamp lokal dalam timezone yang dikonfigurasi (default: Asia/Jakarta).

    [FIX-8] ZoneInfo sudah di-import di top-level â€” fungsi ini tidak perlu
    import lokal yang dieksekusi setiap kali dipanggil.
    """
    utc_now = datetime.now(timezone.utc)
    local_tz = ZoneInfo(settings.DATETIME_TIMEZONE)
    return utc_now.astimezone(local_tz).strftime("%Y-%m-%d %H:%M %Z")


def _extract_winning_argument(entry: dict) -> str:
    """Ambil argumen Bull terakhir (paling refined) dari history debate."""
    bull_args = []
    for raw in entry.get("debate_history", []):
        try:
            msg = _as_debate_message(raw)
        except Exception:
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
        except Exception:
            continue
        if msg.role == "devils_advocate":
            da_args.append(msg.content)
    if not da_args:
        return "Tidak ada challenge devil's advocate yang tercatat."
    arg = da_args[-1]
    return arg[:397] + "..." if len(arg) > 400 else arg


def generate_top3_report(
    top_n: list[dict],
    all_results: list[dict],
    path: Path = TOP3_REPORT_PATH,
    sizing_result: dict | None = None,
) -> str:
    """
    Generate laporan Markdown eksekutif untuk Top N swing trade.

    [FIX-7] conviction_score di-reuse dari entry dict yang sudah diisi oleh
    select_top3 â€” tidak ada pemanggilan ulang compute_conviction_score.
    Untuk ticker error (tidak masuk select_top3), skor default 0.0.
    """
    timestamp = get_local_timestamp()
    total_debated = len(all_results)
    selected_count = len(top_n)
    eligible = sum(
        1
        for r in all_results
        if r.get("verdict", {}).get("rating") not in EXCLUDED_RATINGS
        and r.get("verdict")
    )

    lines: list[str] = [
        f"# TOP {selected_count} HIGH-CONVICTION IHSG SWING TRADES",
        "",
        f"> **Generated**: {timestamp}",
        "> **Pipeline**: Quant Scouting -> Multi-Agent Debate -> CIO Verdict",
        f"> **Stocks Debated**: {total_debated} | **Eligible (BUY/STRONG_BUY)**: {eligible} | **Selected**: {selected_count}",
        "",
        "---",
        "",
    ]

    if not top_n:
        lines += [
            f"**Tidak ada saham yang memenuhi syarat untuk Top {ORCHESTRATOR_CONFIG['top_n_selection']}.**",
            "",
            "Semua kandidat diberi rating HOLD, AVOID, atau SELL oleh CIO Judge. "
            "Tidak ada swing trade high-conviction yang teridentifikasi dalam batch ini.",
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
        score = entry.get("conviction_score", 0.0)
        disagreement = entry.get("disagreement_type")
        consensus_method = entry.get("consensus_method") or "unknown"
        dissenting_agents = entry.get("dissenting_agents") or []
        consensus_label = (
            f"Reached ({consensus_method})"
            if entry.get("consensus_reached")
            else f"No ({consensus_method}; {disagreement or 'unknown'})"
        )

        lines += [
            f"## #{rank} - {ticker}",
            "",
            "### Final Rating & Confidence",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| **Rating** | `{v.get('rating', 'N/A')}` |",
            f"| **CIO Confidence** | {v.get('confidence', 0):.0%} |",
            f"| **Conviction Score** | {score:.2%} |",
            f"| **Debate Consensus** | {consensus_label} |",
            f"| **Dissenting Agents** | {', '.join(dissenting_agents) if dissenting_agents else '-'} |",
            f"| **Timeframe** | {v.get('timeframe', '1-3 Months')} |",
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
            (
                f"| **Fair Value** | Rp {v['fair_value']:,.0f} |"
                if v.get("fair_value")
                else "| **Fair Value** | N/A |"
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
        "| Ticker | Rating | Confidence | R/R Ratio | Conviction Score | Actionability | Consensus | Method | Dissenting Agents | Disagreement | Status |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    # [FIX-7] Untuk ticker yang sudah masuk select_top3, skor sudah ada di entry.
    # Untuk ticker error/excluded, ambil dari entry atau default 0.0 â€” tidak ada
    # pemanggilan ulang compute_conviction_score.
    selected_tickers = {t["ticker"] for t in top_n}
    sorted_results = sorted(
        all_results, key=lambda x: x.get("conviction_score", 0.0), reverse=True
    )

    for entry in sorted_results:
        v = entry.get("verdict", {})
        ticker = entry["ticker"]
        rating = v.get("rating", "ERROR") if v else "ERROR"
        conf = v.get("confidence", 0) if v else 0
        rr = v.get("risk_reward_ratio", "N/A") if v else "N/A"
        cscore = entry.get("conviction_score", 0.0)
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
            f"| {actionability} | {consensus} | {method} | {dissent} | {disagreement} | {status} |"
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
# Main entrypoint
# ---------------------------------------------------------------------------


async def main(
    *,
    dry_run: bool = False,
    output_dir: Path = OUTPUT_DIR,
    user_config: dict | None = None,
    mode: str | None = None,
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
        "✓" if prompt_pack_ok else "!",
        "OK" if prompt_pack_ok else "linter unavailable",
    )
    ticker_override = list(CLI_TICKERS_OVERRIDE or [])

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
        require_gemini=not dry_run,
    )
    _cli._print_dependency_report(deps)
    if not deps.is_valid:
        logger.error("[Dependencies] Blocking issue ditemukan. Pipeline dihentikan.")
        return

    # Step 0a: Dependency Validation
    _cli_renderer.phase("Candidate Validation")
    if ticker_override:
        logger.info(
            "[CLI] Menggunakan --tickers override; "
            "skip quant filter dan top10_candidates.json."
        )
    else:
        validation = check_candidates_file(JSON_PATH, settings.CANDIDATES_MAX_AGE_HOURS)
        if not validation.is_valid:
            logger.warning(f"[Validator] {validation.message}")
            if settings.CANDIDATES_AUTO_RERUN:
                if not maybe_rerun_quant_filter():
                    logger.error("[Validator] Auto-rerun gagal. Pipeline dihentikan.")
                    return
            else:
                logger.error(
                    "[Validator] Set CANDIDATES_AUTO_RERUN=true untuk auto-rerun, "
                    "atau jalankan run_quant_filter.py secara manual."
                )
                return
        else:
            logger.info(f"[Validator] {validation.message}")

    # Step 0b: Market Regime Detection
    _cli_renderer.phase("Market Regime")
    vol = await fetch_ihsg_volatility(settings.REGIME_VOLATILITY_LOOKBACK_DAYS)
    regime: RegimeType = classify_regime(
        vol,
        high_threshold=settings.REGIME_VOLATILITY_HIGH_THRESHOLD,
        low_threshold=settings.REGIME_VOLATILITY_LOW_THRESHOLD,
    )
    regime_params = get_regime_params(regime)
    if regime_params:
        logger.info(f"[Regime] {regime} -- applying overrides: {regime_params}")
        for key in ("top_n_selection", "rpm_limit", "rr_normalization_cap"):
            if key in regime_params:
                ORCHESTRATOR_CONFIG[key] = regime_params[key]
        if "min_conviction_override" in regime_params:
            ORCHESTRATOR_CONFIG["min_conviction_override"] = regime_params[
                "min_conviction_override"
            ]
    else:
        logger.info(f"[Regime] {regime} -- no overrides applied.")
    _cli_renderer.render_market_regime(
        volatility=vol,
        regime=regime,
        regime_params=regime_params,
    )

    # Step 1: Parse
    _cli_renderer.phase("Candidate Intake")
    try:
        if ticker_override:
            tickers = ticker_override
            sector_map = {ticker: "unknown" for ticker in tickers}
            logger.info(f"[CLI] {len(tickers)} ticker dari --tickers: {tickers}")
        else:
            candidates = _load_quant_candidates(JSON_PATH)
            candidates = _apply_candidate_intake(candidates)
            candidates = _apply_pre_cio_filters(candidates, regime)
            tickers = parse_report(candidates=candidates)
            sector_map = parse_sector_map(candidates=candidates)
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"[Orchestrator] {e}")
        return
    _cli_renderer.set_pipeline_status(
        "Candidates",
        "✓",
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

    if not dry_run:
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
                return
            logger.warning(
                "[ProviderHealth] Planner allowed degraded mode despite provider "
                "health failure; continuing."
            )
    else:
        _cli_renderer.set_pipeline_status("Stockbit", "–", "skipped (dry-run)")
        _cli_renderer.set_pipeline_status("yfinance", "–", "skipped (dry-run)")

    # Step 2: Batch Debates
    _cli_renderer.render_pipeline_status()
    _cli_renderer.phase(
        "Per-Ticker Progress", "fetching data -> running analysis -> debating"
    )
    # abort_event dibuat di sini agar signal handler bisa mengaksesnya sebelum gather.
    abort_event = asyncio.Event()
    _setup_abort_signal(asyncio.get_running_loop(), abort_event)
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
                )
            _enhance_completed_results(results, ledger_run_id, fetch_news=not dry_run)
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
        _cli_renderer.flush_buffered_alerts()

    # Step 3: Score + Rank + Diversify
    _cli_renderer.phase("Scoring and Sizing")
    debate_records = load_debate_history(OUTPUT_DIR)
    top_n = select_top_n(results, debate_records=debate_records)
    _annotate_risk_governor(top_n)
    sizing_candidates = _build_sizing_candidates(top_n)
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
    save_full_results(results, FULL_RESULTS_PATH)
    _ledger_artifact_write(
        run_id=ledger_run_id,
        artifact="full_batch_results.json",
        path=FULL_RESULTS_PATH,
        ticker_count=len(results),
    )
    save_individual_debates_versioned(
        results, timestamp=batch_timestamp, output_dir=OUTPUT_DIR
    )
    generate_top3_report(top_n, results, TOP3_REPORT_PATH, sizing_result=sizing_result)
    _log_artifact_validation(results)
    _write_batch_telemetry_report(
        output_dir=OUTPUT_DIR,
        run_id=ledger_run_id,
        batch_timestamp=batch_timestamp,
    )
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
        TOP3_REPORT_PATH,
        OUTPUT_DIR / "debates",
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
    logger.info(f"[Orchestrator] Top {len(top_n)} report -> {TOP3_REPORT_PATH}")

    _cli_renderer.phase("Final Results")
    _cli_renderer.render_debate_summaries(results)
    _cli_renderer.render_final_results_table(results, top_n)

    # Tampilkan error summary dan top picks langsung di terminal (bukan raw markdown).
    _print_error_summary(results)
    if run_mode != "compare":
        _print_top3_summary(top_n)

    _cli_renderer.phase("Summary Footer")
    _cli_renderer.render_summary_footer(
        started_at=started_at,
        regime=str(regime),
        sizing_result=sizing_result,
        output_files=persistence_outputs,
    )


# ---------------------------------------------------------------------------
# CLI helper functions â€” output terminal yang informatif
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


def _print_top3_summary(top_n: list[dict]) -> None:
    """
    Tampilkan ringkasan Top N hasil debate sebagai panel statis.
    """
    if not top_n:
        console.print()
        console.print(
            Panel(
                "[warn]Tidak ada saham yang memenuhi syarat (semua HOLD/AVOID/SELL).[/warn]",
                title="[ok]Top Swing Trade Picks[/ok]",
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
    table.add_column("Conviction %", justify="right")
    table.add_column("R/R", justify="right")
    table.add_column("Entry Range")
    table.add_column("Target")
    table.add_column("SL")
    table.add_column("Action")

    for i, entry in enumerate(top_n, 1):
        v = entry.get("verdict", {})
        ticker = entry["ticker"]
        rating = v.get("rating", "N/A")
        score = entry.get("conviction_score", 0.0)
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
            title="[ok]Top Swing Trade Picks[/ok]",
            subtitle=f"[muted]{TOP3_REPORT_PATH}[/muted]",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# Interactive CLI â€” Rich-powered terminal UI
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

    # â”€â”€ Teks & konstanta tampilan â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _BRAND_TITLE = "IDX Fundamental Analysis"
    _BRAND_SUB = "Quant Scouting  ->  Multi-Agent Debate  ->  CIO Verdict"
    _VALID_INPUTS = {"y", "n"}

    def __init__(self, con: Console = console) -> None:
        self.con = con

    # â”€â”€ Private helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _render_banner(self) -> None:
        """Tampilkan banner dan status sistem ringkas."""
        # â”€â”€ Judul produk â”€â”€
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

        # â”€â”€ Status candidates file (sinkron, tersedia tanpa async) â”€â”€
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
        Tampilkan prompt terminal dan validasi input pengguna.

        Loop berlanjut sampai pengguna memasukkan 'y' atau 'n'.
        Input yang tidak valid ditampilkan sebagai pesan peringatan, bukan exception.
        """
        self.con.print()
        self.con.print(Rule("[step]Persiapan Pipeline[/step]"))
        self.con.print(
            "  [muted]Pipeline memerlukan data hasil scraping yang sudah tersedia di database.[/muted]"
        )
        self.con.print()

        while True:
            # Rich Prompt menampilkan tanda tanya berwarna secara otomatis.
            jawaban = (
                Prompt.ask(
                    "  [prompt]Apakah data scraping sudah tersedia?[/prompt]",
                    choices=["y", "n"],
                    show_choices=True,
                    console=self.con,
                )
                .strip()
                .lower()
            )

            if jawaban in self._VALID_INPUTS:
                return jawaban

            # Guard: tetap dipertahankan untuk kejelasan jika input tidak sesuai.
            self.con.print("  [warn]Input tidak valid. Masukkan 'y' atau 'n'.[/warn]")

    def _run_scraping(self, scrape_cmd: list[str] | None = None) -> bool:
        """
        Jalankan `main.py -f -o excel` di dalam Live spinner.

        Live spinner memberikan feedback visual bahwa sistem sedang bekerja.
        subprocess.run() bersifat blocking â€” pipeline orchestrator tidak akan
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
            _cli_renderer.render_status(f"[Scraping] {_clean_cli_text(line)}")
        for line in (result.stderr or "").splitlines():
            cleaned = _clean_cli_text(line)
            if "WARNING" in cleaned.upper() or "WARN" in cleaned.upper():
                _cli_renderer.render_warning(f"[Scraping] {cleaned}")
            else:
                _cli_renderer.render_status(f"[Scraping] {cleaned}", style="muted")

        if result.returncode == 0:
            self.con.print("  [ok]OK  Scraping selesai.[/ok]")
            return True
        else:
            self.con.print(
                f"  [danger]FAIL  Scraping selesai dengan exit code {result.returncode}. "
                "Pipeline tetap dilanjutkan - periksa log di atas untuk detail.[/danger]"
            )
            return False

    def _print_pipeline_start(self) -> None:
        """Tampilkan garis pemisah sebelum pipeline utama dimulai."""
        self.con.print()
        self.con.print(Rule("[step]Memulai Pipeline Orkestrasi[/step]"))
        self.con.print()

    # â”€â”€ Public entrypoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _print_dependency_report(self, deps: DependencyCheckResult) -> None:
        """Tampilkan hasil pemeriksaan awal sebelum pipeline berjalan."""
        table = Table(title="Pre-flight Checks", show_lines=False)
        table.add_column("Check", style="bold")
        table.add_column("Status")
        table.add_column("Message")
        table.add_column("Hint", style="muted")

        for check in deps.checks.values():
            status = "[ok]OK[/ok]" if check.is_valid else "[danger]FAIL[/danger]"
            table.add_row(check.name, status, check.message, check.hint or "-")

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
    ) -> None:
        """
        Titik masuk CLI: banner â†’ prompt â†’ (opsional) scraping â†’ pipeline start.

        Method ini dipisah dari asyncio.run(main()) agar CLI layer dan
        pipeline layer tetap independen â€” mudah di-test secara terpisah.
        """
        if not interactive:
            return

        self._render_banner()
        if skip_scraping:
            self.con.print(
                "  [ok]OK  Langkah scraping dilewati melalui `--skip-scraping`.[/ok]"
            )
            self._print_pipeline_start()
            return

        jawaban = self._prompt_scraping()

        if jawaban == "n":
            # Pengguna belum menyiapkan data: jalankan scraping terlebih dahulu.
            self._run_scraping(scrape_cmd=scrape_cmd)
        else:
            # Data sudah tersedia, lanjut ke pipeline utama.
            self.con.print(
                "  [ok]OK  Data scraping sudah tersedia. Pipeline akan dilanjutkan.[/ok]"
            )

        self._print_pipeline_start()


# Singleton CLI yang digunakan di __main__.
# Dibuat di sini agar bisa di-mock saat testing.
_cli = InteractiveCLI()


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse orchestrator CLI options while preserving interactive defaults."""
    global CLI_MODE, CLI_TICKERS_OVERRIDE

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
        help="Show raw Loguru lines alongside structured Rich output.",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        help="Override candidate list with specific tickers e.g. --tickers BBCA ADRO TLKM",
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
    args = parser.parse_args(argv)
    CLI_TICKERS_OVERRIDE = None
    CLI_MODE = args.mode
    if args.tickers:
        try:
            args.tickers = _normalize_cli_tickers(args.tickers)
        except ValueError as exc:
            parser.error(str(exc))
        CLI_TICKERS_OVERRIDE = args.tickers
        args.no_interactive = True
        args.skip_scraping = True
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
    _cli.run(
        interactive=not args.no_interactive,
        skip_scraping=args.skip_scraping,
        scrape_cmd=scrape_cmd,
    )
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
