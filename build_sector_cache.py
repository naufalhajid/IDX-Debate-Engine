"""
build_sector_cache.py — One-time sector classification builder untuk IDX universe.
==================================================================================

Jalankan SEKALI sebelum run_quant_filter.py:
    uv run python build_sector_cache.py

Cara kerja:
  1. Baca semua ticker dari Excel universe
  2. Fetch info sektor dari yfinance (sector + industry field)
  3. Map sektor yfinance → internal key (12 sektor IDX/IDXIC)
  4. Simpan ke output/sector_cache.json

Output sector_cache.json akan dibaca otomatis oleh run_quant_filter.py.
Re-run script ini hanya jika ada ticker baru atau ingin refresh data sektor.

Estimasi waktu: ~5-15 menit untuk 957 ticker (rate-limited by yfinance).
"""

import json
import os
import time
import logging
from datetime import datetime

import pandas as pd
import yfinance as yf

# ── Config ────────────────────────────────────────────────────────────────────

INPUT_FILE  = "output/IDX Fundamental Analysis 2026-04-24.xlsx"
OUTPUT_FILE = "output/sector_cache.json"
BATCH_SIZE  = 50     # Fetch N ticker sekaligus via yfinance bulk info
SLEEP_SEC   = 2.0    # Jeda antar batch (hindari rate limit)

# ── yfinance sector → internal key ───────────────────────────────────────────
#
# yfinance menggunakan kategori sektor ala Yahoo Finance (bahasa Inggris).
# Mapping ini menerjemahkan ke 12 sektor internal kita.
# Industry-level override dipakai untuk kasus ambigu (misal: bank vs multifinance).

YF_SECTOR_MAP: dict[str, str] = {
    # Energy
    "Energy":                       "energy",
    # Materials
    "Basic Materials":              "basic_materials",
    # Industrials
    "Industrials":                  "industrials",
    # Consumer
    "Consumer Defensive":           "consumer_staples",
    "Consumer Staples":             "consumer_staples",
    "Consumer Cyclical":            "consumer_disc",
    "Consumer Discretionary":       "consumer_disc",
    # Healthcare
    "Healthcare":                   "healthcare",
    # Financial — default ke finance_nonbank, override ke bank via industry
    "Financial Services":           "finance_nonbank",
    "Financials":                   "finance_nonbank",
    # Real Estate
    "Real Estate":                  "property",
    # Technology / Communication
    "Technology":                   "tech",
    "Communication Services":       "tech",
    # Utilities → infrastruktur (listrik, air, gas)
    "Utilities":                    "infrastructure",
    # Industrials kadang dipakai untuk kontraktor infrastruktur
    # → sudah di-handle oleh YF_SECTOR_MAP["Industrials"] = industrials
}

# Industry keywords yang meng-override sektor → bank
BANK_INDUSTRY_KEYWORDS = [
    "bank", "banking", "commercial bank", "regional bank",
    "savings", "sharia bank", "syariah",
]

# Industry keywords → transport
TRANSPORT_INDUSTRY_KEYWORDS = [
    "airline", "shipping", "trucking", "logistics", "courier",
    "transportation", "freight",
]

# Industry keywords → infrastructure
INFRA_INDUSTRY_KEYWORDS = [
    "toll", "airport", "port", "seaport", "electricity",
    "power", "water", "gas distribution", "pipeline",
]


def classify_ticker(sector_yf: str, industry_yf: str) -> str:
    """
    Map yfinance sector + industry string → internal 12-sektor key.
    Industry override lebih spesifik daripada sektor.
    """
    sector_yf   = (sector_yf or "").strip()
    industry_yf = (industry_yf or "").lower().strip()

    # ── Industry-level override (lebih spesifik) ──────────────────────────────
    if any(kw in industry_yf for kw in BANK_INDUSTRY_KEYWORDS):
        return "bank"
    if any(kw in industry_yf for kw in TRANSPORT_INDUSTRY_KEYWORDS):
        return "transport"
    if any(kw in industry_yf for kw in INFRA_INDUSTRY_KEYWORDS):
        return "infrastructure"

    # ── Sector-level fallback ─────────────────────────────────────────────────
    return YF_SECTOR_MAP.get(sector_yf, "default")


def fetch_sector_batch(tickers_jk: list[str]) -> dict[str, dict]:
    """
    Fetch sector & industry untuk sekelompok ticker via yfinance bulk download.
    Mengembalikan dict: { "BBRI": {"sector": "bank", "yf_sector": "...", "yf_industry": "..."} }
    """
    result = {}
    try:
        # yfinance fast_info / info hanya bisa per-ticker, tapi kita bisa
        # pakai Tickers() untuk batch yang lebih efisien
        batch = yf.Tickers(" ".join(tickers_jk))
        for t_jk in tickers_jk:
            ticker_code = t_jk.replace(".JK", "")
            try:
                info = batch.tickers[t_jk].info
                yf_sector   = info.get("sector", "") or ""
                yf_industry = info.get("industry", "") or ""
                internal    = classify_ticker(yf_sector, yf_industry)
                result[ticker_code] = {
                    "sector":       internal,
                    "yf_sector":    yf_sector,
                    "yf_industry":  yf_industry,
                }
            except Exception as e:
                # Ticker individual gagal → default, jangan blokir batch
                result[ticker_code] = {
                    "sector":       "default",
                    "yf_sector":    "",
                    "yf_industry":  f"fetch_error: {e}",
                }
    except Exception as e:
        # Seluruh batch gagal
        for t_jk in tickers_jk:
            result[t_jk.replace(".JK", "")] = {
                "sector":       "default",
                "yf_sector":    "",
                "yf_industry":  f"batch_error: {e}",
            }
    return result


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("sector_cache")
    os.makedirs("output", exist_ok=True)

    logger.info("=" * 55)
    logger.info("IDX Sector Cache Builder")
    logger.info("=" * 55)

    # ── 1. Baca universe ticker dari Excel ────────────────────────────────────
    logger.info(f"Membaca universe dari: {INPUT_FILE}")
    df = pd.read_excel(INPUT_FILE, sheet_name="key-statistics")
    all_tickers = df["Ticker"].dropna().unique().tolist()
    logger.info(f"Total ticker universe: {len(all_tickers)}")

    # ── 2. Load cache yang sudah ada (resume jika terganggu) ─────────────────
    existing_cache: dict[str, dict] = {}
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing_cache = json.load(f)
        logger.info(f"Cache existing: {len(existing_cache)} ticker ter-load (akan di-skip).")

    # Filter hanya ticker yang belum ada di cache
    remaining = [t for t in all_tickers if t not in existing_cache]
    logger.info(f"Ticker yang perlu di-fetch: {len(remaining)}")

    if not remaining:
        logger.info("Semua ticker sudah ada di cache. Tidak perlu fetch ulang.")
        _print_summary(existing_cache, logger)
        return

    # ── 3. Fetch dalam batch ──────────────────────────────────────────────────
    cache = dict(existing_cache)  # copy
    total_batches = (len(remaining) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(remaining), BATCH_SIZE):
        batch_tickers = remaining[i : i + BATCH_SIZE]
        batch_jk      = [t + ".JK" for t in batch_tickers]
        batch_num     = (i // BATCH_SIZE) + 1

        logger.info(
            f"Batch {batch_num}/{total_batches}: "
            f"{batch_tickers[0]} – {batch_tickers[-1]} "
            f"({len(batch_tickers)} ticker)"
        )

        batch_result = fetch_sector_batch(batch_jk)
        cache.update(batch_result)

        # Simpan incremental (aman jika proses terhenti di tengah)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)

        # Rate limit buffer
        if i + BATCH_SIZE < len(remaining):
            time.sleep(SLEEP_SEC)

    logger.info(f"Cache selesai. Total: {len(cache)} ticker → {OUTPUT_FILE}")
    _print_summary(cache, logger)


def _print_summary(cache: dict, logger: logging.Logger):
    """Tampilkan distribusi sektor dari cache."""
    from collections import Counter
    dist = Counter(v["sector"] for v in cache.values())
    logger.info("")
    logger.info("── Distribusi Sektor ──────────────────────────────────")
    for sector, count in sorted(dist.items(), key=lambda x: -x[1]):
        logger.info(f"  {sector:<20} : {count:>4} ticker")
    default_count = dist.get("default", 0)
    if default_count > 0:
        logger.info("")
        logger.info(
            f"⚠️  {default_count} ticker masih 'default' "
            f"(yfinance tidak punya info sektor). "
            f"Mereka tetap akan masuk scanning tapi PBV ranking-nya kurang akurat."
        )


if __name__ == "__main__":
    main()
