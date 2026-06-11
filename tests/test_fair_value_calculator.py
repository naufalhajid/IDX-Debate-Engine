"""Tests for services/fair_value_calculator.py audit fixes."""

from __future__ import annotations

import pytest

import services.fair_value_calculator as fvc
from services.fair_value_calculator import (
    FairValueCalculator,
    KeyStats,
    build_fair_value_payload,
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

    def capturing_init(self, stats, sector=None, **kwargs):
        captured["stats"] = stats
        original_init(self, stats, sector, **kwargs)

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
    monkeypatch.setitem(
        FairValueCalculator.SECTOR_PROFILE_ALIAS,
        "broken",
        "broken",
    )

    with pytest.raises(AssertionError, match="tidak menjumlah 1.0"):
        FairValueCalculator(KeyStats(ticker="TEST"), sector="broken")


def _calculator_with_methods(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current_price: float,
    pe: float | None = None,
    pb: float | None = None,
    ddm: float | None = None,
) -> FairValueCalculator:
    calc = FairValueCalculator(
        KeyStats(ticker="TEST", current_price=current_price),
        sector="default",
    )
    monkeypatch.setattr(calc, "fair_value_pe", lambda: pe)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: pb)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: ddm)
    return calc


def test_fair_value_range_uses_10pct_for_three_valid_methods(monkeypatch):
    result = _calculator_with_methods(
        monkeypatch,
        current_price=100,
        pe=100,
        pb=100,
        ddm=100,
    ).fair_value_weighted()

    assert result["fair_value"] == 100
    assert result["fair_value_base"] == 100
    assert result["fair_value_low"] == 90
    assert result["fair_value_high"] == 110
    assert result["range_pct"] == pytest.approx(0.10)
    assert result["confidence"] == "HIGH"


def test_fair_value_range_uses_15pct_for_two_valid_methods(monkeypatch):
    result = _calculator_with_methods(
        monkeypatch,
        current_price=100,
        pe=100,
        pb=100,
        ddm=None,
    ).fair_value_weighted()

    assert result["fair_value_low"] == 85
    assert result["fair_value_high"] == 115
    assert result["range_pct"] == pytest.approx(0.15)
    assert result["confidence"] == "MEDIUM"


def test_fair_value_range_uses_25pct_for_one_valid_method(monkeypatch):
    result = _calculator_with_methods(
        monkeypatch,
        current_price=100,
        pe=100,
        pb=None,
        ddm=None,
    ).fair_value_weighted()

    assert result["fair_value_low"] == 75
    assert result["fair_value_high"] == 125
    assert result["range_pct"] == pytest.approx(0.25)
    assert result["confidence"] == "LOW"


def test_fair_value_no_data_has_no_range(monkeypatch):
    result = _calculator_with_methods(
        monkeypatch,
        current_price=100,
        pe=None,
        pb=None,
        ddm=None,
    ).fair_value_weighted()

    assert result["fair_value"] is None
    assert result["fair_value_base"] is None
    assert result["fair_value_low"] is None
    assert result["fair_value_high"] is None
    assert result["range_pct"] is None
    assert result["risk_overvalued"] is False
    assert result["valuation_verdict"] == "DATA_UNAVAILABLE"


def test_price_equal_fair_value_high_is_not_risk_overvalued(monkeypatch):
    result = _calculator_with_methods(
        monkeypatch,
        current_price=115,
        pe=100,
        pb=100,
        ddm=None,
    ).fair_value_weighted()

    assert result["fair_value_high"] == 115
    assert result["risk_overvalued"] is False
    assert result["valuation_verdict"] == "SLIGHTLY_OVERVALUED"


def test_price_above_fair_value_high_is_risk_overvalued(monkeypatch):
    result = _calculator_with_methods(
        monkeypatch,
        current_price=116,
        pe=100,
        pb=100,
        ddm=None,
    ).fair_value_weighted()

    assert result["risk_overvalued"] is True
    assert result["valuation_verdict"] == "OVERVALUED"


def test_price_above_base_inside_range_is_soft_overvalued(monkeypatch):
    result = _calculator_with_methods(
        monkeypatch,
        current_price=108,
        pe=100,
        pb=100,
        ddm=None,
    ).fair_value_weighted()

    assert result["valuation_verdict"] == "SLIGHTLY_OVERVALUED"
    assert result["risk_overvalued"] is False


def test_sector_cache_normalizes_ticker_and_only_changes_weights(
    monkeypatch,
    tmp_path,
):
    cache_path = tmp_path / "sector_cache.json"
    cache_path.write_text('{"bbca.jk": {"sector": "energy"}}', encoding="utf-8")
    monkeypatch.setattr(fvc, "SECTOR_CACHE_PATH", cache_path)
    fvc._load_sector_cache.cache_clear()

    calc = FairValueCalculator(KeyStats(ticker="BBCA.JK"))

    assert calc.raw_sector == "energy"
    assert calc.sector == "mining"
    assert calc.weights == FairValueCalculator.SECTOR_WEIGHTS["mining"]
    assert fvc.get_historical_multiples("bbca.jk") == fvc.HISTORICAL_MULTIPLES["BBCA"]

    fvc._load_sector_cache.cache_clear()


def test_build_fair_value_payload_exposes_range_fields():
    api_response = _stockbit_response(
        [
            ("Current EPS (TTM)", "10"),
            ("Book Value Per Share", "100"),
        ]
    )

    report, payload = build_fair_value_payload(api_response, "TEST", 108.0)

    assert "FAIR VALUE BASE" in report
    assert "FAIR VALUE RANGE" in report
    assert payload["fair_value"] == payload["fair_value_base"]
    assert "fair_value_low" in payload
    assert "fair_value_high" in payload
    assert "risk_overvalued" in payload
