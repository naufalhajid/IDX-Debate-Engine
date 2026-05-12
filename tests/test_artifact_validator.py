import json
from pathlib import Path

from core.artifact_validator import validate_artifacts


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
            {"ticker": "BBCA", "status": "ok"},
            {"ticker": "BBRI", "status": "ok"},
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
        batch=[{"ticker": "BBCA", "status": "ok"}],
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
            {"ticker": "BBCA", "status": "ok"},
            {"ticker": "TLKM", "status": "failed"},
        ],
        markdown="# TOP 3\n\n## #1 - BBCA\n\n## #2 - TLKM\n",
        latest={"ticker": "BBCA"},
    )

    report = validate_artifacts(batch_path, top3_path, latest_path)

    assert report.valid is False
    assert any("Ticker TLKM is listed" in error and "status=failed" in error for error in report.errors)
