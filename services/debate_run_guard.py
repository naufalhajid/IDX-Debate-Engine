"""Guarded execution for per-ticker debate runs."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Literal

from pydantic import BaseModel, ConfigDict


class GuardResult(BaseModel):
    """Structured outcome from a guarded debate coroutine."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    status: Literal["ok", "timeout", "failed"]
    error: str | None
    result: Any | None


def _exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return type(exc).__name__


def _is_timeout_exception(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True

    exc_name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        "timeout" in exc_name
        or "timed out" in message
        or "deadline exceeded" in message
    )


async def run_with_guard(
    ticker: str,
    coro: Awaitable[Any],
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run a per-ticker coroutine with timeout and normalized failure output."""
    try:
        result = await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return GuardResult(
            ticker=ticker,
            status="timeout",
            error="TIMEOUT",
            result=None,
        ).model_dump()
    except Exception as exc:
        if _is_timeout_exception(exc):
            return GuardResult(
                ticker=ticker,
                status="timeout",
                error=_exception_message(exc),
                result=None,
            ).model_dump()
        return GuardResult(
            ticker=ticker,
            status="failed",
            error=_exception_message(exc),
            result=None,
        ).model_dump()

    return GuardResult(
        ticker=ticker,
        status="ok",
        error=None,
        result=result,
    ).model_dump()
