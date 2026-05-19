"""Golden-case harness for checking debate output invariants without live LLMs."""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import BaseModel, ConfigDict


class ExpectedConsensus(BaseModel):
    """Expected consensus shape for a golden ticker case."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    allowed_ratings: list[str]
    min_confidence: float
    must_have_verdict: bool
    max_dissenting_agents: int


class EvalCase(BaseModel):
    """Single golden evaluation case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    description: str
    expected: ExpectedConsensus
    mock_state: dict[str, Any]


class EvalResult(BaseModel):
    """Result of evaluating one golden case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    ticker: str
    passed: bool
    failures: list[str]
    actual_rating: str | None
    actual_confidence: float | None
    actual_dissent_count: int


class AgentEvalHarness:
    """Evaluate mock DebateChamber-like states against golden expectations."""

    def __init__(self, cases: list[EvalCase]):
        self.cases = cases

    def evaluate_one(self, case: EvalCase) -> EvalResult:
        """Check one mock state against its expected consensus shape."""
        failures: list[str] = []
        expected = case.expected
        verdict = _parse_final_verdict(case.mock_state.get("final_verdict"), failures)
        dissent_count = _dissent_count(case.mock_state)

        if expected.must_have_verdict and verdict is None:
            failures.append("final_verdict is required but missing")

        actual_rating = _normalise_rating(verdict.get("rating")) if verdict else None
        actual_confidence = _coerce_confidence(verdict.get("confidence")) if verdict else None

        allowed_ratings = {_normalise_rating(rating) for rating in expected.allowed_ratings}
        if verdict is not None and actual_rating not in allowed_ratings:
            failures.append(
                f"rating {actual_rating!r} not in allowed ratings {sorted(allowed_ratings)}"
            )

        if verdict is not None and actual_confidence is None:
            failures.append("confidence is missing or invalid")
        elif (
            actual_confidence is not None
            and actual_confidence < expected.min_confidence
        ):
            failures.append(
                f"confidence {actual_confidence:.3f} below minimum "
                f"{expected.min_confidence:.3f}"
            )

        if dissent_count > expected.max_dissenting_agents:
            failures.append(
                f"dissent count {dissent_count} exceeds maximum "
                f"{expected.max_dissenting_agents}"
            )

        return EvalResult(
            case_id=case.case_id,
            ticker=expected.ticker,
            passed=not failures,
            failures=failures,
            actual_rating=actual_rating,
            actual_confidence=actual_confidence,
            actual_dissent_count=dissent_count,
        )

    def evaluate_all(self) -> list[EvalResult]:
        """Evaluate every configured golden case."""
        return [self.evaluate_one(case) for case in self.cases]

    def summary(self, results: list[EvalResult]) -> dict:
        """Return aggregate pass/fail statistics for evaluation results."""
        total = len(results)
        passed = sum(1 for result in results if result.passed)
        failed = total - passed
        pass_rate = round(passed / total, 3) if total else 0.0
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
        }


def _parse_final_verdict(raw_verdict: Any, failures: list[str]) -> dict[str, Any] | None:
    if raw_verdict is None or raw_verdict == "":
        return None
    if isinstance(raw_verdict, dict):
        return raw_verdict
    if isinstance(raw_verdict, str):
        try:
            parsed = json.loads(raw_verdict)
        except json.JSONDecodeError as exc:
            failures.append(f"final_verdict is not valid JSON: {exc}")
            return None
        if isinstance(parsed, dict):
            return parsed
    failures.append("final_verdict must be a JSON object")
    return None


def _normalise_rating(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().upper().replace(" ", "_")


def _coerce_confidence(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dissent_count(mock_state: dict[str, Any]) -> int:
    dissenting_agents = mock_state.get("dissenting_agents")
    if isinstance(dissenting_agents, list):
        return len(dissenting_agents)
    return 0


GOLDEN_CASES: list[EvalCase] = [
    EvalCase(
        case_id="strong_buy",
        description="BUY verdict with strong confidence and no dissent.",
        expected=ExpectedConsensus(
            ticker="BBCA",
            allowed_ratings=["BUY"],
            min_confidence=0.70,
            must_have_verdict=True,
            max_dissenting_agents=0,
        ),
        mock_state={
            "final_verdict": json.dumps({"rating": "BUY", "confidence": 0.84}),
            "dissenting_agents": [],
        },
    ),
    EvalCase(
        case_id="hold_dissent",
        description="HOLD verdict is acceptable when dissent remains bounded.",
        expected=ExpectedConsensus(
            ticker="BBRI",
            allowed_ratings=["HOLD"],
            min_confidence=0.50,
            must_have_verdict=True,
            max_dissenting_agents=2,
        ),
        mock_state={
            "final_verdict": json.dumps({"rating": "HOLD", "confidence": 0.58}),
            "dissenting_agents": ["bull"],
        },
    ),
    EvalCase(
        case_id="avoid_overvalued",
        description="AVOID verdict for an overvalued setup with manageable dissent.",
        expected=ExpectedConsensus(
            ticker="GOTO",
            allowed_ratings=["AVOID"],
            min_confidence=0.55,
            must_have_verdict=True,
            max_dissenting_agents=1,
        ),
        mock_state={
            "final_verdict": json.dumps({"rating": "AVOID", "confidence": 0.67}),
            "dissenting_agents": [],
        },
    ),
]


def main() -> int:
    harness = AgentEvalHarness(GOLDEN_CASES)
    results = harness.evaluate_all()
    print(json.dumps(harness.summary(results), indent=2, ensure_ascii=False))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
