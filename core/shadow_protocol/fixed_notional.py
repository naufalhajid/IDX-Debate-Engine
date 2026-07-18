"""Exact, paired fixed-notional shadow evaluation for RS-P2-015.

This module is deliberately additive.  It does not call the one-lot
``evaluate_horizon`` implementation and it never constructs or reinterprets a
``ShadowOutcome``.  Every persisted price and money amount is strict integer
IDR; ratios are quantized to twelve decimal places before hashing.

The fixed-notional view isolates signal quality by giving CONTROL and
CHALLENGER the same frozen opportunity, snapshot, portfolio state, IDR
13,000,000 gross-notional budget, cost assumptions, and point-in-time
liquidity algorithm. Each side uses its own planned integer entry-high price.
If that side-specific geometry changes the shared eligibility classification,
the pair fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re
from typing import Annotated, Literal, Protocol, Sequence, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from .calendar import (
    IDX_TIMEZONE,
    SESSION_OPEN,
    TradingCalendar,
    session_close_at,
)
from .contracts import (
    ComponentID,
    CostAssumptions,
    LabelDefinition,
    ShadowContractError,
    ShadowDecision,
    ShadowObservation,
    ShadowProtocolManifest,
    canonical_sha256,
)
from .evidence import (
    CandidateEvent,
    CandidateSetManifest,
    FrozenSnapshot,
    RawCandidateSetCapture,
)
from .outcome_engine import (
    CorporateActionPolicy,
    canonical_corporate_action_events_sha256,
)
from .portfolio import (
    APPROVED_FIXED_NOTIONAL_IDR,
    APPROVED_MINIMUM_ADTV_IDR,
    APPROVED_RATIO_QUANTIZATION_DECIMAL_PLACES,
    EstimableMoney,
    EstimableRatio,
    FrozenPortfolioPolicy,
    PortfolioState,
    aggregate_bps_cost_idr,
    estimable_money,
    estimable_ratio,
    not_estimable_money,
    not_estimable_ratio,
    quantize_ratio,
    verify_portfolio_manifest_binding,
)


FIXED_NOTIONAL_POLICY_VERSION = "shadow-fixed-notional-policy-v1"
FIXED_NOTIONAL_LIQUIDITY_BAR_VERSION = (
    "shadow-fixed-notional-liquidity-bar-v1"
)
FIXED_NOTIONAL_LIQUIDITY_MEASUREMENT_VERSION = (
    "shadow-fixed-notional-liquidity-measurement-v1"
)
FIXED_NOTIONAL_LIQUIDITY_VERSION = "shadow-fixed-notional-liquidity-v1"
FIXED_NOTIONAL_MARKET_BAR_VERSION = "shadow-fixed-notional-market-bar-v1"
FIXED_NOTIONAL_BAR_SERIES_VERSION = "shadow-fixed-notional-bar-series-v1"
FIXED_NOTIONAL_PAIR_INPUT_VERSION = "shadow-fixed-notional-pair-input-v1"
FIXED_NOTIONAL_CASH_FLOW_VERSION = "shadow-fixed-notional-cash-flow-v1"
FIXED_NOTIONAL_HOLDING_VERSION = "shadow-fixed-notional-holding-v1"
FIXED_NOTIONAL_LIFECYCLE_VERSION = "shadow-fixed-notional-lifecycle-v1"
FIXED_NOTIONAL_PAIRED_RECORD_VERSION = "shadow-fixed-notional-paired-record-v1"
FIXED_NOTIONAL_REFERENCE_VERSION = "shadow-fixed-notional-reference-v1"

FIXED_NOTIONAL_CAPABILITY_STATUS = (
    "RS_P2_015_IMPLEMENTED_NOT_A1_ELIGIBLE"
)
FIXED_NOTIONAL_POLICY_CONFIG_PATH = "config/fixed-notional-policy-v1.json"
FIXED_NOTIONAL_IDR = APPROVED_FIXED_NOTIONAL_IDR
FIXED_NOTIONAL_LOT_SIZE = 100
FIXED_NOTIONAL_PRIMARY_HORIZON = 15
FIXED_NOTIONAL_MAX_SIZEABLE_PRICE_IDR = (
    FIXED_NOTIONAL_IDR // FIXED_NOTIONAL_LOT_SIZE
)
PARTICIPATION_NUMERATOR = 13
PARTICIPATION_DENOMINATOR = 10_000
LIQUIDITY_LOOKBACK_SESSIONS = 20

NOT_SIZEABLE_FIXED_NOTIONAL = "NOT_SIZEABLE_FIXED_NOTIONAL"
NOT_ESTIMABLE_ENTRY_CAPACITY = "NOT_ESTIMABLE_ENTRY_CAPACITY"
NOT_ESTIMABLE_EXIT_CAPACITY = "NOT_ESTIMABLE_EXIT_CAPACITY"

ACTIVATION_RULE = "FIRST_TRADING_SESSION_AFTER_SIGNAL"
HORIZON_CLOCK_RULE = "POST_FILL_SESSIONS_EXCLUDING_FILL_SESSION"
FILL_RULE = "BUY_LIMIT_OPEN_OR_INTRADAY_TOUCH_AT_ENTRY_HIGH"
GAP_RULE = "OBSERVED_OPEN_FOR_MARKETABLE_ENTRY_AND_GAP_EXITS"
ENTRY_GAP_THROUGH_STOP_RULE = (
    "FILL_AND_STOP_AT_OBSERVED_OPEN_USING_PLANNED_ENTRY_HIGH_RISK"
)
AMBIGUITY_RULE = "STOP_FIRST_AND_INTRADAY_ENTRY_TARGET_UNPROVEN"
CORPORATE_ACTION_RULE = (
    "RAW_AS_TRADED_BARS_WITH_FORWARD_SPLIT_AND_DIVIDEND_ADJUSTMENTS"
)
RIGHTS_TREATMENT_RULE = (
    "RIGHTS_INVALID_UNTIL_ELECTION_DELIVERY_AND_COST_RULES_ARE_FROZEN"
)
DIVIDEND_ENTITLEMENT_RULE = "POSITION_OPEN_BEFORE_EX_DATE"
UNFILLED_RULE = "EXPIRE_AFTER_ENTRY_VALIDITY_TRADING_DAYS"

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
LifecycleStatus: TypeAlias = Literal["MATURE", "NOT_ESTIMABLE", "PENDING"]
FixedFillStatus: TypeAlias = Literal[
    "EXPIRED_UNFILLED",
    "FILLED",
    "NOT_APPLICABLE",
    "NOT_ESTIMABLE",
    "PENDING",
]
FixedTerminalEvent: TypeAlias = Literal[
    "NO_ACTION",
    "NOT_ESTIMABLE",
    "PENDING",
    "STOP_FIRST",
    "TARGET_FIRST",
    "TIMEOUT",
    "UNFILLED",
]

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class _FixedNotionalModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class _EvaluationOnlyFixedNotionalArtifact(_FixedNotionalModel):
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False


class FrozenFixedNotionalPolicy(_EvaluationOnlyFixedNotionalArtifact):
    """Owner-approved FN1-FN8 rules derived from the RS-P2-014 policy."""

    contract_version: Literal["shadow-fixed-notional-policy-v1"] = (
        FIXED_NOTIONAL_POLICY_VERSION
    )
    policy_id: NonEmptyString
    phase2_capability_status: Literal[
        "RS_P2_015_IMPLEMENTED_NOT_A1_ELIGIBLE"
    ] = FIXED_NOTIONAL_CAPABILITY_STATUS
    portfolio_policy_id: NonEmptyString
    portfolio_policy_sha256: Sha256
    label_definition_sha256: Sha256
    cost_assumptions_sha256: Sha256
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    methodology_document_sha256: Sha256

    currency: Literal["IDR"] = "IDR"
    fixed_notional_idr: Literal[13_000_000] = FIXED_NOTIONAL_IDR
    lot_size_shares: Literal[100] = FIXED_NOTIONAL_LOT_SIZE
    sizing_price_basis: Literal[
        "PLANNED_ENTRY_HIGH_INTEGER_IDR_AT_SIGNAL"
    ] = "PLANNED_ENTRY_HIGH_INTEGER_IDR_AT_SIGNAL"
    sizing_price_pair_rule: Literal[
        "PER_SIDE_ENTRY_HIGH_WITH_SHARED_ELIGIBILITY_CLASSIFICATION"
    ] = "PER_SIDE_ENTRY_HIGH_WITH_SHARED_ELIGIBILITY_CLASSIFICATION"
    notional_semantics: Literal[
        "GROSS_ENTRY_NOTIONAL_BEFORE_COSTS"
    ] = "GROSS_ENTRY_NOTIONAL_BEFORE_COSTS"
    lot_rounding_rule: Literal[
        "FLOOR_TO_WHOLE_BOARD_LOTS_WITHOUT_EXCEEDING_GROSS_NOTIONAL"
    ] = "FLOOR_TO_WHOLE_BOARD_LOTS_WITHOUT_EXCEEDING_GROSS_NOTIONAL"
    residual_cash_rule: Literal[
        "IDLE_ZERO_RETURN_CASH_IN_FIXED_SLEEVE"
    ] = "IDLE_ZERO_RETURN_CASH_IN_FIXED_SLEEVE"
    opportunity_return_denominator_idr: Literal[13_000_000] = (
        FIXED_NOTIONAL_IDR
    )
    cost_application_rounding_rule: Literal[
        "AGGREGATE_APPLICABLE_BPS_THEN_CEIL_INTEGER_IDR"
    ] = "AGGREGATE_APPLICABLE_BPS_THEN_CEIL_INTEGER_IDR"
    ratio_quantization_decimal_places: Literal[12] = (
        APPROVED_RATIO_QUANTIZATION_DECIMAL_PLACES
    )
    ratio_quantization_rounding_mode: Literal["ROUND_HALF_EVEN"] = (
        "ROUND_HALF_EVEN"
    )

    liquidity_lookback_sessions: Literal[20] = LIQUIDITY_LOOKBACK_SESSIONS
    liquidity_measure_basis: Literal[
        "MEAN_INTEGER_CLOSE_X_INTEGER_VOLUME_LAST_20_COMPLETED_SESSIONS"
    ] = "MEAN_INTEGER_CLOSE_X_INTEGER_VOLUME_LAST_20_COMPLETED_SESSIONS"
    minimum_adtv20_idr: Literal[10_000_000_000] = (
        APPROVED_MINIMUM_ADTV_IDR
    )
    participation_numerator: Literal[13] = PARTICIPATION_NUMERATOR
    participation_denominator: Literal[10_000] = PARTICIPATION_DENOMINATOR
    participation_evidence_class: Literal["DERIVED_NOT_CALIBRATED"] = (
        "DERIVED_NOT_CALIBRATED"
    )
    capacity_rule: Literal["ALL_OR_NONE"] = "ALL_OR_NONE"
    capacity_rounding_rule: Literal[
        "FLOOR_TO_WHOLE_BOARD_LOTS"
    ] = "FLOOR_TO_WHOLE_BOARD_LOTS"
    entry_capacity_reason_code: Literal[
        "NOT_ESTIMABLE_ENTRY_CAPACITY"
    ] = NOT_ESTIMABLE_ENTRY_CAPACITY
    exit_capacity_reason_code: Literal[
        "NOT_ESTIMABLE_EXIT_CAPACITY"
    ] = NOT_ESTIMABLE_EXIT_CAPACITY
    high_price_reason_code: Literal[
        "NOT_SIZEABLE_FIXED_NOTIONAL"
    ] = NOT_SIZEABLE_FIXED_NOTIONAL

    non_action_opportunity_pnl_rule: Literal[
        "EXACT_ZERO_IDR"
    ] = "EXACT_ZERO_IDR"
    non_action_sleeve_return_rule: Literal[
        "EXACT_ZERO"
    ] = "EXACT_ZERO"
    non_action_trade_metrics_rule: Literal[
        "NOT_ESTIMABLE"
    ] = "NOT_ESTIMABLE"
    unfilled_metrics_rule: Literal[
        "NOT_ESTIMABLE_NOT_ZERO"
    ] = "NOT_ESTIMABLE_NOT_ZERO"
    primary_nav_horizon_trading_days: Literal[15] = (
        FIXED_NOTIONAL_PRIMARY_HORIZON
    )
    secondary_horizon_nav_rule: Literal[
        "EXCLUDED_FROM_RS_P2_017_NAV"
    ] = "EXCLUDED_FROM_RS_P2_017_NAV"
    settlement_lag_sessions: Literal[2] = 2

    # FN-N1 through FN-N3 are schema-bound audit labels, not prose defaults.
    effective_universe_max_price_idr: Literal[130_000] = (
        FIXED_NOTIONAL_MAX_SIZEABLE_PRICE_IDR
    )
    effective_universe_boundary_note: Literal[
        "FN_N1_GT_130000_EXCLUDED_IDENTICALLY"
    ] = "FN_N1_GT_130000_EXCLUDED_IDENTICALLY"
    cash_debit_note: Literal[
        "FN_N2_ENTRY_DEBIT_MAY_EXCEED_13000000_BY_SEPARATE_ENTRY_COST"
    ] = "FN_N2_ENTRY_DEBIT_MAY_EXCEED_13000000_BY_SEPARATE_ENTRY_COST"
    exit_censoring_note: Literal[
        "FN_N3_EXIT_CAPACITY_REASON_COUNT_REQUIRED_IN_RS_P2_018"
    ] = "FN_N3_EXIT_CAPACITY_REASON_COUNT_REQUIRED_IN_RS_P2_018"

    @model_validator(mode="after")
    def verify_policy_derivation(self) -> FrozenFixedNotionalPolicy:
        if (
            self.fixed_notional_idr // self.lot_size_shares
            != self.effective_universe_max_price_idr
        ):
            raise ValueError("fixed-notional effective-universe boundary mismatch")
        if (
            self.participation_numerator * APPROVED_MINIMUM_ADTV_IDR
            != self.participation_denominator * APPROVED_FIXED_NOTIONAL_IDR
        ):
            raise ValueError("participation fraction is not the owner derivation")
        return self


class FixedNotionalLiquidityBar(_FixedNotionalModel):
    """One integer price-volume observation known at a point in time."""

    contract_version: Literal[
        "shadow-fixed-notional-liquidity-bar-v1"
    ] = FIXED_NOTIONAL_LIQUIDITY_BAR_VERSION
    trade_date: date
    close_price_idr: StrictPositiveInt
    volume_shares: StrictNonNegativeInt
    available_at: datetime
    source_record_sha256: Sha256

    @field_validator("available_at")
    @classmethod
    def require_aware_available_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("liquidity available_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_publication(self) -> FixedNotionalLiquidityBar:
        if self.available_at < session_close_at(self.trade_date):
            raise ValueError("liquidity bar cannot be available before session close")
        return self

    @property
    def turnover_idr(self) -> int:
        return self.close_price_idr * self.volume_shares


class FixedNotionalLiquidityMeasurement(_FixedNotionalModel):
    """Exact ADTV20 rational and participation-cap evidence for one session."""

    contract_version: Literal[
        "shadow-fixed-notional-liquidity-measurement-v1"
    ] = FIXED_NOTIONAL_LIQUIDITY_MEASUREMENT_VERSION
    measurement_role: Literal["ENTRY", "EXIT"]
    capacity_session: date
    measured_at: datetime
    lookback_sessions: Literal[20] = LIQUIDITY_LOOKBACK_SESSIONS
    bars: tuple[FixedNotionalLiquidityBar, ...] = Field(
        min_length=LIQUIDITY_LOOKBACK_SESSIONS,
        max_length=LIQUIDITY_LOOKBACK_SESSIONS,
    )
    bar_record_sha256s: tuple[Sha256, ...] = Field(
        min_length=LIQUIDITY_LOOKBACK_SESSIONS,
        max_length=LIQUIDITY_LOOKBACK_SESSIONS,
    )
    turnover_sum_idr: StrictNonNegativeInt
    adtv20_numerator_idr: StrictNonNegativeInt
    adtv20_denominator: Literal[20] = LIQUIDITY_LOOKBACK_SESSIONS
    minimum_adtv20_idr: Literal[10_000_000_000] = (
        APPROVED_MINIMUM_ADTV_IDR
    )
    minimum_adtv20_passed: bool
    participation_numerator: Literal[13] = PARTICIPATION_NUMERATOR
    participation_denominator: Literal[10_000] = PARTICIPATION_DENOMINATOR
    capacity_notional_numerator_idr: StrictNonNegativeInt
    capacity_notional_denominator: Literal[200_000] = (
        LIQUIDITY_LOOKBACK_SESSIONS * PARTICIPATION_DENOMINATOR
    )

    @field_validator("measured_at")
    @classmethod
    def require_aware_measured_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("liquidity measured_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_exact_measurement(self) -> FixedNotionalLiquidityMeasurement:
        dates = tuple(item.trade_date for item in self.bars)
        if dates != tuple(sorted(dates)) or len(set(dates)) != len(dates):
            raise ValueError("liquidity lookback dates must be ordered and unique")
        if any(item.available_at > self.measured_at for item in self.bars):
            raise ValueError("liquidity measurement uses future source data")
        capacity_open = datetime.combine(
            self.capacity_session,
            SESSION_OPEN,
            tzinfo=IDX_TIMEZONE,
        )
        if self.measured_at >= capacity_open:
            raise ValueError(
                "liquidity measurement must precede capacity-session open"
            )
        if any(item.available_at >= capacity_open for item in self.bars):
            raise ValueError(
                "liquidity source must precede capacity-session open"
            )
        if any(item.trade_date >= self.capacity_session for item in self.bars):
            raise ValueError("liquidity lookback must precede capacity session")
        expected_hashes = tuple(_required_hash(item) for item in self.bars)
        if self.bar_record_sha256s != expected_hashes:
            raise ValueError("liquidity bar hash sequence mismatch")
        expected_turnover = sum(item.turnover_idr for item in self.bars)
        if (
            self.turnover_sum_idr != expected_turnover
            or self.adtv20_numerator_idr != expected_turnover
        ):
            raise ValueError("liquidity turnover/ADTV numerator mismatch")
        expected_passed = (
            expected_turnover
            >= self.minimum_adtv20_idr * self.adtv20_denominator
        )
        if self.minimum_adtv20_passed is not expected_passed:
            raise ValueError("minimum ADTV20 classification mismatch")
        if (
            self.capacity_notional_numerator_idr
            != expected_turnover * self.participation_numerator
        ):
            raise ValueError("liquidity capacity numerator mismatch")
        return self

    def supports_gross_notional(self, gross_notional_idr: int) -> bool:
        if type(gross_notional_idr) is not int or gross_notional_idr < 0:
            raise ValueError("gross notional must be strict non-negative integer IDR")
        return (
            gross_notional_idr * self.capacity_notional_denominator
            <= self.capacity_notional_numerator_idr
        )

    def capacity_lots_at_price(
        self,
        price_idr: int,
        *,
        lot_size_shares: int = FIXED_NOTIONAL_LOT_SIZE,
    ) -> int:
        if type(price_idr) is not int or price_idr <= 0:
            raise ValueError("capacity price must be strict positive integer IDR")
        if type(lot_size_shares) is not int or lot_size_shares <= 0:
            raise ValueError("lot size must be a strict positive integer")
        denominator = (
            self.capacity_notional_denominator
            * price_idr
            * lot_size_shares
        )
        return self.capacity_notional_numerator_idr // denominator


class FixedNotionalLiquidityRecord(_EvaluationOnlyFixedNotionalArtifact):
    """Shared entry and point-in-time exit-capacity evidence for one pair."""

    contract_version: Literal["shadow-fixed-notional-liquidity-v1"] = (
        FIXED_NOTIONAL_LIQUIDITY_VERSION
    )
    record_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    observation_id: NonEmptyString
    observation_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    portfolio_state_sha256: Sha256
    fixed_notional_policy_sha256: Sha256
    signal_at: datetime
    source_id: NonEmptyString
    source_definition_sha256: Sha256
    source_as_of: datetime
    captured_at: datetime
    previous_record_sha256: Sha256 | None = None
    entry_measurement: FixedNotionalLiquidityMeasurement
    exit_measurements: tuple[FixedNotionalLiquidityMeasurement, ...] = ()
    payload_sha256: Sha256
    source_record_sha256: Sha256

    @field_validator("signal_at", "source_as_of", "captured_at")
    @classmethod
    def require_aware_times(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("liquidity-record times must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_record(self) -> FixedNotionalLiquidityRecord:
        if self.entry_measurement.measurement_role != "ENTRY":
            raise ValueError("entry measurement must have ENTRY role")
        if self.entry_measurement.measured_at > self.signal_at:
            raise ValueError("entry liquidity must be known by signal time")
        if any(item.measurement_role != "EXIT" for item in self.exit_measurements):
            raise ValueError("exit measurements must have EXIT role")
        sessions = tuple(item.capacity_session for item in self.exit_measurements)
        if sessions != tuple(sorted(sessions)) or len(set(sessions)) != len(sessions):
            raise ValueError("exit liquidity sessions must be ordered and unique")
        measurements = (self.entry_measurement, *self.exit_measurements)
        if self.source_as_of != max(
            item.available_at
            for measurement in measurements
            for item in measurement.bars
        ):
            raise ValueError("liquidity source_as_of is not the latest source vintage")
        if self.source_as_of > self.captured_at:
            raise ValueError("liquidity capture precedes source vintage")
        if any(
            item.measured_at > self.captured_at for item in measurements
        ):
            raise ValueError("liquidity capture precedes a measurement")
        expected_payload = canonical_fixed_notional_liquidity_payload_sha256(
            self.entry_measurement,
            self.exit_measurements,
        )
        if self.payload_sha256 != expected_payload:
            raise ValueError("liquidity payload SHA-256 mismatch")
        expected_source = canonical_fixed_notional_liquidity_source_sha256(
            source_id=self.source_id,
            source_definition_sha256=self.source_definition_sha256,
            source_as_of=self.source_as_of,
            payload_sha256=self.payload_sha256,
            previous_record_sha256=self.previous_record_sha256,
        )
        if self.source_record_sha256 != expected_source:
            raise ValueError("liquidity source-record SHA-256 mismatch")
        expected_id = canonical_fixed_notional_liquidity_record_id(
            protocol_id=self.protocol_id,
            manifest_sha256=self.manifest_sha256,
            observation_id=self.observation_id,
            portfolio_state_sha256=self.portfolio_state_sha256,
            fixed_notional_policy_sha256=self.fixed_notional_policy_sha256,
            source_record_sha256=self.source_record_sha256,
        )
        if self.record_id != expected_id:
            raise ValueError("liquidity record ID mismatch")
        return self

    def exit_measurement_for(
        self,
        session: date,
    ) -> FixedNotionalLiquidityMeasurement | None:
        return next(
            (
                item
                for item in self.exit_measurements
                if item.capacity_session == session
            ),
            None,
        )


class FixedNotionalMarketBar(_FixedNotionalModel):
    """Strict integer-IDR OHLCV input for the new evaluator."""

    contract_version: Literal["shadow-fixed-notional-market-bar-v1"] = (
        FIXED_NOTIONAL_MARKET_BAR_VERSION
    )
    trade_date: date
    open_price_idr: StrictPositiveInt
    high_price_idr: StrictPositiveInt
    low_price_idr: StrictPositiveInt
    close_price_idr: StrictPositiveInt
    volume_shares: StrictNonNegativeInt
    dividend_per_share_idr: StrictNonNegativeInt = 0

    @model_validator(mode="after")
    def verify_ohlc(self) -> FixedNotionalMarketBar:
        if self.low_price_idr > min(self.open_price_idr, self.close_price_idr):
            raise ValueError("bar low exceeds open/close")
        if self.high_price_idr < max(self.open_price_idr, self.close_price_idr):
            raise ValueError("bar high is below open/close")
        return self


class FixedNotionalBarSeries(_EvaluationOnlyFixedNotionalArtifact):
    """Complete integer-price outcome source bounded by a source vintage."""

    contract_version: Literal["shadow-fixed-notional-bar-series-v1"] = (
        FIXED_NOTIONAL_BAR_SERIES_VERSION
    )
    ticker: CanonicalTicker
    snapshot_id: NonEmptyString
    snapshot_sha256: Sha256
    source_id: NonEmptyString
    source_definition_sha256: Sha256
    source_sha256: Sha256
    source_as_of: datetime
    previous_source_sha256: Sha256 | None = None
    requested_start: date
    requested_end: date
    bars: tuple[FixedNotionalMarketBar, ...]
    bars_sha256: Sha256
    bar_record_sha256s: tuple[Sha256, ...]
    corporate_action_policy: CorporateActionPolicy

    @field_validator("source_as_of")
    @classmethod
    def require_aware_source_as_of(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("bar-series source_as_of must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_series(self) -> FixedNotionalBarSeries:
        if self.requested_end < self.requested_start:
            raise ValueError("bar-series requested range is inverted")
        dates = tuple(item.trade_date for item in self.bars)
        if dates != tuple(sorted(dates)) or len(set(dates)) != len(dates):
            raise ValueError("fixed-notional bar dates must be ordered and unique")
        if any(
            item.trade_date < self.requested_start
            or item.trade_date > self.requested_end
            for item in self.bars
        ):
            raise ValueError("bar lies outside requested fixed-notional range")
        if self.bars and self.source_as_of < session_close_at(self.bars[-1].trade_date):
            raise ValueError("bar source vintage precedes final session close")
        expected_records = tuple(_required_hash(item) for item in self.bars)
        if self.bar_record_sha256s != expected_records:
            raise ValueError("fixed-notional bar-record hash sequence mismatch")
        expected_bars = _sha256(
            _canonical_mapping_bytes(
                {
                    "bar_record_sha256s": list(expected_records),
                    "snapshot_sha256": self.snapshot_sha256,
                    "ticker": self.ticker,
                }
            )
        )
        if self.bars_sha256 != expected_bars:
            raise ValueError("fixed-notional bars SHA-256 mismatch")
        expected_source = canonical_fixed_notional_bar_source_sha256(
            source_id=self.source_id,
            source_definition_sha256=self.source_definition_sha256,
            source_as_of=self.source_as_of,
            previous_source_sha256=self.previous_source_sha256,
            requested_start=self.requested_start,
            requested_end=self.requested_end,
            ticker=self.ticker,
            snapshot_sha256=self.snapshot_sha256,
            bars_sha256=self.bars_sha256,
            corporate_action_policy_sha256=(
                self.corporate_action_policy.policy_sha256
            ),
            corporate_action_events_sha256=(
                self.corporate_action_policy.events_sha256
            ),
        )
        if self.source_sha256 != expected_source:
            raise ValueError("fixed-notional bar-source hash mismatch")
        if any(
            event.ticker != self.ticker
            for event in self.corporate_action_policy.events
        ):
            raise ValueError("corporate-action ticker differs from bar series")
        for bar in self.bars:
            try:
                expected_dividend = sum(
                    _integer_nonnegative(
                        event.cash_per_share,
                        "corporate-action dividend cash",
                    )
                    for event in self.corporate_action_policy.events
                    if (
                        event.kind == "DIVIDEND"
                        and event.effective_date == bar.trade_date
                    )
                )
            except ValueError as exc:
                raise ValueError(
                    "corporate-action dividend is not integer IDR"
                ) from exc
            if bar.dividend_per_share_idr != expected_dividend:
                raise ValueError(
                    "bar dividend differs from corporate-action events"
                )
        return self


class FixedNotionalSizingPlan(_FixedNotionalModel):
    """One side's mechanically derived lot plan under the shared IDR budget."""

    decision_role: DecisionRole
    sizing_price_idr: StrictPositiveInt | None
    desired_lots: StrictNonNegativeInt | None
    quantity_shares: StrictNonNegativeInt | None
    gross_entry_notional_idr: StrictNonNegativeInt | None
    residual_idle_cash_idr: StrictNonNegativeInt | None
    classification: Literal[
        "ELIGIBLE",
        "NO_ACTION",
        "NOT_ESTIMABLE_ENTRY_CAPACITY",
        "NOT_SIZEABLE_FIXED_NOTIONAL",
    ]

    @model_validator(mode="after")
    def verify_sizing_plan(self) -> FixedNotionalSizingPlan:
        values = (
            self.sizing_price_idr,
            self.desired_lots,
            self.quantity_shares,
            self.gross_entry_notional_idr,
            self.residual_idle_cash_idr,
        )
        if self.classification == "NO_ACTION":
            if any(value is not None for value in values):
                raise ValueError("NO_ACTION sizing plan cannot carry sizing values")
            return self
        if any(value is None for value in values):
            raise ValueError("sized classification requires complete sizing values")
        price = int(self.sizing_price_idr)
        lots = int(self.desired_lots)
        shares = int(self.quantity_shares)
        gross = int(self.gross_entry_notional_idr)
        residual = int(self.residual_idle_cash_idr)
        if lots != fixed_notional_lot_count(price):
            raise ValueError("desired lot count differs from mechanical floor")
        if shares != lots * FIXED_NOTIONAL_LOT_SIZE:
            raise ValueError("sizing share count differs from lot count")
        if gross != shares * price:
            raise ValueError("sizing gross notional arithmetic mismatch")
        if gross + residual != FIXED_NOTIONAL_IDR:
            raise ValueError("sizing gross plus residual does not equal sleeve")
        expected = (
            NOT_SIZEABLE_FIXED_NOTIONAL
            if lots == 0
            else self.classification
        )
        if lots == 0 and self.classification != expected:
            raise ValueError("zero-lot plan must be high-price exclusion")
        if lots > 0 and self.classification == NOT_SIZEABLE_FIXED_NOTIONAL:
            raise ValueError("positive-lot plan cannot be high-price exclusion")
        return self


class FixedNotionalPairInput(_EvaluationOnlyFixedNotionalArtifact):
    """One immutable economic input shared by CONTROL and CHALLENGER."""

    contract_version: Literal["shadow-fixed-notional-pair-input-v1"] = (
        FIXED_NOTIONAL_PAIR_INPUT_VERSION
    )
    pair_input_id: NonEmptyString
    frozen_at: datetime
    evaluation_cutoff: datetime
    manifest: ShadowProtocolManifest
    raw_capture: RawCandidateSetCapture
    candidate_set: CandidateSetManifest
    candidate: CandidateEvent
    snapshot: FrozenSnapshot
    portfolio_state: PortfolioState
    observation: ShadowObservation
    policy: FrozenFixedNotionalPolicy
    liquidity: FixedNotionalLiquidityRecord
    trading_calendar: TradingCalendar
    bar_series: FixedNotionalBarSeries
    manifest_sha256: Sha256
    raw_capture_sha256: Sha256
    candidate_set_sha256: Sha256
    candidate_sha256: Sha256
    snapshot_sha256: Sha256
    portfolio_state_sha256: Sha256
    observation_sha256: Sha256
    policy_sha256: Sha256
    liquidity_sha256: Sha256
    trading_calendar_sha256: Sha256
    bar_series_sha256: Sha256
    label_definition_sha256: Sha256
    cost_assumptions_sha256: Sha256
    control_sizing_plan: FixedNotionalSizingPlan
    challenger_sizing_plan: FixedNotionalSizingPlan
    shared_exclusion_reason: Literal[
        "NOT_ESTIMABLE_ENTRY_CAPACITY",
        "NOT_SIZEABLE_FIXED_NOTIONAL",
    ] | None = None

    @field_validator("frozen_at", "evaluation_cutoff")
    @classmethod
    def require_aware_pair_times(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("fixed-notional pair times must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_pair_input(self) -> FixedNotionalPairInput:
        artifacts = (
            self.manifest,
            self.raw_capture,
            self.candidate_set,
            self.candidate,
            self.snapshot,
            self.portfolio_state,
            self.observation,
            self.policy,
            self.liquidity,
            self.trading_calendar,
            self.bar_series,
        )
        expected_hashes = tuple(_required_hash(item) for item in artifacts)
        actual_hashes = (
            self.manifest_sha256,
            self.raw_capture_sha256,
            self.candidate_set_sha256,
            self.candidate_sha256,
            self.snapshot_sha256,
            self.portfolio_state_sha256,
            self.observation_sha256,
            self.policy_sha256,
            self.liquidity_sha256,
            self.trading_calendar_sha256,
            self.bar_series_sha256,
        )
        if actual_hashes != expected_hashes:
            raise ValueError("fixed-notional pair embedded hash mismatch")
        if self.label_definition_sha256 != _required_hash(self.manifest.labels):
            raise ValueError("fixed-notional label hash mismatch")
        if self.cost_assumptions_sha256 != _required_hash(self.manifest.costs):
            raise ValueError("fixed-notional cost hash mismatch")
        if self.evaluation_cutoff < self.observation.captured_at:
            raise ValueError("evaluation cutoff precedes paired observation")
        if self.bar_series.source_as_of > self.evaluation_cutoff:
            raise ValueError("bar source vintage follows evaluation cutoff")
        measurements = (
            self.liquidity.entry_measurement,
            *self.liquidity.exit_measurements,
        )
        if (
            self.liquidity.source_as_of > self.evaluation_cutoff
            or any(
                measurement.measured_at > self.evaluation_cutoff
                or any(
                    bar.available_at > self.evaluation_cutoff
                    for bar in measurement.bars
                )
                for measurement in measurements
            )
        ):
            raise ValueError("liquidity evidence follows evaluation cutoff")
        if self.frozen_at < max(
            self.observation.captured_at,
            self.liquidity.captured_at,
            self.bar_series.source_as_of,
        ):
            raise ValueError("pair freeze precedes an input capture")
        if self.frozen_at < self.evaluation_cutoff:
            raise ValueError("pair freeze precedes evaluation cutoff")
        _verify_pair_lineage(self)
        _verify_label_definition(self.manifest.labels)
        _verify_pair_sizing_fields(self)
        expected_id = canonical_fixed_notional_event_id(
            prefix="FNINPUT",
            payload={
                "artifact_sha256s": list(expected_hashes),
                "evaluation_cutoff": _utc_iso(self.evaluation_cutoff),
                "frozen_at": _utc_iso(self.frozen_at),
                "shared_exclusion_reason": self.shared_exclusion_reason,
            },
        )
        if self.pair_input_id != expected_id:
            raise ValueError("fixed-notional pair-input ID mismatch")
        return self


class FixedNotionalCashFlowRecord(_EvaluationOnlyFixedNotionalArtifact):
    """One exact economic cash-flow event for later RS-P2-017 consumption."""

    contract_version: Literal["shadow-fixed-notional-cash-flow-v1"] = (
        FIXED_NOTIONAL_CASH_FLOW_VERSION
    )
    cash_flow_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    observation_id: NonEmptyString
    observation_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    pair_input_sha256: Sha256
    portfolio_state_sha256: Sha256
    fixed_notional_policy_sha256: Sha256
    decision_sha256: Sha256
    primary_horizon_trading_days: Literal[15] = FIXED_NOTIONAL_PRIMARY_HORIZON
    rs_p2_017_eligible: Literal[True] = True
    decision_role: DecisionRole
    event_type: Literal["DIVIDEND_CREDIT", "ENTRY_DEBIT", "EXIT_CREDIT"]
    trade_session: date
    occurred_at: datetime
    settlement_session: date
    gross_amount_idr: StrictNonNegativeInt
    cost_idr: StrictNonNegativeInt
    net_cash_change_idr: StrictInt
    quantity_shares: StrictPositiveInt
    price_idr: StrictNonNegativeInt
    cash_availability: Literal[
        "RETURN_ATTRIBUTION_ON_EFFECTIVE_SESSION",
        "SETTLED_T_PLUS_2",
    ]

    @field_validator("occurred_at")
    @classmethod
    def require_aware_occurred_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("cash-flow occurred_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_cash_flow(self) -> FixedNotionalCashFlowRecord:
        if self.occurred_at.astimezone(IDX_TIMEZONE).date() != self.trade_session:
            raise ValueError("cash-flow time differs from trade session")
        if self.settlement_session < self.trade_session:
            raise ValueError("cash-flow settlement precedes event")
        expected = {
            "ENTRY_DEBIT": -(self.gross_amount_idr + self.cost_idr),
            "EXIT_CREDIT": self.gross_amount_idr - self.cost_idr,
            "DIVIDEND_CREDIT": self.gross_amount_idr,
        }[self.event_type]
        if self.net_cash_change_idr != expected:
            raise ValueError("cash-flow arithmetic mismatch")
        if self.event_type == "DIVIDEND_CREDIT":
            if self.cost_idr != 0 or self.price_idr != 0:
                raise ValueError("dividend cash flow cannot carry price/cost")
            if (
                self.cash_availability
                != "RETURN_ATTRIBUTION_ON_EFFECTIVE_SESSION"
                or self.settlement_session != self.trade_session
            ):
                raise ValueError("dividend attribution timing mismatch")
        else:
            if self.cash_availability != "SETTLED_T_PLUS_2":
                raise ValueError("trade cash flow must use T+2 settlement")
            if self.gross_amount_idr != (
                self.quantity_shares * self.price_idr
            ):
                raise ValueError("trade cash-flow gross arithmetic mismatch")
        expected_id = canonical_fixed_notional_event_id(
            prefix="FCF",
            payload={
                "decision_role": self.decision_role,
                "event_type": self.event_type,
                "gross_amount_idr": self.gross_amount_idr,
                "net_cash_change_idr": self.net_cash_change_idr,
                "occurred_at": _utc_iso(self.occurred_at),
                "quantity_shares": self.quantity_shares,
                "protocol_id": self.protocol_id,
                "component_id": self.component_id,
                "manifest_sha256": self.manifest_sha256,
                "observation_id": self.observation_id,
                "observation_sha256": self.observation_sha256,
                "pair_input_sha256": self.pair_input_sha256,
                "raw_event_id": self.raw_event_id,
                "ticker": self.ticker,
                "portfolio_state_sha256": self.portfolio_state_sha256,
                "fixed_notional_policy_sha256": (
                    self.fixed_notional_policy_sha256
                ),
                "decision_sha256": self.decision_sha256,
                "settlement_session": self.settlement_session.isoformat(),
                "trade_session": self.trade_session.isoformat(),
            },
        )
        if self.cash_flow_id != expected_id:
            raise ValueError("cash-flow ID mismatch")
        return self


class FixedNotionalHoldingRecord(_EvaluationOnlyFixedNotionalArtifact):
    """One exact holding transition, never a live order instruction."""

    contract_version: Literal["shadow-fixed-notional-holding-v1"] = (
        FIXED_NOTIONAL_HOLDING_VERSION
    )
    holding_event_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    observation_id: NonEmptyString
    observation_sha256: Sha256
    pair_input_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    portfolio_state_sha256: Sha256
    fixed_notional_policy_sha256: Sha256
    decision_sha256: Sha256
    primary_horizon_trading_days: Literal[15] = FIXED_NOTIONAL_PRIMARY_HORIZON
    rs_p2_017_eligible: Literal[True] = True
    decision_role: DecisionRole
    event_type: Literal["CLOSE", "OPEN", "SPLIT_ADJUSTMENT"]
    event_session: date
    occurred_at: datetime
    quantity_before_shares: StrictNonNegativeInt
    quantity_after_shares: StrictNonNegativeInt
    price_idr: StrictPositiveInt
    marked_value_after_idr: StrictNonNegativeInt

    @field_validator("occurred_at")
    @classmethod
    def require_aware_holding_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("holding event time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_holding(self) -> FixedNotionalHoldingRecord:
        if self.occurred_at.astimezone(IDX_TIMEZONE).date() != self.event_session:
            raise ValueError("holding event time differs from session")
        if self.marked_value_after_idr != (
            self.quantity_after_shares * self.price_idr
        ):
            raise ValueError("holding marked value mismatch")
        if self.event_type == "OPEN" and (
            self.quantity_before_shares != 0 or self.quantity_after_shares == 0
        ):
            raise ValueError("OPEN holding transition is invalid")
        if self.event_type == "CLOSE" and self.quantity_after_shares != 0:
            raise ValueError("CLOSE holding transition must end at zero")
        expected_id = canonical_fixed_notional_event_id(
            prefix="FHOLD",
            payload={
                "decision_role": self.decision_role,
                "event_session": self.event_session.isoformat(),
                "event_type": self.event_type,
                "occurred_at": _utc_iso(self.occurred_at),
                "protocol_id": self.protocol_id,
                "component_id": self.component_id,
                "manifest_sha256": self.manifest_sha256,
                "observation_id": self.observation_id,
                "observation_sha256": self.observation_sha256,
                "pair_input_sha256": self.pair_input_sha256,
                "price_idr": self.price_idr,
                "quantity_after_shares": self.quantity_after_shares,
                "quantity_before_shares": self.quantity_before_shares,
                "raw_event_id": self.raw_event_id,
                "ticker": self.ticker,
                "portfolio_state_sha256": self.portfolio_state_sha256,
                "fixed_notional_policy_sha256": (
                    self.fixed_notional_policy_sha256
                ),
                "decision_sha256": self.decision_sha256,
            },
        )
        if self.holding_event_id != expected_id:
            raise ValueError("holding-event ID mismatch")
        return self


class FixedNotionalLifecycle(_EvaluationOnlyFixedNotionalArtifact):
    """Primary 15-session exact-IDR result for one side."""

    contract_version: Literal["shadow-fixed-notional-lifecycle-v1"] = (
        FIXED_NOTIONAL_LIFECYCLE_VERSION
    )
    lifecycle_id: NonEmptyString
    pair_input_sha256: Sha256
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    observation_id: NonEmptyString
    observation_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    portfolio_state_sha256: Sha256
    fixed_notional_policy_sha256: Sha256
    decision_role: DecisionRole
    decision_sha256: Sha256
    horizon_trading_days: Literal[3, 5, 10, 15]
    primary_horizon: bool
    rs_p2_017_consumption_status: Literal[
        "ELIGIBLE_PRIMARY_15D",
        "EXCLUDED_NOT_ESTIMABLE",
        "NO_HOLDING_OR_CASH_FLOW",
        "SECONDARY_METRIC_ONLY",
    ]
    status: LifecycleStatus
    fill_status: FixedFillStatus
    terminal_event: FixedTerminalEvent
    signal_at: datetime
    evaluated_at: datetime
    maturity_at: datetime | None = None
    filled_at: datetime | None = None
    closed_at: datetime | None = None
    fill_time_precision: Literal["SESSION_ONLY", "SESSION_OPEN"] | None = None
    planned_geometry_sha256: Sha256 | None = None
    sizing_price_idr: StrictPositiveInt | None
    target_sleeve_idr: Literal[13_000_000] = FIXED_NOTIONAL_IDR
    desired_lots: StrictNonNegativeInt | None
    entry_lots: StrictPositiveInt | None = None
    entry_quantity_shares: StrictPositiveInt | None = None
    exit_quantity_shares: StrictPositiveInt | None = None
    fill_price_idr: StrictPositiveInt | None = None
    exit_price_idr: StrictPositiveInt | None = None
    gross_entry_notional_idr: StrictPositiveInt | None = None
    entry_cash_debit_idr: StrictPositiveInt | None = None
    residual_idle_cash_idr: StrictNonNegativeInt | None = None
    gross_exit_value_idr: StrictNonNegativeInt | None = None
    dividend_cash_idr: StrictNonNegativeInt | None = None
    entry_cost_idr: StrictNonNegativeInt | None = None
    exit_cost_idr: StrictNonNegativeInt | None = None
    total_cost_idr: StrictNonNegativeInt | None = None
    risk_capital_basis_idr: StrictPositiveInt | None = None
    net_pnl_idr: EstimableMoney
    sleeve_return: EstimableRatio
    acted_trade_return: EstimableRatio
    net_r: EstimableRatio
    same_bar_ambiguous: bool = False
    ambiguity_resolution: str | None = None
    bars_observed: StrictNonNegativeInt
    holding_records: tuple[FixedNotionalHoldingRecord, ...] = ()
    cash_flow_records: tuple[FixedNotionalCashFlowRecord, ...] = ()
    holding_record_sha256s: tuple[Sha256, ...] = ()
    cash_flow_record_sha256s: tuple[Sha256, ...] = ()
    reason_codes: tuple[NonEmptyString, ...] = Field(min_length=1)

    @field_validator("signal_at", "evaluated_at", "maturity_at", "filled_at", "closed_at")
    @classmethod
    def require_aware_lifecycle_times(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("lifecycle datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_lifecycle(self) -> FixedNotionalLifecycle:
        if self.reason_codes != tuple(dict.fromkeys(self.reason_codes)):
            raise ValueError("lifecycle reason codes must be unique")
        if self.evaluated_at < self.signal_at:
            raise ValueError("lifecycle evaluation precedes signal")
        for label, value in (
            ("maturity", self.maturity_at),
            ("fill", self.filled_at),
            ("close", self.closed_at),
        ):
            if value is not None and value < self.signal_at:
                raise ValueError(f"lifecycle {label} precedes signal")
            if value is not None and value > self.evaluated_at:
                raise ValueError(
                    f"lifecycle {label} follows evaluation cutoff"
                )
        if self.status == "MATURE" and self.maturity_at is None:
            raise ValueError("mature lifecycle requires maturity time")
        if self.status != "MATURE" and self.maturity_at is not None:
            raise ValueError("non-mature lifecycle cannot carry maturity time")
        if self.fill_status == "FILLED":
            if self.filled_at is None:
                raise ValueError("filled lifecycle requires fill time")
            if (
                self.maturity_at is not None
                and self.maturity_at < self.filled_at
            ):
                raise ValueError("lifecycle maturity precedes fill")
            if self.closed_at is not None and self.closed_at < self.filled_at:
                raise ValueError("lifecycle close precedes fill")
        elif self.filled_at is not None or self.closed_at is not None:
            raise ValueError("unfilled lifecycle cannot carry fill/close time")
        if self.closed_at is not None and self.maturity_at != self.closed_at:
            raise ValueError("closed lifecycle maturity must equal close time")
        if self.total_cost_idr is not None and self.total_cost_idr != (
            (self.entry_cost_idr or 0) + (self.exit_cost_idr or 0)
        ):
            raise ValueError("lifecycle total-cost arithmetic mismatch")
        if self.gross_entry_notional_idr is not None:
            if self.entry_cost_idr is None or self.entry_cash_debit_idr != (
                self.gross_entry_notional_idr + self.entry_cost_idr
            ):
                raise ValueError("FN-N2 entry cash-debit arithmetic mismatch")
        elif self.entry_cash_debit_idr is not None:
            raise ValueError("entry cash debit requires gross entry notional")
        if (
            self.gross_entry_notional_idr is not None
            and self.residual_idle_cash_idr is not None
            and self.gross_entry_notional_idr + self.residual_idle_cash_idr
            != self.target_sleeve_idr
        ):
            raise ValueError("fixed sleeve does not reconcile to gross plus residual")
        if self.fill_status == "FILLED":
            required_entry = (
                self.desired_lots,
                self.entry_lots,
                self.entry_quantity_shares,
                self.fill_price_idr,
                self.gross_entry_notional_idr,
                self.entry_cash_debit_idr,
                self.residual_idle_cash_idr,
                self.entry_cost_idr,
                self.risk_capital_basis_idr,
            )
            if any(value is None for value in required_entry):
                raise ValueError("filled lifecycle has incomplete entry sizing")
            if self.entry_lots != self.desired_lots:
                raise ValueError("entry lots differ from frozen desired lots")
            if self.entry_quantity_shares != self.entry_lots * FIXED_NOTIONAL_LOT_SIZE:
                raise ValueError("entry quantity differs from exact board lots")
            if self.gross_entry_notional_idr != (
                self.entry_quantity_shares * self.fill_price_idr
            ):
                raise ValueError("entry gross differs from exact fill quantity")
            if self.gross_entry_notional_idr > self.target_sleeve_idr:
                raise ValueError("entry gross exceeds fixed-notional sleeve")
        if self.holding_record_sha256s != tuple(
            _required_hash(item) for item in self.holding_records
        ):
            raise ValueError("lifecycle holding-record hash sequence mismatch")
        if self.cash_flow_record_sha256s != tuple(
            _required_hash(item) for item in self.cash_flow_records
        ):
            raise ValueError("lifecycle cash-flow hash sequence mismatch")
        if self.primary_horizon is not (
            self.horizon_trading_days == FIXED_NOTIONAL_PRIMARY_HORIZON
        ):
            raise ValueError("primary-horizon classification mismatch")
        if not self.primary_horizon:
            if self.holding_records or self.cash_flow_records:
                raise ValueError(
                    "secondary horizons cannot create NAV holding/cash-flow records"
                )
            if (
                self.holding_record_sha256s
                or self.cash_flow_record_sha256s
                or self.rs_p2_017_consumption_status
                != "SECONDARY_METRIC_ONLY"
            ):
                raise ValueError(
                    "secondary horizon must be explicitly NAV-ineligible"
                )
        common_lineage = (
            self.protocol_id,
            self.component_id,
            self.manifest_sha256,
            self.observation_id,
            self.observation_sha256,
            self.pair_input_sha256,
            self.raw_event_id,
            self.ticker,
            self.portfolio_state_sha256,
            self.fixed_notional_policy_sha256,
            self.decision_sha256,
            self.decision_role,
        )
        for item in (*self.holding_records, *self.cash_flow_records):
            if (
                item.protocol_id,
                item.component_id,
                item.manifest_sha256,
                item.observation_id,
                item.observation_sha256,
                item.pair_input_sha256,
                item.raw_event_id,
                item.ticker,
                item.portfolio_state_sha256,
                item.fixed_notional_policy_sha256,
                item.decision_sha256,
                item.decision_role,
            ) != common_lineage:
                raise ValueError("lifecycle event lineage mismatch")
        _verify_lifecycle_shape(self)
        expected_id = canonical_fixed_notional_lifecycle_id(
            pair_input_sha256=self.pair_input_sha256,
            decision_role=self.decision_role,
            decision_sha256=self.decision_sha256,
            horizon_trading_days=self.horizon_trading_days,
        )
        if self.lifecycle_id != expected_id:
            raise ValueError("fixed-notional lifecycle ID mismatch")
        return self


class PairedFixedNotionalRecord(_EvaluationOnlyFixedNotionalArtifact):
    """Machine-enforced paired result over one exact shared input."""

    contract_version: Literal["shadow-fixed-notional-paired-record-v1"] = (
        FIXED_NOTIONAL_PAIRED_RECORD_VERSION
    )
    paired_record_id: NonEmptyString
    pair_input_id: NonEmptyString
    pair_input_sha256: Sha256
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    observation_id: NonEmptyString
    observation_sha256: Sha256
    raw_event_id: NonEmptyString
    ticker: CanonicalTicker
    portfolio_state_sha256: Sha256
    fixed_notional_policy_sha256: Sha256
    liquidity_record_sha256: Sha256
    opportunity_set_sha256: Sha256
    candidate_set_sha256: Sha256
    snapshot_sha256: Sha256
    label_definition_sha256: Sha256
    cost_assumptions_sha256: Sha256
    trading_calendar: TradingCalendar
    trading_calendar_sha256: Sha256
    target_sleeve_idr: Literal[13_000_000] = FIXED_NOTIONAL_IDR
    control_sizing_plan: FixedNotionalSizingPlan
    challenger_sizing_plan: FixedNotionalSizingPlan
    shared_exclusion_reason: Literal[
        "NOT_ESTIMABLE_ENTRY_CAPACITY",
        "NOT_SIZEABLE_FIXED_NOTIONAL",
    ] | None = None
    control: FixedNotionalLifecycle
    challenger: FixedNotionalLifecycle
    control_secondary: tuple[FixedNotionalLifecycle, ...]
    challenger_secondary: tuple[FixedNotionalLifecycle, ...]
    control_lifecycle_sha256: Sha256
    challenger_lifecycle_sha256: Sha256
    control_secondary_sha256s: tuple[Sha256, ...]
    challenger_secondary_sha256s: tuple[Sha256, ...]
    paired_at: datetime
    parity_verified: Literal[True] = True

    @field_validator("paired_at")
    @classmethod
    def require_aware_paired_at(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("paired_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_pair(self) -> PairedFixedNotionalRecord:
        if _required_hash(self.trading_calendar) != self.trading_calendar_sha256:
            raise ValueError("paired record trading-calendar hash mismatch")
        if self.control.decision_role != "CONTROL":
            raise ValueError("control lifecycle has wrong role")
        if self.challenger.decision_role != "CHALLENGER":
            raise ValueError("challenger lifecycle has wrong role")
        if (
            self.control.raw_event_id,
            self.control.ticker,
            self.challenger.raw_event_id,
            self.challenger.ticker,
        ) != (
            self.raw_event_id,
            self.ticker,
            self.raw_event_id,
            self.ticker,
        ):
            raise ValueError("paired lifecycles refer to different candidates")
        if (
            self.control_lifecycle_sha256,
            self.challenger_lifecycle_sha256,
        ) != (
            _required_hash(self.control),
            _required_hash(self.challenger),
        ):
            raise ValueError("paired lifecycle hashes mismatch")
        if (
            tuple(item.horizon_trading_days for item in self.control_secondary),
            tuple(
                item.horizon_trading_days for item in self.challenger_secondary
            ),
        ) != ((3, 5, 10), (3, 5, 10)):
            raise ValueError("paired secondary horizons must be exactly 3/5/10")
        if (
            self.control.horizon_trading_days,
            self.challenger.horizon_trading_days,
        ) != (15, 15):
            raise ValueError("paired primary lifecycles must use 15 sessions")
        if self.control_secondary_sha256s != tuple(
            _required_hash(item) for item in self.control_secondary
        ) or self.challenger_secondary_sha256s != tuple(
            _required_hash(item) for item in self.challenger_secondary
        ):
            raise ValueError("paired secondary lifecycle hashes mismatch")
        common = (
            self.pair_input_sha256,
            self.protocol_id,
            self.component_id,
            self.manifest_sha256,
            self.observation_id,
            self.observation_sha256,
            self.raw_event_id,
            self.ticker,
            self.portfolio_state_sha256,
            self.fixed_notional_policy_sha256,
        )
        for item in (
            self.control,
            *self.control_secondary,
            self.challenger,
            *self.challenger_secondary,
        ):
            if (
                item.pair_input_sha256,
                item.protocol_id,
                item.component_id,
                item.manifest_sha256,
                item.observation_id,
                item.observation_sha256,
                item.raw_event_id,
                item.ticker,
                item.portfolio_state_sha256,
                item.fixed_notional_policy_sha256,
            ) != common:
                raise ValueError("paired lifecycle shared lineage mismatch")
        lifecycle_cutoffs = {
            item.evaluated_at
            for item in (
                self.control,
                *self.control_secondary,
                self.challenger,
                *self.challenger_secondary,
            )
        }
        if len(lifecycle_cutoffs) != 1 or self.paired_at < next(
            iter(lifecycle_cutoffs)
        ):
            raise ValueError("paired lifecycle evaluation chronology mismatch")
        for primary, secondary, role in (
            (self.control, self.control_secondary, "CONTROL"),
            (self.challenger, self.challenger_secondary, "CHALLENGER"),
        ):
            if primary.decision_role != role or any(
                item.decision_role != role
                or item.decision_sha256 != primary.decision_sha256
                or item.raw_event_id != self.raw_event_id
                or item.ticker != self.ticker
                for item in secondary
            ):
                raise ValueError(
                    "secondary lifecycle decision/candidate lineage mismatch"
                )
        if (
            self.control.sizing_price_idr,
            self.control.desired_lots,
        ) != (
            self.control_sizing_plan.sizing_price_idr,
            self.control_sizing_plan.desired_lots,
        ):
            raise ValueError("control lifecycle sizing-plan mismatch")
        if (
            self.challenger.sizing_price_idr,
            self.challenger.desired_lots,
        ) != (
            self.challenger_sizing_plan.sizing_price_idr,
            self.challenger_sizing_plan.desired_lots,
        ):
            raise ValueError("challenger lifecycle sizing-plan mismatch")
        if self.shared_exclusion_reason is not None:
            expected = (self.shared_exclusion_reason,)
            if (
                self.control.reason_codes != expected
                or self.challenger.reason_codes != expected
                or self.control.status != "NOT_ESTIMABLE"
                or self.challenger.status != "NOT_ESTIMABLE"
            ):
                raise ValueError("shared exclusion is not identical on both sides")
        for lifecycle in (self.control, self.challenger):
            for cash_flow in lifecycle.cash_flow_records:
                if cash_flow.pair_input_sha256 != self.pair_input_sha256:
                    raise ValueError("cash flow refers to another pair input")
                expected_settlement = (
                    cash_flow.trade_session
                    if cash_flow.event_type == "DIVIDEND_CREDIT"
                    else _settlement_session(
                        self.trading_calendar,
                        cash_flow.trade_session,
                        lag_sessions=2,
                    )
                )
                if expected_settlement is None:
                    raise ValueError("cash-flow settlement is absent from calendar")
                if cash_flow.settlement_session != expected_settlement:
                    raise ValueError("cash-flow settlement is not exact T+2")
            if any(
                item.pair_input_sha256 != self.pair_input_sha256
                for item in lifecycle.holding_records
            ):
                raise ValueError("holding record refers to another pair input")
        expected_id = canonical_fixed_notional_paired_record_id(
            pair_input_sha256=self.pair_input_sha256,
            control_sha256s=(
                *self.control_secondary_sha256s,
                self.control_lifecycle_sha256,
            ),
            challenger_sha256s=(
                *self.challenger_secondary_sha256s,
                self.challenger_lifecycle_sha256,
            ),
        )
        if self.paired_record_id != expected_id:
            raise ValueError("paired fixed-notional record ID mismatch")
        return self


class FixedNotionalArtifactReference(_EvaluationOnlyFixedNotionalArtifact):
    """Dual-hash immutable reference for one fixed-notional artifact."""

    contract_version: Literal["shadow-fixed-notional-reference-v1"] = (
        FIXED_NOTIONAL_REFERENCE_VERSION
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
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
    artifact_id: NonEmptyString
    artifact_contract_version: NonEmptyString
    artifact_canonical_sha256: Sha256
    artifact_raw_file_sha256: Sha256
    artifact_raw_byte_length: StrictPositiveInt
    artifact_relative_path: NonEmptyString


class FixedNotionalPairAuthorizationLoader(Protocol):
    """Store-backed A1/closure check before the pair input is frozen."""

    def verify_paired_evaluation_authorization(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
        signal_at: datetime,
        attempted_at: datetime,
    ) -> object: ...


class FixedNotionalMaturationAuthorizationLoader(Protocol):
    """Ledger-reloading closure check at the evaluation linearization point."""

    def verify_fixed_notional_maturation_authorization(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
        observation: ShadowObservation,
        attempted_at: datetime,
    ) -> object: ...


def canonical_fixed_notional_event_id(
    *,
    prefix: str,
    payload: dict[str, object],
) -> str:
    return f"{prefix}-{_sha256(_canonical_mapping_bytes(payload))[:32]}"


def canonical_fixed_notional_lifecycle_id(
    *,
    pair_input_sha256: str,
    decision_role: DecisionRole,
    decision_sha256: str,
    horizon_trading_days: Literal[3, 5, 10, 15],
) -> str:
    return canonical_fixed_notional_event_id(
        prefix="FNLIFE",
        payload={
            "decision_role": decision_role,
            "decision_sha256": decision_sha256,
            "horizon_trading_days": horizon_trading_days,
            "pair_input_sha256": pair_input_sha256,
        },
    )


def canonical_fixed_notional_paired_record_id(
    *,
    pair_input_sha256: str,
    control_sha256s: Sequence[str],
    challenger_sha256s: Sequence[str],
) -> str:
    return canonical_fixed_notional_event_id(
        prefix="FNPAIR",
        payload={
            "challenger_sha256s": list(challenger_sha256s),
            "control_sha256s": list(control_sha256s),
            "pair_input_sha256": pair_input_sha256,
        },
    )


def canonical_fixed_notional_liquidity_payload_sha256(
    entry_measurement: FixedNotionalLiquidityMeasurement,
    exit_measurements: Sequence[FixedNotionalLiquidityMeasurement],
) -> str:
    return _sha256(
        _canonical_mapping_bytes(
            {
                "entry_measurement_sha256": _required_hash(entry_measurement),
                "exit_measurement_sha256s": [
                    _required_hash(item) for item in exit_measurements
                ],
            }
        )
    )


def canonical_fixed_notional_liquidity_source_sha256(
    *,
    source_id: str,
    source_definition_sha256: str,
    source_as_of: datetime,
    payload_sha256: str,
    previous_record_sha256: str | None,
) -> str:
    return _sha256(
        _canonical_mapping_bytes(
            {
                "payload_sha256": payload_sha256,
                "previous_record_sha256": previous_record_sha256,
                "source_as_of": _utc_iso(source_as_of),
                "source_definition_sha256": source_definition_sha256,
                "source_id": source_id,
            }
        )
    )


def canonical_fixed_notional_liquidity_record_id(
    *,
    protocol_id: str,
    manifest_sha256: str,
    observation_id: str,
    portfolio_state_sha256: str,
    fixed_notional_policy_sha256: str,
    source_record_sha256: str,
) -> str:
    return canonical_fixed_notional_event_id(
        prefix="FNLIQ",
        payload={
            "fixed_notional_policy_sha256": fixed_notional_policy_sha256,
            "manifest_sha256": manifest_sha256,
            "observation_id": observation_id,
            "portfolio_state_sha256": portfolio_state_sha256,
            "protocol_id": protocol_id,
            "source_record_sha256": source_record_sha256,
        },
    )


def canonical_fixed_notional_bar_source_sha256(
    *,
    source_id: str,
    source_definition_sha256: str,
    source_as_of: datetime,
    previous_source_sha256: str | None,
    requested_start: date,
    requested_end: date,
    ticker: str,
    snapshot_sha256: str,
    bars_sha256: str,
    corporate_action_policy_sha256: str,
    corporate_action_events_sha256: str,
) -> str:
    return _sha256(
        _canonical_mapping_bytes(
            {
                "bars_sha256": bars_sha256,
                "corporate_action_events_sha256": (
                    corporate_action_events_sha256
                ),
                "corporate_action_policy_sha256": (
                    corporate_action_policy_sha256
                ),
                "previous_source_sha256": previous_source_sha256,
                "requested_end": requested_end.isoformat(),
                "requested_start": requested_start.isoformat(),
                "snapshot_sha256": snapshot_sha256,
                "source_as_of": _utc_iso(source_as_of),
                "source_definition_sha256": source_definition_sha256,
                "source_id": source_id,
                "ticker": ticker,
            }
        )
    )


def fixed_notional_lot_count(
    planned_entry_high_idr: int | float,
    *,
    fixed_notional_idr: int = FIXED_NOTIONAL_IDR,
    lot_size_shares: int = FIXED_NOTIONAL_LOT_SIZE,
) -> int:
    """Floor the budget to whole lots using an exact integer-valued price."""

    price_idr = _integer_price(planned_entry_high_idr, "planned entry_high")
    if type(fixed_notional_idr) is not int or fixed_notional_idr <= 0:
        raise ValueError("fixed notional must be strict positive integer IDR")
    if type(lot_size_shares) is not int or lot_size_shares <= 0:
        raise ValueError("lot size must be a strict positive integer")
    return fixed_notional_idr // (price_idr * lot_size_shares)


def fixed_notional_cost_idr(
    notional_idr: int,
    costs: CostAssumptions,
    *,
    side: Literal["ENTRY", "EXIT"],
) -> int:
    """Aggregate applicable bps and apply one adverse integer-IDR ceiling."""

    trusted = _revalidate(CostAssumptions, costs)
    if side == "ENTRY":
        applicable = (
            trusted.buy_commission_bps,
            trusted.slippage_bps,
            trusted.bid_ask_bps,
        )
    elif side == "EXIT":
        applicable = (
            trusted.sell_commission_bps,
            trusted.sell_tax_bps,
            trusted.slippage_bps,
            trusted.bid_ask_bps,
        )
    else:
        raise ValueError("fixed-notional cost side must be ENTRY or EXIT")
    return aggregate_bps_cost_idr(notional_idr, applicable)


def build_fixed_notional_policy(
    *,
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    policy_id: str,
) -> FrozenFixedNotionalPolicy:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    portfolio_policy = verify_portfolio_manifest_binding(
        manifest,
        portfolio_policy,
    )
    if portfolio_policy.partial_fill_rule != "ALL_OR_NONE":
        raise ShadowContractError(
            "RS-P2-015 requires the owner-approved ALL_OR_NONE rule"
        )
    try:
        policy = FrozenFixedNotionalPolicy(
            policy_id=policy_id,
            portfolio_policy_id=portfolio_policy.policy_id,
            portfolio_policy_sha256=_required_hash(portfolio_policy),
            label_definition_sha256=_required_hash(manifest.labels),
            cost_assumptions_sha256=_required_hash(manifest.costs),
            trading_calendar_sha256=manifest.trading_calendar_sha256,
            corporate_action_policy_sha256=(
                manifest.corporate_action_policy_sha256
            ),
            methodology_document_sha256=(
                manifest.methodology_document_sha256
            ),
        )
    except ValueError as exc:
        raise ShadowContractError("fixed-notional policy is invalid") from exc
    return policy


def verify_fixed_notional_policy_binding(
    manifest: ShadowProtocolManifest,
    portfolio_policy: FrozenPortfolioPolicy,
    policy: FrozenFixedNotionalPolicy,
) -> FrozenFixedNotionalPolicy:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    portfolio_policy = verify_portfolio_manifest_binding(
        manifest,
        portfolio_policy,
    )
    policy = _revalidate(FrozenFixedNotionalPolicy, policy)
    expected = (
        portfolio_policy.policy_id,
        _required_hash(portfolio_policy),
        _required_hash(manifest.labels),
        _required_hash(manifest.costs),
        manifest.trading_calendar_sha256,
        manifest.corporate_action_policy_sha256,
        manifest.methodology_document_sha256,
        portfolio_policy.fixed_notional_idr,
        portfolio_policy.lot_size_shares,
        portfolio_policy.minimum_adt_idr,
        portfolio_policy.settlement_lag_sessions,
        portfolio_policy.cost_application_rounding_rule,
        portfolio_policy.ratio_quantization_decimal_places,
        portfolio_policy.partial_fill_rule,
    )
    actual = (
        policy.portfolio_policy_id,
        policy.portfolio_policy_sha256,
        policy.label_definition_sha256,
        policy.cost_assumptions_sha256,
        policy.trading_calendar_sha256,
        policy.corporate_action_policy_sha256,
        policy.methodology_document_sha256,
        policy.fixed_notional_idr,
        policy.lot_size_shares,
        policy.minimum_adtv20_idr,
        policy.settlement_lag_sessions,
        policy.cost_application_rounding_rule,
        policy.ratio_quantization_decimal_places,
        policy.capacity_rule,
    )
    expected_last = (*expected[:-1], "ALL_OR_NONE")
    if actual != expected_last:
        raise ShadowContractError(
            "fixed-notional policy differs from manifest/portfolio policy"
        )
    policy_hash = _required_hash(policy)
    for side, hashes in (
        ("control", manifest.control_content_hashes),
        ("challenger", manifest.challenger_content_hashes),
    ):
        matches = tuple(
            item
            for item in hashes
            if (
                item.role == "CONFIG"
                and item.path == FIXED_NOTIONAL_POLICY_CONFIG_PATH
                and item.sha256 == policy_hash
            )
        )
        if len(matches) != 1:
            raise ShadowContractError(
                "fixed-notional policy CONFIG hash must appear exactly once "
                f"on {side}"
            )
    _verify_label_definition(manifest.labels)
    return policy


def build_fixed_notional_liquidity_measurement(
    *,
    policy: FrozenFixedNotionalPolicy,
    trading_calendar: TradingCalendar,
    measurement_role: Literal["ENTRY", "EXIT"],
    capacity_session: date,
    measured_at: datetime,
    bars: Sequence[FixedNotionalLiquidityBar],
) -> FixedNotionalLiquidityMeasurement:
    policy = _revalidate(FrozenFixedNotionalPolicy, policy)
    trading_calendar = _revalidate(TradingCalendar, trading_calendar)
    if measured_at.utcoffset() is None:
        raise ShadowContractError("liquidity measured_at must be timezone-aware")
    trusted_bars = tuple(
        _revalidate(FixedNotionalLiquidityBar, item) for item in bars
    )
    expected_dates = _expected_liquidity_dates(
        trading_calendar,
        capacity_session=capacity_session,
        measured_at=measured_at,
    )
    if tuple(item.trade_date for item in trusted_bars) != expected_dates:
        raise ShadowContractError(
            "liquidity bars are not the exact last 20 completed sessions"
        )
    turnover = sum(item.turnover_idr for item in trusted_bars)
    try:
        return FixedNotionalLiquidityMeasurement(
            measurement_role=measurement_role,
            capacity_session=capacity_session,
            measured_at=measured_at,
            bars=trusted_bars,
            bar_record_sha256s=tuple(
                _required_hash(item) for item in trusted_bars
            ),
            turnover_sum_idr=turnover,
            adtv20_numerator_idr=turnover,
            minimum_adtv20_idr=policy.minimum_adtv20_idr,
            minimum_adtv20_passed=(
                turnover
                >= policy.minimum_adtv20_idr
                * policy.liquidity_lookback_sessions
            ),
            participation_numerator=policy.participation_numerator,
            participation_denominator=policy.participation_denominator,
            capacity_notional_numerator_idr=(
                turnover * policy.participation_numerator
            ),
            capacity_notional_denominator=(
                policy.liquidity_lookback_sessions
                * policy.participation_denominator
            ),
        )
    except ValueError as exc:
        raise ShadowContractError(
            "fixed-notional liquidity measurement is invalid"
        ) from exc


def build_fixed_notional_liquidity_record(
    *,
    manifest: ShadowProtocolManifest,
    observation: ShadowObservation,
    portfolio_state: PortfolioState,
    policy: FrozenFixedNotionalPolicy,
    entry_measurement: FixedNotionalLiquidityMeasurement,
    exit_measurements: Sequence[FixedNotionalLiquidityMeasurement],
    captured_at: datetime,
    previous_record_sha256: str | None = None,
) -> FixedNotionalLiquidityRecord:
    manifest = _revalidate(ShadowProtocolManifest, manifest)
    observation = _revalidate(ShadowObservation, observation)
    portfolio_state = _revalidate(PortfolioState, portfolio_state)
    policy = _revalidate(FrozenFixedNotionalPolicy, policy)
    entry = _revalidate(
        FixedNotionalLiquidityMeasurement,
        entry_measurement,
    )
    exits = tuple(
        _revalidate(FixedNotionalLiquidityMeasurement, item)
        for item in exit_measurements
    )
    if captured_at.utcoffset() is None:
        raise ShadowContractError("liquidity capture time must be timezone-aware")
    if (
        observation.protocol_id,
        observation.component_id,
        observation.manifest_sha256,
        observation.portfolio_state_sha256,
        observation.opportunity_set_id,
        observation.opportunity_set_sha256,
        observation.signal_at,
    ) != (
        manifest.protocol_id,
        manifest.component_id,
        _required_hash(manifest),
        _required_hash(portfolio_state),
        portfolio_state.opportunity_set_id,
        portfolio_state.opportunity_set_sha256,
        portfolio_state.signal_at,
    ):
        raise ShadowContractError("liquidity inputs have inconsistent lineage")
    liquidity_source_id = (
        portfolio_state.portfolio_policy.liquidity_source_id
    )
    liquidity_source_definition_sha256 = (
        portfolio_state.portfolio_policy.liquidity_source_definition_sha256
    )
    if (
        portfolio_state.portfolio_policy_sha256,
        portfolio_state.portfolio_policy_id,
        portfolio_state.portfolio_policy.liquidity_source_id,
        portfolio_state.portfolio_policy.liquidity_source_definition_sha256,
        policy.portfolio_policy_sha256,
        policy.portfolio_policy_id,
    ) != (
        _required_hash(portfolio_state.portfolio_policy),
        portfolio_state.portfolio_policy.policy_id,
        liquidity_source_id,
        liquidity_source_definition_sha256,
        _required_hash(portfolio_state.portfolio_policy),
        portfolio_state.portfolio_policy.policy_id,
    ):
        raise ShadowContractError(
            "liquidity policy/state binding is inconsistent"
        )
    source_as_of = max(
        item.available_at
        for measurement in (entry, *exits)
        for item in measurement.bars
    )
    payload_hash = canonical_fixed_notional_liquidity_payload_sha256(
        entry,
        exits,
    )
    source_hash = canonical_fixed_notional_liquidity_source_sha256(
        source_id=liquidity_source_id,
        source_definition_sha256=liquidity_source_definition_sha256,
        source_as_of=source_as_of,
        payload_sha256=payload_hash,
        previous_record_sha256=previous_record_sha256,
    )
    record_id = canonical_fixed_notional_liquidity_record_id(
        protocol_id=manifest.protocol_id,
        manifest_sha256=_required_hash(manifest),
        observation_id=observation.observation_id,
        portfolio_state_sha256=_required_hash(portfolio_state),
        fixed_notional_policy_sha256=_required_hash(policy),
        source_record_sha256=source_hash,
    )
    try:
        return FixedNotionalLiquidityRecord(
            record_id=record_id,
            protocol_id=manifest.protocol_id,
            component_id=manifest.component_id,
            manifest_sha256=_required_hash(manifest),
            observation_id=observation.observation_id,
            observation_sha256=_required_hash(observation),
            raw_event_id=observation.raw_event_id,
            portfolio_state_sha256=_required_hash(portfolio_state),
            fixed_notional_policy_sha256=_required_hash(policy),
            ticker=observation.ticker,
            signal_at=observation.signal_at,
            source_id=liquidity_source_id,
            source_definition_sha256=liquidity_source_definition_sha256,
            source_as_of=source_as_of,
            captured_at=captured_at,
            previous_record_sha256=previous_record_sha256,
            entry_measurement=entry,
            exit_measurements=exits,
            payload_sha256=payload_hash,
            source_record_sha256=source_hash,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "fixed-notional liquidity record is invalid"
        ) from exc


def build_fixed_notional_bar_series(
    *,
    ticker: str,
    snapshot_id: str,
    snapshot_sha256: str,
    source_id: str,
    source_definition_sha256: str,
    source_as_of: datetime,
    requested_start: date,
    requested_end: date,
    bars: Sequence[FixedNotionalMarketBar],
    corporate_action_policy: CorporateActionPolicy,
    previous_source_sha256: str | None = None,
) -> FixedNotionalBarSeries:
    trusted_bars = tuple(
        _revalidate(FixedNotionalMarketBar, item) for item in bars
    )
    ca_policy = _revalidate(CorporateActionPolicy, corporate_action_policy)
    records = tuple(_required_hash(item) for item in trusted_bars)
    bars_hash = _sha256(
        _canonical_mapping_bytes(
            {
                "bar_record_sha256s": list(records),
                "snapshot_sha256": snapshot_sha256,
                "ticker": ticker,
            }
        )
    )
    source_hash = canonical_fixed_notional_bar_source_sha256(
        source_id=source_id,
        source_definition_sha256=source_definition_sha256,
        source_as_of=source_as_of,
        previous_source_sha256=previous_source_sha256,
        requested_start=requested_start,
        requested_end=requested_end,
        ticker=ticker,
        snapshot_sha256=snapshot_sha256,
        bars_sha256=bars_hash,
        corporate_action_policy_sha256=ca_policy.policy_sha256,
        corporate_action_events_sha256=ca_policy.events_sha256,
    )
    try:
        return FixedNotionalBarSeries(
            ticker=ticker,
            snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha256,
            source_id=source_id,
            source_definition_sha256=source_definition_sha256,
            source_sha256=source_hash,
            source_as_of=source_as_of,
            previous_source_sha256=previous_source_sha256,
            requested_start=requested_start,
            requested_end=requested_end,
            bars=trusted_bars,
            bars_sha256=bars_hash,
            bar_record_sha256s=records,
            corporate_action_policy=ca_policy,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "fixed-notional integer bar series is invalid"
        ) from exc


def build_fixed_notional_pair_input(
    *,
    manifest: ShadowProtocolManifest,
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
    candidate: CandidateEvent,
    snapshot: FrozenSnapshot,
    portfolio_state: PortfolioState,
    observation: ShadowObservation,
    policy: FrozenFixedNotionalPolicy,
    liquidity: FixedNotionalLiquidityRecord,
    trading_calendar: TradingCalendar,
    bar_series: FixedNotionalBarSeries,
    frozen_at: datetime,
    evaluation_cutoff: datetime,
    authorization_loader: FixedNotionalMaturationAuthorizationLoader,
    approval_ledger_id: str,
) -> FixedNotionalPairInput:
    """Freeze one exact pair only after reloading current collection authority."""

    manifest = _revalidate(ShadowProtocolManifest, manifest)
    raw_capture = _revalidate(RawCandidateSetCapture, raw_capture)
    candidate_set = _revalidate(CandidateSetManifest, candidate_set)
    candidate = _revalidate(CandidateEvent, candidate)
    snapshot = _revalidate(FrozenSnapshot, snapshot)
    portfolio_state = _revalidate(PortfolioState, portfolio_state)
    observation = _revalidate(ShadowObservation, observation)
    policy = verify_fixed_notional_policy_binding(
        manifest,
        portfolio_state.portfolio_policy,
        policy,
    )
    liquidity = _revalidate(FixedNotionalLiquidityRecord, liquidity)
    trading_calendar = _revalidate(TradingCalendar, trading_calendar)
    bar_series = _revalidate(FixedNotionalBarSeries, bar_series)
    if frozen_at.utcoffset() is None or evaluation_cutoff.utcoffset() is None:
        raise ShadowContractError(
            "fixed-notional freeze/cutoff must be timezone-aware"
        )
    try:
        authorization_loader.verify_fixed_notional_maturation_authorization(
            protocol_id=manifest.protocol_id,
            manifest_canonical_sha256=_required_hash(manifest),
            ledger_id=approval_ledger_id,
            observation=observation,
            attempted_at=frozen_at,
        )
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise ShadowContractError(
            "current fixed-notional pair authorization is unavailable"
        ) from exc
    control_plan, challenger_plan, shared_exclusion = _derive_sizing_plans(
        observation.control_decision,
        observation.challenger_decision,
        liquidity.entry_measurement,
    )
    artifact_hashes = tuple(
        _required_hash(item)
        for item in (
            manifest,
            raw_capture,
            candidate_set,
            candidate,
            snapshot,
            portfolio_state,
            observation,
            policy,
            liquidity,
            trading_calendar,
            bar_series,
        )
    )
    pair_input_id = canonical_fixed_notional_event_id(
        prefix="FNINPUT",
        payload={
            "artifact_sha256s": list(artifact_hashes),
            "evaluation_cutoff": _utc_iso(evaluation_cutoff),
            "frozen_at": _utc_iso(frozen_at),
            "shared_exclusion_reason": shared_exclusion,
        },
    )
    try:
        return FixedNotionalPairInput(
            pair_input_id=pair_input_id,
            frozen_at=frozen_at,
            evaluation_cutoff=evaluation_cutoff,
            manifest=manifest,
            raw_capture=raw_capture,
            candidate_set=candidate_set,
            candidate=candidate,
            snapshot=snapshot,
            portfolio_state=portfolio_state,
            observation=observation,
            policy=policy,
            liquidity=liquidity,
            trading_calendar=trading_calendar,
            bar_series=bar_series,
            manifest_sha256=artifact_hashes[0],
            raw_capture_sha256=artifact_hashes[1],
            candidate_set_sha256=artifact_hashes[2],
            candidate_sha256=artifact_hashes[3],
            snapshot_sha256=artifact_hashes[4],
            portfolio_state_sha256=artifact_hashes[5],
            observation_sha256=artifact_hashes[6],
            policy_sha256=artifact_hashes[7],
            liquidity_sha256=artifact_hashes[8],
            trading_calendar_sha256=artifact_hashes[9],
            bar_series_sha256=artifact_hashes[10],
            label_definition_sha256=_required_hash(manifest.labels),
            cost_assumptions_sha256=_required_hash(manifest.costs),
            control_sizing_plan=control_plan,
            challenger_sizing_plan=challenger_plan,
            shared_exclusion_reason=shared_exclusion,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "fixed-notional paired input is invalid"
        ) from exc


def _derive_sizing_plans(
    control: ShadowDecision,
    challenger: ShadowDecision,
    entry_liquidity: FixedNotionalLiquidityMeasurement,
) -> tuple[
    FixedNotionalSizingPlan,
    FixedNotionalSizingPlan,
    Literal[
        "NOT_ESTIMABLE_ENTRY_CAPACITY",
        "NOT_SIZEABLE_FIXED_NOTIONAL",
    ]
    | None,
]:
    control = _revalidate(ShadowDecision, control)
    challenger = _revalidate(ShadowDecision, challenger)
    entry_liquidity = _revalidate(
        FixedNotionalLiquidityMeasurement,
        entry_liquidity,
    )
    control_plan = _build_sizing_plan(
        control,
        entry_liquidity,
        role="CONTROL",
    )
    challenger_plan = _build_sizing_plan(
        challenger,
        entry_liquidity,
        role="CHALLENGER",
    )
    actionable_classes = tuple(
        item.classification
        for item in (control_plan, challenger_plan)
        if item.classification != "NO_ACTION"
    )
    exclusions = {
        item
        for item in actionable_classes
        if item != "ELIGIBLE"
    }
    if exclusions and (
        len(exclusions) != 1
        or "ELIGIBLE" in actionable_classes
    ):
        raise ShadowContractError(
            "side-specific sizing changes shared eligibility classification"
        )
    shared = next(iter(exclusions)) if exclusions else None
    return control_plan, challenger_plan, shared  # type: ignore[return-value]


def _build_sizing_plan(
    decision: ShadowDecision,
    entry_liquidity: FixedNotionalLiquidityMeasurement,
    *,
    role: DecisionRole,
) -> FixedNotionalSizingPlan:
    if decision.decision_role != role:
        raise ShadowContractError(f"{role} decision has the wrong role")
    if not decision.would_be_actionable:
        return FixedNotionalSizingPlan(
            decision_role=role,
            sizing_price_idr=None,
            desired_lots=None,
            quantity_shares=None,
            gross_entry_notional_idr=None,
            residual_idle_cash_idr=None,
            classification="NO_ACTION",
        )
    if decision.geometry is None:
        raise ShadowContractError(
            f"{role} actionable decision has no planned geometry"
        )
    price = _integer_price(decision.geometry.entry_high, f"{role} entry_high")
    lots = fixed_notional_lot_count(price)
    shares = lots * FIXED_NOTIONAL_LOT_SIZE
    gross = shares * price
    residual = FIXED_NOTIONAL_IDR - gross
    if lots == 0:
        classification = NOT_SIZEABLE_FIXED_NOTIONAL
    elif (
        not entry_liquidity.minimum_adtv20_passed
        or not entry_liquidity.supports_gross_notional(gross)
    ):
        classification = NOT_ESTIMABLE_ENTRY_CAPACITY
    else:
        classification = "ELIGIBLE"
    return FixedNotionalSizingPlan(
        decision_role=role,
        sizing_price_idr=price,
        desired_lots=lots,
        quantity_shares=shares,
        gross_entry_notional_idr=gross,
        residual_idle_cash_idr=residual,
        classification=classification,
    )


def _verify_pair_lineage(pair: FixedNotionalPairInput) -> None:
    manifest = pair.manifest
    candidate = pair.candidate
    observation = pair.observation
    state = pair.portfolio_state
    policy = pair.policy
    liquidity = pair.liquidity
    calendar = pair.trading_calendar
    series = pair.bar_series
    verify_fixed_notional_policy_binding(
        manifest,
        state.portfolio_policy,
        policy,
    )
    manifest_hash = pair.manifest_sha256
    if (
        candidate.protocol_id,
        candidate.component_id,
        candidate.manifest_sha256,
        observation.protocol_id,
        observation.component_id,
        observation.manifest_sha256,
        state.protocol_id,
        state.component_id,
        state.manifest_sha256,
    ) != (
        manifest.protocol_id,
        manifest.component_id,
        manifest_hash,
        manifest.protocol_id,
        manifest.component_id,
        manifest_hash,
        manifest.protocol_id,
        manifest.component_id,
        manifest_hash,
    ):
        raise ValueError("fixed-notional protocol/component lineage mismatch")
    member = next(
        (
            item
            for item in pair.raw_capture.candidates
            if item.raw_event_id == candidate.raw_event_id
        ),
        None,
    )
    if member is None or _required_hash(member) != pair.candidate_sha256:
        raise ValueError("fixed-notional candidate is absent from raw capture")
    if (
        pair.candidate_set.raw_capture_id,
        pair.candidate_set.raw_capture_sha256,
    ) != (
        pair.raw_capture.raw_capture_id,
        pair.raw_capture_sha256,
    ):
        raise ValueError("fixed-notional candidate set/raw capture mismatch")
    candidate_identity = (
        candidate.raw_event_id,
        candidate.ticker,
        candidate.signal_at,
        candidate.as_of_date,
        candidate.opportunity_set_id,
        candidate.opportunity_set_sha256,
        candidate.snapshot_id,
        candidate.snapshot_sha256,
    )
    observation_identity = (
        observation.raw_event_id,
        observation.ticker,
        observation.signal_at,
        observation.as_of_date,
        observation.opportunity_set_id,
        observation.opportunity_set_sha256,
        observation.snapshot_id,
        observation.snapshot_sha256,
    )
    if candidate_identity != observation_identity:
        raise ValueError("candidate differs from paired observation")
    if (
        pair.snapshot.snapshot_id,
        pair.snapshot.snapshot_sha256,
        pair.snapshot.ticker,
        pair.snapshot.as_of_date,
    ) != (
        candidate.snapshot_id,
        candidate.snapshot_sha256,
        candidate.ticker,
        candidate.as_of_date,
    ):
        raise ValueError("fixed-notional snapshot identity mismatch")
    if (
        observation.portfolio_state_sha256,
        observation.candidate_set_id,
        observation.candidate_set_sha256,
        observation.opportunity_set_id,
        observation.opportunity_set_sha256,
        observation.signal_at,
    ) != (
        pair.portfolio_state_sha256,
        state.candidate_set_id,
        state.candidate_set_sha256,
        state.opportunity_set_id,
        state.opportunity_set_sha256,
        state.signal_at,
    ):
        raise ValueError("observation and frozen portfolio state differ")
    if state.captured_at > observation.captured_at:
        raise ValueError("paired observation predates frozen portfolio state")
    if (
        liquidity.protocol_id,
        liquidity.component_id,
        liquidity.manifest_sha256,
        liquidity.observation_id,
        liquidity.observation_sha256,
        liquidity.raw_event_id,
        liquidity.portfolio_state_sha256,
        liquidity.fixed_notional_policy_sha256,
        liquidity.ticker,
        liquidity.signal_at,
    ) != (
        manifest.protocol_id,
        manifest.component_id,
        manifest_hash,
        observation.observation_id,
        pair.observation_sha256,
        observation.raw_event_id,
        pair.portfolio_state_sha256,
        pair.policy_sha256,
        observation.ticker,
        observation.signal_at,
    ):
        raise ValueError("fixed-notional liquidity lineage mismatch")
    if (
        calendar.calendar_id,
        calendar.calendar_sha256,
    ) != (
        candidate.trading_calendar_id,
        manifest.trading_calendar_sha256,
    ):
        raise ValueError("fixed-notional calendar lineage mismatch")
    if (
        series.ticker,
        series.snapshot_id,
        series.snapshot_sha256,
        series.requested_start,
        series.requested_end,
    ) != (
        candidate.ticker,
        candidate.snapshot_id,
        candidate.snapshot_sha256,
        candidate.signal_at.astimezone(IDX_TIMEZONE).date(),
        manifest.fixed_terminal_date,
    ):
        raise ValueError("fixed-notional bar-series identity mismatch")
    if series.corporate_action_policy.policy_sha256 != (
        manifest.corporate_action_policy_sha256
    ):
        raise ValueError("fixed-notional corporate-action policy mismatch")
    if (
        manifest.labels.dividend_return_convention
        != series.corporate_action_policy.dividend_return_convention
    ):
        raise ValueError(
            "manifest dividend-return label differs from corporate-action policy"
        )
    signal_events = tuple(
        event
        for event in series.corporate_action_policy.events
        if event.published_at <= observation.signal_at
    )
    if canonical_corporate_action_events_sha256(signal_events) != (
        candidate.corporate_action_events_at_signal_sha256
    ):
        raise ValueError(
            "signal-time corporate-action event lineage mismatch"
        )
    source_map = {
        item.source_id: _required_hash(item) for item in manifest.sources
    }
    if (
        source_map.get(series.source_id) != series.source_definition_sha256
        or source_map.get(liquidity.source_id)
        != liquidity.source_definition_sha256
    ):
        raise ValueError("fixed-notional source is absent from manifest")
    signal_date = observation.signal_at.astimezone(IDX_TIMEZONE).date()
    if signal_date not in calendar.sessions:
        raise ValueError("signal date is absent from fixed calendar")
    if manifest.fixed_terminal_date not in calendar.sessions:
        raise ValueError("fixed terminal is absent from fixed calendar")
    if pair.evaluation_cutoff > session_close_at(manifest.fixed_terminal_date):
        raise ValueError("evaluation cutoff follows fixed terminal")
    calendar_dates = set(calendar.sessions)
    if any(item.trade_date not in calendar_dates for item in series.bars):
        raise ValueError("fixed-notional bar date is absent from calendar")
    if any(
        session_close_at(item.trade_date) > pair.evaluation_cutoff
        for item in series.bars
    ):
        raise ValueError("fixed-notional bar series contains future data")
    eligible = tuple(
        item
        for item in calendar.sessions
        if signal_date < item <= manifest.fixed_terminal_date
    )
    if len(eligible) < manifest.labels.entry_validity_trading_days + 15:
        raise ValueError("calendar cannot support entry validity plus 15 sessions")
    first_activation = eligible[0]
    if liquidity.entry_measurement.capacity_session != first_activation:
        raise ValueError("entry capacity session is not first activation")
    for measurement in (
        liquidity.entry_measurement,
        *liquidity.exit_measurements,
    ):
        _verify_liquidity_measurement_calendar(measurement, calendar)
    exit_sessions = {
        item.capacity_session for item in liquidity.exit_measurements
    }
    if not exit_sessions.issubset(set(eligible)):
        raise ValueError("exit liquidity refers to an ineligible session")
    _verify_corporate_action_inputs(pair)


def _verify_pair_sizing_fields(pair: FixedNotionalPairInput) -> None:
    control, challenger, shared = _derive_sizing_plans(
        pair.observation.control_decision,
        pair.observation.challenger_decision,
        pair.liquidity.entry_measurement,
    )
    if (
        pair.control_sizing_plan,
        pair.challenger_sizing_plan,
        pair.shared_exclusion_reason,
    ) != (control, challenger, shared):
        raise ValueError("fixed-notional sizing plan is not a deterministic replay")


def _verify_liquidity_measurement_calendar(
    measurement: FixedNotionalLiquidityMeasurement,
    calendar: TradingCalendar,
) -> None:
    expected = _expected_liquidity_dates(
        calendar,
        capacity_session=measurement.capacity_session,
        measured_at=measurement.measured_at,
    )
    if tuple(item.trade_date for item in measurement.bars) != expected:
        raise ValueError(
            "liquidity window is not the exact last 20 completed sessions"
        )


def _expected_liquidity_dates(
    calendar: TradingCalendar,
    *,
    capacity_session: date,
    measured_at: datetime,
) -> tuple[date, ...]:
    if capacity_session not in calendar.sessions:
        raise ShadowContractError("capacity session is absent from calendar")
    capacity_open = datetime.combine(
        capacity_session,
        SESSION_OPEN,
        tzinfo=IDX_TIMEZONE,
    )
    if measured_at >= capacity_open:
        raise ShadowContractError(
            "liquidity measurement must precede capacity-session open"
        )
    preceding = tuple(
        item for item in calendar.sessions if item < capacity_session
    )
    if len(preceding) < LIQUIDITY_LOOKBACK_SESSIONS:
        raise ShadowContractError("fewer than 20 completed liquidity sessions")
    expected = preceding[-LIQUIDITY_LOOKBACK_SESSIONS:]
    if session_close_at(expected[-1]) > measured_at:
        raise ShadowContractError(
            "liquidity measurement is stale or lacks the immediate prior session"
        )
    return expected


def _verify_label_definition(labels: LabelDefinition) -> None:
    labels = _revalidate(LabelDefinition, labels)
    actual = (
        labels.primary_horizon_trading_days,
        labels.activation_rule,
        labels.horizon_clock_rule,
        labels.fill_rule,
        labels.gap_rule,
        labels.entry_gap_through_stop_rule,
        labels.same_bar_ambiguity_rule,
        labels.corporate_action_rule,
        labels.rights_treatment_rule,
        labels.dividend_entitlement_rule,
        labels.unfilled_rule,
    )
    expected = (
        FIXED_NOTIONAL_PRIMARY_HORIZON,
        ACTIVATION_RULE,
        HORIZON_CLOCK_RULE,
        FILL_RULE,
        GAP_RULE,
        ENTRY_GAP_THROUGH_STOP_RULE,
        AMBIGUITY_RULE,
        CORPORATE_ACTION_RULE,
        RIGHTS_TREATMENT_RULE,
        DIVIDEND_ENTITLEMENT_RULE,
        UNFILLED_RULE,
    )
    if actual != expected:
        raise ShadowContractError(
            "manifest label semantics are unsupported by fixed-notional-v1"
        )


def _verify_corporate_action_inputs(pair: FixedNotionalPairInput) -> None:
    policy = pair.bar_series.corporate_action_policy
    manifest_sources = {
        item.source_id: _required_hash(item) for item in pair.manifest.sources
    }
    signal_date = pair.observation.signal_at.astimezone(IDX_TIMEZONE).date()
    for event in policy.events:
        if manifest_sources.get(event.source_id) != event.source_definition_sha256:
            raise ValueError("corporate-action source is absent from manifest")
        if event.published_at > pair.evaluation_cutoff:
            raise ValueError("corporate action was published after cutoff")
        if event.published_at > pair.bar_series.source_as_of:
            raise ValueError("corporate action follows bar-source vintage")
        if (
            signal_date
            < event.effective_date
            <= pair.manifest.fixed_terminal_date
            and event.effective_date not in pair.trading_calendar.sessions
        ):
            raise ValueError(
                "corporate-action effective date is not a frozen trading session"
            )
        if event.kind == "DIVIDEND":
            _integer_nonnegative(
                event.cash_per_share,
                "corporate-action dividend cash",
            )
    for bar in pair.bar_series.bars:
        expected_dividend = sum(
            _integer_nonnegative(
                event.cash_per_share,
                "corporate-action dividend cash",
            )
            for event in policy.events
            if (
                event.kind == "DIVIDEND"
                and event.effective_date == bar.trade_date
            )
        )
        if bar.dividend_per_share_idr != expected_dividend:
            raise ValueError(
                "integer bar dividend differs from corporate-action events"
            )


@dataclass(frozen=True)
class _IntegerGeometry:
    entry_high: int
    target: int
    stop: int


@dataclass(frozen=True)
class _Fill:
    session: date
    filled_at: datetime
    precision: Literal["SESSION_ONLY", "SESSION_OPEN"]
    intraday: bool
    price: int
    quantity: int
    gross: int
    residual: int
    entry_cost: int
    risk_basis: int
    bars_observed: int
    target_sequence_unproven: bool


def evaluate_fixed_notional_pair(
    pair_input: FixedNotionalPairInput,
    *,
    authorization_loader: FixedNotionalMaturationAuthorizationLoader,
    approval_ledger_id: str,
    attempted_at: datetime,
) -> PairedFixedNotionalRecord:
    """Evaluate 3/5/10/15 views after reloading current closure authority."""

    pair = _revalidate(FixedNotionalPairInput, pair_input)
    if attempted_at.utcoffset() is None:
        raise ShadowContractError(
            "fixed-notional attempt time must be timezone-aware"
        )
    if attempted_at < pair.frozen_at:
        raise ShadowContractError(
            "fixed-notional attempt predates the frozen pair"
        )
    try:
        authorization_loader.verify_fixed_notional_maturation_authorization(
            protocol_id=pair.manifest.protocol_id,
            manifest_canonical_sha256=pair.manifest_sha256,
            ledger_id=approval_ledger_id,
            observation=pair.observation,
            attempted_at=attempted_at,
        )
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise ShadowContractError(
            "current fixed-notional maturation authorization is unavailable"
        ) from exc
    return derive_paired_fixed_notional_record(pair)


def derive_paired_fixed_notional_record(
    pair_input: FixedNotionalPairInput,
) -> PairedFixedNotionalRecord:
    """Purely derive the exact paired record from its frozen input.

    This function performs no authorization or clock-dependent I/O.  It is
    therefore suitable for deterministic replay at content-addressed storage
    trust boundaries.  Public maturation still goes through
    :func:`evaluate_fixed_notional_pair`, which verifies current closure
    authority before calling this derivation.
    """

    pair = _revalidate(FixedNotionalPairInput, pair_input)
    input_hash = _required_hash(pair)
    control_all = tuple(
        _evaluate_fixed_notional_horizon(
            pair,
            pair.observation.control_decision,
            pair.control_sizing_plan,
            horizon=horizon,
            pair_input_sha256=input_hash,
        )
        for horizon in (3, 5, 10, 15)
    )
    challenger_all = tuple(
        _evaluate_fixed_notional_horizon(
            pair,
            pair.observation.challenger_decision,
            pair.challenger_sizing_plan,
            horizon=horizon,
            pair_input_sha256=input_hash,
        )
        for horizon in (3, 5, 10, 15)
    )
    if _required_hash(pair) != input_hash:
        raise ShadowContractError("fixed-notional pair input mutated during evaluation")
    control_secondary = control_all[:3]
    challenger_secondary = challenger_all[:3]
    control = control_all[3]
    challenger = challenger_all[3]
    control_secondary_hashes = tuple(
        _required_hash(item) for item in control_secondary
    )
    challenger_secondary_hashes = tuple(
        _required_hash(item) for item in challenger_secondary
    )
    control_hash = _required_hash(control)
    challenger_hash = _required_hash(challenger)
    record_id = canonical_fixed_notional_paired_record_id(
        pair_input_sha256=input_hash,
        control_sha256s=(*control_secondary_hashes, control_hash),
        challenger_sha256s=(*challenger_secondary_hashes, challenger_hash),
    )
    try:
        return PairedFixedNotionalRecord(
            paired_record_id=record_id,
            pair_input_id=pair.pair_input_id,
            pair_input_sha256=input_hash,
            protocol_id=pair.manifest.protocol_id,
            component_id=pair.manifest.component_id,
            manifest_sha256=pair.manifest_sha256,
            observation_id=pair.observation.observation_id,
            observation_sha256=pair.observation_sha256,
            raw_event_id=pair.observation.raw_event_id,
            ticker=pair.observation.ticker,
            portfolio_state_sha256=pair.portfolio_state_sha256,
            fixed_notional_policy_sha256=pair.policy_sha256,
            liquidity_record_sha256=pair.liquidity_sha256,
            opportunity_set_sha256=pair.observation.opportunity_set_sha256,
            candidate_set_sha256=pair.candidate_set_sha256,
            snapshot_sha256=pair.snapshot_sha256,
            label_definition_sha256=pair.label_definition_sha256,
            cost_assumptions_sha256=pair.cost_assumptions_sha256,
            trading_calendar=pair.trading_calendar,
            trading_calendar_sha256=pair.trading_calendar_sha256,
            control_sizing_plan=pair.control_sizing_plan,
            challenger_sizing_plan=pair.challenger_sizing_plan,
            shared_exclusion_reason=pair.shared_exclusion_reason,
            control=control,
            challenger=challenger,
            control_secondary=control_secondary,
            challenger_secondary=challenger_secondary,
            control_lifecycle_sha256=control_hash,
            challenger_lifecycle_sha256=challenger_hash,
            control_secondary_sha256s=control_secondary_hashes,
            challenger_secondary_sha256s=challenger_secondary_hashes,
            paired_at=pair.frozen_at,
        )
    except ValueError as exc:
        raise ShadowContractError(
            "paired fixed-notional result is invalid"
        ) from exc


def verify_paired_fixed_notional_record(
    pair_input: FixedNotionalPairInput,
    record: PairedFixedNotionalRecord,
) -> PairedFixedNotionalRecord:
    """Reject any record that is not the exact pure derivation of its input."""

    trusted = _revalidate(PairedFixedNotionalRecord, record)
    expected = derive_paired_fixed_notional_record(pair_input)
    if _required_hash(trusted) != _required_hash(expected):
        raise ShadowContractError(
            "fixed-notional record differs from exact input derivation"
        )
    return trusted


def replay_fixed_notional_pair(
    pair_input: FixedNotionalPairInput,
    record: PairedFixedNotionalRecord,
    *,
    authorization_loader: FixedNotionalMaturationAuthorizationLoader,
    approval_ledger_id: str,
    attempted_at: datetime,
) -> PairedFixedNotionalRecord:
    """Reload authorization, replay exact inputs, and reject any 1-byte drift."""

    trusted = _revalidate(PairedFixedNotionalRecord, record)
    expected = evaluate_fixed_notional_pair(
        pair_input,
        authorization_loader=authorization_loader,
        approval_ledger_id=approval_ledger_id,
        attempted_at=attempted_at,
    )
    if _required_hash(trusted) != _required_hash(expected):
        raise ShadowContractError(
            "fixed-notional record differs from deterministic replay"
        )
    return trusted


def _evaluate_fixed_notional_horizon(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    plan: FixedNotionalSizingPlan,
    *,
    horizon: Literal[3, 5, 10, 15],
    pair_input_sha256: str,
) -> FixedNotionalLifecycle:
    primary = horizon == 15
    if pair.shared_exclusion_reason is not None:
        return _missing_lifecycle(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
            reason=pair.shared_exclusion_reason,
            fill_status="NOT_ESTIMABLE",
        )
    if not decision.would_be_actionable:
        return _no_action_lifecycle(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
        )
    if plan.classification != "ELIGIBLE" or decision.geometry is None:
        raise ShadowContractError("actionable fixed-notional plan is not eligible")
    base_geometry = _integer_geometry(decision)
    bars = {item.trade_date: item for item in pair.bar_series.bars}
    eligible = _eligible_sessions(pair)
    activation = eligible[: pair.manifest.labels.entry_validity_trading_days]
    closed_activation = tuple(
        item
        for item in activation
        if session_close_at(item) <= pair.evaluation_cutoff
    )
    fill: _Fill | None = None
    primary_holdings: list[FixedNotionalHoldingRecord] = []
    primary_cash: list[FixedNotionalCashFlowRecord] = []
    for index, session in enumerate(closed_activation, start=1):
        bar = bars.get(session)
        if bar is None:
            return _missing_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                reason="MISSING_REQUIRED_SESSION_BAR",
                bars_observed=index - 1,
            )
        if _rights_effective(pair, session):
            return _missing_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                reason="RIGHTS_POLICY_UNSUPPORTED",
                bars_observed=index - 1,
            )
        try:
            geometry = _geometry_for_session(base_geometry, pair, session)
        except ValueError:
            return _missing_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                reason="NOT_ESTIMABLE_SPLIT_NON_INTEGER",
                bars_observed=index - 1,
            )
        entry = _entry_fill(bar, geometry)
        if entry is None:
            continue
        fill_price, intraday = entry
        quantity = int(plan.quantity_shares)
        gross = fill_price * quantity
        if gross > FIXED_NOTIONAL_IDR:
            return _missing_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                reason="FILL_GROSS_EXCEEDS_FIXED_NOTIONAL",
                bars_observed=index,
            )
        # FN1 freezes risk at planned geometry; a favorable fill must never
        # shrink the denominator and mechanically inflate net-R.
        risk_per_share = geometry.entry_high - geometry.stop
        if risk_per_share <= 0:
            return _missing_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                reason="NOT_ESTIMABLE_NONPOSITIVE_RISK",
                bars_observed=index,
            )
        filled_at = (
            session_close_at(session)
            if intraday
            else _session_open_at(session)
        )
        entry_cost = fixed_notional_cost_idr(
            gross,
            pair.manifest.costs,
            side="ENTRY",
        )
        fill = _Fill(
            session=session,
            filled_at=filled_at,
            precision="SESSION_ONLY" if intraday else "SESSION_OPEN",
            intraday=intraday,
            price=fill_price,
            quantity=quantity,
            gross=gross,
            residual=FIXED_NOTIONAL_IDR - gross,
            entry_cost=entry_cost,
            risk_basis=risk_per_share * quantity,
            bars_observed=index,
            target_sequence_unproven=(
                intraday and bar.high_price_idr >= geometry.target
            ),
        )
        if primary:
            settlement = _settlement_session(
                pair.trading_calendar,
                session,
                lag_sessions=pair.policy.settlement_lag_sessions,
            )
            if settlement is None:
                return _missing_lifecycle(
                    pair,
                    decision,
                    plan,
                    horizon=horizon,
                    pair_input_sha256=pair_input_sha256,
                    reason="NOT_ESTIMABLE_SETTLEMENT_SESSION",
                    fill=fill,
                    bars_observed=index,
                )
            primary_holdings.append(
                _holding_event(
                    pair,
                    decision,
                    pair_input_sha256,
                    event_type="OPEN",
                    session=session,
                    occurred_at=filled_at,
                    before=0,
                    after=quantity,
                    price=fill_price,
                )
            )
            primary_cash.append(
                _cash_flow_event(
                    pair,
                    decision,
                    pair_input_sha256,
                    event_type="ENTRY_DEBIT",
                    session=session,
                    occurred_at=filled_at,
                    settlement=settlement,
                    gross=gross,
                    cost=entry_cost,
                    quantity=quantity,
                    price=fill_price,
                )
            )
        terminal = _terminal_for_bar(
            bar,
            geometry,
            intraday_fill=intraday,
        )
        if terminal is not None:
            event, exit_price, ambiguous, reason = terminal
            if fill_price <= geometry.stop:
                reason = "ENTRY_GAP_THROUGH_STOP_GAP"
            return _close_lifecycle(
                pair,
                decision,
                plan,
                fill,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                session=session,
                exit_price=exit_price,
                quantity=quantity,
                dividend_cash=0,
                terminal_event=event,
                reason=reason,
                same_bar_ambiguous=ambiguous,
                holdings=primary_holdings,
                cash_flows=primary_cash,
                bars_observed=index,
            )
        break
    if fill is None:
        if len(closed_activation) < len(activation):
            return _pending_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                fill=None,
                holdings=(),
                cash_flows=(),
                bars_observed=len(closed_activation),
            )
        return _unfilled_lifecycle(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
            maturity_at=session_close_at(activation[-1]),
            bars_observed=len(activation),
        )

    horizon_sessions = tuple(item for item in eligible if item > fill.session)[:horizon]
    closed_horizon = tuple(
        item
        for item in horizon_sessions
        if session_close_at(item) <= pair.evaluation_cutoff
    )
    quantity = fill.quantity
    dividend_cash = 0
    for offset, session in enumerate(closed_horizon, start=1):
        bar = bars.get(session)
        observed = fill.bars_observed + offset
        if bar is None:
            return _missing_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                reason="MISSING_REQUIRED_SESSION_BAR",
                fill=fill,
                holdings=primary_holdings,
                cash_flows=primary_cash,
                bars_observed=observed - 1,
            )
        if _rights_effective(pair, session):
            return _missing_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                reason="RIGHTS_POLICY_UNSUPPORTED",
                fill=fill,
                holdings=primary_holdings,
                cash_flows=primary_cash,
                bars_observed=observed - 1,
            )
        split_result = _apply_split(pair, session, quantity)
        if split_result is None:
            return _missing_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                reason="NOT_ESTIMABLE_SPLIT_NON_INTEGER",
                fill=fill,
                holdings=primary_holdings,
                cash_flows=primary_cash,
                bars_observed=observed - 1,
            )
        new_quantity = split_result
        if primary and new_quantity != quantity:
            primary_holdings.append(
                _holding_event(
                    pair,
                    decision,
                    pair_input_sha256,
                    event_type="SPLIT_ADJUSTMENT",
                    session=session,
                    occurred_at=_session_open_at(session),
                    before=quantity,
                    after=new_quantity,
                    price=bar.open_price_idr,
                )
            )
        quantity = new_quantity
        if (
            pair.bar_series.corporate_action_policy.dividend_return_convention
            == "TOTAL_RETURN"
            and fill.session < session
            and bar.dividend_per_share_idr > 0
        ):
            dividend = bar.dividend_per_share_idr * quantity
            dividend_cash += dividend
            if primary:
                primary_cash.append(
                    _cash_flow_event(
                        pair,
                        decision,
                        pair_input_sha256,
                        event_type="DIVIDEND_CREDIT",
                        session=session,
                        occurred_at=session_close_at(session),
                        settlement=session,
                        gross=dividend,
                        cost=0,
                        quantity=quantity,
                        price=0,
                    )
                )
        try:
            geometry = _geometry_for_session(base_geometry, pair, session)
        except ValueError:
            return _missing_lifecycle(
                pair,
                decision,
                plan,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                reason="NOT_ESTIMABLE_SPLIT_NON_INTEGER",
                fill=fill,
                holdings=primary_holdings,
                cash_flows=primary_cash,
                bars_observed=observed - 1,
            )
        terminal = _terminal_for_bar(bar, geometry, intraday_fill=False)
        if terminal is not None:
            event, exit_price, ambiguous, reason = terminal
            return _close_lifecycle(
                pair,
                decision,
                plan,
                fill,
                horizon=horizon,
                pair_input_sha256=pair_input_sha256,
                session=session,
                exit_price=exit_price,
                quantity=quantity,
                dividend_cash=dividend_cash,
                terminal_event=event,
                reason=reason,
                same_bar_ambiguous=ambiguous,
                holdings=primary_holdings,
                cash_flows=primary_cash,
                bars_observed=observed,
            )
    if len(closed_horizon) < horizon:
        return _pending_lifecycle(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
            fill=fill,
            holdings=primary_holdings,
            cash_flows=primary_cash,
            bars_observed=fill.bars_observed + len(closed_horizon),
        )
    final_session = horizon_sessions[-1]
    final_bar = bars[final_session]
    return _close_lifecycle(
        pair,
        decision,
        plan,
        fill,
        horizon=horizon,
        pair_input_sha256=pair_input_sha256,
        session=final_session,
        exit_price=final_bar.close_price_idr,
        quantity=quantity,
        dividend_cash=dividend_cash,
        terminal_event="TIMEOUT",
        reason="TIMEOUT_HORIZON",
        same_bar_ambiguous=False,
        holdings=primary_holdings,
        cash_flows=primary_cash,
        bars_observed=fill.bars_observed + len(closed_horizon),
    )


def _lifecycle_base(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    plan: FixedNotionalSizingPlan,
    *,
    horizon: Literal[3, 5, 10, 15],
    pair_input_sha256: str,
) -> dict[str, object]:
    decision_hash = _required_hash(decision)
    return {
        "lifecycle_id": canonical_fixed_notional_lifecycle_id(
            pair_input_sha256=pair_input_sha256,
            decision_role=decision.decision_role,
            decision_sha256=decision_hash,
            horizon_trading_days=horizon,
        ),
        "pair_input_sha256": pair_input_sha256,
        "protocol_id": pair.manifest.protocol_id,
        "component_id": pair.manifest.component_id,
        "manifest_sha256": pair.manifest_sha256,
        "observation_id": pair.observation.observation_id,
        "observation_sha256": pair.observation_sha256,
        "raw_event_id": pair.observation.raw_event_id,
        "ticker": pair.observation.ticker,
        "portfolio_state_sha256": pair.portfolio_state_sha256,
        "fixed_notional_policy_sha256": pair.policy_sha256,
        "decision_role": decision.decision_role,
        "decision_sha256": decision_hash,
        "horizon_trading_days": horizon,
        "primary_horizon": horizon == 15,
        "signal_at": pair.observation.signal_at,
        "evaluated_at": pair.evaluation_cutoff,
        "planned_geometry_sha256": canonical_sha256(decision.geometry),
        "sizing_price_idr": plan.sizing_price_idr,
        "desired_lots": plan.desired_lots,
    }


def _nav_status(
    horizon: int,
    *,
    status: LifecycleStatus,
    has_holding: bool,
) -> str:
    if horizon != 15:
        return "SECONDARY_METRIC_ONLY"
    if status == "NOT_ESTIMABLE":
        return "EXCLUDED_NOT_ESTIMABLE"
    return "ELIGIBLE_PRIMARY_15D" if has_holding else "NO_HOLDING_OR_CASH_FLOW"


def _missing_lifecycle(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    plan: FixedNotionalSizingPlan,
    *,
    horizon: Literal[3, 5, 10, 15],
    pair_input_sha256: str,
    reason: str,
    fill_status: FixedFillStatus = "NOT_ESTIMABLE",
    fill: _Fill | None = None,
    holdings: Sequence[FixedNotionalHoldingRecord] = (),
    cash_flows: Sequence[FixedNotionalCashFlowRecord] = (),
    bars_observed: int = 0,
) -> FixedNotionalLifecycle:
    persisted_holdings = tuple(holdings) if horizon == 15 else ()
    persisted_cash = tuple(cash_flows) if horizon == 15 else ()
    persisted_dividend_cash = sum(
        item.gross_amount_idr
        for item in persisted_cash
        if item.event_type == "DIVIDEND_CREDIT"
    )
    return FixedNotionalLifecycle(
        **_lifecycle_base(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
        ),
        rs_p2_017_consumption_status=_nav_status(
            horizon,
            status="NOT_ESTIMABLE",
            has_holding=fill is not None,
        ),
        status="NOT_ESTIMABLE",
        fill_status="FILLED" if fill is not None else fill_status,
        terminal_event="NOT_ESTIMABLE",
        filled_at=fill.filled_at if fill else None,
        fill_time_precision=fill.precision if fill else None,
        entry_lots=int(plan.desired_lots) if fill else None,
        entry_quantity_shares=fill.quantity if fill else None,
        fill_price_idr=fill.price if fill else None,
        gross_entry_notional_idr=fill.gross if fill else None,
        entry_cash_debit_idr=(
            fill.gross + fill.entry_cost if fill else None
        ),
        residual_idle_cash_idr=fill.residual if fill else None,
        dividend_cash_idr=persisted_dividend_cash if fill else None,
        entry_cost_idr=fill.entry_cost if fill else None,
        total_cost_idr=fill.entry_cost if fill else None,
        risk_capital_basis_idr=fill.risk_basis if fill else None,
        net_pnl_idr=not_estimable_money(reason),
        sleeve_return=not_estimable_ratio(reason),
        acted_trade_return=not_estimable_ratio(reason),
        net_r=not_estimable_ratio(reason),
        bars_observed=bars_observed,
        holding_records=persisted_holdings,
        cash_flow_records=persisted_cash,
        holding_record_sha256s=tuple(
            _required_hash(item) for item in persisted_holdings
        ),
        cash_flow_record_sha256s=tuple(
            _required_hash(item) for item in persisted_cash
        ),
        reason_codes=(reason,),
    )


def _no_action_lifecycle(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    plan: FixedNotionalSizingPlan,
    *,
    horizon: Literal[3, 5, 10, 15],
    pair_input_sha256: str,
) -> FixedNotionalLifecycle:
    reason = "NO_ACTION_TRADE_METRIC_NOT_ESTIMABLE"
    return FixedNotionalLifecycle(
        **_lifecycle_base(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
        ),
        rs_p2_017_consumption_status=_nav_status(
            horizon,
            status="MATURE",
            has_holding=False,
        ),
        status="MATURE",
        fill_status="NOT_APPLICABLE",
        terminal_event="NO_ACTION",
        maturity_at=pair.observation.captured_at,
        net_pnl_idr=estimable_money(0),
        sleeve_return=estimable_ratio(0.0),
        acted_trade_return=not_estimable_ratio(reason),
        net_r=not_estimable_ratio(reason),
        bars_observed=0,
        reason_codes=decision.reason_codes,
    )


def _unfilled_lifecycle(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    plan: FixedNotionalSizingPlan,
    *,
    horizon: Literal[3, 5, 10, 15],
    pair_input_sha256: str,
    maturity_at: datetime,
    bars_observed: int,
) -> FixedNotionalLifecycle:
    reason = "UNFILLED_EXPIRED"
    return FixedNotionalLifecycle(
        **_lifecycle_base(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
        ),
        rs_p2_017_consumption_status=_nav_status(
            horizon,
            status="MATURE",
            has_holding=False,
        ),
        status="MATURE",
        fill_status="EXPIRED_UNFILLED",
        terminal_event="UNFILLED",
        maturity_at=maturity_at,
        net_pnl_idr=estimable_money(0),
        sleeve_return=estimable_ratio(0.0),
        acted_trade_return=not_estimable_ratio(reason),
        net_r=not_estimable_ratio(reason),
        bars_observed=bars_observed,
        reason_codes=(reason,),
    )


def _pending_lifecycle(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    plan: FixedNotionalSizingPlan,
    *,
    horizon: Literal[3, 5, 10, 15],
    pair_input_sha256: str,
    fill: _Fill | None,
    holdings: Sequence[FixedNotionalHoldingRecord],
    cash_flows: Sequence[FixedNotionalCashFlowRecord],
    bars_observed: int,
) -> FixedNotionalLifecycle:
    reason = "PENDING_MATURITY"
    persisted_holdings = tuple(holdings) if horizon == 15 else ()
    persisted_cash = tuple(cash_flows) if horizon == 15 else ()
    persisted_dividend_cash = sum(
        item.gross_amount_idr
        for item in persisted_cash
        if item.event_type == "DIVIDEND_CREDIT"
    )
    reasons = (
        (reason, "INTRADAY_ENTRY_TARGET_UNPROVEN")
        if fill is not None and fill.target_sequence_unproven
        else (reason,)
    )
    return FixedNotionalLifecycle(
        **_lifecycle_base(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
        ),
        rs_p2_017_consumption_status=_nav_status(
            horizon,
            status="PENDING",
            has_holding=fill is not None,
        ),
        status="PENDING",
        fill_status="FILLED" if fill else "PENDING",
        terminal_event="PENDING",
        filled_at=fill.filled_at if fill else None,
        fill_time_precision=fill.precision if fill else None,
        entry_lots=int(plan.desired_lots) if fill else None,
        entry_quantity_shares=fill.quantity if fill else None,
        fill_price_idr=fill.price if fill else None,
        gross_entry_notional_idr=fill.gross if fill else None,
        entry_cash_debit_idr=(
            fill.gross + fill.entry_cost if fill else None
        ),
        residual_idle_cash_idr=fill.residual if fill else None,
        dividend_cash_idr=persisted_dividend_cash if fill else None,
        entry_cost_idr=fill.entry_cost if fill else None,
        total_cost_idr=fill.entry_cost if fill else None,
        risk_capital_basis_idr=fill.risk_basis if fill else None,
        net_pnl_idr=not_estimable_money(reason),
        sleeve_return=not_estimable_ratio(reason),
        acted_trade_return=not_estimable_ratio(reason),
        net_r=not_estimable_ratio(reason),
        same_bar_ambiguous=bool(fill and fill.target_sequence_unproven),
        ambiguity_resolution=(
            "INTRADAY_ENTRY_TARGET_UNPROVEN_NOT_CREDITED"
            if fill and fill.target_sequence_unproven
            else None
        ),
        bars_observed=bars_observed,
        holding_records=persisted_holdings,
        cash_flow_records=persisted_cash,
        holding_record_sha256s=tuple(
            _required_hash(item) for item in persisted_holdings
        ),
        cash_flow_record_sha256s=tuple(
            _required_hash(item) for item in persisted_cash
        ),
        reason_codes=reasons,
    )


def _close_lifecycle(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    plan: FixedNotionalSizingPlan,
    fill: _Fill,
    *,
    horizon: Literal[3, 5, 10, 15],
    pair_input_sha256: str,
    session: date,
    exit_price: int,
    quantity: int,
    dividend_cash: int,
    terminal_event: Literal["STOP_FIRST", "TARGET_FIRST", "TIMEOUT"],
    reason: str,
    same_bar_ambiguous: bool,
    holdings: Sequence[FixedNotionalHoldingRecord],
    cash_flows: Sequence[FixedNotionalCashFlowRecord],
    bars_observed: int,
) -> FixedNotionalLifecycle:
    exit_gross = exit_price * quantity
    measurement = pair.liquidity.exit_measurement_for(session)
    if (
        measurement is None
        or not measurement.minimum_adtv20_passed
        or not measurement.supports_gross_notional(exit_gross)
    ):
        return _missing_lifecycle(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
            reason=NOT_ESTIMABLE_EXIT_CAPACITY,
            fill=fill,
            holdings=holdings,
            cash_flows=cash_flows,
            bars_observed=bars_observed,
        )
    exit_cost = fixed_notional_cost_idr(
        exit_gross,
        pair.manifest.costs,
        side="EXIT",
    )
    settlement = _settlement_session(
        pair.trading_calendar,
        session,
        lag_sessions=pair.policy.settlement_lag_sessions,
    )
    if settlement is None:
        return _missing_lifecycle(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
            reason="NOT_ESTIMABLE_SETTLEMENT_SESSION",
            fill=fill,
            holdings=holdings,
            cash_flows=cash_flows,
            bars_observed=bars_observed,
        )
    closed_at = (
        _session_open_at(session)
        if reason.endswith("_GAP")
        else session_close_at(session)
    )
    persisted_holdings = list(holdings) if horizon == 15 else []
    persisted_cash = list(cash_flows) if horizon == 15 else []
    if horizon == 15:
        persisted_holdings.append(
            _holding_event(
                pair,
                decision,
                pair_input_sha256,
                event_type="CLOSE",
                session=session,
                occurred_at=closed_at,
                before=quantity,
                after=0,
                price=exit_price,
            )
        )
        persisted_cash.append(
            _cash_flow_event(
                pair,
                decision,
                pair_input_sha256,
                event_type="EXIT_CREDIT",
                session=session,
                occurred_at=closed_at,
                settlement=settlement,
                gross=exit_gross,
                cost=exit_cost,
                quantity=quantity,
                price=exit_price,
            )
        )
    net_pnl = (
        exit_gross
        + dividend_cash
        - fill.gross
        - fill.entry_cost
        - exit_cost
    )
    reasons = (reason,)
    if fill.target_sequence_unproven:
        reasons = (*reasons, "INTRADAY_ENTRY_TARGET_UNPROVEN")
    ambiguous = same_bar_ambiguous or fill.target_sequence_unproven
    return FixedNotionalLifecycle(
        **_lifecycle_base(
            pair,
            decision,
            plan,
            horizon=horizon,
            pair_input_sha256=pair_input_sha256,
        ),
        rs_p2_017_consumption_status=_nav_status(
            horizon,
            status="MATURE",
            has_holding=True,
        ),
        status="MATURE",
        fill_status="FILLED",
        terminal_event=terminal_event,
        maturity_at=closed_at,
        filled_at=fill.filled_at,
        closed_at=closed_at,
        fill_time_precision=fill.precision,
        entry_lots=int(plan.desired_lots),
        entry_quantity_shares=fill.quantity,
        exit_quantity_shares=quantity,
        fill_price_idr=fill.price,
        exit_price_idr=exit_price,
        gross_entry_notional_idr=fill.gross,
        entry_cash_debit_idr=fill.gross + fill.entry_cost,
        residual_idle_cash_idr=fill.residual,
        gross_exit_value_idr=exit_gross,
        dividend_cash_idr=dividend_cash,
        entry_cost_idr=fill.entry_cost,
        exit_cost_idr=exit_cost,
        total_cost_idr=fill.entry_cost + exit_cost,
        risk_capital_basis_idr=fill.risk_basis,
        net_pnl_idr=estimable_money(net_pnl),
        sleeve_return=estimable_ratio(
            quantize_ratio(net_pnl, FIXED_NOTIONAL_IDR)
        ),
        acted_trade_return=estimable_ratio(
            quantize_ratio(net_pnl, fill.gross)
        ),
        net_r=estimable_ratio(quantize_ratio(net_pnl, fill.risk_basis)),
        same_bar_ambiguous=ambiguous,
        ambiguity_resolution=(
            "STOP_FIRST"
            if same_bar_ambiguous
            else (
                "INTRADAY_ENTRY_TARGET_UNPROVEN_NOT_CREDITED"
                if fill.target_sequence_unproven
                else None
            )
        ),
        bars_observed=bars_observed,
        holding_records=tuple(persisted_holdings),
        cash_flow_records=tuple(persisted_cash),
        holding_record_sha256s=tuple(
            _required_hash(item) for item in persisted_holdings
        ),
        cash_flow_record_sha256s=tuple(
            _required_hash(item) for item in persisted_cash
        ),
        reason_codes=reasons,
    )


def _event_lineage(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    pair_input_sha256: str,
) -> dict[str, object]:
    return {
        "protocol_id": pair.manifest.protocol_id,
        "component_id": pair.manifest.component_id,
        "manifest_sha256": pair.manifest_sha256,
        "observation_id": pair.observation.observation_id,
        "observation_sha256": pair.observation_sha256,
        "pair_input_sha256": pair_input_sha256,
        "raw_event_id": pair.observation.raw_event_id,
        "ticker": pair.observation.ticker,
        "portfolio_state_sha256": pair.portfolio_state_sha256,
        "fixed_notional_policy_sha256": pair.policy_sha256,
        "decision_sha256": _required_hash(decision),
        "decision_role": decision.decision_role,
    }


def _cash_flow_event(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    pair_input_sha256: str,
    *,
    event_type: Literal["DIVIDEND_CREDIT", "ENTRY_DEBIT", "EXIT_CREDIT"],
    session: date,
    occurred_at: datetime,
    settlement: date,
    gross: int,
    cost: int,
    quantity: int,
    price: int,
) -> FixedNotionalCashFlowRecord:
    net = {
        "ENTRY_DEBIT": -(gross + cost),
        "EXIT_CREDIT": gross - cost,
        "DIVIDEND_CREDIT": gross,
    }[event_type]
    payload = {
        **_event_lineage(pair, decision, pair_input_sha256),
        "event_type": event_type,
        "trade_session": session,
        "occurred_at": occurred_at,
        "settlement_session": settlement,
        "gross_amount_idr": gross,
        "cost_idr": cost,
        "net_cash_change_idr": net,
        "quantity_shares": quantity,
        "price_idr": price,
        "cash_availability": (
            "RETURN_ATTRIBUTION_ON_EFFECTIVE_SESSION"
            if event_type == "DIVIDEND_CREDIT"
            else "SETTLED_T_PLUS_2"
        ),
    }
    event_id = canonical_fixed_notional_event_id(
        prefix="FCF",
        payload={
            "decision_role": decision.decision_role,
            "event_type": event_type,
            "gross_amount_idr": gross,
            "net_cash_change_idr": net,
            "occurred_at": _utc_iso(occurred_at),
            "quantity_shares": quantity,
            "protocol_id": pair.manifest.protocol_id,
            "component_id": pair.manifest.component_id,
            "manifest_sha256": pair.manifest_sha256,
            "observation_id": pair.observation.observation_id,
            "observation_sha256": pair.observation_sha256,
            "pair_input_sha256": pair_input_sha256,
            "raw_event_id": pair.observation.raw_event_id,
            "ticker": pair.observation.ticker,
            "portfolio_state_sha256": pair.portfolio_state_sha256,
            "fixed_notional_policy_sha256": pair.policy_sha256,
            "decision_sha256": _required_hash(decision),
            "settlement_session": settlement.isoformat(),
            "trade_session": session.isoformat(),
        },
    )
    return FixedNotionalCashFlowRecord(
        cash_flow_id=event_id,
        **payload,
    )


def _holding_event(
    pair: FixedNotionalPairInput,
    decision: ShadowDecision,
    pair_input_sha256: str,
    *,
    event_type: Literal["CLOSE", "OPEN", "SPLIT_ADJUSTMENT"],
    session: date,
    occurred_at: datetime,
    before: int,
    after: int,
    price: int,
) -> FixedNotionalHoldingRecord:
    payload = {
        **_event_lineage(pair, decision, pair_input_sha256),
        "event_type": event_type,
        "event_session": session,
        "occurred_at": occurred_at,
        "quantity_before_shares": before,
        "quantity_after_shares": after,
        "price_idr": price,
        "marked_value_after_idr": after * price,
    }
    event_id = canonical_fixed_notional_event_id(
        prefix="FHOLD",
        payload={
            "decision_role": decision.decision_role,
            "event_session": session.isoformat(),
            "event_type": event_type,
            "occurred_at": _utc_iso(occurred_at),
            "protocol_id": pair.manifest.protocol_id,
            "component_id": pair.manifest.component_id,
            "manifest_sha256": pair.manifest_sha256,
            "observation_id": pair.observation.observation_id,
            "observation_sha256": pair.observation_sha256,
            "pair_input_sha256": pair_input_sha256,
            "price_idr": price,
            "quantity_after_shares": after,
            "quantity_before_shares": before,
            "raw_event_id": pair.observation.raw_event_id,
            "ticker": pair.observation.ticker,
            "portfolio_state_sha256": pair.portfolio_state_sha256,
            "fixed_notional_policy_sha256": pair.policy_sha256,
            "decision_sha256": _required_hash(decision),
        },
    )
    return FixedNotionalHoldingRecord(
        holding_event_id=event_id,
        **payload,
    )


def _integer_geometry(decision: ShadowDecision) -> _IntegerGeometry:
    if decision.geometry is None:
        raise ShadowContractError("actionable decision has no geometry")
    return _IntegerGeometry(
        entry_high=_integer_price(decision.geometry.entry_high, "entry_high"),
        target=_integer_price(decision.geometry.target_price, "target_price"),
        stop=_integer_price(decision.geometry.stop_loss, "stop_loss"),
    )


def _geometry_for_session(
    geometry: _IntegerGeometry,
    pair: FixedNotionalPairInput,
    session: date,
) -> _IntegerGeometry:
    signal_date = pair.observation.signal_at.astimezone(IDX_TIMEZONE).date()
    factor = Decimal(1)
    for event in pair.bar_series.corporate_action_policy.events:
        if (
            event.kind == "SPLIT"
            and signal_date < event.effective_date <= session
        ):
            factor *= Decimal(str(event.price_factor))
    return _IntegerGeometry(
        entry_high=_decimal_integer(
            Decimal(geometry.entry_high) * factor,
            "split-adjusted entry_high",
        ),
        target=_decimal_integer(
            Decimal(geometry.target) * factor,
            "split-adjusted target",
        ),
        stop=_decimal_integer(
            Decimal(geometry.stop) * factor,
            "split-adjusted stop",
        ),
    )


def _entry_fill(
    bar: FixedNotionalMarketBar,
    geometry: _IntegerGeometry,
) -> tuple[int, bool] | None:
    if bar.open_price_idr <= geometry.entry_high:
        return bar.open_price_idr, False
    if bar.low_price_idr <= geometry.entry_high:
        return geometry.entry_high, True
    return None


def _terminal_for_bar(
    bar: FixedNotionalMarketBar,
    geometry: _IntegerGeometry,
    *,
    intraday_fill: bool,
) -> tuple[
    Literal["STOP_FIRST", "TARGET_FIRST"],
    int,
    bool,
    str,
] | None:
    stop_hit = bar.low_price_idr <= geometry.stop
    target_hit = bar.high_price_idr >= geometry.target
    if not intraday_fill and bar.open_price_idr <= geometry.stop:
        return "STOP_FIRST", bar.open_price_idr, False, "STOP_GAP"
    if not intraday_fill and bar.open_price_idr >= geometry.target:
        return "TARGET_FIRST", bar.open_price_idr, False, "TARGET_GAP"
    if intraday_fill and stop_hit:
        return (
            "STOP_FIRST",
            geometry.stop,
            True,
            "ENTRY_TOUCH_STOP_ORDER_UNKNOWN",
        )
    if intraday_fill and target_hit:
        return None
    if stop_hit and target_hit:
        return "STOP_FIRST", geometry.stop, True, "SAME_BAR_STOP_FIRST"
    if stop_hit:
        return "STOP_FIRST", geometry.stop, False, "STOP_TOUCH"
    if target_hit:
        return "TARGET_FIRST", geometry.target, False, "TARGET_TOUCH"
    return None


def _rights_effective(pair: FixedNotionalPairInput, session: date) -> bool:
    return any(
        event.kind == "RIGHTS" and event.effective_date == session
        for event in pair.bar_series.corporate_action_policy.events
    )


def _apply_split(
    pair: FixedNotionalPairInput,
    session: date,
    quantity: int,
) -> int | None:
    result = Decimal(quantity)
    for event in pair.bar_series.corporate_action_policy.events:
        if event.kind == "SPLIT" and event.effective_date == session:
            result *= Decimal(str(event.quantity_factor))
    if not result.is_finite() or result != result.to_integral_value():
        return None
    converted = int(result)
    return converted if converted > 0 else None


def _eligible_sessions(pair: FixedNotionalPairInput) -> tuple[date, ...]:
    signal_date = pair.observation.signal_at.astimezone(IDX_TIMEZONE).date()
    return tuple(
        item
        for item in pair.trading_calendar.sessions
        if signal_date < item <= pair.manifest.fixed_terminal_date
    )


def _settlement_session(
    calendar: TradingCalendar,
    trade_session: date,
    *,
    lag_sessions: int,
) -> date | None:
    try:
        index = calendar.sessions.index(trade_session)
    except ValueError:
        return None
    target = index + lag_sessions
    return calendar.sessions[target] if target < len(calendar.sessions) else None


def _session_open_at(session: date) -> datetime:
    return datetime.combine(session, SESSION_OPEN, tzinfo=IDX_TIMEZONE)


def _verify_lifecycle_shape(item: FixedNotionalLifecycle) -> None:
    allowed = {
        ("MATURE", "FILLED", "STOP_FIRST"),
        ("MATURE", "FILLED", "TARGET_FIRST"),
        ("MATURE", "FILLED", "TIMEOUT"),
        ("MATURE", "NOT_APPLICABLE", "NO_ACTION"),
        ("MATURE", "EXPIRED_UNFILLED", "UNFILLED"),
        ("PENDING", "PENDING", "PENDING"),
        ("PENDING", "FILLED", "PENDING"),
        ("NOT_ESTIMABLE", "NOT_ESTIMABLE", "NOT_ESTIMABLE"),
        ("NOT_ESTIMABLE", "FILLED", "NOT_ESTIMABLE"),
    }
    state = (item.status, item.fill_status, item.terminal_event)
    if state not in allowed:
        raise ValueError("unsupported fixed-notional lifecycle state")
    _verify_primary_filled_event_prefix(item)
    if item.status == "MATURE" and item.terminal_event in {
        "STOP_FIRST",
        "TARGET_FIRST",
        "TIMEOUT",
    }:
        required = (
            item.filled_at,
            item.closed_at,
            item.entry_lots,
            item.entry_quantity_shares,
            item.exit_quantity_shares,
            item.fill_price_idr,
            item.exit_price_idr,
            item.gross_entry_notional_idr,
            item.residual_idle_cash_idr,
            item.gross_exit_value_idr,
            item.dividend_cash_idr,
            item.entry_cost_idr,
            item.exit_cost_idr,
            item.total_cost_idr,
            item.risk_capital_basis_idr,
        )
        if any(value is None for value in required):
            raise ValueError("mature filled lifecycle is incomplete")
        expected_pnl = (
            int(item.gross_exit_value_idr)
            + int(item.dividend_cash_idr)
            - int(item.gross_entry_notional_idr)
            - int(item.entry_cost_idr)
            - int(item.exit_cost_idr)
        )
        if (
            item.net_pnl_idr.status != "ESTIMABLE"
            or item.net_pnl_idr.value_idr != expected_pnl
        ):
            raise ValueError("lifecycle net P&L arithmetic mismatch")
        expected_ratios = (
            quantize_ratio(expected_pnl, item.target_sleeve_idr),
            quantize_ratio(expected_pnl, int(item.gross_entry_notional_idr)),
            quantize_ratio(expected_pnl, int(item.risk_capital_basis_idr)),
        )
        actual_ratios = (
            item.sleeve_return.value,
            item.acted_trade_return.value,
            item.net_r.value,
        )
        if (
            any(
                metric.status != "ESTIMABLE"
                for metric in (
                    item.sleeve_return,
                    item.acted_trade_return,
                    item.net_r,
                )
            )
            or actual_ratios != expected_ratios
        ):
            raise ValueError("lifecycle return arithmetic mismatch")
        if item.gross_exit_value_idr != (
            int(item.exit_quantity_shares) * int(item.exit_price_idr)
        ):
            raise ValueError("lifecycle exit-value arithmetic mismatch")
        if item.gross_entry_notional_idr != (
            int(item.entry_quantity_shares) * int(item.fill_price_idr)
        ):
            raise ValueError("lifecycle entry-value arithmetic mismatch")
        if item.primary_horizon:
            if (
                len(item.holding_records) < 2
                or item.holding_records[0].event_type != "OPEN"
                or item.holding_records[-1].event_type != "CLOSE"
                or any(
                    event.event_type != "SPLIT_ADJUSTMENT"
                    for event in item.holding_records[1:-1]
                )
            ):
                raise ValueError("primary holding sequence is incomplete")
            if (
                len(item.cash_flow_records) < 2
                or item.cash_flow_records[0].event_type != "ENTRY_DEBIT"
                or item.cash_flow_records[-1].event_type != "EXIT_CREDIT"
                or any(
                    event.event_type != "DIVIDEND_CREDIT"
                    for event in item.cash_flow_records[1:-1]
                )
            ):
                raise ValueError("primary cash-flow sequence is incomplete")
            if (
                item.holding_records[0].quantity_after_shares
                != item.entry_quantity_shares
                or item.holding_records[-1].quantity_before_shares
                != item.exit_quantity_shares
            ):
                raise ValueError("holding sequence differs from lifecycle quantity")
            previous = item.holding_records[0].quantity_after_shares
            for event in item.holding_records[1:]:
                if event.quantity_before_shares != previous:
                    raise ValueError("holding quantity continuity is broken")
                previous = event.quantity_after_shares
            entry = item.cash_flow_records[0]
            exit_flow = item.cash_flow_records[-1]
            dividends = item.cash_flow_records[1:-1]
            if (
                entry.gross_amount_idr != item.gross_entry_notional_idr
                or entry.cost_idr != item.entry_cost_idr
                or exit_flow.gross_amount_idr != item.gross_exit_value_idr
                or exit_flow.cost_idr != item.exit_cost_idr
                or sum(event.gross_amount_idr for event in dividends)
                != item.dividend_cash_idr
                or sum(
                    event.net_cash_change_idr
                    for event in item.cash_flow_records
                )
                != expected_pnl
            ):
                raise ValueError("cash-flow records do not reconcile to lifecycle")
    elif item.terminal_event == "NO_ACTION":
        if (
            item.status != "MATURE"
            or item.fill_status != "NOT_APPLICABLE"
            or item.net_pnl_idr.value_idr != 0
            or item.sleeve_return.value != 0.0
            or item.acted_trade_return.status != "NOT_ESTIMABLE"
            or item.net_r.status != "NOT_ESTIMABLE"
            or item.holding_records
            or item.cash_flow_records
        ):
            raise ValueError("NO_ACTION lifecycle semantics mismatch")
    elif item.terminal_event == "UNFILLED":
        if (
            item.fill_status != "EXPIRED_UNFILLED"
            or item.net_pnl_idr.value_idr != 0
            or item.sleeve_return.value != 0.0
            or item.acted_trade_return.status != "NOT_ESTIMABLE"
            or item.net_r.status != "NOT_ESTIMABLE"
            or item.holding_records
            or item.cash_flow_records
        ):
            raise ValueError("unfilled opportunity/trade metric semantics mismatch")
    elif item.status in {"NOT_ESTIMABLE", "PENDING"}:
        if any(
            metric.status != "NOT_ESTIMABLE"
            for metric in (
                item.net_pnl_idr,
                item.sleeve_return,
                item.acted_trade_return,
                item.net_r,
            )
        ):
            raise ValueError("non-terminal lifecycle metrics must be NOT_ESTIMABLE")
        if item.fill_status == "PENDING" and (
            item.holding_records or item.cash_flow_records
        ):
            raise ValueError("unfilled pending lifecycle cannot carry events")


def _verify_primary_filled_event_prefix(
    item: FixedNotionalLifecycle,
) -> None:
    """Reconcile the open-position prefix consumed later by RS-P2-017."""

    if not item.primary_horizon or item.fill_status != "FILLED":
        return
    required = (
        item.filled_at,
        item.entry_lots,
        item.entry_quantity_shares,
        item.fill_price_idr,
        item.gross_entry_notional_idr,
        item.entry_cash_debit_idr,
        item.residual_idle_cash_idr,
        item.entry_cost_idr,
        item.risk_capital_basis_idr,
    )
    if any(value is None for value in required):
        raise ValueError("primary filled lifecycle lacks entry fields")
    if (
        not item.holding_records
        or item.holding_records[0].event_type != "OPEN"
        or not item.cash_flow_records
        or item.cash_flow_records[0].event_type != "ENTRY_DEBIT"
    ):
        raise ValueError("primary filled lifecycle lacks entry event prefix")
    entry_holding = item.holding_records[0]
    entry_cash = item.cash_flow_records[0]
    if (
        entry_holding.quantity_before_shares != 0
        or entry_holding.quantity_after_shares
        != item.entry_quantity_shares
        or entry_holding.price_idr != item.fill_price_idr
        or entry_cash.quantity_shares != item.entry_quantity_shares
        or entry_cash.price_idr != item.fill_price_idr
        or entry_cash.gross_amount_idr
        != item.gross_entry_notional_idr
        or entry_cash.cost_idr != item.entry_cost_idr
        or -entry_cash.net_cash_change_idr
        != item.entry_cash_debit_idr
        or entry_holding.occurred_at != item.filled_at
        or entry_cash.occurred_at != item.filled_at
    ):
        raise ValueError("primary entry events differ from lifecycle")
    if tuple(
        event.occurred_at for event in item.holding_records
    ) != tuple(sorted(event.occurred_at for event in item.holding_records)):
        raise ValueError("holding events are not chronological")
    if tuple(
        event.occurred_at for event in item.cash_flow_records
    ) != tuple(sorted(event.occurred_at for event in item.cash_flow_records)):
        raise ValueError("cash-flow events are not chronological")
    previous_quantity = entry_holding.quantity_after_shares
    for event in item.holding_records[1:]:
        if event.quantity_before_shares != previous_quantity:
            raise ValueError("holding-event quantity continuity is broken")
        previous_quantity = event.quantity_after_shares
    dividend_cash = sum(
        event.gross_amount_idr
        for event in item.cash_flow_records
        if event.event_type == "DIVIDEND_CREDIT"
    )
    if item.dividend_cash_idr != dividend_cash:
        raise ValueError("dividend cash-flow prefix differs from lifecycle")
    if item.closed_at is not None:
        if (
            item.holding_records[-1].event_type != "CLOSE"
            or item.cash_flow_records[-1].event_type != "EXIT_CREDIT"
            or item.holding_records[-1].occurred_at != item.closed_at
            or item.cash_flow_records[-1].occurred_at != item.closed_at
        ):
            raise ValueError("primary close events differ from lifecycle")


def _integer_price(value: int | float, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    return _decimal_integer(Decimal(str(value)), label)


def _integer_nonnegative(value: int | float, label: str) -> int:
    decimal = Decimal(str(value))
    if not decimal.is_finite() or decimal != decimal.to_integral_value():
        raise ValueError(f"{label} must be exact integer IDR")
    result = int(decimal)
    if result < 0:
        raise ValueError(f"{label} cannot be negative")
    return result


def _decimal_integer(value: Decimal, label: str) -> int:
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError(f"{label} must be exact integer IDR")
    result = int(value)
    if result <= 0:
        raise ValueError(f"{label} must be positive integer IDR")
    return result


def _utc_iso(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("canonical datetime must be timezone-aware")
    return value.astimezone(IDX_TIMEZONE).isoformat()


def _required_hash(model: BaseModel) -> str:
    digest = canonical_sha256(model)
    if digest is None:
        raise ShadowContractError("canonical SHA-256 is unavailable")
    return digest


def _revalidate(model: type[BaseModel], value: BaseModel):
    try:
        return model.model_validate(value.model_dump(mode="python"))
    except ValueError as exc:
        raise ShadowContractError(
            f"{model.__name__} failed trust-boundary validation"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_mapping_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "FIXED_NOTIONAL_BAR_SERIES_VERSION",
    "FIXED_NOTIONAL_CASH_FLOW_VERSION",
    "FIXED_NOTIONAL_CAPABILITY_STATUS",
    "FIXED_NOTIONAL_HOLDING_VERSION",
    "FIXED_NOTIONAL_IDR",
    "FIXED_NOTIONAL_LIFECYCLE_VERSION",
    "FIXED_NOTIONAL_LIQUIDITY_BAR_VERSION",
    "FIXED_NOTIONAL_LIQUIDITY_MEASUREMENT_VERSION",
    "FIXED_NOTIONAL_LIQUIDITY_VERSION",
    "FIXED_NOTIONAL_MARKET_BAR_VERSION",
    "FIXED_NOTIONAL_PAIR_INPUT_VERSION",
    "FIXED_NOTIONAL_PAIRED_RECORD_VERSION",
    "FIXED_NOTIONAL_POLICY_CONFIG_PATH",
    "FIXED_NOTIONAL_POLICY_VERSION",
    "FIXED_NOTIONAL_REFERENCE_VERSION",
    "FixedNotionalArtifactReference",
    "FixedNotionalBarSeries",
    "FixedNotionalCashFlowRecord",
    "FixedNotionalHoldingRecord",
    "FixedNotionalLifecycle",
    "FixedNotionalLiquidityBar",
    "FixedNotionalLiquidityMeasurement",
    "FixedNotionalLiquidityRecord",
    "FixedNotionalMarketBar",
    "FixedNotionalMaturationAuthorizationLoader",
    "FixedNotionalPairAuthorizationLoader",
    "FixedNotionalPairInput",
    "FixedNotionalSizingPlan",
    "FrozenFixedNotionalPolicy",
    "NOT_ESTIMABLE_ENTRY_CAPACITY",
    "NOT_ESTIMABLE_EXIT_CAPACITY",
    "NOT_SIZEABLE_FIXED_NOTIONAL",
    "PairedFixedNotionalRecord",
    "build_fixed_notional_bar_series",
    "build_fixed_notional_liquidity_measurement",
    "build_fixed_notional_liquidity_record",
    "build_fixed_notional_pair_input",
    "build_fixed_notional_policy",
    "canonical_fixed_notional_bar_source_sha256",
    "canonical_fixed_notional_event_id",
    "canonical_fixed_notional_lifecycle_id",
    "canonical_fixed_notional_liquidity_payload_sha256",
    "canonical_fixed_notional_liquidity_record_id",
    "canonical_fixed_notional_liquidity_source_sha256",
    "canonical_fixed_notional_paired_record_id",
    "derive_paired_fixed_notional_record",
    "evaluate_fixed_notional_pair",
    "fixed_notional_cost_idr",
    "fixed_notional_lot_count",
    "replay_fixed_notional_pair",
    "verify_fixed_notional_policy_binding",
    "verify_paired_fixed_notional_record",
]
