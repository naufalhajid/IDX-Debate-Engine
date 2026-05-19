from types import SimpleNamespace

import pytest

from core import verification_runner as vr


@pytest.mark.asyncio
async def test_all_scope_returns_result_with_duration(monkeypatch) -> None:
    async def fake_run_pytest(targets: list[str]) -> SimpleNamespace:
        assert targets == ["tests/"]
        return SimpleNamespace(returncode=0, stdout="92 passed", stderr="")

    monkeypatch.setattr(vr, "_run_pytest", fake_run_pytest)
    monkeypatch.setattr(
        vr,
        "guard_prompt_pack",
        lambda _manifest: SimpleNamespace(valid=True, errors=[], warnings=[]),
    )

    result = await vr.run_verification(vr.DomainScope.ALL)

    assert isinstance(result, vr.VerificationResult)
    assert result.domain == vr.DomainScope.ALL
    assert result.tests_passed is True
    assert result.tests_failed is False
    assert result.lint_valid is True
    assert result.artifact_valid is None
    assert result.duration_seconds > 0


@pytest.mark.asyncio
async def test_debate_scope_only_runs_debate_related_tests(monkeypatch) -> None:
    captured_targets: list[str] = []

    async def fake_run_pytest(targets: list[str]) -> SimpleNamespace:
        captured_targets.extend(targets)
        return SimpleNamespace(returncode=0, stdout="passed", stderr="")

    monkeypatch.setattr(vr, "_run_pytest", fake_run_pytest)

    result = await vr.run_verification(vr.DomainScope.DEBATE)

    assert captured_targets == [
        "tests/test_debate_run_guard.py",
        "tests/test_context_pack_builder.py",
    ]
    assert result.tests_passed is True
    assert result.lint_valid is None
    assert result.artifact_valid is None


@pytest.mark.asyncio
async def test_invalid_scope_raises_value_error(monkeypatch) -> None:
    async def fail_if_called(_targets: list[str]) -> SimpleNamespace:
        raise AssertionError("pytest should not run for an invalid scope")

    monkeypatch.setattr(vr, "_run_pytest", fail_if_called)

    with pytest.raises(ValueError):
        await vr.run_verification("not-a-scope")


@pytest.mark.asyncio
async def test_artifact_scope_uses_reconciler_when_paths_provided(monkeypatch) -> None:
    async def fake_run_pytest(targets: list[str]) -> SimpleNamespace:
        assert targets == ["tests/test_artifact_validator.py"]
        return SimpleNamespace(returncode=0, stdout="passed", stderr="")

    captured_paths: dict[str, object] = {}

    def fake_reconcile(*args, **kwargs) -> SimpleNamespace:
        captured_paths["args"] = args
        captured_paths["kwargs"] = kwargs
        return SimpleNamespace(valid=True, errors=[], warnings=["optional warning"])

    monkeypatch.setattr(vr, "_run_pytest", fake_run_pytest)
    monkeypatch.setattr(vr, "reconcile_artifacts", fake_reconcile)

    result = await vr.run_verification(
        vr.DomainScope.ARTIFACTS,
        artifact_paths={
            "batch": "batch.json",
            "top3": "top3.md",
            "latest": "latest.json",
            "audit": "audit.jsonl",
            "telemetry": "telemetry.jsonl",
            "rag": "rag.jsonl",
        },
    )

    assert result.artifact_valid is True
    assert result.warnings == ["optional warning"]
    assert captured_paths["args"] == ("batch.json", "top3.md", "latest.json")
    assert captured_paths["kwargs"] == {
        "audit_log_path": "audit.jsonl",
        "telemetry_log_path": "telemetry.jsonl",
        "rag_evidence_log_path": "rag.jsonl",
    }
