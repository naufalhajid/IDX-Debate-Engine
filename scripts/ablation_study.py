"""
scripts/ablation_study.py — Ablation study: does the full debate pipeline outperform
simpler configurations on realized backtest outcomes?

Reads:
  output/observations/observations.jsonl  — per-agent predictions
  output/backtest/backtest_memory.jsonl   — realized outcomes

Simulates three configurations from existing data:
  Config A (Screener):     fundamental_scout confidence from observations (proxy for screener signal)
  Config B (Scout):        fundamental_scout OR chartist confidence > 0.65
  Config C (Full Debate):  CIOVerdict rating in {BUY, STRONG_BUY} — actual pipeline output

Outputs:
  Printed table with win_rate, Brier score, avg_pnl_pct, sample_count per config
  output/ablation/ablation_report.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OBS_PATH = Path("output/observations/observations.jsonl")
BT_PATH  = Path("output/backtest/backtest_memory.jsonl")
OUT_PATH = Path("output/ablation/ablation_report.json")

MIN_SAMPLE = 30


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def brier_score(confidence: float, win: float) -> float:
    return (confidence - win) ** 2


def config_a_signal(obs_by_ticker: dict[str, list[dict]]) -> dict[str, tuple[float, float]]:
    """Config A: fundamental_scout confidence — proxy for screener-only signal."""
    result: dict[str, tuple[float, float]] = {}
    for ticker, obs_list in obs_by_ticker.items():
        scout = [o for o in obs_list if o.get("agent") == "fundamental_scout"]
        if not scout:
            continue
        conf = float(scout[-1].get("confidence") or 0.0)
        position = str(scout[-1].get("position") or "").upper()
        if position in ("BUY", "STRONG_BUY"):
            result[ticker] = (conf, 1.0)
        else:
            result[ticker] = (conf, 0.0)
    return result


def config_b_signal(obs_by_ticker: dict[str, list[dict]]) -> dict[str, tuple[float, float]]:
    """Config B: scout (fundamental OR chartist) confidence > 0.65 BUY signal."""
    result: dict[str, tuple[float, float]] = {}
    for ticker, obs_list in obs_by_ticker.items():
        scouts = [
            o for o in obs_list
            if o.get("agent") in ("fundamental_scout", "chartist")
            and str(o.get("position") or "").upper() in ("BUY", "STRONG_BUY")
        ]
        if not scouts:
            continue
        best = max(scouts, key=lambda o: float(o.get("confidence") or 0))
        conf = float(best.get("confidence") or 0.0)
        if conf >= 0.65:
            result[ticker] = (conf, 1.0)
    return result


def config_c_signal(outcomes: dict[str, dict]) -> dict[str, tuple[float, float]]:
    """Config C: actual pipeline output — CIOVerdict BUY/STRONG_BUY."""
    result: dict[str, tuple[float, float]] = {}
    for ticker, rec in outcomes.items():
        conf = float(rec.get("confidence_at_entry") or 0.5)
        result[ticker] = (conf, 1.0)
    return result


def evaluate(
    signal: dict[str, tuple[float, float]],
    outcomes: dict[str, dict],
) -> dict:
    matched = {t: (conf, flag, outcomes[t]) for t, (conf, flag) in signal.items() if t in outcomes}
    if not matched:
        return {"win_rate": None, "brier": None, "avg_pnl_pct": None, "sample": 0}

    wins = 0
    brier_sum = 0.0
    pnl_sum = 0.0
    for ticker, (conf, _, rec) in matched.items():
        win = 1.0 if rec.get("outcome") == "win" else 0.0
        wins += win
        brier_sum += brier_score(conf, win)
        pnl_sum += float(rec.get("pnl_pct") or 0.0)

    n = len(matched)
    return {
        "win_rate": wins / n,
        "brier": brier_sum / n,
        "avg_pnl_pct": pnl_sum / n,
        "sample": n,
        "insufficient": n < MIN_SAMPLE,
    }


def run_ablation() -> dict:
    observations = load_jsonl(OBS_PATH)
    bt_records = load_jsonl(BT_PATH)

    outcomes = {
        r["ticker"]: r
        for r in bt_records
        if r.get("outcome") not in ("open", None)
        and r.get("verdict_rating") in ("BUY", "STRONG_BUY")
    }

    obs_by_ticker: dict[str, list[dict]] = {}
    for o in observations:
        ticker = o.get("ticker") or ""
        obs_by_ticker.setdefault(ticker, []).append(o)

    configs = {
        "A_screener_proxy": evaluate(config_a_signal(obs_by_ticker), outcomes),
        "B_scout_only": evaluate(config_b_signal(obs_by_ticker), outcomes),
        "C_full_debate": evaluate(config_c_signal(outcomes), outcomes),
    }

    report = {"configs": configs, "total_closed_buy_records": len(outcomes)}

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n{'Config':<25} {'Win%':>7} {'Brier':>8} {'AvgPnL%':>9} {'N':>5}  Note")
    print("-" * 70)
    for name, m in configs.items():
        if m["sample"] == 0:
            print(f"  {name:<23} {'N/A':>7} {'N/A':>8} {'N/A':>9} {0:>5}  NO DATA")
        else:
            note = "INSUFFICIENT DATA (n<30)" if m.get("insufficient") else ""
            wr = f"{m['win_rate']:.1%}" if m["win_rate"] is not None else "N/A"
            br = f"{m['brier']:.3f}" if m["brier"] is not None else "N/A"
            pnl = f"{m['avg_pnl_pct']:+.2f}%" if m["avg_pnl_pct"] is not None else "N/A"
            print(f"  {name:<23} {wr:>7} {br:>8} {pnl:>9} {m['sample']:>5}  {note}")

    print(f"\nTotal closed BUY records: {len(outcomes)}")
    print(f"Report written to: {OUT_PATH}\n")
    return report


if __name__ == "__main__":
    run_ablation()
    sys.exit(0)
