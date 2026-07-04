from __future__ import annotations

import typer

PIPELINE_MODES = {"multi", "single", "compare"}

_SCREENER_MODE_ALIASES = {
    "momentum": "momentum",
    "mom": "momentum",
    "trend": "momentum",
    "mean_reversion": "mean_reversion",
    "meanreversion": "mean_reversion",
    "mr": "mean_reversion",
    "reversion": "mean_reversion",
}


def _clean_token(value: str) -> str:
    return (value or "").strip().lower().replace("-", "_")


def normalize_pipeline_mode(value: str) -> str:
    mode = _clean_token(value)
    if mode in PIPELINE_MODES:
        return mode
    raise typer.BadParameter("pipeline mode must be one of: multi, single, compare")


def normalize_screener_mode(value: str) -> str:
    mode = _clean_token(value)
    if mode in _SCREENER_MODE_ALIASES:
        return _SCREENER_MODE_ALIASES[mode]
    raise typer.BadParameter(
        "screener mode must be one of: momentum, mom, trend, "
        "mean-reversion, mean_reversion, meanreversion, mr, reversion"
    )


def is_pipeline_mode_token(value: str) -> bool:
    return _clean_token(value) in PIPELINE_MODES


def is_screener_mode_token(value: str) -> bool:
    return _clean_token(value) in _SCREENER_MODE_ALIASES


def format_screener_mode(mode: str) -> str:
    normalized = normalize_screener_mode(mode)
    return "mean-reversion" if normalized == "mean_reversion" else "momentum"
