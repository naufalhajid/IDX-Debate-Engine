"""Deterministic pre-debate trade setup classification.

This service performs no provider or LLM calls. Callers supply an already
fetched OHLCV snapshot and the production envelope calculator so all execution
surfaces can apply the same gate before an expensive debate starts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Literal

import pandas as pd

from utils.ticker import normalize_idx_ticker


TradeSetupStatus = Literal[
    "EXECUTABLE",
    "WAIT_FOR_PULLBACK",
    "NO_MOMENTUM",
    "RR_TOO_LOW",
    "STOP_INSIDE_NOISE",
    "INSUFFICIENT_DATA",
]

SHORT_INDICATOR_MIN_BARS = 60
FULL_MA200_MIN_BARS = 250
RECENT_LISTING_MAX_AGE_DAYS = 400


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(timestamp):
        return None
    return timestamp.date().isoformat()


def _listing_date(market_data: dict[str, Any]) -> str | None:
    """Return a provider-confirmed listing/first-trade date when available."""

    info = market_data.get("info")
    info = info if isinstance(info, dict) else {}
    for candidate in (
        market_data.get("listing_date"),
        market_data.get("first_trade_date"),
        info.get("listingDate"),
        info.get("firstTradeDate"),
    ):
        parsed = _iso_date(candidate)
        if parsed:
            return parsed

    for key in ("firstTradeDateEpochUtc", "firstTradeDateMilliseconds"):
        raw = info.get(key)
        if raw in (None, ""):
            continue
        try:
            epoch = float(raw)
            if key.endswith("Milliseconds") or epoch > 10_000_000_000:
                epoch /= 1000.0
            return datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            continue
    return None


def _recent_listing(listing_date: str | None) -> bool:
    if not listing_date:
        return False
    try:
        listed = pd.Timestamp(listing_date).date()
    except (TypeError, ValueError, OverflowError):
        return False
    age_days = (datetime.now(timezone.utc).date() - listed).days
    return 0 <= age_days <= RECENT_LISTING_MAX_AGE_DAYS


def prepare_ohlcv_history(history: Any) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Clean an OHLCV frame and return serializable data-quality metadata."""

    audit: dict[str, Any] = {
        "raw_bars": 0,
        "complete_bars": 0,
        "first_date": None,
        "last_date": None,
        "history_status": "unavailable",
        "history_reason": "history_unavailable",
    }
    if history is None or not isinstance(history, pd.DataFrame) or history.empty:
        return None, audit

    frame = history.copy()
    audit["raw_bars"] = len(frame)
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)

    required = ["High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        audit.update(
            history_status="invalid",
            history_reason="missing_ohlcv_columns",
            missing_columns=missing,
        )
        return None, audit

    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.dropna(subset=required)
    audit["complete_bars"] = len(frame)
    if frame.empty:
        audit.update(history_status="invalid", history_reason="no_complete_bars")
        return None, audit
    if (frame["Volume"] <= 0).all():
        audit.update(history_status="invalid", history_reason="all_zero_volume")
        return None, audit

    audit.update(
        history_status="ok",
        history_reason="complete",
        first_date=_iso_date(frame.index[0]),
        last_date=_iso_date(frame.index[-1]),
    )
    return frame, audit


def _insufficient_snapshot(
    *,
    ticker: str,
    reason_code: str,
    reason: str,
    history_audit: dict[str, Any],
    execution_regime: str,
    listing_date: str | None,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": "1.0",
        "ticker": ticker,
        "status": "INSUFFICIENT_DATA",
        "reason_code": reason_code,
        "reason": reason,
        "debate_eligible": False,
        "execution_regime": execution_regime,
        "technical_data_status": "INSUFFICIENT_DATA",
        "history": history_audit,
        "listing_date": listing_date,
        "recent_listing": _recent_listing(listing_date),
        "minimum_short_bars": SHORT_INDICATOR_MIN_BARS,
        "minimum_execution_bars": FULL_MA200_MIN_BARS,
        "technical_indicators": {},
        "preflight": preflight
        or {"status": "skip", "reason": "no_technical_data"},
        "envelope": None,
        "hypothetical_envelope": None,
    }


def build_trade_setup_snapshot(
    *,
    ticker: str,
    market_data: dict[str, Any],
    current_price: float,
    execution_regime: str,
    sector: str,
    technical_indicators: dict[str, Any] | None,
    preflight: dict[str, Any],
    envelope_calculator: Callable[
        [float, float | None, dict[str, Any]], dict[str, Any]
    ],
) -> dict[str, Any]:
    """Classify one candidate before any LLM call."""

    symbol = normalize_idx_ticker(ticker)
    regime = str(execution_regime or "UNKNOWN").upper()
    history, audit = prepare_ohlcv_history(market_data.get("history"))
    listing_date = _listing_date(market_data)

    if preflight.get("status") == "reject":
        return {
            "version": "1.0",
            "ticker": symbol,
            "status": "STOP_INSIDE_NOISE",
            "reason_code": "preflight_noise_reject",
            "reason": str(
                preflight.get("reason") or "Preflight noise rejection."
            ),
            "debate_eligible": False,
            "execution_regime": regime,
            "technical_data_status": (
                "COMPLETE" if technical_indicators else "INSUFFICIENT_DATA"
            ),
            "history": audit,
            "listing_date": listing_date,
            "recent_listing": _recent_listing(listing_date),
            "minimum_short_bars": SHORT_INDICATOR_MIN_BARS,
            "minimum_execution_bars": FULL_MA200_MIN_BARS,
            "technical_indicators": dict(technical_indicators or {}),
            "preflight": dict(preflight),
            "envelope": None,
            "hypothetical_envelope": None,
        }

    provider_error = market_data.get("history_error") or market_data.get(
        "provider_error"
    )
    if history is None:
        if provider_error:
            reason_code = "provider_history_error"
            reason = f"OHLCV provider failed: {provider_error}"
        elif _recent_listing(listing_date):
            reason_code = "recent_listing_short_history"
            reason = "No complete OHLCV bars are available for this recently listed ticker."
        elif audit.get("history_status") == "unavailable":
            reason_code = "provider_history_unavailable"
            reason = "The OHLCV provider returned no usable history."
        else:
            reason_code = str(
                audit.get("history_reason") or "provider_history_unavailable"
            )
            reason = "OHLCV history is unavailable or invalid."
        return _insufficient_snapshot(
            ticker=symbol,
            reason_code=reason_code,
            reason=reason,
            history_audit=audit,
            execution_regime=regime,
            listing_date=listing_date,
            preflight=preflight,
        )

    complete_bars = int(audit.get("complete_bars") or 0)
    if complete_bars < SHORT_INDICATOR_MIN_BARS:
        recent = _recent_listing(listing_date)
        return _insufficient_snapshot(
            ticker=symbol,
            reason_code=(
                "recent_listing_short_history"
                if recent
                else "insufficient_short_history"
            ),
            reason=(
                f"Only {complete_bars} complete bars; at least "
                f"{SHORT_INDICATOR_MIN_BARS} are required for short indicators."
            ),
            history_audit=audit,
            execution_regime=regime,
            listing_date=listing_date,
            preflight=preflight,
        )

    if complete_bars < FULL_MA200_MIN_BARS:
        return _insufficient_snapshot(
            ticker=symbol,
            reason_code="insufficient_ma200_history",
            reason=(
                f"Only {complete_bars} complete bars; at least "
                f"{FULL_MA200_MIN_BARS} are required for MA200 execution."
            ),
            history_audit=audit,
            execution_regime=regime,
            listing_date=listing_date,
            preflight=preflight,
        )

    if not technical_indicators:
        return _insufficient_snapshot(
            ticker=symbol,
            reason_code="technical_indicator_calculation_failed",
            reason=(
                "OHLCV is sufficient but required technical indicators are "
                "unavailable."
            ),
            history_audit=audit,
            execution_regime=regime,
            listing_date=listing_date,
            preflight=preflight,
        )

    tech = dict(technical_indicators)
    tech["regime"] = regime
    if sector:
        tech["sector"] = sector
    envelope = envelope_calculator(current_price, None, tech)

    status: TradeSetupStatus
    hypothetical: dict[str, Any] | None = None
    accepted_envelope: dict[str, Any] | None = None
    if envelope.get("rejected"):
        envelope_reason = str(envelope.get("reason_code") or "")
        status = {
            "no_momentum_confirmation": "NO_MOMENTUM",
            "rr_too_low": "RR_TOO_LOW",
            "target_collapsed": "RR_TOO_LOW",
            "stop_inside_noise": "STOP_INSIDE_NOISE",
        }.get(envelope_reason, "RR_TOO_LOW")
        reason_code = envelope_reason or "trade_envelope_rejected"
        reason = str(envelope.get("reason") or "Trade envelope rejected.")
        hypothetical = envelope.get("hypothetical_envelope")
    else:
        accepted_envelope = dict(envelope)
        entry_low = float(envelope.get("entry_low") or 0.0)
        entry_high = float(envelope.get("entry_high") or 0.0)
        if current_price > entry_high > 0:
            status = "WAIT_FOR_PULLBACK"
            reason_code = "price_above_entry_range"
            reason = "Current price is above the deterministic entry range."
        elif 0 < current_price < entry_low:
            status = "NO_MOMENTUM"
            reason_code = "price_below_entry_range"
            reason = "Current price is below the entry range and lacks confirmation."
        else:
            status = "EXECUTABLE"
            reason_code = "trade_envelope_executable"
            reason = (
                "Technical data and deterministic trade envelope are executable."
            )

    return {
        "version": "1.0",
        "ticker": symbol,
        "status": status,
        "reason_code": reason_code,
        "reason": reason,
        "debate_eligible": status == "EXECUTABLE",
        "execution_regime": regime,
        "technical_data_status": "COMPLETE",
        "history": audit,
        "listing_date": listing_date,
        "recent_listing": _recent_listing(listing_date),
        "minimum_short_bars": SHORT_INDICATOR_MIN_BARS,
        "minimum_execution_bars": FULL_MA200_MIN_BARS,
        "technical_indicators": tech,
        "preflight": dict(preflight),
        "envelope": accepted_envelope,
        "hypothetical_envelope": hypothetical,
    }


__all__ = [
    "FULL_MA200_MIN_BARS",
    "RECENT_LISTING_MAX_AGE_DAYS",
    "SHORT_INDICATOR_MIN_BARS",
    "TradeSetupStatus",
    "build_trade_setup_snapshot",
    "prepare_ohlcv_history",
]
