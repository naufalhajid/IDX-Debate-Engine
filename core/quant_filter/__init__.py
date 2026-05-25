"""Internal modules for the IHSG quantitative filter."""

from core.quant_filter.config import CONFIG


def __getattr__(name: str):
    if name == "run_pipeline":
        from core.quant_filter.pipeline import run_pipeline

        return run_pipeline
    raise AttributeError(name)

__all__ = ["CONFIG", "run_pipeline"]
