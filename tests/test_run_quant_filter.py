from __future__ import annotations

from pathlib import Path

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
