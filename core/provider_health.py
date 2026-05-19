"""Provider health checks for pre-debate price data availability."""

from __future__ import annotations

import asyncio
import sys

import yfinance as yf
from pydantic import BaseModel, ConfigDict

from core.failure_taxonomy import FailureRecord, classify_exception
from services.stockbit_api_client import StockbitApiClient


DEFAULT_TICKER = "BBCA"
STOCKBIT_BASE_URL = "https://exodus.stockbit.com"
YFINANCE_HEALTH_TICKER = "BBCA.JK"


class ProviderHealthReport(BaseModel):
    """Aggregated health status for price providers needed before debates."""

    model_config = ConfigDict(extra="forbid")

    stockbit_ok: bool
    yfinance_ok: bool
    failures: list[str]
    can_proceed: bool


async def check_all_providers(tickers: list[str]) -> ProviderHealthReport:
    """Check Stockbit and yfinance without raising exceptions to callers."""
    stockbit_status, yfinance_status = await asyncio.gather(
        _check_stockbit(tickers),
        _check_yfinance(),
    )

    stockbit_ok, stockbit_failure = stockbit_status
    yfinance_ok, yfinance_failure = yfinance_status
    failures = [
        failure
        for failure in (stockbit_failure, yfinance_failure)
        if failure is not None
    ]

    return ProviderHealthReport(
        stockbit_ok=stockbit_ok,
        yfinance_ok=yfinance_ok,
        failures=failures,
        can_proceed=stockbit_ok or yfinance_ok,
    )


async def _check_stockbit(tickers: list[str]) -> tuple[bool, str | None]:
    ticker = _select_stockbit_ticker(tickers)
    try:
        await asyncio.to_thread(_ping_stockbit, ticker)
    except Exception as exc:
        return False, _format_failure(classify_exception(exc, source="stockbit"))
    return True, None


async def _check_yfinance() -> tuple[bool, str | None]:
    try:
        await asyncio.to_thread(_ping_yfinance)
    except Exception as exc:
        return False, _format_failure(classify_exception(exc, source="yfinance"))
    return True, None


def _ping_stockbit(ticker: str) -> None:
    client = StockbitApiClient()
    url = f"{STOCKBIT_BASE_URL}/company-price-feed/v2/orderbook/companies/{ticker}"
    response = client.get(url)
    if not isinstance(response, dict) or not response.get("data"):
        raise ValueError(f"No price data from Stockbit for {ticker}")


def _ping_yfinance() -> None:
    fast_info = yf.Ticker(YFINANCE_HEALTH_TICKER).fast_info
    if fast_info is None:
        raise ValueError(f"No price data from yfinance for {YFINANCE_HEALTH_TICKER}")


def _select_stockbit_ticker(tickers: list[str]) -> str:
    for ticker in tickers:
        normalized = str(ticker or "").strip().upper()
        if normalized:
            return normalized.removesuffix(".JK")
    return DEFAULT_TICKER


def _format_failure(failure: FailureRecord) -> str:
    return f"{failure.source}:{failure.code.value}: {failure.message}"


async def _main(argv: list[str] | None = None) -> None:
    tickers = argv if argv is not None else sys.argv[1:]
    report = await check_all_providers(tickers or [DEFAULT_TICKER])
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
