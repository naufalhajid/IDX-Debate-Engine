"""scripts/ablation_forward_eval.py — Fase C: evaluator arm-agnostic + laporan komparatif.

VONIS P1.2: baca 3 ledger sandbox forward (quant_only/single_gated/full_debate),
skor outcome realized tiap arm dengan cara IDENTIK, laporkan win-rate/avg-PnL/n per
arm → apakah debate/sentiment (C) > single+gate (B) > quant-only (A)?

Masalah yang dipecahkan (temuan verifikasi Fase A.2): `backtest_outcome_evaluator.
evaluate_memory` TERKOPEL ke artefak debate versioned — `_matching_debate_artifact_exists`
menuntut `debates_dir/TICKER/v{run_id}/{TICKER}_debate.json`, sehingga record Arm A/B
(yang TAK punya artefak debate) di-`skipped: missing_debate_artifact` (empiris 6/6 quant
di-skip). Kalau dibiarkan, dataset forward di-skor NIHIL diam-diam.

Solusi (surgical, bukan reimplement): NETRALKAN satu-satunya kopling debate itu
(`_matching_debate_artifact_exists -> True`) lalu pakai `evaluate_memory` apa adanya. Semua
aturan eligibility/scoring lain (rating ∈ EVALUATED_RATINGS, parse entry_date, fetch bars,
`evaluate_trade_outcome` target/stop/horizon) tetap IDENTIK dengan produksi — jadi ketiga arm
di-skor persis seperti Arm C (debate) di-skor di pipeline nyata. Reimplement loop sengaja
DIHINDARI: risiko divergen halus dari scorer teruji = persis jebakan silent-skip yang mau
dicegah. write=False (laporan read-only; advance open->closed adalah tugas Fase B terjadwal).

CAVEAT FIDELITY (didokumentasikan, bukan bug): Arm A masuk di `Current Price` (market),
Arm B/C di `parse_entry_low` (limit di bawah market). Dengan `--entry-check`, entry B/C hanya
dihitung bila harga benar menyentuh limit; tanpa itu, semua arm di-skor "seandainya terisi".
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Iterator

import core.backtest_outcome_evaluator as ev
from core.backtest_memory import BacktestMemory  # noqa: F401  (dipakai lewat evaluate_memory)

# Reuse konstanta/pathing harness supaya arm & layout ledger tak pernah divergen.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ablation_forward_run import ARMS, FORWARD_DIR, ledger_path  # noqa: E402


@contextlib.contextmanager
def _debate_gate_neutralized() -> Iterator[None]:
    """Sementara buat `_matching_debate_artifact_exists` selalu True (arm-agnostic).

    HANYA kopling debate yang dilepas; eligibility & scoring lain tetap produksi.
    """
    original = ev._matching_debate_artifact_exists
    ev._matching_debate_artifact_exists = lambda *_a, **_k: True
    try:
        yield
    finally:
        ev._matching_debate_artifact_exists = original


def evaluate_arm(
    ledger: Path,
    *,
    today: date | None = None,
    horizon: int = ev.DEFAULT_HORIZON_TRADING_DAYS,
    entry_check: bool = False,
    price_fetcher: Callable | None = None,
) -> dict:
    """Skor satu ledger arm (read-only) dan kembalikan ringkasan realized."""
    if not ledger.exists():
        return {
            "ledger": str(ledger),
            "total": 0,
            "eligible": 0,
            "closed": 0,
            "wins": 0,
            "losses": 0,
            "flat": 0,
            "open": 0,
            "win_rate": None,
            "avg_pnl_pct": None,
        }

    with _debate_gate_neutralized():
        summary = ev.evaluate_memory(
            memory_path=ledger,
            write=False,
            today=today,
            horizon_trading_days=horizon,
            entry_check=entry_check,
            price_fetcher=price_fetcher,
        )

    updated = [
        d.updated_record
        for d in summary.details
        if d.status == "updated" and d.updated_record is not None
    ]
    wins = sum(1 for r in updated if r.outcome == "win")
    losses = sum(1 for r in updated if r.outcome == "loss")
    flat = sum(1 for r in updated if r.outcome == "timeout_flat")
    closed = wins + losses + flat
    pnls = [r.pnl_pct for r in updated if r.pnl_pct is not None]
    decided = wins + losses
    return {
        "ledger": str(ledger),
        "total": summary.total_records,
        "eligible": summary.eligible_records,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        # eligible yg BELUM tertutup (too_early / no_price_data / insufficient_horizon).
        "open": max(summary.eligible_records - closed, 0),
        "win_rate": (wins / decided) if decided else None,
        "avg_pnl_pct": (sum(pnls) / len(pnls)) if pnls else None,
    }


def evaluate_all(
    dirroot: Path,
    *,
    today: date | None = None,
    horizon: int = ev.DEFAULT_HORIZON_TRADING_DAYS,
    entry_check: bool = False,
    price_fetcher: Callable | None = None,
) -> dict[str, dict]:
    """Skor ketiga arm di `dirroot`; kembalikan {arm: ringkasan}."""
    return {
        arm: evaluate_arm(
            ledger_path(dirroot, arm),
            today=today,
            horizon=horizon,
            entry_check=entry_check,
            price_fetcher=price_fetcher,
        )
        for arm in ARMS
    }


def format_report(report: dict[str, dict]) -> str:
    lines = [
        "=== VONIS P1.2 — realized outcome per arm (forward ablation) ===",
        f"{'arm':<14}{'elig':>6}{'closed':>8}{'wins':>6}{'loss':>6}"
        f"{'flat':>6}{'open':>6}{'win%':>8}{'avgPnL%':>9}",
        "-" * 69,
    ]
    for arm in ARMS:
        r = report[arm]
        wr = f"{r['win_rate'] * 100:.1f}" if r["win_rate"] is not None else "n/a"
        ap = f"{r['avg_pnl_pct']:.2f}" if r["avg_pnl_pct"] is not None else "n/a"
        lines.append(
            f"{arm:<14}{r['eligible']:>6}{r['closed']:>8}{r['wins']:>6}"
            f"{r['losses']:>6}{r['flat']:>6}{r['open']:>6}{wr:>8}{ap:>9}"
        )
    lines.append("-" * 69)
    lines.append(
        "Catatan: arm dgn closed=0 -> belum decidable (horizon belum tutup / 0 BUY). "
        "Vonis edge butuh closed cukup di TIAP arm; Arm C sering 0 BUY di rezim DEFENSIVE."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fase C: laporan komparatif realized 3-arm (arm-agnostic)."
    )
    parser.add_argument(
        "--dir", default=str(FORWARD_DIR), help="Root berisi <arm>/backtest/*.jsonl"
    )
    parser.add_argument(
        "--horizon", type=int, default=ev.DEFAULT_HORIZON_TRADING_DAYS, help="Horizon hari dagang."
    )
    parser.add_argument(
        "--entry-check",
        action="store_true",
        help="Hitung entry hanya bila harga menyentuh limit (lebih setia utk Arm B/C).",
    )
    parser.add_argument("--today", default=None, help="Tanggal evaluasi YYYY-MM-DD (opsional).")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else None
    report = evaluate_all(
        Path(args.dir), today=today, horizon=args.horizon, entry_check=args.entry_check
    )
    print(format_report(report))

    out = Path(args.dir) / "forward_eval_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport JSON -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
