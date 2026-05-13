import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def telemetry_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "OPS_TELEMETRY_STORAGE_PATH",
        str(tmp_path / "default" / "telemetry_log.jsonl"),
    )
    sys.modules.pop("core.ops_telemetry", None)
    module = importlib.import_module("core.ops_telemetry")
    yield module
    sys.modules.pop("core.ops_telemetry", None)


def make_metric(module, **overrides):
    data = {
        "ticker": "WIIM",
        "run_id": "run-1",
        "status": "success",
        "verdict_rating": "HOLD",
        "confidence": 0.7,
        "debate_rounds": 2,
        "duration_seconds": 30.0,
        "flash_calls": 1,
        "pro_calls": 1,
        "rag_chunks_selected": 3,
        "rag_chunks_considered": 10,
        "rag_token_estimate": 1200,
        "provider_errors": [],
        "has_stale_data": False,
        "timestamp": "2026-05-13T04:42:36+07:00",
    }
    data.update(overrides)
    return module.TickerMetric(**data)


def make_ops(module, tmp_path: Path):
    return module.OpsTelemetry(tmp_path / "telemetry" / "telemetry_log.jsonl")


def test_record_ticker_adds_to_internal_metrics_list(telemetry_module, tmp_path):
    ops = make_ops(telemetry_module, tmp_path)
    metric = make_metric(telemetry_module)

    ops.record_ticker(metric)

    assert ops._metrics == [metric]


def test_build_batch_report_success_rate(telemetry_module, tmp_path):
    ops = make_ops(telemetry_module, tmp_path)
    for ticker in ("WIIM", "ADRO", "TLKM"):
        ops.record_ticker(make_metric(telemetry_module, ticker=ticker))
    ops.record_ticker(
        make_metric(telemetry_module, ticker="BBCA", status="failed")
    )

    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    assert report.success_rate == 0.75
    assert report.total_tickers == 4
    assert report.succeeded == 3
    assert report.failed == 1


def test_build_batch_report_verdict_breakdown_counts_correctly(
    telemetry_module, tmp_path
):
    ops = make_ops(telemetry_module, tmp_path)
    ops.record_ticker(make_metric(telemetry_module, ticker="WIIM", verdict_rating="HOLD"))
    ops.record_ticker(make_metric(telemetry_module, ticker="TLKM", verdict_rating="HOLD"))
    ops.record_ticker(make_metric(telemetry_module, ticker="ADRO", verdict_rating="BUY"))
    ops.record_ticker(make_metric(telemetry_module, ticker="BBCA", verdict_rating="AVOID"))

    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    assert report.verdict_breakdown == {"BUY": 1, "HOLD": 2, "AVOID": 1}


def test_build_batch_report_avg_confidence_ignores_none_values(
    telemetry_module, tmp_path
):
    ops = make_ops(telemetry_module, tmp_path)
    ops.record_ticker(make_metric(telemetry_module, ticker="WIIM", confidence=0.8))
    ops.record_ticker(make_metric(telemetry_module, ticker="BBCA", confidence=None))

    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    assert report.avg_confidence == 0.8


def test_build_batch_report_longest_run_is_max_duration(telemetry_module, tmp_path):
    ops = make_ops(telemetry_module, tmp_path)
    ops.record_ticker(
        make_metric(telemetry_module, ticker="WIIM", duration_seconds=12.0)
    )
    ops.record_ticker(
        make_metric(telemetry_module, ticker="BBCA", duration_seconds=84.0)
    )
    ops.record_ticker(
        make_metric(telemetry_module, ticker="ADRO", duration_seconds=42.0)
    )

    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    assert report.longest_run == "BBCA"
    assert report.longest_run_seconds == 84.0


def test_build_batch_report_rag_avg_efficiency_pct(telemetry_module, tmp_path):
    ops = make_ops(telemetry_module, tmp_path)
    ops.record_ticker(
        make_metric(
            telemetry_module,
            ticker="WIIM",
            rag_chunks_selected=2,
            rag_chunks_considered=4,
        )
    )
    ops.record_ticker(
        make_metric(
            telemetry_module,
            ticker="BBCA",
            rag_chunks_selected=1,
            rag_chunks_considered=4,
        )
    )
    ops.record_ticker(
        make_metric(
            telemetry_module,
            ticker="ADRO",
            rag_chunks_selected=3,
            rag_chunks_considered=0,
        )
    )

    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    assert report.rag_avg_efficiency_pct == 37.5


def test_build_batch_report_provider_error_summary_aggregates(
    telemetry_module, tmp_path
):
    ops = make_ops(telemetry_module, tmp_path)
    ops.record_ticker(
        make_metric(telemetry_module, ticker="WIIM", provider_errors=["AUTH"])
    )
    ops.record_ticker(
        make_metric(
            telemetry_module,
            ticker="BBCA",
            provider_errors=["TIMEOUT", "AUTH"],
        )
    )

    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    assert report.provider_error_summary == {"AUTH": 2, "TIMEOUT": 1}


def test_build_batch_report_tickers_with_stale_data_only_true(
    telemetry_module, tmp_path
):
    ops = make_ops(telemetry_module, tmp_path)
    ops.record_ticker(
        make_metric(telemetry_module, ticker="WIIM", has_stale_data=True)
    )
    ops.record_ticker(
        make_metric(telemetry_module, ticker="BBCA", has_stale_data=False)
    )
    ops.record_ticker(
        make_metric(telemetry_module, ticker="ADRO", has_stale_data=True)
    )

    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    assert report.tickers_with_stale_data == ["WIIM", "ADRO"]


def test_log_report_writes_one_line_to_jsonl(telemetry_module, tmp_path):
    ops = make_ops(telemetry_module, tmp_path)
    ops.record_ticker(make_metric(telemetry_module))
    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    ops.log_report(report)

    lines = ops.storage_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert telemetry_module.BatchReport.model_validate_json(lines[0]) == report


def test_format_report_contains_header_and_run_id(telemetry_module, tmp_path):
    ops = make_ops(telemetry_module, tmp_path)
    ops.record_ticker(make_metric(telemetry_module))
    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    output = ops.format_report(report)

    assert "OPS TELEMETRY" in output
    assert "run-1" in output


def test_clear_resets_metrics_and_report_totals(telemetry_module, tmp_path):
    ops = make_ops(telemetry_module, tmp_path)
    ops.record_ticker(make_metric(telemetry_module))

    ops.clear()
    report = ops.build_batch_report("run-1", "2026-05-13T04:42:36+07:00")

    assert ops._metrics == []
    assert report.total_tickers == 0
    assert report.success_rate == 0.0
    assert report.ticker_metrics == []
