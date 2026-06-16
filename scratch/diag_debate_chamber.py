"""
Diagnostic: Debate chamber output analysis.

Usage:
    uv run python scratch/diag_debate_chamber.py
    uv run python scratch/diag_debate_chamber.py --days 30

Shows:
  1.  Ringkasan (total debates, ticker unik, rentang tanggal)
  2.  Rating distribution
  3.  Funnel "kenapa tidak BUY" (above_entry / R/R / conf / overvalued / DA)
  4.  Per-agent voting matrix (siapa paling sering HOLD?)
  5.  R/R distribution (berapa % di bawah threshold?)
  6.  Confidence distribution per rating
  7.  Outcome per regime
  8.  Debate rounds distribution
  9.  10 HOLD terdekat ke BUY (R/R tertinggi, kandidat revisit)
  10. Bull/Bear symmetry check (apakah debate genuine atau theater?)
  11. CIO override rate (seberapa sering CIO flip consensus?)
  12. Token budget per debate (flash + pro calls, top 10 paling mahal)
  13. Error & silent failure rate
  14. Scout data quality (fundamental tipis, news gagal fetch)
  15. Confidence trend per bulan
  16. R/R outlier investigation (> 10x atau stop-loss hilang)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--days", type=int, default=0, help="0=semua; N=N hari terakhir")
parser.add_argument("--debates-dir", default="output/debates")
args = parser.parse_args()

DEBATES_DIR = Path(args.debates_dir)
CUTOFF: date | None = (
    (datetime.now() - timedelta(days=args.days)).date() if args.days > 0 else None
)

AGENTS = [
    "fundamental_scout", "chartist", "sentiment_specialist",
    "bull", "bear", "devils_advocate",
]
BUY_RATINGS  = {"BUY", "STRONG_BUY"}
HOLD_RATINGS = {"HOLD"}
AVOID_RATINGS= {"AVOID", "SELL", "INSUFFICIENT_DATA"}


def _parse_entry_high(entry_range: str | None) -> float | None:
    if not entry_range or entry_range.strip() in {"N/A – N/A", "N/A - N/A", "-", ""}:
        return None
    parts = re.split(r"\s*[-–—]\s*", entry_range.strip(), maxsplit=1)
    if len(parts) < 2:
        return None
    raw = parts[1].strip().replace(",", "")
    if "." in raw:
        dot_parts = raw.split(".")
        if all(len(p) == 3 for p in dot_parts[1:]):
            raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


# ── Load ──────────────────────────────────────────────────────────────────────
records: list[dict] = []

for json_path in sorted(DEBATES_DIR.rglob("*_debate.json")):
    if json_path.name == "latest_debate.json":
        continue
    folder = json_path.parent.name
    m = re.match(r"v(\d{8})_(\d{6})", folder)
    if not m:
        continue
    run_date = datetime.strptime(m.group(1), "%Y%m%d").date()
    if CUTOFF and run_date < CUTOFF:
        continue
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        continue

    meta       = data.get("metadata") or {}
    verdict    = data.get("verdict") or {}
    rating     = verdict.get("rating") or "UNKNOWN"
    conf       = float(verdict.get("confidence") or 0.0)
    rr         = verdict.get("risk_reward_ratio")
    rr_min     = float(verdict.get("rr_minimum") or 1.5)
    fv         = verdict.get("fair_value")
    cur_px     = verdict.get("current_price")
    entry_r    = verdict.get("entry_price_range")
    target_px  = verdict.get("target_price")
    stop_px    = verdict.get("stop_loss")
    is_over    = bool(verdict.get("is_overvalued") or verdict.get("risk_overvalued"))
    wait_see   = bool(verdict.get("wait_and_see"))
    cons       = bool(data.get("consensus_reached", False))
    rounds_val = int(data.get("debate_rounds") or 1)
    regime     = meta.get("regime") or "UNKNOWN"
    ticker     = data.get("ticker") or json_path.parent.parent.name

    entry_high  = _parse_entry_high(entry_r)
    above_entry = (
        entry_high is not None
        and cur_px is not None
        and float(cur_px) > float(entry_high)
    )

    agent_votes: dict[str, str]   = {}
    agent_conf:  dict[str, float] = {}
    for vote in data.get("agent_votes") or []:
        ag = vote.get("agent", "")
        agent_votes[ag] = vote.get("position", "")
        agent_conf[ag]  = float(
            vote.get("effective_confidence") or vote.get("confidence") or 0.0
        )

    records.append({
        "ticker":      ticker,
        "run_date":    run_date,
        "month":       run_date.strftime("%Y-%m"),
        "rating":      rating,
        "conf":        conf,
        "rr":          float(rr) if rr is not None else None,
        "rr_min":      rr_min,
        "is_over":     is_over,
        "above_entry": above_entry,
        "wait_see":    wait_see,
        "consensus":   cons,
        "rounds":      rounds_val,
        "regime":      regime,
        "fv":          fv,
        "cur_px":      cur_px,
        "target_px":   target_px,
        "stop_px":     stop_px,
        "entry_range": entry_r,
        "agent_votes": agent_votes,
        "agent_conf":  agent_conf,
        "flash_calls":     int(meta.get("flash_calls") or 0),
        "pro_calls":       int(meta.get("pro_calls") or 0),
        "has_error":       bool(data.get("error")),
        "error_msg":       str(data.get("error") or "")[:120],
        "cw":              str(data.get("consensus_winner") or ""),
        "raw_summary_len": len(str(data.get("raw_data_summary") or "")),
        "news_fail":       bool(meta.get("news_fetch_failure")),
        "first_bull_len":  len(str(next(
            (m.get("content", "") for m in (data.get("debate_history") or [])
             if m.get("role") == "bull"), ""
        ))),
    })

if not records:
    print("Tidak ada debate JSON yang ditemukan.")
    raise SystemExit(0)

records.sort(key=lambda r: r["run_date"])
n_total   = len(records)
n_tickers = len({r["ticker"] for r in records})
date_min  = records[0]["run_date"]
date_max  = records[-1]["run_date"]


def pbar(k, total, width=22):
    pct = k / total * 100 if total else 0
    return f"{k:>4} ({pct:4.0f}%)  {'█' * int(pct / (100 / width))}"


def section(title):
    print()
    print("═" * 68)
    print(f"  {title}")
    print("═" * 68)


# ── 1. Ringkasan ──────────────────────────────────────────────────────────────
section("RINGKASAN")
print(f"  Total debates  : {n_total}")
print(f"  Ticker unik    : {n_tickers}")
print(f"  Rentang tanggal: {date_min} → {date_max}")
if CUTOFF:
    print(f"  Filter         : {args.days} hari terakhir (sejak {CUTOFF})")

# ── 2. Rating distribution ────────────────────────────────────────────────────
section("RATING DISTRIBUTION")
rating_cnt = Counter(r["rating"] for r in records)
for rat in ("STRONG_BUY", "BUY", "HOLD", "AVOID", "SELL", "INSUFFICIENT_DATA", "UNKNOWN"):
    k = rating_cnt.get(rat, 0)
    if k > 0:
        print(f"  {rat:<22} {pbar(k, n_total)}")

n_buy  = sum(rating_cnt.get(r, 0) for r in BUY_RATINGS)
n_hold = sum(rating_cnt.get(r, 0) for r in HOLD_RATINGS)
n_avd  = sum(rating_cnt.get(r, 0) for r in AVOID_RATINGS)
print()
print(f"  BUY+STRONG_BUY : {n_buy:>4} ({n_buy/n_total*100:.0f}%)")
print(f"  HOLD           : {n_hold:>4} ({n_hold/n_total*100:.0f}%)")
print(f"  AVOID+SELL     : {n_avd:>4} ({n_avd/n_total*100:.0f}%)")

# ── 3. Funnel "kenapa tidak BUY?" ─────────────────────────────────────────────
section('FUNNEL: "KENAPA TIDAK BUY?"  (HOLD + AVOID saja)')
non_buy = [r for r in records if r["rating"] not in BUY_RATINGS]
nb = len(non_buy)
if nb:
    reasons = {
        "above_entry (harga > zona entry)":
            sum(1 for r in non_buy if r["above_entry"]),
        "R/R < threshold (1.5x atau 1.3x)":
            sum(1 for r in non_buy if r["rr"] is not None and r["rr"] < r["rr_min"]),
        "confidence < 0.45":
            sum(1 for r in non_buy if r["conf"] < 0.45),
        "is_overvalued = True":
            sum(1 for r in non_buy if r["is_over"]),
        "wait_and_see = True":
            sum(1 for r in non_buy if r["wait_see"]),
        "DA voted AVOID/SELL":
            sum(1 for r in non_buy
                if r["agent_votes"].get("devils_advocate") in {"AVOID", "SELL"}),
        "consensus NOT reached":
            sum(1 for r in non_buy if not r["consensus"]),
    }
    print(f"  (dari {nb} debates non-BUY)\n")
    for reason, k in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason:<42} {pbar(k, nb)}")
    print()
    # berapa yang kena multiple reasons
    multi = sum(
        1 for r in non_buy
        if (
            int(r["above_entry"])
            + int(r["rr"] is not None and r["rr"] < r["rr_min"])
            + int(r["conf"] < 0.45)
            + int(r["is_over"])
        ) >= 2
    )
    print(f"  Kena >= 2 kondisi sekaligus: {multi}/{nb} ({multi/nb*100:.0f}%)")

# ── 4. Per-agent voting matrix ────────────────────────────────────────────────
section("AGENT VOTING MATRIX  (posisi + BUY rate)")
ag_pos   = defaultdict(Counter)
ag_csum  = defaultdict(float)
ag_ccnt  = defaultdict(int)

for r in records:
    for ag in AGENTS:
        pos  = r["agent_votes"].get(ag)
        conf = r["agent_conf"].get(ag)
        if pos:
            ag_pos[ag][pos] += 1
        if conf:
            ag_csum[ag] += conf
            ag_ccnt[ag] += 1

hdr_pos = ["BUY", "STRONG_BUY", "HOLD", "AVOID"]
cw = 10
print(f"  {'Agent':<22}" + "".join(f"{p:>{cw}}" for p in hdr_pos) + f"  {'AvgConf':>8}  {'BUY%':>6}")
print("  " + "─" * 62)
for ag in AGENTS:
    cnts = ag_pos[ag]
    tot  = sum(cnts.values())
    if tot == 0:
        continue
    cells    = "".join(f"{cnts.get(p, 0):>{cw}}" for p in hdr_pos)
    avg_conf = ag_csum[ag] / ag_ccnt[ag] if ag_ccnt[ag] else 0.0
    buy_rt   = (cnts.get("BUY", 0) + cnts.get("STRONG_BUY", 0)) / tot * 100
    print(f"  {ag:<22}{cells}  {avg_conf:>7.1%}  {buy_rt:>5.0f}%")

# ── 5. R/R distribution ───────────────────────────────────────────────────────
section("R/R DISTRIBUTION")
rr_vals = [r["rr"] for r in records if r["rr"] is not None]
if rr_vals:
    buckets = [
        ("< 1.0x  (hard reject)",  [v for v in rr_vals if v < 1.0]),
        ("1.0x – 1.3x",            [v for v in rr_vals if 1.0 <= v < 1.3]),
        ("1.3x – 1.5x",            [v for v in rr_vals if 1.3 <= v < 1.5]),
        ("1.5x – 2.0x",            [v for v in rr_vals if 1.5 <= v < 2.0]),
        ("2.0x – 3.0x",            [v for v in rr_vals if 2.0 <= v < 3.0]),
        (">= 3.0x",                [v for v in rr_vals if v >= 3.0]),
    ]
    nr = len(rr_vals)
    for label, bucket in buckets:
        print(f"  {label:<26} {pbar(len(bucket), nr)}")
    avg_rr   = sum(rr_vals) / nr
    med_rr   = sorted(rr_vals)[nr // 2]
    below_15 = sum(1 for v in rr_vals if v < 1.5)
    print(
        f"\n  Avg={avg_rr:.2f}x  Median={med_rr:.2f}x  "
        f"Di bawah 1.5x: {below_15}/{nr} ({below_15/nr*100:.0f}%)"
    )

# ── 6. Confidence distribution per rating ────────────────────────────────────
section("CONFIDENCE PER RATING")
for label, group_ratings in [
    ("BUY+STRONG_BUY", BUY_RATINGS),
    ("HOLD",           HOLD_RATINGS),
    ("AVOID+SELL",     AVOID_RATINGS),
]:
    grp = [r["conf"] for r in records if r["rating"] in group_ratings]
    if not grp:
        continue
    avg = sum(grp) / len(grp)
    lo, hi = min(grp), max(grp)
    print(f"  {label:<20}  n={len(grp):>3}  avg={avg:.0%}  min={lo:.0%}  max={hi:.0%}")

# ── 7. Outcome per regime ─────────────────────────────────────────────────────
section("OUTCOME PER REGIME")
reg_buy   = defaultdict(int)
reg_total = defaultdict(int)
reg_conf  = defaultdict(list)
for r in records:
    rg = r["regime"]
    reg_total[rg] += 1
    reg_conf[rg].append(r["conf"])
    if r["rating"] in BUY_RATINGS:
        reg_buy[rg] += 1

print(f"  {'Regime':<12}  {'n':>4}  {'BUY%':>6}  {'AvgConf':>8}  BUY rate")
print("  " + "─" * 56)
for rg in sorted(reg_total.keys()):
    nt      = reg_total[rg]
    nb2     = reg_buy[rg]
    ac      = sum(reg_conf[rg]) / nt
    buy_pct = nb2 / nt * 100
    bar     = "█" * int(buy_pct / 5)
    print(f"  {rg:<12}  {nt:>4}  {buy_pct:>5.0f}%  {ac:>7.1%}  {bar}")

# ── 8. Debate rounds ─────────────────────────────────────────────────────────
section("DEBATE ROUNDS DISTRIBUTION")
rounds_cnt = Counter(r["rounds"] for r in records)
for rnd in sorted(rounds_cnt):
    k = rounds_cnt[rnd]
    print(f"  Round {rnd}  {pbar(k, n_total)}")

# ── 9. Near-BUY (HOLD, R/R tertinggi) ────────────────────────────────────────
section("10 HOLD TERDEKAT KE BUY  (R/R tertinggi — kandidat revisit saat pullback)")
near_buy = sorted(
    [r for r in records if r["rating"] in HOLD_RATINGS and r["rr"] is not None],
    key=lambda r: (-(r["rr"] or 0), r["run_date"]),
)[:10]
print(f"  {'Ticker':<8}  {'Tanggal':<12}  {'R/R':>5}  {'Conf':>6}  {'AbvEntry':>9}  {'Regime':<10}  {'Harga':>8}  {'FV':>8}")
print("  " + "─" * 76)
for r in near_buy:
    ae  = "YES" if r["above_entry"] else " - "
    px  = r["cur_px"] or 0
    fv_ = r["fv"] or 0
    print(
        f"  {r['ticker']:<8}  {str(r['run_date']):<12}  {r['rr']:>5.2f}  {r['conf']:>5.0%}"
        f"  {ae:>9}  {r['regime']:<10}  {float(px):>8,.0f}  {float(fv_):>8,.0f}"
    )

# ── 10. Bull / Bear Symmetry Check ───────────────────────────────────────────
section("BULL / BEAR SYMMETRY CHECK")

def _agent_buy_pct(agent: str) -> tuple[int, int, float]:
    votes = [r["agent_votes"].get(agent) for r in records if r["agent_votes"].get(agent)]
    n = len(votes)
    if n == 0:
        return 0, 0, 0.0
    n_buy = sum(1 for v in votes if v in {"BUY", "STRONG_BUY"})
    return n_buy, n, n_buy / n * 100

for ag, label in [("bull", "Bull"), ("bear", "Bear")]:
    n_buy, n_tot, pct = _agent_buy_pct(ag)
    n_hold = sum(1 for r in records if r["agent_votes"].get(ag) == "HOLD")
    n_avoid = sum(1 for r in records if r["agent_votes"].get(ag) in {"AVOID", "SELL"})
    flag = ""
    if ag == "bull" and pct > 95:
        flag = "  ⚠️  SELALU BUY — debate tidak genuine"
    if ag == "bear" and n_avoid / n_tot * 100 > 95 if n_tot else False:
        flag = "  ⚠️  SELALU AVOID — debate tidak genuine"
    print(f"  {label:<6}  n={n_tot:>4}  BUY={n_buy:>4} ({pct:4.0f}%)  "
          f"HOLD={n_hold:>4}  AVOID={n_avoid:>4}{flag}")

# ── 11. CIO Override Rate ─────────────────────────────────────────────────────
section("CIO OVERRIDE RATE  (consensus → CIO final)")

def _tier(r: str) -> str:
    if r in BUY_RATINGS:   return "BUY"
    if r in HOLD_RATINGS:  return "HOLD"
    if r in AVOID_RATINGS: return "AVOID"
    return ""

cio_records = [r for r in records if r["cw"] and _tier(r["cw"]) and _tier(r["rating"])]
n_cio = len(cio_records)
if n_cio:
    same      = sum(1 for r in cio_records if _tier(r["cw"]) == _tier(r["rating"]))
    upgraded  = sum(1 for r in cio_records
                    if (_tier(r["cw"]) == "AVOID" and _tier(r["rating"]) in {"HOLD","BUY"})
                    or (_tier(r["cw"]) == "HOLD"  and _tier(r["rating"]) == "BUY"))
    downgraded= sum(1 for r in cio_records
                    if (_tier(r["cw"]) == "BUY"  and _tier(r["rating"]) in {"HOLD","AVOID"})
                    or (_tier(r["cw"]) == "HOLD" and _tier(r["rating"]) == "AVOID"))
    print(f"  Debates dengan consensus_winner terisi : {n_cio}")
    print(f"  CIO setuju dengan consensus            : {pbar(same,       n_cio)}")
    print(f"  CIO upgrade  (consensus → lebih bullish): {pbar(upgraded,  n_cio)}")
    print(f"  CIO downgrade (consensus → lebih bearish): {pbar(downgraded,n_cio)}")
    if same / n_cio > 0.95:
        print("\n  ⚠️  CIO hampir selalu setuju — mungkin rubber-stamp (boros pro token)")
else:
    print("  consensus_winner tidak terisi di records ini (jalankan pipeline baru).")

# ── 12. Token Budget per Debate ───────────────────────────────────────────────
section("TOKEN BUDGET PER DEBATE  (flash + pro LLM calls)")

flash_vals = [r["flash_calls"] for r in records]
pro_vals   = [r["pro_calls"]   for r in records]
total_f    = sum(flash_vals)
total_p    = sum(pro_vals)
avg_f      = total_f / n_total
avg_p      = total_p / n_total

print(f"  Total flash calls  : {total_f:>6}  (avg {avg_f:.1f}/debate)")
print(f"  Total pro calls    : {total_p:>6}  (avg {avg_p:.1f}/debate)")

buckets_p = [
    ("0 pro calls  (scouts only)",  [r for r in records if r["pro_calls"] == 0]),
    ("1–3 pro calls",               [r for r in records if 1 <= r["pro_calls"] <= 3]),
    ("4–6 pro calls",               [r for r in records if 4 <= r["pro_calls"] <= 6]),
    ("7–10 pro calls",              [r for r in records if 7 <= r["pro_calls"] <= 10]),
    ("> 10 pro calls  (expensive)", [r for r in records if r["pro_calls"] > 10]),
]
print()
for label, grp in buckets_p:
    print(f"  {label:<30} {pbar(len(grp), n_total)}")

top_expensive = sorted(records, key=lambda r: -(r["pro_calls"] + r["flash_calls"] * 0.1))[:10]
print(f"\n  Top 10 debate paling mahal (pro+flash):")
print(f"  {'Ticker':<8}  {'Tanggal':<12}  {'Pro':>4}  {'Flash':>5}  {'Rating':<10}")
print("  " + "─" * 46)
for r in top_expensive:
    print(f"  {r['ticker']:<8}  {str(r['run_date']):<12}  {r['pro_calls']:>4}  "
          f"{r['flash_calls']:>5}  {r['rating']:<10}")

# ── 13. Error & Silent Failure Rate ──────────────────────────────────────────
section("ERROR & SILENT FAILURE RATE")

errors = [r for r in records if r["has_error"]]
n_err  = len(errors)
print(f"  Debates dengan error field terisi: {pbar(n_err, n_total)}")
if errors:
    err_ctr = Counter(r["error_msg"][:60] for r in errors)
    print(f"\n  Error paling umum (top 5):")
    for msg, cnt in err_ctr.most_common(5):
        print(f"    [{cnt:>3}x]  {msg}")

# ── 14. Scout Data Quality ────────────────────────────────────────────────────
# fundamental_data & news_brief tidak disimpan ke JSON (by design — teks besar,
# hanya dipakai selama debate). Proxy dari field yang memang tersimpan:
#   raw_data_summary  → seberapa kaya data pasar yang masuk ke pipeline
#   first_bull_len    → seberapa kaya brief yang diterima debater
#   fundamental_scout confidence → apakah scout yakin dengan data yang ada
section("SCOUT DATA QUALITY  (proxy dari field tersimpan)")

thin_raw   = [r for r in records if r["raw_summary_len"] < 500]
thin_brief = [r for r in records if 0 < r["first_bull_len"] < 400]
low_fscout = [r for r in records
              if r["agent_conf"].get("fundamental_scout", 1.0) < 0.60]
news_fails = [r for r in records if r["news_fail"]]
no_bull    = [r for r in records if r["first_bull_len"] == 0]

print(f"  raw_data_summary < 500 chars (data pasar tipis)  : {pbar(len(thin_raw),   n_total)}")
print(f"  bull R1 message  < 400 chars (brief tipis)       : {pbar(len(thin_brief), n_total)}")
print(f"  bull R1 message  = 0  (debate tanpa bull message): {pbar(len(no_bull),    n_total)}")
print(f"  fundamental_scout confidence < 0.60              : {pbar(len(low_fscout), n_total)}")
print(f"  news_fetch_failure = True                        : {pbar(len(news_fails), n_total)}")

# cross-check: apakah data tipis → lebih sering AVOID?
if thin_raw:
    avoid_thin = sum(1 for r in thin_raw if r["rating"] in AVOID_RATINGS) / len(thin_raw)
    avoid_all  = sum(1 for r in records  if r["rating"] in AVOID_RATINGS) / n_total
    delta = avoid_thin - avoid_all
    flag  = "  ⚠️  mungkin data-driven AVOID" if delta > 0.10 else ""
    print(f"\n  AVOID rate — data tipis: {avoid_thin:.0%}  vs  semua debate: {avoid_all:.0%}{flag}")

# distribusi panjang raw_data_summary
print(f"\n  Distribusi raw_data_summary length:")
rsl_buckets = [
    ("0 chars  (kosong)",        [r for r in records if r["raw_summary_len"] == 0]),
    ("1–499 chars  (sangat tipis)", [r for r in records if 1   <= r["raw_summary_len"] < 500]),
    ("500–1999 chars",            [r for r in records if 500  <= r["raw_summary_len"] < 2000]),
    ("2000–4999 chars",           [r for r in records if 2000 <= r["raw_summary_len"] < 5000]),
    (">= 5000 chars  (kaya)",     [r for r in records if r["raw_summary_len"] >= 5000]),
]
for label, grp in rsl_buckets:
    print(f"    {label:<30} {pbar(len(grp), n_total)}")

# ── 15. Confidence Trend over Time ───────────────────────────────────────────
section("CONFIDENCE TREND PER BULAN")

monthly: dict[str, list] = defaultdict(list)
monthly_buy: dict[str, int] = defaultdict(int)
for r in records:
    monthly[r["month"]].append(r["conf"])
    if r["rating"] in BUY_RATINGS:
        monthly_buy[r["month"]] += 1

print(f"  {'Bulan':<8}  {'n':>4}  {'AvgConf':>8}  {'BUY%':>6}  Trend")
print("  " + "─" * 50)
prev_conf = None
for month in sorted(monthly.keys()):
    grp  = monthly[month]
    n_m  = len(grp)
    ac   = sum(grp) / n_m
    bp   = monthly_buy[month] / n_m * 100
    arr  = ""
    if prev_conf is not None:
        arr = "▲" if ac > prev_conf + 0.01 else ("▼" if ac < prev_conf - 0.01 else "─")
    prev_conf = ac
    print(f"  {month:<8}  {n_m:>4}  {ac:>7.1%}  {bp:>5.0f}%  {arr}")

# ── 16. R/R Outlier Investigation ────────────────────────────────────────────
section("R/R OUTLIER INVESTIGATION  (> 10x atau tidak ada stop-loss)")

outliers = [
    r for r in records
    if (r["rr"] is not None and r["rr"] > 10.0)
    or (r["rr"] is not None and r["stop_px"] is None)
    or (r["rr"] is None and r["rating"] in BUY_RATINGS)
]
outliers.sort(key=lambda r: -(r["rr"] or 0))

print(f"  Total outlier: {len(outliers)}")
if outliers:
    print(f"\n  {'Ticker':<8}  {'Tanggal':<12}  {'R/R':>7}  {'Rating':<10}  "
          f"{'Entry':<18}  {'Target':>8}  {'Stop':>8}")
    print("  " + "─" * 80)
    for r in outliers[:15]:
        rr_s  = f"{r['rr']:.2f}x" if r["rr"] else "N/A"
        tgt   = f"{float(r['target_px']):,.0f}" if r["target_px"] else "N/A"
        stp   = f"{float(r['stop_px']):,.0f}"   if r["stop_px"]   else "MISSING ⚠️"
        entry = str(r["entry_range"] or "N/A")[:17]
        print(f"  {r['ticker']:<8}  {str(r['run_date']):<12}  {rr_s:>7}  "
              f"{r['rating']:<10}  {entry:<18}  {tgt:>8}  {stp:>8}")
    if len(outliers) > 15:
        print(f"  ... dan {len(outliers) - 15} lainnya")

print("\n✅ Selesai.\n")
