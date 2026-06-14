"""Backtesting engine for historical CIO verdict signals."""

from core.backtester.signal_loader import SignalRecord, scan_debate_dir, signals_to_outcomes
from core.backtester.trade_simulator import run_backtest_simulation
from core.backtester.metrics_calculator import BacktestMetrics, TierMetrics, compute_metrics
from core.backtester.report_generator import generate_markdown_report, generate_rich_table, print_report

__all__ = [
    "SignalRecord",
    "scan_debate_dir",
    "signals_to_outcomes",
    "run_backtest_simulation",
    "BacktestMetrics",
    "TierMetrics",
    "compute_metrics",
    "generate_markdown_report",
    "generate_rich_table",
    "print_report",
]
