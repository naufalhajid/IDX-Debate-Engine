"""Immutable, evaluation-only governance for generic shadow protocols.

The local cooling clock is an auditable process control, not an externally
trusted timestamp.  No object in this module can grant live execution, ranking,
or sizing authority.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, model_validator

from .calendar import (
    IDX_TIMEZONE,
    TradingCalendar,
    derive_completed_idx_sessions,
    session_close_at,
)
from .contracts import (
    ApprovalLedger,
    ApprovalLedgerEvent,
    ApprovalRecord,
    ComponentID,
    NonEmptyString,
    ProtocolClosureRecord,
    SHADOW_CANONICALIZATION_VERSION,
    ShadowContractError,
    ShadowObservation,
    ShadowProtocolManifest,
    Sha256,
    _EvaluationOnlyArtifact,
    canonical_json_bytes,
    canonical_sha256,
)
from .portfolio import (
    PORTFOLIO_BINDING_PROFILE,
    FrozenPortfolioPolicy,
    PortfolioArtifactStore,
    PortfolioState,
    PortfolioStateSourceRecord,
    manifest_portfolio_profile,
    verify_portfolio_a1_capability,
)


MANIFEST_REFERENCE_VERSION = "shadow-manifest-reference-v1"
APPROVAL_LEDGER_REFERENCE_VERSION = "shadow-approval-ledger-reference-v1"
APPROVAL_LEDGER_EVENT_REFERENCE_VERSION = (
    "shadow-approval-ledger-event-reference-v1"
)
PROTOCOL_CLOSURE_REFERENCE_VERSION = "shadow-protocol-closure-reference-v1"
PROTOCOL_AUTHORIZATION_BUNDLE_VERSION = "shadow-authorization-bundle-v1"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ManifestArtifactReference(_EvaluationOnlyArtifact):
    """Immutable identity pointer for one exact manifest revision."""

    contract_version: Literal["shadow-manifest-reference-v1"] = (
        MANIFEST_REFERENCE_VERSION
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_revision: int = Field(ge=1)
    manifest_contract_version: Literal["shadow-protocol-manifest-v2"]
    canonicalization_version: Literal["shadow-canonical-json-v1"] = (
        SHADOW_CANONICALIZATION_VERSION
    )
    canonical_sha256: Sha256
    raw_file_sha256: Sha256
    raw_byte_length: int = Field(gt=0)
    methodology_document_path: NonEmptyString
    methodology_document_sha256: Sha256
    manifest_relative_path: NonEmptyString
    methodology_relative_path: NonEmptyString


class ApprovalLedgerReference(_EvaluationOnlyArtifact):
    """Immutable one-ledger claim for one exact manifest byte identity."""

    contract_version: Literal["shadow-approval-ledger-reference-v1"] = (
        APPROVAL_LEDGER_REFERENCE_VERSION
    )
    ledger_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_revision: int = Field(ge=1)
    draft_manifest_canonical_sha256: Sha256
    draft_manifest_raw_file_sha256: Sha256
    draft_manifest_raw_byte_length: int = Field(gt=0)


class ApprovalLedgerEventReference(_EvaluationOnlyArtifact):
    """Stable exclusive sequence claim pointing to one content-addressed event."""

    contract_version: Literal[
        "shadow-approval-ledger-event-reference-v1"
    ] = APPROVAL_LEDGER_EVENT_REFERENCE_VERSION
    ledger_id: NonEmptyString
    sequence: int = Field(ge=1)
    event_canonical_sha256: Sha256
    event_raw_file_sha256: Sha256
    event_raw_byte_length: int = Field(gt=0)


class ProtocolClosureReference(_EvaluationOnlyArtifact):
    """Immutable manifest-level anchor proving that closure was initiated."""

    contract_version: Literal["shadow-protocol-closure-reference-v1"] = (
        PROTOCOL_CLOSURE_REFERENCE_VERSION
    )
    ledger_id: NonEmptyString
    closure_id: NonEmptyString
    closure_canonical_sha256: Sha256
    closure_raw_file_sha256: Sha256
    closure_raw_byte_length: int = Field(gt=0)


class ProtocolAuthorizationBundle(_EvaluationOnlyArtifact):
    """Exact artifacts required to prove A1 and the current closure state."""

    contract_version: Literal["shadow-authorization-bundle-v1"] = (
        PROTOCOL_AUTHORIZATION_BUNDLE_VERSION
    )
    manifest: ShadowProtocolManifest
    manifest_raw_file_bytes: bytes = Field(repr=False)
    methodology_document_bytes: bytes = Field(repr=False)
    approval: ApprovalRecord
    approval_raw_file_bytes: bytes = Field(repr=False)
    approval_ledger: ApprovalLedger
    trading_calendar: TradingCalendar | None
    closure: ProtocolClosureRecord | None = None
    closure_raw_file_bytes: bytes | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def verify_bundle(self) -> ProtocolAuthorizationBundle:
        verify_approval_binding(
            manifest=self.manifest,
            manifest_raw_file_bytes=self.manifest_raw_file_bytes,
            methodology_document_bytes=self.methodology_document_bytes,
            approval=self.approval,
            approval_raw_file_bytes=self.approval_raw_file_bytes,
            approval_ledger=self.approval_ledger,
            trading_calendar=self.trading_calendar,
            closure=self.closure,
            closure_raw_file_bytes=self.closure_raw_file_bytes,
        )
        return self


def load_shadow_protocol_manifest_v2(raw_file_bytes: bytes) -> ShadowProtocolManifest:
    """Load only manifest v2; v1 is audit-only and never gains A1 authority."""

    payload = _strict_json_object(raw_file_bytes, label="shadow manifest")
    version = payload.get("contract_version")
    if version == "shadow-protocol-manifest-v1":
        raise ShadowContractError(
            "shadow-protocol-manifest-v1 is audit-only and cannot authorize "
            "collection; explicit migration to v2 is required"
        )
    if version != "shadow-protocol-manifest-v2":
        raise ShadowContractError(
            f"unsupported shadow manifest contract version: {version!r}"
        )
    return _validate_payload(ShadowProtocolManifest, payload, "shadow manifest")


def load_approval_record_v1(raw_file_bytes: bytes) -> ApprovalRecord:
    payload = _strict_json_object(raw_file_bytes, label="approval record")
    version = payload.get("contract_version")
    if version != "shadow-approval-record-v1":
        raise ShadowContractError(
            f"unsupported approval record contract version: {version!r}"
        )
    return _validate_payload(ApprovalRecord, payload, "approval record")


def load_protocol_closure_v1(raw_file_bytes: bytes) -> ProtocolClosureRecord:
    payload = _strict_json_object(raw_file_bytes, label="closure record")
    version = payload.get("contract_version")
    if version != "shadow-protocol-closure-v1":
        raise ShadowContractError(
            f"unsupported closure record contract version: {version!r}"
        )
    return _validate_payload(ProtocolClosureRecord, payload, "closure record")


def load_approval_ledger_event_v1(raw_file_bytes: bytes) -> ApprovalLedgerEvent:
    payload = _strict_json_object(raw_file_bytes, label="approval-ledger event")
    version = payload.get("contract_version")
    if version != "shadow-approval-ledger-event-v1":
        raise ShadowContractError(
            f"unsupported approval-ledger event version: {version!r}"
        )
    return _validate_payload(
        ApprovalLedgerEvent,
        payload,
        "approval-ledger event",
    )


def verify_approval_binding(
    *,
    manifest: ShadowProtocolManifest,
    manifest_raw_file_bytes: bytes,
    methodology_document_bytes: bytes,
    approval: ApprovalRecord,
    approval_raw_file_bytes: bytes,
    approval_ledger: ApprovalLedger,
    trading_calendar: TradingCalendar | None,
    closure: ProtocolClosureRecord | None = None,
    closure_raw_file_bytes: bytes | None = None,
) -> None:
    """Verify A1 against exact bytes, trusted calendar, and append-only ledger."""

    trusted_manifest = load_shadow_protocol_manifest_v2(manifest_raw_file_bytes)
    trusted_approval = load_approval_record_v1(approval_raw_file_bytes)
    _require_same_model("manifest", manifest, trusted_manifest)
    _require_same_model("approval", approval, trusted_approval)

    manifest_raw_sha256 = _sha256(manifest_raw_file_bytes)
    manifest_canonical_sha256 = _required_canonical_sha256(trusted_manifest)
    if (
        manifest_raw_sha256,
        len(manifest_raw_file_bytes),
        manifest_canonical_sha256,
    ) != (
        trusted_approval.draft_manifest_raw_file_sha256,
        trusted_approval.draft_manifest_raw_byte_length,
        trusted_approval.draft_manifest_canonical_sha256,
    ):
        raise ShadowContractError("manifest hash/length differs from A1 approval")
    _verify_methodology_document(
        methodology_document_bytes,
        trusted_manifest.methodology_document_sha256,
    )

    if trading_calendar is None:
        raise ShadowContractError("trusted trading calendar is unavailable")
    try:
        trusted_calendar = TradingCalendar.model_validate(
            trading_calendar.model_dump(mode="python")
        )
    except (AttributeError, ValueError) as exc:
        raise ShadowContractError("trusted trading calendar is invalid") from exc
    if (
        trusted_calendar.contract_version,
        trusted_calendar.calendar_id,
        trusted_calendar.calendar_sha256,
    ) != (
        trusted_approval.trading_calendar_contract_version,
        trusted_approval.trading_calendar_id,
        trusted_approval.trading_calendar_sha256,
    ):
        raise ShadowContractError("trading calendar differs from A1 approval")
    if (
        trusted_calendar.calendar_id,
        trusted_calendar.calendar_sha256,
    ) != (
        trusted_manifest.trading_calendar_id,
        trusted_manifest.trading_calendar_sha256,
    ):
        raise ShadowContractError("trading calendar differs from manifest")
    if trusted_manifest.fixed_terminal_date not in trusted_calendar.sessions:
        raise ShadowContractError(
            "fixed terminal date is absent from the frozen trading calendar"
        )
    derived_sessions = derive_completed_idx_sessions(
        trusted_calendar,
        draft_frozen_at=trusted_manifest.draft_frozen_at,
        decided_at=trusted_approval.decided_at,
    )
    if trusted_approval.completed_idx_trading_sessions != derived_sessions:
        raise ShadowContractError(
            "approval sessions differ from the trusted calendar derivation"
        )

    if trusted_approval.decided_at > (
        trusted_manifest.collection_start_not_before
    ):
        raise ShadowContractError("collection window begins before A1 approval")
    if (
        trusted_approval.protocol_id,
        trusted_approval.component_id,
        trusted_approval.manifest_contract_version,
        trusted_approval.manifest_revision,
        trusted_approval.draft_frozen_at,
        trusted_approval.owner,
        trusted_approval.governance_mode,
        trusted_approval.independent_reviewer,
    ) != (
        trusted_manifest.protocol_id,
        trusted_manifest.component_id,
        trusted_manifest.contract_version,
        trusted_manifest.manifest_revision,
        trusted_manifest.draft_frozen_at,
        trusted_manifest.owner,
        trusted_manifest.governance_mode,
        trusted_manifest.independent_reviewer,
    ):
        raise ShadowContractError("A1 identity tuple differs from manifest")

    trusted_ledger = ApprovalLedger.model_validate(
        approval_ledger.model_dump(mode="python")
    )
    if (
        trusted_ledger.ledger_id,
        trusted_ledger.protocol_id,
        trusted_ledger.component_id,
        trusted_ledger.manifest_revision,
        trusted_ledger.draft_manifest_canonical_sha256,
        trusted_ledger.draft_manifest_raw_file_sha256,
        trusted_ledger.draft_manifest_raw_byte_length,
    ) != (
        trusted_approval.approval_ledger_id,
        trusted_manifest.protocol_id,
        trusted_manifest.component_id,
        trusted_manifest.manifest_revision,
        manifest_canonical_sha256,
        manifest_raw_sha256,
        len(manifest_raw_file_bytes),
    ):
        raise ShadowContractError("approval-ledger identity differs from manifest")
    approval_event = trusted_ledger.approval_event
    if approval_event is None:
        raise ShadowContractError("ApprovalRecord has no append-only ledger event")
    approval_canonical_sha256 = _required_canonical_sha256(trusted_approval)
    if (
        approval_event.event_id,
        approval_event.record_id,
        approval_event.record_contract_version,
        approval_event.record_canonical_sha256,
        approval_event.record_raw_file_sha256,
        approval_event.record_raw_byte_length,
        approval_event.recorded_at,
    ) != (
        f"{trusted_approval.approval_id}-LEDGER",
        trusted_approval.approval_id,
        trusted_approval.contract_version,
        approval_canonical_sha256,
        _sha256(approval_raw_file_bytes),
        len(approval_raw_file_bytes),
        trusted_approval.decided_at,
    ):
        raise ShadowContractError("approval-ledger event differs from ApprovalRecord")

    closure_event = trusted_ledger.closure_event
    if closure_event is None:
        if closure is not None or closure_raw_file_bytes is not None:
            raise ShadowContractError("closure record has no ledger event")
        return
    if closure is None or closure_raw_file_bytes is None:
        raise ShadowContractError(
            "closure ledger event exists but its record is unavailable"
        )
    trusted_closure = load_protocol_closure_v1(closure_raw_file_bytes)
    _require_same_model("closure", closure, trusted_closure)
    if (
        closure_event.event_id,
        closure_event.record_id,
        closure_event.record_contract_version,
        closure_event.record_canonical_sha256,
        closure_event.record_raw_file_sha256,
        closure_event.record_raw_byte_length,
        closure_event.recorded_at,
    ) != (
        f"{trusted_closure.closure_id}-LEDGER",
        trusted_closure.closure_id,
        trusted_closure.contract_version,
        _required_canonical_sha256(trusted_closure),
        _sha256(closure_raw_file_bytes),
        len(closure_raw_file_bytes),
        trusted_closure.recorded_at,
    ):
        raise ShadowContractError("closure-ledger event differs from ClosureRecord")
    if (
        trusted_closure.approval_ledger_id,
        trusted_closure.protocol_id,
        trusted_closure.component_id,
        trusted_closure.manifest_contract_version,
        trusted_closure.manifest_revision,
        trusted_closure.draft_manifest_canonical_sha256,
        trusted_closure.draft_manifest_raw_file_sha256,
        trusted_closure.draft_manifest_raw_byte_length,
        trusted_closure.approval_id,
        trusted_closure.approval_record_canonical_sha256,
        trusted_closure.governance_mode,
    ) != (
        trusted_ledger.ledger_id,
        trusted_manifest.protocol_id,
        trusted_manifest.component_id,
        trusted_manifest.contract_version,
        trusted_manifest.manifest_revision,
        manifest_canonical_sha256,
        manifest_raw_sha256,
        len(manifest_raw_file_bytes),
        trusted_approval.approval_id,
        approval_canonical_sha256,
        trusted_manifest.governance_mode,
    ):
        raise ShadowContractError("ClosureRecord identity differs from A1")
    if trusted_closure.effective_at < trusted_approval.decided_at:
        raise ShadowContractError("protocol closure precedes A1 approval")
    if (
        trusted_closure.reason_code == "FIXED_TERMINAL_REACHED"
        and trusted_closure.effective_at
        != session_close_at(trusted_manifest.fixed_terminal_date)
    ):
        raise ShadowContractError(
            "fixed-terminal closure time differs from frozen calendar close"
        )
    if (
        trusted_manifest.governance_mode == "SOLO_SELF_REVIEW"
        and trusted_closure.closed_by.casefold()
        != trusted_manifest.owner.casefold()
    ):
        raise ShadowContractError("solo closure must be recorded by the owner")


def verify_observation_collection_authorization(
    authorization: ProtocolAuthorizationBundle,
    observation: ShadowObservation,
    *,
    attempted_at: datetime,
) -> ShadowObservation:
    """Fail closed unless a new observation is currently allowed."""

    if attempted_at.utcoffset() is None:
        raise ShadowContractError("collection attempt time must be timezone-aware")
    trusted = _revalidate_bundle(authorization)
    trusted_observation = ShadowObservation.model_validate(
        observation.model_dump(mode="python")
    )
    if trusted.approval_ledger.closure_event is not None:
        raise ShadowContractError("protocol is closed to new observations")
    if attempted_at < trusted.approval.decided_at:
        raise ShadowContractError("collection attempt precedes A1 decision")
    if attempted_at < trusted.manifest.collection_start_not_before:
        raise ShadowContractError("collection attempt precedes collection window")
    if trusted_observation.signal_at < trusted.approval.decided_at:
        raise ShadowContractError("observation signal predates A1 decision")
    if (
        trusted_observation.signal_at
        < trusted.manifest.collection_start_not_before
    ):
        raise ShadowContractError("observation signal predates collection window")
    if trusted_observation.captured_at > attempted_at:
        raise ShadowContractError("observation capture follows collection attempt")
    terminal_close = session_close_at(trusted.manifest.fixed_terminal_date)
    if (
        attempted_at > terminal_close
        or trusted_observation.captured_at > terminal_close
    ):
        raise ShadowContractError(
            "observation collection is after fixed terminal date"
        )
    signal_date = trusted_observation.signal_at.astimezone(IDX_TIMEZONE).date()
    if signal_date >= trusted.manifest.fixed_terminal_date:
        raise ShadowContractError(
            "observation signal leaves no post-signal terminal runway"
        )
    if trusted.trading_calendar is None:
        raise ShadowContractError("trusted trading calendar is unavailable")
    if signal_date not in trusted.trading_calendar.sessions:
        raise ShadowContractError(
            "observation signal date is not a frozen IDX session"
        )
    post_signal_sessions = tuple(
        session
        for session in trusted.trading_calendar.sessions
        if signal_date < session <= trusted.manifest.fixed_terminal_date
    )
    required_sessions = (
        trusted.manifest.labels.entry_validity_trading_days + 15
    )
    if len(post_signal_sessions) < required_sessions:
        raise ShadowContractError(
            "fixed terminal lacks entry-validity plus 15-session runway"
        )
    if (
        trusted_observation.protocol_id,
        trusted_observation.component_id,
        trusted_observation.manifest_sha256,
    ) != (
        trusted.manifest.protocol_id,
        trusted.manifest.component_id,
        _required_canonical_sha256(trusted.manifest),
    ):
        raise ShadowContractError("observation identity differs from approved manifest")
    return trusted_observation


def verify_maturation_authorization(
    authorization: ProtocolAuthorizationBundle,
    observation: ShadowObservation,
) -> ShadowObservation:
    """Prove that this exact observation entered before any terminal closure."""

    trusted = _revalidate_bundle(authorization)
    trusted_observation = ShadowObservation.model_validate(
        observation.model_dump(mode="python")
    )
    if (
        trusted_observation.protocol_id,
        trusted_observation.component_id,
        trusted_observation.manifest_sha256,
    ) != (
        trusted.manifest.protocol_id,
        trusted.manifest.component_id,
        _required_canonical_sha256(trusted.manifest),
    ):
        raise ShadowContractError("observation identity differs from approved manifest")
    if trusted_observation.signal_at < trusted.approval.decided_at:
        raise ShadowContractError("authorized observation predates A1 decision")
    if (
        trusted_observation.signal_at
        < trusted.manifest.collection_start_not_before
    ):
        raise ShadowContractError("authorized observation predates collection window")
    signal_date = trusted_observation.signal_at.astimezone(IDX_TIMEZONE).date()
    if signal_date >= trusted.manifest.fixed_terminal_date:
        raise ShadowContractError(
            "authorized observation leaves no post-signal terminal runway"
        )
    if trusted.trading_calendar is None:
        raise ShadowContractError("trusted trading calendar is unavailable")
    if signal_date not in trusted.trading_calendar.sessions:
        raise ShadowContractError(
            "authorized signal date is not a frozen IDX session"
        )
    post_signal_sessions = tuple(
        session
        for session in trusted.trading_calendar.sessions
        if signal_date < session <= trusted.manifest.fixed_terminal_date
    )
    if len(post_signal_sessions) < (
        trusted.manifest.labels.entry_validity_trading_days + 15
    ):
        raise ShadowContractError(
            "authorized observation lacks frozen terminal runway"
        )
    observation_bytes = canonical_json_bytes(trusted_observation)
    observation_hash = _sha256(observation_bytes)
    matches = tuple(
        event
        for event in trusted.approval_ledger.events
        if (
            event.event_type == "OBSERVATION_AUTHORIZED"
            and event.record_id == trusted_observation.observation_id
        )
    )
    if len(matches) != 1:
        raise ShadowContractError(
            "observation lacks one exact collection-authorization event"
        )
    observation_event = matches[0]
    if (
        observation_event.event_id,
        observation_event.record_contract_version,
        observation_event.record_canonical_sha256,
        observation_event.record_raw_file_sha256,
        observation_event.record_raw_byte_length,
    ) != (
        f"{trusted_observation.observation_id}-COLLECTION",
        trusted_observation.contract_version,
        observation_hash,
        observation_hash,
        len(observation_bytes),
    ):
        raise ShadowContractError(
            "collection-authorization event differs from observation"
        )
    if observation_event.recorded_at < trusted_observation.captured_at:
        raise ShadowContractError(
            "collection-authorization event predates observation capture"
        )
    if observation_event.recorded_at > session_close_at(
        trusted.manifest.fixed_terminal_date
    ):
        raise ShadowContractError(
            "collection authorization is after fixed terminal"
        )
    closure_event = trusted.approval_ledger.closure_event
    if closure_event is None:
        return trusted_observation
    if observation_event.sequence >= closure_event.sequence:
        raise ShadowContractError("observation was not authorized before closure")
    if trusted.closure is None:
        raise ShadowContractError("closure state is unavailable")
    if trusted.closure.maturation_policy == "BLOCK_ALL_MATURATION":
        raise ShadowContractError("protocol closure blocks all maturation")
    if (
        trusted_observation.captured_at >= trusted.closure.effective_at
        or observation_event.recorded_at >= trusted.closure.effective_at
    ):
        raise ShadowContractError(
            "observation authorization is not pre-closure"
        )
    return trusted_observation


class ProtocolGovernanceStore:
    """Exclusive-create content store plus an append-only A1 lifecycle ledger."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def persist_manifest(
        self,
        manifest_raw_file_bytes: bytes,
        methodology_document_bytes: bytes,
    ) -> Path:
        manifest = load_shadow_protocol_manifest_v2(manifest_raw_file_bytes)
        _verify_methodology_document(
            methodology_document_bytes,
            manifest.methodology_document_sha256,
        )
        canonical_hash = _required_canonical_sha256(manifest)
        raw_hash = _sha256(manifest_raw_file_bytes)
        protocol_root = self._protocol_root(manifest.protocol_id, canonical_hash)
        manifest_path = (
            protocol_root / "manifests" / raw_hash / "manifest.json"
        )
        methodology_path = (
            self.root
            / "methodology"
            / manifest.methodology_document_sha256
            / "document.bin"
        )
        self._exclusive_create(manifest_path, manifest_raw_file_bytes)
        self._exclusive_create(methodology_path, methodology_document_bytes)
        reference = ManifestArtifactReference(
            protocol_id=manifest.protocol_id,
            component_id=manifest.component_id,
            manifest_revision=manifest.manifest_revision,
            manifest_contract_version=manifest.contract_version,
            canonical_sha256=canonical_hash,
            raw_file_sha256=raw_hash,
            raw_byte_length=len(manifest_raw_file_bytes),
            methodology_document_path=manifest.methodology_document_path,
            methodology_document_sha256=(
                manifest.methodology_document_sha256
            ),
            manifest_relative_path=self._relative(manifest_path),
            methodology_relative_path=self._relative(methodology_path),
        )
        reference_path = self._manifest_reference_path(
            manifest.protocol_id,
            manifest.manifest_revision,
        )
        self._exclusive_create(reference_path, canonical_json_bytes(reference))
        return manifest_path

    def persist_trading_calendar(self, calendar: TradingCalendar) -> Path:
        trusted = TradingCalendar.model_validate(calendar.model_dump(mode="python"))
        path = (
            self.root
            / "trading_calendars"
            / trusted.calendar_sha256
            / "calendar.json"
        )
        return self._exclusive_create(path, canonical_json_bytes(trusted))

    def persist_portfolio_policy(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        """Persist a profile-bound CONFIG without granting A1."""

        return PortfolioArtifactStore(self.root).persist_policy(
            manifest,
            raw_file_bytes,
        )

    def persist_portfolio_state_source(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        """Persist exact point-in-time portfolio source evidence."""

        return PortfolioArtifactStore(self.root).persist_source_record(
            manifest,
            raw_file_bytes,
        )

    def persist_portfolio_state(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        """Persist one exact read-only state after its dependencies."""

        return PortfolioArtifactStore(self.root).persist_state(
            manifest,
            raw_file_bytes,
        )

    def append_approval(self, approval_raw_file_bytes: bytes) -> ApprovalLedger:
        approval = load_approval_record_v1(approval_raw_file_bytes)
        manifest, manifest_raw, methodology = self._load_manifest_for_approval(
            approval
        )
        calendar = self.load_trading_calendar(
            approval.trading_calendar_sha256
        )
        empty_ledger = ApprovalLedger(
            ledger_id=approval.approval_ledger_id,
            protocol_id=approval.protocol_id,
            component_id=approval.component_id,
            manifest_revision=approval.manifest_revision,
            draft_manifest_canonical_sha256=(
                approval.draft_manifest_canonical_sha256
            ),
            draft_manifest_raw_file_sha256=(
                approval.draft_manifest_raw_file_sha256
            ),
            draft_manifest_raw_byte_length=(
                approval.draft_manifest_raw_byte_length
            ),
        )
        _verify_approval_without_ledger(
            manifest=manifest,
            manifest_raw_file_bytes=manifest_raw,
            methodology_document_bytes=methodology,
            approval=approval,
            approval_raw_file_bytes=approval_raw_file_bytes,
            trading_calendar=calendar,
        )
        self._verify_portfolio_binding_if_declared(
            manifest,
            require_a1_capability=True,
        )
        ledger_reference = ApprovalLedgerReference(
            ledger_id=approval.approval_ledger_id,
            protocol_id=approval.protocol_id,
            component_id=approval.component_id,
            manifest_revision=approval.manifest_revision,
            draft_manifest_canonical_sha256=(
                approval.draft_manifest_canonical_sha256
            ),
            draft_manifest_raw_file_sha256=(
                approval.draft_manifest_raw_file_sha256
            ),
            draft_manifest_raw_byte_length=(
                approval.draft_manifest_raw_byte_length
            ),
        )
        self._exclusive_create(
            self._ledger_reference_path(
                approval.protocol_id,
                approval.draft_manifest_canonical_sha256,
            ),
            canonical_json_bytes(ledger_reference),
        )
        try:
            existing = self.load_approval_ledger(
                protocol_id=approval.protocol_id,
                manifest_canonical_sha256=(
                    approval.draft_manifest_canonical_sha256
                ),
                ledger_id=approval.approval_ledger_id,
            )
        except ShadowContractError as exc:
            if "ledger event directory is unavailable" not in str(exc):
                raise
            existing = empty_ledger
        approval_canonical_hash = _required_canonical_sha256(approval)
        approval_raw_hash = _sha256(approval_raw_file_bytes)
        if existing.events:
            approval_event = existing.approval_event
            if (
                approval_event is not None
                and (
                    approval_event.event_id,
                    approval_event.record_id,
                    approval_event.record_contract_version,
                    approval_event.record_canonical_sha256,
                    approval_event.record_raw_file_sha256,
                    approval_event.record_raw_byte_length,
                    approval_event.recorded_at,
                )
                == (
                    f"{approval.approval_id}-LEDGER",
                    approval.approval_id,
                    approval.contract_version,
                    approval_canonical_hash,
                    approval_raw_hash,
                    len(approval_raw_file_bytes),
                    approval.decided_at,
                )
            ):
                return existing
            raise ShadowContractError("A1 approval ledger is already initialized")

        approval_path = self._record_path(
            approval.protocol_id,
            approval.draft_manifest_canonical_sha256,
            "approvals",
            approval_canonical_hash,
            approval_raw_hash,
        )
        self._exclusive_create(approval_path, approval_raw_file_bytes)
        event = ApprovalLedgerEvent(
            ledger_id=approval.approval_ledger_id,
            protocol_id=approval.protocol_id,
            component_id=approval.component_id,
            manifest_revision=approval.manifest_revision,
            draft_manifest_canonical_sha256=(
                approval.draft_manifest_canonical_sha256
            ),
            draft_manifest_raw_file_sha256=(
                approval.draft_manifest_raw_file_sha256
            ),
            draft_manifest_raw_byte_length=(
                approval.draft_manifest_raw_byte_length
            ),
            sequence=1,
            event_id=f"{approval.approval_id}-LEDGER",
            previous_event_sha256=None,
            event_type="A1_APPROVED",
            record_kind="APPROVAL",
            record_id=approval.approval_id,
            record_contract_version=approval.contract_version,
            record_canonical_sha256=approval_canonical_hash,
            record_raw_file_sha256=approval_raw_hash,
            record_raw_byte_length=len(approval_raw_file_bytes),
            recorded_at=approval.decided_at,
        )
        _validate_prospective_ledger_event(existing, event)
        self._append_event(event)
        return self.load_approval_ledger(
            protocol_id=approval.protocol_id,
            manifest_canonical_sha256=(
                approval.draft_manifest_canonical_sha256
            ),
            ledger_id=approval.approval_ledger_id,
        )

    def append_closure(
        self,
        closure_raw_file_bytes: bytes,
    ) -> ApprovalLedger:
        closure = load_protocol_closure_v1(closure_raw_file_bytes)
        ledger = self.load_approval_ledger(
            protocol_id=closure.protocol_id,
            manifest_canonical_sha256=(
                closure.draft_manifest_canonical_sha256
            ),
            ledger_id=closure.approval_ledger_id,
        )
        if ledger.closure_event is not None:
            event = ledger.closure_event
            if (
                event.event_id,
                event.record_id,
                event.record_contract_version,
                event.record_canonical_sha256,
                event.record_raw_file_sha256,
                event.record_raw_byte_length,
                event.recorded_at,
            ) != (
                f"{closure.closure_id}-LEDGER",
                closure.closure_id,
                closure.contract_version,
                _required_canonical_sha256(closure),
                _sha256(closure_raw_file_bytes),
                len(closure_raw_file_bytes),
                closure.recorded_at,
            ):
                raise ShadowContractError("different closure already recorded")
            self._claim_closure_reference(
                closure,
                closure_raw_file_bytes,
            )
            path = self._record_path(
                closure.protocol_id,
                closure.draft_manifest_canonical_sha256,
                "closures",
                event.record_canonical_sha256,
                event.record_raw_file_sha256,
            )
            self._exclusive_create(path, closure_raw_file_bytes)
            return self.load_approval_ledger(
                protocol_id=closure.protocol_id,
                manifest_canonical_sha256=(
                    closure.draft_manifest_canonical_sha256
                ),
                ledger_id=closure.approval_ledger_id,
            )

        expected_closure_reference = self._build_closure_reference(
            closure,
            closure_raw_file_bytes,
        )
        closure_reference_path = self._closure_reference_path(
            closure.protocol_id,
            closure.draft_manifest_canonical_sha256,
        )
        if closure_reference_path.is_file():
            authorization = self._load_authorization(
                protocol_id=closure.protocol_id,
                manifest_canonical_sha256=(
                    closure.draft_manifest_canonical_sha256
                ),
                ledger_id=closure.approval_ledger_id,
                expected_pending_closure_reference=(
                    expected_closure_reference
                ),
            )
        else:
            authorization = self.load_authorization(
                protocol_id=closure.protocol_id,
                manifest_canonical_sha256=(
                    closure.draft_manifest_canonical_sha256
                ),
                ledger_id=closure.approval_ledger_id,
            )
        _verify_closure_identity(authorization, closure)
        closure_canonical_hash = _required_canonical_sha256(closure)
        closure_raw_hash = _sha256(closure_raw_file_bytes)
        event = ApprovalLedgerEvent(
            ledger_id=closure.approval_ledger_id,
            protocol_id=closure.protocol_id,
            component_id=closure.component_id,
            manifest_revision=closure.manifest_revision,
            draft_manifest_canonical_sha256=(
                closure.draft_manifest_canonical_sha256
            ),
            draft_manifest_raw_file_sha256=(
                closure.draft_manifest_raw_file_sha256
            ),
            draft_manifest_raw_byte_length=(
                closure.draft_manifest_raw_byte_length
            ),
            sequence=ledger.next_sequence,
            event_id=f"{closure.closure_id}-LEDGER",
            previous_event_sha256=ledger.expected_previous_event_sha256,
            event_type="PROTOCOL_CLOSED",
            record_kind="CLOSURE",
            record_id=closure.closure_id,
            record_contract_version=closure.contract_version,
            record_canonical_sha256=closure_canonical_hash,
            record_raw_file_sha256=closure_raw_hash,
            record_raw_byte_length=len(closure_raw_file_bytes),
            recorded_at=closure.recorded_at,
        )
        _validate_prospective_ledger_event(ledger, event)
        self._claim_closure_reference(
            closure,
            closure_raw_file_bytes,
        )
        # The manifest-level closure reference is claimed before the terminal
        # event. A crash or competing writer therefore leaves collection
        # fail-closed until this exact closure is retried and completed.
        self._append_event(event)
        closure_path = self._record_path(
            closure.protocol_id,
            closure.draft_manifest_canonical_sha256,
            "closures",
            closure_canonical_hash,
            closure_raw_hash,
        )
        self._exclusive_create(closure_path, closure_raw_file_bytes)
        return self.load_approval_ledger(
            protocol_id=closure.protocol_id,
            manifest_canonical_sha256=(
                closure.draft_manifest_canonical_sha256
            ),
            ledger_id=closure.approval_ledger_id,
        )

    def persist_observation(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
        observation: ShadowObservation,
        attempted_at: datetime,
    ) -> Path:
        authorization = self.load_authorization(
            protocol_id=protocol_id,
            manifest_canonical_sha256=manifest_canonical_sha256,
            ledger_id=ledger_id,
        )
        trusted_candidate = ShadowObservation.model_validate(
            observation.model_dump(mode="python")
        )
        if (
            manifest_portfolio_profile(authorization.manifest)
            == PORTFOLIO_BINDING_PROFILE
        ):
            PortfolioArtifactStore(self.root).verify_observation_state(
                authorization.manifest,
                trusted_candidate,
            )
        observation_bytes = canonical_json_bytes(trusted_candidate)
        observation_hash = _sha256(observation_bytes)
        observation_path = self._record_path(
            protocol_id,
            manifest_canonical_sha256,
            "observations",
            observation_hash,
            observation_hash,
        )
        existing_events = tuple(
            event
            for event in authorization.approval_ledger.events
            if (
                event.event_type == "OBSERVATION_AUTHORIZED"
                and event.record_id == trusted_candidate.observation_id
            )
        )
        if existing_events:
            existing = existing_events[0]
            if (
                existing.event_id,
                existing.record_contract_version,
                existing.record_canonical_sha256,
                existing.record_raw_file_sha256,
                existing.record_raw_byte_length,
            ) != (
                f"{trusted_candidate.observation_id}-COLLECTION",
                trusted_candidate.contract_version,
                observation_hash,
                observation_hash,
                len(observation_bytes),
            ):
                raise ShadowContractError(
                    "observation ID is already bound to different content"
                )
            if self._read_exact(
                observation_path,
                "authorized observation",
            ) != observation_bytes:
                raise ShadowContractError(
                    "authorized observation bytes differ from ledger"
                )
            return observation_path

        trusted = verify_observation_collection_authorization(
            authorization,
            trusted_candidate,
            attempted_at=attempted_at,
        )
        observation_bytes = canonical_json_bytes(trusted)
        ledger = authorization.approval_ledger
        event = ApprovalLedgerEvent(
            ledger_id=ledger.ledger_id,
            protocol_id=ledger.protocol_id,
            component_id=ledger.component_id,
            manifest_revision=ledger.manifest_revision,
            draft_manifest_canonical_sha256=(
                ledger.draft_manifest_canonical_sha256
            ),
            draft_manifest_raw_file_sha256=(
                ledger.draft_manifest_raw_file_sha256
            ),
            draft_manifest_raw_byte_length=(
                ledger.draft_manifest_raw_byte_length
            ),
            sequence=ledger.next_sequence,
            event_id=f"{trusted.observation_id}-COLLECTION",
            previous_event_sha256=ledger.expected_previous_event_sha256,
            event_type="OBSERVATION_AUTHORIZED",
            record_kind="OBSERVATION",
            record_id=trusted.observation_id,
            record_contract_version=trusted.contract_version,
            record_canonical_sha256=observation_hash,
            record_raw_file_sha256=observation_hash,
            record_raw_byte_length=len(observation_bytes),
            recorded_at=attempted_at,
        )
        _validate_prospective_ledger_event(ledger, event)
        if self._closure_reference_path(
            protocol_id,
            manifest_canonical_sha256,
        ).is_file():
            raise ShadowContractError(
                "protocol closure was initiated before observation append"
            )
        self._exclusive_create(observation_path, observation_bytes)
        self._append_event(event)
        return observation_path

    def verify_paired_evaluation_authorization(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
        signal_at: datetime,
        attempted_at: datetime,
    ) -> ProtocolAuthorizationBundle:
        """Reload current A1/closure state before either evaluator runs."""

        if signal_at.utcoffset() is None or attempted_at.utcoffset() is None:
            raise ShadowContractError(
                "paired-evaluation authorization times must be timezone-aware"
            )
        authorization = self.load_authorization(
            protocol_id=protocol_id,
            manifest_canonical_sha256=manifest_canonical_sha256,
            ledger_id=ledger_id,
        )
        if authorization.approval_ledger.closure_event is not None:
            raise ShadowContractError("protocol is closed to paired evaluation")
        if attempted_at < signal_at:
            raise ShadowContractError(
                "paired evaluation cannot precede its signal"
            )
        if attempted_at < authorization.approval.decided_at:
            raise ShadowContractError("paired evaluation precedes A1 decision")
        if attempted_at < authorization.manifest.collection_start_not_before:
            raise ShadowContractError(
                "paired evaluation precedes collection window"
            )
        if signal_at < authorization.approval.decided_at:
            raise ShadowContractError("paired signal predates A1 decision")
        if signal_at < authorization.manifest.collection_start_not_before:
            raise ShadowContractError(
                "paired signal predates collection window"
            )
        terminal_close = session_close_at(
            authorization.manifest.fixed_terminal_date
        )
        if attempted_at > terminal_close:
            raise ShadowContractError(
                "paired evaluation is after fixed terminal date"
            )
        signal_date = signal_at.astimezone(IDX_TIMEZONE).date()
        if signal_date >= authorization.manifest.fixed_terminal_date:
            raise ShadowContractError(
                "paired signal leaves no post-signal terminal runway"
            )
        calendar = authorization.trading_calendar
        if calendar is None or signal_date not in calendar.sessions:
            raise ShadowContractError(
                "paired signal date is not a frozen IDX session"
            )
        required_sessions = (
            authorization.manifest.labels.entry_validity_trading_days + 15
        )
        post_signal_sessions = tuple(
            session
            for session in calendar.sessions
            if signal_date
            < session
            <= authorization.manifest.fixed_terminal_date
        )
        if len(post_signal_sessions) < required_sessions:
            raise ShadowContractError(
                "paired evaluation lacks frozen terminal runway"
            )
        return authorization

    def verify_fixed_notional_maturation_authorization(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
        observation: ShadowObservation,
        attempted_at: datetime,
    ) -> ProtocolAuthorizationBundle:
        """Reload current A1/closure state for one RS-P2-015 maturation.

        ``attempted_at`` is the actual local processing time, not the frozen
        market-data cutoff. Owner-stop closure may still mature an exact
        pre-closure observation; integrity closure remains fail-closed through
        ``verify_maturation_authorization``.
        """

        if attempted_at.utcoffset() is None:
            raise ShadowContractError(
                "fixed-notional maturation time must be timezone-aware"
            )
        authorization = self.load_authorization(
            protocol_id=protocol_id,
            manifest_canonical_sha256=manifest_canonical_sha256,
            ledger_id=ledger_id,
        )
        trusted = verify_maturation_authorization(
            authorization,
            observation,
        )
        if attempted_at < trusted.captured_at:
            raise ShadowContractError(
                "fixed-notional maturation predates observation capture"
            )
        return authorization

    def load_portfolio_observation_artifacts(
        self,
        manifest: ShadowProtocolManifest,
        observation: ShadowObservation,
    ) -> tuple[
        FrozenPortfolioPolicy,
        PortfolioStateSourceRecord,
        PortfolioState,
    ]:
        """Reload every RS-P2-014 portfolio edge for replay/maturation."""

        if manifest_portfolio_profile(manifest) != PORTFOLIO_BINDING_PROFILE:
            raise ShadowContractError(
                "manifest does not declare portfolio-binding-v1"
            )
        store = PortfolioArtifactStore(self.root)
        policy = store.load_policy_for_manifest(manifest)
        state = store.verify_observation_state(manifest, observation)
        source, _ = store.load_source_record(
            manifest,
            state.portfolio_source_record_id,
        )
        return policy, source, state

    def load_authorization(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
    ) -> ProtocolAuthorizationBundle:
        return self._load_authorization(
            protocol_id=protocol_id,
            manifest_canonical_sha256=manifest_canonical_sha256,
            ledger_id=ledger_id,
            expected_pending_closure_reference=None,
        )

    def _load_authorization(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
        expected_pending_closure_reference: (
            ProtocolClosureReference | None
        ),
    ) -> ProtocolAuthorizationBundle:
        ledger = self.load_approval_ledger(
            protocol_id=protocol_id,
            manifest_canonical_sha256=manifest_canonical_sha256,
            ledger_id=ledger_id,
        )
        approval_event = ledger.approval_event
        if approval_event is None:
            raise ShadowContractError("approval ledger has no A1 event")
        manifest_path = self._manifest_path(
            protocol_id,
            manifest_canonical_sha256,
            ledger.draft_manifest_raw_file_sha256,
        )
        manifest_reference = self._load_manifest_reference(
            protocol_id,
            ledger.manifest_revision,
        )
        if (
            manifest_reference.protocol_id,
            manifest_reference.component_id,
            manifest_reference.manifest_revision,
            manifest_reference.manifest_contract_version,
            manifest_reference.canonical_sha256,
            manifest_reference.raw_file_sha256,
            manifest_reference.raw_byte_length,
            manifest_reference.manifest_relative_path,
        ) != (
            ledger.protocol_id,
            ledger.component_id,
            ledger.manifest_revision,
            "shadow-protocol-manifest-v2",
            ledger.draft_manifest_canonical_sha256,
            ledger.draft_manifest_raw_file_sha256,
            ledger.draft_manifest_raw_byte_length,
            self._relative(manifest_path),
        ):
            raise ShadowContractError(
                "manifest revision reference differs from approval ledger"
            )
        manifest_raw = self._read_exact(manifest_path, "manifest")
        manifest = load_shadow_protocol_manifest_v2(manifest_raw)
        if _required_canonical_sha256(manifest) != manifest_canonical_sha256:
            raise ShadowContractError("stored manifest canonical hash mismatch")
        methodology_path = (
            self.root
            / "methodology"
            / manifest.methodology_document_sha256
            / "document.bin"
        )
        if (
            manifest_reference.methodology_document_path,
            manifest_reference.methodology_document_sha256,
            manifest_reference.methodology_relative_path,
        ) != (
            manifest.methodology_document_path,
            manifest.methodology_document_sha256,
            self._relative(methodology_path),
        ):
            raise ShadowContractError(
                "manifest revision methodology reference mismatch"
            )
        methodology = self._read_exact(
            methodology_path,
            "methodology document",
        )
        calendar = self.load_trading_calendar(
            manifest.trading_calendar_sha256
        )
        self._verify_portfolio_binding_if_declared(
            manifest,
            require_a1_capability=True,
        )
        approval_path = self._record_path(
            protocol_id,
            manifest_canonical_sha256,
            "approvals",
            approval_event.record_canonical_sha256,
            approval_event.record_raw_file_sha256,
        )
        approval_raw = self._read_exact(approval_path, "ApprovalRecord")
        approval = load_approval_record_v1(approval_raw)

        closure: ProtocolClosureRecord | None = None
        closure_raw: bytes | None = None
        closure_event = ledger.closure_event
        closure_reference_path = self._closure_reference_path(
            protocol_id,
            manifest_canonical_sha256,
        )
        closure_reference: ProtocolClosureReference | None = None
        if closure_reference_path.is_file():
            closure_reference = _validate_payload(
                ProtocolClosureReference,
                _strict_json_object(
                    self._read_exact(
                        closure_reference_path,
                        "protocol closure reference",
                    ),
                    label="protocol closure reference",
                ),
                "protocol closure reference",
            )
        if closure_event is None and closure_reference is not None:
            if (
                expected_pending_closure_reference is None
                or closure_reference
                != expected_pending_closure_reference
            ):
                raise ShadowContractError(
                    "closure reference exists without terminal ledger event"
                )
        if closure_event is not None and closure_reference is None:
            raise ShadowContractError(
                "closure ledger event exists without manifest-level reference"
            )
        if closure_event is not None and closure_reference is not None:
            if (
                closure_reference.ledger_id,
                closure_reference.closure_id,
                closure_reference.closure_canonical_sha256,
                closure_reference.closure_raw_file_sha256,
                closure_reference.closure_raw_byte_length,
            ) != (
                ledger.ledger_id,
                closure_event.record_id,
                closure_event.record_canonical_sha256,
                closure_event.record_raw_file_sha256,
                closure_event.record_raw_byte_length,
            ):
                raise ShadowContractError(
                    "closure reference differs from terminal ledger event"
                )
            closure_path = self._record_path(
                protocol_id,
                manifest_canonical_sha256,
                "closures",
                closure_event.record_canonical_sha256,
                closure_event.record_raw_file_sha256,
            )
            closure_raw = self._read_exact(
                closure_path,
                "closure record referenced by ledger",
            )
            closure = load_protocol_closure_v1(closure_raw)
        return ProtocolAuthorizationBundle(
            manifest=manifest,
            manifest_raw_file_bytes=manifest_raw,
            methodology_document_bytes=methodology,
            approval=approval,
            approval_raw_file_bytes=approval_raw,
            approval_ledger=ledger,
            trading_calendar=calendar,
            closure=closure,
            closure_raw_file_bytes=closure_raw,
        )

    def load_trading_calendar(self, calendar_sha256: str) -> TradingCalendar:
        _safe_segment(calendar_sha256, "calendar SHA-256")
        path = (
            self.root
            / "trading_calendars"
            / calendar_sha256
            / "calendar.json"
        )
        raw = self._read_exact(path, "trusted trading calendar")
        payload = _strict_json_object(raw, label="trusted trading calendar")
        calendar = _validate_payload(
            TradingCalendar,
            payload,
            "trusted trading calendar",
        )
        if calendar.calendar_sha256 != calendar_sha256:
            raise ShadowContractError("stored trading calendar path/hash mismatch")
        return calendar

    def load_approval_ledger(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
    ) -> ApprovalLedger:
        ledger_reference_raw = self._read_exact(
            self._ledger_reference_path(
                protocol_id,
                manifest_canonical_sha256,
            ),
            "approval-ledger reference",
        )
        ledger_reference = _validate_payload(
            ApprovalLedgerReference,
            _strict_json_object(
                ledger_reference_raw,
                label="approval-ledger reference",
            ),
            "approval-ledger reference",
        )
        if (
            ledger_reference.protocol_id,
            ledger_reference.draft_manifest_canonical_sha256,
            ledger_reference.ledger_id,
        ) != (
            protocol_id,
            manifest_canonical_sha256,
            ledger_id,
        ):
            raise ShadowContractError("approval-ledger reference identity mismatch")
        event_dir = self._ledger_event_dir(
            protocol_id,
            manifest_canonical_sha256,
            ledger_id,
        )
        if not event_dir.is_dir():
            raise ShadowContractError("approval ledger event directory is unavailable")
        reference_paths = sorted(event_dir.glob("*.ref.json"))
        if not reference_paths:
            raise ShadowContractError("approval ledger has no event files")
        events: list[ApprovalLedgerEvent] = []
        for expected_sequence, path in enumerate(reference_paths, start=1):
            if path.name != f"{expected_sequence:010d}.ref.json":
                raise ShadowContractError(
                    "approval ledger event references are not contiguous"
                )
            event_reference = _validate_payload(
                ApprovalLedgerEventReference,
                _strict_json_object(
                    self._read_exact(path, "approval-ledger event reference"),
                    label="approval-ledger event reference",
                ),
                "approval-ledger event reference",
            )
            if (
                event_reference.ledger_id != ledger_id
                or event_reference.sequence != expected_sequence
            ):
                raise ShadowContractError(
                    "approval-ledger event reference identity mismatch"
                )
            event_path = self._ledger_event_blob_path(
                protocol_id,
                manifest_canonical_sha256,
                ledger_id,
                event_reference.event_canonical_sha256,
                event_reference.event_raw_file_sha256,
            )
            raw_event = self._read_exact(
                event_path,
                "content-addressed approval-ledger event",
            )
            if (
                _sha256(raw_event) != event_reference.event_raw_file_sha256
                or len(raw_event) != event_reference.event_raw_byte_length
            ):
                raise ShadowContractError(
                    "approval-ledger event raw hash/length mismatch"
                )
            event = load_approval_ledger_event_v1(raw_event)
            if (
                _required_canonical_sha256(event)
                != event_reference.event_canonical_sha256
            ):
                raise ShadowContractError(
                    "approval-ledger event canonical hash/path mismatch"
                )
            events.append(event)
        first = events[0]
        if (
            ledger_reference.ledger_id,
            ledger_reference.protocol_id,
            ledger_reference.component_id,
            ledger_reference.manifest_revision,
            ledger_reference.draft_manifest_canonical_sha256,
            ledger_reference.draft_manifest_raw_file_sha256,
            ledger_reference.draft_manifest_raw_byte_length,
        ) != (
            first.ledger_id,
            first.protocol_id,
            first.component_id,
            first.manifest_revision,
            first.draft_manifest_canonical_sha256,
            first.draft_manifest_raw_file_sha256,
            first.draft_manifest_raw_byte_length,
        ):
            raise ShadowContractError(
                "approval-ledger reference differs from first event"
            )
        if (
            first.protocol_id,
            first.draft_manifest_canonical_sha256,
            first.ledger_id,
        ) != (
            protocol_id,
            manifest_canonical_sha256,
            ledger_id,
        ):
            raise ShadowContractError("approval ledger path identity mismatch")
        try:
            return ApprovalLedger(
                ledger_id=first.ledger_id,
                protocol_id=first.protocol_id,
                component_id=first.component_id,
                manifest_revision=first.manifest_revision,
                draft_manifest_canonical_sha256=(
                    first.draft_manifest_canonical_sha256
                ),
                draft_manifest_raw_file_sha256=(
                    first.draft_manifest_raw_file_sha256
                ),
                draft_manifest_raw_byte_length=(
                    first.draft_manifest_raw_byte_length
                ),
                events=tuple(events),
            )
        except ValueError as exc:
            raise ShadowContractError("approval ledger failed validation") from exc

    def _load_manifest_for_approval(
        self,
        approval: ApprovalRecord,
    ) -> tuple[ShadowProtocolManifest, bytes, bytes]:
        manifest_path = self._manifest_path(
            approval.protocol_id,
            approval.draft_manifest_canonical_sha256,
            approval.draft_manifest_raw_file_sha256,
        )
        manifest_reference = self._load_manifest_reference(
            approval.protocol_id,
            approval.manifest_revision,
        )
        if (
            manifest_reference.protocol_id,
            manifest_reference.component_id,
            manifest_reference.manifest_revision,
            manifest_reference.manifest_contract_version,
            manifest_reference.canonical_sha256,
            manifest_reference.raw_file_sha256,
            manifest_reference.raw_byte_length,
            manifest_reference.manifest_relative_path,
        ) != (
            approval.protocol_id,
            approval.component_id,
            approval.manifest_revision,
            approval.manifest_contract_version,
            approval.draft_manifest_canonical_sha256,
            approval.draft_manifest_raw_file_sha256,
            approval.draft_manifest_raw_byte_length,
            self._relative(manifest_path),
        ):
            raise ShadowContractError(
                "manifest revision reference differs from ApprovalRecord"
            )
        manifest_raw = self._read_exact(manifest_path, "manifest")
        manifest = load_shadow_protocol_manifest_v2(manifest_raw)
        methodology_path = (
            self.root
            / "methodology"
            / manifest.methodology_document_sha256
            / "document.bin"
        )
        if (
            manifest_reference.methodology_document_path,
            manifest_reference.methodology_document_sha256,
            manifest_reference.methodology_relative_path,
        ) != (
            manifest.methodology_document_path,
            manifest.methodology_document_sha256,
            self._relative(methodology_path),
        ):
            raise ShadowContractError(
                "manifest revision methodology reference mismatch"
            )
        methodology = self._read_exact(
            methodology_path,
            "methodology document",
        )
        return manifest, manifest_raw, methodology

    def _verify_portfolio_binding_if_declared(
        self,
        manifest: ShadowProtocolManifest,
        *,
        require_a1_capability: bool = False,
    ) -> None:
        profile = manifest_portfolio_profile(manifest)
        if profile is None:
            return
        if profile != PORTFOLIO_BINDING_PROFILE:
            raise ShadowContractError("unsupported portfolio binding profile")
        policy = PortfolioArtifactStore(self.root).load_policy_for_manifest(
            manifest
        )
        if require_a1_capability:
            verify_portfolio_a1_capability(manifest, policy)

    def _append_event(self, event: ApprovalLedgerEvent) -> Path:
        event_bytes = canonical_json_bytes(event)
        event_canonical_hash = _required_canonical_sha256(event)
        event_raw_hash = _sha256(event_bytes)
        event_path = self._ledger_event_blob_path(
            event.protocol_id,
            event.draft_manifest_canonical_sha256,
            event.ledger_id,
            event_canonical_hash,
            event_raw_hash,
        )
        self._exclusive_create(event_path, event_bytes)
        reference = ApprovalLedgerEventReference(
            ledger_id=event.ledger_id,
            sequence=event.sequence,
            event_canonical_sha256=event_canonical_hash,
            event_raw_file_sha256=event_raw_hash,
            event_raw_byte_length=len(event_bytes),
        )
        reference_path = (
            self._ledger_event_dir(
                event.protocol_id,
                event.draft_manifest_canonical_sha256,
                event.ledger_id,
            )
            / f"{event.sequence:010d}.ref.json"
        )
        return self._exclusive_create(
            reference_path,
            canonical_json_bytes(reference),
        )

    def _manifest_path(
        self,
        protocol_id: str,
        canonical_hash: str,
        raw_hash: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, canonical_hash)
            / "manifests"
            / _safe_segment(raw_hash, "raw manifest SHA-256")
            / "manifest.json"
        )

    def _load_manifest_reference(
        self,
        protocol_id: str,
        manifest_revision: int,
    ) -> ManifestArtifactReference:
        path = self._manifest_reference_path(protocol_id, manifest_revision)
        return _validate_payload(
            ManifestArtifactReference,
            _strict_json_object(
                self._read_exact(path, "manifest revision reference"),
                label="manifest revision reference",
            ),
            "manifest revision reference",
        )

    def _manifest_reference_path(
        self,
        protocol_id: str,
        manifest_revision: int,
    ) -> Path:
        if manifest_revision < 1:
            raise ShadowContractError("manifest revision must be positive")
        return (
            self.root
            / "protocols"
            / _safe_segment(protocol_id, "protocol ID")
            / "manifest_revisions"
            / f"{manifest_revision:08d}.json"
        )

    def _record_path(
        self,
        protocol_id: str,
        canonical_manifest_hash: str,
        namespace: str,
        record_canonical_hash: str,
        record_raw_hash: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, canonical_manifest_hash)
            / _safe_segment(namespace, "record namespace")
            / _safe_segment(record_canonical_hash, "record canonical SHA-256")
            / f"{_safe_segment(record_raw_hash, 'record raw SHA-256')}.json"
        )

    def _ledger_reference_path(
        self,
        protocol_id: str,
        canonical_manifest_hash: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, canonical_manifest_hash)
            / "approval_ledgers"
            / "ledger_reference.json"
        )

    def _claim_closure_reference(
        self,
        closure: ProtocolClosureRecord,
        closure_raw_file_bytes: bytes,
    ) -> Path:
        reference = self._build_closure_reference(
            closure,
            closure_raw_file_bytes,
        )
        return self._exclusive_create(
            self._closure_reference_path(
                closure.protocol_id,
                closure.draft_manifest_canonical_sha256,
            ),
            canonical_json_bytes(reference),
        )

    @staticmethod
    def _build_closure_reference(
        closure: ProtocolClosureRecord,
        closure_raw_file_bytes: bytes,
    ) -> ProtocolClosureReference:
        return ProtocolClosureReference(
            ledger_id=closure.approval_ledger_id,
            closure_id=closure.closure_id,
            closure_canonical_sha256=_required_canonical_sha256(closure),
            closure_raw_file_sha256=_sha256(closure_raw_file_bytes),
            closure_raw_byte_length=len(closure_raw_file_bytes),
        )

    def _closure_reference_path(
        self,
        protocol_id: str,
        canonical_manifest_hash: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, canonical_manifest_hash)
            / "approval_ledgers"
            / "closure_reference.json"
        )

    def _ledger_event_blob_path(
        self,
        protocol_id: str,
        canonical_manifest_hash: str,
        ledger_id: str,
        event_canonical_hash: str,
        event_raw_hash: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, canonical_manifest_hash)
            / "approval_ledgers"
            / _safe_segment(ledger_id, "approval ledger ID")
            / "event_blobs"
            / _safe_segment(
                event_canonical_hash,
                "event canonical SHA-256",
            )
            / f"{_safe_segment(event_raw_hash, 'event raw SHA-256')}.json"
        )

    def _ledger_event_dir(
        self,
        protocol_id: str,
        canonical_manifest_hash: str,
        ledger_id: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, canonical_manifest_hash)
            / "approval_ledgers"
            / _safe_segment(ledger_id, "approval ledger ID")
            / "events"
        )

    def _protocol_root(self, protocol_id: str, canonical_hash: str) -> Path:
        return (
            self.root
            / "protocols"
            / _safe_segment(protocol_id, "protocol ID")
            / _safe_segment(canonical_hash, "canonical manifest SHA-256")
        )

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ShadowContractError("governance path escaped store root") from exc

    @staticmethod
    def _exclusive_create(path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ShadowContractError(
                    f"immutable governance artifact collision: {path}"
                ) from None
        return path

    @staticmethod
    def _read_exact(path: Path, label: str) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ShadowContractError(f"{label} is unavailable: {path}") from exc


def _verify_approval_without_ledger(
    *,
    manifest: ShadowProtocolManifest,
    manifest_raw_file_bytes: bytes,
    methodology_document_bytes: bytes,
    approval: ApprovalRecord,
    approval_raw_file_bytes: bytes,
    trading_calendar: TradingCalendar,
) -> None:
    synthetic_event = ApprovalLedgerEvent(
        ledger_id=approval.approval_ledger_id,
        protocol_id=approval.protocol_id,
        component_id=approval.component_id,
        manifest_revision=approval.manifest_revision,
        draft_manifest_canonical_sha256=(
            approval.draft_manifest_canonical_sha256
        ),
        draft_manifest_raw_file_sha256=(
            approval.draft_manifest_raw_file_sha256
        ),
        draft_manifest_raw_byte_length=(
            approval.draft_manifest_raw_byte_length
        ),
        sequence=1,
        event_id=f"{approval.approval_id}-LEDGER",
        previous_event_sha256=None,
        event_type="A1_APPROVED",
        record_kind="APPROVAL",
        record_id=approval.approval_id,
        record_contract_version=approval.contract_version,
        record_canonical_sha256=_required_canonical_sha256(approval),
        record_raw_file_sha256=_sha256(approval_raw_file_bytes),
        record_raw_byte_length=len(approval_raw_file_bytes),
        recorded_at=approval.decided_at,
    )
    synthetic_ledger = ApprovalLedger(
        ledger_id=approval.approval_ledger_id,
        protocol_id=approval.protocol_id,
        component_id=approval.component_id,
        manifest_revision=approval.manifest_revision,
        draft_manifest_canonical_sha256=(
            approval.draft_manifest_canonical_sha256
        ),
        draft_manifest_raw_file_sha256=(
            approval.draft_manifest_raw_file_sha256
        ),
        draft_manifest_raw_byte_length=(
            approval.draft_manifest_raw_byte_length
        ),
        events=(synthetic_event,),
    )
    verify_approval_binding(
        manifest=manifest,
        manifest_raw_file_bytes=manifest_raw_file_bytes,
        methodology_document_bytes=methodology_document_bytes,
        approval=approval,
        approval_raw_file_bytes=approval_raw_file_bytes,
        approval_ledger=synthetic_ledger,
        trading_calendar=trading_calendar,
    )


def _verify_closure_identity(
    authorization: ProtocolAuthorizationBundle,
    closure: ProtocolClosureRecord,
) -> None:
    if (
        closure.approval_ledger_id,
        closure.protocol_id,
        closure.component_id,
        closure.manifest_contract_version,
        closure.manifest_revision,
        closure.draft_manifest_canonical_sha256,
        closure.draft_manifest_raw_file_sha256,
        closure.draft_manifest_raw_byte_length,
        closure.approval_id,
        closure.approval_record_canonical_sha256,
        closure.governance_mode,
    ) != (
        authorization.approval_ledger.ledger_id,
        authorization.manifest.protocol_id,
        authorization.manifest.component_id,
        authorization.manifest.contract_version,
        authorization.manifest.manifest_revision,
        _required_canonical_sha256(authorization.manifest),
        _sha256(authorization.manifest_raw_file_bytes),
        len(authorization.manifest_raw_file_bytes),
        authorization.approval.approval_id,
        _required_canonical_sha256(authorization.approval),
        authorization.manifest.governance_mode,
    ):
        raise ShadowContractError("ClosureRecord identity differs from authorization")
    if closure.effective_at < authorization.approval.decided_at:
        raise ShadowContractError("protocol closure precedes A1 approval")
    if (
        closure.reason_code == "FIXED_TERMINAL_REACHED"
        and closure.effective_at
        != session_close_at(authorization.manifest.fixed_terminal_date)
    ):
        raise ShadowContractError(
            "fixed-terminal closure time differs from frozen calendar close"
        )
    if (
        authorization.manifest.governance_mode == "SOLO_SELF_REVIEW"
        and closure.closed_by.casefold()
        != authorization.manifest.owner.casefold()
    ):
        raise ShadowContractError("solo closure must be recorded by the owner")


def _revalidate_bundle(
    authorization: ProtocolAuthorizationBundle,
) -> ProtocolAuthorizationBundle:
    try:
        return ProtocolAuthorizationBundle.model_validate(
            authorization.model_dump(mode="python")
        )
    except ValueError as exc:
        raise ShadowContractError("protocol authorization bundle is invalid") from exc


def _strict_json_object(raw_file_bytes: bytes, *, label: str) -> dict[str, object]:
    def reject_duplicate_pairs(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ShadowContractError(f"{label} has duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        decoded = json.loads(
            raw_file_bytes.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
        )
    except ShadowContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ShadowContractError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise ShadowContractError(f"{label} JSON root must be an object")
    return decoded


def _validate_payload(
    model_type: type[_ModelT],
    payload: dict[str, object],
    label: str,
) -> _ModelT:
    try:
        return model_type.model_validate(payload)
    except ValueError as exc:
        raise ShadowContractError(f"{label} failed strict schema validation") from exc


def _require_same_model(label: str, left: BaseModel, right: BaseModel) -> None:
    if canonical_sha256(left) != canonical_sha256(right):
        raise ShadowContractError(f"{label} object differs from exact raw file")


def _required_canonical_sha256(model: BaseModel) -> str:
    digest = canonical_sha256(model)
    if digest is None:
        raise ShadowContractError("canonical SHA-256 is unavailable")
    return digest


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_prospective_ledger_event(
    ledger: ApprovalLedger,
    event: ApprovalLedgerEvent,
) -> None:
    try:
        ApprovalLedger.model_validate(
            {
                **ledger.model_dump(mode="python"),
                "events": (*ledger.events, event),
            }
        )
    except ValueError as exc:
        raise ShadowContractError(
            "prospective approval-ledger event failed validation"
        ) from exc


def _verify_methodology_document(payload: bytes, expected_sha256: str) -> None:
    if _sha256(payload) != expected_sha256:
        raise ShadowContractError("methodology document SHA-256 mismatch")
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ShadowContractError(
            "methodology document must be valid UTF-8 text"
        ) from exc
    if not decoded.strip():
        raise ShadowContractError("methodology document must not be empty")


def _safe_segment(value: str, label: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value):
        raise ShadowContractError(f"unsafe {label}: {value!r}")
    return value


__all__ = [
    "APPROVAL_LEDGER_EVENT_REFERENCE_VERSION",
    "APPROVAL_LEDGER_REFERENCE_VERSION",
    "MANIFEST_REFERENCE_VERSION",
    "PROTOCOL_CLOSURE_REFERENCE_VERSION",
    "PROTOCOL_AUTHORIZATION_BUNDLE_VERSION",
    "ApprovalLedgerEventReference",
    "ApprovalLedgerReference",
    "ManifestArtifactReference",
    "ProtocolClosureReference",
    "ProtocolAuthorizationBundle",
    "ProtocolGovernanceStore",
    "load_approval_ledger_event_v1",
    "load_approval_record_v1",
    "load_protocol_closure_v1",
    "load_shadow_protocol_manifest_v2",
    "verify_approval_binding",
    "verify_maturation_authorization",
    "verify_observation_collection_authorization",
]
