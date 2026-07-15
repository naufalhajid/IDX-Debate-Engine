"""Compatibility CLI wrapper for the IHSG quantitative filter."""

from __future__ import annotations

import argparse
from pathlib import Path

from core.execution_regime import EXECUTION_REGIMES
from core.quant_filter.config import CONFIG, _find_latest_xlsx, canonical_screener_mode
from core.quant_filter.pipeline import run_pipeline

__all__ = ["CONFIG", "build_config", "main", "run_pipeline"]


def build_config(
    *,
    output_dir: str | Path | None = None,
    input_file: str | Path | None = None,
    scratch_dir: str | Path | None = None,
    mode: str | None = None,
    execution_regime: str | None = None,
    execution_regime_reason: str | None = None,
    trend_regime: str | None = None,
    volatility_regime: str | None = None,
) -> dict:
    """Build quant-filter config while keeping legacy defaults."""
    cfg = dict(CONFIG)
    default_output_dir = str(cfg.get("output_dir", "output"))

    if output_dir is not None:
        cfg["output_dir"] = str(output_dir)
    if scratch_dir is not None:
        cfg["scratch_dir"] = str(scratch_dir)
    if mode is not None:
        cfg["screener_mode"] = canonical_screener_mode(mode)
    if execution_regime is not None:
        regime_label = str(execution_regime).upper()
        if regime_label not in EXECUTION_REGIMES:
            allowed = ", ".join(sorted(EXECUTION_REGIMES))
            raise ValueError(
                f"execution_regime must be one of: {allowed}; got {execution_regime!r}"
            )
        cfg["execution_regime"] = regime_label
    if execution_regime_reason is not None:
        cfg["execution_regime_reason"] = str(execution_regime_reason)
    if trend_regime is not None:
        cfg["trend_regime"] = str(trend_regime).upper()
    if volatility_regime is not None:
        cfg["volatility_regime"] = str(volatility_regime).upper()

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
    parser.add_argument(
        "--execution-regime",
        default=None,
        type=str.upper,
        choices=sorted(EXECUTION_REGIMES),
    )
    parser.add_argument("--execution-regime-reason", default=None)
    parser.add_argument("--trend-regime", default=None)
    parser.add_argument("--volatility-regime", default=None)
    args = parser.parse_args(argv)
    run_pipeline(
        build_config(
            output_dir=args.output_dir,
            input_file=args.input_file,
            scratch_dir=args.scratch_dir,
            mode=args.mode,
            execution_regime=args.execution_regime,
            execution_regime_reason=args.execution_regime_reason,
            trend_regime=args.trend_regime,
            volatility_regime=args.volatility_regime,
        )
    )


if __name__ == "__main__":
    main()
