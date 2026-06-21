from unittest.mock import Mock

import requests

from services.stockbit_api_client import StockbitApiClient


class _FakeResponse:
    def __init__(self, status_code: int, body=None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.get_calls = 0

    def get(self, url: str, **kwargs):
        self.get_calls += 1
        return self.responses.pop(0)


def _client_with_session(session: _FakeSession) -> StockbitApiClient:
    client = object.__new__(StockbitApiClient)
    client.headers = {}
    client._get_session = Mock(return_value=session)
    client._authenticate_stockbit = Mock()
    return client


def test_operation_from_url_labels_optional_stockbit_enrichment() -> None:
    assert (
        StockbitApiClient._operation_from_url(
            "https://exodus.stockbit.com/broker-summary/v1/MYOR",
            "GET",
        )
        == "broker summary"
    )
    assert (
        StockbitApiClient._operation_from_url(
            "https://exodus.stockbit.com/order-trade/broker/distribution?symbol=MYOR",
            "GET",
        )
        == "broker summary"
    )
    assert (
        StockbitApiClient._operation_from_url(
            "https://exodus.stockbit.com/findata-view/foreign-domestic/v1/chart-data/MYOR",
            "GET",
        )
        == "foreign flow"
    )


def test_404_unrecognized_command_returns_empty_without_auth_retry(monkeypatch) -> None:
    session = _FakeSession(
        [
            _FakeResponse(
                404,
                {"message": "Unrecognized Command"},
                '{"message":"Unrecognized Command"}',
            )
        ]
    )
    client = _client_with_session(session)
    fake_logger = Mock()
    monkeypatch.setattr("services.stockbit_api_client.logger", fake_logger)

    result = client.get("https://exodus.stockbit.com/broker-summary/v1/MYOR")

    assert result == {}
    assert session.get_calls == 1
    client._authenticate_stockbit.assert_not_called()
    fake_logger.warning.assert_not_called()
    fake_logger.error.assert_not_called()


def test_401_refreshes_token_and_retries_successfully(monkeypatch) -> None:
    monkeypatch.setattr("services.stockbit_api_client.time.sleep", lambda *_: None)
    session = _FakeSession(
        [
            _FakeResponse(401, {"message": "Unauthorized"}, "Unauthorized"),
            _FakeResponse(200, {"data": {"lastprice": 100}}, '{"data":{}}'),
        ]
    )
    client = _client_with_session(session)

    result = client.get(
        "https://exodus.stockbit.com/company-price-feed/v2/orderbook/companies/BBCA"
    )

    assert result == {"data": {"lastprice": 100}}
    assert session.get_calls == 2
    client._authenticate_stockbit.assert_called_once()


def test_non_json_success_returns_empty_dict() -> None:
    session = _FakeSession([_FakeResponse(200, ValueError("not json"), "ok")])
    client = _client_with_session(session)

    result = client.get("https://exodus.stockbit.com/custom")

    assert result == {}


def test_request_exception_returns_empty_dict() -> None:
    class FailingSession:
        def get(self, url: str, **kwargs):
            raise requests.exceptions.ConnectTimeout("timeout")

    client = _client_with_session(FailingSession())

    result = client.get("https://exodus.stockbit.com/custom")

    assert result == {}
