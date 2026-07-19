"""Acceptance tests for the evaluation-only RS-P2-016 policy portfolio."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from core.shadow_protocol import (
    CandidateSetManifest,
    CandidateSetStore,
    CandidateSetView,
    ContentHash,
    PortfolioArtifactStore,
    RawCandidateSetCapture,
    ShadowContractError,
    ShadowDecision,
    ShadowProtocolManifest,
    build_frozen_control_portfolio_state,
    canonical_decision_payload_sha256,
    canonical_json_bytes,
    canonical_opportunity_set_sha256,
    canonical_raw_candidate_set_sha256,
    canonical_sha256,
    canonical_view_sha256,
    produce_paired_observation,
)
from core.shadow_protocol.calendar import session_close_at
from core.shadow_protocol.fixed_notional import (
    FixedNotionalMarketBar,
    FixedNotionalPairInput,
    build_fixed_notional_bar_series,
    build_fixed_notional_liquidity_record,
    build_fixed_notional_pair_input,
    derive_paired_fixed_notional_record,
)
from core.shadow_protocol.policy_portfolio import (
    GATE_ORDER,
    POLICY_PORTFOLIO_CONFIG_PATH,
    POLICY_PORTFOLIO_HEAT_UNIT,
    FrozenPolicyPortfolioPolicy,
    PairedPolicyPortfolioSessionRecord,
    PolicyPortfolioCandidateInput,
    PolicyPortfolioSessionInput,
    PolicyPortfolioSessionState,
    PolicyPortfolioTransitionEvent,
    _MutablePath,
    _admission_gate_results,
    _build_admission_plan,
    _cancel_for_regime_reduction,
    _fill_recheck_reasons,
    _update_daily_stop,
    build_policy_candidate_classification,
    build_policy_portfolio_candidate_input,
    build_policy_portfolio_genesis,
    build_policy_portfolio_policy,
    build_policy_portfolio_session_input,
    build_policy_regime_record,
    build_policy_session_liquidity_record,
    canonical_policy_artifact_id,
    derive_policy_portfolio_session,
    policy_portfolio_manifest_parameters,
    replay_policy_portfolio_session,
    verify_policy_portfolio_policy_binding,
    verify_policy_portfolio_session,
)
from core.shadow_protocol.policy_portfolio_store import (
    PolicyPortfolioArtifactReference,
    PolicyPortfolioArtifactStore,
    load_policy_portfolio_lineage_v1,
    load_policy_portfolio_session_input_v1,
    load_policy_portfolio_state_v1,
)
from tests.test_shadow_protocol_p2_014 import (
    HASH_A,
    SIGNAL,
    _cluster,
    _snapshot,
    _source_record,
)
from tests.test_shadow_protocol_p2_015 import (
    OUTCOME_SOURCE,
    _AuthorizationSpy,
    _decision,
    _manifest_and_policies,
    _market_bars,
    _measurement,
    _raw_and_set_with_actions,
    _split_event,
)


def _allocating_decision(
    role: str,
    *,
    rank: int | None = 1,
    fraction: float | None = 0.01,
    allocate: bool = True,
) -> ShadowDecision:
    base = _decision(role)
    updates = {
        "would_allocate": allocate,
        "recorded_rank": rank,
        "recorded_position_fraction": fraction,
        "position_size_basis": (
            "CONTROL_OBSERVED"
            if role == "CONTROL"
            else "COUNTERFACTUAL"
        )
        if allocate
        else "NONE",
    }
    if not allocate:
        updates["recorded_position_fraction"] = None
        updates["recorded_rank"] = None
    draft = base.model_copy(
        update={
            **updates,
            "decision_payload_sha256": HASH_A,
        }
    )
    return ShadowDecision.model_validate(
        {
            **draft.model_dump(
                mode="python",
                exclude={"decision_payload_sha256"},
            ),
            "decision_payload_sha256": canonical_decision_payload_sha256(
                draft
            ),
        }
    )


def _empty_opportunity_set(
    manifest: ShadowProtocolManifest,
    session: date,
) -> tuple[RawCandidateSetCapture, CandidateSetManifest]:
    reason = "NO_POLICY_CANDIDATES"
    opportunity_id = f"OPP-P2-016-EMPTY-{session.isoformat()}"
    source_hash = "d" * 64
    opportunity_hash = canonical_opportunity_set_sha256(
        opportunity_id,
        session,
        (),
        empty_reason=reason,
        candidate_source_definition_sha256=source_hash,
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=(
            manifest.corporate_action_policy_sha256
        ),
    )
    raw_set_hash = canonical_raw_candidate_set_sha256(
        (),
        empty_reason=reason,
    )
    signal_at = session_close_at(session)
    raw = RawCandidateSetCapture(
        raw_capture_id=f"RAW-P2-016-EMPTY-{session.isoformat()}",
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=canonical_sha256(manifest),
        opportunity_set_id=opportunity_id,
        opportunity_set_sha256=opportunity_hash,
        signal_at=signal_at,
        as_of_date=session,
        captured_at=signal_at + timedelta(minutes=1),
        candidate_source_id="POLICY_SESSION_CANDIDATES",
        candidate_source_definition_sha256=source_hash,
        trading_calendar_id=manifest.trading_calendar_id,
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=(
            manifest.corporate_action_policy_sha256
        ),
        raw_candidate_count=0,
        empty_reason=reason,
        raw_candidate_set_sha256=raw_set_hash,
        candidates=(),
    )
    empty_ids: tuple[str, ...] = ()
    control_view = CandidateSetView(
        side="CONTROL",
        input_event_ids=empty_ids,
        dispositions=(),
        view_sha256=canonical_view_sha256(
            "CONTROL",
            empty_ids,
            (),
        ),
    )
    challenger_view = CandidateSetView(
        side="CHALLENGER",
        input_event_ids=empty_ids,
        dispositions=(),
        view_sha256=canonical_view_sha256(
            "CHALLENGER",
            empty_ids,
            (),
        ),
    )
    candidate_set = CandidateSetManifest(
        candidate_set_id=f"SET-P2-016-EMPTY-{session.isoformat()}",
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=canonical_sha256(manifest),
        raw_capture_id=raw.raw_capture_id,
        raw_capture_sha256=canonical_sha256(raw),
        opportunity_set_id=opportunity_id,
        opportunity_set_sha256=opportunity_hash,
        candidate_source_id=raw.candidate_source_id,
        candidate_source_definition_sha256=source_hash,
        trading_calendar_id=manifest.trading_calendar_id,
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=(
            manifest.corporate_action_policy_sha256
        ),
        as_of_date=session,
        captured_at=signal_at + timedelta(minutes=2),
        raw_candidate_count=0,
        empty_reason=reason,
        raw_candidate_set_sha256=raw_set_hash,
        control_view=control_view,
        challenger_view=challenger_view,
    )
    return raw, candidate_set


@dataclass(frozen=True)
class _World:
    root: Path
    manifest: ShadowProtocolManifest
    portfolio_policy: object
    fixed_policy: object
    policy: FrozenPolicyPortfolioPolicy
    calendar: object
    pair: FixedNotionalPairInput
    candidate_input: PolicyPortfolioCandidateInput
    genesis: object
    control_genesis: PolicyPortfolioSessionState
    challenger_genesis: PolicyPortfolioSessionState

    def session_input(
        self,
        *,
        session: date,
        control: PolicyPortfolioSessionState | None = None,
        challenger: PolicyPortfolioSessionState | None = None,
        regime: str = "BULL",
        admission_candidates: (
            tuple[PolicyPortfolioCandidateInput, ...] | None
        ) = None,
        active_candidates: (
            tuple[PolicyPortfolioCandidateInput, ...] | None
        ) = None,
    ) -> PolicyPortfolioSessionInput:
        control_state = control or self.control_genesis
        challenger_state = challenger or self.challenger_genesis
        index = self.calendar.sessions.index(session)
        observed = self.calendar.sessions[index - 1]
        regime_record = build_policy_regime_record(
            manifest=self.manifest,
            trading_calendar=self.calendar,
            observed_session=observed,
            observed_at=session_close_at(observed),
            source_id="REGIME_TEST",
            source_definition_sha256="8" * 64,
            source_record_sha256=(
                f"{index:064x}"[-64:]
            ),
            source_as_of=session_close_at(observed) - timedelta(minutes=1),
            source_expires_at=session_close_at(session) + timedelta(days=1),
            regime=regime,
            reason_codes=(
                ("REGIME_SOURCE_UNAVAILABLE",)
                if regime == "UNKNOWN"
                else ()
            ),
        )
        if session == SIGNAL.date():
            opportunity_raw = self.pair.raw_capture
            opportunity_set = self.pair.candidate_set
            default_admissions = (self.candidate_input,)
        else:
            opportunity_raw, opportunity_set = _empty_opportunity_set(
                self.manifest,
                session,
            )
            default_admissions = ()
        active_ids = {
            item.candidate_input_id
            for state in (control_state, challenger_state)
            for item in (*state.payload.positions, *state.payload.commitments)
        }
        default_active = (
            (self.candidate_input,)
            if self.candidate_input.candidate_input_id in active_ids
            else ()
        )
        return build_policy_portfolio_session_input(
            manifest=self.manifest,
            portfolio_policy=self.portfolio_policy,
            fixed_notional_policy=self.fixed_policy,
            policy=self.policy,
            trading_calendar=self.calendar,
            session=session,
            previous_control_state=control_state,
            previous_challenger_state=challenger_state,
            regime=regime_record,
            opportunity_raw_capture=opportunity_raw,
            opportunity_candidate_set=opportunity_set,
            admission_candidates=(
                default_admissions
                if admission_candidates is None
                else admission_candidates
            ),
            active_candidates=(
                default_active
                if active_candidates is None
                else active_candidates
            ),
            frozen_at=max(
                self.pair.frozen_at,
                opportunity_set.captured_at,
            ),
        )


def _world(
    tmp_path: Path,
    *,
    control_rank: int | None = 1,
    challenger_rank: int | None = 1,
    control_fraction: float | None = 0.01,
    challenger_fraction: float | None = 0.01,
    control_allocate: bool = True,
    challenger_allocate: bool = True,
    bar_mode: str = "timeout",
    entry_adtv_idr: int = 10_000_000_000,
    exit_adtv_idr: int = 20_000_000_000,
    policy_entry_adtv_idr: int | None = None,
    terminal_date: date | None = None,
    action_events=(),
    manifest_enricher=None,
) -> _World:
    (
        base_manifest,
        portfolio_policy,
        fixed_policy,
        calendar,
        action_policy,
    ) = _manifest_and_policies(action_events=action_events)
    if terminal_date is not None:
        base_manifest = ShadowProtocolManifest.model_validate(
            {
                **base_manifest.model_dump(mode="python"),
                "fixed_terminal_date": terminal_date,
            }
        )
    policy = build_policy_portfolio_policy(
        manifest=base_manifest,
        portfolio_policy=portfolio_policy,
        fixed_notional_policy=fixed_policy,
        policy_id="RS-P2-016-POLICY-TEST",
    )
    policy_hash = canonical_sha256(policy)
    assert policy_hash is not None
    final_manifest = ShadowProtocolManifest.model_validate(
        {
            **base_manifest.model_dump(mode="python"),
            "thresholds": (
                *base_manifest.thresholds,
                *policy_portfolio_manifest_parameters(),
            ),
            "control_content_hashes": (
                *base_manifest.control_content_hashes,
                ContentHash(
                    path=POLICY_PORTFOLIO_CONFIG_PATH,
                    sha256=policy_hash,
                    role="CONFIG",
                ),
            ),
            "challenger_content_hashes": (
                *base_manifest.challenger_content_hashes,
                ContentHash(
                    path=POLICY_PORTFOLIO_CONFIG_PATH,
                    sha256=policy_hash,
                    role="CONFIG",
                ),
            ),
        }
    )
    if manifest_enricher is not None:
        final_manifest = manifest_enricher(
            final_manifest,
            portfolio_policy,
            fixed_policy,
            policy,
        )
    verify_policy_portfolio_policy_binding(
        final_manifest,
        portfolio_policy,
        fixed_policy,
        policy,
    )
    snapshot = _snapshot()
    raw_capture, candidate_set = _raw_and_set_with_actions(
        final_manifest,
        (snapshot,),
        action_policy,
    )
    source_record = _source_record(final_manifest)
    state = build_frozen_control_portfolio_state(
        manifest=final_manifest,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        policy=portfolio_policy,
        source_record=source_record,
        state_as_of=SIGNAL - timedelta(minutes=5),
        captured_at=SIGNAL + timedelta(minutes=3, seconds=30),
    )
    CandidateSetStore(tmp_path).persist_raw(raw_capture)
    CandidateSetStore(tmp_path).persist(candidate_set)
    portfolio_store = PortfolioArtifactStore(tmp_path)
    portfolio_store.persist_policy(
        final_manifest,
        canonical_json_bytes(portfolio_policy),
    )
    portfolio_store.persist_source_record(
        final_manifest,
        canonical_json_bytes(source_record),
    )
    portfolio_store.persist_state(
        final_manifest,
        canonical_json_bytes(state),
    )
    auth = _AuthorizationSpy()
    observation = produce_paired_observation(
        manifest=final_manifest,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        candidate=raw_capture.candidates[0],
        frozen_snapshot=snapshot,
        feature_values_sha256=HASH_A,
        portfolio_state_sha256=str(canonical_sha256(state)),
        artifact_store=portfolio_store,
        authorization_loader=auth,
        approval_ledger_id="TEST-ONLY-NON-AUTHORITY",
        control_evaluator=lambda _: _allocating_decision(
            "CONTROL",
            rank=control_rank,
            fraction=control_fraction,
            allocate=control_allocate,
        ),
        challenger_evaluator=lambda _: _allocating_decision(
            "CHALLENGER",
            rank=challenger_rank,
            fraction=challenger_fraction,
            allocate=challenger_allocate,
        ),
        cluster=_cluster(final_manifest),
        captured_at=SIGNAL + timedelta(minutes=4),
    )
    future = tuple(
        item
        for item in calendar.sessions
        if SIGNAL.date() < item <= final_manifest.fixed_terminal_date
    )
    entry_measurement = _measurement(
        fixed_policy,
        calendar,
        role="ENTRY",
        capacity_session=future[0],
        daily_turnover_idr=entry_adtv_idr,
    )
    exit_indices = (1, 2, 3, 5, 10, 15, 16, 17)
    exit_measurements = tuple(
        _measurement(
            fixed_policy,
            calendar,
            role="EXIT",
            capacity_session=future[index],
            daily_turnover_idr=exit_adtv_idr,
        )
        for index in exit_indices
        if index < len(future)
    )
    cutoff = session_close_at(final_manifest.fixed_terminal_date)
    liquidity = build_fixed_notional_liquidity_record(
        manifest=final_manifest,
        observation=observation,
        portfolio_state=state,
        policy=fixed_policy,
        entry_measurement=entry_measurement,
        exit_measurements=exit_measurements,
        captured_at=cutoff,
    )
    bars = _market_bars(
        future,
        mode="timeout" if bar_mode == "gap_loss" else bar_mode,
    )
    if bar_mode == "gap_loss":
        bars = tuple(
            FixedNotionalMarketBar(
                trade_date=item.trade_date,
                open_price_idr=1_000,
                high_price_idr=1_100,
                low_price_idr=900,
                close_price_idr=1_000,
                volume_shares=item.volume_shares,
            )
            if index == 1
            else item
            for index, item in enumerate(bars)
        )
    bar_series = build_fixed_notional_bar_series(
        ticker=observation.ticker,
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=snapshot.snapshot_sha256,
        source_id=OUTCOME_SOURCE.source_id,
        source_definition_sha256=str(canonical_sha256(OUTCOME_SOURCE)),
        source_as_of=session_close_at(bars[-1].trade_date),
        requested_start=SIGNAL.date(),
        requested_end=final_manifest.fixed_terminal_date,
        bars=bars,
        corporate_action_policy=action_policy,
    )
    pair = build_fixed_notional_pair_input(
        manifest=final_manifest,
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
    paired_fixed = derive_paired_fixed_notional_record(pair)
    classification = build_policy_candidate_classification(
        manifest=final_manifest,
        pair_input=pair,
        source_id="CLASSIFICATION_TEST",
        source_definition_sha256="9" * 64,
        source_record_sha256="a" * 64,
        source_as_of=SIGNAL - timedelta(hours=1),
        source_expires_at=SIGNAL + timedelta(days=30),
        sector_taxonomy_id="IDX-TEST-SECTOR-V1",
        sector_id="FINANCIALS",
        cluster_id="BANKS",
    )
    policy_liquidity = []
    for activation_session in future[:3]:
        measurement = (
            entry_measurement
            if (
                activation_session == future[0]
                and policy_entry_adtv_idr is None
            )
            else _measurement(
                fixed_policy,
                calendar,
                role="ENTRY",
                capacity_session=activation_session,
                daily_turnover_idr=(
                    policy_entry_adtv_idr
                    if policy_entry_adtv_idr is not None
                    else entry_adtv_idr
                ),
            )
        )
        policy_liquidity.append(
            build_policy_session_liquidity_record(
                manifest=final_manifest,
                pair_input=pair,
                measurement=measurement,
            )
        )
    candidate_input = build_policy_portfolio_candidate_input(
        pair_input=pair,
        paired_fixed_notional_record=paired_fixed,
        classification=classification,
        entry_liquidity=policy_liquidity,
    )
    signal_index = calendar.sessions.index(SIGNAL.date())
    genesis_session = calendar.sessions[signal_index - 1]
    genesis, control_genesis, challenger_genesis = (
        build_policy_portfolio_genesis(
            manifest=final_manifest,
            portfolio_policy=portfolio_policy,
            fixed_notional_policy=fixed_policy,
            policy=policy,
            trading_calendar=calendar,
            genesis_session=genesis_session,
            genesis_at=session_close_at(genesis_session),
        )
    )
    return _World(
        root=tmp_path,
        manifest=final_manifest,
        portfolio_policy=portfolio_policy,
        fixed_policy=fixed_policy,
        policy=policy,
        calendar=calendar,
        pair=pair,
        candidate_input=candidate_input,
        genesis=genesis,
        control_genesis=control_genesis,
        challenger_genesis=challenger_genesis,
    )


def _signal_record(world: _World) -> PairedPolicyPortfolioSessionRecord:
    return derive_policy_portfolio_session(
        world.session_input(session=SIGNAL.date())
    )


def _next_record(
    world: _World,
    previous: PairedPolicyPortfolioSessionRecord,
    *,
    offset: int = 1,
    regime: str = "BULL",
) -> PairedPolicyPortfolioSessionRecord:
    index = world.calendar.sessions.index(previous.session)
    session = world.calendar.sessions[index + offset]
    return derive_policy_portfolio_session(
        world.session_input(
            session=session,
            control=previous.control_state,
            challenger=previous.challenger_state,
            regime=regime,
        )
    )


def _events(
    record: PairedPolicyPortfolioSessionRecord,
    role: str = "CONTROL",
) -> tuple[PolicyPortfolioTransitionEvent, ...]:
    return (
        record.control_transition.events
        if role == "CONTROL"
        else record.challenger_transition.events
    )


def _candidate_with_classification(
    world: _World,
    classification,
) -> PolicyPortfolioCandidateInput:
    return build_policy_portfolio_candidate_input(
        pair_input=world.pair,
        paired_fixed_notional_record=(
            world.candidate_input.paired_fixed_notional_record
        ),
        classification=classification,
        entry_liquidity=world.candidate_input.entry_liquidity,
    )


def _clone_commitment(
    original,
    *,
    ticker: str,
    rank: int,
    source_row: int,
):
    values = {
        **original.model_dump(mode="python"),
        "candidate_input_id": f"PPCAND-{ticker}",
        "candidate_input_sha256": f"{rank + 1:064x}"[-64:],
        "pair_input_sha256": f"{rank + 11:064x}"[-64:],
        "decision_sha256": f"{rank + 21:064x}"[-64:],
        "raw_event_id": f"RAW-{ticker}",
        "ticker": ticker,
        "source_row_number": source_row,
        "recorded_rank": rank,
        "priority_key": (rank, source_row, ticker),
    }
    values["commitment_id"] = canonical_policy_artifact_id(
        "PPCOM",
        {
            "decision_role": values["decision_role"],
            "candidate_input_sha256": values[
                "candidate_input_sha256"
            ],
            "decision_sha256": values["decision_sha256"],
            "created_session": values["created_session"].isoformat(),
            "priority_key": list(values["priority_key"]),
            "planned_gross_idr": values["planned_gross_idr"],
            "reserved_debit_idr": values["reserved_debit_idr"],
            "planned_risk_idr": values["planned_risk_idr"],
        },
    )
    return original.__class__.model_validate(values)


def test_pp_a1_policy_config_uses_starting_capital_heat_unit(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    assert world.policy.portfolio_heat_unit == POLICY_PORTFOLIO_HEAT_UNIT
    assert world.policy.max_portfolio_heat_fraction == 0.013


def test_pp_a1_manifest_heat_parameter_uses_starting_capital_unit(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    parameter = next(
        item
        for item in world.manifest.thresholds
        if item.name == "max_portfolio_heat_fraction"
    )
    assert parameter.unit == "fraction_of_starting_capital"


def test_pp_a1_manifest_rejects_fraction_of_nav_heat_unit(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    thresholds = tuple(
        item.__class__.model_validate(
            {
                **item.model_dump(mode="python"),
                "unit": "fraction_of_NAV",
            }
        )
        if item.name == "max_portfolio_heat_fraction"
        else item
        for item in world.manifest.thresholds
    )
    drifted = ShadowProtocolManifest.model_validate(
        {
            **world.manifest.model_dump(mode="python"),
            "thresholds": thresholds,
        }
    )
    with pytest.raises(
        ShadowContractError,
        match="portfolio parameter max_portfolio_heat_fraction differs",
    ):
        verify_policy_portfolio_policy_binding(
            drifted,
            world.portfolio_policy,
            world.fixed_policy,
            world.policy,
        )


def test_genesis_is_one_identical_empty_100m_hash(tmp_path: Path) -> None:
    world = _world(tmp_path)
    assert world.control_genesis.payload_sha256 == (
        world.challenger_genesis.payload_sha256
    )
    payload = world.control_genesis.payload
    assert payload.settled_cash_idr == 100_000_000
    assert payload.positions == ()
    assert payload.commitments == ()
    assert payload.purchase_payables == ()
    assert payload.sale_receivables == ()
    assert world.control_genesis.genesis_sha256 == canonical_sha256(
        world.genesis
    )


def test_policy_admission_records_exact_ten_gate_order(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    record = _signal_record(world)
    accepted = next(
        item
        for item in _events(record)
        if item.event_type == "ADMISSION_ACCEPTED"
    )
    assert tuple(item.gate_id for item in accepted.gate_results) == GATE_ORDER
    assert all(item.passed for item in accepted.gate_results)
    commitment = record.control_state.payload.commitments[0]
    assert commitment.requested_notional_idr == 1_000_000
    assert (
        world.candidate_input.paired_fixed_notional_record.control.target_sleeve_idr
        == 13_000_000
    )
    assert commitment.planned_gross_idr == 1_000_000
    assert commitment.quantity_shares == 100
    assert commitment.reserved_debit_idr > commitment.planned_gross_idr
    assert (
        record.control_state.payload.gross_exposure_idr
        == commitment.planned_gross_idr
    )


def test_duplicate_priority_and_hard_sector_cluster_caps_fail_closed(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    session_input = world.session_input(session=SIGNAL.date())
    genesis_path = _MutablePath.from_state(
        "CONTROL",
        world.control_genesis,
    )
    ambiguous = _build_admission_plan(
        genesis_path,
        session_input,
        world.candidate_input,
        duplicate_rank=True,
    )
    ambiguous_gates = _admission_gate_results(
        genesis_path,
        session_input,
        ambiguous,
    )
    assert "NOT_ESTIMABLE_PRIORITY" in (
        ambiguous_gates[2].reason_codes
    )
    genesis_path.reentry_blocks["BBCA"] = SIGNAL.date()
    blocked_reentry = _build_admission_plan(
        genesis_path,
        session_input,
        world.candidate_input,
        duplicate_rank=False,
    )
    reentry_gates = _admission_gate_results(
        genesis_path,
        session_input,
        blocked_reentry,
    )
    assert "DUPLICATE_OR_REENTRY_BLOCKED" in (
        reentry_gates[2].reason_codes
    )

    signal = derive_policy_portfolio_session(session_input)
    original = signal.control_state.payload.commitments[0]
    mutable = _MutablePath.from_state("CONTROL", signal.control_state)
    mutable.commitments.extend(
        (
            _clone_commitment(
                original,
                ticker="BBRI",
                rank=2,
                source_row=2,
            ),
            _clone_commitment(
                original,
                ticker="BMRI",
                rank=3,
                source_row=3,
            ),
        )
    )
    reasons = _fill_recheck_reasons(
        mutable,
        session_input,
        original,
        world.candidate_input,
        gross=original.planned_gross_idr,
        entry_cost=original.planned_entry_cost_idr,
        planned_risk=original.planned_risk_idr,
    )
    assert "SECTOR_LIMIT" in reasons
    assert "CLUSTER_LIMIT" in reasons


def test_fill_recheck_reapplies_loss_heat_gross_and_cash_limits(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    signal = _signal_record(world)
    commitment = signal.control_state.payload.commitments[0]
    mutable = _MutablePath.from_state("CONTROL", signal.control_state)
    reasons = _fill_recheck_reasons(
        mutable,
        world.session_input(session=SIGNAL.date()),
        commitment,
        world.candidate_input,
        gross=96_000_000,
        entry_cost=1,
        planned_risk=2_100_000,
    )
    assert {
        "PER_POSITION_RISK_LIMIT",
        "TOTAL_LOSS_BUDGET_LIMIT",
        "PORTFOLIO_HEAT_LIMIT",
        "GROSS_EXPOSURE_LIMIT",
        "MINIMUM_CASH_LIMIT",
    }.issubset(set(reasons))


def test_common_input_parity_allows_independent_side_state_divergence(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path, challenger_allocate=False)
    record = _signal_record(world)
    assert record.shared_input_parity is True
    assert len(record.control_state.payload.commitments) == 1
    assert record.challenger_state.payload.commitments == ()
    assert record.control_state_sha256 != record.challenger_state_sha256


def test_active_old_cohort_coexists_with_explicit_empty_current_set(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    signal = _signal_record(world)
    next_session = world.calendar.sessions[
        world.calendar.sessions.index(signal.session) + 1
    ]
    session_input = world.session_input(
        session=next_session,
        control=signal.control_state,
        challenger=signal.challenger_state,
    )
    assert session_input.opportunity_raw_capture.raw_candidate_count == 0
    assert (
        session_input.opportunity_candidate_set.empty_reason
        == "NO_POLICY_CANDIDATES"
    )
    assert session_input.admission_candidate_ids == ()
    assert tuple(
        item.candidate_input_id for item in session_input.candidates
    ) == (world.candidate_input.candidate_input_id,)
    assert derive_policy_portfolio_session(session_input).session == (
        next_session
    )


@pytest.mark.parametrize(
    ("rank", "fraction", "reason"),
    (
        (None, 0.01, "NOT_ESTIMABLE_PRIORITY"),
        (1, 0.0, "NOT_ESTIMABLE_POLICY_SIZE"),
        (1, 0.0001, "NOT_ESTIMABLE_POLICY_SIZE"),
    ),
)
def test_allocate_requires_recorded_fraction_rank_and_nonzero_lot(
    tmp_path: Path,
    rank: int | None,
    fraction: float | None,
    reason: str,
) -> None:
    world = _world(
        tmp_path,
        control_rank=rank,
        control_fraction=fraction,
        challenger_allocate=False,
    )
    record = _signal_record(world)
    rejected = next(
        item
        for item in _events(record)
        if item.event_type == "ADMISSION_REJECTED"
    )
    assert reason in rejected.reason_codes
    assert record.control_state.payload.commitments == ()


def test_commitment_fills_at_policy_quantity_and_creates_t_plus_two_payable(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    signal = _signal_record(world)
    filled = _next_record(world, signal)
    payload = filled.control_state.payload
    assert payload.commitments == ()
    assert len(payload.positions) == 1
    assert payload.positions[0].entry_quantity_shares == 100
    assert (
        payload.gross_exposure_idr
        == payload.positions[0].marked_value_idr
    )
    assert len(payload.purchase_payables) == 1
    leg = payload.purchase_payables[0]
    session_index = world.calendar.sessions.index(filled.session)
    assert leg.settlement_session == world.calendar.sessions[session_index + 2]
    assert payload.settled_cash_idr == 100_000_000
    assert payload.buying_power_idr < 99_000_000


def test_t_plus_two_purchase_payable_posts_only_on_frozen_session(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    signal = _signal_record(world)
    fill = _next_record(world, signal)
    one_day = _next_record(world, fill)
    assert len(one_day.control_state.payload.purchase_payables) == 1
    assert one_day.control_state.payload.settled_cash_idr == 100_000_000
    two_day = _next_record(world, one_day)
    assert two_day.control_state.payload.purchase_payables == ()
    assert two_day.control_state.payload.settled_cash_idr < 100_000_000
    assert any(
        item.event_type == "SETTLEMENT_POSTED"
        for item in _events(two_day)
    )


def test_supported_split_adjusts_quantity_mark_and_journal_without_crash(
    tmp_path: Path,
) -> None:
    split_session = date(2026, 7, 21)
    split = _split_event(
        event_id="P2-016-SPLIT-2-FOR-1",
        effective_date=split_session,
        published_at=SIGNAL + timedelta(hours=1),
    )
    world = _world(tmp_path, action_events=(split,))
    fill = _next_record(world, _signal_record(world))
    split_record = _next_record(world, fill)
    split_event = next(
        item
        for item in _events(split_record)
        if item.event_type == "CORPORATE_ACTION_SPLIT"
    )
    assert split_event.quantity_delta_shares == 100
    assert split_event.gross_exposure_delta_idr == 0
    assert split_record.control_state.payload.path_status == "ACTIVE"
    assert verify_policy_portfolio_session(
        world.session_input(
            session=split_session,
            control=fill.control_state,
            challenger=fill.challenger_state,
        ),
        split_record,
    ) == split_record


def test_sale_receivable_is_not_deployable_until_exact_t_plus_two(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path, bar_mode="target")
    signal = _signal_record(world)
    fill = _next_record(world, signal)
    exit_record = _next_record(world, fill)
    exit_payload = exit_record.control_state.payload
    assert len(exit_payload.sale_receivables) == 1
    receivable = exit_payload.sale_receivables[0]
    exit_index = world.calendar.sessions.index(exit_record.session)
    assert receivable.settlement_session == (
        world.calendar.sessions[exit_index + 2]
    )
    assert exit_payload.buying_power_idr == (
        exit_payload.settled_cash_idr
        - exit_payload.purchase_payable_idr
    )
    one_day = _next_record(world, exit_record)
    assert len(one_day.control_state.payload.sale_receivables) == 1
    two_day = _next_record(world, one_day)
    assert two_day.control_state.payload.sale_receivables == ()
    assert any(
        item.event_type == "SETTLEMENT_POSTED"
        and "SALE_RECEIVABLE_POSTED" in item.reason_codes
        for item in _events(two_day)
    )


def test_unknown_regime_blocks_new_allocation(tmp_path: Path) -> None:
    world = _world(tmp_path)
    record = derive_policy_portfolio_session(
        world.session_input(session=SIGNAL.date(), regime="UNKNOWN")
    )
    rejected = next(
        item
        for item in _events(record)
        if item.event_type == "ADMISSION_REJECTED"
    )
    assert "NOT_ESTIMABLE_REGIME" in rejected.reason_codes
    assert record.control_state.payload.commitments == ()


def test_exit_liquidity_cannot_be_relabelled_as_policy_entry_evidence(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    activation = world.calendar.sessions[
        world.calendar.sessions.index(SIGNAL.date()) + 1
    ]
    exit_measurement = _measurement(
        world.fixed_policy,
        world.calendar,
        role="EXIT",
        capacity_session=activation,
    )
    with pytest.raises(
        ShadowContractError,
        match="must use an ENTRY measurement",
    ):
        build_policy_session_liquidity_record(
            manifest=world.manifest,
            pair_input=world.pair,
            measurement=exit_measurement,
        )


@pytest.mark.parametrize(
    ("source_as_of", "expires_at", "reason"),
    (
        (
            SIGNAL - timedelta(hours=1),
            SIGNAL - timedelta(minutes=1),
            "CLASSIFICATION_STALE_AT_SIGNAL",
        ),
        (
            SIGNAL + timedelta(minutes=1),
            SIGNAL + timedelta(days=1),
            "CLASSIFICATION_SOURCE_AFTER_SIGNAL",
        ),
    ),
)
def test_noncausal_or_stale_classification_is_fail_closed(
    tmp_path: Path,
    source_as_of,
    expires_at,
    reason: str,
) -> None:
    world = _world(tmp_path)
    classification = build_policy_candidate_classification(
        manifest=world.manifest,
        pair_input=world.pair,
        source_id="CLASSIFICATION_CAUSALITY_TEST",
        source_definition_sha256="9" * 64,
        source_record_sha256="b" * 64,
        source_as_of=source_as_of,
        source_expires_at=expires_at,
        sector_taxonomy_id="IDX-TEST-SECTOR-V1",
        sector_id="FINANCIALS",
        cluster_id="BANKS",
    )
    assert classification.status == "NOT_ESTIMABLE"
    assert classification.sector_id is None
    assert classification.cluster_id is None
    assert reason in classification.reason_codes
    candidate = _candidate_with_classification(world, classification)
    record = derive_policy_portfolio_session(
        world.session_input(
            session=SIGNAL.date(),
            admission_candidates=(candidate,),
        )
    )
    rejected = next(
        item
        for item in _events(record)
        if item.event_type == "ADMISSION_REJECTED"
    )
    assert "NOT_ESTIMABLE_CLASSIFICATION" in rejected.reason_codes


def test_fill_recheck_rejects_classification_expired_after_signal(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    classification = build_policy_candidate_classification(
        manifest=world.manifest,
        pair_input=world.pair,
        source_id="CLASSIFICATION_FILL_RECHECK_TEST",
        source_definition_sha256="9" * 64,
        source_record_sha256="c" * 64,
        source_as_of=SIGNAL - timedelta(hours=1),
        source_expires_at=SIGNAL + timedelta(hours=1),
        sector_taxonomy_id="IDX-TEST-SECTOR-V1",
        sector_id="FINANCIALS",
        cluster_id="BANKS",
    )
    candidate = _candidate_with_classification(world, classification)
    signal = derive_policy_portfolio_session(
        world.session_input(
            session=SIGNAL.date(),
            admission_candidates=(candidate,),
        )
    )
    next_session = world.calendar.sessions[
        world.calendar.sessions.index(SIGNAL.date()) + 1
    ]
    filled = derive_policy_portfolio_session(
        world.session_input(
            session=next_session,
            control=signal.control_state,
            challenger=signal.challenger_state,
            admission_candidates=(),
            active_candidates=(candidate,),
        )
    )
    canceled = next(
        item
        for item in _events(filled)
        if item.event_type == "COMMITMENT_CANCELED"
    )
    assert "NOT_ESTIMABLE_CLASSIFICATION" in canceled.reason_codes
    assert filled.control_state.payload.positions == ()


def test_regime_downshift_does_not_force_existing_position_exit(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    fill = _next_record(world, _signal_record(world))
    downshift = _next_record(world, fill, regime="BEAR_STRESS")
    assert len(downshift.control_state.payload.positions) == 1
    assert not any(
        item.event_type == "EXIT_FILLED" for item in _events(downshift)
    )


def test_regime_reduction_cancels_pending_without_forced_exit(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    signal = _signal_record(world)
    assert len(signal.control_state.payload.commitments) == 1
    reduced = _next_record(world, signal, regime="UNKNOWN")
    assert reduced.control_state.payload.commitments == ()
    assert any(
        item.event_type == "COMMITMENT_CANCELED"
        and "REGIME_LIMIT_REDUCTION" in item.reason_codes
        for item in _events(reduced)
    )
    assert not any(
        item.event_type == "EXIT_FILLED" for item in _events(reduced)
    )


def test_regime_reduction_keeps_highest_priority_pending(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    signal = _signal_record(world)
    original = signal.control_state.payload.commitments[0]
    mutable = _MutablePath.from_state("CONTROL", signal.control_state)
    mutable.commitments.extend(
        (
            _clone_commitment(
                original,
                ticker="BBRI",
                rank=2,
                source_row=2,
            ),
            _clone_commitment(
                original,
                ticker="BMRI",
                rank=3,
                source_row=3,
            ),
        )
    )
    _cancel_for_regime_reduction(
        mutable,
        world.session_input(session=SIGNAL.date(), regime="SIDEWAYS"),
    )
    assert tuple(
        item.priority_key for item in mutable.commitments
    ) == tuple(
        sorted(item.priority_key for item in mutable.commitments)
    )
    assert [item.ticker for item in mutable.commitments] == [
        "BBCA",
        "BBRI",
    ]
    canceled = tuple(
        item
        for item in mutable.events
        if item.event_type == "COMMITMENT_CANCELED"
    )
    assert len(canceled) == 1
    assert canceled[0].ticker == "BMRI"
    assert canceled[0].occurred_at == (
        datetime.combine(
            SIGNAL.date(),
            time(9, 0),
            tzinfo=SIGNAL.tzinfo,
        )
    )


def test_fill_rechecks_policy_session_capacity_and_cancels_fail_closed(
    tmp_path: Path,
) -> None:
    world = _world(
        tmp_path,
        policy_entry_adtv_idr=100_000_000,
    )
    signal = _signal_record(world)
    assert len(signal.control_state.payload.commitments) == 1
    fill_attempt = _next_record(world, signal)
    assert fill_attempt.control_state.payload.positions == ()
    assert fill_attempt.control_state.payload.commitments == ()
    assert any(
        item.event_type == "COMMITMENT_CANCELED"
        and "NOT_ESTIMABLE_ENTRY_CAPACITY" in item.reason_codes
        for item in _events(fill_attempt)
    )


def test_daily_realized_loss_stop_latches_after_adverse_gap(
    tmp_path: Path,
) -> None:
    world = _world(
        tmp_path,
        control_fraction=0.06,
        challenger_allocate=False,
        bar_mode="gap_loss",
    )
    fill = _next_record(world, _signal_record(world))
    assert len(fill.control_state.payload.positions) == 1
    loss = _next_record(world, fill)
    payload = loss.control_state.payload
    assert payload.positions == ()
    assert payload.realized_pnl_today_idr < -3_000_000
    assert payload.daily_stop_latched is True
    assert any(
        item.event_type == "DAILY_STOP_LATCHED"
        for item in _events(loss)
    )
    reset = _next_record(world, loss)
    assert reset.control_state.payload.realized_pnl_today_idr == 0
    assert reset.control_state.payload.daily_stop_latched is False


def test_daily_stop_at_exact_threshold_cancels_all_pending(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    signal = _signal_record(world)
    mutable = _MutablePath.from_state("CONTROL", signal.control_state)
    mutable.realized_pnl = -3_000_000
    _update_daily_stop(
        mutable,
        world.session_input(session=SIGNAL.date()),
        "e" * 64,
        occurred_at=session_close_at(SIGNAL.date()),
    )
    assert mutable.daily_stop is True
    assert mutable.commitments == []
    assert [item.event_type for item in mutable.events] == [
        "DAILY_STOP_LATCHED",
        "COMMITMENT_CANCELED",
    ]
    assert all(
        item.occurred_at == session_close_at(SIGNAL.date())
        for item in mutable.events
    )


def test_post_fill_exit_capacity_failure_retains_holding_and_freezes_side(
    tmp_path: Path,
) -> None:
    world = _world(
        tmp_path,
        bar_mode="target",
        exit_adtv_idr=100_000_000,
    )
    fill = _next_record(world, _signal_record(world))
    failed_exit = _next_record(world, fill)
    payload = failed_exit.control_state.payload
    assert len(payload.positions) == 1
    assert payload.path_status == "NOT_ESTIMABLE_FROM_SESSION"
    assert "NOT_ESTIMABLE_EXIT_CAPACITY" in payload.path_reason_codes
    assert payload.sale_receivables == ()
    assert not any(
        item.event_type == "EXIT_FILLED" for item in _events(failed_exit)
    )


def test_not_estimable_side_preserves_last_verified_economic_state(
    tmp_path: Path,
) -> None:
    world = _world(
        tmp_path,
        bar_mode="target",
        exit_adtv_idr=1_000_000_000,
    )
    fill = _next_record(world, _signal_record(world))
    failed = _next_record(world, fill)
    before = failed.control_state.payload
    assert before.path_status == "NOT_ESTIMABLE_FROM_SESSION"
    position_hash = canonical_sha256(before.positions[0])
    propagated = _next_record(world, failed)
    after = propagated.control_state.payload
    assert canonical_sha256(after.positions[0]) == position_hash
    assert after.settled_cash_idr == before.settled_cash_idr
    assert after.purchase_payables == before.purchase_payables
    assert after.sale_receivables == before.sale_receivables
    assert after.commitments == before.commitments
    assert after.gross_exposure_idr == before.gross_exposure_idr
    assert after.planned_risk_idr == before.planned_risk_idr
    assert not any(
        item.event_type
        in {
            "ENTRY_FILLED",
            "EXIT_FILLED",
            "MARK_UPDATED",
            "SETTLEMENT_POSTED",
        }
        for item in _events(propagated)
    )


def test_policy_heat_uses_starting_capital_when_accounting_equity_differs(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    fill = _next_record(world, _signal_record(world))
    payload = fill.control_state.payload
    assert payload.accounting_equity_idr != 100_000_000
    assert payload.planned_risk_idr == 100_000
    assert payload.portfolio_heat == 0.001


def test_policy_heat_gate_rejects_one_lot_over_starting_capital_limit(
    tmp_path: Path,
) -> None:
    world = _world(
        tmp_path,
        control_fraction=0.14,
        challenger_allocate=False,
    )
    record = _signal_record(world)
    rejected = next(
        item
        for item in _events(record)
        if item.event_type == "ADMISSION_REJECTED"
    )
    assert "PORTFOLIO_HEAT_LIMIT" in rejected.reason_codes
    heat_gate = rejected.gate_results[7]
    assert heat_gate.observed_integer_idr == 1_400_000
    assert heat_gate.threshold_integer_idr == 1_300_000
    assert record.control_state.payload.commitments == ()


def test_terminal_runway_rejects_without_synthetic_liquidation(
    tmp_path: Path,
) -> None:
    world = _world(
        tmp_path,
        challenger_allocate=False,
        # The RS-P2-015 lifecycle can mature by this terminal, but the
        # additional T+2 runway required by RS-P2-016 cannot.
        terminal_date=date(2026, 8, 12),
    )
    record = _signal_record(world)
    rejected = next(
        item
        for item in _events(record)
        if item.event_type == "ADMISSION_REJECTED"
    )
    assert "INSUFFICIENT_FIXED_TERMINAL_RUNWAY" in rejected.reason_codes
    assert record.control_state.payload.positions == ()
    assert record.control_state.payload.commitments == ()
    assert not any(
        item.event_type == "EXIT_FILLED" for item in _events(record)
    )


def test_replay_is_idempotent_and_rejects_one_idr_state_drift(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    session_input = world.session_input(session=SIGNAL.date())
    record = derive_policy_portfolio_session(session_input)
    replayed = replay_policy_portfolio_session(session_input, record)
    assert canonical_sha256(replayed) == canonical_sha256(record)
    payload = record.control_state.payload
    with pytest.raises(ValidationError, match="reserved-cash total mismatch"):
        payload.__class__.model_validate(
            {
                **payload.model_dump(mode="python"),
                "reserved_cash_idr": payload.reserved_cash_idr + 1,
            }
        )


def test_session_input_rejects_nonadjacent_predecessor_and_preclose_freeze(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    signal_index = world.calendar.sessions.index(SIGNAL.date())
    skipped_session = world.calendar.sessions[signal_index + 1]
    with pytest.raises(
        ShadowContractError,
        match="session input is invalid",
    ):
        world.session_input(session=skipped_session)

    valid = world.session_input(session=SIGNAL.date())
    with pytest.raises(
        ValidationError,
        match="cannot freeze before session close",
    ):
        valid.__class__.model_validate(
            {
                **valid.model_dump(mode="python"),
                "frozen_at": session_close_at(valid.session)
                - timedelta(seconds=1),
            }
        )


def test_session_input_rejects_transition_after_fixed_terminal(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    valid = world.session_input(session=SIGNAL.date())
    after_terminal = next(
        item
        for item in world.calendar.sessions
        if item > world.manifest.fixed_terminal_date
    )
    with pytest.raises(
        ValidationError,
        match="follows the fixed terminal",
    ):
        valid.__class__.model_validate(
            {
                **valid.model_dump(mode="python"),
                "session": after_terminal,
            }
        )


def test_transition_journal_reconciles_every_integer_resource(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    records = []
    signal = _signal_record(world)
    records.append(
        (
            world.control_genesis.payload,
            signal.control_transition,
            signal.control_state.payload,
        )
    )
    fill = _next_record(world, signal)
    records.append(
        (
            signal.control_state.payload,
            fill.control_transition,
            fill.control_state.payload,
        )
    )
    for pre, transition, post in records:
        events = transition.events
        assert pre.settled_cash_idr + sum(
            item.settled_cash_delta_idr for item in events
        ) == post.settled_cash_idr
        assert pre.purchase_payable_idr + sum(
            item.purchase_payable_delta_idr for item in events
        ) == post.purchase_payable_idr
        assert pre.reserved_cash_idr + sum(
            item.reserved_cash_delta_idr for item in events
        ) == post.reserved_cash_idr
        assert pre.planned_risk_idr + sum(
            item.planned_risk_delta_idr for item in events
        ) == post.planned_risk_idr
        assert pre.gross_exposure_idr + sum(
            item.gross_exposure_delta_idr for item in events
        ) == post.gross_exposure_idr


def test_transition_event_hash_drift_fails_exact_replay(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    session_input = world.session_input(session=SIGNAL.date())
    record = derive_policy_portfolio_session(session_input)
    event = record.control_transition.events[-1]
    with pytest.raises(ValidationError, match="transition-event ID mismatch"):
        event.__class__.model_validate(
            {
                **event.model_dump(mode="python"),
                "reserved_cash_delta_idr": (
                    event.reserved_cash_delta_idr + 1
                ),
            }
        )
    assert verify_policy_portfolio_session(session_input, record) == record


def test_all_policy_portfolio_artifacts_are_evaluation_only(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    session_input = world.session_input(session=SIGNAL.date())
    record = derive_policy_portfolio_session(session_input)
    filled = _next_record(world, record)
    artifacts = (
        world.policy,
        world.genesis,
        world.candidate_input,
        world.candidate_input.classification,
        *world.candidate_input.entry_liquidity,
        session_input.opportunity_raw_capture,
        session_input.opportunity_candidate_set,
        session_input,
        session_input.regime,
        record,
        record.control_transition,
        record.control_state,
        record.challenger_transition,
        record.challenger_state,
        *record.control_transition.events,
        *record.challenger_transition.events,
        *record.control_state.payload.commitments,
        *record.challenger_state.payload.commitments,
        filled,
        filled.control_transition,
        filled.challenger_transition,
        filled.control_state,
        filled.challenger_state,
        *filled.control_state.payload.positions,
        *filled.challenger_state.payload.positions,
        *filled.control_state.payload.purchase_payables,
        *filled.challenger_state.payload.purchase_payables,
    )
    for artifact in artifacts:
        assert artifact.evaluation_only is True
        assert artifact.live_authority is False
        assert artifact.affects_execution is False
        assert artifact.affects_ranking is False
        assert artifact.affects_sizing is False


def test_policy_and_genesis_hashes_are_identical_cross_process(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    policy_json = canonical_json_bytes(world.policy).decode("utf-8")
    genesis_json = canonical_json_bytes(world.genesis).decode("utf-8")
    script = (
        "import json\n"
        "from core.shadow_protocol.contracts import canonical_sha256\n"
        "from core.shadow_protocol.policy_portfolio import "
        "FrozenPolicyPortfolioPolicy, PolicyPortfolioGenesisRecord\n"
        f"p=FrozenPolicyPortfolioPolicy.model_validate_json({policy_json!r})\n"
        f"g=PolicyPortfolioGenesisRecord.model_validate_json({genesis_json!r})\n"
        "print(json.dumps([canonical_sha256(p),canonical_sha256(g)]))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == [
        canonical_sha256(world.policy),
        canonical_sha256(world.genesis),
    ]


def _stored_bundle(tmp_path: Path):
    root = tmp_path / "store"
    world = _world(root)
    session_input = world.session_input(session=SIGNAL.date())
    record = derive_policy_portfolio_session(session_input)
    store = PolicyPortfolioArtifactStore(root)
    paths = store.persist_session_bundle(
        world.manifest,
        world.genesis,
        canonical_json_bytes(session_input),
        canonical_json_bytes(record),
    )
    return world, session_input, record, store, paths


def test_policy_portfolio_store_replay_is_idempotent_and_reconstructable(
    tmp_path: Path,
) -> None:
    world, session_input, record, store, paths = _stored_bundle(tmp_path)
    again = store.persist_session_bundle(
        world.manifest,
        world.genesis,
        canonical_json_bytes(session_input),
        canonical_json_bytes(record),
    )
    assert again == paths
    loaded_input, loaded_record = store.load_session_bundle(
        world.manifest,
        genesis_id=world.genesis.genesis_id,
        session_input_id=session_input.session_input_id,
        paired_session_id=record.paired_session_id,
    )
    assert canonical_sha256(loaded_input) == canonical_sha256(session_input)
    assert canonical_sha256(loaded_record) == canonical_sha256(record)
    replayed = store.replay_session_bundle(
        world.manifest,
        genesis_id=world.genesis.genesis_id,
        session_input_id=session_input.session_input_id,
        paired_session_id=record.paired_session_id,
    )
    assert canonical_sha256(replayed) == canonical_sha256(record)


def test_store_references_and_lineage_preserve_evaluation_only_authority(
    tmp_path: Path,
) -> None:
    _, _, _, store, paths = _stored_bundle(tmp_path)
    lineage = load_policy_portfolio_lineage_v1(
        paths["LINEAGE"][0].read_bytes()
    )
    artifacts = [lineage]
    for reference_path in (
        store.root / "protocols"
    ).rglob("policy_portfolio/refs/*/*.json"):
        artifacts.append(
            PolicyPortfolioArtifactReference.model_validate_json(
                reference_path.read_bytes()
            )
        )
    assert len(artifacts) > 1
    for artifact in artifacts:
        assert artifact.evaluation_only is True
        assert artifact.live_authority is False
        assert artifact.affects_execution is False
        assert artifact.affects_ranking is False
        assert artifact.affects_sizing is False


def test_policy_portfolio_store_rejects_raw_file_tampering(
    tmp_path: Path,
) -> None:
    world, session_input, record, store, paths = _stored_bundle(tmp_path)
    state_path = paths["STATE"][-1]
    state_path.write_bytes(state_path.read_bytes() + b" ")
    with pytest.raises(
        ShadowContractError,
        match="raw-file identity mismatch",
    ):
        store.load_session_bundle(
            world.manifest,
            genesis_id=world.genesis.genesis_id,
            session_input_id=session_input.session_input_id,
            paired_session_id=record.paired_session_id,
        )


def test_policy_portfolio_store_rejects_reference_byte_length_drift(
    tmp_path: Path,
) -> None:
    world, session_input, record, store, _ = _stored_bundle(tmp_path)
    manifest_hash = canonical_sha256(world.manifest)
    assert manifest_hash is not None
    reference_path = (
        store.root
        / "protocols"
        / world.manifest.protocol_id
        / manifest_hash
        / "policy_portfolio"
        / "refs"
        / "paired_session"
        / f"{record.paired_session_id}.json"
    )
    reference = PolicyPortfolioArtifactReference.model_validate_json(
        reference_path.read_bytes()
    )
    drifted = reference.__class__.model_validate(
        {
            **reference.model_dump(mode="python"),
            "artifact_raw_byte_length": (
                reference.artifact_raw_byte_length + 1
            ),
        }
    )
    reference_path.write_bytes(canonical_json_bytes(drifted))
    with pytest.raises(
        ShadowContractError,
        match="raw-file identity mismatch",
    ):
        store.load_session_bundle(
            world.manifest,
            genesis_id=world.genesis.genesis_id,
            session_input_id=session_input.session_input_id,
            paired_session_id=record.paired_session_id,
        )


def test_policy_portfolio_loader_rejects_duplicate_keys_and_v1_contracts(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    raw = canonical_json_bytes(
        world.session_input(session=SIGNAL.date())
    )
    duplicate = raw[:-1] + b',"contract_version":"duplicate"}'
    with pytest.raises(ShadowContractError, match="duplicate JSON key"):
        load_policy_portfolio_session_input_v1(duplicate)
    with pytest.raises(
        ShadowContractError,
        match="contract_version must be shadow-policy-portfolio-state-v1",
    ):
        load_policy_portfolio_state_v1(
            canonical_json_bytes(world.pair.observation)
        )
    with pytest.raises(
        ShadowContractError,
        match=(
            "contract_version must be "
            "shadow-policy-portfolio-session-input-v1; "
            "received 'shadow-evaluation-v1'"
        ),
    ):
        load_policy_portfolio_session_input_v1(
            b'{"contract_version":"shadow-evaluation-v1"}'
        )


def test_policy_portfolio_state_transition_hashes_identical_cross_process(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    record = _signal_record(world)
    state_json = canonical_json_bytes(record.control_state).decode("utf-8")
    transition_json = canonical_json_bytes(
        record.control_transition
    ).decode("utf-8")
    script = (
        "import json\n"
        "from core.shadow_protocol.contracts import canonical_sha256\n"
        "from core.shadow_protocol.policy_portfolio import "
        "PolicyPortfolioSessionState,PolicyPortfolioSessionTransition\n"
        f"s=PolicyPortfolioSessionState.model_validate_json({state_json!r})\n"
        "t=PolicyPortfolioSessionTransition.model_validate_json"
        f"({transition_json!r})\n"
        "print(json.dumps([canonical_sha256(s),canonical_sha256(t)]))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == [
        canonical_sha256(record.control_state),
        canonical_sha256(record.control_transition),
    ]
