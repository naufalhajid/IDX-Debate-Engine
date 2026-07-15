from __future__ import annotations

import asyncio
import math
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from utils.exdate_scanner import (
    CRITICAL_WINDOW_DAYS,
    WARNING_WINDOW_DAYS,
    ExDateInfo,
)
from utils.logger_config import logger
from utils.market_snapshot import (
    IDX_TIMEZONE,
    MarketSnapshot,
    download_market_snapshot,
)
from utils.ticker import normalize_idx_ticker


MarketData = dict[str, Any]
CacheKey = tuple[str, date]

IDX_MARKET_OPEN = time(9, 0)
IDX_MARKET_CLOSE = time(16, 15)
MARKET_OPEN_CACHE_TTL = timedelta(minutes=15)
MARKET_CLOSED_CACHE_TTL = timedelta(hours=6)
PARTIAL_PROVIDER_ERROR_CACHE_TTL = timedelta(seconds=60)


@dataclass(frozen=True)
class _CacheEntry:
    data: MarketData
    fetched_at: datetime
    expires_at: datetime
    session_date: date
    run_generation: int
    key_generation: int


def _as_idx_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=IDX_TIMEZONE)
    return value.astimezone(IDX_TIMEZONE)


def _previous_weekday(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _next_session_open(now: datetime) -> datetime:
    local_now = _as_idx_datetime(now)
    candidate = local_now.date()
    if candidate.weekday() < 5 and local_now.time() < IDX_MARKET_OPEN:
        return datetime.combine(candidate, IDX_MARKET_OPEN, tzinfo=IDX_TIMEZONE)
    candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return datetime.combine(candidate, IDX_MARKET_OPEN, tzinfo=IDX_TIMEZONE)


def _idx_session_date(now: datetime) -> date:
    local_now = _as_idx_datetime(now)
    current = local_now.date()
    if current.weekday() >= 5:
        while current.weekday() >= 5:
            current -= timedelta(days=1)
        return current
    if local_now.time() < IDX_MARKET_OPEN:
        return _previous_weekday(current)
    return current


def _cache_expiry(now: datetime) -> datetime:
    local_now = _as_idx_datetime(now)
    is_weekday = local_now.weekday() < 5
    if is_weekday and IDX_MARKET_OPEN <= local_now.time() < IDX_MARKET_CLOSE:
        market_close = datetime.combine(
            local_now.date(),
            IDX_MARKET_CLOSE,
            tzinfo=IDX_TIMEZONE,
        )
        return min(local_now + MARKET_OPEN_CACHE_TTL, market_close)
    next_open = _next_session_open(local_now)
    return min(local_now + MARKET_CLOSED_CACHE_TTL, next_open)


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
        if math.isfinite(result) and result > 0:
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


def _fetch_yfinance_bundle(
    ticker: str,
    seeded_snapshot: MarketSnapshot | None = None,
) -> MarketData:
    yf = _get_yfinance()
    yf_ticker = yf.Ticker(f"{ticker}.JK")
    fetched_at = datetime.now(timezone.utc).isoformat()
    market_data: MarketData = {
        "history": None,
        "info": {},
        "fast_info": {},
        "calendar": None,
        "dividends": None,
        "source": "yfinance",
        "fetched_at": fetched_at,
        "history_as_of": fetched_at,
        "market_snapshot": None,
        "_market_snapshot_object": None,
        "snapshot_id": None,
        "data_hash": None,
        "history_status": "not_fetched",
        "history_reason_codes": [],
        "history_error_type": None,
        "history_error": None,
        "component_status": {
            "history": "not_fetched",
            "info": "not_fetched",
            "fast_info": "not_fetched",
            "calendar": "not_fetched",
            "dividends": "not_fetched",
        },
    }

    try:
        snapshot = seeded_snapshot or download_market_snapshot(
            ticker,
            downloader=yf.download,
        )
        market_data["history"] = snapshot.history_copy()
        market_data["market_snapshot"] = snapshot.provenance()
        market_data["_market_snapshot_object"] = snapshot
        market_data["snapshot_id"] = snapshot.snapshot_id
        market_data["data_hash"] = snapshot.data_hash
        market_data["history_status"] = snapshot.status
        market_data["component_status"]["history"] = snapshot.status
        market_data["history_reason_codes"] = list(snapshot.reason_codes)
        history = market_data["history"]
        if history is not None and len(history) > 0:
            last_index = history.index[-1]
            history_as_of = pd.Timestamp(last_index)
            if history_as_of.tzinfo is None:
                history_as_of = history_as_of.tz_localize(timezone.utc)
            else:
                history_as_of = history_as_of.tz_convert(timezone.utc)
            market_data["history_as_of"] = history_as_of.isoformat()
    except Exception as exc:
        market_data["history_status"] = "provider_error"
        market_data["history_error_type"] = type(exc).__name__
        market_data["history_error"] = str(exc)
        market_data["component_status"]["history"] = "provider_error"
        logger.warning("[MarketData] {} history fetch failed: {}", ticker, exc)

    try:
        market_data["info"] = yf_ticker.info or {}
        market_data["component_status"]["info"] = (
            "ready" if market_data["info"] else "empty"
        )
    except Exception as exc:
        market_data["component_status"]["info"] = "provider_error"
        logger.warning("[MarketData] {} info fetch failed: {}", ticker, exc)

    try:
        market_data["fast_info"] = yf_ticker.fast_info or {}
        market_data["component_status"]["fast_info"] = (
            "ready" if market_data["fast_info"] else "empty"
        )
    except Exception as exc:
        market_data["component_status"]["fast_info"] = "provider_error"
        logger.warning("[MarketData] {} fast_info fetch failed: {}", ticker, exc)

    try:
        market_data["calendar"] = yf_ticker.calendar
        market_data["component_status"]["calendar"] = "ready"
    except Exception as exc:
        market_data["component_status"]["calendar"] = "provider_error"
        logger.warning("[MarketData] {} calendar fetch failed: {}", ticker, exc)

    try:
        market_data["dividends"] = yf_ticker.dividends
        market_data["component_status"]["dividends"] = "ready"
    except Exception as exc:
        market_data["component_status"]["dividends"] = "provider_error"
        logger.warning("[MarketData] {} dividends fetch failed: {}", ticker, exc)

    market_data["current_price"] = derive_current_price(market_data)
    return market_data


class TickerDataCache:
    """Session-aware async cache with one shared fetch per ticker/session."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._cache: dict[CacheKey, _CacheEntry] = {}
        self._seeded_snapshots: dict[CacheKey, MarketSnapshot] = {}
        self._inflight: dict[CacheKey, asyncio.Task[MarketData]] = {}
        self._key_generations: dict[CacheKey, int] = {}
        self._run_generation = 0
        self._run_session_date: date | None = None
        self._clock = clock or (lambda: datetime.now(IDX_TIMEZONE))
        self._lock = asyncio.Lock()

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def _now(self) -> datetime:
        return _as_idx_datetime(self._clock())

    def _active_session_date(self, now: datetime) -> date:
        return self._run_session_date or _idx_session_date(now)

    @staticmethod
    def _normalise_ticker(ticker: str) -> str:
        return normalize_idx_ticker(ticker)

    async def seed_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Pin exact OHLCV for the active run and invalidate a competing fetch."""
        session_date = self._run_session_date or snapshot.requested_end
        if (
            self._run_session_date is not None
            and snapshot.requested_end != session_date
        ):
            logger.warning(
                "[MarketDataCache] reason_code=stale_seed_snapshot ticker={} "
                "snapshot_requested_end={} run_session={}",
                self._normalise_ticker(snapshot.ticker),
                snapshot.requested_end.isoformat(),
                session_date.isoformat(),
            )
            raise ValueError(
                "reason_code=stale_seed_snapshot: snapshot requested_end "
                f"{snapshot.requested_end.isoformat()} does not match run session "
                f"{session_date.isoformat()}"
            )
        key = (self._normalise_ticker(snapshot.ticker), session_date)
        task: asyncio.Task[MarketData] | None = None
        async with self._lock:
            if self._run_session_date is None:
                self._run_session_date = session_date
            self._key_generations[key] = self._key_generations.get(key, 0) + 1
            self._seeded_snapshots[key] = snapshot
            self._cache.pop(key, None)
            task = self._inflight.pop(key, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _load_and_cache(
        self,
        key: CacheKey,
        seeded_snapshot: MarketSnapshot | None,
        *,
        run_generation: int,
        key_generation: int,
    ) -> MarketData:
        current_task = asyncio.current_task()
        try:
            data = await asyncio.to_thread(
                _fetch_yfinance_bundle,
                key[0],
                seeded_snapshot,
            )
            fetched_at = self._now()
            cacheable = data.get("history_status") != "provider_error"
            component_status = data.get("component_status")
            component_status = (
                component_status if isinstance(component_status, dict) else {}
            )
            partial_error_components = sorted(
                name
                for name, status in component_status.items()
                if name in {"info", "fast_info"}
                and status in {"provider_error", "empty"}
                or name in {"calendar", "dividends"}
                and status == "provider_error"
            )
            if not cacheable:
                expires_at = fetched_at
            elif partial_error_components:
                expires_at = min(
                    fetched_at + PARTIAL_PROVIDER_ERROR_CACHE_TTL,
                    _cache_expiry(fetched_at),
                )
            else:
                expires_at = _cache_expiry(fetched_at)
            ttl_seconds = max(
                0,
                int((expires_at - fetched_at).total_seconds()),
            )
            data["cache_ticker"] = key[0]
            data["cache_session_date"] = key[1].isoformat()
            data["cache_fetched_at"] = fetched_at.isoformat()
            data["cache_expires_at"] = expires_at.isoformat()
            data["cache_ttl_seconds"] = ttl_seconds
            data["cache_key"] = f"{key[0]}:{key[1].isoformat()}"
            data["cache_partial_error_components"] = partial_error_components
            if not cacheable:
                data["cache_policy"] = "provider_error_not_cached"
            elif partial_error_components:
                data["cache_policy"] = "partial_provider_error_60s"
            else:
                data["cache_policy"] = (
                    "market_open_15m"
                    if ttl_seconds <= int(MARKET_OPEN_CACHE_TTL.total_seconds())
                    and (
                        fetched_at.weekday() < 5
                        and IDX_MARKET_OPEN
                        <= fetched_at.time()
                        < IDX_MARKET_CLOSE
                    )
                    else "market_closed_6h_or_next_session"
                )
            entry = _CacheEntry(
                data=data,
                fetched_at=fetched_at,
                expires_at=expires_at,
                session_date=key[1],
                run_generation=run_generation,
                key_generation=key_generation,
            )
            async with self._lock:
                if (
                    self._run_generation == run_generation
                    and self._key_generations.get(key, 0) == key_generation
                ):
                    snapshot = data.get("_market_snapshot_object")
                    if (
                        isinstance(snapshot, MarketSnapshot)
                        and key not in self._seeded_snapshots
                    ):
                        self._seeded_snapshots[key] = snapshot
                    if cacheable:
                        self._cache[key] = entry
            return data
        finally:
            async with self._lock:
                if self._inflight.get(key) is current_task:
                    self._inflight.pop(key, None)

    async def prefetch(self, ticker: str) -> MarketData:
        normalized = self._normalise_ticker(ticker)
        now = self._now()
        key = (normalized, self._active_session_date(now))
        stale_tasks: list[asyncio.Task[MarketData]] = []

        async with self._lock:
            known_keys = (
                set(self._cache)
                | set(self._seeded_snapshots)
                | set(self._inflight)
            )
            stale_keys = sorted(
                candidate
                for candidate in known_keys
                if candidate[0] == normalized and candidate[1] != key[1]
            )
            for stale_key in stale_keys:
                logger.warning(
                    "[MarketDataCache] reason_code=stale_session_cache_ignored "
                    "ticker={} cached_session={} requested_session={}",
                    normalized,
                    stale_key[1].isoformat(),
                    key[1].isoformat(),
                )
                self._cache.pop(stale_key, None)
                self._seeded_snapshots.pop(stale_key, None)
                self._key_generations[stale_key] = (
                    self._key_generations.get(stale_key, 0) + 1
                )
                stale_task = self._inflight.pop(stale_key, None)
                if stale_task is not None:
                    stale_tasks.append(stale_task)

            cached = self._cache.get(key)
            if cached is not None and now < cached.expires_at:
                task = None
                result = cached.data
            else:
                if cached is not None:
                    self._cache.pop(key, None)
                result = None
                task = self._inflight.get(key)
                if task is None:
                    run_generation = self._run_generation
                    key_generation = self._key_generations.get(key, 0)
                    seeded_snapshot = self._seeded_snapshots.get(key)
                    task = asyncio.create_task(
                        self._load_and_cache(
                            key,
                            seeded_snapshot,
                            run_generation=run_generation,
                            key_generation=key_generation,
                        )
                    )
                    self._inflight[key] = task

        for stale_task in stale_tasks:
            stale_task.cancel()
        if stale_tasks:
            await asyncio.gather(*stale_tasks, return_exceptions=True)
        if result is not None:
            return result
        assert task is not None
        return await asyncio.shield(task)

    async def clear_run_cache(
        self,
        *,
        run_session_date: date | None = None,
    ) -> None:
        """Clear cache and freeze one immutable calendar session for the run."""
        frozen_session = run_session_date or self._now().date()
        async with self._lock:
            self._run_generation += 1
            self._run_session_date = frozen_session
            tasks = list(self._inflight.values())
            self._cache.clear()
            self._seeded_snapshots.clear()
            self._inflight.clear()
            self._key_generations.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.debug(
            "[MarketDataCache] Cleared run-scoped cache; run_session_date={}.",
            frozen_session.isoformat(),
        )


DEFAULT_MARKET_DATA_CACHE = TickerDataCache()
_ACTIVE_MARKET_DATA_CACHE: ContextVar[TickerDataCache | None] = ContextVar(
    "active_market_data_cache",
    default=None,
)


def get_active_market_data_cache() -> TickerDataCache:
    """Return the cache isolated to this pipeline context, or the shared default."""
    return _ACTIVE_MARKET_DATA_CACHE.get() or DEFAULT_MARKET_DATA_CACHE


async def prefetch_market_data(ticker: str) -> MarketData:
    return await get_active_market_data_cache().prefetch(ticker)


async def clear_run_cache(*, run_session_date: date | None = None) -> None:
    """Create a cache isolated to this async pipeline context."""
    run_cache = TickerDataCache()
    _ACTIVE_MARKET_DATA_CACHE.set(run_cache)
    await run_cache.clear_run_cache(
        run_session_date=run_session_date
    )


async def seed_market_snapshot(snapshot: MarketSnapshot) -> None:
    """Seed the current pipeline run from a verified snapshot artifact."""
    await get_active_market_data_cache().seed_snapshot(snapshot)


async def seed_market_snapshots(snapshots: list[MarketSnapshot]) -> None:
    for snapshot in snapshots:
        await seed_market_snapshot(snapshot)


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
