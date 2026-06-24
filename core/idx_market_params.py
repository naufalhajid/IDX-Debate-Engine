"""IDX market constants used across valuation, risk, and execution logic.

Keep this module pure: no settings import, no network calls. Runtime settings may
override these defaults, but deterministic helpers should read from here.
"""

from __future__ import annotations

from datetime import date

# === IDX Market Parameters ===
# Source: Damodaran Online country risk premium spreadsheet.
# Last updated: 2026-06-23, using Damodaran April 1, 2026 country ERP table.
DAMODARAN_COUNTRY_RISK_UPDATE = date(2026, 4, 1)
DAMODARAN_ANNUAL_REVIEW_MONTH = 1
DAMODARAN_ANNUAL_REVIEW_DAY = 15

MATURE_MARKET_PREMIUM = 0.0477
INDONESIA_CRP = 0.027801136981829278
INDONESIA_TOTAL_ERP = 0.07550113698182928

# SBN 10-year yield fallback. Live macro_refresh cache can override this.
INDONESIA_RISK_FREE = 0.0714

# === IDX Trading Constraints ===
T_PLUS_SETTLEMENT = 2
MIN_HOLD_DAYS = 2
LOT_SIZE = 100

# === Auto Rejection Limits (Kep-00002/BEI/04-2025, effective Apr 8 2025) ===
ARB_LOWER_LIMIT = 0.15
ARA_UPPER_PRICE_BELOW_50 = 0.35
ARA_UPPER_PRICE_50_200 = 0.25
ARA_UPPER_PRICE_ABOVE_200 = 0.20

# === Tick Size (Fraksi Harga) ===
TICK_BELOW_200 = 1
TICK_200_500 = 2
TICK_500_2000 = 5
TICK_2000_5000 = 10
TICK_ABOVE_5000 = 25

# === Signal Weights (IDX-calibrated based on factor research) ===
BULL_MOMENTUM_WEIGHT = 0.15
BULL_VALUE_WEIGHT = 0.40
BULL_QUALITY_WEIGHT = 0.30
BULL_TECHNICAL_WEIGHT = 0.15


def ara_upper_limit(price: float) -> float:
    """Return the daily ARA upper limit for a given IDX price."""
    if price <= 50:
        return ARA_UPPER_PRICE_BELOW_50
    if price <= 200:
        return ARA_UPPER_PRICE_50_200
    return ARA_UPPER_PRICE_ABOVE_200


def idx_tick_size(price: float) -> int:
    """Return the valid IDX tick size for a given price level."""
    if price < 200:
        return TICK_BELOW_200
    if price < 500:
        return TICK_200_500
    if price < 2000:
        return TICK_500_2000
    if price < 5000:
        return TICK_2000_5000
    return TICK_ABOVE_5000


def next_damodaran_review_date(last_update: date | None = None) -> date:
    """Return the January review date after the latest recorded Damodaran update."""
    ref = last_update or DAMODARAN_COUNTRY_RISK_UPDATE
    next_year = ref.year + 1
    return date(
        next_year,
        DAMODARAN_ANNUAL_REVIEW_MONTH,
        DAMODARAN_ANNUAL_REVIEW_DAY,
    )


def damodaran_review_due(today: date | None = None) -> bool:
    """True once the annual January ERP refresh should be re-checked."""
    return (today or date.today()) >= next_damodaran_review_date()
