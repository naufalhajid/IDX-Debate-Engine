from typing import Any

from core.orchestrator import pipeline as _pipeline

for _name in dir(_pipeline):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_pipeline, _name)


def __getattr__(name: str) -> Any:
    return getattr(_pipeline, name)


def configure_output_dir(output_dir) -> None:
    _pipeline.configure_output_dir(output_dir)
    for _name in ("OUTPUT_DIR", "JSON_PATH", "FULL_RESULTS_PATH", "TOP3_REPORT_PATH"):
        globals()[_name] = getattr(_pipeline, _name)
