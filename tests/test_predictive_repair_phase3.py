"""Regression tests for Phase 3 directional-consensus semantics."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.debate_chamber as dc
from core.orchestrator.legacy import _generate_mock_debate_results
from services.debate_chamber import DebateChamber


def _vote(agent: str, position: str, confidence: float) -> dict[str, object]:
    return {
        "agent": agent,
        "position": position,
        "confidence": confidence,
        "calibration_weight": 1.0,
        "effective_confidence": confidence,
        "round": 1,
    }


@pytest.mark.parametrize("round_count", [1, 2, 3])
def test_three_of_four_directional_votes_reach_buy_in_every_round(
    round_count: int,
) -> None:
    chamber = object.__new__(DebateChamber)
    votes = [
        _vote("fundamental_scout", "HOLD", 1.0),
        _vote("chartist", "BUY", 0.90),
        _vote("sentiment_specialist", "BUY", 0.80),
        _vote("bull", "BUY", 0.95),
        _vote("bear", "HOLD", 0.70),
    ]

    result = chamber._evaluate_consensus_votes(votes, round_count=round_count)

    assert result["consensus_reached"] is True
    assert result["consensus_method"] == "voting"
    assert result["consensus_winner"]["position"] == "BUY"
    assert result["dissenting_agents"] == ["bear"]


def test_one_abstention_recomputes_threshold_as_three_of_three() -> None:
    chamber = object.__new__(DebateChamber)
    votes = [
        _vote("fundamental_scout", "HOLD", 1.0),
        _vote("chartist", "BUY", 0.90),
        _vote("sentiment_specialist", "BUY", 0.80),
        _vote("bull", "BUY", 0.95),
        _vote("bear", "UNKNOWN", 0.0),
    ]

    result = chamber._evaluate_consensus_votes(votes, round_count=1)

    assert result["consensus_reached"] is True
    assert result["consensus_winner"]["position"] == "BUY"
    assert result["dissenting_agents"] == []


def test_two_of_three_participants_is_below_minimum_support() -> None:
    chamber = object.__new__(DebateChamber)
    votes = [
        _vote("chartist", "BUY", 0.90),
        _vote("sentiment_specialist", "BUY", 0.80),
        _vote("bull", "HOLD", 0.70),
        _vote("bear", "UNKNOWN", 0.0),
    ]

    result = chamber._evaluate_consensus_votes(votes, round_count=1)

    assert result["consensus_reached"] is False
    assert result["consensus_method"] is None


def test_collected_voter_roster_is_exactly_four_directional_agents() -> None:
    chamber = object.__new__(DebateChamber)
    votes = chamber._collect_agent_votes(
        {
            "round_count": 1,
            "fundamental_data": "Position: HOLD\nQuality Flag: PASS",
            "technical_data": "Position: BUY\nAgent Confidence: 0.80",
            "sentiment_data": "Position: HOLD\nAgent Confidence: 0.60",
            "debate_history": [],
        }
    )

    assert {str(vote["agent"]) for vote in votes} == {
        "chartist",
        "sentiment_specialist",
        "bull",
        "bear",
    }


def test_nondirectional_agents_neither_count_nor_win_confidence_tiebreak() -> None:
    chamber = object.__new__(DebateChamber)
    votes = [
        _vote("fundamental_scout", "BUY", 1.0),
        _vote("devils_advocate", "HOLD", 1.0),
        _vote("chartist", "BUY", 0.40),
        _vote("sentiment_specialist", "HOLD", 0.50),
        _vote("bull", "HOLD", 0.20),
        _vote("bear", "AVOID", 0.70),
    ]

    result = chamber._evaluate_consensus_votes(votes, round_count=3)

    assert result["consensus_reached"] is False
    assert result["consensus_method"] == "confidence_winner"
    assert result["consensus_winner"]["agent"] == "bear"
    assert result["dissenting_agents"] == [
        "chartist",
        "sentiment_specialist",
        "bull",
    ]


@pytest.mark.asyncio
async def test_fundamental_quality_fail_vetoes_directional_buy() -> None:
    chamber = object.__new__(DebateChamber)
    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 1,
            "fundamental_data": (
                "Position: HOLD\n"
                "Quality Flag: FAIL\n"
                "Agent Confidence: 0.90"
            ),
            "technical_data": "Position: BUY\nAgent Confidence: 0.90",
            "sentiment_data": "Position: BUY\nAgent Confidence: 0.80",
            "debate_history": [
                dc.DebateMessage(
                    role="bull",
                    content="Position: BUY\nAgent Confidence: 0.95",
                    round_num=1,
                ),
                dc.DebateMessage(
                    role="bear",
                    content="Position: HOLD\nAgent Confidence: 0.70",
                    round_num=1,
                ),
            ],
            "metadata": {},
        }
    )

    assert result["consensus_reached"] is True
    assert result["consensus_method"] == "quality_veto"
    assert result["consensus_winner"]["position"] == "HOLD"
    assert result["metadata"]["fundamental_quality_flag"] == "FAIL"


def test_soft_hold_requires_bull_bear_gap_below_fifteen_percent() -> None:
    chamber = object.__new__(DebateChamber)
    votes = [
        _vote("bull", "BUY", 0.60),
        _vote("bear", "AVOID", 0.78),
    ]

    result = chamber._evaluate_consensus_votes(votes, round_count=2)

    assert result["consensus_reached"] is False
    assert result["consensus_method"] is None


def test_soft_hold_accepts_fourteen_percent_gap() -> None:
    chamber = object.__new__(DebateChamber)
    votes = [
        _vote("bull", "BUY", 0.60),
        _vote("bear", "AVOID", 0.74),
    ]

    result = chamber._evaluate_consensus_votes(votes, round_count=2)

    assert result["consensus_reached"] is True
    assert result["consensus_method"] == "soft_hold"


def test_all_directional_abstentions_have_no_confidence_winner() -> None:
    chamber = object.__new__(DebateChamber)
    votes = [
        _vote(agent, "UNKNOWN", 0.0)
        for agent in ("chartist", "sentiment_specialist", "bull", "bear")
    ]
    votes.append(_vote("fundamental_scout", "BUY", 1.0))

    result = chamber._evaluate_consensus_votes(votes, round_count=3)

    assert result["consensus_method"] is None
    assert result["consensus_winner"] is None


def test_quality_veto_override_forces_hold() -> None:
    chamber = object.__new__(DebateChamber)

    result = chamber._apply_consensus_override(
        {"rating": "BUY", "confidence": 0.90, "weighted_reasoning": "Bullish."},
        {
            "consensus_reached": True,
            "consensus_method": "quality_veto",
            "consensus_winner": {
                "agent": "fundamental_quality_veto",
                "position": "HOLD",
            },
            "dissenting_agents": ["chartist", "sentiment_specialist", "bull"],
        },
    )

    assert result["rating"] == "HOLD"
    assert result["confidence"] <= 0.55
    assert "Quality Flag FAIL" in result["weighted_reasoning"]


def test_dry_run_payload_uses_validated_rating_and_directional_roster() -> None:
    result = _generate_mock_debate_results(
        ["BBCA"],
        {"BBCA": "banking"},
    )[0]
    votes = result["agent_votes"]
    expected_position = (
        "BUY" if result["verdict"]["rating"] in {"BUY", "STRONG_BUY"} else "HOLD"
    )

    assert [vote["agent"] for vote in votes] == [
        "chartist",
        "sentiment_specialist",
        "bull",
        "bear",
    ]
    assert result["consensus_winner"]["agent"] != "fundamental_scout"
    assert result["consensus_winner"]["position"] == expected_position
    assert result["debate_history"][0]["position"] == expected_position
    assert result["debate_history"][-1]["position"] == "UNKNOWN"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "position": "HOLD",
            "confidence": 0.0,
            "status": "INSUFFICIENT_DATA",
        },
        {
            "position": "HOLD",
            "confidence": 0.0,
            "status": "PARSE_ERROR",
        },
    ],
)
def test_unusable_sentiment_evidence_is_an_abstention(
    payload: dict[str, object],
) -> None:
    signal = DebateChamber._sentiment_signal_from_payload(payload)

    assert signal == {"position": "UNKNOWN", "confidence": 0.0}


def test_malformed_sentiment_response_is_serialized_as_unknown() -> None:
    payload = DebateChamber._sentiment_payload_from_response(
        "BBCA",
        "BUY karena ramai dibahas, confidence tinggi",
    )
    signal = DebateChamber._sentiment_signal_from_payload(payload)

    assert payload["position"] == "UNKNOWN"
    assert payload["status"] == "PARSE_ERROR"
    assert signal["position"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_devils_advocate_stays_advisory_and_does_not_append_a_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamber = object.__new__(DebateChamber)
    chamber.flash_llm = object()

    async def fake_invoke_for_state(state, llm, messages, inject_rules=True):
        return SimpleNamespace(
            content=(
                "Worst-case macro challenge.\n"
                "Position: STRESS_TEST\n"
                "Agent Confidence: 0.80"
            )
        )

    monkeypatch.setattr(chamber, "_invoke_llm_for_state", fake_invoke_for_state)
    monkeypatch.setattr(dc, "DEFAULT_STORE", SimpleNamespace(append=lambda *_: None))

    result = await chamber._devils_advocate_node(
        {
            "ticker": "BBCA",
            "debate_history": [],
            "decision_brief": "Test brief.",
            "agent_votes": [_vote("bull", "BUY", 0.70)],
            "round_count": 3,
            "metadata": {"run_id": "test_run"},
        }
    )

    assert "agent_votes" not in result
    assert result["debate_history"][0].position == "UNKNOWN"
    assert "Worst-case macro challenge" in result["devils_advocate_question"]
