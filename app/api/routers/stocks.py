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
_cache_file_mtime: float = 0.0  # New: track results file modification time
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
    If the batch results file is missing or invalid, it scans output/debates/
    as a fallback to populate the results list.
    """
    global _cache, _cache_timestamp, _cache_file_mtime

    current_mtime = 0.0
    try:
        if RESULTS_PATH.exists():
            current_mtime = RESULTS_PATH.stat().st_mtime
    except Exception:
        pass

    now = time.monotonic()
    if _cache and (now - _cache_timestamp) < _CACHE_TTL_SECONDS and current_mtime == _cache_file_mtime:
        return _cache

    async with _cache_lock:
        now = time.monotonic()
        try:
            if RESULTS_PATH.exists():
                current_mtime = RESULTS_PATH.stat().st_mtime
        except Exception:
            pass
        if _cache and (now - _cache_timestamp) < _CACHE_TTL_SECONDS and current_mtime == _cache_file_mtime:
            return _cache

        raw_data = []
        try:
            async with aiofiles.open(RESULTS_PATH, mode="r", encoding="utf-8") as f:
                content = await f.read()
            if content.strip():
                loaded = json.loads(content)
                if isinstance(loaded, list):
                    raw_data = loaded
        except Exception as exc:
            logger.warning(
                f"[_load_results] Failed to read {RESULTS_PATH}: {exc}."
            )

        # Merge results: key by ticker. Start with full_batch_results.json items
        compiled_results = {
            item["ticker"]: item
            for item in raw_data
            if isinstance(item, dict) and "ticker" in item
        }

        # Scan output/debates/*.json for any additional or missing individual debates
        debates_dir = RESULTS_PATH.parent / "debates"
        if debates_dir.exists():
            for f in debates_dir.glob("*.json"):
                try:
                    async with aiofiles.open(f, mode="r", encoding="utf-8") as file:
                        file_content = await file.read()
                    if file_content.strip():
                        data = json.loads(file_content)
                        if isinstance(data, dict) and "ticker" in data:
                            ticker_key = data["ticker"]
                            # Only add if not already present in compiled_results
                            if ticker_key not in compiled_results:
                                compiled_results[ticker_key] = data
                except Exception as e:
                    logger.warning(f"[_load_results] Failed to read fallback {f}: {e}")
        
        if not compiled_results:
            raise _error(
                "NO_RESULTS",
                "Belum ada hasil analisis. Jalankan batch terlebih dahulu.",
                status.HTTP_404_NOT_FOUND,
            )

        raw_data = list(compiled_results.values())

        _cache = {  # QW-FIX-2  # QW-FIX-PF1
            item["ticker"]: item
            for item in normalize_batch(raw_data)
            if "ticker" in item
        }
        _cache_timestamp = time.monotonic()
        _cache_file_mtime = current_mtime
        return _cache


def invalidate_results_cache() -> None:
    """
    Call this after a new batch run completes to force cache refresh
    on the next API request. Mark: # QW-FIX-PF1
    """
    global _cache, _cache_timestamp, _cache_file_mtime
    _cache = {}
    _cache_timestamp = 0.0
    _cache_file_mtime = 0.0
    logger.info("[results_cache] Cache invalidated manually.")


@router.get("/health")
async def health_check() -> dict[str, Any]:
    from pathlib import Path
    from datetime import datetime, timedelta
    
    debates_dir = RESULTS_PATH.parent / "debates"
    latest_time = None
    results = []
    
    if debates_dir.exists():
        for f in debates_dir.glob("*.json"):
            try:
                mtime = f.stat().st_mtime
                if latest_time is None or mtime > latest_time:
                    latest_time = mtime
                    
                content = f.read_text(encoding="utf-8")
                if not content.strip():
                    continue
                data = json.loads(content)
                
                verdict = data.get("verdict") or {}
                rating = verdict.get("rating", "UNKNOWN")
                confidence = verdict.get("confidence", 0.0)
                conviction_score = data.get("conviction_score", 0.0)
                rounds = data.get("debate_rounds", 0)
                consensus = data.get("consensus_reached", False)
                
                metadata = data.get("metadata") or {}
                batch_ts = metadata.get("batch_timestamp") or metadata.get("run_timestamp")
                debate_date = datetime.fromtimestamp(mtime)
                if batch_ts:
                    try:
                        debate_date = datetime.strptime(batch_ts, "%Y%m%d_%H%M%S")
                    except ValueError:
                        try:
                            debate_date = datetime.strptime(batch_ts.split("_")[0], "%Y%m%d")
                        except Exception:
                            pass
                
                results.append({
                    "rating": rating,
                    "confidence": confidence,
                    "conviction_score": conviction_score,
                    "rounds": rounds,
                    "consensus": consensus,
                    "date": debate_date
                })
            except Exception:
                pass
                
    total_debates = len(results)
    
    # Calculate stats
    ratings_dist = {"STRONG_BUY": 0, "BUY": 0, "HOLD": 0, "AVOID": 0}
    total_conviction = 0.0
    total_confidence = 0.0
    consensus_count = 0
    fresh_count = 0
    stale_count = 0
    
    # 1 month threshold (30 days) from now
    now = datetime.now()
    one_month_ago = now - timedelta(days=30)
    
    for r in results:
        rating = r["rating"]
        if rating in ratings_dist:
            ratings_dist[rating] += 1
        else:
            ratings_dist[rating] = 1
            
        total_conviction += r["conviction_score"]
        total_confidence += r["confidence"]
        if r["consensus"]:
            consensus_count += 1
        if r["date"] >= one_month_ago:
            fresh_count += 1
        else:
            stale_count += 1
            
    latest_date_str = None
    if latest_time is not None:
        latest_date_str = datetime.fromtimestamp(latest_time).isoformat()
        
    debate_stats = {
        "total_debates": total_debates,
        "avg_conviction": round(total_conviction / total_debates, 3) if total_debates > 0 else 0.0,
        "avg_confidence": round(total_confidence / total_debates, 3) if total_debates > 0 else 0.0,
        "consensus_rate": round((consensus_count / total_debates) * 100, 2) if total_debates > 0 else 0.0,
        "ratings_distribution": ratings_dist,
        "fresh_count": fresh_count,
        "stale_count": stale_count,
        "latest_debate_date": latest_date_str
    }

    return {
        "status": "ok", 
        "results_exist": RESULTS_PATH.exists(),
        "latest_debate_date": latest_date_str,
        "debate_stats": debate_stats
    }


@router.get("/validate-key")
async def validate_key(api_key: str = Depends(get_gemini_api_key)) -> dict[str, bool]:
    if not api_key:
        return {"valid": False}
    
    from providers.gemini import get_flash_llm, gemini_api_key_override
    import asyncio
    
    try:
        with gemini_api_key_override(api_key):
            llm = get_flash_llm()
            # Set a very short timeout to not hang the dashboard
            await asyncio.wait_for(llm.ainvoke("ping"), timeout=5.0)
        return {"valid": True}
    except Exception as e:
        logger.warning(f"API Key validation failed: {e}")
        return {"valid": False}


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
        from core.orchestrator.pipeline import main as orchestrator_main

        event_queue = asyncio.Queue()

        def sse(data: dict[str, Any] | str) -> str:
            if isinstance(data, str):
                return f"data: {data}\n\n"
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        class StreamingDebateChamber(DebateChamber):
            async def run(self, ticker: str, current_price: float = 0.0) -> dict:
                final_result = {}
                async for event in self.stream_run(ticker):
                    await event_queue.put(event)
                    if event.get("type") == "verdict":
                        final_result = event.get("raw_state", {})
                    elif event.get("type") == "error":
                        if not final_result:
                            from core.orchestrator.legacy import _empty_result
                            final_result = _empty_result(ticker, event.get("message", "Unknown error"))
                return final_result

        def chamber_factory():
            return StreamingDebateChamber()

        # Phase 1: Pre-flight checks progress
        for ticker in payload.tickers:
            yield sse({"type": "progress", "ticker": ticker, "phase": "Pre-flight Checks", "pct": 5})

        # Phase 2: Market Regime Detection progress
        for ticker in payload.tickers:
            yield sse({"type": "progress", "ticker": ticker, "phase": "Market Regime", "pct": 10})

        user_config = {
            "total_capital": 1_000_000.0,
            "max_loss_pct": 0.02,
            "max_positions": 5,
        }

        # Run orchestrator main in a background task
        orchestrator_task = None
        try:
            with gemini_api_key_override(api_key):
                orchestrator_task = asyncio.create_task(
                    orchestrator_main(
                        tickers=payload.tickers,
                        user_config=user_config,
                        chamber_factory=chamber_factory,
                        mode="multi",
                        raise_on_error=True,
                    )
                )

            # Consume events from the queue while the task runs
            while not orchestrator_task.done() or not event_queue.empty():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                    if event.get("type") == "verdict":
                        # Strip raw_state to save bandwidth
                        event.pop("raw_state", None)
                    yield sse(event)
                    event_queue.task_done()
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"

            # Check if orchestrator raised an exception
            if orchestrator_task.exception() is not None:
                exc = orchestrator_task.exception()
                logger.error(f"Orchestrator pipeline failed: {exc}", exc_info=exc)
                for ticker in payload.tickers:
                    yield sse({"type": "error", "ticker": ticker, "message": f"Orchestrator pipeline failed: {exc}"})
            else:
                # Yield 95% progress for scoring and sizing
                for ticker in payload.tickers:
                    yield sse({"type": "progress", "ticker": ticker, "phase": "Scoring & Sizing", "pct": 95})

                # Invalidate cache and yield the final scored verdicts
                invalidate_results_cache()
                final_results = await _load_results()
                for ticker in payload.tickers:
                    if ticker in final_results:
                        yield sse({
                            "type": "verdict",
                            "ticker": ticker,
                            "result": final_results[ticker]
                        })
                    yield sse({"type": "done", "ticker": ticker})

        except asyncio.CancelledError:
            if orchestrator_task and not orchestrator_task.done():
                orchestrator_task.cancel()
                try:
                    await orchestrator_task
                except asyncio.CancelledError:
                    pass
            raise
        except Exception as e:
            logger.error(f"Error during streaming debate orchestrator: {e}", exc_info=e)
            for ticker in payload.tickers:
                yield sse({"type": "error", "ticker": ticker, "message": str(e)})
        finally:
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
