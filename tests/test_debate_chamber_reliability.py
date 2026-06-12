"""Reliability tests for services/debate_chamber.py."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from tenacity import stop_after_attempt, wait_fixed

from core.budget import BudgetExhaustedError
from schemas.debate import CIOVerdict, DebateMessage
from services import debate_chamber as dc
from services import debate_prompt_registry
from services.debate_chamber import DebateChamber
from utils import market_data_cache as mdc


class FakeLLM:
    def __init__(self, *, model: str | None = "gemini-2.5-flash", responses=None):
        self.model = model
        self.responses = list(responses or [SimpleNamespace(content="ok")])
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _chamber() -> DebateChamber:
    return object.__new__(DebateChamber)


@pytest.mark.asyncio
async def test_llm_retry_charges_budget_once_per_operation(monkeypatch):
    chamber = _chamber()
    charges: list[int] = []

    async def fake_flash_budget():
        charges.append(1)

    monkeypatch.setattr(dc, "check_and_increment_flash_budget", fake_flash_budget)
    llm = FakeLLM(
        responses=[
            SimpleNamespace(content=""),
            SimpleNamespace(content=""),
            SimpleNamespace(content="usable response"),
        ]
    )

    retrying_attempt = chamber._invoke_llm_attempt.retry_with(
        wait=wait_fixed(0),
        stop=stop_after_attempt(3),
    )

    async def fast_attempt(llm_arg, messages_arg):
        return await retrying_attempt(chamber, llm_arg, messages_arg)

    monkeypatch.setattr(chamber, "_invoke_llm_attempt", fast_attempt)

    response = await chamber._invoke_llm_with_retry(llm, [], "flash")

    assert response.content == "usable response"
    assert llm.calls == 3
    assert len(charges) == 1


@pytest.mark.asyncio
async def test_budget_exhaustion_happens_before_llm_call(monkeypatch):
    chamber = _chamber()
    llm = FakeLLM()

    async def exhausted_flash_budget():
        raise BudgetExhaustedError("spent")

    monkeypatch.setattr(dc, "check_and_increment_flash_budget", exhausted_flash_budget)

    with pytest.raises(BudgetExhaustedError):
        await chamber._invoke_llm_with_retry(llm, [], "flash")

    assert llm.calls == 0


@pytest.mark.asyncio
async def test_unknown_llm_tier_raises_before_call():
    chamber = _chamber()
    llm = FakeLLM(model=None)

    with pytest.raises(RuntimeError, match="Unable to classify LLM tier"):
        await chamber._invoke_llm(llm, [], inject_rules=False)

    assert llm.calls == 0


@pytest.mark.asyncio
async def test_cancelled_error_is_not_wrapped_or_retried():
    chamber = _chamber()
    llm = FakeLLM(responses=[asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await chamber._invoke_llm_with_retry(llm, [], "flash")

    assert llm.calls == 1


@pytest.mark.asyncio
async def test_consensus_round_one_soft_hold_waits_for_more_debate(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        raise AssertionError("consensus evaluator should be deterministic")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 1,
            "debate_history": [
                DebateMessage(role="bull", content="Buy", round_num=1),
                DebateMessage(role="bear", content="Avoid", round_num=1),
            ],
        }
    )

    assert result["consensus_reached"] is False
    assert result["consensus_method"] is None
    assert result["disagreement_type"] == "timing"


@pytest.mark.asyncio
async def test_consensus_round_two_soft_hold_can_conclude(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        raise AssertionError("consensus evaluator should be deterministic")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 2,
            "debate_history": [
                DebateMessage(role="bull", content="Buy", round_num=2),
                DebateMessage(role="bear", content="Avoid", round_num=2),
            ],
        }
    )

    assert result["consensus_reached"] is True
    assert result["consensus_method"] == "soft_hold"
    assert result["dissenting_agents"] == ["bull", "bear"]


@pytest.mark.asyncio
async def test_consensus_round_one_two_of_five_is_not_enough(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        raise AssertionError("consensus evaluator should be deterministic")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 1,
            "debate_history": [
                DebateMessage(role="bull", content="Hold", round_num=1),
                DebateMessage(
                    role="bear", content="Hold, but risks remain", round_num=1
                ),
            ],
        }
    )

    assert result["consensus_reached"] is False
    assert result["consensus_method"] is None


@pytest.mark.asyncio
async def test_consensus_round_one_three_of_five_waits_for_more_debate(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        raise AssertionError("consensus evaluator should be deterministic")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 1,
            "fundamental_data": "Position: BUY\nAgent Confidence: 0.70",
            "technical_data": "Position: BUY\nAgent Confidence: 0.66",
            "sentiment_data": "Position: HOLD\nAgent Confidence: 0.52",
            "debate_history": [
                DebateMessage(
                    role="bull",
                    content="Position: BUY\nAgent Confidence: 0.72",
                    round_num=1,
                ),
                DebateMessage(
                    role="bear",
                    content="Position: AVOID\nAgent Confidence: 0.40",
                    round_num=1,
                ),
            ],
        }
    )

    assert result["consensus_reached"] is False
    assert result["consensus_method"] is None
    assert result["disagreement_type"] == "direction"


@pytest.mark.asyncio
async def test_consensus_round_one_requires_four_of_five_votes(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        raise AssertionError("consensus evaluator should be deterministic")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 1,
            "fundamental_data": "Position: BUY\nAgent Confidence: 0.70",
            "technical_data": "Position: BUY\nAgent Confidence: 0.66",
            "sentiment_data": "Position: BUY\nAgent Confidence: 0.52",
            "debate_history": [
                DebateMessage(
                    role="bull",
                    content="Position: BUY\nAgent Confidence: 0.72",
                    round_num=1,
                ),
                DebateMessage(
                    role="bear",
                    content="Position: AVOID\nAgent Confidence: 0.40",
                    round_num=1,
                ),
            ],
        }
    )

    assert result["consensus_reached"] is True
    assert result["consensus_method"] == "voting"
    assert result["dissenting_agents"] == ["bear"]


@pytest.mark.asyncio
async def test_consensus_round_two_allows_three_of_five_votes(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        raise AssertionError("consensus evaluator should be deterministic")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 2,
            "fundamental_data": "Position: BUY\nAgent Confidence: 0.70",
            "technical_data": "Position: BUY\nAgent Confidence: 0.66",
            "sentiment_data": "Position: HOLD\nAgent Confidence: 0.52",
            "debate_history": [
                DebateMessage(
                    role="bull",
                    content="Position: BUY\nAgent Confidence: 0.72",
                    round_num=2,
                ),
                DebateMessage(
                    role="bear",
                    content="Position: AVOID\nAgent Confidence: 0.40",
                    round_num=2,
                ),
            ],
        }
    )

    assert result["consensus_reached"] is True
    assert result["consensus_method"] == "voting"
    assert result["dissenting_agents"] == ["sentiment_specialist", "bear"]


@pytest.mark.asyncio
async def test_consensus_round_two_majority_beats_soft_hold(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        raise AssertionError("consensus evaluator should be deterministic")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 2,
            "fundamental_data": "Position: BUY\nAgent Confidence: 0.75",
            "technical_data": "Position: BUY\nAgent Confidence: 0.85",
            "sentiment_data": "Position: HOLD\nAgent Confidence: 0.00",
            "debate_history": [
                DebateMessage(
                    role="bull",
                    content="Position: BUY\nAgent Confidence: 0.85",
                    round_num=2,
                ),
                DebateMessage(
                    role="bear",
                    content="Position: AVOID\nAgent Confidence: 0.85",
                    round_num=2,
                ),
            ],
        }
    )

    assert result["consensus_reached"] is True
    assert result["consensus_method"] == "voting"
    assert result["consensus_winner"]["position"] == "BUY"
    assert result["dissenting_agents"] == ["sentiment_specialist", "bear"]


@pytest.mark.asyncio
async def test_consensus_round_three_uses_confidence_winner(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        raise AssertionError("consensus evaluator should be deterministic")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 3,
            "fundamental_data": "Position: BUY\nAgent Confidence: 0.61",
            "technical_data": "Position: AVOID\nAgent Confidence: 0.62",
            "sentiment_data": "Position: HOLD\nAgent Confidence: 0.63",
            "debate_history": [
                DebateMessage(
                    role="bull",
                    content="Position: BUY\nAgent Confidence: 0.64",
                    round_num=3,
                ),
                # 0.93 keeps |bull-bear| = 0.29 > SOFT_HOLD_CONFIDENCE_DELTA (0.27)
                # so soft_hold does not claim this case — confidence_winner fires
                DebateMessage(
                    role="bear",
                    content="Position: AVOID\nAgent Confidence: 0.93",
                    round_num=3,
                ),
            ],
        }
    )

    assert result["consensus_reached"] is False
    assert result["consensus_method"] == "confidence_winner"
    assert result["consensus_winner"]["agent"] == "bear"


@pytest.mark.asyncio
async def test_confidence_winner_uses_effective_calibrated_confidence(monkeypatch):
    chamber = _chamber()
    chamber.agent_calibration_weights = {
        **dc.DEFAULT_AGENT_CALIBRATION_WEIGHTS,
        "bear": 0.5,
    }

    async def fake_invoke(llm, messages, inject_rules=True):
        raise AssertionError("consensus evaluator should be deterministic")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 3,
            "metadata": {"run_id": "run-1"},
            "fundamental_data": "Position: HOLD\nAgent Confidence: 0.10",
            "technical_data": "Position: BUY\nAgent Confidence: 0.11",
            "sentiment_data": "Position: HOLD\nAgent Confidence: 0.12",
            "debate_history": [
                DebateMessage(
                    role="bull",
                    content="Position: BUY\nAgent Confidence: 0.64",
                    round_num=3,
                ),
                DebateMessage(
                    role="bear",
                    content="Position: AVOID\nAgent Confidence: 0.93",
                    round_num=3,
                ),
            ],
        }
    )

    assert result["consensus_method"] == "confidence_winner"
    assert result["consensus_winner"]["agent"] == "bull"
    bear_vote = next(v for v in result["agent_votes"] if v["agent"] == "bear")
    assert bear_vote["confidence"] == 0.93
    assert bear_vote["effective_confidence"] == pytest.approx(0.465)
    assert result["metadata"]["confidence_winner_audit"] == {
        "agent": "bull",
        "raw_confidence": 0.64,
        "effective_confidence": 0.64,
        "calibration_weight": 1.0,
    }


def _confidence_winner_state(
    sentiment_position: str = "HOLD",
    *,
    volume_surge: float = 2.0,
    return_5d: float = 10.0,
) -> dict:
    """Minimal state where bear wins by confidence (AVOID at 0.93), with a
    volume-confirmed momentum breakout in the technicals by default."""
    return {
        "consensus_reached": False,
        "consensus_method": "confidence_winner",
        "consensus_winner": {"agent": "bear", "position": "AVOID", "confidence": 0.93},
        "dissenting_agents": ["bull", "fundamental_scout"],
        "agent_votes": [
            {"agent": "bear", "position": "AVOID", "confidence": 0.93},
            {"agent": "sentiment_specialist", "position": sentiment_position},
        ],
        "technical_indicators": {
            "volume_surge_ratio": volume_surge,
            "return_5d_pct": return_5d,
        },
    }


# DSSA-like overvalued name: price > fair value → value_driven_avoid is True.
_OVERVALUED_PARSED = {
    "rating": "AVOID",
    "confidence": 0.0,
    "current_price": 615.0,
    "fair_value": 304.0,
}


def test_momentum_override_escalates_avoid_to_hold():
    chamber = _chamber()

    result = chamber._apply_consensus_override(
        dict(_OVERVALUED_PARSED), _confidence_winner_state("HOLD")
    )

    assert result["rating"] == "HOLD"
    assert result["confidence"] == 0.55
    assert "MOMENTUM WATCHLIST" in result["weighted_reasoning"]


def test_momentum_override_blocked_by_bearish_sentiment():
    chamber = _chamber()

    # Sentiment specialist bearish → normalises to AVOID → no escalation.
    result = chamber._apply_consensus_override(
        dict(_OVERVALUED_PARSED), _confidence_winner_state("BEARISH")
    )

    assert result["rating"] == "AVOID"
    assert "MOMENTUM WATCHLIST" not in (result.get("weighted_reasoning") or "")


def test_momentum_override_blocked_without_volume_breakout():
    chamber = _chamber()

    # Volume surge below threshold → no momentum breakout → stays AVOID.
    result = chamber._apply_consensus_override(
        dict(_OVERVALUED_PARSED),
        _confidence_winner_state("HOLD", volume_surge=1.0, return_5d=10.0),
    )

    assert result["rating"] == "AVOID"
    assert "MOMENTUM WATCHLIST" not in (result.get("weighted_reasoning") or "")


def test_momentum_override_blocked_when_not_overvalued():
    chamber = _chamber()

    # Undervalued (price < fair value) → AVOID is not value-driven → no escalation,
    # even with a volume-confirmed breakout and non-bearish sentiment.
    parsed = {
        "rating": "AVOID",
        "confidence": 0.0,
        "current_price": 615.0,
        "fair_value": 900.0,
    }
    result = chamber._apply_consensus_override(parsed, _confidence_winner_state("HOLD"))

    assert result["rating"] == "AVOID"
    assert "MOMENTUM WATCHLIST" not in (result.get("weighted_reasoning") or "")


def test_momentum_override_blocked_when_price_inside_fair_value_range():
    chamber = _chamber()

    parsed = {
        "rating": "AVOID",
        "confidence": 0.0,
        "current_price": 108.0,
        "fair_value": 100.0,
        "fair_value_high": 115.0,
    }
    result = chamber._apply_consensus_override(parsed, _confidence_winner_state("HOLD"))

    assert result["rating"] == "AVOID"
    assert "MOMENTUM WATCHLIST" not in (result.get("weighted_reasoning") or "")


def _voting_state(winner_position: str, *, agent: str = "chartist") -> dict:
    return {
        "consensus_reached": True,
        "consensus_method": "voting",
        "consensus_winner": {
            "agent": agent,
            "position": winner_position,
            "confidence": 0.78,
        },
        "dissenting_agents": ["bear"],
        "agent_votes": [],
    }


def test_voting_override_clamps_cio_buy_to_hold_majority():
    chamber = _chamber()

    # INDO 2026-06-11: agents voted HOLD 4/5 + AVOID 1/5 (zero BUY) yet the CIO
    # emitted BUY @ 0.66 — the voting method had no clamp at all.
    parsed = {"rating": "BUY", "confidence": 0.66}
    result = chamber._apply_consensus_override(parsed, _voting_state("HOLD"))

    assert result["rating"] == "HOLD"
    assert result["confidence"] == 0.55
    assert "Consensus override" in result["weighted_reasoning"]


def test_voting_override_keeps_more_bearish_cio_rating():
    chamber = _chamber()

    parsed = {"rating": "HOLD", "confidence": 0.6}
    result = chamber._apply_consensus_override(parsed, _voting_state("BUY"))

    assert result["rating"] == "HOLD"
    assert result["confidence"] == 0.6
    assert "Consensus override" not in (result.get("weighted_reasoning") or "")


def test_voting_override_clamps_strong_buy_to_buy_majority():
    chamber = _chamber()

    parsed = {"rating": "STRONG_BUY", "confidence": 0.8}
    result = chamber._apply_consensus_override(parsed, _voting_state("BUY"))

    assert result["rating"] == "BUY"
    assert result["confidence"] == 0.8


def test_voting_override_clamps_spaced_rating_variant():
    chamber = _chamber()

    # "STRONG BUY" (space) must not dodge the clamp via a failed rank lookup.
    parsed = {"rating": "STRONG BUY", "confidence": 0.8}
    result = chamber._apply_consensus_override(parsed, _voting_state("HOLD"))

    assert result["rating"] == "HOLD"
    assert result["confidence"] == 0.55


def test_voting_override_preserves_zero_confidence_on_clamp():
    chamber = _chamber()

    # Falsy-or must not inflate a legitimate 0.0 confidence to the 0.55 cap.
    parsed = {"rating": "BUY", "confidence": 0.0}
    result = chamber._apply_consensus_override(parsed, _voting_state("HOLD"))

    assert result["rating"] == "HOLD"
    assert result["confidence"] == 0.0


def test_news_adjustment_from_sentiment_is_consistent():
    # Adjustment is derived from the sentiment label, so the two can never
    # contradict (the keyword path could: BREN was POSITIVE overall + -0.20 adj).
    assert dc._news_adjustment_from_sentiment("POSITIVE", False)[0] == 0.05
    assert dc._news_adjustment_from_sentiment("NEGATIVE", False)[0] == -0.10
    assert dc._news_adjustment_from_sentiment("NEGATIVE", True)[0] == -0.20
    assert dc._news_adjustment_from_sentiment("NEUTRAL", False)[0] == 0.0
    assert dc._news_adjustment_from_sentiment("garbage", False)[0] == 0.0
    # Sign never disagrees with the label.
    assert dc._news_adjustment_from_sentiment("POSITIVE", False)[0] > 0
    assert dc._news_adjustment_from_sentiment("NEGATIVE", True)[0] < 0


def _fake_news_fetcher(monkeypatch, *, kw_sentiment="POSITIVE", kw_adjustment=0.05):
    """Patch the news fetcher with a keyword bundle to test the LLM override."""
    from types import SimpleNamespace

    bundle = SimpleNamespace(
        overall_sentiment=SimpleNamespace(value=kw_sentiment),
        sentiment_score=0.5,
        confidence_adjustment=kw_adjustment,
        confidence_adjustment_reason="keyword path",
        has_breaking_news=False,
        items=[],
    )

    class _FakeFetcher:
        async def build_bundle_async(self, ticker):
            return bundle

        def bundle_to_prompt_string(self, b):
            return "NEWS BRIEF"

    monkeypatch.setattr("services.news_fetcher.DEFAULT_FETCHER", _FakeFetcher())


def test_news_context_llm_sentiment_overrides_keyword(monkeypatch):
    # Keyword path says POSITIVE (+0.05), but the LLM judges the stock-specific
    # news NEGATIVE → LLM wins, and overall ≡ adjustment (no contradiction).
    _fake_news_fetcher(monkeypatch, kw_sentiment="POSITIVE", kw_adjustment=0.05)

    out = asyncio.run(
        dc._news_context_for_state({}, "DSSA", llm_news_sentiment="NEGATIVE")
    )

    assert out["news_confidence_adjustment"] == -0.10
    assert out["metadata"]["news_overall_sentiment"] == "NEGATIVE"


def test_news_context_falls_back_to_keyword_when_no_llm_sentiment(monkeypatch):
    # Sparse-social path: no LLM news_sentiment → keyword value is used as-is.
    _fake_news_fetcher(monkeypatch, kw_sentiment="POSITIVE", kw_adjustment=0.05)

    out = asyncio.run(dc._news_context_for_state({}, "DSSA", llm_news_sentiment=None))

    assert out["news_confidence_adjustment"] == 0.05
    assert out["metadata"]["news_overall_sentiment"] == "POSITIVE"


def test_news_context_records_fetch_failure(monkeypatch):
    class _FailingFetcher:
        async def build_bundle_async(self, ticker):
            raise OSError("news provider unavailable")

        def bundle_to_prompt_string(self, bundle):
            return "SHOULD_NOT_RENDER"

    monkeypatch.setattr("services.news_fetcher.DEFAULT_FETCHER", _FailingFetcher())

    state = {"metadata": {"run_id": "run-1"}}
    out = asyncio.run(dc._news_context_for_state(state, "DSSA"))

    failure = out["metadata"]["news_fetch_failure"]
    assert out["news_brief"] == ""
    assert out["news_confidence_adjustment"] == 0.0
    assert out["metadata"]["has_breaking_news"] is False
    assert failure["stage"] == "build_bundle"
    assert failure["type"] == "OSError"
    assert failure["message"] == "news provider unavailable"
    assert state["metadata"]["news_fetch_failure"] == failure


def test_news_context_carries_bundle_fetch_failure(monkeypatch):
    failure = {
        "stage": "rss_fetch",
        "type": "RuntimeError",
        "message": "network failed",
    }
    bundle = SimpleNamespace(
        overall_sentiment=SimpleNamespace(value="UNKNOWN"),
        sentiment_score=0.0,
        confidence_adjustment=0.0,
        confidence_adjustment_reason="News fetch failed - network failed",
        has_breaking_news=False,
        items=[],
        fetch_failure=failure,
    )

    class _FetcherWithFailureBundle:
        async def build_bundle_async(self, ticker):
            return bundle

        def bundle_to_prompt_string(self, b):
            return "No news data available"

    monkeypatch.setattr(
        "services.news_fetcher.DEFAULT_FETCHER",
        _FetcherWithFailureBundle(),
    )

    out = asyncio.run(dc._news_context_for_state({}, "DSSA"))

    assert out["metadata"]["news_fetch_failure"] == failure


def test_fair_value_rejected_without_current_run_rag_evidence():
    fair_value, metadata = dc._reject_unverified_fair_value_if_needed(
        ticker="BBCA",
        run_id="run-1",
        fair_value=10474,
        metadata={"rag_citations": []},
    )

    assert fair_value == 0.0
    assert metadata["fair_value_rejected"] is True
    assert metadata["valuation_gap"] == "unverified"
    assert "fair_value_unverified" in metadata["reasons"]


def test_fair_value_accepted_with_current_run_rag_evidence():
    fair_value, metadata = dc._reject_unverified_fair_value_if_needed(
        ticker="BBCA",
        run_id="run-1",
        fair_value=10474,
        metadata={
            "rag_citations": [
                {
                    "chunk_id": "BBCA_run_1_fair_value_0",
                    "category": "fair_value",
                }
            ]
        },
    )

    assert fair_value == 10474
    assert metadata["fair_value_rag_verified"] is True


@pytest.mark.asyncio
async def test_synthesizer_records_rag_selection_failure(monkeypatch):
    chamber = _chamber()

    class _FailingRanker:
        def build_bundle(self, *, pack, run_id, query_context):
            raise OSError("rag evidence log locked")

        def bundle_to_prompt_string(self, bundle):
            return "SHOULD_NOT_RENDER"

    monkeypatch.setattr(dc, "rag_store", _FailingRanker())

    result = await chamber._synthesizer_node(
        {
            "ticker": "BBRI",
            "fundamental_data": "Revenue improving",
            "technical_data": "MA50 support",
            "sentiment_data": "Position: HOLD",
            "current_price": 1000.0,
            "fair_value_estimate": 0.0,
            "technical_indicators": {"ma50": 980.0, "atr14": 30.0},
            "market_data": {},
            "metadata": {"run_id": "run-1"},
        }
    )

    failure = result["metadata"]["rag_selection_failure"]
    assert result["decision_brief"]
    assert failure["stage"] == "build_bundle"
    assert failure["type"] == "OSError"
    assert failure["message"] == "rag evidence log locked"
    assert "classification" in failure


@pytest.mark.asyncio
async def test_chartist_node_uses_flash_llm(monkeypatch):
    chamber = _chamber()
    chamber.flash_llm = FakeLLM(model="gemini-2.5-flash")
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")
    captured = {}

    async def fast_sleep(_seconds):
        return None

    async def fake_fetch_url(_url):
        return {}

    async def fake_invoke_for_state(state, llm, messages, inject_rules=True):
        captured["llm"] = llm
        captured["messages"] = messages
        return SimpleNamespace(
            content="Technicals support patience.\n\nPosition: HOLD\nAgent Confidence: 0.55"
        )

    monkeypatch.setattr(dc.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(chamber, "_fetch_url", fake_fetch_url)
    monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke_for_state)

    result = await chamber._chartist_node(
        {
            "ticker": "BBCA",
            "market_data": {},
            "metadata": {},
        }
    )

    assert captured["llm"] is chamber.flash_llm
    assert captured["llm"] is not chamber.pro_llm
    assert captured["messages"]
    assert "Position: HOLD" in result["technical_data"]


@pytest.mark.asyncio
async def test_cio_invalid_current_price_fallback_has_no_trade_levels():
    chamber = _chamber()

    result = await chamber._cio_judge_node(
        {
            "ticker": "TOTL",
            "current_price": 0.0,
            "technical_indicators": {},
            "fair_value_estimate": 1000.0,
            "debate_history": [],
            "raw_data": "",
            "devils_advocate_question": "",
        }
    )
    verdict = json.loads(result["final_verdict"])

    assert verdict["rating"] == "HOLD"
    assert verdict["confidence"] == 0.0
    assert verdict["current_price"] == 0.0
    assert verdict["entry_price_range"] is None
    assert verdict["target_price"] is None
    assert verdict["stop_loss"] is None


def test_trade_envelope_keeps_target_above_entry_after_low_fair_value_blend():
    chamber = _chamber()

    envelope = chamber._compute_trade_envelope(
        current_price=1000.0,
        fair_value=800.0,
        tech={"ma50": 980.0, "sma20": 1000.0, "atr14": 10.0},
    )

    assert envelope["stop_loss"] < envelope["entry_low"]
    assert envelope["entry_low"] <= envelope["entry_high"]
    assert envelope["entry_high"] < envelope["target_price"]

    CIOVerdict(
        ticker="BRPT",
        rating="HOLD",
        confidence=0.0,
        entry_price_range=f"{int(envelope['entry_low'])} - {int(envelope['entry_high'])}",
        target_price=envelope["target_price"],
        stop_loss=envelope["stop_loss"],
        current_price=1000.0,
        fair_value=envelope["fair_value"],
    )


@pytest.mark.asyncio
async def test_cio_parse_fallback_survives_low_fair_value_blend(monkeypatch):
    chamber = _chamber()
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")

    async def fake_invoke(llm, messages, inject_rules=True):
        return SimpleNamespace(content="not json")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._cio_judge_node(
        {
            "ticker": "BRPT",
            "current_price": 1000.0,
            "technical_indicators": {"ma50": 980.0, "sma20": 1000.0, "atr14": 10.0},
            "fair_value_estimate": 800.0,
            "debate_history": [],
            "raw_data": "",
            "devils_advocate_question": "",
            "consensus_reached": False,
            "consensus_method": None,
            "dissenting_agents": [],
            "agent_votes": [],
        }
    )
    verdict = json.loads(result["final_verdict"])
    entry_low, entry_high = [
        float(part.strip())
        for part in verdict["entry_price_range"].split("-", maxsplit=1)
    ]

    assert verdict["rating"] == "HOLD"
    assert verdict["stop_loss"] < entry_low <= entry_high < verdict["target_price"]
    failure = result["metadata"]["cio_parse_failure"]
    assert failure["stage"] == "json_parse"
    assert failure["type"] == "JSONDecodeError"
    assert failure["message"]


@pytest.mark.asyncio
async def test_market_data_cache_prefetches_one_yfinance_bundle(monkeypatch):
    calls: list[str] = []

    class FakeTicker:
        def __init__(self, symbol: str):
            calls.append(symbol)

        def history(self, period: str):
            assert period == "1y"
            return pd.DataFrame(
                {
                    "Close": [100.0, 105.0],
                    "High": [101.0, 106.0],
                    "Low": [99.0, 104.0],
                    "Volume": [1_000, 1_100],
                }
            )

        @property
        def info(self):
            return {"currentPrice": 105.0}

        @property
        def fast_info(self):
            return {"last_price": 105.0}

        @property
        def calendar(self):
            return {}

        @property
        def dividends(self):
            return pd.Series(dtype=float)

    monkeypatch.setattr(
        mdc, "_get_yfinance", lambda: SimpleNamespace(Ticker=FakeTicker)
    )
    cache = mdc.TickerDataCache()

    data = await cache.prefetch("BBRI")
    same_data = await cache.prefetch("bbri")

    assert calls == ["BBRI.JK"]
    assert same_data is data
    assert data["current_price"] == 105.0


@pytest.mark.asyncio
async def test_debate_run_derives_current_price_and_adds_prompt_metadata(monkeypatch):
    chamber = _chamber()
    chamber.prompt_version = "test-version"

    class FakeApp:
        async def ainvoke(self, state):
            return state

    async def fake_market_data(ticker):
        return {
            "history": pd.DataFrame({"Close": [990.0, 1010.0]}),
            "info": {},
            "fast_info": {},
            "source": "fake",
            "current_price": 1010.0,
        }

    chamber.app = FakeApp()
    monkeypatch.setattr(chamber, "_fetch_market_data", fake_market_data)

    result = await chamber.run("BBRI")

    assert result["current_price"] == 1010.0
    assert result["metadata"]["prompt_version"] == "test-version"
    assert result["metadata"]["market_data_cached"] is True


def test_prompt_registry_loads_required_prompts_and_version():
    registry = debate_prompt_registry.PROMPT_REGISTRY

    assert registry.prompt_version == "2026-06-12-bull-bear-citation-requirement-v1"
    assert set(debate_prompt_registry.REQUIRED_PROMPTS).issubset(registry.prompts)
    assert "CONFIDENCE CALIBRATION" in registry.prompts["CIO_SYSTEM_PROMPT"]


@pytest.mark.asyncio
async def test_cio_uses_decision_brief_and_redacts_debate_prices(monkeypatch):
    chamber = _chamber()
    captured = {}

    async def fake_invoke(llm, messages, inject_rules=True):
        captured["human"] = messages[-1].content
        return SimpleNamespace(
            content=json.dumps(
                {
                    "ticker": "BBRI",
                    "rating": "BUY",
                    "confidence": 0.72,
                    "summary": "Valid setup.",
                    "weighted_reasoning": "Envelope-driven decision.",
                    "key_catalysts": ["volume"],
                    "key_risks": ["breakdown"],
                    "timeframe": "1-3 Months",
                    "entry_price_range": "1 - 2",
                    "target_price": 3,
                    "stop_loss": 1,
                    "current_price": 1000,
                    "fair_value": 1200,
                    "expected_return": "+5.0%",
                    "risk_reward_ratio": 2.0,
                    "consensus_reached": False,
                    "consensus_method": None,
                    "dissenting_agents": [],
                }
            )
        )

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")

    result = await chamber._cio_judge_node(
        {
            "ticker": "BBRI",
            "current_price": 1000.0,
            "technical_indicators": {"ma50": 980, "atr14": 30},
            "fair_value_estimate": 1200.0,
            "debate_history": [
                DebateMessage(
                    role="bull", content="Buy at Rp 999 target Rp 1200", round_num=1
                )
            ],
            "raw_data": "SHOULD_NOT_LEAK_RAW_DATA",
            "decision_brief": "COMPACT_DECISION_BRIEF",
            "consensus_reached": False,
            "consensus_method": None,
            "dissenting_agents": [],
            "agent_votes": [],
            "disagreement_type": "direction",
            "devils_advocate_question": "What if support breaks?",
        }
    )

    verdict = json.loads(result["final_verdict"])
    human_prompt = captured["human"]

    assert verdict["entry_price_range"] != "1 - 2"
    assert "COMPACT_DECISION_BRIEF" in human_prompt
    assert "SHOULD_NOT_LEAK_RAW_DATA" not in human_prompt
    assert "Rp 999" not in human_prompt
    assert "Rp [REDACTED: use Python Trade Envelope]" in human_prompt


@pytest.mark.asyncio
async def test_cio_records_rag_citation_guard_when_evidence_id_missing(monkeypatch):
    chamber = _chamber()
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")
    evidence_id = "BBRI_run_1_technical_0"

    async def fake_invoke(llm, messages, inject_rules=True):
        return SimpleNamespace(
            content=json.dumps(
                {
                    "ticker": "BBRI",
                    "rating": "BUY",
                    "confidence": 0.72,
                    "summary": "Valid setup.",
                    "weighted_reasoning": "Envelope-driven decision.",
                    "key_catalysts": ["volume"],
                    "key_risks": ["breakdown"],
                    "timeframe": "1-3 Months",
                    "entry_price_range": "1 - 2",
                    "target_price": 3,
                    "stop_loss": 1,
                    "current_price": 1000,
                    "fair_value": 1200,
                    "expected_return": "+5.0%",
                    "risk_reward_ratio": 2.0,
                    "consensus_reached": False,
                    "consensus_method": None,
                    "dissenting_agents": [],
                }
            )
        )

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._cio_judge_node(
        {
            "ticker": "BBRI",
            "current_price": 1000.0,
            "technical_indicators": {"ma50": 980, "atr14": 30},
            "fair_value_estimate": 1200.0,
            "debate_history": [
                DebateMessage(role="bull", content="BUY with momentum", round_num=1)
            ],
            "raw_data": "compact raw",
            "decision_brief": f"Evidence ID: {evidence_id}\nRSI support holds.",
            "consensus_reached": False,
            "consensus_method": None,
            "dissenting_agents": [],
            "agent_votes": [],
            "disagreement_type": "direction",
            "devils_advocate_question": "What if support breaks?",
            "metadata": {
                "rag_citation_ids": [evidence_id],
                "rag_citations": [
                    {
                        "chunk_id": evidence_id,
                        "category": "technical",
                        "source": "yfinance",
                        "relevance_score": 0.95,
                        "is_stale": False,
                    }
                ],
            },
        }
    )

    verdict = json.loads(result["final_verdict"])
    guard = result["metadata"]["rag_citation_guard"]

    assert guard["valid"] is False
    assert guard["missing_citation_ids"] == []
    assert "expected at least 1 citation" in guard["errors"][0]
    assert "Evidence citation guard warning" in verdict["weighted_reasoning"]


@pytest.mark.asyncio
async def test_cio_records_malformed_rag_citation_metadata(monkeypatch):
    chamber = _chamber()
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")

    async def fake_invoke(llm, messages, inject_rules=True):
        return SimpleNamespace(
            content=json.dumps(
                {
                    "ticker": "BBRI",
                    "rating": "BUY",
                    "confidence": 0.72,
                    "summary": "Valid setup.",
                    "weighted_reasoning": "Evidence-backed decision.",
                    "key_catalysts": ["volume"],
                    "key_risks": ["breakdown"],
                    "timeframe": "1-3 Months",
                    "entry_price_range": "1 - 2",
                    "target_price": 3,
                    "stop_loss": 1,
                    "current_price": 1000,
                    "fair_value": 1200,
                    "expected_return": "+5.0%",
                    "risk_reward_ratio": 2.0,
                    "consensus_reached": False,
                    "consensus_method": None,
                    "dissenting_agents": [],
                }
            )
        )

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._cio_judge_node(
        {
            "ticker": "BBRI",
            "current_price": 1000.0,
            "technical_indicators": {"ma50": 980, "atr14": 30},
            "fair_value_estimate": 1200.0,
            "debate_history": [
                DebateMessage(role="bull", content="BUY with momentum", round_num=1)
            ],
            "raw_data": "compact raw",
            "decision_brief": "Evidence ID: BBRI_run_1_technical_0\nRSI support holds.",
            "consensus_reached": False,
            "consensus_method": None,
            "dissenting_agents": [],
            "agent_votes": [],
            "disagreement_type": "direction",
            "devils_advocate_question": "What if support breaks?",
            "metadata": {
                "rag_citation_ids": ["BBRI_run_1_technical_0"],
                "rag_citations": [
                    {
                        "chunk_id": "BBRI_run_1_technical_0",
                        "category": "unknown",
                        "relevance_score": 0.95,
                        "is_stale": False,
                    }
                ],
            },
        }
    )

    verdict = json.loads(result["final_verdict"])
    failures = result["metadata"]["rag_citation_parse_failures"]
    guard = result["metadata"]["rag_citation_guard"]

    assert failures[0]["index"] == "0"
    assert failures[0]["type"] == "ValidationError"
    assert guard["valid"] is False
    assert "RAG citation metadata invalid" in guard["errors"][0]
    assert "Evidence citation guard warning" in verdict["weighted_reasoning"]


def test_cio_verdict_rejects_invalid_price_ordering():
    with pytest.raises(ValueError, match="Invalid swing price ordering"):
        CIOVerdict(
            ticker="BAD",
            rating="BUY",
            confidence=0.7,
            entry_price_range="100 - 110",
            target_price=105,
            stop_loss=90,
            current_price=100,
            fair_value=120,
        )


def test_cio_verdict_parses_en_dash_entry_range():
    # Regression: _parse_entry_bounds had a mojibake character class that
    # silently failed on en-dash ranges, leaving risk_reward_ratio = None and
    # skipping the price-ordering invariant.
    for dash in ("–", "—"):
        verdict = CIOVerdict(
            ticker="DASH",
            rating="BUY",
            confidence=0.7,
            entry_price_range=f"95 {dash} 105",
            target_price=120,
            stop_loss=90,
            current_price=100,
            fair_value=120,
        )

        assert verdict.risk_reward_ratio == 1.0  # (120-105)/(105-90)


def test_cio_verdict_en_dash_range_still_enforces_price_ordering():
    with pytest.raises(ValueError, match="Invalid swing price ordering"):
        CIOVerdict(
            ticker="BAD",
            rating="BUY",
            confidence=0.7,
            entry_price_range="100 – 110",
            target_price=105,
            stop_loss=90,
            current_price=100,
            fair_value=120,
        )


def test_cio_verdict_uses_high_bound_for_overvalued_flag():
    verdict = CIOVerdict(
        ticker="SOFT",
        rating="BUY",
        confidence=0.7,
        entry_price_range="95 - 105",
        target_price=120,
        stop_loss=90,
        current_price=108,
        fair_value=100,
        fair_value_base=100,
        fair_value_high=115,
    )

    assert verdict.risk_overvalued is False
    assert verdict.is_overvalued is False


def test_cio_verdict_marks_risk_overvalued_above_high_bound():
    verdict = CIOVerdict(
        ticker="HARD",
        rating="BUY",
        confidence=0.7,
        entry_price_range="95 - 105",
        target_price=130,
        stop_loss=90,
        current_price=116,
        fair_value=100,
        fair_value_base=100,
        fair_value_high=115,
    )

    assert verdict.risk_overvalued is True
    assert verdict.is_overvalued is True


def test_sanitize_json_preserves_url_and_hash_inside_strings():
    raw = """
    Here is the JSON:
    {
      "summary": "Review https://example.com/path//detail before entry",
      "weighted_reasoning": "#1 catalyst is contract renewal",
    }
    Thanks.
    """

    parsed = json.loads(DebateChamber._sanitize_json(raw))

    assert parsed["summary"] == "Review https://example.com/path//detail before entry"
    assert parsed["weighted_reasoning"] == "#1 catalyst is contract renewal"


def test_llm_content_to_text_extracts_text_parts():
    content = [
        {"type": "text", "text": "First paragraph."},
        {"content": "Second paragraph."},
        SimpleNamespace(text="Third paragraph."),
    ]

    normalized = DebateChamber._llm_content_to_text(content)

    assert normalized == "First paragraph.\nSecond paragraph.\nThird paragraph."
    assert "[{'type': 'text'" not in normalized


@pytest.mark.asyncio
async def test_cio_parses_list_content_response_and_keeps_consensus_override(
    monkeypatch,
):
    chamber = _chamber()
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")

    payload = {
        "ticker": "ADRO",
        "rating": "BUY",
        "confidence": 0.83,
        "summary": "Parsed list content without fallback.",
        "weighted_reasoning": "Signals are constructive but timing remains mixed.",
        "key_catalysts": ["earnings"],
        "key_risks": ["support break"],
        "timeframe": "1-3 Months",
        "entry_price_range": "1 - 2",
        "target_price": 3,
        "stop_loss": 1,
        "current_price": 1000,
        "fair_value": 1300,
        "expected_return": "+8.0%",
        "risk_reward_ratio": 2.0,
        "consensus_reached": True,
        "consensus_method": "soft_hold",
        "dissenting_agents": ["bear"],
    }

    async def fake_invoke(llm, messages, inject_rules=True):
        return SimpleNamespace(
            content=[
                {
                    "type": "text",
                    "text": json.dumps(payload),
                }
            ]
        )

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)

    result = await chamber._cio_judge_node(
        {
            "ticker": "ADRO",
            "current_price": 1000.0,
            "technical_indicators": {"ma50": 990.0, "sma20": 1000.0, "atr14": 30.0},
            "fair_value_estimate": 1300.0,
            "debate_history": [
                DebateMessage(
                    role="bull",
                    content="Position: BUY\nAgent Confidence: 0.82",
                    round_num=2,
                ),
                DebateMessage(
                    role="bear",
                    content="Position: AVOID\nAgent Confidence: 0.88",
                    round_num=2,
                ),
            ],
            "raw_data": "compact raw",
            "decision_brief": "compact brief",
            "devils_advocate_question": "What if support breaks?",
            "consensus_reached": True,
            "consensus_method": "soft_hold",
            "dissenting_agents": ["bull", "bear"],
            "agent_votes": [],
            "disagreement_type": "timing",
        }
    )

    verdict = json.loads(result["final_verdict"])

    assert "CIO parse error" not in verdict["summary"]
    assert verdict["summary"] == "Parsed list content without fallback."
    assert verdict["rating"] == "HOLD"
    assert verdict["confidence"] == 0.55
    assert verdict["consensus_method"] == "soft_hold"


def test_cio_prompt_contains_confidence_calibration_rubric():
    prompt = dc.CIO_SYSTEM_PROMPT

    assert "CONFIDENCE CALIBRATION" in prompt
    assert "0.82" in prompt
    assert "0.78" in prompt
    assert "Caps tidak additive" in prompt
    assert "0.80-0.89" in prompt
    assert "Angka bulat" in prompt
    assert "0.70" in prompt
    assert "0.75" in prompt
    assert "0.80" in prompt
    assert "kurangi 0.10" in prompt
    assert "0.10" in prompt


def test_trade_envelope_guarantees_sufficient_rr_from_entry_high():
    chamber = _chamber()
    envelope = chamber._compute_trade_envelope(
        current_price=127.0,
        fair_value=423.0,
        tech={"ma50": 140.0, "sma20": 133.0, "atr14": 5.0},
    )

    # R/R must be calculated conservatively from entry_high (not entry_mid).
    # With the 10% swing cap (P1.10), this geometry produces R/R ~1.44.
    assert envelope["risk_reward_ratio"] >= 1.3


def test_trade_envelope_fair_value_is_hard_ceiling_not_blend():
    chamber = _chamber()

    # INDO 2026-06-11: 52w high Rp 519 vs FV Rp 253 — the old blend produced
    # target Rp 386 (above FV itself) and R/R 22.3x.
    envelope = chamber._compute_trade_envelope(
        current_price=165.0,
        fair_value=253.0,
        tech={"ma50": 162.0, "sma20": 150.0, "atr14": 6.0, "52w_high": 519.0},
    )

    assert envelope["target_price"] <= 253.0
    assert envelope["target_price"] <= envelope["entry_high"] * 1.16
    assert envelope["risk_reward_ratio"] < 5.0
    assert "(FV Ceiling)" in envelope["target_basis"]
    assert "(Swing Cap)" in envelope["target_basis"]


def test_trade_envelope_swing_cap_applies_even_with_fair_value_above_resistance():
    chamber = _chamber()

    # NZIA 2026-06-11: FV Rp 417 sits above the 52w-high target Rp 316, so the
    # FV ceiling never fired and the envelope shipped +78% / R/R 11.75x.
    envelope = chamber._compute_trade_envelope(
        current_price=177.0,
        fair_value=417.0,
        tech={"ma50": 173.0, "sma20": 160.0, "atr14": 7.0, "52w_high": 316.0},
    )

    assert envelope["target_price"] <= envelope["entry_high"] * 1.16
    assert envelope["risk_reward_ratio"] < 5.0
    assert "(Swing Cap)" in envelope["target_basis"]
    assert "(FV Ceiling)" not in envelope["target_basis"]


def test_trade_envelope_tick_fallback_preserves_ceiling_provenance():
    chamber = _chamber()

    # FV (800) below entry collapses the target; the fallback must append to
    # target_basis, not overwrite it — the audit trail has to show that the
    # FV ceiling (valuation), not tick geometry, killed the setup.
    envelope = chamber._compute_trade_envelope(
        current_price=1000.0,
        fair_value=800.0,
        tech={"ma50": 980.0, "sma20": 1000.0, "atr14": 10.0},
    )

    assert "(FV Ceiling)" in envelope["target_basis"]
    assert "Tick Increment Fallback" in envelope["target_basis"]
    assert envelope["target_price"] > envelope["entry_high"]


@pytest.mark.asyncio
async def test_fundamental_node_propagates_quality_rejection_to_metadata(monkeypatch):
    chamber = _chamber()
    chamber.flash_llm = None  # unused: _invoke_llm_for_state is patched

    async def fake_fetch(url):
        return {"data": "raw"}

    async def fake_invoke(state, llm, messages):
        return SimpleNamespace(content="analysis text")

    monkeypatch.setattr(chamber, "_fetch_url", fake_fetch)
    monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke)
    monkeypatch.setattr(
        dc,
        "build_fair_value_payload",
        lambda raw, ticker, price: (
            "report",
            {
                "fair_value": None,
                "fair_value_base": None,
                "fair_value_low": None,
                "fair_value_high": None,
                "range_pct": None,
                "risk_overvalued": False,
                "fv_quality_rejected": True,
                "fv_quality_reasons": ["fv_methods_lt_2"],
            },
        ),
    )

    partial = await chamber._fundamental_node(
        {"ticker": "NZIA", "current_price": 177.0, "metadata": {"run_id": "t1"}}
    )

    assert partial["fair_value_estimate"] is None
    # Quality rejection must surface through the same metadata fields the
    # RAG-evidence rejection uses, so report consumers treat both alike.
    meta = partial["metadata"]
    assert meta["fair_value_rejected"] is True
    assert meta["valuation_gap"] == "unverified"
    assert "fair_value_quality_rejected" in meta["reasons"]


def test_sentiment_payload_from_response_sanitizes_markdown_json():
    raw_markdown = """```json
{
  "position": "BUY",
  "confidence": 0.7,
  "status": "OK",
  "reasoning": "Social sentiment is overwhelmingly bearish, but rebound expected."
}
```"""
    payload = DebateChamber._sentiment_payload_from_response("BBRI", raw_markdown)
    assert payload["position"] == "BUY"
    assert payload["confidence"] == 0.7
    assert payload["status"] == "OK"


def test_sanitize_json_repairs_truncated_json_inside_string():
    raw_truncated = """
    {
      "position": "HOLD",
      "confidence": 0.6,
      "status": "OK",
      "reasoning": "Social sentiment is mixed, with significant bullishness observed for specific stocks, particularly BDMN, driven by speculative discussions around its potential privatization or free float increase by MUFG. Many users express high price targets and profit-taking opportunities on various stocks. However, this enthusiasm is tempered by a critical observation of abnormally low market trading volume for the broader 
    """
    sanitized = DebateChamber._sanitize_json(raw_truncated)
    parsed = json.loads(sanitized)
    assert parsed["position"] == "HOLD"
    assert parsed["confidence"] == 0.6
    assert parsed["status"] == "OK"
    assert parsed["reasoning"].startswith("Social sentiment is mixed")


def test_sanitize_json_repairs_truncated_json_with_trailing_comma():
    raw_truncated = """
    {
      "position": "SELL",
      "confidence": 0.5,
    """
    sanitized = DebateChamber._sanitize_json(raw_truncated)
    parsed = json.loads(sanitized)
    assert parsed["position"] == "SELL"
    assert parsed["confidence"] == 0.5


def test_sanitize_json_handles_single_quoted_json():
    raw_single_quotes = "{'position': 'BUY', 'confidence': 0.85}"
    sanitized = DebateChamber._sanitize_json(raw_single_quotes)
    parsed = json.loads(sanitized)
    assert parsed["position"] == "BUY"
    assert parsed["confidence"] == 0.85


# ---------------------------------------------------------------------------
# P0.1 — DEFENSIVE clamp: BUY must become HOLD when regime is DEFENSIVE
# ---------------------------------------------------------------------------

def _defensive_state(rating: str = "BUY", confidence: float = 0.75) -> dict:
    """Minimal DebateChamberState with DEFENSIVE regime in metadata."""
    return {
        "metadata": {"regime": "DEFENSIVE"},
        "technical_indicators": {},
        "agent_votes": [],
        "consensus_reached": True,
        "consensus_method": "majority",
        "dissenting_agents": [],
        "ticker": "TEST",
        "current_price": 1000.0,
        "final_verdict": json.dumps(
            {"rating": rating, "confidence": confidence, "risk_reward_ratio": 2.5}
        ),
    }


def test_defensive_clamp_buy_becomes_hold():
    """BUY with DEFENSIVE regime must be clamped to HOLD by _apply_consensus_override."""
    chamber = _chamber()
    parsed = {"rating": "BUY", "confidence": 0.75, "current_price": 1000.0, "fair_value": 800.0}
    result = chamber._apply_consensus_override(parsed, _defensive_state("BUY", 0.75))
    assert result["rating"] == "HOLD", "DEFENSIVE regime must clamp BUY to HOLD"
    assert result["confidence"] <= 0.55


def test_defensive_clamp_strong_buy_becomes_hold():
    """STRONG_BUY with DEFENSIVE regime must also be clamped to HOLD."""
    chamber = _chamber()
    parsed = {"rating": "STRONG_BUY", "confidence": 0.88, "current_price": 500.0, "fair_value": 400.0}
    result = chamber._apply_consensus_override(parsed, _defensive_state("STRONG_BUY", 0.88))
    assert result["rating"] == "HOLD", "DEFENSIVE regime must clamp STRONG_BUY to HOLD"
    assert result["confidence"] <= 0.55


def test_defensive_clamp_not_applied_in_neutral():
    """BUY in NEUTRAL regime must NOT be clamped."""
    chamber = _chamber()
    parsed = {"rating": "BUY", "confidence": 0.75, "current_price": 1000.0, "fair_value": 800.0}
    neutral_state = _defensive_state("BUY", 0.75)
    neutral_state["metadata"] = {"regime": "NEUTRAL"}
    result = chamber._apply_consensus_override(parsed, neutral_state)
    assert result["rating"] != "HOLD" or result.get("confidence", 1.0) > 0.55, (
        "NEUTRAL regime should not trigger the DEFENSIVE clamp"
    )


# ---------------------------------------------------------------------------
# P0.2 — ATR regime multiplier and noise rejection gate
# ---------------------------------------------------------------------------

def test_atr_multiplier_defensive_wider_stop_than_neutral():
    """DEFENSIVE regime uses 3.0x ATR while NEUTRAL uses 2.5x — stop must be lower.

    Inputs chosen so the k_atr candidate dominates the stop (price-sma20 gap > 1.5*atr14)
    and both envelopes clear the noise gate (stop_distance > 1.5*atr14).
    """
    chamber = _chamber()
    # current_price=1000, atr14=20, sma20=960:
    #   NEUTRAL  stop = max(940, 950)=950; distance=50 > noise_floor=30 -> OK
    #   DEFENSIVE stop = max(940, 940)=940; distance=60 > 30 -> OK
    tech_neutral = {"regime": "NEUTRAL", "atr14": 20.0, "sma20": 960.0}
    tech_defensive = {"regime": "DEFENSIVE", "atr14": 20.0, "sma20": 960.0}

    env_neutral = chamber._compute_trade_envelope(1000.0, 1100.0, tech_neutral)
    env_defensive = chamber._compute_trade_envelope(1000.0, 1100.0, tech_defensive)

    assert not env_neutral.get("rejected"), f"NEUTRAL envelope rejected unexpectedly: {env_neutral}"
    assert not env_defensive.get("rejected"), f"DEFENSIVE envelope rejected unexpectedly: {env_defensive}"
    assert env_defensive["stop_loss"] < env_neutral["stop_loss"], (
        f"DEFENSIVE stop {env_defensive['stop_loss']} should be lower than "
        f"NEUTRAL stop {env_neutral['stop_loss']} (wider buffer)"
    )


def test_atr_noise_gate_rejects_stop_inside_noise():
    """If stop_distance < 1.5 * atr14 the envelope must return rejected=True."""
    chamber = _chamber()
    # atr14=100 → noise_floor=150; entry_high≈1010; stop at ~808 → distance ≈202 → OK normally.
    # Set atr14=600 so noise_floor=900 > entry_high-stop → rejection.
    tech = {"regime": "NEUTRAL", "atr14": 600.0, "sma20": 500.0}
    result = chamber._compute_trade_envelope(1000.0, 1100.0, tech)
    assert result.get("rejected") is True, (
        f"Expected rejected=True when stop is inside noise floor, got: {result}"
    )


def test_trade_envelope_rejects_momentum_mode_with_negative_5d_return():
    """Momentum mode (RSI > 40) + negative 5d return must be rejected (F12 fix)."""
    chamber = _chamber()
    tech = {
        "ma50": 1000.0, "sma20": 980.0, "atr14": 20.0,
        "rsi14": 55.0, "return_5d_pct": -3.0,
    }
    result = chamber._compute_trade_envelope(1000.0, 1100.0, tech)
    assert result.get("rejected") is True, f"Expected rejected=True, got: {result}"
    assert "no_momentum_confirmation" in result.get("reason", "")


def test_trade_envelope_allows_mean_reversion_with_negative_5d_return():
    """Mean-reversion mode (RSI <= 40) must not be blocked by the 5d-return gate."""
    chamber = _chamber()
    tech = {
        "ma50": 1100.0, "sma20": 1050.0, "atr14": 20.0,
        "rsi14": 35.0, "return_5d_pct": -5.0,
    }
    result = chamber._compute_trade_envelope(1000.0, 1100.0, tech)
    assert not result.get("rejected"), f"Mean-reversion envelope wrongly rejected: {result}"


def test_trade_envelope_allows_positive_5d_return_in_momentum_mode():
    """Momentum mode with flat/positive 5d return must pass the gate."""
    chamber = _chamber()
    tech = {
        "ma50": 1000.0, "sma20": 980.0, "atr14": 20.0,
        "rsi14": 55.0, "return_5d_pct": 1.5,
    }
    result = chamber._compute_trade_envelope(1000.0, 1100.0, tech)
    assert not result.get("rejected"), f"Positive 5d return wrongly rejected: {result}"


@pytest.mark.asyncio
async def test_devils_advocate_appends_vote_to_agent_votes(monkeypatch):
    """_devils_advocate_node must append its AVOID/HOLD vote to agent_votes (6th entry)."""
    chamber = _chamber()

    async def fake_invoke_for_state(state, llm, messages, inject_rules=True):
        return SimpleNamespace(
            content="Worst-case macro challenge.\nPOSITION: AVOID\nCONFIDENCE: 0.40"
        )

    chamber.flash_llm = FakeLLM(model="gemini-2.5-flash")
    monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke_for_state)
    monkeypatch.setattr(dc, "DEFAULT_STORE", SimpleNamespace(append=lambda *_: None))

    prior_votes = [
        {"agent": "bull", "position": "BUY", "confidence": 0.70, "round": 1},
        {"agent": "bear", "position": "AVOID", "confidence": 0.60, "round": 1},
        {"agent": "fundamental_scout", "position": "BUY", "confidence": 0.65, "round": 1},
        {"agent": "chartist", "position": "BUY", "confidence": 0.68, "round": 1},
        {"agent": "sentiment_specialist", "position": "HOLD", "confidence": 0.55, "round": 1},
    ]

    result = await chamber._devils_advocate_node(
        {
            "ticker": "BBCA",
            "debate_history": [],
            "decision_brief": "Test brief.",
            "agent_votes": prior_votes,
            "round_count": 3,
            "metadata": {"run_id": "test_run"},
        }
    )

    votes = result["agent_votes"]
    assert len(votes) == 6, f"Expected 6 agent_votes, got {len(votes)}"
    da_vote = next((v for v in votes if v["agent"] == "devils_advocate"), None)
    assert da_vote is not None, "devils_advocate vote missing from agent_votes"
    assert da_vote["position"] in ("AVOID", "HOLD"), f"Unexpected DA position: {da_vote['position']}"
    assert 0.0 <= da_vote["confidence"] <= 1.0
    # Original 5 votes must be preserved unchanged
    original_agents = {v["agent"] for v in votes[:5]}
    assert original_agents == {"bull", "bear", "fundamental_scout", "chartist", "sentiment_specialist"}


# ---------------------------------------------------------------------------
# compute_swing_low — unit tests
# ---------------------------------------------------------------------------


def test_compute_swing_low_returns_minimum_of_window() -> None:
    series = pd.Series([100.0, 90.0, 80.0, 95.0, 105.0])
    result = dc.compute_swing_low(series, window=3)
    assert result == 80.0, f"Expected 80.0, got {result}"


def test_compute_swing_low_window_larger_than_series_uses_all() -> None:
    series = pd.Series([100.0, 90.0])
    result = dc.compute_swing_low(series, window=20)
    assert result == 90.0, f"Expected 90.0, got {result}"


# ---------------------------------------------------------------------------
# Structural stop geometry — pure arithmetic assertions
# ---------------------------------------------------------------------------


def test_structural_stop_uses_swing_low_not_just_atr() -> None:
    atr14 = 50.0
    current_price = 1000.0
    swing_low = 850.0
    k_atr = 2.5

    structural_stop = swing_low - (0.5 * atr14)   # 825
    atr_stop = current_price - (k_atr * atr14)     # 875
    stop = max(structural_stop, atr_stop)

    assert stop == 875.0, f"atr_stop should win when swing_low is far: {stop}"


def test_structural_stop_uses_swing_low_when_closer_than_atr() -> None:
    atr14 = 20.0
    current_price = 1000.0
    swing_low = 970.0
    k_atr = 2.5

    structural_stop = swing_low - (0.5 * atr14)   # 960
    atr_stop = current_price - (k_atr * atr14)     # 950
    stop = max(structural_stop, atr_stop)

    assert stop == 960.0, f"structural_stop should win when swing_low is close: {stop}"
