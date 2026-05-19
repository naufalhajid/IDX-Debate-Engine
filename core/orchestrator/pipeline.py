"""Professional import surface for the orchestrator pipeline.

The implementation still lives in ``core.orchestrator.legacy`` for backward
compatibility with existing scripts and tests. New code should prefer this
module, while old imports continue to work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)


def configure_output_dir(output_dir: Path) -> None:
    _legacy.configure_output_dir(output_dir)
    for _name in ("OUTPUT_DIR", "JSON_PATH", "FULL_RESULTS_PATH", "TOP3_REPORT_PATH"):
        globals()[_name] = getattr(_legacy, _name)
