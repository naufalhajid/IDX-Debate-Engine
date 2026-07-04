"""Normalized display semantics for debate reports."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any


@dataclass(frozen=True)
class ValuationDisplayState:
    status: str
    range_text: str | None
    gap_unverified: bool


@dataclass(frozen=True)
class ForecastDisplayState:
    advisory_only: bool
    quality_flags: tuple[str, ...]
    ignored_reason: str | None


@dataclass(frozen=True)
class BreakingNewsDisplayState:
    has_breaking: bool
    lines: tuple[str, ...]


@dataclass(frozen=True)
class DisplayPacket:
    actionability_label: str
    risk_governor_line: str
    valuation: ValuationDisplayState
    forecast: ForecastDisplayState
    breaking_news: BreakingNewsDisplayState
    system_warnings: tuple[str, ...]


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _short_text(value: Any, limit: int = 60) -> str:
    text = str(value or "").strip()
    if not text:
        return "Data unavailable"
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    text = str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", text)
    if not cleaned or cleaned in {"-", ".", ","}:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            cleaned = cleaned.replace(".", "")
    try:
        parsed = float(cleaned)
        return parsed if math.isfinite(parsed) else None
    except ValueError:
        return None


def _money(value: Any, *, include_prefix: bool = True) -> str:
    number = _safe_float(value)
    if number is None or number <= 0:
        return "N/A"
    formatted = f"{number:,.0f}"
    return f"Rp {formatted}" if include_prefix else formatted


def risk_governor_label(risk: dict[str, Any]) -> str:
    payload = _dict_or_empty(risk)
    status = payload.get("status", "unknown")
    reason_codes = payload.get("reason_codes")
    if isinstance(reason_codes, list) and "market_regime_defensive" in {
        str(item) for item in reason_codes
    }:
        return "No sizing (defensive market)"
    labels = {
        "deployable": "Execution ready",
        "conditional_deployable": "Conditional watchlist",
        "wait_for_pullback": "Wait for pullback",
        "watchlist_only": "Watchlist only",
        "reject": "System rejected",
    }
    return labels.get(str(status), str(status))


def valuation_status(
    fair_value: Any,
    current_price: Any,
    fair_value_low: Any = None,
    fair_value_high: Any = None,
) -> str:
    fv = _safe_float(fair_value)
    price = _safe_float(current_price)
    if fv is None or fv <= 0 or price is None or price <= 0:
        return "N/A"
    fv_low = _safe_float(fair_value_low)
    fv_high = _safe_float(fair_value_high)
    if fv_low is not None and fv_low > 0 and price < fv_low:
        return "UNDERVALUED"
    if price < fv * 0.95:
        return "SLIGHTLY_UNDERVALUED"
    if price <= fv * 1.05:
        return "FAIRLY_VALUED"
    if fv_high is not None and fv_high > 0 and price <= fv_high:
        return "SLIGHTLY_OVERVALUED"
    if fv_high is not None and fv_high > 0 and price > fv_high:
        return "OVERVALUED"
    if fv > price:
        return "UNDERVALUED"
    if fv < price:
        return "OVERVALUED"
    return "FAIR VALUE"


def fair_value_range_text(fair_value_low: Any, fair_value_high: Any) -> str | None:
    low = _safe_float(fair_value_low)
    high = _safe_float(fair_value_high)
    if low is None or low <= 0 or high is None or high <= 0:
        return None
    return f"{_money(low)} - {_money(high)}"


def valuation_gap_is_unverified(
    result: dict[str, Any], verdict: dict[str, Any]
) -> bool:
    metadata = _dict_or_empty(result.get("metadata"))
    return (
        str(verdict.get("valuation_gap") or "").lower() == "unverified"
        or str(result.get("valuation_gap") or "").lower() == "unverified"
        or str(metadata.get("valuation_gap") or "").lower() == "unverified"
        or bool(metadata.get("fair_value_rejected"))
    )


def build_valuation_display(result: dict[str, Any]) -> ValuationDisplayState:
    verdict = _dict_or_empty(result.get("verdict"))
    gap_unverified = valuation_gap_is_unverified(result, verdict)
    status = (
        "unverified"
        if gap_unverified
        else valuation_status(
            verdict.get("fair_value"),
            verdict.get("current_price"),
            verdict.get("fair_value_low"),
            verdict.get("fair_value_high"),
        )
    )
    return ValuationDisplayState(
        status=status,
        range_text=fair_value_range_text(
            verdict.get("fair_value_low"),
            verdict.get("fair_value_high"),
        ),
        gap_unverified=gap_unverified,
    )


def build_forecast_display(result: dict[str, Any]) -> ForecastDisplayState:
    forecast_report = _dict_or_empty(result.get("forecast_report"))
    advisory_only = bool(
        forecast_report
        and (
            result.get("forecast_advisory_only") is True
            or result.get("forecast_ranking_enabled") is False
        )
    )
    quality_flags = tuple(
        str(flag) for flag in _list_or_empty(forecast_report.get("data_quality_flags"))
    )
    ignored_reason = str(result.get("forecast_ev_ignored_reason") or "").strip()
    return ForecastDisplayState(
        advisory_only=advisory_only,
        quality_flags=quality_flags,
        ignored_reason=ignored_reason or None,
    )


def breaking_news_lines(result: dict[str, Any]) -> list[str]:
    metadata = _dict_or_empty(result.get("metadata"))
    has_breaking = bool(
        result.get("has_breaking_news") or metadata.get("has_breaking_news")
    )
    if not has_breaking:
        return []
    raw_items = (
        result.get("breaking_news_headlines")
        or metadata.get("breaking_news_headlines")
        or []
    )
    lines: list[str] = []
    bullet = chr(8226)
    em_dash = chr(8212)
    for item in _list_or_empty(raw_items)[:3]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("headline") or "").strip()
        if not title:
            continue
        source = str(item.get("source") or "unknown").strip() or "unknown"
        timestamp = str(item.get("timestamp") or item.get("published_at") or "unknown")
        lines.append(f"{bullet} {title} {em_dash} {source} ({timestamp})")
    if not lines:
        lines.append(
            f"Breaking news detected but headline content unavailable {em_dash} "
            "verify manually."
        )
    return lines


def build_breaking_news_display(result: dict[str, Any]) -> BreakingNewsDisplayState:
    metadata = _dict_or_empty(result.get("metadata"))
    has_breaking = bool(
        result.get("has_breaking_news") or metadata.get("has_breaking_news")
    )
    lines = tuple(breaking_news_lines(result))
    return BreakingNewsDisplayState(has_breaking=has_breaking, lines=lines)


def _failure_warning_line(label: str, value: Any) -> str | None:
    failure = _dict_or_empty(value)
    if not failure:
        return None
    stage = str(failure.get("stage") or "unknown").strip() or "unknown"
    failure_type = str(
        failure.get("type") or failure.get("failure_type") or "unknown"
    ).strip() or "unknown"
    message = _short_text(
        failure.get("message") or failure.get("reason") or "details unavailable",
        limit=96,
    )
    return f"{label}: {stage}/{failure_type} - {message}"


def data_quality_warning_lines(result: dict[str, Any]) -> list[str]:
    metadata = _dict_or_empty(result.get("metadata"))
    forecast = build_forecast_display(result)
    lines: list[str] = []
    for key, label in (
        ("news_fetch_failure", "News fetch failure"),
        ("rag_selection_failure", "RAG selection failure"),
        ("cio_parse_failure", "CIO parse fallback"),
    ):
        line = _failure_warning_line(label, metadata.get(key) or result.get(key))
        if line:
            lines.append(line)

    citation_failures = _list_or_empty(metadata.get("rag_citation_parse_failures"))
    if citation_failures:
        first = _dict_or_empty(citation_failures[0])
        first_type = first.get("type") or "unknown"
        first_message = _short_text(first.get("message"), limit=80)
        lines.append(
            "RAG citation metadata failure: "
            f"{len(citation_failures)} malformed entry(s); "
            f"first={first_type} - {first_message}"
        )

    citation_guard = _dict_or_empty(metadata.get("rag_citation_guard"))
    guard_errors = _list_or_empty(citation_guard.get("errors"))
    if citation_guard and citation_guard.get("valid") is False and guard_errors:
        lines.append(f"RAG citation guard: {_short_text(guard_errors[0], limit=96)}")

    if forecast.advisory_only:
        lines.append("Forecast advisory only: ranking influence disabled")
    if forecast.quality_flags:
        flags = ", ".join(forecast.quality_flags[:4])
        lines.append(f"Forecast data quality: {_short_text(flags, limit=96)}")
    if forecast.ignored_reason:
        lines.append(f"Forecast EV ignored: {forecast.ignored_reason}")

    return lines[:6]


def build_display_packet(result: dict[str, Any]) -> DisplayPacket:
    data = result if isinstance(result, dict) else {}
    risk = _dict_or_empty(data.get("risk_governor"))
    forecast = build_forecast_display(data)
    breaking_news = build_breaking_news_display(data)
    risk_line = risk_governor_label(risk)
    return DisplayPacket(
        actionability_label=risk_line,
        risk_governor_line=risk_line,
        valuation=build_valuation_display(data),
        forecast=forecast,
        breaking_news=breaking_news,
        system_warnings=tuple(data_quality_warning_lines(data)),
    )


__all__ = [
    "BreakingNewsDisplayState",
    "DisplayPacket",
    "ForecastDisplayState",
    "ValuationDisplayState",
    "breaking_news_lines",
    "build_display_packet",
    "data_quality_warning_lines",
    "fair_value_range_text",
    "risk_governor_label",
    "valuation_gap_is_unverified",
    "valuation_status",
]
