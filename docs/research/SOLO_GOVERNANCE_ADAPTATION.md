# Solo Governance Adaptation for Shadow Protocols

**Status:** PROPOSAL ONLY — NOT APPROVED, NOT IMPLEMENTED  
**Date:** 2026-07-18  
**Scope:** A1 shadow-collection governance and RS-P2-023 reporting cadence  
**Explicit non-scope:** no validator change, no collection, no unblinding, no
threshold change, no live authority, and no RS-P2-014 implementation or design
in this document

## Executive decision

The current independent-review rule cannot be satisfied honestly by a solo
owner. Using a second name or account for the same person would only manufacture
the appearance of independence. The proposed substitute is an explicit
`SOLO_SELF_REVIEW` mode with:

1. a frozen DRAFT manifest that never mutates into an approved manifest;
2. a separate immutable `ApprovalRecord` that binds the exact DRAFT manifest's
   canonical-model SHA-256 and raw-file SHA-256;
3. a minimum cooling-off period of **72 elapsed hours and two completed IDX
   trading sessions**, whichever finishes later;
4. a schema-enforced self-adversarial review stored inside the
   `ApprovalRecord`;
5. unchanged technical, statistical, tamper, replay, sample, and shadow-only
   safeguards.

This adapts an organizational ceremony. It does not make self-review
"independent," and the artifacts must label it honestly as
`SOLO_SELF_REVIEW`. A1 continues to authorize only blinded paper-observation
collection. It does not authorize early unblinding, tuning, promotion, ranking,
sizing, execution, or any live decision influence.

## 1. Evidence and problem diagnosis

### 1.1 Current solo blocker

`ShadowProtocolManifest` currently contains `owner` and a mandatory
`independent_reviewer` (`core/shadow_protocol/contracts.py:256-273`). Its
validator rejects an approved/closed manifest when their case-folded strings
match (`contracts.py:328-340`). A solo owner therefore cannot form an approved
manifest without either failing validation or inventing a false second
identity.

The code comparison also proves only that two strings differ. It cannot prove
that two independent humans exist. Replacing the owner's email with an alias
would satisfy the syntax while weakening the meaning, so that option is
explicitly forbidden.

### 1.2 The larger circularity defect

The current manifest also embeds:

- `lifecycle_status`;
- `approval_reference`; and
- `approved_at`.

Canonical serialization includes every model field, including `None`, before
hashing (`contracts.py:1286-1304`). Therefore:

```text
DRAFT manifest bytes/model              -> canonical hash H1
same manifest changed to APPROVED       -> canonical hash H2
approval that named H1 cannot approve H2
```

The current outcome engine then accepts the status inside that same manifest as
approval evidence (`core/shadow_protocol/outcome_engine.py:423-449`). It does
not consume a separate approval artifact. Merely deleting the
`owner != independent_reviewer` check would leave this circularity intact.

### 1.3 Required architectural boundary

The experiment definition and the decision to authorize collection are
different facts and must be different immutable artifacts:

```text
ShadowProtocolManifest v2
  status is always DRAFT
  experiment content only
  canonical SHA-256 = semantic/model identity
  raw-file SHA-256  = exact persisted-byte identity
                 │
                 └── referenced by both hashes
                     in a separate immutable
                     ApprovalRecord v1
                     scope = SHADOW_COLLECTION_ONLY
```

The manifest's hash does not change when A1 is granted. An approval is valid
only for the exact tuple:

```text
(protocol_id,
 component_id,
 manifest_revision,
 draft_manifest_canonical_sha256,
 draft_manifest_raw_file_sha256)
```

Canonical and raw hashes are deliberately both required:

- canonical SHA-256 detects a change in validated model meaning and binds
  downstream semantic lineage;
- raw-file SHA-256 detects any change to the exact approved file bytes,
  including formatting, line endings, reserialization, or replacement.

Neither hash is stored inside the manifest itself. The approval record
references them, which avoids self-reference.

## 2. Step 1A — Solo A1 governance model

### 2.1 Cooling-off policy

**Recommendation:** self-approval becomes eligible only after both:

- at least **72 elapsed hours** since the exact manifest revision was frozen;
  and
- at least **two completed IDX trading sessions** strictly after the IDX-local
  freeze date.

The later condition controls. For example, a Friday freeze cannot be
self-approved on Monday merely because 72 hours have elapsed; the second
post-freeze trading session must also close. Exchange holidays extend the wait
automatically.

Why 72 hours:

- it forces multiple sleep cycles between authorship and approval;
- it creates temporal separation from the emotional momentum of finishing a
  design;
- two market sessions provide a chance to notice whether the universe, source
  freshness, liquidity assumptions, or regime framing was accidentally tied to
  one day's conditions;
- A1 remains non-live and blinded, so this is proportional to the authority
  granted.

This is a conservative **project governance policy**, not a literature-derived
statistical constant. It cannot substitute for minimum samples, untouched
tests, DSR requirements, or deterministic reproduction.

There is no waiver to accelerate a challenger. A genuine safety incident may
halt the current control through its existing safety path, but it may not
fast-track A1.

### 2.2 Events that reset the cooling clock

Any of the following requires a new manifest revision, new canonical and raw
hashes, a new freeze timestamp, a new self-review, and a fresh cooling period:

- any manifest byte or validated-field change;
- threshold, feature, universe, candidate-selection, cost, liquidity, label,
  fill, corporate-action, clustering, terminal-date, or GO/NO-GO change;
- a control/challenger/source/config/code hash change;
- a source refresh or changed source vintage/expiry rule;
- a changed trial-registry binding;
- a changed rollback or safety-stop definition.

Reformatting alone may preserve the canonical hash but changes the raw-file
hash, so it also invalidates the approval. Material post-start changes continue
to require a new protocol ID and trial under `SHADOW_MODE_PROTOCOL.md:28`.

### 2.3 Honest identity semantics

The manifest predeclares one of two modes:

| Mode | Owner | Reviewer | Approver | Meaning |
|---|---|---|---|---|
| `INDEPENDENT_REVIEW` | required | required and different from owner | reviewer | Existing two-person model |
| `SOLO_SELF_REVIEW` | required | must be `null` | must equal owner | Honest self-review with compensating controls |

The value `SOLO_SELF_REVIEW` must appear in the manifest and approval record.
Reports must never relabel it as independent review.

### 2.4 Mandatory self-adversarial checklist

Every solo A1 approval must contain exactly one answered item for every prompt
below. Every item stores the exact prompt, a substantive response, at least one
evidence reference, and a disposition. A valid approval requires all
dispositions to be `PASS`; `FAIL` or `BLOCKED` prevents creation of an approved
record.

| Prompt ID | Mandatory prompt |
|---|---|
| `SKEPTICAL_SUMMARY` | What would a skeptical outside reviewer flag about this manifest's thresholds, universe definition, and cost assumptions? |
| `THRESHOLD_FREEZE` | Which thresholds or choices could have been selected after seeing favorable outcomes, and what proves they were frozen before collection? |
| `UNIVERSE_BIAS` | Could exclusions, missing delisted names, survivorship, sector concentration, or point-in-time membership make the challenger look better? |
| `COST_AND_LIQUIDITY` | Are commission, tax, bid-ask, slippage, lot, notional, capacity, and missing-liquidity assumptions conservative and frozen? |
| `SOURCE_LINEAGE` | Does every input have a source, as-of time, expiry rule, hash/version, and fail-closed missing-data behavior with no future information? |
| `LABEL_LEAKAGE` | Can activation, fill, gap, ambiguity, corporate-action, or maturity rules leak future information or censor adverse outcomes? |
| `PAIRED_PARITY` | Do control and challenger receive exactly the same opportunity set, snapshots, timestamps, costs, labels, and frozen control state? |
| `INDEPENDENCE_AND_SAMPLE` | Could duplicated or correlated rows inflate sample size, and do affected independent clusters—not raw rows—meet the frozen minimum and precision rule? |
| `MULTIPLICITY_AND_DSR` | Are every tried variant and horizon registered, and are DSR/PBO or `NOT_ESTIMABLE` rules protected from selective reporting? |
| `FAIL_CLOSED_METRICS` | Which metrics can be `NOT_ESTIMABLE`, and does each such case block GO rather than become zero or a favorable substitute? |
| `SAFETY_AND_ROLLBACK` | What exact leakage, parity, authority, drawdown, hard-gate, and corruption events stop the challenger, and is rollback reproducible? |
| `DISCONFIRMING_RESULT` | What evidence would force NO-GO, what result might tempt the owner to override the rule, and what precommitment prevents that override? |

This is not a tick-box exercise. The exact prompt text is schema-bound. A
response such as "checked" without evidence should fail human review even if it
passes a minimum string-length constraint.

### 2.5 A1 creation sequence

1. Finish the component manifest as DRAFT.
2. Set a new `manifest_revision` and `draft_frozen_at`.
3. Serialize once to the intended JSON file and persist it using exclusive
   creation in an immutable/versioned path.
4. Re-read the exact bytes in a fresh process.
5. Reject duplicate JSON keys; validate the strict manifest schema.
6. Compute canonical-model SHA-256 and raw-file SHA-256.
7. Record the freeze and wait until both the 72-hour and two-session conditions
   pass.
8. Re-read the persisted file, recompute both hashes, and complete all twelve
   self-adversarial prompts against that exact hash pair.
9. Run all automated contract/tamper checks.
10. Create an immutable `ApprovalRecord` whose scope is exactly
    `SHADOW_COLLECTION_ONLY`.
11. Re-verify the manifest/approval binding at the collection entry point.
12. Start collection no earlier than both the manifest's
    `collection_start_not_before` and the approval decision time.

The approval record itself should be persisted by exclusive creation and
content-addressed externally. Its own hash must be stored in its path/index or
a later ledger event, not inside itself.

### 2.6 Local-clock limitation

A local hash proves content, not an externally trusted wall-clock timestamp. A
solo operator with full machine control could deliberately backdate the local
clock. The proposed validator prevents accidental bypass and makes ordinary
self-deception auditable; it does not pretend to provide third-party
timestamping. If stronger time proof is later desired, an external timestamp or
signed remote commit may be added without weakening the current controls.

## 3. Exact proposed `contracts.py` change — not applied

Because approval metadata is removed from the hashed manifest, this is an
incompatible schema correction. The proposal deliberately advances the generic
manifest contract to `shadow-protocol-manifest-v2`. The reserved component
protocol IDs do not change. No final A1-valid component manifest exists, so no
approved collection artifact is being silently reinterpreted. The separate
forecasting `shadow-evaluation-v1` contract is outside this diff and remains
unchanged.

The following is the exact proposed diff for
`core/shadow_protocol/contracts.py`. It is included for approval review only
and has **not** been applied:

```diff
diff --git a/core/shadow_protocol/contracts.py b/core/shadow_protocol/contracts.py
--- a/core/shadow_protocol/contracts.py
+++ b/core/shadow_protocol/contracts.py
@@
-from datetime import date, datetime, timezone
+from datetime import date, datetime, timedelta, timezone
@@
-SHADOW_PROTOCOL_MANIFEST_VERSION = "shadow-protocol-manifest-v1"
+SHADOW_PROTOCOL_MANIFEST_VERSION = "shadow-protocol-manifest-v2"
+SHADOW_APPROVAL_RECORD_VERSION = "shadow-approval-record-v1"
+SHADOW_CANONICALIZATION_VERSION = "shadow-canonical-json-v1"
+SOLO_A1_COOLING_OFF = timedelta(days=3)
+SOLO_A1_MIN_COMPLETED_IDX_SESSIONS = 2
@@
 SecondaryHorizons: TypeAlias = tuple[Literal[3], Literal[5], Literal[10]]
+GovernanceMode: TypeAlias = Literal[
+    "INDEPENDENT_REVIEW",
+    "SOLO_SELF_REVIEW",
+]
+ReviewDisposition: TypeAlias = Literal["PASS", "FAIL", "BLOCKED"]
+SelfReviewPromptID: TypeAlias = Literal[
+    "SKEPTICAL_SUMMARY",
+    "THRESHOLD_FREEZE",
+    "UNIVERSE_BIAS",
+    "COST_AND_LIQUIDITY",
+    "SOURCE_LINEAGE",
+    "LABEL_LEAKAGE",
+    "PAIRED_PARITY",
+    "INDEPENDENCE_AND_SAMPLE",
+    "MULTIPLICITY_AND_DSR",
+    "FAIL_CLOSED_METRICS",
+    "SAFETY_AND_ROLLBACK",
+    "DISCONFIRMING_RESULT",
+]
+
+SELF_ADVERSARIAL_PROMPTS: dict[SelfReviewPromptID, str] = {
+    "SKEPTICAL_SUMMARY": (
+        "What would a skeptical outside reviewer flag about this manifest's "
+        "thresholds, universe definition, and cost assumptions?"
+    ),
+    "THRESHOLD_FREEZE": (
+        "Which thresholds or choices could have been selected after seeing "
+        "favorable outcomes, and what proves they were frozen before collection?"
+    ),
+    "UNIVERSE_BIAS": (
+        "Could exclusions, missing delisted names, survivorship, sector "
+        "concentration, or point-in-time membership make the challenger look better?"
+    ),
+    "COST_AND_LIQUIDITY": (
+        "Are commission, tax, bid-ask, slippage, lot, notional, capacity, and "
+        "missing-liquidity assumptions conservative and frozen?"
+    ),
+    "SOURCE_LINEAGE": (
+        "Does every input have a source, as-of time, expiry rule, hash/version, "
+        "and fail-closed missing-data behavior with no future information?"
+    ),
+    "LABEL_LEAKAGE": (
+        "Can activation, fill, gap, ambiguity, corporate-action, or maturity "
+        "rules leak future information or censor adverse outcomes?"
+    ),
+    "PAIRED_PARITY": (
+        "Do control and challenger receive exactly the same opportunity set, "
+        "snapshots, timestamps, costs, labels, and frozen control state?"
+    ),
+    "INDEPENDENCE_AND_SAMPLE": (
+        "Could duplicated or correlated rows inflate sample size, and do affected "
+        "independent clusters-not raw rows-meet the frozen minimum and precision rule?"
+    ),
+    "MULTIPLICITY_AND_DSR": (
+        "Are every tried variant and horizon registered, and are DSR/PBO or "
+        "NOT_ESTIMABLE rules protected from selective reporting?"
+    ),
+    "FAIL_CLOSED_METRICS": (
+        "Which metrics can be NOT_ESTIMABLE, and does each such case block GO "
+        "rather than become zero or a favorable substitute?"
+    ),
+    "SAFETY_AND_ROLLBACK": (
+        "What exact leakage, parity, authority, drawdown, hard-gate, and corruption "
+        "events stop the challenger, and is rollback reproducible?"
+    ),
+    "DISCONFIRMING_RESULT": (
+        "What evidence would force NO-GO, what result might tempt the owner to "
+        "override the rule, and what precommitment prevents that override?"
+    ),
+}
@@
+class SelfAdversarialReviewItem(_StrictFrozenModel):
+    """One mandatory, evidence-backed solo review answer."""
+
+    prompt_id: SelfReviewPromptID
+    prompt_text: NonEmptyString
+    response: NonEmptyString
+    evidence_refs: tuple[NonEmptyString, ...] = Field(min_length=1)
+    disposition: ReviewDisposition
+
+    @model_validator(mode="after")
+    def verify_prompt_text(self) -> SelfAdversarialReviewItem:
+        if self.prompt_text != SELF_ADVERSARIAL_PROMPTS[self.prompt_id]:
+            raise ValueError("self-review prompt text differs from the frozen prompt")
+        return self
+
+
 class ShadowProtocolManifest(_EvaluationOnlyArtifact):
@@
-    contract_version: Literal["shadow-protocol-manifest-v1"] = (
+    contract_version: Literal["shadow-protocol-manifest-v2"] = (
         SHADOW_PROTOCOL_MANIFEST_VERSION
     )
     protocol_id: NonEmptyString
     component_id: ComponentID
     manifest_revision: int = Field(ge=1)
-    lifecycle_status: Literal["DRAFT", "APPROVED_FOR_COLLECTION", "CLOSED"]
+    lifecycle_status: Literal["DRAFT"] = "DRAFT"
     created_at: datetime
+    draft_frozen_at: datetime
     collection_start_not_before: datetime
     fixed_terminal_date: date
     owner: NonEmptyString
-    independent_reviewer: NonEmptyString
+    governance_mode: GovernanceMode
+    independent_reviewer: NonEmptyString | None = None
     rollback_owner: NonEmptyString
-    approval_reference: NonEmptyString
-    approved_at: datetime | None = None
     baseline_manifest_id: NonEmptyString
@@
-    @field_validator("created_at", "collection_start_not_before", "approved_at")
+    @field_validator("created_at", "draft_frozen_at", "collection_start_not_before")
     @classmethod
-    def require_aware_datetimes(cls, value: datetime | None) -> datetime | None:
-        if value is not None and value.utcoffset() is None:
+    def require_aware_datetimes(cls, value: datetime) -> datetime:
+        if value.utcoffset() is None:
             raise ValueError("manifest datetimes must be timezone-aware")
         return value
@@
         if self.collection_start_not_before < self.created_at:
             raise ValueError("collection cannot start before manifest creation")
+        if self.draft_frozen_at < self.created_at:
+            raise ValueError("draft freeze cannot precede manifest creation")
+        if self.collection_start_not_before < self.draft_frozen_at:
+            raise ValueError("collection cannot start before draft freeze")
@@
-        if self.lifecycle_status in {"APPROVED_FOR_COLLECTION", "CLOSED"}:
-            pending = {"PENDING", "TBD", "UNASSIGNED"}
-            if self.approval_reference.upper() in pending:
-                raise ValueError("approved manifest needs an approval reference")
-            if self.approved_at is None:
-                raise ValueError("approved manifest needs approved_at")
-            if self.approved_at < self.created_at:
-                raise ValueError("manifest approval cannot precede creation")
-            if self.approved_at > self.collection_start_not_before:
-                raise ValueError("collection cannot begin before manifest approval")
-            if self.owner.casefold() == self.independent_reviewer.casefold():
+        if self.governance_mode == "INDEPENDENT_REVIEW":
+            if self.independent_reviewer is None:
+                raise ValueError("independent review mode needs a reviewer")
+            if self.owner.casefold() == self.independent_reviewer.casefold():
                 raise ValueError("independent reviewer must differ from owner")
-        elif self.approved_at is not None:
-            raise ValueError("only an approved manifest may set approved_at")
+        else:
+            if self.independent_reviewer is not None:
+                raise ValueError("solo mode must not claim an independent reviewer")
+            if (
+                self.collection_start_not_before
+                < self.draft_frozen_at + SOLO_A1_COOLING_OFF
+            ):
+                raise ValueError(
+                    "solo collection window must allow the 72-hour cooling-off"
+                )
         return self
+
+
+class ApprovalRecord(_EvaluationOnlyArtifact):
+    """External A1 authorization for one exact frozen DRAFT manifest."""
+
+    contract_version: Literal["shadow-approval-record-v1"] = (
+        SHADOW_APPROVAL_RECORD_VERSION
+    )
+    approval_id: NonEmptyString
+    approval_gate: Literal["A1"] = "A1"
+    approval_scope: Literal["SHADOW_COLLECTION_ONLY"] = "SHADOW_COLLECTION_ONLY"
+    approval_decision: Literal["APPROVED_FOR_COLLECTION"] = (
+        "APPROVED_FOR_COLLECTION"
+    )
+    protocol_id: NonEmptyString
+    component_id: ComponentID
+    manifest_contract_version: Literal["shadow-protocol-manifest-v2"]
+    manifest_revision: int = Field(ge=1)
+    canonicalization_version: Literal["shadow-canonical-json-v1"] = (
+        SHADOW_CANONICALIZATION_VERSION
+    )
+    draft_manifest_canonical_sha256: Sha256
+    draft_manifest_raw_file_sha256: Sha256
+    draft_manifest_raw_byte_length: int = Field(gt=0)
+    draft_frozen_at: datetime
+    decided_at: datetime
+    owner: NonEmptyString
+    governance_mode: GovernanceMode
+    approved_by: NonEmptyString
+    independent_reviewer: NonEmptyString | None = None
+    trading_calendar_sha256: Sha256
+    completed_idx_trading_sessions: tuple[date, ...] = ()
+    canonical_hash_recomputed: Literal[True] = True
+    raw_file_hash_recomputed: Literal[True] = True
+    automated_contract_validation_passed: Literal[True] = True
+    self_adversarial_review: tuple[SelfAdversarialReviewItem, ...] = ()
+    attestation: Literal[
+        "I approve A1 for this exact manifest hash pair, for shadow collection "
+        "only, with live_authority=false."
+    ]
+
+    @field_validator("draft_frozen_at", "decided_at")
+    @classmethod
+    def require_aware_approval_times(cls, value: datetime) -> datetime:
+        if value.utcoffset() is None:
+            raise ValueError("approval datetimes must be timezone-aware")
+        return value
+
+    @model_validator(mode="after")
+    def verify_approval(self) -> ApprovalRecord:
+        _verify_protocol_component(self.protocol_id, self.component_id)
+        if self.decided_at < self.draft_frozen_at:
+            raise ValueError("approval cannot precede draft freeze")
+        if tuple(sorted(set(self.completed_idx_trading_sessions))) != (
+            self.completed_idx_trading_sessions
+        ):
+            raise ValueError("completed IDX sessions must be unique and ordered")
+
+        if self.governance_mode == "SOLO_SELF_REVIEW":
+            if self.independent_reviewer is not None:
+                raise ValueError("solo approval must not claim an independent reviewer")
+            if self.approved_by.casefold() != self.owner.casefold():
+                raise ValueError("solo approval must be performed by the owner")
+            if self.decided_at < self.draft_frozen_at + SOLO_A1_COOLING_OFF:
+                raise ValueError("solo A1 approval requires 72 elapsed hours")
+            if (
+                len(self.completed_idx_trading_sessions)
+                < SOLO_A1_MIN_COMPLETED_IDX_SESSIONS
+            ):
+                raise ValueError("solo A1 approval requires two completed IDX sessions")
+            frozen_local_date = self.draft_frozen_at.astimezone(IDX_TIMEZONE).date()
+            decided_local_date = self.decided_at.astimezone(IDX_TIMEZONE).date()
+            if any(
+                session <= frozen_local_date or session > decided_local_date
+                for session in self.completed_idx_trading_sessions
+            ):
+                raise ValueError(
+                    "completed IDX sessions must be after freeze and not after approval"
+                )
+            prompt_ids = tuple(
+                item.prompt_id for item in self.self_adversarial_review
+            )
+            if len(prompt_ids) != len(set(prompt_ids)):
+                raise ValueError("self-review prompt IDs must be unique")
+            if set(prompt_ids) != set(SELF_ADVERSARIAL_PROMPTS):
+                raise ValueError("solo approval requires every frozen review prompt")
+            if any(
+                item.disposition != "PASS"
+                for item in self.self_adversarial_review
+            ):
+                raise ValueError("solo approval requires every review item to pass")
+        else:
+            if self.independent_reviewer is None:
+                raise ValueError("independent approval requires a reviewer")
+            if self.owner.casefold() == self.independent_reviewer.casefold():
+                raise ValueError("independent reviewer must differ from owner")
+            if (
+                self.approved_by.casefold()
+                != self.independent_reviewer.casefold()
+            ):
+                raise ValueError("independent approval must be signed by the reviewer")
+        return self
+
+
+def verify_approval_binding(
+    manifest: ShadowProtocolManifest,
+    manifest_raw_file_bytes: bytes,
+    approval: ApprovalRecord,
+    *,
+    verified_completed_idx_trading_sessions: Sequence[date],
+) -> None:
+    """Verify A1 against the exact persisted DRAFT bytes and frozen calendar."""
+
+    def reject_duplicate_json_pairs(
+        pairs: list[tuple[str, object]],
+    ) -> dict[str, object]:
+        result: dict[str, object] = {}
+        for key, value in pairs:
+            if key in result:
+                raise ShadowContractError(f"duplicate JSON key: {key}")
+            result[key] = value
+        return result
+
+    try:
+        raw_payload = json.loads(
+            manifest_raw_file_bytes.decode("utf-8"),
+            object_pairs_hook=reject_duplicate_json_pairs,
+        )
+        raw_manifest = ShadowProtocolManifest.model_validate(raw_payload)
+    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
+        raise ShadowContractError("raw manifest file is invalid") from exc
+
+    raw_sha256 = hashlib.sha256(manifest_raw_file_bytes).hexdigest()
+    canonical_hash = canonical_sha256(raw_manifest)
+    if raw_sha256 != approval.draft_manifest_raw_file_sha256:
+        raise ShadowContractError("raw manifest SHA-256 differs from approval")
+    if len(manifest_raw_file_bytes) != approval.draft_manifest_raw_byte_length:
+        raise ShadowContractError("raw manifest byte length differs from approval")
+    if canonical_hash != approval.draft_manifest_canonical_sha256:
+        raise ShadowContractError("canonical manifest SHA-256 differs from approval")
+    if canonical_sha256(manifest) != canonical_hash:
+        raise ShadowContractError("manifest object differs from approved raw file")
+    if (
+        approval.protocol_id,
+        approval.component_id,
+        approval.manifest_revision,
+        approval.manifest_contract_version,
+        approval.draft_frozen_at,
+        approval.owner,
+        approval.governance_mode,
+        approval.independent_reviewer,
+        approval.trading_calendar_sha256,
+    ) != (
+        manifest.protocol_id,
+        manifest.component_id,
+        manifest.manifest_revision,
+        manifest.contract_version,
+        manifest.draft_frozen_at,
+        manifest.owner,
+        manifest.governance_mode,
+        manifest.independent_reviewer,
+        manifest.trading_calendar_sha256,
+    ):
+        raise ShadowContractError("approval identity differs from manifest")
+    if approval.decided_at > manifest.collection_start_not_before:
+        raise ShadowContractError("collection window begins before A1 approval")
+    if approval.governance_mode == "SOLO_SELF_REVIEW" and tuple(
+        verified_completed_idx_trading_sessions
+    ) != approval.completed_idx_trading_sessions:
+        raise ShadowContractError(
+            "approval sessions differ from the frozen trading calendar"
+        )
@@
 __all__ = [
+    "ApprovalRecord",
@@
+    "GovernanceMode",
@@
+    "SELF_ADVERSARIAL_PROMPTS",
+    "SHADOW_APPROVAL_RECORD_VERSION",
+    "SHADOW_CANONICALIZATION_VERSION",
@@
+    "SOLO_A1_COOLING_OFF",
+    "SOLO_A1_MIN_COMPLETED_IDX_SESSIONS",
+    "SelfAdversarialReviewItem",
@@
+    "verify_approval_binding",
 ]
```

### 3.1 Required atomic companion changes after approval

The diff above must not be applied in isolation. Moving the manifest to
always-DRAFT intentionally makes the current internal lifecycle check in
`outcome_engine.py:440-444` fail closed. A later approved implementation must
atomically:

- pass the `ApprovalRecord`, exact raw manifest bytes, and frozen-calendar
  session evidence into collection and maturation trust boundaries;
- replace the internal lifecycle-status trust with
  `verify_approval_binding(...)`;
- verify approval before the first observation as well as before maturation;
- add exclusive-create storage for manifest and approval artifacts;
- export the new contracts from `core/shadow_protocol/__init__.py`;
- update v1 test fixtures to v2 without treating old v1 objects as newly
  approved;
- add tests for circularity, duplicate JSON keys, raw-only tampering,
  canonical tampering, cooling expiry, two-session evidence, fake reviewer
  aliases, incomplete review, and forbidden authority flags.

These are implementation consequences, not authorization to perform them now.

## 4. Step 1B — Solo RS-P2-023 reporting cadence

RS-P2-023 requires separate daily integrity, weekly operational, monthly
blinded data-quality, and fixed-terminal reports
(`RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md:427-428`). The protocol specifies
their purposes at `SHADOW_MODE_PROTOCOL.md:445-450`.

For a solo operator, the **machine cadence remains four separate cadences**.
Only repetitive human formatting and notification are collapsed.

### 4.1 Daily integrity

**Protects against**

- manifest/snapshot/source/artifact tampering;
- control/challenger opportunity-set mismatch;
- stale/expired sources and broken lineage;
- forbidden authority, ranking, sizing, or execution drift;
- immediate safety-stop conditions.

**Form**

- Always write an immutable, canonical machine-readable pass/fail artifact.
- Include protocol/hash identity, parity, source freshness, authority literals,
  lineage status, safety-stop status, and the prior daily record hash.
- On `PASS`, produce no routine narrative and no notification.
- On `FAIL/STOP`, immediately preserve evidence, stop the affected challenger,
  and generate a human-readable incident report and visible alert.

**Solo adaptation:** automated-silent on success. No daily Markdown report is
required. Statistical efficacy fields are forbidden pre-terminal.

### 4.2 Weekly operational

**Protects against**

- accumulating missingness, quarantines, or unmatched pairs;
- maturity/backfill backlog;
- provider outages, retry storms, latency/cost anomalies;
- incomplete cluster assignment and silent collection gaps.

**Form**

- Always write a separate immutable machine-readable weekly artifact.
- Include counts, missingness reasons, pending/mature/invalid counts without
  outcome values, maturity schedule, cluster-assignment coverage, job health,
  incidents, and hashes.
- On green status, no separate narrative or mandatory sit-down review.
- On amber/red status, render an exception-only human report and alert.

**Solo adaptation:** weekly human formatting is collapsed into exception
handling; green weekly summaries roll into the monthly report. Weekly remains
separate from daily at the artifact level.

### 4.3 Monthly blinded data-quality

**Protects against**

- slow source/universe/missingness drift;
- dependence-adjusted sample composition problems;
- broken train/calibration/test separation;
- trial-registry omissions or checksum drift;
- premature interpretation of efficacy.

**Form**

- Write canonical JSON plus a concise human-readable blinded report.
- Aggregate weekly operations and report raw `n`, independent clusters,
  effective `n` when estimable, issuer/date/event-block coverage, source
  vintage/expiry, quarantine, maturity, registry checksum, and protocol drift.
- Exclude side-by-side return, hit rate, calibration, Sharpe/DSR, and any
  "challenger is winning" language.
- Require the owner to read and acknowledge the report once per month.

**Solo adaptation:** generation and validation are automated, but the monthly
document remains a genuine human review. It is explicitly labeled
`BLINDED_NO_EFFICACY_INTERPRETATION`.

### 4.4 Fixed-terminal review

**Protects against**

- selective unblinding and post-hoc threshold changes;
- ignoring negative/null findings;
- treating inadequate or dependent samples as conclusive;
- substituting raw Sharpe or zero for DSR/`NOT_ESTIMABLE`;
- promoting a challenger without paired control evidence.

**Form**

- This remains a full, human-readable report that the owner must deliberately
  read. It may never be silent or reduced to an automated green check.
- Unblind only after the fixed date and full permitted maturity.
- Reproduce calculations in a clean process from immutable artifacts and record
  all input/output hashes. This is computational reproduction, not a claim that
  another human reviewed it.
- Show all common metrics with explicit denominators, raw and
  dependence-adjusted sample counts, control/challenger paired results,
  fixed-notional and policy-portfolio results when those phases exist, daily
  marked-to-market NAV, drawdown, calibration, DSR readiness, registry,
  deviations, negative/null results, and every `NOT_ESTIMABLE`.
- Apply the predeclared component gate. C7/C8 may receive only a
  `SAFETY_GO`; they may not claim return superiority.
- Produce an explicit `GO`, `CONTINUE`, or `NO_GO` decision and rollback path.
  No auto-GO is permitted.
- Label the sign-off `SOLO_SELF_REVIEWED`, never
  `INDEPENDENTLY_HUMAN_REVIEWED`.

**Solo adaptation:** none of the substantive terminal work is collapsed. The
independent-human ceremony is honestly replaced by clean-process deterministic
reproduction, the recorded self-adversarial review, and a real owner decision.

### 4.5 Cadence decision matrix

| Cadence | Immutable machine artifact | Human-readable on PASS | Human attention on PASS | Human output on failure |
|---|---:|---:|---:|---:|
| Daily integrity | Yes | No | No | Mandatory incident report |
| Weekly operational | Yes | No | No | Mandatory exception report |
| Monthly blinded quality | Yes | Yes | Mandatory owner acknowledgment | Mandatory |
| Fixed terminal | Yes | Yes, full report | Mandatory sit-down GO/CONTINUE/NO-GO review | Mandatory |

Daily and weekly may be notification-silent when healthy. They are not deleted
or merged on disk. Monthly and fixed-terminal remain real documents.

## 5. Safeguards explicitly unchanged

No safeguard below is relaxed by the solo-governance proposal.

### 5.1 Machine-enforced contract and authority safeguards

- Strict frozen models continue to reject unknown fields, mutation, default
  validation failures, non-finite floats, and unstripped strings
  (`contracts.py:66-75`).
- Every shadow artifact remains locked to `evaluation_only=true`,
  `live_authority=false`, `affects_execution=false`, `affects_ranking=false`,
  and `affects_sizing=false` (`contracts.py:78-85`; `evidence.py:55-60`).
- The production feature flag remains default-off
  (`contracts.py:291-293`).
- SHA-256 fields remain exactly 64 lowercase hexadecimal characters
  (`contracts.py:51-54`).
- Canonical JSON remains deterministic, key-sorted, NaN-forbidden,
  null-preserving, and timezone-normalized (`contracts.py:1286-1327`).
- GO/CONTINUE/NO-GO wording and order remain hash-exact
  (`contracts.py:218-252`).
- Protocol/component identity, unique source IDs, thresholds, features,
  tickers, and content-hash paths remain validated
  (`contracts.py:302-327`, `1330-1346`).
- Manifest timestamps, fixed terminal date, universe, code/source hashes,
  labels, costs, cluster rules, feature flag, and rollback remain frozen. The
  proposed v2 removes only mutable approval state from the manifest.

### 5.2 Point-in-time, pairing, and anti-leakage safeguards

- Snapshot and candidate ticker, source, as-of, expiry, and payload hashes
  remain point-in-time and fail closed (`evidence.py:132-207`, `259-354`).
- Raw candidate capture remains before either side can prune; persistence
  remains raw-first and exclusive-create (`evidence.py:652-763`,
  `1476-1579`).
- Quarantined rows must remain pruned identically by control and challenger.
- Exact opportunity-set identity, raw order/count, empty-set reason, and parity
  proof remain mandatory and reconstructed from source artifacts
  (`evidence.py:765-979`).
- Candidate → raw capture → paired set → observation chronology remains causal.
- Every source ID remains bound to the exact manifest source definition.
- Future bars, late-published corporate actions, expired gates, and future
  source vintages remain rejected.

### 5.3 Outcome, cost, and replay safeguards

- The sole primary horizon remains 15 trading days; 3/5/10 are separately
  maturing secondary horizons with multiplicity control.
- Signal-bar exclusion, full post-fill horizon, adverse gap pricing, stop-first
  same-bar ambiguity, and no unproven target credit remain unchanged.
- Raw-as-traded corporate-action handling remains unchanged; unsupported rights
  issues remain fail-closed/invalid.
- Outcome return, cost, risk, and net-R arithmetic remain recomputed rather than
  trusted from submitted values.
- Pending/invalid/unfilled records remain unable to carry terminal performance.
- Trust-boundary revalidation, complete lineage reconstruction, deterministic
  replay, idempotency, monotone backfill, and immutable terminal outcomes remain
  unchanged (`outcome_engine.py:991-1013`, `1035-1184`).
- Trial-registry events remain append-only, hash-chained, pre-outcome
  registered, and collision-rejecting (`contracts.py:1070-1231`).

### 5.4 Statistical and promotion safeguards

- Raw rows remain explicitly non-independent. The unit remains
  `INDEPENDENT_CLUSTER`, with `effective_n <= independent_cluster_n <=
  assigned_raw_n <= raw_n` (`contracts.py:1222-1282`).
- `NOT_ESTIMABLE` remains explicit and cannot publish a fabricated
  `effective_n` (`contracts.py:1265-1275`).
- The minimum remains at least 30 dependence-adjusted **affected independent
  clusters**, with stricter component-specific power/precision requirements.
  Unaffected rows cannot pad `n`; wide intervals remain `CONTINUE/NO-GO`
  (`SHADOW_MODE_PROTOCOL.md:60-73`, `147-154`).
- Important implementation honesty: the current schema validates sample-count
  consistency, but the universal numeric floor of 30 is still primarily a
  protocol/promotion requirement rather than one global runtime validator.
  This proposal neither marks that gap solved nor reduces the floor.
- Train/calibration/untouched-test chronology, 15-day purge/embargo, fixed
  terminal date, and no mid-run tuning remain unchanged.
- All attempted variants and secondary horizons remain registered; Holm/DSR/PBO
  and stopping rules remain unchanged.
- DSR cannot support GO until C6 passes. An outcome-changing component with
  non-estimable DSR cannot GO.
- Daily marked-to-market NAV—not a list of closed trades—remains the required
  basis for portfolio Sharpe, drawdown, exposure, volatility, and turnover.
- C7/C8 remain safety-only: formal/property tests, at least 30 affected events,
  100% invariant enforcement, zero out-of-scope false blocking, and no
  return-superiority claim.
- Signal frequency and BUY count remain diagnostic only, never a GO target.
- Components may collect in parallel but may be promoted only one at a time
  against the newly frozen control.
- A1 remains collection-only. A2/A3/A4 authority is not implied.

### 5.5 Immediate stops and tamper tests

The following remain immediate STOP/NO-GO conditions:

- look-ahead, survivorship, revised-fundamental, or timestamp leakage;
- control/challenger opportunity-set mismatch;
- source, hash, snapshot, manifest, or approval corruption;
- hard-gate false promotion;
- any paper order reaching live execution;
- unregistered model/config selection;
- challenger drawdown breaching its frozen safety envelope;
- any human-readable report claiming a shadow result is live/trusted.

Existing canonical-hash, decision-payload, parity, lineage, source-vintage,
outcome-forgery, replay, idempotency, monotonicity, and authority-literal tests
remain required. New raw-file-hash and ApprovalRecord tests add protection; they
do not replace any current test.

## 6. Baseline and downstream boundaries

- `RS-CONTROL-20260717-01` remains unchanged. This proposal finds no
  RS-P2-014 paired-view field deficiency because RS-P2-014 was not designed or
  implemented in this pass.
- The existing ten reserved protocol IDs remain unchanged.
- C7 remains the first component priority after the common Phase-2 substrate;
  C1 follows.
- No threshold, source behavior, live execution, ranking, sizing, or control
  decision path is changed.
- Part 2 / RS-P2-014 has not begun. Its PortfolioState and paired-view design
  awaits a separate go-ahead after this proposal is approved.

## 7. Approval requested

Approval of this document would authorize the next pass to prepare and test the
solo-governance contract change and its atomic approval-consumer changes. It
would not itself grant A1 to any component, start collection, implement
RS-P2-014, unblind outcomes, or change live authority.

