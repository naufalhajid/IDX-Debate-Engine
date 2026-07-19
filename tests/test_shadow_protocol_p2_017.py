"""Acceptance tests for the evaluation-only RS-P2-017 daily NAV layer."""

from __future__ import annotations

from datetime import date, timedelta, timezone
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from core.shadow_protocol import (
    ContentHash,
    ShadowContractError,
    ShadowProtocolManifest,
    canonical_json_bytes,
    canonical_sha256,
)
import core.shadow_protocol.daily_nav as daily_nav_module
from core.shadow_protocol.daily_nav import (
    DAILY_NAV_CONFIG_PATH,
    INSOLVENT_TERMINAL,
    NOT_ESTIMABLE_NO_PREDECESSOR,
    NOT_ESTIMABLE_SUSPENDED_NO_OFFICIAL_MARK,
    NavMarkInput,
    NavSeriesSnapshot,
    build_daily_nav_policy,
    build_fixed_notional_sleeve_nav_series,
    build_nav_mark_input,
    build_policy_portfolio_nav_series,
    canonical_fixed_sleeve_series_id,
    canonical_policy_nav_series_id,
    load_daily_nav_point_v1,
    load_daily_nav_policy_v1,
    load_nav_mark_input_v1,
    load_nav_series_event_v1,
    load_nav_series_snapshot_v1,
    replay_nav_series_snapshot,
    verify_daily_nav_policy_binding,
)
from core.shadow_protocol.daily_nav_store import (
    DailyNavArtifactReference,
    DailyNavArtifactStore,
)
from core.shadow_protocol.calendar import IDX_TIMEZONE, session_close_at
from core.shadow_protocol.fixed_notional import (
    PairedFixedNotionalRecord,
    _no_action_lifecycle,
    canonical_fixed_notional_paired_record_id,
)
from core.shadow_protocol.portfolio import quantize_ratio
from tests.test_shadow_protocol_p2_014 import SIGNAL
from tests.test_shadow_protocol_p2_015 import OUTCOME_SOURCE, _split_event
from tests.test_shadow_protocol_p2_016 import (
    _World,
    _next_record,
    _signal_record,
    _world,
)


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _nav_world(
    tmp_path: Path,
    **world_kwargs,
) -> tuple[_World, object]:
    frozen: dict[str, object] = {}

    def enrich(
        manifest,
        portfolio_policy,
        fixed_policy,
        policy_portfolio_policy,
    ):
        policy = build_daily_nav_policy(
            manifest=manifest,
            portfolio_policy=portfolio_policy,
            fixed_notional_policy=fixed_policy,
            policy_portfolio_policy=policy_portfolio_policy,
            mark_source=OUTCOME_SOURCE,
            policy_id="RS-P2-017-DAILY-NAV-TEST",
        )
        policy_sha256 = canonical_sha256(policy)
        assert policy_sha256 is not None
        frozen["policy"] = policy
        return ShadowProtocolManifest.model_validate(
            {
                **manifest.model_dump(mode="python"),
                "control_content_hashes": (
                    *manifest.control_content_hashes,
                    ContentHash(
                        path=DAILY_NAV_CONFIG_PATH,
                        sha256=policy_sha256,
                        role="CONFIG",
                    ),
                ),
                "challenger_content_hashes": (
                    *manifest.challenger_content_hashes,
                    ContentHash(
                        path=DAILY_NAV_CONFIG_PATH,
                        sha256=policy_sha256,
                        role="CONFIG",
                    ),
                ),
            }
        )

    world = _world(
        tmp_path,
        manifest_enricher=enrich,
        **world_kwargs,
    )
    return world, frozen["policy"]


def _mark(
    world: _World,
    daily_policy,
    *,
    series_kind: str,
    series_id: str,
    decision_role: str,
    path_id: str,
    session: date,
    ticker: str,
    close_price_idr: int | None,
    mark_status: str = "OFFICIAL_CURRENT_SESSION",
    reason_codes: tuple[str, ...] = (),
):
    close = session_close_at(session)
    record_seed = (
        f"{series_kind}|{series_id}|{decision_role}|{path_id}|"
        f"{session.isoformat()}|{ticker}|{mark_status}|{close_price_idr}"
    )
    return build_nav_mark_input(
        manifest=world.manifest,
        portfolio_policy=world.portfolio_policy,
        fixed_notional_policy=world.fixed_policy,
        policy_portfolio_policy=world.policy,
        daily_nav_policy=daily_policy,
        series_kind=series_kind,
        series_id=series_id,
        decision_role=decision_role,
        path_id=path_id,
        session=session,
        ticker=ticker,
        mark_status=mark_status,
        close_price_idr=close_price_idr,
        volume_shares=(
            1_000_000
            if mark_status == "OFFICIAL_CURRENT_SESSION"
            else None
        ),
        source_record_canonical_sha256=_sha(f"canonical|{record_seed}"),
        source_record_raw_file_sha256=_sha(f"raw|{record_seed}"),
        source_record_raw_byte_length=len(record_seed.encode("utf-8")),
        source_as_of=close,
        available_at=close + timedelta(minutes=1),
        captured_at=close + timedelta(minutes=2),
        reason_codes=reason_codes,
    )


def _policy_series_id_and_path(
    world: _World,
    decision_role: str,
) -> tuple[str, str]:
    state = (
        world.control_genesis
        if decision_role == "CONTROL"
        else world.challenger_genesis
    )
    return canonical_policy_nav_series_id(world.genesis, state), state.path_id


def _official_policy_marks(
    world: _World,
    daily_policy,
    records,
    decision_role: str,
    *,
    price_delta_idr: int = 0,
) -> tuple[object, ...]:
    series_id, path_id = _policy_series_id_and_path(world, decision_role)
    result = []
    for record in records:
        state = (
            record.control_state
            if decision_role == "CONTROL"
            else record.challenger_state
        )
        for position in state.payload.positions:
            result.append(
                _mark(
                    world,
                    daily_policy,
                    series_kind="POLICY_PORTFOLIO_NAV",
                    series_id=series_id,
                    decision_role=decision_role,
                    path_id=path_id,
                    session=record.session,
                    ticker=position.ticker,
                    close_price_idr=(
                        position.last_mark_price_idr + price_delta_idr
                    ),
                )
            )
    return tuple(result)


def _build_policy_series(
    world: _World,
    daily_policy,
    records=(),
    *,
    control_marks=(),
    challenger_marks=(),
    through_session: date | None = None,
):
    end = through_session or (
        records[-1].session if records else world.genesis.genesis_session
    )
    return build_policy_portfolio_nav_series(
        manifest=world.manifest,
        portfolio_policy=world.portfolio_policy,
        fixed_notional_policy=world.fixed_policy,
        policy_portfolio_policy=world.policy,
        daily_nav_policy=daily_policy,
        trading_calendar=world.calendar,
        genesis=world.genesis,
        control_genesis_state=world.control_genesis,
        challenger_genesis_state=world.challenger_genesis,
        paired_sessions=tuple(records),
        control_marks=tuple(control_marks),
        challenger_marks=tuple(challenger_marks),
        through_session=end,
        snapshot_at=session_close_at(end) + timedelta(hours=1),
    )


def _fixed_sessions(paired_record) -> tuple[date, ...]:
    signal_session = paired_record.control.signal_at.astimezone(
        IDX_TIMEZONE
    ).date()
    end_dates = [signal_session]
    for lifecycle in (paired_record.control, paired_record.challenger):
        end_dates.extend(
            item.settlement_session for item in lifecycle.cash_flow_records
        )
        for value in (
            lifecycle.maturity_at,
            lifecycle.closed_at,
            (
                lifecycle.evaluated_at
                if lifecycle.status in ("NOT_ESTIMABLE", "PENDING")
                else None
            ),
        ):
            if value is not None:
                end_dates.append(value.astimezone(IDX_TIMEZONE).date())
    end = max(end_dates)
    return tuple(
        item
        for item in paired_record.trading_calendar.sessions
        if signal_session <= item <= end
    )


def _fixed_marks(
    world: _World,
    daily_policy,
    decision_role: str,
    *,
    price_idr: int = 10_000,
    paired_record=None,
) -> tuple[object, ...]:
    paired_record = (
        world.candidate_input.paired_fixed_notional_record
        if paired_record is None
        else paired_record
    )
    lifecycle = (
        paired_record.control
        if decision_role == "CONTROL"
        else paired_record.challenger
    )
    series_id = canonical_fixed_sleeve_series_id(
        paired_record,
        decision_role,
    )
    holdings_by_session: dict[date, list[object]] = {}
    for holding in lifecycle.holding_records:
        holdings_by_session.setdefault(holding.event_session, []).append(holding)
    quantity = 0
    marks = []
    for session in _fixed_sessions(paired_record):
        for holding in sorted(
            holdings_by_session.get(session, ()),
            key=lambda item: (item.occurred_at, item.holding_event_id),
        ):
            assert holding.quantity_before_shares == quantity
            quantity = holding.quantity_after_shares
        if quantity:
            marks.append(
                _mark(
                    world,
                    daily_policy,
                    series_kind="FIXED_NOTIONAL_SLEEVE_EQUITY",
                    series_id=series_id,
                    decision_role=decision_role,
                    path_id=lifecycle.lifecycle_id,
                    session=session,
                    ticker=lifecycle.ticker,
                    close_price_idr=price_idr,
                )
            )
    return tuple(marks)


def _build_fixed_series(
    world: _World,
    daily_policy,
    *,
    control_marks=None,
    challenger_marks=None,
    paired_record=None,
):
    paired_record = (
        world.candidate_input.paired_fixed_notional_record
        if paired_record is None
        else paired_record
    )
    sessions = _fixed_sessions(paired_record)
    return build_fixed_notional_sleeve_nav_series(
        manifest=world.manifest,
        portfolio_policy=world.portfolio_policy,
        fixed_notional_policy=world.fixed_policy,
        policy_portfolio_policy=world.policy,
        daily_nav_policy=daily_policy,
        paired_record=paired_record,
        control_marks=(
            _fixed_marks(
                world,
                daily_policy,
                "CONTROL",
                paired_record=paired_record,
            )
            if control_marks is None
            else tuple(control_marks)
        ),
        challenger_marks=(
            _fixed_marks(
                world,
                daily_policy,
                "CHALLENGER",
                paired_record=paired_record,
            )
            if challenger_marks is None
            else tuple(challenger_marks)
        ),
        snapshot_at=session_close_at(sessions[-1]) + timedelta(hours=1),
    )


def _with_challenger_no_action(world: _World) -> PairedFixedNotionalRecord:
    """Build a self-validating no-action side for the daily-NAV contract."""

    original = world.candidate_input.paired_fixed_notional_record
    decision = world.pair.observation.challenger_decision
    primary = _no_action_lifecycle(
        world.pair,
        decision,
        original.challenger_sizing_plan,
        horizon=15,
        pair_input_sha256=original.pair_input_sha256,
    )
    secondary = tuple(
        _no_action_lifecycle(
            world.pair,
            decision,
            original.challenger_sizing_plan,
            horizon=horizon,
            pair_input_sha256=original.pair_input_sha256,
        )
        for horizon in (3, 5, 10)
    )
    control_hashes = (
        *original.control_secondary_sha256s,
        original.control_lifecycle_sha256,
    )
    challenger_hashes = (
        *(str(canonical_sha256(item)) for item in secondary),
        str(canonical_sha256(primary)),
    )
    return PairedFixedNotionalRecord.model_validate(
        {
            **original.model_dump(mode="python"),
            "challenger": primary,
            "challenger_secondary": secondary,
            "challenger_lifecycle_sha256": canonical_sha256(primary),
            "challenger_secondary_sha256s": tuple(
                canonical_sha256(item) for item in secondary
            ),
            "paired_record_id": canonical_fixed_notional_paired_record_id(
                pair_input_sha256=original.pair_input_sha256,
                control_sha256s=control_hashes,
                challenger_sha256s=challenger_hashes,
            ),
        }
    )


def test_manifest_binds_one_identical_daily_nav_policy_per_side(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path)

    verify_daily_nav_policy_binding(
        world.manifest,
        world.portfolio_policy,
        world.fixed_policy,
        world.policy,
        daily_policy,
    )
    policy_hash = canonical_sha256(daily_policy)
    assert policy_hash is not None
    for content_hashes in (
        world.manifest.control_content_hashes,
        world.manifest.challenger_content_hashes,
    ):
        matches = tuple(
            item
            for item in content_hashes
            if item.path == DAILY_NAV_CONFIG_PATH
        )
        assert len(matches) == 1
        assert (matches[0].role, matches[0].sha256) == (
            "CONFIG",
            policy_hash,
        )


def test_manifest_asymmetric_daily_nav_binding_fails_closed(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path)
    forged = ShadowProtocolManifest.model_validate(
        {
            **world.manifest.model_dump(mode="python"),
            "challenger_content_hashes": tuple(
                item
                for item in world.manifest.challenger_content_hashes
                if item.path != DAILY_NAV_CONFIG_PATH
            ),
        }
    )

    with pytest.raises(
        ShadowContractError,
        match="challenger manifest does not bind exact daily-NAV CONFIG",
    ):
        verify_daily_nav_policy_binding(
            forged,
            world.portfolio_policy,
            world.fixed_policy,
            world.policy,
            daily_policy,
        )


def test_policy_nav_genesis_is_previous_session_close_with_no_return(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path)
    paired = _build_policy_series(world, daily_policy)

    assert paired.shared_session_union == (world.genesis.genesis_session,)
    for snapshot in (paired.control, paired.challenger):
        point = snapshot.points[0]
        assert point.point_status == "GENESIS_ANCHOR"
        assert point.session == world.genesis.genesis_session
        assert point.as_of == session_close_at(world.genesis.genesis_session)
        assert point.nav.status == "ESTIMABLE"
        assert point.nav.value_idr == 100_000_000
        assert point.daily_return.status == "NOT_ESTIMABLE"
        assert point.daily_return.reason_codes == (
            NOT_ESTIMABLE_NO_PREDECESSOR,
        )


def test_policy_nav_exact_formula_return_and_one_idr_mark_mismatch(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path)
    signal = _signal_record(world)
    fill = _next_record(world, signal)
    records = (signal, fill)
    control_marks = _official_policy_marks(
        world,
        daily_policy,
        records,
        "CONTROL",
    )
    challenger_marks = _official_policy_marks(
        world,
        daily_policy,
        records,
        "CHALLENGER",
    )

    paired = _build_policy_series(
        world,
        daily_policy,
        records,
        control_marks=control_marks,
        challenger_marks=challenger_marks,
    )
    point = paired.control.points[-1]
    payload = fill.control_state.payload
    expected = (
        payload.settled_cash_idr
        + payload.sale_receivable_idr
        - payload.purchase_payable_idr
        + payload.marked_holdings_value_idr
    )
    assert point.nav.value_idr == expected == payload.accounting_equity_idr
    assert point.marked_holdings_value_idr == (
        payload.marked_holdings_value_idr
    )
    previous = paired.control.points[-2]
    assert point.daily_return.value == quantize_ratio(
        expected - previous.nav.value_idr,
        previous.nav.value_idr,
    )

    bad_marks = _official_policy_marks(
        world,
        daily_policy,
        records,
        "CONTROL",
        price_delta_idr=1,
    )
    with pytest.raises(
        ShadowContractError,
        match="official mark differs from EOD state",
    ):
        _build_policy_series(
            world,
            daily_policy,
            records,
            control_marks=bad_marks,
            challenger_marks=challenger_marks,
        )


def test_suspension_creates_permanent_null_without_return_bridge(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path)
    signal = _signal_record(world)
    fill = _next_record(world, signal)
    next_record = _next_record(world, fill)
    records = (signal, fill, next_record)
    control_series_id, control_path_id = _policy_series_id_and_path(
        world,
        "CONTROL",
    )
    challenger_series_id, challenger_path_id = _policy_series_id_and_path(
        world,
        "CHALLENGER",
    )
    ticker = fill.control_state.payload.positions[0].ticker

    def suspended(role: str, series_id: str, path_id: str):
        return _mark(
            world,
            daily_policy,
            series_kind="POLICY_PORTFOLIO_NAV",
            series_id=series_id,
            decision_role=role,
            path_id=path_id,
            session=fill.session,
            ticker=ticker,
            close_price_idr=None,
            mark_status="SUSPENDED_NO_OFFICIAL_RECORD",
            reason_codes=(NOT_ESTIMABLE_SUSPENDED_NO_OFFICIAL_MARK,),
        )

    control_suspended = suspended(
        "CONTROL",
        control_series_id,
        control_path_id,
    )
    challenger_suspended = suspended(
        "CHALLENGER",
        challenger_series_id,
        challenger_path_id,
    )
    control_future = _official_policy_marks(
        world,
        daily_policy,
        (next_record,),
        "CONTROL",
    )
    challenger_future = _official_policy_marks(
        world,
        daily_policy,
        (next_record,),
        "CHALLENGER",
    )
    with pytest.raises(
        ShadowContractError,
        match="extraneous mark evidence",
    ):
        _build_policy_series(
            world,
            daily_policy,
            records,
            control_marks=(control_suspended, *control_future),
            challenger_marks=(challenger_suspended, *challenger_future),
        )

    paired = _build_policy_series(
        world,
        daily_policy,
        records,
        control_marks=(control_suspended,),
        challenger_marks=(challenger_suspended,),
    )

    for snapshot in (paired.control, paired.challenger):
        first_null = snapshot.points[-2]
        later = snapshot.points[-1]
        assert first_null.nav.status == "NOT_ESTIMABLE"
        assert first_null.censored_tickers == (ticker,)
        assert first_null.censor_duration_sessions == 1
        assert first_null.reason_codes == (
            NOT_ESTIMABLE_SUSPENDED_NO_OFFICIAL_MARK,
        )
        assert later.nav.status == "NOT_ESTIMABLE"
        assert later.daily_return.status == "NOT_ESTIMABLE"
        assert later.poisoned_from_session == fill.session
        assert later.censored_tickers == (ticker,)
        assert later.censor_duration_sessions == 2
        assert later.mark_input_ids == ()
        assert snapshot.poisoned_from_session == fill.session
        assert snapshot.poison_reason_codes == first_null.reason_codes


def test_fixed_primary_sleeve_tracks_t_plus_two_liability_and_settlement_only(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path, bar_mode="target")
    record = world.candidate_input.paired_fixed_notional_record
    assert record.control.primary_horizon is True
    assert record.control.horizon_trading_days == 15
    assert all(
        not item.holding_records and not item.cash_flow_records
        for item in record.control_secondary
    )

    paired = _build_fixed_series(world, daily_policy)
    control = paired.control
    entry = next(
        item
        for item in record.control.cash_flow_records
        if item.event_type == "ENTRY_DEBIT"
    )
    exit_credit = next(
        item
        for item in record.control.cash_flow_records
        if item.event_type == "EXIT_CREDIT"
    )
    entry_point = next(
        item for item in control.points if item.session == entry.trade_session
    )
    assert entry_point.purchase_payable_idr == (
        entry.gross_amount_idr + entry.cost_idr
    )
    liability_point = next(
        item
        for item in control.points
        if item.unfunded_cost_liability_idr > 0
    )
    assert liability_point.unfunded_cost_origin_sha256 == canonical_sha256(
        entry
    )
    assert any(
        item.name == "entry_cost"
        and item.sha256 == canonical_sha256(entry)
        for item in liability_point.predecessors
    )
    assert liability_point.unfunded_cost_liability_idr <= entry.cost_idr
    with pytest.raises(
        ValidationError,
        match="not explained by exact entry cost",
    ):
        liability_point.__class__.model_validate(
            {
                **liability_point.model_dump(mode="python"),
                "predecessors": tuple(
                    item
                    for item in liability_point.predecessors
                    if item.name != "entry_cost"
                ),
            }
        )
    settlement_points = tuple(
        item
        for item in control.points
        if item.point_status == "SETTLEMENT_ONLY"
    )
    assert settlement_points
    assert any(
        item.sale_receivable_idr == exit_credit.net_cash_change_idr
        for item in settlement_points
    )
    terminal = control.points[-1]
    assert terminal.session == exit_credit.settlement_session
    assert terminal.purchase_payable_idr == 0
    assert terminal.sale_receivable_idr == 0
    assert terminal.unfunded_cost_liability_idr == 0
    assert terminal.nav.value_idr == (
        13_000_000 + record.control.net_pnl_idr.value_idr
    )


def test_no_action_side_is_flat_only_over_real_paired_union(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(
        tmp_path,
        bar_mode="target",
    )
    record = _with_challenger_no_action(world)
    assert record.challenger.terminal_event == "NO_ACTION"

    paired = _build_fixed_series(
        world,
        daily_policy,
        paired_record=record,
    )
    challenger = paired.challenger
    assert len(paired.shared_session_union) == len(paired.control.points)
    assert len(challenger.points) == len(paired.shared_session_union)
    assert len(challenger.points) < 15
    assert challenger.points[0].point_status == "GENESIS_ANCHOR"
    assert all(
        item.point_status == "NO_ACTION_FLAT"
        and item.nav.value_idr == 13_000_000
        and item.daily_return.value == 0.0
        for item in challenger.points[1:]
    )


def test_fixed_sleeve_return_is_quantized_and_insolvency_is_terminal(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path, bar_mode="target")
    control_marks = _fixed_marks(
        world,
        daily_policy,
        "CONTROL",
        price_idr=1,
    )
    challenger_marks = _fixed_marks(
        world,
        daily_policy,
        "CHALLENGER",
        price_idr=1,
    )
    paired = _build_fixed_series(
        world,
        daily_policy,
        control_marks=control_marks,
        challenger_marks=challenger_marks,
    )

    for snapshot in (paired.control, paired.challenger):
        insolvent = next(
            item for item in snapshot.points if item.point_status == "INSOLVENT"
        )
        assert insolvent.nav.value_idr <= 0
        assert insolvent.daily_return.status == "ESTIMABLE"
        assert insolvent.daily_return.value <= -1.0
        previous = snapshot.points[insolvent.point_sequence - 1]
        assert insolvent.daily_return.value == quantize_ratio(
            insolvent.nav.value_idr - previous.nav.value_idr,
            previous.nav.value_idr,
        )
        for later in snapshot.points[insolvent.point_sequence + 1 :]:
            assert later.nav.status == "NOT_ESTIMABLE"
            assert later.reason_codes == (INSOLVENT_TERMINAL,)


def test_daily_nav_artifacts_are_evaluation_only_and_replay_deterministically(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path)
    signal = _signal_record(world)
    fill = _next_record(world, signal)
    records = (signal, fill)
    control_marks = _official_policy_marks(
        world,
        daily_policy,
        records,
        "CONTROL",
    )
    challenger_marks = _official_policy_marks(
        world,
        daily_policy,
        records,
        "CHALLENGER",
    )
    paired = _build_policy_series(
        world,
        daily_policy,
        records,
        control_marks=control_marks,
        challenger_marks=challenger_marks,
    )

    artifacts = (
        daily_policy,
        *paired.control.mark_inputs,
        *paired.control.points,
        *paired.control.events,
        paired.control,
        *paired.challenger.mark_inputs,
        *paired.challenger.points,
        *paired.challenger.events,
        paired.challenger,
    )
    for artifact in artifacts:
        assert artifact.evaluation_only is True
        assert artifact.live_authority is False
        assert artifact.affects_execution is False
        assert artifact.affects_ranking is False
        assert artifact.affects_sizing is False

    replayed = replay_nav_series_snapshot(paired.control)
    rebuilt = _build_policy_series(
        world,
        daily_policy,
        records,
        control_marks=control_marks,
        challenger_marks=challenger_marks,
    )
    assert canonical_sha256(replayed) == canonical_sha256(paired.control)
    assert canonical_sha256(rebuilt.control) == canonical_sha256(
        paired.control
    )


def test_no_public_fixed_sleeve_aggregation_surface_or_family_relabel(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path, bar_mode="target")
    paired = _build_fixed_series(world, daily_policy)
    public_functions = {
        name
        for name, value in inspect.getmembers(
            daily_nav_module,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }
    assert not {
        name
        for name in public_functions
        if "aggregate" in name.lower() or "portfolio_from_fixed" in name.lower()
    }
    assert (
        daily_policy.cross_opportunity_aggregation_rule
        == "FORBIDDEN_NO_IMPLICIT_HELPER"
    )

    with pytest.raises(
        ValidationError,
        match="cross-series artifact",
    ):
        NavSeriesSnapshot.model_validate(
            {
                **paired.control.model_dump(mode="python"),
                "series_kind": "POLICY_PORTFOLIO_NAV",
            }
        )


@pytest.mark.parametrize(
    "series_kind",
    ("POLICY_PORTFOLIO_NAV", "FIXED_NOTIONAL_SLEEVE_EQUITY"),
)
def test_daily_nav_snapshot_hash_is_identical_cross_process(
    tmp_path: Path,
    series_kind: str,
) -> None:
    world, daily_policy = _nav_world(
        tmp_path,
        bar_mode="target" if series_kind == "FIXED_NOTIONAL_SLEEVE_EQUITY" else "timeout",
    )
    paired = (
        _build_policy_series(world, daily_policy)
        if series_kind == "POLICY_PORTFOLIO_NAV"
        else _build_fixed_series(world, daily_policy)
    )
    raw = canonical_json_bytes(paired.control).decode("utf-8")
    script = (
        "import json\n"
        "from core.shadow_protocol.contracts import canonical_sha256\n"
        "from core.shadow_protocol.daily_nav import NavSeriesSnapshot\n"
        f"s=NavSeriesSnapshot.model_validate_json({raw!r})\n"
        "print(json.dumps(canonical_sha256(s)))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == canonical_sha256(paired.control)


def test_exact_v1_loaders_round_trip_and_reject_duplicate_json_keys(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path)
    paired = _build_policy_series(world, daily_policy)
    series_id, path_id = _policy_series_id_and_path(world, "CONTROL")
    mark = _mark(
        world,
        daily_policy,
        series_kind="POLICY_PORTFOLIO_NAV",
        series_id=series_id,
        decision_role="CONTROL",
        path_id=path_id,
        session=SIGNAL.date(),
        ticker=world.pair.observation.ticker,
        close_price_idr=10_000,
    )
    point = paired.control.points[0]
    event = paired.control.events[0]
    cases = (
        (daily_policy, load_daily_nav_policy_v1),
        (mark, load_nav_mark_input_v1),
        (point, load_daily_nav_point_v1),
        (event, load_nav_series_event_v1),
        (paired.control, load_nav_series_snapshot_v1),
    )
    for artifact, loader in cases:
        raw = canonical_json_bytes(artifact)
        assert canonical_sha256(loader(raw)) == canonical_sha256(artifact)

    raw = canonical_json_bytes(paired.control)
    duplicate = raw[:-1] + b',"contract_version":"duplicate"}'
    with pytest.raises(ShadowContractError, match="duplicate JSON key"):
        load_nav_series_snapshot_v1(duplicate)


@pytest.mark.parametrize(
    "loader",
    (
        load_daily_nav_policy_v1,
        load_nav_mark_input_v1,
        load_daily_nav_point_v1,
        load_nav_series_event_v1,
        load_nav_series_snapshot_v1,
    ),
)
def test_exact_v1_loaders_never_reinterpret_shadow_evaluation_v1(
    loader,
) -> None:
    with pytest.raises(
        ShadowContractError,
        match="never reinterpreted as daily NAV",
    ):
        loader(b'{"contract_version":"shadow-evaluation-v1"}')


@pytest.mark.parametrize(
    "series_kind",
    ("POLICY_PORTFOLIO_NAV", "FIXED_NOTIONAL_SLEEVE_EQUITY"),
)
def test_daily_nav_store_is_content_addressed_idempotent_and_replayable(
    tmp_path: Path,
    series_kind: str,
) -> None:
    world, daily_policy = _nav_world(
        tmp_path / "world",
        bar_mode="target" if series_kind == "FIXED_NOTIONAL_SLEEVE_EQUITY" else "timeout",
    )
    paired = (
        _build_policy_series(world, daily_policy)
        if series_kind == "POLICY_PORTFOLIO_NAV"
        else _build_fixed_series(world, daily_policy)
    )
    snapshot = paired.control
    store = DailyNavArtifactStore(tmp_path / "nav-store")
    pretty_policy = json.dumps(
        daily_policy.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")

    first_paths = store.persist_snapshot_bundle(
        world.manifest,
        policy_raw_file_bytes=pretty_policy,
        snapshot_raw_file_bytes=canonical_json_bytes(snapshot),
    )
    first_files = tuple(
        sorted(
            item.relative_to(store.root).as_posix()
            for item in store.root.rglob("*")
            if item.is_file()
        )
    )
    second_paths = store.persist_snapshot_bundle(
        world.manifest,
        policy_raw_file_bytes=pretty_policy,
        snapshot_raw_file_bytes=canonical_json_bytes(snapshot),
    )
    second_files = tuple(
        sorted(
            item.relative_to(store.root).as_posix()
            for item in store.root.rglob("*")
            if item.is_file()
        )
    )
    assert first_paths == second_paths
    assert first_files == second_files
    assert not any("latest" in item.lower() for item in second_files)

    loaded_policy, loaded_snapshot = store.load_verified_snapshot_bundle(
        world.manifest,
        policy_id=daily_policy.policy_id,
        snapshot_id=snapshot.snapshot_id,
    )
    assert canonical_sha256(loaded_policy) == canonical_sha256(daily_policy)
    assert canonical_sha256(loaded_snapshot) == canonical_sha256(snapshot)
    assert (
        loaded_snapshot.chain_completeness_status
        == "UNANCHORED_NOT_CERTIFIED_COMPLETE"
    )
    _, policy_reference = store.load_by_reference(
        world.manifest,
        kind="POLICY",
        artifact_id=daily_policy.policy_id,
    )
    assert (
        policy_reference.artifact_raw_file_sha256
        != policy_reference.artifact_canonical_sha256
    )
    assert policy_reference.artifact_raw_byte_length == len(pretty_policy)
    assert policy_reference.evaluation_only is True
    assert policy_reference.live_authority is False


@pytest.mark.parametrize(
    "tamper_kind",
    (
        "artifact_raw_byte",
        "embedded_mark_raw_byte",
        "embedded_point_raw_byte",
        "reference_byte_length",
        "duplicate_reference_key",
    ),
)
def test_daily_nav_store_tamper_matrix_fails_closed(
    tmp_path: Path,
    tamper_kind: str,
) -> None:
    world, daily_policy = _nav_world(tmp_path / "world")
    signal = _signal_record(world)
    fill = _next_record(world, signal)
    records = (signal, fill)
    snapshot = _build_policy_series(
        world,
        daily_policy,
        records,
        control_marks=_official_policy_marks(
            world,
            daily_policy,
            records,
            "CONTROL",
        ),
        challenger_marks=_official_policy_marks(
            world,
            daily_policy,
            records,
            "CHALLENGER",
        ),
    ).control
    store = DailyNavArtifactStore(tmp_path / f"store-{tamper_kind}")
    store.persist_snapshot_bundle(
        world.manifest,
        policy_raw_file_bytes=canonical_json_bytes(daily_policy),
        snapshot_raw_file_bytes=canonical_json_bytes(snapshot),
    )
    _, reference = store.load_by_reference(
        world.manifest,
        kind="NAV_SERIES_SNAPSHOT",
        artifact_id=snapshot.snapshot_id,
    )
    artifact_path = store.root / reference.artifact_relative_path
    reference_path = store._reference_path(  # noqa: SLF001
        world.manifest.protocol_id,
        str(canonical_sha256(world.manifest)),
        "NAV_SERIES_SNAPSHOT",
        snapshot.snapshot_id,
    )

    if tamper_kind == "artifact_raw_byte":
        artifact_path.write_bytes(artifact_path.read_bytes() + b"\n")
        match = "raw-file identity mismatch"
    elif tamper_kind in ("embedded_mark_raw_byte", "embedded_point_raw_byte"):
        embedded_kind = (
            "NAV_MARK_INPUT"
            if tamper_kind == "embedded_mark_raw_byte"
            else "NAV_POINT"
        )
        embedded_id = (
            snapshot.mark_inputs[0].mark_input_id
            if tamper_kind == "embedded_mark_raw_byte"
            else snapshot.points[0].point_id
        )
        _, embedded_reference = store.load_by_reference(
            world.manifest,
            kind=embedded_kind,
            artifact_id=embedded_id,
        )
        embedded_path = store.root / embedded_reference.artifact_relative_path
        embedded_path.write_bytes(embedded_path.read_bytes() + b"\n")
        match = "raw-file identity mismatch"
    elif tamper_kind == "reference_byte_length":
        drifted = DailyNavArtifactReference.model_validate(
            {
                **reference.model_dump(mode="python"),
                "artifact_raw_byte_length": (
                    reference.artifact_raw_byte_length + 1
                ),
            }
        )
        reference_path.write_bytes(canonical_json_bytes(drifted))
        match = "raw-file identity mismatch"
    else:
        raw_reference = reference_path.read_bytes()
        reference_path.write_bytes(
            raw_reference[:-1] + b',"artifact_kind":"NAV_POINT"}'
        )
        match = "duplicate JSON key"

    with pytest.raises(ShadowContractError, match=match):
        store.load_verified_snapshot_bundle(
            world.manifest,
            policy_id=daily_policy.policy_id,
            snapshot_id=snapshot.snapshot_id,
        )


def test_nav_mark_timezone_identity_and_chronology_are_deterministic(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(tmp_path)
    series_id, path_id = _policy_series_id_and_path(world, "CONTROL")
    mark = _mark(
        world,
        daily_policy,
        series_kind="POLICY_PORTFOLIO_NAV",
        series_id=series_id,
        decision_role="CONTROL",
        path_id=path_id,
        session=SIGNAL.date(),
        ticker=world.pair.observation.ticker,
        close_price_idr=10_000,
    )
    shifted = NavMarkInput.model_validate(
        {
            **mark.model_dump(mode="python"),
            "source_as_of": mark.source_as_of.astimezone(timezone.utc),
            "available_at": mark.available_at.astimezone(timezone.utc),
            "captured_at": mark.captured_at.astimezone(timezone.utc),
        }
    )
    assert shifted.mark_input_id == mark.mark_input_id
    assert canonical_sha256(shifted) == canonical_sha256(mark)

    with pytest.raises(ValidationError, match="available before close"):
        NavMarkInput.model_validate(
            {
                **mark.model_dump(mode="python"),
                "available_at": session_close_at(mark.session)
                - timedelta(seconds=1),
            }
        )

    with pytest.raises(ValidationError, match="not the frozen current session"):
        NavMarkInput.model_validate(
            {
                **mark.model_dump(mode="python"),
                "source_as_of": mark.source_as_of - timedelta(days=1),
            }
        )


def test_unresolved_fixed_terminal_keeps_canonical_null_and_marked_diagnostic(
    tmp_path: Path,
) -> None:
    world, daily_policy = _nav_world(
        tmp_path,
        bar_mode="target",
        exit_adtv_idr=9_000_000_000,
    )
    record = world.candidate_input.paired_fixed_notional_record
    assert record.control.status == "NOT_ESTIMABLE"
    assert record.control.reason_codes == ("NOT_ESTIMABLE_EXIT_CAPACITY",)

    paired = _build_fixed_series(world, daily_policy)
    for snapshot in (paired.control, paired.challenger):
        terminal = snapshot.points[-1]
        assert terminal.point_status == "NOT_ESTIMABLE"
        assert terminal.nav.status == "NOT_ESTIMABLE"
        assert terminal.diagnostic_equity_idr == 12_967_500
        assert terminal.marked_holdings_value_idr == 13_000_000
        assert terminal.unfunded_cost_liability_idr == 32_500
        assert terminal.mark_input_ids
        assert terminal.censored_tickers == ()
        assert terminal.reason_codes == (
            "NOT_ESTIMABLE_EXIT_CAPACITY",
            "NOT_ESTIMABLE_TERMINAL_UNRESOLVED",
        )


def test_policy_nav_consumes_split_adjusted_state_without_double_adjustment(
    tmp_path: Path,
) -> None:
    split_session = date(2026, 7, 21)
    split = _split_event(
        event_id="P2-017-SPLIT-2-FOR-1",
        effective_date=split_session,
        published_at=SIGNAL + timedelta(hours=1),
    )
    world, daily_policy = _nav_world(
        tmp_path,
        action_events=(split,),
    )
    signal = _signal_record(world)
    fill = _next_record(world, signal)
    split_record = _next_record(world, fill)
    records = (signal, fill, split_record)
    control_marks = _official_policy_marks(
        world,
        daily_policy,
        records,
        "CONTROL",
    )
    challenger_marks = _official_policy_marks(
        world,
        daily_policy,
        records,
        "CHALLENGER",
    )
    paired = _build_policy_series(
        world,
        daily_policy,
        records,
        control_marks=control_marks,
        challenger_marks=challenger_marks,
    )
    state = split_record.control_state
    point = next(
        item for item in paired.control.points if item.session == split_session
    )
    split_event = next(
        item
        for item in split_record.control_transition.events
        if item.event_type == "CORPORATE_ACTION_SPLIT"
    )
    assert split_event.quantity_delta_shares == 100
    assert (
        point.marked_holdings_value_idr
        == state.payload.marked_holdings_value_idr
    )
    assert point.nav.value_idr == state.payload.accounting_equity_idr
