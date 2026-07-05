from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from services import debate_chamber as dc
from services.debate_chamber import DebateChamber


def _post(
    stream_id: str,
    content: str | None = None,
    *,
    post_id: str | None = None,
    stockbit_id: str | None = None,
    is_verified: bool = False,
) -> dict:
    return {
        "id": stockbit_id or f"id-{stream_id}",
        "post_id": post_id or f"post-{stream_id}",
        "stream_id": stream_id,
        "content": content or f"{stream_id} bullish breakout",
        "created_at": "2026-05-27T09:00:00+07:00",
        "user": {"is_verified": is_verified},
    }


def _large_post(stream_id: str) -> dict:
    post = _post(stream_id, content=("ramai dibahas bullish " * 80))
    post.update(
        {
            "attachments": [
                {"url": f"https://example.test/{stream_id}/{i}"} for i in range(20)
            ],
            "comments": [{"content": "nested comment " * 30} for _ in range(20)],
            "raw_html": "<div>" + ("oversized " * 300) + "</div>",
        }
    )
    return post


def _state() -> dict:
    return {
        "ticker": "BBCA",
        "metadata": {},
        "news_brief": "",
        "news_confidence_adjustment": 0.0,
    }


def _ok_sentiment_response() -> SimpleNamespace:
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


def _paged_stream_response(
    posts_by_category: dict[str, list[dict]],
):
    async def fake_post_url(url: str, payload: dict) -> dict:
        assert url.endswith("/stream/v3/symbol/BBCA")
        category = payload["category"]
        if payload["last_stream_id"] in (0, "0"):
            return {"data": {"stream": posts_by_category.get(category, [])}}
        return {"data": {"stream": []}}

    return fake_post_url


@pytest.fixture
def chamber(monkeypatch) -> DebateChamber:
    instance = object.__new__(DebateChamber)
    instance.flash_llm = SimpleNamespace(model="gemini-2.5-flash")
    instance._llm_call_counts = {}

    async def no_sleep(delay: float) -> None:
        return None

    async def no_news(
        state: dict, ticker: str, llm_news_sentiment: str | None = None
    ) -> dict:
        return {}

    async def no_headlines(ticker: str, limit: int = 6) -> str:
        return ""

    monkeypatch.setattr(dc.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(dc, "_news_context_for_state", no_news)
    monkeypatch.setattr(dc, "_news_headlines_for_llm", no_headlines)
    # This file tests post volume/dedup/truncation, not sentiment classification;
    # a live SENTIMENT_INDOBERT_ENABLED=True would append a prior block after the
    # posts JSON these tests capture, breaking their json.loads() assumption.
    monkeypatch.setattr(dc, "indobert_sentiment_prior", lambda posts: ("", {}))
    monkeypatch.setattr(dc, "_ledger_stage_start", lambda *args, **kwargs: None)
    monkeypatch.setattr(dc, "_ledger_stage_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(dc, "_ledger_stage_failure", lambda *args, **kwargs: None)
    monkeypatch.setattr(dc, "_ledger_stage_partial", lambda *args, **kwargs: None)
    monkeypatch.setattr(instance, "_record_observation", lambda *args, **kwargs: None)
    return instance


class TestExtractStockbitPosts:
    def test_null_data_envelope_returns_empty(self) -> None:
        raw = {"message": "Successfully retrieved pinned post", "data": None}
        assert DebateChamber._extract_stockbit_posts(raw) == []

    def test_null_value_data_dict_returns_empty(self) -> None:
        raw = {"data": {"stream_id": None, "content": None, "id": None}}
        assert DebateChamber._extract_stockbit_posts(raw) == []

    def test_id_only_no_content_returns_empty(self) -> None:
        raw = {"data": {"stream_id": "abc", "content": None}}
        assert DebateChamber._extract_stockbit_posts(raw) == []

    def test_content_only_no_id_returns_empty(self) -> None:
        raw = {"data": {"content": "bullish", "stream_id": None}}
        assert DebateChamber._extract_stockbit_posts(raw) == []

    def test_real_post_dict_in_data_extracted(self) -> None:
        post = _post("pinned-1")
        result = DebateChamber._extract_stockbit_posts({"data": post})
        assert len(result) == 1
        assert result[0]["stream_id"] == "pinned-1"

    def test_real_post_list_in_data_extracted(self) -> None:
        result = DebateChamber._extract_stockbit_posts(
            {"data": [_post("p1"), _post("p2")]}
        )
        assert [r["stream_id"] for r in result] == ["p1", "p2"]

    def test_stream_in_data_dict_extracted(self) -> None:
        result = DebateChamber._extract_stockbit_posts(
            {"data": {"stream": [_post("s1"), _post("s2")]}}
        )
        assert [r["stream_id"] for r in result] == ["s1", "s2"]

    def test_root_level_null_id_returns_empty(self) -> None:
        raw = {"stream_id": None, "content": "text", "message": "api msg"}
        assert DebateChamber._extract_stockbit_posts(raw) == []

    def test_root_level_real_post_extracted(self) -> None:
        raw = _post("root-post")
        result = DebateChamber._extract_stockbit_posts(raw)
        assert len(result) == 1
        assert result[0]["stream_id"] == "root-post"


class TestSentimentNodeDataVolume:
    def test_deduplication_prefers_stockbit_stream_id_field(
        self,
        chamber: DebateChamber,
    ) -> None:
        assert (
            chamber._stockbit_post_id(
                {
                    "stream_id": "stream-id",
                    "id": "stockbit-id",
                    "post_id": "fallback-post-id",
                }
            )
            == "stream-id"
        )
        assert chamber._stockbit_post_id({"id": "stockbit-id"}) == "stockbit-id"
        assert chamber._stockbit_post_id({"post_id": "fallback-post-id"}) == (
            "fallback-post-id"
        )

    def test_stream_category_fetch_uses_post_pagination(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        calls: list[dict] = []

        async def fake_post_url(url: str, payload: dict) -> dict:
            assert url.endswith("/stream/v3/symbol/BBCA")
            calls.append(dict(payload))
            if payload["last_stream_id"] in (0, "0"):
                return {"data": {"stream": [_post("p1"), _post("p2")]}}
            if payload["last_stream_id"] == "p2":
                return {"data": {"stream": [_post("p3")]}}
            return {"data": {"stream": []}}

        monkeypatch.setattr(chamber, "_post_url", fake_post_url)

        posts = asyncio.run(
            chamber._fetch_sentiment_stream_posts(
                "BBCA",
                "STREAM_CATEGORY_IDEAS",
                pages=3,
                limit=2,
            )
        )

        assert [post["stream_id"] for post in posts] == ["p1", "p2", "p3"]
        assert [call["category"] for call in calls] == [
            "STREAM_CATEGORY_IDEAS",
            "STREAM_CATEGORY_IDEAS",
            "STREAM_CATEGORY_IDEAS",
        ]
        assert [call["last_stream_id"] for call in calls] == [0, "p2", "p3"]
        assert [call["limit"] for call in calls] == [2, 2, 2]

    def test_combined_fetch_returns_sufficient_posts(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        captured: dict[str, list[dict]] = {}

        async def fake_fetch_url(url: str) -> dict:
            if url.endswith("/pinned"):
                return {"data": _post("pinned-1")}
            raise AssertionError(f"unexpected url: {url}")

        async def fake_invoke(state, llm, messages):
            captured["posts"] = json.loads(messages[-1].content)
            assert "SIGNAL WEIGHTING" in messages[0].content
            return _ok_sentiment_response()

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(
            chamber,
            "_post_url",
            _paged_stream_response(
                {
                    "STREAM_CATEGORY_IDEAS": [_post(f"s{i}") for i in range(1, 16)],
                    "STREAM_CATEGORY_NEWS": [_post(f"n{i}") for i in range(1, 6)],
                }
            ),
        )
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke)

        asyncio.run(chamber._sentiment_node(_state()))

        assert len(captured["posts"]) == 21
        assert len(captured["posts"]) >= 5
        assert captured["posts"][0]["stream_id"] == "pinned-1"

    def test_deduplication_removes_overlapping_posts(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        captured: dict[str, list[dict]] = {}

        async def fake_fetch_url(url: str) -> dict:
            if url.endswith("/pinned"):
                return {"data": [_post("p1"), _post("p2")]}
            raise AssertionError(f"unexpected url: {url}")

        async def fake_invoke(state, llm, messages):
            captured["posts"] = json.loads(messages[-1].content)
            return _ok_sentiment_response()

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(
            chamber,
            "_post_url",
            _paged_stream_response(
                {
                    "STREAM_CATEGORY_IDEAS": [
                        _post("p1", stockbit_id="different-id"),
                        _post("s1"),
                        _post("s2"),
                        _post("s3"),
                    ],
                    "STREAM_CATEGORY_NEWS": [_post("n1"), _post("n2")],
                }
            ),
        )
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke)

        asyncio.run(chamber._sentiment_node(_state()))

        stream_ids = [post["stream_id"] for post in captured["posts"]]
        assert stream_ids == ["p1", "p2", "s1", "s2", "s3", "n1", "n2"]
        assert len(stream_ids) == 7

    def test_both_endpoints_fail_returns_insufficient_data(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        async def fake_fetch_url(url: str) -> dict:
            raise httpx.ConnectError("stockbit unavailable")

        async def fake_post_url(url: str, payload: dict) -> dict:
            raise httpx.ConnectError("stockbit unavailable")

        async def fail_if_called(state, llm, messages):
            raise AssertionError("LLM should not run when all endpoints fail")

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(chamber, "_post_url", fake_post_url)
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", fail_if_called)

        result = asyncio.run(chamber._sentiment_node(_state()))
        signal = chamber._extract_agent_signal(
            result["sentiment_data"],
            "sentiment_specialist",
        )

        assert "INSUFFICIENT_DATA" in result["sentiment_data"]
        assert signal["position"] == "HOLD"
        assert signal["confidence"] == 0.0

    def test_verified_weight_field_added_to_posts(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        captured: dict[str, list[dict]] = {}

        async def fake_fetch_url(url: str) -> dict:
            if url.endswith("/pinned"):
                return {"data": _post("pinned-verified", is_verified=True)}
            raise AssertionError(f"unexpected url: {url}")

        async def fake_invoke(state, llm, messages):
            captured["posts"] = json.loads(messages[-1].content)
            return _ok_sentiment_response()

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(
            chamber,
            "_post_url",
            _paged_stream_response(
                {
                    "STREAM_CATEGORY_IDEAS": [_post("idea-retail")],
                    "STREAM_CATEGORY_NEWS": [_post("news-retail")],
                }
            ),
        )
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke)

        asyncio.run(chamber._sentiment_node(_state()))

        weights = {
            post["stream_id"]: post["_verified_weight"] for post in captured["posts"]
        }
        assert weights == {
            "pinned-verified": 1.5,
            "idea-retail": 1.0,
            "news-retail": 1.0,
        }
        assert len(captured["posts"]) == 3

    def test_non_verified_posts_are_not_excluded(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        captured: dict[str, list[dict]] = {}

        async def fake_fetch_url(url: str) -> dict:
            if url.endswith("/pinned"):
                return {"data": [_post("p1"), _post("p2")]}
            raise AssertionError(f"unexpected url: {url}")

        async def fake_invoke(state, llm, messages):
            captured["posts"] = json.loads(messages[-1].content)
            return _ok_sentiment_response()

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(
            chamber,
            "_post_url",
            _paged_stream_response(
                {
                    "STREAM_CATEGORY_IDEAS": [_post("s1"), _post("s2")],
                    "STREAM_CATEGORY_NEWS": [_post("n1"), _post("n2")],
                }
            ),
        )
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke)

        asyncio.run(chamber._sentiment_node(_state()))

        assert [post["stream_id"] for post in captured["posts"]] == [
            "p1",
            "p2",
            "s1",
            "s2",
            "n1",
            "n2",
        ]
        assert all(post["_verified_weight"] == 1.0 for post in captured["posts"])

    def test_llm_context_truncated_at_10000_chars(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        captured: dict[str, str] = {}
        large_text = "large content " * 80

        async def fake_fetch_url(url: str) -> dict:
            if url.endswith("/pinned"):
                return {"data": _post("pinned-1", content="pinned must stay")}
            raise AssertionError(f"unexpected url: {url}")

        async def fake_invoke(state, llm, messages):
            captured["serialized"] = messages[-1].content
            return _ok_sentiment_response()

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(
            chamber,
            "_post_url",
            _paged_stream_response(
                {
                    "STREAM_CATEGORY_IDEAS": [
                        _post(f"s{i}", content=large_text) for i in range(1, 31)
                    ],
                    "STREAM_CATEGORY_NEWS": [
                        _post(f"n{i}", content=large_text) for i in range(1, 20)
                    ],
                }
            ),
        )
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke)

        asyncio.run(chamber._sentiment_node(_state()))

        serialized = captured["serialized"]
        posts = json.loads(serialized)
        assert len(serialized) <= 10_000
        assert any("_note" in item for item in posts)
        assert posts[0]["stream_id"] == "pinned-1"
        assert posts[0]["content"] == "pinned must stay"

    def test_large_raw_posts_are_compacted_before_truncation(
        self,
        chamber: DebateChamber,
    ) -> None:
        raw_posts = [_large_post(f"s{i}") for i in range(1, 40)]

        serialized, truncated_count = chamber._serialize_stockbit_posts_for_llm(
            raw_posts,
            max_chars=10_000,
        )
        posts = json.loads(serialized)
        visible_posts = [post for post in posts if "_note" not in post]

        assert len(serialized) <= 10_000
        assert len(visible_posts) >= 5
        assert truncated_count < 35
        assert all("attachments" not in post for post in visible_posts)
        assert all("comments" not in post for post in visible_posts)
        assert all(len(post["content"]) <= 283 for post in visible_posts)

    def test_malformed_llm_json_falls_back_to_hold(
        self,
        chamber: DebateChamber,
        monkeypatch,
    ) -> None:
        async def fake_fetch_url(url: str) -> dict:
            if url.endswith("/pinned"):
                return {"data": _post("pinned-1")}
            raise AssertionError(f"unexpected url: {url}")

        async def malformed_response(state, llm, messages):
            return SimpleNamespace(
                content="BUY karena ramai dibahas, confidence tinggi"
            )

        monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
        monkeypatch.setattr(
            chamber,
            "_post_url",
            _paged_stream_response(
                {
                    "STREAM_CATEGORY_IDEAS": [_post(f"s{i}") for i in range(1, 5)],
                    "STREAM_CATEGORY_NEWS": [],
                }
            ),
        )
        monkeypatch.setattr(chamber, "_invoke_llm_for_state", malformed_response)

        result = asyncio.run(chamber._sentiment_node(_state()))
        signal = chamber._extract_agent_signal(
            result["sentiment_data"],
            "sentiment_specialist",
        )

        assert "PARSE_ERROR" in result["sentiment_data"]
        assert signal["position"] == "HOLD"
        assert signal["confidence"] == 0.0
