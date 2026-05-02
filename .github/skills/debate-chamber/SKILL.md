---
name: debate-chamber
description: 'Orchestrate multi-agent AI debates on stock investment decisions. Use when: running debates on specific tickers, modifying debate logic and agent prompts, tuning conviction scores, parsing debate outputs, understanding the LangGraph state machine.'
argument-hint: 'Specify tickers (e.g., BBRI BBCA) or modification area (prompts, consensus, scoring)'
---

# Debate Chamber Skill

## Overview

The Debate Chamber is a **LangGraph-based multi-agent system** that simulates investment debates. Three agents (Bull, Bear, Analyst) iteratively argue about stock viability using fundamental data, technical analysis, and fair value estimates. The system produces a conviction score and trade recommendation.

## Architecture

### Agents & Roles

| Agent | Perspective | Responsibility |
|-------|-------------|-----------------|
| **Bull** | Optimistic | Highlights strengths, upside potential, catalyst events |
| **Bear** | Pessimistic | Identifies risks, downside, valuation concerns |
| **Analyst** | Neutral | Synthesizes both sides, identifies consensus, checks anti-groupthink |

### Debate Flow

```
Initialize Debate State
    ↓ (Provide stock fundamentals, technicals, fair value range)
Bull & Bear Opening Positions (parallel)
    ↓
Analyst Evaluates Arguments
    ↓
Round of Rebuttals (Bull & Bear respond to critiques)
    ↓
Consensus Check
    ├─ Consensus Reached? → Analyst Final Verdict
    ├─ Uses Pro for reasoning-quality evaluation
    └─ Deadlock? → Apply Anti-Groupthink Logic
    ↓
Calculate Conviction Score & Recommendation
    ├─ Weights: Confidence (default 50%) + Risk/Reward Ratio (default 50%)
    ├─ Configured via `core/settings.py` (`CONVICTION_WEIGHT_CONFIDENCE`, `CONVICTION_WEIGHT_RR_RATIO`)
    └─ Thresholds adjusted by market regime
    ↓
Output: Debate Transcript + JSON Verdict
```

### LangGraph State Machine

- **State**: `DebateState` (Pydantic) holds messages, stock data, conviction score, recommendation
- **Nodes**: Functions for Bull, Bear, Analyst, Consensus, Conviction calculation
- **Edges**: Conditional routing (loop until consensus or max rounds)
- **Memory**: All messages preserved; no state mutation

## Entry Points

### Run Debates on Specific Tickers

```bash
python run_debate.py --tickers BBRI BBCA ADRO --output-dir output/debates
```

**Output**: JSON files in `output/debates/` with full debate transcripts and verdicts
- Filename: `{TICKER}_debate.json`
- Contains: agent arguments, consensus notes, conviction score, recommendation

### Integrated in Orchestrator

```bash
python orchestrator.py
```

Runs end-to-end: 
1. Quantitative filter (top 10 candidates)
2. Debates on each candidate
3. Scores & ranks by conviction
4. Outputs `TOP_3_SWING_TRADES.md`

## Common Tasks

### 1. Run a Single Debate

```python
from services.debate_chamber import DebateChamber
from db.session import async_session

async def debate_ticker(ticker: str):
    async with async_session() as session:
        chamber = DebateChamber(session)
        result = await chamber.run_debate(ticker)
        print(f"Verdict: {result.recommendation}")
        print(f"Conviction: {result.conviction_score}")
```

### 2. Modify Agent Prompts

**File**: `services/debate_chamber.py` (search for `BULL_SYSTEM_PROMPT`, `BEAR_SYSTEM_PROMPT`, `ANALYST_SYSTEM_PROMPT`)

Change the tone, analysis angle, or focus:
```python
BULL_SYSTEM_PROMPT = """
You are a bullish equity analyst. Focus on:
1. Revenue growth catalysts
2. Margin expansion opportunities
3. Sector tailwinds
...
"""
```

Then re-run debates; verdicts will reflect new prompts.

### 3. Tune Conviction Scoring

**File**: `core/settings.py` or `.env`

```env
CONVICTION_WEIGHT_CONFIDENCE=0.4    # Confidence component weight
CONVICTION_WEIGHT_RR_RATIO=0.6      # Risk/Reward ratio weight
```

**File**: `services/debate_chamber.py` (search for `calculate_conviction_score`)

- Adjust how AI confidence maps to conviction (e.g., 0.3–0.9 → 0–100)
- Adjust how Risk/Reward ratio impacts final score
- Apply regime-based multipliers (bullish market → lower threshold)

### 4. Modify Consensus Logic

**File**: `services/debate_chamber.py`

Look for `check_consensus()` function:
- Current: Returns True if Bull & Bear agree on recommendation
- Uses Pro model for the consensus evaluator to preserve reasoning quality
- Customization: Add tone similarity threshold, conviction alignment, or voting system

**Anti-Groupthink Bypass**:
- If consensus is reached **but** conviction is very high (>90), Analyst may override if both sides are overly bullish/bearish
- Edit `bypass_groupthink()` logic

### 5. Parse Debate Output

Debate JSON structure:
```json
{
  "ticker": "BBRI",
  "bull_opening": "...",
  "bear_opening": "...",
  "analyst_synthesis": "...",
  "rebuttal_rounds": [
    {
      "round": 1,
      "bull_rebuttal": "...",
      "bear_rebuttal": "..."
    }
  ],
  "final_verdict": {
    "recommendation": "BUY" | "HOLD" | "SELL",
    "conviction_score": 0-100,
    "confidence": 0.3-0.9,
    "risk_reward_ratio": "2.5:1",
    "key_risks": [...],
    "catalysts": [...]
  }
}
```

**Parse in Python**:
```python
import json
with open("output/debates/BBRI_debate.json") as f:
    result = json.load(f)
    print(f"Recommendation: {result['final_verdict']['recommendation']}")
    print(f"Conviction: {result['final_verdict']['conviction_score']}")
```

### 6. Adjust Debate Rounds

**File**: `services/debate_chamber.py` (search for `max_rounds`)

- Current: Typically 2–3 rounds of rebuttals
- Change: Increase for deeper debate, decrease for speed
- Each round calls Gemini Pro (high cost)

### 7. Integrate Debate Results into Scoring

**File**: `core/historical_scorer.py`

- Tracks past debate verdicts vs. actual price outcomes
- Calibrates conviction thresholds based on win rate
- Historical scorer learns: "When conviction >80, BUY wins 75% of the time"

## Debugging

### Debate Runs Too Long
- Reduce `MAX_CONCURRENT_DEBATES` in `.env` (default: 3)
- Reduce debate rounds
- Check Gemini API quota and rate limits (`GEMINI_RPM_LIMIT`)

### Conviction Score Seems Off
- Verify `CONVICTION_WEIGHT_*` in `.env` are summing to 1.0 (or ratio is intentional)
- Check `core/regime.py` — market regime affects thresholds
- Review `core/historical_scorer.py` — recent calibration may lower thresholds if recent losses occurred

### Agent Messages Seem Generic
- Inspect agent system prompts in `debate_chamber.py`
- Ensure fundamental data (P/E, debt, ROE) is being passed correctly
- Check LLM model setting: `GEMINI_PRO_MODEL` should be a high-quality model (not flash)

### Consensus Not Reached
- Increase max rounds: `max_rounds = 5` in `DebateChamber.run_debate()`
- Loosen consensus criteria: Analyst may need less strict agreement threshold
- Check if both agents are given contradictory fundamental data

## Key Files

| File | Purpose |
|------|---------|
| `services/debate_chamber.py` | Main orchestration, LangGraph setup, agent prompts |
| `services/ai_assistant.py` | LLM wrapper (Gemini calls, token tracking) |
| `schemas/debate.py` | Pydantic models for debate I/O |
| `core/settings.py` | Conviction weights, debate config, regime settings |
| `run_debate.py` | CLI entry point (batch debates) |
| `orchestrator.py` | Full pipeline with debates |
| `core/regime.py` | Market regime (affects verdict thresholds) |
| `core/budget.py` | Tracks Gemini API spend |

## Performance Tips

1. **Cache Fundamental Data**: Run `main.py` first to populate DB; debates fetch from repositories
2. **Parallel Debates**: `MAX_CONCURRENT_DEBATES` limits concurrency to avoid rate limit (default: 3)
3. **Batch Delays**: `BATCH_DELAY_SECONDS` adds pause between batches (default: 1s)
4. **Use Flash Model for Synthesis**: Keep Analyst using Pro model, but Bull/Bear could use Flash for rebuttals (modify prompts)

## Example: Full Debate Workflow

```bash
# 1. Fetch stock data
python main.py

# 2. Generate candidates
python run_quant_filter.py

# 3. Debate top candidates
python orchestrator.py

# 4. Review output
cat output/TOP_3_SWING_TRADES.md
```

## Related Documentation

- [Regime Detection](core/regime.py) — Market state classification
- [Fair Value Calculator](services/fair_value_calculator.py) — Valuation inputs to debates
- [Historical Scorer](core/historical_scorer.py) — Conviction calibration
- [Budget Tracking](core/budget.py) — API cost management
