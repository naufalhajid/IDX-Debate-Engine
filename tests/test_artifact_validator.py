import json
from pathlib import Path

from core.artifact_validator import reconcile_artifacts, validate_artifacts


def _risk_governor(
    *,
    status: str = "deployable",
    sizing_allowed: bool = True,
    reason_codes: list[str] | None = None,
) -> dict:
    return {
        "ticker": "BBCA",
        "status": status,
        "sizing_allowed": sizing_allowed,
        "reason_codes": reason_codes or ["price_inside_entry_range"],
        "message": "Harga sekarang berada di zona entry.",
        "current_price": 1000,
        "entry_low": 950,
        "entry_high": 1050,
        "target_price": 1150,
        "stop_loss": 930,
    }


def _write_artifacts(
    tmp_path: Path,
    *,
    batch: list[dict],
    markdown: str,
    latest: dict,
) -> tuple[Path, Path, Path]:
    batch_path = tmp_path / "full_batch_results.json"
    top3_path = tmp_path / "TOP_3_SWING_TRADES.md"
    latest_path = tmp_path / "latest_debate.json"

    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    top3_path.write_text(markdown, encoding="utf-8")
    latest_path.write_text(json.dumps(latest), encoding="utf-8")

    return batch_path, top3_path, latest_path


def test_validate_artifacts_all_valid(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {"ticker": "BBCA", "status": "ok", "risk_governor": _risk_governor()},
            {
                "ticker": "BBRI",
                "status": "ok",
                "risk_governor": {**_risk_governor(), "ticker": "BBRI"},
            },
        ],
        markdown="# TOP 3\n\n## #1 - BBCA\n\n## #2 - BBRI\n",
        latest={"ticker": "BBCA", "verdict": {"rating": "BUY"}},
    )

    report = validate_artifacts(batch_path, top3_path, latest_path)

    assert report.valid is True
    assert report.errors == []
    assert report.warnings == []


def test_validate_artifacts_missing_file(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[{"ticker": "BBCA", "status": "ok", "risk_governor": _risk_governor()}],
        markdown="# TOP 3\n\n## #1 - BBCA\n",
        latest={"ticker": "BBCA"},
    )
    top3_path.unlink()

    report = validate_artifacts(batch_path, top3_path, latest_path)

    assert report.valid is False
    assert any("Missing required artifact: TOP_3_SWING_TRADES.md" in error for error in report.errors)


def test_validate_artifacts_markdown_ticker_failed_in_json(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {"ticker": "BBCA", "status": "ok", "risk_governor": _risk_governor()},
            {"ticker": "TLKM", "status": "failed"},
        ],
        markdown="# TOP 3\n\n## #1 - BBCA\n\n## #2 - TLKM\n",
        latest={"ticker": "BBCA"},
    )

    report = validate_artifacts(batch_path, top3_path, latest_path)

    assert report.valid is False
    assert any("Ticker TLKM is listed" in error and "status=failed" in error for error in report.errors)


def _write_optional_logs(
    tmp_path: Path,
    *,
    ticker: str = "BBCA",
    run_id: str = "run-1",
    stale: bool = False,
) -> tuple[Path, Path, Path]:
    audit_path = tmp_path / "audit_log.jsonl"
    telemetry_path = tmp_path / "telemetry_log.jsonl"
    rag_path = tmp_path / "evidence_log.jsonl"
    audit_path.write_text(
        json.dumps({"ticker": ticker, "run_id": run_id}) + "\n",
        encoding="utf-8",
    )
    telemetry_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "ticker_metrics": [{"ticker": ticker, "status": "success"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rag_path.write_text(
        json.dumps(
            {"ticker": ticker, "run_id": run_id, "has_stale_data": stale}
        )
        + "\n",
        encoding="utf-8",
    )
    return audit_path, telemetry_path, rag_path


def test_reconcile_artifacts_all_valid(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "verdict": {"rating": "BUY"},
                "risk_governor": _risk_governor(),
            }
        ],
        markdown="# TOP 3\n\n## #1 - BBCA\nCurrent Price: Rp 1,000\nSignal: BUY\n",
        latest={"ticker": "BBCA", "metadata": {"run_id": "run-1"}},
    )
    audit_path, telemetry_path, rag_path = _write_optional_logs(tmp_path)

    report = reconcile_artifacts(
        batch_path,
        top3_path,
        latest_path,
        audit_log_path=audit_path,
        telemetry_log_path=telemetry_path,
        rag_evidence_log_path=rag_path,
    )

    assert report.valid is True
    assert report.errors == []
    assert report.warnings == []
    assert report.latest_ticker == "BBCA"
    assert report.latest_run_id == "run-1"
    assert report.surfaces["audit"] is True


def test_reconcile_artifacts_flags_failed_promoted_ticker(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {
                "ticker": "TLKM",
                "status": "failed",
                "verdict": {"rating": "BUY"},
                "risk_governor": {**_risk_governor(), "ticker": "TLKM"},
            }
        ],
        markdown="# TOP 3\n\n## #1 - TLKM\nSignal: BUY\n",
        latest={"ticker": "TLKM", "metadata": {"run_id": "run-1"}},
    )
    audit_path, telemetry_path, rag_path = _write_optional_logs(tmp_path, ticker="TLKM")

    report = reconcile_artifacts(
        batch_path,
        top3_path,
        latest_path,
        audit_log_path=audit_path,
        telemetry_log_path=telemetry_path,
        rag_evidence_log_path=rag_path,
    )

    assert report.valid is False
    assert any(issue.code == "failed_ticker_promoted" for issue in report.issues)


def test_reconcile_artifacts_flags_latest_missing_from_batch(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[{"ticker": "BBCA", "status": "ok", "risk_governor": _risk_governor()}],
        markdown="# TOP 3\n\n## #1 - BBCA\n",
        latest={"ticker": "TLKM", "metadata": {"run_id": "run-1"}},
    )
    audit_path, telemetry_path, rag_path = _write_optional_logs(tmp_path, ticker="TLKM")

    report = reconcile_artifacts(
        batch_path,
        top3_path,
        latest_path,
        audit_log_path=audit_path,
        telemetry_log_path=telemetry_path,
        rag_evidence_log_path=rag_path,
    )

    assert report.valid is False
    assert any("latest_debate.json ticker TLKM is missing" in error for error in report.errors)


def test_reconcile_artifacts_warns_for_stale_or_missing_optional_surfaces(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "verdict": {"rating": "BUY"},
                "risk_governor": _risk_governor(),
            }
        ],
        markdown="# TOP 3\n\n## #1 - BBCA\nSignal: BUY\n",
        latest={"ticker": "BBCA", "metadata": {"run_id": "run-1"}},
    )
    rag_path = tmp_path / "evidence_log.jsonl"
    rag_path.write_text(
        json.dumps({"ticker": "BBCA", "run_id": "run-1", "has_stale_data": True}) + "\n",
        encoding="utf-8",
    )

    report = reconcile_artifacts(
        batch_path,
        top3_path,
        latest_path,
        audit_log_path=tmp_path / "missing_audit.jsonl",
        telemetry_log_path=tmp_path / "missing_telemetry.jsonl",
        rag_evidence_log_path=rag_path,
    )

    assert report.valid is True
    assert any(issue.code == "missing_audit_packet" for issue in report.issues)
    assert any(issue.code == "missing_telemetry" for issue in report.issues)
    assert any(issue.code == "stale_evidence" for issue in report.issues)


def test_validate_artifacts_rejects_position_sizing_on_non_deployable_ticker(
    tmp_path: Path,
) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "risk_governor": _risk_governor(
                    status="wait_for_pullback",
                    sizing_allowed=False,
                    reason_codes=["price_above_entry_range"],
                ),
                "position_sizing": {"lot": 1, "entry_price": 1100},
            }
        ],
        markdown="# TOP 3\n\n## #1 - BBCA\n| **Sizing Allowed** | No |\n",
        latest={"ticker": "BBCA"},
    )

    report = validate_artifacts(batch_path, top3_path, latest_path)

    assert report.valid is False
    assert any("sized_non_deployable" in error for error in report.errors)


def test_reconcile_artifacts_warns_when_promoted_ticker_has_no_risk_governor(
    tmp_path: Path,
) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[{"ticker": "BBCA", "status": "ok", "verdict": {"rating": "BUY"}}],
        markdown="# TOP 3\n\n## #1 - BBCA\nSignal: BUY\n",
        latest={"ticker": "BBCA", "metadata": {"run_id": "run-1"}},
    )
    audit_path, telemetry_path, rag_path = _write_optional_logs(tmp_path)

    report = reconcile_artifacts(
        batch_path,
        top3_path,
        latest_path,
        audit_log_path=audit_path,
        telemetry_log_path=telemetry_path,
        rag_evidence_log_path=rag_path,
    )

    assert report.valid is True
    assert any(issue.code == "missing_risk_governor" for issue in report.issues)


def test_validate_artifacts_rejects_batch_timestamp_mismatch(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "metadata": {"batch_timestamp": "20260601_090000"},
                "risk_governor": _risk_governor(),
            }
        ],
        markdown=(
            "# TOP 1\n\n"
            "> **Batch Timestamp**: 20260601_091500\n"
            "> **Stocks Debated**: 1 | **Eligible (BUY/STRONG_BUY)**: 1 | **Selected**: 1\n\n"
            "## #1 - BBCA\n"
        ),
        latest={
            "ticker": "BBCA",
            "metadata": {"batch_timestamp": "20260601_090000"},
        },
    )

    report = validate_artifacts(batch_path, top3_path, latest_path)

    assert report.valid is False
    assert any("run_scope_batch_timestamp_mismatch" in error for error in report.errors)


def test_reconcile_artifacts_rejects_telemetry_count_mismatch(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "metadata": {
                    "batch_timestamp": "20260601_090000",
                    "run_id": "run-1",
                },
                "verdict": {"rating": "BUY"},
                "risk_governor": _risk_governor(),
            }
        ],
        markdown=(
            "# TOP 1\n\n"
            "> **Batch Timestamp**: 20260601_090000\n"
            "> **Run ID**: run-1\n"
            "> **Stocks Debated**: 1 | **Eligible (BUY/STRONG_BUY)**: 1 | **Selected**: 1\n\n"
            "## #1 - BBCA\nSignal: BUY\n"
        ),
        latest={
            "ticker": "BBCA",
            "metadata": {
                "batch_timestamp": "20260601_090000",
                "run_id": "run-1",
            },
        },
    )
    audit_path, _, rag_path = _write_optional_logs(tmp_path)
    telemetry_path = tmp_path / "telemetry_log.jsonl"
    telemetry_path.write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "batch_timestamp": "20260601_090000",
                "total_tickers": 2,
                "ticker_metrics": [{"ticker": "BBCA", "status": "success"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = reconcile_artifacts(
        batch_path,
        top3_path,
        latest_path,
        audit_log_path=audit_path,
        telemetry_log_path=telemetry_path,
        rag_evidence_log_path=rag_path,
    )

    assert report.valid is False
    assert any(issue.code == "telemetry_ticker_count_mismatch" for issue in report.issues)
