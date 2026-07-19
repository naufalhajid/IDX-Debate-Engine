"""Evaluation-only daily marked-to-market NAV contracts for RS-P2-017.

The module deliberately keeps fixed-notional sleeve equity and policy
portfolio NAV as non-interchangeable artifact families.  It consumes the
immutable RS-P2-014/015/016 contracts and never reaches into live execution,
ranking, sizing, clocks, providers, or mutable "latest" state.

Daily NAV is a build-only capability in this version.  Local series snapshots
are append-only replay projections, not independently authenticated proofs of
chain completeness; that trust-root remains an explicit RS-P2-019 gap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from .calendar import IDX_TIMEZONE, TradingCalendar, session_close_at
from .contracts import (
    DecisionRole,
    ShadowContractError,
    ShadowProtocolManifest,
    SourceDefinition,
    canonical_sha256,
)
from .fixed_notional import (
    FIXED_NOTIONAL_IDR,
    FixedNotionalCashFlowRecord,
    FixedNotionalHoldingRecord,
    FixedNotionalLifecycle,
    FrozenFixedNotionalPolicy,
    PairedFixedNotionalRecord,
)
from .policy_portfolio import (
    FrozenPolicyPortfolioPolicy,
    PairedPolicyPortfolioSessionRecord,
    PolicyPortfolioGenesisRecord,
    PolicyPortfolioSessionState,
)
from .portfolio import (
    APPROVED_RATIO_QUANTIZATION_DECIMAL_PLACES,
    APPROVED_STARTING_CAPITAL_IDR,
    EstimableMoney,
    EstimableRatio,
    FrozenPortfolioPolicy,
    estimable_money,
    estimable_ratio,
    not_estimable_money,
    not_estimable_ratio,
    quantize_ratio,
)


DAILY_NAV_POLICY_VERSION = "shadow-daily-nav-policy-v1"
NAV_MARK_INPUT_VERSION = "shadow-nav-mark-input-v1"
DAILY_NAV_POINT_VERSION = "shadow-daily-nav-point-v1"
NAV_SERIES_EVENT_VERSION = "shadow-nav-series-event-v1"
NAV_SERIES_SNAPSHOT_VERSION = "shadow-nav-series-snapshot-v1"
DAILY_NAV_CONFIG_PATH = "config/daily-nav-policy-v1.json"
DAILY_NAV_CAPABILITY_STATUS = "RS_P2_017_IMPLEMENTED_NOT_A1_ELIGIBLE"
NAV_CHAIN_COMPLETENESS_STATUS = "UNANCHORED_NOT_CERTIFIED_COMPLETE"

NOT_ESTIMABLE_NO_PREDECESSOR = "NOT_ESTIMABLE_NO_PREDECESSOR"
NOT_ESTIMABLE_MISSING_OFFICIAL_MARK = "NOT_ESTIMABLE_MISSING_OFFICIAL_MARK"
NOT_ESTIMABLE_SUSPENDED_NO_OFFICIAL_MARK = (
    "NOT_ESTIMABLE_SUSPENDED_NO_OFFICIAL_MARK"
)
NOT_ESTIMABLE_MISSING_POLICY_SESSION = (
    "NOT_ESTIMABLE_MISSING_POLICY_SESSION_RECORD"
)
NOT_ESTIMABLE_PREDECESSOR_GAP = "NOT_ESTIMABLE_PREDECESSOR_GAP"
NOT_ESTIMABLE_TERMINAL_UNRESOLVED = "NOT_ESTIMABLE_TERMINAL_UNRESOLVED"
INSOLVENT_TERMINAL = "INSOLVENT_TERMINAL"

NavSeriesKind: TypeAlias = Literal[
    "FIXED_NOTIONAL_SLEEVE_EQUITY",
    "POLICY_PORTFOLIO_NAV",
]
NavMarkStatus: TypeAlias = Literal[
    "OFFICIAL_CURRENT_SESSION",
    "MISSING_OFFICIAL_RECORD",
    "SUSPENDED_NO_OFFICIAL_RECORD",
]
NavPointStatus: TypeAlias = Literal[
    "ACTIVE",
    "GENESIS_ANCHOR",
    "INSOLVENT",
    "NO_ACTION_FLAT",
    "NOT_ESTIMABLE",
    "SETTLEMENT_ONLY",
]
NavEventType: TypeAlias = Literal[
    "CORRECTION_APPENDED",
    "PRIMARY_POINT_APPENDED",
]

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
Sha256 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[0-9a-f]{64}$"),
]
CanonicalTicker = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[A-Z0-9][A-Z0-9.-]{0,15}$",
    ),
]
StrictNonNegativeInt = Annotated[StrictInt, Field(ge=0)]
StrictPositiveInt = Annotated[StrictInt, Field(gt=0)]


class _DailyNavModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class _EvaluationOnlyDailyNavArtifact(_DailyNavModel):
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False


class FrozenDailyNavPolicy(_EvaluationOnlyDailyNavArtifact):
    """Schema-bound NV1-NV8 rules without a manifest-hash cycle."""

    contract_version: Literal["shadow-daily-nav-policy-v1"] = (
        DAILY_NAV_POLICY_VERSION
    )
    policy_id: NonEmptyString
    phase2_capability_status: Literal[
        "RS_P2_017_IMPLEMENTED_NOT_A1_ELIGIBLE"
    ] = DAILY_NAV_CAPABILITY_STATUS

    portfolio_policy_id: NonEmptyString
    portfolio_policy_sha256: Sha256
    fixed_notional_policy_id: NonEmptyString
    fixed_notional_policy_sha256: Sha256
    policy_portfolio_policy_id: NonEmptyString
    policy_portfolio_policy_sha256: Sha256
    mark_source_id: NonEmptyString
    mark_source_contract_version: NonEmptyString
    mark_source_definition_sha256: Sha256
    label_definition_sha256: Sha256
    cost_assumptions_sha256: Sha256
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    methodology_document_sha256: Sha256

    currency: Literal["IDR"] = "IDR"
    money_state_rule: Literal["STRICT_INTEGER_IDR"] = "STRICT_INTEGER_IDR"
    mark_price_basis: Literal[
        "OFFICIAL_CURRENT_SESSION_RAW_AS_TRADED_UNADJUSTED_CLOSE"
    ] = "OFFICIAL_CURRENT_SESSION_RAW_AS_TRADED_UNADJUSTED_CLOSE"
    mark_carry_tolerance_sessions: Literal[0] = 0
    mark_fallback_rule: Literal[
        "NO_PRIOR_CLOSE_ENTRY_PRICE_PLANNED_PRICE_OR_VENDOR_FALLBACK"
    ] = "NO_PRIOR_CLOSE_ENTRY_PRICE_PLANNED_PRICE_OR_VENDOR_FALLBACK"
    corporate_action_rule: Literal[
        "CONSUME_FROZEN_PREDECESSOR_EVENTS_NO_NAV_SIDE_REINTERPRETATION"
    ] = "CONSUME_FROZEN_PREDECESSOR_EVENTS_NO_NAV_SIDE_REINTERPRETATION"

    fixed_series_rule: Literal[
        "ONE_OPPORTUNITY_ONE_SIDE_ATTRIBUTION_ONLY"
    ] = "ONE_OPPORTUNITY_ONE_SIDE_ATTRIBUTION_ONLY"
    policy_series_rule: Literal[
        "ONE_INDEPENDENT_POLICY_PATH_PER_SIDE"
    ] = "ONE_INDEPENDENT_POLICY_PATH_PER_SIDE"
    portfolio_metric_authority: Literal[
        "POLICY_PORTFOLIO_NAV_ONLY"
    ] = "POLICY_PORTFOLIO_NAV_ONLY"
    cross_opportunity_aggregation_rule: Literal[
        "FORBIDDEN_NO_IMPLICIT_HELPER"
    ] = "FORBIDDEN_NO_IMPLICIT_HELPER"

    fixed_sleeve_anchor_idr: Literal[13_000_000] = FIXED_NOTIONAL_IDR
    unfunded_cost_liability_rule: Literal[
        "EXPLICIT_ENTRY_COST_EXPLAINED_NO_INTEREST_NO_LEVERAGE"
    ] = "EXPLICIT_ENTRY_COST_EXPLAINED_NO_INTEREST_NO_LEVERAGE"
    fixed_exit_proceeds_rule: Literal[
        "REPAY_UNFUNDED_COST_LIABILITY_BEFORE_IDLE_CASH"
    ] = "REPAY_UNFUNDED_COST_LIABILITY_BEFORE_IDLE_CASH"
    settlement_lag_sessions: Literal[2] = 2
    paired_window_rule: Literal[
        "UNION_OF_REAL_ECONOMIC_AND_SETTLEMENT_WINDOWS"
    ] = "UNION_OF_REAL_ECONOMIC_AND_SETTLEMENT_WINDOWS"
    no_action_rule: Literal[
        "FLAT_ONLY_OVER_REAL_PAIRED_UNION_NO_SYNTHETIC_15D_PATH"
    ] = "FLAT_ONLY_OVER_REAL_PAIRED_UNION_NO_SYNTHETIC_15D_PATH"

    missingness_rule: Literal[
        "PERMANENT_NULL_NO_FORWARD_FILL_BRIDGING_OR_SYNTHETIC_EXIT"
    ] = "PERMANENT_NULL_NO_FORWARD_FILL_BRIDGING_OR_SYNTHETIC_EXIT"
    unresolved_terminal_rule: Literal[
        "CANONICAL_NULL_NUMERIC_EQUITY_DIAGNOSTIC_ONLY"
    ] = "CANONICAL_NULL_NUMERIC_EQUITY_DIAGNOSTIC_ONLY"
    daily_return_rule: Literal[
        "SIMPLE_CLOSE_TO_CLOSE_EXACT_INTEGER_INPUTS"
    ] = "SIMPLE_CLOSE_TO_CLOSE_EXACT_INTEGER_INPUTS"
    ratio_quantization_decimal_places: Literal[12] = (
        APPROVED_RATIO_QUANTIZATION_DECIMAL_PLACES
    )
    ratio_quantization_rounding_mode: Literal["ROUND_HALF_EVEN"] = (
        "ROUND_HALF_EVEN"
    )
    genesis_return_rule: Literal[
        "NOT_ESTIMABLE_NO_PREDECESSOR"
    ] = "NOT_ESTIMABLE_NO_PREDECESSOR"
    insolvency_rule: Literal[
        "NAV_LE_ZERO_TERMINAL_NO_REBASE_OR_CAPITAL_INJECTION"
    ] = "NAV_LE_ZERO_TERMINAL_NO_REBASE_OR_CAPITAL_INJECTION"
    external_flow_rule: Literal[
        "FORBIDDEN_REQUIRES_NEW_PROTOCOL"
    ] = "FORBIDDEN_REQUIRES_NEW_PROTOCOL"
    chain_completeness_rule: Literal[
        "LOCAL_APPEND_ONLY_UNANCHORED_PENDING_RS_P2_019"
    ] = "LOCAL_APPEND_ONLY_UNANCHORED_PENDING_RS_P2_019"


class DailyNavNamedPredecessor(_DailyNavModel):
    """One deterministic name-addressed edge in a NAV lineage graph."""

    name: NonEmptyString
    sha256: Sha256

    @model_validator(mode="after")
    def verify_name(self) -> DailyNavNamedPredecessor:
        if not re.fullmatch(r"[a-z][a-z0-9_.\[\]-]{0,127}", self.name):
            raise ValueError("daily-NAV predecessor name is not canonical")
        return self


class NavMarkInput(_EvaluationOnlyDailyNavArtifact):
    """One point-in-time official close record or explicit source absence."""

    contract_version: Literal["shadow-nav-mark-input-v1"] = (
        NAV_MARK_INPUT_VERSION
    )
    mark_input_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: NonEmptyString
    manifest_sha256: Sha256
    daily_nav_policy_sha256: Sha256
    series_kind: NavSeriesKind
    series_id: NonEmptyString
    decision_role: DecisionRole
    path_id: NonEmptyString
    session: date
    ticker: CanonicalTicker

    mark_status: NavMarkStatus
    close_price_idr: StrictPositiveInt | None = None
    volume_shares: StrictNonNegativeInt | None = None
    price_basis: Literal[
        "OFFICIAL_CURRENT_SESSION_RAW_AS_TRADED_UNADJUSTED_CLOSE"
    ] = "OFFICIAL_CURRENT_SESSION_RAW_AS_TRADED_UNADJUSTED_CLOSE"
    carry_sessions: Literal[0] = 0
    fallback_used: Literal[False] = False

    source_id: NonEmptyString
    source_contract_version: NonEmptyString
    source_definition_sha256: Sha256
    source_record_canonical_sha256: Sha256
    source_record_raw_file_sha256: Sha256
    source_record_raw_byte_length: StrictPositiveInt
    source_as_of: datetime
    available_at: datetime
    captured_at: datetime
    source_revision: StrictNonNegativeInt = 0
    previous_source_record_sha256: Sha256 | None = None
    supersedes_mark_input_id: NonEmptyString | None = None
    supersedes_mark_input_sha256: Sha256 | None = None
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    reason_codes: tuple[NonEmptyString, ...] = ()

    @field_validator("source_as_of", "available_at", "captured_at")
    @classmethod
    def require_aware_times(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("NAV mark timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_mark(self) -> NavMarkInput:
        if self.source_as_of.astimezone(IDX_TIMEZONE).date() != self.session:
            raise ValueError(
                "NAV mark source vintage is not the frozen current session"
            )
        if self.available_at < session_close_at(self.session):
            raise ValueError("official NAV mark cannot be available before close")
        if self.source_as_of > self.available_at:
            raise ValueError("NAV mark source vintage follows availability")
        if self.captured_at < self.available_at:
            raise ValueError("NAV mark capture precedes source availability")
        if self.reason_codes != tuple(dict.fromkeys(self.reason_codes)):
            raise ValueError("NAV mark reason codes must be unique")
        if self.mark_status == "OFFICIAL_CURRENT_SESSION":
            if self.close_price_idr is None or self.volume_shares is None:
                raise ValueError("official NAV mark requires close and volume")
            if self.reason_codes:
                raise ValueError("official NAV mark cannot carry failure reasons")
        else:
            if self.close_price_idr is not None or self.volume_shares is not None:
                raise ValueError("missing NAV mark cannot publish price or volume")
            if not self.reason_codes:
                raise ValueError("missing NAV mark requires an explicit reason")
            required_reason = {
                "MISSING_OFFICIAL_RECORD": NOT_ESTIMABLE_MISSING_OFFICIAL_MARK,
                "SUSPENDED_NO_OFFICIAL_RECORD": (
                    NOT_ESTIMABLE_SUSPENDED_NO_OFFICIAL_MARK
                ),
            }[self.mark_status]
            if required_reason not in self.reason_codes:
                raise ValueError("NAV mark status/reason classification mismatch")
        supersedes = (
            self.supersedes_mark_input_id,
            self.supersedes_mark_input_sha256,
        )
        if self.source_revision == 0:
            if any(value is not None for value in supersedes):
                raise ValueError("first-seen NAV mark cannot supersede another mark")
        elif any(value is None for value in supersedes):
            raise ValueError("NAV mark correction requires predecessor identity")
        expected = canonical_daily_nav_artifact_id(
            "NMARK",
            _identity_payload(self, "mark_input_id"),
        )
        if self.mark_input_id != expected:
            raise ValueError("NAV mark-input ID mismatch")
        return self


class DailyNavPoint(_EvaluationOnlyDailyNavArtifact):
    """One exact or explicitly censored EOD NAV observation."""

    contract_version: Literal["shadow-daily-nav-point-v1"] = (
        DAILY_NAV_POINT_VERSION
    )
    point_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: NonEmptyString
    manifest_sha256: Sha256
    daily_nav_policy_sha256: Sha256
    trading_calendar_sha256: Sha256
    series_kind: NavSeriesKind
    series_id: NonEmptyString
    decision_role: DecisionRole
    path_id: NonEmptyString
    session: date
    as_of: datetime
    frozen_at: datetime
    point_sequence: StrictNonNegativeInt
    revision: StrictNonNegativeInt = 0
    evidence_type: Literal[
        "OFFICIAL_CORRECTION",
        "PRIMARY_FIRST_SEEN",
    ] = "PRIMARY_FIRST_SEEN"
    point_status: NavPointStatus

    settled_cash_idr: StrictNonNegativeInt | None
    sale_receivable_idr: StrictNonNegativeInt | None
    purchase_payable_idr: StrictNonNegativeInt | None
    marked_holdings_value_idr: StrictNonNegativeInt | None
    unfunded_cost_liability_idr: StrictNonNegativeInt = 0
    unfunded_cost_origin_sha256: Sha256 | None = None
    nav: EstimableMoney
    diagnostic_equity_idr: StrictInt | None = None
    daily_return: EstimableRatio

    previous_point_id: NonEmptyString | None = None
    previous_point_sha256: Sha256 | None = None
    previous_nav: EstimableMoney | None = None
    supersedes_point_id: NonEmptyString | None = None
    supersedes_point_sha256: Sha256 | None = None
    mark_input_ids: tuple[NonEmptyString, ...] = ()
    mark_input_sha256s: tuple[Sha256, ...] = ()
    predecessors: tuple[DailyNavNamedPredecessor, ...]
    lifecycle_tags: tuple[NonEmptyString, ...] = ()
    censored_tickers: tuple[CanonicalTicker, ...] = ()
    censor_duration_sessions: StrictNonNegativeInt = 0
    poisoned_from_session: date | None = None
    reason_codes: tuple[NonEmptyString, ...] = ()

    @field_validator("as_of", "frozen_at")
    @classmethod
    def require_aware_point_times(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("NAV point timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_point(self) -> DailyNavPoint:
        if self.as_of != session_close_at(self.session):
            raise ValueError("NAV point as_of must equal frozen IDX close")
        if self.frozen_at < self.as_of:
            raise ValueError("NAV point froze before its EOD anchor")
        if self.reason_codes != tuple(dict.fromkeys(self.reason_codes)):
            raise ValueError("NAV point reason codes must be unique")
        if self.lifecycle_tags != tuple(dict.fromkeys(self.lifecycle_tags)):
            raise ValueError("NAV lifecycle tags must be unique")
        if (
            self.censored_tickers != tuple(sorted(self.censored_tickers))
            or len(set(self.censored_tickers)) != len(self.censored_tickers)
        ):
            raise ValueError("censored tickers must be unique and ordered")
        if bool(self.censored_tickers) is not bool(
            self.censor_duration_sessions
        ):
            raise ValueError("censor tickers and duration must appear together")
        predecessor_names = tuple(item.name for item in self.predecessors)
        if (
            predecessor_names != tuple(sorted(predecessor_names))
            or len(set(predecessor_names)) != len(predecessor_names)
        ):
            raise ValueError("NAV predecessor names must be unique and ordered")
        if len(self.mark_input_ids) != len(self.mark_input_sha256s):
            raise ValueError("NAV mark-input ID/hash tuples differ in length")
        if self.mark_input_ids != tuple(sorted(self.mark_input_ids)):
            raise ValueError("NAV mark-input IDs must be ordered")
        if self.series_kind == "POLICY_PORTFOLIO_NAV" and (
            self.unfunded_cost_liability_idr != 0
            or self.unfunded_cost_origin_sha256 is not None
        ):
            raise ValueError("policy NAV cannot carry fixed-sleeve liability")
        if self.unfunded_cost_liability_idr > 0:
            if self.unfunded_cost_origin_sha256 is None:
                raise ValueError("positive unfunded cost lacks entry-cost lineage")
            entry_cost_edges = tuple(
                item
                for item in self.predecessors
                if item.name == "entry_cost"
            )
            if len(entry_cost_edges) != 1 or (
                entry_cost_edges[0].sha256
                != self.unfunded_cost_origin_sha256
            ):
                raise ValueError(
                    "positive unfunded cost is not explained by exact entry cost"
                )
        elif self.unfunded_cost_origin_sha256 is not None:
            raise ValueError("zero unfunded cost cannot carry origin lineage")

        components = (
            self.settled_cash_idr,
            self.sale_receivable_idr,
            self.purchase_payable_idr,
            self.marked_holdings_value_idr,
        )
        computed = None
        if all(value is not None for value in components):
            settled, receivable, payable, marked = components
            assert settled is not None
            assert receivable is not None
            assert payable is not None
            assert marked is not None
            computed = (
                settled
                + receivable
                - payable
                + marked
                - self.unfunded_cost_liability_idr
            )
        if self.nav.status == "ESTIMABLE":
            if computed is None or self.nav.value_idr != computed:
                raise ValueError("daily NAV arithmetic mismatch")
            if self.diagnostic_equity_idr != computed:
                raise ValueError("estimable NAV diagnostic must equal exact NAV")
            if computed <= 0 and self.point_status != "INSOLVENT":
                raise ValueError("non-positive NAV must be terminal INSOLVENT")
            if computed > 0 and self.point_status in (
                "INSOLVENT",
                "NOT_ESTIMABLE",
            ):
                raise ValueError("positive NAV has an invalid point status")
            if self.reason_codes and self.point_status != "INSOLVENT":
                raise ValueError("estimable NAV cannot carry failure reasons")
        else:
            if self.point_status != "NOT_ESTIMABLE":
                raise ValueError("missing NAV must use NOT_ESTIMABLE point status")
            if not self.reason_codes:
                raise ValueError("missing NAV requires point reason codes")
            if (
                self.diagnostic_equity_idr is not None
                and computed is not None
                and self.diagnostic_equity_idr != computed
            ):
                raise ValueError("diagnostic equity arithmetic mismatch")

        predecessor_tuple = (
            self.previous_point_id,
            self.previous_point_sha256,
            self.previous_nav,
        )
        if self.point_sequence == 0:
            if any(value is not None for value in predecessor_tuple):
                raise ValueError("genesis NAV point cannot have a predecessor")
            if self.daily_return.status != "NOT_ESTIMABLE" or (
                NOT_ESTIMABLE_NO_PREDECESSOR
                not in self.daily_return.reason_codes
            ):
                raise ValueError("genesis NAV return must be NOT_ESTIMABLE")
        else:
            if any(value is None for value in predecessor_tuple):
                raise ValueError("successor NAV point requires predecessor state")
            assert self.previous_nav is not None
            if (
                self.nav.status == "ESTIMABLE"
                and self.previous_nav.status == "ESTIMABLE"
                and self.previous_nav.value_idr is not None
                and self.previous_nav.value_idr != 0
            ):
                assert self.nav.value_idr is not None
                expected_return = quantize_ratio(
                    self.nav.value_idr - self.previous_nav.value_idr,
                    self.previous_nav.value_idr,
                )
                if (
                    self.daily_return.status != "ESTIMABLE"
                    or self.daily_return.value != expected_return
                ):
                    raise ValueError("daily close-to-close return mismatch")
            elif self.daily_return.status != "NOT_ESTIMABLE":
                raise ValueError("NAV gap/zero predecessor cannot publish return")

        supersedes = (
            self.supersedes_point_id,
            self.supersedes_point_sha256,
        )
        if self.revision == 0:
            if self.evidence_type != "PRIMARY_FIRST_SEEN" or any(
                value is not None for value in supersedes
            ):
                raise ValueError("primary NAV point revision shape is invalid")
        elif self.evidence_type != "OFFICIAL_CORRECTION" or any(
            value is None for value in supersedes
        ):
            raise ValueError("NAV correction requires superseded point identity")
        expected = canonical_daily_nav_artifact_id(
            "NPOINT",
            _identity_payload(self, "point_id"),
        )
        if self.point_id != expected:
            raise ValueError("daily-NAV point ID mismatch")
        return self


class NavSeriesEvent(_EvaluationOnlyDailyNavArtifact):
    """One append-only event in a local NAV-series chain."""

    contract_version: Literal["shadow-nav-series-event-v1"] = (
        NAV_SERIES_EVENT_VERSION
    )
    event_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: NonEmptyString
    manifest_sha256: Sha256
    daily_nav_policy_sha256: Sha256
    series_kind: NavSeriesKind
    series_id: NonEmptyString
    decision_role: DecisionRole
    path_id: NonEmptyString
    event_sequence: StrictNonNegativeInt
    event_type: NavEventType
    event_at: datetime
    point_id: NonEmptyString
    point_sha256: Sha256
    point_session: date
    point_revision: StrictNonNegativeInt
    mark_input_sha256s: tuple[Sha256, ...] = ()
    previous_event_id: NonEmptyString | None = None
    previous_event_sha256: Sha256 | None = None

    @field_validator("event_at")
    @classmethod
    def require_aware_event_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("NAV series event time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_event(self) -> NavSeriesEvent:
        if self.event_sequence == 0:
            if (
                self.previous_event_id is not None
                or self.previous_event_sha256 is not None
            ):
                raise ValueError("first NAV event cannot have predecessor")
        elif (
            self.previous_event_id is None
            or self.previous_event_sha256 is None
        ):
            raise ValueError("successor NAV event requires predecessor")
        expected_type = (
            "PRIMARY_POINT_APPENDED"
            if self.point_revision == 0
            else "CORRECTION_APPENDED"
        )
        if self.event_type != expected_type:
            raise ValueError("NAV event type differs from point revision")
        expected = canonical_daily_nav_artifact_id(
            "NEVENT",
            _identity_payload(self, "event_id"),
        )
        if self.event_id != expected:
            raise ValueError("NAV series-event ID mismatch")
        return self


class NavSeriesSnapshot(_EvaluationOnlyDailyNavArtifact):
    """Immutable local projection of one NAV event chain.

    It is intentionally labelled unanchored.  Deleting the local tail and a
    local snapshot together cannot be detected until RS-P2-019 supplies an
    independently authenticated run commitment.
    """

    contract_version: Literal["shadow-nav-series-snapshot-v1"] = (
        NAV_SERIES_SNAPSHOT_VERSION
    )
    snapshot_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: NonEmptyString
    manifest_sha256: Sha256
    daily_nav_policy_sha256: Sha256
    trading_calendar_sha256: Sha256
    series_kind: NavSeriesKind
    series_id: NonEmptyString
    decision_role: DecisionRole
    path_id: NonEmptyString
    snapshot_at: datetime
    first_session: date
    last_session: date

    mark_inputs: tuple[NavMarkInput, ...]
    mark_input_sha256s: tuple[Sha256, ...]
    points: tuple[DailyNavPoint, ...] = Field(min_length=1)
    point_sha256s: tuple[Sha256, ...] = Field(min_length=1)
    events: tuple[NavSeriesEvent, ...] = Field(min_length=1)
    event_sha256s: tuple[Sha256, ...] = Field(min_length=1)
    event_count: StrictPositiveInt
    primary_point_count: StrictPositiveInt
    correction_count: StrictNonNegativeInt
    head_event_id: NonEmptyString
    head_event_sha256: Sha256
    tail_event_id: NonEmptyString
    tail_event_sha256: Sha256
    prior_snapshot_id: NonEmptyString | None = None
    prior_snapshot_sha256: Sha256 | None = None
    chain_completeness_status: Literal[
        "UNANCHORED_NOT_CERTIFIED_COMPLETE"
    ] = NAV_CHAIN_COMPLETENESS_STATUS
    poisoned_from_session: date | None = None
    poison_reason_codes: tuple[NonEmptyString, ...] = ()

    @field_validator("snapshot_at")
    @classmethod
    def require_aware_snapshot_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("NAV snapshot time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_snapshot(self) -> NavSeriesSnapshot:
        if self.last_session < self.first_session:
            raise ValueError("NAV snapshot session range is inverted")
        common = (
            self.protocol_id,
            self.component_id,
            self.manifest_sha256,
            self.daily_nav_policy_sha256,
            self.series_kind,
            self.series_id,
            self.decision_role,
            self.path_id,
        )
        for item in (*self.mark_inputs, *self.points, *self.events):
            if (
                item.protocol_id,
                item.component_id,
                item.manifest_sha256,
                item.daily_nav_policy_sha256,
                item.series_kind,
                item.series_id,
                item.decision_role,
                item.path_id,
            ) != common:
                raise ValueError("NAV snapshot contains cross-series artifact")
        if self.mark_inputs != tuple(
            sorted(
                self.mark_inputs,
                key=lambda item: (
                    item.session,
                    item.ticker,
                    item.source_revision,
                    item.mark_input_id,
                ),
            )
        ):
            raise ValueError("NAV mark inputs are not canonically ordered")
        if self.mark_input_sha256s != tuple(
            _required_hash(item) for item in self.mark_inputs
        ):
            raise ValueError("NAV mark-input hash sequence mismatch")
        if self.point_sha256s != tuple(
            _required_hash(item) for item in self.points
        ):
            raise ValueError("NAV point hash sequence mismatch")
        if self.event_sha256s != tuple(
            _required_hash(item) for item in self.events
        ):
            raise ValueError("NAV event hash sequence mismatch")
        if self.event_count != len(self.events):
            raise ValueError("NAV event count mismatch")
        if tuple(item.event_sequence for item in self.events) != tuple(
            range(len(self.events))
        ):
            raise ValueError("NAV event sequence is not contiguous")
        for index, event in enumerate(self.events):
            if index == 0:
                continue
            previous = self.events[index - 1]
            if (
                event.previous_event_id,
                event.previous_event_sha256,
            ) != (
                previous.event_id,
                _required_hash(previous),
            ):
                raise ValueError("NAV event predecessor chain mismatch")
        point_by_id = {item.point_id: item for item in self.points}
        if len(point_by_id) != len(self.points):
            raise ValueError("NAV point IDs must be unique")
        for event in self.events:
            point = point_by_id.get(event.point_id)
            if point is None or (
                event.point_sha256,
                event.point_session,
                event.point_revision,
                event.mark_input_sha256s,
            ) != (
                _required_hash(point),
                point.session,
                point.revision,
                point.mark_input_sha256s,
            ):
                raise ValueError("NAV event does not bind its exact point")
        primary = tuple(item for item in self.points if item.revision == 0)
        if self.primary_point_count != len(primary):
            raise ValueError("NAV primary-point count mismatch")
        if self.correction_count != len(self.points) - len(primary):
            raise ValueError("NAV correction count mismatch")
        if tuple(item.point_sequence for item in primary) != tuple(
            range(len(primary))
        ):
            raise ValueError("NAV primary point sequence is not contiguous")
        primary_dates = tuple(item.session for item in primary)
        if (
            primary_dates != tuple(sorted(primary_dates))
            or len(set(primary_dates)) != len(primary_dates)
        ):
            raise ValueError("NAV primary sessions must be ordered and unique")
        if (primary[0].session, primary[-1].session) != (
            self.first_session,
            self.last_session,
        ):
            raise ValueError("NAV snapshot boundary sessions mismatch")
        poisoned = False
        insolvent = False
        first_poison = None
        poison_reasons: tuple[str, ...] = ()
        for point in primary:
            if poisoned and point.nav.status != "NOT_ESTIMABLE":
                raise ValueError("NAV series resurrected after permanent censoring")
            if insolvent and point.nav.status != "NOT_ESTIMABLE":
                raise ValueError("NAV series continued after terminal insolvency")
            if point.nav.status == "NOT_ESTIMABLE" and not poisoned:
                poisoned = True
                first_poison = point.session
                poison_reasons = point.reason_codes
            if point.point_status == "INSOLVENT":
                insolvent = True
        if (
            self.poisoned_from_session,
            self.poison_reason_codes,
        ) != (
            first_poison,
            poison_reasons,
        ):
            raise ValueError("NAV snapshot poison summary mismatch")
        if (
            self.head_event_id,
            self.head_event_sha256,
            self.tail_event_id,
            self.tail_event_sha256,
        ) != (
            self.events[0].event_id,
            _required_hash(self.events[0]),
            self.events[-1].event_id,
            _required_hash(self.events[-1]),
        ):
            raise ValueError("NAV snapshot head/tail mismatch")
        if self.snapshot_at < max(
            item.frozen_at for item in self.points
        ) or self.snapshot_at < max(item.event_at for item in self.events):
            raise ValueError("NAV snapshot predates embedded evidence")
        if (self.prior_snapshot_id is None) is not (
            self.prior_snapshot_sha256 is None
        ):
            raise ValueError("prior NAV snapshot identity must be complete")
        expected = canonical_daily_nav_artifact_id(
            "NSNAP",
            _identity_payload(self, "snapshot_id"),
        )
        if self.snapshot_id != expected:
            raise ValueError("NAV series-snapshot ID mismatch")
        return self


class PairedDailyNavSeries(_DailyNavModel):
    """In-memory paired result; not an additional persisted artifact family."""

    series_kind: NavSeriesKind
    control: NavSeriesSnapshot
    challenger: NavSeriesSnapshot
    shared_session_union: tuple[date, ...] = Field(min_length=1)
    paired_input_parity: Literal[True] = True

    @model_validator(mode="after")
    def verify_pair(self) -> PairedDailyNavSeries:
        if (
            self.control.series_kind,
            self.control.decision_role,
            self.challenger.series_kind,
            self.challenger.decision_role,
        ) != (
            self.series_kind,
            "CONTROL",
            self.series_kind,
            "CHALLENGER",
        ):
            raise ValueError("paired NAV family/role mismatch")
        control_dates = tuple(
            item.session for item in self.control.points if item.revision == 0
        )
        challenger_dates = tuple(
            item.session
            for item in self.challenger.points
            if item.revision == 0
        )
        if control_dates != self.shared_session_union or (
            challenger_dates != self.shared_session_union
        ):
            raise ValueError("paired NAV paths do not share the frozen union")
        return self


def build_daily_nav_policy(
    *,
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    fixed_notional_policy: FrozenFixedNotionalPolicy,
    policy_portfolio_policy: FrozenPolicyPortfolioPolicy,
    mark_source: SourceDefinition,
    policy_id: str,
) -> FrozenDailyNavPolicy:
    """Freeze NV1-NV8 rules before adding the policy hash to manifest v2."""

    source_matches = tuple(
        item for item in manifest.sources if item.source_id == mark_source.source_id
    )
    if len(source_matches) != 1 or source_matches[0] != mark_source:
        raise ShadowContractError(
            "daily-NAV mark source is not exactly one manifest source"
        )
    return FrozenDailyNavPolicy(
        policy_id=policy_id,
        portfolio_policy_id=portfolio_policy.policy_id,
        portfolio_policy_sha256=_required_hash(portfolio_policy),
        fixed_notional_policy_id=fixed_notional_policy.policy_id,
        fixed_notional_policy_sha256=_required_hash(fixed_notional_policy),
        policy_portfolio_policy_id=policy_portfolio_policy.policy_id,
        policy_portfolio_policy_sha256=_required_hash(policy_portfolio_policy),
        mark_source_id=mark_source.source_id,
        mark_source_contract_version=mark_source.contract_version,
        mark_source_definition_sha256=_required_hash(mark_source),
        label_definition_sha256=_required_hash(manifest.labels),
        cost_assumptions_sha256=_required_hash(manifest.costs),
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=(
            manifest.corporate_action_policy_sha256
        ),
        methodology_document_sha256=manifest.methodology_document_sha256,
    )


def verify_daily_nav_policy_binding(
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    fixed_notional_policy: FrozenFixedNotionalPolicy,
    policy_portfolio_policy: FrozenPolicyPortfolioPolicy,
    policy: FrozenDailyNavPolicy,
) -> None:
    """Fail closed unless manifest v2 binds one exact daily-NAV policy."""

    expected = (
        portfolio_policy.policy_id,
        _required_hash(portfolio_policy),
        fixed_notional_policy.policy_id,
        _required_hash(fixed_notional_policy),
        policy_portfolio_policy.policy_id,
        _required_hash(policy_portfolio_policy),
        _required_hash(manifest.labels),
        _required_hash(manifest.costs),
        manifest.trading_calendar_sha256,
        manifest.corporate_action_policy_sha256,
        manifest.methodology_document_sha256,
    )
    actual = (
        policy.portfolio_policy_id,
        policy.portfolio_policy_sha256,
        policy.fixed_notional_policy_id,
        policy.fixed_notional_policy_sha256,
        policy.policy_portfolio_policy_id,
        policy.policy_portfolio_policy_sha256,
        policy.label_definition_sha256,
        policy.cost_assumptions_sha256,
        policy.trading_calendar_sha256,
        policy.corporate_action_policy_sha256,
        policy.methodology_document_sha256,
    )
    if actual != expected:
        raise ShadowContractError("daily-NAV predecessor-policy binding mismatch")
    source_matches = tuple(
        item for item in manifest.sources if item.source_id == policy.mark_source_id
    )
    if len(source_matches) != 1:
        raise ShadowContractError("daily-NAV mark source identity is ambiguous")
    mark_source = source_matches[0]
    if (
        mark_source.contract_version,
        _required_hash(mark_source),
    ) != (
        policy.mark_source_contract_version,
        policy.mark_source_definition_sha256,
    ):
        raise ShadowContractError("daily-NAV mark source definition mismatch")
    policy_hash = _required_hash(policy)
    for label, content_hashes in (
        ("control", manifest.control_content_hashes),
        ("challenger", manifest.challenger_content_hashes),
    ):
        matches = tuple(
            item
            for item in content_hashes
            if item.path == DAILY_NAV_CONFIG_PATH
        )
        if len(matches) != 1 or (
            matches[0].role,
            matches[0].sha256,
        ) != ("CONFIG", policy_hash):
            raise ShadowContractError(
                f"{label} manifest does not bind exact daily-NAV CONFIG"
            )


def canonical_fixed_sleeve_series_id(
    paired_record: PairedFixedNotionalRecord,
    decision_role: DecisionRole,
) -> str:
    lifecycle = _fixed_lifecycle(paired_record, decision_role)
    return canonical_daily_nav_artifact_id(
        "FNSERIES",
        {
            "protocol_id": paired_record.protocol_id,
            "manifest_sha256": paired_record.manifest_sha256,
            "pair_input_sha256": paired_record.pair_input_sha256,
            "paired_record_sha256": _required_hash(paired_record),
            "lifecycle_sha256": _required_hash(lifecycle),
            "decision_role": decision_role,
        },
    )


def canonical_policy_nav_series_id(
    genesis: PolicyPortfolioGenesisRecord,
    genesis_state: PolicyPortfolioSessionState,
) -> str:
    return canonical_daily_nav_artifact_id(
        "PPSERIES",
        {
            "protocol_id": genesis.protocol_id,
            "manifest_sha256": genesis.manifest_sha256,
            "genesis_sha256": _required_hash(genesis),
            "genesis_state_sha256": _required_hash(genesis_state),
            "decision_role": genesis_state.decision_role,
            "path_id": genesis_state.path_id,
        },
    )


def build_nav_mark_input(
    *,
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    fixed_notional_policy: FrozenFixedNotionalPolicy,
    policy_portfolio_policy: FrozenPolicyPortfolioPolicy,
    daily_nav_policy: FrozenDailyNavPolicy,
    series_kind: NavSeriesKind,
    series_id: str,
    decision_role: DecisionRole,
    path_id: str,
    session: date,
    ticker: str,
    mark_status: NavMarkStatus,
    close_price_idr: int | None,
    volume_shares: int | None,
    source_record_canonical_sha256: str,
    source_record_raw_file_sha256: str,
    source_record_raw_byte_length: int,
    source_as_of: datetime,
    available_at: datetime,
    captured_at: datetime,
    source_revision: int = 0,
    previous_source_record_sha256: str | None = None,
    supersedes_mark_input: NavMarkInput | None = None,
    reason_codes: Sequence[str] = (),
) -> NavMarkInput:
    """Build one current-session point-in-time mark without fallback."""

    verify_daily_nav_policy_binding(
        manifest,
        portfolio_policy,
        fixed_notional_policy,
        policy_portfolio_policy,
        daily_nav_policy,
    )
    payload = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": _required_hash(manifest),
        "daily_nav_policy_sha256": _required_hash(daily_nav_policy),
        "series_kind": series_kind,
        "series_id": series_id,
        "decision_role": decision_role,
        "path_id": path_id,
        "session": session,
        "ticker": ticker,
        "mark_status": mark_status,
        "close_price_idr": close_price_idr,
        "volume_shares": volume_shares,
        "source_id": daily_nav_policy.mark_source_id,
        "source_contract_version": (
            daily_nav_policy.mark_source_contract_version
        ),
        "source_definition_sha256": (
            daily_nav_policy.mark_source_definition_sha256
        ),
        "source_record_canonical_sha256": (
            source_record_canonical_sha256
        ),
        "source_record_raw_file_sha256": source_record_raw_file_sha256,
        "source_record_raw_byte_length": source_record_raw_byte_length,
        "source_as_of": source_as_of,
        "available_at": available_at,
        "captured_at": captured_at,
        "source_revision": source_revision,
        "previous_source_record_sha256": previous_source_record_sha256,
        "supersedes_mark_input_id": (
            supersedes_mark_input.mark_input_id
            if supersedes_mark_input is not None
            else None
        ),
        "supersedes_mark_input_sha256": (
            _required_hash(supersedes_mark_input)
            if supersedes_mark_input is not None
            else None
        ),
        "trading_calendar_sha256": manifest.trading_calendar_sha256,
        "corporate_action_policy_sha256": (
            manifest.corporate_action_policy_sha256
        ),
        "reason_codes": tuple(reason_codes),
    }
    defaults = {
        "contract_version": NAV_MARK_INPUT_VERSION,
        "evaluation_only": True,
        "live_authority": False,
        "affects_execution": False,
        "affects_ranking": False,
        "affects_sizing": False,
        "price_basis": (
            "OFFICIAL_CURRENT_SESSION_RAW_AS_TRADED_UNADJUSTED_CLOSE"
        ),
        "carry_sessions": 0,
        "fallback_used": False,
    }
    identity = {**defaults, **payload}
    return NavMarkInput(
        mark_input_id=canonical_daily_nav_artifact_id("NMARK", identity),
        **payload,
    )


def build_policy_portfolio_nav_series(
    *,
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    fixed_notional_policy: FrozenFixedNotionalPolicy,
    policy_portfolio_policy: FrozenPolicyPortfolioPolicy,
    daily_nav_policy: FrozenDailyNavPolicy,
    trading_calendar: TradingCalendar,
    genesis: PolicyPortfolioGenesisRecord,
    control_genesis_state: PolicyPortfolioSessionState,
    challenger_genesis_state: PolicyPortfolioSessionState,
    paired_sessions: Sequence[PairedPolicyPortfolioSessionRecord],
    control_marks: Sequence[NavMarkInput],
    challenger_marks: Sequence[NavMarkInput],
    through_session: date,
    snapshot_at: datetime,
) -> PairedDailyNavSeries:
    """Generate paired canonical policy NAV from immutable EOD states."""

    verify_daily_nav_policy_binding(
        manifest,
        portfolio_policy,
        fixed_notional_policy,
        policy_portfolio_policy,
        daily_nav_policy,
    )
    _verify_calendar(manifest, trading_calendar)
    manifest_hash = _required_hash(manifest)
    policy_hash = _required_hash(daily_nav_policy)
    if (
        genesis.protocol_id,
        genesis.component_id,
        genesis.manifest_sha256,
        genesis.policy_sha256,
        genesis.trading_calendar_sha256,
    ) != (
        manifest.protocol_id,
        manifest.component_id,
        manifest_hash,
        _required_hash(policy_portfolio_policy),
        trading_calendar.calendar_sha256,
    ):
        raise ShadowContractError("policy NAV genesis lineage mismatch")
    for state, role in (
        (control_genesis_state, "CONTROL"),
        (challenger_genesis_state, "CHALLENGER"),
    ):
        if (
            state.decision_role,
            state.state_sequence,
            state.genesis_sha256,
            state.payload.session,
            state.payload.accounting_equity_idr,
        ) != (
            role,
            0,
            _required_hash(genesis),
            genesis.genesis_session,
            APPROVED_STARTING_CAPITAL_IDR,
        ):
            raise ShadowContractError("policy NAV genesis state mismatch")
    sessions = _calendar_sessions(
        trading_calendar,
        genesis.genesis_session,
        through_session,
    )
    if not sessions or sessions[0] != genesis.genesis_session:
        raise ShadowContractError("policy NAV genesis is absent from calendar")
    record_by_session = {item.session: item for item in paired_sessions}
    if len(record_by_session) != len(paired_sessions):
        raise ShadowContractError("duplicate policy session record")
    if any(item not in sessions[1:] for item in record_by_session):
        raise ShadowContractError("policy session record lies outside NAV window")
    for record in paired_sessions:
        if (
            record.protocol_id,
            record.component_id,
            record.manifest_sha256,
            record.policy_sha256,
            record.trading_calendar_sha256,
            record.genesis_sha256,
        ) != (
            manifest.protocol_id,
            manifest.component_id,
            manifest_hash,
            _required_hash(policy_portfolio_policy),
            trading_calendar.calendar_sha256,
            _required_hash(genesis),
        ):
            raise ShadowContractError("policy session record lineage mismatch")

    control = _build_policy_path(
        manifest=manifest,
        daily_nav_policy=daily_nav_policy,
        policy_portfolio_policy=policy_portfolio_policy,
        genesis=genesis,
        genesis_state=control_genesis_state,
        sessions=sessions,
        record_by_session=record_by_session,
        marks=control_marks,
        snapshot_at=snapshot_at,
    )
    challenger = _build_policy_path(
        manifest=manifest,
        daily_nav_policy=daily_nav_policy,
        policy_portfolio_policy=policy_portfolio_policy,
        genesis=genesis,
        genesis_state=challenger_genesis_state,
        sessions=sessions,
        record_by_session=record_by_session,
        marks=challenger_marks,
        snapshot_at=snapshot_at,
    )
    assert control.daily_nav_policy_sha256 == policy_hash
    return PairedDailyNavSeries(
        series_kind="POLICY_PORTFOLIO_NAV",
        control=control,
        challenger=challenger,
        shared_session_union=sessions,
    )


def build_fixed_notional_sleeve_nav_series(
    *,
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    fixed_notional_policy: FrozenFixedNotionalPolicy,
    policy_portfolio_policy: FrozenPolicyPortfolioPolicy,
    daily_nav_policy: FrozenDailyNavPolicy,
    paired_record: PairedFixedNotionalRecord,
    control_marks: Sequence[NavMarkInput],
    challenger_marks: Sequence[NavMarkInput],
    snapshot_at: datetime,
) -> PairedDailyNavSeries:
    """Generate paired Rp13m attribution sleeves over one real union window."""

    verify_daily_nav_policy_binding(
        manifest,
        portfolio_policy,
        fixed_notional_policy,
        policy_portfolio_policy,
        daily_nav_policy,
    )
    manifest_hash = _required_hash(manifest)
    if (
        paired_record.protocol_id,
        paired_record.component_id,
        paired_record.manifest_sha256,
        paired_record.fixed_notional_policy_sha256,
        paired_record.trading_calendar.calendar_sha256,
        paired_record.target_sleeve_idr,
    ) != (
        manifest.protocol_id,
        manifest.component_id,
        manifest_hash,
        _required_hash(fixed_notional_policy),
        manifest.trading_calendar_sha256,
        FIXED_NOTIONAL_IDR,
    ):
        raise ShadowContractError("fixed-sleeve paired lineage mismatch")
    sessions = _fixed_union_sessions(paired_record)
    control = _build_fixed_path(
        manifest=manifest,
        daily_nav_policy=daily_nav_policy,
        paired_record=paired_record,
        lifecycle=paired_record.control,
        sessions=sessions,
        marks=control_marks,
        snapshot_at=snapshot_at,
    )
    challenger = _build_fixed_path(
        manifest=manifest,
        daily_nav_policy=daily_nav_policy,
        paired_record=paired_record,
        lifecycle=paired_record.challenger,
        sessions=sessions,
        marks=challenger_marks,
        snapshot_at=snapshot_at,
    )
    return PairedDailyNavSeries(
        series_kind="FIXED_NOTIONAL_SLEEVE_EQUITY",
        control=control,
        challenger=challenger,
        shared_session_union=sessions,
    )


def replay_nav_series_snapshot(
    snapshot: NavSeriesSnapshot,
) -> NavSeriesSnapshot:
    """Rebuild one snapshot solely from its frozen embedded predecessors."""

    return _build_snapshot(
        protocol_id=snapshot.protocol_id,
        component_id=snapshot.component_id,
        manifest_sha256=snapshot.manifest_sha256,
        daily_nav_policy_sha256=snapshot.daily_nav_policy_sha256,
        trading_calendar_sha256=snapshot.trading_calendar_sha256,
        series_kind=snapshot.series_kind,
        series_id=snapshot.series_id,
        decision_role=snapshot.decision_role,
        path_id=snapshot.path_id,
        mark_inputs=snapshot.mark_inputs,
        points=snapshot.points,
        events=snapshot.events,
        snapshot_at=snapshot.snapshot_at,
        prior_snapshot_id=snapshot.prior_snapshot_id,
        prior_snapshot_sha256=snapshot.prior_snapshot_sha256,
    )


def load_daily_nav_policy_v1(raw_file_bytes: bytes) -> FrozenDailyNavPolicy:
    """Load only the exact daily-NAV policy v1 contract."""

    return _load_exact_nav_contract(
        raw_file_bytes,
        FrozenDailyNavPolicy,
        DAILY_NAV_POLICY_VERSION,
        "daily-NAV policy",
    )


def load_nav_mark_input_v1(raw_file_bytes: bytes) -> NavMarkInput:
    """Load only an exact NavMarkInput v1; never reinterpret v1 forecasting."""

    return _load_exact_nav_contract(
        raw_file_bytes,
        NavMarkInput,
        NAV_MARK_INPUT_VERSION,
        "NAV mark input",
    )


def load_daily_nav_point_v1(raw_file_bytes: bytes) -> DailyNavPoint:
    """Load only an exact DailyNavPoint v1."""

    return _load_exact_nav_contract(
        raw_file_bytes,
        DailyNavPoint,
        DAILY_NAV_POINT_VERSION,
        "daily-NAV point",
    )


def load_nav_series_event_v1(raw_file_bytes: bytes) -> NavSeriesEvent:
    """Load only an exact append-only NAV event v1."""

    return _load_exact_nav_contract(
        raw_file_bytes,
        NavSeriesEvent,
        NAV_SERIES_EVENT_VERSION,
        "NAV series event",
    )


def load_nav_series_snapshot_v1(raw_file_bytes: bytes) -> NavSeriesSnapshot:
    """Load only an exact unanchored local NAV snapshot v1."""

    return _load_exact_nav_contract(
        raw_file_bytes,
        NavSeriesSnapshot,
        NAV_SERIES_SNAPSHOT_VERSION,
        "NAV series snapshot",
    )


def canonical_daily_nav_artifact_id(
    prefix: str,
    payload: Mapping[str, object],
) -> str:
    """Return a deterministic prefixed identity over normalized JSON."""

    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,31}", prefix):
        raise ValueError("daily-NAV artifact prefix is not canonical")
    raw = json.dumps(
        _canonical_value(dict(payload)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()}"


def _build_policy_path(
    *,
    manifest: ShadowProtocolManifest,
    daily_nav_policy: FrozenDailyNavPolicy,
    policy_portfolio_policy: FrozenPolicyPortfolioPolicy,
    genesis: PolicyPortfolioGenesisRecord,
    genesis_state: PolicyPortfolioSessionState,
    sessions: tuple[date, ...],
    record_by_session: Mapping[date, PairedPolicyPortfolioSessionRecord],
    marks: Sequence[NavMarkInput],
    snapshot_at: datetime,
) -> NavSeriesSnapshot:
    role = genesis_state.decision_role
    series_id = canonical_policy_nav_series_id(genesis, genesis_state)
    mark_map = _validated_mark_map(
        marks,
        series_kind="POLICY_PORTFOLIO_NAV",
        series_id=series_id,
        decision_role=role,
        path_id=genesis_state.path_id,
        manifest=manifest,
        daily_nav_policy=daily_nav_policy,
    )
    points: list[DailyNavPoint] = []
    events: list[NavSeriesEvent] = []
    genesis_point = _make_point(
        manifest=manifest,
        daily_nav_policy=daily_nav_policy,
        series_kind="POLICY_PORTFOLIO_NAV",
        series_id=series_id,
        decision_role=role,
        path_id=genesis_state.path_id,
        session=genesis.genesis_session,
        frozen_at=max(genesis.genesis_at, snapshot_at),
        point_sequence=0,
        point_status="GENESIS_ANCHOR",
        settled_cash_idr=genesis.settled_cash_idr,
        sale_receivable_idr=genesis.sale_receivable_idr,
        purchase_payable_idr=genesis.purchase_payable_idr,
        marked_holdings_value_idr=genesis.marked_holdings_value_idr,
        unfunded_cost_liability_idr=0,
        unfunded_cost_origin_sha256=None,
        nav=estimable_money(APPROVED_STARTING_CAPITAL_IDR),
        diagnostic_equity_idr=APPROVED_STARTING_CAPITAL_IDR,
        previous=None,
        mark_inputs=(),
        predecessors=(
            DailyNavNamedPredecessor(
                name="daily_nav_policy",
                sha256=_required_hash(daily_nav_policy),
            ),
            DailyNavNamedPredecessor(
                name="genesis",
                sha256=_required_hash(genesis),
            ),
            DailyNavNamedPredecessor(
                name="genesis_state",
                sha256=_required_hash(genesis_state),
            ),
            DailyNavNamedPredecessor(
                name="policy_portfolio_policy",
                sha256=_required_hash(policy_portfolio_policy),
            ),
        ),
        lifecycle_tags=("GENESIS_ANCHOR",),
        censored_tickers=(),
        censor_duration_sessions=0,
        poisoned_from_session=None,
        reason_codes=(),
    )
    points.append(genesis_point)
    events.append(_make_event(genesis_point, None))
    poisoned_session: date | None = None
    poison_reasons: tuple[str, ...] = ()
    insolvent = False
    prior_state = genesis_state
    censor_tickers: tuple[str, ...] = ()
    censor_duration = 0

    for sequence, session in enumerate(sessions[1:], start=1):
        record = record_by_session.get(session)
        if poisoned_session is not None or insolvent:
            reasons = (
                poison_reasons
                if poisoned_session is not None
                else (INSOLVENT_TERMINAL,)
            )
            point = _permanent_null_point(
                manifest=manifest,
                daily_nav_policy=daily_nav_policy,
                series_kind="POLICY_PORTFOLIO_NAV",
                series_id=series_id,
                decision_role=role,
                path_id=genesis_state.path_id,
                session=session,
                frozen_at=snapshot_at,
                point_sequence=sequence,
                previous=points[-1],
                predecessors=(
                    DailyNavNamedPredecessor(
                        name="daily_nav_policy",
                        sha256=_required_hash(daily_nav_policy),
                    ),
                    DailyNavNamedPredecessor(
                        name="previous_point",
                        sha256=_required_hash(points[-1]),
                    ),
                ),
                reasons=reasons,
                poisoned_from_session=poisoned_session or points[-1].session,
                censored_tickers=censor_tickers,
                censor_duration_sessions=(
                    censor_duration + 1 if censor_tickers else 0
                ),
            )
            if censor_tickers:
                censor_duration += 1
        elif record is None:
            poison_reasons = (NOT_ESTIMABLE_MISSING_POLICY_SESSION,)
            poisoned_session = session
            point = _permanent_null_point(
                manifest=manifest,
                daily_nav_policy=daily_nav_policy,
                series_kind="POLICY_PORTFOLIO_NAV",
                series_id=series_id,
                decision_role=role,
                path_id=genesis_state.path_id,
                session=session,
                frozen_at=snapshot_at,
                point_sequence=sequence,
                previous=points[-1],
                predecessors=(
                    DailyNavNamedPredecessor(
                        name="daily_nav_policy",
                        sha256=_required_hash(daily_nav_policy),
                    ),
                    DailyNavNamedPredecessor(
                        name="previous_point",
                        sha256=_required_hash(points[-1]),
                    ),
                ),
                reasons=poison_reasons,
                poisoned_from_session=poisoned_session,
            )
        else:
            state = (
                record.control_state
                if role == "CONTROL"
                else record.challenger_state
            )
            transition = (
                record.control_transition
                if role == "CONTROL"
                else record.challenger_transition
            )
            if (
                state.path_id,
                state.decision_role,
                state.payload.session,
                state.previous_state_sha256,
            ) != (
                genesis_state.path_id,
                role,
                session,
                _required_hash(prior_state),
            ):
                raise ShadowContractError("policy NAV state chain mismatch")
            session_marks = tuple(
                mark_map.get((session, item.ticker))
                for item in state.payload.positions
            )
            missing_inputs = tuple(
                item for item in session_marks if item is None
            )
            explicit_missing = tuple(
                item
                for item in session_marks
                if item is not None
                and item.mark_status != "OFFICIAL_CURRENT_SESSION"
            )
            mark_tickers = tuple(
                sorted(
                    {
                        item.ticker
                        for item in explicit_missing
                        if item is not None
                    }
                    | {
                        position.ticker
                        for position, item in zip(
                            state.payload.positions,
                            session_marks,
                            strict=True,
                        )
                        if item is None
                    }
                )
            )
            state_reasons: tuple[str, ...] = ()
            if state.payload.path_status != "ACTIVE":
                state_reasons = state.payload.path_reason_codes
            elif missing_inputs or explicit_missing:
                state_reasons = tuple(
                    dict.fromkeys(
                        reason
                        for item in explicit_missing
                        if item is not None
                        for reason in item.reason_codes
                    )
                ) or (NOT_ESTIMABLE_MISSING_OFFICIAL_MARK,)
            official_marks = tuple(
                item
                for item in session_marks
                if item is not None
                and item.mark_status == "OFFICIAL_CURRENT_SESSION"
            )
            if not state_reasons:
                for position, mark in zip(
                    state.payload.positions,
                    official_marks,
                    strict=True,
                ):
                    if (
                        mark.ticker,
                        mark.session,
                        mark.close_price_idr,
                    ) != (
                        position.ticker,
                        session,
                        position.last_mark_price_idr,
                    ) or position.last_mark_session != session:
                        raise ShadowContractError(
                            "policy NAV official mark differs from EOD state"
                        )
                marked = sum(
                    position.current_quantity_shares
                    * int(mark.close_price_idr)
                    for position, mark in zip(
                        state.payload.positions,
                        official_marks,
                        strict=True,
                    )
                )
                computed = (
                    state.payload.settled_cash_idr
                    + state.payload.sale_receivable_idr
                    - state.payload.purchase_payable_idr
                    + marked
                )
                if (
                    marked != state.payload.marked_holdings_value_idr
                    or computed != state.payload.accounting_equity_idr
                ):
                    raise ShadowContractError(
                        "policy NAV differs by at least one IDR from EOD state"
                    )
                nav = estimable_money(computed)
                status: NavPointStatus = (
                    "INSOLVENT" if computed <= 0 else "ACTIVE"
                )
                diagnostic = computed
            else:
                marked = (
                    sum(
                        position.current_quantity_shares
                        * int(mark.close_price_idr)
                        for position, mark in zip(
                            state.payload.positions,
                            session_marks,
                            strict=True,
                        )
                        if mark is not None
                        and mark.close_price_idr is not None
                    )
                    if not missing_inputs and not explicit_missing
                    else None
                )
                nav = not_estimable_money(state_reasons[0])
                status = "NOT_ESTIMABLE"
                diagnostic = state.payload.accounting_equity_idr
                poisoned_session = session
                poison_reasons = state_reasons
                censor_tickers = mark_tickers
                censor_duration = 1 if mark_tickers else 0
            predecessors = (
                DailyNavNamedPredecessor(
                    name="daily_nav_policy",
                    sha256=_required_hash(daily_nav_policy),
                ),
                DailyNavNamedPredecessor(
                    name="paired_session",
                    sha256=_required_hash(record),
                ),
                DailyNavNamedPredecessor(
                    name="policy_state",
                    sha256=_required_hash(state),
                ),
                DailyNavNamedPredecessor(
                    name="policy_transition",
                    sha256=_required_hash(transition),
                ),
                DailyNavNamedPredecessor(
                    name="previous_policy_state",
                    sha256=_required_hash(prior_state),
                ),
                DailyNavNamedPredecessor(
                    name="previous_point",
                    sha256=_required_hash(points[-1]),
                ),
                *(
                    DailyNavNamedPredecessor(
                        name=f"mark[{index:04d}]",
                        sha256=_required_hash(mark),
                    )
                    for index, mark in enumerate(
                        sorted(
                            (
                                item
                                for item in session_marks
                                if item is not None
                            ),
                            key=lambda item: item.ticker,
                        )
                    )
                ),
            )
            point = _make_point(
                manifest=manifest,
                daily_nav_policy=daily_nav_policy,
                series_kind="POLICY_PORTFOLIO_NAV",
                series_id=series_id,
                decision_role=role,
                path_id=genesis_state.path_id,
                session=session,
                frozen_at=max(
                    snapshot_at,
                    state.payload.state_as_of,
                    *(
                        item.captured_at
                        for item in session_marks
                        if item is not None
                    ),
                ),
                point_sequence=sequence,
                point_status=status,
                settled_cash_idr=state.payload.settled_cash_idr,
                sale_receivable_idr=state.payload.sale_receivable_idr,
                purchase_payable_idr=state.payload.purchase_payable_idr,
                marked_holdings_value_idr=marked,
                unfunded_cost_liability_idr=0,
                unfunded_cost_origin_sha256=None,
                nav=nav,
                diagnostic_equity_idr=diagnostic,
                previous=points[-1],
                mark_inputs=tuple(
                    sorted(
                        (
                            item
                            for item in session_marks
                            if item is not None
                        ),
                        key=lambda item: item.mark_input_id,
                    )
                ),
                predecessors=predecessors,
                lifecycle_tags=(),
                censored_tickers=mark_tickers,
                censor_duration_sessions=(
                    1 if mark_tickers else 0
                ),
                poisoned_from_session=(
                    session if state_reasons else None
                ),
                reason_codes=state_reasons,
            )
            insolvent = point.point_status == "INSOLVENT"
            prior_state = state
        points.append(point)
        events.append(_make_event(point, events[-1]))
    used_marks = _marks_used(points, mark_map.values())
    if len(used_marks) != len(mark_map):
        raise ShadowContractError("policy NAV received extraneous mark evidence")
    return _build_snapshot(
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=_required_hash(manifest),
        daily_nav_policy_sha256=_required_hash(daily_nav_policy),
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        series_kind="POLICY_PORTFOLIO_NAV",
        series_id=series_id,
        decision_role=role,
        path_id=genesis_state.path_id,
        mark_inputs=used_marks,
        points=tuple(points),
        events=tuple(events),
        snapshot_at=max(
            snapshot_at,
            *(item.frozen_at for item in points),
        ),
    )


def _build_fixed_path(
    *,
    manifest: ShadowProtocolManifest,
    daily_nav_policy: FrozenDailyNavPolicy,
    paired_record: PairedFixedNotionalRecord,
    lifecycle: FixedNotionalLifecycle,
    sessions: tuple[date, ...],
    marks: Sequence[NavMarkInput],
    snapshot_at: datetime,
) -> NavSeriesSnapshot:
    role = lifecycle.decision_role
    series_id = canonical_fixed_sleeve_series_id(paired_record, role)
    path_id = lifecycle.lifecycle_id
    mark_map = _validated_mark_map(
        marks,
        series_kind="FIXED_NOTIONAL_SLEEVE_EQUITY",
        series_id=series_id,
        decision_role=role,
        path_id=path_id,
        manifest=manifest,
        daily_nav_policy=daily_nav_policy,
    )
    holdings_by_session: dict[date, list[FixedNotionalHoldingRecord]] = {}
    for item in lifecycle.holding_records:
        holdings_by_session.setdefault(item.event_session, []).append(item)
    cash_by_trade: dict[date, list[FixedNotionalCashFlowRecord]] = {}
    for item in lifecycle.cash_flow_records:
        cash_by_trade.setdefault(item.trade_session, []).append(item)
    payables: list[tuple[date, int, str]] = []
    receivables: list[tuple[date, int]] = []
    settled_cash = FIXED_NOTIONAL_IDR
    unfunded = 0
    unfunded_origin: str | None = None
    quantity = 0
    entry_cost_total = sum(
        item.cost_idr
        for item in lifecycle.cash_flow_records
        if item.event_type == "ENTRY_DEBIT"
    )
    points: list[DailyNavPoint] = []
    events: list[NavSeriesEvent] = []
    poisoned_session: date | None = None
    poison_reasons: tuple[str, ...] = ()
    censor_duration = 0
    censor_tickers: tuple[str, ...] = ()
    insolvent = False
    terminal_null_session = _fixed_terminal_null_session(lifecycle)

    for sequence, session in enumerate(sessions):
        if poisoned_session is not None or insolvent:
            reasons = (
                poison_reasons
                if poisoned_session is not None
                else (INSOLVENT_TERMINAL,)
            )
            point = _permanent_null_point(
                manifest=manifest,
                daily_nav_policy=daily_nav_policy,
                series_kind="FIXED_NOTIONAL_SLEEVE_EQUITY",
                series_id=series_id,
                decision_role=role,
                path_id=path_id,
                session=session,
                frozen_at=snapshot_at,
                point_sequence=sequence,
                previous=points[-1] if points else None,
                predecessors=(
                    DailyNavNamedPredecessor(
                        name="daily_nav_policy",
                        sha256=_required_hash(daily_nav_policy),
                    ),
                    DailyNavNamedPredecessor(
                        name="fixed_lifecycle",
                        sha256=_required_hash(lifecycle),
                    ),
                    *(
                        (
                            DailyNavNamedPredecessor(
                                name="previous_point",
                                sha256=_required_hash(points[-1]),
                            ),
                        )
                        if points
                        else ()
                    ),
                ),
                reasons=reasons,
                poisoned_from_session=poisoned_session or points[-1].session,
                censored_tickers=censor_tickers,
                censor_duration_sessions=(
                    censor_duration + 1 if censor_tickers else 0
                ),
            )
            if censor_tickers:
                censor_duration += 1
            points.append(point)
            events.append(_make_event(point, events[-1] if events else None))
            continue

        # Legal cash settlement occurs before the session's economic events.
        settling_payables = tuple(
            item for item in payables if item[0] == session
        )
        payables = [item for item in payables if item[0] != session]
        for _, amount, origin in settling_payables:
            paid = min(settled_cash, amount)
            settled_cash -= paid
            shortfall = amount - paid
            if shortfall:
                unfunded += shortfall
                unfunded_origin = origin
        settling_receivables = tuple(
            item for item in receivables if item[0] == session
        )
        receivables = [item for item in receivables if item[0] != session]
        for _, amount in settling_receivables:
            liability_payment = min(unfunded, amount)
            unfunded -= liability_payment
            amount -= liability_payment
            settled_cash += amount
            if unfunded == 0:
                unfunded_origin = None

        for cash_flow in sorted(
            cash_by_trade.get(session, ()),
            key=lambda item: (item.occurred_at, item.cash_flow_id),
        ):
            if cash_flow.event_type == "ENTRY_DEBIT":
                payables.append(
                    (
                        cash_flow.settlement_session,
                        cash_flow.gross_amount_idr + cash_flow.cost_idr,
                        _required_hash(cash_flow),
                    )
                )
            elif cash_flow.event_type == "EXIT_CREDIT":
                receivables.append(
                    (cash_flow.settlement_session, cash_flow.net_cash_change_idr)
                )
            else:
                settled_cash += cash_flow.net_cash_change_idr
        for holding in sorted(
            holdings_by_session.get(session, ()),
            key=lambda item: (item.occurred_at, item.holding_event_id),
        ):
            if holding.quantity_before_shares != quantity:
                raise ShadowContractError(
                    "fixed-sleeve holding transition is not contiguous"
                )
            quantity = holding.quantity_after_shares

        if unfunded > entry_cost_total:
            raise ShadowContractError(
                "fixed-sleeve liability exceeds exact entry-cost lineage"
            )
        if terminal_null_session == session:
            poison_reasons = tuple(
                dict.fromkeys(
                    (
                        *lifecycle.reason_codes,
                        NOT_ESTIMABLE_TERMINAL_UNRESOLVED,
                    )
                )
            )
            poisoned_session = session
            mark = (
                mark_map.get((session, lifecycle.ticker))
                if quantity
                else None
            )
            session_marks = (mark,) if mark is not None else ()
            if (
                mark is not None
                and mark.mark_status == "OFFICIAL_CURRENT_SESSION"
            ):
                assert mark.close_price_idr is not None
                marked = quantity * mark.close_price_idr
            else:
                marked = None
                if quantity:
                    terminal_mark_reasons = (
                        tuple(mark.reason_codes)
                        if mark is not None
                        else (NOT_ESTIMABLE_MISSING_OFFICIAL_MARK,)
                    )
                    poison_reasons = tuple(
                        dict.fromkeys(
                            (*poison_reasons, *terminal_mark_reasons)
                        )
                    )
                    censor_tickers = (lifecycle.ticker,)
                    censor_duration = 1
            nav = not_estimable_money(poison_reasons[0])
            point_status: NavPointStatus = "NOT_ESTIMABLE"
            diagnostic = _fixed_diagnostic(
                settled_cash,
                receivables,
                payables,
                quantity,
                (
                    mark.close_price_idr
                    if marked is not None and mark is not None
                    else None
                ),
                unfunded,
            )
        elif quantity:
            mark = mark_map.get((session, lifecycle.ticker))
            session_marks = (mark,) if mark is not None else ()
            if mark is None or mark.mark_status != "OFFICIAL_CURRENT_SESSION":
                poison_reasons = (
                    tuple(mark.reason_codes)
                    if mark is not None
                    else (NOT_ESTIMABLE_MISSING_OFFICIAL_MARK,)
                )
                poisoned_session = session
                censor_tickers = (lifecycle.ticker,)
                censor_duration = 1
                marked = None
                nav = not_estimable_money(poison_reasons[0])
                point_status = "NOT_ESTIMABLE"
                diagnostic = _fixed_diagnostic(
                    settled_cash,
                    receivables,
                    payables,
                    quantity,
                    None,
                    unfunded,
                )
            else:
                assert mark.close_price_idr is not None
                marked = quantity * mark.close_price_idr
                computed = _fixed_equity(
                    settled_cash,
                    receivables,
                    payables,
                    marked,
                    unfunded,
                )
                nav = estimable_money(computed)
                point_status = "INSOLVENT" if computed <= 0 else "ACTIVE"
                diagnostic = computed
        else:
            session_marks = ()
            marked = 0
            computed = _fixed_equity(
                settled_cash,
                receivables,
                payables,
                marked,
                unfunded,
            )
            nav = estimable_money(computed)
            if sequence == 0:
                point_status = "GENESIS_ANCHOR"
            elif payables or receivables:
                point_status = "SETTLEMENT_ONLY"
            elif lifecycle.terminal_event == "NO_ACTION":
                point_status = "NO_ACTION_FLAT"
            else:
                point_status = "ACTIVE"
            diagnostic = computed
        tags: list[str] = []
        if not quantity and (payables or receivables):
            tags.append("SETTLEMENT_ONLY")
        if lifecycle.terminal_event == "NO_ACTION":
            tags.append("NO_ACTION_FLAT")
        if lifecycle.fill_status == "EXPIRED_UNFILLED":
            tags.append("UNFILLED_WINDOW")
        predecessors = (
            DailyNavNamedPredecessor(
                name="daily_nav_policy",
                sha256=_required_hash(daily_nav_policy),
            ),
            DailyNavNamedPredecessor(
                name="fixed_lifecycle",
                sha256=_required_hash(lifecycle),
            ),
            DailyNavNamedPredecessor(
                name="paired_fixed_record",
                sha256=_required_hash(paired_record),
            ),
            *(
                (
                    DailyNavNamedPredecessor(
                        name="previous_point",
                        sha256=_required_hash(points[-1]),
                    ),
                )
                if points
                else ()
            ),
            *(
                DailyNavNamedPredecessor(
                    name=f"mark[{index:04d}]",
                    sha256=_required_hash(mark),
                )
                for index, mark in enumerate(session_marks)
            ),
            *(
                (
                    DailyNavNamedPredecessor(
                        name="entry_cost",
                        sha256=unfunded_origin,
                    ),
                )
                if unfunded_origin is not None
                else ()
            ),
        )
        point = _make_point(
            manifest=manifest,
            daily_nav_policy=daily_nav_policy,
            series_kind="FIXED_NOTIONAL_SLEEVE_EQUITY",
            series_id=series_id,
            decision_role=role,
            path_id=path_id,
            session=session,
            frozen_at=max(
                (
                    snapshot_at,
                    *(item.captured_at for item in session_marks),
                )
            ),
            point_sequence=sequence,
            point_status=point_status,
            settled_cash_idr=settled_cash,
            sale_receivable_idr=sum(item[1] for item in receivables),
            purchase_payable_idr=sum(item[1] for item in payables),
            marked_holdings_value_idr=marked,
            unfunded_cost_liability_idr=unfunded,
            unfunded_cost_origin_sha256=unfunded_origin,
            nav=nav,
            diagnostic_equity_idr=diagnostic,
            previous=points[-1] if points else None,
            mark_inputs=session_marks,
            predecessors=predecessors,
            lifecycle_tags=tuple(tags),
            censored_tickers=(
                (lifecycle.ticker,)
                if point_status == "NOT_ESTIMABLE"
                and marked is None
                and quantity
                else ()
            ),
            censor_duration_sessions=(
                1
                if point_status == "NOT_ESTIMABLE"
                and marked is None
                and quantity
                else 0
            ),
            poisoned_from_session=(
                session if point_status == "NOT_ESTIMABLE" else None
            ),
            reason_codes=(
                poison_reasons if point_status == "NOT_ESTIMABLE" else ()
            ),
        )
        insolvent = point.point_status == "INSOLVENT"
        points.append(point)
        events.append(_make_event(point, events[-1] if events else None))
    used_marks = _marks_used(points, mark_map.values())
    if len(used_marks) != len(mark_map):
        raise ShadowContractError("fixed-sleeve NAV received extraneous mark evidence")
    return _build_snapshot(
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=_required_hash(manifest),
        daily_nav_policy_sha256=_required_hash(daily_nav_policy),
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        series_kind="FIXED_NOTIONAL_SLEEVE_EQUITY",
        series_id=series_id,
        decision_role=role,
        path_id=path_id,
        mark_inputs=used_marks,
        points=tuple(points),
        events=tuple(events),
        snapshot_at=max(
            snapshot_at,
            *(item.frozen_at for item in points),
        ),
    )


def _make_point(
    *,
    manifest: ShadowProtocolManifest,
    daily_nav_policy: FrozenDailyNavPolicy,
    series_kind: NavSeriesKind,
    series_id: str,
    decision_role: DecisionRole,
    path_id: str,
    session: date,
    frozen_at: datetime,
    point_sequence: int,
    point_status: NavPointStatus,
    settled_cash_idr: int | None,
    sale_receivable_idr: int | None,
    purchase_payable_idr: int | None,
    marked_holdings_value_idr: int | None,
    unfunded_cost_liability_idr: int,
    unfunded_cost_origin_sha256: str | None,
    nav: EstimableMoney,
    diagnostic_equity_idr: int | None,
    previous: DailyNavPoint | None,
    mark_inputs: Sequence[NavMarkInput],
    predecessors: Sequence[DailyNavNamedPredecessor],
    lifecycle_tags: Sequence[str],
    censored_tickers: Sequence[str],
    censor_duration_sessions: int,
    poisoned_from_session: date | None,
    reason_codes: Sequence[str],
) -> DailyNavPoint:
    if previous is None:
        daily_return = not_estimable_ratio(NOT_ESTIMABLE_NO_PREDECESSOR)
        previous_id = None
        previous_hash = None
        previous_nav = None
    else:
        previous_id = previous.point_id
        previous_hash = _required_hash(previous)
        previous_nav = previous.nav
        if (
            nav.status == "ESTIMABLE"
            and previous.nav.status == "ESTIMABLE"
            and nav.value_idr is not None
            and previous.nav.value_idr not in (None, 0)
        ):
            daily_return = estimable_ratio(
                quantize_ratio(
                    nav.value_idr - previous.nav.value_idr,
                    previous.nav.value_idr,
                )
            )
        else:
            daily_return = not_estimable_ratio(
                reason_codes[0]
                if reason_codes
                else NOT_ESTIMABLE_PREDECESSOR_GAP
            )
    ordered_marks = tuple(sorted(mark_inputs, key=lambda item: item.mark_input_id))
    payload = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": _required_hash(manifest),
        "daily_nav_policy_sha256": _required_hash(daily_nav_policy),
        "trading_calendar_sha256": manifest.trading_calendar_sha256,
        "series_kind": series_kind,
        "series_id": series_id,
        "decision_role": decision_role,
        "path_id": path_id,
        "session": session,
        "as_of": session_close_at(session),
        "frozen_at": frozen_at,
        "point_sequence": point_sequence,
        "revision": 0,
        "evidence_type": "PRIMARY_FIRST_SEEN",
        "point_status": point_status,
        "settled_cash_idr": settled_cash_idr,
        "sale_receivable_idr": sale_receivable_idr,
        "purchase_payable_idr": purchase_payable_idr,
        "marked_holdings_value_idr": marked_holdings_value_idr,
        "unfunded_cost_liability_idr": unfunded_cost_liability_idr,
        "unfunded_cost_origin_sha256": unfunded_cost_origin_sha256,
        "nav": nav,
        "diagnostic_equity_idr": diagnostic_equity_idr,
        "daily_return": daily_return,
        "previous_point_id": previous_id,
        "previous_point_sha256": previous_hash,
        "previous_nav": previous_nav,
        "supersedes_point_id": None,
        "supersedes_point_sha256": None,
        "mark_input_ids": tuple(item.mark_input_id for item in ordered_marks),
        "mark_input_sha256s": tuple(
            _required_hash(item) for item in ordered_marks
        ),
        "predecessors": tuple(sorted(predecessors, key=lambda item: item.name)),
        "lifecycle_tags": tuple(lifecycle_tags),
        "censored_tickers": tuple(sorted(censored_tickers)),
        "censor_duration_sessions": censor_duration_sessions,
        "poisoned_from_session": poisoned_from_session,
        "reason_codes": tuple(reason_codes),
    }
    defaults = {
        "contract_version": DAILY_NAV_POINT_VERSION,
        "evaluation_only": True,
        "live_authority": False,
        "affects_execution": False,
        "affects_ranking": False,
        "affects_sizing": False,
    }
    return DailyNavPoint(
        point_id=canonical_daily_nav_artifact_id(
            "NPOINT",
            {**defaults, **payload},
        ),
        **payload,
    )


def _permanent_null_point(
    *,
    manifest: ShadowProtocolManifest,
    daily_nav_policy: FrozenDailyNavPolicy,
    series_kind: NavSeriesKind,
    series_id: str,
    decision_role: DecisionRole,
    path_id: str,
    session: date,
    frozen_at: datetime,
    point_sequence: int,
    previous: DailyNavPoint | None,
    predecessors: Sequence[DailyNavNamedPredecessor],
    reasons: Sequence[str],
    poisoned_from_session: date,
    censored_tickers: Sequence[str] = (),
    censor_duration_sessions: int = 0,
) -> DailyNavPoint:
    reason_tuple = tuple(dict.fromkeys(reasons))
    return _make_point(
        manifest=manifest,
        daily_nav_policy=daily_nav_policy,
        series_kind=series_kind,
        series_id=series_id,
        decision_role=decision_role,
        path_id=path_id,
        session=session,
        frozen_at=max(frozen_at, session_close_at(session)),
        point_sequence=point_sequence,
        point_status="NOT_ESTIMABLE",
        settled_cash_idr=None,
        sale_receivable_idr=None,
        purchase_payable_idr=None,
        marked_holdings_value_idr=None,
        unfunded_cost_liability_idr=0,
        unfunded_cost_origin_sha256=None,
        nav=not_estimable_money(reason_tuple[0]),
        diagnostic_equity_idr=None,
        previous=previous,
        mark_inputs=(),
        predecessors=predecessors,
        lifecycle_tags=("PERMANENT_NULL",),
        censored_tickers=censored_tickers,
        censor_duration_sessions=censor_duration_sessions,
        poisoned_from_session=poisoned_from_session,
        reason_codes=reason_tuple,
    )


def _make_event(
    point: DailyNavPoint,
    previous: NavSeriesEvent | None,
) -> NavSeriesEvent:
    payload = {
        "protocol_id": point.protocol_id,
        "component_id": point.component_id,
        "manifest_sha256": point.manifest_sha256,
        "daily_nav_policy_sha256": point.daily_nav_policy_sha256,
        "series_kind": point.series_kind,
        "series_id": point.series_id,
        "decision_role": point.decision_role,
        "path_id": point.path_id,
        "event_sequence": 0 if previous is None else previous.event_sequence + 1,
        "event_type": (
            "PRIMARY_POINT_APPENDED"
            if point.revision == 0
            else "CORRECTION_APPENDED"
        ),
        "event_at": point.frozen_at,
        "point_id": point.point_id,
        "point_sha256": _required_hash(point),
        "point_session": point.session,
        "point_revision": point.revision,
        "mark_input_sha256s": point.mark_input_sha256s,
        "previous_event_id": previous.event_id if previous else None,
        "previous_event_sha256": _required_hash(previous) if previous else None,
    }
    defaults = {
        "contract_version": NAV_SERIES_EVENT_VERSION,
        "evaluation_only": True,
        "live_authority": False,
        "affects_execution": False,
        "affects_ranking": False,
        "affects_sizing": False,
    }
    return NavSeriesEvent(
        event_id=canonical_daily_nav_artifact_id(
            "NEVENT",
            {**defaults, **payload},
        ),
        **payload,
    )


def _build_snapshot(
    *,
    protocol_id: str,
    component_id: str,
    manifest_sha256: str,
    daily_nav_policy_sha256: str,
    trading_calendar_sha256: str,
    series_kind: NavSeriesKind,
    series_id: str,
    decision_role: DecisionRole,
    path_id: str,
    mark_inputs: Sequence[NavMarkInput],
    points: Sequence[DailyNavPoint],
    events: Sequence[NavSeriesEvent],
    snapshot_at: datetime,
    prior_snapshot_id: str | None = None,
    prior_snapshot_sha256: str | None = None,
) -> NavSeriesSnapshot:
    ordered_marks = tuple(
        sorted(
            mark_inputs,
            key=lambda item: (
                item.session,
                item.ticker,
                item.source_revision,
                item.mark_input_id,
            ),
        )
    )
    point_tuple = tuple(points)
    event_tuple = tuple(events)
    primary = tuple(item for item in point_tuple if item.revision == 0)
    first_poison = next(
        (item for item in primary if item.nav.status == "NOT_ESTIMABLE"),
        None,
    )
    payload = {
        "protocol_id": protocol_id,
        "component_id": component_id,
        "manifest_sha256": manifest_sha256,
        "daily_nav_policy_sha256": daily_nav_policy_sha256,
        "trading_calendar_sha256": trading_calendar_sha256,
        "series_kind": series_kind,
        "series_id": series_id,
        "decision_role": decision_role,
        "path_id": path_id,
        "snapshot_at": snapshot_at,
        "first_session": primary[0].session,
        "last_session": primary[-1].session,
        "mark_inputs": ordered_marks,
        "mark_input_sha256s": tuple(
            _required_hash(item) for item in ordered_marks
        ),
        "points": point_tuple,
        "point_sha256s": tuple(_required_hash(item) for item in point_tuple),
        "events": event_tuple,
        "event_sha256s": tuple(_required_hash(item) for item in event_tuple),
        "event_count": len(event_tuple),
        "primary_point_count": len(primary),
        "correction_count": len(point_tuple) - len(primary),
        "head_event_id": event_tuple[0].event_id,
        "head_event_sha256": _required_hash(event_tuple[0]),
        "tail_event_id": event_tuple[-1].event_id,
        "tail_event_sha256": _required_hash(event_tuple[-1]),
        "prior_snapshot_id": prior_snapshot_id,
        "prior_snapshot_sha256": prior_snapshot_sha256,
        "chain_completeness_status": NAV_CHAIN_COMPLETENESS_STATUS,
        "poisoned_from_session": (
            first_poison.session if first_poison else None
        ),
        "poison_reason_codes": (
            first_poison.reason_codes if first_poison else ()
        ),
    }
    defaults = {
        "contract_version": NAV_SERIES_SNAPSHOT_VERSION,
        "evaluation_only": True,
        "live_authority": False,
        "affects_execution": False,
        "affects_ranking": False,
        "affects_sizing": False,
    }
    return NavSeriesSnapshot(
        snapshot_id=canonical_daily_nav_artifact_id(
            "NSNAP",
            {**defaults, **payload},
        ),
        **payload,
    )


def _validated_mark_map(
    marks: Sequence[NavMarkInput],
    *,
    series_kind: NavSeriesKind,
    series_id: str,
    decision_role: DecisionRole,
    path_id: str,
    manifest: ShadowProtocolManifest,
    daily_nav_policy: FrozenDailyNavPolicy,
) -> dict[tuple[date, str], NavMarkInput]:
    result: dict[tuple[date, str], NavMarkInput] = {}
    common = (
        manifest.protocol_id,
        manifest.component_id,
        _required_hash(manifest),
        _required_hash(daily_nav_policy),
        series_kind,
        series_id,
        decision_role,
        path_id,
        manifest.trading_calendar_sha256,
        manifest.corporate_action_policy_sha256,
    )
    for mark in marks:
        if (
            mark.protocol_id,
            mark.component_id,
            mark.manifest_sha256,
            mark.daily_nav_policy_sha256,
            mark.series_kind,
            mark.series_id,
            mark.decision_role,
            mark.path_id,
            mark.trading_calendar_sha256,
            mark.corporate_action_policy_sha256,
        ) != common:
            raise ShadowContractError("NAV mark belongs to another series")
        key = (mark.session, mark.ticker)
        existing = result.get(key)
        if existing is not None:
            raise ShadowContractError(
                "multiple mark revisions require explicit correction workflow"
            )
        result[key] = mark
    return result


def _fixed_union_sessions(
    paired_record: PairedFixedNotionalRecord,
) -> tuple[date, ...]:
    calendar = paired_record.trading_calendar
    signal_session = paired_record.control.signal_at.astimezone(
        IDX_TIMEZONE
    ).date()
    if paired_record.challenger.signal_at.astimezone(
        IDX_TIMEZONE
    ).date() != signal_session:
        raise ShadowContractError("paired fixed lifecycles have different signal dates")
    end = max(
        _fixed_lifecycle_end(paired_record.control),
        _fixed_lifecycle_end(paired_record.challenger),
    )
    return _calendar_sessions(calendar, signal_session, end)


def _fixed_lifecycle_end(lifecycle: FixedNotionalLifecycle) -> date:
    if lifecycle.terminal_event == "NO_ACTION":
        return lifecycle.signal_at.astimezone(IDX_TIMEZONE).date()
    dates = [
        lifecycle.signal_at.astimezone(IDX_TIMEZONE).date(),
        *(
            item.settlement_session
            for item in lifecycle.cash_flow_records
        ),
    ]
    for value in (
        lifecycle.maturity_at,
        lifecycle.closed_at,
        lifecycle.evaluated_at
        if lifecycle.status in ("NOT_ESTIMABLE", "PENDING")
        else None,
    ):
        if value is not None:
            dates.append(value.astimezone(IDX_TIMEZONE).date())
    return max(dates)


def _fixed_terminal_null_session(
    lifecycle: FixedNotionalLifecycle,
) -> date | None:
    if lifecycle.status not in ("NOT_ESTIMABLE", "PENDING"):
        return None
    return (
        lifecycle.maturity_at
        or lifecycle.evaluated_at
    ).astimezone(IDX_TIMEZONE).date()


def _fixed_lifecycle(
    paired_record: PairedFixedNotionalRecord,
    decision_role: DecisionRole,
) -> FixedNotionalLifecycle:
    return (
        paired_record.control
        if decision_role == "CONTROL"
        else paired_record.challenger
    )


def _calendar_sessions(
    trading_calendar: TradingCalendar,
    start: date,
    end: date,
) -> tuple[date, ...]:
    if end < start:
        raise ShadowContractError("daily-NAV session window is inverted")
    sessions = tuple(
        item for item in trading_calendar.sessions if start <= item <= end
    )
    if not sessions or sessions[0] != start or sessions[-1] != end:
        raise ShadowContractError(
            "daily-NAV boundaries must be frozen IDX sessions"
        )
    return sessions


def _verify_calendar(
    manifest: ShadowProtocolManifest,
    trading_calendar: TradingCalendar,
) -> None:
    if (
        trading_calendar.calendar_id,
        trading_calendar.calendar_sha256,
    ) != (
        manifest.trading_calendar_id,
        manifest.trading_calendar_sha256,
    ):
        raise ShadowContractError("daily-NAV trading-calendar binding mismatch")


def _fixed_equity(
    settled_cash: int,
    receivables: Sequence[tuple[date, int]],
    payables: Sequence[tuple[date, int, str]],
    marked: int,
    liability: int,
) -> int:
    return (
        settled_cash
        + sum(item[1] for item in receivables)
        - sum(item[1] for item in payables)
        + marked
        - liability
    )


def _fixed_diagnostic(
    settled_cash: int,
    receivables: Sequence[tuple[date, int]],
    payables: Sequence[tuple[date, int, str]],
    quantity: int,
    mark_price: int | None,
    liability: int,
) -> int | None:
    if quantity and mark_price is None:
        return None
    return _fixed_equity(
        settled_cash,
        receivables,
        payables,
        quantity * (mark_price or 0),
        liability,
    )


def _marks_used(
    points: Sequence[DailyNavPoint],
    candidates: Sequence[NavMarkInput],
) -> tuple[NavMarkInput, ...]:
    used = {
        sha
        for point in points
        for sha in point.mark_input_sha256s
    }
    return tuple(
        item for item in candidates if _required_hash(item) in used
    )


def _identity_payload(model: BaseModel, identity_field: str) -> dict[str, object]:
    payload = model.model_dump(mode="python", exclude={identity_field})
    return payload


def _load_exact_nav_contract(
    raw_file_bytes: bytes,
    model_type: type[BaseModel],
    required_version: str,
    label: str,
):
    if not isinstance(raw_file_bytes, bytes):
        raise ShadowContractError(f"{label} loader requires raw bytes")
    try:
        payload = json.loads(
            raw_file_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ShadowContractError(f"{label} JSON is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise ShadowContractError(f"{label} root must be a JSON object")
    received = payload.get("contract_version")
    if received != required_version:
        raise ShadowContractError(
            f"{label} requires {required_version}; received {received!r}; "
            "legacy forecasting/shadow-evaluation artifacts are never "
            "reinterpreted as daily NAV"
        )
    try:
        return model_type.model_validate(payload)
    except Exception as exc:
        raise ShadowContractError(f"{label} contract validation failed") from exc


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _required_hash(model: BaseModel | None) -> str:
    value = canonical_sha256(model)
    if value is None:
        raise ValueError("daily-NAV predecessor hash is absent")
    return value


def _canonical_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("canonical daily-NAV datetime must be aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    return value
