"""Phase 0 regression packet for the Type D -> Type B predictive repair.

These tests intentionally separate desired-behaviour regressions from numeric
characterisation.  The EV ceiling test documents the current live rule and is
therefore expected to pass before the shadow-only policy is introduced.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from core.forecasting.ensemble import compute_ensemble_weights
from core.forecasting.labels import TAU_H, TRANSACTION_COST
from core.forecasting.models import ModelBase
from core.forecasting.models.xgboost_model import XGBoostForecaster
from core.forecasting.schemas import ValidationSummary
from core.forecasting.service import (
    ForecastingService,
    _compute_ev,
    _make_decision,
    _model_weights,
)
from schemas.debate import DebateMessage
from services import debate_chamber as dc
from services.debate_chamber import DebateChamber


class _RecordingReturnModel(ModelBase):
    """Minimal return model that records the train/inference boundary."""

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


def test_horizon_10_forecast_uses_known_labels_and_latest_unlabeled_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Training must stop before the latest as-of inference feature row."""

    forecast_as_of = date(2026, 7, 15)
    horizon = 10
    dates = pd.bdate_range(end=forecast_as_of, periods=100)
    close = pd.Series(np.linspace(9_000.0, 9_900.0, len(dates)), index=dates)
    frozen_frame = pd.DataFrame(
        {
            "close": close.to_numpy(),
            "close_t10": close.shift(-horizon).to_numpy(),
            "ocf_price_pct": np.full(len(dates), 0.05),
            "feature_marker": np.arange(len(dates), dtype=float),
        },
        index=pd.MultiIndex.from_arrays(
            [["BBCA"] * len(dates), dates.date],
            names=["ticker", "date"],
        ),
    )

    class _FrozenDatasetBuilder:
        def build(self, tickers, start, end, horizons, **kwargs):
            assert tickers == ["BBCA"]
            assert end == forecast_as_of
            assert horizons == (horizon,)
            return frozen_frame.copy(deep=True)

    recorder = _RecordingReturnModel()
    service = ForecastingService()
    service._dataset_builder = _FrozenDatasetBuilder()
    service._return_model_factories = lambda: {"naive": lambda: recorder}
    monkeypatch.setattr(
        "core.forecasting.service.walk_forward_splits",
        lambda *_args, **_kwargs: [],
    )

    report = service.predict(
        "BBCA",
        as_of=forecast_as_of,
        horizons=(horizon,),
        mode="naive",
    )

    assert recorder.fit_frame is not None
    assert recorder.fit_target is not None
    assert recorder.predict_frame is not None
    training_end_date = pd.Timestamp(recorder.fit_frame.index[-1]).date()
    feature_as_of = pd.Timestamp(recorder.predict_frame.index[-1]).date()

    assert recorder.fit_target.notna().all()
    assert recorder.fit_frame.index.equals(recorder.fit_target.index)
    assert set(recorder.fit_frame.index).isdisjoint(recorder.predict_frame.index)
    assert feature_as_of == forecast_as_of
    assert training_end_date <= forecast_as_of - timedelta(days=horizon)
    assert report.feature_as_of == forecast_as_of
    assert report.training_end_date == training_end_date
    assert getattr(report, "forecast_as_of", None) == forecast_as_of


def test_bbca_frozen_snapshot_has_reproducible_brier_and_weight() -> None:
    """A fixed BBCA-like boundary fixture isolates model execution from data drift."""

    pytest.importorskip("xgboost")
    rng = np.random.default_rng(20260715)
    row_count = 260
    features = pd.DataFrame(
        rng.normal(size=(row_count, 8)),
        columns=[f"feature_{idx}" for idx in range(8)],
    )
    returns = (
        0.006 * features["feature_0"]
        - 0.004 * features["feature_1"]
        + 0.002 * features["feature_2"]
        + rng.normal(0.0, 0.012, row_count)
    )
    base_labels = (returns > 0).astype(float).to_numpy()
    label_rng = np.random.default_rng(96)
    labels = np.where(label_rng.random(row_count) < 0.20, 1 - base_labels, base_labels)

    train_features = features.iloc[:200].copy(deep=True)
    train_returns = returns.iloc[:200].copy(deep=True)
    test_features = features.iloc[200:].copy(deep=True)
    test_labels = labels[200:].copy()
    frozen_hash = hashlib.sha256(
        pd.util.hash_pandas_object(features, index=True).to_numpy().tobytes()
    ).hexdigest()

    briers: list[float] = []
    xgboost_weights: list[float] = []
    observed_hashes: list[str] = []
    for _ in range(10):
        observed_hashes.append(
            hashlib.sha256(
                pd.util.hash_pandas_object(features, index=True).to_numpy().tobytes()
            ).hexdigest()
        )
        model = XGBoostForecaster(n_estimators=40, max_depth=3)
        model.fit(train_features, train_returns)
        predictions = np.asarray(model.predict(test_features), dtype=float)
        prediction_std = float(np.std(predictions))
        assert prediction_std > 1e-10
        logits = (predictions - float(np.mean(predictions))) / prediction_std
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -10.0, 10.0)))
        brier = float(np.mean((probabilities - test_labels) ** 2))
        briers.append(brier)

        weights = compute_ensemble_weights(
            {
                "naive": {
                    "ic": 0.05,
                    "rmse": 0.01,
                    "brier": 0.25,
                    "dsr": 0.10,
                    "bh_passed": True,
                },
                "xgboost": {
                    "ic": 0.05,
                    "rmse": 0.01,
                    "brier": brier,
                    "dsr": 0.10,
                    "bh_passed": True,
                },
            }
        )
        xgboost_weights.append(weights["xgboost"])

    assert observed_hashes == [frozen_hash] * 10
    assert abs(briers[0] - 0.25) <= 0.001, (
        f"fixture moved away from the naive-Brier boundary: {briers[0]:.9f}"
    )
    assert max(briers) - min(briers) <= 1e-6, (
        "frozen BBCA-like features produced unstable Brier scores: "
        f"{briers}"
    )
    assert max(xgboost_weights) - min(xgboost_weights) <= 1e-12, (
        "frozen BBCA-like features produced unstable weight assignments: "
        f"briers={briers}, weights={xgboost_weights}"
    )


def test_failed_naive_brier_remains_xgboost_weight_benchmark() -> None:
    """Failed Naive is excluded from blending, not from benchmark comparison."""

    validations = {
        "naive": ValidationSummary(
            horizon_days=10,
            n_observations=30,
            ic_mean=-0.01,
            ic_t_stat=-0.5,
            brier=0.25,
            rmse=0.02,
            directional_accuracy=0.49,
            dsr=0.0,
            bh_q_value_passed=False,
            status="failed",
        ),
        "xgboost": ValidationSummary(
            horizon_days=10,
            n_observations=30,
            ic_mean=0.05,
            ic_t_stat=3.0,
            brier=0.2548577119102416,
            rmse=0.01,
            directional_accuracy=0.70,
            dsr=0.10,
            bh_q_value_passed=True,
            status="production",
        ),
    }

    weights = _model_weights(
        "ensemble",
        validations,
        {"naive": 0.0, "xgboost": 0.0234},
    )

    assert weights["xgboost"] == pytest.approx(0.0), (
        "BBCA XGBoost Brier 0.2548577 is worse than the retained "
        "Naive benchmark 0.25 and must not receive blend weight"
    )
    assert weights.get("naive", 0.0) == pytest.approx(0.0)


def test_maximally_bullish_directional_agents_can_reach_round_one_buy() -> None:
    """The legitimate three bullish agents must not be blocked by a 3/5 ceiling."""

    chamber = object.__new__(DebateChamber)
    votes = chamber._collect_agent_votes(
        {
            "round_count": 1,
            "fundamental_data": "Position: HOLD\nAgent Confidence: 1.00",
            "technical_data": "Position: BUY\nAgent Confidence: 1.00",
            "sentiment_data": "Position: BUY\nAgent Confidence: 1.00",
            "debate_history": [
                DebateMessage(
                    role="bull",
                    content="Position: BUY\nAgent Confidence: 1.00",
                    round_num=1,
                ),
                DebateMessage(
                    role="bear",
                    content="Position: NEUTRAL\nAgent Confidence: 0.00",
                    round_num=1,
                ),
            ],
        }
    )
    result = chamber._evaluate_consensus_votes(votes, round_count=1)
    buy_agents = [str(vote["agent"]) for vote in votes if vote["position"] == "BUY"]
    winner = result.get("consensus_winner") or {}

    assert {str(vote["agent"]) for vote in votes} == {
        "chartist",
        "sentiment_specialist",
        "bull",
        "bear",
    }
    assert buy_agents == ["chartist", "sentiment_specialist", "bull"]
    assert (
        result["consensus_reached"] is True
        and result["consensus_method"] == "voting"
        and winner.get("position") == "BUY"
    ), (
        "maximally bullish eligible agents cannot reach round-one BUY: "
        f"support={len(buy_agents)}/{len(votes)}={len(buy_agents) / len(votes):.0%}, "
        f"threshold={dc.ROUND1_CONSENSUS_THRESHOLD:.0%}, "
        f"votes={[(v['agent'], v['position']) for v in votes]}"
    )


def test_default_barrier_ev_ceiling_is_below_live_buy_floor() -> None:
    """Document why the current standalone live BUY rule is unreachable."""

    expected_ceilings = {5: 0.003, 10: 0.008, 20: 0.018}
    observed: dict[int, float] = {}

    for horizon, expected_ceiling in expected_ceilings.items():
        ev = _compute_ev(
            p_target=1.0,
            p_stop=0.0,
            cio_verdict=None,
            close=100.0,
            horizon=horizon,
        )
        assert ev is not None
        observed[horizon] = ev
        assert ev == pytest.approx(TAU_H[horizon] - TRANSACTION_COST)
        assert ev == pytest.approx(expected_ceiling)
        assert ev < 0.02
        assert _make_decision(1.0, 0.0, ev, 0.03) == "WATCH"

    assert observed == pytest.approx(expected_ceilings)
