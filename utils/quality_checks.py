"""
utils/quality_checks.py — Post-hoc quality verification for CIOVerdict output.

Task 30: These checks are advisory — they identify narrative gaps and
structural issues that Pydantic's model_validator cannot enforce (non-empty
strings, populated lists, actionable context on BUY/SELL verdicts).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.debate import CIOVerdict


def check_verdict_quality(verdict: "CIOVerdict") -> list[str]:
    """Return a list of quality issue strings. Empty list = all checks pass.

    Designed to be called after the debate pipeline produces a CIOVerdict, before
    report generation. Issues are descriptive strings suitable for logging or test
    assertion output.
    """
    issues: list[str] = []
    actionable = verdict.rating in ("STRONG_BUY", "BUY", "SELL")

    # 1. Weighted reasoning must be non-empty for actionable verdicts
    if actionable and not verdict.weighted_reasoning.strip():
        issues.append("weighted_reasoning is empty on an actionable verdict")

    # 2. Critical risk factor must be non-empty for actionable verdicts
    if actionable and not verdict.critical_risk_factor.strip():
        issues.append("critical_risk_factor is empty on an actionable verdict")

    # 3. At least one risk must be listed (validator auto-appends FV warning if missing FV,
    #    but that only fires when fair_value is None — this catches the zero-risk case after that)
    if not verdict.key_risks:
        issues.append("key_risks is empty")

    # 4. BUY verdicts must have at least one catalyst
    if verdict.rating in ("STRONG_BUY", "BUY") and not verdict.key_catalysts:
        issues.append("key_catalysts is empty on a BUY verdict")

    # 5. Executive summary must be non-empty (drives Svelte trade card)
    if not verdict.summary.strip():
        issues.append("summary is empty")

    # 6. If price levels are set, R/R must have been computable
    if (
        verdict.entry_price_range
        and verdict.target_price is not None
        and verdict.stop_loss is not None
        and verdict.risk_reward_ratio is None
    ):
        issues.append("risk_reward_ratio is None despite price levels being set")

    # 7. Actionable verdicts must have an entry range for the trade card
    if actionable and verdict.entry_price_range is None:
        issues.append("entry_price_range is None on an actionable verdict")

    return issues
