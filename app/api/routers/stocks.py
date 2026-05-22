import asyncio
import json
import time
from typing import Any

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cache import get_cache_lock, get_stocks_cache
from app.api.dependency_injections.api_key import get_gemini_api_key
from app.api.dependency_injections.session import get_db
from app.api.result_adapter import normalize_batch
from app.api.schemas import DebateStreamRequest, StockSchema
from core.settings import settings
from db.models.stock import Stock
from utils.logger_config import logger


router = APIRouter(prefix="/api", tags=["stocks"])
RESULTS_PATH = settings.results_path

_cache: dict[str, dict[str, Any]] = {}  # QW-FIX-PF1
_cache_timestamp: float = 0.0  # QW-FIX-PF1
_CACHE_TTL_SECONDS: float = 60.0  # QW-FIX-PF1
_cache_lock = asyncio.Lock()  # QW-FIX-PF1


def _error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


async def _load_results() -> dict[str, dict[str, Any]]:  # QW-FIX-PF1
    """
    Load batch results from disk into an in-memory dict keyed by ticker.
    Uses a TTL cache to avoid blocking disk reads on every API call.
    Cache is refreshed at most once every _CACHE_TTL_SECONDS.
    """
    global _cache, _cache_timestamp

    now = time.monotonic()
    if _cache and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
        return _cache

    async with _cache_lock:
        now = time.monotonic()
        if _cache and (now - _cache_timestamp) < _CACHE_TTL_SECONDS:
            return _cache

        try:
            async with aiofiles.open(RESULTS_PATH, mode="r", encoding="utf-8") as f:
                content = await f.read()
            raw_data: Any = json.loads(content)
            _cache = {  # QW-FIX-2  # QW-FIX-PF1
                item["ticker"]: item
                for item in normalize_batch(raw_data)
                if "ticker" in item
            }
            _cache_timestamp = time.monotonic()
            return _cache
        except FileNotFoundError as exc:
            raise _error(
                "NO_RESULTS",
                "Belum ada hasil analisis. Jalankan batch terlebih dahulu.",
                status.HTTP_404_NOT_FOUND,
            ) from exc
        except json.JSONDecodeError as exc:
            raise _error(
                "INVALID_RESULTS",
                f"Artifact hasil tidak valid: {exc}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ) from exc
        except Exception as exc:
            logger.error(
                f"[_load_results] Unexpected error reading results file: {exc}",
                exc_info=True,
            )
            raise _error(
                "READ_ERROR",
                "Gagal membaca hasil analisis.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ) from exc


def invalidate_results_cache() -> None:
    """
    Call this after a new batch run completes to force cache refresh
    on the next API request. Mark: # QW-FIX-PF1
    """
    global _cache, _cache_timestamp
    _cache = {}
    _cache_timestamp = 0.0
    logger.info("[results_cache] Cache invalidated manually.")


@router.get("/health")
async def health_check() -> dict[str, Any]:
    return {"status": "ok", "results_exist": RESULTS_PATH.exists()}


@router.get("/validate-key")
async def validate_key(api_key: str = Depends(get_gemini_api_key)) -> dict[str, bool]:
    return {"valid": bool(api_key)}


@router.get("/stocks", response_model=list[StockSchema])  # QW-FIX-5
async def get_stocks(db: AsyncSession = Depends(get_db)) -> list[StockSchema]:
    cache = get_stocks_cache()
    lock = get_cache_lock()
    with lock:
        cached = cache.get("stocks")
        if cached is not None:
            return cached
    stmt = select(Stock).order_by(Stock.ticker)
    result = await db.scalars(stmt)
    stocks = [StockSchema.model_validate(stock) for stock in result]
    with lock:
        cache["stocks"] = stocks
    return stocks


@router.get("/stocks/{ticker}")
async def get_stock_detail(
    ticker: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    normalized_ticker = ticker.upper()
    stmt = select(Stock).where(Stock.ticker == normalized_ticker)
    stock = (await db.scalars(stmt)).first()
    results = {}
    try:
        results = await _load_results()  # QW-FIX-PF1
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
    latest = results.get(normalized_ticker)
    if stock is None and latest is None:
        raise _error(
            "STOCK_NOT_FOUND",
            f"Saham {normalized_ticker} tidak ditemukan.",
            status.HTTP_404_NOT_FOUND,
        )
    return {
        "ticker": normalized_ticker,
        "stock": None
        if stock is None
        else {
            "ticker": stock.ticker,
            "name": stock.name,
            "market_cap": stock.market_cap,
            "home_page": stock.home_page,
        },
        "latest_result": latest,
    }


@router.get("/results")
async def get_results() -> list[dict[str, Any]]:
    return list((await _load_results()).values())  # QW-FIX-PF1


@router.post("/debate/stream")
async def stream_debate(
    payload: DebateStreamRequest,
    api_key: str = Depends(get_gemini_api_key),
) -> StreamingResponse:
    async def event_generator():
        from providers.gemini import gemini_api_key_override
        from services.debate_chamber import DebateChamber

        def sse(data: dict[str, Any] | str) -> str:
            if isinstance(data, str):
                return f"data: {data}\n\n"
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        async def stream_with_heartbeat(chamber: DebateChamber, ticker: str):
            stream = chamber.stream_run(ticker)
            try:
                while True:
                    next_event = asyncio.create_task(anext(stream))
                    while True:
                        try:
                            yield await asyncio.wait_for(
                                asyncio.shield(next_event),
                                timeout=15,
                            )
                            break
                        except asyncio.TimeoutError:
                            yield None
                    if next_event.done() and next_event.exception() is not None:
                        raise next_event.exception()
            except StopAsyncIteration:
                return
            finally:
                await stream.aclose()

        with gemini_api_key_override(api_key):
            for ticker in payload.tickers:
                chamber = DebateChamber()
                try:
                    async for event in stream_with_heartbeat(chamber, ticker):
                        if event is None:
                            yield ": heartbeat\n\n"
                        else:
                            yield sse(event)
                        await asyncio.sleep(0)
                except Exception as exc:
                    yield sse({"type": "error", "ticker": ticker, "message": str(exc)})
        yield sse("[DONE]")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
