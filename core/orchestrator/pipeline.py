"""Professional import surface for the orchestrator pipeline.

The implementation still lives in ``core.orchestrator.legacy`` for backward
compatibility. New code should use ``PipelineRunner``/``PipelineRunConfig``;
legacy helpers are available through ``__getattr__`` instead of being eagerly
copied into this module namespace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import legacy as _legacy
from .runner import PipelineRunConfig, PipelineRunner, run_pipeline


OUTPUT_DIR = _legacy.OUTPUT_DIR
JSON_PATH = _legacy.JSON_PATH
FULL_RESULTS_PATH = _legacy.FULL_RESULTS_PATH
MERGED_RESULTS_PATH = _legacy.MERGED_RESULTS_PATH
TOP3_REPORT_PATH = _legacy.TOP3_REPORT_PATH

# Compatibility bindings used by the top-level CLI facade and presentation tests.
BatchProgressView = _legacy.BatchProgressView
CliRenderer = _legacy.CliRenderer
InteractiveCLI = _legacy.InteractiveCLI
configure_cli_logging = _legacy.configure_cli_logging
shlex = _legacy.shlex
_cli = _legacy._cli
_cli_renderer = _legacy._cli_renderer
_ensure_utf8_stdout = _legacy._ensure_utf8_stdout
_parse_cli_args = _legacy._parse_cli_args


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)


def _sync_path_globals() -> None:
    for _name in (
        "OUTPUT_DIR",
        "JSON_PATH",
        "FULL_RESULTS_PATH",
        "MERGED_RESULTS_PATH",
        "TOP3_REPORT_PATH",
    ):
        globals()[_name] = getattr(_legacy, _name)


def configure_output_dir(output_dir: Path) -> None:
    _legacy.configure_output_dir(output_dir)
    _sync_path_globals()


async def main(
    *,
    dry_run: bool = False,
    output_dir: Path | None = None,
    portfolio_state: dict | None = None,
    user_config: dict | None = None,
    mode: str | None = None,
    screener_mode: str | None = None,
    chamber_factory: Callable[[], Any] | None = None,
    tickers: list[str] | None = None,
    research_compare: bool = False,
    raise_on_error: bool = False,
) -> None:
    await run_pipeline(
        PipelineRunConfig(
            dry_run=dry_run,
            output_dir=output_dir,
            portfolio_state=portfolio_state,
            user_config=user_config,
            mode=mode,
            screener_mode=screener_mode,
            chamber_factory=chamber_factory,
            tickers=tickers,
            research_compare=research_compare,
            raise_on_error=raise_on_error,
        )
    )
    _sync_path_globals()


__all__ = [
    "BatchProgressView",
    "CliRenderer",
    "FULL_RESULTS_PATH",
    "InteractiveCLI",
    "JSON_PATH",
    "MERGED_RESULTS_PATH",
    "OUTPUT_DIR",
    "PipelineRunConfig",
    "PipelineRunner",
    "TOP3_REPORT_PATH",
    "configure_cli_logging",
    "configure_output_dir",
    "main",
    "run_pipeline",
]
