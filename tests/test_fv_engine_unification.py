"""FIX 1 — XLSX and API must converge on ONE canonical FV engine + ONE quality gate.

Prior to this fix, utils/xlsx_adapter.py computed PE/PB/DDM/EV-EBITDA via
FairValueCalculator's individual methods, then aggregated them with its own
_weighted_average_with_ev() — bypassing FairValueCalculator.fair_value_weighted()
and the data-quality gate in build_fair_value_payload(). These tests pin the
post-unification invariant: identical fundamentals must produce identical FV
(and identical quality-gate outcome) regardless of which path supplied them.

Tests are written at the post-extraction boundary (KeyStats already populated)
so they exercise shared valuation logic without depending on Stockbit-flat-field
parsing or xlsx sheet-reading, both of which remain legitimately path-specific.
"""
from __future__ import annotations

from dataclasses import replace

import services.fair_value_calculator as fvc
from services.fair_value_calculator import KeyStats
from utils.xlsx_adapter import XlsxDataAdapter


def _base_stats(**overrides) -> KeyStats:
    """A KeyStats profile with PE and PB both computable, DDM/DCF/EV inactive.

    eps_ttm=100 x historical_pe_avg=10 -> PE FV = 1000
    bvps=400 x historical_pb_avg=3     -> PB FV = 1200 (roe=0 -> no ROE/Ke cap)
    dps=None                            -> DDM inactive
    operating_cash_flow_ttm=0           -> DCF inactive
    sector "default" weights: pe .50 / pb .40 / ddm 0 / dcf .10
    -> weighted_fv = (1000*.50 + 1200*.40) / .90 = 1088.89 -> rounds to 1089
    """
    base = KeyStats(
        ticker="PARITY",
        eps_ttm=100.0,
        historical_pe_avg=10.0,
        book_value_per_share=400.0,
        historical_pb_avg=3.0,
        dps=None,
        roe=0.0,
        net_margin=overrides.pop("net_margin", 0.05),
        current_price=1000.0,
        raw_pe_current=10.0,
        raw_pb_current=2.5,
    )
    return replace(base, **overrides)


def _via_api(monkeypatch, stats: KeyStats) -> tuple[str, dict]:
    monkeypatch.setattr(fvc, "extract_keystats", lambda *a, **k: stats)
    monkeypatch.setattr(fvc, "extract_historical_multiples", lambda *a, **k: {})
    return fvc.build_fair_value_payload({}, stats.ticker, stats.current_price)


def _via_xlsx(monkeypatch, stats: KeyStats) -> tuple[str, float | None]:
    adapter = object.__new__(XlsxDataAdapter)
    monkeypatch.setattr(
        adapter, "extract_keystats", lambda ticker, current_price: stats
    )
    monkeypatch.setattr(adapter, "get_quality_flags", lambda ticker: {})
    return adapter.build_fair_value_report(stats.ticker, stats.current_price)


def test_xlsx_and_api_produce_identical_fv_for_identical_fundamentals(monkeypatch):
    stats_api = _base_stats()
    stats_xlsx = _base_stats()

    _, api_result = _via_api(monkeypatch, stats_api)
    _, xlsx_fv = _via_xlsx(monkeypatch, stats_xlsx)

    assert api_result["fair_value"] == 1089.0
    assert xlsx_fv == api_result["fair_value"]


def test_xlsx_and_api_resolve_identical_sector_for_real_ticker(monkeypatch):
    """Regression guard for a real, previously-divergent case.

    LSIP is hardcoded "energy" (-> mining bucket, EV/EBITDA weight 0.40) in
    xlsx's local _TICKER_SECTOR, but output/sector_cache.json classifies it
    "consumer_staples" (-> consumer bucket, no EV/EBITDA) — LSIP is a palm-oil
    plantation operator, not an energy company. Sector selects SECTOR_WEIGHTS,
    so a mismatched sector silently produces a different FV for identical
    fundamentals even after the weighting math itself is unified. XLSX must
    defer to the same canonical resolution the API path uses, not its own
    hardcoded guess.
    """
    stats_api = _base_stats(ticker="LSIP")
    stats_xlsx = _base_stats(ticker="LSIP")

    _, api_result = _via_api(monkeypatch, stats_api)
    _, xlsx_fv = _via_xlsx(monkeypatch, stats_xlsx)

    assert xlsx_fv == api_result["fair_value"]


def test_xlsx_and_api_breakdown_matches_for_identical_fundamentals(monkeypatch):
    """Not just the blended number — the per-method breakdown must agree too."""
    stats_api = _base_stats()
    stats_xlsx = _base_stats()

    monkeypatch.setattr(fvc, "extract_keystats", lambda *a, **k: stats_api)
    monkeypatch.setattr(fvc, "extract_historical_multiples", lambda *a, **k: {})
    _, api_result = fvc.build_fair_value_payload({}, "PARITY", 1000.0)

    # Route through the same internal core the XLSX report wrapper uses so we
    # can inspect the breakdown, not just the final float.
    from services.fair_value_calculator import _build_fair_value_core

    _, xlsx_result = _build_fair_value_core(
        stats_xlsx, "PARITY", 1000.0, sector="default"
    )

    assert xlsx_result["breakdown"] == api_result["breakdown"]
    assert xlsx_result["confidence"] == api_result["confidence"]


def test_xlsx_path_rejected_by_quality_gate_like_api_path(monkeypatch):
    """INDO-style bug: net_margin > 100% (revenue/net-income mismatch).

    The API path already nulls the anchor for this (build_fair_value_payload
    quality gate). Before this fix, XLSX's independent weighting had no such
    gate and would happily ship the garbage FV as an anchor.
    """
    stats_api = _base_stats(net_margin=1.31)
    stats_xlsx = _base_stats(net_margin=1.31)

    _, api_result = _via_api(monkeypatch, stats_api)
    _, xlsx_fv = _via_xlsx(monkeypatch, stats_xlsx)

    assert api_result["fair_value"] is None
    assert api_result["fv_quality_rejected"] is True
    assert "net_margin_gt_100pct" in api_result["fv_quality_reasons"]

    # XLSX must reject too — same gate, same reasons, not a silently-shipped FV.
    assert xlsx_fv is None


def test_fv_provenance_survives_quality_gate_rejection(monkeypatch):
    """FIX 5: an auditor most wants to know the source of a *rejected* FV --
    _apply_fv_quality_gate must not strip fv_provenance when it nulls the
    anchor fields. Same net_margin>100% (INDO-style) rejection as above."""
    stats = _base_stats(net_margin=1.31)

    _, result = fvc._build_fair_value_core(
        stats,
        "PARITY",
        1000.0,
        sector="default",
        financials_source="xlsx_batch",
    )

    assert result["fair_value"] is None
    assert result["fv_quality_rejected"] is True
    assert result["fv_provenance"] is not None
    assert result["fv_provenance"]["financials_source"] == "xlsx_batch"


def test_xlsx_adapter_has_no_independent_weighting_math():
    """Structural guardrail: the standalone aggregator must be gone, not just unused."""
    assert not hasattr(XlsxDataAdapter, "_weighted_average_with_ev")
