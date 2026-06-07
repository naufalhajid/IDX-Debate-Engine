"""Build compact prompt context packs from raw provider outputs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


CONTEXT_CHAR_LIMIT = 3_200
MAX_PROMPT_CHARS = CONTEXT_CHAR_LIMIT
CONTEXT_FIELD_TIERS = {
    "tier1": [
        "rating",
        "current_price",
        "fair_value",
        "fair_value_low",
        "fair_value_high",
        "risk_overvalued",
        "rr",
        "confidence",
        "entry_low",
        "entry_high",
        "target",
        "stop",
    ],
    "tier2": [
        "roe",
        "debt_ratio",
        "net_margin",
        "revenue_growth",
        "eps",
        "pe_ratio",
        "pbv",
    ],
    "tier3": [
        "news_summary",
        "rag_evidence",
        "analyst_notes",
    ],
}
_FUNDAMENTALS_PLACEHOLDER = "__FUNDAMENTALS__"
logger = logging.getLogger(__name__)


class ContextPack(BaseModel):
    """Normalized, provenance-aware context for debate prompts."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    as_of: datetime
    price: float
    fair_value: float | None
    fair_value_base: float | None = None
    fair_value_low: float | None = None
    fair_value_high: float | None = None
    risk_overvalued: bool | None = None
    fundamentals: dict
    technicals: dict
    sentiment_summary: str | None
    data_sources: list[str]
    source_timestamps: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str]
    token_estimate: int
    priority_fields: dict[str, Any] = Field(default_factory=dict)


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
        "fair_value_base",
        "fair_value_estimate",
        nested_paths=(
            ("fundamentals", "fair_value"),
            ("fundamentals", "fair_value_base"),
            ("fundamental_data", "fair_value"),
            ("fundamental_data", "fair_value_base"),
            ("verdict", "fair_value"),
            ("verdict", "fair_value_base"),
        ),
    )
    fair_value_base = _first_number(
        raw_data,
        "fair_value_base",
        nested_paths=(
            ("fundamentals", "fair_value_base"),
            ("fundamental_data", "fair_value_base"),
            ("verdict", "fair_value_base"),
        ),
    )
    fair_value_low = _first_number(
        raw_data,
        "fair_value_low",
        nested_paths=(
            ("fundamentals", "fair_value_low"),
            ("fundamental_data", "fair_value_low"),
            ("verdict", "fair_value_low"),
        ),
    )
    fair_value_high = _first_number(
        raw_data,
        "fair_value_high",
        nested_paths=(
            ("fundamentals", "fair_value_high"),
            ("fundamental_data", "fair_value_high"),
            ("verdict", "fair_value_high"),
        ),
    )
    risk_overvalued = _first_present(raw_data, "risk_overvalued")
    if risk_overvalued is None:
        for path in (
            ("fundamentals", "risk_overvalued"),
            ("fundamental_data", "risk_overvalued"),
            ("verdict", "risk_overvalued"),
        ):
            value = _nested_get(raw_data, path)
            if value not in (None, ""):
                risk_overvalued = value
                break
    fundamentals = _first_dict(
        raw_data, "fundamentals", "fundamental_data", "fundamental"
    )
    technicals = _first_dict(
        raw_data, "technicals", "technical_indicators", "technical_data"
    )
    sentiment_summary = _first_text(
        raw_data,
        "sentiment_summary",
        "sentiment_data",
        "sentiment",
        nested_paths=(("sentiment", "summary"),),
    )
    data_sources = _collect_data_sources(raw_data)
    source_timestamps = _collect_source_timestamps(raw_data)

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
        fair_value_base=fair_value_base or fair_value,
        fair_value_low=fair_value_low,
        fair_value_high=fair_value_high,
        risk_overvalued=_optional_bool(risk_overvalued),
        fundamentals=fundamentals,
        technicals=technicals,
        sentiment_summary=sentiment_summary,
        data_sources=data_sources,
        source_timestamps=source_timestamps,
        missing_fields=missing_fields,
        token_estimate=0,
        priority_fields=_collect_priority_fields(
            raw_data,
            current_price=price,
            fair_value=fair_value,
            fair_value_base=fair_value_base or fair_value,
            fair_value_low=fair_value_low,
            fair_value_high=fair_value_high,
            risk_overvalued=_optional_bool(risk_overvalued),
        ),
    )
    pack.token_estimate = len(pack_to_prompt_string(pack)) // 4
    return pack


def pack_to_prompt_string(
    pack: ContextPack,
    char_limit: int | None = None,
) -> str:
    """Render a context pack using priority tiers instead of flat truncation."""
    limit = CONTEXT_CHAR_LIMIT if char_limit is None else int(char_limit)
    if limit <= 0:
        raise ValueError("context char limit must be greater than zero")

    fields = _priority_fields_for_pack(pack)
    lines = [
        f"Ticker: {pack.ticker}",
        f"As Of: {pack.as_of.isoformat()}",
        _format_priority_section(
            "Tier1 Core Fields", fields, CONTEXT_FIELD_TIERS["tier1"]
        ),
        f"Data Sources: {', '.join(pack.data_sources) if pack.data_sources else 'unknown'}",
        (
            "Source Timestamps: "
            + (
                _compact_json(pack.source_timestamps)
                if pack.source_timestamps
                else "unknown"
            )
        ),
        f"Missing Fields: {', '.join(pack.missing_fields) if pack.missing_fields else 'none'}",
    ]
    omitted: list[str] = []

    def candidate_text(extra_lines: list[str]) -> str:
        return "\n".join([*lines, *extra_lines, *_truncation_footer(omitted)])

    extra_lines: list[str] = []
    buffer_limit = max(0, limit - 200)
    for field_name in CONTEXT_FIELD_TIERS["tier2"]:
        field_line = _format_priority_field(field_name, fields.get(field_name))
        candidate = candidate_text([*extra_lines, field_line])
        if (
            len(candidate) <= buffer_limit
            or not extra_lines
            and len(candidate) <= limit
        ):
            extra_lines.append(field_line)
        else:
            omitted.append(field_name)

    for field_name in CONTEXT_FIELD_TIERS["tier3"]:
        field_line = _format_priority_field(field_name, fields.get(field_name))
        candidate = candidate_text([*extra_lines, field_line])
        if len(candidate) <= limit:
            extra_lines.append(field_line)
        else:
            omitted.append(field_name)

    prompt = candidate_text(extra_lines)
    while len(prompt) > limit and extra_lines:
        removed_line = extra_lines.pop()
        removed_name = removed_line.split(":", 1)[0].strip() or "field"
        if removed_name not in omitted:
            omitted.append(removed_name)
        prompt = candidate_text(extra_lines)

    if omitted:
        logger.warning(
            "Context pack fields truncated for %s to keep prompt under %s chars: %s",
            pack.ticker,
            limit,
            omitted,
        )
    return prompt


def _collect_priority_fields(
    raw_data: dict,
    *,
    current_price: float | None,
    fair_value: float | None,
    fair_value_base: float | None = None,
    fair_value_low: float | None = None,
    fair_value_high: float | None = None,
    risk_overvalued: Any = None,
) -> dict[str, Any]:
    """Collect tiered context fields from raw provider and verdict payloads."""
    verdict = _first_dict(raw_data, "verdict", "final_verdict")
    fundamentals = _first_dict(
        raw_data, "fundamentals", "fundamental_data", "fundamental"
    )
    technicals = _first_dict(
        raw_data, "technicals", "technical_indicators", "technical_data"
    )
    metadata = _first_dict(raw_data, "metadata")
    return {
        "rating": _first_present(verdict, raw_data, "rating"),
        "current_price": current_price,
        "fair_value": fair_value,
        "fair_value_base": fair_value_base or fair_value,
        "fair_value_low": fair_value_low,
        "fair_value_high": fair_value_high,
        "risk_overvalued": risk_overvalued,
        "rr": _first_present(verdict, raw_data, "rr", "risk_reward_ratio", "rr_ratio"),
        "confidence": _first_present(
            verdict, raw_data, "confidence", "model_confidence"
        ),
        "entry_low": _first_present(verdict, raw_data, "entry_low", "entry_price_low"),
        "entry_high": _first_present(
            verdict, raw_data, "entry_high", "entry_price_high"
        ),
        "target": _first_present(verdict, raw_data, "target", "target_price"),
        "stop": _first_present(verdict, raw_data, "stop", "stop_loss"),
        "roe": _first_present(fundamentals, raw_data, "roe", "return_on_equity"),
        "debt_ratio": _first_present(fundamentals, raw_data, "debt_ratio", "der"),
        "net_margin": _first_present(fundamentals, raw_data, "net_margin"),
        "revenue_growth": _first_present(fundamentals, raw_data, "revenue_growth"),
        "eps": _first_present(fundamentals, raw_data, "eps", "eps_ttm"),
        "pe_ratio": _first_present(fundamentals, raw_data, "pe_ratio", "pe", "per"),
        "pbv": _first_present(fundamentals, raw_data, "pbv", "pb_ratio", "pb"),
        "news_summary": _first_present(
            raw_data,
            metadata,
            "news_summary",
            "news_brief",
            "sentiment_summary",
            "sentiment",
        ),
        "rag_evidence": _first_present(
            raw_data, metadata, "rag_evidence", "decision_brief"
        ),
        "analyst_notes": _first_present(
            raw_data,
            fundamentals,
            technicals,
            "analyst_notes",
            "brief",
            "summary",
        ),
    }


def _priority_fields_for_pack(pack: ContextPack) -> dict[str, Any]:
    """Return priority fields with required tier1 fallback values populated."""
    fields = dict(pack.priority_fields)
    fields["current_price"] = fields.get("current_price", pack.price)
    fields["fair_value"] = fields.get("fair_value", pack.fair_value)
    fields["fair_value_base"] = fields.get("fair_value_base", pack.fair_value_base)
    fields["fair_value_low"] = fields.get("fair_value_low", pack.fair_value_low)
    fields["fair_value_high"] = fields.get("fair_value_high", pack.fair_value_high)
    fields["risk_overvalued"] = fields.get("risk_overvalued", pack.risk_overvalued)
    fields.setdefault("news_summary", pack.sentiment_summary)
    for field_name in CONTEXT_FIELD_TIERS["tier1"]:
        fields.setdefault(field_name, "INSUFFICIENT_DATA")
    return fields


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return bool(value)


def _first_present(*dicts_and_keys: Any) -> Any:
    """Return the first non-empty value from the provided dictionaries and keys."""
    dicts = [item for item in dicts_and_keys if isinstance(item, dict)]
    keys = [str(item) for item in dicts_and_keys if not isinstance(item, dict)]
    for source in dicts:
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    return None


def _format_priority_section(
    title: str,
    fields: dict[str, Any],
    field_names: list[str],
) -> str:
    """Format a compact JSON-like section for a tier of priority fields."""
    payload = {name: _display_field_value(fields.get(name)) for name in field_names}
    return f"{title}: {_compact_json(payload)}"


def _format_priority_field(field_name: str, value: Any) -> str:
    """Format one optional context field for the prompt surface."""
    return f"{field_name}: {_display_field_value(value)}"


def _display_field_value(value: Any) -> Any:
    """Normalize prompt field display without dropping real zero values."""
    if value is None or value == "":
        return "INSUFFICIENT_DATA"
    if isinstance(value, dict | list):
        return _compact_json(value)
    return value


def _truncation_footer(omitted: list[str]) -> list[str]:
    """Return footer lines documenting omitted context fields."""
    return [
        f"[truncated: {field_name} omitted due to context limit]"
        for field_name in omitted
    ]


def _resolve_as_of(raw_data: dict) -> datetime:
    value = (
        raw_data.get("as_of")
        or raw_data.get("timestamp")
        or raw_data.get("generated_at")
    )
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

    for key in (
        "source",
        "market_data_source",
        "fundamental_source",
        "sentiment_source",
    ):
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


def _collect_source_timestamps(raw_data: dict) -> dict[str, str]:
    timestamps: dict[str, str] = {}
    raw_timestamps = raw_data.get("source_timestamps")
    if isinstance(raw_timestamps, dict):
        for source, timestamp in raw_timestamps.items():
            clean_source = str(source).strip().lower()
            clean_timestamp = _optional_text(timestamp)
            if clean_source and clean_timestamp:
                timestamps[clean_source] = clean_timestamp

    market_data = raw_data.get("market_data")
    if isinstance(market_data, dict):
        source = str(market_data.get("source") or "market_data").strip().lower()
        fetched_at = _optional_text(
            market_data.get("history_as_of") or market_data.get("fetched_at")
        )
        if source and fetched_at:
            timestamps[source] = fetched_at
            timestamps.setdefault("market_data", fetched_at)

    for key in ("as_of", "timestamp", "generated_at"):
        timestamp = _optional_text(raw_data.get(key))
        if timestamp:
            timestamps.setdefault("context", timestamp)
            break
    return timestamps


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _compact_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return "." * max_chars
    return value[: max_chars - 3].rstrip() + "..."
