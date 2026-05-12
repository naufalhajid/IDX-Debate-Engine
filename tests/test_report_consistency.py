import json
from pathlib import Path

from core.report_consistency import InconsistencyType, check_consistency


def _write_reports(
    tmp_path: Path,
    *,
    batch: list[dict],
    markdown: str,
) -> tuple[Path, Path]:
    batch_path = tmp_path / "full_batch_results.json"
    top3_path = tmp_path / "TOP_3_SWING_TRADES.md"
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    top3_path.write_text(markdown, encoding="utf-8")
    return batch_path, top3_path


def test_check_consistency_fully_consistent_report(tmp_path: Path) -> None:
    batch_path, top3_path = _write_reports(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "verdict": {"rating": "BUY", "current_price": 1000},
            }
        ],
        markdown="# TOP 3\n\n## BBCA\nCurrent Price: Rp 1,020\nSignal: BUY\n",
    )

    report = check_consistency(batch_path, top3_path)

    assert report.consistent is True
    assert report.inconsistencies == []
    assert report.checked_tickers == ["BBCA"]


def test_check_consistency_flags_ticker_missing_from_json(
    tmp_path: Path,
) -> None:
    batch_path, top3_path = _write_reports(
        tmp_path,
        batch=[{"ticker": "BBCA", "status": "ok", "verdict": {"rating": "BUY"}}],
        markdown="# TOP 3\n\n## TLKM\nSignal: BUY\n",
    )

    report = check_consistency(batch_path, top3_path)

    assert report.consistent is False
    assert report.inconsistencies[0].ticker == "TLKM"
    assert report.inconsistencies[0].type == InconsistencyType.TICKER_NOT_IN_BATCH
    assert report.inconsistencies[0].severity == "error"


def test_check_consistency_flags_failed_ticker_promoted(
    tmp_path: Path,
) -> None:
    batch_path, top3_path = _write_reports(
        tmp_path,
        batch=[
            {
                "ticker": "WIIM",
                "status": "failed",
                "verdict": {"rating": "BUY"},
            }
        ],
        markdown="# TOP 3\n\n### 1. WIIM\nSignal: BUY\n",
    )

    report = check_consistency(batch_path, top3_path)

    assert report.consistent is False
    assert any(
        item.type == InconsistencyType.FAILED_TICKER_PROMOTED
        and item.ticker == "WIIM"
        for item in report.inconsistencies
    )


def test_check_consistency_flags_avoid_rating_presented_positively(
    tmp_path: Path,
) -> None:
    batch_path, top3_path = _write_reports(
        tmp_path,
        batch=[
            {
                "ticker": "BBRI",
                "status": "ok",
                "verdict": {"rating": "AVOID"},
            }
        ],
        markdown="# TOP 3\n\nRecommended pick: **BBRI**\nSignal: BUY\n",
    )

    report = check_consistency(batch_path, top3_path)

    assert report.consistent is False
    assert any(
        item.type == InconsistencyType.RATING_MISMATCH
        and item.ticker == "BBRI"
        for item in report.inconsistencies
    )


def test_check_consistency_ignores_false_positive_section_headers(
    tmp_path: Path,
) -> None:
    batch_path, top3_path = _write_reports(
        tmp_path,
        batch=[],
        markdown=(
            "# TOP 3\n\n"
            "## CIO Review\n"
            "The CIO summary mentions BBCA in prose only.\n\n"
            "## BUY\n"
            "This is a signal label, not a ticker heading.\n\n"
            "### HOLD\n"
            "Another signal label.\n"
        ),
    )

    report = check_consistency(batch_path, top3_path)

    assert report.consistent is True
    assert report.checked_tickers == []
    assert report.inconsistencies == []
