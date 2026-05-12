"""Shared failure taxonomy for provider and debate pipeline errors."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(str, Enum):
    DNS = "DNS"
    QUOTA = "QUOTA"
    AUTH = "AUTH"
    NO_PRICE = "NO_PRICE"
    SCHEMA = "SCHEMA"
    TIMEOUT = "TIMEOUT"
    EMPTY_LLM = "EMPTY_LLM"
    UNKNOWN = "UNKNOWN"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FailureRecord(BaseModel):
    """Normalized failure details for one pipeline step."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    code: ErrorCode
    source: str
    message: str
    timestamp: datetime = Field(default_factory=_utc_now)
    retryable: bool


_AUTH_STATUS_PATTERN = re.compile(r"\b(?:401|403)\b")
_RETRYABLE_BY_CODE = {
    ErrorCode.DNS: True,
    ErrorCode.QUOTA: True,
    ErrorCode.AUTH: False,
    ErrorCode.NO_PRICE: False,
    ErrorCode.SCHEMA: False,
    ErrorCode.TIMEOUT: True,
    ErrorCode.EMPTY_LLM: True,
    ErrorCode.UNKNOWN: False,
}


def classify_exception(e: Exception, source: str) -> FailureRecord:
    """Map common provider/debate exceptions into a normalized failure record."""
    raw_message = str(e)
    message = raw_message.strip() or "Empty LLM response"
    lower_message = message.lower()
    exc_name = type(e).__name__.lower()

    code = ErrorCode.UNKNOWN
    if _is_timeout(e, lower_message, exc_name):
        code = ErrorCode.TIMEOUT
    elif _is_auth(e, lower_message):
        code = ErrorCode.AUTH
    elif _is_dns(e, lower_message, exc_name):
        code = ErrorCode.DNS
    elif _is_quota(lower_message):
        code = ErrorCode.QUOTA
    elif _is_empty_llm(raw_message, lower_message):
        code = ErrorCode.EMPTY_LLM
    elif _is_no_price(lower_message):
        code = ErrorCode.NO_PRICE
    elif _is_schema(lower_message):
        code = ErrorCode.SCHEMA

    return FailureRecord(
        ticker=_extract_ticker(e),
        code=code,
        source=source,
        message=message,
        retryable=_RETRYABLE_BY_CODE[code],
    )


def _extract_ticker(e: Exception) -> str:
    ticker = getattr(e, "ticker", "")
    return str(ticker) if ticker is not None else ""


def _is_timeout(e: Exception, lower_message: str, exc_name: str) -> bool:
    return (
        isinstance(e, TimeoutError)
        or "timeout" in exc_name
        or "timed out" in lower_message
        or "timeout" in lower_message
    )


def _is_auth(e: Exception, lower_message: str) -> bool:
    status_code = _extract_status_code(e)
    return (
        status_code in {401, 403}
        or bool(_AUTH_STATUS_PATTERN.search(lower_message))
        or "unauthorized" in lower_message
        or "forbidden" in lower_message
    )


def _extract_status_code(e: Exception) -> int | None:
    for candidate in (
        getattr(e, "status_code", None),
        getattr(e, "code", None),
        getattr(getattr(e, "response", None), "status_code", None),
    ):
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _is_dns(e: Exception, lower_message: str, exc_name: str) -> bool:
    dns_markers = (
        "dns",
        "enotfound",
        "failed to resolve",
        "getaddrinfo",
        "name resolution",
        "temporary failure in name resolution",
        "nodename nor servname",
    )
    return (
        isinstance(e, ConnectionError)
        or "connectionerror" in exc_name
        or any(marker in lower_message for marker in dns_markers)
    )


def _is_quota(lower_message: str) -> bool:
    quota_markers = (
        "quota",
        "rate limit",
        "resource_exhausted",
        "too many requests",
        "budget exhausted",
        "429",
    )
    return any(marker in lower_message for marker in quota_markers)


def _is_empty_llm(raw_message: str, lower_message: str) -> bool:
    empty_markers = (
        "empty response",
        "empty llm",
        "empty string",
        "no content",
        "llm returned an empty",
    )
    return not raw_message.strip() or any(
        marker in lower_message for marker in empty_markers
    )


def _is_no_price(lower_message: str) -> bool:
    price_markers = (
        "no price",
        "empty price",
        "price unavailable",
        "possibly delisted",
        "no data found",
    )
    return any(marker in lower_message for marker in price_markers)


def _is_schema(lower_message: str) -> bool:
    schema_markers = (
        "schema",
        "validation",
        "pydantic",
        "json decode",
        "decode error",
        "invalid json",
        "model_validate",
    )
    return any(marker in lower_message for marker in schema_markers)
