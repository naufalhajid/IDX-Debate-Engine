"""Reliability tests for services/debate_chamber.py."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from tenacity import stop_after_attempt, wait_fixed

from schemas.debate import DebateMessage
from services import debate_chamber as dc
from services.debate_chamber import DebateChamber


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
async def test_llm_retry_charges_budget_per_attempt(monkeypatch):
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

    retrying_call = chamber._invoke_llm_with_retry.retry_with(
        wait=wait_fixed(0),
        stop=stop_after_attempt(3),
    )

    response = await retrying_call(chamber, llm, [], "flash")

    assert response.content == "usable response"
    assert llm.calls == 3
    assert len(charges) == 3


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
async def test_consensus_json_false(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        return SimpleNamespace(content='{"consensus_reached": false}')

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

    assert result == {"consensus_reached": False}


@pytest.mark.asyncio
async def test_consensus_text_not_true_defaults_false(monkeypatch):
    chamber = _chamber()

    async def fake_invoke(llm, messages, inject_rules=True):
        return SimpleNamespace(content="not true")

    monkeypatch.setattr(chamber, "_invoke_llm", fake_invoke)
    chamber.pro_llm = FakeLLM(model="gemini-2.5-pro")

    result = await chamber._consensus_evaluator_node(
        {
            "round_count": 1,
            "debate_history": [
                DebateMessage(role="bull", content="Hold", round_num=1),
                DebateMessage(role="bear", content="Hold, but risks remain", round_num=1),
            ],
        }
    )

    assert result == {"consensus_reached": False}


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


def test_cio_prompt_contains_confidence_calibration_rubric():
    prompt = dc.CIO_SYSTEM_PROMPT

    assert "CONFIDENCE CALIBRATION" in prompt
    assert "0.82" in prompt
    assert "0.78" in prompt
    assert "Caps are not additive" in prompt
    assert "0.80-0.82" in prompt
    assert "Avoid round/default numbers" in prompt
    assert "0.70" in prompt
    assert "0.75" in prompt
    assert "0.80" in prompt
    assert "subtract exactly" in prompt
    assert "0.10" in prompt
