"""
fair_value_calculator.py — Pure-Python fair value engine untuk saham IHSG.
"""

from __future__ import annotations

import json
import re
import statistics as _stats
from dataclasses import dataclass, field
from datetime import date as _date, datetime as _datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from core.fundamental_factors import (
    calculate_ocf_price_ratio,
    calculate_profitability_score,
)
from utils.logger_config import logger
from utils.trade_math import calculate_rr


SECTOR_CACHE_PATH = Path("output/sector_cache.json")

# ── FV-5: Dynamic Sector Benchmarks ──────────────────────────────────────────
_SECTOR_BENCHMARKS_CACHE_PATH = Path("output/sector_benchmarks.json")
_SECTOR_BENCHMARK_MAX_AGE_DAYS: int = 7

# Representative tickers per IDX sector (12 raw sector keys matching sector_cache.json).
# Tickers are LQ45/IDX80 constituents; failed fetches are silently skipped.
_SECTOR_REPRESENTATIVE_TICKERS: dict[str, list[str]] = {
    "bank":             ["BBCA", "BBRI", "BMRI"],
    "finance_nonbank":  ["BFIN", "ADMF", "MFIN"],
    "energy":           ["ADRO", "PGAS", "MEDC"],
    "basic_materials":  ["ANTM", "INCO", "TINS"],
    "consumer_staples": ["UNVR", "ICBP", "MYOR"],
    "consumer_disc":    ["MAPI", "ACES", "ERAA"],
    "healthcare":       ["KLBF", "SIDO", "MERK"],
    "property":         ["BSDE", "SMRA", "CTRA"],
    "industrials":      ["ASII", "SMGR", "INTP"],
    "tech":             ["EMTK", "DCII", "BELI"],
    "infrastructure":   ["TLKM", "JSMR", "TOWR"],
    "transport":        ["BIRD", "ASSA", "WEHA"],
}

# Static fallback — used when the cache is absent or stale.
# 5 bucket keys = used by FairValueCalculator SECTOR_WEIGHTS.
# 12 raw IDX sector keys = used by build_sector_comparison() raw_sector lookup.
_SECTOR_MEDIAN_PROFILES_DEFAULT: dict[str, dict] = {
    # ── 5 FairValueCalculator bucket keys ─────────────────────────────────────
    "bank":     {"pe": 10.0, "pb": 1.5, "roe": 0.14, "net_margin": 0.25},
    "mining":   {"pe":  7.0, "pb": 1.2, "roe": 0.18, "net_margin": 0.15},
    "consumer": {"pe": 18.0, "pb": 3.0, "roe": 0.20, "net_margin": 0.08},
    "property": {"pe": 12.0, "pb": 0.8, "roe": 0.07, "net_margin": 0.20},
    "default":  {"pe": 14.0, "pb": 1.5, "roe": 0.15, "net_margin": 0.10},
    # ── 12 IDX raw sector keys (sector_cache.json vocabulary) ─────────────────
    "finance_nonbank":  {"pe": 12.0, "pb": 2.0, "roe": 0.12, "net_margin": 0.18},
    "energy":           {"pe":  6.0, "pb": 1.0, "roe": 0.16, "net_margin": 0.12},
    "basic_materials":  {"pe":  8.0, "pb": 1.3, "roe": 0.20, "net_margin": 0.18},
    "consumer_staples": {"pe": 20.0, "pb": 3.5, "roe": 0.22, "net_margin": 0.09},
    "consumer_disc":    {"pe": 16.0, "pb": 2.5, "roe": 0.18, "net_margin": 0.07},
    "healthcare":       {"pe": 22.0, "pb": 3.0, "roe": 0.15, "net_margin": 0.11},
    "industrials":      {"pe": 14.0, "pb": 1.5, "roe": 0.14, "net_margin": 0.08},
    "tech":             {"pe": 25.0, "pb": 3.5, "roe": 0.12, "net_margin": 0.15},
    "infrastructure":   {"pe": 15.0, "pb": 2.0, "roe": 0.13, "net_margin": 0.20},
    "transport":        {"pe": 13.0, "pb": 1.2, "roe": 0.09, "net_margin": 0.06},
}


def _load_dynamic_sector_benchmarks() -> dict[str, dict]:
    """Return sector PE/PB/ROE/margin medians from cache if ≤7 days old."""
    try:
        if _SECTOR_BENCHMARKS_CACHE_PATH.exists():
            raw = json.loads(_SECTOR_BENCHMARKS_CACHE_PATH.read_text(encoding="utf-8"))
            updated_at = _datetime.fromisoformat(raw.get("updated_at", "1970-01-01"))
            age_days = (_datetime.now() - updated_at).days
            if age_days <= _SECTOR_BENCHMARK_MAX_AGE_DAYS:
                benchmarks = raw.get("benchmarks", {})
                if benchmarks:
                    logger.debug(
                        "[FV-5] Loaded dynamic sector benchmarks (age {} days).", age_days
                    )
                    return benchmarks
    except Exception as exc:
        logger.debug("[FV-5] _load_dynamic_sector_benchmarks failed: {}", exc)
    return _SECTOR_MEDIAN_PROFILES_DEFAULT


def refresh_sector_benchmarks(
    fetch_fn: Callable[[str], dict],
    sectors: list[str] | None = None,
) -> dict[str, dict]:
    """
    Fetch keystats for representative tickers per sector, compute medians, and
    write the result to output/sector_benchmarks.json.

    Args:
        fetch_fn: callable(ticker) → raw Stockbit keystats API response dict.
        sectors:  subset of sector keys to refresh; defaults to all.

    Returns:
        dict[sector_key → {"pe", "pb", "roe", "net_margin"}]
    """
    target_sectors = sectors or list(_SECTOR_REPRESENTATIVE_TICKERS.keys())
    benchmarks: dict[str, dict] = {}

    for sector in target_sectors:
        tickers = _SECTOR_REPRESENTATIVE_TICKERS.get(sector, [])
        pe_vals, pb_vals, roe_vals, margin_vals = [], [], [], []
        for ticker in tickers:
            try:
                raw = fetch_fn(ticker)
                s = extract_keystats(raw, ticker=ticker)
                if s.raw_pe_current > 0:
                    pe_vals.append(s.raw_pe_current)
                if s.raw_pb_current > 0:
                    pb_vals.append(s.raw_pb_current)
                if s.roe > 0:
                    roe_vals.append(s.roe)
                if s.net_margin > 0:
                    margin_vals.append(s.net_margin)
            except Exception as exc:
                logger.warning("[FV-5] Skipping {} for sector {}: {}", ticker, sector, exc)

        fallback = _SECTOR_MEDIAN_PROFILES_DEFAULT.get(sector, _SECTOR_MEDIAN_PROFILES_DEFAULT["default"])
        benchmarks[sector] = {
            "pe":         _stats.median(pe_vals)     if pe_vals     else fallback["pe"],
            "pb":         _stats.median(pb_vals)     if pb_vals     else fallback["pb"],
            "roe":        _stats.median(roe_vals)    if roe_vals    else fallback["roe"],
            "net_margin": _stats.median(margin_vals) if margin_vals else fallback["net_margin"],
        }
        logger.info("[FV-5] Sector {} benchmarks: {}", sector, benchmarks[sector])

    try:
        _SECTOR_BENCHMARKS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SECTOR_BENCHMARKS_CACHE_PATH.write_text(
            json.dumps(
                {"updated_at": _datetime.now().isoformat(), "benchmarks": benchmarks},
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("[FV-5] Sector benchmarks cached to {}", _SECTOR_BENCHMARKS_CACHE_PATH)
    except Exception as exc:
        logger.warning("[FV-5] Failed to write sector benchmarks cache: {}", exc)

    return benchmarks


# ── FV-3: SOE Governance Discount ────────────────────────────────────────────
# BUMN/SOE stocks trade at a structural discount vs private peers due to:
# political dividend pressure, slower capital allocation, and governance risk.
# 15% is the market-implied average discount for IDX BUMN vs sector peers.
# ── FV-4: Keystats Staleness Threshold ───────────────────────────────────────
# When EPS/DPS data is older than 30 days, earnings-based methods (PE, DDM)
# become unreliable.  Shift 50% of their weight to P/B, which is more stable.
_STALE_KEYSTATS_DAYS: int = 30
_STALE_EPS_METHODS: frozenset[str] = frozenset({"pe", "ddm", "ev_ebitda"})

_SOE_DISCOUNT_PCT: float = 0.15

_SOE_TICKERS: frozenset[str] = frozenset({
    "BBRI", "BMRI", "BBNI", "BBTN",   # Banking
    "TLKM",                             # Telecoms
    "PGAS",                             # Gas distribution
    "PTBA", "ANTM", "TINS",            # Mining & Resources
    "WIKA", "WSKT", "PTPP", "ADHI",   # Construction
    "SMGR", "SMBR",                    # Cement
    "KAEF", "INAF",                    # Pharma
    "JSMR", "GIAA",                    # Transportation & Infrastructure
    "PPRO",                             # Property
})


def _normalize_ticker_key(ticker: str) -> str:
    """Return the cache key used across IDX ticker payloads."""
    return str(ticker or "").upper().replace(".JK", "").strip()


@lru_cache(maxsize=1)
def _load_sector_cache() -> dict[str, Any]:
    """Load output/sector_cache.json if available, otherwise return an empty map."""
    try:
        with SECTOR_CACHE_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("[FairValue] Failed to load sector cache: {}", exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("[FairValue] sector_cache.json is not a mapping; ignored.")
        return {}

    return {
        _normalize_ticker_key(ticker): payload
        for ticker, payload in raw.items()
        if _normalize_ticker_key(ticker)
    }


def _sector_from_cache(ticker: str) -> str | None:
    payload = _load_sector_cache().get(_normalize_ticker_key(ticker))
    if isinstance(payload, dict):
        sector = str(payload.get("sector") or "").strip().lower()
        return sector or None
    if isinstance(payload, str):
        sector = payload.strip().lower()
        return sector or None
    return None


def _range_pct_for_method_count(method_count: int) -> float | None:
    if method_count >= 3:
        return 0.10
    if method_count == 2:
        return 0.15
    if method_count == 1:
        return 0.25
    return None


def _valuation_verdict_from_range(
    *,
    price: float,
    fair_value_base: float,
    fair_value_low: float,
    fair_value_high: float,
) -> str:
    if price < fair_value_low:
        return "UNDERVALUED"
    if price < fair_value_base * 0.95:
        return "SLIGHTLY_UNDERVALUED"
    if price <= fair_value_base * 1.05:
        return "FAIRLY_VALUED"
    if price <= fair_value_high:
        return "SLIGHTLY_OVERVALUED"
    return "OVERVALUED"


def _capm_cost_of_equity(beta: float = 1.0) -> float:
    """Cost of Equity via CAPM: Ke = SBN10Y + beta × ERP."""
    from core.settings import get_settings  # lazy to avoid circular dep
    from services.macro_refresh import get_live_sbn_10y  # lazy to avoid circular dep
    s = get_settings()
    return get_live_sbn_10y() + beta * s.IDX_ERP


# ---------------------------------------------------------------------------
# Data container — diisi dari response API Stockbit keystats
# ---------------------------------------------------------------------------


@dataclass
class KeyStats:
    """
    Nilai-nilai fundamental yang dibutuhkan untuk kalkulasi.
    Semua field punya default 0.0 / None agar tidak crash saat data parsial.
    """

    ticker: str = ""

    # Income statement
    eps_ttm: float = 0.0  # Earnings Per Share (Trailing Twelve Months)
    eps_forward: float = 0.0  # EPS proyeksi tahun depan (jika tersedia)
    dps: float | None = (
        None  # Dividend Per Share (TTM); None = missing, 0.0 = explicit zero
    )

    # Balance sheet
    book_value_per_share: float = 0.0  # Ekuitas / jumlah saham beredar

    # Profitability
    roe: float = 0.0  # Return on Equity (desimal: 0.22 = 22%)
    net_margin: float = 0.0  # Net Profit Margin (desimal)
    roa: float = 0.0
    rnoa: float = 0.0
    profitability_factor_score: float = 0.0

    # Market
    current_price: float = 0.0
    shares_outstanding: float = 0.0  # lembar saham beredar (dalam unit, bukan miliar)
    operating_cash_flow_ttm: float = 0.0
    ocf_per_share: float = 0.0
    ocf_price_ratio: float = 0.0
    ocf_stability_score: float | None = None

    # Historical P/E dan P/B (rata-rata 3-5 tahun, hardcode per sektor atau ambil dari API)
    # Default ini adalah nilai historis konservatif untuk sektor perbankan IHSG
    historical_pe_avg: float = 18.0  # rata-rata P/E historis 5 tahun
    historical_pb_avg: float = 3.5  # rata-rata P/B historis 5 tahun

    # Cost of equity untuk DDM/Gordon Growth (dalam desimal)
    cost_of_equity: float = field(default_factory=lambda: _capm_cost_of_equity(1.0))
    growth_rate: float = 0.07  # 7% — proyeksi pertumbuhan laba jangka panjang

    # Sumber data mentah (untuk debugging)
    raw_pe_current: float = 0.0
    raw_pb_current: float = 0.0

    # EV/EBITDA current multiple (untuk metode ke-4, mining/energy saja)
    ev_ebitda_current: float | None = None

    # Age of the underlying financial data in days (None = unknown).
    # Populated from the Stockbit API response where a closure date is available.
    keystats_age_days: int | None = None


# ---------------------------------------------------------------------------
# Extractor — parse response JSON dari Stockbit keystats API
# ---------------------------------------------------------------------------


def _parse_stockbit_flat(api_response: dict) -> dict[str, str]:
    """
    Flatten the Stockbit /keystats/ratio/v1/{ticker} response into a simple
    {field_name: raw_value_string} dict.

    Actual API structure (confirmed from live response):
        data.closure_fin_items_results[i]
            .fin_name_results[j]
                .fitem.name   → human-readable field name  (e.g. "Current EPS (TTM)")
                .fitem.value  → raw string value            (e.g. "312.50" or "9.96%")

    This flat dict is then consumed by extract_keystats via _lookup().
    Logging which fields were found makes it easy to add new mappings later.
    """
    flat: dict[str, str] = {}
    try:
        groups = api_response.get("data", {}).get("closure_fin_items_results", [])
        for group in groups:
            for item in group.get("fin_name_results", []):
                fitem = item.get("fitem", {})
                name = fitem.get("name", "").strip()
                value = fitem.get("value", "")
                if name and value not in (None, "", "-", "N/A"):
                    flat[name] = str(value)
    except Exception as e:
        logger.warning("[FairValue] _parse_stockbit_flat failed: {}", e)

    logger.debug("[FairValue] Stockbit flat fields found: {}", list(flat.keys()))
    return flat


def _clean_numeric(raw: str) -> float:
    """
    Convert a raw Stockbit value string to float.
    Handles: "312.50", "9.96%", "Rp 2.530", "1,234.56", "-21.35"
    Returns 0.0 on failure.
    """
    if not raw:
        return 0.0
    # Strip currency prefix and whitespace
    s = re.sub(r"[Rr][Pp]\.?\s*", "", raw).strip()
    # Remove thousand-separators (dot or comma before 3 digits)
    s = re.sub(r"[,.](?=\d{3}(?!\d))", "", s)
    # Remove trailing % sign (caller decides whether to divide by 100)
    s = s.replace("%", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _clean_numeric_or_none(raw: object) -> float | None:
    """Parse a numeric source value while preserving missing values as None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text in {"", "-", "N/A", "n/a", "null", "None"}:
        return None
    s = re.sub(r"[Rr][Pp]\.?\s*", "", text).strip()
    s = re.sub(r"[,.](?=\d{3}(?!\d))", "", s)
    s = s.replace("%", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def extract_keystats(api_response: dict, ticker: str = "") -> KeyStats:
    """
    Ekstrak field yang relevan dari response raw Stockbit keystats API.

    Mendukung DUA struktur API:
      A) closure_fin_items_results (confirmed live format, 2025-2026)
         data.closure_fin_items_results[].fin_name_results[].fitem.{name, value}
      B) Legacy flat key-value (kept as fallback)

    Debug log menampilkan field mana yang berhasil di-parse sehingga mudah
    menambah mapping baru jika Stockbit mengubah nama field.
    """
    stats = KeyStats(ticker=ticker)

    # ── Strategy A: parse by field name (live Stockbit format) ────────────────
    flat = _parse_stockbit_flat(api_response)
    flat_lower = {k.lower(): v for k, v in flat.items()}

    def _lookup_optional(name_patterns: list[str], pct: bool = False) -> float | None:
        """
        Find the first matching name from flat dict.

        Matching order:
          1. Exact match (original key as Stockbit returns it)
          2. Case-insensitive exact match
          3. Case-insensitive partial match (pattern contained in key)
        Robust terhadap variasi nama field antar versi API dan sektor
        (bank vs mining). ROE BBCA pakai "Return on Equity (TTM)"
        (lowercase 'on') — exact match miss, partial match berhasil.

        pct=True: divide by 100 if value > 1 (normalise percent to decimal).
        """
        for pattern in name_patterns:
            # 1. Exact
            val_str = flat.get(pattern)
            if val_str is None:
                # 2. Case-insensitive exact
                val_str = flat_lower.get(pattern.lower())
            if val_str is None:
                # 3. Case-insensitive partial — pattern is substring of a key
                pl = pattern.lower()
                partial_matches = [(k, v) for k, v in flat_lower.items() if pl in k]
                if partial_matches:
                    # Prefer the shortest key (most specific match)
                    val_str = min(partial_matches, key=lambda x: len(x[0]))[1]
            if val_str is not None:
                v = _clean_numeric_or_none(val_str)
                if v is None:
                    return None
                if pct and v > 1.0:
                    v = v / 100.0
                return v
        return None

    def _lookup(name_patterns: list[str], pct: bool = False) -> float:
        """Return a parsed numeric field, defaulting missing non-DPS data to 0.0."""
        value = _lookup_optional(name_patterns, pct=pct)
        return 0.0 if value is None else value

    if flat:
        # ── Per-share data ───────────────────────────────────────────────────
        stats.eps_ttm = _lookup(
            [
                "Current EPS (TTM)",
                "EPS (TTM)",
                "EPS TTM",
                "Earnings Per Share (TTM)",
            ]
        )

        # If EPS is missing but PE and price are available, back-calculate:
        #   EPS = price / PE
        if stats.eps_ttm == 0.0:
            pe_ttm = _lookup(
                [
                    "Current PE Ratio (TTM)",
                    "PE Ratio (TTM)",
                    "Current PE Ratio (Annualised)",
                ]
            )
            if pe_ttm > 0:
                # EPS back-calc dilakukan di build_fair_value_report setelah current_price tersedia
                stats.raw_pe_current = pe_ttm

        stats.eps_forward = (
            _lookup(
                [
                    "Forward EPS",
                    "EPS (Forward)",
                    "Estimated EPS",
                ]
            )
            or stats.eps_ttm
        )  # fallback to TTM

        stats.book_value_per_share = _lookup(
            [
                "Book Value Per Share",
                "BVPS",
                "Book Value/Share",
                "Current Book Value Per Share",
            ]
        )

        stats.dps = _lookup_optional(
            [
                "Dividend Per Share (TTM)",
                "DPS (TTM)",
                "Dividend Per Share",
                "DPS",
                "Annual Dividend Per Share",
                "Cash Dividend Per Share",
                "Total Dividend Per Share",
                "Dividen Per Saham",
            ]
        )

        # ── Profitability ratios ─────────────────────────────────────────────
        stats.roe = _lookup(
            [
                "Return On Equity (TTM)",
                "ROE (TTM)",
                "ROE",
                "Return on Equity",
                "Return On Equity",
                "Return on Equity (TTM)",
                "Imbal Hasil Ekuitas",  # Bahasa Indonesia variant
            ],
            pct=True,
        )

        stats.net_margin = _lookup(
            [
                "Net Profit Margin (TTM)",
                "Net Margin (TTM)",
                "Net Margin",
                "Net Profit Margin",
                "Profit Margin",
            ],
            pct=True,
        )

        stats.roa = _lookup(
            [
                "Return On Assets (TTM)",
                "ROA (TTM)",
                "ROA",
                "Return on Assets",
            ],
            pct=True,
        )

        stats.rnoa = _lookup(
            [
                "Return on Net Operating Assets",
                "RNOA",
            ],
            pct=True,
        )

        stats.operating_cash_flow_ttm = _lookup(
            [
                "Operating Cash Flow (TTM)",
                "Cash From Operations (TTM)",
                "Cash From Operations",
                "Cash Flow from Operations (TTM)",
                "Cash Flow from Operations",
                "Net Cash Provided by Operating Activities",
                "CFO (TTM)",
            ]
        )

        stats.ocf_per_share = _lookup(
            [
                "Operating Cash Flow Per Share",
                "OCF Per Share",
                "Cash Flow from Operations Per Share",
            ]
        )

        stats.shares_outstanding = _lookup(
            [
                "Current Share Outstanding",
                "Shares Outstanding",
                "Outstanding Shares",
                "Share Outstanding",
            ]
        )

        # ── Valuation multiples ──────────────────────────────────────────────
        stats.raw_pe_current = stats.raw_pe_current or _lookup(
            [
                "Current PE Ratio (TTM)",
                "PE Ratio (TTM)",
                "Current PE Ratio (Annualised)",
                "P/E Ratio",
            ]
        )

        stats.raw_pb_current = _lookup(
            [
                "Current Price to Book Value",
                "Price to Book Value",
                "P/B Ratio",
                "Price/Book",
            ]
        )

        stats.ev_ebitda_current = _lookup_optional(
            [
                "EV to EBITDA (TTM)",
                "EV/EBITDA (TTM)",
                "EV/EBITDA",
                "Enterprise Value/EBITDA",
            ]
        )

    # ── Strategy B: legacy flat key-value fallback ────────────────────────────
    # Only runs if Strategy A found nothing useful (flat dict empty or all zeros)
    if not flat or (
        stats.eps_ttm == 0
        and stats.book_value_per_share == 0
        and stats.dps in (None, 0)
    ):
        logger.info(
            "[FairValue] {}: closure_fin_items structure empty, trying legacy key-value",
            ticker,
        )

        def _get_legacy_optional(keys: list[str]) -> float | None:
            """Return a legacy numeric field while preserving missing values."""
            for key in keys:
                try:
                    val = api_response
                    for part in key.split("."):
                        val = val[part]
                    parsed = _clean_numeric_or_none(val)
                    if parsed is not None:
                        return parsed
                except (KeyError, TypeError, ValueError):
                    continue
            # Shallow sub-dict search — satu level dalam dari top-level api_response
            simple_keys = {k.split(".")[-1].lower() for k in keys}
            for top_val in api_response.values():
                if not isinstance(top_val, dict):
                    continue
                for k, v in top_val.items():
                    if k.lower() in simple_keys and v is not None:
                        parsed = _clean_numeric_or_none(v)
                        if parsed is not None:
                            return parsed
            return None

        def _get_legacy(keys: list[str], default: float = 0.0) -> float:
            """Return a legacy numeric field, defaulting absent values to 0.0."""
            parsed = _get_legacy_optional(keys)
            return default if parsed is None else parsed

        stats.eps_ttm = _get_legacy(
            ["eps", "eps_ttm", "earningPerShare", "data.Current.EPS", "EPS"]
        )
        stats.book_value_per_share = _get_legacy(
            ["bookValuePerShare", "bvps", "data.Current.BVPS", "BVPS"]
        )
        legacy_dps = _get_legacy_optional(
            ["dps", "dividendPerShare", "data.Current.DPS", "DPS"]
        )
        if legacy_dps is not None:
            stats.dps = legacy_dps
        stats.roe = _get_legacy(["roe", "returnOnEquity", "data.Current.ROE", "ROE"])
        stats.net_margin = _get_legacy(
            ["netMargin", "net_margin", "data.Current.NetProfitMargin"]
        )
        stats.roa = _get_legacy(["roa", "returnOnAssets", "data.Current.ROA"])
        stats.rnoa = _get_legacy(["rnoa", "returnOnNetOperatingAssets", "data.Current.RNOA"])
        stats.operating_cash_flow_ttm = _get_legacy(
            [
                "operatingCashFlow",
                "operating_cash_flow",
                "cashFlowFromOperations",
                "data.Current.OperatingCashFlow",
            ]
        )
        stats.ocf_per_share = _get_legacy(
            ["ocfPerShare", "operatingCashFlowPerShare", "data.Current.OCFPS"]
        )
        stats.shares_outstanding = _get_legacy(
            ["sharesOutstanding", "shareOutstanding", "data.Current.SharesOutstanding"]
        )
        stats.raw_pe_current = _get_legacy(
            ["pe", "priceEarnings", "data.Current.PE", "PE"]
        )
        stats.raw_pb_current = _get_legacy(
            ["pb", "priceBook", "data.Current.PBV", "PBV"]
        )

        if stats.roe > 1.0:
            stats.roe = stats.roe / 100.0
        if stats.net_margin > 1.0:
            stats.net_margin = stats.net_margin / 100.0
        if stats.roa > 1.0:
            stats.roa = stats.roa / 100.0
        if stats.rnoa > 1.0:
            stats.rnoa = stats.rnoa / 100.0

    # ── Derive DPS from dividend yield × price if DPS still missing ──────────
    # BBCA dan bank besar lain kadang tidak expose DPS langsung di keystats
    # tapi selalu expose Dividend Yield (%). DPS = yield × current_price / 100
    if stats.dps is None and flat:
        div_yield_pct = _lookup(
            [
                "Dividend Yield (TTM)",
                "Dividend Yield",
                "Yield",
                "Trailing Dividend Yield",
                "Dividend Yield (Annual)",
            ]
        )
        if div_yield_pct > 0:
            price_for_dps = stats.current_price or _lookup(
                ["Last Price", "Current Price", "Close Price"]
            )
            if price_for_dps > 0:
                # yield dari Stockbit dalam % (e.g. "5.62") → bagi 100
                stats.dps = (
                    round((div_yield_pct / 100.0) * price_for_dps, 2)
                    if div_yield_pct > 1.0
                    else round(div_yield_pct * price_for_dps, 2)
                )
                logger.info(
                    "[FairValue] {}: DPS derived from yield "
                    "({:.2f}%) × price ({:,.0f}) "
                    "= {:.2f}",
                    ticker,
                    div_yield_pct,
                    price_for_dps,
                    stats.dps,
                )

    # ── FV-4: Best-effort keystats age extraction ─────────────────────────────
    # Try to find a report date so fair_value_weighted() can detect stale data.
    # The Stockbit closure_fin_items groups may carry a "closure_date" key, or
    # a flat fitem may contain an ISO-date-like value under a "date"/"period" key.
    _date_val: str | None = None
    try:
        groups = api_response.get("data", {}).get("closure_fin_items_results", [])
        if groups:
            # Some API versions surface "closure_date" at the group level
            _date_val = groups[0].get("closure_date") or groups[0].get("period_date")
        if not _date_val:
            # Scan the flat dict for any key containing "date" or "period" with
            # a value that looks like an ISO date (YYYY-MM-DD or DD/MM/YYYY)
            _iso_re = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})")
            for k, v in flat.items():
                if ("date" in k.lower() or "period" in k.lower()) and _iso_re.search(str(v)):
                    _date_val = _iso_re.search(str(v)).group(0)
                    break
        if _date_val:
            _date_val = str(_date_val).strip()
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%m/%d/%Y"):
                try:
                    from datetime import datetime
                    report_date = datetime.strptime(_date_val, fmt).date()
                    stats.keystats_age_days = (_date.today() - report_date).days
                    break
                except ValueError:
                    continue
    except Exception as _exc:
        logger.debug("[FairValue] keystats_age_days extraction failed: {}", _exc)

    # ── Debug summary ─────────────────────────────────────────────────────────
    parsed = {
        "eps_ttm": stats.eps_ttm,
        "bvps": stats.book_value_per_share,
        "dps": stats.dps,
        "roe": f"{stats.roe * 100:.1f}%",
        "pe": stats.raw_pe_current,
        "pb": stats.raw_pb_current,
    }
    missing = [k for k, v in parsed.items() if v is None]
    zeros = [k for k, v in parsed.items() if str(v) in ("0", "0.0", "0.0%")]
    if missing:
        logger.info("[FairValue] {}: fields missing after parse: {}", ticker, missing)
    if zeros:
        logger.warning(
            "[FairValue] {}: fields still 0 after parse: {}. "
            "Add the missing Stockbit field name to the mapping list in extract_keystats().",
            ticker,
            zeros,
        )
    else:
        logger.info("[FairValue] {}: all key stats parsed OK: {}", ticker, parsed)

    return stats


# ---------------------------------------------------------------------------
# Main calculator
# ---------------------------------------------------------------------------


class FairValueCalculator:
    """
    Menghitung fair value saham IHSG menggunakan 3 metode:
      1. P/E Band   — EPS × historical average P/E
      2. P/B Band   — BVPS × historical average P/B
      3. DDM / Gordon Growth Model — untuk saham dengan dividen stabil

    Hasil akhir adalah weighted average yang dapat dikonfigurasi per sektor.
    """

    # Bobot default per metode (harus jumlah = 1.0)
    # Untuk bank (BBCA, BBRI, BMRI): P/B lebih relevan karena aset berbasis ekuitas
    SECTOR_WEIGHTS = {
        # DDM (Gordon Growth Model) is a perpetuity formula — unsuitable for
        # 3–15 day swing trades. Bank/property retain a small weight (5%) as
        # dividend yield is a genuine sector signal for those; all others zero.
        "bank":     {"pe": 0.45, "pb": 0.50, "ddm": 0.05},
        "consumer": {"pe": 0.50, "pb": 0.35, "ddm": 0.00, "dcf": 0.15},
        "mining":   {"pe": 0.35, "pb": 0.25, "ddm": 0.00, "ev_ebitda": 0.40},
        "property": {"pe": 0.35, "pb": 0.60, "ddm": 0.05},
        "default":  {"pe": 0.50, "pb": 0.40, "ddm": 0.00, "dcf": 0.10},
    }

    # Ticker → sektor mapping untuk emiten populer IHSG
    TICKER_SECTOR = {
        "BBCA": "bank",
        "BBRI": "bank",
        "BMRI": "bank",
        "BBNI": "bank",
        "BRIS": "bank",
        "BTPS": "bank",
        "TLKM": "default",
        "ASII": "default",
        "UNVR": "consumer",
        "ICBP": "consumer",
        "MYOR": "consumer",
        "ADRO": "mining",
        "BYAN": "mining",
        "MDKA": "mining",
        "BSDE": "property",
        "SMRA": "property",
    }

    SECTOR_PROFILE_ALIAS = {
        "bank": "bank",
        "finance_nonbank": "bank",
        "consumer_staples": "consumer",
        "consumer_disc": "consumer",
        "consumer": "consumer",
        "energy": "mining",
        "basic_materials": "mining",
        "mining": "mining",
        "property": "property",
        "industrials": "default",
        "infrastructure": "default",
        "transport": "default",
        "tech": "default",
        "healthcare": "default",
        "default": "default",
    }

    # IDX sector median multiples and profitability ratios (static reference, ~2024–2025 data)
    SECTOR_MEDIAN_PROFILES: dict[str, dict] = {
        "bank":     {"pe": 10.0, "pb": 1.5, "roe": 0.14, "net_margin": 0.25},
        "mining":   {"pe":  7.0, "pb": 1.2, "roe": 0.18, "net_margin": 0.15},
        "consumer": {"pe": 18.0, "pb": 3.0, "roe": 0.20, "net_margin": 0.08},
        "property": {"pe": 12.0, "pb": 0.8, "roe": 0.07, "net_margin": 0.20},
        "default":  {"pe": 14.0, "pb": 1.5, "roe": 0.15, "net_margin": 0.10},
    }

    # EV/EBITDA target multiple for mining/energy (conservative IDX 5-year median)
    _MINING_EV_EBITDA_TARGET: float = 5.5
    # Cycle-average net margin for IDX mining sector (used for peak EPS normalization)
    _MINING_SECTOR_MEDIAN_MARGIN: float = 0.15

    def __init__(self, stats: KeyStats, sector: str | None = None, eps_derived: bool = False):
        self.stats = stats
        self.eps_derived = eps_derived
        self.raw_sector = (
            str(sector).strip().lower()
            if sector
            else _sector_from_cache(stats.ticker)
            or self.TICKER_SECTOR.get(_normalize_ticker_key(stats.ticker), "default")
        )
        self.sector = self.SECTOR_PROFILE_ALIAS.get(self.raw_sector, "default")
        self.weights = self.SECTOR_WEIGHTS[self.sector]
        self.sector_medians: dict[str, dict] = _load_dynamic_sector_benchmarks()
        self._weighted_result_cache: dict | None = None
        self._pb_roe_capped: bool = False
        self._normalized_eps: float | None = None
        assert abs(sum(self.weights.values()) - 1.0) < 1e-9, (
            f"SECTOR_WEIGHTS['{self.sector}'] tidak menjumlah 1.0: {self.weights}"
        )

    def _cache_weighted_result(self, result: dict) -> dict:
        self._weighted_result_cache = result
        return result

    # ── Cyclical EPS normalization (T3) ─────────────────────────────────────

    def _normalize_cyclical_eps(self) -> float:
        """For mining at peak margin (>2× sector median 15%), normalize EPS to cycle-average."""
        eps = self.stats.eps_ttm or self.stats.eps_forward
        if self.sector != "mining":
            self._normalized_eps = None
            return eps
        margin = self.stats.net_margin
        median_margin = self._MINING_SECTOR_MEDIAN_MARGIN
        if margin > 2 * median_margin:
            normalized = eps * (median_margin / margin)
            self._normalized_eps = round(normalized, 2)
            return self._normalized_eps
        self._normalized_eps = None
        return eps

    # ── Metode 1: P/E Band ───────────────────────────────────────────────────

    def fair_value_pe(self) -> float | None:
        """
        Fair value = EPS_TTM × historical_pe_avg.
        Mining at peak margin (>30%) → EPS normalized to cycle-average.
        """
        eps = self._normalize_cyclical_eps()
        if eps <= 0 or self.stats.historical_pe_avg <= 0:
            return None
        return round(eps * self.stats.historical_pe_avg, 0)

    # ── Metode 2: P/B Band ───────────────────────────────────────────────────

    def fair_value_pb(self) -> float | None:
        """
        Fair value = BVPS × pb_multiple.
        ROE < ke (value trap) → pb_multiple capped at min(historical_pb, roe/ke).
        """
        bvps = self.stats.book_value_per_share
        if bvps <= 0 or self.stats.historical_pb_avg <= 0:
            return None
        roe = self.stats.roe
        ke = self.stats.cost_of_equity
        if roe > 0 and ke > 0 and roe < ke:
            pb_multiple = min(self.stats.historical_pb_avg, roe / ke)
            self._pb_roe_capped = True
        else:
            pb_multiple = self.stats.historical_pb_avg
            self._pb_roe_capped = False
        return round(bvps * pb_multiple, 0)

    # ── Metode 3: DDM (Gordon Growth Model) ─────────────────────────────────

    def fair_value_ddm(self) -> float | None:
        """
        Fair value = DPS / (cost_of_equity - growth_rate)
        """
        dps = self.stats.dps
        ke = self.stats.cost_of_equity
        g = self.stats.growth_rate

        if dps is None or dps <= 0:
            return None
        if ke <= g:
            return None  # model tidak valid
        if ke - g < 0.03:
            return None  # spread < 3% → DDM too sensitive to be reliable
        if self.stats.roe and self.stats.roe > 0.20 and ke > 0.14:
            return None  # DDM breaks down for high-ROE compounders when Ke > 14%

        fv = dps / (ke - g)

        if self.stats.current_price > 0:
            ratio = fv / self.stats.current_price
            if ratio > 10.0 or ratio < 0.1:
                return None  # outlier — abaikan

        return round(fv, 0)

    # ── Metode 4: EV/EBITDA Band (mining/energy only) ───────────────────────

    def fair_value_ev_ebitda(self) -> float | None:
        """
        Fair value = current_price × (target_EV_EBITDA / current_EV_EBITDA)

        Only fires for mining/energy sector. Returns None if EV/EBITDA is
        unavailable or the result falls outside a 3x–0.3x sanity band.
        """
        if self.sector != "mining":
            return None
        current = self.stats.ev_ebitda_current
        price = self.stats.current_price
        if not current or current <= 0 or price <= 0:
            return None
        fv = round(price * (self._MINING_EV_EBITDA_TARGET / current), 0)
        ratio = fv / price
        if ratio > 3.0 or ratio < 0.3:
            return None
        return fv

    # ── Metode 5: 2-Stage DCF using OCF/Share (consumer/industrials) ─────────

    _DCF_TERMINAL_GROWTH: float = 0.04  # IDX nominal GDP long-run proxy
    _DCF_STAGE1_YEARS: int = 5

    def _ocf_data_is_stable(self, ocf_per_share: float) -> bool:
        """Gate OCF-DCF to cases where cash-flow data is usable, not just positive."""
        if self.stats.ocf_stability_score is not None:
            return self.stats.ocf_stability_score >= 0.60
        if self.stats.keystats_age_days is not None and self.stats.keystats_age_days > _STALE_KEYSTATS_DAYS:
            return False
        if self.stats.current_price > 0:
            ocf_yield = ocf_per_share / self.stats.current_price
            if ocf_yield < 0.01 or ocf_yield > 0.50:
                return False
        if self.stats.eps_ttm > 0:
            cash_conversion = ocf_per_share / self.stats.eps_ttm
            if cash_conversion < 0.25 or cash_conversion > 4.0:
                return False
        return True

    def fair_value_dcf(self) -> float | None:
        """2-stage OCF-based DCF. Fires only for consumer and default (industrials) sectors.

        Banks: OCF/share doesn't translate to equity value cleanly (interest income structure).
        Mining: EV/EBITDA (Method 4) is preferred for commodity cycle stocks.
        Stage 1 (years 1–5): OCF grows at stats.growth_rate.
        Terminal value: OCF_5 × (1 + g_t) / (ke − g_t), perpetuity at long-run IDX GDP.
        """
        if self.sector in ("bank", "mining"):
            return None

        ocf_ps = self.stats.ocf_per_share
        if ocf_ps <= 0 and self.stats.operating_cash_flow_ttm > 0 and self.stats.shares_outstanding > 0:
            ocf_ps = self.stats.operating_cash_flow_ttm / self.stats.shares_outstanding
        if ocf_ps <= 0:
            return None
        if not self._ocf_data_is_stable(ocf_ps):
            return None

        ke = self.stats.cost_of_equity
        g = min(self.stats.growth_rate, ke - 0.02)  # ensure spread ≥ 2% for Stage 1
        g_t = self._DCF_TERMINAL_GROWTH

        if ke <= g_t:
            return None

        pv_stage1 = sum(
            ocf_ps * ((1 + g) ** t) / ((1 + ke) ** t)
            for t in range(1, self._DCF_STAGE1_YEARS + 1)
        )
        ocf_terminal = ocf_ps * ((1 + g) ** self._DCF_STAGE1_YEARS)
        tv = ocf_terminal * (1 + g_t) / (ke - g_t)
        pv_tv = tv / ((1 + ke) ** self._DCF_STAGE1_YEARS)

        fv = round(pv_stage1 + pv_tv, 0)

        if self.stats.current_price > 0:
            ratio = fv / self.stats.current_price
            if ratio > 5.0 or ratio < 0.1:
                return None

        return fv

    # ── Task 27: Sector Peer Comparison ──────────────────────────────────────

    def build_sector_comparison(self) -> str:
        # Try raw IDX sector (e.g. "consumer_disc") before falling back to bucket ("consumer").
        median = self.sector_medians.get(
            self.raw_sector,
            self.sector_medians.get(
                self.sector,
                self.sector_medians.get("default", _SECTOR_MEDIAN_PROFILES_DEFAULT["default"]),
            ),
        )
        pe_cur = self.stats.raw_pe_current
        pb_cur = self.stats.raw_pb_current
        roe_cur = self.stats.roe
        margin_cur = self.stats.net_margin

        def _pct_diff(cur: float, med: float) -> str:
            if med <= 0:
                return "N/A"
            diff = (cur - med) / med * 100
            label = "Above Avg" if diff > 10 else ("Below Avg" if diff < -10 else "In Line")
            return f"{label} ({diff:+.0f}%)"

        lines = [
            "── SECTOR PEER CONTEXT (IDX Median) ───────────────────────────────",
            f"  Sektor : {self.raw_sector.upper()} (data: ~2024–2025 median)",
        ]
        if pe_cur > 0:
            lines.append(
                f"  P/E    : {pe_cur:.1f}x stock | {median['pe']:.1f}x sector → {_pct_diff(pe_cur, median['pe'])}"
            )
        if pb_cur > 0:
            lines.append(
                f"  P/BV   : {pb_cur:.1f}x stock | {median['pb']:.1f}x sector → {_pct_diff(pb_cur, median['pb'])}"
            )
        if roe_cur > 0:
            lines.append(
                f"  ROE    : {roe_cur*100:.1f}% stock | {median['roe']*100:.1f}% sector → {_pct_diff(roe_cur, median['roe'])}"
            )
        if margin_cur > 0:
            lines.append(
                f"  Margin : {margin_cur*100:.1f}% stock | {median['net_margin']*100:.1f}% sector → {_pct_diff(margin_cur, median['net_margin'])}"
            )
        return "\n".join(lines)

    def _fundamental_factor_payload(self) -> dict[str, Any]:
        """Expose IDX-calibrated factor signals to downstream agents/reports."""
        ocf_per_share = self.stats.ocf_per_share
        if ocf_per_share <= 0 and self.stats.operating_cash_flow_ttm > 0:
            if self.stats.shares_outstanding > 0:
                ocf_per_share = self.stats.operating_cash_flow_ttm / self.stats.shares_outstanding

        ocf_price_ratio = self.stats.ocf_price_ratio
        if ocf_price_ratio <= 0:
            if ocf_per_share > 0 and self.stats.current_price > 0:
                ocf_price_ratio = ocf_per_share / self.stats.current_price
            else:
                ocf_price_ratio = calculate_ocf_price_ratio(
                    self.stats.operating_cash_flow_ttm,
                    self.stats.shares_outstanding,
                    self.stats.current_price,
                )

        proxy = self.stats.rnoa if self.stats.rnoa > 0 else self.stats.roa
        proxy_source = "rnoa" if self.stats.rnoa > 0 else ("roa" if self.stats.roa > 0 else None)
        quality_score = self.stats.profitability_factor_score
        if quality_score <= 0 and proxy > 0:
            quality_score = calculate_profitability_score(
                {"rnoa": self.stats.rnoa, "roa": self.stats.roa}
            )

        return {
            "ocf_price_ratio": round(ocf_price_ratio, 4) if ocf_price_ratio > 0 else None,
            "ocf_per_share": round(ocf_per_share, 2) if ocf_per_share > 0 else None,
            "rnoa": round(self.stats.rnoa, 4) if self.stats.rnoa > 0 else None,
            "roa": round(self.stats.roa, 4) if self.stats.roa > 0 else None,
            "profitability_proxy": round(proxy, 4) if proxy > 0 else None,
            "profitability_proxy_source": proxy_source,
            "profitability_factor_score": round(quality_score, 2) if quality_score > 0 else None,
        }

    # ── Weighted Average ─────────────────────────────────────────────────────

    def fair_value_weighted(self) -> dict:
        pe_fv = None if self.eps_derived else self.fair_value_pe()
        pb_fv = self.fair_value_pb()
        ddm_fv = self.fair_value_ddm()
        ev_ebitda_fv = self.fair_value_ev_ebitda()
        dcf_fv = self.fair_value_dcf()

        results = {}
        if pe_fv is not None:
            results["pe"] = pe_fv
        if pb_fv is not None:
            results["pb"] = pb_fv
        if ddm_fv is not None:
            results["ddm"] = ddm_fv
        if ev_ebitda_fv is not None:
            results["ev_ebitda"] = ev_ebitda_fv
        if dcf_fv is not None:
            results["dcf"] = dcf_fv

        is_soe = _normalize_ticker_key(self.stats.ticker) in _SOE_TICKERS

        keystats_stale = (
            self.stats.keystats_age_days is not None
            and self.stats.keystats_age_days > _STALE_KEYSTATS_DAYS
        )

        if not results:
            return self._cache_weighted_result(
                {
                    "fair_value": None,
                    "fair_value_base": None,
                    "fair_value_low": None,
                    "fair_value_high": None,
                    "range_pct": None,
                    "risk_overvalued": False,
                    "breakdown": {},
                    "confidence": "INSUFFICIENT_DATA",
                    "margin_of_safety_pct": None,
                    "valuation_verdict": "DATA_UNAVAILABLE",
                    "is_soe": is_soe,
                    "governance_discount_pct": None,
                    "keystats_stale": keystats_stale,
                    "keystats_age_days": self.stats.keystats_age_days,
                    **self._fundamental_factor_payload(),
                }
            )

        # FV-4: Staleness — shift weight from EPS-dependent methods to P/B
        # when the underlying financial data is older than _STALE_KEYSTATS_DAYS.
        if keystats_stale:
            effective_weights = dict(self.weights)
            shift = sum(effective_weights.get(m, 0) * 0.50 for m in _STALE_EPS_METHODS)
            for m in _STALE_EPS_METHODS:
                if m in effective_weights:
                    effective_weights[m] *= 0.50
            effective_weights["pb"] = effective_weights.get("pb", 0) + shift
        else:
            effective_weights = self.weights

        total_weight = sum(effective_weights[m] for m in results)
        weighted_fv = sum(
            results[m] * (effective_weights[m] / total_weight) for m in results
        )
        weighted_fv = round(weighted_fv, 0)

        # SOE governance discount — applied before range/MOS so all derived
        # fields use the discounted FV without a second pass.
        if is_soe:
            weighted_fv = round(weighted_fv * (1 - _SOE_DISCOUNT_PCT), 0)

        n = len(results)
        confidence = "HIGH" if n >= 3 else ("MEDIUM" if n == 2 else "LOW")
        range_pct = _range_pct_for_method_count(n)
        if range_pct is None:
            fair_value_low = None
            fair_value_high = None
        else:
            fair_value_low = round(weighted_fv * (1 - range_pct), 0)
            fair_value_high = round(weighted_fv * (1 + range_pct), 0)

        mos = None
        verdict = "DATA_UNAVAILABLE"
        risk_overvalued = False
        if self.stats.current_price > 0 and weighted_fv > 0:
            mos = round(
                ((weighted_fv - self.stats.current_price) / self.stats.current_price)
                * 100,
                1,
            )
            if fair_value_low is not None and fair_value_high is not None:
                verdict = _valuation_verdict_from_range(
                    price=self.stats.current_price,
                    fair_value_base=weighted_fv,
                    fair_value_low=fair_value_low,
                    fair_value_high=fair_value_high,
                )
                risk_overvalued = self.stats.current_price > fair_value_high

        return self._cache_weighted_result(
            {
                "fair_value": weighted_fv,
                "fair_value_base": weighted_fv,
                "fair_value_low": fair_value_low,
                "fair_value_high": fair_value_high,
                "range_pct": range_pct,
                "risk_overvalued": risk_overvalued,
                "breakdown": {k: int(v) for k, v in results.items()},
                "confidence": confidence,
                "margin_of_safety_pct": mos,
                "valuation_verdict": verdict,
                "is_soe": is_soe,
                "governance_discount_pct": _SOE_DISCOUNT_PCT if is_soe else None,
                "keystats_stale": keystats_stale,
                "keystats_age_days": self.stats.keystats_age_days,
                **self._fundamental_factor_payload(),
            }
        )

    # ── Target & Stop Calculator ─────────────────────────────────────────────

    @staticmethod
    def calculate_trade_levels(
        entry_low: float,
        entry_high: float,
        target_gain_pct: float = 7.0,
        stop_loss_pct: float = 4.0,
    ) -> dict:
        entry_mid = (entry_low + entry_high) / 2
        target_price = round(entry_mid * (1 + target_gain_pct / 100), -1)
        stop_loss = round(entry_mid * (1 - stop_loss_pct / 100), -1)

        rr = calculate_rr(entry_high, target_price, stop_loss)

        return {
            "entry_mid": round(entry_mid, 0),
            "target_price": target_price,
            "stop_loss": stop_loss,
            "expected_return_pct": f"+{target_gain_pct:.1f}%",
            "risk_reward_ratio": rr,
        }

    # ── Build Report String (untuk diinjeksi ke raw_data) ───────────────────

    def build_report(self, current_price: float | None = None) -> str:
        if current_price is not None and current_price != self.stats.current_price:
            self._weighted_result_cache = None
        if current_price is not None:  # ← fix: `if current_price:` is False for 0.0
            self.stats.current_price = current_price

        result = self._weighted_result_cache or self.fair_value_weighted()
        fv = result["fair_value"]
        fv_base = result.get("fair_value_base")
        fv_low = result.get("fair_value_low")
        fv_high = result.get("fair_value_high")
        risk_overvalued = bool(result.get("risk_overvalued"))
        bdown = result["breakdown"]
        mos = result["margin_of_safety_pct"]
        conf = result["confidence"]
        verdict = result["valuation_verdict"]
        dps_text = f"Rp {self.stats.dps:,.0f}" if self.stats.dps is not None else "N/A"
        ocf_price_ratio = result.get("ocf_price_ratio") or self.stats.ocf_price_ratio
        ocf_per_share = result.get("ocf_per_share") or self.stats.ocf_per_share
        ocf_price_text = (
            f"{ocf_price_ratio * 100:.1f}%"
            if ocf_price_ratio and ocf_price_ratio > 0
            else "N/A"
        )
        ocf_per_share_text = (
            f"Rp {ocf_per_share:,.0f}" if ocf_per_share and ocf_per_share > 0 else "N/A"
        )
        proxy_value = result.get("profitability_proxy") or 0.0
        proxy_source_key = result.get("profitability_proxy_source")
        proxy_source = (
            "RNOA"
            if proxy_source_key == "rnoa"
            else ("ROA fallback" if proxy_source_key == "roa" else "N/A")
        )
        proxy_text = f"{proxy_value * 100:.1f}% ({proxy_source})" if proxy_value > 0 else "N/A"
        quality_score = result.get("profitability_factor_score") or 0.0
        quality_text = (
            f"{quality_score:.2f}/1.00"
            if quality_score > 0
            else "N/A"
        )

        lines = [
            "╔══════════════════════════════════════════════════════════════╗",
            "║  FAIR VALUE REPORT                                           ║",
            "╚══════════════════════════════════════════════════════════════╝",
            "",
            f"TICKER          : {self.stats.ticker}",
            f"SEKTOR          : {self.sector.upper()}",
            f"HARGA PASAR     : Rp {self.stats.current_price:,.0f}",
            "",
            "── BREAKDOWN FAIR VALUE ────────────────────────────────────────",
        ]

        if "pe" in bdown:
            if self._normalized_eps is not None:
                lines.append(
                    f"  Metode P/E Band : EPS Rp {self.stats.eps_ttm:,.0f} "
                    f"→ normalized Rp {self._normalized_eps:,.0f} "
                    f"(peak margin, cycle-avg {self._MINING_SECTOR_MEDIAN_MARGIN * 100:.0f}%) × "
                    f"P/E historis {self.stats.historical_pe_avg:.1f}x "
                    f"= Rp {bdown['pe']:,}"
                )
            else:
                lines.append(
                    f"  Metode P/E Band : EPS Rp {self.stats.eps_ttm:,.0f} × "
                    f"P/E historis {self.stats.historical_pe_avg:.1f}x "
                    f"= Rp {bdown['pe']:,}"
                )
        else:
            lines.append(
                "  Metode P/E Band : TIDAK VALID (EPS = 0 atau data tidak tersedia)"
            )

        if "pb" in bdown:
            if self._pb_roe_capped:
                roe = self.stats.roe
                ke = self.stats.cost_of_equity
                capped_pb = min(self.stats.historical_pb_avg, roe / ke)
                lines.append(
                    f"  Metode P/B Band : BVPS Rp {self.stats.book_value_per_share:,.0f} × "
                    f"P/B {self.stats.historical_pb_avg:.2f}x → capped {capped_pb:.2f}x "
                    f"(ROE {roe * 100:.1f}% < ke {ke * 100:.1f}%, value trap gate) "
                    f"= Rp {bdown['pb']:,}"
                )
            else:
                lines.append(
                    f"  Metode P/B Band : BVPS Rp {self.stats.book_value_per_share:,.0f} × "
                    f"P/B historis {self.stats.historical_pb_avg:.1f}x "
                    f"= Rp {bdown['pb']:,}"
                )
        else:
            lines.append(
                "  Metode P/B Band : TIDAK VALID (BVPS = 0 atau data tidak tersedia)"
            )

        if "ddm" in bdown:
            lines.append(
                f"  Metode DDM      : DPS {dps_text} / "
                f"(ke {self.stats.cost_of_equity * 100:.0f}% - g {self.stats.growth_rate * 100:.0f}%) "
                f"= Rp {bdown['ddm']:,}"
            )
        else:
            lines.append("  Metode DDM      : TIDAK VALID")

        if self.sector == "mining":
            if "ev_ebitda" in bdown:
                ev_cur = self.stats.ev_ebitda_current or 0.0
                lines.append(
                    f"  EV/EBITDA Band  : {ev_cur:.1f}x current → "
                    f"{self._MINING_EV_EBITDA_TARGET:.1f}x target "
                    f"= Rp {bdown['ev_ebitda']:,}"
                )
            else:
                lines.append(
                    "  EV/EBITDA Band  : TIDAK VALID (EV/EBITDA tidak tersedia)"
                )

        if "dcf" in self.weights:
            if "dcf" in bdown:
                lines.append(
                    f"  OCF-DCF         : OCF/Share {ocf_per_share_text} "
                    f"diskonto ke {self.stats.cost_of_equity * 100:.1f}% "
                    f"= Rp {bdown['dcf']:,}"
                )
            else:
                lines.append(
                    "  OCF-DCF         : TIDAK VALID "
                    "(OCF kosong/tidak stabil/di luar sanity band)"
                )

        fv_str = (
            f"Rp {fv:,.0f}"
            if fv is not None
            else "Tidak dapat dikalkulasi (Data Kosong / None)"
        )
        fv_base_str = f"Rp {fv_base:,.0f}" if fv_base is not None else "N/A"
        fv_range_str = (
            f"Rp {fv_low:,.0f} - Rp {fv_high:,.0f}"
            if fv_low is not None and fv_high is not None
            else "N/A"
        )
        lines += [
            "",
            "── HASIL AKHIR ─────────────────────────────────────────────────",
            f"  FAIR VALUE (weighted avg) : {fv_str}",
            f"  FAIR VALUE BASE           : {fv_base_str}",
            f"  FAIR VALUE RANGE          : {fv_range_str}",
            f"  Kalkulasi confidence      : {conf} ({len(bdown)}/{len(self.weights)} metode valid)",
            f"  RISK OVERVALUED           : {risk_overvalued}",
            "",
        ]

        if mos is not None:
            symbol = "⬆ UPSIDE" if mos >= 0 else "⬇ PREMIUM"
            lines += [
                "── MARGIN OF SAFETY ────────────────────────────────────────────",
                f"  Harga Pasar   : Rp {self.stats.current_price:,.0f}",
                f"  Fair Value    : {fv_base_str}",
                f"  FV Range      : {fv_range_str}",
                f"  Gap           : {mos:+.1f}% ({symbol})",
                f"  Verdict       : {verdict}",
                f"  Risk Overval. : {risk_overvalued}",
                "",
            ]

            if verdict == "OVERVALUED":
                premium = abs(mos)
                lines += [
                    "🚨 PERINGATAN OVERVALUATION 🚨",
                    f"   Harga pasar {premium:.1f}% DI ATAS base fair value "
                    "dan di atas range high.",
                    "   IMPLIKASI SWING TRADE:",
                    "   • Margin of safety NEGATIF — tidak ada bantalan jika tesis salah.",
                    "   • Entry hanya valid jika ada momentum kuat dan katalis spesifik.",
                    "   • CIO HARUS memberikan rating HOLD atau AVOID kecuali ada alasan",
                    "     teknikal yang sangat kuat untuk override.",
                    "",
                ]
            elif verdict == "SLIGHTLY_OVERVALUED":
                lines += [
                    "CATATAN VALUASI:",
                    "   Harga berada di atas base fair value, "
                    "tetapi masih dalam fair value range.",
                    "   Tidak dianggap overvalued oleh risk governor "
                    "selama harga <= range high.",
                    "",
                ]
            elif verdict == "UNDERVALUED":
                lines += [
                    "✅ MARGIN OF SAFETY POSITIF",
                    f"   Harga pasar {abs(mos):.1f}% DI BAWAH fair value.",
                    "   Setup swing trade punya bantalan fundamental yang kuat.",
                    "",
                ]

        lines += [
            "── KEY FUNDAMENTALS ────────────────────────────────────────────",
            f"  EPS TTM         : Rp {self.stats.eps_ttm:,.0f}",
            f"  BVPS            : Rp {self.stats.book_value_per_share:,.0f}",
            f"  DPS             : {dps_text}",
            f"  ROE             : {self.stats.roe * 100:.1f}%",
            f"  RNOA/ROA Proxy  : {proxy_text}",
            f"  Quality Factor  : {quality_text}",
            f"  Net Margin      : {self.stats.net_margin * 100:.1f}%",
            f"  OCF/Share       : {ocf_per_share_text}",
            f"  OCF/Price       : {ocf_price_text}",
            f"  P/E saat ini    : {self.stats.raw_pe_current:.1f}x "
            f"(hist avg: {self.stats.historical_pe_avg:.1f}x)",
            f"  P/B saat ini    : {self.stats.raw_pb_current:.1f}x "
            f"(hist avg: {self.stats.historical_pb_avg:.1f}x)",
            "",
            self.build_sector_comparison(),
            "",
            "CATATAN: Semua angka di atas dihitung Python dari data API.",
            "         LLM DILARANG menimpa atau menghitung ulang FAIR VALUE.",
            "═" * 65,
        ]

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Historical P/E & P/B defaults — override ini per emiten jika punya data lebih akurat
# ---------------------------------------------------------------------------

HISTORICAL_MULTIPLES: dict[str, dict] = {
    "BBCA": {"pe": 25.0, "pb": 4.5, "beta": 0.85, "growth_rate": 0.07},
    "BBRI": {"pe": 14.0, "pb": 2.2, "beta": 0.95, "growth_rate": 0.06},
    "BMRI": {"pe": 13.0, "pb": 1.8, "beta": 0.95, "growth_rate": 0.06},
    "BBNI": {"pe": 10.0, "pb": 1.3, "beta": 1.00, "growth_rate": 0.05},
    "TLKM": {"pe": 18.0, "pb": 3.0, "beta": 0.85, "growth_rate": 0.05},
    "ASII": {"pe": 14.0, "pb": 1.8, "beta": 1.10, "growth_rate": 0.06},
    "UNVR": {"pe": 35.0, "pb": 20.0, "beta": 0.75, "growth_rate": 0.05},
    "ICBP": {"pe": 20.0, "pb": 3.5, "beta": 0.80, "growth_rate": 0.07},
    "GOTO": {"pe": 0.0, "pb": 3.0, "beta": 1.50, "growth_rate": 0.15},
    "ADRO": {"pe": 8.0, "pb": 1.5, "beta": 1.30, "growth_rate": 0.03},
    "BYAN": {"pe": 7.0, "pb": 3.5, "beta": 1.35, "growth_rate": 0.02},
    "BSDE": {"pe": 10.0, "pb": 0.7, "beta": 1.05, "growth_rate": 0.05},
}


def get_historical_multiples(ticker: str) -> dict:
    """Return valuation multiples; computes cost_of_equity from beta via CAPM."""
    entry = HISTORICAL_MULTIPLES.get(
        _normalize_ticker_key(ticker),
        {"pe": 15.0, "pb": 2.0, "beta": 1.0, "growth_rate": 0.06},
    )
    result = dict(entry)
    beta = result.pop("beta", 1.0)
    result["cost_of_equity"] = _capm_cost_of_equity(beta)
    return result


def extract_historical_multiples(api_response: dict, ticker: str) -> dict:
    """Extract multi-year median PE/PB from Stockbit API response.

    Tries three common Stockbit API response structures to find yearly PE/PB
    values. Falls back to hardcoded HISTORICAL_MULTIPLES if extraction fails or
    yields insufficient data.

    When ≥3 yearly values are found the return dict also includes:
      pe_values : list[float]  — raw yearly series used to compute percentile
      pb_values : list[float]  — raw yearly series used to compute percentile
    """
    pe_values: list[float] = []
    pb_values: list[float] = []

    data = api_response.get("data", {})

    # Pattern 1: data.{year}.{metric}  (e.g. data.2024.PE)
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(key, str) and key.isdigit() and isinstance(val, dict):
                for pe_key in ("PE", "pe", "PER", "per"):
                    pe = val.get(pe_key)
                    if pe is not None:
                        try:
                            pv = float(pe)
                            if pv > 0:
                                pe_values.append(pv)
                        except (ValueError, TypeError):
                            pass
                        break
                for pb_key in ("PBV", "pbv", "PB", "pb", "PriceBook"):
                    pb = val.get(pb_key)
                    if pb is not None:
                        try:
                            bv = float(pb)
                            if bv > 0:
                                pb_values.append(bv)
                        except (ValueError, TypeError):
                            pass
                        break

    # Pattern 2: data.historicalRatio (list of dicts)
    hist = data.get("historicalRatio", data.get("historical_ratio", []))
    if isinstance(hist, list):
        for entry in hist:
            if not isinstance(entry, dict):
                continue
            pe = entry.get("PE") or entry.get("pe") or entry.get("PER")
            pb = entry.get("PBV") or entry.get("pb") or entry.get("PB")
            if pe is not None:
                try:
                    pv = float(pe)
                    if pv > 0:
                        pe_values.append(pv)
                except (ValueError, TypeError):
                    pass
            if pb is not None:
                try:
                    bv = float(pb)
                    if bv > 0:
                        pb_values.append(bv)
                except (ValueError, TypeError):
                    pass

    # Pattern 3: data.closure_fin_items_results[group].fin_name_results[item].fitem
    # Each group may represent one fiscal year (confirmed live format, year_limit=10).
    # _parse_stockbit_flat() overwrites duplicate field names so yearly history is lost
    # there; this pattern reads across ALL groups to reconstruct the series.
    _PE_SUBSTRINGS = ("P/E", "PRICE EARNINGS", "PRICE/EARNINGS", "PRICE TO EARNINGS")
    # "PER" matched as prefix only — bare substring also matches "Dividen Per Saham"
    _PE_PREFIXES = ("PER", "CURRENT PER", "TRAILING PER", "FORWARD PER")
    _PB_SUBSTRINGS = ("PBV", "P/B", "PRICE BOOK", "PRICE/BOOK", "PRICE TO BOOK")
    need_pe = len(pe_values) < 3
    need_pb = len(pb_values) < 3
    if need_pe or need_pb:
        for group in data.get("closure_fin_items_results", []):
            group_pe: float | None = None
            group_pb: float | None = None
            for item in group.get("fin_name_results", []):
                fitem = item.get("fitem", {})
                name = str(fitem.get("name", "")).upper().strip()
                raw_val = (
                    str(fitem.get("value", "")).replace(",", "").replace("%", "").strip()
                )
                if not raw_val or raw_val in ("-", "N/A", "NULL", "NONE"):
                    continue
                try:
                    v = float(raw_val)
                except (ValueError, TypeError):
                    continue
                pe_name_match = any(k in name for k in _PE_SUBSTRINGS) or any(
                    name == p or name.startswith(p + " ") or name.startswith(p + "(")
                    for p in _PE_PREFIXES
                )
                if need_pe and group_pe is None and pe_name_match and 0 < v < 200:
                    group_pe = v
                if need_pb and group_pb is None and any(k in name for k in _PB_SUBSTRINGS) and 0 < v < 100:
                    group_pb = v
            if need_pe and group_pe is not None:
                pe_values.append(group_pe)
            if need_pb and group_pb is not None:
                pb_values.append(group_pb)

    # Start with hardcoded defaults, override with API-derived medians
    result = get_historical_multiples(ticker)
    if len(pe_values) >= 3:
        sorted_pe = sorted(pe_values)
        result["pe"] = round(sorted_pe[len(sorted_pe) // 2], 1)
        result["pe_values"] = pe_values
    if len(pb_values) >= 3:
        sorted_pb = sorted(pb_values)
        result["pb"] = round(sorted_pb[len(sorted_pb) // 2], 1)
        result["pb_values"] = pb_values

    return result


def _compute_valuation_band_context(
    current_pe: float,
    current_pb: float,
    pe_values: list[float],
    pb_values: list[float],
) -> str | None:
    """Return a band section string showing current PE/PBV vs own historical range.

    Returns None when insufficient yearly data (< 3 points or trivially tight range).
    Only uses real API-derived data — never falls back to hardcoded estimates.
    """

    def _band(current: float, series: list[float]) -> tuple[float | None, str, float, float]:
        if len(series) < 3 or current <= 0:
            return None, "INSUFFICIENT_DATA", 0.0, 0.0
        lo, hi = min(series), max(series)
        if hi - lo < 0.5:  # range too tight to be meaningful
            return None, "INSUFFICIENT_DATA", lo, hi
        pct = sum(1 for v in series if v <= current) / len(series) * 100.0
        if pct <= 25:
            label = "HISTORICALLY_CHEAP"
        elif pct <= 50:
            label = "BELOW_AVG"
        elif pct <= 75:
            label = "ABOVE_AVG"
        else:
            label = "HISTORICALLY_EXPENSIVE"
        return round(pct, 0), label, lo, hi

    pe_pct, pe_label, pe_lo, pe_hi = _band(current_pe, pe_values)
    pb_pct, pb_label, pb_lo, pb_hi = _band(current_pb, pb_values)

    if pe_pct is None and pb_pct is None:
        return None

    n_pe = len(pe_values)
    n_pb = len(pb_values)
    n = max(n_pe, n_pb)
    parts: list[str] = []
    if pe_pct is not None:
        parts.append(
            f"  PE  {current_pe:.1f}x → {pe_pct:.0f}th pct of {n_pe}-yr range"
            f" [{pe_lo:.1f}–{pe_hi:.1f}x] → {pe_label}"
        )
    if pb_pct is not None:
        parts.append(
            f"  PBV {current_pb:.2f}x → {pb_pct:.0f}th pct of {n_pb}-yr range"
            f" [{pb_lo:.2f}–{pb_hi:.2f}x] → {pb_label}"
        )

    return (
        "── HISTORICAL VALUATION BAND (C3) ──────────────────────────────\n"
        + "\n".join(parts)
        + f"\n  (source: {n} years of API data; self-relative percentile rank vs own history)\n"
        + "─" * 65
    )


def compute_52w_range_signal(
    current_price: float,
    high_52w: float,
    low_52w: float,
) -> str | None:
    """52-week price range position signal for swing trade context.

    Returns a formatted context string, or None when data is unavailable.
    Percentile expresses where the current price sits inside [low_52w, high_52w].

    Labels (swing-trade oriented):
      NEAR_52W_HIGH  — >= 80th pct: limited upside, potential exhaustion zone
      ABOVE_MID      — >= 55th pct: above midpoint, momentum-neutral
      BELOW_MID      — >= 25th pct: below midpoint, mean-reversion potential
      NEAR_52W_LOW   —  < 25th pct: deep discount zone, watch for reversal catalyst
    """
    if not (current_price > 0 and high_52w > 0 and low_52w > 0):
        return None
    rng = high_52w - low_52w
    if rng <= 0:
        return None
    pct = round((current_price - low_52w) / rng * 100.0, 1)
    midpoint = round((high_52w + low_52w) / 2.0, 0)
    if pct >= 80.0:
        label = "NEAR_52W_HIGH"
    elif pct >= 55.0:
        label = "ABOVE_MID"
    elif pct >= 25.0:
        label = "BELOW_MID"
    else:
        label = "NEAR_52W_LOW"
    return (
        f"52W RANGE SIGNAL: {label} — "
        f"harga saat ini di persentil ke-{pct:.1f} dari kisaran 52 minggu "
        f"(Low Rp {low_52w:,.0f} – High Rp {high_52w:,.0f}, Mid Rp {midpoint:,.0f})."
    )


# ---------------------------------------------------------------------------
# Convenience factory — satu baris dari API response ke report string
# ---------------------------------------------------------------------------


def build_fair_value_payload(
    api_response: dict,
    ticker: str,
    current_price: float,
) -> tuple[str, dict]:
    multiples = extract_historical_multiples(api_response, ticker)
    stats = extract_keystats(api_response, ticker=ticker)

    if multiples.get("pe") is not None:
        stats.historical_pe_avg = multiples["pe"]
    if multiples.get("pb") is not None:
        stats.historical_pb_avg = multiples["pb"]
    if multiples.get("cost_of_equity") is not None:
        stats.cost_of_equity = multiples["cost_of_equity"]
    if multiples.get("growth_rate") is not None:
        stats.growth_rate = multiples["growth_rate"]

    stats.current_price = current_price
    if stats.ocf_per_share <= 0 and stats.operating_cash_flow_ttm > 0:
        if stats.shares_outstanding > 0:
            stats.ocf_per_share = stats.operating_cash_flow_ttm / stats.shares_outstanding
    if stats.ocf_price_ratio <= 0:
        if stats.ocf_per_share > 0 and current_price > 0:
            stats.ocf_price_ratio = stats.ocf_per_share / current_price
        else:
            stats.ocf_price_ratio = calculate_ocf_price_ratio(
                stats.operating_cash_flow_ttm,
                stats.shares_outstanding,
                current_price,
            )
    stats.profitability_factor_score = calculate_profitability_score(
        {"rnoa": stats.rnoa, "roa": stats.roa}
    )

    # ── EPS back-calculation from PE × price ──────────────────────────────
    # The Stockbit closure_fin_items endpoint often includes PE but not EPS
    # directly in the visible section.  If EPS is still 0 but we have PE
    # and the live price, we can back-calculate a reasonable EPS estimate.
    eps_derived = False
    if stats.eps_ttm == 0.0 and stats.raw_pe_current > 0 and current_price > 0:
        stats.eps_ttm = round(current_price / stats.raw_pe_current, 2)
        eps_derived = True
        logger.info(
            "[FairValue] {}: EPS back-calculated from PE (derived, circular): {} / {} = {}",
            ticker,
            current_price,
            stats.raw_pe_current,
            stats.eps_ttm,
        )

    calc = FairValueCalculator(stats, eps_derived=eps_derived)
    result = calc.fair_value_weighted()
    report = calc.build_report(current_price=current_price)

    # ── C3: Historical valuation band ─────────────────────────────────────
    band_ctx = _compute_valuation_band_context(
        current_pe=stats.raw_pe_current,
        current_pb=stats.raw_pb_current,
        pe_values=multiples.get("pe_values", []),
        pb_values=multiples.get("pb_values", []),
    )
    if band_ctx:
        report = report + "\n" + band_ctx
    result["valuation_band_context"] = band_ctx

    if eps_derived:
        result["eps_source"] = "derived_from_pe"

    fv = result["fair_value"]
    if fv is None:
        logger.warning(
            "[FairValue] {}: fair value tidak dapat dikalkulasi — semua metode gagal",
            ticker,
        )

    # ── Data-quality gate ─────────────────────────────────────────────────
    # A fair value built on thin or broken inputs must not anchor the trade
    # envelope or a CIO "undervalued" narrative (NZIA: 1/3 methods valid → FV
    # Rp 417 vs spot Rp 177 became the BUY catalyst; INDO: net margin 131%
    # from a revenue/net-income mismatch). The per-method numbers stay
    # visible in the report text; only the anchor fields are nulled, so the
    # envelope falls back to its swing cap and risk_overvalued never fires
    # off a garbage anchor. Note: the gate keys off structured signals — the
    # LLM's NEEDS_RECONCILIATION label is prose, net_margin > 1.0 is its
    # deterministic equivalent (post-normalisation, margins land in (1, ∞)
    # only when net income exceeds revenue).
    quality_reasons: list[str] = []
    if result.get("confidence") == "LOW":
        quality_reasons.append("fv_methods_lt_2")
    if stats.net_margin > 1.0:
        quality_reasons.append("net_margin_gt_100pct")
    if quality_reasons and fv is not None:
        logger.warning(
            "[FairValue] {}: anchor ditolak quality gate ({}) — FV {} di-drop",
            ticker,
            ", ".join(quality_reasons),
            fv,
        )
        result = {
            **result,
            "fair_value": None,
            "fair_value_base": None,
            "fair_value_low": None,
            "fair_value_high": None,
            "range_pct": None,
            "risk_overvalued": False,
            "margin_of_safety_pct": None,
            "valuation_verdict": "QUALITY_REJECTED",
            "fv_quality_rejected": True,
            "fv_quality_reasons": quality_reasons,
            "valuation_band_context": None,
        }
        report += (
            "\n⚠️ FAIR VALUE QUALITY GATE: estimasi FV di atas TIDAK dipakai "
            f"sebagai anchor valuasi ({', '.join(quality_reasons)}). Jangan "
            "mengutip FV atau valuation gap sebagai fakta; perlakukan valuasi "
            "sebagai UNKNOWN."
        )

    return report, result


def build_fair_value_report(
    api_response: dict,
    ticker: str,
    current_price: float,
) -> tuple[str, float | None]:
    report, result = build_fair_value_payload(api_response, ticker, current_price)
    return report, result["fair_value"]


def check_valuation_disagreement(
    graham_fv: float | None,
    debate_fv: float | None,
    disagreement_threshold: float = 0.25,
) -> dict:
    """Bandingkan FV dari screener (Graham Number) vs debate engine (FairValueCalculator).

    Tidak mereconcile angka — hanya membuat disagreement terlihat di output.
    Threshold default 25%: selisih di atas ini dianggap SIGNIFICANT.

    Returns dict dengan keys:
      valuation_disagreement : "SIGNIFICANT" | "ALIGNED" | "NOT_COMPARABLE"
      disagreement_pct       : float | None
      valuation_note         : str
    """
    if not graham_fv or not debate_fv or graham_fv <= 0 or debate_fv <= 0:
        return {
            "valuation_disagreement": "NOT_COMPARABLE",
            "disagreement_pct": None,
            "valuation_note": "Salah satu atau kedua FV tidak tersedia untuk perbandingan.",
        }

    diff_pct = abs(graham_fv - debate_fv) / min(graham_fv, debate_fv)

    if diff_pct > disagreement_threshold:
        return {
            "valuation_disagreement": "SIGNIFICANT",
            "disagreement_pct": round(diff_pct * 100, 1),
            "valuation_note": (
                f"Graham Number (screener): Rp{graham_fv:,.0f} vs "
                f"FairValueCalculator (debate engine): Rp{debate_fv:,.0f} — "
                f"selisih {diff_pct:.1%}. Kemungkinan disebabkan sifat siklikal "
                "earnings atau perbedaan metode valuasi sektoral. "
                "Pertimbangkan metode mana yang lebih relevan untuk sektor ini."
            ),
        }
    return {
        "valuation_disagreement": "ALIGNED",
        "disagreement_pct": round(diff_pct * 100, 1),
        "valuation_note": "",
    }
