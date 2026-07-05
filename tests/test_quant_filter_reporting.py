"""Focused tests for core/quant_filter/reporting.py._build_position_summary."""

from core.quant_filter.reporting import _build_position_summary


def _sizing_result(**position_overrides) -> dict:
    position = {
        "ticker": "BBRI",
        "rating": "BUY",
        "lot": 10,
        "shares": 1000,
        "position_value": 10_500_000.0,
        "allocation_pct": 0.20,
        "max_loss_rp": 120_000.0,
        "gap_stress_loss_rp": 291_375.0,
        "total_cost_est": 15_750.0,
    }
    position.update(position_overrides)
    return {
        "positions": [position],
        "summary": {"total_capital": 100_000_000},
    }


def test_empty_sizing_result_returns_blank() -> None:
    assert _build_position_summary(None) == ""
    assert _build_position_summary({}) == ""


def test_gap_stress_column_is_rendered() -> None:
    """V4.4: gap_stress_loss_rp must actually reach the human-readable report,
    not just exist on the position dict (this function cherry-picks columns)."""
    markdown = _build_position_summary(_sizing_result())

    assert "Gap-Stress Loss" in markdown
    assert "Rp 291.375" in markdown


def test_gap_stress_footnote_explains_the_scenario() -> None:
    markdown = _build_position_summary(_sizing_result())

    assert "ARB" in markdown
    assert "informasional" in markdown
