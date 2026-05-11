from core.orchestrator import legacy as _legacy

ORCHESTRATOR_CONFIG = _legacy.ORCHESTRATOR_CONFIG


def __getattr__(name: str):
    if name in {"OUTPUT_DIR", "JSON_PATH", "FULL_RESULTS_PATH", "TOP3_REPORT_PATH"}:
        return getattr(_legacy, name)
    raise AttributeError(name)


def configure_output_dir(output_dir):
    return _legacy.configure_output_dir(output_dir)
