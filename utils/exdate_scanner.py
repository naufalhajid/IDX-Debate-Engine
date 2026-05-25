"""
utils/exdate_scanner.py — Dividend Ex-Date Scanner untuk IHSG Swing Trade.

Mencegah "Dividend Trap": membeli saham yang akan ex-dividend dalam 
window swing trade, menyebabkan price drop sebesar dividend yield 
tepat di ex-date (umum di IHSG, terutama Mar-Jun).

Pipeline integration:
  - run_quant_filter.py : hard exclude CRITICAL, soft flag WARNING
  - debate_chamber.py   : injeksi ExDateInfo ke synthesizer sebagai 
                          FACTUAL BLOCK untuk CIO consideration
"""

from utils.logger_config import logger
from datetime import datetime, timezone
from typing import TypedDict

import pandas as pd


# ── Constants ────────────────────────────────────────────────────────────────


def _get_yfinance():
    import yfinance as yf

    return yf

CRITICAL_WINDOW_DAYS = 7    # Hard exclude — terlalu dekat untuk swing entry
WARNING_WINDOW_DAYS  = 30   # Soft flag — masuk debate dengan metadata


# ── Output Schema ────────────────────────────────────────────────────────────

class ExDateInfo(TypedDict):
    has_upcoming_exdate : bool
    ex_date             : str | None
    days_until_exdate   : int | None
    div_per_share       : float | None
    div_yield_pct       : float | None
    risk_tier           : str           # "CRITICAL" | "WARNING" | "CLEAR"
    expected_drop_rp    : float | None
    source              : str


# ── Core Scanner ─────────────────────────────────────────────────────────────

def scan_exdate(ticker: str, current_price: float = 0.0) -> ExDateInfo:
    """
    Fetch upcoming ex-dividend date for an IHSG ticker via yfinance.

    Args:
        ticker        : IHSG stock code tanpa suffix, e.g. "BBRI"
        current_price : Last traded price (IDR). Used to calculate div_yield_pct.
                        Pass 0.0 to skip yield calculation.

    Returns:
        ExDateInfo dict. Never raises — returns CLEAR with source="unavailable"
        on any fetch failure so pipeline is never blocked.
    """
    _CLEAR: ExDateInfo = {
        "has_upcoming_exdate" : False,
        "ex_date"             : None,
        "days_until_exdate"   : None,
        "div_per_share"       : None,
        "div_yield_pct"       : None,
        "risk_tier"           : "CLEAR",
        "expected_drop_rp"    : None,
        "source"              : "unavailable",
    }

    try:
        t = _get_yfinance().Ticker(f"{ticker}.JK")

        # ── 1. Get upcoming dividends from yfinance calendar ─────────────────
        # yfinance returns a dict with "Ex-Dividend Date" as a Timestamp or None
        cal = t.calendar  # dict | None
        ex_date_ts = None

        if isinstance(cal, dict):
            ex_date_ts = cal.get("Ex-Dividend Date")
        elif isinstance(cal, pd.DataFrame):
            # Older yfinance versions return DataFrame
            if "Ex-Dividend Date" in cal.index:
                ex_date_ts = cal.loc["Ex-Dividend Date"].iloc[0]

        if ex_date_ts is None:
            logger.info(f"[ExDate] {ticker}: no ex-date found in calendar")
            return {**_CLEAR, "source": "yfinance"}

        # Normalize to date
        if hasattr(ex_date_ts, "date"):
            ex_date = ex_date_ts.date()
        else:
            ex_date = pd.Timestamp(ex_date_ts).date()

        today = datetime.now(timezone.utc).date()
        days_until = (ex_date - today).days

        # ── 2. Only flag if ex-date is upcoming (not past) ──────────────────
        if days_until < 0:
            logger.info(f"[ExDate] {ticker}: ex-date {ex_date} is in the past ({days_until}d ago)")
            return {**_CLEAR, "source": "yfinance"}

        # ── 3. Get dividend amount from dividends history ────────────────────
        div_per_share: float | None = None
        try:
            divs = t.dividends
            if divs is not None and len(divs) > 0:
                # Most recent dividend as proxy for upcoming
                div_per_share = float(divs.iloc[-1])
        except Exception:
            pass

        # ── 4. Calculate yield if price available ────────────────────────────
        div_yield_pct: float | None = None
        if div_per_share and current_price > 0:
            div_yield_pct = round((div_per_share / current_price) * 100, 2)

        # ── 5. Assign risk tier ──────────────────────────────────────────────
        if days_until <= CRITICAL_WINDOW_DAYS:
            risk_tier = "CRITICAL"
        elif days_until <= WARNING_WINDOW_DAYS:
            risk_tier = "WARNING"
        else:
            risk_tier = "CLEAR"

        result: ExDateInfo = {
            "has_upcoming_exdate" : risk_tier != "CLEAR",
            # M2 fix: jangan tampilkan tanggal ex-date jika sudah lewat atau CLEAR
            # — trader bisa salah baca tanggal 2025 sebagai upcoming
            "ex_date"             : str(ex_date) if risk_tier != "CLEAR" else None,
            "days_until_exdate"   : days_until if risk_tier != "CLEAR" else None,
            "div_per_share"       : div_per_share,
            "div_yield_pct"       : div_yield_pct,
            "risk_tier"           : risk_tier,
            "expected_drop_rp"    : div_per_share if risk_tier != "CLEAR" else None,
            "source"              : "yfinance",
        }

        logger.info(
            f"[ExDate] {ticker}: ex={ex_date}, days={days_until}, "
            f"tier={risk_tier}, div=Rp{div_per_share}, yield={div_yield_pct}%"
        )
        return result

    except Exception as e:
        logger.warning(f"[ExDate] {ticker}: fetch failed — {e}")
        return _CLEAR


def format_exdate_block(ticker: str, info: ExDateInfo) -> str:
    """
    Format ExDateInfo sebagai teks faktual untuk diinjeksi ke CIO context.
    Dirancang agar CIO bisa langsung membaca dan membuat keputusan.
    """
    if not info["has_upcoming_exdate"]:
        return f"=== DIVIDEND EX-DATE SCAN: {ticker} ===\nStatus: CLEAR — Tidak ada ex-dividend date dalam 30 hari ke depan.\n"

    tier_label = {
        "CRITICAL": "🔴 CRITICAL — Ex-date dalam 7 hari",
        "WARNING":  "🟠 WARNING  — Ex-date dalam 30 hari",
    }.get(info["risk_tier"], "CLEAR")

    div_str = f"Rp {info['div_per_share']:,.0f}" if info["div_per_share"] else "N/A"
    yield_str = f"{info['div_yield_pct']:.2f}%" if info["div_yield_pct"] else "N/A"
    drop_str = f"~Rp {info['expected_drop_rp']:,.0f}" if info["expected_drop_rp"] else "N/A"

    return (
        f"=== DIVIDEND EX-DATE SCAN: {ticker} ===\n"
        f"Risk Tier         : {tier_label}\n"
        f"Ex-Dividend Date  : {info['ex_date']} ({info['days_until_exdate']} hari lagi)\n"
        f"Dividend/Share    : {div_str}\n"
        f"Estimated Yield   : {yield_str}\n"
        f"Expected Price Drop on Ex-Date: {drop_str}\n"
        f"\n"
        f"⚠️  CIO NOTE: Saham ini akan mengalami price drop sebesar ~{drop_str} "
        f"pada {info['ex_date']}. Jika swing trade horizon mencakup tanggal ini, "
        f"target price harus di-adjust downward sebesar dividend amount, "
        f"dan R/R ratio efektif lebih buruk dari yang terkalkulasi.\n"
    )
