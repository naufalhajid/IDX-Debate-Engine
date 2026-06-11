import pytest

from core.backtest_memory import TradeOutcome
from core.orchestrator.legacy import (
    _rr_component_score,
    compute_conviction_score,
    select_top_n,
)


def _outcome(ticker: str, outcome: str) -> TradeOutcome:
    return TradeOutcome(
        run_id="run-1",
        ticker=ticker,
        verdict_rating="BUY",
        entry_price=1000,
        exit_price=1100 if outcome == "win" else 950,
        target_price=1100,
        stop_loss=950,
        entry_date="2026-05-01",
        exit_date="2026-05-10",
        outcome=outcome,
        pnl_pct=None,
        hit_target=outcome == "win",
        hit_stop=outcome == "loss",
        confidence_at_entry=0.8,
        notes="Synthetic test outcome.",
    )


def _debate_record(ticker: str, rating: str) -> dict:
    return {"ticker": ticker, "verdict": {"rating": rating, "confidence": 0.8}}


def test_realized_outcomes_take_priority_over_debate_history() -> None:
    verdict = {"confidence": 0.60, "risk_reward_ratio": 1.5}
    base_score, _ = compute_conviction_score(verdict)

    # 15 wins satisfies _MIN_RECORDS_FOR_ADJUSTMENT (10) and _HALF_ADJUSTMENT_THRESHOLD (15)
    # HOLD debate records would produce no bonus — realized must take priority
    score, _ = compute_conviction_score(
        verdict,
        ticker="BBCA",
        debate_records=[_debate_record("BBCA", "HOLD")] * 15,
        realized_outcomes=[_outcome("BBCA", "win")] * 15,
    )

    assert score == pytest.approx(base_score + 0.05)


def test_debate_history_is_fallback_when_realized_data_is_insufficient() -> None:
    verdict = {"confidence": 0.60, "risk_reward_ratio": 1.5}
    base_score, _ = compute_conviction_score(verdict)

    # 1 realized outcome < _MIN_RECORDS_FOR_ADJUSTMENT (10) → falls back to debate history
    # 15 BUY debate records satisfies both thresholds → full +0.05 bonus
    score, _ = compute_conviction_score(
        verdict,
        ticker="BBCA",
        debate_records=[_debate_record("BBCA", "BUY")] * 15,
        realized_outcomes=[_outcome("BBCA", "loss")],
    )

    assert score == pytest.approx(base_score + 0.05)


def test_rr_component_is_zero_at_implausible_rr() -> None:
    # INDO 2026-06-11: R/R 22.3 saturated the old min(rr/cap, 1) ramp at 1.0
    # and pushed conviction to 0.83 — the most suspicious setup ranked #1.
    score, warning = compute_conviction_score(
        {"confidence": 0.66, "risk_reward_ratio": 22.3}
    )

    assert score == pytest.approx(0.33)  # 0.5 x 0.66 + 0.5 x 0.0
    assert warning is not None


def test_rr_component_peaks_on_plateau() -> None:
    score, _ = compute_conviction_score({"confidence": 0.66, "risk_reward_ratio": 3.5})

    assert score == pytest.approx(0.83)  # 0.5 x 0.66 + 0.5 x 1.0


def test_rr_component_declines_past_plateau() -> None:
    score, _ = compute_conviction_score({"confidence": 0.60, "risk_reward_ratio": 4.5})

    assert score == pytest.approx(0.55)  # 0.5 x 0.60 + 0.5 x 0.5


def test_rr_component_still_rises_below_plateau() -> None:
    low, _ = compute_conviction_score({"confidence": 0.60, "risk_reward_ratio": 1.5})
    high, _ = compute_conviction_score({"confidence": 0.60, "risk_reward_ratio": 2.5})

    assert low < high


def test_rr_tent_zero_point_is_anchored_to_governor_ceiling() -> None:
    # LOW regime (cap 6.0) must not reward an R/R the governor hard-rejects
    # (RR_IMPLAUSIBLE_CEILING = 5.0), and the fall ends exactly at that line.
    assert _rr_component_score(5.5, 6.0) == 0.0
    assert _rr_component_score(4.9, 6.0) == pytest.approx(0.5)  # (5-4.9)/(5-4.8)
    # Exact boundary: score 0.0 at 5.0 under every cap — the same line where
    # the governor starts rejecting (rr >= RR_IMPLAUSIBLE_CEILING).
    assert _rr_component_score(5.0, 5.0) == 0.0
    assert _rr_component_score(5.0, 6.0) == 0.0
    assert _rr_component_score(5.0, 4.0) == 0.0


def test_rr_tent_does_not_zero_below_governor_ceiling_in_tight_regimes() -> None:
    # DEFENSIVE/HIGH regime (cap 4.0): R/R 4.0 is past the plateau (2.4-3.2)
    # but still below the governor ceiling, so it must score > 0 — the old
    # cap-anchored fall zeroed it and silently excluded high-R/R setups.
    assert _rr_component_score(4.0, 4.0) == pytest.approx((5.0 - 4.0) / (5.0 - 3.2))
    assert _rr_component_score(3.0, 4.0) == 1.0  # plateau


def test_select_top_n_excludes_governor_rejected_entries() -> None:
    def _entry(ticker: str, rr: float, status: str | None) -> dict:
        entry = {
            "ticker": ticker,
            "sector_key": ticker,  # unique sectors keep the cap out of the way
            "verdict": {
                "ticker": ticker,
                "rating": "BUY",
                "confidence": 0.8,
                "risk_reward_ratio": rr,
            },
        }
        if status is not None:
            entry["risk_governor"] = {
                "status": status,
                "reason_codes": ["rr_implausible"],
            }
        return entry

    results = [
        _entry("AAAA", 3.5, None),
        _entry("BBBB", 5.5, "reject"),
        _entry("CCCC", 2.0, "wait_for_pullback"),
    ]

    top = select_top_n(results)
    tickers = [t["ticker"] for t in top]

    assert "BBBB" not in tickers  # hard reject may not occupy a slot
    assert "AAAA" in tickers
    assert "CCCC" in tickers  # soft holds still rank (watchlist semantics)
