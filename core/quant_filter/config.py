"""Configuration and static mappings for the IHSG quantitative filter."""

import glob
import os
from pathlib import Path

from core.settings import settings

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
    # v3.2 — swing trade optimized
    "version":           "v3.2",

    # ── Path
    # input_file = None → auto-detect xlsx terbaru di output_dir saat runtime
    "input_file":        None,
    "output_dir":        "output",
    "scratch_dir":       "scratch",
    "sector_cache_file": str(settings.sector_cache_path),

    # ── Static Filter
    "min_close_price":        100,        # Rp — buang penny stocks
    # ── DER Cap per Sektor (menggantikan max_der flat)
    "max_der_by_sector": {
        "bank":             8.0,   # Bank: DER tinggi adalah norma (leverage perbankan)
        "finance_nonbank":  5.0,   # Multifinance: leverage bisnis
        "infrastructure":   3.0,   # Infrastruktur & BUMN: project financing
        "property":         2.5,   # Properti: development financing
        "industrials":      2.0,   # Industri: capex heavy
        "transport":        2.0,   # Transportasi: fleet financing
        "consumer_staples": 1.5,   # Konsumer primer: moderat
        "consumer_disc":    1.5,   # Konsumer non-primer: moderat
        "energy":           1.5,   # Energi & mining: moderat
        "basic_materials":  1.5,   # Bahan baku: moderat
        "healthcare":       1.0,   # Healthcare: seharusnya rendah
        "tech":             1.0,   # Tech: seharusnya asset-light
        "default":          1.5,   # Fallback untuk sektor tidak dikenal
    },
    "max_pbv_hard":           6.0,        # PBV ceiling absolut
    "pbv_sector_pctile":      0.80,       # Buang top 20% PBV per sektor
    "min_roe":                0.10,       # ROE minimum TTM (10%)
    "min_piotroski":          4,          # [NEW v3.0] Piotroski F-Score minimum
    "min_altman_z":           1.1,        # [NEW v3.0] Altman Z > 1.1 (bukan distress zone)
    "exclude_pemantauan":     True,       # [NEW v3.0] Exclude PEMANTAUAN KHUSUS
    # Trend Filter — harga harus di atas SMA50 saat entry
    "min_price_vs_sma50":     1.0,        # price >= SMA50 (1.0 = tepat di SMA50, boleh set 0.98 untuk toleransi)
    # Trend Filter — harga harus di atas EMA20 saat entry
    "min_price_vs_ema20":     1.0,        # price >= EMA20 (1.0 = tepat di EMA20, boleh set 0.98 untuk toleransi)
    # Relative Strength vs IHSG
    "min_rs_vs_ihsg_1m":      0.0,        # return 1 bulan saham >= return IHSG 1 bulan (outperform atau minimal setara)

    # ── Graham Number (IHSG-calibrated)
    "graham_k":               18.2,
    "graham_bear_eps":        0.85,
    "graham_bull_eps":        1.15,
    # ── Graham FV Sanity Cap
    "graham_fv_cap_multiplier": 5.0,      # FV maksimal 5x current price

    # ── yfinance (HANYA untuk teknikal OHLCV)
    "yf_period":              "120d",
    "yf_retries":             3,
    "yf_retry_delay":         5,

    # ── Liquidity Gate
    "min_adt_20d":            5_000_000_000,
    "min_bars":               60,

    # ── Volume Filter
    # Volume Surge Scoring Tiers (masuk ke scoring, bukan sekadar gate)
    "vol_surge_tier1":        2.0,        # volume >= 2x rata-rata 20d -> 100% weight_momentum_vol
    "vol_surge_tier2":        1.5,        # volume 1.5–2x          -> 70%
    "vol_surge_tier3":        1.1,        # volume 1.1–1.5x        -> 40%
                                             # volume <1.1x           -> 10%

    # ── Suspended/FCA Heuristic
    "max_zero_vol_days":      3,

    # ── RSI Scoring
    "rsi_hard_reject":        80,
    "rsi_accum_lo":           45,
    "rsi_accum_hi":           55,
    "rsi_strong_hi":          70,

    # ── Stop Loss
    "stop_atr_from_sma20":    1.0,
    "stop_atr_from_price":    2.5,
    "stop_hard_floor_pct":    0.88,

    # ── Score Weights (total = 100)
    "weight_valuation":       20,
    "weight_profitability":   10,
    "weight_momentum_rsi":    25,
    "weight_momentum_vol":    25,
    "weight_price_momentum":  20,

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
    # Oversold (<45)    -> 40%  (potensi reversal, menarik tapi butuh konfirmasi)
    # Akumulasi (45-55) -> 100% (sweet spot entry swing)
    # Uptrend (55-70)   -> 80%  (momentum kuat, masih oke)
    # Overbought (>70)  -> 30%  (hard-reject sudah >80, tapi 70-80 tetap lemah)
    "rsi_weight_oversold":    0.40,
    "rsi_weight_accum":       1.00,
    "rsi_weight_uptrend":     0.80,
    "rsi_weight_overbought":  0.30,

    # ── Price Momentum Scoring (v3.2 — swing trade alignment)
    # Return 1 bulan saham vs IHSG sebagai proxy demand institusional
    "price_mom_period_days":  22,         # ~1 bulan trading
    "price_mom_tier1":        0.10,       # return >= +10% dalam 1 bulan -> 100% weight_price_momentum
    "price_mom_tier2":        0.05,       # return +5% s/d +10%         -> 70%
    "price_mom_tier3":        0.00,       # return flat s/d +5%         -> 40%
                                             # return negatif              -> 0% (hard zero, bukan reject)

    # ── Piotroski Score Adjustment (v3.1 — integrasi ke composite score)
    # F-Score >=7 (strong) -> bonus; F-Score <=5 (marginal) -> penalty
    "piotroski_strong_bonus":  +5,
    "piotroski_weak_penalty":  -5,
    "piotroski_strong_min":     7,
    "piotroski_weak_max":       5,

    # ── Penalties & Bonuses
    "over_extended_penalty":  -15,
    "fresh_breakout_bonus":   +15,

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
NAME_SECTOR_KEYWORDS: list[tuple[list[str], str]] = [
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

__all__ = [
    "CONFIG",
    "NAME_SECTOR_KEYWORDS",
    "SECTOR_PBV_BENCHMARK",
    "TICKER_SECTOR_HARDCODE",
]
