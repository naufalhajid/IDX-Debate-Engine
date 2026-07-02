"""
scripts/historical_backtest.py — OHLCV-only historical replay to measure envelope EV.

Downloads 1-3yr daily OHLCV via yfinance and replays the pipeline's stop/target geometry
on each trading day to measure win rate, avg PnL%, and avg holding days.

Scope: OHLCV-only (no fundamental XLSX — point-in-time only). Answers the question:
  "Given the stop/target geometry, what was the win rate on technical entries over N years?"

Usage:
  python scripts/historical_backtest.py --tickers BBCA,BMRI,TLKM --years 1
  python scripts/historical_backtest.py --tickers BBCA --years 2 --output results.json

Reuses: compute_rsi, compute_atr, compute_swing_low, snap_to_tick from utils/technicals.

WARNING: Envelope constants below mirror production debate_chamber.py. When production
constants are retuned, update these values to keep backtest results representative.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.technicals import compute_atr, compute_rsi, snap_to_tick

# ── Envelope parameters (mirror production debate_chamber.py — keep in sync) ─
MAX_TARGET_RETURN_NO_FV = 0.10   # 10% cap without fair-value anchor
NOISE_GATE_MULTIPLIER   = 1.5    # stop_distance >= 1.5 × ATR14 required
REGIME_ATR_MULTIPLIER   = 2.5    # neutral regime multiplier (no live regime in replay)
ATR_SWING_LOW_BUFFER    = 0.5    # buffer below swing low for structural stop

# ── Screener gates (mirror production config.py) ──────────────────────────────
RSI_MAX             = 70
ATR_PCT_MAX         = 0.04
MA50_ENTRY_DISCOUNT = 0.03

# ── Evaluation ────────────────────────────────────────────────────────────────
HORIZON_TRADING_DAYS = 20


@dataclass
class TradeResult:
    ticker: str
    entry_date: str
    entry_price: float
    target_price: float
    stop_loss: float
    exit_date: str | None = None
    exit_price: float | None = None
    outcome: str = "open"   # win | loss | timeout_flat | open
    pnl_pct: float | None = None
    holding_days: int | None = None


def _compute_envelope(
    current_price: float,
    sma20: float,
    ma50: float | None,
    atr14: float,
    high_20d: float,
    high_50d: float,
    high_52w: float,
    low_20d: float,
    low_50d: float,
) -> dict | None:
    """Simplified inline envelope — mirrors _compute_trade_envelope without self deps."""
    if ma50 and ma50 > 0 and current_price > 0:
        entry_high = snap_to_tick(min(ma50 * 1.02, current_price))
        entry_low  = snap_to_tick(min(ma50, current_price * 0.97))
    else:
        entry_high = snap_to_tick(current_price)
        entry_low  = snap_to_tick(current_price * 0.97)

    if entry_low >= entry_high:
        entry_high = snap_to_tick(current_price)
        entry_low  = snap_to_tick(current_price * 0.97)
    if entry_low >= entry_high:
        return None

    entry_mid = (entry_low + entry_high) / 2

    if atr14 > 0 and sma20 > 0:
        swing_low = min(low_20d, low_50d)
        structural_stop = swing_low - (ATR_SWING_LOW_BUFFER * atr14)
        atr_stop = current_price - (REGIME_ATR_MULTIPLIER * atr14)
        stop = snap_to_tick(max(structural_stop, atr_stop, current_price * 0.92))
    else:
        stop = snap_to_tick(entry_mid * 0.96)

    if stop >= entry_low:
        stop = snap_to_tick(entry_low * 0.96)
    if stop >= entry_low:
        return None

    if atr14 > 0 and (entry_high - stop) < NOISE_GATE_MULTIPLIER * atr14:
        return None

    risk = entry_high - stop
    target_candidate = max(entry_high + risk * 2.0, entry_mid * 1.04)

    # Three resistance tiers: 20d high → 50d high → 52w high (matching production)
    for resistance in (high_20d, high_50d, high_52w):
        if resistance >= target_candidate:
            target_candidate = resistance
            break

    target = snap_to_tick(target_candidate)
    capped  = snap_to_tick(entry_high * (1 + MAX_TARGET_RETURN_NO_FV))
    if 0 < capped < target:
        target = capped

    if target <= entry_high:
        return None

    return {"entry": entry_high, "target": target, "stop": stop}


def _evaluate_trade(
    ohlcv: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    target_price: float,
    stop_loss: float,
) -> dict:
    bars = ohlcv.iloc[entry_idx + 1 : entry_idx + 1 + HORIZON_TRADING_DAYS]
    for i, (date, row) in enumerate(bars.iterrows()):
        hit_stop   = row["Low"]  <= stop_loss
        hit_target = row["High"] >= target_price
        if hit_stop and hit_target:
            return {"outcome": "loss", "exit_date": str(date.date()), "exit_price": stop_loss, "days": i + 1}
        if hit_stop:
            return {"outcome": "loss", "exit_date": str(date.date()), "exit_price": stop_loss, "days": i + 1}
        if hit_target:
            return {"outcome": "win", "exit_date": str(date.date()), "exit_price": target_price, "days": i + 1}

    if len(bars) == 0:
        return {"outcome": "open", "exit_date": None, "exit_price": None, "days": None}

    last_close = float(bars["Close"].iloc[-1])
    last_date  = str(bars.index[-1].date())
    within_2pct = abs(last_close - entry_price) / entry_price <= 0.02
    outcome = "timeout_flat" if within_2pct else ("win" if last_close > entry_price else "loss")
    return {"outcome": outcome, "exit_date": last_date, "exit_price": last_close, "days": HORIZON_TRADING_DAYS}


def replay_ticker(ticker: str, years: int) -> list[TradeResult]:
    symbol = ticker if ticker.endswith(".JK") else f"{ticker}.JK"
    try:
        df = yf.download(symbol, period=f"{years + 1}y", auto_adjust=True, progress=False)
    except Exception as exc:
        print(f"  [{ticker}] download failed: {exc}")
        return []

    if df is None or len(df) < 120:
        print(f"  [{ticker}] insufficient data ({len(df) if df is not None else 0} bars)")
        return []

    # yfinance >=0.2.x returns MultiIndex columns for single-ticker downloads
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()

    if not isinstance(close, pd.Series):
        print(f"  [{ticker}] unexpected DataFrame shape after squeeze — skipping")
        return []

    # Precompute all indicator series once — O(N) instead of O(N²) per bar
    # Reindex to df.index: compute_rsi/atr internally drop the first row, making
    # them one element shorter; reindex restores alignment with NaN at [0].
    rsi_series      = compute_rsi(close).reindex(df.index)
    atr_series      = compute_atr(high, low, close, 14).reindex(df.index)
    ma50_series     = close.rolling(50).mean()
    sma20_series    = close.rolling(20).mean()
    high20_series   = high.rolling(20).max()
    high50_series   = high.rolling(50).max()
    high52w_series  = high.rolling(252).max()
    low20_series    = low.rolling(20).min()
    low50_series    = low.rolling(50).min()
    return_5d_series = close.pct_change(5) * 100  # mirrors tech["return_5d_pct"]

    trades: list[TradeResult] = []
    occupied: set[int] = set()

    for i in range(120, len(df)):
        if i in occupied:
            continue

        price   = float(close.iloc[i])
        rsi_val = float(rsi_series.iloc[i]) if pd.notna(rsi_series.iloc[i]) else 50.0
        atr14   = float(atr_series.iloc[i]) if pd.notna(atr_series.iloc[i]) else 0.0
        ma50    = float(ma50_series.iloc[i]) if pd.notna(ma50_series.iloc[i]) else None
        sma20   = float(sma20_series.iloc[i]) if pd.notna(sma20_series.iloc[i]) else price

        if rsi_val > RSI_MAX:
            continue
        if atr14 > 0 and price > 0 and atr14 / price > ATR_PCT_MAX:
            continue
        if ma50 and price < ma50 * (1 - MA50_ENTRY_DISCOUNT):
            continue
        if ma50 and price > ma50 * 1.05:
            continue

        # Momentum confirmation (F12 mirror): momentum mode only (RSI > 40)
        ret5d = float(return_5d_series.iloc[i]) if pd.notna(return_5d_series.iloc[i]) else 0.0
        if rsi_val > 40.0 and ret5d < 0.0:
            continue

        high_20d = float(high20_series.iloc[i]) if pd.notna(high20_series.iloc[i]) else price
        high_50d = float(high50_series.iloc[i]) if pd.notna(high50_series.iloc[i]) else price
        high_52w = float(high52w_series.iloc[i]) if pd.notna(high52w_series.iloc[i]) else price
        low_20d  = float(low20_series.iloc[i]) if pd.notna(low20_series.iloc[i]) else price * 0.95
        low_50d  = float(low50_series.iloc[i]) if pd.notna(low50_series.iloc[i]) else price * 0.95

        env = _compute_envelope(price, sma20, ma50, atr14, high_20d, high_50d, high_52w, low_20d, low_50d)
        if env is None:
            continue

        result = _evaluate_trade(df, i, env["entry"], env["target"], env["stop"])
        entry_date = str(df.index[i].date())
        exit_price = result["exit_price"]
        pnl = ((exit_price - env["entry"]) / env["entry"]) if exit_price and env["entry"] > 0 else None

        trades.append(TradeResult(
            ticker=ticker,
            entry_date=entry_date,
            entry_price=env["entry"],
            target_price=env["target"],
            stop_loss=env["stop"],
            exit_date=result["exit_date"],
            exit_price=exit_price,
            outcome=result["outcome"],
            pnl_pct=round(pnl, 4) if pnl is not None else None,
            holding_days=result["days"],
        ))

        if result["days"]:
            for j in range(i + 1, min(i + 1 + result["days"], len(df))):
                occupied.add(j)

    return trades


def summarize(trades: list[TradeResult]) -> dict:
    closed = [t for t in trades if t.outcome != "open"]
    wins   = [t for t in closed if t.outcome == "win"]
    flat   = [t for t in closed if t.outcome == "timeout_flat"]
    pnls   = [t.pnl_pct for t in closed if t.pnl_pct is not None]
    days   = [t.holding_days for t in closed if t.holding_days is not None]

    n = len(closed)
    return {
        "total_signals": len(trades),
        "closed": n,
        "wins": len(wins),
        "losses": n - len(wins) - len(flat),
        "timeout_flat": len(flat),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 4) if pnls else None,
        "avg_holding_days": round(sum(days) / len(days), 1) if days else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="OHLCV historical backtest replay")
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers, e.g. BBCA,BMRI")
    parser.add_argument("--years", type=int, default=1, help="Years of history (default: 1)")
    parser.add_argument("--output", default="output/historical_backtest/summary.json")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    all_trades: list[dict] = []
    per_ticker: dict[str, dict] = {}

    for ticker in tickers:
        print(f"Replaying {ticker}...")
        trades = replay_ticker(ticker, args.years)
        summary = summarize(trades)
        per_ticker[ticker] = summary
        all_trades.extend([vars(t) for t in trades])
        print(f"  {summary['closed']} closed trades, win_rate={summary['win_rate']}, avg_pnl={summary['avg_pnl_pct']}")

    aggregate = summarize([TradeResult(**t) for t in all_trades])

    report = {
        "args": {"tickers": tickers, "years": args.years},
        "aggregate": aggregate,
        "per_ticker": per_ticker,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nAggregate: {aggregate}")
    print(f"Report written to: {out}")


if __name__ == "__main__":
    main()
