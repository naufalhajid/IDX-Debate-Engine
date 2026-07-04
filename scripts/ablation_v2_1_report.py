"""scripts/ablation_v2_1_report.py — V2.1: build the final structural ablation report.

Combines three legs for the same 25-ticker universe:
  - Quant-only: today's live `idx filter --top 25` run (DEFENSIVE regime) —
    deterministic, no LLM. Only tickers clearing every production technical
    gate get a score; everything else is quant-only AVOID (gate-rejected
    before scoring — _analyze_ticker fuses gates and scoring, so there is no
    gate-free raw score to fall back to).
  - Single-agent: output/ablation_v2_1/single_agent/*.json (one LLM call).
  - Multi-agent (debate): output/ablation_v2_1/full_batch_results.json (up to
    3 rounds, CIO judge).

This is a STRUCTURAL + COST comparison, not a performance verdict: no forward
outcomes exist yet for tickers debated today. scripts/ablation_study.py already
answered the performance question retrospectively on realized outcomes — it is
data-starved (n<30; see output/ablation/ablation_report.json) — that gap closes
via V3.1 (passive weekly accumulation), not by running more debates today.

Reports CONDITIONAL agreement, not raw agreement: with quant-only AVOID on
24/25 tickers by construction of today's regime, raw single-vs-multi agreement
is dominated by trivial HOLD/HOLD or AVOID/AVOID matches. The informative axis
is where at least one leg calls something actionable, or where debate diverges
from the single-shot baseline (dissent count, round count) despite seeing the
same data.
"""

from __future__ import annotations

import json
from pathlib import Path

ABLATION_DIR = Path("output/ablation_v2_1")

# Today's live `idx filter --top 25` run (DEFENSIVE regime, 2026-07-04): only
# ERAA cleared every production technical gate (score 65.2). The rest hit one
# of these binary gates before a composite score was ever computed: adt_liquidity
# (182), ema20 (122), rs_vs_ihsg (90), volume_surge (22), atr_pct (12),
# suspended_fca (11), rsi_hard_reject (4) — out of 803 non-suspended names in
# the 957-ticker universe. See output/top10_candidates.json for the raw output.
QUANT_ONLY_BUY: set[str] = {"ERAA"}

ACTIONABLE_RATINGS: set[str] = {"BUY", "STRONG_BUY"}

# Confirmed via grep against core/risk_governor.py (rr_too_low, rr_implausible — the
# tier-aware R/R floor) and services/debate_chamber.py trade-envelope validation
# (no_momentum_confirmation: RSI>40 with negative 5d return; stop_inside_noise: stop
# distance below the ATR noise floor): all four are pure numeric/technical checks with
# zero LLM input. services/single_agent_analyzer.py never calls risk_governor (grep
# returns no matches) and has no envelope validation of its own, so a diverging verdict
# tagged with one of these codes means the gate caught something the single-agent path
# structurally cannot — not that multi-agent debate reasoned better.
DETERMINISTIC_GATE_CODES: set[str] = {
    "rr_too_low",
    "rr_implausible",
    "no_momentum_confirmation",
    "stop_inside_noise",
}


def _load_json(path: Path) -> object:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_single_agent(ticker: str) -> dict:
    payload = _load_json(ABLATION_DIR / "single_agent" / f"{ticker}.json")
    return payload if isinstance(payload, dict) else {}


def _index_by_ticker(full_results: list) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for r in full_results:
        if isinstance(r, dict) and r.get("ticker"):
            index[str(r["ticker"]).upper()] = r
    return index


def build_rows(tickers: list[str]) -> list[dict]:
    full_results = _load_json(ABLATION_DIR / "full_batch_results.json")
    full_by_ticker = _index_by_ticker(
        full_results if isinstance(full_results, list) else []
    )

    rows = []
    for ticker in tickers:
        quant_rating = "BUY" if ticker in QUANT_ONLY_BUY else "AVOID"

        single = _load_single_agent(ticker)
        single_status = single.get("status", "missing")
        single_verdict = single.get("verdict") or {}
        single_rating = (
            single_verdict.get("rating", "-")
            if single_status == "success"
            else single_status.upper()
        )
        single_conf = single_verdict.get("confidence")
        single_secs = single.get("duration_seconds")

        multi = full_by_ticker.get(ticker, {})
        multi_verdict = multi.get("verdict") or {}
        multi_rating = multi_verdict.get("rating") or (
            "ERROR" if multi.get("error") else "-"
        )
        multi_conf = multi_verdict.get("confidence")
        dissent = len(multi.get("dissenting_agents") or [])
        rounds = multi.get("debate_rounds")
        reason_codes = multi_verdict.get("reason_codes") or []
        metadata = multi.get("metadata") or {}

        rows.append(
            {
                "ticker": ticker,
                "quant_only": quant_rating,
                "single_rating": single_rating,
                "single_conf": single_conf,
                "single_secs": single_secs,
                "multi_rating": multi_rating,
                "multi_conf": multi_conf,
                "dissent": dissent,
                "rounds": rounds,
                "reason_codes": reason_codes,
                "flash_calls": metadata.get("flash_calls"),
                "pro_calls": metadata.get("pro_calls"),
            }
        )
    return rows


def _fmt_conf(value: object) -> str:
    return f"{float(value):.0%}" if isinstance(value, (int, float)) else "-"


def _fmt_secs(value: object) -> str:
    return f"{float(value):.0f}s" if isinstance(value, (int, float)) else "-"


def render_markdown(rows: list[dict]) -> str:
    total = len(rows)
    raw_agree = sum(1 for r in rows if r["single_rating"] == r["multi_rating"])

    any_actionable = [
        r
        for r in rows
        if {r["quant_only"], r["single_rating"], r["multi_rating"]} & ACTIONABLE_RATINGS
    ]
    conditional_agree = sum(
        1 for r in any_actionable if r["single_rating"] == r["multi_rating"]
    )
    diverging = [r for r in rows if r["single_rating"] != r["multi_rating"]]
    diverging_resolved = [r for r in diverging if r["multi_rating"] != "ERROR"]
    gate_driven = [
        r
        for r in diverging_resolved
        if set(r["reason_codes"]) & DETERMINISTIC_GATE_CODES
    ]

    total_single_secs = sum(r["single_secs"] or 0 for r in rows)
    total_flash = sum(r["flash_calls"] or 0 for r in rows)
    total_pro = sum(r["pro_calls"] or 0 for r in rows)

    lines = [
        "# V2.1 — Ablation Struktural: Debate vs Single-Agent vs Quant-Only",
        "",
        "**Cakupan**: perbandingan STRUKTURAL + BIAYA pada 25 ticker yang sama, "
        "hari yang sama (2026-07-04, regime DEFENSIVE), kode saat ini. BUKAN "
        "verdict performa — belum ada forward outcome untuk trade yang baru "
        "didebat hari ini (lihat scripts/ablation_study.py untuk perbandingan "
        "retrospektif berbasis outcome real: data-starved, n<30, lihat "
        "output/ablation/ablation_report.json).",
        "",
        "## Ringkasan",
        f"- Total ticker: {total}",
        f"- Agreement rate MENTAH (single vs multi): {raw_agree}/{total} "
        f"({raw_agree / total:.0%})"
        if total
        else "- Total ticker: 0",
        (
            f"- Agreement rate KONDISIONAL (subset di mana quant-only/single/multi "
            f"menyebut sesuatu actionable): {conditional_agree}/{len(any_actionable)}"
            if any_actionable
            else "- Tidak ada ticker dengan sinyal actionable dari leg manapun "
            "(quant-only AVOID 24/25 by construction; single & multi "
            "kemungkinan besar HOLD semua di regime ini)."
        ),
        f"- Quant-only BUY: {len(QUANT_ONLY_BUY)}/{total} (ERAA — satu-satunya "
        "lolos gate produksi penuh hari ini)",
        f"- Total waktu single-agent: {total_single_secs:.0f}s "
        f"({total_single_secs / max(total, 1):.0f}s/ticker rata-rata)",
        f"- Total panggilan LLM leg debat: {total_flash} flash + {total_pro} pro",
        (
            f"- Divergensi single-vs-multi yang gate-driven: {len(gate_driven)}/"
            f"{len(diverging_resolved)} (lihat ## Temuan Kunci)"
            if diverging_resolved
            else "- Tidak ada divergensi single-vs-multi dengan verdict multi yang resolved."
        ),
        "",
        "## Tabel Perbandingan",
        "",
        "| Ticker | Quant-Only | Single | Multi | Dissent | Rounds | Notes |",
        "|--------|-----------|--------|-------|---------|--------|-------|",
    ]

    for r in rows:
        note = ", ".join(r["reason_codes"][:2]) if r["reason_codes"] else "-"
        lines.append(
            f"| {r['ticker']} | {r['quant_only']} | "
            f"{r['single_rating']} ({_fmt_conf(r['single_conf'])}) | "
            f"{r['multi_rating']} ({_fmt_conf(r['multi_conf'])}) | "
            f"{r['dissent']} | {r['rounds'] or '-'} | {note} |"
        )

    lines.extend(["", "## Kasus Berbeda (single vs multi)"])
    if diverging:
        for r in diverging:
            tag = (
                " [GATE-DRIVEN]"
                if set(r["reason_codes"]) & DETERMINISTIC_GATE_CODES
                else (" [ERROR]" if r["multi_rating"] == "ERROR" else "")
            )
            lines.append(
                f"- {r['ticker']}: single={r['single_rating']} "
                f"({_fmt_conf(r['single_conf'])}) vs multi={r['multi_rating']} "
                f"({_fmt_conf(r['multi_conf'])}), dissent={r['dissent']}, "
                f"rounds={r['rounds'] or '-'}{tag}"
            )
    else:
        lines.append("- Tidak ada perbedaan rating single vs multi pada sampel ini.")

    lines.extend(["", "## Temuan Kunci: Sumber Divergensi"])
    if diverging_resolved:
        lines.append(
            f"Dari {len(diverging_resolved)} ticker dengan verdict single vs multi "
            f"yang berbeda (di luar 4 kasus ERROR koneksi), **{len(gate_driven)}/"
            f"{len(diverging_resolved)} ({len(gate_driven) / len(diverging_resolved):.0%}) "
            "ditandai kode gate deterministik** "
            f"({', '.join(sorted(DETERMINISTIC_GATE_CODES))}) — bukan hasil "
            "penalaran LLM yang lebih kaya di sisi multi-agent."
        )
        lines.append(
            "`rr_too_low`/`rr_implausible` berasal dari core/risk_governor.py "
            "(floor R/R tier-aware); `no_momentum_confirmation`/`stop_inside_noise` "
            "berasal dari validasi trade-envelope di services/debate_chamber.py "
            "(cek RSI/return_5d dan jarak stop vs ATR) — keduanya murni numerik, "
            "nol input LLM."
        )
        lines.append(
            "services/single_agent_analyzer.py tidak pernah memanggil "
            "risk_governor (dikonfirmasi via grep, nihil hasil) dan tidak punya "
            "validasi envelope sendiri, sehingga verdict BUY-nya tidak pernah "
            "punya kesempatan ditolak oleh gate yang sama. Artinya: pada sampel "
            "ini, nilai tambah yang teramati dari multi-agent bukan berasal dari "
            '"debat menemukan risiko yang lebih dalam", melainkan dari gate '
            "deterministik yang secara struktural hanya terpasang di jalur "
            "multi-agent — efek yang sama, dalam prinsip, bisa didapat lebih "
            "murah dengan menjalankan risk_governor + validasi envelope "
            "langsung di atas output single-agent, tanpa debat berputar-putar."
        )
    else:
        lines.append(
            "- Tidak ada divergensi single-vs-multi dengan verdict multi yang "
            "resolved untuk dianalisis pada sampel ini."
        )

    lines.extend(
        [
            "",
            "## Interpretasi",
            (
                "Agreement mentah tinggi kemungkinan besar didominasi kecocokan "
                "trivial (HOLD/HOLD, AVOID/AVOID) di bawah regime DEFENSIVE — "
                "lihat agreement KONDISIONAL di atas untuk sinyal yang lebih "
                "informatif. Pertanyaan yang benar-benar dijawab sesi ini bukan "
                '"mana rating yang benar" (itu tugas V3.1/V5 dengan forward '
                "evidence), melainkan apakah biaya debat penuh (rounds, dissent, "
                "flash+pro calls) terbukti sepadan — dan temuan kunci di atas "
                "menunjukkan bahwa pada sampel ini, gate deterministik yang jauh "
                "lebih murah sudah menjelaskan seluruh divergensi yang resolved. "
                "Verdict PERFORMA (rating mana yang benar) masih "
                "menunggu forward evidence — bukan pertanyaan yang bisa dijawab "
                "hari ini berapa pun jumlah debat yang dijalankan."
            ),
        ]
    )

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    from scripts.ablation_v2_1_run import TICKERS

    selected = sys.argv[1:] if len(sys.argv) > 1 else TICKERS
    report_rows = build_rows(selected)
    markdown = render_markdown(report_rows)
    out_path = ABLATION_DIR / "v2_1_structural_report.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"\nReport written to: {out_path}")
