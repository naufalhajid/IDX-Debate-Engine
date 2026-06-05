import pytest

from core.backtest_memory import TradeOutcome
from core.orchestrator.legacy import compute_conviction_score


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
