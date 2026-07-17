"""Pipeline orchestration and scoring stages for the IHSG quantitative filter."""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from core.fundamental_factors import (
    calculate_ocf_price_ratio,
    calculate_profitability_score,
)
from core.execution_regime import EXECUTION_REGIMES
from core.quant_filter.config import (
    FINANCIAL_SECTORS,
    FREE_FLOAT_ESTIMATES,
    FREE_FLOAT_MANIPULATION_THRESHOLD,
    NAME_SECTOR_KEYWORDS,
    SECTOR_MEDIAN_PE,
    SECTOR_PBV_BENCHMARK,
    TICKER_SECTOR_HARDCODE,
    _find_latest_xlsx,
    assess_xlsx_staleness,
    canonical_screener_mode,
)
from core.quant_filter.reporting import _build_markdown_report
from core.regime import compute_ihsg_snapshot
from core.settings import settings
from utils.exdate_scanner import (
    CRITICAL_WINDOW_DAYS,
    ExDateInfo,
    WARNING_WINDOW_DAYS,
    scan_exdate,
)
from utils.logger_config import logger
from utils.market_snapshot import (
    SNAPSHOT_AUTO_ADJUST,
    SNAPSHOT_INTERVAL,
    MarketSnapshot,
    build_market_snapshots,
    candidate_snapshot_provenance,
    persist_market_snapshots,
    resample_daily_to_weekly,
    snapshot_window,
    snapshots_to_multiindex,
)
from utils.trade_math import is_lq45_ticker
from utils.dynamic_atr import calculate_dynamic_atr
from utils.technicals import (
    REGIME_ATR_STOP_MULTIPLIER,
    REGIME_ATR_STOP_MULTIPLIER_DEFAULT,
    compute_52w_range_signal,
    compute_atr,
    compute_rsi,
    snap_to_tick,
)
from utils.ticker import InvalidIDXTicker, normalize_idx_ticker, to_yfinance_symbol

try:
    from utils.xlsx_adapter import XlsxDataAdapter

    _HAS_ADAPTER = True
except ImportError:
    _HAS_ADAPTER = False


def _get_yfinance():
    import yfinance as yf

    return yf


def _normalize_workbook_tickers(
    frame: pd.DataFrame,
    *,
    sheet_name: str,
) -> pd.DataFrame:
    """Reject unsafe workbook ticker rows before merge/provider/filesystem use."""
    if "Ticker" not in frame.columns:
        raise ValueError(f"Sheet {sheet_name!r} is missing required Ticker column.")

    normalized_values: list[str | None] = []
    for row_index, raw in frame["Ticker"].items():
        try:
            normalized_values.append(normalize_idx_ticker(raw))
        except InvalidIDXTicker as exc:
            logger.warning(
                f"[TickerValidation] sheet={sheet_name} row={row_index} "
                f"reason_code=invalid_idx_ticker: {exc}"
            )
            normalized_values.append(None)

    cleaned = frame.copy()
    cleaned["Ticker"] = normalized_values
    cleaned = cleaned[cleaned["Ticker"].notna()].copy()
    duplicate_count = int(cleaned["Ticker"].duplicated(keep="first").sum())
    if duplicate_count:
        logger.warning(
            f"[TickerValidation] sheet={sheet_name} "
            f"reason_code=duplicate_idx_ticker count={duplicate_count}"
        )
        cleaned = cleaned.drop_duplicates(subset=["Ticker"], keep="first")
    return cleaned


def _row_float(row: pd.Series | dict, *keys: str, default: float = 0.0) -> float:
    """Read the first present numeric value from a row-like object."""
    for key in keys:
        try:
            value = row.get(key)  # type: ignore[attr-defined]
        except AttributeError:
            value = None
        if value is None or pd.isna(value):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _tier_score(value: float, tier1: float, tier2: float, tier3: float) -> float:
    if value >= tier1:
        return 1.00
    if value >= tier2:
        return 0.70
    if value >= tier3:
        return 0.40
    return 0.10


def _compute_multimethod_mos_pct(row: pd.Series | dict, adapter=None) -> float | None:
    """Multi-method MoS % via FairValueCalculator.

    When adapter is provided and ticker is known, uses adapter.extract_keystats()
    which supplies sector-correct historical multiples, normalised ratios, and SOE
    ticker for the 15% governance discount.  Falls back to inline KeyStats build
    when adapter is unavailable (e.g. tests, non-xlsx mode).
    Returns None when margin_of_safety_pct cannot be computed.
    """
    from services.fair_value_calculator import FairValueCalculator, KeyStats

    try:
        price = _row_float(row, "Close Price", "Current Price")
        if price <= 0:
            return None
        ticker = str(row.get("Ticker", "")) if hasattr(row, "get") else ""
        if adapter is not None and ticker:
            stats = adapter.extract_keystats(ticker, price)
        else:
            _dps_raw = row.get("Dividend (TTM)") if hasattr(row, "get") else None
            dps_val = (
                None
                if (_dps_raw is None or (isinstance(_dps_raw, float) and pd.isna(_dps_raw)))
                else float(_dps_raw)
            )
            _roe = _row_float(row, "Return on Equity (TTM)")
            if _roe > 1.0:
                _roe /= 100.0
            stats = KeyStats(
                ticker=ticker,
                eps_ttm=_row_float(row, "Current EPS (TTM)"),
                book_value_per_share=_row_float(row, "Current Book Value Per Share"),
                roe=_roe,
                dps=dps_val,
                current_price=price,
                operating_cash_flow_ttm=_row_float(
                    row,
                    "Operating Cash Flow (TTM)",
                    "Cash From Operations (TTM)",
                    "Cash From Operations",
                ),
                shares_outstanding=_row_float(row, "Current Share Outstanding"),
            )
        sector = (
            str(row.get("Sector", "default") or "default")
            if hasattr(row, "get")
            else "default"
        )
        calc = FairValueCalculator(stats, sector=sector)
        result = calc.fair_value_weighted()
        mos = result.get("margin_of_safety_pct")
        if mos is None:
            return None
        return mos
    except Exception as e:
        logger.debug(
            "[MultiMoS] %s: %s",
            row.get("Ticker", "?") if hasattr(row, "get") else "?",
            type(e).__name__,
        )
        return None


def _ocf_price_ratio_from_row(row: pd.Series | dict) -> float:
    direct = _row_float(
        row,
        "OCF/Price",
        "OCF_Price_Ratio",
        "Operating Cash Flow Yield",
        default=0.0,
    )
    if direct > 0:
        return direct / 100.0 if direct > 1.0 else direct

    ocf_per_share = _row_float(
        row,
        "Operating Cash Flow Per Share",
        "OCF Per Share",
        "Cash Flow from Operations Per Share",
        default=0.0,
    )
    price = _row_float(row, "Close Price", "Current Price", default=0.0)
    if ocf_per_share > 0 and price > 0:
        return ocf_per_share / price

    ocf = _row_float(
        row,
        "Operating Cash Flow (TTM)",
        "Cash From Operations (TTM)",
        "Cash From Operations",
        "Cash Flow from Operations (TTM)",
        "Cash Flow from Operations",
        "Operating Cash Flow",
        "CFO (TTM)",
        default=0.0,
    )
    shares = _row_float(
        row,
        "Current Share Outstanding",
        "Shares Outstanding",
        "shares_outstanding",
        default=0.0,
    )
    return calculate_ocf_price_ratio(ocf, shares, price)


def _ocf_sector_percentile_tier(percentile: float) -> float:
    """Convert sector-relative OCF/Price percentile into the same 0.10-1.00 tier scale."""
    if percentile > 1.0:
        percentile = percentile / 100.0
    if percentile >= 0.80:
        return 1.00
    if percentile >= 0.60:
        return 0.70
    if percentile >= 0.40:
        return 0.40
    return 0.10


def _compute_sector_ocf_percentiles(frame: pd.DataFrame) -> pd.Series:
    """Rank positive OCF/Price values within each IDX sector; missing/non-positive stays 0."""
    if frame.empty or "OCF_Price_Ratio" not in frame.columns or "Sector" not in frame.columns:
        return pd.Series(0.0, index=frame.index)

    def _rank_positive(series: pd.Series) -> pd.Series:
        positive = series.where(series > 0)
        return positive.rank(pct=True, ascending=True).fillna(0.0)

    return frame.groupby("Sector")["OCF_Price_Ratio"].transform(_rank_positive)


def _profitability_data_from_row(row: pd.Series | dict) -> dict:
    return {
        "rnoa": _row_float(row, "RNOA", "Return on Net Operating Assets", default=0.0),
        "roa": _row_float(
            row,
            "Return on Assets (TTM)",
            "ROA (TTM)",
            "ROA",
            default=0.0,
        ),
        "operating_income": _row_float(
            row,
            "Operating Income (TTM)",
            "Operating Profit (TTM)",
            "EBIT (TTM)",
            default=0.0,
        ),
        "tax_rate": _row_float(row, "Tax Rate", "Effective Tax Rate", default=0.22),
        "average_net_operating_assets": _row_float(
            row,
            "Average Net Operating Assets",
            "Net Operating Assets",
            default=0.0,
        ),
    }


# ── Task 2: Weekly OHLCV + Trend ─────────────────────────────────────────────

def fetch_weekly_data(ticker: str, period: str = "2y") -> "pd.DataFrame | None":
    """Download weekly OHLCV for a single ticker (ticker already has .JK suffix).

    Returns None when data is absent or too short for a 13-week MA.
    """
    try:
        df = _get_yfinance().download(
            ticker,
            period=period,
            interval="1wk",
            auto_adjust=True,
            progress=False,
        )
        if df.empty or len(df) < 13:
            return None
        return df
    except Exception:
        return None


def compute_weekly_trend(weekly_df: "pd.DataFrame | None") -> dict:
    """Classify weekly trend using MA13 / MA26 crossover.

    Returns UPTREND / WEAK_UPTREND / DOWNTREND / INSUFFICIENT_DATA.
    """
    if weekly_df is None or len(weekly_df) < 13:
        return {
            "weekly_trend": "INSUFFICIENT_DATA",
            "weekly_ma13": None,
            "weekly_ma26": None,
            "weekly_above_ma13": None,
        }

    close = weekly_df["Close"].dropna()
    if len(close) < 13:
        return {
            "weekly_trend": "INSUFFICIENT_DATA",
            "weekly_ma13": None,
            "weekly_ma26": None,
            "weekly_above_ma13": None,
        }

    ma13 = float(close.rolling(13).mean().iloc[-1])
    ma26 = float(close.rolling(26).mean().iloc[-1]) if len(close) >= 26 else None
    price = float(close.iloc[-1])
    above_ma13 = price > ma13

    if above_ma13 and (ma26 is None or ma13 > ma26):
        trend = "UPTREND"
    elif above_ma13:  # price > MA13 but MA13 <= MA26: bearish weekly cross
        trend = "WEAK_UPTREND"
    elif ma26 is not None and price > ma26:
        trend = "WEAK_UPTREND"
    else:
        trend = "DOWNTREND"

    return {
        "weekly_trend": trend,
        "weekly_ma13": round(ma13, 2),
        "weekly_ma26": round(ma26, 2) if ma26 is not None else None,
        "weekly_above_ma13": above_ma13,
    }


# ── Task 6: Free Float Check ──────────────────────────────────────────────────

def check_free_float(ticker: str) -> dict:
    """Return free-float estimate and manipulation risk tier for a given ticker.

    float_data_coverage values:
      VERIFIED          — angka ada di FREE_FLOAT_ESTIMATES, sumber teridentifikasi
      PENDING_VERIFICATION — ada di FREE_FLOAT_NEEDS_VERIFICATION, belum terverifikasi
      NO_DATA           — tidak ada di kedua list, treated as UNKNOWN risk
    """
    from core.quant_filter.config import FREE_FLOAT_NEEDS_VERIFICATION
    ff = FREE_FLOAT_ESTIMATES.get(ticker)
    if ff is None:
        coverage = (
            "PENDING_VERIFICATION"
            if ticker in FREE_FLOAT_NEEDS_VERIFICATION
            else "NO_DATA"
        )
        return {
            "free_float_pct": None,
            "manipulation_risk": "UNKNOWN",
            "float_data_coverage": coverage,
        }
    if ff < FREE_FLOAT_MANIPULATION_THRESHOLD:
        risk = "HIGH"
    elif ff < 0.25:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    return {
        "free_float_pct": round(ff, 4),
        "manipulation_risk": risk,
        "float_data_coverage": "VERIFIED",
    }


# ── Task 7: ARA / ARB Risk ────────────────────────────────────────────────────

def compute_ara_arb_risk(
    close: "pd.Series",
    high: "pd.Series",
    low: "pd.Series",
    lookback: int = 5,
) -> dict:
    """Detect near-ARA (overbought run-up) and near-ARB (recent plunge) conditions.

    ARB: -15% flat for all price tiers (Kep-00002/BEI/04-2025, effective Apr 8 2025).
    HIGH threshold -12% = early warning at 80% of ARB limit (5-day intraday window).
    MEDIUM threshold -7% = sustained selling pressure; was the old large-cap ARB tier.
    ARA: +25% (Rp 200-5000), +20% (>Rp 5000).

    arb_lock_risk HIGH → position may be locked on down-day, hard to exit.
    ara_entry_risk HIGH → chasing after a +20% run in 3 days; crowded entry.

    Uses intraday high/low (not close-to-close) so that a stock spiking
    +22% intraday then closing +4% is still correctly flagged ARA-risk HIGH.
    """
    if len(close) < lookback + 1:
        return {
            "arb_lock_risk": "UNKNOWN",
            "ara_entry_risk": "UNKNOWN",
            "ara_arb_note": "Insufficient data",
        }

    baseline_close = float(close.iloc[-lookback - 1])
    if baseline_close <= 0:
        return {
            "arb_lock_risk": "UNKNOWN",
            "ara_entry_risk": "UNKNOWN",
            "ara_arb_note": "Invalid baseline price",
        }

    window_high = float(high.tail(lookback).max())
    window_low = float(low.tail(lookback).min())

    max_gain = (window_high / baseline_close) - 1   # intraday peak vs baseline
    max_drop = (window_low / baseline_close) - 1    # intraday trough vs baseline

    # ARB lock risk — based on intraday LOW
    if max_drop < -0.12:
        arb_risk = "HIGH"
        arb_note = (
            f"Harga sempat menyentuh {max_drop:.1%} dari level {lookback} hari lalu "
            "(intraday low). Risiko ARB lock jika tren turun berlanjut."
        )
    elif max_drop < -0.07:
        arb_risk = "MEDIUM"
        arb_note = "Penurunan intraday signifikan — waspadai ARB lock."
    else:
        arb_risk = "LOW"
        arb_note = ""

    # ARA entry risk — based on intraday HIGH
    if max_gain > 0.20:
        ara_risk = "HIGH"
        ara_note = (
            f"Harga sempat menyentuh +{max_gain:.1%} dari level {lookback} hari lalu "
            "(intraday high). Entry sekarang berisiko masuk di puncak distribusi/ARA."
        )
    elif max_gain > 0.12:
        ara_risk = "MEDIUM"
        ara_note = "Kenaikan intraday cepat — potensi ARA diikuti reversal tajam."
    else:
        ara_risk = "LOW"
        ara_note = ""

    combined_note = " | ".join(filter(None, [arb_note, ara_note]))
    return {
        "arb_lock_risk": arb_risk,
        "ara_entry_risk": ara_risk,
        "ara_arb_note": combined_note or "Within normal range",
    }


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
    *,
    as_of: date | None = None,
    lookback_calendar_days: int = 630,
    min_complete_bars: int = 400,
    snapshot_sink: dict[str, MarketSnapshot] | None = None,
) -> pd.DataFrame:
    """Download and canonicalize one explicit daily snapshot per ticker."""
    _ = period  # compatibility only; provider calls use explicit dates below
    requested_start, requested_end = snapshot_window(
        as_of,
        lookback_calendar_days=lookback_calendar_days,
    )
    for attempt in range(1, retries + 1):
        try:
            logger.info(
                f"yfinance download attempt {attempt}/{retries} ({len(tickers)} ticker)..."
            )
            data = _get_yfinance().download(
                tickers,
                start=requested_start.isoformat(),
                end=(requested_end + timedelta(days=1)).isoformat(),
                interval=SNAPSHOT_INTERVAL,
                group_by="ticker",
                progress=False,
                auto_adjust=SNAPSHOT_AUTO_ADJUST,
                timeout=30,
            )
            if data.empty:
                raise ValueError("yfinance mengembalikan DataFrame kosong.")

            # Paksa MultiIndex untuk single-ticker edge case
            if not isinstance(data.columns, pd.MultiIndex):
                logger.warning("Flat columns dari yfinance — paksa MultiIndex wrapper")
                data = pd.concat({tickers[0]: data}, axis=1)

            snapshots = build_market_snapshots(
                data,
                tickers,
                requested_start=requested_start,
                requested_end=requested_end,
                min_complete_bars=min_complete_bars,
            )
            if snapshot_sink is not None:
                snapshot_sink.clear()
                snapshot_sink.update(snapshots)
            data = snapshots_to_multiindex(snapshots, ready_only=False)
            if data.empty:
                raise ValueError("yfinance tidak menghasilkan complete OHLCV bars.")
            ready_count = sum(snapshot.is_ready for snapshot in snapshots.values())
            logger.info(
                f"Download berhasil. Shape: {data.shape}; "
                f"snapshot_ready={ready_count}/{len(snapshots)}"
            )
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
    regime: str = "NORMAL",
    adapter: "XlsxDataAdapter | None" = None,
    weekly_df: "pd.DataFrame | None" = None,
    gate_counters: "dict[str, int] | None" = None,
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
        if gate_counters is not None:
            gate_counters["suspended_fca"] = gate_counters.get("suspended_fca", 0) + 1
        logger.info(f"[{t}] Excluded: suspek suspended/FCA (volume anomali)")
        return None

    current_px: float = float(close.iloc[-1])
    # DER is already enforced by the static filter in run_pipeline() before any
    # row reaches this function (df["Debt to Equity Ratio (Quarter)"] <=
    # df["Max_DER_Allowed"]) — max_der is kept here only for the "max_der_allowed"
    # display field below, not as a second gate.
    sector_key = str(row.get("Sector", "default") or "default")
    max_der_map = cfg["max_der_by_sector"]
    max_der = float(max_der_map.get(sector_key, max_der_map["default"]))

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
        if str(regime).upper() in ("DEFENSIVE", "HIGH", "BEAR_STRESS"):
            min_price_vs_ema20 = cfg.get(
                "min_price_vs_ema20_defensive",
                cfg.get("min_price_vs_ema20", cfg.get("min_price_vs_sma50", 1.0)),
            )
        else:
            min_price_vs_ema20 = cfg.get(
                "min_price_vs_ema20", cfg.get("min_price_vs_sma50", 1.0)
            )
        if current_px < ema20_latest * min_price_vs_ema20:
            if gate_counters is not None:
                gate_counters["ema20"] = gate_counters.get("ema20", 0) + 1
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
        if gate_counters is not None:
            gate_counters["rs_vs_ihsg"] = gate_counters.get("rs_vs_ihsg", 0) + 1
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
        if gate_counters is not None:
            gate_counters["rsi_hard_reject"] = gate_counters.get("rsi_hard_reject", 0) + 1
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
    # Classic Wilder ATR is always computed and used for the max_atr_pct inclusion
    # gate. GARCH ATR (when enabled) is reserved for stop-loss sizing only: its
    # regime-reactive amplification (capped at 3× classic by _GARCH_CAP_MULTIPLIER)
    # is appropriate for sizing but would mass-reject normal stocks in DEFENSIVE
    # regime through the 5% ceiling if used as the gate signal.
    classic_atr_series = compute_atr(high, low, close)
    if pd.isna(classic_atr_series.iloc[-1]):
        return None
    classic_atr_14: float = float(classic_atr_series.iloc[-1])

    if cfg.get("USE_GARCH_ATR", False):
        atr_14 = calculate_dynamic_atr(
            close,
            period=1,
            use_garch=True,
            fit_window=cfg.get("garch_fit_window", 120),
            model_type=cfg.get("DYNAMIC_ATR_MODEL", "garch"),
        )
        if atr_14 <= 0:
            atr_14 = classic_atr_14
    else:
        atr_14 = classic_atr_14

    # ── Liquidity Gate: ADT 20d ───────────────────────────────────────────────
    adt_20: float = float((close * vol).tail(20).mean())
    if adt_20 < cfg["min_adt_20d"]:
        if gate_counters is not None:
            gate_counters["adt_liquidity"] = gate_counters.get("adt_liquidity", 0) + 1
        logger.debug(f"[{t}] ADT Rp {adt_20:,.0f} < threshold, skip")
        return None
    atr_pct = classic_atr_14 / float(close.iloc[-1]) if float(close.iloc[-1]) > 0 else 0.0
    if atr_pct > cfg.get("max_atr_pct", 0.04):
        if gate_counters is not None:
            gate_counters["atr_pct"] = gate_counters.get("atr_pct", 0) + 1
        logger.debug(f"[{t}] ATR% (classic) {atr_pct:.1%} > max, skip")
        return None

    # ── Volume Confirmation ───────────────────────────────────────────────────
    vol_20d_avg: float = float(vol.iloc[-21:-1].mean()) if len(vol) >= 21 else float(vol.iloc[:-1].mean())
    curr_vol: float = float(vol.iloc[-1])
    vol_surge_ratio: float = curr_vol / vol_20d_avg if vol_20d_avg > 0 else 0.0
    if vol_surge_ratio < cfg.get("min_volume_surge_for_candidate", 0.30):
        if gate_counters is not None:
            gate_counters["volume_surge"] = gate_counters.get("volume_surge", 0) + 1
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
    _piotroski_fail = False
    if piotroski >= cfg["piotroski_strong_min"]:
        total_score += cfg["piotroski_strong_bonus"]
        mom_note.append(f"F-Score Kuat ({piotroski}/9)")
    elif piotroski < cfg["min_piotroski"]:
        total_score += cfg.get("penalty_piotroski_fail", -30)
        mom_note.append(
            f"Penalty: F-Score Buruk ({piotroski}/9) ({cfg.get('penalty_piotroski_fail', -30)})"
        )
        _piotroski_fail = True
    elif piotroski <= cfg["piotroski_weak_max"]:
        total_score += cfg["piotroski_weak_penalty"]
        mom_note.append(f"F-Score Lemah ({piotroski}/9)")

    # [v3.3 FIX] Altman Z-Score Turnaround Penalty (raised from -20)
    altman_z_raw = row.get("Altman Z-Score (Modified)", 0)
    altman_z_val = (
        float(altman_z_raw) if not pd.isna(altman_z_raw) and altman_z_raw else 0.0
    )
    _altman_z_fail = False
    if 0 < altman_z_val < cfg["min_altman_z"]:
        total_score += cfg.get("penalty_altman_z_fail", -40)
        mom_note.append(
            f"Penalty: Altman-Z Distress ({altman_z_val:.2f}) ({cfg.get('penalty_altman_z_fail', -40)})"
        )
        _altman_z_fail = True

    # [v3.3 FIX] ROE Turnaround Penalty (raised from -15)
    roe_raw = row.get("Return on Equity (TTM)", 0)
    roe_val = float(roe_raw) if not pd.isna(roe_raw) and roe_raw else 0.0
    _roe_fail = False
    if roe_val < cfg["roe_penalty_threshold"]:
        total_score += cfg.get("penalty_roe_fail", -30)
        mom_note.append(
            f"Penalty: ROE Buruk ({roe_val * 100:.1f}%) ({cfg.get('penalty_roe_fail', -30)})"
        )
        _roe_fail = True

    # Triple-fail hard reject: all three distress signals firing simultaneously
    # means the stock is financially distressed AND operationally weak — no swing
    # setup justifies the risk regardless of short-term momentum.
    if _altman_z_fail and _piotroski_fail and _roe_fail:
        logger.debug(f"[{t}] Triple-fail hard reject: Altman-Z + Piotroski + ROE all fail")
        return None

    # Proportional MoS penalty: 1pt per 1% overvalued, capped at -20
    # Prefer multi-method FV (PE/PB/DDM/DCF weighted); fall back to Graham if unavailable.
    try:
        _mm = row.get("MultiMethod_MoS_Pct")
        if _mm is not None and pd.isna(_mm):
            _mm = None
        gap_pct = float(_mm) if _mm is not None else float(row.get("Valuation_Gap_Pct", 0) or 0)
        _gap_src = "multi-method" if _mm is not None else "graham"
    except Exception:
        gap_pct, _gap_src = 0.0, "graham"
    if gap_pct < 0.0:
        mos_penalty = max(-20, int(gap_pct))
        total_score += mos_penalty
        mom_note.append(f"Penalty: overvalued {gap_pct:.1f}% [{_gap_src}] ({mos_penalty:+d})")

    # ── Task 6: Free Float Manipulation Penalty ───────────────────────────────
    ff_result = check_free_float(t)
    if ff_result["manipulation_risk"] == "HIGH":
        total_score -= 20
        mom_note.append("Penalty: Float Tipis/Manipulation Risk HIGH (-20)")

    # Cap composite score to 0..100 to keep the scale interpretable
    total_score = max(0.0, min(total_score, 100.0))

    # ── Stop Loss (ATR-based + BEI tick size) ─────────────────────────────────
    # Regime-scaled ATR multiplier — same constant debate_chamber's authoritative
    # trade envelope uses, so the screener's preview matches what actually trades.
    stop_atr_mult = REGIME_ATR_STOP_MULTIPLIER.get(
        str(regime).upper(), REGIME_ATR_STOP_MULTIPLIER_DEFAULT
    )
    stop_candidate_2 = current_px - (stop_atr_mult * atr_14)
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

    # ── Task 7: ARA/ARB Risk ──────────────────────────────────────────────────
    ara_arb = compute_ara_arb_risk(df_t["Close"], df_t["High"], df_t["Low"])

    # ── Task 2: Weekly Trend ──────────────────────────────────────────────────
    weekly_trend_data = compute_weekly_trend(weekly_df)

    # ── P3.4: 52-Week Range Signal ────────────────────────────────────────────
    range_52w_signal: str | None = None
    if weekly_df is None:
        range_52w_status = "no_weekly_data"
    elif len(weekly_df) < 4:
        range_52w_status = "insufficient_weekly_bars"
    else:
        try:
            recent_52w = weekly_df.tail(52)
            high_52w = float(recent_52w["High"].max())
            low_52w = float(recent_52w["Low"].min())
            range_52w_signal = compute_52w_range_signal(current_px, high_52w, low_52w)
            range_52w_status = "ok"
        except Exception as exc:
            range_52w_status = "calculation_failed"
            logger.warning(
                f"[52W] {t}: range signal calculation failed "
                f"exception_type={type(exc).__name__}: {exc}"
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
        "graham_low_roe_capped": bool(row.get("graham_low_roe_capped", False)),
        "Valuation Gap (%)": row["Valuation_Gap_Pct"],
        "Multi-Method MoS (%)": row.get("MultiMethod_MoS_Pct"),
        "Price to Equity Discount": row.get("Price to Equity Discount (%)", 0),
        "RSI (14)": rsi_latest,
        "SMA 20": sma20_latest,
        "ema20": round(ema20_latest, 2),
        "ma200": round(ma200_value, 2) if ma200_value else None,
        "ma200_context": ma200_context,
        "ATR (14)": atr_14,
        "ROE (TTM)": row["Return on Equity (TTM)"],
        "ROA/RNOA Proxy": round(float(row.get("RNOA_Estimate", 0) or 0), 4),
        "OCF/Price": round(float(row.get("OCF_Price_Ratio", 0) or 0), 4),
        "OCF/Price Sector Percentile": round(
            float(row.get("OCF_Price_Sector_Pctile", 0) or 0) * 100,
            1,
        ),
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
        # Task 6
        "free_float_pct": ff_result["free_float_pct"],
        "manipulation_risk": ff_result["manipulation_risk"],
        # Task 7
        "arb_lock_risk": ara_arb["arb_lock_risk"],
        "ara_entry_risk": ara_arb["ara_entry_risk"],
        "ara_arb_note": ara_arb["ara_arb_note"],
        # Task 2
        "weekly_trend": weekly_trend_data["weekly_trend"],
        "weekly_ma13": weekly_trend_data["weekly_ma13"],
        "weekly_ma26": weekly_trend_data["weekly_ma26"],
        "weekly_above_ma13": weekly_trend_data["weekly_above_ma13"],
        # P3.4
        "range_52w_signal": range_52w_signal,
        "range_52w_status": range_52w_status,
        # Task 21
        "is_lq45": is_lq45_ticker(t),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ── SCORING HELPERS ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

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

    if sector in FINANCIAL_SECTORS:
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

    # Non-finansial: OCF/Price (primary when available) plus Graham/PE blend.
    # OCF/Price is an IDX4-inspired stock characteristic. This scorer does not
    # construct the paper's factor portfolios or estimate factor exposures.
    # Graham/PE remains fallback when OCF is absent.
    ocf_yield = _ocf_price_ratio_from_row(row)
    ocf_tier = (
        _tier_score(
            ocf_yield,
            cfg["ocf_yield_tier1"],
            cfg["ocf_yield_tier2"],
            cfg["ocf_yield_tier3"],
        )
        if ocf_yield > 0
        else None
    )
    ocf_sector_pctile = _row_float(
        row,
        "OCF_Price_Sector_Pctile",
        "OCF/Price Sector Percentile",
        default=0.0,
    )
    if ocf_tier is not None and ocf_sector_pctile > 0:
        sector_tier = _ocf_sector_percentile_tier(ocf_sector_pctile)
        w_abs = cfg.get("value_ocf_absolute_weight", 0.65)
        w_pct = cfg.get("value_ocf_sector_pctile_weight", 0.35)
        total_w = w_abs + w_pct
        ocf_tier = (
            (w_abs * ocf_tier + w_pct * sector_tier) / total_w
        )

    # Graham gap (70%) blended with PE-vs-sector-median gap (30%).
    # If EPS is unavailable or negative, fall back to Graham-only (existing behaviour).
    gap = float(row.get("Valuation_Gap_Pct", 0) or 0)
    if gap >= cfg["val_tier1_gap"]:
        graham_tier = 1.00
    elif gap >= cfg["val_tier2_gap"]:
        graham_tier = 0.70
    elif gap >= cfg["val_tier3_gap"]:
        graham_tier = 0.40
    else:
        graham_tier = 0.10

    eps = float(row.get("Current EPS (TTM)", 0) or 0)
    price = float(row.get("Close Price", 0) or 0)
    if eps > 0 and price > 0:
        current_pe = price / eps
        sector_median_pe = SECTOR_MEDIAN_PE.get(sector, SECTOR_MEDIAN_PE["default"])
        pe_gap_pct = max(0.0, (sector_median_pe - current_pe) / sector_median_pe * 100)
        if pe_gap_pct >= cfg["val_tier1_gap"]:
            pe_tier = 1.00
        elif pe_gap_pct >= cfg["val_tier2_gap"]:
            pe_tier = 0.70
        elif pe_gap_pct >= cfg["val_tier3_gap"]:
            pe_tier = 0.40
        else:
            pe_tier = 0.10
        graham_pe_tier = 0.70 * graham_tier + 0.30 * pe_tier
        if ocf_tier is not None:
            return w * (
                cfg["value_ocf_weight"] * ocf_tier
                + cfg["value_graham_pe_weight"] * graham_pe_tier
            )
        return w * graham_pe_tier

    if ocf_tier is not None:
        return w * (
            cfg["value_ocf_weight"] * ocf_tier
            + cfg["value_graham_pe_weight"] * graham_tier
        )
    return w * graham_tier


# [v3.1 FIX] Absolute threshold-based Prof_Score — tidak lagi rank(pct=True).
def _compute_prof_score(roe: float | pd.Series | dict, cfg: dict) -> float:
    w = cfg["weight_profitability"]
    if isinstance(roe, (pd.Series, dict)):
        row = roe
        roe_value = _row_float(row, "Return on Equity (TTM)", "ROE (TTM)", "ROE")
        if roe_value > 1.0:
            roe_value = roe_value / 100.0
        if pd.isna(roe_value) or roe_value <= 0:
            roe_score = 0.0
        elif roe_value >= cfg["prof_roe_tier1"]:
            roe_score = 1.00
        elif roe_value >= cfg["prof_roe_tier2"]:
            roe_score = 0.70
        else:
            roe_score = 0.40

        rnoa_score = calculate_profitability_score(_profitability_data_from_row(row))
        if rnoa_score > 0:
            return w * (
                cfg["profitability_rnoa_weight"] * rnoa_score
                + cfg["profitability_roe_weight"] * roe_score
            )
        return w * roe_score

    if pd.isna(roe) or roe <= 0:
        return 0.0
    if roe >= cfg["prof_roe_tier1"]:
        return w * 1.00
    if roe >= cfg["prof_roe_tier2"]:
        return w * 0.70
    return w * 0.40  # 10-15% — di bawah roe_penalty_threshold (10%), sudah kena penalty -30


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
    regime: str = "NORMAL",
    weekly_data: "pd.DataFrame | None" = None,
    gate_counters: "dict[str, int] | None" = None,
    market_snapshot: MarketSnapshot | None = None,
    snapshot_artifact_path: str | None = None,
) -> dict | None:
    ticker = str(row["Ticker"])
    t_yf = f"{ticker}.JK"

    if market_snapshot is not None and not market_snapshot.is_ready:
        _record_price_failure(
            failures,
            ticker,
            stage="price_bars",
            reason=(
                f"snapshot {market_snapshot.status}: "
                + ",".join(market_snapshot.reason_codes)
            ),
            logger=logger,
        )
        return None

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

    if df_t["Close"].isna().all():
        _record_price_failure(
            failures,
            ticker,
            stage="price_data",
            reason="Close is all-NaN (price feed gap)",
            logger=logger,
        )
        return None

    # Relative strength receives the already-computed scalar IHSG return below.
    # Reindexing ticker OHLCV to the benchmark calendar is therefore unnecessary
    # and can silently discard a valid latest ticker bar when the IHSG feed lags.

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

    weekly_df_t: pd.DataFrame | None = None
    if weekly_data is not None:
        try:
            available_weekly = set(weekly_data.columns.get_level_values(0))
            if t_yf in available_weekly:
                sliced = weekly_data[t_yf].dropna(how="all")
                weekly_df_t = sliced if len(sliced) >= 13 else None
        except Exception:
            weekly_df_t = None

    try:
        result = _analyze_ticker(
            row,
            df_t,
            cfg,
            logger,
            ihsg_return_1m=ihsg_return_1m,
            regime=regime,
            adapter=adapter,
            weekly_df=weekly_df_t,
            gate_counters=gate_counters,
        )
        if result is not None and market_snapshot is not None:
            result.update(
                candidate_snapshot_provenance(
                    market_snapshot,
                    artifact_path=snapshot_artifact_path,
                )
            )
        return result
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

    # ── XLSX staleness gate (SEBELUM ingestion, sebelum scoring) ─────────────
    _xlsx_mtime = datetime.fromtimestamp(Path(cfg["input_file"]).stat().st_mtime)
    _staleness = assess_xlsx_staleness(_xlsx_mtime)
    if _staleness["xlsx_staleness"] == "BLOCKED":
        raise RuntimeError(
            f"[Staleness] Pipeline dihentikan: {_staleness['xlsx_staleness_note']} "
            f"Perbarui file XLSX di folder '{cfg.get('output_dir', 'output')}' "
            "lalu jalankan ulang pipeline."
        )
    if _staleness["xlsx_staleness"] == "DEGRADED":
        logger.warning(f"[Staleness] {_staleness['xlsx_staleness_note']}")

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

    df_ks = _normalize_workbook_tickers(df_ks, sheet_name="key-statistics")
    df_prices = _normalize_workbook_tickers(df_prices, sheet_name="stock-prices")
    df_anal = _normalize_workbook_tickers(df_anal, sheet_name="analysis")
    df_idx = _normalize_workbook_tickers(df_idx, sheet_name="idx-stocks")

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
        "Current Share Outstanding",
        "Shares Outstanding",
        "Operating Cash Flow (TTM)",
        "Cash From Operations (TTM)",
        "Cash Flow from Operations (TTM)",
        "Operating Cash Flow Per Share",
        "Return on Assets (TTM)",
        "RNOA",
        "Operating Income (TTM)",
        "Average Net Operating Assets",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    logger.info(f"Total ticker universe: {len(df)}")
    _n_universe = len(df)

    # ── 1b. Exclude PEMANTAUAN KHUSUS di awal ────────────────────────────────
    if cfg.get("exclude_pemantauan", True):
        n_before = len(df)
        df = df[~df["Note"].str.contains("PEMANTAUAN KHUSUS", na=False)].copy()
        logger.info(f"Exclude PEMANTAUAN KHUSUS: {n_before} → {len(df)}")
    _n_after_pemantauan = len(df)

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
    _n_after_static = len(filtered)

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

    # Graham Low-ROE Cap: stocks with ROE < threshold + inflated BVPS produce misleadingly
    # high Graham FV. Cap them to 1.5x price so they don't masquerade as deep value.
    _low_roe_mask = filtered["Return on Equity (TTM)"] < cfg["roe_penalty_threshold"]
    _low_roe_cap = filtered["Close Price"] * cfg["graham_low_roe_cap_mult"]
    _graham_low_roe_capped = _low_roe_mask & (filtered["Graham_Number"] > _low_roe_cap)
    filtered["graham_low_roe_capped"] = _graham_low_roe_capped
    for _, _lr in filtered[_graham_low_roe_capped].iterrows():
        logger.warning(
            f"[Graham-LowROE] {_lr['Ticker']}: FV={_lr['Graham_Number']:,.0f} > "
            f"{cfg['graham_low_roe_cap_mult']}x price={_lr['Close Price']:,.0f} "
            f"(ROE={_lr['Return on Equity (TTM)']:.1%}). Capped ke {_low_roe_cap[_lr.name]:,.0f}."
        )
    filtered["Graham_Number"] = np.where(_graham_low_roe_capped, _low_roe_cap, filtered["Graham_Number"])
    filtered["Graham_Bear"] = np.where(_graham_low_roe_capped, _low_roe_cap * cfg["graham_bear_eps"], filtered["Graham_Bear"])
    filtered["Graham_Bull"] = np.where(_graham_low_roe_capped, _low_roe_cap * cfg["graham_bull_eps"], filtered["Graham_Bull"])

    filtered["Valuation_Gap_Pct"] = (
        (filtered["Graham_Number"] - filtered["Close Price"])
        / filtered["Close Price"]
        * 100
    )
    filtered["MultiMethod_MoS_Pct"] = filtered.apply(
        lambda row: _compute_multimethod_mos_pct(row, adapter), axis=1
    )

    filtered["OCF_Price_Ratio"] = filtered.apply(_ocf_price_ratio_from_row, axis=1)
    _high_ocf = filtered[filtered["OCF_Price_Ratio"] > 0.40]
    if not _high_ocf.empty:
        _hi_tickers = _high_ocf["Ticker"].tolist() if "Ticker" in _high_ocf.columns else list(_high_ocf.index)
        logger.warning(
            "[OCF/Price] %d ticker(s) report OCF/Price > 40%% — possible data anomaly "
            "(stale, aggregated, or mis-scaled OCF): %s",
            len(_hi_tickers),
            _hi_tickers,
        )
    filtered["OCF_Price_Sector_Pctile"] = _compute_sector_ocf_percentiles(filtered)
    filtered["RNOA_Estimate"] = filtered.apply(
        lambda r: _row_float(r, "RNOA", "Return on Net Operating Assets")
        or _row_float(r, "Return on Assets (TTM)", "ROA (TTM)", "ROA"),
        axis=1,
    )
    filtered["Val_Score"] = filtered.apply(lambda r: _compute_val_score(r, cfg), axis=1)

    # ── 5. PROFITABILITY SCORING ──────────────────────────────────────────────
    filtered["Prof_Score"] = filtered.apply(lambda r: _compute_prof_score(r, cfg), axis=1)

    # ── 6. DYNAMIC TECHNICALS VIA YFINANCE ───────────────────────────────────
    valid_tickers = [normalize_idx_ticker(t) for t in filtered["Ticker"].tolist()]
    tickers_yf = [to_yfinance_symbol(t) for t in valid_tickers]

    market_snapshots: dict[str, MarketSnapshot] = {}
    data = download_yf_with_retry(
        tickers_yf,
        period=cfg["yf_period"],
        retries=cfg["yf_retries"],
        delay=cfg["yf_retry_delay"],
        logger=logger,
        lookback_calendar_days=int(cfg.get("yf_lookback_calendar_days", 630)),
        min_complete_bars=int(cfg.get("snapshot_min_complete_bars", 400)),
        snapshot_sink=market_snapshots,
    )
    snapshot_output_root = Path(cfg["output_dir"]).resolve()
    snapshot_paths_on_disk = persist_market_snapshots(
        market_snapshots,
        snapshot_output_root / "market_snapshots",
    )
    snapshot_paths = {
        ticker: path.relative_to(snapshot_output_root).as_posix()
        for ticker, path in snapshot_paths_on_disk.items()
    }

    # ── Task 2: Derive weekly bars from the exact persisted daily snapshot ───
    weekly_data_batch: pd.DataFrame | None = None
    try:
        _weekly_frames = {
            f"{snapshot.ticker}.JK": resample_daily_to_weekly(snapshot.history)
            for snapshot in market_snapshots.values()
            if not snapshot.history.empty
        }
        _raw_weekly = (
            pd.concat(_weekly_frames, axis=1)
            if _weekly_frames
            else pd.DataFrame()
        )
        # yfinance returns flat columns (not MultiIndex) for single-ticker downloads.
        # Without this guard, weekly_data[t_yf] extraction below fails silently and
        # weekly_trend becomes INSUFFICIENT_DATA for every single-ticker run.
        if not _raw_weekly.empty and not isinstance(_raw_weekly.columns, pd.MultiIndex):
            logger.warning("[Weekly] Flat columns dari yfinance — paksa MultiIndex wrapper")
            _raw_weekly = pd.concat({tickers_yf[0]: _raw_weekly}, axis=1)
        weekly_data_batch = None if _raw_weekly.empty else _raw_weekly
        if weekly_data_batch is not None:
            logger.info(
                f"[Weekly] Batch weekly data built from daily snapshots "
                f"for {len(tickers_yf)} tickers"
            )
    except Exception as _e:
        logger.warning(f"[Weekly] Daily-to-weekly resample failed, skipping: {_e}")

    ihsg_close = None
    ihsg_return_1m: float = 0.0
    try:
        # Wider lookback than cfg["yf_period"] (which only needs to cover the
        # per-ticker technicals) — compute_ihsg_snapshot() needs ~200 trading days
        # for its MA200 defensive check. Only this single-symbol IHSG download is
        # widened; per-ticker downloads above are untouched.
        ihsg_data = _get_yfinance().download(
            "^JKSE",
            period=cfg.get("ihsg_regime_period", "320d"),
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

    # ── Regime — self-computed so the screener works standalone (no orchestrator) ──
    raw_execution_regime = str(cfg.get("execution_regime") or "").upper()
    external_execution_regime = bool(raw_execution_regime)
    execution_regime = (
        raw_execution_regime
        if raw_execution_regime in EXECUTION_REGIMES
        else ("UNKNOWN" if raw_execution_regime else "")
    )
    if raw_execution_regime and execution_regime == "UNKNOWN":
        logger.warning(
            f"[Regime] Invalid external execution regime {raw_execution_regime}; "
            f"fail closed to UNKNOWN."
        )
    scoring_regime_profile: str = str(cfg.get("regime") or "").upper()
    if external_execution_regime:
        scoring_regime_profile = {
            "DEFENSIVE": "DEFENSIVE",
            "SIDEWAYS": "HIGH",
            "BULL": "NORMAL",
            "UNKNOWN": "DEFENSIVE",
        }.get(execution_regime, "DEFENSIVE")
    if not scoring_regime_profile:
        try:
            if ihsg_close is None or ihsg_close.empty:
                raise ValueError("IHSG close series tidak tersedia untuk regime.")
            regime_snapshot = compute_ihsg_snapshot(
                pd.DataFrame({"Close": ihsg_close}),
                lookback_days=settings.REGIME_VOLATILITY_LOOKBACK_DAYS,
                high_threshold=settings.REGIME_VOLATILITY_HIGH_THRESHOLD,
                low_threshold=settings.REGIME_VOLATILITY_LOW_THRESHOLD,
                defensive_weekly_drop_threshold=settings.REGIME_DEFENSIVE_WEEKLY_DROP_THRESHOLD,
                recovery_weekly_threshold=settings.REGIME_HIGH_RECOVERY_WEEKLY_THRESHOLD,
            )
            scoring_regime_profile = regime_snapshot.regime
            logger.info(
                "[Regime] Screener self-computed scoring profile: "
                f"{scoring_regime_profile} "
                f"(reasons={','.join(regime_snapshot.reasons) or '-'})"
            )
        except (ValueError, KeyError, IndexError) as e:
            logger.warning(f"Gagal hitung regime, fallback NORMAL: {e}")
            scoring_regime_profile = "NORMAL"

    if not execution_regime:
        execution_regime = {
            "DEFENSIVE": "DEFENSIVE",
            "HIGH": "SIDEWAYS",
            "RECOVERY": "SIDEWAYS",
            "NORMAL": "SIDEWAYS",
            "LOW": "SIDEWAYS",
        }.get(scoring_regime_profile, "UNKNOWN")

    execution_regime_reason = str(
        cfg.get("execution_regime_reason")
        or (
            "standalone_rule_based_caution"
            if not external_execution_regime
            else "unspecified"
        )
    )
    logger.info(
        f"[Regime] Screener authority execution={execution_regime} "
        f"scoring_profile={scoring_regime_profile} reason={execution_regime_reason}"
    )

    gate_counters: dict[str, int] = {}
    results = []
    price_failures: list[dict[str, str]] = []
    for _, row in filtered.iterrows():
        snapshot_ticker = str(row["Ticker"]).strip().upper().removesuffix(".JK")
        result = _safe_analyze_price_candidate(
            row=row,
            data=data,
            cfg=cfg,
            logger=logger,
            ihsg_close=ihsg_close,
            ihsg_return_1m=ihsg_return_1m,
            regime=scoring_regime_profile,
            adapter=adapter,
            failures=price_failures,
            weekly_data=weekly_data_batch,
            gate_counters=gate_counters,
            market_snapshot=market_snapshots.get(snapshot_ticker),
            snapshot_artifact_path=snapshot_paths.get(snapshot_ticker),
        )
        if result:
            results.append(result)
    _log_price_failure_summary(price_failures, logger)
    _n_after_technical = len(results)

    # ── 7. FINALIZE & OUTPUT ──────────────────────────────────────────────────
    final_df = pd.DataFrame(results)

    if scoring_regime_profile == "DEFENSIVE":
        score_floor = cfg.get("score_floor_defensive_regime", 45)
    elif scoring_regime_profile == "HIGH":
        score_floor = cfg.get("score_floor_high_regime", 35)
    else:
        score_floor = cfg.get("score_floor_normal_regime", 35)

    _pre_floor_sorted: pd.DataFrame = pd.DataFrame()

    if final_df.empty:
        logger.warning("Tidak ada ticker yang lolos semua filter.")
        _n_after_floor = 0
        _n_final = 0
    else:
        # Always stamp staleness metadata so JSON schema is consistent across runs.
        final_df["xlsx_staleness"] = _staleness["xlsx_staleness"]
        final_df["xlsx_staleness_note"] = _staleness["xlsx_staleness_note"]
        final_df["execution_regime"] = execution_regime
        final_df["execution_regime_reason"] = execution_regime_reason
        final_df["trend_regime"] = str(cfg.get("trend_regime") or "UNKNOWN")
        final_df["volatility_regime"] = str(
            cfg.get("volatility_regime") or "UNKNOWN"
        ).upper()
        final_df["scoring_regime_profile"] = scoring_regime_profile

        # DEGRADED staleness: kurangi composite score 10 poin sebelum ranking
        if _staleness["xlsx_staleness"] == "DEGRADED":
            final_df["Composite Score"] = (final_df["Composite Score"] - 10).clip(lower=0)
            logger.warning(
                f"[Staleness] Composite Score dikurangi 10 poin untuk semua "
                f"{len(final_df)} kandidat (XLSX {_staleness['xlsx_age_days']} hari)."
            )
        _pre_floor_sorted = final_df.sort_values("Composite Score", ascending=False).head(5).copy()
        before_floor = len(final_df)
        final_df = final_df[final_df["Composite Score"] >= score_floor]
        if len(final_df) < before_floor:
            logger.info(
                f"[ScoreFloor] {before_floor - len(final_df)} kandidat dibuang "
                "(Composite Score < "
                f"{score_floor} untuk scoring profile {scoring_regime_profile})."
            )
        _n_after_floor = len(final_df)
        final_df = final_df.sort_values("Composite Score", ascending=False).head(
            cfg["top_n"]
        )
        _n_final = len(final_df)
        logger.info(f"Top {_n_final} kandidat berhasil disaring.")

    _sep = "-" * 52
    _gate_line = ""
    if gate_counters:
        _gc_sorted = sorted(gate_counters.items(), key=lambda x: -x[1])
        _gate_line = (
            "\n  Gate rejections (technical):\n"
            + "".join(f"    {k:<20}: {v:>4}\n" for k, v in _gc_sorted)
        )
    print(
        f"\n{_sep}\n"
        "  Filter Funnel  "
        f"[execution={execution_regime}; scoring={scoring_regime_profile}]\n"
        f"{_sep}\n"
        f"  Universe (XLSX)            : {_n_universe:>4}\n"
        f"  Setelah exclude PEMANTAUAN : {_n_after_pemantauan:>4}\n"
        f"  Setelah static filter      : {_n_after_static:>4}  (DER, PBV, harga)\n"
        f"  Setelah technical scoring  : {_n_after_technical:>4}  (EMA, RSI, yfinance)\n"
        f"  Setelah score floor        : {_n_after_floor:>4}  (score >= {score_floor})\n"
        f"  Final output               : {_n_final:>4}\n"
        f"{_sep}"
        f"{_gate_line}"
    )

    # Export JSON (untuk orchestrator.py)
    if not final_df.empty:
        json_path = os.path.join(cfg["output_dir"], "top10_candidates.json")
        export_df = final_df.drop(columns=["_exdate_info"], errors="ignore")
        # Tag each record with the screener mode so the orchestrator can reuse a
        # fresh same-mode cache instead of always re-screening.
        export_df["screener_mode"] = canonical_screener_mode(cfg.get("screener_mode"))
        export_df.to_json(json_path, orient="records", indent=2, force_ascii=False)
        logger.info(f"JSON diekspor -> {json_path}")

    # Watchlist JSON: top pre-floor candidates for CLI display when no main output
    if not _pre_floor_sorted.empty:
        wl_path = os.path.join(cfg["output_dir"], "watchlist_candidates.json")
        wl_export = _pre_floor_sorted.drop(columns=["_exdate_info"], errors="ignore").copy()
        wl_export["scoring_regime_profile"] = scoring_regime_profile
        wl_export["score_floor"] = score_floor
        wl_export.to_json(wl_path, orient="records", indent=2, force_ascii=False)
        logger.info(f"Watchlist JSON diekspor -> {wl_path}")

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
