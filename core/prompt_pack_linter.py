"""Read-only linter for debate prompt packs."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

EXEMPT_PROMPTS = {"CONSENSUS_PROMPT", "STATE_CLEANER_PROMPT"}


class LintReport(BaseModel):
    """Prompt pack lint result."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    total_prompts: int
    errors: list[str]
    warnings: list[str]


def lint_prompt_pack(manifest_path: str) -> LintReport:
    """Validate manifest and prompt files without modifying prompt content."""
    errors: list[str] = []
    warnings: list[str] = []
    manifest = Path(manifest_path)

    manifest_data, duplicate_names = _load_manifest(manifest, errors)
    prompts = _extract_prompts(manifest_data, errors)
    total_prompts = len(prompts)

    for name in duplicate_names:
        errors.append(f"Duplicate prompt name in manifest: {name}")

    for prompt_name, filename in prompts.items():
        _lint_prompt_file(
            prompt_name=prompt_name,
            filename=filename,
            prompt_dir=manifest.parent,
            errors=errors,
        )

    return LintReport(
        valid=not errors,
        total_prompts=total_prompts,
        errors=errors,
        warnings=warnings,
    )


def _load_manifest(
    manifest_path: Path,
    errors: list[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    duplicate_keys: list[str] = []

    def object_pairs_hook(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicate_keys.append(key)
            result[key] = value
        return result

    if not manifest_path.exists():
        errors.append(f"Manifest file is missing: {manifest_path}")
        return None, duplicate_keys
    if not manifest_path.is_file():
        errors.append(f"Manifest path is not a file: {manifest_path}")
        return None, duplicate_keys

    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"Manifest is not valid UTF-8 text: {manifest_path}: {exc}")
        return None, duplicate_keys

    try:
        data = json.loads(raw, object_pairs_hook=object_pairs_hook)
    except json.JSONDecodeError as exc:
        errors.append(f"Manifest is not valid JSON: {manifest_path}: {exc}")
        return None, duplicate_keys

    if not isinstance(data, dict):
        errors.append(f"Manifest root must be a JSON object: {manifest_path}")
        return None, duplicate_keys
    return data, duplicate_keys


def _extract_prompts(
    manifest_data: dict[str, Any] | None,
    errors: list[str],
) -> dict[str, Any]:
    if manifest_data is None:
        return {}
    prompts = manifest_data.get("prompts")
    if not isinstance(prompts, dict):
        errors.append("Manifest requires a 'prompts' object.")
        return {}
    return prompts


def _lint_prompt_file(
    *,
    prompt_name: str,
    filename: Any,
    prompt_dir: Path,
    errors: list[str],
) -> None:
    if not isinstance(filename, str) or not filename.strip():
        errors.append(f"Prompt {prompt_name} must map to a non-empty filename.")
        return

    path = prompt_dir / filename
    if not path.exists():
        errors.append(f"Prompt file missing for {prompt_name}: {path}")
        return
    if not path.is_file():
        errors.append(f"Prompt path is not a file for {prompt_name}: {path}")
        return

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"Prompt file is not valid UTF-8 for {prompt_name}: {path}: {exc}")
        return

    if not content.strip():
        errors.append(f"Prompt file is empty for {prompt_name}: {path}")
        return
    if prompt_name not in EXEMPT_PROMPTS:
        if "Position:" not in content:
            errors.append(f"Prompt file for {prompt_name} is missing required marker: Position:")
        if "Agent Confidence:" not in content:
            errors.append(
                f"Prompt file for {prompt_name} is missing required marker: Agent Confidence:"
            )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lint debate prompt pack files.")
    parser.add_argument("--manifest", required=True, help="Path to manifest.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = lint_prompt_pack(args.manifest)
    print(report.model_dump_json(indent=2))
    return 0 if report.valid else 1


if __name__ == "__main__":
    sys.exit(main())
