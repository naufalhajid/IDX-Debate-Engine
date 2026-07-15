from unittest.mock import MagicMock

import pytest

from providers.idx_broker_summary import (
    _broker_distribution_url,
    _empty,
    _normalize_ticker,
    fetch_broker_summary,
)


_BROKER_DISTRIBUTION_RESPONSE = {
    "message": "OK",
    "data": {
        "by_value": {
            "top_broker_buy": [
                {
                    "detail": {
                        "code": "MG",
                        "name": "Semesta Indovest Sekuritas",
                        "amount": 2_500_000_000,
                        "lot": 120_000,
                    },
                },
                {
                    "broker": {"code": "SS", "name": "Supra Sekuritas"},
                    "total_value": 1_500_000_000,
                    "total_lot": 75_000,
                },
            ],
            "top_broker_sell": [
                {
                    "detail": {
                        "code": "XL",
                        "name": "Stockbit Sekuritas",
                        "amount": 1_000_000_000,
                        "lot": 50_000,
                    },
                },
            ],
        }
    },
}


def _mock_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.get.return_value = response
    return client


def test_broker_distribution_url_uses_stockbit_order_trade_endpoint() -> None:
    url = _broker_distribution_url("DSSA")

    assert url.startswith(
        "https://exodus.stockbit.com/order-trade/broker/distribution?"
    )
    assert "symbol=DSSA" in url
    assert "investor_type=INVESTOR_TYPE_ALL" in url
    assert "market_board=MARKET_TYPE_REGULER" in url
    assert "data_type=BROKER_DISTRIBUTION_DATA_TYPE_VALUE" in url
    assert "period=TB_PERIOD_LAST_1_DAY" in url
    assert "/broker-summary/v1/" not in url


def test_fetch_broker_summary_parses_by_value_distribution() -> None:
    client = _mock_client(_BROKER_DISTRIBUTION_RESPONSE)

    snap = fetch_broker_summary("DSSA", client)

    requested_url = client.get.call_args.args[0]
    assert "/order-trade/broker/distribution?" in requested_url
    assert client.get.call_args.kwargs == {
        "operation": "broker summary",
        "optional": True,
    }
    assert snap.ticker == "DSSA"
    assert snap.top_buyer_codes == "MG, SS"
    assert snap.top_seller_codes == "XL"
    assert snap.net_broker_buy_m == pytest.approx(3000.0)
    assert snap.is_accumulation is True
    assert snap.top_buyers[0] == {
        "code": "MG",
        "name": "Semesta Indovest Sekuritas",
        "lot": 120000.0,
        "value_m": 2500.0,
    }


def test_fetch_broker_summary_keeps_legacy_shape_fallback() -> None:
    client = _mock_client(
        {
            "data": {
                "buy": [{"code": "YP", "name": "Mirae", "value": 500_000_000}],
                "sell": [{"code": "AK", "name": "UBS", "value": 250_000_000}],
            }
        }
    )

    snap = fetch_broker_summary("BBCA.JK", client)

    assert snap.ticker == "BBCA"
    assert snap.top_buyer_codes == "YP"
    assert snap.top_seller_codes == "AK"
    assert snap.net_broker_buy_m == pytest.approx(250.0)


def test_fetch_broker_summary_empty_response_returns_empty() -> None:
    client = _mock_client({})

    snap = fetch_broker_summary("TLKM", client)

    assert snap == _empty("TLKM")


def test_normalize_ticker_strips_jk_suffix() -> None:
    assert _normalize_ticker(" dssa.jk ") == "DSSA"
    assert _normalize_ticker(None) == ""


def test_fetch_broker_summary_rejects_invalid_ticker_before_client_call() -> None:
    client = MagicMock()

    snap = fetch_broker_summary("../escape", client)

    assert snap == _empty("")
    client.get.assert_not_called()
