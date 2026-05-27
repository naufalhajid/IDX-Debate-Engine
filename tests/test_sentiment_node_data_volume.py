from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from services import debate_chamber as dc
from services.debate_chamber import DebateChamber


def _post(post_id: str, content: str | None = None) -> dict:
    return {
        "id": post_id,
        "content": content or f"{post_id} bullish breakout",
        "created_at": "2026-05-27T09:00:00+07:00",
    }


def _state() -> dict:
    return {
        "ticker": "BBCA",
        "metadata": {},
        "news_brief": "",
        "news_confidence_adjustment": 0.0,
    }


@pytest.fixture
def chamber(monkeypatch) -> DebateChamber:
    instance = object.__new__(DebateChamber)
    instance.flash_llm = SimpleNamespace(model="gemini-2.5-flash")
    instance._llm_call_counts = {}

    async def no_sleep(delay: float) -> None:
        return None

    async def no_news(state: dict, ticker: str) -> dict:
        return {}

    monkeypatch.setattr(dc.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(dc, "_news_context_for_state", no_news)
    monkeypatch.setattr(dc, "_ledger_stage_start", lambda *args, **kwargs: None)
    monkeypatch.setattr(dc, "_ledger_stage_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(dc, "_ledger_stage_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr(dc, "_ledger_stage_partial", lambda *args, **kwargs: None)
    monkeypatch.setattr(instance, "_record_observation", lambda *args, **kwargs: None)
    return instance


@pytest.mark.asyncio
class TestSentimentNodeDataVolume:
    async def test_deduplication_prefers_stockbit_id_field(
        self,
        chamber: DebateChamber,
    ) -> None:
        assert chamber._stockbit_post_id(
            {"id": "stockbit-id", "post_id": "fallback-post-id"}
        ) == "stockbit-id"
        assert chamber._stockbit_post_id({"post_id": "fallback-post-id"}) == (
            "fallback-post-id"
        )
        assert chamber._stockbit_post_id({"stream_id": "fallback-stream-id"}) == (
            "fallback-stream-id"
        )

    async def test_combined_fetch_returns_sufficient_posts(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        captured: dict[str, list[dict]] = {}

        async def fake_fetch_url(url: str) -> dict:
            if url.endswith("/pinned"):
                return {"data": _post("pinned-1")}
            return {
                "data": {
                    "stream": [_post(f"s{i}") for i in range(1, 21)],
                }
            }

        async def fake_invoke(state, llm, messages):
            captured["posts"] = json.loads(messages[-1].content)
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "position": "BUY",
                        "confidence": 0.7,
                        "status": "OK",
                        "reasoning": "Active Stockbit discussion.",
                        "key_signals": ["volume"],
                    }
                )
            )

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke)

        await chamber._sentiment_node(_state())

        assert len(captured["posts"]) == 21
        assert len(captured["posts"]) >= 5
        assert captured["posts"][0]["id"] == "pinned-1"

    async def test_deduplication_removes_overlapping_posts(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        captured: dict[str, list[dict]] = {}

        async def fake_fetch_url(url: str) -> dict:
            if url.endswith("/pinned"):
                return {"data": [_post("p1"), _post("p2")]}
            return {
                "data": {
                    "stream": [
                        _post("p1"),
                        _post("s1"),
                        _post("s2"),
                        _post("s3"),
                        _post("s4"),
                    ],
                }
            }

        async def fake_invoke(state, llm, messages):
            captured["posts"] = json.loads(messages[-1].content)
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "position": "HOLD",
                        "confidence": 0.4,
                        "status": "OK",
                        "reasoning": "Mixed but sufficient discussion.",
                        "key_signals": ["mixed"],
                    }
                )
            )

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke)

        await chamber._sentiment_node(_state())

        ids = [post["id"] for post in captured["posts"]]
        assert ids == ["p1", "p2", "s1", "s2", "s3", "s4"]
        assert len(ids) == 6

    async def test_both_endpoints_fail_returns_insufficient_data(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        async def fake_fetch_url(url: str) -> dict:
            raise httpx.ConnectError("stockbit unavailable")

        async def fail_if_called(state, llm, messages):
            raise AssertionError("LLM should not run when both endpoints fail")

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", fail_if_called)

        result = await chamber._sentiment_node(_state())
        signal = chamber._extract_agent_signal(
            result["sentiment_data"],
            "sentiment_specialist",
        )

        assert "INSUFFICIENT_DATA" in result["sentiment_data"]
        assert signal["position"] == "HOLD"
        assert signal["confidence"] == 0.0

    async def test_malformed_llm_json_falls_back_to_hold(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        async def fake_fetch_url(url: str) -> dict:
            if url.endswith("/pinned"):
                return {"data": _post("pinned-1")}
            return {
                "data": {
                    "stream": [_post(f"s{i}") for i in range(1, 5)],
                }
            }

        async def malformed_response(state, llm, messages):
            return SimpleNamespace(content="BUY karena ramai dibahas, confidence tinggi")

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", malformed_response)

        result = await chamber._sentiment_node(_state())
        signal = chamber._extract_agent_signal(
            result["sentiment_data"],
            "sentiment_specialist",
        )

        assert "PARSE_ERROR" in result["sentiment_data"]
        assert signal["position"] == "HOLD"
        assert signal["confidence"] == 0.0
