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
        "lint_prompt_pack",
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
