from datetime import datetime

import pytest

from core.failure_taxonomy import ErrorCode, FailureRecord, classify_exception


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
    result = classify_exception(TimeoutError("debate timed out"), source="debate_chamber")

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
