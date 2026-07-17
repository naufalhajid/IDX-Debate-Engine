"""Phase 2 regressions for out-of-sample forecast correctness.

These tests keep calibration/shadow output separate from the live decision and
exercise historical BBNI/BBCA artifacts without pretending they contain the
raw feature snapshots required for a full refit replay.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import core.forecasting.dataset as dataset_module
import core.forecasting.service as forecasting_service
from core.forecasting.ensemble import blend_votes, compute_ensemble_weights
from core.forecasting.models import ModelBase
from core.forecasting.models.xgboost_model import XGBoostForecaster
from core.forecasting.schemas import ForecastReport, ModelVote, ValidationSummary
from core.settings import Settings


_BBNI_FIXTURE = Path(
    "output/ablation_forward/debates/BBNI/latest_debate.json"
)
_BBCA_FIXTURE = Path(
    "tests/baselines/full_system_exam_20260712/"
    "pipeline_live_6_retry/debates/BBCA/latest_debate.json"
)


class _FixedReturnModel(ModelBase):
    name = "naive"

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._columns = list(X.columns)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        assert list(X.columns) == self._columns
        return np.full(len(X), 0.0234, dtype=float)


def _frozen_forecast_frame(as_of: date, horizon: int = 10) -> pd.DataFrame:
    dates = pd.bdate_range(end=as_of, periods=100)
    close = pd.Series(np.linspace(9_000.0, 9_900.0, len(dates)), index=dates)
    return pd.DataFrame(
        {
            "close": close.to_numpy(),
            f"close_t{horizon}": close.shift(-horizon).to_numpy(),
            "ocf_price_pct": np.full(len(dates), 0.05),
            "feature_marker": np.arange(len(dates), dtype=float),
        },
        index=pd.MultiIndex.from_arrays(
            [["BBCA"] * len(dates), dates.date],
            names=["ticker", "date"],
        ),
    )


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def test_asii_horizon_10_return_is_used_directly_in_target_probability() -> None:
    """The audited ASII case must move from 41.7% to 58.0%, not merely change."""

    horizon = 10
    r_hat_h = 0.0234
    annual_sigma = 0.2741543882837838
    close = 100.0
    target = 101.2450894346
    verdict = SimpleNamespace(target_price=target, stop_loss=98.7549105654)

    sigma_h = annual_sigma * math.sqrt(horizon / 252.0)
    target_log_return = math.log(target / close)
    legacy_double_scaled = 1.0 - _normal_cdf(
        (target_log_return - r_hat_h * horizon / 252.0) / sigma_h
    )
    corrected_target, _ = forecasting_service._compute_probs(
        r_hat_h,
        annual_sigma,
        horizon,
        verdict,
        close,
    )

    assert legacy_double_scaled == pytest.approx(0.417, abs=1e-9)
    assert corrected_target == pytest.approx(0.580, abs=1e-9)


def test_forecast_report_enforces_exact_as_of_and_label_availability() -> None:
    """Provenance must reject stale inference and too-recent training labels."""

    with pytest.raises(ValueError, match="feature_as_of must equal forecast_as_of"):
        ForecastReport(
            ticker="BBCA",
            as_of=date(2026, 7, 15),
            forecast_as_of=date(2026, 7, 15),
            horizon_days=10,
            feature_as_of=date(2026, 7, 14),
            training_end_date=date(2026, 7, 1),
        )

    with pytest.raises(ValueError, match="outcome must be known"):
        ForecastReport(
            ticker="BBCA",
            as_of=date(2026, 7, 15),
            forecast_as_of=date(2026, 7, 15),
            horizon_days=10,
            feature_as_of=date(2026, 7, 15),
            training_end_date=date(2026, 7, 14),
        )


def test_error_report_always_persists_forecast_as_of() -> None:
    as_of = date(2026, 7, 15)

    report = forecasting_service._error_report(
        "BBCA", as_of, 10, ["synthetic_failure"]
    )

    assert report.forecast_as_of == as_of


def test_dataset_split_exposes_known_training_and_latest_unlabeled_feature() -> None:
    as_of = date(2026, 7, 15)
    frame = _frozen_forecast_frame(as_of)
    split_forecast_dataset = getattr(
        dataset_module,
        "split_forecast_dataset",
        None,
    )

    assert callable(split_forecast_dataset), "missing explicit forecast dataset split"
    split = split_forecast_dataset(frame, horizon=10)

    assert len(split.training_features) == 90
    assert len(split.inference_features) == 1
    assert split.training_features["close_t10"].notna().all()
    assert split.inference_features["close_t10"].isna().all()
    assert split.inference_features.index[-1][1] == as_of
    assert set(split.training_features.index).isdisjoint(
        split.inference_features.index
    )


def test_repeated_same_date_forecasts_pin_the_materialized_feature_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changing provider must be consulted once for one service/run key."""

    as_of = date(2026, 7, 15)
    frozen = _frozen_forecast_frame(as_of)

    class _ChangingBuilder:
        def __init__(self) -> None:
            self.calls = 0

        def build(self, *_args, **_kwargs) -> pd.DataFrame:
            self.calls += 1
            changed = frozen.copy(deep=True)
            changed["feature_marker"] += 10_000.0 * (self.calls - 1)
            return changed

    builder = _ChangingBuilder()
    service = forecasting_service.ForecastingService()
    service._dataset_builder = builder
    service._return_model_factories = lambda: {"naive": _FixedReturnModel}
    monkeypatch.setattr(
        forecasting_service,
        "walk_forward_splits",
        lambda *_args, **_kwargs: [],
    )

    reports = [
        service.predict("BBCA", as_of=as_of, horizons=(10,), mode="naive")
        for _ in range(3)
    ]

    assert builder.calls == 1
    assert {report.feature_snapshot_hash for report in reports} == {
        reports[0].feature_snapshot_hash
    }
    assert reports[0].feature_snapshot_hash
    assert {report.expected_return_net for report in reports} == {0.0234}

    # A new requested date is a different calibration snapshot and must miss.
    service.predict(
        "BBCA",
        as_of=as_of + timedelta(days=1),
        horizons=(10,),
        mode="naive",
    )
    assert builder.calls == 2


@pytest.mark.parametrize("xgboost_brier", [0.24895, 0.25005])
def test_borderline_bbca_brier_is_fail_closed_and_logged(
    caplog: pytest.LogCaptureFixture,
    xgboost_brier: float,
) -> None:
    """Both observed sides of the BBCA boundary are calibration ties."""

    with caplog.at_level("WARNING"):
        weights = compute_ensemble_weights(
            {
                "xgboost": {
                    "ic": 0.05,
                    "rmse": 0.01,
                    "brier": xgboost_brier,
                    "dsr": 0.10,
                    "bh_passed": True,
                }
            },
            naive_brier_benchmark=0.25,
        )

    assert weights["xgboost"] == pytest.approx(0.0)
    assert "borderline Brier" in caplog.text


def test_xgboost_contract_is_seeded_and_return_only() -> None:
    model = XGBoostForecaster()

    assert model._xgb_params()["random_state"] == 0
    assert "return-only" in (XGBoostForecaster.__doc__ or "").lower()
    assert not hasattr(model, "predict_proba_target")
    assert not hasattr(model, "predict_proba_stop")


def test_borderline_brier_is_surfaced_in_forecast_quality_flags() -> None:
    flags: list[str] = []
    validations = {
        "naive": ValidationSummary(
            horizon_days=10,
            brier=0.25,
            status="failed",
        ),
        "xgboost": ValidationSummary(
            horizon_days=10,
            ic_mean=0.05,
            brier=0.24895,
            bh_q_value_passed=True,
            status="production",
        ),
    }

    weights = forecasting_service._model_weights(
        "ensemble",
        validations,
        {"naive": 0.0, "xgboost": 0.0234},
        flags=flags,
    )

    assert weights["xgboost"] == pytest.approx(0.0)
    assert any(flag.startswith("brier_borderline:xgboost:") for flag in flags)


def test_shadow_ev_floor_is_configured_without_changing_live_buy_rule() -> None:
    configured = Settings(_env_file=None)
    p_target, p_stop, ev, r_hat = 0.70, 0.10, 0.008, 0.0234

    assert configured.FORECAST_SHADOW_EVALUATION_ENABLED is True
    assert configured.FORECAST_SHADOW_BUY_EV_FLOOR == pytest.approx(0.005)
    assert forecasting_service._make_decision(p_target, p_stop, ev, r_hat) == "WATCH"

    shadow_decision = getattr(
        forecasting_service,
        "_make_shadow_decision",
        None,
    )
    assert callable(shadow_decision)
    assert (
        shadow_decision(
            p_target,
            p_stop,
            ev,
            r_hat,
            ev_floor=configured.FORECAST_SHADOW_BUY_EV_FLOOR,
        )
        == "BUY"
    )


def test_bbni_fixture_replay_changes_scaling_but_not_live_decision() -> None:
    """Replay stored model inputs; legacy JSON lacks features for an honest refit."""

    payload = json.loads(_BBNI_FIXTURE.read_text(encoding="utf-8"))
    report = payload["forecast_report"]
    xgboost_vote = next(
        vote for vote in report["model_votes"] if vote["model_name"] == "xgboost"
    )
    verdict = SimpleNamespace(target_price=3720.0, stop_loss=3200.0)

    p_target, p_stop = forecasting_service._compute_probs(
        xgboost_vote["r_hat_net"],
        xgboost_vote["volatility_forecast"],
        10,
        verdict,
        3320.0,
    )
    ev = forecasting_service._compute_ev(p_target, p_stop, verdict, 3320.0, 10)
    adjusted_ev = ev * (1.0 - report["model_disagreement_penalty"])

    assert xgboost_vote["p_target"] == pytest.approx(0.1952679107446842)
    assert xgboost_vote["p_stop"] == pytest.approx(0.3855257460600442)
    assert p_target == pytest.approx(0.2680934521704894)
    assert p_stop == pytest.approx(0.29768982912217923)
    assert ev == pytest.approx(0.014540542582389834)
    assert adjusted_ev == pytest.approx(0.012464153101624565)
    assert (
        forecasting_service._make_decision(
            p_target,
            p_stop,
            adjusted_ev,
            xgboost_vote["r_hat_net"],
        )
        == "WATCH"
    )


def test_bbca_fixture_replay_keeps_worse_xgboost_out_of_aggregate() -> None:
    payload = json.loads(_BBCA_FIXTURE.read_text(encoding="utf-8"))
    report = payload["forecast_report"]
    validations = {
        name: ValidationSummary(**summary)
        for name, summary in report["validation_by_model"].items()
        if name in {"naive", "xgboost"}
    }
    predictions = {
        vote["model_name"]: vote["r_hat_net"]
        for vote in report["model_votes"]
        if vote["model_name"] in {"naive", "xgboost"}
    }

    weights = forecasting_service._model_weights(
        "ensemble",
        validations,
        predictions,
    )
    xgboost_vote = next(
        vote for vote in report["model_votes"] if vote["model_name"] == "xgboost"
    )
    p_target, p_stop = forecasting_service._compute_probs(
        xgboost_vote["r_hat_net"],
        xgboost_vote["volatility_forecast"],
        10,
        None,
        None,
    )
    blended = blend_votes(
        [
            ModelVote(
                model_name="xgboost",
                r_hat_net=xgboost_vote["r_hat_net"],
                p_target=p_target,
                p_stop=p_stop,
                weight=weights["xgboost"],
            )
        ]
    )

    assert validations["xgboost"].brier == pytest.approx(0.2548577119102416)
    assert validations["naive"].brier == pytest.approx(0.25)
    assert weights["xgboost"] == pytest.approx(0.0)
    assert blended == (None, None, None)
