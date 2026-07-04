# De-Overengineering Audit - IDX Fundamental Analysis

Date: 2026-07-03  
Branch: `testing-version`  
Mode: pragmatic simplification audit  
Scope: production pipeline first, advisory/research layers second  

---

## Implementation Status Update - 2026-07-04

This audit remains the design snapshot that motivated the cleanup. The findings
below intentionally preserve the original evidence, but several recommendations
have already moved from "recommended" to "implemented."

| Phase | Current status |
| --- | --- |
| P0 owner map | Implemented in `docs/architecture_decision_map.md`. |
| P1 forecast ranking influence | Disabled by default with `FORECAST_EV_RANKING_ENABLED=false`; forecast output remains advisory/report-visible. |
| P2 backtest auto-evaluation | Removed from default pipeline startup and exposed through explicit maintenance command. |
| P3 trade envelope | Extracted into `core/trade_envelope.py`; `DebateChamber` keeps compatibility wrapper behavior. |
| P4 display semantics | Normalized display packet added before Markdown/Rich formatting. |
| P5 prompt requirements | Runtime-required prompts split from archived/research prompts. |
| P6 research comparison | Default `idx pipeline` remains production-oriented; comparison is explicit via `idx research compare`. |
| P8 fair value authority | Fair value remains strict for display quality but no longer universally overrides swing execution. |
| P7 legacy strangler | Started with `PipelineRunner` and a narrower `core/orchestrator/pipeline.py` import surface. |
| P9 docs/samples | Current phase: README, owner map, research-doc status, and curated sample semantics aligned. |

Use `docs/de_overengineering_execution_checklist_2026-07-03.md` for the live
phase checklist. Use the finding sections below as historical evidence, not as
proof that every described behavior is still current.

---

## Executive Verdict

Project ini memang sudah masuk zona overengineering, tapi bukan karena semua
kompleksitasnya salah. Core trading engine-nya punya beberapa bagian yang kuat:
quant filter, Python-computed trade envelope, deterministic Risk Governor,
IDX-specific mechanics, dan artifact/reporting discipline. Yang membuat sistem
terasa berat adalah decision authority menyebar ke terlalu banyak layer:
orchestrator, debate chamber, prompt corpus, schema validators, fair-value
calculator, forecasting layer, report formatter, backtest evaluator, dan docs
semuanya ikut memegang semantics keputusan.

Verdict singkat:

- Keep: deterministic trading/risk primitives yang menjawab pertanyaan eksekusi.
- Simplify: orchestrator, debate chamber boundaries, report semantics, fair value
  usage, duplicate setup validation.
- Move to advisory/research: forecasting EV, single-agent comparison, deep
  explainability/audit layers, most research scripts.
- Remove/archive candidates: required-but-unused prompt contracts, legacy import
  surfaces, stale docs that encode old decisions as if current.

Target sehat bukan membuat project kecil secara brutal. Target sehat adalah satu
production spine yang mudah dijelaskan:

```text
Candidate screen -> Trade envelope -> Debate/analysis -> Risk Governor -> Rank/sizing -> Report
```

Semua hal lain harus menjadi context, advisory, research, atau artifact
validation, bukan ikut diam-diam mengubah keputusan utama.

---

## Evidence Snapshot

### Hotspot ukuran dan coupling

Line-count scan pada repo utama menunjukkan hotspot terbesar:

| Area | Approx lines | Assessment |
| --- | ---: | --- |
| `core/orchestrator/legacy.py` | 6191 | God module: CLI rendering, dependency checks, batch execution, forecast injection, scoring, sizing, persistence, reports. |
| `services/debate_chamber.py` | 4896 | God service: graph orchestration, data fetching, prompt execution, technical computation, trade envelope, CIO parsing, preflight. |
| `services/fair_value_calculator.py` | 1779 | Valuable but too broad for swing context: valuation, sector benchmarks, quality gates, report text. |
| `core/quant_filter/pipeline.py` | 1726 | Large but mostly acceptable because screener domain is inherently broad. |
| `services/report_formatter.py` | 1537 | Presentation layer also carries semantics for risk, valuation, forecast, breaking news. |
| `core/risk_governor.py` | 912 | Large but high-signal; this is a real production boundary. |

The problem is not simply file size. The problem is that large files are also
decision authorities.

### Current public/product contract

At audit time, README stated the intended production pipeline as:

```text
Quant Screener -> Regime Detection -> Debate Chamber -> Risk Governor -> Report
```

The current README now expands that path to include the extracted trade envelope,
ranking/sizing, and display packet boundaries. The decision-support philosophy is
still correct: the implementation should limit the number of layers that can
alter rank, sizing, entry/target/stop, or deployability.

---

## System Map

### Production spine

These components should remain production-critical:

1. Quant filter: universe reduction and candidate ranking.
2. Regime detection: market context and defensive gating.
3. Trade envelope: deterministic entry, target, stop, R/R.
4. Debate/CIO: qualitative synthesis and challenge layer.
5. Risk Governor: final deterministic deployability gate.
6. Ranking/sizing: converts eligible results into action priority.
7. Report: explains what happened.

### Advisory layer

These components should inform or explain, but not own decisions:

- Forecasting EV and volatility.
- Fair value range and valuation band context.
- News/breaking-news context.
- Historical scorer and realized outcome hints.
- Single-agent comparison.
- Explainability auditor.

### Research/experimental layer

These should not run implicitly in normal production unless explicitly requested:

- Forecast model validation and research commands.
- Backtest recalibration scripts.
- Single-agent baseline comparison.
- LSTM/Prophet/experimental-unused model families.
- Long-form audit and research documents.

### Legacy/dead-zone layer

These need cleanup or archival:

- `core/orchestrator/legacy.py` responsibilities that have not yet moved behind
  focused services.
- Tests that still import private helpers from `legacy.py`.
- Compatibility artifacts that are useful but should be isolated behind a
  single compatibility writer.

---

## Findings

### F1 - Orchestrator is the largest overengineering center

Classification: Simplify  
Severity: High  

`core/orchestrator/legacy.py` is not just an orchestrator. It performs prompt-pack
linting, backtest auto-evaluation, dependency validation, macro/sector refresh,
candidate cache validation, regime detection, single-agent baseline runs, provider
health checks, batch debate execution, forecast injection, risk annotation,
conviction scoring, sizing, persistence, formatter reports, top-3 reports, and
comparison reports.

Evidence:

- Main pipeline docstring says `Validate -> Regime -> Parse -> Debate -> Rank -> Report`.
- In practice, `main()` also runs `evaluate_memory(write=True)` before the
  pipeline decision path.
- It conditionally runs `SingleAgentAnalyzer()` inside `single` and `compare`
  modes.
- It always calls `_enhance_completed_results(...)`, `_inject_forecast_reports(...)`,
  `_log_risk_warn_distribution(...)`, risk annotation, sizing, report writing,
  and artifact persistence.
- At audit time, `core/orchestrator/pipeline.py` simply re-exported `legacy.py`.
  Phase 7 replaced this with a narrower import surface plus compatibility
  fallback.

Why this is overengineered:

- One file owns too many unrelated reasons for failure.
- A normal pipeline run can mutate backtest memory before it even starts debating.
- Forecasting, comparison research, and reporting details live in the same module
  as candidate intake and batch execution.
- Tests import deep private helpers from `legacy.py`, making it harder to split.

Recommended simplification:

- Define a small production runner that only coordinates stage boundaries.
- Move backtest auto-evaluation out of default pipeline into explicit command or
  pre-run maintenance mode.
- Move forecast injection behind an optional advisory enrichment interface.
- Move report writing and artifact compatibility into a reporting/persistence
  service.
- Keep the old `legacy.py` as compatibility for one transition, but stop adding
  behavior there.

Target state:

```text
PipelineRunner
  -> CandidateService
  -> DebateService
  -> RiskService
  -> RankingService
  -> ReportService
```

The runner should not know how forecast EV is downweighted, how Markdown renders
Risk Governor, or how backtest memory is scored.

---

### F2 - Decision logic is duplicated across DebateChamber, Risk Governor, schemas, and orchestrator

Classification: Simplify  
Severity: High  

Setup validity is checked in at least four places:

- `services/debate_chamber.py` computes entry/target/stop and rejects
  `stop_inside_noise`, `target_collapsed`, and `rr_too_low`.
- `schemas/debate.py` validates CIO verdict geometry and auto-computes expected
  return/R/R.
- `core/orchestrator/legacy.py` has `validate_setup_coherence(...)` and clears
  numeric setup fields on reject.
- `core/risk_governor.py` recomputes R/R, validates price geometry, hard rejects
  unbuyable ratings, overvaluation, liquidity, ex-date, and implausible R/R.

This is not all wrong. The system benefits from defense in depth. But today the
layers are not clearly ranked by authority.

Why this is overengineered:

- It is unclear which layer owns the canonical rejection reason when multiple
  gates fire.
- Every new decision field risks being threaded through schema, prompt, report,
  risk, tests, and docs.
- Some duplicate checks are safeguards; others are business logic repeated in a
  second location.

Recommended simplification:

- Make `TradeEnvelope` the canonical owner of entry/target/stop/R/R geometry.
- Make `RiskGovernor` the canonical owner of deployability/actionability.
- Make schema validators structural only: parseability, field ranges, and
  computed display fields, not business-level gate policy.
- Make orchestrator trust stage outputs rather than revalidating setup geometry
  except at stage boundaries.

Target authority map:

| Question | Canonical owner |
| --- | --- |
| What are entry, target, stop, R/R? | Trade envelope |
| Is the setup executable now? | Risk Governor |
| What is the final qualitative verdict? | CIO/debate |
| What should be ranked/sized? | Ranking/sizing service after Risk Governor |
| How should this be displayed? | Report formatter |

---

### F3 - DebateChamber is both analysis graph and market-data/trade-engine

Classification: Simplify  
Severity: High  

`services/debate_chamber.py` currently owns:

- LangGraph state machine.
- Prompt loading and model invocation.
- News fetching and sentiment metadata.
- Stockbit/fundamental ingestion through scout nodes.
- Technical indicator computation.
- Weekly data fetch.
- Fair value payload construction.
- Trade envelope computation.
- CIO JSON contract and fallback behavior.
- Preflight rejection.

Some of this belongs together, but too much of it is fused into one service.

What to keep:

- Multi-agent debate flow.
- CIO final synthesis.
- Structured debate state.
- Python-computed trade levels passed verbatim to CIO.

What to split:

- Market data and technical computation into a `MarketContextBuilder`.
- Fair value payload into a `ValuationContextService`.
- Trade envelope into a standalone deterministic `TradeEnvelopeService`.
- News/sentiment context into a `NewsContextService`.
- LLM graph remains `DebateChamber`, consuming context instead of producing all
  context itself.

Why this matters:

- Today, changing a technical or valuation concept often means editing the debate
  chamber.
- Debate should be an interpretation layer, not the owner of every data-producing
  dependency.
- Smaller services would make failures easier to isolate: data failure, envelope
  failure, LLM failure, or report failure.

---

### F4 - Forecasting is advisory by intent but still affects ranking

Classification: Move to Advisory/Research  
Severity: High  
Status: Phase 1 disables forecast ranking influence by default; this section is
retained as the original finding and rationale.

Forecasting has a sensible conceptual direction: predict expected value,
`p_target`, `p_stop`, and volatility for a 5-20 day horizon. At audit time, the
integration crossed from advisory into scoring:

- `_inject_forecast_reports(...)` enriches debate results after debate.
- `_forecast_ranking_ev(...)` uses full EV for `production` and downweights
  `research_only` EV by `0.35`.
- `forecast_ev_pct` is fed into `compute_conviction_score(...)` through
  `apply_ev_adjustment(...)`.
- `failed` validation blocks EV usage, but `research_only` still influences
  ranking.

Why this is overengineered:

- Forecasting is still experimental enough that docs report all sample tickers
  as `research_only` or fallback in live validation.
- Forecasting has optional dependencies, experimental-unused model families, and
  research-only validation states, yet it runs as part of the production
  orchestrator enrichment path.
- The rank effect is subtle: a user may see a final ranking shift without
  realizing a research-only model influenced it.

Recommended simplification:

- Keep `ForecastingService` as CLI and report-only advisory until there is
  production validation on a meaningful liquid universe.
- Remove forecast EV from production ranking by default.
- If retained, gate it behind an explicit config flag such as
  `FORECAST_EV_RANKING_ENABLED=false`.
- Report forecast quality and EV separately as "advisory forecast", not blended
  conviction.

Target state:

```text
Production rank = CIO confidence + R/R + realized history + risk gate
Forecast report = advisory context, never rank/sizing unless explicitly enabled
```

---

### F5 - Fair value remains too central for a swing-trading system

Classification: Simplify / Move to Advisory  
Severity: Medium-High  

The fair-value calculator is useful. It parses difficult Stockbit payloads,
normalizes sector methods, computes ranges, and now has stronger quality gates.
The overengineering issue is not the existence of fair value; it is authority.

Current fair value concepts appear in:

- `services/fair_value_calculator.py` as multi-method valuation and quality gate.
- `services/debate_chamber.py` as injected fair value context in trade envelope
  and CIO prompt.
- `schemas/debate.py` as `fair_value`, `fair_value_low`, `fair_value_high`,
  `valuation_gap`, and overvaluation fields.
- `core/risk_governor.py` as `overvalued` hard reject when `risk_overvalued` is
  true.
- `services/report_formatter.py` as fair-value display, unverified valuation
  suppression, range display, and risk flag display.

What is correct:

- Fair value should be context.
- Fair value quality rejection should prevent unverified valuation from being
  presented as fact.
- Historical valuation band is useful swing context.

What is too much:

- Fair value should not be a primary deployability axis for momentum trades.
- Long-horizon methods like DDM should not have direct authority over 5-20 day
  swing execution.
- Risk Governor should avoid treating "overvalued" as a universal hard reject
  unless the setup is explicitly non-momentum or risk-overvalued is confirmed by
  the envelope policy.

Recommended simplification:

- Keep fair value quality gates and report visibility.
- Make fair value an advisory context field by default.
- Let `historically_expensive` be a soft flag, not a hard reject.
- Reserve hard rejection for broken valuation data, unverified valuation misuse,
  or price geometry/R/R failure.

---

### F6 - Report formatter has become a second semantic engine

Classification: Simplify  
Severity: Medium  

`services/report_formatter.py` is supposed to present output, but it also decides
how to interpret:

- unverified valuation gaps,
- fair value range visibility,
- forecast quality flags,
- Risk Governor labels,
- breaking-news prominence,
- Markdown vs Rich terminal semantics.

Some presentation logic is unavoidable. The problem is that report formatter now
knows too much about internal decision semantics.

Recommended simplification:

- Upstream stages should emit a small `DecisionSummary`/`DisplayPacket` with
  already-normalized fields:
  - actionability label,
  - valuation display state,
  - forecast display state,
  - risk display state,
  - warning bullets.
- Formatter should format that packet only.
- Keep Rich and Markdown renderers, but remove policy interpretation from them.

Benefit:

- A report change will not accidentally alter semantics.
- API/UI can use the same display packet instead of re-deriving labels.

---

### F7 - Prompt contract contains dead or legacy-required components

Classification: Remove/Archive  
Severity: Medium  
Status: Phase 5 split runtime-required prompts from archived/research prompts;
this section is retained as the original finding and rationale.

At audit time, `services/debate_prompt_registry.py` required `CONSENSUS_PROMPT`
and `STATE_CLEANER_PROMPT`. The audit evidence indicated these were loaded into
module constants, but consensus/state-cleaner behavior was mostly deterministic
and these prompt files were not consumed as active LLM calls.

This creates a dead-zone:

- Missing or malformed prompt files can block startup even if live behavior does
  not need them.
- Prompt docs imply active behavior that may not exist.
- Future maintainers may tune prompt text and expect behavior to change.

Recommended simplification:

- Either reintroduce the LLM consensus/state-cleaner intentionally, or stop
  requiring those prompt files.
- Prefer archive: keep prompt files in docs/research or `debate_prompts/archive/`
  if useful historically.
- Update prompt-pack linter to distinguish `required_runtime` vs `archived`.

---

### F8 - Production pipeline runs research/comparison concerns too close to normal execution

Classification: Move to Advisory/Research  
Severity: Medium  
Status: Phase 6 moved comparison into explicit research CLI flow; this section is
retained as the original finding and rationale.

`SingleAgentAnalyzer` is useful for academic comparison, but it should not sit
inside the main production orchestrator as a peer mode unless the product goal is
explicitly evaluation. At audit time, the same applied to comparison reports and
some explainability/auditor paths.

Recommended simplification:

- Keep `idx pipeline` production-oriented.
- Move comparison mode to `idx research compare` or `idx eval compare`.
- Keep generated comparison artifacts out of the normal production artifact flow
  unless explicitly requested.

---

### F9 - Backtest auto-evaluation is side-effectful inside pipeline start

Classification: Simplify  
Severity: Medium  

At pipeline start, `main()` calls `evaluate_memory(write=True)`. That means a
normal analysis run can mutate backtest memory before candidate intake.

Why this is a problem:

- It mixes live analysis with historical bookkeeping.
- It can introduce unexpected writes and delays.
- It increases the number of ways a pipeline run can fail before doing its main
  job.

Recommended simplification:

- Move auto-evaluation to an explicit command:
  - `idx backtest evaluate-open`
  - or `idx maintenance evaluate-memory`
- If automatic behavior is desired, make it opt-in with a clear config flag.
- Default pipeline should read historical outcomes but not mutate them.

---

### F10 - Legacy compatibility is useful, but it is now shaping architecture

Classification: Simplify / Remove later  
Severity: Medium  

Compatibility artifacts and wrappers are valuable during active development, but
today they blur boundaries:

- At audit time, `core/orchestrator/pipeline.py` re-exported all of `legacy.py`;
  Phase 7 has started replacing that with a narrow public runner/facade.
- Tests import private helpers from `legacy.py`.
- Debate outputs still write legacy flat files alongside ticker folders.

Recommended simplification:

- Define a narrow public orchestrator interface and migrate tests to it.
- Keep legacy output files temporarily, but isolate them in one compatibility
  writer.
- Stop making new code depend on private helpers in `legacy.py`.

---

## Subsystem Classification

| Subsystem | Classification | Action |
| --- | --- | --- |
| Quant filter | Keep | Keep as production candidate reducer; avoid adding ranking authority elsewhere. |
| Regime detection | Keep | Keep as market context and risk/sizing input. |
| Trade envelope | Keep, extract | Keep deterministic; move out of DebateChamber into dedicated service. |
| Debate Chamber | Simplify | Keep LLM debate; stop owning all data/context/trade computation. |
| CIO verdict schema | Simplify | Keep contract; reduce business-rule duplication. |
| Risk Governor | Keep | Keep as final actionability authority. |
| Fair value calculator | Advisory | Keep quality/range context; reduce deployability authority. |
| Forecasting service | Advisory/Research | Keep CLI/report; disable ranking effect by default. |
| Report formatter | Simplify | Format normalized display packets, not raw policy. |
| Single-agent analyzer | Research | Move out of production pipeline path. |
| Backtest evaluator | Research/maintenance | Do not mutate memory in normal pipeline start. |
| Prompt registry | Simplify/archive | Split runtime-required prompts from archived prompts. |
| Legacy orchestrator wrapper | Transitional | Create narrow public interface, then shrink `legacy.py`. |

---

## Simplification Roadmap

### P0 - Stop decision authority from spreading further

Goal: freeze the shape before adding features.

Actions:

1. Document canonical owners:
   - Trade geometry: Trade Envelope.
   - Actionability: Risk Governor.
   - Qualitative thesis: CIO/debate.
   - Ranking: Ranking service.
   - Display: Report service.
2. Add a short architecture note to README or docs that says forecasting and
   fair value are advisory unless explicitly promoted.
3. Stop adding new business logic to `core/orchestrator/legacy.py`.
4. Stop adding report-only semantics directly into formatter helpers unless the
   same normalized field is also available to API/UI.

Acceptance:

- A future field can be assigned to exactly one owner.
- New contributors know where a rejection reason must be implemented.

### P1 - Extract production spine without changing behavior

Goal: reduce risk by extracting boundaries first, not rewriting logic.

Actions:

1. Extract `TradeEnvelopeService` from DebateChamber:
   - Inputs: current price, sector, technical indicators, fair value context.
   - Output: accepted/rejected envelope with reason codes.
   - Preserve current behavior exactly.
2. Extract `ForecastAdvisoryService` or config-gate `_inject_forecast_reports`.
   - Default: report-only advisory.
   - Optional flag: allow ranking EV.
3. Extract `PipelinePersistenceService`:
   - full results,
   - merged results,
   - ticker artifacts,
   - legacy compatibility file.
4. Extract `ReportPacketBuilder`:
   - turns raw result into normalized display packet.
   - Rich/Markdown formatters consume it.

Acceptance:

- `legacy.py` loses orchestration-adjacent responsibilities without changing
  public CLI behavior.
- Focused tests still pass around envelope, risk governor, reports, and pipeline
  artifacts.

### P2 - Move research and maintenance out of default production runs

Goal: keep useful research without letting it surprise production.

Actions:

1. Move backtest memory evaluation to explicit command or opt-in config.
2. Move single-agent comparison under a research/evaluation command group.
3. Make forecast ranking disabled by default until validation clears a defined
   production gate.
4. Archive unused prompt contracts or mark them non-runtime.
5. Add `docs/architecture_decision_map.md` with owner matrix and lifecycle tags:
   production, advisory, research, archived.

Acceptance:

- `idx pipeline` has fewer implicit side effects.
- Research functionality remains accessible but explicit.
- Startup failures from unused prompt files are impossible.

---

## Target Architecture

```text
CLI/API
  |
  v
PipelineRunner
  |
  +-- CandidateService
  +-- RegimeService
  +-- MarketContextBuilder
  +-- TradeEnvelopeService
  +-- DebateService
  +-- RiskService
  +-- RankingService
  +-- SizingService
  +-- ReportService

Advisory services:
  +-- ValuationContextService
  +-- ForecastAdvisoryService
  +-- NewsContextService
  +-- HistoricalOutcomeContext

Research services:
  +-- SingleAgentComparison
  +-- ForecastValidation
  +-- BacktestRecalibration
```

Core rule: production services may call advisory services for context, but
advisory services must not silently override production decisions.

---

## What Not To Simplify Aggressively

Do not remove these just because they add code:

- Risk Governor: it is the cleanest deterministic safety boundary.
- IDX tick/ARA/ARB mechanics: market-specific and valuable.
- Trade envelope: deterministic price levels are safer than LLM-generated ones.
- Candidate intake and screener gates: universe reduction is necessary.
- Artifact validation and report consistency: useful when multiple outputs exist.
- Focused tests around debate reliability and risk governor.

The goal is not to make the system naive. The goal is to make authority legible.

---

## Recommended First Implementation After This Audit

The first code refactor should be behavior-preserving:

1. Extract `TradeEnvelopeService` from `DebateChamber`.
2. Add a small owner map doc.
3. Add a config flag to disable forecast EV ranking by default.
4. Move `evaluate_memory(write=True)` out of default pipeline or behind opt-in.

This gives the biggest clarity gain with the lowest chance of changing trading
outputs unexpectedly.

---

## Final Assessment

The system is not "bad overengineering"; it is "successful prototype complexity
that has outgrown its boundaries." Many parts were added for good reasons:
safety, auditability, IDX-specific execution rules, fallback handling, and
research validation. The next stage should not add more intelligence. It should
reduce the number of places that can make a decision.

Best north star:

```text
One production spine.
Many advisory contexts.
Zero hidden decision overrides.
```
