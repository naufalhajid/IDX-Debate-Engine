"""Pydantic v2 schemas for the IDX forecasting layer."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ForecastStatus = Literal[
    "READY",
    "NOT_VALIDATED",
    "VALIDATION_FAILED",
    "MODEL_FAILED",
    "ZERO_WEIGHT",
    "UNAVAILABLE",
]

ProbabilitySource = Literal[
    "return_volatility_parametric",
    "unavailable",
]

ProbabilityEvent = Literal["terminal"]
ProbabilityBarrierSource = Literal[
    "cio_trade_levels",
    "default_horizon",
]
ForecastCaptureScope = Literal[
    "standard",
    "preflight_terminal_shadow",
]


class ModelVote(BaseModel):
    """Output from a single forecasting model."""

    model_name: str
    status: Literal[
        "active",
        "not_validated",
        "validation_failed",
        "unavailable",
        "experimental_unused",
    ] = "active"
    reason: str | None = None
    probability_source: ProbabilitySource = "unavailable"
    r_hat_net: float | None = None
    p_target: float | None = None
    p_stop: float | None = None
    volatility_forecast: float | None = None
    weight: float = 0.0
    validation_passed: bool = False
    ic: float | None = None
    brier_target: float | None = None
    rmse: float | None = None
    mae: float | None = None
    mape: float | None = None
    directional_accuracy: float | None = None


class ValidationSummary(BaseModel):
    """Walk-forward validation results for a model."""

    horizon_days: int
    n_observations: int = 0
    ic_mean: float | None = None
    ic_t_stat: float | None = None
    brier: float | None = None
    rmse: float | None = None
    mae: float | None = None
    mape: float | None = None
    directional_accuracy: float | None = None
    dsr: float | None = None
    bh_q_value_passed: bool = False
    status: Literal["production", "research_only", "failed"] = "failed"


class ForecastReport(BaseModel):
    """Full forecast output for a ticker."""

    ticker: str
    as_of: date
    forecast_as_of: date | None = Field(
        default=None,
        description="Effective market-session date for this forecast.",
    )
    horizon_days: int
    feature_as_of: date | None = Field(
        default=None,
        description="Latest observed feature date used for inference.",
    )
    training_end_date: date | None = Field(
        default=None,
        description="Latest labeled feature date used to fit directional return models.",
    )
    feature_snapshot_hash: str | None = Field(
        default=None,
        description="Hash of the fully materialized point-in-time feature input.",
    )
    execution_snapshot_id: str | None = Field(
        default=None,
        description="Identity of the frozen ticker snapshot used for inference.",
    )
    execution_snapshot_hash: str | None = Field(
        default=None,
        description="Content hash of the frozen ticker snapshot.",
    )
    ihsg_snapshot_id: str | None = Field(
        default=None,
        description="Identity of the frozen IHSG benchmark snapshot.",
    )
    ihsg_snapshot_hash: str | None = Field(
        default=None,
        description="Content hash of the frozen IHSG benchmark snapshot.",
    )
    ihsg_feature_as_of: date | None = Field(
        default=None,
        description="Latest IHSG session available to regime features.",
    )
    capture_scope: ForecastCaptureScope = "standard"
    live_authority: Literal[False] = False
    feature_close: float | None = Field(
        default=None,
        description="Close from the inference feature row used in probability math.",
    )
    forecast_status: ForecastStatus = "UNAVAILABLE"
    failure_reason: str | None = "legacy_status_missing"
    expected_return_net: float | None = None
    p_target: float | None = None
    p_stop: float | None = None
    volatility_forecast: float | None = None
    expected_value: float | None = None
    decision: Literal["BUY", "WATCH", "AVOID"] = "AVOID"
    probability_source: ProbabilitySource = "unavailable"
    probability_event: ProbabilityEvent | None = None
    probability_barrier_source: ProbabilityBarrierSource | None = None
    probability_reference_close: float | None = None
    target_barrier_return: float | None = None
    stop_barrier_return: float | None = None
    target_barrier_price: float | None = None
    stop_barrier_price: float | None = None
    shadow_decision: Literal["BUY", "WATCH", "AVOID"] | None = None
    shadow_buy_ev_floor: float | None = None
    shadow_evaluation_only: bool = False
    confidence: float | None = None
    model_votes: list[ModelVote] = Field(default_factory=list)
    validation_summary: ValidationSummary | None = None
    validation_by_model: dict[str, ValidationSummary] = Field(default_factory=dict)
    model_dispersion: float | None = None
    model_disagreement_penalty: float = 0.0
    risk_adjusted_expected_value: float | None = None
    data_quality_flags: list[str] = Field(default_factory=list)
    volatility_fallback: bool = False

    @model_validator(mode="after")
    def validate_status_reason(self) -> ForecastReport:
        """Keep readiness and failure diagnostics internally consistent."""
        if self.forecast_as_of is None:
            # Backward-compatible alias fill for historical report constructors.
            self.forecast_as_of = self.as_of
        if self.forecast_as_of != self.as_of:
            raise ValueError("as_of must equal forecast_as_of")
        if (
            self.feature_as_of is not None
            and self.feature_as_of != self.forecast_as_of
        ):
            raise ValueError("feature_as_of must equal forecast_as_of")
        if (
            self.ihsg_feature_as_of is not None
            and self.ihsg_feature_as_of != self.forecast_as_of
        ):
            raise ValueError("ihsg_feature_as_of must equal forecast_as_of")
        execution_provenance = (
            self.execution_snapshot_id,
            self.execution_snapshot_hash,
        )
        if any(value is not None for value in execution_provenance) and not all(
            value is not None for value in execution_provenance
        ):
            raise ValueError("execution snapshot provenance must be all-or-none")
        ihsg_provenance = (
            self.ihsg_snapshot_id,
            self.ihsg_snapshot_hash,
            self.ihsg_feature_as_of,
        )
        if any(value is not None for value in ihsg_provenance) and not all(
            value is not None for value in ihsg_provenance
        ):
            raise ValueError("IHSG snapshot provenance must be all-or-none")
        if self.capture_scope == "preflight_terminal_shadow" and (
            not all(value is not None for value in execution_provenance)
            or not all(value is not None for value in ihsg_provenance)
        ):
            raise ValueError(
                "preflight shadow capture requires frozen ticker and IHSG provenance"
            )
        if (
            self.training_end_date is not None
            and self.feature_as_of is not None
            and self.training_end_date >= self.feature_as_of
        ):
            raise ValueError("training_end_date must be earlier than feature_as_of")
        if (
            self.training_end_date is not None
            and self.forecast_as_of is not None
            and self.training_end_date
            > self.forecast_as_of - timedelta(days=self.horizon_days)
        ):
            raise ValueError(
                "training_end_date outcome must be known by forecast_as_of"
            )
        if self.shadow_decision is not None and not self.shadow_evaluation_only:
            raise ValueError("shadow_decision must remain shadow_evaluation_only")
        if self.shadow_evaluation_only and self.shadow_buy_ev_floor is None:
            raise ValueError("shadow evaluation requires shadow_buy_ev_floor")
        if (
            self.forecast_status != "READY"
            and "forecast_status" in self.model_fields_set
            and "failure_reason" not in self.model_fields_set
        ):
            raise ValueError(
                "Explicit non-READY forecast_status requires explicit failure_reason"
            )
        reason = str(self.failure_reason or "").strip()
        if self.forecast_status == "READY":
            if reason:
                raise ValueError("READY forecasts cannot include failure_reason")
        elif not reason:
            raise ValueError("Non-READY forecasts require failure_reason")
        return self
