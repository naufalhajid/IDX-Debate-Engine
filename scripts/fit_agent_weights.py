"""
scripts/fit_agent_weights.py — Offline Brier-score calibration for agent weights.

Reads output/observations/observations.jsonl (agent predictions) and joins with
output/backtest/backtest_memory.jsonl (realized outcomes) on (run_id, ticker),
so every observation is scored against the outcome of the exact trade it argued
for. A ticker-only join would pair predictions with unrelated trades from other
runs and count the same outcome many times over.

Per (run_id, ticker, agent) only the agent's FINAL stance is scored (highest
round_num, file order as tiebreak): an early-round BUY the agent walked back to
HOLD by the end of the debate is not a BUY conviction. Only final
BUY/STRONG_BUY stances are scored, and only against closed BUY/STRONG_BUY
verdict trades.

Outcome scoring: "win" → 1.0; "loss" and "timeout_flat" → 0.0 (a BUY conviction
claims the target gets hit within the horizon — a flat expiry means it did
not). "open" trades are skipped.

Output goes to config/agents.yaml in the nested schema read by
services.debate_chamber.load_agent_calibration_weights():

    agents:
      chartist:
        calibration_weight: 0.78

The merge is surgical: unrelated top-level keys and extra per-agent keys are
preserved (YAML comments are not). Only calibration_weight is written.

Minimum 30 scored trades per agent required before updating that agent's
weight; below that the agent's weight is written as a neutral 1.0.

Run manually:
    uv run python scripts/fit_agent_weights.py
    uv run python scripts/fit_agent_weights.py --observations path/to/obs.jsonl \\
                                               --backtest path/to/memory.jsonl \\
                                               --output config/agents.yaml
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import yaml

_DEFAULT_OBSERVATIONS = Path("output/observations/observations.jsonl")
_DEFAULT_BACKTEST = Path("output/backtest/backtest_memory.jsonl")
_DEFAULT_OUTPUT = Path("config/agents.yaml")
_MIN_SAMPLES = 30
_BUY_RATINGS = {"BUY", "STRONG_BUY"}


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _build_outcome_map(backtest_records: list[dict]) -> dict[tuple[str, str], str]:
    """Return {(run_id, ticker): outcome} for closed BUY/STRONG_BUY trades.

    Keyed by (run_id, ticker) so an observation can only match the trade from
    its own run. When the same trade appears more than once (re-evaluation
    appends a corrected record), last-write wins.
    """
    outcome_map: dict[tuple[str, str], str] = {}
    for rec in backtest_records:
        outcome = str(rec.get("outcome") or "")
        if outcome in ("", "open"):
            continue
        rating = str(rec.get("verdict_rating") or "").upper()
        if rating not in _BUY_RATINGS:
            continue
        run_id = str(rec.get("run_id") or "")
        ticker = str(rec.get("ticker") or "").upper()
        if run_id and ticker:
            outcome_map[(run_id, ticker)] = outcome
    return outcome_map


def _final_stances(observations: list[dict]) -> dict[tuple[str, str, str], dict]:
    """Return {(run_id, ticker, agent): observation} keeping each agent's
    final stance per trade — highest round_num, later file position as
    tiebreak."""
    final: dict[tuple[str, str, str], dict] = {}
    rank: dict[tuple[str, str, str], tuple[int, int]] = {}
    for seq, obs in enumerate(observations):
        run_id = str(obs.get("run_id") or "")
        ticker = str(obs.get("ticker") or "").upper()
        agent = str(obs.get("agent") or "")
        if not run_id or not ticker or not agent:
            continue
        try:
            round_num = int(obs.get("round_num") or 0)
        except (TypeError, ValueError):
            round_num = 0
        key = (run_id, ticker, agent)
        candidate = (round_num, seq)
        if key not in rank or candidate > rank[key]:
            rank[key] = candidate
            final[key] = obs
    return final


def _collect_brier_samples(
    observations: list[dict],
    outcome_map: dict[tuple[str, str], str],
) -> dict[str, list[float]]:
    """Brier scores per agent — one sample per (run_id, ticker) trade whose
    final stance was BUY/STRONG_BUY and whose trade outcome is closed."""
    agent_errors: dict[str, list[float]] = defaultdict(list)
    for (run_id, ticker, agent), obs in _final_stances(observations).items():
        position = str(obs.get("position") or "").upper()
        if position not in _BUY_RATINGS:
            continue
        outcome = outcome_map.get((run_id, ticker))
        if outcome is None:
            continue
        try:
            confidence = float(obs.get("confidence") or 0.0)
        except (TypeError, ValueError):
            continue
        win = 1.0 if outcome == "win" else 0.0
        agent_errors[agent].append((confidence - win) ** 2)
    return agent_errors


def _merge_calibration_into_config(
    config: dict, weights: dict[str, float]
) -> dict:
    """Set agents.<name>.calibration_weight, preserving all unrelated keys.

    A legacy flat entry (agent name mapped straight to a float) is upgraded to
    the nested {calibration_weight: X} form the runtime loader reads.
    """
    agents = config.get("agents")
    if not isinstance(agents, dict):
        agents = {}
    for name, weight in weights.items():
        entry = agents.get(name)
        if not isinstance(entry, dict):
            entry = {}
        entry["calibration_weight"] = weight
        agents[name] = entry
    config["agents"] = agents
    return config


def fit_weights(
    observations_path: Path = _DEFAULT_OBSERVATIONS,
    backtest_path: Path = _DEFAULT_BACKTEST,
    output_path: Path = _DEFAULT_OUTPUT,
) -> dict[str, float]:
    if not observations_path.exists():
        raise FileNotFoundError(f"Observations not found: {observations_path}")
    if not backtest_path.exists():
        raise FileNotFoundError(f"Backtest memory not found: {backtest_path}")

    observations = _load_jsonl(observations_path)
    backtest = _load_jsonl(backtest_path)
    outcome_map = _build_outcome_map(backtest)
    agent_errors = _collect_brier_samples(observations, outcome_map)

    print(f"Closed BUY trades joinable on (run_id, ticker): {len(outcome_map)}")

    # Compute weights: lower Brier score → higher weight (inverse Brier, normalized)
    weights: dict[str, float] = {}
    raw_scores: dict[str, float] = {}
    for agent, errors in agent_errors.items():
        if len(errors) < _MIN_SAMPLES:
            print(
                f"  {agent}: {len(errors)} scored trades < {_MIN_SAMPLES} minimum "
                "— defaulting to 1.0"
            )
            weights[agent] = 1.0
        else:
            mean_brier = sum(errors) / len(errors)
            raw_scores[agent] = mean_brier
            print(f"  {agent}: n={len(errors)}, mean_brier={mean_brier:.4f}")

    if raw_scores:
        min_score = min(raw_scores.values())
        for agent, score in raw_scores.items():
            # Normalize: best agent gets 1.0, others scale down proportionally
            weights[agent] = round(min_score / score if score > 0 else 1.0, 4)

    config: dict = {}
    if output_path.exists():
        with output_path.open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                config = loaded
    config = _merge_calibration_into_config(config, weights)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=True)

    merged: dict[str, float] = {}
    for name, entry in config["agents"].items():
        if not isinstance(entry, dict):
            continue
        raw = entry.get("calibration_weight")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            merged[name] = float(raw)

    print(f"\nWeights written to {output_path}:")
    for agent, w in sorted(merged.items()):
        print(f"  {agent}: {w}")

    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit agent calibration weights via Brier score."
    )
    parser.add_argument("--observations", type=Path, default=_DEFAULT_OBSERVATIONS)
    parser.add_argument("--backtest", type=Path, default=_DEFAULT_BACKTEST)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    args = parser.parse_args()
    fit_weights(args.observations, args.backtest, args.output)


if __name__ == "__main__":
    main()
