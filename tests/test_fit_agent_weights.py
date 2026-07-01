"""Tests for scripts/fit_agent_weights.py — E4 fixes.

Covers the two plumbing regressions (output path and YAML schema must match
services.debate_chamber.load_agent_calibration_weights) and the corrected join
semantics: observations are scored against the trade from their own run via
(run_id, ticker), one final stance per agent per trade.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.fit_agent_weights import (
    _build_outcome_map,
    _collect_brier_samples,
    _final_stances,
    fit_weights,
)
from services.debate_chamber import (
    AGENT_CALIBRATION_CONFIG_PATH,
    load_agent_calibration_weights,
)


def _obs(
    run_id: str,
    ticker: str,
    agent: str = "bull",
    position: str = "BUY",
    confidence: float = 0.8,
    round_num: int = 1,
) -> dict:
    return {
        "run_id": run_id,
        "ticker": ticker,
        "agent": agent,
        "position": position,
        "confidence": confidence,
        "round_num": round_num,
    }


def _trade(
    run_id: str, ticker: str, rating: str = "BUY", outcome: str = "win"
) -> dict:
    return {
        "run_id": run_id,
        "ticker": ticker,
        "verdict_rating": rating,
        "outcome": outcome,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(rec) + "\n" for rec in records), encoding="utf-8"
    )


def test_default_output_matches_runtime_config_path() -> None:
    # Regression: the script used to write core/agent_calibration_weights.yaml,
    # which the runtime loader never reads.
    from scripts.fit_agent_weights import _DEFAULT_OUTPUT

    assert _DEFAULT_OUTPUT == AGENT_CALIBRATION_CONFIG_PATH


def test_outcome_map_is_keyed_by_run_id_and_ticker() -> None:
    records = [
        _trade("run1", "AAAA", outcome="win"),
        _trade("run2", "AAAA", outcome="loss"),  # same ticker, different trade
        _trade("run3", "BBBB", outcome="open"),  # unresolved — skipped
        _trade("run4", "CCCC", rating="HOLD"),  # not a BUY verdict — skipped
    ]

    outcome_map = _build_outcome_map(records)

    assert outcome_map == {
        ("run1", "AAAA"): "win",
        ("run2", "AAAA"): "loss",
    }


def test_final_stance_keeps_highest_round_regardless_of_file_order() -> None:
    observations = [
        _obs("run1", "AAAA", round_num=2, position="HOLD", confidence=0.3),
        _obs("run1", "AAAA", round_num=0, position="BUY", confidence=0.9),
    ]

    final = _final_stances(observations)

    assert final[("run1", "AAAA", "bull")]["position"] == "HOLD"


def test_brier_samples_score_one_final_buy_stance_per_trade() -> None:
    outcome_map = {("run1", "AAAA"): "win"}
    observations = [
        # Three rounds of the same trade — only the final BUY counts, once.
        _obs("run1", "AAAA", round_num=0, confidence=0.5),
        _obs("run1", "AAAA", round_num=1, confidence=0.6),
        _obs("run1", "AAAA", round_num=2, confidence=0.9),
        # Same ticker in a run with no closed trade — must not join.
        _obs("run9", "AAAA", round_num=1, confidence=0.9),
    ]

    samples = _collect_brier_samples(observations, outcome_map)

    assert samples["bull"] == [pytest.approx((0.9 - 1.0) ** 2)]


def test_brier_samples_exclude_retracted_buy() -> None:
    # A round-0 BUY the agent walked back to HOLD by round 2 is not a BUY
    # conviction and must not be scored.
    outcome_map = {("run1", "AAAA"): "loss"}
    observations = [
        _obs("run1", "AAAA", round_num=0, position="BUY", confidence=0.9),
        _obs("run1", "AAAA", round_num=2, position="HOLD", confidence=0.4),
    ]

    samples = _collect_brier_samples(observations, outcome_map)

    assert "bull" not in samples


def test_fit_weights_output_is_readable_by_runtime_loader(tmp_path: Path) -> None:
    # The core E4 regression: the fitted YAML must round-trip through the
    # actual runtime loader, not just be written somewhere.
    trades: list[dict] = []
    observations: list[dict] = []
    for i in range(30):
        run_id = f"run{i}"
        ticker = f"TK{i:02d}"
        trades.append(_trade(run_id, ticker, outcome="win"))
        # bull well-calibrated (0.9 on wins), chartist poorly (0.4 on wins)
        observations.append(_obs(run_id, ticker, agent="bull", confidence=0.9))
        observations.append(_obs(run_id, ticker, agent="chartist", confidence=0.4))

    obs_path = tmp_path / "observations.jsonl"
    bt_path = tmp_path / "backtest_memory.jsonl"
    out_path = tmp_path / "agents.yaml"
    _write_jsonl(obs_path, observations)
    _write_jsonl(bt_path, trades)

    fit_weights(obs_path, bt_path, out_path)
    loaded = load_agent_calibration_weights(out_path)

    # mean brier: bull (0.9-1)^2 = 0.01 (best), chartist (0.4-1)^2 = 0.36
    assert loaded["bull"] == 1.0
    assert loaded["chartist"] == pytest.approx(0.01 / 0.36, abs=1e-4)
    # Agents absent from the file keep their hardcoded defaults.
    assert loaded["bear"] == 0.85
    assert loaded["sentiment_specialist"] == 1.0


def test_fit_weights_merge_preserves_unrelated_config(tmp_path: Path) -> None:
    out_path = tmp_path / "agents.yaml"
    out_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "bull": {"calibration_weight": 0.5, "note": "hand-tuned"},
                },
                "other_section": {"keep": True},
            }
        ),
        encoding="utf-8",
    )
    obs_path = tmp_path / "observations.jsonl"
    bt_path = tmp_path / "backtest_memory.jsonl"
    _write_jsonl(obs_path, [_obs("run1", "AAAA", confidence=0.9)])
    _write_jsonl(bt_path, [_trade("run1", "AAAA", outcome="win")])

    fit_weights(obs_path, bt_path, out_path)

    config = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert config["other_section"] == {"keep": True}
    assert config["agents"]["bull"]["note"] == "hand-tuned"
    # One scored trade < 30 minimum — bull is written as a neutral 1.0.
    assert config["agents"]["bull"]["calibration_weight"] == 1.0
