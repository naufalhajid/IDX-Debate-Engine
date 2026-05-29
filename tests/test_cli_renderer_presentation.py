import importlib
import sys
import time
import types
from pathlib import Path

from rich.console import Console

from core.dependency_validator import DependencyCheck, DependencyCheckResult

sys.modules.setdefault("yfinance", types.SimpleNamespace())
sys.modules.setdefault("undetected_chromedriver", types.SimpleNamespace())

legacy = importlib.import_module("core.orchestrator.legacy")
BatchProgressView = legacy.BatchProgressView
CliRenderer = legacy.CliRenderer
InteractiveCLI = legacy.InteractiveCLI


def _recording_console(width: int = 120) -> Console:
    return Console(record=True, width=width, theme=legacy._CLI_THEME)


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
    assert "Risk Gov" in output
    assert "Sizing" in output
    assert "Reason" in output
    assert "Current Price" not in output
    assert "Entry Return" not in output
    assert "Final Target" not in output
    assert "Validator Reason" not in output
    assert "Rp 810" in output
    assert "+10.2% above entry" in output
    assert "target -1.2%" in output
    assert "upside_exhausted" in output


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


def test_cli_renderer_is_running_helper() -> None:
    renderer = CliRenderer()
    assert not renderer.is_running()


def test_final_results_table_uses_conviction_instead_of_model_conf() -> None:
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
    assert "Conviction" in output
    assert "Model Conf" not in output


def test_live_batch_progress_headers_and_legend() -> None:
    console = _recording_console(width=90)
    view = BatchProgressView(["BBCA"], con=console)
    table = view._build_table()
    
    # Verify B is changed to DB
    columns = [col.header for col in table.columns]
    assert "DB" in columns or any("DB" in str(col) for col in columns)
    assert "Conviction" in columns or any("Conviction" in str(col) for col in columns)
    
    # Verify Legend exists in the Group
    renderable = view._build_renderable()
    assert hasattr(renderable, "renderables")
    legend_text = str(renderable.renderables[1])
    assert "Legenda:" in legend_text
    assert "[D] Data Fetching" in legend_text
    assert "[DB] AI Debate" in legend_text


def test_handle_log_record_filters_retryable_warnings() -> None:
    console = _recording_console()
    renderer = CliRenderer(con=console)
    
    # Record that should NOT be filtered
    record_valid = {
        "level": types.SimpleNamespace(name="WARNING"),
        "message": "Some valid custom warning message"
    }
    renderer.handle_log_record(record_valid)
    assert renderer.warning_count == 1
    
    # Records that SHOULD be filtered
    record_filter1 = {
        "level": types.SimpleNamespace(name="WARNING"),
        "message": "Stockbit failure classified: transient connection error"
    }
    record_filter2 = {
        "level": types.SimpleNamespace(name="WARNING"),
        "message": "Request failed: HTTPSConnectionPool(host='exodus.stockbit.com', port=443)"
    }
    record_filter3 = {
        "level": types.SimpleNamespace(name="WARNING"),
        "message": "Failed to retrieve key statistics for BBRI"
    }
    
    renderer.handle_log_record(record_filter1)
    renderer.handle_log_record(record_filter2)
    renderer.handle_log_record(record_filter3)
    
    # warning_count should still be 1 (not 4)
    assert renderer.warning_count == 1
