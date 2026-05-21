import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cache import get_batch_cache, get_cache_lock, get_stocks_cache
from app.api.dependency_injections.api_key import get_gemini_api_key
from app.api.dependency_injections.session import get_db
from app.api.result_adapter import normalize_batch
from app.api.schemas import DebateStreamRequest
from db.models.stock import Stock


router = APIRouter(prefix="/api", tags=["stocks"])
RESULTS_PATH = Path("output/full_batch_results.json")


def _error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _load_results() -> list[dict[str, Any]]:
    cache = get_batch_cache()
    lock = get_cache_lock()
    with lock:
        cached = cache.get("results")
        if cached is not None:
            return cached
    if not RESULTS_PATH.exists():
        raise _error(
            "NO_RESULTS",
            "Belum ada hasil analisis. Jalankan batch terlebih dahulu.",
            status.HTTP_404_NOT_FOUND,
        )
    try:
        raw_data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _error(
            "INVALID_RESULTS",
            f"Artifact hasil tidak valid: {exc}",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc
    data = normalize_batch(raw_data)
    with lock:
        cache["results"] = data
    return data


@router.get("/health")
async def health_check() -> dict[str, Any]:
    return {"status": "ok", "results_exist": RESULTS_PATH.exists()}


@router.get("/validate-key")
async def validate_key(api_key: str = Depends(get_gemini_api_key)) -> dict[str, bool]:
    return {"valid": bool(api_key)}


@router.get("/stocks")
async def get_stocks(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    cache = get_stocks_cache()
    lock = get_cache_lock()
    with lock:
        cached = cache.get("stocks")
        if cached is not None:
            return cached
    stmt = select(Stock).order_by(Stock.ticker)
    result = await db.scalars(stmt)
    stocks = [
        {
            "ticker": stock.ticker,
            "name": stock.name,
            "market_cap": stock.market_cap,
            "home_page": stock.home_page,
        }
        for stock in result
    ]
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
    results = []
    try:
        results = _load_results()
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
    latest = next(
        (item for item in results if item.get("ticker") == normalized_ticker),
        None,
    )
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
    return _load_results()


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
