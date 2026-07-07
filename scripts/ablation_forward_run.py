"""scripts/ablation_forward_run.py — Forward-outcome 3-arm ablation harness.

Menjawab (dengan bukti REALIZED, bukan struktural) pertanyaan P1.2:
"Apakah trade yang dipilih lapis debate/sentiment (mahal) lebih untung daripada
arm yang jauh lebih murah?" Tiga arm pada universe + tanggal yang SAMA, tiap arm
menulis BUY-nya ke ledger sandbox TERPISAH; evaluator forward
(`core.backtest_outcome_evaluator`) menilai outcome identik saat horizon tutup.

    Arm A — quant_only    : screener deterministik (GRATIS). Tak ada target di
                            output screener, jadi envelope R/R 2:1 (+10% cap)
                            dilekatkan di sini — sama geometri `historical_backtest.py`.
    Arm B — single_gated  : verdict `single_agent_analyzer` DILEWATKAN gate
                            `risk_governor` (services/single_agent_gated.py). Menguji
                            langsung hipotesis V2.1: "single+gate ≈ full-debate di ~1/10 biaya".
    Arm C — full_debate   : verdict CIO pipeline penuh (scout+sentiment+debate+CIO).
                            Verdict INI sudah post-risk_governor (governor jalan di
                            pipeline), jadi di-route apa adanya — TIDAK di-gate ulang.

Asimetri gate itu SENGAJA dan justru inti perbandingan: C sudah kena gate di
pipeline; B raw single-agent yang selama ini MELEWATI gate (V2.1 baris 72), jadi
gate diterapkan di sini; A screen mentah yang dilekati envelope.

Dua mode:
  * record  — deterministik, NOL LLM. Baca artefak on-disk (single_agent/*.json,
              debates/*_debate.json, top10_candidates.json) → kipas ke 3 ledger.
              Dipakai untuk verifikasi routing dari artefak lama & untuk fan-out
              pasca run live.
  * live    — MAHAL (debate ~180 call/run). Sandbox ke output/ablation_forward/,
              jalankan `legacy.main(mode="compare")` untuk memproduksi artefak segar,
              lalu record. Dijadwalkan MINGGUAN (Fase B) — JANGAN jalan iseng.

Guardrail: ledger sandbox (output/ablation_forward/<arm>/backtest/*.jsonl) WAJIB
terpisah dari track record organik (output/backtest/*.jsonl) — pola stub
evaluate_memory + redirect DEFAULT_MEMORY sudah ada di scripts/ablation_v2_1_run.py.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from core.backtest_memory import BacktestMemory, TradeOutcome

# ---------------------------------------------------------------------------
# Konstanta harness
# ---------------------------------------------------------------------------

FORWARD_DIR = Path("output/ablation_forward")
ARMS = ("quant_only", "single_gated", "full_debate")

# Rating yang di-SKIP total (tak masuk ledger MAUPUN watchlist).
_SKIP_RATINGS = {"AVOID", "SELL", "INSUFFICIENT_DATA", ""}

# Envelope deterministik Arm A (samakan dgn historical_backtest.py geometry).
_ARM_A_RR = 2.0        # target = entry + RR*(entry-stop)
_ARM_A_TARGET_CAP = 0.10  # target di-cap +10% dari entry


def ledger_path(dirroot: Path, arm: str) -> Path:
    return dirroot / arm / "backtest" / "backtest_memory.jsonl"


def watchlist_path(dirroot: Path, arm: str) -> Path:
    return dirroot / arm / "backtest" / "watchlist_log.jsonl"


# ---------------------------------------------------------------------------
# Parsing & helper writer bersama
# ---------------------------------------------------------------------------

def parse_entry_low(entry_range: object) -> float | None:
    """Ambil batas BAWAH dari string entry ("326 - 332" -> 326.0). None jika gagal."""
    if entry_range is None:
        return None
    text = str(entry_range).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _as_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _today_iso() -> str:
    """Tanggal ISO hari ini (UTC) — fallback entry_date yang WAJIB YYYY-MM-DD.

    JANGAN pernah iris tanggal dari run_id ("20260707_101112"[:10] = "20260707_1",
    bukan ISO) — `backtest_outcome_evaluator._parse_date` akan gagal & record di-skip
    diam-diam. Format rumah = `.date().isoformat()` (lihat _record_backtest_memory).
    """
    return datetime.now(timezone.utc).date().isoformat()


def _entry_date_from(generated_at: object, fallback: str) -> str:
    """YYYY-MM-DD dari ISO `generated_at`; fallback (ISO) bila absen."""
    if generated_at:
        text = str(generated_at)
        if len(text) >= 10:
            return text[:10]
    return fallback


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def _open_record_exists(
    memory: BacktestMemory, ticker: str, entry: float, target: float, stop: float
) -> bool:
    """Dedup: sudah ada record OPEN identik (ticker+entry+target+stop)?"""
    for record in memory.all_records():
        if (
            record.outcome == "open"
            and record.ticker == ticker
            and record.entry_price == entry
            and record.target_price == target
            and record.stop_loss == stop
        ):
            return True
    return False


def record_trade(
    *,
    dirroot: Path,
    arm: str,
    run_id: str,
    ticker: str,
    rating: str,
    entry: float | None,
    target: float | None,
    stop: float | None,
    confidence: float | None,
    entry_date: str,
    reason_codes: list[str] | None = None,
    notes: str = "",
) -> str:
    """Writer BERSAMA untuk ketiga arm. Kembalikan aksi: recorded|watchlist|skipped.

    Routing meniru core/orchestrator/legacy._record_backtest_memory:
      AVOID/SELL/INSUFFICIENT_DATA -> skip; HOLD -> watchlist (counterfactual);
      BUY-family dgn envelope valid -> ledger (dedup). Emit TradeOutcome(outcome="open")
      langsung — skema `core.backtest_memory` — supaya evaluator forward menilai
      ketiga arm dgn cara IDENTIK. Sengaja TIDAK lewat _record_backtest_memory: fungsi
      itu butuh avg_volume_20d dari field debate-result (raw_data/metadata/technicals)
      yang tak dibawa verdict single-agent/quant -> akan di-skip diam-diam.
    """
    ticker = str(ticker or "UNKNOWN").upper()
    rating = str(rating or "").strip().upper()

    if rating in _SKIP_RATINGS:
        return "skipped"

    if rating == "HOLD":
        _append_jsonl(
            watchlist_path(dirroot, arm),
            {
                "run_id": run_id,
                "ticker": ticker,
                "rating": "HOLD",
                "entry_date": entry_date,
                "confidence": confidence,
                "entry_price": entry,
                "target_price": target,
                "stop_loss": stop,
                "reason_codes": list(reason_codes or []),
            },
        )
        return "watchlist"

    # BUY-family: butuh envelope lengkap & waras.
    if entry is None or target is None or stop is None:
        return "skipped"
    if not (stop < entry < target):
        return "skipped"

    memory = BacktestMemory(ledger_path(dirroot, arm))
    if _open_record_exists(memory, ticker, entry, target, stop):
        return "skipped"

    memory.record(
        TradeOutcome(
            run_id=run_id,
            ticker=ticker,
            verdict_rating=rating,
            entry_price=entry,
            exit_price=None,
            target_price=target,
            stop_loss=stop,
            entry_date=entry_date,
            exit_date=None,
            outcome="open",
            pnl_pct=None,
            hit_target=None,
            hit_stop=None,
            confidence_at_entry=confidence,
            notes=notes or f"arm={arm}",
        )
    )
    return "recorded"


# ---------------------------------------------------------------------------
# Arm B — single-agent + gate risk_governor
# ---------------------------------------------------------------------------

def record_single_gated(single_dir: Path, dirroot: Path, run_id: str) -> dict[str, int]:
    """Baca single_agent/*.json, terapkan gate, kipas ke ledger single_gated.

    File on-disk = WRAPPER {ticker, run_id, verdict:{...SingleAgentVerdict...}, status}.
    Arm-B BUY = rating BUY DAN gate mengizinkan sizing (is_gated_buy). Selain itu ->
    watchlist dgn reason_codes gate (data counterfactual, seperti debate HOLD).
    """
    from services.single_agent_analyzer import SingleAgentVerdict
    from services.single_agent_gated import gate_single_agent_verdict, is_gated_buy

    tally = {"recorded": 0, "watchlist": 0, "skipped": 0}
    for path in sorted(single_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        verdict_dict = data.get("verdict")
        if not verdict_dict:
            tally["skipped"] += 1
            continue
        verdict = SingleAgentVerdict(**verdict_dict)
        entry_date = _entry_date_from(verdict.generated_at, _today_iso())

        if is_gated_buy(verdict):
            action = record_trade(
                dirroot=dirroot,
                arm="single_gated",
                run_id=run_id,
                ticker=verdict.ticker,
                rating="BUY",
                entry=parse_entry_low(verdict.entry_price_range),
                target=_as_float(verdict.target_price),
                stop=_as_float(verdict.stop_loss),
                confidence=_as_float(verdict.confidence),
                entry_date=entry_date,
                notes="arm=single_gated gate=pass",
            )
        else:
            # Rating non-BUY, atau BUY yang DITOLAK gate -> watchlist + alasan gate.
            decision = gate_single_agent_verdict(verdict)
            action = record_trade(
                dirroot=dirroot,
                arm="single_gated",
                run_id=run_id,
                ticker=verdict.ticker,
                rating="HOLD" if verdict.rating.upper() == "BUY" else verdict.rating,
                entry=parse_entry_low(verdict.entry_price_range),
                target=_as_float(verdict.target_price),
                stop=_as_float(verdict.stop_loss),
                confidence=_as_float(verdict.confidence),
                entry_date=entry_date,
                reason_codes=list(decision.reason_codes),
            )
        tally[action] = tally.get(action, 0) + 1
    return tally


# ---------------------------------------------------------------------------
# Arm C — full debate (verdict sudah post-risk_governor)
# ---------------------------------------------------------------------------

def record_full_debate(debates_dir: Path, dirroot: Path, run_id: str) -> dict[str, int]:
    """Baca debates/*_debate.json, route verdict CIO apa adanya (sudah post-gate)."""
    tally = {"recorded": 0, "watchlist": 0, "skipped": 0}
    for path in sorted(debates_dir.glob("*_debate.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("error"):
            tally["skipped"] += 1
            continue
        verdict = data.get("verdict") or {}
        if not verdict:
            tally["skipped"] += 1
            continue
        action = record_trade(
            dirroot=dirroot,
            arm="full_debate",
            run_id=run_id,
            ticker=data.get("ticker") or verdict.get("ticker") or "UNKNOWN",
            rating=verdict.get("rating") or "",
            entry=parse_entry_low(verdict.get("entry_price_range")),
            target=_as_float(verdict.get("target_price")),
            stop=_as_float(verdict.get("stop_loss")),
            confidence=_as_float(verdict.get("confidence")),
            entry_date=_entry_date_from(verdict.get("generated_at"), _today_iso()),
            reason_codes=list(verdict.get("reason_codes") or []),
        )
        tally[action] = tally.get(action, 0) + 1
    return tally


# ---------------------------------------------------------------------------
# Arm A — quant-only (lekati envelope R/R 2:1)
# ---------------------------------------------------------------------------

def _attach_envelope(current: float | None, stop: float | None) -> float | None:
    """Target deterministik: entry + RR*(entry-stop), di-cap +10% dari entry."""
    if current is None or stop is None or current <= 0 or stop >= current:
        return None
    raw = current + _ARM_A_RR * (current - stop)
    cap = current * (1.0 + _ARM_A_TARGET_CAP)
    return round(min(raw, cap), 2)


def record_quant_only(
    candidates_path: Path, dirroot: Path, run_id: str
) -> dict[str, int]:
    """Baca top10_candidates.json, lekati envelope, kipas ke ledger quant_only.

    Screener adalah SARINGAN (semua yang lolos = BUY-candidate); ia tak memancarkan
    target, jadi envelope R/R 2:1 dilekatkan di sini agar scoreable di sumbu yg sama.
    """
    tally = {"recorded": 0, "watchlist": 0, "skipped": 0}
    if not candidates_path.exists():
        return tally
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    for cand in candidates:
        entry = _as_float(cand.get("Current Price"))
        stop = _as_float(cand.get("Stop Loss Level"))
        target = _attach_envelope(entry, stop)
        action = record_trade(
            dirroot=dirroot,
            arm="quant_only",
            run_id=run_id,
            ticker=cand.get("Ticker") or "UNKNOWN",
            rating="BUY",
            entry=entry,
            target=target,
            stop=stop,
            confidence=None,
            entry_date=_today_iso(),
            notes="arm=quant_only envelope=rr2.0_cap10",
        )
        tally[action] = tally.get(action, 0) + 1
    return tally


# ---------------------------------------------------------------------------
# Orkestrasi record
# ---------------------------------------------------------------------------

def run_record(
    *,
    single_dir: Path,
    debates_dir: Path,
    candidates_path: Path,
    dirroot: Path,
    run_id: str,
    arms: Iterable[str] = ARMS,
) -> dict[str, dict[str, int]]:
    """Kipas artefak on-disk ke ledger per-arm di `dirroot`. Deterministik, nol LLM."""
    arms = set(arms)
    result: dict[str, dict[str, int]] = {}
    if "quant_only" in arms:
        result["quant_only"] = record_quant_only(candidates_path, dirroot, run_id)
    if "single_gated" in arms:
        result["single_gated"] = record_single_gated(single_dir, dirroot, run_id)
    if "full_debate" in arms:
        result["full_debate"] = record_full_debate(debates_dir, dirroot, run_id)
    return result


# ---------------------------------------------------------------------------
# Mode live (MAHAL — Fase B, jangan jalan iseng)
# ---------------------------------------------------------------------------

async def run_live(tickers: list[str] | None = None) -> dict[str, dict[str, int]]:
    """Produksi artefak segar via compare lalu kipas ke 3 ledger forward.

    Sandbox: DEFAULT_MEMORY/watchlist pipeline diarahkan ke scratch throwaway (bukan
    ledger arm — Arm C tetap di-derive dari debates/*_debate.json lewat run_record supaya
    seragam dgn mode record); observation store & OUTPUT_DIR diredirect ke FORWARD_DIR;
    evaluate_memory di-stub agar tak menyentuh track record organik.
    """
    import core.orchestrator.legacy as legacy
    from core.backtest_outcome_evaluator import EvaluationSummary
    from core.observation_store import DEFAULT_STORE

    scratch = FORWARD_DIR / "_raw_pipeline"
    legacy.DEFAULT_MEMORY.path = scratch / "backtest_memory.jsonl"
    legacy._WATCHLIST_LOG_PATH = scratch / "watchlist_log.jsonl"
    DEFAULT_STORE.path = FORWARD_DIR / "observations" / "observations.jsonl"

    def _stub_evaluate_memory(*_a, **_k) -> EvaluationSummary:
        return EvaluationSummary(
            total_records=0,
            eligible_records=0,
            updated_records=0,
            skipped_records=0,
            unchanged_records=0,
            backup_path=None,
            details=[],
        )

    legacy.evaluate_memory = _stub_evaluate_memory
    legacy.configure_output_dir(FORWARD_DIR)

    if tickers is None:
        from ablation_v2_1_run import TICKERS  # universe 25 ticker yang sama

        tickers = TICKERS

    FORWARD_DIR.mkdir(parents=True, exist_ok=True)
    await legacy.main(mode="compare", tickers=tickers, output_dir=FORWARD_DIR)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidates = FORWARD_DIR / "top10_candidates.json"
    if not candidates.exists():
        candidates = Path("output/top10_candidates.json")
    return run_record(
        single_dir=FORWARD_DIR / "single_agent",
        debates_dir=FORWARD_DIR / "debates",
        candidates_path=candidates,
        dirroot=FORWARD_DIR,
        run_id=run_id,
    )


def _print_tally(result: dict[str, dict[str, int]]) -> None:
    print("\n=== Forward ablation — hasil perekaman per-arm ===")
    for arm in ARMS:
        t = result.get(arm)
        if t is None:
            continue
        print(
            f"{arm:<14} recorded={t.get('recorded', 0):>3} "
            f"watchlist={t.get('watchlist', 0):>3} skipped={t.get('skipped', 0):>3}"
        )


if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts importable

    parser = argparse.ArgumentParser(description="Forward-outcome 3-arm ablation harness.")
    parser.add_argument(
        "--live", action="store_true", help="MAHAL: jalankan compare lalu record (Fase B)."
    )
    parser.add_argument(
        "--record-from",
        default=str(FORWARD_DIR),
        help="Dir sumber artefak (single_agent/, debates/) untuk mode record.",
    )
    parser.add_argument(
        "--candidates",
        default="output/top10_candidates.json",
        help="Path top10_candidates.json untuk Arm A.",
    )
    parser.add_argument(
        "--out",
        default=str(FORWARD_DIR),
        help="Dir tujuan ledger per-arm.",
    )
    parser.add_argument(
        "--run-id",
        default=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        help="Run id (default sekarang).",
    )
    args = parser.parse_args()

    if args.live:
        _print_tally(asyncio.run(run_live()))
    else:
        src = Path(args.record_from)
        tally = run_record(
            single_dir=src / "single_agent",
            debates_dir=src / "debates",
            candidates_path=Path(args.candidates),
            dirroot=Path(args.out),
            run_id=args.run_id,
        )
        _print_tally(tally)
