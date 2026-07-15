"""Shared failure taxonomy for provider and debate pipeline errors."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from utils.secret_redaction import redact_secrets


class ErrorCode(str, Enum):
    DNS = "DNS"
    QUOTA = "QUOTA"
    AUTH = "AUTH"
    NO_PRICE = "NO_PRICE"
    SCHEMA = "SCHEMA"
    TIMEOUT = "TIMEOUT"
    EMPTY_LLM = "EMPTY_LLM"
    UNKNOWN = "UNKNOWN"


class FailureAction(str, Enum):
    RETRY = "retry"
    SKIP = "skip"
    ABORT = "abort"
    FAIL = "fail"


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


class FailureDecision(BaseModel):
    """Routing decision for a normalized pipeline failure."""

    model_config = ConfigDict(extra="forbid")

    failure: FailureRecord
    action: FailureAction
    attempt: int
    max_attempts: int
    retry_after_seconds: float | None = None
    reason: str


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
    raw_message = redact_secrets(e)
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


def route_failure(
    failure: FailureRecord | Exception,
    source: str = "unknown",
    *,
    attempt: int = 1,
    max_attempts: int = 3,
) -> FailureDecision:
    """Choose a consistent retry/skip/abort/fail action for a failure."""
    record = (
        failure
        if isinstance(failure, FailureRecord)
        else classify_exception(failure, source)
    )
    safe_attempt = max(1, attempt)
    safe_max_attempts = max(1, max_attempts)

    action: FailureAction
    retry_after_seconds: float | None = None
    reason: str

    if record.code is ErrorCode.AUTH:
        action = FailureAction.ABORT
        reason = "authentication failures require operator intervention"
    elif record.code is ErrorCode.QUOTA and _is_budget_exhaustion(
        record.message.lower()
    ):
        action = FailureAction.ABORT
        reason = "budget exhaustion should stop remaining agent work"
    elif record.retryable and safe_attempt < safe_max_attempts:
        action = FailureAction.RETRY
        retry_after_seconds = _retry_delay_seconds(record.code, safe_attempt)
        reason = f"{record.code.value} is retryable and attempts remain"
    elif record.code in {
        ErrorCode.DNS,
        ErrorCode.TIMEOUT,
        ErrorCode.EMPTY_LLM,
        ErrorCode.QUOTA,
    }:
        action = FailureAction.SKIP
        reason = f"{record.code.value} exhausted retries; skip this unit of work"
    elif record.code is ErrorCode.NO_PRICE:
        action = FailureAction.SKIP
        reason = "price is unavailable for this ticker"
    else:
        action = FailureAction.FAIL
        reason = f"{record.code.value} is not recoverable by retry"

    return FailureDecision(
        failure=record,
        action=action,
        attempt=safe_attempt,
        max_attempts=safe_max_attempts,
        retry_after_seconds=retry_after_seconds,
        reason=reason,
    )


def _retry_delay_seconds(code: ErrorCode, attempt: int) -> float:
    if code is ErrorCode.QUOTA:
        return 60.0
    return float(min(2 ** max(attempt - 1, 0), 30))


def _is_budget_exhaustion(lower_message: str) -> bool:
    return (
        "budget exhausted" in lower_message or "daily pro-call budget" in lower_message
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
        or "token expired" in lower_message
        or "oauth" in lower_message
        and "invalid" in lower_message
        or "invalid_grant" in lower_message
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
        "rate_limit_exceeded",
        "overloaded",
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
