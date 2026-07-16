"""ForecastingService - public entry point for the IDX forecasting layer."""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import numpy as np
import pandas as pd

from core.forecasting.dataset import DatasetBuilder, split_forecast_dataset
from core.forecasting.ensemble import (
    BRIER_BORDERLINE_EPSILON,
    blend_votes,
    compute_ensemble_weights,
)
from core.forecasting.labels import TRANSACTION_COST, TAU_H, build_labels
from core.forecasting.models import ModelBase
from core.forecasting.models.naive import NaiveModel
from core.forecasting.models.tgarch import TGARCHForecaster
from core.forecasting.models.xgboost_model import XGBoostForecaster
from core.forecasting.schemas import (
    ForecastReport,
    ForecastStatus,
    ModelVote,
    ValidationSummary,
)
from core.forecasting.validation import (
    batch_bh_correction,
    validate_model,
    walk_forward_splits,
)
from core.settings import settings
from utils.market_snapshot import validate_market_snapshot_integrity
from utils.ticker import normalize_idx_ticker

if TYPE_CHECKING:
    from schemas.debate import CIOVerdict
    from utils.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)

ForecastMode = Literal["ensemble", "tgarch", "naive"]

_DEFAULT_HORIZONS: tuple[int, ...] = (5, 10, 20)
_HISTORY_DAYS: int = 756
_RETURN_LABEL_COLS: frozenset[str] = frozenset(
    {"r_net_h", "y_up", "y_target_hit", "y_stop_hit", "sigma_realized"}
)
_DIRECTIONAL_DISAGREEMENT_PENALTY: float = 0.10
_MAX_DISAGREEMENT_PENALTY: float = 0.35
_DISPERSION_REFERENCE_RETURN: float = 0.05
_LIVE_BUY_EV_FLOOR: float = 0.02

_BLOCKLIST_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "forecast_blocklist.json"
_blocklist_cache: dict[str, list[int]] | None = None


def _load_blocklist() -> dict[str, list[int]]:
    global _blocklist_cache
    if _blocklist_cache is not None:
        return _blocklist_cache
    try:
        data = json.loads(_BLOCKLIST_PATH.read_text(encoding="utf-8"))
        _blocklist_cache = {
            k.upper().removesuffix(".JK"): [int(h) for h in v]
            for k, v in data.items()
            if not k.startswith("_")
        }
    except Exception:
        _blocklist_cache = {}
    return _blocklist_cache


def _is_blocked(ticker: str, horizon: int) -> bool:
    blocked = _load_blocklist()
    return horizon in blocked.get(ticker.upper().removesuffix(".JK"), [])


class ForecastingService:
    """Predict forward-looking ForecastReport for ticker."""

    def __init__(self) -> None:
        self._dataset_builder = DatasetBuilder()
        self._tgarch = TGARCHForecaster()
        self._naive = NaiveModel()
        # Run-scoped pinning: repeated computations reuse the fully materialized
        # ticker + IHSG + point-in-time fundamental feature frame. This avoids
        # same-date provider revisions without persisting stale data globally.
        self._feature_snapshot_cache: dict[tuple[object, ...], pd.DataFrame] = {}

    def _materialize_feature_snapshot(
        self,
        *,
        ticker: str,
        start: date,
        end: date,
        horizon: int,
        execution_snapshot: "MarketSnapshot | None",
        ihsg_snapshot: "MarketSnapshot | None",
    ) -> pd.DataFrame:
        ticker_snapshot_identity = (
            f"{execution_snapshot.snapshot_id}:{execution_snapshot.data_hash}"
            if execution_snapshot is not None
            else "provider_materialized"
        )
        ihsg_snapshot_identity = (
            f"{ihsg_snapshot.snapshot_id}:{ihsg_snapshot.data_hash}"
            if ihsg_snapshot is not None
            else "provider_materialized"
        )
        cache_key: tuple[object, ...] = (
            id(self._dataset_builder),
            ticker,
            start,
            end,
            horizon,
            ticker_snapshot_identity,
            ihsg_snapshot_identity,
        )
        cached = self._feature_snapshot_cache.get(cache_key)
        if cached is not None:
            return cached.copy(deep=True)

        build_kwargs: dict[str, object] = {
            "horizons": (horizon,),
            "include_unlabeled_tail": True,
        }
        if execution_snapshot is not None:
            build_kwargs["snapshots"] = {ticker: execution_snapshot}
        if ihsg_snapshot is not None:
            build_kwargs["ihsg_snapshot"] = ihsg_snapshot
        dataset = self._dataset_builder.build(
            [ticker],
            start,
            end,
            **build_kwargs,
        )
        self._feature_snapshot_cache[cache_key] = dataset.copy(deep=True)
        return dataset.copy(deep=True)

    def _return_model_factories(self) -> dict[str, Callable[[], ModelBase]]:
        return {
            "naive": NaiveModel,
            "xgboost": XGBoostForecaster,
        }

    def predict(
        self,
        ticker: str,
        as_of: date | None = None,
        horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
        mode: ForecastMode = "ensemble",
        cio_verdict: "CIOVerdict | None" = None,
        execution_snapshot: "MarketSnapshot | None" = None,
        *,
        ihsg_snapshot: "MarketSnapshot | None" = None,
        frozen_inputs_only: bool = False,
    ) -> ForecastReport:
        """Produce a ForecastReport for ticker.

        Conservative v1 policy:
        - ensemble uses Naive/XGBoost return forecasts plus TGARCH volatility.
        - naive uses only Naive return forecast plus realized-volatility baseline.
        - tgarch uses TGARCH volatility with zero-drift return baseline.
        LSTM and Prophet are intentionally visible as experimental-unused in ensemble.
        """
        caller_supplied_as_of = as_of is not None
        ticker = normalize_idx_ticker(ticker)
        if frozen_inputs_only and execution_snapshot is None:
            resolved_as_of = as_of or date.today()
            return _error_report(
                ticker,
                resolved_as_of,
                min(horizons),
                ["missing_frozen_ticker_snapshot"],
            )
        if execution_snapshot is not None:
            snapshot_ticker = normalize_idx_ticker(execution_snapshot.ticker)
            if snapshot_ticker != ticker:
                raise ValueError(
                    "Execution snapshot ticker does not match forecast ticker."
                )
        if as_of is None and execution_snapshot is not None:
            as_of = execution_snapshot.last_date or execution_snapshot.requested_end
        strict_feature_as_of = caller_supplied_as_of or execution_snapshot is not None
        as_of = as_of or date.today()
        if execution_snapshot is not None:
            try:
                validate_market_snapshot_integrity(
                    execution_snapshot,
                    expected_ticker=ticker,
                )
            except Exception:
                return _error_report(
                    ticker,
                    as_of,
                    min(horizons),
                    ["ticker_snapshot_integrity_failed"],
                )
        if frozen_inputs_only and execution_snapshot is not None:
            if execution_snapshot.last_date != as_of:
                return _error_report(
                    ticker,
                    as_of,
                    min(horizons),
                    ["ticker_snapshot_as_of_mismatch"],
                )
        if frozen_inputs_only and ihsg_snapshot is None:
            return _error_report(
                ticker,
                as_of,
                min(horizons),
                ["missing_frozen_ihsg_snapshot"],
            )
        if ihsg_snapshot is not None:
            try:
                validate_market_snapshot_integrity(
                    ihsg_snapshot,
                    expected_ticker="IHSG",
                )
            except Exception:
                return _error_report(
                    ticker,
                    as_of,
                    min(horizons),
                    ["ihsg_snapshot_integrity_failed"],
                )
            try:
                ihsg_ticker = normalize_idx_ticker(ihsg_snapshot.ticker)
            except Exception:
                ihsg_ticker = ""
            ihsg_dates = pd.to_datetime(
                ihsg_snapshot.history_copy().index,
                errors="coerce",
            )
            ihsg_has_future_or_invalid = bool(
                ihsg_dates.isna().any()
                or (
                    len(ihsg_dates) > 0
                    and ihsg_dates.max().date() > as_of
                )
            )
            if (
                ihsg_ticker != "IHSG"
                or ihsg_snapshot.requested_end != as_of
                or ihsg_snapshot.last_date != as_of
                or ihsg_has_future_or_invalid
            ):
                return _error_report(
                    ticker,
                    as_of,
                    min(horizons),
                    ["ihsg_snapshot_as_of_mismatch"],
                )
        flags: list[str] = []
        mode = _normalize_mode(mode, flags)

        end = as_of
        start = end - timedelta(days=_HISTORY_DAYS)
        horizon = min(horizons)

        if _is_blocked(ticker, horizon):
            flags.append(f"blocked:forecast_blocklist:h{horizon}")
            return _error_report(ticker, as_of, horizon, flags)

        try:
            dataset = self._materialize_feature_snapshot(
                ticker=ticker,
                start=start,
                end=end,
                horizon=horizon,
                execution_snapshot=execution_snapshot,
                ihsg_snapshot=ihsg_snapshot,
            )
        except Exception as e:
            return _error_report(ticker, as_of, horizon, [f"dataset_error:{type(e).__name__}"])

        if dataset.empty:
            return _error_report(ticker, as_of, horizon, ["insufficient_data"])

        feature_snapshot_hash = _hash_feature_snapshot(dataset)
        try:
            dataset_split = split_forecast_dataset(dataset, horizon=horizon)
        except Exception as e:
            return _error_report(
                ticker,
                as_of,
                horizon,
                [f"dataset_split_error:{type(e).__name__}"],
            )
        if dataset_split.training_features.empty:
            return _error_report(ticker, as_of, horizon, ["insufficient_labeled_data"])
        if dataset_split.inference_features.empty:
            return _error_report(
                ticker,
                as_of,
                horizon,
                ["missing_unlabeled_inference_row"],
            )

        flat_dataset = dataset.reset_index(level="ticker", drop=True).sort_index()
        flat_dataset = flat_dataset[flat_dataset["close"].notna()]
        if flat_dataset.empty:
            return _error_report(ticker, as_of, horizon, ["insufficient_data"])
        if flat_dataset["ocf_price_pct"].isna().all():
            flags.append("ocf_missing")

        try:
            prepared = build_labels(flat_dataset, horizon)
        except Exception as e:
            return _error_report(ticker, as_of, horizon, [f"label_error:{type(e).__name__}"])

        training_index = dataset_split.training_features.reset_index(
            level="ticker", drop=True
        ).index
        inference_index = dataset_split.inference_features.reset_index(
            level="ticker", drop=True
        ).index
        labeled = prepared.loc[prepared.index.intersection(training_index)].dropna(
            subset=["r_net_h"]
        )
        latest = prepared.loc[prepared.index.intersection(inference_index)].tail(1)
        if latest.empty or latest["r_net_h"].notna().any():
            return _error_report(
                ticker,
                as_of,
                horizon,
                ["missing_unlabeled_inference_row"],
            )

        if len(labeled) < 30:
            return _error_report(ticker, as_of, horizon, ["insufficient_labeled_data"])

        feature_frame = _feature_frame(labeled)
        current_features = _feature_frame(latest).reindex(
            columns=feature_frame.columns,
            fill_value=0,
        )
        if current_features.empty:
            return _error_report(ticker, as_of, horizon, ["insufficient_feature_data"])

        feature_as_of = _frame_end_date(current_features)
        training_end_date = _frame_end_date(feature_frame)
        if feature_as_of is None:
            return _error_report(ticker, as_of, horizon, ["invalid_inference_date"])
        if training_end_date is None:
            return _error_report(ticker, as_of, horizon, ["invalid_training_date"])
        if strict_feature_as_of and feature_as_of != as_of:
            return _error_report(ticker, as_of, horizon, ["inference_date_mismatch"])
        if not strict_feature_as_of:
            # An omitted date means "latest complete market session", not an
            # artificial calendar-today row on weekends or holidays.
            as_of = feature_as_of
        if training_end_date >= feature_as_of:
            return _error_report(ticker, as_of, horizon, ["training_inference_overlap"])
        if training_end_date > as_of - timedelta(days=horizon):
            return _error_report(ticker, as_of, horizon, ["training_label_not_known_as_of"])

        close = flat_dataset["close"].dropna()
        close_value = float(close.iloc[-1]) if len(close) else None
        sigma_forecast, vol_fallback, volatility_vote = self._volatility_forecast(
            mode,
            close,
            horizon,
            flags,
        )

        validation_by_model: dict[str, ValidationSummary] = {}
        return_votes: list[ModelVote] = []
        if mode == "tgarch":
            r_hat_net = 0.0
            p_target, p_stop = _compute_probs(r_hat_net, sigma_forecast, horizon, cio_verdict, close_value)
            return_votes.append(
                ModelVote(
                    model_name="tgarch",
                    status="active" if not vol_fallback else "validation_failed",
                    reason=None if not vol_fallback else "volatility_fallback",
                    probability_source="return_volatility_parametric",
                    r_hat_net=r_hat_net,
                    p_target=p_target,
                    p_stop=p_stop,
                    volatility_forecast=sigma_forecast,
                    weight=1.0,
                    validation_passed=not vol_fallback,
                )
            )
            validation_summary = None
        else:
            factories = self._return_model_factories()
            if mode == "naive":
                factories = {"naive": factories["naive"]}
            return_votes, validation_by_model = self._run_return_models(
                factories=factories,
                labeled=labeled,
                feature_frame=feature_frame,
                current_features=current_features,
                sigma_forecast=sigma_forecast,
                horizon=horizon,
                cio_verdict=cio_verdict,
                close_value=close_value,
                mode=mode,
                flags=flags,
            )
            validation_summary = _aggregate_validation(validation_by_model, return_votes)

        if mode == "ensemble":
            return_votes.extend(_experimental_unused_votes())
            flags.extend(["experimental_unused:lstm", "experimental_unused:prophet"])

        if mode == "ensemble" and volatility_vote is not None:
            model_votes = [*return_votes, volatility_vote]
        else:
            model_votes = return_votes

        blended_votes = [vote for vote in return_votes if vote.weight > 0]
        if mode == "tgarch":
            blended_votes = return_votes
        r_hat_net, p_target, p_stop = blend_votes(blended_votes)
        if r_hat_net is None and mode == "tgarch":
            r_hat_net = 0.0
            p_target, p_stop = _compute_probs(r_hat_net, sigma_forecast, horizon, cio_verdict, close_value)

        ev = _compute_ev(p_target, p_stop, cio_verdict, close_value, horizon)
        dispersion, penalty = _model_disagreement_penalty(return_votes)
        if penalty > 0:
            flags.append(f"model_disagreement_penalty:{penalty:.3f}")
        risk_adjusted_ev = _risk_adjusted_ev(ev, penalty, validation_summary, mode)

        forecast_status, failure_reason = _classify_forecast_status(
            mode=mode,
            return_votes=return_votes,
            validations=validation_by_model,
            flags=flags,
            volatility_fallback=vol_fallback,
            sigma_forecast=sigma_forecast,
            r_hat_net=r_hat_net,
            p_target=p_target,
            p_stop=p_stop,
            expected_value=ev,
            risk_adjusted_expected_value=risk_adjusted_ev,
        )

        if mode != "tgarch":
            if validation_summary is None:
                flags.append("validation_status:failed")
            else:
                flags.append(f"validation_status:{validation_summary.status}")

        decision_ev = risk_adjusted_ev if risk_adjusted_ev is not None else ev
        decision = _make_decision(p_target, p_stop, decision_ev, r_hat_net)
        shadow_decision = None
        shadow_buy_ev_floor = None
        shadow_evaluation_only = False
        if settings.FORECAST_SHADOW_EVALUATION_ENABLED:
            shadow_buy_ev_floor = float(settings.FORECAST_SHADOW_BUY_EV_FLOOR)
            shadow_decision = _make_shadow_decision(
                p_target,
                p_stop,
                decision_ev,
                r_hat_net,
                ev_floor=shadow_buy_ev_floor,
            )
            shadow_evaluation_only = True
            flags.append(f"shadow_only:buy_ev_floor={shadow_buy_ev_floor:.4f}")
        confidence = _compute_confidence(p_target, p_stop, decision_ev, validation_summary, penalty)
        probability_source = (
            "return_volatility_parametric"
            if p_target is not None and p_stop is not None
            else "unavailable"
        )
        probability_barriers = (
            _probability_barrier_metadata(horizon, cio_verdict, close_value)
            if probability_source == "return_volatility_parametric"
            else None
        )

        return ForecastReport(
            ticker=ticker.upper(),
            as_of=as_of,
            forecast_as_of=as_of,
            horizon_days=horizon,
            feature_as_of=feature_as_of,
            training_end_date=training_end_date,
            feature_snapshot_hash=feature_snapshot_hash,
            execution_snapshot_id=(
                execution_snapshot.snapshot_id
                if execution_snapshot is not None
                else None
            ),
            execution_snapshot_hash=(
                execution_snapshot.data_hash
                if execution_snapshot is not None
                else None
            ),
            ihsg_snapshot_id=(
                ihsg_snapshot.snapshot_id if ihsg_snapshot is not None else None
            ),
            ihsg_snapshot_hash=(
                ihsg_snapshot.data_hash if ihsg_snapshot is not None else None
            ),
            ihsg_feature_as_of=(
                ihsg_snapshot.last_date if ihsg_snapshot is not None else None
            ),
            feature_close=close_value,
            forecast_status=forecast_status,
            failure_reason=failure_reason,
            expected_return_net=r_hat_net,
            p_target=p_target,
            p_stop=p_stop,
            volatility_forecast=sigma_forecast,
            expected_value=ev,
            decision=decision,
            probability_source=probability_source,
            probability_event=(
                probability_barriers["probability_event"]
                if probability_barriers
                else None
            ),
            probability_barrier_source=(
                probability_barriers["probability_barrier_source"]
                if probability_barriers
                else None
            ),
            probability_reference_close=(
                probability_barriers["probability_reference_close"]
                if probability_barriers
                else None
            ),
            target_barrier_return=(
                probability_barriers["target_barrier_return"]
                if probability_barriers
                else None
            ),
            stop_barrier_return=(
                probability_barriers["stop_barrier_return"]
                if probability_barriers
                else None
            ),
            target_barrier_price=(
                probability_barriers["target_barrier_price"]
                if probability_barriers
                else None
            ),
            stop_barrier_price=(
                probability_barriers["stop_barrier_price"]
                if probability_barriers
                else None
            ),
            shadow_decision=shadow_decision,
            shadow_buy_ev_floor=shadow_buy_ev_floor,
            shadow_evaluation_only=shadow_evaluation_only,
            confidence=confidence,
            model_votes=model_votes,
            validation_summary=validation_summary,
            validation_by_model=validation_by_model,
            model_dispersion=dispersion,
            model_disagreement_penalty=penalty,
            risk_adjusted_expected_value=risk_adjusted_ev,
            data_quality_flags=_dedupe(flags),
            volatility_fallback=vol_fallback,
        )

    def _volatility_forecast(
        self,
        mode: ForecastMode,
        close: pd.Series,
        horizon: int,
        flags: list[str],
    ) -> tuple[float | None, bool, ModelVote | None]:
        if mode == "naive":
            sigma = _realized_volatility(close)
            flags.append("mode_naive_realized_volatility")
            return sigma, False, None

        close_series = pd.Series(close.values, index=close.index)
        tgarch_sigmas, vol_fallback = self._tgarch.predict_volatility(close_series, horizon)
        if vol_fallback:
            flags.append("tgarch_fallback")
        sigma = tgarch_sigmas[0] if tgarch_sigmas else None
        vote = ModelVote(
            model_name="tgarch",
            status="active" if not vol_fallback else "validation_failed",
            reason=None if not vol_fallback else "volatility_fallback",
            volatility_forecast=sigma,
            weight=0.0,
            validation_passed=not vol_fallback,
        )
        return sigma, vol_fallback, vote

    def _run_return_models(
        self,
        *,
        factories: dict[str, Callable[[], ModelBase]],
        labeled: pd.DataFrame,
        feature_frame: pd.DataFrame,
        current_features: pd.DataFrame,
        sigma_forecast: float | None,
        horizon: int,
        cio_verdict: "CIOVerdict | None",
        close_value: float | None,
        mode: ForecastMode,
        flags: list[str],
    ) -> tuple[list[ModelVote], dict[str, ValidationSummary]]:
        predictions: dict[str, float] = {}
        unavailable: dict[str, str] = {}
        validation_failures: dict[str, str] = {}
        validations: dict[str, ValidationSummary] = {}

        splits = walk_forward_splits(labeled, n_splits=5, test_size_days=30) if len(labeled) >= 60 else []
        if not splits:
            flags.append("validation_unavailable")

        for name, factory in factories.items():
            try:
                model = factory()
                model.fit(feature_frame, labeled["r_net_h"].fillna(0))
                pred = _first_prediction(model.predict(current_features))
                if pred is None:
                    raise ValueError("empty_or_nonfinite_prediction")
                predictions[name] = pred
            except Exception as exc:
                logger.warning("[ForecastSvc] %s unavailable: %s", name, exc)
                unavailable[name] = f"{type(exc).__name__}:{exc}"
                flags.append(f"model_unavailable:{name}")
                continue

            if splits:
                try:
                    validations[name] = validate_model(factory(), splits, horizon)
                except Exception as exc:
                    logger.warning("[ForecastSvc] %s validation failed: %s", name, exc)
                    validation_failures[name] = f"{type(exc).__name__}:{exc}"
                    flags.append(f"model_validation_failed:{name}")

        if validations:
            validations = batch_bh_correction(validations)

        weights = _model_weights(mode, validations, predictions, flags=flags)
        if mode != "naive" and predictions and sum(weights.values()) <= 1e-12:
            flags.append("no_validated_return_model")
        votes: list[ModelVote] = []
        for name in factories:
            if name in unavailable:
                votes.append(
                    ModelVote(
                        model_name=name,
                        status="unavailable",
                        reason=unavailable[name],
                        weight=0.0,
                    )
                )
                continue

            pred = predictions.get(name)
            validation = validations.get(name)
            p_target, p_stop = _compute_probs(pred, sigma_forecast, horizon, cio_verdict, close_value)
            if name in validation_failures:
                status, reason = "validation_failed", validation_failures[name]
            else:
                status, reason = _vote_status(validation)
            weight = weights.get(name, 0.0)
            votes.append(
                ModelVote(
                    model_name=name,
                    status=status,
                    reason=reason,
                    probability_source=(
                        "return_volatility_parametric"
                        if p_target is not None and p_stop is not None
                        else "unavailable"
                    ),
                    r_hat_net=pred,
                    p_target=p_target,
                    p_stop=p_stop,
                    volatility_forecast=sigma_forecast,
                    weight=weight,
                    validation_passed=validation.status in {"production", "research_only"}
                    if validation
                    else False,
                    ic=validation.ic_mean if validation else None,
                    brier_target=validation.brier if validation else None,
                    rmse=validation.rmse if validation else None,
                    mae=validation.mae if validation else None,
                    mape=validation.mape if validation else None,
                    directional_accuracy=validation.directional_accuracy if validation else None,
                )
            )
        return votes, validations


def _feature_frame(labeled: pd.DataFrame) -> pd.DataFrame:
    feature_cols = [c for c in labeled.columns if c not in _RETURN_LABEL_COLS]
    return labeled[feature_cols].select_dtypes(include=[np.number]).fillna(0)


def _hash_feature_snapshot(dataset: pd.DataFrame) -> str:
    """Return a stable hash for the exact materialized feature snapshot."""
    column_payload = "\x1f".join(str(column) for column in dataset.columns).encode(
        "utf-8"
    )
    row_payload = pd.util.hash_pandas_object(dataset, index=True).to_numpy().tobytes()
    return hashlib.sha256(column_payload + b"\x00" + row_payload).hexdigest()


def _frame_end_date(frame: pd.DataFrame) -> date | None:
    """Return the final feature index as a date for provenance checks."""
    if frame.empty:
        return None
    try:
        value = frame.index[-1]
        if isinstance(value, tuple):
            value = value[-1]
        return pd.Timestamp(value).date()
    except (OverflowError, TypeError, ValueError):
        return None


def _normalize_mode(value: str, flags: list[str]) -> ForecastMode:
    mode = str(value or "ensemble").lower()
    if mode in {"ensemble", "tgarch", "naive"}:
        return cast(ForecastMode, mode)
    flags.append(f"invalid_mode:{mode}")
    return "ensemble"


def _realized_volatility(close: pd.Series) -> float | None:
    returns = close.astype(float).pct_change().dropna()
    if len(returns) < 5:
        return None
    sigma = float(returns.tail(60).std() * math.sqrt(252.0))
    return sigma if math.isfinite(sigma) and sigma > 0 else None


def _first_prediction(values: np.ndarray) -> float | None:
    if len(values) == 0:
        return None
    pred = float(values[0])
    return pred if math.isfinite(pred) else None


def _model_weights(
    mode: ForecastMode,
    validations: dict[str, ValidationSummary],
    predictions: dict[str, float],
    *,
    flags: list[str] | None = None,
) -> dict[str, float]:
    if mode == "naive":
        return {"naive": 1.0 if "naive" in predictions else 0.0}
    if not predictions:
        return {}
    scores = {
        name: {
            "ic": validation.ic_mean,
            "rmse": validation.rmse,
            "brier": validation.brier,
            "dsr": validation.dsr,
            "bh_passed": validation.bh_q_value_passed,
            "dir_acc": validation.directional_accuracy,
        }
        for name, validation in validations.items()
        if name in predictions
        and validation.status in {"production", "research_only"}
    }
    naive_validation = validations.get("naive")
    naive_brier_benchmark = (
        naive_validation.brier
        if naive_validation is not None
        and naive_validation.brier is not None
        and math.isfinite(float(naive_validation.brier))
        else None
    )
    if naive_brier_benchmark is not None:
        for name, validation in validations.items():
            if name == "naive" or name not in predictions or validation.brier is None:
                continue
            brier_delta = float(validation.brier) - float(naive_brier_benchmark)
            if abs(brier_delta) <= BRIER_BORDERLINE_EPSILON and flags is not None:
                flags.append(
                    f"brier_borderline:{name}:delta={brier_delta:+.6f}:"
                    f"epsilon={BRIER_BORDERLINE_EPSILON:.4f}"
                )
    weights = (
        compute_ensemble_weights(
            scores,
            naive_brier_benchmark=naive_brier_benchmark,
        )
        if scores
        else {}
    )
    if sum(weights.values()) > 1e-12:
        return weights
    return {name: 0.0 for name in predictions}


def _vote_status(validation: ValidationSummary | None) -> tuple[str, str | None]:
    if validation is None:
        return "not_validated", "validation_unavailable"
    if validation.status == "failed":
        return "validation_failed", "validation_status:failed"
    return "active", None


def _classify_forecast_status(
    *,
    mode: ForecastMode,
    return_votes: list[ModelVote],
    validations: dict[str, ValidationSummary],
    flags: list[str],
    volatility_fallback: bool,
    sigma_forecast: float | None,
    r_hat_net: float | None,
    p_target: float | None,
    p_stop: float | None,
    expected_value: float | None,
    risk_adjusted_expected_value: float | None,
) -> tuple[ForecastStatus, str | None]:
    """Classify forecast readiness without inferring it from nullable values."""

    def complete(*values: float | None) -> bool:
        return all(value is not None and math.isfinite(float(value)) for value in values)

    if mode == "tgarch":
        if volatility_fallback:
            return "MODEL_FAILED", "tgarch_volatility_model_failed"
        if complete(sigma_forecast, r_hat_net, p_target, p_stop, expected_value):
            return "READY", None
        return "UNAVAILABLE", "forecast_output_unavailable"

    available_votes = [
        vote
        for vote in return_votes
        if vote.status != "unavailable"
        and vote.r_hat_net is not None
        and math.isfinite(float(vote.r_hat_net))
    ]
    if not available_votes:
        return "MODEL_FAILED", "all_return_models_unavailable"

    validation_runtime_failed = any(
        str(flag).startswith("model_validation_failed:") for flag in flags
    )
    if not validations:
        if validation_runtime_failed:
            return "VALIDATION_FAILED", "walk_forward_validation_failed"
        return "NOT_VALIDATED", "walk_forward_validation_unavailable"

    validated_votes = [
        vote
        for vote in available_votes
        if vote.weight > 1e-12
        and vote.validation_passed
        and validations.get(vote.model_name) is not None
        and validations[vote.model_name].status in {"production", "research_only"}
    ]
    if not validated_votes:
        if all(validation.status == "failed" for validation in validations.values()):
            return "VALIDATION_FAILED", "all_return_models_failed_validation"
        return "ZERO_WEIGHT", "all_validated_return_models_disqualified"

    if not complete(
        sigma_forecast,
        r_hat_net,
        p_target,
        p_stop,
        expected_value,
        risk_adjusted_expected_value,
    ):
        return "UNAVAILABLE", "forecast_output_unavailable"
    return "READY", None


def _aggregate_validation(
    validations: dict[str, ValidationSummary],
    votes: list[ModelVote],
) -> ValidationSummary | None:
    if not validations:
        return None
    ordered = list(validations.values())
    weights_by_name = {vote.model_name: max(0.0, float(vote.weight or 0.0)) for vote in votes}
    total_weight = sum(weights_by_name.get(name, 0.0) for name in validations)
    active_validations = [
        validation
        for name, validation in validations.items()
        if weights_by_name.get(name, 0.0) > 1e-12
    ]

    def metric(name: str) -> float | None:
        values = []
        for model_name, validation in validations.items():
            value = getattr(validation, name)
            if value is None:
                continue
            weight = weights_by_name.get(model_name, 0.0)
            values.append((float(value), weight))
        if not values:
            return None
        if total_weight > 1e-12:
            used = [(value, weight) for value, weight in values if weight > 0]
            if not used:
                return None
            return sum(value * weight for value, weight in used) / sum(weight for _, weight in used)
        return sum(value for value, _ in values) / len(values)

    if total_weight <= 1e-12:
        status: str = "failed"
    elif any(v.status == "production" for v in active_validations):
        status = "production"
    elif any(v.status == "research_only" for v in active_validations):
        status = "research_only"
    else:
        status = "failed"

    return ValidationSummary(
        horizon_days=ordered[0].horizon_days,
        n_observations=max(
            v.n_observations for v in (active_validations or ordered)
        ),
        ic_mean=metric("ic_mean"),
        ic_t_stat=metric("ic_t_stat"),
        brier=metric("brier"),
        rmse=metric("rmse"),
        mae=metric("mae"),
        mape=metric("mape"),
        directional_accuracy=metric("directional_accuracy"),
        dsr=metric("dsr"),
        bh_q_value_passed=(
            any(v.bh_q_value_passed for v in active_validations)
            if active_validations
            else False
        ),
        status=status,  # type: ignore[arg-type]
    )


def _experimental_unused_votes() -> list[ModelVote]:
    return [
        ModelVote(
            model_name="lstm",
            status="experimental_unused",
            reason="conservative_policy",
            weight=0.0,
        ),
        ModelVote(
            model_name="prophet",
            status="experimental_unused",
            reason="conservative_policy_not_primary_return_model",
            weight=0.0,
        ),
    ]


def _model_disagreement_penalty(votes: list[ModelVote]) -> tuple[float | None, float]:
    predictions = [
        float(vote.r_hat_net)
        for vote in votes
        if vote.status in {"active", "validation_failed"}
        and vote.r_hat_net is not None
        and math.isfinite(float(vote.r_hat_net))
    ]
    if len(predictions) < 2:
        return None, 0.0
    dispersion = float(np.std(predictions))
    scale_penalty = min(0.20, (dispersion / _DISPERSION_REFERENCE_RETURN) * 0.10)
    signs = {1 if value > 1e-6 else -1 if value < -1e-6 else 0 for value in predictions}
    signs.discard(0)
    direction_penalty = _DIRECTIONAL_DISAGREEMENT_PENALTY if len(signs) > 1 else 0.0
    penalty = min(_MAX_DISAGREEMENT_PENALTY, scale_penalty + direction_penalty)
    return dispersion, round(penalty, 4)


def _risk_adjusted_ev(
    ev: float | None,
    penalty: float,
    validation: ValidationSummary | None,
    mode: ForecastMode,
) -> float | None:
    if ev is None or validation is None or validation.status == "failed" or mode == "tgarch":
        return None
    if ev <= 0:
        return ev
    return ev * (1.0 - penalty)


def _compute_probs(
    r_hat: float | None,
    sigma: float | None,
    horizon: int,
    cio_verdict: "CIOVerdict | None",
    close: float | None,
) -> tuple[float | None, float | None]:
    """Log-normal terminal probability using an already-H-day return forecast."""
    if sigma is None or sigma <= 0:
        return None, None

    h_frac = horizon / 252.0
    # r_hat is trained against labels.r_net_h and is already an H-day return.
    # Only the annualized volatility needs sqrt(H/252) conversion.
    drift_h = r_hat or 0.0
    sigma_h = sigma * math.sqrt(h_frac)

    if sigma_h < 1e-10:
        return None, None

    barriers = _probability_barrier_metadata(horizon, cio_verdict, close)
    if barriers is None:
        return None, None
    G = float(barriers["target_barrier_return"])
    L = float(barriers["stop_barrier_return"])

    if G <= 0 or L <= 0:
        return None, None

    try:
        from scipy.stats import norm as _norm  # noqa: PLC0415
        ln_target = math.log(1 + G)
        ln_stop = math.log(1 - L) if L < 1 else float("-inf")

        p_target = float(1 - _norm.cdf((ln_target - drift_h) / sigma_h))
        p_stop = (
            float(_norm.cdf((ln_stop - drift_h) / sigma_h))
            if math.isfinite(ln_stop)
            else 0.0
        )

        p_target = max(0.0, min(1.0, p_target))
        p_stop = max(0.0, min(1.0, p_stop))
        return p_target, p_stop
    except Exception as e:
        logger.warning("[ForecastSvc] _compute_probs failed: %s", e)
        return None, None


def _probability_barrier_metadata(
    horizon: int,
    cio_verdict: "CIOVerdict | None",
    close: float | None,
) -> dict[str, float | str] | None:
    """Return the exact terminal-event barriers used by ``_compute_probs``.

    Persisting these values makes later shadow Brier scoring reproducible.  It
    does not change probability math or any live decision threshold.
    """

    if close is None or not math.isfinite(float(close)) or float(close) <= 0:
        return None
    reference = float(close)
    source = "default_horizon"
    target_price: float | None = None
    stop_price: float | None = None
    if cio_verdict is not None:
        target = cio_verdict.target_price
        stop = cio_verdict.stop_loss
        if target and stop and target > 0 and stop > 0 and stop < reference < target:
            target_price = float(target)
            stop_price = float(stop)
            source = "cio_trade_levels"

    if target_price is None or stop_price is None:
        tau = float(TAU_H.get(horizon, 0.015))
        target_price = reference * (1.0 + tau)
        stop_price = reference * (1.0 - tau)

    target_return = (target_price - reference) / reference
    stop_return = (reference - stop_price) / reference
    if target_return <= 0 or stop_return <= 0:
        return None
    return {
        "probability_event": "terminal",
        "probability_barrier_source": source,
        "probability_reference_close": reference,
        "target_barrier_return": target_return,
        "stop_barrier_return": stop_return,
        "target_barrier_price": target_price,
        "stop_barrier_price": stop_price,
    }

def _compute_ev(
    p_target: float | None,
    p_stop: float | None,
    cio_verdict: "CIOVerdict | None",
    close,
    horizon: int,
) -> float | None:
    if p_target is None or p_stop is None:
        return None

    close_val = float(close.iloc[-1]) if hasattr(close, "iloc") else (float(close) if close else None)
    G = L = TAU_H.get(horizon, 0.015)

    if cio_verdict is not None and close_val is not None and close_val > 0:
        if cio_verdict.target_price and cio_verdict.stop_loss:
            if cio_verdict.stop_loss < close_val < cio_verdict.target_price:
                G = (cio_verdict.target_price - close_val) / close_val
                L = (close_val - cio_verdict.stop_loss) / close_val

    cost = TRANSACTION_COST
    ev = p_target * G - p_stop * L - cost
    return float(ev)


def _make_decision(
    p_target: float | None,
    p_stop: float | None,
    ev: float | None,
    r_hat_net: float | None,
) -> Literal["BUY", "WATCH", "AVOID"]:
    """Live forecast decision; its 2% EV floor is intentionally unchanged."""
    return _decision_with_ev_floor(
        p_target,
        p_stop,
        ev,
        r_hat_net,
        ev_floor=_LIVE_BUY_EV_FLOOR,
    )


def _make_shadow_decision(
    p_target: float | None,
    p_stop: float | None,
    ev: float | None,
    r_hat_net: float | None,
    *,
    ev_floor: float,
) -> Literal["BUY", "WATCH", "AVOID"]:
    """Calibration-only decision that is never consumed by live execution."""
    return _decision_with_ev_floor(
        p_target,
        p_stop,
        ev,
        r_hat_net,
        ev_floor=ev_floor,
    )


def _decision_with_ev_floor(
    p_target: float | None,
    p_stop: float | None,
    ev: float | None,
    r_hat_net: float | None,
    *,
    ev_floor: float,
) -> Literal["BUY", "WATCH", "AVOID"]:
    if any(v is None for v in [p_target, p_stop, ev, r_hat_net]):
        return "AVOID"

    buy = (
        p_target >= 0.55  # type: ignore[operator]
        and (p_target - p_stop) >= 0.15  # type: ignore[operator]
        and ev >= ev_floor  # type: ignore[operator]
        and r_hat_net >= 0.015  # type: ignore[operator]
    )
    if buy:
        return "BUY"

    if ev > 0:
        return "WATCH"

    return "AVOID"


def _compute_confidence(
    p_target: float | None,
    p_stop: float | None,
    ev: float | None,
    validation: ValidationSummary | None,
    disagreement_penalty: float = 0.0,
) -> float | None:
    if p_target is None:
        return None
    base = max(0.0, min(1.0, p_target - (p_stop or 0.0)))
    if validation and validation.ic_mean is not None:
        ic_boost = max(0.0, min(0.1, validation.ic_mean))
        base = min(1.0, base + ic_boost)
    base = max(0.0, base * (1.0 - max(0.0, min(disagreement_penalty, 1.0))))
    return round(base, 4)


def _error_report(
    ticker: str,
    as_of: date,
    horizon: int,
    flags: list[str],
) -> ForecastReport:
    return ForecastReport(
        ticker=ticker.upper(),
        as_of=as_of,
        forecast_as_of=as_of,
        horizon_days=horizon,
        forecast_status="UNAVAILABLE",
        failure_reason=flags[0] if flags else "forecast_unavailable",
        data_quality_flags=flags,
        decision="AVOID",
    )


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
