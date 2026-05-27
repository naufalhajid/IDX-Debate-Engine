"""Tests for services/fair_value_calculator.py audit fixes."""

from __future__ import annotations

import pytest

from services.fair_value_calculator import (
    FairValueCalculator,
    KeyStats,
    build_fair_value_report,
    extract_keystats,
)


def _stockbit_response(fields: list[tuple[str, str]]) -> dict:
    return {
        "data": {
            "closure_fin_items_results": [
                {
                    "fin_name_results": [
                        {"fitem": {"name": name, "value": value}}
                        for name, value in fields
                    ]
                }
            ]
        }
    }


def test_goto_zero_pe_multiple_disables_pe_method(monkeypatch):
    captured: dict[str, KeyStats] = {}
    original_init = FairValueCalculator.__init__

    def capturing_init(self, stats, sector=None):
        captured["stats"] = stats
        original_init(self, stats, sector)

    monkeypatch.setattr(FairValueCalculator, "__init__", capturing_init)

    api_response = _stockbit_response(
        [
            ("Current EPS (TTM)", "10"),
            ("Book Value Per Share", "100"),
        ]
    )

    build_fair_value_report(api_response, "GOTO", 100.0)

    stats = captured["stats"]
    assert stats.historical_pe_avg == 0.0
    assert FairValueCalculator(stats).fair_value_pe() is None


def test_weighted_average_includes_zero_method_result(monkeypatch):
    calc = FairValueCalculator(KeyStats(ticker="ZERO", current_price=100.0))

    monkeypatch.setattr(calc, "fair_value_pe", lambda: 0.0)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: None)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: None)

    result = calc.fair_value_weighted()

    assert result["fair_value"] == 0.0
    assert result["breakdown"] == {"pe": 0}
    assert result["confidence"] == "LOW"


def test_build_fair_value_report_empty_api_returns_none():
    report, fair_value = build_fair_value_report({}, "NODATA", 100.0)

    assert isinstance(report, str)
    assert fair_value is None


def test_build_fair_value_report_calls_weighted_once(monkeypatch):
    calls = 0
    original = FairValueCalculator.fair_value_weighted

    def counted_weighted(self):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(FairValueCalculator, "fair_value_weighted", counted_weighted)

    build_fair_value_report({}, "NODATA", 100.0)

    assert calls == 1


def test_extract_keystats_partial_match_prefers_shortest_key():
    api_response = _stockbit_response(
        [
            ("Current EPS (TTM)", "1"),
            ("Return on Equity per Share Adjusted", "99%"),
            ("Return on Equity Latest", "15%"),
        ]
    )

    stats = extract_keystats(api_response, "TEST")

    assert stats.roe == pytest.approx(0.15)


def test_extract_keystats_distinguishes_missing_dps_from_explicit_zero():
    missing_dps = extract_keystats(
        _stockbit_response(
            [
                ("Current EPS (TTM)", "10"),
                ("Book Value Per Share", "100"),
            ]
        ),
        "MISS",
    )
    zero_dps = extract_keystats(
        _stockbit_response(
            [
                ("Current EPS (TTM)", "10"),
                ("Book Value Per Share", "100"),
                ("DPS", "0"),
            ]
        ),
        "ZERO",
    )

    assert missing_dps.dps is None
    assert zero_dps.dps == 0.0
    assert FairValueCalculator(missing_dps).fair_value_ddm() is None
    assert FairValueCalculator(zero_dps).fair_value_ddm() is None


def test_sector_weight_assertion(monkeypatch):
    monkeypatch.setitem(
        FairValueCalculator.SECTOR_WEIGHTS,
        "broken",
        {"pe": 0.5, "pb": 0.5, "ddm": 0.5},
    )

    with pytest.raises(AssertionError, match="tidak menjumlah 1.0"):
        FairValueCalculator(KeyStats(ticker="TEST"), sector="broken")
