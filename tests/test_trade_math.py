import pytest

from core.orchestrator.legacy import _confidence_gate_should_skip
from schemas.debate import CIOVerdict
from utils.trade_math import (
    DEFAULT_RR_MINIMUM,
    LARGE_CAP_RR_MINIMUM,
    LARGE_CAP_THRESHOLD_IDR,
    _load_largecap_fallback,
    calculate_rr,
    get_rr_minimum,
)


@pytest.fixture(autouse=True)
def clear_rr_tier_cache() -> None:
    """Keep R/R tier config cache isolated between tests."""
    _load_largecap_fallback.cache_clear()
    yield
    _load_largecap_fallback.cache_clear()


def test_calculate_rr_standard_case() -> None:
    assert calculate_rr(6775, 7400, 6350) == 1.47


def test_calculate_rr_wide_entry() -> None:
    assert calculate_rr(6625, 7400, 6350) == 2.82


def test_calculate_rr_raises_on_stop_above_entry() -> None:
    with pytest.raises(ValueError, match="must be below entry_high"):
        calculate_rr(6775, 7400, 6800)


def test_calculate_rr_raises_on_equal_stop() -> None:
    with pytest.raises(ValueError, match="must be below entry_high"):
        calculate_rr(6775, 7400, 6775)


def test_cio_verdict_uses_conservative_rr_from_entry_high() -> None:
    verdict = CIOVerdict(
        ticker="INDF",
        rating="BUY",
        confidence=0.8,
        fair_value=8000,
        current_price=6600,
        entry_price_range="6600 - 6775",
        target_price=7400,
        stop_loss=6350,
    )

    assert verdict.risk_reward_ratio == 1.47


def test_get_rr_minimum_large_cap_dynamic() -> None:
    yf_info = {"marketCap": 400_000_000_000_000}

    assert get_rr_minimum("BMRI", yf_info=yf_info) == LARGE_CAP_RR_MINIMUM


def test_get_rr_minimum_default_dynamic() -> None:
    yf_info = {"marketCap": 5_000_000_000_000}

    assert get_rr_minimum("CYBR", yf_info=yf_info) == DEFAULT_RR_MINIMUM


def test_get_rr_minimum_at_threshold_boundary() -> None:
    yf_info = {"marketCap": LARGE_CAP_THRESHOLD_IDR}

    assert get_rr_minimum("SOME_TICKER", yf_info=yf_info) == LARGE_CAP_RR_MINIMUM


def test_get_rr_minimum_just_below_threshold() -> None:
    yf_info = {"marketCap": LARGE_CAP_THRESHOLD_IDR - 1}

    assert get_rr_minimum("SOME_TICKER", yf_info=yf_info) == DEFAULT_RR_MINIMUM


def test_get_rr_minimum_static_fallback_large_cap() -> None:
    assert get_rr_minimum("BBCA", yf_info=None) == LARGE_CAP_RR_MINIMUM


def test_get_rr_minimum_static_fallback_default() -> None:
    assert get_rr_minimum("UNKNOWN_TICKER_ZZZ", yf_info=None) == DEFAULT_RR_MINIMUM


def test_get_rr_minimum_missing_market_cap_falls_back() -> None:
    yf_info = {"previousClose": 4130}

    assert get_rr_minimum("BBCA", yf_info=yf_info) == LARGE_CAP_RR_MINIMUM


def test_get_rr_minimum_zero_market_cap_falls_back() -> None:
    yf_info = {"marketCap": 0}

    assert get_rr_minimum("BBRI", yf_info=yf_info) == LARGE_CAP_RR_MINIMUM


def test_configured_fallback_ticker_changes_threshold_without_code_change(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "rr_tiers.yaml"
    config_path.write_text(
        """
large_cap_fallback:
  - NEWC
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("utils.trade_math.RR_TIERS_CONFIG_PATH", config_path)
    _load_largecap_fallback.cache_clear()

    assert get_rr_minimum("newc", yf_info=None) == LARGE_CAP_RR_MINIMUM


def test_load_largecap_fallback_missing_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "utils.trade_math.RR_TIERS_CONFIG_PATH",
        tmp_path / "nonexistent.yaml",
    )
    _load_largecap_fallback.cache_clear()

    assert _load_largecap_fallback() == set()


def test_rr_exactly_at_large_cap_threshold_passes() -> None:
    assert calculate_rr(entry_high=100, target=113, stop=90) == 1.3
    assert get_rr_minimum("BBRI", yf_info=None) == LARGE_CAP_RR_MINIMUM


def test_rr_below_large_cap_threshold_fails() -> None:
    assert 1.29 < get_rr_minimum("BMRI", yf_info=None)


def test_confidence_gate_passes_at_exact_threshold() -> None:
    assert _confidence_gate_should_skip(confidence=25) is False
    assert _confidence_gate_should_skip(confidence=24) is True
    assert _confidence_gate_should_skip(confidence=26) is False
