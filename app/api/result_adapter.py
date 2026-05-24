import json
import re
from typing import Any

from core.settings import settings


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return default
    text = text.replace("Rp", "").replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return default


def _as_percent_score(value: Any) -> int:
    number = _as_float(value)
    if number <= 1:
        number *= 100
    return max(0, min(100, round(number)))


def _parse_entry_range(value: Any) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _as_float(value[0]), _as_float(value[1])
    numbers = re.findall(r"\d+(?:[.,]\d+)?", str(value or ""))
    if not numbers:
        return 0.0, 0.0
    parsed = [_as_float(item.replace(",", ".")) for item in numbers[:2]]
    if len(parsed) == 1:
        return parsed[0], parsed[0]
    return parsed[0], parsed[1]


def _normalize_date(timestamp_str: Any) -> str:
    if not timestamp_str:
        return ""
    text = str(timestamp_str).strip()
    match = re.match(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)} {match.group(4)}:{match.group(5)}:{match.group(6)}"
    if "T" in text:
        return text.replace("T", " ").split(".")[0]
    match_short = re.match(r"^(\d{4})(\d{2})(\d{2})$", text)
    if match_short:
        return f"{match_short.group(1)}-{match_short.group(2)}-{match_short.group(3)}"
    return text


def _metric_value(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else "-"


def _field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        if key == "round" and key not in obj:
            return obj.get("round_num", default)
        return obj.get(key, default)
    if key == "round":
        return getattr(obj, "round_num", default)
    return getattr(obj, key, default)


def _build_scout_metrics(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    verdict = entry.get("verdict") if isinstance(entry.get("verdict"), dict) else {}
    risk = entry.get("risk_governor") if isinstance(entry.get("risk_governor"), dict) else {}
    raw = str(entry.get("raw_data_summary") or "")
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    return {
        "technical": {
            "current_price": risk.get("current_price") or verdict.get("current_price") or 0,
            "entry": (
                f"{risk.get('entry_low', 0):,.0f} - {risk.get('entry_high', 0):,.0f}"
                if risk
                else verdict.get("entry_price_range", "-")
            ),
            "ma200": _metric_value(raw, r"MA200[^\d]*(\d+(?:[.,]\d+)?)"),
            "rsi14": _metric_value(raw, r"RSI\(14\)[^\d]*(\d+(?:[.,]\d+)?)"),
        },
        "fundamental": {
            "fair_value": verdict.get("fair_value") or 0,
            "expected_return": verdict.get("expected_return") or "-",
            "confidence": verdict.get("confidence") or 0,
            "sector": entry.get("sector_key") or "unknown",
        },
        "sentiment": {
            "news": entry.get("news_sentiment") or metadata.get("news_overall_sentiment") or "UNKNOWN",
            "adjustment": entry.get("news_confidence_adjustment") or 0,
            "consensus": entry.get("consensus_method") or verdict.get("consensus_method") or "-",
            "status": entry.get("status") or "-",
        },
    }


def _build_rounds(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for message in history:
        role = str(_field(message, "role") or "").lower()
        if role not in {"bull", "bear"}:
            continue
        round_no = int(_as_float(_field(message, "round"), 0))
        if round_no <= 0:
            continue
        item = grouped.setdefault(
            round_no,
            {
                "round": round_no,
                "bull_argument": "",
                "bear_argument": "",
                "score_delta": 0,
            },
        )
        content = str(_field(message, "content") or "").strip()
        confidence = _as_float(_field(message, "confidence"), 0)
        if role == "bull":
            item["bull_argument"] = content
            item["_bull_confidence"] = confidence
        else:
            item["bear_argument"] = content
            item["_bear_confidence"] = confidence
    rounds: list[dict[str, Any]] = []
    for item in sorted(grouped.values(), key=lambda x: x["round"]):
        bull = _as_float(item.pop("_bull_confidence", 0), 0)
        bear = _as_float(item.pop("_bear_confidence", 0), 0)
        item["score_delta"] = round((bull - bear) * 100)
        rounds.append(item)
    return rounds


_SECTOR_CACHE_PATH = settings.sector_cache_path
_sector_cache: dict[str, dict[str, str]] | None = None


def _get_sector_cache() -> dict[str, dict[str, str]]:
    global _sector_cache
    if _sector_cache is not None:
        return _sector_cache
    if _SECTOR_CACHE_PATH.exists():
        try:
            _sector_cache = json.loads(_SECTOR_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _sector_cache = {}
    else:
        _sector_cache = {}
    return _sector_cache


def _resolve_sector(ticker: str, raw_sector: str | None) -> str:
    """Return a human-readable sector, falling back to sector_cache.json."""
    if raw_sector and raw_sector.lower() not in ("", "unknown"):
        return raw_sector
    cache = _get_sector_cache()
    cached = cache.get(ticker.upper(), {})
    return cached.get("sector") or cached.get("yf_sector") or "unknown"


def normalize_result(entry: dict[str, Any]) -> dict[str, Any]:
    verdict = entry.get("verdict") if isinstance(entry.get("verdict"), dict) else {}
    risk = entry.get("risk_governor") if isinstance(entry.get("risk_governor"), dict) else {}
    entry_low, entry_high = _parse_entry_range(verdict.get("entry_price_range"))
    entry_low = _as_float(risk.get("entry_low"), entry_low)
    entry_high = _as_float(risk.get("entry_high"), entry_high)
    history = entry.get("debate_history") if isinstance(entry.get("debate_history"), list) else []
    rating = str(verdict.get("rating") or "HOLD").upper()
    if rating == "SELL":
        rating = "AVOID"
    ticker = str(entry.get("ticker") or verdict.get("ticker") or "").upper()

    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    raw_date = (
        metadata.get("batch_timestamp")
        or metadata.get("run_timestamp")
        or metadata.get("run_id")
    )
    if not raw_date or str(raw_date).lower() == "unknown":
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from core.settings import settings
        raw_date = datetime.now(ZoneInfo(settings.DATETIME_TIMEZONE)).strftime("%Y%m%d_%H%M%S")

    return {
        "ticker": ticker,
        "sector": _resolve_sector(ticker, entry.get("sector_key")),
        "conviction_score": _as_percent_score(
            entry.get("conviction_score") or verdict.get("confidence")
        ),
        "rating": rating if rating in {"STRONG_BUY", "BUY", "HOLD", "AVOID"} else "HOLD",
        "actionable": bool(risk.get("sizing_allowed", not verdict.get("wait_and_see", False))),
        "target_price": _as_float(risk.get("target_price") or verdict.get("target_price")),
        "stop_loss": _as_float(risk.get("stop_loss") or verdict.get("stop_loss")),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "risk_reward": _as_float(verdict.get("risk_reward_ratio")),
        "debate_rounds": _build_rounds(history),
        "scout_metrics": _build_scout_metrics(entry),
        "devil_advocate_triggered": any(
            str(_field(message, "role") or "").lower() == "devils_advocate"
            for message in history
        ),
        "verdict_summary": str(
            verdict.get("summary")
            or entry.get("error")
            or "No verdict summary available."
        ),
        "verdict_reasoning": str(
            verdict.get("weighted_reasoning")
            or ""
        ),
        "last_debated_at": _normalize_date(raw_date),
    }


def normalize_batch(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [normalize_result(item) for item in data if isinstance(item, dict)]


def normalize_debate_state(ticker: str, state: dict[str, Any]) -> dict[str, Any]:
    verdict: dict[str, Any] = {}
    raw_verdict = state.get("final_verdict")
    if isinstance(raw_verdict, str) and raw_verdict.strip():
        try:
            parsed = json.loads(raw_verdict)
            if isinstance(parsed, dict):
                verdict = parsed
        except json.JSONDecodeError:
            verdict = {"ticker": ticker, "rating": "HOLD", "summary": raw_verdict}
    elif isinstance(raw_verdict, dict):
        verdict = raw_verdict
    entry = {
        "ticker": ticker,
        "verdict": verdict,
        "debate_history": state.get("debate_history") or [],
        "raw_data_summary": state.get("raw_data") or "",
        "metadata": state.get("metadata") or {},
        "error": state.get("error"),
        "status": "failed" if state.get("error") else "success",
        "consensus_method": state.get("consensus_method"),
        "news_sentiment": state.get("metadata", {}).get("news_overall_sentiment"),
        "news_confidence_adjustment": state.get("news_confidence_adjustment", 0.0),
    }
    return normalize_result(entry)


def adapt_result(ticker: str, state: dict[str, Any]) -> dict[str, Any]:
    return normalize_debate_state(ticker, state)
