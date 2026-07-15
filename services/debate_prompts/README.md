# Debate Chamber Prompts

This directory contains the AI prompts that power the debate chamber's multi-agent system. Each prompt is carefully designed to guide specific agents toward their role in the investment decision process.

## Overview

The debate chamber follows this flow:

```
Scout Phase (Parallel Execution)
├── Fundamental Scout: Analyzes financial metrics
├── Chartist Scout: Examines technical patterns
└── Sentiment Scout: Evaluates market sentiment

→ Synthesizer: Aggregates scout findings

→ Debate Phase (Iterative Rounds)
├── Round 1: Bull makes initial case → Bear audits
├── Round 2: Bull responds → Bear challenges with focus on margin of safety
├── [Repeat if consensus not reached, max 3 rounds]

→ Consensus Evaluator: Check for agreement

→ Final Judgment Phase
├── Devil's Advocate: Bias testing & stress scenarios
└── CIO Judge: Final BUY/HOLD/SELL verdict with confidence & reasoning
```

## Prompt Files

### Scout Prompts (Data Extraction Phase)

These prompts run in **parallel** and extract structured evidence from market data.

#### `fundamental_scout.txt`
**Role**: Financial Analyst  
**LLM Tier**: flash — via `get_llm("flash")` (Gemini Flash by default; provider switchable to Anthropic/Codex in `providers/llm_factory.py`)  
**Output**: Key financial metrics, valuation signals, growth trends  
**Context Window**: ~2,000 tokens (lightweight)

Key instructions:
- Analyze P/E, P/B, PEG ratios against sector peers
- Track dividend safety and free cash flow
- Identify accounting quality red flags
- Grade overall financial health (Strong/Fair/Weak)

#### `chartist.txt`
**Role**: Technical Analyst  
**LLM Tier**: flash  
**Output**: Support/resistance levels, trend strength, pattern signals  
**Context Window**: ~1,500 tokens

Key instructions:
- Analyze MA50, MA200, RSI, ATR from pre-computed OHLCV data
- Identify golden cross / death cross signals
- Note volume confirmation & divergence warnings
- Assess trend direction & reversal probability

#### `sentiment.txt`
**Role**: Market Sentiment Scout  
**LLM Tier**: flash  
**Output**: News sentiment, institutional flows, retail positioning  
**Context Window**: ~1,500 tokens

Key instructions:
- Summarize recent news & corporate actions
- Note insider buying/selling signals
- Flag regulatory or competitive threats
- Grade sentiment (Bullish/Neutral/Bearish)

### Debate Phase Prompts

#### `bull_r1.txt` (Round 1 — Initial Bull Case)
**Role**: Bullish Analyst making the buy case  
**LLM Tier**: flash  
**Output**: 3-5 key arguments for buying, recommended entry/target/stop prices  
**Token Budget**: ~3,000-4,000 tokens

Key instructions:
- Synthesize evidence from all three scouts
- Build a compelling narrative (why NOW?)
- Cite specific numbers (e.g., "P/B of 1.2x vs sector 1.8x")
- Argue within the Python-computed trade envelope (`_compute_trade_envelope`: resistance-based target with sector-aware swing cap — +10% default, up to +20% mining; agents never invent prices)
- R/R must clear the tier-aware floor: 1.4x large-cap (≥ Rp 50T) / 1.62x default, regime-scaled (`utils/trade_math.py`)

#### `bull_r2.txt` (Round 2 — Bull Responds to Bear)
**Role**: Bullish Analyst addressing bear challenges  
**LLM Tier**: flash  
**Output**: Counterarguments to bear concerns, margin of safety analysis  
**Token Budget**: ~3,000-4,000 tokens

Key instructions:
- MUST address bear's specific R1 points (no hand-waving)
- Acknowledge valid risks but reframe them
- Explain margin of safety using Graham-style valuation
- Provide data-backed responses (earnings quality, FCF sustainability)
- Avoid repeating R1 evidence (no redundancy)

#### `bear_r1.txt` (Round 1 — Bear's Initial Audit)
**Role**: Risk Auditor challenging the bull case  
**LLM Tier**: flash  
**Output**: 3-5 key downside risks, valuation concerns, warning flags  
**Token Budget**: ~3,000-4,000 tokens

Key instructions:
- Play devil's advocate: assume trade is already wrong
- Identify worst-case scenarios & stress points
- Challenge valuation assumptions (is margin of safety adequate?)
- Flag execution risks (liquidity, earnings revision risk, macro headwinds)
- Propose more conservative stop loss levels

#### `bear_r2.txt` (Round 2 — Bear Doubles Down)
**Role**: Bearish Auditor with focused pressure  
**LLM Tier**: flash  
**Output**: Deep dive on margin of safety + ATR-based downside risk  
**Token Budget**: ~3,000-4,000 tokens

Key instructions:
- Calculate margin of safety using Graham Valuation (intrinsic - current price)
- Use ATR to quantify downside to SMA20 support
- Challenge bull's "target price" realism
- Focus on **if this thesis breaks, how far down?**
- Highlight NPL, debt, or sector cycle risks

### Integration & Decision Prompts

#### `consensus.txt`
**Role**: Consensus Checker  
**Execution**: deterministic Python vote counting (`_evaluate_consensus_votes`) — no LLM call; prompt file retained in manifest  
**Output**: Have bull & bear reached agreement? If not, why?  
**Token Budget**: ~1,500 tokens

Logic:
- If both agree on risk level and trade setup → move to CIO Judge
- If they diverge (one bullish, one bearish) → trigger another round
- Max 3 rounds to avoid infinite debate loops

#### `state_cleaner.txt`
**Role**: Context Pruner (between debate rounds)  
**Execution**: deterministic zero-LLM pruning (tail truncation + regex price extraction); prompt file retained in manifest  
**Output**: Compressed state, removing redundant evidence  
**Token Budget**: ~1,500 tokens

Key instructions:
- Summarize consensus points from previous round
- Remove duplicate arguments
- Keep only novel evidence from current round
- Maintain token budget for next round

#### `devils_advocate.txt`
**Role**: Bias Tester  
**LLM Tier**: flash  
**Output**: Stress test scenarios, hidden assumption challenges  
**Token Budget**: ~2,500 tokens

Key instructions:
- Assume institutional bias toward the consensus position
- Propose 2-3 stress scenarios (tech shock, macro surprise, company-specific catalyst)
- Would the trade still make sense at -20%? -30%?
- Flag overconfidence or black swan blindness

#### `cio_judge.txt`
**Role**: Final Decision Maker  
**LLM Tier**: pro — the only pro-tier call in the graph (`get_llm("pro")`)  
**Output**: CIOVerdict JSON with BUY/HOLD/SELL, confidence (0-1), detailed reasoning  
**Token Budget**: ~4,000-5,000 tokens

Key instructions:
- Synthesize bull/bear arguments into ONE clear recommendation
- Confidence = probability trade thesis remains valid for the 5-20 trading-day swing window
- R/R below the tier-aware floor (1.4x large-cap / 1.62x default) is hard-rejected downstream by the deterministic risk governor
- Flag high-uncertainty scenarios (skip trade if confidence < 0.5)
- Never invent prices; use the Python-computed trade envelope injected into the prompt
- Require margin of safety ≥ 5% for entry

#### `agent_signal.txt`
**Role**: Agent Signal Aggregator  
**LLM Tier**: flash  
**Output**: Integration matrix showing which scouts agree/disagree  
**Token Budget**: ~1,500 tokens

Key instructions:
- Check if fundamental, technical, and sentiment scouts align
- Flag misalignment (e.g., "strong technicals but weak fundamentals")
- Grade signal quality: Strong/Moderate/Weak
- Feed into CIO confidence adjustment

## Customization Guide

### When to Update Prompts

Update prompts when:

1. **New Market Regime**: High volatility requires more conservative stops/position sizes
2. **Regulatory Changes**: New disclosure rules or sector rules require new metrics
3. **Proven Drift**: Backtests show the prompt strategy underperforms a benchmark
4. **Scout Feedback**: Agents consistently ignore evidence → strengthen prompts
5. **Token Budget Pressure**: Compress prompt size or split roles

### How to Update Prompts

1. **Edit the prompt file** (e.g., `bull_r1.txt`)
2. **Increment the version** in `manifest.json`:
   ```json
   {
     "prompt_version": "2026-06-15-improved-margin-of-safety",
     ...
   }
   ```
3. **Document the change** in `PROMPT_MIGRATION.md` (see below)
4. **Run tests**:
   ```bash
   uv run pytest tests/test_debate_chamber_reliability.py -v
   ```
5. **Commit** with clear message:
   ```
   feat: Strengthen bear_r2 margin of safety analysis
   
   - Added Graham valuation formula
   - Increased downside scenario depth
   - Better alignment with risk governor thresholds
   ```

### Version History

See `PROMPT_MIGRATION.md` for:
- When each prompt version was released
- What changed and why
- Backtest results for that version
- Fallback instructions if new version underperforms

**Current Version**: `2026-06-30-cio-regime-labels-v27` — `manifest.json` is the source of truth; always check it rather than this README

### Testing Prompts

Before deploying a new prompt:

```bash
# Run on a single ticker with detailed output
python -c "
from services.debate_chamber import DebateChamber
from core.settings import settings

async def test():
    dc = DebateChamber()
    result = await dc.run('BBRI')
    print(result.model_dump_json(indent=2))

import asyncio
asyncio.run(test())
"

# Backtest new prompt against historical trades
uv run pytest tests/test_debate_chamber_reliability.py::test_cio_judge_confidence_calibration -v
```

## Token Budget Allocation

**Total debate per ticker**: ~35,000-40,000 tokens (flash + pro combined; see table below)

| Phase | Model | Budget | Justification |
|-------|-------|--------|---|
| Scouts (3×) | Flash | 6,000 | Data extraction; low reasoning needed |
| Bull R1 | Flash | 3,500 | Core reasoning & trade logic |
| Bear R1 | Flash | 3,500 | Risk challenge requires depth |
| Bull R2 | Flash | 3,000 | Counterargument; less novel data |
| Bear R2 | Flash | 3,500 | Critical margin of safety analysis |
| Consensus | — (deterministic) | 0 | Python vote counting, no LLM |
| State Cleaner (×2 max) | — (deterministic) | 0 | Zero-LLM context pruning |
| Devil's Advocate | Flash | 2,500 | Stress testing & bias detection |
| CIO Judge | Pro | 4,500 | Final reasoning, highest stakes |
| **Total** | | ~35,000-40,000 tokens | ~1,000 tickers/month @ $10 budget |

## Troubleshooting Prompts

### Problem: Bull & Bear Keep Disagreeing (>3 rounds)

**Symptoms**: State cleaner runs but debate loops infinitely

**Fix**:
1. Check if consensus.txt is too strict (lower agreement threshold?)
2. Add more concrete guardrails in both prompts
3. Check if risk governor is actually filtering (see `core/risk_governor.py`)

### Problem: CIO Judge Flip-Flops on Confidence

**Symptoms**: Same ticker, different runs → confidence varies wildly

**Fix**:
1. Add **deterministic scoring** to cio_judge.txt (use margin of safety % as floor)
2. Check if scouts are non-deterministic (randomized data feeds?)
3. Lock in key metrics (RSI, P/E) in scout output schemas

### Problem: Prompt Tokens Exceed Budget

**Symptoms**: `BudgetExhaustedError` during debate

**Fix**:
1. Reduce context window in scout prompts (summarize instead of full lists)
2. Use Flash more, Pro less
3. Add early exit logic: if scouts unanimously agree in Round 1, skip rounds 2-3

## Monitoring Prompt Health

Check these metrics weekly:

```python
from core.ops_telemetry import DEFAULT_TELEMETRY

# Token consumption by phase
telemetry = DEFAULT_TELEMETRY.rollup_by_agent()
print(telemetry.token_usage)  # Alerts if avg > 40k

# Debate length distribution
debate_lengths = DEFAULT_TELEMETRY.debate_round_distribution()
# Alert if median > 2.5 rounds (indicates unresolved disagreements)

# CIO confidence distribution
confidences = DEFAULT_TELEMETRY.cio_confidence_percentiles()
# Alert if median < 0.55 (indicates insufficient signal)
```

## References

- **LangGraph Docs**: [State graphs & callbacks](https://langchain-ai.github.io/langgraph/)
- **Pydantic Schemas**: [schemas/debate.py](../schemas/debate.py)
- **Debate Chamber**: [services/debate_chamber.py](../debate_chamber.py)
- **Test Suite**: [tests/test_debate_chamber_reliability.py](../../tests/test_debate_chamber_reliability.py)
