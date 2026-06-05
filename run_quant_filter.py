"""Compatibility CLI wrapper for the IHSG quantitative filter."""

from __future__ import annotations

import argparse
from pathlib import Path

from core.quant_filter.config import CONFIG, _find_latest_xlsx
from core.quant_filter.pipeline import run_pipeline

__all__ = ["CONFIG", "build_config", "main", "run_pipeline"]


def build_config(
    *,
    output_dir: str | Path | None = None,
    input_file: str | Path | None = None,
    scratch_dir: str | Path | None = None,
    mode: str | None = None,
) -> dict:
    """Build quant-filter config while keeping legacy defaults."""
    cfg = dict(CONFIG)
    default_output_dir = str(cfg.get("output_dir", "output"))

    if output_dir is not None:
        cfg["output_dir"] = str(output_dir)
    if scratch_dir is not None:
        cfg["scratch_dir"] = str(scratch_dir)
    if mode is not None:
        cfg["screener_mode"] = (
            "mean_reversion"
            if str(mode).replace("-", "_") == "mean_reversion"
            else "momentum"
        )

    if input_file is not None:
        cfg["input_file"] = str(input_file)
    elif output_dir is not None and Path(output_dir) != Path(default_output_dir):
        try:
            cfg["input_file"] = _find_latest_xlsx(default_output_dir)
        except FileNotFoundError:
            # Let the quant pipeline raise its normal, more specific message.
            pass
    return cfg


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run IHSG quantitative filter.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory where top10_candidates.json should be written.",
    )
    parser.add_argument(
        "--input-file",
        default=None,
        help="Excel workbook to screen. Defaults to latest workbook in output/.",
    )
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="Directory for the markdown scan report.",
    )
    parser.add_argument(
        "--mode",
        default="momentum",
        choices=["momentum", "mean_reversion", "mean-reversion"],
        help="Screener strategy: momentum (default) or mean-reversion.",
    )
    args = parser.parse_args(argv)
    run_pipeline(
        build_config(
            output_dir=args.output_dir,
            input_file=args.input_file,
            scratch_dir=args.scratch_dir,
            mode=args.mode,
        )
    )


if __name__ == "__main__":
    main()
