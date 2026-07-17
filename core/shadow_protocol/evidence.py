"""Point-in-time candidate, snapshot, parity, and lineage evidence.

This module is deliberately evaluation-only.  It has no dependency on the
orchestrator, ranking, sizing, risk-governor, execution, or network paths.
Raw source payloads are stored as canonical JSON strings so a frozen Pydantic
model cannot retain a mutable nested ``dict`` or ``list`` reference.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import TYPE_CHECKING, Literal, Mapping, Sequence
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from .contracts import (
    CanonicalTicker,
    ComponentID,
    NonEmptyString,
    Sha256,
    ShadowContractError,
    ShadowObservation,
    ShadowOutcome,
    ShadowProtocolManifest,
    _StrictFrozenModel,
    _verify_protocol_component,
    canonical_json_bytes,
    canonical_outcome_id,
    canonical_sha256,
)

if TYPE_CHECKING:
    from .outcome_engine import FrozenBarSeries


CANDIDATE_EVENT_VERSION = "shadow-candidate-event-v1"
QUARANTINED_CANDIDATE_EVENT_VERSION = "shadow-quarantined-candidate-event-v1"
FROZEN_SNAPSHOT_VERSION = "shadow-frozen-snapshot-v1"
RAW_CANDIDATE_SET_VERSION = "shadow-raw-candidate-set-v1"
CANDIDATE_SET_VERSION = "shadow-candidate-set-v1"
LINEAGE_BUNDLE_VERSION = "shadow-lineage-bundle-v1"
PARITY_RESULT_VERSION = "shadow-opportunity-parity-v1"
IDX_TIMEZONE = ZoneInfo("Asia/Jakarta")

CandidateSide = Literal["CONTROL", "CHALLENGER"]
DispositionState = Literal["PRUNED", "RETAINED"]


class _EvidenceOnlyArtifact(_StrictFrozenModel):
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False


def canonical_payload_json(payload: Mapping[str, object] | str) -> str:
    """Return canonical object JSON after rejecting mutable/invalid values.

    A string input is parsed and canonicalized as well.  The root must always
    be an object because source rows and snapshots need named fields.
    """

    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload is not valid JSON") from exc
    else:
        decoded = payload
    if not isinstance(decoded, dict):
        raise ValueError("payload JSON root must be an object")
    _validate_json_value(decoded, path="$", seen=set())
    return json.dumps(
        decoded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_payload_bytes(payload: Mapping[str, object] | str) -> bytes:
    """Return deterministic UTF-8 bytes for a JSON object."""

    return canonical_payload_json(payload).encode("utf-8")


def canonical_payload_sha256(payload: Mapping[str, object] | str) -> str:
    """Return a full lowercase SHA-256 for canonical object JSON."""

    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()


def canonical_source_record_sha256(
    *,
    source_id: str,
    source_definition_sha256: str,
    source_as_of: datetime,
    source_expires_at: datetime | None,
    source_row_number: int | None,
    payload_sha256: str,
) -> str:
    """Hash one source record using its stable definition and PIT metadata."""

    if source_as_of.utcoffset() is None:
        raise ValueError("source_as_of must be timezone-aware")
    if source_expires_at is not None and source_expires_at.utcoffset() is None:
        raise ValueError("source_expires_at must be timezone-aware")
    return canonical_payload_sha256(
        {
            "payload_sha256": payload_sha256,
            "source_as_of": _utc_iso(source_as_of),
            "source_definition_sha256": source_definition_sha256,
            "source_expires_at": (
                _utc_iso(source_expires_at)
                if source_expires_at is not None
                else None
            ),
            "source_id": source_id,
            "source_row_number": source_row_number,
        }
    )


class FrozenSnapshot(_EvidenceOnlyArtifact):
    """One immutable, point-in-time snapshot used by a candidate decision."""

    contract_version: Literal["shadow-frozen-snapshot-v1"] = (
        FROZEN_SNAPSHOT_VERSION
    )
    snapshot_id: NonEmptyString
    ticker: CanonicalTicker
    as_of_date: date
    snapshot_as_of: datetime
    snapshot_sha256: Sha256
    source_id: NonEmptyString
    source_definition_sha256: Sha256
    source_record_sha256: Sha256
    source_as_of: datetime
    source_expires_at: datetime | None = None
    payload_json: NonEmptyString
    payload_sha256: Sha256

    @field_validator("snapshot_as_of", "source_as_of", "source_expires_at")
    @classmethod
    def require_aware_times(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("snapshot datetimes must be timezone-aware")
        return value

    @field_validator("payload_json")
    @classmethod
    def require_canonical_payload(cls, value: str) -> str:
        if canonical_payload_json(value) != value:
            raise ValueError("snapshot payload must already be canonical JSON")
        return value

    @model_validator(mode="after")
    def verify_snapshot(self) -> FrozenSnapshot:
        if self.snapshot_as_of.astimezone(IDX_TIMEZONE).date() > self.as_of_date:
            raise ValueError("snapshot vintage is after its signal date")
        if self.source_as_of > self.snapshot_as_of:
            raise ValueError("snapshot source vintage is after snapshot finalization")
        if (
            self.source_expires_at is not None
            and self.source_expires_at <= self.snapshot_as_of
        ):
            raise ValueError("expired source record cannot finalize a snapshot")
        if canonical_payload_sha256(self.payload_json) != self.payload_sha256:
            raise ValueError("snapshot payload hash mismatch")
        payload_ticker = _payload_ticker(self.payload_json)
        if payload_ticker is not None and payload_ticker != self.ticker:
            raise ValueError("snapshot payload ticker does not match snapshot ticker")
        expected_record = canonical_source_record_sha256(
            source_id=self.source_id,
            source_definition_sha256=self.source_definition_sha256,
            source_as_of=self.source_as_of,
            source_expires_at=self.source_expires_at,
            source_row_number=None,
            payload_sha256=self.payload_sha256,
        )
        if self.source_record_sha256 != expected_record:
            raise ValueError("snapshot source-record hash mismatch")
        expected_snapshot = canonical_frozen_snapshot_sha256(
            snapshot_id=self.snapshot_id,
            ticker=self.ticker,
            as_of_date=self.as_of_date,
            snapshot_as_of=self.snapshot_as_of,
            source_id=self.source_id,
            source_definition_sha256=self.source_definition_sha256,
            source_record_sha256=self.source_record_sha256,
            source_as_of=self.source_as_of,
            source_expires_at=self.source_expires_at,
            payload_json=self.payload_json,
            payload_sha256=self.payload_sha256,
        )
        if self.snapshot_sha256 != expected_snapshot:
            raise ValueError("canonical snapshot hash mismatch")
        return self

    @property
    def payload(self) -> dict[str, object]:
        """Decode a fresh copy; callers never receive stored mutable state."""

        return json.loads(self.payload_json)


def canonical_frozen_snapshot_sha256(
    *,
    snapshot_id: str,
    ticker: str,
    as_of_date: date,
    snapshot_as_of: datetime,
    source_id: str,
    source_definition_sha256: str,
    source_record_sha256: str,
    source_as_of: datetime,
    source_expires_at: datetime | None,
    payload_json: str,
    payload_sha256: str,
) -> str:
    """Compute snapshot identity before constructing ``FrozenSnapshot``."""

    if snapshot_as_of.utcoffset() is None or source_as_of.utcoffset() is None:
        raise ValueError("snapshot hash datetimes must be timezone-aware")
    if source_expires_at is not None and source_expires_at.utcoffset() is None:
        raise ValueError("snapshot expiry must be timezone-aware")
    return canonical_payload_sha256(
        {
            "as_of_date": as_of_date.isoformat(),
            "payload_json": canonical_payload_json(payload_json),
            "payload_sha256": payload_sha256,
            "snapshot_as_of": _utc_iso(snapshot_as_of),
            "snapshot_id": snapshot_id,
            "source_as_of": _utc_iso(source_as_of),
            "source_definition_sha256": source_definition_sha256,
            "source_expires_at": (
                _utc_iso(source_expires_at)
                if source_expires_at is not None
                else None
            ),
            "source_id": source_id,
            "source_record_sha256": source_record_sha256,
            "ticker": ticker,
        }
    )


class CandidateEvent(_EvidenceOnlyArtifact):
    """One complete raw candidate before either side applies pruning."""

    contract_version: Literal["shadow-candidate-event-v1"] = CANDIDATE_EVENT_VERSION
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    opportunity_set_id: NonEmptyString
    opportunity_set_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    signal_at: datetime
    as_of_date: date
    captured_at: datetime
    snapshot_id: NonEmptyString
    snapshot_sha256: Sha256
    snapshot_as_of: datetime
    snapshot_source_record_sha256: Sha256
    candidate_source_id: NonEmptyString
    candidate_source_definition_sha256: Sha256
    candidate_source_sha256: Sha256
    candidate_source_as_of: datetime
    candidate_source_expires_at: datetime | None = None
    trading_calendar_id: NonEmptyString
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    corporate_action_events_at_signal_sha256: Sha256
    source_row_number: int = Field(ge=0)
    raw_payload_json: NonEmptyString
    raw_payload_sha256: Sha256

    @field_validator(
        "signal_at",
        "captured_at",
        "snapshot_as_of",
        "candidate_source_as_of",
        "candidate_source_expires_at",
    )
    @classmethod
    def require_aware_times(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("candidate-event datetimes must be timezone-aware")
        return value

    @field_validator("raw_payload_json")
    @classmethod
    def require_canonical_payload(cls, value: str) -> str:
        if canonical_payload_json(value) != value:
            raise ValueError("candidate payload must already be canonical JSON")
        return value

    @model_validator(mode="after")
    def verify_candidate_event(self) -> CandidateEvent:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.signal_at.astimezone(IDX_TIMEZONE).date() != self.as_of_date:
            raise ValueError("candidate as_of_date must equal local signal date")
        if self.captured_at < self.signal_at:
            raise ValueError("candidate capture cannot precede its signal")
        if self.snapshot_as_of > self.signal_at:
            raise ValueError("candidate snapshot vintage is after signal")
        if self.candidate_source_as_of > self.signal_at:
            raise ValueError("candidate source vintage is after signal")
        if (
            self.candidate_source_expires_at is not None
            and self.candidate_source_expires_at < self.candidate_source_as_of
        ):
            raise ValueError("candidate source expiry precedes source vintage")
        if (
            self.candidate_source_expires_at is not None
            and self.candidate_source_expires_at <= self.signal_at
        ):
            raise ValueError("expired source record cannot become a candidate")
        if canonical_payload_sha256(self.raw_payload_json) != self.raw_payload_sha256:
            raise ValueError("candidate raw-payload hash mismatch")
        expected_source_record = canonical_source_record_sha256(
            source_id=self.candidate_source_id,
            source_definition_sha256=self.candidate_source_definition_sha256,
            source_as_of=self.candidate_source_as_of,
            source_expires_at=self.candidate_source_expires_at,
            source_row_number=self.source_row_number,
            payload_sha256=self.raw_payload_sha256,
        )
        if self.candidate_source_sha256 != expected_source_record:
            raise ValueError("candidate source-record hash mismatch")
        payload_ticker = _payload_ticker(self.raw_payload_json)
        if payload_ticker is not None and payload_ticker != self.ticker:
            raise ValueError("candidate payload ticker does not match event ticker")
        expected_event_id = canonical_raw_event_id(
            opportunity_set_id=self.opportunity_set_id,
            ticker=self.ticker,
            signal_at=self.signal_at,
            snapshot_sha256=self.snapshot_sha256,
            candidate_source_sha256=self.candidate_source_sha256,
            source_row_number=self.source_row_number,
            raw_payload_sha256=self.raw_payload_sha256,
        )
        if self.raw_event_id != expected_event_id:
            raise ValueError("raw_event_id is not the deterministic candidate identity")
        return self

    @property
    def raw_payload(self) -> dict[str, object]:
        """Decode a fresh payload copy without exposing stored mutable state."""

        return json.loads(self.raw_payload_json)


class QuarantinedCandidateEvent(_EvidenceOnlyArtifact):
    """Raw source row which cannot safely become a canonical candidate."""

    contract_version: Literal["shadow-quarantined-candidate-event-v1"] = (
        QUARANTINED_CANDIDATE_EVENT_VERSION
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    opportunity_set_id: NonEmptyString
    opportunity_set_sha256: Sha256
    raw_event_id: NonEmptyString
    raw_ticker: NonEmptyString
    quarantine_reason: NonEmptyString
    signal_at: datetime
    as_of_date: date
    captured_at: datetime
    snapshot_id: NonEmptyString
    snapshot_sha256: Sha256
    snapshot_as_of: datetime
    snapshot_source_record_sha256: Sha256
    candidate_source_id: NonEmptyString
    candidate_source_definition_sha256: Sha256
    candidate_source_sha256: Sha256
    candidate_source_as_of: datetime
    candidate_source_expires_at: datetime | None = None
    trading_calendar_id: NonEmptyString
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    corporate_action_events_at_signal_sha256: Sha256
    source_row_number: int = Field(ge=0)
    raw_payload_json: NonEmptyString
    raw_payload_sha256: Sha256

    @field_validator(
        "signal_at",
        "captured_at",
        "snapshot_as_of",
        "candidate_source_as_of",
        "candidate_source_expires_at",
    )
    @classmethod
    def require_aware_times(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("quarantined-event datetimes must be timezone-aware")
        return value

    @field_validator("raw_payload_json")
    @classmethod
    def require_canonical_payload(cls, value: str) -> str:
        if canonical_payload_json(value) != value:
            raise ValueError("quarantined payload must already be canonical JSON")
        return value

    @model_validator(mode="after")
    def verify_quarantined_event(self) -> QuarantinedCandidateEvent:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.signal_at.astimezone(IDX_TIMEZONE).date() != self.as_of_date:
            raise ValueError("quarantined as_of_date must equal local signal date")
        if self.captured_at < self.signal_at:
            raise ValueError("quarantined capture cannot precede signal")
        if self.snapshot_as_of > self.signal_at:
            raise ValueError("quarantined snapshot vintage is after signal")
        if self.candidate_source_as_of > self.signal_at:
            raise ValueError("quarantined source vintage is after signal")
        if (
            self.candidate_source_expires_at is not None
            and self.candidate_source_expires_at < self.candidate_source_as_of
        ):
            raise ValueError("quarantined source expiry precedes source vintage")
        expired = (
            self.candidate_source_expires_at is not None
            and self.candidate_source_expires_at <= self.signal_at
        )
        explicitly_expired = self.quarantine_reason.upper() == "EXPIRED"
        if expired and not explicitly_expired:
            raise ValueError(
                "expired record is permitted only with explicit EXPIRED quarantine"
            )
        if explicitly_expired and not expired:
            raise ValueError("EXPIRED quarantine requires expiry before signal")
        if canonical_payload_sha256(self.raw_payload_json) != self.raw_payload_sha256:
            raise ValueError("quarantined raw-payload hash mismatch")
        expected_source_record = canonical_source_record_sha256(
            source_id=self.candidate_source_id,
            source_definition_sha256=self.candidate_source_definition_sha256,
            source_as_of=self.candidate_source_as_of,
            source_expires_at=self.candidate_source_expires_at,
            source_row_number=self.source_row_number,
            payload_sha256=self.raw_payload_sha256,
        )
        if self.candidate_source_sha256 != expected_source_record:
            raise ValueError("quarantined source-record hash mismatch")
        expected_event_id = canonical_raw_event_id(
            opportunity_set_id=self.opportunity_set_id,
            ticker=self.raw_ticker,
            signal_at=self.signal_at,
            snapshot_sha256=self.snapshot_sha256,
            candidate_source_sha256=self.candidate_source_sha256,
            source_row_number=self.source_row_number,
            raw_payload_sha256=self.raw_payload_sha256,
        )
        if self.raw_event_id != expected_event_id:
            raise ValueError("quarantined raw_event_id is not deterministic")
        return self

    @property
    def raw_payload(self) -> dict[str, object]:
        return json.loads(self.raw_payload_json)


CandidateRecord = CandidateEvent | QuarantinedCandidateEvent


def canonical_raw_event_id(
    *,
    opportunity_set_id: str,
    ticker: str,
    signal_at: datetime,
    snapshot_sha256: str,
    candidate_source_sha256: str,
    source_row_number: int,
    raw_payload_sha256: str,
) -> str:
    """Derive a stable raw-event ID from immutable record identities."""

    if signal_at.utcoffset() is None:
        raise ValueError("raw event signal time must be timezone-aware")
    digest = canonical_payload_sha256(
        {
            "candidate_source_sha256": candidate_source_sha256,
            "opportunity_set_id": opportunity_set_id,
            "raw_payload_sha256": raw_payload_sha256,
            "signal_at": _utc_iso(signal_at),
            "snapshot_sha256": snapshot_sha256,
            "source_row_number": source_row_number,
            "ticker": ticker,
        }
    )
    return f"EV-{digest[:32]}"


class CandidateDisposition(_StrictFrozenModel):
    """One side's non-authoritative disposition of one raw candidate."""

    raw_event_id: NonEmptyString
    state: DispositionState
    reason_codes: tuple[NonEmptyString, ...]

    @model_validator(mode="after")
    def verify_reason_codes(self) -> CandidateDisposition:
        if self.state == "PRUNED" and not self.reason_codes:
            raise ValueError("pruned candidate needs at least one reason code")
        return self


def canonical_view_sha256(
    side: CandidateSide,
    input_event_ids: Sequence[str],
    dispositions: Sequence[CandidateDisposition],
) -> str:
    return canonical_payload_sha256(
        {
            "dispositions": [item.model_dump(mode="json") for item in dispositions],
            "input_event_ids": list(input_event_ids),
            "side": side,
        }
    )


class CandidateSetView(_EvidenceOnlyArtifact):
    """Complete input order plus retained/pruned state for one side."""

    side: CandidateSide
    input_event_ids: tuple[NonEmptyString, ...] = ()
    dispositions: tuple[CandidateDisposition, ...] = ()
    view_sha256: Sha256

    @model_validator(mode="after")
    def verify_view(self) -> CandidateSetView:
        if len(set(self.input_event_ids)) != len(self.input_event_ids):
            raise ValueError("candidate input event IDs must be unique")
        disposition_ids = tuple(item.raw_event_id for item in self.dispositions)
        if disposition_ids != self.input_event_ids:
            raise ValueError(
                "candidate disposition order must exactly equal raw input order"
            )
        expected = canonical_view_sha256(
            self.side,
            self.input_event_ids,
            self.dispositions,
        )
        if self.view_sha256 != expected:
            raise ValueError("candidate-set view hash mismatch")
        return self

    @property
    def retained_event_ids(self) -> tuple[str, ...]:
        return tuple(
            item.raw_event_id
            for item in self.dispositions
            if item.state == "RETAINED"
        )

    @property
    def pruned_event_ids(self) -> tuple[str, ...]:
        return tuple(
            item.raw_event_id
            for item in self.dispositions
            if item.state == "PRUNED"
        )


def canonical_opportunity_set_sha256(
    opportunity_set_id: str,
    as_of_date: date,
    events: Sequence[CandidateRecord],
    *,
    empty_reason: str | None,
    candidate_source_definition_sha256: str,
    trading_calendar_sha256: str,
    corporate_action_policy_sha256: str,
) -> str:
    """Hash the complete raw opportunity identity without mutable payloads."""

    rows = [
        {
            "candidate_source_sha256": event.candidate_source_sha256,
            "corporate_action_events_at_signal_sha256": (
                event.corporate_action_events_at_signal_sha256
            ),
            "raw_event_id": event.raw_event_id,
            "raw_payload_sha256": event.raw_payload_sha256,
            "snapshot_as_of": _utc_iso(event.snapshot_as_of),
            "snapshot_id": event.snapshot_id,
            "snapshot_sha256": event.snapshot_sha256,
            "snapshot_source_record_sha256": (
                event.snapshot_source_record_sha256
            ),
            "source_row_number": event.source_row_number,
            "ticker": (
                event.ticker
                if isinstance(event, CandidateEvent)
                else event.raw_ticker
            ),
            "quarantine_reason": (
                None
                if isinstance(event, CandidateEvent)
                else event.quarantine_reason
            ),
        }
        for event in events
    ]
    return canonical_payload_sha256(
        {
            "as_of_date": as_of_date.isoformat(),
            "candidate_source_definition_sha256": (
                candidate_source_definition_sha256
            ),
            "corporate_action_policy_sha256": corporate_action_policy_sha256,
            "empty_reason": empty_reason,
            "events": rows,
            "opportunity_set_id": opportunity_set_id,
            "trading_calendar_sha256": trading_calendar_sha256,
        }
    )


def canonical_raw_candidate_set_sha256(
    events: Sequence[CandidateRecord],
    *,
    empty_reason: str | None,
) -> str:
    """Hash complete raw candidate records in source input order."""

    return canonical_payload_sha256(
        {
            "candidate_events": [
                _required_hash(event)
                for event in events
            ],
            "empty_reason": empty_reason,
        }
    )


class RawCandidateSetCapture(_EvidenceOnlyArtifact):
    """Raw-first, immutable capture produced before either side can prune."""

    contract_version: Literal["shadow-raw-candidate-set-v1"] = (
        RAW_CANDIDATE_SET_VERSION
    )
    raw_capture_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    opportunity_set_id: NonEmptyString
    opportunity_set_sha256: Sha256
    signal_at: datetime
    as_of_date: date
    captured_at: datetime
    candidate_source_id: NonEmptyString
    candidate_source_definition_sha256: Sha256
    trading_calendar_id: NonEmptyString
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    raw_candidate_count: int = Field(ge=0)
    empty_reason: str | None = None
    raw_candidate_set_sha256: Sha256
    candidates: tuple[CandidateRecord, ...] = ()

    @field_validator("signal_at", "captured_at")
    @classmethod
    def require_aware_times(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("raw-capture datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_raw_capture(self) -> RawCandidateSetCapture:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.signal_at.astimezone(IDX_TIMEZONE).date() != self.as_of_date:
            raise ValueError("raw-capture date must equal local signal date")
        if self.captured_at < self.signal_at:
            raise ValueError("raw capture cannot precede signal")
        if self.raw_candidate_count != len(self.candidates):
            raise ValueError("raw candidate count does not match candidates")
        if self.raw_candidate_count == 0 and not str(self.empty_reason or "").strip():
            raise ValueError("empty raw capture needs an explicit reason")
        if self.raw_candidate_count > 0 and self.empty_reason is not None:
            raise ValueError("non-empty raw capture cannot carry empty_reason")
        event_ids = tuple(event.raw_event_id for event in self.candidates)
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("raw candidate event IDs must be unique")
        source_rows = tuple(event.source_row_number for event in self.candidates)
        if len(set(source_rows)) != len(source_rows):
            raise ValueError("raw candidate source-row numbers must be unique")
        if source_rows != tuple(sorted(source_rows)):
            raise ValueError("raw candidates must preserve ascending source-row order")
        for event in self.candidates:
            actual = (
                event.protocol_id,
                event.component_id,
                event.manifest_sha256,
                event.opportunity_set_id,
                event.signal_at,
                event.as_of_date,
                event.candidate_source_id,
                event.candidate_source_definition_sha256,
                event.trading_calendar_id,
                event.trading_calendar_sha256,
                event.corporate_action_policy_sha256,
            )
            expected = (
                self.protocol_id,
                self.component_id,
                self.manifest_sha256,
                self.opportunity_set_id,
                self.signal_at,
                self.as_of_date,
                self.candidate_source_id,
                self.candidate_source_definition_sha256,
                self.trading_calendar_id,
                self.trading_calendar_sha256,
                self.corporate_action_policy_sha256,
            )
            if actual != expected:
                raise ValueError("candidate identity differs from raw capture")
            if event.captured_at > self.captured_at:
                raise ValueError("candidate was captured after raw-capture finalization")
        expected_opportunity = canonical_opportunity_set_sha256(
            self.opportunity_set_id,
            self.as_of_date,
            self.candidates,
            empty_reason=self.empty_reason,
            candidate_source_definition_sha256=(
                self.candidate_source_definition_sha256
            ),
            trading_calendar_sha256=self.trading_calendar_sha256,
            corporate_action_policy_sha256=self.corporate_action_policy_sha256,
        )
        if self.opportunity_set_sha256 != expected_opportunity:
            raise ValueError("opportunity-set hash mismatch")
        if any(
            event.opportunity_set_sha256 != expected_opportunity
            for event in self.candidates
        ):
            raise ValueError("candidate carries a different opportunity-set hash")
        if (
            self.raw_candidate_set_sha256
            != canonical_raw_candidate_set_sha256(
                self.candidates,
                empty_reason=self.empty_reason,
            )
        ):
            raise ValueError("raw candidate-set hash mismatch")
        return self


class CandidateSetManifest(_EvidenceOnlyArtifact):
    """Paired dispositions referencing an already-persisted exact raw capture."""

    contract_version: Literal["shadow-candidate-set-v1"] = CANDIDATE_SET_VERSION
    candidate_set_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    raw_capture_id: NonEmptyString
    raw_capture_sha256: Sha256
    opportunity_set_id: NonEmptyString
    opportunity_set_sha256: Sha256
    candidate_source_id: NonEmptyString
    candidate_source_definition_sha256: Sha256
    trading_calendar_id: NonEmptyString
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    as_of_date: date
    captured_at: datetime
    raw_candidate_count: int = Field(ge=0)
    empty_reason: str | None = None
    raw_candidate_set_sha256: Sha256
    control_view: CandidateSetView
    challenger_view: CandidateSetView

    @field_validator("captured_at")
    @classmethod
    def require_aware_times(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("candidate-set datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_candidate_set(self) -> CandidateSetManifest:
        _verify_protocol_component(self.protocol_id, self.component_id)
        if self.control_view.side != "CONTROL":
            raise ValueError("control_view must have CONTROL side")
        if self.challenger_view.side != "CHALLENGER":
            raise ValueError("challenger_view must have CHALLENGER side")
        if self.control_view.input_event_ids != self.challenger_view.input_event_ids:
            raise ValueError("control/challenger raw input order differs")
        if len(self.control_view.input_event_ids) != self.raw_candidate_count:
            raise ValueError("paired view size differs from raw candidate count")
        if self.raw_candidate_count == 0 and not str(self.empty_reason or "").strip():
            raise ValueError("empty candidate set needs an explicit reason")
        if self.raw_candidate_count > 0 and self.empty_reason is not None:
            raise ValueError("non-empty candidate set cannot carry empty_reason")
        return self


def _verify_manifest_capture_pair(
    candidate_set: CandidateSetManifest,
    raw_capture: RawCandidateSetCapture,
) -> None:
    if candidate_set.raw_capture_sha256 != _required_hash(raw_capture):
        raise ShadowContractError("candidate-set raw-capture hash mismatch")
    event_ids = tuple(item.raw_event_id for item in raw_capture.candidates)
    if candidate_set.control_view.input_event_ids != event_ids:
        raise ShadowContractError("candidate-set view does not match raw capture order")
    for index, event in enumerate(raw_capture.candidates):
        if not isinstance(event, QuarantinedCandidateEvent):
            continue
        dispositions = (
            candidate_set.control_view.dispositions[index],
            candidate_set.challenger_view.dispositions[index],
        )
        if any(
            disposition.state != "PRUNED"
            or event.quarantine_reason not in disposition.reason_codes
            for disposition in dispositions
        ):
            raise ShadowContractError(
                "quarantined candidate must be pruned by both views with "
                "its quarantine reason"
            )
    actual = (
        candidate_set.raw_capture_id,
        candidate_set.protocol_id,
        candidate_set.component_id,
        candidate_set.manifest_sha256,
        candidate_set.opportunity_set_id,
        candidate_set.opportunity_set_sha256,
        candidate_set.candidate_source_id,
        candidate_set.candidate_source_definition_sha256,
        candidate_set.trading_calendar_id,
        candidate_set.trading_calendar_sha256,
        candidate_set.corporate_action_policy_sha256,
        candidate_set.as_of_date,
        candidate_set.raw_candidate_count,
        candidate_set.empty_reason,
        candidate_set.raw_candidate_set_sha256,
    )
    expected = (
        raw_capture.raw_capture_id,
        raw_capture.protocol_id,
        raw_capture.component_id,
        raw_capture.manifest_sha256,
        raw_capture.opportunity_set_id,
        raw_capture.opportunity_set_sha256,
        raw_capture.candidate_source_id,
        raw_capture.candidate_source_definition_sha256,
        raw_capture.trading_calendar_id,
        raw_capture.trading_calendar_sha256,
        raw_capture.corporate_action_policy_sha256,
        raw_capture.as_of_date,
        raw_capture.raw_candidate_count,
        raw_capture.empty_reason,
        raw_capture.raw_candidate_set_sha256,
    )
    if actual != expected:
        raise ShadowContractError("candidate-set identity differs from raw capture")
    if candidate_set.captured_at < raw_capture.captured_at:
        raise ShadowContractError("paired views cannot predate their raw capture")


class OpportunitySetParity(_EvidenceOnlyArtifact):
    """Positive proof that two paired views use identical raw inputs."""

    contract_version: Literal["shadow-opportunity-parity-v1"] = (
        PARITY_RESULT_VERSION
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    left_candidate_set_id: NonEmptyString
    right_candidate_set_id: NonEmptyString
    left_raw_capture_id: NonEmptyString
    right_raw_capture_id: NonEmptyString
    opportunity_set_id: NonEmptyString
    opportunity_set_sha256: Sha256
    raw_candidate_set_sha256: Sha256
    empty_reason: str | None = None
    event_ids_sha256: Sha256
    event_count: int = Field(ge=0)
    exact_match: Literal[True] = True


def assert_opportunity_set_parity(
    left: CandidateSetManifest,
    left_raw: RawCandidateSetCapture,
    right: CandidateSetManifest,
    right_raw: RawCandidateSetCapture,
) -> OpportunitySetParity:
    """Fail closed unless both sides reference byte-equivalent raw captures."""

    left = _revalidate(CandidateSetManifest, left)
    right = _revalidate(CandidateSetManifest, right)
    left_raw = _revalidate(RawCandidateSetCapture, left_raw)
    right_raw = _revalidate(RawCandidateSetCapture, right_raw)
    _verify_manifest_capture_pair(left, left_raw)
    _verify_manifest_capture_pair(right, right_raw)
    left_ids = tuple(item.raw_event_id for item in left_raw.candidates)
    right_ids = tuple(item.raw_event_id for item in right_raw.candidates)
    comparable = (
        left.protocol_id,
        left.component_id,
        left.manifest_sha256,
        left.opportunity_set_id,
        left.opportunity_set_sha256,
        left.as_of_date,
        left.raw_candidate_set_sha256,
        left.empty_reason,
        left_ids,
    )
    other = (
        right.protocol_id,
        right.component_id,
        right.manifest_sha256,
        right.opportunity_set_id,
        right.opportunity_set_sha256,
        right.as_of_date,
        right.raw_candidate_set_sha256,
        right.empty_reason,
        right_ids,
    )
    if comparable != other:
        raise ShadowContractError("opportunity-set parity mismatch")
    return OpportunitySetParity(
        protocol_id=left.protocol_id,
        component_id=left.component_id,
        manifest_sha256=left.manifest_sha256,
        left_candidate_set_id=left.candidate_set_id,
        right_candidate_set_id=right.candidate_set_id,
        left_raw_capture_id=left_raw.raw_capture_id,
        right_raw_capture_id=right_raw.raw_capture_id,
        opportunity_set_id=left.opportunity_set_id,
        opportunity_set_sha256=left.opportunity_set_sha256,
        raw_candidate_set_sha256=left.raw_candidate_set_sha256,
        empty_reason=left.empty_reason,
        event_ids_sha256=canonical_payload_sha256({"event_ids": list(left_ids)}),
        event_count=len(left_ids),
    )


def verify_opportunity_set_parity(
    proof: OpportunitySetParity,
    left: CandidateSetManifest,
    left_raw: RawCandidateSetCapture,
    right: CandidateSetManifest,
    right_raw: RawCandidateSetCapture,
) -> OpportunitySetParity:
    """Rebuild a parity certificate from both exact raw captures."""

    trusted = _revalidate(OpportunitySetParity, proof)
    rebuilt = assert_opportunity_set_parity(
        left,
        left_raw,
        right,
        right_raw,
    )
    if canonical_sha256(trusted) != canonical_sha256(rebuilt):
        raise ShadowContractError(
            "opportunity parity proof differs from exact-artifact reconstruction"
        )
    return trusted


class LineageBundle(_EvidenceOnlyArtifact):
    """Validated hash chain from actual protocol inputs to optional outcome."""

    contract_version: Literal["shadow-lineage-bundle-v1"] = LINEAGE_BUNDLE_VERSION
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    raw_capture_id: NonEmptyString
    raw_capture_sha256: Sha256
    candidate_set_id: NonEmptyString
    candidate_set_sha256: Sha256
    raw_candidate_set_sha256: Sha256
    opportunity_set_id: NonEmptyString
    opportunity_set_sha256: Sha256
    raw_event_id: NonEmptyString
    candidate_sha256: Sha256
    ticker: CanonicalTicker
    signal_at: datetime
    as_of_date: date
    snapshot_id: NonEmptyString
    snapshot_sha256: Sha256
    frozen_snapshot_sha256: Sha256
    snapshot_as_of: datetime
    snapshot_source_id: NonEmptyString
    snapshot_source_definition_sha256: Sha256
    snapshot_source_record_sha256: Sha256
    snapshot_source_expires_at: datetime | None = None
    candidate_source_id: NonEmptyString
    candidate_source_definition_sha256: Sha256
    candidate_source_sha256: Sha256
    candidate_source_as_of: datetime
    candidate_source_expires_at: datetime | None = None
    source_expired_at_signal: Literal[False] = False
    trading_calendar_id: NonEmptyString
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    corporate_action_events_at_signal_sha256: Sha256
    observation_id: NonEmptyString
    observation_sha256: Sha256
    decision_role: Literal["CONTROL", "CHALLENGER"] | None = None
    outcome_id: str | None = None
    outcome_sha256: Sha256 | None = None
    outcome_horizon_trading_days: Literal[3, 5, 10, 15] | None = None
    outcome_source_id: str | None = None
    outcome_source_definition_sha256: Sha256 | None = None
    outcome_source_sha256: Sha256 | None = None
    outcome_source_as_of: datetime | None = None
    outcome_bars_sha256: Sha256 | None = None
    outcome_bar_record_sha256s: tuple[Sha256, ...] | None = None
    outcome_bar_series_sha256: Sha256 | None = None
    outcome_corporate_action_policy_sha256: Sha256 | None = None
    outcome_corporate_action_events_sha256: Sha256 | None = None
    outcome_corporate_action_event_record_sha256s: (
        tuple[Sha256, ...] | None
    ) = None
    lineage_valid: Literal[True] = True

    @field_validator(
        "signal_at",
        "snapshot_as_of",
        "snapshot_source_expires_at",
        "candidate_source_as_of",
        "candidate_source_expires_at",
        "outcome_source_as_of",
    )
    @classmethod
    def require_aware_lineage_times(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("lineage source times must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_optional_outcome(self) -> LineageBundle:
        optional_outcome_fields = (
            self.decision_role,
            self.outcome_id,
            self.outcome_sha256,
            self.outcome_horizon_trading_days,
            self.outcome_source_id,
            self.outcome_source_definition_sha256,
            self.outcome_source_sha256,
            self.outcome_source_as_of,
            self.outcome_bars_sha256,
            self.outcome_bar_record_sha256s,
            self.outcome_bar_series_sha256,
            self.outcome_corporate_action_policy_sha256,
            self.outcome_corporate_action_events_sha256,
            self.outcome_corporate_action_event_record_sha256s,
        )
        if any(value is not None for value in optional_outcome_fields) and any(
            value is None for value in optional_outcome_fields
        ):
            raise ValueError("complete outcome lineage fields must appear together")
        return self


def build_lineage_bundle(
    protocol_manifest: ShadowProtocolManifest,
    frozen_snapshot: FrozenSnapshot,
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
    candidate: CandidateEvent,
    observation: ShadowObservation,
    *,
    bar_series: FrozenBarSeries | BaseModel | None = None,
    outcome: ShadowOutcome | None = None,
) -> LineageBundle:
    """Recompute and verify every available evidence edge.

    ``bar_series`` is deliberately duck-typed.  Importing this module never
    imports the outcome engine at runtime, avoiding a dependency cycle.
    """

    protocol_manifest = _revalidate(ShadowProtocolManifest, protocol_manifest)
    frozen_snapshot = _revalidate(FrozenSnapshot, frozen_snapshot)
    raw_capture = _revalidate(RawCandidateSetCapture, raw_capture)
    candidate_set = _revalidate(CandidateSetManifest, candidate_set)
    candidate = _revalidate(CandidateEvent, candidate)
    observation = _revalidate(ShadowObservation, observation)
    if outcome is not None:
        outcome = _revalidate(ShadowOutcome, outcome)
    if (outcome is None) != (bar_series is None):
        raise ShadowContractError(
            "outcome and exact frozen bar series must be supplied together"
        )
    if bar_series is not None:
        bar_series = _revalidate_dynamic(bar_series)

    manifest_hash = _required_hash(protocol_manifest)
    _require_equal(
        "protocol identity",
        (
            raw_capture.protocol_id,
            raw_capture.component_id,
            raw_capture.manifest_sha256,
        ),
        (
            protocol_manifest.protocol_id,
            protocol_manifest.component_id,
            manifest_hash,
        ),
    )
    _verify_manifest_capture_pair(candidate_set, raw_capture)
    if not (
        candidate.captured_at
        <= raw_capture.captured_at
        <= candidate_set.captured_at
        <= observation.captured_at
    ):
        raise ShadowContractError("evidence capture chronology is not causal")
    member = next(
        (
            event
            for event in raw_capture.candidates
            if event.raw_event_id == candidate.raw_event_id
        ),
        None,
    )
    if member is None:
        raise ShadowContractError("candidate is not a member of its raw capture")
    if not isinstance(member, CandidateEvent):
        raise ShadowContractError("quarantined candidate cannot produce observation")
    if _required_hash(member) != _required_hash(candidate):
        raise ShadowContractError("candidate content differs from raw-capture member")
    if (
        protocol_manifest.universe.explicit_tickers
        and candidate.ticker not in protocol_manifest.universe.explicit_tickers
    ):
        raise ShadowContractError("candidate ticker is outside the frozen universe")

    candidate_set_hash = _required_hash(candidate_set)
    raw_capture_hash = _required_hash(raw_capture)
    observation_hash = _required_hash(observation)
    if candidate_set.raw_capture_sha256 != raw_capture_hash:
        raise ShadowContractError("candidate set does not bind exact raw capture")
    if (
        observation.candidate_set_id != candidate_set.candidate_set_id
        or observation.candidate_set_sha256 != candidate_set_hash
    ):
        raise ShadowContractError("observation is not bound to candidate-set artifact")

    _require_equal(
        "candidate/observation identity",
        (
            candidate.protocol_id,
            candidate.component_id,
            candidate.manifest_sha256,
            candidate.opportunity_set_id,
            candidate.opportunity_set_sha256,
            candidate.raw_event_id,
            candidate.ticker,
            candidate.signal_at,
            candidate.as_of_date,
            candidate.snapshot_id,
            candidate.snapshot_sha256,
        ),
        (
            observation.protocol_id,
            observation.component_id,
            observation.manifest_sha256,
            observation.opportunity_set_id,
            observation.opportunity_set_sha256,
            observation.raw_event_id,
            observation.ticker,
            observation.signal_at,
            observation.as_of_date,
            observation.snapshot_id,
            observation.snapshot_sha256,
        ),
    )
    if frozen_snapshot.snapshot_as_of > candidate.signal_at:
        raise ShadowContractError("snapshot vintage is after candidate signal")
    if (
        frozen_snapshot.source_expires_at is not None
        and frozen_snapshot.source_expires_at <= candidate.signal_at
    ):
        raise ShadowContractError("snapshot source record expired by candidate signal")
    _require_equal(
        "candidate/frozen-snapshot identity",
        (
            candidate.snapshot_id,
            candidate.snapshot_sha256,
            candidate.ticker,
            candidate.as_of_date,
            candidate.snapshot_as_of,
            candidate.snapshot_source_record_sha256,
        ),
        (
            frozen_snapshot.snapshot_id,
            frozen_snapshot.snapshot_sha256,
            frozen_snapshot.ticker,
            frozen_snapshot.as_of_date,
            frozen_snapshot.snapshot_as_of,
            frozen_snapshot.source_record_sha256,
        ),
    )

    candidate_definition = _manifest_source_definition_hash(
        protocol_manifest,
        candidate.candidate_source_id,
    )
    snapshot_definition = _manifest_source_definition_hash(
        protocol_manifest,
        frozen_snapshot.source_id,
    )
    if candidate_definition != candidate.candidate_source_definition_sha256:
        raise ShadowContractError(
            "candidate source definition is not the frozen manifest definition"
        )
    if (
        candidate.candidate_source_definition_sha256
        != protocol_manifest.universe.candidate_source_sha256
    ):
        raise ShadowContractError(
            "candidate source definition differs from manifest universe"
        )
    if snapshot_definition != frozen_snapshot.source_definition_sha256:
        raise ShadowContractError(
            "snapshot source definition is not the frozen manifest definition"
        )
    _require_equal(
        "manifest/candidate market policies",
        (
            protocol_manifest.trading_calendar_id,
            protocol_manifest.trading_calendar_sha256,
            protocol_manifest.corporate_action_policy_sha256,
        ),
        (
            candidate.trading_calendar_id,
            candidate.trading_calendar_sha256,
            candidate.corporate_action_policy_sha256,
        ),
    )

    decision_role: Literal["CONTROL", "CHALLENGER"] | None = None
    outcome_id: str | None = None
    outcome_hash: str | None = None
    outcome_horizon: Literal[3, 5, 10, 15] | None = None
    outcome_source_id: str | None = None
    outcome_source_definition_sha256: str | None = None
    outcome_source_sha256: str | None = None
    outcome_source_as_of: datetime | None = None
    outcome_bars_sha256: str | None = None
    outcome_bar_record_sha256s: tuple[str, ...] | None = None
    outcome_bar_series_sha256: str | None = None
    outcome_policy_sha256: str | None = None
    outcome_events_sha256: str | None = None
    outcome_event_record_sha256s: tuple[str, ...] | None = None

    if outcome is not None and bar_series is not None:
        expected_decision = (
            observation.control_decision
            if outcome.decision_role == "CONTROL"
            else observation.challenger_decision
        )
        expected_geometry = canonical_sha256(expected_decision.geometry) or ("0" * 64)
        if outcome.planned_geometry_sha256 != expected_geometry:
            raise ShadowContractError("outcome geometry does not match decision side")
        expected_outcome_id = canonical_outcome_id(
            protocol_id=observation.protocol_id,
            manifest_sha256=observation.manifest_sha256,
            observation_id=observation.observation_id,
            raw_event_id=observation.raw_event_id,
            ticker=observation.ticker,
            signal_at=observation.signal_at,
            decision_role=outcome.decision_role,
            horizon_trading_days=outcome.horizon_trading_days,
        )
        if outcome.outcome_id != expected_outcome_id:
            raise ShadowContractError("outcome identity is not deterministic")
        if (
            outcome.label_definition_sha256
            != _required_hash(protocol_manifest.labels)
            or outcome.cost_assumptions_sha256
            != _required_hash(protocol_manifest.costs)
        ):
            raise ShadowContractError(
                "outcome label/cost definitions differ from frozen manifest"
            )
        _require_equal(
            "observation/outcome identity",
            (
                observation.protocol_id,
                observation.component_id,
                observation.manifest_sha256,
                candidate_set_hash,
                observation.observation_id,
                observation.raw_event_id,
                observation.independent_cluster_id,
                observation.ticker,
                observation.signal_at,
                observation.snapshot_id,
                observation.snapshot_sha256,
                expected_decision.decision_role,
            ),
            (
                outcome.protocol_id,
                outcome.component_id,
                outcome.manifest_sha256,
                outcome.candidate_set_sha256,
                outcome.observation_id,
                outcome.raw_event_id,
                outcome.independent_cluster_id,
                outcome.ticker,
                outcome.signal_at,
                outcome.snapshot_id,
                outcome.snapshot_sha256,
                outcome.decision_role,
            ),
        )
        outcome_definition = _manifest_source_definition_hash(
            protocol_manifest,
            outcome.outcome_source_id,
        )
        for event in bar_series.corporate_action_policy.events:
            event_definition = _manifest_source_definition_hash(
                protocol_manifest,
                event.source_id,
            )
            if event_definition != event.source_definition_sha256:
                raise ShadowContractError(
                    "corporate-action source definition is not the frozen "
                    "manifest definition"
                )
        _require_bar_lineage(
            bar_series=bar_series,
            outcome=outcome,
            observation=observation,
            outcome_source_definition_sha256=outcome_definition,
            trading_calendar_sha256=protocol_manifest.trading_calendar_sha256,
            corporate_action_policy_sha256=(
                protocol_manifest.corporate_action_policy_sha256
            ),
            corporate_action_events_at_signal_sha256=(
                candidate.corporate_action_events_at_signal_sha256
            ),
            decision_geometry=expected_decision.geometry,
        )
        decision_role = outcome.decision_role
        outcome_id = outcome.outcome_id
        outcome_hash = _required_hash(outcome)
        outcome_horizon = outcome.horizon_trading_days
        outcome_source_id = outcome.outcome_source_id
        outcome_source_definition_sha256 = (
            outcome.outcome_source_definition_sha256
        )
        outcome_source_sha256 = outcome.outcome_source_sha256
        outcome_source_as_of = outcome.outcome_source_as_of
        outcome_bars_sha256 = outcome.outcome_bars_sha256
        outcome_bar_record_sha256s = outcome.outcome_bar_record_sha256s
        outcome_bar_series_sha256 = _required_hash(bar_series)
        outcome_policy_sha256 = outcome.corporate_action_policy_sha256
        outcome_events_sha256 = outcome.corporate_action_events_sha256
        outcome_event_record_sha256s = (
            outcome.corporate_action_event_record_sha256s
        )

    return LineageBundle(
        protocol_id=observation.protocol_id,
        component_id=observation.component_id,
        manifest_sha256=manifest_hash,
        raw_capture_id=raw_capture.raw_capture_id,
        raw_capture_sha256=raw_capture_hash,
        candidate_set_id=candidate_set.candidate_set_id,
        candidate_set_sha256=candidate_set_hash,
        raw_candidate_set_sha256=raw_capture.raw_candidate_set_sha256,
        opportunity_set_id=observation.opportunity_set_id,
        opportunity_set_sha256=observation.opportunity_set_sha256,
        raw_event_id=observation.raw_event_id,
        candidate_sha256=_required_hash(candidate),
        ticker=observation.ticker,
        signal_at=observation.signal_at,
        as_of_date=observation.as_of_date,
        snapshot_id=frozen_snapshot.snapshot_id,
        snapshot_sha256=frozen_snapshot.snapshot_sha256,
        frozen_snapshot_sha256=_required_hash(frozen_snapshot),
        snapshot_as_of=frozen_snapshot.snapshot_as_of,
        snapshot_source_id=frozen_snapshot.source_id,
        snapshot_source_definition_sha256=(
            frozen_snapshot.source_definition_sha256
        ),
        snapshot_source_record_sha256=frozen_snapshot.source_record_sha256,
        snapshot_source_expires_at=frozen_snapshot.source_expires_at,
        candidate_source_id=candidate.candidate_source_id,
        candidate_source_definition_sha256=(
            candidate.candidate_source_definition_sha256
        ),
        candidate_source_sha256=candidate.candidate_source_sha256,
        candidate_source_as_of=candidate.candidate_source_as_of,
        candidate_source_expires_at=candidate.candidate_source_expires_at,
        trading_calendar_id=candidate.trading_calendar_id,
        trading_calendar_sha256=candidate.trading_calendar_sha256,
        corporate_action_policy_sha256=(
            candidate.corporate_action_policy_sha256
        ),
        corporate_action_events_at_signal_sha256=(
            candidate.corporate_action_events_at_signal_sha256
        ),
        observation_id=observation.observation_id,
        observation_sha256=observation_hash,
        decision_role=decision_role,
        outcome_id=outcome_id,
        outcome_sha256=outcome_hash,
        outcome_horizon_trading_days=outcome_horizon,
        outcome_source_id=outcome_source_id,
        outcome_source_definition_sha256=outcome_source_definition_sha256,
        outcome_source_sha256=outcome_source_sha256,
        outcome_source_as_of=outcome_source_as_of,
        outcome_bars_sha256=outcome_bars_sha256,
        outcome_bar_record_sha256s=outcome_bar_record_sha256s,
        outcome_bar_series_sha256=outcome_bar_series_sha256,
        outcome_corporate_action_policy_sha256=outcome_policy_sha256,
        outcome_corporate_action_events_sha256=outcome_events_sha256,
        outcome_corporate_action_event_record_sha256s=(
            outcome_event_record_sha256s
        ),
    )


def verify_lineage_bundle(
    bundle: LineageBundle,
    protocol_manifest: ShadowProtocolManifest,
    frozen_snapshot: FrozenSnapshot,
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
    candidate: CandidateEvent,
    observation: ShadowObservation,
    *,
    bar_series: FrozenBarSeries | BaseModel | None = None,
    outcome: ShadowOutcome | None = None,
) -> LineageBundle:
    """Rebuild a lineage proof from exact artifacts and compare canonically."""

    trusted = _revalidate(LineageBundle, bundle)
    rebuilt = build_lineage_bundle(
        protocol_manifest,
        frozen_snapshot,
        raw_capture,
        candidate_set,
        candidate,
        observation,
        bar_series=bar_series,
        outcome=outcome,
    )
    if canonical_sha256(trusted) != canonical_sha256(rebuilt):
        raise ShadowContractError(
            "lineage bundle differs from exact-artifact reconstruction"
        )
    return trusted


class CandidateSetStore:
    """Raw-first content store using exclusive creation and strict reloads."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def persist_raw(self, raw_capture: RawCandidateSetCapture) -> Path:
        """Persist the raw capture before any paired-view manifest may exist."""

        trusted = _revalidate(RawCandidateSetCapture, raw_capture)
        path = self._path_for(
            trusted.protocol_id,
            trusted.manifest_sha256,
            "raw_candidate_sets",
            trusted.raw_capture_id,
        )
        return self._exclusive_create(path, canonical_json_bytes(trusted))

    def persist(self, candidate_set: CandidateSetManifest) -> Path:
        """Persist paired views only after loading the exact raw capture."""

        trusted = _revalidate(CandidateSetManifest, candidate_set)
        raw = self.load_raw(
            trusted.raw_capture_id,
            protocol_id=trusted.protocol_id,
            manifest_sha256=trusted.manifest_sha256,
        )
        _verify_manifest_capture_pair(trusted, raw)
        path = self._path_for(
            trusted.protocol_id,
            trusted.manifest_sha256,
            "candidate_sets",
            trusted.candidate_set_id,
        )
        return self._exclusive_create(path, canonical_json_bytes(trusted))

    def persist_manifest(self, candidate_set: CandidateSetManifest) -> Path:
        """Explicit alias documenting the second phase of raw-first storage."""

        return self.persist(candidate_set)

    def load_raw(
        self,
        raw_capture_id: str,
        *,
        protocol_id: str,
        manifest_sha256: str,
    ) -> RawCandidateSetCapture:
        path = self._path_for(
            protocol_id,
            manifest_sha256,
            "raw_candidate_sets",
            raw_capture_id,
        )
        return self._load_artifact(
            path,
            RawCandidateSetCapture,
            raw_capture_id,
            "raw_capture_id",
            protocol_id,
            manifest_sha256,
        )

    def load(
        self,
        candidate_set_id: str,
        *,
        protocol_id: str,
        manifest_sha256: str,
    ) -> CandidateSetManifest:
        path = self._path_for(
            protocol_id,
            manifest_sha256,
            "candidate_sets",
            candidate_set_id,
        )
        candidate_set = self._load_artifact(
            path,
            CandidateSetManifest,
            candidate_set_id,
            "candidate_set_id",
            protocol_id,
            manifest_sha256,
        )
        raw = self.load_raw(
            candidate_set.raw_capture_id,
            protocol_id=protocol_id,
            manifest_sha256=manifest_sha256,
        )
        _verify_manifest_capture_pair(candidate_set, raw)
        return candidate_set

    @staticmethod
    def _exclusive_create(path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
                handle.flush()
        except FileExistsError as exc:
            try:
                existing = path.read_bytes()
            except OSError as read_exc:
                raise ShadowContractError(
                    "existing evidence artifact cannot be verified"
                ) from read_exc
            if existing == payload:
                return path
            raise ShadowContractError(
                "evidence artifact exists with different content"
            ) from exc
        return path

    @staticmethod
    def _load_artifact(
        path: Path,
        model: type[RawCandidateSetCapture] | type[CandidateSetManifest],
        expected_id: str,
        id_field: str,
        protocol_id: str,
        manifest_sha256: str,
    ) -> RawCandidateSetCapture | CandidateSetManifest:
        try:
            payload = path.read_bytes()
        except FileNotFoundError as exc:
            raise ShadowContractError("evidence artifact does not exist") from exc
        try:
            artifact = model.model_validate_json(payload)
        except Exception as exc:
            raise ShadowContractError("evidence artifact is invalid") from exc
        if getattr(artifact, id_field) != expected_id:
            raise ShadowContractError("evidence artifact path/identity mismatch")
        if (
            artifact.protocol_id != protocol_id
            or artifact.manifest_sha256 != manifest_sha256
        ):
            raise ShadowContractError("evidence artifact namespace mismatch")
        if canonical_json_bytes(artifact) != payload:
            raise ShadowContractError("evidence artifact is not canonical JSON")
        return artifact

    def _path_for(
        self,
        protocol_id: str,
        manifest_sha256: str,
        artifact_kind: Literal["candidate_sets", "raw_candidate_sets"],
        artifact_id: str,
    ) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", protocol_id):
            raise ShadowContractError("unsafe protocol_id")
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise ShadowContractError("unsafe manifest_sha256")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", artifact_id):
            raise ShadowContractError("unsafe evidence artifact ID")
        namespace = (
            self.root / protocol_id / manifest_sha256 / artifact_kind
        ).resolve()
        path = (namespace / f"{artifact_id}.json").resolve()
        if path.parent != namespace:
            raise ShadowContractError("evidence artifact path escaped its root")
        return path


def _require_bar_lineage(
    *,
    bar_series: BaseModel,
    outcome: ShadowOutcome,
    observation: ShadowObservation,
    outcome_source_definition_sha256: str,
    trading_calendar_sha256: str,
    corporate_action_policy_sha256: str,
    corporate_action_events_at_signal_sha256: str,
    decision_geometry: BaseModel | None,
) -> None:
    required_attributes = (
        "ticker",
        "snapshot_id",
        "snapshot_sha256",
        "source_id",
        "source_sha256",
        "source_as_of",
        "bars",
        "bars_sha256",
        "bar_record_sha256s",
        "corporate_action_policy",
        "source_definition_sha256",
    )
    if any(not hasattr(bar_series, name) for name in required_attributes):
        raise ShadowContractError("bar series does not expose required lineage fields")
    policy = bar_series.corporate_action_policy
    if any(
        not hasattr(policy, name)
        for name in ("policy_sha256", "events_sha256", "events")
    ):
        raise ShadowContractError("corporate-action policy lineage is incomplete")
    _require_equal(
        "bar-series observation identity",
        (
            bar_series.ticker,
            bar_series.snapshot_id,
            bar_series.snapshot_sha256,
        ),
        (
            observation.ticker,
            observation.snapshot_id,
            observation.snapshot_sha256,
        ),
    )
    event_record_hashes = tuple(event.source_sha256 for event in policy.events)
    _require_equal(
        "outcome/bar-series source lineage",
        (
            outcome.outcome_source_id,
            outcome.outcome_source_definition_sha256,
            outcome.outcome_source_sha256,
            outcome.outcome_source_as_of,
            outcome.outcome_bars_sha256,
            outcome.outcome_bar_record_sha256s,
            outcome.corporate_action_policy_sha256,
            outcome.corporate_action_events_sha256,
            outcome.corporate_action_event_ids,
            outcome.corporate_action_event_record_sha256s,
            outcome.corporate_action_event_published_ats,
            outcome.trading_calendar_sha256,
        ),
        (
            bar_series.source_id,
            bar_series.source_definition_sha256,
            bar_series.source_sha256,
            bar_series.source_as_of,
            bar_series.bars_sha256,
            bar_series.bar_record_sha256s,
            policy.policy_sha256,
            policy.events_sha256,
            tuple(event.event_id for event in policy.events),
            event_record_hashes,
            tuple(event.published_at for event in policy.events),
            trading_calendar_sha256,
        ),
    )
    if bar_series.source_definition_sha256 != outcome_source_definition_sha256:
        raise ShadowContractError(
            "bar source definition differs from frozen manifest"
        )
    if policy.policy_sha256 != corporate_action_policy_sha256:
        raise ShadowContractError(
            "outcome corporate-action policy differs from frozen manifest"
        )
    events_known_at_signal = tuple(
        event
        for event in policy.events
        if event.published_at <= observation.signal_at
    )
    expected_signal_events_hash = canonical_payload_sha256(
        {"events": [_required_hash(item) for item in events_known_at_signal]}
    )
    if expected_signal_events_hash != corporate_action_events_at_signal_sha256:
        raise ShadowContractError(
            "corporate-action events-at-signal lineage mismatch"
        )
    applied_split_events = tuple(
        event
        for event in policy.events
        if (
            event.kind == "SPLIT"
            and event.effective_date
            > observation.signal_at.astimezone(IDX_TIMEZONE).date()
            and event.effective_date
            <= outcome.evaluated_at.astimezone(IDX_TIMEZONE).date()
        )
    )
    if applied_split_events:
        if decision_geometry is None:
            raise ShadowContractError(
                "split adjustment lineage needs decision geometry"
            )
        expected_adjustment = canonical_payload_sha256(
            {
                "corporate_actions": [
                    _required_hash(event) for event in applied_split_events
                ],
                "geometry_sha256": _required_hash(decision_geometry),
                "price_basis": "RAW_AS_TRADED",
            }
        )
    else:
        expected_adjustment = None
    if outcome.corporate_action_adjustment != expected_adjustment:
        raise ShadowContractError(
            "outcome corporate-action adjustment mismatch"
        )


def _manifest_source_definition_hash(
    manifest: ShadowProtocolManifest,
    source_id: str,
) -> str:
    matches = tuple(item for item in manifest.sources if item.source_id == source_id)
    if len(matches) != 1:
        raise ShadowContractError(
            f"manifest must contain exactly one source definition for {source_id}"
        )
    return _required_hash(matches[0])


def _revalidate(model: type[BaseModel], value: BaseModel):
    try:
        return model.model_validate(value.model_dump(mode="python"))
    except Exception as exc:
        raise ShadowContractError(
            f"{model.__name__} failed trust-boundary validation"
        ) from exc


def _revalidate_dynamic(value: BaseModel) -> BaseModel:
    if not isinstance(value, BaseModel):
        raise ShadowContractError("bar series must be a validated Pydantic model")
    try:
        return value.__class__.model_validate(value.model_dump(mode="python"))
    except Exception as exc:
        raise ShadowContractError(
            "bar series failed trust-boundary validation"
        ) from exc


def _required_hash(model: BaseModel | None) -> str:
    digest = canonical_sha256(model)
    if digest is None:
        raise ShadowContractError("cannot hash missing evidence model")
    return digest


def _require_equal(label: str, actual: tuple[object, ...], expected: tuple[object, ...]) -> None:
    if actual != expected:
        raise ShadowContractError(f"{label} mismatch")


def _payload_ticker(payload_json: str) -> str | None:
    payload = json.loads(payload_json)
    for key in ("ticker", "Ticker", "symbol", "Symbol"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip().upper()
        if text.endswith(".JK"):
            text = text[:-3]
        return text
    return None


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _validate_json_value(value: object, *, path: str, seen: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON number at {path}")
        return
    if isinstance(value, list):
        identity = id(value)
        if identity in seen:
            raise ValueError(f"cyclic JSON list at {path}")
        seen.add(identity)
        try:
            for index, item in enumerate(value):
                _validate_json_value(item, path=f"{path}[{index}]", seen=seen)
        finally:
            seen.remove(identity)
        return
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            raise ValueError(f"cyclic JSON object at {path}")
        seen.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError(f"non-string JSON key at {path}")
                _validate_json_value(item, path=f"{path}.{key}", seen=seen)
        finally:
            seen.remove(identity)
        return
    raise ValueError(f"non-JSON value {type(value).__name__} at {path}")


__all__ = [
    "CANDIDATE_EVENT_VERSION",
    "CANDIDATE_SET_VERSION",
    "FROZEN_SNAPSHOT_VERSION",
    "LINEAGE_BUNDLE_VERSION",
    "PARITY_RESULT_VERSION",
    "QUARANTINED_CANDIDATE_EVENT_VERSION",
    "RAW_CANDIDATE_SET_VERSION",
    "CandidateDisposition",
    "CandidateEvent",
    "CandidateRecord",
    "CandidateSetManifest",
    "CandidateSetStore",
    "CandidateSetView",
    "FrozenSnapshot",
    "LineageBundle",
    "OpportunitySetParity",
    "QuarantinedCandidateEvent",
    "RawCandidateSetCapture",
    "assert_opportunity_set_parity",
    "build_lineage_bundle",
    "canonical_frozen_snapshot_sha256",
    "canonical_opportunity_set_sha256",
    "canonical_payload_bytes",
    "canonical_payload_json",
    "canonical_payload_sha256",
    "canonical_raw_candidate_set_sha256",
    "canonical_raw_event_id",
    "canonical_source_record_sha256",
    "canonical_view_sha256",
    "verify_lineage_bundle",
    "verify_opportunity_set_parity",
]
