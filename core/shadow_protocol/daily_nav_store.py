"""Immutable content-addressed storage for RS-P2-017 daily-NAV artifacts.

This module intentionally stops at a locally replayable, append-only snapshot.
The snapshot is always labelled ``UNANCHORED_NOT_CERTIFIED_COMPLETE``.  An
independently authenticated ledger/run commitment is an RS-P2-019 concern and
is not simulated by discovering a local tail.
"""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json
from pathlib import Path
import re
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, model_validator

from .contracts import (
    ShadowContractError,
    ShadowProtocolManifest,
    canonical_json_bytes,
    canonical_sha256,
)
from .daily_nav import (
    DAILY_NAV_CONFIG_PATH,
    DAILY_NAV_POINT_VERSION,
    DAILY_NAV_POLICY_VERSION,
    NAV_CHAIN_COMPLETENESS_STATUS,
    NAV_MARK_INPUT_VERSION,
    NAV_SERIES_EVENT_VERSION,
    NAV_SERIES_SNAPSHOT_VERSION,
    DailyNavNamedPredecessor,
    DailyNavPoint,
    FrozenDailyNavPolicy,
    NavMarkInput,
    NavSeriesEvent,
    NavSeriesSnapshot,
    load_daily_nav_point_v1,
    load_daily_nav_policy_v1,
    load_nav_mark_input_v1,
    load_nav_series_event_v1,
    load_nav_series_snapshot_v1,
    replay_nav_series_snapshot,
)


DAILY_NAV_REFERENCE_VERSION = "shadow-daily-nav-reference-v1"

ArtifactKind: TypeAlias = Literal[
    "POLICY",
    "NAV_MARK_INPUT",
    "NAV_POINT",
    "NAV_SERIES_EVENT",
    "NAV_SERIES_SNAPSHOT",
]
DailyNavArtifact: TypeAlias = (
    FrozenDailyNavPolicy
    | NavMarkInput
    | DailyNavPoint
    | NavSeriesEvent
    | NavSeriesSnapshot
)

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}")
_SHA256 = re.compile(r"[0-9a-f]{64}")

_NAMESPACE_BY_KIND: dict[ArtifactKind, str] = {
    "POLICY": "policies",
    "NAV_MARK_INPUT": "nav_mark_inputs",
    "NAV_POINT": "nav_points",
    "NAV_SERIES_EVENT": "nav_series_events",
    "NAV_SERIES_SNAPSHOT": "nav_series_snapshots",
}
_VERSION_BY_KIND: dict[ArtifactKind, str] = {
    "POLICY": DAILY_NAV_POLICY_VERSION,
    "NAV_MARK_INPUT": NAV_MARK_INPUT_VERSION,
    "NAV_POINT": DAILY_NAV_POINT_VERSION,
    "NAV_SERIES_EVENT": NAV_SERIES_EVENT_VERSION,
    "NAV_SERIES_SNAPSHOT": NAV_SERIES_SNAPSHOT_VERSION,
}
_ID_ATTRIBUTE_BY_KIND: dict[ArtifactKind, str] = {
    "POLICY": "policy_id",
    "NAV_MARK_INPUT": "mark_input_id",
    "NAV_POINT": "point_id",
    "NAV_SERIES_EVENT": "event_id",
    "NAV_SERIES_SNAPSHOT": "snapshot_id",
}


class _StoreModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class DailyNavArtifactReference(_StoreModel):
    """Dual-hash immutable reference to one daily-NAV artifact."""

    contract_version: Literal["shadow-daily-nav-reference-v1"] = (
        DAILY_NAV_REFERENCE_VERSION
    )
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False

    protocol_id: str
    component_id: str
    manifest_sha256: str
    artifact_kind: ArtifactKind
    artifact_id: str
    artifact_contract_version: str
    artifact_canonical_sha256: str
    artifact_raw_file_sha256: str
    artifact_raw_byte_length: int
    artifact_relative_path: str
    predecessors: tuple[DailyNavNamedPredecessor, ...]

    @model_validator(mode="after")
    def verify_reference(self) -> DailyNavArtifactReference:
        for label, digest in (
            ("manifest", self.manifest_sha256),
            ("artifact canonical", self.artifact_canonical_sha256),
            ("artifact raw-file", self.artifact_raw_file_sha256),
        ):
            _require_sha256(digest, f"{label} SHA-256")
        if self.artifact_raw_byte_length <= 0:
            raise ValueError("daily-NAV reference byte length must be positive")
        names = tuple(item.name for item in self.predecessors)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError(
                "daily-NAV predecessor names must be unique and ordered"
            )
        if not self.artifact_relative_path or "\\" in self.artifact_relative_path:
            raise ValueError("daily-NAV reference path must be canonical POSIX")
        relative = Path(self.artifact_relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("daily-NAV reference path is not root-relative")
        return self


class DailyNavArtifactStore:
    """Exclusive-create store with strict v1 loaders and local replay.

    The store proves internal byte, model, predecessor, and snapshot-chain
    consistency.  It does not claim that a locally discovered snapshot is an
    independently authenticated completeness commitment.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def persist_policy(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_daily_nav_policy_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "POLICY", artifact)
        return self._persist(
            manifest,
            kind="POLICY",
            artifact=artifact,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_nav_mark_input(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_nav_mark_input_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "NAV_MARK_INPUT", artifact)
        return self._persist(
            manifest,
            kind="NAV_MARK_INPUT",
            artifact=artifact,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_nav_point(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_daily_nav_point_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "NAV_POINT", artifact)
        return self._persist(
            manifest,
            kind="NAV_POINT",
            artifact=artifact,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_nav_series_event(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_nav_series_event_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "NAV_SERIES_EVENT", artifact)
        return self._persist(
            manifest,
            kind="NAV_SERIES_EVENT",
            artifact=artifact,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_nav_series_snapshot(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_nav_series_snapshot_v1(raw_file_bytes)
        self._verify_manifest_lineage(
            manifest,
            "NAV_SERIES_SNAPSHOT",
            artifact,
        )
        if artifact.chain_completeness_status != NAV_CHAIN_COMPLETENESS_STATUS:
            raise ShadowContractError(
                "daily-NAV snapshot made an unsupported completeness claim"
            )
        self._verify_prior_snapshot(manifest, artifact)
        return self._persist(
            manifest,
            kind="NAV_SERIES_SNAPSHOT",
            artifact=artifact,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_snapshot_bundle(
        self,
        manifest: ShadowProtocolManifest,
        *,
        policy_raw_file_bytes: bytes,
        snapshot_raw_file_bytes: bytes,
    ) -> dict[str, tuple[Path, ...]]:
        """Persist one policy and every independently addressable snapshot node."""

        manifest = _revalidate_manifest(manifest)
        policy = load_daily_nav_policy_v1(policy_raw_file_bytes)
        snapshot = load_nav_series_snapshot_v1(snapshot_raw_file_bytes)
        self._verify_manifest_lineage(manifest, "POLICY", policy)
        self._verify_manifest_lineage(
            manifest,
            "NAV_SERIES_SNAPSHOT",
            snapshot,
        )
        self._verify_policy_binding(policy, snapshot)
        self._verify_snapshot_node_identity(snapshot)
        self._verify_prior_snapshot(manifest, snapshot)

        paths: dict[str, list[Path]] = {
            namespace: [] for namespace in _NAMESPACE_BY_KIND.values()
        }

        def persist(
            kind: ArtifactKind,
            artifact: DailyNavArtifact,
            raw: bytes | None = None,
        ) -> None:
            path = self._persist(
                manifest,
                kind=kind,
                artifact=artifact,
                raw_file_bytes=raw or canonical_json_bytes(artifact),
            )
            paths[_NAMESPACE_BY_KIND[kind]].append(path)

        persist("POLICY", policy, policy_raw_file_bytes)
        for mark_input in snapshot.mark_inputs:
            persist("NAV_MARK_INPUT", mark_input)
        for point in snapshot.points:
            persist("NAV_POINT", point)
        for event in snapshot.events:
            persist("NAV_SERIES_EVENT", event)
        persist(
            "NAV_SERIES_SNAPSHOT",
            snapshot,
            snapshot_raw_file_bytes,
        )

        loaded_policy, loaded_snapshot = self.load_verified_snapshot_bundle(
            manifest,
            policy_id=policy.policy_id,
            snapshot_id=snapshot.snapshot_id,
        )
        if (
            _required_hash(loaded_policy),
            _required_hash(loaded_snapshot),
        ) != (
            _required_hash(policy),
            _required_hash(snapshot),
        ):
            raise ShadowContractError(
                "persisted daily-NAV bundle differs after verified reload"
            )
        return {
            namespace: tuple(namespace_paths)
            for namespace, namespace_paths in paths.items()
            if namespace_paths
        }

    def load_by_reference(
        self,
        manifest: ShadowProtocolManifest,
        *,
        kind: ArtifactKind,
        artifact_id: str,
    ) -> tuple[DailyNavArtifact, DailyNavArtifactReference]:
        """Load one object only through its immutable logical reference."""

        manifest = _revalidate_manifest(manifest)
        manifest_hash = _required_hash(manifest)
        reference_path = self._reference_path(
            manifest.protocol_id,
            manifest_hash,
            kind,
            artifact_id,
        )
        raw_reference = self._read_exact(
            reference_path,
            "daily-NAV reference",
        )
        reference = _load_reference(raw_reference)
        if raw_reference != canonical_json_bytes(reference):
            raise ShadowContractError(
                "daily-NAV reference bytes are not canonical"
            )
        expected_identity = (
            manifest.protocol_id,
            manifest.component_id,
            manifest_hash,
            kind,
            artifact_id,
            _VERSION_BY_KIND[kind],
        )
        actual_identity = (
            reference.protocol_id,
            reference.component_id,
            reference.manifest_sha256,
            reference.artifact_kind,
            reference.artifact_id,
            reference.artifact_contract_version,
        )
        if actual_identity != expected_identity:
            raise ShadowContractError("daily-NAV reference identity mismatch")

        artifact_path = (self.root / reference.artifact_relative_path).resolve()
        self._require_within_root(artifact_path)
        expected_path = self._record_path(
            manifest.protocol_id,
            manifest_hash,
            kind,
            reference.artifact_canonical_sha256,
            reference.artifact_raw_file_sha256,
        )
        if (
            artifact_path != expected_path
            or reference.artifact_relative_path != self._relative(expected_path)
        ):
            raise ShadowContractError(
                "daily-NAV reference escaped its exact content namespace"
            )
        raw_artifact = self._read_exact(
            artifact_path,
            "daily-NAV artifact",
        )
        if (
            len(raw_artifact) != reference.artifact_raw_byte_length
            or _sha256(raw_artifact) != reference.artifact_raw_file_sha256
        ):
            raise ShadowContractError("daily-NAV raw-file identity mismatch")
        artifact = _load_kind(kind, raw_artifact)
        if (
            _required_hash(artifact) != reference.artifact_canonical_sha256
            or _artifact_id(kind, artifact) != artifact_id
        ):
            raise ShadowContractError(
                "daily-NAV canonical identity/reference mismatch"
            )
        self._verify_manifest_lineage(manifest, kind, artifact)
        expected_predecessors = _named_predecessors(artifact)
        if reference.predecessors != expected_predecessors:
            raise ShadowContractError(
                "daily-NAV named predecessor reference drift"
            )
        return artifact, reference

    def load_verified_snapshot_bundle(
        self,
        manifest: ShadowProtocolManifest,
        *,
        policy_id: str,
        snapshot_id: str,
    ) -> tuple[FrozenDailyNavPolicy, NavSeriesSnapshot]:
        """Reload and replay one complete local snapshot graph.

        The return value is locally verified but remains explicitly unanchored.
        This method never infers an independently authenticated expected tail.
        """

        manifest = _revalidate_manifest(manifest)
        policy_artifact, _ = self.load_by_reference(
            manifest,
            kind="POLICY",
            artifact_id=policy_id,
        )
        snapshot_artifact, _ = self.load_by_reference(
            manifest,
            kind="NAV_SERIES_SNAPSHOT",
            artifact_id=snapshot_id,
        )
        if not isinstance(policy_artifact, FrozenDailyNavPolicy) or not isinstance(
            snapshot_artifact,
            NavSeriesSnapshot,
        ):
            raise ShadowContractError("daily-NAV bundle returned wrong types")
        policy = policy_artifact
        snapshot = snapshot_artifact
        self._verify_policy_binding(policy, snapshot)
        self._verify_snapshot_node_identity(snapshot)

        for mark_input in snapshot.mark_inputs:
            loaded, _ = self.load_by_reference(
                manifest,
                kind="NAV_MARK_INPUT",
                artifact_id=mark_input.mark_input_id,
            )
            if _required_hash(loaded) != _required_hash(mark_input):
                raise ShadowContractError(
                    "stored NAV mark input differs from snapshot lineage"
                )
        for point in snapshot.points:
            loaded, _ = self.load_by_reference(
                manifest,
                kind="NAV_POINT",
                artifact_id=point.point_id,
            )
            if _required_hash(loaded) != _required_hash(point):
                raise ShadowContractError(
                    "stored daily-NAV point differs from snapshot lineage"
                )
        for event in snapshot.events:
            loaded, _ = self.load_by_reference(
                manifest,
                kind="NAV_SERIES_EVENT",
                artifact_id=event.event_id,
            )
            if _required_hash(loaded) != _required_hash(event):
                raise ShadowContractError(
                    "stored NAV event differs from snapshot lineage"
                )
        self._verify_prior_snapshot(manifest, snapshot)
        replayed = replay_nav_series_snapshot(snapshot)
        if _required_hash(replayed) != _required_hash(snapshot):
            raise ShadowContractError(
                "stored daily-NAV snapshot fails deterministic replay"
            )
        if snapshot.chain_completeness_status != NAV_CHAIN_COMPLETENESS_STATUS:
            raise ShadowContractError(
                "local NAV snapshot cannot claim certified completeness"
            )
        return policy, snapshot

    def _persist(
        self,
        manifest: ShadowProtocolManifest,
        *,
        kind: ArtifactKind,
        artifact: DailyNavArtifact,
        raw_file_bytes: bytes,
    ) -> Path:
        manifest = _revalidate_manifest(manifest)
        loaded = _load_kind(kind, raw_file_bytes)
        if _required_hash(loaded) != _required_hash(artifact):
            raise ShadowContractError(
                "daily-NAV raw bytes differ from supplied artifact"
            )
        self._verify_manifest_lineage(manifest, kind, loaded)
        manifest_hash = _required_hash(manifest)
        canonical_hash = _required_hash(loaded)
        raw_hash = _sha256(raw_file_bytes)
        artifact_id = _artifact_id(kind, loaded)
        path = self._record_path(
            manifest.protocol_id,
            manifest_hash,
            kind,
            canonical_hash,
            raw_hash,
        )
        self._require_within_root(path.resolve())
        self._exclusive_create(path, raw_file_bytes)

        reference = DailyNavArtifactReference(
            protocol_id=manifest.protocol_id,
            component_id=manifest.component_id,
            manifest_sha256=manifest_hash,
            artifact_kind=kind,
            artifact_id=artifact_id,
            artifact_contract_version=_VERSION_BY_KIND[kind],
            artifact_canonical_sha256=canonical_hash,
            artifact_raw_file_sha256=raw_hash,
            artifact_raw_byte_length=len(raw_file_bytes),
            artifact_relative_path=self._relative(path),
            predecessors=_named_predecessors(loaded),
        )
        reference_path = self._reference_path(
            manifest.protocol_id,
            manifest_hash,
            kind,
            artifact_id,
        )
        self._require_within_root(reference_path.resolve())
        self._exclusive_create(
            reference_path,
            canonical_json_bytes(reference),
        )
        return path

    @staticmethod
    def _verify_manifest_lineage(
        manifest: ShadowProtocolManifest,
        kind: ArtifactKind,
        artifact: DailyNavArtifact,
    ) -> None:
        manifest = _revalidate_manifest(manifest)
        manifest_hash = _required_hash(manifest)
        if kind == "POLICY":
            if not isinstance(artifact, FrozenDailyNavPolicy):
                raise ShadowContractError("daily-NAV policy type mismatch")
            expected = (
                _required_hash(manifest.labels),
                _required_hash(manifest.costs),
                manifest.trading_calendar_sha256,
                manifest.corporate_action_policy_sha256,
                manifest.methodology_document_sha256,
            )
            actual = (
                artifact.label_definition_sha256,
                artifact.cost_assumptions_sha256,
                artifact.trading_calendar_sha256,
                artifact.corporate_action_policy_sha256,
                artifact.methodology_document_sha256,
            )
            if actual != expected:
                raise ShadowContractError(
                    "daily-NAV policy differs from manifest dependencies"
                )
            source_matches = tuple(
                source
                for source in manifest.sources
                if source.source_id == artifact.mark_source_id
                and source.contract_version
                == artifact.mark_source_contract_version
                and _required_hash(source)
                == artifact.mark_source_definition_sha256
            )
            if len(source_matches) != 1:
                raise ShadowContractError(
                    "daily-NAV policy mark source differs from manifest"
                )
            policy_hash = _required_hash(artifact)
            for label, content_hashes in (
                ("control", manifest.control_content_hashes),
                ("challenger", manifest.challenger_content_hashes),
            ):
                matches = tuple(
                    item
                    for item in content_hashes
                    if item.path == DAILY_NAV_CONFIG_PATH
                )
                if len(matches) != 1 or (
                    matches[0].role,
                    matches[0].sha256,
                ) != ("CONFIG", policy_hash):
                    raise ShadowContractError(
                        f"{label} manifest lacks exact daily-NAV CONFIG"
                    )
            return

        actual_identity = (
            getattr(artifact, "protocol_id", None),
            getattr(artifact, "component_id", None),
            getattr(artifact, "manifest_sha256", None),
        )
        expected_identity = (
            manifest.protocol_id,
            manifest.component_id,
            manifest_hash,
        )
        if actual_identity != expected_identity:
            raise ShadowContractError(
                f"{kind} artifact belongs to another manifest"
            )
        if isinstance(artifact, (NavMarkInput, DailyNavPoint, NavSeriesSnapshot)):
            if artifact.trading_calendar_sha256 != manifest.trading_calendar_sha256:
                raise ShadowContractError(
                    f"{kind} artifact carries another trading calendar"
                )
        if isinstance(artifact, NavMarkInput):
            if (
                artifact.corporate_action_policy_sha256
                != manifest.corporate_action_policy_sha256
            ):
                raise ShadowContractError(
                    "NAV mark input carries another corporate-action policy"
                )

    @staticmethod
    def _verify_policy_binding(
        policy: FrozenDailyNavPolicy,
        snapshot: NavSeriesSnapshot,
    ) -> None:
        policy_hash = _required_hash(policy)
        if (
            snapshot.daily_nav_policy_sha256 != policy_hash
            or snapshot.trading_calendar_sha256
            != policy.trading_calendar_sha256
        ):
            raise ShadowContractError(
                "NAV snapshot differs from stored daily-NAV policy"
            )
        for mark_input in snapshot.mark_inputs:
            if (
                mark_input.daily_nav_policy_sha256 != policy_hash
                or mark_input.source_id != policy.mark_source_id
                or mark_input.source_contract_version
                != policy.mark_source_contract_version
                or mark_input.source_definition_sha256
                != policy.mark_source_definition_sha256
                or mark_input.trading_calendar_sha256
                != policy.trading_calendar_sha256
                or mark_input.corporate_action_policy_sha256
                != policy.corporate_action_policy_sha256
            ):
                raise ShadowContractError(
                    "NAV mark input differs from stored policy/source"
                )
        for point in snapshot.points:
            if (
                point.daily_nav_policy_sha256 != policy_hash
                or point.trading_calendar_sha256
                != policy.trading_calendar_sha256
            ):
                raise ShadowContractError(
                    "daily-NAV point differs from stored policy/calendar"
                )
        if any(
            event.daily_nav_policy_sha256 != policy_hash
            for event in snapshot.events
        ):
            raise ShadowContractError(
                "NAV event differs from stored daily-NAV policy"
            )

    @staticmethod
    def _verify_snapshot_node_identity(snapshot: NavSeriesSnapshot) -> None:
        for label, artifacts, id_attribute in (
            ("mark input", snapshot.mark_inputs, "mark_input_id"),
            ("point", snapshot.points, "point_id"),
            ("event", snapshot.events, "event_id"),
        ):
            identities = tuple(
                getattr(artifact, id_attribute) for artifact in artifacts
            )
            if len(identities) != len(set(identities)):
                raise ShadowContractError(
                    f"daily-NAV snapshot repeats a {label} identity"
                )

        mark_hashes = {_required_hash(item) for item in snapshot.mark_inputs}
        point_hashes = {
            item.point_id: _required_hash(item) for item in snapshot.points
        }
        event_hashes = {
            item.event_id: _required_hash(item) for item in snapshot.events
        }
        for point in snapshot.points:
            if not set(point.mark_input_sha256s).issubset(mark_hashes):
                raise ShadowContractError(
                    "daily-NAV point refers to an absent mark input"
                )
            if point.previous_point_id is not None:
                if (
                    point_hashes.get(point.previous_point_id)
                    != point.previous_point_sha256
                ):
                    raise ShadowContractError(
                        "daily-NAV previous-point lineage is absent"
                    )
            if point.supersedes_point_id is not None:
                superseded = next(
                    (
                        item
                        for item in snapshot.points
                        if item.point_id == point.supersedes_point_id
                    ),
                    None,
                )
                if (
                    superseded is None
                    or _required_hash(superseded)
                    != point.supersedes_point_sha256
                    or superseded.session != point.session
                    or superseded.revision + 1 != point.revision
                ):
                    raise ShadowContractError(
                        "daily-NAV correction lineage is invalid"
                    )
        for event in snapshot.events:
            if point_hashes.get(event.point_id) != event.point_sha256:
                raise ShadowContractError(
                    "NAV event refers to an absent point"
                )
            if event.previous_event_id is not None and (
                event_hashes.get(event.previous_event_id)
                != event.previous_event_sha256
            ):
                raise ShadowContractError(
                    "NAV event predecessor is absent"
                )

    def _verify_prior_snapshot(
        self,
        manifest: ShadowProtocolManifest,
        snapshot: NavSeriesSnapshot,
    ) -> None:
        if snapshot.prior_snapshot_id is None:
            return
        assert snapshot.prior_snapshot_sha256 is not None
        prior_artifact, _ = self.load_by_reference(
            manifest,
            kind="NAV_SERIES_SNAPSHOT",
            artifact_id=snapshot.prior_snapshot_id,
        )
        if not isinstance(prior_artifact, NavSeriesSnapshot):
            raise ShadowContractError("prior NAV snapshot has wrong type")
        prior = prior_artifact
        if _required_hash(prior) != snapshot.prior_snapshot_sha256:
            raise ShadowContractError("prior NAV snapshot hash mismatch")
        if (
            prior.protocol_id,
            prior.component_id,
            prior.manifest_sha256,
            prior.daily_nav_policy_sha256,
            prior.trading_calendar_sha256,
            prior.series_kind,
            prior.series_id,
            prior.decision_role,
            prior.path_id,
        ) != (
            snapshot.protocol_id,
            snapshot.component_id,
            snapshot.manifest_sha256,
            snapshot.daily_nav_policy_sha256,
            snapshot.trading_calendar_sha256,
            snapshot.series_kind,
            snapshot.series_id,
            snapshot.decision_role,
            snapshot.path_id,
        ):
            raise ShadowContractError("prior NAV snapshot belongs to another path")
        if snapshot.snapshot_at < prior.snapshot_at:
            raise ShadowContractError("NAV snapshot predates its predecessor")
        if snapshot.event_count <= prior.event_count:
            raise ShadowContractError(
                "successor NAV snapshot did not append an event"
            )
        if snapshot.event_sha256s[: prior.event_count] != prior.event_sha256s:
            raise ShadowContractError(
                "successor NAV snapshot rewrote its event prefix"
            )
        if snapshot.head_event_sha256 != prior.head_event_sha256:
            raise ShadowContractError(
                "successor NAV snapshot changed the chain head"
            )

    def _record_path(
        self,
        protocol_id: str,
        manifest_hash: str,
        kind: ArtifactKind,
        canonical_hash: str,
        raw_hash: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, manifest_hash)
            / "daily_nav"
            / _NAMESPACE_BY_KIND[kind]
            / _safe_segment(canonical_hash, "canonical SHA-256")
            / f"{_safe_segment(raw_hash, 'raw SHA-256')}.json"
        )

    def _reference_path(
        self,
        protocol_id: str,
        manifest_hash: str,
        kind: ArtifactKind,
        artifact_id: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, manifest_hash)
            / "daily_nav"
            / "refs"
            / _NAMESPACE_BY_KIND[kind]
            / f"{_safe_segment(artifact_id, 'artifact ID')}.json"
        )

    def _protocol_root(self, protocol_id: str, manifest_hash: str) -> Path:
        return (
            self.root
            / "protocols"
            / _safe_segment(protocol_id, "protocol ID")
            / _safe_segment(manifest_hash, "manifest SHA-256")
        )

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        self._require_within_root(resolved)
        return resolved.relative_to(self.root).as_posix()

    def _require_within_root(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ShadowContractError(
                "daily-NAV path escaped store root"
            ) from exc

    @staticmethod
    def _exclusive_create(path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ShadowContractError(
                    f"immutable daily-NAV artifact collision: {path}"
                ) from None
        return path

    @staticmethod
    def _read_exact(path: Path, label: str) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ShadowContractError(f"{label} is unavailable: {path}") from exc


def _load_kind(kind: ArtifactKind, raw_file_bytes: bytes) -> DailyNavArtifact:
    loader = {
        "POLICY": load_daily_nav_policy_v1,
        "NAV_MARK_INPUT": load_nav_mark_input_v1,
        "NAV_POINT": load_daily_nav_point_v1,
        "NAV_SERIES_EVENT": load_nav_series_event_v1,
        "NAV_SERIES_SNAPSHOT": load_nav_series_snapshot_v1,
    }[kind]
    return loader(raw_file_bytes)


def _artifact_id(kind: ArtifactKind, artifact: DailyNavArtifact) -> str:
    attribute = _ID_ATTRIBUTE_BY_KIND[kind]
    value = getattr(artifact, attribute, None)
    if not isinstance(value, str) or not value:
        raise ShadowContractError(f"{kind} artifact lacks its logical ID")
    return value


def _named_predecessors(
    artifact: DailyNavArtifact,
) -> tuple[DailyNavNamedPredecessor, ...]:
    items: list[DailyNavNamedPredecessor] = []

    def add(name: str, digest: str | None) -> None:
        if digest is None:
            return
        items.append(DailyNavNamedPredecessor(name=name, sha256=digest))

    if isinstance(artifact, FrozenDailyNavPolicy):
        add("corporate_action_policy", artifact.corporate_action_policy_sha256)
        add("cost_assumptions", artifact.cost_assumptions_sha256)
        add("fixed_notional_policy", artifact.fixed_notional_policy_sha256)
        add("label_definition", artifact.label_definition_sha256)
        add("mark_source_definition", artifact.mark_source_definition_sha256)
        add("methodology_document", artifact.methodology_document_sha256)
        add("policy_portfolio_policy", artifact.policy_portfolio_policy_sha256)
        add("portfolio_policy", artifact.portfolio_policy_sha256)
        add("trading_calendar", artifact.trading_calendar_sha256)
    elif isinstance(artifact, NavMarkInput):
        add("corporate_action_policy", artifact.corporate_action_policy_sha256)
        add("daily_nav_policy", artifact.daily_nav_policy_sha256)
        add("previous_source_record", artifact.previous_source_record_sha256)
        add("source_definition", artifact.source_definition_sha256)
        add("source_record_canonical", artifact.source_record_canonical_sha256)
        add("source_record_raw", artifact.source_record_raw_file_sha256)
        add("supersedes_mark_input", artifact.supersedes_mark_input_sha256)
        add("trading_calendar", artifact.trading_calendar_sha256)
    elif isinstance(artifact, DailyNavPoint):
        items.extend(artifact.predecessors)
        add("daily_nav_policy", artifact.daily_nav_policy_sha256)
        add("previous_point", artifact.previous_point_sha256)
        add("supersedes_point", artifact.supersedes_point_sha256)
        add("trading_calendar", artifact.trading_calendar_sha256)
        add("unfunded_cost_origin", artifact.unfunded_cost_origin_sha256)
        for index, digest in enumerate(artifact.mark_input_sha256s):
            add(f"mark_input[{index:04d}]", digest)
    elif isinstance(artifact, NavSeriesEvent):
        add("daily_nav_policy", artifact.daily_nav_policy_sha256)
        add("point", artifact.point_sha256)
        add("previous_event", artifact.previous_event_sha256)
        for index, digest in enumerate(artifact.mark_input_sha256s):
            add(f"mark_input[{index:04d}]", digest)
    elif isinstance(artifact, NavSeriesSnapshot):
        add("daily_nav_policy", artifact.daily_nav_policy_sha256)
        add("prior_snapshot", artifact.prior_snapshot_sha256)
        add("trading_calendar", artifact.trading_calendar_sha256)
        for index, digest in enumerate(artifact.mark_input_sha256s):
            add(f"mark_input[{index:06d}]", digest)
        for index, digest in enumerate(artifact.point_sha256s):
            add(f"point[{index:06d}]", digest)
        for index, digest in enumerate(artifact.event_sha256s):
            add(f"event[{index:06d}]", digest)
    else:  # pragma: no cover - closed union guard
        raise ShadowContractError("unsupported daily-NAV artifact type")
    return _merge_predecessors(items)


def _merge_predecessors(
    predecessors: Iterable[DailyNavNamedPredecessor],
) -> tuple[DailyNavNamedPredecessor, ...]:
    by_name: dict[str, str] = {}
    for predecessor in predecessors:
        existing = by_name.get(predecessor.name)
        if existing is not None and existing != predecessor.sha256:
            raise ShadowContractError(
                f"daily-NAV predecessor name collision: {predecessor.name}"
            )
        by_name[predecessor.name] = predecessor.sha256
    return tuple(
        DailyNavNamedPredecessor(name=name, sha256=by_name[name])
        for name in sorted(by_name)
    )


def _load_reference(raw_file_bytes: bytes) -> DailyNavArtifactReference:
    if not isinstance(raw_file_bytes, bytes):
        raise ShadowContractError("daily-NAV reference loader requires raw bytes")
    try:
        payload = json.loads(
            raw_file_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ShadowContractError(
            f"daily-NAV reference JSON is invalid: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ShadowContractError("daily-NAV reference root must be a JSON object")
    received = payload.get("contract_version")
    if received != DAILY_NAV_REFERENCE_VERSION:
        raise ShadowContractError(
            "daily-NAV reference requires "
            f"{DAILY_NAV_REFERENCE_VERSION}; received {received!r}"
        )
    try:
        return DailyNavArtifactReference.model_validate(payload)
    except Exception as exc:
        raise ShadowContractError(
            "daily-NAV reference contract validation failed"
        ) from exc


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _revalidate_manifest(
    manifest: ShadowProtocolManifest,
) -> ShadowProtocolManifest:
    try:
        return ShadowProtocolManifest.model_validate(
            manifest.model_dump(mode="python")
        )
    except Exception as exc:
        raise ShadowContractError("daily-NAV manifest is invalid") from exc


def _required_hash(model: BaseModel) -> str:
    digest = canonical_sha256(model)
    if digest is None:
        raise ShadowContractError("daily-NAV canonical SHA-256 is absent")
    _require_sha256(digest, "daily-NAV canonical SHA-256")
    return digest


def _require_sha256(value: str, label: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _safe_segment(value: str, label: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value):
        raise ShadowContractError(f"unsafe {label}: {value!r}")
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "ArtifactKind",
    "DAILY_NAV_REFERENCE_VERSION",
    "DailyNavArtifact",
    "DailyNavArtifactReference",
    "DailyNavArtifactStore",
]
