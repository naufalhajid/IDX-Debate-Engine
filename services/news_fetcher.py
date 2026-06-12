"""Free-source news and sentiment integration for IDX ticker debates."""

from __future__ import annotations

import argparse
import asyncio
from email.utils import parsedate_to_datetime
import html
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

NEWS_LOOKBACK_DAYS = 60
MAX_NEWS_ITEMS = 10
BREAKING_NEWS_HOURS = 48
STALE_NEWS_HOURS = 144
CACHE_TTL_MINUTES = 30
UNKNOWN_DATE_RECENCY_SCORE = 0.15

IDX_TICKER_RE = re.compile(r"^[A-Z]{4}$")


NEGATIVE_KEYWORDS = [
    "rugi",
    "turun",
    "penurunan",
    "melemah",
    "pelemahan",
    "jual",
    "penjualan",
    "cabut",
    "gagal",
    "default",
    "delisting",
    "suspensi",
    "penalti",
    "denda",
    "korupsi",
    "fraud",
    "loss",
    "decline",
    "suspend",
    "penalty",
    "downgrade",
    "cut",
    "miss",
    "below",
]

POSITIVE_KEYWORDS = [
    "laba",
    "naik",
    "menguat",
    "beli",
    "ekspansi",
    "kontrak",
    "dividen",
    "buyback",
    "upgrade",
    "profit",
    "growth",
    "acquire",
    "partnership",
    "beat",
    "above",
    "record",
    "strong",
]

CORPORATE_ACTION_KEYWORDS = [
    "rights issue",
    "stock split",
    "reverse split",
    "RUPS",
    "merger",
    "akuisisi",
    "divestasi",
    "rights",
    "waran",
    "obligasi",
    "IPO anak",
    "ex-date",
    "cum-date",
    "dividen",
]

MACRO_KEYWORDS = [
    "suku bunga",
    "BI rate",
    "inflasi",
    "rupiah",
    "IHSG",
    "bursa",
    "OJK",
    "pajak",
    "tarif",
    "interest rate",
    "inflation",
    "currency",
    "trade war",
    "sanctions",
    "export ban",
]

logger = logging.getLogger(__name__)


def _exception_message(exc: BaseException) -> str:
    text = str(exc).strip()
    if text:
        return text
    if getattr(exc, "args", None):
        return repr(exc.args)
    return type(exc).__name__


async def fetch_news_rss(
    ticker: str,
    company_name: str = "",
    lookback_days: int = 60,
    limit: int = 10,
    diagnostics: list[dict[str, str]] | None = None,
) -> list[dict]:
    """Fetch recent IDX news through Google News RSS with a best-effort Kontan fallback."""
    normalized_ticker = _normalize_ticker(ticker)
    query_name = company_name.strip() or normalized_ticker
    query = quote(f"{query_name} saham")
    google_url = f"https://news.google.com/rss/search?q={query}&hl=id&gl=ID&ceid=ID:id"

    google_items = await _fetch_rss_items(
        google_url,
        ticker=normalized_ticker,
        source="Google News RSS",
        diagnostics=diagnostics,
    )
    selected = _recent_rss_items(
        google_items,
        lookback_days=lookback_days,
        limit=limit,
    )

    if not selected:
        kontan_url = f"https://www.kontan.co.id/search/?q={quote(normalized_ticker)}"
        kontan_items = await _fetch_rss_items(
            kontan_url,
            ticker=normalized_ticker,
            source="Kontan RSS",
            default_item_source="Kontan",
            silent_http_statuses={403},
            silent_timeout=True,
            diagnostics=diagnostics,
        )
        selected = _recent_rss_items(
            [*google_items, *kontan_items],
            lookback_days=lookback_days,
            limit=limit,
        )

    return selected


async def _fetch_rss_items(
    url: str,
    *,
    ticker: str,
    source: str,
    default_item_source: str | None = None,
    silent_http_statuses: set[int] | None = None,
    silent_timeout: bool = False,
    diagnostics: list[dict[str, str]] | None = None,
) -> list[dict]:
    try:
        xml_text = await _fetch_text(
            url,
            silent_http_statuses=silent_http_statuses or set(),
            silent_timeout=silent_timeout,
        )
    except Exception as exc:
        silenced = _should_silence_rss_error(exc, silent_http_statuses, silent_timeout)
        if diagnostics is not None:
            diagnostics.append(
                {
                    "source": source,
                    "stage": "fetch",
                    "type": type(exc).__name__,
                    "message": _exception_message(exc),
                    "silenced": str(silenced).lower(),
                }
            )
        if silenced:
            logger.debug("[News] %s %s skipped: %s", ticker, source, exc)
        else:
            logger.warning("[News] %s %s fetch failed: %s", ticker, source, exc)
        return []

    if not xml_text:
        return []

    try:
        return _parse_rss_items(
            xml_text, default_source=default_item_source, ticker=ticker
        )
    except Exception as exc:
        if diagnostics is not None:
            diagnostics.append(
                {
                    "source": source,
                    "stage": "parse",
                    "type": type(exc).__name__,
                    "message": _exception_message(exc),
                    "silenced": "false",
                }
            )
        logger.warning("[News] %s %s parse failed: %s", ticker, source, exc)
        return []


async def _fetch_text(
    url: str,
    *,
    silent_http_statuses: set[int],
    silent_timeout: bool,
) -> str:
    try:
        import aiohttp
    except ImportError:
        aiohttp = None

    if aiohttp is not None:
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status in silent_http_statuses:
                        raise _SilentHttpStatus(response.status)
                    if response.status >= 400:
                        raise RuntimeError(f"HTTP {response.status}")
                    return await response.text()
        except TimeoutError as exc:
            if silent_timeout:
                raise _SilentTimeout(str(exc)) from exc
            raise

    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "Neither aiohttp nor httpx is available for RSS fetch"
        ) from exc

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.TimeoutException as exc:
        if silent_timeout:
            raise _SilentTimeout(str(exc)) from exc
        raise

    if response.status_code in silent_http_statuses:
        raise _SilentHttpStatus(response.status_code)
    response.raise_for_status()
    return response.text


def _parse_rss_items(
    xml_text: str, default_source: str | None = None, ticker: str | None = None
) -> list[dict]:
    root = ET.fromstring(xml_text)
    parsed_items: list[dict] = []

    ticker_pattern = None
    if ticker:
        ticker_pattern = re.compile(rf"\b{re.escape(ticker.upper())}\b")

    for item in root.findall(".//item"):
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        published_dt = _parse_rss_pub_date(_child_text(item, "pubDate"))
        if not title or not link or published_dt is None:
            continue

        summary = _strip_html(_child_text(item, "description"))

        if ticker_pattern:
            title_upper = title.upper()
            summary_upper = summary.upper()
            if not (
                ticker_pattern.search(title_upper)
                or ticker_pattern.search(summary_upper)
            ):
                continue

        source = _rss_item_source(item, title, default_source=default_source)
        parsed_items.append(
            {
                "title": title,
                "link": link,
                "published": published_dt.isoformat(),
                "source": source,
                "summary": summary,
            }
        )
    return parsed_items


def _child_text(item: ET.Element, tag: str) -> str:
    child = item.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _rss_item_source(
    item: ET.Element,
    title: str,
    *,
    default_source: str | None = None,
) -> str:
    if default_source:
        return default_source
    source = _child_text(item, "source")
    if source:
        return source
    parts = title.rsplit(" - ", 1)
    return parts[-1].strip() if len(parts) == 2 else "unknown"


def _parse_rss_pub_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _strip_html(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _recent_rss_items(
    items: list[dict],
    *,
    lookback_days: int,
    limit: int,
) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    deduped: dict[str, dict] = {}
    for item in items:
        published = _parse_publish_time(item.get("published"))
        link = str(item.get("link") or "").strip()
        if not published or published < cutoff or published > now + timedelta(days=1):
            continue
        if not link:
            continue
        key = link.lower().rstrip("/")
        existing_published = (
            _parse_publish_time(deduped[key].get("published"))
            if key in deduped
            else None
        )
        if (
            key not in deduped
            or existing_published is None
            or published > existing_published
        ):
            deduped[key] = item

    return sorted(
        deduped.values(),
        key=lambda raw: (
            _parse_publish_time(raw.get("published"))
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )[:limit]


class _SilentHttpStatus(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _SilentTimeout(TimeoutError):
    pass


def _should_silence_rss_error(
    exc: Exception,
    silent_http_statuses: set[int] | None,
    silent_timeout: bool,
) -> bool:
    if isinstance(exc, _SilentHttpStatus):
        return exc.status_code in (silent_http_statuses or set())
    return silent_timeout and isinstance(exc, _SilentTimeout)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[Any] = []
    errors: list[BaseException] = []

    def runner() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]


class NewsSentiment(str, Enum):
    """Ticker news sentiment classes used by the debate prompt."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class NewsEventTag(str, Enum):
    """Non-sentiment news event flags used for trading-risk validation."""

    CORPORATE_ACTION = "CORPORATE_ACTION"
    MACRO_EVENT = "MACRO_EVENT"
    BREAKING = "BREAKING"


class NewsItem(BaseModel):
    """One normalized news item from a free source."""

    model_config = ConfigDict(extra="forbid")

    title: str
    source: str
    published_at: str | None
    url: str | None
    sentiment: NewsSentiment
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    is_breaking: bool
    is_corporate_action: bool
    is_macro: bool
    event_tags: list[NewsEventTag] = Field(default_factory=list)
    relevance_score: float = Field(ge=0.0, le=1.0)
    summary: str


class NewsBundle(BaseModel):
    """Aggregated recent-news signal for one ticker."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    fetched_at: str
    lookback_days: int
    items: list[NewsItem]
    total_fetched: int
    total_relevant: int
    overall_sentiment: NewsSentiment
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    has_breaking_news: bool
    has_corporate_action: bool
    has_macro_event: bool
    confidence_adjustment: float
    confidence_adjustment_reason: str
    staleness_warning: str | None
    data_available: bool
    fetch_failure: dict[str, str] | None = None


class NewsFetcher:
    """Fetch, classify, cache, and format recent ticker news."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[NewsBundle, datetime]] = {}
        self._last_fetch_failure: dict[str, str] | None = None

    async def fetch_news_async(self, ticker: str, company_name: str = "") -> list[dict]:
        """Fetch raw RSS news for an IDX ticker without raising."""
        normalized = _normalize_ticker(ticker)
        self._last_fetch_failure = None
        if not _is_valid_idx_ticker(normalized):
            return []
        diagnostics: list[dict[str, str]] = []
        try:
            items = await fetch_news_rss(
                normalized,
                company_name=company_name,
                lookback_days=NEWS_LOOKBACK_DAYS,
                limit=MAX_NEWS_ITEMS,
                diagnostics=diagnostics,
            )
            if not items:
                failure = next(
                    (
                        item
                        for item in diagnostics
                        if item.get("silenced") != "true"
                    ),
                    None,
                )
                if failure:
                    self._last_fetch_failure = {
                        "stage": f"rss_{failure.get('stage') or 'fetch'}",
                        "source": failure.get("source") or "unknown",
                        "type": failure.get("type") or "unknown",
                        "message": (
                            f"{failure.get('source') or 'unknown'}: "
                            f"{failure.get('message') or 'unknown'}"
                        ),
                    }
            return items
        except Exception as exc:
            self._last_fetch_failure = {
                "stage": "rss_fetch",
                "type": type(exc).__name__,
                "message": _exception_message(exc),
            }
            logger.warning(
                "[News] RSS fetch failed for %s: %s",
                normalized,
                exc,
            )
            return []

    def fetch_news(self, ticker: str, company_name: str = "") -> list[dict]:
        """Synchronous compatibility wrapper around the async RSS fetcher."""
        return _run_async(self.fetch_news_async(ticker, company_name=company_name))

    def fetch_yfinance_news(self, ticker: str) -> list[dict]:
        """Compatibility wrapper; news now comes from RSS, not yfinance."""
        return self.fetch_news(ticker)

    def classify_item(self, raw: dict, ticker: str) -> NewsItem:
        """Normalize and score one raw news dictionary."""
        title = _extract_title(raw)
        source = str(raw.get("publisher") or raw.get("source") or "unknown")
        published_at_dt = _extract_publish_time(raw)
        published_at = published_at_dt.isoformat() if published_at_dt else None
        url = _extract_url(raw)
        text = _extract_text(raw)

        negative_hits = _keyword_hits(text, NEGATIVE_KEYWORDS)
        positive_hits = _keyword_hits(text, POSITIVE_KEYWORDS)
        hit_count = positive_hits + negative_hits
        net_score = (
            0.0 if hit_count == 0 else (positive_hits - negative_hits) / hit_count
        )
        net_score = _clamp(net_score, -1.0, 1.0)

        if net_score > 0.2:
            sentiment = NewsSentiment.POSITIVE
        elif net_score < -0.2:
            sentiment = NewsSentiment.NEGATIVE
        else:
            sentiment = NewsSentiment.NEUTRAL

        is_corporate_action = _contains_keyword(text, CORPORATE_ACTION_KEYWORDS)
        is_macro = _contains_keyword(text, MACRO_KEYWORDS)
        is_breaking = _is_breaking(published_at_dt)
        event_tags = _event_tags(
            is_breaking=is_breaking,
            is_corporate_action=is_corporate_action,
            is_macro=is_macro,
        )
        relevance_score = _relevance_score(
            text=text,
            ticker=ticker,
            published_at=published_at_dt,
            sentiment_score=net_score,
            event_tags=event_tags,
        )

        return NewsItem(
            title=title,
            source=source or "unknown",
            published_at=published_at,
            url=url,
            sentiment=sentiment,
            sentiment_score=net_score,
            is_breaking=is_breaking,
            is_corporate_action=is_corporate_action,
            is_macro=is_macro,
            event_tags=event_tags,
            relevance_score=relevance_score,
            summary=text[:300],
        )

    def build_bundle(self, ticker: str, use_cache: bool = True) -> NewsBundle:
        """Fetch, classify, rank, and aggregate recent news."""
        return _run_async(self.build_bundle_async(ticker, use_cache=use_cache))

    async def build_bundle_async(
        self,
        ticker: str,
        use_cache: bool = True,
    ) -> NewsBundle:
        """Async RSS-backed implementation for debate-time news fetching."""
        normalized_ticker = _normalize_ticker(ticker)
        now = datetime.now(timezone.utc)
        if not _is_valid_idx_ticker(normalized_ticker):
            return _empty_bundle(
                ticker=normalized_ticker,
                fetched_at=now,
                reason="Invalid IDX ticker - news sentiment unavailable",
            )

        if use_cache and normalized_ticker in self._cache:
            cached_bundle, cached_at = self._cache[normalized_ticker]
            if now - cached_at <= timedelta(minutes=CACHE_TTL_MINUTES):
                return cached_bundle

        raw_items = await self.fetch_news_async(normalized_ticker)
        if not raw_items and self._last_fetch_failure:
            bundle = _empty_bundle(
                ticker=normalized_ticker,
                fetched_at=now,
                reason=(
                    "News fetch failed - "
                    f"{self._last_fetch_failure['message']}"
                ),
                fetch_failure=self._last_fetch_failure,
            )
            self._cache[normalized_ticker] = (bundle, now)
            return bundle
        cutoff = now - timedelta(days=NEWS_LOOKBACK_DAYS)
        classified = [self.classify_item(raw, normalized_ticker) for raw in raw_items]
        recent_items = [
            item
            for item in classified
            if _published_datetime(item) is None or _published_datetime(item) >= cutoff
        ]
        deduped_items = _dedupe_items(recent_items)
        selected = sorted(
            deduped_items,
            key=lambda item: item.relevance_score,
            reverse=True,
        )[:MAX_NEWS_ITEMS]

        # Check if there is an upcoming corporate action risk using scan_exdate
        has_upcoming_exdate = False
        if any(item.is_corporate_action for item in selected):
            try:
                from utils.market_data_cache import (
                    DEFAULT_MARKET_DATA_CACHE,
                    scan_exdate_from_market_data,
                )

                market_data = await DEFAULT_MARKET_DATA_CACHE.prefetch(
                    normalized_ticker
                )
                exdate_info = scan_exdate_from_market_data(
                    normalized_ticker, market_data
                )
                has_upcoming_exdate = exdate_info.get("has_upcoming_exdate", False)
            except Exception as exc:
                logger.warning(
                    f"[News] Failed to check upcoming ex-date for {normalized_ticker}: {exc}"
                )
                # Fallback: assume it is upcoming for safety if there are corporate action news items
                has_upcoming_exdate = True

        bundle = self._aggregate_bundle(
            ticker=normalized_ticker,
            fetched_at=now,
            raw_count=len(raw_items),
            items=selected,
            has_upcoming_exdate=has_upcoming_exdate,
        )
        self._cache[normalized_ticker] = (bundle, now)
        return bundle

    def bundle_to_prompt_string(self, bundle: NewsBundle) -> str:
        """Format a bundle for prompt injection."""
        lines = [
            f"=== NEWS BRIEF: {bundle.ticker} ===",
            f"Period: last {bundle.lookback_days} days",
            (
                "Sentiment: "
                f"{bundle.overall_sentiment.value} "
                f"(score: {bundle.sentiment_score:+.2f})"
            ),
        ]
        if bundle.has_breaking_news:
            lines.append("BREAKING NEWS DETECTED")
        if bundle.has_corporate_action:
            lines.append("CORPORATE ACTION DETECTED")
        if bundle.has_macro_event:
            lines.append("MACRO EVENT DETECTED")
        if not bundle.data_available:
            lines.append("No news data available")

        if bundle.confidence_adjustment != 0 or bundle.confidence_adjustment_reason:
            lines.extend(
                [
                    (f"Confidence adjustment: {bundle.confidence_adjustment:+.2f}"),
                    f"Reason: {bundle.confidence_adjustment_reason}",
                ]
            )

        lines.extend(["", "TOP NEWS:"])
        for index, item in enumerate(bundle.items[:5], 1):
            published = item.published_at[:10] if item.published_at else "unknown"
            lines.append(f"{index}. [{item.sentiment.value}] {item.title}")
            lines.append(f"   Source: {item.source} | {published}")
            if item.event_tags:
                events = " | ".join(tag.value for tag in item.event_tags)
                lines.append(f"   Events: {events}")
        if not bundle.items:
            lines.append("none")
        if bundle.staleness_warning:
            lines.append(bundle.staleness_warning)
        return "\n".join(lines)

    def as_evidence_chunk(self, bundle: NewsBundle) -> dict:
        """Return a RAG evidence-compatible sentiment chunk dictionary."""
        return {
            "category": "sentiment",
            "content": self.bundle_to_prompt_string(bundle),
            "source": "google_news_rss",
            "fetched_at": bundle.fetched_at,
            "relevance_score": min(0.6 + abs(bundle.sentiment_score) * 0.4, 1.0),
            "is_stale": bundle.staleness_warning is not None,
        }

    def _aggregate_bundle(
        self,
        *,
        ticker: str,
        fetched_at: datetime,
        raw_count: int,
        items: list[NewsItem],
        has_upcoming_exdate: bool = False,
    ) -> NewsBundle:
        data_available = bool(items)
        has_breaking_news = any(item.is_breaking for item in items)
        has_corporate_action = any(item.is_corporate_action for item in items)
        has_macro_event = any(item.is_macro for item in items)
        sentiment_score = _weighted_sentiment(items)
        overall_sentiment = _overall_sentiment(
            sentiment_score,
            data_available=data_available,
        )
        adjustment, reason = _confidence_adjustment(
            overall_sentiment,
            data_available=data_available,
            items=items,
            has_upcoming_exdate=has_upcoming_exdate,
        )
        staleness_warning = _staleness_warning(items, fetched_at)
        return NewsBundle(
            ticker=ticker,
            fetched_at=fetched_at.isoformat(),
            lookback_days=NEWS_LOOKBACK_DAYS,
            items=items,
            total_fetched=raw_count,
            total_relevant=len(items),
            overall_sentiment=overall_sentiment,
            sentiment_score=sentiment_score,
            has_breaking_news=has_breaking_news,
            has_corporate_action=has_corporate_action,
            has_macro_event=has_macro_event,
            confidence_adjustment=adjustment,
            confidence_adjustment_reason=reason,
            staleness_warning=staleness_warning,
            data_available=data_available,
        )


def _normalize_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper().removesuffix(".JK")


def _is_valid_idx_ticker(ticker: str) -> bool:
    return bool(IDX_TICKER_RE.fullmatch(ticker))


def _normalize_symbol(ticker: str) -> str | None:
    normalized = _normalize_ticker(ticker)
    if not _is_valid_idx_ticker(normalized):
        return None
    return f"{normalized}.JK"


def _empty_bundle(
    *,
    ticker: str,
    fetched_at: datetime,
    reason: str,
    fetch_failure: dict[str, str] | None = None,
) -> NewsBundle:
    return NewsBundle(
        ticker=ticker,
        fetched_at=fetched_at.isoformat(),
        lookback_days=NEWS_LOOKBACK_DAYS,
        items=[],
        total_fetched=0,
        total_relevant=0,
        overall_sentiment=NewsSentiment.UNKNOWN,
        sentiment_score=0.0,
        has_breaking_news=False,
        has_corporate_action=False,
        has_macro_event=False,
        confidence_adjustment=-0.05,
        confidence_adjustment_reason=reason,
        staleness_warning=None,
        data_available=False,
        fetch_failure=fetch_failure,
    )


def _extract_title(raw: dict) -> str:
    title = raw.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    content = raw.get("content")
    if isinstance(content, dict) and isinstance(content.get("title"), str):
        return content["title"].strip()
    return ""


def _extract_text(raw: dict) -> str:
    parts: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped not in parts:
                parts.append(stripped)

    add(raw.get("title"))
    content = raw.get("content")
    if isinstance(content, dict):
        add(content.get("title"))
        add(content.get("summary"))
        add(content.get("description"))
    add(raw.get("summary"))
    add(raw.get("description"))
    return " ".join(parts)


def _extract_url(raw: dict) -> str | None:
    for key in ("link", "url"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = raw.get("content")
    if isinstance(content, dict):
        canonical_url = content.get("canonicalUrl")
        if isinstance(canonical_url, dict):
            url = canonical_url.get("url")
            if isinstance(url, str) and url.strip():
                return url.strip()
    return None


def _extract_publish_time(raw: dict) -> datetime | None:
    for key in (
        "providerPublishTime",
        "publishTime",
        "pubDate",
        "published",
        "displayTime",
    ):
        if parsed := _parse_publish_time(raw.get(key)):
            return parsed

    content = raw.get("content")
    if isinstance(content, dict):
        for key in (
            "providerPublishTime",
            "publishTime",
            "pubDate",
            "published",
            "displayTime",
        ):
            if parsed := _parse_publish_time(content.get(key)):
                return parsed
    return None


def _parse_publish_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip().isdigit():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _published_datetime(item: NewsItem) -> datetime | None:
    if not item.published_at:
        return None
    try:
        return datetime.fromisoformat(item.published_at)
    except ValueError:
        return None


def _is_breaking(published_at: datetime | None) -> bool:
    if published_at is None:
        return False
    age = datetime.now(timezone.utc) - published_at
    return timedelta(0) <= age < timedelta(hours=BREAKING_NEWS_HOURS)


def _keyword_hits(text: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if _contains_keyword(text, [keyword]))


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    return any(_keyword_matches(text, keyword) for keyword in keywords)


def _keyword_matches(text: str, keyword: str) -> bool:
    normalized_keyword = keyword.strip()
    if not normalized_keyword:
        return False
    escaped = re.escape(normalized_keyword).replace(r"\ ", r"\s+")
    pattern = re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
    return bool(pattern.search(text))


def _ticker_in_text(text: str, ticker: str) -> bool:
    normalized = _normalize_ticker(ticker)
    if not _is_valid_idx_ticker(normalized):
        return False
    pattern = re.compile(
        rf"(?<![A-Z0-9]){re.escape(normalized)}(?:\.JK)?(?![A-Z0-9])", re.IGNORECASE
    )
    return bool(pattern.search(text))


def _event_tags(
    *,
    is_breaking: bool,
    is_corporate_action: bool,
    is_macro: bool,
) -> list[NewsEventTag]:
    tags: list[NewsEventTag] = []
    if is_breaking:
        tags.append(NewsEventTag.BREAKING)
    if is_corporate_action:
        tags.append(NewsEventTag.CORPORATE_ACTION)
    if is_macro:
        tags.append(NewsEventTag.MACRO_EVENT)
    return tags


def _relevance_score(
    *,
    text: str,
    ticker: str,
    published_at: datetime | None,
    sentiment_score: float,
    event_tags: list[NewsEventTag],
) -> float:
    ticker_match_score = 1.0 if _ticker_in_text(text, ticker) else 0.0
    recency_score = _recency_score(published_at, datetime.now(timezone.utc))
    event_score = _event_score(event_tags)
    score = (
        ticker_match_score * 0.35
        + recency_score * 0.30
        + abs(sentiment_score) * 0.20
        + event_score * 0.15
    )
    return _clamp(score, 0.0, 1.0)


def _recency_score(published_at: datetime | None, now: datetime) -> float:
    if published_at is None:
        return UNKNOWN_DATE_RECENCY_SCORE
    age = now - published_at
    if age <= timedelta(0):
        return 1.0
    age_days = age.total_seconds() / 86400
    return _clamp(1.0 - (age_days / NEWS_LOOKBACK_DAYS), 0.0, 1.0)


def _event_score(event_tags: list[NewsEventTag]) -> float:
    if not event_tags:
        return 0.0
    score = 0.0
    if NewsEventTag.CORPORATE_ACTION in event_tags:
        score += 1.0
    if NewsEventTag.MACRO_EVENT in event_tags:
        score += 0.6
    if NewsEventTag.BREAKING in event_tags:
        score += 0.6
    return _clamp(score, 0.0, 1.0)


def _dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    deduped: dict[str, NewsItem] = {}
    order: list[str] = []
    for item in items:
        key = _dedupe_key(item)
        if key not in deduped:
            deduped[key] = item
            order.append(key)
            continue
        if item.relevance_score > deduped[key].relevance_score:
            deduped[key] = item
    return [deduped[key] for key in order]


def _dedupe_key(item: NewsItem) -> str:
    if item.url:
        return "url:" + item.url.strip().lower().rstrip("/")
    normalized_title = re.sub(r"\s+", " ", item.title.strip().lower())
    return f"title:{normalized_title}"


def _weighted_sentiment(items: list[NewsItem]) -> float:
    if not items:
        return 0.0
    total_weight = sum(item.relevance_score for item in items)
    if total_weight <= 0:
        return 0.0
    score = sum(item.sentiment_score * item.relevance_score for item in items)
    return _clamp(score / total_weight, -1.0, 1.0)


def _overall_sentiment(
    score: float,
    *,
    data_available: bool,
) -> NewsSentiment:
    if not data_available:
        return NewsSentiment.UNKNOWN
    if score > 0.2:
        return NewsSentiment.POSITIVE
    if score < -0.2:
        return NewsSentiment.NEGATIVE
    return NewsSentiment.NEUTRAL


def _confidence_adjustment(
    sentiment: NewsSentiment,
    *,
    data_available: bool,
    items: list[NewsItem],
    has_upcoming_exdate: bool = False,
) -> tuple[float, str]:
    if not data_available:
        return 0.0, "No news data available - sentiment unverified"

    has_breaking_negative_news = any(
        item.is_breaking and item.sentiment_score < -0.2 for item in items
    )
    has_breaking_positive_news = any(
        item.is_breaking and item.sentiment_score > 0.2 for item in items
    )
    has_negative_corporate_action = any(
        item.is_corporate_action and item.sentiment_score < -0.2 for item in items
    )
    if has_breaking_negative_news:
        return (
            -0.20,
            f"Breaking negative news in last {BREAKING_NEWS_HOURS}h - significant risk detected",
        )
    if has_negative_corporate_action:
        return (
            -0.15,
            f"Negative corporate action news in last {NEWS_LOOKBACK_DAYS} days - validate trade plan",
        )
    if sentiment is NewsSentiment.NEGATIVE:
        return -0.10, f"Negative news sentiment in last {NEWS_LOOKBACK_DAYS} days"
    if has_upcoming_exdate:
        return (
            0.0,
            "Upcoming corporate action detected - validate trade plan against corporate event",
        )
    if has_breaking_positive_news:
        return (
            0.10,
            f"Breaking positive news in last {BREAKING_NEWS_HOURS}h - significant opportunity detected",
        )
    if sentiment is NewsSentiment.POSITIVE:
        return 0.05, "Positive news sentiment supports trade"
    return 0.0, ""


def _staleness_warning(items: list[NewsItem], fetched_at: datetime) -> str | None:
    warnings: list[str] = []
    if any(item.published_at is None for item in items):
        warnings.append("Some selected news items have unknown publish dates")

    published_times = [
        published
        for item in items
        if (published := _published_datetime(item)) is not None
    ]
    if published_times:
        latest = max(published_times)
        hours_old = int((fetched_at - latest).total_seconds() // 3600)
        if hours_old > STALE_NEWS_HOURS:
            warnings.append(f"Most recent news is {hours_old}h old")
    return "; ".join(warnings) if warnings else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


DEFAULT_FETCHER = NewsFetcher()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a recent-news brief for a ticker."
    )
    parser.add_argument("--ticker", required=True, help="IDX ticker, e.g. BBCA")
    args = parser.parse_args()

    bundle = DEFAULT_FETCHER.build_bundle(args.ticker)
    print(DEFAULT_FETCHER.bundle_to_prompt_string(bundle))


if __name__ == "__main__":
    main()
