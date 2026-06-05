"""Academic comparison reporter for single-agent vs multi-agent outputs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from services.single_agent_analyzer import SingleAgentResult


class ComparisonRow(BaseModel):
    """One ticker row in the single-vs-multi comparison table."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    single_rating: str | None
    single_confidence: float | None
    single_rr_ratio: float | None
    multi_rating: str | None
    multi_confidence: float | None
    multi_rr_ratio: float | None
    multi_debate_rounds: int | None
    multi_dissent_count: int | None
    ratings_agree: bool
    confidence_delta: float | None
    notes: str


class ComparisonReport(BaseModel):
    """Aggregate comparison report for thesis Chapter 4 tables."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    generated_at: str
    tickers: list[str]
    rows: list[ComparisonRow]
    total_tickers: int
    agreement_rate: float
    avg_confidence_delta: float
    multi_higher_confidence_count: int
    single_higher_confidence_count: int
    summary: str


class ComparisonReporter:
    """Build and format single-agent vs multi-agent comparison reports."""

    def build_comparison(
        self,
        single_results: list[SingleAgentResult],
        multi_results_path: str | Path,
    ) -> ComparisonReport:
        multi_results = self._load_multi_results(Path(multi_results_path))
        rows: list[ComparisonRow] = []

        for single in single_results:
            ticker = single.ticker.strip().upper()
            multi = multi_results.get(ticker, {})
            single_verdict = single.verdict

            single_rating = single_verdict.rating if single_verdict else None
            single_confidence = single_verdict.confidence if single_verdict else None
            single_rr_ratio = (
                single_verdict.risk_reward_ratio if single_verdict else None
            )
            multi_rating = self._string_or_none(multi.get("rating"))
            multi_confidence = self._to_float(multi.get("confidence"))
            multi_rr_ratio = self._to_float(multi.get("risk_reward_ratio"))
            debate_rounds = self._to_int(multi.get("debate_rounds"))
            dissent_count = self._to_int(multi.get("dissent_count"))

            ratings_agree = (
                single_rating is not None
                and multi_rating is not None
                and single_rating == multi_rating
            )
            confidence_delta = None
            if single_confidence is not None and multi_confidence is not None:
                confidence_delta = round(multi_confidence - single_confidence, 4)

            rows.append(
                ComparisonRow(
                    ticker=ticker,
                    single_rating=single_rating,
                    single_confidence=single_confidence,
                    single_rr_ratio=single_rr_ratio,
                    multi_rating=multi_rating,
                    multi_confidence=multi_confidence,
                    multi_rr_ratio=multi_rr_ratio,
                    multi_debate_rounds=debate_rounds,
                    multi_dissent_count=dissent_count,
                    ratings_agree=ratings_agree,
                    confidence_delta=confidence_delta,
                    notes=self._build_notes(single, multi, ratings_agree),
                )
            )

        total = len(rows)
        agreement_count = sum(1 for row in rows if row.ratings_agree)
        deltas = [
            row.confidence_delta for row in rows if row.confidence_delta is not None
        ]
        avg_delta = round(sum(deltas) / len(deltas), 4) if deltas else 0.0
        multi_higher = sum(1 for delta in deltas if delta > 0)
        single_higher = sum(1 for delta in deltas if delta < 0)
        agreement_rate = agreement_count / total if total else 0.0
        run_id = single_results[0].run_id if single_results else "comparison"

        return ComparisonReport(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            tickers=[row.ticker for row in rows],
            rows=rows,
            total_tickers=total,
            agreement_rate=agreement_rate,
            avg_confidence_delta=avg_delta,
            multi_higher_confidence_count=multi_higher,
            single_higher_confidence_count=single_higher,
            summary=(
                f"{agreement_count}/{total} ticker memiliki rating yang sama; "
                f"delta confidence rata-rata multi vs single {avg_delta:+.2f}."
            ),
        )

    def format_markdown_table(self, report: ComparisonReport) -> str:
        lines = [
            "# Perbandingan Single-Agent vs Multi-Agent",
            "",
            "## Ringkasan",
            f"- Total ticker dianalisis: {report.total_tickers}",
            f"- Tingkat kesepakatan rating: {report.agreement_rate:.0%}",
            (
                "- Rata-rata delta confidence: "
                f"{report.avg_confidence_delta:+.2f} (multi vs single)"
            ),
            "",
            "## Tabel Perbandingan",
            "",
            (
                "| Ticker | Single Rating | Single Conf | Multi Rating | "
                "Multi Conf | Agree? | Notes |"
            ),
            "|--------|---------------|-------------|--------------|------------|--------|-------|",
        ]

        for row in report.rows:
            lines.append(
                "| "
                f"{row.ticker} | "
                f"{row.single_rating or '-'} | "
                f"{self._format_pct(row.single_confidence)} | "
                f"{row.multi_rating or '-'} | "
                f"{self._format_pct(row.multi_confidence)} | "
                f"{'Ya' if row.ratings_agree else 'Tidak'} | "
                f"{row.notes} |"
            )

        lines.extend(
            [
                "",
                "## Analisis",
                "### Kasus Perbedaan Signifikan",
            ]
        )
        disagreements = [row for row in report.rows if not row.ratings_agree]
        if disagreements:
            for row in disagreements:
                lines.append(
                    f"- {row.ticker}: single={row.single_rating or '-'}, "
                    f"multi={row.multi_rating or '-'}; {row.notes}"
                )
        else:
            lines.append("- Tidak ada perbedaan rating.")

        lines.extend(
            [
                "",
                "### Confidence Distribution",
                f"Multi lebih confident: {report.multi_higher_confidence_count} ticker",
                f"Single lebih confident: {report.single_higher_confidence_count} ticker",
            ]
        )
        return "\n".join(lines)

    def save_report(
        self,
        report: ComparisonReport,
        output_path: str | Path,
    ) -> None:
        path = Path(output_path)
        if path.suffix.lower() == ".json":
            json_path = path
            markdown_path = path.with_suffix(".md")
        elif path.suffix.lower() == ".md":
            markdown_path = path
            json_path = path.with_suffix(".json")
        else:
            path.mkdir(parents=True, exist_ok=True)
            json_path = path / "comparison_report.json"
            markdown_path = path / "comparison_report.md"

        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        markdown_path.write_text(self.format_markdown_table(report), encoding="utf-8")

    def _load_multi_results(self, path: Path) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
        for file_path in files:
            if not file_path.exists():
                continue
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for item in self._iter_result_items(payload):
                record = self._extract_multi_record(item)
                ticker = self._string_or_none(record.get("ticker"))
                if ticker:
                    records[ticker.upper()] = record
        return records

    def _iter_result_items(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("results", "tickers", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if "ticker" in payload or "verdict" in payload or "final_verdict" in payload:
            return [payload]
        return [item for item in payload.values() if isinstance(item, dict)]

    def _extract_multi_record(self, item: dict[str, Any]) -> dict[str, Any]:
        verdict = self._coerce_dict(item.get("verdict"))
        if not verdict:
            verdict = self._coerce_dict(item.get("final_verdict"))
        source = verdict or item
        dissenting_agents = item.get("dissenting_agents")
        if dissenting_agents is None:
            dissenting_agents = source.get("dissenting_agents")
        if not isinstance(dissenting_agents, list):
            dissenting_agents = []

        return {
            "ticker": item.get("ticker") or source.get("ticker"),
            "rating": source.get("rating") or item.get("rating"),
            "confidence": self._first_present(
                source.get("confidence"),
                item.get("confidence"),
            ),
            "risk_reward_ratio": (
                source.get("risk_reward_ratio")
                if source.get("risk_reward_ratio") is not None
                else item.get("risk_reward_ratio")
            ),
            "debate_rounds": self._first_present(
                item.get("debate_rounds"),
                item.get("round_count"),
            ),
            "dissent_count": len(dissenting_agents),
        }

    @staticmethod
    def _coerce_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _build_notes(
        single: SingleAgentResult,
        multi: dict[str, Any],
        ratings_agree: bool,
    ) -> str:
        if single.status != "success":
            return f"Single-agent {single.status}: {single.error or 'unknown error'}"
        if not multi:
            return "Multi-agent result missing"
        single_rating = single.verdict.rating if single.verdict else None
        multi_rating = ComparisonReporter._string_or_none(multi.get("rating"))
        if not ratings_agree:
            return f"Rating berbeda: single={single_rating}, multi={multi_rating}"
        return "Ratings agree"

    @staticmethod
    def _format_pct(value: float | None) -> str:
        return "-" if value is None else f"{value:.0%}"

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value in (None, ""):
            return None
        return str(value).strip().upper()

    @staticmethod
    def _first_present(*values: Any) -> Any:
        for value in values:
            if value not in (None, ""):
                return value
        return None


DEFAULT_REPORTER = ComparisonReporter()


def _load_single_results(path: str | Path) -> list[SingleAgentResult]:
    base = Path(path)
    files = sorted(base.glob("*.json")) if base.is_dir() else [base]
    results: list[SingleAgentResult] = []
    for file_path in files:
        try:
            results.append(
                SingleAgentResult.model_validate_json(
                    file_path.read_text(encoding="utf-8")
                )
            )
        except (OSError, ValueError):
            continue
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare single-agent and multi-agent stock decisions."
    )
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--single-results", required=True)
    parser.add_argument("--multi-results", required=True)
    args = parser.parse_args()

    single_results = _load_single_results(args.single_results)
    if args.tickers:
        selected = {ticker.upper() for ticker in args.tickers}
        single_results = [
            result for result in single_results if result.ticker.upper() in selected
        ]
    report = DEFAULT_REPORTER.build_comparison(
        single_results=single_results,
        multi_results_path=args.multi_results,
    )
    print(DEFAULT_REPORTER.format_markdown_table(report))


if __name__ == "__main__":
    main()
