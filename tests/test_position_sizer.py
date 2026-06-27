"""Focused tests for core/quant_filter/position_sizer.py price-basis math."""

import pytest

from core.quant_filter.position_sizer import calculate_positions

USER_CONFIG = {
    "total_capital": 100_000_000,
    "max_loss_pct": 0.02,
    "max_positions": 5,
}


def _candidate(**overrides) -> dict:
    payload = {
        "ticker": "BBRI",
        "rating": "BUY",
        "confidence": 0.80,
        "current_price": 1000.0,
        "entry_high": 1050.0,
        "stop_loss": 930.0,
        "rr_ratio": 2.0,
        "target_price": 1150.0,
        "expected_return": "+12.2%",  # entry-mid basis echo; must not be used
    }
    payload.update(overrides)
    return payload


def test_position_priced_and_risked_at_entry_high() -> None:
    result = calculate_positions([_candidate()], USER_CONFIG)

    [position] = result["positions"]
    assert position["entry_price"] == 1050.0
    # Risk budget respected at the worst-case fill basis.
    assert position["max_loss_rp"] <= USER_CONFIG["total_capital"] * 0.02
    assert position["max_loss_rp"] == position["shares"] * (1050.0 - 930.0)


def test_expected_return_pct_shares_entry_price_basis() -> None:
    result = calculate_positions([_candidate()], USER_CONFIG)

    [position] = result["positions"]
    expected_pct = (1150.0 - 1050.0) / 1050.0 * 100
    assert position["expected_return_pct"] == pytest.approx(expected_pct)
    assert position["expected_return_rp"] == pytest.approx(
        position["position_value"] * expected_pct / 100
    )


def test_expected_return_falls_back_to_string_without_target() -> None:
    result = calculate_positions(
        [_candidate(target_price=None)],
        USER_CONFIG,
    )

    [position] = result["positions"]
    assert position["expected_return_pct"] == pytest.approx(12.2)


def test_entry_price_falls_back_to_current_price_without_entry_high() -> None:
    result = calculate_positions([_candidate(entry_high=None)], USER_CONFIG)

    [position] = result["positions"]
    assert position["entry_price"] == 1000.0


def test_per_position_risk_budget_enforced_across_multiple_candidates() -> None:
    """Each position must not exceed 1/max_positions of the total risk budget."""
    config = {**USER_CONFIG, "max_positions": 3}
    candidates = [
        _candidate(ticker="BBRI", entry_high=1050.0, stop_loss=930.0),
        _candidate(ticker="TLKM", entry_high=3400.0, stop_loss=3100.0),
        _candidate(ticker="ASII", entry_high=5200.0, stop_loss=4900.0),
    ]
    result = calculate_positions(candidates, config)

    total_budget = config["total_capital"] * config["max_loss_pct"]
    per_pos_budget = total_budget / config["max_positions"]
    for pos in result["positions"]:
        assert pos["max_loss_rp"] <= per_pos_budget + 1e-6  # tolerance for floor rounding


# ── P8: Regime-aware position sizing ─────────────────────────────────────────

_BULL_REGIME_PARAMS = {
    "max_position_pct": 0.020,
    "max_concurrent_positions": 3,
    "label": "BULL",
}
_BEAR_REGIME_PARAMS = {
    "max_position_pct": 0.005,
    "max_concurrent_positions": 1,
    "label": "BEAR_STRESS",
}


def test_regime_label_in_summary_when_regime_params_provided() -> None:
    config = {**USER_CONFIG, "regime_params": _BULL_REGIME_PARAMS}
    result = calculate_positions([_candidate()], config)

    assert result["summary"]["regime_label"] == "BULL"
    assert result["summary"]["regime_max_position_pct"] == pytest.approx(0.020)


def test_no_regime_summary_fields_default_when_absent() -> None:
    result = calculate_positions([_candidate()], USER_CONFIG)

    assert result["summary"]["regime_label"] == "N/A"
    assert result["summary"]["regime_max_position_pct"] is None


def test_bear_stress_caps_to_one_position() -> None:
    """BEAR_STRESS max_concurrent_positions=1 must limit output to one position."""
    config = {**USER_CONFIG, "max_positions": 5, "regime_params": _BEAR_REGIME_PARAMS}
    candidates = [
        _candidate(ticker="BBRI"),
        _candidate(ticker="TLKM", current_price=4000.0, entry_high=4100.0, stop_loss=3800.0),
    ]
    result = calculate_positions(candidates, config)

    assert len(result["positions"]) <= 1


def test_bear_stress_per_position_risk_tighter_than_user_budget() -> None:
    """0.5% regime cap must override the user's looser 2% budget in BEAR_STRESS."""
    capital = 100_000_000
    config = {
        "total_capital": capital,
        "max_loss_pct": 0.02,   # user allows 2% total loss -> per-pos = 2%/1 = 2%
        "max_positions": 1,
        "regime_params": _BEAR_REGIME_PARAMS,  # regime cap: 0.5%
    }
    result = calculate_positions([_candidate()], config)

    if result["positions"]:
        max_risk = result["positions"][0]["max_loss_rp"]
        regime_budget = capital * 0.005  # 0.5% of 100M = 500_000
        assert max_risk <= regime_budget + 1e-6


def test_bull_regime_does_not_tighten_when_user_budget_is_smaller() -> None:
    """When user's per-position budget < regime cap, user's budget must win."""
    capital = 100_000_000
    # User budget: 1% / 3 positions = 0.33% per position (< 2% BULL cap)
    config = {
        "total_capital": capital,
        "max_loss_pct": 0.01,
        "max_positions": 3,
        "regime_params": _BULL_REGIME_PARAMS,
    }
    result = calculate_positions([_candidate()], config)

    if result["positions"]:
        max_risk = result["positions"][0]["max_loss_rp"]
        user_budget = capital * 0.01 / 3  # ~333_333
        assert max_risk <= user_budget + 1e-6


def test_no_regime_params_backward_compatible() -> None:
    """Existing callers without regime_params must get unchanged behaviour."""
    result_without = calculate_positions([_candidate()], USER_CONFIG)
    result_with_none = calculate_positions(
        [_candidate()], {**USER_CONFIG, "regime_params": None}
    )

    assert result_without["positions"][0]["lot"] == result_with_none["positions"][0]["lot"]
