import json
import re
from typing import Any

from utils.ticker import canonicalize_result_identity

from core.execution_regime import execution_regime_from_payload
from core.settings import settings
from utils.trade_math import get_required_rr_resolution


__all__ = [
    "build_execution_decision",
    "normalize_batch",
    "normalize_debate_state",
    "normalize_result",
]


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


def _as_optional_float(value: Any) -> float | None:
    """Parse a finite number without converting missing data into semantic zero."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None

    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Rp", "").replace("rp", "").replace("%", "").strip()
    text = re.sub(r"\s+", "", text)
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        tail = text.rsplit(",", 1)[-1]
        text = text.replace(",", "") if len(tail) == 3 else text.replace(",", ".")
    elif "." in text:
        tail = text.rsplit(".", 1)[-1]
        if len(tail) == 3 and text.replace(".", "").isdigit():
            text = text.replace(".", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _as_confidence(value: Any) -> float | None:
    number = _as_optional_float(value)
    if number is None:
        return None
    if number > 1.0:
        number /= 100.0
    return max(0.0, min(number, 1.0))


def _as_percent_score(value: Any) -> int:
    number = _as_float(value)
    if number <= 1:
        number *= 100
    return max(0, min(100, round(number)))


def _parse_entry_range(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _as_optional_float(value[0]), _as_optional_float(value[1])
    text = str(value or "").strip()
    if not text:
        return None, None
    parts = [
        part
        for part in re.split(r"\s*(?:-|–|—|to|s/d)\s*", text, maxsplit=1)
        if part
    ]
    if len(parts) >= 2:
        return _as_optional_float(parts[0]), _as_optional_float(parts[1])
    parsed = _as_optional_float(text)
    return (parsed, parsed) if parsed is not None else (None, None)


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


def _explicit_regime_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Preserve the canonical execution-regime contract without legacy inference."""
    return {
        "rule_regime_snapshot": entry.get("rule_regime_snapshot"),
        "regime_context": _field(entry, "regime_context", {}),
        "hmm_regime": _field(entry, "hmm_regime", {}),
        "trend_regime": _field(entry, "trend_regime", {}),
        "volatility_regime": entry.get("volatility_regime"),
        "execution_regime": entry.get("execution_regime"),
        "execution_regime_reason": entry.get("execution_regime_reason"),
        "trading_params": _field(entry, "trading_params", {}),
    }


def _build_scout_metrics(
    entry: dict[str, Any],
    *,
    model_confidence: float | None = None,
) -> dict[str, dict[str, Any]]:
    verdict = entry.get("verdict") if isinstance(entry.get("verdict"), dict) else {}
    risk = (
        entry.get("risk_governor")
        if isinstance(entry.get("risk_governor"), dict)
        else {}
    )
    raw = str(entry.get("raw_data_summary") or "")
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    entry_text = verdict.get("entry_price_range") or "-"
    risk_entry_low = _as_optional_float(risk.get("entry_low"))
    risk_entry_high = _as_optional_float(risk.get("entry_high"))
    if risk_entry_low is not None and risk_entry_high is not None:
        entry_text = f"{risk_entry_low:,.0f} - {risk_entry_high:,.0f}"
    return {
        "technical": {
            "current_price": risk.get("current_price")
            or verdict.get("current_price")
            or 0,
            "entry": entry_text,
            "ma200": _metric_value(raw, r"MA200[^\d]*(\d+(?:[.,]\d+)?)"),
            "rsi14": _metric_value(raw, r"RSI\(14\)[^\d]*(\d+(?:[.,]\d+)?)"),
        },
        "fundamental": {
            "fair_value": verdict.get("fair_value"),
            "fair_value_status": verdict.get("fair_value_status"),
            "expected_return": verdict.get("expected_return") or "-",
            "confidence": model_confidence,
            "sector": entry.get("sector_key") or "unknown",
        },
        "sentiment": {
            "news": entry.get("news_sentiment")
            or metadata.get("news_overall_sentiment")
            or "UNKNOWN",
            "adjustment": entry.get("news_confidence_adjustment") or 0,
            "consensus": entry.get("consensus_method")
            or verdict.get("consensus_method")
            or "-",
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


_MODEL_RATINGS = {"STRONG_BUY", "BUY", "HOLD", "SELL", "AVOID"}
_DECISION_SOURCES = {"cio", "preflight", "risk_guard"}
_EXECUTION_STATUSES = {
    "EXECUTABLE_BUY",
    "WAITLIST",
    "NO_TRADE",
    "AVOID",
    "INSUFFICIENT_DATA",
}
_PREFLIGHT_CODES = {
    "rr_too_low",
    "stop_inside_noise",
    "target_collapsed",
    "no_momentum_confirmation",
    "preflight_noise_reject",
    "no_technical_data",
    "insufficient_data",
}
_INSUFFICIENT_CODES = {
    "no_technical_data",
    "insufficient_data",
    "insufficient_technical_data",
    "insufficient_short_history",
    "insufficient_ma200_history",
    "recent_listing_short_history",
    "technical_indicator_calculation_failed",
    "provider_error",
    "provider_history_error",
    "provider_history_unavailable",
    "llm_budget_capacity_exhausted",
    "candidate_intake_invalid",
    "empty_data",
    "invalid_ticker",
}
_WAITLIST_RISK_STATUSES = {
    "conditional_deployable",
    "wait_for_pullback",
    "watchlist_only",
}


def _normalise_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _collect_reason_codes(
    entry: dict[str, Any],
    verdict: dict[str, Any],
    risk: dict[str, Any],
    decision: dict[str, Any],
) -> list[str]:
    values: list[str] = []
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    for source in (decision, entry, verdict, risk, metadata):
        for key in ("reason_codes", "reasons", "rejection_reasons"):
            raw = source.get(key) if isinstance(source, dict) else None
            if isinstance(raw, list):
                values.extend(str(item).strip() for item in raw if str(item).strip())
            elif isinstance(raw, str) and raw.strip():
                values.append(raw.strip())

    preflight = metadata.get("tradeability_preflight")
    if isinstance(preflight, dict):
        reason = str(preflight.get("reason") or "").strip()
        if reason:
            values.append(reason)

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = _normalise_token(value)
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _decision_contract(
    entry: dict[str, Any],
    verdict: dict[str, Any],
    risk: dict[str, Any],
    *,
    entry_low: float | None,
    entry_high: float | None,
) -> dict[str, Any]:
    """Resolve one fail-closed execution decision while preserving legacy fields."""
    decision = (
        entry.get("execution_decision")
        if isinstance(entry.get("execution_decision"), dict)
        else {}
    )
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    position = (
        entry.get("position_sizing")
        if isinstance(entry.get("position_sizing"), dict)
        else {}
    )
    reasons = _collect_reason_codes(entry, verdict, risk, decision)
    reason_set = set(reasons)

    raw_rating = str(
        decision.get("model_rating")
        or entry.get("model_rating")
        or verdict.get("model_rating")
        or verdict.get("rating")
        or ""
    ).strip().upper()
    model_rating = raw_rating if raw_rating in _MODEL_RATINGS else None

    preflight = metadata.get("tradeability_preflight")
    preflight_status = (
        _normalise_token(preflight.get("status"))
        if isinstance(preflight, dict)
        else ""
    )
    verdict_reason_values = verdict.get("reason_codes")
    verdict_reason_set = {
        _normalise_token(value)
        for value in (
            verdict_reason_values if isinstance(verdict_reason_values, list) else []
        )
        if _normalise_token(value)
    }
    verdict_risk_flags = verdict.get("risk_flags")
    has_preflight_guard = (
        bool(verdict_reason_set & _PREFLIGHT_CODES)
        or any(
            str(flag).strip().upper() == "PREFLIGHT_NOISE_REJECT"
            for flag in (
                verdict_risk_flags if isinstance(verdict_risk_flags, list) else []
            )
        )
        or _normalise_token(verdict.get("decision_source")) == "preflight"
        or _normalise_token(entry.get("decision_source")) == "preflight"
        or _normalise_token(metadata.get("decision_source")) == "preflight"
        or preflight_status in {"reject", "skip"}
    )
    risk_status = _normalise_token(risk.get("status"))
    pipeline_status = _normalise_token(entry.get("status"))
    pipeline_failed = bool(entry.get("error")) or pipeline_status in {
        "failed",
        "timeout",
        "error",
        "aborted",
    }

    result_source = _normalise_token(
        decision.get("decision_source")
        or entry.get("decision_source")
    )
    verdict_source = _normalise_token(verdict.get("decision_source"))
    if has_preflight_guard:
        decision_source = "preflight"
    elif pipeline_failed:
        decision_source = "risk_guard"
    elif risk_status in {*_WAITLIST_RISK_STATUSES, "reject"} and model_rating not in {
        "SELL",
        "AVOID",
    }:
        decision_source = "risk_guard"
    elif result_source in _DECISION_SOURCES:
        decision_source = result_source
    elif verdict_source in _DECISION_SOURCES:
        decision_source = verdict_source
    elif model_rating is not None:
        decision_source = "cio"
    else:
        decision_source = "risk_guard"

    summary_text = " ".join(
        str(value or "")
        for value in (
            verdict.get("summary"),
            verdict.get("weighted_reasoning"),
            entry.get("error"),
        )
    ).lower()
    guard_without_model = (
        has_preflight_guard
        or pipeline_failed
        or "cio parse error" in summary_text
        or "trading halted" in summary_text
        or "harga pasar tidak valid" in summary_text
        or bool(metadata.get("cio_parse_failure"))
        or bool(metadata.get("pre_cio_rejection"))
        or bool(metadata.get("candidate_intake_rejection"))
        or bool(reason_set & _INSUFFICIENT_CODES)
    )
    if guard_without_model:
        model_rating = None

    explicit_model_confidence = (
        decision.get("model_confidence")
        if decision.get("model_confidence") is not None
        else entry.get("model_confidence")
        if entry.get("model_confidence") is not None
        else verdict.get("model_confidence")
    )
    if guard_without_model:
        model_confidence = None
    elif explicit_model_confidence is not None:
        model_confidence = _as_confidence(explicit_model_confidence)
    elif model_rating is not None:
        model_confidence = _as_confidence(verdict.get("confidence"))
    else:
        model_confidence = None

    explicit_policy_confidence = (
        decision.get("policy_confidence")
        if decision.get("policy_confidence") is not None
        else entry.get("policy_confidence")
        if entry.get("policy_confidence") is not None
        else verdict.get("policy_confidence")
    )
    policy_confidence = _as_confidence(explicit_policy_confidence)
    if policy_confidence is None and (
        decision_source in {"preflight", "risk_guard"} or risk_status
    ):
        policy_confidence = 1.0

    target_price = _as_optional_float(
        risk.get("target_price")
        if risk.get("target_price") is not None
        else verdict.get("target_price")
    )
    stop_loss = _as_optional_float(
        risk.get("stop_loss")
        if risk.get("stop_loss") is not None
        else verdict.get("stop_loss")
    )
    rr_ratio = _as_optional_float(
        verdict.get("risk_reward_ratio")
        if verdict.get("risk_reward_ratio") is not None
        else entry.get("risk_reward_ratio")
    )
    required_rr = _as_optional_float(
        verdict.get("required_rr")
        if verdict.get("required_rr") is not None
        else entry.get("required_rr")
    )
    ticker = str(entry.get("ticker") or verdict.get("ticker") or "UNKNOWN")
    market_data = entry.get("market_data")
    market_data = market_data if isinstance(market_data, dict) else {}
    yf_info = market_data.get("info")
    yf_info = yf_info if isinstance(yf_info, dict) else None
    if yf_info is None:
        for source in (metadata, entry, verdict, risk):
            market_cap = (
                source.get("rr_market_cap_idr")
                or source.get("market_cap_idr")
                or source.get("marketCap")
            )
            if market_cap not in (None, ""):
                yf_info = {"marketCap": market_cap}
                break
    rr_requirement = get_required_rr_resolution(
        ticker,
        regime=execution_regime_from_payload(entry) or None,
        yf_info=yf_info,
    )
    required_rr = max(rr_requirement.required_rr, required_rr or 0.0)
    horizon_days = _as_optional_float(verdict.get("execution_horizon_days"))
    lot_count = _as_optional_float(
        position.get("lot_count")
        if position.get("lot_count") is not None
        else position.get("lot")
    )
    shares = _as_optional_float(position.get("shares"))
    max_loss_rp = _as_optional_float(position.get("max_loss_rp"))

    valid_setup = bool(
        entry_low is not None
        and entry_high is not None
        and target_price is not None
        and stop_loss is not None
        and rr_ratio is not None
        and entry_low > 0
        and entry_low <= entry_high
        and stop_loss < entry_low
        and target_price > entry_high
    )
    sized_position = bool(
        lot_count is not None
        and shares is not None
        and max_loss_rp is not None
        and lot_count >= 1
        and shares == lot_count * 100
        and max_loss_rp > 0
    )
    executable_buy = bool(
        model_rating in {"BUY", "STRONG_BUY"}
        and valid_setup
        and rr_ratio is not None
        and rr_ratio >= required_rr
        and horizon_days is not None
        and horizon_days >= 2
        and risk_status == "deployable"
        and risk.get("sizing_allowed") is True
        and sized_position
    )

    result_status = str(
        decision.get("execution_status")
        or entry.get("execution_status")
        or ""
    ).strip().upper()
    verdict_status = str(verdict.get("execution_status") or "").strip().upper()
    if pipeline_failed or reason_set & _INSUFFICIENT_CODES:
        execution_status = "INSUFFICIENT_DATA"
    elif preflight_status == "skip":
        execution_status = "INSUFFICIENT_DATA"
    elif executable_buy:
        execution_status = "EXECUTABLE_BUY"
    elif model_rating in {"SELL", "AVOID"}:
        execution_status = "AVOID"
    elif risk_status == "reject":
        execution_status = "NO_TRADE"
    elif risk_status in _WAITLIST_RISK_STATUSES:
        execution_status = "WAITLIST" if valid_setup else "NO_TRADE"
    elif result_status in _EXECUTION_STATUSES and result_status != "EXECUTABLE_BUY":
        execution_status = result_status
    elif verdict_status in _EXECUTION_STATUSES and verdict_status != "EXECUTABLE_BUY":
        execution_status = verdict_status
    elif model_rating == "HOLD":
        execution_status = "WAITLIST" if valid_setup else "NO_TRADE"
    elif model_rating in {"BUY", "STRONG_BUY"}:
        execution_status = "WAITLIST" if valid_setup else "NO_TRADE"
    elif not verdict:
        execution_status = "INSUFFICIENT_DATA"
    else:
        execution_status = "NO_TRADE"

    def add_reason(code: str) -> None:
        if code not in reason_set:
            reason_set.add(code)
            reasons.append(code)

    if execution_status == "WAITLIST" and not risk_status:
        add_reason("risk_decision_missing")
    if (
        model_rating in {"BUY", "STRONG_BUY"}
        and risk_status == "deployable"
        and risk.get("sizing_allowed") is True
        and not sized_position
    ):
        add_reason("position_sizing_pending")
    if (
        result_status == "EXECUTABLE_BUY" or verdict_status == "EXECUTABLE_BUY"
    ) and not executable_buy:
        add_reason("executable_contract_incomplete")

    legacy_rating = (
        "STRONG_BUY"
        if execution_status == "EXECUTABLE_BUY" and model_rating == "STRONG_BUY"
        else "BUY"
        if execution_status == "EXECUTABLE_BUY"
        else "AVOID"
        if execution_status == "AVOID"
        else "HOLD"
    )
    return {
        "decision_contract_version": 1,
        "decision_source": decision_source,
        "execution_status": execution_status,
        "model_rating": model_rating,
        "model_confidence": model_confidence,
        "policy_confidence": policy_confidence,
        "legacy_rating": legacy_rating,
        "actionable": execution_status == "EXECUTABLE_BUY",
        "risk_status": risk_status or None,
        "reason_codes": reasons,
        "target_price": target_price,
        "stop_loss": stop_loss,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "risk_reward": rr_ratio,
        "required_rr": required_rr,
        "rr_base_minimum": rr_requirement.base_rr_minimum,
        "rr_regime_minimum": rr_requirement.regime_rr_minimum,
        "rr_user_floor": rr_requirement.user_execution_floor,
        "rr_regime": rr_requirement.execution_regime,
        "rr_regime_multiplier": rr_requirement.regime_multiplier,
        "rr_tier": rr_requirement.tier_name,
        "rr_tier_label": rr_requirement.tier_label,
        "rr_tier_source": rr_requirement.tier_source,
        "rr_requirement_source": "max_user_floor_tier_x_regime",
        "execution_horizon_days": (
            int(horizon_days) if horizon_days is not None else None
        ),
        "lot_count": int(lot_count) if lot_count is not None else None,
        "shares": int(shares) if shares is not None else None,
        "shares_per_lot": 100,
        "max_loss_rp": max_loss_rp,
        "position_sizing": position or None,
    }


def build_execution_decision(entry: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical fail-closed decision from a persisted pipeline result.

    This public wrapper is intentionally shared by persistence and API consumers so
    the post-risk, post-sizing artifact cannot drift from the dashboard decision.
    """
    if not isinstance(entry, dict):
        raise TypeError("entry must be a dictionary")
    entry = canonicalize_result_identity(entry)

    verdict = entry.get("verdict")
    if not isinstance(verdict, dict):
        verdict = (
            entry.get("final_verdict")
            if isinstance(entry.get("final_verdict"), dict)
            else {}
        )
    risk = (
        entry.get("risk_governor")
        if isinstance(entry.get("risk_governor"), dict)
        else {}
    )
    entry_low, entry_high = _parse_entry_range(verdict.get("entry_price_range"))
    risk_entry_low = _as_optional_float(risk.get("entry_low"))
    risk_entry_high = _as_optional_float(risk.get("entry_high"))
    if risk_entry_low is not None:
        entry_low = risk_entry_low
    if risk_entry_high is not None:
        entry_high = risk_entry_high

    return _decision_contract(
        entry,
        verdict,
        risk,
        entry_low=entry_low,
        entry_high=entry_high,
    )


def normalize_result(entry: dict[str, Any]) -> dict[str, Any]:
    entry = canonicalize_result_identity(entry)
    verdict = entry.get("verdict") if isinstance(entry.get("verdict"), dict) else {}
    risk = (
        entry.get("risk_governor")
        if isinstance(entry.get("risk_governor"), dict)
        else {}
    )
    decision = build_execution_decision(entry)
    from services.recommendation_context import project_recommendation_context

    recommendation_context = project_recommendation_context(
        entry,
        decision=decision,
    )
    entry_low = decision["entry_low"]
    entry_high = decision["entry_high"]
    history = (
        entry.get("debate_history")
        if isinstance(entry.get("debate_history"), list)
        else []
    )
    ticker = entry["ticker"]
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

        raw_date = datetime.now(ZoneInfo(settings.DATETIME_TIMEZONE)).strftime(
            "%Y%m%d_%H%M%S"
        )

    conviction_value = (
        entry.get("trade_conviction")
        if entry.get("trade_conviction") is not None
        else entry.get("conviction_score")
    )
    return {
        "ticker": ticker,
        "sector": _resolve_sector(ticker, entry.get("sector_key")),
        "conviction_score": _as_percent_score(conviction_value),
        # Legacy clients keep receiving BUY/HOLD/AVOID through ``rating``.
        # New clients must use execution_status as the canonical action.
        "rating": decision["legacy_rating"],
        "legacy_rating": decision["legacy_rating"],
        "model_rating": decision["model_rating"],
        "decision_contract_version": decision["decision_contract_version"],
        "decision_source": decision["decision_source"],
        "execution_status": decision["execution_status"],
        "model_confidence": decision["model_confidence"],
        "policy_confidence": decision["policy_confidence"],
        "actionable": decision["actionable"],
        "target_price": decision["target_price"],
        "stop_loss": decision["stop_loss"],
        "entry_low": entry_low,
        "entry_high": entry_high,
        "risk_reward": decision["risk_reward"],
        "fair_value_status": verdict.get("fair_value_status"),
        "required_rr": decision["required_rr"],
        "execution_horizon_days": decision["execution_horizon_days"],
        "lot_count": decision["lot_count"],
        "shares": decision["shares"],
        "shares_per_lot": decision["shares_per_lot"],
        "max_loss_rp": decision["max_loss_rp"],
        "position_sizing": decision["position_sizing"],
        "risk_status": decision["risk_status"],
        "reason_codes": decision["reason_codes"],
        "recommendation_state": recommendation_context.get(
            "recommendation_state"
        ),
        "recommendation_context": recommendation_context,
        "execution_decision": decision,
        "risk_governor": risk or None,
        "debate_rounds": _build_rounds(history),
        "scout_metrics": _build_scout_metrics(
            entry,
            model_confidence=decision["model_confidence"],
        ),
        "devil_advocate_triggered": any(
            str(_field(message, "role") or "").lower() == "devils_advocate"
            for message in history
        ),
        "verdict_summary": str(
            verdict.get("summary")
            or entry.get("error")
            or "No verdict summary available."
        ),
        "verdict_reasoning": str(verdict.get("weighted_reasoning") or ""),
        "last_debated_at": _normalize_date(raw_date),
        **_explicit_regime_fields(entry),
    }


def normalize_batch(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [normalize_result(item) for item in data if isinstance(item, dict)]


def normalize_debate_state(ticker: str, state: dict[str, Any]) -> dict[str, Any]:
    verdict: dict[str, Any] = {}
    verdict_parse_error: str | None = None
    raw_verdict = state.get("final_verdict")
    if isinstance(raw_verdict, str) and raw_verdict.strip():
        try:
            parsed = json.loads(raw_verdict)
            if isinstance(parsed, dict):
                verdict = parsed
        except json.JSONDecodeError as exc:
            # Invalid model output is missing evidence, not a synthetic HOLD.
            verdict = {"ticker": ticker, "summary": raw_verdict}
            verdict_parse_error = f"Invalid final verdict JSON: {exc}"
    elif isinstance(raw_verdict, dict):
        verdict = raw_verdict
    entry = {
        "ticker": ticker,
        "verdict": verdict,
        "debate_history": state.get("debate_history") or [],
        "raw_data_summary": state.get("raw_data") or "",
        "metadata": state.get("metadata") or {},
        "error": state.get("error") or verdict_parse_error,
        "status": (
            "failed" if state.get("error") or verdict_parse_error else "success"
        ),
        "risk_governor": state.get("risk_governor") or {},
        "position_sizing": state.get("position_sizing"),
        "execution_decision": state.get("execution_decision"),
        "decision_source": state.get("decision_source"),
        "execution_status": state.get("execution_status"),
        "model_rating": state.get("model_rating"),
        "model_confidence": state.get("model_confidence"),
        "policy_confidence": state.get("policy_confidence"),
        "trade_conviction": state.get("trade_conviction"),
        "conviction_score": state.get("conviction_score"),
        "consensus_method": state.get("consensus_method"),
        "news_sentiment": state.get("metadata", {}).get("news_overall_sentiment"),
        "news_confidence_adjustment": state.get("news_confidence_adjustment", 0.0),
        **_explicit_regime_fields(state),
    }
    return normalize_result(entry)


def adapt_result(ticker: str, state: dict[str, Any]) -> dict[str, Any]:
    return normalize_debate_state(ticker, state)
