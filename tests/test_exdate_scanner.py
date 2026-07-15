from __future__ import annotations

from types import SimpleNamespace

from utils import exdate_scanner


def test_scan_exdate_rejects_invalid_ticker_before_yfinance(monkeypatch) -> None:
    def fail_provider_creation():
        raise AssertionError("invalid ticker reached yfinance provider creation")

    monkeypatch.setattr(exdate_scanner, "_get_yfinance", fail_provider_creation)

    result = exdate_scanner.scan_exdate("../escape")

    assert result["source"] == "unavailable"
    assert result["has_upcoming_exdate"] is False


def test_scan_exdate_normalizes_yfinance_symbol(monkeypatch) -> None:
    calls: list[str] = []

    def ticker_factory(symbol: str):
        calls.append(symbol)
        return SimpleNamespace(calendar=None)

    monkeypatch.setattr(
        exdate_scanner,
        "_get_yfinance",
        lambda: SimpleNamespace(Ticker=ticker_factory),
    )

    result = exdate_scanner.scan_exdate(" bbca.jk ")

    assert calls == ["BBCA.JK"]
    assert result["source"] == "yfinance"
