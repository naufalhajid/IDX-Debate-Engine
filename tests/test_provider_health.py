import pytest

from core import provider_health


class HealthyStockbitClient:
    def get(self, url: str) -> dict:
        assert "orderbook/companies/BBCA" in url
        return {"data": {"lastprice": 9000}}


class DownStockbitClient:
    def get(self, url: str) -> dict:
        raise ConnectionError("Failed to resolve 'exodus.stockbit.com'")


class HealthyTicker:
    def __init__(self, symbol: str) -> None:
        assert symbol == provider_health.YFINANCE_HEALTH_TICKER

    @property
    def fast_info(self) -> dict:
        return {"last_price": 9000}


class DownTicker:
    def __init__(self, symbol: str) -> None:
        assert symbol == provider_health.YFINANCE_HEALTH_TICKER

    @property
    def fast_info(self) -> dict:
        raise TimeoutError("yfinance timed out")


@pytest.mark.asyncio
async def test_check_all_providers_both_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_health, "StockbitApiClient", HealthyStockbitClient)
    monkeypatch.setattr(provider_health.yf, "Ticker", HealthyTicker)

    report = await provider_health.check_all_providers(["BBCA"])

    assert report.stockbit_ok is True
    assert report.yfinance_ok is True
    assert report.failures == []
    assert report.can_proceed is True


@pytest.mark.asyncio
async def test_check_all_providers_stockbit_down_yfinance_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_health, "StockbitApiClient", DownStockbitClient)
    monkeypatch.setattr(provider_health.yf, "Ticker", HealthyTicker)

    report = await provider_health.check_all_providers(["BBCA"])

    assert report.stockbit_ok is False
    assert report.yfinance_ok is True
    assert report.can_proceed is True
    assert len(report.failures) == 1
    assert "stockbit:DNS:" in report.failures[0]


@pytest.mark.asyncio
async def test_check_all_providers_both_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_health, "StockbitApiClient", DownStockbitClient)
    monkeypatch.setattr(provider_health.yf, "Ticker", DownTicker)

    report = await provider_health.check_all_providers(["BBCA"])

    assert report.stockbit_ok is False
    assert report.yfinance_ok is False
    assert report.can_proceed is False
    assert len(report.failures) == 2
    assert any("stockbit:DNS:" in failure for failure in report.failures)
    assert any("yfinance:TIMEOUT:" in failure for failure in report.failures)
