"""Strict, evaluation-only contracts for generic paired shadow protocols.

This module is intentionally isolated from the orchestrator, Risk Governor,
ranking, sizing, and order paths.  It defines evidence records only.  Importing
it cannot execute a pipeline, write an artifact, or grant live authority.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from types import MappingProxyType
from typing import Annotated, Literal, Mapping, Sequence, TypeAlias
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


SHADOW_PROTOCOL_MANIFEST_VERSION = "shadow-protocol-manifest-v2"
SHADOW_APPROVAL_RECORD_VERSION = "shadow-approval-record-v1"
SHADOW_PROTOCOL_CLOSURE_VERSION = "shadow-protocol-closure-v1"
SHADOW_APPROVAL_LEDGER_EVENT_VERSION = "shadow-approval-ledger-event-v1"
SHADOW_APPROVAL_LEDGER_VERSION = "shadow-approval-ledger-v1"
SHADOW_CANONICALIZATION_VERSION = "shadow-canonical-json-v1"
SHADOW_OBSERVATION_VERSION = "shadow-observation-v1"
SHADOW_OUTCOME_VERSION = "shadow-outcome-v1"
TRIAL_REGISTRY_VERSION = "shadow-trial-registry-v1"
EFFECTIVE_SAMPLE_VERSION = "shadow-effective-sample-v1"
IDX_TIMEZONE = ZoneInfo("Asia/Jakarta")
SOLO_A1_COOLING_OFF = timedelta(days=3)
SOLO_A1_MIN_COMPLETED_IDX_SESSIONS = 2

ComponentID: TypeAlias = Literal[
    "C1",
    "C2",
    "C3",
    "C4a",
    "C4b1",
    "C4b2",
    "C5",
    "C6",
    "C7",
    "C8",
]
PrimitiveValue: TypeAlias = str | int | float | bool | None
NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
Sha256 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]
CanonicalTicker = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Z0-9][A-Z0-9.-]{0,15}$"),
]
SecondaryHorizons: TypeAlias = tuple[Literal[3], Literal[5], Literal[10]]
GovernanceMode: TypeAlias = Literal[
    "INDEPENDENT_REVIEW",
    "SOLO_SELF_REVIEW",
]
ReviewDisposition: TypeAlias = Literal["PASS", "FAIL", "BLOCKED"]
SelfReviewPromptID: TypeAlias = Literal[
    "SKEPTICAL_SUMMARY",
    "THRESHOLD_FREEZE",
    "UNIVERSE_BIAS",
    "COST_AND_LIQUIDITY",
    "SOURCE_LINEAGE",
    "LABEL_LEAKAGE",
    "PAIRED_PARITY",
    "INDEPENDENCE_AND_SAMPLE",
    "MULTIPLICITY_AND_DSR",
    "FAIL_CLOSED_METRICS",
    "SAFETY_AND_ROLLBACK",
    "DISCONFIRMING_RESULT",
]
ApprovalLedgerEventType: TypeAlias = Literal[
    "A1_APPROVED",
    "OBSERVATION_AUTHORIZED",
    "PROTOCOL_CLOSED",
]
ApprovalLedgerRecordKind: TypeAlias = Literal[
    "APPROVAL",
    "OBSERVATION",
    "CLOSURE",
]
ClosureReason: TypeAlias = Literal[
    "OWNER_REQUEST",
    "FIXED_TERMINAL_REACHED",
    "INTEGRITY_STOP",
    "SAFETY_STOP",
    "DRAWDOWN_STOP",
    "SOURCE_CORRUPTION",
    "MANIFEST_CORRUPTION",
    "SUPERSEDED",
    "OTHER",
]
ClosureMaturationPolicy: TypeAlias = Literal[
    "ALLOW_PRE_CLOSURE_MATURATION",
    "BLOCK_ALL_MATURATION",
]

SELF_ADVERSARIAL_PROMPTS: Mapping[SelfReviewPromptID, str] = MappingProxyType({
    "SKEPTICAL_SUMMARY": (
        "What would a skeptical outside reviewer flag about this manifest's "
        "thresholds, universe definition, and cost assumptions?"
    ),
    "THRESHOLD_FREEZE": (
        "Which thresholds or choices could have been selected after seeing "
        "favorable outcomes, and what proves they were frozen before collection?"
    ),
    "UNIVERSE_BIAS": (
        "Could exclusions, missing delisted names, survivorship, sector "
        "concentration, or point-in-time membership make the challenger look better?"
    ),
    "COST_AND_LIQUIDITY": (
        "Are commission, tax, bid-ask, slippage, lot, notional, capacity, and "
        "missing-liquidity assumptions conservative and frozen?"
    ),
    "SOURCE_LINEAGE": (
        "Does every input have a source, as-of time, expiry rule, hash/version, "
        "and fail-closed missing-data behavior with no future information?"
    ),
    "LABEL_LEAKAGE": (
        "Can activation, fill, gap, ambiguity, corporate-action, or maturity "
        "rules leak future information or censor adverse outcomes?"
    ),
    "PAIRED_PARITY": (
        "Do control and challenger receive exactly the same opportunity set, "
        "snapshots, timestamps, costs, labels, and frozen control state?"
    ),
    "INDEPENDENCE_AND_SAMPLE": (
        "Could duplicated or correlated rows inflate sample size, and do affected "
        "independent clusters-not raw rows-meet the frozen minimum and precision rule?"
    ),
    "MULTIPLICITY_AND_DSR": (
        "Are every tried variant and horizon registered, and are DSR/PBO or "
        "NOT_ESTIMABLE rules protected from selective reporting?"
    ),
    "FAIL_CLOSED_METRICS": (
        "Which metrics can be NOT_ESTIMABLE, and does each such case block GO "
        "rather than become zero or a favorable substitute?"
    ),
    "SAFETY_AND_ROLLBACK": (
        "What exact leakage, parity, authority, drawdown, hard-gate, and corruption "
        "events stop the challenger, and is rollback reproducible?"
    ),
    "DISCONFIRMING_RESULT": (
        "What evidence would force NO-GO, what result might tempt the owner to "
        "override the rule, and what precommitment prevents that override?"
    ),
})


class ShadowContractError(ValueError):
    """A generic shadow artifact violates a frozen protocol invariant."""


class _StrictFrozenModel(BaseModel):
    """Base contract: no extras, no mutation, and no non-finite floats."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class _EvaluationOnlyArtifact(_StrictFrozenModel):
    """Authority literals shared by every generic shadow artifact."""

    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False


class ContentHash(_StrictFrozenModel):
    """Content identity for one frozen input, source, or implementation file."""

    path: NonEmptyString
    sha256: Sha256
    role: Literal[
        "CONTROL",
        "CHALLENGER",
        "SOURCE",
        "CONFIG",
        "FEATURE",
        "LABEL",
        "OTHER",
    ]


class FrozenParameter(_StrictFrozenModel):
    """One named, reviewable threshold, parameter, or registry detail."""

    name: NonEmptyString
    value: PrimitiveValue
    unit: str | None = None
    source: NonEmptyString
    description: str | None = None

    @field_validator("value")
    @classmethod
    def reject_nonfinite_value(cls, value: PrimitiveValue) -> PrimitiveValue:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("parameter value must be finite")
        return value


class FeatureDefinition(_StrictFrozenModel):
    """Point-in-time feature definition frozen before collection."""

    name: NonEmptyString
    dtype: Literal["BOOL", "CATEGORY", "FLOAT", "INTEGER", "STRING"]
    source_id: NonEmptyString
    source_field: NonEmptyString
    as_of_field: NonEmptyString
    expiry_rule: NonEmptyString
    missing_policy: Literal["ABSTAIN", "CONTROL_DEFAULT", "EXCLUDE", "UNKNOWN"]
    transformation: NonEmptyString


class SourceDefinition(_StrictFrozenModel):
    """Source locator, vintage, expiry, and missingness behavior."""

    source_id: NonEmptyString
    source_type: Literal[
        "DATABASE",
        "FILE",
        "MARKET_SNAPSHOT",
        "MODEL",
        "OFFICIAL_API",
        "OFFICIAL_DOCUMENT",
        "OTHER",
    ]
    locator: NonEmptyString
    as_of_field: NonEmptyString
    expiry_rule: NonEmptyString
    missing_policy: Literal["ABSTAIN", "CONTROL_DEFAULT", "EXCLUDE", "UNKNOWN"]
    contract_version: NonEmptyString
    source_sha256: Sha256 | None = None


class UniverseDefinition(_StrictFrozenModel):
    """Complete opportunity-set definition shared by control and challenger."""

    universe_id: NonEmptyString
    quant_mode: NonEmptyString
    selection_rule: NonEmptyString
    point_in_time: Literal[True] = True
    candidate_source_sha256: Sha256
    explicit_tickers: tuple[CanonicalTicker, ...] = ()


class CostAssumptions(_StrictFrozenModel):
    """Frozen transaction-cost and execution-unit assumptions."""

    currency: Literal["IDR"] = "IDR"
    buy_commission_bps: float = Field(ge=0.0)
    sell_commission_bps: float = Field(ge=0.0)
    sell_tax_bps: float = Field(ge=0.0)
    slippage_bps: float = Field(ge=0.0)
    bid_ask_bps: float = Field(ge=0.0)
    lot_size: int = Field(ge=1)
    liquidity_execution_rule: NonEmptyString
    price_rounding_rule: NonEmptyString
    cost_model_version: NonEmptyString


class LabelDefinition(_StrictFrozenModel):
    """Frozen primary/secondary estimands and conservative fill conventions."""

    primary_horizon_trading_days: Literal[15] = 15
    secondary_horizons_trading_days: SecondaryHorizons = (3, 5, 10)
    primary_estimand: Literal[
        "TARGET_BEFORE_STOP_WITHIN_15_TRADING_DAYS_AFTER_VALID_FILL"
    ] = "TARGET_BEFORE_STOP_WITHIN_15_TRADING_DAYS_AFTER_VALID_FILL"
    entry_validity_trading_days: int = Field(ge=1, le=15)
    activation_rule: NonEmptyString
    horizon_clock_rule: NonEmptyString
    fill_rule: NonEmptyString
    gap_rule: NonEmptyString
    entry_gap_through_stop_rule: NonEmptyString
    same_bar_ambiguity_rule: NonEmptyString
    corporate_action_rule: NonEmptyString
    rights_treatment_rule: NonEmptyString
    dividend_return_convention: Literal["PRICE_RETURN", "TOTAL_RETURN"]
    dividend_entitlement_rule: NonEmptyString
    unfilled_rule: NonEmptyString


class ClusterRuleDefinition(_StrictFrozenModel):
    """Pre-run rule for dependence clusters and effective sample accounting."""

    rule_version: NonEmptyString
    overlap_window_trading_days: Literal[15] = 15
    same_ticker_overlapping_windows: Literal[True] = True
    issuer_group_rule: NonEmptyString
    economic_group_rule: NonEmptyString
    correlation_cluster_rule: NonEmptyString
    systemic_date_block_rule: NonEmptyString
    duplicate_setup_rule: NonEmptyString
    representative_rule: NonEmptyString
    effective_n_rule: NonEmptyString


def canonical_rules_sha256(
    go_rules: Sequence[str],
    continue_rules: Sequence[str],
    no_go_rules: Sequence[str],
) -> str:
    """Hash the exact GO/CONTINUE/NO-GO wording and order."""

    payload = {
        "CONTINUE": list(continue_rules),
        "GO": list(go_rules),
        "NO_GO": list(no_go_rules),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class GoNoGoRules(_StrictFrozenModel):
    """Component-specific decision rules copied verbatim into the manifest."""

    go: tuple[NonEmptyString, ...] = Field(min_length=1)
    continue_rules: tuple[NonEmptyString, ...] = Field(min_length=1)
    no_go: tuple[NonEmptyString, ...] = Field(min_length=1)
    rules_sha256: Sha256

    @model_validator(mode="after")
    def verify_rule_hash(self) -> GoNoGoRules:
        expected = canonical_rules_sha256(self.go, self.continue_rules, self.no_go)
        if self.rules_sha256 != expected:
            raise ValueError("GO/CONTINUE/NO-GO wording hash mismatch")
        return self


class SelfAdversarialReviewItem(_StrictFrozenModel):
    """One mandatory, evidence-backed solo review answer."""

    prompt_id: SelfReviewPromptID
    prompt_text: NonEmptyString
    response: NonEmptyString
    evidence_refs: tuple[NonEmptyString, ...] = Field(min_length=1)
    disposition: ReviewDisposition

    @model_validator(mode="after")
    def verify_prompt_text(self) -> SelfAdversarialReviewItem:
        if self.prompt_text != SELF_ADVERSARIAL_PROMPTS[self.prompt_id]:
            raise ValueError("self-review prompt text differs from the frozen prompt")
        return self


class ShadowProtocolManifest(_EvaluationOnlyArtifact):
    """Strict pre-registration contract for exactly one challenger component."""

    contract_version: Literal["shadow-protocol-manifest-v2"] = (
        SHADOW_PROTOCOL_MANIFEST_VERSION
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_revision: int = Field(ge=1)
    lifecycle_status: Literal["DRAFT"] = "DRAFT"
    created_at: datetime
    draft_frozen_at: datetime
    collection_start_not_before: datetime
    fixed_terminal_date: date
    owner: NonEmptyString
    governance_mode: GovernanceMode
    independent_reviewer: NonEmptyString | None = None
    rollback_owner: NonEmptyString
    baseline_manifest_id: NonEmptyString
    baseline_manifest_sha256: Sha256
    methodology_document_path: NonEmptyString
    methodology_document_sha256: Sha256
    control_content_hashes: tuple[ContentHash, ...] = Field(min_length=1)
    challenger_content_hashes: tuple[ContentHash, ...] = Field(min_length=1)
    universe: UniverseDefinition
    trading_calendar_id: NonEmptyString
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    thresholds: tuple[FrozenParameter, ...]
    features: tuple[FeatureDefinition, ...]
    sources: tuple[SourceDefinition, ...] = Field(min_length=1)
    labels: LabelDefinition
    costs: CostAssumptions
    cluster_rules: ClusterRuleDefinition
    cluster_rules_sha256: Sha256
    go_no_go: GoNoGoRules
    trial_registry_id: NonEmptyString
    production_feature_flag: NonEmptyString
    production_feature_flag_default: Literal[False] = False
    rollback_plan: NonEmptyString

    @field_validator("created_at", "draft_frozen_at", "collection_start_not_before")
    @classmethod
    def require_aware_datetimes(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("manifest datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_manifest(self) -> ShadowProtocolManifest:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.fixed_terminal_date <= self.collection_start_not_before.astimezone(
            IDX_TIMEZONE
        ).date():
            raise ValueError("fixed_terminal_date must follow collection start")
        if self.collection_start_not_before < self.created_at:
            raise ValueError("collection cannot start before manifest creation")
        if self.draft_frozen_at < self.created_at:
            raise ValueError("draft freeze cannot precede manifest creation")
        if self.collection_start_not_before < self.draft_frozen_at:
            raise ValueError("collection cannot start before draft freeze")
        if self.cluster_rules_sha256 != canonical_sha256(self.cluster_rules):
            raise ValueError("cluster rule hash mismatch")
        _reject_duplicate_hash_paths(self.control_content_hashes, "control")
        _reject_duplicate_hash_paths(self.challenger_content_hashes, "challenger")
        source_ids = tuple(source.source_id for source in self.sources)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("manifest source IDs must be unique")
        threshold_names = tuple(item.name for item in self.thresholds)
        if len(threshold_names) != len(set(threshold_names)):
            raise ValueError("manifest threshold names must be unique")
        feature_names = tuple(item.name for item in self.features)
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("manifest feature names must be unique")
        if len(self.universe.explicit_tickers) != len(
            set(self.universe.explicit_tickers)
        ):
            raise ValueError("manifest explicit tickers must be unique")
        if self.governance_mode == "INDEPENDENT_REVIEW":
            if self.independent_reviewer is None:
                raise ValueError("independent review mode needs a reviewer")
            if self.owner.casefold() == self.independent_reviewer.casefold():
                raise ValueError("independent reviewer must differ from owner")
        else:
            if self.independent_reviewer is not None:
                raise ValueError("solo mode must not claim an independent reviewer")
            if self.rollback_owner.casefold() != self.owner.casefold():
                raise ValueError("solo rollback owner must be the owner")
            if (
                self.collection_start_not_before
                < self.draft_frozen_at + SOLO_A1_COOLING_OFF
            ):
                raise ValueError(
                    "solo collection window must allow the 72-hour cooling-off"
                )
        return self


class ApprovalRecord(_EvaluationOnlyArtifact):
    """External A1 authorization for one exact frozen DRAFT manifest.

    The cooling timestamps are locally auditable but are not an externally
    trusted timestamp.  This record grants shadow collection only.
    """

    contract_version: Literal["shadow-approval-record-v1"] = (
        SHADOW_APPROVAL_RECORD_VERSION
    )
    approval_id: NonEmptyString
    approval_ledger_id: NonEmptyString
    approval_gate: Literal["A1"] = "A1"
    approval_scope: Literal["SHADOW_COLLECTION_ONLY"] = "SHADOW_COLLECTION_ONLY"
    approval_decision: Literal["APPROVED_FOR_COLLECTION"] = (
        "APPROVED_FOR_COLLECTION"
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_contract_version: Literal["shadow-protocol-manifest-v2"]
    manifest_revision: int = Field(ge=1)
    canonicalization_version: Literal["shadow-canonical-json-v1"] = (
        SHADOW_CANONICALIZATION_VERSION
    )
    draft_manifest_canonical_sha256: Sha256
    draft_manifest_raw_file_sha256: Sha256
    draft_manifest_raw_byte_length: int = Field(gt=0)
    draft_frozen_at: datetime
    decided_at: datetime
    owner: NonEmptyString
    governance_mode: GovernanceMode
    approved_by: NonEmptyString
    independent_reviewer: NonEmptyString | None = None
    trading_calendar_contract_version: Literal["shadow-trading-calendar-v1"]
    trading_calendar_id: NonEmptyString
    trading_calendar_sha256: Sha256
    completed_idx_trading_sessions: tuple[date, ...] = ()
    canonical_hash_recomputed: Literal[True] = True
    raw_file_hash_recomputed: Literal[True] = True
    automated_contract_validation_passed: Literal[True] = True
    self_adversarial_review: tuple[SelfAdversarialReviewItem, ...] = ()
    attestation: Literal[
        "I approve A1 for this exact manifest hash pair, for shadow collection "
        "only, with live_authority=false."
    ]

    @field_validator("draft_frozen_at", "decided_at")
    @classmethod
    def require_aware_approval_times(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("approval datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_approval(self) -> ApprovalRecord:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.decided_at < self.draft_frozen_at:
            raise ValueError("approval cannot precede draft freeze")
        if tuple(sorted(set(self.completed_idx_trading_sessions))) != (
            self.completed_idx_trading_sessions
        ):
            raise ValueError("completed IDX sessions must be unique and ordered")

        if self.governance_mode == "SOLO_SELF_REVIEW":
            if self.independent_reviewer is not None:
                raise ValueError("solo approval must not claim an independent reviewer")
            if self.approved_by.casefold() != self.owner.casefold():
                raise ValueError("solo approval must be performed by the owner")
            if self.decided_at < self.draft_frozen_at + SOLO_A1_COOLING_OFF:
                raise ValueError("solo A1 approval requires 72 elapsed hours")
            if (
                len(self.completed_idx_trading_sessions)
                < SOLO_A1_MIN_COMPLETED_IDX_SESSIONS
            ):
                raise ValueError("solo A1 approval requires two completed IDX sessions")
            frozen_local_date = self.draft_frozen_at.astimezone(IDX_TIMEZONE).date()
            decided_local_date = self.decided_at.astimezone(IDX_TIMEZONE).date()
            if any(
                session <= frozen_local_date or session > decided_local_date
                for session in self.completed_idx_trading_sessions
            ):
                raise ValueError(
                    "completed IDX sessions must be after freeze and not after approval"
                )
            prompt_ids = tuple(
                item.prompt_id for item in self.self_adversarial_review
            )
            if prompt_ids != tuple(SELF_ADVERSARIAL_PROMPTS):
                raise ValueError(
                    "solo approval requires every frozen review prompt in order"
                )
            if any(
                item.disposition != "PASS"
                for item in self.self_adversarial_review
            ):
                raise ValueError("solo approval requires every review item to pass")
        else:
            if self.independent_reviewer is None:
                raise ValueError("independent approval requires a reviewer")
            if self.owner.casefold() == self.independent_reviewer.casefold():
                raise ValueError("independent reviewer must differ from owner")
            if (
                self.approved_by.casefold()
                != self.independent_reviewer.casefold()
            ):
                raise ValueError("independent approval must be signed by the reviewer")
        return self


class ProtocolClosureRecord(_EvaluationOnlyArtifact):
    """External terminal collection state; the DRAFT manifest never mutates."""

    contract_version: Literal["shadow-protocol-closure-v1"] = (
        SHADOW_PROTOCOL_CLOSURE_VERSION
    )
    closure_id: NonEmptyString
    approval_ledger_id: NonEmptyString
    closure_scope: Literal["STOP_NEW_SHADOW_COLLECTION"] = (
        "STOP_NEW_SHADOW_COLLECTION"
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_contract_version: Literal["shadow-protocol-manifest-v2"]
    manifest_revision: int = Field(ge=1)
    draft_manifest_canonical_sha256: Sha256
    draft_manifest_raw_file_sha256: Sha256
    draft_manifest_raw_byte_length: int = Field(gt=0)
    approval_id: NonEmptyString
    approval_record_canonical_sha256: Sha256
    effective_at: datetime
    recorded_at: datetime
    closed_by: NonEmptyString
    governance_mode: GovernanceMode
    reason_code: ClosureReason
    reason: NonEmptyString
    maturation_policy: ClosureMaturationPolicy
    preserve_artifacts: Literal[True] = True
    new_observations_allowed: Literal[False] = False
    authorizes_unblinding: Literal[False] = False
    authorizes_promotion: Literal[False] = False

    @field_validator("effective_at", "recorded_at")
    @classmethod
    def require_aware_closure_times(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("closure datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_closure(self) -> ProtocolClosureRecord:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.recorded_at < self.effective_at:
            raise ValueError("closure cannot be recorded before it is effective")
        if (
            self.reason_code != "FIXED_TERMINAL_REACHED"
            and self.recorded_at != self.effective_at
        ):
            raise ValueError("unscheduled closure must not be backdated")
        integrity_reasons = {
            "INTEGRITY_STOP",
            "SOURCE_CORRUPTION",
            "MANIFEST_CORRUPTION",
        }
        if (
            self.reason_code in integrity_reasons
            and self.maturation_policy != "BLOCK_ALL_MATURATION"
        ):
            raise ValueError("integrity closure must block maturation")
        return self


class ApprovalLedgerEvent(_EvaluationOnlyArtifact):
    """One append-only event in the external A1 lifecycle chain."""

    contract_version: Literal["shadow-approval-ledger-event-v1"] = (
        SHADOW_APPROVAL_LEDGER_EVENT_VERSION
    )
    ledger_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_revision: int = Field(ge=1)
    draft_manifest_canonical_sha256: Sha256
    draft_manifest_raw_file_sha256: Sha256
    draft_manifest_raw_byte_length: int = Field(gt=0)
    sequence: int = Field(ge=1)
    event_id: NonEmptyString
    previous_event_sha256: Sha256 | None = None
    event_type: ApprovalLedgerEventType
    record_kind: ApprovalLedgerRecordKind
    record_id: NonEmptyString
    record_contract_version: NonEmptyString
    record_canonical_sha256: Sha256
    record_raw_file_sha256: Sha256
    record_raw_byte_length: int = Field(gt=0)
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def require_aware_event_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("approval-ledger event time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_event(self) -> ApprovalLedgerEvent:
        _verify_protocol_component(self.protocol_id, self.component_id)
        expected_kind = {
            "A1_APPROVED": "APPROVAL",
            "OBSERVATION_AUTHORIZED": "OBSERVATION",
            "PROTOCOL_CLOSED": "CLOSURE",
        }[self.event_type]
        if self.record_kind != expected_kind:
            raise ValueError("approval-ledger event and record kind differ")
        if self.sequence == 1 and self.previous_event_sha256 is not None:
            raise ValueError("first approval-ledger event cannot have a predecessor")
        if self.sequence > 1 and self.previous_event_sha256 is None:
            raise ValueError("non-first approval-ledger event needs a predecessor")
        return self


class ApprovalLedger(_EvaluationOnlyArtifact):
    """Validated immutable view rebuilt from append-only event files."""

    contract_version: Literal["shadow-approval-ledger-v1"] = (
        SHADOW_APPROVAL_LEDGER_VERSION
    )
    ledger_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_revision: int = Field(ge=1)
    draft_manifest_canonical_sha256: Sha256
    draft_manifest_raw_file_sha256: Sha256
    draft_manifest_raw_byte_length: int = Field(gt=0)
    events: tuple[ApprovalLedgerEvent, ...] = ()

    @model_validator(mode="after")
    def verify_ledger(self) -> ApprovalLedger:
        _verify_protocol_component(self.protocol_id, self.component_id)
        event_ids: set[str] = set()
        record_ids: set[str] = set()
        approval_seen = False
        closure_seen = False
        previous: ApprovalLedgerEvent | None = None
        for expected_sequence, event in enumerate(self.events, start=1):
            if event.sequence != expected_sequence:
                raise ValueError("approval-ledger sequence must be contiguous")
            if (
                event.ledger_id,
                event.protocol_id,
                event.component_id,
                event.manifest_revision,
                event.draft_manifest_canonical_sha256,
                event.draft_manifest_raw_file_sha256,
                event.draft_manifest_raw_byte_length,
            ) != (
                self.ledger_id,
                self.protocol_id,
                self.component_id,
                self.manifest_revision,
                self.draft_manifest_canonical_sha256,
                self.draft_manifest_raw_file_sha256,
                self.draft_manifest_raw_byte_length,
            ):
                raise ValueError("approval-ledger event identity differs from ledger")
            expected_previous = (
                canonical_sha256(previous) if previous is not None else None
            )
            if event.previous_event_sha256 != expected_previous:
                raise ValueError("approval-ledger previous-event hash mismatch")
            if previous is not None and event.recorded_at < previous.recorded_at:
                raise ValueError("approval-ledger event time moved backward")
            if event.event_id in event_ids:
                raise ValueError("approval-ledger event IDs must be unique")
            if event.record_id in record_ids:
                raise ValueError("approval-ledger record IDs must be unique")
            event_ids.add(event.event_id)
            record_ids.add(event.record_id)

            if event.event_type == "A1_APPROVED":
                if approval_seen or expected_sequence != 1:
                    raise ValueError("A1 approval must be the sole first approval event")
                approval_seen = True
            elif event.event_type == "OBSERVATION_AUTHORIZED":
                if not approval_seen or closure_seen:
                    raise ValueError(
                        "observation authorization requires active A1 approval"
                    )
            else:
                if not approval_seen or closure_seen:
                    raise ValueError("protocol closure requires one active A1 approval")
                closure_seen = True
            previous = event

        if closure_seen and self.events[-1].event_type != "PROTOCOL_CLOSED":
            raise ValueError("no approval-ledger event may follow protocol closure")
        return self

    @property
    def next_sequence(self) -> int:
        return len(self.events) + 1

    @property
    def expected_previous_event_sha256(self) -> str | None:
        if not self.events:
            return None
        return canonical_sha256(self.events[-1])

    @property
    def approval_event(self) -> ApprovalLedgerEvent | None:
        return next(
            (event for event in self.events if event.event_type == "A1_APPROVED"),
            None,
        )

    @property
    def closure_event(self) -> ApprovalLedgerEvent | None:
        return next(
            (event for event in self.events if event.event_type == "PROTOCOL_CLOSED"),
            None,
        )

    def append(self, event: ApprovalLedgerEvent) -> ApprovalLedger:
        trusted = ApprovalLedgerEvent.model_validate(event.model_dump(mode="python"))
        for existing in self.events:
            if existing.event_id != trusted.event_id:
                continue
            if canonical_sha256(existing) == canonical_sha256(trusted):
                return self
            raise ShadowContractError(
                "approval-ledger event ID collides with different content"
            )
        if trusted.sequence != self.next_sequence:
            raise ShadowContractError("approval-ledger append sequence mismatch")
        if (
            trusted.previous_event_sha256
            != self.expected_previous_event_sha256
        ):
            raise ShadowContractError("approval-ledger append predecessor mismatch")
        return ApprovalLedger.model_validate(
            {
                **self.model_dump(mode="python"),
                "events": (*self.events, trusted),
            }
        )


class GateMeasurement(_StrictFrozenModel):
    """One exact observed value/threshold pair in a paired decision."""

    gate_id: NonEmptyString
    observed: PrimitiveValue
    threshold: PrimitiveValue
    comparator: NonEmptyString
    unit: str | None = None
    passed: bool | None
    reason_code: NonEmptyString
    source_id: NonEmptyString
    source_definition_sha256: Sha256
    source_as_of: datetime | None = None
    expires_at: datetime | None = None
    is_missing: bool = False

    @field_validator("observed", "threshold")
    @classmethod
    def reject_nonfinite_measurement(cls, value: PrimitiveValue) -> PrimitiveValue:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("gate values must be finite")
        return value

    @field_validator("source_as_of", "expires_at")
    @classmethod
    def require_aware_measurement_time(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("measurement datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_missingness_and_expiry(self) -> GateMeasurement:
        if self.is_missing and self.observed is not None:
            raise ValueError("missing gate measurement cannot have observed value")
        if (
            self.source_as_of is not None
            and self.expires_at is not None
            and self.expires_at <= self.source_as_of
        ):
            raise ValueError("gate expiry must follow source as-of")
        return self


class RecordedTradeGeometry(_StrictFrozenModel):
    """Record-only setup geometry; never an order instruction."""

    entry_low: float = Field(gt=0.0)
    entry_high: float = Field(gt=0.0)
    target_price: float = Field(gt=0.0)
    stop_loss: float = Field(gt=0.0)
    risk_reward_ratio: float = Field(gt=0.0)
    required_risk_reward: float = Field(gt=0.0)
    record_only: Literal[True] = True

    @model_validator(mode="after")
    def verify_geometry(self) -> RecordedTradeGeometry:
        if self.entry_low > self.entry_high:
            raise ValueError("entry_low cannot exceed entry_high")
        if self.stop_loss >= self.entry_low:
            raise ValueError("long setup stop must be below entry range")
        if self.target_price <= self.entry_high:
            raise ValueError("long setup target must be above entry range")
        return self


DecisionRole: TypeAlias = Literal["CONTROL", "CHALLENGER"]
DecisionState: TypeAlias = Literal[
    "ABSTAIN",
    "CONDITIONAL_DEPLOYABLE",
    "DATA_INSUFFICIENT",
    "DEPLOYABLE",
    "NO_TRADE",
    "REJECT",
    "UNKNOWN",
    "WAITLIST",
]


class ShadowDecision(_StrictFrozenModel):
    """One side of a paired decision, expressed in non-live field names."""

    decision_role: DecisionRole
    decision_state: DecisionState
    rating: str | None = None
    would_be_actionable: bool
    would_allocate: bool
    recorded_rank: int | None = Field(default=None, ge=1)
    recorded_position_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    position_size_basis: Literal["NONE", "CONTROL_OBSERVED", "COUNTERFACTUAL"]
    reason_codes: tuple[NonEmptyString, ...] = Field(min_length=1)
    gate_measurements: tuple[GateMeasurement, ...]
    geometry: RecordedTradeGeometry | None = None
    decision_payload_sha256: Sha256

    @model_validator(mode="after")
    def verify_decision(self) -> ShadowDecision:
        if self.would_allocate and not self.would_be_actionable:
            raise ValueError("allocation cannot be recorded for an inactionable side")
        if self.position_size_basis == "NONE":
            if self.recorded_position_fraction is not None or self.would_allocate:
                raise ValueError("NONE size basis cannot carry a position")
        elif self.recorded_position_fraction is None:
            raise ValueError("position size basis requires recorded fraction")
        if self.decision_role == "CHALLENGER" and (
            self.position_size_basis == "CONTROL_OBSERVED"
        ):
            raise ValueError("challenger size cannot be labeled control-observed")
        if self.decision_role == "CONTROL" and (
            self.position_size_basis == "COUNTERFACTUAL"
        ):
            raise ValueError("control size cannot be labeled counterfactual")
        if (
            self.decision_payload_sha256
            != canonical_decision_payload_sha256(self)
        ):
            raise ValueError("decision payload hash mismatch")
        return self


ClusterAssignmentStatus: TypeAlias = Literal[
    "ASSIGNED",
    "NOT_EVALUATED_FOR_INDEPENDENCE",
]


class IndependentClusterMetadata(_StrictFrozenModel):
    """Dependence-cluster assignment for a raw event."""

    assignment_status: ClusterAssignmentStatus
    cluster_id: str | None = None
    cluster_rule_sha256: Sha256
    member_event_ids: tuple[NonEmptyString, ...] = ()
    membership_reasons: tuple[NonEmptyString, ...] = ()
    issuer_group_id: str | None = None
    economic_group_id: str | None = None
    correlation_cluster_id: str | None = None
    systemic_date_block_id: str | None = None
    raw_event_count: int = Field(ge=0)
    effective_n_contribution: float | None = Field(default=None, gt=0.0, le=1.0)
    assigned_at: datetime | None = None
    clustering_inputs_sha256: Sha256 | None = None

    @field_validator("assigned_at")
    @classmethod
    def require_aware_assignment_time(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("cluster assignment time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_assignment(self) -> IndependentClusterMetadata:
        members = tuple(dict.fromkeys(self.member_event_ids))
        if len(members) != len(self.member_event_ids):
            raise ValueError("cluster member event IDs must be unique")
        if self.assignment_status == "ASSIGNED":
            required = (
                self.cluster_id,
                self.assigned_at,
                self.clustering_inputs_sha256,
                self.effective_n_contribution,
            )
            if any(value is None for value in required):
                raise ValueError("assigned cluster is missing required metadata")
            if not self.member_event_ids or not self.membership_reasons:
                raise ValueError("assigned cluster needs members and reasons")
            if self.raw_event_count != len(self.member_event_ids):
                raise ValueError("raw_event_count must equal cluster member count")
        else:
            if any(
                value is not None
                for value in (
                    self.cluster_id,
                    self.assigned_at,
                    self.clustering_inputs_sha256,
                    self.effective_n_contribution,
                )
            ):
                raise ValueError("unevaluated cluster cannot claim an assignment")
            if self.member_event_ids or self.membership_reasons:
                raise ValueError("unevaluated cluster cannot claim membership")
            if self.raw_event_count != 0:
                raise ValueError("unevaluated cluster raw_event_count must be zero")
        return self


DivergenceClassification: TypeAlias = Literal[
    "ABSTENTION_CHANGE",
    "ACTIONABILITY_CHANGE",
    "DECISION_CHANGE",
    "MULTIPLE",
    "NO_CHANGE",
    "RANK_ONLY",
    "REASON_ONLY",
    "SIZE_ONLY",
]


def classify_divergence(
    control: ShadowDecision,
    challenger: ShadowDecision,
) -> DivergenceClassification:
    """Derive the paired divergence class from frozen decisions."""

    control_abstains = control.decision_state in {"ABSTAIN", "DATA_INSUFFICIENT"}
    challenger_abstains = challenger.decision_state in {
        "ABSTAIN",
        "DATA_INSUFFICIENT",
    }
    if control_abstains != challenger_abstains:
        return "ABSTENTION_CHANGE"
    if control.would_be_actionable != challenger.would_be_actionable:
        return "ACTIONABILITY_CHANGE"

    differences: set[str] = set()
    if (control.decision_state, control.rating) != (
        challenger.decision_state,
        challenger.rating,
    ):
        differences.add("DECISION")
    if control.recorded_rank != challenger.recorded_rank:
        differences.add("RANK")
    if (
        control.would_allocate,
        control.recorded_position_fraction,
        control.position_size_basis,
    ) != (
        challenger.would_allocate,
        challenger.recorded_position_fraction,
        challenger.position_size_basis,
    ):
        differences.add("SIZE")
    if (
        control.reason_codes,
        control.gate_measurements,
        control.geometry,
    ) != (
        challenger.reason_codes,
        challenger.gate_measurements,
        challenger.geometry,
    ):
        differences.add("REASON")
    if not differences:
        return "NO_CHANGE"
    if len(differences) > 1:
        return "MULTIPLE"
    return {
        "DECISION": "DECISION_CHANGE",
        "RANK": "RANK_ONLY",
        "REASON": "REASON_ONLY",
        "SIZE": "SIZE_ONLY",
    }[differences.pop()]  # type: ignore[return-value]


class ShadowObservation(_EvaluationOnlyArtifact):
    """One paired control/challenger observation on identical frozen inputs."""

    contract_version: Literal["shadow-observation-v1"] = SHADOW_OBSERVATION_VERSION
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    candidate_set_id: NonEmptyString
    candidate_set_sha256: Sha256
    observation_id: NonEmptyString
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    signal_at: datetime
    as_of_date: date
    captured_at: datetime
    opportunity_set_id: NonEmptyString
    opportunity_set_sha256: Sha256
    snapshot_id: NonEmptyString
    snapshot_sha256: Sha256
    feature_values_sha256: Sha256
    portfolio_state_sha256: Sha256
    cluster_rule_sha256: Sha256
    independent_cluster_id: str | None = None
    cluster: IndependentClusterMetadata
    control_decision: ShadowDecision
    challenger_decision: ShadowDecision
    divergence: DivergenceClassification

    @field_validator("signal_at", "captured_at")
    @classmethod
    def require_aware_observation_times(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("observation datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_pairing(self) -> ShadowObservation:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.control_decision.decision_role != "CONTROL":
            raise ValueError("control_decision must have CONTROL role")
        if self.challenger_decision.decision_role != "CHALLENGER":
            raise ValueError("challenger_decision must have CHALLENGER role")
        if self.as_of_date != self.signal_at.astimezone(IDX_TIMEZONE).date():
            raise ValueError("as_of_date must equal IDX-local signal date")
        if self.captured_at < self.signal_at:
            raise ValueError("captured_at cannot precede signal_at")
        if self.cluster_rule_sha256 != self.cluster.cluster_rule_sha256:
            raise ValueError("observation and cluster rule hashes differ")
        if self.independent_cluster_id != self.cluster.cluster_id:
            raise ValueError("independent cluster identity mismatch")
        if self.cluster.assignment_status == "ASSIGNED" and (
            self.raw_event_id not in self.cluster.member_event_ids
        ):
            raise ValueError("raw event is not a member of its assigned cluster")
        if (
            self.cluster.assigned_at is not None
            and self.cluster.assigned_at > self.captured_at
        ):
            raise ValueError("cluster assignment cannot follow observation capture")
        expected = classify_divergence(
            self.control_decision,
            self.challenger_decision,
        )
        if self.divergence != expected:
            raise ValueError(
                f"divergence must be {expected}, received {self.divergence}"
            )
        return self


OutcomeStatus: TypeAlias = Literal["INVALID", "MATURE", "PENDING"]
FillStatus: TypeAlias = Literal["EXPIRED_UNFILLED", "FILLED", "INVALID", "PENDING"]
TerminalEvent: TypeAlias = Literal[
    "EXCEPTION",
    "INVALID",
    "PENDING",
    "STOP_FIRST",
    "TARGET_FIRST",
    "TIMEOUT",
    "UNFILLED",
]


class ShadowOutcome(_EvaluationOnlyArtifact):
    """One immutable outcome state for one observation and one horizon."""

    contract_version: Literal["shadow-outcome-v1"] = SHADOW_OUTCOME_VERSION
    protocol_id: NonEmptyString
    component_id: ComponentID
    decision_role: DecisionRole
    manifest_sha256: Sha256
    candidate_set_sha256: Sha256
    outcome_id: NonEmptyString
    observation_id: NonEmptyString
    raw_event_id: NonEmptyString
    independent_cluster_id: str | None = None
    ticker: CanonicalTicker
    snapshot_id: NonEmptyString
    snapshot_sha256: Sha256
    trading_calendar_sha256: Sha256
    label_definition_sha256: Sha256
    cost_assumptions_sha256: Sha256
    execution_policy_sha256: Sha256
    horizon_trading_days: Literal[3, 5, 10, 15]
    primary_horizon: bool
    status: OutcomeStatus
    fill_status: FillStatus
    terminal_event: TerminalEvent
    signal_at: datetime
    evaluated_at: datetime
    maturity_at: datetime | None = None
    filled_at: datetime | None = None
    closed_at: datetime | None = None
    planned_geometry_sha256: Sha256
    outcome_source_id: NonEmptyString
    outcome_source_definition_sha256: Sha256
    outcome_source_sha256: Sha256
    previous_outcome_source_sha256: Sha256 | None = None
    outcome_source_as_of: datetime
    outcome_bars_sha256: Sha256
    outcome_bar_record_sha256s: tuple[Sha256, ...]
    corporate_action_policy_sha256: Sha256
    corporate_action_events_sha256: Sha256
    corporate_action_event_ids: tuple[NonEmptyString, ...]
    corporate_action_event_record_sha256s: tuple[Sha256, ...]
    corporate_action_event_published_ats: tuple[datetime, ...]
    bars_observed: int = Field(ge=0)
    fill_price: float | None = Field(default=None, gt=0.0)
    exit_price: float | None = Field(default=None, gt=0.0)
    position_quantity_at_exit: float | None = Field(default=None, gt=0.0)
    invested_capital: float | None = Field(default=None, gt=0.0)
    exit_position_value: float | None = Field(default=None, gt=0.0)
    dividend_cash: float | None = Field(default=None, ge=0.0)
    entry_cost_cash: float | None = Field(default=None, ge=0.0)
    exit_cost_cash: float | None = Field(default=None, ge=0.0)
    total_cost_cash: float | None = Field(default=None, ge=0.0)
    risk_capital_basis: float | None = Field(default=None, gt=0.0)
    capital_return: float | None = None
    dividend_return: float | None = None
    gross_return: float | None = None
    net_return: float | None = None
    net_r: float | None = None
    risk_fraction_at_fill: float | None = Field(default=None, gt=0.0)
    total_cost_fraction: float | None = Field(default=None, ge=0.0)
    fill_time_precision: Literal["SESSION_OPEN", "SESSION_ONLY"] | None = None
    same_bar_ambiguous: bool = False
    ambiguity_resolution: str | None = None
    corporate_action_adjustment: Sha256 | None = None
    reason_codes: tuple[NonEmptyString, ...] = Field(min_length=1)

    @field_validator(
        "signal_at",
        "evaluated_at",
        "maturity_at",
        "filled_at",
        "closed_at",
        "outcome_source_as_of",
    )
    @classmethod
    def require_aware_outcome_times(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("outcome datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_outcome_state(self) -> ShadowOutcome:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.primary_horizon != (self.horizon_trading_days == 15):
            raise ValueError("only the 15-trading-day outcome is primary")
        if self.outcome_id != canonical_outcome_id(
            protocol_id=self.protocol_id,
            manifest_sha256=self.manifest_sha256,
            observation_id=self.observation_id,
            raw_event_id=self.raw_event_id,
            ticker=self.ticker,
            signal_at=self.signal_at,
            decision_role=self.decision_role,
            horizon_trading_days=self.horizon_trading_days,
        ):
            raise ValueError("outcome_id is not the deterministic outcome identity")
        if self.evaluated_at < self.signal_at:
            raise ValueError("outcome evaluation cannot precede signal")
        if self.outcome_source_as_of > self.evaluated_at:
            raise ValueError("outcome source vintage is after evaluation cutoff")
        if (
            self.previous_outcome_source_sha256
            == self.outcome_source_sha256
        ):
            raise ValueError("source-vintage predecessor cannot equal current hash")
        if self.bars_observed > len(self.outcome_bar_record_sha256s):
            raise ValueError("bars_observed exceeds frozen bar evidence")
        event_count = len(self.corporate_action_event_ids)
        if (
            len(self.corporate_action_event_record_sha256s) != event_count
            or len(self.corporate_action_event_published_ats) != event_count
        ):
            raise ValueError("corporate-action evidence tuples differ in length")
        if len(set(self.corporate_action_event_ids)) != event_count:
            raise ValueError("corporate-action event IDs must be unique")
        if any(
            item.utcoffset() is None
            for item in self.corporate_action_event_published_ats
        ):
            raise ValueError(
                "corporate-action publication times must be timezone-aware"
            )
        realized = (
            self.fill_price,
            self.exit_price,
            self.position_quantity_at_exit,
            self.invested_capital,
            self.exit_position_value,
            self.dividend_cash,
            self.entry_cost_cash,
            self.exit_cost_cash,
            self.total_cost_cash,
            self.risk_capital_basis,
            self.capital_return,
            self.dividend_return,
            self.gross_return,
            self.net_return,
            self.net_r,
            self.risk_fraction_at_fill,
            self.total_cost_fraction,
        )
        if self.status == "PENDING":
            if self.terminal_event != "PENDING" or self.fill_status not in {
                "PENDING",
                "FILLED",
            }:
                raise ValueError("pending outcome has an invalid state")
            if self.maturity_at is not None or any(
                value is not None for value in realized[1:]
            ):
                raise ValueError("pending outcome cannot carry terminal values")
            if self.fill_status == "FILLED":
                if (
                    self.fill_price is None
                    or self.filled_at is None
                    or self.fill_time_precision is None
                ):
                    raise ValueError(
                        "pending filled outcome needs fill price/time precision"
                    )
            elif (
                self.fill_price is not None
                or self.filled_at is not None
                or self.fill_time_precision is not None
            ):
                raise ValueError("pending unfilled outcome cannot carry fill metadata")
            if self.closed_at is not None:
                raise ValueError("pending outcome cannot be closed")
        elif self.status == "INVALID":
            if self.terminal_event != "INVALID" or self.fill_status not in {
                "INVALID",
                "FILLED",
            }:
                raise ValueError("invalid outcome has invalid terminal/fill states")
            if self.fill_status == "FILLED":
                if (
                    self.fill_price is None
                    or self.filled_at is None
                    or self.fill_time_precision is None
                ):
                    raise ValueError(
                        "invalid-after-fill outcome must preserve fill evidence"
                    )
                if any(value is not None for value in realized[1:]):
                    raise ValueError(
                        "invalid-after-fill outcome cannot carry realized values"
                    )
            elif any(value is not None for value in realized):
                raise ValueError("invalid pre-fill outcome cannot carry values")
            if self.closed_at is not None:
                raise ValueError("invalid outcome cannot claim a realized close")
        else:
            if self.maturity_at is None or self.terminal_event in {"PENDING", "INVALID"}:
                raise ValueError("mature outcome needs terminal maturity metadata")
            if self.maturity_at < self.signal_at or self.evaluated_at < self.maturity_at:
                raise ValueError("mature outcome timing is inconsistent")
            if self.terminal_event == "UNFILLED":
                if self.fill_status != "EXPIRED_UNFILLED":
                    raise ValueError("unfilled terminal event needs expired-unfilled state")
                if any(value is not None for value in realized):
                    raise ValueError("unfilled outcome cannot carry realized values")
                if (
                    self.filled_at is not None
                    or self.closed_at is not None
                    or self.fill_time_precision is not None
                ):
                    raise ValueError("unfilled outcome cannot carry trade times")
            elif self.terminal_event in {
                "TARGET_FIRST",
                "STOP_FIRST",
                "TIMEOUT",
                "EXCEPTION",
            }:
                if (
                    self.terminal_event == "EXCEPTION"
                    and self.fill_status == "EXPIRED_UNFILLED"
                ):
                    if any(value is not None for value in realized):
                        raise ValueError(
                            "unfilled exception cannot carry realized values"
                        )
                    if self.filled_at is not None or self.closed_at is not None:
                        raise ValueError(
                            "unfilled exception cannot carry trade times"
                        )
                else:
                    if self.fill_status != "FILLED":
                        raise ValueError("realized trade outcome must be filled")
                    if any(value is None for value in realized):
                        raise ValueError(
                            "realized trade outcome needs prices, returns, R, and costs"
                        )
                    if self.filled_at is None or self.closed_at is None:
                        raise ValueError(
                            "realized trade outcome needs fill and close times"
                        )
                    if self.fill_time_precision is None:
                        raise ValueError(
                            "realized trade outcome needs fill-time precision"
                        )
                    if not math.isclose(
                        self.exit_position_value,
                        self.exit_price * self.position_quantity_at_exit,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("exit position-value arithmetic mismatch")
                    if not math.isclose(
                        self.capital_return,
                        (
                            self.exit_position_value - self.invested_capital
                        )
                        / self.invested_capital,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("capital return arithmetic mismatch")
                    if not math.isclose(
                        self.dividend_return,
                        self.dividend_cash / self.invested_capital,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("dividend return arithmetic mismatch")
                    if not math.isclose(
                        self.gross_return,
                        self.capital_return + self.dividend_return,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("gross return arithmetic mismatch")
                    if not math.isclose(
                        self.total_cost_cash,
                        self.entry_cost_cash + self.exit_cost_cash,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("total cost-cash arithmetic mismatch")
                    if not math.isclose(
                        self.total_cost_fraction,
                        self.total_cost_cash / self.invested_capital,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("total cost-fraction arithmetic mismatch")
                    if not math.isclose(
                        self.risk_fraction_at_fill,
                        self.risk_capital_basis / self.invested_capital,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("risk-capital arithmetic mismatch")
                    if not math.isclose(
                        self.net_return,
                        self.gross_return - self.total_cost_fraction,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("net return arithmetic mismatch")
                    if not math.isclose(
                        self.net_r,
                        self.net_return / self.risk_fraction_at_fill,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    ):
                        raise ValueError("net-R arithmetic mismatch")
        if self.filled_at is not None:
            if self.filled_at < self.signal_at or self.filled_at > self.evaluated_at:
                raise ValueError("fill time is outside the observable outcome window")
        if self.closed_at is not None:
            if self.filled_at is None or self.closed_at < self.filled_at:
                raise ValueError("close time cannot precede fill time")
            if self.closed_at > self.evaluated_at:
                raise ValueError("close time cannot follow evaluation time")
        if self.same_bar_ambiguous and not self.ambiguity_resolution:
            raise ValueError("same-bar ambiguity needs an explicit resolution")
        return self


def canonical_decision_payload_sha256(decision: ShadowDecision) -> str:
    """Hash every decision field except its self-referential digest."""

    payload = decision.model_dump(
        mode="python",
        exclude={"decision_payload_sha256"},
    )
    return hashlib.sha256(
        json.dumps(
            _canonicalize_json_value(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def canonical_outcome_id(
    *,
    protocol_id: str,
    manifest_sha256: str,
    observation_id: str,
    raw_event_id: str,
    ticker: str,
    signal_at: datetime,
    decision_role: str,
    horizon_trading_days: int,
) -> str:
    """Derive the stable identity used by outcome creation and backfill."""

    payload = {
        "decision_role": decision_role,
        "horizon_trading_days": horizon_trading_days,
        "manifest_sha256": manifest_sha256,
        "observation_id": observation_id,
        "protocol_id": protocol_id,
        "raw_event_id": raw_event_id,
        "signal_at": _utc_iso(signal_at),
        "ticker": ticker,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return f"OUT-{digest[:40]}"


TrialEventType: TypeAlias = Literal[
    "COMPLETED",
    "DISCARDED",
    "FAILED",
    "REGISTERED",
    "SELECTED",
    "STARTED",
]


class TrialRegistryEvent(_EvaluationOnlyArtifact):
    """One immutable event in a hash-chained trial/attempt registry."""

    contract_version: Literal["shadow-trial-registry-v1"] = TRIAL_REGISTRY_VERSION
    registry_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    sequence: int = Field(ge=1)
    event_id: NonEmptyString
    previous_event_sha256: Sha256 | None = None
    trial_id: NonEmptyString
    attempt_id: NonEmptyString
    event_type: TrialEventType
    recorded_at: datetime
    configuration_sha256: Sha256
    feature_set_sha256: Sha256
    thresholds_sha256: Sha256
    code_sha256: Sha256
    registered_before_outcome_access: Literal[True] = True
    selected_for_prospective_test: bool = False
    reason: str | None = None
    details: tuple[FrozenParameter, ...] = ()

    @field_validator("recorded_at")
    @classmethod
    def require_aware_trial_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("trial event time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_trial_event(self) -> TrialRegistryEvent:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.sequence == 1 and self.previous_event_sha256 is not None:
            raise ValueError("first registry event cannot have a previous hash")
        if self.sequence > 1 and self.previous_event_sha256 is None:
            raise ValueError("non-first registry event needs previous hash")
        if self.event_type in {"FAILED", "DISCARDED"} and not str(
            self.reason or ""
        ).strip():
            raise ValueError("failed/discarded event needs a reason")
        if self.selected_for_prospective_test != (self.event_type == "SELECTED"):
            raise ValueError("selection flag must match SELECTED event type")
        return self


_TRIAL_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"REGISTERED"}),
    "REGISTERED": frozenset({"STARTED", "DISCARDED"}),
    "STARTED": frozenset({"COMPLETED", "FAILED"}),
    "COMPLETED": frozenset({"SELECTED", "DISCARDED"}),
    "FAILED": frozenset(),
    "DISCARDED": frozenset(),
    "SELECTED": frozenset(),
}


class TrialRegistry(_EvaluationOnlyArtifact):
    """Validated append-only view over immutable hash-chained trial events."""

    contract_version: Literal["shadow-trial-registry-v1"] = TRIAL_REGISTRY_VERSION
    registry_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    revision: int = Field(ge=0)
    events: tuple[TrialRegistryEvent, ...] = ()

    @model_validator(mode="after")
    def verify_event_chain(self) -> TrialRegistry:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.revision != len(self.events):
            raise ValueError("registry revision must equal event count")
        event_ids: set[str] = set()
        states: dict[str, str] = {}
        attempts: dict[str, str] = {}
        attempt_fingerprints: dict[str, tuple[str, str, str, str]] = {}
        trial_fingerprints: dict[str, tuple[str, str, str, str]] = {}
        selected = 0
        previous: TrialRegistryEvent | None = None
        for expected_sequence, event in enumerate(self.events, start=1):
            _verify_registry_event_identity(self, event)
            if event.sequence != expected_sequence:
                raise ValueError("registry event sequence is not contiguous")
            if event.event_id in event_ids:
                raise ValueError("registry event IDs must be unique")
            event_ids.add(event.event_id)
            expected_previous = canonical_sha256(previous) if previous else None
            if event.previous_event_sha256 != expected_previous:
                raise ValueError("registry previous-event hash mismatch")
            if previous is not None and event.recorded_at < previous.recorded_at:
                raise ValueError("registry event time must be nondecreasing")
            existing_trial = attempts.get(event.attempt_id)
            if existing_trial is not None and existing_trial != event.trial_id:
                raise ValueError("attempt_id cannot move between trial configurations")
            attempts[event.attempt_id] = event.trial_id
            fingerprint = (
                event.configuration_sha256,
                event.feature_set_sha256,
                event.thresholds_sha256,
                event.code_sha256,
            )
            existing_attempt_fingerprint = attempt_fingerprints.get(event.attempt_id)
            if (
                existing_attempt_fingerprint is not None
                and existing_attempt_fingerprint != fingerprint
            ):
                raise ValueError("attempt fingerprint changed after registration")
            attempt_fingerprints[event.attempt_id] = fingerprint
            existing_trial_fingerprint = trial_fingerprints.get(event.trial_id)
            if (
                existing_trial_fingerprint is not None
                and existing_trial_fingerprint != fingerprint
            ):
                raise ValueError("trial_id cannot identify multiple configurations")
            trial_fingerprints[event.trial_id] = fingerprint
            current = states.get(event.attempt_id)
            if event.event_type not in _TRIAL_TRANSITIONS[current]:
                raise ValueError(
                    f"invalid trial transition {current!r} -> {event.event_type!r}"
                )
            states[event.attempt_id] = event.event_type
            selected += int(event.event_type == "SELECTED")
            previous = event
        if selected > 1:
            raise ValueError("only one attempt may be selected per protocol registry")
        return self

    def append_event(self, event: TrialRegistryEvent) -> TrialRegistry:
        """Return a new registry; never mutate or replace an existing event."""

        for existing in self.events:
            if existing.event_id != event.event_id:
                continue
            if canonical_sha256(existing) == canonical_sha256(event):
                return self
            raise ShadowContractError("event_id collision with different content")
        payload = self.model_dump(mode="python")
        payload["revision"] = self.revision + 1
        payload["events"] = (*self.events, event)
        return TrialRegistry.model_validate(payload)

    @property
    def next_sequence(self) -> int:
        return self.revision + 1

    @property
    def expected_previous_event_sha256(self) -> str | None:
        return canonical_sha256(self.events[-1]) if self.events else None


class EffectiveSampleMetadata(_EvaluationOnlyArtifact):
    """Corpus-level dependence accounting; raw rows never imply independence."""

    contract_version: Literal["shadow-effective-sample-v1"] = (
        EFFECTIVE_SAMPLE_VERSION
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    observation_set_sha256: Sha256
    cluster_rule_sha256: Sha256
    computed_at: datetime
    unit_of_analysis: Literal["INDEPENDENT_CLUSTER"] = "INDEPENDENT_CLUSTER"
    raw_rows_are_independent: Literal[False] = False
    status: Literal["ESTIMABLE", "NOT_ESTIMABLE"]
    raw_n: int = Field(ge=0)
    assigned_raw_n: int = Field(ge=0)
    independent_cluster_n: int = Field(ge=0)
    effective_n: float | None = Field(default=None, gt=0.0)
    cluster_ids: tuple[NonEmptyString, ...] = ()
    unique_tickers: int = Field(ge=0)
    unique_issuers: int = Field(ge=0)
    unique_signal_dates: int = Field(ge=0)
    unique_event_blocks: int = Field(ge=0)
    calculation_rule: NonEmptyString

    @field_validator("computed_at")
    @classmethod
    def require_aware_sample_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("sample metadata time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_counts(self) -> EffectiveSampleMetadata:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if not (
            0 <= self.independent_cluster_n <= self.assigned_raw_n <= self.raw_n
        ):
            raise ValueError("sample counts must satisfy clusters <= assigned <= raw")
        if len(set(self.cluster_ids)) != len(self.cluster_ids):
            raise ValueError("cluster IDs must be unique")
        if len(self.cluster_ids) != self.independent_cluster_n:
            raise ValueError("cluster ID count must equal independent_cluster_n")
        if self.status == "ESTIMABLE":
            if self.effective_n is None:
                raise ValueError("estimable sample needs effective_n")
            if self.independent_cluster_n == 0:
                raise ValueError("estimable sample needs an independent cluster")
            if self.effective_n > self.independent_cluster_n:
                raise ValueError("effective_n cannot exceed independent cluster count")
        elif self.effective_n is not None:
            raise ValueError("NOT_ESTIMABLE must not publish effective_n")
        for value in (
            self.unique_tickers,
            self.unique_issuers,
            self.unique_signal_dates,
            self.unique_event_blocks,
        ):
            if value > self.raw_n:
                raise ValueError("unique counts cannot exceed raw_n")
        return self


def canonical_json_bytes(model: BaseModel) -> bytes:
    """Serialize one contract deterministically for content addressing."""

    payload = model.model_dump(mode="python", by_alias=True, exclude_none=False)
    return json.dumps(
        _canonicalize_json_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(model: BaseModel | None) -> str | None:
    """Return a full lowercase SHA-256 over canonical JSON."""

    if model is None:
        return None
    return hashlib.sha256(canonical_json_bytes(model)).hexdigest()


def _utc_iso(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("canonical datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _canonicalize_json_value(value: object) -> object:
    """Normalize temporal values before deterministic JSON serialization."""

    if isinstance(value, datetime):
        return _utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _canonicalize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_json_value(item) for item in value]
    return value


def _reject_duplicate_hash_paths(
    records: Sequence[ContentHash],
    label: str,
) -> None:
    paths = [record.path for record in records]
    if len(paths) != len(set(paths)):
        raise ValueError(f"{label} content hash paths must be unique")


def _verify_protocol_component(protocol_id: str, component_id: ComponentID) -> None:
    token = {
        "C4a": "C4A",
        "C4b1": "C4B1",
        "C4b2": "C4B2",
    }.get(component_id, component_id.upper())
    if not protocol_id.upper().startswith(f"RS-{token}-"):
        raise ValueError("protocol_id does not match component_id")


def _verify_registry_event_identity(
    registry: TrialRegistry,
    event: TrialRegistryEvent,
) -> None:
    if (
        event.registry_id,
        event.protocol_id,
        event.component_id,
        event.manifest_sha256,
    ) != (
        registry.registry_id,
        registry.protocol_id,
        registry.component_id,
        registry.manifest_sha256,
    ):
        raise ValueError("trial event identity does not match registry")


__all__ = [
    "ApprovalLedger",
    "ApprovalLedgerEvent",
    "ApprovalLedgerEventType",
    "ApprovalLedgerRecordKind",
    "ApprovalRecord",
    "ClusterAssignmentStatus",
    "ClusterRuleDefinition",
    "ClosureMaturationPolicy",
    "ClosureReason",
    "ComponentID",
    "ContentHash",
    "CostAssumptions",
    "DivergenceClassification",
    "DecisionRole",
    "DecisionState",
    "EffectiveSampleMetadata",
    "FeatureDefinition",
    "FillStatus",
    "FrozenParameter",
    "GateMeasurement",
    "GoNoGoRules",
    "GovernanceMode",
    "IndependentClusterMetadata",
    "LabelDefinition",
    "ProtocolClosureRecord",
    "RecordedTradeGeometry",
    "SELF_ADVERSARIAL_PROMPTS",
    "SHADOW_APPROVAL_LEDGER_EVENT_VERSION",
    "SHADOW_APPROVAL_LEDGER_VERSION",
    "SHADOW_APPROVAL_RECORD_VERSION",
    "SHADOW_CANONICALIZATION_VERSION",
    "SHADOW_OBSERVATION_VERSION",
    "SHADOW_OUTCOME_VERSION",
    "SHADOW_PROTOCOL_CLOSURE_VERSION",
    "SHADOW_PROTOCOL_MANIFEST_VERSION",
    "SOLO_A1_COOLING_OFF",
    "SOLO_A1_MIN_COMPLETED_IDX_SESSIONS",
    "SelfAdversarialReviewItem",
    "SelfReviewPromptID",
    "ShadowContractError",
    "ShadowDecision",
    "ShadowObservation",
    "ShadowOutcome",
    "ShadowProtocolManifest",
    "SourceDefinition",
    "TRIAL_REGISTRY_VERSION",
    "TerminalEvent",
    "TrialEventType",
    "TrialRegistry",
    "TrialRegistryEvent",
    "UniverseDefinition",
    "OutcomeStatus",
    "canonical_json_bytes",
    "canonical_rules_sha256",
    "canonical_sha256",
    "classify_divergence",
]
