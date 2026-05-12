"""Build compact prompt context packs from raw provider outputs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict


MAX_PROMPT_CHARS = 3_200
_FUNDAMENTALS_PLACEHOLDER = "__FUNDAMENTALS__"
logger = logging.getLogger(__name__)


class ContextPack(BaseModel):
    """Normalized, provenance-aware context for debate prompts."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    as_of: datetime
    price: float
    fair_value: float | None
    fundamentals: dict
    technicals: dict
    sentiment_summary: str | None
    data_sources: list[str]
    missing_fields: list[str]
    token_estimate: int


def build_context_pack(ticker: str, raw_data: dict) -> ContextPack:
    """Map raw provider output into a normalized, compact context pack."""
    normalized_ticker = str(ticker or raw_data.get("ticker") or "").strip().upper()
    price = _first_number(
        raw_data,
        "price",
        "current_price",
        "last_price",
        "close",
        nested_paths=(
            ("market_data", "price"),
            ("market_data", "current_price"),
            ("market_data", "last_price"),
            ("technical_indicators", "current_price"),
            ("technicals", "current_price"),
            ("verdict", "current_price"),
        ),
    )
    fair_value = _first_number(
        raw_data,
        "fair_value",
        "fair_value_estimate",
        nested_paths=(
            ("fundamentals", "fair_value"),
            ("fundamental_data", "fair_value"),
            ("verdict", "fair_value"),
        ),
    )
    fundamentals = _first_dict(raw_data, "fundamentals", "fundamental_data", "fundamental")
    technicals = _first_dict(raw_data, "technicals", "technical_indicators", "technical_data")
    sentiment_summary = _first_text(
        raw_data,
        "sentiment_summary",
        "sentiment_data",
        "sentiment",
        nested_paths=(("sentiment", "summary"),),
    )
    data_sources = _collect_data_sources(raw_data)

    missing_fields = []
    if price is None:
        missing_fields.append("price")
    if fair_value is None:
        missing_fields.append("fair_value")
    if not fundamentals:
        missing_fields.append("fundamentals")
    if not technicals:
        missing_fields.append("technicals")
    if sentiment_summary is None:
        missing_fields.append("sentiment_summary")
    if not data_sources:
        missing_fields.append("data_sources")

    pack = ContextPack(
        ticker=normalized_ticker,
        as_of=_resolve_as_of(raw_data),
        price=price or 0.0,
        fair_value=fair_value,
        fundamentals=fundamentals,
        technicals=technicals,
        sentiment_summary=sentiment_summary,
        data_sources=data_sources,
        missing_fields=missing_fields,
        token_estimate=0,
    )
    pack.token_estimate = len(pack_to_prompt_string(pack)) // 4
    return pack


def pack_to_prompt_string(pack: ContextPack) -> str:
    """Render a context pack as compact text suitable for prompt injection."""
    fundamentals_json = _compact_json(pack.fundamentals)
    technicals_json = _compact_json(pack.technicals)
    sources = ", ".join(pack.data_sources) if pack.data_sources else "unknown"
    missing = ", ".join(pack.missing_fields) if pack.missing_fields else "none"
    fair_value = (
        f"Rp {pack.fair_value:,.0f}" if pack.fair_value is not None else "INSUFFICIENT_DATA"
    )
    sentiment = pack.sentiment_summary or "INSUFFICIENT_DATA"

    def render(fundamentals: str) -> str:
        return (
            f"Ticker: {pack.ticker}\n"
            f"As Of: {pack.as_of.isoformat()}\n"
            f"Current Price: Rp {pack.price:,.0f}\n"
            f"Fair Value Estimate: {fair_value}\n"
            f"Data Sources: {sources}\n"
            f"Missing Fields: {missing}\n"
            f"Technical Indicators: {technicals_json}\n\n"
            f"Fundamental Brief: {fundamentals}\n\n"
            f"Sentiment Brief: {_compact_text(sentiment, 600)}"
        )

    prompt = render(fundamentals_json)
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt

    overhead = len(render(_FUNDAMENTALS_PLACEHOLDER)) - len(_FUNDAMENTALS_PLACEHOLDER)
    budget = max(80, MAX_PROMPT_CHARS - overhead - 20)
    truncated_fundamentals = _compact_text(fundamentals_json, budget)
    logger.warning(
        "Context pack fundamentals truncated for %s to keep prompt under %s chars.",
        pack.ticker,
        MAX_PROMPT_CHARS,
    )

    prompt = render(truncated_fundamentals)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = _compact_text(prompt, MAX_PROMPT_CHARS)
    return prompt


def _resolve_as_of(raw_data: dict) -> datetime:
    value = raw_data.get("as_of") or raw_data.get("timestamp") or raw_data.get("generated_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _first_dict(raw_data: dict, *keys: str) -> dict:
    for key in keys:
        value = raw_data.get(key)
        if isinstance(value, dict) and value:
            return value
        if isinstance(value, str) and value.strip():
            return {"brief": value.strip()}
    return {}


def _first_text(
    raw_data: dict,
    *keys: str,
    nested_paths: tuple[tuple[str, ...], ...] = (),
) -> str | None:
    for key in keys:
        text = _optional_text(raw_data.get(key))
        if text is not None:
            return text
    for path in nested_paths:
        text = _optional_text(_nested_get(raw_data, path))
        if text is not None:
            return text
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return _compact_json(value)
    text = str(value).strip()
    return text or None


def _first_number(
    raw_data: dict,
    *keys: str,
    nested_paths: tuple[tuple[str, ...], ...] = (),
) -> float | None:
    for key in keys:
        number = _optional_number(raw_data.get(key))
        if number is not None:
            return number
    for path in nested_paths:
        number = _optional_number(_nested_get(raw_data, path))
        if number is not None:
            return number
    return None


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _nested_get(raw_data: dict, path: tuple[str, ...]) -> Any:
    value: Any = raw_data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _collect_data_sources(raw_data: dict) -> list[str]:
    sources: list[str] = []
    for key in ("data_sources", "sources", "providers"):
        value = raw_data.get(key)
        if isinstance(value, list):
            sources.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            sources.append(value.strip())

    for key in ("source", "market_data_source", "fundamental_source", "sentiment_source"):
        value = raw_data.get(key)
        if isinstance(value, str) and value.strip():
            sources.append(value.strip())

    market_data = raw_data.get("market_data")
    if isinstance(market_data, dict):
        source = market_data.get("source")
        if isinstance(source, str) and source.strip():
            sources.append(source.strip())

    unique_sources: list[str] = []
    seen = set()
    for source in sources:
        if source not in seen:
            seen.add(source)
            unique_sources.append(source)
    return unique_sources


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _compact_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return "." * max_chars
    return value[: max_chars - 3].rstrip() + "..."
