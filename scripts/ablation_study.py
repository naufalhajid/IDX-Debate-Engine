"""
scripts/ablation_study.py — Ablation study: does the full debate pipeline outperform
simpler configurations on realized backtest outcomes?

Reads:
  output/observations/observations.jsonl  — per-agent predictions
  output/backtest/backtest_memory.jsonl   — realized outcomes

Simulates three configurations from existing data:
  Config A (Screener):     fundamental_scout BUY signal from observations
  Config B (Scout):        fundamental_scout OR chartist confidence > 0.65 BUY signal
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


def config_a_signal(obs_by_ticker: dict[str, list[dict]]) -> dict[str, float]:
    """Config A: tickers where fundamental_scout said BUY/STRONG_BUY → their confidence."""
    result: dict[str, float] = {}
    for ticker, obs_list in obs_by_ticker.items():
        scout = [o for o in obs_list if o.get("agent") == "fundamental_scout"]
        if not scout:
            continue
        last = scout[-1]
        if str(last.get("position") or "").upper() in ("BUY", "STRONG_BUY"):
            result[ticker] = float(last.get("confidence") or 0.0)
    return result


def config_b_signal(obs_by_ticker: dict[str, list[dict]]) -> dict[str, float]:
    """Config B: tickers where fundamental_scout OR chartist conf > 0.65 said BUY."""
    result: dict[str, float] = {}
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
            result[ticker] = conf
    return result


def config_c_signal(outcome_records: list[dict]) -> dict[str, float]:
    """Config C: tickers where pipeline issued BUY/STRONG_BUY → confidence_at_entry."""
    result: dict[str, float] = {}
    for rec in outcome_records:
        ticker = rec.get("ticker") or ""
        if ticker and ticker not in result:
            result[ticker] = float(rec.get("confidence_at_entry") or 0.5)
    return result


def evaluate(
    signal: dict[str, float],
    outcome_records: list[dict],
) -> dict:
    """Join signal against ALL outcome records (not just last per ticker).

    Only tickers where the signal fired (present in signal dict) are included.
    """
    matched = [
        (signal[rec["ticker"]], rec)
        for rec in outcome_records
        if rec.get("ticker") in signal
    ]
    if not matched:
        return {"win_rate": None, "brier": None, "avg_pnl_pct": None, "sample": 0}

    wins = 0
    brier_sum = 0.0
    pnl_sum = 0.0
    for conf, rec in matched:
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

    # All closed BUY/STRONG_BUY records — kept as list to count multi-trade tickers
    outcome_records = [
        r for r in bt_records
        if r.get("outcome") not in ("open", None)
        and r.get("verdict_rating") in ("BUY", "STRONG_BUY")
    ]

    obs_by_ticker: dict[str, list[dict]] = {}
    for o in observations:
        ticker = o.get("ticker") or ""
        obs_by_ticker.setdefault(ticker, []).append(o)

    configs = {
        "A_screener_proxy": evaluate(config_a_signal(obs_by_ticker), outcome_records),
        "B_scout_only": evaluate(config_b_signal(obs_by_ticker), outcome_records),
        "C_full_debate": evaluate(config_c_signal(outcome_records), outcome_records),
    }

    report = {"configs": configs, "total_closed_buy_records": len(outcome_records)}

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n{'Config':<25} {'Win%':>7} {'Brier':>8} {'AvgPnL%':>9} {'N':>5}  Note")
    print("-" * 70)
    for name, m in configs.items():
        if m["sample"] == 0:
            print(f"  {name:<23} {'N/A':>7} {'N/A':>8} {'N/A':>9} {0:>5}  NO DATA")
        else:
            note = "INSUFFICIENT DATA (n<30)" if m.get("insufficient") else ""
            wr  = f"{m['win_rate']:.1%}" if m["win_rate"] is not None else "N/A"
            br  = f"{m['brier']:.3f}" if m["brier"] is not None else "N/A"
            pnl = f"{m['avg_pnl_pct']:+.2f}%" if m["avg_pnl_pct"] is not None else "N/A"
            print(f"  {name:<23} {wr:>7} {br:>8} {pnl:>9} {m['sample']:>5}  {note}")

    print(f"\nTotal closed BUY records: {len(outcome_records)}")
    print(f"Report written to: {OUT_PATH}\n")
    return report


if __name__ == "__main__":
    run_ablation()
    sys.exit(0)
