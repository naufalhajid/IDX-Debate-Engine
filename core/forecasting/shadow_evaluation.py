"""Offline, evaluation-only outcome tracking for advisory signal packets.

This module deliberately has no dependency on the orchestrator, debate chamber,
risk governor, sizing, or order paths.  It consumes persisted run artifacts and
hash-verified market snapshots, then writes an isolated shadow-calibration
ledger.  It never recomputes a signal or forecast and never grants execution
authority.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Literal, Mapping, Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from core.forecasting.labels import TRANSACTION_COST
from core.forecasting.validation import compute_ic
from schemas.debate import SignalPacket
from utils.market_snapshot import MarketSnapshot, load_market_snapshot
from utils.ticker import normalize_idx_ticker, resolve_within_root


SHADOW_EVALUATION_CONTRACT_VERSION = "shadow-evaluation-v1"
DEFAULT_SHADOW_HORIZONS: tuple[int, ...] = (5, 10, 20)
_LOG_TRANSACTION_COST = math.log1p(TRANSACTION_COST)


class ShadowProvenanceError(ValueError):
    """Persisted signal inputs do not satisfy the point-in-time contract."""


class ShadowSignalObservation(BaseModel):
    """Immutable observation copied from one persisted pipeline result."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["shadow-evaluation-v1"] = (
        SHADOW_EVALUATION_CONTRACT_VERSION
    )
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    observation_id: str
    run_id: str
    ticker: str
    signal_as_of: date
    signal_price: float = Field(gt=0)
    source_snapshot_id: str
    source_data_hash: str
    source_snapshot_last_date: date
    signal_packet: SignalPacket
    setup_status: str
    setup_reason_code: str | None = None
    observed_execution_eligible: bool | None = None
    observed_execution_status: str | None = None
    observed_actionable: bool | None = None
    observed_verdict_rating: str | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    forecast_report: dict[str, Any] | None = None

    @property
    def key(self) -> tuple[str, str, date]:
        return self.run_id, self.ticker, self.signal_as_of


class ShadowHorizonOutcome(BaseModel):
    """One fixed-horizon outcome; this schema cannot publish a trade action."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["shadow-evaluation-v1"] = (
        SHADOW_EVALUATION_CONTRACT_VERSION
    )
    evaluation_only: Literal[True] = True
    live_authority: Literal[False] = False
    outcome_id: str
    observation_id: str
    run_id: str
    ticker: str
    signal_as_of: date
    horizon: int = Field(gt=0)
    status: Literal["MATURE", "PENDING", "INVALID"]
    reason: str
    evaluation_as_of: date
    bars_available: int = Field(ge=0)
    maturity_date: date | None = None
    reference_close: float = Field(gt=0)
    horizon_close: float | None = None
    forward_return_gross: float | None = None
    realized_return_net: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    target_hit: bool | None = None
    stop_hit: bool | None = None
    first_touch: Literal["TARGET", "STOP"] | None = None
    same_bar_ambiguous: bool | None = None
    terminal_target_event: bool | None = None
    terminal_stop_event: bool | None = None
    forecast_score_status: Literal["READY", "SKIPPED", "PENDING", "NOT_APPLICABLE"]
    forecast_score_reason: str
    predicted_return_net: float | None = None
    p_target: float | None = None
    p_stop: float | None = None
    signal_packet: SignalPacket
    setup_status: str
    setup_reason_code: str | None = None
    observed_execution_eligible: bool | None = None

    @property
    def key(self) -> tuple[str, str, date, int]:
        return self.run_id, self.ticker, self.signal_as_of, self.horizon


class ShadowSourceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    status: Literal["INVALID"] = "INVALID"
    reason: str


def extract_shadow_observation(
    result: Mapping[str, Any],
    *,
    signal_as_of: date,
    validate_snapshot_ticker: bool = True,
) -> ShadowSignalObservation:
    """Copy and validate one stored signal without recomputing any feature."""

    ticker = normalize_idx_ticker(str(result.get("ticker") or ""))
    metadata = _mapping(result.get("metadata"), "metadata")
    packet_payload = metadata.get("signal_packet")
    if not isinstance(packet_payload, Mapping):
        raise ShadowProvenanceError("missing or malformed signal_packet")
    try:
        signal_packet = SignalPacket.model_validate(dict(packet_payload))
    except Exception as exc:
        raise ShadowProvenanceError(f"malformed signal_packet: {exc}") from exc

    provenance = _mapping(metadata.get("market_snapshot"), "market_snapshot")
    source_last_date = _parse_date(
        provenance.get("last_date"), "market_snapshot.last_date"
    )
    if source_last_date != signal_as_of:
        raise ShadowProvenanceError(
            "market_snapshot.last_date must equal signal_as_of"
        )
    source_ticker = str(provenance.get("ticker") or "").upper()
    if validate_snapshot_ticker and source_ticker != ticker:
        raise ShadowProvenanceError("market_snapshot ticker mismatch")
    snapshot_id = str(provenance.get("snapshot_id") or "").strip()
    data_hash = str(provenance.get("data_hash") or "").strip()
    if not snapshot_id or not data_hash:
        raise ShadowProvenanceError("market_snapshot identity/hash is missing")
    if validate_snapshot_ticker and not snapshot_id.startswith(f"{ticker}-"):
        raise ShadowProvenanceError("market_snapshot snapshot_id ticker mismatch")

    current_price_as_of = _parse_date(
        metadata.get("current_price_as_of"), "metadata.current_price_as_of"
    )
    if current_price_as_of != signal_as_of:
        raise ShadowProvenanceError(
            "metadata.current_price_as_of must equal signal_as_of"
        )

    setup = _mapping(
        metadata.get("trade_setup_snapshot"), "trade_setup_snapshot"
    )
    technicals = setup.get("technical_indicators")
    technicals = dict(technicals) if isinstance(technicals, Mapping) else {}
    verdict = result.get("verdict")
    verdict = dict(verdict) if isinstance(verdict, Mapping) else {}
    signal_price = _first_finite_positive(
        technicals.get("current_price"),
        verdict.get("current_price"),
    )
    if signal_price is None:
        raise ShadowProvenanceError("signal reference price is missing")

    levels = setup.get("envelope")
    if not isinstance(levels, Mapping):
        levels = setup.get("hypothetical_envelope")
    levels = dict(levels) if isinstance(levels, Mapping) else {}
    target_price = _first_finite_positive(
        levels.get("target_price"), verdict.get("target_price")
    )
    stop_loss = _first_finite_positive(
        levels.get("stop_loss"), verdict.get("stop_loss")
    )

    forecast_payload = result.get("forecast_report")
    forecast_report: dict[str, Any] | None = None
    if forecast_payload is not None:
        if not isinstance(forecast_payload, Mapping):
            raise ShadowProvenanceError("forecast_report is malformed")
        forecast_report = json.loads(json.dumps(dict(forecast_payload), default=str))
        _validate_forecast_provenance(
            forecast_report,
            ticker,
            signal_as_of,
            signal_price=signal_price,
            source_snapshot_id=snapshot_id,
            source_data_hash=data_hash,
        )

    run_id = str(metadata.get("run_id") or "").strip()
    if not run_id:
        raise ShadowProvenanceError("metadata.run_id is missing")
    identity = "|".join(
        (run_id, ticker, signal_as_of.isoformat(), snapshot_id, data_hash)
    )
    observation_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    setup_status = str(setup.get("status") or "UNKNOWN").upper()
    reason_code = str(setup.get("reason_code") or "").strip() or None

    return ShadowSignalObservation(
        observation_id=observation_id,
        run_id=run_id,
        ticker=ticker,
        signal_as_of=signal_as_of,
        signal_price=signal_price,
        source_snapshot_id=snapshot_id,
        source_data_hash=data_hash,
        source_snapshot_last_date=source_last_date,
        signal_packet=signal_packet,
        setup_status=setup_status,
        setup_reason_code=reason_code,
        observed_execution_eligible=signal_packet.execution_eligible,
        observed_execution_status=(
            str(result.get("execution_status"))
            if result.get("execution_status") is not None
            else None
        ),
        observed_actionable=(
            bool(result.get("actionable"))
            if result.get("actionable") is not None
            else None
        ),
        observed_verdict_rating=(
            str(verdict.get("rating")) if verdict.get("rating") is not None else None
        ),
        target_price=target_price,
        stop_loss=stop_loss,
        forecast_report=forecast_report,
    )


def evaluate_shadow_observation(
    observation: ShadowSignalObservation,
    outcome_snapshot: MarketSnapshot,
    *,
    horizon: int,
    evaluation_as_of: date,
    validate_snapshot_ticker: bool = True,
) -> ShadowHorizonOutcome:
    """Evaluate exactly H observed sessions after the point-in-time signal."""

    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if evaluation_as_of < observation.signal_as_of:
        return _invalid_outcome(
            observation,
            horizon,
            evaluation_as_of,
            "evaluation_as_of_before_signal",
        )
    if validate_snapshot_ticker and normalize_idx_ticker(outcome_snapshot.ticker) != (
        observation.ticker
    ):
        return _invalid_outcome(
            observation, horizon, evaluation_as_of, "outcome_snapshot_ticker_mismatch"
        )

    history = outcome_snapshot.history_copy()
    history.index = pd.to_datetime(history.index, errors="coerce").normalize()
    history = history[~history.index.isna()].sort_index(kind="stable")
    history = history[~history.index.duplicated(keep="last")]
    signal_rows = history[history.index.date == observation.signal_as_of]
    if signal_rows.empty:
        return _invalid_outcome(
            observation,
            horizon,
            evaluation_as_of,
            "signal_date_missing_from_outcome_snapshot",
        )
    snapshot_signal_close = _finite(signal_rows.iloc[-1].get("Close"))
    if snapshot_signal_close is None or not math.isclose(
        snapshot_signal_close,
        observation.signal_price,
        rel_tol=1e-6,
        abs_tol=1e-6,
    ):
        return _invalid_outcome(
            observation,
            horizon,
            evaluation_as_of,
            "signal_close_mismatch",
        )

    eligible = history[
        (history.index.date > observation.signal_as_of)
        & (history.index.date <= evaluation_as_of)
    ]
    bounded = eligible.iloc[:horizon]
    bars_available = len(bounded)
    outcome_id = _outcome_id(observation, horizon)
    if bars_available < horizon:
        effective_cutoff = (
            bounded.index[-1].date() if bars_available else evaluation_as_of
        )
        return ShadowHorizonOutcome(
            outcome_id=outcome_id,
            observation_id=observation.observation_id,
            run_id=observation.run_id,
            ticker=observation.ticker,
            signal_as_of=observation.signal_as_of,
            horizon=horizon,
            status="PENDING",
            reason="insufficient_observed_sessions",
            evaluation_as_of=effective_cutoff,
            bars_available=bars_available,
            reference_close=observation.signal_price,
            forecast_score_status="PENDING",
            forecast_score_reason="insufficient_horizon",
            signal_packet=observation.signal_packet,
            setup_status=observation.setup_status,
            setup_reason_code=observation.setup_reason_code,
            observed_execution_eligible=observation.observed_execution_eligible,
        )

    maturity_date = bounded.index[horizon - 1].date()
    horizon_close = _finite(bounded.iloc[horizon - 1].get("Close"))
    highs = [_finite(value) for value in bounded.get("High", pd.Series(dtype=float))]
    lows = [_finite(value) for value in bounded.get("Low", pd.Series(dtype=float))]
    if horizon_close is None or any(value is None for value in highs + lows):
        return _invalid_outcome(
            observation,
            horizon,
            maturity_date,
            "malformed_outcome_bars",
            bars_available=bars_available,
        )

    reference = observation.signal_price
    target_hit: bool | None = None
    stop_hit: bool | None = None
    first_touch: Literal["TARGET", "STOP"] | None = None
    same_bar_ambiguous: bool | None = None
    if observation.target_price is not None or observation.stop_loss is not None:
        target_hit = False if observation.target_price is not None else None
        stop_hit = False if observation.stop_loss is not None else None
        same_bar_ambiguous = False
        for _, row in bounded.iterrows():
            high = float(row["High"])
            low = float(row["Low"])
            target_now = bool(
                observation.target_price is not None
                and high >= observation.target_price
            )
            stop_now = bool(
                observation.stop_loss is not None and low <= observation.stop_loss
            )
            if target_now:
                target_hit = True
            if stop_now:
                stop_hit = True
            if first_touch is None:
                if stop_now:
                    # Existing project policy is conservative when both touch
                    # within one OHLC bar: stop wins because intraday order is
                    # unknowable from daily data.
                    first_touch = "STOP"
                    same_bar_ambiguous = target_now
                elif target_now:
                    first_touch = "TARGET"

    forward_return = horizon_close / reference - 1.0
    realized_net = math.log(horizon_close / reference) - _LOG_TRANSACTION_COST
    mfe = max(float(value) for value in highs if value is not None) / reference - 1.0
    mae = min(float(value) for value in lows if value is not None) / reference - 1.0
    forecast = _score_forecast(observation, horizon, horizon_close)

    return ShadowHorizonOutcome(
        outcome_id=outcome_id,
        observation_id=observation.observation_id,
        run_id=observation.run_id,
        ticker=observation.ticker,
        signal_as_of=observation.signal_as_of,
        horizon=horizon,
        status="MATURE",
        reason="horizon_complete",
        evaluation_as_of=maturity_date,
        bars_available=horizon,
        maturity_date=maturity_date,
        reference_close=reference,
        horizon_close=horizon_close,
        forward_return_gross=forward_return,
        realized_return_net=realized_net,
        mfe_pct=mfe,
        mae_pct=mae,
        target_hit=target_hit,
        stop_hit=stop_hit,
        first_touch=first_touch,
        same_bar_ambiguous=same_bar_ambiguous,
        terminal_target_event=forecast["terminal_target_event"],
        terminal_stop_event=forecast["terminal_stop_event"],
        forecast_score_status=forecast["status"],
        forecast_score_reason=forecast["reason"],
        predicted_return_net=forecast["predicted_return_net"],
        p_target=forecast["p_target"],
        p_stop=forecast["p_stop"],
        signal_packet=observation.signal_packet,
        setup_status=observation.setup_status,
        setup_reason_code=observation.setup_reason_code,
        observed_execution_eligible=observation.observed_execution_eligible,
    )


def compute_shadow_metrics(
    observations: Sequence[ShadowSignalObservation],
    outcomes: Sequence[ShadowHorizonOutcome],
    *,
    source_invalid: int = 0,
) -> dict[str, Any]:
    """Aggregate coverage and calibration with explicit per-metric denominators."""

    unique_observations = {observation.observation_id: observation for observation in observations}
    deduped_outcomes = merge_shadow_outcomes([], outcomes)
    cohorts = {
        "signal_lean": dict(
            sorted(
                Counter(
                    observation.signal_packet.signal_lean
                    for observation in unique_observations.values()
                ).items()
            )
        ),
        "setup_status": dict(
            sorted(
                Counter(
                    observation.setup_status
                    for observation in unique_observations.values()
                ).items()
            )
        ),
        "rejection_reason": dict(
            sorted(
                Counter(
                    observation.signal_packet.execution_rejection_reason or "NONE"
                    for observation in unique_observations.values()
                ).items()
            )
        ),
        "forecast_status": dict(
            sorted(
                Counter(
                    str((observation.forecast_report or {}).get("forecast_status") or "MISSING")
                    for observation in unique_observations.values()
                ).items()
            )
        ),
    }

    by_horizon: dict[str, dict[str, Any]] = {}
    for horizon in sorted({outcome.horizon for outcome in deduped_outcomes}):
        records = [outcome for outcome in deduped_outcomes if outcome.horizon == horizon]
        mature = [outcome for outcome in records if outcome.status == "MATURE"]
        pending = [outcome for outcome in records if outcome.status == "PENDING"]
        invalid = [outcome for outcome in records if outcome.status == "INVALID"]
        actuals = [
            float(outcome.realized_return_net)
            for outcome in mature
            if outcome.realized_return_net is not None
        ]
        gross_returns = [
            float(outcome.forward_return_gross)
            for outcome in mature
            if outcome.forward_return_gross is not None
        ]
        mae_values = [float(outcome.mae_pct) for outcome in mature if outcome.mae_pct is not None]
        mfe_values = [float(outcome.mfe_pct) for outcome in mature if outcome.mfe_pct is not None]

        return_pairs = [
            (float(outcome.predicted_return_net), float(outcome.realized_return_net))
            for outcome in mature
            if outcome.forecast_score_status == "READY"
            and outcome.predicted_return_net is not None
            and outcome.realized_return_net is not None
        ]
        return_errors = [predicted - actual for predicted, actual in return_pairs]
        predicted_values = [predicted for predicted, _ in return_pairs]
        realized_values = [actual for _, actual in return_pairs]
        ic = compute_ic(np.asarray(realized_values), np.asarray(predicted_values))
        ic_value = float(ic) if math.isfinite(float(ic)) else None

        target_pairs = [
            (float(outcome.p_target), float(bool(outcome.terminal_target_event)))
            for outcome in mature
            if outcome.forecast_score_status == "READY"
            and outcome.p_target is not None
            and outcome.terminal_target_event is not None
        ]
        stop_pairs = [
            (float(outcome.p_stop), float(bool(outcome.terminal_stop_event)))
            for outcome in mature
            if outcome.forecast_score_status == "READY"
            and outcome.p_stop is not None
            and outcome.terminal_stop_event is not None
        ]
        forecast_ready = [
            outcome for outcome in mature if outcome.forecast_score_status == "READY"
        ]
        by_horizon[str(horizon)] = {
            "total_ingested": len(records),
            "mature": len(mature),
            "pending": len(pending),
            "invalid": len(invalid),
            "signal_scored_n": len(actuals),
            "forecast_ready_n": len(forecast_ready),
            "avg_forward_return_gross": _mean_or_none(gross_returns),
            "median_forward_return_gross": (
                float(median(gross_returns)) if gross_returns else None
            ),
            "positive_return_rate": _mean_or_none(
                [float(value > 0.0) for value in gross_returns]
            ),
            "target_hit_n": sum(outcome.target_hit is not None for outcome in mature),
            "target_hit_rate": _mean_or_none(
                [float(bool(outcome.target_hit)) for outcome in mature if outcome.target_hit is not None]
            ),
            "stop_hit_n": sum(outcome.stop_hit is not None for outcome in mature),
            "stop_hit_rate": _mean_or_none(
                [float(bool(outcome.stop_hit)) for outcome in mature if outcome.stop_hit is not None]
            ),
            "mae_path_n": len(mae_values),
            "avg_mae_pct": _mean_or_none(mae_values),
            "mfe_path_n": len(mfe_values),
            "avg_mfe_pct": _mean_or_none(mfe_values),
            "return_error_n": len(return_errors),
            "mae": _mean_or_none([abs(value) for value in return_errors]),
            "rmse": (
                math.sqrt(_mean_or_none([value * value for value in return_errors]) or 0.0)
                if return_errors
                else None
            ),
            "directional_accuracy": _mean_or_none(
                [
                    float(math.copysign(1.0, predicted) == math.copysign(1.0, actual))
                    for predicted, actual in return_pairs
                    if predicted != 0.0 and actual != 0.0
                ]
            ),
            "ic_n": len(return_pairs),
            "ic": ic_value,
            "p_target_brier_n": len(target_pairs),
            "p_target_brier": _mean_or_none(
                [(probability - event) ** 2 for probability, event in target_pairs]
            ),
            "p_stop_brier_n": len(stop_pairs),
            "p_stop_brier": _mean_or_none(
                [(probability - event) ** 2 for probability, event in stop_pairs]
            ),
        }

    return {
        "contract_version": SHADOW_EVALUATION_CONTRACT_VERSION,
        "evaluation_only": True,
        "live_authority": False,
        "coverage": {
            "total_ingested": len(unique_observations),
            "source_invalid": int(source_invalid),
            "outcomes": len(deduped_outcomes),
            "mature": sum(outcome.status == "MATURE" for outcome in deduped_outcomes),
            "pending": sum(outcome.status == "PENDING" for outcome in deduped_outcomes),
            "invalid": sum(outcome.status == "INVALID" for outcome in deduped_outcomes),
        },
        "cohorts": cohorts,
        "by_horizon": by_horizon,
    }


def merge_shadow_outcomes(
    existing: Sequence[ShadowHorizonOutcome],
    incoming: Sequence[ShadowHorizonOutcome],
) -> list[ShadowHorizonOutcome]:
    """Idempotently merge outcomes; a mature record can never be downgraded."""

    rank = {"INVALID": 0, "PENDING": 1, "MATURE": 2}
    merged: dict[tuple[str, str, date, int], ShadowHorizonOutcome] = {}
    for outcome in [*existing, *incoming]:
        previous = merged.get(outcome.key)
        if previous is None or rank[outcome.status] >= rank[previous.status]:
            merged[outcome.key] = outcome
    return sorted(
        merged.values(),
        key=lambda outcome: (
            outcome.signal_as_of,
            outcome.ticker,
            outcome.horizon,
            outcome.run_id,
        ),
    )


def run_shadow_backfill(
    *,
    source_results_path: Path,
    snapshot_manifest_path: Path,
    signal_as_of: date,
    evaluation_as_of: date,
    horizons: Sequence[int] = DEFAULT_SHADOW_HORIZONS,
    output_dir: Path,
    tickers: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run a strictly offline backfill and persist isolated shadow artifacts."""

    normalized_horizons = tuple(sorted({int(horizon) for horizon in horizons}))
    if not normalized_horizons or any(horizon <= 0 for horizon in normalized_horizons):
        raise ValueError("horizons must contain positive integers")
    if evaluation_as_of < signal_as_of:
        raise ValueError("evaluation_as_of cannot be before signal_as_of")
    ticker_filter = (
        {normalize_idx_ticker(ticker) for ticker in tickers} if tickers else None
    )

    source_bytes = Path(source_results_path).read_bytes()
    raw_payload = json.loads(source_bytes.decode("utf-8"))
    if isinstance(raw_payload, list):
        raw_results = raw_payload
    elif isinstance(raw_payload, Mapping) and isinstance(raw_payload.get("results"), list):
        raw_results = raw_payload["results"]
    else:
        raise ValueError("source results must be a JSON list or contain a results list")

    observations: list[ShadowSignalObservation] = []
    issues: list[ShadowSourceIssue] = []
    for raw in raw_results:
        ticker_hint = str(raw.get("ticker") or "UNKNOWN") if isinstance(raw, Mapping) else "UNKNOWN"
        if not isinstance(raw, Mapping):
            issues.append(ShadowSourceIssue(ticker=ticker_hint, reason="result_not_object"))
            continue
        try:
            normalized_ticker = normalize_idx_ticker(ticker_hint)
        except Exception as exc:
            issues.append(ShadowSourceIssue(ticker=ticker_hint, reason=f"invalid_ticker:{exc}"))
            continue
        if ticker_filter is not None and normalized_ticker not in ticker_filter:
            continue
        try:
            observations.append(
                extract_shadow_observation(raw, signal_as_of=signal_as_of)
            )
        except Exception as exc:
            issues.append(
                ShadowSourceIssue(ticker=normalized_ticker, reason=str(exc))
            )

    records_path = Path(output_dir) / "shadow_records.jsonl"
    existing_observations = _read_existing_observations(records_path)
    observations_by_key = {
        observation.key: observation
        for observation in [*existing_observations, *observations]
    }
    observations = sorted(
        observations_by_key.values(),
        key=lambda item: (item.signal_as_of, item.ticker, item.run_id),
    )
    snapshots, snapshot_issues = _load_manifest_snapshots(
        Path(snapshot_manifest_path), {observation.ticker for observation in observations}
    )
    issues.extend(snapshot_issues)

    incoming_outcomes: list[ShadowHorizonOutcome] = []
    for observation in observations:
        snapshot = snapshots.get(observation.ticker)
        for horizon in normalized_horizons:
            if snapshot is None:
                incoming_outcomes.append(
                    _invalid_outcome(
                        observation,
                        horizon,
                        evaluation_as_of,
                        "outcome_snapshot_unavailable",
                    )
                )
            else:
                incoming_outcomes.append(
                    evaluate_shadow_observation(
                        observation,
                        snapshot,
                        horizon=horizon,
                        evaluation_as_of=evaluation_as_of,
                    )
                )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outcomes_path = output_dir / "shadow_outcomes.jsonl"
    existing_outcomes = _read_existing_outcomes(outcomes_path)
    outcomes = merge_shadow_outcomes(existing_outcomes, incoming_outcomes)
    metrics = compute_shadow_metrics(
        observations, outcomes, source_invalid=len(issues)
    )

    records_text = "".join(
        observation.model_dump_json() + "\n" for observation in observations
    )
    outcomes_text = "".join(outcome.model_dump_json() + "\n" for outcome in outcomes)
    _atomic_write_text(records_path, records_text)
    _atomic_write_text(outcomes_path, outcomes_text)
    _atomic_write_text(
        output_dir / "shadow_metrics.json",
        json.dumps(metrics, indent=2, ensure_ascii=False),
    )

    manifest_bytes = Path(snapshot_manifest_path).read_bytes()
    run_manifest = {
        "contract_version": SHADOW_EVALUATION_CONTRACT_VERSION,
        "evaluation_only": True,
        "live_authority": False,
        "offline_only": True,
        "network_fallback": False,
        "signal_as_of": signal_as_of.isoformat(),
        "evaluation_as_of": evaluation_as_of.isoformat(),
        "horizons": list(normalized_horizons),
        "source_results_path": str(Path(source_results_path)),
        "source_results_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "snapshot_manifest_path": str(Path(snapshot_manifest_path)),
        "snapshot_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "observations": len(observations),
        "outcomes": len(outcomes),
        "issues": [issue.model_dump(mode="json") for issue in issues],
    }
    _atomic_write_text(
        output_dir / "run_manifest.json",
        json.dumps(run_manifest, indent=2, ensure_ascii=False),
    )
    _atomic_write_text(output_dir / "shadow_report.md", _format_markdown(metrics))

    coverage = metrics["coverage"]
    return {
        "evaluation_only": True,
        "live_authority": False,
        "observations": len(observations),
        "outcomes": len(outcomes),
        "mature": coverage["mature"],
        "pending": coverage["pending"],
        "invalid": coverage["invalid"],
        "source_invalid": len(issues),
        "output_dir": str(output_dir),
    }


def _load_manifest_snapshots(
    manifest_path: Path,
    tickers: set[str],
) -> tuple[dict[str, MarketSnapshot], list[ShadowSourceIssue]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = payload.get("snapshots")
    if not isinstance(entries, list):
        raise ValueError("snapshot manifest is missing snapshots list")
    root = manifest_path.resolve().parent
    candidates: dict[str, list[Mapping[str, Any]]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        try:
            ticker = normalize_idx_ticker(str(entry.get("ticker") or ""))
        except Exception:
            continue
        if ticker in tickers:
            candidates.setdefault(ticker, []).append(entry)

    snapshots: dict[str, MarketSnapshot] = {}
    issues: list[ShadowSourceIssue] = []
    for ticker in sorted(tickers):
        ticker_entries = candidates.get(ticker, [])
        if not ticker_entries:
            issues.append(
                ShadowSourceIssue(ticker=ticker, reason="ticker_missing_from_snapshot_manifest")
            )
            continue
        entry = max(
            ticker_entries,
            key=lambda item: str(item.get("last_date") or ""),
        )
        artifact_path = str(entry.get("artifact_path") or "").strip()
        if not artifact_path:
            issues.append(
                ShadowSourceIssue(ticker=ticker, reason="snapshot_artifact_path_missing")
            )
            continue
        try:
            path = resolve_within_root(root, artifact_path)
            snapshots[ticker] = load_market_snapshot(
                path,
                expected_snapshot_id=str(entry.get("snapshot_id") or ""),
                expected_data_hash=str(entry.get("data_hash") or ""),
            )
        except Exception as exc:
            issues.append(
                ShadowSourceIssue(ticker=ticker, reason=f"snapshot_integrity_failed:{exc}")
            )
    return snapshots, issues


def _score_forecast(
    observation: ShadowSignalObservation,
    horizon: int,
    horizon_close: float,
) -> dict[str, Any]:
    report = observation.forecast_report
    empty = {
        "terminal_target_event": None,
        "terminal_stop_event": None,
        "predicted_return_net": None,
        "p_target": None,
        "p_stop": None,
    }
    if report is None:
        return {**empty, "status": "NOT_APPLICABLE", "reason": "forecast_report_missing"}
    if str(report.get("forecast_status") or "") != "READY":
        return {**empty, "status": "SKIPPED", "reason": "forecast_not_ready"}
    if int(report.get("horizon_days") or 0) != horizon:
        return {**empty, "status": "SKIPPED", "reason": "forecast_horizon_mismatch"}

    predicted_return = _finite(report.get("expected_return_net"))
    p_target = _probability(report.get("p_target"))
    p_stop = _probability(report.get("p_stop"))
    barrier_fields = (
        report.get("probability_event"),
        report.get("probability_barrier_source"),
        _finite(report.get("probability_reference_close")),
        _finite(report.get("target_barrier_price")),
        _finite(report.get("stop_barrier_price")),
    )
    event, source, reference, target_price, stop_price = barrier_fields
    if (
        event != "terminal"
        or not str(source or "").strip()
        or reference is None
        or target_price is None
        or stop_price is None
        or not math.isclose(reference, observation.signal_price, rel_tol=1e-6, abs_tol=1e-6)
    ):
        return {
            **empty,
            "predicted_return_net": predicted_return,
            "p_target": p_target,
            "p_stop": p_stop,
            "status": "SKIPPED",
            "reason": "missing_probability_barrier_provenance",
        }
    return {
        "terminal_target_event": horizon_close >= target_price,
        "terminal_stop_event": horizon_close <= stop_price,
        "predicted_return_net": predicted_return,
        "p_target": p_target,
        "p_stop": p_stop,
        "status": "READY",
        "reason": "forecast_and_barrier_provenance_ready",
    }


def _invalid_outcome(
    observation: ShadowSignalObservation,
    horizon: int,
    evaluation_as_of: date,
    reason: str,
    *,
    bars_available: int = 0,
) -> ShadowHorizonOutcome:
    return ShadowHorizonOutcome(
        outcome_id=_outcome_id(observation, horizon),
        observation_id=observation.observation_id,
        run_id=observation.run_id,
        ticker=observation.ticker,
        signal_as_of=observation.signal_as_of,
        horizon=horizon,
        status="INVALID",
        reason=reason,
        evaluation_as_of=evaluation_as_of,
        bars_available=bars_available,
        reference_close=observation.signal_price,
        forecast_score_status="SKIPPED",
        forecast_score_reason=reason,
        signal_packet=observation.signal_packet,
        setup_status=observation.setup_status,
        setup_reason_code=observation.setup_reason_code,
        observed_execution_eligible=observation.observed_execution_eligible,
    )


def _validate_forecast_provenance(
    report: Mapping[str, Any],
    ticker: str,
    signal_as_of: date,
    *,
    signal_price: float,
    source_snapshot_id: str,
    source_data_hash: str,
) -> None:
    if str(report.get("ticker") or "").upper() != ticker:
        raise ShadowProvenanceError("forecast_report ticker mismatch")
    for field in ("as_of", "forecast_as_of"):
        if _parse_date(report.get(field), f"forecast_report.{field}") != signal_as_of:
            raise ShadowProvenanceError(
                f"forecast_report.{field} must equal signal_as_of"
            )
    forecast_ready = str(report.get("forecast_status") or "") == "READY"
    feature_as_of = report.get("feature_as_of")
    if feature_as_of is not None:
        if _parse_date(
            feature_as_of, "forecast_report.feature_as_of"
        ) != signal_as_of:
            raise ShadowProvenanceError(
                "forecast_report.feature_as_of must equal signal_as_of"
            )
    elif forecast_ready:
        raise ShadowProvenanceError("READY forecast feature_as_of is missing")

    if report.get("capture_scope") == "preflight_terminal_shadow":
        if report.get("live_authority") is not False:
            raise ShadowProvenanceError(
                "preflight shadow forecast must have live_authority=false"
            )
        if str(report.get("execution_snapshot_id") or "") != source_snapshot_id:
            raise ShadowProvenanceError(
                "preflight shadow execution_snapshot_id mismatch"
            )
        if str(report.get("execution_snapshot_hash") or "") != source_data_hash:
            raise ShadowProvenanceError(
                "preflight shadow execution_snapshot_hash mismatch"
            )
        ihsg_snapshot_id = str(report.get("ihsg_snapshot_id") or "").strip()
        ihsg_snapshot_hash = str(report.get("ihsg_snapshot_hash") or "").strip()
        if not ihsg_snapshot_id.startswith("IHSG-") or not ihsg_snapshot_hash:
            raise ShadowProvenanceError(
                "preflight shadow IHSG snapshot identity/hash is missing"
            )
        if _parse_date(
            report.get("ihsg_feature_as_of"),
            "forecast_report.ihsg_feature_as_of",
        ) != signal_as_of:
            raise ShadowProvenanceError(
                "forecast_report.ihsg_feature_as_of must equal signal_as_of"
            )
        ihsg_provenance = _mapping(
            report.get("ihsg_market_snapshot"),
            "forecast_report.ihsg_market_snapshot",
        )
        if str(ihsg_provenance.get("ticker") or "").upper() != "IHSG":
            raise ShadowProvenanceError("forecast_report IHSG ticker mismatch")
        if str(ihsg_provenance.get("snapshot_id") or "") != ihsg_snapshot_id:
            raise ShadowProvenanceError("forecast_report IHSG snapshot_id mismatch")
        if str(ihsg_provenance.get("data_hash") or "") != ihsg_snapshot_hash:
            raise ShadowProvenanceError("forecast_report IHSG data_hash mismatch")
        if _parse_date(
            ihsg_provenance.get("last_date"),
            "forecast_report.ihsg_market_snapshot.last_date",
        ) != signal_as_of:
            raise ShadowProvenanceError(
                "forecast_report IHSG last_date must equal signal_as_of"
            )
    training_end = report.get("training_end_date")
    horizon = int(report.get("horizon_days") or 0)
    if training_end is not None:
        training_end_date = _parse_date(
            training_end, "forecast_report.training_end_date"
        )
        if (
            horizon <= 0
            or training_end_date > signal_as_of - timedelta(days=horizon)
        ):
            raise ShadowProvenanceError("forecast training/inference provenance overlaps")
    if forecast_ready:
        if not str(report.get("feature_snapshot_hash") or "").strip():
            raise ShadowProvenanceError("READY forecast feature_snapshot_hash is missing")
        feature_close = _finite(report.get("feature_close"))
        if feature_close is None or feature_close <= 0:
            raise ShadowProvenanceError("READY forecast feature_close is missing")
        if not math.isclose(
            feature_close, signal_price, rel_tol=1e-6, abs_tol=1e-6
        ):
            raise ShadowProvenanceError("READY forecast feature_close mismatch")


def _outcome_id(observation: ShadowSignalObservation, horizon: int) -> str:
    payload = "|".join(
        (
            observation.run_id,
            observation.ticker,
            observation.signal_as_of.isoformat(),
            str(horizon),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowProvenanceError(f"missing or malformed {field}")
    return value


def _parse_date(value: Any, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception as exc:
        raise ShadowProvenanceError(f"invalid {field}") from exc


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _first_finite_positive(*values: Any) -> float | None:
    for value in values:
        number = _finite(value)
        if number is not None and number > 0:
            return number
    return None


def _probability(value: Any) -> float | None:
    number = _finite(value)
    if number is None or not 0.0 <= number <= 1.0:
        return None
    return number


def _mean_or_none(values: Iterable[float]) -> float | None:
    materialized = [float(value) for value in values]
    if not materialized:
        return None
    return sum(materialized) / len(materialized)


def _read_existing_outcomes(path: Path) -> list[ShadowHorizonOutcome]:
    if not path.exists():
        return []
    records: list[ShadowHorizonOutcome] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(ShadowHorizonOutcome.model_validate_json(line))
    return records


def _read_existing_observations(path: Path) -> list[ShadowSignalObservation]:
    if not path.exists():
        return []
    records: list[ShadowSignalObservation] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(ShadowSignalObservation.model_validate_json(line))
    return records


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _format_markdown(metrics: Mapping[str, Any]) -> str:
    coverage = metrics["coverage"]
    lines = [
        "# Shadow Outcome Evaluation",
        "",
        "> Evaluation only. This report has no live execution authority.",
        "",
        f"- Signal observations: {coverage['total_ingested']}",
        f"- Mature outcomes: {coverage['mature']}",
        f"- Pending outcomes: {coverage['pending']}",
        f"- Invalid outcomes: {coverage['invalid']}",
        f"- Invalid source/snapshot records: {coverage['source_invalid']}",
        "",
        "## Horizon coverage",
        "",
        "| Horizon | Mature | Pending | Invalid | Forecast-ready |",
        "|---:|---:|---:|---:|---:|",
    ]
    for horizon, values in metrics["by_horizon"].items():
        lines.append(
            f"| {horizon} | {values['mature']} | {values['pending']} | "
            f"{values['invalid']} | {values['forecast_ready_n']} |"
        )
    lines.extend(
        [
            "",
            "No recommendation, promotion target, position size, or risk-gate override "
            "is produced by this evaluator.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_SHADOW_HORIZONS",
    "SHADOW_EVALUATION_CONTRACT_VERSION",
    "ShadowHorizonOutcome",
    "ShadowProvenanceError",
    "ShadowSignalObservation",
    "compute_shadow_metrics",
    "evaluate_shadow_observation",
    "extract_shadow_observation",
    "merge_shadow_outcomes",
    "run_shadow_backfill",
]
