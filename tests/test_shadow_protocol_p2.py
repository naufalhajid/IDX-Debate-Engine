"""Acceptance and tamper tests for build-only RS-P2-008 through RS-P2-013."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from core.shadow_protocol import (
    ApprovalLedger,
    ApprovalLedgerEvent,
    ApprovalRecord,
    CandidateDisposition,
    CandidateEvent,
    CandidateSetManifest,
    CandidateSetStore,
    CandidateSetView,
    ClusterRuleDefinition,
    ContentHash,
    CorporateActionEvent,
    CorporateActionPolicy,
    CostAssumptions,
    FrozenBarSeries,
    FrozenSnapshot,
    GateMeasurement,
    GoNoGoRules,
    IndependentClusterMetadata,
    LabelDefinition,
    MaturationRequest,
    OutcomeBar,
    OutcomeLedger,
    ProtocolAuthorizationBundle,
    ProtocolClosureRecord,
    QuarantinedCandidateEvent,
    RawCandidateSetCapture,
    RecordedTradeGeometry,
    ShadowContractError,
    ShadowDecision,
    ShadowObservation,
    ShadowProtocolManifest,
    SELF_ADVERSARIAL_PROMPTS,
    SelfAdversarialReviewItem,
    SourceDefinition,
    TradingCalendar,
    UniverseDefinition,
    assert_opportunity_set_parity,
    build_execution_policy,
    build_lineage_bundle,
    build_verified_outcome_lineage as _build_verified_outcome_lineage,
    canonical_bar_series_sha256,
    canonical_corporate_action_events_sha256,
    canonical_corporate_action_policy_sha256,
    canonical_corporate_action_source_record_sha256,
    canonical_decision_payload_sha256,
    canonical_frozen_snapshot_sha256,
    canonical_json_bytes,
    canonical_opportunity_set_sha256,
    canonical_outcome_id,
    canonical_outcome_bar_sha256,
    canonical_outcome_source_record_sha256,
    canonical_payload_json,
    canonical_payload_sha256,
    canonical_raw_candidate_set_sha256,
    canonical_raw_event_id,
    canonical_rules_sha256,
    canonical_sha256,
    canonical_source_record_sha256,
    canonical_trading_calendar_sha256,
    canonical_view_sha256,
    classify_divergence,
    derive_completed_idx_sessions,
    evaluate_all_horizons as _evaluate_all_horizons,
    evaluate_horizon as _evaluate_horizon,
    verify_lineage_bundle,
    verify_opportunity_set_parity,
    verify_outcome_against_request as _verify_outcome_against_request,
)


IDX = ZoneInfo("Asia/Jakarta")
SIGNAL = datetime(2026, 7, 17, 16, 30, tzinfo=IDX)
CREATED = datetime(2026, 7, 7, 12, 0, tzinfo=IDX)
DRAFT_FROZEN = datetime(2026, 7, 8, 12, 0, tzinfo=IDX)
APPROVED_AT = datetime(2026, 7, 14, 17, 0, tzinfo=IDX)
COLLECTION_START = datetime(2026, 7, 17, 16, 0, tzinfo=IDX)
METHODOLOGY = b"# Frozen methodology\n\nPredeclared test methodology.\n"
METHODOLOGY_SHA256 = hashlib.sha256(METHODOLOGY).hexdigest()
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


@dataclass(frozen=True)
class _StaticAuthorizationLoader:
    authorization: ProtocolAuthorizationBundle

    def load_authorization(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
    ) -> ProtocolAuthorizationBundle:
        assert protocol_id == self.authorization.manifest.protocol_id
        assert manifest_canonical_sha256 == canonical_sha256(
            self.authorization.manifest
        )
        assert ledger_id == self.authorization.approval_ledger.ledger_id
        return self.authorization


def _authorization_loader(
    request: MaturationRequest,
) -> _StaticAuthorizationLoader:
    return _StaticAuthorizationLoader(request.authorization)


def evaluate_horizon(request: MaturationRequest):
    return _evaluate_horizon(
        request,
        authorization_loader=_authorization_loader(request),
    )


def evaluate_all_horizons(request: MaturationRequest):
    return _evaluate_all_horizons(
        request,
        authorization_loader=_authorization_loader(request),
    )


def verify_outcome_against_request(request: MaturationRequest, outcome):
    return _verify_outcome_against_request(
        request,
        outcome,
        authorization_loader=_authorization_loader(request),
    )


def build_verified_outcome_lineage(request: MaturationRequest, outcome):
    return _build_verified_outcome_lineage(
        request,
        outcome,
        authorization_loader=_authorization_loader(request),
    )


def _weekday_sessions(start: date, count: int) -> tuple[date, ...]:
    result: list[date] = [start]
    cursor = start
    while len(result) < count:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            result.append(cursor)
    return tuple(result)


SESSIONS = _weekday_sessions(SIGNAL.date(), 32)
PRIOR_SESSIONS = _weekday_sessions(date(2026, 7, 9), 6)


def _source_definition(source_id: str, marker: str) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        source_type="FILE",
        locator=f"shadow://{source_id.lower()}",
        as_of_field="source_as_of",
        expiry_rule="frozen point-in-time record",
        missing_policy="ABSTAIN",
        contract_version="source-definition-v1",
        source_sha256=marker * 64,
    )


def _source_definitions() -> tuple[SourceDefinition, ...]:
    return (
        _source_definition("CANDIDATES", "1"),
        _source_definition("SNAPSHOT", "2"),
        _source_definition("OUTCOME_BARS", "3"),
        _source_definition("CORPORATE_ACTIONS", "4"),
    )


def _source_definition_hash(source_id: str) -> str:
    source = next(
        item for item in _source_definitions() if item.source_id == source_id
    )
    digest = canonical_sha256(source)
    assert digest is not None
    return digest


def _geometry() -> RecordedTradeGeometry:
    return RecordedTradeGeometry(
        entry_low=100.0,
        entry_high=105.0,
        target_price=120.0,
        stop_loss=90.0,
        risk_reward_ratio=2.0,
        required_risk_reward=2.0,
    )


def _costs() -> CostAssumptions:
    return CostAssumptions(
        buy_commission_bps=15.0,
        sell_commission_bps=25.0,
        sell_tax_bps=10.0,
        slippage_bps=5.0,
        bid_ask_bps=5.0,
        lot_size=100,
        liquidity_execution_rule="FIXED_ONE_LOT_NO_LIQUIDITY_MODEL",
        price_rounding_rule="SOURCE_SUPPLIED_PRICES_NO_ENGINE_ROUNDING",
        cost_model_version="SHADOW_CASH_FLOW_BPS_V1",
    )


def _calendar(sessions: tuple[date, ...] = SESSIONS) -> TradingCalendar:
    all_sessions = tuple(sorted(set((*PRIOR_SESSIONS, *sessions))))
    calendar_id = f"IDX-FROZEN-{all_sessions[-1].isoformat()}"
    return TradingCalendar(
        calendar_id=calendar_id,
        calendar_sha256=canonical_trading_calendar_sha256(
            calendar_id,
            all_sessions,
        ),
        sessions=all_sessions,
    )


def _event(
    event_id: str,
    kind: str,
    effective_date: date,
    *,
    ticker: str = "BBCA",
    price_factor: float = 1.0,
    quantity_factor: float = 1.0,
    capital_call: float = 0.0,
    cash: float = 0.0,
    published_at: datetime = SIGNAL,
    terms_complete: bool = True,
    source_id: str = "CORPORATE_ACTIONS",
    definition_source_id: str = "CORPORATE_ACTIONS",
) -> CorporateActionEvent:
    source_definition_sha256 = _source_definition_hash(definition_source_id)
    source_sha256 = canonical_corporate_action_source_record_sha256(
        event_id=event_id,
        ticker=ticker,
        effective_date=effective_date,
        kind=kind,
        price_factor=price_factor,
        quantity_factor=quantity_factor,
        capital_call_per_pre_event_share=capital_call,
        cash_per_share=cash,
        source_id=source_id,
        source_definition_sha256=source_definition_sha256,
        published_at=published_at,
    )
    return CorporateActionEvent(
        event_id=event_id,
        ticker=ticker,
        effective_date=effective_date,
        kind=kind,
        price_factor=price_factor,
        quantity_factor=quantity_factor,
        capital_call_per_pre_event_share=capital_call,
        cash_per_share=cash,
        source_id=source_id,
        source_definition_sha256=source_definition_sha256,
        source_sha256=source_sha256,
        published_at=published_at,
        terms_complete=terms_complete,
    )


def _policy(
    *,
    convention: str = "PRICE_RETURN",
    events: tuple[CorporateActionEvent, ...] = (),
    dividends_are_in_prices: bool = False,
) -> CorporateActionPolicy:
    return CorporateActionPolicy(
        dividend_return_convention=convention,
        dividend_entitlement_rule="POSITION_OPEN_BEFORE_EX_DATE",
        bar_price_basis="RAW_AS_TRADED",
        prices_are_adjusted=False,
        dividends_are_in_prices=dividends_are_in_prices,
        events=events,
        events_sha256=canonical_corporate_action_events_sha256(events),
        policy_sha256=canonical_corporate_action_policy_sha256(
            convention,
            False,
            dividends_are_in_prices,
        ),
    )


def _cluster_rules() -> ClusterRuleDefinition:
    return ClusterRuleDefinition(
        rule_version="cluster-v1",
        issuer_group_rule="same issuer",
        economic_group_rule="same economic group",
        correlation_cluster_rule="frozen rolling correlation clusters",
        systemic_date_block_rule="same signal-date block",
        duplicate_setup_rule="same ticker and geometry",
        representative_rule="first event by signal time",
        effective_n_rule="one independent representative per cluster",
    )


def _manifest(
    policy: CorporateActionPolicy,
    *,
    validity: int = 1,
    sessions: tuple[date, ...] = SESSIONS,
    tickers: tuple[str, ...] = ("BBCA",),
) -> ShadowProtocolManifest:
    calendar = _calendar(sessions)
    cluster_rules = _cluster_rules()
    go = ("predeclared GO",)
    cont = ("predeclared CONTINUE",)
    no_go = ("predeclared NO-GO",)
    return ShadowProtocolManifest(
        protocol_id="RS-C1-P2-TEST",
        component_id="C1",
        manifest_revision=1,
        lifecycle_status="DRAFT",
        created_at=CREATED,
        draft_frozen_at=DRAFT_FROZEN,
        collection_start_not_before=COLLECTION_START,
        fixed_terminal_date=sessions[-1],
        owner="owner@example",
        governance_mode="SOLO_SELF_REVIEW",
        independent_reviewer=None,
        rollback_owner="owner@example",
        baseline_manifest_id="RS-CONTROL-20260717-01",
        baseline_manifest_sha256=HASH_A,
        methodology_document_path=(
            "docs/research/methodology/RS-C1-P2-TEST.md"
        ),
        methodology_document_sha256=METHODOLOGY_SHA256,
        control_content_hashes=(
            ContentHash(path="control.py", sha256=HASH_A, role="CONTROL"),
        ),
        challenger_content_hashes=(
            ContentHash(
                path="challenger.py",
                sha256=HASH_B,
                role="CHALLENGER",
            ),
        ),
        universe=UniverseDefinition(
            universe_id="IDX-P2-TEST",
            quant_mode="MOMENTUM",
            selection_rule="complete raw set before pruning",
            candidate_source_sha256=_source_definition_hash("CANDIDATES"),
            explicit_tickers=tickers,
        ),
        trading_calendar_id=calendar.calendar_id,
        trading_calendar_sha256=calendar.calendar_sha256,
        corporate_action_policy_sha256=policy.policy_sha256,
        thresholds=(),
        features=(),
        sources=_source_definitions(),
        labels=LabelDefinition(
            entry_validity_trading_days=validity,
            activation_rule="FIRST_TRADING_SESSION_AFTER_SIGNAL",
            horizon_clock_rule=(
                "POST_FILL_SESSIONS_EXCLUDING_FILL_SESSION"
            ),
            fill_rule="BUY_LIMIT_OPEN_OR_INTRADAY_TOUCH_AT_ENTRY_HIGH",
            gap_rule="OBSERVED_OPEN_FOR_MARKETABLE_ENTRY_AND_GAP_EXITS",
            entry_gap_through_stop_rule=(
                "FILL_AND_STOP_AT_OBSERVED_OPEN_USING_PLANNED_ENTRY_HIGH_RISK"
            ),
            same_bar_ambiguity_rule=(
                "STOP_FIRST_AND_INTRADAY_ENTRY_TARGET_UNPROVEN"
            ),
            corporate_action_rule=(
                "RAW_AS_TRADED_BARS_WITH_FORWARD_SPLIT_AND_DIVIDEND_ADJUSTMENTS"
            ),
            rights_treatment_rule=(
                "RIGHTS_INVALID_UNTIL_ELECTION_DELIVERY_AND_COST_RULES_ARE_FROZEN"
            ),
            dividend_return_convention=convention_cast(policy),
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
        trial_registry_id="TRIAL-P2-TEST",
        production_feature_flag="SHADOW_P2_TEST",
        rollback_plan="delete evaluation-only artifacts; control unchanged",
    )


def convention_cast(policy: CorporateActionPolicy) -> str:
    return policy.dividend_return_convention


def _snapshot(
    manifest: ShadowProtocolManifest,
    *,
    ticker: str = "BBCA",
    snapshot_id: str | None = None,
    source_expires_at: datetime | None = SIGNAL + timedelta(days=1),
) -> FrozenSnapshot:
    resolved_snapshot_id = snapshot_id or (
        "SNAP-1" if ticker == "BBCA" else f"SNAP-{ticker}"
    )
    payload_json = canonical_payload_json(
        {"Ticker": ticker, "as_of": SIGNAL.isoformat(), "close": 100.0}
    )
    payload_sha256 = canonical_payload_sha256(payload_json)
    source_definition_sha256 = _source_definition_hash("SNAPSHOT")
    snapshot_as_of = SIGNAL - timedelta(minutes=30)
    source_as_of = SIGNAL - timedelta(hours=1)
    source_record_sha256 = canonical_source_record_sha256(
        source_id="SNAPSHOT",
        source_definition_sha256=source_definition_sha256,
        source_as_of=source_as_of,
        source_expires_at=source_expires_at,
        source_row_number=None,
        payload_sha256=payload_sha256,
    )
    snapshot_sha256 = canonical_frozen_snapshot_sha256(
        snapshot_id=resolved_snapshot_id,
        ticker=ticker,
        as_of_date=SIGNAL.date(),
        snapshot_as_of=snapshot_as_of,
        source_id="SNAPSHOT",
        source_definition_sha256=source_definition_sha256,
        source_record_sha256=source_record_sha256,
        source_as_of=source_as_of,
        source_expires_at=source_expires_at,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
    )
    assert canonical_sha256(manifest) is not None
    return FrozenSnapshot(
        snapshot_id=resolved_snapshot_id,
        ticker=ticker,
        as_of_date=SIGNAL.date(),
        snapshot_as_of=snapshot_as_of,
        snapshot_sha256=snapshot_sha256,
        source_id="SNAPSHOT",
        source_definition_sha256=source_definition_sha256,
        source_record_sha256=source_record_sha256,
        source_as_of=source_as_of,
        source_expires_at=source_expires_at,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
    )


def _signal_event_hash(policy: CorporateActionPolicy) -> str:
    events = tuple(
        event
        for event in policy.events
        if event.published_at <= SIGNAL
    )
    return canonical_corporate_action_events_sha256(events)


def _candidate_record(
    manifest: ShadowProtocolManifest,
    snapshot: FrozenSnapshot,
    policy: CorporateActionPolicy,
    *,
    ticker: str,
    row: int,
    opportunity_sha256: str = HASH_A,
    expires_at: datetime | None = None,
    quarantine_reason: str | None = None,
) -> CandidateEvent | QuarantinedCandidateEvent:
    raw_ticker = ticker
    payload = {
        "Ticker": raw_ticker,
        "nested": {"rank": row},
        "price": 100.0 + row,
        "row": row,
    }
    payload_json = canonical_payload_json(payload)
    payload_sha256 = canonical_payload_sha256(payload_json)
    source_as_of = SIGNAL - timedelta(hours=1)
    resolved_expiry = (
        SIGNAL + timedelta(days=1) if expires_at is None else expires_at
    )
    source_definition_sha256 = _source_definition_hash("CANDIDATES")
    source_sha256 = canonical_source_record_sha256(
        source_id="CANDIDATES",
        source_definition_sha256=source_definition_sha256,
        source_as_of=source_as_of,
        source_expires_at=resolved_expiry,
        source_row_number=row,
        payload_sha256=payload_sha256,
    )
    event_id = canonical_raw_event_id(
        opportunity_set_id="OPP-1",
        ticker=raw_ticker,
        signal_at=SIGNAL,
        snapshot_sha256=snapshot.snapshot_sha256,
        candidate_source_sha256=source_sha256,
        source_row_number=row,
        raw_payload_sha256=payload_sha256,
    )
    common = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": canonical_sha256(manifest),
        "opportunity_set_id": "OPP-1",
        "opportunity_set_sha256": opportunity_sha256,
        "raw_event_id": event_id,
        "signal_at": SIGNAL,
        "as_of_date": SIGNAL.date(),
        "captured_at": SIGNAL + timedelta(minutes=1),
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "snapshot_as_of": snapshot.snapshot_as_of,
        "snapshot_source_record_sha256": snapshot.source_record_sha256,
        "candidate_source_id": "CANDIDATES",
        "candidate_source_definition_sha256": source_definition_sha256,
        "candidate_source_sha256": source_sha256,
        "candidate_source_as_of": source_as_of,
        "candidate_source_expires_at": resolved_expiry,
        "trading_calendar_id": manifest.trading_calendar_id,
        "trading_calendar_sha256": manifest.trading_calendar_sha256,
        "corporate_action_policy_sha256": policy.policy_sha256,
        "corporate_action_events_at_signal_sha256": (
            _signal_event_hash(policy)
        ),
        "source_row_number": row,
        "raw_payload_json": payload_json,
        "raw_payload_sha256": payload_sha256,
    }
    if quarantine_reason is None:
        return CandidateEvent(ticker=ticker, **common)
    return QuarantinedCandidateEvent(
        raw_ticker=raw_ticker,
        quarantine_reason=quarantine_reason,
        **common,
    )


def _view(
    side: str,
    event_ids: tuple[str, ...],
    *,
    pruned_ids: frozenset[str] = frozenset(),
    prune_reasons: dict[str, str] | None = None,
) -> CandidateSetView:
    resolved_reasons = prune_reasons or {}
    dispositions = tuple(
        CandidateDisposition(
            raw_event_id=event_id,
            state="PRUNED" if event_id in pruned_ids else "RETAINED",
            reason_codes=(resolved_reasons.get(event_id, "STATIC_PRUNE"),)
            if event_id in pruned_ids
            else (),
        )
        for event_id in event_ids
    )
    return CandidateSetView(
        side=side,
        input_event_ids=event_ids,
        dispositions=dispositions,
        view_sha256=canonical_view_sha256(
            side,
            event_ids,
            dispositions,
        ),
    )


def _raw_and_set(
    manifest: ShadowProtocolManifest,
    snapshot: FrozenSnapshot,
    policy: CorporateActionPolicy,
    *,
    tickers: tuple[str, ...] = ("BBCA",),
    include_quarantine: bool = False,
    empty: bool = False,
    candidate_set_id: str = "SET-1",
    snapshots: dict[str, FrozenSnapshot] | None = None,
    policies: dict[str, CorporateActionPolicy] | None = None,
    empty_reason: str = "NO_SOURCE_ROWS",
) -> tuple[RawCandidateSetCapture, CandidateSetManifest]:
    snapshot_by_ticker = snapshots or {}
    policy_by_ticker = policies or {}
    records: list[CandidateEvent | QuarantinedCandidateEvent] = []
    if not empty:
        records.extend(
            _candidate_record(
                manifest,
                snapshot_by_ticker.get(ticker)
                or (
                    snapshot
                    if snapshot.ticker == ticker
                    else _snapshot(manifest, ticker=ticker)
                ),
                policy_by_ticker.get(ticker, policy),
                ticker=ticker,
                row=index,
            )
            for index, ticker in enumerate(tickers, start=1)
        )
    if include_quarantine:
        records.append(
            _candidate_record(
                manifest,
                snapshot,
                policy,
                ticker="../bad",
                row=len(records) + 1,
                quarantine_reason="INVALID_TICKER",
            )
        )
    opportunity_sha256 = canonical_opportunity_set_sha256(
        "OPP-1",
        SIGNAL.date(),
        records,
        empty_reason=empty_reason if not records else None,
        candidate_source_definition_sha256=(
            _source_definition_hash("CANDIDATES")
        ),
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=policy.policy_sha256,
    )
    records = [
        type(record).model_validate(
            {
                **record.model_dump(mode="python"),
                "opportunity_set_sha256": opportunity_sha256,
            }
        )
        for record in records
    ]
    raw_hash = canonical_raw_candidate_set_sha256(
        records,
        empty_reason=empty_reason if not records else None,
    )
    raw_capture = RawCandidateSetCapture(
        raw_capture_id=f"RAW-{candidate_set_id}",
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=canonical_sha256(manifest),
        opportunity_set_id="OPP-1",
        opportunity_set_sha256=opportunity_sha256,
        signal_at=SIGNAL,
        as_of_date=SIGNAL.date(),
        captured_at=SIGNAL + timedelta(minutes=2),
        candidate_source_id="CANDIDATES",
        candidate_source_definition_sha256=(
            _source_definition_hash("CANDIDATES")
        ),
        trading_calendar_id=manifest.trading_calendar_id,
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=policy.policy_sha256,
        raw_candidate_count=len(records),
        empty_reason=empty_reason if not records else None,
        raw_candidate_set_sha256=raw_hash,
        candidates=tuple(records),
    )
    event_ids = tuple(record.raw_event_id for record in records)
    quarantined = frozenset(
        record.raw_event_id
        for record in records
        if isinstance(record, QuarantinedCandidateEvent)
    )
    quarantine_reasons = {
        record.raw_event_id: record.quarantine_reason
        for record in records
        if isinstance(record, QuarantinedCandidateEvent)
    }
    candidate_set = CandidateSetManifest(
        candidate_set_id=candidate_set_id,
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=canonical_sha256(manifest),
        raw_capture_id=raw_capture.raw_capture_id,
        raw_capture_sha256=canonical_sha256(raw_capture),
        opportunity_set_id=raw_capture.opportunity_set_id,
        opportunity_set_sha256=raw_capture.opportunity_set_sha256,
        candidate_source_id=raw_capture.candidate_source_id,
        candidate_source_definition_sha256=(
            raw_capture.candidate_source_definition_sha256
        ),
        trading_calendar_id=raw_capture.trading_calendar_id,
        trading_calendar_sha256=raw_capture.trading_calendar_sha256,
        corporate_action_policy_sha256=(
            raw_capture.corporate_action_policy_sha256
        ),
        as_of_date=raw_capture.as_of_date,
        captured_at=SIGNAL + timedelta(minutes=3),
        raw_candidate_count=raw_capture.raw_candidate_count,
        empty_reason=raw_capture.empty_reason,
        raw_candidate_set_sha256=raw_capture.raw_candidate_set_sha256,
        control_view=_view(
            "CONTROL",
            event_ids,
            pruned_ids=quarantined,
            prune_reasons=quarantine_reasons,
        ),
        challenger_view=_view(
            "CHALLENGER",
            event_ids,
            pruned_ids=quarantined,
            prune_reasons=quarantine_reasons,
        ),
    )
    return raw_capture, candidate_set


def _gate(
    *,
    expires_at: datetime | None = SIGNAL + timedelta(days=1),
) -> GateMeasurement:
    return GateMeasurement(
        gate_id="rr_floor",
        observed=2.0,
        threshold=2.0,
        comparator=">=",
        passed=True,
        reason_code="RR_PASS",
        source_id="CANDIDATES",
        source_definition_sha256=_source_definition_hash("CANDIDATES"),
        source_as_of=SIGNAL - timedelta(hours=1),
        expires_at=expires_at,
    )


def _decision(
    role: str,
    *,
    geometry: RecordedTradeGeometry | None = None,
    gate: GateMeasurement | None = None,
) -> ShadowDecision:
    values = {
        "decision_role": role,
        "decision_state": "DEPLOYABLE",
        "rating": "BUY",
        "would_be_actionable": True,
        "would_allocate": False,
        "position_size_basis": "NONE",
        "reason_codes": ("RR_PASS",),
        "gate_measurements": (gate or _gate(),),
        "geometry": geometry or _geometry(),
    }
    draft = ShadowDecision.model_construct(
        **values,
        decision_payload_sha256=HASH_A,
    )
    return ShadowDecision(
        **values,
        decision_payload_sha256=canonical_decision_payload_sha256(draft),
    )


def _observation(
    manifest: ShadowProtocolManifest,
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
    *,
    candidate: CandidateEvent | None = None,
    observation_id: str = "OBS-1",
    control_decision: ShadowDecision | None = None,
    challenger_decision: ShadowDecision | None = None,
) -> ShadowObservation:
    resolved_candidate = candidate or next(
        record
        for record in raw_capture.candidates
        if isinstance(record, CandidateEvent)
    )
    cluster_id = f"CL-{resolved_candidate.ticker}"
    cluster = IndependentClusterMetadata(
        assignment_status="ASSIGNED",
        cluster_id=cluster_id,
        cluster_rule_sha256=canonical_sha256(manifest.cluster_rules),
        member_event_ids=(resolved_candidate.raw_event_id,),
        membership_reasons=("same setup",),
        raw_event_count=1,
        effective_n_contribution=1.0,
        assigned_at=SIGNAL,
        clustering_inputs_sha256=HASH_A,
    )
    resolved_control = control_decision or _decision("CONTROL")
    resolved_challenger = challenger_decision or _decision("CHALLENGER")
    return ShadowObservation(
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=canonical_sha256(manifest),
        candidate_set_id=candidate_set.candidate_set_id,
        candidate_set_sha256=canonical_sha256(candidate_set),
        observation_id=observation_id,
        raw_event_id=resolved_candidate.raw_event_id,
        ticker=resolved_candidate.ticker,
        signal_at=resolved_candidate.signal_at,
        as_of_date=resolved_candidate.as_of_date,
        captured_at=SIGNAL + timedelta(minutes=4),
        opportunity_set_id=resolved_candidate.opportunity_set_id,
        opportunity_set_sha256=resolved_candidate.opportunity_set_sha256,
        snapshot_id=resolved_candidate.snapshot_id,
        snapshot_sha256=resolved_candidate.snapshot_sha256,
        feature_values_sha256=HASH_A,
        portfolio_state_sha256=HASH_B,
        cluster_rule_sha256=cluster.cluster_rule_sha256,
        independent_cluster_id=cluster.cluster_id,
        cluster=cluster,
        control_decision=resolved_control,
        challenger_decision=resolved_challenger,
        divergence=classify_divergence(
            resolved_control,
            resolved_challenger,
        ),
    )


@dataclass(frozen=True)
class Scenario:
    manifest: ShadowProtocolManifest
    trading_calendar: TradingCalendar
    snapshot: FrozenSnapshot
    raw_capture: RawCandidateSetCapture
    candidate_set: CandidateSetManifest
    candidate: CandidateEvent
    observation: ShadowObservation
    authorization: ProtocolAuthorizationBundle | None


def _self_adversarial_review() -> tuple[SelfAdversarialReviewItem, ...]:
    return tuple(
        SelfAdversarialReviewItem(
            prompt_id=prompt_id,
            prompt_text=prompt_text,
            response="Reviewed against the frozen P2 test fixture.",
            evidence_refs=("tests/test_shadow_protocol_p2.py",),
            disposition="PASS",
        )
        for prompt_id, prompt_text in SELF_ADVERSARIAL_PROMPTS.items()
    )


def _authorization(
    manifest: ShadowProtocolManifest,
    observation: ShadowObservation,
    trading_calendar: TradingCalendar,
) -> ProtocolAuthorizationBundle:
    manifest_raw = canonical_json_bytes(manifest)
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    completed_sessions = derive_completed_idx_sessions(
        trading_calendar,
        draft_frozen_at=manifest.draft_frozen_at,
        decided_at=APPROVED_AT,
    )
    approval = ApprovalRecord(
        approval_id="APPROVAL-P2-TEST",
        approval_ledger_id="LEDGER-P2-TEST",
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_contract_version=manifest.contract_version,
        manifest_revision=manifest.manifest_revision,
        draft_manifest_canonical_sha256=manifest_sha256,
        draft_manifest_raw_file_sha256=manifest_sha256,
        draft_manifest_raw_byte_length=len(manifest_raw),
        draft_frozen_at=manifest.draft_frozen_at,
        decided_at=APPROVED_AT,
        owner=manifest.owner,
        governance_mode=manifest.governance_mode,
        approved_by=manifest.owner,
        independent_reviewer=None,
        trading_calendar_contract_version=trading_calendar.contract_version,
        trading_calendar_id=trading_calendar.calendar_id,
        trading_calendar_sha256=trading_calendar.calendar_sha256,
        completed_idx_trading_sessions=completed_sessions,
        self_adversarial_review=_self_adversarial_review(),
        attestation=(
            "I approve A1 for this exact manifest hash pair, for shadow "
            "collection only, with live_authority=false."
        ),
    )
    approval_raw = canonical_json_bytes(approval)
    approval_hash = hashlib.sha256(approval_raw).hexdigest()
    approval_event = ApprovalLedgerEvent(
        ledger_id=approval.approval_ledger_id,
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_revision=manifest.manifest_revision,
        draft_manifest_canonical_sha256=manifest_sha256,
        draft_manifest_raw_file_sha256=manifest_sha256,
        draft_manifest_raw_byte_length=len(manifest_raw),
        sequence=1,
        event_id=f"{approval.approval_id}-LEDGER",
        event_type="A1_APPROVED",
        record_kind="APPROVAL",
        record_id=approval.approval_id,
        record_contract_version=approval.contract_version,
        record_canonical_sha256=approval_hash,
        record_raw_file_sha256=approval_hash,
        record_raw_byte_length=len(approval_raw),
        recorded_at=approval.decided_at,
    )
    observation_raw = canonical_json_bytes(observation)
    observation_hash = hashlib.sha256(observation_raw).hexdigest()
    observation_event = ApprovalLedgerEvent(
        ledger_id=approval.approval_ledger_id,
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_revision=manifest.manifest_revision,
        draft_manifest_canonical_sha256=manifest_sha256,
        draft_manifest_raw_file_sha256=manifest_sha256,
        draft_manifest_raw_byte_length=len(manifest_raw),
        sequence=2,
        event_id=f"{observation.observation_id}-COLLECTION",
        previous_event_sha256=canonical_sha256(approval_event),
        event_type="OBSERVATION_AUTHORIZED",
        record_kind="OBSERVATION",
        record_id=observation.observation_id,
        record_contract_version=observation.contract_version,
        record_canonical_sha256=observation_hash,
        record_raw_file_sha256=observation_hash,
        record_raw_byte_length=len(observation_raw),
        recorded_at=observation.captured_at,
    )
    ledger = ApprovalLedger(
        ledger_id=approval.approval_ledger_id,
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_revision=manifest.manifest_revision,
        draft_manifest_canonical_sha256=manifest_sha256,
        draft_manifest_raw_file_sha256=manifest_sha256,
        draft_manifest_raw_byte_length=len(manifest_raw),
        events=(approval_event, observation_event),
    )
    return ProtocolAuthorizationBundle(
        manifest=manifest,
        manifest_raw_file_bytes=manifest_raw,
        methodology_document_bytes=METHODOLOGY,
        approval=approval,
        approval_raw_file_bytes=approval_raw,
        approval_ledger=ledger,
        trading_calendar=trading_calendar,
    )


def _authorization_closed_for_all_maturation(
    authorization: ProtocolAuthorizationBundle,
    *,
    effective_at: datetime,
) -> ProtocolAuthorizationBundle:
    manifest = authorization.manifest
    approval = authorization.approval
    manifest_sha256 = canonical_sha256(manifest)
    closure = ProtocolClosureRecord(
        closure_id="CLOSE-P2-BLOCK-ALL-TEST",
        approval_ledger_id=approval.approval_ledger_id,
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_contract_version=manifest.contract_version,
        manifest_revision=manifest.manifest_revision,
        draft_manifest_canonical_sha256=manifest_sha256,
        draft_manifest_raw_file_sha256=hashlib.sha256(
            authorization.manifest_raw_file_bytes
        ).hexdigest(),
        draft_manifest_raw_byte_length=len(
            authorization.manifest_raw_file_bytes
        ),
        approval_id=approval.approval_id,
        approval_record_canonical_sha256=canonical_sha256(approval),
        effective_at=effective_at,
        recorded_at=effective_at,
        closed_by=manifest.owner,
        governance_mode=manifest.governance_mode,
        reason_code="INTEGRITY_STOP",
        reason="Frozen P2 test closes the protocol after observation capture.",
        maturation_policy="BLOCK_ALL_MATURATION",
    )
    closure_raw = canonical_json_bytes(closure)
    closure_sha256 = hashlib.sha256(closure_raw).hexdigest()
    ledger = authorization.approval_ledger
    closure_event = ApprovalLedgerEvent(
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
        event_id=f"{closure.closure_id}-LEDGER",
        previous_event_sha256=ledger.expected_previous_event_sha256,
        event_type="PROTOCOL_CLOSED",
        record_kind="CLOSURE",
        record_id=closure.closure_id,
        record_contract_version=closure.contract_version,
        record_canonical_sha256=closure_sha256,
        record_raw_file_sha256=closure_sha256,
        record_raw_byte_length=len(closure_raw),
        recorded_at=closure.recorded_at,
    )
    return ProtocolAuthorizationBundle(
        **{
            **authorization.model_dump(
                mode="python",
                exclude={
                    "approval_ledger",
                    "closure",
                    "closure_raw_file_bytes",
                },
            ),
            "approval_ledger": ledger.append(closure_event),
            "closure": closure,
            "closure_raw_file_bytes": closure_raw,
        }
    )


def _scenario(
    policy: CorporateActionPolicy | None = None,
    *,
    validity: int = 1,
    sessions: tuple[date, ...] = SESSIONS,
    approved: bool = True,
) -> Scenario:
    resolved_policy = policy or _policy()
    manifest = _manifest(
        resolved_policy,
        validity=validity,
        sessions=sessions,
    )
    trading_calendar = _calendar(sessions)
    snapshot = _snapshot(manifest)
    raw_capture, candidate_set = _raw_and_set(
        manifest,
        snapshot,
        resolved_policy,
    )
    candidate = next(
        record
        for record in raw_capture.candidates
        if isinstance(record, CandidateEvent)
    )
    observation = _observation(manifest, raw_capture, candidate_set)
    return Scenario(
        manifest=manifest,
        trading_calendar=trading_calendar,
        snapshot=snapshot,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        candidate=candidate,
        observation=observation,
        authorization=(
            _authorization(manifest, observation, trading_calendar)
            if approved
            else None
        ),
    )


def _bar(
    session: date,
    *,
    open_price: float = 102.0,
    high: float = 110.0,
    low: float = 100.0,
    close: float = 105.0,
    dividend: float = 0.0,
) -> OutcomeBar:
    return OutcomeBar(
        trade_date=session,
        open=open_price,
        high=high,
        low=low,
        close=close,
        dividend_per_share=dividend,
    )


def _bar_series(
    scenario: Scenario,
    policy: CorporateActionPolicy,
    bars: tuple[OutcomeBar, ...],
    *,
    previous_source_sha256: str | None = None,
    source_as_of: datetime | None = None,
) -> FrozenBarSeries:
    bars_sha256 = canonical_bar_series_sha256(
        scenario.observation.ticker,
        scenario.snapshot.snapshot_id,
        scenario.snapshot.snapshot_sha256,
        bars,
    )
    resolved_source_as_of = source_as_of or (
        datetime.combine(
            bars[-1].trade_date if bars else SIGNAL.date(),
            time(17, 0),
            tzinfo=IDX,
        )
    )
    source_definition_sha256 = _source_definition_hash("OUTCOME_BARS")
    source_sha256 = canonical_outcome_source_record_sha256(
        source_id="OUTCOME_BARS",
        source_definition_sha256=source_definition_sha256,
        source_as_of=resolved_source_as_of,
        requested_start=SIGNAL.date(),
        requested_end=scenario.manifest.fixed_terminal_date,
        ticker=scenario.observation.ticker,
        snapshot_sha256=scenario.snapshot.snapshot_sha256,
        bars_sha256=bars_sha256,
        corporate_action_policy_sha256=policy.policy_sha256,
        corporate_action_events_sha256=policy.events_sha256,
        previous_source_sha256=previous_source_sha256,
    )
    return FrozenBarSeries(
        ticker=scenario.observation.ticker,
        snapshot_id=scenario.snapshot.snapshot_id,
        snapshot_sha256=scenario.snapshot.snapshot_sha256,
        source_id="OUTCOME_BARS",
        source_definition_sha256=source_definition_sha256,
        source_sha256=source_sha256,
        source_as_of=resolved_source_as_of,
        previous_source_sha256=previous_source_sha256,
        requested_start=SIGNAL.date(),
        requested_end=scenario.manifest.fixed_terminal_date,
        bars=bars,
        bars_sha256=bars_sha256,
        bar_record_sha256s=tuple(
            canonical_outcome_bar_sha256(bar) for bar in bars
        ),
        corporate_action_policy=policy,
    )


def _request(
    scenario: Scenario,
    policy: CorporateActionPolicy,
    bars: tuple[OutcomeBar, ...],
    *,
    side: str = "CONTROL",
    horizon: int = 3,
    cutoff: datetime | None = None,
    previous_source_sha256: str | None = None,
    source_as_of: datetime | None = None,
) -> MaturationRequest:
    series = _bar_series(
        scenario,
        policy,
        bars,
        previous_source_sha256=previous_source_sha256,
        source_as_of=source_as_of,
    )
    return MaturationRequest(
        authorization=scenario.authorization,
        manifest=scenario.manifest,
        raw_capture=scenario.raw_capture,
        candidate_set=scenario.candidate_set,
        candidate=scenario.candidate,
        snapshot=scenario.snapshot,
        observation=scenario.observation,
        side=side,
        decision=(
            scenario.observation.control_decision
            if side == "CONTROL"
            else scenario.observation.challenger_decision
        ),
        horizon_trading_days=horizon,
        evaluation_cutoff=cutoff or series.source_as_of,
        execution_policy=build_execution_policy(scenario.manifest),
        trading_calendar=scenario.trading_calendar,
        bar_series=series,
    )


def test_raw_capture_preserves_quarantine_empty_and_deep_immutability() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    snapshot = _snapshot(manifest)
    raw, _ = _raw_and_set(
        manifest,
        snapshot,
        policy,
        include_quarantine=True,
    )
    assert raw.raw_candidate_count == 2
    assert isinstance(raw.candidates[1], QuarantinedCandidateEvent)
    candidate = raw.candidates[0]
    assert isinstance(candidate, CandidateEvent)
    decoded = candidate.raw_payload
    nested = decoded["nested"]
    assert isinstance(nested, dict)
    nested["rank"] = 999
    assert candidate.raw_payload["nested"] == {"rank": 1}

    empty_raw, empty_set = _raw_and_set(
        manifest,
        snapshot,
        policy,
        empty=True,
        candidate_set_id="EMPTY",
    )
    assert empty_raw.empty_reason == "NO_SOURCE_ROWS"
    assert empty_set.raw_candidate_count == 0


def test_multi_ticker_snapshots_actions_persist_lineage_and_mature(
    tmp_path: Path,
) -> None:
    known_dividend = _event(
        "BBCA-KNOWN-DIV",
        "DIVIDEND",
        SIGNAL.date(),
        cash=1.0,
    )
    bbca_policy = _policy(events=(known_dividend,))
    tlkm_policy = _policy()
    manifest = _manifest(
        bbca_policy,
        tickers=("BBCA", "TLKM"),
    )
    snapshots = {
        "BBCA": _snapshot(manifest, ticker="BBCA"),
        "TLKM": _snapshot(manifest, ticker="TLKM"),
    }
    raw_capture, candidate_set = _raw_and_set(
        manifest,
        snapshots["BBCA"],
        bbca_policy,
        tickers=("BBCA", "TLKM"),
        snapshots=snapshots,
        policies={"BBCA": bbca_policy, "TLKM": tlkm_policy},
    )
    assert raw_capture.candidates[0].snapshot_sha256 != (
        raw_capture.candidates[1].snapshot_sha256
    )
    assert (
        raw_capture.candidates[0].corporate_action_events_at_signal_sha256
        != raw_capture.candidates[1].corporate_action_events_at_signal_sha256
    )

    store = CandidateSetStore(tmp_path)
    store.persist_raw(raw_capture)
    store.persist_manifest(candidate_set)
    assert_opportunity_set_parity(
        candidate_set,
        raw_capture,
        candidate_set,
        raw_capture,
    )

    for index, record in enumerate(raw_capture.candidates, start=1):
        assert isinstance(record, CandidateEvent)
        policy = bbca_policy if record.ticker == "BBCA" else tlkm_policy
        observation = _observation(
            manifest,
            raw_capture,
            candidate_set,
            candidate=record,
            observation_id=f"OBS-{record.ticker}",
        )
        scenario = Scenario(
            manifest=manifest,
            trading_calendar=_calendar(),
            snapshot=snapshots[record.ticker],
            raw_capture=raw_capture,
            candidate_set=candidate_set,
            candidate=record,
            observation=observation,
            authorization=_authorization(
                manifest,
                observation,
                _calendar(),
            ),
        )
        request = _request(
            scenario,
            policy,
            tuple(_bar(day) for day in SESSIONS[1:5]),
        )
        outcome = evaluate_horizon(request)
        assert outcome.status == "MATURE", index
        bundle = build_verified_outcome_lineage(request, outcome)
        assert (
            verify_lineage_bundle(
                bundle,
                manifest,
                snapshots[record.ticker],
                raw_capture,
                candidate_set,
                record,
                observation,
                bar_series=request.bar_series,
                outcome=outcome,
            )
            == bundle
        )


def test_quarantined_record_cannot_be_retained_by_either_view(
    tmp_path: Path,
) -> None:
    policy = _policy()
    manifest = _manifest(policy)
    snapshot = _snapshot(manifest)
    raw_capture, candidate_set = _raw_and_set(
        manifest,
        snapshot,
        policy,
        include_quarantine=True,
    )
    quarantined = raw_capture.candidates[-1]
    assert isinstance(quarantined, QuarantinedCandidateEvent)
    dispositions = list(candidate_set.control_view.dispositions)
    dispositions[-1] = CandidateDisposition(
        raw_event_id=quarantined.raw_event_id,
        state="RETAINED",
        reason_codes=(),
    )
    bad_view = CandidateSetView(
        side="CONTROL",
        input_event_ids=candidate_set.control_view.input_event_ids,
        dispositions=tuple(dispositions),
        view_sha256=canonical_view_sha256(
            "CONTROL",
            candidate_set.control_view.input_event_ids,
            dispositions,
        ),
    )
    bad_set = CandidateSetManifest.model_validate(
        {
            **candidate_set.model_dump(mode="python"),
            "control_view": bad_view,
        }
    )
    store = CandidateSetStore(tmp_path)
    store.persist_raw(raw_capture)
    with pytest.raises(ShadowContractError):
        store.persist_manifest(bad_set)


def test_empty_capture_reason_is_part_of_opportunity_parity() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    snapshot = _snapshot(manifest)
    left_raw, left_set = _raw_and_set(
        manifest,
        snapshot,
        policy,
        empty=True,
        candidate_set_id="EMPTY-SOURCE",
        empty_reason="NO_SOURCE_ROWS",
    )
    right_raw, right_set = _raw_and_set(
        manifest,
        snapshot,
        policy,
        empty=True,
        candidate_set_id="EMPTY-FAILED",
        empty_reason="SOURCE_FETCH_FAILED",
    )
    assert left_raw.opportunity_set_sha256 != right_raw.opportunity_set_sha256
    assert (
        left_raw.raw_candidate_set_sha256
        != right_raw.raw_candidate_set_sha256
    )
    with pytest.raises(ShadowContractError):
        assert_opportunity_set_parity(
            left_set,
            left_raw,
            right_set,
            right_raw,
        )


def test_candidate_store_requires_raw_first_and_rejects_tamper(
    tmp_path: Path,
) -> None:
    scenario = _scenario()
    store = CandidateSetStore(tmp_path)
    with pytest.raises(ShadowContractError):
        store.persist_manifest(scenario.candidate_set)
    raw_path = store.persist_raw(scenario.raw_capture)
    manifest_path = store.persist_manifest(scenario.candidate_set)
    assert store.persist_raw(scenario.raw_capture) == raw_path
    assert store.persist_manifest(scenario.candidate_set) == manifest_path

    raw_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ShadowContractError):
        store.load_raw(
            scenario.raw_capture.raw_capture_id,
            protocol_id=scenario.manifest.protocol_id,
            manifest_sha256=scenario.observation.manifest_sha256,
        )


def test_store_revalidates_model_copy_before_any_write(tmp_path: Path) -> None:
    scenario = _scenario()
    candidate = scenario.candidate.model_copy(
        update={"raw_payload_json": canonical_payload_json({"Ticker": "TLKM"})}
    )
    tampered = scenario.raw_capture.model_copy(
        update={"candidates": (candidate,)}
    )
    with pytest.raises(ShadowContractError):
        CandidateSetStore(tmp_path).persist_raw(tampered)
    assert not tuple(tmp_path.rglob("*.json"))


def test_disposition_order_and_exact_opportunity_parity() -> None:
    scenario = _scenario()
    ids = scenario.candidate_set.control_view.input_event_ids
    dispositions = tuple(reversed(scenario.candidate_set.control_view.dispositions))
    if len(ids) == 1:
        ids = (ids[0], f"{ids[0]}-OTHER")
        dispositions = (
            CandidateDisposition(raw_event_id=ids[1], state="RETAINED", reason_codes=()),
            CandidateDisposition(raw_event_id=ids[0], state="RETAINED", reason_codes=()),
        )
    with pytest.raises(ValidationError):
        CandidateSetView(
            side="CONTROL",
            input_event_ids=ids,
            dispositions=dispositions,
            view_sha256=canonical_view_sha256("CONTROL", ids, dispositions),
        )

    right = CandidateSetManifest.model_validate(
        {
            **scenario.candidate_set.model_dump(mode="python"),
            "candidate_set_id": "SET-2",
        }
    )
    parity = assert_opportunity_set_parity(
        scenario.candidate_set,
        scenario.raw_capture,
        right,
        scenario.raw_capture,
    )
    assert parity.exact_match is True
    assert (
        verify_opportunity_set_parity(
            parity,
            scenario.candidate_set,
            scenario.raw_capture,
            right,
            scenario.raw_capture,
        )
        == parity
    )
    with pytest.raises(ShadowContractError):
        verify_opportunity_set_parity(
            parity.model_copy(update={"event_count": parity.event_count + 1}),
            scenario.candidate_set,
            scenario.raw_capture,
            right,
            scenario.raw_capture,
        )

    policy = _policy()
    manifest = _manifest(policy)
    snapshot = _snapshot(manifest)
    other_raw, other_set = _raw_and_set(
        manifest,
        snapshot,
        policy,
        tickers=("TLKM",),
        candidate_set_id="SET-OTHER",
    )
    with pytest.raises(ShadowContractError):
        assert_opportunity_set_parity(
            scenario.candidate_set,
            scenario.raw_capture,
            other_set,
            other_raw,
        )


def test_request_binds_approved_manifest_policy_calendar_and_sources() -> None:
    policy = _policy()
    draft = _scenario(policy, approved=False)
    bars = (_bar(SESSIONS[1]),)
    with pytest.raises(ValidationError):
        _request(draft, policy, bars)

    scenario = _scenario(policy)
    valid = _request(scenario, policy, bars)
    other_calendar = _calendar(_weekday_sessions(SIGNAL.date(), 33))
    with pytest.raises(ValidationError):
        MaturationRequest.model_validate(
            {
                **valid.model_dump(mode="python"),
                "trading_calendar": other_calendar,
            }
        )
    tampered_policy = valid.execution_policy.model_copy(
        update={"entry_validity_trading_days": 2}
    )
    with pytest.raises(ValidationError):
        MaturationRequest.model_validate(
            {
                **valid.model_dump(mode="python"),
                "execution_policy": tampered_policy,
            }
        )


def test_source_ids_bind_to_exact_manifest_definitions() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    request = _request(scenario, policy, (_bar(SESSIONS[1]),))
    series = request.bar_series
    wrong_source_id = "SNAPSHOT"
    wrong_source_hash = canonical_outcome_source_record_sha256(
        source_id=wrong_source_id,
        source_definition_sha256=series.source_definition_sha256,
        source_as_of=series.source_as_of,
        requested_start=series.requested_start,
        requested_end=series.requested_end,
        ticker=series.ticker,
        snapshot_sha256=series.snapshot_sha256,
        bars_sha256=series.bars_sha256,
        corporate_action_policy_sha256=(
            series.corporate_action_policy.policy_sha256
        ),
        corporate_action_events_sha256=(
            series.corporate_action_policy.events_sha256
        ),
        previous_source_sha256=series.previous_source_sha256,
    )
    wrong_series = FrozenBarSeries.model_validate(
        {
            **series.model_dump(mode="python"),
            "source_id": wrong_source_id,
            "source_sha256": wrong_source_hash,
        }
    )
    with pytest.raises(ValidationError):
        MaturationRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "bar_series": wrong_series,
            }
        )

    wrong_action = _event(
        "WRONG-SOURCE",
        "SPLIT",
        SESSIONS[2],
        price_factor=0.5,
        quantity_factor=2.0,
        source_id="SNAPSHOT",
        definition_source_id="CORPORATE_ACTIONS",
    )
    wrong_policy = _policy(events=(wrong_action,))
    wrong_scenario = _scenario(wrong_policy)
    with pytest.raises(ValidationError):
        _request(
            wrong_scenario,
            wrong_policy,
            (_bar(SESSIONS[1]),),
        )

    wrong_gate = GateMeasurement(
        **{
            **_gate().model_dump(mode="python"),
            "source_id": "SNAPSHOT",
        }
    )
    wrong_control = _decision("CONTROL", gate=wrong_gate)
    wrong_observation = _observation(
        scenario.manifest,
        scenario.raw_capture,
        scenario.candidate_set,
        control_decision=wrong_control,
    )
    wrong_gate_scenario = Scenario(
        manifest=scenario.manifest,
        trading_calendar=scenario.trading_calendar,
        snapshot=scenario.snapshot,
        raw_capture=scenario.raw_capture,
        candidate_set=scenario.candidate_set,
        candidate=scenario.candidate,
        observation=wrong_observation,
        authorization=_authorization(
            scenario.manifest,
            wrong_observation,
            scenario.trading_calendar,
        ),
    )
    with pytest.raises(ValidationError):
        _request(
            wrong_gate_scenario,
            policy,
            (_bar(SESSIONS[1]),),
        )


def test_snapshot_gate_expiry_and_capture_chronology_are_half_open() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    with pytest.raises(ValidationError):
        _snapshot(
            manifest,
            source_expires_at=SIGNAL - timedelta(minutes=30),
        )

    expired_snapshot = _snapshot(
        manifest,
        source_expires_at=SIGNAL,
    )
    raw_capture, candidate_set = _raw_and_set(
        manifest,
        expired_snapshot,
        policy,
    )
    candidate = next(
        item for item in raw_capture.candidates if isinstance(item, CandidateEvent)
    )
    observation = _observation(manifest, raw_capture, candidate_set)
    expired_scenario = Scenario(
        manifest=manifest,
        trading_calendar=_calendar(),
        snapshot=expired_snapshot,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        candidate=candidate,
        observation=observation,
        authorization=_authorization(manifest, observation, _calendar()),
    )
    with pytest.raises(ValidationError):
        _request(
            expired_scenario,
            policy,
            (_bar(SESSIONS[1]),),
        )
    with pytest.raises(ShadowContractError):
        build_lineage_bundle(
            manifest,
            expired_snapshot,
            raw_capture,
            candidate_set,
            candidate,
            observation,
        )

    valid = _scenario(policy)
    stale_control = _decision(
        "CONTROL",
        gate=_gate(expires_at=SIGNAL),
    )
    stale_observation = _observation(
        valid.manifest,
        valid.raw_capture,
        valid.candidate_set,
        control_decision=stale_control,
    )
    stale_scenario = Scenario(
        manifest=valid.manifest,
        trading_calendar=valid.trading_calendar,
        snapshot=valid.snapshot,
        raw_capture=valid.raw_capture,
        candidate_set=valid.candidate_set,
        candidate=valid.candidate,
        observation=stale_observation,
        authorization=_authorization(
            valid.manifest,
            stale_observation,
            valid.trading_calendar,
        ),
    )
    with pytest.raises(ValidationError):
        _request(stale_scenario, policy, (_bar(SESSIONS[1]),))

    early_observation = ShadowObservation.model_validate(
        {
            **valid.observation.model_dump(mode="python"),
            "captured_at": SIGNAL + timedelta(minutes=2),
        }
    )
    early_scenario = Scenario(
        manifest=valid.manifest,
        trading_calendar=valid.trading_calendar,
        snapshot=valid.snapshot,
        raw_capture=valid.raw_capture,
        candidate_set=valid.candidate_set,
        candidate=valid.candidate,
        observation=early_observation,
        authorization=_authorization(
            valid.manifest,
            early_observation,
            valid.trading_calendar,
        ),
    )
    with pytest.raises(ValidationError):
        _request(early_scenario, policy, (_bar(SESSIONS[1]),))
    with pytest.raises(ShadowContractError):
        build_lineage_bundle(
            valid.manifest,
            valid.snapshot,
            valid.raw_capture,
            valid.candidate_set,
            valid.candidate,
            early_observation,
        )


def test_signal_bar_excluded_and_horizons_mature_independently() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    bars = (
        _bar(SIGNAL.date(), high=130.0, low=80.0),
        _bar(SESSIONS[1]),
        _bar(SESSIONS[2]),
        _bar(SESSIONS[3]),
        _bar(SESSIONS[4]),
    )
    outcomes = evaluate_all_horizons(_request(scenario, policy, bars))
    assert [item.horizon_trading_days for item in outcomes] == [3, 5, 10, 15]
    assert outcomes[0].status == "MATURE"
    assert outcomes[0].terminal_event == "TIMEOUT"
    assert all(item.status == "PENDING" for item in outcomes[1:])
    assert outcomes[-1].primary_horizon is True


def test_outcome_consumer_reloads_and_rejects_stale_active_authorization() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    request = _request(
        scenario,
        policy,
        tuple(_bar(day) for day in SESSIONS[1:5]),
    )
    assert request.authorization.approval_ledger.closure_event is None
    closed_authorization = _authorization_closed_for_all_maturation(
        request.authorization,
        effective_at=request.observation.captured_at + timedelta(minutes=1),
    )
    assert (
        closed_authorization.closure is not None
        and closed_authorization.closure.maturation_policy
        == "BLOCK_ALL_MATURATION"
    )

    class RecordingLoader:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def load_authorization(
            self,
            *,
            protocol_id: str,
            manifest_canonical_sha256: str,
            ledger_id: str,
        ) -> ProtocolAuthorizationBundle:
            self.calls.append(
                (protocol_id, manifest_canonical_sha256, ledger_id)
            )
            return closed_authorization

    loader = RecordingLoader()
    with pytest.raises(
        ShadowContractError,
        match="current maturation authorization rejected the request",
    ) as exc_info:
        _evaluate_horizon(request, authorization_loader=loader)

    assert loader.calls == [
        (
            request.manifest.protocol_id,
            canonical_sha256(request.manifest),
            request.authorization.approval_ledger.ledger_id,
        )
    ]
    assert exc_info.value.__cause__ is not None
    assert "protocol closure blocks all maturation" in str(
        exc_info.value.__cause__
    )


def test_outcome_consumer_fails_closed_when_authorization_reload_unavailable() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    request = _request(
        scenario,
        policy,
        tuple(_bar(day) for day in SESSIONS[1:5]),
    )

    class UnavailableLoader:
        def __init__(self) -> None:
            self.calls = 0

        def load_authorization(
            self,
            *,
            protocol_id: str,
            manifest_canonical_sha256: str,
            ledger_id: str,
        ) -> ProtocolAuthorizationBundle:
            del protocol_id, manifest_canonical_sha256, ledger_id
            self.calls += 1
            raise OSError("simulated approval-ledger outage")

    loader = UnavailableLoader()
    with pytest.raises(
        ShadowContractError,
        match="current maturation authorization is unavailable",
    ):
        _evaluate_horizon(request, authorization_loader=loader)
    assert loader.calls == 1


def test_late_fill_receives_full_post_fill_horizon() -> None:
    policy = _policy()
    scenario = _scenario(policy, validity=3)
    no_touch = {
        "open_price": 110.0,
        "high": 112.0,
        "low": 106.0,
        "close": 110.0,
    }
    prefix = (
        _bar(SESSIONS[1], **no_touch),
        _bar(SESSIONS[2], **no_touch),
        _bar(SESSIONS[3]),
    )
    fourteen = prefix + tuple(_bar(day) for day in SESSIONS[4:18])
    pending = evaluate_horizon(
        _request(scenario, policy, fourteen, horizon=15)
    )
    assert pending.status == "PENDING"
    assert pending.fill_status == "FILLED"

    fifteen = prefix + tuple(_bar(day) for day in SESSIONS[4:19])
    mature = evaluate_horizon(
        _request(scenario, policy, fifteen, horizon=15)
    )
    assert mature.status == "MATURE"
    assert mature.terminal_event == "TIMEOUT"
    assert mature.maturity_at.date() == SESSIONS[18]


def test_mid_session_cutoff_without_current_bar_remains_pending() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    cutoff = datetime.combine(SESSIONS[1], time(12, 0), tzinfo=IDX)
    outcome = evaluate_horizon(
        _request(
            scenario,
            policy,
            (),
            cutoff=cutoff,
            source_as_of=datetime.combine(
                SIGNAL.date(),
                time(17, 0),
                tzinfo=IDX,
            ),
        )
    )
    assert outcome.status == "PENDING"
    assert outcome.reason_codes == ("PENDING_MATURITY",)


def test_terminal_calendar_must_support_validity_plus_primary_horizon() -> None:
    policy = _policy()
    short_sessions = _weekday_sessions(SIGNAL.date(), 10)
    scenario = _scenario(policy, sessions=short_sessions)
    with pytest.raises(ValidationError):
        _request(scenario, policy, (_bar(short_sessions[1]),))


def test_order_expiry_is_shared_by_every_horizon() -> None:
    policy = _policy()
    scenario = _scenario(policy, validity=2)
    bars = (
        _bar(SESSIONS[1], open_price=110.0, high=112.0, low=106.0, close=110.0),
        _bar(SESSIONS[2], open_price=110.0, high=112.0, low=106.0, close=110.0),
    )
    outcomes = evaluate_all_horizons(_request(scenario, policy, bars))
    assert {item.terminal_event for item in outcomes} == {"UNFILLED"}
    assert {item.maturity_at for item in outcomes} == {
        datetime.combine(SESSIONS[2], time(16, 0), tzinfo=IDX)
    }


def test_target_and_stop_gaps_use_observed_open() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    target = evaluate_horizon(
        _request(
            scenario,
            policy,
            (
                _bar(SESSIONS[1]),
                _bar(
                    SESSIONS[2],
                    open_price=125.0,
                    high=126.0,
                    low=124.0,
                    close=125.0,
                ),
            ),
        )
    )
    assert target.terminal_event == "TARGET_FIRST"
    assert target.exit_price == 125.0
    assert target.closed_at.time() == time(9, 0)

    stop = evaluate_horizon(
        _request(
            scenario,
            policy,
            (
                _bar(SESSIONS[1]),
                _bar(
                    SESSIONS[2],
                    open_price=85.0,
                    high=87.0,
                    low=80.0,
                    close=86.0,
                ),
            ),
        )
    )
    assert stop.terminal_event == "STOP_FIRST"
    assert stop.exit_price == 85.0
    assert stop.closed_at.time() == time(9, 0)


def test_target_open_gap_precedes_later_intraday_stop() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    outcome = evaluate_horizon(
        _request(
            scenario,
            policy,
            (
                _bar(SESSIONS[1]),
                _bar(
                    SESSIONS[2],
                    open_price=125.0,
                    high=126.0,
                    low=85.0,
                    close=100.0,
                ),
            ),
        )
    )
    assert outcome.terminal_event == "TARGET_FIRST"
    assert outcome.exit_price == 125.0
    assert outcome.reason_codes == ("TARGET_GAP",)
    assert outcome.closed_at.time() == time(9, 0)


def test_entry_gap_through_stop_is_realized_not_censored() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    outcome = evaluate_horizon(
        _request(
            scenario,
            policy,
            (
                _bar(
                    SESSIONS[1],
                    open_price=85.0,
                    high=87.0,
                    low=80.0,
                    close=86.0,
                ),
            ),
        )
    )
    assert outcome.status == "MATURE"
    assert outcome.fill_status == "FILLED"
    assert outcome.terminal_event == "STOP_FIRST"
    assert outcome.fill_price == outcome.exit_price == 85.0
    assert outcome.net_return < 0.0
    assert outcome.reason_codes == ("ENTRY_GAP_THROUGH_STOP_GAP",)
    assert outcome.closed_at == outcome.filled_at


def test_early_terminal_does_not_require_post_exit_bars() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    late_cutoff = datetime.combine(SESSIONS[4], time(17, 0), tzinfo=IDX)
    outcome = evaluate_horizon(
        _request(
            scenario,
            policy,
            (
                _bar(SESSIONS[1]),
                _bar(
                    SESSIONS[2],
                    open_price=125.0,
                    high=126.0,
                    low=124.0,
                    close=125.0,
                ),
            ),
            cutoff=late_cutoff,
            source_as_of=late_cutoff,
        )
    )
    assert outcome.status == "MATURE"
    assert outcome.terminal_event == "TARGET_FIRST"


def test_evaluator_revalidates_model_copy_at_trust_boundary() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    request = _request(scenario, policy, (_bar(SESSIONS[1]),))
    tampered_bar = request.bar_series.bars[0].model_copy(
        update={"high": 130.0}
    )
    tampered_series = request.bar_series.model_copy(
        update={"bars": (tampered_bar,)}
    )
    tampered_request = request.model_copy(
        update={"bar_series": tampered_series}
    )
    with pytest.raises(ValidationError):
        evaluate_horizon(tampered_request)


def test_same_bar_and_intraday_sequence_are_conservative() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    same_bar = evaluate_horizon(
        _request(
            scenario,
            policy,
            (_bar(SESSIONS[1], high=125.0, low=85.0),),
        )
    )
    assert same_bar.terminal_event == "STOP_FIRST"
    assert same_bar.same_bar_ambiguous is True

    intraday_then_next_stop = evaluate_horizon(
        _request(
            scenario,
            policy,
            (
                _bar(
                    SESSIONS[1],
                    open_price=110.0,
                    high=125.0,
                    low=100.0,
                    close=110.0,
                ),
                _bar(
                    SESSIONS[2],
                    open_price=105.0,
                    high=110.0,
                    low=89.0,
                    close=95.0,
                ),
            ),
        )
    )
    assert intraday_then_next_stop.terminal_event == "STOP_FIRST"
    assert intraday_then_next_stop.reason_codes == (
        "STOP_TOUCH",
        "INTRADAY_ENTRY_TARGET_UNPROVEN",
    )
    assert intraday_then_next_stop.same_bar_ambiguous is True
    assert (
        intraday_then_next_stop.ambiguity_resolution
        == "INTRADAY_ENTRY_TARGET_UNPROVEN_NOT_CREDITED"
    )
    assert intraday_then_next_stop.fill_time_precision == "SESSION_ONLY"

    pending = evaluate_horizon(
        _request(
            scenario,
            policy,
            (
                _bar(
                    SESSIONS[1],
                    open_price=110.0,
                    high=125.0,
                    low=100.0,
                    close=110.0,
                ),
            ),
        )
    )
    assert pending.status == "PENDING"
    assert pending.same_bar_ambiguous is True
    assert "INTRADAY_ENTRY_TARGET_UNPROVEN" in pending.reason_codes


def test_split_rescales_geometry_while_rights_fail_closed() -> None:
    split = _event(
        "SPLIT-1",
        "SPLIT",
        SESSIONS[1],
        price_factor=0.5,
        quantity_factor=2.0,
    )
    split_policy = _policy(events=(split,))
    split_scenario = _scenario(split_policy)
    split_outcome = evaluate_horizon(
        _request(
            split_scenario,
            split_policy,
            (
                _bar(
                    SESSIONS[1],
                    open_price=51.0,
                    high=55.0,
                    low=50.0,
                    close=52.0,
                ),
                _bar(
                    SESSIONS[2],
                    open_price=61.0,
                    high=62.0,
                    low=60.0,
                    close=61.0,
                ),
            ),
        )
    )
    assert split_outcome.terminal_event == "TARGET_FIRST"
    assert split_outcome.fill_price == 51.0
    assert split_outcome.exit_price == 61.0
    assert split_outcome.corporate_action_adjustment is not None

    rights = _event(
        "RIGHTS-1",
        "RIGHTS",
        SESSIONS[2],
        price_factor=0.8,
        quantity_factor=1.25,
        capital_call=20.0,
    )
    rights_policy = _policy(events=(rights,))
    rights_scenario = _scenario(rights_policy)
    rights_outcome = evaluate_horizon(
        _request(
            rights_scenario,
            rights_policy,
            (
                _bar(SESSIONS[1]),
                _bar(
                    SESSIONS[2],
                    open_price=82.0,
                    high=90.0,
                    low=80.0,
                    close=85.0,
                ),
                _bar(
                    SESSIONS[3],
                    open_price=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.0,
                ),
            ),
        )
    )
    assert rights_outcome.status == "INVALID"
    assert rights_outcome.fill_status == "FILLED"
    assert rights_outcome.reason_codes == ("RIGHTS_POLICY_UNSUPPORTED",)


def test_notional_cost_cash_scales_with_exit_and_split_quantity() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    target = evaluate_horizon(
        _request(
            scenario,
            policy,
            (
                _bar(SESSIONS[1]),
                _bar(
                    SESSIONS[2],
                    open_price=125.0,
                    high=126.0,
                    low=124.0,
                    close=125.0,
                ),
            ),
        )
    )
    assert target.position_quantity_at_exit == 100.0
    assert target.invested_capital == 10_200.0
    assert target.entry_cost_cash == pytest.approx(25.5)
    assert target.exit_cost_cash == pytest.approx(56.25)
    assert target.total_cost_cash == pytest.approx(81.75)
    assert target.total_cost_fraction == pytest.approx(81.75 / 10_200.0)

    split = _event(
        "POST-FILL-SPLIT",
        "SPLIT",
        SESSIONS[2],
        price_factor=0.5,
        quantity_factor=2.0,
    )
    split_policy = _policy(events=(split,))
    split_scenario = _scenario(split_policy)
    split_outcome = evaluate_horizon(
        _request(
            split_scenario,
            split_policy,
            (
                _bar(SESSIONS[1]),
                _bar(
                    SESSIONS[2],
                    open_price=61.0,
                    high=62.0,
                    low=60.0,
                    close=61.0,
                ),
            ),
        )
    )
    assert split_outcome.position_quantity_at_exit == 200.0
    assert split_outcome.exit_position_value == 12_200.0
    assert split_outcome.exit_cost_cash == pytest.approx(54.9)
    assert split_outcome.risk_capital_basis == pytest.approx(1_200.0)


def test_control_and_challenger_geometry_adjust_independently() -> None:
    split = _event(
        "SIDE-SPLIT",
        "SPLIT",
        SESSIONS[2],
        price_factor=0.5,
        quantity_factor=2.0,
    )
    policy = _policy(events=(split,))
    base = _scenario(policy)
    challenger_geometry = RecordedTradeGeometry(
        entry_low=98.0,
        entry_high=103.0,
        target_price=118.0,
        stop_loss=88.0,
        risk_reward_ratio=2.0,
        required_risk_reward=2.0,
    )
    observation = _observation(
        base.manifest,
        base.raw_capture,
        base.candidate_set,
        control_decision=_decision("CONTROL"),
        challenger_decision=_decision(
            "CHALLENGER",
            geometry=challenger_geometry,
        ),
    )
    scenario = Scenario(
        manifest=base.manifest,
        trading_calendar=base.trading_calendar,
        snapshot=base.snapshot,
        raw_capture=base.raw_capture,
        candidate_set=base.candidate_set,
        candidate=base.candidate,
        observation=observation,
        authorization=_authorization(
            base.manifest,
            observation,
            base.trading_calendar,
        ),
    )
    bars = (
        _bar(SESSIONS[1]),
        _bar(
            SESSIONS[2],
            open_price=61.0,
            high=62.0,
            low=60.0,
            close=61.0,
        ),
    )
    control = evaluate_horizon(_request(scenario, policy, bars))
    challenger = evaluate_horizon(
        _request(
            scenario,
            policy,
            bars,
            side="CHALLENGER",
        )
    )
    assert control.status == challenger.status == "MATURE"
    assert control.terminal_event == challenger.terminal_event == "TARGET_FIRST"
    assert (
        control.corporate_action_adjustment
        != challenger.corporate_action_adjustment
    )


def test_future_known_actions_non_session_actions_and_rights_scope() -> None:
    announced_split = _event(
        "ANNOUNCED-SPLIT",
        "SPLIT",
        SESSIONS[2],
        price_factor=0.5,
        quantity_factor=2.0,
        published_at=SIGNAL,
    )
    split_policy = _policy(events=(announced_split,))
    split_scenario = _scenario(split_policy)
    assert (
        split_scenario.candidate.corporate_action_events_at_signal_sha256
        == canonical_corporate_action_events_sha256((announced_split,))
    )
    before_effective = evaluate_horizon(
        _request(
            split_scenario,
            split_policy,
            (_bar(SESSIONS[1]),),
        )
    )
    assert before_effective.status == "PENDING"

    weekend = SIGNAL.date() + timedelta(days=8)
    assert weekend.weekday() == 5
    weekend_split = _event(
        "WEEKEND-SPLIT",
        "SPLIT",
        weekend,
        price_factor=0.5,
        quantity_factor=2.0,
    )
    weekend_policy = _policy(events=(weekend_split,))
    weekend_scenario = _scenario(weekend_policy)
    with pytest.raises(ValidationError):
        _request(
            weekend_scenario,
            weekend_policy,
            (_bar(SESSIONS[1]),),
        )

    rights_before_fill = _event(
        "RIGHTS-BEFORE",
        "RIGHTS",
        SESSIONS[1],
        price_factor=0.8,
        quantity_factor=1.25,
        capital_call=20.0,
    )
    before_policy = _policy(events=(rights_before_fill,))
    before_scenario = _scenario(before_policy)
    invalid_before = evaluate_horizon(
        _request(
            before_scenario,
            before_policy,
            (_bar(SESSIONS[1]),),
        )
    )
    assert invalid_before.fill_status == "INVALID"
    assert invalid_before.reason_codes == ("RIGHTS_POLICY_UNSUPPORTED",)

    rights_after_horizon = _event(
        "RIGHTS-AFTER",
        "RIGHTS",
        SESSIONS[5],
        price_factor=0.8,
        quantity_factor=1.25,
        capital_call=20.0,
    )
    after_policy = _policy(events=(rights_after_horizon,))
    after_scenario = _scenario(after_policy)
    mature_before_rights = evaluate_horizon(
        _request(
            after_scenario,
            after_policy,
            tuple(_bar(day) for day in SESSIONS[1:5]),
            horizon=3,
        )
    )
    assert mature_before_rights.status == "MATURE"
    assert mature_before_rights.terminal_event == "TIMEOUT"


def test_dividend_entitlement_and_double_count_protection() -> None:
    dividend = _event(
        "DIV-1",
        "DIVIDEND",
        SESSIONS[2],
        cash=2.0,
    )
    policy = _policy(convention="TOTAL_RETURN", events=(dividend,))
    scenario = _scenario(policy)
    outcome = evaluate_horizon(
        _request(
            scenario,
            policy,
            (
                _bar(SESSIONS[1], close=102.0),
                _bar(SESSIONS[2], close=102.0, dividend=2.0),
                _bar(SESSIONS[3], close=102.0),
                _bar(SESSIONS[4], close=102.0),
            ),
        )
    )
    assert outcome.terminal_event == "TIMEOUT"
    assert outcome.dividend_return == pytest.approx(2.0 / 102.0)

    ex_date = _event(
        "DIV-EX",
        "DIVIDEND",
        SESSIONS[1],
        cash=2.0,
    )
    ex_policy = _policy(convention="TOTAL_RETURN", events=(ex_date,))
    ex_scenario = _scenario(ex_policy)
    ex_outcome = evaluate_horizon(
        _request(
            ex_scenario,
            ex_policy,
            (
                _bar(SESSIONS[1], close=102.0, dividend=2.0),
                _bar(SESSIONS[2], close=102.0),
                _bar(SESSIONS[3], close=102.0),
                _bar(SESSIONS[4], close=102.0),
            ),
        )
    )
    assert ex_outcome.dividend_return == 0.0

    with pytest.raises(ValidationError):
        _policy(
            convention="TOTAL_RETURN",
            dividends_are_in_prices=True,
        )


def test_corporate_action_leakage_ticker_order_and_completeness_fail_closed() -> None:
    leaked = _event(
        "LATE",
        "SPLIT",
        SESSIONS[1],
        price_factor=0.5,
        quantity_factor=2.0,
        published_at=datetime.combine(
            SESSIONS[2],
            time(9, 0),
            tzinfo=IDX,
        ),
    )
    leaked_policy = _policy(events=(leaked,))
    leaked_scenario = _scenario(leaked_policy)
    with pytest.raises(ValidationError):
        _request(
            leaked_scenario,
            leaked_policy,
            (_bar(SESSIONS[1], open_price=51.0, high=55.0, low=50.0, close=52.0),),
        )

    wrong_ticker = _event(
        "WRONG",
        "SPLIT",
        SESSIONS[1],
        ticker="TLKM",
        price_factor=0.5,
        quantity_factor=2.0,
    )
    wrong_policy = _policy(events=(wrong_ticker,))
    wrong_scenario = _scenario(wrong_policy)
    with pytest.raises(ValidationError):
        _bar_series(
            wrong_scenario,
            wrong_policy,
            (_bar(SESSIONS[1]),),
        )

    first = _event("DIV-2", "DIVIDEND", SESSIONS[2], cash=1.0)
    second = _event("DIV-1", "DIVIDEND", SESSIONS[1], cash=1.0)
    with pytest.raises(ValidationError):
        _policy(events=(first, second))

    with pytest.raises(ValidationError):
        _event(
            "INCOMPLETE",
            "RIGHTS",
            SESSIONS[2],
            price_factor=0.8,
            quantity_factor=1.25,
            capital_call=20.0,
            terms_complete=False,
        )

    same_session_first = _event(
        "A-SPLIT",
        "SPLIT",
        SESSIONS[2],
        price_factor=0.5,
        quantity_factor=2.0,
    )
    same_session_second = _event(
        "B-SPLIT",
        "SPLIT",
        SESSIONS[2],
        price_factor=0.5,
        quantity_factor=2.0,
    )
    with pytest.raises(ValidationError):
        _policy(events=(same_session_first, same_session_second))


def test_vintage_aware_backfill_updates_pending_and_matures() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    v1_request = _request(
        scenario,
        policy,
        (_bar(SESSIONS[1]), _bar(SESSIONS[2])),
    )
    pending_v1 = evaluate_horizon(v1_request)
    ledger, status = OutcomeLedger().backfill(
        pending_v1,
        request=v1_request,
        authorization_loader=_authorization_loader(v1_request),
    )
    assert status == "INSERTED"

    v2_request = _request(
        scenario,
        policy,
        (
            _bar(SESSIONS[1]),
            _bar(SESSIONS[2]),
            _bar(SESSIONS[3]),
        ),
        previous_source_sha256=v1_request.bar_series.source_sha256,
    )
    pending_v2 = evaluate_horizon(v2_request)
    ledger, status = ledger.backfill(
        pending_v2,
        request=v2_request,
        authorization_loader=_authorization_loader(v2_request),
    )
    assert status == "UPDATED_PENDING"

    v3_request = _request(
        scenario,
        policy,
        (
            _bar(SESSIONS[1]),
            _bar(SESSIONS[2]),
            _bar(SESSIONS[3]),
            _bar(SESSIONS[4]),
        ),
        previous_source_sha256=v2_request.bar_series.source_sha256,
    )
    mature = evaluate_horizon(v3_request)
    ledger, status = ledger.backfill(
        mature,
        request=v3_request,
        authorization_loader=_authorization_loader(v3_request),
    )
    assert status == "UPDATED_TO_MATURE"
    assert ledger.records[0].status == "MATURE"
    assert ledger.backfill(
        mature,
        request=v3_request,
        authorization_loader=_authorization_loader(v3_request),
    )[1] == "UNCHANGED"


def test_pending_fill_can_transition_to_invalid_with_fill_evidence() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    v1_request = _request(
        scenario,
        policy,
        (_bar(SESSIONS[1]),),
    )
    pending = evaluate_horizon(v1_request)
    assert pending.status == "PENDING"
    assert pending.fill_status == "FILLED"
    ledger, _ = OutcomeLedger().backfill(
        pending,
        request=v1_request,
        authorization_loader=_authorization_loader(v1_request),
    )

    v2_request = _request(
        scenario,
        policy,
        (
            _bar(SESSIONS[1]),
            _bar(SESSIONS[3]),
        ),
        previous_source_sha256=v1_request.bar_series.source_sha256,
    )
    invalid = evaluate_horizon(v2_request)
    assert invalid.status == "INVALID"
    assert invalid.fill_status == "FILLED"
    assert invalid.fill_price == pending.fill_price
    assert invalid.filled_at == pending.filled_at
    ledger, status = ledger.backfill(
        invalid,
        request=v2_request,
        authorization_loader=_authorization_loader(v2_request),
    )
    assert status == "UPDATED_TO_INVALID"
    assert ledger.records[0].fill_price == pending.fill_price


def test_late_announced_earlier_effective_action_extends_vintage() -> None:
    later_effective = _event(
        "KNOWN-LATER",
        "SPLIT",
        SESSIONS[5],
        price_factor=0.5,
        quantity_factor=2.0,
        published_at=SIGNAL,
    )
    initial_policy = _policy(events=(later_effective,))
    scenario = _scenario(initial_policy)
    v1_request = _request(
        scenario,
        initial_policy,
        (_bar(SESSIONS[1]), _bar(SESSIONS[2])),
    )
    pending_v1 = evaluate_horizon(v1_request)
    ledger, _ = OutcomeLedger().backfill(
        pending_v1,
        request=v1_request,
        authorization_loader=_authorization_loader(v1_request),
    )

    newly_announced = _event(
        "NEW-EARLIER",
        "SPLIT",
        SESSIONS[4],
        price_factor=0.5,
        quantity_factor=2.0,
        published_at=datetime.combine(
            SESSIONS[3],
            time(9, 0),
            tzinfo=IDX,
        ),
    )
    extended_policy = _policy(events=(newly_announced, later_effective))
    v2_request = _request(
        scenario,
        extended_policy,
        (
            _bar(SESSIONS[1]),
            _bar(SESSIONS[2]),
            _bar(SESSIONS[3]),
        ),
        previous_source_sha256=v1_request.bar_series.source_sha256,
    )
    pending_v2 = evaluate_horizon(v2_request)
    ledger, status = ledger.backfill(
        pending_v2,
        request=v2_request,
        authorization_loader=_authorization_loader(v2_request),
    )
    assert status == "UPDATED_PENDING"
    assert set(ledger.records[0].corporate_action_event_ids) == {
        "KNOWN-LATER",
        "NEW-EARLIER",
    }


def test_backfill_rejects_revised_prefix_terminal_overwrite_and_forged_math() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    v1_request = _request(
        scenario,
        policy,
        (_bar(SESSIONS[1]), _bar(SESSIONS[2])),
    )
    pending = evaluate_horizon(v1_request)
    ledger, _ = OutcomeLedger().backfill(
        pending,
        request=v1_request,
        authorization_loader=_authorization_loader(v1_request),
    )

    revised_request = _request(
        scenario,
        policy,
        (
            _bar(SESSIONS[1], close=104.0),
            _bar(SESSIONS[2]),
            _bar(SESSIONS[3]),
            _bar(SESSIONS[4]),
        ),
        previous_source_sha256=v1_request.bar_series.source_sha256,
    )
    with pytest.raises(ShadowContractError):
        ledger.backfill(
            evaluate_horizon(revised_request),
            request=revised_request,
            authorization_loader=_authorization_loader(revised_request),
        )

    invalid_request = _request(
        scenario,
        policy,
        (),
        cutoff=datetime.combine(SESSIONS[1], time(17, 0), tzinfo=IDX),
        source_as_of=datetime.combine(
            SIGNAL.date(),
            time(17, 0),
            tzinfo=IDX,
        ),
    )
    invalid = evaluate_horizon(invalid_request)
    invalid_ledger, _ = OutcomeLedger().backfill(
        invalid,
        request=invalid_request,
        authorization_loader=_authorization_loader(invalid_request),
    )
    changed_invalid = invalid.model_copy(update={"reason_codes": ("OTHER",)})
    with pytest.raises(ShadowContractError):
        invalid_ledger.backfill(
            changed_invalid,
            request=invalid_request,
            authorization_loader=_authorization_loader(invalid_request),
        )

    full_request = _request(
        scenario,
        policy,
        tuple(_bar(day) for day in SESSIONS[1:5]),
    )
    full = evaluate_horizon(full_request)
    forged = full.model_copy(update={"net_return": 99.0})
    with pytest.raises(ValidationError):
        OutcomeLedger().backfill(
            forged,
            request=full_request,
            authorization_loader=_authorization_loader(full_request),
        )

    quantity = float(full.position_quantity_at_exit)
    invested = float(full.invested_capital)
    exit_value = 999.0 * quantity
    capital_return = (exit_value - invested) / invested
    dividend_return = float(full.dividend_return)
    gross_return = capital_return + dividend_return
    entry_cost = float(full.entry_cost_cash)
    exit_cost = exit_value * 0.0045
    total_cost = entry_cost + exit_cost
    cost_fraction = total_cost / invested
    net_return = gross_return - cost_fraction
    coherent_forgery = full.model_copy(
        update={
            "exit_price": 999.0,
            "exit_position_value": exit_value,
            "capital_return": capital_return,
            "gross_return": gross_return,
            "exit_cost_cash": exit_cost,
            "total_cost_cash": total_cost,
            "total_cost_fraction": cost_fraction,
            "net_return": net_return,
            "net_r": net_return / float(full.risk_fraction_at_fill),
        }
    )
    with pytest.raises(ShadowContractError):
        verify_outcome_against_request(full_request, coherent_forgery)
    with pytest.raises(ShadowContractError):
        OutcomeLedger().backfill(
            coherent_forgery,
            request=full_request,
            authorization_loader=_authorization_loader(full_request),
        )
    with pytest.raises(ShadowContractError):
        build_verified_outcome_lineage(
            full_request,
            coherent_forgery,
        )


def test_outcome_ledger_is_side_specific_and_order_deterministic() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    bars = tuple(_bar(day) for day in SESSIONS[1:5])
    control_request = _request(scenario, policy, bars)
    challenger_request = _request(
        scenario,
        policy,
        bars,
        side="CHALLENGER",
    )
    control = evaluate_horizon(control_request)
    challenger = evaluate_horizon(challenger_request)
    left, _ = OutcomeLedger().backfill(
        control,
        request=control_request,
        authorization_loader=_authorization_loader(control_request),
    )
    left, _ = left.backfill(
        challenger,
        request=challenger_request,
        authorization_loader=_authorization_loader(challenger_request),
    )
    right, _ = OutcomeLedger().backfill(
        challenger,
        request=challenger_request,
        authorization_loader=_authorization_loader(challenger_request),
    )
    right, _ = right.backfill(
        control,
        request=control_request,
        authorization_loader=_authorization_loader(control_request),
    )
    assert left.ledger_sha256 == right.ledger_sha256
    assert len(left.records) == 2


def test_full_lineage_recomputes_manifest_snapshot_source_bars_and_outcome() -> None:
    policy = _policy()
    scenario = _scenario(policy)
    request = _request(
        scenario,
        policy,
        tuple(_bar(day) for day in SESSIONS[1:5]),
    )
    outcome = evaluate_horizon(request)
    bundle = build_verified_outcome_lineage(request, outcome)
    assert bundle.lineage_valid is True
    assert bundle.outcome_bars_sha256 == request.bar_series.bars_sha256
    assert bundle.outcome_corporate_action_events_sha256 == policy.events_sha256
    tampered_bundle = bundle.model_copy(
        update={"candidate_sha256": "f" * 64}
    )
    with pytest.raises(ShadowContractError):
        verify_lineage_bundle(
            tampered_bundle,
            scenario.manifest,
            scenario.snapshot,
            scenario.raw_capture,
            scenario.candidate_set,
            scenario.candidate,
            scenario.observation,
            bar_series=request.bar_series,
            outcome=outcome,
        )

    tampered_snapshot = scenario.snapshot.model_copy(
        update={"payload_json": canonical_payload_json({"Ticker": "TLKM"})}
    )
    with pytest.raises(ShadowContractError):
        build_lineage_bundle(
            scenario.manifest,
            tampered_snapshot,
            scenario.raw_capture,
            scenario.candidate_set,
            scenario.candidate,
            scenario.observation,
        )

    forged_signal = outcome.model_copy(
        update={"signal_at": SIGNAL - timedelta(days=1)}
    )
    with pytest.raises(ShadowContractError):
        build_lineage_bundle(
            scenario.manifest,
            scenario.snapshot,
            scenario.raw_capture,
            scenario.candidate_set,
            scenario.candidate,
            scenario.observation,
            bar_series=request.bar_series,
            outcome=forged_signal,
        )


def test_timezone_equivalent_instants_have_identical_ids_and_hashes() -> None:
    signal_utc = SIGNAL.astimezone(timezone.utc)
    outcome_kwargs = {
        "protocol_id": "RS-C1-P2-TEST",
        "manifest_sha256": HASH_A,
        "observation_id": "OBS-TZ",
        "raw_event_id": "EVENT-TZ",
        "ticker": "BBCA",
        "decision_role": "CONTROL",
        "horizon_trading_days": 15,
    }
    assert canonical_outcome_id(
        **outcome_kwargs,
        signal_at=SIGNAL,
    ) == canonical_outcome_id(
        **outcome_kwargs,
        signal_at=signal_utc,
    )

    action_kwargs = {
        "event_id": "TZ-ACTION",
        "ticker": "BBCA",
        "effective_date": SESSIONS[2],
        "kind": "SPLIT",
        "price_factor": 0.5,
        "quantity_factor": 2.0,
        "capital_call_per_pre_event_share": 0.0,
        "cash_per_share": 0.0,
        "source_id": "CORPORATE_ACTIONS",
        "source_definition_sha256": _source_definition_hash(
            "CORPORATE_ACTIONS"
        ),
    }
    assert canonical_corporate_action_source_record_sha256(
        **action_kwargs,
        published_at=SIGNAL,
    ) == canonical_corporate_action_source_record_sha256(
        **action_kwargs,
        published_at=signal_utc,
    )

    source_kwargs = {
        "source_id": "OUTCOME_BARS",
        "source_definition_sha256": _source_definition_hash("OUTCOME_BARS"),
        "requested_start": SIGNAL.date(),
        "requested_end": SESSIONS[-1],
        "ticker": "BBCA",
        "snapshot_sha256": HASH_A,
        "bars_sha256": HASH_B,
        "corporate_action_policy_sha256": HASH_C,
        "corporate_action_events_sha256": HASH_A,
        "previous_source_sha256": None,
    }
    assert canonical_outcome_source_record_sha256(
        **source_kwargs,
        source_as_of=SIGNAL,
    ) == canonical_outcome_source_record_sha256(
        **source_kwargs,
        source_as_of=signal_utc,
    )

    scenario = _scenario()
    equivalent = ShadowObservation.model_validate(
        {
            **scenario.observation.model_dump(mode="python"),
            "signal_at": signal_utc,
            "captured_at": scenario.observation.captured_at.astimezone(
                timezone.utc
            ),
        }
    )
    assert canonical_sha256(equivalent) == canonical_sha256(
        scenario.observation
    )


def test_expired_candidate_rejected_and_explicit_quarantine_allowed() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    snapshot = _snapshot(manifest)
    expired_at = SIGNAL - timedelta(minutes=1)
    for rejected_expiry in (expired_at, SIGNAL):
        with pytest.raises(ValidationError):
            _candidate_record(
                manifest,
                snapshot,
                policy,
                ticker="BBCA",
                row=1,
                expires_at=rejected_expiry,
            )
    quarantined = _candidate_record(
        manifest,
        snapshot,
        policy,
        ticker="BBCA",
        row=1,
        expires_at=expired_at,
        quarantine_reason="EXPIRED",
    )
    assert isinstance(quarantined, QuarantinedCandidateEvent)


def test_optional_candidate_expiry_does_not_crash_validator() -> None:
    policy = _policy()
    manifest = _manifest(policy)
    snapshot = _snapshot(manifest)
    payload_json = canonical_payload_json({"Ticker": "BBCA"})
    payload_sha256 = canonical_payload_sha256(payload_json)
    definition = _source_definition_hash("CANDIDATES")
    source_as_of = SIGNAL - timedelta(hours=1)
    source_hash = canonical_source_record_sha256(
        source_id="CANDIDATES",
        source_definition_sha256=definition,
        source_as_of=source_as_of,
        source_expires_at=None,
        source_row_number=1,
        payload_sha256=payload_sha256,
    )
    event_id = canonical_raw_event_id(
        opportunity_set_id="OPP-OPTIONAL",
        ticker="BBCA",
        signal_at=SIGNAL,
        snapshot_sha256=snapshot.snapshot_sha256,
        candidate_source_sha256=source_hash,
        source_row_number=1,
        raw_payload_sha256=payload_sha256,
    )
    candidate = CandidateEvent(
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=canonical_sha256(manifest),
        opportunity_set_id="OPP-OPTIONAL",
        opportunity_set_sha256=HASH_A,
        raw_event_id=event_id,
        ticker="BBCA",
        signal_at=SIGNAL,
        as_of_date=SIGNAL.date(),
        captured_at=SIGNAL + timedelta(minutes=1),
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=snapshot.snapshot_sha256,
        snapshot_as_of=snapshot.snapshot_as_of,
        snapshot_source_record_sha256=snapshot.source_record_sha256,
        candidate_source_id="CANDIDATES",
        candidate_source_definition_sha256=definition,
        candidate_source_sha256=source_hash,
        candidate_source_as_of=source_as_of,
        candidate_source_expires_at=None,
        trading_calendar_id=manifest.trading_calendar_id,
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=policy.policy_sha256,
        corporate_action_events_at_signal_sha256=_signal_event_hash(policy),
        source_row_number=1,
        raw_payload_json=payload_json,
        raw_payload_sha256=payload_sha256,
    )
    assert candidate.candidate_source_expires_at is None
