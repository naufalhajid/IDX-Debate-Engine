import json
from pathlib import Path

import pytest

from core.prompt_pack_linter import guard_prompt_pack, lint_prompt_pack
from services import debate_prompt_registry
from services.debate_prompt_registry import (
    ARCHIVED_PROMPTS,
    MANIFEST_PATH,
    RUNTIME_REQUIRED_PROMPTS,
)


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
    assert any(
        "Prompt file missing for BULL_PROMPT" in error for error in report.errors
    )


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
    assert any(
        "Duplicate prompt name in manifest: BULL_PROMPT" in error
        for error in report.errors
    )


def _write_required_prompt_pack(
    prompt_dir: Path,
    *,
    missing_prompt: str | None = None,
    prompt_version: str | None = "test-v1",
    write_archived_files: bool = True,
) -> Path:
    runtime_prompts: dict[str, str] = {}
    for prompt_name, filename in RUNTIME_REQUIRED_PROMPTS.items():
        if prompt_name == missing_prompt:
            continue
        runtime_prompts[prompt_name] = filename
        (prompt_dir / filename).write_text(VALID_PROMPT, encoding="utf-8")

    archived_prompts = dict(ARCHIVED_PROMPTS)
    if write_archived_files:
        for filename in archived_prompts.values():
            (prompt_dir / filename).write_text("Archived prompt body\n", encoding="utf-8")

    manifest_payload: dict[str, object] = {
        "runtime_required_prompts": runtime_prompts,
        "archived_prompts": archived_prompts,
    }
    if prompt_version is not None:
        manifest_payload["prompt_version"] = prompt_version
    manifest_path = prompt_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    return manifest_path


def test_guard_prompt_pack_current_manifest_valid() -> None:
    report = guard_prompt_pack(str(MANIFEST_PATH))

    assert report.valid is True
    assert report.errors == []


def test_guard_prompt_pack_missing_required_prompt_error(tmp_path: Path) -> None:
    manifest_path = _write_required_prompt_pack(
        tmp_path,
        missing_prompt="BULL_SYSTEM_PROMPT_R1",
    )

    report = guard_prompt_pack(
        str(manifest_path),
        expected_prompt_version="test-v1",
    )

    assert report.valid is False
    assert any(
        "Manifest missing required runtime prompt: BULL_SYSTEM_PROMPT_R1" in error
        for error in report.errors
    )


def test_guard_prompt_pack_missing_archived_prompt_is_warning_not_error(
    tmp_path: Path,
) -> None:
    manifest_path = _write_required_prompt_pack(
        tmp_path,
        write_archived_files=False,
    )

    report = guard_prompt_pack(
        str(manifest_path),
        expected_prompt_version="test-v1",
    )

    assert report.valid is True
    assert report.errors == []
    assert any(
        "Archived prompt file missing for CONSENSUS_PROMPT" in warning
        for warning in report.warnings
    )


def test_load_prompt_registry_does_not_require_archived_prompts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest_path = _write_required_prompt_pack(
        tmp_path,
        write_archived_files=False,
    )
    monkeypatch.setattr(debate_prompt_registry, "PROMPT_DIR", tmp_path)
    monkeypatch.setattr(debate_prompt_registry, "MANIFEST_PATH", manifest_path)

    registry = debate_prompt_registry.load_prompt_registry()

    assert "BULL_SYSTEM_PROMPT_R1" in registry.prompts
    assert "CONSENSUS_PROMPT" not in registry.prompts
    assert "STATE_CLEANER_PROMPT" not in registry.prompts
    assert registry.archived_prompts == {}


def test_load_prompt_registry_rejects_missing_runtime_manifest_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest_path = _write_required_prompt_pack(
        tmp_path,
        missing_prompt="BULL_SYSTEM_PROMPT_R1",
    )
    monkeypatch.setattr(debate_prompt_registry, "PROMPT_DIR", tmp_path)
    monkeypatch.setattr(debate_prompt_registry, "MANIFEST_PATH", manifest_path)

    with pytest.raises(ValueError, match="BULL_SYSTEM_PROMPT_R1"):
        debate_prompt_registry.load_prompt_registry()


def test_guard_prompt_pack_missing_prompt_version_error(tmp_path: Path) -> None:
    manifest_path = _write_required_prompt_pack(tmp_path, prompt_version=None)

    report = guard_prompt_pack(str(manifest_path))

    assert report.valid is False
    assert any("non-empty prompt_version" in error for error in report.errors)


def test_guard_prompt_pack_version_mismatch_error(tmp_path: Path) -> None:
    manifest_path = _write_required_prompt_pack(tmp_path, prompt_version="test-v1")

    report = guard_prompt_pack(
        str(manifest_path),
        expected_prompt_version="expected-v2",
    )

    assert report.valid is False
    assert any("prompt_version mismatch" in error for error in report.errors)
