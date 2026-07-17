"""Contract tests for the isolated, evaluation-only shadow protocol plane."""

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.shadow_protocol import (
    ClusterRuleDefinition,
    ContentHash,
    CostAssumptions,
    EffectiveSampleMetadata,
    FeatureDefinition,
    FrozenParameter,
    GateMeasurement,
    GoNoGoRules,
    IndependentClusterMetadata,
    LabelDefinition,
    RecordedTradeGeometry,
    ShadowContractError,
    ShadowDecision,
    ShadowObservation,
    ShadowOutcome,
    ShadowProtocolManifest,
    SourceDefinition,
    TrialRegistry,
    TrialRegistryEvent,
    UniverseDefinition,
    canonical_decision_payload_sha256,
    canonical_outcome_id,
    canonical_rules_sha256,
    canonical_sha256,
    classify_divergence,
)


UTC = timezone.utc
CREATED = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
SIGNAL = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _rules() -> GoNoGoRules:
    go = ("predeclared GO rule",)
    cont = ("predeclared CONTINUE rule",)
    no_go = ("predeclared NO-GO rule",)
    return GoNoGoRules(
        go=go,
        continue_rules=cont,
        no_go=no_go,
        rules_sha256=canonical_rules_sha256(go, cont, no_go),
    )


def _cluster_rules() -> ClusterRuleDefinition:
    return ClusterRuleDefinition(
        rule_version="cluster-v1",
        issuer_group_rule="same issuer",
        economic_group_rule="same economic group",
        correlation_cluster_rule="rolling correlation",
        systemic_date_block_rule="same signal date block",
        duplicate_setup_rule="same ticker and geometry",
        representative_rule="first event by signal time",
        effective_n_rule="one independent representative per cluster",
    )


def _manifest(**overrides: object) -> ShadowProtocolManifest:
    cluster_rules = _cluster_rules()
    values: dict[str, object] = {
        "protocol_id": "RS-C1-CONTRACT-TEST",
        "component_id": "C1",
        "manifest_revision": 1,
        "lifecycle_status": "DRAFT",
        "created_at": CREATED,
        "draft_frozen_at": CREATED + timedelta(hours=1),
        "collection_start_not_before": CREATED + timedelta(days=4),
        "fixed_terminal_date": date(2026, 12, 31),
        "owner": "owner@example",
        "governance_mode": "SOLO_SELF_REVIEW",
        "independent_reviewer": None,
        "rollback_owner": "owner@example",
        "baseline_manifest_id": "RS-CONTROL-20260717-01",
        "baseline_manifest_sha256": HASH_A,
        "methodology_document_path": (
            "docs/research/SHADOW_MODE_PROTOCOL.md"
        ),
        "methodology_document_sha256": HASH_C,
        "control_content_hashes": (
            ContentHash(path="core/control.py", sha256=HASH_A, role="CONTROL"),
        ),
        "challenger_content_hashes": (
            ContentHash(path="core/challenger.py", sha256=HASH_B, role="CHALLENGER"),
        ),
        "universe": UniverseDefinition(
            universe_id="IDX-SWING-20260718",
            quant_mode="MOMENTUM",
            selection_rule="frozen candidate snapshot",
            candidate_source_sha256=HASH_C,
            explicit_tickers=("BBCA", "BBRI"),
        ),
        "trading_calendar_id": "IDX-REGULAR-V1",
        "trading_calendar_sha256": HASH_A,
        "corporate_action_policy_sha256": HASH_B,
        "thresholds": (
            FrozenParameter(
                name="rr_floor",
                value=2.0,
                unit="ratio",
                source="control-config",
                description="record-only threshold",
            ),
        ),
        "features": (
            FeatureDefinition(
                name="close_price",
                dtype="FLOAT",
                source_id="market",
                source_field="close",
                as_of_field="as_of",
                expiry_rule="expires next trading day",
                missing_policy="ABSTAIN",
                transformation="identity",
            ),
        ),
        "sources": (
            SourceDefinition(
                source_id="market",
                source_type="MARKET_SNAPSHOT",
                locator="snapshot://market/20260718",
                as_of_field="as_of",
                expiry_rule="one trading day",
                missing_policy="ABSTAIN",
                contract_version="market-v1",
                source_sha256=HASH_C,
            ),
        ),
        "labels": LabelDefinition(
            entry_validity_trading_days=1,
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
            dividend_return_convention="PRICE_RETURN",
            dividend_entitlement_rule="POSITION_OPEN_BEFORE_EX_DATE",
            unfilled_rule="EXPIRE_AFTER_ENTRY_VALIDITY_TRADING_DAYS",
        ),
        "costs": CostAssumptions(
            buy_commission_bps=15.0,
            sell_commission_bps=25.0,
            sell_tax_bps=10.0,
            slippage_bps=5.0,
            bid_ask_bps=5.0,
            lot_size=100,
            liquidity_execution_rule="minimum daily value and lot-aware fill",
            price_rounding_rule="exchange tick-size table",
            cost_model_version="cost-v1",
        ),
        "cluster_rules": cluster_rules,
        "cluster_rules_sha256": canonical_sha256(cluster_rules),
        "go_no_go": _rules(),
        "trial_registry_id": "TRIAL-C1-20260717",
        "production_feature_flag": "SHADOW_C1_ENABLED",
        "rollback_plan": "disable the flag and retain the control path",
    }
    values.update(overrides)
    return ShadowProtocolManifest(**values)


def _gate(passed: bool = True) -> GateMeasurement:
    return GateMeasurement(
        gate_id="rr_floor",
        observed=2.4,
        threshold=2.0,
        comparator=">=",
        unit="ratio",
        passed=passed,
        reason_code="RR_PASS" if passed else "RR_FAIL",
        source_id="market",
        source_definition_sha256=canonical_sha256(
            _manifest().sources[0]
        ),
        source_as_of=SIGNAL,
        expires_at=SIGNAL + timedelta(days=1),
    )


def _geometry() -> RecordedTradeGeometry:
    return RecordedTradeGeometry(
        entry_low=100.0,
        entry_high=101.0,
        target_price=121.0,
        stop_loss=90.0,
        risk_reward_ratio=2.0,
        required_risk_reward=2.0,
    )


def _decision(
    role: str,
    *,
    state: str = "NO_TRADE",
    actionable: bool = False,
    allocate: bool = False,
    size_basis: str = "NONE",
    rank: int | None = None,
    fraction: float | None = None,
    reasons: tuple[str, ...] = ("RR_FAIL",),
) -> ShadowDecision:
    values = {
        "decision_role": role,
        "decision_state": state,
        "rating": "WATCH",
        "would_be_actionable": actionable,
        "would_allocate": allocate,
        "recorded_rank": rank,
        "recorded_position_fraction": fraction,
        "position_size_basis": size_basis,
        "reason_codes": reasons,
        "gate_measurements": (_gate(actionable),),
        "geometry": _geometry() if actionable else None,
    }
    draft = ShadowDecision.model_construct(
        **values,
        decision_payload_sha256=HASH_A,
    )
    return ShadowDecision(
        **values,
        decision_payload_sha256=canonical_decision_payload_sha256(draft),
    )


def _cluster(assigned: bool = True) -> IndependentClusterMetadata:
    if assigned:
        return IndependentClusterMetadata(
            assignment_status="ASSIGNED",
            cluster_id="CL-1",
            cluster_rule_sha256=HASH_C,
            member_event_ids=("EVENT-1",),
            membership_reasons=("same issuer",),
            issuer_group_id="ISSUER-1",
            raw_event_count=1,
            effective_n_contribution=1.0,
            assigned_at=SIGNAL,
            clustering_inputs_sha256=HASH_B,
        )
    return IndependentClusterMetadata(
        assignment_status="NOT_EVALUATED_FOR_INDEPENDENCE",
        cluster_rule_sha256=HASH_C,
        raw_event_count=0,
    )


def _observation(
    *,
    challenger: ShadowDecision | None = None,
    cluster: IndependentClusterMetadata | None = None,
    divergence: str = "NO_CHANGE",
) -> ShadowObservation:
    cluster = cluster or _cluster()
    return ShadowObservation(
        protocol_id="RS-C1-CONTRACT-TEST",
        component_id="C1",
        manifest_sha256=HASH_A,
        candidate_set_id="SET-1",
        candidate_set_sha256=HASH_B,
        observation_id="OBS-1",
        raw_event_id="EVENT-1",
        ticker="BBCA",
        signal_at=SIGNAL,
        as_of_date=SIGNAL.date(),
        captured_at=SIGNAL + timedelta(minutes=1),
        opportunity_set_id="OPP-1",
        opportunity_set_sha256=HASH_B,
        snapshot_id="SNAP-1",
        snapshot_sha256=HASH_C,
        feature_values_sha256=HASH_A,
        portfolio_state_sha256=HASH_B,
        cluster_rule_sha256=HASH_C,
        independent_cluster_id=cluster.cluster_id,
        cluster=cluster,
        control_decision=_decision("CONTROL"),
        challenger_decision=challenger or _decision("CHALLENGER"),
        divergence=divergence,
    )


def _outcome(
    *,
    status: str = "PENDING",
    fill_status: str = "PENDING",
    terminal_event: str = "PENDING",
    horizon: int = 15,
    **overrides: object,
) -> ShadowOutcome:
    values: dict[str, object] = {
        "protocol_id": "RS-C1-CONTRACT-TEST",
        "component_id": "C1",
        "decision_role": "CONTROL",
        "manifest_sha256": HASH_A,
        "candidate_set_sha256": HASH_B,
        "outcome_id": canonical_outcome_id(
            protocol_id="RS-C1-CONTRACT-TEST",
            manifest_sha256=HASH_A,
            observation_id="OBS-1",
            raw_event_id="EVENT-1",
            ticker="BBCA",
            signal_at=SIGNAL,
            decision_role="CONTROL",
            horizon_trading_days=horizon,
        ),
        "observation_id": "OBS-1",
        "raw_event_id": "EVENT-1",
        "independent_cluster_id": "CL-1",
        "ticker": "BBCA",
        "snapshot_id": "SNAP-1",
        "snapshot_sha256": HASH_C,
        "trading_calendar_sha256": HASH_A,
        "label_definition_sha256": HASH_B,
        "cost_assumptions_sha256": HASH_C,
        "execution_policy_sha256": HASH_A,
        "horizon_trading_days": horizon,
        "primary_horizon": horizon == 15,
        "status": status,
        "fill_status": fill_status,
        "terminal_event": terminal_event,
        "signal_at": SIGNAL,
        "evaluated_at": SIGNAL + timedelta(days=1),
        "planned_geometry_sha256": HASH_B,
        "outcome_source_id": "bars-20260719",
        "outcome_source_definition_sha256": HASH_B,
        "outcome_source_sha256": HASH_C,
        "outcome_source_as_of": SIGNAL + timedelta(days=1),
        "outcome_bars_sha256": HASH_A,
        "outcome_bar_record_sha256s": (HASH_A,),
        "corporate_action_policy_sha256": HASH_B,
        "corporate_action_events_sha256": HASH_C,
        "corporate_action_event_ids": (),
        "corporate_action_event_record_sha256s": (),
        "corporate_action_event_published_ats": (),
        "bars_observed": 1,
        "reason_codes": ("PENDING",),
    }
    values.update(overrides)
    if "outcome_bar_record_sha256s" not in overrides:
        values["outcome_bar_record_sha256s"] = (
            HASH_A,
        ) * int(values["bars_observed"])
    return ShadowOutcome(**values)


def _event(
    registry: TrialRegistry,
    event_type: str,
    *,
    attempt_id: str = "ATTEMPT-1",
    sequence: int | None = None,
    previous: str | None = None,
    reason: str | None = None,
) -> TrialRegistryEvent:
    return TrialRegistryEvent(
        registry_id=registry.registry_id,
        protocol_id=registry.protocol_id,
        component_id=registry.component_id,
        manifest_sha256=registry.manifest_sha256,
        sequence=sequence if sequence is not None else registry.next_sequence,
        event_id=f"EVENT-{sequence or registry.next_sequence}-{event_type}",
        previous_event_sha256=(
            previous if previous is not None else registry.expected_previous_event_sha256
        ),
        trial_id="TRIAL-1",
        attempt_id=attempt_id,
        event_type=event_type,
        recorded_at=CREATED,
        configuration_sha256=HASH_A,
        feature_set_sha256=HASH_B,
        thresholds_sha256=HASH_C,
        code_sha256=HASH_A,
        selected_for_prospective_test=event_type == "SELECTED",
        reason=reason,
    )


def _registry() -> TrialRegistry:
    return TrialRegistry(
        registry_id="TRIAL-C1-20260717",
        protocol_id="RS-C1-CONTRACT-TEST",
        component_id="C1",
        manifest_sha256=HASH_A,
        revision=0,
    )


def test_manifest_is_strict_frozen_draft_and_rejects_legacy_lifecycle() -> None:
    draft = _manifest()
    assert draft.contract_version == "shadow-protocol-manifest-v2"
    assert draft.lifecycle_status == "DRAFT"
    assert draft.governance_mode == "SOLO_SELF_REVIEW"
    assert draft.independent_reviewer is None
    with pytest.raises(ValidationError):
        draft.protocol_id = "mutated"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        _manifest(lifecycle_status="APPROVED_FOR_COLLECTION")
    with pytest.raises(ValidationError):
        _manifest(lifecycle_status="CLOSED")
    with pytest.raises(ValidationError):
        _manifest(approval_reference="LEGACY-A1")
    with pytest.raises(ValidationError):
        _manifest(approved_at=CREATED + timedelta(days=3))


@pytest.mark.parametrize(
    "field,value",
    [
        ("evaluation_only", False),
        ("live_authority", True),
        ("affects_execution", True),
        ("affects_ranking", True),
        ("affects_sizing", True),
    ],
)
def test_authority_literals_reject_influence(field: str, value: bool) -> None:
    with pytest.raises(ValidationError):
        _manifest(**{field: value})


def test_manifest_rejects_component_mismatch_and_cluster_hash_tampering() -> None:
    with pytest.raises(ValidationError):
        _manifest(protocol_id="RS-C2-WRONG")  # type: ignore[call-arg]

    values = _manifest().model_dump()
    values["cluster_rules_sha256"] = HASH_A
    with pytest.raises(ValidationError):
        ShadowProtocolManifest(**values)


def test_manifest_rejects_nonfinite_threshold_and_duplicate_content_path() -> None:
    with pytest.raises(ValidationError):
        FrozenParameter(
            name="bad",
            value=float("nan"),
            source="test",
        )

    values = _manifest().model_dump()
    values["control_content_hashes"] = (
        ContentHash(path="same.py", sha256=HASH_A, role="CONTROL"),
        ContentHash(path="same.py", sha256=HASH_B, role="CONFIG"),
    )
    with pytest.raises(ValidationError):
        ShadowProtocolManifest(**values)


def test_canonical_hash_is_stable_and_full_length() -> None:
    left = _manifest()
    right = ShadowProtocolManifest.model_validate(left.model_dump(mode="json"))
    assert canonical_sha256(left) == canonical_sha256(right)
    assert len(canonical_sha256(left) or "") == 64


def test_observation_requires_roles_identity_and_derived_divergence() -> None:
    observation = _observation()
    assert classify_divergence(
        observation.control_decision, observation.challenger_decision
    ) == "NO_CHANGE"

    with pytest.raises(ValidationError):
        _observation(divergence="ACTIONABILITY_CHANGE")

    challenger = _decision(
        "CHALLENGER",
        state="DEPLOYABLE",
        actionable=True,
        reasons=("RR_PASS",),
    )
    changed = _observation(
        challenger=challenger,
        divergence="ACTIONABILITY_CHANGE",
    )
    assert changed.divergence == "ACTIONABILITY_CHANGE"

    values = observation.model_dump()
    values["control_decision"]["decision_role"] = "CHALLENGER"
    with pytest.raises(ValidationError):
        ShadowObservation(**values)

    values = observation.model_dump()
    values["component_id"] = "C2"
    with pytest.raises(ValidationError):
        ShadowObservation(**values)


def test_observation_rejects_unassigned_event_and_cluster_identity_mismatch() -> None:
    values = _observation().model_dump()
    values["raw_event_id"] = "EVENT-NOT-IN-CLUSTER"
    with pytest.raises(ValidationError):
        ShadowObservation(**values)

    unevaluated = _observation(cluster=_cluster(False))
    assert unevaluated.independent_cluster_id is None

    values = unevaluated.model_dump()
    values["independent_cluster_id"] = "CL-CLAIMED"
    with pytest.raises(ValidationError):
        ShadowObservation(**values)


def test_decision_rejects_inactionable_allocation_and_challenger_control_size() -> None:
    with pytest.raises(ValidationError):
        _decision("CONTROL", allocate=True)
    with pytest.raises(ValidationError):
        _decision(
            "CHALLENGER",
            actionable=True,
            size_basis="CONTROL_OBSERVED",
            fraction=0.1,
        )


def test_outcome_pending_and_mature_states_fail_closed() -> None:
    pending = _outcome()
    assert pending.status == "PENDING"

    pending_filled = _outcome(
        fill_status="FILLED",
        fill_price=100.0,
        filled_at=SIGNAL + timedelta(hours=1),
        fill_time_precision="SESSION_OPEN",
    )
    assert pending_filled.fill_price == 100.0

    with pytest.raises(ValidationError):
        _outcome(exit_price=101.0)
    with pytest.raises(ValidationError):
        _outcome(fill_status="FILLED")

    mature = _outcome(
        status="MATURE",
        fill_status="FILLED",
        terminal_event="TARGET_FIRST",
        maturity_at=SIGNAL + timedelta(days=15),
        evaluated_at=SIGNAL + timedelta(days=15),
        filled_at=SIGNAL + timedelta(days=1),
        closed_at=SIGNAL + timedelta(days=10),
        fill_price=100.0,
        exit_price=121.0,
        position_quantity_at_exit=1.0,
        invested_capital=100.0,
        exit_position_value=121.0,
        dividend_cash=0.0,
        entry_cost_cash=0.4,
        exit_cost_cash=0.6,
        total_cost_cash=1.0,
        risk_capital_basis=10.0,
        capital_return=0.21,
        dividend_return=0.0,
        gross_return=0.21,
        net_return=0.20,
        net_r=2.0,
        risk_fraction_at_fill=0.1,
        total_cost_fraction=0.01,
        fill_time_precision="SESSION_OPEN",
        reason_codes=("TARGET_FIRST",),
    )
    assert mature.primary_horizon is True

    unfilled = _outcome(
        status="MATURE",
        fill_status="EXPIRED_UNFILLED",
        terminal_event="UNFILLED",
        maturity_at=SIGNAL + timedelta(days=15),
        evaluated_at=SIGNAL + timedelta(days=15),
        bars_observed=15,
        reason_codes=("UNFILLED",),
    )
    assert unfilled.fill_price is None

    with pytest.raises(ValidationError):
        _outcome(
            status="MATURE",
            fill_status="EXPIRED_UNFILLED",
            terminal_event="UNFILLED",
            maturity_at=SIGNAL + timedelta(days=15),
            fill_price=100.0,
        )

    with pytest.raises(ValidationError):
        _outcome(horizon=10, primary_horizon=True)


def test_outcome_invalid_state_and_same_bar_ambiguity_require_explanation() -> None:
    invalid = _outcome(
        status="INVALID",
        fill_status="INVALID",
        terminal_event="INVALID",
        reason_codes=("SOURCE_INVALID",),
    )
    assert invalid.status == "INVALID"

    with pytest.raises(ValidationError):
        _outcome(
            status="MATURE",
            fill_status="FILLED",
            terminal_event="TIMEOUT",
            maturity_at=SIGNAL + timedelta(days=15),
            evaluated_at=SIGNAL + timedelta(days=15),
            filled_at=SIGNAL + timedelta(days=1),
            closed_at=SIGNAL + timedelta(days=15),
            fill_price=100.0,
            exit_price=100.0,
            position_quantity_at_exit=1.0,
            invested_capital=100.0,
            exit_position_value=100.0,
            dividend_cash=0.0,
            entry_cost_cash=0.4,
            exit_cost_cash=0.6,
            total_cost_cash=1.0,
            risk_capital_basis=10.0,
            capital_return=0.0,
            dividend_return=0.0,
            gross_return=0.0,
            net_return=-0.01,
            net_r=-0.1,
            risk_fraction_at_fill=0.1,
            total_cost_fraction=0.01,
            fill_time_precision="SESSION_OPEN",
            same_bar_ambiguous=True,
        )


def test_trial_registry_is_hash_chained_append_only_and_idempotent() -> None:
    registry = _registry()
    registered = _event(registry, "REGISTERED")
    registry = registry.append_event(registered)
    started = _event(registry, "STARTED")
    registry = registry.append_event(started)
    completed = _event(registry, "COMPLETED")
    registry = registry.append_event(completed)
    selected = _event(registry, "SELECTED")
    registry = registry.append_event(selected)
    assert registry.revision == 4
    assert registry.next_sequence == 5
    assert registry.append_event(selected) == registry

    with pytest.raises(ShadowContractError):
        registry.append_event(selected.model_copy(update={"reason": "tampered"}))

    with pytest.raises(ValidationError):
        empty = _registry()
        empty.append_event(_event(empty, "STARTED"))


def test_trial_registry_rejects_bad_hash_transition_and_terminal_append() -> None:
    registry = _registry().append_event(_event(_registry(), "REGISTERED"))
    with pytest.raises(ValidationError):
        registry.append_event(
            _event(
                registry,
                "STARTED",
                previous=HASH_A,
            )
        )

    registry = registry.append_event(_event(registry, "STARTED"))
    registry = registry.append_event(_event(registry, "FAILED", reason="test failure"))
    with pytest.raises(ValidationError):
        registry.append_event(_event(registry, "STARTED"))


def test_trial_registry_rejects_mid_attempt_configuration_change() -> None:
    empty = _registry()
    registry = empty.append_event(_event(empty, "REGISTERED"))
    started = _event(registry, "STARTED")
    with pytest.raises(ValidationError):
        registry.append_event(started.model_copy(update={"configuration_sha256": HASH_B}))


def test_trial_registry_requires_reason_for_discard_and_selection_flag_matches() -> None:
    registry = _registry()
    with pytest.raises(ValidationError):
        _event(registry, "DISCARDED")

    values = _event(registry, "REGISTERED").model_dump()
    values["selected_for_prospective_test"] = True
    with pytest.raises(ValidationError):
        TrialRegistryEvent(**values)


def test_effective_sample_metadata_never_promotes_raw_rows_to_independence() -> None:
    metadata = EffectiveSampleMetadata(
        protocol_id="RS-C1-CONTRACT-TEST",
        component_id="C1",
        manifest_sha256=HASH_A,
        observation_set_sha256=HASH_B,
        cluster_rule_sha256=HASH_C,
        computed_at=CREATED,
        status="ESTIMABLE",
        raw_n=4,
        assigned_raw_n=3,
        independent_cluster_n=2,
        effective_n=1.8,
        cluster_ids=("CL-1", "CL-2"),
        unique_tickers=2,
        unique_issuers=2,
        unique_signal_dates=2,
        unique_event_blocks=2,
        calculation_rule="predeclared cluster-weight rule",
    )
    assert metadata.effective_n == 1.8
    assert metadata.unit_of_analysis == "INDEPENDENT_CLUSTER"
    assert metadata.raw_rows_are_independent is False

    with pytest.raises(ValidationError):
        EffectiveSampleMetadata(
            **metadata.model_dump(exclude={"effective_n", "status"}),
            status="NOT_ESTIMABLE",
            effective_n=1.0,
        )

    values = metadata.model_dump()
    values["effective_n"] = 3.0
    with pytest.raises(ValidationError):
        EffectiveSampleMetadata(**values)


def test_shadow_protocol_module_is_import_isolated_from_live_paths() -> None:
    source = Path(__file__).parents[1] / "core" / "shadow_protocol" / "contracts.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden = ("risk_governor", "orchestrator", "position_sizer", "execution_ledger")
    assert not any(
        token in module for token in forbidden for module in imported_modules
    )
