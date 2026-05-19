"""Deterministic buyability guard for swing-trade recommendations."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


RiskStatus = Literal["deployable", "wait_for_pullback", "watchlist_only", "reject"]


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
    verdict = candidate.get("verdict") if isinstance(candidate.get("verdict"), dict) else {}
    ticker = _clean_ticker(candidate.get("ticker") or verdict.get("ticker"))
    current_price = _first_float(
        verdict.get("current_price"),
        candidate.get("current_price"),
    )
    entry_low, entry_high = _parse_entry_range(
        verdict.get("entry_price_range") or candidate.get("entry_price_range")
    )
    target_price = _first_float(verdict.get("target_price"), candidate.get("target_price"))
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

    if target_price <= current_price:
        return RiskDecision(
            ticker=ticker,
            status="reject",
            sizing_allowed=False,
            reason_codes=["upside_exhausted"],
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
            reason_codes=["invalid_stop_loss"],
            message="Stop-loss tidak berada di bawah harga sekarang.",
            current_price=current_price,
            entry_low=entry_low,
            entry_high=entry_high,
            target_price=target_price,
            stop_loss=stop_loss,
        )

    if entry_low <= current_price <= entry_high:
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
            reason_codes=["price_above_entry_range"],
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
        reason_codes=["price_below_entry_range"],
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


__all__ = ["RiskDecision", "RiskStatus", "annotate_risk", "evaluate_risk"]
