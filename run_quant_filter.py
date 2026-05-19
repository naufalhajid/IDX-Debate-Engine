"""Compatibility CLI wrapper for the IHSG quantitative filter."""

from core.quant_filter.config import CONFIG
from core.quant_filter.pipeline import run_pipeline

__all__ = ["CONFIG", "run_pipeline"]


if __name__ == "__main__":
    run_pipeline(CONFIG)
