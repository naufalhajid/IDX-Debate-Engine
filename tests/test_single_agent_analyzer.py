from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from services.context_pack_builder import ContextPack
from services.single_agent_analyzer import (
    SingleAgentAnalyzer,
    SingleAgentResult,
)


def _context(ticker: str = "BBCA") -> ContextPack:
    return ContextPack(
        ticker=ticker,
        as_of=datetime.now(timezone.utc),
        price=9000.0,
        fair_value=10500.0,
        fundamentals={"fair_value": 10500.0, "roe": 0.21},
        technicals={"ma50": 8800.0, "rsi14": 55.0},
        sentiment_summary="Sentiment neutral.",
        data_sources=["stockbit", "yfinance"],
        missing_fields=[],
        token_estimate=128,
    )


def _valid_response(rating: str = "BUY") -> str:
    return json.dumps(
        {
            "rating": rating,
            "confidence": 0.72,
            "entry_price_range": "8600 - 8800",
            "target_price": 9500.0,
            "stop_loss": 8300.0,
            "risk_reward_ratio": 2.1,
            "reasoning": "Momentum remains constructive while valuation support is adequate.",
            "key_risks": [
                "Breakdown below support",
                "Weak sector flow",
                "Earnings miss",
            ],
            "key_catalysts": ["Volume expansion", "Banking sector rotation"],
            "timeframe": "5-20 Trading Days",
            "execution_horizon_days": 10,
        }
    )


def test_build_prompt_contains_ticker_name_and_json_instruction() -> None:
    analyzer = SingleAgentAnalyzer()
    prompt = analyzer._build_prompt("BBCA", _context(), fair_value=None)

    assert "BBCA" in prompt
    assert "JSON" in prompt


def test_build_prompt_contains_fair_value_when_provided() -> None:
    analyzer = SingleAgentAnalyzer()
    prompt = analyzer._build_prompt("BBCA", _context(), fair_value=10500.0)

    assert "Fair Value Estimate: Rp 10,500" in prompt


def test_parse_response_valid_json_returns_success() -> None:
    analyzer = SingleAgentAnalyzer()
    result = analyzer._parse_response(
        _valid_response(),
        "BBCA",
        _context(),
        fair_value=10500.0,
        run_id="run-1",
        duration=0.25,
    )

    assert result.status == "success"
    assert result.verdict is not None
    assert result.verdict.ticker == "BBCA"
    assert result.verdict.timeframe == "5-20 Trading Days"
    assert result.verdict.execution_horizon_days == 10


def test_parse_response_with_json_fences_still_parses() -> None:
    analyzer = SingleAgentAnalyzer()
    result = analyzer._parse_response(
        f"```json\n{_valid_response()}\n```",
        "BBCA",
        _context(),
        fair_value=10500.0,
        run_id="run-1",
        duration=0.25,
    )

    assert result.status == "success"
    assert result.verdict is not None


def test_parse_response_invalid_json_returns_failed() -> None:
    analyzer = SingleAgentAnalyzer()
    result = analyzer._parse_response(
        "not json",
        "BBCA",
        _context(),
        fair_value=10500.0,
        run_id="run-1",
        duration=0.25,
    )

    assert result.status == "failed"
    assert result.error is not None


@pytest.mark.asyncio
async def test_analyze_llm_timeout_returns_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = SingleAgentAnalyzer(timeout_seconds=0.01)
    monkeypatch.setattr(analyzer, "_fetch_market_data", lambda ticker: _context(ticker))

    async def slow_llm(prompt: str) -> str:
        await asyncio.sleep(1)
        return _valid_response()

    monkeypatch.setattr(analyzer, "_call_llm", slow_llm)

    result = await analyzer.analyze("BBCA", "run-1")

    assert result.status == "timeout"
    assert result.verdict is None


@pytest.mark.asyncio
async def test_analyze_llm_exception_returns_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = SingleAgentAnalyzer()
    monkeypatch.setattr(analyzer, "_fetch_market_data", lambda ticker: _context(ticker))

    async def failing_llm(prompt: str) -> str:
        raise RuntimeError("provider down")

    monkeypatch.setattr(analyzer, "_call_llm", failing_llm)

    result = await analyzer.analyze("BBCA", "run-1")

    assert result.status == "failed"
    assert "provider down" in str(result.error)


@pytest.mark.asyncio
async def test_analyze_valid_response_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = SingleAgentAnalyzer()
    monkeypatch.setattr(analyzer, "_fetch_market_data", lambda ticker: _context(ticker))

    async def fake_llm(prompt: str) -> str:
        return _valid_response()

    monkeypatch.setattr(analyzer, "_call_llm", fake_llm)

    result = await analyzer.analyze("BBCA", "run-1")

    assert result.status == "success"
    assert result.verdict is not None


@pytest.mark.asyncio
async def test_analyze_verdict_rating_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = SingleAgentAnalyzer()
    monkeypatch.setattr(analyzer, "_fetch_market_data", lambda ticker: _context(ticker))

    async def fake_llm(prompt: str) -> str:
        return _valid_response("HOLD")

    monkeypatch.setattr(analyzer, "_call_llm", fake_llm)

    result = await analyzer.analyze("BBCA", "run-1")

    assert result.verdict is not None
    assert result.verdict.rating in {"BUY", "HOLD", "AVOID"}


@pytest.mark.asyncio
async def test_analyze_verdict_mode_is_single_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = SingleAgentAnalyzer()
    monkeypatch.setattr(analyzer, "_fetch_market_data", lambda ticker: _context(ticker))

    async def fake_llm(prompt: str) -> str:
        return _valid_response()

    monkeypatch.setattr(analyzer, "_call_llm", fake_llm)

    result = await analyzer.analyze("BBCA", "run-1")

    assert result.verdict is not None
    assert result.verdict.mode == "single_agent"


@pytest.mark.asyncio
async def test_analyze_batch_returns_one_result_per_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = SingleAgentAnalyzer()

    async def fake_analyze(ticker: str, run_id: str) -> SingleAgentResult:
        return analyzer._parse_response(
            _valid_response(),
            ticker,
            _context(ticker),
            fair_value=10500.0,
            run_id=run_id,
            duration=0.1,
        )

    monkeypatch.setattr(analyzer, "analyze", fake_analyze)

    results = await analyzer.analyze_batch(["BBCA", "ADRO"], "run-1")

    assert [result.ticker for result in results] == ["BBCA", "ADRO"]


@pytest.mark.asyncio
async def test_analyze_batch_one_failure_does_not_stop_other_tickers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = SingleAgentAnalyzer()

    async def fake_analyze(ticker: str, run_id: str) -> SingleAgentResult:
        if ticker == "BAD":
            raise RuntimeError("boom")
        return analyzer._parse_response(
            _valid_response(),
            ticker,
            _context(ticker),
            fair_value=10500.0,
            run_id=run_id,
            duration=0.1,
        )

    monkeypatch.setattr(analyzer, "analyze", fake_analyze)

    results = await analyzer.analyze_batch(["BBCA", "BAD", "ADRO"], "run-1")

    assert len(results) == 3
    assert results[1].status == "failed"
    assert results[2].status == "success"
