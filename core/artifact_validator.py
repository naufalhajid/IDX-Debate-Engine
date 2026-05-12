"""Read-only validation for batch output artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class ValidationReport(BaseModel):
    """Validation result for generated debate artifacts."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str]
    warnings: list[str]


_TOP_PICK_HEADING = re.compile(
    r"^##\s+.*?#\d+\s*[-\u2013\u2014]\s*([A-Z][A-Z0-9]{1,5})\b",
    re.MULTILINE,
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

    return ValidationReport(valid=not errors, errors=errors, warnings=warnings)


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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = validate_artifacts(args.batch, args.top3, args.latest)
    print(report.model_dump_json(indent=2))
    return 0 if report.valid else 1


if __name__ == "__main__":
    sys.exit(main())
