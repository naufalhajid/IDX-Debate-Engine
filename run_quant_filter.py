"""
run_quant_filter.py — IHSG Quantitative Swing-Trade Scouting Engine
====================================================================
Versi: 3.1

Perubahan dari v3.0:
  1. [INTEGRATE] XlsxDataAdapter sebagai primary data source — Close Price,
     Volume, BVPS, DPS, Piotroski, Altman, ExDate semuanya dari xlsx.
     yfinance HANYA dipakai untuk OHLCV teknikal (SMA, ATR, RSI) yang
     butuh data intraday 60d — ini tidak ada di xlsx scraping.

  2. [FIX KRITIS] Sektor tidak lagi 100% 'default' karena tidak ada cache.
     Sektor sekarang di-resolve via 3 lapis prioritas:
       a. sector_cache.json (output build_sector_cache.py) — paling akurat
       b. TICKER_SECTOR hardcode (40+ ticker populer)
       c. Inferensi dari kolom 'Name' di idx-stocks (kata kunci bank/tbk)
       d. Fallback: 'default'

  3. [INTEGRATE] ExDate dari xlsx (Latest Dividend Ex-Date) — tidak perlu
     yfinance per-ticker lagi. scan_exdate() dipanggil HANYA jika kolom
     xlsx kosong/tidak tersedia.

  4. [IMPROVE] Static filter tambah Piotroski F-Score >= 4 dan
     Altman Z-Score > 1.1 (exclude distressed) langsung dari xlsx.

  5. [IMPROVE] Tambah 'Price to Equity Discount (%)' dari sheet analysis
     sebagai kolom alternatif untuk Valuation Gap (lebih akurat dari Graham
     Number untuk saham yang EPS/BVPS-nya kurang reliable).

  6. [IMPROVE] Semua sheet digabung di awal (single merge) — lebih efisien
     dari baca ulang di beberapa fungsi.

  7. [IMPROVE] PEMANTAUAN KHUSUS dari idx-stocks sheet langsung di-exclude
     di awal pipeline tanpa perlu cek manual.

Perubahan v3.1 (perbaikan mendalam):
  8. [FIX KRITIS] Val_Score dan Prof_Score berubah dari rank(pct=True)
     relatif menjadi scoring absolut berbasis threshold. Score sebelumnya
     tergantung seberapa jelek universe hari itu — saham mediocre bisa
     dapat score tinggi kalau saingannya lebih buruk. Sekarang score
     mencerminkan kualitas absolut saham.

  9. [FIX] Volume benchmark di momentum scoring berubah dari vol_5d_avg
     ke vol_20d_avg, konsisten dengan liquidity gate dan ADT calculation.
     vol_5d_avg terlalu pendek dan mudah distorsi oleh 1-2 hari spike.

  10. [FIX] RSI scoring diperbaiki: RSI rendah (<45, zona akumulasi
      oversold) sekarang diberi skor lebih tinggi dari RSI lemah (35-45).
      Sebelumnya RSI <45 dan RSI >70 (overbought) dapat skor yang sama
      (×0.25) — ini tidak masuk akal secara swing trade logic.
      Tiers baru: Akumulasi (45-55) = 100%, Uptrend (55-70) = 80%,
      Oversold (<45) = 60%, Overbought (>70) = 20%.

  11. [FIX] Piotroski F-Score diintegrasikan ke dalam composite score
      sebagai bonus/penalty eksplisit, bukan hanya gate di static filter.
      F-Score ≥7 (strong) = +5 bonus, F-Score 4-5 (weak) = -5 penalty.

  12. [FIX] Sektor bank dan finance_nonbank menggunakan valuasi berbasis
      ROE/PBV relatif (bukan Graham Number) karena Graham Number tidak
      valid untuk institusi finansial dengan leverage tinggi di aset.

  13. [FIX BUG] _resolve_exdate: loop parse tanggal sekarang punya
      explicit fallback ke yfinance jika semua format gagal di lapis 2,
      bukan silent return None yang menyebabkan AttributeError downstream.

  14. [IMPROVE] CONFIG tambah parameter baru untuk absolute scoring
      thresholds dan RSI tier weights.

Execution order:
  TIDAK perlu build_sector_cache.py terlebih dahulu —
  sektor di-resolve otomatis dari xlsx + hardcode + cache (jika ada).
"""

import glob
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from utils.technicals import compute_atr, compute_rsi, snap_to_tick
from utils.exdate_scanner import ExDateInfo, format_exdate_block, scan_exdate

# ── Import adapter (opsional — graceful jika belum ada) ──────────────────────
try:
    from utils.xlsx_adapter import XlsxDataAdapter
    _HAS_ADAPTER = True
except ImportError:
    _HAS_ADAPTER = False


# ══════════════════════════════════════════════════════════════════════════════
# ── AUTO-DETECT INPUT FILE ────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _find_latest_xlsx(output_dir: str = "output") -> str:
    """
    Auto-detect file xlsx IDX terbaru di folder output/.
    Support Windows (backslash) dan Unix (forward slash).
    Raise FileNotFoundError jika tidak ada file ditemukan.
    """
    patterns = [
        os.path.join(output_dir, "IDX_Fundamental_Analysis_*.xlsx"),
        os.path.join(output_dir, "IDX Fundamental Analysis *.xlsx"),
    ]
    found = []
    for pat in patterns:
        found.extend(glob.glob(pat))

    if not found:
        raise FileNotFoundError(
            f"Tidak ada file IDX_Fundamental_Analysis_*.xlsx di folder '{output_dir}'."
            f"\nPastikan file xlsx hasil scraping sudah ada di folder tersebut."
        )
    # Ambil yang terbaru (sort by nama file — tanggal ada di nama)
    return str(Path(sorted(found, reverse=True)[0]))


# ══════════════════════════════════════════════════════════════════════════════
# ── KONFIGURASI TERPUSAT ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

CONFIG = {
    # ── Path
    # input_file = None → auto-detect xlsx terbaru di output_dir saat runtime
    "input_file":        None,
    "output_dir":        "output",
    "scratch_dir":       "scratch",
    "sector_cache_file": str(Path("output") / "sector_cache.json"),

    # ── Static Filter
    "min_close_price":        100,        # Rp — buang penny stocks
    "max_der":                1.5,        # Debt to Equity Ratio maksimum
    "max_pbv_hard":           6.0,        # PBV ceiling absolut
    "pbv_sector_pctile":      0.80,       # Buang top 20% PBV per sektor
    "min_roe":                0.10,       # ROE minimum TTM (10%)
    "min_piotroski":          4,          # [NEW v3.0] Piotroski F-Score minimum
    "min_altman_z":           1.1,        # [NEW v3.0] Altman Z > 1.1 (bukan distress zone)
    "exclude_pemantauan":     True,       # [NEW v3.0] Exclude PEMANTAUAN KHUSUS

    # ── Graham Number (IHSG-calibrated)
    "graham_k":               18.2,
    "graham_bear_eps":        0.85,
    "graham_bull_eps":        1.15,

    # ── yfinance (HANYA untuk teknikal OHLCV)
    "yf_period":              "60d",
    "yf_retries":             3,
    "yf_retry_delay":         5,

    # ── Liquidity Gate
    "min_adt_20d":            5_000_000_000,
    "min_bars":               20,

    # ── Volume Filter
    "vol_confirmation_ratio": 0.80,

    # ── Suspended/FCA Heuristic
    "max_zero_vol_days":      3,

    # ── RSI Scoring
    "rsi_hard_reject":        75,
    "rsi_accum_lo":           45,
    "rsi_accum_hi":           55,
    "rsi_strong_hi":          70,

    # ── Stop Loss
    "stop_atr_from_sma20":    1.0,
    "stop_atr_from_price":    2.0,
    "stop_hard_floor_pct":    0.92,

    # ── Score Weights (total = 100)
    "weight_valuation":       40,
    "weight_profitability":   20,
    "weight_momentum_rsi":    20,
    "weight_momentum_vol":    20,

    # ── Absolute Valuation Scoring Thresholds (v3.1)
    # Val_Score dihitung absolut: gap tiered, bukan rank relatif.
    # Tier 1 (>=50% gap) -> 100% weight_valuation
    # Tier 2 (20-50%)    -> 70%
    # Tier 3 (5-20%)     -> 40%
    # Tier 4 (<5%)       -> 10% (nyaris tidak undervalued)
    "val_tier1_gap":          50.0,
    "val_tier2_gap":          20.0,
    "val_tier3_gap":           5.0,

    # ── Absolute Profitability Scoring Thresholds (v3.1)
    # ROE >=25% -> 100% weight_profitability
    # ROE 15-25% -> 70%
    # ROE 10-15% -> 40%  (min ROE sudah di-gate di static filter)
    "prof_roe_tier1":         0.25,
    "prof_roe_tier2":         0.15,

    # ── RSI Scoring Weights per tier (v3.1 — asimetris, swing-trade aware)
    # Oversold (<45)    -> 60%  (potensi reversal, menarik tapi butuh konfirmasi)
    # Akumulasi (45-55) -> 100% (sweet spot entry swing)
    # Uptrend (55-70)   -> 80%  (momentum kuat, masih oke)
    # Overbought (>70)  -> 20%  (hard-reject sudah >75, tapi 70-75 tetap lemah)
    "rsi_weight_oversold":    0.60,
    "rsi_weight_accum":       1.00,
    "rsi_weight_uptrend":     0.80,
    "rsi_weight_overbought":  0.20,

    # ── Piotroski Score Adjustment (v3.1 — integrasi ke composite score)
    # F-Score >=7 (strong) -> bonus; F-Score <=5 (marginal) -> penalty
    "piotroski_strong_bonus":  +5,
    "piotroski_weak_penalty":  -5,
    "piotroski_strong_min":     7,
    "piotroski_weak_max":       5,

    # ── Penalties & Bonuses
    "over_extended_penalty":  -15,
    "fresh_breakout_bonus":   +10,

    # ── Output
    "top_n": 10,
}


# ══════════════════════════════════════════════════════════════════════════════
# ── SECTOR MAP — IDX Industry Classification (IDXIC) ─────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

SECTOR_PBV_BENCHMARK = {
    "energy":           {"label": "Energi",               "fair_lo": 0.8, "fair_hi": 2.5},
    "basic_materials":  {"label": "Barang Baku",           "fair_lo": 0.8, "fair_hi": 2.5},
    "industrials":      {"label": "Perindustrian",         "fair_lo": 0.8, "fair_hi": 2.5},
    "consumer_staples": {"label": "Konsumen Primer",       "fair_lo": 1.0, "fair_hi": 3.0},
    "consumer_disc":    {"label": "Konsumen Non-Primer",   "fair_lo": 0.8, "fair_hi": 2.5},
    "healthcare":       {"label": "Kesehatan",             "fair_lo": 1.5, "fair_hi": 4.0},
    "bank":             {"label": "Perbankan",             "fair_lo": 1.5, "fair_hi": 4.0},
    "finance_nonbank":  {"label": "Keuangan Non-Bank",     "fair_lo": 0.8, "fair_hi": 2.5},
    "property":         {"label": "Properti & Real Estate","fair_lo": 0.5, "fair_hi": 1.5},
    "tech":             {"label": "Teknologi",             "fair_lo": 1.5, "fair_hi": 6.0},
    "infrastructure":   {"label": "Infrastruktur",         "fair_lo": 0.8, "fair_hi": 2.5},
    "transport":        {"label": "Transportasi & Logistik","fair_lo": 0.8, "fair_hi": 2.5},
    "default":          {"label": "Lain-lain",             "fair_lo": 0.8, "fair_hi": 2.5},
}

# Hardcode 70+ ticker populer sebagai fallback lapis-2
# (dipakai jika sector_cache.json tidak ada)
TICKER_SECTOR_HARDCODE: dict[str, str] = {
    # Bank
    "BBCA": "bank", "BBRI": "bank", "BMRI": "bank", "BBNI": "bank",
    "BRIS": "bank", "BTPS": "bank", "BNGA": "bank", "BNII": "bank",
    "PNBN": "bank", "BDMN": "bank", "MEGA": "bank", "BJTM": "bank",
    "BJBR": "bank", "NISP": "bank", "BBTN": "bank", "AGRO": "bank",
    "BABP": "bank", "ARTO": "bank", "SEABANK": "bank",
    # Finance non-bank
    "ADMF": "finance_nonbank", "BFIN": "finance_nonbank", "WOMF": "finance_nonbank",
    "MFIN": "finance_nonbank", "CFIN": "finance_nonbank", "PNLF": "finance_nonbank",
    "ASII": "finance_nonbank",  # Astra Financial arm — industrial tapi PBV mix
    "SRTG": "finance_nonbank",
    # Consumer staples
    "UNVR": "consumer_staples", "ICBP": "consumer_staples", "MYOR": "consumer_staples",
    "INDF": "consumer_staples", "SIDO": "consumer_staples", "CPIN": "consumer_staples",
    "JPFA": "consumer_staples", "GOOD": "consumer_staples", "ULTJ": "consumer_staples",
    "AALI": "consumer_staples", "LSIP": "consumer_staples", "SGRO": "consumer_staples",
    # Consumer discretionary
    "AUTO": "consumer_disc", "GJTL": "consumer_disc", "SMSM": "consumer_disc",
    "RALS": "consumer_disc", "MAPI": "consumer_disc", "ACES": "consumer_disc",
    # Healthcare
    "KLBF": "healthcare", "HEAL": "healthcare", "MIKA": "healthcare",
    "PRDA": "healthcare", "DVLA": "healthcare", "TSPC": "healthcare",
    "MERK": "healthcare",
    # Mining / Energy
    "ADRO": "energy", "BYAN": "energy", "PTBA": "energy", "ITMG": "energy",
    "HRUM": "energy", "DOID": "energy", "ELSA": "energy", "MEDC": "energy",
    "AKRA": "energy", "PGAS": "infrastructure",
    # Basic materials
    "ANTM": "basic_materials", "INCO": "basic_materials", "MDKA": "basic_materials",
    "TINS": "basic_materials", "SMGR": "basic_materials", "INTP": "basic_materials",
    "TPIA": "basic_materials",
    # Property
    "BSDE": "property", "SMRA": "property", "CTRA": "property",
    "PWON": "property", "LPKR": "property", "DMAS": "property",
    # Tech / Telecom
    "TLKM": "tech", "EXCL": "tech", "ISAT": "tech",
    "GOTO": "tech", "BUKA": "tech", "EMTK": "tech",
    # Infrastructure
    "JSMR": "infrastructure", "WSKT": "infrastructure", "WIKA": "infrastructure",
    # Transport
    "GIAA": "transport", "BIRD": "transport", "BLUEBIRD": "transport",
    # Industrials
    "MAIN": "industrials", "SRIL": "industrials", "KINO": "industrials",
}

# Kata kunci di kolom 'Name' untuk inferensi sektor — lapis-3
_NAME_SECTOR_KEYWORDS: list[tuple[list[str], str]] = [
    (["bank", "banking", "syariah bank", "bpr"],                     "bank"),
    (["multifinance", "finance", "leasing", "asuransi", "insurance"], "finance_nonbank"),
    (["properti", "property", "real estate", "realty", "realestate"], "property"),
    (["farmasi", "pharma", "hospital", "rumah sakit", "kesehatan",
      "klinik", "alkes", "medika", "medis"],                         "healthcare"),
    (["tambang", "mining", "coal", "batubara", "nikel", "nickel",
      "gold", "emas", "timah", "tembaga", "copper"],                  "energy"),
    (["semen", "cement", "kimia", "chemical", "petrokimia"],          "basic_materials"),
    (["teknologi", "technology", "telekomunikasi", "telecom",
      "digital", "internet", "software"],                             "tech"),
    (["toll", "tol", "pelabuhan", "port", "bandara", "airport",
      "listrik", "electricity", "gas", "air minum", "pdam"],         "infrastructure"),
    (["logistik", "logistic", "shipping", "pelayaran", "penerbangan",
      "airline", "trucking", "ekspedisi"],                            "transport"),
    (["konsumer", "consumer", "makanan", "minuman", "food",
      "beverage", "agri", "perkebunan", "plantation", "kelapa sawit",
      "palm oil", "poultry", "peternakan"],                           "consumer_staples"),
    (["otomotif", "automotive", "motor", "mobil", "tekstil",
      "fashion", "retail", "ritel", "department store"],              "consumer_disc"),
]


# ══════════════════════════════════════════════════════════════════════════════
# ── SEKTOR RESOLVER — 4-lapis prioritas ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _build_sector_map(
    tickers: list[str],
    names: dict[str, str],
    cache_file: str,
    logger: logging.Logger,
) -> dict[str, str]:
    """
    Resolve sektor untuk setiap ticker via 4 lapis prioritas:
      1. sector_cache.json  → hasil build_sector_cache.py (yfinance)
      2. TICKER_SECTOR_HARDCODE → 70+ ticker populer hardcode
      3. Inferensi dari nama perusahaan via keyword matching
      4. Fallback: 'default'

    Args:
        tickers   : list semua ticker yang perlu di-resolve
        names     : dict {ticker: company_name} dari idx-stocks sheet
        cache_file: path ke sector_cache.json
        logger    : logger instance

    Returns:
        dict {ticker: sector_key}
    """
    result: dict[str, str] = {}

    # ── Lapis 1: sector_cache.json ────────────────────────────────────────────
    cache: dict[str, str] = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Format: {"BBRI": {"sector": "bank", ...}} atau {"BBRI": "bank"}
            for t, v in raw.items():
                cache[t] = v["sector"] if isinstance(v, dict) else str(v)
            logger.info(f"[Sector] Cache loaded: {len(cache)} ticker dari {cache_file}")
        except Exception as e:
            logger.warning(f"[Sector] Gagal baca cache: {e}")
    else:
        logger.warning(
            f"[Sector] {cache_file} tidak ada. "
            f"Gunakan lapis 2–4 (hardcode + keyword + default). "
            f"Jalankan build_sector_cache.py untuk akurasi lebih baik."
        )

    miss_l1, miss_l2, miss_l3, miss_l4 = [], [], [], []

    for t in tickers:
        # Lapis 1
        if t in cache:
            result[t] = cache[t]
            continue
        miss_l1.append(t)

        # Lapis 2: hardcode
        if t in TICKER_SECTOR_HARDCODE:
            result[t] = TICKER_SECTOR_HARDCODE[t]
            continue
        miss_l2.append(t)

        # Lapis 3: keyword matching dari nama perusahaan
        name_lower = names.get(t, "").lower()
        matched = False
        for keywords, sector in _NAME_SECTOR_KEYWORDS:
            if any(kw in name_lower for kw in keywords):
                result[t] = sector
                matched = True
                break
        if matched:
            continue
        miss_l3.append(t)

        # Lapis 4: default
        result[t] = "default"
        miss_l4.append(t)

    logger.info(
        f"[Sector] Resolve selesai: "
        f"cache={len(tickers)-len(miss_l1)}, "
        f"hardcode={len(miss_l1)-len(miss_l2)}, "
        f"keyword={len(miss_l2)-len(miss_l3)}, "
        f"default={len(miss_l4)}"
    )
    if miss_l4:
        logger.debug(f"[Sector] Ticker 'default': {miss_l4[:20]}"
                     + ("..." if len(miss_l4) > 20 else ""))

    return result


# ══════════════════════════════════════════════════════════════════════════════
# ── EXDATE RESOLVER — xlsx primary, yfinance fallback ────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_exdate(
    ticker: str,
    row: pd.Series,
    current_px: float,
    adapter: "XlsxDataAdapter | None",
) -> ExDateInfo:
    """
    Resolve ExDateInfo untuk satu ticker via 3 lapis prioritas:
      1. XlsxDataAdapter.get_exdate_info() — O(1), paling akurat
      2. Parse langsung kolom 'Latest Dividend Ex-Date' dari row xlsx
         - Jika tanggal sudah lewat (days < 0): lanjut ke lapis 3
         - Jika semua format parse gagal (ValueError): lanjut ke lapis 3
      3. scan_exdate() via yfinance — fallback lambat, hanya jika lapis 1-2 gagal

    Catatan: import CRITICAL_WINDOW_DAYS / WARNING_WINDOW_DAYS dilakukan
    di level modul — tidak perlu import ulang di dalam fungsi ini.
    """
    from utils.exdate_scanner import CRITICAL_WINDOW_DAYS, WARNING_WINDOW_DAYS

    # Lapis 1: xlsx adapter
    if adapter is not None:
        try:
            info = adapter.get_exdate_info(ticker, current_px)
            if info["source"] == "xlsx":
                return info
        except Exception:
            pass

    # Lapis 2: parse langsung kolom 'Latest Dividend Ex-Date' dari row
    exdate_str = str(row.get("Latest Dividend Ex-Date", "")).strip()
    if exdate_str and exdate_str not in ("-", "nan", "NaT", ""):
        parsed_date = None
        for fmt in ("%d %b %y", "%d %b %Y", "%Y-%m-%d"):
            try:
                parsed_date = datetime.strptime(exdate_str, fmt).date()
                break
            except ValueError:
                continue

        if parsed_date is not None:
            today = datetime.now(timezone.utc).date()
            days  = (parsed_date - today).days
            if days >= 0:
                # Ex-date masih ke depan — hitung risk tier
                if days <= CRITICAL_WINDOW_DAYS:   tier = "CRITICAL"
                elif days <= WARNING_WINDOW_DAYS:  tier = "WARNING"
                else:                              tier = "CLEAR"
                div = float(row.get("Dividend (TTM)", 0) or 0)
                return {
                    "has_upcoming_exdate": tier != "CLEAR",
                    "ex_date":             str(parsed_date),
                    "days_until_exdate":   days,
                    "div_per_share":       div or None,
                    "div_yield_pct":       round(div / current_px * 100, 2) if div and current_px > 0 else None,
                    "risk_tier":           tier,
                    "expected_drop_rp":    div or None,
                    "source":              "xlsx_direct",
                }
            # days < 0 → ex-date sudah lewat → fall through ke lapis 3

    # Lapis 3: yfinance (fallback lambat — hanya jika xlsx tidak ada/kosong/lewat)
    return scan_exdate(ticker, current_price=current_px)


# ══════════════════════════════════════════════════════════════════════════════
# ── HELPERS ───────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def setup_logging(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("quant_filter")


def download_yf_with_retry(
    tickers: list[str],
    period: str,
    retries: int,
    delay: int,
    logger: logging.Logger,
) -> pd.DataFrame:
    """Download yfinance OHLCV dengan retry + paksa MultiIndex untuk single ticker."""
    for attempt in range(1, retries + 1):
        try:
            logger.info(f"yfinance download attempt {attempt}/{retries} ({len(tickers)} ticker)...")
            data = yf.download(
                tickers,
                period=period,
                group_by="ticker",
                progress=False,
                auto_adjust=True,
            )
            if data.empty:
                raise ValueError("yfinance mengembalikan DataFrame kosong.")

            # Paksa MultiIndex untuk single-ticker edge case
            if not isinstance(data.columns, pd.MultiIndex):
                logger.warning("Flat columns dari yfinance — paksa MultiIndex wrapper")
                data = pd.concat({tickers[0]: data}, axis=1)

            logger.info(f"Download berhasil. Shape: {data.shape}")
            return data

        except Exception as exc:
            logger.warning(f"Download gagal (attempt {attempt}): {exc}")
            if attempt < retries:
                wait = delay * attempt
                logger.info(f"Retry dalam {wait} detik...")
                time.sleep(wait)

    logger.error("Semua retry yfinance gagal. Pipeline dihentikan.")
    raise RuntimeError("yfinance download gagal setelah semua retry.")


# ══════════════════════════════════════════════════════════════════════════════
# ── TICKER ANALYZER ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _analyze_ticker(
    row: pd.Series,
    df_t: pd.DataFrame,
    cfg: dict,
    logger: logging.Logger,
    adapter: "XlsxDataAdapter | None" = None,
) -> dict | None:
    """
    Analisis teknikal + fundamental satu ticker.
    Return dict result jika lolos semua filter, None jika tidak lolos.
    """
    t = row["Ticker"]

    close = df_t["Close"].squeeze()
    vol   = df_t["Volume"].squeeze()
    high  = df_t["High"].squeeze()
    low   = df_t["Low"].squeeze()

    # ── Suspended / FCA Board Exclusion ──────────────────────────────────────
    recent_vol  = vol.tail(5).sum()
    avg_vol_20d = vol.tail(20).mean()
    if (
        (vol.tail(20) == 0).sum() > cfg["max_zero_vol_days"] or
        (avg_vol_20d > 0 and (recent_vol / avg_vol_20d) < 0.10)
    ):
        logger.info(f"[{t}] Excluded: suspek suspended/FCA (volume anomali)")
        return None

    current_px: float = float(close.iloc[-1])

    # ── ExDate — xlsx primary, yfinance fallback ──────────────────────────────
    exdate_info: ExDateInfo = _resolve_exdate(t, row, current_px, adapter)

    if exdate_info["risk_tier"] == "CRITICAL":
        logger.info(
            f"[{t}] Excluded: ex-date CRITICAL "
            f"(dalam {exdate_info['days_until_exdate']} hari — "
            f"source={exdate_info['source']})"
        )
        return None

    # ── RSI (14) ──────────────────────────────────────────────────────────────
    rsi_series = compute_rsi(close)
    if len(rsi_series) == 0:
        return None
    rsi_latest: float = float(rsi_series.iloc[-1])

    if rsi_latest > cfg["rsi_hard_reject"]:
        logger.debug(f"[{t}] RSI {rsi_latest:.1f} > {cfg['rsi_hard_reject']}, hard reject")
        return None

    # ── SMA 20 — Uptrend Confirmation ────────────────────────────────────────
    sma20 = close.rolling(20).mean()
    if pd.isna(sma20.iloc[-1]):
        return None
    sma20_latest: float = float(sma20.iloc[-1])

    if current_px <= sma20_latest:
        return None  # Harga di bawah SMA20 = downtrend, skip

    # ── ATR (14) ──────────────────────────────────────────────────────────────
    atr_series = compute_atr(high, low, close)
    if pd.isna(atr_series.iloc[-1]):
        return None
    atr_14: float = float(atr_series.iloc[-1])

    # ── Liquidity Gate: ADT 20d ───────────────────────────────────────────────
    adt_20: float = float((close * vol).tail(20).mean())
    if adt_20 < cfg["min_adt_20d"]:
        logger.debug(f"[{t}] ADT Rp {adt_20:,.0f} < threshold, skip")
        return None

    # ── Volume Confirmation ───────────────────────────────────────────────────
    vol_20d_avg: float = float(vol.tail(20).mean())
    vol_3d_avg:  float = float(vol.tail(3).mean())
    if vol_3d_avg <= vol_20d_avg * cfg["vol_confirmation_ratio"]:
        return None

    # curr_vol dipakai untuk momentum scoring (dibandingkan vol_20d_avg)
    curr_vol: float = float(vol.iloc[-1])

    # ── Momentum Score ────────────────────────────────────────────────────────
    mom_score: float = 0.0
    mom_note:  list[str] = []

    # [v3.1 FIX] RSI scoring asimetris — swing-trade aware.
    # Oversold (<45) lebih menarik dari overbought (>70) untuk entry swing,
    # karena ada potensi reversal. Sebelumnya keduanya dapat skor yang sama (x0.25).
    rsi_w = cfg["weight_momentum_rsi"]
    if cfg["rsi_accum_lo"] <= rsi_latest <= cfg["rsi_accum_hi"]:
        mom_score += rsi_w * cfg["rsi_weight_accum"]
        mom_note.append(f"RSI Akumulasi ({rsi_latest:.1f})")
    elif cfg["rsi_accum_hi"] < rsi_latest <= cfg["rsi_strong_hi"]:
        mom_score += rsi_w * cfg["rsi_weight_uptrend"]
        mom_note.append(f"RSI Uptrend Kuat ({rsi_latest:.1f})")
    elif rsi_latest > cfg["rsi_strong_hi"]:
        # Overbought (70-75 range — di atas 75 sudah hard-reject)
        mom_score += rsi_w * cfg["rsi_weight_overbought"]
        mom_note.append(f"RSI Overbought ({rsi_latest:.1f})")
    else:
        # RSI < rsi_accum_lo (< 45) — zona oversold, potensi reversal
        mom_score += rsi_w * cfg["rsi_weight_oversold"]
        mom_note.append(f"RSI Oversold ({rsi_latest:.1f})")

    # [v3.1 FIX] Volume benchmark berubah dari vol_5d_avg ke vol_20d_avg.
    # vol_5d_avg terlalu pendek — mudah terdistorsi oleh 1-2 hari spike volume,
    # dan tidak konsisten dengan liquidity gate (ADT 20d) dan vol_20d_avg
    # yang sudah dihitung di atas untuk volume confirmation.
    if curr_vol > vol_20d_avg:
        mom_score += cfg["weight_momentum_vol"]
        mom_note.append("Volume Breakout")
    else:
        mom_score += cfg["weight_momentum_vol"] * 0.5
        mom_note.append("Volume Normal")

    # ── Composite Score + SMA20 Distance Adjustments ─────────────────────────
    total_score: float = row["Val_Score"] + row["Prof_Score"] + mom_score
    dist_to_sma20_pct: float = (current_px - sma20_latest) / sma20_latest

    if dist_to_sma20_pct > 0.10:
        total_score += cfg["over_extended_penalty"]
        mom_note.append(f"Over-Extended (+{dist_to_sma20_pct*100:.1f}% SMA20)")
    elif 0.01 <= dist_to_sma20_pct <= 0.05:
        total_score += cfg["fresh_breakout_bonus"]
        mom_note.append(f"Fresh Breakout (+{dist_to_sma20_pct*100:.1f}% SMA20)")

    # [v3.1 NEW] Piotroski F-Score adjustment — diintegrasikan ke composite score.
    # Sebelumnya F-Score hanya jadi gate di static filter (>=4) tanpa membedakan
    # kualitas antara saham F-Score 4 vs F-Score 9. Sekarang ada reward/penalty
    # eksplisit untuk mencerminkan perbedaan kualitas fundamental yang nyata.
    piotroski = int(row.get("Piotroski F-Score", 0) or 0)
    if piotroski >= cfg["piotroski_strong_min"]:
        total_score += cfg["piotroski_strong_bonus"]
        mom_note.append(f"F-Score Kuat ({piotroski}/9)")
    elif piotroski <= cfg["piotroski_weak_max"]:
        total_score += cfg["piotroski_weak_penalty"]
        mom_note.append(f"F-Score Lemah ({piotroski}/9)")

    # Penalti jika tidak ada margin of safety (Valuation gap == 0)
    try:
        gap_pct = float(row.get("Valuation_Gap_Pct", 0) or 0)
    except Exception:
        gap_pct = 0.0
    if gap_pct == 0.0:
        total_score -= 10
        mom_note.append("Penalty: no margin of safety (-10)")

    # Cap composite score to 0..100 to keep the scale interpretable
    total_score = max(0.0, min(total_score, 100.0))

    # ── Stop Loss (ATR-based + BEI tick size) ─────────────────────────────────
    stop_candidate_1 = sma20_latest - (cfg["stop_atr_from_sma20"] * atr_14)
    stop_candidate_2 = current_px   - (cfg["stop_atr_from_price"] * atr_14)
    stop_loss: float = max(stop_candidate_1, stop_candidate_2)
    stop_loss = max(stop_loss, current_px * cfg["stop_hard_floor_pct"])
    stop_loss = snap_to_tick(stop_loss)

    # ── Sector PBV Context ────────────────────────────────────────────────────
    sector_key   = row["Sector"]
    sector_bench = SECTOR_PBV_BENCHMARK.get(sector_key, SECTOR_PBV_BENCHMARK["default"])
    pbv_current: float = float(row.get("Current Price to Book Value", 0))
    pbv_label = (
        "Murah" if pbv_current < sector_bench["fair_lo"] else
        "Wajar" if pbv_current <= sector_bench["fair_hi"] else
        "Mahal"
    )

    # ── Quality Flags dari xlsx ───────────────────────────────────────────────
    # Catatan: piotroski sudah didefinisikan di atas (blok Piotroski adjustment)
    altman_z = row.get("Altman Z-Score (Modified)", 0)

    return {
        "Ticker":                    t,
        "Sektor":                    row["Sector_Label"],
        "Sektor Key":                sector_key,
        "Current Price":             current_px,
        "Stop Loss Level":           round(stop_loss, 0),
        "Est. Fair Value (Graham)":  row["Graham_Number"],
        "Graham_Bear":               row["Graham_Bear"],
        "Graham_Bull":               row["Graham_Bull"],
        "Valuation Gap (%)":         row["Valuation_Gap_Pct"],
        "Price to Equity Discount": row.get("Price to Equity Discount (%)", 0),
        "RSI (14)":                  rsi_latest,
        "SMA 20":                    sma20_latest,
        "ATR (14)":                  atr_14,
        "ROE (TTM)":                 row["Return on Equity (TTM)"],
        "DER (Quarter)":             row["Debt to Equity Ratio (Quarter)"],
        "PBV":                       pbv_current,
        "PBV vs Sektor":             pbv_label,
        "PBV Sektor Percentile":     round(row["PBV_Sector_Pctile"] * 100, 1),
        "ADT 20d (Rp)":              adt_20,
        "Composite Score":           total_score,
        "Entry Strategy":            " | ".join(mom_note),
        "Piotroski F-Score":         int(piotroski) if piotroski else 0,
        "Altman Z-Score":            float(altman_z) if altman_z else 0.0,
        "ExDate Risk":               exdate_info["risk_tier"],
        "ExDate Date":               exdate_info.get("ex_date"),
        "ExDate Source":             exdate_info.get("source", "unknown"),
        "_exdate_info":              exdate_info,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(cfg: dict) -> pd.DataFrame:
    logger = setup_logging(cfg["scratch_dir"])
    os.makedirs(cfg["output_dir"], exist_ok=True)
    os.makedirs(cfg["scratch_dir"], exist_ok=True)

    logger.info("=" * 60)
    logger.info("IHSG Quantitative Swing-Trade Scouting Engine v3.0")
    logger.info("=" * 60)

    # ── Resolve input_file — auto-detect jika None ────────────────────────────
    if not cfg.get("input_file"):
        cfg["input_file"] = _find_latest_xlsx(cfg.get("output_dir", "output"))
    cfg["input_file"] = str(Path(cfg["input_file"]))  # normalisasi separator OS
    logger.info(f"Input file: {cfg['input_file']}")

    # ── Resolve sector_cache_file path (Windows-safe) ─────────────────────────
    cfg["sector_cache_file"] = str(Path(cfg["sector_cache_file"]))

    # ── 0. Inisialisasi XlsxDataAdapter ──────────────────────────────────────
    adapter: "XlsxDataAdapter | None" = None
    if _HAS_ADAPTER:
        try:
            adapter = XlsxDataAdapter(cfg["input_file"])
            logger.info(f"[Adapter] XlsxDataAdapter aktif → {cfg['input_file']}")
        except Exception as e:
            logger.warning(f"[Adapter] Gagal init XlsxDataAdapter: {e}")

    # ── 1. DATA INGESTION — semua dari xlsx ───────────────────────────────────
    logger.info(f"Membaca: {cfg['input_file']}")

    df_ks     = pd.read_excel(cfg["input_file"], sheet_name="key-statistics")
    df_prices = pd.read_excel(cfg["input_file"], sheet_name="stock-prices")
    df_anal   = pd.read_excel(cfg["input_file"], sheet_name="analysis")
    df_idx    = pd.read_excel(cfg["input_file"], sheet_name="idx-stocks")

    # Merge semua sheet
    df = df_ks.merge(
        df_prices[["Ticker", "Close Price", "Volume", "High Price", "Low Price"]],
        on="Ticker", how="left",
    ).merge(
        df_anal[["Ticker", "Price to Equity Discount (%)", "Composite Rank"]],
        on="Ticker", how="left",
    ).merge(
        df_idx[["Ticker", "Name", "Note"]],
        on="Ticker", how="left",
    )

    # Numeric coerce
    for col in [
        "Close Price", "Debt to Equity Ratio (Quarter)",
        "Current Price to Book Value", "Return on Equity (TTM)",
        "Current EPS (TTM)", "Piotroski F-Score",
        "Altman Z-Score (Modified)", "Price to Equity Discount (%)",
        "Current Book Value Per Share",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    logger.info(f"Total ticker universe: {len(df)}")

    # ── 1b. Exclude PEMANTAUAN KHUSUS di awal ────────────────────────────────
    if cfg.get("exclude_pemantauan", True):
        n_before = len(df)
        df = df[~df["Note"].str.contains("PEMANTAUAN KHUSUS", na=False)].copy()
        logger.info(f"Exclude PEMANTAUAN KHUSUS: {n_before} → {len(df)}")

    # ── 2. SECTOR RESOLVE — 4 lapis prioritas ────────────────────────────────
    names_map = dict(zip(df_idx["Ticker"], df_idx["Name"].fillna("")))
    sector_map = _build_sector_map(
        tickers=df["Ticker"].tolist(),
        names=names_map,
        cache_file=cfg["sector_cache_file"],
        logger=logger,
    )
    df["Sector"] = df["Ticker"].map(sector_map).fillna("default")

    # PBV percentile per sektor (untuk filter + scoring)
    df["PBV_Sector_Pctile"] = df.groupby("Sector")["Current Price to Book Value"].rank(
        pct=True, ascending=True
    )
    df["Sector_Label"] = df["Sector"].map(
        {k: v["label"] for k, v in SECTOR_PBV_BENCHMARK.items()}
    ).fillna("Lain-lain")

    # ── 3. STATIC FILTERING ───────────────────────────────────────────────────
    alt_col = "Altman Z-Score (Modified)"

    filtered = df[
        (df["Close Price"] > cfg["min_close_price"]) &
        (df["Debt to Equity Ratio (Quarter)"] < cfg["max_der"]) &
        (df["PBV_Sector_Pctile"] < cfg["pbv_sector_pctile"]) &
        (df["Current Price to Book Value"] < cfg["max_pbv_hard"]) &
        (df["Return on Equity (TTM)"] > cfg["min_roe"]) &
        # [NEW v3.0] Piotroski F-Score
        (df["Piotroski F-Score"] >= cfg["min_piotroski"]) &
        # [NEW v3.0] Altman Z-Score — exclude distress zone
        # (0 = data tidak ada, skip filter; > 0 harus > threshold)
        ((df[alt_col] == 0) | (df[alt_col].isna()) | (df[alt_col] > cfg["min_altman_z"]))
    ].copy()

    logger.info(f"Lolos static filter: {len(filtered)} ticker")

    # Distribusi sektor setelah filter
    sector_dist = filtered["Sector"].value_counts()
    logger.info("Distribusi sektor:\n" + sector_dist.to_string())

    # ── 4. VALUATION SCORING — Graham Number (IHSG-calibrated) ───────────────
    bvps = filtered["Current Book Value Per Share"]
    eps  = filtered["Current EPS (TTM)"]
    k    = cfg["graham_k"]

    valid_graham = (eps > 0) & (bvps > 0)
    filtered["Graham_Number"] = np.where(valid_graham, np.sqrt(k * eps * bvps), 0)
    filtered["Graham_Bear"]   = np.where(valid_graham, np.sqrt(k * eps * cfg["graham_bear_eps"] * bvps), 0)
    filtered["Graham_Bull"]   = np.where(valid_graham, np.sqrt(k * eps * cfg["graham_bull_eps"] * bvps), 0)

    filtered["Valuation_Gap_Pct"] = (
        (filtered["Graham_Number"] - filtered["Close Price"]) / filtered["Close Price"] * 100
    ).clip(lower=0)

    # [v3.1 FIX] Absolute threshold-based Val_Score — tidak lagi rank(pct=True).
    # Rank relatif membuat saham mediocre dapat score tinggi jika universe sedang
    # penuh saham jelek. Score absolut mencerminkan kualitas saham itu sendiri.
    #
    # Sektor bank dan finance_nonbank dikecualikan dari Graham Number karena
    # formula Graham dirancang untuk non-finansial. Untuk bank/finance,
    # digunakan PBV relatif vs benchmark sektor sebagai proxy valuasi.
    def _compute_val_score(row: pd.Series, cfg: dict) -> float:
        sector = row.get("Sector", "default")
        w = cfg["weight_valuation"]

        if sector in ("bank", "finance_nonbank"):
            # Untuk sektor finansial: gunakan PBV vs sektor benchmark
            pbv = float(row.get("Current Price to Book Value", 0) or 0)
            bench = SECTOR_PBV_BENCHMARK.get(sector, SECTOR_PBV_BENCHMARK["default"])
            fair_lo = bench["fair_lo"]
            if pbv <= 0:
                return w * 0.10
            if pbv < fair_lo * 0.70:          # sangat murah vs benchmark sektor
                return w * 1.00
            if pbv < fair_lo * 0.90:
                return w * 0.70
            if pbv <= fair_lo:
                return w * 0.40
            return w * 0.10                   # di atas fair_lo = tidak menarik

        # Non-finansial: Graham-based gap
        gap = float(row.get("Valuation_Gap_Pct", 0) or 0)
        if gap >= cfg["val_tier1_gap"]:
            return w * 1.00
        if gap >= cfg["val_tier2_gap"]:
            return w * 0.70
        if gap >= cfg["val_tier3_gap"]:
            return w * 0.40
        return w * 0.10

    filtered["Val_Score"] = filtered.apply(lambda r: _compute_val_score(r, cfg), axis=1)

    # ── 5. PROFITABILITY SCORING ──────────────────────────────────────────────
    # [v3.1 FIX] Absolute threshold-based Prof_Score — tidak lagi rank(pct=True).
    def _compute_prof_score(roe: float, cfg: dict) -> float:
        w = cfg["weight_profitability"]
        if pd.isna(roe) or roe <= 0:
            return 0.0
        if roe >= cfg["prof_roe_tier1"]:
            return w * 1.00
        if roe >= cfg["prof_roe_tier2"]:
            return w * 0.70
        return w * 0.40  # 10-15% — sudah lolos min_roe gate (10%)

    filtered["Prof_Score"] = filtered["Return on Equity (TTM)"].apply(
        lambda r: _compute_prof_score(r, cfg)
    )

    # ── 6. DYNAMIC TECHNICALS VIA YFINANCE ───────────────────────────────────
    valid_tickers = filtered["Ticker"].tolist()
    tickers_yf    = [t + ".JK" for t in valid_tickers]

    data = download_yf_with_retry(
        tickers_yf,
        period=cfg["yf_period"],
        retries=cfg["yf_retries"],
        delay=cfg["yf_retry_delay"],
        logger=logger,
    )

    results = []
    for _, row in filtered.iterrows():
        t_yf = row["Ticker"] + ".JK"
        if t_yf not in data.columns.get_level_values(0):
            continue
        df_t = data[t_yf].dropna(how="all")
        if len(df_t) < cfg["min_bars"]:
            continue
        result = _analyze_ticker(row, df_t, cfg, logger, adapter=adapter)
        if result:
            results.append(result)

    # ── 7. FINALIZE & OUTPUT ──────────────────────────────────────────────────
    final_df = pd.DataFrame(results)

    if final_df.empty:
        logger.warning("Tidak ada ticker yang lolos semua filter.")
    else:
        final_df = final_df.sort_values("Composite Score", ascending=False).head(cfg["top_n"])
        logger.info(f"Top {len(final_df)} kandidat berhasil disaring.")

    # Export JSON (untuk orchestrator.py)
    if not final_df.empty:
        json_path = os.path.join(cfg["output_dir"], "top10_candidates.json")
        export_df = final_df.drop(columns=["_exdate_info"], errors="ignore")
        export_df.to_json(json_path, orient="records", indent=2, force_ascii=False)
        logger.info(f"JSON diekspor → {json_path}")

    # Export Markdown Report
    md_content = _build_markdown_report(final_df, cfg)
    report_path = os.path.join(cfg["scratch_dir"], "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info(f"Report → {report_path}")

    logger.info("PIPELINE SELESAI.")
    return final_df


# ══════════════════════════════════════════════════════════════════════════════
# ── REPORT BUILDER ────────────────────────────────────────────════════════════
# ══════════════════════════════════════════════════════════════════════════════

def _build_markdown_report(final_df: pd.DataFrame, cfg: dict) -> str:
    lines = []
    lines.append(f"# 🏆 Top {cfg['top_n']} High-Conviction IHSG Swing Candidates")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append(f"*Engine: v3.1 — Absolute scoring | Asymmetric RSI | Piotroski integrated | Bank-aware valuation*")
    lines.append("")
    lines.append(
        "| Rank | Ticker | Sektor | Harga | Stop Loss | Graham Fair Value "
        "| Score | Gap | RSI | PBV | F-Score | Entry Note |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")

    for i, (_, r) in enumerate(final_df.iterrows(), 1):
        fv_str = (
            f"Rp {r['Graham_Bear']:,.0f} – Rp {r['Graham_Bull']:,.0f}"
            if r["Est. Fair Value (Graham)"] > 0 else "N/A"
        )
        exdate_icon = " ⚠️" if r["ExDate Risk"] == "WARNING" else ""
        ex_src = f" [{r.get('ExDate Source','?')}]" if r.get("ExDate Source") else ""
        piotroski_icon = (
            "🟢" if r.get("Piotroski F-Score", 0) >= 7 else
            "🟡" if r.get("Piotroski F-Score", 0) >= 4 else "🔴"
        )
        lines.append(
            f"| {i} "
            f"| **{r['Ticker']}**{exdate_icon} "
            f"| {r['Sektor']} "
            f"| Rp {r['Current Price']:,.0f} "
            f"| **Rp {r['Stop Loss Level']:,.0f}** "
            f"| {fv_str} "
            f"| **{r['Composite Score']:.1f}/100** "
            f"| +{r['Valuation Gap (%)']:.1f}% "
            f"| {r['RSI (14)']:.1f} "
            f"| {r['PBV']:.1f}× ({r['PBV vs Sektor']}) "
            f"| {piotroski_icon} {r.get('Piotroski F-Score', 'N/A')}/9 "
            f"| {r['Entry Strategy']}{ex_src} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("> ⚠️ = Mendekati ex-date dividen. F-Score: 🟢 ≥7 / 🟡 4–6 / 🔴 <4")
    lines.append("")

    # ExDate Detail Blocks untuk WARNING tier
    if not final_df.empty:
        warning_rows = final_df[final_df["ExDate Risk"] == "WARNING"]
        if not warning_rows.empty:
            lines.append("## ⚠️ Dividend Ex-Date Risk Details")
            lines.append("")
            for _, wr in warning_rows.iterrows():
                ex_info: ExDateInfo = wr["_exdate_info"]
                lines.append("```")
                lines.append(format_exdate_block(wr["Ticker"], ex_info).strip())
                lines.append("```")
                lines.append("")

    # Investment thesis untuk rank #1
    if not final_df.empty:
        top1 = final_df.iloc[0]
        max_dd = ((top1["Current Price"] - top1["Stop Loss Level"]) / top1["Current Price"]) * 100
        lines.append(f"## 💡 Investment Thesis: {top1['Ticker']} (Rank #1)")
        lines.append("")
        lines.append(
            f"**{top1['Ticker']}** ({top1['Sektor']}) adalah kandidat tertinggi "
            f"berdasarkan multi-factor swing strategy."
        )
        lines.append("")
        lines.append(
            f"- **Valuation MoS**: Diskon **{top1['Valuation Gap (%)']:.1f}%** "
            f"terhadap Graham Fair Value. "
            f"PBV saat ini {top1['PBV']:.1f}× — **{top1['PBV vs Sektor']}** vs sektor."
        )
        lines.append(
            f"- **Quality**: Piotroski F-Score **{top1.get('Piotroski F-Score','N/A')}/9** | "
            f"Altman Z **{top1.get('Altman Z-Score', 'N/A')}**"
        )
        lines.append(
            f"- **Momentum**: Harga Rp {top1['Current Price']:,.0f} di atas "
            f"SMA-20 (Rp {top1['SMA 20']:,.0f}). {top1['Entry Strategy']}."
        )
        lines.append(
            f"- **Profitabilitas**: ROE {top1['ROE (TTM)']*100:.1f}% | "
            f"DER {top1['DER (Quarter)']:.2f}×"
        )
        lines.append(
            f"- **Risk Management**: Stop loss di **Rp {top1['Stop Loss Level']:,.0f}** "
            f"(ATR-based, max drawdown ~{max_dd:.1f}%)"
        )
    else:
        lines.append(
            "> Tidak ada ticker yang lolos semua filter. "
            "Coba longgarkan threshold atau perbarui data input."
        )

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ── ENTRY POINT ───────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_pipeline(CONFIG)