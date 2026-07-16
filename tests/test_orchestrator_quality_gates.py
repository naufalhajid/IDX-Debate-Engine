from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import core.orchestrator.legacy as orchestrator
from core.orchestrator.legacy import (
    MIN_CONFIDENCE_FOR_SETUP,
    SetupCoherenceError,
    apply_extreme_overvaluation_flag,
    apply_minimum_confidence_gate,
    apply_setup_coherence_gate,
    generate_top3_report,
    run_batch_debates,
    save_full_results,
    save_merged_results,
    sync_metric_aliases,
    validate_setup_coherence,
)
from services.debate_chamber import apply_staleness_penalty
from utils.ticker import PathContainmentError


def _result(confidence: float = 0.13) -> dict:
    return {
        "ticker": "AMRT",
        "verdict": {
            "ticker": "AMRT",
            "rating": "BUY",
            "confidence": confidence,
            "current_price": 1000,
            "fair_value": 1200,
            "entry_price_range": "950 - 1000",
            "target_price": 1150,
            "stop_loss": 900,
            "risk_reward_ratio": 1.5,
        },
        "conviction_score": 0.5,
        "metadata": {},
    }


def test_minimum_confidence_gate_skips_setup_generation() -> None:
    called = False
    result = _result(confidence=(MIN_CONFIDENCE_FOR_SETUP - 1) / 100)

    def generate_setup() -> None:
        nonlocal called
        called = True

    skipped = apply_minimum_confidence_gate("AMRT", result, generate_setup)

    assert skipped is True
    assert called is False
    assert result["verdict"]["rating"] == "INSUFFICIENT_DATA"
    assert result["verdict"]["action"] == "SKIP"
    assert result["verdict"]["entry_price_range"] is None
    assert result["verdict"]["target_price"] is None
    assert result["verdict"]["stop_loss"] is None
    assert result["verdict"]["risk_reward_ratio"] is None
    assert result["risk_governor"]["status"] == "reject"
    assert result["sizing"] == "Skip — confidence below threshold"
    assert "confidence_24pct_below_minimum" in result["reasons"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "current_price": 100,
                "entry_low": 95,
                "entry_high": 100,
                "target": 100,
                "stop": 90,
            },
            "target (100) does not exceed top of entry range (100)",
        ),
        (
            {
                "current_price": 100,
                "entry_low": 95,
                "entry_high": 100,
                "target": 120,
                "stop": 95,
            },
            "stop (95) is not below bottom of entry range (95)",
        ),
        (
            {
                "current_price": 112,
                "entry_low": 95,
                "entry_high": 100,
                "target": 120,
                "stop": 90,
            },
            "current price (112) is more than 10% above entry range top (100)",
        ),
        (
            {
                "current_price": 100,
                "entry_low": 95,
                "entry_high": 100,
                "target": 112,
                "stop": 90,
            },
            (
                "R/R (1.20x) below canonical threshold of 2.000x "
                "(default tier; execution regime UNSPECIFIED x1.00; "
                "user floor 2.0x)"
            ),
        ),
    ],
)
def test_validate_setup_coherence_conditions(kwargs: dict, message: str) -> None:
    with pytest.raises(SetupCoherenceError, match=re.escape(message)):
        validate_setup_coherence("TEST", **kwargs)


def test_apply_setup_coherence_gate_removes_gula_like_setup() -> None:
    result = {
        "ticker": "GULA",
        "verdict": {
            "ticker": "GULA",
            "rating": "BUY",
            "confidence": 0.62,
            "current_price": 500,
            "entry_price_range": "360 - 366",
            "target_price": 368,
            "stop_loss": 346,
            "risk_reward_ratio": 1.8,
        },
    }

    rejected = apply_setup_coherence_gate("GULA", result)

    assert rejected is True
    assert result["verdict"]["rating"] == "AVOID"
    assert result["verdict"]["entry_price_range"] is None
    assert result["verdict"]["target_price"] is None
    assert result["verdict"]["stop_loss"] is None
    assert result["risk_governor"]["status"] == "reject"
    assert any(
        "more than 10% above entry range" in reason for reason in result["reasons"]
    )


def test_coherence_uses_large_cap_threshold_for_bmri() -> None:
    validate_setup_coherence(
        ticker="BMRI",
        current_price=4130,
        entry_low=4050,
        entry_high=4100,
        target=4700,
        stop=3800,
        yf_info={"marketCap": 400_000_000_000_000},
    )


def test_coherence_still_fails_default_ticker_at_same_rr() -> None:
    with pytest.raises(SetupCoherenceError, match="canonical threshold of 2.000x"):
        validate_setup_coherence(
            ticker="CYBR",
            current_price=590,
            entry_low=580,
            entry_high=590,
            target=660,
            stop=540,
        )


def test_rr_exactly_at_large_cap_threshold_passes_coherence() -> None:
    validate_setup_coherence(
        ticker="BBRI",
        current_price=100,
        entry_low=95,
        entry_high=100,
        target=120,
        stop=90,
        yf_info={"marketCap": 50_000_000_000_000},
    )


def test_rr_below_large_cap_threshold_fails_coherence() -> None:
    with pytest.raises(
        SetupCoherenceError,
        match="canonical threshold of 2.000x",
    ):
        validate_setup_coherence(
            ticker="BMRI",
            current_price=100,
            entry_low=95,
            entry_high=100,
            target=112.9,
            stop=90,
            yf_info={"marketCap": 400_000_000_000_000},
        )


def test_apply_setup_coherence_gate_records_large_cap_threshold_note() -> None:
    result = {
        "ticker": "BMRI",
        "verdict": {
            "ticker": "BMRI",
            "rating": "BUY",
            "confidence": 0.62,
            "current_price": 4130,
            "entry_price_range": "4050 - 4100",
            "target_price": 4700,
            "stop_loss": 3800,
            "risk_reward_ratio": 2.0,
        },
        "metadata": {"market_cap_idr": 400_000_000_000_000},
    }

    rejected = apply_setup_coherence_gate("BMRI", result)

    assert rejected is False
    assert result["rr_tier"] == "large_cap"
    assert result["rr_minimum"] == 2.0
    assert result["required_rr"] == 2.0
    assert result["rr_base_minimum"] == 1.4
    assert result["rr_regime_multiplier"] == 1.0
    assert result["rr_requirement_source"] == "max_user_floor_tier_x_regime"
    assert result["rr_tier_source"] == "market_cap"
    assert result["rr_market_cap_idr"] == 400_000_000_000_000
    assert result["rr_tier_note"] == (
        "Required R/R: 2.000x (base 1.40x, Large Cap, "
        "UNSPECIFIED x1.00, user floor 2.0x)"
    )
    assert result["verdict"]["rr_tier_note"] == result["rr_tier_note"]


@pytest.mark.parametrize(
    ("age_hours", "expected"),
    [(24, 0.80), (48, 0.68), (72, 0.56), (100, 0.56)],
)
def test_apply_staleness_penalty_boundaries(age_hours: int, expected: float) -> None:
    assert apply_staleness_penalty(0.80, age_hours) == pytest.approx(expected)


def test_extreme_overvaluation_flag_adds_reason_and_note() -> None:
    result = {
        "ticker": "CYBR",
        "verdict": {
            "ticker": "CYBR",
            "current_price": 1200,
            "fair_value": 100,
            "rating": "HOLD",
            "confidence": 0.5,
        },
    }

    flagged = apply_extreme_overvaluation_flag("CYBR", result)

    assert flagged is True
    assert "EXTREME_OVERVALUATION" in result["flags"]
    assert "EXTREME_OVERVALUATION" in result["reasons"]
    assert "fair_value_model_may_not_apply" in result["reasons"]
    assert "price/FV ratio 12.0x" in result["note"]


def test_metric_aliases_and_report_labels_are_unambiguous(tmp_path: Path) -> None:
    entry = _result(confidence=0.61)
    entry["ticker"] = "INDF"
    entry["verdict"]["ticker"] = "INDF"
    entry["conviction_score"] = 0.50
    entry["trade_conviction"] = 0.50
    entry["risk_governor"] = {
        "status": "deployable",
        "sizing_allowed": True,
        "message": "ok",
    }
    sync_metric_aliases(entry)

    assert entry["model_confidence"] == pytest.approx(0.61)
    assert entry["trade_conviction"] == pytest.approx(0.50)

    report = generate_top3_report(
        [entry],
        [entry],
        path=tmp_path / "TOP_3_SWING_TRADES.md",
    )

    assert "| **Trade Setup Conviction** | 61% |" in report
    assert "| **Trade Conviction** | 50.00% |" in report
    assert "Trade Setup Conviction" in report
    assert "Trade Conviction" in report


def test_full_results_are_snapshot_and_merged_state_is_separate(tmp_path: Path) -> None:
    full_path = tmp_path / "full_batch_results.json"
    merged_path = tmp_path / "merged_batch_results.json"
    current = [{"ticker": "NEWW", "status": "success"}]
    full_path.write_text(
        '[{"ticker": "OLDD", "status": "success"}]',
        encoding="utf-8",
    )

    save_merged_results(current, path=merged_path, seed_path=full_path)
    save_full_results(current, path=full_path)

    assert "OLDD" not in full_path.read_text(encoding="utf-8")
    assert "NEWW" in full_path.read_text(encoding="utf-8")
    merged_text = merged_path.read_text(encoding="utf-8")
    assert "OLDD" in merged_text
    assert "NEWW" in merged_text


@pytest.mark.asyncio
async def test_batch_debate_records_unexpected_ticker_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeChamber:
        async def run(self, ticker: str, current_price: float = 0.0, sector: str = "") -> dict:
            if ticker == "BADX":
                raise KeyError("raw_data")
            return {
                "ticker": ticker,
                "final_verdict": json.dumps(
                    {"ticker": ticker, "rating": "HOLD", "confidence": 0.4}
                ),
                "round_count": 1,
                "raw_data": "ok",
                "debate_history": [],
                "metadata": {},
            }

    monkeypatch.setitem(orchestrator.ORCHESTRATOR_CONFIG, "batch_delay", 0)
    monkeypatch.setattr(
        "core.orchestrator.legacy.get_usage",
        lambda: {
            "pro_calls": 0,
            "pro_budget": 10,
            "flash_calls": 0,
            "flash_budget": 10,
        },
    )

    results = await run_batch_debates(
        ["BBCA", "BADX", "TLKM"],
        sector_map={"BADX": "technology"},
        chamber_factory=FakeChamber,
    )

    assert [result["ticker"] for result in results] == ["BBCA", "BADX", "TLKM"]
    failed = results[1]
    assert failed["status"] == "failed"
    assert failed["sector_key"] == "technology"
    assert "KeyError" in failed["error"]
    assert failed["metadata"]["failure_type"] == "KeyError"
    assert failed["metadata"]["failure_stage"] == "single_debate"


@pytest.mark.asyncio
async def test_batch_debates_forward_graham_fv_from_candidates() -> None:
    """Task I: run_batch_debates must extract 'Est. Fair Value (Graham)' from
    candidates_by_ticker and forward it to chamber.run() so the CIO judge can
    render the real-time valuation cross-check. Tickers without the field keep
    the backward-compatible call (graham_fv stays None); test doubles without
    the kwarg must keep working because None is never forwarded explicitly."""
    calls: dict[str, float | None] = {}

    class FakeChamber:
        async def run(
            self,
            ticker: str,
            current_price: float = 0.0,
            sector: str = "",
            graham_fv: float | None = None,
        ) -> dict:
            calls[ticker] = graham_fv
            return {
                "ticker": ticker,
                "final_verdict": json.dumps(
                    {"ticker": ticker, "rating": "HOLD", "confidence": 0.4}
                ),
                "metadata": {},
            }

    results = await run_batch_debates(
        ["BBCA", "TLKM"],
        chamber_factory=FakeChamber,
        candidates_by_ticker={
            "BBCA": {"Est. Fair Value (Graham)": 2500.0},
            "TLKM": {},
        },
    )

    assert [r["ticker"] for r in results] == ["BBCA", "TLKM"]
    assert calls["BBCA"] == 2500.0
    assert calls["TLKM"] is None


@pytest.mark.asyncio
async def test_batch_results_serialize_one_canonical_execution_regime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = {
        "rule_based_regime": "DEFENSIVE",
        "trend_regime": {
            "label": "SIDEWAYS",
            "confidence": 0.9467,
            "source": "hmm",
        },
        "volatility_regime": "HIGH",
        "execution_regime": "DEFENSIVE",
        "execution_regime_reason": "rule_based_defensive_override",
        "execution_params": {
            "consensus_threshold": 0.80,
            "max_position_pct": 0.005,
            "max_concurrent_positions": 1,
        },
    }
    hmm = {"label": "SIDEWAYS", "confidence": 0.9467}

    class FakeChamber:
        async def run(
            self,
            ticker: str,
            current_price: float = 0.0,
            sector: str = "",
        ) -> dict:
            return {
                "ticker": ticker,
                "final_verdict": json.dumps(
                    {"ticker": ticker, "rating": "HOLD", "confidence": 0.4}
                ),
                "round_count": 1,
                "raw_data": "ok",
                "debate_history": [],
                "metadata": {},
            }

    monkeypatch.setitem(orchestrator.ORCHESTRATOR_CONFIG, "batch_delay", 0)
    monkeypatch.setitem(
        orchestrator.ORCHESTRATOR_CONFIG, "regime_context", context
    )
    monkeypatch.setitem(orchestrator.ORCHESTRATOR_CONFIG, "hmm_regime", hmm)
    monkeypatch.setattr(
        "core.orchestrator.legacy.get_usage",
        lambda: {
            "pro_calls": 0,
            "pro_budget": 10,
            "flash_calls": 0,
            "flash_budget": 10,
        },
    )

    [result] = await run_batch_debates(
        ["BBCA"],
        chamber_factory=FakeChamber,
    )

    assert result["execution_regime"] == "DEFENSIVE"
    assert result["execution_regime_reason"] == "rule_based_defensive_override"
    assert result["trend_regime"]["label"] == "SIDEWAYS"
    assert result["hmm_regime"]["label"] == "SIDEWAYS"
    assert result["volatility_regime"] == "HIGH"
    assert result["trading_params"]["consensus_threshold"] == 0.80
    assert "regime" not in result
    assert "regime" not in result["metadata"]
    json.dumps(result)


def test_failure_result_and_top3_report_keep_canonical_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    context = {
        "trend_regime": {"label": "SIDEWAYS", "confidence": 0.9467},
        "volatility_regime": "HIGH",
        "execution_regime": "DEFENSIVE",
        "execution_regime_reason": "rule_based_defensive_override",
        "execution_params": {"consensus_threshold": 0.80},
    }
    monkeypatch.setitem(
        orchestrator.ORCHESTRATOR_CONFIG, "regime_context", context
    )
    monkeypatch.setitem(
        orchestrator.ORCHESTRATOR_CONFIG,
        "hmm_regime",
        {"label": "SIDEWAYS", "confidence": 0.9467},
    )

    result = orchestrator._empty_result("BACH", "insufficient bars")
    report = generate_top3_report(
        [],
        [result],
        path=tmp_path / "TOP_3_SWING_TRADES.md",
    )

    assert result["execution_regime"] == "DEFENSIVE"
    assert result["trend_regime"]["label"] == "SIDEWAYS"
    assert "regime" not in result["metadata"]
    assert "> **Execution Regime**: DEFENSIVE" in report
    assert "> **Execution Regime Reason**: rule_based_defensive_override" in report
    assert "> **Trend Regime (diagnostic)**: SIDEWAYS" in report


def test_final_batch_boundary_stamps_mock_result_canonical_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = {
        "trend_regime": {"label": "SIDEWAYS", "confidence": 0.9467},
        "volatility_regime": "NORMAL",
        "execution_regime": "DEFENSIVE",
        "execution_regime_reason": "rule_based_defensive_override",
        "execution_params": {
            "consensus_threshold": 0.80,
            "max_position_pct": 0.005,
            "max_concurrent_positions": 1,
        },
    }
    monkeypatch.setitem(
        orchestrator.ORCHESTRATOR_CONFIG, "regime_context", context
    )
    monkeypatch.setitem(
        orchestrator.ORCHESTRATOR_CONFIG,
        "hmm_regime",
        {"label": "SIDEWAYS", "confidence": 0.9467},
    )
    result = {"ticker": "BBCA", "metadata": {}}

    orchestrator._stamp_execution_regime_contract(result)

    assert result["execution_regime"] == "DEFENSIVE"
    assert result["execution_regime_reason"] == "rule_based_defensive_override"
    assert result["trend_regime"]["label"] == "SIDEWAYS"
    assert result["volatility_regime"] == "NORMAL"
    assert result["trading_params"]["consensus_threshold"] == 0.80
    assert result["metadata"]["execution_regime"] == "DEFENSIVE"
    assert "regime" not in result
    assert "regime" not in result["metadata"]


@pytest.mark.parametrize(
    ("cached_mode", "cached_regime", "expected"),
    [
        ("momentum", "DEFENSIVE", False),
        ("mean_reversion", "DEFENSIVE", True),
        ("momentum", "SIDEWAYS", True),
        ("momentum", None, True),
    ],
)
def test_candidate_cache_reuse_requires_same_execution_context(
    cached_mode,
    cached_regime,
    expected,
) -> None:
    assert (
        orchestrator._candidate_cache_context_mismatch(
            cached_mode=cached_mode,
            requested_mode="momentum",
            cached_execution_regime=cached_regime,
            execution_regime="DEFENSIVE",
        )
        is expected
    )


def test_candidate_snapshot_contract_requires_current_requested_end(
    tmp_path: Path,
) -> None:
    requested_end = date(2026, 7, 13)
    candidate_file = tmp_path / "top10_candidates.json"
    candidate = {
        "Ticker": "BBCA",
        "snapshot_id": "snapshot-id",
        "data_hash": "data-hash",
        "snapshot_path": "market_snapshots/snapshot.json.gz",
        "market_snapshot": {
            "snapshot_id": "snapshot-id",
            "data_hash": "data-hash",
            "artifact_path": "market_snapshots/snapshot.json.gz",
            "requested_end": requested_end.isoformat(),
        },
    }
    candidate_file.write_text(json.dumps([candidate]), encoding="utf-8")

    assert orchestrator._candidate_file_has_snapshot_contract(
        candidate_file,
        expected_requested_end=requested_end,
    )
    assert not orchestrator._candidate_file_has_snapshot_contract(
        candidate_file,
        expected_requested_end=date(2026, 7, 14),
    )


@pytest.mark.asyncio
async def test_batch_preflight_terminal_does_not_require_llm_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PreflightOnlyChamber:
        async def prepare_trade_setup(
            self,
            ticker: str,
            current_price: float = 0.0,
            sector: str = "",
        ) -> dict:
            return {
                "ticker": ticker,
                "current_price": 1000.0,
                "sector": sector,
                "market_data": {},
                "regime_context": {},
                "hmm_regime": {},
                "rule_regime_snapshot": None,
                "trade_setup_snapshot": {
                    "status": "RR_TOO_LOW",
                    "reason_code": "rr_too_low",
                    "reason": "R/R 0.50 below floor",
                    "debate_eligible": False,
                },
            }

        async def run(
            self,
            ticker: str,
            current_price: float = 0.0,
            sector: str = "",
            prepared_setup: dict | None = None,
        ) -> dict:
            assert prepared_setup is not None
            return {
                "ticker": ticker,
                "final_verdict": json.dumps(
                    {
                        "ticker": ticker,
                        "rating": "HOLD",
                        "confidence": 0.0,
                        "execution_status": "NO_TRADE",
                        "decision_source": "preflight",
                        "reason_codes": ["rr_too_low"],
                    }
                ),
                "round_count": 0,
                "raw_data": "",
                "debate_history": [],
                "metadata": {
                    "flash_calls": 0,
                    "pro_calls": 0,
                    "llm_calls": 0,
                },
            }

    monkeypatch.setitem(orchestrator.ORCHESTRATOR_CONFIG, "batch_delay", 0)
    monkeypatch.setattr(
        "core.orchestrator.legacy.get_usage",
        lambda: {
            "pro_calls": 0,
            "pro_budget": 0,
            "flash_calls": 0,
            "flash_budget": 0,
        },
    )

    [result] = await run_batch_debates(
        ["LSIP"],
        chamber_factory=PreflightOnlyChamber,
    )

    assert result["status"] == "success"
    assert result["debate_rounds"] == 0
    assert result["metadata"]["llm_calls"] == 0
    assert result["verdict"]["execution_status"] == "NO_TRADE"


@pytest.mark.asyncio
async def test_candidate_snapshot_artifact_is_verified_before_cache_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from utils.market_snapshot import (
        build_market_snapshot,
        candidate_snapshot_provenance,
        save_market_snapshot,
    )

    index = pd.date_range("2024-01-02", periods=400, freq="B")
    history = pd.DataFrame(
        {
            "Open": 1000.0,
            "High": 1020.0,
            "Low": 990.0,
            "Close": 1010.0,
            "Volume": 1_000_000.0,
        },
        index=index,
    )
    end = index[-1].date()
    snapshot = build_market_snapshot(
        "BBCA",
        history,
        requested_start=index[0].date(),
        requested_end=end,
        now=datetime.combine(
            end,
            datetime.min.time().replace(hour=17),
            tzinfo=ZoneInfo("Asia/Jakarta"),
        ),
    )
    relative = Path("market_snapshots") / f"{snapshot.snapshot_id}.json.gz"
    save_market_snapshot(snapshot, tmp_path / relative)
    candidate = {
        "Ticker": "BBCA",
        **candidate_snapshot_provenance(
            snapshot,
            artifact_path=relative.as_posix(),
        ),
    }
    seeded = []

    async def _capture(items):
        seeded.extend(items)

    monkeypatch.setattr(
        "utils.market_data_cache.seed_market_snapshots",
        _capture,
    )

    provenance = await orchestrator._seed_candidate_market_snapshots(
        [candidate],
        output_dir=tmp_path,
    )

    assert [item.snapshot_id for item in seeded] == [snapshot.snapshot_id]
    assert provenance["BBCA"]["data_hash"] == snapshot.data_hash
    assert provenance["BBCA"]["row_count"] == 400


@pytest.mark.asyncio
async def test_candidate_snapshot_path_must_stay_inside_output_root(
    tmp_path: Path,
) -> None:
    candidate = {
        "Ticker": "BBCA",
        "snapshot_id": "fake",
        "data_hash": "fake",
        "snapshot_path": "../escape.json.gz",
    }
    with pytest.raises(
        PathContainmentError,
        match=r"Artifact path .*(?:parent component|requested root)",
    ):
        await orchestrator._seed_candidate_market_snapshots(
            [candidate],
            output_dir=tmp_path,
        )


def test_attach_news_signal_mirrors_debate_evaluation(monkeypatch):
    # Regression: the post-debate news re-fetch used to stamp a second,
    # contradictory signal on the top level (e.g. keyword NEUTRAL/-0.2 while
    # the debate applied LLM POSITIVE/+0.05 to the verdict confidence).
    def _explode(_ticker):
        raise AssertionError(
            "must not re-fetch news when the debate already evaluated it"
        )

    monkeypatch.setattr(orchestrator.DEFAULT_FETCHER, "build_bundle", _explode)
    result = {
        "ticker": "ADMR",
        "metadata": {
            "news_overall_sentiment": "POSITIVE",
            "news_confidence_adjustment": 0.05,
            "has_breaking_news": False,
            "breaking_news_headlines": [],
        },
    }

    orchestrator._attach_news_signal("ADMR", result)

    assert result["news_sentiment"] == "POSITIVE"
    assert result["news_confidence_adjustment"] == 0.05
    assert result["has_breaking_news"] is False
    assert result["breaking_news_headlines"] == []


def test_attach_news_signal_fetches_when_debate_has_no_news(monkeypatch):
    from types import SimpleNamespace

    bundle = SimpleNamespace(
        overall_sentiment=SimpleNamespace(value="NEUTRAL"),
        confidence_adjustment=-0.2,
        confidence_adjustment_reason="stale coverage",
        has_breaking_news=False,
        items=[],
    )
    monkeypatch.setattr(
        orchestrator.DEFAULT_FETCHER, "build_bundle", lambda _ticker: bundle
    )
    result = {"ticker": "ADMR", "metadata": {}}

    orchestrator._attach_news_signal("ADMR", result)

    assert result["news_sentiment"] == "NEUTRAL"
    assert result["news_confidence_adjustment"] == -0.2


def test_pre_cio_filters_preserve_rejections_as_terminal_results() -> None:
    rejected: list[dict] = []
    candidates = [
        {
            "Ticker": "BBCA",
            "days_until_exdate": 5,
            "MA200 Context": "ABOVE",
            "market_snapshot": {
                "snapshot_id": "snap-bbca",
                "data_hash": "hash-bbca",
            },
        },
        {
            "Ticker": "LSIP",
            "Days to Ex-Date": 30,
            "MA200 Context": "BELOW",
        },
        {
            "Ticker": "BMRI",
            "Days to Ex-Date": 30,
            "MA200 Context": "ABOVE",
        },
    ]

    accepted = orchestrator._apply_pre_cio_filters(
        candidates,
        "DEFENSIVE",
        rejected_results=rejected,
    )

    assert [item["Ticker"] for item in accepted] == ["BMRI"]
    assert [item["ticker"] for item in rejected] == ["BBCA", "LSIP"]
    assert all(item["metadata"]["llm_calls"] == 0 for item in rejected)
    assert rejected[0]["metadata"]["snapshot_id"] == "snap-bbca"
    assert rejected[0]["reason_codes"] == ["exdate_imminent"]
    assert rejected[1]["reason_codes"] == ["counter_trend_defensive"]


@pytest.mark.parametrize(
    ("setup_status", "reason_code", "expected_risk_status"),
    [
        ("WAIT_FOR_PULLBACK", "price_above_entry_range", "wait_for_pullback"),
        (
            "WAIT_FOR_CONFIRMATION",
            "wait_for_momentum_confirmation",
            "watchlist_only",
        ),
    ],
)
def test_terminal_preflight_bypasses_confidence_gate_and_maps_waitlist(
    monkeypatch: pytest.MonkeyPatch,
    setup_status: str,
    reason_code: str,
    expected_risk_status: str,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "_attach_news_signal",
        lambda *_args, **_kwargs: pytest.fail("terminal preflight fetched news"),
    )
    result = {
        "ticker": "ERAA",
        "status": "success",
        "verdict": {
            "ticker": "ERAA",
            "rating": "HOLD",
            "confidence": 0.40,
            "entry_price_range": "500 - 510",
            "target_price": 560,
            "stop_loss": 480,
            "risk_reward_ratio": 2.5,
            "execution_horizon_days": 10,
            "reason_codes": [reason_code],
        },
        "metadata": {
            "decision_source": "preflight",
            "llm_calls": 0,
            "trade_setup_snapshot": {
                "status": setup_status,
                "reason_code": reason_code,
                "reason": "Deterministic setup is waiting.",
                "debate_eligible": False,
                "technical_data_status": "COMPLETE",
            },
        },
    }

    orchestrator._enhance_completed_results(
        [result],
        "phase3-test",
        fetch_news=True,
    )
    orchestrator._finalize_execution_decisions([result])

    assert result["verdict"]["rating"] == "HOLD"
    assert result["risk_governor"]["status"] == expected_risk_status
    assert result["execution_status"] == "WAITLIST"
    assert result["decision_source"] == "preflight"
    assert result["model_confidence"] is None


def test_terminal_shadow_only_setup_stays_no_trade_with_canonical_risk_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "_attach_news_signal",
        lambda *_args, **_kwargs: pytest.fail("terminal preflight fetched news"),
    )
    result = {
        "ticker": "TAPG",
        "status": "success",
        "verdict": {
            "ticker": "TAPG",
            "rating": "HOLD",
            "confidence": 0.0,
            "entry_price_range": None,
            "target_price": None,
            "stop_loss": None,
            "reason_codes": ["shadow_only_momentum_recalibration"],
        },
        "metadata": {
            "decision_source": "preflight",
            "llm_calls": 0,
            "trade_setup_snapshot": {
                "status": "SHADOW_ONLY",
                "reason_code": "shadow_only_momentum_recalibration",
                "reason": "Calibration only; live authorization is disabled.",
                "debate_eligible": False,
                "technical_data_status": "COMPLETE",
            },
        },
    }

    orchestrator._enhance_completed_results(
        [result],
        "phase4-shadow-test",
        fetch_news=True,
    )
    orchestrator._finalize_execution_decisions([result])

    assert result["risk_governor"]["status"] == "watchlist_only"
    assert result["risk_governor"]["sizing_allowed"] is False
    assert result["execution_status"] == "NO_TRADE"
    assert result["decision_source"] == "preflight"


def test_execution_funnel_counts_pre_cio_and_terminal_outcomes() -> None:
    results = [
        orchestrator._pre_cio_terminal_result(
            {"Ticker": "BBCA"},
            reason_code="exdate_imminent",
            reason="Ex-date soon.",
        ),
        {
            "ticker": "BACH",
            "execution_status": "INSUFFICIENT_DATA",
            "reason_codes": ["recent_listing_short_history"],
            "metadata": {
                "llm_calls": 0,
                "trade_setup_snapshot": {
                    "status": "INSUFFICIENT_DATA",
                    "technical_data_status": "INSUFFICIENT_DATA",
                },
            },
        },
        {
            "ticker": "BMRI",
            "execution_status": "EXECUTABLE_BUY",
            "reason_codes": [],
            "debate_rounds": 2,
            "metadata": {
                "llm_calls": 6,
                "trade_setup_snapshot": {
                    "status": "EXECUTABLE",
                    "technical_data_status": "COMPLETE",
                },
            },
            "risk_governor": {
                "status": "deployable",
                "sizing_allowed": True,
            },
            "position_sizing": {
                "lot": 2,
                "shares": 200,
                "max_loss_rp": 100_000,
            },
        },
    ]

    funnel = orchestrator.build_execution_funnel(results)

    assert funnel["counts"] == {
        "quant_candidates": 3,
        "technical_data_complete": 2,
        "trade_envelope_valid": 1,
        "debated": 1,
        "risk_deployable": 1,
        "position_sized": 1,
    }
    assert funnel["execution_status_counts"] == {
        "NO_TRADE": 1,
        "INSUFFICIENT_DATA": 1,
        "EXECUTABLE_BUY": 1,
    }


def test_execution_funnel_does_not_count_shadow_momentum_states_as_valid() -> None:
    results = [
        {
            "ticker": "TAPG",
            "execution_status": "WAITLIST",
            "metadata": {
                "llm_calls": 0,
                "trade_setup_snapshot": {
                    "status": "WAIT_FOR_CONFIRMATION",
                    "technical_data_status": "COMPLETE",
                },
            },
        },
        {
            "ticker": "GGRM",
            "execution_status": "NO_TRADE",
            "metadata": {
                "llm_calls": 0,
                "trade_setup_snapshot": {
                    "status": "SHADOW_ONLY",
                    "technical_data_status": "COMPLETE",
                },
            },
        },
    ]

    funnel = orchestrator.build_execution_funnel(results)

    assert funnel["counts"]["technical_data_complete"] == 2
    assert funnel["counts"]["trade_envelope_valid"] == 0
    assert funnel["counts"]["debated"] == 0
    assert funnel["counts"]["risk_deployable"] == 0


@pytest.mark.asyncio
async def test_forecast_setup_failure_removes_runtime_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = {
        "ticker": "BBCA",
        "status": "success",
        "verdict": {"ticker": "BBCA", "rating": "HOLD"},
        "_execution_snapshot": object(),
    }
    monkeypatch.setitem(sys.modules, "core.forecasting", None)

    await orchestrator._inject_forecast_reports([result])

    assert "_execution_snapshot" not in result
    json.dumps(result)


def test_top3_report_includes_snapshot_provenance_when_no_trade(
    tmp_path: Path,
) -> None:
    result = {
        "ticker": "BBCA",
        "verdict": {"ticker": "BBCA", "rating": "HOLD"},
        "metadata": {
            "snapshot_id": "snap-bbca-20260713",
            "data_hash": "sha256-bbca",
        },
    }

    report = orchestrator.generate_top3_report(
        [],
        [result],
        tmp_path / "TOP_3_SWING_TRADES.md",
    )

    assert "snap-bbca-20260713" in report
    assert "sha256-bbca" in report


def test_execution_finalizer_is_idempotent_for_risk_rejection() -> None:
    from app.api.result_adapter import build_execution_decision

    result = {
        "ticker": "BBCA",
        "status": "success",
        "verdict": {
            "ticker": "BBCA",
            "rating": "BUY",
            "confidence": 0.72,
            "entry_price_range": "9000 - 9100",
            "target_price": 10000,
            "stop_loss": 8600,
            "risk_reward_ratio": 2.2,
            "execution_horizon_days": 10,
            "reason_codes": [],
        },
        "risk_governor": {
            "status": "reject",
            "sizing_allowed": False,
            "reason_codes": ["rr_too_low"],
        },
    }

    orchestrator._finalize_execution_decisions([result])
    first = dict(result["execution_decision"])
    rebuilt = build_execution_decision(json.loads(json.dumps(result)))
    orchestrator._finalize_execution_decisions([result])

    assert rebuilt == first
    assert result["execution_decision"] == first
    assert first["decision_source"] == "risk_guard"
    assert first["model_rating"] == "BUY"
    assert first["model_confidence"] == pytest.approx(0.72)
    assert first["execution_status"] == "NO_TRADE"


def test_strict_ranking_backfills_with_risk_deployable_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(orchestrator.ORCHESTRATOR_CONFIG, "top_n_selection", 1)
    rejected = _result(0.95)
    rejected["ticker"] = "AAAA"
    rejected["verdict"].update(
        ticker="AAAA",
        rating="BUY",
        risk_reward_ratio=3.0,
    )
    rejected["risk_governor"] = {
        "status": "reject",
        "sizing_allowed": False,
        "reason_codes": ["setup_geometry_invalid"],
    }
    deployable = _result(0.75)
    deployable["ticker"] = "BBBB"
    deployable["verdict"].update(
        ticker="BBBB",
        rating="BUY",
        risk_reward_ratio=2.5,
    )
    deployable["risk_governor"] = {
        "status": "deployable",
        "sizing_allowed": True,
        "reason_codes": [],
    }

    selected = orchestrator.select_top_n(
        [rejected, deployable],
        require_risk_deployable=True,
    )

    assert [item["ticker"] for item in selected] == ["BBBB"]


@pytest.mark.asyncio
async def test_executable_budget_capacity_does_not_hide_terminal_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class MixedChamber:
        async def prepare_trade_setup(self, ticker, current_price=0.0, sector=""):
            terminal = ticker == "BACH"
            return {
                "ticker": ticker,
                "current_price": 500.0,
                "sector": sector,
                "market_data": {},
                "trade_setup_snapshot": {
                    "status": "INSUFFICIENT_DATA" if terminal else "EXECUTABLE",
                    "reason_code": (
                        "recent_listing_short_history"
                        if terminal
                        else "trade_envelope_executable"
                    ),
                    "reason": "short history" if terminal else "executable",
                    "debate_eligible": not terminal,
                },
            }

        async def run(self, ticker, current_price=0.0, sector="", prepared_setup=None):
            calls.append(ticker)
            snapshot = prepared_setup["trade_setup_snapshot"]
            return {
                "ticker": ticker,
                "final_verdict": json.dumps(
                    {
                        "ticker": ticker,
                        "rating": "HOLD",
                        "confidence": 0.0,
                        "decision_source": "preflight",
                        "execution_status": "INSUFFICIENT_DATA",
                        "reason_codes": [snapshot["reason_code"]],
                    }
                ),
                "round_count": 0,
                "raw_data": "",
                "metadata": {
                    "decision_source": "preflight",
                    "trade_setup_snapshot": snapshot,
                    "flash_calls": 0,
                    "pro_calls": 0,
                    "llm_calls": 0,
                },
            }

    monkeypatch.setitem(orchestrator.ORCHESTRATOR_CONFIG, "batch_delay", 0)
    results = await orchestrator.run_batch_debates(
        ["BACH", "BBCA"],
        chamber_factory=MixedChamber,
        max_executable_debates=0,
    )

    assert len(results) == 2
    by_ticker = {item["ticker"]: item for item in results}
    assert by_ticker["BACH"]["metadata"]["llm_calls"] == 0
    assert by_ticker["BBCA"]["status"] == "skipped"
    assert by_ticker["BBCA"]["verdict"]["reason_codes"] == [
        "llm_budget_capacity_exhausted"
    ]
    assert calls == ["BACH"]


def test_candidate_intake_and_critical_risk_never_disappear() -> None:
    terminal: list[dict] = []
    accepted = orchestrator._apply_candidate_intake(
        [{"Ticker": "BACH", "Current Price": 0}],
        rejected_results=terminal,
    )

    assert accepted == []
    assert len(terminal) == 1
    assert terminal[0]["execution_status"] == "INSUFFICIENT_DATA"
    assert terminal[0]["reason_codes"] == ["candidate_intake_invalid"]

    critical = {"Ticker": "BBCA", "Entry Strategy": "Critical Risk: ex-date"}
    accepted = orchestrator._apply_critical_risk_filter(
        [critical],
        rejected_results=terminal,
    )

    assert accepted == []
    assert len(terminal) == 2
    assert terminal[1]["execution_status"] == "NO_TRADE"
    assert terminal[1]["reason_codes"] == ["critical_risk_flag"]
