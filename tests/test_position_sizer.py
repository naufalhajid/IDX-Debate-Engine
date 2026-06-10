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
