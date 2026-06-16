"""
Diagnostic: Momentum filter funnel breakdown + threshold sensitivity grid.

Usage:
    uv run python scratch/diag_momentum_funnel.py

Shows:
  1. Berapa ticker lolos tiap gate momentum secara individual
  2. Grid (EMA20 tolerance) x (volume surge min)
  3. Dampak RS gate dan RSI hard reject
  4. Kandidat di threshold rekomendasi
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
EMA20_FLOORS   = [1.00, 0.99, 0.98, 0.97, 0.95]
VOL_SURGE_MINS = [0.30, 0.20, 0.10]
RSI_REJECTS    = [70, 75, 80]
RS_MINS        = [0.00, -0.03, -0.05]

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

filtered = df[
    (df["Close Price"] > cfg["min_close_price"])
    & (df["Debt to Equity Ratio (Quarter)"] <= 8.0)
    & (df["Current Price to Book Value"] < cfg["max_pbv_hard"])
].copy()
print(f"   Lolos static filter: {len(filtered)} ticker")

# ── 2. Download yfinance + IHSG ───────────────────────────────────────────────
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

# ── 3. Compute per-ticker momentum conditions ─────────────────────────────────
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

        px        = float(close.iloc[-1])
        ema20     = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        rsi       = float(compute_rsi(close).iloc[-1])
        atr       = float(compute_atr(high, low, close).iloc[-1])
        adt       = float((close * vol).tail(20).mean())
        vol_20d   = float(vol.tail(20).mean())
        vol_surge = float(vol.iloc[-1]) / vol_20d if vol_20d > 0 else 0.0
        ret_1m    = float(px / float(close.iloc[-p - 1]) - 1) if len(close) > p else None
        rs        = (ret_1m - ihsg_return_1m) if ret_1m is not None else None
        atr_pct   = atr / px if px > 0 else 0.0

        records.append({
            "ticker":      t,
            "px":          px,
            "px_vs_ema20": px / ema20 if ema20 > 0 else 0.0,
            "rsi":         rsi,
            "atr_pct":     atr_pct,
            "adt":         adt,
            "vol_surge":   vol_surge,
            "ret_1m":      ret_1m,
            "rs":          rs,
        })
    except Exception:
        continue

df_d = pd.DataFrame(records)
n = len(df_d)
print(f"\n✅ Data teknikal: {n} ticker\n")

# ── 4. Funnel individual ──────────────────────────────────────────────────────
def bar(k, total):
    pct = k / total * 100 if total else 0
    return f"{k:>4}  ({pct:4.0f}%)  {'█' * int(pct / 4)}"

print("═" * 62)
print("  FUNNEL — berapa ticker lolos tiap gate secara terpisah")
print("═" * 62)
print(f"  {'Total data teknikal':<44} {bar(n, n)}")
print(f"  {'price >= EMA20 (1.00x, config saat ini)':<44} {bar((df_d['px_vs_ema20'] >= 1.00).sum(), n)}")
print(f"  {'price >= EMA20 x 0.97 (toleransi 3%)':<44} {bar((df_d['px_vs_ema20'] >= 0.97).sum(), n)}")
print(f"  {'RSI <= 70 (hard reject saat ini)':<44} {bar((df_d['rsi'] <= 70).sum(), n)}")
print(f"  {'RSI <= 75':<44} {bar((df_d['rsi'] <= 75).sum(), n)}")
print(f"  {'ADT >= Rp10B':<44} {bar((df_d['adt'] >= cfg['min_adt_20d']).sum(), n)}")
print(f"  {'ATR% <= 5%':<44} {bar((df_d['atr_pct'] <= cfg['max_atr_pct']).sum(), n)}")
print(f"  {'Volume surge >= 0.30x (config saat ini)':<44} {bar((df_d['vol_surge'] >= 0.30).sum(), n)}")
print(f"  {'Volume surge >= 0.20x':<44} {bar((df_d['vol_surge'] >= 0.20).sum(), n)}")
print(f"  {'RS vs IHSG >= 0%':<44} {bar((df_d['rs'].fillna(-999) >= 0).sum(), n)}")
print(f"  {'RS vs IHSG >= -3%':<44} {bar((df_d['rs'].fillna(-999) >= -0.03).sum(), n)}")

liquid = (df_d["adt"] >= cfg["min_adt_20d"]) & (df_d["atr_pct"] <= cfg["max_atr_pct"])
cur = (
    liquid
    & (df_d["px_vs_ema20"] >= 1.00)
    & (df_d["rsi"] <= 70)
    & (df_d["vol_surge"] >= 0.30)
    & (df_d["rs"].fillna(-999) >= 0.00)
)
print(f"\n  {'KOMBINASI CONFIG SAAT INI':<44} {cur.sum():>4}  ← hasilnya ini")

# ── 5. Grid: EMA20 x Volume surge ────────────────────────────────────────────
print("\n" + "═" * 62)
print("  GRID 1: (EMA20 floor) x (Volume surge min)")
print("  Base: ADT>=10B + ATR<=5% + RSI<=70 + RS>=0%")
print("═" * 62)

base_rs = liquid & (df_d["rsi"] <= 70) & (df_d["rs"].fillna(-999) >= 0.00)

cw = 10
header = f"  {'EMA20 floor':>12}  " + "".join(f"{'Vol>='+str(int(v*100))+'%':>{cw}}" for v in VOL_SURGE_MINS)
print(header)
print("  " + "─" * (len(header) - 2))
for ef in EMA20_FLOORS:
    ema_ok = df_d["px_vs_ema20"] >= ef
    cells = []
    for vm in VOL_SURGE_MINS:
        k = (base_rs & ema_ok & (df_d["vol_surge"] >= vm)).sum()
        tag = "◄" if ef == 1.00 and vm == 0.30 else ""
        cells.append(f"{k:>{cw - len(tag)}}{tag}")
    print(f"  {f'>= {ef:.2f}':>12}  {''.join(cells)}")

# ── 6. Grid: EMA20 x RS gate ─────────────────────────────────────────────────
print("\n" + "═" * 62)
print("  GRID 2: (EMA20 floor) x (RS vs IHSG min)")
print("  Base: ADT>=10B + ATR<=5% + RSI<=70 + Vol>=0.20")
print("═" * 62)

base_vol = liquid & (df_d["rsi"] <= 70) & (df_d["vol_surge"] >= 0.20)

header2 = f"  {'EMA20 floor':>12}  " + "".join(f"{'RS>='+str(int(r*100))+'%':>{cw}}" for r in RS_MINS)
print(header2)
print("  " + "─" * (len(header2) - 2))
for ef in EMA20_FLOORS:
    ema_ok = df_d["px_vs_ema20"] >= ef
    cells = []
    for rm in RS_MINS:
        k = (base_vol & ema_ok & (df_d["rs"].fillna(-999) >= rm)).sum()
        tag = "◄" if ef == 1.00 and rm == 0.00 else ""
        cells.append(f"{k:>{cw - len(tag)}}{tag}")
    print(f"  {f'>= {ef:.2f}':>12}  {''.join(cells)}")

# ── 7. RSI hard reject impact ────────────────────────────────────────────────
print("\n" + "═" * 62)
print("  RSI HARD REJECT — dampak threshold")
print("  Base: ADT>=10B + ATR<=5% + EMA20>=0.97 + Vol>=0.20 + RS>=0%")
print("═" * 62)
base_rsi_test = liquid & (df_d["px_vs_ema20"] >= 0.97) & (df_d["vol_surge"] >= 0.20) & (df_d["rs"].fillna(-999) >= 0.00)
for rm in RSI_REJECTS:
    k = (base_rsi_test & (df_d["rsi"] <= rm)).sum()
    tag = " ◄ current" if rm == 70 else ""
    print(f"  RSI <= {rm}: {k}{tag}")

# ── 8. Kandidat di threshold rekomendasi ─────────────────────────────────────
print("\n" + "═" * 62)
print("  KANDIDAT: EMA20>=0.97, Vol>=0.20, RSI<=70, RS>=0%")
print("═" * 62)
mask_rec = (
    liquid
    & (df_d["px_vs_ema20"] >= 0.97)
    & (df_d["vol_surge"] >= 0.20)
    & (df_d["rsi"] <= 70)
    & (df_d["rs"].fillna(-999) >= 0.00)
)
cands = df_d[mask_rec].sort_values("rs", ascending=False).copy()
if cands.empty:
    print("  (tidak ada)")
else:
    print(f"  {'Ticker':<8}  {'Harga':>8}  {'RSI':>6}  {'EMA20%':>7}  {'Vol':>6}  {'1m':>7}  {'RS':>7}")
    print("  " + "─" * 58)
    for _, r in cands.iterrows():
        ema_s = f"{r['px_vs_ema20']:.1%}"
        vol_s = f"{r['vol_surge']:.2f}x"
        ret_s = f"{r['ret_1m']:+.1%}" if r["ret_1m"] is not None else "N/A"
        rs_s  = f"{r['rs']:+.1%}"     if r["rs"] is not None else "N/A"
        print(
            f"  {r['ticker']:<8}  {r['px']:>8,.0f}  {r['rsi']:>6.1f}  "
            f"{ema_s:>7}  {vol_s:>6}  {ret_s:>7}  {rs_s:>7}"
        )

print(f"\n  Total: {mask_rec.sum()} kandidat\n")
print("✅ Selesai.\n")
