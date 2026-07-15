"""
diagnostic_gate_reopen.py — V0.3: bukti konfigurasi hardened masih bisa BUY.

Sejak hardening akhir Juni 2026 pipeline hanya menghasilkan HOLD (regime
DEFENSIVE + HMM BEAR_STRESS/MSCI override memblokir semua entry). Script ini
menjawab satu pertanyaan: kalau market TIDAK crash, apakah konfigurasi yang
sama masih mampu mengeluarkan BUY end-to-end?

Dua sistem regime dioverride ke label non-crash, HANYA di proses ini
(kode produksi tidak diubah — murni monkeypatch runtime):
  - rule-based detect_market_regime()  -> NORMAL  (k_atr envelope, regime params)
  - HMM regime_gate_node               -> SIDEWAYS | BULL (--hmm-label)

Semua tulisan pipeline dialihkan ke output/diagnostics/gate_reopen_<ts>/
sehingga ledger produksi (trade ledger, watchlist_log, TOP_3 report) tidak
tersentuh oleh run diagnostik ini.

Phase A (default — deterministik, tanpa LLM):
  Sweep _compute_trade_envelope per ticker di bawah DEFENSIVE vs NORMAL:
  tabel pass/reject + reason_code + R/R (aktual, atau hipotetis dari
  counterfactual envelope bila ditolak).

Phase B (--debate — memanggil LLM, berbayar):
  Pipeline penuh (debate -> governor -> ranking) pada ticker terpilih dengan
  kedua override aktif. Verdict akhir menjawab pertanyaan diagnostik.

Pakai:
  uv run python scripts/diagnostic_gate_reopen.py
  uv run python scripts/diagnostic_gate_reopen.py --tickers BBCA ICBP
  uv run python scripts/diagnostic_gate_reopen.py --debate
  uv run python scripts/diagnostic_gate_reopen.py --debate --hmm-label BULL
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.ticker import InvalidIDXTicker, normalize_idx_tickers  # noqa: E402

# Pool default: kandidat screener terakhir + blue chip likuid lintas sektor.
DEFAULT_POOL = [
    "BBCA",
    "BBRI",
    "BMRI",
    "TLKM",
    "ASII",
    "ICBP",
    "INDF",
    "ANTM",
    "ACES",
    "UNVR",
]


def _screener_candidate_tickers() -> list[str]:
    """Ticker dari output screener terakhir (kalau ada) — prioritas pool."""
    try:
        import core.orchestrator.legacy as legacy

        candidates = legacy._load_quant_candidates(legacy.JSON_PATH)
        return [str(c["Ticker"]).upper() for c in candidates if c.get("Ticker")]
    except Exception:
        return []


def _build_pool(explicit: list[str] | None) -> list[str]:
    if explicit:
        return normalize_idx_tickers(explicit)
    pool: list[str] = []
    for ticker in _screener_candidate_tickers() + DEFAULT_POOL:
        if ticker not in pool:
            pool.append(ticker)
    return normalize_idx_tickers(pool[:12])


# ── Phase A — sweep envelope deterministik (tanpa LLM) ───────────────────────


def phase_a_envelope_sweep(tickers: list[str]) -> list[dict]:
    import yfinance as yf

    from services.debate_chamber import DebateChamber

    chamber = DebateChamber.__new__(DebateChamber)
    rows: list[dict] = []
    for ticker in tickers:
        try:
            df = yf.download(
                f"{ticker}.JK",
                period="1y",
                progress=False,
                auto_adjust=True,
                timeout=20,
            )
        except Exception as exc:
            rows.append({"ticker": ticker, "error": f"download gagal: {exc}"})
            continue
        tech = DebateChamber._compute_technical_indicators(df)
        if not tech:
            rows.append({"ticker": ticker, "error": "OHLCV < 20 bar"})
            continue
        row: dict = {
            "ticker": ticker,
            "price": tech.get("current_price"),
            "rsi14": tech.get("rsi14"),
            "return_5d_pct": tech.get("return_5d_pct"),
        }
        for regime in ("DEFENSIVE", "NORMAL"):
            envelope = chamber._compute_trade_envelope(
                tech["current_price"], 0.0, {**tech, "regime": regime}
            )
            if envelope.get("rejected"):
                hypo = envelope.get("hypothetical_envelope") or {}
                row[regime.lower()] = {
                    "status": "REJECT",
                    "reason_code": envelope.get("reason_code"),
                    "rr": hypo.get("risk_reward_ratio"),
                }
            else:
                row[regime.lower()] = {
                    "status": "PASS",
                    "reason_code": None,
                    "rr": envelope.get("risk_reward_ratio"),
                }
        rows.append(row)
    return rows


def render_phase_a(rows: list[dict]) -> str:
    header = (
        f"{'TICKER':<7} {'PRICE':>8} {'RSI':>5} {'RET5D%':>7} "
        f"| {'DEFENSIVE':<32} | {'NORMAL':<32}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        if row.get("error"):
            lines.append(f"{row['ticker']:<7} ERROR: {row['error']}")
            continue

        def _cell(info: dict) -> str:
            rr = info.get("rr")
            rr_txt = f"R/R {rr:.2f}" if isinstance(rr, (int, float)) else "R/R n/a"
            code = info.get("reason_code") or "-"
            return f"{info['status']:<6} {rr_txt:<9} {code}"

        ret5 = row.get("return_5d_pct")
        ret5_txt = f"{ret5:+.1f}" if isinstance(ret5, (int, float)) else "n/a"
        lines.append(
            f"{row['ticker']:<7} {row['price']:>8,.0f} {row['rsi14']:>5.1f} "
            f"{ret5_txt:>7} | {_cell(row['defensive']):<32} | {_cell(row['normal']):<32}"
        )
    return "\n".join(lines)


# ── Phase B — pipeline penuh dengan regime dioverride (LLM) ──────────────────


def _apply_diagnostic_patches(hmm_label: str, diag_dir: Path) -> None:
    """Override kedua sistem regime + alihkan semua tulisan ke diag_dir.

    Harus dipanggil SEBELUM chamber dibuat: graph LangGraph mengikat
    regime_gate_node dari namespace services.debate_chamber saat build.
    """
    import core.orchestrator.legacy as legacy
    import services.debate_chamber as dc
    from core.backtest_memory import DEFAULT_MEMORY
    from core.idx_market_params import REGIME_RULES
    from core.regime import RegimeSnapshot

    diag_dir.mkdir(parents=True, exist_ok=True)
    source_candidates = Path("output/top10_candidates.json")
    if source_candidates.exists():
        shutil.copy(source_candidates, diag_dir / "top10_candidates.json")

    # Semua report/ledger pipeline menulis ke folder diagnostik.
    legacy.configure_output_dir(diag_dir)
    legacy._WATCHLIST_LOG_PATH = diag_dir / "watchlist_log.jsonl"
    DEFAULT_MEMORY.path = diag_dir / "trade_ledger.jsonl"
    legacy.evaluate_memory = lambda write=True: SimpleNamespace(updated_records=0)

    async def fake_detect_market_regime() -> RegimeSnapshot:
        # Snapshot sintetis konsisten dengan pasar NORMAL: vol 1.2%/hari,
        # close di atas seluruh MA (defensive tidak ter-trigger).
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
            reasons=["diagnostic_gate_reopen_override"],
        )

    legacy.detect_market_regime = fake_detect_market_regime

    async def fake_regime_gate_node(state) -> dict:
        return {
            "regime": {
                "label": hmm_label,
                "confidence": 0.99,
                "probabilities": {hmm_label: 0.99},
                "msci_override": False,
                "training_days": 0,
                "detected_at": datetime.now().isoformat(),
                "notes": "diagnostic_gate_reopen override — bukan deteksi nyata",
            },
            "trading_params": dict(REGIME_RULES[hmm_label]),
            "should_trade": True,
        }

    dc.regime_gate_node = fake_regime_gate_node


async def phase_b_pipeline(
    tickers: list[str], hmm_label: str, diag_dir: Path
) -> list[dict]:
    import core.orchestrator.legacy as legacy

    _apply_diagnostic_patches(hmm_label, diag_dir)
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


def summarize_phase_b(results: list[dict]) -> tuple[list[dict], int]:
    rows: list[dict] = []
    buy_count = 0
    for result in results:
        if not isinstance(result, dict):
            continue
        verdict = result.get("verdict") or {}
        governor = result.get("risk_governor") or {}
        market_regime = (result.get("market_regime") or {}).get("regime")
        hmm_regime = (result.get("regime") or {}).get("label")
        rating = str(verdict.get("rating") or "").upper()
        if rating in ("BUY", "STRONG_BUY"):
            buy_count += 1
        rows.append(
            {
                "ticker": result.get("ticker"),
                "rating": rating or "?",
                "confidence": verdict.get("confidence"),
                "risk_reward_ratio": verdict.get("risk_reward_ratio"),
                "reason_codes": verdict.get("reason_codes") or [],
                "governor_status": governor.get("status"),
                "governor_codes": governor.get("reason_codes") or [],
                "market_regime": market_regime,
                "hmm_regime": hmm_regime,
            }
        )
    return rows, buy_count


def render_phase_b(rows: list[dict]) -> str:
    header = (
        f"{'TICKER':<7} {'RATING':<11} {'CONF':>5} {'R/R':>6} "
        f"{'GOVERNOR':<18} {'REGIME(rule/HMM)':<20} REASON_CODES"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        conf = row.get("confidence")
        conf_txt = f"{conf:.2f}" if isinstance(conf, (int, float)) else "n/a"
        rr = row.get("risk_reward_ratio")
        rr_txt = f"{rr:.2f}" if isinstance(rr, (int, float)) else "n/a"
        governor = row.get("governor_status") or "-"
        if row.get("governor_codes"):
            governor = f"{governor}:{','.join(row['governor_codes'][:2])}"
        regimes = f"{row.get('market_regime') or '?'}/{row.get('hmm_regime') or '?'}"
        codes = ",".join(row.get("reason_codes") or []) or "-"
        lines.append(
            f"{row['ticker']:<7} {row['rating']:<11} {conf_txt:>5} {rr_txt:>6} "
            f"{governor:<18} {regimes:<20} {codes}"
        )
    return "\n".join(lines)


# ── Report ────────────────────────────────────────────────────────────────────


def write_report(
    diag_dir: Path,
    hmm_label: str,
    phase_a_rows: list[dict],
    phase_a_table: str,
    phase_b_rows: list[dict] | None,
    phase_b_table: str | None,
    buy_count: int | None,
) -> Path:
    diag_dir.mkdir(parents=True, exist_ok=True)
    normal_pass = [
        r["ticker"]
        for r in phase_a_rows
        if not r.get("error") and r.get("normal", {}).get("status") == "PASS"
    ]
    defensive_pass = [
        r["ticker"]
        for r in phase_a_rows
        if not r.get("error") and r.get("defensive", {}).get("status") == "PASS"
    ]
    lines = [
        "# Diagnostic Gate Re-Open (V0.3)",
        "",
        f"- Dijalankan: {datetime.now().isoformat(timespec='seconds')}",
        f"- Override: rule-based=NORMAL, HMM={hmm_label} (hanya proses ini)",
        f"- Semua output dialihkan ke: `{diag_dir}`",
        "",
        "## Phase A — envelope sweep deterministik (DEFENSIVE vs NORMAL)",
        "",
        "```",
        phase_a_table,
        "```",
        "",
        f"- PASS di DEFENSIVE: {len(defensive_pass)} ({', '.join(defensive_pass) or '-'})",
        f"- PASS di NORMAL   : {len(normal_pass)} ({', '.join(normal_pass) or '-'})",
        "",
    ]
    if phase_b_rows is not None and phase_b_table is not None:
        verdict_line = (
            f"**{buy_count} BUY dihasilkan** — gate BISA terbuka kembali."
            if buy_count
            else "**0 BUY** — gate tetap tertutup meski regime dioverride."
        )
        lines += [
            f"## Phase B — pipeline penuh (debate + governor), HMM={hmm_label}",
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
        description="V0.3 diagnostic: bisa tidaknya konfigurasi sekarang menghasilkan BUY"
    )
    parser.add_argument(
        "--tickers", nargs="*", default=None, help="pool ticker eksplisit"
    )
    parser.add_argument(
        "--debate",
        action="store_true",
        help="jalankan Phase B (pipeline penuh, memanggil LLM — berbayar)",
    )
    parser.add_argument("--hmm-label", choices=["SIDEWAYS", "BULL"], default="SIDEWAYS")
    parser.add_argument(
        "--top",
        type=int,
        default=2,
        help="jumlah ticker Phase B (diambil dari hasil PASS Phase A ber-R/R tertinggi)",
    )
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    diag_dir = Path("output/diagnostics") / f"gate_reopen_{stamp}"

    try:
        pool = _build_pool(args.tickers)
    except InvalidIDXTicker as exc:
        parser.error(str(exc))
    print(f"[Phase A] Envelope sweep {len(pool)} ticker: {', '.join(pool)}")
    phase_a_rows = phase_a_envelope_sweep(pool)
    phase_a_table = render_phase_a(phase_a_rows)
    print(phase_a_table)

    diag_dir.mkdir(parents=True, exist_ok=True)
    (diag_dir / "phase_a_envelope_sweep.json").write_text(
        json.dumps(phase_a_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    phase_b_rows: list[dict] | None = None
    phase_b_table: str | None = None
    buy_count: int | None = None
    exit_code = 0

    if args.debate:
        candidates = [
            r
            for r in phase_a_rows
            if not r.get("error") and r.get("normal", {}).get("status") == "PASS"
        ]
        candidates.sort(key=lambda r: r["normal"].get("rr") or 0.0, reverse=True)
        chosen = (
            normalize_idx_tickers(args.tickers)
            if args.tickers
            else [r["ticker"] for r in candidates[: args.top]]
        )
        if not chosen:
            print(
                "[Phase B] DIBATALKAN: tidak ada ticker yang lolos envelope di "
                "regime NORMAL — temuan diagnostik itu sendiri."
            )
            exit_code = 2
        else:
            print(f"[Phase B] Debate penuh (HMM={args.hmm_label}): {', '.join(chosen)}")
            results = asyncio.run(phase_b_pipeline(chosen, args.hmm_label, diag_dir))
            phase_b_rows, buy_count = summarize_phase_b(results)
            phase_b_table = render_phase_b(phase_b_rows)
            print(phase_b_table)
            sane = all(
                (r.get("market_regime") or "NORMAL") == "NORMAL" for r in phase_b_rows
            )
            if not sane:
                print(
                    "[Phase B] PERINGATAN: market_regime bukan NORMAL — override gagal?"
                )
            print(
                f"[Phase B] Hasil: {buy_count} BUY dari {len(phase_b_rows)} ticker "
                f"({'GATE TERBUKA' if buy_count else 'GATE TETAP TERTUTUP'})"
            )
            if not buy_count:
                exit_code = 1

    report_path = write_report(
        diag_dir,
        args.hmm_label,
        phase_a_rows,
        phase_a_table,
        phase_b_rows,
        phase_b_table,
        buy_count,
    )
    print(f"[Report] {report_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
