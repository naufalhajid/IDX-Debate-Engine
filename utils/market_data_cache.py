from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from utils.exdate_scanner import (
    CRITICAL_WINDOW_DAYS,
    WARNING_WINDOW_DAYS,
    ExDateInfo,
)
from utils.logger_config import logger


MarketData = dict[str, Any]


def _get_yfinance():
    import yfinance as yf

    return yf


def _normalise_history(history: Any) -> Any:
    """Return a history frame with yfinance MultiIndex columns flattened."""
    if history is None:
        return history
    if isinstance(getattr(history, "columns", None), pd.MultiIndex):
        history = history.copy()
        history.columns = history.columns.get_level_values(0)
    return history


def _first_float(*values: Any) -> float:
    for value in values:
        if value in (None, ""):
            continue
        try:
            result = float(value)
        except (TypeError, ValueError):
            continue
        if result > 0:
            return result
    return 0.0


def derive_current_price(market_data: MarketData) -> float:
    """Derive the best current price from cached market data."""
    history = _normalise_history(market_data.get("history"))
    if history is not None and len(history) > 0 and "Close" in history.columns:
        close_prices = history["Close"].dropna()
        if len(close_prices) > 0:
            return _first_float(close_prices.iloc[-1])

    fast_info = market_data.get("fast_info") or {}
    info = market_data.get("info") or {}
    return _first_float(
        _safe_get(fast_info, "last_price"),
        _safe_get(fast_info, "lastPrice"),
        _safe_get(fast_info, "regular_market_price"),
        _safe_get(fast_info, "regularMarketPrice"),
        info.get("currentPrice"),
        info.get("regularMarketPrice"),
        info.get("previousClose"),
    )


def _safe_get(mapping: Any, key: str) -> Any:
    try:
        return mapping.get(key)
    except AttributeError:
        try:
            return getattr(mapping, key)
        except AttributeError:
            return None


def _fetch_yfinance_bundle(ticker: str) -> MarketData:
    yf_ticker = _get_yfinance().Ticker(f"{ticker}.JK")
    market_data: MarketData = {
        "history": None,
        "info": {},
        "fast_info": {},
        "calendar": None,
        "dividends": None,
        "source": "yfinance",
    }

    try:
        market_data["history"] = _normalise_history(yf_ticker.history(period="1y"))
    except Exception as exc:
        logger.warning("[MarketData] {} history fetch failed: {}", ticker, exc)

    try:
        market_data["info"] = yf_ticker.info or {}
    except Exception as exc:
        logger.warning("[MarketData] {} info fetch failed: {}", ticker, exc)

    try:
        market_data["fast_info"] = yf_ticker.fast_info or {}
    except Exception as exc:
        logger.warning("[MarketData] {} fast_info fetch failed: {}", ticker, exc)

    try:
        market_data["calendar"] = yf_ticker.calendar
    except Exception as exc:
        logger.warning("[MarketData] {} calendar fetch failed: {}", ticker, exc)

    try:
        market_data["dividends"] = yf_ticker.dividends
    except Exception as exc:
        logger.warning("[MarketData] {} dividends fetch failed: {}", ticker, exc)

    market_data["current_price"] = derive_current_price(market_data)
    return market_data


class TickerDataCache:
    """Small async cache so each ticker uses one yfinance bundle per run."""

    def __init__(self) -> None:
        self._cache: dict[str, MarketData] = {}
        self._lock = asyncio.Lock()

    async def prefetch(self, ticker: str) -> MarketData:
        key = ticker.strip().upper()
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        data = await asyncio.to_thread(_fetch_yfinance_bundle, key)

        async with self._lock:
            self._cache[key] = data
            return data


DEFAULT_MARKET_DATA_CACHE = TickerDataCache()


async def prefetch_market_data(ticker: str) -> MarketData:
    return await DEFAULT_MARKET_DATA_CACHE.prefetch(ticker)


def scan_exdate_from_market_data(
    ticker: str,
    market_data: MarketData,
    current_price: float = 0.0,
) -> ExDateInfo:
    """Compute ex-date risk from a cached yfinance bundle without refetching."""
    clear: ExDateInfo = {
        "has_upcoming_exdate": False,
        "ex_date": None,
        "days_until_exdate": None,
        "div_per_share": None,
        "div_yield_pct": None,
        "risk_tier": "CLEAR",
        "expected_drop_rp": None,
        "source": "cached_yfinance",
    }

    try:
        cal = market_data.get("calendar")
        ex_date_ts = None
        if isinstance(cal, dict):
            ex_date_ts = cal.get("Ex-Dividend Date")
        elif isinstance(cal, pd.DataFrame) and "Ex-Dividend Date" in cal.index:
            ex_date_ts = cal.loc["Ex-Dividend Date"].iloc[0]

        if ex_date_ts is None:
            return clear

        if hasattr(ex_date_ts, "date"):
            ex_date = ex_date_ts.date()
        else:
            ex_date = pd.Timestamp(ex_date_ts).date()

        days_until = (ex_date - datetime.now(timezone.utc).date()).days
        if days_until < 0:
            return clear

        div_per_share: float | None = None
        divs = market_data.get("dividends")
        if divs is not None and len(divs) > 0:
            div_per_share = float(divs.iloc[-1])

        div_yield_pct: float | None = None
        if div_per_share and current_price > 0:
            div_yield_pct = round((div_per_share / current_price) * 100, 2)

        if days_until <= CRITICAL_WINDOW_DAYS:
            risk_tier = "CRITICAL"
        elif days_until <= WARNING_WINDOW_DAYS:
            risk_tier = "WARNING"
        else:
            risk_tier = "CLEAR"

        return {
            "has_upcoming_exdate": risk_tier != "CLEAR",
            "ex_date": str(ex_date) if risk_tier != "CLEAR" else None,
            "days_until_exdate": days_until if risk_tier != "CLEAR" else None,
            "div_per_share": div_per_share,
            "div_yield_pct": div_yield_pct,
            "risk_tier": risk_tier,
            "expected_drop_rp": div_per_share if risk_tier != "CLEAR" else None,
            "source": "cached_yfinance",
        }
    except Exception as exc:
        logger.warning("[ExDate] {} cached scan failed: {}", ticker, exc)
        return clear
