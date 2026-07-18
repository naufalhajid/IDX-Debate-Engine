# RS-P2-019–022 Gap Specification

**Audit date:** 2026-07-19 (Asia/Jakarta)

**Audited worktree HEAD:** `0d0d94ff09e626b6c3914342bfa55fe07721d86d`

**Mode:** documentation-only gap specification

**Implementation authority:** none

**Live authority / A1 / collection / threshold changes:** none

## 1. Purpose, boundary, and status vocabulary

This document turns every residual cell in the
[RS-P2-019/021/022 reconciliation matrix](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L60)
into a concrete work item that can be converted into an implementation prompt
after RS-P2-018. It also specifies RS-P2-020, which the matrix identified as a
cross-cutting missing global validator rather than a family-local model check
([reconciliation lines 329–350](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L329)).

This pass does not implement any store, validator, test, replay engine, report,
or family contract. RS-P2-016 is being implemented in another worktree; this
document therefore treats policy-portfolio storage as a design contract and
does not assume its eventual class or file names.

The existing coarse matrix uses `COVERED`, `PARTIAL`, and `MISSING`
([legend](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L62)). The finer matrices
below use:

- **D** — direct test of the named capability. For persisted-artifact tamper
  vectors, `D` specifically requires persisted bytes/reference/ledger state;
  for replay semantics it may exercise the exact typed capability without a
  store.
- **P** — partial or adjacent evidence only, such as model-level or in-memory
  validation, a narrower artifact subtype, or an ID collision rather than a
  forced digest collision.
- **G** — implementation or direct-test gap.
- **N/A** — the vector structurally does not apply to that cell; for example, a
  root artifact has no predecessor or a non-ledger family has no ledger tail.

A coarse `COVERED` verdict is not silently upgraded into coverage of every
fine-grained vector. For example, portfolio-state and fixed-notional satisfy
the original replay/hash criterion, but neither has a direct
timezone-equivalence test for all datetime-bearing family artifacts.

## 2. Shared immutable-storage contract

### 2.1 Logical identity and physical content

Every new family must have one stable logical identity:

```text
(protocol_id, manifest_canonical_sha256, artifact_kind, artifact_id)
```

The logical reference and physical bytes serve different purposes:

```text
protocols/{protocol_id}/{manifest_canonical_sha256}/
  {artifact_kind}/objects/
    {artifact_canonical_sha256}/{artifact_raw_file_sha256}.{extension}
  {artifact_kind}/refs/
    {artifact_id}.ref.json
```

For a logical object that legitimately changes state, such as a generic outcome
moving from `PENDING` to `MATURE`, the stable ID must not point to a mutable
file. Use immutable version references plus an append-only event ledger:

```text
protocols/{protocol_id}/{manifest_canonical_sha256}/
  {artifact_kind}/objects/
    {artifact_canonical_sha256}/{artifact_raw_file_sha256}.json
  {artifact_kind}/refs/{artifact_id}/
    {version_or_sequence:010d}.ref.json
  {artifact_kind}_ledgers/{ledger_id}/
    event_blobs/{event_canonical_sha256}/{event_raw_file_sha256}.json
    events/{sequence:010d}.ref.json
```

Every reference must contain at least:

- contract version and all five authority literals;
- protocol, component, manifest, artifact kind, and artifact ID;
- canonical SHA-256, raw-file SHA-256, raw byte length, and canonical relative
  path;
- deterministically sorted, uniquely named predecessor IDs and hashes;
- family-specific chronology, sequence, and transition data.

The loader order is mandatory: root containment and path identity, raw length
and hash, strict duplicate-key JSON parsing, contract version and schema,
canonical bytes/hash, authority literals, then semantic predecessor lineage.
No `latest_*` alias may be a source of truth.

### 2.2 Tail-deletion limitation

Hash chaining alone cannot prove that a locally discovered tail was not deleted.
The validator needs an independently supplied expected head/count/tail
commitment. Closure currently supplies such an anchor for its terminal event,
which is why deleting that event fails closed in
[`test_closure_marker_makes_tail_event_deletion_fail_closed`](../../tests/test_shadow_protocol_governance.py#L1430).
An open approval or outcome ledger has no equivalent durable external
commitment today.

Every new append-only store must therefore emit an immutable
`LedgerCommitment` containing ledger ID, checkpoint ID, event count, tail event
hash, and prior commitment ID/hash:

```text
protocols/{protocol_id}/{manifest_canonical_sha256}/
  ledger_commitments/objects/{canonical_sha256}/{raw_sha256}.json
  ledger_commitments/refs/{ledger_id}/{checkpoint_id}.ref.json
```

The object does not contain its own artifact hash; canonical/raw hashes live in
the path/reference. If a separate `ledger_state_commitment_sha256` is useful,
its domain-tagged preimage is exactly ledger ID, checkpoint, count, tail, and
prior commitment and excludes that hash field itself.

Anchoring is backward-only and two-stage:

1. freeze the graph through ledger tail `N`, then build the commitment over
   that graph;
2. bind the resulting commitment ID/hash in either an external WORM/signed
   checkpoint or a dedicated `CHECKPOINT_COMMITTED` event at `N+1` that is
   explicitly excluded from the graph/tail committed at stage 1.

The anchor and commitment are verified separately. An anchor event must never
be included in the graph whose commitment hash it contains; a later commitment
may include the prior anchor normally. RS-P2-020 must receive an authenticated
commitment plus its anchor proof; directory discovery alone is insufficient.
The implementation prompt must state the detection window for an open,
not-yet-checkpointed tail.

### 2.3 What may be generalized

The most complete reusable precedent is the fixed-notional reference: it binds
dual hashes, byte length, canonical relative path, authority literals, and
ordered named predecessors
([reference model](../../core/shadow_protocol/fixed_notional_store.py#L215),
[strict reload](../../core/shadow_protocol/fixed_notional_store.py#L941),
[persist primitive](../../core/shadow_protocol/fixed_notional_store.py#L1271)).

RS-P2-019 should extract or add a domain-neutral immutable-store kernel for:

- safe path segments, root containment, and symlink/path-substitution defense;
- exclusive-create and identical-retry behavior;
- collision preservation: different bytes at the same claimed address never
  overwrite the winner;
- strict duplicate-key/version/canonical JSON loading;
- dual hashes, byte length, relative-path verification, and named predecessor
  references.

The following remain typed family adapters and must not be reduced to a generic
JSON bag:

- schema loader and contract version;
- artifact-ID derivation;
- required predecessor set;
- chronology and transition rules;
- semantic replay and family-specific parity.

Governance/A1/closure remains a dedicated concurrency-sensitive store.
OutcomeLedger needs its own monotonic append semantics. Rendered report bytes
need a binary/render adapter. Do not make new families subclasses of the
domain-specific `FixedNotionalArtifactStore`; share primitives underneath it.

## 3. RS-P2-019 — versioned immutable output paths

### 3.1 Inventory: stores that already exist

| Family | Current immutable shape | Evidence and verdict |
|---|---|---|
| Candidate raw/set | `{root}/{protocol_id}/{manifest_sha256}/{raw_candidate_sets\|candidate_sets}/{artifact_id}.json`; raw-first, exclusive-create, canonical reload | Store/write/load/path are implemented in [`CandidateSetStore`](../../core/shadow_protocol/evidence.py#L1476) through [its path builder](../../core/shadow_protocol/evidence.py#L1617); retry and raw-capture tamper are exercised by [`test_candidate_store_requires_raw_first_and_rejects_tamper`](../../tests/test_shadow_protocol_p2.py#L1392). **Existing immutable store**, but not a dual-hash/reference envelope. |
| Manifest | `protocols/{protocol_id}/{manifest_canonical_sha256}/manifests/{raw_sha256}/manifest.json` plus immutable revision reference | Dual identity, byte length, methodology binding, and revision ref are written by [`persist_manifest`](../../core/shadow_protocol/governance.py#L615); paths are built at [lines 1676–1717](../../core/shadow_protocol/governance.py#L1676); revision collision is covered by [`test_protocol_manifest_revision_is_globally_immutable`](../../tests/test_shadow_protocol_governance.py#L1145). **Existing immutable store.** |
| Approval / ledger | Dual-hash ApprovalRecord, content-addressed event blobs, and ordered `sequence.ref.json` files | Append is implemented at [`append_approval`](../../core/shadow_protocol/governance.py#L707); contiguous reconstruction begins at [`load_approval_ledger`](../../core/shadow_protocol/governance.py#L1425); event persistence and references are at [lines 1644–1674](../../core/shadow_protocol/governance.py#L1644). **Existing immutable store.** |
| Closure | Dual-hash ClosureRecord, manifest-level closure reference, terminal ledger event | [`append_closure`](../../core/shadow_protocol/governance.py#L848) binds record, reference, and event; closure reference construction/path are at [lines 1745–1784](../../core/shadow_protocol/governance.py#L1745). **Existing immutable store.** |
| Portfolio policy/source/state | Dual-hash blobs; source/state ID references carry raw length, path, and dependency hashes, while policy lookup is manifest-CONFIG-bound | Store starts at [`PortfolioArtifactStore`](../../core/shadow_protocol/portfolio.py#L1888); manifest-bound policy lookup is at [lines 1913–1954](../../core/shadow_protocol/portfolio.py#L1913); source ref/write/load are at [lines 1956–2097](../../core/shadow_protocol/portfolio.py#L1956); state reference/write/reload are at [lines 2099–2292](../../core/shadow_protocol/portfolio.py#L2099); shared path/ref primitives are at [lines 2329–2363](../../core/shadow_protocol/portfolio.py#L2329). **Existing immutable store for all three blobs and full refs for source/state; policy still lacks its own ID ref/raw-length envelope.** |
| Fixed-notional graph | Typed dual-hash objects and ID refs for policy, liquidity, bars, input, lifecycle, holdings, cash flows, paired record, and lineage | Supported kinds are explicit at [lines 62–72](../../core/shadow_protocol/fixed_notional_store.py#L62); write adapters begin at [`FixedNotionalArtifactStore`](../../core/shadow_protocol/fixed_notional_store.py#L517); path/ref layout is at [lines 1401–1438](../../core/shadow_protocol/fixed_notional_store.py#L1401); stored replay is covered by [`test_fixed_notional_store_replay_is_idempotent_and_reconstructable`](../../tests/test_shadow_protocol_p2_015.py#L1419). **Existing immutable store.** |
| Calendar | Global canonical-only blob `trading_calendars/{calendar_sha256}/calendar.json` | Write and load/path-hash checks exist at [`persist_trading_calendar`](../../core/shadow_protocol/governance.py#L661) and [`load_trading_calendar`](../../core/shadow_protocol/governance.py#L1406). **Existing immutable blob**, but no separate raw hash, byte length, or protocol-scoped reference; the gap is recorded at [reconciliation lines 321–327](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L321). |

There is a namespace inconsistency that must be preserved during migration:
`CandidateSetStore` omits the `protocols/` segment
([path lines 1630–1633](../../core/shadow_protocol/evidence.py#L1630)),
while governance, portfolio, and fixed-notional use
`root/protocols/{protocol_id}/{manifest}`
([governance](../../core/shadow_protocol/governance.py#L1819),
[portfolio](../../core/shadow_protocol/portfolio.py#L2357),
[fixed-notional](../../core/shadow_protocol/fixed_notional_store.py#L1432)).
Fixed-notional reconstruction already depends on the legacy candidate namespace
([lines 883–903](../../core/shadow_protocol/fixed_notional_store.py#L883)).
RS-P2-019 must not relocate covered stores in place.

### 3.2 Current-family gap work items

#### RS-P2-019-A — persist the generic paired decision input

Current state: `PairedCandidateDecisionInput` is a strict immutable model with
authority literals and embedded lineage validation
([model lines 50–162](../../core/shadow_protocol/paired_view.py#L50)), but it is
an ephemeral producer input; the reconciliation confirms no path, loader, raw
hash, or byte length
([lines 201–227](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L201)).

Required implementation:

```text
protocols/{protocol_id}/{manifest_sha256}/
  paired_inputs/objects/{canonical_sha256}/{raw_sha256}.json
  paired_inputs/refs/{paired_input_id}.ref.json
```

- Add a deterministic `paired_input_id`, or normatively define the canonical
  SHA-256 as its ID.
- Persist and strictly reload the input before evaluator one; both evaluators
  receive the same reloaded immutable object.
- Add `paired_input_id` and `paired_input_sha256` to the observation lineage.
- Reuse the neutral dual-hash/reference kernel; keep the paired lineage adapter
  in the paired-view domain.

Migration risk: a contract revision is required for paired input and
observation. Observation v1 stores the component IDs/hashes and decisions, not
a `PairedCandidateDecisionInput` artifact ID/hash
([contract lines 1076–1102](../../core/shadow_protocol/contracts.py#L1076)).
Therefore it cannot recover or prove the exact paired-input canonical hash or
raw bytes. The v1 adapter must report that limitation; it must not synthesize a
paired-input identity.

#### RS-P2-019-B — upgrade Observation from immutable blob to full store/load contract

Current state must be described precisely: Observation is persisted, but it is
not a complete dedicated store. `persist_observation()` serializes canonical
bytes and uses the same digest for canonical and raw path identity
([lines 981–1014](../../core/shadow_protocol/governance.py#L981)); its ledger
event records ID, hash, and length
([lines 1055–1093](../../core/shadow_protocol/governance.py#L1055)).
There is no `load_observation_by_id()`. Maturation validates a caller-supplied
object instead of reloading the persisted blob, which is the explicit residual
gap
([reconciliation lines 250–275](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L250)).

Required v2 shape:

```text
protocols/{protocol_id}/{manifest_sha256}/
  observations/objects/{canonical_sha256}/{raw_sha256}.json
  observations/refs/{observation_id}.ref.json
```

The reference must bind paired-input predecessor, ledger event ID/sequence,
dual hashes, raw length, and canonical path. `load_observation_by_id()` must
reload reference → bytes → schema/hashes/path → paired input → portfolio state
→ authorization ledger before maturation can use the observation.

Migration rule: retain a read-only v1 adapter for `{hash}/{hash}.json`; all new
writes use v2. Do not rewrite old bytes or claim a distinct original raw
identity where only canonical bytes were stored. The existing retry/ID-collision
behavior in
[`test_observation_retry_is_idempotent_and_id_collision_fails_cleanly`](../../tests/test_shadow_protocol_governance.py#L1181)
must remain.

Do not reuse the unrelated `core/observation_store.py` as this store. It models
per-agent `AgentObservation` records
([lines 20–40](../../core/observation_store.py#L20)) and appends mutable JSONL
lines
([lines 43–55](../../core/observation_store.py#L43)); it does not implement the
`ShadowObservation` dual-hash/reference contract.

#### RS-P2-019-C — add generic FrozenSnapshot and source-vintage storage

`FrozenSnapshot` already validates canonical payload, point-in-time source
chronology, source-record hash, and snapshot hash
([lines 132–209](../../core/shadow_protocol/evidence.py#L132)), but there is no
generic immutable store. The reconciliation explicitly requires one and warns
that fixed-notional-specific storage does not cover generic outcome evidence
([lines 343–345](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L343)).

Required shape:

```text
protocols/{protocol_id}/{manifest_sha256}/
  snapshots/objects/{canonical_sha256}/{raw_sha256}.json
  snapshots/refs/{snapshot_id}.ref.json
  source_vintages/objects/{canonical_sha256}/{raw_sha256}.json
  source_vintages/refs/{source_record_id}.ref.json
```

- Introduce a typed generic `SourceVintageRecord`; storing only a digest is
  insufficient.
- Bind source definition, payload, as-of/expiry, prior vintage when applicable,
  and every consuming snapshot/bar/outcome.
- Reuse the neutral kernel with typed snapshot/source adapters.

Migration risk: generic observation/candidate records expose snapshot
ID/hash, not the exact embedded snapshot bytes. Exact embedded snapshot bytes
are recoverable today only through persisted fixed-notional `PairInput`
([model lines 733–748](../../core/shadow_protocol/fixed_notional.py#L733);
[loader lines 875–880](../../core/shadow_protocol/fixed_notional_store.py#L875)).
Only that recoverable case may be labeled `LEGACY_CANONICAL_ONLY`; other legacy
records are references without recoverable snapshot bytes. Neither status
proves an original source raw-file identity, and neither may be presented as v2
completeness.

#### RS-P2-019-D — add generic outcome objects and monotonic OutcomeLedger persistence

There is no filesystem store for generic `ShadowOutcome` or `OutcomeLedger`
([reconciliation lines 277–298](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L277)).
The current ledger is an immutable in-memory tuple whose backfill rules replace
pending state monotonically and reject terminal overwrite
([`OutcomeLedger`](../../core/shadow_protocol/outcome_engine.py#L1108)).

Required shape:

```text
protocols/{protocol_id}/{manifest_sha256}/
  outcomes/objects/{canonical_sha256}/{raw_sha256}.json
  outcomes/refs/{outcome_id}/{sequence:010d}.ref.json
  outcome_ledgers/{ledger_id}/
    event_blobs/{canonical_sha256}/{raw_sha256}.json
    events/{sequence:010d}.ref.json
  outcome_bars/...
  outcome_sources/...
  corporate_action_vintages/...
```

- Preserve stable deterministic `outcome_id`, but persist every pending,
  mature, or invalid state as a new immutable version.
- Ledger events bind previous event, previous outcome/source version, new
  outcome object, raw length, exact bars, source vintage, and corporate-action
  evidence.
- Rebuild only from a contiguous sequence and the ledger commitment named by
  the authenticated protocol-run commitment/external anchor. There is no
  writable `latest`.
- Reuse the neutral object/reference kernel; implement a new
  outcome-transition/ledger adapter.

Migration risk: a single exclusive `{outcome_id}.json` reference would require
overwrite during `PENDING → MATURE` and is forbidden. Existing in-memory ledger
hashes are useful logical evidence, not proof of stored-byte replay.

#### RS-P2-019-E — harden calendar identity without losing global deduplication

Retain global deduplication, but separate canonical object identity from
protocol binding:

```text
trading_calendars/{canonical_sha256}/{raw_sha256}.json
protocols/{protocol_id}/{manifest_sha256}/
  trading_calendars/refs/{calendar_id}.ref.json
```

The protocol-scoped reference binds contract version, dual hashes, raw length,
path, manifest calendar ID/hash, and frozen clock semantics. Existing
`{calendar_sha256}/calendar.json` remains read-only legacy. Do not claim its
original raw bytes are known.

#### RS-P2-019-F — additive candidate-store v2 adapter, not an in-place move

Candidate storage already satisfies the original immutable-path baseline. Its
direct `{artifact_id}.json` path is the accepted v1 logical-reference form, but
it does not satisfy the shared dual-hash/reference envelope. Therefore:

- new v2 writes must adopt the shared
  `protocols/.../objects/.../refs/...` envelope;
- legacy candidate paths remain readable;
- fixed-notional and portfolio reconstruction must accept explicit v1/v2
  loaders without directory guessing;
- no migration may change candidate hashes or lineage.

RS-P2-019 closes only after the v2 adapter exists for new writes; it does not
rewrite existing v1 files.

#### RS-P2-019-G — persist canonical-live-payload receipts

RS-P2-020 cannot prove forbidden execution drift unless its pre-shadow and
post-shadow baselines are immutable artifacts rather than caller-owned byte
arguments. The §3.1 store inventory and current shadow package exports
([lines 314–508](../../core/shadow_protocol/__init__.py#L314)) contain no such
receipt; the global reconciler also has no shadow context
([signature lines 153–168](../../core/artifact_validator.py#L153)).

Required shape:

```text
protocols/{protocol_id}/{manifest_sha256}/
  canonical_live_source_inputs/
    objects/{canonical_sha256}/{raw_sha256}.json
    refs/{run_id}.ref.json
  canonical_live_payloads/
    objects/{payload_canonical_sha256}/{payload_raw_sha256}.json
    refs/{run_id}/{capture_boundary}.ref.json
  canonical_live_payload_receipts/
    objects/{canonical_sha256}/{raw_sha256}.json
    refs/{run_id}/{capture_boundary}.ref.json
```

The source-input artifact freezes every byte needed to regenerate the live
projection without database/provider access. `capture_boundary` is the literal
`PRE_SHADOW` or `POST_SHADOW`. Each receipt is an envelope that binds run ID,
source-input ID/hash, projection name/version, protected-field-set version,
boundary, manifest, and an exact payload artifact. It records both:

- the live payload's own `payload_canonical_sha256`,
  `payload_raw_file_sha256`, raw length, and path; and
- the receipt envelope's distinct dual hashes, length, and path.

PRE and POST receipt hashes are expected to differ because the boundary is part
of the envelope. The no-drift comparison is between the embedded live-payload
bytes/canonical hash and protected projection fields—not between the two
receipt-envelope hashes. The pre payload is captured after live
decision/risk/ranking/sizing finalization and before Phase-2 work; the post
payload uses the same projection boundary after shadow work. A capture adapter
at the live orchestration boundary owns these writes but receives no
shadow/ranking/sizing/execution authority. Reuse the neutral kernel with typed
source-input, payload, and receipt adapters.

Migration risk: a receipt created after shadow processing cannot be relabeled
as `PRE_SHADOW`. Runs without a genuine pre receipt remain unverifiable and
must not receive a no-drift claim.

#### RS-P2-019-H — add a trusted protocol-run commitment

A filesystem glob or caller-supplied list cannot prove that an artifact, ledger
event, or entire family was deleted. Add one normative immutable
`ProtocolRunCommitment` containing a typed `ArtifactIndex`:

```text
protocols/{protocol_id}/{manifest_sha256}/
  run_commitments/objects/{canonical_sha256}/{raw_sha256}.json
  run_commitments/refs/{run_id}/{checkpoint_id}.ref.json
```

Its typed index binds manifest revision/hash, exact required family
capabilities, artifact IDs/counts by kind, ledger commitment IDs/counts/tails,
canonical-live source/payload/receipt IDs/hashes, and the versioned
`input_evidence_root_sha256`, `canonical_input_semantic_root_sha256`, and
`expected_output_graph_sha256` defined in §6.3.
Every checkpoint ref binds the prior run-commitment ID/hash when one exists;
open/daily/final checkpoints are new immutable refs, never an overwritten
single run ref.

Bootstrap is explicit: immediately after A1 authorization and before the first
collection artifact, create and anchor checkpoint 0. It commits the approved
manifest/capabilities, the authorized governance tail, and zero counts for
not-yet-created stage families. The first transition validates against this
genesis commitment, then successful transitions create/anchor checkpoint
`N+1`. No validator may infer a synthetic genesis from an empty directory.
The manifest freezes the anchor policy, authorized trust-root ID/hash, and
deterministic run/checkpoint-ID derivation—not the not-yet-created commitment
hash.

After graph tail `N` is frozen, build the commitment; then bind its ID/hash in a
stage-2 external WORM/signed record or dedicated anchor event `N+1`. The stage-2
anchor is outside the committed graph/tail and is compared separately, which
avoids `commitment → anchor → commitment` hashing. A commitment discovered only
beside the artifacts cannot prove its own deletion.

Current `ApprovalLedgerReference` binds ledger/protocol/component and
draft-manifest identity, but has no ledger event count/tail or its own
path/raw-identity fields
([lines 85–98](../../core/shadow_protocol/governance.py#L85)); the in-memory
`OutcomeLedger` contains only its records
([lines 1108–1113](../../core/shadow_protocol/outcome_engine.py#L1108)).

Reuse the neutral object/reference kernel, but add a graph-index builder and
strict verifier. Migration risk: historical directories without an external
commitment may be internally consistent, but cannot be certified complete.
Anchor policy, trust-root identity, and checkpoint-ID derivation require either
a new manifest contract version or a separate policy artifact authorized before
A1. Never append fields to or re-hash an already A1-authorized v2 manifest.
Historical v2 runs remain non-certifiable unless they already have an
independent external anchor.

#### RS-P2-019-I — complete the portfolio-policy reference envelope

`PortfolioArtifactStore.persist_policy()` stores a dual-hash policy blob and
`load_policy_for_manifest()` discovers it through manifest CONFIG identity, but
there is no policy ID reference carrying path/raw length
([lines 1894–1954](../../core/shadow_protocol/portfolio.py#L1894)). Add:

```text
protocols/{protocol_id}/{manifest_sha256}/
  portfolio_policies/refs/{policy_id}.ref.json
```

The immutable ref binds policy ID, manifest CONFIG content `path`, `role`, and
SHA-256 (the current `ContentHash` fields are defined at
[lines 195–205](../../core/shadow_protocol/contracts.py#L195)), dual hashes, raw
length, object path, contract version, and authority envelope. It must not
invent a nonexistent CONFIG ID. Existing manifest-bound lookup remains a
read-only adapter. This is a narrow completion of the existing portfolio store,
not a new portfolio implementation.

### 3.3 Birth contracts for families not present in this worktree

RS-P2-016, 017, and 018 are still open in this worktree
([checklist lines 442–447](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md#L442)),
and the checklist states that the current `rs_p2_017_eligible` marker is not a
NAV implementation and metrics aggregation does not exist
([lines 600–602](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md#L600)).
RS-P2-023 is also open
([lines 630–631](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md#L630)).

| Future family | Required artifact kinds and path contract at birth | Reuse versus new adapter | Additional invariants |
|---|---|---|---|
| RS-P2-016 policy portfolio | `policy_portfolio_inputs`, `policy_portfolio_policies`, `policy_portfolio_events`, `policy_portfolio_states`, `orders`, `holdings`, `cash_flows`, `lifecycles`, and lineage, each under the shared objects/refs envelope | Reuse the neutral object/ref kernel; add a dedicated portfolio-transition and append-ledger adapter. | Same starting capital/risk/cost input on both sides; every transition binds prior state/event, side, candidate/opportunity, calendar, costs, and source vintages. Append-only ledger and externally anchored head commitment are mandatory. |
| RS-P2-017 daily NAV | `nav_mark_inputs`, `nav_points`, `nav_series_events`, and `nav_series_snapshots`; logical NAV ID includes portfolio path and trading date | Reuse the kernel; add a dedicated ordered NAV-series/correction adapter. | Bind exact positions, unsettled/settled cash, prices, calendar, corporate actions, source vintages, and previous NAV event. Corrections are new revisions, never overwritten days. Integer-money and deterministic ratio rules must be explicit. |
| RS-P2-018 common metrics | `metric_input_sets`, `metric_results`, `denominator_records`, and trial/cohort references | Reuse the kernel; add a typed metrics/denominator adapter, not a ledger unless the design truly appends revisions. | Metric ID is deterministic from exact input-set hash, metric definition/version, horizon, and window. Store all numerator/denominator/censor counts and literal `NOT_ESTIMABLE`; zero is never a missing-data substitute. |
| RS-P2-023 reports | `report_data`, `rendered_reports`, and cadence-specific report refs for daily, weekly, monthly, and fixed-terminal output | Reuse the kernel for canonical data/envelopes; add a binary/render adapter with a frozen renderer environment. | Separate canonical report-data JSON from rendered Markdown/PDF bytes. Reference stores data canonical hash, render raw hash/length/MIME, renderer version, window, revision, and metrics/NAV/ledger predecessors. Daily reports bind the previous daily report/ledger commitment. |

The four report cadences are frozen by the protocol:
daily integrity, weekly blinded operations, monthly blinded data quality, and
fixed-terminal unblinded review
([protocol lines 445–450](SHADOW_MODE_PROTOCOL.md#L445)).

### 3.4 RS-P2-019 acceptance gate

RS-P2-019 may close only when:

1. every active family has an explicit versioned loader and logical reference,
   including candidate v2 for all new writes;
2. observation, paired input, generic snapshot/source evidence, generic
   outcome/ledger commitment, and calendar reference satisfy the shared
   envelope;
3. the 016/017/018/023 birth contracts are codified as mandatory acceptance
   criteria; actual compliance is gated when each future family lands, so 019
   does not depend on an unimplemented 023;
4. legacy paths are additive read-only adapters, never silently rewritten;
5. identical retry changes no bytes, references, event count, or ledger tail;
6. same logical ID/different content, same raw digest/address/different raw
   bytes, and same canonical digest/different canonical bytes fail without
   overwriting the winner;
7. canonical-live source input, PRE/POST payloads/receipts, and the
   independently anchored trusted protocol-run commitment exist for new v2
   runs;
8. portfolio policy has the same immutable reference guarantees as source and
   state;
9. no live/ranking/sizing/execution authority is introduced.

## 4. RS-P2-020 — global artifact validator

### 4.1 Current integration boundary

`core/artifact_validator.py` imports stdlib, Pydantic, settings, and logger, but
not `core.shadow_protocol`
([imports lines 3–15](../../core/artifact_validator.py#L3)). Its base validator
accepts only batch JSON, Top-3 Markdown, and latest debate JSON
([`validate_artifacts`](../../core/artifact_validator.py#L116)); reconciliation
adds optional audit, telemetry, and RAG logs but no shadow root/protocol context
([signature lines 153–168](../../core/artifact_validator.py#L153)).
Its generic JSON loader uses plain `json.loads`
([lines 348–355](../../core/artifact_validator.py#L348)), which is not a
duplicate-key trust boundary.

The module is imported by the always-on legacy orchestrator
([`legacy.py` line 87](../../core/orchestrator/legacy.py#L87)), and the current
caller logs invalidity rather than stopping the live pipeline
([lines 6185–6214](../../core/orchestrator/legacy.py#L6185)). This is the correct
authority direction for RS-P2-020: a shadow validation error must invalidate
or quarantine shadow collection/maturation/reporting, but must not mutate or
silently stop the canonical live control payload.

### 4.2 Architecture decision: separate registered rule provider

Use a separate rule module registered through a neutral registry. Do not add
concrete shadow model/store imports directly to `core/artifact_validator.py`.

```text
CLI / explicit composition root
          |
          v
artifact-validation registry  <---- neutral context/finding types
          |
          +---- legacy batch/report rules
          |
          +---- shadow-protocol rule provider
                         |
                         v
                strict family loaders
                         |
                         v
neutral findings --> ReconciliationIssue --> ReconciliationReport
```

Conceptual modules:

- `core/artifact_validation_registry.py` — neutral
  `ArtifactValidationContext`, `ArtifactRuleFinding`, rule protocol, and static
  registry/factory;
- `core/shadow_protocol/validation_rules.py` — imports shadow models/stores,
  traverses the exact immutable graph, returns neutral findings;
- an explicit bootstrap/composition function registers built-in rule providers
  when shadow context is supplied;
- `core/artifact_validator.py` adapts neutral findings into its existing report.

The shadow provider must not import orchestrator, ranking, sizing, risk, or
execution code. Existing evidence is narrower:
[`test_shadow_protocol_module_is_import_isolated_from_live_paths`](../../tests/test_shadow_protocol.py#L745)
parses only `core/shadow_protocol/contracts.py`
([assertion scope lines 745–762](../../tests/test_shadow_protocol.py#L745)).
RS-P2-020 must add an import-boundary test over every shadow module, including
the proposed `validation_rules.py`, rather than treating that one-file test as
package-wide proof.
The live-drift comparison belongs in the global adapter, not inside the shadow
package. Provider import/traversal failure is an error, never a silent skip.

Minimum shadow validation context:

- `store_root`;
- exact `protocol_id`, manifest canonical SHA-256, and ledger ID;
- trusted protocol-run commitment ID/hash and its external/authorization anchor,
  from which expected families, counts, IDs, and ledger commitments are read;
- frozen anchor-proof locator/raw hash plus manifest-authorized trust-root
  ID/hash; verification material cannot come from mutable environment defaults;
- immutable pre/post canonical-live receipt IDs/hashes;
- optional run/report window.

Traversal must authenticate the commitment first, then start from its exact
manifest/revision, family, ledger, and receipt references and follow named
predecessors. It must not accept caller-supplied expectations or a filesystem
glob as proof of completeness.

### 4.3 Validation order

1. Resolve store root and exact namespace; reject root escape, symlink escape,
   and noncanonical path.
2. Read reference and raw bytes; verify raw length and raw SHA-256.
3. Strictly parse UTF-8 JSON with duplicate-key rejection; check root, schema,
   and contract version.
4. Recompute canonical bytes/SHA-256; verify artifact ID, reference identity,
   and content-addressed path.
5. Check authority literals on contract kinds that are required to carry them;
   otherwise require an authority-bearing reference/envelope before semantic
   use.
6. Authenticate the protocol-run commitment, then rebuild manifest, approval,
   ledger, closure, and calendar authorization.
7. Reload raw candidate sets and prove paired opportunity parity.
8. Reload snapshot/source/portfolio/paired-input/observation/outcome
   predecessors and chronology.
9. Run family semantic replay where the family exposes it.
10. Compare the immutable pre/post canonical live payload.
11. Scan for unreachable blobs only after the trusted graph is known.

Independent branches may continue to collect diagnostics after one branch
fails. Descendants of an invalid parent must not be parsed as trusted evidence.
If the live payload is invalid or absent, emit `live_payload_unavailable` and
skip dependent drift comparisons; never derive a baseline by stripping shadow
keys after the fact.

### 4.4 Rule catalog

| Code / category | Input | Failure condition | Severity | Fail-closed behavior |
|---|---|---|---|---|
| `shadow_run_commitment_invalid` / schema | Commitment ID/hash, external/authorization anchor, commitment bytes | Commitment is absent, unauthenticated, noncanonical, or its input-evidence/canonical-semantic/expected-output root or counts do not reconstruct | error | Reject completeness claim and whole shadow surface |
| `shadow_required_artifact_missing` / schema | Authenticated protocol-run commitment, manifest capability, exact ref | A committed/declared/referenced required artifact or checkpoint is absent | error | Stop that family and descendants; invalidate shadow report |
| `shadow_json_invalid` / schema | Raw bytes | Invalid UTF-8/JSON/root or duplicate key | error | Do not instantiate or trust artifact |
| `shadow_schema_invalid` / schema | Parsed object, registered model/version | Missing/extra/type/literal/version violation | error | Stop branch |
| `shadow_required_canonical_encoding_mismatch` / schema | Parsed typed object, stored bytes, family encoding contract | A family that explicitly requires canonical raw encoding stores different bytes; dual-hash families that intentionally preserve noncanonical raw evidence are exempt | error | Stop branch |
| `shadow_unreferenced_blob` / inventory | Trusted graph versus exact committed namespace; reverse-reference-aware shared-store scan | A blob in the committed run namespace is unreachable, or a shared blob has no reference from any manifest; artifacts belonging to another run are excluded | warning; error when an active ledger/index claims it | Never consume blob |
| `shadow_raw_identity_mismatch` / dual hash | Ref and raw bytes | Raw SHA-256 or byte length differs | error | Stop before schema use |
| `shadow_canonical_identity_mismatch` / dual hash | Typed artifact, ref, path | Canonical SHA-256 differs | error | Stop branch |
| `shadow_path_identity_mismatch` / dual hash | Root, ref path, expected namespace | Path escape, sibling substitution, wrong ID/kind, or noncanonical relative path | error | Stop branch |
| `shadow_hash_collision` / dual hash | Two discovered refs/objects in one explicit raw or canonical identity domain | Same raw SHA/full physical address resolves to unequal raw bytes, or same canonical SHA resolves to unequal canonicalized model bytes; equal canonical bytes with distinct raw identities is valid | error | Stop both branches; preserve stored evidence |
| `shadow_store_collision_rejected` / write invariant | Store persist attempt, incumbent bytes, proposed bytes | Same logical ID/digest/address is proposed with unequal bytes, or the incumbent is overwritten | error at persist boundary | Never overwrite; preserve winner; validator later verifies the surviving graph |
| `shadow_legacy_raw_identity_absent` / dual hash | Legacy candidate/calendar/observation | Legacy artifact has no separate raw hash/length | warning for explicit legacy read; error if v2 capability is claimed | Never report v2-complete |
| `shadow_authority_violation` / authority | Authority-bearing contract kinds or their mandatory reference/envelope | A required `evaluation_only`/live/execution/ranking/sizing literal has the wrong value or is absent from both artifact and mandatory envelope | error | Invalidate whole protocol surface |
| `shadow_opportunity_parity_mismatch` / parity | Exact raw captures, views, paired input, observation | Opportunity IDs/hashes, member order/count, empty reason, or exact input differs by side | error | Block paired evaluation, outcomes, and reports |
| `shadow_parity_proof_mismatch` / parity | Stored proof and reconstruction | Canonical proof differs from exact-artifact reconstruction | error | Block descendants |
| `shadow_source_definition_mismatch` / lineage | Manifest sources and source-bearing artifacts | Source ID/definition/hash is not declared or bound | error | Stop descendants |
| `shadow_source_vintage_invalid` / lineage | Source as-of/expiry, signal/evaluation cutoff, eligibility/quarantine disposition | Future, stale, expired, or noncausal evidence is consumed as eligible input contrary to its contract | error | Stop descendants; allow explicit expired/rejected quarantine evidence |
| `shadow_predecessor_chain_broken` / lineage | Named predecessors, outcome/source chain, bar prefix, corporate actions | Missing/hash mismatch, revised prefix, retroactive evidence, or invalid transition | error | Stop ledger/replay |
| `shadow_ledger_tail_mismatch` / lineage | Expected external commitment and reconstructed ledger | Event count or tail hash differs, sequence is noncontiguous, or checkpoint is absent | error | Invalidate ledger and all descendants |
| `shadow_orphan_source_record` / lineage | Trusted graph versus source store | Source artifact is valid but unreachable | warning | Never use it |
| `shadow_live_payload_baseline_missing` / execution drift | Active shadow graph and pre-shadow receipt | No immutable canonical live baseline exists | error | Cannot claim no-drift; invalidate shadow result |
| `shadow_execution_drift` / execution drift | Payload artifacts named by PRE/POST receipt envelopes and versioned projection | Payload bytes/canonical hash or protected decision/risk/rank/sizing/execution field differs; receipt-envelope hashes are not compared to each other | error | Quarantine shadow result; never rewrite live payload |
| `shadow_validator_failed` / integration | Provider invocation | Unexpected import, loader, or traversal exception | error | Report invalid; no fallback to partial trust |

`shadow_store_collision_rejected` is enforced and tested at the store write
boundary; a later read-only reconciliation can report it only if an immutable
failed-write/audit receipt exists. The read-only collision rule instead compares
discovered claims and stored bytes. These two cases must not be conflated.

Raw identity is not universally canonical encoding. Governance loaders
strict-parse and schema-validate preserved raw JSON
([lines 158–174](../../core/shadow_protocol/governance.py#L158)), and
[`test_a1_rejects_raw_only_manifest_reformat`](../../tests/test_shadow_protocol_governance.py#L518)
demonstrates distinct raw bytes for one canonical model. The global rule must
always recompute the canonical model/hash, but demand raw-equals-canonical only
where that family contract says so.

The rule semantics already have family-local precedents:

- strict/frozen model base and the narrower authority-bearing base:
  [`_StrictFrozenModel` and `_EvaluationOnlyArtifact`](../../core/shadow_protocol/contracts.py#L169);
- strict duplicate-key/canonical loaders:
  [governance](../../core/shadow_protocol/governance.py#L1976),
  [portfolio](../../core/shadow_protocol/portfolio.py#L2767), and
  [fixed-notional](../../core/shadow_protocol/fixed_notional_store.py#L1692);
- paired opportunity reconstruction:
  [`verify_opportunity_set_parity`](../../core/shadow_protocol/evidence.py#L959);
- snapshot source lineage:
  [`FrozenSnapshot.verify_snapshot`](../../core/shadow_protocol/evidence.py#L168);
- full generic lineage reconstruction:
  [`build_lineage_bundle`](../../core/shadow_protocol/evidence.py#L1081);
- outcome source/chronology invariants:
  [`ShadowOutcome.verify_outcome_state`](../../core/shadow_protocol/contracts.py#L1242).

Expired evidence remains valid evidence when its disposition is explicitly
quarantined, as represented by `QuarantinedCandidateEvent`
([lines 420–446](../../core/shadow_protocol/evidence.py#L420)); it is invalid
only when consumed as eligible input.

Not every strict frozen model carries the five authority literals:
`TradingCalendar`
([lines 22–44](../../core/shadow_protocol/calendar.py#L22)) and
`OutcomeLedger`
([lines 1108–1113](../../core/shadow_protocol/outcome_engine.py#L1108)) are
examples. The rule therefore applies only to kinds whose contract requires the
literals; other active kinds must be reached through an authority-bearing
reference/envelope. Absence without an active/declared family is
`NOT_APPLICABLE`, not a warning. Once the authenticated commitment and
manifest/capability declare the family required, absence is an error.

### 4.5 Canonical live payload

The drift baseline must be captured after canonical live
decision/risk/ranking/sizing finalization and before Phase-2 instrumentation.
It is an exact, versioned projection or exact canonical payload—not a
post-hoc denylist that removes fields named “shadow.”

Both captures are stored through the RS-P2-019-G receipt contract and named by
the authenticated RS-P2-019-H run commitment. The validator receives their
IDs/hashes, never unauthenticated expected bytes from the caller.

Compare:

1. exact raw/canonical payload artifact named by the PRE receipt;
2. exact raw/canonical payload artifact named by the POST receipt;
3. protected decision, execution status, risk, rank, sizing, and order fields.

PRE/POST receipt-envelope hashes differ by design and are validated
individually; they are not the no-drift operands. Any payload/protected-field
mismatch invalidates the shadow run and triggers preservation/incident evidence.
It does not authorize the validator to edit, roll back, or halt the live control
artifact. This closes the still-open required test that control must be
byte/semantic-equivalent with shadow enabled or disabled
([checklist lines 643–653](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md#L643)).

### 4.6 ReconciliationReport integration

The existing report already has the required transport:
`ReconciliationIssue` contains source, code, severity, message, and optional
ticker
([lines 28–37](../../core/artifact_validator.py#L28));
`ReconciliationReport` contains valid/errors/warnings/issues/surfaces
([lines 59–75](../../core/artifact_validator.py#L59)).
Current base validation findings are already mapped into issues
([lines 170–191](../../core/artifact_validator.py#L170)), and final validity is
`not errors`
([lines 319–332](../../core/artifact_validator.py#L319)).

Two validation entry points are required:

1. provider/registry-level `validate_shadow_transition(context)` validates the
   exact staged artifact and its already-available predecessors at collection
   and maturation time, against the current authenticated checkpoint. It does
   not require batch JSON, Top-3 Markdown, latest debate, a POST receipt, or
   not-yet-due reports;
2. final `reconcile_artifacts(...)` invokes whole-run graph validation after
   final commitment/POST receipt exists, performs execution-drift/report rules,
   and aggregates both staged and final neutral findings.

Each transition context declares a literal stage/capability set from its
authenticated checkpoint, so a future surface is `NOT_APPLICABLE`, not
“missing.” Checkpoint 0 is the anchored post-A1 genesis defined in RS-P2-019-H;
therefore the first artifact never relies on an inferred empty graph. A
successful staged validation permits creation/anchoring of the next immutable
checkpoint; a failed artifact is quarantined and cannot advance it. The final
graph cannot erase an earlier staged error.

Required mapping:

- `source="shadow_protocol"`;
- stable rule code from the catalog;
- errors append to both `issues` and `errors`; warnings to `issues` and
  `warnings`;
- `surfaces["shadow_protocol"]` and
  `surfaces["canonical_live_payload"]` show whether each exact surface was
  supplied;
- one shadow error makes `ReconciliationReport.valid=False`;
- legacy `ValidationReport` remains the three-file validator; shadow findings
  enter at reconciliation level to avoid breaking its existing contract.

`ReconciliationReport.valid=False` is necessary but not sufficient because the
current orchestrator only logs reconciliation findings
([lines 6185–6214](../../core/orchestrator/legacy.py#L6185)). RS-P2-020 must add
a shadow-only acceptance gate driven by `validate_shadow_transition` at every
collection/maturation boundary and final reconciliation at report publication:
an invalid shadow surface is quarantined and cannot be published or matured,
while the canonical live pipeline continues unchanged. An integration test
must prove both halves—invalid shadow output is unavailable to shadow consumers
and the canonical live payload is byte/semantic-identical.

## 5. RS-P2-021 — global tamper matrix

### 5.1 Interpretation

“Hash collision” below means a forced digest/address collision: two unequal
byte payloads are made to claim the same digest/path through an isolated test
seam or pre-seeded address. It does not mean finding a real SHA-256 collision.
An observation-ID collision, revision collision, or competing ledger sequence
is useful but only **P** for this vector.

Identity domains remain separate: a raw collision is one raw SHA/full physical
address resolving to unequal raw bytes; a canonical collision is one canonical
SHA resolving to unequal canonicalized model bytes. Distinct raw encodings may
legitimately share a canonical SHA when their canonicalized bytes are equal and
their raw hashes/paths remain distinct; that is not a collision.

### 5.2 Matrix

Evidence IDs are expanded with exact test names and lines in section 5.3. Gap
IDs are expanded into proposed test names and scenarios in section 5.4.

| Family | Raw byte | Canonical field | Predecessor / lineage | Path substitution | Duplicate key | Byte length | Hash collision | Ledger tail deletion |
|---|---|---|---|---|---|---|---|---|
| Manifest | P M2/M4; G M-G1 | P M1/M6; G M-G2 | N/A for root; P M4 methodology binding; G M-G7 persisted dependency | G M-G3 | P M3; G M-G6 persisted reload | P M1; G M-G4 | P M5; G M-G5 | N/A |
| Approval / ledger | P A1/A3; approval/event blob breadth G A-G1/A-G8 | P A1/A4; G A-G9 blob breadth | G A-G2 | P A4; G A-G3 | P M3 for ApprovalRecord; G A-G4 | G A-G5 | P A2/A5; G A-G6 | P C2 only when closure-anchored; G A-G7 |
| Closure | G C-G1 | G C-G2 | P C2/C3; G C-G3 | G C-G4 | P M3; G C-G7 persisted reload | G C-G5 | P C1; G C-G6 | D C2 |
| Calendar | G K-G1 | P K1; G K-G2 | P K2; G K-G3 | G K-G4 | G K-G5 | G K-G6 | G K-G7 | N/A |
| Candidate raw/set | P V1 raw only; G V-G1 normalized | P V2/V3; G V-G13 persisted | P V3/V4; G V-G14 persisted | G V-G2 | G V-G3 | G V-G4 | G V-G5 | N/A |
| Generic snapshot/source vintage | G S-G1 | P X2; G S-G2 | P X1/X2; G S-G3 | G S-G4 | G S-G5 | G S-G6 | G S-G7 | G S-G8 for version chain |
| Portfolio policy/source/state | P P3/P4/P9; policy G P-G6 | P P5/P6; policy G P-G7 and source/state G P-G8 | P P6/P7/P8; G P-G5 family breadth | G P-G1; source/state now, policy after 019-I | P P2; G P-G4 persisted breadth | G P-G2; source/state now, policy after 019-I | G P-G3 | N/A for exact frozen-state lookup |
| Paired input/view | G V-G6 | P V5/V6 in memory; G V-G7 persisted | P V4; G V-G8 persisted | G V-G9 | G V-G10 | G V-G11 | G V-G12 | N/A |
| Observation | G O-G1 | P O1/O2; G O-G2 persisted | P V4; G O-G3 stored substitution | G O-G4 | G O-G5 | G O-G6 | P O2 ID collision; G O-G7 | G O-G8 |
| Fixed-notional | D F1 | D F2 | D F3/F4/F5 | D F8 | P F6; G F-G2 persisted reload | D F7 | G F-G1 | N/A |
| Outcome / OutcomeLedger | G X-G1 | P X1; G X-G2 persisted | P X1/X2; G X-G3 persisted | G X-G4 | G X-G5 | G X-G6 | G X-G7 | G X-G8 |
| LedgerCommitment | G LC-G1 | G LC-G1 | G LC-G1 | G LC-G1 | G LC-G1 | G LC-G1 | G LC-G1 | G LC-G2 |
| Canonical-live source/payload/receipt | G I-G1 | G I-G1 | G I-G1 | G I-G1 | G I-G1 | G I-G1 | G I-G1 | N/A |
| Protocol-run commitment | G I-G2 | G I-G2 | G I-G2 | G I-G2 | G I-G2 | G I-G2 | G I-G2 | G I-G3 |
| ReplayReceipt | G RR-G1 | G RR-G1 | G RR-G1 | G RR-G1 | G RR-G1 | G RR-G1 | G RR-G1 | N/A |
| RS-P2-016 policy portfolio | G N-G1 | G N-G1 | G N-G1 | G N-G1 | G N-G1 | G N-G1 | G N-G1 | G N-G2 |
| RS-P2-017 daily NAV | G N-G3 | G N-G3 | G N-G3 | G N-G3 | G N-G3 | G N-G3 | G N-G3 | G N-G4 |
| RS-P2-018 metrics | G N-G5 | G N-G5 | G N-G5 | G N-G5 | G N-G5 | G N-G5 | G N-G5 | N/A unless implemented as an append chain |
| RS-P2-023 reports | G N-G6 | G N-G6 | G N-G6 | G N-G6 | G N-G6 | G N-G6 | G N-G6 | G N-G7 for due-report/daily-chain deletion |

### 5.3 Existing tamper evidence catalog

| ID | Exact test and file:line | What it proves |
|---|---|---|
| M1 | [`test_a1_rejects_manifest_hash_or_byte_length_mismatch`](../../tests/test_shadow_protocol_governance.py#L484) | Approval binding rejects declared manifest canonical/raw/length mismatch. |
| M2 | [`test_a1_rejects_raw_only_manifest_reformat`](../../tests/test_shadow_protocol_governance.py#L518) | Same canonical model with different raw bytes is rejected by binding. |
| M3 | [`test_governance_loaders_reject_duplicate_json_keys`](../../tests/test_shadow_protocol_governance.py#L605) | Manifest, ApprovalRecord, and ClosureRecord loaders reject duplicate keys. |
| M4 | [`test_manifest_methodology_bytes_must_match_declared_hash`](../../tests/test_shadow_protocol_governance.py#L864) | Persist-time methodology substitution is rejected; it is not a post-write reload test. |
| M5 | [`test_protocol_manifest_revision_is_globally_immutable`](../../tests/test_shadow_protocol_governance.py#L1145) | Competing content cannot replace a claimed manifest revision. |
| M6 | [`test_manifest_rejects_component_mismatch_and_cluster_hash_tampering`](../../tests/test_shadow_protocol.py#L446) | Model-level canonical manifest identity/cluster tamper is rejected. |
| A1 | [`test_terminal_ledger_event_tampering_is_detected`](../../tests/test_shadow_protocol_governance.py#L969) | Persisted event-reference byte/canonical-field tamper breaks ledger reload. |
| A2 | [`test_exact_manifest_can_bind_only_one_approval_ledger`](../../tests/test_shadow_protocol_governance.py#L1115) | Competing approval-ledger binding loses. |
| A3 | [`test_missing_content_addressed_event_blob_is_rejected`](../../tests/test_shadow_protocol_governance.py#L1343) | Missing referenced event blob fails reload. |
| A4 | [`test_event_reference_hash_mismatch_is_rejected`](../../tests/test_shadow_protocol_governance.py#L1363) | Event reference/hash substitution fails. |
| A5 | [`test_losing_sequence_claim_cannot_corrupt_winning_event`](../../tests/test_shadow_protocol_governance.py#L1385) | Competing same-sequence claim preserves the winner. |
| C1 | [`test_store_is_content_addressed_and_ledger_is_ordered`](../../tests/test_shadow_protocol_governance.py#L906) | Closure exact retry and conflicting closure behavior are exercised at lines 940–966. |
| C2 | [`test_closure_marker_makes_tail_event_deletion_fail_closed`](../../tests/test_shadow_protocol_governance.py#L1430) | Closure reference anchors terminal-tail presence. |
| C3 | [`test_pending_closure_marker_fails_closed_and_only_exact_retry_recovers`](../../tests/test_shadow_protocol_governance.py#L1461) | Interrupted closure is fail-closed and only exact retry recovers. |
| P1 | [`test_store_retry_is_idempotent_and_reference_collision_fails`](../../tests/test_shadow_protocol_p2_014.py#L1002) | Portfolio exact retry plus persisted noncanonical reference-byte tamper rejection. |
| P2 | [`test_store_rejects_duplicate_keys_and_noncanonical_reformatting`](../../tests/test_shadow_protocol_p2_014.py#L1026) | Portfolio policy persist boundary rejects duplicate/noncanonical JSON before write. |
| P3 | [`test_store_detects_tampered_state_bytes`](../../tests/test_shadow_protocol_p2_014.py#L1046) | Persisted state-byte tamper fails reload. |
| P4 | [`test_store_detects_tampered_source_record`](../../tests/test_shadow_protocol_p2_014.py#L1065) | Persisted source-record tamper fails state reload. |
| P5 | [`test_source_reference_rejects_canonical_semantic_tamper`](../../tests/test_shadow_protocol_p2_014.py#L1084) | Canonical source-reference identity drift fails. |
| P6 | [`test_state_reference_rejects_canonical_semantic_tamper`](../../tests/test_shadow_protocol_p2_014.py#L1111) | Canonical state-reference dependency drift fails. |
| P7 | [`test_state_binding_rejects_baseline_identity_drift`](../../tests/test_shadow_protocol_p2_014.py#L1138) | Baseline predecessor mismatch fails. |
| P8 | [`test_state_binding_rejects_opportunity_and_signal_drift`](../../tests/test_shadow_protocol_p2_014.py#L1235) | Opportunity/signal predecessor mismatch fails. |
| P9 | [`test_maturation_reload_rejects_tampered_portfolio_state`](../../tests/test_shadow_protocol_p2_014.py#L1676) | Tampered persisted state fails at maturation reload. |
| V1 | [`test_candidate_store_requires_raw_first_and_rejects_tamper`](../../tests/test_shadow_protocol_p2.py#L1392) | Raw-first order, exact retry, and persisted raw-capture tamper rejection. |
| V2 | [`test_store_revalidates_model_copy_before_any_write`](../../tests/test_shadow_protocol_p2.py#L1413) | Canonical/model tamper fails before candidate write. |
| V3 | [`test_disposition_order_and_exact_opportunity_parity`](../../tests/test_shadow_protocol_p2.py#L1426) | Ordered exact opportunity-set parity. |
| V4 | [`test_full_lineage_recomputes_manifest_snapshot_source_bars_and_outcome`](../../tests/test_shadow_protocol_p2.py#L2827) | In-memory full lineage and snapshot/outcome tamper rejection. |
| V5 | [`test_paired_evaluators_receive_same_immutable_input`](../../tests/test_shadow_protocol_p2_014.py#L1456) | Both evaluators receive one immutable in-memory paired input. |
| V6 | [`test_evaluator_mutation_fails_before_second_side`](../../tests/test_shadow_protocol_p2_014.py#L1495) | In-memory paired-input mutation fails before the second evaluator. |
| F1 | [`test_fixed_notional_store_rejects_raw_file_tampering`](../../tests/test_shadow_protocol_p2_015.py#L1750) | Direct raw-byte identity rejection. |
| F2 | [`test_fixed_notional_store_rejects_canonical_model_tampering`](../../tests/test_shadow_protocol_p2_015.py#L1768) | Coordinated raw/path rewrite still fails canonical identity. |
| F3 | [`test_fixed_notional_store_rejects_tampered_raw_capture_predecessor`](../../tests/test_shadow_protocol_p2_015.py#L1516) | Base candidate predecessor tamper fails lineage reload. |
| F4 | [`test_paired_reference_predecessor_drift_is_rejected`](../../tests/test_shadow_protocol_p2_015.py#L1612) | Named predecessor hash drift fails. |
| F5 | [`test_lineage_reference_names_and_verifies_predecessor_hashes`](../../tests/test_shadow_protocol_p2_015.py#L1646) | Lineage ref names and validates predecessor hashes. |
| F6 | [`test_fixed_notional_loader_rejects_duplicate_keys`](../../tests/test_shadow_protocol_p2_015.py#L1811) | Duplicate key fails strict loader. |
| F7 | [`test_fixed_notional_store_rejects_reference_byte_length_drift`](../../tests/test_shadow_protocol_p2_015.py#L1827) | Reference byte-length drift fails. |
| F8 | [`test_fixed_notional_store_rejects_reference_path_substitution`](../../tests/test_shadow_protocol_p2_015.py#L1853) | Explicit wrong-namespace path substitution fails. |
| O1 | [`test_observation_requires_roles_identity_and_derived_divergence`](../../tests/test_shadow_protocol.py#L480) | Model-level observation role/identity/divergence validation. |
| O2 | [`test_observation_retry_is_idempotent_and_id_collision_fails_cleanly`](../../tests/test_shadow_protocol_governance.py#L1181) | Observation exact retry and logical-ID collision. |
| X1 | [`test_backfill_rejects_revised_prefix_terminal_overwrite_and_forged_math`](../../tests/test_shadow_protocol_p2.py#L2679) | In-memory outcome transition/source-prefix/math tamper rejection. |
| X2 | [`test_full_lineage_recomputes_manifest_snapshot_source_bars_and_outcome`](../../tests/test_shadow_protocol_p2.py#L2827) | In-memory snapshot/source/bar/outcome lineage reconstruction. |
| K1 | [`test_solo_a1_recomputes_exact_sessions_from_frozen_calendar`](../../tests/test_shadow_protocol_governance.py#L643) | Trusted calendar sessions are re-derived. |
| K2 | [`test_a1_calendar_must_match_manifest`](../../tests/test_shadow_protocol_governance.py#L846) | Manifest/approval calendar substitution fails. |

### 5.4 Gap tests and scenarios

#### Manifest

- **M-G1** `test_manifest_store_reload_rejects_raw_byte_tamper`: mutate
  persisted `manifest.json` without changing semantics; authorization reload
  fails against raw identity.
- **M-G2** `test_manifest_store_reload_rejects_canonical_field_tamper`: change a
  valid canonical field and coordinate raw metadata; revision/canonical
  identity still fails.
- **M-G3** `test_manifest_reference_rejects_path_substitution`: point the
  revision ref to another valid manifest namespace.
- **M-G4** `test_manifest_reference_rejects_byte_length_drift`: change length by
  ±1 and fail before model use.
- **M-G5** `test_manifest_store_rejects_forced_digest_collision_without_overwrite`:
  force two unequal manifests to the same address; the second fails and first
  bytes remain identical.
- **M-G6** `test_manifest_authorization_reload_rejects_persisted_duplicate_keys`:
  rewrite stored manifest bytes with a duplicate key and require reload failure.
- **M-G7** `test_manifest_authorization_reload_rejects_persisted_methodology_tamper`:
  mutate the stored methodology dependency after persist and require
  authorization reload failure.

#### Approval / ledger

- **A-G1** `test_approval_record_blob_tamper_fails_authorization_reload`.
- **A-G2** `test_approval_ledger_event_predecessor_tamper_fails_reload`: alter
  `previous_event_sha256` while keeping valid canonical JSON.
- **A-G3** `test_approval_event_reference_rejects_content_address_substitution`:
  transplant another valid event blob/hash.
- **A-G4** `test_approval_ledger_event_and_references_reject_duplicate_keys`:
  parameterize event blob, event ref, and ledger ref.
- **A-G5** `test_approval_event_reference_rejects_byte_length_drift`.
- **A-G6** `test_governance_store_rejects_forced_event_digest_collision_without_overwrite`.
- **A-G7** `test_open_approval_ledger_tail_deletion_fails_closed`: persist
  approval plus observations, checkpoint the expected head externally, delete
  the last observation ref/blob, and require head/count/tail mismatch.
- **A-G8** `test_approval_event_blob_byte_tamper_fails_ledger_reload`: mutate
  the content-addressed event blob itself, not only its sequence reference.
- **A-G9** `test_approval_record_and_event_blob_reject_coordinated_canonical_field_tamper`:
  alter a semantic field and coordinate raw hash/length/path metadata so raw
  checks pass; canonical identity/authorization binding must still fail.

#### Closure

- **C-G1** `test_closure_record_raw_byte_tamper_fails_authorization_reload`.
- **C-G2** `test_closure_record_canonical_field_tamper_fails_authorization_reload`.
- **C-G3** `test_closure_reference_rejects_predecessor_or_ledger_binding_drift`.
- **C-G4** `test_closure_reference_rejects_content_address_substitution`.
- **C-G5** `test_closure_reference_rejects_byte_length_drift`.
- **C-G6** `test_closure_store_rejects_forced_digest_collision_without_overwrite`.
- **C-G7** `test_closure_authorization_reload_rejects_persisted_duplicate_keys`.

#### Calendar

- **K-G1** `test_calendar_loader_rejects_persisted_raw_byte_tamper`.
- **K-G2** `test_calendar_loader_rejects_canonical_session_tamper`.
- **K-G3** `test_calendar_reference_rejects_manifest_binding_substitution`.
- **K-G4** `test_calendar_reference_rejects_path_substitution`.
- **K-G5** `test_calendar_loader_rejects_duplicate_keys`.
- **K-G6** `test_calendar_reference_rejects_byte_length_drift`.
- **K-G7** `test_calendar_store_rejects_forced_digest_collision_without_overwrite`.

#### Candidate and paired input

- **V-G1** `test_candidate_normalized_store_rejects_persisted_raw_byte_tamper`.
- **V-G2** `test_candidate_reference_rejects_path_substitution`.
- **V-G3** `test_candidate_loaders_reject_duplicate_keys`.
- **V-G4** `test_candidate_reference_rejects_byte_length_drift`.
- **V-G5** `test_candidate_store_rejects_forced_digest_collision_without_overwrite`.
- **V-G6** `test_paired_input_reload_rejects_raw_byte_tamper`.
- **V-G7** `test_paired_input_reload_rejects_canonical_field_tamper`.
- **V-G8** `test_paired_input_reference_rejects_predecessor_drift`.
- **V-G9** `test_paired_input_reference_rejects_path_substitution`.
- **V-G10** `test_paired_input_loader_rejects_duplicate_keys`.
- **V-G11** `test_paired_input_reference_rejects_byte_length_drift`.
- **V-G12** `test_paired_input_store_rejects_forced_digest_collision_without_overwrite`.
- **V-G13** `test_candidate_store_reload_rejects_persisted_canonical_field_tamper`:
  coordinate stored bytes/raw metadata while retaining the claimed canonical
  candidate identity; strict reload fails.
- **V-G14** `test_candidate_set_reload_rejects_raw_capture_or_opportunity_predecessor_substitution`:
  transplant a valid sibling raw-capture/opportunity predecessor so failure is
  lineage/parity, not missing-file or schema failure.

The paired-input tests are blocked until RS-P2-019-A provides its store.

#### Snapshot / source vintage

- **S-G1** `test_snapshot_source_store_rejects_raw_byte_tamper`.
- **S-G2** `test_snapshot_source_store_rejects_canonical_field_tamper`.
- **S-G3** `test_snapshot_source_reference_rejects_predecessor_drift`.
- **S-G4** `test_snapshot_source_reference_rejects_path_substitution`.
- **S-G5** `test_snapshot_source_loader_rejects_duplicate_keys`.
- **S-G6** `test_snapshot_source_reference_rejects_byte_length_drift`.
- **S-G7** `test_snapshot_source_store_rejects_forced_digest_collision_without_overwrite`.
- **S-G8** `test_source_vintage_tail_deletion_fails_closed`: delete the newest
  vintage while retaining its predecessor; an external current-vintage
  commitment exposes the deletion.

#### Portfolio

- **P-G1** `test_portfolio_reference_rejects_path_substitution_for_each_kind`:
  parameterize policy, source, and state refs.
- **P-G2** `test_portfolio_reference_rejects_byte_length_drift_for_each_kind`.
- **P-G3** `test_portfolio_store_rejects_forced_digest_collision_without_overwrite`.
- **P-G4** `test_portfolio_store_reload_rejects_persisted_duplicate_keys_for_each_kind`
  covers policy, source, state, and references after write.
- **P-G5** `test_portfolio_state_reference_rejects_previous_state_sha256_transplant`:
  transplant a valid sibling predecessor hash into a persisted state reference;
  strict reload must fail.
- **P-G6** `test_portfolio_policy_store_rejects_persisted_raw_byte_tamper`:
  mutate policy object bytes and require manifest-bound policy reload failure.
- **P-G7** `test_portfolio_policy_store_rejects_canonical_field_tamper`:
  coordinate valid JSON/raw metadata but change policy semantics; canonical
  identity and CONFIG binding still fail.
- **P-G8** `test_portfolio_source_and_state_store_reject_coordinated_canonical_field_tamper`:
  parameterize persisted source/state objects, update raw hash/length/path ref
  metadata, retain the original claimed canonical identity, and require reload
  failure.

#### Observation

- **O-G1** `test_observation_maturation_reload_rejects_raw_byte_tamper`.
- **O-G2** `test_observation_maturation_reload_rejects_canonical_field_tamper`.
- **O-G3** `test_observation_maturation_reload_rejects_lineage_substitution`.
- **O-G4** `test_observation_reference_rejects_path_substitution`.
- **O-G5** `test_observation_loader_rejects_duplicate_keys`.
- **O-G6** `test_observation_reference_rejects_byte_length_drift`.
- **O-G7** `test_observation_store_rejects_forced_digest_collision_without_overwrite`.
- **O-G8** `test_observation_authorization_tail_deletion_fails_closed`: remove
  the last authorized observation event after checkpoint and require
  commitment mismatch.

These tests require the dedicated observation loader/reference in RS-P2-019-B.

#### Fixed-notional

- **F-G1** `test_fixed_notional_store_rejects_forced_digest_collision_without_overwrite`:
  force the same private digest/address for distinct canonical inputs; second
  persist fails and the original graph remains reconstructable.
- **F-G2** `test_fixed_notional_store_reload_rejects_persisted_duplicate_keys`.

#### Outcome / ledger

- **X-G1** `test_outcome_store_rejects_raw_byte_tamper`.
- **X-G2** `test_outcome_store_rejects_canonical_field_tamper`.
- **X-G3** `test_outcome_reference_rejects_predecessor_lineage_tamper`.
- **X-G4** `test_outcome_reference_rejects_path_substitution`.
- **X-G5** `test_outcome_loader_rejects_duplicate_keys`.
- **X-G6** `test_outcome_reference_rejects_byte_length_drift`.
- **X-G7** `test_outcome_store_rejects_forced_digest_collision_without_overwrite`.
- **X-G8** `test_outcome_ledger_tail_deletion_fails_closed`: validate against
  the authenticated run commitment's event-count/tail after deleting the
  newest outcome version.

#### Ledger, live, run, and replay commitments

- **LC-G1** `test_ledger_commitment_store_tamper_matrix[...]` covers persisted
  raw byte; coordinated count/tail canonical field; ledger/checkpoint
  predecessor substitution; valid-sibling path; duplicate key; byte length;
  and forced collision.
- **LC-G2** `test_ledger_commitment_and_external_anchor_expose_tail_deletion`:
  authenticate the stage-2 anchor, delete tail `N` or its ref/blob, and require
  committed count/tail mismatch; deleting/substituting the anchor proof also
  fails separately.
- **I-G1** `test_canonical_live_source_payload_and_receipt_tamper_matrix[...]`
  parameterizes source-input object/ref, PRE/POST payload object/ref, and receipt
  envelope across persisted raw byte, protected canonical field/boundary,
  source→payload→receipt lineage substitution, valid-sibling path, duplicate
  key, byte length, and forced collision.
- **I-G2** `test_protocol_run_commitment_tamper_matrix[...]` covers the same
  vectors, including family-count/root-graph/predecessor-anchor substitution.
- **I-G3** `test_protocol_run_commitment_exposes_artifact_or_ledger_tail_deletion`:
  authenticate a commitment from an independent anchor, delete the last
  artifact/ref/event, and require count/tail/root mismatch. Merely rebuilding a
  self-consistent local graph after deletion must not count as completeness.
- **RR-G1** `test_replay_receipt_store_tamper_matrix[...]` covers raw receipt
  bytes, actual-output-root/engine canonical fields, input-commitment
  predecessor, valid-sibling path, duplicate key, byte length, and forced
  collision.

#### Future-family birth tests

- **N-G1** `test_policy_portfolio_store_tamper_matrix[...]` covers raw byte,
  canonical field, predecessor transplant, path substitution, duplicate key,
  byte length, and forced collision.
- **N-G2** `test_policy_portfolio_ledger_tail_deletion_fails_closed`: delete the
  newest committed transition and require independently anchored
  count/head/tail mismatch.
- **N-G3** `test_daily_nav_store_tamper_matrix[...]` covers raw byte,
  coordinated canonical-field, predecessor transplant, valid-sibling path,
  duplicate key, byte length, and forced collision.
- **N-G4** `test_daily_nav_chain_tail_deletion_fails_closed`: delete the newest
  committed NAV point/correction and require independently anchored
  count/head/tail mismatch.
- **N-G5** `test_common_metrics_store_tamper_matrix[...]` covers raw byte,
  coordinated canonical-field, denominator/input predecessor transplant,
  valid-sibling path, duplicate key, byte length, and forced collision.
- **N-G6** `test_shadow_report_store_tamper_matrix[...]` covers report-data
  JSON/report-ref duplicate keys; rendered-byte hash, length, and MIME
  mismatch; metrics/NAV/ledger/previous-daily predecessor transplant;
  valid-sibling report-path substitution; raw/canonical tamper; and forced
  collisions for both canonical report data and rendered bytes.
- **N-G7** `test_daily_report_chain_tail_or_due_report_deletion_fails_closed`:
  delete the newest daily report or an expected cadence report and require the
  authenticated schedule/count/head commitment to expose it.

Metrics need a tail-deletion test only if implemented as an ordered append
chain; otherwise their schema must not claim ledger semantics. Reports are not
optional here: the birth contract binds each daily report to its predecessor
and the trusted run commitment declares every due cadence, so N-G7 is required.

## 6. RS-P2-022 — replay, idempotency, and canonical-hash matrix

### 6.1 Matrix

The absence anchors for this decomposition are explicit: manifest retry and
cross-process gaps
([reconciliation lines 118–120](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L118));
approval/ledger retry and cross-process gaps
([lines 147–149](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L147));
paired-input path/hash gaps
([lines 221–227](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L221));
outcome store/stored-byte gaps
([lines 277–298](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L277));
calendar retry/hash/raw-identity gaps
([lines 321–327](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L321));
generic snapshot/source storage
([lines 343–345](SHADOW_STATUS_RECONCILIATION_2026-07-18.md#L343));
unborn 016–018 families
([checklist lines 442–447](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md#L442));
and the open report family
([lines 630–633](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md#L630)).

| Family | Deterministic replay | Idempotent persist | Cross-process hash | Timezone equivalence | Full frozen replay |
|---|---|---|---|---|---|
| Manifest | P [`test_canonical_hash_is_stable_and_full_length`](../../tests/test_shadow_protocol.py#L473); G R-G25 stored-byte replay | G R-G1 | G R-G2 | G R-G3 | G E-G1 |
| Approval / ledger | D ordered reconstruction in [`test_store_is_content_addressed_and_ledger_is_ordered`](../../tests/test_shadow_protocol_governance.py#L906) | G R-G4 initial append | G R-G5 | G R-G6 | G E-G1 |
| Closure | D exact retry/recovery in [`test_store_is_content_addressed_and_ledger_is_ordered`](../../tests/test_shadow_protocol_governance.py#L906), assertions at [lines 940–953](../../tests/test_shadow_protocol_governance.py#L940), and [`test_pending_closure_marker_fails_closed_and_only_exact_retry_recovers`](../../tests/test_shadow_protocol_governance.py#L1461) | D same closure retry in [`test_store_is_content_addressed_and_ledger_is_ordered`](../../tests/test_shadow_protocol_governance.py#L906), assertions at [lines 946–953](../../tests/test_shadow_protocol_governance.py#L946) | G R-G5 | G R-G6 | G E-G1 |
| Calendar | P trusted derivation/load in [`test_solo_a1_recomputes_exact_sessions_from_frozen_calendar`](../../tests/test_shadow_protocol_governance.py#L643); G R-G26 clean stored replay | G R-G7 | G R-G8 | N/A for date-only calendar hash; chronology G R-G9 | G E-G1 |
| Candidate raw/set | P raw-first persistence/idempotent retry plus tampered-load rejection in [`test_candidate_store_requires_raw_first_and_rejects_tamper`](../../tests/test_shadow_protocol_p2.py#L1392); G R-G27 clean reload | D identical raw/set retry in [`test_candidate_store_requires_raw_first_and_rejects_tamper`](../../tests/test_shadow_protocol_p2.py#L1392), assertions at [lines 1399–1402](../../tests/test_shadow_protocol_p2.py#L1399) | G R-G10 | G R-G11 | G E-G1 |
| Generic snapshot/source | P in-memory graph reconstruction in [`test_full_lineage_recomputes_manifest_snapshot_source_bars_and_outcome`](../../tests/test_shadow_protocol_p2.py#L2827); G R-G12 stored replay | G R-G12 | G R-G13 | P source/action hashes only in [`test_timezone_equivalent_instants_have_identical_ids_and_hashes`](../../tests/test_shadow_protocol_p2.py#L2885); G R-G14 | G E-G1 |
| Portfolio policy/source/state | P same-input observation replay and in-memory lineage in [`test_identical_inputs_replay_identical_state_and_observation_hashes`](../../tests/test_shadow_protocol_p2_014.py#L1568) and [`test_lineage_v2_reconstructs_policy_source_state_and_observation`](../../tests/test_shadow_protocol_p2_014.py#L1634); G R-G29 stored-predecessor replay | P state persist only in [`test_store_retry_is_idempotent_and_reference_collision_fails`](../../tests/test_shadow_protocol_p2_014.py#L1002); G R-G30 family breadth | P state only in [`test_state_hash_is_identical_across_separate_python_processes`](../../tests/test_shadow_protocol_p2_014.py#L1723); G R-G15 breadth | G R-G16 | G E-G1 |
| Paired input/view | P in-memory immutable-input/mutation guards in [`test_paired_evaluators_receive_same_immutable_input`](../../tests/test_shadow_protocol_p2_014.py#L1456) and [`test_evaluator_mutation_fails_before_second_side`](../../tests/test_shadow_protocol_p2_014.py#L1495); G R-G17 stored replay | G R-G17 | G R-G18 | P final observation only; paired input G R-G19 | G E-G1 |
| Observation | D deterministic observation in [`test_identical_inputs_replay_identical_state_and_observation_hashes`](../../tests/test_shadow_protocol_p2_014.py#L1568) | D [`test_observation_retry_is_idempotent_and_id_collision_fails_cleanly`](../../tests/test_shadow_protocol_governance.py#L1181) | G R-G20 | D [`test_timezone_equivalent_instants_have_identical_ids_and_hashes`](../../tests/test_shadow_protocol_p2.py#L2885), observation assertions at [lines 2946–2958](../../tests/test_shadow_protocol_p2.py#L2946) | G E-G1 |
| Fixed-notional | D [`test_replay_is_idempotent_and_detects_record_drift`](../../tests/test_shadow_protocol_p2_015.py#L1994) | D [`test_fixed_notional_store_replay_is_idempotent_and_reconstructable`](../../tests/test_shadow_protocol_p2_015.py#L1419) | D policy and graph in [`test_policy_hash_is_identical_in_a_separate_python_process`](../../tests/test_shadow_protocol_p2_015.py#L2051) and [`test_pair_lifecycle_and_record_hashes_are_identical_cross_process`](../../tests/test_shadow_protocol_p2_015.py#L2073) | G R-G21 | G E-G1; family-local only |
| Outcome / ledger | P in-memory deterministic transition/order in [`test_backfill_rejects_revised_prefix_terminal_overwrite_and_forged_math`](../../tests/test_shadow_protocol_p2.py#L2679) and [`test_outcome_ledger_is_side_specific_and_order_deterministic`](../../tests/test_shadow_protocol_p2.py#L2790); G R-G28 stored replay | P in-memory unchanged retry in [`test_vintage_aware_backfill_updates_pending_and_matures`](../../tests/test_shadow_protocol_p2.py#L2520); G R-G22 stored | G R-G23 | P ID/source/action hashes in [`test_timezone_equivalent_instants_have_identical_ids_and_hashes`](../../tests/test_shadow_protocol_p2.py#L2885), assertions at [lines 2885–2944](../../tests/test_shadow_protocol_p2.py#L2885); G R-G24 full family | G E-G1 |
| LedgerCommitment | G LC-R1 | G LC-R2 | G LC-R3 | G LC-R4 | G E-G1 |
| Canonical-live source/payload/receipt | G I-R1 | G I-R2 | G I-R3 | G I-R4 | G E-G1 |
| Protocol-run commitment | G I-R5 | G I-R6 | G I-R7 | G I-R8 | G E-G1 |
| ReplayReceipt | G RR-R1 | G RR-R2 | G RR-R3 | G RR-R4 | G E-G1 |
| RS-P2-016 policy portfolio | G N-R1 | G N-R2 | G N-R3 | G N-R4 | G E-G1 |
| RS-P2-017 NAV | G N-R5 | G N-R6 | G N-R7 | G N-R8 | G E-G1 |
| RS-P2-018 metrics | G N-R9 | G N-R10 | G N-R11 | G N-R12 | G E-G1 |
| RS-P2-023 reports | G N-R13 | G N-R14 | G N-R15 | G N-R16 | G E-G2 |

### 6.2 Family replay gap tests

#### Governance and calendar

- **R-G1** `test_manifest_persist_exact_retry_is_idempotent`: persist identical
  manifest/methodology twice; returned path, revision-ref bytes, file tree, and
  revision count are unchanged.
- **R-G2** `test_manifest_hash_and_canonical_bytes_are_identical_cross_process`.
- **R-G3** `test_manifest_timezone_equivalent_instants_hash_identically`.
- **R-G25** `test_manifest_replay_from_stored_bytes_reproduces_canonical_model_and_hash`.
- **R-G4** `test_approval_initial_append_exact_retry_is_idempotent`: exact
  second `append_approval()` creates no second event/ref/blob.
- **R-G5** `test_governance_records_and_ledger_hashes_are_identical_cross_process`,
  parameterized for ApprovalRecord, event, reconstructed ledger, and closure.
- **R-G6** `test_governance_family_timezone_equivalent_instants_hash_identically`.
- **R-G7** `test_trading_calendar_persist_exact_retry_is_idempotent`.
- **R-G8** `test_trading_calendar_hash_is_identical_cross_process`.
- **R-G9** `test_completed_session_derivation_is_timezone_equivalent`.
- **R-G26** `test_trading_calendar_replay_from_stored_bytes_reproduces_sessions_and_hash`.

#### Candidate, snapshot, paired input, and observation

- **R-G10** `test_candidate_family_hashes_are_identical_cross_process`.
- **R-G11** `test_candidate_family_timezone_equivalent_instants_hash_identically`.
- **R-G27** `test_candidate_store_reloads_exact_raw_and_manifest_bytes_and_hashes`
  uses two fresh store instances and asserts a successful clean reload.
- **R-G12** `test_snapshot_source_store_replay_is_idempotent_and_reconstructable`.
- **R-G13** `test_snapshot_source_hashes_are_identical_cross_process`.
- **R-G14** `test_snapshot_source_timezone_equivalent_instants_hash_identically`.
- **R-G17** `test_paired_input_store_replay_is_idempotent_and_reconstructable`.
- **R-G18** `test_pair_input_and_paired_observation_hashes_are_identical_cross_process`.
- **R-G19** `test_pair_input_timezone_equivalent_instants_hash_identically`.
- **R-G20** `test_observation_hash_is_identical_cross_process`.

#### Portfolio and fixed-notional

- **R-G15** `test_portfolio_family_hashes_are_identical_cross_process` covers
  policy, source, state, and references, extending the current state-only proof.
- **R-G16** `test_portfolio_family_timezone_equivalent_instants_hash_identically`.
- **R-G29** `test_portfolio_state_replay_from_stored_predecessors_is_deterministic`.
- **R-G30** `test_portfolio_family_persist_exact_retry_is_idempotent` covers
  policy, source, state, and reference bytes/counts.
- **R-G21** `test_fixed_notional_family_timezone_equivalent_instants_hash_identically`
  covers pair input, liquidity, bars, lifecycle, holding, cash flow, and paired
  record.

#### Outcome

- **R-G28** `test_outcome_store_replay_from_stored_bytes_is_deterministic`:
  reload every predecessor, rebuild maturation input, evaluate, and compare
  exact outcome IDs/canonical/raw hashes.
- **R-G22** `test_outcome_store_exact_retry_and_backfill_are_idempotent`: same
  persist changes neither object/ref count nor ledger tail; mature retry is
  byte-identical.
- **R-G23** `test_outcome_and_ledger_hashes_are_identical_cross_process`.
- **R-G24** `test_outcome_family_timezone_equivalent_instants_hash_identically`
  covers full ShadowOutcome, source/action records, refs, events, and ledger.

#### Ledger, live, run, and replay commitments

- **LC-R1** `test_ledger_commitment_reconstructs_exact_checkpoint_deterministically`
  also proves any ledger-state hash preimage excludes that hash and the
  artifact's own canonical hash remains reference/path metadata.
- **LC-R2** `test_ledger_commitment_persist_exact_retry_is_idempotent`.
- **LC-R3** `test_ledger_commitment_hashes_are_identical_cross_process`.
- **LC-R4** `test_ledger_commitment_timezone_equivalent_instants_normalize_semantically`.
- **I-R1** `test_canonical_live_source_payload_and_receipt_replay_is_deterministic`
  covers all three persisted kinds and derives capture timestamps only from a
  named frozen checkpoint/cutoff.
- **I-R2** `test_canonical_live_source_payload_and_receipt_persist_exact_retry_is_idempotent`
  covers every object/ref and both capture boundaries.
- **I-R3** `test_canonical_live_source_payload_and_receipt_hashes_are_identical_cross_process`.
- **I-R4** `test_canonical_live_source_payload_and_receipt_timezone_equivalent_instants_normalize_semantically`.
- **I-R5** `test_protocol_run_commitment_reconstructs_exact_graph_deterministically`
  uses a frozen checkpoint timestamp, never wall clock.
- **I-R6** `test_protocol_run_commitment_persist_exact_retry_is_idempotent`.
- **I-R7** `test_protocol_run_commitment_hashes_are_identical_cross_process`.
- **I-R8** `test_protocol_run_commitment_timezone_variants_bind_distinct_raw_roots_and_equal_semantic_roots`:
  each encoding has its own authenticated raw-evidence commitment, while
  canonical semantic and expected-output roots match.
- **RR-R1** `test_replay_receipt_reconstructs_actual_output_root_deterministically`
  derives every timestamp from the frozen checkpoint.
- **RR-R2** `test_replay_receipt_persist_exact_retry_is_idempotent`.
- **RR-R3** `test_replay_receipt_hashes_are_identical_cross_process`.
- **RR-R4** `test_replay_receipt_timezone_variants_share_canonical_output_root`.

#### Future-family birth tests

Each future implementation must land four direct tests:

- **N-R1** `test_policy_portfolio_replay_from_stored_predecessors_is_deterministic`.
- **N-R2** `test_policy_portfolio_persist_exact_retry_is_idempotent`.
- **N-R3** `test_policy_portfolio_hashes_are_identical_cross_process`.
- **N-R4** `test_policy_portfolio_timezone_equivalent_instants_hash_identically`.
- **N-R5** `test_daily_nav_replay_from_stored_predecessors_is_deterministic`.
- **N-R6** `test_daily_nav_persist_exact_retry_is_idempotent`.
- **N-R7** `test_daily_nav_hashes_are_identical_cross_process`.
- **N-R8** `test_daily_nav_timezone_equivalent_instants_hash_identically`.
- **N-R9** `test_common_metrics_replay_from_stored_predecessors_is_deterministic`.
- **N-R10** `test_common_metrics_persist_exact_retry_is_idempotent`.
- **N-R11** `test_common_metrics_hashes_are_identical_cross_process`.
- **N-R12** `test_common_metrics_timezone_equivalent_instants_hash_identically`.
- **N-R13** `test_shadow_report_replay_from_stored_predecessors_is_deterministic`.
- **N-R14** `test_shadow_report_persist_exact_retry_is_idempotent`.
- **N-R15** `test_shadow_report_hashes_are_identical_cross_process`.
- **N-R16** `test_shadow_report_timezone_equivalent_instants_hash_identically`.
  Reports additionally require
  `test_report_bytes_reproduce_exactly_from_manifest`.

Every future test must make wall-clock access fail: NAV, metrics, and report
timestamps derive only from named frozen calendar/session/checkpoint inputs or
are absent.

### 6.3 Operational definition of “full frozen replay” Phase-2 DoD

The checklist requires a frozen replay with no instrumentation-caused live
decision difference and reproduction of the same hashes and report from the
manifest
([Phase-2 DoD lines 655–660](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md#L655)).
Operationally:

#### Invocation

Start a clean process with only:

```text
(
  immutable_input_root,
  output_root,
  output_mode = CREATE_EMPTY | CREATE_OR_VERIFY,
  protocol_id,
  manifest_revision or manifest_canonical_sha256,
  protocol_run_commitment_id,
  protocol_run_commitment_canonical_sha256,
  anchor_proof_locator,
  anchor_proof_raw_sha256,
  trust_root_id
)
```

`immutable_input_root` is read-only and may never alias `output_root`.
`CREATE_EMPTY` requires a new empty output root. `CREATE_OR_VERIFY` is allowed
only for exact retry against an already populated output created from the same
commitment/replay ID; it may verify existing identical bytes but create or
change nothing. Do not accept `latest_*`, caller-supplied model objects, current
database/live state, provider/network calls, wall clock, or mutable environment
defaults. Tests must make write access to the input root and
network/provider/clock/latest-alias access raise immediately.

The manifest freezes anchor policy, deterministic run/checkpoint-ID derivation,
and the authorized trust-root ID/hash. `anchor_proof_locator` resolves a
read-only exported WORM/signature proof; its explicit raw hash and the
manifest-authorized public verification material authenticate the exact
commitment ID/hash. The manifest does not pretend to know a future commitment
hash. Missing, divergent, merely directory-discovered, or implicitly configured
anchor/commitment material fails before traversal. Expected families, artifact
IDs/counts, and ledger event-count/head-or-tail commitments are read only from
the authenticated commitment; callers cannot omit them through parameters.

#### Required stored inputs

- manifest raw bytes, methodology bytes, and revision reference;
- authenticated protocol-run commitment, its exact stage-2 anchor proof/trust
  root, and every committed artifact/family count. The commitment object and
  every record/event that authenticates its hash are excluded from its graph
  roots and verified separately;
- ApprovalRecord, approval ledger refs/events/blobs, exact expected closure
  state, and manifest-bound or independently signed ledger commitments. For an
  open checkpoint, closure absence must be proven against the anchored
  commitment; for fixed-terminal replay, ClosureRecord/reference/terminal event
  are mandatory;
- frozen calendar;
- raw and normalized candidate sets;
- generic snapshots and every candidate/outcome/source/corporate-action
  vintage;
- portfolio policy/source/state;
- independently persisted paired input and observations;
- fixed-notional inputs/results where applicable;
- after RS-P2-016–018: policy-portfolio events/states, daily NAV marks/series,
  and metric input/denominator/result artifacts;
- frozen canonical live source input plus authenticated pre/post
  canonical-live-payload receipt predecessors;
- every evaluation/capture/checkpoint/report timestamp source as an exact named
  frozen cutoff, event, or calendar input. Produced artifacts either derive
  their timestamp from one of these or omit it; `now()` is forbidden;
- canonicalizer, schemas, replay-engine version, exact content-hash-verified
  source tree or executable/container identity, dependency lock, and runtime
  version;
- after RS-P2-023: canonical report-data/predecessors and frozen
  renderer/font/locale/toolchain identity. Stored rendered bytes/ref are
  comparison oracles, not renderer input.

#### Root-hash domains

The commitment and receipt use three non-interchangeable roots:

1. `input_evidence_root_sha256` commits to exact raw input evidence;
2. `canonical_input_semantic_root_sha256` commits to normalized typed input
   meaning and lineage;
3. `expected_output_graph_sha256` in the authenticated commitment is compared
   only with `actual_output_graph_sha256` in the newly generated
   `ReplayReceipt`.

Every root has a frozen domain/version tag. Its preimage consists of unique
domain-separated node records and edge records, encoded with length-prefixing
or canonical JSON, duplicate-key/duplicate-node rejection, namespace-relative
paths only, and deterministic bytewise sort keys. Raw-evidence nodes include
kind, logical ID, canonical hash, raw hash, byte length, and namespace-relative
path. Canonical-semantic nodes omit encoding-only raw/path identity but retain
kind, ID, canonical hash, and named edges. Output nodes use the same exact
record grammar under a distinct output-domain tag.

The commitment object and its stage-2 authenticating anchor are excluded from
all roots whose values they contain and are compared separately. Thus there is
no self-hash or anchor cycle. Two timezone-equivalent raw encodings may have
different `input_evidence_root_sha256` values and separately authenticated
commitments; their canonical semantic root and expected/actual output graph
roots must match.

#### Produced outputs

- fully validated authorization and artifact graph;
- paired decisions and observations;
- generic outcomes and reconstructed OutcomeLedger;
- fixed-notional and policy-portfolio records;
- daily NAV and common metrics;
- deterministic `ReplayReceipt` containing sorted nodes, edges, raw/canonical
  hashes, byte lengths, event counts/tails, and
  `actual_output_graph_sha256` over produced graph nodes excluding the receipt
  itself, persisted as:

  ```text
  protocols/{protocol_id}/{manifest_sha256}/
    replay_receipts/objects/{canonical_sha256}/{raw_sha256}.json
    replay_receipts/refs/{replay_id}.ref.json
  ```

  `replay_id` is deterministically derived from protocol ID, manifest canonical
  hash, checkpoint/commitment canonical hash, and replay-engine/root-domain
  version—never output path, process identity, or time. The receipt also carries
  dual hashes, length, path, engine/environment identities, input commitment
  ID/hash, and output-root graph hash. Its timestamp is absent or derived from
  the frozen checkpoint, never wall clock;
- independently generated shadow-disabled and shadow-enabled canonical live
  payload receipts from the same frozen source input;
- after RS-P2-023, all due daily/weekly/monthly/fixed-terminal report artifacts.

The authenticated input commitment is the completeness index. Its
`expected_output_graph_sha256`/expected derived-output section, prior live
receipt, and stored rendered report are comparison oracles and must be read only
after generation. Replay must never copy an expected output into the produced
tree.

#### Hash and byte comparisons

Compare against the authenticated protocol-run commitment:

1. the commitment ID/canonical hash and its independent anchor;
2. reconstructed `input_evidence_root_sha256` against that commitment;
3. reconstructed `canonical_input_semantic_root_sha256` against that
   commitment;
4. every input raw SHA-256/length and parsed canonical SHA-256/path;
5. every derived artifact ID and sorted predecessor list;
6. every event hash, ledger event count/tail, and checkpoint commitment;
7. independently produced `actual_output_graph_sha256` only against committed
   `expected_output_graph_sha256`;
8. canonical live payload bytes/hash before versus after replay;
9. once RS-P2-023 exists, canonical report-data hash and exact
   rendered-report raw-byte hash.

Exact retry must create no new refs/events and change no input or output byte.

#### Repetition matrix

Run:

1. twice in one process from the same read-only input root into two separate,
   non-aliasing empty output roots;
2. a third time in `CREATE_OR_VERIFY` mode against the first populated output
   root; assert identical paths/bytes/root/receipt ref and zero new or changed
   refs, events, files, or tails;
3. in fresh child processes with different `PYTHONHASHSEED`;
4. with equivalent instants encoded in UTC and Asia/Jakarta and different
   process timezone environments; if the platform cannot vary host timezone
   reliably, make every implicit local-time lookup fail and test explicit
   equivalent UTC/WIB instants.

For identical raw input, every input-evidence, semantic, and output root must
match. For timezone-equivalent but byte-distinct inputs, each
`input_evidence_root_sha256` must match its own authenticated commitment and may
differ. Canonical semantic input/derived-output artifacts and their semantic
IDs/predecessor order, `canonical_input_semantic_root_sha256`, ledger semantic
hashes, and `actual_output_graph_sha256` must match across variants.
Raw-evidence-bound commitment, anchor, and `ReplayReceipt` IDs/canonical bytes
may differ; each must verify against its own anchor, and both receipts must name
the same canonical output root. Once RS-P2-023 exists, report bytes must also
match. Mutating or deleting any required byte, reference, or checkpoint must
fail before descendant output is trusted.

#### Live-control proof

Hash the explicit versioned canonical live payload with instrumentation disabled
and with shadow replay enabled. Decision, risk, rank, sizing, execution, and
order fields must be identical. Only isolated shadow artifacts may differ.
Failure invalidates the shadow replay; it does not authorize live payload edits.

#### Completion dependency

- **E-G1** `test_full_frozen_replay_from_manifest_reproduces_artifact_graph`,
  `test_full_frozen_replay_is_cross_process_timezone_and_persist_idempotent`,
  `test_full_frozen_replay_separates_raw_semantic_and_output_root_domains`,
  `test_full_frozen_replay_authenticates_stage2_commitment_anchor`,
  `test_full_frozen_replay_preserves_canonical_live_payload_hash`, and
  `test_full_frozen_replay_requires_no_network_clock_or_latest_alias`.
- **E-G2** `test_full_frozen_replay_reproduces_report_bytes_from_manifest`.

E-G1 and all family cells after RS-P2-018 can produce an interim,
graph-complete but report-incomplete replay receipt. RS-P2-022 remains
`PARTIAL` while E-G2 is `G`; the full Phase-2 DoD closes only after RS-P2-023
and E-G2. Checklist line 659 explicitly requires reproduction of the same
**report**, while the report task remains open
([checklist lines 630–633](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md#L630)).

## 7. Sequencing proposal and estimated implementation size

Use an interleaved sequence, while retaining the task IDs as closure gates:

| Pass | Order and rationale | Estimate | Likely files |
|---|---|---|---|
| 1. RS-P2-019 kernel + current gaps | First stabilize immutable loaders/references for candidate raw/set v2, paired input, observation, snapshot/source, outcome/ledger commitments, calendar, canonical-live source/payload/receipts, run commitment, and the missing portfolio-policy ref. Do not relocate or rewrite v1 candidate artifacts. | **L** | New proposed `core/shadow_protocol/artifact_store.py`, `evidence_store.py`, `outcome_store.py`, `live_payload_store.py`, `run_commitment.py`; targeted changes to `evidence.py`, `paired_view.py`, `governance.py`, `outcome_engine.py`, `calendar.py`, `portfolio.py`, `contracts.py`, `__init__.py`; focused shadow tests. |
| 1a. Local 021/022 tests with every 019 slice | Each new store lands with its own raw/canonical/path/duplicate/length/collision tests plus exact retry and stored replay. This prevents an untested persistence interval; tests count toward 021/022 but do not close them globally. Repeat this **M**-sized pass per family slice. | **M** | Existing `tests/test_shadow_protocol_governance.py`, `test_shadow_protocol_p2.py`, `test_shadow_protocol_p2_014.py`, plus proposed focused store tests. |
| 2. RS-P2-020 registry and global rules | Build only after loader APIs and trusted protocol-run commitments are stable. Integrate staged/final findings into `ReconciliationReport`; preserve live-control authority. | **L** | New proposed `core/artifact_validation_registry.py`, `core/shadow_protocol/validation_rules.py`; targeted `core/artifact_validator.py`; `tests/test_artifact_validator.py`; proposed `tests/test_shadow_protocol_validation.py`. |
| 3. RS-P2-021 matrix closure | Parameterize all remaining governance/candidate/portfolio/fixed/current-family vectors and global-validator tamper traversal, including forced collision and externally anchored tail deletion. | **L** | Primarily test files above plus proposed shared tamper fixtures; production changes only where a missing enforcement primitive is proven. |
| 4. RS-P2-022 family replay closure | Close every remaining `R-G`, `I-R`, and `LC-R` cell: governance/calendar exact retry, clean stored replay, candidate/paired/snapshot/portfolio/outcome breadth, commitment/live-receipt coverage, and all cross-process/timezone gaps. Verify the 016–018 `N-R1`–`N-R12` birth suites before E-G1. | **L** | Existing shadow test files, new outcome/store/commitment tests, and frozen fixtures/hash indexes. |
| 5. RS-P2-022 ReplayReceipt/E-G1, then report E-G2 | Close `RR-R1`–`RR-R4` and land a graph-complete but visibly report-incomplete clean-process reproducer after 016–018. After 023, close `N-R13`–`N-R16`, deterministic report generation/oracle comparison, and E-G2. | **L** | Proposed `core/shadow_protocol/replay.py`, `core/shadow_protocol/__init__.py`, `tests/test_shadow_protocol_replay.py`, frozen fixture/hash-index tree; later 023 reporting module/tests. |

Why not strict `019 → 020 → 021 → 022` with all tests deferred:

- storage without immediate negative and idempotency tests creates a trust gap;
- RS-P2-020 needs stable strict loader APIs, so it should follow the 019 kernel;
- RS-P2-021 exposes enforcement defects that may require small store fixes before
  the replay graph is frozen;
- cross-process/full replay should be last because it composes all family
  identities and future 016–018 outputs;
- report-byte replay remains a deliberate dependency on RS-P2-023, not a
  fabricated early completion.

## 8. Implementation-prompt checklist

Every implementation prompt derived from this specification must state:

- exact family, contract version, path/ref scheme, and legacy-read policy;
- typed artifact ID, raw/canonical hashes, byte length, path, and predecessors;
- whether it is a single immutable object or a versioned append-only state;
- expected ledger commitment and tail-deletion detection window where relevant;
- exact tamper cells and replay cells closed by named tests;
- authority-literal and canonical-live-payload invariants;
- `NOT_APPLICABLE` behavior for families not declared by the manifest;
- touched files and a prohibition on unrelated live/A1/collection/threshold
  changes;
- verification that no `latest_*` alias is required for reconstruction;
- explicit residual gaps that remain after the pass.

The final implementation handoff must report family-by-family matrix deltas. A
task checkbox must remain partial if its coarse criterion passes while a
required fine-grained stored-byte, retry, cross-process, timezone, or full
replay proof is still absent.
