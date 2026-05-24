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
        from pathlib import Path
        debates_dir = Path("output/debates")
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
    
    debates_dir = Path("output/debates")
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
                            # Catch the verdict event to update the results file
                            if event.get("type") == "verdict" and "raw_state" in event:
                                try:
                                    # Create a serializable format mimicking _run_single_debate
                                    raw_state = event["raw_state"]
                                    verdict_dict = {}
                                    if raw_state.get("final_verdict"):
                                        try:
                                            verdict_dict = json.loads(raw_state["final_verdict"])
                                        except Exception:
                                            pass
                                    
                                    debate_history = raw_state.get("debate_history", [])
                                    serializable_state = {
                                        "ticker": ticker,
                                        "verdict": verdict_dict,
                                        "debate_rounds": raw_state.get("round_count", 0),
                                        "consensus_reached": raw_state.get("consensus_reached", False),
                                        "consensus_method": raw_state.get("consensus_method"),
                                        "dissenting_agents": raw_state.get("dissenting_agents", []),
                                        "agent_votes": raw_state.get("agent_votes", []),
                                        "disagreement_type": raw_state.get("disagreement_type"),
                                        "debate_history": [
                                            {
                                                "role": getattr(m, "role", "unknown"),
                                                "content": getattr(m, "content", ""),
                                                "round": getattr(m, "round_num", 0),
                                                "position": getattr(m, "position", "UNKNOWN"),
                                                "confidence": getattr(m, "confidence", None),
                                            }
                                            if hasattr(m, "role") else m
                                            for m in debate_history
                                        ],
                                        "raw_data_summary": raw_state.get("raw_data", ""),
                                        "metadata": raw_state.get("metadata", {}),
                                        "error": raw_state.get("error"),
                                        "status": "failed" if raw_state.get("error") else "success",
                                        "conviction_score": 0.0,
                                    }

                                    # Write back to full_batch_results.json
                                    async with _cache_lock:
                                        content = "[]"
                                        try:
                                            async with aiofiles.open(RESULTS_PATH, mode="r", encoding="utf-8") as f:
                                                content = await f.read()
                                        except FileNotFoundError:
                                            pass
                                        
                                        raw_data = json.loads(content) if content.strip() else []
                                        if not isinstance(raw_data, list):
                                            raw_data = []
                                            
                                        # Update or append the result
                                        updated = False
                                        for i, item in enumerate(raw_data):
                                            if item.get("ticker") == ticker:
                                                raw_data[i] = serializable_state
                                                updated = True
                                                break
                                        if not updated:
                                            raw_data.append(serializable_state)
                                            
                                        async with aiofiles.open(RESULTS_PATH, mode="w", encoding="utf-8") as f:
                                            await f.write(json.dumps(raw_data, indent=2))
                                        
                                    # Force UI to fetch fresh results next time
                                    invalidate_results_cache()
                                except Exception as write_err:
                                    logger.error(f"Failed to save stream result for {ticker}: {write_err}")
                                
                                # Do not send raw_state to frontend to save bandwidth
                                event.pop("raw_state", None)

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
