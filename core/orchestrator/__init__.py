from typing import Any

from core.orchestrator import legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)


def configure_output_dir(output_dir) -> None:
    _legacy.configure_output_dir(output_dir)
    for _name in ("OUTPUT_DIR", "JSON_PATH", "FULL_RESULTS_PATH", "TOP3_REPORT_PATH"):
        globals()[_name] = getattr(_legacy, _name)
