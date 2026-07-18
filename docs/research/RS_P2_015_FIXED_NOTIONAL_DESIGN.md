# RS-P2-015 Identical Fixed-Notional View Design

**Status:** IMPLEMENTED — VERIFICATION GATE GREEN; A1 NOT GRANTED
**Decision date:** 2026-07-18
**Target:** RS-P2-015 only
**Predecessor:** RS-P2-014 frozen portfolio state and paired decision view
**Successors:** RS-P2-016 policy portfolio, RS-P2-017 daily MTM NAV, and
RS-P2-018 common metrics remain out of scope
**Authority:** evaluation only; no A1 grant, collection, unblinding, promotion,
live execution, ranking, or sizing authority

## 1. Executive decision

RS-P2-015 implements the second of the protocol's three paired views:

1. the RS-P2-014 decision view already compares both sides against one frozen
   control portfolio state;
2. this pass adds an **identical Rp13,000,000 gross fixed-notional view** that
   isolates signal quality; and
3. RS-P2-016 will later add independently evolving policy portfolios.

This ordering is required by `SHADOW_MODE_PROTOCOL.md:86-105` and
`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:406-417`. The fixed-notional view
does not mutate the RS-P2-014 state and is not a portfolio simulator.

All economic conventions required by this pass are now frozen. The owner
approved FN1–FN8 and FN-N1–FN-N3 below. There is no unresolved owner decision
and no manifest-v3 requirement.

The implementation is an additive artifact family. It must not call the
one-lot evaluator as fixed-notional authority, multiply a one-lot result, load
`ShadowOutcome-v1` as a fixed-notional result, or silently reinterpret any
`shadow-evaluation-v1` artifact.

## 2. Evidence boundary

The design is grounded in the current contracts and protocol:

- a raw event is one ticker, signal timestamp, and frozen snapshot delivered
  to both sides (`SHADOW_MODE_PROTOCOL.md:42-45`);
- the primary estimand is the 15-trading-day target-before-stop label, while
  3/5/10 days remain independent secondary labels
  (`SHADOW_MODE_PROTOCOL.md:46-56`);
- the three paired views and the no-live-order boundary are defined at
  `SHADOW_MODE_PROTOCOL.md:86-105`;
- undefined metrics must be `NOT_ESTIMABLE`, never coerced to zero
  (`SHADOW_MODE_PROTOCOL.md:107-122`);
- manifest v2 already supports content hashes, named frozen parameters,
  source definitions, label rules, and cost assumptions
  (`core/shadow_protocol/contracts.py:191-303`,
  `core/shadow_protocol/contracts.py:375-414`);
- RS-P2-014 already provides strict evaluation-only authority literals
  (`core/shadow_protocol/portfolio.py:132-147`), the owner-approved portfolio
  assumptions (`core/shadow_protocol/portfolio.py:210-342`), one immutable
  state (`core/shadow_protocol/portfolio.py:700-779`), and one immutable paired
  input (`core/shadow_protocol/paired_view.py:50-162`);
- the paired producer already verifies authorization before either evaluator,
  loads the exact persisted state, delivers the same input to both evaluators,
  checks the input after each call, and reloads the state from storage
  (`core/shadow_protocol/paired_view.py:186-343`);
- the portfolio store already uses canonical and raw SHA-256 in an
  exclusive-create, content-addressed namespace
  (`core/shadow_protocol/portfolio.py:2330-2394`);
- the historical outcome engine fixes quantity to one manifest lot
  (`core/shadow_protocol/outcome_engine.py:844-890`), and
  `ShadowOutcome-v1` persists quantity and money as floats
  (`core/shadow_protocol/contracts.py:1159-1224`). Those two facts make it
  unsuitable as RS-P2-015 authority.

References to `outcome_engine.py` and `ShadowOutcome-v1` in this document are
diagnostic only. Those files and semantics remain unchanged.

## 3. Pre-implementation convention inventory

### 3.1 Already frozen before RS-P2-015

| Convention | Frozen value or source | Consequence for RS-P2-015 |
|---|---|---|
| Starting capital | Rp100,000,000 | Identity/provenance only; fixed-notional records do not evolve the capital path. |
| Gross fixed notional | Rp13,000,000 | Same target gross stock notional for both sides. |
| Board lot | 100 shares | Every planned entry quantity is a positive whole-lot multiple. |
| Money representation | strict integer IDR | No persisted float money, shares, cost, NAV, or cash flow. |
| Ratio representation | finite float quantized to 12 decimal places with `ROUND_HALF_EVEN` | Applied once before ratio serialization. |
| Cost rounding | `AGGREGATE_APPLICABLE_BPS_THEN_CEIL_INTEGER_IDR` | The only permitted money rounding point. |
| Entry cost components | buy commission + one slippage + one bid-ask charge | Aggregate first, then one ceiling. |
| Exit cost components | sell commission + sell tax + one slippage + one bid-ask charge | Aggregate first, then one ceiling. |
| ADTV20 basis | mean close × volume over the last 20 completed sessions | Must be point-in-time and source-hash bound. |
| ADTV20 minimum | Rp10,000,000,000 | Below-minimum or unavailable capacity does not become infinite liquidity. |
| Participation cap | 0.0013, `DERIVED_NOT_CALIBRATED` | Exact mechanical ratio Rp13m ÷ Rp10bn, not an impact estimate. |
| Capacity rounding | floor to whole board lots | Never round capacity upward. |
| Missing/stale capacity | `NOT_ESTIMABLE` | Never substitute zero, the threshold, or unlimited capacity. |
| Settlement | T+2 completed IDX sessions | Cash-flow records carry trade and settlement sessions separately. |
| Leverage/shorting | forbidden | Fixed-notional artifacts are long-only record-only counterfactuals. |
| Fill and outcome rules | manifest `LabelDefinition` and its bound execution policy | No alternate fill, expiry, gap, ambiguity, dividend, rights, or horizon rule is introduced. |
| Primary horizon | 15 trading days | Only the 15-day lifecycle may later feed RS-P2-017 NAV. |
| Secondary horizons | 3/5/10 trading days | Metric outcomes only; never additional NAV positions. |
| Frozen state | exact RS-P2-014 `PortfolioState` hash | Both sides use the same read-only state; no state mutation occurs. |
| Authority | evaluation-only true; all `affects_*` false; live authority false | Artifacts cannot affect the live pipeline. |

The cost composition agrees with `SHADOW_MODE_PROTOCOL.md:50-56` and the
manifest cost fields at `core/shadow_protocol/contracts.py:269-281`.
Activation, gap, ambiguity, corporate-action, rights, dividend, and unfilled
semantics remain those in `LabelDefinition`
(`core/shadow_protocol/contracts.py:284-303`).

### 3.2 Mechanical derivations

The following are not owner discretion once the frozen inputs above exist:

- integer conversion of a validated integer-IDR price;
- floor division into whole lots;
- exact share count;
- exact gross entry/exit values;
- exact unused sleeve cash;
- exact integer entry/exit costs after the one authorized ceiling;
- T+2 settlement-session lookup from the manifest-bound calendar;
- exact P&L and planned-risk arithmetic;
- 12-place return and net-R quantization;
- canonical JSON, canonical SHA-256, raw-file SHA-256, byte length, immutable
  path, reference, and lineage computation;
- propagation of a missing prerequisite to `NOT_ESTIMABLE`;
- rejection of any non-finite or non-integer persisted money input.

### 3.3 Owner decisions introduced for RS-P2-015

FN1–FN8 below were new economic conventions. They are now explicitly approved,
so implementation may proceed without inventing defaults.

## 4. Approved owner decisions FN1–FN8

### FN1 — sizing-price basis

Sizing uses the decision's **planned integer-IDR `entry_high`**. It is fixed
before fill and therefore cannot benefit from future execution knowledge.

For any acted side:

```text
one_lot_cash_basis_idr = entry_high_idr × 100
desired_lots           = floor(13,000,000 / one_lot_cash_basis_idr)
desired_shares         = desired_lots × 100
planned_gross_idr      = desired_shares × entry_high_idr
```

The realized fill rule still comes from the manifest. A fill at a lower valid
price does not increase quantity after the fact.

### FN2 — meaning of Rp13,000,000

Rp13,000,000 caps **gross stock notional before entry costs**. Entry costs are
stored separately. Therefore:

```text
gross_entry_value_idr <= 13,000,000
entry_cash_debit_idr   = gross_entry_value_idr + entry_cost_idr
```

The second value may exceed Rp13,000,000. It must not be clipped, netted
against residual cash, or represented as if cost were free.

### FN3 — residual cash and metric denominators

Unused sleeve cash remains idle, zero-return cash:

```text
unused_sleeve_cash_idr = 13,000,000 - gross_entry_value_idr
```

The required metrics are:

- primary per-opportunity return =
  `net_pnl_idr / 13,000,000`;
- acted-trade return =
  `net_pnl_idr / gross_entry_value_idr`;
- net-R =
  `net_pnl_idr / planned_risk_idr`.

Each denominator is explicit in the artifact. An unavailable or zero
trade/risk denominator yields `NOT_ESTIMABLE`; it is never replaced by the
sleeve denominator.

### FN4 — entry and exit capacity are all-or-none

The RS-P2-014 task gate is resolved to the exact policy literal
`ALL_OR_NONE`.

At entry:

- if full desired lots fit within point-in-time capacity, evaluation may
  continue;
- if full desired lots do not fit, the candidate becomes
  `NOT_ESTIMABLE` with `NOT_ESTIMABLE_ENTRY_CAPACITY`;
- quantity is never reduced to available capacity;
- partial quantities are not accumulated across days;
- a capacity exclusion is not mislabeled as market-price `UNFILLED`.

At exit:

- the entire then-current share quantity must fit within capacity computed
  from ADTV20 available before the exit session;
- otherwise the lifecycle becomes `NOT_ESTIMABLE` with
  `NOT_ESTIMABLE_EXIT_CAPACITY`;
- the evaluator does not assume liquidation, defer the trigger, or synthesize
  a multi-day exit.

### FN5 — high-price exclusion

If:

```text
desired_lots == 0
```

the exact result is:

```text
status      = NOT_ESTIMABLE
reason_code = NOT_SIZEABLE_FIXED_NOTIONAL
```

It is a recorded shared exclusion, never a zero-size holding, a zero-return
trade, or a silent omission.

### FN6 — non-action side

For a side that does not act:

- opportunity P&L is exact Rp0;
- sleeve return is exactly quantized `0.0`;
- fill return, acted-trade return, and net-R are `NOT_ESTIMABLE`;
- the side's exact decision reason codes remain attached;
- a record is persisted; the side is not removed from the pair.

This is not a coercion of an undefined trade metric. Rp0 is the observed cash
result of making no transaction, while trade-only denominators do not exist.

An order that expires unfilled follows the manifest's `unfilled_rule`; because
no cash flow occurred, its sleeve cash result is also Rp0 while fill/trade
metrics remain `NOT_ESTIMABLE`. It retains the distinct unfilled outcome label
defined by the manifest.

### FN7 — point-in-time exit liquidity and honest censoring

Entry liquidity uses ADTV20 available at signal time. Exit liquidity uses
ADTV20 available before the applicable exit session. Missing, stale, or
insufficient full-exit capacity produces
`NOT_ESTIMABLE_EXIT_CAPACITY`.

The outcome trigger remains the trigger dictated by the frozen label rule. It
is not moved to a later session to obtain a more convenient liquidity result.

### FN8 — one NAV-consumable lifecycle

Only the primary 15-trading-day lifecycle may carry:

```text
nav_consumption_eligible = true
```

The 3/5/10-day secondary outcomes must carry:

```text
nav_consumption_eligible = false
```

Secondary outcomes may reference the same entry evidence, but they cannot
create additional positions or cash flows for RS-P2-017.

## 5. Mandatory owner notes FN-N1–FN-N3

### FN-N1 — effective high-price universe boundary

FN5 mechanically excludes any stock whose planned `entry_high` exceeds
Rp130,000 per share:

```text
13,000,000 / 100 shares = 130,000 IDR/share
```

This is an effective fixed-notional-view universe boundary, not a live trading
restriction. The shared exclusion is identical for both sides.

At exactly Rp130,000, one lot is sizeable. Above Rp130,000, zero lots are
sizeable and the record is `NOT_SIZEABLE_FIXED_NOTIONAL`.

### FN-N2 — cash debit can exceed the sleeve denominator

Because Rp13,000,000 is a pre-cost gross-notional ceiling, total entry cash
debit may exceed it by `entry_cost_idr`. The sleeve-return denominator remains
exactly Rp13,000,000. The artifact must expose gross value, cost, total debit,
and denominator as separate integer fields so a later consumer cannot infer a
different convention.

### FN-N3 — exit-capacity censoring must be visible

`NOT_ESTIMABLE_EXIT_CAPACITY` removes that outcome from the estimable return
sample instead of assuming an exit. RS-P2-018 must report its count and rate
explicitly, by side and where possible by ticker/liquidity band. It must not
silently drop the record or report the missing outcome as zero.

## 6. Exact arithmetic contract

### 6.1 Price validation

Every price used for persisted RS-P2-015 money arithmetic must be:

- finite;
- strictly positive;
- integer-valued IDR;
- causally available under the relevant signal/fill/exit clock.

The existing bar evidence type contains float fields. Reusing that immutable
source evidence does not authorize float money arithmetic. The RS-P2-015 trust
boundary must validate an integer-valued price and convert it exactly to a
strict integer before any multiplication. A fractional IDR price is
`NOT_ESTIMABLE` or rejected according to whether it is an unavailable market
measurement or a malformed artifact; it is never rounded because cost
application is the only authorized money-rounding point.

### 6.2 Entry sizing

```text
lot_size_shares             = 100
target_gross_notional_idr   = 13,000,000
one_lot_basis_idr           = entry_high_idr × lot_size_shares
desired_lots                = target_gross_notional_idr // one_lot_basis_idr
desired_shares              = desired_lots × lot_size_shares
planned_gross_notional_idr  = desired_shares × entry_high_idr
planned_unused_cash_idr     = target_gross_notional_idr
                              - planned_gross_notional_idr
```

All values are exact integers. `desired_lots == 0` triggers FN5.

### 6.3 Capacity

The approved participation fraction is represented by its exact rational
derivation, not binary-float multiplication:

```text
participation_numerator_idr   = 13,000,000
participation_denominator_idr = 10,000,000,000

capacity_cash_idr =
  floor(
    adtv20_idr × participation_numerator_idr
    / participation_denominator_idr
  )
```

For entry, the full planned gross amount must be no greater than
`capacity_cash_idr`. For exit, the full gross exit value at the frozen
label-consistent exit price must be no greater than the causally sourced exit
capacity. Capacity in lots is also persisted for audit, using floor division
at the relevant integer price.

The minimum ADTV threshold and participation cap are both checked. Passing one
does not waive the other.

### 6.4 Costs

Rates are converted to exact decimals from their frozen manifest
representations. Applicable rates are added before multiplication:

```text
entry_bps = buy_commission_bps + slippage_bps + bid_ask_bps
exit_bps  = sell_commission_bps + sell_tax_bps
            + slippage_bps + bid_ask_bps

entry_cost_idr =
  ceil(gross_entry_value_idr × entry_bps / 10,000)

exit_cost_idr =
  ceil(gross_exit_value_idr × exit_bps / 10,000)
```

There is one ceiling per applicable side of the transaction. Rounding must
never reduce a cost or improve portfolio P&L.

### 6.5 Cash flows and P&L

For an estimable filled-and-closed lifecycle:

```text
entry_cash_debit_idr = gross_entry_value_idr + entry_cost_idr
exit_cash_credit_idr = gross_exit_value_idr - exit_cost_idr

net_pnl_idr =
  exit_cash_credit_idr
  + eligible_dividend_cash_idr
  - entry_cash_debit_idr
```

The currently supported corporate-action cash is dividend cash. It must be
exact integer IDR with source lineage. If exact integer cash is unavailable,
the affected metric becomes `NOT_ESTIMABLE`; no second rounding rule is
introduced. Rights remain unsupported under the frozen label policy.

### 6.6 Risk and ratios

Planned risk follows the frozen entry-high basis:

```text
planned_risk_per_share_idr = entry_high_idr - planned_stop_idr
planned_risk_idr           = planned_risk_per_share_idr × desired_shares
```

Ratios are computed from exact integer numerator and denominator, quantized
once to 12 decimal places with `ROUND_HALF_EVEN`, normalized so negative zero
becomes `0.0`, and then persisted as strict finite floats. This follows the
existing `EstimableRatio` boundary
(`core/shadow_protocol/portfolio.py:163-194`).

## 7. Artifact family

Every artifact below is frozen, rejects unknown fields, rejects non-finite
floats, carries exact lower-case SHA-256 bindings, and includes:

```text
evaluation_only  = true
live_authority   = false
affects_execution = false
affects_ranking   = false
affects_sizing    = false
```

### 7.1 `FrozenFixedNotionalPolicy`

Contract version:

```text
shadow-fixed-notional-policy-v1
```

Required sections:

| Section | Required binding |
|---|---|
| Identity | policy ID, `RS-P2-015` implementation profile, protocol/component, manifest revision/hash |
| Predecessor | `FrozenPortfolioPolicy` ID, contract version, canonical hash |
| Inputs | LabelDefinition hash, CostAssumptions hash, execution-policy hash, calendar hash, corporate-action policy hash, methodology hash |
| Money | currency IDR, integer-money rule, Rp13m target, gross-before-cost semantics |
| Quantity | lot 100, planned-entry-high basis, floor-to-whole-lots rule, no post-fill resize |
| Costs | aggregate-applicable-bps then adverse integer ceiling |
| Ratios | 12 decimal places and `ROUND_HALF_EVEN` |
| Residual | idle zero-return sleeve cash and explicit denominator rules |
| Capacity | ADTV20 definition, minimum, exact-rational participation, all-or-none entry/exit, missing/stale behavior |
| Exclusions | exact reason-code registry in Section 11 |
| Labels | manifest-owned fills/unfilled/gaps/ambiguity/corporate actions; primary 15-day NAV eligibility |
| Capability | `RS_P2_015_IMPLEMENTED_NOT_A1_ELIGIBLE` |

The new policy does not modify V2 amounts, N1–N3, or path-dependent portfolio
rules. It resolves only `partial_fill_rule` from
`TASK_GATED_TO_RS_P2_015` to the approved `ALL_OR_NONE`.

### 7.2 `FixedNotionalLiquidityRecord`

Contract version:

```text
shadow-fixed-notional-liquidity-v1
```

One record represents the shared point-in-time liquidity evidence for one
candidate pair. It contains one `ENTRY` measurement and zero or more ordered,
session-unique `EXIT` measurements. Each measurement contains:

- its `ENTRY` or `EXIT` role;
- applicable trading session;
- measurement time and the exact 20 causally available liquidity bars;
- exact ADTV20 status/value in integer IDR;
- exact-rational participation inputs;
- capacity cash and whole lots;
- raw bar hashes and measurement hash.

The enclosing record binds the deterministic record ID; protocol, component,
manifest, observation, raw-event, state, and policy identities; source
definition and vintage; capture time; previous-record hash; and exact
payload/source-record hashes.

Chronology:

- entry evidence must be known by the signal and use the exact immediately
  preceding 20 completed sessions;
- exit source vintage must precede the applicable exit session;
- capture cannot precede source vintage;
- a later source revision cannot replace the stored hash.

### 7.3 `FixedNotionalHoldingRecord`

Contract version:

```text
shadow-fixed-notional-holding-v1
```

This record contains one exact counterfactual holding transition:

- role (`CONTROL` or `CHALLENGER`);
- observation, pair-input, decision, policy, and state hashes;
- event type (`OPEN`, `CLOSE`, or `SPLIT_ADJUSTMENT`);
- event session and timezone-aware occurrence time;
- exact quantity before and after the transition;
- exact integer-IDR event price and marked value after the transition;
- `rs_p2_017_eligible=true`, because only primary-lifecycle transitions are
  emitted.

Sizing, target/stop geometry, costs, residual cash, and terminal status remain
on the lifecycle rather than being duplicated into every transition record.

A high-price or entry-capacity exclusion produces no zero-share holding. The
paired result retains the exclusion, but no holding artifact falsely claims a
position.

### 7.4 `FixedNotionalCashFlowRecord`

Contract version:

```text
shadow-fixed-notional-cash-flow-v1
```

Each event contains:

- deterministic cash-flow ID; sequence is defined by its containing
  lifecycle's ordered cash-flow tuple;
- observation, pair-input, decision, policy, and state identity;
- event type: `ENTRY_DEBIT`, `EXIT_CREDIT`, or `DIVIDEND_CREDIT`;
- event timestamp/session and exact settlement/attribution session;
- exact gross amount, embedded cost, and signed net cash change in integer IDR;
- exact quantity and integer price;
- `SETTLED_T_PLUS_2` for trade cash flows or
  `RETURN_ATTRIBUTION_ON_EFFECTIVE_SESSION` for dividends;
- role and `rs_p2_017_eligible=true`.

Costs are fields on the entry/exit cash-flow event, not separate synthetic
events. Split effects are holding transitions, and the currently supported
corporate-action cash event is the manifest-consistent dividend credit.
Cash-flow sums must reproduce lifecycle P&L exactly. A future RS-P2-017 loader
may consume only primary-lifecycle records and must independently verify every
hash.

### 7.5 `FixedNotionalLifecycle`

Contract version:

```text
shadow-fixed-notional-lifecycle-v1
```

There is one lifecycle per side and horizon. It binds:

- pair-input, observation, decision, policy, and frozen-state identities
  directly;
- snapshot, calendar, labels, costs, bars, corporate actions, and liquidity
  transitively through the exact pair-input hash;
- holding and ordered cash-flow hashes;
- status, fill status, terminal event, and reason codes;
- strict integer-IDR entry, exit, dividend, cost, and denominator fields;
- P&L through `EstimableMoney`;
- sleeve return, acted return, and net-R through `EstimableRatio`;
- whether the lifecycle is primary and NAV-consumable.

The 3/5/10 lifecycles can report secondary metrics but must have no independent
NAV-consumable holding/cash-flow family.

### 7.6 `PairedFixedNotionalRecord`

Contract version:

```text
shadow-fixed-notional-paired-record-v1
```

This is the candidate-level comparison root. It contains:

- deterministic paired-record ID;
- direct manifest, opportunity-set, candidate-set, snapshot, observation,
  frozen-state, fixed-policy, liquidity, label, cost, and calendar hashes;
- raw capture, candidate, feature evidence, portfolio policy/source, bars, and
  corporate actions transitively through the exact pair-input hash;
- one shared fixed-notional target and exclusion classification;
- exact control and challenger sizing plans;
- control and challenger primary and secondary lifecycles plus hashes;
- primary and secondary horizon identities;
- one optional shared admission/exclusion reason;
- `parity_verified=true`.

The pair record is deterministically derived in memory from one validated
`FixedNotionalPairInput`. It becomes trusted persisted evidence only after the
store reloads its exact input predecessor, re-derives the complete record, and
verifies every persisted dependency and parity invariant in Section 8.

### 7.7 `FixedNotionalLineageBundle`

Contract version:

```text
shadow-fixed-notional-lineage-v1
```

The lineage composes rather than reinterprets the RS-P2-014
`PortfolioLineageBundle`. It includes:

- exact base lineage object and hash;
- fixed-notional policy and shared-input hashes;
- one shared liquidity-record hash covering its entry measurement and ordered
  exit measurements;
- integer bar-series hash;
- paired record hash;
- side lifecycle, holding, and ordered cash-flow hashes;
- `lineage_valid=true`.

The embedded base lineage is identity-bearing but is never trusted by itself.
The store reconstructs it from the exact `CandidateSetStore` and
`PortfolioArtifactStore` artifacts and requires equality. Corporate-action
policy, corporate-action event-set, and bar-source hashes are additionally
named as ordered predecessors on the BAR_SERIES reference. Reconstruction from
exact artifacts must reproduce the same canonical hash.

## 8. Machine-enforced parity

### 8.1 Shared parity envelope

The producer constructs one immutable input before deriving either side. The
following are shared and cannot be supplied or overridden by one side:

- protocol/component/manifest identity;
- complete opportunity-set and raw-candidate identities;
- candidate and snapshot identities;
- frozen control `PortfolioState` object and hash;
- Rp13m target gross notional;
- lot size and sizing rule;
- complete CostAssumptions object/hash and cost-rounding rule;
- fixed-notional policy object/hash;
- causal liquidity source identity and capacity calculation;
- shared sizing/admission exclusion state and reason codes.

There are no pluggable side-evaluator callbacks in RS-P2-015. The complete
control and challenger result is a pure deterministic derivation of the same
hash-bound input. `verify_paired_fixed_notional_record()` derives the expected
record again, and the immutable store invokes it at the persistence/load trust
boundary. Mutation, coordinated arithmetic drift, or hash drift is a
fail-closed `ShadowContractError`.

### 8.2 Geometry handling

Sizing uses the planned integer `entry_high` of an actionable decision. A side
that does not act has no trade denominator and receives FN6 treatment.

Where both sides act, the pair may contain distinct signal geometry only if the
fixed-notional target, policy, cost model, state, and capacity treatment remain
identical. A pair-level admission/exclusion mismatch is never accepted. If one
side would be `NOT_SIZEABLE_FIXED_NOTIONAL` or
`NOT_ESTIMABLE_ENTRY_CAPACITY` while the other would not, the producer fails
closed instead of persisting asymmetric exclusions.

Lifecycle outcomes may legitimately diverge because decisions or geometry
produce different fills/exit sessions. An exit-capacity record is therefore
compared for parity whenever the sides share the same ticker, session,
quantity, and price basis. Different causal exit sessions are retained as
different outcome evidence, not mislabeled as shared-input parity.

### 8.3 Allowed side differences

Only outcome-relevant consequences of the already recorded side decisions may
differ:

- action versus non-action;
- side decision reason and rating;
- signal geometry;
- fill/terminal outcome;
- lifecycle-specific exit session and its causally prior liquidity record;
- exact P&L and return derived from those differences.

No side may change the notional, lot, cost, source, capacity algorithm, frozen
state, or opportunity set.

## 9. Producer flow

The implemented fixed-notional producer and store perform this order:

1. Revalidate the manifest-v2 model and reload current governance
   authorization.
2. Verify the manifest-bound `FrozenPortfolioPolicy`, the additive
   `FrozenFixedNotionalPolicy`, and their predecessor/content-hash bindings.
3. Load the exact RS-P2-014 state and paired observation substrate.
4. Verify raw capture, opportunity set, candidate, snapshot, state, policy,
   observation, calendar, label, cost, corporate-action, and liquidity
   identities and chronology.
5. Build and hash one immutable `FixedNotionalPairInput`.
6. Mechanically derive per-side lots from integer `entry_high`, then apply the
   shared high-price and all-or-none entry-capacity rules.
7. Directly and deterministically derive control and challenger primary and
   secondary lifecycles; there are no side callbacks.
8. Apply manifest fill/expiry/gap/ambiguity/corporate-action rules using the
   exact-IDR artifact family and causally prior exit liquidity.
9. Mark only the primary 15-day holding/cash-flow records as eligible for a
   future RS-P2-017 consumer.
10. Verify exact holding/cash-flow arithmetic, denominators, ratios, chronology,
    and paired parity.
11. Before accepting persistence, reconstruct the RS-P2-014 base lineage from
    `CandidateSetStore` and `PortfolioArtifactStore` rather than trusting the
    caller's embedded lineage.
12. Build the fixed-notional lineage bundle, then persist every independently
    addressable node and reference with exclusive create.
13. Reload every node/reference, re-derive the complete paired result from the
    exact PairInput, and reconstruct the lineage again.

Authorization must be checked before the first evaluation and again before
maturation. Closure and fixed-terminal rules remain governed by the external
approval ledger; this pass creates no approval or collection event.

## 10. Immutable storage and replay

The additive store follows the existing RS-P2-014 namespace:

```text
protocols/
└── {protocol_id}/
    └── {manifest_canonical_sha256}/
        └── fixed_notional/
            ├── {kind_lower}/
            │   └── {canonical_sha256}/{raw_sha256}.json
            └── refs/
                └── {kind_lower}/
                    └── {artifact_id}.json
```

The exact `kind_lower` namespaces are `policy`, `liquidity`, `bar_series`,
`input`, `holding`, `cash_flow`, `lifecycle`, `paired_record`, and `lineage`.
Non-lineage references use `FixedNotionalGraphReference`; lineage uses
`FixedNotionalLineageReference`. Both carry deterministically sorted,
name-addressed predecessor hashes.

Every reference binds:

- artifact ID and contract version;
- canonical SHA-256;
- raw-file SHA-256;
- raw byte length;
- canonical relative path;
- protocol/component/manifest identity;
- relevant predecessor hashes.

Loaders reject:

- duplicate JSON keys;
- invalid UTF-8 or non-object roots;
- noncanonical raw bytes;
- unknown fields or wrong contract version;
- lowercase-SHA violations;
- byte-length, canonical-hash, or raw-hash mismatch;
- path traversal or noncanonical reference paths;
- semantic predecessor/lineage mismatch.

Exclusive create is idempotent only when the existing bytes are identical.
Same identity with different bytes is an immutable collision error.

Replay from the same exact inputs must reproduce identical IDs, records,
cash-flow sequence, ratios, and hashes. Tests cover same-process idempotency
and separate-process policy, pair-input, lifecycle, and paired-record hash
determinism.

## 11. Status and reason-code registry

The following meanings are frozen:

| Code | Meaning | Sample treatment |
|---|---|---|
| `NOT_SIZEABLE_FIXED_NOTIONAL` | One lot at planned `entry_high` exceeds Rp13m. | `NOT_ESTIMABLE`; no zero-size holding; count explicitly. |
| `NOT_ESTIMABLE_ENTRY_CAPACITY` | Full desired entry does not fit, or mandatory entry capacity is unavailable/stale. | `NOT_ESTIMABLE`; excluded from estimable-return denominator; count explicitly. |
| `NOT_ESTIMABLE_EXIT_CAPACITY` | Full exit does not fit, or mandatory exit capacity is unavailable/stale. | Honest censoring; excluded from estimable-return denominator; mandatory RS-P2-018 count/rate. |
| `NO_ACTION_CONTROL` | Control decision makes no transaction. | Opportunity P&L Rp0; sleeve return 0; trade metrics `NOT_ESTIMABLE`. |
| `NO_ACTION_CHALLENGER` | Challenger decision makes no transaction. | Same metric semantics as control non-action. |
| `EXPIRED_UNFILLED` | Manifest-valid order never fills before expiry. | Preserve manifest outcome; cash result Rp0; fill/trade metrics `NOT_ESTIMABLE`. |
| `NON_INTEGER_IDR_PRICE` | Required price is not exact integer IDR. | Fail closed or `NOT_ESTIMABLE` based on malformed-artifact versus missing-measurement boundary; never round. |
| `PARITY_MISMATCH` | Shared opportunity/snapshot/state/notional/cost/admission exclusion differs. | Raise fail-closed error; do not persist a purported valid pair. |
| `LINEAGE_MISMATCH` | Exact predecessor or source reconstruction fails. | Raise fail-closed error. |

Manifest-defined outcome reason codes remain authoritative for fills, gaps,
ambiguity, target, stop, timeout, rights, dividends, and exceptional events.
RS-P2-015 must not rename them into a new taxonomy.

## 12. Manifest-v2 sufficiency

Manifest v2 is sufficient and requires no schema field addition:

| Requirement | Existing v2 representation |
|---|---|
| Exact fixed-notional policy bytes | `ContentHash` with `role=CONFIG` in both control and challenger content hashes |
| Policy scalar decisions | uniquely named `FrozenParameter`s |
| ADTV and bar sources | `SourceDefinition`s with vintage, expiry, missing policy, and source hashes |
| Fixed universe | `UniverseDefinition` |
| Fill/outcome labels | `LabelDefinition` |
| Costs and lot | `CostAssumptions` |
| Calendar/corporate actions | existing manifest SHA-256 fields |
| Hypothesis/split/sample/safety methodology | external methodology document path + SHA-256 |
| GO/CONTINUE/NO-GO | hashed `GoNoGoRules` |

An eventual real component manifest must include the same
`config/fixed-notional-policy-v1.json` content hash on both sides. The config,
parameters, source definitions, and methodology hash are bound before A1.

This pass creates contracts, test fixtures, and storage capability only. It
does not create or approve a real C1/C7 component manifest.

The existing RS-P2-014 portfolio capability remains closed, and the additive
fixed-notional policy must report
`RS_P2_015_IMPLEMENTED_NOT_A1_ELIGIBLE`. RS-P2-015 alone does not authorize
A1. RS-P2-016–025 remain checklist-open, and every real component manifest
must separately satisfy the prerequisites applicable to that component before
a distinct owner A1 approval.

## 13. Explicit non-reinterpretation of v1

The following are prohibited:

- changing `ShadowOutcome-v1`;
- treating its float money or quantity fields as exact integer-IDR values;
- multiplying its one-lot money values by `desired_lots`;
- changing the one-lot evaluator in `outcome_engine.py`;
- loading an artifact with `contract_version=shadow-outcome-v1` into an
  RS-P2-015 loader;
- relabeling historical `shadow-evaluation-v1` output as fixed-notional
  evidence;
- pooling historical one-lot rows into an RS-P2-015 prospective cohort.

The new evaluator may consume independently verified immutable upstream
evidence such as the candidate, snapshot, calendar, raw-as-traded bars, and
corporate-action records. It must revalidate integer-IDR prices and build new
RS-P2-015 artifacts from those sources. Reuse of source evidence is not reuse
of the v1 outcome.

Historical one-lot output remains readable under its original contract and
meaning only.

## 14. Implemented test matrix

### 14.1 Policy and owner decisions

- exact FN1 sizing at integer `entry_high`;
- exact FN2 pre-cost gross ceiling and over-Rp13m total debit;
- FN3 residual cash and all three denominators;
- FN4 all-or-none entry and exit;
- FN5 Rp130,000 boundary and Rp130,001 exclusion;
- FN6 no-action metrics;
- FN7 exit censoring/countable reason;
- FN8 only 15-day NAV eligibility;
- FN-N1 effective-universe boundary;
- FN-N2 separate gross/cost/debit fields;
- FN-N3 censoring never becomes zero outcome.

### 14.2 Exact arithmetic

- lot-floor exactness around every boundary;
- no quantity increase after a favorable fill;
- aggregate bps then one adverse ceiling;
- a 1-IDR gross, cost, cash-flow, P&L, or denominator drift is rejected;
- fractional IDR market/dividend/corporate-action cash is never rounded;
- ratios require strict finite float and exact 12-place quantization;
- negative zero normalizes to `0.0`.

### 14.3 Parity

- same opportunity set, candidate, snapshot, frozen state, notional, lot,
  policy, cost, and shared admission exclusion for both sides;
- mismatch in any shared field raises before persistence;
- both sides are deterministically derived from the same immutable input hash;
- coordinated record drift is rejected by exact semantic replay;
- persisted state is reconstructed and reloaded at the store trust boundary;
- high-price and entry-capacity exclusions are identical;
- same-session exit-capacity calculation is identical;
- action/non-action divergence preserves FN6 without changing shared inputs.

### 14.4 Capacity and chronology

- entry ADTV source must be available and unexpired at signal;
- exit ADTV source must be available before exit session;
- future-vintage, expired, missing, or hash-mismatched liquidity fails closed;
- capacity below one full order never generates a partial quantity;
- target/stop/timeout is not shifted to a later liquid session;
- T+2 settlement uses exact completed IDX sessions;
- authorization is verified before PairInput production and again before
  deterministic maturation.

### 14.5 Tamper and immutable storage

- duplicate JSON keys rejected;
- noncanonical reformatting rejected by raw identity;
- canonical-only and raw-only tamper rejected;
- byte-length mismatch rejected;
- reference path traversal and namespace mismatch rejected;
- same ID/different bytes collision rejected;
- policy, state, observation, liquidity, lifecycle, holding, cash-flow, and
  lineage tamper rejected;
- missing predecessor artifact fails closed.

### 14.6 Replay

- same input replay is idempotent;
- candidate ordering cannot change per-candidate fixed result;
- source revision produces a new identity, never overwrites;
- policy, PairInput, lifecycle, and paired-record hashes are deterministic in a
  separate Python process;
- reconstructed fixed-notional lineage equals the persisted lineage.

### 14.7 Authority and compatibility

- every new artifact enforces `evaluation_only=true`;
- every new artifact enforces `live_authority=false`;
- every new artifact enforces all `affects_*` fields false;
- a forecasting-v1 fixture is explicitly rejected at the fixed-notional
  PairInput loader boundary; every other loader requires its own exact contract
  version;
- existing `contracts.py`, `calendar.py`, and `outcome_engine.py` remain
  byte-identical;
- RS-P2-014 state and paired-view behavior remains regression-green.

### 14.8 Executable evidence map

The executable evidence is in
`tests/test_shadow_protocol_p2_015.py`:

- FN1/FN-N1 sizing and the Rp130,000 boundary:
  `test_fixed_notional_lot_floor_boundaries`,
  `test_high_price_exclusion_is_identical_and_zero_size_is_not_persisted`,
  and `test_eligible_side_geometry_may_differ_but_exclusion_mismatch_fails`;
- FN2/FN-N2 gross-before-cost semantics:
  `test_fn_n2_entry_cost_is_separate_and_debit_can_exceed_sleeve`;
- FN3 residual cash and explicit denominators:
  `test_fn3_residual_is_idle_cash_and_sleeve_return_keeps_13m_denominator`;
- FN4/FN7 capacity and honest censoring:
  `test_entry_capacity_is_all_or_none_and_shared` and
  `test_exit_capacity_censors_instead_of_assuming_exit`;
- FN6 no-action/unfilled semantics:
  `test_non_action_is_zero_opportunity_but_trade_metrics_not_estimable` and
  `test_unfilled_preserves_distinct_label_and_no_trade_metrics`;
- FN8 primary-only NAV eligibility:
  `test_secondary_horizons_never_create_nav_events`;
- 1-IDR drift:
  `test_one_idr_pnl_drift_is_rejected` and
  `test_coordinated_one_idr_risk_basis_drift_fails_exact_replay`;
- corporate-action/source causality: the signal-time action-hash, dividend
  convention, non-session split, exact ADTV window, stale-source, and capture
  chronology tests;
- lifecycle chronology and T+2: the maturity-before-signal,
  pairing-before-evaluation, and weekend/holiday settlement tests;
- tamper/storage: the base-lineage reconstruction, orphan predecessor, named
  predecessor, canonical/raw tamper, duplicate-key, byte-length, and
  namespace/path tests;
- replay and cross-process determinism:
  `test_replay_is_idempotent_and_detects_record_drift`,
  `test_policy_hash_is_identical_in_a_separate_python_process`, and
  `test_pair_lifecycle_and_record_hashes_are_identical_cross_process`;
- authority and compatibility:
  `test_all_new_artifacts_are_evaluation_only` and
  `test_forecasting_shadow_v1_is_not_reinterpreted_as_fixed_notional`.

FN-N3 requires the exit-capacity reason to survive as countable censoring
evidence. Actual aggregation and reporting of that count/rate remain
RS-P2-018 work.

## 15. Scope boundary and Definition of Done

RS-P2-015 is complete only when:

1. FN1–FN8 and FN-N1–FN-N3 are schema-bound and executable.
2. Both sides use one exact Rp13m gross-notional policy and one frozen
   RS-P2-014 state.
3. High-price, entry-capacity, and exit-capacity cases fail closed with exact
   reason codes.
4. Every persisted money value and cash flow is exact integer IDR.
5. Every cost uses aggregate-applicable-bps then one adverse ceiling.
6. Primary holdings/cash-flow output is immutable and exposes a hash-bound
   RS-P2-017 eligibility contract without making it a live order. Actual NAV
   ingestion and compatibility remain RS-P2-017 work.
7. Only the primary 15-day lifecycle is NAV-consumable.
8. Shared parity divergence is an error, not a warning.
9. Canonical/raw dual hashes, references, exclusive create, replay, and lineage
   are verified.
10. `ShadowOutcome-v1` and the one-lot evaluator are unchanged and are never
    interpreted as fixed-notional evidence.
11. Focused and full verification gates pass.
12. The master checklist records honest evidence and leaves RS-P2-016–018
    open.

Out of scope:

- independent path-dependent portfolio allocation or capital competition
  (RS-P2-016);
- daily marked-to-market NAV or true NAV drawdown (RS-P2-017);
- common portfolio metrics, DSR inputs, or censor-rate reporting engine
  (RS-P2-018);
- report cadence, unblinding, GO/NO-GO, or production promotion;
- any live authority, threshold, recommendation, ranking, or execution change;
- any real component A1 grant or collection start.

## 16. Approval record for this design

The owner approved:

- FN1–FN8 exactly as Sections 4.1–4.8;
- FN-N1–FN-N3 exactly as Section 5;
- the additive artifact family described here;
- non-reuse of the one-lot evaluator as fixed-notional authority;
- no change or reinterpretation of `ShadowOutcome-v1`;
- `ALL_OR_NONE` as the sole permitted resolution of the RS-P2-014
  `TASK_GATED_TO_RS_P2_015` literal.

This implementation approval is not A1. It authorizes code, tests, immutable
test artifacts, documentation, and commits for RS-P2-015 only.

## 17. Implementation and verification evidence

### 17.1 Implemented boundary

- Owner decisions FN1–FN8 and notes FN-N1–FN-N3 are literal/schema-bound in
  `FrozenFixedNotionalPolicy`.
- `FixedNotionalPairInput` binds the exact manifest, raw capture, candidate set,
  candidate, snapshot, RS-P2-014 state, observation, fixed-notional policy,
  liquidity evidence, frozen trading calendar, integer bar series, labels, and
  costs.
- `derive_paired_fixed_notional_record()` is the pure deterministic derivation;
  `verify_paired_fixed_notional_record()` re-derives and compares the complete
  record at the immutable-store trust boundary.
- `FixedNotionalArtifactStore` persists canonical and raw identities under the
  content-addressed paths in Section 10, writes ordered named-predecessor
  references, reconstructs the RS-P2-014 base lineage from persisted substrate,
  and reloads every independent node before reporting success.
- `ProtocolGovernanceStore.verify_fixed_notional_maturation_authorization()`
  reloads current A1/closure state using the actual attempted time. Its
  existence does not grant A1.
- The only RS-P2-014 policy literal changed is the owner-approved resolution
  from `TASK_GATED_TO_RS_P2_015` to `ALL_OR_NONE`.

### 17.2 Verification gate

- RS-P2-015 test file: `57 passed`.
- Focused five-file shadow suite: `216 passed`.
- Full pytest: `1866 passed, 3 skipped`.
- Repository-wide `ruff check --fix .`: passed and changed no files.
- `uv lock --check`: passed.

### 17.3 SHA-256 evidence

Protected files remained byte-identical to the approved pre-pass boundary:

- `core/shadow_protocol/contracts.py`:
  `87b605fb9cc3cb3bee73d903110801699e06e63f4d41e9e8b94cdd48d0ee54b7`;
- `core/shadow_protocol/calendar.py`:
  `fe27a4e5c964c26f3093921193f29ec45f4f4c09f620b52ca94806ab302c7151`;
- `core/shadow_protocol/outcome_engine.py`:
  `b90d149df67d91f59408618e580c75f55d2de2257cf6e5f46f4265dffaaa27a8`.

Final changed/new Python and test hashes:

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

### 17.4 Hard scope boundary

No real component manifest, A1 approval, approval-ledger event, collection
cohort, unblinding, threshold change, decision-logic change, `trade_math.py`
change, baseline change, or live/ranking/sizing/execution authority was
created. Synthetic temporary governance fixtures in tests are not a granted A1
or collection cohort. `shadow-evaluation-v1` remains unchanged and is not
reinterpreted. RS-P2-016, RS-P2-017, RS-P2-018, and RS-P2-019–025 remain open.

Implementation commit:
`6e459c8a15b439f35e46d4791db1ddbbcb2d92af`.
The documentation commit is reported in the external handoff after this file
is committed; it is intentionally not embedded here to avoid self-reference.
