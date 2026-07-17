import importlib
import sys
import time
import types
from pathlib import Path

import pandas as pd
from rich.console import Console

from app.cli.ui.tables import (
    build_filter_results_table,
    build_recommendation_diagnostics_tables,
    build_verdict_summary_table,
)
from core.dependency_validator import DependencyCheck, DependencyCheckResult

sys.modules.setdefault("yfinance", types.SimpleNamespace())
sys.modules.setdefault("undetected_chromedriver", types.SimpleNamespace())

legacy = importlib.import_module("core.orchestrator.legacy")
BatchProgressView = legacy.BatchProgressView
CliRenderer = legacy.CliRenderer
InteractiveCLI = legacy.InteractiveCLI


def _recording_console(width: int = 120) -> Console:
    return Console(record=True, width=width, theme=legacy._CLI_THEME)


def test_filter_results_table_shows_pbv_based_for_financial_sector() -> None:
    """Bank/finance_nonbank rows must not display a Graham FV/upside they didn't earn."""
    df = pd.DataFrame(
        [
            {
                "Ticker": "BBCA",
                "Sektor Key": "bank",
                "Sektor": "Perbankan",
                "Composite Score": 65.0,
                "Current Price": 9000.0,
                "Est. Fair Value (Graham)": 12000.0,
                "Valuation Gap (%)": 33.0,
                "RSI (14)": 55.0,
                "ExDate Risk": "CLEAR",
                "Entry Strategy": "RSI Akumulasi",
                "Piotroski F-Score": 7,
            },
            {
                "Ticker": "TLKM",
                "Sektor Key": "tech",
                "Sektor": "Teknologi",
                "Composite Score": 60.0,
                "Current Price": 3000.0,
                "Est. Fair Value (Graham)": 3600.0,
                "Valuation Gap (%)": 20.0,
                "RSI (14)": 50.0,
                "ExDate Risk": "CLEAR",
                "Entry Strategy": "RSI Uptrend",
                "Piotroski F-Score": 6,
            },
        ]
    )

    table = build_filter_results_table(df, top_n=10)
    console = _recording_console(width=160)
    console.print(table)
    output = console.export_text()

    assert "PBV-based" in output
    assert "Rp3,600" in output
    assert "+33.0%" not in output


def test_cli_alerts_are_buffered_until_execution_warning_block() -> None:
    console = _recording_console()
    renderer = CliRenderer(con=console)

    renderer.render_error("ADRO.JK: possibly delisted; no price data found")

    assert console.export_text() == ""

    renderer.flush_buffered_alerts()
    output = console.export_text()

    assert "Execution Warnings" in output
    assert "ERROR" in output
    assert "possibly delisted" in output


def test_cli_alerts_summarize_provider_dns_noise() -> None:
    console = _recording_console(width=180)
    renderer = CliRenderer(con=console)

    renderer.render_error(
        "Stockbit failure classified: HTTPSConnectionPool(host='exodus.stockbit.com', "
        "port=443): Failed to resolve 'exodus.stockbit.com' ([Errno 11001] getaddrinfo failed)"
    )
    renderer.render_warning(
        "[MarketData] BREN info fetch failed: Failed to perform, curl: (6) "
        "Could not resolve host: query2.finance.yahoo.com."
    )
    renderer.render_warning(
        "$BREN.JK: possibly delisted; no price data found  (period=5d)"
    )
    renderer.render_warning("[News] BREN: BREAKING NEWS DETECTED")

    renderer.flush_buffered_alerts()

    output = console.export_text()
    assert "Provider DNS/network failures" in output
    assert "exodus.stockbit.com x1" in output
    assert "query2.finance.yahoo.com x1" in output
    assert "Market price data unavailable for BREN.JK" in output
    assert "BREAKING NEWS DETECTED" in output
    assert "HTTPSConnectionPool" not in output


def test_summary_footer_uses_explicit_metric_labels() -> None:
    console = _recording_console()
    renderer = CliRenderer(con=console)

    renderer.render_summary_footer(
        started_at=time.monotonic() - 2.0,
        regime="NORMAL",
        sizing_result={"summary": {"total_deployed": 0, "deployed_pct": 0}},
        output_files=[Path("output/full_batch_results.json")],
    )
    output = console.export_text()

    assert "Status" in output
    assert "Duration" in output
    assert "Tokens Used" in output
    assert "API Quota" in output
    assert "Market Regime" in output
    assert "Capital Deployed" in output
    assert "Value" not in output


def test_summary_footer_displays_audit_corrupt_line_count() -> None:
    console = _recording_console(width=200)
    renderer = CliRenderer(con=console)

    renderer.render_summary_footer(
        started_at=time.monotonic() - 2.0,
        regime="NORMAL",
        sizing_result={"summary": {"total_deployed": 0, "deployed_pct": 0}},
        output_files=[Path("output/full_batch_results.json")],
        corrupt_lines=2,
    )

    output = console.export_text()
    assert "Audit integrity: 2 corrupt line(s)" in output
    assert "audit_corrupt.jsonl" in output


def test_final_results_table_explains_price_validation_context() -> None:
    console = _recording_console(width=180)
    renderer = CliRenderer(con=console)
    result = {
        "ticker": "MARK",
        "verdict": {
            "rating": "HOLD",
            "confidence": 0.53,
            "risk_reward_ratio": 1.93,
            "expected_return": "+10.0%",
            "current_price": 810,
            "entry_price_range": "720 - 735",
            "target_price": 800,
            "stop_loss": 690,
        },
        "risk_governor": {
            "status": "reject",
            "reason_codes": ["upside_exhausted"],
            "current_price": 810,
            "entry_low": 720,
            "entry_high": 735,
            "target_price": 800,
            "stop_loss": 690,
        },
    }

    renderer.render_final_results_table([result], [])

    output = console.export_text()
    assert "Final Results - Trading Setup" in output
    assert "Final Results - Validation" in output
    assert "Current" in output
    assert "Entry" in output
    assert "Target" in output
    assert "Stop" in output
    assert "R/R" in output
    assert "Evidence Age" in output
    assert "Status" in output
    assert "Context" in output
    assert "Current Price" not in output
    assert "Entry Return" not in output
    assert "Final Target" not in output
    assert "Validator Reason" not in output
    assert "Rp 810" in output
    assert "+10.2% above entry" in output
    assert "tgt -1.2%" in output
    assert "upside exhausted" in output


def test_final_results_table_labels_preflight_reject_plainly() -> None:
    console = _recording_console(width=180)
    renderer = CliRenderer(con=console)
    result = {
        "ticker": "TPIA",
        "verdict": {
            "rating": "HOLD",
            "confidence": 0.40,
            "current_price": 2100,
            "entry_price_range": None,
            "target_price": None,
            "stop_loss": None,
            "risk_flags": ["PREFLIGHT_NOISE_REJECT"],
        },
        "risk_governor": {
            "status": "reject",
            "sizing_allowed": False,
            "reason_codes": ["preflight_noise_reject"],
            "message": "Setup ditolak oleh preflight noise gate sebelum debat.",
            "current_price": 2100,
        },
    }

    renderer.render_final_results_table([result], [])

    output = console.export_text()
    assert "preflight noise" in output
    assert "invalid entry range" not in output
    assert "missing target price" not in output
    assert "missing stop loss" not in output


def test_final_results_table_shows_defensive_no_sizing_as_plain_language() -> None:
    console = _recording_console(width=180)
    renderer = CliRenderer(con=console)
    result = {
        "ticker": "BBCA",
        "verdict": {
            "rating": "BUY",
            "confidence": 0.76,
            "risk_reward_ratio": 2.4,
            "expected_return": "+14.0%",
            "current_price": 1000,
            "entry_price_range": "950 - 1050",
            "target_price": 1200,
            "stop_loss": 930,
        },
        "risk_governor": {
            "status": "watchlist_only",
            "sizing_allowed": False,
            "reason_codes": ["price_inside_entry_range", "market_regime_defensive"],
            "current_price": 1000,
            "entry_low": 950,
            "entry_high": 1050,
            "target_price": 1200,
            "stop_loss": 930,
        },
    }

    renderer.render_final_results_table([result], [])

    output = console.export_text()
    assert "No Sizing" in output
    assert "defensive market" in output
    assert "market_regime_defensive" not in output


def test_verdict_summary_table_shows_defensive_execution_guard() -> None:
    console = _recording_console(width=180)
    table = build_verdict_summary_table(
        [
            {
                "ticker": "BBCA",
                "verdict": {
                    "rating": "BUY",
                    "confidence": 0.76,
                    "risk_reward_ratio": 2.4,
                    "entry_price_range": "950 - 1050",
                    "target_price": 1200,
                    "stop_loss": 930,
                    "expected_return": "+14.0%",
                },
                "risk_governor": {
                    "status": "watchlist_only",
                    "sizing_allowed": False,
                    "reason_codes": ["market_regime_defensive"],
                },
                "debate_rounds": 3,
            }
        ]
    )

    console.print(table)

    output = console.export_text()
    assert "Action" in output
    assert "No sizing: defensive market" in output
    assert "market_regime_defensive" not in output


def test_recommendation_diagnostics_keep_reject_out_of_sizing() -> None:
    console = _recording_console(width=220)
    result = {
        "ticker": "MYOR",
        "execution_decision": {
            "execution_status": "NO_TRADE",
            "decision_source": "preflight",
            "actionable": False,
        },
        "recommendation_context": {
            "contract_version": "recommendation-context-v1",
            "display_only": True,
            "full_pipeline_evaluated": False,
            "classification_basis": "trade_setup_snapshot",
            "recommendation_state": "SINGLE_GATE_REJECT",
            "actionability": "REJECT",
            "execution_eligible": False,
            "sizing_allowed": False,
            "opportunity_rank_eligible": False,
            "decision_source": "preflight",
            "blockers": [
                {
                    "gate_id": "risk_reward_floor",
                    "hard_or_soft": "SOFT",
                    "reason_code": "rr_too_low",
                    "observations": [
                        {
                            "name": "risk_reward_ratio",
                            "observed": 1.5,
                            "threshold": 2.0,
                            "comparator": ">=",
                            "unit": "x",
                            "absolute_gap": 0.5,
                            "percentage_gap": 0.25,
                        }
                    ],
                    "provenance": "fixture",
                    "detail": None,
                    "next_observable_trigger": "Wait for Rp1,745; recompute.",
                }
            ],
            "hypothetical_setup": None,
            "next_observable_trigger": "Wait for Rp1,745; recompute.",
            "evidence_quality": "COMPLETE",
            "calibration_status": "NOT_AVAILABLE",
        },
    }

    tables = build_recommendation_diagnostics_tables([result])
    assert len(tables) == 1
    console.print(tables[0])

    output = console.export_text()
    assert "Rejected Setups" in output
    assert "SINGLE_GATE_REJECT" in output
    assert "1.50x" in output
    assert ">= 2.00x" in output
    assert "0.50x / 25.0%" in output
    assert "NO" in output


def test_live_batch_progress_uses_compact_headers_and_summary_note() -> None:
    console = _recording_console(width=90)
    view = BatchProgressView(["BBCA"], con=console)
    result = {
        "ticker": "BBCA",
        "verdict": {"rating": "HOLD", "confidence": 0.47},
        "risk_governor": {
            "sizing_allowed": False,
            "status": "reject",
            "reason_codes": [
                "rating_hold",
                "low_confidence",
                "counter_trend_setup",
                "price_inside_entry_range",
            ],
        },
    }

    view.update_from_result(result)
    console.print(view._build_table())

    output = console.export_text()
    assert "Live Batch Progress" in output
    assert "Fetching" not in output
    assert "Analysis" not in output
    assert "Debating" not in output
    assert "Confidence" not in output
    assert "Note" in output
    assert "HOLD / low conf /" in output
    assert "counter-trend" in output
    assert "price_inside_entry_range" not in output


def test_live_batch_progress_does_not_label_preflight_policy_as_model_confidence() -> None:
    console = _recording_console(width=90)
    view = BatchProgressView(["LSIP"], con=console)

    view.update_from_result(
        {
            "ticker": "LSIP",
            "verdict": {
                "rating": "HOLD",
                "confidence": 0.40,
                "model_confidence": None,
                "decision_source": "preflight",
                "reason_codes": ["rr_too_low"],
            },
            "risk_governor": {
                "sizing_allowed": False,
                "status": "reject",
                "reason_codes": ["rr_too_low"],
            },
        }
    )

    assert view._rows["LSIP"]["confidence"] == "-"
    assert "low conf" not in view._rows["LSIP"]["status"].lower()


def test_preflight_hint_column_is_hidden_when_empty() -> None:
    console = _recording_console(width=100)
    cli = InteractiveCLI(con=console)
    deps = DependencyCheckResult(
        is_valid=True,
        checks={
            "db": DependencyCheck("database", True, "Database OK"),
            "llm": DependencyCheck("gemini_api_key", True, "GEMINI_API_KEY tersedia."),
        },
        failed_checks=[],
        blocking_issues=[],
    )

    cli._print_dependency_report(deps)

    assert "Hint" not in console.export_text()


def test_preflight_hint_column_appears_when_actionable() -> None:
    console = _recording_console(width=100)
    cli = InteractiveCLI(con=console)
    deps = DependencyCheckResult(
        is_valid=False,
        checks={
            "llm": DependencyCheck(
                "gemini_api_key",
                False,
                "GEMINI_API_KEY kosong.",
                hint="Isi GEMINI_API_KEY di .env.",
            ),
        },
        failed_checks=["llm"],
        blocking_issues=["llm"],
    )

    cli._print_dependency_report(deps)

    output = console.export_text()
    assert "Hint" in output
    assert "Isi GEMINI_API_KEY" in output
