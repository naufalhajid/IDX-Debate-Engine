from datetime import datetime

import pytest

from core.failure_taxonomy import ErrorCode, FailureRecord, classify_exception
from core.failure_taxonomy import FailureAction, route_failure


def test_classify_connection_error_as_dns() -> None:
    result = classify_exception(
        ConnectionError("Failed to resolve 'exodus.stockbit.com'"),
        source="stockbit",
    )

    assert isinstance(result, FailureRecord)
    assert result.code is ErrorCode.DNS
    assert result.source == "stockbit"
    assert result.retryable is True
    assert isinstance(result.timestamp, datetime)


def test_classify_timeout_error_as_timeout() -> None:
    result = classify_exception(
        TimeoutError("debate timed out"), source="debate_chamber"
    )

    assert result.code is ErrorCode.TIMEOUT
    assert result.source == "debate_chamber"
    assert result.retryable is True


@pytest.mark.parametrize("status_code", [401, 403])
def test_classify_http_auth_response_as_auth(status_code: int) -> None:
    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    class HTTPError(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__(f"HTTP {status_code}")
            self.response = Response(status_code)

    result = classify_exception(HTTPError(status_code), source="stockbit")

    assert result.code is ErrorCode.AUTH
    assert result.retryable is False


def test_classify_empty_string_llm_response_as_empty_llm() -> None:
    result = classify_exception(ValueError(""), source="gemini")

    assert result.code is ErrorCode.EMPTY_LLM
    assert result.source == "gemini"
    assert result.message == "Empty LLM response"
    assert result.retryable is True


def test_route_failure_retries_retryable_error_with_attempts_remaining() -> None:
    failure = classify_exception(TimeoutError("debate timed out"), source="debate")

    decision = route_failure(failure, attempt=1, max_attempts=3)

    assert decision.action is FailureAction.RETRY
    assert decision.retry_after_seconds == 1.0
    assert "attempts remain" in decision.reason


def test_route_failure_skips_retryable_error_after_max_attempts() -> None:
    failure = classify_exception(TimeoutError("debate timed out"), source="debate")

    decision = route_failure(failure, attempt=3, max_attempts=3)

    assert decision.action is FailureAction.SKIP
    assert decision.retry_after_seconds is None


def test_route_failure_aborts_auth_and_budget_exhaustion() -> None:
    auth = classify_exception(PermissionError("401 Unauthorized"), source="stockbit")
    budget = classify_exception(
        RuntimeError("Budget exhausted at charge point"), source="orchestrator"
    )

    assert route_failure(auth).action is FailureAction.ABORT
    assert route_failure(budget).action is FailureAction.ABORT


def test_route_failure_accepts_raw_exception() -> None:
    decision = route_failure(ValueError("invalid json"), source="cio")

    assert decision.failure.code is ErrorCode.SCHEMA
    assert decision.action is FailureAction.FAIL
