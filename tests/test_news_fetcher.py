from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
import pandas as pd

from services.news_fetcher import (
    BREAKING_NEWS_HOURS,
    NEWS_LOOKBACK_DAYS,
    STALE_NEWS_HOURS,
    NewsEventTag,
    NewsFetcher,
    NewsSentiment,
    _parse_rss_items,
    fetch_news_rss,
)


def _ts(hours_ago: int = 1) -> int:
    return int((datetime.now(timezone.utc) - timedelta(hours=hours_ago)).timestamp())


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


def _raw(
    title: str,
    *,
    hours_ago: int = 1,
    include_date: bool = True,
    url: str | None = None,
    summary: str | None = None,
    description: str | None = None,
    content: dict | None = None,
) -> dict:
    raw = {
        "title": title,
        "publisher": "UnitTest News",
        "link": url or f"https://example.test/news/{_slug(title)}",
    }
    if include_date:
        raw["providerPublishTime"] = _ts(hours_ago)
    if summary is not None:
        raw["summary"] = summary
    if description is not None:
        raw["description"] = description
    if content is not None:
        raw["content"] = content
    return raw


def _mock_ticker(news: list[dict] | Exception):
    mocked_fetch = Mock()

    async def fake_fetch_news(self, ticker: str, company_name: str = "") -> list[dict]:
        mocked_fetch(ticker, company_name=company_name)
        if isinstance(news, Exception):
            raise news
        return news

    patcher = patch(
        "services.news_fetcher.NewsFetcher.fetch_news_async",
        fake_fetch_news,
    )
    patcher.start()
    return patcher, mocked_fetch


def test_classify_item_positive_title() -> None:
    item = NewsFetcher().classify_item(_raw("BBCA laba naik strong growth"), "BBCA")

    assert item.sentiment is NewsSentiment.POSITIVE
    assert item.sentiment_score > 0


def test_classify_item_negative_title() -> None:
    item = NewsFetcher().classify_item(_raw("BBCA rugi turun below estimate"), "BBCA")

    assert item.sentiment is NewsSentiment.NEGATIVE
    assert item.sentiment_score < 0


def test_classify_item_uses_summary_and_description() -> None:
    item = NewsFetcher().classify_item(
        _raw(
            "BBCA update emiten",
            summary="Laba turun dan penurunan kinerja below estimate",
            description="Manajemen menjelaskan pelemahan margin",
        ),
        "BBCA",
    )

    assert item.sentiment is NewsSentiment.NEGATIVE
    assert item.sentiment_score < 0


def test_classify_item_corporate_action() -> None:
    item = NewsFetcher().classify_item(_raw("BBCA umumkan stock split"), "BBCA")

    assert item.is_corporate_action is True
    assert NewsEventTag.CORPORATE_ACTION in item.event_tags
    assert item.sentiment is NewsSentiment.NEUTRAL


def test_classify_item_macro_event() -> None:
    item = NewsFetcher().classify_item(_raw("IHSG bergerak karena BI rate"), "BBCA")

    assert item.is_macro is True
    assert NewsEventTag.MACRO_EVENT in item.event_tags
    assert item.sentiment is NewsSentiment.NEUTRAL


def test_classify_item_recent_is_breaking() -> None:
    item = NewsFetcher().classify_item(_raw("BBCA profit growth", hours_ago=2), "BBCA")

    assert item.is_breaking is True
    assert NewsEventTag.BREAKING in item.event_tags


def test_classify_item_old_is_not_breaking() -> None:
    item = NewsFetcher().classify_item(
        _raw("BBCA profit growth", hours_ago=BREAKING_NEWS_HOURS + 1),
        "BBCA",
    )

    assert item.is_breaking is False
    assert NewsEventTag.BREAKING not in item.event_tags


def test_invalid_ticker_returns_empty_bundle_without_fetch() -> None:
    patcher, mocked_ticker = _mock_ticker([_raw("BBCA profit growth")])
    try:
        bundle = NewsFetcher().build_bundle(".JK")
    finally:
        patcher.stop()

    assert mocked_ticker.call_count == 0
    assert bundle.data_available is False
    assert bundle.overall_sentiment is NewsSentiment.UNKNOWN
    assert bundle.confidence_adjustment < 0
    assert "Invalid IDX ticker" in bundle.confidence_adjustment_reason


def test_lowercase_and_jk_suffix_ticker_normalization() -> None:
    patcher, mocked_ticker = _mock_ticker([_raw("BBCA profit growth", hours_ago=2)])
    try:
        bundle = NewsFetcher().build_bundle("bbca.jk")
    finally:
        patcher.stop()

    assert bundle.ticker == "BBCA"
    mocked_ticker.assert_called_once_with("BBCA", company_name="")


def test_build_bundle_empty_response_marks_unavailable() -> None:
    patcher, _ = _mock_ticker([])
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.data_available is False
    assert bundle.confidence_adjustment == 0.0
    assert "No news data available" in bundle.confidence_adjustment_reason


def test_build_bundle_negative_news_sets_negative_sentiment() -> None:
    news = [
        _raw("BBCA rugi turun"),
        _raw("BBCA melemah below support"),
        _raw("BBCA downgrade setelah miss target"),
    ]
    patcher, _ = _mock_ticker(news)
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.overall_sentiment is NewsSentiment.NEGATIVE
    assert bundle.confidence_adjustment <= -0.10


def test_build_bundle_breaking_negative_news_penalizes_more() -> None:
    patcher, _ = _mock_ticker([_raw("BBCA rugi turun below", hours_ago=2)])
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.has_breaking_news is True
    assert bundle.confidence_adjustment <= -0.20


def test_build_bundle_breaking_positive_news_amplifies_more() -> None:
    patcher, _ = _mock_ticker([_raw("BBCA laba naik profit above growth strong", hours_ago=2)])
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.has_breaking_news is True
    assert bundle.confidence_adjustment == 0.10
    assert f"{BREAKING_NEWS_HOURS}h" in bundle.confidence_adjustment_reason


def test_breaking_negative_wins_over_breaking_positive() -> None:
    patcher, _ = _mock_ticker([
        _raw("BBCA laba naik profit strong", hours_ago=2),
        _raw("BBCA rugi turun below", hours_ago=1),
    ])
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.has_breaking_news is True
    assert bundle.confidence_adjustment <= -0.20


def test_breaking_negative_corporate_action_gets_negative_adjustment() -> None:
    patcher, _ = _mock_ticker([_raw("BBCA rights issue rugi turun below", hours_ago=2)])
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.items[0].sentiment is NewsSentiment.NEGATIVE
    assert bundle.items[0].is_breaking is True
    assert bundle.items[0].is_corporate_action is True
    assert bundle.confidence_adjustment == -0.20
    assert f"{BREAKING_NEWS_HOURS}h" in bundle.confidence_adjustment_reason


def test_corporate_action_does_not_override_negative_sentiment() -> None:
    item = NewsFetcher().classify_item(
        _raw("BBCA rights issue rugi turun below"),
        "BBCA",
    )

    assert item.is_corporate_action is True
    assert NewsEventTag.CORPORATE_ACTION in item.event_tags
    assert item.sentiment is NewsSentiment.NEGATIVE


def test_macro_event_does_not_override_negative_sentiment() -> None:
    item = NewsFetcher().classify_item(
        _raw("BBCA turun dan IHSG melemah karena BI rate"),
        "BBCA",
    )

    assert item.is_macro is True
    assert NewsEventTag.MACRO_EVENT in item.event_tags
    assert item.sentiment is NewsSentiment.NEGATIVE


def test_unknown_publish_date_gets_warning_or_penalty() -> None:
    fetcher = NewsFetcher()
    unknown_date_item = fetcher.classify_item(
        _raw("BBCA laba naik", include_date=False),
        "BBCA",
    )
    dated_item = fetcher.classify_item(_raw("BBCA laba naik", hours_ago=2), "BBCA")

    assert unknown_date_item.published_at is None
    assert unknown_date_item.relevance_score < dated_item.relevance_score

    patcher, _ = _mock_ticker(
        [
            _raw("BBCA laba naik tanpa tanggal", include_date=False),
            _raw("BBCA profit growth stale", hours_ago=STALE_NEWS_HOURS + 2),
        ]
    )
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.staleness_warning is not None
    assert "unknown publish dates" in bundle.staleness_warning
    assert "Most recent news is" in bundle.staleness_warning


def test_duplicate_news_items_are_removed() -> None:
    news = [
        _raw("BBCA profit growth", url="https://example.test/news/duplicate"),
        _raw("BBCA profit growth", url="https://example.test/news/duplicate"),
    ]
    patcher, _ = _mock_ticker(news)
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.total_fetched == 2
    assert bundle.total_relevant == 1
    assert len(bundle.items) == 1


def test_dividend_or_buyback_upcoming_neutralizes_adjustment() -> None:
    patcher_ticker, _ = _mock_ticker(
        [_raw("BBCA umumkan dividen dan buyback setelah laba naik", hours_ago=4)]
    )

    async def mock_prefetch(ticker: str):
        return {
            "calendar": {
                "Ex-Dividend Date": datetime.now(timezone.utc) + timedelta(days=10)
            },
            "dividends": pd.Series([100.0]),
        }

    patcher_cache = patch(
        "utils.market_data_cache.DEFAULT_MARKET_DATA_CACHE.prefetch",
        mock_prefetch,
    )
    patcher_cache.start()

    try:
        bundle = NewsFetcher().build_bundle("BBCA")
        prompt = NewsFetcher().bundle_to_prompt_string(bundle)
    finally:
        patcher_cache.stop()
        patcher_ticker.stop()

    assert bundle.has_corporate_action is True
    assert bundle.overall_sentiment is NewsSentiment.POSITIVE
    assert bundle.confidence_adjustment == 0.0
    assert "Upcoming corporate action detected" in bundle.confidence_adjustment_reason
    assert "Reason: Upcoming corporate action detected" in prompt


def test_dividend_or_buyback_past_does_not_neutralize_adjustment() -> None:
    patcher_ticker, _ = _mock_ticker(
        [_raw("BBCA umumkan dividen dan buyback setelah laba naik", hours_ago=4)]
    )

    async def mock_prefetch(ticker: str):
        # Ex-date is in the past, so it should be CLEAR
        return {
            "calendar": {
                "Ex-Dividend Date": datetime.now(timezone.utc) - timedelta(days=10)
            },
            "dividends": pd.Series([100.0]),
        }

    patcher_cache = patch(
        "utils.market_data_cache.DEFAULT_MARKET_DATA_CACHE.prefetch",
        mock_prefetch,
    )
    patcher_cache.start()

    try:
        bundle = NewsFetcher().build_bundle("BBCA")
        prompt = NewsFetcher().bundle_to_prompt_string(bundle)
    finally:
        patcher_cache.stop()
        patcher_ticker.stop()

    assert bundle.has_corporate_action is True
    assert bundle.overall_sentiment is NewsSentiment.POSITIVE
    assert bundle.confidence_adjustment == 0.10
    assert "Breaking positive news" in bundle.confidence_adjustment_reason
    assert "Reason: Breaking positive news" in prompt


def test_confidence_reason_matches_configured_constants() -> None:
    patcher, _ = _mock_ticker(
        [_raw("BBCA rugi turun below", hours_ago=BREAKING_NEWS_HOURS + 3)]
    )
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert (
        bundle.confidence_adjustment_reason
        == f"Negative news sentiment in last {NEWS_LOOKBACK_DAYS} days"
    )


def test_bundle_to_prompt_string_includes_event_flags_without_mislabeling_sentiment() -> (
    None
):
    patcher, _ = _mock_ticker([_raw("BBCA rights issue rugi turun below", hours_ago=2)])
    try:
        fetcher = NewsFetcher()
        bundle = fetcher.build_bundle("BBCA")
        prompt = fetcher.bundle_to_prompt_string(bundle)
    finally:
        patcher.stop()

    assert "[NEGATIVE] BBCA rights issue rugi turun below" in prompt
    assert "Events: BREAKING | CORPORATE_ACTION" in prompt
    assert "[CORPORATE_ACTION]" not in prompt


def test_build_bundle_positive_news_has_non_negative_adjustment() -> None:
    patcher, _ = _mock_ticker([_raw("BBCA laba naik record strong", hours_ago=2)])
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.confidence_adjustment >= 0


def test_bundle_to_prompt_string_contains_header_and_ticker() -> None:
    patcher, _ = _mock_ticker([_raw("BBCA profit growth", hours_ago=2)])
    try:
        fetcher = NewsFetcher()
        bundle = fetcher.build_bundle("BBCA")
        prompt = fetcher.bundle_to_prompt_string(bundle)
    finally:
        patcher.stop()

    assert "NEWS BRIEF" in prompt
    assert "BBCA" in prompt


def test_bundle_to_prompt_string_shows_breaking() -> None:
    patcher, _ = _mock_ticker([_raw("BBCA profit growth", hours_ago=2)])
    try:
        fetcher = NewsFetcher()
        bundle = fetcher.build_bundle("BBCA")
        prompt = fetcher.bundle_to_prompt_string(bundle)
    finally:
        patcher.stop()

    assert "BREAKING" in prompt


def test_cache_hit_avoids_second_news_fetch() -> None:
    patcher, mocked_ticker = _mock_ticker([_raw("BBCA profit growth", hours_ago=2)])
    try:
        fetcher = NewsFetcher()
        fetcher.build_bundle("BBCA")
        fetcher.build_bundle("BBCA")
    finally:
        patcher.stop()

    assert mocked_ticker.call_count == 1


def test_as_evidence_chunk_returns_sentiment_chunk() -> None:
    patcher, _ = _mock_ticker([_raw("BBCA profit growth", hours_ago=2)])
    try:
        fetcher = NewsFetcher()
        bundle = fetcher.build_bundle("BBCA")
        chunk = fetcher.as_evidence_chunk(bundle)
    finally:
        patcher.stop()

    assert chunk["category"] == "sentiment"
    assert chunk["content"]


def test_fetch_news_failure_returns_empty_list() -> None:
    async def failing_fetch(*args, **kwargs):
        raise RuntimeError("network failed")

    patcher = patch("services.news_fetcher.fetch_news_rss", failing_fetch)
    patcher.start()
    try:
        news = NewsFetcher().fetch_news("BBCA")
    finally:
        patcher.stop()

    assert news == []


def test_build_bundle_fetch_failure_records_failure_metadata() -> None:
    async def failing_fetch(*args, **kwargs):
        raise RuntimeError("network failed")

    patcher = patch("services.news_fetcher.fetch_news_rss", failing_fetch)
    patcher.start()
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.data_available is False
    assert bundle.fetch_failure == {
        "stage": "rss_fetch",
        "type": "RuntimeError",
        "message": "network failed",
    }
    assert "News fetch failed" in bundle.confidence_adjustment_reason


def test_build_bundle_rss_source_failure_records_source_metadata() -> None:
    async def failing_text(*args, **kwargs):
        raise OSError("rss transport down")

    patcher = patch("services.news_fetcher._fetch_text", failing_text)
    patcher.start()
    try:
        bundle = NewsFetcher().build_bundle("BBCA")
    finally:
        patcher.stop()

    assert bundle.data_available is False
    assert bundle.fetch_failure is not None
    assert bundle.fetch_failure["stage"] == "rss_fetch"
    assert bundle.fetch_failure["source"] == "Google News RSS"
    assert bundle.fetch_failure["type"] == "OSError"
    assert "rss transport down" in bundle.fetch_failure["message"]
    assert "Google News RSS" in bundle.confidence_adjustment_reason


def test_fetch_news_rss_uses_kontan_fallback_when_google_empty() -> None:
    calls: list[str] = []
    now = datetime.now(timezone.utc)
    kontan_item = {
        "title": "BBCA laba naik",
        "link": "https://www.kontan.co.id/news/bbca-laba-naik",
        "published": now.isoformat(),
        "summary": "Kinerja BBCA positif.",
    }

    async def fake_fetch_rss_items(url: str, **kwargs) -> list[dict]:
        calls.append(kwargs["source"])
        if kwargs["source"] == "Google News RSS":
            return []
        return [kontan_item]

    patcher = patch("services.news_fetcher._fetch_rss_items", fake_fetch_rss_items)
    patcher.start()
    try:
        news = asyncio.run(fetch_news_rss("BBCA"))
    finally:
        patcher.stop()

    assert calls == ["Google News RSS", "Kontan RSS"]
    assert news == [kontan_item]


def test_parse_rss_items_uses_google_source_tag() -> None:
    xml_text = """
    <rss>
      <channel>
        <item>
          <title>Wismilak (WIIM) Bagikan Dividen - IDX Channel</title>
          <source url="https://www.idxchannel.com">IDX Channel</source>
          <link>https://example.test/wiim-dividen</link>
          <pubDate>Mon, 25 May 2026 10:00:00 GMT</pubDate>
          <description><![CDATA[<p>Dividen cair Juni.</p>]]></description>
        </item>
      </channel>
    </rss>
    """

    parsed = _parse_rss_items(xml_text)

    assert parsed[0]["source"] == "IDX Channel"
    assert parsed[0]["summary"] == "Dividen cair Juni."


def test_parse_rss_items_falls_back_to_title_suffix_source() -> None:
    xml_text = """
    <rss>
      <channel>
        <item>
          <title>Target Volume SKT Dua Miliar Batang - KabarBursa.com</title>
          <link>https://example.test/wiim-target-volume</link>
          <pubDate>Fri, 22 May 2026 08:00:00 GMT</pubDate>
          <description>WIIM masuk radar beli investor.</description>
        </item>
      </channel>
    </rss>
    """

    parsed = _parse_rss_items(xml_text)

    assert parsed[0]["source"] == "KabarBursa.com"


def test_parse_rss_items_uses_kontan_default_source() -> None:
    xml_text = """
    <rss>
      <channel>
        <item>
          <title>WIIM Update</title>
          <link>https://www.kontan.co.id/news/wiim-update</link>
          <pubDate>Fri, 22 May 2026 08:00:00 GMT</pubDate>
          <description>Berita emiten.</description>
        </item>
      </channel>
    </rss>
    """

    parsed = _parse_rss_items(xml_text, default_source="Kontan")

    assert parsed[0]["source"] == "Kontan"
