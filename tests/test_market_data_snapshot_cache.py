from __future__ import annotations

import asyncio
import threading
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from utils import market_data_cache as mdc
from utils.market_snapshot import IDX_TIMEZONE, build_market_snapshot


def _snapshot(
    *,
    rows: int = 5,
    minimum: int = 1,
    requested_end: date = date(2026, 7, 10),
):
    index = pd.bdate_range(end="2026-07-10", periods=rows)
    close = pd.Series(range(100, 100 + rows), index=index, dtype="float64")
    history = pd.DataFrame(
        {
            "Open": close - 1,
            "High": close + 2,
            "Low": close - 2,
            "Close": close,
            "Volume": 1_000.0,
        },
        index=index,
    )
    return build_market_snapshot(
        "BBCA",
        history,
        requested_start=date(2024, 11, 17),
        requested_end=requested_end,
        min_complete_bars=minimum,
        now=datetime.combine(
            requested_end,
            datetime.min.time().replace(hour=17),
            tzinfo=IDX_TIMEZONE,
        ),
    )


class _FakeTicker:
    @property
    def info(self):
        return {"currentPrice": 104.0, "marketCap": 1_000_000.0}

    @property
    def fast_info(self):
        return {"last_price": 104.0}

    @property
    def calendar(self):
        return {}

    @property
    def dividends(self):
        return pd.Series(dtype="float64")


@pytest.mark.asyncio
async def test_seeded_snapshot_skips_history_download_but_keeps_auxiliary_fetch(
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    download_calls = 0
    ticker_calls: list[str] = []

    def fail_download(*_args, **_kwargs):
        nonlocal download_calls
        download_calls += 1
        raise AssertionError("seeded snapshot must prevent an OHLC refetch")

    def fake_ticker(symbol: str):
        ticker_calls.append(symbol)
        return _FakeTicker()

    monkeypatch.setattr(
        mdc,
        "_get_yfinance",
        lambda: SimpleNamespace(Ticker=fake_ticker, download=fail_download),
    )
    cache = mdc.TickerDataCache()
    await cache.seed_snapshot(snapshot)

    data = await cache.prefetch("bbca")

    assert download_calls == 0
    assert ticker_calls == ["BBCA.JK"]
    assert data["_market_snapshot_object"] is snapshot
    assert data["snapshot_id"] == snapshot.snapshot_id
    assert data["history_status"] == "ready"
    assert data["info"]["marketCap"] == 1_000_000.0
    pd.testing.assert_frame_equal(data["history"], snapshot.history)


@pytest.mark.asyncio
async def test_insufficient_seed_is_distinct_from_provider_failure(monkeypatch) -> None:
    snapshot = _snapshot(rows=3, minimum=400)
    monkeypatch.setattr(
        mdc,
        "_get_yfinance",
        lambda: SimpleNamespace(
            Ticker=lambda _symbol: _FakeTicker(),
            download=lambda *_args, **_kwargs: pytest.fail("must not download"),
        ),
    )
    cache = mdc.TickerDataCache()
    await cache.seed_snapshot(snapshot)

    data = await cache.prefetch("BBCA")

    assert data["history_status"] == "insufficient_data"
    assert data["history_reason_codes"] == ["insufficient_complete_bars"]
    assert data["history_error"] is None


@pytest.mark.asyncio
async def test_provider_failure_records_typed_history_error(monkeypatch) -> None:
    def fail_download(*_args, **_kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(
        mdc,
        "_get_yfinance",
        lambda: SimpleNamespace(
            Ticker=lambda _symbol: _FakeTicker(),
            download=fail_download,
        ),
    )
    data = await mdc.TickerDataCache().prefetch("BBCA")

    assert data["history"] is None
    assert data["history_status"] == "provider_error"
    assert data["history_error_type"] == "TimeoutError"
    assert data["history_error"] == "provider timed out"


@pytest.mark.asyncio
async def test_concurrent_miss_fetches_once_and_returns_same_object(monkeypatch) -> None:
    now = datetime(2026, 7, 13, 10, 0, tzinfo=IDX_TIMEZONE)
    calls = 0
    call_lock = threading.Lock()

    def fake_fetch(ticker: str, seeded_snapshot=None):
        nonlocal calls
        with call_lock:
            calls += 1
        time.sleep(0.05)
        return {"ticker": ticker, "seed": seeded_snapshot}

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", fake_fetch)
    cache = mdc.TickerDataCache(clock=lambda: now)

    first, second = await asyncio.gather(
        cache.prefetch("BBCA"),
        cache.prefetch("bbca"),
    )

    assert calls == 1
    assert first is second
    assert cache.inflight_count == 0


@pytest.mark.asyncio
async def test_failed_inflight_is_removed_and_next_request_retries(monkeypatch) -> None:
    now = datetime(2026, 7, 13, 10, 0, tzinfo=IDX_TIMEZONE)
    calls = 0

    def flaky_fetch(ticker: str, seeded_snapshot=None):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        if calls == 1:
            raise TimeoutError("temporary provider failure")
        return {"ticker": ticker, "fetch_number": calls}

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", flaky_fetch)
    cache = mdc.TickerDataCache(clock=lambda: now)

    outcomes = await asyncio.gather(
        cache.prefetch("BBCA"),
        cache.prefetch("bbca"),
        return_exceptions=True,
    )

    assert calls == 1
    assert all(isinstance(item, TimeoutError) for item in outcomes)
    assert cache.inflight_count == 0
    recovered = await cache.prefetch("BBCA")
    assert recovered["fetch_number"] == 2
    assert calls == 2


@pytest.mark.asyncio
async def test_open_market_cache_expires_after_15_minutes(monkeypatch) -> None:
    now = [datetime(2026, 7, 13, 10, 0, tzinfo=IDX_TIMEZONE)]
    calls = 0

    def fake_fetch(ticker: str, seeded_snapshot=None):
        nonlocal calls
        calls += 1
        return {"ticker": ticker, "fetch_number": calls}

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", fake_fetch)
    cache = mdc.TickerDataCache(clock=lambda: now[0])

    first = await cache.prefetch("BBCA")
    now[0] += timedelta(minutes=14, seconds=59)
    assert await cache.prefetch("BBCA") is first
    now[0] += timedelta(seconds=2)
    second = await cache.prefetch("BBCA")

    assert second is not first
    assert calls == 2
    assert first["cache_ttl_seconds"] == 15 * 60


@pytest.mark.asyncio
async def test_after_close_cache_expires_after_six_hours(monkeypatch) -> None:
    now = [datetime(2026, 7, 13, 17, 0, tzinfo=IDX_TIMEZONE)]
    calls = 0

    def fake_fetch(ticker: str, seeded_snapshot=None):
        nonlocal calls
        calls += 1
        return {"ticker": ticker, "fetch_number": calls}

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", fake_fetch)
    cache = mdc.TickerDataCache(clock=lambda: now[0])

    first = await cache.prefetch("BBCA")
    now[0] += timedelta(hours=5, minutes=59)
    assert await cache.prefetch("BBCA") is first
    now[0] += timedelta(minutes=2)
    second = await cache.prefetch("BBCA")

    assert second is not first
    assert calls == 2
    assert first["cache_ttl_seconds"] == 6 * 60 * 60


@pytest.mark.asyncio
async def test_session_rollover_ignores_old_entry_and_warns(monkeypatch) -> None:
    now = [datetime(2026, 7, 13, 8, 30, tzinfo=IDX_TIMEZONE)]
    calls = 0
    warnings: list[str] = []

    def fake_fetch(ticker: str, seeded_snapshot=None):
        nonlocal calls
        calls += 1
        return {"ticker": ticker, "fetch_number": calls}

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", fake_fetch)
    monkeypatch.setattr(
        mdc.logger,
        "warning",
        lambda message, *args: warnings.append(str(message).format(*args)),
    )
    cache = mdc.TickerDataCache(clock=lambda: now[0])

    friday_data = await cache.prefetch("BBCA")
    assert friday_data["cache_session_date"] == "2026-07-10"
    now[0] = datetime(2026, 7, 13, 9, 0, tzinfo=IDX_TIMEZONE)
    monday_data = await cache.prefetch("BBCA")

    assert monday_data is not friday_data
    assert monday_data["cache_session_date"] == "2026-07-13"
    assert calls == 2
    assert any("stale_session_cache_ignored" in item for item in warnings)


@pytest.mark.asyncio
async def test_clear_run_cache_forces_new_fetch(monkeypatch) -> None:
    now = datetime(2026, 7, 13, 10, 0, tzinfo=IDX_TIMEZONE)
    calls = 0

    def fake_fetch(ticker: str, seeded_snapshot=None):
        nonlocal calls
        calls += 1
        return {"ticker": ticker, "fetch_number": calls}

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", fake_fetch)
    cache = mdc.TickerDataCache(clock=lambda: now)

    first = await cache.prefetch("BBCA")
    await cache.clear_run_cache()
    second = await cache.prefetch("BBCA")

    assert second is not first
    assert calls == 2


@pytest.mark.asyncio
async def test_seeded_concurrent_requests_share_bundle_and_snapshot(monkeypatch) -> None:
    snapshot = _snapshot()
    now = datetime(2026, 7, 10, 17, 0, tzinfo=IDX_TIMEZONE)
    calls = 0

    def fake_fetch(ticker: str, seeded_snapshot=None):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return {
            "ticker": ticker,
            "_market_snapshot_object": seeded_snapshot,
            "snapshot_id": seeded_snapshot.snapshot_id,
        }

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", fake_fetch)
    cache = mdc.TickerDataCache(clock=lambda: now)
    await cache.seed_snapshot(snapshot)

    first, second = await asyncio.gather(
        cache.prefetch("BBCA"),
        cache.prefetch("bbca"),
    )

    assert calls == 1
    assert first is second
    assert first["_market_snapshot_object"] is snapshot


@pytest.mark.asyncio
async def test_run_crossing_market_open_keeps_exact_seeded_snapshot(monkeypatch) -> None:
    now = [datetime(2026, 7, 13, 8, 59, tzinfo=IDX_TIMEZONE)]
    snapshot = _snapshot(requested_end=date(2026, 7, 13))
    received_seeds: list[object] = []

    def fake_fetch(ticker: str, seeded_snapshot=None):
        received_seeds.append(seeded_snapshot)
        return {
            "ticker": ticker,
            "_market_snapshot_object": seeded_snapshot,
            "history_status": "ready",
        }

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", fake_fetch)
    cache = mdc.TickerDataCache(clock=lambda: now[0])
    await cache.clear_run_cache()
    await cache.seed_snapshot(snapshot)
    now[0] = datetime(2026, 7, 13, 9, 1, tzinfo=IDX_TIMEZONE)

    data = await cache.prefetch("BBCA")

    assert received_seeds == [snapshot]
    assert data["_market_snapshot_object"] is snapshot
    assert data["cache_session_date"] == "2026-07-13"


@pytest.mark.asyncio
async def test_stale_seed_snapshot_fails_closed(monkeypatch) -> None:
    now = datetime(2026, 7, 13, 10, 0, tzinfo=IDX_TIMEZONE)
    snapshot = _snapshot(requested_end=date(2026, 7, 10))
    warnings: list[str] = []
    monkeypatch.setattr(
        mdc.logger,
        "warning",
        lambda message, *args: warnings.append(str(message).format(*args)),
    )
    cache = mdc.TickerDataCache(clock=lambda: now)
    await cache.clear_run_cache()

    with pytest.raises(ValueError, match="stale_seed_snapshot"):
        await cache.seed_snapshot(snapshot)

    assert any("stale_seed_snapshot" in item for item in warnings)


@pytest.mark.asyncio
async def test_preopen_cache_ttl_is_capped_at_six_hours(monkeypatch) -> None:
    now = datetime(2026, 7, 13, 0, 0, tzinfo=IDX_TIMEZONE)
    monkeypatch.setattr(
        mdc,
        "_fetch_yfinance_bundle",
        lambda ticker, seeded_snapshot=None: {
            "ticker": ticker,
            "history_status": "ready",
        },
    )
    cache = mdc.TickerDataCache(clock=lambda: now)

    data = await cache.prefetch("BBCA")

    assert data["cache_ttl_seconds"] == 6 * 60 * 60


@pytest.mark.asyncio
async def test_provider_error_bundle_is_not_cached(monkeypatch) -> None:
    now = datetime(2026, 7, 13, 10, 0, tzinfo=IDX_TIMEZONE)
    calls = 0

    def provider_error(ticker: str, seeded_snapshot=None):
        nonlocal calls
        calls += 1
        return {
            "ticker": ticker,
            "history_status": "provider_error",
            "history_error_type": "TimeoutError",
        }

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", provider_error)
    cache = mdc.TickerDataCache(clock=lambda: now)

    first = await cache.prefetch("BBCA")
    second = await cache.prefetch("BBCA")

    assert calls == 2
    assert first is not second
    assert first["cache_ttl_seconds"] == 0
    assert first["cache_policy"] == "provider_error_not_cached"


@pytest.mark.asyncio
async def test_first_fetched_snapshot_stays_pinned_after_bundle_ttl(
    monkeypatch,
) -> None:
    now = [datetime(2026, 7, 13, 10, 0, tzinfo=IDX_TIMEZONE)]
    snapshot = _snapshot(requested_end=date(2026, 7, 13))
    received_seeds: list[object] = []

    def fake_fetch(ticker: str, seeded_snapshot=None):
        received_seeds.append(seeded_snapshot)
        active_snapshot = seeded_snapshot or snapshot
        return {
            "ticker": ticker,
            "history_status": "ready",
            "_market_snapshot_object": active_snapshot,
            "snapshot_id": active_snapshot.snapshot_id,
        }

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", fake_fetch)
    cache = mdc.TickerDataCache(clock=lambda: now[0])
    await cache.clear_run_cache()

    first = await cache.prefetch("BBCA")
    now[0] += timedelta(minutes=16)
    second = await cache.prefetch("BBCA")

    assert received_seeds == [None, snapshot]
    assert first is not second
    assert first["_market_snapshot_object"] is snapshot
    assert second["_market_snapshot_object"] is snapshot


@pytest.mark.asyncio
async def test_overlapping_pipeline_contexts_use_isolated_run_caches(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_fetch(ticker: str, seeded_snapshot=None):
        calls.append(ticker)
        time.sleep(0.05)
        return {
            "ticker": ticker,
            "history_status": "ready",
            "_market_snapshot_object": seeded_snapshot,
        }

    async def run(ticker: str, session: date):
        await mdc.clear_run_cache(run_session_date=session)
        first, second = await asyncio.gather(
            mdc.prefetch_market_data(ticker),
            mdc.prefetch_market_data(ticker.lower()),
        )
        return first, second

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", fake_fetch)
    (bbca_first, bbca_second), (bmri_first, bmri_second) = await asyncio.gather(
        run("BBCA", date(2026, 7, 13)),
        run("BMRI", date(2026, 7, 14)),
    )

    assert sorted(calls) == ["BBCA", "BMRI"]
    assert bbca_first is bbca_second
    assert bmri_first is bmri_second
    assert bbca_first["cache_session_date"] == "2026-07-13"
    assert bmri_first["cache_session_date"] == "2026-07-14"


@pytest.mark.asyncio
async def test_partial_auxiliary_failure_uses_short_ttl(monkeypatch) -> None:
    now = [datetime(2026, 7, 13, 10, 0, tzinfo=IDX_TIMEZONE)]
    calls = 0

    def partial_fetch(ticker: str, seeded_snapshot=None):
        nonlocal calls
        calls += 1
        return {
            "ticker": ticker,
            "history_status": "ready",
            "component_status": {
                "history": "ready",
                "info": "provider_error",
                "fast_info": "ready",
                "calendar": "ready",
                "dividends": "ready",
            },
        }

    monkeypatch.setattr(mdc, "_fetch_yfinance_bundle", partial_fetch)
    cache = mdc.TickerDataCache(clock=lambda: now[0])

    first = await cache.prefetch("BBCA")
    assert await cache.prefetch("BBCA") is first
    now[0] += timedelta(seconds=61)
    second = await cache.prefetch("BBCA")

    assert calls == 2
    assert second is not first
    assert first["cache_ttl_seconds"] == 60
    assert first["cache_policy"] == "partial_provider_error_60s"
    assert first["cache_partial_error_components"] == ["info"]


@pytest.mark.asyncio
async def test_empty_execution_critical_component_uses_short_ttl(
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 13, 10, 0, tzinfo=IDX_TIMEZONE)
    monkeypatch.setattr(
        mdc,
        "_fetch_yfinance_bundle",
        lambda ticker, seeded_snapshot=None: {
            "ticker": ticker,
            "history_status": "ready",
            "component_status": {
                "history": "ready",
                "info": "empty",
                "fast_info": "ready",
                "calendar": "ready",
                "dividends": "ready",
            },
        },
    )
    cache = mdc.TickerDataCache(clock=lambda: now)

    data = await cache.prefetch("BBCA")

    assert data["cache_ttl_seconds"] == 60
    assert data["cache_policy"] == "partial_provider_error_60s"
    assert data["cache_partial_error_components"] == ["info"]
