"""Domain-scoped verification runner for focused development checks."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.artifact_validator import reconcile_artifacts
from core.prompt_pack_linter import guard_prompt_pack


class DomainScope(str, Enum):
    """Supported verification domains."""

    PROVIDERS = "providers"
    DEBATE = "debate"
    ORCHESTRATOR = "orchestrator"
    ARTIFACTS = "artifacts"
    PROMPTS = "prompts"
    ALL = "all"


class VerificationResult(BaseModel):
    """Result returned by a domain-scoped verification run."""

    model_config = ConfigDict(extra="forbid")

    domain: DomainScope
    tests_passed: bool
    tests_failed: bool
    artifact_valid: bool | None = None
    lint_valid: bool | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROMPT_MANIFEST_PATH = _REPO_ROOT / "services" / "debate_prompts" / "manifest.json"

_TEST_TARGETS: dict[DomainScope, list[str]] = {
    DomainScope.PROVIDERS: [
        "tests/test_failure_taxonomy.py",
        "tests/test_provider_health.py",
    ],
    DomainScope.DEBATE: [
        "tests/test_debate_run_guard.py",
        "tests/test_context_pack_builder.py",
    ],
    DomainScope.ORCHESTRATOR: [
        "tests/test_candidate_intake.py",
        "tests/test_artifact_validator.py",
    ],
    DomainScope.PROMPTS: ["tests/test_prompt_pack_linter.py"],
    DomainScope.ARTIFACTS: ["tests/test_artifact_validator.py"],
    DomainScope.ALL: ["tests/"],
}


def _coerce_scope(scope: DomainScope | str) -> DomainScope:
    if isinstance(scope, DomainScope):
        return scope
    if isinstance(scope, str):
        normalized = scope.strip().lower()
        try:
            return DomainScope(normalized)
        except ValueError:
            try:
                return DomainScope[scope.strip().upper()]
            except KeyError as exc:
                raise ValueError(f"Unknown verification scope: {scope}") from exc
    raise ValueError(f"Unknown verification scope: {scope!r}")


async def _run_pytest(targets: list[str]) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "pytest", *targets, "--tb=short", "-q"]
    return await asyncio.to_thread(
        subprocess.run,
        command,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _artifact_path(artifact_paths: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = artifact_paths.get(key)
        if value:
            return value
    accepted = ", ".join(keys)
    raise ValueError(f"Missing artifact path; expected one of: {accepted}")


def _format_pytest_error(result: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if not output.strip():
        return f"pytest failed with exit code {result.returncode}"
    return output.strip()


async def run_verification(
    scope: DomainScope | str,
    artifact_paths: dict | None = None,
) -> VerificationResult:
    """Run the focused pytest/lint/artifact checks for ``scope``."""
    started = time.perf_counter()
    domain = _coerce_scope(scope)
    errors: list[str] = []
    warnings: list[str] = []
    artifact_valid: bool | None = None
    lint_valid: bool | None = None

    pytest_result = await _run_pytest(_TEST_TARGETS[domain])
    tests_passed = pytest_result.returncode == 0
    tests_failed = not tests_passed
    if tests_failed:
        errors.append(_format_pytest_error(pytest_result))

    if domain in {DomainScope.ARTIFACTS, DomainScope.ALL} and artifact_paths:
        try:
            artifact_report = reconcile_artifacts(
                _artifact_path(artifact_paths, "batch", "batch_json_path"),
                _artifact_path(artifact_paths, "top3", "top3_md_path"),
                _artifact_path(artifact_paths, "latest", "latest_json_path"),
                audit_log_path=artifact_paths.get("audit")
                or artifact_paths.get("audit_log_path"),
                telemetry_log_path=artifact_paths.get("telemetry")
                or artifact_paths.get("telemetry_log_path"),
                rag_evidence_log_path=artifact_paths.get("rag")
                or artifact_paths.get("rag_log_path")
                or artifact_paths.get("rag_evidence_log_path"),
            )
            artifact_valid = artifact_report.valid
            errors.extend(artifact_report.errors)
            warnings.extend(artifact_report.warnings)
        except Exception as exc:
            artifact_valid = False
            errors.append(f"artifact validation failed: {exc}")

    if domain in {DomainScope.PROMPTS, DomainScope.ALL}:
        try:
            lint_report = guard_prompt_pack(str(_PROMPT_MANIFEST_PATH))
            lint_valid = lint_report.valid
            errors.extend(lint_report.errors)
            warnings.extend(lint_report.warnings)
        except Exception as exc:
            lint_valid = False
            errors.append(f"prompt lint failed: {exc}")

    return VerificationResult(
        domain=domain,
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        artifact_valid=artifact_valid,
        lint_valid=lint_valid,
        errors=errors,
        warnings=warnings,
        duration_seconds=time.perf_counter() - started,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run focused verification checks.")
    parser.add_argument(
        "--scope",
        required=True,
        choices=[scope.value for scope in DomainScope],
        help="Verification domain to run.",
    )
    parser.add_argument("--batch", help="Path to full_batch_results.json")
    parser.add_argument("--top3", help="Path to TOP_3_SWING_TRADES.md")
    parser.add_argument("--latest", help="Path to latest_debate.json")
    parser.add_argument("--audit", help="Path to audit_log.jsonl")
    parser.add_argument("--telemetry", help="Path to telemetry_log.jsonl")
    parser.add_argument("--rag", help="Path to evidence_log.jsonl")
    return parser.parse_args(argv)


def _artifact_paths_from_args(args: argparse.Namespace) -> dict[str, str] | None:
    paths = {
        "batch": args.batch,
        "top3": args.top3,
        "latest": args.latest,
        "audit": args.audit,
        "telemetry": args.telemetry,
        "rag": args.rag,
    }
    return {key: value for key, value in paths.items() if value} or None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = asyncio.run(
        run_verification(
            DomainScope(args.scope),
            artifact_paths=_artifact_paths_from_args(args),
        )
    )
    print(result.model_dump_json(indent=2))
    failed = (
        result.tests_failed
        or result.artifact_valid is False
        or result.lint_valid is False
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
