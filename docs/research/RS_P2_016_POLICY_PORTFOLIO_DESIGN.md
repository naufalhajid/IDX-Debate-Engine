# RS-P2-016 Independent Policy-Portfolio Design

**Status:** IMPLEMENTED — VERIFICATION GATE GREEN; A1 NOT GRANTED
**Decision date:** 2026-07-18
**Target:** RS-P2-016 only
**Predecessors:** RS-P2-014 frozen portfolio state/paired decision view and
RS-P2-015 fixed-notional signal-isolation view
**Successors:** RS-P2-017 daily marked-to-market NAV and RS-P2-018 common
metrics remain out of scope
**Authority:** evaluation only; no A1 grant, collection, unblinding,
promotion, live execution, ranking, sizing, or decision authority

## 1. Executive decision

RS-P2-016 adds a third, distinct paired view:

1. RS-P2-014 keeps the **decision view** against one read-only frozen control
   state.
2. RS-P2-015 keeps the **fixed-notional view** at Rp13,000,000 per side to
   isolate signal quality.
3. RS-P2-016 adds the **policy-portfolio view**, in which control and
   challenger start from identical Rp100,000,000 genesis states and then
   evolve independently under identical frozen risk, cost, calendar,
   accounting, priority, and transition rules.

This directly implements the third view required by
`SHADOW_MODE_PROTOCOL.md:86-105`, especially the independent/path-dependent
requirement at `SHADOW_MODE_PROTOCOL.md:101`. The master-checklist task is
`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:442-443`.

The policy portfolio is a **formalization of the current control**, not a
claim that the live control already has a persistent portfolio engine. The
live path is batch-oriented and leaves several path-dependent cases
undefined. Every formalization below applies identically to control and
challenger, so the comparison remains paired even when their states diverge
because their recorded decisions differ.

RS-P2-016 does not generate the canonical daily NAV series. It persists the
cash, payable, receivable, holding, commitment, risk, and session-transition
ingredients from which RS-P2-017 must later produce daily marked-to-market
NAV. Portfolio Sharpe, drawdown, DSR inputs, and other time-series metrics
remain unavailable until RS-P2-017/018.

## 2. Normative boundary

The normative hierarchy for this pass is:

1. the owner-approved PP1–PP14 decisions in Section 4;
2. PP-A1 and PP-N1–PP-N3 in Sections 5–8;
3. `SHADOW_MODE_PROTOCOL.md:86-105`, which defines the three views and
   requires daily NAV for portfolio time-series metrics;
4. the task wording at
   `RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:436-447`;
5. the already frozen RS-P2-014/015 contracts and their hashes.

When live behavior is undefined, this design does not silently call an
invented behavior “control parity.” It records the deviation as a symmetric
formalization. When an input needed by the formal policy cannot be
reconstructed, the policy view fails closed or reports an explicit
`NOT_ESTIMABLE` reason; it never substitutes a favorable value.

The following remain prohibited:

- changes to any live threshold or decision path;
- changes to `trade_math.py`;
- exchange orders or live portfolio mutation;
- A1 approval, collection, unblinding, promotion, or baseline replacement;
- reinterpretation of `shadow-evaluation-v1`;
- reinterpretation of the RS-P2-014 `PortfolioState` as an evolving
  portfolio;
- generation of a synthetic terminal liquidation;
- creation of a daily NAV series or portfolio-performance metric before
  RS-P2-017/018.

## 3. Frozen predecessor contracts

### 3.1 RS-P2-014 policy

The owner-approved policy already freezes:

- starting capital Rp100,000,000;
- fixed notional Rp13,000,000;
- integer-IDR money state and one adverse ceiling for aggregated applicable
  costs;
- lot size 100;
- minimum ADTV20 Rp10,000,000,000 and participation cap 0.13%;
- target deployment 65% as a sizing basis;
- minimum cash 5% and maximum gross exposure 95%;
- base maximum positions 5 and BULL/SIDEWAYS/BEAR_STRESS/UNKNOWN limits
  3/2/1/0;
- total loss budget 2%;
- portfolio heat 1.3%;
- daily realized-loss stop 3%;
- sector and cluster maximum two names;
- long-only, no leverage;
- T+2 settlement on the frozen IDX calendar;
- true NAV drawdown and sector-exposure fraction as `NOT_ESTIMABLE`.

The RS-P2-014 `PortfolioState` remains a
`CONTROL_FROZEN_REFERENCE`/sequence-zero snapshot. It is not mutated or
reinterpreted by this pass.

### 3.2 RS-P2-015 fixed-notional evidence

RS-P2-015 remains the signal-isolation view. Its Rp13,000,000 cash-flow
records are not multiplied or scaled into a policy portfolio. RS-P2-016
replays the raw, hash-bound `PairInput` at each side's policy quantity so
that lot rounding, costs, capacity, payable, holding, and exit evidence are
recomputed at the actual policy size.

Only the primary 15-day lifecycle is eligible to supply lifecycle evidence to
the future NAV path. Secondary 3/5/10-day results never create extra
positions or cash flows.

### 3.3 Shared identity, independent evolution

At genesis, both sides must bind the same:

- protocol and exact manifest hash;
- baseline manifest;
- frozen calendar;
- cost assumptions;
- methodology document;
- frozen portfolio policy;
- policy-portfolio policy;
- opportunity/candidate input;
- starting capital;
- genesis session and timestamp.

After genesis, control and challenger maintain separate predecessor chains.
State divergence is valid only when it is causally explained by their
recorded decisions or by subsequent consequences of those decisions. Shared
input, source, calendar, cost, policy, and authority divergence is a
fail-closed contract error.

## 4. Approved owner decisions PP1–PP14

### PP1 — identical genesis

Both sides start from:

- settled cash Rp100,000,000;
- zero unsettled sale receivable;
- zero unsettled purchase payable;
- zero reservation;
- no holdings;
- no pending commitments;
- realized P&L for the session equal to zero;
- an inactive daily-loss latch;
- the close of the frozen IDX session immediately preceding the first
  policy-transition session (`GENESIS_ANCHOR`).

Observed live holdings are never imported into genesis.

> **Documentation erratum — 2026-07-19 (NV-N1).** The former “opening of
> the first frozen IDX session” wording contradicted the already implemented
> and tested contract. Normative genesis is the close of the immediately
> preceding frozen IDX session:
> `genesis_at == session_close_at(genesis_session)`. This is a prose
> correction only. It does not change `policy_portfolio.py`, any persisted
> state identity, economics, decision logic, or authority. RS-P2-017 treats
> this close as `GENESIS_ANCHOR`; its return is
> `NOT_ESTIMABLE_NO_PREDECESSOR`.

### PP2 — recorded-position sizing authority

For a side with `would_allocate=true`, requested size comes from that side's
`recorded_position_fraction`:

- control uses a `CONTROL_OBSERVED` size basis;
- challenger uses a `COUNTERFACTUAL` size basis;
- missing or invalid size produces `NOT_ESTIMABLE_POLICY_SIZE`;
- no Rp13,000,000 fallback is allowed;
- no silent zero-size allocation is allowed.

Requested notional is computed against frozen starting capital, then
converted to exact board-lot quantity at the planned integer entry price.
The engine replays costs and capacity at that quantity.

### PP3 — deterministic admission priority

Competing candidates are ordered separately for each side by:

```text
recorded_rank ASC
→ source_row_number ASC
→ ticker ASC
```

For allocating decisions, missing rank or an ambiguous duplicate competitive
rank produces `NOT_ESTIMABLE_PRIORITY`. Input iteration order, dictionary
order, or filesystem order can never become a hidden tie-break.

### PP4 — hard sector and cluster caps

The two-name sector and two-name cluster limits are hard caps:

- no live-style soft overflow is allowed;
- open positions and pending commitments both consume the count;
- point-in-time sector and cluster classification must be hash-bound;
- missing or unverifiable classification produces
  `NOT_ESTIMABLE_CLASSIFICATION`.

### PP5 — atomic ten-step gate order

Every candidate is evaluated against one immutable pre-candidate state. All
applicable gate results and reasons are recorded before any mutation:

1. integrity, lineage, chronology, and estimability;
2. daily realized-loss stop;
3. allocation size and duplicate/re-entry eligibility;
4. regime/slot limit;
5. sector/cluster count limits;
6. point-in-time liquidity/capacity;
7. per-position and aggregate loss budget;
8. portfolio heat;
9. gross exposure;
10. minimum cash/buying power.

Only a candidate passing every required gate creates a commitment. The next
candidate in PP3 order sees the resulting state.

### PP6 — regime transition

A point-in-time, hash-bound regime record is shared by both sides:

- a newly observed regime becomes effective at the opening of the next frozen
  IDX session;
- missing, stale, or hash-mismatched regime evidence maps to `UNKNOWN`;
- `UNKNOWN` permits zero new allocations;
- a lower limit never forces an existing filled position to exit;
- existing positions remain until a natural target, stop, timeout, or other
  manifest-valid terminal event;
- pending commitments are re-evaluated and canceled when the new regime no
  longer permits them;
- new allocations remain blocked until open plus pending count is below the
  effective limit.

### PP7 — commitment and reservation lifecycle

When a candidate passes admission:

- create an immutable pending commitment;
- reserve worst-case planned gross debit plus exact entry cost;
- count the commitment toward slot, sector, cluster, gross exposure, planned
  risk, heat, and loss-budget gates;
- at fill, re-check every dynamic safety constraint and point-in-time
  capacity. Slot, sector/cluster, planned risk, heat, and gross resources are
  already reserved by the commitment and are revalidated against their
  journaled resource state; daily stop, regime, duplicate-ticker,
  classification freshness, exact-session ENTRY liquidity, gross exposure,
  loss budget, heat, and cash remain fail-closed;
- require a distinct hash-bound `PolicySessionLiquidityRecord` for the exact
  activation/fill session. The RS-P2-015 signal-time measurement, a stale
  earlier-session measurement, or an EXIT-role measurement can never be
  silently relabeled as entry-capacity evidence;
- atomically replace the commitment with the holding;
- never double-count commitment and holding;
- on expiry, unfilled outcome, failed re-check, regime cancellation, or daily
  stop, release the reservation with an explicit reason.

### PP8 — T+2 payable and receivable

The economic accounting identity is:

```text
NAV =
    settled cash
  + unsettled sale receivable
  - unsettled purchase payable
  + marked holdings
```

The P2-016 state must preserve every ingredient, but RS-P2-017 remains
responsible for constructing the canonical daily marked-holdings/NAV series.

At a valid entry fill:

- recognize the holding on trade date;
- recognize the exact purchase payable on trade date;
- preserve the order-time reservation until it is atomically replaced by the
  filled accounting state;
- settle the purchase payable against settled cash at the opening of T+2.

At a valid exit:

- remove the sold holding according to the frozen event ordering;
- recognize net sale proceeds as a receivable on trade date;
- make those proceeds settled and deployable only at the opening of T+2.

Receivables are never deployable before settlement. Payables can never be
omitted from equity merely because legal cash settlement occurs later.

### PP9 — deterministic session event order

For every frozen session:

1. reset the new-session realized-P&L accumulator and daily-loss latch;
2. post T+2 payables and receivables;
3. apply supported split/corporate-action opening effects;
4. process explicit opening exits;
5. process opening fills in PP3 priority;
6. process close/session-only exits and eligible dividends;
7. process session-only fills;
8. expire or cancel commitments;
9. admit new signals in PP3 order;
10. persist the immutable end-of-session state and transition.

New signals are admitted only after the current session's fills, exits,
settlements, and cancellations. They create commitments for later activation
sessions and can never fill in the signal session.

When source evidence identifies only a session and cannot prove intraday
order, adverse ordering applies:

- a loss-causing exit may activate the daily stop;
- resources released by an exit cannot be reused optimistically by an entry
  at the same ambiguous timestamp;
- no same-timestamp reuse of proceeds, slots, or heat capacity is credited.

### PP10 — daily realized-loss stop

The 3% threshold:

- uses net realized economic P&L after costs;
- includes eligible dividend cash in session realized P&L;
- allows realized gains to offset realized losses in that session;
- excludes unrealized MTM movements;
- triggers at loss `>=3%` of starting capital;
- latches until the end of that IDX session;
- blocks new commitments and fills after trigger;
- cancels all not-yet-filled commitments and releases their reservations;
- does not force-liquidate open positions;
- resets only at the opening of the next frozen IDX session.

Ambiguous same-session chronology uses adverse/loss-first ordering.

### PP11 — one position per ticker and re-entry

- At most one open position or pending commitment may exist per ticker and
  side.
- A new signal while that ticker is open or pending is rejected.
- No same-session re-entry is permitted after exit.
- A later re-entry requires a new signal no earlier than the next IDX
  session.
- No additional multi-session cooldown is introduced.
- Pyramiding is not supported.

### PP12 — planned-risk heat and loss budget

For open positions and pending commitments:

```text
planned risk IDR =
    quantity × (planned entry high − planned stop)

portfolio heat =
    aggregate planned risk IDR / starting capital IDR
```

The rules are:

- planned risk is frozen from the approved geometry and is not reduced by a
  favorable fill;
- open and pending risks both count;
- heat is a hard 1.3% of starting capital cap;
- aggregate planned risk may not exceed the 2% total loss budget;
- per-position risk cap is `2% / active regime maximum slots`;
- transaction costs remain separately recorded and do not enter the
  stop-distance numerator;
- missing stop or risk input blocks allocation.

Section 5 records PP-A1's mandatory semantic correction to the heat unit.

### PP13 — fail-closed path propagation

- A failure before fill produces no position.
- A failure after a valid fill never fabricates an exit, sale proceeds, a
  released slot, or released risk.
- A post-fill failure such as `NOT_ESTIMABLE_EXIT_CAPACITY` retains the last
  verified holding and cash state.
- The affected side becomes `NOT_ESTIMABLE_FROM_SESSION`.
- No later allocation or promotion-grade NAV/metrics may be claimed until the
  lineage is reconciled.
- Control and challenger estimability may diverge, but shared-input integrity
  may not.

### PP14 — fixed-terminal behavior

- There is no synthetic or forced terminal liquidation.
- A new entry is rejected when the frozen calendar lacks enough runway for
  remaining entry validity, the primary 15-session lifecycle, and T+2
  settlement.
- A position still unresolved at the terminal remains open and explicitly
  `NOT_ESTIMABLE`; it is not silently treated as closed.
- Terminal marking and the daily NAV series remain RS-P2-017 work.

## 5. PP-A1 — mandatory heat-unit correction

### 5.1 Normative decision

The heat denominator is **starting capital**, not current NAV. The numeric
threshold remains `0.013`; only the semantic label is corrected.

The earlier owner wording “1.3% NAV” is non-normative after PP-A1. No
parameter may remain labeled `fraction_of_NAV` while being evaluated against
starting capital.

### 5.2 Existing contract evidence

The existing RS-P2-014 code already enforces the corrected semantics:

- `FrozenPortfolioPolicy.portfolio_heat_denominator` is the literal
  `STARTING_CAPITAL` at `core/shadow_protocol/portfolio.py:304-305`;
- state validation divides open risk by starting capital at
  `core/shadow_protocol/portfolio.py:978-983`;
- the state builder uses the same denominator at
  `core/shadow_protocol/portfolio.py:1659-1664`;
- `portfolio_manifest_parameters()` emits unit
  `fraction_of_starting_capital` at
  `core/shadow_protocol/portfolio.py:1346-1349`;
- manifest binding rejects value or unit drift at
  `core/shadow_protocol/portfolio.py:1459-1473`.

The canonical parent policy CONFIG is therefore semantically:

```json
{
  "max_portfolio_heat_fraction": 0.013,
  "portfolio_heat_denominator": "STARTING_CAPITAL"
}
```

The exact manifest parameter is:

```json
{
  "name": "max_portfolio_heat_fraction",
  "value": 0.013,
  "unit": "fraction_of_starting_capital",
  "source": "owner decision V2"
}
```

The source field preserves the historical owner-decision identity; this
document records PP-A1 as the later semantic correction. The additive P2-016
policy CONFIG must also bind an exact literal equivalent to:

```json
{
  "portfolio_heat_unit": "fraction_of_starting_capital"
}
```

It must reference and revalidate the parent `FrozenPortfolioPolicy` hash.

### 5.3 What PP-A1 does not change

PP-A1 does not relabel unrelated NAV concepts:

- gross exposure remains a fraction of point-in-time accounting equity:
  settled cash plus receivables minus payables plus currently verified holding
  marks. This gate-only denominator is the economic NAV identity at one
  transition, but is deliberately labeled
  `POINT_IN_TIME_ACCOUNTING_EQUITY_NOT_RS_P2_017_NAV_SERIES`; it is neither a
  starting-capital proxy nor a claimed daily NAV/return series. A missing
  current mark makes the gate/path `NOT_ESTIMABLE`;
- true NAV drawdown remains a NAV concept and `NOT_ESTIMABLE` until
  RS-P2-017 plus a new protocol;
- sector-exposure fraction remains `NOT_ESTIMABLE`;
- the heat number remains 0.013;
- starting capital remains Rp100,000,000.

### 5.4 Manifest version decision

Manifest v3 is not required. Manifest v2 already provides:

- extensible, uniquely named `FrozenParameter`s
  (`core/shadow_protocol/contracts.py:207-216,403,443-445`);
- control/challenger content hashes;
- mandatory methodology binding;
- calendar, costs, labels, terminal date, and source identities.

A future real component must freeze a new **v2 manifest revision/hash** that
includes the P2-016 CONFIG on both sides. That is a content revision, not a
schema-v3 migration. This pass creates no real component manifest or A1.

## 6. PP-N1 — control formalization and explicit deviations

The policy view must never be described as a byte-for-byte simulation of the
live portfolio path. The live path has no equivalent persistent state
machine. The following deviations are intentional, owner-approved
formalizations applied identically to both sides:

| Area | Current live control | RS-P2-016 formalization | Why paired validity is preserved |
|---|---|---|---|
| Persistent state | Batch sizing; no complete path-dependent cash, commitment, holding, payable, and receivable ledger. | Independent immutable control/challenger predecessor chains from identical Rp100m genesis. | Both sides receive the same state-machine contract; only recorded decisions create divergence. |
| Admission priority | `select_top_n` orders by conviction (`core/orchestrator/legacy.py:5889-5912`), then the position sizer reorders by rating/confidence/R/R (`core/quant_filter/position_sizer.py:127-132,483-489`). | Side-specific `recorded_rank`, then source row, then ticker; missing/ambiguous priority fails closed. | Each side's recorded policy output is honored under one deterministic tie-break contract. |
| Sector/cluster cap | Greedy cap followed by a soft fallback that may exceed the cap (`core/portfolio_optimizer.py:113-123`). | Hard maximum two names for both sector and cluster; no overflow. | The stricter formal cap is identical for control and challenger and is explicitly disclosed. |
| Regime limit decreases | Undefined for already-open persistent positions. | No forced exit; cancel invalid pending commitments and block new entries until back under the effective limit. | The same transition rule applies to both sides. |
| Daily-loss lifecycle | Current circuit breaker blocks batch sizing; pending-order cancellation and next-session reset are not a persistent-state contract (`core/orchestrator/legacy.py:6257-6286`). | Session latch, cancel pending, no forced exit, reset next session. | Symmetric formal lifecycle prevents one side receiving favorable unstated handling. |
| Purchase payable | Live sizing can accept aggregate `unsettled_capital`, but there is no exact purchase-payable ledger (`core/quant_filter/position_sizer.py:364-366`). | Exact trade-date payable, T+2 settlement, and NAV subtraction. | Both sides use the same accounting identity and cannot double-count capital. |
| Heat semantics | Historical V2 prose used “1.3% NAV,” while the implemented frozen contract already divides by starting capital. | Explicit `fraction_of_starting_capital` label and denominator under PP-A1. | Both sides bind the same exact denominator; no hidden NAV dependency. |

These deviations do not authorize a live change. They define only the
evaluation estimand used to compare two shadow policies.

## 7. PP-N2 — Phase-8 data-production requirement

The legacy `ShadowDecision` schema may retain optional fields for
backward-compatible artifact loading:

- `recorded_rank`;
- `recorded_position_fraction`.

P2-016 intake is stricter. For every decision with
`would_allocate=true`:

- `recorded_position_fraction` must be present, finite, in range, and paired
  with the correct side-specific `position_size_basis`;
- `recorded_rank` must be present and unambiguous within the competitive
  session cohort;
- missing fraction produces `NOT_ESTIMABLE_POLICY_SIZE`;
- missing or ambiguous rank produces `NOT_ESTIMABLE_PRIORITY`;
- no fallback size or order is permitted.

These are mandatory data-production requirements for Phase 8 prospective
collection. The contract fields may remain optional at
`core/shadow_protocol/contracts.py:906-909`; enforcement belongs at the
policy-view intake boundary.

## 8. PP-N3 — fixed-terminal runway taper

Before admitting a new commitment, the engine must use the frozen calendar
to prove sufficient remaining sessions for:

```text
remaining entry-validity sessions
+ 15 primary lifecycle sessions
+ 2 settlement sessions
```

The minimum practical taper is roughly 17 sessions and can be longer when
the manifest allows more than one entry-validity session. Therefore a decline
in policy-portfolio activity near the fixed terminal is expected protocol
behavior, not evidence that the signal engine failed.

The terminal check may not:

- shorten the primary horizon;
- assume immediate fill;
- omit T+2;
- create a synthetic exit;
- move an unresolved position into the closed sample;
- continue settlement beyond the registered terminal while claiming complete
  maturity.

The terminal date, label horizon, entry validity, settlement lag, and exact
calendar are already bindable under manifest v2.

## 9. Manifest-v2 and policy binding

### 9.1 Required shared CONFIG identities

The exact parent policy CONFIG and P2-016 policy CONFIG must each appear once
on both manifest sides under reserved canonical paths:

```text
control CONFIG:
  config/portfolio-policy-v1.json
  config/policy-portfolio-policy-v1.json

challenger CONFIG:
  config/portfolio-policy-v1.json
  config/policy-portfolio-policy-v1.json
```

The exact final P2-016 path/version names must match the implementation
constants. Any deviation from the reserved path, duplicate, missing side,
hash mismatch, or model mismatch fails closed.

### 9.2 Methodology binding

PP1–PP14, PP-A1, and PP-N1–PP-N3 are methodology, not a reason to add dozens
of narrative manifest fields. The manifest must bind this document or its
approved successor through:

- `methodology_document_path`;
- `methodology_document_sha256`.

Queryable scalar/literal decisions may additionally appear as exact
`FrozenParameter`s. Detailed transition formulas remain bound through the
canonical policy CONFIG and methodology hash.

### 9.3 No capability unlock

RS-P2-016 completion does not make a component A1-eligible. RS-P2-017/018 and
the remaining applicable Phase-2 storage/reporting tasks still remain open.
No capability literal may imply Phase 2 is complete.

## 10. Additive artifact family

The implemented models below are mapped to their concrete files and line
ranges in Section 19.1.

### 10.1 Frozen policy-portfolio policy

Required content includes:

- contract version and policy ID;
- parent `FrozenPortfolioPolicy` canonical SHA-256;
- methodology SHA-256;
- calendar, cost, corporate-action, and source hashes;
- identical-genesis rule;
- recorded-position sizing rule;
- exact priority rule;
- exact ten-step gate order;
- hard sector/cluster semantics;
- regime effective-time and down-transition rules;
- reservation/fill/recheck rules;
- T+2 payable/receivable rules;
- session event order and adverse-order rule;
- daily-stop latch/reset/cancellation rules;
- duplicate/re-entry rule;
- planned-risk formula and `fraction_of_starting_capital` heat unit;
- `NOT_ESTIMABLE` propagation rule;
- fixed-terminal runway rule;
- authority literals fixed to evaluation-only false-live values.

### 10.2 Regime record

The point-in-time regime artifact must bind:

- source ID/hash and record hash;
- observation timestamp and source as-of;
- effective session;
- exact normalized state:
  `BULL`, `SIDEWAYS`, `BEAR_STRESS`, or `UNKNOWN`;
- staleness/expiry decision;
- evaluation-only authority.

Missing or stale source evidence must be produced as an explicit `UNKNOWN`
record with a reason; a malformed or hash-mismatched record is rejected before
transition. Both paths permit zero new allocations and never inherit a
previous favorable regime silently.

### 10.3 Point-in-time liquidity and classification

`PolicySessionLiquidityRecord` binds one exact possible fill session, an
ENTRY-role measurement, its 20 causally prior turnover bars/hashes, exact
ADTV20 rational, and exact participation-cap rational. The producer rejects
EXIT-role reuse. Candidate input carries a canonically ordered, unique
per-session sequence, and fill recheck requires the record whose
`capacity_session` equals the actual activation session.

`PolicyCandidateClassification` binds the sector taxonomy, sector, cluster,
source record, source as-of, expiry, and cluster-rule hash. Future-dated,
expired, missing, or incomplete evidence is normalized to
`NOT_ESTIMABLE_CLASSIFICATION`; it cannot consume a fabricated default bucket.
Freshness is checked both at signal admission and again at fill.

### 10.4 Commitment

Each pending commitment needs:

- side, protocol, manifest, policy, session, candidate, snapshot, and
  decision identities;
- ticker, source row, recorded rank, and deterministic priority key;
- planned geometry and requested position fraction;
- planned integer quantity, gross IDR, entry cost, reserved debit, and
  planned risk;
- sector and cluster IDs/hashes;
- signal, validity, and prospective fill sessions;
- creation session, activation sessions, and immutable candidate/decision
  hashes; cancellation/terminal reasons live in ordered transition events;
- evaluation-only authority.

### 10.5 Holding

Each open holding needs:

- originating commitment and fill-event hashes;
- exact integer shares and integer-IDR entry basis;
- exact costs paid;
- trade date and settlement session;
- planned stop/target and planned risk frozen from admission;
- sector/cluster binding;
- supported split/corporate-action lineage;
- last verified state and estimability status;
- no live-order authority.

### 10.6 Payable and receivable

Every unsettled cash item needs:

- side and portfolio predecessor identity;
- originating fill/exit/corporate-action hash;
- signed economic role, but non-negative exact integer-IDR amount;
- trade/effective session;
- settlement session derived from the frozen calendar;
- posted/unposted status;
- no early deployability;
- exact canonical identity.

### 10.7 Policy-portfolio state

Every per-side state must bind:

- side and exact protocol/manifest/policies;
- state sequence and predecessor-state hash;
- frozen session and state timestamp;
- settled cash;
- unsettled sale receivables;
- unsettled purchase payables;
- reserved cash;
- pending commitments;
- open holdings;
- realized P&L for the session;
- daily-stop latch;
- effective and pending regime records;
- slots, sector/cluster counts, planned risk, heat, gross exposure, and
  deployable buying-power ingredients;
- estimability state/reason;
- evaluation-only authority.

All persisted money is strict integer IDR. Derived ratios are finite and
quantized using the already frozen rule.

### 10.8 Transition

Every session transition must preserve:

- exact full pre-state hash and exact post-state **payload** hash;
- ordered input/event hashes;
- ordered gate results;
- accepted/rejected/canceled commitments;
- fills, exits, corporate actions, payables, receivables, settlements, and
  reservation releases;
- reason codes;
- deterministic arithmetic reconciliation;
- session chronology;
- policy and authority identities.

The successor state then binds the full transition hash plus the same payload
hash. This two-way payload/transition identity avoids an impossible circular
full-state/full-transition hash while still making any mutation detectable.

### 10.9 Paired session envelope

The paired envelope binds:

- one shared session input/opportunity-set identity;
- one common genesis/policy/calendar/cost identity;
- control pre/post state;
- challenger pre/post state;
- allowed state divergence;
- shared-input parity proof;
- no claim that post-genesis state hashes must match.

## 11. Exact integer-IDR accounting

### 11.1 Money

Persisted money values are strict integer IDR:

- settled cash;
- reservation;
- purchase payable;
- sale receivable;
- gross fill/exit value;
- applied costs;
- holding basis/value inputs;
- realized P&L;
- planned risk.

Boolean values cannot pass as integers. Floats, NaN, infinity, strings, or
implicit coercions fail validation.

### 11.2 Costs

For each entry or exit:

1. collect every applicable frozen bps component;
2. aggregate bps once;
3. apply it to exact integer gross notional;
4. ceil once in the direction adverse to the portfolio.

Costs are recomputed at policy quantity. Fixed-notional costs are not scaled.

### 11.3 Ratios

Ratios use finite values and the frozen 12-decimal `ROUND_HALF_EVEN`
quantization. Ratios are recomputed from integer numerators/denominators and
cannot be trusted from payload fields.

### 11.4 Accounting reconciliation

Every transition must reconcile:

- reservation creation/release;
- payable creation/posting;
- receivable creation/posting;
- holding creation/removal;
- exact costs;
- settled cash delta;
- realized P&L;
- planned-risk and count deltas.

A coordinated one-IDR drift that preserves a superficial total but violates
any underlying event must be rejected.

### 11.5 Gate-only accounting equity versus RS-P2-017 NAV

P2-016 persists `accounting_equity_idr` solely to evaluate gross exposure and
to prove the exact cash/payable/receivable/mark identity:

```text
accounting_equity_idr =
    settled_cash_idr
  + sale_receivable_idr
  - purchase_payable_idr
  + marked_holdings_value_idr
```

Every holding mark must be point-in-time, current for the session, and
hash-bound through the candidate/bar lineage. Missing current mark evidence
poisons the affected path rather than substituting entry price, prior NAV, or
starting capital. The persisted `nav_metric_status` remains
`NOT_ESTIMABLE_UNTIL_RS_P2_017` and `nav_metric_value` remains null. Therefore
P2-016 does not claim a daily NAV series, returns, drawdown, or any
promotion-grade portfolio metric.

## 12. Independent transition engine

For each session:

1. load and revalidate the exact manifest, policies, methodology, calendar,
   sources, and prior states;
2. prove both sides use the same shared input identity;
3. derive the effective regime;
4. apply PP9 session-start settlements and events;
5. build each side's competitive candidate cohort;
6. enforce PP-N2 intake requirements;
7. sort each side by PP3;
8. evaluate each candidate atomically through PP5;
9. create commitments only after complete admission;
10. process fills/exits/corporate actions under PP7–PP10;
11. propagate PP13 estimability;
12. enforce PP14 runway and terminal rules;
13. reconcile exact arithmetic;
14. persist each immutable side transition/state;
15. persist the paired envelope and parity proof.

There are no pluggable side callbacks that may secretly use different
economic rules. Side-specific decision evidence is data; the transition
engine and policy are shared.

## 13. Parity versus valid divergence

### 13.1 Must be identical

- protocol/manifest identity;
- baseline identity;
- policy CONFIGs;
- methodology;
- starting capital;
- frozen calendar and session;
- cost rules;
- raw opportunity set and shared snapshots;
- sector/cluster source definitions;
- regime source evidence;
- corporate-action source evidence;
- gate and event order;
- arithmetic and authority rules.

### 13.2 May diverge

- recorded allocation decision;
- recorded rank and recorded position fraction;
- commitment existence;
- holding existence and quantity;
- later cash/payable/receivable path;
- later slots, heat, exposure, and buying power;
- later eligibility caused by path-dependent capital competition;
- side-specific estimability after a valid side-specific post-fill failure.

Unexpected shared-input divergence is an error, not a warning.

## 14. `NOT_ESTIMABLE` and reason registry

At minimum, the implementation must preserve countable reasons for:

| Reason | Meaning | State effect |
|---|---|---|
| `NOT_ESTIMABLE_POLICY_SIZE` | Allocating side lacks valid recorded fraction or exact policy quantity. | No commitment. |
| `NOT_ESTIMABLE_PRIORITY` | Missing or ambiguous allocating rank. | Affected competitive cohort cannot be ordered favorably. |
| `NOT_ESTIMABLE_CLASSIFICATION` | Sector/cluster source is missing, stale, or unverifiable. | No commitment. |
| `NOT_ESTIMABLE_REGIME` | Regime source is missing/stale/tampered. | Normalize to `UNKNOWN`; no new allocation. |
| `NOT_ESTIMABLE_ENTRY_CAPACITY` | Full policy quantity lacks valid point-in-time entry capacity. | No fill/position. |
| `NOT_ESTIMABLE_EXIT_CAPACITY` | A valid holding lacks a provable full-capacity exit. | Retain last verified holding; side freezes from session. |
| `NOT_ESTIMABLE_FROM_SESSION` | Post-fill lineage/accounting path cannot be completed without fabrication. | Preserve last verified state; block later allocations/metrics. |
| `INSUFFICIENT_FIXED_TERMINAL_RUNWAY` | Calendar cannot support entry validity, primary horizon, and T+2 before terminal. | No new commitment. |
| `DAILY_REALIZED_LOSS_STOP` | Session loss reached the frozen threshold. | Latch, cancel pending, no new fills/commitments. |
| `REGIME_LIMIT_REDUCTION` | Pending commitment no longer fits next-session regime limit. | Cancel pending; do not force-exit holdings. |
| `DUPLICATE_OR_REENTRY_BLOCKED` | Ticker is open/pending or same-session re-entry attempted. | No commitment. |
| `SECTOR_LIMIT` / `CLUSTER_LIMIT` | Hard two-name cap would be exceeded. | No commitment. |
| `PORTFOLIO_HEAT_LIMIT` | Planned risk / starting capital would exceed 0.013. | No commitment. |
| `TOTAL_LOSS_BUDGET_LIMIT` | Aggregate planned risk would exceed 0.02 of starting capital. | No commitment. |
| `GROSS_EXPOSURE_LIMIT` | Gross exposure would exceed the frozen limit. | No commitment. |
| `MINIMUM_CASH_LIMIT` | Reservation/debit would breach minimum buying power. | No commitment. |

Final code may use more specific names, but it must not collapse distinct
entry, exit, priority, classification, runway, or post-fill failures into one
ambiguous reason.

## 15. Immutable storage and lineage

The implemented path family is:

```text
protocols/{protocol_id}/{manifest_sha256}/policy_portfolio/
  policy/{canonical_sha256}/{raw_sha256}.json
  genesis/{canonical_sha256}/{raw_sha256}.json
  regime/{canonical_sha256}/{raw_sha256}.json
  classification/{canonical_sha256}/{raw_sha256}.json
  liquidity/{canonical_sha256}/{raw_sha256}.json
  candidate_input/{canonical_sha256}/{raw_sha256}.json
  event/{canonical_sha256}/{raw_sha256}.json
  state/{canonical_sha256}/{raw_sha256}.json
  session_input/{canonical_sha256}/{raw_sha256}.json
  transition/{canonical_sha256}/{raw_sha256}.json
  paired_session/{canonical_sha256}/{raw_sha256}.json
  lineage/{canonical_sha256}/{raw_sha256}.json
  refs/{kind}/{artifact_id}.json
```

Commitments, positions, settlement legs, and re-entry blocks are immutable
nested state/event payloads rather than separately addressable top-level store
kinds. The following are enforced:

- exclusive create;
- content-addressed canonical and raw SHA-256 identity;
- exact raw byte length;
- duplicate-JSON-key rejection;
- canonical path verification;
- immutable reference files;
- sorted, uniquely named predecessor references;
- load-time model revalidation;
- semantic replay from predecessors;
- orphan rejection;
- chronology checks;
- cross-process canonical-hash determinism.

The store reconstructs rather than trusts the RS-P2-014 candidate/portfolio
lineage, persists/reloads the exact opportunity set, and verifies the
RS-P2-015 PairInput/paired-record predecessors it consumes.

## 16. Implemented test matrix

The implementation maps every row to the concrete tests summarized in
Section 19.2.

| Area | Mandatory coverage |
|---|---|
| Identical genesis | Both sides start with exact Rp100m and no imported holdings/payables/receivables/reservations. |
| Independent evolution | One side allocating changes only its own successor state; shared policy/input remains identical. |
| Recorded sizing | Missing fraction fails with `NOT_ESTIMABLE_POLICY_SIZE`; no Rp13m fallback; correct side basis enforced. |
| Priority | Rank/source-row/ticker order is deterministic; missing/ambiguous rank fails closed. |
| Gate order | Exact ten-step order and reason ordering are deterministic; later candidates see earlier accepted commitments. |
| Hard caps | Sector/cluster overflow is rejected; no soft fallback. |
| Regime | Next-session effectiveness, `UNKNOWN` fail-closed, lower-limit cancellation, and no forced exit. |
| Reservation/fill | Commitment consumes all required resources, fill rechecks capacity, and conversion never double-counts. |
| T+2 | Purchase payable and sale receivable post on exact frozen sessions; early deployability rejected. |
| Session order | Settlement, corporate action, exits, fills, cancellation, and persistence follow PP9. |
| Adverse ambiguity | Same-timestamp proceeds/slot/heat cannot be reused optimistically. |
| Daily stop | Trigger at exactly 3%, latch, cancel pending, preserve open holdings, reset next session. |
| Re-entry | One ticker only, no pyramiding, no same-session re-entry, next-session new-signal eligibility. |
| PP-A1 | Policy CONFIG and manifest use starting-capital heat unit; NAV unit mutation fails. |
| Heat arithmetic | Heat remains open-risk/starting-capital when NAV differs; one-IDR over-limit rejected. |
| Loss budget | Pending plus open risk counts; per-position and aggregate limits enforced. |
| Costs | Aggregate applicable bps then one adverse ceiling at policy quantity. |
| Exact money | Float/bool/nonfinite money rejected; coordinated one-IDR drift rejected. |
| `NOT_ESTIMABLE` | Pre-fill failure creates no position; post-fill failure retains holding and freezes the side. |
| Terminal | Runway check, expected taper, unresolved open state, and no synthetic liquidation. |
| Tamper | Policy, regime, input, state, transition, paired envelope, and refs reject byte/hash/model drift. |
| Replay | Exclusive create is idempotent only for exact bytes; semantic replay reconstructs every successor. |
| Chronology | Future source, reversed state/session, invalid settlement, and stale regime fail closed. |
| Cross-process | Canonical state/transition hashes match in separate Python processes. |
| Authority | Every new artifact is evaluation-only, false live authority, and all `affects_*` flags false. |
| v1 isolation | `shadow-evaluation-v1` cannot load as any P2-016 artifact and is never reinterpreted. |

Implemented PP-A1 test names:

```text
test_pp_a1_policy_config_uses_starting_capital_heat_unit
test_pp_a1_manifest_heat_parameter_uses_starting_capital_unit
test_pp_a1_manifest_rejects_fraction_of_nav_heat_unit
test_policy_heat_uses_starting_capital_when_nav_differs
test_policy_heat_gate_rejects_one_lot_over_starting_capital_limit
```

## 17. Scope boundary and Definition of Done

RS-P2-016 is complete only when:

1. all PP1–PP14 decisions are represented in immutable, validated policy and
   transition artifacts;
2. PP-A1 is explicit in policy CONFIG, manifest binding, arithmetic, tests,
   and this document;
3. PP-N1 deviations are visible and never described as exact live replay;
4. PP-N2 fields fail closed at policy intake and are documented as Phase-8
   production requirements;
5. PP-N3 runway taper is deterministic from the frozen calendar;
6. both sides start from identical genesis and evolve independently;
7. shared-input parity and allowed state divergence are machine-enforced;
8. exact integer-IDR accounting, payables, receivables, reservations, costs,
   holdings, and planned risk reconcile;
9. T+2 and the complete session transition order are tested;
10. post-fill missingness never fabricates an exit or favorable capital;
11. immutable content-addressed storage, references, replay, tamper detection,
    chronology, and cross-process hashing pass;
12. all authority literals remain evaluation only;
13. focused shadow tests, full pytest, Ruff, and lock verification pass;
14. the master checklist records evidence honestly;
15. no A1, collection, unblinding, baseline change, live change, or v1
    reinterpretation occurs.

The following remain out of scope:

- canonical daily marked-to-market NAV/return series (RS-P2-017);
- portfolio Sharpe, DSR, drawdown, calibration, censor rates, and common
  metric aggregation (RS-P2-018);
- real component manifest creation or A1;
- report cadence, unblinding, GO/NO-GO, canary, or promotion;
- any live threshold, ranking, sizing, recommendation, or execution change.

## 18. Owner approval record for this design

On 2026-07-18 the owner approved PP1–PP14 exactly as specified, then added:

- **PP-A1:** portfolio heat is 0.013 of starting capital; every policy/manifest
  unit must say `fraction_of_starting_capital`, and old “1.3% NAV” prose is
  explicitly corrected;
- **PP-N1:** the view is a symmetric formalization of batch control, with
  deviations disclosed;
- **PP-N2:** allocating decisions must provide recorded fraction and rank at
  collection intake;
- **PP-N3:** the roughly 17+ session terminal taper is expected protocol
  behavior.

The owner also confirmed:

- `PortfolioState` RS-P2-014 remains a read-only control reference;
- RS-P2-016 uses an additive artifact family;
- raw `PairInput` is replayed at policy quantity;
- RS-P2-015 cash flows are not multiplied into policy outcomes.

This approval authorizes implementation and tests for RS-P2-016 only. It does
not grant A1 or any live authority.

## 19. Implementation and verification evidence

Implementation completed on 2026-07-19. Every required verification gate is
green. The implementation is additive and remains incapable of granting A1 or
affecting the live path.

### 19.1 Final implementation map

| Concept | Final file and lines | Contract/version | Evidence |
|---|---|---|---|
| Frozen policy-portfolio policy | `core/shadow_protocol/policy_portfolio.py:177-316,1908-2027` | `shadow-policy-portfolio-policy-v1` | PP1–PP14 literals, PP-A1 heat unit, not-A1-eligible capability, exact CONFIG path/hash on both manifest sides, parent-policy and methodology binding. |
| Regime, liquidity, classification, candidate input | `core/shadow_protocol/policy_portfolio.py:381-710,2036-2284` | `shadow-policy-portfolio-regime-v1`; `shadow-policy-liquidity-session-v1`; `shadow-policy-candidate-classification-v1`; `shadow-policy-portfolio-candidate-input-v1` | Next-session regime, UNKNOWN path, ENTRY-only per-session capacity, causal/fresh classification, exact P2-015 predecessor binding. |
| Genesis | `core/shadow_protocol/policy_portfolio.py:319-378,2287-2411` | `shadow-policy-portfolio-genesis-v1` | One common empty Rp100m economic payload, side-specific immutable wrappers, no observed-holding import. |
| Settlement, commitment, position, event | `core/shadow_protocol/policy_portfolio.py:741-1091` | settlement/commitment/position/event v1 contracts | Exact integer-IDR reservation, T+2 legs, policy quantity, planned risk, marks, corporate actions, ordered event deltas. |
| Per-side payload/state and session input | `core/shadow_protocol/policy_portfolio.py:1093-1712,2414-2612` | payload/state/session-input v1 contracts | Payable/receivable accounting, gate-only equity, null NAV metric, predecessor chronology, opportunity/candidate split, paired input parity. |
| Transition and paired envelope | `core/shadow_protocol/policy_portfolio.py:1714-1905` | transition/paired-session v1 contracts | Pre-state + post-payload hash bridge, full successor transition hash, side-specific states, shared-input parity. |
| Transition engine | `core/shadow_protocol/policy_portfolio.py:2615-4850` | deterministic policy-portfolio engine v1 | Pure replay, PP9 event order, independent side evolution, exact-session fill at `:3336-3837`, admission/gates at `:3838-4326`, terminal/risk/accounting helpers at `:4327-4850`. |
| Integer resource journal | `core/shadow_protocol/policy_portfolio.py:4851-4911` | transition-journal verifier | Reconciles settled cash, receivables, payables, reservations, planned risk, gross exposure, quantity, and realized P&L. |
| Immutable references and lineage | `core/shadow_protocol/policy_portfolio_store.py:101-466,1082-1160` | reference/lineage v1 | Dual hashes, byte length, authority literals, named predecessors, exact opportunity/P2-014/P2-015 reconstruction. |
| Immutable store/replay | `core/shadow_protocol/policy_portfolio_store.py:467-1081` | content-addressed store v1 | Exclusive create, canonical/raw paths, strict loaders, semantic replay, reference reload, orphan/tamper rejection. |
| Governance boundary | `core/shadow_protocol/governance.py` unchanged | existing v2 manifest/approval governance | No new approval path was needed or added; the policy capability remains `RS_P2_016_IMPLEMENTED_NOT_A1_ELIGIBLE`. |
| Public exports | `core/shadow_protocol/__init__.py:243-318,658-715` | additive exports | New policy-portfolio contracts, builders, replay, loaders, store, references, and lineage only. |
| Tests | `tests/test_shadow_protocol_p2_016.py:703-1840` | 40 functions / 45 collected cases | PP1–PP14, PP-A1, PP-N notes, exact arithmetic, negative paths, storage, authority, v1 isolation, and cross-process hashes. |

### 19.2 Final test mapping

| Requirement | Test functions |
|---|---|
| PP-A1 and manifest binding | `test_pp_a1_policy_config_uses_starting_capital_heat_unit`; `test_pp_a1_manifest_heat_parameter_uses_starting_capital_unit`; `test_pp_a1_manifest_rejects_fraction_of_nav_heat_unit` |
| Genesis and independent paired evolution | `test_genesis_is_one_identical_empty_100m_hash`; `test_common_input_parity_allows_independent_side_state_divergence`; `test_active_old_cohort_coexists_with_explicit_empty_current_set` |
| Recorded sizing, rank, re-entry, hard caps | `test_policy_admission_records_exact_ten_gate_order`; `test_duplicate_priority_and_hard_sector_cluster_caps_fail_closed`; parametrized `test_allocate_requires_recorded_fraction_rank_and_nonzero_lot` |
| Fill-time safety and capacity | `test_fill_recheck_reapplies_loss_heat_gross_and_cash_limits`; `test_fill_rechecks_policy_session_capacity_and_cancels_fail_closed`; `test_exit_liquidity_cannot_be_relabelled_as_policy_entry_evidence` |
| Classification causality/freshness | parametrized `test_noncausal_or_stale_classification_is_fail_closed`; `test_fill_recheck_rejects_classification_expired_after_signal` |
| Policy quantity, payable, split | `test_commitment_fills_at_policy_quantity_and_creates_t_plus_two_payable`; `test_supported_split_adjusts_quantity_mark_and_journal_without_crash` |
| T+2 cash timeline | `test_t_plus_two_purchase_payable_posts_only_on_frozen_session`; `test_sale_receivable_is_not_deployable_until_exact_t_plus_two` |
| Regime transition | `test_unknown_regime_blocks_new_allocation`; `test_regime_downshift_does_not_force_existing_position_exit`; `test_regime_reduction_cancels_pending_without_forced_exit`; `test_regime_reduction_keeps_highest_priority_pending` |
| Daily stop | `test_daily_realized_loss_stop_latches_after_adverse_gap`; `test_daily_stop_at_exact_threshold_cancels_all_pending` |
| Post-fill fail-closed path | `test_post_fill_exit_capacity_failure_retains_holding_and_freezes_side`; `test_not_estimable_side_preserves_last_verified_economic_state` |
| Heat, accounting equity, risk threshold | `test_policy_heat_uses_starting_capital_when_accounting_equity_differs`; `test_policy_heat_gate_rejects_one_lot_over_starting_capital_limit`; `test_fill_recheck_reapplies_loss_heat_gross_and_cash_limits` |
| Terminal and chronology | `test_terminal_runway_rejects_without_synthetic_liquidation`; `test_session_input_rejects_nonadjacent_predecessor_and_preclose_freeze`; `test_session_input_rejects_transition_after_fixed_terminal` |
| Journal, replay, one-IDR drift | `test_transition_journal_reconciles_every_integer_resource`; `test_replay_is_idempotent_and_rejects_one_idr_state_drift`; `test_transition_event_hash_drift_fails_exact_replay` |
| Store replay/tamper/identity | `test_policy_portfolio_store_replay_is_idempotent_and_reconstructable`; `test_policy_portfolio_store_rejects_raw_file_tampering`; `test_policy_portfolio_store_rejects_reference_byte_length_drift` |
| Authority literals | `test_all_policy_portfolio_artifacts_are_evaluation_only`; `test_store_references_and_lineage_preserve_evaluation_only_authority` |
| Strict loaders and v1 isolation | `test_policy_portfolio_loader_rejects_duplicate_keys_and_v1_contracts` |
| Cross-process hashes | `test_policy_and_genesis_hashes_are_identical_cross_process`; `test_policy_portfolio_state_transition_hashes_identical_cross_process` |

### 19.3 Verification gate

```text
Focused shadow suite : 261 passed
Full pytest          : first sandbox run reached 1904 passed, 3 skipped, then
                       7 certifi PermissionError environment failures;
                       approved outside-sandbox rerun GREEN:
                       1911 passed, 3 skipped, 86 warnings
ruff check --fix .   : GREEN; changed no files
subsequent ruff check: GREEN
uv lock --check      : GREEN
```

The seven initial full-suite errors were filesystem permission failures while
pytest accessed the installed `certifi` package, not assertion or application
failures. The complete approved rerun outside the restricted sandbox passed.

### 19.4 Final SHA-256

```text
Protected byte-identical files:
  core/shadow_protocol/contracts.py
    87b605fb9cc3cb3bee73d903110801699e06e63f4d41e9e8b94cdd48d0ee54b7
  core/shadow_protocol/calendar.py
    fe27a4e5c964c26f3093921193f29ec45f4f4c09f620b52ca94806ab302c7151
  core/shadow_protocol/outcome_engine.py
    b90d149df67d91f59408618e580c75f55d2de2257cf6e5f46f4265dffaaa27a8
  core/shadow_protocol/paired_view.py
    873e94fa8cb6ebaf50c127a32da6c86326c1a68989a1a4ec6f2d53d7d9fef684

Additive-only boundary files:
  core/shadow_protocol/portfolio.py
    63f7150f04c362791841f618969beba81de3849feb778681264d028fc815ee10
  core/shadow_protocol/fixed_notional.py
    485039d73f187e3b3092d5fbb733f2e9b5ee61617a2fa9e4dd0bef9e65bb0146
  core/shadow_protocol/fixed_notional_store.py
    ff148c6a350025636342da113f8ff40cbc390049b9eb64c00610b564d50bfbcc
  core/shadow_protocol/governance.py
    b2cdb46e36f453ba10e28a76403ce1768947c7dcd9d5093c37f13ab4ad4be7c4
  core/shadow_protocol/__init__.py
    fec2c0b9dd1c5bb7b2913c808dc880815420a6d125eb26716dd19a65092cb5dd

New implementation/test files:
  core/shadow_protocol/policy_portfolio.py
    25605c1882904b9035271e8ff0fa9f8f20ed8ed3ccf4ff6b6f4499ca083530d8
  core/shadow_protocol/policy_portfolio_store.py
    9c19bbcdb03c34effaafde0be82b7057b164d6509f50514807f38542877df4a5
  tests/test_shadow_protocol_p2_016.py
    3eaa831053186cd582c2464c16069cf26fb82b7b14e77b32ef23a9b073b4c2fe

Explicit unchanged isolation artifacts:
  core/forecasting/shadow_evaluation.py
    193f8ab098ce31f4f8f6c74de473a2cec909ff16fbf073c58a132894b0a945cd
  docs/research/BASELINE_CONTROL_MANIFEST_2026-07-17.json
    fe40a2cb7c0c4454bd2633aa32f14597a48e19945703347957e90b896a7ba4be
```

### 19.5 Commits and final authority confirmation

```text
Feature commit : 9638762998626a372cf6e33851f4423937dd9584
Docs commit    : reported externally after commit to avoid self-reference
Push           : none
Working tree   : intentionally dirty until the authorized feature/docs commits;
                 final clean-tree result is reported externally
```

Confirmed for this implementation:

- no A1 was granted;
- no collection or unblinding occurred;
- no live authority or threshold changed;
- baseline and `shadow-evaluation-v1` were untouched;
- at the P2-016 hard stop, RS-P2-017/018 remained open; RS-P2-017 was later
  implemented in `d5ae02fddbb4ba070857e4d6281b2d33afe14b6d`, while RS-P2-018
  remains open;
- PP1–PP14, PP-A1, and PP-N1–PP-N3 were implemented without an undisclosed
  deviation.
