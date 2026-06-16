"""
Diagnostic: Market regime deep-dive.

Usage:
    uv run python scratch/diag_regime.py

Shows:
  1. IHSG snapshot saat ini (close, MA, vol, weekly return, regime breakdown)
  2. Time series 30 hari terakhir: regime retroaktif per hari
  3. Sensitivity grid: recovery threshold x vol threshold
  4. Perbandingan params pipeline per regime (termasuk RECOVERY)
"""

from __future__ import annotations

import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import yfinance as yf

from core.regime import classify_regime, get_regime_params
from core.settings import settings

# ── Config ────────────────────────────────────────────────────────────────────
VOL_LOOKBACK    = settings.REGIME_VOLATILITY_LOOKBACK_DAYS         # 20
HIGH_THRESHOLD  = settings.REGIME_VOLATILITY_HIGH_THRESHOLD        # 0.02
LOW_THRESHOLD   = settings.REGIME_VOLATILITY_LOW_THRESHOLD         # 0.01
DEF_THRESHOLD   = settings.REGIME_DEFENSIVE_WEEKLY_DROP_THRESHOLD  # 0.05
RECOVERY_THRESHOLD = getattr(settings, "REGIME_HIGH_RECOVERY_WEEKLY_THRESHOLD", 0.10)
HISTORY_ROWS    = 30
RECOVERY_CANDIDATES = [0.05, 0.08, 0.10, 0.12, 0.15]
VOL_THRESHOLDS  = [0.015, 0.020, 0.025]

# ── Download IHSG ─────────────────────────────────────────────────────────────
print("\n⏳ Download ^JKSE (1y)...")
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    raw = yf.download("^JKSE", period="1y", progress=False, auto_adjust=True)

if raw.empty:
    print("❌ Gagal download ^JKSE")
    raise SystemExit(1)

close = raw["Close"].squeeze().dropna()
print(f"✅ {len(close)} hari data  ({close.index[0].date()} → {close.index[-1].date()})\n")

# ── Rolling indicators ────────────────────────────────────────────────────────
ret      = close.pct_change()
vol_s    = ret.rolling(VOL_LOOKBACK).std()
wret_s   = close.pct_change(5)
ma20_s   = close.rolling(20).mean()
ma50_s   = close.rolling(50).mean()
ma200_s  = close.rolling(200).mean()


# ── Helper: classify one day ──────────────────────────────────────────────────
def _classify_day(c_px, c_vol, c_wret, c_ma20, c_ma50, c_ma200, rec_thr=RECOVERY_THRESHOLD):
    """Return (vol_regime, defensive, recovery, final_regime)."""
    nan = pd.isna
    vr   = classify_regime(c_vol, HIGH_THRESHOLD, LOW_THRESHOLD) if not nan(c_vol) else "N/A"
    bma  = (
        not any(nan(x) for x in [c_ma20, c_ma50, c_ma200])
        and c_px < c_ma20 and c_px < c_ma50 and c_px < c_ma200
    )
    ddef = (not nan(c_wret) and c_wret <= -DEF_THRESHOLD) or bma
    rec  = vr == "HIGH" and not ddef and not nan(c_wret) and c_wret >= rec_thr
    if ddef:
        final = "DEFENSIVE"
    elif rec:
        final = "RECOVERY"
    else:
        final = vr
    return vr, ddef, rec, final


# ── 1. Snapshot saat ini ──────────────────────────────────────────────────────
px    = float(close.iloc[-1])
vol   = float(vol_s.iloc[-1])
wret  = float(wret_s.iloc[-1])
ma20  = float(ma20_s.iloc[-1])
ma50  = float(ma50_s.iloc[-1])
ma200 = float(ma200_s.iloc[-1])

vr, ddef, rec, final = _classify_day(px, vol, wret, ma20, ma50, ma200)

print("═" * 64)
print("  IHSG SNAPSHOT — SAAT INI")
print("═" * 64)
print(f"  Close              : Rp {px:>10,.0f}")
print(f"  MA20               : Rp {ma20:>10,.0f}  {'▲ above' if px >= ma20 else '▼ BELOW'}")
print(f"  MA50               : Rp {ma50:>10,.0f}  {'▲ above' if px >= ma50 else '▼ BELOW'}")
print(f"  MA200              : Rp {ma200:>10,.0f}  {'▲ above' if px >= ma200 else '▼ BELOW'}")
print(f"  20d realized vol   : {vol:.4f}  ({vol * 100:.2f}% daily std)  → {vr}")
print(f"  5d return (proxy)  : {wret:+.2%}")
print()
print(f"  DEFENSIVE triggered: {'YES ⚠️' if ddef else 'no'}")
if ddef:
    if wret <= -DEF_THRESHOLD:
        print(f"    → 5d drop {wret:+.2%} ≤ -{DEF_THRESHOLD:.0%} threshold")
    if not any(pd.isna(x) for x in [ma20, ma50, ma200]) and px < ma20 and px < ma50 and px < ma200:
        print(f"    → close di bawah MA20 + MA50 + MA200")
print(f"  RECOVERY triggered : {'YES 🟢' if rec else 'no'}  (threshold ≥{RECOVERY_THRESHOLD:.0%})")
print(f"  Final regime       : ► {final} ◄")

params = get_regime_params(final)
print()
print("  Pipeline params:")
print(f"    top_n_selection  = {params.get('top_n_selection', '3 (default)')}")
print(f"    min_conviction   = {params.get('min_conviction_override', 'default')}")
print(f"    rpm_limit        = {params.get('rpm_limit', 'default')}")
print(f"    rr_cap           = {params.get('rr_normalization_cap', 'default')}")

# ── 2. Time series 30 hari ───────────────────────────────────────────────────
print()
print("═" * 80)
print("  TIME SERIES — 30 HARI TERAKHIR  (regime retroaktif per hari)")
print(f"  Recovery threshold = {RECOVERY_THRESHOLD:.0%}  |  Defensive threshold = {DEF_THRESHOLD:.0%}")
print("═" * 80)
print(f"  {'Tanggal':<12}  {'Close':>8}  {'20d_vol':>7}  {'5d_ret':>7}  "
      f"{'Vol':>5}  {'Def':>3}  {'Sebelum Opsi A':<14}  {'Setelah Opsi A':<14}")
print("  " + "─" * 74)

for dt in close.index[-HISTORY_ROWS:]:
    i       = close.index.get_loc(dt)
    c_px    = float(close.iloc[i])
    c_vol   = float(vol_s.iloc[i])   if not pd.isna(vol_s.iloc[i])  else float("nan")
    c_wret  = float(wret_s.iloc[i])  if not pd.isna(wret_s.iloc[i]) else float("nan")
    c_ma20  = float(ma20_s.iloc[i])  if not pd.isna(ma20_s.iloc[i]) else float("nan")
    c_ma50  = float(ma50_s.iloc[i])  if not pd.isna(ma50_s.iloc[i]) else float("nan")
    c_ma200 = float(ma200_s.iloc[i]) if not pd.isna(ma200_s.iloc[i]) else float("nan")

    c_vr, c_def, c_rec, c_final = _classify_day(c_px, c_vol, c_wret, c_ma20, c_ma50, c_ma200)
    before_a = "DEFENSIVE" if c_def else c_vr

    vol_str  = f"{c_vol*100:.2f}%" if not pd.isna(c_vol)  else "  N/A"
    wret_str = f"{c_wret:+.2%}"   if not pd.isna(c_wret) else "  N/A"
    def_str  = "YES" if c_def else " - "
    changed  = " ✓" if c_rec else ""
    tag      = "  ◄ hari ini" if dt == close.index[-1] else ""

    print(
        f"  {str(dt.date()):<12}  {c_px:>8,.0f}  {vol_str:>7}  {wret_str:>7}  "
        f"{c_vr:>5}  {def_str:>3}  {before_a:<14}  {c_final:<14}{changed}{tag}"
    )

# ── 3. Sensitivity grid: berapa hari → RECOVERY ──────────────────────────────
print()
print("═" * 64)
print("  GRID: Berapa hari (dari 30) yang jadi RECOVERY")
print(f"  Base: vol regime HIGH  +  NOT defensive")
print("═" * 64)

tail_idx = close.index[-HISTORY_ROWS:]
cw = 11

hdr3 = f"  {'wret threshold':>16}  " + "".join(f"{'vol>'+f\"{v*100:.1f}\".rstrip('0').rstrip('.')+'%':{cw}}" for v in VOL_THRESHOLDS)
print(hdr3)
print("  " + "─" * (len(hdr3) - 2))

for rthr in RECOVERY_CANDIDATES:
    cells = []
    for vthr in VOL_THRESHOLDS:
        k = 0
        for dt in tail_idx:
            i       = close.index.get_loc(dt)
            c_vol   = float(vol_s.iloc[i])   if not pd.isna(vol_s.iloc[i])  else float("nan")
            c_wret  = float(wret_s.iloc[i])  if not pd.isna(wret_s.iloc[i]) else float("nan")
            c_ma20  = float(ma20_s.iloc[i])  if not pd.isna(ma20_s.iloc[i]) else float("nan")
            c_ma50  = float(ma50_s.iloc[i])  if not pd.isna(ma50_s.iloc[i]) else float("nan")
            c_ma200 = float(ma200_s.iloc[i]) if not pd.isna(ma200_s.iloc[i]) else float("nan")
            c_px    = float(close.iloc[i])
            bma = (
                not any(pd.isna(x) for x in [c_ma20, c_ma50, c_ma200])
                and c_px < c_ma20 and c_px < c_ma50 and c_px < c_ma200
            )
            c_def = (not pd.isna(c_wret) and c_wret <= -DEF_THRESHOLD) or bma
            if (
                not pd.isna(c_vol) and c_vol >= vthr
                and not c_def
                and not pd.isna(c_wret) and c_wret >= rthr
            ):
                k += 1
        tag = "◄" if abs(rthr - RECOVERY_THRESHOLD) < 1e-9 and abs(vthr - HIGH_THRESHOLD) < 1e-9 else ""
        cells.append(f"{k:>{cw - len(tag)}}{tag}")
    cur_tag = "  ← config saat ini" if abs(rthr - RECOVERY_THRESHOLD) < 1e-9 else ""
    print(f"  {'wret >= '+f'{rthr:.0%}':>16}  {''.join(cells)}{cur_tag}")

# ── 4. Params per regime ─────────────────────────────────────────────────────
print()
print("═" * 62)
print("  PIPELINE PARAMS — PERBANDINGAN PER REGIME")
print("═" * 62)
print(f"  {'Regime':<12}  {'top_n':>6}  {'min_conv':>9}  {'rpm':>5}  {'rr_cap':>7}")
print("  " + "─" * 44)
for r in ("DEFENSIVE", "HIGH", "NORMAL", "LOW", "RECOVERY"):
    p       = get_regime_params(r)
    top_n   = p.get("top_n_selection", "3*")
    mconv   = p.get("min_conviction_override", "—")
    rpm     = p.get("rpm_limit", "—")
    rrc     = p.get("rr_normalization_cap", "—")
    cur_tag = "  ◄ CURRENT" if r == final else ""
    print(f"  {r:<12}  {str(top_n):>6}  {str(mconv):>9}  {str(rpm):>5}  {str(rrc):>7}{cur_tag}")

print()
print("  * NORMAL = tidak ada override; pipeline memakai default env/config")
print(f"  * RECOVERY = vol HIGH + 5d return ≥ {RECOVERY_THRESHOLD:.0%} + NOT defensive")

print("\n✅ Selesai.\n")
