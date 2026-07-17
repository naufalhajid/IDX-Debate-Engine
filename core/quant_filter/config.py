"""Configuration and static mappings for the IHSG quantitative filter."""

import glob
import os
from datetime import datetime
from pathlib import Path

from core.settings import settings

# Calendar days, not trading days — over the weekend a Friday file already
# reads as 2-3 days old, so this fires earlier than "N trading days" would.
# That's the safe direction (warns sooner, never later), kept as-is.
MAX_XLSX_AGE_CALENDAR_DAYS = 3
MAX_XLSX_AGE_HARD_BLOCK_DAYS = 5  # Pipeline dihentikan jika XLSX lebih tua dari ini


def assess_xlsx_staleness(
    xlsx_mtime: datetime,
    now: datetime | None = None,
) -> dict:
    """Evaluasi umur XLSX dan kembalikan staleness tier.

    Returns dict dengan keys:
      xlsx_staleness : "FRESH" | "DEGRADED" | "BLOCKED"
      xlsx_age_days  : int
      xlsx_staleness_note : str
    """
    now = now or datetime.now()
    age_days = (now - xlsx_mtime).days

    if age_days > MAX_XLSX_AGE_HARD_BLOCK_DAYS:
        return {
            "xlsx_staleness": "BLOCKED",
            "xlsx_age_days": age_days,
            "xlsx_staleness_note": (
                f"Data XLSX sudah {age_days} hari, melebihi batas "
                f"{MAX_XLSX_AGE_HARD_BLOCK_DAYS} hari. Refresh data fundamental "
                "sebelum menjalankan pipeline."
            ),
        }
    if age_days > MAX_XLSX_AGE_CALENDAR_DAYS:
        return {
            "xlsx_staleness": "DEGRADED",
            "xlsx_age_days": age_days,
            "xlsx_staleness_note": (
                f"Data XLSX sudah {age_days} hari (batas normal "
                f"{MAX_XLSX_AGE_CALENDAR_DAYS} hari). Composite Score dikurangi 10 "
                "untuk semua kandidat."
            ),
        }
    return {
        "xlsx_staleness": "FRESH",
        "xlsx_age_days": age_days,
        "xlsx_staleness_note": "",
    }


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
    latest = Path(sorted(found, reverse=True)[0])
    return str(latest)


# ══════════════════════════════════════════════════════════════════════════════
# ── KONFIGURASI TERPUSAT ──────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

CONFIG = {
    # v3.4 — IC-recalibrated score weights (RSI 15→8, volume scoring 8→0);
    # v3.3 — PE-to-sector blend + triple-fail reject + volume gate tightened
    "version": "v3.4",
    # ── Path
    # input_file = None → auto-detect xlsx terbaru di output_dir saat runtime
    "input_file": None,
    "output_dir": "output",
    "scratch_dir": "scratch",
    "sector_cache_file": str(settings.sector_cache_path),
    # ── Static Filter
    "min_close_price": 100,  # Rp — buang penny stocks
    # ── DER Cap per Sektor (menggantikan max_der flat)
    "max_der_by_sector": {
        "bank": 8.0,  # Bank: DER tinggi adalah norma (leverage perbankan)
        "finance_nonbank": 5.0,  # Multifinance: leverage bisnis
        "infrastructure": 3.0,  # Infrastruktur & BUMN: project financing
        "property": 2.5,  # Properti: development financing
        "industrials": 2.0,  # Industri: capex heavy
        "transport": 2.0,  # Transportasi: fleet financing
        "consumer_staples": 1.5,  # Konsumer primer: moderat
        "consumer_disc": 1.5,  # Konsumer non-primer: moderat
        "energy": 1.5,  # Energi & mining: moderat
        "basic_materials": 1.5,  # Bahan baku: moderat
        "healthcare": 1.0,  # Healthcare: seharusnya rendah
        "tech": 1.0,  # Tech: seharusnya asset-light
        "default": 1.5,  # Fallback untuk sektor tidak dikenal
    },
    "max_pbv_hard": 6.0,  # PBV ceiling absolut
    "pbv_sector_pctile": 0.80,  # Buang top 20% PBV per sektor
    "roe_penalty_threshold": 0.10,  # Scoring penalty (NOT hard-gate) — since v3.2, ROE below
    # this threshold triggers penalty_roe_fail (-30 pts) in composite score, not exclusion.
    # See _compute_prof_score() for scoring logic.
    "min_piotroski": 4,  # [NEW v3.0] Piotroski F-Score minimum
    "min_altman_z": 1.1,  # [NEW v3.0] Emerging Markets Altman Z''-Score (Modified) > 1.1 (Z'' < 1.1 = distress, 1.1-2.6 = grey, >2.6 = safe)
    "exclude_pemantauan": True,  # [NEW v3.0] Exclude PEMANTAUAN KHUSUS
    # Trend Filter — harga harus di atas SMA50 saat entry
    "min_price_vs_sma50": 1.0,  # price >= SMA50 (1.0 = tepat di SMA50, boleh set 0.98 untuk toleransi)
    # Trend Filter — harga harus di atas EMA20 saat entry
    "min_price_vs_ema20": 1.0,  # price >= EMA20 (1.0 = tepat di EMA20, boleh set 0.98 untuk toleransi)
    # In DEFENSIVE/HIGH/BEAR_STRESS regime, relax EMA20 to allow mild pullbacks.
    # 0.97 = up to 3% below EMA20. Recoveries often start below EMA20 in a broad selloff.
    "min_price_vs_ema20_defensive": 0.97,
    # Relative Strength vs IHSG
    "min_rs_vs_ihsg_1m": 0.0,  # return 1 bulan saham >= return IHSG 1 bulan (outperform atau minimal setara)
    # ── Graham Number (IHSG-calibrated)
    # 18.2 = 13x P/E × 1.4x P/B (IDX universe median).
    # Conservative vs the US standard of 22.5 (15x P/E × 1.5x P/B).
    # Consumer/telecom sectors may warrant a higher k in future calibration.
    "graham_k": 18.2,
    "graham_bear_eps": 0.85,
    "graham_bull_eps": 1.15,
    # ── Graham FV Sanity Cap
    "graham_fv_cap_multiplier": 5.0,  # FV maksimal 5x current price
    # ── Graham Low-ROE Cap: low-quality earners inflate FV via high BVPS despite poor ROE.
    # Fires when Graham_Number > cap_mult × price AND ROE < roe_penalty_threshold.
    "graham_low_roe_cap_mult": 1.5,  # cap FV ke 1.5x price untuk low-ROE stocks
    # ── yfinance (HANYA untuk teknikal OHLCV)
    # Kept as a compatibility label only; the downloader uses explicit dates.
    "yf_period": "630d",
    "yf_lookback_calendar_days": 630,
    "snapshot_min_complete_bars": 400,
    "garch_fit_window": 120,        # GARCH fits only this many recent bars (reactive to current regime)
    "yf_retries": 3,
    "yf_retry_delay": 5,
    # IHSG-only lookback for self-computed market regime (needs ~200 trading days
    # for MA200 — same default core.regime.fetch_ihsg_ohlcv() uses). Does not
    # affect per-ticker downloads above, which stay at yf_period.
    "ihsg_regime_period": "320d",
    # Optional override: set to a RegimeType string ("DEFENSIVE"/"RECOVERY"/"HIGH"/
    # "NORMAL"/"LOW") to skip self-computation, e.g. when the orchestrator already
    # has a fresh snapshot. None (default) means the screener computes its own.
    "regime": None,
    # ── Liquidity Gate
    "min_adt_20d": 10_000_000_000,   # Rp 10B — still 2x original, opens mid-caps
    "max_atr_pct": 0.05,             # 5% — IDX mid-caps naturally more volatile
    "USE_GARCH_ATR": True,           # use GARCH(1,1) dynamic ATR (utils.dynamic_atr); falls back to classic on non-convergence
    "DYNAMIC_ATR_MODEL": "garch",     # set "tgarch" to validate asymmetric-vol sizing
    "min_bars": 60,
    # ── Volume Filter
    # Volume Surge Scoring Tiers (masuk ke scoring, bukan sekadar gate)
    "vol_surge_tier1": 2.0,  # volume >= 2x rata-rata 20d -> 100% weight_momentum_vol
    "vol_surge_tier2": 1.5,  # volume 1.5–2x          -> 70%
    "vol_surge_tier3": 1.1,  # volume 1.1–1.5x        -> 40%
    # volume <1.1x           -> 10%
    "min_volume_surge_for_candidate": 1.00,  # hard gate: at-par volume minimum for swing entry confirmation
    # ── Suspended/FCA Heuristic
    "max_zero_vol_days": 3,
    # ── RSI Scoring
    "rsi_hard_reject": 70,
    "rsi_accum_lo": 45,
    "rsi_accum_hi": 55,
    "rsi_strong_hi": 70,
    # ── Stop Loss
    # ATR multiplier for the price-anchored candidate is now regime-scaled — see
    # utils.technicals.REGIME_ATR_STOP_MULTIPLIER (shared with debate_chamber's
    # authoritative trade envelope) instead of a flat value here.
    "stop_atr_from_sma20": 1.0,
    "stop_hard_floor_pct": 0.88,
    # ── Score Weights (total = 100)
    # v3.4 IC recalibration (2026-07-02) — per-signal Spearman IC on 19 XLSX
    # snapshots (Apr 23 – Jun 26 2026) + yfinance 5d-forward panel; BH FDR
    # across the combined family. Full results:
    # docs/research/screener_signal_ic_2026-07-02.md
    # Harvey/Liu/Zhu bar (mean IC > 0.05, |t| >= 2.57): NO signal passed in
    # this crash-dominated window — it validates nothing, but flags harm:
    #   weight_valuation (48)      → IDX4-inspired characteristic only;
    #                                 not a validated factor-model implementation.
    #                                 in-sample IC ~0 (neutral)
    #   weight_profitability (37)  → VALIDATED (literature: IDX quality premium);
    #                                 in-sample pbv_x_roe IC +0.018 (neutral)
    #   weight_price_momentum (7)  → VALIDATED (literature: EM momentum premium);
    #                                 in-sample -0.048 (crash inversion; kept small)
    #   weight_momentum_rsi (8)    → REDUCED 15→8: tiered score IC +0.018 (t 0.64),
    #                                 raw RSI -0.029 — no alpha evidence either way;
    #                                 small timing weight kept, RSI-70 hard gate stays
    #   weight_momentum_vol (0)    → CUT 8→0: vol_surge IC -0.148 (t -1.47), tiered
    #                                 -0.106 — negative lean + zero IDX evidence.
    #                                 Volume keeps its GATE role (min_volume_surge,
    #                                 ADT liquidity); only the score contribution is
    #                                 removed. Weights are explicit ints now —
    #                                 decoupled from BULL_* debate constants.
    # Re-run scripts/validate_screener_signal_ic.py once the archive spans
    # >= 6 months and >= 2 regimes; restore weights only on an HLZ pass.
    "weight_valuation": 48,
    "weight_profitability": 37,
    "weight_momentum_rsi": 8,
    "weight_momentum_vol": 0,
    "weight_price_momentum": 7,
    "value_ocf_weight": 0.50,
    "value_graham_pe_weight": 0.50,
    "value_ocf_absolute_weight": 0.65,
    "value_ocf_sector_pctile_weight": 0.35,
    "profitability_rnoa_weight": 0.70,
    "profitability_roe_weight": 0.30,
    "ocf_yield_tier1": 0.15,
    "ocf_yield_tier2": 0.08,
    "ocf_yield_tier3": 0.03,
    "rnoa_tier1": 0.20,
    "rnoa_tier2": 0.12,
    # ── Absolute Valuation Scoring Thresholds (v3.1)
    # Val_Score dihitung absolut: gap tiered, bukan rank relatif.
    # Tier 1 (>=50% gap) -> 100% weight_valuation
    # Tier 2 (20-50%)    -> 70%
    # Tier 3 (5-20%)     -> 40%
    # Tier 4 (<5%)       -> 10% (nyaris tidak undervalued)
    "val_tier1_gap": 50.0,
    "val_tier2_gap": 20.0,
    "val_tier3_gap": 5.0,
    # ── Absolute Profitability Scoring Thresholds (v3.1)
    # ROE >=25% -> 100% weight_profitability
    # ROE 15-25% -> 70%
    # ROE 10-15% -> 40%  (min ROE sudah di-gate di static filter)
    "prof_roe_tier1": 0.25,
    "prof_roe_tier2": 0.15,
    # ── RSI Scoring Weights per tier (v3.1 — asimetris, swing-trade aware)
    # Oversold (<45)    -> 40%  (potensi reversal, menarik tapi butuh konfirmasi)
    # Akumulasi (45-55) -> 100% (sweet spot entry swing)
    # Uptrend (55-70)   -> 80%  (momentum kuat, masih oke)
    # Overbought (>70)  -> 30%  (hard-reject sudah >80, tapi 70-80 tetap lemah)
    "rsi_weight_oversold": 0.40,
    "rsi_weight_accum": 1.00,
    "rsi_weight_uptrend": 0.80,
    "rsi_weight_overbought": 0.30,
    # ── Price Momentum Scoring (v3.2 — swing trade alignment)
    # Return 1 bulan saham vs IHSG sebagai proxy demand institusional
    "price_mom_period_days": 22,  # ~1 bulan trading
    "price_mom_tier1": 0.10,  # return >= +10% dalam 1 bulan -> 100% weight_price_momentum
    "price_mom_tier2": 0.05,  # return +5% s/d +10%         -> 70%
    "price_mom_tier3": 0.00,  # return flat s/d +5%         -> 40%
    # return negatif              -> 0% (hard zero, bukan reject)
    # ── Piotroski Score Adjustment (v3.1 — integrasi ke composite score)
    # F-Score >=7 (strong) -> bonus; F-Score <=5 (marginal) -> penalty
    "piotroski_strong_bonus": +5,
    "piotroski_weak_penalty": -5,
    "piotroski_strong_min": 7,
    "piotroski_weak_max": 5,
    # ── Penalties & Bonuses
    "over_extended_penalty": -15,
    "fresh_breakout_bonus": +15,
    # ── Turnaround Momentum Penalties (v3.3 — raised per audit C1-F01)
    # Combined -100 floors composite score to 0 for triple-fail stocks, making it
    # extremely hard to rank in the top-10 except in universally weak markets.
    # Caveat: without an absolute score floor, a 0-score stock can still appear in
    # top_n when the whole universe scores poorly (Option A — hard rejects — would
    # guarantee exclusion but was deferred).
    "penalty_roe_fail": -30,
    "penalty_piotroski_fail": -30,
    "penalty_altman_z_fail": -40,
    # ── Mean-Reversion Mode (v3.3) ────────────────────────────────────────────
    # screener_mode = "momentum" (default, trend-following) | "mean_reversion".
    # Mean-reversion looks for a pullback in an intact uptrend: price has dipped
    # BELOW EMA20 but the long-term trend is still up (above MA200), and RSI is
    # oversold. This surfaces counter-trend reversal candidates in markets where
    # the momentum screener (which requires price > EMA20) finds nothing.
    "screener_mode": "momentum",
    "mr_rsi_oversold_max": 45.0,  # RSI <= this counts as oversold (reversal setup)
    "mr_max_pullback_1m": -0.30,  # reject 1m drops deeper than this (falling knife)
    # Long-term support floor: price must be within 20% below MA200. Raised from
    # 0.90 because a market-wide -15% IHSG correction drags solid stocks well below
    # MA200 without fundamental deterioration — 0.90 gave 0 candidates.
    "mr_ma200_floor": 0.80,
    # ── Score Floor: minimum composite score to appear in final output.
    # Prevents weak stocks from filling top_n slots in thin universes.
    "score_floor_high_regime": 35,       # HIGH regime: lower floor — sell-off widens valuation gaps
    "score_floor_defensive_regime": 45, # DEFENSIVE regime: strict floor retained
    "score_floor_normal_regime": 35,    # NORMAL/RECOVERY/LOW regime
    # ── Output
    "top_n": 10,
}


def canonical_screener_mode(value: str | None) -> str:
    """Canonicalize a screener-mode value to 'momentum' or 'mean_reversion'.

    Accepts 'mean_reversion' or 'mean-reversion'; anything else (None or unknown)
    falls back to 'momentum'. Never raises — for non-CLI code paths. CLI input
    validation and aliases (mom/trend/mr/...) live in app/cli/mode_utils.py.
    """
    return (
        "mean_reversion"
        if str(value or "").replace("-", "_") == "mean_reversion"
        else "momentum"
    )


# ══════════════════════════════════════════════════════════════════════════════
# ── SECTOR MAP — IDX Industry Classification (IDXIC) ─────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

# Sectors excluded from Graham Number valuation (the formula assumes a
# non-financial balance sheet) — scored by PBV-vs-sector-benchmark instead.
# Shared by pipeline.py's _compute_val_score and reporting.py / CLI tables so
# Graham FV/gap is never displayed for a ticker whose score didn't come from it.
FINANCIAL_SECTORS = ("bank", "finance_nonbank")

SECTOR_PBV_BENCHMARK = {
    "energy": {"label": "Energi", "fair_lo": 0.8, "fair_hi": 2.5},
    "basic_materials": {"label": "Barang Baku", "fair_lo": 0.8, "fair_hi": 2.5},
    "industrials": {"label": "Perindustrian", "fair_lo": 0.8, "fair_hi": 2.5},
    "consumer_staples": {"label": "Konsumen Primer", "fair_lo": 1.0, "fair_hi": 3.0},
    "consumer_disc": {"label": "Konsumen Non-Primer", "fair_lo": 0.8, "fair_hi": 2.5},
    "healthcare": {"label": "Kesehatan", "fair_lo": 1.5, "fair_hi": 4.0},
    "bank": {"label": "Perbankan", "fair_lo": 1.5, "fair_hi": 4.0},
    "finance_nonbank": {"label": "Keuangan Non-Bank", "fair_lo": 0.8, "fair_hi": 2.5},
    "property": {"label": "Properti & Real Estate", "fair_lo": 0.5, "fair_hi": 1.5},
    "tech": {"label": "Teknologi", "fair_lo": 1.5, "fair_hi": 6.0},
    "infrastructure": {"label": "Infrastruktur", "fair_lo": 0.8, "fair_hi": 2.5},
    "transport": {"label": "Transportasi & Logistik", "fair_lo": 0.8, "fair_hi": 2.5},
    "default": {"label": "Lain-lain", "fair_lo": 0.8, "fair_hi": 2.5},
}

# Sector median trailing PE — sourced from _SECTOR_MEDIAN_PROFILES_DEFAULT in
# services/fair_value_calculator.py. Kept as a local copy so core/quant_filter
# stays self-contained (no cross-layer import). Update both dicts together.
SECTOR_MEDIAN_PE: dict[str, float] = {
    "bank":             10.0,
    "finance_nonbank":  12.0,
    "energy":            6.0,
    "basic_materials":   8.0,
    "industrials":      14.0,
    "consumer_staples": 20.0,
    "consumer_disc":    16.0,
    "healthcare":       22.0,
    "property":         12.0,
    "tech":             25.0,
    "infrastructure":   15.0,
    "transport":        13.0,
    "default":          14.0,
}

# Hardcode 70+ ticker populer sebagai fallback lapis-2
# (dipakai jika sector_cache.json tidak ada)
TICKER_SECTOR_HARDCODE: dict[str, str] = {
    # Bank
    "BBCA": "bank",
    "BBRI": "bank",
    "BMRI": "bank",
    "BBNI": "bank",
    "BRIS": "bank",
    "BTPS": "bank",
    "BNGA": "bank",
    "BNII": "bank",
    "PNBN": "bank",
    "BDMN": "bank",
    "MEGA": "bank",
    "BJTM": "bank",
    "BJBR": "bank",
    "NISP": "bank",
    "BBTN": "bank",
    "AGRO": "bank",
    "BABP": "bank",
    "ARTO": "bank",
    "SEABANK": "bank",
    # Finance non-bank
    "ADMF": "finance_nonbank",
    "BFIN": "finance_nonbank",
    "WOMF": "finance_nonbank",
    "MFIN": "finance_nonbank",
    "CFIN": "finance_nonbank",
    "PNLF": "finance_nonbank",
    "ASII": "finance_nonbank",  # Astra Financial arm — industrial tapi PBV mix
    "SRTG": "finance_nonbank",
    # Consumer staples
    "UNVR": "consumer_staples",
    "ICBP": "consumer_staples",
    "MYOR": "consumer_staples",
    "INDF": "consumer_staples",
    "SIDO": "consumer_staples",
    "CPIN": "consumer_staples",
    "JPFA": "consumer_staples",
    "GOOD": "consumer_staples",
    "ULTJ": "consumer_staples",
    "AALI": "consumer_staples",
    "LSIP": "consumer_staples",
    "SGRO": "consumer_staples",
    # Consumer discretionary
    "AUTO": "consumer_disc",
    "GJTL": "consumer_disc",
    "SMSM": "consumer_disc",
    "RALS": "consumer_disc",
    "MAPI": "consumer_disc",
    "ACES": "consumer_disc",
    # Healthcare
    "KLBF": "healthcare",
    "HEAL": "healthcare",
    "MIKA": "healthcare",
    "PRDA": "healthcare",
    "DVLA": "healthcare",
    "TSPC": "healthcare",
    "MERK": "healthcare",
    # Mining / Energy
    "ADRO": "energy",
    "BYAN": "energy",
    "PTBA": "energy",
    "ITMG": "energy",
    "HRUM": "energy",
    "DOID": "energy",
    "ELSA": "energy",
    "MEDC": "energy",
    "AKRA": "energy",
    "PGAS": "infrastructure",
    # Basic materials
    "ANTM": "basic_materials",
    "INCO": "basic_materials",
    "MDKA": "basic_materials",
    "TINS": "basic_materials",
    "SMGR": "basic_materials",
    "INTP": "basic_materials",
    "TPIA": "basic_materials",
    # Property
    "BSDE": "property",
    "SMRA": "property",
    "CTRA": "property",
    "PWON": "property",
    "LPKR": "property",
    "DMAS": "property",
    # Tech / Telecom
    "TLKM": "tech",
    "EXCL": "tech",
    "ISAT": "tech",
    "GOTO": "tech",
    "BUKA": "tech",
    "EMTK": "tech",
    # Infrastructure
    "JSMR": "infrastructure",
    "WSKT": "infrastructure",
    "WIKA": "infrastructure",
    # Transport
    "GIAA": "transport",
    "BIRD": "transport",
    "BLUEBIRD": "transport",
    # Industrials
    "MAIN": "industrials",
    "SRIL": "industrials",
    "KINO": "industrials",
}

# Kata kunci di kolom 'Name' untuk inferensi sektor — lapis-3
NAME_SECTOR_KEYWORDS: list[tuple[list[str], str]] = [
    (["bank", "banking", "syariah bank", "bpr"], "bank"),
    (
        ["multifinance", "finance", "leasing", "asuransi", "insurance"],
        "finance_nonbank",
    ),
    (["properti", "property", "real estate", "realty", "realestate"], "property"),
    (
        [
            "farmasi",
            "pharma",
            "hospital",
            "rumah sakit",
            "kesehatan",
            "klinik",
            "alkes",
            "medika",
            "medis",
        ],
        "healthcare",
    ),
    (
        [
            "tambang",
            "mining",
            "coal",
            "batubara",
            "nikel",
            "nickel",
            "gold",
            "emas",
            "timah",
            "tembaga",
            "copper",
        ],
        "energy",
    ),
    (["semen", "cement", "kimia", "chemical", "petrokimia"], "basic_materials"),
    (
        [
            "teknologi",
            "technology",
            "telekomunikasi",
            "telecom",
            "digital",
            "internet",
            "software",
        ],
        "tech",
    ),
    (
        [
            "toll",
            "tol",
            "pelabuhan",
            "port",
            "bandara",
            "airport",
            "listrik",
            "electricity",
            "gas",
            "air minum",
            "pdam",
        ],
        "infrastructure",
    ),
    (
        [
            "logistik",
            "logistic",
            "shipping",
            "pelayaran",
            "penerbangan",
            "airline",
            "trucking",
            "ekspedisi",
        ],
        "transport",
    ),
    (
        [
            "konsumer",
            "consumer",
            "makanan",
            "minuman",
            "food",
            "beverage",
            "agri",
            "perkebunan",
            "plantation",
            "kelapa sawit",
            "palm oil",
            "poultry",
            "peternakan",
        ],
        "consumer_staples",
    ),
    (
        [
            "otomotif",
            "automotive",
            "motor",
            "mobil",
            "tekstil",
            "fashion",
            "retail",
            "ritel",
            "department store",
        ],
        "consumer_disc",
    ),
]


# ── LQ45 Members (per BEI pengumuman Aug 2025) ───────────────────────────────
# Confirmed removals vs Feb 2025 list:
#   BUKA — voluntary delisting from IDX effective Sept 25, 2024
#   WSKT — trading suspended 2024 (Notasi Khusus E), removed at Feb 2025 rebalancing
# Full Aug 2025 composition needs manual verification against official BEI announcement.
# Review setiap rebalancing LQ45 (Februari & Agustus). Gunakan ini sebagai
# referensi untuk FREE_FLOAT_ESTIMATES expansion — jangan duplikasi list ini
# di tempat lain di codebase.
LQ45_MEMBERS: list[str] = [
    "ADRO", "AKRA", "AMRT", "ANTM", "AMMN", "ASII",
    "BBCA", "BBNI", "BBRI", "BMRI", "BREN", "BSDE",
    "CPIN", "DSSA", "EMTK", "EXCL", "GGRM",
    "GOTO", "HMSP", "ICBP", "INCO", "INDF", "INKP",
    "INTP", "ISAT", "ITMG", "JPFA", "KLBF", "MAPI",
    "MBMA", "MDKA", "MEDC", "MIKA", "PGAS", "PGEO",
    "PTBA", "PTPP", "SMGR", "TBIG", "TLKM", "TOWR",
    "UNTR", "UNVR",
]

# ── IDX80 Members (per InvestasiKu update Mei 2026) ──────────────────────────
# IDX80 = 80 most liquid stocks by market cap + liquidity + fundamentals.
# LQ45 ⊂ IDX80. BUKA excluded (delisting Sept 2024). BREN exited May 2026 rebalancing.
# Review setiap rebalancing IDX80 (Februari & Agustus).
IDX80_MEMBERS: list[str] = [
    "AADI", "ACES", "ADMR", "ADRO", "AKRA", "AMMN", "AMRT", "ANTM", "ARTO", "ASII",
    "BBCA", "BBNI", "BBRI", "BBTN", "BKSL", "BMRI", "BRMS", "BRPT", "BSDE", "BUMI",
    "CBDK", "CMRY", "CPIN", "CTRA", "CUAN",
    "DEWA", "DSNG",
    "ELSA", "EMTK", "ENRG", "ERAA", "ESSA", "EXCL",
    "GGRM", "GOTO",
    "HEAL", "HRTA", "HRUM",
    "ICBP", "INCO", "INDF", "INDY", "INKP", "INTP", "ISAT", "ITMG",
    "JPFA", "JSMR",
    "KIJA", "KLBF", "KPIG",
    "MAPA", "MAPI", "MBMA", "MDKA", "MEDC", "MIKA", "MYOR",
    "PANI", "PGAS", "PGEO", "PNLF", "PTBA", "PTRO", "PWON",
    "RAJA", "RATU",
    "SCMA", "SIDO", "SMGR", "SMRA", "SSIA",
    "TAPG", "TLKM", "TOWR", "TPIA",
    "UNTR", "UNVR",
    "WIFI",
]


# ── Task 6: Free Float Estimates ─────────────────────────────────────────────
# Estimates based on public ownership data (KSEI, IDX disclosure, annual reports).
# Sources: IDX company profiles, BEI fact sheets, KSEI kepemilikan data.
# Tickers not in this dict are treated as UNKNOWN manipulation risk.
#
# PENTING: Jangan tambahkan angka yang tidak bisa ditelusuri sumbernya.
# Lebih baik ticker tetap UNKNOWN daripada pakai angka tebakan untuk risk scoring.
# Review dict ini setiap LQ45 rebalancing (Februari & Agustus).
FREE_FLOAT_ESTIMATES: dict[str, float] = {
    # ── 16 original entries (verified, unchanged) ─────────────────────────
    "BBCA": 0.44,   # Djarum Group ~56% → float ~44%
    "BBRI": 0.43,   # Government ~57% → float ~43%
    "BMRI": 0.40,   # Government ~60% → float ~40%
    "TLKM": 0.47,   # Government ~53% → float ~47%
    "ASII": 0.50,   # Jardine Matheson ~50% → float ~50%
    "UNVR": 0.15,   # Unilever PLC ~85% → float ~15%
    "ICBP": 0.20,   # Indofood/Salim ~80% → float ~20%
    "KLBF": 0.40,   # Djoenaedi family ~60% → float ~40%
    "ANTM": 0.35,   # Government ~65% → float ~35%
    "PTBA": 0.35,   # Government ~65% → float ~35%
    "INCO": 0.25,   # Vale Canada ~60%, MIND ID ~20% → float ~25%
    "BREN": 0.05,   # Prajogo Pangestu ~95% → float ~5%
    "DSSA": 0.06,   # Prajogo Pangestu ~94% → float ~6%
    "AMMN": 0.20,   # AP Investment ~82% → float ~20% (post-IPO 2023)
    "MDKA": 0.30,   # Saratoga/Provident ~70% → float ~30%
    "GOTO": 0.35,   # Institutional + public post-IPO → float ~35%
    # ── LQ45 expansion (verified dari IDX/KSEI, update Feb 2025) ─────────
    "ADRO": 0.48,   # Edwin Soeryadjaya & family ~52% → float ~48%
    "AKRA": 0.44,   # Soegiarto family (Haryanto Adikoesoemo) ~56% → float ~44%
    "AMRT": 0.32,   # Djoko Susanto & family (Sumber Alfaria) ~68% → float ~32%
    "BBNI": 0.40,   # Government ~60% → float ~40%
    "BSDE": 0.55,   # Sinarmas Group (Widjaja) ~45% → float ~55%
    "CPIN": 0.44,   # CP Foods Thailand (Jiaravanon family) ~56% → float ~44%
    "EMTK": 0.30,   # Sariaatmadja family ~70% → float ~30%
    "EXCL": 0.34,   # Axiata Group Berhad ~66% → float ~34%
    "GGRM": 0.24,   # Wonowidjojo family ~76% → float ~24%
    "HMSP": 0.08,   # Philip Morris International ~92.5% → float ~8%
    "INDF": 0.50,   # First Pacific/Salim ~50% → float ~50%
    "INTP": 0.49,   # HeidelbergMaterials ~51% → float ~49%
    "ISAT": 0.35,   # Ooredoo Asia ~65% → float ~35%
    "ITMG": 0.35,   # Banpu Public Co Thailand ~65% → float ~35%
    "JPFA": 0.47,   # Japfa Ltd ~53% → float ~47%
    "MEDC": 0.35,   # Panigoro family (Medco) ~65% → float ~35%
    "PGAS": 0.43,   # Government (Pertamina) ~57% → float ~43%
    "PGEO": 0.14,   # Pertamina ~86% → float ~14% (HIGH manipulation risk)
    "PTPP": 0.35,   # Government ~65% → float ~35%
    "SMGR": 0.49,   # Government ~51% → float ~49%
    "TBIG": 0.45,   # Telkom Group ~55% → float ~45%
    "TOWR": 0.49,   # Provident Agro/public ~49% → float ~49%
    "UNTR": 0.40,   # PT Astra International ~60% → float ~40%
    "WSKT": 0.33,   # Government ~67% → float ~33%
    # ── P3.5 resolved (verified dari annual report / prospektus publik) ────
    "INKP": 0.25,   # PT Purinusa Ekapersada (Sinarmas/APP) ~75% → float ~25% (AR 2023)
    "MAPI": 0.50,   # PT Satya Mulia Gema Gemilang ~49.7% → float ~50% (AR 2023)
    "MBMA": 0.32,   # PT Merdeka Copper Gold (MDKA) ~68.2% → float ~32% (prospektus IPO 2023)
    "MIKA": 0.27,   # PT Karya Bersama Anugerah ~73.2% → float ~27% (AR 2023)
    # ── IDX80 expansion (verified dari web search / AR / IndoPremier, update Jun 2026) ─
    # Pemerintah/BUMN
    "BBTN": 0.40,   # Pemerintah RI ~60% → float ~40%
    "ELSA": 0.49,   # PT Pertamina Hulu Energi (PHE) ~51% → float ~49%
    "JSMR": 0.30,   # Pemerintah RI ~70% → float ~30%
    # Prajogo Pangestu group
    "BRPT": 0.29,   # Prajogo Pangestu voting rights ~71% → float ~29% (Jan 2025)
    "CUAN": 0.15,   # Prajogo Pangestu ~84.97% → float ~15% (HIGH manipulation risk)
    "TPIA": 0.09,   # BRPT 34.63% + SCG Chemicals 30.57% + Top Investment 15% + Prajogo 5% ≈ 85% → float ~9%
    # Adaro/Thohir group
    "AADI": 0.19,   # Adaro Strategic 41.09% + Alamtri (ADRO) 15.37% + affiliates 18.85% + Garibaldi 5.83% ≈ 81% → float ~19% (Oct 2025)
    "ADMR": 0.16,   # PT Alamtri Resources Indonesia (ADRO) ~83.84% → float ~16% (Q3 2024)
    # Bakrie group
    "BUMI": 0.40,   # Mach Energy (HK)/Bakrie+Salim 45.78% + affiliates → float ~40% (approx Dec 2025)
    "BRMS": 0.42,   # Emirates Tarian Global 25.1% + BUMI 2.87% → public float 41.52% (Sep 2025)
    "DEWA": 0.60,   # Controlling entity (Nirwan Bakrie UBO) ~11.5%; diverse non-controlling → float ~60% (Jan 2025)
    "ENRG": 0.52,   # PT Shima Global Kapital (Bakrie) ~18.15%; Trimegah Sekuritas (custodian) 23.33%; public 58.52% → float ~52%
    # PIK/Agung Sedayu group
    "BKSL": 0.31,   # PT Sakti Generasi Perdana ~69.47% → float ~31%
    "CBDK": 0.45,   # PT Pantai Indah Kapuk Dua (PANI/Aguan) ~45.9% → float ~45%
    "PANI": 0.10,   # PT Multi Artha Pratama (Agung Sedayu+Salim) ~89.92% → float ~10% (EXTREME manipulation risk)
    # Other property
    "CTRA": 0.47,   # PT Sang Pelopor (Ciputra family) ~53.31% → float ~47%
    "KIJA": 0.48,   # Founders (Setyono Djuandi+Aida Garnida) + Mu'min Ali 21.08% + IDB 11.52% → float ~48% (Nov 2025)
    "PWON": 0.31,   # Pakuwon Arthaniaga (Tedja family) ~68.68% → float ~31%
    "SMRA": 0.58,   # PT Semarop Agung ~36% + Liliawati Rahardjo ~5.72% → float ~58% (Apr 2026)
    "SSIA": 0.52,   # Public (<5% holders) 52.26%; Djarum 10.24% + Persada Capital 7.85% non-controlling (Apr 2026)
    # Consumer/retail
    "ACES": 0.40,   # PT Kawan Lama Sejahtera ~60% → float ~40% (Oct 2024)
    "CMRY": 0.24,   # Bambang Sutantio 53.56% + family 21.63% → float ~24% (Q3 2023)
    "ERAA": 0.45,   # PT Eralink International ~55.17% → float ~45%
    "MAPA": 0.43,   # PT Mitra Adiperkasa (MAPI) ~57% → float ~43% (AR 2023)
    "MYOR": 0.41,   # UNITA BRANINDO 32.93% + MAYORA DHANA UTAMA 26.14% → float ~41% (Q3 2024)
    # Energy/mining
    "DSNG": 0.38,   # Triputra affiliates (Persada 23.24% + Investindo 22.93% + TRI NUR 7.44% + Daya 14.02% + Gochean 10.9%) → float ~38%
    "ESSA": 0.55,   # PT Trinugraha Akraya Sejahtera 23.1% + Chander Laroya ~18.88% → float ~55% (approx 2025)
    "HRTA": 0.29,   # PT Terang Anugerah Abadi ~71% → float ~29% (Jul 2025)
    "HRUM": 0.17,   # PT Karunia Bara Perkasa ~79.79% → float ~17% (Apr 2026)
    "INDY": 0.29,   # Indika Inti 37.78% + Teladan 28.08% + Pandri 5.09% ≈ 71% → float ~29% (Sep 2025)
    "PTRO": 0.28,   # PT Kreasi Jasa Persada (CUAN) 45.31% + PT Caraka Reksa Optima 27.17% → float ~28% (Aug 2025)
    "TAPG": 0.14,   # Triputra affiliates ~85.98% → float ~14% (EXTREME manipulation risk)
    "WIFI": 0.45,   # PT Investasi Sukses Bersama ~54.71% → float ~45%
    # Banking/financial
    "ARTO": 0.37,   # MEI/Jerry Ng 29.81% + WTT/Patrick 11.69% + GoTo 21.4% ≈ 63%; GIC 9.12% + public 27.98% = ~37% investable float
    "PNLF": 0.32,   # PT Paninvest Tbk (Panin Group/Gunawan family) ~67.89% → float ~32% (May 2026)
    # Healthcare
    "HEAL": 0.67,   # Controllers (Yulisar+Binsar+Hasmoro+Meijani) 25.08%; large institutional float ~67% (Sep 2025)
    # Media/entertainment
    "KPIG": 0.50,   # MNC Asia Holding (BHIT) 21.37% + HT Investment 9% → float ~50% (approx Jun 2025)
    "SCMA": 0.39,   # PT Elang Mahkota Teknologi (EMTK) ~61.09% → float ~39% (Dec 2024)
    # Energy infrastructure
    "RAJA": 0.36,   # PT Sentosa Bersama 36.07% + Happy Hapsoro 28.23% → float ~36%
    "RATU": 0.31,   # PT Rukun Raharja (RAJA) ~68.68% → float ~31% (Apr 2026)
    # Pharma/consumer health
    "SIDO": 0.21,   # PT Hotel Candi Baru (Hidayat family) ~77.59% → float ~21% (Sep 2025)
}

# Float < 15% → HIGH manipulation risk; < 25% → MEDIUM; else LOW
FREE_FLOAT_MANIPULATION_THRESHOLD: float = 0.15

# LQ45 members yang data free float-nya belum bisa diverifikasi dari sumber publik.
# BUKA dihapus karena delisting Sept 2024. INKP/MAPI/MBMA/MIKA sudah dipindah ke
# FREE_FLOAT_ESTIMATES di atas (resolved dari AR/prospektus publik).
FREE_FLOAT_NEEDS_VERIFICATION: list[str] = [
]


# ══════════════════════════════════════════════════════════════════════════════
# ── SEKTOR RESOLVER — 4-lapis prioritas ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

__all__ = [
    "CONFIG",
    "FINANCIAL_SECTORS",
    "FREE_FLOAT_ESTIMATES",
    "FREE_FLOAT_MANIPULATION_THRESHOLD",
    "FREE_FLOAT_NEEDS_VERIFICATION",
    "IDX80_MEMBERS",
    "LQ45_MEMBERS",
    "MAX_XLSX_AGE_HARD_BLOCK_DAYS",
    "assess_xlsx_staleness",
    "NAME_SECTOR_KEYWORDS",
    "SECTOR_MEDIAN_PE",
    "SECTOR_PBV_BENCHMARK",
    "TICKER_SECTOR_HARDCODE",
    "canonical_screener_mode",
]
