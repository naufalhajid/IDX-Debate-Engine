"""
Tests for core/fundamental_factors.py.
OCF/Price, RNOA, and profitability scoring — IDX4 Factor Model helpers.
"""

from __future__ import annotations

import pytest

from core.fundamental_factors import (
    calculate_ocf_price_ratio,
    calculate_profitability_score,
    calculate_rnoa,
)


# ── calculate_ocf_price_ratio ──────────────────────────────────────────────────

def test_ocf_price_basic():
    # OCF=1.2M, shares=100K, price=100 → OCF/share=12 → yield=12/100=0.12
    assert calculate_ocf_price_ratio(1_200_000, 100_000, 100.0) == pytest.approx(0.12)


def test_ocf_price_zero_ocf():
    assert calculate_ocf_price_ratio(0.0, 1_000_000, 5000.0) == 0.0


def test_ocf_price_negative_ocf():
    assert calculate_ocf_price_ratio(-500_000_000, 1_000_000, 1000.0) == 0.0


def test_ocf_price_zero_shares():
    assert calculate_ocf_price_ratio(1_000_000_000, 0.0, 1000.0) == 0.0


def test_ocf_price_zero_price():
    assert calculate_ocf_price_ratio(1_000_000_000, 1_000_000, 0.0) == 0.0


def test_ocf_price_none_inputs():
    assert calculate_ocf_price_ratio(None, None, None) == 0.0  # type: ignore[arg-type]


# ── calculate_rnoa ─────────────────────────────────────────────────────────────

def test_rnoa_direct_decimal():
    assert calculate_rnoa({"rnoa": 0.18}) == pytest.approx(0.18)


def test_rnoa_direct_percentage():
    # provided as percentage (18 → 0.18)
    assert calculate_rnoa({"rnoa": 18.0}) == pytest.approx(0.18)


def test_rnoa_computed_from_ebit_and_noa():
    data = {
        "ebit": 1_000_000_000,
        "tax_rate": 22.0,
        "net_operating_assets": 5_000_000_000,
    }
    expected = 1_000_000_000 * (1 - 0.22) / 5_000_000_000
    assert calculate_rnoa(data) == pytest.approx(expected, rel=1e-6)


def test_rnoa_computed_from_operating_income():
    data = {
        "operating_income": 800_000_000,
        "tax_rate": 0.25,
        "avg_net_operating_assets": 4_000_000_000,
    }
    expected = 800_000_000 * 0.75 / 4_000_000_000
    assert calculate_rnoa(data) == pytest.approx(expected, rel=1e-6)


def test_rnoa_missing_noa_returns_zero():
    assert calculate_rnoa({"operating_income": 500_000_000, "tax_rate": 22.0}) == 0.0


def test_rnoa_zero_operating_income_returns_zero():
    data = {"operating_income": 0.0, "tax_rate": 22.0, "net_operating_assets": 1_000_000_000}
    assert calculate_rnoa(data) == 0.0


def test_rnoa_empty_data():
    assert calculate_rnoa({}) == 0.0


# ── calculate_profitability_score ──────────────────────────────────────────────

def test_profitability_rnoa_tier1_high():
    # RNOA >= 20% → 1.00
    assert calculate_profitability_score({"rnoa": 0.25}) == pytest.approx(1.00)


def test_profitability_rnoa_tier2_medium():
    # 12% <= RNOA < 20% → 0.70
    assert calculate_profitability_score({"rnoa": 0.15}) == pytest.approx(0.70)


def test_profitability_rnoa_tier3_low():
    # 0% < RNOA < 12% → 0.40
    assert calculate_profitability_score({"rnoa": 0.08}) == pytest.approx(0.40)


def test_profitability_roa_fallback_percentage():
    # No rnoa; roa=15% as percentage → 0.15 decimal → tier 0.70
    assert calculate_profitability_score({"roa": 15.0}) == pytest.approx(0.70)


def test_profitability_roa_fallback_decimal():
    assert calculate_profitability_score({"return_on_assets": 0.22}) == pytest.approx(1.00)


def test_profitability_no_data_returns_zero():
    assert calculate_profitability_score({}) == 0.0


def test_profitability_negative_rnoa_returns_zero():
    assert calculate_profitability_score({"rnoa": -0.05}) == 0.0
