import json
from pathlib import Path

import pytest

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
    assert any(
        "Missing required artifact: TOP_3_SWING_TRADES.md" in error
        for error in report.errors
    )


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
    assert any(
        "Ticker TLKM is listed" in error and "status=failed" in error
        for error in report.errors
    )


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
        json.dumps({"ticker": ticker, "run_id": run_id, "has_stale_data": stale})
        + "\n",
        encoding="utf-8",
    )
    return audit_path, telemetry_path, rag_path


def _terminal_entry(
    ticker: str,
    *,
    terminal_status: str = "RR_TOO_LOW",
    reason_code: str = "rr_too_low",
    run_id: str = "run-1",
) -> dict:
    risk = _risk_governor(
        status="reject",
        sizing_allowed=False,
        reason_codes=[reason_code],
    )
    risk["ticker"] = ticker
    return {
        "ticker": ticker,
        "status": "success",
        "execution_status": "NO_TRADE",
        "verdict": {
            "ticker": ticker,
            "rating": "HOLD",
            "decision_source": "preflight",
        },
        "risk_governor": risk,
        "debate_rounds": 0,
        "agent_votes": [],
        "debate_history": [],
        "metadata": {
            "run_id": run_id,
            "decision_source": "preflight",
            "flash_calls": 0,
            "pro_calls": 0,
            "llm_calls": 0,
            "trade_setup_snapshot": {
                "status": terminal_status,
                "reason_code": reason_code,
                "debate_eligible": False,
            },
        },
        "error": None,
    }


def _reconcile_single_entry(tmp_path: Path, entry: dict):
    tmp_path.mkdir(parents=True, exist_ok=True)
    ticker = entry["ticker"]
    run_id = entry["metadata"]["run_id"]
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[entry],
        markdown="# TOP 0\n",
        latest={"ticker": ticker, "metadata": {"run_id": run_id}},
    )
    return reconcile_artifacts(
        batch_path,
        top3_path,
        latest_path,
        audit_log_path=tmp_path / "missing_audit.jsonl",
        telemetry_log_path=tmp_path / "missing_telemetry.jsonl",
        rag_evidence_log_path=tmp_path / "missing_evidence.jsonl",
    )


@pytest.mark.parametrize(
    "terminal_status",
    [
        "WAIT_FOR_PULLBACK",
        "WAIT_FOR_CONFIRMATION",
        "SHADOW_ONLY",
        "NO_MOMENTUM",
        "RR_TOO_LOW",
        "STOP_INSIDE_NOISE",
        "INSUFFICIENT_DATA",
    ],
)
def test_reconcile_artifacts_exempts_allowlisted_trade_setup_terminals(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    report = _reconcile_single_entry(
        tmp_path,
        _terminal_entry("MAPA", terminal_status=terminal_status),
    )

    assert not any(issue.code == "missing_rag_evidence" for issue in report.issues)
    assert len(report.rag_not_applicable) == 1
    record = report.rag_not_applicable[0]
    assert record.ticker == "MAPA"
    assert record.status == "NOT_APPLICABLE"
    assert record.terminal_kind == "trade_setup"
    assert record.terminal_status == terminal_status
    assert record.graph_activity is False


@pytest.mark.parametrize(
    "reason_code",
    ["critical_risk_flag", "exdate_imminent", "counter_trend_defensive"],
)
def test_reconcile_artifacts_exempts_allowlisted_pre_cio_terminals(
    tmp_path: Path,
    reason_code: str,
) -> None:
    entry = _terminal_entry("AKRA", reason_code=reason_code)
    entry["metadata"].pop("trade_setup_snapshot")
    entry["metadata"].pop("decision_source")
    entry["metadata"]["pre_cio_rejection"] = {"reason_code": reason_code}
    entry["verdict"]["decision_source"] = "risk_guard"

    report = _reconcile_single_entry(tmp_path, entry)

    assert not any(issue.code == "missing_rag_evidence" for issue in report.issues)
    assert report.rag_not_applicable[0].terminal_kind == "pre_cio"
    assert report.rag_not_applicable[0].reason_code == reason_code


def test_reconcile_artifacts_exempts_exact_budget_and_intake_markers(
    tmp_path: Path,
) -> None:
    budget_entry = _terminal_entry("AKRA")
    budget_entry["status"] = "skipped"
    budget_entry["metadata"].pop("trade_setup_snapshot")
    budget_entry["metadata"]["budget_capacity_rejection"] = {
        "reason_code": "llm_budget_capacity_exhausted"
    }
    budget_report = _reconcile_single_entry(tmp_path / "budget", budget_entry)

    intake_entry = _terminal_entry("MYOR")
    intake_entry["metadata"].pop("trade_setup_snapshot")
    intake_entry["metadata"]["candidate_intake_rejection"] = {
        "reason_code": "candidate_intake_invalid"
    }
    intake_entry["metadata"]["artifact_scope"] = "batch_only"
    intake_report = _reconcile_single_entry(tmp_path / "intake", intake_entry)

    assert budget_report.rag_not_applicable[0].terminal_kind == "budget_capacity"
    assert intake_report.rag_not_applicable[0].terminal_kind == "candidate_intake"


def test_reconcile_artifacts_fails_closed_for_ambiguous_or_graph_activity(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, dict]] = []

    unknown_status = _terminal_entry("MAPA", terminal_status="FUTURE_TERMINAL")
    cases.append(("unknown_status", unknown_status))

    graph_eligible = _terminal_entry("MAPA")
    graph_eligible["metadata"]["trade_setup_snapshot"]["debate_eligible"] = True
    cases.append(("graph_eligible", graph_eligible))

    rag_failure = _terminal_entry("MAPA")
    rag_failure["metadata"]["rag_selection_failure"] = "evidence log locked"
    cases.append(("rag_selection_failure", rag_failure))

    missing_counter = _terminal_entry("MAPA")
    missing_counter["metadata"].pop("llm_calls")
    cases.append(("missing_counter", missing_counter))

    for case_name, entry in cases:
        report = _reconcile_single_entry(tmp_path / case_name, entry)
        assert report.rag_not_applicable == []
        assert any(
            issue.code == "missing_rag_evidence" and issue.ticker == "MAPA"
            for issue in report.issues
        )


def test_reconcile_artifacts_validates_all_required_batch_tickers(
    tmp_path: Path,
) -> None:
    mapa = _terminal_entry("MAPA", run_id="batch-1")
    akra = {
        "ticker": "AKRA",
        "status": "success",
        "verdict": {"ticker": "AKRA", "rating": "HOLD"},
        "risk_governor": {**_risk_governor(), "ticker": "AKRA"},
        "metadata": {"run_id": "batch-1"},
        "debate_rounds": 1,
        "agent_votes": [{"agent": "bull"}],
        "debate_history": [{"role": "bull"}],
        "error": None,
    }
    myor = {
        **akra,
        "ticker": "MYOR",
        "verdict": {"ticker": "MYOR", "rating": "HOLD"},
        "risk_governor": {**_risk_governor(), "ticker": "MYOR"},
    }
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[mapa, akra, myor],
        markdown="# TOP 0\n",
        latest={"ticker": "MAPA", "metadata": {"run_id": "batch-1"}},
    )
    rag_path = tmp_path / "evidence_log.jsonl"
    rag_path.write_text(
        json.dumps({"ticker": "AKRA", "run_id": "batch-1"}) + "\n",
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

    missing_rag_tickers = {
        issue.ticker
        for issue in report.issues
        if issue.code == "missing_rag_evidence"
    }
    assert missing_rag_tickers == {"MYOR"}
    assert [record.ticker for record in report.rag_not_applicable] == ["MAPA"]


def test_reconcile_artifacts_all_valid(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "execution_status": "EXECUTABLE_BUY",
                "verdict": {"rating": "BUY"},
                "risk_governor": _risk_governor(),
            }
        ],
        markdown="# TOP 1\n\n## #1 - BBCA\nCurrent Price: Rp 1,000\nSignal: BUY\n",
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


def test_reconcile_artifacts_preserves_corrupt_audit_jsonl(tmp_path: Path) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "execution_status": "EXECUTABLE_BUY",
                "verdict": {"rating": "BUY"},
                "risk_governor": _risk_governor(),
            }
        ],
        markdown="# TOP 1\n\n## #1 - BBCA\nSignal: BUY\n",
        latest={"ticker": "BBCA", "metadata": {"run_id": "run-1"}},
    )
    audit_path, telemetry_path, rag_path = _write_optional_logs(tmp_path)
    audit_path.write_text(
        json.dumps({"ticker": "BBCA", "run_id": "run-1"})
        + "\n"
        + '{"ticker":"BBCA","run_id":"run-1","summary":"unterminated\n',
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

    corrupt_path = tmp_path / "audit_corrupt.jsonl"
    corrupt_records = [
        json.loads(line)
        for line in corrupt_path.read_text(encoding="utf-8").splitlines()
    ]
    assert report.corrupt_lines == 1
    assert any("line 2" in warning and "char" in warning for warning in report.warnings)
    assert corrupt_records == [
        {
            "error": "invalid_json",
            "line": 2,
            "raw": '{"ticker":"BBCA","run_id":"run-1","summary":"unterminated',
        }
    ]


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
    assert any(
        "latest_debate.json ticker TLKM is missing" in error for error in report.errors
    )


def test_reconcile_artifacts_warns_for_stale_or_missing_optional_surfaces(
    tmp_path: Path,
) -> None:
    batch_path, top3_path, latest_path = _write_artifacts(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "execution_status": "EXECUTABLE_BUY",
                "verdict": {"rating": "BUY"},
                "risk_governor": _risk_governor(),
            }
        ],
        markdown="# TOP 1\n\n## #1 - BBCA\nSignal: BUY\n",
        latest={"ticker": "BBCA", "metadata": {"run_id": "run-1"}},
    )
    rag_path = tmp_path / "evidence_log.jsonl"
    rag_path.write_text(
        json.dumps({"ticker": "BBCA", "run_id": "run-1", "has_stale_data": True})
        + "\n",
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
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "execution_status": "EXECUTABLE_BUY",
                "verdict": {"rating": "BUY"},
            }
        ],
        markdown="# TOP 1\n\n## #1 - BBCA\nSignal: BUY\n",
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
    assert any(
        issue.code == "telemetry_ticker_count_mismatch" for issue in report.issues
    )
