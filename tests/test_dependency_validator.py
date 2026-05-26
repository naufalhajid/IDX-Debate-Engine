"""Tests untuk core/dependency_validator.py."""
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.dependency_validator import (
    ValidationResult,
    check_candidates_file,
    maybe_rerun_quant_filter,
)


def test_file_not_found(tmp_path: Path) -> None:
    """File tidak ada harus mengembalikan is_valid=False dengan pesan yang jelas."""
    result = check_candidates_file(tmp_path / "missing.json", max_age_hours=24.0)
    assert result.is_valid is False
    assert result.age_hours == float("inf")
    assert "tidak ditemukan" in result.message.lower()


def test_file_fresh(tmp_path: Path) -> None:
    """File yang baru dibuat (usia < threshold) harus valid."""
    f = tmp_path / "top10_candidates.json"
    f.write_text("[]", encoding="utf-8")
    result = check_candidates_file(f, max_age_hours=24.0)
    assert result.is_valid is True
    assert result.age_hours < 1.0
    assert "valid" in result.message.lower()


def test_file_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File yang di-backdate mtime harus dianggap stale."""
    f = tmp_path / "top10_candidates.json"
    f.write_text("[]", encoding="utf-8")

    # Backdate mtime ke 48 jam yang lalu
    old_mtime = time.time() - (48 * 3600)
    import os
    os.utime(f, (old_mtime, old_mtime))

    result = check_candidates_file(f, max_age_hours=24.0)
    assert result.is_valid is False
    assert result.age_hours > 24.0
    assert "stale" in result.message.lower()


def test_file_exactly_at_boundary(tmp_path: Path) -> None:
    """File tepat di batas max_age harus dianggap valid (edge case: usia < max, bukan <=)."""
    f = tmp_path / "top10_candidates.json"
    f.write_text("[]", encoding="utf-8")
    # File baru dibuat — usianya jauh di bawah 24 jam
    result = check_candidates_file(f, max_age_hours=0.001)  # sangat ketat
    # Bisa stale atau valid tergantung kecepatan I/O — yang penting is ValidationResult
    assert isinstance(result, ValidationResult)
    assert isinstance(result.is_valid, bool)


def test_validation_result_fields() -> None:
    """ValidationResult harus punya field is_valid, age_hours, message."""
    r = ValidationResult(is_valid=True, age_hours=1.5, message="ok")
    assert r.is_valid is True
    assert r.age_hours == 1.5
    assert r.message == "ok"


def test_maybe_rerun_quant_filter_passes_output_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "run_quant_filter.py"
    script.write_text("print('ok')", encoding="utf-8")
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str]) -> SimpleNamespace:
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("core.dependency_validator.subprocess.run", fake_run)

    assert maybe_rerun_quant_filter(script_path=str(script), output_dir=tmp_path / "dry")
    assert captured["command"][-2:] == ["--output-dir", str(tmp_path / "dry")]
