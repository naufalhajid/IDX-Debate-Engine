"""Regression tests for Phase 5 shadow-only outcome validation."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from app.cli.main import app
from core.forecasting.labels import TRANSACTION_COST
from core.forecasting.service import _probability_barrier_metadata
from core.forecasting.shadow_evaluation import (
    ShadowHorizonOutcome,
    ShadowProvenanceError,
    compute_shadow_metrics,
    evaluate_shadow_observation,
    extract_shadow_observation,
    merge_shadow_outcomes,
    run_shadow_backfill,
)
from schemas.debate import SignalPacket
from utils.market_snapshot import (
    MarketSnapshot,
    build_market_snapshot,
    persist_market_snapshots,
)


AS_OF = date(2026, 1, 5)


def _history(
    future_closes: list[float],
    *,
    future_highs: list[float] | None = None,
    future_lows: list[float] | None = None,
) -> pd.DataFrame:
    prior_dates = pd.bdate_range(end=AS_OF, periods=4)
    future_dates = pd.bdate_range(start=AS_OF + timedelta(days=1), periods=len(future_closes))
    closes = [97.0, 98.0, 99.0, 100.0, *future_closes]
    highs = [value + 1.0 for value in closes]
    lows = [value - 1.0 for value in closes]
    if future_highs is not None:
        highs[-len(future_highs) :] = future_highs
    if future_lows is not None:
        lows[-len(future_lows) :] = future_lows
    index = prior_dates.append(future_dates)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1_000_000.0] * len(closes),
        },
        index=index,
    )


def _snapshot(history: pd.DataFrame, *, requested_end: date | None = None) -> MarketSnapshot:
    end = requested_end or history.index[-1].date()
    return build_market_snapshot(
        "BBCA",
        history,
        requested_start=history.index[0].date(),
        requested_end=end,
        min_complete_bars=1,
        now=datetime(2026, 2, 1, 18, 0),
    )


def _signal_snapshot() -> MarketSnapshot:
    return _snapshot(_history([]), requested_end=AS_OF)


def _forecast_report(
    *,
    horizon: int = 5,
    expected_return: float = 0.02,
    p_target: float | None = 0.7,
    p_stop: float | None = 0.2,
    with_barriers: bool = True,
) -> dict:
    payload = {
        "ticker": "BBCA",
        "as_of": AS_OF.isoformat(),
        "forecast_as_of": AS_OF.isoformat(),
        "feature_as_of": AS_OF.isoformat(),
        "training_end_date": "2025-12-19",
        "horizon_days": horizon,
        "forecast_status": "READY",
        "failure_reason": None,
        "expected_return_net": expected_return,
        "p_target": p_target,
        "p_stop": p_stop,
        "feature_close": 100.0,
        "feature_snapshot_hash": "phase5-fixture-feature-hash",
        "decision": "AVOID",
        "shadow_decision": "WATCH",
        "shadow_evaluation_only": True,
    }
    if with_barriers:
        payload.update(
            {
                "probability_event": "terminal",
                "probability_barrier_source": "default_horizon",
                "probability_reference_close": 100.0,
                "target_barrier_return": 0.10,
                "stop_barrier_return": 0.05,
                "target_barrier_price": 110.0,
                "stop_barrier_price": 95.0,
            }
        )
    return payload


def _result(
    *,
    forecast_report: dict | None = None,
    setup_status: str = "RR_TOO_LOW",
) -> dict:
    source_snapshot = _signal_snapshot()
    packet = SignalPacket(
        signal_lean="BULLISH_SETUP",
        chart_strength="BULLISH",
        relative_strength=4.2,
        volume_confirmation=True,
        fundamental_quality="ADEQUATE",
        valuation_state="FAIR",
        forecast_state="READY" if forecast_report else "SKIPPED_PREFLIGHT",
        execution_eligible=False,
        execution_rejection_reason="rr_too_low",
        required_entry_trigger="Wait at or below Rp 95.",
    ).model_dump(mode="json")
    result = {
        "ticker": "BBCA",
        "status": "success",
        "actionable": False,
        "execution_status": "NO_TRADE",
        "verdict": {
            "ticker": "BBCA",
            "rating": "HOLD",
            "current_price": 100.0,
            "target_price": None,
            "stop_loss": None,
        },
        "metadata": {
            "run_id": "phase5-fixture",
            "current_price_as_of": f"{AS_OF.isoformat()}T00:00:00+00:00",
            "market_snapshot": source_snapshot.provenance(),
            "signal_packet": packet,
            "trade_setup_snapshot": {
                "status": setup_status,
                "reason_code": "rr_too_low",
                "technical_indicators": {"current_price": 100.0},
                "hypothetical_envelope": {
                    "entry_high": 100.0,
                    "target_price": 110.0,
                    "stop_loss": 95.0,
                    "required_rr": 2.0,
                },
            },
        },
    }
    if forecast_report is not None:
        result["forecast_report"] = forecast_report
    return result


def test_shadow_evaluator_uses_persisted_point_in_time_provenance_only() -> None:
    source = _result(forecast_report=_forecast_report())
    frozen = deepcopy(source)
    observation = extract_shadow_observation(source, signal_as_of=AS_OF)
    outcome_snapshot = _snapshot(_history([101, 102, 103, 104, 105, 999]))

    outcome = evaluate_shadow_observation(
        observation,
        outcome_snapshot,
        horizon=5,
        evaluation_as_of=outcome_snapshot.last_date,
    )

    assert outcome.status == "MATURE"
    assert outcome.maturity_date == pd.bdate_range(
        start=AS_OF + timedelta(days=1), periods=5
    )[-1].date()
    assert outcome.horizon_close == 105.0
    assert source == frozen
    assert outcome.evaluation_only is True
    assert outcome.live_authority is False

    mismatched = deepcopy(source)
    mismatched["metadata"]["market_snapshot"]["last_date"] = "2026-01-02"
    with pytest.raises(ShadowProvenanceError, match="last_date"):
        extract_shadow_observation(mismatched, signal_as_of=AS_OF)


@pytest.mark.parametrize("horizon", [5, 10, 20])
def test_horizon_maturity_requires_exact_observed_sessions(horizon: int) -> None:
    observation = extract_shadow_observation(_result(), signal_as_of=AS_OF)
    future = [101.0 + index for index in range(horizon)]
    snapshot = _snapshot(_history(future, future_highs=[111.0, *future[1:]]))
    future_dates = pd.bdate_range(start=AS_OF + timedelta(days=1), periods=horizon)

    pending = evaluate_shadow_observation(
        observation,
        snapshot,
        horizon=horizon,
        evaluation_as_of=future_dates[horizon - 2].date(),
    )
    mature = evaluate_shadow_observation(
        observation,
        snapshot,
        horizon=horizon,
        evaluation_as_of=future_dates[-1].date(),
    )

    assert pending.status == "PENDING"
    assert pending.bars_available == horizon - 1
    assert pending.forward_return_gross is None
    assert pending.target_hit is None
    assert mature.status == "MATURE"
    assert mature.bars_available == horizon


def test_horizon_cutoff_and_h_plus_one_cannot_leak() -> None:
    observation = extract_shadow_observation(_result(), signal_as_of=AS_OF)
    first_five = [101, 102, 103, 104, 105]
    snapshot = _snapshot(
        _history(
            [*first_five, 5_000],
            future_highs=[102, 103, 104, 105, 106, 9_000],
            future_lows=[99, 99, 99, 99, 99, 1],
        )
    )

    at_five = evaluate_shadow_observation(
        observation,
        snapshot,
        horizon=5,
        evaluation_as_of=snapshot.history.index[-2].date(),
    )
    after_six = evaluate_shadow_observation(
        observation,
        snapshot,
        horizon=5,
        evaluation_as_of=snapshot.history.index[-1].date(),
    )

    assert at_five.model_dump() == after_six.model_dump()


def test_rejected_signal_packet_is_preserved_and_scored() -> None:
    source = _result()
    original_packet = deepcopy(source["metadata"]["signal_packet"])
    observation = extract_shadow_observation(source, signal_as_of=AS_OF)
    outcome = evaluate_shadow_observation(
        observation,
        _snapshot(_history([101, 102, 103, 104, 105])),
        horizon=5,
        evaluation_as_of=date(2026, 1, 30),
    )
    metrics = compute_shadow_metrics([observation], [outcome])

    assert observation.signal_packet.model_dump(mode="json") == original_packet
    assert outcome.signal_packet.model_dump(mode="json") == original_packet
    assert metrics["cohorts"]["signal_lean"] == {"BULLISH_SETUP": 1}
    assert metrics["cohorts"]["setup_status"] == {"RR_TOO_LOW": 1}
    assert metrics["cohorts"]["rejection_reason"] == {"rr_too_low": 1}
    assert source["metadata"]["signal_packet"] == original_packet

    malformed = _result()
    malformed["metadata"].pop("signal_packet")
    with pytest.raises(ShadowProvenanceError, match="signal_packet"):
        extract_shadow_observation(malformed, signal_as_of=AS_OF)


def test_target_stop_first_touch_and_mae_are_exact() -> None:
    observation = extract_shadow_observation(_result(), signal_as_of=AS_OF)
    snapshot = _snapshot(
        _history(
            [101, 108, 99, 102, 103],
            future_highs=[104, 111, 105, 104, 105],
            future_lows=[99, 98, 94, 97, 98],
        )
    )
    outcome = evaluate_shadow_observation(
        observation,
        snapshot,
        horizon=5,
        evaluation_as_of=date(2026, 1, 30),
    )

    assert outcome.target_hit is True
    assert outcome.stop_hit is True
    assert outcome.first_touch == "TARGET"
    assert outcome.same_bar_ambiguous is False
    assert outcome.mae_pct == pytest.approx(-0.06)
    assert outcome.mfe_pct == pytest.approx(0.11)

    same_bar = _snapshot(
        _history(
            [100, 100, 100, 100, 100],
            future_highs=[111, 101, 101, 101, 101],
            future_lows=[94, 99, 99, 99, 99],
        )
    )
    ambiguous = evaluate_shadow_observation(
        observation,
        same_bar,
        horizon=5,
        evaluation_as_of=date(2026, 1, 30),
    )
    assert ambiguous.first_touch == "STOP"
    assert ambiguous.same_bar_ambiguous is True


def test_forecast_metrics_use_training_return_and_separate_denominators() -> None:
    observations = []
    outcomes = []
    closes = [102.0, 104.0, 112.0]
    predictions = [0.01, 0.02, 0.03]
    probabilities = [(0.2, 0.1), (None, 0.4), (0.8, 0.2)]
    for ticker, close, prediction, probs in zip(
        ("BBCA", "BBCB", "BBCC"),
        closes,
        predictions,
        probabilities,
        strict=True,
    ):
        result = _result(
            forecast_report=_forecast_report(
                expected_return=prediction,
                p_target=probs[0],
                p_stop=probs[1],
            )
        )
        result["ticker"] = ticker
        result["verdict"]["ticker"] = ticker
        result["metadata"]["market_snapshot"]["ticker"] = ticker
        result["forecast_report"]["ticker"] = ticker
        observation = extract_shadow_observation(
            result,
            signal_as_of=AS_OF,
            validate_snapshot_ticker=False,
        )
        outcome = evaluate_shadow_observation(
            observation,
            _snapshot(_history([100, 100, 100, 100, close])),
            horizon=5,
            evaluation_as_of=date(2026, 1, 30),
            validate_snapshot_ticker=False,
        )
        observations.append(observation)
        outcomes.append(outcome)

    metrics = compute_shadow_metrics(observations, outcomes)
    horizon_metrics = metrics["by_horizon"]["5"]
    expected_actual = [
        math.log(close / 100.0) - math.log(1.0 + TRANSACTION_COST)
        for close in closes
    ]

    assert [outcome.realized_return_net for outcome in outcomes] == pytest.approx(
        expected_actual
    )
    assert horizon_metrics["return_error_n"] == 3
    expected_mae = sum(
        abs(predicted - actual)
        for predicted, actual in zip(predictions, expected_actual, strict=True)
    ) / 3
    assert horizon_metrics["mae"] == pytest.approx(expected_mae)
    assert horizon_metrics["ic"] == pytest.approx(1.0)
    assert horizon_metrics["p_target_brier_n"] == 2
    assert horizon_metrics["p_stop_brier_n"] == 3
    assert horizon_metrics["p_target_brier"] == pytest.approx(
        ((0.2 - 0.0) ** 2 + (0.8 - 1.0) ** 2) / 2
    )


def test_missing_probability_barriers_skip_brier_without_guessing() -> None:
    observation = extract_shadow_observation(
        _result(forecast_report=_forecast_report(with_barriers=False)),
        signal_as_of=AS_OF,
    )
    outcome = evaluate_shadow_observation(
        observation,
        _snapshot(_history([100, 100, 100, 100, 112])),
        horizon=5,
        evaluation_as_of=date(2026, 1, 30),
    )
    metrics = compute_shadow_metrics([observation], [outcome])

    assert outcome.forecast_score_status == "SKIPPED"
    assert outcome.forecast_score_reason == "missing_probability_barrier_provenance"
    assert metrics["by_horizon"]["5"]["p_target_brier_n"] == 0
    assert metrics["by_horizon"]["5"]["p_target_brier"] is None


def test_new_forecasts_persist_exact_terminal_probability_barriers() -> None:
    metadata = _probability_barrier_metadata(10, None, 100.0)

    assert metadata == {
        "probability_event": "terminal",
        "probability_barrier_source": "default_horizon",
        "probability_reference_close": 100.0,
        "target_barrier_return": pytest.approx(0.015),
        "stop_barrier_return": pytest.approx(0.015),
        "target_barrier_price": pytest.approx(101.5),
        "stop_barrier_price": pytest.approx(98.5),
    }


def test_shadow_outcome_merge_is_idempotent_and_never_downgrades_mature() -> None:
    observation = extract_shadow_observation(_result(), signal_as_of=AS_OF)
    snapshot = _snapshot(_history([101, 102, 103, 104, 105]))
    pending = evaluate_shadow_observation(
        observation,
        snapshot,
        horizon=5,
        evaluation_as_of=snapshot.history.index[-2].date(),
    )
    mature = evaluate_shadow_observation(
        observation,
        snapshot,
        horizon=5,
        evaluation_as_of=snapshot.history.index[-1].date(),
    )

    assert merge_shadow_outcomes([pending], [pending]) == [pending]
    assert merge_shadow_outcomes([pending], [mature]) == [mature]
    assert merge_shadow_outcomes([mature], [pending]) == [mature]


def test_backfill_writes_isolated_evaluation_only_artifacts(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "full_batch_results.json"
    source = [_result()]
    source_path.write_text(json.dumps(source), encoding="utf-8")
    snapshots_dir = tmp_path / "snapshots"
    persist_market_snapshots(
        {"BBCA": _snapshot(_history([101, 102, 103, 104]))},
        snapshots_dir,
    )
    output_dir = tmp_path / "shadow"

    summary = run_shadow_backfill(
        source_results_path=source_path,
        snapshot_manifest_path=snapshots_dir / "manifest.json",
        signal_as_of=AS_OF,
        evaluation_as_of=date(2026, 1, 9),
        horizons=(5, 10, 20),
        output_dir=output_dir,
    )

    assert summary["evaluation_only"] is True
    assert summary["live_authority"] is False
    assert summary["observations"] == 1
    assert summary["outcomes"] == 3
    assert summary["mature"] == 0
    assert summary["pending"] == 3
    assert source == json.loads(source_path.read_text(encoding="utf-8"))
    for name in (
        "run_manifest.json",
        "shadow_records.jsonl",
        "shadow_outcomes.jsonl",
        "shadow_metrics.json",
        "shadow_report.md",
    ):
        assert (output_dir / name).is_file()

    records = [
        json.loads(line)
        for line in (output_dir / "shadow_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    outcomes = [
        ShadowHorizonOutcome.model_validate_json(line)
        for line in (output_dir / "shadow_outcomes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records[0]["signal_packet"]["execution_eligible"] is False
    assert all(outcome.live_authority is False for outcome in outcomes)
    assert not any(
        key in ShadowHorizonOutcome.model_fields
        for key in ("decision", "rating", "actionable", "position_size")
    )


def test_shadow_backfill_cli_is_explicit_and_offline(tmp_path: Path) -> None:
    source_path = tmp_path / "full_batch_results.json"
    source_path.write_text(json.dumps([_result()]), encoding="utf-8")
    snapshots_dir = tmp_path / "snapshots"
    persist_market_snapshots(
        {"BBCA": _snapshot(_history([101, 102, 103, 104]))},
        snapshots_dir,
    )
    output_dir = tmp_path / "shadow-cli"

    result = CliRunner().invoke(
        app,
        [
            "forecast",
            "shadow-backfill",
            "--source-results",
            str(source_path),
            "--snapshot-manifest",
            str(snapshots_dir / "manifest.json"),
            "--as-of",
            AS_OF.isoformat(),
            "--evaluation-as-of",
            "2026-01-09",
            "--horizon",
            "5",
            "--horizon",
            "10",
            "--horizon",
            "20",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "live_authority=false" in result.output
    assert "mature=0" in result.output
    assert "pending=3" in result.output
