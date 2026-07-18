"""Acceptance tests for the evaluation-only RS-P2-014 portfolio substrate."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from core.shadow_protocol.calendar import (
    TradingCalendar,
    canonical_trading_calendar_sha256,
)
from core.shadow_protocol import (
    CandidateDisposition,
    CandidateEvent,
    CandidateSetManifest,
    CandidateSetStore,
    CandidateSetView,
    ClusterRuleDefinition,
    ContentHash,
    CostAssumptions,
    EstimableMoney,
    EstimableRatio,
    FrozenPortfolioPolicy,
    FrozenPortfolioSourceCommitment,
    FrozenPortfolioSourcePayload,
    FrozenPortfolioSourcePosition,
    FrozenSnapshot,
    GateMeasurement,
    GoNoGoRules,
    IndependentClusterMetadata,
    LabelDefinition,
    PortfolioArtifactStore,
    PortfolioState,
    PortfolioStateReference,
    PortfolioStateSourceRecord,
    PortfolioStateSourceReference,
    ProtocolGovernanceStore,
    RecordedTradeGeometry,
    ShadowContractError,
    ShadowDecision,
    ShadowProtocolManifest,
    SourceDefinition,
    UniverseDefinition,
    aggregate_bps_cost_idr,
    build_frozen_control_portfolio_state,
    build_portfolio_lineage_bundle,
    canonical_decision_payload_sha256,
    canonical_frozen_snapshot_sha256,
    canonical_json_bytes,
    canonical_opportunity_set_sha256,
    canonical_payload_json,
    canonical_payload_sha256,
    canonical_raw_candidate_set_sha256,
    canonical_raw_event_id,
    canonical_rules_sha256,
    canonical_sha256,
    canonical_source_record_sha256,
    canonical_view_sha256,
    estimable_money,
    estimable_ratio,
    load_portfolio_state_v1,
    not_estimable_money,
    manifest_portfolio_profile,
    portfolio_manifest_parameters,
    produce_paired_observation,
    quantize_ratio,
    verify_portfolio_manifest_binding,
    verify_portfolio_lineage_bundle,
    verify_portfolio_state_binding,
)
from tests.test_shadow_protocol_governance import _approval as _governance_approval


IDX = ZoneInfo("Asia/Jakarta")
SIGNAL = datetime(2026, 7, 17, 16, 30, tzinfo=IDX)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
METHODOLOGY_SHA256 = hashlib.sha256(b"RS-P2-014 methodology").hexdigest()


def _source(source_id: str, marker: str) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        source_type="FILE",
        locator=f"artifact://{source_id.lower()}",
        as_of_field="source_as_of",
        expiry_rule="strictly after signal; otherwise ABSTAIN",
        missing_policy="ABSTAIN",
        contract_version="source-v1",
        source_sha256=marker * 64,
    )


def _sources() -> tuple[SourceDefinition, ...]:
    return (
        _source("CANDIDATES", "1"),
        _source("SNAPSHOT", "2"),
        _source("PORTFOLIO", "3"),
        _source("LIQUIDITY", "4"),
        _source("MARK", "5"),
    )


def _source_hash(source_id: str) -> str:
    source = next(item for item in _sources() if item.source_id == source_id)
    digest = canonical_sha256(source)
    assert digest is not None
    return digest


def _costs() -> CostAssumptions:
    return CostAssumptions(
        buy_commission_bps=15.0,
        sell_commission_bps=25.0,
        sell_tax_bps=10.0,
        slippage_bps=5.0,
        bid_ask_bps=5.0,
        lot_size=100,
        liquidity_execution_rule="POLICY_BOUND_CAPACITY;MISSING=ABSTAIN",
        price_rounding_rule="SOURCE_SUPPLIED_INTEGER_IDR",
        cost_model_version="integer-idr-cost-v1",
    )


def _policy(**updates: object) -> FrozenPortfolioPolicy:
    values: dict[str, object] = {
        "policy_id": "RS-P2-014-POLICY-TEST",
        "starting_capital_idr": 100_000_000,
        "fixed_notional_idr": 13_000_000,
        "fixed_notional_fraction": 0.13,
        "target_deployment_fraction": 0.65,
        "effective_fixed_notional_max_positions": 3,
        "effective_fixed_notional_max_deployment_fraction": 0.39,
        "lot_size_shares": 100,
        "settlement_lag_sessions": 2,
        "minimum_cash_reserve_fraction": 0.05,
        "liquidity_source_id": "LIQUIDITY",
        "liquidity_source_definition_sha256": _source_hash("LIQUIDITY"),
        "liquidity_expiry_rule": "strictly after signal; otherwise ABSTAIN",
        "minimum_adt_idr": 10_000_000_000,
        "max_participation_fraction": 0.0013,
        "participation_derivation_numerator_idr": 13_000_000,
        "participation_derivation_denominator_idr": 10_000_000_000,
        "base_max_concurrent_positions": 5,
        "bull_max_positions": 3,
        "sideways_max_positions": 2,
        "bear_stress_max_positions": 1,
        "unknown_max_positions": 0,
        "total_loss_budget_fraction": 0.02,
        "max_portfolio_heat_fraction": 0.013,
        "max_gross_exposure_fraction": 0.95,
        "sector_max_names": 2,
        "cluster_max_names": 2,
        "daily_loss_stop_fraction": 0.03,
        "portfolio_source_id": "PORTFOLIO",
        "portfolio_source_definition_sha256": _source_hash("PORTFOLIO"),
        "mark_price_source_id": "MARK",
        "mark_price_source_definition_sha256": _source_hash("MARK"),
        "price_rounding_rule": "SOURCE_SUPPLIED_INTEGER_IDR",
        "corporate_action_policy_sha256": HASH_C,
        "trading_calendar_sha256": HASH_D,
        "cost_assumptions_sha256": canonical_sha256(_costs()),
        "methodology_document_sha256": METHODOLOGY_SHA256,
    }
    values.update(updates)
    return FrozenPortfolioPolicy(**values)


def _cluster_rules() -> ClusterRuleDefinition:
    return ClusterRuleDefinition(
        rule_version="cluster-v1",
        issuer_group_rule="same issuer",
        economic_group_rule="same economic group",
        correlation_cluster_rule="frozen point-in-time cluster",
        systemic_date_block_rule="same signal date",
        duplicate_setup_rule="same ticker and setup",
        representative_rule="first event by signal time",
        effective_n_rule="one representative per cluster",
    )


def _manifest(
    policy: FrozenPortfolioPolicy | None = None,
    *,
    control_policy_hash: str | None = None,
    challenger_policy_hash: str | None = None,
) -> ShadowProtocolManifest:
    policy = policy or _policy()
    policy_hash = canonical_sha256(policy)
    assert policy_hash is not None
    cluster_rules = _cluster_rules()
    go = ("predeclared GO",)
    cont = ("predeclared CONTINUE",)
    no_go = ("predeclared NO-GO",)
    return ShadowProtocolManifest(
        protocol_id="RS-C7-P2-014-TEST",
        component_id="C7",
        manifest_revision=1,
        created_at=datetime(2026, 7, 1, 12, 0, tzinfo=IDX),
        draft_frozen_at=datetime(2026, 7, 2, 12, 0, tzinfo=IDX),
        collection_start_not_before=datetime(2026, 7, 10, 12, 0, tzinfo=IDX),
        fixed_terminal_date=date(2026, 9, 30),
        owner="solo-owner",
        governance_mode="SOLO_SELF_REVIEW",
        independent_reviewer=None,
        rollback_owner="solo-owner",
        baseline_manifest_id="RS-CONTROL-20260717-01",
        baseline_manifest_sha256=HASH_A,
        methodology_document_path=(
            "docs/research/RS_P2_014_PORTFOLIO_STATE_DESIGN.md"
        ),
        methodology_document_sha256=METHODOLOGY_SHA256,
        control_content_hashes=(
            ContentHash(path="control.py", sha256=HASH_A, role="CONTROL"),
            ContentHash(
                path="config/portfolio-policy-v1.json",
                sha256=control_policy_hash or policy_hash,
                role="CONFIG",
            ),
        ),
        challenger_content_hashes=(
            ContentHash(path="challenger.py", sha256=HASH_B, role="CHALLENGER"),
            ContentHash(
                path="config/portfolio-policy-v1.json",
                sha256=challenger_policy_hash or policy_hash,
                role="CONFIG",
            ),
        ),
        universe=UniverseDefinition(
            universe_id="IDX-P2-014-TEST",
            quant_mode="MOMENTUM",
            selection_rule="complete raw set before pruning",
            candidate_source_sha256=_source_hash("CANDIDATES"),
            explicit_tickers=("BBCA", "BBRI"),
        ),
        trading_calendar_id="IDX-CALENDAR-P2-014",
        trading_calendar_sha256=policy.trading_calendar_sha256,
        corporate_action_policy_sha256=policy.corporate_action_policy_sha256,
        thresholds=portfolio_manifest_parameters(policy),
        features=(),
        sources=_sources(),
        labels=LabelDefinition(
            entry_validity_trading_days=3,
            activation_rule="FIRST_TRADING_SESSION_AFTER_SIGNAL",
            horizon_clock_rule="POST_FILL_SESSIONS_EXCLUDING_FILL_SESSION",
            fill_rule="BUY_LIMIT_OPEN_OR_INTRADAY_TOUCH_AT_ENTRY_HIGH",
            gap_rule="OBSERVED_OPEN_FOR_MARKETABLE_ENTRY_AND_GAP_EXITS",
            entry_gap_through_stop_rule="FILL_AND_STOP_AT_OBSERVED_OPEN",
            same_bar_ambiguity_rule="STOP_FIRST",
            corporate_action_rule="RAW_AS_TRADED_WITH_FROZEN_ACTIONS",
            rights_treatment_rule="NOT_ESTIMABLE_UNTIL_EXACT_CASH_LINEAGE",
            dividend_return_convention="PRICE_RETURN",
            dividend_entitlement_rule="POSITION_OPEN_BEFORE_EX_DATE",
            unfilled_rule="EXPIRE_AFTER_ENTRY_VALIDITY_TRADING_DAYS",
        ),
        costs=_costs(),
        cluster_rules=cluster_rules,
        cluster_rules_sha256=canonical_sha256(cluster_rules),
        go_no_go=GoNoGoRules(
            go=go,
            continue_rules=cont,
            no_go=no_go,
            rules_sha256=canonical_rules_sha256(go, cont, no_go),
        ),
        trial_registry_id="TRIAL-P2-014-TEST",
        production_feature_flag="SHADOW_P2_014_DISABLED",
        rollback_plan="remove evaluation-only artifacts; control unchanged",
    )


def _snapshot(ticker: str = "BBCA") -> FrozenSnapshot:
    payload_json = canonical_payload_json(
        {"Ticker": ticker, "close": 10_000, "as_of": SIGNAL.isoformat()}
    )
    payload_sha256 = canonical_payload_sha256(payload_json)
    source_as_of = SIGNAL - timedelta(hours=1)
    snapshot_as_of = SIGNAL - timedelta(minutes=30)
    expires_at = SIGNAL + timedelta(days=1)
    record_hash = canonical_source_record_sha256(
        source_id="SNAPSHOT",
        source_definition_sha256=_source_hash("SNAPSHOT"),
        source_as_of=source_as_of,
        source_expires_at=expires_at,
        source_row_number=None,
        payload_sha256=payload_sha256,
    )
    snapshot_hash = canonical_frozen_snapshot_sha256(
        snapshot_id=f"SNAP-{ticker}",
        ticker=ticker,
        as_of_date=SIGNAL.date(),
        snapshot_as_of=snapshot_as_of,
        source_id="SNAPSHOT",
        source_definition_sha256=_source_hash("SNAPSHOT"),
        source_record_sha256=record_hash,
        source_as_of=source_as_of,
        source_expires_at=expires_at,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
    )
    return FrozenSnapshot(
        snapshot_id=f"SNAP-{ticker}",
        ticker=ticker,
        as_of_date=SIGNAL.date(),
        snapshot_as_of=snapshot_as_of,
        snapshot_sha256=snapshot_hash,
        source_id="SNAPSHOT",
        source_definition_sha256=_source_hash("SNAPSHOT"),
        source_record_sha256=record_hash,
        source_as_of=source_as_of,
        source_expires_at=expires_at,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
    )


def _candidate(
    manifest: ShadowProtocolManifest,
    snapshot: FrozenSnapshot,
    *,
    ticker: str = "BBCA",
    row: int = 1,
    opportunity_sha256: str = HASH_A,
) -> CandidateEvent:
    payload_json = canonical_payload_json(
        {"Ticker": ticker, "price": 10_000, "row": row}
    )
    payload_sha256 = canonical_payload_sha256(payload_json)
    source_as_of = SIGNAL - timedelta(hours=1)
    expires_at = SIGNAL + timedelta(days=1)
    source_hash = canonical_source_record_sha256(
        source_id="CANDIDATES",
        source_definition_sha256=_source_hash("CANDIDATES"),
        source_as_of=source_as_of,
        source_expires_at=expires_at,
        source_row_number=row,
        payload_sha256=payload_sha256,
    )
    event_id = canonical_raw_event_id(
        opportunity_set_id="OPP-P2-014",
        ticker=ticker,
        signal_at=SIGNAL,
        snapshot_sha256=snapshot.snapshot_sha256,
        candidate_source_sha256=source_hash,
        source_row_number=row,
        raw_payload_sha256=payload_sha256,
    )
    return CandidateEvent(
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=canonical_sha256(manifest),
        opportunity_set_id="OPP-P2-014",
        opportunity_set_sha256=opportunity_sha256,
        raw_event_id=event_id,
        ticker=ticker,
        signal_at=SIGNAL,
        as_of_date=SIGNAL.date(),
        captured_at=SIGNAL + timedelta(minutes=1),
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=snapshot.snapshot_sha256,
        snapshot_as_of=snapshot.snapshot_as_of,
        snapshot_source_record_sha256=snapshot.source_record_sha256,
        candidate_source_id="CANDIDATES",
        candidate_source_definition_sha256=_source_hash("CANDIDATES"),
        candidate_source_sha256=source_hash,
        candidate_source_as_of=source_as_of,
        candidate_source_expires_at=expires_at,
        trading_calendar_id=manifest.trading_calendar_id,
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=manifest.corporate_action_policy_sha256,
        corporate_action_events_at_signal_sha256=HASH_B,
        source_row_number=row,
        raw_payload_json=payload_json,
        raw_payload_sha256=payload_sha256,
    )


def _view(
    side: str,
    event_ids: tuple[str, ...],
    *,
    pruned: frozenset[str] = frozenset(),
) -> CandidateSetView:
    dispositions = tuple(
        CandidateDisposition(
            raw_event_id=event_id,
            state="PRUNED" if event_id in pruned else "RETAINED",
            reason_codes=("STATIC_PRUNE",) if event_id in pruned else (),
        )
        for event_id in event_ids
    )
    return CandidateSetView(
        side=side,
        input_event_ids=event_ids,
        dispositions=dispositions,
        view_sha256=canonical_view_sha256(side, event_ids, dispositions),
    )


def _raw_and_set(
    manifest: ShadowProtocolManifest,
    snapshots: tuple[FrozenSnapshot, ...] = (_snapshot(),),
) -> tuple[object, CandidateSetManifest]:
    records = tuple(
        _candidate(
            manifest,
            snapshot,
            ticker=snapshot.ticker,
            row=index,
        )
        for index, snapshot in enumerate(snapshots, start=1)
    )
    opportunity_hash = canonical_opportunity_set_sha256(
        "OPP-P2-014",
        SIGNAL.date(),
        records,
        empty_reason=None,
        candidate_source_definition_sha256=_source_hash("CANDIDATES"),
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=manifest.corporate_action_policy_sha256,
    )
    records = tuple(
        CandidateEvent.model_validate(
            {
                **record.model_dump(mode="python"),
                "opportunity_set_sha256": opportunity_hash,
            }
        )
        for record in records
    )
    raw_hash = canonical_raw_candidate_set_sha256(records, empty_reason=None)
    from core.shadow_protocol import RawCandidateSetCapture

    raw = RawCandidateSetCapture(
        raw_capture_id="RAW-P2-014",
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=canonical_sha256(manifest),
        opportunity_set_id="OPP-P2-014",
        opportunity_set_sha256=opportunity_hash,
        signal_at=SIGNAL,
        as_of_date=SIGNAL.date(),
        captured_at=SIGNAL + timedelta(minutes=2),
        candidate_source_id="CANDIDATES",
        candidate_source_definition_sha256=_source_hash("CANDIDATES"),
        trading_calendar_id=manifest.trading_calendar_id,
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=manifest.corporate_action_policy_sha256,
        raw_candidate_count=len(records),
        raw_candidate_set_sha256=raw_hash,
        candidates=records,
    )
    event_ids = tuple(item.raw_event_id for item in records)
    candidate_set = CandidateSetManifest(
        candidate_set_id="SET-P2-014",
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=canonical_sha256(manifest),
        raw_capture_id=raw.raw_capture_id,
        raw_capture_sha256=canonical_sha256(raw),
        opportunity_set_id=raw.opportunity_set_id,
        opportunity_set_sha256=raw.opportunity_set_sha256,
        candidate_source_id=raw.candidate_source_id,
        candidate_source_definition_sha256=raw.candidate_source_definition_sha256,
        trading_calendar_id=raw.trading_calendar_id,
        trading_calendar_sha256=raw.trading_calendar_sha256,
        corporate_action_policy_sha256=raw.corporate_action_policy_sha256,
        as_of_date=raw.as_of_date,
        captured_at=SIGNAL + timedelta(minutes=3),
        raw_candidate_count=len(records),
        raw_candidate_set_sha256=raw.raw_candidate_set_sha256,
        control_view=_view("CONTROL", event_ids),
        challenger_view=_view("CHALLENGER", event_ids),
    )
    return raw, candidate_set


def _source_record(
    manifest: ShadowProtocolManifest,
    *,
    source_as_of: datetime = SIGNAL - timedelta(minutes=10),
    expires_at: datetime | None = SIGNAL + timedelta(days=1),
    captured_at: datetime = SIGNAL + timedelta(minutes=2, seconds=30),
    payload: FrozenPortfolioSourcePayload | None = None,
) -> PortfolioStateSourceRecord:
    payload = payload or FrozenPortfolioSourcePayload(
        settled_cash=estimable_money(100_000_000),
        unsettled_cash_receivable=estimable_money(0),
        reserved_cash=estimable_money(0),
        realized_pnl_today=estimable_money(0),
        control_30d_closed_trade_avg_pnl=estimable_ratio(0.0),
        positions_status="ESTIMABLE",
        positions=(),
        pending_commitments_status="ESTIMABLE",
        pending_commitments=(),
    )
    payload_json = canonical_json_bytes(payload).decode("utf-8")
    return PortfolioStateSourceRecord(
        source_record_id="PORTFOLIO-SOURCE-P2-014",
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_revision=manifest.manifest_revision,
        manifest_sha256=canonical_sha256(manifest),
        source_id="PORTFOLIO",
        source_definition_sha256=_source_hash("PORTFOLIO"),
        source_as_of=source_as_of,
        source_expires_at=expires_at,
        captured_at=captured_at,
        payload_json=payload_json,
        payload_sha256=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
    )


def _state_scenario(
    tmp_path: Path,
    *,
    snapshots: tuple[FrozenSnapshot, ...] = (_snapshot(),),
) -> tuple[
    ShadowProtocolManifest,
    FrozenPortfolioPolicy,
    object,
    CandidateSetManifest,
    PortfolioStateSourceRecord,
    PortfolioState,
    PortfolioArtifactStore,
]:
    policy = _policy()
    manifest = _manifest(policy)
    raw, candidate_set = _raw_and_set(manifest, snapshots)
    source = _source_record(manifest)
    state = build_frozen_control_portfolio_state(
        manifest=manifest,
        raw_capture=raw,
        candidate_set=candidate_set,
        policy=policy,
        source_record=source,
        state_as_of=SIGNAL - timedelta(minutes=5),
        captured_at=SIGNAL + timedelta(minutes=3, seconds=30),
    )
    store = PortfolioArtifactStore(tmp_path)
    candidate_store = CandidateSetStore(tmp_path)
    candidate_store.persist_raw(raw)
    candidate_store.persist(candidate_set)
    store.persist_policy(manifest, canonical_json_bytes(policy))
    store.persist_source_record(manifest, canonical_json_bytes(source))
    store.persist_state(manifest, canonical_json_bytes(state))
    return manifest, policy, raw, candidate_set, source, state, store


def _decision(
    role: str,
    *,
    actionable: bool = True,
    reason_codes: tuple[str, ...] = ("RR_PASS",),
) -> ShadowDecision:
    geometry = RecordedTradeGeometry(
        entry_low=9_900,
        entry_high=10_000,
        target_price=12_000,
        stop_loss=9_000,
        risk_reward_ratio=2.0,
        required_risk_reward=2.0,
    )
    gate = GateMeasurement(
        gate_id="rr_floor",
        observed=2.0,
        threshold=2.0,
        comparator=">=",
        passed=actionable,
        reason_code=reason_codes[0],
        source_id="CANDIDATES",
        source_definition_sha256=_source_hash("CANDIDATES"),
        source_as_of=SIGNAL - timedelta(hours=1),
        expires_at=SIGNAL + timedelta(days=1),
    )
    values = {
        "decision_role": role,
        "decision_state": "DEPLOYABLE" if actionable else "REJECT",
        "rating": "BUY" if actionable else None,
        "would_be_actionable": actionable,
        "would_allocate": False,
        "position_size_basis": "NONE",
        "reason_codes": reason_codes,
        "gate_measurements": (gate,),
        "geometry": geometry if actionable else None,
    }
    draft = ShadowDecision.model_construct(
        **values,
        decision_payload_sha256=HASH_A,
    )
    return ShadowDecision(
        **values,
        decision_payload_sha256=canonical_decision_payload_sha256(draft),
    )


def _cluster(manifest: ShadowProtocolManifest) -> IndependentClusterMetadata:
    return IndependentClusterMetadata(
        assignment_status="NOT_EVALUATED_FOR_INDEPENDENCE",
        cluster_rule_sha256=manifest.cluster_rules_sha256,
        raw_event_count=0,
    )


class _TestOnlyAuthorizationLoader:
    """Non-authoritative spy; production uses ProtocolGovernanceStore."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def verify_paired_evaluation_authorization(
        self,
        **kwargs: object,
    ) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return object()


def test_manifest_v2_binds_owner_values_without_v3() -> None:
    policy = _policy()
    manifest = _manifest(policy)

    trusted = verify_portfolio_manifest_binding(manifest, policy)

    assert trusted.starting_capital_idr == 100_000_000
    assert trusted.fixed_notional_idr == 13_000_000
    assert trusted.max_participation_fraction == 0.0013
    assert trusted.max_sector_exposure_fraction is None
    assert trusted.max_drawdown_stop_fraction is None


def test_manifest_binding_rejects_different_control_challenger_policy() -> None:
    policy = _policy()
    manifest = _manifest(policy, challenger_policy_hash=HASH_C)

    with pytest.raises(ShadowContractError, match="both sides"):
        verify_portfolio_manifest_binding(manifest, policy)


def test_portfolio_markers_without_binding_profile_fail_closed() -> None:
    manifest = _manifest()
    stripped = ShadowProtocolManifest.model_validate(
        {
            **manifest.model_dump(mode="python"),
            "thresholds": tuple(
                item
                for item in manifest.thresholds
                if item.name != "portfolio_binding_profile"
            ),
        }
    )

    with pytest.raises(ShadowContractError, match="markers require"):
        manifest_portfolio_profile(stripped)


def test_portfolio_marker_downgrade_with_numeric_profile_fails_closed() -> None:
    manifest = _manifest()
    removed_identity_names = {
        "portfolio_binding_profile",
        "portfolio_policy_contract_version",
        "phase2_capability_status",
    }
    stripped = ShadowProtocolManifest.model_validate(
        {
            **manifest.model_dump(mode="python"),
            "thresholds": tuple(
                item
                for item in manifest.thresholds
                if item.name not in removed_identity_names
            ),
            "control_content_hashes": tuple(
                ContentHash(
                    path=(
                        "config/renamed-policy.json"
                        if item.role == "CONFIG"
                        else item.path
                    ),
                    sha256=item.sha256,
                    role=item.role,
                )
                for item in manifest.control_content_hashes
            ),
            "challenger_content_hashes": tuple(
                ContentHash(
                    path=(
                        "config/renamed-policy.json"
                        if item.role == "CONFIG"
                        else item.path
                    ),
                    sha256=item.sha256,
                    role=item.role,
                )
                for item in manifest.challenger_content_hashes
            ),
        }
    )

    with pytest.raises(ShadowContractError, match="markers require"):
        manifest_portfolio_profile(stripped)


def test_manifest_binding_rejects_noncanonical_policy_config_path() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    renamed = ShadowProtocolManifest.model_validate(
        {
            **manifest.model_dump(mode="python"),
            "control_content_hashes": tuple(
                ContentHash(
                    path=(
                        "config/renamed-policy.json"
                        if item.role == "CONFIG"
                        else item.path
                    ),
                    sha256=item.sha256,
                    role=item.role,
                )
                for item in manifest.control_content_hashes
            ),
            "challenger_content_hashes": tuple(
                ContentHash(
                    path=(
                        "config/renamed-policy.json"
                        if item.role == "CONFIG"
                        else item.path
                    ),
                    sha256=item.sha256,
                    role=item.role,
                )
                for item in manifest.challenger_content_hashes
            ),
        }
    )

    with pytest.raises(ShadowContractError, match="reserved canonical path"):
        verify_portfolio_manifest_binding(renamed, policy)


def test_governance_profile_preflight_requires_persisted_exact_policy(
    tmp_path: Path,
) -> None:
    policy = _policy()
    manifest = _manifest(policy)
    store = ProtocolGovernanceStore(tmp_path)

    with pytest.raises(ShadowContractError, match="persisted shared"):
        store._verify_portfolio_binding_if_declared(manifest)

    store.persist_portfolio_policy(manifest, canonical_json_bytes(policy))
    store._verify_portfolio_binding_if_declared(manifest)


def test_append_approval_fails_until_full_phase2_capability_evidence(
    tmp_path: Path,
) -> None:
    sessions = tuple(
        day
        for offset in range(100)
        if (day := date(2026, 7, 1) + timedelta(days=offset)).weekday() < 5
    )
    calendar_id = "IDX-CALENDAR-P2-014"
    calendar = TradingCalendar(
        calendar_id=calendar_id,
        calendar_sha256=canonical_trading_calendar_sha256(
            calendar_id,
            sessions,
        ),
        sessions=sessions,
    )
    policy = _policy(trading_calendar_sha256=calendar.calendar_sha256)
    draft_manifest = _manifest(policy)
    manifest = ShadowProtocolManifest.model_validate(
        {
            **draft_manifest.model_dump(mode="python"),
            "collection_start_not_before": datetime(
                2026,
                7,
                22,
                9,
                0,
                tzinfo=IDX,
            ),
        }
    )
    methodology = b"RS-P2-014 methodology"
    manifest_raw = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    approval = _governance_approval(manifest, manifest_raw, calendar)
    approval_raw = json.dumps(
        approval.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    store = ProtocolGovernanceStore(tmp_path)
    store.persist_trading_calendar(calendar)
    store.persist_manifest(manifest_raw, methodology)
    store.persist_portfolio_policy(manifest, canonical_json_bytes(policy))
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ShadowContractError, match="capability evidence"):
        store.append_approval(approval_raw)

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("starting_capital_idr", 100_000_000.0),
        ("starting_capital_idr", True),
        ("starting_capital_idr", "100000000"),
        ("fixed_notional_idr", 13_000_000.0),
        ("minimum_adt_idr", 10_000_000_000.0),
    ],
)
def test_policy_rejects_non_integer_idr_money(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _policy(**{field: value})


@pytest.mark.parametrize("value", [0.0, True, "0"])
def test_estimable_money_requires_strict_integer(value: object) -> None:
    with pytest.raises(ValidationError):
        EstimableMoney(status="ESTIMABLE", value_idr=value)


@pytest.mark.parametrize("value", [True, 1, "0.1"])
def test_ratio_fields_reject_implicit_numeric_coercion(value: object) -> None:
    with pytest.raises(ValidationError, match="strict floats"):
        EstimableRatio(status="ESTIMABLE", value=value)


def test_missing_money_never_becomes_zero() -> None:
    missing = not_estimable_money("SOURCE_MISSING")
    zero = estimable_money(0)

    assert missing.value_idr is None
    assert zero.value_idr == 0
    assert missing != zero
    with pytest.raises(ValidationError):
        EstimableMoney(
            status="NOT_ESTIMABLE",
            value_idr=0,
            reason_codes=("SOURCE_MISSING",),
        )


def test_n1_participation_cap_is_derived_not_calibrated() -> None:
    policy = _policy()

    assert policy.max_participation_fraction == 0.0013
    assert policy.participation_evidence_class == "DERIVED_NOT_CALIBRATED"
    assert quantize_ratio(
        policy.participation_derivation_numerator_idr,
        policy.participation_derivation_denominator_idr,
    ) == 0.0013


def test_n1_rejects_percent_fraction_confusion() -> None:
    with pytest.raises(ValidationError, match="max_participation_fraction"):
        _policy(max_participation_fraction=0.13)


def test_n2_sizing_basis_is_distinct_from_effective_maximum() -> None:
    policy = _policy()

    assert policy.target_deployment_fraction == 0.65
    assert policy.target_deployment_semantics == "SIZING_BASIS"
    assert policy.effective_fixed_notional_max_deployment_fraction == 0.39
    assert quantize_ratio(
        policy.effective_fixed_notional_max_positions
        * policy.fixed_notional_idr,
        policy.starting_capital_idr,
    ) == 0.39


def test_n2_rejects_sixty_five_percent_as_effective_fixed_maximum() -> None:
    with pytest.raises(ValidationError, match="effective"):
        _policy(effective_fixed_notional_max_deployment_fraction=0.65)


def test_n3_cannot_activate_nav_drawdown_gate() -> None:
    with pytest.raises(ValidationError):
        _policy(max_drawdown_stop_fraction=0.15)


def test_closed_trade_average_cannot_populate_nav_drawdown(
    tmp_path: Path,
) -> None:
    *_, state, _ = _state_scenario(tmp_path)
    with pytest.raises(ValidationError, match="peak NAV mismatch"):
        PortfolioState.model_validate(
            {
                **state.model_dump(mode="python"),
                "peak_nav": estimable_money(100_000_000),
                "nav_drawdown": estimable_ratio(
                    state.control_30d_closed_trade_avg_pnl.value or 0.0
                ),
            }
        )


def test_cost_bps_aggregate_then_ceil_once_against_portfolio() -> None:
    assert aggregate_bps_cost_idr(10_001, (15.0, 5.0, 5.0)) == 26
    assert aggregate_bps_cost_idr(10_001, (25.0, 10.0, 5.0, 5.0)) == 46
    assert aggregate_bps_cost_idr(10_000, (15.0, 5.0, 5.0)) == 25
    assert aggregate_bps_cost_idr(10_001, (15.0, 5.0, 5.0)) != 28


def test_frozen_state_uses_exact_integer_accounting(tmp_path: Path) -> None:
    *_, state, _ = _state_scenario(tmp_path)

    assert state.settled_cash.value_idr == 100_000_000
    assert state.deployable_cash.value_idr == 95_000_000
    assert state.marked_positions_value.value_idr == 0
    assert state.nav.value_idr == 100_000_000
    assert state.gross_exposure.value_idr == 0
    assert state.open_risk.value_idr == 0
    assert state.portfolio_heat.value == 0.0
    assert state.peak_nav.status == "NOT_ESTIMABLE"
    assert state.nav_drawdown.status == "NOT_ESTIMABLE"


def test_negative_cash_or_nav_fails_closed_under_no_leverage_policy(
    tmp_path: Path,
) -> None:
    *_, state, _ = _state_scenario(tmp_path)

    with pytest.raises(ValidationError, match="cannot be negative"):
        PortfolioState.model_validate(
            {
                **state.model_dump(mode="python"),
                "settled_cash": estimable_money(-1),
                "deployable_cash": estimable_money(0),
                "nav": estimable_money(-1),
                "portfolio_status": "ACTIVE",
            }
        )


def test_state_rejects_one_idr_nav_drift(tmp_path: Path) -> None:
    *_, state, _ = _state_scenario(tmp_path)
    payload = state.model_dump(mode="python")
    payload["nav"] = estimable_money(100_000_001)

    with pytest.raises(ValidationError, match="NAV mismatch"):
        PortfolioState.model_validate(payload)


def test_state_binding_rejects_coherent_source_payload_divergence(
    tmp_path: Path,
) -> None:
    manifest, policy, raw, candidate_set, source, state, _ = _state_scenario(
        tmp_path
    )
    forged = PortfolioState.model_validate(
        {
            **state.model_dump(mode="python"),
            "settled_cash": estimable_money(99_000_000),
            "deployable_cash": estimable_money(94_000_000),
            "nav": estimable_money(99_000_000),
        }
    )

    with pytest.raises(ShadowContractError, match="exact source payload"):
        verify_portfolio_state_binding(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            policy=policy,
            source_record=source,
            state=forged,
        )


def test_store_retry_is_idempotent_and_reference_collision_fails(
    tmp_path: Path,
) -> None:
    manifest, _, _, _, _, state, store = _state_scenario(tmp_path)
    raw = canonical_json_bytes(state)
    first = store.persist_state(manifest, raw)
    second = store.persist_state(manifest, raw)

    assert first == second
    assert first.read_bytes() == raw
    reference = (
        tmp_path
        / "protocols"
        / manifest.protocol_id
        / str(canonical_sha256(manifest))
        / "portfolio_state_refs"
        / f"{state.portfolio_state_id}.json"
    )
    original = reference.read_bytes()
    reference.write_bytes(original + b" ")
    with pytest.raises(ShadowContractError):
        store.load_state_by_hash(manifest, str(canonical_sha256(state)))


def test_store_rejects_duplicate_keys_and_noncanonical_reformatting(
    tmp_path: Path,
) -> None:
    policy = _policy()
    manifest = _manifest(policy)
    store = PortfolioArtifactStore(tmp_path)
    canonical = canonical_json_bytes(policy)
    duplicate = canonical[:-1] + b',"policy_id":"DUPLICATE"}'
    pretty = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    with pytest.raises(ShadowContractError, match="duplicate JSON key"):
        store.persist_policy(manifest, duplicate)
    with pytest.raises(ShadowContractError, match="not canonical JSON"):
        store.persist_policy(manifest, pretty)


def test_store_detects_tampered_state_bytes(tmp_path: Path) -> None:
    manifest, _, _, _, _, state, store = _state_scenario(tmp_path)
    state_hash = str(canonical_sha256(state))
    state_path = (
        tmp_path
        / "protocols"
        / manifest.protocol_id
        / str(canonical_sha256(manifest))
        / "portfolio_states"
        / state_hash
        / f"{state_hash}.json"
    )
    original = state_path.read_bytes()
    state_path.write_bytes(original.replace(b"95000000", b"95000001", 1))

    with pytest.raises(ShadowContractError):
        store.load_state_by_hash(manifest, state_hash)


def test_store_detects_tampered_source_record(tmp_path: Path) -> None:
    manifest, _, _, _, source, state, store = _state_scenario(tmp_path)
    source_path = (
        tmp_path
        / "protocols"
        / manifest.protocol_id
        / str(canonical_sha256(manifest))
        / "portfolio_state_sources"
        / str(canonical_sha256(source))
        / f"{canonical_sha256(source)}.json"
    )
    source_path.write_bytes(
        source_path.read_bytes().replace(b"100000000", b"100000001", 1)
    )

    with pytest.raises(ShadowContractError):
        store.load_state_by_hash(manifest, str(canonical_sha256(state)))


def test_source_reference_rejects_canonical_semantic_tamper(
    tmp_path: Path,
) -> None:
    manifest, _, _, _, source, _, store = _state_scenario(tmp_path)
    reference_path = (
        tmp_path
        / "protocols"
        / manifest.protocol_id
        / str(canonical_sha256(manifest))
        / "portfolio_state_source_refs"
        / f"{source.source_record_id}.json"
    )
    reference = PortfolioStateSourceReference.model_validate_json(
        reference_path.read_bytes()
    )
    forged = PortfolioStateSourceReference.model_validate(
        {
            **reference.model_dump(mode="python"),
            "protocol_id": "WRONG-PROTOCOL",
        }
    )
    reference_path.write_bytes(canonical_json_bytes(forged))

    with pytest.raises(ShadowContractError, match="reference identity"):
        store.load_source_record(manifest, source.source_record_id)


def test_state_reference_rejects_canonical_semantic_tamper(
    tmp_path: Path,
) -> None:
    manifest, _, _, _, _, state, store = _state_scenario(tmp_path)
    reference_path = (
        tmp_path
        / "protocols"
        / manifest.protocol_id
        / str(canonical_sha256(manifest))
        / "portfolio_state_refs"
        / f"{state.portfolio_state_id}.json"
    )
    reference = PortfolioStateReference.model_validate_json(
        reference_path.read_bytes()
    )
    forged = PortfolioStateReference.model_validate(
        {
            **reference.model_dump(mode="python"),
            "captured_at": reference.captured_at + timedelta(seconds=1),
        }
    )
    reference_path.write_bytes(canonical_json_bytes(forged))

    with pytest.raises(ShadowContractError, match="dependency reference"):
        store.load_state_by_hash(manifest, str(canonical_sha256(state)))


def test_state_binding_rejects_baseline_identity_drift(tmp_path: Path) -> None:
    manifest, policy, raw, candidate_set, source, state, _ = _state_scenario(
        tmp_path
    )
    tampered = PortfolioState.model_validate(
        {
            **state.model_dump(mode="python"),
            "baseline_manifest_sha256": HASH_B,
        }
    )

    with pytest.raises(ShadowContractError, match="lineage binding"):
        verify_portfolio_state_binding(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            policy=policy,
            source_record=source,
            state=tampered,
        )


def test_portfolio_loader_rejects_old_contract_explicitly(
    tmp_path: Path,
) -> None:
    *_, state, _ = _state_scenario(tmp_path)
    payload = {
        **state.model_dump(mode="json"),
        "contract_version": "shadow-portfolio-state-v0",
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    with pytest.raises(ShadowContractError, match="contract_version must be"):
        load_portfolio_state_v1(raw)


def test_state_rejects_future_or_expired_source(tmp_path: Path) -> None:
    policy = _policy()
    manifest = _manifest(policy)
    raw, candidate_set = _raw_and_set(manifest)
    future = _source_record(
        manifest,
        source_as_of=SIGNAL + timedelta(minutes=1),
    )
    expired = _source_record(
        manifest,
        expires_at=SIGNAL,
    )

    with pytest.raises(ShadowContractError, match="newer"):
        build_frozen_control_portfolio_state(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            policy=policy,
            source_record=future,
            state_as_of=SIGNAL - timedelta(minutes=5),
            captured_at=SIGNAL + timedelta(minutes=4),
        )
    with pytest.raises(ShadowContractError, match="expired"):
        build_frozen_control_portfolio_state(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            policy=policy,
            source_record=expired,
            state_as_of=SIGNAL - timedelta(minutes=5),
            captured_at=SIGNAL + timedelta(minutes=4),
        )


def test_state_rejects_source_captured_after_state(tmp_path: Path) -> None:
    policy = _policy()
    manifest = _manifest(policy)
    raw, candidate_set = _raw_and_set(manifest)
    source = _source_record(
        manifest,
        captured_at=SIGNAL + timedelta(minutes=5),
    )

    with pytest.raises(ShadowContractError, match="capture follows"):
        build_frozen_control_portfolio_state(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            policy=policy,
            source_record=source,
            state_as_of=SIGNAL - timedelta(minutes=5),
            captured_at=SIGNAL + timedelta(minutes=4),
        )


def test_state_binding_rejects_opportunity_and_signal_drift(
    tmp_path: Path,
) -> None:
    manifest, policy, raw, candidate_set, source, state, _ = _state_scenario(
        tmp_path
    )
    forged = PortfolioState.model_validate(
        {
            **state.model_dump(mode="python"),
            "opportunity_set_id": "WRONG-SET",
            "opportunity_set_sha256": HASH_D,
        }
    )

    with pytest.raises(ShadowContractError, match="lineage binding"):
        verify_portfolio_state_binding(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            policy=policy,
            source_record=source,
            state=forged,
        )


def test_entry_lot_position_and_commitment_invariants_are_exact() -> None:
    with pytest.raises(ValidationError, match="whole board lots"):
        FrozenPortfolioSourcePosition(
            position_id="POS-1",
            ticker="BBCA",
            opened_at=SIGNAL - timedelta(days=2),
            entry_quantity_lots=1,
            current_quantity_shares=99,
            quantity_origin="ENTRY_LOT_ROUNDED",
            total_cost_basis_idr=900_000,
            mark_price_idr=10_000,
            mark_as_of=SIGNAL - timedelta(minutes=10),
            source_record_sha256=HASH_A,
        )
    with pytest.raises(ValidationError):
        FrozenPortfolioSourcePosition(
            position_id="POS-FRACTIONAL",
            ticker="BBCA",
            opened_at=SIGNAL - timedelta(days=2),
            entry_quantity_lots=1,
            current_quantity_shares=100.5,
            quantity_origin="CORPORATE_ACTION_ADJUSTED",
            quantity_adjustment_event_sha256=HASH_B,
            total_cost_basis_idr=900_000,
            mark_price_idr=10_000,
            mark_as_of=SIGNAL - timedelta(minutes=10),
            source_record_sha256=HASH_A,
        )

    position = FrozenPortfolioSourcePosition(
        position_id="POS-1",
        ticker="BBCA",
        opened_at=SIGNAL - timedelta(days=2),
        entry_quantity_lots=1,
        current_quantity_shares=100,
        quantity_origin="ENTRY_LOT_ROUNDED",
        total_cost_basis_idr=900_000,
        mark_price_idr=10_000,
        mark_as_of=SIGNAL - timedelta(minutes=10),
        source_record_sha256=HASH_A,
    )
    duplicate_id = FrozenPortfolioSourcePosition(
        **{
            **position.model_dump(mode="python"),
            "ticker": "BBRI",
        }
    )
    with pytest.raises(ValidationError, match="globally unique"):
        FrozenPortfolioSourcePayload(
            settled_cash=estimable_money(98_000_000),
            unsettled_cash_receivable=estimable_money(0),
            reserved_cash=estimable_money(0),
            realized_pnl_today=estimable_money(0),
            control_30d_closed_trade_avg_pnl=estimable_ratio(0.0),
            positions_status="ESTIMABLE",
            positions=(position, duplicate_id),
            pending_commitments_status="ESTIMABLE",
            pending_commitments=(),
        )


def test_nonempty_position_and_commitment_use_exact_integer_idr() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    raw, candidate_set = _raw_and_set(manifest)
    position = FrozenPortfolioSourcePosition(
        position_id="POS-BBCA",
        ticker="BBCA",
        opened_at=SIGNAL - timedelta(days=2),
        entry_quantity_lots=2,
        current_quantity_shares=200,
        quantity_origin="ENTRY_LOT_ROUNDED",
        total_cost_basis_idr=1_800_000,
        mark_price_idr=10_000,
        mark_as_of=SIGNAL - timedelta(minutes=10),
        planned_stop_price_idr=9_000,
        source_record_sha256=HASH_A,
    )
    commitment = FrozenPortfolioSourceCommitment(
        commitment_id="COMMIT-BBRI",
        ticker="BBRI",
        created_at=SIGNAL - timedelta(minutes=20),
        expires_at=SIGNAL + timedelta(hours=1),
        reserved_cash_idr=100_000,
        potential_exposure_idr=13_000_000,
        potential_risk_idr=200_000,
        source_decision_sha256=HASH_B,
        status="PENDING",
    )
    payload = FrozenPortfolioSourcePayload(
        settled_cash=estimable_money(98_000_000),
        unsettled_cash_receivable=estimable_money(0),
        reserved_cash=estimable_money(100_000),
        realized_pnl_today=estimable_money(0),
        control_30d_closed_trade_avg_pnl=estimable_ratio(0.0),
        positions_status="ESTIMABLE",
        positions=(position,),
        pending_commitments_status="ESTIMABLE",
        pending_commitments=(commitment,),
    )
    source = _source_record(manifest, payload=payload)
    state = build_frozen_control_portfolio_state(
        manifest=manifest,
        raw_capture=raw,
        candidate_set=candidate_set,
        policy=policy,
        source_record=source,
        state_as_of=SIGNAL - timedelta(minutes=5),
        captured_at=SIGNAL + timedelta(minutes=4),
    )

    assert state.marked_positions_value.value_idr == 2_000_000
    assert state.nav.value_idr == 100_000_000
    assert state.open_risk.value_idr == 200_000
    assert state.reserved_cash.value_idr == 100_000
    assert state.deployable_cash.value_idr == 92_900_000
    assert state.portfolio_heat.value == 0.002


def test_expired_commitment_cannot_remain_pending() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    raw, candidate_set = _raw_and_set(manifest)
    commitment = FrozenPortfolioSourceCommitment(
        commitment_id="COMMIT-EXPIRED",
        ticker="BBRI",
        created_at=SIGNAL - timedelta(minutes=20),
        expires_at=SIGNAL - timedelta(minutes=6),
        reserved_cash_idr=100_000,
        potential_exposure_idr=13_000_000,
        potential_risk_idr=200_000,
        source_decision_sha256=HASH_B,
        status="PENDING",
    )
    payload = FrozenPortfolioSourcePayload(
        settled_cash=estimable_money(100_000_000),
        unsettled_cash_receivable=estimable_money(0),
        reserved_cash=estimable_money(100_000),
        realized_pnl_today=estimable_money(0),
        control_30d_closed_trade_avg_pnl=estimable_ratio(0.0),
        positions_status="ESTIMABLE",
        positions=(),
        pending_commitments_status="ESTIMABLE",
        pending_commitments=(commitment,),
    )
    source = _source_record(manifest, payload=payload)

    with pytest.raises(ShadowContractError, match="state is invalid"):
        build_frozen_control_portfolio_state(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            policy=policy,
            source_record=source,
            state_as_of=SIGNAL - timedelta(minutes=5),
            captured_at=SIGNAL + timedelta(minutes=4),
        )


def test_two_candidates_share_exact_prebatch_state(tmp_path: Path) -> None:
    snapshots = (_snapshot("BBCA"), _snapshot("BBRI"))
    manifest, _, raw, candidate_set, _, state, store = _state_scenario(
        tmp_path,
        snapshots=snapshots,
    )
    seen: list[str] = []

    def control(decision_input):
        seen.append(decision_input.portfolio_state_sha256)
        return _decision("CONTROL")

    def challenger(decision_input):
        seen.append(decision_input.portfolio_state_sha256)
        return _decision("CHALLENGER")

    for candidate, snapshot in zip(raw.candidates, snapshots, strict=True):
        produce_paired_observation(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            candidate=candidate,
            frozen_snapshot=snapshot,
            feature_values_sha256=HASH_A,
            portfolio_state_sha256=str(canonical_sha256(state)),
            artifact_store=store,
            authorization_loader=_TestOnlyAuthorizationLoader(),
            approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
            control_evaluator=control,
            challenger_evaluator=challenger,
            cluster=_cluster(manifest),
            captured_at=SIGNAL + timedelta(minutes=4),
        )

    assert seen == [canonical_sha256(state)] * 4


def test_paired_evaluators_receive_same_immutable_input(tmp_path: Path) -> None:
    manifest, _, raw, candidate_set, _, state, store = _state_scenario(tmp_path)
    candidate = raw.candidates[0]
    snapshot = _snapshot()
    input_hashes: list[str] = []

    def control(decision_input):
        input_hashes.append(str(canonical_sha256(decision_input)))
        with pytest.raises(ValidationError):
            decision_input.portfolio_state.starting_capital_idr = 1
        return _decision("CONTROL")

    def challenger(decision_input):
        input_hashes.append(str(canonical_sha256(decision_input)))
        return _decision("CHALLENGER", actionable=False, reason_codes=("WAIT",))

    observation = produce_paired_observation(
        manifest=manifest,
        raw_capture=raw,
        candidate_set=candidate_set,
        candidate=candidate,
        frozen_snapshot=snapshot,
        feature_values_sha256=HASH_A,
        portfolio_state_sha256=str(canonical_sha256(state)),
        artifact_store=store,
        authorization_loader=_TestOnlyAuthorizationLoader(),
        approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
        control_evaluator=control,
        challenger_evaluator=challenger,
        cluster=_cluster(manifest),
        captured_at=SIGNAL + timedelta(minutes=4),
    )

    assert input_hashes[0] == input_hashes[1]
    assert observation.control_decision.would_be_actionable is True
    assert observation.challenger_decision.would_be_actionable is False
    assert observation.portfolio_state_sha256 == canonical_sha256(state)


def test_evaluator_mutation_fails_before_second_side(tmp_path: Path) -> None:
    manifest, _, raw, candidate_set, _, state, store = _state_scenario(tmp_path)
    challenger_called = False

    def malicious_control(decision_input):
        object.__setattr__(
            decision_input.portfolio_state,
            "starting_capital_idr",
            1,
        )
        return _decision("CONTROL")

    def challenger(decision_input):
        nonlocal challenger_called
        challenger_called = True
        return _decision("CHALLENGER")

    with pytest.raises(ShadowContractError, match="mutated"):
        produce_paired_observation(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            candidate=raw.candidates[0],
            frozen_snapshot=_snapshot(),
            feature_values_sha256=HASH_A,
            portfolio_state_sha256=str(canonical_sha256(state)),
            artifact_store=store,
            authorization_loader=_TestOnlyAuthorizationLoader(),
            approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
            control_evaluator=malicious_control,
            challenger_evaluator=challenger,
            cluster=_cluster(manifest),
            captured_at=SIGNAL + timedelta(minutes=4),
        )
    assert challenger_called is False


def test_paired_authorization_is_checked_before_first_evaluator(
    tmp_path: Path,
) -> None:
    manifest, _, raw, candidate_set, _, state, store = _state_scenario(tmp_path)
    evaluator_calls = 0
    loader = _TestOnlyAuthorizationLoader(
        error=ShadowContractError("synthetic closed protocol")
    )

    def evaluator(_):
        nonlocal evaluator_calls
        evaluator_calls += 1
        return _decision("CONTROL")

    with pytest.raises(ShadowContractError, match="authorization"):
        produce_paired_observation(
            manifest=manifest,
            raw_capture=raw,
            candidate_set=candidate_set,
            candidate=raw.candidates[0],
            frozen_snapshot=_snapshot(),
            feature_values_sha256=HASH_A,
            portfolio_state_sha256=str(canonical_sha256(state)),
            artifact_store=store,
            authorization_loader=loader,
            approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
            control_evaluator=evaluator,
            challenger_evaluator=lambda _: _decision("CHALLENGER"),
            cluster=_cluster(manifest),
            captured_at=SIGNAL + timedelta(minutes=4),
        )

    assert len(loader.calls) == 1
    assert evaluator_calls == 0


def test_identical_inputs_replay_identical_state_and_observation_hashes(
    tmp_path: Path,
) -> None:
    manifest, _, raw, candidate_set, _, state, store = _state_scenario(tmp_path)

    def control(_):
        return _decision("CONTROL")

    def challenger(_):
        return _decision("CHALLENGER")

    kwargs = {
        "manifest": manifest,
        "raw_capture": raw,
        "candidate_set": candidate_set,
        "candidate": raw.candidates[0],
        "frozen_snapshot": _snapshot(),
        "feature_values_sha256": HASH_A,
        "portfolio_state_sha256": str(canonical_sha256(state)),
        "artifact_store": store,
        "authorization_loader": _TestOnlyAuthorizationLoader(),
        "approval_ledger_id": "TEST-ONLY-NON-AUTHORITY",
        "control_evaluator": control,
        "challenger_evaluator": challenger,
        "cluster": _cluster(manifest),
        "captured_at": SIGNAL + timedelta(minutes=4),
    }
    first = produce_paired_observation(**kwargs)
    second = produce_paired_observation(**kwargs)

    assert canonical_sha256(first) == canonical_sha256(second)
    assert first.observation_id == second.observation_id


def test_observation_identity_changes_with_feature_hash(
    tmp_path: Path,
) -> None:
    manifest, _, raw, candidate_set, _, state, store = _state_scenario(tmp_path)
    common = {
        "manifest": manifest,
        "raw_capture": raw,
        "candidate_set": candidate_set,
        "candidate": raw.candidates[0],
        "frozen_snapshot": _snapshot(),
        "portfolio_state_sha256": str(canonical_sha256(state)),
        "artifact_store": store,
        "authorization_loader": _TestOnlyAuthorizationLoader(),
        "approval_ledger_id": "TEST-ONLY-NON-AUTHORITY",
        "control_evaluator": lambda _: _decision("CONTROL"),
        "challenger_evaluator": lambda _: _decision("CHALLENGER"),
        "cluster": _cluster(manifest),
        "captured_at": SIGNAL + timedelta(minutes=4),
    }
    first = produce_paired_observation(
        **common,
        feature_values_sha256=HASH_A,
    )
    second = produce_paired_observation(
        **common,
        feature_values_sha256=HASH_B,
    )

    assert first.observation_id != second.observation_id
    assert canonical_sha256(first) != canonical_sha256(second)


def test_lineage_v2_reconstructs_policy_source_state_and_observation(
    tmp_path: Path,
) -> None:
    manifest, policy, raw, candidate_set, source, state, store = _state_scenario(
        tmp_path
    )
    candidate = raw.candidates[0]
    snapshot = _snapshot()
    observation = produce_paired_observation(
        manifest=manifest,
        raw_capture=raw,
        candidate_set=candidate_set,
        candidate=candidate,
        frozen_snapshot=snapshot,
        feature_values_sha256=HASH_A,
        portfolio_state_sha256=str(canonical_sha256(state)),
        artifact_store=store,
        authorization_loader=_TestOnlyAuthorizationLoader(),
        approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
        control_evaluator=lambda _: _decision("CONTROL"),
        challenger_evaluator=lambda _: _decision("CHALLENGER"),
        cluster=_cluster(manifest),
        captured_at=SIGNAL + timedelta(minutes=4),
    )
    artifacts = {
        "manifest": manifest,
        "frozen_snapshot": snapshot,
        "raw_capture": raw,
        "candidate_set": candidate_set,
        "candidate": candidate,
        "observation": observation,
        "policy": policy,
        "source_record": source,
        "state": state,
    }
    bundle = build_portfolio_lineage_bundle(**artifacts)

    assert bundle.contract_version == "shadow-lineage-bundle-v2"
    assert bundle.base_lineage.contract_version == "shadow-lineage-bundle-v1"
    assert verify_portfolio_lineage_bundle(bundle, **artifacts) == bundle


def test_maturation_reload_rejects_tampered_portfolio_state(
    tmp_path: Path,
) -> None:
    manifest, _, raw, candidate_set, _, state, store = _state_scenario(tmp_path)
    observation = produce_paired_observation(
        manifest=manifest,
        raw_capture=raw,
        candidate_set=candidate_set,
        candidate=raw.candidates[0],
        frozen_snapshot=_snapshot(),
        feature_values_sha256=HASH_A,
        portfolio_state_sha256=str(canonical_sha256(state)),
        artifact_store=store,
        authorization_loader=_TestOnlyAuthorizationLoader(),
        approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
        control_evaluator=lambda _: _decision("CONTROL"),
        challenger_evaluator=lambda _: _decision("CHALLENGER"),
        cluster=_cluster(manifest),
        captured_at=SIGNAL + timedelta(minutes=4),
    )
    governance = ProtocolGovernanceStore(tmp_path)
    loaded = governance.load_portfolio_observation_artifacts(
        manifest,
        observation,
    )
    assert canonical_sha256(loaded[2]) == canonical_sha256(state)
    state_hash = str(canonical_sha256(state))
    state_path = (
        tmp_path
        / "protocols"
        / manifest.protocol_id
        / str(canonical_sha256(manifest))
        / "portfolio_states"
        / state_hash
        / f"{state_hash}.json"
    )
    state_path.write_bytes(
        state_path.read_bytes().replace(b"95000000", b"95000001", 1)
    )

    with pytest.raises(ShadowContractError):
        governance.load_portfolio_observation_artifacts(
            manifest,
            observation,
        )


def test_state_hash_is_identical_across_separate_python_processes(
    tmp_path: Path,
) -> None:
    *_, state, _ = _state_scenario(tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_bytes(canonical_json_bytes(state))
    script = (
        "from pathlib import Path;"
        "from core.shadow_protocol import "
        "canonical_sha256,load_portfolio_state_v1;"
        f"p=Path({str(state_path)!r});"
        "print(canonical_sha256(load_portfolio_state_v1(p.read_bytes())))"
    )
    hashes: list[str] = []
    for seed in ("1", "987654"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        hashes.append(completed.stdout.strip())

    assert hashes == [canonical_sha256(state), canonical_sha256(state)]


def test_authority_literals_remain_evaluation_only(tmp_path: Path) -> None:
    manifest, policy, _, _, source, state, _ = _state_scenario(tmp_path)

    for artifact in (policy, source, state):
        assert artifact.evaluation_only is True
        assert artifact.live_authority is False
        assert artifact.affects_execution is False
        assert artifact.affects_ranking is False
        assert artifact.affects_sizing is False
    assert manifest.production_feature_flag_default is False
