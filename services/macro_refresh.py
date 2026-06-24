"""
Fetch and cache live macro rates for WACC/CAPM calibration.

Source: World Bank Open Data API (free, no auth required).
  - FR.INR.DPST = Indonesia deposit rate (annual, ~2 year lag)
  - SBN 10Y estimate = deposit_rate + _SBN_DEPOSIT_SPREAD (1.7% historical avg)

Cache: output/macro_rates.json, TTL = 7 days (data is annual, refreshing daily is wasteful).

To wire a live source (e.g. Stockbit macro endpoint once known):
  set STOCKBIT_SBN_URL=https://exodus.stockbit.com/macro/bi-rate  # not yet confirmed
  The fetcher will try that URL first via StockbitApiClient before falling back to World Bank.

Consumed by: services/fair_value_calculator._capm_cost_of_equity()
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from core.settings import get_settings
from utils.logger_config import logger


_MACRO_CACHE_PATH = Path("output/macro_rates.json")
_MACRO_CACHE_TTL_DAYS: int = 7

# Historical spread between Indonesia SBN 10Y yield and bank deposit rate.
# Source: BI/DJPPR data 2019-2025 average.
_SBN_DEPOSIT_SPREAD: float = 0.017

_WB_DEPOSIT_URL = (
    "https://api.worldbank.org/v2/country/ID/indicator/FR.INR.DPST"
    "?format=json&per_page=3&mrv=3"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_worldbank_deposit_rate() -> float | None:
    """Return the most recent Indonesia deposit rate from World Bank (0.0–0.20 range)."""
    try:
        resp = requests.get(_WB_DEPOSIT_URL, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        if not (isinstance(data, list) and len(data) > 1):
            return None
        for record in data[1]:
            val = record.get("value")
            if val is not None:
                rate = float(val) / 100.0  # World Bank returns % e.g. 5.43
                logger.info(
                    "[MacroRefresh] World Bank deposit rate ({} data): {:.4f}",
                    record.get("date", "?"),
                    rate,
                )
                return rate
    except Exception as exc:
        logger.warning("[MacroRefresh] World Bank fetch failed: {}", exc)
    return None


def _fetch_stockbit_sbn(client: Any) -> float | None:
    """
    Try a Stockbit macro endpoint for SBN 10Y yield.
    Only called if STOCKBIT_SBN_URL env var is set.
    Returns decimal rate (e.g. 0.0714) or None.
    """
    url = os.environ.get("STOCKBIT_SBN_URL", "").strip()
    if not url:
        return None
    try:
        raw = client.get(url)
        # Adjust parsing once the actual Stockbit response shape is known.
        val = raw.get("data", {}).get("value")
        if val is not None:
            rate = float(val)
            if rate > 1:
                rate = rate / 100.0  # convert % to decimal
            logger.info("[MacroRefresh] Stockbit SBN 10Y: {:.4f}", rate)
            return rate
    except Exception as exc:
        logger.debug("[MacroRefresh] Stockbit SBN fetch failed: {}", exc)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_cached_macro_rates() -> dict | None:
    """
    Return cached macro rates if cache is ≤TTL days old, else None.
    Dict keys: sbn_10y, deposit_rate, source, fetched_at.
    """
    try:
        if not _MACRO_CACHE_PATH.exists():
            return None
        raw = json.loads(_MACRO_CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(raw.get("fetched_at", "1970-01-01"))
        age_days = (datetime.now(timezone.utc) - fetched_at.replace(tzinfo=timezone.utc)).days
        if age_days <= _MACRO_CACHE_TTL_DAYS:
            return raw
    except Exception as exc:
        logger.debug("[MacroRefresh] load_cached_macro_rates failed: {}", exc)
    return None


def refresh_macro_rates(stockbit_client: Any | None = None) -> dict:
    """
    Fetch current macro rates and write to output/macro_rates.json.

    Returns dict with keys: sbn_10y, deposit_rate, source, fetched_at.
    On failure falls back to settings values — never raises.
    """
    s = get_settings()
    deposit_rate: float | None = None
    sbn_10y: float | None = None
    source = "settings_fallback"

    # Step 1: try Stockbit if URL configured
    if stockbit_client is not None:
        sbn_10y = _fetch_stockbit_sbn(stockbit_client)
        if sbn_10y is not None:
            source = "stockbit"

    # Step 2: fall back to World Bank deposit rate + spread
    if sbn_10y is None:
        deposit_rate = _fetch_worldbank_deposit_rate()
        if deposit_rate is not None:
            sbn_10y = round(deposit_rate + _SBN_DEPOSIT_SPREAD, 4)
            source = "worldbank_deposit+spread"
            logger.info(
                "[MacroRefresh] SBN 10Y estimated: {:.4f} "
                "(deposit {:.4f} + spread {:.4f})",
                sbn_10y,
                deposit_rate,
                _SBN_DEPOSIT_SPREAD,
            )

    # Step 3: final fallback
    if sbn_10y is None:
        logger.warning(
            "[MacroRefresh] All sources failed — using settings.SBN_10Y_YIELD={:.4f}",
            s.SBN_10Y_YIELD,
        )
        sbn_10y = s.SBN_10Y_YIELD
        source = "settings_fallback"

    result = {
        "sbn_10y": sbn_10y,
        "deposit_rate": deposit_rate,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        _MACRO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MACRO_CACHE_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
        logger.info(
            "[MacroRefresh] Cached: SBN 10Y={:.4f} source={}",
            sbn_10y,
            source,
        )
    except Exception as exc:
        logger.warning("[MacroRefresh] Failed to write macro cache: {}", exc)

    return result


def get_live_sbn_10y(
    *,
    refresh_if_stale: bool = False,
    stockbit_client: Any | None = None,
) -> float:
    """
    Return the best available SBN 10Y yield estimate.
    Reads from cache if fresh. If refresh_if_stale=True, refreshes the cache
    before falling back to settings.SBN_10Y_YIELD.
    """
    cached = load_cached_macro_rates()
    if cached:
        val = cached.get("sbn_10y")
        if val is not None and 0.01 < float(val) < 0.30:  # sanity: 1%–30%
            return float(val)
    if refresh_if_stale:
        refreshed = refresh_macro_rates(stockbit_client=stockbit_client)
        val = refreshed.get("sbn_10y")
        if val is not None and 0.01 < float(val) < 0.30:
            return float(val)
    return get_settings().SBN_10Y_YIELD
