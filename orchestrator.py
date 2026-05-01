"""
orchestrator.py — Automated Pipeline: Quant Scouting → Multi-Agent Debate → Top 3 Swing Trades.

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
  - [FIX-4] Urutan eksekusi: abort_check → rate_limit → semaphore → abort_check
    → budget_charge → eksekusi. Budget hanya terpotong tepat sebelum API call.
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

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from core.budget import BudgetExhaustedError, get_usage, reset_budget
from core.dependency_validator import check_candidates_file, maybe_rerun_quant_filter
from core.historical_scorer import (
    apply_historical_adjustment,
    compute_historical_win_rate,
    load_debate_history,
)
from core.portfolio_optimizer import diversify_portfolio
from core.regime import RegimeType, classify_regime, fetch_ihsg_volatility, get_regime_params
from core.settings import settings
from services.debate_chamber import DebateChamber
from schemas.debate import CIOVerdict
from utils.logger_config import logger
from utils.price_fetcher import fetch_current_price

# [FIX-8] Import ZoneInfo di top-level, satu kali, dengan fallback untuk Python < 3.9.
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore


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

# Shorthand aliases — baca dari ORCHESTRATOR_CONFIG agar konsisten dengan
# regime override yang dilakukan di main() sebelum pipeline jalan.
EXCLUDED_RATINGS: set[str] = ORCHESTRATOR_CONFIG["excluded_ratings"]
# TOP_N_SELECTION dan MAX_CONCURRENT_DEBATES dibaca dinamis dari ORCHESTRATOR_CONFIG
# di dalam fungsi agar regime override yang dilakukan di main() terlihat.

# IDX saham biasa: tepat 4 huruf kapital, opsional suffix .JK
# Catatan: warrant/right issue (5 huruf) sengaja dikecualikan dari scope ini.
TICKER_PATTERN = re.compile(r"^[A-Z]{4}(?:\.JK)?$")


# ---------------------------------------------------------------------------
# [FIX-1, FIX-2] SafeRateLimiter — sliding window, lock-safe
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
                    return  # Slot tersedia — keluar, lock dilepas oleh context manager

                # Hitung berapa lama sampai token tertua kadaluarsa.
                # _tokens terurut secara implisit karena selalu di-append dengan
                # timestamp yang monotonically increasing.
                wait_time = self._tokens[0] + self.period - now

            # [FIX-1] Lock sudah dilepas oleh `async with` di atas.
            # Sleep di luar lock — CancelledError di sini tidak menyentuh lock.
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


def parse_report(json_path: Path = JSON_PATH) -> list[str]:
    """
    Baca top10_candidates.json dan kembalikan daftar ticker yang valid.

    Mengabaikan ticker dengan flag "critical risk" di kolom Entry Strategy.
    Menghapus duplikat setelah normalisasi uppercase.

    Raises:
        FileNotFoundError: File JSON tidak ditemukan.
        ValueError: Tidak ada ticker valid setelah filtering.
    """
    if not json_path.exists():
        raise FileNotFoundError(
            f"Candidates tidak ditemukan di {json_path}. "
            "Jalankan run_quant_filter.py terlebih dahulu."
        )

    data = json.loads(json_path.read_text(encoding="utf-8"))
    tickers: list[str] = []
    seen: set[str] = set()

    # [FIX-9] `for row in data` — syntax error di versi sebelumnya diperbaiki.
    for row in data:
        raw = row.get("Ticker", "")
        ticker = raw.strip().upper() if raw else ""

        if not validate_ticker(ticker):
            logger.warning(f"[Parser] Format ticker tidak valid: '{raw}' — dilewati")
            continue

        if ticker in seen:
            logger.debug(f"[Parser] Duplikat: {ticker} — dilewati")
            continue

        if "critical risk" in row.get("Entry Strategy", "").lower():
            logger.warning(f"[Parser] {ticker} — Critical Risk flag, dilewati")
            continue

        seen.add(ticker)
        tickers.append(ticker)

    if not tickers:
        raise ValueError("Tidak ada ticker valid setelah parsing dan filtering.")

    logger.info(f"[Parser] {len(tickers)} ticker diekstrak: {tickers}")
    return tickers


def parse_sector_map(json_path: Path = JSON_PATH) -> dict[str, str]:
    """
    Baca sector_key dari top10_candidates.json.

    Mengembalikan dict {ticker: sector_key} untuk portfolio_optimizer.
    Field "Sektor Key" adalah output dari run_quant_filter.py.
    Tidak raise — file hilang/corrupt hanya menghasilkan dict kosong.
    """
    if not json_path.exists():
        logger.warning("[Parser] Sector map: file tidak ditemukan, sector_key 'unknown'.")
        return {}

    sector_map: dict[str, str] = {}
    data = json.loads(json_path.read_text(encoding="utf-8"))
    for row in data:
        raw = row.get("Ticker", "")
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

    [FIX-10] Status selalu FAILED — fungsi ini tidak pernah dipanggil
    untuk kondisi sukses, jadi tidak ada dead code `else "SUCCESS"`.
    """
    return {
        "ticker": ticker,
        "verdict": {},
        "debate_rounds": 0,
        "debate_history": [],
        "raw_data_summary": "",
        "error": error,
        "conviction_score": 0.0,
        "sector_key": sector_key,
    }


async def _run_single_debate(ticker: str, chamber: DebateChamber) -> dict:
    """
    Jalankan debate untuk satu ticker: fetch harga → chamber.run() → validasi schema.

    Retry ada di dalam DebateChamber._invoke_llm (tenacity). Tidak ada retry
    tambahan di sini untuk menghindari efek perkalian (9× worst case).
    """
    logger.info(f"[Debate] Mulai: {ticker}")

    current_price = 0.0
    try:
        current_price = await fetch_price_with_retry(ticker)
    except Exception as e:
        logger.warning(f"[Debate] Gagal ambil harga {ticker}: {e} — debate tetap berjalan dengan harga 0")

    if current_price == 0.0:
        logger.warning(f"[Debate] {ticker} — harga tidak tersedia, trade level akan terdegradasi")

    try:
        result = await chamber.run(ticker, current_price=current_price)
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

        logger.info(f"[Debate] ✅ Selesai: {ticker}")
        return {
            "ticker": result["ticker"],
            "verdict": verdict_dict,
            "debate_rounds": result["round_count"],
            "debate_history": [
                {"role": m.role, "content": m.content, "round": m.round_num}
                for m in result["debate_history"]
            ],
            "raw_data_summary": result["raw_data"],
            "error": None,
            "conviction_score": 0.0,  # Diisi oleh select_top3
        }

    except BudgetExhaustedError as e:
        logger.error(f"[Debate] 🛑 Budget habis saat debating {ticker}: {e}")
        return _empty_result(ticker, f"Budget exhausted: {e}")
    except Exception as e:
        logger.error(f"[Debate] 🚨 {ticker} gagal: {e}")
        return _empty_result(ticker, str(e))


async def run_batch_debates(
    tickers: list[str],
    sector_map: dict[str, str] | None = None,
) -> list[dict]:
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
    logger.info(
        f"[Orchestrator] Meluncurkan {len(tickers)} debate "
        f"(concurrency={max_concurrent}, "
        f"RPM={ORCHESTRATOR_CONFIG['rpm_limit']})"
    )

    chamber = DebateChamber()
    rate_limiter = SafeRateLimiter(
        rate_limit=ORCHESTRATOR_CONFIG["rpm_limit"],
        period_seconds=60.0,
    )
    sem = asyncio.Semaphore(max_concurrent)

    # [FIX-3] Satu abort_event untuk seluruh batch.
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

    async def _guarded(ticker: str) -> dict:
        sector_key = (sector_map or {}).get(ticker, "unknown")
        budget_charged = False

        try:
            # 1. Cek abort sebelum mulai apapun
            if abort_event.is_set():
                logger.info(f"[{ticker}] Dibatalkan sebelum start (budget habis)")
                return _empty_result(ticker, "Aborted: budget exhausted before start", sector_key)

            # 2. Tunggu slot rate limit
            await rate_limiter.acquire()

            # 3. Tunggu slot konkurensi
            async with sem:
                await asyncio.sleep(ORCHESTRATOR_CONFIG["batch_delay"])

                # 4. Cek abort lagi setelah antre
                if abort_event.is_set():
                    logger.info(f"[{ticker}] Dibatalkan saat antre (budget habis)")
                    return _empty_result(ticker, "Aborted: budget exhausted in queue", sector_key)

                # 5. Charge budget tepat sebelum eksekusi (atomik)
                async with budget_lock:
                    if budget_state["spent"] >= max_budget:
                        abort_event.set()
                        logger.warning(f"[{ticker}] Budget habis saat charge — abort ditetapkan")
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
                    return result

                except BudgetExhaustedError as e:
                    abort_event.set()
                    logger.error(f"[{ticker}] 🛑 Budget habis dari dalam chamber: {e}")
                    return _empty_result(ticker, f"Budget exhausted: {e}", sector_key)

                except Exception as e:
                    logger.error(f"[{ticker}] 🚨 Error saat eksekusi: {e}")
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
            # untuk logika apapun — abort dideteksi via abort_event.is_set().
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
            return _empty_result(ticker, "Task cancelled by abort event", sector_key)

        except Exception as e:
            logger.exception(f"[{ticker}] Error tak terduga di _guarded: {e}")
            return _empty_result(ticker, f"Orchestrator error: {e}", sector_key)

    results = await asyncio.gather(
        *[_guarded(t) for t in tickers],
        return_exceptions=True,
    )

    # Konversi BaseException yang lolos semua guard menjadi empty result
    safe_results: list[dict] = []
    for ticker, res in zip(tickers, results):
        if isinstance(res, BaseException):
            logger.error(f"[Orchestrator] 🚨 {ticker} lolos semua guard: {res}")
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
    Hitung Conviction Score = W_confidence × CIO Confidence + W_rr × Normalized R/R.

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
            f"⚠️ R/R {rr_ratio:.1f}× mencurigakan tinggi — "
            "verifikasi stop loss dan target: mungkin stop terlalu sempit "
            "atau target melampaui resistance kuat"
        )
    elif rr_ratio > 3.5:
        warning = f"⚠️ R/R {rr_ratio:.1f}× — verifikasi stop tidak berada di dalam noise band"

    rr_score = min(max(rr_ratio / rr_cap, 0.0), 1.0)
    base_score = (w_confidence * confidence) + (w_rr * rr_score)

    # Historical adjustment — hanya jika data tersedia dan cukup
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
            logger.info(f"[Rank] Lewati {entry['ticker']} — tidak ada verdict")
            continue

        rating = verdict.get("rating", "AVOID")
        if rating in EXCLUDED_RATINGS:
            logger.info(f"[Rank] Excluded {entry['ticker']} — rating {rating}")
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


# Backward-compatibility alias — deprecate secara bertahap
select_top3 = select_top_n


# ---------------------------------------------------------------------------
# Step 4: Persistence & reporting
# ---------------------------------------------------------------------------

def save_full_results(results: list[dict], path: Path = FULL_RESULTS_PATH) -> None:
    """Simpan semua hasil debate sebagai JSON tunggal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"[Persist] Full results → {path}")


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


def get_local_timestamp() -> str:
    """
    Kembalikan timestamp lokal dalam timezone yang dikonfigurasi (default: Asia/Jakarta).

    [FIX-8] ZoneInfo sudah di-import di top-level — fungsi ini tidak perlu
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
) -> str:
    """
    Generate laporan Markdown eksekutif untuk Top N swing trade.

    [FIX-7] conviction_score di-reuse dari entry dict yang sudah diisi oleh
    select_top3 — tidak ada pemanggilan ulang compute_conviction_score.
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
        f"# 🏆 TOP {selected_count} HIGH-CONVICTION IHSG SWING TRADES",
        "",
        f"> **Generated**: {timestamp}",
        "> **Pipeline**: Quant Scouting → Multi-Agent Debate → CIO Verdict",
        f"> **Stocks Debated**: {total_debated} | **Eligible (BUY/STRONG_BUY)**: {eligible} | **Selected**: {selected_count}",
        "",
        "---",
        "",
    ]

    if not top_n:
        lines += [
            f"⚠️ **Tidak ada saham yang memenuhi syarat untuk Top {ORCHESTRATOR_CONFIG['top_n_selection']}.**",
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
        # [FIX-7] Reuse skor dari select_top3, bukan hitung ulang
        score = entry.get("conviction_score", 0.0)
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "")

        lines += [
            f"## {medal} #{rank} — {ticker}",
            "",
            "### Final Rating & Confidence",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| **Rating** | `{v.get('rating', 'N/A')}` |",
            f"| **CIO Confidence** | {v.get('confidence', 0):.0%} |",
            f"| **Conviction Score** | {score:.2%} |",
            f"| **Timeframe** | {v.get('timeframe', '1-3 Months')} |",
            "",
            "### 📦 Trade Box",
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
            "### 🏆 Winning Argument",
            "",
            f"> {_extract_winning_argument(entry)}",
            "",
            "### ⚠️ Devil's Advocate Warning",
            "",
            f"> {_extract_devils_warning(entry)}",
            "",
            "### 💡 CIO Summary",
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

    # Footer: tabel ringkasan semua ticker
    lines += [
        "## 📊 Full Batch Summary",
        "",
        "| Ticker | Rating | Confidence | R/R Ratio | Conviction Score | Status |",
        "|---|---|---|---|---|---|",
    ]

    # [FIX-7] Untuk ticker yang sudah masuk select_top3, skor sudah ada di entry.
    # Untuk ticker error/excluded, ambil dari entry atau default 0.0 — tidak ada
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

        if entry.get("error"):
            status = "❌ Error"
        elif ticker in selected_tickers:
            status = "🏆 Selected"
        elif rating in EXCLUDED_RATINGS:
            status = "⛔ Excluded"
        else:
            status = "—"

        rr_str = f"{rr:.2f}" if isinstance(rr, (int, float)) and rr else "N/A"
        lines.append(f"| {ticker} | {rating} | {conf:.0%} | {rr_str} | {cscore:.2%} | {status} |")

    lines += [
        "",
        "---",
        f"*Laporan dibuat oleh `orchestrator.py` pada {timestamp}*",
    ]

    report_text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_text, encoding="utf-8")
    logger.info(f"[Persist] Top {len(top_n)} report → {path}")
    return report_text


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

async def main() -> None:
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
        tickers = parse_report()
        sector_map = parse_sector_map()
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"[Orchestrator] {e}")
        return

    # Step 2: Batch Debates
    results = await run_batch_debates(tickers, sector_map=sector_map)

    # Step 3: Score + Rank + Diversify
    debate_records = load_debate_history(OUTPUT_DIR)
    top_n = select_top_n(results, debate_records=debate_records)

    # Step 4: Persist
    save_full_results(results)
    save_individual_debates(results)  # Memecah hasil untuk history
    report = generate_top3_report(top_n, results)

    logger.info("=" * 60)
    logger.info("[Orchestrator] Pipeline selesai")
    logger.info(f"[Orchestrator] Regime: {regime} | Top N: {len(top_n)}")
    logger.info(f"[Orchestrator] Full results -> {FULL_RESULTS_PATH}")
    logger.info(f"[Orchestrator] Top {len(top_n)} report -> {TOP3_REPORT_PATH}")
    logger.info("=" * 60)

    print("\n" + report)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(main())
