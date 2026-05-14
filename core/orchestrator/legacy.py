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
from contextlib import contextmanager
import json
import os
import random
import re
import shlex
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Prompt
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from tenacity import retry, stop_after_attempt, wait_exponential

from core.budget import BudgetExhaustedError, get_usage, reset_budget
from core.artifact_validator import validate_artifacts
from core.candidate_intake import normalize_batch
from core.dependency_validator import (
    DependencyCheckResult,
    check_all_dependencies,
    check_candidates_file,
    maybe_rerun_quant_filter,
)
from core.historical_scorer import (
    apply_historical_adjustment,
    compute_historical_win_rate,
    load_debate_history,
)
from core.quant_filter.position_sizer import calculate_positions
from core.quant_filter.reporting import _build_position_summary
from core.portfolio_optimizer import diversify_portfolio
from core.provider_health import check_all_providers
from core.regime import RegimeType, classify_regime, fetch_ihsg_volatility, get_regime_params
from core.risk_governor import annotate_risk
from core.settings import settings
from services.debate_prompt_registry import PROMPT_VERSION
from utils.logger_config import logger
from utils.price_fetcher import fetch_current_price


# Tema warna konsisten â€” ubah di sini, berlaku di seluruh CLI.
_CLI_THEME = Theme({
    "brand":   "bold cyan",
    "ok":      "bold green",
    "warn":    "bold yellow",
    "danger":  "bold red",
    "muted":   "dim white",
    "prompt":  "bold white",
    "step":    "bold magenta",
    "amber":   "yellow",
})
console = Console(theme=_CLI_THEME, highlight=False)

# Peta rating CIO â†’ warna Rich. Digunakan oleh live table dan result summary.
_RATING_STYLE: dict[str, str] = {
    "STRONG_BUY": "bold green",
    "BUY":        "cyan",
    "HOLD":       "dim",
    "SELL":       "red",
    "AVOID":      "red",
    "ERROR":      "bold red",
    "ABORTED":    "dim red",
    "debating":   "yellow",
    "queued":     "dim",
}


@contextmanager
def _pipeline_file_logging_only():
    """
    Redirect Loguru output to a file while Rich owns the terminal.

    Rich Live/Progress redraws the terminal frequently. Console log sinks can
    corrupt that display, so the debate phase temporarily writes Loguru events
    to pipeline.log only and then restores the standard project sinks.
    """
    logger.remove()
    logger.add("pipeline.log", level="INFO")
    try:
        yield
    finally:
        logger.remove()
        logger.add(
            sys.stderr,
            format=settings.LOG_FORMAT,
            level=settings.LOG_LEVEL,
            colorize=True,
        )
        logger.add(
            settings.LOG_APP_FILENAME,
            format=("{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} {file}:{line} | {message}"),
            level=settings.LOG_LEVEL,
            rotation="1 MB",
            retention="10 days",
            compression="zip",
        )


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


def configure_output_dir(output_dir: Path) -> None:
    """Update module-level output paths from CLI/env configuration."""
    global OUTPUT_DIR, JSON_PATH, FULL_RESULTS_PATH, TOP3_REPORT_PATH
    OUTPUT_DIR = output_dir
    JSON_PATH = OUTPUT_DIR / "top10_candidates.json"
    FULL_RESULTS_PATH = OUTPUT_DIR / "full_batch_results.json"
    TOP3_REPORT_PATH = OUTPUT_DIR / "TOP_3_SWING_TRADES.md"


def _prompt_user_config() -> dict:
    """Tanya input modal, max loss, max posisi ke user via terminal."""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("\n" + "â•" * 50)
    print("  IHSG Swing Trade â€” Position Sizing Setup")
    print("â•" * 50)

    while True:
        try:
            capital = float(input("\nModal total (Rp): ").replace(",", "").replace(".", ""))
            if capital > 0:
                break
            print("  âš ï¸  Modal harus lebih dari 0.")
        except ValueError:
            print("  âš ï¸  Masukkan angka tanpa huruf.")

    while True:
        try:
            raw = input("Max loss per trade (%, default 2): ").strip()
            max_loss = float(raw) / 100 if raw else 0.02
            if 0 < max_loss <= 0.10:
                break
            print("  âš ï¸  Max loss harus antara 0.1% - 10%.")
        except ValueError:
            print("  âš ï¸  Masukkan angka, contoh: 2")

    while True:
        try:
            raw = input("Max jumlah posisi (default 5): ").strip()
            max_pos = int(raw) if raw else 5
            if 1 <= max_pos <= 20:
                break
            print("  âš ï¸  Max posisi harus antara 1 - 20.")
        except ValueError:
            print("  âš ï¸  Masukkan angka bulat, contoh: 5")

    print(f"\n  âœ… Modal: Rp {capital:,.0f} | Max loss: {max_loss*100:.1f}% | Max posisi: {max_pos}")
    print("â•" * 50 + "\n")

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
        raise ValueError(f"Format candidates tidak valid di {json_path}: expected list.")
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
    normalized, rejected = normalize_batch([_candidate_for_intake(c) for c in candidates])
    for item in rejected:
        rejected_candidate = item.get("candidate", {})
        ticker = rejected_candidate.get("ticker") or rejected_candidate.get("Ticker") or "UNKNOWN"
        logger.warning(f"[CandidateIntake] Rejected {ticker}: {item.get('error')}")

    if not normalized:
        logger.warning(
            "[CandidateIntake] No candidates normalized; continuing with raw candidates "
            "for backward compatibility."
        )
        return candidates

    valid_tickers = {candidate.ticker for candidate in normalized}
    filtered = [candidate for candidate in candidates if _candidate_ticker(candidate) in valid_tickers]
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
    return str(candidate.get("ma200_context") or candidate.get("MA200 Context") or "").upper()


def _apply_pre_cio_filters(candidates: list[dict], regime: str) -> list[dict]:
    """
    Hard filter sebelum masuk CIO â€” buang kandidat yang tidak layak
    tanpa membuang LLM token untuk mereka.
    """
    filtered = []
    for c in candidates:
        ticker = _candidate_ticker(c) or str(c.get("ticker") or c.get("Ticker") or "UNKNOWN")

        # ExDate hard disqualifier (redundant safety net di Python level)
        exdate_days = _candidate_exdate_days(c)
        if exdate_days is not None and exdate_days <= 7:
            logger.info(f"[PreCIO] {ticker} SKIP â€” ExDate {exdate_days}d")
            continue

        # Counter-trend di HIGH regime â†’ skip langsung
        # Di NORMAL/LOW regime â†’ biarkan masuk tapi CIO beri penalty
        if regime == "HIGH" and _candidate_ma200_context(c) == "BELOW":
            logger.info(f"[PreCIO] {ticker} SKIP â€” counter-trend di HIGH regime")
            continue

        filtered.append(c)

    return filtered


def parse_report(json_path: Path = JSON_PATH, candidates: list[dict] | None = None) -> list[str]:
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
            logger.warning(f"[Parser] Format ticker tidak valid: '{raw}' â€” dilewati")
            continue

        if ticker in seen:
            logger.debug(f"[Parser] Duplikat: {ticker} â€” dilewati")
            continue

        if "critical risk" in row.get("Entry Strategy", "").lower():
            logger.warning(f"[Parser] {ticker} â€” Critical Risk flag, dilewati")
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
        logger.warning("[Parser] Sector map: file tidak ditemukan, sector_key 'unknown'.")
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
        "conviction_score": 0.0,
        "sector_key": sector_key,
    }


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

        logger.info(f"[Debate] âœ… Selesai: {ticker}")
        disagreement_type = result.get("disagreement_type")
        if disagreement_type:
            logger.info(f"[Debate] {ticker} disagreement_type={disagreement_type}")
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
                for m in result["debate_history"]
            ],
            "raw_data_summary": result["raw_data"],
            "metadata": result.get("metadata", {}),
            "error": None,
            "conviction_score": 0.0,  # Diisi oleh select_top3
        }

    except BudgetExhaustedError as e:
        logger.error(f"[Debate] ðŸ›‘ Budget habis saat debating {ticker}: {e}")
        return _empty_result(ticker, f"Budget exhausted: {e}")
    except Exception as e:
        logger.error(f"[Debate] ðŸš¨ {ticker} gagal: {e}")
        return _empty_result(ticker, str(e))


async def run_batch_debates(
    tickers: list[str],
    sector_map: dict[str, str] | None = None,
    abort_event: asyncio.Event | None = None,
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

    ticker_rows: dict[str, dict[str, str]] = {
        ticker: {"status": "QUEUED", "conviction": "", "rr": ""}
        for ticker in tickers
    }
    total_tickers = len(tickers)
    progress_state = {"completed": 0}
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        auto_refresh=False,
    )
    progress_task = progress.add_task(f"Debating 0/{total_tickers} tickers", total=total_tickers)

    def _status_badge(status: str) -> str:
        badges = {
            "QUEUED": "[yellow]QUEUED[/yellow]",
            "DEBATING": "[cyan]DEBATING...[/cyan]",
            "STRONG_BUY": "[green]STRONG BUY[/green]",
            "BUY": "[green]BUY[/green]",
            "HOLD": "[blue]HOLD[/blue]",
            "AVOID": "[red]AVOID[/red]",
            "SELL": "[red]SELL[/red]",
            "ERROR": "[red]ERROR[/red]",
            "ABORTED": "[red]ABORTED[/red]",
        }
        return badges.get(status, f"[white]{status}[/white]")

    def _format_confidence(value: Any) -> str:
        if value in (None, ""):
            return ""
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return ""
        if confidence <= 1.0:
            confidence *= 100.0
        return f"{confidence:.0f}%"

    def _format_rr(value: Any) -> str:
        if value in (None, ""):
            return ""
        try:
            return f"{float(value):.2f}x"
        except (TypeError, ValueError):
            return str(value)

    def _build_status_table() -> Table:
        table = Table(
            box=box.SIMPLE,
            expand=False,
            show_edge=False,
            pad_edge=False,
        )
        table.add_column("Ticker", style="bold")
        table.add_column("Status")
        table.add_column("Conviction", justify="right")
        table.add_column("R/R", justify="right")
        for ticker, row in ticker_rows.items():
            table.add_row(
                ticker,
                _status_badge(row["status"]),
                row["conviction"],
                row["rr"],
            )
        return table

    def _render_live_display() -> Group:
        return Group(progress, _build_status_table())

    def _refresh_live(live: Live) -> None:
        live.update(_render_live_display(), refresh=True)

    def _set_status(ticker: str, status: str, result: dict | None = None) -> None:
        ticker_rows[ticker]["status"] = status
        if result is None:
            return
        verdict = result.get("verdict", {}) or {}
        ticker_rows[ticker]["conviction"] = _format_confidence(verdict.get("confidence"))
        ticker_rows[ticker]["rr"] = _format_rr(verdict.get("risk_reward_ratio"))

    def _advance_progress(live: Live) -> None:
        if progress_state["completed"] < total_tickers:
            progress_state["completed"] += 1
            completed = progress_state["completed"]
            progress.update(
                progress_task,
                advance=1,
                description=f"Debating {completed}/{total_tickers} tickers",
            )
        _refresh_live(live)

    async def _guarded(ticker: str, _live: Live) -> dict:
        sector_key = (sector_map or {}).get(ticker, "unknown")
        budget_charged = False
        progress_recorded = False

        try:
            # 1. Cek abort sebelum mulai apapun
            if abort_event.is_set():
                logger.info(f"[{ticker}] Dibatalkan sebelum start (budget habis)")
                _set_status(ticker, "ABORTED")
                return _empty_result(ticker, "Aborted: budget exhausted before start", sector_key)

            # 2. Tunggu slot rate limit
            await rate_limiter.acquire()

            # 3. Tunggu slot konkurensi
            async with sem:
                await asyncio.sleep(ORCHESTRATOR_CONFIG["batch_delay"])

                # 4. Cek abort lagi setelah antre
                if abort_event.is_set():
                    logger.info(f"[{ticker}] Dibatalkan saat antre (budget habis)")
                    _set_status(ticker, "ABORTED")
                    return _empty_result(ticker, "Aborted: budget exhausted in queue", sector_key)

                # Update status: sedang berdebat.
                _set_status(ticker, "DEBATING")
                _refresh_live(_live)

                # 5. Charge budget tepat sebelum eksekusi (atomik)
                async with budget_lock:
                    if budget_state["spent"] >= max_budget:
                        abort_event.set()
                        logger.warning(f"[{ticker}] Budget habis saat charge -- abort ditetapkan")
                        _set_status(ticker, "ABORTED")
                        return _empty_result(ticker, "Budget exhausted at charge point", sector_key)

                    budget_state["spent"] += 1
                    budget_charged = True  # Set di dalam lock, tepat setelah increment
                    current = budget_state["spent"]

                logger.info(f"[{ticker}] Budget terpakai: {current}/{max_budget}")

                # 6. Eksekusi
                try:
                    result = await _run_single_debate(ticker, chamber)

                    # Propagasi BudgetExhaustedError dari dalam chamber
                    if result.get("error") and result["error"].startswith("Budget exhausted"):
                        abort_event.set()

                    result["sector_key"] = sector_key

                    # Update status: rating final atau ERROR.
                    final_rating = result.get("verdict", {}).get("rating") or (
                        "ERROR" if result.get("error") else "HOLD"
                    )
                    _set_status(ticker, final_rating, result)
                    return result

                except BudgetExhaustedError as e:
                    abort_event.set()
                    logger.error(f"[{ticker}] Budget habis dari dalam chamber: {e}")
                    _set_status(ticker, "ERROR")
                    return _empty_result(ticker, f"Budget exhausted: {e}", sector_key)

                except Exception as e:
                    logger.error(f"[{ticker}] Error saat eksekusi: {e}")
                    _set_status(ticker, "ERROR")
                    return _empty_result(ticker, str(e), sector_key)

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
            _set_status(ticker, "ABORTED")
            return _empty_result(ticker, "Task cancelled by abort event", sector_key)

        except Exception as e:
            logger.exception(f"[{ticker}] Error tak terduga di _guarded: {e}")
            _set_status(ticker, "ERROR")
            return _empty_result(ticker, f"Orchestrator error: {e}", sector_key)

        finally:
            if not progress_recorded:
                _advance_progress(_live)
                progress_recorded = True

    # Rich owns the terminal during the debate phase; Loguru writes to file only.
    with _pipeline_file_logging_only():
        logger.info(
            f"[Orchestrator] Meluncurkan {len(tickers)} debate "
            f"(concurrency={max_concurrent}, "
            f"RPM={ORCHESTRATOR_CONFIG['rpm_limit']})"
        )
        with Live(
            _render_live_display(),
            console=console,
            refresh_per_second=4,
            transient=True,
        ) as live:
            from services.debate_chamber import DebateChamber

            chamber = DebateChamber()
            results = await asyncio.gather(
                *[_guarded(t, live) for t in tickers],
                return_exceptions=True,
            )

    # Konversi BaseException yang lolos semua guard menjadi empty result
    safe_results: list[dict] = []
    for ticker, res in zip(tickers, results):
        if isinstance(res, BaseException):
            logger.error(f"[Orchestrator] ðŸš¨ {ticker} lolos semua guard: {res}")
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
        warning = f"R/R {rr_ratio:.1f}x - verifikasi stop tidak berada di dalam noise band"

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
            logger.info(f"[Rank] Lewati {entry['ticker']} â€” tidak ada verdict")
            continue

        rating = verdict.get("rating", "AVOID")
        if rating in EXCLUDED_RATINGS:
            logger.info(f"[Rank] Excluded {entry['ticker']} â€” rating {rating}")
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
            json.dumps(entry, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        count += 1

    if count > 0:
        logger.info(f"[Persist] {count} individual debate records disimpan ke {debates_dir}")


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
        logger.error(f"[ArtifactValidator] Output artifact validation failed: {report.errors}")


def _build_sizing_candidates(top_n: list[dict]) -> list[dict]:
    """Flatten selected orchestrator entries into position-sizer input records."""
    candidates: list[dict] = []
    for entry in top_n:
        risk = entry.get("risk_governor")
        if isinstance(risk, dict) and risk.get("sizing_allowed") is False:
            continue
        verdict = entry.get("verdict") or {}
        candidates.append({
            "ticker": entry.get("ticker") or verdict.get("ticker"),
            "current_price": verdict.get("current_price"),
            "stop_loss": verdict.get("stop_loss"),
            "rating": verdict.get("rating"),
            "confidence": verdict.get("confidence"),
            "rr_ratio": verdict.get("risk_reward_ratio"),
            "target_price": verdict.get("target_price"),
            "expected_return": verdict.get("expected_return"),
        })
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
        holds.append({
            "ticker": entry.get("ticker") or risk.get("ticker"),
            "status": risk.get("status"),
            "message": risk.get("message"),
        })
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
    if any(token in sector for token in ("energy", "energi", "oil", "coal", "basic", "material")):
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
    if any(token in sector for token in ("consumer", "konsumen", "health", "healthcare")):
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

        results.append({
            "ticker": ticker,
            "verdict": verdict,
            "debate_rounds": 3,
            "consensus_reached": True,
            "consensus_method": "voting",
            "dissenting_agents": ["bear"] if verdict["rating"] in {"BUY", "STRONG_BUY"} else [],
            "agent_votes": [
                {"agent": "fundamental_scout", "position": "BUY", "confidence": 0.66, "round": 0},
                {"agent": "chartist", "position": "BUY", "confidence": 0.64, "round": 0},
                {"agent": "sentiment_specialist", "position": "HOLD", "confidence": 0.55, "round": 0},
                {"agent": "bull", "position": "BUY", "confidence": 0.70, "round": 1},
                {"agent": "bear", "position": "AVOID", "confidence": 0.58, "round": 1},
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
        })

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
    bull_args = [
        h["content"] for h in entry.get("debate_history", []) if h.get("role") == "bull"
    ]
    if not bull_args:
        return "Tidak ada argumen bull yang tercatat."
    arg = bull_args[-1]
    return arg[:497] + "..." if len(arg) > 500 else arg


def _extract_devils_warning(entry: dict) -> str:
    """Ambil challenge terakhir dari Devil's Advocate."""
    da_args = [
        h["content"]
        for h in entry.get("debate_history", [])
        if h.get("role") == "devils_advocate"
    ]
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
        1 for r in all_results
        if r.get("verdict", {}).get("rating") not in EXCLUDED_RATINGS and r.get("verdict")
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
        risk = entry.get("risk_governor") if isinstance(entry.get("risk_governor"), dict) else {}
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
            (f"| **Target Price** | Rp {v['target_price']:,.0f} |" if v.get("target_price") else "| **Target Price** | N/A |"),
            (f"| **Stop Loss** | Rp {v['stop_loss']:,.0f} |" if v.get("stop_loss") else "| **Stop Loss** | N/A |"),
            (f"| **Fair Value** | Rp {v['fair_value']:,.0f} |" if v.get("fair_value") else "| **Fair Value** | N/A |"),
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
    sorted_results = sorted(all_results, key=lambda x: x.get("conviction_score", 0.0), reverse=True)

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
        risk = entry.get("risk_governor") if isinstance(entry.get("risk_governor"), dict) else {}
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
    logger.info("=" * 60)
    logger.info("[Orchestrator] Memulai IHSG Swing Trade Pipeline")
    logger.info("=" * 60)

    reset_budget()
    if user_config is None:
        user_config = _prompt_user_config()

    deps = check_all_dependencies(
        output_dir,
        require_gemini=not dry_run,
    )
    _cli._print_dependency_report(deps)
    if not deps.is_valid:
        logger.error("[Dependencies] Blocking issue ditemukan. Pipeline dihentikan.")
        return

    # Step 0a: Dependency Validation
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
            ORCHESTRATOR_CONFIG["min_conviction_override"] = regime_params["min_conviction_override"]
    else:
        logger.info(f"[Regime] {regime} -- no overrides applied.")

    # Step 1: Parse
    try:
        candidates = _load_quant_candidates(JSON_PATH)
        candidates = _apply_candidate_intake(candidates)
        candidates = _apply_pre_cio_filters(candidates, regime)
        tickers = parse_report(candidates=candidates)
        sector_map = parse_sector_map(candidates=candidates)
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"[Orchestrator] {e}")
        return

    if not dry_run:
        provider_health = await check_all_providers(tickers)
        logger.info(f"[ProviderHealth] {provider_health.model_dump()}")
        for failure in provider_health.failures:
            logger.warning(f"[ProviderHealth] {failure}")
        if not provider_health.can_proceed:
            logger.error("[ProviderHealth] No price provider available. Pipeline dihentikan.")
            return

    # Step 2: Batch Debates
    # abort_event dibuat di sini agar signal handler bisa mengaksesnya sebelum gather.
    abort_event = asyncio.Event()
    _setup_abort_signal(asyncio.get_running_loop(), abort_event)
    if dry_run:
        logger.info("[DryRun] Melewati run_batch_debates(); memakai mock results.")
        results = _generate_mock_debate_results(tickers, sector_map=sector_map)
    else:
        results = await run_batch_debates(tickers, sector_map=sector_map, abort_event=abort_event)

    # Step 3: Score + Rank + Diversify
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

    # Step 4: Persist
    batch_timestamp = datetime.now(ZoneInfo(settings.DATETIME_TIMEZONE)).strftime("%Y%m%d_%H%M%S")
    save_full_results(results, FULL_RESULTS_PATH)
    save_individual_debates_versioned(results, timestamp=batch_timestamp, output_dir=OUTPUT_DIR)
    generate_top3_report(top_n, results, TOP3_REPORT_PATH, sizing_result=sizing_result)
    _log_artifact_validation(results)

    logger.info("=" * 60)
    logger.info("[Orchestrator] Pipeline selesai")
    logger.info(f"[Orchestrator] Regime: {regime} | Top N: {len(top_n)}")
    logger.info(f"[Orchestrator] Full results -> {FULL_RESULTS_PATH}")
    logger.info(f"[Orchestrator] Top {len(top_n)} report -> {TOP3_REPORT_PATH}")
    logger.info("=" * 60)

    # Tampilkan error summary dan top-3 langsung di terminal (bukan raw markdown).
    _print_error_summary(results)
    _print_top3_summary(top_n)


# ---------------------------------------------------------------------------
# CLI helper functions â€” output terminal yang informatif
# ---------------------------------------------------------------------------

def _setup_abort_signal(loop: asyncio.AbstractEventLoop, abort_event: asyncio.Event) -> None:
    """
    Pasang handler Ctrl+C yang graceful.

    - Ctrl+C pertama: set abort_event agar debate aktif selesai dan partial results disimpan.
    - Ctrl+C kedua: SystemExit(1) untuk force quit.
    - Windows: add_signal_handler tidak tersedia; fallback ke signal.signal().
    """
    count = {"n": 0}

    def _handler() -> None:
        count["n"] += 1
        if count["n"] == 1:
            console.print("\n[warn]Ctrl+C - menghentikan pipeline setelah debate aktif selesai...[/warn]")
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
        risk = entry.get("risk_governor") if isinstance(entry.get("risk_governor"), dict) else {}
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
    _BRAND_TITLE  = "IDX Fundamental Analysis"
    _BRAND_SUB    = "Quant Scouting  ->  Multi-Agent Debate  ->  CIO Verdict"
    _VALID_INPUTS = {"y", "n"}

    def __init__(self, con: Console = console) -> None:
        self.con = con

    # â”€â”€ Private helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _render_banner(self) -> None:
        """Tampilkan banner dan status sistem ringkas."""
        # â”€â”€ Judul produk â”€â”€
        title  = Text(self._BRAND_TITLE, style="brand", justify="center")
        sub    = Text(self._BRAND_SUB,   style="muted",  justify="center")
        self.con.print(Panel(
            Text.assemble(title, "\n", sub),
            border_style="cyan",
            padding=(1, 4),
            expand=False,
        ))

        # â”€â”€ Status candidates file (sinkron, tersedia tanpa async) â”€â”€
        # Regime tidak ditampilkan di sini karena fetch_ihsg_volatility adalah async;
        # regime akan muncul di log main() setelah detection selesai.
        validation = check_candidates_file(JSON_PATH, settings.CANDIDATES_MAX_AGE_HOURS)
        cand_icon  = "[ok]OK[/ok]" if validation.is_valid else "[warn]WARN[/warn]"
        cand_msg   = validation.message

        self.con.print(Panel(
            f"{cand_icon} candidates: [muted]{cand_msg}[/muted]",
            title="[muted]pipeline status[/muted]",
            border_style="dim",
            padding=(0, 2),
            expand=False,
        ))

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
            jawaban = Prompt.ask(
                "  [prompt]Apakah data scraping sudah tersedia?[/prompt]",
                choices=["y", "n"],
                show_choices=True,
                console=self.con,
            ).strip().lower()

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
        spinner = Spinner("dots", text=Text(" Scraping data saham IDX...", style="step"))

        with Live(spinner, console=self.con, refresh_per_second=10):
            result = subprocess.run(
                command,
                # stdout/stderr tidak di-capture agar output asli tetap muncul
                # di terminal â€” user bisa melihat progress scraping secara langsung.
            )

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
            self.con.print("[danger]Pipeline tidak dapat dimulai karena ada blocking issue.[/danger]")

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
            self.con.print("  [ok]OK  Langkah scraping dilewati melalui `--skip-scraping`.[/ok]")
            self._print_pipeline_start()
            return

        jawaban = self._prompt_scraping()

        if jawaban == "n":
            # Pengguna belum menyiapkan data: jalankan scraping terlebih dahulu.
            self._run_scraping(scrape_cmd=scrape_cmd)
        else:
            # Data sudah tersedia, lanjut ke pipeline utama.
            self.con.print("  [ok]OK  Data scraping sudah tersedia. Pipeline akan dilanjutkan.[/ok]")

        self._print_pipeline_start()


# Singleton CLI yang digunakan di __main__.
# Dibuat di sini agar bisa di-mock saat testing.
_cli = InteractiveCLI()


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse orchestrator CLI options while preserving interactive defaults."""
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
    return parser.parse_args(argv)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    args = _parse_cli_args()
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
    asyncio.run(main(dry_run=args.dry_run, output_dir=OUTPUT_DIR))
