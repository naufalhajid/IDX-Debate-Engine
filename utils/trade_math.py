"""Shared trade math helpers for swing-trade setup calculations."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from utils.logger_config import logger


RR_TIERS_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "rr_tiers.yaml"
)
DEFAULT_RR_TIER_NAME = "default"
LARGE_CAP_TIER_NAME = "large_cap"

# Market cap threshold for large-cap classification (IDR). Rp 50 trillion
# captures LQ45-class stocks without pulling in mid-caps.
LARGE_CAP_THRESHOLD_IDR: int = 50_000_000_000_000
LARGE_CAP_RR_MINIMUM: float = 1.3
DEFAULT_RR_MINIMUM: float = 1.5


@dataclass(frozen=True)
class RRTierResolution:
    """Resolved R/R threshold metadata for one ticker."""

    ticker: str
    tier_name: str
    tier_label: str
    rr_minimum: float
    source: str
    market_cap_idr: int | None = None


def calculate_rr(entry_high: float, target: float, stop: float) -> float:
    """Return conservative risk/reward using entry_high as worst-case fill."""
    if stop >= entry_high:
        raise ValueError(
            f"stop ({stop}) must be below entry_high ({entry_high}) to calculate R/R"
        )
    risk = entry_high - stop
    reward = target - entry_high
    return round(reward / risk, 2)


def _get_market_cap_idr(
    ticker: str, yf_info: dict[str, Any] | None = None
) -> int | None:
    """
    Return market cap in IDR from a yfinance info dict.

    Args:
        ticker: Stock ticker symbol.
        yf_info: Cached yfinance ``Ticker.info`` dict, if available upstream.

    Returns:
        Market cap as integer IDR, or ``None`` when unavailable.
    """
    info = yf_info or {}
    market_cap = info.get("marketCap") if isinstance(info, dict) else None

    if (
        isinstance(market_cap, (int, float))
        and not isinstance(market_cap, bool)
        and market_cap > 0
    ):
        return int(market_cap)

    logger.debug(
        f"[RRTier] {ticker}: marketCap unavailable from yfinance info "
        f"(value={market_cap}). Will use static fallback."
    )
    return None


@lru_cache(maxsize=1)
def _load_largecap_fallback() -> set[str]:
    """
    Load fallback large-cap tickers from ``config/rr_tiers.yaml``.

    Returns:
        Uppercase ticker set. Missing or malformed config disables fallback and
        returns an empty set after logging a warning.
    """
    try:
        with RR_TIERS_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        if not isinstance(config, dict):
            raise ValueError("rr_tiers.yaml must contain a YAML mapping.")
        tickers = config.get("large_cap_fallback", [])
        if tickers is None:
            return set()
        if not isinstance(tickers, list):
            raise ValueError("large_cap_fallback must be a list.")
        return {
            str(ticker).upper().strip() for ticker in tickers if str(ticker).strip()
        }
    except FileNotFoundError:
        logger.warning(
            "[RRTier] config/rr_tiers.yaml not found. Static fallback disabled; "
            "all tickers without marketCap use DEFAULT threshold."
        )
        return set()
    except Exception as exc:
        logger.warning(f"[RRTier] Failed to load rr_tiers.yaml: {exc}")
        return set()


def get_rr_resolution(
    ticker: str,
    yf_info: dict[str, Any] | None = None,
) -> RRTierResolution:
    """
    Resolve the R/R threshold and source for a ticker.

    Resolution priority:
    1. Cached yfinance marketCap.
    2. Static large-cap fallback list.
    3. Default threshold.
    """
    ticker_upper = str(ticker or "").upper().strip()
    market_cap = _get_market_cap_idr(ticker_upper, yf_info)
    if market_cap is not None:
        if market_cap >= LARGE_CAP_THRESHOLD_IDR:
            resolution = RRTierResolution(
                ticker=ticker_upper,
                tier_name=LARGE_CAP_TIER_NAME,
                tier_label="Large Cap",
                rr_minimum=LARGE_CAP_RR_MINIMUM,
                source="market_cap",
                market_cap_idr=market_cap,
            )
        else:
            resolution = RRTierResolution(
                ticker=ticker_upper,
                tier_name=DEFAULT_RR_TIER_NAME,
                tier_label="Default",
                rr_minimum=DEFAULT_RR_MINIMUM,
                source="market_cap",
                market_cap_idr=market_cap,
            )
        _log_rr_resolution(resolution)
        return resolution

    if ticker_upper in _load_largecap_fallback():
        resolution = RRTierResolution(
            ticker=ticker_upper,
            tier_name=LARGE_CAP_TIER_NAME,
            tier_label="Large Cap",
            rr_minimum=LARGE_CAP_RR_MINIMUM,
            source="static_fallback",
            market_cap_idr=None,
        )
        _log_rr_resolution(resolution)
        return resolution

    resolution = RRTierResolution(
        ticker=ticker_upper,
        tier_name=DEFAULT_RR_TIER_NAME,
        tier_label="Default",
        rr_minimum=DEFAULT_RR_MINIMUM,
        source="static_default",
        market_cap_idr=None,
    )
    _log_rr_resolution(resolution)
    return resolution


def get_rr_minimum(ticker: str, yf_info: dict[str, Any] | None = None) -> float:
    """
    Return the minimum acceptable R/R threshold for a ticker.

    Cached yfinance marketCap is preferred. When unavailable, the static YAML
    fallback list is used; otherwise the default threshold is returned.
    """
    return get_rr_resolution(ticker, yf_info=yf_info).rr_minimum


def get_rr_tier_name(ticker: str, yf_info: dict[str, Any] | None = None) -> str:
    """Return the resolved R/R tier name for a ticker."""
    return get_rr_resolution(ticker, yf_info=yf_info).tier_name


def get_rr_tier_label(ticker: str, yf_info: dict[str, Any] | None = None) -> str:
    """Return the human-readable resolved R/R tier label for a ticker."""
    return get_rr_resolution(ticker, yf_info=yf_info).tier_label


def format_rr_resolution_context(resolution: RRTierResolution) -> str:
    """Return a human-readable threshold source suffix for coherence messages."""
    if resolution.source == "market_cap" and resolution.market_cap_idr is not None:
        market_cap_t = resolution.market_cap_idr / 1_000_000_000_000
        return f"({resolution.tier_name} tier - marketCap Rp {market_cap_t:.0f}T)"
    if resolution.source == "static_fallback":
        return f"({resolution.tier_name} tier - static fallback)"
    return f"({resolution.tier_name} tier)"


def _log_rr_resolution(resolution: RRTierResolution) -> None:
    """Emit auditable DEBUG logging for R/R tier resolution."""
    if resolution.source == "market_cap" and resolution.market_cap_idr is not None:
        logger.debug(
            f"[RRTier] {resolution.ticker} -> tier={resolution.tier_name}, "
            f"source=market_cap, marketCap=Rp "
            f"{resolution.market_cap_idr / 1_000_000_000_000:.1f}T, "
            f"rr_minimum={resolution.rr_minimum:.1f}x"
        )
        return
    logger.debug(
        f"[RRTier] {resolution.ticker} -> tier={resolution.tier_name}, "
        f"source={resolution.source}, rr_minimum={resolution.rr_minimum:.1f}x"
    )
