# Phase 0 Baseline — Executable Swing System

Captured: 2026-07-12

## Protected worktree changes

The following user-owned modifications existed before Phase 0 and must remain
untouched:

- `README.md`
- `REMEDIATION_BACKLOG.md`
- `services/debate_prompts/README.md`

Phase 0 changes are limited to regression tests and this ignored diagnostic
baseline. No production source behavior is changed.

## Verified runtime baseline

- Full pytest: 1,152 passed in 36.54s.
- Calculation-focused pytest: 189 passed in 5.90s.
- Agent/orchestrator reliability pytest: 227 passed in 5.07s.
- Active-module Ruff scan: passed.
- Live filter: 961 -> 808 -> 461 -> 4 -> 2.
- Fresh six-stock live run: exit 0; 6 succeeded / 0 failed; 56 Flash + 1 Pro;
  288.5 seconds; 0 executable positions.

## Frozen artifact hashes (SHA-256)

- `filter_momentum/top10_candidates.json`:
  `A877B606875E38FDF62247DB1695A0EFC6DAD47FA57102565550076C3E696B6C`
- `pipeline_dry_run/full_batch_results.json`:
  `1C2C09A6F8FF305B48EEEF501CDEE89C0D729D6B3CDC3D14820D777527BA7EC7`
- `pipeline_live_6_retry/full_batch_results.json`:
  `3D94DDFDCA3FDA4EAC411A4AF758659ACF5F9A90D512733F83EC8358446CC488`
- `pipeline_live_6_retry/latest_batch_report.md`:
  `8BBD0C31DCF3B19E122C3D605BE0F76A649920821F03EA161350E066897D6B61`
- `pipeline_live_6_retry/TOP_3_SWING_TRADES.md`:
  `E2FE8993AEE1B5CA521258A4BCD34D7AE0105C0C10538D069479FF35D8056B1B`

## Strict expected-failure contracts added in Phase 0

- `P0-DPS-PRICE`
- `P0-FV-ACTIVE-METHODS`
- `P0-ARA-2025`
- `P1-TICKER-CONTAINMENT` — CLI, direct parser, and API schema
- `P1-REPORT-RATING-COVERAGE`

These tests use `xfail(strict=True)`. Normal Phase 0 verification must report
seven XFAIL results and zero XPASS results. `pytest --runxfail` must expose the
underlying failures until the owning implementation phase fixes each defect.

## Contracts intentionally deferred until their design phase

- Canonical execution-regime contract.
- Unified R/R authority and pre-debate trade-envelope eligibility.
- Shared OHLC snapshot identity.
- No-technical-data zero-LLM short circuit.
- Decision/model/policy confidence schema.
- OAuth refresh lifecycle.
- Cache TTL and in-flight deduplication.
- Structured replacement for silent failures.
- Forecast validation-to-weight aggregation.

These require a deliberate target contract before a strict regression test can
be written without locking in the wrong architecture.

---

## Re-verification addendum — 2026-07-15

This directory (`tests/baselines/full_system_exam_20260712/`) is a tracked copy
of the original baseline captured under `tmp/full_system_exam_20260712/`. The
original lives in a gitignored path (`.gitignore` line `tmp/`) and is therefore
not protected against cleanup; this copy is the durable regression baseline.

Verified on 2026-07-15 (branch `experiment/momentum-only`, HEAD `ff0b2dc`):

- All five SHA-256 hashes above re-computed on both the originals and this
  copy — all match the recorded values. Baseline integrity intact.
- The three protected user files still carry their uncommitted changes
  (README.md 8 lines, REMEDIATION_BACKLOG.md +20, debate_prompts README 58).
- Of the strict-xfail Phase 0 contracts, only `P1-REPORT-RATING-COVERAGE`
  (tests/test_report_formatter.py) still XFAILs — its owning phase (5.1 batch
  rating coverage) is not yet implemented. The other contracts (`P0-DPS-PRICE`,
  `P0-FV-ACTIVE-METHODS`, `P0-ARA-2025`, `P1-TICKER-CONTAINMENT`) have been
  flipped to normal passing tests by their landed fixes. Full suite as of
  2026-07-14: 1431 passed, 1 failed (stale message assertion in
  test_orchestrator_quality_gates.py path-containment test — behavior correct,
  assertion outdated), 3 skipped, 1 xfailed.
- `pipeline_live_6_retry/debates/` and `telemetry/` are included in this copy
  as per-ticker records for offline replay (decision-semantics and report
  consistency work, Phases 3.4/5.x).

Live re-runs (Phase 9.3/9.4) remain blocked on Codex OAuth login and are the
final acceptance step only; all intermediate phases verify offline against
this baseline, unit tests, and `--dry-run` pipelines.
