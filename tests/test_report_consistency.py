import json
from pathlib import Path

from core.report_consistency import InconsistencyType, check_consistency


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
                "risk_governor": _risk_governor(),
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
                "risk_governor": {**_risk_governor(), "ticker": "BBRI"},
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


def test_check_consistency_warns_for_missing_risk_governor(
    tmp_path: Path,
) -> None:
    batch_path, top3_path = _write_reports(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "verdict": {"rating": "BUY", "current_price": 1000},
            }
        ],
        markdown="# TOP 3\n\n## BBCA\nSignal: BUY\n",
    )

    report = check_consistency(batch_path, top3_path)

    assert report.consistent is False
    assert any(
        item.type == InconsistencyType.MISSING_RISK_GOVERNOR
        and item.severity == "warning"
        for item in report.inconsistencies
    )


def test_check_consistency_flags_non_deployable_that_looks_executable(
    tmp_path: Path,
) -> None:
    batch_path, top3_path = _write_reports(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "verdict": {"rating": "BUY", "current_price": 1100},
                "risk_governor": _risk_governor(
                    status="wait_for_pullback",
                    sizing_allowed=False,
                    reason_codes=["price_above_entry_range"],
                ),
            }
        ],
        markdown=(
            "# TOP 3\n\n"
            "## BBCA\n"
            "| **Actionability** | Wait For Pullback |\n"
            "| **Sizing Allowed** | Yes |\n"
        ),
    )

    report = check_consistency(batch_path, top3_path)

    assert report.consistent is False
    assert any(
        item.type == InconsistencyType.NON_DEPLOYABLE_PROMOTED
        for item in report.inconsistencies
    )


def test_check_consistency_flags_sized_non_deployable(
    tmp_path: Path,
) -> None:
    batch_path, top3_path = _write_reports(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "verdict": {"rating": "BUY", "current_price": 1100},
                "risk_governor": _risk_governor(
                    status="wait_for_pullback",
                    sizing_allowed=False,
                    reason_codes=["price_above_entry_range"],
                ),
                "position_sizing": {"lot": 1, "entry_price": 1100},
            }
        ],
        markdown="# TOP 3\n\n## BBCA\n| **Sizing Allowed** | No |\n",
    )

    report = check_consistency(batch_path, top3_path)

    assert report.consistent is False
    assert any(
        item.type == InconsistencyType.SIZED_NON_DEPLOYABLE
        and item.severity == "error"
        for item in report.inconsistencies
    )


def test_check_consistency_flags_upside_exhausted_promoted(
    tmp_path: Path,
) -> None:
    batch_path, top3_path = _write_reports(
        tmp_path,
        batch=[
            {
                "ticker": "BBCA",
                "status": "ok",
                "verdict": {"rating": "BUY", "current_price": 1100},
                "risk_governor": _risk_governor(
                    status="reject",
                    sizing_allowed=False,
                    reason_codes=["upside_exhausted"],
                ),
            }
        ],
        markdown="# TOP 3\n\n## BBCA\n| **Sizing Allowed** | No |\n",
    )

    report = check_consistency(batch_path, top3_path)

    assert report.consistent is False
    assert any(
        item.type == InconsistencyType.UPSIDE_EXHAUSTED
        and item.severity == "error"
        for item in report.inconsistencies
    )
