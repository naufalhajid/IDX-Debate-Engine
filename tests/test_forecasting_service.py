"""Tests for ForecastingService decision logic and graceful fallback."""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest

from core.forecasting.models import ModelBase
from core.forecasting.schemas import ForecastReport, ModelVote, ValidationSummary
from core.forecasting.service import (
    ForecastingService,
    _aggregate_validation,
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
        assert report.forecast_status == "UNAVAILABLE"
        assert report.failure_reason == "test_flag"
        assert "test_flag" in report.data_quality_flags
        assert report.p_target is None

    def test_forecast_report_serializes_cleanly(self):
        from datetime import date
        d = _error_report("TLKM", date.today(), 10, []).model_dump()

        assert d["ticker"] == "TLKM"
        assert d["forecast_status"] == "UNAVAILABLE"
        assert d["failure_reason"] == "forecast_unavailable"
        assert isinstance(d["model_votes"], list)
        assert isinstance(d["data_quality_flags"], list)

    def test_forecast_report_rejects_inconsistent_status_reason(self):
        from datetime import date

        with pytest.raises(ValueError, match="READY forecasts"):
            ForecastReport(
                ticker="BBCA",
                as_of=date.today(),
                horizon_days=10,
                forecast_status="READY",
                failure_reason="unexpected_failure",
            )

        with pytest.raises(ValueError, match="Non-READY forecasts"):
            ForecastReport(
                ticker="BBCA",
                as_of=date.today(),
                horizon_days=10,
                forecast_status="MODEL_FAILED",
                failure_reason=None,
            )

        with pytest.raises(ValueError, match="explicit failure_reason"):
            ForecastReport(
                ticker="BBCA",
                as_of=date.today(),
                horizon_days=10,
                forecast_status="MODEL_FAILED",
            )

    def test_forecast_report_serializes_training_and_inference_provenance(self):
        from datetime import date

        report = ForecastReport(
            ticker="BBCA",
            as_of=date(2026, 7, 15),
            horizon_days=10,
            feature_as_of=date(2026, 7, 15),
            training_end_date=date(2026, 7, 1),
        )

        payload = report.model_dump(mode="json")

        assert payload["feature_as_of"] == "2026-07-15"
        assert payload["training_end_date"] == "2026-07-01"

    def test_forecast_report_rejects_training_inference_overlap(self):
        from datetime import date

        with pytest.raises(ValueError, match="training_end_date must be earlier"):
            ForecastReport(
                ticker="BBCA",
                as_of=date(2026, 7, 15),
                horizon_days=10,
                feature_as_of=date(2026, 7, 15),
                training_end_date=date(2026, 7, 15),
            )


def test_predict_cli_renders_explicit_status_and_reason(monkeypatch) -> None:
    from datetime import date

    from rich.console import Console

    from app.cli.commands import forecast as forecast_cli

    report = ForecastReport(
        ticker="BBCA",
        as_of=date(2026, 7, 15),
        horizon_days=10,
        forecast_status="ZERO_WEIGHT",
        failure_reason="all_validated_return_models_disqualified",
    )

    class FakeService:
        def predict(self, *_args, **_kwargs):
            return report

    test_console = Console(record=True, width=140)
    monkeypatch.setattr(forecast_cli, "console", test_console)
    monkeypatch.setattr(forecast_cli, "_get_service", lambda: FakeService())

    forecast_cli.predict_command("BBCA", horizon=10, mode="ensemble")
    output = test_console.export_text()

    assert "status: ZERO_WEIGHT" in output
    assert "reason: all_validated_return_models_disqualified" in output


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
                    forecast_status="VALIDATION_FAILED",
                    failure_reason="all_return_models_failed_validation",
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
        assert results[0]["forecast_ev_ignored_reason"] == "forecast_validation_failed"

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
                    forecast_status="READY",
                    failure_reason=None,
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
                    forecast_status="READY",
                    failure_reason=None,
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

    @pytest.mark.parametrize(
        ("forecast_status", "expected_reason"),
        [
            ("NOT_VALIDATED", "forecast_not_validated"),
            ("VALIDATION_FAILED", "forecast_validation_failed"),
            ("MODEL_FAILED", "forecast_model_failed"),
            ("ZERO_WEIGHT", "forecast_zero_weight"),
            ("UNAVAILABLE", "forecast_unavailable"),
        ],
    )
    def test_ranking_reports_distinct_non_ready_reason(
        self,
        forecast_status,
        expected_reason,
    ):
        from core.orchestrator.legacy import _forecast_ranking_ev

        ranking_ev, downweight, ignored_reason = _forecast_ranking_ev(
            {
                "forecast_status": forecast_status,
                "risk_adjusted_expected_value": 0.20,
                "validation_summary": {"status": "production"},
            }
        )

        assert ranking_ev is None
        assert downweight is None
        assert ignored_reason == expected_reason

    def test_ranking_fails_closed_when_legacy_status_is_missing(self):
        from core.orchestrator.legacy import _forecast_ranking_ev

        ranking_ev, downweight, ignored_reason = _forecast_ranking_ev(
            {
                "risk_adjusted_expected_value": 0.20,
                "validation_summary": {"status": "production"},
            }
        )

        assert ranking_ev is None
        assert downweight is None
        assert ignored_reason == "forecast_status_missing"


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


class _RecordingReturnModel(ModelBase):
    name = "naive"

    def __init__(self) -> None:
        self.fit_frame: pd.DataFrame | None = None
        self.fit_target: pd.Series | None = None
        self.predict_frame: pd.DataFrame | None = None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self.fit_frame = X.copy()
        self.fit_target = y.copy()

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        self.predict_frame = X.copy()
        return np.full(len(X), 0.02, dtype=float)


class _FailingReturnModel(ModelBase):
    name = "xgboost"

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        raise ImportError("xgboost missing")

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.zeros(len(X), dtype=float)


class _FakeDatasetBuilder:
    def build(
        self,
        tickers,
        start,
        end,
        horizons,
        snapshots=None,
        *,
        include_unlabeled_tail=False,
    ):
        assert include_unlabeled_tail is True
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        index = pd.MultiIndex.from_product([["BBCA"], dates], names=["ticker", "date"])
        close = pd.Series(np.linspace(9000.0, 9900.0, len(index)))
        horizon = int(horizons[0])
        return pd.DataFrame(
            {
                "close": close.to_numpy(),
                f"close_t{horizon}": close.shift(-horizon).to_numpy(),
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
    r_net[-1] = np.nan
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


def test_predict_fits_labeled_history_and_predicts_latest_unlabeled_row(
    monkeypatch,
) -> None:
    from datetime import date

    as_of = date(2026, 7, 15)
    dates = pd.bdate_range(end=as_of, periods=100)
    close = pd.Series(np.linspace(9000.0, 9900.0, len(dates)), index=dates)
    frame = pd.DataFrame(
        {
            "close": close.to_numpy(),
            "close_t10": close.shift(-10).to_numpy(),
            "ocf_price_pct": np.full(len(dates), 0.05),
            "feature_marker": np.arange(len(dates), dtype=float),
        },
        index=pd.MultiIndex.from_arrays(
            [["BBCA"] * len(dates), dates.date],
            names=["ticker", "date"],
        ),
    )

    class LatestRowDatasetBuilder:
        def build(
            self,
            tickers,
            start,
            end,
            horizons,
            snapshots=None,
            *,
            include_unlabeled_tail=False,
        ):
            assert tickers == ["BBCA"]
            assert end == as_of
            assert horizons == (10,)
            assert include_unlabeled_tail is True
            return frame

    recorder = _RecordingReturnModel()
    service = ForecastingService()
    service._dataset_builder = LatestRowDatasetBuilder()
    service._return_model_factories = lambda: {"naive": lambda: recorder}
    monkeypatch.setattr(
        "core.forecasting.service.walk_forward_splits",
        lambda *_args, **_kwargs: [],
    )

    report = service.predict("BBCA", as_of=as_of, horizons=(10,), mode="naive")

    assert recorder.fit_frame is not None
    assert recorder.fit_target is not None
    assert recorder.predict_frame is not None
    assert len(recorder.fit_frame) == 90
    assert len(recorder.predict_frame) == 1
    assert recorder.fit_target.notna().all()
    assert recorder.fit_frame.index.equals(recorder.fit_target.index)
    assert recorder.fit_frame.columns.equals(recorder.predict_frame.columns)
    assert pd.Timestamp(recorder.fit_frame.index[-1]).date() == dates[-11].date()
    assert pd.Timestamp(recorder.predict_frame.index[-1]).date() == dates[-1].date()
    assert recorder.predict_frame["feature_marker"].iloc[0] == pytest.approx(99.0)
    assert set(recorder.fit_frame.index).isdisjoint(recorder.predict_frame.index)
    assert report.as_of == as_of
    assert report.feature_as_of == as_of
    assert report.training_end_date == dates[-11].date()


def test_predict_fails_closed_without_unlabeled_inference_row(monkeypatch) -> None:
    service = _service_with_fakes(monkeypatch)
    fully_labeled = _fake_labeled_frame()
    fully_labeled.loc[fully_labeled.index[-1], "r_net_h"] = 0.01
    monkeypatch.setattr(
        "core.forecasting.service.build_labels",
        lambda _df, _horizon: fully_labeled,
    )

    report = service.predict("BBCA", mode="ensemble")

    assert report.forecast_status == "UNAVAILABLE"
    assert report.decision == "AVOID"
    assert report.failure_reason == "missing_unlabeled_inference_row"


def _service_with_fakes(
    monkeypatch,
    factories=None,
    status_map=None,
    brier_map=None,
):
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
    briers = brier_map or {"naive": 0.25, "xgboost": 0.15}

    def fake_validate(model, _splits, horizon):
        status = statuses.get(model.name, "production")
        return ValidationSummary(
            horizon_days=horizon,
            n_observations=30,
            ic_mean=0.05 if status != "failed" else -0.01,
            ic_t_stat=3.0 if status == "production" else 1.0,
            brier=briers.get(model.name, 0.15),
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
    assert naive.forecast_status == "READY"
    assert naive.failure_reason is None
    assert "mode_naive_realized_volatility" in naive.data_quality_flags
    assert [vote.model_name for vote in tgarch.model_votes] == ["tgarch"]
    assert tgarch.forecast_status == "READY"
    assert tgarch.failure_reason is None
    assert tgarch.validation_summary is None
    assert tgarch.risk_adjusted_expected_value is None

    names = {vote.model_name for vote in ensemble.model_votes}
    assert {"naive", "xgboost", "tgarch", "lstm", "prophet"}.issubset(names)
    statuses = {vote.model_name: vote.status for vote in ensemble.model_votes}
    assert statuses["lstm"] == "experimental_unused"
    assert statuses["prophet"] == "experimental_unused"
    assert ensemble.forecast_status == "READY"
    assert ensemble.failure_reason is None
    assert ensemble.risk_adjusted_expected_value is not None


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


def test_partial_model_failure_keeps_viable_ensemble_ready(monkeypatch) -> None:
    factories = {
        "naive": _FailingReturnModel,
        "xgboost": lambda: _FixedReturnModel("xgboost", 0.024),
    }
    service = _service_with_fakes(monkeypatch, factories=factories)

    report = service.predict("BBCA", mode="ensemble")
    xgboost_vote = next(vote for vote in report.model_votes if vote.model_name == "xgboost")

    assert xgboost_vote.weight == pytest.approx(1.0)
    assert report.forecast_status == "READY"
    assert report.failure_reason is None
    assert report.risk_adjusted_expected_value is not None


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
    assert report.forecast_status == "VALIDATION_FAILED"
    assert report.failure_reason == "all_return_models_failed_validation"
    assert "no_validated_return_model" in report.data_quality_flags


def test_ensemble_reports_zero_weight_when_validated_models_are_disqualified(
    monkeypatch,
) -> None:
    service = _service_with_fakes(
        monkeypatch,
        brier_map={"naive": 0.20, "xgboost": 0.30},
    )

    report = service.predict("BBCA", mode="ensemble")
    return_votes = [
        vote
        for vote in report.model_votes
        if vote.model_name in {"naive", "xgboost"}
    ]

    assert all(vote.r_hat_net is not None for vote in return_votes)
    assert all(vote.weight == 0.0 for vote in return_votes)
    assert report.forecast_status == "ZERO_WEIGHT"
    assert report.failure_reason == "all_validated_return_models_disqualified"
    assert report.expected_return_net is None


def test_failed_naive_still_benchmarks_production_xgboost_brier(monkeypatch) -> None:
    """Historical BBCA metrics must retain Naive as a non-blended benchmark."""
    service = _service_with_fakes(
        monkeypatch,
        status_map={"naive": "failed", "xgboost": "production"},
        brier_map={"naive": 0.25, "xgboost": 0.26252},
    )

    report = service.predict("BBCA", mode="ensemble")
    return_votes = {
        vote.model_name: vote
        for vote in report.model_votes
        if vote.model_name in {"naive", "xgboost"}
    }

    assert return_votes["naive"].status == "validation_failed"
    assert return_votes["naive"].weight == pytest.approx(0.0)
    assert return_votes["xgboost"].status == "active"
    assert return_votes["xgboost"].validation_passed is True
    assert return_votes["xgboost"].weight == pytest.approx(0.0)
    assert report.forecast_status == "ZERO_WEIGHT"
    assert report.failure_reason == "all_validated_return_models_disqualified"
    assert report.expected_return_net is None
    assert "no_validated_return_model" in report.data_quality_flags


def test_ensemble_reports_not_validated_when_walk_forward_is_unavailable(
    monkeypatch,
) -> None:
    service = _service_with_fakes(monkeypatch)
    monkeypatch.setattr(
        "core.forecasting.service.walk_forward_splits",
        lambda *_args, **_kwargs: [],
    )

    report = service.predict("BBCA", mode="ensemble")
    return_votes = [
        vote
        for vote in report.model_votes
        if vote.model_name in {"naive", "xgboost"}
    ]

    assert {vote.status for vote in return_votes} == {"not_validated"}
    assert report.forecast_status == "NOT_VALIDATED"
    assert report.failure_reason == "walk_forward_validation_unavailable"


def test_ensemble_reports_model_failed_when_all_predictions_fail(monkeypatch) -> None:
    service = _service_with_fakes(
        monkeypatch,
        factories={"naive": _FailingReturnModel, "xgboost": _FailingReturnModel},
    )

    report = service.predict("BBCA", mode="ensemble")

    assert report.forecast_status == "MODEL_FAILED"
    assert report.failure_reason == "all_return_models_unavailable"
    assert all(
        vote.status == "unavailable"
        for vote in report.model_votes
        if vote.model_name in {"naive", "xgboost"}
    )
    assert "validation_unavailable" not in report.data_quality_flags


def test_ensemble_reports_validation_runtime_failure(monkeypatch) -> None:
    service = _service_with_fakes(monkeypatch)

    def fail_validation(*_args, **_kwargs):
        raise RuntimeError("validation exploded")

    monkeypatch.setattr("core.forecasting.service.validate_model", fail_validation)

    report = service.predict("BBCA", mode="ensemble")

    assert report.forecast_status == "VALIDATION_FAILED"
    assert report.failure_reason == "walk_forward_validation_failed"
    assert {
        vote.status
        for vote in report.model_votes
        if vote.model_name in {"naive", "xgboost"}
    } == {"validation_failed"}
    assert "validation_unavailable" not in report.data_quality_flags


def test_explicit_naive_mode_can_still_emit_failed_baseline(monkeypatch) -> None:
    service = _service_with_fakes(monkeypatch, status_map={"naive": "failed"})

    report = service.predict("BBCA", mode="naive")
    naive_vote = report.model_votes[0]

    assert naive_vote.model_name == "naive"
    assert naive_vote.status == "validation_failed"
    assert naive_vote.weight == 1.0
    assert report.forecast_status == "VALIDATION_FAILED"
    assert report.failure_reason == "all_return_models_failed_validation"
    assert report.expected_return_net == pytest.approx(0.020)
    assert report.risk_adjusted_expected_value is None
    assert "no_validated_return_model" not in report.data_quality_flags


def test_tgarch_fallback_is_model_failed(monkeypatch) -> None:
    service = _service_with_fakes(monkeypatch)

    class FailingTGarch:
        def predict_volatility(self, _returns: pd.Series, _horizon: int):
            return [0.20], True

    service._tgarch = FailingTGarch()

    report = service.predict("BBCA", mode="tgarch")

    assert report.forecast_status == "MODEL_FAILED"
    assert report.failure_reason == "tgarch_volatility_model_failed"


def test_aggregate_validation_ignores_zero_weight_model_metadata() -> None:
    validations = {
        "active": ValidationSummary(
            horizon_days=10,
            n_observations=20,
            ic_mean=0.04,
            mape=None,
            bh_q_value_passed=False,
            status="research_only",
        ),
        "excluded": ValidationSummary(
            horizon_days=10,
            n_observations=999,
            ic_mean=0.90,
            mape=99.0,
            bh_q_value_passed=True,
            status="production",
        ),
    }
    votes = [
        ModelVote(
            model_name="active",
            weight=1.0,
            validation_passed=True,
        ),
        ModelVote(
            model_name="excluded",
            weight=0.0,
            validation_passed=True,
        ),
    ]

    summary = _aggregate_validation(validations, votes)

    assert summary is not None
    assert summary.status == "research_only"
    assert summary.n_observations == 20
    assert summary.ic_mean == pytest.approx(0.04)
    assert summary.mape is None
    assert summary.bh_q_value_passed is False


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
