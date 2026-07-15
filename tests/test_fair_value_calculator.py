"""Tests for services/fair_value_calculator.py audit fixes."""

from __future__ import annotations

import pytest

import services.fair_value_calculator as fvc
from services.fair_value_calculator import (
    FairValueCalculator,
    KeyStats,
    _SECTOR_MEDIAN_PROFILES_DEFAULT,
    _compute_valuation_band_context,
    _load_dynamic_sector_benchmarks,
    build_fair_value_payload,
    build_fair_value_report,
    compute_52w_range_signal,
    extract_historical_multiples,
    extract_keystats,
    refresh_sector_benchmarks,
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


def test_current_price_lookup_does_not_partial_match_price_to_book_ratio():
    """FIX 2: 'Current Price' must never partial-match 'Current Price to Book
    Value'. The DPS-from-yield fallback (extract_keystats, ~line 781) looks
    up ["Last Price", "Current Price", "Close Price"] with allow_partial=False
    specifically to prevent this — a flat dict that has the PB ratio field
    but no exact price field must leave the price unresolved (DPS stays
    None), not silently substitute the PB ratio as if it were a price.
    """
    api_response = _stockbit_response(
        [
            # EPS/BVPS present so Strategy A is treated as authoritative and
            # the legacy key-value fallback (which would independently
            # re-resolve raw_pb_current via different field names) never
            # fires — see extract_keystats' "found nothing useful" check.
            ("Current EPS (TTM)", "10"),
            ("Book Value Per Share", "100"),
            ("Current Price to Book Value", "2.5"),
            ("Dividend Yield (TTM)", "5.0"),
        ]
    )

    # current_price intentionally omitted (None) so the vulnerable fallback
    # path — which only runs when the caller didn't already supply a price —
    # actually executes instead of being short-circuited.
    stats = extract_keystats(api_response, "TEST", current_price=None)

    assert stats.raw_pb_current == 2.5
    assert stats.dps is None
    assert stats.dps_price_used is None


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
    assert missing_dps.dps_source is None
    assert zero_dps.dps == 0.0
    assert zero_dps.dps_source == "stockbit_direct"
    assert FairValueCalculator(missing_dps).fair_value_ddm() is None
    assert FairValueCalculator(zero_dps).fair_value_ddm() is None


def test_build_payload_derives_missing_dps_from_market_price_not_pb_ratio(
    monkeypatch,
):
    """Freeze the BMRI-shaped defect reproduced by the 2026-07-12 live run."""
    captured: dict[str, KeyStats] = {}
    original_init = FairValueCalculator.__init__

    def capturing_init(self, stats, *args, **kwargs):
        captured["stats"] = stats
        original_init(self, stats, *args, **kwargs)

    monkeypatch.setattr(FairValueCalculator, "__init__", capturing_init)

    api_response = _stockbit_response(
        [
            ("Current EPS (TTM)", "10"),
            ("Book Value Per Share", "100"),
            ("Dividend Yield (TTM)", "11.69"),
            ("Current Price to Book Value", "1.25"),
        ]
    )

    report, result = build_fair_value_payload(api_response, "BMRI", 4_080.0)

    stats = captured["stats"]
    assert stats.current_price == 4_080.0
    assert stats.raw_pb_current == pytest.approx(1.25)
    assert stats.dps == pytest.approx(476.95)
    assert stats.dps_source == "yield_x_market_price"
    assert stats.dps_yield_pct == pytest.approx(11.69)
    assert stats.dps_price_used == pytest.approx(4_080.0)
    assert result["dps_source"] == "yield_x_market_price"
    assert "DPS Source" in report
    assert "11.69% x Rp 4,080" in report


def test_fractional_dividend_yield_is_still_percentage_point_data():
    stats = extract_keystats(
        _stockbit_response(
            [
                ("Dividend Yield (TTM)", "0.50"),
                ("Current Price to Book Value", "2.00"),
            ]
        ),
        ticker="TEST",
        current_price=10_000.0,
    )

    assert stats.current_price == 10_000.0
    assert stats.dps == pytest.approx(50.0)
    assert stats.dps_source == "yield_x_market_price"


def test_missing_market_price_does_not_fall_back_to_pb_ratio_for_dps():
    stats = extract_keystats(
        _stockbit_response(
            [
                ("Current EPS (TTM)", "10"),
                ("Dividend Yield (TTM)", "11.69"),
                ("Current Price to Book Value", "1.25"),
            ]
        ),
        ticker="TEST",
    )

    assert stats.raw_pb_current == pytest.approx(1.25)
    assert stats.current_price == 0.0
    assert stats.dps is None
    assert stats.dps_source is None


def test_explicit_invalid_market_price_does_not_use_api_price_fallback():
    stats = extract_keystats(
        _stockbit_response(
            [
                ("Dividend Yield (TTM)", "11.69"),
                ("Last Price", "4080"),
            ]
        ),
        ticker="TEST",
        current_price=0.0,
    )

    assert stats.current_price == 0.0
    assert stats.dps is None
    assert stats.dps_source is None


def test_exact_case_insensitive_api_price_is_used_only_when_price_omitted():
    stats = extract_keystats(
        _stockbit_response(
            [
                ("Dividend Yield (TTM)", "11.69"),
                ("current price", "4080"),
            ]
        ),
        ticker="TEST",
    )

    assert stats.current_price == 0.0
    assert stats.dps == pytest.approx(476.95)
    assert stats.dps_source == "yield_x_market_price"
    assert stats.dps_price_used == pytest.approx(4_080.0)


@pytest.mark.parametrize(
    ("ticker", "yield_pct", "price", "expected_dps"),
    [
        ("BBCA", "4.87", 6_175.0, 300.72),
        ("BMRI", "11.69", 4_080.0, 476.95),
        ("LSIP", "6.41", 1_295.0, 83.01),
    ],
)
def test_idx_live_regression_dps_values(
    ticker,
    yield_pct,
    price,
    expected_dps,
):
    stats = extract_keystats(
        _stockbit_response(
            [
                ("Current EPS (TTM)", "10"),
                ("Dividend Yield (TTM)", yield_pct),
                ("Current Price to Book Value", "1.25"),
            ]
        ),
        ticker=ticker,
        current_price=price,
    )

    assert stats.dps == pytest.approx(expected_dps, abs=0.01)
    assert stats.dps_source == "yield_x_market_price"
    assert stats.dps_price_used == pytest.approx(price)


def test_direct_dps_takes_precedence_over_dividend_yield():
    stats = extract_keystats(
        _stockbit_response(
            [
                ("Current EPS (TTM)", "10"),
                ("Dividend Per Share (TTM)", "325"),
                ("Dividend Yield (TTM)", "11.69"),
                ("Current Price to Book Value", "1.25"),
            ]
        ),
        ticker="BBCA",
        current_price=6_175.0,
    )

    assert stats.dps == pytest.approx(325.0)
    assert stats.dps_source == "stockbit_direct"
    assert stats.dps_yield_pct is None
    assert stats.dps_price_used is None


def test_dps_growth_field_does_not_masquerade_as_direct_dps():
    stats = extract_keystats(
        _stockbit_response(
            [
                ("Current EPS (TTM)", "10"),
                ("DPS Growth", "10"),
                ("Dividend Yield (TTM)", "5"),
            ]
        ),
        ticker="TEST",
        current_price=1_000.0,
    )

    assert stats.dps == pytest.approx(50.0)
    assert stats.dps_source == "yield_x_market_price"
    assert stats.dps_yield_pct == pytest.approx(5.0)
    assert stats.dps_price_used == pytest.approx(1_000.0)


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
    sector: str = "default",
) -> FairValueCalculator:
    calc = FairValueCalculator(
        KeyStats(ticker="TEST", current_price=current_price),
        sector=sector,
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
        sector="bank",
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
    result = fvc.get_historical_multiples("bbca.jk")
    expected = fvc.HISTORICAL_MULTIPLES["BBCA"]
    assert result["pe"] == expected["pe"]
    assert result["pb"] == expected["pb"]
    assert result["growth_rate"] == expected["growth_rate"]
    assert "cost_of_equity" in result
    assert "beta" not in result

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


def test_build_fair_value_payload_exposes_idx_factor_signals():
    api_response = _stockbit_response(
        [
            ("Current EPS (TTM)", "10"),
            ("Book Value Per Share", "100"),
            ("Cash From Operations (TTM)", "120000"),
            ("Current Share Outstanding", "10000"),
            ("Return on Assets (TTM)", "12%"),
        ]
    )

    report, payload = build_fair_value_payload(api_response, "TEST", 100.0)

    assert payload["ocf_per_share"] == pytest.approx(12.0)
    assert payload["ocf_price_ratio"] == pytest.approx(0.12)
    assert payload["roa"] == pytest.approx(0.12)
    assert payload["profitability_proxy"] == pytest.approx(0.12)
    assert payload["profitability_proxy_source"] == "roa"
    assert payload["profitability_factor_score"] == pytest.approx(0.70)
    assert "OCF/Price" in report
    assert "RNOA/ROA Proxy" in report


def _patch_methods(monkeypatch, pe=None, pb=None, ddm=None):
    monkeypatch.setattr(FairValueCalculator, "fair_value_pe", lambda self: pe)
    monkeypatch.setattr(FairValueCalculator, "fair_value_pb", lambda self: pb)
    monkeypatch.setattr(FairValueCalculator, "fair_value_ddm", lambda self: ddm)


def _quality_gate_response(margin: str) -> dict:
    # EPS + BVPS keep extract_keystats on Strategy A so the margin field sticks
    # (Strategy B legacy fallback would clobber net_margin back to 0.0).
    return _stockbit_response(
        [
            ("Current EPS (TTM)", "10"),
            ("Book Value Per Share", "100"),
            ("Net Profit Margin (TTM)", margin),
        ]
    )


def test_quality_gate_rejects_single_method_fair_value(monkeypatch):
    # NZIA 2026-06-11: only 1/3 methods valid, yet FV Rp 417 vs spot Rp 177
    # became the headline BUY catalyst.
    _patch_methods(monkeypatch, pe=417.0)

    report, result = build_fair_value_payload(
        _quality_gate_response("12%"), "NZIA", 177.0
    )

    assert result["fair_value"] is None
    assert result["fair_value_high"] is None
    assert result["risk_overvalued"] is False
    assert result["fv_quality_rejected"] is True
    assert result["fv_quality_reasons"] == ["fv_methods_lt_2"]
    assert result["valuation_verdict"] == "QUALITY_REJECTED"
    assert "FAIR VALUE QUALITY GATE" in report


def test_quality_gate_rejects_margin_above_100_percent(monkeypatch):
    # INDO 2026-06-11: net margin 131.07% (net income > revenue) — broken data
    # that the debate could only label NEEDS_RECONCILIATION in prose.
    _patch_methods(monkeypatch, pe=250.0, pb=260.0)

    report, result = build_fair_value_payload(
        _quality_gate_response("131.07%"), "INDO", 165.0
    )

    assert result["fair_value"] is None
    assert result["fv_quality_rejected"] is True
    assert result["fv_quality_reasons"] == ["net_margin_gt_100pct"]
    assert "FAIR VALUE QUALITY GATE" in report


def test_quality_gate_passes_two_methods_with_sane_margin(monkeypatch):
    _patch_methods(monkeypatch, pe=250.0, pb=260.0)

    report, result = build_fair_value_payload(
        _quality_gate_response("12%"), "BBCA", 200.0
    )

    assert result["fair_value"] is not None
    assert result["confidence"] == "MEDIUM"
    assert "fv_quality_rejected" not in result
    assert "FAIR VALUE QUALITY GATE" not in report


# ---------------------------------------------------------------------------
# FIX 3A: EV/EBITDA proper bridge — EBITDA x target multiple -> Enterprise
# Value -> subtract Net Debt -> Equity Value -> / shares. Replaces the old
# price x (target/current multiple) shortcut (former "Task 24" tests below).
# ---------------------------------------------------------------------------

def _mining_calc(
    *,
    ebitda_ttm: float | None = 1_000_000_000_000.0,
    net_debt: float | None = 200_000_000_000.0,
    shares_outstanding: float = 10_000_000_000.0,
    price: float = 500.0,
) -> FairValueCalculator:
    stats = KeyStats(
        ticker="ADRO",
        current_price=price,
        ebitda_ttm=ebitda_ttm,
        net_debt=net_debt,
        shares_outstanding=shares_outstanding,
    )
    return FairValueCalculator(stats, sector="mining")


def test_fair_value_ev_ebitda_returns_none_for_non_mining():
    stats = KeyStats(
        ticker="BBCA",
        current_price=9000,
        ebitda_ttm=1_000_000_000_000.0,
        net_debt=0.0,
        shares_outstanding=10_000_000_000.0,
    )
    calc = FairValueCalculator(stats, sector="bank")
    assert calc.fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_matches_hand_calculated_bridge():
    # EBITDA 1e12 x target 5.5 = EV 5.5e12; equity = 5.5e12 - net_debt 2e11
    # = 5.3e12; / shares 1e10 = 530. NOT the old shortcut's price-ratio value.
    calc = _mining_calc()
    assert calc.fair_value_ev_ebitda() == pytest.approx(530.0)


def test_fair_value_ev_ebitda_missing_ebitda_returns_none():
    assert _mining_calc(ebitda_ttm=None).fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_negative_ebitda_excludes_method():
    # Method not applicable for loss-making EBITDA -- not a negative FV.
    assert _mining_calc(ebitda_ttm=-1_000_000_000.0).fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_zero_ebitda_excludes_method():
    assert _mining_calc(ebitda_ttm=0.0).fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_missing_net_debt_returns_none_not_zero():
    """Unknown net debt must exclude the method, never silently assume
    debt-free (which would inflate equity value)."""
    assert _mining_calc(net_debt=None).fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_explicit_zero_net_debt_is_valid():
    """Net debt of exactly 0.0 (net-cash company) is a real, known value —
    distinct from None (unknown) — and must NOT be excluded."""
    # EBITDA 1e12 x 5.5 = 5.5e12; equity = 5.5e12 - 0 = 5.5e12; / 1e10 = 550.
    calc = _mining_calc(net_debt=0.0)
    assert calc.fair_value_ev_ebitda() == pytest.approx(550.0)


def test_fair_value_ev_ebitda_debt_exceeding_enterprise_value_returns_none():
    # EV 5.5e12 - net_debt 6e12 = negative equity value -> not applicable.
    calc = _mining_calc(net_debt=6_000_000_000_000.0)
    assert calc.fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_missing_shares_returns_none():
    assert _mining_calc(shares_outstanding=0.0).fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_rejects_outlier_high():
    # fv 530 vs price 100 -> ratio 5.3x > 3.0x sanity band -> rejected.
    assert _mining_calc(price=100.0).fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_rejects_outlier_low():
    # fv 530 vs price 5000 -> ratio 0.106x < 0.3x sanity band -> rejected.
    assert _mining_calc(price=5000.0).fair_value_ev_ebitda() is None


def test_extract_keystats_parses_ev_ebitda():
    api_response = _stockbit_response(
        [
            ("Current EPS (TTM)", "100"),
            ("Book Value Per Share", "500"),
            ("EV to EBITDA (TTM)", "4.2"),
        ]
    )
    stats = extract_keystats(api_response, "ADRO")
    assert stats.ev_ebitda_current == pytest.approx(4.2)


def test_mining_weighted_includes_ev_ebitda(monkeypatch):
    # bridge fv 530 (price=1000 -> ratio 0.53, within sanity band). PE/PB
    # mocked close to 530 so this test isolates method-count-based confidence
    # from the (separately tested) cross-method dispersion mechanism.
    calc = _mining_calc(price=1000.0)
    monkeypatch.setattr(calc, "fair_value_pe", lambda: 550.0)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: 500.0)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: None)
    result = calc.fair_value_weighted()
    assert "ev_ebitda" in result["breakdown"]
    assert result["confidence"] == "HIGH"


def test_mining_weighted_four_methods_gives_high_confidence(monkeypatch):
    calc = _mining_calc(price=1000.0)
    monkeypatch.setattr(calc, "fair_value_pe", lambda: 550.0)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: 500.0)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: 520.0)
    result = calc.fair_value_weighted()
    assert len(result["breakdown"]) == 4
    assert result["confidence"] == "HIGH"


def test_mining_weights_sum_to_one():
    weights = FairValueCalculator.SECTOR_WEIGHTS["mining"]
    assert abs(sum(weights.values()) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Task 27: Sector Peer Comparison
# ---------------------------------------------------------------------------

def test_build_sector_comparison_shows_mining_medians():
    stats = KeyStats(
        ticker="ADRO",
        current_price=2000.0,
        raw_pe_current=8.2,
        raw_pb_current=1.1,
        roe=0.223,
        net_margin=0.185,
    )
    calc = FairValueCalculator(stats, sector="mining")
    # Inject static defaults so the assertion is not tied to live cache values.
    calc.sector_medians = _SECTOR_MEDIAN_PROFILES_DEFAULT
    text = calc.build_sector_comparison()
    assert "MINING" in text.upper()
    assert "8.2x" in text
    assert "7.0x" in text   # sector median PE for mining (static default)


def test_build_sector_comparison_in_report():
    api_response = _stockbit_response(
        [
            ("Current EPS (TTM)", "100"),
            ("Book Value Per Share", "500"),
            ("Return on Equity (TTM)", "22%"),
            ("Net Profit Margin (TTM)", "18%"),
        ]
    )
    report, _ = build_fair_value_payload(api_response, "ADRO", 1000.0)
    assert "SECTOR PEER CONTEXT" in report


# ---------------------------------------------------------------------------
# T1: WACC Calibration — cost_of_equity from CAPM
# ---------------------------------------------------------------------------

def test_capm_cost_of_equity_default_beta():
    """Ke = get_live_sbn_10y() + 1.0 × ERP."""
    from services.fair_value_calculator import _capm_cost_of_equity
    from services.macro_refresh import get_live_sbn_10y
    from core.settings import get_settings
    ke = _capm_cost_of_equity(beta=1.0)
    expected = get_live_sbn_10y() + 1.0 * get_settings().IDX_ERP
    assert abs(ke - expected) < 0.0001


def test_capm_cost_of_equity_banking_beta():
    """BBCA beta=0.85 → Ke = get_live_sbn_10y() + 0.85 × ERP."""
    from services.fair_value_calculator import _capm_cost_of_equity
    from services.macro_refresh import get_live_sbn_10y
    from core.settings import get_settings
    ke = _capm_cost_of_equity(beta=0.85)
    expected = get_live_sbn_10y() + 0.85 * get_settings().IDX_ERP
    assert abs(ke - expected) < 0.0001


def test_get_historical_multiples_returns_capm_coe_not_beta():
    """get_historical_multiples pops beta and injects cost_of_equity from CAPM."""
    from services.fair_value_calculator import get_historical_multiples, HISTORICAL_MULTIPLES
    from services.macro_refresh import get_live_sbn_10y
    from core.settings import get_settings
    result = get_historical_multiples("BBCA")
    assert "cost_of_equity" in result
    assert "beta" not in result
    expected_ke = get_live_sbn_10y() + HISTORICAL_MULTIPLES["BBCA"]["beta"] * get_settings().IDX_ERP
    assert abs(result["cost_of_equity"] - expected_ke) < 0.0001


def test_get_historical_multiples_does_not_mutate_module_dict():
    """Calling get_historical_multiples must not modify HISTORICAL_MULTIPLES."""
    from services.fair_value_calculator import get_historical_multiples, HISTORICAL_MULTIPLES
    before = dict(HISTORICAL_MULTIPLES["ADRO"])
    get_historical_multiples("ADRO")
    assert HISTORICAL_MULTIPLES["ADRO"] == before
    assert "beta" in HISTORICAL_MULTIPLES["ADRO"]


def test_keystats_default_cost_of_equity_uses_capm():
    """KeyStats() default CoE comes from CAPM, not hardcoded 10%."""
    from services.macro_refresh import get_live_sbn_10y
    from core.settings import get_settings
    stats = KeyStats()
    expected_ke = get_live_sbn_10y() + 1.0 * get_settings().IDX_ERP
    assert abs(stats.cost_of_equity - expected_ke) < 0.0001
    assert stats.cost_of_equity != pytest.approx(0.10)


# ---------------------------------------------------------------------------
# T2: ROE-vs-CoE Gate in fair_value_pb()
# ---------------------------------------------------------------------------

def test_fair_value_pb_capped_when_roe_below_ke():
    """ROE < ke → min(historical_pb, roe/ke) used; _pb_roe_capped=True."""
    stats = KeyStats(book_value_per_share=1000.0, roe=0.07, historical_pb_avg=1.5)
    stats.cost_of_equity = 0.15
    calc = FairValueCalculator(stats)
    fv = calc.fair_value_pb()
    justified_pb = 0.07 / 0.15  # 0.467
    assert fv == round(1000.0 * min(1.5, justified_pb), 0)
    assert calc._pb_roe_capped is True


def test_fair_value_pb_no_cap_when_roe_above_ke():
    """ROE > ke → historical_pb_avg used as normal; _pb_roe_capped=False."""
    stats = KeyStats(book_value_per_share=1000.0, roe=0.22, historical_pb_avg=4.5)
    stats.cost_of_equity = 0.15
    calc = FairValueCalculator(stats)
    fv = calc.fair_value_pb()
    assert fv == round(1000.0 * 4.5, 0)
    assert calc._pb_roe_capped is False


def test_fair_value_pb_no_cap_when_roe_missing():
    """roe=0 → gate not applied (missing data)."""
    stats = KeyStats(book_value_per_share=1000.0, roe=0.0, historical_pb_avg=1.5)
    stats.cost_of_equity = 0.15
    calc = FairValueCalculator(stats)
    fv = calc.fair_value_pb()
    assert fv == round(1000.0 * 1.5, 0)
    assert calc._pb_roe_capped is False


def test_fair_value_pb_cap_never_raises_multiple():
    """When historical_pb < roe/ke, min() picks historical (no uplift)."""
    # property: historical_pb_avg=0.7, roe=0.07, ke=0.08 → roe/ke=0.875
    # min(0.7, 0.875) = 0.7 → FV unchanged, but gate still fired
    stats = KeyStats(book_value_per_share=1000.0, roe=0.07, historical_pb_avg=0.7)
    stats.cost_of_equity = 0.08
    calc = FairValueCalculator(stats)
    fv = calc.fair_value_pb()
    assert fv == round(1000.0 * 0.7, 0)  # min doesn't raise the multiple
    assert calc._pb_roe_capped is True


def test_pb_roe_gate_shows_in_report():
    """build_report surfaces value trap gate when _pb_roe_capped is True."""
    stats = KeyStats(
        ticker="BBTN",
        current_price=1000.0,
        book_value_per_share=2000.0,
        roe=0.08,
        historical_pb_avg=1.2,
    )
    stats.cost_of_equity = 0.15
    calc = FairValueCalculator(stats)
    report = calc.build_report()
    assert "value trap gate" in report
    assert "ROE" in report


# ---------------------------------------------------------------------------
# T3: Cyclical EPS Normalization (mining peak)
# ---------------------------------------------------------------------------

def test_normalize_cyclical_eps_at_peak_margin():
    """Mining margin >30% → EPS normalized down to cycle-average."""
    stats = KeyStats(ticker="ADRO", eps_ttm=500.0, net_margin=0.40, historical_pe_avg=8.0)
    calc = FairValueCalculator(stats, sector="mining")
    normalized = calc._normalize_cyclical_eps()
    expected = 500.0 * (0.15 / 0.40)
    assert abs(normalized - expected) < 0.1
    assert calc._normalized_eps is not None


def test_normalize_cyclical_eps_at_normal_margin():
    """Mining at median margin (15%) → EPS unchanged."""
    stats = KeyStats(eps_ttm=300.0, net_margin=0.15, historical_pe_avg=8.0)
    calc = FairValueCalculator(stats, sector="mining")
    normalized = calc._normalize_cyclical_eps()
    assert normalized == 300.0
    assert calc._normalized_eps is None


def test_fair_value_pe_uses_normalized_eps_for_mining_peak():
    """fair_value_pe() applies normalization for mining at peak margin."""
    stats = KeyStats(ticker="ADRO", eps_ttm=1000.0, net_margin=0.50, historical_pe_avg=8.0)
    calc = FairValueCalculator(stats, sector="mining")
    fv = calc.fair_value_pe()
    normalized_eps = 1000.0 * (0.15 / 0.50)  # 300
    assert fv == round(normalized_eps * 8.0, 0)  # 2400
    assert calc._normalized_eps is not None


def test_fair_value_pe_unchanged_for_non_mining_high_margin():
    """Non-mining sectors must not have EPS normalized even at high margin."""
    stats = KeyStats(eps_ttm=500.0, net_margin=0.50, historical_pe_avg=18.0)
    calc = FairValueCalculator(stats, sector="consumer")
    fv = calc.fair_value_pe()
    assert fv == round(500.0 * 18.0, 0)
    assert calc._normalized_eps is None


def test_normalized_eps_shows_in_report():
    """build_report surfaces EPS normalization when active."""
    stats = KeyStats(
        ticker="ADRO",
        current_price=2000.0,
        eps_ttm=1000.0,
        net_margin=0.50,
        historical_pe_avg=8.0,
    )
    calc = FairValueCalculator(stats, sector="mining")
    report = calc.build_report()
    assert "normalized" in report
    assert "peak margin" in report


# ── FV-3: SOE Governance Discount ────────────────────────────────────────────

def _make_soe_calc(ticker: str, sector: str = "bank") -> FairValueCalculator:
    stats = KeyStats(
        ticker=ticker,
        current_price=5000.0,
        eps_ttm=400.0,
        book_value_per_share=3000.0,
        roe=0.15,
        historical_pe_avg=15.0,
        historical_pb_avg=2.5,
    )
    return FairValueCalculator(stats, sector=sector)


def test_soe_ticker_fair_value_is_discounted():
    calc_soe = _make_soe_calc("BBRI")
    calc_private = _make_soe_calc("BBCA")  # BBCA is private — no discount
    result_soe = calc_soe.fair_value_weighted()
    result_private = calc_private.fair_value_weighted()
    assert result_soe["fair_value"] is not None
    assert result_private["fair_value"] is not None
    assert result_soe["fair_value"] < result_private["fair_value"]
    expected = round(result_private["fair_value"] * 0.85, 0)
    assert result_soe["fair_value"] == expected


def test_soe_result_flags_is_soe_true():
    result = _make_soe_calc("BMRI").fair_value_weighted()
    assert result["is_soe"] is True
    assert result["governance_discount_pct"] == pytest.approx(0.15)


def test_non_soe_result_flags_is_soe_false():
    result = _make_soe_calc("BBCA").fair_value_weighted()
    assert result["is_soe"] is False
    assert result["governance_discount_pct"] is None


def test_soe_mos_lower_than_non_soe():
    mos_soe = _make_soe_calc("BBRI").fair_value_weighted()["margin_of_safety_pct"]
    mos_private = _make_soe_calc("BBCA").fair_value_weighted()["margin_of_safety_pct"]
    assert mos_soe is not None and mos_private is not None
    assert mos_soe < mos_private


# ── FV-4: Staleness Weight Adjustment ────────────────────────────────────────

def _make_stale_calc(age_days: int | None, ticker: str = "BBCA") -> FairValueCalculator:
    stats = KeyStats(
        ticker=ticker,
        current_price=5000.0,
        eps_ttm=300.0,
        book_value_per_share=2500.0,
        roe=0.12,
        historical_pe_avg=14.0,
        historical_pb_avg=2.0,
        keystats_age_days=age_days,
    )
    return FairValueCalculator(stats, sector="default")


def test_stale_keystats_flag_in_result():
    result = _make_stale_calc(45).fair_value_weighted()
    assert result["keystats_stale"] is True
    assert result["keystats_age_days"] == 45


def test_fresh_keystats_flag_in_result():
    result = _make_stale_calc(20).fair_value_weighted()
    assert result["keystats_stale"] is False
    assert result["keystats_age_days"] == 20


def test_unknown_age_no_staleness():
    result = _make_stale_calc(None).fair_value_weighted()
    assert result["keystats_stale"] is False
    assert result["keystats_age_days"] is None


# ── FV-5: Dynamic Sector Benchmarks ──────────────────────────────────────────

def test_load_dynamic_sector_benchmarks_falls_back_to_default_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fvc, "_SECTOR_BENCHMARKS_CACHE_PATH", tmp_path / "no_file.json")
    result = _load_dynamic_sector_benchmarks()
    assert result == _SECTOR_MEDIAN_PROFILES_DEFAULT


def test_load_dynamic_sector_benchmarks_reads_cache_when_fresh(tmp_path, monkeypatch):
    from datetime import datetime
    cache = tmp_path / "sector_benchmarks.json"
    fresh_benchmarks = {"bank": {"pe": 9.5, "pb": 1.3, "roe": 0.13, "net_margin": 0.24}}
    cache.write_text(
        __import__("json").dumps({"updated_at": datetime.now().isoformat(), "benchmarks": fresh_benchmarks}),
        encoding="utf-8",
    )
    monkeypatch.setattr(fvc, "_SECTOR_BENCHMARKS_CACHE_PATH", cache)
    result = _load_dynamic_sector_benchmarks()
    assert result == fresh_benchmarks


def test_refresh_sector_benchmarks_writes_and_returns_medians(tmp_path, monkeypatch):
    monkeypatch.setattr(fvc, "_SECTOR_BENCHMARKS_CACHE_PATH", tmp_path / "benchmarks.json")

    def _fake_fetch(ticker: str) -> dict:
        # Use exact field names extract_keystats() looks for.
        # eps_ttm must be non-zero so Strategy B doesn't overwrite Strategy A values.
        return {
            "data": {
                "closure_fin_items_results": [
                    {
                        "fin_name_results": [
                            {"fitem": {"name": "Current EPS (TTM)", "value": "100"}},
                            {"fitem": {"name": "Book Value Per Share", "value": "500"}},
                            {"fitem": {"name": "Current PE Ratio (TTM)", "value": "12.0"}},
                            {"fitem": {"name": "Current Price to Book Value", "value": "1.8"}},
                            {"fitem": {"name": "Return On Equity (TTM)", "value": "15%"}},
                            {"fitem": {"name": "Net Profit Margin (TTM)", "value": "10%"}},
                        ]
                    }
                ]
            }
        }

    result = refresh_sector_benchmarks(_fake_fetch, sectors=["industrials"])
    assert "industrials" in result
    assert result["industrials"]["pe"] == pytest.approx(12.0)
    assert result["industrials"]["pb"] == pytest.approx(1.8)
    # Cache file must exist
    assert (tmp_path / "benchmarks.json").exists()


def test_calculator_uses_dynamic_benchmarks(monkeypatch):
    custom = {"bank": {"pe": 8.0, "pb": 1.0, "roe": 0.10, "net_margin": 0.20}, "default": {"pe": 8.0, "pb": 1.0, "roe": 0.10, "net_margin": 0.10}}
    monkeypatch.setattr(fvc, "_load_dynamic_sector_benchmarks", lambda: custom)
    stats = KeyStats(ticker="BBCA", current_price=5000.0, eps_ttm=300.0, book_value_per_share=2000.0)
    calc = FairValueCalculator(stats, sector="bank")
    assert calc.sector_medians["bank"]["pe"] == pytest.approx(8.0)


def test_stale_fv_shifted_toward_pb():
    # Make PE-implied FV >> PB-implied FV so staleness (which halves PE weight
    # and boosts PB) visibly pulls the composite FV down.
    stats_fresh = KeyStats(
        ticker="BBCA",
        current_price=10000.0,
        eps_ttm=1000.0,           # PE FV = 1000 × 14 = 14 000
        book_value_per_share=2000.0,  # PB FV = 2000 × 2 = 4 000
        historical_pe_avg=14.0,
        historical_pb_avg=2.0,
        keystats_age_days=5,      # fresh
    )
    stats_stale = KeyStats(
        ticker="BBCA",
        current_price=10000.0,
        eps_ttm=1000.0,
        book_value_per_share=2000.0,
        historical_pe_avg=14.0,
        historical_pb_avg=2.0,
        keystats_age_days=60,     # stale
    )
    fv_fresh = FairValueCalculator(stats_fresh, sector="default").fair_value_weighted()["fair_value"]
    fv_stale = FairValueCalculator(stats_stale, sector="default").fair_value_weighted()["fair_value"]
    assert fv_fresh is not None and fv_stale is not None
    # Stale shifts weight toward lower PB-implied FV → composite FV must fall
    assert fv_stale < fv_fresh


# ---------------------------------------------------------------------------
# C3 — Historical Valuation Band tests
# ---------------------------------------------------------------------------

def _multi_year_response(pe_by_year: list[float], pb_by_year: list[float]) -> dict:
    """Build a closure_fin_items_results response with one group per year."""
    groups = []
    for pe, pb in zip(pe_by_year, pb_by_year):
        groups.append({
            "fin_name_results": [
                {"fitem": {"name": "PER", "value": str(pe)}},
                {"fitem": {"name": "PBV", "value": str(pb)}},
            ]
        })
    return {"data": {"closure_fin_items_results": groups}}


def test_extract_historical_multiples_pattern3_returns_values_list():
    pe_series = [8.0, 10.0, 14.0, 18.0, 22.0]
    pb_series = [1.2, 1.5, 2.0, 2.5, 3.0]
    result = extract_historical_multiples(_multi_year_response(pe_series, pb_series), "BBRI")
    assert "pe_values" in result
    assert "pb_values" in result
    assert set(result["pe_values"]) == set(pe_series)
    assert set(result["pb_values"]) == set(pb_series)


def test_extract_historical_multiples_pattern3_updates_median():
    # 5 groups → median of [8, 10, 14, 18, 22] = 14.0
    pe_series = [8.0, 10.0, 14.0, 18.0, 22.0]
    pb_series = [1.2, 1.5, 2.0, 2.5, 3.0]
    result = extract_historical_multiples(_multi_year_response(pe_series, pb_series), "BBRI")
    assert result["pe"] == 14.0
    assert result["pb"] == 2.0


def test_extract_historical_multiples_pattern3_insufficient_data_no_list():
    # Only 2 groups → insufficient for percentile, pe_values not returned
    resp = _multi_year_response([10.0, 14.0], [1.5, 2.0])
    result = extract_historical_multiples(resp, "XXXX")
    assert "pe_values" not in result
    assert "pb_values" not in result


def test_compute_valuation_band_context_returns_none_on_empty():
    assert _compute_valuation_band_context(15.0, 2.0, [], []) is None


def test_compute_valuation_band_context_returns_none_on_insufficient():
    assert _compute_valuation_band_context(15.0, 2.0, [10.0, 20.0], [1.0, 3.0]) is None


def test_compute_valuation_band_context_cheap_label():
    pe_series = [8.0, 10.0, 14.0, 18.0, 22.0]   # range 8–22, current=9 → ~7th pct → CHEAP
    pb_series = [1.2, 1.5, 2.0, 2.5, 3.0]
    ctx = _compute_valuation_band_context(9.0, 1.3, pe_series, pb_series)
    assert ctx is not None
    assert "HISTORICALLY_CHEAP" in ctx


def test_compute_valuation_band_context_expensive_label():
    pe_series = [8.0, 10.0, 14.0, 18.0, 22.0]   # current=21 → ~93rd pct → EXPENSIVE
    pb_series = [1.2, 1.5, 2.0, 2.5, 3.0]
    ctx = _compute_valuation_band_context(21.0, 2.8, pe_series, pb_series)
    assert ctx is not None
    assert "HISTORICALLY_EXPENSIVE" in ctx


def test_compute_valuation_band_context_tight_range_returns_none():
    # Range < 0.5 → insufficient
    pe_series = [14.0, 14.1, 14.2, 14.3, 14.4]
    ctx = _compute_valuation_band_context(14.2, 0.0, pe_series, [])
    assert ctx is None


def test_compute_valuation_band_context_includes_year_count():
    pe_series = [8.0, 10.0, 14.0, 18.0, 22.0]
    ctx = _compute_valuation_band_context(14.0, 0.0, pe_series, [])
    assert ctx is not None
    assert "5-yr" in ctx


def test_build_fair_value_payload_band_context_in_result_when_data_present():
    pe_series = [8.0, 10.0, 14.0, 18.0, 22.0]
    pb_series = [1.2, 1.5, 2.0, 2.5, 3.0]
    resp = _multi_year_response(pe_series, pb_series)
    # Add live fields so extract_keystats can set raw_pe_current / raw_pb_current.
    # "Current EPS (TTM)" blocks Strategy B (which would overwrite Strategy A's PE with 0).
    # "Book Value Per Share" enables a second FV method (PBV) to avoid LOW confidence
    # (quality gate fires on LOW confidence and correctly nulls valuation_band_context).
    resp["data"]["closure_fin_items_results"][0]["fin_name_results"] += [
        {"fitem": {"name": "Current EPS (TTM)", "value": "500"}},
        {"fitem": {"name": "Book Value Per Share", "value": "3000"}},
        {"fitem": {"name": "P/E Ratio", "value": "14"}},
        {"fitem": {"name": "P/B Ratio", "value": "1.5"}},
    ]
    _, result = build_fair_value_payload(resp, "BBRI", 7000.0)
    assert result.get("valuation_band_context") is not None


def test_build_fair_value_payload_band_context_none_when_no_history():
    resp = {"data": {"closure_fin_items_results": [
        {"fin_name_results": [
            {"fitem": {"name": "Current EPS (TTM)", "value": "500"}},
            {"fitem": {"name": "Book Value Per Share", "value": "3000"}},
        ]}
    ]}}
    _, result = build_fair_value_payload(resp, "XXXX", 7000.0)
    assert result.get("valuation_band_context") is None


# ---------------------------------------------------------------------------
# Task C: DDM weight reduction
# ---------------------------------------------------------------------------


def test_sector_weights_ddm_zero_for_default_and_consumer() -> None:
    assert FairValueCalculator.SECTOR_WEIGHTS["default"]["ddm"] == 0.0
    assert FairValueCalculator.SECTOR_WEIGHTS["consumer"]["ddm"] == 0.0


def test_sector_weights_ddm_at_most_five_pct_for_bank_and_property() -> None:
    assert FairValueCalculator.SECTOR_WEIGHTS["bank"]["ddm"] <= 0.05
    assert FairValueCalculator.SECTOR_WEIGHTS["property"]["ddm"] <= 0.05


def test_composite_fair_value_ignores_ddm_when_weight_is_zero(monkeypatch) -> None:
    """Zero-weight DDM must not pull the composite FV even when DDM returns a value."""
    stats = KeyStats(ticker="TEST", current_price=1000.0)
    calc = FairValueCalculator(stats, sector="default")
    monkeypatch.setattr(calc, "fair_value_pe", lambda: 1200.0)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: 800.0)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: 3000.0)  # outlier
    monkeypatch.setattr(calc, "fair_value_dcf", lambda: None)  # not available

    result = calc.fair_value_weighted()

    # default weights: pe=0.50, pb=0.40, ddm=0.00, dcf=0.10 (dcf=None → not in results)
    # active methods: pe + pb; total_weight=0.90; (1200*0.50 + 800*0.40)/0.90 = 920/0.90 ≈ 1022
    # DDM outlier (weight=0.00) has no effect.
    assert result["fair_value"] == 1022.0
    assert result["fair_value"] < 1100.0


# ---------------------------------------------------------------------------
# compute_52w_range_signal
# ---------------------------------------------------------------------------

def test_52w_range_signal_near_high():
    result = compute_52w_range_signal(current_price=9_500, high_52w=10_000, low_52w=5_000)
    assert result is not None
    assert "NEAR_52W_HIGH" in result
    assert "90.0" in result  # (9500-5000)/(10000-5000)*100 = 90.0


def test_52w_range_signal_near_low():
    result = compute_52w_range_signal(current_price=5_500, high_52w=10_000, low_52w=5_000)
    assert result is not None
    assert "NEAR_52W_LOW" in result
    assert "10.0" in result  # (5500-5000)/5000*100 = 10.0


def test_52w_range_signal_below_mid():
    result = compute_52w_range_signal(current_price=7_000, high_52w=10_000, low_52w=5_000)
    assert result is not None
    assert "BELOW_MID" in result
    assert "40.0" in result  # (7000-5000)/5000*100 = 40.0


def test_52w_range_signal_above_mid():
    result = compute_52w_range_signal(current_price=8_000, high_52w=10_000, low_52w=5_000)
    assert result is not None
    assert "ABOVE_MID" in result
    assert "60.0" in result  # (8000-5000)/5000*100 = 60.0


def test_52w_range_signal_returns_none_on_missing_data():
    assert compute_52w_range_signal(0.0, 10_000, 5_000) is None
    assert compute_52w_range_signal(7_000, 0.0, 5_000) is None
    assert compute_52w_range_signal(7_000, 10_000, 0.0) is None


def test_52w_range_signal_returns_none_when_range_zero():
    assert compute_52w_range_signal(10_000, 10_000, 10_000) is None


def test_52w_range_signal_contains_rp_prices():
    result = compute_52w_range_signal(7_500, 10_000, 5_000)
    assert result is not None
    assert "Rp 5,000" in result


# ---------------------------------------------------------------------------
# P10: 2-Stage DCF (fair_value_dcf)
# ---------------------------------------------------------------------------

def _consumer_calc(ocf_per_share: float = 0.0, ocf_ttm: float = 0.0, shares: float = 1_000_000.0,
                   price: float = 5_000.0, ke: float = 0.12, g: float = 0.07,
                   capex_ttm: float | None = None) -> FairValueCalculator:
    if capex_ttm is None:
        # FIX 3B: default capex = 20% of OCF (a plausible maintenance-capex
        # ratio) so tests that aren't specifically about capex still exercise
        # a positive FCFE, same as they exercised a positive OCF before.
        effective_ocf_ttm = ocf_ttm if ocf_ttm else (ocf_per_share * shares)
        capex_ttm = effective_ocf_ttm * 0.2 if effective_ocf_ttm > 0 else 0.0
    stats = KeyStats(
        ticker="UNVR",
        current_price=price,
        ocf_per_share=ocf_per_share,
        operating_cash_flow_ttm=ocf_ttm,
        shares_outstanding=shares,
        cost_of_equity=ke,
        growth_rate=g,
        capex_ttm=capex_ttm,
    )
    return FairValueCalculator(stats, sector="consumer")


def test_fair_value_dcf_returns_positive_float():
    calc = _consumer_calc(ocf_per_share=300.0)
    result = calc.fair_value_dcf()
    assert result is not None
    assert result > 0


def test_fair_value_dcf_returns_none_for_bank():
    stats = KeyStats(ticker="BBCA", current_price=9_000, ocf_per_share=500.0)
    calc = FairValueCalculator(stats, sector="bank")
    assert calc.fair_value_dcf() is None


def test_fair_value_dcf_returns_none_for_mining():
    stats = KeyStats(ticker="ADRO", current_price=2_000, ocf_per_share=300.0)
    calc = FairValueCalculator(stats, sector="mining")
    assert calc.fair_value_dcf() is None


def test_fair_value_dcf_returns_none_when_ocf_zero():
    calc = _consumer_calc(ocf_per_share=0.0, ocf_ttm=0.0, shares=0.0)
    assert calc.fair_value_dcf() is None


def test_fair_value_dcf_requires_stable_ocf_when_score_is_available():
    calc = _consumer_calc(ocf_per_share=300.0)
    calc.stats.ocf_stability_score = 0.20
    assert calc.fair_value_dcf() is None

    calc.stats.ocf_stability_score = 0.80
    assert calc.fair_value_dcf() is not None


def test_fair_value_dcf_rejects_stale_ocf_data():
    calc = _consumer_calc(ocf_per_share=300.0)
    calc.stats.keystats_age_days = fvc._STALE_KEYSTATS_DAYS + 1
    assert calc.fair_value_dcf() is None


def test_fair_value_dcf_derives_ocf_from_ttm_and_shares():
    # ocf_per_share=0 but derivable from ttm/shares
    calc = _consumer_calc(ocf_per_share=0.0, ocf_ttm=3_000_000_000, shares=10_000_000, price=5_000.0)
    result = calc.fair_value_dcf()
    assert result is not None and result > 0


def test_fair_value_dcf_rejects_outlier_high():
    # Very low price vs very high OCF → ratio > 5× → rejected
    calc = _consumer_calc(ocf_per_share=50_000.0, price=100.0)
    assert calc.fair_value_dcf() is None


def test_fair_value_dcf_rejects_outlier_low():
    # Very high price vs tiny OCF → ratio < 0.1× → rejected
    calc = _consumer_calc(ocf_per_share=1.0, price=1_000_000.0)
    assert calc.fair_value_dcf() is None


def test_fair_value_dcf_result_within_sanity_band():
    # For a normal consumer stock: FV should be within 0.1x-5x of current price
    calc = _consumer_calc(ocf_per_share=300.0, price=5_000.0)
    result = calc.fair_value_dcf()
    assert result is not None
    assert 500.0 <= result <= 25_000.0


# ---------------------------------------------------------------------------
# FIX 3B: FCFE = OCF - Capex (deviation from spec's WACC/FCFF -- see
# fair_value_dcf() docstring: Stockbit's indirect-method OCF is already
# post-interest/levered, so WACC + net-debt bridge would double-count debt
# service already reflected in the numerator).
# ---------------------------------------------------------------------------

def test_fair_value_dcf_fcfe_lower_than_ocf_only_proxy():
    """Capex must actually reduce the DCF output vs an OCF-only base --
    proving FCFE is really driving the calculation, not silently ignored.
    The formula is linear in the base cash flow, so scaling fcfe_ps from
    1000 (capex=0) to 800 (capex/share=200) must scale the FV by the same
    0.8x -- this IS the hand-calculated proof, not just "some number differs".
    """
    ocf_only = _consumer_calc(ocf_per_share=1000.0, shares=1_000_000.0, capex_ttm=0.0)
    with_capex = _consumer_calc(
        ocf_per_share=1000.0, shares=1_000_000.0, capex_ttm=200_000_000.0,
    )
    fv_ocf_only = ocf_only.fair_value_dcf()
    fv_with_capex = with_capex.fair_value_dcf()
    assert fv_ocf_only is not None
    assert fv_with_capex is not None
    assert fv_with_capex < fv_ocf_only
    assert fv_with_capex == pytest.approx(fv_ocf_only * 0.8, rel=0.01)


def test_fair_value_dcf_missing_capex_returns_none_not_ocf_fallback():
    """Missing capex must exclude the method, never silently fall back to
    the old OCF-only proxy -- that would defeat this fix's entire purpose."""
    stats = KeyStats(
        ticker="UNVR",
        current_price=5_000.0,
        ocf_per_share=1_000.0,
        shares_outstanding=1_000_000.0,
        cost_of_equity=0.12,
        growth_rate=0.07,
        capex_ttm=None,
    )
    calc = FairValueCalculator(stats, sector="consumer")
    assert calc.fair_value_dcf() is None


def test_fair_value_dcf_negative_fcfe_returns_none():
    """Capex exceeding OCF -> negative FCFE -> method not applicable, not a
    negative or misleading fair value."""
    calc = _consumer_calc(
        ocf_per_share=1_000.0, shares=1_000_000.0, capex_ttm=1_500_000_000.0,
    )
    assert calc.fair_value_dcf() is None


def test_fair_value_dcf_explicit_zero_capex_is_valid():
    """Capex of exactly 0.0 is a known value distinct from None (unknown) --
    FCFE collapses to OCF via the capex path, not a silent skip."""
    calc = _consumer_calc(ocf_per_share=1_000.0, shares=1_000_000.0, capex_ttm=0.0)
    result = calc.fair_value_dcf()
    assert result is not None
    assert result > 0


def test_fair_value_dcf_missing_shares_returns_none():
    """Capex is only reported as a TTM aggregate -- without shares outstanding
    it cannot be converted to a per-share figure."""
    calc = _consumer_calc(
        ocf_per_share=1_000.0, shares=0.0, capex_ttm=100_000_000.0,
    )
    assert calc.fair_value_dcf() is None


def test_consumer_sector_weights_include_dcf():
    weights = FairValueCalculator.SECTOR_WEIGHTS["consumer"]
    assert "dcf" in weights
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_default_sector_weights_include_dcf():
    weights = FairValueCalculator.SECTOR_WEIGHTS["default"]
    assert "dcf" in weights
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_fair_value_weighted_consumer_includes_dcf(monkeypatch):
    calc = _consumer_calc(ocf_per_share=300.0, price=5_000.0)
    monkeypatch.setattr(calc, "fair_value_pe", lambda: 5_500.0)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: 4_800.0)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: None)
    result = calc.fair_value_weighted()
    assert "dcf" in result["breakdown"]


# ---------------------------------------------------------------------------
# FV-6 (V1.2): cross-method dispersion + implied-PE sanity band
# ---------------------------------------------------------------------------

def test_dispersion_above_soft_ratio_widens_band(monkeypatch):
    # pe=100, pb=250 → ratio 2.5 > soft 2.0: band must cover the real spread
    # instead of the count-based ±15%.
    result = _calculator_with_methods(
        monkeypatch, current_price=100, pe=100.0, pb=250.0, ddm=None
    ).fair_value_weighted()

    assert result["fv_method_dispersion_ratio"] == pytest.approx(2.5)
    expected_half_spread = (250.0 - 100.0) / (2 * result["fair_value"])
    assert result["range_pct"] == pytest.approx(expected_half_spread, abs=1e-4)
    assert result["range_pct"] > 0.15


def test_dispersion_downgrades_high_confidence_to_medium(monkeypatch):
    # 3 weighted methods would be HIGH by count, but a 3.0x spread is not
    # agreement — confidence must drop to MEDIUM (soft gate; 3.0 is not > hard).
    calc = _calculator_with_methods(
        monkeypatch, current_price=100, pe=100.0, pb=240.0, ddm=None
    )
    monkeypatch.setattr(calc, "fair_value_dcf", lambda: 300.0)
    result = calc.fair_value_weighted()

    assert len(result["breakdown"]) == 3
    assert result["fv_method_dispersion_ratio"] == pytest.approx(3.0)
    assert result["confidence"] == "MEDIUM"
    assert result["range_pct"] > 0.10


def test_dispersion_benign_keeps_count_based_contract(monkeypatch):
    # pe=100, pb=120 → ratio 1.2: methods agree, nothing changes.
    result = _calculator_with_methods(
        monkeypatch, current_price=100, pe=100.0, pb=120.0, ddm=None
    ).fair_value_weighted()

    assert result["fv_method_dispersion_ratio"] == pytest.approx(1.2)
    assert result["range_pct"] == pytest.approx(0.15)
    assert result["confidence"] == "MEDIUM"


def test_zero_weight_ddm_outlier_does_not_trip_dispersion(monkeypatch):
    # Default profile: ddm weight 0.00 — a wild DDM cannot move the composite
    # (test_composite_fair_value_ignores_ddm_when_weight_is_zero), so it must
    # not move the dispersion flags either.
    result = _calculator_with_methods(
        monkeypatch, current_price=100, pe=100.0, pb=110.0, ddm=3000.0
    ).fair_value_weighted()

    assert result["fv_method_dispersion_ratio"] == pytest.approx(1.1)


def test_zero_weight_ddm_does_not_inflate_confidence_or_tighten_range(
    monkeypatch,
):
    result = _calculator_with_methods(
        monkeypatch,
        current_price=100,
        pe=100.0,
        pb=110.0,
        ddm=3000.0,
    ).fair_value_weighted()

    assert result["breakdown"]["ddm"] == 3000
    assert result["fv_method_dispersion_ratio"] == pytest.approx(1.1)
    assert result["confidence"] == "MEDIUM"
    assert result["range_pct"] == pytest.approx(0.15)
    assert result["active_method_count"] == 2
    assert result["valid_method_count"] == 2
    assert result["available_method_count"] == 3


def test_only_zero_weight_method_returns_insufficient_data(monkeypatch):
    result = _calculator_with_methods(
        monkeypatch,
        current_price=100,
        pe=None,
        pb=None,
        ddm=3000.0,
    ).fair_value_weighted()

    assert result["breakdown"] == {"ddm": 3000}
    assert result["fair_value"] is None
    assert result["confidence"] == "INSUFFICIENT_DATA"
    assert result["active_method_count"] == 0
    assert result["valid_method_count"] == 0
    assert result["available_method_count"] == 1


def test_quality_gate_rejects_extreme_dispersion(monkeypatch):
    # pe=100 vs pb=350 → ratio 3.5 > hard 3.0: the anchor must not survive
    # into the payload (ICBP/UNVR inflation audit).
    _patch_methods(monkeypatch, pe=100.0, pb=350.0)

    report, result = build_fair_value_payload(
        _quality_gate_response("12%"), "TEST", 150.0
    )

    assert result["fair_value"] is None
    assert result["fv_quality_rejected"] is True
    assert result["fv_quality_reasons"] == ["fv_dispersion_extreme"]
    assert result["valuation_verdict"] == "QUALITY_REJECTED"
    assert "FAIR VALUE QUALITY GATE" in report


def test_implied_pe_extreme_flags_icbp_like_inflation(monkeypatch):
    # ICBP audit shape: price 6,800 at PE 17x, composite FV ~15,900 → implied
    # PE ~40x, above BOTH the sector cap (15×1.5=22.5) and self cap (17×1.5=25.5).
    calc = FairValueCalculator(
        KeyStats(
            ticker="ICBP",
            current_price=6_800.0,
            eps_ttm=400.0,
            raw_pe_current=17.0,
        ),
        sector="default",
    )
    monkeypatch.setattr(calc, "sector_medians", {"default": {"pe": 15.0}})
    monkeypatch.setattr(calc, "fair_value_pe", lambda: 15_500.0)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: 16_500.0)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: None)
    result = calc.fair_value_weighted()

    assert result["fv_implied_pe"] == pytest.approx(39.9, abs=0.2)
    assert result["fv_implied_pe_extreme"] is True
    # Orthogonal to dispersion: the methods agree with each other here.
    assert result["fv_method_dispersion_ratio"] < 2.0


def test_implied_pe_within_self_cap_is_not_extreme(monkeypatch):
    # A stock already priced at 50x: FV implying ~55x breaches the sector cap
    # but stays inside its own 1.5x cap — must NOT be flagged, otherwise every
    # quality name trading above sector median gets rejected.
    calc = FairValueCalculator(
        KeyStats(
            ticker="TEST",
            current_price=5_000.0,
            eps_ttm=100.0,
            raw_pe_current=50.0,
        ),
        sector="default",
    )
    monkeypatch.setattr(calc, "sector_medians", {"default": {"pe": 15.0}})
    monkeypatch.setattr(calc, "fair_value_pe", lambda: 5_400.0)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: 5_600.0)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: None)
    result = calc.fair_value_weighted()

    assert result["fv_implied_pe"] == pytest.approx(54.9, abs=0.2)
    assert result["fv_implied_pe_extreme"] is False


# ── FIX 5: audit-grade provenance ────────────────────────────────────────────


def test_fair_value_weighted_exposes_active_and_diagnostic_methods():
    """active_methods lists only methods that carried weight into the
    composite; diagnostic_methods lists methods computed but zero-weighted
    (DDM under the default sector) plus sector_comp (build_sector_comparison()
    is always diagnostic text, never a weighted method)."""
    calc = FairValueCalculator(
        KeyStats(
            ticker="TEST",
            current_price=1000.0,
            eps_ttm=100.0,
            book_value_per_share=800.0,
            dps=20.0,
            roe=0.15,
            cost_of_equity=0.12,
            growth_rate=0.05,
            historical_pe_avg=10.0,
            historical_pb_avg=1.2,
        ),
        sector="default",
    )
    result = calc.fair_value_weighted()

    assert "pe" in result["active_methods"]
    assert "pb" in result["active_methods"]
    assert "ddm" not in result["active_methods"]
    assert "ddm" in result["diagnostic_methods"]
    assert "sector_comp" in result["diagnostic_methods"]
    assert "sector_comp" not in result["active_methods"]


def test_fair_value_weighted_no_methods_still_reports_sector_comp_diagnostic():
    """Even when every weighted method fails, sector_comp stays listed as
    diagnostic -- build_sector_comparison() only needs sector medians, which
    always resolve to something (falls back to the default profile)."""
    calc = FairValueCalculator(
        KeyStats(ticker="TEST", current_price=1000.0), sector="default"
    )
    result = calc.fair_value_weighted()

    assert result["fair_value"] is None
    assert result["active_methods"] == []
    assert result["diagnostic_methods"] == ["sector_comp"]


def test_build_fair_value_payload_includes_fv_provenance():
    """The FV result payload carries per-source provenance so an auditor can
    trace which data source and price basis produced a given fair value.
    financials_source is fixed at 'stockbit_api' for this entry point;
    current_price_source/as_of pass through whatever the caller (debate_
    chamber, per FIX 2) supplies."""
    api_response = _stockbit_response(
        [
            ("Current EPS (TTM)", "100"),
            ("Book Value Per Share", "800"),
        ]
    )
    _report, result = build_fair_value_payload(
        api_response,
        "TEST",
        1000.0,
        current_price_source="market_data",
        current_price_as_of="2026-07-16T09:00:00+07:00",
    )
    prov = result["fv_provenance"]
    assert prov["financials_source"] == "stockbit_api"
    assert prov["current_price_source"] == "market_data"
    assert prov["current_price_as_of"] == "2026-07-16T09:00:00+07:00"
    assert prov["sector_comp_source"] in ("sector_benchmarks_cache", "static_default")


def test_build_fair_value_payload_provenance_defaults_to_none_when_unsupplied():
    """Backward compatibility: existing callers that don't pass current_price_
    source/as_of (e.g. every pre-FIX-5 call site) must not break -- the new
    kwargs default to None rather than becoming required."""
    api_response = _stockbit_response([("Current EPS (TTM)", "100")])
    _report, result = build_fair_value_payload(api_response, "TEST", 1000.0)
    prov = result["fv_provenance"]
    assert prov["current_price_source"] is None
    assert prov["current_price_as_of"] is None
    assert prov["financials_source"] == "stockbit_api"
