"""Tests for ForecastingService decision logic and graceful fallback."""
from __future__ import annotations


from core.forecasting.service import _make_decision, _error_report
from core.forecasting.schemas import ForecastReport


class TestDecisionThresholds:
    """BUY: p_target>=0.55 AND edge>=0.15 AND EV>=0.02 AND r_hat_net>=0.015."""

    def test_buy_all_thresholds_met(self):
        assert _make_decision(p_target=0.60, p_stop=0.40, ev=0.03, r_hat_net=0.02) == "BUY"

    def test_buy_exact_boundary(self):
        assert _make_decision(p_target=0.55, p_stop=0.40, ev=0.02, r_hat_net=0.015) == "BUY"

    def test_watch_when_ev_positive_but_p_target_low(self):
        assert _make_decision(p_target=0.50, p_stop=0.35, ev=0.01, r_hat_net=0.02) == "WATCH"

    def test_avoid_when_ev_zero(self):
        assert _make_decision(p_target=0.60, p_stop=0.45, ev=0.0, r_hat_net=0.02) == "AVOID"

    def test_avoid_when_ev_negative(self):
        assert _make_decision(p_target=0.40, p_stop=0.55, ev=-0.01, r_hat_net=0.02) == "AVOID"

    def test_avoid_when_any_input_none(self):
        assert _make_decision(None, None, None, None) == "AVOID"
        assert _make_decision(0.60, None, 0.03, 0.02) == "AVOID"

    def test_watch_when_edge_too_small(self):
        assert _make_decision(p_target=0.58, p_stop=0.48, ev=0.03, r_hat_net=0.02) == "WATCH"

    def test_watch_when_r_hat_net_low(self):
        assert _make_decision(p_target=0.60, p_stop=0.40, ev=0.03, r_hat_net=0.010) == "WATCH"


class TestForecastReportSchema:
    def test_error_report_is_valid_forecast_report(self):
        from datetime import date
        report = _error_report("BBCA", date.today(), 10, ["test_flag"])

        assert isinstance(report, ForecastReport)
        assert report.ticker == "BBCA"
        assert report.decision == "AVOID"
        assert "test_flag" in report.data_quality_flags
        assert report.p_target is None

    def test_forecast_report_serializes_cleanly(self):
        from datetime import date
        d = _error_report("TLKM", date.today(), 10, []).model_dump()

        assert d["ticker"] == "TLKM"
        assert isinstance(d["model_votes"], list)
        assert isinstance(d["data_quality_flags"], list)


class TestGracefulFallback:
    def test_import_core_forecasting_succeeds_without_optional_deps(self):
        from core.forecasting import ForecastingService, ForecastReport  # noqa: F401
        assert ForecastingService is not None

    def test_inject_forecast_reports_smoke_empty_list(self):
        """_inject_forecast_reports on empty list must not raise."""
        import asyncio
        from core.orchestrator.legacy import _inject_forecast_reports

        asyncio.run(_inject_forecast_reports([]))

    def test_inject_forecast_skips_non_success_results(self):
        """Results without verdict or with error status are skipped silently."""
        import asyncio
        from core.orchestrator.legacy import _inject_forecast_reports

        results = [
            {"ticker": "BBCA", "status": "error", "verdict": None},
            {"ticker": "BBRI", "status": "success", "verdict": None},
        ]
        asyncio.run(_inject_forecast_reports(results))
        assert "forecast_report" not in results[0]
        assert "forecast_report" not in results[1]
