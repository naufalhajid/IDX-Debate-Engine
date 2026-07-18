"""Pure paired candidate-decision producer for RS-P2-014."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .contracts import (
    ComponentID,
    IndependentClusterMetadata,
    ShadowContractError,
    ShadowDecision,
    ShadowObservation,
    ShadowProtocolManifest,
    canonical_sha256,
    classify_divergence,
)
from .evidence import (
    CandidateDisposition,
    CandidateEvent,
    CandidateSetManifest,
    FrozenSnapshot,
    QuarantinedCandidateEvent,
    RawCandidateSetCapture,
)
from .portfolio import (
    PortfolioArtifactStore,
    PortfolioState,
    verify_portfolio_state_binding,
)


PAIRED_DECISION_INPUT_VERSION = "shadow-paired-decision-input-v1"


class _PairedViewModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class PairedCandidateDecisionInput(_PairedViewModel):
    """The one immutable input delivered to both evaluators."""

    contract_version: Literal["shadow-paired-decision-input-v1"] = (
        PAIRED_DECISION_INPUT_VERSION
    )
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False
    protocol_id: str
    component_id: ComponentID
    manifest_sha256: str
    raw_capture_sha256: str
    candidate_set_sha256: str
    candidate_sha256: str
    snapshot_sha256: str
    feature_values_sha256: str
    portfolio_policy_sha256: str
    portfolio_source_record_sha256: str
    portfolio_state_sha256: str
    candidate: CandidateEvent
    frozen_snapshot: FrozenSnapshot
    portfolio_state: PortfolioState
    control_disposition: CandidateDisposition
    challenger_disposition: CandidateDisposition

    @field_validator(
        "manifest_sha256",
        "raw_capture_sha256",
        "candidate_set_sha256",
        "candidate_sha256",
        "snapshot_sha256",
        "feature_values_sha256",
        "portfolio_policy_sha256",
        "portfolio_source_record_sha256",
        "portfolio_state_sha256",
    )
    @classmethod
    def require_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("paired-decision hashes must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def verify_embedded_hashes(self) -> PairedCandidateDecisionInput:
        expected = (
            canonical_sha256(self.candidate),
            self.candidate.snapshot_sha256,
            canonical_sha256(self.portfolio_state.portfolio_policy),
            self.portfolio_state.portfolio_source_record_sha256,
            canonical_sha256(self.portfolio_state),
        )
        actual = (
            self.candidate_sha256,
            self.snapshot_sha256,
            self.portfolio_policy_sha256,
            self.portfolio_source_record_sha256,
            self.portfolio_state_sha256,
        )
        if actual != expected:
            raise ValueError("paired-decision embedded hash mismatch")
        if (
            self.protocol_id,
            self.component_id,
            self.manifest_sha256,
        ) != (
            self.candidate.protocol_id,
            self.candidate.component_id,
            self.candidate.manifest_sha256,
        ):
            raise ValueError("paired-decision candidate identity mismatch")
        if (
            self.manifest_sha256,
            self.raw_capture_sha256,
            self.candidate_set_sha256,
            self.portfolio_policy_sha256,
            self.portfolio_source_record_sha256,
            self.candidate.opportunity_set_id,
            self.candidate.opportunity_set_sha256,
            self.candidate.signal_at,
            self.candidate.as_of_date,
        ) != (
            self.portfolio_state.manifest_sha256,
            self.portfolio_state.raw_capture_sha256,
            self.portfolio_state.candidate_set_sha256,
            self.portfolio_state.portfolio_policy_sha256,
            self.portfolio_state.portfolio_source_record_sha256,
            self.portfolio_state.opportunity_set_id,
            self.portfolio_state.opportunity_set_sha256,
            self.portfolio_state.signal_at,
            self.portfolio_state.as_of_date,
        ):
            raise ValueError("paired-decision state lineage mismatch")
        if (
            self.frozen_snapshot.snapshot_id,
            self.frozen_snapshot.snapshot_sha256,
            self.frozen_snapshot.ticker,
        ) != (
            self.candidate.snapshot_id,
            self.candidate.snapshot_sha256,
            self.candidate.ticker,
        ):
            raise ValueError("paired-decision snapshot identity mismatch")
        if (
            self.control_disposition.raw_event_id
            != self.candidate.raw_event_id
            or self.challenger_disposition.raw_event_id
            != self.candidate.raw_event_id
        ):
            raise ValueError("paired dispositions refer to another candidate")
        return self


class DecisionEvaluator(Protocol):
    def __call__(
        self,
        decision_input: PairedCandidateDecisionInput,
    ) -> ShadowDecision: ...


class PairedDecisionAuthorizationLoader(Protocol):
    """Store-backed A1/closure precheck used before the first evaluator call."""

    def verify_paired_evaluation_authorization(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
        signal_at: datetime,
        attempted_at: datetime,
    ) -> object: ...


def produce_paired_observation(
    *,
    manifest: ShadowProtocolManifest,
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
    candidate: CandidateEvent,
    frozen_snapshot: FrozenSnapshot,
    feature_values_sha256: str,
    portfolio_state_sha256: str,
    artifact_store: PortfolioArtifactStore,
    authorization_loader: PairedDecisionAuthorizationLoader,
    approval_ledger_id: str,
    control_evaluator: DecisionEvaluator,
    challenger_evaluator: DecisionEvaluator,
    cluster: IndependentClusterMetadata,
    captured_at: datetime,
) -> ShadowObservation:
    """Evaluate both sides against one exact persisted pre-batch state."""

    manifest = _revalidate(ShadowProtocolManifest, manifest)
    raw_capture = _revalidate(RawCandidateSetCapture, raw_capture)
    candidate_set = _revalidate(CandidateSetManifest, candidate_set)
    if isinstance(candidate, QuarantinedCandidateEvent):
        raise ShadowContractError("quarantined candidate cannot be evaluated")
    candidate = _revalidate(CandidateEvent, candidate)
    frozen_snapshot = _revalidate(FrozenSnapshot, frozen_snapshot)
    cluster = _revalidate(IndependentClusterMetadata, cluster)
    if captured_at.utcoffset() is None:
        raise ShadowContractError("observation capture time must be timezone-aware")
    try:
        authorization_loader.verify_paired_evaluation_authorization(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_required_hash(manifest),
            ledger_id=approval_ledger_id,
            signal_at=candidate.signal_at,
            attempted_at=captured_at,
        )
    except (AttributeError, OSError, ValueError) as exc:
        raise ShadowContractError(
            "current paired-evaluation authorization is unavailable"
        ) from exc

    policy = artifact_store.load_policy_for_manifest(manifest)
    state, _ = artifact_store.load_state_by_hash(
        manifest,
        portfolio_state_sha256,
    )
    source_record, _ = artifact_store.load_source_record(
        manifest,
        state.portfolio_source_record_id,
    )
    verify_portfolio_state_binding(
        manifest=manifest,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        policy=policy,
        source_record=source_record,
        state=state,
    )
    _verify_candidate_and_snapshot(
        raw_capture,
        candidate_set,
        candidate,
        frozen_snapshot,
    )
    if state.captured_at > captured_at:
        raise ShadowContractError("observation capture precedes portfolio state")
    control_disposition = _disposition_for(
        candidate_set.control_view.dispositions,
        candidate.raw_event_id,
    )
    challenger_disposition = _disposition_for(
        candidate_set.challenger_view.dispositions,
        candidate.raw_event_id,
    )
    decision_input = PairedCandidateDecisionInput(
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=_required_hash(manifest),
        raw_capture_sha256=_required_hash(raw_capture),
        candidate_set_sha256=_required_hash(candidate_set),
        candidate_sha256=_required_hash(candidate),
        snapshot_sha256=frozen_snapshot.snapshot_sha256,
        feature_values_sha256=feature_values_sha256,
        portfolio_policy_sha256=_required_hash(policy),
        portfolio_source_record_sha256=_required_hash(source_record),
        portfolio_state_sha256=_required_hash(state),
        candidate=candidate,
        frozen_snapshot=frozen_snapshot,
        portfolio_state=state,
        control_disposition=control_disposition,
        challenger_disposition=challenger_disposition,
    )
    input_hash = _required_hash(decision_input)
    state_hash = _required_hash(state)

    control = _evaluate_side(
        control_evaluator,
        decision_input,
        role="CONTROL",
        disposition=control_disposition,
    )
    _verify_unchanged_input(decision_input, input_hash, state_hash)
    challenger = _evaluate_side(
        challenger_evaluator,
        decision_input,
        role="CHALLENGER",
        disposition=challenger_disposition,
    )
    _verify_unchanged_input(decision_input, input_hash, state_hash)

    # Reload exact bytes after both calls.  A caller object and an opaque hash
    # are never trusted in place of the immutable artifact.
    reloaded_state, _ = artifact_store.load_state_by_hash(
        manifest,
        state_hash,
    )
    if canonical_sha256(reloaded_state) != state_hash:
        raise ShadowContractError("persisted portfolio state changed during evaluation")
    observation_id = _observation_id(
        protocol_id=manifest.protocol_id,
        manifest_sha256=_required_hash(manifest),
        raw_event_id=candidate.raw_event_id,
        paired_input_sha256=input_hash,
        cluster_sha256=_required_hash(cluster),
        control_decision_sha256=control.decision_payload_sha256,
        challenger_decision_sha256=challenger.decision_payload_sha256,
    )
    try:
        observation = ShadowObservation(
            protocol_id=manifest.protocol_id,
            component_id=manifest.component_id,
            manifest_sha256=_required_hash(manifest),
            candidate_set_id=candidate_set.candidate_set_id,
            candidate_set_sha256=_required_hash(candidate_set),
            observation_id=observation_id,
            raw_event_id=candidate.raw_event_id,
            ticker=candidate.ticker,
            signal_at=candidate.signal_at,
            as_of_date=candidate.as_of_date,
            captured_at=captured_at,
            opportunity_set_id=candidate.opportunity_set_id,
            opportunity_set_sha256=candidate.opportunity_set_sha256,
            snapshot_id=candidate.snapshot_id,
            snapshot_sha256=candidate.snapshot_sha256,
            feature_values_sha256=feature_values_sha256,
            portfolio_state_sha256=state_hash,
            cluster_rule_sha256=manifest.cluster_rules_sha256,
            independent_cluster_id=cluster.cluster_id,
            cluster=cluster,
            control_decision=control,
            challenger_decision=challenger,
            divergence=classify_divergence(control, challenger),
        )
    except ValueError as exc:
        raise ShadowContractError("paired observation is invalid") from exc
    artifact_store.verify_observation_state(manifest, observation)
    return observation


def _evaluate_side(
    evaluator: DecisionEvaluator,
    decision_input: PairedCandidateDecisionInput,
    *,
    role: Literal["CONTROL", "CHALLENGER"],
    disposition: CandidateDisposition,
) -> ShadowDecision:
    try:
        decision = ShadowDecision.model_validate(
            evaluator(decision_input).model_dump(mode="python")
        )
    except (AttributeError, ValueError, TypeError) as exc:
        raise ShadowContractError(f"{role} evaluator returned an invalid decision") from exc
    if decision.decision_role != role:
        raise ShadowContractError(f"{role} evaluator returned the wrong role")
    if disposition.state == "PRUNED":
        if decision.would_be_actionable or decision.would_allocate:
            raise ShadowContractError(f"{role} pruned candidate became actionable")
        missing_reasons = set(disposition.reason_codes) - set(decision.reason_codes)
        if missing_reasons:
            raise ShadowContractError(
                f"{role} pruned decision omitted disposition reasons"
            )
    return decision


def _verify_unchanged_input(
    decision_input: PairedCandidateDecisionInput,
    expected_input_hash: str,
    expected_state_hash: str,
) -> None:
    try:
        trusted = PairedCandidateDecisionInput.model_validate(
            decision_input.model_dump(mode="python")
        )
    except ValueError as exc:
        raise ShadowContractError("evaluator mutated paired input") from exc
    if (
        canonical_sha256(trusted) != expected_input_hash
        or canonical_sha256(trusted.portfolio_state) != expected_state_hash
    ):
        raise ShadowContractError("evaluator mutated paired input or portfolio state")


def _verify_candidate_and_snapshot(
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
    candidate: CandidateEvent,
    snapshot: FrozenSnapshot,
) -> None:
    member = next(
        (
            item
            for item in raw_capture.candidates
            if item.raw_event_id == candidate.raw_event_id
        ),
        None,
    )
    if member is None or isinstance(member, QuarantinedCandidateEvent):
        raise ShadowContractError("candidate is unavailable or quarantined")
    if canonical_sha256(member) != canonical_sha256(candidate):
        raise ShadowContractError("candidate differs from raw capture")
    if (
        candidate_set.raw_capture_id,
        candidate_set.raw_capture_sha256,
        candidate_set.opportunity_set_id,
        candidate_set.opportunity_set_sha256,
    ) != (
        raw_capture.raw_capture_id,
        _required_hash(raw_capture),
        raw_capture.opportunity_set_id,
        raw_capture.opportunity_set_sha256,
    ):
        raise ShadowContractError("candidate set differs from raw capture")
    if not (
        candidate.captured_at
        <= raw_capture.captured_at
        <= candidate_set.captured_at
    ):
        raise ShadowContractError("candidate evidence chronology is not causal")
    if (
        snapshot.snapshot_id,
        snapshot.snapshot_sha256,
        snapshot.ticker,
        snapshot.as_of_date,
    ) != (
        candidate.snapshot_id,
        candidate.snapshot_sha256,
        candidate.ticker,
        candidate.as_of_date,
    ):
        raise ShadowContractError("candidate snapshot differs from exact artifact")


def _disposition_for(
    dispositions: tuple[CandidateDisposition, ...],
    raw_event_id: str,
) -> CandidateDisposition:
    matches = tuple(item for item in dispositions if item.raw_event_id == raw_event_id)
    if len(matches) != 1:
        raise ShadowContractError("candidate disposition is missing or duplicated")
    return matches[0]


def _observation_id(
    *,
    protocol_id: str,
    manifest_sha256: str,
    raw_event_id: str,
    paired_input_sha256: str,
    cluster_sha256: str,
    control_decision_sha256: str,
    challenger_decision_sha256: str,
) -> str:
    payload = {
        "challenger_decision_sha256": challenger_decision_sha256,
        "cluster_sha256": cluster_sha256,
        "control_decision_sha256": control_decision_sha256,
        "manifest_sha256": manifest_sha256,
        "paired_input_sha256": paired_input_sha256,
        "protocol_id": protocol_id,
        "raw_event_id": raw_event_id,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"OBS-{digest[:32]}"


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


__all__ = [
    "DecisionEvaluator",
    "PAIRED_DECISION_INPUT_VERSION",
    "PairedCandidateDecisionInput",
    "PairedDecisionAuthorizationLoader",
    "produce_paired_observation",
]
