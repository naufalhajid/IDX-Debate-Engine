"""Regression tests for frozen-input preflight forecast capture."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import types
from typing import Any

import numpy as np
import pandas as pd
import pytest

from core.forecasting import dataset as dataset_module
from core.forecasting.schemas import ForecastReport
from core.forecasting.service import ForecastingService
from core.forecasting.shadow_evaluation import (
    ShadowProvenanceError,
    extract_shadow_observation,
)
from schemas.debate import SignalPacket
from utils.market_snapshot import (
    MarketSnapshot,
    build_market_snapshot,
    load_market_snapshot,
)


AS_OF = date(2026, 7, 15)


def _history(
    *,
    periods: int = 850,
    final_close: float = 100.0,
) -> pd.DataFrame:
    index = pd.bdate_range(end=AS_OF, periods=periods)
    close = np.linspace(final_close * 0.65, final_close, periods)
    close += np.sin(np.arange(periods) / 13.0) * final_close * 0.004
    close[-1] = final_close
    return pd.DataFrame(
        {
            "Open": close * 0.998,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(periods, 10_000_000.0),
        },
        index=index,
    )


def _snapshot(
    ticker: str,
    *,
    periods: int = 850,
    final_close: float = 100.0,
    requested_end: date = AS_OF,
) -> MarketSnapshot:
    history = _history(periods=periods, final_close=final_close)
    return build_market_snapshot(
        ticker,
        history,
        requested_start=history.index[0].date(),
        requested_end=requested_end,
        min_complete_bars=1,
        now=datetime(2026, 7, 16, 18, 0),
    )


def _feature_frame() -> pd.DataFrame:
    dates = pd.bdate_range(end=AS_OF, periods=101)
    close = np.linspace(80.0, 100.0, len(dates))
    forward = pd.Series(close, index=dates).shift(-10)
    index = pd.MultiIndex.from_arrays(
        [["BBCA"] * len(dates), [value.date() for value in dates]],
        names=["ticker", "date"],
    )
    return pd.DataFrame(
        {
            "close": close,
            "close_t10": forward.to_numpy(),
            "feature_marker": np.arange(len(dates), dtype=float),
            "ocf_price_pct": np.nan,
        },
        index=index,
    )


def _signal_packet() -> dict[str, Any]:
    return SignalPacket(
        signal_lean="BULLISH_SETUP",
        chart_strength="BULLISH",
        volume_confirmation=True,
        execution_eligible=False,
        execution_rejection_reason="rr_too_low",
        forecast_state="SKIPPED_PREFLIGHT",
    ).model_dump(mode="json")


def _terminal_result(
    ticker_snapshot: MarketSnapshot,
    *,
    technical_status: str = "COMPLETE",
) -> dict[str, Any]:
    return {
        "ticker": "BBCA",
        "status": "success",
        "actionable": False,
        "execution_status": "NO_TRADE",
        "conviction_score": 0.0,
        "trade_conviction": 0.0,
        "verdict": {
            "ticker": "BBCA",
            "rating": "HOLD",
            "confidence": 0.0,
            "current_price": 100.0,
            "execution_status": "NO_TRADE",
        },
        "risk_governor": {
            "status": "reject",
            "sizing_allowed": False,
            "reason_codes": ["rr_too_low"],
        },
        "execution_decision": {
            "actionable": False,
            "status": "NO_TRADE",
        },
        "metadata": {
            "run_id": "phase5b-fixture",
            "current_price_as_of": f"{AS_OF.isoformat()}T00:00:00+00:00",
            "market_snapshot": ticker_snapshot.provenance(),
            "signal_packet": _signal_packet(),
            "trade_setup_snapshot": {
                "status": "RR_TOO_LOW",
                "reason_code": "rr_too_low",
                "debate_eligible": False,
                "technical_data_status": technical_status,
                "technical_indicators": {"current_price": 100.0},
                "hypothetical_envelope": {
                    "entry_high": 100.0,
                    "target_price": 110.0,
                    "stop_loss": 95.0,
                },
            },
        },
        "_execution_snapshot": ticker_snapshot,
    }


def _ready_report() -> ForecastReport:
    return ForecastReport(
        ticker="BBCA",
        as_of=AS_OF,
        forecast_as_of=AS_OF,
        feature_as_of=AS_OF,
        training_end_date=AS_OF - timedelta(days=20),
        horizon_days=10,
        feature_snapshot_hash="phase5b-feature-hash",
        feature_close=100.0,
        forecast_status="READY",
        failure_reason=None,
        expected_return_net=0.04,
        p_target=0.80,
        p_stop=0.10,
        expected_value=0.03,
        risk_adjusted_expected_value=0.03,
        decision="BUY",
        probability_source="return_volatility_parametric",
        probability_event="terminal",
        probability_barrier_source="default_horizon",
        probability_reference_close=100.0,
        target_barrier_return=0.015,
        stop_barrier_return=0.015,
        target_barrier_price=101.5,
        stop_barrier_price=98.5,
    )


def _captured_terminal_result(
    *,
    report: ForecastReport | None = None,
) -> dict[str, Any]:
    ticker_snapshot = _snapshot("BBCA")
    ihsg_snapshot = _snapshot("IHSG", final_close=6_200.0)
    result = _terminal_result(ticker_snapshot)
    result.pop("_execution_snapshot")
    payload = (report or _ready_report()).model_dump(mode="json")
    payload.update(
        {
            "capture_scope": "preflight_terminal_shadow",
            "live_authority": False,
            "execution_snapshot_id": ticker_snapshot.snapshot_id,
            "execution_snapshot_hash": ticker_snapshot.data_hash,
            "ihsg_snapshot_id": ihsg_snapshot.snapshot_id,
            "ihsg_snapshot_hash": ihsg_snapshot.data_hash,
            "ihsg_feature_as_of": AS_OF.isoformat(),
            "ihsg_market_snapshot": ihsg_snapshot.provenance(),
        }
    )
    result["forecast_report"] = payload
    return result


def test_injected_ihsg_snapshot_prevents_live_regime_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker_snapshot = _snapshot("BBCA")
    ihsg_snapshot = _snapshot("IHSG", final_close=6_200.0)
    builder = dataset_module.DatasetBuilder()

    monkeypatch.setattr(
        dataset_module,
        "_compute_ihsg_regimes",
        lambda *_args, **_kwargs: pytest.fail("live IHSG provider path reached"),
    )

    def fill_empty_fundamentals(frame: pd.DataFrame, _ticker: str) -> None:
        frame["pe_ratio"] = np.nan
        frame["pb_ratio"] = np.nan
        frame["ocf_price_pct"] = np.nan

    monkeypatch.setattr(dataset_module, "_fill_fundamentals", fill_empty_fundamentals)

    built = builder.build(
        ["BBCA"],
        AS_OF - timedelta(days=756),
        AS_OF,
        horizons=(10,),
        snapshots={"BBCA": ticker_snapshot},
        ihsg_snapshot=ihsg_snapshot,
        include_unlabeled_tail=True,
    )

    assert not built.empty
    assert built.index[-1][1] == AS_OF
    assert {
        "regime_defensive",
        "regime_recovery",
        "regime_high",
        "regime_low",
    }.issubset(built.columns)


def test_service_cache_identity_includes_ticker_and_ihsg_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker_snapshot = _snapshot("BBCA")
    ihsg_a = _snapshot("IHSG", final_close=6_200.0)
    ihsg_b = _snapshot("IHSG", final_close=6_250.0)
    frozen = _feature_frame()

    class RecordingBuilder:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def build(self, *_args: Any, **kwargs: Any) -> pd.DataFrame:
            self.calls.append(kwargs)
            return frozen.copy(deep=True)

    builder = RecordingBuilder()
    service = ForecastingService()
    service._dataset_builder = builder
    monkeypatch.setattr(
        "core.forecasting.service.walk_forward_splits",
        lambda *_args, **_kwargs: [],
    )

    first = service.predict(
        "BBCA",
        as_of=AS_OF,
        horizons=(10,),
        mode="naive",
        execution_snapshot=ticker_snapshot,
        ihsg_snapshot=ihsg_a,
        frozen_inputs_only=True,
    )
    repeated = service.predict(
        "BBCA",
        as_of=AS_OF,
        horizons=(10,),
        mode="naive",
        execution_snapshot=ticker_snapshot,
        ihsg_snapshot=ihsg_a,
        frozen_inputs_only=True,
    )
    changed_benchmark = service.predict(
        "BBCA",
        as_of=AS_OF,
        horizons=(10,),
        mode="naive",
        execution_snapshot=ticker_snapshot,
        ihsg_snapshot=ihsg_b,
        frozen_inputs_only=True,
    )

    assert len(builder.calls) == 2
    assert first.feature_snapshot_hash == repeated.feature_snapshot_hash
    assert first.ihsg_snapshot_id == ihsg_a.snapshot_id
    assert first.ihsg_snapshot_hash == ihsg_a.data_hash
    assert changed_benchmark.ihsg_snapshot_id == ihsg_b.snapshot_id
    assert builder.calls[0]["ihsg_snapshot"] is ihsg_a
    assert builder.calls[1]["ihsg_snapshot"] is ihsg_b


def test_frozen_service_fails_closed_on_missing_or_future_ihsg_snapshot() -> None:
    ticker_snapshot = _snapshot("BBCA")
    future_ihsg = _snapshot(
        "IHSG",
        requested_end=AS_OF + timedelta(days=1),
    )
    service = ForecastingService()

    missing = service.predict(
        "BBCA",
        as_of=AS_OF,
        horizons=(10,),
        mode="naive",
        execution_snapshot=ticker_snapshot,
        frozen_inputs_only=True,
    )
    future = service.predict(
        "BBCA",
        as_of=AS_OF,
        horizons=(10,),
        mode="naive",
        execution_snapshot=ticker_snapshot,
        ihsg_snapshot=future_ihsg,
        frozen_inputs_only=True,
    )

    assert missing.forecast_status == "UNAVAILABLE"
    assert missing.failure_reason == "missing_frozen_ihsg_snapshot"
    assert future.forecast_status == "UNAVAILABLE"
    assert future.failure_reason == "ihsg_snapshot_as_of_mismatch"


def test_mutated_frozen_snapshots_fail_closed_before_feature_cache() -> None:
    ticker_snapshot = _snapshot("BBCA")
    ihsg_snapshot = _snapshot("IHSG", final_close=6_200.0)
    ticker_snapshot.history.iloc[-1, ticker_snapshot.history.columns.get_loc("Close")] += 1.0
    ticker_failure = ForecastingService().predict(
        "BBCA",
        as_of=AS_OF,
        horizons=(10,),
        mode="naive",
        execution_snapshot=ticker_snapshot,
        ihsg_snapshot=ihsg_snapshot,
        frozen_inputs_only=True,
    )

    clean_ticker = _snapshot("BBCA")
    ihsg_snapshot.history.iloc[-1, ihsg_snapshot.history.columns.get_loc("Close")] += 1.0
    ihsg_failure = ForecastingService().predict(
        "BBCA",
        as_of=AS_OF,
        horizons=(10,),
        mode="naive",
        execution_snapshot=clean_ticker,
        ihsg_snapshot=ihsg_snapshot,
        frozen_inputs_only=True,
    )

    assert ticker_failure.failure_reason == "ticker_snapshot_integrity_failed"
    assert ihsg_failure.failure_reason == "ihsg_snapshot_integrity_failed"


def test_sparse_ihsg_history_cannot_masquerade_as_ma200_coverage() -> None:
    start = AS_OF - timedelta(days=756)
    full_history = _history(periods=850, final_close=6_200.0)
    pre_start = full_history[full_history.index.date < start].iloc[:199]
    post_start = full_history[full_history.index.date >= start].iloc[-80:]
    sparse_history = pd.concat([pre_start, post_start]).sort_index()
    sparse_ihsg = build_market_snapshot(
        "IHSG",
        sparse_history,
        requested_start=sparse_history.index[0].date(),
        requested_end=AS_OF,
        min_complete_bars=1,
        now=datetime(2026, 7, 16, 18, 0),
    )

    with pytest.raises(ValueError, match="200 complete sessions"):
        dataset_module.DatasetBuilder().build(
            ["BBCA"],
            start,
            AS_OF,
            horizons=(10,),
            snapshots={"BBCA": _snapshot("BBCA")},
            ihsg_snapshot=sparse_ihsg,
            include_unlabeled_tail=True,
        )


def test_complete_preflight_terminal_receives_isolated_shadow_forecast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.orchestrator.legacy import _inject_forecast_reports

    ticker_snapshot = _snapshot("BBCA")
    ihsg_snapshot = _snapshot("IHSG", final_close=6_200.0)
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class FakeForecastingService:
        def predict(self, *args: Any, **kwargs: Any) -> ForecastReport:
            calls.append((args, kwargs))
            return _ready_report()

    fake_module = types.ModuleType("core.forecasting")
    fake_module.ForecastingService = FakeForecastingService
    monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)
    result = _terminal_result(ticker_snapshot)
    protected = deepcopy(
        {
            key: result[key]
            for key in (
                "verdict",
                "risk_governor",
                "execution_decision",
                "actionable",
                "execution_status",
                "conviction_score",
                "trade_conviction",
            )
        }
    )

    asyncio.run(
        _inject_forecast_reports(
            [result],
            ihsg_snapshot=ihsg_snapshot,
            persist_ihsg_snapshot=False,
        )
    )

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[4] is None  # no synthetic terminal HOLD levels in probability math
    assert args[5] is ticker_snapshot
    assert kwargs["ihsg_snapshot"] is ihsg_snapshot
    assert kwargs["frozen_inputs_only"] is True
    assert result["forecast_report"]["capture_scope"] == (
        "preflight_terminal_shadow"
    )
    assert result["forecast_report"]["live_authority"] is False
    assert result["forecast_report"]["execution_snapshot_id"] == (
        ticker_snapshot.snapshot_id
    )
    assert result["forecast_report"]["ihsg_snapshot_id"] == ihsg_snapshot.snapshot_id
    assert result["metadata"]["signal_packet"]["forecast_state"] == "SHADOW_READY"
    assert result["forecast_ev_ignored_reason"] == (
        "preflight_terminal_shadow_only"
    )
    assert "forecast_ev_pct" not in result
    assert "forecast_ev_downweight" not in result
    assert {
        key: result[key]
        for key in protected
    } == protected
    assert "_execution_snapshot" not in result


def test_insufficient_preflight_stores_explicit_unavailable_without_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.orchestrator.legacy import _inject_forecast_reports

    ticker_snapshot = _snapshot("BBCA", periods=6)
    ihsg_snapshot = _snapshot("IHSG", final_close=6_200.0)

    class FakeForecastingService:
        def predict(self, *_args: Any, **_kwargs: Any) -> ForecastReport:
            return ForecastReport(
                ticker="BBCA",
                as_of=AS_OF,
                forecast_as_of=AS_OF,
                horizon_days=10,
                forecast_status="UNAVAILABLE",
                failure_reason="insufficient_data",
                decision="AVOID",
            )

    fake_module = types.ModuleType("core.forecasting")
    fake_module.ForecastingService = FakeForecastingService
    monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)
    result = _terminal_result(
        ticker_snapshot,
        technical_status="INSUFFICIENT_DATA",
    )

    asyncio.run(
        _inject_forecast_reports(
            [result],
            ihsg_snapshot=ihsg_snapshot,
            persist_ihsg_snapshot=False,
        )
    )

    report = result["forecast_report"]
    assert report["forecast_status"] == "UNAVAILABLE"
    assert report["decision"] == "AVOID"
    assert report["expected_value"] is None
    assert report["p_target"] is None
    assert report["p_stop"] is None
    assert report["capture_scope"] == "preflight_terminal_shadow"
    assert result["metadata"]["signal_packet"]["forecast_state"] == (
        "SHADOW_UNAVAILABLE"
    )
    assert result["actionable"] is False
    assert "forecast_ev_pct" not in result


def test_missing_shared_ihsg_never_falls_back_per_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.orchestrator.legacy import _inject_forecast_reports

    ticker_snapshot = _snapshot("BBCA")

    class ForbiddenForecastingService:
        def predict(self, *_args: Any, **_kwargs: Any) -> ForecastReport:
            pytest.fail("forecast service reached without frozen IHSG")

    fake_module = types.ModuleType("core.forecasting")
    fake_module.ForecastingService = ForbiddenForecastingService
    monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)
    monkeypatch.setattr(
        dataset_module,
        "download_ihsg_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("provider unavailable")
        ),
        raising=False,
    )
    result = _terminal_result(ticker_snapshot)

    asyncio.run(_inject_forecast_reports([result], persist_ihsg_snapshot=False))

    assert "forecast_report" not in result
    assert result["forecast_ev_ignored_reason"] == "frozen_ihsg_snapshot_unavailable"
    assert result["metadata"]["signal_packet"]["forecast_state"] == (
        "MISSING_FROZEN_IHSG"
    )
    assert "_execution_snapshot" not in result


def test_terminal_capture_without_frozen_ticker_never_uses_legacy_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.orchestrator.legacy import _inject_forecast_reports

    class ForbiddenForecastingService:
        def predict(self, *_args: Any, **_kwargs: Any) -> ForecastReport:
            pytest.fail("legacy provider forecast reached without ticker snapshot")

    fake_module = types.ModuleType("core.forecasting")
    fake_module.ForecastingService = ForbiddenForecastingService
    monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)
    result = _terminal_result(_snapshot("BBCA"))
    result.pop("_execution_snapshot")

    asyncio.run(
        _inject_forecast_reports(
            [result],
            ihsg_snapshot=_snapshot("IHSG", final_close=6_200.0),
            persist_ihsg_snapshot=False,
        )
    )

    assert "forecast_report" not in result
    assert result["forecast_ev_ignored_reason"] == (
        "missing_frozen_ticker_snapshot"
    )
    assert result["metadata"]["signal_packet"]["forecast_state"] == (
        "MISSING_FROZEN_TICKER"
    )


def test_ticker_snapshot_metadata_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.orchestrator.legacy import _inject_forecast_reports

    class ForbiddenForecastingService:
        def predict(self, *_args: Any, **_kwargs: Any) -> ForecastReport:
            pytest.fail("forecast reached with mismatched snapshot provenance")

    fake_module = types.ModuleType("core.forecasting")
    fake_module.ForecastingService = ForbiddenForecastingService
    monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)
    result = _terminal_result(_snapshot("BBCA"))
    result["metadata"]["market_snapshot"]["data_hash"] = "stale-hash"

    asyncio.run(
        _inject_forecast_reports(
            [result],
            ihsg_snapshot=_snapshot("IHSG", final_close=6_200.0),
            persist_ihsg_snapshot=False,
        )
    )

    assert result["forecast_ev_ignored_reason"] == (
        "ticker_snapshot_provenance_mismatch"
    )
    assert result["metadata"]["signal_packet"]["forecast_state"] == (
        "SNAPSHOT_PROVENANCE_MISMATCH"
    )


def test_forecast_exception_clears_stale_ranking_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.orchestrator.legacy import _inject_forecast_reports

    class FailingForecastingService:
        def predict(self, *_args: Any, **_kwargs: Any) -> ForecastReport:
            raise RuntimeError("forecast failed")

    fake_module = types.ModuleType("core.forecasting")
    fake_module.ForecastingService = FailingForecastingService
    monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)
    result = _terminal_result(_snapshot("BBCA"))
    result["forecast_report"] = {"stale": True}
    result["forecast_ev_pct"] = 99.0
    result["forecast_ev_downweight"] = 1.0

    asyncio.run(
        _inject_forecast_reports(
            [result],
            ihsg_snapshot=_snapshot("IHSG", final_close=6_200.0),
            persist_ihsg_snapshot=False,
        )
    )

    assert "forecast_report" not in result
    assert "forecast_ev_pct" not in result
    assert "forecast_ev_downweight" not in result
    assert result["forecast_ev_ignored_reason"] == "forecast_capture_error"
    assert result["metadata"]["signal_packet"]["forecast_state"] == "ERROR"


def test_persisted_shared_ihsg_round_trips_with_report_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from core.orchestrator import legacy

    class FakeForecastingService:
        def predict(self, *_args: Any, **_kwargs: Any) -> ForecastReport:
            return _ready_report()

    fake_module = types.ModuleType("core.forecasting")
    fake_module.ForecastingService = FakeForecastingService
    monkeypatch.setitem(sys.modules, "core.forecasting", fake_module)
    monkeypatch.setattr(legacy, "OUTPUT_DIR", tmp_path)
    result = _terminal_result(_snapshot("BBCA"))
    ihsg_snapshot = _snapshot("IHSG", final_close=6_200.0)

    asyncio.run(
        legacy._inject_forecast_reports(
            [result],
            ihsg_snapshot=ihsg_snapshot,
            persist_ihsg_snapshot=True,
        )
    )

    artifact = result["forecast_report"]["ihsg_market_snapshot"]["artifact_path"]
    loaded = load_market_snapshot(tmp_path / artifact)
    assert loaded.snapshot_id == ihsg_snapshot.snapshot_id
    assert loaded.data_hash == ihsg_snapshot.data_hash
    ticker_artifact = result["forecast_report"]["market_snapshot"][
        "artifact_path"
    ]
    loaded_ticker = load_market_snapshot(tmp_path / ticker_artifact)
    assert loaded_ticker.snapshot_id == result["forecast_report"][
        "execution_snapshot_id"
    ]
    manifest = json.loads(
        (tmp_path / "forecast_snapshots" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert {entry["ticker"] for entry in manifest["snapshots"]} == {
        "BBCA",
        "IHSG",
    }


def test_phase5_extractor_requires_dual_snapshot_shadow_provenance() -> None:
    valid = _captured_terminal_result()
    observation = extract_shadow_observation(valid, signal_as_of=AS_OF)

    assert observation.forecast_report is not None
    assert observation.forecast_report["capture_scope"] == (
        "preflight_terminal_shadow"
    )

    missing_hash = deepcopy(valid)
    missing_hash["forecast_report"].pop("ihsg_snapshot_hash")
    with pytest.raises(ShadowProvenanceError, match="IHSG snapshot"):
        extract_shadow_observation(missing_hash, signal_as_of=AS_OF)

    stale_benchmark = deepcopy(valid)
    stale_benchmark["forecast_report"]["ihsg_feature_as_of"] = "2026-07-14"
    with pytest.raises(ShadowProvenanceError, match="ihsg_feature_as_of"):
        extract_shadow_observation(stale_benchmark, signal_as_of=AS_OF)

    stale_execution = deepcopy(valid)
    stale_execution["forecast_report"]["execution_snapshot_hash"] = "stale"
    with pytest.raises(ShadowProvenanceError, match="execution_snapshot_hash"):
        extract_shadow_observation(stale_execution, signal_as_of=AS_OF)

    nested_mismatch = deepcopy(valid)
    nested_mismatch["forecast_report"]["ihsg_market_snapshot"]["data_hash"] = (
        "stale"
    )
    with pytest.raises(ShadowProvenanceError, match="IHSG data_hash"):
        extract_shadow_observation(nested_mismatch, signal_as_of=AS_OF)

    live_authority = deepcopy(valid)
    live_authority["forecast_report"]["live_authority"] = True
    with pytest.raises(ShadowProvenanceError, match="live_authority"):
        extract_shadow_observation(live_authority, signal_as_of=AS_OF)


def test_phase5_extractor_accepts_explicit_unavailable_shadow_capture() -> None:
    unavailable = ForecastReport(
        ticker="BBCA",
        as_of=AS_OF,
        forecast_as_of=AS_OF,
        horizon_days=10,
        forecast_status="UNAVAILABLE",
        failure_reason="insufficient_data",
        decision="AVOID",
    )
    result = _captured_terminal_result(report=unavailable)

    observation = extract_shadow_observation(result, signal_as_of=AS_OF)

    assert observation.forecast_report is not None
    assert observation.forecast_report["forecast_status"] == "UNAVAILABLE"
    assert observation.forecast_report["feature_as_of"] is None
