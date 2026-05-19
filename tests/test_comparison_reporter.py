from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.comparison_reporter import ComparisonReporter
from services.single_agent_analyzer import SingleAgentResult, SingleAgentVerdict


def _single_result(
    ticker: str,
    rating: str,
    confidence: float = 0.70,
    rr_ratio: float = 2.0,
) -> SingleAgentResult:
    generated_at = datetime.now(timezone.utc).isoformat()
    return SingleAgentResult(
        ticker=ticker,
        run_id="run-1",
        verdict=SingleAgentVerdict(
            ticker=ticker,
            rating=rating,
            confidence=confidence,
            fair_value=10000.0,
            current_price=9000.0,
            entry_price_range="8600 - 8800",
            target_price=9500.0,
            stop_loss=8300.0,
            risk_reward_ratio=rr_ratio,
            reasoning="Single baseline reasoning.",
            key_risks=["Risk"],
            key_catalysts=["Catalyst"],
            timeframe="1-3 Months",
            mode="single_agent",
            model_used="gemini-2.5-flash",
            generated_at=generated_at,
            run_id="run-1",
            data_sources=["stockbit", "yfinance"],
        ),
        status="success",
        error=None,
        duration_seconds=0.1,
        context_tokens=100,
        generated_at=generated_at,
    )


def _write_multi_results(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "full_batch_results.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _multi_row(
    ticker: str,
    rating: str,
    confidence: float = 0.80,
    rr_ratio: float = 2.5,
) -> dict:
    return {
        "ticker": ticker,
        "verdict": {
            "ticker": ticker,
            "rating": rating,
            "confidence": confidence,
            "risk_reward_ratio": rr_ratio,
            "dissenting_agents": ["bear"],
        },
        "debate_rounds": 2,
    }


def test_build_comparison_agreement_rate_is_one_when_all_ratings_match(
    tmp_path: Path,
) -> None:
    reporter = ComparisonReporter()
    single = [_single_result("BBCA", "BUY"), _single_result("ADRO", "HOLD")]
    multi_path = _write_multi_results(
        tmp_path,
        [_multi_row("BBCA", "BUY"), _multi_row("ADRO", "HOLD")],
    )

    report = reporter.build_comparison(single, multi_path)

    assert report.agreement_rate == 1.0


def test_build_comparison_agreement_rate_is_zero_when_no_ratings_match(
    tmp_path: Path,
) -> None:
    reporter = ComparisonReporter()
    single = [_single_result("BBCA", "BUY"), _single_result("ADRO", "AVOID")]
    multi_path = _write_multi_results(
        tmp_path,
        [_multi_row("BBCA", "AVOID"), _multi_row("ADRO", "HOLD")],
    )

    report = reporter.build_comparison(single, multi_path)

    assert report.agreement_rate == 0.0


def test_build_comparison_marks_disagreement_for_buy_vs_avoid(tmp_path: Path) -> None:
    reporter = ComparisonReporter()
    single = [_single_result("BBCA", "BUY")]
    multi_path = _write_multi_results(tmp_path, [_multi_row("BBCA", "AVOID")])

    report = reporter.build_comparison(single, multi_path)

    assert report.rows[0].ratings_agree is False


def test_confidence_delta_is_multi_minus_single(tmp_path: Path) -> None:
    reporter = ComparisonReporter()
    single = [_single_result("BBCA", "BUY", confidence=0.60)]
    multi_path = _write_multi_results(
        tmp_path,
        [_multi_row("BBCA", "BUY", confidence=0.85)],
    )

    report = reporter.build_comparison(single, multi_path)

    assert report.rows[0].confidence_delta == 0.25


def test_format_markdown_table_contains_title(tmp_path: Path) -> None:
    reporter = ComparisonReporter()
    single = [_single_result("BBCA", "BUY")]
    multi_path = _write_multi_results(tmp_path, [_multi_row("BBCA", "BUY")])
    report = reporter.build_comparison(single, multi_path)

    markdown = reporter.format_markdown_table(report)

    assert "Perbandingan Single-Agent vs Multi-Agent" in markdown


def test_format_markdown_table_shows_disagreement_cases(tmp_path: Path) -> None:
    reporter = ComparisonReporter()
    single = [_single_result("BBCA", "BUY")]
    multi_path = _write_multi_results(tmp_path, [_multi_row("BBCA", "AVOID")])
    report = reporter.build_comparison(single, multi_path)

    markdown = reporter.format_markdown_table(report)

    assert "single=BUY, multi=AVOID" in markdown


def test_comparison_row_notes_populated_when_ratings_disagree(tmp_path: Path) -> None:
    reporter = ComparisonReporter()
    single = [_single_result("BBCA", "BUY")]
    multi_path = _write_multi_results(tmp_path, [_multi_row("BBCA", "AVOID")])

    report = reporter.build_comparison(single, multi_path)

    assert report.rows[0].notes
    assert "Rating berbeda" in report.rows[0].notes
