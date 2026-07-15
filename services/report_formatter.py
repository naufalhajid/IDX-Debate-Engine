"""Human-friendly Rich and Markdown formatters for debate results."""

from __future__ import annotations

import math

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from services.explainability_auditor import AuditPacket
from utils.logger_config import logger


def _llm_provider_label() -> str:
    try:
        from core.settings import get_settings
        provider = str(get_settings().DEFAULT_LLM_PROVIDER or "").lower()
        return {"gemini": "Google Gemini", "anthropic": "Anthropic Claude", "codex": "OpenAI Codex"}.get(
            provider, provider.title() or "AI"
        )
    except Exception:
        return "AI"


_AGENT_ORDER = (
    "bull",
    "bear",
    "chartist",
    "fundamental_scout",
    "sentiment_specialist",
    "devils_advocate",
)

_AGENT_LABELS = {
    "bull": "Bull",
    "bear": "Bear",
    "chartist": "Chartist",
    "fundamental_scout": "Fundamental Scout",
    "devils_advocate": "Devil's Advocate",
    "sentiment_specialist": "Sentiment Specialist",
}

_METHOD_LABELS = {
    "voting": "Majority voting",
    "confidence_winner": "Confidence winner",
    "soft_hold": "Soft hold rule",
}


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _snapshot_provenance(result: dict[str, Any]) -> tuple[str, str]:
    metadata = _dict_or_empty(result.get("metadata"))
    snapshot = _dict_or_empty(metadata.get("market_snapshot"))
    snapshot_id = snapshot.get("snapshot_id") or metadata.get("snapshot_id") or "-"
    data_hash = snapshot.get("data_hash") or metadata.get("data_hash") or "-"
    return str(snapshot_id), str(data_hash)


def _execution_contract(result: dict[str, Any]) -> dict[str, Any]:
    decision = _dict_or_empty(result.get("execution_decision"))
    if decision:
        return decision
    status = result.get("execution_status")
    if status:
        return {
            "execution_status": status,
            "decision_source": result.get("decision_source"),
            "actionable": result.get("actionable"),
        }
    return {}


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _canonical_position(value: Any) -> str:
    token = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if token in {"STRONG_BUY", "BUY", "BULLISH", "ACCUMULATE"}:
        return "BUY"
    if token in {"SELL", "AVOID", "BEARISH", "DISTRIBUTE"}:
        return "AVOID"
    if token in {"HOLD", "NEUTRAL", "WAIT", "WAIT_AND_SEE"}:
        return "HOLD"
    return token or "UNKNOWN"


def _regime_value(value: Any, *keys: str) -> str:
    """Return a compact uppercase regime label from strings or mappings."""
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate).strip().upper()
        return ""
    return str(value or "").strip().upper()


def _regime_display(result: dict[str, Any]) -> dict[str, Any]:
    """Extract execution authority without promoting legacy regime metadata."""
    data = result if isinstance(result, dict) else {}
    context = _dict_or_empty(data.get("regime_context"))
    metadata = _dict_or_empty(data.get("metadata"))

    execution = _regime_value(data.get("execution_regime"))
    if not execution:
        execution = _regime_value(context.get("execution_regime"))
    if not execution:
        execution = _regime_value(metadata.get("execution_regime"))

    reason = str(
        data.get("execution_regime_reason")
        or context.get("execution_regime_reason")
        or metadata.get("execution_regime_reason")
        or ""
    ).strip()

    trend_payload = (
        data.get("trend_regime")
        or context.get("trend_regime")
        or data.get("hmm_regime")
    )
    trend = _regime_value(trend_payload, "label", "regime") or "UNKNOWN"
    trend_confidence = None
    if isinstance(trend_payload, dict):
        trend_confidence = _confidence(trend_payload.get("confidence"))

    volatility = _regime_value(
        data.get("volatility_regime") or context.get("volatility_regime")
    )
    if not volatility:
        snapshot = _dict_or_empty(
            metadata.get("rule_regime_snapshot")
            or metadata.get("regime_snapshot")
        )
        volatility = _regime_value(snapshot.get("volatility_regime"))

    legacy = _regime_value(metadata.get("regime"))
    if not legacy:
        legacy = _regime_value(data.get("regime"), "label", "regime")

    if not execution:
        execution = "UNKNOWN"
        reason = reason or "legacy_artifact_missing_execution_regime"
    else:
        reason = reason or "reason_not_recorded"

    return {
        "execution": execution,
        "reason": reason,
        "trend": trend,
        "trend_confidence": trend_confidence,
        "volatility": volatility or "UNKNOWN",
        "legacy": legacy,
    }


def _trend_regime_text(regime: dict[str, Any]) -> str:
    confidence = regime.get("trend_confidence")
    suffix = "" if confidence is None else f" ({confidence:.1%})"
    return f"{regime.get('trend') or 'UNKNOWN'}{suffix}"


def _is_devils_advocate_agent(value: Any) -> bool:
    token = _canonical(value)
    return "devil" in token or "devils_advocate" in token


def _agent_label(agent: Any) -> str:
    agent_key = _canonical(agent)
    if agent_key in _AGENT_LABELS:
        return _AGENT_LABELS[agent_key]
    text = str(agent or "Unknown").strip().replace("_", " ")
    return text.title() if text else "Unknown"


def _winner_missing(value: Any) -> bool:
    token = _canonical(value)
    return token in {
        "",
        "none",
        "null",
        "unknown",
        "n/a",
        "data_tidak_tersedia",
        "data_unavailable",
    }


def _soft_hold_label() -> str:
    return "Soft Hold Rule\n(no consensus)"


def _is_soft_hold_winner(method: Any, winner: Any = None) -> bool:
    try:
        method_token = _canonical(method)
        winner_token = _canonical(winner)
        return "soft_hold" in method_token or winner_token in {
            "soft_rule",
            "soft_hold_rule",
        }
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
        return False


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


def _confidence(value: Any) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    if number > 1.0:
        number /= 100.0
    return max(0.0, min(number, 1.0))


def _model_confidence(verdict: dict[str, Any]) -> float | None:
    """Return the explicit model confidence alias, falling back to legacy confidence."""
    return _confidence(
        verdict.get("model_confidence")
        if verdict.get("model_confidence") is not None
        else verdict.get("confidence")
    )


def _money(value: Any, *, include_prefix: bool = True) -> str:
    number = _safe_float(value)
    if number is None or number <= 0:
        return "N/A"
    formatted = f"{number:,.0f}"
    return f"Rp {formatted}" if include_prefix else formatted


def _pct(value: Any) -> str:
    number = _confidence(value)
    return "N/A" if number is None else f"{number:.0%}"


def _signed_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.1f}%"


def _ratio(value: Any) -> str:
    number = _safe_float(value)
    return "N/A" if number is None else f"{number:.2f}x"


def _execution_horizon(verdict: dict[str, Any]) -> str:
    days = _safe_float(verdict.get("execution_horizon_days"))
    if days is None or days <= 0:
        return "N/A"
    return f"{days:.0f} trading days"


def _method_indonesian(value: Any) -> str:
    method = str(value or "unknown")
    return _METHOD_LABELS.get(method, method)


def _yes_no(value: Any) -> str:
    return "Yes" if bool(value) else "No"


def _optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
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


def _now_wib() -> datetime:
    try:
        return datetime.now(ZoneInfo("Asia/Jakarta"))
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
        return datetime.now().astimezone()


def _date_wib() -> str:
    return _now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")


def _verdict(result: dict[str, Any]) -> dict[str, Any]:
    return _dict_or_empty(result.get("verdict"))


def _ticker(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    return str(
        result.get("ticker")
        or _verdict(result).get("ticker")
        or (packet.ticker if packet else None)
        or "UNKNOWN"
    ).upper()


def _rating(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    verdict = _verdict(result)
    return _canonical_position(
        verdict.get("rating")
        or result.get("rating")
        or (packet.verdict_rating if packet else None)
        or "UNKNOWN"
    )


def _entry_bounds(verdict: dict[str, Any]) -> tuple[float | None, float | None]:
    low = _safe_float(verdict.get("entry_low") or verdict.get("entry_price_low"))
    high = _safe_float(verdict.get("entry_high") or verdict.get("entry_price_high"))
    if low is not None and high is not None:
        return low, high
    raw = verdict.get("entry_price_range")
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return _safe_float(raw[0]), _safe_float(raw[1])
    text = str(raw or "")
    parts = [part for part in re.split(r"\s*(?:-|–|—|to|s/d)\s*", text) if part]
    if len(parts) >= 2:
        return _safe_float(parts[0]), _safe_float(parts[1])
    parsed = _safe_float(text)
    return parsed, parsed


def _price_diff_pct(fair_value: Any, current_price: Any) -> float | None:
    fv = _safe_float(fair_value)
    price = _safe_float(current_price)
    if fv is None or fv <= 0 or price is None or price <= 0:
        return None
    return ((fv - price) / price) * 100


def _move_pct(target: Any, current_price: Any) -> float | None:
    target_number = _safe_float(target)
    price = _safe_float(current_price)
    if target_number is None or target_number <= 0 or price is None or price <= 0:
        return None
    return ((target_number - price) / price) * 100


def _downside_pct(stop_loss: Any, current_price: Any) -> float | None:
    stop_number = _safe_float(stop_loss)
    price = _safe_float(current_price)
    if stop_number is None or stop_number <= 0 or price is None or price <= 0:
        return None
    return ((price - stop_number) / price) * 100


def _valuation_status(
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


def _fair_value_range_text(fair_value_low: Any, fair_value_high: Any) -> str | None:
    low = _safe_float(fair_value_low)
    high = _safe_float(fair_value_high)
    if low is None or low <= 0 or high is None or high <= 0:
        return None
    return f"{_money(low)} - {_money(high)}"


# FIX: ISSUE 1 — Treat rejected fair value as an unverified valuation gap.
def _valuation_gap_is_unverified(
    result: dict[str, Any], verdict: dict[str, Any]
) -> bool:
    metadata = _dict_or_empty(result.get("metadata"))
    return (
        str(verdict.get("valuation_gap") or "").lower() == "unverified"
        or str(result.get("valuation_gap") or "").lower() == "unverified"
        or str(metadata.get("valuation_gap") or "").lower() == "unverified"
        or bool(metadata.get("fair_value_rejected"))
    )


# FIX: ISSUE 3 — Format breaking-news headlines for Rich and Markdown reports.
def _breaking_news_lines(result: dict[str, Any]) -> list[str]:
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
    for item in _list_or_empty(raw_items)[:3]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("headline") or "").strip()
        if not title:
            continue
        source = str(item.get("source") or "unknown").strip() or "unknown"
        timestamp = str(item.get("timestamp") or item.get("published_at") or "unknown")
        lines.append(f"• {title} — {source} ({timestamp})")
    if not lines:
        lines.append(
            "Breaking news detected but headline content unavailable — verify manually."
        )
    return lines


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


def _data_quality_warning_lines(result: dict[str, Any]) -> list[str]:
    metadata = _dict_or_empty(result.get("metadata"))
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

    forecast_report = _dict_or_empty(result.get("forecast_report"))
    forecast_flags = _list_or_empty(forecast_report.get("data_quality_flags"))
    if forecast_flags:
        flags = ", ".join(str(flag) for flag in forecast_flags[:4])
        lines.append(f"Forecast data quality: {_short_text(flags, limit=96)}")
    ignored_reason = str(result.get("forecast_ev_ignored_reason") or "").strip()
    if ignored_reason:
        lines.append(f"Forecast EV ignored: {ignored_reason}")

    return lines[:6]


def _short_text(value: Any, limit: int = 60) -> str:
    text = str(value or "").strip()
    if not text:
        return "Data unavailable"
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _get_news(result: dict[str, Any]) -> tuple[str, float]:
    metadata = _dict_or_empty(result.get("metadata"))
    sentiment = str(
        result.get("news_sentiment")
        or metadata.get("news_overall_sentiment")
        or metadata.get("news_sentiment")
        or "unavailable"
    )
    adjustment = _safe_float(
        result.get("news_confidence_adjustment")
        or metadata.get("news_confidence_adjustment")
        or metadata.get("news_adjustment")
    )
    return sentiment, adjustment if adjustment is not None else 0.0


def _generated_at(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    metadata = _dict_or_empty(result.get("metadata"))
    raw = (
        metadata.get("generated_at")
        or metadata.get("run_timestamp")
        or metadata.get("batch_timestamp")
        or (packet.generated_at if packet else None)
    )
    if not raw:
        return _date_wib()
    text = str(raw)
    return text if "WIB" in text.upper() else f"{text} WIB"


def _run_id(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    metadata = _dict_or_empty(result.get("metadata"))
    for raw in (
        metadata.get("run_id"),
        metadata.get("run_timestamp"),
        metadata.get("batch_timestamp"),
        packet.run_id if packet else None,
    ):
        text = str(raw or "").strip()
        if text and text.lower() != "unknown":
            return text
    return "unknown"


def _risk(result: dict[str, Any]) -> dict[str, Any]:
    return _dict_or_empty(result.get("risk_governor"))


def _risk_governor_label(risk: dict) -> str:
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


def _key_risks(result: dict[str, Any]) -> list[str]:
    verdict = _verdict(result)
    risks = _list_or_empty(verdict.get("key_risks"))
    if not risks and verdict.get("critical_risk_factor"):
        risks = [verdict["critical_risk_factor"]]
    return [str(item).strip() for item in risks if str(item).strip()]


def _catalysts(result: dict[str, Any]) -> list[str]:
    verdict = _verdict(result)
    return [
        str(item).strip()
        for item in _list_or_empty(verdict.get("key_catalysts"))
        if str(item).strip()
    ]


_ARGUMENT_NUMBER_RE = re.compile(r"(?:Rp\s*)?\d[\d.,]*(?:\s*(?:%|x))?", re.IGNORECASE)


def _history_round(raw: dict[str, Any]) -> int:
    value = raw.get("round")
    if value is None:
        value = raw.get("round_num")
    number = _safe_float(value)
    return -1 if number is None else int(number)


def _clean_argument_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.split(
        r"\n\s*(?:Position|Agent Confidence)\s*:",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return re.sub(r"\s+", " ", text).strip()


def _argument_sentences(value: Any) -> list[str]:
    text = _clean_argument_text(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]


def _summarize_argument_text(value: Any, limit: int = 220) -> str:
    sentences = _argument_sentences(value)
    if not sentences:
        return "Data unavailable"

    selected: list[str] = [sentences[0]]
    for sentence in sentences[1:]:
        if len(selected) >= 3:
            break
        if _ARGUMENT_NUMBER_RE.search(sentence):
            selected.append(sentence)
    for sentence in sentences[1:]:
        if len(selected) >= 2:
            break
        if sentence not in selected:
            selected.append(sentence)

    summary = " ".join(selected)
    return summary if len(summary) <= limit else summary[: limit - 3].rstrip() + "..."


def _latest_history_argument(result: dict[str, Any], role: str) -> str:
    target = _canonical(role)
    matches: list[tuple[int, str]] = []
    for raw in _list_or_empty(result.get("debate_history")):
        if not isinstance(raw, dict):
            continue
        role_key = _canonical(raw.get("role"))
        if target == "devils_advocate":
            is_match = _is_devils_advocate_agent(role_key)
        else:
            is_match = role_key == target
        if is_match:
            matches.append((_history_round(raw), str(raw.get("content") or "")))
    if not matches:
        return ""
    matches.sort(key=lambda item: item[0])
    return matches[-1][1]


def _key_argument(
    result: dict[str, Any],
    packet: AuditPacket | None,
    role: str,
) -> str:
    if packet:
        if role == "bull" and packet.key_bull_argument:
            return packet.key_bull_argument
        if role == "bear" and packet.key_bear_argument:
            return packet.key_bear_argument
        if role == "devils_advocate" and packet.devils_advocate_question:
            return packet.devils_advocate_question
    text = _latest_history_argument(result, role)
    if not text:
        return "Data unavailable"
    return text if len(text) <= 500 else text[:497] + "..."


def _key_argument_summary(
    result: dict[str, Any],
    packet: AuditPacket | None,
    role: str,
    limit: int = 220,
) -> str:
    packet_text = ""
    if packet:
        if role == "bull":
            packet_text = packet.key_bull_argument
        elif role == "bear":
            packet_text = packet.key_bear_argument
        elif role == "devils_advocate":
            packet_text = packet.devils_advocate_question
    source_text = packet_text or _latest_history_argument(result, role)
    return _summarize_argument_text(source_text, limit)


def _vote_distribution_summary(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> str:
    votes = [
        vote
        for vote in _agent_votes(result, packet)
        if not _is_devils_advocate_agent(_vote_value(vote, "agent"))
    ]
    if not votes:
        return "voting data unavailable"

    counts: dict[str, int] = {}
    for vote in votes:
        position = _canonical_position(_vote_value(vote, "position"))
        if position == "UNKNOWN":
            continue
        counts[position] = counts.get(position, 0) + 1
    if not counts:
        return "voting data unavailable"
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    total = sum(counts.values())
    return ", ".join(f"{position} {count}/{total}" for position, count in ordered)


def _fallback_agent_reason(agent: Any, position: str) -> str:
    agent_key = _canonical(agent)
    if agent_key == "fundamental_scout":
        return f"chose {position} based on valuation, profitability, and fundamental quality"
    if agent_key == "chartist":
        return f"chose {position} based on price trend, momentum, and technical setup"
    if agent_key == "sentiment_specialist":
        return f"chose {position} based on news sentiment and headline risk"
    return f"chose {position} based on available debate signals"


def _agent_choice_reason_lines(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
    *,
    per_reason_limit: int = 130,
) -> list[str]:
    lines: list[str] = []
    for vote in _agent_votes(result, packet):
        agent = _vote_value(vote, "agent")
        if _is_devils_advocate_agent(agent):
            continue
        position = _canonical_position(_vote_value(vote, "position"))
        label = _agent_label(agent)
        reason = str(_vote_value(vote, "summary") or "").strip()
        if not reason and _canonical(agent) in {"bull", "bear"}:
            reason = _key_argument_summary(
                result,
                packet,
                _canonical(agent),
                limit=per_reason_limit,
            )
            if reason == "Data unavailable":
                reason = ""
        if not reason:
            reason = _fallback_agent_reason(agent, position)
        else:
            reason = f"chose {position} because {reason}"
        reason = _short_text(reason, per_reason_limit).rstrip(".; ")
        lines.append(f"{label}: {reason}")
    return lines


def _debate_decision_summary(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
    *,
    limit: int = 700,
) -> str:
    verdict = _verdict(result)
    rating = _rating(result, packet)
    reasoning = str(
        verdict.get("weighted_reasoning")
        or verdict.get("summary")
        or _summary(result, packet)
    ).strip()
    distribution = _vote_distribution_summary(result, packet)
    reason_lines = _agent_choice_reason_lines(result, packet, per_reason_limit=120)

    parts = [
        f"Final decision {rating}: {_short_text(reasoning, 500)}",
        f"Agent choice distribution: {distribution}.",
    ]
    if reason_lines:
        parts.append("Agent rationale: " + "; ".join(reason_lines) + ".")
    return _short_text(" ".join(parts), limit)


def _summary(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    if packet and packet.one_line_summary:
        return packet.one_line_summary
    verdict = _verdict(result)
    return str(
        verdict.get("summary")
        or verdict.get("weighted_reasoning")
        or result.get("summary")
        or "Data unavailable"
    )


def _sources(result: dict[str, Any], packet: AuditPacket | None = None) -> list[str]:
    sources: list[str] = []
    if packet:
        for item in packet.evidence_used:
            if item.source and item.source not in sources:
                sources.append(item.source)
    metadata = _dict_or_empty(result.get("metadata"))
    for key in ("data_sources", "sources", "source"):
        raw = metadata.get(key) or result.get(key)
        if isinstance(raw, list):
            sources.extend(str(item) for item in raw if str(item))
        elif raw:
            sources.append(str(raw))
    unique: list[str] = []
    for source in sources:
        if source not in unique:
            unique.append(source)
    return unique


def _missing_fields(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> list[str]:
    if packet and packet.missing_fields:
        return list(packet.missing_fields)
    metadata = _dict_or_empty(result.get("metadata"))
    raw = result.get("missing_fields") or metadata.get("missing_fields")
    return [str(item) for item in _list_or_empty(raw) if str(item)]


def _vote_value(vote: Any, key: str) -> Any:
    if isinstance(vote, dict):
        return vote.get(key)
    return getattr(vote, key, None)


def _agent_votes(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> list[Any]:
    votes = _list_or_empty(result.get("agent_votes"))
    if not votes and packet:
        votes = list(packet.agent_votes)
    return votes


def _votes_by_agent(votes: list[Any]) -> dict[str, Any]:
    by_agent: dict[str, Any] = {}
    for vote in votes:
        key = _canonical(_vote_value(vote, "agent"))
        if key:
            by_agent[key] = vote
    return by_agent


def _winner_agent(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    verdict = _verdict(result)
    method = result.get("consensus_method") or verdict.get("consensus_method")
    packet_winner = packet.winner_agent if packet else None
    if _is_soft_hold_winner(method, packet_winner):
        return _soft_hold_label()
    if packet and packet.winner_agent and not _winner_missing(packet.winner_agent):
        return packet.winner_agent
    winner = (
        result.get("winner_agent")
        or result.get("confidence_winner")
        or verdict.get("winner_agent")
        or verdict.get("consensus_winner")
    )
    if isinstance(winner, dict):
        winner = winner.get("agent")
    if _is_soft_hold_winner(method, winner):
        return _soft_hold_label()
    if not _winner_missing(winner):
        return str(winner)
    if _canonical(method) == "voting":
        voting_winners = _voting_winner_agents(result, packet)
        if voting_winners:
            return f"{', '.join(voting_winners)} (voting)"
        return "Voting majority"
    return _soft_hold_label() if _is_soft_hold_winner(method) else "Data unavailable"


def _voting_winner_agents(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> list[str]:
    rating = _canonical_position(_rating(result, packet))
    winners: list[str] = []
    for vote in _agent_votes(result, packet):
        agent = _vote_value(vote, "agent")
        if _is_devils_advocate_agent(agent):
            continue
        if _canonical_position(_vote_value(vote, "position")) != rating:
            continue
        label = _agent_label(agent)
        if label not in winners:
            winners.append(label)
    return winners


def _support_mark(
    *,
    vote: Any,
    rating: str,
    agent_key: str,
    winner: str,
) -> str:
    explicit = _vote_value(vote, "supporting_winner")
    if explicit is True:
        return "WINNER" if _canonical(winner) == agent_key else "SUPPORTS"
    if _canonical(winner) == agent_key:
        return "WINNER"
    position = _canonical_position(_vote_value(vote, "position"))
    return "SUPPORTS" if position == _canonical_position(rating) else "DIFFERS"


def _soft_hold_override_note(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> str | None:
    verdict = _verdict(result)
    method = (
        result.get("consensus_method")
        or verdict.get("consensus_method")
        or (packet.consensus_method if packet else None)
    )
    winner = (
        result.get("winner_agent")
        or result.get("confidence_winner")
        or verdict.get("winner_agent")
        or verdict.get("consensus_winner")
        or (packet.winner_agent if packet else None)
    )
    if isinstance(winner, dict):
        winner = winner.get("agent")
    if _is_soft_hold_winner(method, winner):
        return "All agents were overridden by soft_hold_rule because no consensus was reached."
    return None


def _agent_rows(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> list[tuple[str, str, str, str, str, str | None]]:
    votes = _agent_votes(result, packet)
    if not votes:
        return []
    by_agent = _votes_by_agent(votes)
    rating = _rating(result, packet)
    winner = _winner_agent(result, packet)
    rows: list[tuple[str, str, str, str, str, str | None]] = []
    for agent_key in _AGENT_ORDER:
        vote = by_agent.get(agent_key)
        is_adversarial = _is_devils_advocate_agent(agent_key)
        agent_label = _AGENT_LABELS[agent_key]
        if is_adversarial:
            agent_label = f"{agent_label} (adversarial)"
            if vote is None:
                vote = next(
                    (
                        candidate
                        for key, candidate in by_agent.items()
                        if _is_devils_advocate_agent(key)
                    ),
                    None,
                )
        if vote is None:
            result_text = "N/A" if is_adversarial else "NO VOTE"
            style = "dim" if is_adversarial else None
            rows.append((agent_label, "--", "--", "--", result_text, style))
            continue
        position = str(_vote_value(vote, "position") or "--").upper()
        confidence = _confidence(_vote_value(vote, "confidence"))
        confidence_text = "--" if confidence is None else f"{confidence:.0%}"
        effective = _confidence(_vote_value(vote, "effective_confidence"))
        effective_text = "--" if effective is None else f"{effective:.0%}"
        if is_adversarial:
            rows.append(
                (agent_label, position, confidence_text, effective_text, "N/A", "dim")
            )
            continue
        rows.append(
            (
                agent_label,
                position,
                confidence_text,
                effective_text,
                _support_mark(
                    vote=vote,
                    rating=rating,
                    agent_key=agent_key,
                    winner=winner,
                ),
                None,
            )
        )
    return rows


class RichFormatter:
    """Render readable debate summaries to the terminal with Rich."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def _rating_style(self, rating: str) -> str:
        normalized = _canonical_position(rating)
        if normalized == "BUY":
            return "bold green"
        if normalized == "HOLD":
            return "bold yellow"
        if normalized == "AVOID":
            return "bold red"
        return "bold white"

    def _rating_emoji(self, rating: str) -> str:
        normalized = _canonical_position(rating)
        if normalized in {"BUY", "HOLD", "AVOID"}:
            return normalized
        return "UNKNOWN"

    def _confidence_bar(self, confidence: float, width: int = 20) -> str:
        try:
            value = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        filled = round(value * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"{bar} {value:.0%}"

    def _terminal_confidence_bar(self, confidence: float, width: int = 20) -> str:
        if self._console_supports_unicode():
            return self._confidence_bar(confidence, width)
        try:
            value = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        filled = round(value * width)
        bar = "#" * filled + "-" * (width - filled)
        return f"{bar} {value:.0%}"

    def _risk_governor_line(self, risk: dict) -> str:
        return _risk_governor_label(risk)

    def _terminal_emoji(self, rating: str) -> str:
        normalized = _canonical_position(rating)
        return normalized if normalized in {"BUY", "HOLD", "AVOID"} else "UNKNOWN"

    def _terminal_risk_governor_line(self, risk: dict) -> str:
        if self._console_supports_unicode():
            return self._risk_governor_line(risk)
        return _risk_governor_label(risk)

    def _console_supports_unicode(self) -> bool:
        encoding = str(getattr(self.console.file, "encoding", "") or "")
        return "utf" in encoding.lower()

    def _warning_marker(self) -> str:
        return "- "

    def _sparkle_marker(self) -> str:
        return "- "

    def _support_symbol(self, value: str) -> str:
        if value == "N/A":
            return "N/A"
        return value

    def _argument_style(self, role: str) -> str:
        if role == "bull":
            return "green"
        if role == "bear":
            return "red"
        return "yellow"

    def _build_argument_group(
        self,
        result: dict[str, Any],
        packet: AuditPacket | None,
    ) -> Table:
        table = Table.grid(padding=(0, 1))
        table.add_column(style="bold", no_wrap=True)
        table.add_column()
        for label, role in (
            ("Bull", "bull"),
            ("Bear", "bear"),
        ):
            table.add_row(
                Text(label, style=self._argument_style(role)),
                Text(_key_argument_summary(result, packet, role)),
            )
        table.add_row(
            Text("Decision Summary", style="yellow"),
            Text(_debate_decision_summary(result, packet, limit=420)),
        )
        return table

    def render_ticker_panel(
        self,
        result: dict,
        packet: AuditPacket | None = None,
    ) -> None:
        """Print a comprehensive one-page Rich panel for a single ticker."""
        try:
            data = result if isinstance(result, dict) else {}
            ticker = _ticker(data, packet)
            verdict = _verdict(data)
            rating = _rating(data, packet)
            rating_style = self._rating_style(rating)
            confidence = _model_confidence(verdict)
            if confidence is None and packet:
                confidence = _confidence(packet.verdict_confidence)
            confidence_text = (
                "Data unavailable"
                if confidence is None
                else self._terminal_confidence_bar(confidence)
            )
            current_price = verdict.get("current_price")
            fair_value = verdict.get("fair_value")
            fair_value_low = verdict.get("fair_value_low")
            fair_value_high = verdict.get("fair_value_high")
            fair_value_range = _fair_value_range_text(
                fair_value_low, fair_value_high
            )
            risk_overvalued = _optional_bool(verdict.get("risk_overvalued"))
            valuation_unverified = _valuation_gap_is_unverified(data, verdict)
            value_gap = _price_diff_pct(fair_value, current_price)
            value_status = _valuation_status(
                fair_value,
                current_price,
                fair_value_low,
                fair_value_high,
            )
            value_style = "green" if (value_gap or 0) >= 0 else "red"
            entry_low, entry_high = _entry_bounds(verdict)
            target = verdict.get("target_price")
            stop = verdict.get("stop_loss")
            upside = _move_pct(target, current_price)
            downside = _downside_pct(stop, current_price)
            risk = _risk(data)
            regime = _regime_display(data)

            consensus = data.get("consensus_reached")
            if consensus is None:
                consensus = verdict.get("consensus_reached")
            method = data.get("consensus_method") or verdict.get("consensus_method")

            # 1. Header Table
            header_table = Table.grid(padding=(0, 2), expand=True)
            header_table.add_column(justify="left")
            header_table.add_column(justify="right")
            header_table.add_row(
                Text.assemble(
                    ("RATING: ", "bold cyan"),
                    (f"{rating}  ", rating_style),
                    ("TRADE SETUP CONVICTION: ", "bold cyan"),
                    (confidence_text),
                ),
                Text.assemble(
                    ("CONSENSUS: ", "bold cyan"),
                    (
                        _yes_no(consensus) + f" ({_method_indonesian(method)})",
                        "green" if consensus else "yellow",
                    ),
                    ("   ROUND: ", "bold cyan"),
                    (str(data.get("debate_rounds") or "N/A"), "magenta"),
                ),
            )

            # 2. Left Column: Trade Plan & Valuation
            left_table = Table.grid(padding=(0, 2))
            left_table.add_column(style="bold cyan", no_wrap=True)
            left_table.add_column(style="white")
            left_table.add_row("Current Price", _money(current_price))
            if not valuation_unverified:
                left_table.add_row("Fair Value", _money(fair_value))
                if fair_value_range:
                    left_table.add_row("FV Range", fair_value_range)
                if risk_overvalued is not None:
                    left_table.add_row("Risk Overvalued", str(risk_overvalued))
            left_table.add_row(
                "Valuation Gap",
                Text(
                    (
                        "unverified"
                        if valuation_unverified
                        else f"{_signed_pct(value_gap)} ({value_status})"
                    ),
                    style="yellow" if valuation_unverified else value_style,
                ),
            )
            left_table.add_row(
                "Entry Zone", f"{_money(entry_low)} – {_money(entry_high)}"
            )
            left_table.add_row(
                "Target Price", f"{_money(target)}  ({_signed_pct(upside)})"
            )
            left_table.add_row(
                "Stop Loss",
                f"{_money(stop)}  ({_signed_pct(-downside if downside is not None else None)})",
            )
            left_table.add_row("Risk/Reward", _ratio(verdict.get("risk_reward_ratio")))
            left_table.add_row("Timeframe", str(verdict.get("timeframe") or "N/A"))
            left_table.add_row("Execution Horizon", _execution_horizon(verdict))

            # 3. Right Column: Agent Voting & Integration
            vote_table = self._build_vote_table(data, packet)

            # Wrap columns in a parent grid table to show side-by-side
            columns_table = Table.grid(padding=(0, 4), expand=True)
            columns_table.add_column(ratio=1)
            columns_table.add_column(ratio=1)

            trade_panel = Panel(
                left_table,
                title="[bold cyan]TRADE PLAN & VALUATION[/bold cyan]",
                border_style="cyan",
                expand=True,
            )
            vote_panel = Panel(
                vote_table,
                title="[bold magenta]AGENT VOTING & INTEGRATION[/bold magenta]",
                border_style="magenta",
                expand=True,
            )

            columns_table.add_row(trade_panel, vote_panel)

            # 4. Key Debate Arguments
            arg_table = Table.grid(padding=(0, 2), expand=True)
            arg_table.add_column(style="bold", no_wrap=True, width=18)
            arg_table.add_column()

            bull_arg = _key_argument_summary(data, packet, "bull", limit=800)
            bear_arg = _key_argument_summary(data, packet, "bear", limit=800)
            decision_summary = _debate_decision_summary(data, packet, limit=1200)

            arg_table.add_row(
                Text("🟢 Bull (Optimistic)", style="green"), Text(bull_arg)
            )
            arg_table.add_row("", "")
            arg_table.add_row(
                Text("🔴 Bear (Pessimistic)", style="red"), Text(bear_arg)
            )
            da_arg = _key_argument_summary(data, packet, "devils_advocate", limit=800)
            if da_arg and da_arg != "Data unavailable":
                arg_table.add_row("", "")
                arg_table.add_row(
                    Text("⚔️  Devil's Advocate", style="dim"),
                    Text(da_arg, style="dim"),
                )
            arg_table.add_row("", "")
            arg_table.add_row(
                Text("Decision Summary", style="yellow"), Text(decision_summary)
            )

            arg_panel = Panel(
                arg_table,
                title="[bold yellow]KEY DEBATE ARGUMENTS[/bold yellow]",
                border_style="yellow",
                padding=(1, 2),
                expand=True,
            )

            # FIX: ISSUE 3 — Display breaking-news headlines below debate arguments.
            breaking_lines = _breaking_news_lines(data)
            breaking_panel = None
            if breaking_lines:
                breaking_panel = Panel(
                    Text("\n".join(breaking_lines)),
                    title="[bold red]⚠️  BREAKING NEWS[/bold red]",
                    border_style="red",
                    padding=(1, 2),
                    expand=True,
                )

            # 5. System & Risk Management
            risks = _key_risks(data)[:3]
            catalysts = _catalysts(data)[:2] if rating in ("BUY", "STRONG_BUY") else []

            sys_table = Table.grid(padding=(0, 2))
            sys_table.add_column(style="bold cyan", no_wrap=True, width=18)
            sys_table.add_column(style="white")
            sys_table.add_row("Risk Governor", self._terminal_risk_governor_line(risk))
            sys_table.add_row("Execution Regime", regime["execution"])
            sys_table.add_row("Regime Reason", regime["reason"])
            sys_table.add_row(
                "Trend (diagnostic)",
                _trend_regime_text(regime),
            )
            sys_table.add_row(
                "Volatility (diagnostic)",
                regime["volatility"],
            )
            if regime["execution"] == "UNKNOWN" and regime["legacy"]:
                sys_table.add_row(
                    "Legacy Regime (diagnostic)",
                    regime["legacy"],
                )

            if risks:
                sys_table.add_row("Key Risks", "\n".join(f"• {r}" for r in risks))
            if catalysts:
                sys_table.add_row(
                    "Key Catalysts", "\n".join(f"• {c}" for c in catalysts)
                )

            quality_lines = _data_quality_warning_lines(data)
            if quality_lines:
                sys_table.add_row(
                    "Data Quality",
                    "\n".join(f"- {line}" for line in quality_lines),
                )

            sys_panel = Panel(
                sys_table,
                title="[bold blue]SYSTEM & RISK MANAGEMENT[/bold blue]",
                border_style="blue",
                padding=(1, 2),
                expand=True,
            )

            # Assemble everything into a Group and display inside the grand border Panel
            grand_group = Group(
                header_table,
                Rule(style="dim"),
                columns_table,
                arg_panel,
                *([breaking_panel] if breaking_panel is not None else []),
                sys_panel,
                Text(
                    f"Generated: {_generated_at(data, packet)}",
                    style="dim",
                    justify="right",
                ),
            )

            border = {
                "BUY": "green",
                "HOLD": "yellow",
                "AVOID": "red",
            }.get(rating, "white")

            self.console.print(
                Panel(
                    grand_group,
                    title=f"DEBATE ANALYSIS: [bold]{ticker}[/bold]",
                    border_style=border,
                    padding=(1, 2),
                )
            )
        except Exception as exc:
            self.console.print(
                Panel(
                    f"Data unavailable ({exc})",
                    title="ANALYSIS: UNKNOWN",
                    border_style="white",
                )
            )

    def render_batch_summary(
        self,
        results: list[dict],
        *,
        succeeded: int | None = None,
        failed: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Print a readable batch summary panel."""
        try:
            rows = results if isinstance(results, list) else []
            ok_results = [row for row in rows if not row.get("error")]
            fail_results = [row for row in rows if row.get("error")]
            succeeded = len(ok_results) if succeeded is None else succeeded
            failed = len(fail_results) if failed is None else failed

            table = Table(show_header=True, header_style="bold", expand=True)
            table.add_column("Ticker", style="bold", no_wrap=True)
            table.add_column("Rating", no_wrap=True)
            table.add_column("Setup Conviction", justify="right", no_wrap=True)
            table.add_column("R/R", justify="right", no_wrap=True)
            table.add_column("Current Price", justify="right", no_wrap=True)
            table.add_column("Entry Zone")
            table.add_column("Target")
            table.add_column("Risk Gov")
            table.add_column("Execution Regime")

            for row in rows:
                verdict = _verdict(row)
                rating = "ERROR" if row.get("error") else _rating(row)
                low, high = _entry_bounds(verdict)
                risk = _risk(row)
                regime = _regime_display(row)
                current_price = verdict.get("current_price") or risk.get(
                    "current_price"
                )
                table.add_row(
                    _ticker(row),
                    Text(rating, style=self._rating_style(rating)),
                    _pct(_model_confidence(verdict)),
                    _ratio(verdict.get("risk_reward_ratio")),
                    _money(current_price),
                    f"{_money(low, include_prefix=False)}-{_money(high, include_prefix=False)}",
                    _money(verdict.get("target_price")),
                    self._terminal_risk_governor_line(risk),
                    regime["execution"],
                )

            regime_table = Table(
                show_header=True,
                header_style="bold",
                expand=True,
            )
            regime_table.add_column("Ticker", style="bold", no_wrap=True)
            regime_table.add_column("Execution Regime", no_wrap=True)
            regime_table.add_column("Reason")
            regime_table.add_column("Trend (diagnostic)")
            regime_table.add_column("Volatility (diagnostic)")
            for row in rows:
                regime = _regime_display(row)
                regime_table.add_row(
                    _ticker(row),
                    regime["execution"],
                    regime["reason"],
                    _trend_regime_text(regime),
                    regime["volatility"],
                )

            duration_text = ""
            if duration_seconds is not None:
                minutes, seconds = divmod(max(0, int(duration_seconds)), 60)
                duration_text = f"  |  Duration: {minutes}m {seconds:02d}s"
            footer = Text(
                f"Succeeded: {succeeded}  |  Failed: {failed}{duration_text}",
                style="dim",
            )

            self.console.print(
                Panel(
                    Group(
                        table,
                        Rule("REGIME AUTHORITY", style="dim"),
                        regime_table,
                        footer,
                    ),
                    title="DEBATE RESULTS",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )
        except Exception as exc:
            self.console.print(
                Panel(
                    f"Batch summary unavailable ({exc})",
                    title="DEBATE RESULTS",
                    border_style="white",
                )
            )

    def _build_vote_table(
        self,
        result: dict[str, Any],
        packet: AuditPacket | None,
    ) -> Table | Text | Group:
        rows = _agent_rows(result, packet)
        if not rows:
            return Text("No voting data", style="dim")
        table = Table(show_header=True, header_style="bold", expand=False)
        table.add_column("Agent")
        table.add_column("Position")
        table.add_column("Confidence", justify="right")
        table.add_column("Effective", justify="right")
        table.add_column("Outcome")
        for agent, position, confidence, effective, result_text, style in rows:
            table.add_row(
                agent,
                position,
                confidence,
                effective,
                result_text,
                style=style,
            )
        override_note = _soft_hold_override_note(result, packet)
        if override_note:
            return Group(table, Text(override_note, style="dim"))
        return table

    def _batch_recommendation_line(self, result: dict[str, Any], rating: str) -> Group:
        verdict = _verdict(result)
        ticker = _ticker(result)
        confidence = _pct(_model_confidence(verdict))
        rr = _ratio(verdict.get("risk_reward_ratio"))
        low, high = _entry_bounds(verdict)
        target = _money(verdict.get("target_price"))
        reason = _short_text(
            verdict.get("summary") or verdict.get("weighted_reasoning"),
            60,
        )
        first = f"{rating:<5} - {ticker}"
        if rating == "BUY":
            second = (
                f"   model_conf={confidence}  R/R={rr}\n"
                f"   Entry: {_money(low)}-{_money(high)}\n"
                f"   Target: {target}"
            )
        else:
            second = f"   model_conf={confidence}  {reason}"
        return Group(Text(first, style=self._rating_style(rating)), Text(second))

    def _deployable_line(self, result: dict[str, Any]) -> str:
        verdict = _verdict(result)
        low, high = _entry_bounds(verdict)
        return (
            f"{_ticker(result)} - price is inside the entry zone\n"
            f"   Entry: {_money(low)}-{_money(high)}  "
            f"Target: {_money(verdict.get('target_price'))}  "
            f"Stop: {_money(verdict.get('stop_loss'))}"
        )


class MarkdownFormatter:
    """Generate English Markdown reports for debate results."""

    def generate_ticker_report(
        self,
        result: dict,
        packet: AuditPacket | None = None,
    ) -> str:
        """Generate a complete ticker report without raising."""
        try:
            data = result if isinstance(result, dict) else {}
            verdict = _verdict(data)
            ticker = _ticker(data, packet)
            rating = _rating(data, packet)
            confidence = _model_confidence(verdict)
            if confidence is None and packet:
                confidence = _confidence(packet.verdict_confidence)
            confidence_text = "N/A" if confidence is None else f"{confidence:.0%}"
            current_price = verdict.get("current_price")
            fair_value = verdict.get("fair_value")
            fair_value_low = verdict.get("fair_value_low")
            fair_value_high = verdict.get("fair_value_high")
            fair_value_range = _fair_value_range_text(
                fair_value_low, fair_value_high
            )
            risk_overvalued = _optional_bool(verdict.get("risk_overvalued"))
            valuation_unverified = _valuation_gap_is_unverified(data, verdict)
            value_gap = _price_diff_pct(fair_value, current_price)
            value_status = (
                "unverified"
                if valuation_unverified
                else _valuation_status(
                    fair_value,
                    current_price,
                    fair_value_low,
                    fair_value_high,
                )
            )
            low, high = _entry_bounds(verdict)
            target = verdict.get("target_price")
            stop = verdict.get("stop_loss")
            upside = _move_pct(target, current_price)
            downside = _downside_pct(stop, current_price)
            method = data.get("consensus_method") or verdict.get("consensus_method")
            risk = _risk(data)
            news_sentiment, news_adj = _get_news(data)
            sources = _sources(data, packet)
            missing = _missing_fields(data, packet)
            regime = _regime_display(data)
            snapshot_id, data_hash = _snapshot_provenance(data)
            execution = _execution_contract(data)
            recommendation = execution.get("execution_status") or rating
            decision_source = execution.get("decision_source") or "legacy"

            lines = [
                "---",
                f"# Analysis Report: {ticker}",
                f"**Date**: {_date_wib()}",
                f"**Run ID**: {_run_id(data, packet)}",
                "**Mode**: Multi-Agent AI Debate",
                "",
                "---",
                "",
                "## Executive Summary",
                "",
                "| Item | Detail |",
                "|------|--------|",
                f"| **Recommendation** | **{recommendation}** |",
                f"| **Model Opinion** | {rating} |",
                f"| **Decision Source** | {decision_source} |",
                f"| **Trade Setup Conviction** | {confidence_text} |",
                f"| **Execution Regime** | {regime['execution']} |",
                f"| **Execution Regime Reason** | {regime['reason']} |",
                f"| **Trend Regime (diagnostic)** | {_trend_regime_text(regime)} |",
                f"| **Volatility Regime (diagnostic)** | {regime['volatility']} |",
                *(
                    [
                        f"| **Legacy Regime (diagnostic)** | {regime['legacy']} |"
                    ]
                    if regime["execution"] == "UNKNOWN" and regime["legacy"]
                    else []
                ),
                f"| **Current Price** | {_money(current_price)} |",
                *(
                    []
                    if valuation_unverified
                    else [f"| **Fair Value** | {_money(fair_value)} |"]
                ),
                *(
                    []
                    if valuation_unverified or not fair_value_range
                    else [f"| **Fair Value Range** | {fair_value_range} |"]
                ),
                *(
                    []
                    if valuation_unverified or risk_overvalued is None
                    else [
                        f"| **Risk Overvalued** | {str(risk_overvalued)} |"
                    ]
                ),
                (
                    f"| **Gap** | {value_status} |"
                    if valuation_unverified
                    else f"| **Gap** | {_signed_pct(value_gap)} ({value_status}) |"
                ),
                "",
                f"> {_summary(data, packet)}",
                "",
                "---",
                "",
                "## Trade Plan",
                "",
                "| Parameter | Value |",
                "|-----------|-------|",
                f"| **Entry Zone** | {_money(low)} - {_money(high)} |",
                f"| **Target Price** | {_money(target)} ({_signed_pct(upside)}) |",
                f"| **Stop Loss** | {_money(stop)} ({_signed_pct(-downside if downside is not None else None)}) |",
                f"| **Risk/Reward** | {_ratio(verdict.get('risk_reward_ratio'))} |",
                f"| **Timeframe** | {verdict.get('timeframe') or 'N/A'} |",
                f"| **Execution Horizon** | {_execution_horizon(verdict)} |",
                "",
                "---",
                "",
                "## Multi-Agent Debate Process",
                "",
                f"**Rounds**: {data.get('debate_rounds') or 'N/A'}",
                f"**Consensus**: {_yes_no(data.get('consensus_reached') or verdict.get('consensus_reached'))}",
                f"**Method**: {_method_indonesian(method)}",
                "",
                "### Agent Voting",
                "",
                *self._markdown_vote_table(data, packet),
                "",
                "### Key Arguments",
                "",
                "**Bull (Optimistic):**",
                f"> {_key_argument(data, packet, 'bull')}",
                "",
                "**Bear (Pessimistic):**",
                f"> {_key_argument(data, packet, 'bear')}",
                "",
                "**Decision Summary & Agent Rationale:**",
                f"> {_debate_decision_summary(data, packet, limit=900)}",
                "",
                *(
                    [
                        "⚠️  BREAKING NEWS",
                        "",
                        *_breaking_news_lines(data),
                        "",
                    ]
                    if _breaking_news_lines(data)
                    else []
                ),
                "---",
                "",
                "## Risk Analysis",
                "",
            ]
            risks = _key_risks(data)
            if risks:
                lines.extend(f"- {item}" for item in risks)
            else:
                lines.append("Risk data unavailable.")
            lines.append("")

            if rating == "BUY":
                lines.extend(["## Potential Catalysts", ""])
                catalysts = _catalysts(data)
                if catalysts:
                    lines.extend(f"- {item}" for item in catalysts)
                else:
                    lines.append("Catalyst data unavailable.")
                lines.append("")

            quality_lines = _data_quality_warning_lines(data)
            lines.extend(
                [
                    "---",
                    "",
                    "## System Evaluation",
                    "",
                    "| Component | Result |",
                    "|----------|-------|",
                    f"| **Risk Governor** | {_risk_governor_label(risk)} |",
                    f"| **News Sentiment** | {news_sentiment} ({news_adj:+.2f}) |",
                    f"| **Available Data** | {', '.join(sources) if sources else 'None'} |",
                    f"| **Missing Fields** | {', '.join(missing) if missing else 'None'} |",
                    f"| **Data Quality Warnings** | {'; '.join(quality_lines) if quality_lines else 'None'} |",
                    f"| **Market Snapshot ID** | {snapshot_id} |",
                    f"| **Market Snapshot Data Hash** | {data_hash} |",
                    "",
                    "---",
                    "",
                    "## Methodology",
                    "",
                    "This analysis was generated by the",
                    "**Multi-Agent AI Debate** system using",
                    f"{_llm_provider_label()} as the LLM engine.",
                    "Five AI agents with different perspectives",
                    "(Bull, Bear, Chartist, Fundamental Scout,",
                    "Sentiment Specialist) debated for",
                    f"{data.get('debate_rounds') or 'N/A'} rounds before the CIO Agent",
                    "made the final decision.",
                    "",
                    "Every decision can be audited",
                    "against the evidence available",
                    "at analysis time.",
                    "",
                    "---",
                    "*This report was generated automatically by",
                    "IDX Fundamental Analysis System.",
                    "It is not official investment advice.*",
                ]
            )
            return "\n".join(lines)
        except Exception as exc:
            ticker = _ticker(result if isinstance(result, dict) else {}, packet)
            return "\n".join(
                [
                    "---",
                    f"# Analysis Report: {ticker}",
                    f"**Date**: {_date_wib()}",
                    "**Mode**: Multi-Agent AI Debate",
                    "",
                    "Data unavailable.",
                    f"Error formatter: {exc}",
                ]
            )

    def generate_batch_summary(self, results: list[dict], run_id: str) -> str:
        """Generate a Markdown batch summary without raising."""
        try:
            rows = results if isinstance(results, list) else []
            grouped = {"BUY": [], "HOLD": [], "AVOID": []}
            for row in rows:
                grouped.setdefault(_rating(row), []).append(row)
            execution_grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                status = str(
                    _execution_contract(row).get("execution_status")
                    or "UNCLASSIFIED"
                ).upper()
                execution_grouped.setdefault(status, []).append(row)
            deployable = [
                row
                for row in rows
                if str(
                    _execution_contract(row).get("execution_status") or ""
                ).upper()
                == "EXECUTABLE_BUY"
                and _execution_contract(row).get("actionable") is True
            ]
            waiting = [
                row
                for row in rows
                if str(
                    _execution_contract(row).get("execution_status") or ""
                ).upper()
                == "WAITLIST"
            ]
            lines = [
                "---",
                "# Batch Analysis Summary",
                f"**Date**: {_date_wib()}",
                f"**Run ID**: {run_id}",
                f"**Total Stocks**: {len(rows)}",
                "",
                "## Overall Results",
                "",
                "| Rating | Count | Stocks |",
                "|--------|--------|-------|",
                self._rating_summary_row("BUY", grouped.get("BUY", [])),
                self._rating_summary_row("HOLD", grouped.get("HOLD", [])),
                self._rating_summary_row("AVOID", grouped.get("AVOID", [])),
                *[
                    self._rating_summary_row(label, grouped[label])
                    for label in sorted(grouped)
                    if label not in ("BUY", "HOLD", "AVOID")
                ],
                "",
                "## Canonical Execution Decisions",
                "",
                "| Execution Status | Count | Stocks |",
                "|------------------|-------|--------|",
                *[
                    self._execution_summary_row(label, status_rows)
                    for label, status_rows in sorted(execution_grouped.items())
                ],
                "",
                "## Execution Regime Authority",
                "",
                "| Stock | Execution Regime | Reason | Trend (diagnostic) | Volatility (diagnostic) |",
                "|-------|------------------|--------|--------------------|-------------------------|",
                *[self._regime_summary_row(row) for row in rows],
                "",
                "## Market Snapshot Provenance",
                "",
                "| Stock | Snapshot ID | Data Hash |",
                "|-------|-------------|-----------|",
                *[self._snapshot_summary_row(row) for row in rows],
                "",
                "## Executable Stocks",
                "",
            ]
            if deployable:
                for row in deployable:
                    lines.extend(self._deployable_summary(row))
            else:
                lines.append("None.")
                lines.append("")

            lines.extend(["## Watchlist Stocks", ""])
            if waiting:
                for row in waiting:
                    verdict = _verdict(row)
                    low, high = _entry_bounds(verdict)
                    lines.extend(
                        [
                            f"### {_ticker(row)}",
                            f"- Wait for pullback to: {_money(low)} - {_money(high)}",
                            "",
                        ]
                    )
            else:
                lines.append("None.")
                lines.append("")

            lines.extend(["---", "*Generated by IDX Fundamental Analysis*"])
            return "\n".join(lines)
        except Exception as exc:
            return "\n".join(
                [
                    "---",
                    "# Batch Analysis Summary",
                    f"**Date**: {_date_wib()}",
                    f"**Run ID**: {run_id}",
                    "",
                    "Data unavailable.",
                    f"Error formatter: {exc}",
                ]
            )

    def _markdown_vote_table(
        self,
        result: dict[str, Any],
        packet: AuditPacket | None,
    ) -> list[str]:
        rows = _agent_rows(result, packet)
        if not rows:
            return ["No voting data"]
        lines = [
            "| Agent | Position | Confidence | Effective Confidence |",
            "|-------|--------|-----------|----------------------|",
        ]
        for agent, position, confidence, effective, _outcome, _style in rows:
            lines.append(f"| {agent} | {position} | {confidence} | {effective} |")
        override_note = _soft_hold_override_note(result, packet)
        if override_note:
            lines.extend(["", f"*{override_note}*"])
        return lines

    def _rating_summary_row(self, label: str, rows: list[dict[str, Any]]) -> str:
        tickers = ", ".join(_ticker(row) for row in rows) if rows else "-"
        return f"| {label} | {len(rows)} | {tickers} |"

    def _execution_summary_row(
        self,
        label: str,
        rows: list[dict[str, Any]],
    ) -> str:
        tickers = ", ".join(_ticker(row) for row in rows) if rows else "-"
        return f"| {label} | {len(rows)} | {tickers} |"

    def _regime_summary_row(self, result: dict[str, Any]) -> str:
        regime = _regime_display(result)
        return (
            f"| {_ticker(result)} | {regime['execution']} | {regime['reason']} | "
            f"{_trend_regime_text(regime)} | {regime['volatility']} |"
        )

    def _snapshot_summary_row(self, result: dict[str, Any]) -> str:
        snapshot_id, data_hash = _snapshot_provenance(result)
        return f"| {_ticker(result)} | {snapshot_id} | {data_hash} |"

    def _deployable_summary(self, result: dict[str, Any]) -> list[str]:
        verdict = _verdict(result)
        low, high = _entry_bounds(verdict)
        target = verdict.get("target_price")
        current = verdict.get("current_price")
        return [
            f"### {_ticker(result)}",
            f"- Entry: {_money(low)} - {_money(high)}",
            f"- Target: {_money(target)} ({_signed_pct(_move_pct(target, current))})",
            f"- Stop: {_money(verdict.get('stop_loss'))}",
            (
                f"- R/R: {_ratio(verdict.get('risk_reward_ratio'))} | "
                f"Trade Setup Conviction: {_pct(_model_confidence(verdict))}"
            ),
            "",
        ]


DEFAULT_RICH = RichFormatter()
DEFAULT_MD = MarkdownFormatter()
