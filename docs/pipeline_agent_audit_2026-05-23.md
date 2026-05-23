# Pipeline Agent Audit - 2026-05-23

Scope: production orchestrator pipeline (`orchestrator.py` -> `core/orchestrator/legacy.py` -> `services/debate_chamber.py`), direct debate wrapper (`run_debate.py`), API stream path (`app/api/routers/stocks.py`), prompt pack config, and current output artifacts.

No code was changed. This is a surgical architecture audit and restructuring recommendation.

## Step 1 - Pipeline Reconnaissance

### 1A. System-wide scan

Primary entry points:

- CLI facade: `orchestrator.py:36-58`
- Typer CLI bridge: `app/cli/commands/pipeline.py:21-35`, `app/cli/commands/pipeline.py:38-95`
- Production async pipeline: `core/orchestrator/legacy.py:4269-4628`
- Direct debate wrapper: `run_debate.py:1163-1240`
- API streaming debate path: `app/api/routers/stocks.py:167-216`

Pipeline config and prompt config:

- Python package/entry script surface: `pyproject.toml:48-63`
- Orchestrator compatibility import surface: `core/orchestrator/pipeline.py:1-27`
- Prompt registry and required prompt list: `services/debate_prompt_registry.py:11-24`
- Prompt manifest: `services/debate_prompts/manifest.json:1-17`
- Debate state schema and reducer: `schemas/debate.py:33-47`, `schemas/debate.py:439-520`
- LLM factories: `providers/gemini.py:45-99`

Detected agent patterns:

- Sequential agents: synthesizer -> bull -> bear -> consensus -> state cleaner loop -> devil's advocate -> CIO.
- Parallel agents: fundamental scout, chartist, sentiment scout fan out from `START`; per-ticker debates are run with `asyncio.gather`.
- Hierarchical/supervisor pattern: outer orchestrator supervises batch debates; adaptive planner supervises failure actions; CIO supervises final decision synthesis.
- Router/dispatcher agents: `post_evaluator_router` chooses `devils_advocate` vs `state_cleaner`.
- Aggregator/reducer agents: synthesizer fan-in, consensus evaluator vote reducer, state cleaner history reducer, CIO final reducer.

### 1B. Pipeline identity card

```text
PIPELINE OVERVIEW
------------------------------------------------------------
Total Agents        : 10 production DebateChamber nodes
Optional Agent      : 1 single-agent baseline in --mode single/compare
Execution Pattern   : Hybrid (parallel scout fan-out, sequential debate loop,
                      router, reducer/aggregator, bounded parallel batch)
Entry Point         : orchestrator.py:_run_cli -> core.orchestrator.legacy.main
Exit Point          : save_full_results, save_individual_debates_versioned,
                      generate_top3_report, formatter reports
Shared State        : Yes - DebateChamberState in schemas/debate.py,
                      reducer on debate_history only
Orchestrator        : core/orchestrator/legacy.py main/run_batch_debates
------------------------------------------------------------
```

Agent registry:

| Agent Name | Pattern Type | Responsibility |
|---|---|---|
| fundamental | Parallel scout | Fetch Stockbit fundamentals, compute fair value, produce fundamental brief and signal. |
| chartist | Parallel scout | Build technical indicators from cached yfinance data and Stockbit orderbook, produce timing brief and signal. |
| sentiment | Parallel scout | Fetch Stockbit pinned social stream plus news bundle, produce sentiment brief and confidence adjustment. |
| synthesizer | Aggregator/reducer | Merge scout outputs, build ContextPack/RAG decision brief, inject margin-of-safety and ex-date context. |
| bullish_analyst | Sequential debate worker | Build strongest BUY case using synthesized context and prior round history. |
| bearish_auditor | Sequential debate worker | Challenge bull case and build AVOID/SELL risk case. |
| consensus_evaluator | Router/reducer | Deterministically collect five votes and decide whether to continue or conclude. |
| state_cleaner | Sequential reducer | Compact debate history and preserve cited prices before another round. |
| devils_advocate | Sequential adversarial worker | Add worst-case/macro challenge before final decision. |
| cio_judge | Supervisor/aggregator | Compute trade envelope, apply consensus/news overrides, produce Pydantic CIOVerdict JSON. |
| SingleAgentAnalyzer | Optional baseline | One-shot comparator used only in `--mode single` or `--mode compare`. |

### 1C. Execution flow map

```text
INPUT
  |
  v
orchestrator.py:_run_cli
  |
  v
core.orchestrator.legacy.main
  |
  +--> prompt pack lint / backtest eval / dependency checks
  |
  +--> candidate file validation / optional quant filter rerun
  |
  +--> market regime detection
  |
  +--> candidate intake + pre-CIO filters
  |
  +--> optional SingleAgentAnalyzer branch (--mode single/compare)
  |
  +--> provider health checks
  |
  v
run_batch_debates (bounded parallel per ticker)
  |
  +------------------------------ per ticker ------------------------------+
  |                                                                       |
  | DebateChamber.run                                                     |
  |   |                                                                   |
  |   +--> prefetch_market_data                                           |
  |   |                                                                   |
  |   +--> LangGraph START                                                |
  |          |                                                            |
  |          +--> [fundamental] --+                                       |
  |          +--> [chartist] -----+--> [synthesizer] --> [bullish]         |
  |          +--> [sentiment] ----+                         |              |
  |                                                        v              |
  |                                                   [bearish]           |
  |                                                        |              |
  |                                                        v              |
  |                                             [consensus_evaluator]      |
  |                                                |              |       |
  |                                  state_cleaner-+              +--> [devils_advocate]
  |                                      |                               |
  |                                      +--------> next round           v
  |                                                                 [cio_judge]
  |                                                                      |
  |                                                                      v
  |                                                                  END state
  +-----------------------------------------------------------------------+
  |
  v
postprocess: news top-level attachment, risk governor, telemetry
  |
  v
select_top_n -> risk governor annotation -> position sizing
  |
  v
OUTPUT: full_batch_results.json, latest_debate.json, TOP_3_SWING_TRADES.md,
        latest_batch_report.md, telemetry, comparison report if requested
```

## Step 2 - Per-Agent Deep Audit

Legend:

- Verdict: Essential / Redundant / Dead Weight
- Health: Solid / Fragile / Dangerous / Inconsistent / Unused
- Score format: necessity, input, output, role, pattern, resilience -> average

### Agent audit: fundamental

File location: `services/debate_chamber.py:1130-1215`

A - Necessity test:

- Unique task: pulls Stockbit key stats (`services/debate_chamber.py:1141-1143`), builds fair value (`services/debate_chamber.py:1155`), turns raw fundamentals into a scout brief/signaled stance (`services/debate_chamber.py:1160-1166`).
- Could be absorbed: not safely; the CIO and consensus need a separate fundamental vote.
- Removal impact: `fundamental_data` and `fair_value_estimate` vanish, weakening synthesizer (`services/debate_chamber.py:1469`, `services/debate_chamber.py:1502`) and CIO envelope (`services/debate_chamber.py:2277`, `services/debate_chamber.py:2315`).
- Verdict: Essential.

B - Input integrity:

- Input source: shared state ticker/current price (`services/debate_chamber.py:1131-1132`) plus Stockbit API.
- Validation: ticker assumes valid upstream; missing Stockbit raw returns `"Data Unavailable"` (`services/debate_chamber.py:1144-1153`).
- Malformed/error behavior: classified, planner can retry/proceed/skip (`services/debate_chamber.py:1177-1215`).
- Input health: Solid.

C - Output quality:

- Output: `fundamental_data` string and `fair_value_estimate` (`services/debate_chamber.py:1173-1175`).
- Consumed by: synthesizer and consensus vote collector (`services/debate_chamber.py:1469`, `services/debate_chamber.py:716`).
- Issue: scout signal is regex-derived from text, not typed JSON (`services/debate_chamber.py:636-662`).
- Output health: Solid but schema-light.

D - Role clarity:

- Single responsibility: mostly sharp; fetches, values, and summarizes fundamentals.
- Overlap: fair value is later enforced by CIO/trade envelope, but the scout is the correct producer.
- Role clarity: Sharp.

E - Pattern fit:

- Current: parallel scout.
- Correct: parallel scout, because it has no dependency on chartist or sentiment.
- Pattern fit: Correct.

F - Error resilience:

- Retry wrapper exists for `_fetch_url` (`services/debate_chamber.py:1113-1123`) and LLM calls (`services/debate_chamber.py:1076-1111`).
- Planner fallback and partial metadata are returned for key paths (`services/debate_chamber.py:1199-1215`).
- Resilience: Robust.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 10 | Solid |
| Input Integrity | 8 | Solid |
| Output Quality | 8 | Solid |
| Role Clarity | 8 | Solid |
| Pattern Fit | 10 | Solid |
| Error Resilience | 8 | Solid |
| Total | 8.7/10 | Keep |

### Agent audit: chartist

File location: `services/debate_chamber.py:1217-1377`

A - Necessity test:

- Unique task: computes deterministic technical ground truth from cached yfinance OHLCV (`services/debate_chamber.py:1233-1286`) and adds Stockbit orderbook context (`services/debate_chamber.py:1320-1323`).
- Could be absorbed: deterministic feature computation could be split out, but the chartist scout vote should remain independent.
- Removal impact: CIO loses MA50/ATR inputs for envelope/risk signal (`services/debate_chamber.py:2276`, `services/debate_chamber.py:2315-2322`).
- Verdict: Essential.

B - Input integrity:

- Input source: `market_data.history` (`services/debate_chamber.py:1233-1234`) and Stockbit orderbook.
- Validation: checks history length >=20; handles MultiIndex columns (`services/debate_chamber.py:1234-1238`).
- Malformed/error behavior: planner retry/partial path (`services/debate_chamber.py:1288-1317`, `services/debate_chamber.py:1324-1352`).
- Input health: Solid.

C - Output quality:

- Output: `technical_data` string plus `technical_indicators` dict (`services/debate_chamber.py:1374-1377`).
- Consumed internally by synthesizer/CIO (`services/debate_chamber.py:1475`, `services/debate_chamber.py:2276`).
- Handoff problem: orchestrator result drops `technical_indicators` in `_run_single_debate` (`core/orchestrator/legacy.py:3113-3137`), while risk governor later looks for them (`core/orchestrator/legacy.py:2807-2818`). Current `output/full_batch_results.json` also lacks a `technical_indicators` key.
- Output health: Inconsistent at wrapper boundary.

D - Role clarity:

- It combines deterministic indicator construction, orderbook fetch, and LLM commentary. This works but is heavier than one responsibility.
- Better ownership: split deterministic `technical_feature_builder` from LLM `chartist_commentary`.
- Role clarity: Blurry.

E - Pattern fit:

- Current: parallel scout.
- Correct: parallel scout, but deterministic feature builder should run before LLM commentary.
- Pattern fit: Correct but internally split-worthy.

F - Error resilience:

- Good provider fallback, but partial planner metadata is assigned through state mutation (`services/debate_chamber.py:1316`, `services/debate_chamber.py:1352`) and not returned in the normal chartist return (`services/debate_chamber.py:1374-1377`). In LangGraph, returned updates are the reliable handoff contract.
- Resilience: Partial.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 10 | Solid |
| Input Integrity | 8 | Solid |
| Output Quality | 6 | Inconsistent |
| Role Clarity | 6 | Blurry |
| Pattern Fit | 8 | Solid |
| Error Resilience | 6 | Partial |
| Total | 7.3/10 | Keep, split deterministic features |

### Agent audit: sentiment

File location: `services/debate_chamber.py:1379-1451`

A - Necessity test:

- Unique task: converts Stockbit social signal into a sentiment vote (`services/debate_chamber.py:1390-1409`) and attaches news context (`services/debate_chamber.py:1402`, `services/debate_chamber.py:1417`).
- Could be absorbed: social and news could be split, but at least one sentiment/news agent is needed.
- Removal impact: consensus loses the sentiment vote (`services/debate_chamber.py:718`) and CIO loses news confidence adjustment (`services/debate_chamber.py:2457-2475`).
- Verdict: Essential.

B - Input integrity:

- Input source: ticker plus Stockbit pinned stream and news fetcher.
- Validation: empty social data returns `"Data Unavailable"` plus news context (`services/debate_chamber.py:1393-1403`).
- Malformed/error behavior: planner partial path and news fallback (`services/debate_chamber.py:1419-1451`, `services/debate_chamber.py:237-278`).
- Input health: Solid.

C - Output quality:

- Output: `sentiment_data`, `news_brief`, `news_confidence_adjustment`, metadata (`services/debate_chamber.py:1417-1418`, `services/debate_chamber.py:1447-1450`).
- Consumed by synthesizer and CIO (`services/debate_chamber.py:1471-1473`, `services/debate_chamber.py:2457-2475`).
- Downstream duplication: orchestrator fetches news again after debate (`core/orchestrator/legacy.py:2908-2909`) and writes separate top-level news fields (`core/orchestrator/legacy.py:2779-2795`). That can diverge from the news used in CIO confidence.
- Output health: Solid internally, duplicated downstream.

D - Role clarity:

- It is both social sentiment scout and news enricher. That is useful but not a clean single responsibility.
- Better ownership: one `news_context_provider` before scouts, then sentiment node consumes it.
- Role clarity: Blurry.

E - Pattern fit:

- Current: parallel scout.
- Correct: parallel for social sentiment; news fetch should be precomputed once per ticker or folded into this node only, not repeated post-CIO.
- Pattern fit: Suboptimal.

F - Error resilience:

- Strong fallback through planner and `_news_context_for_state`.
- Risk is consistency, not crash safety.
- Resilience: Robust.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 9 | Solid |
| Input Integrity | 8 | Solid |
| Output Quality | 7 | Duplicated |
| Role Clarity | 6 | Blurry |
| Pattern Fit | 7 | Suboptimal |
| Error Resilience | 8 | Robust |
| Total | 7.5/10 | Keep, remove duplicate news pass |

### Agent audit: synthesizer

File location: `services/debate_chamber.py:1453-1674`

A - Necessity test:

- Unique task: fan-in of three scout outputs (`services/debate_chamber.py:1469-1499`), margin-of-safety warning (`services/debate_chamber.py:1501-1527`), ContextPack/RAG bundle (`services/debate_chamber.py:1536-1587`), final decision brief (`services/debate_chamber.py:1654-1674`).
- Could be absorbed: no; debate agents need one canonical context.
- Removal impact: bull/bear receive no `raw_data`/`decision_brief` (`services/debate_chamber.py:1685`, `services/debate_chamber.py:1736`).
- Verdict: Essential.

B - Input integrity:

- Input source: all scout outputs, market data, metadata, ex-date scanner, RAG store.
- Validation: warnings for missing fields and large token estimates (`services/debate_chamber.py:1553-1560`); fallback to raw data on RAG/context failure (`services/debate_chamber.py:1588-1653`).
- Input health: Solid.

C - Output quality:

- Output: `raw_data`, `decision_brief`, `fair_value_estimate`, metadata (`services/debate_chamber.py:1669-1674`).
- Consumed by bull/bear/CIO (`services/debate_chamber.py:1685`, `services/debate_chamber.py:1868`, `services/debate_chamber.py:2364-2378`).
- Output health: Solid.

D - Role clarity:

- It is an aggregator plus RAG selector plus margin-of-safety injector. This is coherent as "context builder," but `synthesizer` sounds like an LLM synthesis agent; this node is mostly deterministic.
- Role clarity: Mostly sharp, rename recommended.

E - Pattern fit:

- Current: aggregator after parallel scouts.
- Correct: yes.
- Pattern fit: Correct.

F - Error resilience:

- Good fallback ladder for RAG/context failure (`services/debate_chamber.py:1588-1653`).
- Resilience: Robust.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 10 | Solid |
| Input Integrity | 8 | Solid |
| Output Quality | 9 | Solid |
| Role Clarity | 7 | Mostly sharp |
| Pattern Fit | 10 | Correct |
| Error Resilience | 8 | Robust |
| Total | 8.7/10 | Keep, consider rename |

### Agent audit: bullish_analyst

File location: `services/debate_chamber.py:1678-1718`

A - Necessity test:

- Unique task: generates pro-BUY thesis using synthesized market data and prior history (`services/debate_chamber.py:1683-1704`).
- Could be absorbed: not without losing adversarial contrast.
- Removal impact: bear has no bull argument to challenge (`services/debate_chamber.py:1727-1737`), and consensus loses one side of debate.
- Verdict: Essential.

B - Input integrity:

- Input source: state ticker, `raw_data`, and optional pruned history (`services/debate_chamber.py:1685-1697`).
- Validation: relies on `state["raw_data"]`; no explicit empty guard beyond upstream synthesizer.
- Input health: Solid but dependent on synthesizer.

C - Output quality:

- Output: `DebateMessage(role="bull", position, confidence)` (`services/debate_chamber.py:1710-1718`).
- Consumed by bear, consensus, CIO transcript, reports (`services/debate_chamber.py:1727-1737`, `services/debate_chamber.py:720-737`, `services/debate_chamber.py:2356-2363`).
- Output health: Solid.

D - Role clarity:

- Clear adversarial BUY-side role.
- Role clarity: Sharp.

E - Pattern fit:

- Current: sequential before bear.
- Correct: yes; bear depends on latest bull.
- Pattern fit: Correct.

F - Error resilience:

- LLM retry and empty-response guard are centralized (`services/debate_chamber.py:1076-1111`).
- Short response warning only logs; no retry on suspiciously short non-empty output (`services/debate_chamber.py:1705-1709`).
- Resilience: Partial.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 10 | Solid |
| Input Integrity | 8 | Solid |
| Output Quality | 8 | Solid |
| Role Clarity | 10 | Sharp |
| Pattern Fit | 10 | Correct |
| Error Resilience | 7 | Partial |
| Total | 8.8/10 | Keep |

### Agent audit: bearish_auditor

File location: `services/debate_chamber.py:1720-1767`

A - Necessity test:

- Unique task: forensic challenge to the bull argument (`services/debate_chamber.py:1727-1737`) and anti-repeat rebuttal in later rounds (`services/debate_chamber.py:1740-1745`).
- Could be absorbed: not without losing anti-groupthink value.
- Removal impact: no adversarial debate, no bull/bear disagreement signal, consensus loses a core vote.
- Verdict: Essential.

B - Input integrity:

- Input source: latest bull argument and raw data.
- Validation: if no bull exists, uses placeholder (`services/debate_chamber.py:1732-1733`), but graph order normally prevents that.
- Input health: Solid.

C - Output quality:

- Output: `DebateMessage(role="bear", position, confidence)` and increments `round_count` (`services/debate_chamber.py:1759-1767`).
- Consumed by consensus, CIO, reports.
- Output health: Solid.

D - Role clarity:

- Clear AVOID/risk role.
- Role clarity: Sharp.

E - Pattern fit:

- Current: sequential after bull.
- Correct: yes.
- Pattern fit: Correct.

F - Error resilience:

- Same LLM retry/empty guard as bull.
- Short response only warns (`services/debate_chamber.py:1754-1758`).
- Resilience: Partial.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 10 | Solid |
| Input Integrity | 8 | Solid |
| Output Quality | 9 | Solid |
| Role Clarity | 10 | Sharp |
| Pattern Fit | 10 | Correct |
| Error Resilience | 7 | Partial |
| Total | 9.0/10 | Keep |

### Agent audit: consensus_evaluator

File location: `services/debate_chamber.py:1771-1787`; helper logic `services/debate_chamber.py:714-849`; router `services/debate_chamber.py:427-440`

A - Necessity test:

- Unique task: deterministic vote collection from 5 agents (`services/debate_chamber.py:714-737`) and consensus/soft-hold/confidence-winner logic (`services/debate_chamber.py:756-849`).
- Could be absorbed: could merge with router, but keeping a named vote reducer is clearer.
- Removal impact: no adaptive debate loop and no consensus metadata consumed by CIO/reporting.
- Verdict: Essential.

B - Input integrity:

- Input source: scout text plus latest bull/bear messages.
- Validation: signal extraction is heuristic regex (`services/debate_chamber.py:636-662`). Missing messages become empty votes (`services/debate_chamber.py:720-725`).
- Input health: Fragile.

C - Output quality:

- Output: `consensus_reached`, `consensus_method`, `dissenting_agents`, `consensus_winner`, `agent_votes`, `disagreement_type` (`services/debate_chamber.py:776-849`).
- Consumed by router and CIO consensus directive/override (`services/debate_chamber.py:427-440`, `services/debate_chamber.py:2195-2261`, `services/debate_chamber.py:2355-2376`).
- Output health: Solid.

D - Role clarity:

- Current code is deterministic, but prompt config still includes `CONSENSUS_PROMPT` (`services/debate_prompt_registry.py:19`, `services/debate_chamber.py:412`) and `ConsensusSchema` is unused (`services/debate_chamber.py:377-385`). Docstring says "Uses Pro" (`services/debate_chamber.py:1773-1775`) but no LLM call occurs.
- Role clarity: Blurry due to stale prompt/schema references.

E - Pattern fit:

- Current: reducer/router.
- Correct: yes. Deterministic is preferable for repeatable consensus.
- Pattern fit: Correct.

F - Error resilience:

- No external call; robust against provider failure.
- Fragility is semantic extraction from free text.
- Resilience: Robust mechanically, fragile semantically.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 9 | Solid |
| Input Integrity | 6 | Fragile |
| Output Quality | 8 | Solid |
| Role Clarity | 5 | Blurry |
| Pattern Fit | 9 | Correct |
| Error Resilience | 8 | Robust |
| Total | 7.5/10 | Keep, remove stale LLM prompt/schema |

### Agent audit: state_cleaner

File location: `services/debate_chamber.py:1795-1852`; reducer `schemas/debate.py:439-457`

A - Necessity test:

- Unique task: deterministic history pruning and price preservation before another round (`services/debate_chamber.py:1795-1811`).
- Could be absorbed: yes, as a helper inside debate loop, but as a graph node it makes the loop explicit.
- Removal impact: longer context, higher cost, less reliable price preservation.
- Verdict: Essential.

B - Input integrity:

- Input source: `debate_history` list.
- Validation: converts each raw item via `_as_debate_message` (`services/debate_chamber.py:1820-1821`).
- Malformed behavior: conversion exceptions would propagate; no per-message skip.
- Input health: Solid.

C - Output quality:

- Output: sentinel `round_num=-1` plus compacted messages (`services/debate_chamber.py:1849-1852`).
- Consumed by `history_updater`, which replaces history when sentinel appears (`schemas/debate.py:446-456`).
- Output health: Solid.

D - Role clarity:

- Clear deterministic reducer. However `STATE_CLEANER_PROMPT` is required/loaded but unused (`services/debate_prompt_registry.py:20`, `services/debate_chamber.py:414`).
- Role clarity: Sharp in code, blurry in config.

E - Pattern fit:

- Current: sequential reducer before additional debate round (`services/debate_chamber.py:2558-2559`).
- Correct: yes.
- Pattern fit: Correct.

F - Error resilience:

- Zero LLM/provider risk.
- Needs stronger malformed-message isolation if persisted history ever changes shape.
- Resilience: Solid.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 8 | Solid |
| Input Integrity | 8 | Solid |
| Output Quality | 9 | Solid |
| Role Clarity | 7 | Config mismatch |
| Pattern Fit | 9 | Correct |
| Error Resilience | 8 | Solid |
| Total | 8.2/10 | Keep, remove stale prompt config |

### Agent audit: devils_advocate

File location: `services/debate_chamber.py:1854-1886`

A - Necessity test:

- Unique task: adversarial final challenge before CIO (`services/debate_chamber.py:1854-1874`).
- Could be absorbed: could be folded into CIO prompt, but a separate adversarial LLM call produces a distinct artifact and report section.
- Removal impact: weaker anti-rubber-stamp control; `generate_top3_report` has a specific Devil's Advocate section (`core/orchestrator/legacy.py:4155-4157`, `core/orchestrator/legacy.py:4021-4035`).
- Verdict: Essential.

B - Input integrity:

- Input source: debate history and decision brief (`services/debate_chamber.py:1860-1872`).
- Validation: no structured schema, but gets normalized via signal footer.
- Input health: Solid.

C - Output quality:

- Output: `DebateMessage(role="devils_advocate")` and `devils_advocate_question` (`services/debate_chamber.py:1875-1886`).
- Consumed by CIO prompt (`services/debate_chamber.py:2379`) and markdown report extractor (`core/orchestrator/legacy.py:4021-4035`).
- Output health: Solid.

D - Role clarity:

- Clear adversarial stress-test role.
- Role clarity: Sharp.

E - Pattern fit:

- Current: sequential immediately before CIO.
- Correct: yes, since it needs full debate transcript.
- Pattern fit: Correct.

F - Error resilience:

- LLM retry/empty response guard applies.
- No fallback challenge if LLM fails; whole graph guard may fail.
- Resilience: Partial.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 8 | Solid |
| Input Integrity | 8 | Solid |
| Output Quality | 8 | Solid |
| Role Clarity | 10 | Sharp |
| Pattern Fit | 10 | Correct |
| Error Resilience | 6 | Partial |
| Total | 8.3/10 | Keep, add deterministic fallback |

### Agent audit: cio_judge

File location: `services/debate_chamber.py:2265-2523`

A - Necessity test:

- Unique task: final CIO synthesis into validated `CIOVerdict` (`services/debate_chamber.py:2477-2491`), fallback verdict on parsing failure (`services/debate_chamber.py:2492-2507`).
- Could be absorbed: no; this is the final decision agent.
- Removal impact: no structured verdict, no scoring/sizing/reporting.
- Verdict: Essential.

B - Input integrity:

- Input source: current price, technical indicators, fair value, consensus metadata, decision brief, transcript, Devil's Advocate challenge (`services/debate_chamber.py:2274-2380`).
- Validation: invalid current price produces HOLD fallback (`services/debate_chamber.py:2286-2312`); Pydantic validates final verdict (`services/debate_chamber.py:2490`).
- Input health: Solid.

C - Output quality:

- Output: JSON-serialized `CIOVerdict` (`services/debate_chamber.py:2490`, `services/debate_chamber.py:2523`).
- Consumed by orchestrator `_run_single_debate` validation (`core/orchestrator/legacy.py:3094-3104`) and ranking/reporting.
- Output health: Solid.

D - Role clarity:

- Too much responsibility: computes trade envelope (`services/debate_chamber.py:2032-2111`), classifies deterministic signals (`services/debate_chamber.py:1903-1975`), builds prompt/schema hint (`services/debate_chamber.py:2355-2420`), parses JSON, applies envelope, applies consensus override, applies news adjustment (`services/debate_chamber.py:2422-2490`).
- Better ownership: split deterministic `TradeEnvelopeBuilder` and `VerdictPostProcessor` from LLM CIO.
- Role clarity: Blurry.

E - Pattern fit:

- Current: final supervisor/aggregator.
- Correct: yes, but deterministic pre/post should be separate stages.
- Pattern fit: Correct with internal split needed.

F - Error resilience:

- Good fallback on invalid price and parse failure.
- Fallback still records success in ledger even on parse fallback (`services/debate_chamber.py:2492-2522`), which is okay for artifact survival but hides output-quality degradation unless monitored.
- Resilience: Robust but should surface fallback status.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 10 | Solid |
| Input Integrity | 9 | Solid |
| Output Quality | 8 | Solid |
| Role Clarity | 5 | Blurry |
| Pattern Fit | 8 | Correct |
| Error Resilience | 8 | Robust |
| Total | 8.0/10 | Keep, split deterministic responsibilities |

### Agent audit: SingleAgentAnalyzer (optional baseline)

File location: `services/single_agent_analyzer.py:79-330`

A - Necessity test:

- Unique task: thesis/baseline comparison against multi-agent pipeline (`core/orchestrator/legacy.py:4431-4444`, `core/orchestrator/legacy.py:4604-4619`).
- Could be absorbed: not into production multi-agent path; it is intentionally optional.
- Removal impact: `--mode single` and `--mode compare` break, but default `--mode multi` remains intact.
- Verdict: Essential for comparison mode, not production-critical.

B - Input integrity:

- Input source: tickers from orchestrator and its own market/fundamental/sentiment fetches (`services/single_agent_analyzer.py:259-281`, `services/single_agent_analyzer.py:338-365`).
- Validation: typed `SingleAgentVerdict` and `SingleAgentResult` (`services/single_agent_analyzer.py:39-77`).
- Input health: Solid.

C - Output quality:

- Output: Pydantic `SingleAgentResult` persisted per ticker (`core/orchestrator/legacy.py:3583-3592`).
- Consumed only by `ComparisonReporter` (`core/comparison_reporter.py:55-126`).
- Output health: Solid, optional.

D - Role clarity:

- Clear one-shot baseline role.
- Role clarity: Sharp.

E - Pattern fit:

- Current: sequential batch loop (`services/single_agent_analyzer.py:301-330`).
- Better: bounded parallel, because per-ticker analyses are independent.
- Pattern fit: Suboptimal.

F - Error resilience:

- Timeout wrapper and failure result exist (`services/single_agent_analyzer.py:269-299`).
- Batch preserves failures (`services/single_agent_analyzer.py:311-322`).
- Resilience: Robust.

G - Agent health score:

| Dimension | Score | Status |
|---|---:|---|
| Necessity | 7 | Optional |
| Input Integrity | 8 | Solid |
| Output Quality | 8 | Solid |
| Role Clarity | 9 | Sharp |
| Pattern Fit | 5 | Suboptimal |
| Error Resilience | 8 | Robust |
| Total | 7.5/10 | Keep optional, parallelize |

## Step 3 - Pipeline Flow Audit

### A. Data handoff integrity

| From | To | Data Passed | Schema Match | Risk |
|---|---|---|---|---|
| `run_quant_filter.py` | `top10_candidates.json` | ranked candidate records | Partial | JSON shape is loosely normalized by aliases in `core/orchestrator/legacy.py:2471-2491`; invalid candidates may fall back to raw if all normalize fails (`core/orchestrator/legacy.py:2515-2520`). |
| `top10_candidates.json` | `parse_report` | candidate list -> tickers | Yes | Ticker validation and critical-risk filtering exist (`core/orchestrator/legacy.py:2584-2625`). |
| `parse_report` | `run_batch_debates` | ticker list + sector map | Yes | Solid; sector defaults to unknown (`core/orchestrator/legacy.py:2628-2654`). |
| `run_batch_debates` | `DebateChamber.run` | ticker string | Yes | Per-ticker guard, budget, semaphore, RPM controls exist (`core/orchestrator/legacy.py:3149-3420`). |
| `DebateChamber.run` | parallel scouts | shared `DebateChamberState` | Partial | State schema exists but only `debate_history` has reducer; other fields are last-write-wins (`schemas/debate.py:473-477`). |
| fundamental | synthesizer | `fundamental_data`, `fair_value_estimate` | Partial | String brief and numeric fair value; no typed scout schema. |
| chartist | synthesizer/CIO | `technical_data`, `technical_indicators` | Partial | Works inside DebateChamber but gets dropped by orchestrator result (`core/orchestrator/legacy.py:3113-3137`). |
| sentiment | synthesizer/CIO | `sentiment_data`, `news_brief`, adjustment | Partial | News is fetched once here, then fetched again post-debate (`core/orchestrator/legacy.py:2908-2909`). |
| synthesizer | bull/bear/CIO | `raw_data`, `decision_brief`, metadata | Yes | Strong internal handoff (`services/debate_chamber.py:1669-1674`). |
| bull | bear | latest bull message | Yes | Bear explicitly consumes latest bull (`services/debate_chamber.py:1727-1737`). |
| bull/bear/scouts | consensus | free-text signals parsed by regex | No | Signal extraction is heuristic (`services/debate_chamber.py:636-662`); no typed vote packet. |
| consensus | router | consensus fields | Yes | Router consumes state fields (`services/debate_chamber.py:427-440`). |
| state_cleaner | history reducer | sentinel message | Yes | Reducer contract is explicit (`schemas/debate.py:446-456`). |
| devils_advocate | CIO/report | challenge message and question | Yes | CIO uses question (`services/debate_chamber.py:2379`), report extracts role (`core/orchestrator/legacy.py:4021-4035`). |
| CIO | orchestrator | `final_verdict` JSON string | Yes | Re-validated into `CIOVerdict` (`core/orchestrator/legacy.py:3094-3104`). |
| DebateChamber result | `_run_single_debate` | final state subset | No | Drops `technical_indicators`, top-level `news_brief`, `devils_advocate_question`; direct `run_debate.py` drops even more fields (`run_debate.py:1017-1030`). |
| `_run_single_debate` | risk governor | verdict + result fields | Partial | Risk governor looks for fields not preserved (`core/orchestrator/legacy.py:2807-2818`). |
| results | `full_batch_results.json` | list of result dicts | Partial | Current artifact has `agent_votes`, consensus, history, verdict, risk, but not `technical_indicators`. |
| results | direct latest debate via `run_debate.py` | report dict | No | Direct wrapper omits top-level `agent_votes`, consensus fields, and history confidence/position (`run_debate.py:1017-1030`). |

### B. Execution order validation

Sequential segments:

- Candidate validation -> regime -> candidate intake -> provider health -> debates -> scoring -> sizing -> persistence is logically correct (`core/orchestrator/legacy.py:4269-4628`).
- Bull before bear is correct because bear explicitly attacks latest bull (`services/debate_chamber.py:1727-1737`).
- Consensus after bear is correct.
- Devil's Advocate before CIO is correct.
- Documentation mismatch: router doc says "2 rounds complete" (`services/debate_chamber.py:430-432`), but code exits on `round_count >= MAX_DEBATE_ROUNDS` where `MAX_DEBATE_ROUNDS = 3` (`services/debate_chamber.py:451`, `services/debate_chamber.py:437`).

Parallel segments:

- Scout fan-out is valid because fundamental/chartist/sentiment depend only on initial state.
- Per-ticker debates are truly independent and correctly awaited with `asyncio.gather(..., return_exceptions=True)` (`core/orchestrator/legacy.py:3415-3419`).
- Provider health checks are correctly parallelized (`core/provider_health.py:31-36`).
- Risk: non-`debate_history` state fields are last-write-wins (`schemas/debate.py:473-477`), so nodes should return all updates instead of mutating state.

Hierarchical segments:

- Orchestrator delegates per ticker to DebateChamber and normalizes failures (`core/orchestrator/legacy.py:3078-3145`, `core/orchestrator/legacy.py:3149-3420`).
- Adaptive planner returns deterministic recovery actions (`core/adaptive_planner.py:26-49`, `core/adaptive_planner.py:145-202`).
- CIO collects worker outputs and validates via Pydantic.
- Worker failure handling is good at outer guard level but uneven inside `devils_advocate` and short LLM responses.

### C. Dead zones and bottlenecks

Dead zones:

- `CONSENSUS_PROMPT`, `STATE_CLEANER_PROMPT`, and `ConsensusSchema` are required/loaded but not consumed (`services/debate_prompt_registry.py:19-20`, `services/debate_chamber.py:377-414`).
- `core/handoff_envelope.py` defines validation contracts (`core/handoff_envelope.py:24-127`) but production agent handoffs use plain dict/string state; only observation logging creates an envelope (`services/debate_chamber.py:677-685`).
- Direct `run_debate.py` loses agent vote/position/confidence data despite those being produced by DebateChamber (`run_debate.py:1017-1030`).

Bottlenecks:

- `SingleAgentAnalyzer.analyze_batch` is sequential (`services/single_agent_analyzer.py:301-330`).
- Direct `run_debate.py` processes tickers sequentially (`run_debate.py:1204-1206`), unlike orchestrator batch debates.
- `cio_judge` is a single overloaded stage doing deterministic precompute, LLM synthesis, post-processing, and validation.

Redundant passes:

- News is fetched inside sentiment and again in orchestrator postprocess (`services/debate_chamber.py:237-278`, `core/orchestrator/legacy.py:2779-2795`, `core/orchestrator/legacy.py:2908-2909`).
- Risk governor can run in direct debate wrapper and orchestrator, but with different payload shapes.

Missing checkpoints:

- No typed validation for scout outputs before consensus; votes are extracted from free text.
- No contract test ensuring `DebateChamber.run` final state fields survive into `full_batch_results.json`.
- No unified adapter for `run_debate.py`, orchestrator JSON, and API normalized results.

## Step 4 - Pipeline Restructuring Recommendation

### A. Problems summary

| Issue ID | Agent/Stage | Problem | Severity | Type |
|---|---|---|---|---|
| PL-01 | chartist -> orchestrator | `technical_indicators` are produced but not persisted in `_run_single_debate`, while risk governor tries to consume them. | High | Handoff |
| PL-02 | sentiment/news | News is fetched inside debate and again post-debate; top-level artifact can diverge from CIO-applied news adjustment. | High | Flow |
| PL-03 | consensus/state_cleaner | LLM prompt files and `ConsensusSchema` are required but unused after deterministic refactor. | Medium | Dead zone |
| PL-04 | run_debate.py wrapper | Direct debate artifacts omit `agent_votes`, top-level consensus, and message position/confidence. | High | Output |
| PL-05 | all scout agents | Actual handoffs are strings/dicts, despite a typed `HandoffEnvelope` module existing. | Medium | Schema |
| PL-06 | cio_judge | CIO does too many jobs: envelope compute, conflict classification, prompt, parse, overrides, news adjustment. | Medium | Role |
| PL-07 | SingleAgentAnalyzer | Optional baseline runs per ticker sequentially. | Low | Performance |
| PL-08 | chartist partial metadata | Some metadata updates are state mutations, not returned updates. | Medium | State |
| PL-09 | router docs | Router comment says 2 rounds; implementation uses max 3 rounds. | Low | Maintainability |

### B. Restructuring decisions

| Agent | Current State | Decision | Reason |
|---|---|---|---|
| fundamental | Parallel scout | Keep as-is | Essential, well-scoped, solid fallback. Add typed scout output later. |
| chartist | Parallel scout | Split into two stages | Deterministic indicators should be a typed feature stage; LLM commentary can remain chartist. |
| sentiment | Parallel scout | Split/move news provider | Social sentiment and news enrichment are separate data products; remove post-debate duplicate fetch. |
| synthesizer | Aggregator | Keep, rename optional | Function is context builder/RAG aggregator, not an LLM synthesizer. |
| bullish_analyst | Sequential worker | Keep as-is | Correct dependency and role. |
| bearish_auditor | Sequential worker | Keep as-is | Correct dependency and role. |
| consensus_evaluator | Reducer/router | Keep, remove stale prompt/schema | Deterministic consensus is the right pattern; config should match reality. |
| state_cleaner | Reducer | Keep, remove stale prompt | Deterministic reducer is useful; prompt config is dead. |
| devils_advocate | Sequential adversary | Keep, add fallback | Important anti-rubber-stamp role; add deterministic fallback if LLM fails. |
| cio_judge | Supervisor/aggregator | Split internals | Move envelope and post-processing out of the LLM node. |
| SingleAgentAnalyzer | Optional sequential baseline | Change execution pattern | Run bounded parallel in single/compare modes. |
| Result adapter/writers | Fragmented wrappers | New agent/stage needed | Add one canonical `DebateResultAdapter` used by orchestrator, run_debate, and API. |

### C. Recommended pipeline architecture

```text
INPUT candidates
  |
  v
[CandidateValidator + RegimeGate]
  |
  v
[ProviderHealthGate]
  |
  v
bounded parallel per ticker
  |
  +--> [MarketDataPrefetch]
          |
          +--> [FundamentalScout] --------+
          +--> [TechnicalFeatureBuilder] -+--> [ScoutPacketValidator]
          |          |
          |          +--> [ChartistCommentary]
          +--> [SocialSentimentScout]
          +--> [NewsContextProvider]
                                      |
                                      v
                              [ContextBuilder/RAG]
                                      |
                                      v
                              [BullishAnalyst]
                                      |
                                      v
                              [BearishAuditor]
                                      |
                                      v
                              [VoteReducer + Router]
                                  |             |
                         [StateCompactor]       |
                                  |             v
                                  +-----> [DevilsAdvocate]
                                                |
                                                v
                                     [TradeEnvelopeBuilder]
                                                |
                                                v
                                          [CIOJudge]
                                                |
                                                v
                                [VerdictPostProcessor/Validator]
                                                |
                                                v
                         [RiskGovernor] -> [PositionSizer] -> [ArtifactWriter]
```

Structural improvements:

- Correctness: typed `ScoutPacket`/result adapter preserves technicals, votes, confidence, and news fields across all exits.
- Performance: single-agent comparison and direct debate batch can use bounded concurrency.
- Maintainability: deterministic trade-envelope logic moves out of CIO LLM prompt assembly; dead prompts removed or reactivated intentionally.

### D. Implementation roadmap

| Priority | Change | Agents Affected | Effort | Impact |
|---|---|---|---|---|
| P0 | Preserve `technical_indicators`, `news_brief`, `devils_advocate_question`, and consensus fields in `_run_single_debate` and `run_debate.py` artifacts. | chartist, sentiment, devils_advocate, consensus, CIO | Low | Critical artifact truth fix |
| P0 | Introduce one `DebateResultAdapter` used by orchestrator, run_debate, and API result adapter. | all output stages | Medium | Eliminates output divergence |
| P1 | Remove duplicate post-debate news fetch or make it read debate metadata only. | sentiment/news, CIO, orchestrator postprocess | Low | Prevents confidence/news mismatch |
| P1 | Return metadata updates from every node; stop relying on state mutation. | chartist, sentiment, synthesizer | Low | Safer LangGraph state behavior |
| P1 | Remove unused consensus/state-cleaner prompt requirements, or reintroduce LLM consensus intentionally. | consensus_evaluator, state_cleaner | Low | Removes dead config |
| P1 | Split CIO deterministic envelope/postprocessor from LLM judge. | CIO | Medium | Easier testing and safer final verdicts |
| P2 | Add typed `ScoutPacket`/`VotePacket` validation before consensus. | scout agents, consensus | Medium | Better handoff integrity |
| P2 | Bounded parallelize `SingleAgentAnalyzer.analyze_batch` and direct `run_debate.py` batches. | optional baseline, direct debate | Medium | Faster non-default workflows |

### E. Code templates for key changes

#### P0 - Preserve rich DebateChamber state in orchestrator result

```python
# core/orchestrator/legacy.py, inside _run_single_debate return payload
return {
    "ticker": result["ticker"],
    "verdict": verdict_dict,
    "debate_rounds": result["round_count"],
    "consensus_reached": result.get("consensus_reached", False),
    "consensus_method": result.get("consensus_method"),
    "dissenting_agents": result.get("dissenting_agents", []),
    "agent_votes": result.get("agent_votes", []),
    "disagreement_type": result.get("disagreement_type"),
    "devils_advocate_question": result.get("devils_advocate_question", ""),
    "technical_indicators": result.get("technical_indicators", {}),
    "news_brief": result.get("news_brief", ""),
    "news_confidence_adjustment": result.get("news_confidence_adjustment", 0.0),
    "debate_history": [...],
    "raw_data_summary": result["raw_data"],
    "metadata": result.get("metadata", {}),
    "error": None,
    "status": "success",
    "conviction_score": 0.0,
}
```

#### P0 - Canonical result adapter used by all exits

```python
class DebateResultAdapter:
    def from_state(self, state: dict, *, include_content: bool = True) -> dict:
        verdict = self.parse_verdict(state.get("final_verdict"))
        history = [self.normalize_message(m) for m in state.get("debate_history", [])]
        return {
            "ticker": state.get("ticker") or verdict.get("ticker"),
            "verdict": verdict,
            "debate_rounds": state.get("round_count", 0),
            "consensus_reached": state.get("consensus_reached", False),
            "consensus_method": state.get("consensus_method"),
            "dissenting_agents": state.get("dissenting_agents", []),
            "agent_votes": state.get("agent_votes", []),
            "disagreement_type": state.get("disagreement_type"),
            "devils_advocate_question": state.get("devils_advocate_question", ""),
            "technical_indicators": state.get("technical_indicators", {}),
            "raw_data_summary": state.get("raw_data", ""),
            "debate_history": history if include_content else [],
            "metadata": state.get("metadata", {}),
            "error": state.get("error"),
            "status": "failed" if state.get("error") else "success",
        }
```

#### P1 - Remove duplicate news fetch

```python
def _attach_news_signal_from_metadata(result: dict[str, Any]) -> None:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    result["news_sentiment"] = metadata.get("news_overall_sentiment", "UNKNOWN")
    result["news_confidence_adjustment"] = metadata.get("news_confidence_adjustment", 0.0)
    result["news_brief"] = result.get("news_brief") or metadata.get("news_brief", "")

# Replace _attach_news_signal(ticker, result) in _enhance_completed_results
# with _attach_news_signal_from_metadata(result).
```

#### P1 - Return metadata instead of mutating state only

```python
# Bad: mutation only may be lost by graph reducers.
state["metadata"] = _metadata_with_planner_note(state, decision)
return {"technical_data": content, "technical_indicators": tech_indicators}

# Better: return the metadata as part of the node update.
metadata = _metadata_with_planner_note(state, decision)
return {
    "technical_data": content,
    "technical_indicators": tech_indicators,
    "metadata": metadata,
}
```

#### P1 - Agent splitting pattern for CIO

```python
class TradeEnvelopeBuilder:
    def build(self, state: DebateChamberState) -> dict:
        current_price = state.get("current_price", 0.0)
        fair_value = state.get("fair_value_estimate", 0.0)
        tech = state.get("technical_indicators", {})
        return compute_trade_envelope(current_price, fair_value, tech)

class VerdictPostProcessor:
    def apply(self, parsed: dict, state: DebateChamberState, envelope: dict) -> CIOVerdict:
        parsed = apply_envelope(parsed, envelope)
        parsed = apply_consensus_override(parsed, state)
        parsed = apply_news_adjustment(parsed, state)
        return CIOVerdict(**parsed)
```

#### P2 - Handoff schema validation

```python
class ScoutPacket(BaseModel):
    agent: Literal["fundamental_scout", "chartist", "sentiment_specialist"]
    ticker: str
    brief: str
    position: Literal["BUY", "HOLD", "AVOID", "UNKNOWN"]
    confidence: float = Field(ge=0.0, le=1.0)
    metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

def validate_scout_packet(raw: dict[str, Any]) -> ScoutPacket:
    return ScoutPacket.model_validate(raw)
```

#### P2 - Bounded parallel single-agent baseline

```python
async def analyze_batch(self, tickers: list[str], run_id: str) -> list[SingleAgentResult]:
    sem = asyncio.Semaphore(3)

    async def one(ticker: str) -> SingleAgentResult:
        async with sem:
            try:
                return await self.analyze(ticker.strip().upper(), run_id)
            except Exception as exc:
                return self._failure_result(
                    ticker=ticker.strip().upper(),
                    run_id=run_id,
                    status="failed",
                    error=str(exc),
                    duration=0.0,
                    context_tokens=0,
                )

    return list(await asyncio.gather(*(one(t) for t in tickers)))
```

#### Supervisor/worker failure pattern

```python
async def run_worker_with_planner(worker_name: str, coro: Awaitable[dict], ctx: PlannerContext) -> dict:
    try:
        return await asyncio.wait_for(coro, timeout=ctx.timeout_seconds)
    except Exception as exc:
        failure = classify_exception(exc, source=worker_name).model_dump(mode="json")
        decision = DEFAULT_PLANNER.plan(ctx.model_copy(update={"failure_record": failure}))
        if decision.action is PlanAction.PROCEED_PARTIAL:
            return {"metadata": {"planner_context_notes": [decision.context_note]}}
        if decision.action is PlanAction.SKIP_TICKER:
            return {"error": decision.reason}
        raise
```

## Step 5 - Final Pipeline Verdict

```text
PIPELINE AUDIT VERDICT
------------------------------------------------------------
Overall Pipeline Health  : 74/100
Agents Audited           : 10 production + 1 optional baseline
Agents: Keep as-is       : 4
Agents: Needs changes    : 7
Agents: Remove           : 0 production agents
Dead Config to Remove    : 2 prompt requirements + 1 unused schema
New Stages Recommended   : 3
Critical Issues          : 4
------------------------------------------------------------
Pipeline Status:
Needs Minor Fixes trending toward Major Rework at artifact boundaries.
Core architecture is good; output-contract integrity is the weak point.
------------------------------------------------------------
Top 3 Most Critical Changes:
1. Preserve full DebateChamber state into orchestrator/direct-debate artifacts.
2. Remove duplicate news fetch and make CIO-used news the artifact source of truth.
3. Add a canonical DebateResultAdapter and typed scout/vote handoff validation.
------------------------------------------------------------
```

Final verdict:

The multi-agent design is fundamentally sound. The parallel scout -> adversarial debate -> deterministic consensus -> adversarial challenge -> CIO verdict pattern is the right architecture for this domain.

The main problem is not "too many agents." The main problem is boundary erosion: agents produce useful state, but wrappers and artifacts do not preserve it consistently. The recommended restructuring should keep all production agents, split a few overloaded responsibilities, and harden handoff schemas and result adapters before changing the debate logic itself.
