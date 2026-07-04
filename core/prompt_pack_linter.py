"""Read-only linter for debate prompt packs."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

# JSON-only and archived contracts: they do not carry the agent footer markers
# used by scout/debate prompts.
EXEMPT_PROMPTS = {
    "CONSENSUS_PROMPT",
    "STATE_CLEANER_PROMPT",
    "SENTIMENT_PROMPT",
    "CIO_SYSTEM_PROMPT",
}

# Bull/Bear debate rounds keep a static "Debate Confidence:" footer by design and
# get "Agent Confidence:" appended at runtime via AGENT_SIGNAL_PROMPT — so the
# static file legitimately lacks the literal marker. Position: is still required.
EXEMPT_AGENT_CONFIDENCE_MARKER = {
    "BULL_SYSTEM_PROMPT_R1",
    "BULL_SYSTEM_PROMPT_R2",
    "BEAR_SYSTEM_PROMPT_R1",
    "BEAR_SYSTEM_PROMPT_R2",
}


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
    runtime_prompts, archived_prompts = _extract_prompt_sections(
        manifest_data,
        errors,
    )
    total_prompts = len(runtime_prompts) + len(archived_prompts)

    for name in duplicate_names:
        errors.append(f"Duplicate prompt name in manifest: {name}")

    duplicate_sections = sorted(set(runtime_prompts) & set(archived_prompts))
    for prompt_name in duplicate_sections:
        errors.append(f"Prompt cannot be both runtime and archived: {prompt_name}")

    for prompt_name, filename in runtime_prompts.items():
        _lint_prompt_file(
            prompt_name=prompt_name,
            filename=filename,
            prompt_dir=manifest.parent,
            errors=errors,
            warnings=warnings,
            blocking=True,
            enforce_markers=True,
        )

    for prompt_name, filename in archived_prompts.items():
        _lint_prompt_file(
            prompt_name=prompt_name,
            filename=filename,
            prompt_dir=manifest.parent,
            errors=errors,
            warnings=warnings,
            blocking=False,
            enforce_markers=False,
        )

    return LintReport(
        valid=not errors,
        total_prompts=total_prompts,
        errors=errors,
        warnings=warnings,
    )


def guard_prompt_pack(
    manifest_path: str,
    *,
    required_prompts: dict[str, str] | None = None,
    expected_prompt_version: str | None = None,
) -> LintReport:
    """Run release-grade prompt checks while preserving the lint report shape."""
    lint_report = lint_prompt_pack(manifest_path)
    errors = list(lint_report.errors)
    warnings = list(lint_report.warnings)

    manifest = Path(manifest_path)
    manifest_data, _duplicate_names = _load_manifest(manifest, [])
    runtime_prompts, _archived_prompts = _extract_prompt_sections(manifest_data, [])

    prompt_version = ""
    if isinstance(manifest_data, dict):
        prompt_version = str(manifest_data.get("prompt_version") or "").strip()
    if not prompt_version:
        errors.append("Manifest requires a non-empty prompt_version.")

    expected_version = expected_prompt_version
    if expected_version is None:
        expected_version = _current_registry_version(manifest)
    if expected_version and prompt_version and prompt_version != expected_version:
        errors.append(
            "Manifest prompt_version mismatch: "
            f"expected {expected_version}, got {prompt_version}."
        )

    required = required_prompts if required_prompts is not None else _required_prompts()
    missing_required = sorted(set(required) - set(runtime_prompts))
    for prompt_name in missing_required:
        errors.append(f"Manifest missing required runtime prompt: {prompt_name}")

    return LintReport(
        valid=not errors,
        total_prompts=lint_report.total_prompts,
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


def _extract_prompt_sections(
    manifest_data: dict[str, Any] | None,
    errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if manifest_data is None:
        return {}, {}

    runtime = manifest_data.get("runtime_required_prompts")
    archived = manifest_data.get("archived_prompts")
    if runtime is not None or archived is not None:
        runtime_prompts: dict[str, Any] = {}
        archived_prompts: dict[str, Any] = {}
        if not isinstance(runtime, dict):
            errors.append("Manifest requires a 'runtime_required_prompts' object.")
        else:
            runtime_prompts = runtime
        if archived is not None:
            if not isinstance(archived, dict):
                errors.append("Manifest 'archived_prompts' must be an object.")
            else:
                archived_prompts = archived
        return runtime_prompts, archived_prompts

    prompts = manifest_data.get("prompts")
    if not isinstance(prompts, dict):
        errors.append(
            "Manifest requires a 'runtime_required_prompts' object "
            "or legacy 'prompts' object."
        )
        return {}, {}
    return prompts, {}


def _lint_prompt_file(
    *,
    prompt_name: str,
    filename: Any,
    prompt_dir: Path,
    errors: list[str],
    warnings: list[str],
    blocking: bool,
    enforce_markers: bool,
) -> None:
    problems = errors if blocking else warnings
    prompt_kind = "Prompt" if blocking else "Archived prompt"
    if not isinstance(filename, str) or not filename.strip():
        problems.append(f"{prompt_kind} {prompt_name} must map to a non-empty filename.")
        return

    path = prompt_dir / filename
    if not path.exists():
        problems.append(f"{prompt_kind} file missing for {prompt_name}: {path}")
        return
    if not path.is_file():
        problems.append(f"{prompt_kind} path is not a file for {prompt_name}: {path}")
        return

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        problems.append(
            f"{prompt_kind} file is not valid UTF-8 for {prompt_name}: {path}: {exc}"
        )
        return

    if not content.strip():
        problems.append(f"{prompt_kind} file is empty for {prompt_name}: {path}")
        return
    if enforce_markers and prompt_name not in EXEMPT_PROMPTS:
        if "Position:" not in content:
            errors.append(
                f"Prompt file for {prompt_name} is missing required marker: Position:"
            )
        if (
            prompt_name not in EXEMPT_AGENT_CONFIDENCE_MARKER
            and "Agent Confidence:" not in content
        ):
            errors.append(
                f"Prompt file for {prompt_name} is missing required marker: Agent Confidence:"
            )


def _required_prompts() -> dict[str, str]:
    from services.debate_prompt_registry import REQUIRED_PROMPTS

    return REQUIRED_PROMPTS


def _current_registry_version(manifest_path: Path) -> str | None:
    from services.debate_prompt_registry import MANIFEST_PATH, PROMPT_VERSION

    try:
        if manifest_path.resolve() == MANIFEST_PATH.resolve():
            return PROMPT_VERSION
    except OSError:
        return None
    return None


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
