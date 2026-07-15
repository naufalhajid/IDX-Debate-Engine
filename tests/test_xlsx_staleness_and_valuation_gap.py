"""
Tests for Task 5 (XLSX staleness tiering) and Task 6 (valuation disagreement).

Task 5: assess_xlsx_staleness() must return FRESH/DEGRADED/BLOCKED based on age.
Task 6: check_valuation_disagreement() must surface SIGNIFICANT/ALIGNED/NOT_COMPARABLE.
"""
from __future__ import annotations

from datetime import datetime, timedelta


from core.quant_filter.config import (
    MAX_XLSX_AGE_CALENDAR_DAYS,
    MAX_XLSX_AGE_HARD_BLOCK_DAYS,
    assess_xlsx_staleness,
)
from services.fair_value_calculator import check_valuation_disagreement
from services.fair_value_calculator import KeyStats
from utils.xlsx_adapter import XlsxDataAdapter


# ── Task 5 — assess_xlsx_staleness ────────────────────────────────────────────

def _mtime(days_ago: int) -> datetime:
    return datetime.now() - timedelta(days=days_ago)


def test_fresh_xlsx_no_action():
    result = assess_xlsx_staleness(_mtime(1))
    assert result["xlsx_staleness"] == "FRESH"
    assert result["xlsx_staleness_note"] == ""
    assert result["xlsx_age_days"] == 1


def test_degraded_xlsx_above_soft_limit():
    result = assess_xlsx_staleness(_mtime(MAX_XLSX_AGE_CALENDAR_DAYS + 1))
    assert result["xlsx_staleness"] == "DEGRADED"
    assert result["xlsx_age_days"] == MAX_XLSX_AGE_CALENDAR_DAYS + 1
    assert "dikurangi 10" in result["xlsx_staleness_note"]


def test_blocked_xlsx_above_hard_limit():
    result = assess_xlsx_staleness(_mtime(MAX_XLSX_AGE_HARD_BLOCK_DAYS + 1))
    assert result["xlsx_staleness"] == "BLOCKED"
    assert result["xlsx_age_days"] == MAX_XLSX_AGE_HARD_BLOCK_DAYS + 1
    assert "Refresh" in result["xlsx_staleness_note"]


def test_boundary_at_soft_limit_is_fresh():
    """Tepat di hari MAX_XLSX_AGE_CALENDAR_DAYS → masih FRESH (bukan DEGRADED)."""
    result = assess_xlsx_staleness(_mtime(MAX_XLSX_AGE_CALENDAR_DAYS))
    assert result["xlsx_staleness"] == "FRESH"


def test_boundary_at_hard_limit_is_degraded():
    """Tepat di hari MAX_XLSX_AGE_HARD_BLOCK_DAYS → DEGRADED (belum BLOCKED)."""
    result = assess_xlsx_staleness(_mtime(MAX_XLSX_AGE_HARD_BLOCK_DAYS))
    assert result["xlsx_staleness"] == "DEGRADED"


def test_custom_now_parameter():
    """Fungsi harus memakai `now` yang diberikan, bukan datetime.now()."""
    fixed_now = datetime(2026, 6, 19, 12, 0, 0)
    mtime = datetime(2026, 6, 13, 12, 0, 0)  # 6 hari lalu relatif ke fixed_now
    result = assess_xlsx_staleness(mtime, now=fixed_now)
    assert result["xlsx_staleness"] == "BLOCKED"
    assert result["xlsx_age_days"] == 6


def test_staleness_output_fields_always_present():
    for days in [1, MAX_XLSX_AGE_CALENDAR_DAYS + 1, MAX_XLSX_AGE_HARD_BLOCK_DAYS + 1]:
        result = assess_xlsx_staleness(_mtime(days))
        assert "xlsx_staleness" in result
        assert "xlsx_age_days" in result
        assert "xlsx_staleness_note" in result


# ── Task 6 — check_valuation_disagreement ────────────────────────────────────

def test_significant_disagreement_above_threshold():
    """Graham FV 2000, debate FV 1000 → selisih 100% → SIGNIFICANT."""
    result = check_valuation_disagreement(graham_fv=2000.0, debate_fv=1000.0)
    assert result["valuation_disagreement"] == "SIGNIFICANT"
    assert result["disagreement_pct"] == 100.0
    assert "Graham Number" in result["valuation_note"]
    assert "FairValueCalculator" in result["valuation_note"]


def test_aligned_disagreement_below_threshold():
    """Graham FV 1000, debate FV 1050 → selisih 5% → ALIGNED."""
    result = check_valuation_disagreement(graham_fv=1000.0, debate_fv=1050.0)
    assert result["valuation_disagreement"] == "ALIGNED"
    assert result["disagreement_pct"] == 5.0
    assert result["valuation_note"] == ""


def test_boundary_exactly_at_threshold_is_aligned():
    """Tepat di threshold (25%) → ALIGNED (bukan SIGNIFICANT)."""
    result = check_valuation_disagreement(graham_fv=1000.0, debate_fv=1250.0)
    assert result["valuation_disagreement"] == "ALIGNED"


def test_just_above_threshold_is_significant():
    """26% > 25% threshold → SIGNIFICANT."""
    result = check_valuation_disagreement(graham_fv=1000.0, debate_fv=1260.0)
    assert result["valuation_disagreement"] == "SIGNIFICANT"


def test_none_graham_fv_returns_not_comparable():
    result = check_valuation_disagreement(graham_fv=None, debate_fv=1000.0)
    assert result["valuation_disagreement"] == "NOT_COMPARABLE"
    assert result["disagreement_pct"] is None


def test_none_debate_fv_returns_not_comparable():
    result = check_valuation_disagreement(graham_fv=1000.0, debate_fv=None)
    assert result["valuation_disagreement"] == "NOT_COMPARABLE"


def test_zero_graham_fv_returns_not_comparable():
    result = check_valuation_disagreement(graham_fv=0.0, debate_fv=1000.0)
    assert result["valuation_disagreement"] == "NOT_COMPARABLE"


def test_disagreement_output_fields_always_present():
    for g, d in [(1000.0, 2000.0), (1000.0, 1010.0), (None, 1000.0)]:
        result = check_valuation_disagreement(graham_fv=g, debate_fv=d)
        assert "valuation_disagreement" in result
        assert "disagreement_pct" in result
        assert "valuation_note" in result


def test_custom_threshold():
    """Custom threshold 0.50 → selisih 40% masih ALIGNED."""
    result = check_valuation_disagreement(
        graham_fv=1000.0, debate_fv=1400.0, disagreement_threshold=0.50
    )
    assert result["valuation_disagreement"] == "ALIGNED"


def test_xlsx_default_sector_ignores_zero_weight_ddm(monkeypatch):
    """FIX 1 regression guard: DDM's weight is 0 for the 'default' sector bucket.

    Even when DPS data is present and fair_value_ddm() succeeds, it must show
    up in `breakdown` (it *did* compute) but must NOT enter the weighted blend
    or count toward active_method_count. This is the same "only positive-weight
    methods count as active" invariant the old, now-removed XLSX-only
    _weighted_average_with_ev() pinned locally — it's now just a property of
    the single canonical FairValueCalculator every source shares (routed here
    through the real XlsxDataAdapter.build_fair_value_report() entry point,
    not a hand-picked internal call).
    """
    adapter = object.__new__(XlsxDataAdapter)
    stats = KeyStats(
        ticker="TEST",
        current_price=100.0,
        eps_ttm=10.0,
        historical_pe_avg=10.0,  # PE FV = 100
        book_value_per_share=50.0,
        historical_pb_avg=3.0,  # PB FV = 150 (roe=0 -> no ROE/Ke cap)
        dps=5.0,
        cost_of_equity=0.10,
        growth_rate=0.04,  # DDM FV = 5/(0.10-0.04) = 83 -- computes, but weight=0
        roe=0.0,
    )
    monkeypatch.setattr(
        adapter, "extract_keystats", lambda ticker, current_price: stats
    )
    monkeypatch.setattr(adapter, "get_quality_flags", lambda ticker: {})

    report, fair_value = adapter.build_fair_value_report("TEST", 100.0)

    assert fair_value == 122.0  # (100*.50 + 150*.40) / .90 -- DDM's 83 excluded
    assert "Metode 3 DDM        : DPS Rp 5" in report  # computed, just unweighted


def test_xlsx_report_preserves_missing_fair_value_as_none(monkeypatch):
    """All methods fail on an empty KeyStats -> fair_value stays None, never 0."""
    adapter = object.__new__(XlsxDataAdapter)
    monkeypatch.setattr(
        adapter,
        "extract_keystats",
        lambda ticker, current_price: KeyStats(
            ticker=ticker,
            current_price=current_price,
        ),
    )
    monkeypatch.setattr(adapter, "get_quality_flags", lambda ticker: {})
    monkeypatch.setattr(
        adapter,
        "_build_extended_report",
        lambda **kwargs: "report",
    )

    report, fair_value = adapter.build_fair_value_report(
        "TEST",
        current_price=100.0,
    )

    assert report == "report"
    assert fair_value is None
