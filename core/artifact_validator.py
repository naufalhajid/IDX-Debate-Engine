"""Read-only validation for batch output artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.settings import settings
from utils.logger_config import logger


class ValidationReport(BaseModel):
    """Validation result for generated debate artifacts."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str]
    warnings: list[str]


class ReconciliationIssue(BaseModel):
    """Single finding from cross-artifact reconciliation."""

    model_config = ConfigDict(extra="forbid")

    source: str
    code: str
    severity: Literal["error", "warning"]
    message: str
    ticker: str | None = None


class RagNotApplicableRecord(BaseModel):
    """Auditable proof that a batch result never entered the RAG graph path."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    run_id: str | None = None
    status: Literal["NOT_APPLICABLE"] = "NOT_APPLICABLE"
    terminal_kind: Literal[
        "trade_setup",
        "pre_cio",
        "budget_capacity",
        "candidate_intake",
    ]
    terminal_status: str | None = None
    reason_code: str
    graph_activity: Literal[False] = False


class ReconciliationReport(BaseModel):
    """Combined truth report across batch, markdown, debate, and logs."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    issues: list[ReconciliationIssue] = Field(default_factory=list)
    validation_report: ValidationReport
    consistency_checked: bool
    checked_tickers: list[str] = Field(default_factory=list)
    latest_ticker: str | None = None
    latest_run_id: str | None = None
    surfaces: dict[str, bool] = Field(default_factory=dict)
    corrupt_lines: int = 0
    rag_not_applicable: list[RagNotApplicableRecord] = Field(default_factory=list)


_TOP_PICK_HEADING = re.compile(
    r"^##\s+.*?#\d+\s*[-\u2013\u2014]\s*([A-Z][A-Z0-9]{1,5})\b",
    re.MULTILINE,
)
DEFAULT_AUDIT_LOG_PATH = settings.audit_log_path
DEFAULT_TELEMETRY_LOG_PATH = settings.ops_telemetry_path
DEFAULT_RAG_EVIDENCE_LOG_PATH = settings.rag_evidence_log_path
_TRADE_SETUP_RAG_NOT_APPLICABLE_STATUSES = frozenset(
    {
        "WAIT_FOR_PULLBACK",
        "WAIT_FOR_CONFIRMATION",
        "SHADOW_ONLY",
        "NO_MOMENTUM",
        "RR_TOO_LOW",
        "STOP_INSIDE_NOISE",
        "INSUFFICIENT_DATA",
        "UNKNOWN_REJECT",
    }
)
_PRE_CIO_RAG_NOT_APPLICABLE_CODES = frozenset(
    {
        "critical_risk_flag",
        "exdate_imminent",
        "counter_trend_defensive",
    }
)
_BUDGET_RAG_NOT_APPLICABLE_CODE = "llm_budget_capacity_exhausted"
_CANDIDATE_INTAKE_RAG_NOT_APPLICABLE_CODE = "candidate_intake_invalid"
_RAG_ACTIVITY_METADATA_KEYS = frozenset(
    {
        "rag_selection_failure",
        "rag_chunks_selected",
        "rag_chunks_considered",
        "rag_citation_ids",
    }
)


def validate_artifacts(
    batch_json_path: str | Path,
    top3_md_path: str | Path,
    latest_json_path: str | Path,
) -> ValidationReport:
    """Validate artifact presence and cross-file ticker consistency."""
    errors: list[str] = []
    warnings: list[str] = []

    batch_path = Path(batch_json_path)
    top3_path = Path(top3_md_path)
    latest_path = Path(latest_json_path)

    batch_text = _read_required_text(batch_path, "full_batch_results.json", errors)
    top3_text = _read_required_text(top3_path, "TOP_3_SWING_TRADES.md", errors)
    latest_text = _read_required_text(latest_path, "latest_debate.json", errors)

    batch_results = _load_json(batch_text, batch_path, errors)
    if batch_results is not None and not isinstance(batch_results, list):
        errors.append(f"{batch_path} must contain a JSON list.")
        batch_results = None

    latest_result = _load_json(latest_text, latest_path, errors)
    if latest_result is not None and not isinstance(latest_result, dict):
        errors.append(f"{latest_path} must contain a JSON object.")
        latest_result = None

    batch_by_ticker = _index_batch_results(batch_results or [], errors, warnings)
    _validate_latest_ticker(latest_result, latest_path, batch_by_ticker, errors)
    _validate_markdown_tickers(top3_text, top3_path, batch_by_ticker, errors)
    _validate_batch_risk_governor(batch_by_ticker, errors, warnings)
    _validate_recommendation_contexts(batch_by_ticker, errors)
    _validate_basic_run_scope(batch_results or [], top3_text, latest_result, errors)

    return ValidationReport(valid=not errors, errors=errors, warnings=warnings)


def reconcile_artifacts(
    batch_json_path: str | Path,
    top3_md_path: str | Path,
    latest_json_path: str | Path,
    *,
    audit_log_path: str | Path | None = DEFAULT_AUDIT_LOG_PATH,
    telemetry_log_path: str | Path | None = DEFAULT_TELEMETRY_LOG_PATH,
    rag_evidence_log_path: str | Path | None = DEFAULT_RAG_EVIDENCE_LOG_PATH,
) -> ReconciliationReport:
    """Reconcile generated output surfaces into one structured truth report."""
    batch_path = Path(batch_json_path)
    top3_path = Path(top3_md_path)
    latest_path = Path(latest_json_path)
    audit_path = Path(audit_log_path or DEFAULT_AUDIT_LOG_PATH)
    telemetry_path = Path(telemetry_log_path or DEFAULT_TELEMETRY_LOG_PATH)
    rag_path = Path(rag_evidence_log_path or DEFAULT_RAG_EVIDENCE_LOG_PATH)

    validation_report = validate_artifacts(batch_path, top3_path, latest_path)
    errors = list(validation_report.errors)
    warnings = list(validation_report.warnings)
    corrupt_audit_lines: list[dict[str, Any]] = []
    issues: list[ReconciliationIssue] = [
        ReconciliationIssue(
            source="artifact_validator",
            code="validation_error",
            severity="error",
            message=message,
        )
        for message in validation_report.errors
    ]
    issues.extend(
        ReconciliationIssue(
            source="artifact_validator",
            code="validation_warning",
            severity="warning",
            message=message,
        )
        for message in validation_report.warnings
    )

    consistency_checked = False
    checked_tickers: list[str] = []
    try:
        from core.report_consistency import check_consistency

        consistency_report = check_consistency(batch_path, top3_path)
        consistency_checked = True
        checked_tickers = consistency_report.checked_tickers
        for item in consistency_report.inconsistencies:
            message = (
                f"{item.type.value}: markdown={item.markdown_value}, "
                f"json={item.json_value}"
            )
            issue = ReconciliationIssue(
                source="report_consistency",
                code=item.type.value,
                severity=item.severity,
                message=message,
                ticker=item.ticker,
            )
            issues.append(issue)
            if item.severity == "error":
                errors.append(f"{item.ticker}: {message}")
            else:
                warnings.append(f"{item.ticker}: {message}")
    except Exception as exc:
        message = f"Report consistency check failed: {exc}"
        errors.append(message)
        issues.append(
            ReconciliationIssue(
                source="report_consistency",
                code="check_failed",
                severity="error",
                message=message,
            )
        )

    latest_payload = _load_json(
        _read_required_text(latest_path, "latest_debate.json", []),
        latest_path,
        [],
    )
    latest_ticker = (
        _extract_ticker(latest_payload) if isinstance(latest_payload, dict) else None
    )
    latest_run_id = (
        _extract_run_id(latest_payload) if isinstance(latest_payload, dict) else None
    )

    batch_payload = _load_json(
        _read_required_text(batch_path, "full_batch_results.json", []),
        batch_path,
        [],
    )
    batch_results = batch_payload if isinstance(batch_payload, list) else []
    rag_targets, rag_not_applicable = _rag_validation_plan(
        batch_results,
        latest_ticker=latest_ticker,
        latest_run_id=latest_run_id,
    )
    top3_text = _read_required_text(top3_path, "TOP_3_SWING_TRADES.md", [])
    telemetry_records = (
        _load_jsonl(telemetry_path, "telemetry_log.jsonl", warnings)
        if telemetry_path.exists()
        else []
    )
    _reconcile_run_scope(
        batch_results=batch_results,
        markdown_text=top3_text,
        latest_payload=latest_payload if isinstance(latest_payload, dict) else None,
        telemetry_records=telemetry_records,
        issues=issues,
        errors=errors,
        warnings=warnings,
    )

    surfaces = {
        "batch": batch_path.exists(),
        "top3": top3_path.exists(),
        "latest": latest_path.exists(),
        "audit": bool(audit_path and audit_path.exists()),
        "telemetry": bool(telemetry_path and telemetry_path.exists()),
        "rag_evidence": bool(rag_path and rag_path.exists()),
    }

    if latest_ticker is not None:
        _reconcile_audit_log(
            audit_path,
            latest_ticker,
            latest_run_id,
            issues,
            warnings,
            corrupt_audit_lines,
        )
        _reconcile_telemetry_log(
            telemetry_path,
            latest_ticker,
            latest_run_id,
            issues,
            warnings,
        )
    else:
        message = (
            "latest_debate.json has no ticker; optional log surfaces cannot be linked."
        )
        warnings.append(message)
        issues.append(
            ReconciliationIssue(
                source="latest_debate",
                code="missing_latest_ticker",
                severity="warning",
                message=message,
            )
        )

    if rag_targets:
        rag_records = _load_jsonl(rag_path, "evidence_log.jsonl", warnings)
        for ticker, run_id in rag_targets:
            _reconcile_rag_records(
                rag_records,
                ticker,
                run_id,
                issues,
                warnings,
            )

    return ReconciliationReport(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        issues=issues,
        validation_report=validation_report,
        consistency_checked=consistency_checked,
        checked_tickers=checked_tickers,
        latest_ticker=latest_ticker,
        latest_run_id=latest_run_id,
        surfaces=surfaces,
        corrupt_lines=len(corrupt_audit_lines),
        rag_not_applicable=rag_not_applicable,
    )


def _read_required_text(path: Path, label: str, errors: list[str]) -> str | None:
    if not path.exists():
        errors.append(f"Missing required artifact: {label} at {path}")
        return None
    if not path.is_file():
        errors.append(f"Required artifact is not a file: {label} at {path}")
        return None
    if path.stat().st_size == 0:
        errors.append(f"Required artifact is empty: {label} at {path}")
        return None
    return path.read_text(encoding="utf-8")


def _load_json(text: str | None, path: Path, errors: list[str]) -> Any | None:
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append(f"{path} is not valid JSON: {exc}")
        return None


def _load_jsonl(
    path: Path | None,
    label: str,
    warnings: list[str],
    corrupt_lines: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if path is None:
        return []
    if not path.exists():
        warnings.append(f"Missing optional artifact surface: {label} at {path}")
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            # FIX: ISSUE 4 — Preserve corrupt audit JSONL lines for diagnosis.
            preview = line[:80]
            message = (
                f"{label} line {line_number} is not valid JSON at char {exc.pos}: "
                f"{exc}; raw={preview!r}"
            )
            logger.warning(message)
            warnings.append(message)
            if label == "audit_log.jsonl" and corrupt_lines is not None:
                corrupt_record = {
                    "error": "invalid_json",
                    "line": line_number,
                    "raw": line,
                }
                corrupt_lines.append(corrupt_record)
                _write_corrupt_audit_line(path, corrupt_record)
            continue
        if isinstance(payload, dict):
            records.append(payload)
        else:
            warnings.append(f"{label} line {line_number} is not a JSON object.")
    return records


def _write_corrupt_audit_line(path: Path, record: dict[str, Any]) -> None:
    output_dir = path.parent.parent if path.parent.name == "audit" else path.parent
    corrupt_path = output_dir / "audit_corrupt.jsonl"
    try:
        corrupt_path.parent.mkdir(parents=True, exist_ok=True)
        with corrupt_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning(f"Failed to write {corrupt_path}: {exc}")


def _index_batch_results(
    batch_results: list[Any],
    errors: list[str],
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(batch_results):
        if not isinstance(item, dict):
            warnings.append(f"Batch entry #{index} is not an object; skipping.")
            continue
        ticker = _extract_ticker(item)
        if ticker is None:
            warnings.append(f"Batch entry #{index} has no ticker; skipping.")
            continue
        if ticker in by_ticker:
            errors.append(f"Duplicate ticker in full_batch_results.json: {ticker}")
            continue
        by_ticker[ticker] = item
    return by_ticker


def _validate_latest_ticker(
    latest_result: dict[str, Any] | None,
    latest_path: Path,
    batch_by_ticker: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    if latest_result is None:
        return
    ticker = _extract_ticker(latest_result)
    if ticker is None:
        errors.append(f"{latest_path} does not contain a resolvable ticker.")
        return
    if ticker not in batch_by_ticker:
        errors.append(
            f"latest_debate.json ticker {ticker} is missing from full_batch_results.json."
        )


def _validate_markdown_tickers(
    markdown_text: str | None,
    top3_path: Path,
    batch_by_ticker: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    if markdown_text is None:
        return
    tickers = _extract_markdown_tickers(markdown_text)
    for ticker in tickers:
        batch_entry = batch_by_ticker.get(ticker)
        if batch_entry is None:
            errors.append(
                f"Ticker {ticker} mentioned in {top3_path} is missing from full_batch_results.json."
            )
            continue
        if str(batch_entry.get("status", "")).lower() == "failed":
            errors.append(
                f"Ticker {ticker} is listed in {top3_path} but has status=failed in full_batch_results.json."
            )


def _validate_batch_risk_governor(
    batch_by_ticker: dict[str, dict[str, Any]],
    errors: list[str],
    warnings: list[str],
) -> None:
    for ticker, entry in batch_by_ticker.items():
        risk = entry.get("risk_governor")
        if not isinstance(risk, dict):
            continue
        if risk.get("sizing_allowed") is False and _has_position_sizing(entry):
            errors.append(
                f"sized_non_deployable: {ticker} has position_sizing but "
                "risk_governor.sizing_allowed=False."
            )
        reason_codes = {
            str(code) for code in risk.get("reason_codes", []) if code is not None
        }
        if "upside_exhausted" in reason_codes:
            warnings.append(
                f"upside_exhausted: {ticker} target_price is not above current_price."
            )


def _validate_recommendation_contexts(
    batch_by_ticker: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    """Validate the display contract without granting it execution authority."""

    from schemas.debate import RecommendationContext

    for ticker, entry in batch_by_ticker.items():
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        top_level = entry.get("recommendation_context")
        metadata_level = metadata.get("recommendation_context")
        if top_level is not None and metadata_level is not None:
            if top_level != metadata_level:
                errors.append(
                    f"recommendation_context_drift: {ticker} top-level and "
                    "metadata projections differ."
                )
                continue
        raw = top_level if top_level is not None else metadata_level
        if raw is None:
            continue
        if not isinstance(raw, dict):
            errors.append(
                f"recommendation_context_malformed: {ticker} context is not an object."
            )
            continue
        try:
            context = RecommendationContext.model_validate(raw)
        except Exception as exc:
            errors.append(
                f"recommendation_context_malformed: {ticker} failed schema: {exc}"
            )
            continue

        decision = entry.get("execution_decision")
        decision = decision if isinstance(decision, dict) else {}
        if decision:
            actionable = bool(decision.get("actionable"))
            if context.execution_eligible is not actionable:
                errors.append(
                    f"recommendation_execution_mismatch: {ticker} context "
                    "execution_eligible disagrees with execution_decision.actionable."
                )
            expected_actionability = "PASS" if actionable else (
                "ABSTAIN"
                if str(decision.get("execution_status") or "").upper()
                == "INSUFFICIENT_DATA"
                else "REJECT"
            )
            if context.actionability != expected_actionability:
                errors.append(
                    f"recommendation_actionability_mismatch: {ticker} context says "
                    f"{context.actionability}, expected {expected_actionability}."
                )
            decision_source = str(decision.get("decision_source") or "")
            if decision_source and context.decision_source != decision_source:
                errors.append(
                    f"recommendation_source_mismatch: {ticker} context source "
                    f"{context.decision_source} != {decision_source}."
                )

        top_state = entry.get("recommendation_state")
        if top_state is not None and top_state != context.recommendation_state:
            errors.append(
                f"recommendation_state_mismatch: {ticker} top-level state "
                f"{top_state} != {context.recommendation_state}."
            )


def _validate_basic_run_scope(
    batch_results: list[Any],
    markdown_text: str | None,
    latest_result: dict[str, Any] | None,
    errors: list[str],
) -> None:
    batch_entries = [item for item in batch_results if isinstance(item, dict)]
    timestamps = _collect_batch_timestamps(batch_entries)
    if len(timestamps) > 1:
        errors.append(
            "run_scope_batch_timestamp_mismatch: full_batch_results.json "
            f"contains multiple batch_timestamp values: {sorted(timestamps)}."
        )
    batch_timestamp = _extract_batch_timestamp(batch_entries)
    markdown_timestamp = _extract_markdown_field(markdown_text, "Batch Timestamp")
    latest_timestamp = _extract_batch_timestamp(
        [latest_result] if latest_result else []
    )

    _compare_optional_value(
        "batch_timestamp",
        batch_timestamp,
        markdown_timestamp,
        "TOP_3_SWING_TRADES.md",
        errors,
    )
    _compare_optional_value(
        "batch_timestamp",
        batch_timestamp,
        latest_timestamp,
        "latest_debate.json",
        errors,
    )

    debated_count = _extract_markdown_debated_count(markdown_text)
    if debated_count is not None and debated_count != len(batch_entries):
        errors.append(
            "run_scope_ticker_count_mismatch: TOP_3_SWING_TRADES.md "
            f"reports {debated_count} debated ticker(s), but full_batch_results.json "
            f"contains {len(batch_entries)}."
        )


def _reconcile_run_scope(
    *,
    batch_results: list[Any],
    markdown_text: str | None,
    latest_payload: dict[str, Any] | None,
    telemetry_records: list[dict[str, Any]],
    issues: list[ReconciliationIssue],
    errors: list[str],
    warnings: list[str],
) -> None:
    batch_entries = [item for item in batch_results if isinstance(item, dict)]
    timestamps = _collect_batch_timestamps(batch_entries)
    if len(timestamps) > 1:
        _append_error_issue(
            issues,
            errors,
            source="run_scope",
            code="batch_timestamp_mismatch",
            message=(
                "full_batch_results.json contains multiple batch_timestamp "
                f"values: {sorted(timestamps)}."
            ),
        )
    batch_tickers = {
        ticker
        for item in batch_entries
        if (ticker := _extract_ticker(item)) is not None
    }
    batch_timestamp = _extract_batch_timestamp(batch_entries)
    batch_run_id = _extract_shared_run_id(batch_entries)
    markdown_timestamp = _extract_markdown_field(markdown_text, "Batch Timestamp")
    markdown_run_id = _extract_markdown_field(markdown_text, "Run ID")
    latest_timestamp = _extract_batch_timestamp(
        [latest_payload] if latest_payload else []
    )
    latest_run_id = _extract_run_id(latest_payload) if latest_payload else None

    _append_run_scope_mismatch(
        issues,
        errors,
        field="batch_timestamp",
        expected=batch_timestamp,
        actual=markdown_timestamp,
        surface="TOP_3_SWING_TRADES.md",
    )
    _append_run_scope_mismatch(
        issues,
        errors,
        field="batch_timestamp",
        expected=batch_timestamp,
        actual=latest_timestamp,
        surface="latest_debate.json",
    )
    _append_run_scope_mismatch(
        issues,
        errors,
        field="run_id",
        expected=batch_run_id,
        actual=markdown_run_id,
        surface="TOP_3_SWING_TRADES.md",
    )
    _append_run_scope_mismatch(
        issues,
        errors,
        field="run_id",
        expected=batch_run_id,
        actual=latest_run_id,
        surface="latest_debate.json",
    )

    debated_count = _extract_markdown_debated_count(markdown_text)
    if debated_count is not None and debated_count != len(batch_entries):
        _append_error_issue(
            issues,
            errors,
            source="run_scope",
            code="ticker_count_mismatch",
            message=(
                "TOP_3_SWING_TRADES.md reports "
                f"{debated_count} debated ticker(s), but full_batch_results.json "
                f"contains {len(batch_entries)}."
            ),
        )

    if batch_run_id is None and batch_timestamp is None:
        return

    report = _find_matching_telemetry_report(
        telemetry_records,
        run_id=batch_run_id,
        batch_timestamp=batch_timestamp,
    )
    if report is None:
        if telemetry_records:
            _append_warning_issue(
                issues,
                warnings,
                source="run_scope",
                code="missing_matching_telemetry",
                message=(
                    "No telemetry report matches the latest batch "
                    f"run_id={batch_run_id or 'unknown'} "
                    f"batch_timestamp={batch_timestamp or 'unknown'}."
                ),
            )
        return

    telemetry_timestamp = str(report.get("batch_timestamp") or "").strip() or None
    telemetry_run_id = str(report.get("run_id") or "").strip() or None
    _append_run_scope_mismatch(
        issues,
        errors,
        field="batch_timestamp",
        expected=batch_timestamp,
        actual=telemetry_timestamp,
        surface="telemetry_log.jsonl",
    )
    _append_run_scope_mismatch(
        issues,
        errors,
        field="run_id",
        expected=batch_run_id,
        actual=telemetry_run_id,
        surface="telemetry_log.jsonl",
    )

    total_tickers = report.get("total_tickers")
    if isinstance(total_tickers, int) and total_tickers != len(batch_entries):
        _append_error_issue(
            issues,
            errors,
            source="run_scope",
            code="telemetry_ticker_count_mismatch",
            message=(
                f"telemetry_log.jsonl reports {total_tickers} ticker(s), "
                f"but full_batch_results.json contains {len(batch_entries)}."
            ),
        )

    telemetry_tickers = _extract_telemetry_tickers(report)
    if telemetry_tickers and telemetry_tickers != batch_tickers:
        _append_error_issue(
            issues,
            errors,
            source="run_scope",
            code="telemetry_ticker_set_mismatch",
            message=(
                "telemetry_log.jsonl ticker set does not match full_batch_results.json."
            ),
        )


def _has_position_sizing(entry: dict[str, Any]) -> bool:
    value = entry.get("position_sizing")
    return isinstance(value, dict) and bool(value)


def _extract_ticker(payload: dict[str, Any]) -> str | None:
    for candidate in (
        payload.get("ticker"),
        _nested_get(payload, "verdict", "ticker"),
        _nested_get(payload, "result", "ticker"),
    ):
        ticker = _clean_ticker(candidate)
        if ticker is not None:
            return ticker
    return None


def _nested_get(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _clean_ticker(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    return text.removesuffix(".JK")


def _extract_run_id(payload: dict[str, Any]) -> str | None:
    candidates = (
        payload.get("run_id"),
        _nested_get(payload, "metadata", "run_id"),
        _nested_get(payload, "metadata", "run_timestamp"),
        _nested_get(payload, "metadata", "batch_timestamp"),
        _nested_get(payload, "verdict", "run_id"),
    )
    for candidate in candidates:
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return None


def _is_explicit_zero(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == 0
    )


def _has_no_graph_activity(entry: dict[str, Any]) -> bool:
    metadata = entry.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return (
        entry.get("error") in (None, "")
        and _is_explicit_zero(entry.get("debate_rounds"))
        and not entry.get("agent_votes")
        and not entry.get("debate_history")
        and _is_explicit_zero(metadata.get("flash_calls"))
        and _is_explicit_zero(metadata.get("pro_calls"))
        and _is_explicit_zero(metadata.get("llm_calls"))
        and not any(key in metadata for key in _RAG_ACTIVITY_METADATA_KEYS)
    )


def _rag_not_applicable_record(
    entry: dict[str, Any],
    *,
    ticker: str,
    run_id: str | None,
) -> RagNotApplicableRecord | None:
    if not _has_no_graph_activity(entry):
        return None

    metadata = entry.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    verdict = entry.get("verdict")
    verdict = verdict if isinstance(verdict, dict) else {}

    snapshot = metadata.get("trade_setup_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    snapshot_status = str(snapshot.get("status") or "").upper()
    snapshot_reason = str(snapshot.get("reason_code") or "").strip()
    if (
        snapshot.get("debate_eligible") is False
        and str(metadata.get("decision_source") or "").lower() == "preflight"
        and str(verdict.get("decision_source") or "").lower() == "preflight"
        and snapshot_status in _TRADE_SETUP_RAG_NOT_APPLICABLE_STATUSES
        and bool(snapshot_reason)
    ):
        return RagNotApplicableRecord(
            ticker=ticker,
            run_id=run_id,
            terminal_kind="trade_setup",
            terminal_status=snapshot_status,
            reason_code=snapshot_reason,
        )

    pre_cio = metadata.get("pre_cio_rejection")
    pre_cio = pre_cio if isinstance(pre_cio, dict) else {}
    pre_cio_code = str(pre_cio.get("reason_code") or "").strip()
    if (
        pre_cio_code in _PRE_CIO_RAG_NOT_APPLICABLE_CODES
        and str(verdict.get("decision_source") or "").lower() == "risk_guard"
    ):
        return RagNotApplicableRecord(
            ticker=ticker,
            run_id=run_id,
            terminal_kind="pre_cio",
            terminal_status=str(entry.get("execution_status") or "NO_TRADE"),
            reason_code=pre_cio_code,
        )

    budget = metadata.get("budget_capacity_rejection")
    budget = budget if isinstance(budget, dict) else {}
    budget_code = str(budget.get("reason_code") or "").strip()
    if (
        budget_code == _BUDGET_RAG_NOT_APPLICABLE_CODE
        and entry.get("status") == "skipped"
    ):
        return RagNotApplicableRecord(
            ticker=ticker,
            run_id=run_id,
            terminal_kind="budget_capacity",
            terminal_status=str(
                entry.get("execution_status") or "INSUFFICIENT_DATA"
            ),
            reason_code=budget_code,
        )

    candidate_intake = metadata.get("candidate_intake_rejection")
    candidate_intake = candidate_intake if isinstance(candidate_intake, dict) else {}
    candidate_intake_code = str(
        candidate_intake.get("reason_code") or ""
    ).strip()
    if (
        candidate_intake_code == _CANDIDATE_INTAKE_RAG_NOT_APPLICABLE_CODE
        and metadata.get("artifact_scope") == "batch_only"
    ):
        return RagNotApplicableRecord(
            ticker=ticker,
            run_id=run_id,
            terminal_kind="candidate_intake",
            terminal_status=str(
                entry.get("execution_status") or "INSUFFICIENT_DATA"
            ),
            reason_code=candidate_intake_code,
        )

    return None


def _rag_validation_plan(
    batch_results: list[Any],
    *,
    latest_ticker: str | None,
    latest_run_id: str | None,
) -> tuple[list[tuple[str, str | None]], list[RagNotApplicableRecord]]:
    """Split batch targets into RAG-required and explicit terminal records."""
    targets: list[tuple[str, str | None]] = []
    not_applicable: list[RagNotApplicableRecord] = []
    grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for entry in batch_results:
        if not isinstance(entry, dict):
            continue
        ticker = _extract_ticker(entry)
        if ticker is None:
            continue
        run_id = _extract_run_id(entry)
        if run_id is None and ticker == latest_ticker:
            run_id = latest_run_id
        target = (ticker, run_id)
        grouped.setdefault(target, []).append(entry)

    for (ticker, run_id), entries in grouped.items():
        records = [
            _rag_not_applicable_record(
                entry,
                ticker=ticker,
                run_id=run_id,
            )
            for entry in entries
        ]
        if records and all(record is not None for record in records):
            not_applicable.extend(
                record for record in records if record is not None
            )
        else:
            targets.append((ticker, run_id))

    if not grouped and latest_ticker is not None:
        targets.append((latest_ticker, latest_run_id))
    return targets, not_applicable


def _collect_batch_timestamps(entries: list[dict[str, Any]]) -> set[str]:
    return {
        str(value).strip()
        for entry in entries
        if (
            value := (
                _nested_get(entry, "metadata", "batch_timestamp")
                or entry.get("batch_timestamp")
            )
        )
    }


def _extract_batch_timestamp(entries: list[dict[str, Any]]) -> str | None:
    timestamps = _collect_batch_timestamps(entries)
    if len(timestamps) == 1:
        return next(iter(timestamps))
    return None


def _extract_shared_run_id(entries: list[dict[str, Any]]) -> str | None:
    run_ids = {
        run_id for entry in entries if (run_id := _extract_run_id(entry)) is not None
    }
    if len(run_ids) == 1:
        return next(iter(run_ids))
    return None


def _extract_markdown_field(markdown_text: str | None, label: str) -> str | None:
    if not markdown_text:
        return None
    pattern = rf"^\>\s+\*\*{re.escape(label)}\*\*:\s*(.+?)\s*$"
    match = re.search(pattern, markdown_text, flags=re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    return value if value and value != "-" else None


def _extract_markdown_debated_count(markdown_text: str | None) -> int | None:
    if not markdown_text:
        return None
    match = re.search(r"\*\*Stocks Debated\*\*:\s*(\d+)", markdown_text)
    return int(match.group(1)) if match else None


def _compare_optional_value(
    field: str,
    expected: str | None,
    actual: str | None,
    surface: str,
    errors: list[str],
) -> None:
    if expected is None or actual is None or expected == actual:
        return
    errors.append(
        f"run_scope_{field}_mismatch: {surface} has {actual}, "
        f"but full_batch_results.json has {expected}."
    )


def _append_error_issue(
    issues: list[ReconciliationIssue],
    errors: list[str],
    *,
    source: str,
    code: str,
    message: str,
    ticker: str | None = None,
) -> None:
    errors.append(message)
    issues.append(
        ReconciliationIssue(
            source=source,
            code=code,
            severity="error",
            message=message,
            ticker=ticker,
        )
    )


def _append_run_scope_mismatch(
    issues: list[ReconciliationIssue],
    errors: list[str],
    *,
    field: str,
    expected: str | None,
    actual: str | None,
    surface: str,
) -> None:
    if expected is None or actual is None or expected == actual:
        return
    _append_error_issue(
        issues,
        errors,
        source="run_scope",
        code=f"{field}_mismatch",
        message=(
            f"{surface} has {field}={actual}, "
            f"but full_batch_results.json has {field}={expected}."
        ),
    )


def _find_matching_telemetry_report(
    records: list[dict[str, Any]],
    *,
    run_id: str | None,
    batch_timestamp: str | None,
) -> dict[str, Any] | None:
    for record in reversed(records):
        record_run_id = str(record.get("run_id") or "").strip() or None
        record_timestamp = str(record.get("batch_timestamp") or "").strip() or None
        if run_id is not None and record_run_id == run_id:
            return record
        if batch_timestamp is not None and record_timestamp == batch_timestamp:
            return record
    return None


def _extract_telemetry_tickers(record: dict[str, Any]) -> set[str]:
    metrics = record.get("ticker_metrics")
    if not isinstance(metrics, list):
        return set()
    return {
        ticker
        for metric in metrics
        if isinstance(metric, dict)
        if (ticker := _clean_ticker(metric.get("ticker"))) is not None
    }


def _record_matches(record: dict[str, Any], ticker: str, run_id: str | None) -> bool:
    record_ticker = _clean_ticker(record.get("ticker"))
    if record_ticker != ticker:
        return False
    if run_id is None:
        return True
    record_run_id = record.get("run_id") or _nested_get(record, "metadata", "run_id")
    return record_run_id is None or str(record_run_id) == run_id


def _append_warning_issue(
    issues: list[ReconciliationIssue],
    warnings: list[str],
    *,
    source: str,
    code: str,
    message: str,
    ticker: str | None = None,
) -> None:
    warnings.append(message)
    issues.append(
        ReconciliationIssue(
            source=source,
            code=code,
            severity="warning",
            message=message,
            ticker=ticker,
        )
    )


def _reconcile_audit_log(
    audit_path: Path | None,
    ticker: str,
    run_id: str | None,
    issues: list[ReconciliationIssue],
    warnings: list[str],
    corrupt_lines: list[dict[str, Any]] | None = None,
) -> None:
    records = _load_jsonl(audit_path, "audit_log.jsonl", warnings, corrupt_lines)
    if not records:
        _append_warning_issue(
            issues,
            warnings,
            source="audit_log",
            code="missing_audit_packet",
            message=f"No audit packet found for {ticker}.",
            ticker=ticker,
        )
        return
    if not any(_record_matches(record, ticker, run_id) for record in records):
        _append_warning_issue(
            issues,
            warnings,
            source="audit_log",
            code="missing_audit_packet",
            message=f"No audit packet matches {ticker} run_id={run_id or 'unknown'}.",
            ticker=ticker,
        )


def _reconcile_telemetry_log(
    telemetry_path: Path | None,
    ticker: str,
    run_id: str | None,
    issues: list[ReconciliationIssue],
    warnings: list[str],
) -> None:
    records = _load_jsonl(telemetry_path, "telemetry_log.jsonl", warnings)
    if not records:
        _append_warning_issue(
            issues,
            warnings,
            source="telemetry_log",
            code="missing_telemetry",
            message=f"No telemetry report found for {ticker}.",
            ticker=ticker,
        )
        return

    for record in records:
        if run_id is not None and str(record.get("run_id")) != run_id:
            continue
        metrics = record.get("ticker_metrics")
        if isinstance(metrics, list):
            if any(
                _clean_ticker(metric.get("ticker")) == ticker
                for metric in metrics
                if isinstance(metric, dict)
            ):
                return
    _append_warning_issue(
        issues,
        warnings,
        source="telemetry_log",
        code="missing_telemetry",
        message=f"No telemetry entry matches {ticker} run_id={run_id or 'unknown'}.",
        ticker=ticker,
    )


def _reconcile_rag_records(
    records: list[dict[str, Any]],
    ticker: str,
    run_id: str | None,
    issues: list[ReconciliationIssue],
    warnings: list[str],
) -> None:
    if not records:
        _append_warning_issue(
            issues,
            warnings,
            source="rag_evidence",
            code="missing_rag_evidence",
            message=f"No RAG evidence record found for {ticker}.",
            ticker=ticker,
        )
        return

    matches = [record for record in records if _record_matches(record, ticker, run_id)]
    if not matches:
        _append_warning_issue(
            issues,
            warnings,
            source="rag_evidence",
            code="missing_rag_evidence",
            message=f"No RAG evidence record matches {ticker} run_id={run_id or 'unknown'}.",
            ticker=ticker,
        )
        return
    if any(bool(record.get("has_stale_data")) for record in matches):
        _append_warning_issue(
            issues,
            warnings,
            source="rag_evidence",
            code="stale_evidence",
            message=f"RAG evidence for {ticker} includes stale data.",
            ticker=ticker,
        )


def _extract_markdown_tickers(markdown_text: str) -> list[str]:
    seen: set[str] = set()
    tickers: list[str] = []
    for match in _TOP_PICK_HEADING.finditer(markdown_text):
        ticker = match.group(1).upper()
        if ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate debate output artifacts.")
    parser.add_argument(
        "--batch", required=True, help="Path to full_batch_results.json"
    )
    parser.add_argument("--top3", required=True, help="Path to TOP_3_SWING_TRADES.md")
    parser.add_argument("--latest", required=True, help="Path to latest_debate.json")
    parser.add_argument(
        "--reconcile", action="store_true", help="Run full artifact reconciliation."
    )
    parser.add_argument("--audit-log", help="Path to audit_log.jsonl")
    parser.add_argument("--telemetry-log", help="Path to telemetry_log.jsonl")
    parser.add_argument("--rag-log", help="Path to evidence_log.jsonl")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.reconcile:
        report = reconcile_artifacts(
            args.batch,
            args.top3,
            args.latest,
            audit_log_path=args.audit_log,
            telemetry_log_path=args.telemetry_log,
            rag_evidence_log_path=args.rag_log,
        )
        print(report.model_dump_json(indent=2))
        return 0 if report.valid else 1

    report = validate_artifacts(args.batch, args.top3, args.latest)
    print(report.model_dump_json(indent=2))
    return 0 if report.valid else 1


if __name__ == "__main__":
    sys.exit(main())
