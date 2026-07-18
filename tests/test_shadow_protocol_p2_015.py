"""Acceptance tests for the evaluation-only RS-P2-015 fixed-notional view."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from core.shadow_protocol import (
    CandidateSetStore,
    ContentHash,
    CorporateActionEvent,
    CorporateActionPolicy,
    FrozenSnapshot,
    LabelDefinition,
    PortfolioArtifactStore,
    RecordedTradeGeometry,
    ShadowContractError,
    ShadowDecision,
    ShadowProtocolManifest,
    SourceDefinition,
    TradingCalendar,
    build_frozen_control_portfolio_state,
    build_portfolio_lineage_bundle,
    canonical_corporate_action_events_sha256,
    canonical_corporate_action_policy_sha256,
    canonical_corporate_action_source_record_sha256,
    canonical_decision_payload_sha256,
    canonical_fixed_notional_event_id,
    canonical_fixed_notional_paired_record_id,
    canonical_json_bytes,
    canonical_opportunity_set_sha256,
    canonical_raw_candidate_set_sha256,
    canonical_sha256,
    canonical_trading_calendar_sha256,
    produce_paired_observation,
    quantize_ratio,
)
from core.shadow_protocol.calendar import session_close_at
from core.shadow_protocol.fixed_notional import (
    FIXED_NOTIONAL_POLICY_CONFIG_PATH,
    FixedNotionalBarSeries,
    FixedNotionalCashFlowRecord,
    FixedNotionalLifecycle,
    FixedNotionalLiquidityBar,
    FixedNotionalLiquidityMeasurement,
    FixedNotionalLiquidityRecord,
    FixedNotionalMarketBar,
    FixedNotionalPairInput,
    FrozenFixedNotionalPolicy,
    PairedFixedNotionalRecord,
    build_fixed_notional_bar_series,
    build_fixed_notional_liquidity_measurement,
    build_fixed_notional_liquidity_record,
    build_fixed_notional_pair_input,
    build_fixed_notional_policy,
    evaluate_fixed_notional_pair,
    fixed_notional_cost_idr,
    fixed_notional_lot_count,
    replay_fixed_notional_pair,
    verify_fixed_notional_policy_binding,
    verify_paired_fixed_notional_record,
)
from core.shadow_protocol.fixed_notional_store import (
    FixedNotionalArtifactStore,
    FixedNotionalGraphReference,
    FixedNotionalLineageReference,
    build_fixed_notional_lineage_bundle,
    load_fixed_notional_pair_input_v1,
    load_fixed_notional_policy_v1,
)
from tests.test_shadow_protocol_p2_014 import (
    HASH_A,
    METHODOLOGY_SHA256,
    SIGNAL,
    _cluster,
    _decision as _p14_decision,
    _manifest as _p14_manifest,
    _policy as _p14_policy,
    _raw_and_set,
    _snapshot,
    _source_record,
)
from tests.test_shadow_protocol_governance import (
    _approved_store as _governance_approved_store,
    _closure as _governance_closure,
    _observation as _governance_observation,
    _raw as _governance_raw,
)


def _weekday_sessions(start: date, end: date) -> tuple[date, ...]:
    sessions: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return tuple(sessions)


def _calendar(
    *,
    excluded_sessions: tuple[date, ...] = (),
) -> TradingCalendar:
    sessions = tuple(
        item
        for item in _weekday_sessions(
            date(2026, 5, 1),
            date(2026, 10, 7),
        )
        if item not in excluded_sessions
    )
    calendar_id = "IDX-P2-015-FROZEN"
    return TradingCalendar(
        calendar_id=calendar_id,
        calendar_sha256=canonical_trading_calendar_sha256(
            calendar_id,
            sessions,
        ),
        sessions=sessions,
    )


def _source(source_id: str, marker: str) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        source_type="FILE",
        locator=f"artifact://{source_id.lower()}",
        as_of_field="source_as_of",
        expiry_rule="point-in-time immutable record",
        missing_policy="ABSTAIN",
        contract_version="source-v1",
        source_sha256=marker * 64,
    )


OUTCOME_SOURCE = _source("OUTCOME_BARS", "6")
ACTION_SOURCE = _source("CORPORATE_ACTIONS", "7")


def _split_event(
    *,
    event_id: str,
    effective_date: date,
    published_at: datetime,
) -> CorporateActionEvent:
    source_definition_sha256 = canonical_sha256(ACTION_SOURCE)
    assert source_definition_sha256 is not None
    values = {
        "event_id": event_id,
        "ticker": "BBCA",
        "effective_date": effective_date,
        "kind": "SPLIT",
        "price_factor": 0.5,
        "quantity_factor": 2.0,
        "capital_call_per_pre_event_share": 0.0,
        "cash_per_share": 0.0,
        "source_id": ACTION_SOURCE.source_id,
        "source_definition_sha256": source_definition_sha256,
        "published_at": published_at,
    }
    return CorporateActionEvent(
        **values,
        source_sha256=canonical_corporate_action_source_record_sha256(
            **values,
        ),
    )


def _corporate_action_policy(
    *,
    dividend_return_convention: str = "PRICE_RETURN",
    events: tuple[CorporateActionEvent, ...] = (),
) -> CorporateActionPolicy:
    return CorporateActionPolicy(
        dividend_return_convention=dividend_return_convention,
        dividend_entitlement_rule="POSITION_OPEN_BEFORE_EX_DATE",
        bar_price_basis="RAW_AS_TRADED",
        prices_are_adjusted=False,
        dividends_are_in_prices=False,
        events=events,
        events_sha256=canonical_corporate_action_events_sha256(events),
        policy_sha256=canonical_corporate_action_policy_sha256(
            dividend_return_convention,
            False,
            False,
        ),
    )


def _labels(
    *,
    dividend_return_convention: str = "PRICE_RETURN",
) -> LabelDefinition:
    return LabelDefinition(
        entry_validity_trading_days=3,
        activation_rule="FIRST_TRADING_SESSION_AFTER_SIGNAL",
        horizon_clock_rule="POST_FILL_SESSIONS_EXCLUDING_FILL_SESSION",
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
        dividend_return_convention=dividend_return_convention,
        dividend_entitlement_rule="POSITION_OPEN_BEFORE_EX_DATE",
        unfilled_rule="EXPIRE_AFTER_ENTRY_VALIDITY_TRADING_DAYS",
    )


def _manifest_and_policies(
    *,
    label_dividend_return: str = "PRICE_RETURN",
    policy_dividend_return: str | None = None,
    action_events: tuple[CorporateActionEvent, ...] = (),
    excluded_calendar_sessions: tuple[date, ...] = (),
) -> tuple[
    ShadowProtocolManifest,
    object,
    FrozenFixedNotionalPolicy,
    TradingCalendar,
    CorporateActionPolicy,
]:
    calendar = _calendar(
        excluded_sessions=excluded_calendar_sessions,
    )
    action_policy = _corporate_action_policy(
        dividend_return_convention=(
            policy_dividend_return or label_dividend_return
        ),
        events=action_events,
    )
    portfolio_policy = _p14_policy(
        trading_calendar_sha256=calendar.calendar_sha256,
        corporate_action_policy_sha256=action_policy.policy_sha256,
    )
    provisional = _p14_manifest(portfolio_policy)
    provisional = ShadowProtocolManifest.model_validate(
        {
            **provisional.model_dump(mode="python"),
            "trading_calendar_id": calendar.calendar_id,
            "labels": _labels(
                dividend_return_convention=label_dividend_return,
            ),
            "sources": (*provisional.sources, OUTCOME_SOURCE, ACTION_SOURCE),
        }
    )
    fixed_policy = build_fixed_notional_policy(
        manifest=provisional,
        portfolio_policy=portfolio_policy,
        policy_id="RS-P2-015-FIXED-POLICY-TEST",
    )
    policy_sha256 = canonical_sha256(fixed_policy)
    assert policy_sha256 is not None
    final = ShadowProtocolManifest.model_validate(
        {
            **provisional.model_dump(mode="python"),
            "control_content_hashes": (
                *provisional.control_content_hashes,
                ContentHash(
                    path=FIXED_NOTIONAL_POLICY_CONFIG_PATH,
                    sha256=policy_sha256,
                    role="CONFIG",
                ),
            ),
            "challenger_content_hashes": (
                *provisional.challenger_content_hashes,
                ContentHash(
                    path=FIXED_NOTIONAL_POLICY_CONFIG_PATH,
                    sha256=policy_sha256,
                    role="CONFIG",
                ),
            ),
        }
    )
    verify_fixed_notional_policy_binding(
        final,
        portfolio_policy,
        fixed_policy,
    )
    return final, portfolio_policy, fixed_policy, calendar, action_policy


def _raw_and_set_with_actions(
    manifest: ShadowProtocolManifest,
    snapshots: tuple[FrozenSnapshot, ...],
    action_policy: CorporateActionPolicy,
    *,
    signal_events_sha256_override: str | None = None,
):
    raw_capture, candidate_set = _raw_and_set(manifest, snapshots)
    signal_events = tuple(
        event
        for event in action_policy.events
        if event.published_at <= SIGNAL
    )
    signal_events_sha256 = (
        signal_events_sha256_override
        or canonical_corporate_action_events_sha256(signal_events)
    )
    candidates = tuple(
        item.__class__.model_validate(
            {
                **item.model_dump(mode="python"),
                "corporate_action_events_at_signal_sha256": (
                    signal_events_sha256
                ),
            }
        )
        for item in raw_capture.candidates
    )
    opportunity_sha256 = canonical_opportunity_set_sha256(
        raw_capture.opportunity_set_id,
        raw_capture.as_of_date,
        candidates,
        empty_reason=raw_capture.empty_reason,
        candidate_source_definition_sha256=(
            raw_capture.candidate_source_definition_sha256
        ),
        trading_calendar_sha256=raw_capture.trading_calendar_sha256,
        corporate_action_policy_sha256=(
            raw_capture.corporate_action_policy_sha256
        ),
    )
    candidates = tuple(
        item.__class__.model_validate(
            {
                **item.model_dump(mode="python"),
                "opportunity_set_sha256": opportunity_sha256,
            }
        )
        for item in candidates
    )
    raw_candidate_set_sha256 = canonical_raw_candidate_set_sha256(
        candidates,
        empty_reason=raw_capture.empty_reason,
    )
    rebuilt_raw = raw_capture.__class__.model_validate(
        {
            **raw_capture.model_dump(mode="python"),
            "opportunity_set_sha256": opportunity_sha256,
            "raw_candidate_set_sha256": raw_candidate_set_sha256,
            "candidates": candidates,
        }
    )
    rebuilt_set = candidate_set.__class__.model_validate(
        {
            **candidate_set.model_dump(mode="python"),
            "raw_capture_sha256": canonical_sha256(rebuilt_raw),
            "opportunity_set_sha256": opportunity_sha256,
            "raw_candidate_set_sha256": raw_candidate_set_sha256,
        }
    )
    return rebuilt_raw, rebuilt_set


def _decision(
    role: str,
    *,
    actionable: bool = True,
    entry_high: int = 10_000,
) -> ShadowDecision:
    if not actionable:
        return _p14_decision(
            role,
            actionable=False,
            reason_codes=("WAIT",),
        )
    base = _p14_decision(role)
    geometry = RecordedTradeGeometry(
        entry_low=entry_high - 100,
        entry_high=entry_high,
        target_price=entry_high + 2_000,
        stop_loss=entry_high - 1_000,
        risk_reward_ratio=2.0,
        required_risk_reward=2.0,
    )
    values = base.model_dump(mode="python")
    values.pop("decision_payload_sha256")
    values["geometry"] = geometry
    draft = ShadowDecision.model_construct(
        **values,
        decision_payload_sha256=HASH_A,
    )
    return ShadowDecision(
        **values,
        decision_payload_sha256=canonical_decision_payload_sha256(draft),
    )


class _AuthorizationSpy:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.paired_calls: list[dict[str, object]] = []
        self.maturation_calls: list[dict[str, object]] = []

    def verify_paired_evaluation_authorization(
        self,
        **kwargs: object,
    ) -> object:
        self.paired_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return object()

    def verify_fixed_notional_maturation_authorization(
        self,
        **kwargs: object,
    ) -> object:
        self.maturation_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return object()


def _liquidity_bars(
    calendar: TradingCalendar,
    capacity_session: date,
    *,
    daily_turnover_idr: int,
    stale_by_sessions: int = 0,
) -> tuple[FixedNotionalLiquidityBar, ...]:
    preceding = [
        item for item in calendar.sessions if item < capacity_session
    ]
    end = len(preceding) - stale_by_sessions
    dates = preceding[end - 20 : end]
    close = 10_000
    assert daily_turnover_idr % close == 0
    volume = daily_turnover_idr // close
    return tuple(
        FixedNotionalLiquidityBar(
            trade_date=item,
            close_price_idr=close,
            volume_shares=volume,
            available_at=session_close_at(item),
            source_record_sha256=hashlib.sha256(
                f"{item.isoformat()}:{daily_turnover_idr}".encode()
            ).hexdigest(),
        )
        for item in dates
    )


def _measurement(
    policy: FrozenFixedNotionalPolicy,
    calendar: TradingCalendar,
    *,
    role: str,
    capacity_session: date,
    daily_turnover_idr: int = 10_000_000_000,
    stale_by_sessions: int = 0,
) -> FixedNotionalLiquidityMeasurement:
    bars = _liquidity_bars(
        calendar,
        capacity_session,
        daily_turnover_idr=daily_turnover_idr,
        stale_by_sessions=stale_by_sessions,
    )
    return build_fixed_notional_liquidity_measurement(
        policy=policy,
        trading_calendar=calendar,
        measurement_role=role,
        capacity_session=capacity_session,
        measured_at=bars[-1].available_at,
        bars=bars,
    )


def _market_bars(
    sessions: tuple[date, ...],
    *,
    mode: str,
    target_bar_index: int = 1,
) -> tuple[FixedNotionalMarketBar, ...]:
    bars: list[FixedNotionalMarketBar] = []
    for index, session in enumerate(sessions[:16]):
        if mode == "unfilled":
            values = (11_000, 11_200, 10_500, 10_800)
        elif mode == "target" and index == target_bar_index:
            values = (12_000, 12_100, 11_900, 12_000)
        else:
            values = (10_000, 10_100, 9_900, 10_000)
        bars.append(
            FixedNotionalMarketBar(
                trade_date=session,
                open_price_idr=values[0],
                high_price_idr=values[1],
                low_price_idr=values[2],
                close_price_idr=values[3],
                volume_shares=1_000_000,
            )
        )
    return tuple(bars)


@dataclass(frozen=True)
class _Scenario:
    manifest: ShadowProtocolManifest
    portfolio_policy: object
    policy: FrozenFixedNotionalPolicy
    calendar: TradingCalendar
    raw_capture: object
    candidate_set: object
    source_record: object
    snapshot: FrozenSnapshot
    state: object
    observation: object
    liquidity: FixedNotionalLiquidityRecord
    bar_series: FixedNotionalBarSeries
    pair: FixedNotionalPairInput
    auth: _AuthorizationSpy


def _scenario(
    tmp_path: Path,
    *,
    control_actionable: bool = True,
    challenger_actionable: bool = True,
    control_entry_high: int = 10_000,
    challenger_entry_high: int = 10_000,
    entry_adtv_idr: int = 10_000_000_000,
    exit_adtv_idr: int = 20_000_000_000,
    bar_mode: str = "timeout",
    label_dividend_return: str = "PRICE_RETURN",
    policy_dividend_return: str | None = None,
    action_events: tuple[CorporateActionEvent, ...] = (),
    signal_events_sha256_override: str | None = None,
    excluded_calendar_sessions: tuple[date, ...] = (),
    target_bar_index: int = 1,
    exit_measurement_indices: tuple[int, ...] = (1, 3, 5, 10, 15),
) -> _Scenario:
    (
        manifest,
        portfolio_policy,
        fixed_policy,
        calendar,
        action_policy,
    ) = _manifest_and_policies(
        label_dividend_return=label_dividend_return,
        policy_dividend_return=policy_dividend_return,
        action_events=action_events,
        excluded_calendar_sessions=excluded_calendar_sessions,
    )
    snapshot = _snapshot()
    raw_capture, candidate_set = _raw_and_set_with_actions(
        manifest,
        (snapshot,),
        action_policy,
        signal_events_sha256_override=signal_events_sha256_override,
    )
    source_record = _source_record(manifest)
    state = build_frozen_control_portfolio_state(
        manifest=manifest,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        policy=portfolio_policy,
        source_record=source_record,
        state_as_of=SIGNAL - timedelta(minutes=5),
        captured_at=SIGNAL + timedelta(minutes=3, seconds=30),
    )
    candidate_store = CandidateSetStore(tmp_path)
    portfolio_store = PortfolioArtifactStore(tmp_path)
    candidate_store.persist_raw(raw_capture)
    candidate_store.persist(candidate_set)
    portfolio_store.persist_policy(
        manifest,
        canonical_json_bytes(portfolio_policy),
    )
    portfolio_store.persist_source_record(
        manifest,
        canonical_json_bytes(source_record),
    )
    portfolio_store.persist_state(manifest, canonical_json_bytes(state))

    auth = _AuthorizationSpy()
    observation = produce_paired_observation(
        manifest=manifest,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        candidate=raw_capture.candidates[0],
        frozen_snapshot=snapshot,
        feature_values_sha256=HASH_A,
        portfolio_state_sha256=str(canonical_sha256(state)),
        artifact_store=portfolio_store,
        authorization_loader=auth,
        approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
        control_evaluator=lambda _: _decision(
            "CONTROL",
            actionable=control_actionable,
            entry_high=control_entry_high,
        ),
        challenger_evaluator=lambda _: _decision(
            "CHALLENGER",
            actionable=challenger_actionable,
            entry_high=challenger_entry_high,
        ),
        cluster=_cluster(manifest),
        captured_at=SIGNAL + timedelta(minutes=4),
    )
    future = tuple(
        item
        for item in calendar.sessions
        if SIGNAL.date() < item <= manifest.fixed_terminal_date
    )
    entry_measurement = _measurement(
        fixed_policy,
        calendar,
        role="ENTRY",
        capacity_session=future[0],
        daily_turnover_idr=entry_adtv_idr,
    )
    exit_measurements = tuple(
        _measurement(
            fixed_policy,
            calendar,
            role="EXIT",
            capacity_session=future[index],
            daily_turnover_idr=exit_adtv_idr,
        )
        for index in exit_measurement_indices
    )
    cutoff = session_close_at(manifest.fixed_terminal_date)
    liquidity = build_fixed_notional_liquidity_record(
        manifest=manifest,
        observation=observation,
        portfolio_state=state,
        policy=fixed_policy,
        entry_measurement=entry_measurement,
        exit_measurements=exit_measurements,
        captured_at=cutoff,
    )
    market_bars = _market_bars(
        future,
        mode=bar_mode,
        target_bar_index=target_bar_index,
    )
    bar_series = build_fixed_notional_bar_series(
        ticker=observation.ticker,
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=snapshot.snapshot_sha256,
        source_id=OUTCOME_SOURCE.source_id,
        source_definition_sha256=str(canonical_sha256(OUTCOME_SOURCE)),
        source_as_of=session_close_at(market_bars[-1].trade_date),
        requested_start=SIGNAL.date(),
        requested_end=manifest.fixed_terminal_date,
        bars=market_bars,
        corporate_action_policy=action_policy,
    )
    pair = build_fixed_notional_pair_input(
        manifest=manifest,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        candidate=raw_capture.candidates[0],
        snapshot=snapshot,
        portfolio_state=state,
        observation=observation,
        policy=fixed_policy,
        liquidity=liquidity,
        trading_calendar=calendar,
        bar_series=bar_series,
        frozen_at=cutoff,
        evaluation_cutoff=cutoff,
        authorization_loader=auth,
        approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
    )
    return _Scenario(
        manifest=manifest,
        portfolio_policy=portfolio_policy,
        policy=fixed_policy,
        calendar=calendar,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        source_record=source_record,
        snapshot=snapshot,
        state=state,
        observation=observation,
        liquidity=liquidity,
        bar_series=bar_series,
        pair=pair,
        auth=auth,
    )


def _evaluate(scenario: _Scenario) -> PairedFixedNotionalRecord:
    return evaluate_fixed_notional_pair(
        scenario.pair,
        authorization_loader=scenario.auth,
        approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
        attempted_at=scenario.pair.frozen_at,
    )


def _base_portfolio_lineage(scenario: _Scenario) -> object:
    return build_portfolio_lineage_bundle(
        manifest=scenario.manifest,
        frozen_snapshot=scenario.snapshot,
        raw_capture=scenario.raw_capture,
        candidate_set=scenario.candidate_set,
        candidate=scenario.raw_capture.candidates[0],
        observation=scenario.observation,
        policy=scenario.portfolio_policy,
        source_record=scenario.source_record,
        state=scenario.state,
    )


@dataclass(frozen=True)
class _StoredScenario:
    scenario: _Scenario
    result: PairedFixedNotionalRecord
    base_lineage: object
    store: FixedNotionalArtifactStore
    paths: dict[str, tuple[Path, ...]]


def _stored_scenario(tmp_path: Path) -> _StoredScenario:
    store_root = tmp_path / "store"
    scenario = _scenario(store_root, bar_mode="target")
    result = _evaluate(scenario)
    base_lineage = _base_portfolio_lineage(scenario)
    store = FixedNotionalArtifactStore(store_root)
    paths = store.persist_pair_bundle(
        scenario.manifest,
        base_lineage,
        canonical_json_bytes(scenario.pair),
        canonical_json_bytes(result),
    )
    return _StoredScenario(
        scenario=scenario,
        result=result,
        base_lineage=base_lineage,
        store=store,
        paths=paths,
    )


def _stored_lineage(stored: _StoredScenario):
    return build_fixed_notional_lineage_bundle(
        manifest=stored.scenario.manifest,
        base_portfolio_lineage=stored.base_lineage,
        pair_input=stored.scenario.pair,
        paired_record=stored.result,
    )


def _reference_path(
    stored: _StoredScenario,
    kind: str,
    artifact_id: str,
) -> Path:
    manifest_hash = str(canonical_sha256(stored.scenario.manifest))
    return (
        stored.store.root
        / "protocols"
        / stored.scenario.manifest.protocol_id
        / manifest_hash
        / "fixed_notional"
        / "refs"
        / kind.lower()
        / f"{artifact_id}.json"
    )


def _rewrite_reference(
    path: Path,
    **changes: object,
) -> FixedNotionalGraphReference:
    payload = json.loads(path.read_text(encoding="utf-8"))
    reference = FixedNotionalGraphReference.model_validate(
        {**payload, **changes}
    )
    path.write_bytes(canonical_json_bytes(reference))
    return reference


def test_policy_binds_owner_decisions_without_manifest_hash_circularity() -> None:
    manifest, portfolio, policy, _, _ = _manifest_and_policies()

    assert "manifest_sha256" not in FrozenFixedNotionalPolicy.model_fields
    assert policy.fixed_notional_idr == 13_000_000
    assert policy.capacity_rule == "ALL_OR_NONE"
    assert policy.participation_evidence_class == "DERIVED_NOT_CALIBRATED"
    assert policy.effective_universe_max_price_idr == 130_000
    assert policy.primary_nav_horizon_trading_days == 15
    assert (
        policy.phase2_capability_status
        == "RS_P2_015_IMPLEMENTED_NOT_A1_ELIGIBLE"
    )
    assert verify_fixed_notional_policy_binding(manifest, portfolio, policy) == policy


def test_manifest_requires_same_fixed_policy_hash_on_both_sides() -> None:
    manifest, portfolio, policy, _, _ = _manifest_and_policies()
    forged = ShadowProtocolManifest.model_validate(
        {
            **manifest.model_dump(mode="python"),
            "challenger_content_hashes": tuple(
                ContentHash(
                    path=item.path,
                    sha256=("f" * 64)
                    if item.path == FIXED_NOTIONAL_POLICY_CONFIG_PATH
                    else item.sha256,
                    role=item.role,
                )
                for item in manifest.challenger_content_hashes
            ),
        }
    )

    with pytest.raises(ShadowContractError, match="CONFIG hash"):
        verify_fixed_notional_policy_binding(forged, portfolio, policy)


def test_signal_time_corporate_action_hash_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    event = _split_event(
        event_id="SPLIT-KNOWN-AT-SIGNAL",
        effective_date=date(2026, 7, 21),
        published_at=SIGNAL - timedelta(days=1),
    )

    with pytest.raises(
        ShadowContractError,
        match="paired input is invalid",
    ) as error:
        _scenario(
            tmp_path,
            action_events=(event,),
            signal_events_sha256_override=HASH_A,
        )
    assert "signal-time corporate-action event lineage mismatch" in str(
        error.value.__cause__
    )


def test_manifest_label_and_corporate_action_dividend_convention_must_match(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ShadowContractError,
        match="paired input is invalid",
    ) as error:
        _scenario(
            tmp_path,
            label_dividend_return="PRICE_RETURN",
            policy_dividend_return="TOTAL_RETURN",
        )
    assert "dividend-return label differs" in str(error.value.__cause__)


def test_post_signal_split_on_non_session_fails_closed(
    tmp_path: Path,
) -> None:
    event = _split_event(
        event_id="SPLIT-ON-SATURDAY",
        effective_date=date(2026, 7, 18),
        published_at=SIGNAL + timedelta(hours=1),
    )

    with pytest.raises(
        ShadowContractError,
        match="paired input is invalid",
    ) as error:
        _scenario(tmp_path, action_events=(event,))
    assert "effective date is not a frozen trading session" in str(
        error.value.__cause__
    )


@pytest.mark.parametrize(
    ("price", "lots"),
    ((130_000, 1), (130_001, 0), (9_900, 13)),
)
def test_fixed_notional_lot_floor_boundaries(price: int, lots: int) -> None:
    assert fixed_notional_lot_count(price) == lots


def test_costs_aggregate_then_ceil_once() -> None:
    manifest, _, _, _, _ = _manifest_and_policies()

    assert fixed_notional_cost_idr(
        999_900,
        manifest.costs,
        side="ENTRY",
    ) == 2_500
    assert fixed_notional_cost_idr(
        999_900,
        manifest.costs,
        side="EXIT",
    ) == 4_500


def test_fn_n2_entry_cost_is_separate_and_debit_can_exceed_sleeve(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, bar_mode="target")
    result = _evaluate(scenario)
    entry = next(
        item
        for item in result.control.cash_flow_records
        if item.event_type == "ENTRY_DEBIT"
    )

    assert entry.gross_amount_idr == 13_000_000
    assert entry.cost_idr == 32_500
    assert entry.net_cash_change_idr == -13_032_500
    assert result.control.target_sleeve_idr == 13_000_000
    assert result.control.residual_idle_cash_idr == 0


def test_fn3_residual_is_idle_cash_and_sleeve_return_keeps_13m_denominator(
    tmp_path: Path,
) -> None:
    scenario = _scenario(
        tmp_path,
        control_entry_high=9_900,
        challenger_entry_high=9_900,
    )
    result = _evaluate(scenario)
    control = result.control

    assert scenario.pair.control_sizing_plan.desired_lots == 13
    assert control.gross_entry_notional_idr == 12_870_000
    assert control.residual_idle_cash_idr == 130_000
    assert (
        control.gross_entry_notional_idr
        + control.residual_idle_cash_idr
        == 13_000_000
    )
    assert control.net_pnl_idr.value_idr is not None
    assert control.sleeve_return.value == quantize_ratio(
        control.net_pnl_idr.value_idr,
        13_000_000,
    )
    assert control.acted_trade_return.value == quantize_ratio(
        control.net_pnl_idr.value_idr,
        control.gross_entry_notional_idr,
    )


def test_non_action_is_zero_opportunity_but_trade_metrics_not_estimable(
    tmp_path: Path,
) -> None:
    scenario = _scenario(
        tmp_path,
        control_actionable=False,
        challenger_actionable=True,
    )
    result = _evaluate(scenario)

    assert result.control.terminal_event == "NO_ACTION"
    assert result.control.net_pnl_idr.value_idr == 0
    assert result.control.sleeve_return.value == 0.0
    assert result.control.acted_trade_return.status == "NOT_ESTIMABLE"
    assert result.control.net_r.status == "NOT_ESTIMABLE"
    assert result.control.holding_records == ()
    assert result.control.cash_flow_records == ()


def test_unfilled_preserves_distinct_label_and_no_trade_metrics(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, bar_mode="unfilled")
    result = _evaluate(scenario)

    assert result.control.fill_status == "EXPIRED_UNFILLED"
    assert result.control.terminal_event == "UNFILLED"
    assert result.control.net_pnl_idr.value_idr == 0
    assert result.control.sleeve_return.value == 0.0
    assert result.control.acted_trade_return.status == "NOT_ESTIMABLE"
    assert result.control.net_r.status == "NOT_ESTIMABLE"
    assert result.control.holding_records == ()
    assert result.control.cash_flow_records == ()


def test_liquidity_window_is_exact_immediate_prior_twenty_sessions() -> None:
    _, _, policy, calendar, _ = _manifest_and_policies()
    capacity = next(item for item in calendar.sessions if item > SIGNAL.date())

    measurement = _measurement(
        policy,
        calendar,
        role="ENTRY",
        capacity_session=capacity,
    )

    expected = tuple(item for item in calendar.sessions if item < capacity)[-20:]
    assert tuple(item.trade_date for item in measurement.bars) == expected


def test_stale_liquidity_window_is_rejected() -> None:
    _, _, policy, calendar, _ = _manifest_and_policies()
    capacity = next(item for item in calendar.sessions if item > SIGNAL.date())

    with pytest.raises(
        ShadowContractError,
        match="stale or lacks the immediate prior session",
    ):
        _measurement(
            policy,
            calendar,
            role="ENTRY",
            capacity_session=capacity,
            stale_by_sessions=2,
        )


def test_liquidity_capture_before_measurement_is_rejected(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    measurement = scenario.liquidity.exit_measurements[-1]
    values = scenario.liquidity.model_dump(mode="python")
    values["captured_at"] = measurement.measured_at - timedelta(seconds=1)

    with pytest.raises(ValidationError, match="precedes source vintage"):
        FixedNotionalLiquidityRecord.model_validate(values)


def test_entry_capacity_is_all_or_none_and_shared(tmp_path: Path) -> None:
    scenario = _scenario(
        tmp_path,
        entry_adtv_idr=9_000_000_000,
    )
    result = _evaluate(scenario)

    assert scenario.pair.shared_exclusion_reason == (
        "NOT_ESTIMABLE_ENTRY_CAPACITY"
    )
    assert result.control.reason_codes == ("NOT_ESTIMABLE_ENTRY_CAPACITY",)
    assert result.challenger.reason_codes == (
        "NOT_ESTIMABLE_ENTRY_CAPACITY",
    )
    assert result.control.entry_quantity_shares is None
    assert result.challenger.entry_quantity_shares is None


def test_exit_capacity_censors_instead_of_assuming_exit(tmp_path: Path) -> None:
    scenario = _scenario(
        tmp_path,
        exit_adtv_idr=9_000_000_000,
        bar_mode="target",
    )
    result = _evaluate(scenario)

    assert result.control.status == "NOT_ESTIMABLE"
    assert result.control.reason_codes == ("NOT_ESTIMABLE_EXIT_CAPACITY",)
    assert result.control.net_pnl_idr.status == "NOT_ESTIMABLE"
    assert result.control.rs_p2_017_consumption_status == (
        "EXCLUDED_NOT_ESTIMABLE"
    )


def test_high_price_exclusion_is_identical_and_zero_size_is_not_persisted(
    tmp_path: Path,
) -> None:
    scenario = _scenario(
        tmp_path,
        control_entry_high=130_001,
        challenger_entry_high=130_001,
    )
    result = _evaluate(scenario)

    assert scenario.pair.shared_exclusion_reason == (
        "NOT_SIZEABLE_FIXED_NOTIONAL"
    )
    assert result.control.reason_codes == ("NOT_SIZEABLE_FIXED_NOTIONAL",)
    assert result.challenger.reason_codes == (
        "NOT_SIZEABLE_FIXED_NOTIONAL",
    )
    assert result.control.holding_records == ()
    assert result.challenger.holding_records == ()


def test_eligible_side_geometry_may_differ_but_exclusion_mismatch_fails(
    tmp_path: Path,
) -> None:
    eligible = _scenario(
        tmp_path / "eligible",
        control_entry_high=10_000,
        challenger_entry_high=9_900,
    )
    assert eligible.pair.control_sizing_plan.desired_lots == 13
    assert eligible.pair.challenger_sizing_plan.desired_lots == 13
    assert (
        eligible.pair.control_sizing_plan.gross_entry_notional_idr
        != eligible.pair.challenger_sizing_plan.gross_entry_notional_idr
    )

    with pytest.raises(ShadowContractError, match="eligibility"):
        _scenario(
            tmp_path / "mismatch",
            control_entry_high=130_001,
            challenger_entry_high=10_000,
        )


def test_secondary_horizons_never_create_nav_events(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, bar_mode="target")
    result = _evaluate(scenario)

    assert tuple(
        item.horizon_trading_days for item in result.control_secondary
    ) == (3, 5, 10)
    for lifecycle in (
        *result.control_secondary,
        *result.challenger_secondary,
    ):
        assert lifecycle.rs_p2_017_consumption_status == (
            "SECONDARY_METRIC_ONLY"
        )
        assert lifecycle.holding_records == ()
        assert lifecycle.cash_flow_records == ()
    assert result.control.horizon_trading_days == 15
    assert result.control.primary_horizon is True
    assert result.control.holding_records
    assert result.control.cash_flow_records
    assert all(item.rs_p2_017_eligible for item in result.control.holding_records)
    assert all(item.rs_p2_017_eligible for item in result.control.cash_flow_records)


def test_trade_cash_flows_use_exact_t_plus_two_across_weekend_and_holiday(
    tmp_path: Path,
) -> None:
    monday_holiday = date(2026, 7, 27)
    scenario = _scenario(
        tmp_path,
        bar_mode="target",
        excluded_calendar_sessions=(monday_holiday,),
        target_bar_index=4,
        exit_measurement_indices=(1, 3, 4, 5, 10, 15),
    )
    result = _evaluate(scenario)
    exit_credit = next(
        item
        for item in result.control.cash_flow_records
        if item.event_type == "EXIT_CREDIT"
    )

    assert exit_credit.trade_session == date(2026, 7, 24)
    assert exit_credit.trade_session.weekday() == 4
    assert monday_holiday not in scenario.calendar.sessions
    assert exit_credit.settlement_session == date(2026, 7, 29)
    trade_index = scenario.calendar.sessions.index(exit_credit.trade_session)
    assert (
        scenario.calendar.sessions[trade_index + 2]
        == exit_credit.settlement_session
    )

    wrong_settlement = scenario.calendar.sessions[trade_index + 1]
    cash_values = exit_credit.model_dump(mode="python")
    cash_values["settlement_session"] = wrong_settlement
    cash_values["cash_flow_id"] = canonical_fixed_notional_event_id(
        prefix="FCF",
        payload={
            "decision_role": exit_credit.decision_role,
            "event_type": exit_credit.event_type,
            "gross_amount_idr": exit_credit.gross_amount_idr,
            "net_cash_change_idr": exit_credit.net_cash_change_idr,
            "occurred_at": exit_credit.occurred_at.isoformat(),
            "quantity_shares": exit_credit.quantity_shares,
            "protocol_id": exit_credit.protocol_id,
            "component_id": exit_credit.component_id,
            "manifest_sha256": exit_credit.manifest_sha256,
            "observation_id": exit_credit.observation_id,
            "observation_sha256": exit_credit.observation_sha256,
            "pair_input_sha256": exit_credit.pair_input_sha256,
            "raw_event_id": exit_credit.raw_event_id,
            "ticker": exit_credit.ticker,
            "portfolio_state_sha256": (
                exit_credit.portfolio_state_sha256
            ),
            "fixed_notional_policy_sha256": (
                exit_credit.fixed_notional_policy_sha256
            ),
            "decision_sha256": exit_credit.decision_sha256,
            "settlement_session": wrong_settlement.isoformat(),
            "trade_session": exit_credit.trade_session.isoformat(),
        },
    )
    forged_cash = FixedNotionalCashFlowRecord.model_validate(cash_values)
    cash_records = tuple(
        forged_cash if item.cash_flow_id == exit_credit.cash_flow_id else item
        for item in result.control.cash_flow_records
    )
    control = FixedNotionalLifecycle.model_validate(
        {
            **result.control.model_dump(mode="python"),
            "cash_flow_records": cash_records,
            "cash_flow_record_sha256s": tuple(
                canonical_sha256(item) for item in cash_records
            ),
        }
    )
    control_sha256 = canonical_sha256(control)
    assert control_sha256 is not None
    paired_values = result.model_dump(mode="python")
    paired_values["control"] = control
    paired_values["control_lifecycle_sha256"] = control_sha256
    paired_values["paired_record_id"] = (
        canonical_fixed_notional_paired_record_id(
            pair_input_sha256=result.pair_input_sha256,
            control_sha256s=(
                *result.control_secondary_sha256s,
                control_sha256,
            ),
            challenger_sha256s=(
                *result.challenger_secondary_sha256s,
                result.challenger_lifecycle_sha256,
            ),
        )
    )

    with pytest.raises(ValidationError, match="settlement is not exact T\\+2"):
        PairedFixedNotionalRecord.model_validate(paired_values)


def test_pair_input_id_and_embedded_hash_drift_are_rejected(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    values = scenario.pair.model_dump(mode="python")
    values["pair_input_id"] = "FNINPUT-arbitrary"
    with pytest.raises(ValidationError, match="pair-input ID"):
        FixedNotionalPairInput.model_validate(values)

    values = scenario.pair.model_dump(mode="python")
    values["portfolio_state_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="embedded hash"):
        FixedNotionalPairInput.model_validate(values)


@pytest.mark.parametrize("event_kind", ("cash_flow", "holding"))
def test_event_lineage_transplant_between_sides_is_rejected(
    tmp_path: Path,
    event_kind: str,
) -> None:
    scenario = _scenario(tmp_path, bar_mode="target")
    result = _evaluate(scenario)
    control = result.control
    challenger = result.challenger
    values = control.model_dump(mode="python")
    if event_kind == "cash_flow":
        transplanted = challenger.cash_flow_records
        values["cash_flow_records"] = transplanted
        values["cash_flow_record_sha256s"] = tuple(
            canonical_sha256(item) for item in transplanted
        )
    else:
        transplanted = challenger.holding_records
        values["holding_records"] = transplanted
        values["holding_record_sha256s"] = tuple(
            canonical_sha256(item) for item in transplanted
        )

    with pytest.raises(ValidationError, match="lineage mismatch"):
        FixedNotionalLifecycle.model_validate(values)


def test_one_idr_pnl_drift_is_rejected(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, bar_mode="target")
    result = _evaluate(scenario)
    values = result.control.model_dump(mode="python")
    pnl = result.control.net_pnl_idr
    assert pnl.value_idr is not None
    values["net_pnl_idr"] = {
        **pnl.model_dump(mode="python"),
        "value_idr": pnl.value_idr + 1,
    }

    with pytest.raises(ValidationError, match="P&L arithmetic"):
        FixedNotionalLifecycle.model_validate(values)


def test_coordinated_one_idr_risk_basis_drift_fails_exact_replay(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, bar_mode="target")
    result = _evaluate(scenario)
    control_values = result.control.model_dump(mode="python")
    risk_basis = result.control.risk_capital_basis_idr
    pnl = result.control.net_pnl_idr.value_idr
    assert risk_basis is not None
    assert pnl is not None
    control_values["risk_capital_basis_idr"] = risk_basis + 1
    control_values["net_r"] = {
        **result.control.net_r.model_dump(mode="python"),
        "value": quantize_ratio(pnl, risk_basis + 1),
    }
    forged_control = FixedNotionalLifecycle.model_validate(control_values)
    forged_control_sha256 = canonical_sha256(forged_control)
    assert forged_control_sha256 is not None

    paired_values = result.model_dump(mode="python")
    paired_values["control"] = forged_control
    paired_values["control_lifecycle_sha256"] = forged_control_sha256
    paired_values["paired_record_id"] = (
        canonical_fixed_notional_paired_record_id(
            pair_input_sha256=result.pair_input_sha256,
            control_sha256s=(
                *result.control_secondary_sha256s,
                forged_control_sha256,
            ),
            challenger_sha256s=(
                *result.challenger_secondary_sha256s,
                result.challenger_lifecycle_sha256,
            ),
        )
    )
    forged_record = PairedFixedNotionalRecord.model_validate(paired_values)

    with pytest.raises(
        ShadowContractError,
        match="exact input derivation",
    ):
        verify_paired_fixed_notional_record(scenario.pair, forged_record)


def test_fixed_notional_cost_rejects_unknown_side() -> None:
    manifest, _, _, _, _ = _manifest_and_policies()

    with pytest.raises(ValueError, match="ENTRY or EXIT"):
        fixed_notional_cost_idr(
            13_000_000,
            manifest.costs,
            side="UNKNOWN",  # type: ignore[arg-type]
        )


def test_lifecycle_rejects_maturity_before_signal(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, bar_mode="target")
    lifecycle = _evaluate(scenario).control

    with pytest.raises(ValidationError, match="maturity precedes signal"):
        FixedNotionalLifecycle.model_validate(
            {
                **lifecycle.model_dump(mode="python"),
                "maturity_at": lifecycle.signal_at - timedelta(seconds=1),
                "closed_at": lifecycle.signal_at - timedelta(seconds=1),
            }
        )


def test_paired_record_rejects_pairing_before_lifecycle_evaluation(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, bar_mode="target")
    result = _evaluate(scenario)

    with pytest.raises(
        ValidationError,
        match="lifecycle evaluation chronology",
    ):
        PairedFixedNotionalRecord.model_validate(
            {
                **result.model_dump(mode="python"),
                "paired_at": result.control.evaluated_at
                - timedelta(seconds=1),
            }
        )


def test_authorization_checked_before_pair_and_maturation(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    assert len(scenario.auth.paired_calls) == 1
    assert len(scenario.auth.maturation_calls) == 1

    _evaluate(scenario)

    assert len(scenario.auth.maturation_calls) == 2


def test_delayed_evaluation_passes_actual_attempt_time_to_governance(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    attempted_at = scenario.pair.frozen_at + timedelta(hours=6)

    evaluate_fixed_notional_pair(
        scenario.pair,
        authorization_loader=scenario.auth,
        approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
        attempted_at=attempted_at,
    )

    assert scenario.auth.maturation_calls[-1]["attempted_at"] == attempted_at


def test_authorization_failure_blocks_maturation(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    scenario.auth.error = ShadowContractError("closed")

    with pytest.raises(ShadowContractError, match="authorization"):
        _evaluate(scenario)


def test_fixed_notional_store_replay_is_idempotent_and_reconstructable(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    replay_paths = stored.store.persist_pair_bundle(
        stored.scenario.manifest,
        stored.base_lineage,
        canonical_json_bytes(stored.scenario.pair),
        canonical_json_bytes(stored.result),
    )
    assert replay_paths == stored.paths

    pair, paired = stored.store.load_pair_bundle(
        stored.scenario.manifest,
        pair_input_id=stored.scenario.pair.pair_input_id,
        paired_record_id=stored.result.paired_record_id,
    )
    expected_lineage = _stored_lineage(stored)
    lineage = stored.store.load_verified_lineage(
        stored.scenario.manifest,
        lineage_id=expected_lineage.lineage_id,
        pair_input_id=stored.scenario.pair.pair_input_id,
        paired_record_id=stored.result.paired_record_id,
    )

    assert pair == stored.scenario.pair
    assert paired == stored.result
    assert lineage == expected_lineage
    assert lineage.contract_version == "shadow-fixed-notional-lineage-v1"
    assert lineage.base_portfolio_lineage_sha256 == canonical_sha256(
        stored.base_lineage
    )


def test_fixed_notional_store_reconstructs_base_from_persisted_substrate(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)

    reconstructed = stored.store.reconstruct_base_portfolio_lineage(
        stored.scenario.manifest,
        pair_input=stored.scenario.pair,
    )

    assert reconstructed == stored.base_lineage
    assert canonical_sha256(reconstructed) == (
        _stored_lineage(stored).base_portfolio_lineage_sha256
    )


def test_fixed_notional_store_rejects_caller_base_not_backed_by_substrate(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    forged = stored.base_lineage.model_copy(
        update={"portfolio_source_payload_sha256": "f" * 64}
    )

    with pytest.raises(
        ShadowContractError,
        match="caller base portfolio lineage differs from persisted substrate",
    ):
        stored.store.persist_pair_bundle(
            stored.scenario.manifest,
            forged,
            canonical_json_bytes(stored.scenario.pair),
            canonical_json_bytes(stored.result),
        )


def test_fixed_notional_store_rejects_missing_candidate_predecessor(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    manifest_hash = str(canonical_sha256(stored.scenario.manifest))
    candidate_path = (
        stored.store.root
        / stored.scenario.manifest.protocol_id
        / manifest_hash
        / "candidate_sets"
        / f"{stored.scenario.pair.candidate_set.candidate_set_id}.json"
    )
    candidate_path.unlink()
    lineage = _stored_lineage(stored)

    with pytest.raises(
        ShadowContractError,
        match="evidence artifact does not exist",
    ):
        stored.store.load_verified_lineage(
            stored.scenario.manifest,
            lineage_id=lineage.lineage_id,
            pair_input_id=stored.scenario.pair.pair_input_id,
            paired_record_id=stored.result.paired_record_id,
        )


def test_fixed_notional_store_rejects_tampered_raw_capture_predecessor(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    manifest_hash = str(canonical_sha256(stored.scenario.manifest))
    raw_path = (
        stored.store.root
        / stored.scenario.manifest.protocol_id
        / manifest_hash
        / "raw_candidate_sets"
        / f"{stored.scenario.pair.raw_capture.raw_capture_id}.json"
    )
    raw_path.write_bytes(raw_path.read_bytes() + b"\n")
    lineage = _stored_lineage(stored)

    with pytest.raises(
        ShadowContractError,
        match="evidence artifact is not canonical JSON",
    ):
        stored.store.load_verified_lineage(
            stored.scenario.manifest,
            lineage_id=lineage.lineage_id,
            pair_input_id=stored.scenario.pair.pair_input_id,
            paired_record_id=stored.result.paired_record_id,
        )


def test_paired_record_cannot_be_persisted_or_loaded_without_exact_input(
    tmp_path: Path,
) -> None:
    scenario_root = tmp_path / "store"
    scenario = _scenario(scenario_root, bar_mode="target")
    result = _evaluate(scenario)
    store = FixedNotionalArtifactStore(scenario_root)

    with pytest.raises(
        ShadowContractError,
        match="fixed-notional reference is unavailable",
    ):
        store.persist_paired_record(
            scenario.manifest,
            canonical_json_bytes(result),
            pair_input_id=scenario.pair.pair_input_id,
        )

    store.persist_input(
        scenario.manifest,
        canonical_json_bytes(scenario.pair),
    )
    store.persist_paired_record(
        scenario.manifest,
        canonical_json_bytes(result),
        pair_input_id=scenario.pair.pair_input_id,
    )
    with pytest.raises(
        ShadowContractError,
        match="requires exact PairInput predecessor",
    ):
        store.load_by_reference(
            scenario.manifest,
            kind="PAIRED_RECORD",
            artifact_id=result.paired_record_id,
        )


def test_lineage_cannot_be_persisted_without_exact_pair_graph(
    tmp_path: Path,
) -> None:
    scenario_root = tmp_path / "store"
    scenario = _scenario(scenario_root, bar_mode="target")
    result = _evaluate(scenario)
    base = _base_portfolio_lineage(scenario)
    lineage = build_fixed_notional_lineage_bundle(
        manifest=scenario.manifest,
        base_portfolio_lineage=base,
        pair_input=scenario.pair,
        paired_record=result,
    )
    store = FixedNotionalArtifactStore(scenario_root)
    store.persist_input(
        scenario.manifest,
        canonical_json_bytes(scenario.pair),
    )

    with pytest.raises(
        ShadowContractError,
        match="fixed-notional reference is unavailable",
    ):
        store.persist_lineage(
            scenario.manifest,
            canonical_json_bytes(lineage),
            pair_input_id=scenario.pair.pair_input_id,
            paired_record_id=result.paired_record_id,
        )


def test_paired_reference_predecessor_drift_is_rejected(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    reference_path = _reference_path(
        stored,
        "PAIRED_RECORD",
        stored.result.paired_record_id,
    )
    payload = json.loads(reference_path.read_text(encoding="utf-8"))
    payload["predecessors"] = [
        (
            {**item, "sha256": "f" * 64}
            if item["name"] == "pair_input_sha256"
            else item
        )
        for item in payload["predecessors"]
    ]
    forged = FixedNotionalGraphReference.model_validate(
        payload
    )
    reference_path.write_bytes(canonical_json_bytes(forged))

    with pytest.raises(
        ShadowContractError,
        match="fixed-notional predecessor reference drift",
    ):
        stored.store.load_pair_bundle(
            stored.scenario.manifest,
            pair_input_id=stored.scenario.pair.pair_input_id,
            paired_record_id=stored.result.paired_record_id,
        )


def test_lineage_reference_names_and_verifies_predecessor_hashes(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    lineage = _stored_lineage(stored)
    assert lineage.evaluation_only is True
    assert lineage.live_authority is False
    assert lineage.affects_execution is False
    assert lineage.affects_ranking is False
    assert lineage.affects_sizing is False
    reference_path = _reference_path(
        stored,
        "LINEAGE",
        lineage.lineage_id,
    )
    reference = FixedNotionalLineageReference.model_validate_json(
        reference_path.read_bytes()
    )
    assert reference.evaluation_only is True
    assert reference.live_authority is False
    assert reference.affects_execution is False
    assert reference.affects_ranking is False
    assert reference.affects_sizing is False
    assert (
        reference.base_portfolio_lineage_sha256,
        reference.pair_input_id,
        reference.pair_input_sha256,
        reference.paired_record_id,
        reference.paired_record_sha256,
    ) == (
        canonical_sha256(stored.base_lineage),
        stored.scenario.pair.pair_input_id,
        canonical_sha256(stored.scenario.pair),
        stored.result.paired_record_id,
        canonical_sha256(stored.result),
    )
    predecessor_names = tuple(item.name for item in reference.predecessors)
    assert predecessor_names == tuple(sorted(predecessor_names))
    assert {
        "base_portfolio_lineage_sha256",
        "fixed_notional_policy_sha256",
        "pair_input_sha256",
        "liquidity_record_sha256",
        "bar_series_sha256",
        "paired_record_sha256",
        "control_lifecycle_sha256s[0]",
        "challenger_lifecycle_sha256s[0]",
        "ordered_holding_sha256s[0]",
        "ordered_cash_flow_sha256s[0]",
    }.issubset(predecessor_names)
    forged = reference.model_copy(
        update={"base_portfolio_lineage_sha256": "f" * 64}
    )
    reference_path.write_bytes(canonical_json_bytes(forged))

    with pytest.raises(
        ShadowContractError,
        match="lineage predecessor reference drift",
    ):
        stored.store.load_verified_lineage(
            stored.scenario.manifest,
            lineage_id=lineage.lineage_id,
            pair_input_id=stored.scenario.pair.pair_input_id,
            paired_record_id=stored.result.paired_record_id,
        )


def test_every_non_lineage_reference_has_ordered_named_predecessors(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    reference_root = _reference_path(
        stored,
        "INPUT",
        stored.scenario.pair.pair_input_id,
    ).parents[1]
    reference_paths = tuple(
        path
        for path in reference_root.glob("*/*.json")
        if path.parent.name != "lineage"
    )

    assert reference_paths
    for path in reference_paths:
        reference = FixedNotionalGraphReference.model_validate_json(
            path.read_bytes()
        )
        assert reference.evaluation_only is True
        assert reference.live_authority is False
        assert reference.affects_execution is False
        assert reference.affects_ranking is False
        assert reference.affects_sizing is False
        names = tuple(item.name for item in reference.predecessors)
        assert names
        assert names == tuple(sorted(names))
        assert len(names) == len(set(names))
        if reference.artifact_kind == "BAR_SERIES":
            assert {
                "corporate_action_events_sha256",
                "corporate_action_policy_sha256",
                "source_sha256",
            }.issubset(names)


def test_fixed_notional_store_rejects_raw_file_tampering(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    input_path = stored.paths["INPUT"][0]
    input_path.write_bytes(input_path.read_bytes() + b"\n")

    with pytest.raises(
        ShadowContractError,
        match="raw-file identity mismatch",
    ):
        stored.store.load_by_reference(
            stored.scenario.manifest,
            kind="INPUT",
            artifact_id=stored.scenario.pair.pair_input_id,
        )


def test_fixed_notional_store_rejects_canonical_model_tampering(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    original_path = stored.paths["POLICY"][0]
    tampered = FrozenFixedNotionalPolicy.model_validate(
        {
            **stored.scenario.policy.model_dump(mode="python"),
            "policy_id": "RS-P2-015-TAMPERED-POLICY",
        }
    )
    tampered_raw = canonical_json_bytes(tampered)
    tampered_raw_hash = hashlib.sha256(tampered_raw).hexdigest()
    tampered_path = original_path.with_name(f"{tampered_raw_hash}.json")
    tampered_path.write_bytes(tampered_raw)
    reference_path = _reference_path(
        stored,
        "POLICY",
        stored.scenario.policy.policy_id,
    )
    _rewrite_reference(
        reference_path,
        artifact_raw_file_sha256=tampered_raw_hash,
        artifact_raw_byte_length=len(tampered_raw),
        artifact_relative_path=(
            tampered_path.resolve()
            .relative_to(stored.store.root)
            .as_posix()
        ),
    )

    with pytest.raises(
        ShadowContractError,
        match="canonical identity mismatch",
    ):
        stored.store.load_by_reference(
            stored.scenario.manifest,
            kind="POLICY",
            artifact_id=stored.scenario.policy.policy_id,
            portfolio_policy=stored.scenario.portfolio_policy,
        )


def test_fixed_notional_loader_rejects_duplicate_keys() -> None:
    _, _, policy, _, _ = _manifest_and_policies()
    raw = canonical_json_bytes(policy)
    duplicate = raw.replace(
        b"{",
        (
            b'{"contract_version":'
            b'"shadow-fixed-notional-policy-v1",'
        ),
        1,
    )

    with pytest.raises(ShadowContractError, match="duplicate JSON key"):
        load_fixed_notional_policy_v1(duplicate)


def test_fixed_notional_store_rejects_reference_byte_length_drift(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    reference_path = _reference_path(
        stored,
        "INPUT",
        stored.scenario.pair.pair_input_id,
    )
    payload = json.loads(reference_path.read_text(encoding="utf-8"))
    _rewrite_reference(
        reference_path,
        artifact_raw_byte_length=payload["artifact_raw_byte_length"] - 1,
    )

    with pytest.raises(
        ShadowContractError,
        match="raw-file identity mismatch",
    ):
        stored.store.load_by_reference(
            stored.scenario.manifest,
            kind="INPUT",
            artifact_id=stored.scenario.pair.pair_input_id,
        )


def test_fixed_notional_store_rejects_reference_path_substitution(
    tmp_path: Path,
) -> None:
    stored = _stored_scenario(tmp_path)
    reference_path = _reference_path(
        stored,
        "INPUT",
        stored.scenario.pair.pair_input_id,
    )
    _rewrite_reference(
        reference_path,
        artifact_relative_path="protocols/wrong/namespace/artifact.json",
    )

    with pytest.raises(
        ShadowContractError,
        match="escaped its exact namespace",
    ):
        stored.store.load_by_reference(
            stored.scenario.manifest,
            kind="INPUT",
            artifact_id=stored.scenario.pair.pair_input_id,
        )


def test_forecasting_shadow_v1_is_not_reinterpreted_as_fixed_notional() -> None:
    raw = json.dumps(
        {
            "contract_version": "shadow-evaluation-v1",
            "evaluation_only": True,
            "live_authority": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    with pytest.raises(
        ShadowContractError,
        match="contract_version must be shadow-fixed-notional-pair-input-v1",
    ):
        load_fixed_notional_pair_input_v1(raw)


def test_real_governance_owner_stop_allows_exact_preclosure_maturation(
    tmp_path: Path,
) -> None:
    store, _, manifest, manifest_raw, approval, _ = (
        _governance_approved_store(tmp_path)
    )
    observation = _governance_observation(manifest)
    store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=str(canonical_sha256(manifest)),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=observation.captured_at,
    )
    closure = _governance_closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=observation.captured_at + timedelta(minutes=1),
    )
    store.append_closure(_governance_raw(closure))

    authorization = store.verify_fixed_notional_maturation_authorization(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=str(canonical_sha256(manifest)),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=closure.effective_at + timedelta(minutes=1),
    )

    assert authorization.closure == closure


def test_real_governance_integrity_stop_blocks_preclosure_maturation(
    tmp_path: Path,
) -> None:
    store, _, manifest, manifest_raw, approval, _ = (
        _governance_approved_store(tmp_path)
    )
    observation = _governance_observation(manifest)
    store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=str(canonical_sha256(manifest)),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=observation.captured_at,
    )
    closure = _governance_closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=observation.captured_at + timedelta(minutes=1),
        reason_code="INTEGRITY_STOP",
        maturation_policy="BLOCK_ALL_MATURATION",
    )
    store.append_closure(_governance_raw(closure))

    with pytest.raises(
        ShadowContractError,
        match="closure blocks all maturation",
    ):
        store.verify_fixed_notional_maturation_authorization(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=str(canonical_sha256(manifest)),
            ledger_id=approval.approval_ledger_id,
            observation=observation,
            attempted_at=closure.effective_at + timedelta(minutes=1),
        )


def test_real_governance_maturation_before_observation_capture_is_blocked(
    tmp_path: Path,
) -> None:
    store, _, manifest, _, approval, _ = _governance_approved_store(
        tmp_path,
    )
    observation = _governance_observation(manifest)
    store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=str(canonical_sha256(manifest)),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=observation.captured_at,
    )

    with pytest.raises(
        ShadowContractError,
        match="maturation predates observation capture",
    ):
        store.verify_fixed_notional_maturation_authorization(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=str(canonical_sha256(manifest)),
            ledger_id=approval.approval_ledger_id,
            observation=observation,
            attempted_at=observation.captured_at - timedelta(seconds=1),
        )


def test_replay_is_idempotent_and_detects_record_drift(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path, bar_mode="target")
    first = _evaluate(scenario)
    second = _evaluate(scenario)
    assert canonical_sha256(first) == canonical_sha256(second)
    assert first.paired_record_id == second.paired_record_id
    assert (
        replay_fixed_notional_pair(
            scenario.pair,
            first,
            authorization_loader=scenario.auth,
            approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
            attempted_at=scenario.pair.frozen_at,
        )
        == first
    )

    drift = PairedFixedNotionalRecord.model_validate(
        {
            **first.model_dump(mode="python"),
            "paired_at": first.paired_at + timedelta(seconds=1),
        }
    )
    with pytest.raises(ShadowContractError, match="deterministic replay"):
        replay_fixed_notional_pair(
            scenario.pair,
            drift,
            authorization_loader=scenario.auth,
            approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
            attempted_at=scenario.pair.frozen_at + timedelta(seconds=1),
        )


def test_all_new_artifacts_are_evaluation_only(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path, bar_mode="target")
    result = _evaluate(scenario)
    artifacts = (
        scenario.policy,
        scenario.liquidity,
        scenario.bar_series,
        scenario.pair,
        result,
        result.control,
        result.challenger,
        *result.control.holding_records,
        *result.control.cash_flow_records,
    )
    for artifact in artifacts:
        assert artifact.evaluation_only is True
        assert artifact.live_authority is False
        assert artifact.affects_execution is False
        assert artifact.affects_ranking is False
        assert artifact.affects_sizing is False


def test_policy_hash_is_identical_in_a_separate_python_process() -> None:
    _, _, policy, _, _ = _manifest_and_policies()
    payload = canonical_json_bytes(policy).decode("utf-8")
    expected = canonical_sha256(policy)
    code = (
        "from core.shadow_protocol.fixed_notional import "
        "FrozenFixedNotionalPolicy\n"
        "from core.shadow_protocol import canonical_sha256\n"
        f"obj=FrozenFixedNotionalPolicy.model_validate_json({payload!r})\n"
        "print(canonical_sha256(obj))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == expected


def test_pair_lifecycle_and_record_hashes_are_identical_cross_process(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path / "scenario", bar_mode="target")
    result = _evaluate(scenario)
    pair_path = tmp_path / "pair.json"
    record_path = tmp_path / "record.json"
    pair_path.write_bytes(canonical_json_bytes(scenario.pair))
    record_path.write_bytes(canonical_json_bytes(result))
    expected = [
        canonical_sha256(scenario.pair),
        canonical_sha256(result.control),
        canonical_sha256(result.challenger),
        canonical_sha256(result),
    ]
    code = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from core.shadow_protocol import canonical_sha256\n"
        "from core.shadow_protocol.fixed_notional import "
        "FixedNotionalPairInput,PairedFixedNotionalRecord\n"
        "pair=FixedNotionalPairInput.model_validate_json("
        "Path(sys.argv[1]).read_bytes())\n"
        "record=PairedFixedNotionalRecord.model_validate_json("
        "Path(sys.argv[2]).read_bytes())\n"
        "print(json.dumps([canonical_sha256(pair),"
        "canonical_sha256(record.control),"
        "canonical_sha256(record.challenger),"
        "canonical_sha256(record)]))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, str(pair_path), str(record_path)],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == expected


def test_fixed_policy_serialized_example_is_canonical_and_owner_bound() -> None:
    _, _, policy, _, _ = _manifest_and_policies()
    payload = json.loads(canonical_json_bytes(policy))

    assert payload["fixed_notional_idr"] == 13_000_000
    assert payload["effective_universe_boundary_note"] == (
        "FN_N1_GT_130000_EXCLUDED_IDENTICALLY"
    )
    assert payload["cash_debit_note"] == (
        "FN_N2_ENTRY_DEBIT_MAY_EXCEED_13000000_BY_SEPARATE_ENTRY_COST"
    )
    assert payload["exit_censoring_note"] == (
        "FN_N3_EXIT_CAPACITY_REASON_COUNT_REQUIRED_IN_RS_P2_018"
    )
    assert payload["methodology_document_sha256"] == METHODOLOGY_SHA256
