"""IDX Multi-Model Forecasting Layer — public API.

Usage:
    from core.forecasting import ForecastingService, ForecastReport

Graceful degradation: importing this package never fails even when optional
dependencies (statsmodels, xgboost, prophet, torch) are not installed.
Only the relevant model raises ImportError when actually called.
"""
from __future__ import annotations

from core.forecasting.schemas import (
    ForecastReport,
    ForecastStatus,
    ModelVote,
    ValidationSummary,
)
from core.forecasting.service import ForecastingService

__all__ = [
    "ForecastingService",
    "ForecastReport",
    "ForecastStatus",
    "ModelVote",
    "ValidationSummary",
]
