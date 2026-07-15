"""Pydantic v2 schemas for the IDX forecasting layer."""
from __future__ import annotations

from datetime import date
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
    horizon_days: int
    forecast_status: ForecastStatus = "UNAVAILABLE"
    failure_reason: str | None = "legacy_status_missing"
    expected_return_net: float | None = None
    p_target: float | None = None
    p_stop: float | None = None
    volatility_forecast: float | None = None
    expected_value: float | None = None
    decision: Literal["BUY", "WATCH", "AVOID"] = "AVOID"
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
