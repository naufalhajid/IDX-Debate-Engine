from core.orchestrator import pipeline as _pipeline

ORCHESTRATOR_CONFIG = _pipeline.ORCHESTRATOR_CONFIG


def __getattr__(name: str):
    if name in {
        "OUTPUT_DIR",
        "JSON_PATH",
        "FULL_RESULTS_PATH",
        "MERGED_RESULTS_PATH",
        "TOP3_REPORT_PATH",
    }:
        return getattr(_pipeline, name)
    raise AttributeError(name)


def configure_output_dir(output_dir):
    return _pipeline.configure_output_dir(output_dir)
