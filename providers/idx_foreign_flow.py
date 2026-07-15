"""Per-ticker foreign net flow from Stockbit findata-view endpoint."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from urllib.parse import quote

from utils.ticker import InvalidIDXTicker, normalize_idx_ticker

logger = logging.getLogger(__name__)

_BASE_URL = "https://exodus.stockbit.com"
_PERIOD = "PERIOD_RANGE_1D"
_MARKET_TYPE = "MARKET_TYPE_REGULAR"


@dataclass
class ForeignFlowSnapshot:
    ticker: str
    net_foreign_flow_m: float | None     # net IDR flow in millions; positive = net buy
    foreign_buy_m: float | None          # gross foreign buy, millions IDR
    foreign_sell_m: float | None         # gross foreign sell, millions IDR
    foreign_vol_pct: float | None        # foreign % of total traded volume (buy+sell)
    net_foreign_vol: int | None          # net foreign volume in shares
    is_net_foreign_buy: bool | None      # True if foreigners are net buyers
    as_of_date: str | None               # "YYYY-MM-DD"
    source: str = "stockbit_foreign_flow"


def _empty(ticker: str) -> ForeignFlowSnapshot:
    return ForeignFlowSnapshot(
        ticker=ticker,
        net_foreign_flow_m=None,
        foreign_buy_m=None,
        foreign_sell_m=None,
        foreign_vol_pct=None,
        net_foreign_vol=None,
        is_net_foreign_buy=None,
        as_of_date=None,
    )


def _normalize_ticker(ticker: str) -> str:
    """Normalize local ticker symbols before building the Stockbit URL."""
    if ticker is None or not str(ticker).strip():
        return ""
    return normalize_idx_ticker(ticker)


def _safe_raw(mapping: dict, key: str) -> float | None:
    """Extract .value.raw from a Stockbit value-node."""
    node = mapping.get(key)
    if not isinstance(node, dict):
        return None
    val = node.get("value", {})
    raw = val.get("raw") if isinstance(val, dict) else None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _safe_pct(mapping: dict, key: str) -> float | None:
    """Extract .percentage.raw from a Stockbit volume-node."""
    node = mapping.get(key)
    if not isinstance(node, dict):
        return None
    pct = node.get("percentage", {})
    raw = pct.get("raw") if isinstance(pct, dict) else None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def fetch_foreign_flow(ticker: str, client=None) -> ForeignFlowSnapshot:
    """
    Fetch net foreign flow for one ticker via Stockbit's findata-view endpoint.

    Returns a ForeignFlowSnapshot with all-None values on any failure so callers
    can always proceed without a guard.
    """
    try:
        ticker = _normalize_ticker(ticker)
    except InvalidIDXTicker as exc:
        logger.warning("[ForeignFlow] invalid ticker rejected before fetch: %s", exc)
        return _empty("")
    if not ticker:
        return _empty(ticker)

    if client is None:
        from services.stockbit_api_client import StockbitApiClient
        client = StockbitApiClient()

    url = (
        f"{_BASE_URL}/findata-view/foreign-domestic/v1/chart-data/{quote(ticker, safe='')}"
        f"?market_type={_MARKET_TYPE}&period={_PERIOD}"
    )

    try:
        resp = client.get(url)
    except Exception as exc:
        logger.warning("[ForeignFlow] fetch failed for %s: %s", ticker, exc)
        return _empty(ticker)

    if not isinstance(resp, dict) or not isinstance(resp.get("data"), dict):
        logger.warning("[ForeignFlow] unexpected response shape for %s", ticker)
        return _empty(ticker)

    data = resp["data"]
    summary = data.get("summary", {})
    volume_section = data.get("volume", {})
    summary_volume = summary.get("volume", {})

    net_raw = _safe_raw(summary, "net_foreign")
    buy_raw = _safe_raw(summary, "foreign_buy")
    sell_raw = _safe_raw(summary, "foreign_sell")
    net_vol_raw = _safe_raw(summary_volume, "net_foreign_reguler")
    foreign_vol_pct = _safe_pct(volume_section, "foreign_total")

    def _to_m(v: float | None) -> float | None:
        return round(v / 1_000_000, 2) if v is not None else None

    net_m = _to_m(net_raw)
    return ForeignFlowSnapshot(
        ticker=ticker,
        net_foreign_flow_m=net_m,
        foreign_buy_m=_to_m(buy_raw),
        foreign_sell_m=_to_m(sell_raw),
        foreign_vol_pct=foreign_vol_pct,
        net_foreign_vol=int(net_vol_raw) if net_vol_raw is not None else None,
        is_net_foreign_buy=(net_m > 0) if net_m is not None else None,
        as_of_date=data.get("from"),
    )
