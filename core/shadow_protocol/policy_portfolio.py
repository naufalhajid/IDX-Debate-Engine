"""Independent, evaluation-only policy portfolios for RS-P2-016.

This module is additive.  It formalizes identical policy rules for CONTROL
and CHALLENGER, while allowing their economic paths to diverge after one
shared all-cash genesis.  It never changes a live decision, sizes a live
order, grants collection authority, or reinterprets ``shadow-evaluation-v1``.

All persisted money is strict integer IDR.  Ratio fields are derived from
integer numerators, quantized to twelve decimal places, and validated before
canonical hashing.  The exact RS-P2-015 PairInput and primary lifecycle are
lineage evidence only: policy-size fills, costs, capacity, settlement, and
cash flows are replayed from the raw PairInput at the recorded policy size.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
from typing import Annotated, Literal, Sequence, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from .calendar import IDX_TIMEZONE, SESSION_OPEN, TradingCalendar, session_close_at
from .contracts import (
    ComponentID,
    FrozenParameter,
    ShadowContractError,
    ShadowDecision,
    ShadowProtocolManifest,
    canonical_sha256,
)
from .evidence import (
    CandidateSetManifest,
    RawCandidateSetCapture,
    assert_opportunity_set_parity,
)
from .fixed_notional import (
    FixedNotionalLiquidityMeasurement,
    FixedNotionalMarketBar,
    FixedNotionalPairInput,
    FrozenFixedNotionalPolicy,
    PairedFixedNotionalRecord,
    _apply_split,
    _entry_fill,
    _geometry_for_session,
    _integer_geometry,
    _rights_effective,
    _settlement_session,
    _terminal_for_bar,
    fixed_notional_cost_idr,
    verify_fixed_notional_policy_binding,
    verify_paired_fixed_notional_record,
)
from .portfolio import (
    APPROVED_DAILY_LOSS_STOP_FRACTION,
    APPROVED_MAX_GROSS_EXPOSURE_FRACTION,
    APPROVED_MAX_PORTFOLIO_HEAT_FRACTION,
    APPROVED_MINIMUM_CASH_RESERVE_FRACTION,
    APPROVED_RATIO_QUANTIZATION_DECIMAL_PLACES,
    APPROVED_STARTING_CAPITAL_IDR,
    APPROVED_TOTAL_LOSS_BUDGET_FRACTION,
    FrozenPortfolioPolicy,
    portfolio_manifest_parameters,
    quantize_ratio,
    quantize_ratio_decimal,
    verify_portfolio_manifest_binding,
)


POLICY_PORTFOLIO_POLICY_VERSION = "shadow-policy-portfolio-policy-v1"
POLICY_PORTFOLIO_GENESIS_VERSION = "shadow-policy-portfolio-genesis-v1"
POLICY_PORTFOLIO_REGIME_VERSION = "shadow-policy-portfolio-regime-v1"
POLICY_PORTFOLIO_LIQUIDITY_VERSION = "shadow-policy-liquidity-session-v1"
POLICY_PORTFOLIO_CLASSIFICATION_VERSION = (
    "shadow-policy-candidate-classification-v1"
)
POLICY_PORTFOLIO_CANDIDATE_INPUT_VERSION = (
    "shadow-policy-portfolio-candidate-input-v1"
)
POLICY_PORTFOLIO_SESSION_INPUT_VERSION = "shadow-policy-portfolio-session-input-v1"
POLICY_PORTFOLIO_SETTLEMENT_LEG_VERSION = (
    "shadow-policy-portfolio-settlement-leg-v1"
)
POLICY_PORTFOLIO_COMMITMENT_VERSION = "shadow-policy-portfolio-commitment-v1"
POLICY_PORTFOLIO_POSITION_VERSION = "shadow-policy-portfolio-position-v1"
POLICY_PORTFOLIO_EVENT_VERSION = "shadow-policy-portfolio-event-v1"
POLICY_PORTFOLIO_PAYLOAD_VERSION = "shadow-policy-portfolio-payload-v1"
POLICY_PORTFOLIO_TRANSITION_VERSION = "shadow-policy-portfolio-transition-v1"
POLICY_PORTFOLIO_STATE_VERSION = "shadow-policy-portfolio-state-v1"
POLICY_PORTFOLIO_PAIRED_SESSION_VERSION = (
    "shadow-paired-policy-portfolio-session-v1"
)

POLICY_PORTFOLIO_CAPABILITY_STATUS = (
    "RS_P2_016_IMPLEMENTED_NOT_A1_ELIGIBLE"
)
POLICY_PORTFOLIO_CONFIG_PATH = "config/policy-portfolio-policy-v1.json"
POLICY_PORTFOLIO_HEAT_UNIT = "fraction_of_starting_capital"
POLICY_PORTFOLIO_NAV_STATUS = "NOT_ESTIMABLE_UNTIL_RS_P2_017"
POLICY_PORTFOLIO_PRIMARY_HORIZON = 15

GATE_ORDER: tuple[str, ...] = (
    "INTEGRITY_ESTIMABILITY",
    "DAILY_REALIZED_LOSS_STOP",
    "ALLOCATION_SIZE_DUPLICATE_REENTRY",
    "REGIME_SLOT_LIMIT",
    "SECTOR_CLUSTER_LIMIT",
    "LIQUIDITY_CAPACITY",
    "POSITION_AND_TOTAL_LOSS_BUDGET",
    "PORTFOLIO_HEAT",
    "GROSS_EXPOSURE",
    "MINIMUM_CASH_BUYING_POWER",
)

SESSION_EVENT_ORDER: tuple[str, ...] = (
    "RESET_DAILY_LATCH",
    "POST_T_PLUS_2_SETTLEMENTS",
    "APPLY_OPENING_CORPORATE_ACTIONS",
    "PROCESS_OPENING_EXITS",
    "PROCESS_OPENING_FILLS",
    "PROCESS_SESSION_EXITS_AND_DIVIDENDS",
    "PROCESS_SESSION_ONLY_FILLS",
    "EXPIRE_OR_CANCEL_COMMITMENTS",
    "ADMIT_NEW_SIGNALS",
    "PERSIST_END_OF_SESSION_STATE",
)

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
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Z0-9][A-Z0-9.-]{0,15}$"),
]
StrictPositiveInt = Annotated[StrictInt, Field(gt=0)]
StrictNonNegativeInt = Annotated[StrictInt, Field(ge=0)]
DecisionRole: TypeAlias = Literal["CONTROL", "CHALLENGER"]
RegimeName: TypeAlias = Literal["BULL", "SIDEWAYS", "BEAR_STRESS", "UNKNOWN"]
PathStatus: TypeAlias = Literal["ACTIVE", "NOT_ESTIMABLE_FROM_SESSION"]


class _PolicyModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class _EvaluationOnlyPolicyArtifact(_PolicyModel):
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False


class FrozenPolicyPortfolioPolicy(_EvaluationOnlyPolicyArtifact):
    """Owner-approved PP1-PP14 rules without a manifest-hash cycle."""

    contract_version: Literal["shadow-policy-portfolio-policy-v1"] = (
        POLICY_PORTFOLIO_POLICY_VERSION
    )
    policy_id: NonEmptyString
    phase2_capability_status: Literal[
        "RS_P2_016_IMPLEMENTED_NOT_A1_ELIGIBLE"
    ] = POLICY_PORTFOLIO_CAPABILITY_STATUS
    portfolio_policy_id: NonEmptyString
    portfolio_policy_sha256: Sha256
    fixed_notional_policy_id: NonEmptyString
    fixed_notional_policy_sha256: Sha256
    label_definition_sha256: Sha256
    cost_assumptions_sha256: Sha256
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    methodology_document_sha256: Sha256

    starting_capital_idr: Literal[100_000_000] = (
        APPROVED_STARTING_CAPITAL_IDR
    )
    genesis_rule: Literal[
        "IDENTICAL_EMPTY_ALL_CASH_NO_OBSERVED_HOLDINGS"
    ] = "IDENTICAL_EMPTY_ALL_CASH_NO_OBSERVED_HOLDINGS"
    recorded_size_rule: Literal[
        "RECORDED_FRACTION_OF_STARTING_CAPITAL_GROSS_BEFORE_COST"
    ] = "RECORDED_FRACTION_OF_STARTING_CAPITAL_GROSS_BEFORE_COST"
    recorded_size_missing_rule: Literal[
        "NOT_ESTIMABLE_POLICY_SIZE_NO_FALLBACK"
    ] = "NOT_ESTIMABLE_POLICY_SIZE_NO_FALLBACK"
    priority_rule: Literal[
        "RECORDED_RANK_ASC_SOURCE_ROW_NUMBER_ASC_TICKER_ASC"
    ] = "RECORDED_RANK_ASC_SOURCE_ROW_NUMBER_ASC_TICKER_ASC"
    ambiguous_priority_rule: Literal[
        "NOT_ESTIMABLE_PRIORITY"
    ] = "NOT_ESTIMABLE_PRIORITY"
    lot_size_shares: Literal[100] = 100
    lot_rounding_rule: Literal[
        "FLOOR_REQUESTED_GROSS_TO_WHOLE_BOARD_LOTS_AT_PLANNED_ENTRY_HIGH"
    ] = "FLOOR_REQUESTED_GROSS_TO_WHOLE_BOARD_LOTS_AT_PLANNED_ENTRY_HIGH"
    cost_rounding_rule: Literal[
        "AGGREGATE_APPLICABLE_BPS_THEN_CEIL_INTEGER_IDR"
    ] = "AGGREGATE_APPLICABLE_BPS_THEN_CEIL_INTEGER_IDR"

    gate_order: tuple[NonEmptyString, ...] = GATE_ORDER
    sector_max_names: Literal[2] = 2
    cluster_max_names: Literal[2] = 2
    sector_cluster_rule: Literal[
        "HARD_CAP_OPEN_PLUS_PENDING_NO_SOFT_OVERFLOW"
    ] = "HARD_CAP_OPEN_PLUS_PENDING_NO_SOFT_OVERFLOW"
    regime_effective_rule: Literal[
        "OBSERVED_SESSION_EFFECTIVE_NEXT_SESSION"
    ] = "OBSERVED_SESSION_EFFECTIVE_NEXT_SESSION"
    regime_down_rule: Literal[
        "NO_FORCED_EXIT_CANCEL_LOWEST_PRIORITY_PENDING"
    ] = "NO_FORCED_EXIT_CANCEL_LOWEST_PRIORITY_PENDING"
    unknown_regime_max_positions: Literal[0] = 0
    bull_max_positions: Literal[3] = 3
    sideways_max_positions: Literal[2] = 2
    bear_stress_max_positions: Literal[1] = 1

    reservation_rule: Literal[
        "COMMITMENT_WORST_CASE_GROSS_PLUS_ENTRY_COST"
    ] = "COMMITMENT_WORST_CASE_GROSS_PLUS_ENTRY_COST"
    fill_recheck_rule: Literal[
        "RECHECK_ALL_SAFETY_AND_POINT_IN_TIME_CAPACITY"
    ] = "RECHECK_ALL_SAFETY_AND_POINT_IN_TIME_CAPACITY"
    capacity_rule: Literal["ALL_OR_NONE"] = "ALL_OR_NONE"
    settlement_lag_sessions: Literal[2] = 2
    settlement_rule: Literal[
        "PURCHASE_PAYABLE_AND_SALE_RECEIVABLE_T_PLUS_2"
    ] = "PURCHASE_PAYABLE_AND_SALE_RECEIVABLE_T_PLUS_2"
    session_event_order: tuple[NonEmptyString, ...] = SESSION_EVENT_ORDER
    adverse_order_rule: Literal[
        "LOSS_VISIBLE_RESOURCES_NOT_REUSED_AT_SAME_TIMESTAMP"
    ] = "LOSS_VISIBLE_RESOURCES_NOT_REUSED_AT_SAME_TIMESTAMP"

    daily_loss_stop_fraction: Literal[0.03] = (
        APPROVED_DAILY_LOSS_STOP_FRACTION
    )
    daily_loss_denominator: Literal[
        "STARTING_CAPITAL"
    ] = "STARTING_CAPITAL"
    daily_stop_rule: Literal[
        "LATCH_TO_SESSION_CLOSE_CANCEL_PENDING_NO_FORCED_EXIT"
    ] = "LATCH_TO_SESSION_CLOSE_CANCEL_PENDING_NO_FORCED_EXIT"
    ticker_rule: Literal[
        "ONE_OPEN_OR_PENDING_NO_SAME_SESSION_REENTRY"
    ] = "ONE_OPEN_OR_PENDING_NO_SAME_SESSION_REENTRY"

    total_loss_budget_fraction: Literal[0.02] = (
        APPROVED_TOTAL_LOSS_BUDGET_FRACTION
    )
    max_portfolio_heat_fraction: Literal[0.013] = (
        APPROVED_MAX_PORTFOLIO_HEAT_FRACTION
    )
    portfolio_heat_unit: Literal[
        "fraction_of_starting_capital"
    ] = POLICY_PORTFOLIO_HEAT_UNIT
    risk_rule: Literal[
        "PLANNED_ENTRY_HIGH_MINUS_STOP_OPEN_PLUS_PENDING"
    ] = "PLANNED_ENTRY_HIGH_MINUS_STOP_OPEN_PLUS_PENDING"
    max_gross_exposure_fraction: Literal[0.95] = (
        APPROVED_MAX_GROSS_EXPOSURE_FRACTION
    )
    gross_exposure_denominator: Literal[
        "POINT_IN_TIME_ACCOUNTING_EQUITY_NOT_RS_P2_017_NAV_SERIES"
    ] = "POINT_IN_TIME_ACCOUNTING_EQUITY_NOT_RS_P2_017_NAV_SERIES"
    minimum_cash_reserve_fraction: Literal[0.05] = (
        APPROVED_MINIMUM_CASH_RESERVE_FRACTION
    )
    ratio_quantization_decimal_places: Literal[12] = (
        APPROVED_RATIO_QUANTIZATION_DECIMAL_PLACES
    )
    ratio_quantization_rounding_mode: Literal["ROUND_HALF_EVEN"] = (
        "ROUND_HALF_EVEN"
    )

    post_fill_missing_rule: Literal[
        "RETAIN_LAST_VERIFIED_HOLDING_AND_FREEZE_SIDE"
    ] = "RETAIN_LAST_VERIFIED_HOLDING_AND_FREEZE_SIDE"
    terminal_rule: Literal[
        "NO_SYNTHETIC_LIQUIDATION_REQUIRE_ENTRY_VALIDITY_15D_T_PLUS_2_RUNWAY"
    ] = "NO_SYNTHETIC_LIQUIDATION_REQUIRE_ENTRY_VALIDITY_15D_T_PLUS_2_RUNWAY"
    primary_horizon_trading_days: Literal[15] = (
        POLICY_PORTFOLIO_PRIMARY_HORIZON
    )
    p2_015_lifecycle_role: Literal[
        "EVIDENCE_ONLY_NOT_POLICY_CASH_AUTHORITY"
    ] = "EVIDENCE_ONLY_NOT_POLICY_CASH_AUTHORITY"

    @model_validator(mode="after")
    def verify_policy(self) -> FrozenPolicyPortfolioPolicy:
        if self.gate_order != GATE_ORDER:
            raise ValueError("policy-portfolio gate order differs from PP5")
        if self.session_event_order != SESSION_EVENT_ORDER:
            raise ValueError("policy-portfolio session order differs from PP9")
        return self


class PolicyPortfolioGenesisRecord(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal["shadow-policy-portfolio-genesis-v1"] = (
        POLICY_PORTFOLIO_GENESIS_VERSION
    )
    genesis_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    baseline_manifest_id: NonEmptyString
    baseline_manifest_sha256: Sha256
    policy_sha256: Sha256
    portfolio_policy_sha256: Sha256
    fixed_notional_policy_sha256: Sha256
    trading_calendar_sha256: Sha256
    genesis_session: date
    genesis_at: datetime
    starting_capital_idr: Literal[100_000_000] = (
        APPROVED_STARTING_CAPITAL_IDR
    )
    settled_cash_idr: Literal[100_000_000] = APPROVED_STARTING_CAPITAL_IDR
    sale_receivable_idr: Literal[0] = 0
    purchase_payable_idr: Literal[0] = 0
    reserved_cash_idr: Literal[0] = 0
    marked_holdings_value_idr: Literal[0] = 0
    holdings_count: Literal[0] = 0
    commitments_count: Literal[0] = 0
    observed_holdings_imported: Literal[False] = False

    @field_validator("genesis_at")
    @classmethod
    def require_aware_genesis(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("genesis_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_genesis(self) -> PolicyPortfolioGenesisRecord:
        if self.genesis_at.astimezone(IDX_TIMEZONE).date() != self.genesis_session:
            raise ValueError("genesis time differs from genesis session")
        if self.genesis_at != session_close_at(self.genesis_session):
            raise ValueError("genesis must freeze at IDX session close")
        expected = canonical_policy_artifact_id(
            "PPGEN",
            {
                "protocol_id": self.protocol_id,
                "component_id": self.component_id,
                "manifest_sha256": self.manifest_sha256,
                "baseline_manifest_id": self.baseline_manifest_id,
                "baseline_manifest_sha256": self.baseline_manifest_sha256,
                "policy_sha256": self.policy_sha256,
                "portfolio_policy_sha256": self.portfolio_policy_sha256,
                "fixed_notional_policy_sha256": self.fixed_notional_policy_sha256,
                "trading_calendar_sha256": self.trading_calendar_sha256,
                "genesis_session": self.genesis_session.isoformat(),
                "genesis_at": _utc_iso(self.genesis_at),
            },
        )
        if self.genesis_id != expected:
            raise ValueError("policy-portfolio genesis ID mismatch")
        return self


class PolicyRegimeRecord(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal["shadow-policy-portfolio-regime-v1"] = (
        POLICY_PORTFOLIO_REGIME_VERSION
    )
    regime_record_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    trading_calendar_sha256: Sha256
    observed_session: date
    effective_session: date
    observed_at: datetime
    source_id: NonEmptyString
    source_definition_sha256: Sha256
    source_record_sha256: Sha256
    source_as_of: datetime
    source_expires_at: datetime | None = None
    regime: RegimeName
    reason_codes: tuple[NonEmptyString, ...] = ()

    @field_validator(
        "observed_at",
        "source_as_of",
        "source_expires_at",
    )
    @classmethod
    def require_aware_regime_time(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("regime timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_regime_record(self) -> PolicyRegimeRecord:
        if self.effective_session <= self.observed_session:
            raise ValueError("regime must become effective after observation")
        if self.observed_at != session_close_at(self.observed_session):
            raise ValueError(
                "regime observation must be frozen at observed-session close"
            )
        if self.source_as_of > self.observed_at:
            raise ValueError("regime source vintage follows observation")
        if (
            self.source_expires_at is not None
            and self.source_expires_at <= self.source_as_of
        ):
            raise ValueError("regime expiry must follow source vintage")
        stale = (
            self.source_expires_at is not None
            and self.source_expires_at <= self.observed_at
        )
        if self.regime == "UNKNOWN":
            if not self.reason_codes:
                raise ValueError("UNKNOWN regime requires a reason")
        elif self.reason_codes or stale:
            raise ValueError("estimable regime cannot carry failure reasons")
        expected = canonical_policy_artifact_id(
            "PPREG",
            {
                "protocol_id": self.protocol_id,
                "manifest_sha256": self.manifest_sha256,
                "observed_session": self.observed_session.isoformat(),
                "effective_session": self.effective_session.isoformat(),
                "source_record_sha256": self.source_record_sha256,
                "regime": self.regime,
                "reason_codes": list(self.reason_codes),
            },
        )
        if self.regime_record_id != expected:
            raise ValueError("regime-record ID mismatch")
        return self


class PolicySessionLiquidityRecord(_EvaluationOnlyPolicyArtifact):
    """Causal full-quantity capacity evidence for one possible fill session."""

    contract_version: Literal["shadow-policy-liquidity-session-v1"] = (
        POLICY_PORTFOLIO_LIQUIDITY_VERSION
    )
    record_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    capacity_session: date
    measured_at: datetime
    measurement_source_sha256: Sha256
    bar_record_sha256s: tuple[Sha256, ...] = Field(min_length=20, max_length=20)
    turnover_sum_idr: StrictNonNegativeInt
    adtv20_denominator: Literal[20] = 20
    minimum_adtv20_idr: Literal[10_000_000_000] = 10_000_000_000
    minimum_adtv20_passed: bool
    participation_numerator: Literal[13] = 13
    participation_denominator: Literal[10_000] = 10_000
    capacity_notional_numerator_idr: StrictNonNegativeInt
    capacity_notional_denominator: Literal[200_000] = 200_000
    evidence_role: Literal[
        "POLICY_ENTRY_POINT_IN_TIME_RECHECK"
    ] = "POLICY_ENTRY_POINT_IN_TIME_RECHECK"

    @field_validator("measured_at")
    @classmethod
    def require_aware_liquidity(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("policy liquidity time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_liquidity(self) -> PolicySessionLiquidityRecord:
        if self.measured_at >= _session_open_at(self.capacity_session):
            raise ValueError("policy liquidity must precede capacity-session open")
        if self.capacity_notional_numerator_idr != (
            self.turnover_sum_idr * self.participation_numerator
        ):
            raise ValueError("policy liquidity capacity numerator mismatch")
        if self.minimum_adtv20_passed is not (
            self.turnover_sum_idr
            >= self.minimum_adtv20_idr * self.adtv20_denominator
        ):
            raise ValueError("policy liquidity ADTV classification mismatch")
        expected = canonical_policy_artifact_id(
            "PPLIQ",
            {
                "protocol_id": self.protocol_id,
                "manifest_sha256": self.manifest_sha256,
                "raw_event_id": self.raw_event_id,
                "capacity_session": self.capacity_session.isoformat(),
                "measurement_source_sha256": self.measurement_source_sha256,
                "turnover_sum_idr": self.turnover_sum_idr,
            },
        )
        if self.record_id != expected:
            raise ValueError("policy liquidity record ID mismatch")
        return self

    def supports_gross_notional(self, gross_notional_idr: int) -> bool:
        if type(gross_notional_idr) is not int or gross_notional_idr < 0:
            raise ValueError("gross notional must be strict non-negative IDR")
        return (
            gross_notional_idr * self.capacity_notional_denominator
            <= self.capacity_notional_numerator_idr
        )


class PolicyCandidateClassification(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal[
        "shadow-policy-candidate-classification-v1"
    ] = POLICY_PORTFOLIO_CLASSIFICATION_VERSION
    classification_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    source_id: NonEmptyString
    source_definition_sha256: Sha256
    source_record_sha256: Sha256
    source_as_of: datetime
    source_expires_at: datetime | None = None
    sector_taxonomy_id: NonEmptyString
    sector_id: NonEmptyString | None
    cluster_rule_sha256: Sha256
    cluster_id: NonEmptyString | None
    status: Literal["ESTIMABLE", "NOT_ESTIMABLE"]
    reason_codes: tuple[NonEmptyString, ...] = ()

    @field_validator("source_as_of", "source_expires_at")
    @classmethod
    def require_aware_classification_time(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("classification timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_classification(self) -> PolicyCandidateClassification:
        if (
            self.source_expires_at is not None
            and self.source_expires_at <= self.source_as_of
        ):
            raise ValueError(
                "classification expiry must follow its source vintage"
            )
        complete = self.sector_id is not None and self.cluster_id is not None
        if self.status == "ESTIMABLE":
            if not complete or self.reason_codes:
                raise ValueError("estimable classification is incomplete")
        elif complete or not self.reason_codes:
            raise ValueError("NOT_ESTIMABLE classification shape is invalid")
        expected = canonical_policy_artifact_id(
            "PPCLASS",
            {
                "protocol_id": self.protocol_id,
                "manifest_sha256": self.manifest_sha256,
                "raw_event_id": self.raw_event_id,
                "source_record_sha256": self.source_record_sha256,
                "sector_id": self.sector_id,
                "cluster_id": self.cluster_id,
                "status": self.status,
                "reason_codes": list(self.reason_codes),
            },
        )
        if self.classification_id != expected:
            raise ValueError("classification ID mismatch")
        return self


class PolicyPortfolioCandidateInput(_EvaluationOnlyPolicyArtifact):
    """One exact P2-015 predecessor plus policy-specific causal evidence."""

    contract_version: Literal[
        "shadow-policy-portfolio-candidate-input-v1"
    ] = POLICY_PORTFOLIO_CANDIDATE_INPUT_VERSION
    candidate_input_id: NonEmptyString
    pair_input: FixedNotionalPairInput
    pair_input_sha256: Sha256
    paired_fixed_notional_record: PairedFixedNotionalRecord
    paired_fixed_notional_record_sha256: Sha256
    control_primary_lifecycle_sha256: Sha256
    challenger_primary_lifecycle_sha256: Sha256
    lifecycle_authority: Literal[
        "EVIDENCE_ONLY_NOT_POLICY_CASH_AUTHORITY"
    ] = "EVIDENCE_ONLY_NOT_POLICY_CASH_AUTHORITY"
    classification: PolicyCandidateClassification
    classification_sha256: Sha256
    entry_liquidity: tuple[PolicySessionLiquidityRecord, ...]
    entry_liquidity_sha256s: tuple[Sha256, ...]

    @model_validator(mode="after")
    def verify_candidate_input(self) -> PolicyPortfolioCandidateInput:
        if canonical_sha256(self.pair_input) != self.pair_input_sha256:
            raise ValueError("candidate PairInput hash mismatch")
        if (
            canonical_sha256(self.paired_fixed_notional_record)
            != self.paired_fixed_notional_record_sha256
        ):
            raise ValueError("candidate fixed-notional record hash mismatch")
        if canonical_sha256(self.classification) != self.classification_sha256:
            raise ValueError("candidate classification hash mismatch")
        try:
            verify_paired_fixed_notional_record(
                self.pair_input,
                self.paired_fixed_notional_record,
            )
        except ShadowContractError as exc:
            raise ValueError("fixed-notional predecessor replay failed") from exc
        paired = self.paired_fixed_notional_record
        expected_lifecycle_hashes = (
            canonical_sha256(paired.control),
            canonical_sha256(paired.challenger),
        )
        if expected_lifecycle_hashes != (
            self.control_primary_lifecycle_sha256,
            self.challenger_primary_lifecycle_sha256,
        ):
            raise ValueError("primary lifecycle lineage mismatch")
        observation = self.pair_input.observation
        signal_at = observation.signal_at
        classification_is_causal = (
            self.classification.source_as_of <= signal_at
        )
        classification_is_fresh = (
            self.classification.source_expires_at is None
            or self.classification.source_expires_at > signal_at
        )
        if self.classification.status == "ESTIMABLE" and (
            not classification_is_causal or not classification_is_fresh
        ):
            raise ValueError(
                "estimable classification is not causal and fresh at signal"
            )
        identity = (
            self.classification.protocol_id,
            self.classification.component_id,
            self.classification.manifest_sha256,
            self.classification.raw_event_id,
            self.classification.ticker,
        )
        expected_identity = (
            self.pair_input.manifest.protocol_id,
            self.pair_input.manifest.component_id,
            self.pair_input.manifest_sha256,
            observation.raw_event_id,
            observation.ticker,
        )
        if identity != expected_identity:
            raise ValueError("classification differs from PairInput identity")
        sessions = tuple(item.capacity_session for item in self.entry_liquidity)
        if sessions != tuple(sorted(sessions)) or len(set(sessions)) != len(sessions):
            raise ValueError("policy entry-liquidity sessions must be ordered/unique")
        expected_hashes = tuple(canonical_sha256(item) for item in self.entry_liquidity)
        if expected_hashes != self.entry_liquidity_sha256s:
            raise ValueError("policy entry-liquidity hash sequence mismatch")
        if any(
            (
                item.protocol_id,
                item.component_id,
                item.manifest_sha256,
                item.raw_event_id,
                item.ticker,
            )
            != expected_identity
            for item in self.entry_liquidity
        ):
            raise ValueError("policy liquidity differs from candidate identity")
        expected_id = canonical_policy_artifact_id(
            "PPCAND",
            {
                "pair_input_sha256": self.pair_input_sha256,
                "paired_fixed_notional_record_sha256": (
                    self.paired_fixed_notional_record_sha256
                ),
                "classification_sha256": self.classification_sha256,
                "entry_liquidity_sha256s": list(self.entry_liquidity_sha256s),
            },
        )
        if self.candidate_input_id != expected_id:
            raise ValueError("policy candidate-input ID mismatch")
        return self

    def liquidity_for(self, session: date) -> PolicySessionLiquidityRecord | None:
        return next(
            (item for item in self.entry_liquidity if item.capacity_session == session),
            None,
        )


def canonical_policy_artifact_id(prefix: str, payload: dict[str, object]) -> str:
    if not prefix or not prefix.isascii():
        raise ValueError("policy artifact prefix must be non-empty ASCII")
    return f"{prefix}-{_sha256(_canonical_mapping_bytes(payload))[:24]}"


def _canonical_mapping_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _session_open_at(session: date) -> datetime:
    return datetime.combine(session, SESSION_OPEN, tzinfo=IDX_TIMEZONE)


class PolicySettlementLeg(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal[
        "shadow-policy-portfolio-settlement-leg-v1"
    ] = POLICY_PORTFOLIO_SETTLEMENT_LEG_VERSION
    settlement_leg_id: NonEmptyString
    decision_role: DecisionRole
    leg_type: Literal["PURCHASE_PAYABLE", "SALE_RECEIVABLE"]
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    source_event_id: NonEmptyString
    source_event_sha256: Sha256
    trade_session: date
    settlement_session: date
    amount_idr: StrictPositiveInt

    @model_validator(mode="after")
    def verify_settlement_leg(self) -> PolicySettlementLeg:
        if self.settlement_session <= self.trade_session:
            raise ValueError("settlement must follow trade session")
        expected = canonical_policy_artifact_id(
            "PPLEG",
            {
                "decision_role": self.decision_role,
                "leg_type": self.leg_type,
                "raw_event_id": self.raw_event_id,
                "source_event_sha256": self.source_event_sha256,
                "trade_session": self.trade_session.isoformat(),
                "settlement_session": self.settlement_session.isoformat(),
                "amount_idr": self.amount_idr,
            },
        )
        if self.settlement_leg_id != expected:
            raise ValueError("settlement-leg ID mismatch")
        return self


class PolicyCommitment(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal[
        "shadow-policy-portfolio-commitment-v1"
    ] = POLICY_PORTFOLIO_COMMITMENT_VERSION
    commitment_id: NonEmptyString
    decision_role: DecisionRole
    candidate_input_id: NonEmptyString
    candidate_input_sha256: Sha256
    pair_input_sha256: Sha256
    decision_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    source_row_number: StrictPositiveInt
    recorded_rank: StrictPositiveInt
    priority_key: tuple[StrictPositiveInt, StrictPositiveInt, CanonicalTicker]
    sector_id: NonEmptyString
    cluster_id: NonEmptyString
    signal_session: date
    created_session: date
    activation_sessions: tuple[date, ...] = Field(min_length=1)
    requested_position_fraction: StrictFloat
    requested_notional_idr: StrictPositiveInt
    planned_entry_high_idr: StrictPositiveInt
    planned_stop_idr: StrictPositiveInt
    planned_target_idr: StrictPositiveInt
    planned_lots: StrictPositiveInt
    quantity_shares: StrictPositiveInt
    planned_gross_idr: StrictPositiveInt
    planned_entry_cost_idr: StrictNonNegativeInt
    reserved_debit_idr: StrictPositiveInt
    planned_risk_idr: StrictPositiveInt

    @field_validator("requested_position_fraction", mode="before")
    @classmethod
    def require_strict_fraction(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("requested position fraction must be strict float")
        return value

    @model_validator(mode="after")
    def verify_commitment(self) -> PolicyCommitment:
        if self.priority_key != (
            self.recorded_rank,
            self.source_row_number,
            self.ticker,
        ):
            raise ValueError("commitment priority key mismatch")
        if self.activation_sessions != tuple(sorted(self.activation_sessions)):
            raise ValueError("activation sessions must be ordered")
        if len(set(self.activation_sessions)) != len(self.activation_sessions):
            raise ValueError("activation sessions must be unique")
        if self.created_session != self.signal_session:
            raise ValueError("commitment must be born on signal session")
        if any(item <= self.signal_session for item in self.activation_sessions):
            raise ValueError("activation session must follow signal")
        if self.planned_stop_idr >= self.planned_entry_high_idr:
            raise ValueError("planned stop must be below entry high")
        if self.planned_target_idr <= self.planned_entry_high_idr:
            raise ValueError("planned target must exceed entry high")
        if self.quantity_shares != self.planned_lots * 100:
            raise ValueError("commitment quantity differs from board lots")
        if self.planned_gross_idr != (
            self.quantity_shares * self.planned_entry_high_idr
        ):
            raise ValueError("commitment gross arithmetic mismatch")
        if self.planned_gross_idr > self.requested_notional_idr:
            raise ValueError("commitment gross exceeds requested notional")
        if self.reserved_debit_idr != (
            self.planned_gross_idr + self.planned_entry_cost_idr
        ):
            raise ValueError("commitment reservation arithmetic mismatch")
        if self.planned_risk_idr != self.quantity_shares * (
            self.planned_entry_high_idr - self.planned_stop_idr
        ):
            raise ValueError("commitment planned-risk arithmetic mismatch")
        if self.requested_position_fraction != quantize_ratio_decimal(
            Decimal(str(self.requested_position_fraction))
        ):
            raise ValueError("commitment fraction is not pre-quantized")
        expected = canonical_policy_artifact_id(
            "PPCOM",
            {
                "decision_role": self.decision_role,
                "candidate_input_sha256": self.candidate_input_sha256,
                "decision_sha256": self.decision_sha256,
                "created_session": self.created_session.isoformat(),
                "priority_key": list(self.priority_key),
                "planned_gross_idr": self.planned_gross_idr,
                "reserved_debit_idr": self.reserved_debit_idr,
                "planned_risk_idr": self.planned_risk_idr,
            },
        )
        if self.commitment_id != expected:
            raise ValueError("commitment ID mismatch")
        return self


class PolicyPosition(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal[
        "shadow-policy-portfolio-position-v1"
    ] = POLICY_PORTFOLIO_POSITION_VERSION
    position_id: NonEmptyString
    decision_role: DecisionRole
    originating_commitment_id: NonEmptyString
    originating_commitment_sha256: Sha256
    candidate_input_id: NonEmptyString
    candidate_input_sha256: Sha256
    pair_input_sha256: Sha256
    decision_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    sector_id: NonEmptyString
    cluster_id: NonEmptyString
    opened_session: date
    opened_at: datetime
    entry_price_idr: StrictPositiveInt
    entry_quantity_shares: StrictPositiveInt
    current_quantity_shares: StrictPositiveInt
    entry_gross_idr: StrictPositiveInt
    entry_cost_idr: StrictNonNegativeInt
    cost_basis_idr: StrictPositiveInt
    planned_entry_high_idr: StrictPositiveInt
    planned_stop_idr: StrictPositiveInt
    planned_target_idr: StrictPositiveInt
    planned_risk_idr: StrictPositiveInt
    post_fill_sessions_observed: StrictNonNegativeInt = 0
    last_mark_session: date
    last_mark_price_idr: StrictPositiveInt
    last_verified_at: datetime

    @field_validator("opened_at", "last_verified_at")
    @classmethod
    def require_aware_position_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("position times must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_position(self) -> PolicyPosition:
        if self.opened_at.astimezone(IDX_TIMEZONE).date() != self.opened_session:
            raise ValueError("position open time differs from session")
        if self.last_mark_session < self.opened_session:
            raise ValueError("position mark precedes opening")
        if self.last_verified_at.astimezone(IDX_TIMEZONE).date() < (
            self.last_mark_session
        ):
            raise ValueError("position verification precedes mark")
        if self.entry_gross_idr != (
            self.entry_price_idr * self.entry_quantity_shares
        ):
            raise ValueError("position entry-gross arithmetic mismatch")
        if self.cost_basis_idr != self.entry_gross_idr + self.entry_cost_idr:
            raise ValueError("position cost-basis arithmetic mismatch")
        if self.planned_risk_idr != self.current_quantity_shares * (
            self.planned_entry_high_idr - self.planned_stop_idr
        ):
            raise ValueError("position planned-risk arithmetic mismatch")
        expected = canonical_policy_artifact_id(
            "PPPOS",
            {
                "decision_role": self.decision_role,
                "originating_commitment_sha256": (
                    self.originating_commitment_sha256
                ),
                "candidate_input_sha256": self.candidate_input_sha256,
                "decision_sha256": self.decision_sha256,
                "opened_session": self.opened_session.isoformat(),
                "entry_price_idr": self.entry_price_idr,
                "entry_quantity_shares": self.entry_quantity_shares,
            },
        )
        if self.position_id != expected:
            raise ValueError("position ID mismatch")
        return self

    @property
    def marked_value_idr(self) -> int:
        return self.current_quantity_shares * self.last_mark_price_idr


class PolicyTickerReentryBlock(_PolicyModel):
    ticker: CanonicalTicker
    exit_session: date


class PolicyGateResult(_PolicyModel):
    gate_index: int = Field(ge=1, le=10)
    gate_id: NonEmptyString
    passed: bool
    reason_codes: tuple[NonEmptyString, ...]
    observed_integer_idr: StrictInt | None = None
    threshold_integer_idr: StrictInt | None = None
    observed_ratio: StrictFloat | None = None
    threshold_ratio: StrictFloat | None = None

    @field_validator("observed_ratio", "threshold_ratio", mode="before")
    @classmethod
    def require_strict_ratio(cls, value: object) -> object:
        if value is not None and type(value) is not float:
            raise ValueError("gate ratios must be strict floats")
        return value

    @model_validator(mode="after")
    def verify_gate_result(self) -> PolicyGateResult:
        if self.gate_id != GATE_ORDER[self.gate_index - 1]:
            raise ValueError("gate result differs from exact PP5 order")
        if self.passed and self.reason_codes:
            raise ValueError("passed gate cannot carry failure reasons")
        if not self.passed and not self.reason_codes:
            raise ValueError("failed gate requires a reason")
        for ratio in (self.observed_ratio, self.threshold_ratio):
            if ratio is not None and ratio != quantize_ratio_decimal(
                Decimal(str(ratio))
            ):
                raise ValueError("gate ratio is not pre-quantized")
        return self


PolicyEventType: TypeAlias = Literal[
    "ADMISSION_ACCEPTED",
    "ADMISSION_REJECTED",
    "COMMITMENT_CANCELED",
    "COMMITMENT_EXPIRED",
    "CORPORATE_ACTION_SPLIT",
    "DAILY_STOP_LATCHED",
    "DIVIDEND_CREDIT",
    "ENTRY_FILLED",
    "EXIT_FILLED",
    "MARK_UPDATED",
    "PATH_NOT_ESTIMABLE",
    "REGIME_APPLIED",
    "SETTLEMENT_POSTED",
]


class PolicyPortfolioTransitionEvent(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal["shadow-policy-portfolio-event-v1"] = (
        POLICY_PORTFOLIO_EVENT_VERSION
    )
    event_id: NonEmptyString
    decision_role: DecisionRole
    session: date
    event_sequence: StrictPositiveInt
    event_type: PolicyEventType
    occurred_at: datetime
    ticker: CanonicalTicker | None = None
    candidate_input_sha256: Sha256 | None = None
    source_artifact_sha256: Sha256
    gate_results: tuple[PolicyGateResult, ...] = ()
    reason_codes: tuple[NonEmptyString, ...] = ()
    settled_cash_delta_idr: StrictInt = 0
    sale_receivable_delta_idr: StrictInt = 0
    purchase_payable_delta_idr: StrictInt = 0
    reserved_cash_delta_idr: StrictInt = 0
    realized_pnl_delta_idr: StrictInt = 0
    planned_risk_delta_idr: StrictInt = 0
    gross_exposure_delta_idr: StrictInt = 0
    quantity_delta_shares: StrictInt = 0

    @field_validator("occurred_at")
    @classmethod
    def require_aware_event_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("transition event time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_event(self) -> PolicyPortfolioTransitionEvent:
        if self.occurred_at.astimezone(IDX_TIMEZONE).date() != self.session:
            raise ValueError("transition event time differs from session")
        if self.gate_results:
            indices = tuple(item.gate_index for item in self.gate_results)
            if indices != tuple(range(1, 11)):
                raise ValueError("admission event must carry all ten gates")
        if self.event_type == "ADMISSION_ACCEPTED":
            if not self.gate_results or any(
                not item.passed for item in self.gate_results
            ):
                raise ValueError("accepted admission must pass all gates")
        if self.event_type == "ADMISSION_REJECTED":
            if not self.gate_results or all(
                item.passed for item in self.gate_results
            ):
                raise ValueError("rejected admission needs a failed gate")
        expected = canonical_policy_artifact_id(
            "PPEVT",
            {
                "decision_role": self.decision_role,
                "session": self.session.isoformat(),
                "event_sequence": self.event_sequence,
                "event_type": self.event_type,
                "occurred_at": _utc_iso(self.occurred_at),
                "ticker": self.ticker,
                "candidate_input_sha256": self.candidate_input_sha256,
                "source_artifact_sha256": self.source_artifact_sha256,
                "gate_result_sha256s": [
                    canonical_sha256(item) for item in self.gate_results
                ],
                "reason_codes": list(self.reason_codes),
                "money_deltas": [
                    self.settled_cash_delta_idr,
                    self.sale_receivable_delta_idr,
                    self.purchase_payable_delta_idr,
                    self.reserved_cash_delta_idr,
                    self.realized_pnl_delta_idr,
                    self.planned_risk_delta_idr,
                    self.gross_exposure_delta_idr,
                    self.quantity_delta_shares,
                ],
            },
        )
        if self.event_id != expected:
            raise ValueError("transition-event ID mismatch")
        return self


class PolicyPortfolioStatePayload(_PolicyModel):
    """Economic state payload kept separate to avoid transition/state cycles."""

    contract_version: Literal["shadow-policy-portfolio-payload-v1"] = (
        POLICY_PORTFOLIO_PAYLOAD_VERSION
    )
    session: date
    state_as_of: datetime
    effective_regime: RegimeName
    path_status: PathStatus
    path_reason_codes: tuple[NonEmptyString, ...] = ()
    settled_cash_idr: StrictNonNegativeInt
    sale_receivables: tuple[PolicySettlementLeg, ...] = ()
    purchase_payables: tuple[PolicySettlementLeg, ...] = ()
    commitments: tuple[PolicyCommitment, ...] = ()
    positions: tuple[PolicyPosition, ...] = ()
    reentry_blocks: tuple[PolicyTickerReentryBlock, ...] = ()
    realized_pnl_today_idr: StrictInt
    daily_stop_latched: bool
    sale_receivable_idr: StrictNonNegativeInt
    purchase_payable_idr: StrictNonNegativeInt
    reserved_cash_idr: StrictNonNegativeInt
    marked_holdings_value_idr: StrictNonNegativeInt
    accounting_equity_idr: StrictInt
    gross_exposure_idr: StrictNonNegativeInt
    planned_risk_idr: StrictNonNegativeInt
    portfolio_heat: StrictFloat
    realized_loss_fraction: StrictFloat
    buying_power_idr: StrictNonNegativeInt
    deployable_cash_above_minimum_idr: StrictNonNegativeInt
    nav_metric_status: Literal[
        "NOT_ESTIMABLE_UNTIL_RS_P2_017"
    ] = POLICY_PORTFOLIO_NAV_STATUS
    nav_metric_value: None = None

    @field_validator("state_as_of")
    @classmethod
    def require_aware_state_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("state_as_of must be timezone-aware")
        return value

    @field_validator("portfolio_heat", "realized_loss_fraction", mode="before")
    @classmethod
    def require_strict_payload_ratio(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("state ratios must be strict floats")
        return value

    @model_validator(mode="after")
    def verify_payload(self) -> PolicyPortfolioStatePayload:
        if self.state_as_of.astimezone(IDX_TIMEZONE).date() != self.session:
            raise ValueError("state timestamp differs from session")
        if self.path_status == "ACTIVE":
            if self.path_reason_codes:
                raise ValueError("active path cannot carry failure reasons")
        elif not self.path_reason_codes:
            raise ValueError("not-estimable path requires a reason")
        if self.sale_receivables != tuple(
            sorted(
                self.sale_receivables,
                key=lambda item: (
                    item.settlement_session,
                    item.ticker,
                    item.settlement_leg_id,
                ),
            )
        ):
            raise ValueError("sale receivables must be canonically ordered")
        if self.purchase_payables != tuple(
            sorted(
                self.purchase_payables,
                key=lambda item: (
                    item.settlement_session,
                    item.ticker,
                    item.settlement_leg_id,
                ),
            )
        ):
            raise ValueError("purchase payables must be canonically ordered")
        if self.commitments != tuple(
            sorted(self.commitments, key=lambda item: item.priority_key)
        ):
            raise ValueError("commitments must use PP3 priority order")
        if self.positions != tuple(
            sorted(self.positions, key=lambda item: (item.ticker, item.position_id))
        ):
            raise ValueError("positions must be ordered by ticker and ID")
        if self.reentry_blocks != tuple(
            sorted(self.reentry_blocks, key=lambda item: item.ticker)
        ):
            raise ValueError("reentry blocks must be ordered by ticker")
        tickers = tuple(
            item.ticker for item in (*self.positions, *self.commitments)
        )
        if len(tickers) != len(set(tickers)):
            raise ValueError("one ticker cannot be both open and pending")
        if len({item.position_id for item in self.positions}) != len(
            self.positions
        ):
            raise ValueError("position IDs must be unique")
        if len({item.commitment_id for item in self.commitments}) != len(
            self.commitments
        ):
            raise ValueError("commitment IDs must be unique")
        if len({item.settlement_leg_id for item in self.sale_receivables}) != len(
            self.sale_receivables
        ):
            raise ValueError("sale-receivable IDs must be unique")
        if len(
            {item.settlement_leg_id for item in self.purchase_payables}
        ) != len(self.purchase_payables):
            raise ValueError("purchase-payable IDs must be unique")

        expected_sale = sum(item.amount_idr for item in self.sale_receivables)
        expected_purchase = sum(
            item.amount_idr for item in self.purchase_payables
        )
        expected_reserved = sum(
            item.reserved_debit_idr for item in self.commitments
        )
        expected_marked = sum(item.marked_value_idr for item in self.positions)
        expected_gross = expected_marked + sum(
            item.planned_gross_idr for item in self.commitments
        )
        expected_risk = sum(
            item.planned_risk_idr
            for item in (*self.positions, *self.commitments)
        )
        if self.sale_receivable_idr != expected_sale:
            raise ValueError("sale-receivable total mismatch")
        if self.purchase_payable_idr != expected_purchase:
            raise ValueError("purchase-payable total mismatch")
        if self.reserved_cash_idr != expected_reserved:
            raise ValueError("reserved-cash total mismatch")
        if self.marked_holdings_value_idr != expected_marked:
            raise ValueError("marked-holdings total mismatch")
        if self.gross_exposure_idr != expected_gross:
            raise ValueError(
                "gross exposure differs from holdings plus commitments"
            )
        if self.planned_risk_idr != expected_risk:
            raise ValueError("planned-risk total mismatch")
        expected_equity = (
            self.settled_cash_idr
            + self.sale_receivable_idr
            - self.purchase_payable_idr
            + self.marked_holdings_value_idr
        )
        if self.accounting_equity_idr != expected_equity:
            raise ValueError("point-in-time accounting equity mismatch")
        expected_heat = quantize_ratio(
            self.planned_risk_idr,
            APPROVED_STARTING_CAPITAL_IDR,
        )
        if self.portfolio_heat != expected_heat:
            raise ValueError("portfolio heat does not use starting capital")
        expected_loss = quantize_ratio(
            max(-self.realized_pnl_today_idr, 0),
            APPROVED_STARTING_CAPITAL_IDR,
        )
        if self.realized_loss_fraction != expected_loss:
            raise ValueError("realized-loss ratio mismatch")
        if (
            not self.daily_stop_latched
            and expected_loss >= APPROVED_DAILY_LOSS_STOP_FRACTION
        ):
            raise ValueError("daily-stop failed to latch at threshold")
        expected_buying_power = max(
            self.settled_cash_idr
            - self.purchase_payable_idr
            - self.reserved_cash_idr,
            0,
        )
        if self.buying_power_idr != expected_buying_power:
            raise ValueError("buying-power arithmetic mismatch")
        minimum = int(
            Decimal(APPROVED_STARTING_CAPITAL_IDR)
            * Decimal(str(APPROVED_MINIMUM_CASH_RESERVE_FRACTION))
        )
        if self.deployable_cash_above_minimum_idr != max(
            expected_buying_power - minimum,
            0,
        ):
            raise ValueError("minimum-cash deployability mismatch")
        for ratio in (self.portfolio_heat, self.realized_loss_fraction):
            if ratio != quantize_ratio_decimal(Decimal(str(ratio))):
                raise ValueError("payload ratio is not pre-quantized")
        return self


class PolicyPortfolioSessionState(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal["shadow-policy-portfolio-state-v1"] = (
        POLICY_PORTFOLIO_STATE_VERSION
    )
    state_id: NonEmptyString
    decision_role: DecisionRole
    path_id: NonEmptyString
    state_sequence: StrictNonNegativeInt
    previous_state_sha256: Sha256 | None
    genesis_sha256: Sha256
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    policy_sha256: Sha256
    trading_calendar_sha256: Sha256
    session_input_sha256: Sha256 | None
    transition_sha256: Sha256 | None
    payload: PolicyPortfolioStatePayload
    payload_sha256: Sha256

    @model_validator(mode="after")
    def verify_state(self) -> PolicyPortfolioSessionState:
        if canonical_sha256(self.payload) != self.payload_sha256:
            raise ValueError("state payload hash mismatch")
        if self.state_sequence == 0:
            if any(
                value is not None
                for value in (
                    self.previous_state_sha256,
                    self.session_input_sha256,
                    self.transition_sha256,
                )
            ):
                raise ValueError("genesis state cannot carry transition lineage")
        elif any(
            value is None
            for value in (
                self.previous_state_sha256,
                self.session_input_sha256,
                self.transition_sha256,
            )
        ):
            raise ValueError("successor state requires complete transition lineage")
        expected = canonical_policy_artifact_id(
            "PPSTATE",
            {
                "decision_role": self.decision_role,
                "path_id": self.path_id,
                "state_sequence": self.state_sequence,
                "previous_state_sha256": self.previous_state_sha256,
                "genesis_sha256": self.genesis_sha256,
                "protocol_id": self.protocol_id,
                "manifest_sha256": self.manifest_sha256,
                "policy_sha256": self.policy_sha256,
                "trading_calendar_sha256": self.trading_calendar_sha256,
                "session_input_sha256": self.session_input_sha256,
                "transition_sha256": self.transition_sha256,
                "payload_sha256": self.payload_sha256,
            },
        )
        if self.state_id != expected:
            raise ValueError("policy-portfolio state ID mismatch")
        return self


class PolicyPortfolioSessionInput(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal[
        "shadow-policy-portfolio-session-input-v1"
    ] = POLICY_PORTFOLIO_SESSION_INPUT_VERSION
    session_input_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest: ShadowProtocolManifest
    manifest_sha256: Sha256
    policy: FrozenPolicyPortfolioPolicy
    policy_sha256: Sha256
    portfolio_policy: FrozenPortfolioPolicy
    portfolio_policy_sha256: Sha256
    fixed_notional_policy: FrozenFixedNotionalPolicy
    fixed_notional_policy_sha256: Sha256
    trading_calendar: TradingCalendar
    trading_calendar_sha256: Sha256
    session: date
    previous_control_state: PolicyPortfolioSessionState
    previous_control_state_sha256: Sha256
    previous_challenger_state: PolicyPortfolioSessionState
    previous_challenger_state_sha256: Sha256
    regime: PolicyRegimeRecord
    regime_sha256: Sha256
    opportunity_raw_capture: RawCandidateSetCapture
    opportunity_raw_capture_sha256: Sha256
    opportunity_candidate_set: CandidateSetManifest
    opportunity_candidate_set_sha256: Sha256
    admission_candidate_ids: tuple[NonEmptyString, ...]
    candidates: tuple[PolicyPortfolioCandidateInput, ...]
    candidate_sha256s: tuple[Sha256, ...]
    frozen_at: datetime

    @field_validator("frozen_at")
    @classmethod
    def require_aware_input_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("session input freeze must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_session_input(self) -> PolicyPortfolioSessionInput:
        embedded = (
            canonical_sha256(self.manifest),
            canonical_sha256(self.policy),
            canonical_sha256(self.portfolio_policy),
            canonical_sha256(self.fixed_notional_policy),
        )
        if embedded != (
            self.manifest_sha256,
            self.policy_sha256,
            self.portfolio_policy_sha256,
            self.fixed_notional_policy_sha256,
        ):
            raise ValueError("session input embedded policy/manifest hash mismatch")
        if (
            self.manifest.protocol_id,
            self.manifest.component_id,
        ) != (self.protocol_id, self.component_id):
            raise ValueError("session input manifest identity mismatch")
        try:
            verify_policy_portfolio_policy_binding(
                self.manifest,
                self.portfolio_policy,
                self.fixed_notional_policy,
                self.policy,
            )
        except ShadowContractError as exc:
            raise ValueError("session input policy binding failed") from exc
        if self.session not in self.trading_calendar.sessions:
            raise ValueError("policy session is absent from frozen calendar")
        if self.session > self.manifest.fixed_terminal_date:
            raise ValueError("policy session follows the fixed terminal")
        if self.trading_calendar.calendar_sha256 != self.trading_calendar_sha256:
            raise ValueError("embedded trading-calendar hash mismatch")
        if self.regime.effective_session != self.session:
            raise ValueError("regime record is not effective for session")
        if canonical_sha256(self.regime) != self.regime_sha256:
            raise ValueError("regime record hash mismatch")
        if (
            self.regime.protocol_id,
            self.regime.component_id,
            self.regime.manifest_sha256,
            self.regime.trading_calendar_sha256,
        ) != (
            self.protocol_id,
            self.component_id,
            self.manifest_sha256,
            self.trading_calendar_sha256,
        ):
            raise ValueError("regime lineage differs from session input")
        if session_close_at(self.regime.observed_session) != (
            self.regime.observed_at
        ):
            raise ValueError("regime observation is not frozen at session close")
        state_hashes = (
            canonical_sha256(self.previous_control_state),
            canonical_sha256(self.previous_challenger_state),
        )
        if state_hashes != (
            self.previous_control_state_sha256,
            self.previous_challenger_state_sha256,
        ):
            raise ValueError("previous-state hash mismatch")
        control = self.previous_control_state
        challenger = self.previous_challenger_state
        if (
            control.decision_role,
            challenger.decision_role,
        ) != ("CONTROL", "CHALLENGER"):
            raise ValueError("session input state roles are reversed")
        common_state = (
            control.protocol_id,
            control.component_id,
            control.manifest_sha256,
            control.policy_sha256,
            control.trading_calendar_sha256,
            control.genesis_sha256,
            control.state_sequence,
        )
        if common_state != (
            challenger.protocol_id,
            challenger.component_id,
            challenger.manifest_sha256,
            challenger.policy_sha256,
            challenger.trading_calendar_sha256,
            challenger.genesis_sha256,
            challenger.state_sequence,
        ):
            raise ValueError("paired predecessor state common lineage differs")
        if common_state[:5] != (
            self.protocol_id,
            self.component_id,
            self.manifest_sha256,
            self.policy_sha256,
            self.trading_calendar_sha256,
        ):
            raise ValueError("previous states differ from session identity")
        previous_sessions = (
            control.payload.session,
            challenger.payload.session,
        )
        try:
            session_index = self.trading_calendar.sessions.index(self.session)
        except ValueError as exc:  # pragma: no cover - guarded above
            raise ValueError("session is absent from calendar") from exc
        if session_index == 0:
            raise ValueError("successor session needs a prior calendar session")
        expected_previous = self.trading_calendar.sessions[session_index - 1]
        if previous_sessions != (expected_previous, expected_previous):
            raise ValueError("session input does not follow predecessor session")
        if self.regime.observed_session != expected_previous:
            raise ValueError(
                "regime observation is not the immediate prior IDX session"
            )
        if self.regime.observed_at > self.frozen_at:
            raise ValueError("regime observation follows session-input freeze")
        if self.frozen_at < session_close_at(self.session):
            raise ValueError("session input cannot freeze before session close")
        opportunity_hashes = (
            canonical_sha256(self.opportunity_raw_capture),
            canonical_sha256(self.opportunity_candidate_set),
        )
        if opportunity_hashes != (
            self.opportunity_raw_capture_sha256,
            self.opportunity_candidate_set_sha256,
        ):
            raise ValueError("opportunity-set embedded hash mismatch")
        try:
            assert_opportunity_set_parity(
                self.opportunity_candidate_set,
                self.opportunity_raw_capture,
                self.opportunity_candidate_set,
                self.opportunity_raw_capture,
            )
        except ShadowContractError as exc:
            raise ValueError(
                "current-session opportunity-set proof is invalid"
            ) from exc
        opportunity_identity = (
            self.opportunity_candidate_set.protocol_id,
            self.opportunity_candidate_set.component_id,
            self.opportunity_candidate_set.manifest_sha256,
            self.opportunity_candidate_set.trading_calendar_sha256,
            self.opportunity_candidate_set.as_of_date,
            self.opportunity_raw_capture.signal_at.astimezone(
                IDX_TIMEZONE
            ).date(),
        )
        if opportunity_identity != (
            self.protocol_id,
            self.component_id,
            self.manifest_sha256,
            self.trading_calendar_sha256,
            self.session,
            self.session,
        ):
            raise ValueError(
                "opportunity set is not the current frozen session"
            )
        expected_candidate_hashes = tuple(
            canonical_sha256(item) for item in self.candidates
        )
        if expected_candidate_hashes != self.candidate_sha256s:
            raise ValueError("candidate-input hash sequence mismatch")
        raw_events = tuple(
            item.pair_input.observation.raw_event_id for item in self.candidates
        )
        if len(raw_events) != len(set(raw_events)):
            raise ValueError("candidate inputs must have unique raw events")
        if self.candidates != tuple(
            sorted(
                self.candidates,
                key=lambda item: (
                    item.pair_input.observation.signal_at,
                    item.pair_input.candidate.source_row_number or 0,
                    item.pair_input.observation.ticker,
                ),
            )
        ):
            raise ValueError("candidate inputs must use canonical shared order")
        for candidate in self.candidates:
            pair = candidate.pair_input
            if (
                pair.manifest.protocol_id,
                pair.manifest.component_id,
                pair.manifest_sha256,
                pair.trading_calendar.calendar_sha256,
            ) != (
                self.protocol_id,
                self.component_id,
                self.manifest_sha256,
                self.trading_calendar_sha256,
            ):
                raise ValueError("candidate PairInput differs from session lineage")
            nested_times = (
                pair.frozen_at,
                candidate.classification.source_as_of,
                *(
                    item.measured_at
                    for item in candidate.entry_liquidity
                ),
            )
            if any(item > self.frozen_at for item in nested_times):
                raise ValueError(
                    "session input freeze predates candidate evidence"
                )
        if (
            self.opportunity_raw_capture.captured_at > self.frozen_at
            or self.opportunity_candidate_set.captured_at > self.frozen_at
        ):
            raise ValueError(
                "session input freeze predates opportunity-set evidence"
            )
        candidate_by_id = {
            item.candidate_input_id: item for item in self.candidates
        }
        if len(candidate_by_id) != len(self.candidates):
            raise ValueError("candidate-input IDs must be unique")
        required_ids = set(candidate_by_id)
        if self.admission_candidate_ids != tuple(
            sorted(self.admission_candidate_ids)
        ) or len(set(self.admission_candidate_ids)) != len(
            self.admission_candidate_ids
        ):
            raise ValueError(
                "admission candidate IDs must be unique and ordered"
            )
        admission_ids = set(self.admission_candidate_ids)
        if not admission_ids.issubset(required_ids):
            raise ValueError(
                "admission candidate lacks a policy candidate input"
            )
        current_raw_events = {
            item.raw_event_id
            for item in self.opportunity_raw_capture.candidates
        }
        required_decision_events = {
            raw_event_id
            for raw_event_id, control_disposition, challenger_disposition in zip(
                self.opportunity_candidate_set.control_view.input_event_ids,
                self.opportunity_candidate_set.control_view.dispositions,
                self.opportunity_candidate_set.challenger_view.dispositions,
                strict=True,
            )
            if (
                control_disposition.state == "RETAINED"
                or challenger_disposition.state == "RETAINED"
            )
        }
        admission_raw_events: set[str] = set()
        for candidate_id in admission_ids:
            pair = candidate_by_id[candidate_id].pair_input
            if (
                pair.raw_capture_sha256
                != self.opportunity_raw_capture_sha256
                or pair.candidate_set_sha256
                != self.opportunity_candidate_set_sha256
                or pair.observation.raw_event_id not in current_raw_events
            ):
                raise ValueError(
                    "admission candidate differs from current opportunity set"
                )
            admission_raw_events.add(pair.observation.raw_event_id)
        if admission_raw_events != required_decision_events:
            raise ValueError(
                "retained opportunity lacks one paired policy decision input"
            )
        current_pair_ids = {
            item.candidate_input_id
            for item in self.candidates
            if (
                item.pair_input.raw_capture_sha256
                == self.opportunity_raw_capture_sha256
                and item.pair_input.candidate_set_sha256
                == self.opportunity_candidate_set_sha256
            )
        }
        if current_pair_ids != admission_ids:
            raise ValueError(
                "current opportunity candidate inputs differ from admissions"
            )
        active_ids = {
            item.candidate_input_id
            for state in (control, challenger)
            for item in (*state.payload.positions, *state.payload.commitments)
        }
        if not active_ids.issubset(required_ids):
            raise ValueError("active path predecessor lacks candidate input")
        if required_ids != active_ids | admission_ids:
            raise ValueError(
                "session input carries extraneous candidate lineage"
            )
        expected_id = canonical_policy_artifact_id(
            "PPINPUT",
            {
                "protocol_id": self.protocol_id,
                "manifest_sha256": self.manifest_sha256,
                "policy_sha256": self.policy_sha256,
                "session": self.session.isoformat(),
                "previous_control_state_sha256": (
                    self.previous_control_state_sha256
                ),
                "previous_challenger_state_sha256": (
                    self.previous_challenger_state_sha256
                ),
                "regime_sha256": self.regime_sha256,
                "opportunity_raw_capture_sha256": (
                    self.opportunity_raw_capture_sha256
                ),
                "opportunity_candidate_set_sha256": (
                    self.opportunity_candidate_set_sha256
                ),
                "admission_candidate_ids": list(
                    self.admission_candidate_ids
                ),
                "candidate_sha256s": list(self.candidate_sha256s),
                "frozen_at": _utc_iso(self.frozen_at),
            },
        )
        if self.session_input_id != expected_id:
            raise ValueError("policy session-input ID mismatch")
        return self


class PolicyPortfolioSessionTransition(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal[
        "shadow-policy-portfolio-transition-v1"
    ] = POLICY_PORTFOLIO_TRANSITION_VERSION
    transition_id: NonEmptyString
    decision_role: DecisionRole
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    policy_sha256: Sha256
    trading_calendar_sha256: Sha256
    session: date
    session_input_sha256: Sha256
    pre_state_sha256: Sha256
    pre_state_sequence: StrictNonNegativeInt
    events: tuple[PolicyPortfolioTransitionEvent, ...]
    event_sha256s: tuple[Sha256, ...]
    post_state_payload_sha256: Sha256
    transition_status: PathStatus
    reason_codes: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def verify_transition(self) -> PolicyPortfolioSessionTransition:
        sequences = tuple(item.event_sequence for item in self.events)
        if sequences != tuple(range(1, len(self.events) + 1)):
            raise ValueError("transition event sequence is not contiguous")
        timestamps = tuple(item.occurred_at for item in self.events)
        if timestamps != tuple(sorted(timestamps)):
            raise ValueError(
                "transition events are not chronologically ordered"
            )
        if any(
            (item.decision_role, item.session)
            != (self.decision_role, self.session)
            for item in self.events
        ):
            raise ValueError("transition contains cross-side/session event")
        if tuple(canonical_sha256(item) for item in self.events) != (
            self.event_sha256s
        ):
            raise ValueError("transition event-hash sequence mismatch")
        if self.transition_status == "ACTIVE":
            if self.reason_codes:
                raise ValueError("active transition cannot carry failure reasons")
        elif not self.reason_codes:
            raise ValueError("not-estimable transition requires reasons")
        expected = canonical_policy_artifact_id(
            "PPTRANS",
            {
                "decision_role": self.decision_role,
                "protocol_id": self.protocol_id,
                "manifest_sha256": self.manifest_sha256,
                "policy_sha256": self.policy_sha256,
                "session": self.session.isoformat(),
                "session_input_sha256": self.session_input_sha256,
                "pre_state_sha256": self.pre_state_sha256,
                "pre_state_sequence": self.pre_state_sequence,
                "event_sha256s": list(self.event_sha256s),
                "post_state_payload_sha256": self.post_state_payload_sha256,
                "transition_status": self.transition_status,
                "reason_codes": list(self.reason_codes),
            },
        )
        if self.transition_id != expected:
            raise ValueError("policy transition ID mismatch")
        return self


class PairedPolicyPortfolioSessionRecord(_EvaluationOnlyPolicyArtifact):
    contract_version: Literal[
        "shadow-paired-policy-portfolio-session-v1"
    ] = POLICY_PORTFOLIO_PAIRED_SESSION_VERSION
    paired_session_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    policy_sha256: Sha256
    trading_calendar_sha256: Sha256
    session: date
    session_input_id: NonEmptyString
    session_input_sha256: Sha256
    genesis_sha256: Sha256
    control_transition: PolicyPortfolioSessionTransition
    control_transition_sha256: Sha256
    control_state: PolicyPortfolioSessionState
    control_state_sha256: Sha256
    challenger_transition: PolicyPortfolioSessionTransition
    challenger_transition_sha256: Sha256
    challenger_state: PolicyPortfolioSessionState
    challenger_state_sha256: Sha256
    shared_input_parity: Literal[True] = True

    @model_validator(mode="after")
    def verify_paired_session(self) -> PairedPolicyPortfolioSessionRecord:
        hashes = (
            canonical_sha256(self.control_transition),
            canonical_sha256(self.control_state),
            canonical_sha256(self.challenger_transition),
            canonical_sha256(self.challenger_state),
        )
        if hashes != (
            self.control_transition_sha256,
            self.control_state_sha256,
            self.challenger_transition_sha256,
            self.challenger_state_sha256,
        ):
            raise ValueError("paired-session embedded hash mismatch")
        if (
            self.control_transition.decision_role,
            self.control_state.decision_role,
            self.challenger_transition.decision_role,
            self.challenger_state.decision_role,
        ) != ("CONTROL", "CONTROL", "CHALLENGER", "CHALLENGER"):
            raise ValueError("paired-session roles are invalid")
        for transition, state in (
            (self.control_transition, self.control_state),
            (self.challenger_transition, self.challenger_state),
        ):
            if (
                transition.session_input_sha256,
                transition.session,
                transition.post_state_payload_sha256,
                canonical_sha256(transition),
            ) != (
                self.session_input_sha256,
                self.session,
                state.payload_sha256,
                state.transition_sha256,
            ):
                raise ValueError("paired-session transition/state lineage mismatch")
            if state.genesis_sha256 != self.genesis_sha256:
                raise ValueError("paired-session genesis lineage mismatch")
        expected = canonical_policy_artifact_id(
            "PPPAIR",
            {
                "protocol_id": self.protocol_id,
                "manifest_sha256": self.manifest_sha256,
                "policy_sha256": self.policy_sha256,
                "session": self.session.isoformat(),
                "session_input_sha256": self.session_input_sha256,
                "genesis_sha256": self.genesis_sha256,
                "control_transition_sha256": self.control_transition_sha256,
                "control_state_sha256": self.control_state_sha256,
                "challenger_transition_sha256": (
                    self.challenger_transition_sha256
                ),
                "challenger_state_sha256": self.challenger_state_sha256,
            },
        )
        if self.paired_session_id != expected:
            raise ValueError("paired-session ID mismatch")
        return self


POLICY_PORTFOLIO_PARAMETER_SPECS: tuple[
    tuple[str, object, str | None, str],
    ...,
] = (
    (
        "policy_portfolio_contract_version",
        POLICY_PORTFOLIO_POLICY_VERSION,
        None,
        "RS-P2-016 owner-approved contract",
    ),
    (
        "policy_portfolio_capability_status",
        POLICY_PORTFOLIO_CAPABILITY_STATUS,
        None,
        "RS-P2-016 remains not A1-eligible",
    ),
    (
        "policy_portfolio_heat_unit",
        POLICY_PORTFOLIO_HEAT_UNIT,
        None,
        "PP-A1 semantic correction",
    ),
    (
        "policy_portfolio_recorded_size_rule",
        "RECORDED_FRACTION_OF_STARTING_CAPITAL_GROSS_BEFORE_COST",
        None,
        "PP2 owner decision",
    ),
    (
        "policy_portfolio_reservation_rule",
        "COMMITMENT_WORST_CASE_GROSS_PLUS_ENTRY_COST",
        None,
        "PP7 owner decision",
    ),
)
POLICY_PORTFOLIO_PARAMETER_NAMES = frozenset(
    item[0] for item in POLICY_PORTFOLIO_PARAMETER_SPECS
)


def policy_portfolio_manifest_parameters() -> tuple[FrozenParameter, ...]:
    return tuple(
        FrozenParameter(name=name, value=value, unit=unit, source=source)  # type: ignore[arg-type]
        for name, value, unit, source in POLICY_PORTFOLIO_PARAMETER_SPECS
    )


def build_policy_portfolio_policy(
    *,
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    fixed_notional_policy: FrozenFixedNotionalPolicy,
    policy_id: str,
) -> FrozenPolicyPortfolioPolicy:
    """Freeze PP1-PP14 without embedding the manifest that will bind it."""

    manifest = _revalidate(ShadowProtocolManifest, manifest)
    portfolio_policy = verify_portfolio_manifest_binding(
        manifest,
        portfolio_policy,
    )
    fixed_notional_policy = verify_fixed_notional_policy_binding(
        manifest,
        portfolio_policy,
        fixed_notional_policy,
    )
    return FrozenPolicyPortfolioPolicy(
        policy_id=policy_id,
        portfolio_policy_id=portfolio_policy.policy_id,
        portfolio_policy_sha256=_required_hash(portfolio_policy),
        fixed_notional_policy_id=fixed_notional_policy.policy_id,
        fixed_notional_policy_sha256=_required_hash(fixed_notional_policy),
        label_definition_sha256=_required_hash(manifest.labels),
        cost_assumptions_sha256=_required_hash(manifest.costs),
        trading_calendar_sha256=manifest.trading_calendar_sha256,
        corporate_action_policy_sha256=manifest.corporate_action_policy_sha256,
        methodology_document_sha256=manifest.methodology_document_sha256,
    )


def verify_policy_portfolio_policy_binding(
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    fixed_notional_policy: FrozenFixedNotionalPolicy,
    policy: FrozenPolicyPortfolioPolicy,
) -> FrozenPolicyPortfolioPolicy:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    portfolio_policy = verify_portfolio_manifest_binding(
        manifest,
        portfolio_policy,
    )
    fixed_notional_policy = verify_fixed_notional_policy_binding(
        manifest,
        portfolio_policy,
        fixed_notional_policy,
    )
    policy = _revalidate(FrozenPolicyPortfolioPolicy, policy)
    actual = (
        policy.portfolio_policy_id,
        policy.portfolio_policy_sha256,
        policy.fixed_notional_policy_id,
        policy.fixed_notional_policy_sha256,
        policy.label_definition_sha256,
        policy.cost_assumptions_sha256,
        policy.trading_calendar_sha256,
        policy.corporate_action_policy_sha256,
        policy.methodology_document_sha256,
    )
    expected = (
        portfolio_policy.policy_id,
        _required_hash(portfolio_policy),
        fixed_notional_policy.policy_id,
        _required_hash(fixed_notional_policy),
        _required_hash(manifest.labels),
        _required_hash(manifest.costs),
        manifest.trading_calendar_sha256,
        manifest.corporate_action_policy_sha256,
        manifest.methodology_document_sha256,
    )
    if actual != expected:
        raise ShadowContractError(
            "policy-portfolio policy differs from frozen predecessors"
        )
    policy_hash = _required_hash(policy)
    for side, content_hashes in (
        ("control", manifest.control_content_hashes),
        ("challenger", manifest.challenger_content_hashes),
    ):
        matches = tuple(
            item
            for item in content_hashes
            if item.role == "CONFIG"
            and item.path == POLICY_PORTFOLIO_CONFIG_PATH
            and item.sha256 == policy_hash
        )
        if len(matches) != 1:
            raise ShadowContractError(
                f"{side} must bind exactly one policy-portfolio CONFIG hash"
            )
    actual_parameters = {item.name: item for item in manifest.thresholds}
    for expected_parameter in policy_portfolio_manifest_parameters():
        actual_parameter = actual_parameters.get(expected_parameter.name)
        if actual_parameter is None:
            raise ShadowContractError(
                "manifest is missing policy-portfolio parameter "
                f"{expected_parameter.name}"
            )
        if (
            type(actual_parameter.value) is not type(expected_parameter.value)
            or actual_parameter.value != expected_parameter.value
            or actual_parameter.unit != expected_parameter.unit
        ):
            raise ShadowContractError(
                "policy-portfolio parameter differs from frozen policy: "
                f"{expected_parameter.name}"
            )
    # PP-A1 must also remain true in the parent profile.
    parent = {
        item.name: item for item in portfolio_manifest_parameters(portfolio_policy)
    }
    heat = parent["max_portfolio_heat_fraction"]
    if heat.unit != POLICY_PORTFOLIO_HEAT_UNIT:
        raise ShadowContractError(
            "parent manifest heat unit is not fraction_of_starting_capital"
        )
    return policy


def build_policy_regime_record(
    *,
    manifest: ShadowProtocolManifest,
    trading_calendar: TradingCalendar,
    observed_session: date,
    observed_at: datetime,
    source_id: str,
    source_definition_sha256: str,
    source_record_sha256: str,
    source_as_of: datetime,
    source_expires_at: datetime | None,
    regime: RegimeName,
    reason_codes: Sequence[str] = (),
) -> PolicyRegimeRecord:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    trading_calendar = _revalidate(TradingCalendar, trading_calendar)
    _verify_calendar_binding(manifest, trading_calendar)
    try:
        index = trading_calendar.sessions.index(observed_session)
        effective_session = trading_calendar.sessions[index + 1]
    except (ValueError, IndexError) as exc:
        raise ShadowContractError(
            "regime observation lacks a next frozen session"
        ) from exc
    values = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": _required_hash(manifest),
        "trading_calendar_sha256": trading_calendar.calendar_sha256,
        "observed_session": observed_session,
        "effective_session": effective_session,
        "observed_at": observed_at,
        "source_id": source_id,
        "source_definition_sha256": source_definition_sha256,
        "source_record_sha256": source_record_sha256,
        "source_as_of": source_as_of,
        "source_expires_at": source_expires_at,
        "regime": regime,
        "reason_codes": tuple(reason_codes),
    }
    record_id = canonical_policy_artifact_id(
        "PPREG",
        {
            "protocol_id": manifest.protocol_id,
            "manifest_sha256": _required_hash(manifest),
            "observed_session": observed_session.isoformat(),
            "effective_session": effective_session.isoformat(),
            "source_record_sha256": source_record_sha256,
            "regime": regime,
            "reason_codes": list(reason_codes),
        },
    )
    try:
        return PolicyRegimeRecord(regime_record_id=record_id, **values)
    except ValueError as exc:
        raise ShadowContractError("policy regime record is invalid") from exc


def build_policy_session_liquidity_record(
    *,
    manifest: ShadowProtocolManifest,
    pair_input: FixedNotionalPairInput,
    measurement: FixedNotionalLiquidityMeasurement,
) -> PolicySessionLiquidityRecord:
    """Rebind exact rational capacity evidence to policy entry recheck."""

    manifest = _revalidate(ShadowProtocolManifest, manifest)
    pair_input = _revalidate(FixedNotionalPairInput, pair_input)
    measurement = _revalidate(FixedNotionalLiquidityMeasurement, measurement)
    _verify_pair_manifest(manifest, pair_input)
    if measurement.measurement_role != "ENTRY":
        raise ShadowContractError(
            "policy entry-capacity evidence must use an ENTRY measurement"
        )
    values = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": _required_hash(manifest),
        "raw_event_id": pair_input.observation.raw_event_id,
        "ticker": pair_input.observation.ticker,
        "capacity_session": measurement.capacity_session,
        "measured_at": measurement.measured_at,
        "measurement_source_sha256": _required_hash(measurement),
        "bar_record_sha256s": measurement.bar_record_sha256s,
        "turnover_sum_idr": measurement.turnover_sum_idr,
        "minimum_adtv20_passed": measurement.minimum_adtv20_passed,
        "capacity_notional_numerator_idr": (
            measurement.capacity_notional_numerator_idr
        ),
    }
    record_id = canonical_policy_artifact_id(
        "PPLIQ",
        {
            "protocol_id": manifest.protocol_id,
            "manifest_sha256": _required_hash(manifest),
            "raw_event_id": pair_input.observation.raw_event_id,
            "capacity_session": measurement.capacity_session.isoformat(),
            "measurement_source_sha256": _required_hash(measurement),
            "turnover_sum_idr": measurement.turnover_sum_idr,
        },
    )
    try:
        return PolicySessionLiquidityRecord(record_id=record_id, **values)
    except ValueError as exc:
        raise ShadowContractError(
            "policy session-liquidity record is invalid"
        ) from exc


def build_policy_candidate_classification(
    *,
    manifest: ShadowProtocolManifest,
    pair_input: FixedNotionalPairInput,
    source_id: str,
    source_definition_sha256: str,
    source_record_sha256: str,
    source_as_of: datetime,
    source_expires_at: datetime | None,
    sector_taxonomy_id: str,
    sector_id: str | None,
    cluster_id: str | None,
    reason_codes: Sequence[str] = (),
) -> PolicyCandidateClassification:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    pair_input = _revalidate(FixedNotionalPairInput, pair_input)
    _verify_pair_manifest(manifest, pair_input)
    normalized_reasons = list(dict.fromkeys(reason_codes))
    signal_at = pair_input.observation.signal_at
    if source_as_of > signal_at:
        normalized_reasons.append("CLASSIFICATION_SOURCE_AFTER_SIGNAL")
    if source_expires_at is not None and source_expires_at <= signal_at:
        normalized_reasons.append("CLASSIFICATION_STALE_AT_SIGNAL")
    normalized_reasons = list(dict.fromkeys(normalized_reasons))
    status: Literal["ESTIMABLE", "NOT_ESTIMABLE"] = (
        "ESTIMABLE"
        if (
            sector_id is not None
            and cluster_id is not None
            and not normalized_reasons
        )
        else "NOT_ESTIMABLE"
    )
    normalized_sector = sector_id if status == "ESTIMABLE" else None
    normalized_cluster = cluster_id if status == "ESTIMABLE" else None
    values = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": _required_hash(manifest),
        "raw_event_id": pair_input.observation.raw_event_id,
        "ticker": pair_input.observation.ticker,
        "source_id": source_id,
        "source_definition_sha256": source_definition_sha256,
        "source_record_sha256": source_record_sha256,
        "source_as_of": source_as_of,
        "source_expires_at": source_expires_at,
        "sector_taxonomy_id": sector_taxonomy_id,
        "sector_id": normalized_sector,
        "cluster_rule_sha256": pair_input.observation.cluster_rule_sha256,
        "cluster_id": normalized_cluster,
        "status": status,
        "reason_codes": tuple(normalized_reasons),
    }
    classification_id = canonical_policy_artifact_id(
        "PPCLASS",
        {
            "protocol_id": manifest.protocol_id,
            "manifest_sha256": _required_hash(manifest),
            "raw_event_id": pair_input.observation.raw_event_id,
            "source_record_sha256": source_record_sha256,
            "sector_id": normalized_sector,
            "cluster_id": normalized_cluster,
            "status": status,
            "reason_codes": normalized_reasons,
        },
    )
    try:
        return PolicyCandidateClassification(
            classification_id=classification_id,
            **values,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "policy candidate classification is invalid"
        ) from exc


def build_policy_portfolio_candidate_input(
    *,
    pair_input: FixedNotionalPairInput,
    paired_fixed_notional_record: PairedFixedNotionalRecord,
    classification: PolicyCandidateClassification,
    entry_liquidity: Sequence[PolicySessionLiquidityRecord],
) -> PolicyPortfolioCandidateInput:
    pair_input = _revalidate(FixedNotionalPairInput, pair_input)
    paired = _revalidate(
        PairedFixedNotionalRecord,
        paired_fixed_notional_record,
    )
    classification = _revalidate(
        PolicyCandidateClassification,
        classification,
    )
    records = tuple(
        sorted(
            (
                _revalidate(PolicySessionLiquidityRecord, item)
                for item in entry_liquidity
            ),
            key=lambda item: item.capacity_session,
        )
    )
    values = {
        "pair_input": pair_input,
        "pair_input_sha256": _required_hash(pair_input),
        "paired_fixed_notional_record": paired,
        "paired_fixed_notional_record_sha256": _required_hash(paired),
        "control_primary_lifecycle_sha256": _required_hash(paired.control),
        "challenger_primary_lifecycle_sha256": _required_hash(
            paired.challenger
        ),
        "classification": classification,
        "classification_sha256": _required_hash(classification),
        "entry_liquidity": records,
        "entry_liquidity_sha256s": tuple(
            _required_hash(item) for item in records
        ),
    }
    candidate_input_id = canonical_policy_artifact_id(
        "PPCAND",
        {
            "pair_input_sha256": values["pair_input_sha256"],
            "paired_fixed_notional_record_sha256": values[
                "paired_fixed_notional_record_sha256"
            ],
            "classification_sha256": values["classification_sha256"],
            "entry_liquidity_sha256s": list(
                values["entry_liquidity_sha256s"]  # type: ignore[arg-type]
            ),
        },
    )
    try:
        return PolicyPortfolioCandidateInput(
            candidate_input_id=candidate_input_id,
            **values,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "policy candidate input is invalid"
        ) from exc


def build_policy_portfolio_genesis(
    *,
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    fixed_notional_policy: FrozenFixedNotionalPolicy,
    policy: FrozenPolicyPortfolioPolicy,
    trading_calendar: TradingCalendar,
    genesis_session: date,
    genesis_at: datetime,
) -> tuple[
    PolicyPortfolioGenesisRecord,
    PolicyPortfolioSessionState,
    PolicyPortfolioSessionState,
]:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    portfolio_policy = _revalidate(FrozenPortfolioPolicy, portfolio_policy)
    fixed_notional_policy = _revalidate(
        FrozenFixedNotionalPolicy,
        fixed_notional_policy,
    )
    policy = verify_policy_portfolio_policy_binding(
        manifest,
        portfolio_policy,
        fixed_notional_policy,
        policy,
    )
    trading_calendar = _revalidate(TradingCalendar, trading_calendar)
    _verify_calendar_binding(manifest, trading_calendar)
    if genesis_session not in trading_calendar.sessions:
        raise ShadowContractError("genesis session is absent from calendar")
    manifest_hash = _required_hash(manifest)
    policy_hash = _required_hash(policy)
    genesis_values = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": manifest_hash,
        "baseline_manifest_id": manifest.baseline_manifest_id,
        "baseline_manifest_sha256": manifest.baseline_manifest_sha256,
        "policy_sha256": policy_hash,
        "portfolio_policy_sha256": _required_hash(portfolio_policy),
        "fixed_notional_policy_sha256": _required_hash(
            fixed_notional_policy
        ),
        "trading_calendar_sha256": trading_calendar.calendar_sha256,
        "genesis_session": genesis_session,
        "genesis_at": genesis_at,
    }
    genesis_id = canonical_policy_artifact_id(
        "PPGEN",
        {
            "protocol_id": manifest.protocol_id,
            "component_id": manifest.component_id,
            "manifest_sha256": manifest_hash,
            "baseline_manifest_id": manifest.baseline_manifest_id,
            "baseline_manifest_sha256": manifest.baseline_manifest_sha256,
            "policy_sha256": policy_hash,
            "portfolio_policy_sha256": _required_hash(portfolio_policy),
            "fixed_notional_policy_sha256": _required_hash(
                fixed_notional_policy
            ),
            "trading_calendar_sha256": trading_calendar.calendar_sha256,
            "genesis_session": genesis_session.isoformat(),
            "genesis_at": _utc_iso(genesis_at),
        },
    )
    try:
        genesis = PolicyPortfolioGenesisRecord(
            genesis_id=genesis_id,
            **genesis_values,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "policy-portfolio genesis is invalid"
        ) from exc
    genesis_hash = _required_hash(genesis)
    payload = _make_payload(
        session=genesis_session,
        state_as_of=genesis_at,
        regime="UNKNOWN",
        path_status="ACTIVE",
        path_reasons=(),
        settled_cash=APPROVED_STARTING_CAPITAL_IDR,
        sale_receivables=(),
        purchase_payables=(),
        commitments=(),
        positions=(),
        reentry_blocks=(),
        realized_pnl=0,
        daily_stop_latched=False,
    )
    control = _make_state(
        role="CONTROL",
        path_id=canonical_policy_artifact_id(
            "PPPATH",
            {"genesis_sha256": genesis_hash, "decision_role": "CONTROL"},
        ),
        sequence=0,
        previous_state_sha256=None,
        genesis_sha256=genesis_hash,
        manifest=manifest,
        policy=policy,
        trading_calendar=trading_calendar,
        session_input_sha256=None,
        transition_sha256=None,
        payload=payload,
    )
    challenger = _make_state(
        role="CHALLENGER",
        path_id=canonical_policy_artifact_id(
            "PPPATH",
            {"genesis_sha256": genesis_hash, "decision_role": "CHALLENGER"},
        ),
        sequence=0,
        previous_state_sha256=None,
        genesis_sha256=genesis_hash,
        manifest=manifest,
        policy=policy,
        trading_calendar=trading_calendar,
        session_input_sha256=None,
        transition_sha256=None,
        payload=payload,
    )
    if control.payload_sha256 != challenger.payload_sha256:
        raise ShadowContractError("paired genesis economic payloads differ")
    return genesis, control, challenger


def build_policy_portfolio_session_input(
    *,
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    fixed_notional_policy: FrozenFixedNotionalPolicy,
    policy: FrozenPolicyPortfolioPolicy,
    trading_calendar: TradingCalendar,
    session: date,
    previous_control_state: PolicyPortfolioSessionState,
    previous_challenger_state: PolicyPortfolioSessionState,
    regime: PolicyRegimeRecord,
    opportunity_raw_capture: RawCandidateSetCapture,
    opportunity_candidate_set: CandidateSetManifest,
    admission_candidates: Sequence[PolicyPortfolioCandidateInput],
    active_candidates: Sequence[PolicyPortfolioCandidateInput],
    frozen_at: datetime,
) -> PolicyPortfolioSessionInput:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    portfolio_policy = _revalidate(FrozenPortfolioPolicy, portfolio_policy)
    fixed_notional_policy = _revalidate(
        FrozenFixedNotionalPolicy,
        fixed_notional_policy,
    )
    policy = verify_policy_portfolio_policy_binding(
        manifest,
        portfolio_policy,
        fixed_notional_policy,
        policy,
    )
    trading_calendar = _revalidate(TradingCalendar, trading_calendar)
    _verify_calendar_binding(manifest, trading_calendar)
    control = _revalidate(
        PolicyPortfolioSessionState,
        previous_control_state,
    )
    challenger = _revalidate(
        PolicyPortfolioSessionState,
        previous_challenger_state,
    )
    regime = _revalidate(PolicyRegimeRecord, regime)
    opportunity_raw_capture = _revalidate(
        RawCandidateSetCapture,
        opportunity_raw_capture,
    )
    opportunity_candidate_set = _revalidate(
        CandidateSetManifest,
        opportunity_candidate_set,
    )
    candidate_map: dict[str, PolicyPortfolioCandidateInput] = {}
    admission_ids: set[str] = set()
    for item in admission_candidates:
        trusted = _revalidate(PolicyPortfolioCandidateInput, item)
        candidate_map[trusted.candidate_input_id] = trusted
        admission_ids.add(trusted.candidate_input_id)
    for item in active_candidates:
        trusted = _revalidate(PolicyPortfolioCandidateInput, item)
        existing = candidate_map.get(trusted.candidate_input_id)
        if existing is not None and _required_hash(existing) != _required_hash(
            trusted
        ):
            raise ShadowContractError(
                "active/admission candidate ID collision"
            )
        candidate_map[trusted.candidate_input_id] = trusted
    ordered_candidates = tuple(
        sorted(
            candidate_map.values(),
            key=lambda item: (
                item.pair_input.observation.signal_at,
                item.pair_input.candidate.source_row_number or 0,
                item.pair_input.observation.ticker,
            ),
        )
    )
    manifest_hash = _required_hash(manifest)
    policy_hash = _required_hash(policy)
    values = {
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest": manifest,
        "manifest_sha256": manifest_hash,
        "policy": policy,
        "policy_sha256": policy_hash,
        "portfolio_policy": portfolio_policy,
        "portfolio_policy_sha256": _required_hash(portfolio_policy),
        "fixed_notional_policy": fixed_notional_policy,
        "fixed_notional_policy_sha256": _required_hash(
            fixed_notional_policy
        ),
        "trading_calendar": trading_calendar,
        "trading_calendar_sha256": trading_calendar.calendar_sha256,
        "session": session,
        "previous_control_state": control,
        "previous_control_state_sha256": _required_hash(control),
        "previous_challenger_state": challenger,
        "previous_challenger_state_sha256": _required_hash(challenger),
        "regime": regime,
        "regime_sha256": _required_hash(regime),
        "opportunity_raw_capture": opportunity_raw_capture,
        "opportunity_raw_capture_sha256": _required_hash(
            opportunity_raw_capture
        ),
        "opportunity_candidate_set": opportunity_candidate_set,
        "opportunity_candidate_set_sha256": _required_hash(
            opportunity_candidate_set
        ),
        "admission_candidate_ids": tuple(sorted(admission_ids)),
        "candidates": ordered_candidates,
        "candidate_sha256s": tuple(
            _required_hash(item) for item in ordered_candidates
        ),
        "frozen_at": frozen_at,
    }
    session_input_id = canonical_policy_artifact_id(
        "PPINPUT",
        {
            "protocol_id": manifest.protocol_id,
            "manifest_sha256": manifest_hash,
            "policy_sha256": policy_hash,
            "session": session.isoformat(),
            "previous_control_state_sha256": _required_hash(control),
            "previous_challenger_state_sha256": _required_hash(challenger),
            "regime_sha256": _required_hash(regime),
            "opportunity_raw_capture_sha256": values[
                "opportunity_raw_capture_sha256"
            ],
            "opportunity_candidate_set_sha256": values[
                "opportunity_candidate_set_sha256"
            ],
            "admission_candidate_ids": list(
                values["admission_candidate_ids"]  # type: ignore[arg-type]
            ),
            "candidate_sha256s": list(values["candidate_sha256s"]),  # type: ignore[arg-type]
            "frozen_at": _utc_iso(frozen_at),
        },
    )
    try:
        return PolicyPortfolioSessionInput(
            session_input_id=session_input_id,
            **values,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "policy-portfolio session input is invalid"
        ) from exc


@dataclass
class _ResourceSnapshot:
    occupied_count: int
    sector_counts: dict[str, int]
    cluster_counts: dict[str, int]
    planned_risk: int
    gross_exposure: int
    accounting_equity: int
    buying_power: int


@dataclass
class _MutablePath:
    role: DecisionRole
    settled_cash: int
    sale_receivables: list[PolicySettlementLeg]
    purchase_payables: list[PolicySettlementLeg]
    commitments: list[PolicyCommitment]
    positions: list[PolicyPosition]
    reentry_blocks: dict[str, date]
    realized_pnl: int
    daily_stop: bool
    path_status: PathStatus
    path_reasons: list[str]
    events: list[PolicyPortfolioTransitionEvent]
    adverse_close_snapshot: _ResourceSnapshot | None

    @classmethod
    def from_state(
        cls,
        role: DecisionRole,
        state: PolicyPortfolioSessionState,
    ) -> _MutablePath:
        payload = state.payload
        return cls(
            role=role,
            settled_cash=payload.settled_cash_idr,
            sale_receivables=list(payload.sale_receivables),
            purchase_payables=list(payload.purchase_payables),
            commitments=list(payload.commitments),
            positions=list(payload.positions),
            reentry_blocks={
                item.ticker: item.exit_session
                for item in payload.reentry_blocks
            },
            realized_pnl=0,
            daily_stop=False,
            path_status=payload.path_status,
            path_reasons=list(payload.path_reason_codes),
            events=[],
            adverse_close_snapshot=None,
        )


def derive_policy_portfolio_session(
    session_input: PolicyPortfolioSessionInput,
) -> PairedPolicyPortfolioSessionRecord:
    """Pure deterministic replay for one frozen IDX session."""

    session_input = _revalidate(
        PolicyPortfolioSessionInput,
        session_input,
    )
    input_hash = _required_hash(session_input)
    control_transition, control_state = _advance_side(
        session_input,
        session_input.previous_control_state,
        "CONTROL",
        input_hash,
    )
    challenger_transition, challenger_state = _advance_side(
        session_input,
        session_input.previous_challenger_state,
        "CHALLENGER",
        input_hash,
    )
    _verify_transition_journal(
        session_input.previous_control_state.payload,
        control_transition,
        control_state.payload,
    )
    _verify_transition_journal(
        session_input.previous_challenger_state.payload,
        challenger_transition,
        challenger_state.payload,
    )
    if _required_hash(session_input) != input_hash:
        raise ShadowContractError(
            "policy-portfolio session input mutated during replay"
        )
    values = {
        "protocol_id": session_input.protocol_id,
        "component_id": session_input.component_id,
        "manifest_sha256": session_input.manifest_sha256,
        "policy_sha256": session_input.policy_sha256,
        "trading_calendar_sha256": session_input.trading_calendar_sha256,
        "session": session_input.session,
        "session_input_id": session_input.session_input_id,
        "session_input_sha256": input_hash,
        "genesis_sha256": control_state.genesis_sha256,
        "control_transition": control_transition,
        "control_transition_sha256": _required_hash(control_transition),
        "control_state": control_state,
        "control_state_sha256": _required_hash(control_state),
        "challenger_transition": challenger_transition,
        "challenger_transition_sha256": _required_hash(
            challenger_transition
        ),
        "challenger_state": challenger_state,
        "challenger_state_sha256": _required_hash(challenger_state),
    }
    paired_id = canonical_policy_artifact_id(
        "PPPAIR",
        {
            "protocol_id": session_input.protocol_id,
            "manifest_sha256": session_input.manifest_sha256,
            "policy_sha256": session_input.policy_sha256,
            "session": session_input.session.isoformat(),
            "session_input_sha256": input_hash,
            "genesis_sha256": control_state.genesis_sha256,
            "control_transition_sha256": values[
                "control_transition_sha256"
            ],
            "control_state_sha256": values["control_state_sha256"],
            "challenger_transition_sha256": values[
                "challenger_transition_sha256"
            ],
            "challenger_state_sha256": values["challenger_state_sha256"],
        },
    )
    try:
        return PairedPolicyPortfolioSessionRecord(
            paired_session_id=paired_id,
            **values,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "paired policy-portfolio session is invalid"
        ) from exc


def verify_policy_portfolio_session(
    session_input: PolicyPortfolioSessionInput,
    record: PairedPolicyPortfolioSessionRecord,
) -> PairedPolicyPortfolioSessionRecord:
    trusted = _revalidate(PairedPolicyPortfolioSessionRecord, record)
    expected = derive_policy_portfolio_session(session_input)
    if _required_hash(trusted) != _required_hash(expected):
        raise ShadowContractError(
            "policy-portfolio record differs from exact replay"
        )
    return trusted


def replay_policy_portfolio_session(
    session_input: PolicyPortfolioSessionInput,
    record: PairedPolicyPortfolioSessionRecord,
) -> PairedPolicyPortfolioSessionRecord:
    return verify_policy_portfolio_session(session_input, record)


def _advance_side(
    session_input: PolicyPortfolioSessionInput,
    previous_state: PolicyPortfolioSessionState,
    role: DecisionRole,
    session_input_sha256: str,
) -> tuple[
    PolicyPortfolioSessionTransition,
    PolicyPortfolioSessionState,
]:
    mutable = _MutablePath.from_state(role, previous_state)
    session = session_input.session
    candidates = {
        item.candidate_input_id: item for item in session_input.candidates
    }
    _append_event(
        mutable,
        session=session,
        event_type="REGIME_APPLIED",
        occurred_at=_session_open_at(session),
        source_artifact_sha256=session_input.regime_sha256,
        reason_codes=(
            ("NOT_ESTIMABLE_REGIME",)
            if session_input.regime.regime == "UNKNOWN"
            else ()
        ),
    )
    if mutable.path_status == "ACTIVE":
        _post_due_settlements(mutable, session)
    if mutable.path_status == "ACTIVE":
        _apply_opening_corporate_actions(
            mutable,
            session_input,
            candidates,
        )
    if mutable.path_status == "ACTIVE":
        _cancel_for_regime_reduction(
            mutable,
            session_input,
        )
    if mutable.path_status == "ACTIVE":
        _process_exits(
            mutable,
            session_input,
            candidates,
            phase="OPEN",
        )
    if mutable.path_status == "ACTIVE":
        _process_fills(
            mutable,
            session_input,
            candidates,
            phase="OPEN",
        )
    mutable.adverse_close_snapshot = _resource_snapshot(mutable)
    if mutable.path_status == "ACTIVE":
        _process_exits(
            mutable,
            session_input,
            candidates,
            phase="SESSION",
        )
    if mutable.path_status == "ACTIVE":
        _process_dividends(
            mutable,
            session_input,
            candidates,
        )
    if mutable.path_status == "ACTIVE":
        _process_fills(
            mutable,
            session_input,
            candidates,
            phase="SESSION",
        )
    if mutable.path_status == "ACTIVE":
        _expire_or_cancel_commitments(mutable, session_input)
    if mutable.path_status == "ACTIVE":
        _admit_new_signals(mutable, session_input)
    if mutable.path_status == "ACTIVE":
        _mark_remaining_positions(mutable, session_input, candidates)
    if (
        session == session_input.manifest.fixed_terminal_date
        and mutable.positions
        and mutable.path_status == "ACTIVE"
    ):
        _poison_path(
            mutable,
            session=session,
            reason="UNRESOLVED_AT_FIXED_TERMINAL",
            source_sha256=session_input.manifest_sha256,
        )
    state_as_of = max(
        (session_close_at(session), *(item.occurred_at for item in mutable.events))
    )
    payload = _make_payload(
        session=session,
        state_as_of=state_as_of,
        regime=session_input.regime.regime,
        path_status=mutable.path_status,
        path_reasons=tuple(dict.fromkeys(mutable.path_reasons)),
        settled_cash=mutable.settled_cash,
        sale_receivables=tuple(mutable.sale_receivables),
        purchase_payables=tuple(mutable.purchase_payables),
        commitments=tuple(mutable.commitments),
        positions=tuple(mutable.positions),
        reentry_blocks=tuple(
            PolicyTickerReentryBlock(ticker=ticker, exit_session=exit_session)
            for ticker, exit_session in sorted(mutable.reentry_blocks.items())
        ),
        realized_pnl=mutable.realized_pnl,
        daily_stop_latched=mutable.daily_stop,
    )
    payload_hash = _required_hash(payload)
    events = tuple(mutable.events)
    event_hashes = tuple(_required_hash(item) for item in events)
    transition_values = {
        "decision_role": role,
        "protocol_id": session_input.protocol_id,
        "component_id": session_input.component_id,
        "manifest_sha256": session_input.manifest_sha256,
        "policy_sha256": session_input.policy_sha256,
        "trading_calendar_sha256": session_input.trading_calendar_sha256,
        "session": session,
        "session_input_sha256": session_input_sha256,
        "pre_state_sha256": _required_hash(previous_state),
        "pre_state_sequence": previous_state.state_sequence,
        "events": events,
        "event_sha256s": event_hashes,
        "post_state_payload_sha256": payload_hash,
        "transition_status": mutable.path_status,
        "reason_codes": tuple(dict.fromkeys(mutable.path_reasons)),
    }
    transition_id = canonical_policy_artifact_id(
        "PPTRANS",
        {
            "decision_role": role,
            "protocol_id": session_input.protocol_id,
            "manifest_sha256": session_input.manifest_sha256,
            "policy_sha256": session_input.policy_sha256,
            "session": session.isoformat(),
            "session_input_sha256": session_input_sha256,
            "pre_state_sha256": _required_hash(previous_state),
            "pre_state_sequence": previous_state.state_sequence,
            "event_sha256s": list(event_hashes),
            "post_state_payload_sha256": payload_hash,
            "transition_status": mutable.path_status,
            "reason_codes": list(dict.fromkeys(mutable.path_reasons)),
        },
    )
    try:
        transition = PolicyPortfolioSessionTransition(
            transition_id=transition_id,
            **transition_values,
        )
    except ValueError as exc:
        raise ShadowContractError(
            f"{role} policy-portfolio transition is invalid"
        ) from exc
    state = _make_state(
        role=role,
        path_id=previous_state.path_id,
        sequence=previous_state.state_sequence + 1,
        previous_state_sha256=_required_hash(previous_state),
        genesis_sha256=previous_state.genesis_sha256,
        manifest=session_input.manifest,
        policy=session_input.policy,
        trading_calendar=session_input.trading_calendar,
        session_input_sha256=session_input_sha256,
        transition_sha256=_required_hash(transition),
        payload=payload,
    )
    return transition, state


def _post_due_settlements(
    mutable: _MutablePath,
    session: date,
) -> None:
    due_payables = tuple(
        item
        for item in mutable.purchase_payables
        if item.settlement_session == session
    )
    due_receivables = tuple(
        item
        for item in mutable.sale_receivables
        if item.settlement_session == session
    )
    overdue = tuple(
        item
        for item in (*mutable.purchase_payables, *mutable.sale_receivables)
        if item.settlement_session < session
    )
    if overdue:
        _poison_path(
            mutable,
            session=session,
            reason="OVERDUE_SETTLEMENT_LEG",
            source_sha256=_required_hash(overdue[0]),
            occurred_at=_session_open_at(session),
        )
        return
    for leg in due_payables:
        if leg.amount_idr > mutable.settled_cash:
            _poison_path(
                mutable,
                session=session,
                reason="PURCHASE_PAYABLE_EXCEEDS_SETTLED_CASH",
                source_sha256=_required_hash(leg),
                occurred_at=_session_open_at(session),
            )
            return
        mutable.settled_cash -= leg.amount_idr
        mutable.purchase_payables.remove(leg)
        _append_event(
            mutable,
            session=session,
            event_type="SETTLEMENT_POSTED",
            occurred_at=_session_open_at(session),
            ticker=leg.ticker,
            source_artifact_sha256=_required_hash(leg),
            reason_codes=("PURCHASE_PAYABLE_POSTED",),
            settled_cash_delta_idr=-leg.amount_idr,
            purchase_payable_delta_idr=-leg.amount_idr,
        )
    for leg in due_receivables:
        mutable.settled_cash += leg.amount_idr
        mutable.sale_receivables.remove(leg)
        _append_event(
            mutable,
            session=session,
            event_type="SETTLEMENT_POSTED",
            occurred_at=_session_open_at(session),
            ticker=leg.ticker,
            source_artifact_sha256=_required_hash(leg),
            reason_codes=("SALE_RECEIVABLE_POSTED",),
            settled_cash_delta_idr=leg.amount_idr,
            sale_receivable_delta_idr=-leg.amount_idr,
        )


def _apply_opening_corporate_actions(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    candidates: dict[str, PolicyPortfolioCandidateInput],
) -> None:
    updated: list[PolicyPosition] = []
    for position in mutable.positions:
        candidate = candidates[position.candidate_input_id]
        pair = candidate.pair_input
        if _rights_effective(pair, session_input.session):
            _poison_path(
                mutable,
                session=session_input.session,
                reason="RIGHTS_POLICY_UNSUPPORTED",
                source_sha256=candidate.pair_input_sha256,
                occurred_at=_session_open_at(session_input.session),
                ticker=position.ticker,
            )
            return
        adjusted_quantity = _apply_split(
            pair,
            session_input.session,
            position.current_quantity_shares,
        )
        if adjusted_quantity is None:
            _poison_path(
                mutable,
                session=session_input.session,
                reason="NOT_ESTIMABLE_SPLIT_NON_INTEGER",
                source_sha256=candidate.pair_input_sha256,
                occurred_at=_session_open_at(session_input.session),
                ticker=position.ticker,
            )
            return
        if adjusted_quantity == position.current_quantity_shares:
            updated.append(position)
            continue
        decision = _decision_for(candidate, mutable.role)
        try:
            geometry = _geometry_for_session(
                _integer_geometry(decision),
                pair,
                session_input.session,
            )
        except (ShadowContractError, ValueError):
            _poison_path(
                mutable,
                session=session_input.session,
                reason="NOT_ESTIMABLE_SPLIT_NON_INTEGER",
                source_sha256=candidate.pair_input_sha256,
                occurred_at=_session_open_at(session_input.session),
                ticker=position.ticker,
            )
            return
        mark_factor = Decimal(1)
        for action in pair.bar_series.corporate_action_policy.events:
            if (
                action.kind == "SPLIT"
                and action.effective_date == session_input.session
            ):
                mark_factor *= Decimal(str(action.price_factor))
        adjusted_mark_decimal = (
            Decimal(position.last_mark_price_idr) * mark_factor
        )
        if (
            not adjusted_mark_decimal.is_finite()
            or adjusted_mark_decimal
            != adjusted_mark_decimal.to_integral_value()
        ):
            _poison_path(
                mutable,
                session=session_input.session,
                reason="NOT_ESTIMABLE_SPLIT_NON_INTEGER",
                source_sha256=candidate.pair_input_sha256,
                occurred_at=_session_open_at(session_input.session),
                ticker=position.ticker,
            )
            return
        adjusted_mark = int(adjusted_mark_decimal)
        planned_risk = adjusted_quantity * (
            geometry.entry_high - geometry.stop
        )
        rebuilt = position.__class__.model_validate(
            {
                **position.model_dump(mode="python"),
                "current_quantity_shares": adjusted_quantity,
                "planned_entry_high_idr": geometry.entry_high,
                "planned_stop_idr": geometry.stop,
                "planned_target_idr": geometry.target,
                "planned_risk_idr": planned_risk,
                "last_mark_session": session_input.session,
                "last_mark_price_idr": adjusted_mark,
                "last_verified_at": _session_open_at(
                    session_input.session
                ),
            }
        )
        updated.append(rebuilt)
        _append_event(
            mutable,
            session=session_input.session,
            event_type="CORPORATE_ACTION_SPLIT",
            occurred_at=_session_open_at(session_input.session),
            ticker=position.ticker,
            candidate_input_sha256=_required_hash(candidate),
            source_artifact_sha256=candidate.pair_input_sha256,
            reason_codes=("SUPPORTED_SPLIT_APPLIED",),
            planned_risk_delta_idr=(
                rebuilt.planned_risk_idr - position.planned_risk_idr
            ),
            gross_exposure_delta_idr=(
                rebuilt.marked_value_idr - position.marked_value_idr
            ),
            quantity_delta_shares=(
                adjusted_quantity - position.current_quantity_shares
            ),
        )
    mutable.positions = updated


def _cancel_for_regime_reduction(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
) -> None:
    limit = _regime_limit(session_input.regime.regime)
    keep = max(limit - len(mutable.positions), 0)
    ordered = sorted(mutable.commitments, key=lambda item: item.priority_key)
    survivors = ordered[:keep]
    canceled = ordered[keep:]
    mutable.commitments = survivors
    for commitment in canceled:
        _append_event(
            mutable,
            session=session_input.session,
            event_type="COMMITMENT_CANCELED",
            occurred_at=_session_open_at(session_input.session),
            ticker=commitment.ticker,
            candidate_input_sha256=commitment.candidate_input_sha256,
            source_artifact_sha256=_required_hash(commitment),
            reason_codes=("REGIME_LIMIT_REDUCTION",),
            reserved_cash_delta_idr=-commitment.reserved_debit_idr,
            planned_risk_delta_idr=-commitment.planned_risk_idr,
            gross_exposure_delta_idr=-commitment.planned_gross_idr,
        )


def _process_exits(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    candidates: dict[str, PolicyPortfolioCandidateInput],
    *,
    phase: Literal["OPEN", "SESSION"],
) -> None:
    for position in tuple(mutable.positions):
        if mutable.path_status != "ACTIVE" or position not in mutable.positions:
            continue
        candidate = candidates[position.candidate_input_id]
        pair = candidate.pair_input
        bar = _bar_for(pair, session_input.session)
        if bar is None:
            _poison_path(
                mutable,
                session=session_input.session,
                reason="MISSING_REQUIRED_SESSION_BAR",
                source_sha256=candidate.pair_input_sha256,
                ticker=position.ticker,
            )
            return
        decision = _decision_for(candidate, mutable.role)
        try:
            geometry = _geometry_for_session(
                _integer_geometry(decision),
                pair,
                session_input.session,
            )
        except (ShadowContractError, ValueError):
            _poison_path(
                mutable,
                session=session_input.session,
                reason="NOT_ESTIMABLE_SPLIT_NON_INTEGER",
                source_sha256=candidate.pair_input_sha256,
                ticker=position.ticker,
            )
            return
        terminal = _terminal_for_bar(bar, geometry, intraday_fill=False)
        opening_terminal = (
            bar.open_price_idr <= geometry.stop
            or bar.open_price_idr >= geometry.target
        )
        post_fill_count = _post_fill_session_count(
            session_input.trading_calendar,
            position.opened_session,
            session_input.session,
        )
        timeout = (
            phase == "SESSION"
            and post_fill_count >= POLICY_PORTFOLIO_PRIMARY_HORIZON
        )
        if phase == "OPEN":
            if terminal is None or not opening_terminal:
                continue
        elif opening_terminal:
            continue
        elif terminal is None and not timeout:
            continue
        if timeout and terminal is None:
            exit_price = bar.close_price_idr
            reason = "TIMEOUT_HORIZON"
            occurred_at = session_close_at(session_input.session)
        else:
            assert terminal is not None
            _, exit_price, _, reason = terminal
            occurred_at = (
                _session_open_at(session_input.session)
                if phase == "OPEN"
                else session_close_at(session_input.session)
            )
        if not _execute_exit(
            mutable,
            session_input,
            candidate,
            position,
            exit_price=exit_price,
            occurred_at=occurred_at,
            reason=reason,
        ):
            return


def _execute_exit(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    candidate: PolicyPortfolioCandidateInput,
    position: PolicyPosition,
    *,
    exit_price: int,
    occurred_at: datetime,
    reason: str,
) -> bool:
    pair = candidate.pair_input
    gross = exit_price * position.current_quantity_shares
    measurement = pair.liquidity.exit_measurement_for(session_input.session)
    if (
        measurement is None
        or not measurement.minimum_adtv20_passed
        or not measurement.supports_gross_notional(gross)
    ):
        _poison_path(
            mutable,
            session=session_input.session,
            reason="NOT_ESTIMABLE_EXIT_CAPACITY",
            source_sha256=candidate.pair_input_sha256,
            occurred_at=occurred_at,
            ticker=position.ticker,
        )
        return False
    settlement = _settlement_session(
        session_input.trading_calendar,
        session_input.session,
        lag_sessions=session_input.policy.settlement_lag_sessions,
    )
    if settlement is None or settlement > session_input.manifest.fixed_terminal_date:
        _poison_path(
            mutable,
            session=session_input.session,
            reason="NOT_ESTIMABLE_EXIT_SETTLEMENT",
            source_sha256=candidate.pair_input_sha256,
            occurred_at=occurred_at,
            ticker=position.ticker,
        )
        return False
    exit_cost = fixed_notional_cost_idr(
        gross,
        session_input.manifest.costs,
        side="EXIT",
    )
    net = gross - exit_cost
    realized = net - position.cost_basis_idr
    event = _append_event(
        mutable,
        session=session_input.session,
        event_type="EXIT_FILLED",
        occurred_at=occurred_at,
        ticker=position.ticker,
        candidate_input_sha256=_required_hash(candidate),
        source_artifact_sha256=_required_hash(position),
        reason_codes=(reason,),
        sale_receivable_delta_idr=net,
        realized_pnl_delta_idr=realized,
        planned_risk_delta_idr=-position.planned_risk_idr,
        gross_exposure_delta_idr=-position.marked_value_idr,
        quantity_delta_shares=-position.current_quantity_shares,
    )
    event_hash = _required_hash(event)
    leg_values = {
        "decision_role": mutable.role,
        "leg_type": "SALE_RECEIVABLE",
        "raw_event_id": position.raw_event_id,
        "ticker": position.ticker,
        "source_event_id": event.event_id,
        "source_event_sha256": event_hash,
        "trade_session": session_input.session,
        "settlement_session": settlement,
        "amount_idr": net,
    }
    leg_id = canonical_policy_artifact_id(
        "PPLEG",
        {
            "decision_role": mutable.role,
            "leg_type": "SALE_RECEIVABLE",
            "raw_event_id": position.raw_event_id,
            "source_event_sha256": event_hash,
            "trade_session": session_input.session.isoformat(),
            "settlement_session": settlement.isoformat(),
            "amount_idr": net,
        },
    )
    leg = PolicySettlementLeg(settlement_leg_id=leg_id, **leg_values)
    mutable.sale_receivables.append(leg)
    mutable.positions.remove(position)
    mutable.reentry_blocks[position.ticker] = session_input.session
    mutable.realized_pnl += realized
    _update_daily_stop(
        mutable,
        session_input,
        event_hash,
        occurred_at=occurred_at,
    )
    return True


def _process_dividends(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    candidates: dict[str, PolicyPortfolioCandidateInput],
) -> None:
    for position in tuple(mutable.positions):
        candidate = candidates[position.candidate_input_id]
        bar = _bar_for(candidate.pair_input, session_input.session)
        if (
            bar is None
            or bar.dividend_per_share_idr == 0
            or position.opened_session >= session_input.session
        ):
            continue
        if (
            candidate.pair_input.bar_series.corporate_action_policy.dividend_return_convention
            != "TOTAL_RETURN"
        ):
            continue
        cash = bar.dividend_per_share_idr * position.current_quantity_shares
        mutable.settled_cash += cash
        mutable.realized_pnl += cash
        event = _append_event(
            mutable,
            session=session_input.session,
            event_type="DIVIDEND_CREDIT",
            occurred_at=session_close_at(session_input.session),
            ticker=position.ticker,
            candidate_input_sha256=_required_hash(candidate),
            source_artifact_sha256=candidate.pair_input_sha256,
            reason_codes=("ELIGIBLE_DIVIDEND_CREDIT",),
            settled_cash_delta_idr=cash,
            realized_pnl_delta_idr=cash,
        )
        _update_daily_stop(
            mutable,
            session_input,
            _required_hash(event),
            occurred_at=session_close_at(session_input.session),
        )


def _process_fills(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    candidates: dict[str, PolicyPortfolioCandidateInput],
    *,
    phase: Literal["OPEN", "SESSION"],
) -> None:
    for commitment in tuple(
        sorted(mutable.commitments, key=lambda item: item.priority_key)
    ):
        if (
            mutable.path_status != "ACTIVE"
            or commitment not in mutable.commitments
            or session_input.session not in commitment.activation_sessions
        ):
            continue
        candidate = candidates[commitment.candidate_input_id]
        pair = candidate.pair_input
        bar = _bar_for(pair, session_input.session)
        if bar is None:
            _cancel_commitment(
                mutable,
                session_input,
                commitment,
                "MISSING_REQUIRED_SESSION_BAR",
            )
            continue
        if _rights_effective(pair, session_input.session):
            _cancel_commitment(
                mutable,
                session_input,
                commitment,
                "RIGHTS_POLICY_UNSUPPORTED",
                occurred_at=_session_open_at(session_input.session),
            )
            continue
        decision = _decision_for(candidate, mutable.role)
        try:
            geometry = _geometry_for_session(
                _integer_geometry(decision),
                pair,
                session_input.session,
            )
        except (ShadowContractError, ValueError):
            _cancel_commitment(
                mutable,
                session_input,
                commitment,
                "NOT_ESTIMABLE_SPLIT_NON_INTEGER",
                occurred_at=_session_open_at(session_input.session),
            )
            continue
        fill = _entry_fill(bar, geometry)
        if fill is None:
            continue
        fill_price, intraday = fill
        if (phase == "OPEN" and intraday) or (
            phase == "SESSION" and not intraday
        ):
            continue
        occurred_at = (
            session_close_at(session_input.session)
            if intraday
            else _session_open_at(session_input.session)
        )
        fill_quantity = _commitment_quantity_for_session(
            pair,
            commitment,
            session_input.session,
        )
        if fill_quantity is None:
            _cancel_commitment(
                mutable,
                session_input,
                commitment,
                "NOT_ESTIMABLE_SPLIT_NON_INTEGER",
                occurred_at=occurred_at,
            )
            continue
        gross = fill_price * fill_quantity
        entry_cost = fixed_notional_cost_idr(
            gross,
            session_input.manifest.costs,
            side="ENTRY",
        )
        fill_planned_risk = fill_quantity * (
            geometry.entry_high - geometry.stop
        )
        reasons = _fill_recheck_reasons(
            mutable,
            session_input,
            commitment,
            candidate,
            gross=gross,
            entry_cost=entry_cost,
            planned_risk=fill_planned_risk,
        )
        if reasons:
            _cancel_commitment(
                mutable,
                session_input,
                commitment,
                *reasons,
                occurred_at=occurred_at,
            )
            continue
        settlement = _settlement_session(
            session_input.trading_calendar,
            session_input.session,
            lag_sessions=session_input.policy.settlement_lag_sessions,
        )
        if (
            settlement is None
            or settlement > session_input.manifest.fixed_terminal_date
        ):
            _cancel_commitment(
                mutable,
                session_input,
                commitment,
                "NOT_ESTIMABLE_ENTRY_SETTLEMENT",
                occurred_at=occurred_at,
            )
            continue
        event = _append_event(
            mutable,
            session=session_input.session,
            event_type="ENTRY_FILLED",
            occurred_at=occurred_at,
            ticker=commitment.ticker,
            candidate_input_sha256=_required_hash(candidate),
            source_artifact_sha256=_required_hash(commitment),
            reason_codes=("POLICY_ENTRY_FILLED",),
            purchase_payable_delta_idr=gross + entry_cost,
            reserved_cash_delta_idr=-commitment.reserved_debit_idr,
            planned_risk_delta_idr=(
                fill_planned_risk - commitment.planned_risk_idr
            ),
            gross_exposure_delta_idr=(
                fill_quantity * bar.close_price_idr
                - commitment.planned_gross_idr
            ),
            quantity_delta_shares=fill_quantity,
        )
        event_hash = _required_hash(event)
        leg_id = canonical_policy_artifact_id(
            "PPLEG",
            {
                "decision_role": mutable.role,
                "leg_type": "PURCHASE_PAYABLE",
                "raw_event_id": commitment.raw_event_id,
                "source_event_sha256": event_hash,
                "trade_session": session_input.session.isoformat(),
                "settlement_session": settlement.isoformat(),
                "amount_idr": gross + entry_cost,
            },
        )
        payable = PolicySettlementLeg(
            settlement_leg_id=leg_id,
            decision_role=mutable.role,
            leg_type="PURCHASE_PAYABLE",
            raw_event_id=commitment.raw_event_id,
            ticker=commitment.ticker,
            source_event_id=event.event_id,
            source_event_sha256=event_hash,
            trade_session=session_input.session,
            settlement_session=settlement,
            amount_idr=gross + entry_cost,
        )
        position_id = canonical_policy_artifact_id(
            "PPPOS",
            {
                "decision_role": mutable.role,
                "originating_commitment_sha256": _required_hash(commitment),
                "candidate_input_sha256": _required_hash(candidate),
                "decision_sha256": commitment.decision_sha256,
                "opened_session": session_input.session.isoformat(),
                "entry_price_idr": fill_price,
                "entry_quantity_shares": commitment.quantity_shares,
            },
        )
        position = PolicyPosition(
            position_id=position_id,
            decision_role=mutable.role,
            originating_commitment_id=commitment.commitment_id,
            originating_commitment_sha256=_required_hash(commitment),
            candidate_input_id=candidate.candidate_input_id,
            candidate_input_sha256=_required_hash(candidate),
            pair_input_sha256=candidate.pair_input_sha256,
            decision_sha256=commitment.decision_sha256,
            raw_event_id=commitment.raw_event_id,
            ticker=commitment.ticker,
            sector_id=commitment.sector_id,
            cluster_id=commitment.cluster_id,
            opened_session=session_input.session,
            opened_at=occurred_at,
            entry_price_idr=fill_price,
            entry_quantity_shares=fill_quantity,
            current_quantity_shares=fill_quantity,
            entry_gross_idr=gross,
            entry_cost_idr=entry_cost,
            cost_basis_idr=gross + entry_cost,
            planned_entry_high_idr=geometry.entry_high,
            planned_stop_idr=geometry.stop,
            planned_target_idr=geometry.target,
            planned_risk_idr=fill_planned_risk,
            last_mark_session=session_input.session,
            last_mark_price_idr=bar.close_price_idr,
            last_verified_at=session_close_at(session_input.session),
        )
        mutable.commitments.remove(commitment)
        mutable.purchase_payables.append(payable)
        mutable.positions.append(position)
        if intraday:
            terminal = _terminal_for_bar(
                bar,
                geometry,
                intraday_fill=True,
            )
            if terminal is not None:
                _, exit_price, _, exit_reason = terminal
                if not _execute_exit(
                    mutable,
                    session_input,
                    candidate,
                    position,
                    exit_price=exit_price,
                    occurred_at=session_close_at(session_input.session),
                    reason=exit_reason,
                ):
                    return


def _fill_recheck_reasons(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    commitment: PolicyCommitment,
    candidate: PolicyPortfolioCandidateInput,
    *,
    gross: int,
    entry_cost: int,
    planned_risk: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if mutable.path_status != "ACTIVE":
        reasons.append("NOT_ESTIMABLE_FROM_SESSION")
    if mutable.daily_stop:
        reasons.append("DAILY_REALIZED_LOSS_STOP")
    if any(item.ticker == commitment.ticker for item in mutable.positions):
        reasons.append("DUPLICATE_OR_REENTRY_BLOCKED")
    if mutable.reentry_blocks.get(commitment.ticker) == session_input.session:
        reasons.append("DUPLICATE_OR_REENTRY_BLOCKED")

    decision = _decision_for(candidate, mutable.role)
    try:
        base_geometry = _integer_geometry(decision)
    except (ShadowContractError, ValueError):
        base_geometry = None
    fraction = decision.recorded_position_fraction
    if (
        fraction is None
        or type(fraction) is not float
        or base_geometry is None
    ):
        reasons.append("NOT_ESTIMABLE_POLICY_SIZE")
    else:
        requested = int(
            (
                Decimal(str(fraction))
                * Decimal(session_input.policy.starting_capital_idr)
            ).to_integral_value(rounding=ROUND_FLOOR)
        )
        expected_lots = requested // (
            base_geometry.entry_high * session_input.policy.lot_size_shares
        )
        if (
            requested != commitment.requested_notional_idr
            or expected_lots != commitment.planned_lots
            or commitment.quantity_shares
            != expected_lots * session_input.policy.lot_size_shares
        ):
            reasons.append("NOT_ESTIMABLE_POLICY_SIZE")
    try:
        fill_index = session_input.trading_calendar.sessions.index(
            session_input.session
        )
        terminal_index = (
            fill_index
            + session_input.policy.primary_horizon_trading_days
            + session_input.policy.settlement_lag_sessions
        )
        fill_has_runway = (
            terminal_index < len(session_input.trading_calendar.sessions)
            and session_input.trading_calendar.sessions[terminal_index]
            <= session_input.manifest.fixed_terminal_date
        )
    except ValueError:  # pragma: no cover - session contract guards this
        fill_has_runway = False
    if not fill_has_runway:
        reasons.append("INSUFFICIENT_FIXED_TERMINAL_RUNWAY")

    other_commitments = tuple(
        item
        for item in mutable.commitments
        if item.commitment_id != commitment.commitment_id
    )
    limit = _regime_limit(session_input.regime.regime)
    occupied_after = len(mutable.positions) + len(other_commitments) + 1
    if occupied_after > limit:
        reasons.append("REGIME_SLOT_LIMIT")

    classification = candidate.classification
    classification_expired = (
        classification.source_expires_at is not None
        and classification.source_expires_at
        <= _session_open_at(session_input.session)
    )
    if classification.status != "ESTIMABLE" or classification_expired:
        reasons.append("NOT_ESTIMABLE_CLASSIFICATION")
    else:
        assert (
            classification.sector_id is not None
            and classification.cluster_id is not None
        )
        sector_count = sum(
            item.sector_id == classification.sector_id
            for item in (*mutable.positions, *other_commitments)
        ) + 1
        cluster_count = sum(
            item.cluster_id == classification.cluster_id
            for item in (*mutable.positions, *other_commitments)
        ) + 1
        adverse = mutable.adverse_close_snapshot
        if adverse is not None:
            sector_count = max(
                sector_count,
                adverse.sector_counts.get(classification.sector_id, 0),
            )
            cluster_count = max(
                cluster_count,
                adverse.cluster_counts.get(classification.cluster_id, 0),
            )
        if sector_count > session_input.policy.sector_max_names:
            reasons.append("SECTOR_LIMIT")
        if cluster_count > session_input.policy.cluster_max_names:
            reasons.append("CLUSTER_LIMIT")

    liquidity = candidate.liquidity_for(session_input.session)
    if (
        liquidity is None
        or not liquidity.minimum_adtv20_passed
        or not liquidity.supports_gross_notional(gross)
    ):
        reasons.append("NOT_ESTIMABLE_ENTRY_CAPACITY")

    existing_risk = sum(
        item.planned_risk_idr
        for item in (*mutable.positions, *other_commitments)
    )
    risk_after = existing_risk + planned_risk
    if mutable.adverse_close_snapshot is not None:
        risk_after = max(
            risk_after,
            mutable.adverse_close_snapshot.planned_risk,
        )
    loss_budget_idr = _fraction_idr(
        session_input.policy.starting_capital_idr,
        session_input.policy.total_loss_budget_fraction,
    )
    if limit <= 0 or planned_risk * limit > loss_budget_idr:
        reasons.append("PER_POSITION_RISK_LIMIT")
    if risk_after > loss_budget_idr:
        reasons.append("TOTAL_LOSS_BUDGET_LIMIT")
    heat_limit_idr = _fraction_idr(
        session_input.policy.starting_capital_idr,
        session_input.policy.max_portfolio_heat_fraction,
    )
    if risk_after > heat_limit_idr:
        reasons.append("PORTFOLIO_HEAT_LIMIT")

    exposure_after = (
        sum(item.marked_value_idr for item in mutable.positions)
        + sum(item.planned_gross_idr for item in other_commitments)
        + gross
    )
    accounting_equity_after = _accounting_equity(mutable) - entry_cost
    if mutable.adverse_close_snapshot is not None:
        exposure_after = max(
            exposure_after,
            mutable.adverse_close_snapshot.gross_exposure,
        )
        accounting_equity_after = min(
            accounting_equity_after,
            mutable.adverse_close_snapshot.accounting_equity - entry_cost,
        )
    if accounting_equity_after <= 0:
        reasons.append("NOT_ESTIMABLE_ACCOUNTING_EQUITY")
    elif (
        Decimal(exposure_after)
        > Decimal(accounting_equity_after)
        * Decimal(str(session_input.policy.max_gross_exposure_fraction))
    ):
        reasons.append("GROSS_EXPOSURE_LIMIT")

    other_reserved = sum(
        item.reserved_debit_idr for item in other_commitments
    )
    payables = sum(item.amount_idr for item in mutable.purchase_payables)
    minimum_cash = _minimum_cash_idr(session_input.policy)
    buying_power_after = (
        mutable.settled_cash
        - payables
        - other_reserved
        - gross
        - entry_cost
    )
    if mutable.adverse_close_snapshot is not None:
        buying_power_after = min(
            buying_power_after,
            mutable.adverse_close_snapshot.buying_power,
        )
    if buying_power_after < minimum_cash:
        reasons.append("MINIMUM_CASH_LIMIT")
    return tuple(dict.fromkeys(reasons))


def _expire_or_cancel_commitments(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
) -> None:
    if mutable.daily_stop:
        for commitment in tuple(mutable.commitments):
            _cancel_commitment(
                mutable,
                session_input,
                commitment,
                "DAILY_REALIZED_LOSS_STOP",
            )
        return
    for commitment in tuple(mutable.commitments):
        if session_input.session >= commitment.activation_sessions[-1]:
            mutable.commitments.remove(commitment)
            _append_event(
                mutable,
                session=session_input.session,
                event_type="COMMITMENT_EXPIRED",
                occurred_at=session_close_at(session_input.session),
                ticker=commitment.ticker,
                candidate_input_sha256=commitment.candidate_input_sha256,
                source_artifact_sha256=_required_hash(commitment),
                reason_codes=("ENTRY_VALIDITY_EXPIRED_UNFILLED",),
                reserved_cash_delta_idr=-commitment.reserved_debit_idr,
                planned_risk_delta_idr=-commitment.planned_risk_idr,
                gross_exposure_delta_idr=-commitment.planned_gross_idr,
            )


def _cancel_commitment(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    commitment: PolicyCommitment,
    *reasons: str,
    occurred_at: datetime | None = None,
) -> None:
    if commitment not in mutable.commitments:
        return
    mutable.commitments.remove(commitment)
    _append_event(
        mutable,
        session=session_input.session,
        event_type="COMMITMENT_CANCELED",
        occurred_at=occurred_at or session_close_at(session_input.session),
        ticker=commitment.ticker,
        candidate_input_sha256=commitment.candidate_input_sha256,
        source_artifact_sha256=_required_hash(commitment),
        reason_codes=tuple(dict.fromkeys(reasons)),
        reserved_cash_delta_idr=-commitment.reserved_debit_idr,
        planned_risk_delta_idr=-commitment.planned_risk_idr,
        gross_exposure_delta_idr=-commitment.planned_gross_idr,
    )


@dataclass(frozen=True)
class _AdmissionPlan:
    candidate: PolicyPortfolioCandidateInput
    decision: ShadowDecision
    source_row: int | None
    rank: int | None
    fraction: float | None
    activation_sessions: tuple[date, ...]
    requested_notional: int
    entry_high: int
    stop: int
    target: int
    lots: int
    shares: int
    gross: int
    entry_cost: int
    reservation: int
    risk: int
    preliminary_reasons: tuple[str, ...]


def _admit_new_signals(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
) -> None:
    candidates = tuple(
        item
        for item in session_input.candidates
        if item.candidate_input_id
        in set(session_input.admission_candidate_ids)
        and _decision_for(item, mutable.role).would_allocate
    )
    rank_counts: dict[int, int] = {}
    for candidate in candidates:
        rank = _decision_for(candidate, mutable.role).recorded_rank
        if rank is not None:
            rank_counts[rank] = rank_counts.get(rank, 0) + 1
    plans = tuple(
        _build_admission_plan(
            mutable,
            session_input,
            candidate,
            duplicate_rank=(
                _decision_for(candidate, mutable.role).recorded_rank is not None
                and rank_counts[
                    int(_decision_for(candidate, mutable.role).recorded_rank)
                ]
                > 1
            ),
        )
        for candidate in candidates
    )
    ordered = tuple(
        sorted(
            plans,
            key=lambda item: (
                item.rank if item.rank is not None else 2**31,
                item.source_row if item.source_row is not None else 2**31,
                item.candidate.pair_input.observation.ticker,
            ),
        )
    )
    for plan in ordered:
        gate_results = _admission_gate_results(
            mutable,
            session_input,
            plan,
        )
        failed_reasons = tuple(
            reason
            for gate in gate_results
            for reason in gate.reason_codes
        )
        source_hash = _required_hash(plan.candidate)
        ticker = plan.candidate.pair_input.observation.ticker
        if failed_reasons:
            processed_at = max(
                plan.candidate.pair_input.observation.signal_at,
                session_close_at(session_input.session),
            )
            _append_event(
                mutable,
                session=session_input.session,
                event_type="ADMISSION_REJECTED",
                occurred_at=processed_at,
                ticker=ticker,
                candidate_input_sha256=source_hash,
                source_artifact_sha256=source_hash,
                gate_results=gate_results,
                reason_codes=tuple(dict.fromkeys(failed_reasons)),
            )
            continue
        assert (
            plan.rank is not None
            and plan.source_row is not None
            and plan.fraction is not None
            and plan.lots > 0
        )
        classification = plan.candidate.classification
        assert (
            classification.sector_id is not None
            and classification.cluster_id is not None
        )
        decision_hash = _required_hash(plan.decision)
        candidate_hash = _required_hash(plan.candidate)
        commitment_id = canonical_policy_artifact_id(
            "PPCOM",
            {
                "decision_role": mutable.role,
                "candidate_input_sha256": candidate_hash,
                "decision_sha256": decision_hash,
                "created_session": session_input.session.isoformat(),
                "priority_key": [plan.rank, plan.source_row, ticker],
                "planned_gross_idr": plan.gross,
                "reserved_debit_idr": plan.reservation,
                "planned_risk_idr": plan.risk,
            },
        )
        commitment = PolicyCommitment(
            commitment_id=commitment_id,
            decision_role=mutable.role,
            candidate_input_id=plan.candidate.candidate_input_id,
            candidate_input_sha256=candidate_hash,
            pair_input_sha256=plan.candidate.pair_input_sha256,
            decision_sha256=decision_hash,
            raw_event_id=plan.candidate.pair_input.observation.raw_event_id,
            ticker=ticker,
            source_row_number=plan.source_row,
            recorded_rank=plan.rank,
            priority_key=(plan.rank, plan.source_row, ticker),
            sector_id=classification.sector_id,
            cluster_id=classification.cluster_id,
            signal_session=session_input.session,
            created_session=session_input.session,
            activation_sessions=plan.activation_sessions,
            requested_position_fraction=plan.fraction,
            requested_notional_idr=plan.requested_notional,
            planned_entry_high_idr=plan.entry_high,
            planned_stop_idr=plan.stop,
            planned_target_idr=plan.target,
            planned_lots=plan.lots,
            quantity_shares=plan.shares,
            planned_gross_idr=plan.gross,
            planned_entry_cost_idr=plan.entry_cost,
            reserved_debit_idr=plan.reservation,
            planned_risk_idr=plan.risk,
        )
        mutable.commitments.append(commitment)
        mutable.commitments.sort(key=lambda item: item.priority_key)
        _append_event(
            mutable,
            session=session_input.session,
            event_type="ADMISSION_ACCEPTED",
            occurred_at=max(
                plan.candidate.pair_input.observation.signal_at,
                session_close_at(session_input.session),
            ),
            ticker=ticker,
            candidate_input_sha256=candidate_hash,
            source_artifact_sha256=_required_hash(commitment),
            gate_results=gate_results,
            reason_codes=("POLICY_COMMITMENT_CREATED",),
            reserved_cash_delta_idr=plan.reservation,
            planned_risk_delta_idr=plan.risk,
            gross_exposure_delta_idr=plan.gross,
        )


def _build_admission_plan(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    candidate: PolicyPortfolioCandidateInput,
    *,
    duplicate_rank: bool,
) -> _AdmissionPlan:
    decision = _decision_for(candidate, mutable.role)
    reasons: list[str] = []
    source_row = candidate.pair_input.candidate.source_row_number
    rank = decision.recorded_rank
    fraction = decision.recorded_position_fraction
    if rank is None or source_row is None or duplicate_rank:
        reasons.append("NOT_ESTIMABLE_PRIORITY")
    expected_basis = (
        "CONTROL_OBSERVED" if mutable.role == "CONTROL" else "COUNTERFACTUAL"
    )
    if decision.position_size_basis != expected_basis:
        reasons.append("NOT_ESTIMABLE_POLICY_SIZE")
    if fraction is None or type(fraction) is not float:
        reasons.append("NOT_ESTIMABLE_POLICY_SIZE")
    elif fraction != quantize_ratio_decimal(Decimal(str(fraction))):
        reasons.append("NOT_ESTIMABLE_POLICY_SIZE")
    if decision.geometry is None:
        reasons.append("NOT_ESTIMABLE_POLICY_SIZE")
    activation_sessions = _activation_sessions(
        session_input,
        candidate,
    )
    if not _has_terminal_runway(
        session_input,
        activation_sessions,
    ):
        reasons.append("INSUFFICIENT_FIXED_TERMINAL_RUNWAY")
    requested = 0
    entry_high = 0
    stop = 0
    target = 0
    lots = 0
    shares = 0
    gross = 0
    entry_cost = 0
    reservation = 0
    risk = 0
    if fraction is not None and decision.geometry is not None:
        try:
            geometry = _integer_geometry(decision)
            entry_high = geometry.entry_high
            stop = geometry.stop
            target = geometry.target
            requested = int(
                (
                    Decimal(str(fraction))
                    * Decimal(session_input.policy.starting_capital_idr)
                ).to_integral_value(rounding=ROUND_FLOOR)
            )
            lots = requested // (
                entry_high * session_input.policy.lot_size_shares
            )
            shares = lots * session_input.policy.lot_size_shares
            gross = shares * entry_high
            if lots <= 0:
                reasons.append("NOT_ESTIMABLE_POLICY_SIZE")
            else:
                entry_cost = fixed_notional_cost_idr(
                    gross,
                    session_input.manifest.costs,
                    side="ENTRY",
                )
                reservation = gross + entry_cost
                risk = shares * (entry_high - stop)
        except (ShadowContractError, ValueError):
            reasons.append("NOT_ESTIMABLE_POLICY_SIZE")
    return _AdmissionPlan(
        candidate=candidate,
        decision=decision,
        source_row=source_row,
        rank=rank,
        fraction=fraction,
        activation_sessions=activation_sessions,
        requested_notional=requested,
        entry_high=entry_high,
        stop=stop,
        target=target,
        lots=lots,
        shares=shares,
        gross=gross,
        entry_cost=entry_cost,
        reservation=reservation,
        risk=risk,
        preliminary_reasons=tuple(dict.fromkeys(reasons)),
    )


def _admission_gate_results(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    plan: _AdmissionPlan,
) -> tuple[PolicyGateResult, ...]:
    ticker = plan.candidate.pair_input.observation.ticker
    signal_at = plan.candidate.pair_input.observation.signal_at
    adverse = (
        mutable.adverse_close_snapshot
        if signal_at <= session_close_at(session_input.session)
        else None
    )
    preliminary = set(plan.preliminary_reasons)
    integrity_reasons: list[str] = []
    if mutable.path_status != "ACTIVE":
        integrity_reasons.append("NOT_ESTIMABLE_FROM_SESSION")
    if "INSUFFICIENT_FIXED_TERMINAL_RUNWAY" in preliminary:
        integrity_reasons.append("INSUFFICIENT_FIXED_TERMINAL_RUNWAY")
    if (
        signal_at.astimezone(IDX_TIMEZONE).date() != session_input.session
        or signal_at > session_input.frozen_at
    ):
        integrity_reasons.append("FUTURE_DATED_SIGNAL")

    daily_reasons = (
        ["DAILY_REALIZED_LOSS_STOP"] if mutable.daily_stop else []
    )
    size_reasons = [
        item
        for item in (
            "NOT_ESTIMABLE_PRIORITY",
            "NOT_ESTIMABLE_POLICY_SIZE",
        )
        if item in preliminary
    ]
    if any(
        item.ticker == ticker
        for item in (*mutable.positions, *mutable.commitments)
    ) or mutable.reentry_blocks.get(ticker) == session_input.session:
        size_reasons.append("DUPLICATE_OR_REENTRY_BLOCKED")

    regime_limit = _regime_limit(session_input.regime.regime)
    occupied_count = len(mutable.positions) + len(mutable.commitments)
    if adverse is not None:
        occupied_count = max(occupied_count, adverse.occupied_count)
    regime_reasons: list[str] = []
    if session_input.regime.regime == "UNKNOWN":
        regime_reasons.append("NOT_ESTIMABLE_REGIME")
    if occupied_count + 1 > regime_limit:
        regime_reasons.append("REGIME_SLOT_LIMIT")

    classification = plan.candidate.classification
    sector_cluster_reasons: list[str] = []
    if classification.status != "ESTIMABLE":
        sector_cluster_reasons.append("NOT_ESTIMABLE_CLASSIFICATION")
    else:
        assert (
            classification.sector_id is not None
            and classification.cluster_id is not None
        )
        sector_count = sum(
            item.sector_id == classification.sector_id
            for item in (*mutable.positions, *mutable.commitments)
        )
        cluster_count = sum(
            item.cluster_id == classification.cluster_id
            for item in (*mutable.positions, *mutable.commitments)
        )
        if adverse is not None:
            sector_count = max(
                sector_count,
                adverse.sector_counts.get(classification.sector_id, 0),
            )
            cluster_count = max(
                cluster_count,
                adverse.cluster_counts.get(classification.cluster_id, 0),
            )
        if sector_count + 1 > session_input.policy.sector_max_names:
            sector_cluster_reasons.append("SECTOR_LIMIT")
        if cluster_count + 1 > session_input.policy.cluster_max_names:
            sector_cluster_reasons.append("CLUSTER_LIMIT")

    liquidity_reasons: list[str] = []
    entry_measurement = plan.candidate.pair_input.liquidity.entry_measurement
    if (
        plan.gross <= 0
        or not entry_measurement.minimum_adtv20_passed
        or not entry_measurement.supports_gross_notional(plan.gross)
    ):
        liquidity_reasons.append("NOT_ESTIMABLE_ENTRY_CAPACITY")

    existing_risk = _planned_risk(mutable)
    if adverse is not None:
        existing_risk = max(existing_risk, adverse.planned_risk)
    loss_budget_idr = _fraction_idr(
        session_input.policy.starting_capital_idr,
        session_input.policy.total_loss_budget_fraction,
    )
    loss_reasons: list[str] = []
    if regime_limit <= 0 or plan.risk * regime_limit > loss_budget_idr:
        loss_reasons.append("PER_POSITION_RISK_LIMIT")
    if existing_risk + plan.risk > loss_budget_idr:
        loss_reasons.append("TOTAL_LOSS_BUDGET_LIMIT")

    heat_limit_idr = _fraction_idr(
        session_input.policy.starting_capital_idr,
        session_input.policy.max_portfolio_heat_fraction,
    )
    heat_reasons = (
        ["PORTFOLIO_HEAT_LIMIT"]
        if existing_risk + plan.risk > heat_limit_idr
        else []
    )
    heat_ratio = quantize_ratio(
        existing_risk + plan.risk,
        session_input.policy.starting_capital_idr,
    )

    accounting_equity = _accounting_equity(mutable)
    existing_exposure = _policy_gross_exposure(mutable)
    if adverse is not None:
        existing_exposure = max(
            existing_exposure,
            adverse.gross_exposure,
        )
        if adverse.accounting_equity > 0 and accounting_equity > 0:
            current_ratio = Decimal(existing_exposure + plan.gross) / Decimal(
                accounting_equity
            )
            adverse_ratio = Decimal(
                adverse.gross_exposure + plan.gross
            ) / Decimal(adverse.accounting_equity)
            if adverse_ratio > current_ratio:
                accounting_equity = adverse.accounting_equity
    exposure_after = existing_exposure + plan.gross
    gross_reasons: list[str] = []
    gross_ratio: float | None = None
    if accounting_equity <= 0:
        gross_reasons.append("NOT_ESTIMABLE_ACCOUNTING_EQUITY")
    else:
        gross_ratio = quantize_ratio(exposure_after, accounting_equity)
        if (
            Decimal(exposure_after)
            > Decimal(accounting_equity)
            * Decimal(
                str(session_input.policy.max_gross_exposure_fraction)
            )
        ):
            gross_reasons.append("GROSS_EXPOSURE_LIMIT")

    buying_power_after = (
        mutable.settled_cash
        - sum(item.amount_idr for item in mutable.purchase_payables)
        - sum(item.reserved_debit_idr for item in mutable.commitments)
        - plan.reservation
    )
    if adverse is not None:
        buying_power_after = min(
            buying_power_after,
            adverse.buying_power - plan.reservation,
        )
    minimum_cash = _minimum_cash_idr(session_input.policy)
    cash_reasons = (
        ["MINIMUM_CASH_LIMIT"]
        if buying_power_after < minimum_cash
        else []
    )

    reason_groups = (
        integrity_reasons,
        daily_reasons,
        size_reasons,
        regime_reasons,
        sector_cluster_reasons,
        liquidity_reasons,
        loss_reasons,
        heat_reasons,
        gross_reasons,
        cash_reasons,
    )
    observed = (
        {},
        {
            "observed_ratio": quantize_ratio(
                max(-mutable.realized_pnl, 0),
                session_input.policy.starting_capital_idr,
            ),
            "threshold_ratio": session_input.policy.daily_loss_stop_fraction,
        },
        {
            "observed_integer_idr": plan.requested_notional,
        },
        {
            "observed_integer_idr": (
                occupied_count + 1
            ),
            "threshold_integer_idr": regime_limit,
        },
        {},
        {
            "observed_integer_idr": plan.gross,
        },
        {
            "observed_integer_idr": existing_risk + plan.risk,
            "threshold_integer_idr": loss_budget_idr,
        },
        {
            "observed_integer_idr": existing_risk + plan.risk,
            "threshold_integer_idr": heat_limit_idr,
            "observed_ratio": heat_ratio,
            "threshold_ratio": (
                session_input.policy.max_portfolio_heat_fraction
            ),
        },
        {
            "observed_integer_idr": exposure_after,
            "threshold_integer_idr": (
                int(
                    Decimal(max(accounting_equity, 0))
                    * Decimal(
                        str(
                            session_input.policy.max_gross_exposure_fraction
                        )
                    )
                )
            ),
            "observed_ratio": gross_ratio,
            "threshold_ratio": (
                session_input.policy.max_gross_exposure_fraction
            ),
        },
        {
            "observed_integer_idr": buying_power_after,
            "threshold_integer_idr": minimum_cash,
        },
    )
    return tuple(
        PolicyGateResult(
            gate_index=index,
            gate_id=GATE_ORDER[index - 1],
            passed=not reasons,
            reason_codes=tuple(dict.fromkeys(reasons)),
            **observed[index - 1],
        )
        for index, reasons in enumerate(reason_groups, start=1)
    )


def _mark_remaining_positions(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    candidates: dict[str, PolicyPortfolioCandidateInput],
) -> None:
    updated: list[PolicyPosition] = []
    for position in mutable.positions:
        candidate = candidates[position.candidate_input_id]
        bar = _bar_for(candidate.pair_input, session_input.session)
        if bar is None:
            if mutable.path_status == "ACTIVE":
                _poison_path(
                    mutable,
                    session=session_input.session,
                    reason="MISSING_REQUIRED_SESSION_BAR",
                    source_sha256=candidate.pair_input_sha256,
                    ticker=position.ticker,
                )
            updated.append(position)
            continue
        observed = _post_fill_session_count(
            session_input.trading_calendar,
            position.opened_session,
            session_input.session,
        )
        rebuilt = position.__class__.model_validate(
            {
                **position.model_dump(mode="python"),
                "post_fill_sessions_observed": observed,
                "last_mark_session": session_input.session,
                "last_mark_price_idr": bar.close_price_idr,
                "last_verified_at": session_close_at(
                    session_input.session
                ),
            }
        )
        updated.append(rebuilt)
        mark_delta = rebuilt.marked_value_idr - position.marked_value_idr
        if mark_delta:
            _append_event(
                mutable,
                session=session_input.session,
                event_type="MARK_UPDATED",
                occurred_at=session_close_at(session_input.session),
                ticker=position.ticker,
                candidate_input_sha256=_required_hash(candidate),
                source_artifact_sha256=candidate.pair_input_sha256,
                reason_codes=("END_OF_SESSION_MARK",),
                gross_exposure_delta_idr=mark_delta,
            )
    mutable.positions = updated


def _update_daily_stop(
    mutable: _MutablePath,
    session_input: PolicyPortfolioSessionInput,
    source_sha256: str,
    *,
    occurred_at: datetime,
) -> None:
    loss = max(-mutable.realized_pnl, 0)
    limit = _fraction_idr(
        session_input.policy.starting_capital_idr,
        session_input.policy.daily_loss_stop_fraction,
    )
    if loss < limit or mutable.daily_stop:
        return
    mutable.daily_stop = True
    _append_event(
        mutable,
        session=session_input.session,
        event_type="DAILY_STOP_LATCHED",
        occurred_at=occurred_at,
        source_artifact_sha256=source_sha256,
        reason_codes=("DAILY_REALIZED_LOSS_STOP",),
    )
    for commitment in tuple(mutable.commitments):
        _cancel_commitment(
            mutable,
            session_input,
            commitment,
            "DAILY_REALIZED_LOSS_STOP",
            occurred_at=occurred_at,
        )


def _poison_path(
    mutable: _MutablePath,
    *,
    session: date,
    reason: str,
    source_sha256: str,
    occurred_at: datetime | None = None,
    ticker: str | None = None,
) -> None:
    if reason not in mutable.path_reasons:
        mutable.path_reasons.append(reason)
    mutable.path_status = "NOT_ESTIMABLE_FROM_SESSION"
    _append_event(
        mutable,
        session=session,
        event_type="PATH_NOT_ESTIMABLE",
        occurred_at=occurred_at or session_close_at(session),
        ticker=ticker,
        source_artifact_sha256=source_sha256,
        reason_codes=(reason, "NOT_ESTIMABLE_FROM_SESSION"),
    )


def _append_event(
    mutable: _MutablePath,
    *,
    session: date,
    event_type: PolicyEventType,
    occurred_at: datetime,
    source_artifact_sha256: str,
    ticker: str | None = None,
    candidate_input_sha256: str | None = None,
    gate_results: tuple[PolicyGateResult, ...] = (),
    reason_codes: tuple[str, ...] = (),
    settled_cash_delta_idr: int = 0,
    sale_receivable_delta_idr: int = 0,
    purchase_payable_delta_idr: int = 0,
    reserved_cash_delta_idr: int = 0,
    realized_pnl_delta_idr: int = 0,
    planned_risk_delta_idr: int = 0,
    gross_exposure_delta_idr: int = 0,
    quantity_delta_shares: int = 0,
) -> PolicyPortfolioTransitionEvent:
    sequence = len(mutable.events) + 1
    payload = {
        "decision_role": mutable.role,
        "session": session,
        "event_sequence": sequence,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "ticker": ticker,
        "candidate_input_sha256": candidate_input_sha256,
        "source_artifact_sha256": source_artifact_sha256,
        "gate_results": gate_results,
        "reason_codes": tuple(dict.fromkeys(reason_codes)),
        "settled_cash_delta_idr": settled_cash_delta_idr,
        "sale_receivable_delta_idr": sale_receivable_delta_idr,
        "purchase_payable_delta_idr": purchase_payable_delta_idr,
        "reserved_cash_delta_idr": reserved_cash_delta_idr,
        "realized_pnl_delta_idr": realized_pnl_delta_idr,
        "planned_risk_delta_idr": planned_risk_delta_idr,
        "gross_exposure_delta_idr": gross_exposure_delta_idr,
        "quantity_delta_shares": quantity_delta_shares,
    }
    event_id = canonical_policy_artifact_id(
        "PPEVT",
        {
            "decision_role": mutable.role,
            "session": session.isoformat(),
            "event_sequence": sequence,
            "event_type": event_type,
            "occurred_at": _utc_iso(occurred_at),
            "ticker": ticker,
            "candidate_input_sha256": candidate_input_sha256,
            "source_artifact_sha256": source_artifact_sha256,
            "gate_result_sha256s": [
                canonical_sha256(item) for item in gate_results
            ],
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "money_deltas": [
                settled_cash_delta_idr,
                sale_receivable_delta_idr,
                purchase_payable_delta_idr,
                reserved_cash_delta_idr,
                realized_pnl_delta_idr,
                planned_risk_delta_idr,
                gross_exposure_delta_idr,
                quantity_delta_shares,
            ],
        },
    )
    event = PolicyPortfolioTransitionEvent(event_id=event_id, **payload)
    mutable.events.append(event)
    return event


def _make_payload(
    *,
    session: date,
    state_as_of: datetime,
    regime: RegimeName,
    path_status: PathStatus,
    path_reasons: tuple[str, ...],
    settled_cash: int,
    sale_receivables: tuple[PolicySettlementLeg, ...],
    purchase_payables: tuple[PolicySettlementLeg, ...],
    commitments: tuple[PolicyCommitment, ...],
    positions: tuple[PolicyPosition, ...],
    reentry_blocks: tuple[PolicyTickerReentryBlock, ...],
    realized_pnl: int,
    daily_stop_latched: bool,
) -> PolicyPortfolioStatePayload:
    ordered_sale = tuple(
        sorted(
            sale_receivables,
            key=lambda item: (
                item.settlement_session,
                item.ticker,
                item.settlement_leg_id,
            ),
        )
    )
    ordered_purchase = tuple(
        sorted(
            purchase_payables,
            key=lambda item: (
                item.settlement_session,
                item.ticker,
                item.settlement_leg_id,
            ),
        )
    )
    ordered_commitments = tuple(
        sorted(commitments, key=lambda item: item.priority_key)
    )
    ordered_positions = tuple(
        sorted(positions, key=lambda item: (item.ticker, item.position_id))
    )
    ordered_blocks = tuple(
        sorted(reentry_blocks, key=lambda item: item.ticker)
    )
    sale_total = sum(item.amount_idr for item in ordered_sale)
    purchase_total = sum(item.amount_idr for item in ordered_purchase)
    reserved = sum(
        item.reserved_debit_idr for item in ordered_commitments
    )
    marked = sum(item.marked_value_idr for item in ordered_positions)
    gross = marked + sum(
        item.planned_gross_idr for item in ordered_commitments
    )
    risk = sum(
        item.planned_risk_idr
        for item in (*ordered_positions, *ordered_commitments)
    )
    equity = settled_cash + sale_total - purchase_total + marked
    buying_power = max(settled_cash - purchase_total - reserved, 0)
    minimum = int(
        Decimal(APPROVED_STARTING_CAPITAL_IDR)
        * Decimal(str(APPROVED_MINIMUM_CASH_RESERVE_FRACTION))
    )
    try:
        return PolicyPortfolioStatePayload(
            session=session,
            state_as_of=state_as_of,
            effective_regime=regime,
            path_status=path_status,
            path_reason_codes=path_reasons,
            settled_cash_idr=settled_cash,
            sale_receivables=ordered_sale,
            purchase_payables=ordered_purchase,
            commitments=ordered_commitments,
            positions=ordered_positions,
            reentry_blocks=ordered_blocks,
            realized_pnl_today_idr=realized_pnl,
            daily_stop_latched=daily_stop_latched,
            sale_receivable_idr=sale_total,
            purchase_payable_idr=purchase_total,
            reserved_cash_idr=reserved,
            marked_holdings_value_idr=marked,
            accounting_equity_idr=equity,
            gross_exposure_idr=gross,
            planned_risk_idr=risk,
            portfolio_heat=quantize_ratio(
                risk,
                APPROVED_STARTING_CAPITAL_IDR,
            ),
            realized_loss_fraction=quantize_ratio(
                max(-realized_pnl, 0),
                APPROVED_STARTING_CAPITAL_IDR,
            ),
            buying_power_idr=buying_power,
            deployable_cash_above_minimum_idr=max(
                buying_power - minimum,
                0,
            ),
        )
    except ValueError as exc:
        raise ShadowContractError(
            "policy-portfolio state payload is invalid"
        ) from exc


def _make_state(
    *,
    role: DecisionRole,
    path_id: str,
    sequence: int,
    previous_state_sha256: str | None,
    genesis_sha256: str,
    manifest: ShadowProtocolManifest,
    policy: FrozenPolicyPortfolioPolicy,
    trading_calendar: TradingCalendar,
    session_input_sha256: str | None,
    transition_sha256: str | None,
    payload: PolicyPortfolioStatePayload,
) -> PolicyPortfolioSessionState:
    values = {
        "decision_role": role,
        "path_id": path_id,
        "state_sequence": sequence,
        "previous_state_sha256": previous_state_sha256,
        "genesis_sha256": genesis_sha256,
        "protocol_id": manifest.protocol_id,
        "component_id": manifest.component_id,
        "manifest_sha256": _required_hash(manifest),
        "policy_sha256": _required_hash(policy),
        "trading_calendar_sha256": trading_calendar.calendar_sha256,
        "session_input_sha256": session_input_sha256,
        "transition_sha256": transition_sha256,
        "payload": payload,
        "payload_sha256": _required_hash(payload),
    }
    state_id = canonical_policy_artifact_id(
        "PPSTATE",
        {
            "decision_role": role,
            "path_id": path_id,
            "state_sequence": sequence,
            "previous_state_sha256": previous_state_sha256,
            "genesis_sha256": genesis_sha256,
            "protocol_id": manifest.protocol_id,
            "manifest_sha256": _required_hash(manifest),
            "policy_sha256": _required_hash(policy),
            "trading_calendar_sha256": trading_calendar.calendar_sha256,
            "session_input_sha256": session_input_sha256,
            "transition_sha256": transition_sha256,
            "payload_sha256": _required_hash(payload),
        },
    )
    try:
        return PolicyPortfolioSessionState(state_id=state_id, **values)
    except ValueError as exc:
        raise ShadowContractError(
            f"{role} policy-portfolio state is invalid"
        ) from exc


def _decision_for(
    candidate: PolicyPortfolioCandidateInput,
    role: DecisionRole,
) -> ShadowDecision:
    return (
        candidate.pair_input.observation.control_decision
        if role == "CONTROL"
        else candidate.pair_input.observation.challenger_decision
    )


def _bar_for(
    pair: FixedNotionalPairInput,
    session: date,
) -> FixedNotionalMarketBar | None:
    return next(
        (item for item in pair.bar_series.bars if item.trade_date == session),
        None,
    )


def _activation_sessions(
    session_input: PolicyPortfolioSessionInput,
    candidate: PolicyPortfolioCandidateInput,
) -> tuple[date, ...]:
    signal_session = candidate.pair_input.observation.signal_at.astimezone(
        IDX_TIMEZONE
    ).date()
    return tuple(
        item
        for item in session_input.trading_calendar.sessions
        if signal_session < item <= session_input.manifest.fixed_terminal_date
    )[: session_input.manifest.labels.entry_validity_trading_days]


def _commitment_quantity_for_session(
    pair: FixedNotionalPairInput,
    commitment: PolicyCommitment,
    session: date,
) -> int | None:
    quantity = Decimal(commitment.quantity_shares)
    for action in pair.bar_series.corporate_action_policy.events:
        if (
            action.kind == "SPLIT"
            and commitment.signal_session
            < action.effective_date
            <= session
        ):
            quantity *= Decimal(str(action.quantity_factor))
    if (
        not quantity.is_finite()
        or quantity != quantity.to_integral_value()
        or quantity <= 0
    ):
        return None
    return int(quantity)


def _has_terminal_runway(
    session_input: PolicyPortfolioSessionInput,
    activation_sessions: tuple[date, ...],
) -> bool:
    required_activation = (
        session_input.manifest.labels.entry_validity_trading_days
    )
    if len(activation_sessions) != required_activation:
        return False
    calendar = session_input.trading_calendar.sessions
    worst_fill = activation_sessions[-1]
    try:
        fill_index = calendar.index(worst_fill)
    except ValueError:
        return False
    timeout_index = fill_index + POLICY_PORTFOLIO_PRIMARY_HORIZON
    settlement_index = timeout_index + session_input.policy.settlement_lag_sessions
    if settlement_index >= len(calendar):
        return False
    return (
        calendar[settlement_index]
        <= session_input.manifest.fixed_terminal_date
    )


def _post_fill_session_count(
    calendar: TradingCalendar,
    opened_session: date,
    current_session: date,
) -> int:
    return sum(
        opened_session < item <= current_session for item in calendar.sessions
    )


def _regime_limit(regime: RegimeName) -> int:
    return {
        "BULL": 3,
        "SIDEWAYS": 2,
        "BEAR_STRESS": 1,
        "UNKNOWN": 0,
    }[regime]


def _planned_risk(mutable: _MutablePath) -> int:
    return sum(
        item.planned_risk_idr
        for item in (*mutable.positions, *mutable.commitments)
    )


def _policy_gross_exposure(mutable: _MutablePath) -> int:
    return sum(item.marked_value_idr for item in mutable.positions) + sum(
        item.planned_gross_idr for item in mutable.commitments
    )


def _resource_snapshot(mutable: _MutablePath) -> _ResourceSnapshot:
    sector_counts: dict[str, int] = {}
    cluster_counts: dict[str, int] = {}
    for item in (*mutable.positions, *mutable.commitments):
        sector_counts[item.sector_id] = sector_counts.get(item.sector_id, 0) + 1
        cluster_counts[item.cluster_id] = (
            cluster_counts.get(item.cluster_id, 0) + 1
        )
    return _ResourceSnapshot(
        occupied_count=len(mutable.positions) + len(mutable.commitments),
        sector_counts=sector_counts,
        cluster_counts=cluster_counts,
        planned_risk=_planned_risk(mutable),
        gross_exposure=_policy_gross_exposure(mutable),
        accounting_equity=_accounting_equity(mutable),
        buying_power=max(
            mutable.settled_cash
            - sum(item.amount_idr for item in mutable.purchase_payables)
            - sum(item.reserved_debit_idr for item in mutable.commitments),
            0,
        ),
    )


def _accounting_equity(mutable: _MutablePath) -> int:
    return (
        mutable.settled_cash
        + sum(item.amount_idr for item in mutable.sale_receivables)
        - sum(item.amount_idr for item in mutable.purchase_payables)
        + sum(item.marked_value_idr for item in mutable.positions)
    )


def _fraction_idr(base_idr: int, fraction: float) -> int:
    value = Decimal(base_idr) * Decimal(str(fraction))
    if value != value.to_integral_value():
        raise ShadowContractError(
            "frozen policy fraction does not produce exact integer IDR"
        )
    return int(value)


def _minimum_cash_idr(policy: FrozenPolicyPortfolioPolicy) -> int:
    return _fraction_idr(
        policy.starting_capital_idr,
        policy.minimum_cash_reserve_fraction,
    )


def _verify_calendar_binding(
    manifest: ShadowProtocolManifest,
    trading_calendar: TradingCalendar,
) -> None:
    if (
        trading_calendar.calendar_sha256
        != manifest.trading_calendar_sha256
    ):
        raise ShadowContractError(
            "policy-portfolio calendar differs from manifest"
        )
    if manifest.fixed_terminal_date not in trading_calendar.sessions:
        raise ShadowContractError(
            "policy-portfolio fixed terminal is not an IDX session"
        )


def _verify_transition_journal(
    pre: PolicyPortfolioStatePayload,
    transition: PolicyPortfolioSessionTransition,
    post: PolicyPortfolioStatePayload,
) -> None:
    """Reconcile every integer-IDR/resource delta to the successor payload."""

    events = transition.events
    reconciliations = (
        (
            "settled cash",
            pre.settled_cash_idr
            + sum(item.settled_cash_delta_idr for item in events),
            post.settled_cash_idr,
        ),
        (
            "sale receivable",
            pre.sale_receivable_idr
            + sum(item.sale_receivable_delta_idr for item in events),
            post.sale_receivable_idr,
        ),
        (
            "purchase payable",
            pre.purchase_payable_idr
            + sum(item.purchase_payable_delta_idr for item in events),
            post.purchase_payable_idr,
        ),
        (
            "reserved cash",
            pre.reserved_cash_idr
            + sum(item.reserved_cash_delta_idr for item in events),
            post.reserved_cash_idr,
        ),
        (
            "planned risk",
            pre.planned_risk_idr
            + sum(item.planned_risk_delta_idr for item in events),
            post.planned_risk_idr,
        ),
        (
            "gross exposure",
            pre.gross_exposure_idr
            + sum(item.gross_exposure_delta_idr for item in events),
            post.gross_exposure_idr,
        ),
        (
            "position quantity",
            sum(
                item.current_quantity_shares for item in pre.positions
            )
            + sum(item.quantity_delta_shares for item in events),
            sum(
                item.current_quantity_shares for item in post.positions
            ),
        ),
        (
            "realized P&L today",
            sum(item.realized_pnl_delta_idr for item in events),
            post.realized_pnl_today_idr,
        ),
    )
    mismatches = tuple(
        label
        for label, expected, actual in reconciliations
        if expected != actual
    )
    if mismatches:
        raise ShadowContractError(
            "policy-portfolio transition journal mismatch: "
            + ", ".join(mismatches)
        )


def _verify_pair_manifest(
    manifest: ShadowProtocolManifest,
    pair_input: FixedNotionalPairInput,
) -> None:
    expected = (
        manifest.protocol_id,
        manifest.component_id,
        _required_hash(manifest),
        manifest.trading_calendar_sha256,
    )
    actual = (
        pair_input.manifest.protocol_id,
        pair_input.manifest.component_id,
        pair_input.manifest_sha256,
        pair_input.trading_calendar.calendar_sha256,
    )
    if actual != expected or _required_hash(pair_input.manifest) != expected[2]:
        raise ShadowContractError(
            "policy candidate PairInput belongs to another manifest: "
            f"actual={actual!r}, expected={expected!r}, "
            f"embedded={_required_hash(pair_input.manifest)!r}"
        )


def _required_hash(model: BaseModel) -> str:
    digest = canonical_sha256(model)
    if digest is None:  # pragma: no cover - non-optional by signature
        raise ShadowContractError("required canonical hash is unavailable")
    return digest


def _revalidate(model_type: type[BaseModel], value: BaseModel) -> BaseModel:
    try:
        return model_type.model_validate(
            value.model_dump(mode="python"),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ShadowContractError(
            f"{model_type.__name__} failed strict revalidation"
        ) from exc
