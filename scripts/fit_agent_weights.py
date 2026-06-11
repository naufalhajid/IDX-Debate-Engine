"""
scripts/fit_agent_weights.py — Offline Brier-score calibration for agent weights.

Reads output/observations/observations.jsonl (agent predictions) and joins with
output/backtest/backtest_memory.jsonl (realized outcomes) on ticker. Computes
Brier score per agent for BUY/STRONG_BUY observations, then writes inverse-Brier
weights to core/agent_calibration_weights.yaml.

Minimum 30 BUY-only samples per agent required before updating that agent's weight;
otherwise the agent's weight stays at 1.0 (no data yet).

Run manually:
    python scripts/fit_agent_weights.py
    python scripts/fit_agent_weights.py --observations path/to/obs.jsonl \\
                                        --backtest path/to/memory.jsonl \\
                                        --output core/agent_calibration_weights.yaml
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import yaml

_DEFAULT_OBSERVATIONS = Path("output/observations/observations.jsonl")
_DEFAULT_BACKTEST = Path("output/backtest/backtest_memory.jsonl")
_DEFAULT_OUTPUT = Path("core/agent_calibration_weights.yaml")
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


def _build_outcome_map(backtest_records: list[dict]) -> dict[str, str]:
    """Return {ticker: outcome} for closed (non-open) BUY/STRONG_BUY records.

    When a ticker has multiple closed records, last-write wins (most recent run).
    """
    outcome_map: dict[str, str] = {}
    for rec in backtest_records:
        outcome = rec.get("outcome", "")
        if outcome == "open":
            continue
        rating = str(rec.get("verdict_rating") or "").upper()
        if rating not in _BUY_RATINGS:
            continue
        ticker = str(rec.get("ticker") or "").upper()
        if ticker:
            outcome_map[ticker] = outcome
    return outcome_map


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

    # Accumulate squared errors per agent: {agent: [brier_score, ...]}
    agent_errors: dict[str, list[float]] = defaultdict(list)

    for obs in observations:
        position = str(obs.get("position") or "").upper()
        if position not in _BUY_RATINGS:
            continue
        ticker = str(obs.get("ticker") or "").upper()
        outcome = outcome_map.get(ticker)
        if outcome is None:
            continue  # no closed outcome yet
        win = 1.0 if outcome == "win" else 0.0
        try:
            confidence = float(obs.get("confidence") or 0.0)
        except (TypeError, ValueError):
            continue
        agent = str(obs.get("agent") or "unknown")
        brier = (confidence - win) ** 2
        agent_errors[agent].append(brier)

    # Compute weights: lower Brier score → higher weight (inverse Brier, normalized)
    weights: dict[str, float] = {}
    raw_scores: dict[str, float] = {}
    for agent, errors in agent_errors.items():
        if len(errors) < _MIN_SAMPLES:
            print(
                f"  {agent}: {len(errors)} samples < {_MIN_SAMPLES} minimum — defaulting to 1.0"
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

    # Merge with existing weights (keep agents not in this run at 1.0)
    existing: dict[str, float] = {}
    if output_path.exists():
        with output_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            existing = data.get("agents", {})

    merged = {**existing, **weights}
    output = {"agents": merged}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        yaml.dump(output, f, default_flow_style=False, sort_keys=True)

    print(f"\nWeights written to {output_path}:")
    for agent, w in sorted(merged.items()):
        print(f"  {agent}: {w}")

    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit agent calibration weights via Brier score.")
    parser.add_argument("--observations", type=Path, default=_DEFAULT_OBSERVATIONS)
    parser.add_argument("--backtest", type=Path, default=_DEFAULT_BACKTEST)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    args = parser.parse_args()
    fit_weights(args.observations, args.backtest, args.output)


if __name__ == "__main__":
    main()
