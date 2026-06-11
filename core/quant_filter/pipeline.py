"""Pipeline orchestration and scoring stages for the IHSG quantitative filter."""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from core.quant_filter.config import (
    NAME_SECTOR_KEYWORDS,
    SECTOR_PBV_BENCHMARK,
    TICKER_SECTOR_HARDCODE,
    _find_latest_xlsx,
    canonical_screener_mode,
)
from core.quant_filter.reporting import _build_markdown_report
from utils.exdate_scanner import (
    CRITICAL_WINDOW_DAYS,
    ExDateInfo,
    WARNING_WINDOW_DAYS,
    scan_exdate,
)
from utils.logger_config import logger
from utils.technicals import compute_atr, compute_rsi, snap_to_tick

try:
    from utils.xlsx_adapter import XlsxDataAdapter

    _HAS_ADAPTER = True
except ImportError:
    _HAS_ADAPTER = False


def _get_yfinance():
    import yfinance as yf

    return yf


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
        for keywords, sector in NAME_SECTOR_KEYWORDS:
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
        f"cache={len(tickers) - len(miss_l1)}, "
        f"hardcode={len(miss_l1) - len(miss_l2)}, "
        f"keyword={len(miss_l2) - len(miss_l3)}, "
        f"default={len(miss_l4)}"
    )
    if miss_l4:
        logger.debug(
            f"[Sector] Ticker 'default': {miss_l4[:20]}"
            + ("..." if len(miss_l4) > 20 else "")
        )

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

    Mengembalikan risk tier berbasis jarak hari ke ex-date.
    """

    # Lapis 1: xlsx adapter
    if adapter is not None:
        try:
            info = adapter.get_exdate_info(ticker, current_px)
            if info["source"] == "xlsx":
                return info
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)

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
            days = (parsed_date - today).days
            if days >= 0:
                # Ex-date masih ke depan — hitung risk tier
                if days <= CRITICAL_WINDOW_DAYS:
                    tier = "CRITICAL"
                elif days <= WARNING_WINDOW_DAYS:
                    tier = "WARNING"
                else:
                    tier = "CLEAR"
                div = float(row.get("Dividend (TTM)", 0) or 0)
                return {
                    "has_upcoming_exdate": tier != "CLEAR",
                    "ex_date": str(parsed_date),
                    "days_until_exdate": days,
                    "div_per_share": div or None,
                    "div_yield_pct": round(div / current_px * 100, 2)
                    if div and current_px > 0
                    else None,
                    "risk_tier": tier,
                    "expected_drop_rp": div or None,
                    "source": "xlsx_direct",
                }
            # days < 0 → ex-date sudah lewat → fall through ke lapis 3

    # Lapis 3: yfinance (fallback lambat — hanya jika xlsx tidak ada/kosong/lewat)
    return scan_exdate(ticker, current_price=current_px)


# ══════════════════════════════════════════════════════════════════════════════
# ── HELPERS ───────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════


def setup_logging(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(
        log_dir, f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    logger = logging.getLogger("quant_filter")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
    return logger


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
            logger.info(
                f"yfinance download attempt {attempt}/{retries} ({len(tickers)} ticker)..."
            )
            data = _get_yfinance().download(
                tickers,
                period=period,
                group_by="ticker",
                progress=False,
                auto_adjust=True,
                timeout=30,
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
    ihsg_return_1m: float = 0.0,
    adapter: "XlsxDataAdapter | None" = None,
) -> dict | None:
    """
    Analisis teknikal + fundamental satu ticker.
    Return dict result jika lolos semua filter, None jika tidak lolos.
    """
    t = row["Ticker"]

    close = df_t["Close"].squeeze()
    vol = df_t["Volume"].squeeze()
    high = df_t["High"].squeeze()
    low = df_t["Low"].squeeze()

    # ── Suspended / FCA Board Exclusion ──────────────────────────────────────
    recent_vol = vol.tail(5).sum()
    avg_vol_20d = vol.tail(20).mean()
    if (vol.tail(20) == 0).sum() > cfg["max_zero_vol_days"] or (
        avg_vol_20d > 0 and (recent_vol / avg_vol_20d) < 0.10
    ):
        logger.info(f"[{t}] Excluded: suspek suspended/FCA (volume anomali)")
        return None

    current_px: float = float(close.iloc[-1])
    sector_key = str(row.get("sektor_key", row.get("Sector", "default")) or "default")
    max_der_map = cfg["max_der_by_sector"]
    max_der = float(max_der_map.get(sector_key, max_der_map["default"]))
    der_raw = row.get("Debt to Equity Ratio (Quarter)")
    try:
        der = None if der_raw is None or pd.isna(der_raw) else float(der_raw)
    except (TypeError, ValueError):
        der = None
    if der is not None and der > max_der:
        logger.debug(
            f"[{t}] DER {der:.2f} > sector cap {max_der:.2f} ({sector_key}), skip"
        )
        return None

    price_mom_period = int(cfg.get("price_mom_period_days", 22))

    # Trend Filter: EMA20 is more responsive for swing entries than SMA50.
    ema20 = close.ewm(span=20, adjust=False).mean()
    if pd.isna(ema20.iloc[-1]):
        return None
    ema20_latest: float = float(ema20.iloc[-1])

    ma200 = close.rolling(window=200, min_periods=50).mean()
    ma200_value: float | None = None
    if len(close) >= 50 and not pd.isna(ma200.iloc[-1]):
        ma200_value = float(ma200.iloc[-1])

    if ma200_value is None:
        ma200_context = "INSUFFICIENT_DATA"
    elif current_px > ma200_value * 1.02:
        ma200_context = "ABOVE"
    elif current_px < ma200_value * 0.98:
        ma200_context = "BELOW"
    else:
        prev5 = close.iloc[-6:-1]
        if (
            len(prev5) == 5
            and float(prev5.mean()) < ma200_value
            and current_px > ma200_value
        ):
            ma200_context = "CROSSOVER_RECENT"
        else:
            ma200_context = "ABOVE" if current_px >= ma200_value else "BELOW"

    mode = cfg.get("screener_mode", "momentum")
    if mode == "mean_reversion":
        # Pullback in an intact uptrend: price has dipped below EMA20, but the
        # long-term trend is still up (above MA200) to avoid falling knives.
        if current_px >= ema20_latest:
            logger.debug(f"[{t}] MR: price not below EMA20 (no pullback), skip")
            return None
        if ma200_value is None or current_px < ma200_value * cfg["mr_ma200_floor"]:
            logger.debug(
                f"[{t}] MR: price {current_px:.0f} below MA200 floor "
                f"({cfg['mr_ma200_floor']:.0%} of {ma200_value}), skip"
            )
            return None
    else:
        min_price_vs_ema20 = cfg.get(
            "min_price_vs_ema20", cfg.get("min_price_vs_sma50", 1.0)
        )
        if current_px < ema20_latest * min_price_vs_ema20:
            logger.debug(f"[{t}] Price below EMA20 trend filter, skip")
            return None

    # Relative Strength vs IHSG: reject candidates underperforming the index.
    if len(close) <= price_mom_period:
        return None
    price_return_1m: float = float(
        (current_px / float(close.iloc[-price_mom_period - 1])) - 1
    )
    rs_vs_ihsg: float = price_return_1m - ihsg_return_1m
    if mode == "mean_reversion":
        # Oversold names underperform short-term by definition, so the RS gate
        # does not apply. Reject only freefall (falling-knife) drops.
        if price_return_1m < cfg["mr_max_pullback_1m"]:
            logger.debug(f"[{t}] MR: 1m drop {price_return_1m:.1%} too deep, skip")
            return None
    elif rs_vs_ihsg < cfg["min_rs_vs_ihsg_1m"]:
        logger.debug(f"[{t}] RS vs IHSG {rs_vs_ihsg:.2%} < threshold, skip")
        return None

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

    if mode == "mean_reversion":
        if rsi_latest > cfg["mr_rsi_oversold_max"]:
            logger.debug(
                f"[{t}] MR: RSI {rsi_latest:.1f} > {cfg['mr_rsi_oversold_max']} "
                f"(not oversold), skip"
            )
            return None
    elif rsi_latest > cfg["rsi_hard_reject"]:
        logger.debug(
            f"[{t}] RSI {rsi_latest:.1f} > {cfg['rsi_hard_reject']}, hard reject"
        )
        return None

    # ── SMA 20 — Uptrend Confirmation ────────────────────────────────────────
    sma20 = close.rolling(20).mean()
    if pd.isna(sma20.iloc[-1]):
        return None
    sma20_latest: float = float(sma20.iloc[-1])

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
    atr_pct = atr_14 / float(close.iloc[-1]) if float(close.iloc[-1]) > 0 else 0.0
    if atr_pct > cfg.get("max_atr_pct", 0.04):
        logger.debug(f"[{t}] ATR% {atr_pct:.1%} > max, skip")
        return None

    # ── Volume Confirmation ───────────────────────────────────────────────────
    vol_20d_avg: float = float(vol.tail(20).mean())
    curr_vol: float = float(vol.iloc[-1])
    vol_surge_ratio: float = curr_vol / vol_20d_avg if vol_20d_avg > 0 else 0.0
    if vol_surge_ratio < cfg.get("min_volume_surge_for_candidate", 0.30):
        logger.debug(f"[{t}] Volume anemia: {vol_surge_ratio:.2f}x < min, skip")
        return None

    # ── Momentum Score ────────────────────────────────────────────────────────
    mom_note: list[str] = []

    # [v3.1 FIX] RSI scoring asimetris — swing-trade aware.
    # Oversold (<45) lebih menarik dari overbought (>70) untuk entry swing,
    # karena ada potensi reversal. Sebelumnya keduanya dapat skor yang sama (x0.25).
    rsi_w = cfg["weight_momentum_rsi"]
    if cfg["rsi_accum_lo"] <= rsi_latest <= cfg["rsi_accum_hi"]:
        momentum_rsi_score = rsi_w * cfg["rsi_weight_accum"]
        mom_note.append(f"RSI Akumulasi ({rsi_latest:.1f})")
    elif cfg["rsi_accum_hi"] < rsi_latest <= cfg["rsi_strong_hi"]:
        momentum_rsi_score = rsi_w * cfg["rsi_weight_uptrend"]
        mom_note.append(f"RSI Uptrend Kuat ({rsi_latest:.1f})")
    elif rsi_latest > cfg["rsi_strong_hi"]:
        # Overbought (70-75 range — di atas 75 sudah hard-reject)
        momentum_rsi_score = rsi_w * cfg["rsi_weight_overbought"]
        mom_note.append(f"RSI Overbought ({rsi_latest:.1f})")
    else:
        # RSI < rsi_accum_lo (< 45) — zona oversold, potensi reversal
        momentum_rsi_score = rsi_w * cfg["rsi_weight_oversold"]
        mom_note.append(f"RSI Oversold ({rsi_latest:.1f})")

    # [v3.1 FIX] Volume benchmark berubah dari vol_5d_avg ke vol_20d_avg.
    # vol_5d_avg terlalu pendek — mudah terdistorsi oleh 1-2 hari spike volume,
    # dan tidak konsisten dengan liquidity gate (ADT 20d) dan vol_20d_avg
    # yang sudah dihitung di atas untuk volume confirmation.
    if vol_surge_ratio >= cfg["vol_surge_tier1"]:
        vol_score = 1.00
    elif vol_surge_ratio >= cfg["vol_surge_tier2"]:
        vol_score = 0.70
    elif vol_surge_ratio >= cfg["vol_surge_tier3"]:
        vol_score = 0.40
    else:
        vol_score = 0.10
    momentum_vol_score: float = vol_score * cfg["weight_momentum_vol"]
    mom_note.append(f"Volume Surge {vol_surge_ratio:.2f}x")

    if price_return_1m >= cfg["price_mom_tier1"]:
        price_mom_score = 1.00
    elif price_return_1m >= cfg["price_mom_tier2"]:
        price_mom_score = 0.70
    elif price_return_1m >= cfg["price_mom_tier3"]:
        price_mom_score = 0.40
    else:
        price_mom_score = 0.00
    price_momentum_score: float = price_mom_score * cfg["weight_price_momentum"]
    mom_note.append(f"Price Mom {price_return_1m * 100:.1f}%")

    # ── Composite Score + SMA20 Distance Adjustments ─────────────────────────
    total_score: float = (
        row["Val_Score"]
        + row["Prof_Score"]
        + momentum_rsi_score
        + momentum_vol_score
        + price_momentum_score
    )
    dist_to_sma20_pct: float = (current_px - sma20_latest) / sma20_latest

    if dist_to_sma20_pct > 0.10:
        total_score += cfg["over_extended_penalty"]
        mom_note.append(f"Over-Extended (+{dist_to_sma20_pct * 100:.1f}% SMA20)")
    elif 0.01 <= dist_to_sma20_pct <= 0.05:
        total_score += cfg["fresh_breakout_bonus"]
        mom_note.append(f"Fresh Breakout (+{dist_to_sma20_pct * 100:.1f}% SMA20)")

    if ma200_context == "CROSSOVER_RECENT":
        total_score += 7
        mom_note.append("MA200 Crossover Recent (+7)")
    elif ma200_context == "BELOW":
        total_score -= 7
        mom_note.append("Below MA200 (-7)")

    if mode == "mean_reversion":
        # MR v1 score (override): fundamentals + oversold-RSI only. The momentum,
        # volume, price-momentum and trend adjustments above reward the opposite
        # of a pullback, so they are replaced here. Lower RSI = stronger setup.
        # Fundamental penalties (Piotroski/Altman/ROE/margin) below still apply.
        if rsi_latest <= 30:
            mr_rsi_score = 1.00
        elif rsi_latest <= 35:
            mr_rsi_score = 0.80
        else:
            mr_rsi_score = 0.50
        momentum_rsi_score = rsi_w * mr_rsi_score
        momentum_vol_score = 0.0
        price_momentum_score = 0.0
        total_score = row["Val_Score"] + row["Prof_Score"] + momentum_rsi_score
        mom_note = [
            f"MR Oversold RSI ({rsi_latest:.1f})",
            f"Pullback {price_return_1m * 100:.1f}% (below EMA20)",
        ]

    # [v3.2 FIX] Piotroski F-Score adjustment & Turnaround Penalty.
    piotroski = int(row.get("Piotroski F-Score", 0) or 0)
    if piotroski >= cfg["piotroski_strong_min"]:
        total_score += cfg["piotroski_strong_bonus"]
        mom_note.append(f"F-Score Kuat ({piotroski}/9)")
    elif piotroski < cfg["min_piotroski"]:
        total_score += cfg.get("penalty_piotroski_fail", -15)
        mom_note.append(
            f"Penalty: F-Score Buruk ({piotroski}/9) ({cfg.get('penalty_piotroski_fail', -15)})"
        )
    elif piotroski <= cfg["piotroski_weak_max"]:
        total_score += cfg["piotroski_weak_penalty"]
        mom_note.append(f"F-Score Lemah ({piotroski}/9)")

    # [v3.2 FIX] Altman Z-Score Turnaround Penalty
    altman_z_raw = row.get("Altman Z-Score (Modified)", 0)
    altman_z_val = (
        float(altman_z_raw) if not pd.isna(altman_z_raw) and altman_z_raw else 0.0
    )
    if 0 < altman_z_val < cfg["min_altman_z"]:
        total_score += cfg.get("penalty_altman_z_fail", -20)
        mom_note.append(
            f"Penalty: Altman-Z Distress ({altman_z_val:.2f}) ({cfg.get('penalty_altman_z_fail', -20)})"
        )

    # [v3.2 FIX] ROE Turnaround Penalty
    roe_raw = row.get("Return on Equity (TTM)", 0)
    roe_val = float(roe_raw) if not pd.isna(roe_raw) and roe_raw else 0.0
    if roe_val < cfg["min_roe"]:
        total_score += cfg.get("penalty_roe_fail", -15)
        mom_note.append(
            f"Penalty: ROE Buruk ({roe_val * 100:.1f}%) ({cfg.get('penalty_roe_fail', -15)})"
        )

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
    stop_candidate_2 = current_px - (cfg["stop_atr_from_price"] * atr_14)
    if mode == "mean_reversion":
        # Mean-reversion candidates are below SMA20 by design, so an
        # SMA20-anchored stop would sit ABOVE entry. Anchor below current price.
        stop_loss: float = stop_candidate_2
    else:
        stop_candidate_1 = sma20_latest - (cfg["stop_atr_from_sma20"] * atr_14)
        stop_loss = max(stop_candidate_1, stop_candidate_2)
    stop_loss = max(stop_loss, current_px * cfg["stop_hard_floor_pct"])
    stop_loss = snap_to_tick(stop_loss)

    # ── Sector PBV Context ────────────────────────────────────────────────────
    sector_bench = SECTOR_PBV_BENCHMARK.get(sector_key, SECTOR_PBV_BENCHMARK["default"])
    pbv_current: float = float(row.get("Current Price to Book Value", 0))
    pbv_label = (
        "Murah"
        if pbv_current < sector_bench["fair_lo"]
        else "Wajar"
        if pbv_current <= sector_bench["fair_hi"]
        else "Mahal"
    )

    # ── Quality Flags dari xlsx ───────────────────────────────────────────────
    # Catatan: piotroski sudah didefinisikan di atas (blok Piotroski adjustment)
    altman_z = row.get("Altman Z-Score (Modified)", 0)

    return {
        "Ticker": t,
        "Sektor": row["Sector_Label"],
        "Sektor Key": sector_key,
        "Current Price": current_px,
        "Stop Loss Level": round(stop_loss, 0),
        "Est. Fair Value (Graham)": row["Graham_Number"],
        "Graham_Bear": row["Graham_Bear"],
        "Graham_Bull": row["Graham_Bull"],
        "graham_fv_capped": bool(row.get("graham_fv_capped", False)),
        "Valuation Gap (%)": row["Valuation_Gap_Pct"],
        "Price to Equity Discount": row.get("Price to Equity Discount (%)", 0),
        "RSI (14)": rsi_latest,
        "SMA 20": sma20_latest,
        "ema20": round(ema20_latest, 2),
        "ma200": round(ma200_value, 2) if ma200_value else None,
        "ma200_context": ma200_context,
        "ATR (14)": atr_14,
        "ROE (TTM)": row["Return on Equity (TTM)"],
        "DER (Quarter)": row["Debt to Equity Ratio (Quarter)"],
        "max_der_allowed": max_der,
        "PBV": pbv_current,
        "PBV vs Sektor": pbv_label,
        "PBV Sektor Percentile": round(row["PBV_Sector_Pctile"] * 100, 1),
        "ADT 20d (Rp)": adt_20,
        "Composite Score": total_score,
        "price_return_1m": round(price_return_1m * 100, 2),
        "rs_vs_ihsg_1m": round(rs_vs_ihsg * 100, 2),
        "vol_surge_ratio": round(vol_surge_ratio, 2),
        "price_momentum_score": round(price_momentum_score, 2),
        "Entry Strategy": " | ".join(mom_note),
        "Piotroski F-Score": int(piotroski) if piotroski else 0,
        "Altman Z-Score": float(altman_z) if altman_z else 0.0,
        "ExDate Risk": exdate_info["risk_tier"],
        "ExDate Date": exdate_info.get("ex_date"),
        "ExDate Source": exdate_info.get("source", "unknown"),
        "_exdate_info": exdate_info,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════


def _exception_message(exc: BaseException) -> str:
    text = str(exc).strip()
    if text:
        return text
    if getattr(exc, "args", None):
        return repr(exc.args)
    return type(exc).__name__


def _record_price_failure(
    failures: list[dict[str, str]],
    ticker: str,
    *,
    stage: str,
    reason: str,
    logger: logging.Logger,
    exc: BaseException | None = None,
) -> None:
    record = {"ticker": ticker, "stage": stage, "reason": reason}
    if exc is not None:
        record["failure_type"] = type(exc).__name__
    failures.append(record)

    message = f"[{ticker}] Price path {stage} failed: {reason}; skip."
    if exc is None:
        logger.warning(message)
    else:
        logger.error(message, exc_info=(type(exc), exc, exc.__traceback__))


def _log_price_failure_summary(
    failures: list[dict[str, str]],
    logger: logging.Logger,
) -> None:
    if not failures:
        return
    counts: dict[str, int] = {}
    for failure in failures:
        stage = failure.get("stage", "unknown")
        counts[stage] = counts.get(stage, 0) + 1
    summary = ", ".join(f"{stage}={count}" for stage, count in sorted(counts.items()))
    logger.warning(f"Price path failures: total={len(failures)} | {summary}")


def _safe_analyze_price_candidate(
    *,
    row: pd.Series,
    data: pd.DataFrame,
    cfg: dict,
    logger: logging.Logger,
    ihsg_close: pd.Series | None,
    ihsg_return_1m: float,
    adapter: "XlsxDataAdapter | None",
    failures: list[dict[str, str]],
) -> dict | None:
    ticker = str(row["Ticker"])
    t_yf = f"{ticker}.JK"

    try:
        available_tickers = set(data.columns.get_level_values(0))
    except (AttributeError, IndexError, TypeError) as exc:
        _record_price_failure(
            failures,
            ticker,
            stage="price_columns",
            reason=_exception_message(exc),
            logger=logger,
            exc=exc,
        )
        return None

    if t_yf not in available_tickers:
        _record_price_failure(
            failures,
            ticker,
            stage="price_download",
            reason=f"missing yfinance OHLCV for {t_yf}",
            logger=logger,
        )
        return None

    try:
        df_t = data[t_yf].dropna(how="all")
    except (KeyError, TypeError, AttributeError) as exc:
        _record_price_failure(
            failures,
            ticker,
            stage="price_slice",
            reason=_exception_message(exc),
            logger=logger,
            exc=exc,
        )
        return None

    required_columns = {"Close", "Volume", "High", "Low"}
    missing_columns = sorted(required_columns - set(df_t.columns))
    if missing_columns:
        _record_price_failure(
            failures,
            ticker,
            stage="price_columns",
            reason="missing OHLCV columns: " + ", ".join(missing_columns),
            logger=logger,
        )
        return None

    if ihsg_close is not None and not ihsg_close.empty:
        df_t = df_t.reindex(ihsg_close.index).dropna(how="all")

    min_bars = int(cfg["min_bars"])
    if len(df_t) < min_bars:
        _record_price_failure(
            failures,
            ticker,
            stage="price_bars",
            reason=f"only {len(df_t)} bars (< min_bars={min_bars})",
            logger=logger,
        )
        return None

    try:
        return _analyze_ticker(
            row,
            df_t,
            cfg,
            logger,
            ihsg_return_1m=ihsg_return_1m,
            adapter=adapter,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        _record_price_failure(
            failures,
            ticker,
            stage="ticker_analysis",
            reason=_exception_message(exc),
            logger=logger,
            exc=exc,
        )
        return None


def run_pipeline(cfg: dict) -> pd.DataFrame:
    cfg = dict(cfg)
    logger = setup_logging(cfg["scratch_dir"])
    os.makedirs(cfg["output_dir"], exist_ok=True)
    os.makedirs(cfg["scratch_dir"], exist_ok=True)

    logger.info("=" * 60)
    logger.info("IHSG Quantitative Swing-Trade Scouting Engine v3.2")
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

    df_ks = pd.read_excel(cfg["input_file"], sheet_name="key-statistics")
    df_prices = pd.read_excel(cfg["input_file"], sheet_name="stock-prices")
    df_anal = pd.read_excel(cfg["input_file"], sheet_name="analysis")
    df_idx = pd.read_excel(cfg["input_file"], sheet_name="idx-stocks")

    # Merge semua sheet
    df = (
        df_ks.merge(
            df_prices[["Ticker", "Close Price", "Volume", "High Price", "Low Price"]],
            on="Ticker",
            how="left",
        )
        .merge(
            df_anal[["Ticker", "Price to Equity Discount (%)", "Composite Rank"]],
            on="Ticker",
            how="left",
        )
        .merge(
            df_idx[["Ticker", "Name", "Note"]],
            on="Ticker",
            how="left",
        )
    )

    # Numeric coerce
    for col in [
        "Close Price",
        "Debt to Equity Ratio (Quarter)",
        "Current Price to Book Value",
        "Return on Equity (TTM)",
        "Current EPS (TTM)",
        "Piotroski F-Score",
        "Altman Z-Score (Modified)",
        "Price to Equity Discount (%)",
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
    df["Sector_Label"] = (
        df["Sector"]
        .map({k: v["label"] for k, v in SECTOR_PBV_BENCHMARK.items()})
        .fillna("Lain-lain")
    )
    max_der_map = cfg["max_der_by_sector"]
    df["Max_DER_Allowed"] = df["Sector"].map(max_der_map).fillna(max_der_map["default"])

    # ── 3. STATIC FILTERING ───────────────────────────────────────────────────
    # [v3.2 FIX] Turnaround-friendly filtering
    # Menghapus filter absolut untuk ROE, Piotroski, dan Altman Z.
    # Saham dengan fundamental buruk dibiarkan lolos ke scoring,
    # di mana mereka akan mendapatkan PENALTI BERAT.
    filtered = df[
        (df["Close Price"] > cfg["min_close_price"])
        & (df["Debt to Equity Ratio (Quarter)"] <= df["Max_DER_Allowed"])
        & (df["PBV_Sector_Pctile"] < cfg["pbv_sector_pctile"])
        & (df["Current Price to Book Value"] < cfg["max_pbv_hard"])
    ].copy()

    logger.info(f"Lolos static filter: {len(filtered)} ticker")

    # Distribusi sektor setelah filter
    sector_dist = filtered["Sector"].value_counts()
    logger.info("Distribusi sektor:\n" + sector_dist.to_string())

    # ── 4. VALUATION SCORING — Graham Number (IHSG-calibrated) ───────────────
    bvps = filtered["Current Book Value Per Share"]
    eps = filtered["Current EPS (TTM)"]
    k = cfg["graham_k"]

    valid_graham = (eps > 0) & (bvps > 0)
    filtered["Graham_Number"] = np.where(
        valid_graham, np.sqrt(np.clip(k * eps * bvps, 0, None)), 0
    )
    filtered["Graham_Bear"] = np.where(
        valid_graham,
        np.sqrt(np.clip(k * eps * cfg["graham_bear_eps"] * bvps, 0, None)),
        0,
    )
    filtered["Graham_Bull"] = np.where(
        valid_graham,
        np.sqrt(np.clip(k * eps * cfg["graham_bull_eps"] * bvps, 0, None)),
        0,
    )

    # Graham FV Sanity Cap: prevent extreme EPS/BVPS outliers from dominating ranking.
    fv_cap = filtered["Close Price"] * cfg["graham_fv_cap_multiplier"]
    filtered["graham_fv_capped"] = filtered["Graham_Number"] > fv_cap
    capped_rows = filtered[filtered["graham_fv_capped"]]
    for _, capped_row in capped_rows.iterrows():
        logger.warning(
            f"[Graham] {capped_row['Ticker']}: FV={capped_row['Graham_Number']:,.0f} > "
            f"{cfg['graham_fv_cap_multiplier']}x "
            f"price={capped_row['Close Price']:,.0f}. Capped ke "
            f"{capped_row['Close Price'] * cfg['graham_fv_cap_multiplier']:,.0f}."
        )

    filtered["Graham_Number"] = np.where(
        filtered["graham_fv_capped"], fv_cap, filtered["Graham_Number"]
    )
    filtered["Graham_Bear"] = np.where(
        filtered["graham_fv_capped"],
        fv_cap * cfg["graham_bear_eps"],
        filtered["Graham_Bear"],
    )
    filtered["Graham_Bull"] = np.where(
        filtered["graham_fv_capped"],
        fv_cap * cfg["graham_bull_eps"],
        filtered["Graham_Bull"],
    )

    filtered["Valuation_Gap_Pct"] = (
        (filtered["Graham_Number"] - filtered["Close Price"])
        / filtered["Close Price"]
        * 100
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
            if pbv < fair_lo * 0.70:  # sangat murah vs benchmark sektor
                return w * 1.00
            if pbv < fair_lo * 0.90:
                return w * 0.70
            if pbv <= fair_lo:
                return w * 0.40
            return w * 0.10  # di atas fair_lo = tidak menarik

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
    tickers_yf = [t + ".JK" for t in valid_tickers]

    data = download_yf_with_retry(
        tickers_yf,
        period=cfg["yf_period"],
        retries=cfg["yf_retries"],
        delay=cfg["yf_retry_delay"],
        logger=logger,
    )

    ihsg_close = None
    ihsg_return_1m: float = 0.0
    try:
        ihsg_data = _get_yfinance().download(
            "^JKSE",
            period=cfg["yf_period"],
            progress=False,
            auto_adjust=True,
        )
        if ihsg_data.empty:
            raise ValueError("yfinance mengembalikan data IHSG kosong.")

        if isinstance(ihsg_data.columns, pd.MultiIndex):
            if "Close" in ihsg_data.columns.get_level_values(0):
                ihsg_close = ihsg_data["Close"].squeeze()
            elif "Close" in ihsg_data.columns.get_level_values(-1):
                ihsg_close = ihsg_data.xs("Close", axis=1, level=-1).squeeze()
            else:
                raise KeyError("Kolom Close IHSG tidak ditemukan.")
        else:
            ihsg_close = ihsg_data["Close"].squeeze()

        ihsg_close = ihsg_close.dropna()
        price_mom_period = int(cfg.get("price_mom_period_days", 22))
        if len(ihsg_close) <= price_mom_period:
            raise ValueError("Data IHSG tidak cukup untuk return 1 bulan.")

        ihsg_return_1m = float(
            (float(ihsg_close.iloc[-1]) / float(ihsg_close.iloc[-price_mom_period - 1]))
            - 1
        )
        logger.info(f"IHSG return 1 bulan: {ihsg_return_1m:.2%}")
    except Exception as e:
        logger.warning(f"Gagal ambil return IHSG 1 bulan, fallback 0.0: {e}")
        ihsg_return_1m = 0.0

    results = []
    price_failures: list[dict[str, str]] = []
    for _, row in filtered.iterrows():
        result = _safe_analyze_price_candidate(
            row=row,
            data=data,
            cfg=cfg,
            logger=logger,
            ihsg_close=ihsg_close,
            ihsg_return_1m=ihsg_return_1m,
            adapter=adapter,
            failures=price_failures,
        )
        if result:
            results.append(result)
    _log_price_failure_summary(price_failures, logger)

    # ── 7. FINALIZE & OUTPUT ──────────────────────────────────────────────────
    final_df = pd.DataFrame(results)

    if final_df.empty:
        logger.warning("Tidak ada ticker yang lolos semua filter.")
    else:
        final_df = final_df.sort_values("Composite Score", ascending=False).head(
            cfg["top_n"]
        )
        logger.info(f"Top {len(final_df)} kandidat berhasil disaring.")

    # Export JSON (untuk orchestrator.py)
    if not final_df.empty:
        json_path = os.path.join(cfg["output_dir"], "top10_candidates.json")
        export_df = final_df.drop(columns=["_exdate_info"], errors="ignore")
        # Tag each record with the screener mode so the orchestrator can reuse a
        # fresh same-mode cache instead of always re-screening.
        export_df["screener_mode"] = canonical_screener_mode(cfg.get("screener_mode"))
        export_df.to_json(json_path, orient="records", indent=2, force_ascii=False)
        logger.info(f"JSON diekspor -> {json_path}")

    # Export Markdown Report
    md_content = _build_markdown_report(final_df, cfg)
    report_path = os.path.join(cfg["scratch_dir"], "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info(f"Report -> {report_path}")

    logger.info("PIPELINE SELESAI.")
    return final_df


# ══════════════════════════════════════════════════════════════════════════════
# ── REPORT BUILDER ────────────────────────────────────────────════════════════
# ══════════════════════════════════════════════════════════════════════════════

__all__ = ["run_pipeline"]
