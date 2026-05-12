import json

from core.agent_eval_harness import (
    AgentEvalHarness,
    EvalCase,
    ExpectedConsensus,
    GOLDEN_CASES,
)


def _case(
    *,
    rating: str = "BUY",
    confidence: float = 0.75,
    allowed_ratings: list[str] | None = None,
    min_confidence: float = 0.5,
    dissenting_agents: list[str] | None = None,
    max_dissenting_agents: int = 1,
) -> EvalCase:
    return EvalCase(
        case_id="synthetic",
        description="Synthetic harness test case.",
        expected=ExpectedConsensus(
            ticker="BBCA",
            allowed_ratings=allowed_ratings or ["BUY"],
            min_confidence=min_confidence,
            must_have_verdict=True,
            max_dissenting_agents=max_dissenting_agents,
        ),
        mock_state={
            "final_verdict": json.dumps(
                {
                    "rating": rating,
                    "confidence": confidence,
                }
            ),
            "dissenting_agents": dissenting_agents or [],
        },
    )


def test_evaluate_one_passes_for_correct_mock_state() -> None:
    harness = AgentEvalHarness([])

    result = harness.evaluate_one(_case())

    assert result.passed is True
    assert result.failures == []
    assert result.actual_rating == "BUY"
    assert result.actual_confidence == 0.75
    assert result.actual_dissent_count == 0


def test_evaluate_one_fails_when_rating_not_allowed() -> None:
    harness = AgentEvalHarness([])

    result = harness.evaluate_one(_case(rating="AVOID", allowed_ratings=["BUY"]))

    assert result.passed is False
    assert any("not in allowed ratings" in failure for failure in result.failures)


def test_evaluate_one_fails_when_confidence_below_minimum() -> None:
    harness = AgentEvalHarness([])

    result = harness.evaluate_one(_case(confidence=0.49, min_confidence=0.5))

    assert result.passed is False
    assert any("below minimum" in failure for failure in result.failures)


def test_evaluate_one_fails_when_dissent_count_exceeds_maximum() -> None:
    harness = AgentEvalHarness([])

    result = harness.evaluate_one(
        _case(
            dissenting_agents=["bull", "bear"],
            max_dissenting_agents=1,
        )
    )

    assert result.passed is False
    assert any("dissent count" in failure for failure in result.failures)


def test_evaluate_all_returns_one_result_per_case() -> None:
    harness = AgentEvalHarness(GOLDEN_CASES)

    results = harness.evaluate_all()

    assert len(results) == len(GOLDEN_CASES)
    assert all(result.passed for result in results)


def test_summary_pass_rate_is_correct() -> None:
    harness = AgentEvalHarness([])
    results = [
        harness.evaluate_one(_case(rating=case_rating, allowed_ratings=allowed))
        for case_rating, allowed in (
            ("BUY", ["BUY"]),
            ("HOLD", ["HOLD"]),
            ("AVOID", ["BUY"]),
        )
    ]

    assert harness.summary(results) == {
        "total": 3,
        "passed": 2,
        "failed": 1,
        "pass_rate": 0.667,
    }
