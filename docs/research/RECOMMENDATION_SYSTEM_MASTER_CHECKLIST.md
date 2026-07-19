# Master Implementation Checklist — Calibrated Recommendation System

**Document status:** active execution plan; update after every approved work phase  
**Baseline date:** 2026-07-17 (Asia/Jakarta)  
**Target system:** calibrated, selective recommendation system — not a higher-frequency BUY generator  
**Evidence authority:** [Research Ledger](RESEARCH_LEDGER_2026-07.md), [Redesign Proposal](DEFENSIVE_TO_RECOMMENDATION_REDESIGN_2026-07.md), and [Shadow Mode Protocol](SHADOW_MODE_PROTOCOL.md)

## 1. How to use this checklist

This is the operational handoff document for future sessions. A request such as
“continue Phase 3” means: execute only the unchecked IDs in that phase, respect
its dependencies and hard stop, verify its Definition of Done, then update this
file with evidence.

### Status convention

- `[x]` — completed and supported by code/test/artifact evidence.
- `[ ]` — pending.
- `BLOCKED` — cannot start until the listed dependency or evidence exists.
- `SHADOW-ONLY` — may be implemented and measured, but cannot affect live
  rating, rank, sizing, Top-3 eligibility, or execution.
- `APPROVAL` — explicit approval is required before crossing that boundary.

### Completion discipline

- [ ] Never mark an item complete from code inspection alone when it requires
  an empirical outcome.
- [ ] Never use elapsed calendar time as a substitute for mature independent
  samples.
- [ ] For every completed ID, add the commit/content hash, tests, and artifact
  path to the execution log at the end of this file.
- [ ] If implementation changes any frozen feature, threshold, label, source,
  cost, or GO rule after shadow collection begins, close that protocol as-is and
  create a new protocol ID.
- [ ] Treat `CONTINUE` as “do not promote.” It is not a weak GO.
- [ ] Preserve negative and null results. Never delete or overwrite a failed
  challenger.

## 2. Target architecture and invariants

The finished system has four separate authority layers:

1. **Deterministic control:** data integrity, geometry, execution, liquidity,
   corporate-action, regime, circuit-breaker, and portfolio gates.
2. **Recommendation-information layer:** exact state, blocker, distance,
   provenance, next trigger, and non-executable hypothetical geometry.
3. **Calibrated shadow layer:** target/stop/timeout probabilities, expected net
   R, uncertainty, calibration diagnostics, and counterfactual ranks.
4. **Approved production overlay:** at most one challenger promoted after its
   own mature GO, canary, and explicit approval. It still cannot override a hard
   gate.

### Non-negotiable invariants

- [ ] No BUY-count, pass-rate, or Top-3 quota may be an optimization target.
- [ ] Do not lower the canonical R/R floor to make more candidates pass.
- [ ] Do not loosen momentum, SIDEWAYS, debate-eligibility, liquidity, or
  portfolio-risk thresholds without a separately pre-registered challenger.
- [ ] A failed snapshot/provenance, impossible geometry, suspension/FCA,
  critical ex-date, circuit breaker, or mandatory-data gate can never be
  overridden by probability, agent confidence, factor score, or LLM prose.
- [ ] Every shadow artifact must enforce `evaluation_only=true`,
  `live_authority=false`, and `affects_execution=false` at schema level.
- [ ] Control and challenger must see the same point-in-time opportunity set,
  snapshots, timestamps, costs, and labels.
- [ ] The primary outcome horizon is exactly 15 trading days. The 3/5/10-day
  horizons are secondary and cannot replace the primary test.
- [ ] Promotion is one component at a time. After one promotion, freeze a new
  control before judging the next component.

## 3. Current-state truth table

| Area | Status on 2026-07-19 | What that means |
|---|---|---|
| Recommendation information contract | **DONE** | `recommendation-context-v1`, six states, exact blockers/gaps, non-executable hypothetical setup, API/CLI/Markdown/Rich parity, and validation exist. |
| Live thresholds/actionability | **UNCHANGED** | The information refactor did not loosen R/R, momentum, SIDEWAYS, debate eligibility, liquidity, regime, or sizing. |
| Generic paired shadow protocol | **RS-P2-017 DAILY-NAV SUBSTRATE COMPLETE; RS-P2-025 DONE; RS-P2-019/021/022 PARTIAL** | The isolated package now includes the frozen control state/paired decision view, exact fixed-notional signal-isolation lifecycles, independently evolving control/challenger policy portfolios from one identical Rp100m genesis, and two non-interchangeable daily marked-to-market series: policy-portfolio NAV and per-opportunity fixed-notional sleeve equity. Integer-IDR accounting, point-in-time official marks, permanent explicit censoring, simple daily returns, deterministic local replay, and content-addressed lineage are implemented. Common metrics, generic reporting, an externally authenticated NAV-tail commitment, completion of every family-level storage/tamper/replay gap, real component approval, and collection remain unimplemented; residual family coverage is tracked in the [status-reconciliation ledger](SHADOW_STATUS_RECONCILIATION_2026-07-18.md). |
| Existing forecasting shadow | **PARTIAL FOUNDATION** | It is non-authoritative, but defaults to 5/10/20-day horizons and lacks signed protocol IDs, paired control/challenger decisions, trial registry, and the 15-day primary estimand. |
| C1 calibrated recommender | **PARTIAL FOUNDATION ONLY** | Some forecast probabilities exist; there is no promotion-grade competing-risk calibration or risk–coverage layer. |
| C2 discount-rate decomposition | **PROPOSAL ONLY** | Live logic still uses SBN plus beta times total ERP; source decomposition/expiry/abstention is absent. |
| C3 finance sentiment | **BASELINE ONLY** | General-domain IndoBERT prior exists; finance benchmark, calibration, abstention, and outcome validation do not. |
| C4a regime challenger | **CONTROL ONLY** | Three-state HMM exists; persistence/jump challenger and long OOS transition validation do not. |
| C4b1 foreign flow | **INTERFACE ONLY** | HMM accepts the feature, but runtime does not supply a point-in-time foreign-flow series. |
| C4b2 MSCI state | **PROPOSAL ONLY** | Current state is a hard-coded Boolean, not an official dated/expiring record. |
| C5 IDX4 | **NAMING FIX + CHARACTERISTICS ONLY** | Stock-level characteristics exist; paper-faithful factor portfolios and point-in-time coverage do not. |
| C6 DSR/trial governance | **NOT RECONCILED** | Duplicate implementations and incorrect `n_trials` usage remain; DSR is not promotion-grade. |
| C7 missing liquidity | **PARTIAL SWITCH, WRONG FINAL SEMANTIC** | A default-false fail-closed switch can reject missing ADT; the target is a paired `ABSTAIN / DATA_INSUFFICIENT` challenger. |
| C8 `momentum_play` exemption | **REACHABLE, NOT DISABLED** | CIO prompt can set it, schema accepts it, and risk governor honors it. Sampled outputs being false does not prove dormancy. |
| Old research archive | **DEFERRED** | No files have been moved; evidence metadata must be captured first. |

### Critical research reconciliation before implementation

- [ ] **RS-P0-C8-RECON:** Correct the “no production path sets
  `momentum_play`” assumption. Evidence currently shows:
  `services/debate_prompts/cio_judge.txt` asks the CIO to emit it,
  `schemas/debate.py` accepts/caps it, and `core/risk_governor.py` grants the
  overvaluation exemption.
- [ ] Measure prevalence in immutable historical artifacts instead of calling
  the branch dormant from a small sampled corpus.
- [ ] Decide whether the C8 control is “current reachable exemption” or a
  separately frozen configuration. Record that choice in the C8 manifest
  before any replay or prospective observation.

## 4. Roadmap and dependency order

| Phase | Outcome | May run in parallel? | Promotion dependency |
|---:|---|---|---|
| 0 | Freeze baseline, authority, and protocol boundaries | No; first | Required by all phases |
| 1 | Recommendation-information layer | Already complete | Keep verified |
| 2 | Generic immutable paired shadow/evidence plane | No; critical substrate | Required by C1–C8 collection |
| 3 | C6 canonical DSR and trial governance | Yes, after Phase 2 | Must pass before C1–C5 can GO |
| 4 | C7/C8 safety challengers | Yes, after Phase 2 | Safety GO does not require positive Sharpe |
| 5 | Point-in-time data/provenance foundations | Yes by component | Required before related model challenger |
| 6 | C1 calibrated selective recommender | After Phase 2 and data readiness | C6 required before GO |
| 7 | C2/C3/C4/C5 challengers | Separate parallel protocols | C6 required before GO |
| 8 | Prospective shadow collection and paper portfolios | Parallel collection allowed | Each component keeps its own maturity |
| 9 | Fixed-terminal evaluation and decision | Per component | Independent reproduction |
| 10 | One-component canary and promotion | Strictly sequential | Explicit approval after GO |
| 11 | Production monitoring and research hygiene | Continuous | Automatic rollback rules |

Recommended critical path:

`Phase 0 → Phase 2 → Phase 3 → Phase 6 → Phase 8 → Phase 9 → Phase 10`

Safety track:

`Phase 0 → Phase 2 → Phase 4 → Phase 8 → Phase 9 → Phase 10`

Long-horizon data/model tracks in Phase 5 and Phase 7 should start collecting
early, but they must not block delivery of C1 unless their data is a declared C1
feature.

---

## Phase 0 — Freeze baseline and remove ambiguity

**Objective:** create a reproducible defensive control and remove factual
disagreements before any challenger code or outcome collection.

**Live authority:** unchanged.

**Phase-0 baseline manifest:** [BASELINE_CONTROL_MANIFEST_2026-07-17.json](BASELINE_CONTROL_MANIFEST_2026-07-17.json)

The manifest is a read-only capture of the dirty worktree and current
defensive control. It records hashes, effective non-secret configuration,
fixtures, C7/C8 probes, and test conditions. Its presence does not authorize
shadow collection, threshold changes, or live execution.

### Baseline manifest

- [x] **RS-P0-001:** Record the full dirty-worktree inventory with content
  hashes; Git HEAD alone is insufficient.
- [x] **RS-P0-002:** Record Python/UV lock state, dependency versions, operating
  timezone, trading calendar version, environment-variable names and effective
  non-secret values.
- [x] **RS-P0-003:** Hash all control files owning candidate intake, quant
  filtering, trade setup, debate/CIO, risk governor, ranking, sizing, reporting,
  snapshot creation, and outcome evaluation.
- [x] **RS-P0-004:** Freeze current threshold/config values, including R/R,
  EMA20, relative strength, ADT, ATR, RSI, volume, SIDEWAYS controls,
  confidence, ex-date, ARA/ARB, circuit breaker, portfolio heat, and kill switch.
- [ ] **RS-P0-005 (PARTIAL):** Capture a representative frozen baseline run containing
  executable, wait, single-gate reject, hard reject, and insufficient-data
  examples where available. The frozen Phase-5c fixture supplies reject and
  insufficient-data examples, but no provenance-valid executable/WAIT exemplar
  was found; do not fabricate one.
- [ ] **RS-P0-006 (HASHED-ONLY):** Store baseline artifacts under a versioned immutable path;
  never use mutable `latest_*` files as the sole evidence anchor.
- [x] **RS-P0-007:** Rerun focused tests, touched-file Ruff/compile checks, and
  the full suite; record exact counts and command output. Full-suite
  reproducibility requires the workspace `TEMP/TMP/SSL_CERT_FILE` override;
  without it seven model-integration tests stop at certificate permission
  setup before assertions.

### Authority and protocol decisions

- [ ] **RS-P0-008 (DRAFTED; APPROVAL PENDING):** Assign one protocol ID per component: C1, C2, C3, C4a,
  C4b1, C4b2, C5, C6, C7, and C8. Never combine C4a/b1/b2.
- [ ] **RS-P0-009 (APPROVAL PENDING):** Assign owner, governance mode, rollback
  owner, and approval reference for every protocol. For `INDEPENDENT_REVIEW`,
  assign an independent reviewer distinct from the owner. For
  `SOLO_SELF_REVIEW`, require `independent_reviewer=null` and predeclare the
  same owner as approver and rollback owner; cooling-off requires 72 hours AND
  2 completed IDX sessions, whichever completes later, with no waiver, and the
  frozen self-adversarial review remains mandatory.
- [ ] **RS-P0-010 (DRAFTED; APPROVAL PENDING):** Copy component-specific GO/CONTINUE/NO-GO rules verbatim
  into each manifest.
- [ ] **RS-P0-011 (DRAFTED; APPROVAL PENDING):** Freeze the 15-day primary horizon, 3/5/10-day secondary
  horizons, fee/tax/slippage, fill rules, corporate-action handling, and
  ambiguity convention.
- [ ] **RS-P0-012 (DRAFTED; APPROVAL PENDING):** Freeze the independent-cluster definition: overlapping
  ticker windows, issuer/economic groups, correlated clusters, duplicated
  setups, and systemic date blocks.
- [ ] **RS-P0-013 (APPROVAL PENDING):** Establish approval gates for: start collection, unblind,
  canary, and production promotion.

### Required truth corrections

- [ ] **RS-P0-014 (PARTIAL FINDING):** Complete `RS-P0-C8-RECON` and patch the research wording so
  C8 is described as reachable unless evidence proves otherwise. Active
  redesign/protocol wording is corrected; an older research artifact still
  contains the superseded dormancy claim and needs a pointer/archive decision.

  **Known reconciliation boundary:** Active master/redesign/protocol authority
  describes C8 as reachable. Historical dormancy wording, prevalence
  measurement, and the exact frozen C8 control choice remain unresolved;
  operational truth for new work is corrected, but Phase-0 research
  reconciliation is not complete. See the
  [status-reconciliation ledger](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#5-known-phase-0--c8-inconsistency).
- [x] **RS-P0-015:** Confirm C7 missingness semantics for `None`, zero,
  negative, non-numeric, `NaN`, infinity, stale data, and source failure. Current
  logic needs an explicit finite-number check. The matrix is captured in the
  manifest; no source fix was made in this read-only pass.
- [ ] **RS-P0-016 (OPEN RECONCILIATION):** Correct stale IndoBERT code/documentation claims that imply
  the current general-domain checkpoint already has a finance benchmark. The
  ledger is corrected, but the older gap-analysis wording remains for a later
  documentation/archive decision.
- [x] **RS-P0-017:** Record that existing forecasting shadow artifacts are not
  equivalent to the Phase-2 paired protocol.

### Phase 0 Definition of Done

- [ ] Full content manifest can reproduce the control (hashes are captured;
  ignored runtime fixtures still need an approved tracked/pinned copy).
- [ ] Baseline test/artifact evidence is immutable and linked below (current
  output fixtures are versioned and hashed but git-ignored).
- [x] C7 missingness taxonomy and C8 reachability are stated correctly in the
  active authority.
- [ ] C8 prevalence, exact frozen control definition, and stale
  historical-artifact pointer/archive reconciliation are complete.
- [ ] Every component has a unique protocol ID, mode-valid governance identity
  tuple, rollback owner, and approval binding (IDs are drafted; real component
  manifests/A1 remain pending).
- [x] No live threshold or actionability changed.

**HARD STOP:** obtain approval of the manifests before collecting the first
shadow observation.

---

## Phase 1 — Recommendation-information layer

**Objective:** make every defensive decision useful to a human without changing
whether it passes.

**Status:** implemented in the experimental duplicate; preserve and re-verify.

### Completed checkpoint

- [x] **RS-P1-001:** Versioned `recommendation-context-v1` contract.
- [x] **RS-P1-002:** States `QUALIFIED`, `WAIT_TRIGGER`, `NEAR_MISS`,
  `SINGLE_GATE_REJECT`, `HARD_REJECT`, and `DATA_INSUFFICIENT`.
- [x] **RS-P1-003:** Exact observed value, threshold, absolute gap, normalized
  gap, provenance, and next trigger for measurable blockers.
- [x] **RS-P1-004:** Presentation-only 10% near-miss band; it cannot grant
  actionability or sizing.
- [x] **RS-P1-005:** Explicitly non-executable hypothetical entry/target/stop/RR.
- [x] **RS-P1-006:** Full gate-failure instrumentation instead of retaining only
  the first failure.
- [x] **RS-P1-007:** Persistence and parity across orchestrator, API, CLI,
  Markdown, Rich output, and artifact validation.
- [x] **RS-P1-008:** Zero-agent/preflight outputs no longer claim a five-agent
  debate or fabricated CIO opinion.
- [x] **RS-P1-009:** Current features are labeled “IDX4-inspired
  characteristics,” not a validated IDX4 factor model.
- [x] **RS-P1-010:** Canonical actionability, thresholds, sizing, and BUY count
  remained unchanged.

### Regression obligations for every later phase

These are recurring obligations, not finish-once tasks. Keep their global
checkboxes open and append one evidence entry for every relevant pass using the
[recurring-evidence log](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#3-rs-p1-r01--r05-recurring-evidence-log).

- [ ] **RS-P1-R01:** A shadow field can never change `execution_decision`,
  `risk_governor`, rank, sizing, or Top-3 eligibility.
- [ ] **RS-P1-R02:** `NEAR_MISS`, `WAIT_TRIGGER`, and hypothetical setups remain
  non-executable.
- [ ] **RS-P1-R03:** Top-level and metadata recommendation contexts stay equal.
- [ ] **RS-P1-R04:** API/CLI/Markdown/Rich outputs retain state and blocker
  parity.
- [ ] **RS-P1-R05:** `calibration_status` remains `NOT_AVAILABLE` or explicit
  shadow status until C1 passes and is approved.

### Phase 1 Definition of Done

The implementation checkpoint is met, but regression obligations are recurring
and are not permanently closed. Reconfirm them after every later phase. The
latest backfill reconfirms R01–R04 for RS-P2-015 and leaves R05 **PARTIAL**
because no direct pre-C1 calibration-status guard test exists; see the
[recurring-evidence log](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#3-rs-p1-r01--r05-recurring-evidence-log).

---

## Phase 2 — Generic immutable paired shadow/evidence plane

**Objective:** build the shared experiment substrate once, without silently
turning the forecasting-only shadow module into authority for unrelated
components.

**Dependencies:** Phase-0 baseline and governance design are approved for
build-only, evaluation-only substrate work. RS-P2-001–016 were implemented
under explicit scoped owner approvals while real component manifests and A1
remained closed. This sequencing exception does not complete the Phase-0
component-manifest tasks and does not authorize collection; an exact component
manifest plus A1 remains mandatory before the first observation. The
2026-07-18 reason and scope are recorded in the
[status-reconciliation ledger](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#4-phase-ordering-exception-record).

**Live authority:** `false` by construction.

**Likely owners:** new isolated shadow protocol module/schema, with adapters in
`core/orchestrator/legacy.py`, `core/artifact_validator.py`,
`utils/market_snapshot.py`, and carefully reused writer patterns from
`core/forecasting/shadow_evaluation.py`.

### Contracts

- [x] **RS-P2-001:** Define strict `ShadowProtocolManifest` schema with protocol
  ID, component ID, version, owner/reviewer, approval, content hashes, universe,
  thresholds, features, labels, source expiry, costs, cluster rules, GO rules,
  fixed terminal date, flag, and rollback.
- [x] **RS-P2-002:** Define strict paired `ShadowObservation` schema containing
  one raw event ID, identical snapshot/opportunity-set identity, control
  decision, challenger decision, exact values/thresholds, reason codes, rank,
  hypothetical size, and divergence classification.
- [x] **RS-P2-003:** Enforce literal `evaluation_only=true`,
  `live_authority=false`, and `affects_execution=false`; invalid values must fail
  schema validation.
- [x] **RS-P2-004:** Keep shadow records outside strict `RiskDecision`
  (`extra=forbid`) and outside fields consumed by canonical ranking/sizing.
- [x] **RS-P2-005:** Define `ShadowOutcome` for fill/unfilled, target-first,
  stop-first, timeout/exception, return, net R, costs, ambiguity, maturity, and
  source hash.
- [x] **RS-P2-006:** Define append-only trial registry for every attempted model,
  parameter, feature set, threshold, and discarded variant.
- [x] **RS-P2-007:** Define independent cluster ID and effective-sample metadata;
  raw rows must not masquerade as independent observations.

### RS-P2-001…007 contract-slice evidence — 2026-07-17

- New package: `core/shadow_protocol/`; it imports no orchestrator, risk
  governor, ranking, sizing, execution-ledger, or forecasting-shadow module.
- All top-level evidence artifacts are frozen and `extra=forbid`, with literal
  `evaluation_only=true`, `live_authority=false`, `affects_execution=false`,
  `affects_ranking=false`, and `affects_sizing=false`.
- The trial registry is a frozen append-return-new event view with contiguous
  sequence, full canonical SHA-256 chaining, immutable trial fingerprints,
  valid lifecycle transitions, explicit discarded/failed variants, and
  idempotent identical-event replay.
- Contract file SHA-256:
  `5dc7b13280aefa2eb14662a25b2f9bad0a15d68dd32a987cef4424b23066b65f`.
  Package export SHA-256:
  `5a669b42323f81a85814812efe3e07a2f2011986fd001d7920e70a11c2e60e0c`.
  Test SHA-256:
  `aad1b6402514f50e8f86851cde752738e3f1936845b397a67e3db7a1f9377f9c`.
- Verification: contract suite `20 passed`; boundary regressions `293 passed`;
  full suite `1670 passed, 3 skipped, 56 warnings`; touched files pass Ruff and
  `py_compile`.
- No component manifest was approved, no sample was collected, no outcome was
  matured, and no threshold or live-authority path changed. The Phase-0
  baseline manifest remains the historical pre-contract control snapshot and
  was intentionally not rewritten.

### Point-in-time and outcome engine

- [x] **RS-P2-008:** Persist the complete candidate set before pruning for both
  control and challenger.
- [x] **RS-P2-009:** Prove exact opportunity-set parity by event IDs and hashes.
- [x] **RS-P2-010:** Implement 15-trading-day primary maturation and separate
  3/5/10-day secondary maturation.
- [x] **RS-P2-011:** Apply the frozen activation/fill rules: never fill on the
  signal bar; handle open gaps, limit touches, stop gaps, target gaps, same-day
  target/stop ambiguity, splits/rights, dividends, and expired/unfilled orders.
- [x] **RS-P2-012:** Implement idempotent outcome backfill that never downgrades
  a mature record or fetches future information for an earlier evaluation date.
- [x] **RS-P2-013:** Hash every snapshot, source record, manifest, observation,
  and outcome; validate ticker/as-of consistency.

### RS-P2-008…013 engine/evidence evidence — 2026-07-17

- Raw capture is persisted with exclusive creation before paired dispositions;
  both views retain the complete source order, and quarantined rows must be
  pruned by both sides with their exact quarantine reason.
- Opportunity parity includes event IDs, full raw-record hashes, per-ticker
  snapshot/source/known-action lineage, and empty-capture reason. Persisted
  parity and lineage proofs are trusted only after reconstruction from the
  exact referenced artifacts.
- The evaluator excludes the signal bar, uses one shared order-validity clock,
  then gives each 3/5/10/15-day label its own post-fill session clock. Missing
  pre-exit evidence fails closed; bars after an observed terminal event are not
  required.
- The supported bar basis is raw-as-traded. Splits rescale geometry and
  quantity on an effective frozen trading session; dividends require
  pre-ex-date ownership. Rights are handled conservatively as
  `INVALID / RIGHTS_POLICY_UNSUPPORTED` until election, subscription,
  delivery/lapse, cost, and monetary-risk rules are separately frozen. This is
  an explicit exceptional outcome, not an assumed automatic exercise.
- A marketable entry that gaps through its planned stop is recorded as a
  same-open fill/stop with costs, not discarded. Intraday entry/target ordering
  uncertainty remains visible and never receives unproven target credit.
- At the 2026-07-17 engine checkpoint, one frozen lot was used; source prices
  were accepted without engine-side tick rounding and no liquidity model was
  claimed. Entry and exit fees/slippage were calculated from their respective
  cash notionals. RS-P2-014 now freezes the future fixed-notional,
  starting-capital, liquidity, and portfolio-risk policy/state assumptions,
  but the legacy one-lot evaluator remains unchanged until RS-P2-015.
- Every ledger insert/update requires deterministic replay from the exact
  `MaturationRequest`. Source-vintage chains preserve bar prefixes and immutable
  corporate-action records while allowing a later-published event with an
  earlier effective date.
- Build-only boundary preserved: no orchestrator adapter, collection job,
  portfolio/NAV path, live threshold, ranking, sizing, or execution authority
  was added. P0-011 remains drafted/approval-pending; synthetic tests are not a
  collection cohort.
- Verification evidence: focused shadow suite `56 passed`; cross-boundary
  recommendation/forecasting/API/report/orchestrator regressions
  `403 passed, 1 skipped`; full suite `1706 passed, 3 skipped`; touched Python
  files pass Ruff and `py_compile`.
- Frozen content SHA-256:
  - `contracts.py`:
    `7871e70bf26cab6c3a704c04963fe9e4892a7eca228af14b968b5fb1d1adb7d8`
  - `evidence.py`:
    `44ce5f34ecd6af3249b5b102df0bc81e5b49a26683e0479e3237ebebf7af18bb`
  - `outcome_engine.py`:
    `b3c25dabb06b9bb40104467a3390888672bc6beebc69585f2a729b3c9a123b23`
  - `core/shadow_protocol/__init__.py`:
    `e233c8b93973df6a7588553ccbc8ec42d53ce5d2f073d918ceb48488dcea59db`
  - `tests/test_shadow_protocol.py`:
    `be8b81af9f4190f7ea126bdbe7db7b67b2287cf4136a1c088bd98bf1be109a4f`
  - `tests/test_shadow_protocol_p2.py`:
    `2cdc3c1add430596adfe9f2e5882311017e46d2885355b390528d15358d9dadf`
  - `SHADOW_MODE_PROTOCOL.md`:
    `66743ed7fd2bd85161a259a3c8db517b2f44e95ced6e4cd8f8ec37df04b092ab`

### Paper portfolios and metrics substrate

- [x] **RS-P2-014:** Implement paired candidate-level decision view using frozen
  control portfolio state.
- [x] **RS-P2-015:** Implement identical fixed-notional view to isolate signal
  quality.
- [x] **RS-P2-016:** Implement independent policy-portfolio view from identical
  starting capital and risk/cost rules.
- [x] **RS-P2-017:** Generate daily marked-to-market NAV; never derive portfolio
  Sharpe/drawdown from a list of closed trades.
- [ ] **RS-P2-018:** Emit common metrics with explicit denominators and
  `NOT_ESTIMABLE`, never coerced zeroes. Per NV-N2, report suspension/
  missing-official-mark censor counts and deterministic censored-session
  durations by side and ticker, plus source/liquidity slices only where the
  evidence is adequate; never silently remove censored paths from a
  denominator.

### RS-P2-014 portfolio-state/paired-view evidence — 2026-07-18

- **Owner policy frozen exactly:** starting capital Rp100,000,000; fixed
  notional Rp13,000,000; minimum ADTV20 Rp10,000,000,000; participation
  `0.0013`; target deployment `0.65`; effective fixed-notional maximum
  deployment `0.39`; minimum cash `0.05`; gross exposure `0.95`; position
  limits `5` and `3/2/1/0`; loss budget `0.02`; heat `0.013`; daily realized
  loss stop `0.03`; sector/cluster count limit 2; lot 100; and T+2.
- **N1–N3 preserved:** participation is labeled
  `DERIVED_NOT_CALIBRATED`; 65% is a `SIZING_BASIS`, not promised
  utilization; and true NAV drawdown remains `NOT_ESTIMABLE` until RS-P2-017
  plus a new protocol. `MAX_30D_DRAWDOWN` is not reinterpreted.
- **Manifest-v2 sufficiency proven:** the same canonical policy CONFIG hash is
  mandatory on both control/challenger sides, all portfolio scalar decisions
  are exact `FrozenParameter`s, and costs/source/calendar/corporate-action/
  methodology hashes are revalidated. Every reserved portfolio parameter and
  the canonical CONFIG path fail closed against profile downgrade
  (`core/shadow_protocol/portfolio.py:1204-1537`).
- **No premature A1:** the frozen capability literal is
  `RS_P2_014_ONLY_NOT_A1_ELIGIBLE`; approval append and authorization reload
  reject the profile until later Phase-2 capability evidence is represented by
  a new policy/manifest revision (`core/shadow_protocol/portfolio.py:1540-1551`;
  `core/shadow_protocol/governance.py:1591-1607`).
- **Hybrid identity is exact:** persisted money, positions, commitments, NAV,
  applied costs, and cash are strict integer IDR; ratios are finite,
  12-decimal `ROUND_HALF_EVEN`; applicable bps are aggregated and then
  conservatively ceiled once (`core/shadow_protocol/portfolio.py:210-553,
  1109-1144`).
- **Frozen-state lineage:** source record, policy, baseline, manifest,
  opportunity, chronology, positions/commitments, and state arithmetic are
  verified before immutable persistence and again on load
  (`core/shadow_protocol/portfolio.py:555-1050,1554-1887,1890-2520`).
- **Paired view:** authorization is reloaded before the first evaluator; both
  evaluators receive one immutable input and exact pre-batch state; role,
  disposition, mutation, chronology, and observation identities are checked
  (`core/shadow_protocol/paired_view.py:37-502`;
  `core/shadow_protocol/governance.py:1095-1215`).
- **Regression boundary:** `contracts.py`, `calendar.py`, and
  `outcome_engine.py` are byte-identical to the prepass. The existing
  `shadow-evaluation-v1` evaluator was not changed or silently reinterpreted.
  Maturation/backfill portfolio-lineage wiring remains later Phase-2 work and
  is unreachable for an authorized portfolio profile while the capability
  gate is closed.
- **Tests:** tamper, downgrade, replay/idempotency, exact integer arithmetic,
  source/state chronology, baseline/opportunity binding, same-state parity,
  evaluator mutation, authorization ordering, lineage-v2 reconstruction,
  cross-process hash determinism, and authority literals are covered in
  `tests/test_shadow_protocol_p2_014.py:632-1761`.
- **Verification:** focused shadow suite `159 passed`; full suite `1809 passed,
  3 skipped`; touched-file `py_compile` passed; repository-wide
  `ruff check --fix` passed and changed no files; `uv lock --check` passed.
- **Frozen content SHA-256:**
  - `core/shadow_protocol/portfolio.py`:
    `502ac2e1ca34f31c855da7d16a3834de1a7aa7524cac8705c6b3ce0719828d3e`
  - `core/shadow_protocol/paired_view.py`:
    `873e94fa8cb6ebaf50c127a32da6c86326c1a68989a1a4ec6f2d53d7d9fef684`
  - `core/shadow_protocol/governance.py`:
    `ae026aa3c4a4c46e98c1b662912cdd5075b62c7ec07966ad420cf7d12c3eb38c`
  - `core/shadow_protocol/__init__.py`:
    `7c0198e940c876f0d96bfd9fcc91a7bf8cf1ba6f7760c1f92219695b179e6ef1`
  - `tests/test_shadow_protocol_p2_014.py`:
    `5a124a40430e6745622e287ce11a9e4d7ef0204a82c9f9baf470aa9db17e0c77`
  - `docs/research/RS_P2_014_PORTFOLIO_STATE_DESIGN.md`:
    `66a6761859936b0c84fa83afb475952bc172759366752193475d6f4aabf7762c`
- At the RS-P2-014 hard stop, no A1 was granted, no collection/unblinding
  occurred, no baseline or live authority changed, and RS-P2-015–018 were
  open. RS-P2-015, RS-P2-016, and RS-P2-017 were subsequently implemented
  under separate owner approvals; RS-P2-018 remains open.

### RS-P2-015 fixed-notional evidence — 2026-07-18

- **Owner policy frozen exactly:** both sides receive one Rp13,000,000 gross
  pre-cost sleeve against the same frozen RS-P2-014 state. Sizing uses planned
  integer `entry_high`, whole lots of 100, residual cash remains idle, entry
  and exit capacity are `ALL_OR_NONE`, and only the primary 15-day lifecycle
  emits future-NAV-eligible holding/cash-flow records
  (`core/shadow_protocol/fixed_notional.py:187-308, 677-1479`).
- **FN1–FN8 and FN-N1–FN-N3 are executable:** the Rp130,000/one-lot boundary,
  separate entry cost and potentially greater-than-Rp13m debit, explicit
  sleeve/trade/risk denominators, no-action semantics, honest entry/exit
  capacity exclusions, exit censoring, and the mandatory future RS-P2-018
  censor-count note are literal/schema-bound. Participation remains
  `DERIVED_NOT_CALIBRATED`; no empirical market-impact claim was introduced.
- **Exact arithmetic:** persisted money, gross value, cost, debit/credit,
  holding value, P&L, and cash flows are strict integer IDR. Applicable bps are
  aggregated and ceiled adversely once. Ratios use the frozen 12-decimal
  `ROUND_HALF_EVEN` rule. Exact semantic replay rejects coordinated arithmetic
  drift, including a recomputed one-IDR risk-basis change
  (`core/shadow_protocol/fixed_notional.py:1675-1715, 2700-2807`;
  `core/shadow_protocol/fixed_notional_store.py:1476-1534`).
- **Causal shared input:** `FixedNotionalPairInput` binds the exact manifest,
  raw capture, candidate set/candidate, snapshot, portfolio state, paired
  observation, fixed policy, causal liquidity, frozen calendar, integer bars,
  label, cost, and corporate-action identities. Signal-time action hashes,
  dividend convention, post-signal effective sessions, source vintages,
  evaluation cutoffs, and lifecycle chronology fail closed
  (`core/shadow_protocol/fixed_notional.py:733-858, 2067-2664`).
- **Deterministic paired result:** control and challenger are derived directly
  from the same hash-bound PairInput; there are no pluggable side callbacks.
  Shared admission/exclusion must agree, while decision geometry and causal
  outcomes may differ. Primary/secondary lifecycle identities and exact T+2
  cash settlement are machine-validated
  (`core/shadow_protocol/fixed_notional.py:2667-2832`).
- **Immutable storage and lineage:** artifacts live under
  `protocols/{protocol_id}/{manifest_sha256}/fixed_notional/{kind}/`
  `{canonical_sha256}/{raw_sha256}.json`; references live under
  `fixed_notional/refs/{kind}/{artifact_id}.json`. Exclusive create, dual
  canonical/raw identity, byte length, canonical path, sorted named
  predecessors, exact PairInput semantic replay, and orphan rejection are
  enforced. The embedded RS-P2-014 lineage is not trusted: candidate and
  portfolio predecessors are reconstructed through their strict stores
  (`core/shadow_protocol/fixed_notional_store.py:517-1438`).
- **Governance remains closed:** current approval/closure state is reloaded
  using actual attempt time before fixed-notional maturation
  (`core/shadow_protocol/governance.py:1166-1200`). The additive capability
  literal is `RS_P2_015_IMPLEMENTED_NOT_A1_ELIGIBLE`; its existence does not
  grant A1.
- **Tests:** FN1–FN8/FN-N1–FN-N3, corporate-action causality, point-in-time
  liquidity, high-price/entry/exit exclusions, exact T+2, chronology,
  coordinated one-IDR drift, tamper, replay/idempotency, base-lineage
  reconstruction, named predecessors, duplicate keys, byte/path identity,
  authority literals, v1 non-reinterpretation, and cross-process hashes are
  covered in `tests/test_shadow_protocol_p2_015.py:793-2113`.
- **Verification:** RS-P2-015 file `57 passed`; focused five-file shadow suite
  `216 passed`; full suite `1866 passed, 3 skipped`; repository-wide
  `ruff check --fix .` passed and changed no files; `uv lock --check` passed.
- **Protected SHA-256 remained byte-identical:**
  - `core/shadow_protocol/contracts.py`:
    `87b605fb9cc3cb3bee73d903110801699e06e63f4d41e9e8b94cdd48d0ee54b7`;
  - `core/shadow_protocol/calendar.py`:
    `fe27a4e5c964c26f3093921193f29ec45f4f4c09f620b52ca94806ab302c7151`;
  - `core/shadow_protocol/outcome_engine.py`:
    `b90d149df67d91f59408618e580c75f55d2de2257cf6e5f46f4265dffaaa27a8`.
- **Final changed/new SHA-256:**
  - `core/shadow_protocol/__init__.py`:
    `213e021453fe67f45363f1a57d70aac3af6602ff5e12cabb01c884b12e82376c`;
  - `core/shadow_protocol/governance.py`:
    `b2cdb46e36f453ba10e28a76403ce1768947c7dcd9d5093c37f13ab4ad4be7c4`;
  - `core/shadow_protocol/portfolio.py`:
    `63f7150f04c362791841f618969beba81de3849feb778681264d028fc815ee10`;
  - `core/shadow_protocol/fixed_notional.py`:
    `485039d73f187e3b3092d5fbb733f2e9b5ee61617a2fa9e4dd0bef9e65bb0146`;
  - `core/shadow_protocol/fixed_notional_store.py`:
    `ff148c6a350025636342da113f8ff40cbc390049b9eb64c00610b564d50bfbcc`;
  - `tests/test_shadow_protocol_p2_015.py`:
    `3c90d9af8041ede84d9df59c4a382efda41acaff5be72e722631455fcfd6d06a`.
- **Hard scope boundary:** no real component manifest/A1, approval-ledger event,
  collection cohort, unblinding, threshold or decision-logic change,
  `trade_math.py` change, baseline change, or live/ranking/sizing/execution
  authority was created. Synthetic temporary governance fixtures are not a
  granted A1 or collection cohort. `shadow-evaluation-v1` is unchanged and not
  reinterpreted. RS-P2-016 and RS-P2-017 were subsequently implemented under
  separate owner approvals; RS-P2-018 remains open, and the global
  RS-P2-019/021/022 family-coverage tasks remain partial. The
  `rs_p2_017_eligible` marker remains the P2-015 intake boundary consumed by
  the separate P2-017 daily-NAV family; RS-P2-018 aggregation/reporting does
  not yet exist.
- Implementation commit:
  `6e459c8a15b439f35e46d4791db1ddbbcb2d92af`.
- The documentation commit is reported in the external handoff after this
  checklist is committed; it is intentionally not embedded here to avoid
  self-reference.

### RS-P2-016 policy-portfolio evidence — 2026-07-19

- **PP1–PP14 are frozen as an evaluation-only policy:** control and
  challenger begin from the same side-neutral, empty Rp100,000,000 economic
  genesis and then evolve independently. Admission uses each side's
  `recorded_position_fraction` and `recorded_rank`; missing or ambiguous values
  fail closed without a Rp13m fallback. Candidate priority is
  `recorded_rank ASC → source_row_number ASC → ticker ASC`; sector and cluster
  limits are hard two-name caps with no soft overflow
  (`core/shadow_protocol/policy_portfolio.py:177-318,319-380,593-701`).
- **Risk and session semantics are executable, not prose-only:** every
  admission records all ten gates in the owner-approved order; pending
  commitments reserve worst-case gross plus exact entry cost and are rechecked
  at fill. Regime changes take effect on the next frozen IDX session, do not
  force-liquidate existing positions, and cancel lower-priority excess
  commitments. The state tracks strict integer-IDR cash, positions,
  reservations, T+2 purchase payables and sale receivables, realized P&L,
  planned risk, exposure, and path status
  (`core/shadow_protocol/policy_portfolio.py:702-1890,2615-4563`).
- **PP-A1 is enforced at both policy and manifest boundaries:** portfolio heat
  is `0.013` of starting capital and is labeled
  `fraction_of_starting_capital`; a `fraction_of_NAV` heat label is rejected.
  This is the approved semantic correction to the earlier V2 wording, not a
  parameter change. Other true-NAV fields remain
  `NOT_ESTIMABLE_UNTIL_RS_P2_017`
  (`core/shadow_protocol/policy_portfolio.py:1915-2053`;
  `tests/test_shadow_protocol_p2_016.py:703-754`).
- **PP-N1–PP-N3 are explicit:** the view is a symmetric formalization of a
  batch-only control, not a false claim that live control already had a
  persistent portfolio engine. The documented deviations include hard
  sector/cluster caps, pending-cancellation/reset behavior, regime-down
  handling, and purchase-payable accounting. Phase-8 data production must
  populate rank and position fraction for allocating decisions, while the
  older `ShadowDecision` fields remain schema-compatible. The expected
  approximately 17+ session terminal taper is deterministic and is not treated
  as an activity anomaly
  (`docs/research/RS_P2_016_POLICY_PORTFOLIO_DESIGN.md:477-536`).
- **Deterministic independent evolution:** one hash-bound
  `PolicyPortfolioSessionInput` carries the shared current opportunity-set
  evidence plus active-cohort lineage. Control and challenger consume the same
  frozen manifest, calendar, cost/risk rules, regime, and available inputs,
  while their post-decision holdings and cash paths may diverge legitimately.
  Session transitions are pure-derived and exact-replay verified; integer
  resource journals, chronology, causal classification/liquidity, regime
  timing, split handling, daily-stop behavior, terminal rules, and
  `NOT_ESTIMABLE_FROM_SESSION` propagation fail closed
  (`core/shadow_protocol/policy_portfolio.py:1349-1890,2615-4963`).
- **Additive immutable artifact family:** new frozen policy, genesis, regime,
  classification, session-liquidity, candidate/session-input, settlement-leg,
  commitment, position, event, payload, state, transition, paired-session,
  reference, and lineage artifacts live only in the isolated shadow package.
  The store uses exclusive creation, exact manifest namespaces, canonical-model
  and raw-file SHA-256 plus byte length, sorted predecessor hashes, strict
  loaders, persisted RS-P2-014/015 predecessor reconstruction, and
  deterministic bundle replay
  (`core/shadow_protocol/policy_portfolio_store.py:142-1348`). Public exports
  are additive (`core/shadow_protocol/__init__.py:243-317,660-708`).
- **Validation coverage:** the 45-test RS-P2-016 file covers PP-A1 binding,
  identical genesis, ten-gate order, duplicate priority, hard sector/cluster
  caps, point-in-time fill rechecks, independent divergence, explicit empty
  current sets with active older cohorts, recorded-size/rank failures, exact
  T+2 payable/receivable timelines, supported splits, causal
  classification/liquidity, regime downshifts and priority cancellation,
  exact-threshold daily stop and pending cancellation, fail-closed frozen
  paths, starting-capital heat, terminal runway, one-IDR drift,
  transition-journal reconciliation, chronology, authority literals,
  content-addressed tamper/replay, duplicate keys, explicit
  `shadow-evaluation-v1` rejection, and cross-process hash determinism
  (`tests/test_shadow_protocol_p2_016.py:703-1840`).
- **Verification gate:** RS-P2-016 file `45 passed`; focused six-file shadow
  suite `261 passed`; full suite `1911 passed, 3 skipped`; repository-wide
  `ruff check --fix .` passed and changed no files; `uv lock --check` resolved
  all 185 packages successfully. The first sandbox-only full-suite attempt
  stopped during environment setup with seven `certifi` `PermissionError`s,
  not test assertion failures; the authorized rerun outside that sandbox
  passed `1911/3`.
- **Compatibility and scope boundary:** the original forecasting
  `shadow-evaluation-v1` implementation is unchanged and its artifacts are
  rejected explicitly by P2-016 loaders rather than reinterpreted. No real
  component manifest or A1 was granted, no approval-ledger collection event
  was appended, no observation/outcome cohort was collected or unblinded, and
  no threshold, live decision path, ranking, `trade_math.py`, baseline, or live
  authority changed. All new artifacts remain `evaluation_only=true`,
  `live_authority=false`, and `affects_execution/ranking/sizing=false`.
- **Subsequent status:** RS-P2-017 now creates the actual daily
  marked-to-market series under a separate approved pass. RS-P2-018 must still
  compute common metrics and explicit
  `NOT_ESTIMABLE` denominators/censor counts. RS-P2-019, RS-P2-021, and
  RS-P2-022 remain globally **PARTIAL** until every artifact family in the
  coverage matrix has immutable-path, tamper, and replay/canonical-hash
  evidence. RS-P2-023/024 reporting and the first real C7 manifest/A1 also
  remain separate approval-gated work.
- Feature implementation commit:
  `9638762998626a372cf6e33851f4423937dd9584`.
- The documentation commit is reported in the external handoff after this
  checklist is committed; it is intentionally not embedded here to avoid
  self-reference.

### RS-P2-017 daily-NAV evidence — 2026-07-19

- **Two non-interchangeable series are executable:**
  `POLICY_PORTFOLIO_NAV` is keyed by one independently evolving P2-016
  portfolio path and may later supply portfolio metrics;
  `FIXED_NOTIONAL_SLEEVE_EQUITY` is keyed by one P2-015 opportunity and side
  and cannot be relabeled, summed, or netted across opportunities. The frozen
  policy binds both families, parent policy hashes, exact official-mark
  source, calendar, costs, labels, corporate-action policy, methodology, and
  the identical control/challenger CONFIG hash
  (`core/shadow_protocol/daily_nav.py:148-242,878-985`;
  `tests/test_shadow_protocol_p2_017.py:420-476,862-895`).
- **Point-in-time marks and exact accounting:** persisted marks are strict
  integer-IDR official raw closes with source-record canonical/raw hashes,
  byte identity, `available_at`, `captured_at`, calendar, and
  corporate-action lineage. Policy NAV recomputes settled cash plus sale
  receivable minus purchase payable plus marked holdings from exact P2-016
  predecessors. Fixed sleeves separately account for idle/settled cash,
  payable, receivable, marked holdings, and the explicit unfunded-cost
  liability; no starting-capital, entry-price, previous-close, stale, or
  future-mark fallback is allowed
  (`core/shadow_protocol/daily_nav.py:257-566,1409-1810,1811-2166`;
  `tests/test_shadow_protocol_p2_017.py:478-561,657-797,1101-1176`).
- **Daily return and missingness semantics are honest:** genesis has
  `NOT_ESTIMABLE_NO_PREDECESSOR`; later simple returns use only immediately
  adjacent estimable points and the frozen 12-place quantization. A missing
  official mark, suspension, predecessor failure, unresolved terminal, or
  insolvency cannot be bridged or silently converted to zero. Canonical
  missingness permanently poisons the primary chain. Each censor retains
  ticker, first/current session, deterministic duration, source/reason
  lineage, family, and side
  (`core/shadow_protocol/daily_nav.py:354-566,684-838,2167-2331`;
  `tests/test_shadow_protocol_p2_017.py:478-655,763-801`).
- **NV-N2 remains an RS-P2-018 obligation:** the P2-017 point contract
  preserves `censored_tickers` and `censor_duration_sessions`; P2-018 must
  aggregate and report censor count and duration by side and ticker, with
  source/liquidity slices only when supported. It must expose denominator
  impact and cannot silently drop suspended or missing-mark paths
  (`core/shadow_protocol/daily_nav.py:400-430`;
  `docs/research/RS_P2_017_DAILY_NAV_DESIGN.md:271-285`).
- **Immutable local evidence graph:** policy, mark-input, point, event, and
  snapshot nodes use exact v1 loaders, duplicate-key rejection,
  exclusive-create content-addressed paths, canonical-model and raw-file
  SHA-256, byte length, immutable logical references, named predecessors,
  local prior-snapshot prefix checks, and deterministic stored-byte replay
  (`core/shadow_protocol/daily_nav_store.py:102-530,666-853,860-1003`;
  `tests/test_shadow_protocol_p2_017.py:897-1099`).
- **Local is not global completeness:** every snapshot is literally
  `UNANCHORED_NOT_CERTIFIED_COMPLETE`. P2-017 does not invent the independently
  authenticated expected-tail/run commitment needed to detect deletion of a
  valid local tail. That external anchor and global stored-byte reconstruction
  remain residual RS-P2-019/021/022 work; local discovery is never treated as
  certification
  (`core/shadow_protocol/daily_nav.py:630-838`;
  `core/shadow_protocol/daily_nav_store.py:149-157,398-472`).
- **Safety and compatibility:** all new artifacts/references remain
  `evaluation_only=true`, `live_authority=false`, and
  `affects_execution/ranking/sizing=false`. Exact-v1 loaders reject
  `shadow-evaluation-v1`; no existing forecasting artifact is reinterpreted
  (`tests/test_shadow_protocol_p2_017.py:803-860,920-974`).
- **Verification:** RS-P2-017 file `28 passed`; focused seven-file shadow suite
  `289 passed`; authorized unsandboxed full suite `1939 passed, 3 skipped`.
  The initial sandboxed full run reached `1932 passed, 3 skipped` but the same
  seven environment-sensitive `test_model_integration.py` tests seen in the
  P2-016 pass failed there; all seven passed outside the sandbox.
  Repository-wide Ruff `check --fix` passed with no edits; `uv lock --check`
  resolved 185 packages; touched-file `py_compile` passed.
- **Hard scope boundary:** no component manifest or A1 was granted, no
  collection/unblinding occurred, and no threshold, recommendation,
  decision, ranking, sizing, execution, baseline, or live authority changed.
  RS-P2-018, RS-P2-019, RS-P2-021, RS-P2-022, RS-P2-023, and RS-P2-024 remain
  separate work.
- Feature implementation commit:
  `d5ae02fddbb4ba070857e4d6281b2d33afe14b6d`.
- The documentation commit is reported in the external handoff after this
  checklist is committed; it is intentionally not embedded here to avoid
  self-reference.

### Storage, validation, and reporting

- [ ] **RS-P2-019 (PARTIAL):** Use versioned immutable output paths; no sole
  reliance on `latest_*` aliases. Manifest, approval/ledger, closure,
  portfolio-state, candidate-set, fixed-notional, observation, and calendar
  paths exist. P2-017 daily-NAV nodes also have local dual-hash immutable
  paths, but their snapshot chain is explicitly
  `UNANCHORED_NOT_CERTIFIED_COMPLETE`; an authenticated expected-tail/run
  commitment does not yet exist. Generic paired-input, snapshot/source-vintage,
  and outcome-ledger storage remain incomplete. See the
  [family coverage matrix](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#2-rs-p2-019--rs-p2-021--rs-p2-022-coverage-matrix).
- [ ] **RS-P2-020:** Add artifact-validator rules for schema, hashes, authority
  flags, paired opportunity sets, source lineage, and forbidden execution drift.
- [ ] **RS-P2-021 (PARTIAL):** Add tamper tests: modified manifest, snapshot,
  decision, source vintage, or outcome must be rejected. Strong family-level
  coverage exists, including local P2-017 NAV byte/reference tamper rejection,
  but no external NAV-tail deletion proof exists. Stored
  ClosureRecord/closure-reference, paired-input, observation maturation,
  generic outcome-storage, calendar, and generic snapshot/source-vintage gaps
  also remain. See the
  [family coverage matrix](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#2-rs-p2-019--rs-p2-021--rs-p2-022-coverage-matrix).
- [ ] **RS-P2-022 (PARTIAL):** Add replay/idempotency tests and deterministic
  canonical JSON/hash tests. Portfolio-state, fixed-notional, and the local
  P2-017 NAV graph are covered; manifest, initial approval/ledger,
  paired-input, calendar, generic outcome stored-byte replay, externally
  anchored NAV-tail replay, and stored-byte end-to-end replay remain
  incomplete. See the
  [family coverage matrix](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#2-rs-p2-019--rs-p2-021--rs-p2-022-coverage-matrix).
- [ ] **RS-P2-023:** Produce separate daily integrity, weekly operational,
  monthly blinded data-quality, and fixed-terminal reports.
- [ ] **RS-P2-024:** Make reports visibly label all results as shadow and reject
  any human-readable claim that the challenger is live/trusted.
- [x] **RS-P2-025:** Preserve the existing forecasting `shadow-evaluation-v1`
  contract or migrate it explicitly with backward-compatibility tests; do not
  silently reinterpret old artifacts. The preservation branch is verified:
  the original module is unchanged since
  `ca105098b78ba8acfa08878c5d9fb0e9e642a2c2`, while manifest-v2 and
  fixed-notional loaders explicitly reject reinterpretation. Exact tests,
  commits, and residual hardening are recorded in the
  [RS-P2-025 verdict](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#1-rs-p2-025-preservation-verdict).

### Required tests

- [x] Schema rejects live authority or execution influence.
- [ ] Control payload is byte/semantic-equivalent with shadow enabled or
  disabled.
- [x] Same opportunity set and snapshot hash are mandatory.
- [x] Primary 15-day label cannot be closed by a 3/5/10-day checkpoint.
- [x] Leakage, tampering, duplicate maturity, and invalid corporate-action cases
  fail closed.
- [x] Full recommendation-context, signal-packet, artifact, forecasting-shadow,
  API, report, and orchestrator regressions pass.

### Phase 2 Definition of Done

- [ ] A frozen replay produces paired observations and outcomes with no live
  decision difference caused by instrumentation.
- [ ] A reviewer can reproduce the same hashes and report from the manifest.
- [x] No C1–C8 outcome collection has started without an approved manifest.

**HARD STOP:** Phase 2 creates measurement capability only. Do not run a live
paper cohort until its component manifest is separately approved.

---

## Phase 3 — C6 canonical DSR and trial governance

**Objective:** make statistical promotion governance trustworthy before using
DSR to approve an outcome-changing component.

**Dependencies:** Phase 2 evidence/NAV/trial-registry substrate.

**Current issue:** duplicate implementations remain in
`core/backtester/metrics_calculator.py` and
`src/evaluation/backtest_metrics.py`; some paths incorrectly use the number of
OOS windows as `n_trials`, while other paths use `n_trials=1`.

### Canonicalization

- [ ] **RS-P3-001:** Designate one canonical PSR/DSR implementation.
- [ ] **RS-P3-002:** Convert the duplicate to a compatibility wrapper or retire
  it after call-site and artifact migration tests.
- [ ] **RS-P3-003:** Label `n_trials=1` results PSR-like/no-selection-deflation;
  never market them as multiple-testing-adjusted evidence.
- [ ] **RS-P3-004:** Remove “OOS windows = trials” semantics. `n_trials` must
  represent tried strategy/configuration variants, adjusted for dependence.
- [ ] **RS-P3-005:** Reconstruct 100% of variants in each research family in the
  immutable trial registry, including discarded variants.

### Return-series and autocorrelation governance

- [ ] **RS-P3-006:** Use daily marked-to-market NAV returns for the canonical
  path.
- [ ] **RS-P3-007:** Add predeclared HAC/Newey–West autocorrelation adjustment
  capped at lag 14.
- [ ] **RS-P3-008:** Add fixed-calendar-origin, non-overlapping 15-day return
  sensitivity.
- [ ] **RS-P3-009:** Record skew, kurtosis, sample length, Sharpe variance,
  effective sample size, effective trial count, benchmark Sharpe, and every
  assumption in the output.
- [ ] **RS-P3-010:** Implement PBO/CSCV only when its minimum registered
  configuration/block requirements are met; otherwise emit
  `PBO_NOT_ESTIMABLE`.

### Numerical validation

- [ ] **RS-P3-011:** Create at least 30 frozen return-series fixtures covering
  Gaussian, non-Gaussian, skewed, fat-tailed, positive/negative autocorrelation,
  overlapping holdings, one-trial, and multi-trial cases.
- [ ] **RS-P3-012:** Add hand-worked paper examples and an independent reference
  implementation.
- [ ] **RS-P3-013:** Require canonical/reference agreement within `1e-6` on
  frozen fixtures.
- [ ] **RS-P3-014:** Run at least 10,000 Monte Carlo replications per registered
  coverage/error case.
- [ ] **RS-P3-015:** Predeclare the 95% simulation interval around nominal
  behavior before examining results.
- [ ] **RS-P3-016:** Confirm HAC and non-overlapping sensitivity do not reverse a
  GO conclusion.

### C6 decision

- [ ] **RS-P3-017:** Independent reviewer reconstructs formula, trial family,
  fixtures, and Monte Carlo results.
- [ ] **RS-P3-018:** Mark C6 `GO` only if agreement, coverage, sensitivity, and
  registry completeness all pass.
- [ ] **RS-P3-019:** On failure, label DSR `NOT_PROMOTION_GRADE`; raw Sharpe may
  not replace it.

### Phase 3 Definition of Done

- [ ] One canonical implementation and one independent reference agree.
- [ ] Trial registry completeness is 100%.
- [ ] Every fixture family passes its predeclared numerical gate.
- [ ] C6 has a signed GO/NO-GO report.

**BLOCKER:** C1, C2, C3, C4a, C4b1, C4b2, and C5 cannot receive GO until C6
passes, even if their other metrics look good.

---

## Phase 4 — C7/C8 safety challengers

**Objective:** close two unsafe bypass semantics under paired shadow evidence.

**Dependencies:** Phase 2. C6 is not required for a safety-only GO, but DSR must
remain descriptive until C6 passes.

### C7 — Missing-liquidity abstention

- [ ] **RS-P4-C7-001:** Define mandatory liquidity/capacity inputs and exact
  missingness taxonomy: absent, zero, negative, non-numeric, `NaN`, infinity,
  stale, hash-invalid, expired, and provider failure.
- [ ] **RS-P4-C7-002:** Add finite-number validation to ADT computation and
  record source precedence.
- [ ] **RS-P4-C7-003:** Build a SHADOW-ONLY challenger returning
  `ABSTAIN / DATA_INSUFFICIENT` with `sizing_allowed=false` when mandatory
  liquidity is unmeasurable.
- [ ] **RS-P4-C7-004:** Keep measured `<Rp2B`, `Rp2B–10B`, and `≥Rp10B` behavior
  unchanged in this trial.
- [ ] **RS-P4-C7-005:** Record control versus challenger status, missingness root
  cause, recovery latency, and any downstream sizing attempt.
- [ ] **RS-P4-C7-006:** Property-test every missingness representation and every
  known-liquidity band.
- [ ] **RS-P4-C7-007:** Prove that truly missing mandatory liquidity yields 100%
  abstention and zero shadow sizing attempts.
- [ ] **RS-P4-C7-008:** Prove zero false block for valid measured-liquidity
  candidates outside scope.
- [ ] **RS-P4-C7-009:** Collect at least 30 affected independent events; target
  60. Expected duration is 6–12+ months and may be longer for sparse paths.
- [ ] **RS-P4-C7-010:** Require false-missing rate `<1%` and at least 95% of
  recoverable failures within the predeclared refresh SLA.
- [ ] **RS-P4-C7-011:** Report opportunity cost, DSR, and drawdown where
  estimable, but never claim return superiority from a safety GO.
- [ ] **RS-P4-C7-012:** On widespread source outage/false missingness, stop the
  challenger and globally halt the affected execution path; never silently fail
  open.

### C8 — Disable the `momentum_play` overvaluation exemption

- [ ] **RS-P4-C8-001:** Complete Phase-0 reachability audit across prompt,
  parsing, schema, validation, risk governor, historical artifacts, and tests.
- [ ] **RS-P4-C8-002:** Freeze the current reachable behavior as control; do not
  relabel it “dormant” without prevalence evidence.
- [ ] **RS-P4-C8-003:** Build a SHADOW-ONLY challenger in which an overvalued
  candidate follows ordinary fail-closed overvaluation handling even when
  `momentum_play=true`.
- [ ] **RS-P4-C8-004:** Do not test enabling or broadening the exemption. Claimed
  R/R 2.5 and half-size protections are not evidence and are out of scope.
- [ ] **RS-P4-C8-005:** Preserve all unrelated candidates and all other safety
  gates exactly.
- [ ] **RS-P4-C8-006:** Property-test snapshot, liquidity, ex-date, noise,
  circuit-breaker, and portfolio gates so the flag can bypass none of them.
- [ ] **RS-P4-C8-007:** Collect at least 30 independent overvalued
  `momentum_play=true` events and 30 matched overvalued records without the
  flag. Pre-registered replay may exercise the branch but may not inject flags
  into live decisions.
- [ ] **RS-P4-C8-008:** Require 100% ordinary fail-closed enforcement, zero
  unrelated decision changes, and zero safety-gate bypasses.
- [ ] **RS-P4-C8-009:** Report opportunity cost/return/DSR/drawdown only as
  descriptive diagnostics.
- [ ] **RS-P4-C8-010:** If natural occurrences are insufficient, status remains
  `CONTINUE`; the permissive branch must not be promoted or expanded.

### Phase 4 Definition of Done

- [ ] C7 and C8 have separate immutable protocol IDs and paired records.
- [ ] Canonical control output remains unchanged during shadow collection.
- [ ] Formal/property tests cover their full safety scope.
- [ ] Each receives only `SAFETY_GO`, `CONTINUE`, or `NO_GO`.

---

## Phase 5 — Point-in-time data and provenance foundations

**Objective:** make source truth explicit before building models that depend on
it. This phase may collect data in parallel by component.

### Shared source envelope

- [ ] **RS-P5-001:** Standardize source record fields: source ID/URL, authority
  class, raw value/unit/currency/tenor, published/effective/fetched/as-of dates,
  expiry, raw hash, parser/transform version, revision/restatement status, and
  freshness state.
- [ ] **RS-P5-002:** Expired, incompatible, unverified, hash-invalid, or future
  data must produce explicit `ABSTAIN_SOURCE_*`/UNKNOWN, never a silent fallback
  that is more permissive.
- [ ] **RS-P5-003:** Store raw and normalized records immutably and make every
  model feature traceable to them.
- [ ] **RS-P5-004:** Add source-age, missingness, revision, coverage, and leakage
  dashboards by component.

### C2 data — Discount-rate decomposition

- [ ] **RS-P5-C2-001:** Acquire point-in-time 10-year IDR government yield with
  a two-business-day expiry.
- [ ] **RS-P5-C2-002:** Acquire point-in-time Damodaran rating default spread,
  mature ERP, and rating CRP; expire at the stated next update or 190 calendar
  days, whichever comes first.
- [ ] **RS-P5-C2-003:** Validate currency/tenor conventions and source date
  compatibility.
- [ ] **RS-P5-C2-004:** Freeze primary rating construction; keep CDS as a
  separately registered sensitivity.
- [ ] **RS-P5-C2-005:** Hold control beta, lambda=1, and current sector premium
  fixed so the first trial isolates sovereign-risk construction.
- [ ] **RS-P5-C2-006:** Add two-reviewer numerical reconciliation fixtures.

### C3 data — Indonesian finance sentiment

- [ ] **RS-P5-C3-001:** Build at least 300 URL/content/syndication-deduplicated
  Indonesian finance texts with timestamps and ticker/event grouping.
- [ ] **RS-P5-C3-002:** Freeze train/calibration/test time splits with no
  ticker/event leakage.
- [ ] **RS-P5-C3-003:** Freeze annotation guide, sentiment classes, out-of-domain
  and abstain semantics before revealing model outputs.
- [ ] **RS-P5-C3-004:** Use two blinded annotators for the untouched test set and
  at least 20% of earlier cohorts; use blinded third-party adjudication.
- [ ] **RS-P5-C3-005:** Require Krippendorff’s alpha `≥.75` before model
  comparison.
- [ ] **RS-P5-C3-006:** Ensure untouched test has at least 50 examples per class;
  collect sparse classes rather than oversampling the final test.

### C4b1 data — Foreign flow

- [ ] **RS-P5-C4B1-001:** Select an authoritative point-in-time foreign-flow
  source and freeze definitions, timezone, market-close availability, revision,
  and expiry behavior.
- [ ] **RS-P5-C4B1-002:** Persist at least 252 OOS daily source observations and
  two predeclared flow strata.
- [ ] **RS-P5-C4B1-003:** Prevent post-close/future data from entering a signal
  generated earlier that day.
- [ ] **RS-P5-C4B1-004:** Expired/missing flow must resolve to UNKNOWN/control,
  never to a more permissive regime.

### C4b2 data — MSCI state

- [ ] **RS-P5-C4B2-001:** Replace the concept of a permanent Boolean in the
  challenger with an official-source record: state, announcement date,
  effective date, review date, expiry, source URL/hash, and fetch date.
- [ ] **RS-P5-C4B2-002:** Keep MSCI and S&P DJI states separate.
- [ ] **RS-P5-C4B2-003:** Repeated daily copies of one announcement count as one
  source-state vintage.
- [ ] **RS-P5-C4B2-004:** Missing/expired records must be non-decisional or
  conservative.

### C5 data — Paper-faithful IDX4

- [ ] **RS-P5-C5-001:** Implement restatement-aware, point-in-time filing
  vintages; never forward-fill a future filing.
- [ ] **RS-P5-C5-002:** Reproduce the paper’s OCF/market-equity and EBIT/book-
  enterprise-value definitions exactly.
- [ ] **RS-P5-C5-003:** Reproduce the paper’s 2×3 portfolio construction before
  outcome collection.
- [ ] **RS-P5-C5-004:** Achieve at least 80% point-in-time coverage in the
  eligible non-financial universe.
- [ ] **RS-P5-C5-005:** Require at least 100 eligible names in every prospective
  monthly cross-section.
- [ ] **RS-P5-C5-006:** Audit coverage/missingness by size, sector, survival, and
  subsequent return.

### Phase 5 Definition of Done

- [ ] Every related challenger can reconstruct every feature from immutable
  source records available at the signal timestamp.
- [ ] Missing/expired sources have explicit conservative behavior.
- [ ] Component-specific entry prerequisites are met before its Phase-7 model
  trial begins.

---

## Phase 6 — C1 calibrated selective-recommendation core

**Objective:** add calibrated recommendation value above the deterministic
control without changing hard-gate actionability.

**Dependencies:** Phase 2; C6 may run in parallel but must pass before C1 GO.

### Outcome and dataset design

- [ ] **RS-P6-001:** Define mutually exclusive primary outcomes:
  `target_first`, `stop_first`, `timeout_or_exception`, plus separate `unfilled`.
- [ ] **RS-P6-002:** Model conditional timeout return or expected net R directly;
  do not assume timeout return is zero.
- [ ] **RS-P6-003:** Use point-in-time features only and preserve planned versus
  executed entry/stop/target/slippage.
- [ ] **RS-P6-004:** Create anchored walk-forward training, later
  calibration/selection, and untouched prospective-test cohorts.
- [ ] **RS-P6-005:** Apply 15-trading-day purge/embargo and issuer/event purging
  at every boundary.
- [ ] **RS-P6-006:** Define material outcome classes, score bins, and
  recommendation-state-changing score regions before testing.

### Model and calibration layer

- [ ] **RS-P6-007:** Establish frozen time/regime base-rate and intercept/slope
  baselines.
- [ ] **RS-P6-008:** Train candidate competing-risk probability models only on
  the training cohort.
- [ ] **RS-P6-009:** Compare calibration mappings only on the calibration cohort:
  intercept/slope baseline, Platt/temperature where structurally appropriate,
  and isotonic only with adequate calibration data.
- [ ] **RS-P6-010:** Register every tried model, feature set, calibration method,
  and hyperparameter in C6 trial governance.
- [ ] **RS-P6-011:** Freeze one challenger before untouched testing.
- [ ] **RS-P6-012:** Ensure `p_target + p_stop + p_timeout_or_exception = 1`
  within numerical tolerance.
- [ ] **RS-P6-013:** Compute expected net R directly or as probability times
  conditional outcome net R, with costs included.
- [ ] **RS-P6-014:** Add conformal prediction/risk sets only if assumptions and
  held-out coverage are defensible; label exchangeability/shift limitations.

### Authority and output integration

- [ ] **RS-P6-015:** Add shadow fields to the recommendation context with model
  version, cohort sizes, calibration window, Brier, ECE, interval/set, and shift
  status.
- [ ] **RS-P6-016:** Until approval, label them `SHADOW_UNCALIBRATED` or
  equivalent and enforce zero influence on rating, rank, sizing, and execution.
- [ ] **RS-P6-017:** Create a separate counterfactual opportunity rank; never
  mix it with executable rank or near-miss rank.
- [ ] **RS-P6-018:** Expose reliability, risk–coverage, evidence quality, and
  cohort `n`, not only one confidence number.
- [ ] **RS-P6-019:** Preserve exact hard-gate blocker and actionability even when
  the calibrated expected net R is favorable.

### Required untouched-test evidence

- [ ] **RS-P6-020:** At least 100 independent closed test clusters.
- [ ] **RS-P6-021:** At least 30 test observations in every predeclared material
  outcome class.
- [ ] **RS-P6-022:** Five calibration-cohort-defined score bins, each with at
  least 20 untouched-test observations.
- [ ] **RS-P6-023:** At least 30 clusters in every score region that could change
  displayed recommendation state.
- [ ] **RS-P6-024:** Expected duration: 4–8+ months using the full screened
  universe, or 6–12+ months using only late-stage candidates.

### C1 GO gate

- [ ] Universal and outcome-changing non-inferiority gates pass.
- [ ] C6 has passed.
- [ ] Brier skill is at least 5% versus the frozen base-rate model.
- [ ] ECE point estimate `≤.05`; upper 95% issuer/date-block bound `≤.08`.
- [ ] Logistic calibration slope is `.8–1.2`; intercept magnitude `≤.10`, with
  confidence intervals and adequate event counts.
- [ ] Risk–coverage is non-inferior at every control operating point and
  strictly better at one calibration-selected point.
- [ ] Challenger DSR is `≥.95`; paired incremental net-R lower bound exceeds
  `−0.05 R` per independent cluster under the registered non-inferiority claim.
- [ ] Challenger drawdown is not worse than control by more than the greater of
  one percentage point or 10% relative.
- [ ] Zero hard-rejected candidate becomes actionable.

### C1 revert

- [ ] Remove probability/rank overlay, retain deterministic states/reasons, and
  keep historical shadow fields for audit.

---

## Phase 7 — Component challengers C2, C3, C4, and C5

Every subsection requires its own protocol ID and paired adapter. Do not ship a
combined “all improvements” model because attribution would be impossible.

### C2 — Discount-rate decomposition challenger

- [ ] **RS-P7-C2-001:** Implement frozen rating formula:
  `Rf_IDR = 10y IDR yield − rating default spread` and
  `Ke = Rf_IDR + control_beta × mature_ERP + 1.0 × rating_CRP + unchanged sector premium`.
- [ ] **RS-P7-C2-002:** Return `ABSTAIN_SOURCE_MISMATCH` on expiry, date/tenor/
  currency mismatch, or irreproducible source.
- [ ] **RS-P7-C2-003:** Keep beta/lambda re-estimation and sector-premium changes
  out of this trial.
- [ ] **RS-P7-C2-004:** Register CDS as sensitivity; only the rating path can own
  GO.
- [ ] **RS-P7-C2-005:** Pair FV bands, risk codes, ranks, decisions, expected
  versus realized R, geometry, DSR, and drawdown.
- [ ] **RS-P7-C2-006:** Collect at least 30 valuation-sensitive changed clusters,
  target 60, across at least two regimes and two valuation sectors; expect
  6–12+ months.
- [ ] **RS-P7-C2-007:** Require two independent formula/input reproductions.
- [ ] **RS-P7-C2-008:** Require superiority gate, absolute rating/CDS Ke
  difference `≤75 bp`, and at least 90% preservation of FV-band/actionability
  direction.
- [ ] **RS-P7-C2-009:** On NO-GO, restore control formula, label it stale/method-
  limited, and keep challenger valuation advisory-only.

### C3 — Finance-domain sentiment challenger

- [ ] **RS-P7-C3-001:** Compare current checkpoint, finance-tuned challenger,
  keyword, LLM-only, and base-rate baselines on the frozen benchmark.
- [ ] **RS-P7-C3-002:** Calibrate probabilities on calibration data only and add
  explicit out-of-domain/abstain behavior.
- [ ] **RS-P7-C3-003:** Report macro-F1, per-class precision/recall, Brier, ECE,
  abstention, context/ticker slices, and issuer/date-block confidence intervals.
- [ ] **RS-P7-C3-004:** Keep debate-confidence influence shadow-only.
- [ ] **RS-P7-C3-005:** Collect at least 30 changed independent signal clusters,
  target 60; expect 3–6+ months for text work and 4–8+ months for outcomes.
- [ ] **RS-P7-C3-006:** Require lower confidence bounds supporting macro-F1
  `≥.75`, every-class recall `≥.65`, Brier improvement `≥5%`, and ECE upper
  bound `≤.05`.
- [ ] **RS-P7-C3-007:** Require zero influence for out-of-domain or below-
  calibrated-confidence cases plus the outcome-changing non-inferiority/DSR
  gate.
- [ ] **RS-P7-C3-008:** On NO-GO, restore disabled-prior/LLM-only control and
  retain model output as research metadata.

### C4a — Regime-model stability challenger

- [ ] **RS-P7-C4A-001:** Freeze exactly one persistence-penalized jump/regime
  challenger.
- [ ] **RS-P7-C4A-002:** Learn state alignment only on training data; freeze
  transition labels and detection costs before test.
- [ ] **RS-P7-C4A-003:** Persist posterior entropy, transition count, dwell time,
  five-day flip rate, detection delay, false-transition cost, and decision
  disagreement.
- [ ] **RS-P7-C4A-004:** UNKNOWN/low-posterior states must never be more
  permissive than the conservative resolver.
- [ ] **RS-P7-C4A-005:** Collect at least 504 untouched OOS days after warm-up,
  all three states, at least five independent transitions, and 30 transition-
  sensitive clusters; expect 2–3+ years.
- [ ] **RS-P7-C4A-006:** Require at least 20% lower five-day flip rate at the
  upper confidence bound and added detection-delay upper bound `≤2` trading
  days, plus non-inferiority/DSR/drawdown gates.
- [ ] **RS-P7-C4A-007:** Without all states/transitions, status is `CONTINUE`, not
  scoped production GO.
- [ ] **RS-P7-C4A-008:** Revert to current HMM plus conservative resolver.

### C4b1 — Point-in-time foreign-flow challenger

- [ ] **RS-P7-C4B1-001:** Wire the frozen foreign-flow record into a separate
  shadow regime input; do not combine with C4a or C4b2.
- [ ] **RS-P7-C4B1-002:** Record source/as-of/expiry and feature availability in
  every decision.
- [ ] **RS-P7-C4B1-003:** Collect at least 30 affected clusters, two flow strata,
  and 252 OOS source days; expect 12+ months.
- [ ] **RS-P7-C4B1-004:** Require 100% lineage, unexpected missingness `<1%`,
  conservative expiry, and predeclared regime-information improvement with
  confidence interval.
- [ ] **RS-P7-C4B1-005:** Require outcome-changing non-inferiority and C6.
- [ ] **RS-P7-C4B1-006:** Revert by omitting the feature and retaining display-
  only provenance.

### C4b2 — Dated MSCI review-state challenger

- [ ] **RS-P7-C4B2-001:** Feed the official dated/expiring state into a separate
  shadow adapter; keep hard-coded control frozen for comparison.
- [ ] **RS-P7-C4B2-002:** Collect at least 30 affected clusters across at least
  two official source-state vintages; expect 12+ months or longer.
- [ ] **RS-P7-C4B2-003:** Require 100% official lineage, zero stale-active days,
  and no missing/expired state more permissive than control.
- [ ] **RS-P7-C4B2-004:** Fewer than two source-state vintages means `CONTINUE`.
- [ ] **RS-P7-C4B2-005:** Require outcome-changing non-inferiority and C6.
- [ ] **RS-P7-C4B2-006:** Revert to conservative resolver and keep official state
  non-decisional.

### C5 — Paper-faithful IDX4 factor challenger

- [ ] **RS-P7-C5-001:** Do not start outcome collection until every Phase-5 C5
  entry prerequisite passes.
- [ ] **RS-P7-C5-002:** Build factor portfolios/returns/exposures; do not infer a
  factor model from stock-level tier weights.
- [ ] **RS-P7-C5-003:** Predeclare the discovery family, simple sector-neutral
  characteristic baseline, effect size, power calculation, costs, and PBO
  configuration.
- [ ] **RS-P7-C5-004:** Collect at least 36 untouched monthly cross-sections, 100
  eligible names in every cross-section, 30 closed signal clusters, and two
  regimes; expect 3–5+ years.
- [ ] **RS-P7-C5-005:** Report coverage, turnover, factor returns, exposure
  stability, IC/HAC or block-bootstrap inference, missingness bias, net R, DSR,
  PBO, drawdown, and sector/size neutrality.
- [ ] **RS-P7-C5-006:** Require coverage `≥80%`, mean OOS IC `>.03`, HAC t-stat
  `≥3.0`, DSR `≥.95`, and costs/neutrality robustness.
- [ ] **RS-P7-C5-007:** If at least eight configurations and eight non-overlap
  blocks make PBO estimable, require PBO `<.20`; otherwise emit
  `PBO_NOT_ESTIMABLE`.
- [ ] **RS-P7-C5-008:** On NO-GO, retain current characteristics with no factor-
  model claim and zero challenger outcome influence.

---

## Phase 8 — Prospective shadow collection and paper trading

**Objective:** collect comparable mature evidence without tuning or live
influence.

### Before first observation

- [ ] **RS-P8-001:** Manifest is signed and immutable.
- [ ] **RS-P8-002:** Control/challenger content hashes and opportunity-set parity
  checks are active.
- [ ] **RS-P8-003:** Terminal date, sample requirements, metrics, multiplicity,
  safety envelope, and rollback owner are frozen.
- [ ] **RS-P8-004:** Full test suite and frozen replay pass.
- [ ] **RS-P8-005:** Paper orders are structurally incapable of reaching live
  execution.

### During collection

- [ ] **RS-P8-006:** Ingest every raw event, even when one side abstains.
- [ ] **RS-P8-007:** Record raw `n`, effective independent `n`, unique issuers,
  dates, groups, and event blocks.
- [ ] **RS-P8-008:** Mature horizons independently; never let an early horizon
  close the 15-day primary label.
- [ ] **RS-P8-009:** Run daily integrity/source/safety checks.
- [ ] **RS-P8-010:** Produce weekly blinded operations reports.
- [ ] **RS-P8-011:** Produce monthly blinded data-quality/sample-maturity and
  trial-registry checksum reports.
- [ ] **RS-P8-012:** Do not inspect or tune on unblinded efficacy metrics before
  the fixed terminal date unless an approved always-valid stopping rule was
  pre-registered.
- [ ] **RS-P8-013:** Apply Holm correction to registered secondary-horizon tests.
- [ ] **RS-P8-014:** Keep control live-authoritative; collect challengers in
  parallel but never blend them into one causal trial.

### Immediate stop/NO-GO conditions

- [ ] Look-ahead, survivorship, revised-fundamental, or timestamp leakage.
- [ ] Opportunity-set mismatch.
- [ ] Manifest/snapshot/source/hash corruption.
- [ ] Any hard-gate false promotion.
- [ ] Any paper order reaching live execution.
- [ ] Unregistered model/configuration selection.
- [ ] Challenger drawdown breaching its frozen safety envelope.
- [ ] A report claiming shadow output is live or trusted.

### Phase 8 Definition of Done

- [ ] Fixed terminal date reached and all required primary outcomes matured.
- [ ] Sample and precision status is computed from independent clusters.
- [ ] Artifacts are complete, immutable, and ready for independent reproduction.

---

## Phase 9 — Fixed-terminal evaluation and component decision

### Universal review

- [ ] **RS-P9-001:** Unblind only after fixed terminal/maturity.
- [ ] **RS-P9-002:** Reproduce from manifest on an independent path/reviewer.
- [ ] **RS-P9-003:** Confirm 100% lineage, zero look-ahead, zero opportunity-set
  mismatch, zero false hard-gate promotions, and complete trial registry.
- [ ] **RS-P9-004:** Confirm affected independent sample and component-specific
  precision; unaffected rows cannot pad `n`.
- [ ] **RS-P9-005:** Report all common return/risk/calibration/system metrics,
  negative results, and `NOT_ESTIMABLE` values.
- [ ] **RS-P9-006:** Verify DSR interpretation is allowed by C6 for C1–C5.

### Decision labels

- [ ] **GO:** every predeclared universal and component gate passed for the
  frozen scope.
- [ ] **CONTINUE:** sample, state, source-vintage, class, or precision requirement
  remains insufficient. No promotion.
- [ ] **NO-GO:** metric, integrity, safety, or fixed-terminal criterion failed.
  Do not tune on the same test and relabel it.

### Decision discipline

- [ ] **RS-P9-007:** Signal frequency/BUY count is diagnostic only.
- [ ] **RS-P9-008:** “Newer” model/source is not automatically better; paired
  evidence decides.
- [ ] **RS-P9-009:** A safety GO for C7/C8 cannot be presented as return
  superiority.
- [ ] **RS-P9-010:** A GO still requires explicit promotion approval.

---

## Phase 10 — One-component canary and promotion

**Objective:** introduce one approved component reversibly while preserving the
previous control.

### Approval and release preparation

- [ ] **RS-P10-001 — APPROVAL:** Obtain explicit approval for the named component
  and frozen GO report.
- [ ] **RS-P10-002:** Freeze rollback commit/content manifest and previous
  production control.
- [ ] **RS-P10-003:** Add a default-off feature flag scoped only to the approved
  component.
- [ ] **RS-P10-004:** Dual-write old and new decisions with exact reason/probability
  differences.
- [ ] **RS-P10-005:** Define small canary scope, duration, users/universe, safety
  limits, and automatic rollback before enabling.
- [ ] **RS-P10-006:** Confirm hard gates remain authoritative and no unrelated
  threshold changed.

### Canary monitoring

- [ ] **RS-P10-007:** Monitor lineage, opportunity-set parity, calibration,
  false-promotion count, drawdown, source freshness, latency, and failure rate.
- [ ] **RS-P10-008:** Automatically disable on lineage failure, hard-gate false
  promotion, calibration breach, drawdown breach, or source-expiry violation.
- [ ] **RS-P10-009:** Retain dual-write evidence after rollback.
- [ ] **RS-P10-010 — APPROVAL:** Require a separate explicit approval before
  expanding beyond canary.

### Sequential control rule

- [ ] **RS-P10-011:** After promotion, freeze a new control manifest containing
  only the approved change.
- [ ] **RS-P10-012:** Re-evaluate the next challenger against this new control;
  never promote two components from one confounded comparison.

---

## Phase 11 — Ongoing monitoring and research hygiene

- [ ] **RS-P11-001:** Monitor reliability/Brier/ECE/slope/intercept by approved
  cohorts and distribution shift.
- [ ] **RS-P11-002:** Monitor risk–coverage, abstention, hard rejects, false
  promotions, realized versus predicted net R, drawdown, turnover, exposure,
  costs, and source freshness.
- [ ] **RS-P11-003:** Freeze retraining/recalibration triggers and require a new
  protocol for material model/feature/threshold changes.
- [ ] **RS-P11-004:** Keep trial registry append-only across failed and successful
  experiments.
- [ ] **RS-P11-005:** Refresh time-sensitive market/rate/MSCI/source records by
  expiry rather than manual “current” constants.
- [ ] **RS-P11-006:** Capture commands, config revisions, immutable inputs/hashes,
  and output paths for old empirical reports.
- [ ] **RS-P11-007 — APPROVAL:** Archive old research snapshots only after that
  metadata is complete; add a one-line pointer to the ledger. Do not delete.
- [ ] **RS-P11-008:** Keep this checklist, ledger, redesign, and shadow protocol
  as the current top-level research authority.

---

## 5. Component readiness and expected evidence horizon

| Component | Engineering readiness | Earliest honest evidence horizon | Current blocker |
|---|---|---:|---|
| C1 calibrated recommender | Partial forecast foundation | 4–12+ months | Paired 15-day protocol, held-out calibration, C6 |
| C2 discount rate | Control plumbing only | 6–12+ months | Point-in-time source decomposition and expiry |
| C3 finance sentiment | General baseline only | 3–8+ months | Benchmark, annotation, calibration, outcome pairs |
| C4a regime stability | HMM control exists | 2–3+ years | Challenger, all states, five transitions, 504 OOS days |
| C4b1 foreign flow | Interface only | 12+ months | Authoritative PIT source and runtime wiring |
| C4b2 MSCI state | Hard-coded control | 12+ months or longer | Two official source-state vintages |
| C5 IDX4 factors | Characteristics only | 3–5+ years | PIT coverage, portfolios, monthly cross-sections |
| C6 DSR governance | Duplicate partial code | Weeks for formula; component-dependent for outcomes | Canonicalization, registry, HAC, fixtures, Monte Carlo |
| C7 liquidity abstention | Partial fail-closed switch | 6–12+ months | Correct abstain semantic and affected events |
| C8 exemption removal | Reachable branch | 6–12+ months | Reachability/prevalence audit and matched events |

These durations may overlap because collection can run in parallel. They do not
justify parallel promotion.

## 6. Verification matrix for every code phase

| Verification layer | Minimum evidence |
|---|---|
| Static | Touched-file Ruff plus `py_compile`; no new lint/type import errors |
| Unit | Pure schema/math/source/parser/calibration tests |
| Property | Authority flags, C7 missingness domain, C8 bypass invariants, probability sum, hash/tamper cases |
| Integration | Orchestrator → artifact → API/CLI/report parity with control unchanged |
| Replay | Frozen content-hash replay; deterministic paired decisions and idempotent maturity |
| Statistical | Analytic/reference fixtures, confidence intervals, calibration, dependence-adjusted sample, multiplicity |
| Provenance | Snapshot/source/as-of/expiry/hash and no-future-data checks |
| Negative path | Missing/stale/corrupt sources, invalid geometry, no outcomes, undefined metrics, provider failure |
| Full suite | Entire pytest suite after each phase that changes shared contracts or orchestration |

Recommended focused suites include:

- `tests/test_recommendation_context.py`
- `tests/test_signal_packet.py`
- `tests/test_artifact_validator.py`
- `tests/test_risk_governor.py`
- `tests/test_decision_contract.py`
- `tests/test_orchestrator_quality_gates.py`
- `tests/test_result_adapter.py`
- `tests/test_report_formatter.py`
- `tests/test_debate_chamber_reliability.py`
- forecasting dataset/service/validation/shadow suites
- backtester/DSR/trial-registry suites
- regime HMM/gate/execution-regime suites
- fundamental-factor/quant-filter point-in-time coverage suites

## 7. Approval gates

| Gate | Approval authorizes | It does not authorize |
|---|---|---|
| A0 — Baseline/manifest | Build instrumentation and the named challenger | Run collection, change live decisions |
| A1 — Start shadow | Collect paper observations for one protocol | Unblind early, tune, promote |
| A2 — Terminal review | Unblind and independently evaluate | Automatic GO or code promotion |
| A3 — Canary | Enable one GO component in small default-off scope | Full rollout or other components |
| A4 — Production | Expand the named component after canary | Lower unrelated thresholds or combine challengers |
| A5 — Archive | Move documented historical snapshots with pointers | Delete evidence |

## 8. Per-session execution log

Add one row after every implementation or validation session. Use immutable
artifact paths, not mutable aliases.

| Date | Phase / IDs | Status | Files changed | Verification | Artifact / protocol ID | Next exact ID |
|---|---|---|---|---|---|---|
| 2026-07-17 | Phase 1 / RS-P1-001…010 | DONE | Recommendation context, persistence, API/CLI/report/validator surfaces | Reported full suite: 1650 passed, 3 skipped; re-freeze under Phase 0 before new collection | `recommendation-context-v1` | RS-P0-001 |
| 2026-07-17 | Phase 0 / RS-P0-001…017 | BASELINE CAPTURED; HARD STOP | No source, threshold, schema, service, or live-authority edits; one read-only manifest added | Focused 113 passed; non-model 1605 passed/3 skipped; full 1650 passed/3 skipped with workspace SSL/TEMP workaround; Ruff and 126-file py_compile passed | `BASELINE_CONTROL_MANIFEST_2026-07-17.json`, `RS-CONTROL-20260717-01` | User approval of P0-009/P0-013, then RS-P2-001 |
| 2026-07-17 | Phase 2 / RS-P2-001…007 | CONTRACTS DONE; HARD STOP | Added isolated `core/shadow_protocol/` contracts and `tests/test_shadow_protocol.py`; no adapter, writer, engine, threshold, or live-authority change | Contract 20 passed; boundary 293 passed; full 1670 passed/3 skipped; Ruff and py_compile passed | `shadow-protocol-manifest-v1`; no collection protocol instantiated | RS-P2-008 (build only), then approval A1 before any collection |
| 2026-07-17 | Phase 2 / RS-P2-008…013 | ENGINE/EVIDENCE DONE; HARD STOP | Added raw-first paired evidence, parity/lineage reconstruction, causal maturation, corporate-action handling, deterministic replay, and immutable backfill inside isolated `core/shadow_protocol/`; updated protocol/tests only | Focused 56 passed; cross-boundary 403 passed/1 skipped; full 1706 passed/3 skipped; Ruff and py_compile passed | No protocol instantiated; raw n=0, independent n=0, mature n=0 | RS-P2-014 build only after explicit approval; A1 required before collection |
| 2026-07-18 | Phase 2 / RS-P2-014 | DONE; A1 CAPABILITY REMAINS CLOSED | Added evaluation-only frozen portfolio policy/source/state, manifest-v2 binding, immutable lineage/storage, paired candidate producer, governance reloads, exports, tests, and evidence; no legacy evaluator or live path changed | Focused 159 passed; full 1809 passed/3 skipped; py_compile, repo-wide Ruff (no edits), and lock check passed | Design commit `2b40802`; no component manifest/A1/cohort; raw n=0, independent n=0, mature n=0 | RS-P2-015 under separate approval |
| 2026-07-18 | Phase 2 / RS-P2-015 | DONE; A1 CAPABILITY REMAINS CLOSED | Added exact integer-IDR fixed-notional policy/input/lifecycles, causal liquidity and bars, primary holding/cash-flow records, deterministic semantic replay, immutable graph references/lineage, additive governance reload, exports, tests, and evidence; no live path changed | RS-P2-015 57 passed; focused shadow 216 passed; full 1866 passed/3 skipped; repo-wide Ruff no edits; lock check passed | No real component manifest/A1/cohort; synthetic test fixtures only; implementation commit `6e459c8`; documentation commit reported in the external handoff | RS-P2-016 only under separate approval |
| 2026-07-18 | Audit-only status reconciliation / RS-P2-019, 021, 022, 025; RS-P0-009; RS-P1-R01…R05 | RS-P2-025 DONE; RS-P2-019/021/022 PARTIAL; HARD STOP | Documentation/checklist/ledger only; no source or test changed. Added family coverage matrix, SOLO_SELF_REVIEW alignment, build-only phase-order exception, C8 inconsistency record, and recurring-evidence backfills | Full 1866 passed/3 skipped; repo-wide Ruff passed with no edits; all 10 `core/shadow_protocol/*` hashes byte-identical | `SHADOW_STATUS_RECONCILIATION_2026-07-18.md`; no protocol/A1/cohort; raw n=0, independent n=0, mature n=0 | RS-P2-016 only under separate approval |
| 2026-07-19 | Phase 2 / RS-P2-016 | DONE; A1 CAPABILITY REMAINS CLOSED; HARD STOP | Added the isolated evaluation-only policy-portfolio engine, identical genesis, independently evolving paired paths, exact integer-IDR session state/journal, point-in-time gates and fill rechecks, T+2 payable/receivable accounting, fail-closed terminal/path rules, immutable store/lineage, additive exports, tests, design evidence, and checklist status; no live path changed | RS-P2-016 45 passed; focused six-file shadow 261 passed; full 1911 passed/3 skipped outside sandbox after the initial sandbox run hit seven certifi PermissionErrors; Ruff --fix passed with no edits; lock check resolved 185 | No real component manifest/A1/cohort or collection; feature commit `9638762998626a372cf6e33851f4423937dd9584`; PP1–PP14 and PP-A1/PP-N1–PP-N3 frozen | RS-P2-017 under separate approval, then RS-P2-018 and remaining RS-P2-019/021/022 family gaps |

| 2026-07-19 | Phase 2 / RS-P2-017 | DONE; A1 CAPABILITY REMAINS CLOSED; HARD STOP | Added two non-interchangeable evaluation-only daily-NAV families, exact official mark input, integer-IDR policy/sleeve accounting, explicit fixed-sleeve cost liability, T+2 settlement tail, permanent censor/null propagation, daily return series, immutable local graph storage/replay, tests, design evidence, and PP1 wording erratum; no live path changed | RS-P2-017 28 passed; focused shadow 289 passed; authorized unsandboxed full suite 1939 passed/3 skipped; Ruff --fix no edits; lock check resolved 185 | No real component manifest/A1/cohort or collection; feature commit `d5ae02fddbb4ba070857e4d6281b2d33afe14b6d`; snapshots remain `UNANCHORED_NOT_CERTIFIED_COMPLETE` | RS-P2-018 under separate approval, plus remaining global RS-P2-019/021/022 gaps |

## 9. Handoff template

Future agents should end each phase with this exact information:

```text
Phase / checklist IDs completed:
Control content manifest:
Protocol ID:
Files changed:
Thresholds or live authority changed: YES/NO (must explain YES)
Focused tests:
Full-suite result:
Immutable artifact paths and hashes:
Sample status: raw n / independent n / mature n:
GO / CONTINUE / NO-GO status:
Immediate stop condition triggered: YES/NO
Next unblocked checklist ID:
Approval required before next action:
```

## 10. Final completion criterion

The project becomes a recommendation system when it can reliably answer, for
every candidate:

- why it is actionable, waiting, rejected, or abstaining;
- how far it is from each relevant gate and what observable event changes it;
- the calibrated target/stop/timeout uncertainty and expected net R for a
  comparable point-in-time cohort;
- the reliability, sample size, source freshness, distribution-shift status,
  and limitations of that estimate;
- while preserving zero hard-gate overrides and reversible one-component
  promotion.

“More BUYs” is not a completion criterion.
