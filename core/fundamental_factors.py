"""IDX-calibrated fundamental factor helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "-", "N/A"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_ocf_price_ratio(ocf: float, shares: float, price: float) -> float:
    """Return OCF/Price = (operating cash flow / shares) / price.

    Inputs can be absolute currency values. The return is a decimal yield, e.g.
    0.12 means OCF per share is 12% of the current stock price.
    """
    ocf_value = _as_float(ocf)
    shares_value = _as_float(shares)
    price_value = _as_float(price)
    if ocf_value <= 0 or shares_value <= 0 or price_value <= 0:
        return 0.0
    return (ocf_value / shares_value) / price_value


def calculate_rnoa(data: Mapping[str, Any]) -> float:
    """Return RNOA when enough operating data exists, otherwise 0.0."""
    direct = _as_float(data.get("rnoa"))
    if direct:
        return direct / 100.0 if direct > 1.0 else direct

    operating_income = _as_float(
        data.get("operating_income")
        or data.get("operating_profit")
        or data.get("ebit")
    )
    tax_rate = _as_float(data.get("tax_rate"), 0.22)
    avg_noa = _as_float(
        data.get("average_net_operating_assets")
        or data.get("avg_net_operating_assets")
        or data.get("net_operating_assets")
    )
    if operating_income > 0 and avg_noa > 0:
        tax_rate = tax_rate / 100.0 if tax_rate > 1.0 else tax_rate
        nopat = operating_income * (1.0 - max(0.0, min(tax_rate, 1.0)))
        return nopat / avg_noa
    return 0.0


def calculate_profitability_score(data: dict) -> float:
    """Return a 0..1 quality score using RNOA first, then ROA fallback.

    IDX4-inspired research prefers operating profitability over ROE. This
    stock-level helper is not the published factor model. When RNOA
    cannot be calculated from available data, ROA is used as the lower-fidelity
    approximation requested by the recalibration brief.
    """
    rnoa = calculate_rnoa(data)
    if rnoa <= 0:
        roa = _as_float(data.get("roa") or data.get("return_on_assets"))
        rnoa = roa / 100.0 if roa > 1.0 else roa
    if rnoa <= 0:
        return 0.0
    if rnoa >= 0.20:
        return 1.00
    if rnoa >= 0.12:
        return 0.70
    return 0.40
