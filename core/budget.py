"""
core/budget.py — Per-process budget guard for Gemini Pro calls.

Prevents runaway LLM spend by capping the number of Pro invocations
per process (daemon / script run). Designed to be imported from both
`orchestrator.py` and `services/debate_chamber.py` without creating a
circular dependency.

Usage:
    from core.budget import check_and_increment_pro_budget

    # Inside an async path where a Pro call is about to happen:
    await check_and_increment_pro_budget()
    response = await pro_llm.ainvoke(messages)

Reset at the start of each batch run via ``reset_budget()`` if desired.
"""

from __future__ import annotations

import asyncio
import os

# ---------------------------------------------------------------------------
# Configuration — override via env vars if needed
# ---------------------------------------------------------------------------

#: Absolute ceiling on Gemini Pro calls per process.  Safe default for
#: the Rp 500 k/month budget target at current pricing.
MAX_PRO_CALLS_PER_RUN: int = int(os.environ.get("MAX_PRO_CALLS_PER_RUN", "200"))

#: Absolute ceiling on Gemini Flash calls per process.  Flash is cheap
#: so we keep the cap permissive but finite — guards against runaway
#: loops caused by buggy graphs.
MAX_FLASH_CALLS_PER_RUN: int = int(os.environ.get("MAX_FLASH_CALLS_PER_RUN", "2000"))


# ---------------------------------------------------------------------------
# State (module-level — one counter per process)
# ---------------------------------------------------------------------------

_pro_call_counter: int = 0
_flash_call_counter: int = 0
_counter_lock: asyncio.Lock | None = None  # lazy-init so import-time is cheap


def _lock() -> asyncio.Lock:
    """Lazy-initialise the asyncio lock on first use.

    Creating an ``asyncio.Lock()`` at module-import time ties the lock
    to whatever event-loop happens to exist at import.  Some test
    runners import modules outside of a running loop — we sidestep this
    by constructing the lock on first call.
    """

    global _counter_lock
    if _counter_lock is None:
        _counter_lock = asyncio.Lock()
    return _counter_lock


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class BudgetExhaustedError(RuntimeError):
    """Raised when the daily Pro call budget has been exceeded."""


async def check_and_increment_pro_budget(cost: int = 1) -> None:
    """Atomically increment the Pro counter or raise if budget exhausted.

    Args:
        cost: Number of Pro calls this operation will consume.  Default 1.

    Raises:
        BudgetExhaustedError: If the increment would exceed
            ``MAX_PRO_CALLS_PER_RUN``. No state mutation occurs on raise.
    """

    global _pro_call_counter
    async with _lock():
        if _pro_call_counter + cost > MAX_PRO_CALLS_PER_RUN:
            raise BudgetExhaustedError(
                f"🛑 Daily Pro-call budget exhausted "
                f"({_pro_call_counter}/{MAX_PRO_CALLS_PER_RUN}). "
                "Aborting further Pro invocations."
            )
        _pro_call_counter += cost


async def check_and_increment_flash_budget(cost: int = 1) -> None:
    """Same semantics as ``check_and_increment_pro_budget`` but for Flash."""

    global _flash_call_counter
    async with _lock():
        if _flash_call_counter + cost > MAX_FLASH_CALLS_PER_RUN:
            raise BudgetExhaustedError(
                f"🛑 Flash-call budget exhausted "
                f"({_flash_call_counter}/{MAX_FLASH_CALLS_PER_RUN})."
            )
        _flash_call_counter += cost


def get_usage() -> dict:
    """Return a snapshot of budget usage (non-blocking, test-friendly)."""

    return {
        "pro_calls": _pro_call_counter,
        "pro_budget": MAX_PRO_CALLS_PER_RUN,
        "flash_calls": _flash_call_counter,
        "flash_budget": MAX_FLASH_CALLS_PER_RUN,
    }


def reset_budget() -> None:
    """Reset all counters. Intended for test fixtures and batch starts."""

    global _pro_call_counter, _flash_call_counter
    _pro_call_counter = 0
    _flash_call_counter = 0
