"""
Diagnostic: Mean Reversion funnel breakdown + threshold sensitivity grid.

Usage:
    uv run python scratch/diag_mr_funnel.py

Shows:
  1. Berapa ticker lolos tiap gate MR secara individual
  2. Grid (MA200 floor) x (RSI max) -> berapa kandidat gabungan
"""

from __future__ import annotations

import logging
import warnings

import pandas as pd

from core.quant_filter.config import CONFIG, _find_latest_xlsx
from core.quant_filter.pipeline import download_yf_with_retry
from utils.technicals import compute_atr, compute_rsi

logging.basicConfig(level=logging.WARNING, format="%(message)s")

# ── Threshold grids ───────────────────────────────────────────────────────────
MA200_FLOORS = [0.90, 0.85, 0.80, 0.75, 0.70]
RSI_MAXES    = [35, 40, 45, 50]
PULLBACK_MAX = -0.30   # fixed — falling-knife protection

# ── 1. Load xlsx ──────────────────────────────────────────────────────────────
cfg = dict(CONFIG)
cfg["input_file"] = cfg.get("input_file") or _find_latest_xlsx(cfg["output_dir"])

print(f"\n📂 Input: {cfg['input_file']}")

df_ks     = pd.read_excel(cfg["input_file"], sheet_name="key-statistics")
df_prices = pd.read_excel(cfg["input_file"], sheet_name="stock-prices")
df_anal   = pd.read_excel(cfg["input_file"], sheet_name="analysis")
df_idx    = pd.read_excel(cfg["input_file"], sheet_name="idx-stocks")

df = (
    df_ks
    .merge(df_prices[["Ticker", "Close Price", "Volume", "High Price", "Low Price"]], on="Ticker", how="left")
    .merge(df_anal[["Ticker", "Price to Equity Discount (%)", "Composite Rank"]], on="Ticker", how="left")
    .merge(df_idx[["Ticker", "Name", "Note"]], on="Ticker", how="left")
)
for col in [
    "Close Price", "Debt to Equity Ratio (Quarter)", "Current Price to Book Value",
    "Return on Equity (TTM)", "Current EPS (TTM)", "Piotroski F-Score",
    "Altman Z-Score (Modified)", "Current Book Value Per Share",
]:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

print(f"📊 Universe: {len(df)} ticker")

df = df[~df["Note"].str.contains("PEMANTAUAN KHUSUS", na=False)].copy()
print(f"   Setelah exclude PEMANTAUAN KHUSUS: {len(df)}")

# ── 2. Static filter (harga, DER permissive, PBV) ────────────────────────────
filtered = df[
    (df["Close Price"] > cfg["min_close_price"])
    & (df["Debt to Equity Ratio (Quarter)"] <= 8.0)
    & (df["Current Price to Book Value"] < cfg["max_pbv_hard"])
].copy()
print(f"   Lolos static filter: {len(filtered)} ticker")

# ── 3. Download yfinance ──────────────────────────────────────────────────────
tickers_yf = [t + ".JK" for t in filtered["Ticker"].tolist()]
print(f"\n⏳ Download yfinance {len(tickers_yf)} ticker ({cfg['yf_period']})...")

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    data = download_yf_with_retry(
        tickers_yf,
        period=cfg["yf_period"],
        retries=cfg["yf_retries"],
        delay=cfg["yf_retry_delay"],
        logger=logging.getLogger("yf"),
    )

# ── 4. IHSG return 1m ────────────────────────────────────────────────────────
import yfinance as yf  # noqa: E402

ihsg_return_1m = 0.0
try:
    ihsg_raw = yf.download("^JKSE", period=cfg["yf_period"], progress=False, auto_adjust=True)
    if not ihsg_raw.empty:
        ihsg_close = ihsg_raw["Close"].squeeze().dropna()
        p = int(cfg.get("price_mom_period_days", 22))
        if len(ihsg_close) > p:
            ihsg_return_1m = float(ihsg_close.iloc[-1] / ihsg_close.iloc[-p - 1] - 1)
except Exception as exc:
    print(f"   IHSG gagal: {exc}")
print(f"   IHSG return 1m: {ihsg_return_1m:+.2%}")

# ── 5. Compute per-ticker MR conditions ──────────────────────────────────────
p = int(cfg.get("price_mom_period_days", 22))
records: list[dict] = []

for _, row in filtered.iterrows():
    t = str(row["Ticker"])
    t_yf = t + ".JK"
    try:
        available = set(data.columns.get_level_values(0))
        if t_yf not in available:
            continue
        df_t = data[t_yf].dropna(how="all")
        if len(df_t) < 60:
            continue
        if not {"Close", "Volume", "High", "Low"}.issubset(df_t.columns):
            continue

        close = df_t["Close"].squeeze()
        high  = df_t["High"].squeeze()
        low   = df_t["Low"].squeeze()
        vol   = df_t["Volume"].squeeze()

        px    = float(close.iloc[-1])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ma200_s = close.rolling(window=200, min_periods=50).mean()
        ma200 = float(ma200_s.iloc[-1]) if len(close) >= 50 and not pd.isna(ma200_s.iloc[-1]) else None
        rsi   = float(compute_rsi(close).iloc[-1])
        atr   = float(compute_atr(high, low, close).iloc[-1])
        adt   = float((close * vol).tail(20).mean())
        ret_1m = float(px / float(close.iloc[-p - 1]) - 1) if len(close) > p else None

        records.append({
            "ticker":      t,
            "px":          px,
            "rsi":         rsi,
            "ma200_pct":   (px / ma200) if ma200 and ma200 > 0 else None,
            "ret_1m":      ret_1m,
            "rs":          (ret_1m - ihsg_return_1m) if ret_1m is not None else None,
            "atr_pct":     atr / px if px > 0 else 0.0,
            "adt":         adt,
            "below_ema20": px < ema20,
        })
    except Exception:
        continue

df_d = pd.DataFrame(records)
print(f"\n✅ Data teknikal: {len(df_d)} ticker\n")

# ── 6. Funnel individual ──────────────────────────────────────────────────────
n = len(df_d)
print("═" * 58)
print("  FUNNEL — berapa ticker lolos tiap gate secara terpisah")
print("═" * 58)

def pct_bar(k, total):
    p = k / total * 100 if total else 0
    return f"{k:>4}  ({p:4.0f}%)  {'█' * int(p / 4)}"

print(f"  {'Total data teknikal':<40} {pct_bar(n, n)}")
print(f"  {'price < EMA20':<40} {pct_bar(df_d['below_ema20'].sum(), n)}")
print(f"  {'1m drop > -30% (anti falling-knife)':<40} {pct_bar((df_d['ret_1m'] >= PULLBACK_MAX).sum(), n)}")
print(f"  {'ADT >= Rp10B':<40} {pct_bar((df_d['adt'] >= cfg['min_adt_20d']).sum(), n)}")
print(f"  {'ATR% <= 5%':<40} {pct_bar((df_d['atr_pct'] <= cfg['max_atr_pct']).sum(), n)}")
print(f"  {'RS vs IHSG >= 0%':<40} {pct_bar((df_d['rs'].fillna(-999) >= 0).sum(), n)}")
print(f"  {'MA200 floor >= 90% (config saat ini)':<40} {pct_bar((df_d['ma200_pct'].fillna(0) >= 0.90).sum(), n)}")
print(f"  {'RSI <= 40 (config saat ini)':<40} {pct_bar((df_d['rsi'] <= 40).sum(), n)}")

base_all = (
    df_d["below_ema20"]
    & (df_d["ret_1m"].fillna(-999) >= PULLBACK_MAX)
    & (df_d["adt"] >= cfg["min_adt_20d"])
    & (df_d["atr_pct"] <= cfg["max_atr_pct"])
)
current = base_all & (df_d["ma200_pct"].fillna(0) >= 0.90) & (df_d["rsi"] <= 40)
print(f"\n  {'KOMBINASI CONFIG SAAT INI':<40} {current.sum():>4}  ← hasil 0")

# ── 7. Sensitivity grid (base: EMA20 + anti-knife + liquidity) ───────────────
print("\n" + "═" * 58)
print("  GRID: (MA200 floor) x (RSI max)")
print("  Base: price<EMA20  +  ret>-30%  +  ADT/ATR OK")
print("  [RS vs IHSG gate DILEPAS untuk diagnostic]")
print("═" * 58)

col_w = 9
header = f"  {'MA200 floor':>12}  " + "".join(f"{'RSI≤'+str(r):>{col_w}}" for r in RSI_MAXES)
print(header)
print("  " + "─" * (len(header) - 2))

for floor in MA200_FLOORS:
    ma200_ok = df_d["ma200_pct"].fillna(0) >= floor
    cells = []
    for rsi_max in RSI_MAXES:
        k = (base_all & ma200_ok & (df_d["rsi"] <= rsi_max)).sum()
        tag = "◄" if floor == 0.90 and rsi_max == 40 else ""
        cells.append(f"{k:>{col_w - len(tag)}}{tag}")
    label = f">= {floor:.0%}"
    print(f"  {label:>12}  {''.join(cells)}")

# ── 8. RS gate impact at recommended thresholds ──────────────────────────────
print("\n" + "═" * 58)
print("  DAMPAK RS GATE (MA200>=80%, RSI<=45)")
print("═" * 58)
base_rec = base_all & (df_d["ma200_pct"].fillna(0) >= 0.80) & (df_d["rsi"] <= 45)
rs_mask  = df_d["rs"].fillna(-999) >= 0
print(f"  Dengan RS gate (>= 0% vs IHSG): {(base_rec & rs_mask).sum()}")
print(f"  Tanpa RS gate:                  {base_rec.sum()}")
print(f"  Dibuang RS gate:                {base_rec.sum() - (base_rec & rs_mask).sum()}")

# ── 9. Kandidat di threshold rekomendasi ─────────────────────────────────────
print("\n" + "═" * 58)
print("  KANDIDAT: MA200>=80%, RSI<=45, TANPA RS gate")
print("═" * 58)
cands = df_d[base_rec].sort_values("rsi").copy()
if cands.empty:
    print("  (tidak ada — coba turunkan MA200 floor ke 0.75)")
else:
    print(f"  {'Ticker':<8}  {'Harga':>8}  {'RSI':>6}  {'MA200%':>7}  {'1m':>7}  {'RS':>7}")
    print("  " + "─" * 50)
    for _, r in cands.iterrows():
        ma_s  = f"{r['ma200_pct']:.0%}"  if r["ma200_pct"] else "N/A"
        ret_s = f"{r['ret_1m']:+.1%}"   if r["ret_1m"] is not None else "N/A"
        rs_s  = f"{r['rs']:+.1%}"       if r["rs"] is not None else "N/A"
        print(f"  {r['ticker']:<8}  {r['px']:>8,.0f}  {r['rsi']:>6.1f}  {ma_s:>7}  {ret_s:>7}  {rs_s:>7}")

print("\n✅ Selesai.\n")
