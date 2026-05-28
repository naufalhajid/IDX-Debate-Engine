import pytest

from schemas.debate import CIOVerdict
from utils.trade_math import calculate_rr


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
