# Architecture Decision Map

Date: 2026-07-03
Status: Current owner map, updated through Phase 9 on 2026-07-04

Sources:

- `docs/de_overengineering_audit_2026-07-03.md`
- `docs/de_overengineering_execution_checklist_2026-07-03.md`
- `docs/decision_semantics.md`

This map freezes decision ownership before de-overengineering work continues. It
does not change runtime behavior. Its job is to make every rejection, ranking,
sizing, and display decision traceable to one owner.

Core rule:

```text
One production spine.
Many advisory contexts.
Zero hidden decision overrides.
```

Production services may consume advisory context, but advisory services must not
silently override production decisions.

## Production Spine

```text
Candidate screen
  -> Regime context
  -> Trade envelope
  -> Debate/CIO
  -> Risk Governor
  -> Ranking
  -> Position sizing
  -> Report
```

## Canonical Owners

| Decision question | Canonical owner | Current implementation | Output authority | Non-owners |
| --- | --- | --- | --- | --- |
| Which stocks enter the production debate queue? | Candidate screen / candidate service | `core/quant_filter/pipeline.py`, orchestrator intake in `core/orchestrator/legacy.py` | Candidate inclusion, screener score, candidate metadata | Forecasting, fair value, report formatter |
| What market regime/context applies? | Regime service | `core/regime.py`, `core/regime_gate.py`, `core/regime_hmm.py` | Regime label, defensive context, threshold context | Report formatter, prompt text, forecasting |
| What are entry, target, stop, and R/R? | Trade envelope | `core/trade_envelope.py`; `DebateChamber` keeps a thin compatibility wrapper | Trade geometry, envelope rejection reasons such as `rr_too_low`, `target_collapsed`, `stop_inside_noise` | CIO prompt, schemas, report formatter, forecasting, standalone fair value |
| Is the setup executable/deployable now? | `RiskGovernor` | `core/risk_governor.py` | Actionability status, hard rejection reasons, `sizing_allowed` | Report formatter, CIO narrative, schemas, ranking |
| What is the qualitative thesis and final CIO verdict? | Debate/CIO layer | `services/debate_chamber.py` | Rating, confidence, thesis, dissent, qualitative risk notes | Risk Governor, ranking, position sizing |
| How are eligible ideas ranked? | Ranking service | Current temporary owner: `core/orchestrator/legacy.py` scoring and top selection | Conviction score, rank order, ranking warning | Forecasting by default, report formatter, fair value |
| How much position can be allocated? | Position sizer | `core/quant_filter/position_sizer.py` after Risk Governor handoff | Lots, capital allocation, risk budget allocation | CIO narrative, report formatter, fair value |
| How should a result be displayed? | Normalized display packet / report service | `services/display_packet.py`, consumed by `services/report_formatter.py` | Labels and warnings already derived from upstream state | New business policy, rejection logic, ranking logic |

If a future change needs to reject, rank, size, or alter trade geometry, assign
it to exactly one row above before implementation.

## Advisory Defaults

Forecasting EV and fair value are advisory by default.

Forecasting may produce report payloads such as expected value, `p_target`,
`p_stop`, volatility, model quality, and validation flags. Those fields can be
shown in reports, but they must not affect production ranking or sizing unless a
phase explicitly promotes them through a named setting and focused tests.

Fair value may provide valuation context, quality warnings, and display ranges.
Unverified valuation must remain suppressed or labeled as unverified. Fair value
does not own trade geometry or deployability. Any hard rejection tied to
valuation belongs in `RiskGovernor` policy, not in the report formatter or
standalone valuation display.

Current production interface: new callers should prefer
`core.orchestrator.runner.PipelineRunner` and `PipelineRunConfig`. The old
`core.orchestrator.legacy` module remains a compatibility wrapper while
candidate intake, advisory enrichment, persistence, and ranking continue to be
extracted over time.

## Lifecycle Tags

| Tag | Meaning | Examples |
| --- | --- | --- |
| Production | Runs in the normal production spine and can affect trade output | Quant screen, regime context, trade envelope, CIO verdict, Risk Governor, ranking, position sizing, report artifacts |
| Advisory | Informs or explains production output, but does not silently override it | Forecast report, fair value range, news context, historical outcome context, explainability notes |
| Research | Explicit evaluation or experimentation path, not default production | Forecast validation, single-agent comparison, backtest recalibration, experimental model families |
| Archived | Historical or compatibility material that should not block runtime | Unused prompt contracts, stale architecture docs, legacy compatibility outputs after replacement readers exist |

## Development Rules

1. Do not add new business logic to `core/orchestrator/legacy.py`. If a
   transition requires touching it, keep the behavior as compatibility glue and
   identify the target owner.
2. Report formatters should not invent new policy states from raw internals.
   Upstream code should emit normalized display state first.
3. Schemas should validate structure, ranges, and parseability. They should not
   become a second owner of business gate policy.
4. Prompt files only change live behavior when they are consumed by an active
   runtime LLM call. Deterministic policy must live in deterministic code.
5. Advisory services may add context to reports, but their effect on ranking,
   sizing, or deployability must be opt-in and visible.
6. Runtime behavior for `idx pipeline` stays stable unless a phase explicitly
   states a behavior change and adds focused tests.
7. When a phase touches a dirty file, inspect the existing diff first and
   preserve unrelated user changes.

## New Decision Checklist

Before adding a new field or gate, answer these questions:

1. Does it change entry, target, stop, or R/R? Put it in the trade envelope.
2. Does it reject or permit deployment? Put it in `RiskGovernor`.
3. Does it alter rank order? Put it in the ranking owner and make the input
   visible.
4. Does it alter position size? Put it in the position sizer.
5. Does it only explain context? Keep it advisory and display it as context.
6. Does it only affect presentation? Put the normalized state upstream, then let
   the formatter render it.
