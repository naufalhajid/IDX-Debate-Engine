from __future__ import annotations

from pathlib import Path

import pytest

import run_quant_filter


def test_build_config_custom_output_keeps_default_workbook_lookup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        run_quant_filter,
        "_find_latest_xlsx",
        lambda output_dir: f"{output_dir}/IDX Fundamental Analysis.xlsx",
    )

    cfg = run_quant_filter.build_config(output_dir=tmp_path / "dry_run")

    assert cfg["output_dir"] == str(tmp_path / "dry_run")
    assert cfg["input_file"] == "output/IDX Fundamental Analysis.xlsx"


def test_build_config_uses_canonical_regime_field_names() -> None:
    cfg = run_quant_filter.build_config(
        execution_regime="defensive",
        execution_regime_reason="rule_based_defensive",
        trend_regime="sideways",
        volatility_regime="high",
    )

    assert cfg["execution_regime"] == "DEFENSIVE"
    assert cfg["execution_regime_reason"] == "rule_based_defensive"
    assert cfg["trend_regime"] == "SIDEWAYS"
    assert cfg["volatility_regime"] == "HIGH"
    assert "execution_volatility_regime" not in cfg


def test_build_config_rejects_noncanonical_execution_regime() -> None:
    with pytest.raises(ValueError, match="execution_regime must be one of"):
        run_quant_filter.build_config(execution_regime="bear_stress")
