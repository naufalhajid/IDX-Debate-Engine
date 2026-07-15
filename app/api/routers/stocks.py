import asyncio
import json
import time
from pathlib import Path
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
from utils.ticker import (
    InvalidIDXTicker,
    PathContainmentError,
    canonicalize_result_identity,
    normalize_idx_ticker,
    resolve_within_root,
)


router = APIRouter(prefix="/api", tags=["stocks"])
RESULTS_PATH = settings.results_path
MERGED_RESULTS_PATH = settings.merged_results_path

_cache: dict[str, dict[str, Any]] = {}  # QW-FIX-PF1
_cache_timestamp: float = 0.0  # QW-FIX-PF1
_cache_file_mtime: float = 0.0  # Tracks batch/debate artifact modification time
_CACHE_TTL_SECONDS: float = 60.0  # QW-FIX-PF1
_cache_lock = asyncio.Lock()  # QW-FIX-PF1


def _error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _safe_root_file(path: Path) -> Path:
    return resolve_within_root(RESULTS_PATH.parent, path.name)


def _safe_root_file_exists(path: Path) -> bool:
    try:
        return _safe_root_file(path).is_file()
    except (OSError, PathContainmentError):
        return False


def _validate_stock_ticker_path(ticker: str) -> None:
    """Route-level guard that runs before normal endpoint dependencies."""

    try:
        normalize_idx_ticker(ticker)
    except InvalidIDXTicker as exc:
        raise _error(
            "INVALID_IDX_TICKER",
            str(exc),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc


def _safe_debate_files() -> list[Path]:
    try:
        debates_dir = resolve_within_root(RESULTS_PATH.parent, "debates")
    except PathContainmentError as exc:
        logger.warning(
            "[results_artifacts] reason_code=unsafe_debates_directory: {}", exc
        )
        return []
    if not debates_dir.is_dir():
        return []

    files: list[Path] = []
    for candidate in debates_dir.glob("*.json"):
        try:
            safe_path = resolve_within_root(debates_dir, candidate.name)
        except PathContainmentError as exc:
            logger.warning(
                "[results_artifacts] reason_code=unsafe_debate_artifact file={}: {}",
                candidate.name,
                exc,
            )
            continue
        if safe_path.is_file():
            files.append(safe_path)
    return files


def _expected_ticker_from_debate_path(path: Path) -> str | None:
    suffix = "_debate.json"
    if not path.name.endswith(suffix):
        return None
    raw = path.name[: -len(suffix)]
    try:
        return normalize_idx_ticker(raw)
    except InvalidIDXTicker:
        return None


def _canonical_result_item(
    item: Any,
    *,
    expected_ticker: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    try:
        return canonicalize_result_identity(item, expected_ticker=expected_ticker)
    except InvalidIDXTicker as exc:
        logger.warning(
            "[results_artifacts] reason_code=invalid_artifact_ticker: {}", exc
        )
        return None


def _results_signature() -> float:
    signature = 0.0
    for configured_path in (RESULTS_PATH, _merged_results_path()):
        try:
            path = _safe_root_file(configured_path)
            if path.exists():
                signature = max(signature, path.stat().st_mtime)
        except (OSError, PathContainmentError):
            pass

    for path in _safe_debate_files():
        try:
            signature = max(signature, path.stat().st_mtime)
        except OSError:
            continue
    return signature


def _merged_results_path() -> Path:
    if MERGED_RESULTS_PATH.parent == RESULTS_PATH.parent:
        return MERGED_RESULTS_PATH
    return RESULTS_PATH.parent / "merged_batch_results.json"


def _primary_results_path() -> Path:
    merged_path = _safe_root_file(_merged_results_path())
    results_path = _safe_root_file(RESULTS_PATH)
    return merged_path if merged_path.exists() else results_path


async def _load_results() -> dict[str, dict[str, Any]]:  # QW-FIX-PF1
    """
    Load batch results from disk into an in-memory dict keyed by ticker.
    Uses a TTL cache to avoid blocking disk reads on every API call.
    Cache is refreshed at most once every _CACHE_TTL_SECONDS.
    If the batch results file is missing or invalid, it scans output/debates/
    as a fallback to populate the results list.
    """
    global _cache, _cache_timestamp, _cache_file_mtime

    current_mtime = _results_signature()

    now = time.monotonic()
    if (
        _cache
        and (now - _cache_timestamp) < _CACHE_TTL_SECONDS
        and current_mtime == _cache_file_mtime
    ):
        return _cache

    async with _cache_lock:
        now = time.monotonic()
        current_mtime = _results_signature()
        if (
            _cache
            and (now - _cache_timestamp) < _CACHE_TTL_SECONDS
            and current_mtime == _cache_file_mtime
        ):
            return _cache

        raw_data = []
        results_path: Path | None = None
        try:
            results_path = _primary_results_path()
            async with aiofiles.open(results_path, mode="r", encoding="utf-8") as f:
                content = await f.read()
            if content.strip():
                loaded = json.loads(content)
                if isinstance(loaded, list):
                    raw_data = loaded
        except Exception as exc:
            logger.warning(
                f"[_load_results] Failed to read results artifact: {exc}."
            )

        # Merge results: key by ticker. Start with full_batch_results.json items
        compiled_results: dict[str, dict[str, Any]] = {}
        for item in raw_data:
            normalized_item = _canonical_result_item(item)
            if normalized_item is not None:
                compiled_results[normalized_item["ticker"]] = normalized_item

        # Scan output/debates/*.json for any additional or missing individual debates
        for f in _safe_debate_files():
            try:
                async with aiofiles.open(f, mode="r", encoding="utf-8") as file:
                    file_content = await file.read()
                if file_content.strip():
                    normalized_item = _canonical_result_item(
                        json.loads(file_content),
                        expected_ticker=_expected_ticker_from_debate_path(f),
                    )
                    if normalized_item is not None:
                        ticker_key = normalized_item["ticker"]
                        if (
                            results_path is None
                            or results_path.name == RESULTS_PATH.name
                            or ticker_key not in compiled_results
                        ):
                            compiled_results[ticker_key] = normalized_item
            except Exception as e:
                logger.warning(f"[_load_results] Failed to read fallback {f}: {e}")

        if not compiled_results:
            raise _error(
                "NO_RESULTS",
                "No analysis results found. Run batch first.",
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
    from datetime import datetime, timedelta

    latest_time = None
    results = []

    for f in _safe_debate_files():
        try:
            mtime = f.stat().st_mtime
            content = f.read_text(encoding="utf-8")
            if not content.strip():
                continue
            data = _canonical_result_item(
                json.loads(content),
                expected_ticker=_expected_ticker_from_debate_path(f),
            )
            if data is None:
                continue
            if latest_time is None or mtime > latest_time:
                latest_time = mtime

            verdict = data.get("verdict") or {}
            rating = verdict.get("rating", "UNKNOWN")
            confidence = verdict.get("confidence", 0.0)
            conviction_score = data.get("conviction_score", 0.0)
            rounds = data.get("debate_rounds", 0)
            consensus = data.get("consensus_reached", False)

            metadata = data.get("metadata") or {}
            batch_ts = metadata.get("batch_timestamp") or metadata.get(
                "run_timestamp"
            )
            debate_date = datetime.fromtimestamp(mtime)
            if batch_ts:
                try:
                    debate_date = datetime.strptime(batch_ts, "%Y%m%d_%H%M%S")
                except ValueError:
                    try:
                        debate_date = datetime.strptime(
                            batch_ts.split("_")[0], "%Y%m%d"
                        )
                    except Exception:
                        pass

            results.append(
                {
                    "rating": rating,
                    "confidence": confidence,
                    "conviction_score": conviction_score,
                    "rounds": rounds,
                    "consensus": consensus,
                    "date": debate_date,
                }
            )
        except Exception as exc:
            logger.warning(f"[health] Failed to read debate artifact {f}: {exc}")

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
        "avg_conviction": round(total_conviction / total_debates, 3)
        if total_debates > 0
        else 0.0,
        "avg_confidence": round(total_confidence / total_debates, 3)
        if total_debates > 0
        else 0.0,
        "consensus_rate": round((consensus_count / total_debates) * 100, 2)
        if total_debates > 0
        else 0.0,
        "ratings_distribution": ratings_dist,
        "fresh_count": fresh_count,
        "stale_count": stale_count,
        "latest_debate_date": latest_date_str,
    }

    return {
        "status": "ok",
        "results_exist": _safe_root_file_exists(RESULTS_PATH)
        or _safe_root_file_exists(_merged_results_path())
        or bool(results),
        "latest_debate_date": latest_date_str,
        "debate_stats": debate_stats,
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


@router.get(
    "/stocks/{ticker}",
    dependencies=[Depends(_validate_stock_ticker_path)],
)
async def get_stock_detail(
    ticker: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        normalized_ticker = normalize_idx_ticker(ticker)
    except InvalidIDXTicker as exc:
        raise _error(
            "INVALID_IDX_TICKER",
            str(exc),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from exc
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
            f"Stock {normalized_ticker} not found.",
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
            async def run(
                self,
                ticker: str,
                current_price: float = 0.0,
                sector: str | None = None,
                **kwargs: Any,
            ) -> dict:
                final_result = {}
                async for event in self.stream_run(ticker):
                    if event.get("type") != "done":
                        await event_queue.put(event)
                    if event.get("type") == "verdict":
                        final_result = event.get("raw_state", {})
                    elif event.get("type") == "error":
                        if not final_result:
                            from core.orchestrator.legacy import _empty_result

                            final_result = _empty_result(
                                ticker, event.get("message", "Unknown error")
                            )
                return final_result

        def chamber_factory():
            return StreamingDebateChamber()

        # Phase 1: Pre-flight checks progress
        for ticker in payload.tickers:
            yield sse(
                {
                    "type": "progress",
                    "ticker": ticker,
                    "phase": "Pre-flight Checks",
                    "pct": 5,
                }
            )

        # Phase 2: Market Regime Detection progress
        for ticker in payload.tickers:
            yield sse(
                {
                    "type": "progress",
                    "ticker": ticker,
                    "phase": "Market Regime",
                    "pct": 10,
                }
            )

        user_config = {
            "total_capital": float(payload.total_capital),
            "max_loss_pct": float(payload.max_loss_pct),
            "max_positions": int(payload.max_positions),
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
                        event.setdefault("stage", "interim")
                    yield sse(event)
                    event_queue.task_done()
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"

            # Check if orchestrator raised an exception
            if orchestrator_task.exception() is not None:
                exc = orchestrator_task.exception()
                logger.error(f"Orchestrator pipeline failed: {exc}", exc_info=exc)
                for ticker in payload.tickers:
                    yield sse(
                        {
                            "type": "error",
                            "ticker": ticker,
                            "message": f"Orchestrator pipeline failed: {exc}",
                        }
                    )
            else:
                # Yield 95% progress for scoring and sizing
                for ticker in payload.tickers:
                    yield sse(
                        {
                            "type": "progress",
                            "ticker": ticker,
                            "phase": "Scoring & Sizing",
                            "pct": 95,
                        }
                    )

                # Invalidate cache and yield the final scored verdicts
                invalidate_results_cache()
                final_results = await _load_results()
                for ticker in payload.tickers:
                    if ticker in final_results:
                        yield sse(
                            {
                                "type": "verdict",
                                "ticker": ticker,
                                "stage": "final",
                                "result": final_results[ticker],
                            }
                        )
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
