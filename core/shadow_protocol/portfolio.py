"""Frozen, evaluation-only portfolio-state evidence for RS-P2-014.

This module is additive.  It does not size a trade, evolve a portfolio, grant
collection authority, or reinterpret the existing one-lot outcome contract.
All persisted money is strict integer IDR.  Ratios are quantized before they
enter canonical JSON.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
import hashlib
import json
from pathlib import Path
import re
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

from .contracts import (
    ComponentID,
    ContentHash,
    FrozenParameter,
    IDX_TIMEZONE,
    SHADOW_PROTOCOL_MANIFEST_VERSION,
    ShadowContractError,
    ShadowObservation,
    ShadowOutcome,
    ShadowProtocolManifest,
    canonical_json_bytes,
    canonical_sha256,
)
from .evidence import (
    CandidateEvent,
    CandidateSetManifest,
    CandidateSetStore,
    FrozenSnapshot,
    LineageBundle,
    RawCandidateSetCapture,
    build_lineage_bundle,
)


PORTFOLIO_BINDING_PROFILE = "portfolio-binding-v1"
PORTFOLIO_POLICY_CONFIG_PATH = "config/portfolio-policy-v1.json"
PORTFOLIO_POLICY_VERSION = "shadow-portfolio-policy-v1"
PORTFOLIO_STATE_SOURCE_VERSION = "shadow-portfolio-state-source-v1"
PORTFOLIO_STATE_SOURCE_REFERENCE_VERSION = (
    "shadow-portfolio-state-source-reference-v1"
)
PORTFOLIO_STATE_VERSION = "shadow-portfolio-state-v1"
PORTFOLIO_STATE_REFERENCE_VERSION = "shadow-portfolio-state-reference-v1"
PORTFOLIO_LINEAGE_BUNDLE_VERSION = "shadow-lineage-bundle-v2"
PORTFOLIO_CAPABILITY_STATUS = "RS_P2_014_ONLY_NOT_A1_ELIGIBLE"
PORTFOLIO_MANIFEST_PARAMETER_NAMES = frozenset(
    {
        "portfolio_binding_profile",
        "portfolio_policy_contract_version",
        "phase2_capability_status",
        "starting_capital_idr",
        "fixed_notional_idr",
        "minimum_adt_idr",
        "max_participation_fraction",
        "participation_evidence_class",
        "target_deployment_fraction",
        "effective_fixed_notional_max_deployment_fraction",
        "minimum_cash_reserve_fraction",
        "max_gross_exposure_fraction",
        "base_max_concurrent_positions",
        "bull_max_positions",
        "sideways_max_positions",
        "bear_stress_max_positions",
        "unknown_max_positions",
        "total_loss_budget_fraction",
        "max_portfolio_heat_fraction",
        "daily_loss_stop_fraction",
        "max_sector_exposure_fraction",
        "max_drawdown_stop_fraction",
        "sector_max_names",
        "cluster_max_names",
        "lot_size_shares",
        "settlement_lag_sessions",
        "ratio_quantization_decimal_places",
        "cost_application_rounding_rule",
    }
)

APPROVED_STARTING_CAPITAL_IDR = 100_000_000
APPROVED_FIXED_NOTIONAL_IDR = 13_000_000
APPROVED_MINIMUM_ADTV_IDR = 10_000_000_000
APPROVED_MAX_PARTICIPATION_FRACTION = 0.0013
APPROVED_TARGET_DEPLOYMENT_FRACTION = 0.65
APPROVED_EFFECTIVE_FIXED_NOTIONAL_MAX_DEPLOYMENT_FRACTION = 0.39
APPROVED_MINIMUM_CASH_RESERVE_FRACTION = 0.05
APPROVED_MAX_GROSS_EXPOSURE_FRACTION = 0.95
APPROVED_TOTAL_LOSS_BUDGET_FRACTION = 0.02
APPROVED_MAX_PORTFOLIO_HEAT_FRACTION = 0.013
APPROVED_DAILY_LOSS_STOP_FRACTION = 0.03
APPROVED_RATIO_QUANTIZATION_DECIMAL_PLACES = 12

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
StrictNonNegativeInt = Annotated[StrictInt, Field(ge=0)]
StrictPositiveInt = Annotated[StrictInt, Field(gt=0)]
EstimabilityStatus: TypeAlias = Literal["ESTIMABLE", "NOT_ESTIMABLE"]

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_RATIO_QUANTUM = Decimal("1e-12")
_NOT_ESTIMABLE_NAV_REASON = "RS_P2_017_DAILY_NAV_NOT_IMPLEMENTED"


class _PortfolioModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class _EvaluationOnlyPortfolioArtifact(_PortfolioModel):
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    affects_execution: Literal[False] = False
    affects_ranking: Literal[False] = False
    affects_sizing: Literal[False] = False


class EstimableMoney(_PortfolioModel):
    """An exact integer-IDR amount or an explicit missing measurement."""

    status: EstimabilityStatus
    value_idr: StrictInt | None
    reason_codes: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def verify_estimability(self) -> EstimableMoney:
        _verify_estimability(self.status, self.value_idr, self.reason_codes)
        return self


class EstimableRatio(_PortfolioModel):
    """A pre-quantized finite ratio or an explicit missing measurement."""

    status: EstimabilityStatus
    value: StrictFloat | None
    reason_codes: tuple[NonEmptyString, ...] = ()

    @field_validator("value", mode="before")
    @classmethod
    def require_strict_float(
        cls,
        value: object,
    ) -> object:
        if value is not None and type(value) is not float:
            raise ValueError("persisted ratio values must be strict floats")
        return value

    @field_validator("value")
    @classmethod
    def normalize_negative_zero(cls, value: float | None) -> float | None:
        if value == 0.0:
            return 0.0
        return value

    @model_validator(mode="after")
    def verify_estimability(self) -> EstimableRatio:
        _verify_estimability(self.status, self.value, self.reason_codes)
        if self.value is not None and self.value != quantize_ratio_decimal(
            Decimal(str(self.value))
        ):
            raise ValueError("ratio value must be quantized before persistence")
        return self


class EstimableCount(_PortfolioModel):
    """A strict non-negative count or an explicit missing measurement."""

    status: EstimabilityStatus
    value: StrictNonNegativeInt | None
    reason_codes: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def verify_estimability(self) -> EstimableCount:
        _verify_estimability(self.status, self.value, self.reason_codes)
        return self


class FrozenPortfolioPolicy(_EvaluationOnlyPortfolioArtifact):
    """Owner-approved RS-P2-014 portfolio assumptions and provenance."""

    contract_version: Literal["shadow-portfolio-policy-v1"] = (
        PORTFOLIO_POLICY_VERSION
    )
    binding_profile: Literal["portfolio-binding-v1"] = PORTFOLIO_BINDING_PROFILE
    policy_id: NonEmptyString
    policy_scope: Literal[
        "DECISION_FIXED_NOTIONAL_POLICY_NAV_ASSUMPTIONS"
    ] = "DECISION_FIXED_NOTIONAL_POLICY_NAV_ASSUMPTIONS"
    phase2_capability_status: Literal[
        "RS_P2_014_ONLY_NOT_A1_ELIGIBLE"
    ] = PORTFOLIO_CAPABILITY_STATUS
    state_origin: Literal["SIMULATED_PAPER_CONTROL"] = "SIMULATED_PAPER_CONTROL"
    currency: Literal["IDR"] = "IDR"

    starting_capital_idr: StrictPositiveInt
    fixed_notional_idr: StrictPositiveInt
    fixed_notional_fraction: float = Field(ge=0.0, le=1.0)
    target_deployment_fraction: float = Field(ge=0.0, le=1.0)
    target_deployment_semantics: Literal["SIZING_BASIS"] = "SIZING_BASIS"
    effective_fixed_notional_max_positions: StrictPositiveInt
    effective_fixed_notional_max_deployment_fraction: float = Field(
        ge=0.0,
        le=1.0,
    )

    money_storage_rule: Literal["INTEGER_IDR_EXACT"] = "INTEGER_IDR_EXACT"
    cost_application_rounding_rule: Literal[
        "AGGREGATE_APPLICABLE_BPS_THEN_CEIL_INTEGER_IDR"
    ] = "AGGREGATE_APPLICABLE_BPS_THEN_CEIL_INTEGER_IDR"
    ratio_quantization_decimal_places: Literal[12] = (
        APPROVED_RATIO_QUANTIZATION_DECIMAL_PLACES
    )
    ratio_quantization_rounding_mode: Literal["ROUND_HALF_EVEN"] = (
        "ROUND_HALF_EVEN"
    )

    lot_size_shares: StrictPositiveInt
    fixed_notional_rounding_rule: Literal[
        "FLOOR_TO_WHOLE_BOARD_LOTS_WITHOUT_EXCEEDING_NOTIONAL"
    ] = "FLOOR_TO_WHOLE_BOARD_LOTS_WITHOUT_EXCEEDING_NOTIONAL"
    insufficient_notional_rule: Literal["NOT_ESTIMABLE"] = "NOT_ESTIMABLE"
    cash_reservation_rule: Literal["READ_ONLY_NO_NEW_RESERVATION"] = (
        "READ_ONLY_NO_NEW_RESERVATION"
    )
    unsettled_cash_rule: Literal["NOT_DEPLOYABLE_UNTIL_SETTLED"] = (
        "NOT_DEPLOYABLE_UNTIL_SETTLED"
    )
    settlement_lag_sessions: StrictNonNegativeInt
    minimum_cash_reserve_fraction: float = Field(ge=0.0, le=1.0)
    cash_reserve_denominator: Literal["STARTING_CAPITAL"] = "STARTING_CAPITAL"
    allocation_fraction_denominator: Literal["STARTING_CAPITAL"] = (
        "STARTING_CAPITAL"
    )
    realized_loss_denominator: Literal["STARTING_CAPITAL"] = "STARTING_CAPITAL"
    realized_loss_sign_convention: Literal["POSITIVE_LOSS_MAGNITUDE"] = (
        "POSITIVE_LOSS_MAGNITUDE"
    )
    circuit_breaker_comparison_rule: Literal[
        "GREATER_THAN_OR_EQUAL"
    ] = "GREATER_THAN_OR_EQUAL"
    zero_nav_rule: Literal[
        "RETAIN_INSOLVENCY_AND_MARK_DENOMINATOR_RATIOS_NOT_ESTIMABLE"
    ] = "RETAIN_INSOLVENCY_AND_MARK_DENOMINATOR_RATIOS_NOT_ESTIMABLE"
    leverage_allowed: Literal[False] = False
    shorting_allowed: Literal[False] = False

    liquidity_source_id: NonEmptyString
    liquidity_source_definition_sha256: Sha256
    liquidity_measure_basis: Literal[
        "MEAN_CLOSE_X_VOLUME_LAST_20_COMPLETED_SESSIONS"
    ] = "MEAN_CLOSE_X_VOLUME_LAST_20_COMPLETED_SESSIONS"
    liquidity_lookback_sessions: Literal[20] = 20
    minimum_adt_idr: StrictPositiveInt
    liquidity_expiry_rule: NonEmptyString
    max_participation_fraction: float = Field(ge=0.0, le=1.0)
    participation_evidence_class: Literal["DERIVED_NOT_CALIBRATED"] = (
        "DERIVED_NOT_CALIBRATED"
    )
    participation_derivation_numerator_idr: StrictPositiveInt
    participation_derivation_denominator_idr: StrictPositiveInt
    capacity_rounding_rule: Literal[
        "FLOOR_TO_WHOLE_BOARD_LOTS"
    ] = "FLOOR_TO_WHOLE_BOARD_LOTS"
    unmeasurable_capacity_state: Literal["NOT_ESTIMABLE"] = "NOT_ESTIMABLE"

    base_max_concurrent_positions: StrictPositiveInt
    bull_max_positions: StrictNonNegativeInt
    sideways_max_positions: StrictNonNegativeInt
    bear_stress_max_positions: StrictNonNegativeInt
    unknown_max_positions: StrictNonNegativeInt
    total_loss_budget_fraction: float = Field(ge=0.0, le=1.0)
    max_portfolio_heat_fraction: float = Field(ge=0.0, le=1.0)
    portfolio_heat_denominator: Literal["STARTING_CAPITAL"] = "STARTING_CAPITAL"
    max_gross_exposure_fraction: float = Field(ge=0.0, le=1.0)
    max_sector_exposure_fraction: None = None
    sector_max_names: StrictPositiveInt
    cluster_max_names: StrictPositiveInt
    daily_loss_stop_fraction: float = Field(ge=0.0, le=1.0)
    max_drawdown_stop_fraction: None = None
    nav_drawdown_gate_status: Literal[
        "NOT_ESTIMABLE_UNTIL_RS_P2_017_NEW_PROTOCOL"
    ] = "NOT_ESTIMABLE_UNTIL_RS_P2_017_NEW_PROTOCOL"
    same_timestamp_priority_rule: Literal[
        "SOURCE_ROW_NUMBER_ASC_THEN_TICKER_ASC"
    ] = "SOURCE_ROW_NUMBER_ASC_THEN_TICKER_ASC"
    partial_fill_rule: Literal["TASK_GATED_TO_RS_P2_015"] = (
        "TASK_GATED_TO_RS_P2_015"
    )

    portfolio_source_id: NonEmptyString
    portfolio_source_definition_sha256: Sha256
    mark_price_source_id: NonEmptyString
    mark_price_source_definition_sha256: Sha256
    entry_price_source_rule: Literal[
        "SOURCE_SUPPLIED_INTEGER_IDR"
    ] = "SOURCE_SUPPLIED_INTEGER_IDR"
    price_rounding_rule: NonEmptyString
    mark_price_rule: Literal[
        "POINT_IN_TIME_SOURCE_INTEGER_IDR"
    ] = "POINT_IN_TIME_SOURCE_INTEGER_IDR"
    stale_mark_rule: Literal["NOT_ESTIMABLE"] = "NOT_ESTIMABLE"
    fractional_entitlement_rule: Literal[
        "EXACT_INTEGER_CASH_IN_LIEU_OR_NOT_ESTIMABLE"
    ] = "EXACT_INTEGER_CASH_IN_LIEU_OR_NOT_ESTIMABLE"
    odd_lot_rule: Literal["INTEGER_SHARES_ALLOWED"] = "INTEGER_SHARES_ALLOWED"
    cash_in_lieu_rule: Literal[
        "EXACT_INTEGER_IDR_WITH_SOURCE_LINEAGE"
    ] = "EXACT_INTEGER_IDR_WITH_SOURCE_LINEAGE"
    corporate_action_policy_sha256: Sha256
    trading_calendar_sha256: Sha256
    cost_assumptions_sha256: Sha256
    methodology_document_sha256: Sha256

    @field_validator(
        "fixed_notional_fraction",
        "target_deployment_fraction",
        "effective_fixed_notional_max_deployment_fraction",
        "minimum_cash_reserve_fraction",
        "max_participation_fraction",
        "total_loss_budget_fraction",
        "max_portfolio_heat_fraction",
        "max_gross_exposure_fraction",
        "daily_loss_stop_fraction",
        mode="before",
    )
    @classmethod
    def require_strict_float_ratios(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("persisted policy ratios must be strict floats")
        return value

    @model_validator(mode="after")
    def verify_owner_approved_policy(self) -> FrozenPortfolioPolicy:
        exact_integers = {
            "starting_capital_idr": APPROVED_STARTING_CAPITAL_IDR,
            "fixed_notional_idr": APPROVED_FIXED_NOTIONAL_IDR,
            "lot_size_shares": 100,
            "settlement_lag_sessions": 2,
            "minimum_adt_idr": APPROVED_MINIMUM_ADTV_IDR,
            "participation_derivation_numerator_idr": (
                APPROVED_FIXED_NOTIONAL_IDR
            ),
            "participation_derivation_denominator_idr": (
                APPROVED_MINIMUM_ADTV_IDR
            ),
            "base_max_concurrent_positions": 5,
            "bull_max_positions": 3,
            "sideways_max_positions": 2,
            "bear_stress_max_positions": 1,
            "unknown_max_positions": 0,
            "sector_max_names": 2,
            "cluster_max_names": 2,
        }
        for field_name, expected in exact_integers.items():
            if getattr(self, field_name) != expected:
                raise ValueError(
                    f"{field_name} differs from the owner-approved value"
                )
        exact_ratios = {
            "fixed_notional_fraction": Decimal("0.13"),
            "target_deployment_fraction": Decimal("0.65"),
            "effective_fixed_notional_max_deployment_fraction": Decimal("0.39"),
            "minimum_cash_reserve_fraction": Decimal("0.05"),
            "max_participation_fraction": Decimal("0.0013"),
            "total_loss_budget_fraction": Decimal("0.02"),
            "max_portfolio_heat_fraction": Decimal("0.013"),
            "max_gross_exposure_fraction": Decimal("0.95"),
            "daily_loss_stop_fraction": Decimal("0.03"),
        }
        for field_name, expected in exact_ratios.items():
            if Decimal(str(getattr(self, field_name))) != expected:
                raise ValueError(
                    f"{field_name} differs from the owner-approved value"
                )
        if quantize_ratio(
            self.fixed_notional_idr,
            self.starting_capital_idr,
        ) != self.fixed_notional_fraction:
            raise ValueError("fixed-notional fraction derivation mismatch")
        expected_effective = quantize_ratio(
            self.effective_fixed_notional_max_positions
            * self.fixed_notional_idr,
            self.starting_capital_idr,
        )
        if expected_effective != self.effective_fixed_notional_max_deployment_fraction:
            raise ValueError("effective fixed-notional deployment mismatch")
        if quantize_ratio(
            self.participation_derivation_numerator_idr,
            self.participation_derivation_denominator_idr,
        ) != self.max_participation_fraction:
            raise ValueError("participation-cap derivation mismatch")
        return self


class FrozenPortfolioSourcePosition(_PortfolioModel):
    position_id: NonEmptyString
    ticker: CanonicalTicker
    opened_at: datetime
    entry_quantity_lots: StrictPositiveInt
    current_quantity_shares: StrictPositiveInt
    quantity_origin: Literal["ENTRY_LOT_ROUNDED", "CORPORATE_ACTION_ADJUSTED"]
    quantity_adjustment_event_sha256: Sha256 | None = None
    total_cost_basis_idr: StrictPositiveInt
    mark_price_idr: StrictPositiveInt
    mark_as_of: datetime
    planned_stop_price_idr: StrictPositiveInt | None = None
    source_record_sha256: Sha256

    @field_validator("opened_at", "mark_as_of")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("position datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_quantity_origin(self) -> FrozenPortfolioSourcePosition:
        adjusted = self.quantity_origin == "CORPORATE_ACTION_ADJUSTED"
        if adjusted != (self.quantity_adjustment_event_sha256 is not None):
            raise ValueError(
                "corporate-action quantity needs exactly one adjustment hash"
            )
        if self.opened_at > self.mark_as_of:
            raise ValueError("position mark cannot precede position opening")
        if (
            not adjusted
            and self.current_quantity_shares != self.entry_quantity_lots * 100
        ):
            raise ValueError("entry-lot quantity does not equal whole board lots")
        return self


class FrozenPortfolioSourceCommitment(_PortfolioModel):
    commitment_id: NonEmptyString
    ticker: CanonicalTicker
    created_at: datetime
    expires_at: datetime
    reserved_cash_idr: StrictNonNegativeInt
    potential_exposure_idr: StrictNonNegativeInt
    potential_risk_idr: StrictNonNegativeInt
    source_decision_sha256: Sha256
    status: Literal["PENDING"]

    @field_validator("created_at", "expires_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("commitment datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_chronology(self) -> FrozenPortfolioSourceCommitment:
        if self.expires_at <= self.created_at:
            raise ValueError("commitment expiry must follow creation")
        return self


class FrozenPortfolioSourcePayload(_PortfolioModel):
    settled_cash: EstimableMoney
    unsettled_cash_receivable: EstimableMoney
    reserved_cash: EstimableMoney
    realized_pnl_today: EstimableMoney
    control_30d_closed_trade_avg_pnl: EstimableRatio
    positions_status: EstimabilityStatus
    positions: tuple[FrozenPortfolioSourcePosition, ...] = ()
    pending_commitments_status: EstimabilityStatus
    pending_commitments: tuple[FrozenPortfolioSourceCommitment, ...] = ()

    @model_validator(mode="after")
    def verify_collections(self) -> FrozenPortfolioSourcePayload:
        _verify_collection_status(
            self.positions_status,
            self.positions,
            "positions",
        )
        _verify_collection_status(
            self.pending_commitments_status,
            self.pending_commitments,
            "pending commitments",
        )
        position_keys = tuple((item.ticker, item.position_id) for item in self.positions)
        if position_keys != tuple(sorted(position_keys)):
            raise ValueError("source positions must be sorted by ticker and ID")
        position_ids = tuple(item.position_id for item in self.positions)
        tickers = tuple(item.ticker for item in self.positions)
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("source position IDs must be globally unique")
        if len(tickers) != len(set(tickers)):
            raise ValueError("RS-P2-014 permits one source position per ticker")
        commitment_keys = tuple(
            (item.created_at, item.ticker, item.commitment_id)
            for item in self.pending_commitments
        )
        if commitment_keys != tuple(sorted(commitment_keys)):
            raise ValueError("source commitments must use deterministic order")
        commitment_ids = tuple(
            item.commitment_id for item in self.pending_commitments
        )
        if len(commitment_ids) != len(set(commitment_ids)):
            raise ValueError("source commitment IDs must be globally unique")
        for label, money in (
            ("settled cash", self.settled_cash),
            ("unsettled cash receivable", self.unsettled_cash_receivable),
            ("reserved cash", self.reserved_cash),
        ):
            _require_nonnegative_money(label, money)
        if self.pending_commitments_status == "NOT_ESTIMABLE":
            if self.reserved_cash.status != "NOT_ESTIMABLE":
                raise ValueError(
                    "unknown commitments require reserved cash NOT_ESTIMABLE"
                )
        elif self.reserved_cash.status == "ESTIMABLE":
            expected_reserved = sum(
                item.reserved_cash_idr for item in self.pending_commitments
            )
            if self.reserved_cash.value_idr != expected_reserved:
                raise ValueError(
                    "reserved cash differs from pending commitments"
                )
        return self


class PortfolioStateSourceRecord(_EvaluationOnlyPortfolioArtifact):
    contract_version: Literal["shadow-portfolio-state-source-v1"] = (
        PORTFOLIO_STATE_SOURCE_VERSION
    )
    source_record_id: NonEmptyString
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_revision: int = Field(ge=1)
    manifest_sha256: Sha256
    source_id: NonEmptyString
    source_definition_sha256: Sha256
    source_as_of: datetime
    source_expires_at: datetime | None = None
    captured_at: datetime
    payload_json: NonEmptyString
    payload_sha256: Sha256

    @field_validator("source_as_of", "source_expires_at", "captured_at")
    @classmethod
    def require_aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("source-record datetimes must be timezone-aware")
        return value

    @field_validator("payload_json")
    @classmethod
    def require_canonical_payload(cls, value: str) -> str:
        payload = _strict_json_object(value.encode("utf-8"), "portfolio payload")
        trusted = FrozenPortfolioSourcePayload.model_validate(payload)
        if canonical_json_bytes(trusted).decode("utf-8") != value:
            raise ValueError("portfolio payload must be canonical JSON")
        return value

    @model_validator(mode="after")
    def verify_source_record(self) -> PortfolioStateSourceRecord:
        if self.source_as_of > self.captured_at:
            raise ValueError("source vintage cannot follow source capture")
        if (
            self.source_expires_at is not None
            and self.source_expires_at <= self.source_as_of
        ):
            raise ValueError("source expiry must follow source vintage")
        if _sha256(self.payload_json.encode("utf-8")) != self.payload_sha256:
            raise ValueError("portfolio source payload SHA-256 mismatch")
        return self

    @property
    def payload(self) -> FrozenPortfolioSourcePayload:
        return FrozenPortfolioSourcePayload.model_validate_json(self.payload_json)


class PortfolioStateSourceReference(_EvaluationOnlyPortfolioArtifact):
    contract_version: Literal[
        "shadow-portfolio-state-source-reference-v1"
    ] = PORTFOLIO_STATE_SOURCE_REFERENCE_VERSION
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    source_record_id: NonEmptyString
    source_record_contract_version: Literal["shadow-portfolio-state-source-v1"]
    source_record_canonical_sha256: Sha256
    source_record_raw_file_sha256: Sha256
    source_record_raw_byte_length: StrictPositiveInt
    source_record_relative_path: NonEmptyString
    source_definition_sha256: Sha256
    payload_sha256: Sha256


class FrozenPositionState(_PortfolioModel):
    position_id: NonEmptyString
    ticker: CanonicalTicker
    opened_at: datetime
    entry_quantity_lots: StrictPositiveInt
    current_quantity_shares: StrictPositiveInt
    quantity_origin: Literal["ENTRY_LOT_ROUNDED", "CORPORATE_ACTION_ADJUSTED"]
    quantity_adjustment_event_sha256: Sha256 | None = None
    total_cost_basis_idr: StrictPositiveInt
    mark_price_idr: StrictPositiveInt
    mark_as_of: datetime
    market_value: EstimableMoney
    planned_stop_price_idr: StrictPositiveInt | None = None
    allocation_fraction: EstimableRatio
    risk_to_stop: EstimableMoney
    source_record_sha256: Sha256

    @field_validator("opened_at", "mark_as_of")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("position datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_exact_position_money(self) -> FrozenPositionState:
        adjusted = self.quantity_origin == "CORPORATE_ACTION_ADJUSTED"
        if adjusted != (self.quantity_adjustment_event_sha256 is not None):
            raise ValueError(
                "corporate-action quantity needs exactly one adjustment hash"
            )
        if self.opened_at > self.mark_as_of:
            raise ValueError("position mark cannot precede position opening")
        if (
            not adjusted
            and self.current_quantity_shares != self.entry_quantity_lots * 100
        ):
            raise ValueError("entry-lot quantity does not equal whole board lots")
        expected_value = self.current_quantity_shares * self.mark_price_idr
        if self.market_value != estimable_money(expected_value):
            raise ValueError("position market value is not exact integer IDR")
        if self.planned_stop_price_idr is None:
            if self.risk_to_stop.status != "NOT_ESTIMABLE":
                raise ValueError("position without stop has non-estimable risk")
        else:
            expected_risk = (
                max(self.mark_price_idr - self.planned_stop_price_idr, 0)
                * self.current_quantity_shares
            )
            if self.risk_to_stop != estimable_money(expected_risk):
                raise ValueError("position risk-to-stop mismatch")
        return self


class FrozenPendingCommitment(_PortfolioModel):
    commitment_id: NonEmptyString
    ticker: CanonicalTicker
    created_at: datetime
    expires_at: datetime
    reserved_cash: EstimableMoney
    potential_exposure: EstimableMoney
    potential_risk: EstimableMoney
    source_decision_sha256: Sha256
    status: Literal["PENDING"]

    @field_validator("created_at", "expires_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("commitment datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_chronology(self) -> FrozenPendingCommitment:
        if self.expires_at <= self.created_at:
            raise ValueError("commitment expiry must follow creation")
        return self


class PortfolioState(_EvaluationOnlyPortfolioArtifact):
    """One immutable pre-batch control portfolio reference state."""

    contract_version: Literal["shadow-portfolio-state-v1"] = (
        PORTFOLIO_STATE_VERSION
    )
    implementation_profile: Literal["RS-P2-014"] = "RS-P2-014"
    portfolio_state_id: NonEmptyString
    state_role: Literal["CONTROL_FROZEN_REFERENCE"] = "CONTROL_FROZEN_REFERENCE"
    portfolio_path_id: NonEmptyString
    state_sequence: Literal[0] = 0
    previous_state_sha256: None = None

    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_contract_version: Literal["shadow-protocol-manifest-v2"] = (
        SHADOW_PROTOCOL_MANIFEST_VERSION
    )
    manifest_revision: int = Field(ge=1)
    manifest_sha256: Sha256
    baseline_manifest_id: NonEmptyString
    baseline_manifest_sha256: Sha256

    opportunity_set_id: NonEmptyString
    opportunity_set_sha256: Sha256
    raw_capture_id: NonEmptyString
    raw_capture_sha256: Sha256
    raw_capture_captured_at: datetime
    candidate_set_id: NonEmptyString
    candidate_set_sha256: Sha256
    candidate_set_captured_at: datetime
    signal_at: datetime
    as_of_date: date
    state_session_date: date
    state_as_of: datetime
    captured_at: datetime
    trading_calendar_id: NonEmptyString
    trading_calendar_sha256: Sha256

    portfolio_policy_id: NonEmptyString
    portfolio_policy_sha256: Sha256
    portfolio_policy: FrozenPortfolioPolicy
    portfolio_source_id: NonEmptyString
    portfolio_source_definition_sha256: Sha256
    portfolio_source_record_id: NonEmptyString
    portfolio_source_record_sha256: Sha256
    portfolio_source_payload_sha256: Sha256
    source_as_of: datetime
    source_expires_at: datetime | None = None

    starting_capital_idr: StrictPositiveInt
    settled_cash: EstimableMoney
    unsettled_cash_receivable: EstimableMoney
    reserved_cash: EstimableMoney
    deployable_cash: EstimableMoney
    marked_positions_value: EstimableMoney
    nav: EstimableMoney
    peak_nav: EstimableMoney
    nav_drawdown: EstimableRatio
    gross_exposure: EstimableMoney
    net_exposure: EstimableMoney
    positions_status: EstimabilityStatus
    positions: tuple[FrozenPositionState, ...] = ()
    pending_commitments_status: EstimabilityStatus
    pending_commitments: tuple[FrozenPendingCommitment, ...] = ()
    open_positions_count: EstimableCount
    open_risk: EstimableMoney
    portfolio_heat: EstimableRatio
    realized_pnl_today: EstimableMoney
    realized_loss_today: EstimableRatio
    control_30d_closed_trade_avg_pnl: EstimableRatio
    circuit_breaker_active: bool | None
    portfolio_status: Literal["ACTIVE", "INSOLVENT", "NOT_ESTIMABLE"]
    portfolio_gate_inputs_complete: bool
    state_completeness: Literal[
        "COMPLETE",
        "PARTIAL_CONTROL_OBSERVED",
        "NOT_ESTIMABLE",
    ]
    missing_state_fields: tuple[NonEmptyString, ...] = ()

    @field_validator(
        "raw_capture_captured_at",
        "candidate_set_captured_at",
        "signal_at",
        "state_as_of",
        "captured_at",
        "source_as_of",
        "source_expires_at",
    )
    @classmethod
    def require_aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("portfolio-state datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_state(self) -> PortfolioState:
        if self.as_of_date != self.signal_at.astimezone(IDX_TIMEZONE).date():
            raise ValueError("state date must equal IDX-local signal date")
        if self.state_session_date != self.as_of_date:
            raise ValueError("RS-P2-014 state session must equal signal date")
        if not (
            self.source_as_of
            <= self.state_as_of
            <= self.signal_at
            <= self.captured_at
        ):
            raise ValueError("portfolio-state source/signal chronology is invalid")
        if not (
            self.raw_capture_captured_at
            <= self.candidate_set_captured_at
            <= self.captured_at
        ):
            raise ValueError("portfolio-state capture chronology is invalid")
        if (
            self.source_expires_at is not None
            and self.source_expires_at <= self.signal_at
        ):
            raise ValueError("expired portfolio source cannot produce state")
        if canonical_sha256(self.portfolio_policy) != self.portfolio_policy_sha256:
            raise ValueError("embedded portfolio policy hash mismatch")
        if self.portfolio_policy.policy_id != self.portfolio_policy_id:
            raise ValueError("embedded portfolio policy ID mismatch")
        if self.starting_capital_idr != self.portfolio_policy.starting_capital_idr:
            raise ValueError("state starting capital differs from policy")
        if (
            self.portfolio_source_id
            != self.portfolio_policy.portfolio_source_id
            or self.portfolio_source_definition_sha256
            != self.portfolio_policy.portfolio_source_definition_sha256
        ):
            raise ValueError("state source differs from portfolio policy")
        if (
            self.trading_calendar_sha256
            != self.portfolio_policy.trading_calendar_sha256
        ):
            raise ValueError("state calendar differs from portfolio policy")
        expected_state_id = canonical_portfolio_state_id(
            protocol_id=self.protocol_id,
            manifest_sha256=self.manifest_sha256,
            raw_capture_sha256=self.raw_capture_sha256,
            candidate_set_sha256=self.candidate_set_sha256,
            portfolio_policy_sha256=self.portfolio_policy_sha256,
            portfolio_source_record_sha256=self.portfolio_source_record_sha256,
        )
        if self.portfolio_state_id != expected_state_id:
            raise ValueError("portfolio_state_id is not deterministic")
        self._verify_collections_and_arithmetic()
        return self

    def _verify_collections_and_arithmetic(self) -> None:
        _verify_collection_status(self.positions_status, self.positions, "positions")
        _verify_collection_status(
            self.pending_commitments_status,
            self.pending_commitments,
            "pending commitments",
        )
        position_keys = tuple((item.ticker, item.position_id) for item in self.positions)
        if position_keys != tuple(sorted(position_keys)):
            raise ValueError("positions must be sorted by ticker and ID")
        position_ids = tuple(item.position_id for item in self.positions)
        tickers = tuple(item.ticker for item in self.positions)
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("position IDs must be globally unique")
        if len(tickers) != len(set(tickers)):
            raise ValueError("RS-P2-014 permits one position per ticker")
        if any(
            item.opened_at > self.state_as_of or item.mark_as_of > self.state_as_of
            for item in self.positions
        ):
            raise ValueError("position fact is newer than state_as_of")
        commitment_keys = tuple(
            (item.created_at, item.ticker, item.commitment_id)
            for item in self.pending_commitments
        )
        if commitment_keys != tuple(sorted(commitment_keys)):
            raise ValueError("commitments must use deterministic order")
        commitment_ids = tuple(
            item.commitment_id for item in self.pending_commitments
        )
        if len(commitment_ids) != len(set(commitment_ids)):
            raise ValueError("commitment IDs must be globally unique")
        if any(item.created_at > self.state_as_of for item in self.pending_commitments):
            raise ValueError("commitment is newer than state_as_of")
        if any(item.expires_at <= self.state_as_of for item in self.pending_commitments):
            raise ValueError("expired commitment cannot remain PENDING")
        for label, money in (
            ("settled cash", self.settled_cash),
            ("unsettled cash receivable", self.unsettled_cash_receivable),
            ("reserved cash", self.reserved_cash),
            ("deployable cash", self.deployable_cash),
            ("marked positions value", self.marked_positions_value),
            ("NAV", self.nav),
            ("gross exposure", self.gross_exposure),
            ("net exposure", self.net_exposure),
            ("open risk", self.open_risk),
        ):
            _require_nonnegative_money(label, money)

        expected_marked = _sum_estimable_money(
            tuple(item.market_value for item in self.positions),
            missing_reason="POSITION_VALUE_NOT_ESTIMABLE",
            collection_status=self.positions_status,
        )
        _require_model_equal(
            "marked positions value",
            self.marked_positions_value,
            expected_marked,
        )
        expected_count = (
            estimable_count(len(self.positions))
            if self.positions_status == "ESTIMABLE"
            else not_estimable_count("POSITIONS_NOT_ESTIMABLE")
        )
        _require_model_equal(
            "open positions count",
            self.open_positions_count,
            expected_count,
        )
        expected_open_risk = _sum_estimable_money(
            tuple(item.risk_to_stop for item in self.positions),
            missing_reason="POSITION_RISK_NOT_ESTIMABLE",
            collection_status=self.positions_status,
        )
        _require_model_equal("open risk", self.open_risk, expected_open_risk)

        if self.pending_commitments_status == "NOT_ESTIMABLE":
            expected_reserved = not_estimable_money(
                "PENDING_COMMITMENTS_NOT_ESTIMABLE"
            )
        else:
            expected_reserved = _sum_estimable_money(
                tuple(item.reserved_cash for item in self.pending_commitments),
                missing_reason="COMMITMENT_RESERVE_NOT_ESTIMABLE",
            )
        _require_model_equal(
            "reserved cash",
            self.reserved_cash,
            expected_reserved,
        )

        reserve_amount = _exact_fraction_of_idr(
            self.starting_capital_idr,
            self.portfolio_policy.minimum_cash_reserve_fraction,
        )
        if (
            self.settled_cash.status == "ESTIMABLE"
            and self.reserved_cash.status == "ESTIMABLE"
            and reserve_amount is not None
        ):
            expected_deployable = estimable_money(
                max(
                    int(self.settled_cash.value_idr)
                    - int(self.reserved_cash.value_idr)
                    - reserve_amount,
                    0,
                )
            )
        else:
            expected_deployable = not_estimable_money(
                "DEPLOYABLE_CASH_INPUT_NOT_ESTIMABLE"
            )
        _require_model_equal("deployable cash", self.deployable_cash, expected_deployable)

        expected_nav = _sum_estimable_money(
            (
                self.settled_cash,
                self.unsettled_cash_receivable,
                self.marked_positions_value,
            ),
            missing_reason="NAV_INPUT_NOT_ESTIMABLE",
        )
        _require_model_equal("NAV", self.nav, expected_nav)
        _require_model_equal("gross exposure", self.gross_exposure, expected_marked)
        _require_model_equal("net exposure", self.net_exposure, expected_marked)

        if self.open_risk.status == "ESTIMABLE":
            expected_heat = estimable_ratio(
                quantize_ratio(
                    int(self.open_risk.value_idr),
                    self.starting_capital_idr,
                )
            )
        else:
            expected_heat = not_estimable_ratio("OPEN_RISK_NOT_ESTIMABLE")
        _require_model_equal("portfolio heat", self.portfolio_heat, expected_heat)

        if self.realized_pnl_today.status == "ESTIMABLE":
            realized_loss_idr = max(-int(self.realized_pnl_today.value_idr), 0)
            expected_realized_loss = estimable_ratio(
                quantize_ratio(
                    realized_loss_idr,
                    self.starting_capital_idr,
                )
            )
            expected_breaker: bool | None = (
                expected_realized_loss.value
                >= self.portfolio_policy.daily_loss_stop_fraction
            )
        else:
            expected_realized_loss = not_estimable_ratio(
                "REALIZED_PNL_NOT_ESTIMABLE"
            )
            expected_breaker = None
        _require_model_equal(
            "realized loss today",
            self.realized_loss_today,
            expected_realized_loss,
        )
        if self.circuit_breaker_active != expected_breaker:
            raise ValueError("daily circuit-breaker state mismatch")

        expected_nav_missing = not_estimable_money(_NOT_ESTIMABLE_NAV_REASON)
        expected_drawdown_missing = not_estimable_ratio(_NOT_ESTIMABLE_NAV_REASON)
        _require_model_equal("peak NAV", self.peak_nav, expected_nav_missing)
        _require_model_equal(
            "NAV drawdown",
            self.nav_drawdown,
            expected_drawdown_missing,
        )

        expected_status = (
            "NOT_ESTIMABLE"
            if self.nav.status == "NOT_ESTIMABLE"
            else "INSOLVENT"
            if self.nav.value_idr == 0
            else "ACTIVE"
        )
        if self.portfolio_status != expected_status:
            raise ValueError("portfolio status differs from exact NAV status")
        missing = _state_missing_fields(self)
        if self.missing_state_fields != missing:
            raise ValueError("missing_state_fields is not sorted and complete")
        expected_complete = not missing
        if self.portfolio_gate_inputs_complete != expected_complete:
            raise ValueError("portfolio gate completeness mismatch")
        expected_completeness = (
            "COMPLETE"
            if expected_complete
            else "NOT_ESTIMABLE"
            if self.nav.status == "NOT_ESTIMABLE"
            else "PARTIAL_CONTROL_OBSERVED"
        )
        if self.state_completeness != expected_completeness:
            raise ValueError("state completeness classification mismatch")


class PortfolioStateReference(_EvaluationOnlyPortfolioArtifact):
    contract_version: Literal["shadow-portfolio-state-reference-v1"] = (
        PORTFOLIO_STATE_REFERENCE_VERSION
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    portfolio_state_id: NonEmptyString
    state_contract_version: Literal["shadow-portfolio-state-v1"]
    state_canonical_sha256: Sha256
    state_raw_file_sha256: Sha256
    state_raw_byte_length: StrictPositiveInt
    state_relative_path: NonEmptyString
    portfolio_policy_canonical_sha256: Sha256
    portfolio_policy_raw_file_sha256: Sha256
    source_record_canonical_sha256: Sha256
    source_record_raw_file_sha256: Sha256
    state_sequence: Literal[0]
    portfolio_path_id: NonEmptyString
    previous_state_sha256: None
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("state-reference capture time must be timezone-aware")
        return value


class PortfolioLineageBundle(_EvaluationOnlyPortfolioArtifact):
    """Lineage-v2 composes, but never reinterprets, historical lineage-v1."""

    contract_version: Literal["shadow-lineage-bundle-v2"] = (
        PORTFOLIO_LINEAGE_BUNDLE_VERSION
    )
    protocol_id: NonEmptyString
    component_id: ComponentID
    manifest_sha256: Sha256
    base_lineage: LineageBundle
    base_lineage_sha256: Sha256
    portfolio_policy_id: NonEmptyString
    portfolio_policy_sha256: Sha256
    portfolio_source_record_id: NonEmptyString
    portfolio_source_record_sha256: Sha256
    portfolio_source_payload_sha256: Sha256
    portfolio_state_id: NonEmptyString
    portfolio_state_sha256: Sha256
    observation_id: NonEmptyString
    observation_sha256: Sha256
    lineage_valid: Literal[True] = True

    @model_validator(mode="after")
    def verify_hashes(self) -> PortfolioLineageBundle:
        if canonical_sha256(self.base_lineage) != self.base_lineage_sha256:
            raise ValueError("base lineage-v1 hash mismatch")
        return self


def quantize_ratio(numerator: int, denominator: int) -> float:
    """Compute a deterministic ratio from exact integers."""

    if type(numerator) is not int or type(denominator) is not int:
        raise TypeError("ratio inputs must be strict integers")
    if denominator == 0:
        raise ZeroDivisionError("ratio denominator cannot be zero")
    return quantize_ratio_decimal(Decimal(numerator) / Decimal(denominator))


def quantize_ratio_decimal(value: Decimal) -> float:
    if not value.is_finite():
        raise ValueError("ratio must be finite")
    quantized = value.quantize(_RATIO_QUANTUM, rounding=ROUND_HALF_EVEN)
    result = float(quantized)
    return 0.0 if result == 0.0 else result


def aggregate_bps_cost_idr(
    notional_idr: int,
    applicable_bps: Sequence[float],
) -> int:
    """Apply all bps once and round the non-negative cost against portfolio."""

    if type(notional_idr) is not int or notional_idr < 0:
        raise ValueError("notional_idr must be a strict non-negative integer")
    rates: list[Decimal] = []
    for value in applicable_bps:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("bps values must be numeric rates")
        rate = Decimal(str(value))
        if not rate.is_finite() or rate < 0:
            raise ValueError("bps values must be finite and non-negative")
        rates.append(rate)
    exact = Decimal(notional_idr) * sum(rates, Decimal(0)) / Decimal(10_000)
    return int(exact.to_integral_value(rounding=ROUND_CEILING))


def estimable_money(value_idr: int) -> EstimableMoney:
    return EstimableMoney(status="ESTIMABLE", value_idr=value_idr)


def not_estimable_money(reason: str) -> EstimableMoney:
    return EstimableMoney(
        status="NOT_ESTIMABLE",
        value_idr=None,
        reason_codes=(reason,),
    )


def estimable_ratio(value: float) -> EstimableRatio:
    return EstimableRatio(status="ESTIMABLE", value=value)


def not_estimable_ratio(reason: str) -> EstimableRatio:
    return EstimableRatio(
        status="NOT_ESTIMABLE",
        value=None,
        reason_codes=(reason,),
    )


def estimable_count(value: int) -> EstimableCount:
    return EstimableCount(status="ESTIMABLE", value=value)


def not_estimable_count(reason: str) -> EstimableCount:
    return EstimableCount(
        status="NOT_ESTIMABLE",
        value=None,
        reason_codes=(reason,),
    )


def canonical_portfolio_state_id(
    *,
    protocol_id: str,
    manifest_sha256: str,
    raw_capture_sha256: str,
    candidate_set_sha256: str,
    portfolio_policy_sha256: str,
    portfolio_source_record_sha256: str,
) -> str:
    payload = {
        "candidate_set_sha256": candidate_set_sha256,
        "manifest_sha256": manifest_sha256,
        "portfolio_policy_sha256": portfolio_policy_sha256,
        "portfolio_source_record_sha256": portfolio_source_record_sha256,
        "protocol_id": protocol_id,
        "raw_capture_sha256": raw_capture_sha256,
    }
    digest = _sha256(_canonical_mapping_bytes(payload))
    return f"PSTATE-{digest[:32]}"


def manifest_portfolio_profile(
    manifest: ShadowProtocolManifest,
) -> str | None:
    trusted = _revalidate(ShadowProtocolManifest, manifest)
    matches = tuple(
        item for item in trusted.thresholds if item.name == "portfolio_binding_profile"
    )
    if not matches:
        threshold_names = {item.name for item in trusted.thresholds}
        has_reserved_config = any(
            item.path == PORTFOLIO_POLICY_CONFIG_PATH
            for item in (
                *trusted.control_content_hashes,
                *trusted.challenger_content_hashes,
            )
        )
        if threshold_names & PORTFOLIO_MANIFEST_PARAMETER_NAMES or has_reserved_config:
            raise ShadowContractError(
                "portfolio markers require portfolio_binding_profile"
            )
        return None
    if len(matches) != 1 or type(matches[0].value) is not str:
        raise ShadowContractError("portfolio binding profile is malformed")
    if matches[0].value != PORTFOLIO_BINDING_PROFILE:
        raise ShadowContractError("unsupported portfolio binding profile")
    return PORTFOLIO_BINDING_PROFILE


def portfolio_manifest_parameters(
    policy: FrozenPortfolioPolicy,
) -> tuple[FrozenParameter, ...]:
    """Return the exact scalar profile required in manifest v2."""

    trusted = _revalidate(FrozenPortfolioPolicy, policy)
    specs: tuple[tuple[str, object, str | None, str], ...] = (
        (
            "portfolio_binding_profile",
            PORTFOLIO_BINDING_PROFILE,
            None,
            "RS-P2-014 owner-approved portfolio binding",
        ),
        (
            "portfolio_policy_contract_version",
            PORTFOLIO_POLICY_VERSION,
            None,
            "RS-P2-014 contract",
        ),
        (
            "phase2_capability_status",
            trusted.phase2_capability_status,
            None,
            "RS-P2-014 substrate is not A1-eligible",
        ),
        (
            "starting_capital_idr",
            trusted.starting_capital_idr,
            "IDR",
            "owner decision V2",
        ),
        (
            "fixed_notional_idr",
            trusted.fixed_notional_idr,
            "IDR",
            "owner decision V2",
        ),
        (
            "minimum_adt_idr",
            trusted.minimum_adt_idr,
            "IDR",
            "owner decision V2",
        ),
        (
            "max_participation_fraction",
            trusted.max_participation_fraction,
            "fraction_of_ADTV20",
            "owner decision N1",
        ),
        (
            "participation_evidence_class",
            trusted.participation_evidence_class,
            None,
            "owner decision N1",
        ),
        (
            "target_deployment_fraction",
            trusted.target_deployment_fraction,
            "fraction_of_starting_capital",
            "owner decision N2",
        ),
        (
            "effective_fixed_notional_max_deployment_fraction",
            trusted.effective_fixed_notional_max_deployment_fraction,
            "fraction_of_starting_capital",
            "owner decision N2",
        ),
        (
            "minimum_cash_reserve_fraction",
            trusted.minimum_cash_reserve_fraction,
            "fraction_of_starting_capital",
            "owner decision V2",
        ),
        (
            "max_gross_exposure_fraction",
            trusted.max_gross_exposure_fraction,
            "fraction_of_NAV",
            "owner decision V2",
        ),
        (
            "base_max_concurrent_positions",
            trusted.base_max_concurrent_positions,
            "positions",
            "owner decision V2",
        ),
        (
            "bull_max_positions",
            trusted.bull_max_positions,
            "positions",
            "owner decision V2",
        ),
        (
            "sideways_max_positions",
            trusted.sideways_max_positions,
            "positions",
            "owner decision V2",
        ),
        (
            "bear_stress_max_positions",
            trusted.bear_stress_max_positions,
            "positions",
            "owner decision V2",
        ),
        (
            "unknown_max_positions",
            trusted.unknown_max_positions,
            "positions",
            "owner decision V2",
        ),
        (
            "total_loss_budget_fraction",
            trusted.total_loss_budget_fraction,
            "fraction_of_starting_capital",
            "owner decision V2",
        ),
        (
            "max_portfolio_heat_fraction",
            trusted.max_portfolio_heat_fraction,
            "fraction_of_starting_capital",
            "owner decision V2",
        ),
        (
            "daily_loss_stop_fraction",
            trusted.daily_loss_stop_fraction,
            "fraction_of_starting_capital",
            "owner decision V2",
        ),
        (
            "max_sector_exposure_fraction",
            None,
            "fraction_of_NAV",
            "owner decision V2: NOT_ESTIMABLE",
        ),
        (
            "max_drawdown_stop_fraction",
            None,
            "fraction_of_NAV",
            "owner decision N3: NOT_ESTIMABLE",
        ),
        (
            "sector_max_names",
            trusted.sector_max_names,
            "names",
            "owner decision V2",
        ),
        (
            "cluster_max_names",
            trusted.cluster_max_names,
            "names",
            "owner decision V2",
        ),
        (
            "lot_size_shares",
            trusted.lot_size_shares,
            "shares",
            "owner decision V2",
        ),
        (
            "settlement_lag_sessions",
            trusted.settlement_lag_sessions,
            "IDX_trading_sessions",
            "owner decision V2",
        ),
        (
            "ratio_quantization_decimal_places",
            trusted.ratio_quantization_decimal_places,
            "decimal_places",
            "owner-approved hybrid arithmetic",
        ),
        (
            "cost_application_rounding_rule",
            trusted.cost_application_rounding_rule,
            None,
            "owner-approved hybrid arithmetic",
        ),
    )
    parameter_names = tuple(name for name, _, _, _ in specs)
    if (
        len(parameter_names) != len(set(parameter_names))
        or frozenset(parameter_names) != PORTFOLIO_MANIFEST_PARAMETER_NAMES
    ):
        raise ShadowContractError(
            "portfolio manifest parameter registry differs from profile"
        )
    return tuple(
        FrozenParameter(
            name=name,
            value=value,  # type: ignore[arg-type]
            unit=unit,
            source=source,
        )
        for name, value, unit, source in specs
    )


def verify_portfolio_manifest_binding(
    manifest: ShadowProtocolManifest,
    policy: FrozenPortfolioPolicy,
) -> FrozenPortfolioPolicy:
    """Fail closed unless manifest v2 binds the exact policy on both sides."""

    trusted_manifest = _revalidate(ShadowProtocolManifest, manifest)
    trusted_policy = _revalidate(FrozenPortfolioPolicy, policy)
    if manifest_portfolio_profile(trusted_manifest) != PORTFOLIO_BINDING_PROFILE:
        raise ShadowContractError("manifest does not declare portfolio-binding-v1")
    policy_hash = _required_hash(trusted_policy)
    control_matches = _matching_config_hashes(
        trusted_manifest.control_content_hashes,
        policy_hash,
    )
    challenger_matches = _matching_config_hashes(
        trusted_manifest.challenger_content_hashes,
        policy_hash,
    )
    if len(control_matches) != 1 or len(challenger_matches) != 1:
        raise ShadowContractError(
            "portfolio policy CONFIG hash must appear exactly once on both sides"
        )
    if (
        control_matches[0].path,
        challenger_matches[0].path,
    ) != (
        PORTFOLIO_POLICY_CONFIG_PATH,
        PORTFOLIO_POLICY_CONFIG_PATH,
    ):
        raise ShadowContractError(
            "portfolio policy CONFIG must use the reserved canonical path"
        )

    actual_parameters = {item.name: item for item in trusted_manifest.thresholds}
    for expected in portfolio_manifest_parameters(trusted_policy):
        actual = actual_parameters.get(expected.name)
        if actual is None:
            raise ShadowContractError(
                f"manifest is missing portfolio parameter {expected.name}"
            )
        if type(actual.value) is not type(expected.value):
            raise ShadowContractError(
                f"portfolio parameter {expected.name} has the wrong exact type"
            )
        if actual.value != expected.value or actual.unit != expected.unit:
            raise ShadowContractError(
                f"portfolio parameter {expected.name} differs from policy"
            )

    if trusted_manifest.costs.lot_size != trusted_policy.lot_size_shares:
        raise ShadowContractError("manifest lot size differs from portfolio policy")
    if (
        trusted_manifest.costs.price_rounding_rule
        != trusted_policy.price_rounding_rule
    ):
        raise ShadowContractError(
            "manifest price-rounding rule differs from portfolio policy"
        )
    if _required_hash(trusted_manifest.costs) != trusted_policy.cost_assumptions_sha256:
        raise ShadowContractError("manifest costs differ from portfolio policy")
    if (
        trusted_manifest.trading_calendar_sha256
        != trusted_policy.trading_calendar_sha256
    ):
        raise ShadowContractError("manifest calendar differs from portfolio policy")
    if (
        trusted_manifest.corporate_action_policy_sha256
        != trusted_policy.corporate_action_policy_sha256
    ):
        raise ShadowContractError(
            "manifest corporate-action policy differs from portfolio policy"
        )
    if (
        trusted_manifest.methodology_document_sha256
        != trusted_policy.methodology_document_sha256
    ):
        raise ShadowContractError(
            "manifest methodology differs from portfolio policy"
        )
    required_sources = (
        (
            trusted_policy.portfolio_source_id,
            trusted_policy.portfolio_source_definition_sha256,
        ),
        (
            trusted_policy.liquidity_source_id,
            trusted_policy.liquidity_source_definition_sha256,
        ),
        (
            trusted_policy.mark_price_source_id,
            trusted_policy.mark_price_source_definition_sha256,
        ),
    )
    for source_id, expected_hash in required_sources:
        matches = tuple(
            item for item in trusted_manifest.sources if item.source_id == source_id
        )
        if len(matches) != 1 or _required_hash(matches[0]) != expected_hash:
            raise ShadowContractError(
                f"manifest source binding is invalid for {source_id}"
            )
        if (
            source_id == trusted_policy.liquidity_source_id
            and matches[0].expiry_rule
            != trusted_policy.liquidity_expiry_rule
        ):
            raise ShadowContractError(
                "liquidity expiry rule differs from source definition"
            )
    return trusted_policy


def verify_portfolio_a1_capability(
    manifest: ShadowProtocolManifest,
    policy: FrozenPortfolioPolicy,
) -> None:
    """Block A1 until the remaining Phase-2 substrate is explicitly complete."""

    trusted = verify_portfolio_manifest_binding(manifest, policy)
    if trusted.phase2_capability_status != "PHASE_2_COMPLETE_A1_ELIGIBLE":
        raise ShadowContractError(
            "portfolio-binding-v1 is RS-P2-014 substrate only; "
            "RS-P2-015 through RS-P2-025 capability evidence is incomplete"
        )


def build_frozen_control_portfolio_state(
    *,
    manifest: ShadowProtocolManifest,
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
    policy: FrozenPortfolioPolicy,
    source_record: PortfolioStateSourceRecord,
    state_as_of: datetime,
    captured_at: datetime,
) -> PortfolioState:
    """Build one read-only state shared by every candidate in a batch."""

    manifest = _revalidate(ShadowProtocolManifest, manifest)
    raw_capture = _revalidate(RawCandidateSetCapture, raw_capture)
    candidate_set = _revalidate(CandidateSetManifest, candidate_set)
    policy = verify_portfolio_manifest_binding(manifest, policy)
    source_record = _revalidate(PortfolioStateSourceRecord, source_record)
    _verify_candidate_set_pair(raw_capture, candidate_set)
    _verify_manifest_candidate_binding(manifest, raw_capture, candidate_set)
    manifest_hash = _required_hash(manifest)
    raw_capture_hash = _required_hash(raw_capture)
    candidate_set_hash = _required_hash(candidate_set)
    policy_hash = _required_hash(policy)
    source_record_hash = _required_hash(source_record)
    if (
        source_record.protocol_id,
        source_record.component_id,
        source_record.manifest_revision,
        source_record.manifest_sha256,
        source_record.source_id,
        source_record.source_definition_sha256,
    ) != (
        manifest.protocol_id,
        manifest.component_id,
        manifest.manifest_revision,
        manifest_hash,
        policy.portfolio_source_id,
        policy.portfolio_source_definition_sha256,
    ):
        raise ShadowContractError("portfolio source record binding mismatch")
    if source_record.source_as_of > state_as_of:
        raise ShadowContractError("portfolio source is newer than state_as_of")
    if source_record.captured_at > captured_at:
        raise ShadowContractError(
            "portfolio source capture follows state finalization"
        )
    if state_as_of > raw_capture.signal_at:
        raise ShadowContractError("portfolio state_as_of follows signal time")
    if source_record.source_expires_at is not None and (
        source_record.source_expires_at <= raw_capture.signal_at
    ):
        raise ShadowContractError("portfolio source expired at signal time")
    if not (
        raw_capture.captured_at
        <= candidate_set.captured_at
        <= captured_at
    ):
        raise ShadowContractError("portfolio-state build chronology is not causal")
    payload = source_record.payload
    positions = tuple(
        _build_position(item, policy)
        for item in payload.positions
    )
    commitments = tuple(
        _build_commitment(item)
        for item in payload.pending_commitments
    )
    marked_positions = _sum_estimable_money(
        tuple(item.market_value for item in positions),
        missing_reason="POSITION_VALUE_NOT_ESTIMABLE",
        collection_status=payload.positions_status,
    )
    open_risk = _sum_estimable_money(
        tuple(item.risk_to_stop for item in positions),
        missing_reason="POSITION_RISK_NOT_ESTIMABLE",
        collection_status=payload.positions_status,
    )
    reserve_amount = _exact_fraction_of_idr(
        policy.starting_capital_idr,
        policy.minimum_cash_reserve_fraction,
    )
    if (
        payload.settled_cash.status == "ESTIMABLE"
        and payload.reserved_cash.status == "ESTIMABLE"
        and reserve_amount is not None
    ):
        deployable_cash = estimable_money(
            max(
                int(payload.settled_cash.value_idr)
                - int(payload.reserved_cash.value_idr)
                - reserve_amount,
                0,
            )
        )
    else:
        deployable_cash = not_estimable_money(
            "DEPLOYABLE_CASH_INPUT_NOT_ESTIMABLE"
        )
    nav = _sum_estimable_money(
        (
            payload.settled_cash,
            payload.unsettled_cash_receivable,
            marked_positions,
        ),
        missing_reason="NAV_INPUT_NOT_ESTIMABLE",
    )
    if open_risk.status == "ESTIMABLE":
        portfolio_heat = estimable_ratio(
            quantize_ratio(
                int(open_risk.value_idr),
                policy.starting_capital_idr,
            )
        )
    else:
        portfolio_heat = not_estimable_ratio("OPEN_RISK_NOT_ESTIMABLE")
    if payload.realized_pnl_today.status == "ESTIMABLE":
        realized_loss_today = estimable_ratio(
            quantize_ratio(
                max(-int(payload.realized_pnl_today.value_idr), 0),
                policy.starting_capital_idr,
            )
        )
        circuit_breaker_active: bool | None = (
            realized_loss_today.value >= policy.daily_loss_stop_fraction
        )
    else:
        realized_loss_today = not_estimable_ratio(
            "REALIZED_PNL_NOT_ESTIMABLE"
        )
        circuit_breaker_active = None

    portfolio_status: Literal["ACTIVE", "INSOLVENT", "NOT_ESTIMABLE"]
    if nav.status == "NOT_ESTIMABLE":
        portfolio_status = "NOT_ESTIMABLE"
    elif nav.value_idr == 0:
        portfolio_status = "INSOLVENT"
    else:
        portfolio_status = "ACTIVE"
    state_id = canonical_portfolio_state_id(
        protocol_id=manifest.protocol_id,
        manifest_sha256=manifest_hash,
        raw_capture_sha256=raw_capture_hash,
        candidate_set_sha256=candidate_set_hash,
        portfolio_policy_sha256=policy_hash,
        portfolio_source_record_sha256=source_record_hash,
    )
    provisional = PortfolioState.model_construct(
        portfolio_state_id=state_id,
        portfolio_path_id=f"CONTROL-{raw_capture.opportunity_set_id}",
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_revision=manifest.manifest_revision,
        manifest_sha256=manifest_hash,
        baseline_manifest_id=manifest.baseline_manifest_id,
        baseline_manifest_sha256=manifest.baseline_manifest_sha256,
        opportunity_set_id=raw_capture.opportunity_set_id,
        opportunity_set_sha256=raw_capture.opportunity_set_sha256,
        raw_capture_id=raw_capture.raw_capture_id,
        raw_capture_sha256=raw_capture_hash,
        raw_capture_captured_at=raw_capture.captured_at,
        candidate_set_id=candidate_set.candidate_set_id,
        candidate_set_sha256=candidate_set_hash,
        candidate_set_captured_at=candidate_set.captured_at,
        signal_at=raw_capture.signal_at,
        as_of_date=raw_capture.as_of_date,
        state_session_date=raw_capture.as_of_date,
        state_as_of=state_as_of,
        captured_at=captured_at,
        trading_calendar_id=raw_capture.trading_calendar_id,
        trading_calendar_sha256=raw_capture.trading_calendar_sha256,
        portfolio_policy_id=policy.policy_id,
        portfolio_policy_sha256=policy_hash,
        portfolio_policy=policy,
        portfolio_source_id=source_record.source_id,
        portfolio_source_definition_sha256=(
            source_record.source_definition_sha256
        ),
        portfolio_source_record_id=source_record.source_record_id,
        portfolio_source_record_sha256=source_record_hash,
        portfolio_source_payload_sha256=source_record.payload_sha256,
        source_as_of=source_record.source_as_of,
        source_expires_at=source_record.source_expires_at,
        starting_capital_idr=policy.starting_capital_idr,
        settled_cash=payload.settled_cash,
        unsettled_cash_receivable=payload.unsettled_cash_receivable,
        reserved_cash=payload.reserved_cash,
        deployable_cash=deployable_cash,
        marked_positions_value=marked_positions,
        nav=nav,
        peak_nav=not_estimable_money(_NOT_ESTIMABLE_NAV_REASON),
        nav_drawdown=not_estimable_ratio(_NOT_ESTIMABLE_NAV_REASON),
        gross_exposure=marked_positions,
        net_exposure=marked_positions,
        positions_status=payload.positions_status,
        positions=positions,
        pending_commitments_status=payload.pending_commitments_status,
        pending_commitments=commitments,
        open_positions_count=(
            estimable_count(len(positions))
            if payload.positions_status == "ESTIMABLE"
            else not_estimable_count("POSITIONS_NOT_ESTIMABLE")
        ),
        open_risk=open_risk,
        portfolio_heat=portfolio_heat,
        realized_pnl_today=payload.realized_pnl_today,
        realized_loss_today=realized_loss_today,
        control_30d_closed_trade_avg_pnl=(
            payload.control_30d_closed_trade_avg_pnl
        ),
        circuit_breaker_active=circuit_breaker_active,
        portfolio_status=portfolio_status,
    )
    provisional_dict = provisional.model_dump(mode="python")
    missing_fields = _state_missing_fields_from_dict(provisional_dict)
    provisional_dict.update(
        {
            "missing_state_fields": missing_fields,
            "portfolio_gate_inputs_complete": not missing_fields,
            "state_completeness": (
                "COMPLETE"
                if not missing_fields
                else "NOT_ESTIMABLE"
                if nav.status == "NOT_ESTIMABLE"
                else "PARTIAL_CONTROL_OBSERVED"
            ),
        }
    )
    try:
        return PortfolioState.model_validate(provisional_dict)
    except ValueError as exc:
        raise ShadowContractError("frozen portfolio state is invalid") from exc


def verify_portfolio_state_binding(
    *,
    manifest: ShadowProtocolManifest,
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
    policy: FrozenPortfolioPolicy,
    source_record: PortfolioStateSourceRecord,
    state: PortfolioState,
) -> PortfolioState:
    """Rebuild the immutable edges without rebuilding state values."""

    manifest = _revalidate(ShadowProtocolManifest, manifest)
    raw_capture = _revalidate(RawCandidateSetCapture, raw_capture)
    candidate_set = _revalidate(CandidateSetManifest, candidate_set)
    policy = verify_portfolio_manifest_binding(manifest, policy)
    source_record = _revalidate(PortfolioStateSourceRecord, source_record)
    state = _revalidate(PortfolioState, state)
    _verify_candidate_set_pair(raw_capture, candidate_set)
    _verify_manifest_candidate_binding(manifest, raw_capture, candidate_set)
    actual = (
        state.protocol_id,
        state.component_id,
        state.manifest_revision,
        state.manifest_sha256,
        state.baseline_manifest_id,
        state.baseline_manifest_sha256,
        state.raw_capture_id,
        state.raw_capture_sha256,
        state.raw_capture_captured_at,
        state.candidate_set_id,
        state.candidate_set_sha256,
        state.candidate_set_captured_at,
        state.opportunity_set_id,
        state.opportunity_set_sha256,
        state.signal_at,
        state.as_of_date,
        state.portfolio_policy_sha256,
        state.portfolio_policy_id,
        state.portfolio_source_id,
        state.portfolio_source_definition_sha256,
        state.portfolio_source_record_id,
        state.portfolio_source_record_sha256,
        state.portfolio_source_payload_sha256,
        state.source_as_of,
        state.source_expires_at,
        state.trading_calendar_id,
        state.trading_calendar_sha256,
    )
    expected = (
        manifest.protocol_id,
        manifest.component_id,
        manifest.manifest_revision,
        _required_hash(manifest),
        manifest.baseline_manifest_id,
        manifest.baseline_manifest_sha256,
        raw_capture.raw_capture_id,
        _required_hash(raw_capture),
        raw_capture.captured_at,
        candidate_set.candidate_set_id,
        _required_hash(candidate_set),
        candidate_set.captured_at,
        raw_capture.opportunity_set_id,
        raw_capture.opportunity_set_sha256,
        raw_capture.signal_at,
        raw_capture.as_of_date,
        _required_hash(policy),
        policy.policy_id,
        source_record.source_id,
        source_record.source_definition_sha256,
        source_record.source_record_id,
        _required_hash(source_record),
        source_record.payload_sha256,
        source_record.source_as_of,
        source_record.source_expires_at,
        raw_capture.trading_calendar_id,
        raw_capture.trading_calendar_sha256,
    )
    if actual != expected:
        raise ShadowContractError("portfolio-state lineage binding mismatch")
    if (
        source_record.protocol_id,
        source_record.component_id,
        source_record.manifest_revision,
        source_record.manifest_sha256,
        source_record.source_id,
        source_record.source_definition_sha256,
    ) != (
        manifest.protocol_id,
        manifest.component_id,
        manifest.manifest_revision,
        _required_hash(manifest),
        policy.portfolio_source_id,
        policy.portfolio_source_definition_sha256,
    ):
        raise ShadowContractError("portfolio source record identity mismatch")
    if source_record.captured_at > state.captured_at:
        raise ShadowContractError(
            "portfolio source capture follows state finalization"
        )
    _verify_state_source_derivation(state, source_record, policy)
    return state


class PortfolioArtifactStore:
    """Exclusive-create, content-addressed storage for portfolio evidence."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def persist_policy(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        policy = load_portfolio_policy_v1(raw_file_bytes)
        verify_portfolio_manifest_binding(manifest, policy)
        manifest_hash = _required_hash(manifest)
        canonical_hash = _required_hash(policy)
        raw_hash = _sha256(raw_file_bytes)
        path = self._record_path(
            manifest.protocol_id,
            manifest_hash,
            "portfolio_policies",
            canonical_hash,
            raw_hash,
        )
        return self._exclusive_create(path, raw_file_bytes)

    def load_policy_for_manifest(
        self,
        manifest: ShadowProtocolManifest,
    ) -> FrozenPortfolioPolicy:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        if manifest_portfolio_profile(manifest) != PORTFOLIO_BINDING_PROFILE:
            raise ShadowContractError("manifest has no portfolio binding profile")
        control_configs = tuple(
            item for item in manifest.control_content_hashes if item.role == "CONFIG"
        )
        challenger_configs = tuple(
            item
            for item in manifest.challenger_content_hashes
            if item.role == "CONFIG"
        )
        shared = tuple(
            left
            for left in control_configs
            if any(
                (right.path, right.sha256) == (left.path, left.sha256)
                for right in challenger_configs
            )
        )
        candidates: list[FrozenPortfolioPolicy] = []
        for item in shared:
            path = self._record_path(
                manifest.protocol_id,
                _required_hash(manifest),
                "portfolio_policies",
                item.sha256,
                item.sha256,
            )
            if not path.is_file():
                continue
            policy = load_portfolio_policy_v1(self._read_exact(path, "policy"))
            if canonical_sha256(policy) == item.sha256:
                candidates.append(policy)
        if len(candidates) != 1:
            raise ShadowContractError(
                "exactly one persisted shared portfolio policy is required"
            )
        return verify_portfolio_manifest_binding(manifest, candidates[0])

    def persist_source_record(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        policy = self.load_policy_for_manifest(manifest)
        record = load_portfolio_state_source_v1(raw_file_bytes)
        canonical_hash = _required_hash(record)
        raw_hash = _sha256(raw_file_bytes)
        if (
            record.protocol_id,
            record.component_id,
            record.manifest_revision,
            record.manifest_sha256,
            record.source_id,
            record.source_definition_sha256,
        ) != (
            manifest.protocol_id,
            manifest.component_id,
            manifest.manifest_revision,
            _required_hash(manifest),
            policy.portfolio_source_id,
            policy.portfolio_source_definition_sha256,
        ):
            raise ShadowContractError("portfolio source record binding mismatch")
        path = self._record_path(
            manifest.protocol_id,
            _required_hash(manifest),
            "portfolio_state_sources",
            canonical_hash,
            raw_hash,
        )
        self._exclusive_create(path, raw_file_bytes)
        reference = PortfolioStateSourceReference(
            protocol_id=manifest.protocol_id,
            component_id=manifest.component_id,
            manifest_sha256=_required_hash(manifest),
            source_record_id=record.source_record_id,
            source_record_contract_version=record.contract_version,
            source_record_canonical_sha256=canonical_hash,
            source_record_raw_file_sha256=raw_hash,
            source_record_raw_byte_length=len(raw_file_bytes),
            source_record_relative_path=self._relative(path),
            source_definition_sha256=record.source_definition_sha256,
            payload_sha256=record.payload_sha256,
        )
        self._exclusive_create(
            self._reference_path(
                manifest.protocol_id,
                _required_hash(manifest),
                "portfolio_state_source_refs",
                record.source_record_id,
            ),
            canonical_json_bytes(reference),
        )
        return path

    def load_source_record(
        self,
        manifest: ShadowProtocolManifest,
        source_record_id: str,
    ) -> tuple[PortfolioStateSourceRecord, PortfolioStateSourceReference]:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        reference_path = self._reference_path(
            manifest.protocol_id,
            _required_hash(manifest),
            "portfolio_state_source_refs",
            source_record_id,
        )
        reference = _load_canonical_model(
            PortfolioStateSourceReference,
            self._read_exact(reference_path, "portfolio source reference"),
            "portfolio source reference",
        )
        record_path = (self.root / reference.source_record_relative_path).resolve()
        self._require_within_root(record_path)
        expected_record_path = self._record_path(
            manifest.protocol_id,
            _required_hash(manifest),
            "portfolio_state_sources",
            reference.source_record_canonical_sha256,
            reference.source_record_raw_file_sha256,
        )
        if record_path != expected_record_path:
            raise ShadowContractError(
                "portfolio source reference escaped its exact namespace"
            )
        if reference.source_record_relative_path != self._relative(
            expected_record_path
        ):
            raise ShadowContractError(
                "portfolio source reference path is not canonical"
            )
        raw = self._read_exact(record_path, "portfolio source record")
        if (
            len(raw) != reference.source_record_raw_byte_length
            or _sha256(raw) != reference.source_record_raw_file_sha256
        ):
            raise ShadowContractError("portfolio source raw identity mismatch")
        record = load_portfolio_state_source_v1(raw)
        if _required_hash(record) != reference.source_record_canonical_sha256:
            raise ShadowContractError("portfolio source canonical hash mismatch")
        actual = (
            reference.protocol_id,
            reference.component_id,
            reference.manifest_sha256,
            reference.source_record_id,
            reference.source_record_contract_version,
            reference.source_definition_sha256,
            reference.payload_sha256,
            record.protocol_id,
            record.component_id,
            record.manifest_revision,
            record.manifest_sha256,
            record.source_record_id,
            record.contract_version,
            record.source_definition_sha256,
            record.payload_sha256,
        )
        expected = (
            manifest.protocol_id,
            manifest.component_id,
            _required_hash(manifest),
            source_record_id,
            record.contract_version,
            record.source_definition_sha256,
            record.payload_sha256,
            manifest.protocol_id,
            manifest.component_id,
            manifest.manifest_revision,
            _required_hash(manifest),
            source_record_id,
            record.contract_version,
            record.source_definition_sha256,
            record.payload_sha256,
        )
        if actual != expected:
            raise ShadowContractError(
                "portfolio source reference identity mismatch"
            )
        return record, reference

    def persist_state(
        self,
        manifest: ShadowProtocolManifest,
        raw_file_bytes: bytes,
    ) -> Path:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        policy = self.load_policy_for_manifest(manifest)
        state = load_portfolio_state_v1(raw_file_bytes)
        source_record, source_reference = self.load_source_record(
            manifest,
            state.portfolio_source_record_id,
        )
        candidate_set = CandidateSetStore(self.root).load(
            state.candidate_set_id,
            protocol_id=manifest.protocol_id,
            manifest_sha256=_required_hash(manifest),
        )
        raw_capture = CandidateSetStore(self.root).load_raw(
            state.raw_capture_id,
            protocol_id=manifest.protocol_id,
            manifest_sha256=_required_hash(manifest),
        )
        verify_portfolio_state_binding(
            manifest=manifest,
            raw_capture=raw_capture,
            candidate_set=candidate_set,
            policy=policy,
            source_record=source_record,
            state=state,
        )
        if (
            state.protocol_id,
            state.component_id,
            state.manifest_revision,
            state.manifest_sha256,
            state.baseline_manifest_id,
            state.baseline_manifest_sha256,
            state.portfolio_policy_sha256,
            state.portfolio_source_record_sha256,
            state.portfolio_source_payload_sha256,
        ) != (
            manifest.protocol_id,
            manifest.component_id,
            manifest.manifest_revision,
            _required_hash(manifest),
            manifest.baseline_manifest_id,
            manifest.baseline_manifest_sha256,
            _required_hash(policy),
            _required_hash(source_record),
            source_record.payload_sha256,
        ):
            raise ShadowContractError("portfolio state cannot bind stored evidence")
        canonical_hash = _required_hash(state)
        raw_hash = _sha256(raw_file_bytes)
        path = self._record_path(
            manifest.protocol_id,
            _required_hash(manifest),
            "portfolio_states",
            canonical_hash,
            raw_hash,
        )
        self._exclusive_create(path, raw_file_bytes)
        policy_raw_hash = _required_hash(policy)
        reference = PortfolioStateReference(
            protocol_id=manifest.protocol_id,
            component_id=manifest.component_id,
            manifest_sha256=_required_hash(manifest),
            portfolio_state_id=state.portfolio_state_id,
            state_contract_version=state.contract_version,
            state_canonical_sha256=canonical_hash,
            state_raw_file_sha256=raw_hash,
            state_raw_byte_length=len(raw_file_bytes),
            state_relative_path=self._relative(path),
            portfolio_policy_canonical_sha256=_required_hash(policy),
            portfolio_policy_raw_file_sha256=policy_raw_hash,
            source_record_canonical_sha256=_required_hash(source_record),
            source_record_raw_file_sha256=(
                source_reference.source_record_raw_file_sha256
            ),
            state_sequence=state.state_sequence,
            portfolio_path_id=state.portfolio_path_id,
            previous_state_sha256=state.previous_state_sha256,
            captured_at=state.captured_at,
        )
        self._exclusive_create(
            self._reference_path(
                manifest.protocol_id,
                _required_hash(manifest),
                "portfolio_state_refs",
                state.portfolio_state_id,
            ),
            canonical_json_bytes(reference),
        )
        return path

    def load_state_by_hash(
        self,
        manifest: ShadowProtocolManifest,
        state_sha256: str,
    ) -> tuple[PortfolioState, PortfolioStateReference]:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        state_dir = (
            self._protocol_root(manifest.protocol_id, _required_hash(manifest))
            / "portfolio_states"
            / _safe_segment(state_sha256, "portfolio state SHA-256")
        )
        path = state_dir / f"{_safe_segment(state_sha256, 'state raw SHA-256')}.json"
        state = load_portfolio_state_v1(
            self._read_exact(path, "portfolio state")
        )
        if _required_hash(state) != state_sha256:
            raise ShadowContractError("portfolio state canonical hash mismatch")
        reference_path = self._reference_path(
            manifest.protocol_id,
            _required_hash(manifest),
            "portfolio_state_refs",
            state.portfolio_state_id,
        )
        reference = _load_canonical_model(
            PortfolioStateReference,
            self._read_exact(reference_path, "portfolio state reference"),
            "portfolio state reference",
        )
        raw = self._read_exact(path, "portfolio state")
        expected_path = self._record_path(
            manifest.protocol_id,
            _required_hash(manifest),
            "portfolio_states",
            reference.state_canonical_sha256,
            reference.state_raw_file_sha256,
        )
        if (
            reference.state_canonical_sha256 != state_sha256
            or reference.state_raw_file_sha256 != _sha256(raw)
            or reference.state_raw_byte_length != len(raw)
            or reference.state_relative_path != self._relative(path)
            or path != expected_path
        ):
            raise ShadowContractError("portfolio state reference identity mismatch")
        policy = self.load_policy_for_manifest(manifest)
        source, source_reference = self.load_source_record(
            manifest,
            state.portfolio_source_record_id,
        )
        candidate_store = CandidateSetStore(self.root)
        candidate_set = candidate_store.load(
            state.candidate_set_id,
            protocol_id=manifest.protocol_id,
            manifest_sha256=_required_hash(manifest),
        )
        raw_capture = candidate_store.load_raw(
            state.raw_capture_id,
            protocol_id=manifest.protocol_id,
            manifest_sha256=_required_hash(manifest),
        )
        verify_portfolio_state_binding(
            manifest=manifest,
            raw_capture=raw_capture,
            candidate_set=candidate_set,
            policy=policy,
            source_record=source,
            state=state,
        )
        if (
            reference.protocol_id,
            reference.component_id,
            reference.manifest_sha256,
            reference.portfolio_state_id,
            reference.state_contract_version,
            reference.state_sequence,
            reference.portfolio_path_id,
            reference.previous_state_sha256,
            reference.captured_at,
            reference.portfolio_policy_canonical_sha256,
            reference.portfolio_policy_raw_file_sha256,
            reference.source_record_canonical_sha256,
            reference.source_record_raw_file_sha256,
        ) != (
            manifest.protocol_id,
            manifest.component_id,
            _required_hash(manifest),
            state.portfolio_state_id,
            state.contract_version,
            state.state_sequence,
            state.portfolio_path_id,
            state.previous_state_sha256,
            state.captured_at,
            _required_hash(policy),
            _required_hash(policy),
            _required_hash(source),
            source_reference.source_record_raw_file_sha256,
        ):
            raise ShadowContractError("portfolio state dependency reference mismatch")
        return state, reference

    def verify_observation_state(
        self,
        manifest: ShadowProtocolManifest,
        observation: ShadowObservation,
    ) -> PortfolioState:
        manifest = _revalidate(ShadowProtocolManifest, manifest)
        observation = _revalidate(ShadowObservation, observation)
        state, _ = self.load_state_by_hash(
            manifest,
            observation.portfolio_state_sha256,
        )
        if (
            state.protocol_id,
            state.component_id,
            state.manifest_sha256,
            state.candidate_set_id,
            state.candidate_set_sha256,
            state.opportunity_set_id,
            state.opportunity_set_sha256,
            state.signal_at,
        ) != (
            observation.protocol_id,
            observation.component_id,
            observation.manifest_sha256,
            observation.candidate_set_id,
            observation.candidate_set_sha256,
            observation.opportunity_set_id,
            observation.opportunity_set_sha256,
            observation.signal_at,
        ):
            raise ShadowContractError("observation differs from portfolio state")
        if state.captured_at > observation.captured_at:
            raise ShadowContractError("observation was captured before portfolio state")
        return state

    def _record_path(
        self,
        protocol_id: str,
        manifest_hash: str,
        namespace: str,
        canonical_hash: str,
        raw_hash: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, manifest_hash)
            / _safe_segment(namespace, "portfolio namespace")
            / _safe_segment(canonical_hash, "canonical SHA-256")
            / f"{_safe_segment(raw_hash, 'raw SHA-256')}.json"
        )

    def _reference_path(
        self,
        protocol_id: str,
        manifest_hash: str,
        namespace: str,
        artifact_id: str,
    ) -> Path:
        return (
            self._protocol_root(protocol_id, manifest_hash)
            / _safe_segment(namespace, "portfolio reference namespace")
            / f"{_safe_segment(artifact_id, 'portfolio reference ID')}.json"
        )

    def _protocol_root(self, protocol_id: str, manifest_hash: str) -> Path:
        return (
            self.root
            / "protocols"
            / _safe_segment(protocol_id, "protocol ID")
            / _safe_segment(manifest_hash, "manifest SHA-256")
        )

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        self._require_within_root(resolved)
        return resolved.relative_to(self.root).as_posix()

    def _require_within_root(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ShadowContractError("portfolio path escaped store root") from exc

    @staticmethod
    def _exclusive_create(path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ShadowContractError(
                    f"immutable portfolio artifact collision: {path}"
                ) from None
        return path

    @staticmethod
    def _read_exact(path: Path, label: str) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ShadowContractError(f"{label} is unavailable: {path}") from exc


def load_portfolio_policy_v1(raw_file_bytes: bytes) -> FrozenPortfolioPolicy:
    return _load_raw_model(
        FrozenPortfolioPolicy,
        raw_file_bytes,
        expected_version=PORTFOLIO_POLICY_VERSION,
        label="portfolio policy",
    )


def load_portfolio_state_source_v1(
    raw_file_bytes: bytes,
) -> PortfolioStateSourceRecord:
    return _load_raw_model(
        PortfolioStateSourceRecord,
        raw_file_bytes,
        expected_version=PORTFOLIO_STATE_SOURCE_VERSION,
        label="portfolio state source",
    )


def load_portfolio_state_v1(raw_file_bytes: bytes) -> PortfolioState:
    return _load_raw_model(
        PortfolioState,
        raw_file_bytes,
        expected_version=PORTFOLIO_STATE_VERSION,
        label="portfolio state",
    )


def build_portfolio_lineage_bundle(
    *,
    manifest: ShadowProtocolManifest,
    frozen_snapshot: FrozenSnapshot,
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
    candidate: CandidateEvent,
    observation: ShadowObservation,
    policy: FrozenPortfolioPolicy,
    source_record: PortfolioStateSourceRecord,
    state: PortfolioState,
    bar_series: BaseModel | None = None,
    outcome: ShadowOutcome | None = None,
) -> PortfolioLineageBundle:
    verify_portfolio_state_binding(
        manifest=manifest,
        raw_capture=raw_capture,
        candidate_set=candidate_set,
        policy=policy,
        source_record=source_record,
        state=state,
    )
    if observation.portfolio_state_sha256 != _required_hash(state):
        raise ShadowContractError("observation portfolio-state hash is unresolved")
    if state.captured_at > observation.captured_at:
        raise ShadowContractError("observation predates portfolio state")
    base = build_lineage_bundle(
        manifest,
        frozen_snapshot,
        raw_capture,
        candidate_set,
        candidate,
        observation,
        bar_series=bar_series,
        outcome=outcome,
    )
    return PortfolioLineageBundle(
        protocol_id=manifest.protocol_id,
        component_id=manifest.component_id,
        manifest_sha256=_required_hash(manifest),
        base_lineage=base,
        base_lineage_sha256=_required_hash(base),
        portfolio_policy_id=policy.policy_id,
        portfolio_policy_sha256=_required_hash(policy),
        portfolio_source_record_id=source_record.source_record_id,
        portfolio_source_record_sha256=_required_hash(source_record),
        portfolio_source_payload_sha256=source_record.payload_sha256,
        portfolio_state_id=state.portfolio_state_id,
        portfolio_state_sha256=_required_hash(state),
        observation_id=observation.observation_id,
        observation_sha256=_required_hash(observation),
    )


def verify_portfolio_lineage_bundle(
    bundle: PortfolioLineageBundle,
    **artifacts: object,
) -> PortfolioLineageBundle:
    trusted = _revalidate(PortfolioLineageBundle, bundle)
    rebuilt = build_portfolio_lineage_bundle(**artifacts)  # type: ignore[arg-type]
    if canonical_sha256(trusted) != canonical_sha256(rebuilt):
        raise ShadowContractError(
            "portfolio lineage differs from exact-artifact reconstruction"
        )
    return trusted


def _build_position(
    source: FrozenPortfolioSourcePosition,
    policy: FrozenPortfolioPolicy,
) -> FrozenPositionState:
    market_value_idr = source.current_quantity_shares * source.mark_price_idr
    risk = (
        not_estimable_money("PLANNED_STOP_NOT_ESTIMABLE")
        if source.planned_stop_price_idr is None
        else estimable_money(
            max(source.mark_price_idr - source.planned_stop_price_idr, 0)
            * source.current_quantity_shares
        )
    )
    return FrozenPositionState(
        **source.model_dump(mode="python"),
        market_value=estimable_money(market_value_idr),
        allocation_fraction=estimable_ratio(
            quantize_ratio(market_value_idr, policy.starting_capital_idr)
        ),
        risk_to_stop=risk,
    )


def _build_commitment(
    source: FrozenPortfolioSourceCommitment,
) -> FrozenPendingCommitment:
    return FrozenPendingCommitment(
        commitment_id=source.commitment_id,
        ticker=source.ticker,
        created_at=source.created_at,
        expires_at=source.expires_at,
        reserved_cash=estimable_money(source.reserved_cash_idr),
        potential_exposure=estimable_money(source.potential_exposure_idr),
        potential_risk=estimable_money(source.potential_risk_idr),
        source_decision_sha256=source.source_decision_sha256,
        status=source.status,
    )


def _state_missing_fields(state: PortfolioState) -> tuple[str, ...]:
    return _state_missing_fields_from_dict(state.model_dump(mode="python"))


def _state_missing_fields_from_dict(payload: dict[str, object]) -> tuple[str, ...]:
    names = (
        "settled_cash",
        "unsettled_cash_receivable",
        "reserved_cash",
        "deployable_cash",
        "marked_positions_value",
        "nav",
        "gross_exposure",
        "net_exposure",
        "open_positions_count",
        "open_risk",
        "portfolio_heat",
        "realized_pnl_today",
        "realized_loss_today",
        "control_30d_closed_trade_avg_pnl",
    )
    missing = [
        name
        for name in names
        if getattr(payload[name], "status", None) == "NOT_ESTIMABLE"
        or (
            isinstance(payload[name], dict)
            and payload[name].get("status") == "NOT_ESTIMABLE"
        )
    ]
    if payload.get("positions_status") == "NOT_ESTIMABLE":
        missing.append("positions")
    if payload.get("pending_commitments_status") == "NOT_ESTIMABLE":
        missing.append("pending_commitments")
    if payload.get("circuit_breaker_active") is None:
        missing.append("circuit_breaker_active")
    return tuple(sorted(set(missing)))


def _verify_estimability(
    status: EstimabilityStatus,
    value: object | None,
    reason_codes: tuple[str, ...],
) -> None:
    if reason_codes != tuple(sorted(set(reason_codes))):
        raise ValueError("estimability reason codes must be sorted and unique")
    if status == "ESTIMABLE":
        if value is None:
            raise ValueError("ESTIMABLE measurement requires a value")
        if reason_codes:
            raise ValueError("ESTIMABLE measurement cannot carry missing reasons")
    else:
        if value is not None:
            raise ValueError("NOT_ESTIMABLE measurement cannot carry a value")
        if not reason_codes:
            raise ValueError("NOT_ESTIMABLE measurement needs a reason")


def _require_nonnegative_money(label: str, money: EstimableMoney) -> None:
    if (
        money.status == "ESTIMABLE"
        and money.value_idr is not None
        and money.value_idr < 0
    ):
        raise ValueError(f"{label} cannot be negative")


def _verify_collection_status(
    status: EstimabilityStatus,
    values: Sequence[object],
    label: str,
) -> None:
    if status == "NOT_ESTIMABLE" and values:
        raise ValueError(f"non-estimable {label} cannot carry values")


def _sum_estimable_money(
    values: Sequence[EstimableMoney],
    *,
    missing_reason: str,
    collection_status: EstimabilityStatus = "ESTIMABLE",
) -> EstimableMoney:
    if collection_status == "NOT_ESTIMABLE" or any(
        item.status == "NOT_ESTIMABLE" for item in values
    ):
        return not_estimable_money(missing_reason)
    return estimable_money(sum(int(item.value_idr) for item in values))


def _exact_fraction_of_idr(amount_idr: int, fraction: float) -> int | None:
    exact = Decimal(amount_idr) * Decimal(str(fraction))
    integral = exact.to_integral_value()
    if exact != integral:
        return None
    return int(integral)


def _require_model_equal(label: str, actual: BaseModel, expected: BaseModel) -> None:
    if canonical_sha256(actual) != canonical_sha256(expected):
        raise ValueError(f"{label} mismatch")


def _verify_candidate_set_pair(
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
) -> None:
    if (
        raw_capture.protocol_id,
        raw_capture.component_id,
        raw_capture.manifest_sha256,
        raw_capture.raw_capture_id,
        _required_hash(raw_capture),
        raw_capture.opportunity_set_id,
        raw_capture.opportunity_set_sha256,
        raw_capture.as_of_date,
        raw_capture.candidate_source_id,
        raw_capture.candidate_source_definition_sha256,
        raw_capture.trading_calendar_id,
        raw_capture.trading_calendar_sha256,
        raw_capture.corporate_action_policy_sha256,
        raw_capture.raw_candidate_count,
        raw_capture.raw_candidate_set_sha256,
    ) != (
        candidate_set.protocol_id,
        candidate_set.component_id,
        candidate_set.manifest_sha256,
        candidate_set.raw_capture_id,
        candidate_set.raw_capture_sha256,
        candidate_set.opportunity_set_id,
        candidate_set.opportunity_set_sha256,
        candidate_set.as_of_date,
        candidate_set.candidate_source_id,
        candidate_set.candidate_source_definition_sha256,
        candidate_set.trading_calendar_id,
        candidate_set.trading_calendar_sha256,
        candidate_set.corporate_action_policy_sha256,
        candidate_set.raw_candidate_count,
        candidate_set.raw_candidate_set_sha256,
    ):
        raise ShadowContractError("candidate set differs from raw capture")
    event_ids = tuple(item.raw_event_id for item in raw_capture.candidates)
    if (
        candidate_set.control_view.input_event_ids != event_ids
        or candidate_set.challenger_view.input_event_ids != event_ids
    ):
        raise ShadowContractError("candidate views differ from raw input order")


def _verify_manifest_candidate_binding(
    manifest: ShadowProtocolManifest,
    raw_capture: RawCandidateSetCapture,
    candidate_set: CandidateSetManifest,
) -> None:
    manifest_hash = _required_hash(manifest)
    expected = (
        manifest.protocol_id,
        manifest.component_id,
        manifest_hash,
        manifest.trading_calendar_id,
        manifest.trading_calendar_sha256,
        manifest.corporate_action_policy_sha256,
    )
    raw_actual = (
        raw_capture.protocol_id,
        raw_capture.component_id,
        raw_capture.manifest_sha256,
        raw_capture.trading_calendar_id,
        raw_capture.trading_calendar_sha256,
        raw_capture.corporate_action_policy_sha256,
    )
    candidate_actual = (
        candidate_set.protocol_id,
        candidate_set.component_id,
        candidate_set.manifest_sha256,
        candidate_set.trading_calendar_id,
        candidate_set.trading_calendar_sha256,
        candidate_set.corporate_action_policy_sha256,
    )
    if raw_actual != expected or candidate_actual != expected:
        raise ShadowContractError(
            "candidate evidence differs from manifest identity"
        )


def _verify_state_source_derivation(
    state: PortfolioState,
    source_record: PortfolioStateSourceRecord,
    policy: FrozenPortfolioPolicy,
) -> None:
    payload = source_record.payload
    expected_positions = tuple(
        _build_position(item, policy) for item in payload.positions
    )
    expected_commitments = tuple(
        _build_commitment(item) for item in payload.pending_commitments
    )
    actual = (
        state.settled_cash,
        state.unsettled_cash_receivable,
        state.reserved_cash,
        state.realized_pnl_today,
        state.control_30d_closed_trade_avg_pnl,
        state.positions_status,
        state.positions,
        state.pending_commitments_status,
        state.pending_commitments,
    )
    expected = (
        payload.settled_cash,
        payload.unsettled_cash_receivable,
        payload.reserved_cash,
        payload.realized_pnl_today,
        payload.control_30d_closed_trade_avg_pnl,
        payload.positions_status,
        expected_positions,
        payload.pending_commitments_status,
        expected_commitments,
    )
    if actual != expected:
        raise ShadowContractError(
            "portfolio state values differ from exact source payload"
        )


def _matching_config_hashes(
    records: Sequence[ContentHash],
    policy_hash: str,
) -> tuple[ContentHash, ...]:
    return tuple(
        item
        for item in records
        if item.role == "CONFIG" and item.sha256 == policy_hash
    )


def _load_raw_model(
    model: type[BaseModel],
    raw_file_bytes: bytes,
    *,
    expected_version: str,
    label: str,
):
    payload = _strict_json_object(raw_file_bytes, label)
    actual_version = payload.get("contract_version")
    if actual_version != expected_version:
        raise ShadowContractError(
            f"{label} contract_version must be {expected_version}; "
            f"received {actual_version!r}"
        )
    try:
        trusted = model.model_validate(payload)
    except ValueError as exc:
        raise ShadowContractError(f"{label} failed strict validation") from exc
    if canonical_json_bytes(trusted) != raw_file_bytes:
        raise ShadowContractError(f"{label} raw bytes are not canonical JSON")
    return trusted


def _load_canonical_model(
    model: type[BaseModel],
    raw_file_bytes: bytes,
    label: str,
):
    payload = _strict_json_object(raw_file_bytes, label)
    try:
        trusted = model.model_validate(payload)
    except ValueError as exc:
        raise ShadowContractError(f"{label} failed strict validation") from exc
    if canonical_json_bytes(trusted) != raw_file_bytes:
        raise ShadowContractError(f"{label} is not canonical JSON")
    return trusted


def _strict_json_object(raw_file_bytes: bytes, label: str) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ShadowContractError(f"{label} has duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw_file_bytes.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except ShadowContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ShadowContractError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ShadowContractError(f"{label} JSON root must be an object")
    return payload


def _revalidate(model: type[BaseModel], value: BaseModel):
    try:
        return model.model_validate(value.model_dump(mode="python"))
    except ValueError as exc:
        raise ShadowContractError(
            f"{model.__name__} failed trust-boundary validation"
        ) from exc


def _required_hash(model: BaseModel) -> str:
    digest = canonical_sha256(model)
    if digest is None:
        raise ShadowContractError("canonical SHA-256 is unavailable")
    return digest


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


def _safe_segment(value: str, label: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value):
        raise ShadowContractError(f"unsafe {label}: {value!r}")
    return value


__all__ = [
    "APPROVED_DAILY_LOSS_STOP_FRACTION",
    "APPROVED_EFFECTIVE_FIXED_NOTIONAL_MAX_DEPLOYMENT_FRACTION",
    "APPROVED_FIXED_NOTIONAL_IDR",
    "APPROVED_MAX_GROSS_EXPOSURE_FRACTION",
    "APPROVED_MAX_PARTICIPATION_FRACTION",
    "APPROVED_MAX_PORTFOLIO_HEAT_FRACTION",
    "APPROVED_MINIMUM_ADTV_IDR",
    "APPROVED_MINIMUM_CASH_RESERVE_FRACTION",
    "APPROVED_RATIO_QUANTIZATION_DECIMAL_PLACES",
    "APPROVED_STARTING_CAPITAL_IDR",
    "APPROVED_TARGET_DEPLOYMENT_FRACTION",
    "APPROVED_TOTAL_LOSS_BUDGET_FRACTION",
    "EstimableCount",
    "EstimableMoney",
    "EstimableRatio",
    "FrozenPendingCommitment",
    "FrozenPortfolioPolicy",
    "FrozenPortfolioSourceCommitment",
    "FrozenPortfolioSourcePayload",
    "FrozenPortfolioSourcePosition",
    "FrozenPositionState",
    "PORTFOLIO_BINDING_PROFILE",
    "PORTFOLIO_CAPABILITY_STATUS",
    "PORTFOLIO_LINEAGE_BUNDLE_VERSION",
    "PORTFOLIO_POLICY_VERSION",
    "PORTFOLIO_STATE_REFERENCE_VERSION",
    "PORTFOLIO_STATE_SOURCE_REFERENCE_VERSION",
    "PORTFOLIO_STATE_SOURCE_VERSION",
    "PORTFOLIO_STATE_VERSION",
    "PortfolioArtifactStore",
    "PortfolioLineageBundle",
    "PortfolioState",
    "PortfolioStateReference",
    "PortfolioStateSourceRecord",
    "PortfolioStateSourceReference",
    "aggregate_bps_cost_idr",
    "build_frozen_control_portfolio_state",
    "build_portfolio_lineage_bundle",
    "canonical_portfolio_state_id",
    "estimable_count",
    "estimable_money",
    "estimable_ratio",
    "load_portfolio_policy_v1",
    "load_portfolio_state_source_v1",
    "load_portfolio_state_v1",
    "manifest_portfolio_profile",
    "not_estimable_count",
    "not_estimable_money",
    "not_estimable_ratio",
    "portfolio_manifest_parameters",
    "quantize_ratio",
    "quantize_ratio_decimal",
    "verify_portfolio_lineage_bundle",
    "verify_portfolio_a1_capability",
    "verify_portfolio_manifest_binding",
    "verify_portfolio_state_binding",
]
