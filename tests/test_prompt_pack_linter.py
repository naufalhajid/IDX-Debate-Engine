import json
from pathlib import Path

from core.prompt_pack_linter import lint_prompt_pack


VALID_PROMPT = "You are an agent.\n\nPosition: BUY\nAgent Confidence: 0.75\n"


def _write_manifest(prompt_dir: Path, prompts: dict[str, str]) -> Path:
    manifest_path = prompt_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"prompt_version": "test-v1", "prompts": prompts}),
        encoding="utf-8",
    )
    return manifest_path


def test_lint_prompt_pack_all_prompts_valid(tmp_path: Path) -> None:
    (tmp_path / "bull.txt").write_text(VALID_PROMPT, encoding="utf-8")
    (tmp_path / "bear.txt").write_text(VALID_PROMPT, encoding="utf-8")
    manifest_path = _write_manifest(
        tmp_path,
        {
            "BULL_PROMPT": "bull.txt",
            "BEAR_PROMPT": "bear.txt",
        },
    )

    report = lint_prompt_pack(str(manifest_path))

    assert report.valid is True
    assert report.total_prompts == 2
    assert report.errors == []
    assert report.warnings == []


def test_lint_prompt_pack_missing_file_error(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, {"BULL_PROMPT": "missing.txt"})

    report = lint_prompt_pack(str(manifest_path))

    assert report.valid is False
    assert report.total_prompts == 1
    assert any("Prompt file missing for BULL_PROMPT" in error for error in report.errors)


def test_lint_prompt_pack_missing_position_marker_error(tmp_path: Path) -> None:
    (tmp_path / "bull.txt").write_text(
        "You are an agent.\n\nAgent Confidence: 0.75\n",
        encoding="utf-8",
    )
    manifest_path = _write_manifest(tmp_path, {"BULL_PROMPT": "bull.txt"})

    report = lint_prompt_pack(str(manifest_path))

    assert report.valid is False
    assert any("missing required marker: Position:" in error for error in report.errors)


def test_lint_prompt_pack_duplicate_prompt_name_error(tmp_path: Path) -> None:
    (tmp_path / "bull.txt").write_text(VALID_PROMPT, encoding="utf-8")
    (tmp_path / "bear.txt").write_text(VALID_PROMPT, encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        """
        {
          "prompt_version": "test-v1",
          "prompts": {
            "BULL_PROMPT": "bull.txt",
            "BULL_PROMPT": "bear.txt"
          }
        }
        """,
        encoding="utf-8",
    )

    report = lint_prompt_pack(str(manifest_path))

    assert report.valid is False
    assert any("Duplicate prompt name in manifest: BULL_PROMPT" in error for error in report.errors)
