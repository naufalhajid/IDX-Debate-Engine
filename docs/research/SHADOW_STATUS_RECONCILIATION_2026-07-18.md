# Shadow-Protocol Status Reconciliation Ledger

**Audit date:** 2026-07-18 (Asia/Jakarta)

**Mode:** documentation-only, evidence reconciliation

**Audited HEAD:** `98d20c5277741069792812e44e160920dbec2027`

**Live authority:** unchanged (`false`)

**Collection / A1:** none granted or started

This ledger reconciles checklist status against the repository after
RS-P2-014 and RS-P2-015. It is not an implementation artifact and does not
authorize a component manifest, A1, collection, unblinding, or a live-path
change.

## 1. RS-P2-025 preservation verdict

**Verdict: DONE through the preservation branch.**

The task permits either preserving the existing forecasting
`shadow-evaluation-v1` contract or explicitly migrating it with
backward-compatibility tests. The repository chose preservation:

- The original contract literal and frozen models remain in
  [`core/forecasting/shadow_evaluation.py`](../../core/forecasting/shadow_evaluation.py#L32),
  and that file has no diff from its introducing commit
  `ca105098b78ba8acfa08878c5d9fb0e9e642a2c2`.
- Its original operational regression coverage remains, including
  [`test_shadow_evaluator_uses_persisted_point_in_time_provenance_only`](../../tests/test_predictive_repair_phase5.py#L176),
  [`test_shadow_outcome_merge_is_idempotent_and_never_downgrades_mature`](../../tests/test_predictive_repair_phase5.py#L420),
  [`test_backfill_writes_isolated_evaluation_only_artifacts`](../../tests/test_predictive_repair_phase5.py#L441),
  and
  [`test_shadow_backfill_cli_is_explicit_and_offline`](../../tests/test_predictive_repair_phase5.py#L499).
- The manifest-v2 loader explicitly rejects both the old audit-only manifest
  and `shadow-evaluation-v1`, tested by
  [`test_manifest_v1_is_explicitly_audit_only_and_rejected`](../../tests/test_shadow_protocol_governance.py#L1088)
  and
  [`test_forecasting_shadow_evaluation_v1_is_not_reinterpreted_as_manifest_v2`](../../tests/test_shadow_protocol_governance.py#L1099).
  Those tests were introduced by
  `efaf3d9429a479316427d1ee87d1f36b1711b76b`.
- The RS-P2-015 fixed-notional loader also rejects the forecasting artifact
  instead of treating it as a fixed-notional pair input, tested by
  [`test_forecasting_shadow_v1_is_not_reinterpreted_as_fixed_notional`](../../tests/test_shadow_protocol_p2_015.py#L1878),
  introduced by `6e459c8a15b439f35e46d4791db1ddbbcb2d92af`.

Residual hardening is useful but is not required to satisfy the task's
preservation branch:

1. commit a golden artifact produced by the original
   `shadow-evaluation-v1` reader/writer before any future migration and
   round-trip it byte-for-byte through that original reader; and
2. parameterize `shadow-evaluation-v1` rejection across every future artifact
   loader, rather than relying on the current manifest and fixed-notional
   boundary tests.

These are follow-up hardening items, not evidence of a silent migration.

## 2. RS-P2-019 / RS-P2-021 / RS-P2-022 coverage matrix

### 2.1 Legend and global verdict

- **COVERED:** the named capability has implementation plus a direct test for
  this artifact family.
- **PARTIAL:** useful implementation or tests exist, but at least one required
  stored-artifact, negative path, retry, or deterministic-hash proof is absent.
- **MISSING:** the artifact family has no implementation for the named
  capability.

| Artifact family | RS-P2-019 immutable/versioned path | RS-P2-021 tamper tests | RS-P2-022 replay/idempotency/canonical hash |
|---|---|---|---|
| Manifest | COVERED | COVERED | PARTIAL |
| Approval / ledger | COVERED | COVERED | PARTIAL |
| Closure | COVERED | PARTIAL | COVERED |
| Portfolio-state | COVERED | COVERED | COVERED |
| Paired-view | PARTIAL | PARTIAL | PARTIAL |
| Fixed-notional | COVERED | COVERED | COVERED |
| Observation | COVERED | PARTIAL | COVERED |
| Outcome | MISSING | PARTIAL | PARTIAL |
| Calendar | COVERED | PARTIAL | PARTIAL |

Global checklist verdicts:

- **RS-P2-019: PARTIAL, checkbox remains open.**
- **RS-P2-021: PARTIAL, checkbox remains open.**
- **RS-P2-022: PARTIAL, checkbox remains open.**

### 2.2 Per-family evidence and residual gaps

#### Manifest

Path:

```text
protocols/{protocol_id}/{manifest_canonical_sha256}/
  manifests/{manifest_raw_sha256}/manifest.json
protocols/{protocol_id}/
  manifest_revisions/{manifest_revision:08d}.json
```

Implementation evidence:

- [`ProtocolGovernanceStore.persist_manifest`](../../core/shadow_protocol/governance.py#L615)
  writes canonical/raw content-addressed manifest evidence with exclusive
  creation and a revision reference.
- [`test_store_is_content_addressed_and_ledger_is_ordered`](../../tests/test_shadow_protocol_governance.py#L906)
  checks the path identity.
- [`test_protocol_manifest_revision_is_globally_immutable`](../../tests/test_shadow_protocol_governance.py#L1145)
  checks the immutable revision claim.
- Canonical hash, raw-only reformatting, byte length, duplicate-key, and
  methodology tampering are covered by tests at
  [`:484`](../../tests/test_shadow_protocol_governance.py#L484),
  [`:518`](../../tests/test_shadow_protocol_governance.py#L518),
  [`:605`](../../tests/test_shadow_protocol_governance.py#L605), and
  [`:864`](../../tests/test_shadow_protocol_governance.py#L864).

Residual replay gap: there is no dedicated exact-retry test for persisting the
same manifest twice and no manifest-specific cross-process canonical-byte/hash
test.

#### Approval / ledger

Paths:

```text
protocols/{protocol_id}/{manifest_canonical_sha256}/
  approvals/{approval_canonical_sha256}/{approval_raw_sha256}.json
  approval_ledgers/ledger_reference.json
  approval_ledgers/{ledger_id}/
    event_blobs/{event_canonical_sha256}/{event_raw_sha256}.json
    events/{sequence:010d}.ref.json
```

Implementation evidence:

- [`append_approval`](../../core/shadow_protocol/governance.py#L707) persists
  the ApprovalRecord and append-only ledger event.
- Ordered content-addressing, terminal event tampering, missing event blobs,
  reference/hash mismatch, and competing sequence claims are covered by
  [`test_store_is_content_addressed_and_ledger_is_ordered`](../../tests/test_shadow_protocol_governance.py#L906),
  [`test_terminal_ledger_event_tampering_is_detected`](../../tests/test_shadow_protocol_governance.py#L969),
  [`test_missing_content_addressed_event_blob_is_rejected`](../../tests/test_shadow_protocol_governance.py#L1343),
  [`test_event_reference_hash_mismatch_is_rejected`](../../tests/test_shadow_protocol_governance.py#L1363), and
  [`test_losing_sequence_claim_cannot_corrupt_winning_event`](../../tests/test_shadow_protocol_governance.py#L1385).

Residual replay gap: the exact initial `append_approval()` retry is supported
by code but has no dedicated test, and ApprovalRecord / ApprovalLedgerEvent /
reconstructed ApprovalLedger have no cross-process hash test.

#### Closure

Paths:

```text
protocols/{protocol_id}/{manifest_canonical_sha256}/
  closures/{closure_canonical_sha256}/{closure_raw_sha256}.json
  approval_ledgers/closure_reference.json
```

Implementation evidence:

- [`append_closure`](../../core/shadow_protocol/governance.py#L848) binds the
  content-addressed record, closure reference, and terminal ledger event.
- Exact retry and conflicting closure rejection are covered in
  [`test_store_is_content_addressed_and_ledger_is_ordered`](../../tests/test_shadow_protocol_governance.py#L906).
- Tail deletion, interrupted closure recovery, and fixed-terminal chronology
  are covered by tests at
  [`:1430`](../../tests/test_shadow_protocol_governance.py#L1430),
  [`:1461`](../../tests/test_shadow_protocol_governance.py#L1461), and
  [`:1549`](../../tests/test_shadow_protocol_governance.py#L1549).

Residual tamper gap: the ledger/reference interruption paths are exercised, but
no test mutates the persisted content-addressed ClosureRecord JSON or its
closure reference and then proves authorization reload fails closed.

#### Portfolio-state

Paths:

```text
protocols/{protocol_id}/{manifest_sha256}/
  portfolio_states/{state_canonical_sha256}/{state_raw_sha256}.json
  portfolio_state_refs/{portfolio_state_id}.json
```

Associated policy and source evidence use the same dual-hash structure under
`portfolio_policies` and `portfolio_state_sources`.

Implementation evidence:

- [`PortfolioArtifactStore`](../../core/shadow_protocol/portfolio.py#L1888)
  provides exclusive-create storage and strict reload.
- Retry/collision, duplicate keys/noncanonical formatting, state/source byte
  tampering, semantic reference tampering, deterministic replay, lineage
  reconstruction, and cross-process state hashing are covered by
  [`tests/test_shadow_protocol_p2_014.py:1002-1751`](../../tests/test_shadow_protocol_p2_014.py#L1002).

No residual gap was found for the three capabilities in this matrix.

#### Paired-view

The raw and normalized candidate sets are persisted through
[`CandidateSetStore`](../../core/shadow_protocol/evidence.py#L1476), while
[`PairedCandidateDecisionInput`](../../core/shadow_protocol/paired_view.py#L50)
is an ephemeral producer input and the produced pair is embedded in the
stored `ShadowObservation`.

Evidence includes:

- raw-first candidate-store tamper rejection in
  [`test_candidate_store_requires_raw_first_and_rejects_tamper`](../../tests/test_shadow_protocol_p2.py#L1392);
- same immutable evaluator input and mutation rejection in
  [`test_paired_evaluators_receive_same_immutable_input`](../../tests/test_shadow_protocol_p2_014.py#L1456)
  and
  [`test_evaluator_mutation_fails_before_second_side`](../../tests/test_shadow_protocol_p2_014.py#L1495);
- same-input replay and lineage reconstruction in tests at
  [`:1568`](../../tests/test_shadow_protocol_p2_014.py#L1568) and
  [`:1634`](../../tests/test_shadow_protocol_p2_014.py#L1634).

Residual gaps:

1. no dedicated paired-input artifact path, loader, raw-file hash, or byte
   length;
2. no direct post-write tamper test for the paired input; and
3. no cross-process hash test for `PairedCandidateDecisionInput` or the final
   paired observation.

#### Fixed-notional

Paths:

```text
protocols/{protocol_id}/{manifest_sha256}/
  fixed_notional/{artifact_kind}/{canonical_sha256}/{raw_sha256}.json
  fixed_notional/refs/{artifact_kind}/{artifact_id}.json
```

Implementation evidence:

- [`FixedNotionalArtifactStore`](../../core/shadow_protocol/fixed_notional_store.py#L517)
  provides exclusive-create dual-hash storage and full graph reload.
- Replay/reconstruction, predecessor tamper, raw and canonical tamper,
  duplicate keys, byte-length/path substitution, semantic drift, and
  cross-process hashes are covered by
  [`tests/test_shadow_protocol_p2_015.py:1419-2113`](../../tests/test_shadow_protocol_p2_015.py#L1419).

No residual gap was found for the three capabilities in this matrix.

#### Observation

Path:

```text
protocols/{protocol_id}/{manifest_canonical_sha256}/
  observations/{observation_sha256}/{observation_sha256}.json
```

Implementation evidence:

- [`persist_observation`](../../core/shadow_protocol/governance.py#L981)
  stores canonical bytes and records their identity in the approval ledger.
- The path is asserted by
  [`test_store_is_content_addressed_and_ledger_is_ordered`](../../tests/test_shadow_protocol_governance.py#L906).
- Exact retry / ID collision and deterministic observation identity are
  covered at
  [`tests/test_shadow_protocol_governance.py:1181`](../../tests/test_shadow_protocol_governance.py#L1181),
  [`tests/test_shadow_protocol_p2_014.py:1568`](../../tests/test_shadow_protocol_p2_014.py#L1568),
  and
  [`tests/test_shadow_protocol_p2.py:2885`](../../tests/test_shadow_protocol_p2.py#L2885).

Residual tamper gap: no test mutates persisted observation JSON and proves
maturation/load rejects it. Maturation validates the caller-supplied
observation against the ledger; it does not reload the observation blob through
a dedicated observation loader.

#### Outcome

There is no immutable filesystem store for generic `ShadowOutcome` or
[`OutcomeLedger`](../../core/shadow_protocol/outcome_engine.py#L1108).

In-process/in-memory implementation evidence:

- idempotent pending-to-mature backfill at
  [`tests/test_shadow_protocol_p2.py:2520`](../../tests/test_shadow_protocol_p2.py#L2520);
- terminal overwrite, source-prefix, and forged-math rejection at
  [`:2679`](../../tests/test_shadow_protocol_p2.py#L2679);
- deterministic side ordering and ledger hash at
  [`:2790`](../../tests/test_shadow_protocol_p2.py#L2790);
- complete logical lineage tamper checks at
  [`:2827`](../../tests/test_shadow_protocol_p2.py#L2827).

Residual gaps:

1. RS-P2-019 is missing for this family;
2. persisted-blob tamper testing cannot exist until the store exists; and
3. the current replay/hash evidence is in-memory rather than a replay from
   immutable stored bytes, so RS-P2-022 remains partial for this family.

#### Calendar

Path:

```text
trading_calendars/{calendar_sha256}/calendar.json
```

Implementation evidence:

- [`TradingCalendar`](../../core/shadow_protocol/calendar.py#L22) validates its
  deterministic session hash.
- [`persist_trading_calendar`](../../core/shadow_protocol/governance.py#L661)
  uses exclusive create, and
  [`load_trading_calendar`](../../core/shadow_protocol/governance.py#L1406)
  checks the stored path/hash.
- Trusted-session derivation and manifest/calendar mismatch are covered at
  [`tests/test_shadow_protocol_governance.py:643`](../../tests/test_shadow_protocol_governance.py#L643)
  and
  [`:846`](../../tests/test_shadow_protocol_governance.py#L846).

Residual gaps:

1. no direct mutation test for persisted `calendar.json`;
2. no exact same-calendar persist retry test;
3. no cross-process calendar-hash test; and
4. unlike manifest/portfolio/fixed-notional, calendar storage has no separate
   raw-file hash and byte-length reference.

### 2.3 Cross-cutting gap specification for the later RS-P2-019--022 pass

1. Add immutable generic storage for `ShadowOutcome`, `OutcomeLedger`, outcome
   bars, corporate-action vintages, and outcome-source records.
2. Decide whether `PairedCandidateDecisionInput` becomes an independently
   stored artifact or whether the observation family must carry its exact raw
   identity and loader semantics.
3. Add a dedicated observation loader and prove post-write observation
   tampering fails at the maturation boundary.
4. Add direct persisted-calendar tamper, retry, and cross-process hash tests;
   decide explicitly whether raw hash and byte length are required.
5. Add stored ClosureRecord and closure-reference tamper tests.
6. Add manifest and initial ApprovalRecord/ledger exact-retry tests plus
   component-specific cross-process canonical hash tests.
7. Add a generic immutable store for `FrozenSnapshot` and source-vintage
   evidence; fixed-notional-specific storage does not cover the generic
   outcome family.
8. Add an end-to-end reproducer that starts only from stored bytes and
   reconstructs manifest -> approval -> calendar -> snapshot/source -> paired
   input -> observation -> outcome.
9. Complete RS-P2-020's global artifact validator; distributed model/store
   validation is not equivalent to a family-complete validator.

## 3. RS-P1-R01--R05 recurring-evidence log

### 3.1 Entry format and status rules

Every future pass that touches recommendation state, shadow authority,
ranking/sizing, user-facing output, or calibration must append one entry:

```text
Date / pass:
Pass commit(s):
Recommendation-context baseline commit:
Relevant surfaces touched:
Verification gate:

R01 status / exact tests / residual gap
R02 status / exact tests / residual gap
R03 status / exact tests / residual gap
R04 status / exact tests / residual gap
R05 status / exact tests / residual gap
```

Allowed evidence statuses:

- `RECONFIRMED`: the pass ran a test that directly exercises the obligation.
- `RECONFIRMED_WITH_COVERAGE_NOTE`: direct collective evidence exists, with a
  named residual scope limitation.
- `PARTIAL`: relevant evidence exists but does not close the obligation.
- `NOT_EVIDENCED`: no direct evidence exists.
- `N/A_PRE_CONTRACT`: the pass predates the obligation/contract.
- `PARTIAL_PRE_CONTRACT`: adjacent safety evidence exists, but the later
  obligation could not yet be tested.

The test symbol is the durable evidence identity. A `file:line` pointer is
included for navigation but may move. The pass commit and test-origin commit
must be kept distinct. R01--R05 are recurring obligations: their master
checkboxes never become permanently complete.

### 3.2 Shared Phase-1 invariant tests

The following tests originate in recommendation-context commit
`a385ed1c4081ff79cbffc775c05c7d049c56f145`.

| Obligation | Direct evidence |
|---|---|
| R01 | [`test_projection_does_not_mutate_canonical_decision`](../../tests/test_recommendation_context.py#L325); [`test_non_promotable_setup_states_fail_closed`](../../tests/test_recommendation_context.py#L169); [`test_terminal_shadow_only_setup_stays_no_trade_with_canonical_risk_status`](../../tests/test_orchestrator_quality_gates.py#L910); [`test_execution_funnel_does_not_count_shadow_momentum_states_as_valid`](../../tests/test_orchestrator_quality_gates.py#L1016); [`test_strict_ranking_backfills_with_risk_deployable_candidate`](../../tests/test_orchestrator_quality_gates.py#L1127); [`test_recommendation_diagnostics_keep_reject_out_of_sizing`](../../tests/test_cli_renderer_presentation.py#L304). |
| R02 | [`test_rr_near_miss_band_is_presentation_only`](../../tests/test_recommendation_context.py#L68); [`test_non_promotable_setup_states_fail_closed`](../../tests/test_recommendation_context.py#L169); [`test_bare_waitlist_is_not_promoted_to_wait_trigger`](../../tests/test_recommendation_context.py#L211); [`test_schema_rejects_actionable_hypothetical_setup`](../../tests/test_recommendation_context.py#L275); [`test_recommendation_diagnostics_keep_reject_out_of_sizing`](../../tests/test_cli_renderer_presentation.py#L304). |
| R03 | [`test_artifact_validator_detects_context_drift_and_decision_mismatch`](../../tests/test_recommendation_context.py#L292). |
| R04 | [`test_pre_cio_result_has_api_report_and_persistence_parity`](../../tests/test_recommendation_context.py#L353); [`test_rr_report_shows_exact_gap_and_non_executable_geometry`](../../tests/test_recommendation_context.py#L372); [`test_recommendation_diagnostics_keep_reject_out_of_sizing`](../../tests/test_cli_renderer_presentation.py#L304). |
| R05 | **Gap:** schema defaults to `NOT_AVAILABLE`, but no direct regression test prevents pre-C1 `VALIDATED`; the schema currently permits that literal. |

R04's evidence is collective across API/persistence/Markdown/Rich surfaces.
There is not yet one end-to-end test that feeds one identical fixture through
all four renderers and compares normalized state/blocker output.

### 3.3 Backfill: FV/RAG semantic-status pass

- **Pass commit:** `3948508528f7c27bbae8a78c61c703304f8d029a`
- **Contract status:** `PRE_RECOMMENDATION_CONTEXT`; the Phase-1 contract first
  appears in child commit
  `a385ed1c4081ff79cbffc775c05c7d049c56f145`.
- **Historical artifact:** `output/diagnostic_20260717_fv_rag_final/`
  (`run_id=20260717_022910`), frozen in the baseline manifest by
  `full_batch_results_sha256=ad59a87a714f40e04b45dc1264810dc42d6e894776048dbf506e14d4ca843999`
  and
  `execution_funnel_sha256=9ddfc090f63c75aeb80ffab845f07bd297fecfd8c0550a69bf3699c89561fa5a`.
- **Verification record:** no pass-specific pytest count was found; do not
  infer one.

| Obligation | Status | Evidence / limitation |
|---|---|---|
| R01 | N/A_PRE_CONTRACT | Adjacent fail-closed/advisory evidence from `test_reconcile_artifacts_fails_closed_for_ambiguous_or_graph_activity` ([`tests/test_artifact_validator.py:276`](../../tests/test_artifact_validator.py#L276)) and `test_devils_advocate_stays_advisory_without_appending_vote` ([`tests/test_debate_chamber_reliability.py:2661`](../../tests/test_debate_chamber_reliability.py#L2661)), both in pass commit `3948508528f7c27bbae8a78c61c703304f8d029a`; no recommendation shadow field existed. |
| R02 | PARTIAL_PRE_CONTRACT | [`test_trade_envelope_rejects_hard_momentum_breakdown_below_ema20`](../../tests/test_debate_chamber_reliability.py#L2557) and the diagnostic's `rr_too_low` outcomes show no loosening, but `NEAR_MISS` / `WAIT_TRIGGER` / hypothetical contracts did not exist. |
| R03 | N/A_PRE_CONTRACT | No top-level/metadata recommendation-context pair existed. |
| R04 | PARTIAL_PRE_CONTRACT | [`test_normalize_result_preserves_explicit_preflight_fair_value_status`](../../tests/test_result_adapter.py#L77) and [`test_preflight_fair_value_status_is_visible_in_markdown_and_rich`](../../tests/test_report_formatter.py#L559) cover FV semantics, not later recommendation state/blocker parity. |
| R05 | N/A_PRE_CONTRACT | `calibration_status` was not part of the later recommendation contract. |

This entry is historical context, not a post-Phase-1 reconfirmation.

### 3.4 Backfill: RS-P2-014

- **Implementation commit:**
  `e0303a517dcd85c93f3edc4574b325cec97b4e82`
- **Design commit:** `2b408026898f111f153786df908b6c0c2fd1e7c3`
- **Phase-1 test origin:**
  `a385ed1c4081ff79cbffc775c05c7d049c56f145`
- **Recorded gate:** focused `159 passed`; full `1809 passed, 3 skipped`;
  Ruff and lock checks passed.

| Obligation | Status | Evidence / limitation |
|---|---|---|
| R01 | RECONFIRMED | Shared R01 suite plus [`test_paired_evaluators_receive_same_immutable_input`](../../tests/test_shadow_protocol_p2_014.py#L1456), [`test_evaluator_mutation_fails_before_second_side`](../../tests/test_shadow_protocol_p2_014.py#L1495), [`test_paired_authorization_is_checked_before_first_evaluator`](../../tests/test_shadow_protocol_p2_014.py#L1532), and [`test_authority_literals_remain_evaluation_only`](../../tests/test_shadow_protocol_p2_014.py#L1752). |
| R02 | RECONFIRMED | Shared R02 tests passed in the recorded full suite; no live recommendation logic was changed. |
| R03 | RECONFIRMED | The shared context-drift validation passed. |
| R04 | RECONFIRMED_WITH_COVERAGE_NOTE | Collective API/persistence/Markdown/Rich tests passed; the single-fixture end-to-end gap remains. |
| R05 | PARTIAL | No direct pre-C1 calibration-status guard test exists. |

### 3.5 Backfill: RS-P2-015

- **Implementation commit:**
  `6e459c8a15b439f35e46d4791db1ddbbcb2d92af`
- **Documentation commit:**
  `98d20c5277741069792812e44e160920dbec2027`
- **Phase-1 test origin:**
  `a385ed1c4081ff79cbffc775c05c7d049c56f145`
- **Recorded gate:** RS-P2-015 file `57 passed`; focused shadow
  `216 passed`; full `1866 passed, 3 skipped`; Ruff and lock checks passed.

| Obligation | Status | Evidence / limitation |
|---|---|---|
| R01 | RECONFIRMED | Shared R01 suite plus `test_entry_capacity_is_all_or_none_and_shared` ([`:1038`](../../tests/test_shadow_protocol_p2_015.py#L1038)), `test_high_price_exclusion_is_identical_and_zero_size_is_not_persisted` ([`:1072`](../../tests/test_shadow_protocol_p2_015.py#L1072)), `test_eligible_side_geometry_may_differ_but_exclusion_mismatch_fails` ([`:1093`](../../tests/test_shadow_protocol_p2_015.py#L1093)), `test_authorization_checked_before_pair_and_maturation` ([`:1383`](../../tests/test_shadow_protocol_p2_015.py#L1383)), `test_authorization_failure_blocks_maturation` ([`:1411`](../../tests/test_shadow_protocol_p2_015.py#L1411)), and `test_all_new_artifacts_are_evaluation_only` ([`:2029`](../../tests/test_shadow_protocol_p2_015.py#L2029)); all six originate in implementation commit `6e459c8a15b439f35e46d4791db1ddbbcb2d92af`. |
| R02 | RECONFIRMED | Shared R02 tests passed; fixed-notional exclusions remain evaluation-only and do not promote a setup. |
| R03 | RECONFIRMED | The shared context-drift validation passed. |
| R04 | RECONFIRMED_WITH_COVERAGE_NOTE | Collective API/persistence/Markdown/Rich tests passed; the single-fixture end-to-end gap remains. |
| R05 | PARTIAL | No direct pre-C1 calibration-status guard test exists. |

## 4. Phase-ordering exception record

**Recorded on:** 2026-07-18

Phase 2 states a Phase-0 dependency, while Phase-0 still has incomplete DoD
items: immutable tracked baseline evidence, real component manifests/identity
tuples, A1 bindings, and C8 historical reconciliation.

RS-P2-001 through RS-P2-015 nevertheless proceeded under explicit, scoped
owner approvals for **build-only, evaluation-only substrate work**. The
reason was to construct and verify technical measurement safeguards without
granting any component authority. Those approvals did not:

- instantiate a real C1--C8 component manifest;
- grant A1;
- start collection or paper trading;
- unblind outcomes;
- change a live threshold, decision, rank, sizing, or execution path; or
- mark Phase-0 DoD complete.

This is an explicit sequencing exception, not a retroactive claim that the
Phase-0 dependency was met. Before the first real observation, the exact
component manifest plus immutable ApprovalRecord/A1 binding remains mandatory.

## 5. Known Phase-0 / C8 inconsistency

The active master/redesign/protocol authority correctly treats C8
`momentum_play` as reachable. However:

- `RS-P0-C8-RECON` remains open for prevalence measurement and the exact frozen
  control choice;
- `RS-P0-014` remains partial because historical dormancy wording still needs
  a pointer/archive decision; and
- the previous Phase-0 DoD sentence combined corrected active reachability
  with unfinished historical reconciliation and therefore overstated
  completion.

The checklist now separates:

1. the corrected active-authority truth, which is complete; from
2. C8 prevalence, frozen-control definition, and historical-artifact
   reconciliation, which remain open.

No C8 reachability correction or source change was performed in this pass.

## 6. Audit-pass verification and protected boundary

| Gate | Result |
|---|---|
| Full pytest | PASS: `1866 passed, 3 skipped, 41 warnings in 132.74s` |
| Ruff check | PASS: `uv run ruff check .` -> `All checks passed!`; no files changed |
| Core shadow-protocol boundary | PASS: all 10 pre-pass hashes reproduced exactly after the documentation changes |

Protected boundary:

| File | SHA-256 before and after |
|---|---|
| `core/shadow_protocol/__init__.py` | `213e021453fe67f45363f1a57d70aac3af6602ff5e12cabb01c884b12e82376c` |
| `core/shadow_protocol/calendar.py` | `fe27a4e5c964c26f3093921193f29ec45f4f4c09f620b52ca94806ab302c7151` |
| `core/shadow_protocol/contracts.py` | `87b605fb9cc3cb3bee73d903110801699e06e63f4d41e9e8b94cdd48d0ee54b7` |
| `core/shadow_protocol/evidence.py` | `44ce5f34ecd6af3249b5b102df0bc81e5b49a26683e0479e3237ebebf7af18bb` |
| `core/shadow_protocol/fixed_notional.py` | `485039d73f187e3b3092d5fbb733f2e9b5ee61617a2fa9e4dd0bef9e65bb0146` |
| `core/shadow_protocol/fixed_notional_store.py` | `ff148c6a350025636342da113f8ff40cbc390049b9eb64c00610b564d50bfbcc` |
| `core/shadow_protocol/governance.py` | `b2cdb46e36f453ba10e28a76403ce1768947c7dcd9d5093c37f13ab4ad4be7c4` |
| `core/shadow_protocol/outcome_engine.py` | `b90d149df67d91f59408618e580c75f55d2de2257cf6e5f46f4265dffaaa27a8` |
| `core/shadow_protocol/paired_view.py` | `873e94fa8cb6ebaf50c127a32da6c86326c1a68989a1a4ec6f2d53d7d9fef684` |
| `core/shadow_protocol/portfolio.py` | `63f7150f04c362791841f618969beba81de3849feb778681264d028fc815ee10` |

No production code, test, threshold, decision logic, live path, baseline,
manifest, A1 record, collection artifact, or unblinded result was created or
changed.
