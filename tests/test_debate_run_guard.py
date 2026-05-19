import asyncio

import pytest

from services.debate_run_guard import GuardResult, run_with_guard


@pytest.mark.asyncio
async def test_run_with_guard_returns_timeout_result() -> None:
    async def slow_run() -> dict[str, str]:
        await asyncio.sleep(1)
        return {"verdict": "late"}

    result = await run_with_guard("ADRO", slow_run(), timeout_seconds=0)

    assert result == {
        "ticker": "ADRO",
        "status": "timeout",
        "error": "TIMEOUT",
        "result": None,
    }
    assert GuardResult.model_validate(result).status == "timeout"


@pytest.mark.asyncio
async def test_run_with_guard_returns_failed_result_on_exception() -> None:
    async def failed_run() -> dict[str, str]:
        raise RuntimeError("provider unavailable")

    result = await run_with_guard("BBRI", failed_run())

    assert result == {
        "ticker": "BBRI",
        "status": "failed",
        "error": "provider unavailable",
        "result": None,
    }
    assert GuardResult.model_validate(result).status == "failed"


@pytest.mark.asyncio
async def test_run_with_guard_returns_ok_result_on_success() -> None:
    expected_payload = {"verdict": "BUY", "confidence": 0.82}

    async def successful_run() -> dict[str, float | str]:
        return expected_payload

    result = await run_with_guard("TLKM", successful_run())

    assert result == {
        "ticker": "TLKM",
        "status": "ok",
        "error": None,
        "result": expected_payload,
    }
    assert GuardResult.model_validate(result).status == "ok"
