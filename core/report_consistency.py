"""Check consistency between batch JSON and human-readable top-pick reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class InconsistencyType(str, Enum):
    """Known consistency failures between markdown and batch JSON."""

    RATING_MISMATCH = "rating_mismatch"
    TICKER_NOT_IN_BATCH = "ticker_not_in_batch"
    FAILED_TICKER_PROMOTED = "failed_ticker_promoted"
    PRICE_MISMATCH = "price_mismatch"
    MISSING_VERDICT = "missing_verdict"
    MISSING_RISK_GOVERNOR = "missing_risk_governor"
    SIZED_NON_DEPLOYABLE = "sized_non_deployable"
    NON_DEPLOYABLE_PROMOTED = "non_deployable_promoted"
    UPSIDE_EXHAUSTED = "upside_exhausted"


class Inconsistency(BaseModel):
    """Single inconsistency found during report checks."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    type: InconsistencyType
    markdown_value: str | None
    json_value: str | None
    severity: Literal["error", "warning"]


class ConsistencyReport(BaseModel):
    """Aggregate consistency result."""

    model_config = ConfigDict(extra="forbid")

    consistent: bool
    inconsistencies: list[Inconsistency]
    checked_tickers: list[str]
    timestamp: str


_HEADING_TICKER_RE = re.compile(
    r"^#{2,3}\s+(?:\d+[.)]\s*)?(?:#\d+\s*[-\u2013\u2014]\s*)?"
    r"([A-Z]{4}\d*)(?:\.JK)?\b",
    re.MULTILINE,
)
_BOLD_TICKER_RE = re.compile(r"\*\*([A-Z]{4}\d*)(?:\.JK)?\*\*")
_PRICE_RE = re.compile(r"(?:Rp\.?\s*)?(\d[\d.,]{2,})")
EXCLUDED_WORDS = {
    "AVOID",
    "BUY",
    "CIO",
    "HOLD",
    "SELL",
    "IDR",
    "ROE",
    "EPS",
    "P/E",
    "RSI",
    "ATR",
    "MA50",
    "MA200",
    "IHSG",
    "ETF",
    "IPO",
}


def check_consistency(
    batch_json_path: str | Path,
    top3_md_path: str | Path,
) -> ConsistencyReport:
    """Compare promoted markdown tickers against authoritative batch JSON."""
    batch_path = Path(batch_json_path)
    top3_path = Path(top3_md_path)
    batch_entries = _load_batch_entries(batch_path)
    markdown_text = top3_path.read_text(encoding="utf-8")

    batch_by_ticker = {
        ticker: entry
        for entry in batch_entries
        if (ticker := _extract_ticker(entry)) is not None
    }
    markdown_tickers = _extract_markdown_tickers(markdown_text)
    inconsistencies: list[Inconsistency] = []

    for ticker in markdown_tickers:
        markdown_context = _ticker_context(markdown_text, ticker)
        batch_entry = batch_by_ticker.get(ticker)
        if batch_entry is None:
            inconsistencies.append(
                Inconsistency(
                    ticker=ticker,
                    type=InconsistencyType.TICKER_NOT_IN_BATCH,
                    markdown_value="present",
                    json_value=None,
                    severity="error",
                )
            )
            continue

        status = _extract_status(batch_entry)
        if status == "failed":
            inconsistencies.append(
                Inconsistency(
                    ticker=ticker,
                    type=InconsistencyType.FAILED_TICKER_PROMOTED,
                    markdown_value="present",
                    json_value=status,
                    severity="error",
                )
            )

        rating = _extract_rating(batch_entry)
        if rating in {"AVOID", "SELL"} and _markdown_presents_positive(
            markdown_context
        ):
            inconsistencies.append(
                Inconsistency(
                    ticker=ticker,
                    type=InconsistencyType.RATING_MISMATCH,
                    markdown_value="positive",
                    json_value=rating,
                    severity="error",
                )
            )

        markdown_price = _extract_markdown_price(markdown_context)
        json_price = _extract_json_price(batch_entry)
        if (
            markdown_price is not None
            and json_price is not None
            and _relative_gap(markdown_price, json_price) > 0.05
        ):
            inconsistencies.append(
                Inconsistency(
                    ticker=ticker,
                    type=InconsistencyType.PRICE_MISMATCH,
                    markdown_value=str(markdown_price),
                    json_value=str(json_price),
                    severity="warning",
                )
            )

        _append_actionability_inconsistencies(
            inconsistencies,
            ticker=ticker,
            batch_entry=batch_entry,
            markdown_context=markdown_context,
        )

    return ConsistencyReport(
        consistent=not inconsistencies,
        inconsistencies=inconsistencies,
        checked_tickers=markdown_tickers,
        timestamp=datetime.now(UTC).isoformat(),
    )


def _load_batch_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("results", "batch_results", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_markdown_tickers(markdown_text: str) -> list[str]:
    seen: set[str] = set()
    tickers: list[str] = []
    for regex in (_HEADING_TICKER_RE, _BOLD_TICKER_RE):
        for match in regex.finditer(markdown_text):
            ticker = _clean_ticker(match.group(1))
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
    return tickers


def _clean_ticker(value: Any) -> str | None:
    if value is None:
        return None
    ticker = str(value).strip().upper().removesuffix(".JK")
    if not ticker or ticker in EXCLUDED_WORDS:
        return None
    if not re.fullmatch(r"[A-Z]{4}\d*", ticker):
        return None
    return ticker


def _extract_ticker(entry: dict[str, Any]) -> str | None:
    candidates = (
        entry.get("ticker"),
        _nested_get(entry, "verdict", "ticker"),
        _nested_get(entry, "result", "ticker"),
    )
    for candidate in candidates:
        ticker = _clean_ticker(candidate)
        if ticker:
            return ticker
    return None


def _extract_status(entry: dict[str, Any]) -> str | None:
    value = entry.get("status") or _nested_get(entry, "result", "status")
    if value is None:
        return None
    return str(value).strip().lower()


def _extract_rating(entry: dict[str, Any]) -> str | None:
    verdict = entry.get("verdict")
    if isinstance(verdict, str):
        try:
            verdict = json.loads(verdict)
        except json.JSONDecodeError:
            verdict = None
    candidates = (
        entry.get("rating"),
        verdict.get("rating") if isinstance(verdict, dict) else None,
        _nested_get(entry, "result", "rating"),
        _nested_get(entry, "result", "verdict", "rating"),
    )
    for candidate in candidates:
        if candidate is not None:
            return str(candidate).strip().upper().replace(" ", "_")
    return None


def _extract_json_price(entry: dict[str, Any]) -> float | None:
    verdict = entry.get("verdict")
    if isinstance(verdict, str):
        try:
            verdict = json.loads(verdict)
        except json.JSONDecodeError:
            verdict = None
    candidates = (
        entry.get("current_price"),
        entry.get("price"),
        verdict.get("current_price") if isinstance(verdict, dict) else None,
        verdict.get("entry_price") if isinstance(verdict, dict) else None,
        _nested_get(entry, "position_sizing", "entry_price"),
        _nested_get(entry, "position_sizing", "current_price"),
    )
    for candidate in candidates:
        price = _parse_price(candidate)
        if price is not None:
            return price
    return None


def _append_actionability_inconsistencies(
    inconsistencies: list[Inconsistency],
    *,
    ticker: str,
    batch_entry: dict[str, Any],
    markdown_context: str,
) -> None:
    risk = batch_entry.get("risk_governor")
    if not isinstance(risk, dict):
        inconsistencies.append(
            Inconsistency(
                ticker=ticker,
                type=InconsistencyType.MISSING_RISK_GOVERNOR,
                markdown_value="present",
                json_value=None,
                severity="warning",
            )
        )
        return

    sizing_allowed = risk.get("sizing_allowed")
    reason_codes = {
        str(code) for code in risk.get("reason_codes", []) if code is not None
    }
    status = str(risk.get("status") or "")

    if sizing_allowed is False and _has_position_sizing(batch_entry):
        inconsistencies.append(
            Inconsistency(
                ticker=ticker,
                type=InconsistencyType.SIZED_NON_DEPLOYABLE,
                markdown_value="present",
                json_value=status or "sizing_allowed=False",
                severity="error",
            )
        )

    if "upside_exhausted" in reason_codes:
        inconsistencies.append(
            Inconsistency(
                ticker=ticker,
                type=InconsistencyType.UPSIDE_EXHAUSTED,
                markdown_value="promoted",
                json_value="target_price <= current_price",
                severity="error",
            )
        )

    if sizing_allowed is False and _markdown_presents_executable_buy(markdown_context):
        inconsistencies.append(
            Inconsistency(
                ticker=ticker,
                type=InconsistencyType.NON_DEPLOYABLE_PROMOTED,
                markdown_value="executable",
                json_value=status or "sizing_allowed=False",
                severity="warning",
            )
        )


def _has_position_sizing(entry: dict[str, Any]) -> bool:
    value = entry.get("position_sizing")
    return isinstance(value, dict) and bool(value)


def _ticker_context(markdown_text: str, ticker: str) -> str:
    lines = markdown_text.splitlines()
    for index, line in enumerate(lines):
        if re.search(rf"\b{re.escape(ticker)}(?:\.JK)?\b", line):
            end = min(index + 8, len(lines))
            for next_index in range(index + 1, len(lines)):
                if next_index > index + 1 and lines[next_index].startswith("#"):
                    end = next_index
                    break
            return "\n".join(lines[index:end])
    return ""


def _markdown_presents_positive(markdown_context: str) -> bool:
    lowered = markdown_context.lower()
    negative_terms = ("avoid", "failed", "reject", "not recommended")
    positive_terms = (
        "accumulate",
        "buy",
        "bullish",
        "candidate",
        "recommended",
        "top",
    )
    if any(term in lowered for term in positive_terms):
        return True
    return not any(term in lowered for term in negative_terms)


def _markdown_presents_executable_buy(markdown_context: str) -> bool:
    lowered = markdown_context.lower()
    executable_markers = (
        "sizing allowed** | yes",
        "sizing allowed | yes",
        "market buy",
        "buy now",
        "beli sekarang",
        "deploy 60% sekarang",
        "masuk sizing",
    )
    return any(marker in lowered for marker in executable_markers)


def _extract_markdown_price(markdown_context: str) -> float | None:
    if not markdown_context:
        return None
    price_lines = [
        line
        for line in markdown_context.splitlines()
        if any(label in line.lower() for label in ("price", "harga", "current"))
    ]
    search_text = "\n".join(price_lines) or markdown_context
    match = _PRICE_RE.search(search_text)
    if not match:
        return None
    return _parse_price(match.group(1))


def _parse_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if not match:
        return None
    token = match.group(1)
    if "," in token and "." in token:
        token = token.replace(".", "").replace(",", ".")
    else:
        token = token.replace(",", "").replace(".", "")
    try:
        return float(token)
    except ValueError:
        return None


def _relative_gap(markdown_price: float, json_price: float) -> float:
    denominator = max(abs(json_price), 1.0)
    return abs(markdown_price - json_price) / denominator


def _nested_get(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check TOP_3 markdown against full batch JSON."
    )
    parser.add_argument(
        "--batch", required=True, help="Path to full_batch_results.json"
    )
    parser.add_argument("--top3", required=True, help="Path to TOP_3_SWING_TRADES.md")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = check_consistency(args.batch, args.top3)
    print(report.model_dump_json(indent=2))
    return 0 if report.consistent else 1


if __name__ == "__main__":
    sys.exit(main())
