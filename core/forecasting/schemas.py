"""Pydantic v2 schemas for the IDX forecasting layer."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class ModelVote(BaseModel):
    """Output from a single forecasting model."""

    model_name: str
    r_hat_net: float | None = None
    p_target: float | None = None
    p_stop: float | None = None
    volatility_forecast: float | None = None
    weight: float = 0.0
    validation_passed: bool = False
    ic: float | None = None
    brier_target: float | None = None


class ValidationSummary(BaseModel):
    """Walk-forward validation results for a model."""

    horizon_days: int
    n_observations: int = 0
    ic_mean: float | None = None
    ic_t_stat: float | None = None
    brier: float | None = None
    rmse: float | None = None
    dsr: float | None = None
    bh_q_value_passed: bool = False
    status: Literal["production", "research_only", "failed"] = "failed"


class ForecastReport(BaseModel):
    """Full forecast output for a ticker."""

    ticker: str
    as_of: date
    horizon_days: int
    expected_return_net: float | None = None
    p_target: float | None = None
    p_stop: float | None = None
    volatility_forecast: float | None = None
    expected_value: float | None = None
    decision: Literal["BUY", "WATCH", "AVOID"] = "AVOID"
    confidence: float | None = None
    model_votes: list[ModelVote] = Field(default_factory=list)
    validation_summary: ValidationSummary | None = None
    data_quality_flags: list[str] = Field(default_factory=list)
    volatility_fallback: bool = False
