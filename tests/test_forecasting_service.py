"""Tests for ForecastingService decision logic and graceful fallback."""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest

from core.forecasting.models import ModelBase
from core.forecasting.schemas import ForecastReport, ValidationSummary
from core.forecasting.service import (
    ForecastingService,
    _error_report,
    _make_decision,
    _model_disagreement_penalty,
)
from core.forecasting.validation import compute_ic, validate_model
from utils.ticker import InvalidIDXTicker


class TestDecisionThresholds:
    """BUY: p_target>=0.55 AND edge>=0.15 AND EV>=0.02 AND r_hat_net>=0.015."""

    def test_buy_all_thresholds_met(self):
        assert _make_decision(p_target=0.60, p_stop=0.40, ev=0.03, r_hat_net=0.02) == "BUY"

    def test_buy_exact_boundary(self):
        assert _make_decision(p_target=0.55, p_stop=0.40, ev=0.02, r_hat_net=0.015) == "BUY"

    def test_watch_when_ev_positive_but_p_target_low(self):
        assert _make_decision(p_target=0.50, p_stop=0.35, ev=0.01, r_hat_net=0.02) == "WATCH"

    def test_avoid_when_ev_zero(self):
        assert _make_decision(p_target=0.60, p_stop=0.45, ev=0.0, r_hat_net=0.02) == "AVOID"

    def test_avoid_when_ev_negative(self):
        assert _make_decision(p_target=0.40, p_stop=0.55, ev=-0.01, r_hat_net=0.02) == "AVOID"

    def test_avoid_when_any_input_none(self):
        assert _make_decision(None, None, None, None) == "AVOID"
        assert _make_decision(0.60, None, 0.03, 0.02) == "AVOID"

    def test_watch_when_edge_too_small(self):
        assert _make_decision(p_target=0.58, p_stop=0.48, ev=0.03, r_hat_net=0.02) == "WATCH"

    def test_watch_when_r_hat_net_low(self):
        assert _make_decision(p_target=0.60, p_stop=0.40, ev=0.03, r_hat_net=0.010) == "WATCH"


class TestForecastReportSchema:
    def test_error_report_is_valid_forecast_report(self):
        from datetime import date
        report = _error_report("BBCA", date.today(), 10, ["test_flag"])

        assert isinstance(report, ForecastReport)
        assert report.ticker == "BBCA"
        assert report.decision == "AVOID"
        assert "test_flag" in report.data_quality_flags
        assert report.p_target is None

    def test_forecast_report_serializes_cleanly(self):
        from datetime import date
        d = _error_report("TLKM", date.today(), 10, []).model_dump()

        assert d["ticker"] == "TLKM"
        assert isinstance(d["model_votes"], list)
        assert isinstance(d["data_quality_flags"], list)


def test_predict_rejects_invalid_ticker_before_policy_or_dataset(monkeypatch) -> None:
    service = ForecastingService()

    monkeypatch.setattr(
        "core.forecasting.service._is_blocked",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid ticker reached forecast blocklist"
        ),
    )
    monkeypatch.setattr(
        service._dataset_builder,
        "build",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid ticker reached forecast dataset"
        ),
    )

    with pytest.raises(InvalidIDXTicker):
        service.predict("../escape")


def _valid_verdict() -> dict:
    return {
        "ticker": "BBCA",
        "rating": "BUY",
        "confidence": 0.72,
        "current_price": 9000,
        "entry_price_range": "8900 - 9100",
        "target_price": 10000,
        "stop_loss": 8600,
        "risk_reward_ratio": 2.0,
    }


class TestGracefulFallback:
    def test_import_core_forecasting_succeeds_without_optional_deps(self):
        from core.forecasting import ForecastingService, ForecastReport  # noqa: F401
        assert ForecastingService is not None

    def test_inject_forecast_reports_smoke_empty_list(self):
        """_inject_forecast_reports on empty list must not raise."""
        import asyncio
        from core.orchestrator.legacy import _inject_forecast_reports

        asyncio.run(_inject_forecast_reports([]))

    def test_inject_forecast_skips_non_success_results(self):
        """Results without verdict or with error status are skipped silently."""
        import asyncio
        from core.orchestrator.legacy import _inject_forecast_reports

        results = [
            {"ticker": "BBCA", "status": "error", "verdict": None},
            {"ticker": "BBRI", "status": "success", "verdict": None},
        ]
        asyncio.run(_inject_forecast_reports(results))
        assert "forecast_report" not in results[0]
        assert "forecast_report" not in results[1]

    def test_inject_forecast_ignores_ev_when_validation_failed(self, monkeypatch):
        """Failed walk-forward validation must not influence conviction scoring."""
        import asyncio
        import sys
        import types
        from datetime import date

        from core.forecasting.schemas import ForecastReport, ValidationSummary
        from core.orchestrator.legacy import _inject_forecast_reports

        class FakeForecastingService:
            def predict(self, *_args, **_kwargs):
                return ForecastReport(
                    ticker="BBCA",
                    as_of=date(2026, 6, 30),
                    horizon_days=10,
                    expected_value=0.42,
                    validation_summary=ValidationSummary(
                        horizon_days=10,
                        status="failed",
                    ),
                    data_quality_flags=["validation_status:failed"],
                )

        fake_module = types.ModuleType("core.forecasting")
        fake_module.ForecastingService = FakeForecastingService
        monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)

        results = [
            {
                "ticker": "BBCA",
                "status": "success",
                "verdict": {
                    "ticker": "BBCA",
                    "rating": "BUY",
                    "confidence": 0.72,
                    "current_price": 9000,
                    "entry_price_range": "8900 - 9100",
                    "target_price": 10000,
                    "stop_loss": 8600,
                    "risk_reward_ratio": 2.0,
                },
            }
        ]

        asyncio.run(_inject_forecast_reports(results))

        assert results[0]["forecast_report"]["validation_summary"]["status"] == "failed"
        assert "forecast_ev_pct" not in results[0]
        assert results[0]["forecast_ev_ignored_reason"] == "validation_failed"

    def test_inject_forecast_uses_full_risk_adjusted_ev_when_production(self, monkeypatch):
        import asyncio
        import sys
        import types
        from datetime import date

        from core.forecasting.schemas import ForecastReport, ValidationSummary
        from core.orchestrator.legacy import _inject_forecast_reports

        class FakeForecastingService:
            def predict(self, *_args, **_kwargs):
                return ForecastReport(
                    ticker="BBCA",
                    as_of=date(2026, 6, 30),
                    horizon_days=10,
                    expected_value=0.42,
                    risk_adjusted_expected_value=0.20,
                    validation_summary=ValidationSummary(
                        horizon_days=10,
                        status="production",
                    ),
                    data_quality_flags=["validation_status:production"],
                )

        fake_module = types.ModuleType("core.forecasting")
        fake_module.ForecastingService = FakeForecastingService
        monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)
        results = [{"ticker": "BBCA", "status": "success", "verdict": _valid_verdict()}]

        asyncio.run(_inject_forecast_reports(results))

        assert results[0]["forecast_ev_pct"] == pytest.approx(20.0)
        assert "forecast_ev_downweight" not in results[0]
        assert "forecast_ev_ignored_reason" not in results[0]

    def test_inject_forecast_downweights_research_only_ev(self, monkeypatch):
        import asyncio
        import sys
        import types
        from datetime import date

        from core.forecasting.schemas import ForecastReport, ValidationSummary
        from core.orchestrator.legacy import _inject_forecast_reports

        class FakeForecastingService:
            def predict(self, *_args, **_kwargs):
                return ForecastReport(
                    ticker="BBCA",
                    as_of=date(2026, 6, 30),
                    horizon_days=10,
                    expected_value=0.42,
                    risk_adjusted_expected_value=0.20,
                    validation_summary=ValidationSummary(
                        horizon_days=10,
                        status="research_only",
                    ),
                    data_quality_flags=["validation_status:research_only"],
                )

        fake_module = types.ModuleType("core.forecasting")
        fake_module.ForecastingService = FakeForecastingService
        monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)
        results = [{"ticker": "BBCA", "status": "success", "verdict": _valid_verdict()}]

        asyncio.run(_inject_forecast_reports(results))

        assert results[0]["forecast_ev_pct"] == pytest.approx(7.0)
        assert results[0]["forecast_ev_downweight"] == pytest.approx(0.35)
        assert "forecast_ev_ignored_reason" not in results[0]


class _PredictionColumnModel(ModelBase):
    name = "prediction_column"

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        return None

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return X["pred"].to_numpy(dtype=float)


class _FixedReturnModel(ModelBase):
    def __init__(self, name: str, value: float) -> None:
        self.name = name
        self._value = value

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        return None

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.full(len(X), self._value, dtype=float)


class _FailingReturnModel(ModelBase):
    name = "xgboost"

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        raise ImportError("xgboost missing")

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.zeros(len(X), dtype=float)


class _FakeDatasetBuilder:
    def build(self, tickers, start, end, horizons):
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        index = pd.MultiIndex.from_product([["BBCA"], dates], names=["ticker", "date"])
        return pd.DataFrame(
            {
                "close": np.linspace(9000.0, 9900.0, len(index)),
                "ocf_price_pct": np.full(len(index), 0.05),
            },
            index=index,
        )


class _FakeTGarch:
    def predict_volatility(self, returns: pd.Series, horizon: int):
        return [0.20], False


def _fake_labeled_frame() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=100, freq="D")
    r_net = np.linspace(-0.02, 0.03, len(dates))
    return pd.DataFrame(
        {
            "feature": np.linspace(0.0, 1.0, len(dates)),
            "r_net_h": r_net,
            "y_up": (r_net > 0).astype(float),
            "y_target_hit": (r_net > 0.015).astype(float),
            "y_stop_hit": (r_net < -0.015).astype(float),
            "sigma_realized": np.full(len(dates), 0.18),
        },
        index=dates,
    )


def _service_with_fakes(monkeypatch, factories=None, status_map=None):
    service = ForecastingService()
    service._dataset_builder = _FakeDatasetBuilder()
    service._tgarch = _FakeTGarch()
    default_factories = {
        "naive": lambda: _FixedReturnModel("naive", 0.020),
        "xgboost": lambda: _FixedReturnModel("xgboost", 0.024),
    }
    service._return_model_factories = lambda: factories or default_factories

    monkeypatch.setattr(
        "core.forecasting.service.build_labels",
        lambda _df, _horizon: _fake_labeled_frame(),
    )
    monkeypatch.setattr(
        "core.forecasting.service.walk_forward_splits",
        lambda labeled, n_splits, test_size_days: [(labeled.iloc[:60], labeled.iloc[60:90])],
    )

    statuses = status_map or {}

    def fake_validate(model, _splits, horizon):
        status = statuses.get(model.name, "production")
        return ValidationSummary(
            horizon_days=horizon,
            n_observations=30,
            ic_mean=0.05 if status != "failed" else -0.01,
            ic_t_stat=3.0 if status == "production" else 1.0,
            brier=0.20,
            rmse=0.01,
            mae=0.008,
            mape=0.40,
            directional_accuracy=0.70,
            dsr=0.10,
            bh_q_value_passed=status == "production",
            status=status,
        )

    monkeypatch.setattr("core.forecasting.service.validate_model", fake_validate)
    return service


def test_validate_model_reports_error_and_direction_metrics() -> None:
    train = pd.DataFrame(
        {
            "pred": [0.01, -0.02, 0.03, -0.01, 0.04],
            "r_net_h": [0.01, -0.02, 0.03, -0.01, 0.04],
            "y_up": [1, 0, 1, 0, 1],
        }
    )
    test = pd.DataFrame(
        {
            "pred": [0.08, -0.02, -0.01, -0.03, 0.05],
            "r_net_h": [0.10, -0.05, 0.02, -0.01, 0.04],
            "y_up": [1, 0, 1, 0, 1],
        }
    )

    summary = validate_model(_PredictionColumnModel(), [(train, test)], horizon=10)

    assert summary.mae == pytest.approx(0.022)
    assert summary.mape == pytest.approx(0.91)
    assert summary.directional_accuracy == pytest.approx(0.8)


def test_compute_ic_returns_nan_for_constant_prediction_without_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ic = compute_ic(np.array([0.01, -0.02, 0.03]), np.array([0.0, 0.0, 0.0]))

    assert math.isnan(ic)
    assert not caught


def test_predict_modes_have_distinct_model_composition(monkeypatch) -> None:
    service = _service_with_fakes(monkeypatch)

    naive = service.predict("BBCA", mode="naive")
    tgarch = service.predict("BBCA", mode="tgarch")
    ensemble = service.predict("BBCA", mode="ensemble")

    assert [vote.model_name for vote in naive.model_votes] == ["naive"]
    assert "mode_naive_realized_volatility" in naive.data_quality_flags
    assert [vote.model_name for vote in tgarch.model_votes] == ["tgarch"]
    assert tgarch.validation_summary is None
    assert tgarch.risk_adjusted_expected_value is None

    names = {vote.model_name for vote in ensemble.model_votes}
    assert {"naive", "xgboost", "tgarch", "lstm", "prophet"}.issubset(names)
    statuses = {vote.model_name: vote.status for vote in ensemble.model_votes}
    assert statuses["lstm"] == "experimental_unused"
    assert statuses["prophet"] == "experimental_unused"


def test_predict_marks_unavailable_optional_models(monkeypatch) -> None:
    factories = {
        "naive": lambda: _FixedReturnModel("naive", 0.020),
        "xgboost": _FailingReturnModel,
    }
    service = _service_with_fakes(monkeypatch, factories=factories)

    report = service.predict("BBCA", mode="ensemble")
    xgboost_vote = next(vote for vote in report.model_votes if vote.model_name == "xgboost")

    assert xgboost_vote.status == "unavailable"
    assert "model_unavailable:xgboost" in report.data_quality_flags


def test_ensemble_does_not_blend_failed_return_models(monkeypatch) -> None:
    service = _service_with_fakes(
        monkeypatch,
        status_map={"naive": "failed", "xgboost": "failed"},
    )

    report = service.predict("BBCA", mode="ensemble")
    return_votes = [
        vote
        for vote in report.model_votes
        if vote.model_name in {"naive", "xgboost"}
    ]

    assert {vote.status for vote in return_votes} == {"validation_failed"}
    assert all(vote.weight == 0.0 for vote in return_votes)
    assert report.expected_return_net is None
    assert report.p_target is None
    assert report.p_stop is None
    assert report.expected_value is None
    assert report.risk_adjusted_expected_value is None
    assert report.decision == "AVOID"
    assert "no_validated_return_model" in report.data_quality_flags


def test_explicit_naive_mode_can_still_emit_failed_baseline(monkeypatch) -> None:
    service = _service_with_fakes(monkeypatch, status_map={"naive": "failed"})

    report = service.predict("BBCA", mode="naive")
    naive_vote = report.model_votes[0]

    assert naive_vote.model_name == "naive"
    assert naive_vote.status == "validation_failed"
    assert naive_vote.weight == 1.0
    assert report.expected_return_net == pytest.approx(0.020)
    assert report.risk_adjusted_expected_value is None
    assert "no_validated_return_model" not in report.data_quality_flags


def test_model_disagreement_penalty_detects_direction_conflict() -> None:
    from core.forecasting.schemas import ModelVote

    dispersion, penalty = _model_disagreement_penalty(
        [ModelVote(model_name="a", r_hat_net=0.05, status="active")]
    )
    assert dispersion is None
    assert penalty == 0.0

    dispersion, penalty = _model_disagreement_penalty(
        [
            ModelVote(model_name="a", r_hat_net=0.05, status="active"),
            ModelVote(model_name="b", r_hat_net=-0.04, status="active"),
        ]
    )

    assert dispersion is not None and dispersion > 0
    assert penalty >= 0.10
