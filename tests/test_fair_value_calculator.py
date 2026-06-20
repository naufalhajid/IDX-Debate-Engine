"""Tests for services/fair_value_calculator.py audit fixes."""

from __future__ import annotations

import pytest

import services.fair_value_calculator as fvc
from services.fair_value_calculator import (
    FairValueCalculator,
    KeyStats,
    _SECTOR_MEDIAN_PROFILES_DEFAULT,
    _load_dynamic_sector_benchmarks,
    build_fair_value_payload,
    build_fair_value_report,
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
# Task 24: EV/EBITDA method
# ---------------------------------------------------------------------------

def _mining_calc(ev_ebitda_current: float | None, price: float = 1000.0) -> FairValueCalculator:
    stats = KeyStats(
        ticker="ADRO",
        current_price=price,
        ev_ebitda_current=ev_ebitda_current,
    )
    return FairValueCalculator(stats, sector="mining")


def test_fair_value_ev_ebitda_returns_none_for_non_mining():
    stats = KeyStats(ticker="BBCA", current_price=9000, ev_ebitda_current=10.0)
    calc = FairValueCalculator(stats, sector="bank")
    assert calc.fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_returns_none_when_field_missing():
    assert _mining_calc(ev_ebitda_current=None).fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_returns_none_when_zero():
    assert _mining_calc(ev_ebitda_current=0.0).fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_computes_correctly():
    # price 1000, current EV/EBITDA 4.0, target 5.5 → FV = 1000 × 5.5/4.0 = 1375
    calc = _mining_calc(ev_ebitda_current=4.0, price=1000.0)
    assert calc.fair_value_ev_ebitda() == pytest.approx(1375.0)


def test_fair_value_ev_ebitda_rejects_outlier_high():
    # current EV/EBITDA 0.5 → FV/price = 5.5/0.5 = 11× → rejected
    assert _mining_calc(ev_ebitda_current=0.5).fair_value_ev_ebitda() is None


def test_fair_value_ev_ebitda_rejects_outlier_low():
    # current EV/EBITDA 50 → FV/price = 5.5/50 = 0.11× → rejected
    assert _mining_calc(ev_ebitda_current=50.0).fair_value_ev_ebitda() is None


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
    calc = _mining_calc(ev_ebitda_current=4.0, price=1000.0)
    monkeypatch.setattr(calc, "fair_value_pe", lambda: 1200.0)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: 900.0)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: None)
    # ev_ebitda fires: 1000 × 5.5/4.0 = 1375
    result = calc.fair_value_weighted()
    assert "ev_ebitda" in result["breakdown"]
    assert result["confidence"] == "HIGH"


def test_mining_weighted_four_methods_gives_high_confidence(monkeypatch):
    calc = _mining_calc(ev_ebitda_current=4.0, price=1000.0)
    monkeypatch.setattr(calc, "fair_value_pe", lambda: 1200.0)
    monkeypatch.setattr(calc, "fair_value_pb", lambda: 900.0)
    monkeypatch.setattr(calc, "fair_value_ddm", lambda: 1100.0)
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
