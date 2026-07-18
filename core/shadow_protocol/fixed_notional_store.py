"""Immutable content-addressed storage for RS-P2-015 artifacts.

The store treats canonical model identity and raw-file identity as separate
trust boundaries.  Every loader rejects duplicate JSON keys, non-canonical
bytes, wrong contract families, path substitution, byte-length drift, raw
tampering, canonical tampering, and cross-protocol artifact transplants.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, model_validator

from .calendar import TradingCalendar
from .contracts import (
    ShadowContractError,
    ShadowProtocolManifest,
    canonical_json_bytes,
    canonical_sha256,
)
from .fixed_notional import (
    FIXED_NOTIONAL_BAR_SERIES_VERSION,
    FIXED_NOTIONAL_CASH_FLOW_VERSION,
    FIXED_NOTIONAL_HOLDING_VERSION,
    FIXED_NOTIONAL_LIFECYCLE_VERSION,
    FIXED_NOTIONAL_LIQUIDITY_VERSION,
    FIXED_NOTIONAL_PAIR_INPUT_VERSION,
    FIXED_NOTIONAL_PAIRED_RECORD_VERSION,
    FIXED_NOTIONAL_POLICY_VERSION,
    FixedNotionalBarSeries,
    FixedNotionalCashFlowRecord,
    FixedNotionalHoldingRecord,
    FixedNotionalLifecycle,
    FixedNotionalLiquidityRecord,
    FixedNotionalPairInput,
    FrozenFixedNotionalPolicy,
    PairedFixedNotionalRecord,
    verify_fixed_notional_policy_binding,
    verify_paired_fixed_notional_record,
)
from .evidence import CandidateSetStore
from .portfolio import (
    FrozenPortfolioPolicy,
    PortfolioArtifactStore,
    PortfolioLineageBundle,
    build_portfolio_lineage_bundle,
)


FIXED_NOTIONAL_LINEAGE_VERSION = "shadow-fixed-notional-lineage-v1"
FIXED_NOTIONAL_LINEAGE_REFERENCE_VERSION = (
    "shadow-fixed-notional-lineage-reference-v1"
)
FIXED_NOTIONAL_REFERENCE_VERSION = "shadow-fixed-notional-reference-v1"


ArtifactKind: TypeAlias = Literal[
    "BAR_SERIES",
    "CASH_FLOW",
    "HOLDING",
    "INPUT",
    "LIFECYCLE",
    "LINEAGE",
    "LIQUIDITY",
    "PAIRED_RECORD",
    "POLICY",
]


class _StoreModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class FixedNotionalLineageBundle(_StoreModel):
    """Reconstructable RS-P2-014 + RS-P2-015 hash graph."""

    contract_version: Literal["shadow-fixed-notional-lineage-v1"] = (
        FIXED_NOTIONAL_LINEAGE_VERSION
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
    base_portfolio_lineage: PortfolioLineageBundle
    base_portfolio_lineage_sha256: str
    fixed_notional_policy_id: str
    fixed_notional_policy_sha256: str
    pair_input_id: str
    pair_input_sha256: str
    liquidity_record_id: str
    liquidity_record_sha256: str
    bar_series_id: str
    bar_series_sha256: str
    paired_record_id: str
    paired_record_sha256: str
    control_lifecycle_sha256s: tuple[str, ...]
    challenger_lifecycle_sha256s: tuple[str, ...]
    ordered_holding_sha256s: tuple[str, ...]
    ordered_cash_flow_sha256s: tuple[str, ...]
    lineage_valid: Literal[True] = True

    @model_validator(mode="after")
    def verify_lineage_hashes(self) -> FixedNotionalLineageBundle:
        _require_sha256(self.manifest_sha256, "manifest_sha256")
        hash_fields = (
            self.base_portfolio_lineage_sha256,
            self.fixed_notional_policy_sha256,
            self.pair_input_sha256,
            self.liquidity_record_sha256,
            self.bar_series_sha256,
            self.paired_record_sha256,
            *self.control_lifecycle_sha256s,
            *self.challenger_lifecycle_sha256s,
            *self.ordered_holding_sha256s,
            *self.ordered_cash_flow_sha256s,
        )
        for digest in hash_fields:
            _require_sha256(digest, "lineage SHA-256")
        if (
            canonical_sha256(self.base_portfolio_lineage)
            != self.base_portfolio_lineage_sha256
        ):
            raise ValueError("embedded base portfolio-lineage SHA-256 mismatch")
        expected_id = _lineage_id(
            protocol_id=self.protocol_id,
            manifest_sha256=self.manifest_sha256,
            base_lineage_sha256=self.base_portfolio_lineage_sha256,
            pair_input_sha256=self.pair_input_sha256,
            paired_record_sha256=self.paired_record_sha256,
        )
        if self.lineage_id != expected_id:
            raise ValueError("fixed-notional lineage ID mismatch")
        return self


class FixedNotionalNamedPredecessor(_StoreModel):
    """One deterministic, name-addressed predecessor hash."""

    name: str
    sha256: str

    @model_validator(mode="after")
    def verify_hash(self) -> FixedNotionalNamedPredecessor:
        if not re.fullmatch(r"[a-z][a-z0-9_.\[\]-]{0,127}", self.name):
            raise ValueError("predecessor name is not canonical")
        _require_sha256(self.sha256, f"{self.name} SHA-256")
        return self


class FixedNotionalLineageReference(_StoreModel):
    contract_version: Literal[
        "shadow-fixed-notional-lineage-reference-v1"
    ] = FIXED_NOTIONAL_LINEAGE_REFERENCE_VERSION
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False
    protocol_id: str
    component_id: str
    manifest_sha256: str
    artifact_kind: Literal["LINEAGE"] = "LINEAGE"
    artifact_id: str
    artifact_contract_version: Literal[
        "shadow-fixed-notional-lineage-v1"
    ] = FIXED_NOTIONAL_LINEAGE_VERSION
    artifact_canonical_sha256: str
    artifact_raw_file_sha256: str
    artifact_raw_byte_length: int
    artifact_relative_path: str
    base_portfolio_lineage_sha256: str
    pair_input_id: str
    pair_input_sha256: str
    paired_record_id: str
    paired_record_sha256: str
    predecessors: tuple[FixedNotionalNamedPredecessor, ...]

    @model_validator(mode="after")
    def verify_reference_hashes(self) -> FixedNotionalLineageReference:
        for digest in (
            self.manifest_sha256,
            self.artifact_canonical_sha256,
            self.artifact_raw_file_sha256,
            self.base_portfolio_lineage_sha256,
            self.pair_input_sha256,
            self.paired_record_sha256,
        ):
            _require_sha256(digest, "lineage-reference SHA-256")
        if self.artifact_raw_byte_length <= 0:
            raise ValueError("lineage-reference byte length must be positive")
        names = tuple(item.name for item in self.predecessors)
        if names != tuple(sorted(names)) or len(set(names)) != len(names):
            raise ValueError(
                "lineage predecessor names must be unique and ordered"
            )
        return self


class FixedNotionalGraphReference(_StoreModel):
    """Dual-hash artifact reference with all named predecessor hashes."""

    contract_version: Literal["shadow-fixed-notional-reference-v1"] = (
        FIXED_NOTIONAL_REFERENCE_VERSION
    )
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False
    protocol_id: str
    component_id: str
    manifest_sha256: str
    artifact_kind: Literal[
        "BAR_SERIES",
        "CASH_FLOW",
        "HOLDING",
        "INPUT",
        "LIFECYCLE",
        "LIQUIDITY",
        "PAIRED_RECORD",
        "POLICY",
    ]
    artifact_id: str
    artifact_contract_version: str
    artifact_canonical_sha256: str
    artifact_raw_file_sha256: str
    artifact_raw_byte_length: int
    artifact_relative_path: str
    predecessors: tuple[FixedNotionalNamedPredecessor, ...]

    @model_validator(mode="after")
    def verify_reference_hashes(
        self,
    ) -> FixedNotionalGraphReference:
        for digest in (
            self.manifest_sha256,
            self.artifact_canonical_sha256,
            self.artifact_raw_file_sha256,
        ):
            _require_sha256(digest, "artifact-reference SHA-256")
        if self.artifact_raw_byte_length <= 0:
            raise ValueError("artifact-reference byte length must be positive")
        names = tuple(item.name for item in self.predecessors)
        if names != tuple(sorted(names)) or len(set(names)) != len(names):
            raise ValueError(
                "predecessor names must be unique and deterministically ordered"
            )
        return self


FixedNotionalReference: TypeAlias = (
    FixedNotionalGraphReference | FixedNotionalLineageReference
)
FixedNotionalArtifact: TypeAlias = (
    FrozenFixedNotionalPolicy
    | FixedNotionalLiquidityRecord
    | FixedNotionalBarSeries
    | FixedNotionalPairInput
    | FixedNotionalLifecycle
    | FixedNotionalHoldingRecord
    | FixedNotionalCashFlowRecord
    | PairedFixedNotionalRecord
    | FixedNotionalLineageBundle
)

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MODEL_BY_KIND: dict[ArtifactKind, type[BaseModel]] = {
    "BAR_SERIES": FixedNotionalBarSeries,
    "CASH_FLOW": FixedNotionalCashFlowRecord,
    "HOLDING": FixedNotionalHoldingRecord,
    "INPUT": FixedNotionalPairInput,
    "LIFECYCLE": FixedNotionalLifecycle,
    "LINEAGE": FixedNotionalLineageBundle,
    "LIQUIDITY": FixedNotionalLiquidityRecord,
    "PAIRED_RECORD": PairedFixedNotionalRecord,
    "POLICY": FrozenFixedNotionalPolicy,
}
_VERSION_BY_KIND: dict[ArtifactKind, str] = {
    "BAR_SERIES": FIXED_NOTIONAL_BAR_SERIES_VERSION,
    "CASH_FLOW": FIXED_NOTIONAL_CASH_FLOW_VERSION,
    "HOLDING": FIXED_NOTIONAL_HOLDING_VERSION,
    "INPUT": FIXED_NOTIONAL_PAIR_INPUT_VERSION,
    "LIFECYCLE": FIXED_NOTIONAL_LIFECYCLE_VERSION,
    "LINEAGE": FIXED_NOTIONAL_LINEAGE_VERSION,
    "LIQUIDITY": FIXED_NOTIONAL_LIQUIDITY_VERSION,
    "PAIRED_RECORD": FIXED_NOTIONAL_PAIRED_RECORD_VERSION,
    "POLICY": FIXED_NOTIONAL_POLICY_VERSION,
}


def load_fixed_notional_policy_v1(
    raw_file_bytes: bytes,
) -> FrozenFixedNotionalPolicy:
    return _load_raw_model(
        FrozenFixedNotionalPolicy,
        raw_file_bytes,
        expected_version=FIXED_NOTIONAL_POLICY_VERSION,
        label="fixed-notional policy",
    )


def load_fixed_notional_liquidity_v1(
    raw_file_bytes: bytes,
) -> FixedNotionalLiquidityRecord:
    return _load_raw_model(
        FixedNotionalLiquidityRecord,
        raw_file_bytes,
        expected_version=FIXED_NOTIONAL_LIQUIDITY_VERSION,
        label="fixed-notional liquidity",
    )


def load_fixed_notional_bar_series_v1(
    raw_file_bytes: bytes,
) -> FixedNotionalBarSeries:
    return _load_raw_model(
        FixedNotionalBarSeries,
        raw_file_bytes,
        expected_version=FIXED_NOTIONAL_BAR_SERIES_VERSION,
        label="fixed-notional bar series",
    )


def load_fixed_notional_pair_input_v1(
    raw_file_bytes: bytes,
) -> FixedNotionalPairInput:
    return _load_raw_model(
        FixedNotionalPairInput,
        raw_file_bytes,
        expected_version=FIXED_NOTIONAL_PAIR_INPUT_VERSION,
        label="fixed-notional pair input",
    )


def load_fixed_notional_lifecycle_v1(
    raw_file_bytes: bytes,
) -> FixedNotionalLifecycle:
    return _load_raw_model(
        FixedNotionalLifecycle,
        raw_file_bytes,
        expected_version=FIXED_NOTIONAL_LIFECYCLE_VERSION,
        label="fixed-notional lifecycle",
    )


def load_fixed_notional_holding_v1(
    raw_file_bytes: bytes,
) -> FixedNotionalHoldingRecord:
    return _load_raw_model(
        FixedNotionalHoldingRecord,
        raw_file_bytes,
        expected_version=FIXED_NOTIONAL_HOLDING_VERSION,
        label="fixed-notional holding",
    )


def load_fixed_notional_cash_flow_v1(
    raw_file_bytes: bytes,
) -> FixedNotionalCashFlowRecord:
    return _load_raw_model(
        FixedNotionalCashFlowRecord,
        raw_file_bytes,
        expected_version=FIXED_NOTIONAL_CASH_FLOW_VERSION,
        label="fixed-notional cash flow",
    )


def load_paired_fixed_notional_record_v1(
    raw_file_bytes: bytes,
) -> PairedFixedNotionalRecord:
    return _load_raw_model(
        PairedFixedNotionalRecord,
        raw_file_bytes,
        expected_version=FIXED_NOTIONAL_PAIRED_RECORD_VERSION,
        label="paired fixed-notional record",
    )


def load_fixed_notional_lineage_v1(
    raw_file_bytes: bytes,
) -> FixedNotionalLineageBundle:
    return _load_raw_model(
        FixedNotionalLineageBundle,
        raw_file_bytes,
        expected_version=FIXED_NOTIONAL_LINEAGE_VERSION,
        label="fixed-notional lineage bundle",
    )


def build_fixed_notional_lineage_bundle(
    *,
    manifest: ShadowProtocolManifest,
    base_portfolio_lineage: PortfolioLineageBundle,
    pair_input: FixedNotionalPairInput,
    paired_record: PairedFixedNotionalRecord,
) -> FixedNotionalLineageBundle:
    """Compose the exact RS-P2-014 lineage with every RS-P2-015 node."""

    manifest = _revalidate(ShadowProtocolManifest, manifest)
    base = _revalidate(PortfolioLineageBundle, base_portfolio_lineage)
    pair_input = _revalidate(FixedNotionalPairInput, pair_input)
    paired = _revalidate(PairedFixedNotionalRecord, paired_record)
    _verify_pair_binding(manifest, pair_input, paired)
    manifest_hash = _required_hash(manifest)
    base_hash = _required_hash(base)
    if (
        base.protocol_id,
        base.component_id,
        base.manifest_sha256,
        base.portfolio_policy_sha256,
        base.portfolio_state_sha256,
        base.observation_id,
        base.observation_sha256,
    ) != (
        manifest.protocol_id,
        manifest.component_id,
        manifest_hash,
        pair_input.portfolio_state.portfolio_policy_sha256,
        pair_input.portfolio_state_sha256,
        pair_input.observation.observation_id,
        pair_input.observation_sha256,
    ):
        raise ShadowContractError(
            "base portfolio lineage differs from fixed-notional input"
        )
    control = (*paired.control_secondary, paired.control)
    challenger = (*paired.challenger_secondary, paired.challenger)
    lifecycles = (*control, *challenger)
    control_hashes = tuple(_required_hash(item) for item in control)
    challenger_hashes = tuple(_required_hash(item) for item in challenger)
    holding_hashes = tuple(
        _required_hash(event)
        for lifecycle in lifecycles
        for event in lifecycle.holding_records
    )
    cash_hashes = tuple(
        _required_hash(event)
        for lifecycle in lifecycles
        for event in lifecycle.cash_flow_records
    )
    pair_input_hash = _required_hash(pair_input)
    paired_hash = _required_hash(paired)
    lineage_id = _lineage_id(
        protocol_id=manifest.protocol_id,
        manifest_sha256=manifest_hash,
        base_lineage_sha256=base_hash,
        pair_input_sha256=pair_input_hash,
        paired_record_sha256=paired_hash,
    )
    try:
        return FixedNotionalLineageBundle(
            lineage_id=lineage_id,
            protocol_id=manifest.protocol_id,
            component_id=manifest.component_id,
            manifest_sha256=manifest_hash,
            base_portfolio_lineage=base,
            base_portfolio_lineage_sha256=base_hash,
            fixed_notional_policy_id=pair_input.policy.policy_id,
            fixed_notional_policy_sha256=pair_input.policy_sha256,
            pair_input_id=pair_input.pair_input_id,
            pair_input_sha256=pair_input_hash,
            liquidity_record_id=pair_input.liquidity.record_id,
            liquidity_record_sha256=pair_input.liquidity_sha256,
            bar_series_id=_bar_series_id(pair_input.bar_series),
            bar_series_sha256=pair_input.bar_series_sha256,
            paired_record_id=paired.paired_record_id,
            paired_record_sha256=paired_hash,
            control_lifecycle_sha256s=control_hashes,
            challenger_lifecycle_sha256s=challenger_hashes,
            ordered_holding_sha256s=holding_hashes,
            ordered_cash_flow_sha256s=cash_hashes,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "fixed-notional lineage bundle is invalid"
        ) from exc


def verify_fixed_notional_lineage_bundle(
    bundle: FixedNotionalLineageBundle,
    *,
    manifest: ShadowProtocolManifest,
    base_portfolio_lineage: PortfolioLineageBundle,
    pair_input: FixedNotionalPairInput,
    paired_record: PairedFixedNotionalRecord,
) -> FixedNotionalLineageBundle:
    trusted = _revalidate(FixedNotionalLineageBundle, bundle)
    rebuilt = build_fixed_notional_lineage_bundle(
        manifest=manifest,
        base_portfolio_lineage=base_portfolio_lineage,
        pair_input=pair_input,
        paired_record=paired_record,
    )
    if _required_hash(trusted) != _required_hash(rebuilt):
        raise ShadowContractError(
            "fixed-notional lineage differs from exact reconstruction"
        )
    return trusted


class FixedNotionalArtifactStore:
    """Exclusive-create store with dual-hash references and replay lineage."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def persist_policy(
        self,
        manifest: ShadowProtocolManifest,
        portfolio_policy: FrozenPortfolioPolicy,
        raw_file_bytes: bytes,
    ) -> Path:
        policy = load_fixed_notional_policy_v1(raw_file_bytes)
        verify_fixed_notional_policy_binding(
            manifest,
            portfolio_policy,
            policy,
        )
        return self._persist(
            manifest,
            kind="POLICY",
            artifact=policy,
            artifact_id=policy.policy_id,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_liquidity(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_fixed_notional_liquidity_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "LIQUIDITY", artifact)
        return self._persist(
            manifest,
            kind="LIQUIDITY",
            artifact=artifact,
            artifact_id=artifact.record_id,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_bar_series(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_fixed_notional_bar_series_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "BAR_SERIES", artifact)
        return self._persist(
            manifest,
            kind="BAR_SERIES",
            artifact=artifact,
            artifact_id=_bar_series_id(artifact),
            raw_file_bytes=raw_file_bytes,
        )

    def persist_input(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_fixed_notional_pair_input_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "INPUT", artifact)
        return self._persist(
            manifest,
            kind="INPUT",
            artifact=artifact,
            artifact_id=artifact.pair_input_id,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_lifecycle(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
        *,
        trading_calendar: TradingCalendar | None = None,
    ) -> Path:
        artifact = load_fixed_notional_lifecycle_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "LIFECYCLE", artifact)
        if artifact.cash_flow_records:
            if trading_calendar is None:
                raise ShadowContractError(
                    "trusted trading calendar is required for lifecycle cash flows"
                )
            for cash_flow in artifact.cash_flow_records:
                _verify_cash_flow_calendar(
                    cash_flow,
                    trading_calendar,
                    manifest,
                )
        return self._persist(
            manifest,
            kind="LIFECYCLE",
            artifact=artifact,
            artifact_id=artifact.lifecycle_id,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_lineage(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
        *,
        pair_input_id: str,
        paired_record_id: str,
    ) -> Path:
        artifact = load_fixed_notional_lineage_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "LINEAGE", artifact)
        pair_input, paired = self.load_pair_bundle(
            manifest,
            pair_input_id=pair_input_id,
            paired_record_id=paired_record_id,
        )
        base = self.reconstruct_base_portfolio_lineage(
            manifest,
            pair_input=pair_input,
        )
        verify_fixed_notional_lineage_bundle(
            artifact,
            manifest=manifest,
            base_portfolio_lineage=base,
            pair_input=pair_input,
            paired_record=paired,
        )
        return self._persist(
            manifest,
            kind="LINEAGE",
            artifact=artifact,
            artifact_id=artifact.lineage_id,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_holding(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_fixed_notional_holding_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "HOLDING", artifact)
        return self._persist(
            manifest,
            kind="HOLDING",
            artifact=artifact,
            artifact_id=artifact.holding_event_id,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_cash_flow(
        self,
        manifest: ShadowProtocolManifest,
        trading_calendar: TradingCalendar,
        raw_file_bytes: bytes,
    ) -> Path:
        artifact = load_fixed_notional_cash_flow_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "CASH_FLOW", artifact)
        _verify_cash_flow_calendar(artifact, trading_calendar, manifest)
        return self._persist(
            manifest,
            kind="CASH_FLOW",
            artifact=artifact,
            artifact_id=artifact.cash_flow_id,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_paired_record(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
        *,
        pair_input_id: str,
    ) -> Path:
        artifact = load_paired_fixed_notional_record_v1(raw_file_bytes)
        self._verify_manifest_lineage(manifest, "PAIRED_RECORD", artifact)
        pair_input, _ = self.load_by_reference(
            manifest,
            kind="INPUT",
            artifact_id=pair_input_id,
        )
        if not isinstance(pair_input, FixedNotionalPairInput):
            raise ShadowContractError(
                "paired-record predecessor returned wrong type"
            )
        _verify_pair_binding(manifest, pair_input, artifact)
        return self._persist(
            manifest,
            kind="PAIRED_RECORD",
            artifact=artifact,
            artifact_id=artifact.paired_record_id,
            raw_file_bytes=raw_file_bytes,
        )

    def persist_pair_bundle(
        self,
        manifest: ShadowProtocolManifest,
        base_portfolio_lineage: PortfolioLineageBundle,
        pair_input_raw_file_bytes: bytes,
        paired_record_raw_file_bytes: bytes,
    ) -> dict[str, tuple[Path, ...]]:
        """Persist a pair and every independently addressable dependency.

        Nested artifacts are serialized canonically because their independent
        raw file is born at this boundary.  The input and paired-record raw
        byte identities remain exactly the caller-supplied identities.
        """

        manifest = _revalidate(ShadowProtocolManifest, manifest)
        pair_input = load_fixed_notional_pair_input_v1(
            pair_input_raw_file_bytes
        )
        paired = load_paired_fixed_notional_record_v1(
            paired_record_raw_file_bytes
        )
        _verify_pair_binding(manifest, pair_input, paired)
        reconstructed_base = self.reconstruct_base_portfolio_lineage(
            manifest,
            pair_input=pair_input,
        )
        supplied_base = _revalidate(
            PortfolioLineageBundle,
            base_portfolio_lineage,
        )
        if _required_hash(supplied_base) != _required_hash(
            reconstructed_base
        ):
            raise ShadowContractError(
                "caller base portfolio lineage differs from persisted substrate"
            )
        lineage = build_fixed_notional_lineage_bundle(
            manifest=manifest,
            base_portfolio_lineage=reconstructed_base,
            pair_input=pair_input,
            paired_record=paired,
        )

        policy_path = self.persist_policy(
            manifest,
            pair_input.portfolio_state.portfolio_policy,
            canonical_json_bytes(pair_input.policy),
        )
        liquidity_path = self.persist_liquidity(
            manifest,
            canonical_json_bytes(pair_input.liquidity),
        )
        bar_path = self.persist_bar_series(
            manifest,
            canonical_json_bytes(pair_input.bar_series),
        )
        input_path = self.persist_input(
            manifest,
            pair_input_raw_file_bytes,
        )

        lifecycles = (
            paired.control,
            *paired.control_secondary,
            paired.challenger,
            *paired.challenger_secondary,
        )
        holding_paths: list[Path] = []
        cash_paths: list[Path] = []
        lifecycle_paths: list[Path] = []
        for lifecycle in lifecycles:
            for holding in lifecycle.holding_records:
                holding_paths.append(
                    self.persist_holding(
                        manifest,
                        canonical_json_bytes(holding),
                    )
                )
            for cash_flow in lifecycle.cash_flow_records:
                cash_paths.append(
                    self.persist_cash_flow(
                        manifest,
                        paired.trading_calendar,
                        canonical_json_bytes(cash_flow),
                    )
                )
            lifecycle_paths.append(
                self.persist_lifecycle(
                    manifest,
                    canonical_json_bytes(lifecycle),
                    trading_calendar=paired.trading_calendar,
                )
            )
        paired_path = self.persist_paired_record(
            manifest,
            paired_record_raw_file_bytes,
            pair_input_id=pair_input.pair_input_id,
        )
        lineage_path = self.persist_lineage(
            manifest,
            canonical_json_bytes(lineage),
            pair_input_id=pair_input.pair_input_id,
            paired_record_id=paired.paired_record_id,
        )

        # Reload every independent node. This catches a pre-existing tampered
        # reference before a bundle can be reported as persisted.
        self.load_by_reference(
            manifest,
            kind="INPUT",
            artifact_id=pair_input.pair_input_id,
        )
        for lifecycle in lifecycles:
            self.load_by_reference(
                manifest,
                kind="LIFECYCLE",
                artifact_id=lifecycle.lifecycle_id,
                trading_calendar=paired.trading_calendar,
            )
            for holding in lifecycle.holding_records:
                self.load_by_reference(
                    manifest,
                    kind="HOLDING",
                    artifact_id=holding.holding_event_id,
                )
            for cash_flow in lifecycle.cash_flow_records:
                self.load_by_reference(
                    manifest,
                    kind="CASH_FLOW",
                    artifact_id=cash_flow.cash_flow_id,
                    trading_calendar=paired.trading_calendar,
                )
        self.load_by_reference(
            manifest,
            kind="PAIRED_RECORD",
            artifact_id=paired.paired_record_id,
            pair_input_id=pair_input.pair_input_id,
        )
        loaded_lineage = self.load_verified_lineage(
            manifest,
            lineage_id=lineage.lineage_id,
            pair_input_id=pair_input.pair_input_id,
            paired_record_id=paired.paired_record_id,
        )
        if _required_hash(loaded_lineage) != _required_hash(lineage):
            raise ShadowContractError(
                "stored lineage differs from reconstructed bundle"
            )
        return {
            "BAR_SERIES": (bar_path,),
            "CASH_FLOW": tuple(cash_paths),
            "HOLDING": tuple(holding_paths),
            "INPUT": (input_path,),
            "LIFECYCLE": tuple(lifecycle_paths),
            "LINEAGE": (lineage_path,),
            "LIQUIDITY": (liquidity_path,),
            "PAIRED_RECORD": (paired_path,),
            "POLICY": (policy_path,),
        }

    def reconstruct_base_portfolio_lineage(
        self,
        manifest: ShadowProtocolManifest,
        *,
        pair_input: FixedNotionalPairInput,
    ) -> PortfolioLineageBundle:
        """Rebuild RS-P2-014 lineage from persisted substrate, never embedding it.

        Candidate and portfolio artifacts are reloaded through their own
        strict stores.  Only the snapshot and observation are taken from the
        exact persisted PairInput because those are the PairInput boundary
        artifacts named by RS-P2-015.
        """

        manifest = _revalidate(ShadowProtocolManifest, manifest)
        pair_input = _revalidate(FixedNotionalPairInput, pair_input)
        manifest_hash = _required_hash(manifest)
        candidate_store = CandidateSetStore(self.root)
        raw_capture = candidate_store.load_raw(
            pair_input.raw_capture.raw_capture_id,
            protocol_id=manifest.protocol_id,
            manifest_sha256=manifest_hash,
        )
        candidate_set = candidate_store.load(
            pair_input.candidate_set.candidate_set_id,
            protocol_id=manifest.protocol_id,
            manifest_sha256=manifest_hash,
        )
        portfolio_store = PortfolioArtifactStore(self.root)
        state = portfolio_store.verify_observation_state(
            manifest,
            pair_input.observation,
        )
        policy = portfolio_store.load_policy_for_manifest(manifest)
        source_record, _ = portfolio_store.load_source_record(
            manifest,
            state.portfolio_source_record_id,
        )
        persisted_hashes = (
            _required_hash(raw_capture),
            _required_hash(candidate_set),
            _required_hash(state),
            _required_hash(policy),
            _required_hash(source_record),
        )
        embedded_hashes = (
            pair_input.raw_capture_sha256,
            pair_input.candidate_set_sha256,
            pair_input.portfolio_state_sha256,
            pair_input.portfolio_state.portfolio_policy_sha256,
            pair_input.portfolio_state.portfolio_source_record_sha256,
        )
        if persisted_hashes != embedded_hashes:
            raise ShadowContractError(
                "PairInput differs from persisted RS-P2-014 substrate"
            )
        if _required_hash(pair_input.candidate) != pair_input.candidate_sha256:
            raise ShadowContractError(
                "PairInput candidate identity is unresolved"
            )
        return build_portfolio_lineage_bundle(
            manifest=manifest,
            frozen_snapshot=pair_input.snapshot,
            raw_capture=raw_capture,
            candidate_set=candidate_set,
            candidate=pair_input.candidate,
            observation=pair_input.observation,
            policy=policy,
            source_record=source_record,
            state=state,
        )

    def load_by_reference(
        self,
        manifest: ShadowProtocolManifest,
        *,
        kind: ArtifactKind,
        artifact_id: str,
        trading_calendar: TradingCalendar | None = None,
        portfolio_policy: FrozenPortfolioPolicy | None = None,
        pair_input_id: str | None = None,
    ) -> tuple[FixedNotionalArtifact, FixedNotionalReference]:
        """Load one exact reference, raw artifact, and semantic lineage."""

        manifest = _revalidate(ShadowProtocolManifest, manifest)
        manifest_hash = _required_hash(manifest)
        reference_path = self._reference_path(
            manifest.protocol_id,
            manifest_hash,
            kind,
            artifact_id,
        )
        reference_model: type[BaseModel]
        if kind == "LINEAGE":
            reference_model = FixedNotionalLineageReference
        else:
            reference_model = FixedNotionalGraphReference
        reference = _load_canonical_model(
            reference_model,
            self._read_exact(reference_path, "fixed-notional reference"),
            "fixed-notional reference",
        )
        expected_reference_identity = (
            manifest.protocol_id,
            manifest.component_id,
            manifest_hash,
            kind,
            artifact_id,
            _VERSION_BY_KIND[kind],
        )
        actual_reference_identity = (
            reference.protocol_id,
            reference.component_id,
            reference.manifest_sha256,
            reference.artifact_kind,
            reference.artifact_id,
            reference.artifact_contract_version,
        )
        if actual_reference_identity != expected_reference_identity:
            raise ShadowContractError(
                "fixed-notional reference identity mismatch"
            )
        artifact_path = (
            self.root / reference.artifact_relative_path
        ).resolve()
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
            or reference.artifact_relative_path
            != self._relative(expected_path)
        ):
            raise ShadowContractError(
                "fixed-notional reference escaped its exact namespace"
            )
        raw = self._read_exact(artifact_path, "fixed-notional artifact")
        if (
            len(raw) != reference.artifact_raw_byte_length
            or _sha256(raw) != reference.artifact_raw_file_sha256
        ):
            raise ShadowContractError(
                "fixed-notional raw-file identity mismatch"
            )
        artifact = _load_kind(kind, raw)
        if _required_hash(artifact) != reference.artifact_canonical_sha256:
            raise ShadowContractError(
                "fixed-notional canonical identity mismatch"
            )
        if _artifact_id(kind, artifact) != artifact_id:
            raise ShadowContractError(
                "fixed-notional artifact ID/reference mismatch"
            )
        self._verify_manifest_lineage(manifest, kind, artifact)
        expected_named_predecessors = _named_predecessors(artifact)
        if kind == "LINEAGE":
            if not isinstance(reference, FixedNotionalLineageReference):
                raise ShadowContractError(
                    "fixed-notional lineage reference type mismatch"
                )
            if reference.predecessors != expected_named_predecessors:
                raise ShadowContractError(
                    "lineage named predecessor reference drift"
                )
        else:
            if not isinstance(reference, FixedNotionalGraphReference):
                raise ShadowContractError(
                    "fixed-notional graph reference type mismatch"
                )
            if reference.predecessors != expected_named_predecessors:
                raise ShadowContractError(
                    "fixed-notional predecessor reference drift"
                )
        if kind == "POLICY":
            if portfolio_policy is None:
                raise ShadowContractError(
                    "portfolio policy is required to load fixed-notional policy"
                )
            verify_fixed_notional_policy_binding(
                manifest,
                portfolio_policy,
                artifact,  # type: ignore[arg-type]
            )
        if kind == "CASH_FLOW":
            if trading_calendar is None:
                raise ShadowContractError(
                    "trusted trading calendar is required for cash-flow load"
                )
            _verify_cash_flow_calendar(
                artifact,  # type: ignore[arg-type]
                trading_calendar,
                manifest,
            )
        if kind == "LIFECYCLE":
            lifecycle = artifact
            if not isinstance(lifecycle, FixedNotionalLifecycle):
                raise ShadowContractError("lifecycle artifact type mismatch")
            if lifecycle.cash_flow_records:
                if trading_calendar is None:
                    raise ShadowContractError(
                        "trusted trading calendar is required for lifecycle load"
                    )
                for cash_flow in lifecycle.cash_flow_records:
                    _verify_cash_flow_calendar(
                        cash_flow,
                        trading_calendar,
                        manifest,
                    )
        if kind == "PAIRED_RECORD":
            if pair_input_id is None:
                raise ShadowContractError(
                    "paired-record load requires exact PairInput predecessor"
                )
            if not isinstance(
                reference,
                FixedNotionalGraphReference,
            ) or not isinstance(artifact, PairedFixedNotionalRecord):
                raise ShadowContractError(
                    "paired-record reference type mismatch"
                )
            pair_input, _ = self.load_by_reference(
                manifest,
                kind="INPUT",
                artifact_id=pair_input_id,
            )
            if not isinstance(pair_input, FixedNotionalPairInput):
                raise ShadowContractError(
                    "paired-record predecessor returned wrong type"
                )
            if (
                artifact.pair_input_id,
                artifact.pair_input_sha256,
            ) != (
                pair_input.pair_input_id,
                _required_hash(pair_input),
            ):
                raise ShadowContractError(
                    "paired-record predecessor reference drift"
                )
            _verify_pair_binding(manifest, pair_input, artifact)
        return artifact, reference

    def load_pair_bundle(
        self,
        manifest: ShadowProtocolManifest,
        *,
        pair_input_id: str,
        paired_record_id: str,
    ) -> tuple[FixedNotionalPairInput, PairedFixedNotionalRecord]:
        """Reload a complete pair and every ordered nested dependency."""

        pair_input, _ = self.load_by_reference(
            manifest,
            kind="INPUT",
            artifact_id=pair_input_id,
        )
        paired, _ = self.load_by_reference(
            manifest,
            kind="PAIRED_RECORD",
            artifact_id=paired_record_id,
            pair_input_id=pair_input_id,
        )
        if not isinstance(pair_input, FixedNotionalPairInput) or not isinstance(
            paired,
            PairedFixedNotionalRecord,
        ):
            raise ShadowContractError("fixed-notional bundle type mismatch")
        _verify_pair_binding(manifest, pair_input, paired)
        stored_policy, _ = self.load_by_reference(
            manifest,
            kind="POLICY",
            artifact_id=pair_input.policy.policy_id,
            portfolio_policy=pair_input.portfolio_state.portfolio_policy,
        )
        stored_liquidity, _ = self.load_by_reference(
            manifest,
            kind="LIQUIDITY",
            artifact_id=pair_input.liquidity.record_id,
        )
        stored_bars, _ = self.load_by_reference(
            manifest,
            kind="BAR_SERIES",
            artifact_id=_bar_series_id(pair_input.bar_series),
        )
        if (
            _required_hash(stored_policy),
            _required_hash(stored_liquidity),
            _required_hash(stored_bars),
        ) != (
            pair_input.policy_sha256,
            pair_input.liquidity_sha256,
            pair_input.bar_series_sha256,
        ):
            raise ShadowContractError(
                "stored fixed-notional dependencies differ from pair input"
            )
        for lifecycle in (
            paired.control,
            *paired.control_secondary,
            paired.challenger,
            *paired.challenger_secondary,
        ):
            loaded_lifecycle, _ = self.load_by_reference(
                manifest,
                kind="LIFECYCLE",
                artifact_id=lifecycle.lifecycle_id,
                trading_calendar=paired.trading_calendar,
            )
            if _required_hash(loaded_lifecycle) != _required_hash(lifecycle):
                raise ShadowContractError(
                    "stored lifecycle differs from paired-record lineage"
                )
            for holding in lifecycle.holding_records:
                loaded_holding, _ = self.load_by_reference(
                    manifest,
                    kind="HOLDING",
                    artifact_id=holding.holding_event_id,
                )
                if _required_hash(loaded_holding) != _required_hash(holding):
                    raise ShadowContractError(
                        "stored holding differs from lifecycle lineage"
                    )
            for cash_flow in lifecycle.cash_flow_records:
                loaded_cash, _ = self.load_by_reference(
                    manifest,
                    kind="CASH_FLOW",
                    artifact_id=cash_flow.cash_flow_id,
                    trading_calendar=paired.trading_calendar,
                )
                if _required_hash(loaded_cash) != _required_hash(cash_flow):
                    raise ShadowContractError(
                        "stored cash flow differs from lifecycle lineage"
                    )
        return pair_input, paired

    def load_verified_lineage(
        self,
        manifest: ShadowProtocolManifest,
        *,
        lineage_id: str,
        pair_input_id: str,
        paired_record_id: str,
    ) -> FixedNotionalLineageBundle:
        lineage, reference = self.load_by_reference(
            manifest,
            kind="LINEAGE",
            artifact_id=lineage_id,
        )
        if not isinstance(lineage, FixedNotionalLineageBundle):
            raise ShadowContractError("lineage reference returned wrong type")
        pair_input, paired = self.load_pair_bundle(
            manifest,
            pair_input_id=pair_input_id,
            paired_record_id=paired_record_id,
        )
        base = self.reconstruct_base_portfolio_lineage(
            manifest,
            pair_input=pair_input,
        )
        if not isinstance(reference, FixedNotionalLineageReference):
            raise ShadowContractError("lineage reference returned wrong type")
        expected_predecessors = (
            _required_hash(base),
            pair_input.pair_input_id,
            _required_hash(pair_input),
            paired.paired_record_id,
            _required_hash(paired),
        )
        reference_predecessors = (
            reference.base_portfolio_lineage_sha256,
            reference.pair_input_id,
            reference.pair_input_sha256,
            reference.paired_record_id,
            reference.paired_record_sha256,
        )
        lineage_predecessors = (
            lineage.base_portfolio_lineage_sha256,
            lineage.pair_input_id,
            lineage.pair_input_sha256,
            lineage.paired_record_id,
            lineage.paired_record_sha256,
        )
        if (
            reference_predecessors != expected_predecessors
            or lineage_predecessors != expected_predecessors
        ):
            raise ShadowContractError(
                "lineage predecessor reference drift"
            )
        return verify_fixed_notional_lineage_bundle(
            lineage,
            manifest=manifest,
            base_portfolio_lineage=base,
            pair_input=pair_input,
            paired_record=paired,
        )

    def _persist(
        self,
        manifest: ShadowProtocolManifest,
        *,
        kind: ArtifactKind,
        artifact: FixedNotionalArtifact,
        artifact_id: str,
        raw_file_bytes: bytes,
    ) -> Path:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        canonical_hash = _required_hash(artifact)
        raw_hash = _sha256(raw_file_bytes)
        manifest_hash = _required_hash(manifest)
        path = self._record_path(
            manifest.protocol_id,
            manifest_hash,
            kind,
            canonical_hash,
            raw_hash,
        )
        self._require_within_root(path.resolve())
        self._exclusive_create(path, raw_file_bytes)
        reference: FixedNotionalReference
        if kind == "LINEAGE":
            if not isinstance(artifact, FixedNotionalLineageBundle):
                raise ShadowContractError("lineage artifact type mismatch")
            reference = FixedNotionalLineageReference(
                protocol_id=manifest.protocol_id,
                component_id=manifest.component_id,
                manifest_sha256=manifest_hash,
                artifact_id=artifact_id,
                artifact_canonical_sha256=canonical_hash,
                artifact_raw_file_sha256=raw_hash,
                artifact_raw_byte_length=len(raw_file_bytes),
                artifact_relative_path=self._relative(path),
                base_portfolio_lineage_sha256=(
                    artifact.base_portfolio_lineage_sha256
                ),
                pair_input_id=artifact.pair_input_id,
                pair_input_sha256=artifact.pair_input_sha256,
                paired_record_id=artifact.paired_record_id,
                paired_record_sha256=artifact.paired_record_sha256,
                predecessors=_named_predecessors(artifact),
            )
        else:
            reference = FixedNotionalGraphReference(
                protocol_id=manifest.protocol_id,
                component_id=manifest.component_id,
                manifest_sha256=manifest_hash,
                artifact_kind=kind,  # type: ignore[arg-type]
                artifact_id=artifact_id,
                artifact_contract_version=_VERSION_BY_KIND[kind],
                artifact_canonical_sha256=canonical_hash,
                artifact_raw_file_sha256=raw_hash,
                artifact_raw_byte_length=len(raw_file_bytes),
                artifact_relative_path=self._relative(path),
                predecessors=_named_predecessors(artifact),
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
        artifact: FixedNotionalArtifact,
    ) -> None:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        manifest_hash = _required_hash(manifest)
        if kind == "POLICY":
            return
        if kind == "BAR_SERIES":
            series = artifact
            if not isinstance(series, FixedNotionalBarSeries):
                raise ShadowContractError("bar-series artifact type mismatch")
            source_matches = tuple(
                source
                for source in manifest.sources
                if source.source_id == series.source_id
                and _required_hash(source)
                == series.source_definition_sha256
            )
            if (
                len(source_matches) != 1
                or series.corporate_action_policy.policy_sha256
                != manifest.corporate_action_policy_sha256
            ):
                raise ShadowContractError(
                    "bar series is not bound to manifest sources/policy"
                )
            return
        if kind == "INPUT":
            pair_input = artifact
            if not isinstance(pair_input, FixedNotionalPairInput):
                raise ShadowContractError("pair-input artifact type mismatch")
            if (
                pair_input.manifest_sha256 != manifest_hash
                or _required_hash(pair_input.manifest) != manifest_hash
                or pair_input.manifest.protocol_id != manifest.protocol_id
                or pair_input.manifest.component_id != manifest.component_id
            ):
                raise ShadowContractError(
                    "pair input embeds another manifest"
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
            / "fixed_notional"
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
            / "fixed_notional"
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
                "fixed-notional path escaped store root"
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
                    f"immutable fixed-notional artifact collision: {path}"
                ) from None
        return path

    @staticmethod
    def _read_exact(path: Path, label: str) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ShadowContractError(
                f"{label} is unavailable: {path}"
            ) from exc


def _verify_pair_binding(
    manifest: ShadowProtocolManifest,
    pair_input: FixedNotionalPairInput,
    paired: PairedFixedNotionalRecord,
) -> None:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    pair_input = _revalidate(FixedNotionalPairInput, pair_input)
    paired = _revalidate(PairedFixedNotionalRecord, paired)
    actual = (
        paired.pair_input_id,
        paired.pair_input_sha256,
        paired.protocol_id,
        paired.component_id,
        paired.manifest_sha256,
        paired.observation_id,
        paired.observation_sha256,
        paired.raw_event_id,
        paired.ticker,
        paired.portfolio_state_sha256,
        paired.fixed_notional_policy_sha256,
        paired.liquidity_record_sha256,
        paired.opportunity_set_sha256,
        paired.candidate_set_sha256,
        paired.snapshot_sha256,
        paired.label_definition_sha256,
        paired.cost_assumptions_sha256,
        paired.trading_calendar_sha256,
        paired.control_sizing_plan,
        paired.challenger_sizing_plan,
        paired.shared_exclusion_reason,
    )
    expected = (
        pair_input.pair_input_id,
        _required_hash(pair_input),
        manifest.protocol_id,
        manifest.component_id,
        _required_hash(manifest),
        pair_input.observation.observation_id,
        pair_input.observation_sha256,
        pair_input.observation.raw_event_id,
        pair_input.observation.ticker,
        pair_input.portfolio_state_sha256,
        pair_input.policy_sha256,
        pair_input.liquidity_sha256,
        pair_input.observation.opportunity_set_sha256,
        pair_input.candidate_set_sha256,
        pair_input.snapshot_sha256,
        pair_input.label_definition_sha256,
        pair_input.cost_assumptions_sha256,
        pair_input.trading_calendar_sha256,
        pair_input.control_sizing_plan,
        pair_input.challenger_sizing_plan,
        pair_input.shared_exclusion_reason,
    )
    if actual != expected:
        raise ShadowContractError(
            "paired record differs from exact pair-input lineage"
        )
    verify_paired_fixed_notional_record(pair_input, paired)


def _verify_cash_flow_calendar(
    cash_flow: FixedNotionalCashFlowRecord,
    trading_calendar: TradingCalendar,
    manifest: ShadowProtocolManifest,
) -> None:
    cash_flow = _revalidate(FixedNotionalCashFlowRecord, cash_flow)
    calendar = _revalidate(TradingCalendar, trading_calendar)
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    if (
        calendar.calendar_sha256 != manifest.trading_calendar_sha256
        or cash_flow.trade_session not in calendar.sessions
    ):
        raise ShadowContractError(
            "cash-flow calendar differs from manifest/session"
        )
    if cash_flow.event_type == "DIVIDEND_CREDIT":
        expected = cash_flow.trade_session
    else:
        index = calendar.sessions.index(cash_flow.trade_session)
        target = index + 2
        if target >= len(calendar.sessions):
            raise ShadowContractError(
                "cash-flow T+2 session is absent from calendar"
            )
        expected = calendar.sessions[target]
    if cash_flow.settlement_session != expected:
        raise ShadowContractError(
            "cash-flow settlement is not exact frozen-calendar T+2"
        )


def _artifact_id(
    kind: ArtifactKind,
    artifact: FixedNotionalArtifact,
) -> str:
    attribute = {
        "BAR_SERIES": None,
        "CASH_FLOW": "cash_flow_id",
        "HOLDING": "holding_event_id",
        "INPUT": "pair_input_id",
        "LIFECYCLE": "lifecycle_id",
        "LINEAGE": "lineage_id",
        "LIQUIDITY": "record_id",
        "PAIRED_RECORD": "paired_record_id",
        "POLICY": "policy_id",
    }[kind]
    if kind == "BAR_SERIES":
        if not isinstance(artifact, FixedNotionalBarSeries):
            raise ShadowContractError("bar-series artifact type mismatch")
        return _bar_series_id(artifact)
    value = getattr(artifact, str(attribute), None)
    if not isinstance(value, str) or not value:
        raise ShadowContractError(f"{kind} artifact ID is unavailable")
    return value


def _named_predecessors(
    artifact: FixedNotionalArtifact,
) -> tuple[FixedNotionalNamedPredecessor, ...]:
    """Extract every named causal hash edge in deterministic order.

    The bar ``source_sha256`` transitively binds the corporate-action policy,
    but the policy and event-set hashes are also named directly so a reference
    audit never has to infer that transitive edge.
    """

    payload = artifact.model_dump(mode="python")
    named: list[FixedNotionalNamedPredecessor] = []
    for field_name, value in payload.items():
        if field_name == "manifest_sha256":
            continue
        if field_name.endswith("_sha256") and isinstance(value, str):
            named.append(
                FixedNotionalNamedPredecessor(
                    name=field_name,
                    sha256=value,
                )
            )
            continue
        if field_name.endswith("_sha256s") and isinstance(
            value,
            (list, tuple),
        ):
            named.extend(
                FixedNotionalNamedPredecessor(
                    name=f"{field_name}[{index}]",
                    sha256=digest,
                )
                for index, digest in enumerate(value)
            )
    if isinstance(artifact, FixedNotionalBarSeries):
        named.extend(
            (
                FixedNotionalNamedPredecessor(
                    name="corporate_action_events_sha256",
                    sha256=artifact.corporate_action_policy.events_sha256,
                ),
                FixedNotionalNamedPredecessor(
                    name="corporate_action_policy_sha256",
                    sha256=artifact.corporate_action_policy.policy_sha256,
                ),
            )
        )
    return tuple(sorted(named, key=lambda item: item.name))


def _bar_series_id(artifact: FixedNotionalBarSeries) -> str:
    return f"FNBARS-{artifact.source_sha256[:32]}"


def _load_kind(
    kind: ArtifactKind,
    raw_file_bytes: bytes,
) -> FixedNotionalArtifact:
    loader = {
        "BAR_SERIES": load_fixed_notional_bar_series_v1,
        "CASH_FLOW": load_fixed_notional_cash_flow_v1,
        "HOLDING": load_fixed_notional_holding_v1,
        "INPUT": load_fixed_notional_pair_input_v1,
        "LIFECYCLE": load_fixed_notional_lifecycle_v1,
        "LINEAGE": load_fixed_notional_lineage_v1,
        "LIQUIDITY": load_fixed_notional_liquidity_v1,
        "PAIRED_RECORD": load_paired_fixed_notional_record_v1,
        "POLICY": load_fixed_notional_policy_v1,
    }[kind]
    return loader(raw_file_bytes)


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
        raise ShadowContractError(
            f"{label} failed strict validation"
        ) from exc
    if canonical_json_bytes(trusted) != raw_file_bytes:
        raise ShadowContractError(
            f"{label} raw bytes are not canonical JSON"
        )
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
        raise ShadowContractError(
            f"{label} failed strict validation"
        ) from exc
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
        raise ShadowContractError(
            f"{label} is not valid UTF-8 JSON"
        ) from exc
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


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _lineage_id(
    *,
    protocol_id: str,
    manifest_sha256: str,
    base_lineage_sha256: str,
    pair_input_sha256: str,
    paired_record_sha256: str,
) -> str:
    payload = json.dumps(
        {
            "base_lineage_sha256": base_lineage_sha256,
            "manifest_sha256": manifest_sha256,
            "pair_input_sha256": pair_input_sha256,
            "paired_record_sha256": paired_record_sha256,
            "protocol_id": protocol_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"FNLINEAGE-{_sha256(payload)[:32]}"


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


__all__ = [
    "ArtifactKind",
    "FixedNotionalArtifact",
    "FixedNotionalArtifactStore",
    "FixedNotionalGraphReference",
    "FixedNotionalLineageBundle",
    "FixedNotionalLineageReference",
    "FixedNotionalNamedPredecessor",
    "FixedNotionalReference",
    "FIXED_NOTIONAL_REFERENCE_VERSION",
    "FIXED_NOTIONAL_LINEAGE_REFERENCE_VERSION",
    "FIXED_NOTIONAL_LINEAGE_VERSION",
    "build_fixed_notional_lineage_bundle",
    "load_fixed_notional_bar_series_v1",
    "load_fixed_notional_cash_flow_v1",
    "load_fixed_notional_holding_v1",
    "load_fixed_notional_lifecycle_v1",
    "load_fixed_notional_lineage_v1",
    "load_fixed_notional_liquidity_v1",
    "load_fixed_notional_pair_input_v1",
    "load_fixed_notional_policy_v1",
    "load_paired_fixed_notional_record_v1",
    "verify_fixed_notional_lineage_bundle",
]
