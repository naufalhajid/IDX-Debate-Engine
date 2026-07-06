"""
diagnostic_momentum_floor_bypass.py — V0.1: apakah kandidat momentum-kuat/
fundamental-lemah bahkan pernah sampai ke debate chamber?

Konteks (docs/research/profit_over_quality_philosophy_2026-07-06.md): CIO
judge sudah punya jalur "Momentum Play" (FAIL fundamental + PASS teknikal +
volume breakout -> BUY 50% size, schemas/debate.py CIOVerdict.momentum_play)
tapi nol kejadian historis. Riset menduga penyebabnya BUKAN cuma konflik
risk_governor vs momentum_play (risk_overvalued dihitung independen dari
momentum_play, schemas/debate.py:364-373), tapi gerbang yang jauh lebih awal:
skor komposit quant_filter 85% berbobot fundamental (valuation 48% +
profitability 37% vs momentum_rsi 8% + price_momentum 7%, momentum_vol=0),
dengan score_floor (default 35) yang mengeluarkan kandidat dari top_n SEBELUM
sempat sampai ke fundamental_scout/CIO.

Script ini TIDAK mengubah kode produksi — murni override parameter cfg
(score_floor, top_n) dan monkeypatch runtime dalam proses ini saja, sama
seperti scripts/diagnostic_gate_reopen.py. Semua tulisan pipeline dialihkan
ke output/diagnostics/momentum_floor_bypass_<ts>/.

Phase A (default — deterministik, tanpa LLM):
  Jalankan run_pipeline() dengan score_floor_* di-override ke -999 dan
  top_n dibesarkan supaya SELURUH universe yang lolos filter teknikal
  ikut terbawa (bukan cuma top 10 by Composite Score). Dari situ, saring
  kandidat yang (a) Composite Score < floor produksi (35) — artinya
  SEHARUSNYA tersingkir — tapi (b) vol_surge_ratio & price momentum kuat.

Phase B (--debate — memanggil LLM, berbayar, default 1 ticker):
  Ambil kandidat Phase A ber-momentum terkuat, jalankan lewat pipeline
  penuh (scout -> debate -> CIO -> risk_governor) dengan regime dioverride
  ke NORMAL/SIDEWAYS (mengisolasi variabel yang diuji dari confound regime
  defensive). Baca verdict.momentum_play, verdict.risk_overvalued, dan
  governor.reason_codes untuk memastikan apakah momentum_play benar-benar
  tereksekusi ATAU risk_governor tetap block via "overvalued".

Pakai:
  uv run python scripts/diagnostic_momentum_floor_bypass.py
  uv run python scripts/diagnostic_momentum_floor_bypass.py --debate
  uv run python scripts/diagnostic_momentum_floor_bypass.py --debate --top-b 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Floor produksi aktual (core/quant_filter/config.py) — dipakai di sini hanya
# untuk MENANDAI mana kandidat yang seharusnya tersingkir, bukan diterapkan.
PRODUCTION_FLOOR_NORMAL = 35
MIN_VOL_SURGE = 1.5  # sinkron dengan ambang volume breakout CIO (cio_judge.txt STEP 3)


# ── Phase A — sweep screener dengan floor dibypass (tanpa LLM) ───────────────


def phase_a_floor_bypass_sweep(diag_dir: Path) -> pd.DataFrame:
    from core.quant_filter.config import CONFIG, _find_latest_xlsx
    from core.quant_filter.pipeline import run_pipeline

    cfg = dict(CONFIG)
    # Resolve the real scraped-data xlsx from the production output/ folder
    # BEFORE redirecting output_dir, otherwise auto-detect looks for it inside
    # the fresh (empty) diagnostics folder and fails.
    if not cfg.get("input_file"):
        cfg["input_file"] = _find_latest_xlsx(cfg.get("output_dir", "output"))
    cfg["output_dir"] = str(diag_dir)
    cfg["top_n"] = 500  # efektif "tanpa cap" -- ambil seluruh universe yang lolos filter teknikal
    cfg["score_floor_normal_regime"] = -999.0
    cfg["score_floor_high_regime"] = -999.0
    cfg["score_floor_defensive_regime"] = -999.0
    df = run_pipeline(cfg)
    return df if df is not None else pd.DataFrame()


def find_momentum_weak_fundamental(df: pd.DataFrame, top_k: int = 10) -> pd.DataFrame:
    if df.empty:
        return df
    required = {"Composite Score", "vol_surge_ratio", "price_return_1m"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"Kolom hilang dari final_df: {missing}")
    candidates = df[
        (df["Composite Score"] < PRODUCTION_FLOOR_NORMAL)
        & (df["vol_surge_ratio"] >= MIN_VOL_SURGE)
        & (df["price_return_1m"] > 0)
    ].copy()
    return candidates.sort_values("vol_surge_ratio", ascending=False).head(top_k)


def render_phase_a(all_df: pd.DataFrame, candidates: pd.DataFrame) -> str:
    lines = [
        f"Universe setelah filter teknikal (floor dibypass): {len(all_df)} ticker",
        f"Kandidat momentum-kuat/fundamental-lemah (Composite Score < "
        f"{PRODUCTION_FLOOR_NORMAL}, vol_surge_ratio >= {MIN_VOL_SURGE}, "
        f"return 1m > 0): {len(candidates)}",
        "",
    ]
    if candidates.empty:
        lines.append("(tidak ada kandidat yang cocok profil ini di tape saat ini)")
        return "\n".join(lines)
    header = (
        f"{'TICKER':<7} {'COMP.SCORE':>10} {'VOL_SURGE':>9} {'RET_1M%':>8} "
        f"{'PIOTROSKI':>9} {'ALTMAN_Z':>9} {'ROE%':>6}"
    )
    lines += [header, "-" * len(header)]
    for _, row in candidates.iterrows():
        lines.append(
            f"{row['Ticker']:<7} {row['Composite Score']:>10.1f} "
            f"{row['vol_surge_ratio']:>9.2f} {row['price_return_1m']:>8.2f} "
            f"{row.get('Piotroski F-Score', 0):>9} {row.get('Altman Z-Score', 0):>9.2f} "
            f"{row.get('ROE (TTM)', 0):>6.1f}"
        )
    return "\n".join(lines)


# ── Phase B — pipeline penuh, regime dioverride (LLM) ────────────────────────


def _apply_diagnostic_patches(diag_dir: Path) -> None:
    """Alihkan semua tulisan ke diag_dir + override regime ke NORMAL/SIDEWAYS.

    Mengisolasi variabel yang diuji (screener floor + CIO momentum_play vs
    risk_governor overvalued) dari confound regime defensive -- pola identik
    dengan scripts/diagnostic_gate_reopen.py::_apply_diagnostic_patches.
    Harus dipanggil SEBELUM chamber dibuat (graph LangGraph mengikat
    regime_gate_node dari namespace services.debate_chamber saat build).
    """
    import core.orchestrator.legacy as legacy
    import services.debate_chamber as dc
    from core.backtest_memory import DEFAULT_MEMORY
    from core.idx_market_params import REGIME_RULES
    from core.regime import RegimeSnapshot

    diag_dir.mkdir(parents=True, exist_ok=True)
    legacy.configure_output_dir(diag_dir)
    legacy._WATCHLIST_LOG_PATH = diag_dir / "watchlist_log.jsonl"
    DEFAULT_MEMORY.path = diag_dir / "trade_ledger.jsonl"
    legacy.evaluate_memory = lambda write=True: SimpleNamespace(updated_records=0)

    async def fake_detect_market_regime() -> RegimeSnapshot:
        return RegimeSnapshot(
            regime="NORMAL",
            volatility_regime="NORMAL",
            volatility=0.012,
            weekly_return=0.01,
            latest_close=7200.0,
            ma20=7150.0,
            ma50=7100.0,
            ma200=7000.0,
            defensive_triggered=False,
            reasons=["diagnostic_momentum_floor_bypass_override"],
        )

    legacy.detect_market_regime = fake_detect_market_regime

    async def fake_regime_gate_node(state) -> dict:
        return {
            "regime": {
                "label": "SIDEWAYS",
                "confidence": 0.99,
                "probabilities": {"SIDEWAYS": 0.99},
                "msci_override": False,
                "training_days": 0,
                "detected_at": datetime.now().isoformat(),
                "notes": "diagnostic_momentum_floor_bypass override — bukan deteksi nyata",
            },
            "trading_params": dict(REGIME_RULES["SIDEWAYS"]),
            "should_trade": True,
        }

    dc.regime_gate_node = fake_regime_gate_node


async def phase_b_pipeline(tickers: list[str], diag_dir: Path) -> list[dict]:
    import core.orchestrator.legacy as legacy

    _apply_diagnostic_patches(diag_dir)
    await legacy.main(
        tickers=tickers,
        output_dir=diag_dir,
        mode="multi",
        user_config={
            "total_capital": 100_000_000.0,
            "max_loss_pct": 0.02,
            "max_positions": 5,
        },
        raise_on_error=True,
    )
    results_path = diag_dir / "full_batch_results.json"
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("results") or payload.get("data") or []
    return payload if isinstance(payload, list) else []


def summarize_phase_b(results: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        verdict = result.get("verdict") or {}
        governor = result.get("risk_governor") or {}
        rows.append(
            {
                "ticker": result.get("ticker"),
                "rating": str(verdict.get("rating") or "").upper() or "?",
                "confidence": verdict.get("confidence"),
                "momentum_play": bool(verdict.get("momentum_play", False)),
                "risk_overvalued": verdict.get("risk_overvalued"),
                "risk_reward_ratio": verdict.get("risk_reward_ratio"),
                "verdict_reason_codes": verdict.get("reason_codes") or [],
                "governor_status": governor.get("status"),
                "governor_reason_codes": governor.get("reason_codes") or [],
            }
        )
    return rows


def render_phase_b(rows: list[dict]) -> str:
    header = (
        f"{'TICKER':<7} {'RATING':<11} {'CONF':>5} {'MOM_PLAY':>8} {'OVERVAL':>7} "
        f"{'R/R':>6} {'GOVERNOR':<28} REASON_CODES(verdict)"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        conf = row.get("confidence")
        conf_txt = f"{conf:.2f}" if isinstance(conf, (int, float)) else "n/a"
        rr = row.get("risk_reward_ratio")
        rr_txt = f"{rr:.2f}" if isinstance(rr, (int, float)) else "n/a"
        governor = row.get("governor_status") or "-"
        if row.get("governor_reason_codes"):
            governor = f"{governor}:{','.join(row['governor_reason_codes'][:2])}"
        codes = ",".join(row.get("verdict_reason_codes") or []) or "-"
        lines.append(
            f"{row['ticker']:<7} {row['rating']:<11} {conf_txt:>5} "
            f"{str(row['momentum_play']):>8} {str(row['risk_overvalued']):>7} "
            f"{rr_txt:>6} {governor:<28} {codes}"
        )
    return "\n".join(lines)


# ── Report ────────────────────────────────────────────────────────────────────


def write_report(
    diag_dir: Path,
    universe_n: int,
    candidates: pd.DataFrame,
    phase_a_table: str,
    phase_b_rows: list[dict] | None,
    phase_b_table: str | None,
) -> Path:
    diag_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Diagnostic Momentum Floor Bypass (V0.1)",
        "",
        f"- Dijalankan: {datetime.now().isoformat(timespec='seconds')}",
        "- Override: score_floor_* -> -999 (Phase A), regime -> NORMAL/SIDEWAYS (Phase B)",
        f"- Semua output dialihkan ke: `{diag_dir}`",
        "",
        "## Phase A — sweep floor-bypass (tanpa LLM)",
        "",
        "```",
        phase_a_table,
        "```",
        "",
    ]
    if phase_b_rows is not None and phase_b_table is not None:
        momentum_fired = any(r.get("momentum_play") for r in phase_b_rows)
        blocked_despite_momentum = any(
            r.get("momentum_play")
            and r.get("governor_status") in ("reject", "REJECT")
            and any("overval" in c.lower() for c in r.get("governor_reason_codes") or [])
            for r in phase_b_rows
        )
        if momentum_fired and blocked_despite_momentum:
            verdict_line = (
                "**Konflik terkonfirmasi secara empiris**: CIO menetapkan "
                "momentum_play=true, tapi risk_governor tetap menolak via "
                "reason code overvalued -- risk_governor tidak mengecualikan "
                "momentum_play seperti temuan riset."
            )
        elif momentum_fired:
            verdict_line = (
                "**momentum_play=true berhasil tereksekusi** dan tidak diblokir "
                "risk_governor pada kandidat ini -- konflik teoretis tidak "
                "termanifestasi di kasus ini secara spesifik."
            )
        else:
            verdict_line = (
                "**momentum_play tidak pernah bernilai true** pada kandidat ini -- "
                "kemungkinan fundamental_scout tidak FAIL, atau volume breakout "
                "tidak terkonfirmasi ulang oleh technical scout debate_chamber "
                "(vol_surge_ratio/return_5d_pct dihitung ulang dari OHLCV live, "
                "bisa berbeda dari price_return_1m/vol_surge_ratio quant_filter)."
            )
        lines += [
            "## Phase B — pipeline penuh (debate + governor)",
            "",
            "```",
            phase_b_table,
            "```",
            "",
            verdict_line,
            "",
        ]
    report_path = diag_dir / "DIAGNOSTIC_REPORT.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="V0.1 diagnostic: apakah kandidat momentum-kuat/fundamental-lemah "
        "bahkan sampai ke debate chamber, dan apa yang terjadi kalau dipaksa masuk"
    )
    parser.add_argument(
        "--debate",
        action="store_true",
        help="jalankan Phase B (pipeline penuh, memanggil LLM — berbayar)",
    )
    parser.add_argument(
        "--top-b",
        type=int,
        default=1,
        help="jumlah ticker Phase B, diambil dari kandidat Phase A ber-vol_surge_ratio "
        "tertinggi (default 1 untuk membatasi biaya LLM)",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    diag_dir = Path("output/diagnostics") / f"momentum_floor_bypass_{stamp}"

    print("[Phase A] Sweep screener dengan score_floor dibypass...")
    all_df = phase_a_floor_bypass_sweep(diag_dir)
    candidates = find_momentum_weak_fundamental(all_df)
    phase_a_table = render_phase_a(all_df, candidates)
    print(phase_a_table)

    diag_dir.mkdir(parents=True, exist_ok=True)
    candidates.to_json(
        diag_dir / "phase_a_candidates.json", orient="records", indent=2
    )

    phase_b_rows: list[dict] | None = None
    phase_b_table: str | None = None
    exit_code = 0

    if args.debate:
        if candidates.empty:
            print(
                "[Phase B] DIBATALKAN: tidak ada kandidat momentum-kuat/"
                "fundamental-lemah di tape saat ini — temuan diagnostik itu sendiri."
            )
            exit_code = 2
        else:
            chosen = candidates["Ticker"].head(args.top_b).tolist()
            print(f"[Phase B] Debate penuh (regime dioverride NORMAL/SIDEWAYS): {', '.join(chosen)}")
            results = asyncio.run(phase_b_pipeline(chosen, diag_dir))
            phase_b_rows = summarize_phase_b(results)
            phase_b_table = render_phase_b(phase_b_rows)
            print(phase_b_table)

    report_path = write_report(
        diag_dir, len(all_df), candidates, phase_a_table, phase_b_rows, phase_b_table
    )
    print(f"[Report] {report_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
