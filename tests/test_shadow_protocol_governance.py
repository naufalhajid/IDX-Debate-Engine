"""Governance acceptance tests for the v2 shadow-protocol authority boundary.

These tests intentionally exercise only evaluation-only contracts and the
local governance artifact store.  They never invoke ranking, sizing, order, or
live-execution paths.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from core.shadow_protocol.calendar import (
    TRADING_CALENDAR_VERSION,
    TradingCalendar,
    canonical_trading_calendar_sha256,
    derive_completed_idx_sessions,
    session_close_at,
)
from core.shadow_protocol.contracts import (
    ApprovalLedgerEvent,
    ApprovalRecord,
    ClusterRuleDefinition,
    ContentHash,
    CostAssumptions,
    GoNoGoRules,
    IndependentClusterMetadata,
    LabelDefinition,
    ProtocolClosureRecord,
    SELF_ADVERSARIAL_PROMPTS,
    SelfAdversarialReviewItem,
    ShadowContractError,
    ShadowDecision,
    ShadowObservation,
    ShadowProtocolManifest,
    SourceDefinition,
    UniverseDefinition,
    canonical_decision_payload_sha256,
    canonical_json_bytes,
    canonical_rules_sha256,
    canonical_sha256,
)
from core.shadow_protocol.governance import (
    ProtocolGovernanceStore,
    load_approval_record_v1,
    load_protocol_closure_v1,
    load_shadow_protocol_manifest_v2,
    verify_approval_binding,
    verify_maturation_authorization,
)


IDX = ZoneInfo("Asia/Jakarta")
PROTOCOL_ID = "RS-C1-SHADOW-20260717-01"
LEDGER_ID = "LEDGER-RS-C1-SHADOW-20260717-01"
APPROVAL_ID = "A1-RS-C1-SHADOW-20260717-01"
METHODOLOGY = b"# Frozen C1 methodology\n\nPredeclared shadow-only method.\n"
FREEZE = datetime(2026, 7, 17, 16, 30, tzinfo=IDX)
DECIDED = datetime(2026, 7, 21, 16, 30, tzinfo=IDX)
COLLECTION_START = datetime(2026, 7, 22, 9, 0, tzinfo=IDX)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _weekday_sessions(start: date, count: int) -> tuple[date, ...]:
    sessions: list[date] = []
    cursor = start
    while len(sessions) < count:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return tuple(sessions)


SESSIONS = _weekday_sessions(date(2026, 7, 17), 30)


def _digest(model: object) -> str:
    digest = canonical_sha256(model)  # type: ignore[arg-type]
    assert digest is not None
    return digest


def _raw(model: object) -> bytes:
    payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ).encode("utf-8")


def _artifact_snapshot(root: Path) -> dict[str, bytes]:
    """Capture every immutable artifact so rejected writes are observable."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _calendar(
    *,
    calendar_id: str = "IDX-FROZEN-20260717-20260827",
    sessions: tuple[date, ...] = SESSIONS,
) -> TradingCalendar:
    return TradingCalendar(
        calendar_id=calendar_id,
        calendar_sha256=canonical_trading_calendar_sha256(
            calendar_id,
            sessions,
        ),
        sessions=sessions,
    )


def _cluster_rules() -> ClusterRuleDefinition:
    return ClusterRuleDefinition(
        rule_version="cluster-v1",
        issuer_group_rule="same issuer",
        economic_group_rule="same economic group",
        correlation_cluster_rule="frozen correlation-cluster lookup",
        systemic_date_block_rule="same signal-date block",
        duplicate_setup_rule="same ticker and setup geometry",
        representative_rule="first event by signal time",
        effective_n_rule="one independent representative per cluster",
    )


def _manifest(
    calendar: TradingCalendar,
    **overrides: object,
) -> ShadowProtocolManifest:
    cluster_rules = _cluster_rules()
    go = ("GO only when every predeclared criterion passes.",)
    continue_rules = ("CONTINUE while minimum independent N is unmet.",)
    no_go = ("NO-GO on any integrity or authority breach.",)
    values: dict[str, object] = {
        "protocol_id": PROTOCOL_ID,
        "component_id": "C1",
        "manifest_revision": 1,
        "lifecycle_status": "DRAFT",
        "created_at": FREEZE - timedelta(hours=1),
        "draft_frozen_at": FREEZE,
        "collection_start_not_before": COLLECTION_START,
        "fixed_terminal_date": calendar.sessions[-1],
        "owner": "owner@example.test",
        "governance_mode": "SOLO_SELF_REVIEW",
        "independent_reviewer": None,
        "rollback_owner": "owner@example.test",
        "baseline_manifest_id": "RS-CONTROL-20260717-01",
        "baseline_manifest_sha256": HASH_A,
        "methodology_document_path": (
            "docs/research/SHADOW_MODE_PROTOCOL.md"
        ),
        "methodology_document_sha256": hashlib.sha256(
            METHODOLOGY
        ).hexdigest(),
        "control_content_hashes": (
            ContentHash(
                path="control/current.py",
                sha256=HASH_A,
                role="CONTROL",
            ),
        ),
        "challenger_content_hashes": (
            ContentHash(
                path="challenger/c1.py",
                sha256=HASH_B,
                role="CHALLENGER",
            ),
        ),
        "universe": UniverseDefinition(
            universe_id="IDX-C1-FROZEN-UNIVERSE",
            quant_mode="MOMENTUM",
            selection_rule="complete point-in-time candidate set",
            candidate_source_sha256=HASH_A,
            explicit_tickers=("BBCA",),
        ),
        "trading_calendar_id": calendar.calendar_id,
        "trading_calendar_sha256": calendar.calendar_sha256,
        "corporate_action_policy_sha256": HASH_B,
        "thresholds": (),
        "features": (),
        "sources": (
            SourceDefinition(
                source_id="FROZEN_SNAPSHOT",
                source_type="FILE",
                locator="shadow://frozen-snapshot",
                as_of_field="snapshot_as_of",
                expiry_rule="reject after the frozen expiry instant",
                missing_policy="ABSTAIN",
                contract_version="source-contract-v1",
                source_sha256=HASH_C,
            ),
        ),
        "labels": LabelDefinition(
            entry_validity_trading_days=3,
            activation_rule="first trading session after signal",
            horizon_clock_rule="post-fill sessions excluding fill session",
            fill_rule="entry limit touched by observed market data",
            gap_rule="use observed open for marketable gaps",
            entry_gap_through_stop_rule=(
                "fill and stop at observed open using planned risk"
            ),
            same_bar_ambiguity_rule="stop first",
            corporate_action_rule="apply frozen point-in-time policy",
            rights_treatment_rule="invalidate until full terms are frozen",
            dividend_return_convention="PRICE_RETURN",
            dividend_entitlement_rule="position open before ex-date",
            unfilled_rule="expire after entry-validity sessions",
        ),
        "costs": CostAssumptions(
            buy_commission_bps=15.0,
            sell_commission_bps=25.0,
            sell_tax_bps=10.0,
            slippage_bps=5.0,
            bid_ask_bps=5.0,
            lot_size=100,
            liquidity_execution_rule="one frozen evaluation lot",
            price_rounding_rule="frozen IDX tick rule",
            cost_model_version="shadow-cost-v1",
        ),
        "cluster_rules": cluster_rules,
        "cluster_rules_sha256": _digest(cluster_rules),
        "go_no_go": GoNoGoRules(
            go=go,
            continue_rules=continue_rules,
            no_go=no_go,
            rules_sha256=canonical_rules_sha256(
                go,
                continue_rules,
                no_go,
            ),
        ),
        "trial_registry_id": "TRIAL-RS-C1-SHADOW-20260717-01",
        "production_feature_flag": "SHADOW_C1_CHALLENGER",
        "production_feature_flag_default": False,
        "rollback_plan": (
            "close collection, preserve evidence, and leave control unchanged"
        ),
    }
    values.update(overrides)
    return ShadowProtocolManifest.model_validate(values)


def _review_items() -> tuple[SelfAdversarialReviewItem, ...]:
    return tuple(
        SelfAdversarialReviewItem(
            prompt_id=prompt_id,
            prompt_text=prompt_text,
            response=f"Evidence-backed response for {prompt_id}.",
            evidence_refs=(f"evidence://{prompt_id.lower()}",),
            disposition="PASS",
        )
        for prompt_id, prompt_text in SELF_ADVERSARIAL_PROMPTS.items()
    )


def _approval(
    manifest: ShadowProtocolManifest,
    manifest_raw: bytes,
    calendar: TradingCalendar,
    **overrides: object,
) -> ApprovalRecord:
    decided_at = overrides.get("decided_at", DECIDED)
    assert isinstance(decided_at, datetime)
    independent = manifest.governance_mode == "INDEPENDENT_REVIEW"
    values: dict[str, object] = {
        "approval_id": APPROVAL_ID,
        "approval_ledger_id": LEDGER_ID,
        "approval_gate": "A1",
        "approval_scope": "SHADOW_COLLECTION_ONLY",
        "approval_decision": "APPROVED_FOR_COLLECTION",
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_contract_version": manifest.contract_version,
        "manifest_revision": manifest.manifest_revision,
        "draft_manifest_canonical_sha256": _digest(manifest),
        "draft_manifest_raw_file_sha256": hashlib.sha256(
            manifest_raw
        ).hexdigest(),
        "draft_manifest_raw_byte_length": len(manifest_raw),
        "draft_frozen_at": manifest.draft_frozen_at,
        "decided_at": decided_at,
        "owner": manifest.owner,
        "governance_mode": manifest.governance_mode,
        "approved_by": (
            manifest.independent_reviewer if independent else manifest.owner
        ),
        "independent_reviewer": manifest.independent_reviewer,
        "trading_calendar_contract_version": TRADING_CALENDAR_VERSION,
        "trading_calendar_id": calendar.calendar_id,
        "trading_calendar_sha256": calendar.calendar_sha256,
        "completed_idx_trading_sessions": (
            ()
            if independent
            else derive_completed_idx_sessions(
                calendar,
                draft_frozen_at=manifest.draft_frozen_at,
                decided_at=decided_at,
            )
        ),
        "canonical_hash_recomputed": True,
        "raw_file_hash_recomputed": True,
        "automated_contract_validation_passed": True,
        "self_adversarial_review": () if independent else _review_items(),
        "attestation": (
            "I approve A1 for this exact manifest hash pair, for shadow "
            "collection only, with live_authority=false."
        ),
    }
    values.update(overrides)
    return ApprovalRecord.model_validate(values)


def _closure(
    manifest: ShadowProtocolManifest,
    manifest_raw: bytes,
    approval: ApprovalRecord,
    *,
    effective_at: datetime,
    reason_code: str = "OWNER_REQUEST",
    maturation_policy: str = "ALLOW_PRE_CLOSURE_MATURATION",
    closure_id: str = "CLOSE-RS-C1-SHADOW-20260717-01",
    **overrides: object,
) -> ProtocolClosureRecord:
    values: dict[str, object] = {
        "closure_id": closure_id,
        "approval_ledger_id": approval.approval_ledger_id,
        "closure_scope": "STOP_NEW_SHADOW_COLLECTION",
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_contract_version": manifest.contract_version,
        "manifest_revision": manifest.manifest_revision,
        "draft_manifest_canonical_sha256": _digest(manifest),
        "draft_manifest_raw_file_sha256": hashlib.sha256(
            manifest_raw
        ).hexdigest(),
        "draft_manifest_raw_byte_length": len(manifest_raw),
        "approval_id": approval.approval_id,
        "approval_record_canonical_sha256": _digest(approval),
        "effective_at": effective_at,
        "recorded_at": effective_at,
        "closed_by": manifest.owner,
        "governance_mode": manifest.governance_mode,
        "reason_code": reason_code,
        "reason": f"Predeclared closure reason: {reason_code}.",
        "maturation_policy": maturation_policy,
        "preserve_artifacts": True,
        "new_observations_allowed": False,
        "authorizes_unblinding": False,
        "authorizes_promotion": False,
    }
    values.update(overrides)
    return ProtocolClosureRecord.model_validate(values)


def _decision(role: str) -> ShadowDecision:
    values: dict[str, object] = {
        "decision_role": role,
        "decision_state": "NO_TRADE",
        "rating": None,
        "would_be_actionable": False,
        "would_allocate": False,
        "recorded_rank": None,
        "recorded_position_fraction": None,
        "position_size_basis": "NONE",
        "reason_codes": ("FROZEN_TEST_REASON",),
        "gate_measurements": (),
        "geometry": None,
    }
    unhashed = ShadowDecision.model_construct(
        **values,
        decision_payload_sha256=HASH_A,
    )
    return ShadowDecision.model_validate(
        {
            **values,
            "decision_payload_sha256": canonical_decision_payload_sha256(
                unhashed
            ),
        }
    )


def _observation(
    manifest: ShadowProtocolManifest,
    *,
    observation_id: str = "OBS-RS-C1-0001",
    signal_at: datetime = COLLECTION_START + timedelta(minutes=5),
    captured_at: datetime | None = None,
    **overrides: object,
) -> ShadowObservation:
    resolved_capture = captured_at or signal_at + timedelta(minutes=1)
    control = _decision("CONTROL")
    challenger = _decision("CHALLENGER")
    values: dict[str, object] = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": _digest(manifest),
        "candidate_set_id": "CANDIDATE-SET-0001",
        "candidate_set_sha256": HASH_A,
        "observation_id": observation_id,
        "raw_event_id": f"RAW-{observation_id}",
        "ticker": "BBCA",
        "signal_at": signal_at,
        "as_of_date": signal_at.astimezone(IDX).date(),
        "captured_at": resolved_capture,
        "opportunity_set_id": "OPPORTUNITY-SET-0001",
        "opportunity_set_sha256": HASH_B,
        "snapshot_id": "SNAPSHOT-0001",
        "snapshot_sha256": HASH_C,
        "feature_values_sha256": HASH_A,
        "portfolio_state_sha256": HASH_B,
        "cluster_rule_sha256": manifest.cluster_rules_sha256,
        "independent_cluster_id": None,
        "cluster": IndependentClusterMetadata(
            assignment_status="NOT_EVALUATED_FOR_INDEPENDENCE",
            cluster_rule_sha256=manifest.cluster_rules_sha256,
            raw_event_count=0,
        ),
        "control_decision": control,
        "challenger_decision": challenger,
        "divergence": "NO_CHANGE",
    }
    values.update(overrides)
    return ShadowObservation.model_validate(values)


def _approved_store(
    tmp_path: Path,
    *,
    manifest: ShadowProtocolManifest | None = None,
    calendar: TradingCalendar | None = None,
) -> tuple[
    ProtocolGovernanceStore,
    TradingCalendar,
    ShadowProtocolManifest,
    bytes,
    ApprovalRecord,
    bytes,
]:
    trusted_calendar = calendar or _calendar()
    trusted_manifest = manifest or _manifest(trusted_calendar)
    manifest_raw = _raw(trusted_manifest)
    approval = _approval(
        trusted_manifest,
        manifest_raw,
        trusted_calendar,
    )
    approval_raw = _raw(approval)
    store = ProtocolGovernanceStore(tmp_path / "governance")
    store.persist_trading_calendar(trusted_calendar)
    store.persist_manifest(manifest_raw, METHODOLOGY)
    store.append_approval(approval_raw)
    return (
        store,
        trusted_calendar,
        trusted_manifest,
        manifest_raw,
        approval,
        approval_raw,
    )


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("draft_manifest_canonical_sha256", HASH_C),
        ("draft_manifest_raw_file_sha256", HASH_C),
        ("draft_manifest_raw_byte_length", 1),
    ),
)
def test_a1_rejects_manifest_hash_or_byte_length_mismatch(
    tmp_path: Path,
    field_name: str,
    bad_value: object,
) -> None:
    store, calendar, manifest, manifest_raw, approval, _ = _approved_store(
        tmp_path
    )
    bundle = store.load_authorization(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
    )
    bad = ApprovalRecord.model_validate(
        {
            **approval.model_dump(mode="python"),
            field_name: bad_value,
        }
    )
    with pytest.raises(
        ShadowContractError,
        match="manifest hash/length differs",
    ):
        verify_approval_binding(
            manifest=manifest,
            manifest_raw_file_bytes=manifest_raw,
            methodology_document_bytes=METHODOLOGY,
            approval=bad,
            approval_raw_file_bytes=_raw(bad),
            approval_ledger=bundle.approval_ledger,
            trading_calendar=calendar,
        )


def test_a1_rejects_raw_only_manifest_reformat() -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    approval = _approval(manifest, manifest_raw, calendar)
    approval_raw = _raw(approval)
    alternate_raw = canonical_json_bytes(manifest)
    assert alternate_raw != manifest_raw
    assert _digest(load_shadow_protocol_manifest_v2(alternate_raw)) == _digest(
        manifest
    )

    with pytest.raises(
        ShadowContractError,
        match="manifest hash/length differs",
    ):
        verify_approval_binding(
            manifest=manifest,
            manifest_raw_file_bytes=alternate_raw,
            methodology_document_bytes=METHODOLOGY,
            approval=approval,
            approval_raw_file_bytes=approval_raw,
            approval_ledger=_approved_store_for_ledger(
                calendar,
                manifest,
                manifest_raw,
                approval,
                approval_raw,
            ),
            trading_calendar=calendar,
        )


def _approved_store_for_ledger(
    calendar: TradingCalendar,
    manifest: ShadowProtocolManifest,
    manifest_raw: bytes,
    approval: ApprovalRecord,
    approval_raw: bytes,
):
    """Build the exact immutable ledger object without touching a filesystem."""

    from core.shadow_protocol.contracts import ApprovalLedger, ApprovalLedgerEvent

    event = ApprovalLedgerEvent(
        ledger_id=approval.approval_ledger_id,
        protocol_id=approval.protocol_id,
        component_id=approval.component_id,
        manifest_revision=approval.manifest_revision,
        draft_manifest_canonical_sha256=_digest(manifest),
        draft_manifest_raw_file_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        draft_manifest_raw_byte_length=len(manifest_raw),
        sequence=1,
        event_id=f"{approval.approval_id}-LEDGER",
        previous_event_sha256=None,
        event_type="A1_APPROVED",
        record_kind="APPROVAL",
        record_id=approval.approval_id,
        record_contract_version=approval.contract_version,
        record_canonical_sha256=_digest(approval),
        record_raw_file_sha256=hashlib.sha256(approval_raw).hexdigest(),
        record_raw_byte_length=len(approval_raw),
        recorded_at=approval.decided_at,
    )
    return ApprovalLedger(
        ledger_id=approval.approval_ledger_id,
        protocol_id=approval.protocol_id,
        component_id=approval.component_id,
        manifest_revision=approval.manifest_revision,
        draft_manifest_canonical_sha256=_digest(manifest),
        draft_manifest_raw_file_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        draft_manifest_raw_byte_length=len(manifest_raw),
        events=(event,),
    )


@pytest.mark.parametrize(
    ("loader", "version"),
    (
        (
            load_shadow_protocol_manifest_v2,
            "shadow-protocol-manifest-v2",
        ),
        (load_approval_record_v1, "shadow-approval-record-v1"),
        (load_protocol_closure_v1, "shadow-protocol-closure-v1"),
    ),
)
def test_governance_loaders_reject_duplicate_json_keys(
    loader,
    version: str,
) -> None:
    raw = (
        f'{{"contract_version":"{version}",'
        f'"contract_version":"{version}"}}'
    ).encode("utf-8")
    with pytest.raises(ShadowContractError, match="duplicate JSON key"):
        loader(raw)


def test_solo_a1_requires_72_elapsed_hours() -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    with pytest.raises(ValidationError, match="72 elapsed hours"):
        _approval(
            manifest,
            manifest_raw,
            calendar,
            decided_at=FREEZE + timedelta(hours=71, minutes=59),
        )


def test_solo_a1_requires_two_completed_idx_sessions() -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    with pytest.raises(ValidationError, match="two completed IDX sessions"):
        _approval(
            manifest,
            manifest_raw,
            calendar,
            completed_idx_trading_sessions=(date(2026, 7, 20),),
        )


def test_solo_a1_recomputes_exact_sessions_from_frozen_calendar(
    tmp_path: Path,
) -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    forged = _approval(
        manifest,
        manifest_raw,
        calendar,
        completed_idx_trading_sessions=(
            date(2026, 7, 19),
            date(2026, 7, 20),
        ),
    )
    store = ProtocolGovernanceStore(tmp_path / "governance")
    store.persist_trading_calendar(calendar)
    store.persist_manifest(manifest_raw, METHODOLOGY)

    with pytest.raises(
        ShadowContractError,
        match="sessions differ from the trusted calendar derivation",
    ):
        store.append_approval(_raw(forged))


def test_collection_window_cannot_begin_before_a1_decision(
    tmp_path: Path,
) -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    late_approval = _approval(
        manifest,
        manifest_raw,
        calendar,
        decided_at=COLLECTION_START + timedelta(hours=1),
    )
    store = ProtocolGovernanceStore(tmp_path / "governance")
    store.persist_trading_calendar(calendar)
    store.persist_manifest(manifest_raw, METHODOLOGY)

    with pytest.raises(
        ShadowContractError,
        match="collection window begins before A1 approval",
    ):
        store.append_approval(_raw(late_approval))


def test_collection_rejects_signals_outside_frozen_window(
    tmp_path: Path,
) -> None:
    store, _, manifest, _, approval, _ = _approved_store(tmp_path)
    early_signal = COLLECTION_START - timedelta(minutes=1)
    early = _observation(
        manifest,
        observation_id="OBS-EARLY",
        signal_at=early_signal,
    )
    with pytest.raises(
        ShadowContractError,
        match="signal predates collection window",
    ):
        store.persist_observation(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
            observation=early,
            attempted_at=early.captured_at + timedelta(minutes=1),
        )

    late_signal = datetime.combine(
        manifest.fixed_terminal_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=IDX,
    ) + timedelta(hours=9)
    late = _observation(
        manifest,
        observation_id="OBS-LATE",
        signal_at=late_signal,
    )
    with pytest.raises(
        ShadowContractError,
        match="after fixed terminal date",
    ):
        store.persist_observation(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
            observation=late,
            attempted_at=late.captured_at + timedelta(minutes=1),
        )


def test_fake_or_self_claimed_reviewers_are_rejected() -> None:
    calendar = _calendar()
    with pytest.raises(
        ValidationError,
        match="solo mode must not claim an independent reviewer",
    ):
        _manifest(calendar, independent_reviewer="fake@example.test")

    with pytest.raises(
        ValidationError,
        match="independent reviewer must differ from owner",
    ):
        _manifest(
            calendar,
            governance_mode="INDEPENDENT_REVIEW",
            independent_reviewer="OWNER@EXAMPLE.TEST",
        )

    independent_manifest = _manifest(
        calendar,
        governance_mode="INDEPENDENT_REVIEW",
        independent_reviewer="reviewer@example.test",
    )
    with pytest.raises(
        ValidationError,
        match="must be signed by the reviewer",
    ):
        _approval(
            independent_manifest,
            _raw(independent_manifest),
            calendar,
            approved_by=independent_manifest.owner,
        )


def test_solo_review_requires_exact_12_prompt_texts_order_and_pass() -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    reviews = _review_items()
    assert len(SELF_ADVERSARIAL_PROMPTS) == 12
    assert tuple(item.prompt_id for item in reviews) == tuple(
        SELF_ADVERSARIAL_PROMPTS
    )

    with pytest.raises(ValidationError, match="frozen prompt"):
        SelfAdversarialReviewItem(
            prompt_id=reviews[0].prompt_id,
            prompt_text=f"{reviews[0].prompt_text} altered",
            response=reviews[0].response,
            evidence_refs=reviews[0].evidence_refs,
            disposition="PASS",
        )

    for invalid_reviews in (
        reviews[:-1],
        tuple(reversed(reviews)),
    ):
        with pytest.raises(
            ValidationError,
            match="every frozen review prompt in order",
        ):
            _approval(
                manifest,
                manifest_raw,
                calendar,
                self_adversarial_review=invalid_reviews,
            )

    failed_first = SelfAdversarialReviewItem(
        **{
            **reviews[0].model_dump(mode="python"),
            "disposition": "FAIL",
        }
    )
    with pytest.raises(
        ValidationError,
        match="every review item to pass",
    ):
        _approval(
            manifest,
            manifest_raw,
            calendar,
            self_adversarial_review=(failed_first, *reviews[1:]),
        )


def test_a1_identity_tuple_must_match_manifest(tmp_path: Path) -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    attacker = _approval(
        manifest,
        manifest_raw,
        calendar,
        owner="attacker@example.test",
        approved_by="attacker@example.test",
    )
    store = ProtocolGovernanceStore(tmp_path / "governance")
    store.persist_trading_calendar(calendar)
    store.persist_manifest(manifest_raw, METHODOLOGY)

    with pytest.raises(
        ShadowContractError,
        match="A1 identity tuple differs from manifest",
    ):
        store.append_approval(_raw(attacker))


def test_a1_calendar_must_match_manifest(tmp_path: Path) -> None:
    calendar = _calendar()
    alternate = _calendar(calendar_id="IDX-FROZEN-ALTERNATE")
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    approval = _approval(manifest, manifest_raw, alternate)
    store = ProtocolGovernanceStore(tmp_path / "governance")
    store.persist_trading_calendar(calendar)
    store.persist_trading_calendar(alternate)
    store.persist_manifest(manifest_raw, METHODOLOGY)

    with pytest.raises(
        ShadowContractError,
        match="trading calendar differs from manifest",
    ):
        store.append_approval(_raw(approval))


def test_manifest_methodology_bytes_must_match_declared_hash(
    tmp_path: Path,
) -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    store = ProtocolGovernanceStore(tmp_path / "governance")
    store.persist_trading_calendar(calendar)

    with pytest.raises(
        ShadowContractError,
        match="methodology document SHA-256 mismatch",
    ):
        store.persist_manifest(_raw(manifest), b"tampered methodology")


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("evaluation_only", False),
        ("live_authority", True),
        ("affects_execution", True),
        ("affects_ranking", True),
        ("affects_sizing", True),
    ),
)
def test_authority_literals_cannot_be_elevated(
    field_name: str,
    bad_value: bool,
) -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    approval = _approval(manifest, manifest_raw, calendar)
    with pytest.raises(ValidationError):
        ApprovalRecord.model_validate(
            {
                **approval.model_dump(mode="python"),
                field_name: bad_value,
            }
        )


def test_store_is_content_addressed_and_ledger_is_ordered(
    tmp_path: Path,
) -> None:
    calendar = _calendar()
    manifest = _manifest(calendar)
    manifest_raw = _raw(manifest)
    approval = _approval(manifest, manifest_raw, calendar)
    approval_raw = _raw(approval)
    store = ProtocolGovernanceStore(tmp_path / "governance")
    store.persist_trading_calendar(calendar)
    manifest_path = store.persist_manifest(manifest_raw, METHODOLOGY)
    ledger = store.append_approval(approval_raw)

    manifest_raw_hash = hashlib.sha256(manifest_raw).hexdigest()
    assert manifest_path.name == "manifest.json"
    assert manifest_path.parent.name == manifest_raw_hash
    assert manifest_path.parents[2].name == _digest(manifest)
    assert [event.event_type for event in ledger.events] == ["A1_APPROVED"]

    observation = _observation(manifest)
    attempted_at = observation.captured_at + timedelta(minutes=1)
    observation_path = store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=attempted_at,
    )
    observation_hash = hashlib.sha256(
        canonical_json_bytes(observation)
    ).hexdigest()
    assert observation_path.parent.name == observation_hash
    assert observation_path.stem == observation_hash

    closure = _closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=attempted_at + timedelta(minutes=1),
    )
    ledger = store.append_closure(_raw(closure))
    assert [event.event_type for event in ledger.events] == [
        "A1_APPROVED",
        "OBSERVATION_AUTHORIZED",
        "PROTOCOL_CLOSED",
    ]
    assert [event.sequence for event in ledger.events] == [1, 2, 3]
    assert len(store.append_closure(_raw(closure)).events) == 3

    different = _closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=closure.effective_at,
        closure_id="CLOSE-DIFFERENT",
    )
    with pytest.raises(
        ShadowContractError,
        match="different closure already recorded",
    ):
        store.append_closure(_raw(different))


def test_terminal_ledger_event_tampering_is_detected(tmp_path: Path) -> None:
    store, _, manifest, _, approval, _ = _approved_store(tmp_path)
    event_paths = list(
        (tmp_path / "governance").rglob("0000000001*.json")
    )
    assert len(event_paths) == 1
    event_path = event_paths[0]
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    payload["event_id"] = "TAMPERED-EVENT-ID"
    event_path.write_text(
        json.dumps(payload, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(ShadowContractError):
        store.load_approval_ledger(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )


def test_owner_closure_blocks_new_collection_but_allows_preclosure_maturation(
    tmp_path: Path,
) -> None:
    store, _, manifest, manifest_raw, approval, _ = _approved_store(tmp_path)
    observation = _observation(manifest)
    attempted_at = observation.captured_at + timedelta(minutes=1)
    store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=attempted_at,
    )
    closure = _closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=attempted_at + timedelta(minutes=1),
    )
    store.append_closure(_raw(closure))
    authorization = store.load_authorization(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
    )

    assert (
        verify_maturation_authorization(authorization, observation)
        == observation
    )
    new_observation = _observation(
        manifest,
        observation_id="OBS-RS-C1-0002",
        signal_at=observation.signal_at + timedelta(minutes=10),
    )
    with pytest.raises(
        ShadowContractError,
        match="closed to new observations",
    ):
        store.persist_observation(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
            observation=new_observation,
            attempted_at=closure.effective_at + timedelta(minutes=1),
        )


def test_integrity_closure_blocks_even_preclosure_maturation(
    tmp_path: Path,
) -> None:
    store, _, manifest, manifest_raw, approval, _ = _approved_store(tmp_path)
    observation = _observation(manifest)
    attempted_at = observation.captured_at + timedelta(minutes=1)
    store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=attempted_at,
    )
    closure = _closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=attempted_at + timedelta(minutes=1),
        reason_code="INTEGRITY_STOP",
        maturation_policy="BLOCK_ALL_MATURATION",
    )
    store.append_closure(_raw(closure))
    authorization = store.load_authorization(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
    )

    with pytest.raises(
        ShadowContractError,
        match="closure blocks all maturation",
    ):
        verify_maturation_authorization(authorization, observation)

    with pytest.raises(
        ValidationError,
        match="integrity closure must block maturation",
    ):
        _closure(
            manifest,
            manifest_raw,
            approval,
            effective_at=closure.effective_at,
            reason_code="SOURCE_CORRUPTION",
            maturation_policy="ALLOW_PRE_CLOSURE_MATURATION",
            closure_id="INVALID-INTEGRITY-CLOSURE",
        )


def test_manifest_v1_is_explicitly_audit_only_and_rejected() -> None:
    raw = json.dumps(
        {"contract_version": "shadow-protocol-manifest-v1"}
    ).encode("utf-8")
    with pytest.raises(
        ShadowContractError,
        match="audit-only.*explicit migration to v2",
    ):
        load_shadow_protocol_manifest_v2(raw)


def test_forecasting_shadow_evaluation_v1_is_not_reinterpreted_as_manifest_v2(
) -> None:
    raw = json.dumps(
        {
            "contract_version": "shadow-evaluation-v1",
            "evaluation_only": True,
            "live_authority": False,
        }
    ).encode("utf-8")
    with pytest.raises(
        ShadowContractError,
        match="unsupported shadow manifest contract version",
    ):
        load_shadow_protocol_manifest_v2(raw)


def test_exact_manifest_can_bind_only_one_approval_ledger(
    tmp_path: Path,
) -> None:
    store, calendar, manifest, manifest_raw, approval, _ = _approved_store(
        tmp_path
    )
    competing = _approval(
        manifest,
        manifest_raw,
        calendar,
        approval_id="A1-COMPETING",
        approval_ledger_id="LEDGER-COMPETING",
    )

    with pytest.raises(
        ShadowContractError,
        match="immutable governance artifact collision",
    ):
        store.append_approval(_raw(competing))

    surviving = store.load_approval_ledger(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
    )
    assert [event.record_id for event in surviving.events] == [
        approval.approval_id
    ]


def test_protocol_manifest_revision_is_globally_immutable(
    tmp_path: Path,
) -> None:
    calendar = _calendar()
    first = _manifest(calendar)
    competing = _manifest(
        calendar,
        trial_registry_id="TRIAL-COMPETING-CONTENT",
    )
    assert _digest(first) != _digest(competing)
    store = ProtocolGovernanceStore(tmp_path / "governance")
    store.persist_trading_calendar(calendar)
    store.persist_manifest(_raw(first), METHODOLOGY)
    reference_path = next(
        (tmp_path / "governance").rglob(
            "manifest_revisions/00000001.json"
        )
    )
    winner_reference = reference_path.read_bytes()

    with pytest.raises(
        ShadowContractError,
        match="immutable governance artifact collision",
    ):
        store.persist_manifest(_raw(competing), METHODOLOGY)

    assert reference_path.read_bytes() == winner_reference
    assert len(
        list(
            (tmp_path / "governance").rglob(
                "manifest_revisions/00000001.json"
            )
        )
    ) == 1


def test_observation_retry_is_idempotent_and_id_collision_fails_cleanly(
    tmp_path: Path,
) -> None:
    store, _, manifest, _, approval, _ = _approved_store(tmp_path)
    observation = _observation(manifest)
    attempted_at = observation.captured_at + timedelta(minutes=1)
    first_path = store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=attempted_at,
    )
    initial_ledger = store.load_approval_ledger(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
    )

    retry_path = store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=attempted_at + timedelta(minutes=1),
    )
    assert retry_path == first_path
    assert (
        store.load_approval_ledger(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )
        == initial_ledger
    )

    conflicting = ShadowObservation.model_validate(
        {
            **observation.model_dump(mode="python"),
            "ticker": "BBRI",
        }
    )
    with pytest.raises(
        ShadowContractError,
        match="observation ID is already bound to different content",
    ):
        store.persist_observation(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
            observation=conflicting,
            attempted_at=attempted_at + timedelta(minutes=2),
        )

    assert (
        store.load_approval_ledger(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )
        == initial_ledger
    )


def test_observation_time_regression_is_rejected_before_any_artifact_write(
    tmp_path: Path,
) -> None:
    store, _, manifest, _, approval, _ = _approved_store(tmp_path)
    first = _observation(
        manifest,
        observation_id="OBS-RS-C1-TIME-ORDER-0001",
        signal_at=COLLECTION_START + timedelta(minutes=20),
    )
    first_attempted_at = first.captured_at + timedelta(minutes=1)
    store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
        observation=first,
        attempted_at=first_attempted_at,
    )
    trusted_ledger = store.load_approval_ledger(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
    )
    store_root = tmp_path / "governance"
    before = _artifact_snapshot(store_root)

    stale = _observation(
        manifest,
        observation_id="OBS-RS-C1-TIME-ORDER-0002",
        signal_at=COLLECTION_START + timedelta(minutes=5),
    )
    stale_attempted_at = COLLECTION_START + timedelta(minutes=10)
    assert stale.captured_at <= stale_attempted_at < first_attempted_at

    with pytest.raises(
        ShadowContractError,
        match="prospective approval-ledger event failed validation",
    ):
        store.persist_observation(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
            observation=stale,
            attempted_at=stale_attempted_at,
        )

    assert _artifact_snapshot(store_root) == before
    assert (
        store.load_approval_ledger(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )
        == trusted_ledger
    )


def test_non_session_signal_is_rejected_before_any_artifact_write(
    tmp_path: Path,
) -> None:
    store, calendar, manifest, _, approval, _ = _approved_store(tmp_path)
    weekend = date(2026, 7, 25)
    assert weekend.weekday() >= 5
    assert weekend not in calendar.sessions
    signal_at = datetime.combine(
        weekend,
        datetime.min.time(),
        tzinfo=IDX,
    ) + timedelta(hours=9)
    observation = _observation(
        manifest,
        observation_id="OBS-RS-C1-WEEKEND-0001",
        signal_at=signal_at,
    )
    store_root = tmp_path / "governance"
    before = _artifact_snapshot(store_root)

    with pytest.raises(
        ShadowContractError,
        match="signal date is not a frozen IDX session",
    ):
        store.persist_observation(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
            observation=observation,
            attempted_at=observation.captured_at + timedelta(minutes=1),
        )

    assert _artifact_snapshot(store_root) == before
    assert len(
        store.load_approval_ledger(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        ).events
    ) == 1


def test_missing_content_addressed_event_blob_is_rejected(
    tmp_path: Path,
) -> None:
    store, _, manifest, _, approval, _ = _approved_store(tmp_path)
    event_blob = next(
        (tmp_path / "governance").rglob("event_blobs/**/*.json")
    )
    event_blob.unlink()

    with pytest.raises(
        ShadowContractError,
        match="content-addressed approval-ledger event is unavailable",
    ):
        store.load_approval_ledger(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )


def test_event_reference_hash_mismatch_is_rejected(
    tmp_path: Path,
) -> None:
    store, _, manifest, _, approval, _ = _approved_store(tmp_path)
    reference_path = next(
        (tmp_path / "governance").rglob("events/0000000001.ref.json")
    )
    payload = json.loads(reference_path.read_text(encoding="utf-8"))
    payload["event_raw_file_sha256"] = HASH_C
    reference_path.write_text(
        json.dumps(payload, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(ShadowContractError):
        store.load_approval_ledger(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )


def test_losing_sequence_claim_cannot_corrupt_winning_event(
    tmp_path: Path,
) -> None:
    store, _, manifest, _, approval, _ = _approved_store(tmp_path)
    observation = _observation(manifest)
    attempted_at = observation.captured_at + timedelta(minutes=1)
    store.persist_observation(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
        observation=observation,
        attempted_at=attempted_at,
    )
    winner = store.load_approval_ledger(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
    )
    winning_event = winner.events[-1]
    loser = ApprovalLedgerEvent.model_validate(
        {
            **winning_event.model_dump(mode="python"),
            "event_id": "LOSER-SAME-SEQUENCE",
            "record_id": "OBS-LOSER",
            "record_canonical_sha256": HASH_C,
            "record_raw_file_sha256": HASH_C,
        }
    )

    with pytest.raises(
        ShadowContractError,
        match="immutable governance artifact collision",
    ):
        store._append_event(loser)

    assert (
        store.load_approval_ledger(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )
        == winner
    )


def test_closure_marker_makes_tail_event_deletion_fail_closed(
    tmp_path: Path,
) -> None:
    store, _, manifest, manifest_raw, approval, _ = _approved_store(tmp_path)
    closure = _closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=COLLECTION_START + timedelta(hours=1),
    )
    store.append_closure(_raw(closure))
    closure_reference = next(
        (tmp_path / "governance").rglob("closure_reference.json")
    )
    assert closure_reference.is_file()
    terminal_event_reference = next(
        (tmp_path / "governance").rglob("events/0000000002.ref.json")
    )
    terminal_event_reference.unlink()

    with pytest.raises(
        ShadowContractError,
        match="closure reference exists without terminal ledger event",
    ):
        store.load_authorization(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )


def test_pending_closure_marker_fails_closed_and_only_exact_retry_recovers(
    tmp_path: Path,
) -> None:
    store, _, manifest, manifest_raw, approval, _ = _approved_store(tmp_path)
    closure = _closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=COLLECTION_START + timedelta(hours=1),
        closure_id="CLOSE-RS-C1-PENDING-0001",
    )
    closure_raw = _raw(closure)

    # Simulate a crash after the manifest-level closure claim but before the
    # terminal ledger event and content-addressed ClosureRecord are persisted.
    marker_path = store._claim_closure_reference(closure, closure_raw)
    marker_bytes = marker_path.read_bytes()
    pending_ledger = store.load_approval_ledger(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
    )
    assert pending_ledger.closure_event is None

    with pytest.raises(
        ShadowContractError,
        match="closure reference exists without terminal ledger event",
    ):
        store.load_authorization(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )

    observation = _observation(
        manifest,
        observation_id="OBS-RS-C1-PENDING-CLOSURE",
    )
    before_collection_attempt = _artifact_snapshot(tmp_path / "governance")
    with pytest.raises(
        ShadowContractError,
        match="closure reference exists without terminal ledger event",
    ):
        store.persist_observation(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
            observation=observation,
            attempted_at=observation.captured_at + timedelta(minutes=1),
        )
    assert (
        _artifact_snapshot(tmp_path / "governance")
        == before_collection_attempt
    )

    competing = _closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=closure.effective_at + timedelta(minutes=1),
        closure_id="CLOSE-RS-C1-PENDING-COMPETITOR",
    )
    with pytest.raises(
        ShadowContractError,
        match="closure reference exists without terminal ledger event",
    ):
        store.append_closure(_raw(competing))
    assert marker_path.read_bytes() == marker_bytes
    assert (
        store.load_approval_ledger(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
        )
        == pending_ledger
    )

    recovered = store.append_closure(closure_raw)
    assert recovered.closure_event is not None
    assert recovered.closure_event.record_id == closure.closure_id
    authorization = store.load_authorization(
        protocol_id=manifest.protocol_id,
        manifest_canonical_sha256=_digest(manifest),
        ledger_id=approval.approval_ledger_id,
    )
    assert authorization.closure == closure


def test_fixed_terminal_closure_requires_exact_frozen_session_close(
    tmp_path: Path,
) -> None:
    store, _, manifest, manifest_raw, approval, _ = _approved_store(tmp_path)
    terminal_close = session_close_at(manifest.fixed_terminal_date)
    wrong = _closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=terminal_close - timedelta(minutes=1),
        reason_code="FIXED_TERMINAL_REACHED",
        closure_id="CLOSE-WRONG-TERMINAL",
    )

    with pytest.raises(
        ShadowContractError,
        match="fixed-terminal closure time differs from frozen calendar close",
    ):
        store.append_closure(_raw(wrong))

    correct = _closure(
        manifest,
        manifest_raw,
        approval,
        effective_at=terminal_close,
        reason_code="FIXED_TERMINAL_REACHED",
        closure_id="CLOSE-EXACT-TERMINAL",
    )
    ledger = store.append_closure(_raw(correct))
    assert ledger.closure_event is not None
    assert ledger.closure_event.record_id == correct.closure_id


def test_collection_requires_entry_validity_plus_primary_horizon_runway(
    tmp_path: Path,
) -> None:
    store, calendar, manifest, _, approval, _ = _approved_store(tmp_path)
    signal_date = calendar.sessions[-18]
    signal_at = datetime.combine(
        signal_date,
        datetime.min.time(),
        tzinfo=IDX,
    ) + timedelta(hours=9)
    observation = _observation(
        manifest,
        observation_id="OBS-INSUFFICIENT-RUNWAY",
        signal_at=signal_at,
    )

    with pytest.raises(
        ShadowContractError,
        match="entry-validity plus 15-session runway",
    ):
        store.persist_observation(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
            observation=observation,
            attempted_at=observation.captured_at + timedelta(minutes=1),
        )


@pytest.mark.parametrize("late_field", ("captured_at", "attempted_at"))
def test_collection_rejects_capture_or_attempt_after_terminal_close(
    tmp_path: Path,
    late_field: str,
) -> None:
    store, _, manifest, _, approval, _ = _approved_store(tmp_path)
    terminal_close = session_close_at(manifest.fixed_terminal_date)
    captured_at = COLLECTION_START + timedelta(minutes=6)
    attempted_at = captured_at + timedelta(minutes=1)
    if late_field == "captured_at":
        captured_at = terminal_close + timedelta(seconds=1)
        attempted_at = captured_at + timedelta(seconds=1)
    else:
        attempted_at = terminal_close + timedelta(seconds=1)
    observation = _observation(
        manifest,
        observation_id=f"OBS-LATE-{late_field.upper()}",
        captured_at=captured_at,
    )

    with pytest.raises(
        ShadowContractError,
        match="after fixed terminal date",
    ):
        store.persist_observation(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_digest(manifest),
            ledger_id=approval.approval_ledger_id,
            observation=observation,
            attempted_at=attempted_at,
        )


def test_independent_approval_sessions_must_match_trusted_calendar() -> None:
    calendar = _calendar()
    manifest = _manifest(
        calendar,
        governance_mode="INDEPENDENT_REVIEW",
        independent_reviewer="reviewer@example.test",
    )
    manifest_raw = _raw(manifest)
    approval = _approval(
        manifest,
        manifest_raw,
        calendar,
        completed_idx_trading_sessions=(),
    )
    approval_raw = _raw(approval)
    ledger = _approved_store_for_ledger(
        calendar,
        manifest,
        manifest_raw,
        approval,
        approval_raw,
    )

    with pytest.raises(
        ShadowContractError,
        match="sessions differ from the trusted calendar derivation",
    ):
        verify_approval_binding(
            manifest=manifest,
            manifest_raw_file_bytes=manifest_raw,
            methodology_document_bytes=METHODOLOGY,
            approval=approval,
            approval_raw_file_bytes=approval_raw,
            approval_ledger=ledger,
            trading_calendar=calendar,
        )


@pytest.mark.parametrize(
    ("payload", "error"),
    (
        (b"", "must not be empty"),
        (b" \r\n\t", "must not be empty"),
        (b"\xff\xfe", "must be valid UTF-8 text"),
    ),
)
def test_methodology_requires_nonblank_utf8_text(
    tmp_path: Path,
    payload: bytes,
    error: str,
) -> None:
    calendar = _calendar()
    manifest = _manifest(
        calendar,
        methodology_document_sha256=hashlib.sha256(payload).hexdigest(),
    )
    store = ProtocolGovernanceStore(tmp_path / "governance")
    store.persist_trading_calendar(calendar)

    with pytest.raises(ShadowContractError, match=error):
        store.persist_manifest(_raw(manifest), payload)


def test_self_adversarial_prompt_registry_is_immutable() -> None:
    original = SELF_ADVERSARIAL_PROMPTS["SKEPTICAL_SUMMARY"]
    with pytest.raises(TypeError):
        SELF_ADVERSARIAL_PROMPTS["SKEPTICAL_SUMMARY"] = (  # type: ignore[index]
            "mutated prompt"
        )
    assert SELF_ADVERSARIAL_PROMPTS["SKEPTICAL_SUMMARY"] == original
