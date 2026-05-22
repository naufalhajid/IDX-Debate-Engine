"""Deterministic buyability guard for swing-trade recommendations."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


RiskStatus = Literal[
    "deployable",
    "conditional_deployable",
    "wait_for_pullback",
    "watchlist_only",
    "reject",
]

MIN_BUYABLE_CONFIDENCE = 0.60
MIN_BUYABLE_RR = 1.50
UNBUYABLE_RATINGS = {"AVOID", "SELL"}
SOFT_BUYABLE_RATINGS = {"HOLD"}
HARD_REJECT_CODES = {
    "rating_not_buyable",
    "overvalued",
    "rr_too_low",
    "insufficient_technical_data",
}


class RiskDecision(BaseModel):
    """Actionability decision used before position sizing and reporting."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    status: RiskStatus
    sizing_allowed: bool
    reason_codes: list[str]
    message: str
    current_price: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None


_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?")


def evaluate_risk(candidate: dict[str, Any]) -> RiskDecision:
    """Classify whether a CIO setup is executable at the current price."""
    verdict = (
        candidate.get("verdict") if isinstance(candidate.get("verdict"), dict) else {}
    )
    ticker = _clean_ticker(candidate.get("ticker") or verdict.get("ticker"))
    current_price = _first_float(
        verdict.get("current_price"),
        candidate.get("current_price"),
    )
    entry_low, entry_high = _parse_entry_range(
        verdict.get("entry_price_range") or candidate.get("entry_price_range")
    )
    target_price = _first_float(
        verdict.get("target_price"),
        candidate.get("target_price"),
    )
    stop_loss = _first_float(verdict.get("stop_loss"), candidate.get("stop_loss"))

    reason_codes: list[str] = []
    if current_price is None or current_price <= 0:
        reason_codes.append("missing_current_price")
    if entry_low is None or entry_high is None or entry_low <= 0 or entry_high <= 0:
        reason_codes.append("invalid_entry_range")
    elif entry_low > entry_high:
        reason_codes.append("invalid_entry_range")
    if target_price is None or target_price <= 0:
        reason_codes.append("missing_target_price")
    if stop_loss is None or stop_loss <= 0:
        reason_codes.append("missing_stop_loss")

    if reason_codes:
        return RiskDecision(
            ticker=ticker,
            status="reject",
            sizing_allowed=False,
            reason_codes=reason_codes,
            message="Setup ditolak karena data harga kunci belum valid.",
            current_price=current_price,
            entry_low=entry_low,
            entry_high=entry_high,
            target_price=target_price,
            stop_loss=stop_loss,
        )

    assert current_price is not None
    assert entry_low is not None
    assert entry_high is not None
    assert target_price is not None
    assert stop_loss is not None

    verdict_reason_codes = _verdict_reason_codes(candidate, verdict)
    hard_rejects = [code for code in verdict_reason_codes if code in HARD_REJECT_CODES]
    if hard_rejects:
        return RiskDecision(
            ticker=ticker,
            status="reject",
            sizing_allowed=False,
            reason_codes=verdict_reason_codes,
            message=_reject_message(verdict_reason_codes),
            current_price=current_price,
            entry_low=entry_low,
            entry_high=entry_high,
            target_price=target_price,
            stop_loss=stop_loss,
        )

    if target_price <= current_price:
        return RiskDecision(
            ticker=ticker,
            status="reject",
            sizing_allowed=False,
            reason_codes=[*verdict_reason_codes, "upside_exhausted"],
            message="Target sudah tidak memberi upside dari harga sekarang.",
            current_price=current_price,
            entry_low=entry_low,
            entry_high=entry_high,
            target_price=target_price,
            stop_loss=stop_loss,
        )

    if stop_loss >= current_price:
        return RiskDecision(
            ticker=ticker,
            status="reject",
            sizing_allowed=False,
            reason_codes=[*verdict_reason_codes, "invalid_stop_loss"],
            message="Stop-loss tidak berada di bawah harga sekarang.",
            current_price=current_price,
            entry_low=entry_low,
            entry_high=entry_high,
            target_price=target_price,
            stop_loss=stop_loss,
        )

    conditional = _is_conditional_setup(verdict_reason_codes, verdict)
    if entry_low <= current_price <= entry_high:
        if conditional:
            return RiskDecision(
                ticker=ticker,
                status="conditional_deployable",
                sizing_allowed=False,
                reason_codes=[*verdict_reason_codes, "price_inside_entry_range"],
                message=(
                    "Setup hanya conditional/watchlist; rating atau confidence belum "
                    "cukup untuk sizing normal. Wajib konfirmasi breakout/volume "
                    "dan gunakan sizing terbatas."
                ),
                current_price=current_price,
                entry_low=entry_low,
                entry_high=entry_high,
                target_price=target_price,
                stop_loss=stop_loss,
            )
        return RiskDecision(
            ticker=ticker,
            status="deployable",
            sizing_allowed=True,
            reason_codes=["price_inside_entry_range"],
            message="Harga sekarang berada di zona entry; kandidat boleh masuk sizing.",
            current_price=current_price,
            entry_low=entry_low,
            entry_high=entry_high,
            target_price=target_price,
            stop_loss=stop_loss,
        )

    if current_price > entry_high:
        return RiskDecision(
            ticker=ticker,
            status="wait_for_pullback",
            sizing_allowed=False,
            reason_codes=[*verdict_reason_codes, "price_above_entry_range"],
            message="Setup valid sebagai watchlist, tetapi tunggu pullback ke buy range.",
            current_price=current_price,
            entry_low=entry_low,
            entry_high=entry_high,
            target_price=target_price,
            stop_loss=stop_loss,
        )

    return RiskDecision(
        ticker=ticker,
        status="watchlist_only",
        sizing_allowed=False,
        reason_codes=[*verdict_reason_codes, "price_below_entry_range"],
        message="Harga belum berada di zona entry; pantau sampai setup terkonfirmasi.",
        current_price=current_price,
        entry_low=entry_low,
        entry_high=entry_high,
        target_price=target_price,
        stop_loss=stop_loss,
    )


def annotate_risk(entry: dict[str, Any]) -> RiskDecision:
    """Attach a top-level risk_governor artifact to an orchestrator entry."""
    decision = evaluate_risk(entry)
    entry["risk_governor"] = decision.model_dump()
    return decision


def _clean_ticker(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.removesuffix(".JK") or "UNKNOWN"


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = _NUMBER_RE.search(text.replace("Rp", "").replace("rp", ""))
    if match is None:
        return None
    cleaned = match.group(0).replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_entry_range(value: Any) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    numbers = [_to_float(match.group(0)) for match in _NUMBER_RE.finditer(str(value))]
    parsed = [number for number in numbers if number is not None]
    if len(parsed) < 2:
        return None, None
    return parsed[0], parsed[1]


def _verdict_reason_codes(
    candidate: dict[str, Any],
    verdict: dict[str, Any],
) -> list[str]:
    reason_codes: list[str] = []
    rating = _clean_rating(verdict.get("rating") or candidate.get("rating"))
    if rating in UNBUYABLE_RATINGS:
        reason_codes.append("rating_not_buyable")
    elif rating in SOFT_BUYABLE_RATINGS:
        reason_codes.append("rating_hold")

    confidence = _confidence(
        verdict.get("confidence"),
        candidate.get("confidence"),
    )
    if confidence is not None and confidence < MIN_BUYABLE_CONFIDENCE:
        reason_codes.append("low_confidence")

    if _truthy(verdict.get("is_overvalued") or candidate.get("is_overvalued")):
        reason_codes.append("overvalued")

    rr_ratio = _first_float(
        verdict.get("risk_reward_ratio"),
        candidate.get("risk_reward_ratio"),
        candidate.get("rr_ratio"),
    )
    if rr_ratio is not None and rr_ratio < MIN_BUYABLE_RR:
        reason_codes.append("rr_too_low")

    if _technical_data_insufficient(candidate, verdict):
        reason_codes.append("insufficient_technical_data")
    if _counter_trend_setup(candidate, verdict):
        reason_codes.append("counter_trend_setup")

    return _dedupe(reason_codes)


def _clean_rating(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "_")


def _confidence(*values: Any) -> float | None:
    number = _first_float(*values)
    if number is None:
        return None
    if number > 1.0:
        number /= 100.0
    return max(0.0, min(number, 1.0))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "overvalued"}


def _technical_data_insufficient(
    candidate: dict[str, Any],
    verdict: dict[str, Any],
) -> bool:
    if _truthy(
        candidate.get("technical_data_insufficient")
        or verdict.get("technical_data_insufficient")
    ):
        return True

    text = _combined_text(candidate, verdict)
    if "INSUFFICIENT_DATA" not in text:
        return False

    insufficient_technical_phrases = (
        "TECHNICAL DATA",
        "DATA TEKNIS",
        "DATA TEKNIKAL",
        "TECHNICAL INDICATORS",
        "INDIKATOR TEKNIS",
        "INDIKATOR TEKNIKAL",
        "TECHNICAL SUPPORT DATA",
        "ABSENCE OF TECHNICAL INDICATORS",
        "LACK OF TECHNICAL SUPPORT DATA",
        "LACK OF TECHNICAL DATA",
    )
    return any(phrase in text for phrase in insufficient_technical_phrases)


def _counter_trend_setup(
    candidate: dict[str, Any],
    verdict: dict[str, Any],
) -> bool:
    technicals = candidate.get("technical_indicators")
    if isinstance(technicals, dict):
        ma200_context = str(technicals.get("ma200_context") or "").upper()
        if ma200_context == "BELOW":
            return True
    risk_context = candidate.get("risk_context")
    if isinstance(risk_context, dict):
        ma200_context = str(risk_context.get("ma200_context") or "").upper()
        if ma200_context == "BELOW":
            return True

    text = _combined_text(candidate, verdict)
    counter_trend_markers = (
        "COUNTER-TREND",
        "COUNTER TREND",
        "STRUCTURAL DOWNTREND",
        "DOWNTREND STRUKTURAL",
        "PRICE BELOW MA200",
        "BELOW MA200",
        "DI BAWAH MA200",
    )
    return any(marker in text for marker in counter_trend_markers)


def _combined_text(candidate: dict[str, Any], verdict: dict[str, Any]) -> str:
    fields: list[Any] = [
        verdict.get("weighted_reasoning"),
        verdict.get("summary"),
        verdict.get("key_risks"),
        verdict.get("key_catalysts"),
        candidate.get("raw_data_summary"),
        candidate.get("technical_brief"),
    ]
    raw_data = candidate.get("raw_data")
    if isinstance(raw_data, dict):
        fields.extend(raw_data.values())
    metadata = candidate.get("metadata")
    if isinstance(metadata, dict):
        fields.extend(metadata.values())
    return " ".join(_flatten_text(fields)).upper()


def _flatten_text(values: list[Any]) -> list[str]:
    flattened: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            flattened.extend(_flatten_text(list(value.values())))
        elif isinstance(value, list | tuple | set):
            flattened.extend(_flatten_text(list(value)))
        else:
            text = str(value).strip()
            if text:
                flattened.append(text)
    return flattened


def _is_conditional_setup(reason_codes: list[str], verdict: dict[str, Any]) -> bool:
    if not reason_codes:
        return False
    if any(code in HARD_REJECT_CODES for code in reason_codes):
        return False
    rating = _clean_rating(verdict.get("rating"))
    return rating in SOFT_BUYABLE_RATINGS or "counter_trend_setup" in reason_codes


def _reject_message(reason_codes: list[str]) -> str:
    if "rating_not_buyable" in reason_codes:
        return "Setup ditolak karena verdict akhir bukan rating buyable."
    if "insufficient_technical_data" in reason_codes:
        return "Setup ditolak karena data teknikal tidak cukup untuk validasi risiko."
    if "overvalued" in reason_codes:
        return "Setup ditolak karena verdict menandai saham overvalued."
    if "rr_too_low" in reason_codes:
        return "Setup ditolak karena risk/reward terlalu rendah."
    return "Setup ditolak oleh risk governor."


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = ["RiskDecision", "RiskStatus", "annotate_risk", "evaluate_risk"]
