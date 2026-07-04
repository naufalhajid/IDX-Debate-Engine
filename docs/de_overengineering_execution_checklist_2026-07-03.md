# De-Overengineering Execution Checklist

Date: 2026-07-03  
Branch: `testing-version`  
Source audit: `docs/de_overengineering_audit_2026-07-03.md`  
Execution stance: pragmatic, behavior-preserving first  

This checklist turns the de-overengineering audit into executable phases. The
goal is not to make the system smaller at any cost. The goal is to make decision
authority legible:

```text
One production spine.
Many advisory contexts.
Zero hidden decision overrides.
```

---

## Execution Rules

- [x] Keep `idx pipeline` behavior stable unless a phase explicitly changes it.
- [x] Do not mix behavior changes with extraction-only refactors.
- [x] Preserve existing artifacts and legacy paths until replacement readers are
      verified.
- [x] Treat `RiskGovernor` as the final production deployability authority.
- [x] Treat forecasting and fair value as advisory unless a phase explicitly
      promotes them.
- [x] Every phase must end with focused tests and `git diff --stat`.
- [x] When a phase touches a current dirty file, inspect the existing diff first
      and preserve unrelated user changes.

---

## Phase 0 - Freeze Decision Ownership

Purpose: stop new overengineering while refactors are prepared.

### Tasks

- [x] Create `docs/architecture_decision_map.md`.
- [x] Document canonical owners:
  - [x] Trade geometry: trade envelope.
  - [x] Deployability/actionability: `core/risk_governor.py`.
  - [x] Qualitative thesis: CIO/debate layer.
  - [x] Ranking: orchestrator ranking service or extracted ranking module.
  - [x] Position sizing: position sizer.
  - [x] Display semantics: normalized display packet.
- [x] Add a short note that forecasting EV and fair value are advisory by default.
- [x] Add a development rule: do not add new business logic to
      `core/orchestrator/legacy.py`.
- [x] Add a development rule: report formatters should not invent new policy
      states from raw internals.

### Acceptance Criteria

- [x] A contributor can identify exactly one owner for each rejection or ranking
      decision.
- [x] The owner map references the audit document and this checklist.
- [x] No runtime code is changed in this phase unless needed for docs links.

### Verification

- [x] `git diff --stat`
- [x] Manual read of `docs/architecture_decision_map.md`

---

## Phase 1 - Disable Hidden Advisory Influence

Purpose: make advisory layers visible but non-authoritative by default.

### Tasks

- [x] Add an explicit setting for forecast ranking influence, default disabled.
      Suggested name: `FORECAST_EV_RANKING_ENABLED=false`.
- [x] Keep `_inject_forecast_reports(...)` or its replacement writing
      `forecast_report` for display.
- [x] When the setting is disabled, do not write `forecast_ev_pct` into ranking
      inputs.
- [x] Keep forecast quality flags visible in reports.
- [x] Add report text that labels forecast output as advisory when ranking is
      disabled.
- [x] Add tests proving:
  - [x] production forecast EV does not affect rank when disabled.
  - [x] research-only forecast EV does not affect rank when disabled.
  - [x] forecast report still appears in result/report payloads.
  - [x] enabling the setting preserves the old ranking behavior.

### Acceptance Criteria

- [x] Forecasting remains useful for analysis.
- [x] Ranking no longer changes from forecast EV unless explicitly enabled.
- [x] No hidden `research_only` forecast can alter production ranking by default.

### Likely Files

- [x] `core/settings.py`
- [x] `core/orchestrator/legacy.py`
- [x] `services/report_formatter.py`
- [x] `.env.example`
- [x] `tests/test_forecasting_service.py`
- [x] `tests/test_orchestrator_realized_scoring.py`
- [x] `tests/test_report_formatter.py`

### Verification

- [x] `uv run pytest tests/test_forecasting_service.py tests/test_orchestrator_realized_scoring.py tests/test_report_formatter.py -q`
- [x] `uv run python -m py_compile core/orchestrator/legacy.py core/settings.py services/report_formatter.py`
- [x] `uv run ruff check core/orchestrator/legacy.py core/settings.py services/report_formatter.py tests/test_forecasting_service.py tests/test_orchestrator_realized_scoring.py tests/test_report_formatter.py`

---

## Phase 2 - Move Backtest Auto-Evaluation Out of Pipeline Startup

Purpose: remove side-effectful historical bookkeeping from normal live analysis.

### Tasks

- [x] Add an opt-in setting for pipeline startup memory evaluation.
      Suggested name: `PIPELINE_AUTO_EVALUATE_MEMORY=false`.
- [x] Keep current `evaluate_memory(write=True)` behavior only when the setting
      is enabled.
- [x] Add an explicit CLI command for this operation.
      Suggested command: `idx backtest evaluate-open`.
- [x] Make the command call `evaluate_memory(write=True)` and display the summary.
- [x] Ensure default `idx pipeline` no longer mutates backtest memory at startup.
- [x] Add tests proving:
  - [x] default pipeline path does not call `evaluate_memory(write=True)`.
  - [x] opt-in setting calls it.
  - [x] explicit backtest command calls it.

### Acceptance Criteria

- [x] Normal pipeline start has fewer hidden writes.
- [x] Existing maintenance behavior remains available.
- [x] No backtest memory semantics are changed.

### Likely Files

- [x] `core/settings.py`
- [x] `core/orchestrator/legacy.py`
- [x] `app/cli/commands/backtest.py`
- [x] `.env.example`
- [x] `tests/test_cli_v1.py`
- [x] `tests/test_backtest_outcome_evaluator.py`

### Verification

- [x] `uv run pytest tests/test_cli_v1.py tests/test_backtest_outcome_evaluator.py -q`
- [x] `uv run python -m py_compile core/orchestrator/legacy.py core/settings.py app/cli/commands/backtest.py`
- [x] `uv run ruff check core/orchestrator/legacy.py core/settings.py app/cli/commands/backtest.py tests/test_cli_v1.py tests/test_backtest_outcome_evaluator.py`

---

## Phase 3 - Extract Trade Envelope Service

Purpose: make trade geometry a standalone deterministic production primitive.

### Tasks

- [x] Create a dedicated trade-envelope module.
      Suggested path: `core/trade_envelope.py` or `services/trade_envelope.py`.
- [x] Move current envelope logic out of `DebateChamber._compute_trade_envelope`.
- [x] Preserve current behavior exactly:
  - [x] MA50 pullback entry.
  - [x] regime-scaled ATR stop.
  - [x] noise gate.
  - [x] resistance-first target selection.
  - [x] sector swing cap.
  - [x] `target_collapsed`, `stop_inside_noise`, `rr_too_low` rejection shape.
  - [x] hypothetical envelope for rejected setups.
- [x] Keep `DebateChamber._compute_trade_envelope` as a thin compatibility wrapper
      for one transition.
- [x] Add focused tests for the new service.
- [x] Update existing debate tests to assert behavior through the new service
      where practical.

### Acceptance Criteria

- [x] Debate chamber no longer owns trade geometry implementation.
- [x] Existing CIO prompts still receive the same envelope fields.
- [x] Existing report/risk behavior remains unchanged.
- [x] No production output changes except import/module ownership.

### Likely Files

- [x] `services/debate_chamber.py`
- [x] `core/trade_envelope.py`
- [x] `tests/test_debate_chamber_reliability.py`
- [x] `tests/test_trade_envelope.py`

### Verification

- [x] `uv run pytest tests/test_debate_chamber_reliability.py tests/test_trade_envelope.py -q`
- [x] `uv run python -m py_compile services/debate_chamber.py core/trade_envelope.py`
- [x] `uv run ruff check services/debate_chamber.py core/trade_envelope.py tests/test_trade_envelope.py`

---

## Phase 4 - Normalize Display Semantics Before Formatting

Purpose: stop report formatters from becoming policy engines.

### Tasks

- [x] Create a display packet builder.
      Suggested module: `services/display_packet.py`.
- [x] Define a small normalized packet containing:
  - [x] actionability label.
  - [x] risk governor display line.
  - [x] valuation display state.
  - [x] forecast display state.
  - [x] breaking-news warning state.
  - [x] system notes/warnings.
- [x] Make Rich and Markdown formatters consume the display packet.
- [x] Preserve current rendered output initially.
- [x] Add tests proving Markdown and Rich still show:
  - [x] fair value suppression for unverified valuation.
  - [x] forecast quality flags.
  - [x] Risk Governor status.
  - [x] breaking news.
  - [x] execution horizon.

### Acceptance Criteria

- [x] Formatter code formats normalized state instead of deriving policy from
      raw result internals.
- [x] API/UI can reuse the same packet later.
- [x] No report content regression.

### Likely Files

- [x] `services/report_formatter.py`
- [x] new `services/display_packet.py`
- [x] `tests/test_report_formatter.py`

### Verification

- [x] `uv run pytest tests/test_report_formatter.py -q`
- [x] `uv run python -m py_compile services/report_formatter.py services/display_packet.py`
- [x] `uv run ruff check services/report_formatter.py services/display_packet.py tests/test_report_formatter.py`

---

## Phase 5 - Split Runtime Prompt Requirements From Archived Prompts

Purpose: remove required-but-unused prompt contracts.

### Tasks

- [x] Split prompt registry into:
  - [x] runtime required prompts.
  - [x] archived/research prompts.
- [x] Stop requiring `CONSENSUS_PROMPT` if deterministic consensus is the live
      implementation.
- [x] Stop requiring `STATE_CLEANER_PROMPT` if deterministic state cleaning is
      the live implementation.
- [x] Keep archived prompt files available for reference.
- [x] Update prompt manifest and prompt-pack linter.
- [x] Update prompt docs to state which prompts are live runtime prompts.
- [x] Add tests proving startup does not fail when archived prompts are absent.

### Acceptance Criteria

- [x] Missing archived prompt files cannot block production startup.
- [x] Runtime prompt list matches actual LLM calls.
- [x] Future prompt edits have predictable impact.

### Likely Files

- [x] `services/debate_prompt_registry.py`
- [x] `services/debate_prompts/manifest.json`
- [x] `services/debate_prompts/README.md`
- [x] `tests/test_prompt_pack_linter.py`
- [x] `tests/test_debate_chamber_reliability.py`

### Verification

- [x] `uv run pytest tests/test_prompt_pack_linter.py tests/test_debate_chamber_reliability.py -q`
- [x] `uv run python -m py_compile services/debate_prompt_registry.py services/debate_chamber.py`
- [x] `uv run ruff check services/debate_prompt_registry.py core/prompt_pack_linter.py services/debate_chamber.py tests/test_prompt_pack_linter.py tests/test_debate_chamber_reliability.py`

---

## Phase 6 - Move Research Modes Out of Production Pipeline

Purpose: keep research useful but explicit.

### Tasks

- [x] Decide final CLI namespace:
  - [x] recommended: keep current commands but add `idx research compare`.
  - [x] keep backward-compatible `idx pipeline compare` temporarily.
- [x] Move `SingleAgentAnalyzer` execution out of default production runner path.
- [x] Keep comparison artifacts only when comparison/research mode is explicit.
- [x] Add deprecation warning for `idx pipeline compare` if replaced.
- [x] Update docs and tests.

### Acceptance Criteria

- [x] `idx pipeline` is production-oriented by default.
- [x] Comparison remains available.
- [x] Research artifacts are not produced unless requested.

### Likely Files

- [x] `app/cli/main.py`
- [x] `app/cli/commands/pipeline.py`
- [x] new or existing research/eval command module
- [x] `core/orchestrator/legacy.py`
- [x] `tests/test_cli_v1.py`
- [x] `tests/test_comparison_reporter.py`

### Verification

- [x] `uv run pytest tests/test_cli_v1.py tests/test_comparison_reporter.py -q`
- [x] `uv run python -m py_compile app/cli/main.py app/cli/commands/pipeline.py app/cli/commands/research.py app/cli/mode_utils.py core/orchestrator/legacy.py orchestrator.py`

---

## Phase 7 - Strangle `core/orchestrator/legacy.py`

Purpose: replace the god module with small production services over time.

### Tasks

- [x] Introduce `PipelineRunner` as the new narrow production interface.
- [ ] Extract candidate intake functions into a candidate service.
- [ ] Extract forecast enrichment into advisory service or disable-by-default
      wrapper.
- [ ] Extract risk annotation and sizing handoff into dedicated modules.
- [ ] Extract persistence/report writing into dedicated service.
- [x] Stop re-exporting every private symbol from `core/orchestrator/pipeline.py`.
- [ ] Migrate tests away from private `legacy.py` helpers where practical.
- [x] Keep `legacy.py` as a compatibility wrapper until all public callers move.

### Acceptance Criteria

- [x] New code imports public runner/service APIs, not private legacy helpers.
- [ ] `legacy.py` shrinks by moving behavior, not by deleting coverage.
- [x] Existing CLI commands still work.
- [x] Artifact paths remain backward-compatible.

### Likely Files

- [ ] `core/orchestrator/legacy.py`
- [x] `core/orchestrator/pipeline.py`
- [x] new orchestrator service modules
- [ ] `tests/test_orchestrator_quality_gates.py`
- [ ] `tests/test_orchestrator_risk_governor.py`
- [x] `tests/test_cli_renderer_presentation.py`

### Verification

- [x] `uv run pytest tests/test_orchestrator_quality_gates.py tests/test_orchestrator_risk_governor.py tests/test_cli_renderer_presentation.py -q`
- [x] `uv run python -m py_compile core/orchestrator/legacy.py core/orchestrator/pipeline.py core/orchestrator/runner.py`

---

## Phase 8 - Fair Value Authority Cleanup

Purpose: keep valuation useful without letting it overrule swing execution.

### Tasks

- [x] Review every place that treats overvaluation as hard rejection.
- [x] Make fair value display and quality gate remain strict.
- [x] Keep unverified fair value suppressed in reports.
- [x] Treat `historically_expensive` as soft context unless a separate production
      gate explicitly promotes it.
- [x] Define when `risk_overvalued=True` is allowed to hard reject:
  - [x] confirmed quality valuation,
  - [x] non-momentum setup,
  - [x] no technical breakout/volume confirmation,
  - [x] or explicit CIO risk override.
- [x] Add tests for momentum setup above fair value that should not be rejected
      purely because of valuation.

### Acceptance Criteria

- [x] Fair value no longer feels like long-horizon value investing controlling a
      5-20 day swing system.
- [x] Broken/unverified valuation still cannot be presented as truth.
- [x] Risk Governor remains deterministic.

### Likely Files

- [x] `services/fair_value_calculator.py`
- [x] `services/debate_chamber.py`
- [x] `core/risk_governor.py`
- [x] `services/report_formatter.py`
- [x] `tests/test_fair_value_calculator.py`
- [x] `tests/test_risk_governor.py`
- [x] `tests/test_debate_chamber_reliability.py`

### Verification

- [x] `uv run pytest tests/test_fair_value_calculator.py tests/test_risk_governor.py tests/test_debate_chamber_reliability.py -q`
- [x] `uv run python -m py_compile services/fair_value_calculator.py core/risk_governor.py services/debate_chamber.py`

---

## Phase 9 - Final Cleanup and Documentation Alignment

Purpose: remove stale conceptual contradictions after behavior is stabilized.

### Tasks

- [x] Update README pipeline description to match new owner map.
- [x] Update audit docs with implemented status.
- [x] Move stale strategic/research docs into an archive section or mark as
      historical.
- [x] Add a short "production vs advisory vs research" table to docs.
- [x] Verify sample outputs still match current report semantics.
- [x] Run a focused end-to-end dry run if dependencies permit.

### Acceptance Criteria

- [x] Docs no longer describe old prompt or forecast behavior as current.
- [x] A new contributor can tell what is production, advisory, research, or
      archived.
- [x] Sample artifacts do not imply hidden decision authority.

### Verification

- [x] `git diff --stat`
- [x] `uv run pytest tests/test_report_formatter.py tests/test_cli_v1.py -q`
- [x] Optional: `uv run idx pipeline --dry-run --tickers BBCA --skip-scraping --no-interactive`

---

## Master Checklist

- [x] P0 owner map complete.
- [x] P1 forecast ranking influence disabled by default.
- [x] P2 backtest auto-evaluation removed from default pipeline startup.
- [x] P3 trade envelope extracted and behavior preserved.
- [x] P4 display packet created and reports consume normalized semantics.
- [x] P5 runtime prompt requirements cleaned up.
- [x] P6 research/comparison modes explicit.
- [x] P7 legacy orchestrator strangler started.
- [x] P8 fair value authority cleaned up.
- [x] P9 docs and samples aligned.

---

## Phase Order Recommendation

Execute in this order:

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 4
6. Phase 5
7. Phase 6
8. Phase 8
9. Phase 7
10. Phase 9

Reason: first remove hidden decision influence and side effects, then extract
stable primitives, then clean research/legacy boundaries. The largest refactor,
strangling `legacy.py`, should happen after ownership and behavior are already
clear.
