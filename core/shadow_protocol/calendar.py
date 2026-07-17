"""Frozen IDX trading-calendar contract and shared session-clock helpers."""

from __future__ import annotations

from datetime import date, datetime, time
import hashlib
import json
from typing import Literal, Sequence
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .contracts import Sha256, _StrictFrozenModel


TRADING_CALENDAR_VERSION = "shadow-trading-calendar-v1"
IDX_TIMEZONE = ZoneInfo("Asia/Jakarta")
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(16, 0)


class TradingCalendar(_StrictFrozenModel):
    """Frozen ordered session dates used for trading-day horizons."""

    contract_version: Literal["shadow-trading-calendar-v1"] = (
        TRADING_CALENDAR_VERSION
    )
    calendar_id: str
    calendar_sha256: Sha256
    sessions: tuple[date, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def verify_calendar(self) -> TradingCalendar:
        if len(set(self.sessions)) != len(self.sessions):
            raise ValueError("trading-calendar sessions must be unique")
        if tuple(sorted(self.sessions)) != self.sessions:
            raise ValueError("trading-calendar sessions must be ordered")
        expected = canonical_trading_calendar_sha256(
            self.calendar_id,
            self.sessions,
        )
        if self.calendar_sha256 != expected:
            raise ValueError("trading-calendar hash mismatch")
        return self


def canonical_trading_calendar_sha256(
    calendar_id: str,
    sessions: Sequence[date],
) -> str:
    """Hash dates together with the frozen IDX clock semantics."""

    payload = {
        "contract_version": TRADING_CALENDAR_VERSION,
        "calendar_id": calendar_id,
        "timezone": IDX_TIMEZONE.key,
        "session_open_local": SESSION_OPEN.isoformat(),
        "session_close_local": SESSION_CLOSE.isoformat(),
        "sessions": [item.isoformat() for item in sessions],
    }
    canonical_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def session_close_at(session: date) -> datetime:
    """Return the frozen IDX-local close instant for one session date."""

    return datetime.combine(session, SESSION_CLOSE, tzinfo=IDX_TIMEZONE)


def derive_completed_idx_sessions(
    trading_calendar: TradingCalendar,
    *,
    draft_frozen_at: datetime,
    decided_at: datetime,
) -> tuple[date, ...]:
    """Derive completed post-freeze-date sessions from one trusted calendar.

    A session counts only when its date is strictly after the IDX-local draft
    freeze date and its frozen close instant is no later than the approval
    decision instant.
    """

    if draft_frozen_at.utcoffset() is None or decided_at.utcoffset() is None:
        raise ValueError("calendar chronology datetimes must be timezone-aware")
    if decided_at < draft_frozen_at:
        raise ValueError("calendar decision time cannot precede draft freeze")

    trusted_calendar = TradingCalendar.model_validate(
        trading_calendar.model_dump(mode="python")
    )
    frozen_local_date = draft_frozen_at.astimezone(IDX_TIMEZONE).date()
    decided_local = decided_at.astimezone(IDX_TIMEZONE)
    return tuple(
        session
        for session in trusted_calendar.sessions
        if (
            session > frozen_local_date
            and session_close_at(session) <= decided_local
        )
    )


__all__ = [
    "IDX_TIMEZONE",
    "SESSION_CLOSE",
    "SESSION_OPEN",
    "TRADING_CALENDAR_VERSION",
    "TradingCalendar",
    "canonical_trading_calendar_sha256",
    "derive_completed_idx_sessions",
    "session_close_at",
]
