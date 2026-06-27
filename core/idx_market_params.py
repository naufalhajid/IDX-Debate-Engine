"""IDX market constants used across valuation, risk, and execution logic.

Keep this module pure: no settings import, no network calls. Runtime settings may
override these defaults, but deterministic helpers should read from here.
"""

from __future__ import annotations

from datetime import date

# === IDX Market Parameters ===
# Source: Damodaran Online country risk premium spreadsheet, Jan 5 2026.
# MATURE_MARKET_ERP: implied S&P 500 ERP (Damodaran Jan 2026) = 4.23%
# INDONESIA_CRP: country risk premium Indonesia (Damodaran Jan 2026) = 2.46%
# INDONESIA_TOTAL_ERP = MATURE_MARKET_ERP + INDONESIA_CRP = 6.69%
DAMODARAN_COUNTRY_RISK_UPDATE = date(2026, 1, 5)
DAMODARAN_ANNUAL_REVIEW_MONTH = 1
DAMODARAN_ANNUAL_REVIEW_DAY = 15

MATURE_MARKET_ERP = 0.0423
INDONESIA_CRP = 0.0246
INDONESIA_TOTAL_ERP = 0.0669

# SBN 10-year yield fallback (~6.5% Juni 2026). Live macro_refresh cache can override.
INDONESIA_RISK_FREE = 0.065

# BI Rate (cut cycle mid-2025, per Juni 2026) and corporate tax rate (PPh Badan)
BI_RATE = 0.0575
INDONESIA_TAX_RATE = 0.22

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

# === IHSG Market Context 2026 ===
# Update IHSG_CURRENT manually each session; other values are historical facts.
IHSG_ATH_2026 = 9174.47       # All-time high, Jan 9 2026
IHSG_LOW_2026 = 5317.91       # Year low, Jun 8 2026 (bottom of crash)
IHSG_CURRENT = 6137.0         # Estimated Jun 2026; update manually each session
IHSG_YTD_RETURN_2026 = -0.29  # -29% YTD through May 2026
IHSG_PE_CURRENT = 10.15       # P/E as of Jun 18 2026 (vs historical 13–14x mean)
# MSCI Global Accessibility Review: Indonesia retained EM status Jun 19 2026,
# annual review extended to Nov 2026. Set False after Nov 2026 review resolves.
MSCI_REVIEW_ACTIVE = True

# === HMM → Legacy Regime Bridge ===
# Single source of truth for mapping the new 3-state HMM vocabulary
# (BULL/SIDEWAYS/BEAR_STRESS) to the old 5-state vol-threshold vocabulary
# (DEFENSIVE/RECOVERY/HIGH/NORMAL/LOW) used by risk_governor, trade_math,
# and technicals.  Consumers that only know the old labels should call:
#   legacy = HMM_TO_LEGACY_REGIME.get(hmm_label, "NORMAL")
# SIDEWAYS maps to HIGH (not NORMAL) so ATR/R-R scaling stays cautious;
# UNKNOWN maps to DEFENSIVE so the system defaults to maximum caution.
HMM_TO_LEGACY_REGIME: dict[str, str] = {
    "BULL":        "NORMAL",
    "SIDEWAYS":    "HIGH",
    "BEAR_STRESS": "DEFENSIVE",
    "UNKNOWN":     "DEFENSIVE",
}

# === Regime-Aware Trading Rules ===
# Consumed by IDXRegimeDetector.get_trading_rules() and the LangGraph regime_gate node.
# Research basis: MARCD (arXiv 2510.10807) K=3 BIC optimum + IDX crash context Jun 2026.
REGIME_RULES: dict[str, dict] = {
    "BULL": {
        "description": "IHSG uptrend, low volatility, foreign net buy",
        "max_position_pct": 0.020,       # 2% per position
        "min_risk_reward": 1.5,
        "consensus_threshold": 0.60,     # 60% agent agreement required
        "max_concurrent_positions": 3,
        "devil_advocate_weight": 0.20,
        "regime_multiplier": 1.0,        # bull-analyst confidence multiplier
        "trading_allowed": True,
    },
    "SIDEWAYS": {
        "description": "IHSG flat, moderate volatility",
        "max_position_pct": 0.010,
        "min_risk_reward": 2.0,
        "consensus_threshold": 0.70,
        "max_concurrent_positions": 2,
        "devil_advocate_weight": 0.30,
        "regime_multiplier": 0.85,
        "trading_allowed": True,
    },
    "BEAR_STRESS": {
        "description": "IHSG downtrend, high volatility, foreign net sell",
        "max_position_pct": 0.005,       # 0.5% per position — very tight
        "min_risk_reward": 2.5,
        "consensus_threshold": 0.80,     # 80% agreement — highest bar
        "max_concurrent_positions": 1,
        "devil_advocate_weight": 0.40,
        "regime_multiplier": 0.60,
        "trading_allowed": True,
        "note": "Log warning if bear_stress confidence > 0.85",
    },
    "UNKNOWN": {
        "description": "Regime undetermined — insufficient data",
        "max_position_pct": 0.005,
        "min_risk_reward": 2.5,
        "consensus_threshold": 0.80,
        "max_concurrent_positions": 0,  # pause trading
        "devil_advocate_weight": 0.50,
        "regime_multiplier": 0.50,
        "trading_allowed": False,
    },
}


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
