"""Read-only validation for batch output artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


_TOP_PICK_HEADING = re.compile(
    r"^##\s+.*?#\d+\s*[-\u2013\u2014]\s*([A-Z][A-Z0-9]{1,5})\b",
    re.MULTILINE,
)
DEFAULT_AUDIT_LOG_PATH = Path("output/audit/audit_log.jsonl")
DEFAULT_TELEMETRY_LOG_PATH = Path("output/telemetry/telemetry_log.jsonl")
DEFAULT_RAG_EVIDENCE_LOG_PATH = Path("output/rag_evidence/evidence_log.jsonl")


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
    latest_ticker = _extract_ticker(latest_payload) if isinstance(latest_payload, dict) else None
    latest_run_id = _extract_run_id(latest_payload) if isinstance(latest_payload, dict) else None

    surfaces = {
        "batch": batch_path.exists(),
        "top3": top3_path.exists(),
        "latest": latest_path.exists(),
        "audit": bool(audit_path and audit_path.exists()),
        "telemetry": bool(telemetry_path and telemetry_path.exists()),
        "rag_evidence": bool(rag_path and rag_path.exists()),
    }

    if latest_ticker is not None:
        _reconcile_audit_log(audit_path, latest_ticker, latest_run_id, issues, warnings)
        _reconcile_telemetry_log(
            telemetry_path,
            latest_ticker,
            latest_run_id,
            issues,
            warnings,
        )
        _reconcile_rag_log(rag_path, latest_ticker, latest_run_id, issues, warnings)
    else:
        message = "latest_debate.json has no ticker; optional log surfaces cannot be linked."
        warnings.append(message)
        issues.append(
            ReconciliationIssue(
                source="latest_debate",
                code="missing_latest_ticker",
                severity="warning",
                message=message,
            )
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


def _load_jsonl(path: Path | None, label: str, warnings: list[str]) -> list[dict[str, Any]]:
    if path is None:
        return []
    if not path.exists():
        warnings.append(f"Missing optional artifact surface: {label} at {path}")
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.append(f"{label} line {line_number} is not valid JSON: {exc}")
            continue
        if isinstance(payload, dict):
            records.append(payload)
        else:
            warnings.append(f"{label} line {line_number} is not a JSON object.")
    return records


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
            str(code)
            for code in risk.get("reason_codes", [])
            if code is not None
        }
        if "upside_exhausted" in reason_codes:
            warnings.append(
                f"upside_exhausted: {ticker} target_price is not above current_price."
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
) -> None:
    records = _load_jsonl(audit_path, "audit_log.jsonl", warnings)
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
            if any(_clean_ticker(metric.get("ticker")) == ticker for metric in metrics if isinstance(metric, dict)):
                return
    _append_warning_issue(
        issues,
        warnings,
        source="telemetry_log",
        code="missing_telemetry",
        message=f"No telemetry entry matches {ticker} run_id={run_id or 'unknown'}.",
        ticker=ticker,
    )


def _reconcile_rag_log(
    rag_path: Path | None,
    ticker: str,
    run_id: str | None,
    issues: list[ReconciliationIssue],
    warnings: list[str],
) -> None:
    records = _load_jsonl(rag_path, "evidence_log.jsonl", warnings)
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
    parser.add_argument("--batch", required=True, help="Path to full_batch_results.json")
    parser.add_argument("--top3", required=True, help="Path to TOP_3_SWING_TRADES.md")
    parser.add_argument("--latest", required=True, help="Path to latest_debate.json")
    parser.add_argument("--reconcile", action="store_true", help="Run full artifact reconciliation.")
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
