"""
Diagnostic: Consensus mechanism deep-dive.

Usage:
    uv run python scratch/diag_consensus.py
    uv run python scratch/diag_consensus.py --days 15

Shows:
  1.  Ringkasan & consensus method distribution (voting/soft_hold/confidence_winner)
  2.  Siapa yang menang consensus (winning agent + position)
  3.  CIO override rate (consensus_winner position vs final verdict)
  4.  Soft_hold analysis (timing disagreement breakdown)
  5.  Confidence winner analysis (direction disagreement, siapa yang menang)
  6.  Voting consensus — distribusi suara mayoritas
  7.  Debates tanpa consensus (consensus_reached=False) — apa yang terjadi?
  8.  Round saat consensus winner ditentukan
  9.  Consensus position vs final rating (per method)
  10. Bull/Bear sebagai consensus winner
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

parser = argparse.ArgumentParser()
parser.add_argument("--days", type=int, default=15, help="0=semua; N=N hari terakhir")
parser.add_argument("--debates-dir", default="output/debates")
args = parser.parse_args()

DEBATES_DIR = Path(args.debates_dir)
CUTOFF: date | None = (
    (datetime.now() - timedelta(days=args.days)).date() if args.days > 0 else None
)

BUY_RATINGS   = {"BUY", "STRONG_BUY"}
HOLD_RATINGS  = {"HOLD"}
AVOID_RATINGS = {"AVOID", "SELL", "INSUFFICIENT_DATA"}

def _tier(r: str) -> str:
    if r in BUY_RATINGS:   return "BUY"
    if r in HOLD_RATINGS:  return "HOLD"
    if r in AVOID_RATINGS: return "AVOID"
    return "?"

def pbar(k, total, width=20):
    pct = k / total * 100 if total else 0
    return f"{k:>4} ({pct:4.0f}%)  {'█' * int(pct / (100 / width))}"

def section(title):
    print()
    print("═" * 68)
    print(f"  {title}")
    print("═" * 68)


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

    verdict = data.get("verdict") or {}
    rating  = verdict.get("rating") or "UNKNOWN"
    conf    = float(verdict.get("confidence") or 0.0)
    ticker  = data.get("ticker") or json_path.parent.parent.name

    cw_raw   = data.get("consensus_winner")
    cw_agent = ""
    cw_pos   = ""
    cw_conf  = 0.0
    cw_round = 0
    if isinstance(cw_raw, dict):
        cw_agent = cw_raw.get("agent", "")
        cw_pos   = cw_raw.get("position", "")
        cw_conf  = float(cw_raw.get("effective_confidence") or cw_raw.get("confidence") or 0.0)
        cw_round = int(cw_raw.get("round") or 0)

    agent_votes: dict[str, str]   = {}
    agent_conf:  dict[str, float] = {}
    for vote in data.get("agent_votes") or []:
        ag = vote.get("agent", "")
        agent_votes[ag] = vote.get("position", "")
        agent_conf[ag]  = float(
            vote.get("effective_confidence") or vote.get("confidence") or 0.0
        )

    records.append({
        "ticker":            ticker,
        "run_date":          run_date,
        "rating":            rating,
        "conf":              conf,
        "consensus_reached": bool(data.get("consensus_reached", False)),
        "consensus_method":  str(data.get("consensus_method") or "none"),
        "disagreement_type": str(data.get("disagreement_type") or "none"),
        "debate_rounds":     int(data.get("debate_rounds") or 1),
        "cw_agent":          cw_agent,
        "cw_pos":            cw_pos,
        "cw_conf":           cw_conf,
        "cw_round":          cw_round,
        "agent_votes":       agent_votes,
        "agent_conf":        agent_conf,
    })

if not records:
    print("Tidak ada debate JSON yang ditemukan.")
    raise SystemExit(0)

records.sort(key=lambda r: r["run_date"])
n     = len(records)
d_min = records[0]["run_date"]
d_max = records[-1]["run_date"]
n_with_cw = sum(1 for r in records if r["cw_agent"])

# ── 1. Ringkasan & Consensus Method Distribution ──────────────────────────────
section("RINGKASAN")
print(f"  Total debates         : {n}")
print(f"  Rentang tanggal       : {d_min} → {d_max}")
if CUTOFF:
    print(f"  Filter                : {args.days} hari terakhir (sejak {CUTOFF})")
print(f"  consensus_winner terisi: {n_with_cw} ({n_with_cw/n*100:.0f}%)")

section("CONSENSUS METHOD DISTRIBUTION")
methods = Counter(r["consensus_method"] for r in records)
desc = {
    "voting":            "mayoritas suara ≥ threshold",
    "soft_hold":         "timing disagreement → default HOLD",
    "confidence_winner": "direction disagreement → agent confidence tertinggi menang",
    "none":              "consensus tidak tercapai (CIO putuskan sendiri)",
}
for method in ("voting", "soft_hold", "confidence_winner", "none"):
    k = methods.get(method, 0)
    if k == 0:
        continue
    print(f"  {method:<22} {pbar(k, n)}")
    print(f"    └ {desc[method]}")

# ── 2. Siapa yang Menang Consensus ────────────────────────────────────────────
section("SIAPA YANG MENANG CONSENSUS  (consensus_winner agent)")
winner_agents = Counter(r["cw_agent"] for r in records if r["cw_agent"])
print(f"  (dari {n_with_cw} debates dengan consensus_winner terisi)\n")
print(f"  {'Agent':<26}  {'Menang'}   Position breakdown")
print("  " + "─" * 62)
for agent, cnt in winner_agents.most_common():
    pos_cnts = Counter(r["cw_pos"] for r in records if r["cw_agent"] == agent)
    pos_str  = "  ".join(f"{p}:{c}" for p, c in pos_cnts.most_common())
    print(f"  {agent:<26}  {pbar(cnt, n_with_cw, width=12)}   {pos_str}")

# ── 3. CIO Override Rate ──────────────────────────────────────────────────────
section("CIO OVERRIDE RATE  (consensus position → final verdict)")
cio_recs = [r for r in records if r["cw_pos"] and _tier(r["cw_pos"]) != "?"]
n_cio = len(cio_recs)
if n_cio:
    same       = [r for r in cio_recs if _tier(r["cw_pos"]) == _tier(r["rating"])]
    upgraded   = [r for r in cio_recs
                  if (_tier(r["cw_pos"]) == "AVOID" and _tier(r["rating"]) in {"HOLD","BUY"})
                  or (_tier(r["cw_pos"]) == "HOLD"  and _tier(r["rating"]) == "BUY")]
    downgraded = [r for r in cio_recs
                  if (_tier(r["cw_pos"]) == "BUY"   and _tier(r["rating"]) in {"HOLD","AVOID"})
                  or (_tier(r["cw_pos"]) == "HOLD"  and _tier(r["rating"]) == "AVOID")]

    print(f"  Debates dievaluasi          : {n_cio}")
    print(f"  CIO setuju                  : {pbar(len(same),       n_cio)}")
    print(f"  CIO upgrade  (→ lebih bullish): {pbar(len(upgraded),  n_cio)}")
    print(f"  CIO downgrade (→ lebih bearish): {pbar(len(downgraded),n_cio)}")

    if upgraded:
        print(f"\n  CIO Upgrade — contoh:")
        print(f"  {'Ticker':<8}  {'Tanggal':<12}  {'CW Pos':<8}  {'Final':<10}  Conf")
        print("  " + "─" * 50)
        for r in sorted(upgraded, key=lambda x: -x["conf"])[:5]:
            print(f"  {r['ticker']:<8}  {r['run_date']}  "
                  f"{r['cw_pos']:<8}  {r['rating']:<10}  {r['conf']:.0%}")

    if downgraded:
        print(f"\n  CIO Downgrade — contoh:")
        print(f"  {'Ticker':<8}  {'Tanggal':<12}  {'CW Pos':<8}  {'Final':<10}  Conf")
        print("  " + "─" * 50)
        for r in sorted(downgraded, key=lambda x: -x["conf"])[:5]:
            print(f"  {r['ticker']:<8}  {r['run_date']}  "
                  f"{r['cw_pos']:<8}  {r['rating']:<10}  {r['conf']:.0%}")

    if len(same) / n_cio > 0.90:
        print("\n  ⚠️  CIO hampir selalu setuju dengan consensus (>90%) — mungkin rubber-stamp")
else:
    print("  Tidak ada records dengan consensus_winner terisi.")

# ── 4. Soft_hold Analysis ─────────────────────────────────────────────────────
section("SOFT_HOLD ANALYSIS  (timing disagreement → HOLD)")
sh = [r for r in records if r["consensus_method"] == "soft_hold"]
n_sh = len(sh)
if n_sh:
    print(f"  Total soft_hold : {n_sh} ({n_sh/n*100:.0f}% dari semua debates)\n")

    sh_winners = Counter(r["cw_agent"] for r in sh)
    print("  Winner agent di soft_hold debates:")
    for ag, cnt in sh_winners.most_common():
        print(f"    {ag:<30} {cnt}x")

    print(f"\n  Vote breakdown bull/bear/DA di soft_hold debates:")
    for ag in ("bull", "bear", "devils_advocate"):
        pos_cnt = Counter(r["agent_votes"].get(ag, "-") for r in sh)
        parts   = "  ".join(f"{p}:{c}" for p, c in pos_cnt.most_common() if p != "-")
        if parts:
            print(f"    {ag:<24} {parts}")

    final_dist = Counter(_tier(r["rating"]) for r in sh)
    print(f"\n  Final verdict setelah soft_hold:")
    for tier in ("BUY", "HOLD", "AVOID"):
        k = final_dist.get(tier, 0)
        if k:
            print(f"    {tier:<8} {pbar(k, n_sh, width=12)}")

# ── 5. Confidence Winner Analysis ─────────────────────────────────────────────
section("CONFIDENCE WINNER ANALYSIS  (direction disagreement)")
cfw = [r for r in records if r["consensus_method"] == "confidence_winner"]
n_cfw = len(cfw)
if n_cfw:
    print(f"  Total confidence_winner: {n_cfw} ({n_cfw/n*100:.0f}% dari semua debates)\n")

    winner_breakdown = Counter(f"{r['cw_agent']} ({r['cw_pos']})" for r in cfw)
    print("  Siapa yang menang dengan confidence tertinggi:")
    for k, cnt in winner_breakdown.most_common():
        print(f"    {k:<38} {cnt:>3}x")

    avg_winning_conf = sum(r["cw_conf"] for r in cfw) / n_cfw
    print(f"\n  Avg winning confidence : {avg_winning_conf:.1%}")

    final_dist = Counter(_tier(r["rating"]) for r in cfw)
    print(f"\n  Final verdict setelah confidence_winner:")
    for tier in ("BUY", "HOLD", "AVOID"):
        k = final_dist.get(tier, 0)
        if k:
            print(f"    {tier:<8} {pbar(k, n_cfw, width=12)}")

# ── 6. Voting Consensus — Distribusi Suara ────────────────────────────────────
section("VOTING CONSENSUS — JUMLAH SUARA MAYORITAS")
voting = [r for r in records if r["consensus_method"] == "voting"]
n_v = len(voting)
if n_v:
    print(f"  Total voting consensus: {n_v} ({n_v/n*100:.0f}% dari semua debates)\n")

    agreement_counts: Counter = Counter()
    for r in voting:
        if not r["cw_pos"]:
            continue
        agree = sum(1 for pos in r["agent_votes"].values() if pos == r["cw_pos"])
        agreement_counts[agree] += 1

    print("  Jumlah agent yang vote sama dengan winner:")
    for cnt in sorted(agreement_counts.keys()):
        print(f"    {cnt} agent setuju  {pbar(agreement_counts[cnt], n_v, width=12)}")

    final_dist = Counter(_tier(r["rating"]) for r in voting)
    print(f"\n  Final verdict setelah voting:")
    for tier in ("BUY", "HOLD", "AVOID"):
        k = final_dist.get(tier, 0)
        if k:
            print(f"    {tier:<8} {pbar(k, n_v, width=12)}")

# ── 7. Debates Tanpa Consensus ────────────────────────────────────────────────
section("DEBATES TANPA CONSENSUS  (consensus_reached = False)")
no_cons = [r for r in records if not r["consensus_reached"]]
n_nc = len(no_cons)
if n_nc:
    print(f"  Total          : {n_nc} ({n_nc/n*100:.0f}% dari semua debates)\n")

    final_dist = Counter(_tier(r["rating"]) for r in no_cons)
    print("  Final verdict (CIO putuskan sendiri):")
    for tier in ("BUY", "HOLD", "AVOID"):
        k = final_dist.get(tier, 0)
        if k:
            print(f"    {tier:<8} {pbar(k, n_nc, width=12)}")

    avg_conf = sum(r["conf"] for r in no_cons) / n_nc
    print(f"\n  Avg confidence (tanpa consensus): {avg_conf:.1%}")

    print(f"\n  Vote breakdown bull/bear/DA di no-consensus debates:")
    for ag in ("bull", "bear", "devils_advocate"):
        pos_cnt = Counter(r["agent_votes"].get(ag, "-") for r in no_cons)
        parts   = "  ".join(f"{p}:{c}" for p, c in pos_cnt.most_common() if p != "-")
        if parts:
            print(f"    {ag:<24} {parts}")

    print(f"\n  Contoh debates tanpa consensus:")
    print(f"  {'Ticker':<8}  {'Tanggal':<12}  {'Rnd':>4}  {'Rating':<12}  bull/bear/da")
    print("  " + "─" * 60)
    for r in sorted(no_cons, key=lambda x: x["run_date"])[:10]:
        bull = r["agent_votes"].get("bull", "-")
        bear = r["agent_votes"].get("bear", "-")
        da   = r["agent_votes"].get("devils_advocate", "-")
        print(f"  {r['ticker']:<8}  {str(r['run_date']):<12}  {r['debate_rounds']:>4}  "
              f"{r['rating']:<12}  {bull}/{bear}/{da}")
else:
    print("  Semua debates mencapai consensus.")

# ── 8. Round Saat Consensus Winner Ditentukan ─────────────────────────────────
section("ROUND SAAT CONSENSUS WINNER DITENTUKAN")
round_dist = Counter(r["cw_round"] for r in records if r["cw_agent"])
for rnd in sorted(round_dist.keys()):
    k     = round_dist[rnd]
    label = f"Round {rnd}  (scout phase — sebelum debate)" if rnd == 0 else f"Round {rnd}"
    print(f"  {label:<44} {pbar(k, n_with_cw or 1, width=14)}")

# ── 9. Consensus Position vs Final Rating per Method ─────────────────────────
section("CONSENSUS POSITION vs FINAL RATING  (per method)")
print(f"  {'Method':<22}  {'CW Pos':<6}  {'→ BUY':>8}  {'→ HOLD':>9}  {'→ AVOID':>10}")
print("  " + "─" * 64)
for method in ("voting", "soft_hold", "confidence_winner"):
    grp = [r for r in records if r["consensus_method"] == method and r["cw_pos"]]
    if not grp:
        continue
    for cw_p in ("BUY", "HOLD", "AVOID"):
        sub = [r for r in grp if _tier(r["cw_pos"]) == cw_p]
        if not sub:
            continue
        n_sub   = len(sub)
        to_buy  = sum(1 for r in sub if _tier(r["rating"]) == "BUY")
        to_hold = sum(1 for r in sub if _tier(r["rating"]) == "HOLD")
        to_avd  = sum(1 for r in sub if _tier(r["rating"]) == "AVOID")
        print(f"  {method:<22}  {cw_p:<6}  "
              f"{to_buy:>4} ({to_buy/n_sub*100:2.0f}%)  "
              f"{to_hold:>4} ({to_hold/n_sub*100:2.0f}%)  "
              f"{to_avd:>5} ({to_avd/n_sub*100:2.0f}%)")

# ── 10. Bull/Bear sebagai Consensus Winner ────────────────────────────────────
section("BULL / BEAR SEBAGAI CONSENSUS WINNER")
for ag in ("bull", "bear"):
    wins  = [r for r in records if r["cw_agent"] == ag]
    n_wins = len(wins)
    if n_wins == 0:
        print(f"  {ag.capitalize():<8}  tidak pernah jadi consensus winner")
        continue
    pos_dist   = Counter(r["cw_pos"]   for r in wins)
    round_dist_ag = Counter(r["cw_round"] for r in wins)
    final_dist = Counter(_tier(r["rating"]) for r in wins)
    print(f"  {ag.capitalize():<8}  menang {n_wins}x")
    print(f"    Position : " + "  ".join(f"{p}:{c}" for p, c in pos_dist.most_common()))
    print(f"    Round    : " + "  ".join(f"R{rnd}:{c}" for rnd, c in sorted(round_dist_ag.items())))
    print(f"    → Final  : " + "  ".join(f"{t}:{c}" for t, c in final_dist.most_common()))

print("\n✅ Selesai.\n")
