# RS-P2-017 Daily Marked-to-Market NAV Design

**Status:** IMPLEMENTED AND VERIFIED FOR LOCAL BUILD/REPLAY;
A1 NOT GRANTED
**Decision date:** 2026-07-19
**Implementation reconciliation:** 2026-07-19
**Target:** RS-P2-017 only
**Predecessors:** RS-P2-014 frozen portfolio state and decision view,
RS-P2-015 fixed-notional signal-isolation view, and RS-P2-016 independently
evolving policy portfolios
**Successor:** RS-P2-018 common metrics remains out of scope
**Authority:** evaluation only; no A1 grant, collection, unblinding,
promotion, live execution, ranking, sizing, or decision authority

## 1. Executive decision

RS-P2-017 creates the first canonical daily marked-to-market portfolio NAV
series without reinterpreting any predecessor artifact. It preserves two
economically different families:

1. **`FIXED_NOTIONAL_SLEEVE_EQUITY`** is an opportunity-by-side series that
   reconciles one Rp13,000,000 fixed-notional sleeve. It is signal-isolation
   evidence, not a capital-competing portfolio.
2. **`POLICY_PORTFOLIO_NAV`** is a side-by-session series derived from the
   independently evolving RS-P2-016 control and challenger portfolios. It is
   the sole canonical input for later portfolio drawdown, volatility, Sharpe,
   DSR, exposure, and turnover metrics.

The candidate-level RS-P2-014 decision view has no NAV. Fixed-notional sleeves
must never be summed, netted, or otherwise aggregated into a synthetic policy
portfolio.

Both economic families use the same persisted `DailyNavPoint`,
`NavSeriesEvent`, and `NavSeriesSnapshot` contracts, discriminated by the
schema-bound `series_kind`. `PairedDailyNavSeries` is only an in-memory
control/challenger envelope over two snapshots. It is not a third persisted
artifact family, a performance-difference record, or an aggregation API.

The implementation is additive in `core/shadow_protocol/daily_nav.py`,
`daily_nav_store.py`, and package exports. It does not widen
`PortfolioState.state_role="CONTROL_FROZEN_REFERENCE"`, reinterpret
`PolicyPortfolioStatePayload.accounting_equity_idr` as an already trusted NAV,
or modify `shadow-evaluation-v1`.

The content-addressed store proves local daily-NAV byte/model identity and the
embedded mark/point/event/snapshot chain. It deliberately does **not** claim an
independently authenticated global run commitment, complete external
P2-015/P2-016 predecessor reconstruction, or deletion-proof tail completeness.
Every snapshot therefore remains
`UNANCHORED_NOT_CERTIFIED_COMPLETE`; the global residual belongs to
RS-P2-019/021/022 rather than being simulated in RS-P2-017.

## 2. Normative hierarchy

The implementation must satisfy, in order:

1. the owner-approved NV1–NV8 decisions in Section 3;
2. mandatory notes NV-N1–NV-N4 in Section 4;
3. the three-view and daily-NAV requirements in
   `SHADOW_MODE_PROTOCOL.md:86-105`;
4. the `NOT_ESTIMABLE` and common-metric boundary in
   `SHADOW_MODE_PROTOCOL.md:107-122`;
5. the daily-return governance requirement in
   `SHADOW_MODE_PROTOCOL.md:358-388`;
6. the RS-P2-017 task at
   `RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:436-447`;
7. RS-P2-014 hybrid integer-IDR arithmetic, mark-source, stale-mark, and N3
   boundaries;
8. RS-P2-015 FN1–FN8 and FN-N1–FN-N3; and
9. RS-P2-016 PP1–PP14 and PP-A1/PP-N1–PP-N3.

No closed-trade row, one-lot result, fixed-notional terminal return, or
gate-only accounting-equity field may substitute for the daily NAV series.

## 3. Approved owner decisions NV1–NV8

### NV1 — two non-interchangeable artifact families

RS-P2-017 must implement:

- `FIXED_NOTIONAL_SLEEVE_EQUITY`, keyed by one opportunity and one side; and
- `POLICY_PORTFOLIO_NAV`, keyed by one policy path, side, and IDX session.

Only the policy-portfolio family may supply portfolio Sharpe, drawdown, DSR,
exposure, turnover, or other capital-competition metrics. A fixed-notional
sleeve may be compared only with the other side of the same opportunity. It
must never be summed across opportunities or relabeled as policy NAV.

RS-P2-015 cash-flow records remain fixed-notional evidence. They never become
RS-P2-016 policy cash authority, and RS-P2-016 continues to replay raw
`FixedNotionalPairInput` at the independently recorded policy quantity.

### NV2 — previous-session-close genesis and EOD timing

The policy series starts at the executable RS-P2-016 genesis:

```text
GENESIS_ANCHOR =
    close of the frozen IDX session immediately preceding
    the first policy-transition session
```

Both sides have exact equity Rp100,000,000 at this anchor. The genesis daily
return is:

```text
status = NOT_ESTIMABLE
reason = NOT_ESTIMABLE_NO_PREDECESSOR
```

Every successor point represents the economic end of its frozen IDX session
after the complete PP9 event order and the immutable end-of-session
RS-P2-016 state/transition have been produced. `as_of` is the frozen session
close. `captured_at`/`frozen_at` may be later only to accommodate source
availability, and must never precede any input's `available_at`.

### NV3 — official current-session mark with zero-session carry

Every open holding requires an exact, manifest-bound `NavMarkInput`:

- current frozen IDX session;
- official source-supplied raw/unadjusted close in strict integer IDR;
- ticker and quantity identity;
- source ID, contract version, definition hash, record hash, and raw identity;
- `available_at`, `captured_at`, and session-close chronology;
- corporate-action policy and exact predecessor lineage; and
- evaluation-only authority literals.

The carry-forward allowance is **zero sessions**. A previous close, entry
price, planned price, adjusted-price series, starting capital, or later
favorable revision is never a fallback.

A zero-volume or suspended name is estimable only when the frozen source
explicitly publishes a current-session official close/reference under the
registered source contract. A copied prior-session value without an exact
current-session source record is `NOT_ESTIMABLE`.

Missing, stale, future-dated, adjusted, duplicate, or hash-mismatched mark
evidence creates an explicit null expected-session point. It is not silently
omitted or replaced.

### NV4 — explicit fixed-sleeve cost liability

The fixed-notional sleeve begins with exact Rp13,000,000 and preserves the
approved gross-before-cost convention. Entry cost that is outside the gross
notional is represented explicitly as:

```text
unfunded_cost_liability_idr
```

The liability:

- is strict non-negative integer IDR;
- is bound to the exact entry-cost cash-flow predecessor;
- is subtracted explicitly when sleeve equity is reconciled;
- is never hidden by allowing unexplained negative idle cash;
- accrues no interest;
- grants no additional buying power, position size, or leverage; and
- is paid from exit proceeds before any residual becomes idle sleeve cash.

A positive `unfunded_cost_liability_idr` without the exact entry-cost amount,
cash-flow hash, and causal fill lineage is an invalid artifact, not a
`NOT_ESTIMABLE` market observation.

Trade-date payables, sale receivables, T+2 posting, eligible dividends, and
position marks remain separate exact integer-IDR components. Economic entry
cost is recognized once; it must not be subtracted a second time during
settlement.

### NV5 — paired union window and settlement-only tail

For each fixed-notional opportunity, control and challenger use the union of
their real expected sessions:

- signal/activation/fill/holding sessions when economically applicable;
- terminal sessions;
- and any additional sessions required to post the last T+2 payable or
  receivable.

A side with `NO_ACTION` is flat at the Rp13,000,000 baseline only on sessions
that actually exist in this paired union. The producer must not fabricate a
15-session sequence of zero returns merely because the primary label horizon
is 15 sessions.

After a holding closes but before its last settlement posts, the series uses
the explicit state:

```text
SETTLEMENT_ONLY
```

`SETTLEMENT_ONLY` has no holding, retains exact payable/receivable/liability
state, and ends when every registered settlement obligation is resolved.

### NV6 — permanent expected-session nulls and no favorable reconstruction

Every expected session has an immutable point. When a required mark, state,
settlement, or predecessor is unavailable, that point is persisted with a
null value and specific `NOT_ESTIMABLE` reason codes.

The producer must not:

- omit the expected session;
- carry a prior value;
- interpolate or forward-fill;
- bridge a daily return across the null;
- fabricate a fill or exit;
- continue an unresolved holding as if the planned exit did not occur;
- synthesize terminal liquidation; or
- overwrite the primary frozen point with a later favorable source revision.

For a policy path already marked `NOT_ESTIMABLE_FROM_SESSION`, numeric
`accounting_equity_idr` remains diagnostic predecessor evidence only. It
cannot become canonical NAV.

An unresolved terminal position may expose a separately labeled diagnostic
marked-equity value when exact current-session mark evidence exists, but its
canonical NAV/return remains `NOT_ESTIMABLE` and cannot feed promotion-grade
metrics.

### NV7 — simple daily return, insolvency, and no external flows

For two consecutive estimable policy NAV points:

```text
daily_return_t =
    (nav_t_idr - nav_previous_idr) / nav_previous_idr
```

The numerator and denominator use exact integer IDR. The ratio is quantized
once to 12 decimal places with `ROUND_HALF_EVEN`, normalized from negative
zero to positive zero, and serialized as a finite float.

Rules:

- the genesis return is `NOT_ESTIMABLE_NO_PREDECESSOR`;
- no return is computed when the immediately preceding expected-session NAV
  is null;
- no return is bridged across a missing session;
- a transition from positive equity to zero or negative equity is estimable
  and may be `-1.0` or below `-1.0`;
- zero or negative current equity sets terminal status `INSOLVENT`;
- no return is produced after insolvency;
- insolvency is never rebased to Rp100,000,000 or Rp13,000,000; and
- deposits, withdrawals, recapitalization, owner cash, or other external
  flows are unsupported. Supporting them requires a new protocol.

RS-P2-017 aligns the control and challenger snapshots to the same
`shared_session_union`, but it does not compute or persist a
challenger-minus-control return, R, or other performance difference. Common
paired performance metrics and their estimability/denominator rules remain
RS-P2-018 work.

### NV8 — immutable storage, report, and metric boundary

All persisted RS-P2-017 artifacts and references use:

- exclusive creation;
- canonical-model and raw-file SHA-256;
- exact raw byte length;
- duplicate-key rejection;
- canonical path verification;
- immutable references;
- deterministic named predecessors;
- exact stored-byte replay;
- chronology checks;
- one-IDR arithmetic-drift rejection; and
- cross-process hash determinism.

The implemented replay guarantee is deliberately local. It verifies the exact
stored policy, mark inputs, points, events, snapshot, named predecessor hashes,
and any explicitly linked prior local snapshot. It does not authenticate that
the locally visible head/tail is globally complete or independently reload
every external predecessor object. The mandatory snapshot literal
`UNANCHORED_NOT_CERTIFIED_COMPLETE` makes that residual machine-visible.

RS-P2-017 produces machine-readable evidence only. Human-readable daily,
weekly, monthly, and fixed-terminal reports remain RS-P2-023. Common metric
aggregation, censor counts/rates, drawdown, Sharpe, DSR, exposure, and turnover
remain RS-P2-018 or later.

## 4. Mandatory owner notes NV-N1–NV-N4

### NV-N1 — RS-P2-016 PP1 documentation erratum only

The prior RS-P2-016 phrase “opening of the first frozen IDX session” was a
documentation inconsistency. The implemented and tested contract already uses
the immediately preceding frozen session close. The erratum in
`RS_P2_016_POLICY_PORTFOLIO_DESIGN.md:138-163` now states the same executable
rule used by RS-P2-016 and RS-P2-017:

- `policy_portfolio.py` remains byte-identical;
- existing state identity and replay semantics remain unchanged;
- no economic, decision, threshold, or authority rule changes; and
- RS-P2-017 names that existing close `GENESIS_ANCHOR` and emits
  `NOT_ESTIMABLE_NO_PREDECESSOR` for its first return.

Implementation consistency is explicit: `build_policy_portfolio_nav_series()`
starts from `genesis.genesis_session`; the first `DailyNavPoint.as_of` is
`session_close_at(genesis.genesis_session)`; both sides start at exact
Rp100,000,000. There is no second genesis interpretation in the NAV layer.

### NV-N2 — suspension censoring must remain measurable

Every suspension/missing-official-mark censor record must retain:

- ticker;
- first censored session;
- current/last censored session;
- deterministic censored-session duration/count;
- mark-source and reason-code lineage; and
- affected family and side.

The actual fields are `DailyNavPoint.session`, `censored_tickers`,
`censor_duration_sessions`, and `poisoned_from_session`, plus point/snapshot
`reason_codes`/`poison_reason_codes` and the embedded `NavMarkInput`. A later
point increments duration while remaining permanently null; later favorable
mark evidence is not allowed to resurrect the primary series.

RS-P2-018 must report the count and duration of this censoring by side and
ticker, with source/liquidity slices where adequately supported. It must not
silently remove suspended paths from denominators.

### NV-N3 — no unexplained positive liability

`unfunded_cost_liability_idr > 0` is valid only when exact entry-cost and fill
lineage explains the amount. A positive liability without that predecessor is
a contract error. A zero liability is valid without an origin hash; the
producer removes the origin after the liability is fully paid.

The implemented `DailyNavPoint` validator requires exactly one named
`entry_cost` predecessor whose SHA-256 equals
`unfunded_cost_origin_sha256`. Policy NAV forbids this liability entirely.
Fixed-sleeve settlement repays it from exit proceeds before residual cash.

### NV-N4 — no cross-opportunity aggregation surface

The public module and package exports must not expose an API/helper that sums,
nets, weights, or aggregates independent fixed-notional sleeves into a
portfolio. The implemented public producers are
`build_fixed_notional_sleeve_nav_series()` and
`build_policy_portfolio_nav_series()`; they accept different predecessor
types and produce a shared contract tagged with different `series_kind`
literals. `PairedDailyNavSeries` only aligns two sides of one family and one
window. It neither aggregates opportunities nor emits a paired performance
difference. Model validation rejects relabeling a fixed-sleeve snapshot as
policy NAV, and no public cross-opportunity aggregation helper is exported.

## 5. Manifest-v2 sufficiency

Manifest v2 remains sufficient. No new top-level manifest field is required.

The additive frozen NAV policy is a strict CONFIG artifact bound at the
reserved path:

```text
config/daily-nav-policy-v1.json
```

Its canonical SHA-256 must occur exactly once in both control and challenger
content-hash sets. A component-specific
`verify_daily_nav_policy_binding()` must additionally verify:

- parent `FrozenPortfolioPolicy`, `FrozenFixedNotionalPolicy`, and
  `FrozenPolicyPortfolioPolicy` IDs/hashes;
- exact mark `SourceDefinition`, source ID, and definition hash;
- methodology-document SHA-256;
- frozen trading-calendar SHA-256;
- cost and `LabelDefinition` SHA-256;
- corporate-action policy SHA-256;
- schema-bound NV1–NV8, authority, and no-aggregation literals; and
- capability status
  `RS_P2_017_IMPLEMENTED_NOT_A1_ELIGIBLE`.

`FrozenDailyNavPolicy` intentionally does not contain the final manifest hash:
that would recreate the hash circularity already removed from the governance
design. Instead, the manifest binds the policy hash identically on both sides,
and every observation/point/event/snapshot carries the final
protocol/component/manifest identity. The store independently revalidates the
policy's manifest dependencies and exact CONFIG binding.

`NavMarkInput.available_at` is observation-level evidence and belongs in the
mark artifact, not the manifest. `unfunded_cost_liability_idr` is state, not a
threshold or manifest scalar.

A material edit to the NAV policy after a manifest is frozen requires a new
manifest revision and hash pair. After collection starts, it requires a new
protocol ID/trial under the existing protocol rule.

## 6. Actual additive API and artifact families

### 6.1 `FrozenDailyNavPolicy`

The implemented `shadow-daily-nav-policy-v1` contract contains:

- `policy_id` and
  `RS_P2_017_IMPLEMENTED_NOT_A1_ELIGIBLE`;
- the three parent policy IDs/hashes;
- mark-source contract/version/definition identity;
- methodology, calendar, cost, label, and corporate-action hashes;
- every frozen price basis, zero-carry, family, liability, settlement,
  missingness, return, insolvency, external-flow, and no-aggregation literal;
  and
- evaluation-only authority flags.

Its manifest identity is external through the identical CONFIG hash on both
sides, avoiding a policy/manifest hash cycle.

### 6.2 `NavMarkInput`

The implemented `shadow-nav-mark-input-v1` contract carries:

- protocol/component/final-manifest and daily-NAV policy hashes;
- `series_kind`, series/path/side, ticker, and frozen session;
- official current-session integer-IDR close and volume, or one explicit
  source-absence status;
- source definition, canonical-record, raw-file, byte-length, vintage,
  availability, capture, and revision identity;
- calendar and corporate-action hashes; and
- zero carry, no fallback, and evaluation-only authority literals.

### 6.3 Shared persisted point/event/snapshot contracts

There are no separate Python classes named “fixed-sleeve point” and
“policy-NAV point.” Both families use:

- `DailyNavPoint` / `shadow-daily-nav-point-v1`;
- `NavSeriesEvent` / `shadow-nav-series-event-v1`; and
- `NavSeriesSnapshot` / `shadow-nav-series-snapshot-v1`.

The mandatory `series_kind` is either
`FIXED_NOTIONAL_SLEEVE_EQUITY` or `POLICY_PORTFOLIO_NAV`. Each point contains
the exact money-state components, NAV/diagnostic/return envelopes,
previous-point and mark hashes, deterministic named predecessors, censor
state, and authority literals. Events provide the local append-only event
chain. Snapshots embed the exact marks, points, events, their hash sequences,
head/tail, poison summary, and the mandatory unanchored-completeness label.

Family-specific lineage remains distinct:

- fixed-sleeve points bind the exact P2-015 paired record and primary
  lifecycle, including entry-cost liability lineage; and
- policy points bind the exact P2-016 paired session, state, transition, and
  previous state.

### 6.4 In-memory `PairedDailyNavSeries`

`PairedDailyNavSeries` is not independently persisted. It contains exactly:

- one `series_kind`;
- one control `NavSeriesSnapshot`;
- one challenger `NavSeriesSnapshot`;
- one `shared_session_union`; and
- `paired_input_parity=true`.

Validation enforces family/role identity and that both primary point sequences
match the frozen union. It does not calculate paired performance, persist a
third paired artifact, or certify global lineage completeness.

### 6.5 `DailyNavArtifactStore` and references

The store supports exactly five `ArtifactKind` values:

- `POLICY`;
- `NAV_MARK_INPUT`;
- `NAV_POINT`;
- `NAV_SERIES_EVENT`; and
- `NAV_SERIES_SNAPSHOT`.

`DailyNavArtifactReference` binds logical ID/version, canonical SHA-256,
raw-file SHA-256, exact byte length, canonical relative path, manifest
identity, deterministic named predecessors, and evaluation-only authority.
There is no separate paired-series, lineage-bundle, or metric-aggregation
artifact in RS-P2-017.

## 7. Exact accounting rules

### 7.1 Policy NAV

```text
policy_nav_idr =
    settled_cash_idr
  + sale_receivable_idr
  - purchase_payable_idr
  + marked_holdings_value_idr
```

The producer recomputes every component from the exact P2-016 state and
current-session `NavMarkInput`s. Equality with P2-016
`accounting_equity_idr` is a reconciliation check only when the path is active
and all marks are current. Caller-supplied equality cannot override a failed
path or missing mark.

### 7.2 Fixed-sleeve equity

Fixed-sleeve equity separately identifies:

```text
sleeve_equity_idr =
    settled_or_idle_cash_idr
  + sale_receivable_idr
  - purchase_payable_idr
  + marked_holding_value_idr
  - unfunded_cost_liability_idr
```

The implementation must prove that transaction cost is recognized exactly
once across payable, settlement, liability, and exit-proceeds application.
The terminal estimable equity must reconcile to the approved P2-015 economic
result:

```text
13_000_000 + net_pnl_idr
```

when every settlement/capacity/mark prerequisite is estimable.

## 8. Expected-session and censoring rules

Expected sessions come only from the frozen trading calendar. Each series
persists a deterministic ordered tuple of expected sessions and one immutable
point per tuple entry.

The exact daily-NAV reason-code registry introduced by
`daily_nav.py` is:

- `NOT_ESTIMABLE_NO_PREDECESSOR`;
- `NOT_ESTIMABLE_MISSING_OFFICIAL_MARK`;
- `NOT_ESTIMABLE_SUSPENDED_NO_OFFICIAL_MARK`;
- `NOT_ESTIMABLE_MISSING_POLICY_SESSION_RECORD`;
- `NOT_ESTIMABLE_PREDECESSOR_GAP`;
- `NOT_ESTIMABLE_TERMINAL_UNRESOLVED`; and
- `INSOLVENT_TERMINAL`.

Predecessor reason codes remain source-preserving. For example,
`NOT_ESTIMABLE_EXIT_CAPACITY` or a P2-016 path reason can propagate into the
daily-NAV point; RS-P2-017 does not rename it.

Several earlier design labels are therefore not persisted reason codes.
`INSOLVENT`, `SETTLEMENT_ONLY`, `NO_ACTION_FLAT`, and `GENESIS_ANCHOR` are
`point_status` values. Stale/future/adjusted/source-mismatched evidence fails
contract or binding validation; it is not converted into invented
`NOT_ESTIMABLE_MARK_STALE`, `...MARK_FUTURE`, or
`...MARK_SOURCE_MISMATCH` records.

Missingness is permanent for the primary frozen series. Later corrected source
vintages may be retained as separate diagnostic/sensitivity artifacts, but
must not overwrite or silently promote the primary point.

## 9. Immutable storage and lineage

Implemented namespace:

```text
protocols/{protocol_id}/{manifest_sha256}/daily_nav/
  policies/{canonical_sha256}/{raw_sha256}.json
  nav_mark_inputs/{canonical_sha256}/{raw_sha256}.json
  nav_points/{canonical_sha256}/{raw_sha256}.json
  nav_series_events/{canonical_sha256}/{raw_sha256}.json
  nav_series_snapshots/{canonical_sha256}/{raw_sha256}.json
  refs/policies/{policy_id}.json
  refs/nav_mark_inputs/{mark_input_id}.json
  refs/nav_points/{point_id}.json
  refs/nav_series_events/{event_id}.json
  refs/nav_series_snapshots/{snapshot_id}.json
```

Every reference binds:

- artifact ID and contract version;
- canonical and raw SHA-256;
- exact raw byte length and relative path;
- manifest identity;
- deterministically sorted named predecessors; and
- authority literals.

The store reloads and verifies the exact daily-NAV policy, mark inputs, points,
events, and one side's snapshot. It verifies internal previous-point/event
links, correction links, mark membership, hash sequences, policy/source/
calendar identity, and an explicitly supplied prior local snapshot.

External P2-015/P2-016 objects are represented by deterministic named hashes
inside points/references, but `DailyNavArtifactStore` does not traverse the
candidate-set, fixed-notional, or policy-portfolio stores to reload those
objects. Nor does it persist the in-memory `PairedDailyNavSeries`. Therefore:

- local bytes/model/replay consistency is implemented;
- locally linked snapshot-prefix append-only consistency is implemented;
- independently authenticated run/tail completeness is not implemented; and
- end-to-end global cross-store lineage closure remains residual
  RS-P2-019/021/022 work.

This is why `NavSeriesSnapshot.chain_completeness_status` is always
`UNANCHORED_NOT_CERTIFIED_COMPLETE`. Deleting a local tail together with every
local pointer to it is not claimed detectable by this pass.

No `latest_*` alias is authoritative.

## 10. Producer and replay order

1. Revalidate manifest v2 and the exact frozen NAV policy.
2. Verify parent policy, source, calendar, label, cost, methodology, and
   corporate-action bindings.
3. Consume exact validated P2-015 or P2-016 predecessor objects and verify
   their manifest/policy/calendar identities and named hashes. Cross-store
   stored-byte traversal remains the explicit RS-P2-019/021/022 residual.
4. Construct and validate each `NavMarkInput`.
5. Derive the expected-session sequence and paired union without arbitrary
   padding.
6. Derive sleeve or policy points using exact integer arithmetic.
7. Apply liability, T+2, no-action, `SETTLEMENT_ONLY`, censoring, and
   insolvency rules.
8. Derive simple returns only from immediately adjacent estimable points.
9. Verify family separation and identical paired session-union parity; do not
   emit a paired performance difference in this pass.
10. Persist all independently addressable nodes with exclusive create.
11. Reload raw bytes, reconstruct the local NAV graph, and replay the complete
    local series without claiming external-predecessor or tail completeness.
12. Require canonical equality and reject any one-IDR, chronology, mark,
    liability, or family drift.

## 11. Required test plan

### Policy and manifest

- same NAV CONFIG hash on both sides;
- missing/asymmetric/wrong-path CONFIG rejection;
- wrong mark-source definition rejection;
- parent-policy/methodology/calendar/cost/label hash rejection;
- manifest-v1 and `shadow-evaluation-v1` non-reinterpretation; and
- not-A1-eligible capability and evaluation-only literals.

### Genesis, marks, and chronology

- previous-session-close genesis;
- `NOT_ESTIMABLE_NO_PREDECESSOR`;
- current-session official raw mark;
- future `available_at`, stale session, adjusted source, previous-close,
  entry-price, and starting-capital fallback rejection;
- suspension ticker/duration evidence; and
- missing expected-session null persistence.

### Fixed sleeve

- primary-15-day eligibility only;
- explicit liability and exact entry-cost lineage;
- rejection of unexplained positive or missing required liability;
- no interest/leverage/buying power;
- exit proceeds pay liability before idle cash;
- T+2 payable/receivable;
- terminal reconciliation to Rp13m plus net P&L;
- `SETTLEMENT_ONLY`;
- no-action flat only over the real union; and
- no fabricated 15-session zero path.

### Policy NAV and returns

- exact four-component NAV formula;
- active-state reconciliation and one-IDR drift rejection;
- numeric accounting equity cannot override a failed path;
- simple return and 12-place `ROUND_HALF_EVEN`;
- no gap bridge;
- identical paired session union and no P2-017 performance-difference output;
- return below `-1.0`;
- zero/negative terminal insolvency;
- no rebase/external flow; and
- terminal diagnostic-only mark.

### Family separation, storage, and replay

- fixed sleeve cannot enter policy builder/loader;
- no public cross-opportunity aggregation API/helper;
- content-addressed path and exact retry;
- raw/canonical/byte-length/path/duplicate-key tamper rejection;
- predecessor/state/mark/previous-point drift rejection;
- orphan rejection;
- exact stored-byte replay;
- cross-process hash determinism; and
- authority literals on every artifact/reference/lineage node.

## 12. Scope exclusions and Definition of Done

RS-P2-017 is complete only when:

1. NV1–NV8 and NV-N1–NV-N4 are schema-bound, executable, and tested.
2. Both artifact families remain distinct and machine-enforced.
3. Every mark has point-in-time source lineage and zero-session carry.
4. Fixed-sleeve liability and settlement arithmetic reconcile exactly.
5. Policy NAV recomputes from exact P2-016 predecessors.
6. Every expected session has a value or explicit permanent null.
7. Simple returns, insolvency, and no-flow rules are deterministic.
8. Immutable storage, replay, tamper, one-IDR, and cross-process tests pass.
9. All new artifacts remain evaluation only.
10. Focused and full verification gates pass.
11. The master checklist records honest evidence only after implementation.

Observed verification evidence:

- RS-P2-017 file: `28 passed`;
- focused seven-file shadow suite: `289 passed`;
- full suite: `1939 passed, 3 skipped` in the authorized unsandboxed rerun;
- the initial sandboxed full run reached `1932 passed, 3 skipped` but the same
  seven `test_model_integration.py` environment-sensitive tests seen in the
  prior P2-016 pass failed there; none failed outside the sandbox;
- repository-wide Ruff `check --fix`: passed and changed no file;
- `uv lock --check`: passed, `Resolved 185 packages`; and
- touched-file `py_compile`: passed.

Feature implementation commit:
`d5ae02fddbb4ba070857e4d6281b2d33afe14b6d`.

Out of scope:

- common metrics, drawdown, Sharpe, DSR, exposure, turnover, calibration, or
  censor aggregation (RS-P2-018 or later);
- report cadence and human-readable reports (RS-P2-023/024);
- a true NAV drawdown gate without a new protocol;
- any component manifest instantiation or A1 grant;
- collection, unblinding, GO/NO-GO, canary, or promotion;
- cross-opportunity fixed-sleeve aggregation;
- external cash flows or recapitalization;
- modification of predecessor economics or live behavior; and
- any threshold, recommendation, ranking, sizing, or execution change.

## 13. Approval boundary

The owner approved NV1–NV8 and NV-N1–NV-N4 for the RS-P2-017 implementation
pass. This approval authorizes additive evaluation-only contracts, producers,
immutable storage/replay, tests, this design document, and the NV-N1
documentation erratum.

It does not:

- grant A1;
- start collection;
- authorize a real component manifest;
- alter `policy_portfolio.py`;
- activate portfolio metrics or drawdown gates;
- change the baseline;
- change live authority; or
- authorize RS-P2-018.
