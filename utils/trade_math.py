"""Shared trade math helpers for swing-trade setup calculations."""


def calculate_rr(entry_high: float, target: float, stop: float) -> float:
    """Return conservative risk/reward using entry_high as worst-case fill."""
    if stop >= entry_high:
        raise ValueError(
            f"stop ({stop}) must be below entry_high ({entry_high}) to calculate R/R"
        )
    risk = entry_high - stop
    reward = target - entry_high
    return round(reward / risk, 2)
