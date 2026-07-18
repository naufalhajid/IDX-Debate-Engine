# RS-P2-014 Portfolio State and Paired Decision View Design

**Status:** APPROVED DESIGN — RS-P2-014 IMPLEMENTATION AUTHORIZED
**Date:** 2026-07-18
**Target:** RS-P2-014 only; RS-P2-015–018 remain unimplemented
**Priority context:** C7 safety challenger first; C1 calibrated recommender later
**Implementation evidence:** This document authorizes implementation but does
not prove completion. Completion is recorded only in
`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md` after code, tests, and every
verification gate pass.

**Design and implementation authority boundary:** additive evaluation-only
contracts, builders, paired-view production, immutable storage/lineage, and
tests for RS-P2-014 are authorized. No component manifest or A1 grant,
collection, unblinding, threshold or decision-logic change, live-path
integration, RS-P2-015–018 evaluator implementation, or change to
`RS-CONTROL-20260717-01` is authorized.

## Executive decision

Adopt **Option 2 at the contract and preregistration level now**:

- freeze fixed notional, starting capital, liquidity/capacity assumptions, and
  portfolio-risk assumptions before any component collection;
- implement only the paired candidate-level decision view in RS-P2-014;
- implement fixed-notional signal isolation in RS-P2-015 before the
  path-dependent policy portfolio in RS-P2-016;
- generate daily marked-to-market NAV in RS-P2-017;
- expose metrics and honest `NOT_ESTIMABLE` states in RS-P2-018.

This is not authorization to implement all five tasks together. It prevents a
one-lot-only RS-P2-014 contract from becoming disposable as soon as C1 needs
economically meaningful portfolio metrics.

The existing `ShadowObservation` should remain the one persistent
candidate-level paired-decision artifact. RS-P2-014 should add a
dereferenceable, immutable `FrozenPortfolioPolicy` and `PortfolioState`, then
make the observation producer and lineage verifier load and validate the exact
state named by `ShadowObservation.portfolio_state_sha256`. A second competing
paired-decision artifact would create two sources of truth and is not proposed.

Manifest v2 is sufficient. The portfolio policy can be a strict,
machine-readable CONFIG artifact whose exact hash is present in both manifest
content-hash sets; its scalar decisions are also queryable as named
`FrozenParameter`s, its data inputs are declared in `sources`, its costs remain
in `costs`, and its formulas are bound by the methodology-document hash. A
component-specific fail-closed preflight must enforce that profile. No manifest
v3 field is required by this design.

### Approved owner decisions — V2, hybrid arithmetic, and N1–N3

The owner approved this normalized, evaluation-only paper policy:

- starting capital: Rp100,000,000;
- fixed notional: Rp13,000,000;
- liquidity: mean daily `close × volume` over the last 20 completed IDX
  sessions;
- minimum ADTV: Rp10,000,000,000;
- maximum participation: `0.0013` (0.13%);
- target deployment sizing basis: `0.65`;
- minimum cash reserve: `0.05`;
- hard gross-exposure ceiling: `0.95`;
- base maximum positions: 5, with BULL/SIDEWAYS/BEAR_STRESS/UNKNOWN limits
  `3/2/1/0`;
- total loss budget: `0.02`;
- portfolio heat cap: `0.013`;
- daily realized-loss stop: `0.03`;
- lot size: 100 shares;
- settlement lag: two completed IDX sessions;
- sector-exposure fraction: explicitly absent/`NOT_ESTIMABLE`; the existing
  two-name sector and cluster count limits remain separate concepts; and
- true NAV-drawdown gate: explicitly absent/`NOT_ESTIMABLE` pending
  RS-P2-017.

All persisted and hash-bound money state is strict integer IDR. Ratios remain
finite floats quantized to 12 decimal places with `ROUND_HALF_EVEN` before
serialization and hashing. The only permitted rounding of money is application
of aggregated applicable bps to integer notional, rounded once with `CEILING`
against the portfolio.

**N1 — derived liquidity cap.** The `0.0013` participation cap is frozen with
the evidence label `DERIVED_NOT_CALIBRATED`. It is the mechanical ratio
`13_000_000 / 10_000_000_000`, not an empirical market-impact calibration.

**N2 — sizing basis versus achievable deployment.** The 65% target is a
`SIZING_BASIS`, not promised utilization. One fixed notional is 13% of starting
capital; under the largest approved active-regime limit of three positions,
the fixed-notional view can deploy at most Rp39,000,000, or 39%, before costs.
Later NAV review must not report this difference as an anomaly.

**N3 — drawdown boundary.** `MAX_30D_DRAWDOWN` remains a closed-trade
average-P&L control input and is never renamed, copied, or reused as NAV
drawdown. A true NAV-drawdown gate may be introduced only under a new protocol
after RS-P2-017 produces a real daily-NAV series.

## 0. Pre-RS-P2-014 design-pass confirmation

### 0.1 Historical scoped Git state and commit identity

At the pre-implementation checkpoint, the following scope was clean and
tracked:

```text
core/shadow_protocol/
tests/test_shadow_protocol.py
tests/test_shadow_protocol_p2.py
tests/test_shadow_protocol_governance.py
```

The latest commit touching that scope is:

```text
full commit : efaf3d9429a479316427d1ee87d1f36b1711b76b
short       : efaf3d9
committed   : 2026-07-18T03:46:03+07:00
subject     : feat(shadow): shadow protocol substrate RS-P2-001..013 + solo governance v2
```

The repository HEAD at verification time was
`e8b74beac3853214b01cac7a07d3b01b5c68fe3a`; this is later than the scoped
shadow-protocol commit. Nine scoped files are tracked and scoped
`git status --short` returned no entries. Git also emitted its existing warning
that the global ignore file under the user profile was inaccessible; that
warning did not change the scoped status result.

### 0.2 Pre-implementation reference SHA-256

All five hashes matched the prior pass report byte-for-byte before
RS-P2-014. They are comparison anchors, not claims about post-implementation
bytes. Every file changed by the implementation pass receives a new hash in
the implementation report.

| File | Recomputed SHA-256 | Result |
|---|---|---|
| `core/shadow_protocol/contracts.py` | `87b605fb9cc3cb3bee73d903110801699e06e63f4d41e9e8b94cdd48d0ee54b7` | MATCH |
| `core/shadow_protocol/calendar.py` | `fe27a4e5c964c26f3093921193f29ec45f4f4c09f620b52ca94806ab302c7151` | MATCH |
| `core/shadow_protocol/governance.py` | `95410bf4e136f2bd000fadf51a3f0b97931669e9fdde7913013cab853da8d8eb` | MATCH |
| `core/shadow_protocol/outcome_engine.py` | `b90d149df67d91f59408618e580c75f55d2de2257cf6e5f46f4265dffaaa27a8` | MATCH |
| `core/shadow_protocol/__init__.py` | `3988903cf50514638d612a0f562c6df7065fa4cb1eb6386c1de3b7a2fb6d17cd` | MATCH |

## 1. Normative boundary and pre-implementation gap

### 1.1 What the protocol requires

The control and challenger must process the same point-in-time opportunity set,
data vintages, timestamps, costs, and labels in parallel; the challenger never
overrides the control in shadow mode
(`SHADOW_MODE_PROTOCOL.md:11-13`).

The protocol requires three distinct views:

1. candidate-level decisions against the frozen control portfolio state;
2. identical fixed-notional evaluation to isolate signal quality; and
3. independent, path-dependent control and challenger policy portfolios
   starting from identical capital and risk/cost rules
   (`SHADOW_MODE_PROTOCOL.md:97-101`).

Drawdown, volatility, DSR inputs, exposure, and turnover must come from daily
marked-to-market NAV rather than a sequence of closed trades
(`SHADOW_MODE_PROTOCOL.md:103`). Undefined metrics must remain
`NOT_ESTIMABLE`, never zero (`SHADOW_MODE_PROTOCOL.md:107-109`).

The master checklist maps those requirements to RS-P2-014–018
(`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:404-415`). Phase 2 is the common
substrate required before C1–C8 collection, while C7/C8 run after Phase 2 and C1
comes after Phase 2 plus its data readiness
(`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:104-116`).

### 1.2 Pre-implementation substrate

At the design checkpoint, the isolated evaluator intentionally used:

- one frozen exchange lot;
- source-supplied prices without engine-side tick rounding; and
- no liquidity model.

Those limitations must be replaced or explicitly retained in a new manifest
before fixed-notional or portfolio collection starts
(`SHADOW_MODE_PROTOCOL.md:52-54`;
`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:372-375`).

The code at that checkpoint already provided most of the paired-record shell:

- strict frozen models reject unknown fields, mutation, and non-finite floats
  (`core/shadow_protocol/contracts.py:169-188`);
- `ShadowDecision` represents one CONTROL or CHALLENGER decision and
  distinguishes `CONTROL_OBSERVED` from `COUNTERFACTUAL` sizing
  (`core/shadow_protocol/contracts.py:899-936`);
- `ShadowObservation` contains one opaque `portfolio_state_sha256` plus both
  decisions (`core/shadow_protocol/contracts.py:1076-1102`);
- its validator checks roles, chronology, cluster identity, and recomputed
  divergence (`core/shadow_protocol/contracts.py:1104-1143`);
- tests currently supply a dummy portfolio-state hash rather than a real state
  artifact (`tests/test_shadow_protocol.py:272-303`).

The implementation gap identified at that checkpoint was therefore not another
decision record. It was the absence of a `PortfolioState` model, producer,
store, loader, and dereferenced lineage edge. This paragraph is historical
design evidence; implementation completion is recorded only in the master
checklist after every gate passes.

## Part 1 — Q1 decision brief

## 2. Option comparison

### 2.1 Option definitions

**Option 1 — retain the current evaluation semantics**

```text
one exchange lot
source-supplied price
no liquidity/capacity model
no defined starting capital
no policy-portfolio path
```

This option is protocol-legal only if it is explicitly retained in a new
manifest (`SHADOW_MODE_PROTOCOL.md:54`). That permission does not make one lot
equivalent to fixed notional or a portfolio.

**Option 2 — define the future-complete assumptions now**

```text
fixed notional
starting capital
liquidity source and participation/capacity rules
portfolio-risk and cash rules
marking and NAV rules
```

The assumptions are frozen now, but evaluator activation remains staged.
Merely carrying a future-use field must never make a metric estimable or a
checklist item complete.

### 2.2 Concrete impact by task

| Task | Option 1 — retain one lot | Option 2 — freeze assumptions now |
|---|---|---|
| **RS-P2-014** | Can record gate, reason, actionability, rank, and divergence against a read-only control snapshot. Position fraction and portfolio-risk measurements lack a reproducible capital denominator. This is only a partial foundation. | The same state binds cash, NAV, positions, heat, exposure, policy, source, and capacity assumptions. Both sides receive one exact state hash; decision output can be reproduced without activating later evaluators. |
| **RS-P2-015** | Cannot satisfy “identical fixed-notional.” One lot has different rupiah value for every ticker. Marking the task done would be false. | Uses the already frozen `fixed_notional_idr`, deterministic lot rounding, costs, and capacity rule. It can isolate signal quality without redefining the contract. |
| **RS-P2-016** | Cannot form independent path-dependent portfolios without starting capital, cash reservation, simultaneous-order priority, and risk/cost rules. | Evolves control and challenger independently from identical genesis capital and identical policy. Only their decisions produce divergence. |
| **RS-P2-017** | One-lot P&L is not NAV. There is no cash ledger, capital denominator, exposure, or portfolio path. Normalizing it later would manufacture a return series. | Marks cash and open positions daily using the frozen source/rule, producing meaningful NAV and returns. |
| **RS-P2-018** | Candidate/event metrics can be reported, but portfolio metrics must remain `NOT_ESTIMABLE`. | Portfolio metrics become structurally estimable after RS-P2-016/017 and sufficient maturity. Statistical insufficiency can still require `NOT_ESTIMABLE`. |

The task wording itself is at
`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:406-415`. The three-view distinction
and daily-NAV requirement are at `SHADOW_MODE_PROTOCOL.md:97-103`.

### 2.3 Metric estimability matrix

| Metric family | Option 1 | Option 2 after the responsible task | Reason |
|---|---|---|---|
| Paired state/reason/actionability divergence | ESTIMABLE | ESTIMABLE after RS-P2-014 | Candidate-level decision artifact does not require a portfolio return series. |
| Coverage, abstention, hard reject, false promotion | ESTIMABLE | ESTIMABLE after RS-P2-014 | These are system-behavior metrics required by `SHADOW_MODE_PROTOCOL.md:135-143`. |
| Target-before-stop, fill, net return, net R under one-lot convention | ESTIMABLE under that limited estimand | ESTIMABLE | These are event-level outcomes, but Option 1 must be labeled one-lot. |
| Identical fixed-notional P&L/return | `NOT_ESTIMABLE` | ESTIMABLE after RS-P2-015 | A fixed quantity is not a fixed notional. |
| Policy-portfolio return/NAV | `NOT_ESTIMABLE` | ESTIMABLE after RS-P2-016/017 | Requires capital, cash, allocation, and path evolution. |
| Chronological portfolio drawdown | `NOT_ESTIMABLE` | ESTIMABLE after RS-P2-017 | Must use daily MTM NAV (`SHADOW_MODE_PROTOCOL.md:103`). |
| Portfolio volatility/downside deviation | `NOT_ESTIMABLE` | ESTIMABLE after RS-P2-017 | Closed trades cannot substitute for daily returns (`SHADOW_MODE_PROTOCOL.md:103,117-122`). |
| Exposure, turnover, capital utilization, portfolio heat | `NOT_ESTIMABLE` | ESTIMABLE after RS-P2-016/017 | Requires capital and daily holdings/cash state. |
| Capacity-aware opportunity cost | `NOT_ESTIMABLE` beyond one-lot approximation | ESTIMABLE after RS-P2-015/016 | Requires liquidity/capacity and the action that displaced or consumed capital. |
| Canonical control/challenger/paired DSR | `NOT_ESTIMABLE` | Structurally estimable after RS-P2-017; promotion-grade only after C6 | DSR uses daily MTM returns and C6 governance (`SHADOW_MODE_PROTOCOL.md:119,156-167,358-388`). |
| C7 missingness incidence, false-missing rate, recovery, abstention enforcement | STRUCTURALLY ESTIMABLE only after C7 freezes its taxonomy/source precedence, implements the challenger, and collects affected evidence; not dependent on policy NAV | Same | These are C7’s safety estimands (`SHADOW_MODE_PROTOCOL.md:390-411`; `RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:542-565`). |

“Structurally estimable” does not mean “statistically sufficient.” Insufficient
sample, immature outcomes, an underpowered slice, an incomplete C6 gate, or an
undefined PBO still requires `NOT_ESTIMABLE`/`CONTINUE`; no value may be
coerced to zero (`SHADOW_MODE_PROTOCOL.md:69-73,107-122,160-167,350`).

## 3. Effect on C7 and C1

### 3.1 C7 safety challenger

Option 1’s missing portfolio DSR/drawdown does **not**, by itself, block C7
`SAFETY_GO`. C7/C8 require formal/property evidence, at least 30
dependence-adjusted independent affected events/clusters (with C7 targeting at
least 60), 100% enforcement, zero false blocking outside scope, and
opportunity-cost/DSR/drawdown reporting only where estimable. They may make no
return-superiority claim
(`SHADOW_MODE_PROTOCOL.md:149-150,173-177,394-398`). The C7-specific gate
repeats that DSR, drawdown, and opportunity cost are not prerequisites for
fail-closed safety GO (`SHADOW_MODE_PROTOCOL.md:400-411`).

This statistical exception does not waive the engineering dependency. The
checklist still places C7 after the common Phase-2 substrate
(`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:104-114,535-540`) and requires C7
to keep a separate protocol, paired records, unchanged control output, full
property coverage, and only `SAFETY_GO`, `CONTINUE`, or `NO_GO`
(`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:598-603`).

Practical conclusion: only after the full Phase-2 substrate is complete and a
C7 manifest has its own A1 may C7 collect candidate-level safety evidence while
return outcomes/metrics remain statistically immature. Option 1 is not a reason
to collect early or falsely close RS-P2-015–017.

### 3.2 C1 calibrated recommender

Option 1 blocks C1 **GO**, although it need not block preliminary C1 design or
model work:

- the outcome-changing gate requires control, challenger, and paired daily-MTM
  DSR, plus a drawdown non-inferiority bound
  (`SHADOW_MODE_PROTOCOL.md:156-165`);
- if DSR is not estimable, an outcome-changing component cannot GO
  (`SHADOW_MODE_PROTOCOL.md:167`);
- C1’s primary metrics include counterfactual top-k return, corrected DSR, and
  maximum drawdown (`SHADOW_MODE_PROTOCOL.md:193-197`);
- the checklist requires C6, challenger DSR at least `.95`, the incremental
  net-R bound, and drawdown non-inferiority
  (`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:771-785`).

Brier score, ECE, calibration slope/intercept, and risk-coverage could be
computed without a full policy portfolio, but passing those alone cannot
replace the missing portfolio gate.

## 4. Migration cost: Option 1 now, Option 2 later

### 4.1 Before collection-start authorization

If no manifest has been frozen or approved, migration is still engineering
work: expand the state contract, fixtures, producer, store, lineage, and replay
tests.

If an Option-1 DRAFT has already been frozen, changing notional/capital/
liquidity/risk assumptions changes the canonical and likely raw manifest hash.
At minimum it requires a new manifest revision, fresh hash pair, fresh
self-review, cooling-off, and a new A1 ApprovalRecord. Whether the same
protocol ID may be retained depends on the four lifecycle states in Section 12,
not merely whether observed `n` is still zero.

### 4.2 After collection starts

The rule is unambiguous: a material post-start change creates a new protocol ID
and new trial and cannot overwrite the original
(`SHADOW_MODE_PROTOCOL.md:15-28`).

Moving from one lot/no capital/no liquidity to fixed notional plus a portfolio
changes the estimand, scaling, fill capacity, cash path, and risk opportunity
set. Therefore:

- the Option-1 cohort remains immutable sensitivity evidence;
- it is not pooled into the Option-2 prospective C1 cohort;
- raw candidates, snapshots, bars, and corporate-action artifacts may be
  replay inputs if their lineage remains valid, but that replay is engineering/
  sensitivity evidence only and cannot count as untouched prospective C1
  evidence under the frozen train/calibration/test separation
  (`SHADOW_MODE_PROTOCOL.md:30-38`);
- derived one-lot returns cannot be relabeled fixed-notional or NAV returns;
- a narrow Option-1-only state schema would require an explicit
  compatible-version-versus-new-family decision, loaders, and migration tests.

Versioned immutable paths and replay/idempotency tests are already required by
RS-P2-019 and RS-P2-022
(`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:419-426`).

### 4.3 Decision

Choose Option 2 now, with this staged activation:

```text
RS-P2-014  freeze policy + persist state + paired decision observation
    ↓
RS-P2-015  activate identical fixed-notional evaluator
    ↓
RS-P2-016  activate independent path-dependent policy portfolios
    ↓
RS-P2-017  generate daily marked-to-market NAV
    ↓
RS-P2-018  compute common metrics with honest NOT_ESTIMABLE states
```

This order serves C7 first without pretending C7 is a return-superiority trial,
and it avoids rebuilding the substrate before C1.

## Part 2 — PortfolioState and paired-view contract design

## 5. Design principles

1. **One paired-decision source of truth.** `ShadowObservation` remains the
   persistent candidate-level paired view.
2. **One frozen reference state per RS-P2-014 batch.** Every candidate in the
   same simultaneous batch receives the same pre-decision control-state hash.
   Later path-dependent roles form separate predecessor chains; they do not
   mutate this reference state.
3. **No state mutation during decision view.** RS-P2-014 observes decisions; it
   does not reserve cash, evolve a paper portfolio, or create orders.
4. **Future-complete policy, honest capability tracking.** Fields for
   RS-P2-015–017 exist now, while implementation capability remains external
   evidence in checklist/test/build artifacts. Mutable build status is not
   mixed into the scientific policy hash.
5. **Hash the model, do not self-reference.** Policy/state identity is the
   existing canonical SHA-256 of the validated model. No object embeds its own
   top-level hash.
6. **Persist before reference.** Policy and source record precede state; state
   precedes observation; observation precedes outcome.
7. **Fail closed on missing evidence.** An opaque hash with no loadable artifact
   is not valid lineage.
8. **No live portfolio dependency.** The default scientific state is a
   simulated paper-control portfolio. Importing the owner’s actual holdings
   would create a different estimand and requires an explicit future decision.

These choices implement the same-state paired requirement in
`SHADOW_MODE_PROTOCOL.md:88-105` while retaining the control as the only live
authority (`SHADOW_MODE_PROTOCOL.md:105`).

## 6. `FrozenPortfolioPolicy` contract

`FrozenPortfolioPolicy` is a static, machine-readable, pre-A1 CONFIG artifact.
It freezes assumptions that would otherwise be hidden in prose.

All fields use the existing strict frozen conventions: unknown fields rejected,
immutable instances, validated defaults, stripped strings, and non-finite
floats rejected (`core/shadow_protocol/contracts.py:169-178`). Policy cap
fractions are finite values in `[0,1]`; observed ratios use field-specific
ranges so a breached cap is recorded rather than rejected. All persisted,
hash-bound Rupiah values are strict integers; booleans, floats, numeric strings,
and implicit coercion are rejected. Starting capital and fixed notional are
strictly positive.

### 6.1 Identity and scope

| Field | Type / constraint | Purpose |
|---|---|---|
| `contract_version` | literal `shadow-portfolio-policy-v1` | Prevent silent reinterpretation. |
| `binding_profile` | literal `portfolio-binding-v1` | Reserved semantic preflight; removing it while portfolio markers remain fails closed. |
| `policy_id` | non-empty string | Human-readable immutable identity. |
| `state_origin` | literal `SIMULATED_PAPER_CONTROL` | Never imports or claims the owner's live holdings. |
| `currency` | literal `IDR` | Prevent mixed-currency arithmetic. |
| `policy_scope` | literal `DECISION_FIXED_NOTIONAL_POLICY_NAV_ASSUMPTIONS` | States that assumptions cover all three views without claiming their implementation exists. |
| `phase2_capability_status` | literal `RS_P2_014_ONLY_NOT_A1_ELIGIBLE` | Machine-enforced A1 block until RS-P2-015–025 evidence exists. |

Scientific assumptions and implementation capability remain separate concepts.
The policy carries one narrow, fail-closed capability literal so RS-P2-014
field presence cannot accidentally grant A1. Completing later Phase-2 tasks
therefore requires a new policy/manifest revision and hashes rather than
silently reinterpreting this artifact:

- the master checklist, capability registry, implementation content hashes, and
  tests establish which evaluator exists;
- `RS_P2_014_ONLY_NOT_A1_ELIGIBLE` makes `append_approval` and authorization
  reload fail before any profile collection can begin;
- no real component portfolio policy, manifest A1, or collection may be frozen
  until the complete Phase-2 substrate is ready; and
- field presence is never treated as implementation evidence.

### 6.2 Capital, notional, and cash

| Field | Type / constraint | Purpose |
|---|---|---|
| `starting_capital_idr` | strict integer `100_000_000` | Common genesis denominator for policy portfolios. |
| `fixed_notional_idr` | strict integer `13_000_000` | Identical signal-isolation notional. |
| `fixed_notional_fraction` | quantized ratio `0.13` | Exact notional/start-capital ratio. |
| `target_deployment_fraction` | quantized ratio `0.65` | `SIZING_BASIS`, not guaranteed utilization. |
| `target_deployment_semantics` | literal `SIZING_BASIS` | Prevent 65% from being reported as achievable fixed-notional deployment. |
| `effective_fixed_notional_max_positions` | strict integer `3` | Largest approved active-regime concurrency. |
| `effective_fixed_notional_max_deployment_fraction` | derived quantized ratio `0.39` | Must equal `3 × 13_000_000 / 100_000_000`; not an independent threshold. |
| `lot_size_shares` | strict integer `100` | Must equal `manifest.costs.lot_size`. |
| `fixed_notional_rounding_rule` | literal `FLOOR_TO_WHOLE_BOARD_LOTS_WITHOUT_EXCEEDING_NOTIONAL` | Deterministic conversion from rupiah to lots. |
| `insufficient_notional_rule` | literal `NOT_ESTIMABLE` | Behavior when one lot exceeds the target notional. |
| `money_storage_rule` | literal `INTEGER_IDR_EXACT` | Money identity uses exact integer equality. |
| `cost_application_rounding_rule` | literal `AGGREGATE_APPLICABLE_BPS_THEN_CEIL_INTEGER_IDR` | The only permitted money-rounding point. |
| `ratio_quantization_decimal_places` | literal `12` | Frozen scale before float serialization/hashing. |
| `ratio_quantization_rounding_mode` | literal `ROUND_HALF_EVEN` | Frozen deterministic ratio rounding mode. |
| `cash_reservation_rule` | literal `READ_ONLY_NO_NEW_RESERVATION` | RS-P2-014 never creates a new reservation. |
| `unsettled_cash_rule` | literal `NOT_DEPLOYABLE_UNTIL_SETTLED` | T+ receivable is separate and not deployable. |
| `settlement_lag_sessions` | strict integer `2` | Frozen T+2 settlement assumption. |
| `minimum_cash_reserve_fraction` | quantized ratio `0.05` | Cash that cannot be deployed. |
| `cash_reserve_denominator` | literal `STARTING_CAPITAL` | Exact reserve base. |
| `allocation_fraction_denominator` | literal `STARTING_CAPITAL` | Exact denominator for reported allocation. |
| `realized_loss_denominator` | literal `STARTING_CAPITAL` | Exact base for the daily loss magnitude. |
| `realized_loss_sign_convention` | literal `POSITIVE_LOSS_MAGNITUDE` | Separates signed P&L from a loss fraction. |
| `circuit_breaker_comparison_rule` | literal `GREATER_THAN_OR_EQUAL` | Equality activates the stop. |
| `zero_nav_rule` | literal `RETAIN_INSOLVENCY_AND_MARK_DENOMINATOR_RATIOS_NOT_ESTIMABLE` | Preserves total-loss observations. |
| `leverage_allowed` | literal `false` for v1 | Prevent accidental borrowing. |
| `shorting_allowed` | literal `false` for v1 | Preserve long-only IDX scope. |

Money identities use exact integer equality. Integer addition is order
independent; no frozen summation order or money tolerance participates in
correctness. Multiplication by a policy fraction must already produce an exact
integer IDR result or become `NOT_ESTIMABLE`; it may not introduce another
rounding point.

```text
applied_cost_idr =
    ceil(
        integer_notional_idr
        × sum(applicable_bps)
        / 10_000
    )
```

Applicable bps are converted through exact decimal text, aggregated, and
rounded once with `CEILING`. Buy costs increase cash debit and sell costs
decrease proceeds. Independently ceiling each fee component is forbidden.
Ratios are computed from exact integers with Decimal, quantized to 12 decimal
places using `ROUND_HALF_EVEN`, normalized from negative zero to positive zero,
and only then serialized as finite floats.

### 6.3 Liquidity and capacity

| Field | Type / constraint | Purpose |
|---|---|---|
| `liquidity_source_id` | non-empty string | Must resolve to one manifest `SourceDefinition`. |
| `liquidity_source_definition_sha256` | 64 lowercase hex | Binds the exact source semantics. |
| `liquidity_measure_basis` | literal `MEAN_CLOSE_X_VOLUME_LAST_20_COMPLETED_SESSIONS` | Frozen point-in-time ADTV definition. |
| `liquidity_lookback_sessions` | literal `20` | Completed IDX-session window. |
| `minimum_adt_idr` | strict integer `10_000_000_000` | Minimum modeled ADTV. |
| `liquidity_expiry_rule` | frozen string | Staleness boundary. |
| `max_participation_fraction` | literal `0.0013` | Maximum modeled order/ADTV share. |
| `participation_evidence_class` | literal `DERIVED_NOT_CALIBRATED` | N1 provenance; not market-impact calibration. |
| `participation_derivation_numerator_idr` | strict integer `13_000_000` | Mechanical derivation numerator. |
| `participation_derivation_denominator_idr` | strict integer `10_000_000_000` | Mechanical derivation denominator. |
| `capacity_rounding_rule` | literal `FLOOR_TO_WHOLE_BOARD_LOTS` | Deterministic conversion to tradable lots. |
| `unmeasurable_capacity_state` | literal `NOT_ESTIMABLE` | Missing data is not zero capacity or infinite capacity. |

The policy freezes shared input and capacity semantics. Side-specific behavior
for missing liquidity remains in each decision policy: the current control may
exhibit its frozen behavior while the C7 challenger abstains. The same raw
missingness classification and source hash must reach both sides. The 0.13%
cap is an owner-approved normalized capacity assumption labeled
`DERIVED_NOT_CALIBRATED`; reports must never describe it as empirical
market-impact calibration or confuse it with 13%.

### 6.4 Portfolio risk and ordering

| Field | Type / constraint | Purpose |
|---|---|---|
| `base_max_concurrent_positions` | literal `5` | Frozen non-regime base limit. |
| `bull_max_positions` | literal `3` | Frozen BULL limit. |
| `sideways_max_positions` | literal `2` | Frozen SIDEWAYS limit. |
| `bear_stress_max_positions` | literal `1` | Frozen BEAR_STRESS limit. |
| `unknown_max_positions` | literal `0` | Frozen fail-closed UNKNOWN limit. |
| `total_loss_budget_fraction` | literal `0.02` | Total control loss-budget assumption. |
| `max_portfolio_heat_fraction` | literal `0.013` | Aggregate open-risk cap. |
| `portfolio_heat_denominator` | literal `STARTING_CAPITAL` | Defines denominator; cannot drift between views. |
| `max_gross_exposure_fraction` | literal `0.95` | Total long market-value cap. |
| `max_sector_exposure_fraction` | literal `None` | Explicitly `NOT_ESTIMABLE`; two-name count limits remain separate. |
| `sector_max_names` | literal `2` | Existing count limit, not exposure fraction. |
| `cluster_max_names` | literal `2` | Existing count limit, not exposure fraction. |
| `daily_loss_stop_fraction` | literal `0.03` | Frozen daily realized-loss stop. |
| `max_drawdown_stop_fraction` | literal `None` | N3: no true NAV gate exists before RS-P2-017 and a new protocol. |
| `nav_drawdown_gate_status` | literal `NOT_ESTIMABLE_UNTIL_RS_P2_017_NEW_PROTOCOL` | Enforces N3 without reusing closed-trade P&L. |
| `same_timestamp_priority_rule` | literal `SOURCE_ROW_NUMBER_ASC_THEN_TICKER_ASC` | Deterministic batch ordering/tie-break. |
| `partial_fill_rule` | literal `TASK_GATED_TO_RS_P2_015` | Prevents RS-P2-014 from claiming fill logic. |

### 6.5 Price, NAV, and provenance

| Field | Type / constraint | Purpose |
|---|---|---|
| `portfolio_source_id` | non-empty string | Manifest source for frozen cash, holdings, and commitments. |
| `portfolio_source_definition_sha256` | SHA-256 | Exact portfolio-source semantics. |
| `entry_price_source_rule` | literal `SOURCE_SUPPLIED_INTEGER_IDR` | Source/time used for simulated entry. |
| `price_rounding_rule` | frozen string | Must agree with `manifest.costs`. |
| `mark_price_source_id` | non-empty string | Manifest source for daily marks. |
| `mark_price_source_definition_sha256` | SHA-256 | Exact mark-source semantics. |
| `mark_price_rule` | literal `POINT_IN_TIME_SOURCE_INTEGER_IDR` | Frozen point-in-time integer mark. |
| `stale_mark_rule` | literal `NOT_ESTIMABLE` | A stale mark is never silently carried. |
| `fractional_entitlement_rule` | literal `EXACT_INTEGER_CASH_IN_LIEU_OR_NOT_ESTIMABLE` | Corporate-action treatment of fractional shares. |
| `odd_lot_rule` | literal `INTEGER_SHARES_ALLOWED` | Post-action odd lots remain exact integer shares. |
| `cash_in_lieu_rule` | literal `EXACT_INTEGER_IDR_WITH_SOURCE_LINEAGE` | Exact cash-in-lieu evidence or unsupported status. |
| `corporate_action_policy_sha256` | SHA-256 | Must equal the manifest value. |
| `trading_calendar_sha256` | SHA-256 | Must equal the manifest value. |
| `cost_assumptions_sha256` | SHA-256 | Canonical hash of `manifest.costs`. |
| `methodology_document_sha256` | SHA-256 | Must equal the manifest value. |

The policy’s identity is
`canonical_sha256(FrozenPortfolioPolicy)`. It does not contain a
`portfolio_policy_sha256` field, avoiding self-reference.

## 7. `PortfolioState` contract

`PortfolioState` is a versioned state envelope. RS-P2-014 is allowed to produce
only the exact read-only control snapshot immediately before a candidate batch.
The schema reserves explicit path-dependent and daily-mark roles so
RS-P2-016/017 do not require silently redefining v1; those roles remain
unproducible until their own implementations and tests exist.

### 7.1 Artifact and protocol identity

| Field | Type / constraint | Purpose |
|---|---|---|
| authority literals | `evaluation_only=true`, all authority/affect flags `false` | Preserve shadow-only boundary. |
| `contract_version` | literal `shadow-portfolio-state-v1` | Explicit loader family. |
| `portfolio_state_id` | deterministic non-empty string | Stable human/path reference; not the content hash. |
| `state_role` | literal `CONTROL_FROZEN_REFERENCE` | Later path and daily-NAV roles require new task-specific contracts; v1 is never silently widened. |
| `portfolio_path_id` | non-empty string | Distinguishes frozen reference, control policy, and challenger policy paths. |
| `state_sequence` | integer `>=0` | Monotone sequence within one path. |
| `previous_state_sha256` | SHA-256 or `None` | `None` only at a declared genesis; otherwise exact predecessor. |
| `protocol_id` | non-empty string | Exact component protocol. |
| `component_id` | existing component enum | Must equal manifest and candidate. |
| `manifest_contract_version` | literal `shadow-protocol-manifest-v2` | Reject v1 reinterpretation. |
| `manifest_revision` | integer `>=1` | Must equal manifest. |
| `manifest_sha256` | SHA-256 | Canonical manifest identity. |
| `baseline_manifest_id` | non-empty string | Must equal manifest baseline ID. |
| `baseline_manifest_sha256` | SHA-256 | Must equal manifest baseline hash. |

The manifest already contains the baseline ID/hash pair
(`core/shadow_protocol/contracts.py:393-407`). Copying and verifying it in the
state creates a direct replay edge; it does not modify the baseline manifest.

### 7.2 Opportunity and chronology identity

| Field | Type / constraint | Purpose |
|---|---|---|
| `opportunity_set_id` | non-empty string or `None` | Required for candidate-decision roles; absent only for a pure daily mark. |
| `opportunity_set_sha256` | SHA-256 or `None` | Must equal candidate/raw/set artifacts when applicable. |
| `raw_capture_id` | non-empty string or `None` | Required for candidate-decision roles; absent only for a pure daily mark. |
| `raw_capture_sha256` | SHA-256 or `None` | Exact raw capture when applicable. |
| `raw_capture_captured_at` | aware datetime or `None` | Chronology edge when applicable. |
| `candidate_set_id` | non-empty string or `None` | Required for candidate-decision roles; absent only for a pure daily mark. |
| `candidate_set_sha256` | SHA-256 or `None` | Exact candidate-set content when applicable. |
| `candidate_set_captured_at` | aware datetime or `None` | Chronology edge when applicable. |
| `signal_at` | aware datetime or `None` | Required batch decision time for candidate roles; absent for a pure daily mark. |
| `as_of_date` | date | Equals IDX-local `signal_at` date for candidate roles and `state_session_date` for a daily mark. |
| `state_session_date` | date | Trading session represented by path/daily state. |
| `state_as_of` | timezone-aware datetime | Latest fact permitted in state. |
| `captured_at` | timezone-aware datetime | State finalization time. |
| `trading_calendar_id` | non-empty string | Exact calendar identity. |
| `trading_calendar_sha256` | SHA-256 | Must equal manifest/candidate. |

Required chronology for candidate-decision roles:

```text
source_as_of <= state_as_of <= signal_at <= captured_at
portfolio_state.captured_at <= observation.captured_at
every position mark time <= state_as_of
source_expires_at is null or source_expires_at > signal_at
```

For candidate-decision roles, the exact-artifact causal chain is also required:

```text
candidate.captured_at
<= raw_capture.captured_at
<= candidate_set.captured_at
<= portfolio_state.captured_at
<= observation.captured_at
```

For `DAILY_NAV_MARK`, raw/candidate-set fields must all be `None`; the state is
instead bound to `state_session_date`, its predecessor, and exact mark sources.
For every non-genesis path state, `state_sequence` increments by one and
`previous_state_sha256` resolves to an earlier same-path artifact. The producer
may finalize a state after the signal timestamp only from data already known by
`state_as_of`; it may not include either side’s new decision in the
`CONTROL_FROZEN_REFERENCE`.

### 7.3 Policy and source identity

| Field | Type / constraint | Purpose |
|---|---|---|
| `portfolio_policy_id` | non-empty string | Must equal embedded/loaded policy. |
| `portfolio_policy_sha256` | SHA-256 | Canonical hash of the exact policy. |
| `portfolio_policy` | `FrozenPortfolioPolicy` | Self-contained replay copy. |
| `portfolio_source_id` | non-empty string | Source of control cash/positions. |
| `portfolio_source_definition_sha256` | SHA-256 | Exact manifest source definition. |
| `portfolio_source_record_sha256` | SHA-256 | PIT record identity. |
| `portfolio_source_payload_sha256` | SHA-256 | Exact source bytes/payload. |
| `source_as_of` | aware datetime | Vintage clock. |
| `source_expires_at` | aware datetime or `None` | Expiry clock. |

The embedded policy must hash to `portfolio_policy_sha256`, and that hash must
match the pre-A1 CONFIG artifact bound by manifest v2.

### 7.4 Cash, NAV, and risk primitives

Potentially missing numeric state is represented by strict envelopes rather
than a number-or-string union:

```text
EstimableMoney:
  status: ESTIMABLE | NOT_ESTIMABLE
  value_idr: strict integer | null
  reason_codes: tuple[str, ...]

EstimableRatio:
  status: ESTIMABLE | NOT_ESTIMABLE
  value: finite float | null
  reason_codes: tuple[str, ...]

EstimableCount:
  status: ESTIMABLE | NOT_ESTIMABLE
  value: integer >= 0 | null
  reason_codes: tuple[str, ...]
```

`ESTIMABLE` requires a value. `NOT_ESTIMABLE` requires `null` plus at least one
reason code. A real measured zero remains `ESTIMABLE` with value `0`; missing
state can never be encoded as zero. Derived values propagate
`NOT_ESTIMABLE` from any unavailable prerequisite. Each field adds its own
range rule; an observed breach is not rejected merely because it exceeds a
policy cap.

| Field | Type / constraint | Purpose |
|---|---|---|
| `starting_capital_idr` | strict integer `>0` | Must equal policy. |
| `settled_cash` | `EstimableMoney` | Cash economically settled. |
| `unsettled_cash_receivable` | `EstimableMoney` | T+ receivable kept separate. |
| `reserved_cash` | `EstimableMoney` | Pending commitments. |
| `deployable_cash` | `EstimableMoney` | Recomputed, not trusted. |
| `marked_positions_value` | `EstimableMoney` | Sum of position market values. |
| `nav` | `EstimableMoney`, estimable value `>=0` | Recomputed accounting identity; zero NAV is retained as insolvency, not censored. |
| `peak_nav` | `EstimableMoney` | Always `NOT_ESTIMABLE` under RS-P2-014. A true predecessor NAV chain and gate require RS-P2-017 plus a new protocol. |
| `nav_drawdown` | `EstimableRatio` | Always `NOT_ESTIMABLE` under RS-P2-014; it cannot be populated from a closed-trade statistic. |
| `gross_exposure` | `EstimableMoney` | Sum of absolute market values. |
| `net_exposure` | `EstimableMoney` | Signed exposure; equals gross exposure for complete long-only v1 state. |
| `open_positions_count` | `EstimableCount` | Must equal position tuple length when positions are estimable. |
| `open_risk` | `EstimableMoney` | Sum of estimable risk-to-stop cash; otherwise propagates `NOT_ESTIMABLE`. |
| `portfolio_heat` | `EstimableRatio`, estimable value `>=0` | Recomputed only when risk and denominator are estimable; may exceed its cap. |
| `realized_pnl_today` | `EstimableMoney`, signed | Daily circuit-breaker input. |
| `realized_loss_today` | `EstimableRatio`, estimable value `>=0` | Positive loss magnitude recomputed from signed P&L and frozen denominator. |
| `control_30d_closed_trade_avg_pnl` | `EstimableRatio`, signed | Honest name for current control input; never called NAV drawdown. |
| `circuit_breaker_active` | boolean or `None` | `None` is allowed only when listed as missing and must lead to fail-closed decision handling. |
| `portfolio_status` | `ACTIVE`, `INSOLVENT`, or `NOT_ESTIMABLE` | Zero estimable NAV requires `INSOLVENT`; protocol closure remains the separate governance `ClosureRecord`. |
| `portfolio_gate_inputs_complete` | boolean | Completeness indicator. |
| `state_completeness` | `COMPLETE`, `PARTIAL_CONTROL_OBSERVED`, or `NOT_ESTIMABLE` | Prevent missing fields from becoming zero. |
| `missing_state_fields` | sorted unique tuple of names | Required when state is not complete. |

Money arithmetic is recomputed by exact integer equality only when every
prerequisite has `status=ESTIMABLE`. Policy fractions used in money identities
must produce an exact integer result; otherwise the dependent value is
`NOT_ESTIMABLE`, never rounded:

```text
deployable_cash.value_idr =
    max(
        settled_cash.value_idr
        - reserved_cash.value_idr
        - (
            minimum_cash_reserve_fraction
            * value(selected by cash_reserve_denominator)
        ),
        0,
    )

marked_positions_value.value_idr =
    sum(position.market_value.value_idr)

nav.value_idr =
    settled_cash.value_idr
    + unsettled_cash_receivable.value_idr
    + marked_positions_value.value_idr

gross_exposure.value_idr =
    sum(abs(position.market_value.value_idr))

open_risk.value_idr =
    sum(position.risk_to_stop.value_idr)

portfolio_heat.value =
    open_risk.value_idr / frozen_heat_denominator

realized_loss_today.value =
    max(-realized_pnl_today.value_idr, 0)
    / value(selected by realized-loss denominator)

circuit_breaker_active =
    compare(
        realized_loss_today.value,
        daily_loss_stop_fraction,
        circuit_breaker_comparison_rule,
    )
```

Realized P&L already reflected in cash must not be added to NAV a second time.
For long-only v1, estimable net exposure must equal estimable gross exposure.
If any prerequisite is `NOT_ESTIMABLE`, the derived envelope must also be
`NOT_ESTIMABLE` with causal reason codes; summing a sentinel is forbidden.
When NAV is estimable and zero, later return ratios that divide by NAV are
`NOT_ESTIMABLE` under `zero_nav_rule`, the portfolio becomes terminal
`INSOLVENT`, and the total-loss path remains in drawdown evidence.
`control_30d_closed_trade_avg_pnl` remains a separate historical control
measurement. Copying it into `peak_nav`, `nav_drawdown`, or a NAV-drawdown gate
is a protocol error.

### 7.5 Positions and pending commitments

`positions` is a deterministic tuple of strict `FrozenPositionState` objects:

| Field | Constraint |
|---|---|
| `position_id` | unique non-empty string |
| `ticker` | canonical ticker; unique unless policy explicitly permits tax lots |
| `opened_at` | aware datetime `<= state_as_of` |
| `entry_quantity_lots` | integer `>=1`; the original order obeyed the frozen lot rule |
| `current_quantity_shares` | strict integer `>0`; odd lots are valid, fractional entitlements are not silently rounded |
| `quantity_origin` | `ENTRY_LOT_ROUNDED` or `CORPORATE_ACTION_ADJUSTED` |
| `quantity_adjustment_event_sha256` | required for `CORPORATE_ACTION_ADJUSTED`, otherwise `None` |
| `total_cost_basis_idr` | strict integer `>0`; average price, if needed, is a derived quantized ratio |
| `mark_price_idr` | strict integer `>0` |
| `mark_as_of` | aware datetime `<= state_as_of` |
| `market_value` | `EstimableMoney`, recomputed exactly as integer shares × integer mark |
| `planned_stop_price_idr` | strict integer `>0` or `None` |
| `allocation_fraction` | `EstimableRatio`, recomputed from the policy-selected `allocation_fraction_denominator` |
| `risk_to_stop` | `EstimableMoney`, recomputed when stop and quantity are known |
| `source_record_sha256` | exact mark/source lineage |

`pending_commitments` is a deterministic tuple of strict
`FrozenPendingCommitment` objects containing commitment ID, ticker, created
time, expiry, reserved cash, potential exposure/risk, source decision hash, and
status. IDs are unique; tuples are sorted by frozen keys. Separate
`positions_status` and `pending_commitments_status` values distinguish a truly
empty estimable tuple from an unavailable source.

Fractional corporate-action entitlements must be resolved upstream to an exact
integer-share holding plus integer cash-in-lieu with source lineage, or the
affected position/state becomes `NOT_ESTIMABLE`. Multiplying a fractional
share quantity by a price and rounding to money is forbidden.

### 7.6 State identity

The externally used state identity is:

```text
portfolio_state_sha256 = canonical_sha256(PortfolioState)
```

That value:

- names the immutable content-addressed state artifact;
- is copied into every related `ShadowObservation`; and
- is recomputed from the exact state during lineage validation.

No top-level self-hash is stored inside `PortfolioState`.

## 8. Manifest v2 binding and preflight

### 8.1 Why v2 is sufficient

Manifest v2 already has all required representational hooks:

| Required binding | Existing v2 mechanism |
|---|---|
| Baseline control identity | `baseline_manifest_id` + `baseline_manifest_sha256` |
| Machine-readable portfolio policy | Same CONFIG path/hash in `control_content_hashes` and `challenger_content_hashes` |
| Queryable scalar assumptions | Uniquely named `FrozenParameter`s |
| Liquidity, mark, and portfolio-state sources | `SourceDefinition` with contract, hash, as-of, expiry, and missing policy |
| Fee/tax/slippage/lot/execution rules | `CostAssumptions` |
| Detailed formulas and exception semantics | `methodology_document_path` + mandatory SHA-256 |
| Producer/validator implementation identity | Control/challenger content hashes |
| Full approval identity | Manifest canonical/raw hash pair in external ApprovalRecord |

The relevant fields are present in
`core/shadow_protocol/contracts.py:191-281,375-414`.

The following is a concrete serialized manifest-v2 excerpt. The 64-zero CONFIG
hash is deliberately an illustrative placeholder because this pass creates no
real C1/C7 component policy or manifest; a real manifest must replace it with
the SHA-256 of its exact canonical policy bytes.

```json
{
  "control_content_hashes": [
    {
      "path": "config/portfolio-policy-v1.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "role": "CONFIG"
    }
  ],
  "challenger_content_hashes": [
    {
      "path": "config/portfolio-policy-v1.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "role": "CONFIG"
    }
  ],
  "thresholds": [
    {
      "name": "portfolio_binding_profile",
      "value": "portfolio-binding-v1",
      "unit": null,
      "source": "RS-P2-014 owner-approved portfolio binding"
    },
    {
      "name": "phase2_capability_status",
      "value": "RS_P2_014_ONLY_NOT_A1_ELIGIBLE",
      "unit": null,
      "source": "RS-P2-014 substrate is not A1-eligible"
    },
    {
      "name": "starting_capital_idr",
      "value": 100000000,
      "unit": "IDR",
      "source": "owner decision V2"
    },
    {
      "name": "fixed_notional_idr",
      "value": 13000000,
      "unit": "IDR",
      "source": "owner decision V2"
    },
    {
      "name": "minimum_adt_idr",
      "value": 10000000000,
      "unit": "IDR",
      "source": "owner decision V2"
    },
    {
      "name": "max_participation_fraction",
      "value": 0.0013,
      "unit": "fraction_of_ADTV20",
      "source": "owner decision N1"
    },
    {
      "name": "target_deployment_fraction",
      "value": 0.65,
      "unit": "fraction_of_starting_capital",
      "source": "owner decision N2"
    },
    {
      "name": "effective_fixed_notional_max_deployment_fraction",
      "value": 0.39,
      "unit": "fraction_of_starting_capital",
      "source": "owner decision N2"
    },
    {
      "name": "max_drawdown_stop_fraction",
      "value": null,
      "unit": "fraction_of_NAV",
      "source": "owner decision N3: NOT_ESTIMABLE"
    }
  ],
  "costs": {
    "currency": "IDR",
    "buy_commission_bps": 15.0,
    "sell_commission_bps": 25.0,
    "sell_tax_bps": 10.0,
    "slippage_bps": 5.0,
    "bid_ask_bps": 5.0,
    "lot_size": 100,
    "liquidity_execution_rule": "POLICY_BOUND_CAPACITY;MISSING=ABSTAIN",
    "price_rounding_rule": "SOURCE_SUPPLIED_INTEGER_IDR",
    "cost_model_version": "integer-idr-cost-v1"
  }
}
```

This proves V1 at the schema level: capital, notional, ADTV and participation
caps are typed `FrozenParameter`s; the same policy hash is present on both
sides; and lot/cost/liquidity execution assumptions stay in `costs`. Detailed
source identities and formulas remain bound by `sources` and the methodology
document hash. No manifest-v3 field is needed.

### 8.2 Mandatory portfolio-binding profile

Before A1 or collection, a new fail-closed preflight named conceptually
`portfolio-binding-v1` must prove:

1. the exact policy CONFIG hash appears in **both** control and challenger
   content-hash tuples;
2. the two entries refer to the same bytes and contract version;
3. required named capital/notional/liquidity/risk parameters exist once, have
   correct units/types, and match the policy artifact;
4. source IDs for portfolio state, marks, liquidity, calendar, and corporate
   actions exist and their hashes match;
5. policy cost/calendar/methodology hashes equal the manifest;
6. the capability registry and test evidence show every required Phase-2 view
   actually implemented before a real component A1; field presence alone is
   rejected as capability evidence;
7. any missing/mismatched item rejects A1 eligibility and observation
   authorization.

This semantic preflight is necessary because the generic manifest schema can
represent arbitrary components and therefore does not hard-code
portfolio-policy parameter names.

The exact enforcement points are mandatory:

1. **A1 creation:** before `ProtocolGovernanceStore.append_approval` can append
   an `A1_APPROVED` event, it loads the exact CONFIG bytes, validates
   `shadow-portfolio-policy-v1`, runs `portfolio-binding-v1`, and relies on the
   approved manifest hash to transitively bind the policy/profile identity.
2. **Authorization reload:** `load_authorization` reloads the same policy bytes
   and re-runs the profile binding rather than trusting a prior pass.
3. **Observation authorization:** before an observation event, the store loads
   the exact policy, state source, state, candidate set, and observation and
   reconstructs every hash/chronology edge.
4. **Maturation/backfill:** before any outcome is matured or backfilled, the
   outcome consumer reloads the same policy/source/state and verifies the
   observation lineage again. Observation-time validation alone is not enough.
5. **Fixed-terminal replay:** terminal reproduction starts from those exact
   artifacts; a missing policy/state makes the affected result invalid, not
   recoverable from caller parameters.

RS-P2-014 implements points 1–3 and exposes an additive exact-artifact reload
method for later consumers. It deliberately does not modify
`shadow-evaluation-v1` or its one-lot outcome engine in this pass. Therefore
points 4–5 remain mandatory Phase-2 work before A1 eligibility can ever be
enabled. The frozen capability literal
`RS_P2_014_ONLY_NOT_A1_ELIGIBLE` makes both `append_approval` and authorization
reload fail closed in the interim, so the unwired maturation path is
unreachable by an authorized portfolio-profile cohort.

The profile version and exact reserved policy CONFIG path are themselves frozen
as named manifest parameters and covered by producer/preflight content hashes.
Generic `role=CONFIG` without these checks is not sufficient.

### 8.3 v3 decision

**No manifest v3 delta is proposed.**

A dedicated `portfolio_policy_sha256` manifest field would be convenient but
would duplicate the already typed CONFIG content hash. Integrity is preserved
by the strict policy artifact plus fail-closed preflight. A v3 would become
necessary only if the project later requires every generic manifest to embed a
typed portfolio-policy object directly instead of binding a typed external
artifact.

This conclusion means the current pass does not trigger the “proposed v3 delta
and STOP” branch.

## 9. Paired candidate-level producer

The proposed producer’s persistent result is the existing
`ShadowObservation`:

```text
produce_paired_observation(
    manifest,
    raw_capture,
    candidate_set,
    candidate,
    frozen_snapshot,
    feature_values_sha256,
    portfolio_state_sha256,
    artifact_store,
    authorization_loader,
    approval_ledger_id,
    control_evaluator,
    challenger_evaluator,
    cluster,
    captured_at,
) -> ShadowObservation
```

### 9.1 Required sequence

1. Reload and strict-revalidate every supplied artifact.
2. Ask the store-backed authorization loader to reload active A1/closure state
   for the exact manifest and ledger before the first evaluator call. This does
   not grant A1; it consumes a separately granted authorization. Test-only
   spies grant no authority.
3. Verify raw-capture/candidate-set order, membership, quarantine behavior, and
   opportunity-set parity using existing evidence rules.
4. Load policy by manifest-bound CONFIG hash; verify its canonical bytes and
   verify evaluator capability separately from policy content.
5. Load the `PortfolioState` by canonical hash from immutable storage.
6. Verify protocol, component, manifest revision/hash, baseline pair, calendar,
   opportunity set, candidate set, policy, costs, methodology, source record,
   and chronology.
7. Construct one immutable `DecisionInput` containing the exact candidate,
   snapshot, feature hash, costs, and state.
8. Pass that same validated input and state hash independently to control and
   challenger evaluators. Neither evaluator receives the other’s output.
9. Recompute the state hash before and after each call. Any change, missing
   artifact, or side-specific substitution fails closed.
10. Require role/basis correctness:
    - control: `CONTROL`; size basis `NONE` or `CONTROL_OBSERVED`;
    - challenger: `CHALLENGER`; size basis `NONE` or `COUNTERFACTUAL`.
11. Preserve both decisions when only one side acts. The protocol explicitly
    includes such cases in the decision view
    (`SHADOW_MODE_PROTOCOL.md:97-100`).
12. If a side’s candidate-set disposition is `PRUNED`, require an inactionable,
    non-allocating decision carrying the corresponding reason. Quarantined
    inputs remain pruned by both sides under existing evidence rules.
13. Compute both decision payload hashes and divergence through existing
    contract functions.
14. Build one `ShadowObservation` whose `portfolio_state_sha256` equals the
    recomputed canonical state hash.
15. Before authorization/persistence, reload the exact state and reconstruct
    its lineage again. Missing or changed state means no observation event.

### 9.2 Same-batch rule

All candidates from one simultaneous opportunity-set capture use the same
pre-batch state. Candidate order may affect later RS-P2-016 allocations only
through the frozen `same_timestamp_priority_rule`; it must not make candidate
2 in RS-P2-014 see candidate 1’s counterfactual decision.

### 9.3 No live side effect

The producer returns evidence only. It does not:

- call an exchange/order adapter;
- mutate the control portfolio;
- alter live rank, sizing, or actionability;
- reserve real cash;
- publish a “trusted” recommendation; or
- make a later view active.

The control remains the only live authority and shadow orders remain records,
not exchange orders (`SHADOW_MODE_PROTOCOL.md:103-105`). The Phase-2 hard stop
also states that measurement capability cannot start a cohort without a
separately approved component manifest
(`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:447-455`).

## 10. Immutable storage and lineage

### 10.1 Paths

Use the governance store’s protocol/manifest namespace and canonical/raw hash
layout:

```text
protocols/{protocol_id}/{manifest_canonical_sha256}/
  portfolio_policies/
    {policy_canonical_sha256}/{policy_raw_sha256}.json
  portfolio_state_sources/
    {source_record_canonical_sha256}/{source_record_raw_sha256}.json
  portfolio_state_source_refs/
    {source_record_id}.json
  portfolio_states/
    {state_canonical_sha256}/{state_raw_sha256}.json
  portfolio_state_refs/
    {portfolio_state_id}.json
  observations/
    {observation_canonical_sha256}/{observation_raw_sha256}.json
```

Two strict support contracts make those paths verifiable:

**`PortfolioStateSourceRecord`**

- `contract_version = shadow-portfolio-state-source-v1`;
- protocol/component/manifest identity;
- source record ID, source ID, and source-definition SHA-256;
- source as-of, expiry, and capture timestamps;
- canonical payload JSON plus payload SHA-256;
- evaluation-only authority literals.

**`PortfolioStateSourceReference`**

- `contract_version = shadow-portfolio-state-source-reference-v1`;
- protocol/component/manifest identity and source-record ID;
- source-record contract version, canonical SHA-256, raw-file SHA-256, raw byte
  length, and immutable relative path; and
- source-definition and payload SHA-256.

**`PortfolioStateReference`**

- `contract_version = shadow-portfolio-state-reference-v1`;
- protocol/component/manifest identity and `portfolio_state_id`;
- state contract version, canonical SHA-256, raw SHA-256, raw byte length, and
  immutable relative path;
- policy canonical/raw hashes;
- source-record canonical/raw hashes; and
- state sequence, path ID, predecessor SHA-256, and capture timestamp.

Each reference contains hashes of its target; neither target contains its own
raw-file hash or reference, avoiding circularity. Duplicate JSON keys,
raw-byte changes, wrong byte length, wrong hash path, or a reference to
unavailable bytes all fail closed.

The dual-hash path follows
`core/shadow_protocol/governance.py:1510-1523`; exclusive-create collision
behavior follows `core/shadow_protocol/governance.py:1625-1636`.
CandidateSetStore’s raw-first and strict canonical reload pattern remains the
evidence-side precedent (`core/shadow_protocol/evidence.py:1476-1615`).

Rules:

- source record is persisted before derived state;
- policy is persisted before state;
- state is persisted before observation;
- exact retry with identical bytes is idempotent;
- same reference ID/path with different bytes is a collision;
- raw bytes with duplicate keys or noncanonical serialization are rejected;
- no `latest_*` path is authoritative;
- an orphan state grants no collection authority.

### 10.2 Lineage chain

```text
Manifest v2 canonical hash
├── baseline manifest ID + SHA-256
├── methodology SHA-256
├── identical portfolio-policy CONFIG SHA-256
├── source-definition SHA-256
│   └── exact source-record SHA-256
│       └── PortfolioState canonical SHA-256
└── raw capture / candidate set / snapshot / feature hashes
    └── ShadowObservation.portfolio_state_sha256
        ├── control decision payload SHA-256
        └── challenger decision payload SHA-256
            └── observation canonical/raw hashes
                └── authorized observation ledger event
```

The current `LineageBundle` must be advanced explicitly—never silently
reinterpreted—to include and rehash the exact policy, source record, and
PortfolioState. The existing bundle currently reconstructs the other evidence
edges but does not dereference the state
(`core/shadow_protocol/evidence.py:982-1135`).

Recommended artifact change for the implementation pass:

```text
shadow-lineage-bundle-v1  remains readable as historical substrate evidence
shadow-lineage-bundle-v2  requires portfolio-state lineage for RS-P2-014 records
```

This is an evidence-contract version, not a manifest-v3 change.

`ShadowObservation-v1` itself may remain structurally readable, but dispatch is
profile-aware:

- historical observation-v1 artifacts without `portfolio-binding-v1` remain
  readable as pre-RS-P2-014 substrate evidence;
- they are not A1-eligible collection evidence for RS-P2-014 and their opaque
  dummy state hashes are never treated as resolved;
- a manifest declaring `portfolio-binding-v1` requires lineage-bundle-v2 and a
  loadable exact PortfolioState for every observation; and
- loaders must issue an explicit profile/version error rather than silently
  reinterpret old observation-v1 artifacts.

## 11. Dependency and test plan

### 11.1 Existing Phase-2 dependencies touched

| Existing task | Proposed impact | Must remain true |
|---|---|---|
| RS-P2-001/002 contracts | Add strict frozen policy/state/source models and explicit exports. | Unknown fields, non-finite values, bad hashes, mutation, and authority drift remain rejected. |
| RS-P2-008 raw-first capture | Producer consumes the already persisted raw capture/candidate set. | No decision may prune before raw capture. |
| RS-P2-009 opportunity parity | Add identical state/policy/input-hash parity. | Control and challenger keep the same raw order, snapshot, features, costs, and state. |
| RS-P2-010/012 maturation and backfill | Reload policy/source/state before maturation and every idempotent backfill. | A previously authorized observation with missing/tampered state cannot mature. |
| RS-P2-013 lineage/replay | Extend reconstruction to dereference state and policy. | Opaque submitted hashes are never trusted. |
| Governance A1/authorization/observation consumers | Run policy preflight before A1, bind it in authorization reload, and load state before observation persistence. | Existing cooling-off, calendar, approval, and closure checks remain unchanged and fail closed. |

This design does not alter the meaning of RS-P2-015–018 or mark them done.

### 11.2 Proposed test matrix

#### A. Contract and arithmetic

- [ ] `test_portfolio_policy_rejects_extra_field`
- [ ] `test_portfolio_state_rejects_extra_field`
- [ ] `test_portfolio_contracts_are_frozen`
- [ ] `test_portfolio_contracts_reject_naive_datetime`
- [ ] `test_portfolio_contracts_reject_nonfinite_numeric_fields`
- [ ] `test_portfolio_contracts_reject_bad_sha256`
- [ ] `test_policy_rejects_nonpositive_capital_or_notional`
- [ ] `test_policy_rejects_fraction_outside_unit_interval`
- [ ] `test_state_rejects_duplicate_or_unsorted_positions`
- [ ] `test_state_rejects_duplicate_or_unsorted_commitments`
- [ ] `test_state_rejects_position_count_mismatch`
- [ ] `test_state_rejects_share_lot_mismatch`
- [ ] `test_state_rejects_position_market_value_mismatch`
- [ ] `test_state_rejects_nav_cash_identity_mismatch`
- [ ] `test_state_rejects_heat_or_exposure_mismatch`
- [ ] `test_zero_nav_is_retained_as_terminal_insolvency`
- [ ] `test_zero_nav_denominator_metric_is_not_estimable`
- [ ] `test_policy_rejects_non_integer_idr_money`
- [ ] `test_state_rejects_non_integer_idr_money`
- [ ] `test_integer_money_identities_are_exact`
- [ ] `test_cost_bps_rounding_aggregates_then_ceils_once_against_portfolio`
- [ ] `test_ratio_quantization_precedes_serialization_and_hashing`
- [ ] `test_incomplete_state_requires_explicit_missing_fields`
- [ ] `test_missing_state_value_never_becomes_zero`
- [ ] `test_not_estimable_position_risk_propagates_to_heat`
- [ ] `test_post_split_odd_or_fractional_quantity_requires_action_lineage`
- [ ] `test_participation_cap_is_bound_as_derived_not_calibrated`
- [ ] `test_participation_cap_rejects_percent_fraction_confusion`
- [ ] `test_target_deployment_basis_is_not_effective_deployment`
- [ ] `test_effective_fixed_notional_max_deployment_is_39_percent`
- [ ] `test_closed_trade_average_cannot_populate_nav_drawdown`
- [ ] `test_nav_drawdown_gate_requires_post_rs_p2_017_new_protocol`

#### B. Manifest, policy, and baseline binding

- [ ] `test_portfolio_preflight_requires_policy_hash_on_both_sides`
- [ ] `test_portfolio_preflight_rejects_different_control_challenger_policy`
- [ ] `test_portfolio_preflight_requires_all_named_parameters`
- [ ] `test_portfolio_preflight_rejects_wrong_parameter_unit_or_type`
- [ ] `test_portfolio_preflight_rejects_missing_source_definition`
- [ ] `test_portfolio_preflight_rejects_cost_hash_mismatch`
- [ ] `test_portfolio_preflight_rejects_methodology_hash_mismatch`
- [ ] `test_portfolio_preflight_rejects_calendar_hash_mismatch`
- [ ] `test_a1_append_rejects_missing_or_invalid_portfolio_binding_profile`
- [ ] `test_authorization_reload_revalidates_policy_bytes`
- [ ] `test_portfolio_state_rejects_manifest_revision_or_hash_mismatch`
- [ ] `test_portfolio_state_rejects_baseline_id_or_hash_mismatch`
- [ ] `test_portfolio_state_rejects_policy_hash_mismatch`
- [ ] `test_portfolio_loader_rejects_manifest_v1_explicitly`

#### C. Tamper and immutable storage

- [ ] `test_portfolio_store_exclusive_create_is_idempotent_for_same_bytes`
- [ ] `test_portfolio_store_rejects_same_reference_with_different_bytes`
- [ ] `test_portfolio_store_rejects_duplicate_json_keys`
- [ ] `test_portfolio_store_rejects_noncanonical_reformatting`
- [ ] `test_portfolio_store_rejects_content_under_wrong_hash_path`
- [ ] `test_portfolio_store_rejects_modified_source_record`
- [ ] `test_observation_rejects_modified_portfolio_state`
- [ ] `test_observation_authorization_rejects_missing_portfolio_state`
- [ ] `test_lineage_rejects_opaque_unresolved_portfolio_state_hash`

#### D. Chronology

- [ ] `test_state_rejects_future_source_vintage`
- [ ] `test_state_rejects_future_position_mark`
- [ ] `test_state_rejects_expired_source_at_signal`
- [ ] `test_state_rejects_state_as_of_after_signal`
- [ ] `test_observation_rejects_state_captured_after_observation`
- [ ] `test_store_rejects_observation_before_state_persistence`
- [ ] `test_state_rejects_stale_source_under_frozen_expiry_rule`
- [ ] `test_same_batch_state_excludes_current_batch_decisions`
- [ ] `test_candidate_state_requires_raw_set_state_observation_causal_chain`
- [ ] `test_daily_mark_state_allows_no_candidate_set_but_requires_predecessor`

#### E. Pairing and parity

- [ ] `test_both_evaluators_receive_same_input_and_state_hash`
- [ ] `test_evaluator_state_mutation_fails_closed`
- [ ] `test_side_specific_state_substitution_fails_closed`
- [ ] `test_control_and_challenger_role_and_size_basis_are_enforced`
- [ ] `test_one_side_acts_other_abstains_both_are_persisted`
- [ ] `test_pruned_side_cannot_become_actionable`
- [ ] `test_quarantined_candidate_remains_pruned_on_both_sides`
- [ ] `test_candidate_snapshot_or_feature_side_drift_fails_closed`
- [ ] `test_all_candidates_in_simultaneous_batch_share_prebatch_state`
- [ ] `test_challenger_evaluation_order_cannot_change_control_payload`

#### F. Replay and lineage

- [ ] `test_identical_input_replays_identical_policy_state_decision_hashes`
- [ ] `test_portfolio_state_hash_matches_across_separate_python_processes`
- [ ] `test_timezone_equivalent_instants_hash_identically`
- [ ] `test_canonical_map_and_tuple_order_is_deterministic`
- [ ] `test_full_lineage_reconstructs_portfolio_state_and_observation`
- [ ] `test_lineage_v1_is_not_silently_reinterpreted_as_v2`
- [ ] `test_authorized_observation_cannot_outlive_missing_policy_or_state`
- [ ] `test_maturation_rejects_missing_or_tampered_policy_source_or_state`
- [ ] `test_backfill_revalidates_portfolio_lineage_on_every_attempt`

#### G. Authority and regression

- [ ] `test_new_artifacts_are_evaluation_only`
- [ ] `test_new_artifacts_have_no_live_ranking_sizing_execution_authority`
- [ ] `test_paired_producer_never_calls_live_order_or_portfolio_writer`
- [ ] `test_control_payload_unchanged_with_shadow_enabled_or_disabled`
- [ ] all existing `tests/test_shadow_protocol*.py` pass unchanged
- [ ] full pytest passes
- [ ] Ruff and lock verification pass under the implementation pass gate

These tests extend the checklist’s existing parity, tamper, replay, and
instrumentation requirements
(`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:419-445`). They do not run a cohort.

### 11.3 RS-P2-014 design Definition of Done

The future implementation may mark RS-P2-014 done only when:

- the policy, source, state, and loader contracts exist and are exported;
- manifest-v2 portfolio preflight is fail-closed;
- the same exact state reaches both evaluators;
- `ShadowObservation.portfolio_state_sha256` resolves to exact persisted bytes;
- lineage reconstruction rehashes policy/source/state rather than trusting the
  submitted value;
- observation persistence refuses missing/tampered state;
- tamper, replay, parity, chronology, authority, and regression tests pass;
- no fixed-notional, policy-portfolio, or NAV feature is misreported as active;
- no A1 is granted and no collection occurs as a side effect; and
- the checklist status cites actual files/tests and remains partial if any edge
  above is absent.

## 12. Resolved implementation decisions

The owner’s V2 approval and hybrid amendment close the former numeric and
representation questions:

1. The normalized paper state uses Rp100,000,000 starting capital and
   Rp13,000,000 fixed notional; it never imports the owner’s live holdings.
2. Liquidity uses point-in-time mean daily `close × volume` over the last 20
   completed sessions, a Rp10,000,000,000 floor, and the N1 cap `0.0013`
   labeled `DERIVED_NOT_CALIBRATED`.
3. Portfolio controls use the approved base/regime position counts, loss
   budget, heat, gross-exposure, cash-reserve, daily-loss, lot, and settlement
   assumptions. Sector exposure and true NAV drawdown remain explicitly
   `NOT_ESTIMABLE`.
4. Persisted money is strict integer IDR; ratios use 12-decimal
   `ROUND_HALF_EVEN`; applied costs aggregate bps and use one adverse `CEILING`.
5. Same-timestamp evidence order is source-row ascending then ticker ascending.
   RS-P2-014 never evolves the state in that order; every candidate in the
   batch sees the same pre-batch hash.
6. RS-P2-014 reserves no new cash and performs no fills. Unsettled cash remains
   separately recorded and non-deployable. Reservation, partial-fill, and
   insufficient-notional execution behavior remain task-gated to RS-P2-015/016.
7. Source-supplied integer-IDR price is retained for the decision view. Daily
   mark production remains task-gated to RS-P2-017. Odd-lot integer shares are
   valid; fractional entitlements require exact integer cash-in-lieu lineage or
   become `NOT_ESTIMABLE`.
8. A material edit before A1 requires a new manifest revision, new hashes,
   self-review, cooling-off, and A1. A material edit after collection starts,
   even at observed `n=0`, requires a new protocol/trial. Existing solo
   governance and closure rules remain unchanged.

## 13. Approval boundary and next action

The owner approved this design and authorized the RS-P2-014 implementation
pass for additive contracts, frozen-state construction, paired candidate
evaluation, immutable storage/lineage, and tests. It does **not**:

- grant A1 to C7, C1, or any other component;
- start collection;
- activate fixed-notional, policy-portfolio, or NAV logic;
- change a threshold or control decision;
- touch live execution, ranking, or sizing;
- modify `RS-CONTROL-20260717-01`; or
- mark RS-P2-015–018 complete.

After RS-P2-014 is implemented and verified, proceed to RS-P2-015. C7 remains
the first component challenger after the full Phase-2 measurement substrate;
C1 follows only when the portfolio/NAV and C6 gates can support its required
metrics.
