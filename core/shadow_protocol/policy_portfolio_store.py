"""Immutable content-addressed storage for RS-P2-016 artifacts.

Canonical model identity and raw-file identity are independent trust
boundaries.  References therefore bind both hashes, the exact byte length,
the exact relative path, and a deterministic list of named predecessor
hashes.  Every load is fail-closed: duplicate JSON keys, non-canonical JSON,
wrong contract families, path substitution, byte drift, and replay drift are
rejected.
"""

from __future__ import annotations

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
from .evidence import CandidateSetStore
from .fixed_notional_store import FixedNotionalArtifactStore
from .policy_portfolio import (
    POLICY_PORTFOLIO_CANDIDATE_INPUT_VERSION,
    POLICY_PORTFOLIO_CLASSIFICATION_VERSION,
    POLICY_PORTFOLIO_EVENT_VERSION,
    POLICY_PORTFOLIO_GENESIS_VERSION,
    POLICY_PORTFOLIO_LIQUIDITY_VERSION,
    POLICY_PORTFOLIO_PAIRED_SESSION_VERSION,
    POLICY_PORTFOLIO_POLICY_VERSION,
    POLICY_PORTFOLIO_REGIME_VERSION,
    POLICY_PORTFOLIO_SESSION_INPUT_VERSION,
    POLICY_PORTFOLIO_STATE_VERSION,
    POLICY_PORTFOLIO_TRANSITION_VERSION,
    FrozenPolicyPortfolioPolicy,
    PairedPolicyPortfolioSessionRecord,
    PolicyCandidateClassification,
    PolicyPortfolioCandidateInput,
    PolicyPortfolioGenesisRecord,
    PolicyPortfolioSessionInput,
    PolicyPortfolioSessionState,
    PolicyPortfolioSessionTransition,
    PolicyPortfolioTransitionEvent,
    PolicyRegimeRecord,
    PolicySessionLiquidityRecord,
    replay_policy_portfolio_session,
    verify_policy_portfolio_policy_binding,
    verify_policy_portfolio_session,
)


POLICY_PORTFOLIO_LINEAGE_VERSION = "shadow-policy-portfolio-lineage-v1"
POLICY_PORTFOLIO_REFERENCE_VERSION = "shadow-policy-portfolio-reference-v1"

ArtifactKind: TypeAlias = Literal[
    "CANDIDATE_INPUT",
    "CLASSIFICATION",
    "EVENT",
    "GENESIS",
    "LINEAGE",
    "LIQUIDITY",
    "PAIRED_SESSION",
    "POLICY",
    "REGIME",
    "SESSION_INPUT",
    "STATE",
    "TRANSITION",
]


class _StoreModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class PolicyPortfolioNamedPredecessor(_StoreModel):
    """One deterministic name-addressed edge in the artifact graph."""

    name: str
    sha256: str

    @model_validator(mode="after")
    def verify_predecessor(self) -> PolicyPortfolioNamedPredecessor:
        if not re.fullmatch(r"[a-z][a-z0-9_.\[\]-]{0,127}", self.name):
            raise ValueError("policy-portfolio predecessor name is not canonical")
        _require_sha256(self.sha256, f"{self.name} SHA-256")
        return self


class PolicyPortfolioArtifactReference(_StoreModel):
    """Dual-hash reference to one immutable policy-portfolio artifact."""

    contract_version: Literal["shadow-policy-portfolio-reference-v1"] = (
        POLICY_PORTFOLIO_REFERENCE_VERSION
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
    predecessors: tuple[PolicyPortfolioNamedPredecessor, ...]

    @model_validator(mode="after")
    def verify_reference(self) -> PolicyPortfolioArtifactReference:
        for digest in (
            self.manifest_sha256,
            self.artifact_canonical_sha256,
            self.artifact_raw_file_sha256,
        ):
            _require_sha256(digest, "policy-portfolio reference SHA-256")
        if self.artifact_raw_byte_length <= 0:
            raise ValueError("policy-portfolio reference byte length must be positive")
        names = tuple(item.name for item in self.predecessors)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError(
                "policy-portfolio predecessor names must be unique and ordered"
            )
        return self


class PolicyPortfolioLineageBundle(_StoreModel):
    """Complete replay graph for one paired RS-P2-016 session."""

    contract_version: Literal["shadow-policy-portfolio-lineage-v1"] = (
        POLICY_PORTFOLIO_LINEAGE_VERSION
    )
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False
    lineage_id: str
    protocol_id: str
    component_id: str
    manifest_sha256: str
    policy_id: str
    policy_sha256: str
    genesis_id: str
    genesis_sha256: str
    session_input_id: str
    session_input_sha256: str
    paired_session_id: str
    paired_session_sha256: str
    previous_control_state_id: str
    previous_control_state_sha256: str
    previous_challenger_state_id: str
    previous_challenger_state_sha256: str
    regime_record_id: str
    regime_sha256: str
    opportunity_raw_capture_id: str
    opportunity_raw_capture_sha256: str
    opportunity_candidate_set_id: str
    opportunity_candidate_set_sha256: str
    admission_candidate_ids: tuple[str, ...]
    candidate_input_sha256s: tuple[str, ...]
    fixed_notional_pair_input_sha256s: tuple[str, ...]
    fixed_notional_paired_record_sha256s: tuple[str, ...]
    control_event_sha256s: tuple[str, ...]
    challenger_event_sha256s: tuple[str, ...]
    control_transition_sha256: str
    challenger_transition_sha256: str
    control_state_sha256: str
    challenger_state_sha256: str
    lineage_valid: Literal[True] = True

    @model_validator(mode="after")
    def verify_lineage(self) -> PolicyPortfolioLineageBundle:
        for digest in (
            self.manifest_sha256,
            self.policy_sha256,
            self.genesis_sha256,
            self.session_input_sha256,
            self.paired_session_sha256,
            self.previous_control_state_sha256,
            self.previous_challenger_state_sha256,
            self.regime_sha256,
            self.opportunity_raw_capture_sha256,
            self.opportunity_candidate_set_sha256,
            *self.candidate_input_sha256s,
            *self.fixed_notional_pair_input_sha256s,
            *self.fixed_notional_paired_record_sha256s,
            *self.control_event_sha256s,
            *self.challenger_event_sha256s,
            self.control_transition_sha256,
            self.challenger_transition_sha256,
            self.control_state_sha256,
            self.challenger_state_sha256,
        ):
            _require_sha256(digest, "policy-portfolio lineage SHA-256")
        if self.admission_candidate_ids != tuple(
            sorted(self.admission_candidate_ids)
        ) or len(set(self.admission_candidate_ids)) != len(
            self.admission_candidate_ids
        ):
            raise ValueError(
                "lineage admission candidate IDs must be unique and ordered"
            )
        expected = _lineage_id(
            protocol_id=self.protocol_id,
            manifest_sha256=self.manifest_sha256,
            genesis_sha256=self.genesis_sha256,
            session_input_sha256=self.session_input_sha256,
            paired_session_sha256=self.paired_session_sha256,
        )
        if self.lineage_id != expected:
            raise ValueError("policy-portfolio lineage ID mismatch")
        return self


PolicyPortfolioArtifact: TypeAlias = (
    FrozenPolicyPortfolioPolicy
    | PolicyPortfolioGenesisRecord
    | PolicyRegimeRecord
    | PolicySessionLiquidityRecord
    | PolicyCandidateClassification
    | PolicyPortfolioCandidateInput
    | PolicyPortfolioTransitionEvent
    | PolicyPortfolioSessionState
    | PolicyPortfolioSessionInput
    | PolicyPortfolioSessionTransition
    | PairedPolicyPortfolioSessionRecord
    | PolicyPortfolioLineageBundle
)

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MODEL_BY_KIND: dict[ArtifactKind, type[BaseModel]] = {
    "CANDIDATE_INPUT": PolicyPortfolioCandidateInput,
    "CLASSIFICATION": PolicyCandidateClassification,
    "EVENT": PolicyPortfolioTransitionEvent,
    "GENESIS": PolicyPortfolioGenesisRecord,
    "LINEAGE": PolicyPortfolioLineageBundle,
    "LIQUIDITY": PolicySessionLiquidityRecord,
    "PAIRED_SESSION": PairedPolicyPortfolioSessionRecord,
    "POLICY": FrozenPolicyPortfolioPolicy,
    "REGIME": PolicyRegimeRecord,
    "SESSION_INPUT": PolicyPortfolioSessionInput,
    "STATE": PolicyPortfolioSessionState,
    "TRANSITION": PolicyPortfolioSessionTransition,
}
_VERSION_BY_KIND: dict[ArtifactKind, str] = {
    "CANDIDATE_INPUT": POLICY_PORTFOLIO_CANDIDATE_INPUT_VERSION,
    "CLASSIFICATION": POLICY_PORTFOLIO_CLASSIFICATION_VERSION,
    "EVENT": POLICY_PORTFOLIO_EVENT_VERSION,
    "GENESIS": POLICY_PORTFOLIO_GENESIS_VERSION,
    "LINEAGE": POLICY_PORTFOLIO_LINEAGE_VERSION,
    "LIQUIDITY": POLICY_PORTFOLIO_LIQUIDITY_VERSION,
    "PAIRED_SESSION": POLICY_PORTFOLIO_PAIRED_SESSION_VERSION,
    "POLICY": POLICY_PORTFOLIO_POLICY_VERSION,
    "REGIME": POLICY_PORTFOLIO_REGIME_VERSION,
    "SESSION_INPUT": POLICY_PORTFOLIO_SESSION_INPUT_VERSION,
    "STATE": POLICY_PORTFOLIO_STATE_VERSION,
    "TRANSITION": POLICY_PORTFOLIO_TRANSITION_VERSION,
}


def load_policy_portfolio_policy_v1(
    raw_file_bytes: bytes,
) -> FrozenPolicyPortfolioPolicy:
    return _load_kind_as("POLICY", raw_file_bytes)


def load_policy_portfolio_genesis_v1(
    raw_file_bytes: bytes,
) -> PolicyPortfolioGenesisRecord:
    return _load_kind_as("GENESIS", raw_file_bytes)


def load_policy_regime_record_v1(
    raw_file_bytes: bytes,
) -> PolicyRegimeRecord:
    return _load_kind_as("REGIME", raw_file_bytes)


def load_policy_session_liquidity_v1(
    raw_file_bytes: bytes,
) -> PolicySessionLiquidityRecord:
    return _load_kind_as("LIQUIDITY", raw_file_bytes)


def load_policy_candidate_classification_v1(
    raw_file_bytes: bytes,
) -> PolicyCandidateClassification:
    return _load_kind_as("CLASSIFICATION", raw_file_bytes)


def load_policy_portfolio_candidate_input_v1(
    raw_file_bytes: bytes,
) -> PolicyPortfolioCandidateInput:
    return _load_kind_as("CANDIDATE_INPUT", raw_file_bytes)


def load_policy_portfolio_event_v1(
    raw_file_bytes: bytes,
) -> PolicyPortfolioTransitionEvent:
    return _load_kind_as("EVENT", raw_file_bytes)


def load_policy_portfolio_state_v1(
    raw_file_bytes: bytes,
) -> PolicyPortfolioSessionState:
    return _load_kind_as("STATE", raw_file_bytes)


def load_policy_portfolio_session_input_v1(
    raw_file_bytes: bytes,
) -> PolicyPortfolioSessionInput:
    return _load_kind_as("SESSION_INPUT", raw_file_bytes)


def load_policy_portfolio_transition_v1(
    raw_file_bytes: bytes,
) -> PolicyPortfolioSessionTransition:
    return _load_kind_as("TRANSITION", raw_file_bytes)


def load_paired_policy_portfolio_session_v1(
    raw_file_bytes: bytes,
) -> PairedPolicyPortfolioSessionRecord:
    return _load_kind_as("PAIRED_SESSION", raw_file_bytes)


def load_policy_portfolio_lineage_v1(
    raw_file_bytes: bytes,
) -> PolicyPortfolioLineageBundle:
    return _load_kind_as("LINEAGE", raw_file_bytes)


def build_policy_portfolio_lineage_bundle(
    *,
    manifest: ShadowProtocolManifest,
    genesis: PolicyPortfolioGenesisRecord,
    session_input: PolicyPortfolioSessionInput,
    paired_session: PairedPolicyPortfolioSessionRecord,
) -> PolicyPortfolioLineageBundle:
    """Bind every persisted predecessor needed for an exact session replay."""

    manifest = _revalidate(ShadowProtocolManifest, manifest)
    genesis = _revalidate(PolicyPortfolioGenesisRecord, genesis)
    session_input = _revalidate(PolicyPortfolioSessionInput, session_input)
    paired_session = verify_policy_portfolio_session(session_input, paired_session)
    manifest_hash = _required_hash(manifest)
    genesis_hash = _required_hash(genesis)
    session_input_hash = _required_hash(session_input)
    paired_hash = _required_hash(paired_session)
    _verify_common_lineage(manifest, genesis, session_input, paired_session)
    if paired_session.genesis_sha256 != genesis_hash:
        raise ShadowContractError("paired session differs from supplied genesis")
    values = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": manifest_hash,
        "policy_id": session_input.policy.policy_id,
        "policy_sha256": session_input.policy_sha256,
        "genesis_id": genesis.genesis_id,
        "genesis_sha256": genesis_hash,
        "session_input_id": session_input.session_input_id,
        "session_input_sha256": session_input_hash,
        "paired_session_id": paired_session.paired_session_id,
        "paired_session_sha256": paired_hash,
        "previous_control_state_id": session_input.previous_control_state.state_id,
        "previous_control_state_sha256": (
            session_input.previous_control_state_sha256
        ),
        "previous_challenger_state_id": (
            session_input.previous_challenger_state.state_id
        ),
        "previous_challenger_state_sha256": (
            session_input.previous_challenger_state_sha256
        ),
        "regime_record_id": session_input.regime.regime_record_id,
        "regime_sha256": session_input.regime_sha256,
        "opportunity_raw_capture_id": (
            session_input.opportunity_raw_capture.raw_capture_id
        ),
        "opportunity_raw_capture_sha256": (
            session_input.opportunity_raw_capture_sha256
        ),
        "opportunity_candidate_set_id": (
            session_input.opportunity_candidate_set.candidate_set_id
        ),
        "opportunity_candidate_set_sha256": (
            session_input.opportunity_candidate_set_sha256
        ),
        "admission_candidate_ids": session_input.admission_candidate_ids,
        "candidate_input_sha256s": session_input.candidate_sha256s,
        "fixed_notional_pair_input_sha256s": tuple(
            item.pair_input_sha256 for item in session_input.candidates
        ),
        "fixed_notional_paired_record_sha256s": tuple(
            item.paired_fixed_notional_record_sha256
            for item in session_input.candidates
        ),
        "control_event_sha256s": (
            paired_session.control_transition.event_sha256s
        ),
        "challenger_event_sha256s": (
            paired_session.challenger_transition.event_sha256s
        ),
        "control_transition_sha256": paired_session.control_transition_sha256,
        "challenger_transition_sha256": (
            paired_session.challenger_transition_sha256
        ),
        "control_state_sha256": paired_session.control_state_sha256,
        "challenger_state_sha256": paired_session.challenger_state_sha256,
    }
    lineage_id = _lineage_id(
        protocol_id=manifest.protocol_id,
        manifest_sha256=manifest_hash,
        genesis_sha256=genesis_hash,
        session_input_sha256=session_input_hash,
        paired_session_sha256=paired_hash,
    )
    try:
        return PolicyPortfolioLineageBundle(
            lineage_id=lineage_id,
            **values,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "policy-portfolio lineage bundle is invalid"
        ) from exc


def verify_policy_portfolio_lineage_bundle(
    bundle: PolicyPortfolioLineageBundle,
    *,
    manifest: ShadowProtocolManifest,
    genesis: PolicyPortfolioGenesisRecord,
    session_input: PolicyPortfolioSessionInput,
    paired_session: PairedPolicyPortfolioSessionRecord,
) -> PolicyPortfolioLineageBundle:
    trusted = _revalidate(PolicyPortfolioLineageBundle, bundle)
    rebuilt = build_policy_portfolio_lineage_bundle(
        manifest=manifest,
        genesis=genesis,
        session_input=session_input,
        paired_session=paired_session,
    )
    if _required_hash(trusted) != _required_hash(rebuilt):
        raise ShadowContractError(
            "policy-portfolio lineage differs from exact reconstruction"
        )
    return trusted


class PolicyPortfolioArtifactStore:
    """Exclusive-create store with dual hashes and executable replay lineage."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def persist_session_bundle(
        self,
        manifest: ShadowProtocolManifest,
        genesis: PolicyPortfolioGenesisRecord,
        session_input_raw_file_bytes: bytes,
        paired_session_raw_file_bytes: bytes,
    ) -> dict[str, tuple[Path, ...]]:
        """Persist and re-read one complete paired session graph.

        The two top-level raw byte strings preserve caller-supplied file
        identity.  Nested artifacts are born at this boundary and are written
        in canonical form.
        """

        manifest = _revalidate(ShadowProtocolManifest, manifest)
        genesis = _revalidate(PolicyPortfolioGenesisRecord, genesis)
        session_input = load_policy_portfolio_session_input_v1(
            session_input_raw_file_bytes
        )
        paired = load_paired_policy_portfolio_session_v1(
            paired_session_raw_file_bytes
        )
        verify_policy_portfolio_session(session_input, paired)
        _verify_common_lineage(manifest, genesis, session_input, paired)
        lineage = build_policy_portfolio_lineage_bundle(
            manifest=manifest,
            genesis=genesis,
            session_input=session_input,
            paired_session=paired,
        )
        opportunity_store = CandidateSetStore(self.root)
        opportunity_store.persist_raw(
            session_input.opportunity_raw_capture
        )
        opportunity_store.persist(
            session_input.opportunity_candidate_set
        )
        self._persist_fixed_notional_predecessors(manifest, session_input)

        paths: dict[str, list[Path]] = {
            kind: [] for kind in _MODEL_BY_KIND
        }

        def persist(
            kind: ArtifactKind,
            artifact: PolicyPortfolioArtifact,
            raw: bytes | None = None,
        ) -> None:
            paths[kind].append(
                self._persist(
                    manifest,
                    kind=kind,
                    artifact=artifact,
                    raw_file_bytes=raw or canonical_json_bytes(artifact),
                )
            )

        persist("POLICY", session_input.policy)
        persist("GENESIS", genesis)
        persist("REGIME", session_input.regime)
        for candidate in session_input.candidates:
            persist("CLASSIFICATION", candidate.classification)
            for liquidity in candidate.entry_liquidity:
                persist("LIQUIDITY", liquidity)
            persist("CANDIDATE_INPUT", candidate)
        persist("STATE", session_input.previous_control_state)
        persist("STATE", session_input.previous_challenger_state)
        persist("SESSION_INPUT", session_input, session_input_raw_file_bytes)
        for transition in (
            paired.control_transition,
            paired.challenger_transition,
        ):
            for event in transition.events:
                persist("EVENT", event)
            persist("TRANSITION", transition)
        persist("STATE", paired.control_state)
        persist("STATE", paired.challenger_state)
        persist("PAIRED_SESSION", paired, paired_session_raw_file_bytes)
        persist("LINEAGE", lineage)

        loaded_input, loaded_paired = self.load_session_bundle(
            manifest,
            genesis_id=genesis.genesis_id,
            session_input_id=session_input.session_input_id,
            paired_session_id=paired.paired_session_id,
        )
        loaded_lineage = self.load_verified_lineage(
            manifest,
            lineage_id=lineage.lineage_id,
            genesis_id=genesis.genesis_id,
            session_input_id=session_input.session_input_id,
            paired_session_id=paired.paired_session_id,
        )
        if (
            _required_hash(loaded_input),
            _required_hash(loaded_paired),
            _required_hash(loaded_lineage),
        ) != (
            _required_hash(session_input),
            _required_hash(paired),
            _required_hash(lineage),
        ):
            raise ShadowContractError(
                "stored policy-portfolio bundle differs after reload"
            )
        return {
            kind: tuple(kind_paths)
            for kind, kind_paths in paths.items()
            if kind_paths
        }

    def load_by_reference(
        self,
        manifest: ShadowProtocolManifest,
        *,
        kind: ArtifactKind,
        artifact_id: str,
    ) -> tuple[PolicyPortfolioArtifact, PolicyPortfolioArtifactReference]:
        """Load one artifact through its immutable dual-hash reference."""

        manifest = _revalidate(ShadowProtocolManifest, manifest)
        manifest_hash = _required_hash(manifest)
        reference_path = self._reference_path(
            manifest.protocol_id,
            manifest_hash,
            kind,
            artifact_id,
        )
        reference = _load_canonical_model(
            PolicyPortfolioArtifactReference,
            self._read_exact(reference_path, "policy-portfolio reference"),
            "policy-portfolio reference",
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
            raise ShadowContractError(
                "policy-portfolio reference identity mismatch"
            )
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
                "policy-portfolio reference escaped its exact namespace"
            )
        raw = self._read_exact(artifact_path, "policy-portfolio artifact")
        if (
            len(raw) != reference.artifact_raw_byte_length
            or _sha256(raw) != reference.artifact_raw_file_sha256
        ):
            raise ShadowContractError(
                "policy-portfolio raw-file identity mismatch"
            )
        artifact = _load_kind(kind, raw)
        if (
            _required_hash(artifact) != reference.artifact_canonical_sha256
            or _artifact_id(kind, artifact) != artifact_id
        ):
            raise ShadowContractError(
                "policy-portfolio canonical identity/reference mismatch"
            )
        self._verify_manifest_lineage(manifest, kind, artifact)
        if reference.predecessors != _named_predecessors(artifact):
            raise ShadowContractError(
                "policy-portfolio named predecessor reference drift"
            )
        return artifact, reference

    def load_session_bundle(
        self,
        manifest: ShadowProtocolManifest,
        *,
        genesis_id: str,
        session_input_id: str,
        paired_session_id: str,
    ) -> tuple[
        PolicyPortfolioSessionInput,
        PairedPolicyPortfolioSessionRecord,
    ]:
        """Reload every independently persisted node and verify exact replay."""

        genesis, _ = self.load_by_reference(
            manifest,
            kind="GENESIS",
            artifact_id=genesis_id,
        )
        session_input, _ = self.load_by_reference(
            manifest,
            kind="SESSION_INPUT",
            artifact_id=session_input_id,
        )
        paired, _ = self.load_by_reference(
            manifest,
            kind="PAIRED_SESSION",
            artifact_id=paired_session_id,
        )
        if (
            not isinstance(genesis, PolicyPortfolioGenesisRecord)
            or not isinstance(session_input, PolicyPortfolioSessionInput)
            or not isinstance(paired, PairedPolicyPortfolioSessionRecord)
        ):
            raise ShadowContractError(
                "policy-portfolio bundle returned wrong artifact type"
            )
        _verify_common_lineage(manifest, genesis, session_input, paired)
        self._load_and_match(
            manifest,
            "POLICY",
            session_input.policy.policy_id,
            session_input.policy,
        )
        self._load_and_match(
            manifest,
            "REGIME",
            session_input.regime.regime_record_id,
            session_input.regime,
        )
        for state in (
            session_input.previous_control_state,
            session_input.previous_challenger_state,
        ):
            self._load_and_match(manifest, "STATE", state.state_id, state)
        opportunity_store = CandidateSetStore(self.root)
        loaded_raw = opportunity_store.load_raw(
            session_input.opportunity_raw_capture.raw_capture_id,
            protocol_id=manifest.protocol_id,
            manifest_sha256=_required_hash(manifest),
        )
        loaded_set = opportunity_store.load(
            session_input.opportunity_candidate_set.candidate_set_id,
            protocol_id=manifest.protocol_id,
            manifest_sha256=_required_hash(manifest),
        )
        if (
            _required_hash(loaded_raw),
            _required_hash(loaded_set),
        ) != (
            session_input.opportunity_raw_capture_sha256,
            session_input.opportunity_candidate_set_sha256,
        ):
            raise ShadowContractError(
                "stored current opportunity set differs from session input"
            )
        for candidate in session_input.candidates:
            self._load_and_match(
                manifest,
                "CLASSIFICATION",
                candidate.classification.classification_id,
                candidate.classification,
            )
            for liquidity in candidate.entry_liquidity:
                self._load_and_match(
                    manifest,
                    "LIQUIDITY",
                    liquidity.record_id,
                    liquidity,
                )
            self._load_and_match(
                manifest,
                "CANDIDATE_INPUT",
                candidate.candidate_input_id,
                candidate,
            )
        for transition, state in (
            (paired.control_transition, paired.control_state),
            (paired.challenger_transition, paired.challenger_state),
        ):
            for event in transition.events:
                self._load_and_match(
                    manifest,
                    "EVENT",
                    event.event_id,
                    event,
                )
            self._load_and_match(
                manifest,
                "TRANSITION",
                transition.transition_id,
                transition,
            )
            self._load_and_match(manifest, "STATE", state.state_id, state)
        self._verify_fixed_notional_predecessors(manifest, session_input)
        return session_input, replay_policy_portfolio_session(
            session_input,
            paired,
        )

    def load_verified_lineage(
        self,
        manifest: ShadowProtocolManifest,
        *,
        lineage_id: str,
        genesis_id: str,
        session_input_id: str,
        paired_session_id: str,
    ) -> PolicyPortfolioLineageBundle:
        artifact, _ = self.load_by_reference(
            manifest,
            kind="LINEAGE",
            artifact_id=lineage_id,
        )
        genesis, _ = self.load_by_reference(
            manifest,
            kind="GENESIS",
            artifact_id=genesis_id,
        )
        session_input, paired = self.load_session_bundle(
            manifest,
            genesis_id=genesis_id,
            session_input_id=session_input_id,
            paired_session_id=paired_session_id,
        )
        if not isinstance(artifact, PolicyPortfolioLineageBundle) or not isinstance(
            genesis,
            PolicyPortfolioGenesisRecord,
        ):
            raise ShadowContractError(
                "policy-portfolio lineage reference returned wrong type"
            )
        return verify_policy_portfolio_lineage_bundle(
            artifact,
            manifest=manifest,
            genesis=genesis,
            session_input=session_input,
            paired_session=paired,
        )

    def replay_session_bundle(
        self,
        manifest: ShadowProtocolManifest,
        *,
        genesis_id: str,
        session_input_id: str,
        paired_session_id: str,
    ) -> PairedPolicyPortfolioSessionRecord:
        """Load all nodes and return only the exact deterministic replay."""

        session_input, paired = self.load_session_bundle(
            manifest,
            genesis_id=genesis_id,
            session_input_id=session_input_id,
            paired_session_id=paired_session_id,
        )
        return replay_policy_portfolio_session(session_input, paired)

    def _verify_fixed_notional_predecessors(
        self,
        manifest: ShadowProtocolManifest,
        session_input: PolicyPortfolioSessionInput,
    ) -> None:
        store = FixedNotionalArtifactStore(self.root)
        for candidate in session_input.candidates:
            pair_input = candidate.pair_input
            paired = candidate.paired_fixed_notional_record
            loaded_input, loaded_paired = store.load_pair_bundle(
                manifest,
                pair_input_id=pair_input.pair_input_id,
                paired_record_id=paired.paired_record_id,
            )
            if (
                _required_hash(loaded_input),
                _required_hash(loaded_paired),
            ) != (
                candidate.pair_input_sha256,
                candidate.paired_fixed_notional_record_sha256,
            ):
                raise ShadowContractError(
                    "policy candidate differs from persisted fixed-notional lineage"
                )

    def _persist_fixed_notional_predecessors(
        self,
        manifest: ShadowProtocolManifest,
        session_input: PolicyPortfolioSessionInput,
    ) -> None:
        """Materialize embedded P2-015 candidates through their strict store.

        A candidate input embeds an already validated PairInput and paired
        fixed-notional record.  At the P2-016 storage boundary those nested
        models are born as canonical raw files, while the P2-015 store
        reconstructs and verifies their persisted P2-014 substrate.
        """

        store = FixedNotionalArtifactStore(self.root)
        for candidate in session_input.candidates:
            pair_input = candidate.pair_input
            paired = candidate.paired_fixed_notional_record
            base_lineage = store.reconstruct_base_portfolio_lineage(
                manifest,
                pair_input=pair_input,
            )
            store.persist_pair_bundle(
                manifest,
                base_lineage,
                canonical_json_bytes(pair_input),
                canonical_json_bytes(paired),
            )
        self._verify_fixed_notional_predecessors(manifest, session_input)

    def _load_and_match(
        self,
        manifest: ShadowProtocolManifest,
        kind: ArtifactKind,
        artifact_id: str,
        embedded: BaseModel,
    ) -> None:
        loaded, _ = self.load_by_reference(
            manifest,
            kind=kind,
            artifact_id=artifact_id,
        )
        if _required_hash(loaded) != _required_hash(embedded):
            raise ShadowContractError(
                f"stored {kind} differs from embedded session lineage"
            )

    def _persist(
        self,
        manifest: ShadowProtocolManifest,
        *,
        kind: ArtifactKind,
        artifact: PolicyPortfolioArtifact,
        raw_file_bytes: bytes,
    ) -> Path:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        expected = _load_kind(kind, raw_file_bytes)
        if _required_hash(expected) != _required_hash(artifact):
            raise ShadowContractError(
                "policy-portfolio raw bytes differ from supplied artifact"
            )
        self._verify_manifest_lineage(manifest, kind, expected)
        manifest_hash = _required_hash(manifest)
        canonical_hash = _required_hash(expected)
        raw_hash = _sha256(raw_file_bytes)
        artifact_id = _artifact_id(kind, expected)
        path = self._record_path(
            manifest.protocol_id,
            manifest_hash,
            kind,
            canonical_hash,
            raw_hash,
        )
        self._require_within_root(path.resolve())
        self._exclusive_create(path, raw_file_bytes)
        reference = PolicyPortfolioArtifactReference(
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
            predecessors=_named_predecessors(expected),
        )
        reference_path = self._reference_path(
            manifest.protocol_id,
            manifest_hash,
            kind,
            artifact_id,
        )
        self._require_within_root(reference_path.resolve())
        self._exclusive_create(reference_path, canonical_json_bytes(reference))
        return path

    @staticmethod
    def _verify_manifest_lineage(
        manifest: ShadowProtocolManifest,
        kind: ArtifactKind,
        artifact: PolicyPortfolioArtifact,
    ) -> None:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        manifest_hash = _required_hash(manifest)
        if kind in {"POLICY", "EVENT"}:
            return
        if kind == "CANDIDATE_INPUT":
            if not isinstance(artifact, PolicyPortfolioCandidateInput):
                raise ShadowContractError(
                    "candidate-input artifact type mismatch"
                )
            actual = (
                artifact.pair_input.manifest.protocol_id,
                artifact.pair_input.manifest.component_id,
                artifact.pair_input.manifest_sha256,
            )
            expected = (
                manifest.protocol_id,
                manifest.component_id,
                manifest_hash,
            )
            if actual != expected:
                raise ShadowContractError(
                    "CANDIDATE_INPUT artifact belongs to another manifest"
                )
            return
        actual = (
            getattr(artifact, "protocol_id", None),
            getattr(artifact, "component_id", None),
            getattr(artifact, "manifest_sha256", None),
        )
        expected = (
            manifest.protocol_id,
            manifest.component_id,
            manifest_hash,
        )
        if actual != expected:
            raise ShadowContractError(
                f"{kind} artifact belongs to another manifest"
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
            / "policy_portfolio"
            / _safe_segment(kind.lower(), "artifact namespace")
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
            / "policy_portfolio"
            / "refs"
            / _safe_segment(kind.lower(), "reference namespace")
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
                "policy-portfolio path escaped store root"
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
                    f"immutable policy-portfolio artifact collision: {path}"
                ) from None
        return path

    @staticmethod
    def _read_exact(path: Path, label: str) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ShadowContractError(f"{label} is unavailable: {path}") from exc


def _verify_common_lineage(
    manifest: ShadowProtocolManifest,
    genesis: PolicyPortfolioGenesisRecord,
    session_input: PolicyPortfolioSessionInput,
    paired: PairedPolicyPortfolioSessionRecord,
) -> None:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    manifest_hash = _required_hash(manifest)
    expected = (manifest.protocol_id, manifest.component_id, manifest_hash)
    for label, artifact in (
        ("genesis", genesis),
        ("session input", session_input),
        ("paired session", paired),
    ):
        actual = (
            artifact.protocol_id,
            artifact.component_id,
            artifact.manifest_sha256,
        )
        if actual != expected:
            raise ShadowContractError(f"policy-portfolio {label} lineage mismatch")
    if (
        genesis.baseline_manifest_id,
        genesis.baseline_manifest_sha256,
        genesis.policy_sha256,
        genesis.portfolio_policy_sha256,
        genesis.fixed_notional_policy_sha256,
        genesis.trading_calendar_sha256,
        _required_hash(genesis),
    ) != (
        manifest.baseline_manifest_id,
        manifest.baseline_manifest_sha256,
        session_input.policy_sha256,
        session_input.portfolio_policy_sha256,
        session_input.fixed_notional_policy_sha256,
        session_input.trading_calendar_sha256,
        paired.genesis_sha256,
    ):
        raise ShadowContractError(
            "policy-portfolio genesis/policy/calendar lineage mismatch"
        )
    verify_policy_portfolio_policy_binding(
        manifest,
        session_input.portfolio_policy,
        session_input.fixed_notional_policy,
        session_input.policy,
    )
    verify_policy_portfolio_session(session_input, paired)


def _artifact_id(
    kind: ArtifactKind,
    artifact: PolicyPortfolioArtifact,
) -> str:
    attribute = {
        "CANDIDATE_INPUT": "candidate_input_id",
        "CLASSIFICATION": "classification_id",
        "EVENT": "event_id",
        "GENESIS": "genesis_id",
        "LINEAGE": "lineage_id",
        "LIQUIDITY": "record_id",
        "PAIRED_SESSION": "paired_session_id",
        "POLICY": "policy_id",
        "REGIME": "regime_record_id",
        "SESSION_INPUT": "session_input_id",
        "STATE": "state_id",
        "TRANSITION": "transition_id",
    }[kind]
    value = getattr(artifact, attribute, None)
    if not isinstance(value, str) or not value:
        raise ShadowContractError(f"{kind} artifact ID is unavailable")
    return value


def _named_predecessors(
    artifact: PolicyPortfolioArtifact,
) -> tuple[PolicyPortfolioNamedPredecessor, ...]:
    payload = artifact.model_dump(mode="python")
    named: list[PolicyPortfolioNamedPredecessor] = []
    for field_name, value in payload.items():
        if field_name == "manifest_sha256":
            continue
        if field_name.endswith("_sha256") and isinstance(value, str):
            named.append(
                PolicyPortfolioNamedPredecessor(
                    name=field_name,
                    sha256=value,
                )
            )
        elif field_name.endswith("_sha256s") and isinstance(value, (tuple, list)):
            named.extend(
                PolicyPortfolioNamedPredecessor(
                    name=f"{field_name}[{index}]",
                    sha256=digest,
                )
                for index, digest in enumerate(value)
            )
    return tuple(sorted(named, key=lambda item: item.name))


def _load_kind(
    kind: ArtifactKind,
    raw_file_bytes: bytes,
) -> PolicyPortfolioArtifact:
    return _load_raw_model(
        _MODEL_BY_KIND[kind],
        raw_file_bytes,
        expected_version=_VERSION_BY_KIND[kind],
        label=f"policy-portfolio {kind.lower().replace('_', ' ')}",
    )


def _load_kind_as(kind: ArtifactKind, raw_file_bytes: bytes):
    return _load_kind(kind, raw_file_bytes)


def _load_raw_model(
    model: type[BaseModel],
    raw_file_bytes: bytes,
    *,
    expected_version: str,
    label: str,
):
    payload = _strict_json_object(raw_file_bytes, label)
    actual_version = payload.get("contract_version")
    if actual_version != expected_version:
        raise ShadowContractError(
            f"{label} contract_version must be {expected_version}; "
            f"received {actual_version!r}"
        )
    try:
        trusted = model.model_validate(payload)
    except ValueError as exc:
        raise ShadowContractError(f"{label} failed strict validation") from exc
    if canonical_json_bytes(trusted) != raw_file_bytes:
        raise ShadowContractError(f"{label} raw bytes are not canonical JSON")
    return trusted


def _load_canonical_model(
    model: type[BaseModel],
    raw_file_bytes: bytes,
    label: str,
):
    payload = _strict_json_object(raw_file_bytes, label)
    try:
        trusted = model.model_validate(payload)
    except ValueError as exc:
        raise ShadowContractError(f"{label} failed strict validation") from exc
    if canonical_json_bytes(trusted) != raw_file_bytes:
        raise ShadowContractError(f"{label} is not canonical JSON")
    return trusted


def _strict_json_object(
    raw_file_bytes: bytes,
    label: str,
) -> dict[str, object]:
    def reject_duplicates(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ShadowContractError(
                    f"{label} has duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw_file_bytes.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except ShadowContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ShadowContractError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ShadowContractError(f"{label} JSON root must be an object")
    return payload


def _revalidate(model: type[BaseModel], value: BaseModel):
    try:
        return model.model_validate(value.model_dump(mode="python"))
    except ValueError as exc:
        raise ShadowContractError(
            f"{model.__name__} failed trust-boundary validation"
        ) from exc


def _required_hash(model: BaseModel) -> str:
    digest = canonical_sha256(model)
    if digest is None:
        raise ShadowContractError("canonical SHA-256 is unavailable")
    return digest


def _lineage_id(
    *,
    protocol_id: str,
    manifest_sha256: str,
    genesis_sha256: str,
    session_input_sha256: str,
    paired_session_sha256: str,
) -> str:
    payload = json.dumps(
        {
            "genesis_sha256": genesis_sha256,
            "manifest_sha256": manifest_sha256,
            "paired_session_sha256": paired_session_sha256,
            "protocol_id": protocol_id,
            "session_input_sha256": session_input_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"PPLINEAGE-{_sha256(payload)[:32]}"


def _require_sha256(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _safe_segment(value: str, label: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value):
        raise ShadowContractError(f"unsafe {label}: {value!r}")
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "ArtifactKind",
    "POLICY_PORTFOLIO_LINEAGE_VERSION",
    "POLICY_PORTFOLIO_REFERENCE_VERSION",
    "PolicyPortfolioArtifact",
    "PolicyPortfolioArtifactReference",
    "PolicyPortfolioArtifactStore",
    "PolicyPortfolioLineageBundle",
    "PolicyPortfolioNamedPredecessor",
    "build_policy_portfolio_lineage_bundle",
    "load_paired_policy_portfolio_session_v1",
    "load_policy_candidate_classification_v1",
    "load_policy_portfolio_candidate_input_v1",
    "load_policy_portfolio_event_v1",
    "load_policy_portfolio_genesis_v1",
    "load_policy_portfolio_lineage_v1",
    "load_policy_portfolio_policy_v1",
    "load_policy_portfolio_session_input_v1",
    "load_policy_portfolio_state_v1",
    "load_policy_portfolio_transition_v1",
    "load_policy_regime_record_v1",
    "load_policy_session_liquidity_v1",
    "verify_policy_portfolio_lineage_bundle",
]
