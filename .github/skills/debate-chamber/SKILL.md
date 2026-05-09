---
name: debate-chamber
description: Develop, run, and validate the IDX Fundamental Analysis debate chamber. Use when Codex is asked to run ticker debates, modify multi-agent prompts or LangGraph routing, tune consensus/confidence/conviction behavior, inspect debate outputs, or fix debate reliability tests in services/debate_chamber.py, schemas/debate.py, run_debate.py, or orchestrator.py.
---

# Debate Chamber

## Orientation

Treat the debate chamber as the project CIO workflow for swing-trade decisions. It gathers fundamental, chart, sentiment, Bull, Bear, auditor, consensus, and CIO verdict signals through a LangGraph state machine in `services/debate_chamber.py`.

Before changing behavior, inspect the live code around the node or parser involved. This file changes often, and prompt text, regex extraction, routing, and schema fields are tightly coupled.

## Key Files

- `services/debate_chamber.py`: LangGraph nodes, prompt contracts, vote extraction, consensus routing, CIO verdict parsing, and `DebateChamber.run`.
- `schemas/debate.py`: typed state and output contracts consumed by the chamber and orchestrator.
- `tests/test_debate_chamber_reliability.py`: parser, consensus, fallback, and reliability expectations.
- `orchestrator.py`: batch debate execution, conviction ranking, per-ticker debate persistence, and top-3 report generation.
- `core/settings.py`: Gemini model names, conviction weights, rate limits, and debate concurrency settings.
- `core/budget.py`: LLM cost/rate tracking.

## Running Debates

Use real LLM/API runs only when the user asks for an actual debate or asks to verify end-to-end behavior. Confirm `.env` has `GEMINI_API_KEY` and any StockBit/market data requirements before assuming a live run can succeed.

```bash
uv run python run_debate.py --tickers BBRI BBCA --output-dir output/debates
```

For the full batch workflow after candidates exist:

```bash
uv run python orchestrator.py --output-dir output
```

For safer local checks after code changes:

```bash
uv run pytest tests/test_debate_chamber_reliability.py tests/test_historical_scorer.py
uv run ruff check .
```

## Prompt Changes

Preserve the machine-readable phrases that downstream parsers rely on. In Bull/Bear/Auditor-style responses, keep explicit position and confidence lines such as `Position:` and `Agent Confidence:` unless you also update extraction helpers and tests.

When editing prompts:

1. Search the node that owns the behavior: `_fundamental_node`, `_chartist_node`, `_sentiment_node`, `_bullish_node`, `_bearish_node`, `_devils_advocate_node`, `_consensus_evaluator_node`, or `_cio_judge_node`.
2. Keep Indonesian market context and swing-trade framing intact unless the user requests a different investment horizon.
3. Keep fallback behavior conservative. Missing data should reduce confidence or produce HOLD/AVOID, not fabricated precision.
4. Update parser tests when changing required JSON fields, confidence scale, rating names, or output wording.

## Consensus And Routing

For vote and consensus work, inspect these functions together:

- `post_evaluator_router`
- `_extract_signal`
- `_evaluate_consensus_votes`
- `_format_consensus_directive`
- `_apply_consensus_override`
- `_cio_judge_node`
- `_build_graph`

Respect the existing consensus methods: `voting`, `confidence_winner`, and `soft_hold`. If a method changes semantics, update both CIO directives and orchestrator/report assumptions.

## Output Contracts

The final verdict must remain parseable as structured data. Before adding or renaming fields, trace all consumers in `orchestrator.py`, `schemas/debate.py`, and tests. Important fields include ticker, rating/recommendation, confidence, risk/reward ratio, thesis, risks, catalysts, consensus flags, current price, target price, stop loss, and take profit.

When adding a new output field, prefer additive changes first. Keep old fields available if `output/debates/*` or historical scorer compatibility depends on them.

## Debugging Checklist

- If every ticker returns HOLD with zero confidence, inspect LLM invocation, current price, state initialization, and CIO JSON parsing fallback.
- If consensus feels wrong, log or inspect extracted Bull/Bear/Auditor votes before changing prompts.
- If outputs are malformed, tighten the prompt contract and parser fallback together.
- If live runs hit quota or rate limits, tune `MAX_CONCURRENT_DEBATES`, `GEMINI_RPM_LIMIT`, and `BATCH_DELAY_SECONDS`.
- Never print or commit API keys from `.env`.
