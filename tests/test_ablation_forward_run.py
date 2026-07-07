"""Fase A.2 — verifikasi ROUTING harness forward 3-arm, TANPA LLM.

Menguji wiring `scripts/ablation_forward_run.py` (bukan re-test risk_governor yang
sudah dicakup tests/test_single_agent_gated.py): dari artefak sintetis on-disk,
pastikan tiap arm menulis ke ledger sandbox TERPISAH, HOLD -> watchlist, BUY ->
ledger, Arm B mengikuti keputusan gate, dan dedup bekerja. Semua di tmp_path
(dir buang) — tak menyentuh output/ablation_forward/ organik.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))  # scripts/ bukan package -> path insert

import ablation_forward_run as afr  # noqa: E402
from core.backtest_memory import BacktestMemory  # noqa: E402
from services.single_agent_analyzer import SingleAgentVerdict  # noqa: E402
from services.single_agent_gated import is_gated_buy  # noqa: E402


def _single_wrapper(ticker: str, rating: str, **ov) -> dict:
    verdict = dict(
        ticker=ticker,
        rating=rating,
        confidence=0.75,
        fair_value=1300.0,
        current_price=1000.0,
        entry_price_range="960 - 1000",
        target_price=1150.0,
        stop_loss=920.0,
        risk_reward_ratio=2.5,
        reasoning="synthetic",
        key_risks=["r1"],
        key_catalysts=["c1"],
        model_used="test-flash",
        generated_at="2026-07-04T00:00:00+00:00",
        run_id="test",
        data_sources=["yfinance"],
    )
    verdict.update(ov)
    return {"ticker": ticker, "run_id": "test", "verdict": verdict, "status": "success"}


def _debate(ticker: str, rating: str, **ov) -> dict:
    verdict = dict(
        ticker=ticker,
        rating=rating,
        confidence=0.7,
        entry_price_range="960 - 1000",
        target_price=1150.0,
        stop_loss=920.0,
        generated_at="2026-07-04T00:00:00+00:00",
    )
    verdict.update(ov)
    return {"ticker": ticker, "verdict": verdict, "error": None}


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    single = tmp_path / "src" / "single_agent"
    debates = tmp_path / "src" / "debates"
    single.mkdir(parents=True)
    debates.mkdir(parents=True)
    (single / "AAAA.json").write_text(json.dumps(_single_wrapper("AAAA", "BUY")))
    (single / "BBBB.json").write_text(json.dumps(_single_wrapper("BBBB", "HOLD")))
    (debates / "CCCC_debate.json").write_text(json.dumps(_debate("CCCC", "BUY")))
    (debates / "DDDD_debate.json").write_text(json.dumps(_debate("DDDD", "HOLD")))
    cands = tmp_path / "cands.json"
    cands.write_text(
        json.dumps([{"Ticker": "EEEE", "Current Price": 1000.0, "Stop Loss Level": 920.0}])
    )
    return single, debates, cands


def _run(tmp_path: Path):
    single, debates, cands = _setup(tmp_path)
    out = tmp_path / "out"
    tally = afr.run_record(
        single_dir=single,
        debates_dir=debates,
        candidates_path=cands,
        dirroot=out,
        run_id="20260707_000000",
    )
    return out, tally


def test_three_arms_write_separate_ledgers(tmp_path):
    out, _ = _run(tmp_path)
    paths = {arm: afr.ledger_path(out, arm) for arm in afr.ARMS}
    # Ketiga path ledger harus BERBEDA (isolasi per-arm).
    assert len({str(p) for p in paths.values()}) == 3
    # quant_only & full_debate pasti punya >=1 BUY -> file ledger ada.
    assert paths["quant_only"].exists()
    assert paths["full_debate"].exists()


def test_arm_a_records_candidate_with_attached_envelope(tmp_path):
    out, tally = _run(tmp_path)
    assert tally["quant_only"]["recorded"] == 1
    records = BacktestMemory(afr.ledger_path(out, "quant_only")).all_records()
    assert len(records) == 1
    rec = records[0]
    assert rec.ticker == "EEEE"
    assert rec.outcome == "open"
    # Envelope R/R 2:1 di-cap +10%: entry 1000, stop 920 -> target min(1160, 1100)=1100.
    assert rec.entry_price == 1000.0
    assert rec.stop_loss == 920.0
    assert rec.target_price == 1100.0
    assert rec.stop_loss < rec.entry_price < rec.target_price
    # entry_date WAJIB ISO YYYY-MM-DD (cermin backtest_outcome_evaluator._parse_date):
    # jangan pernah "run_id[:10]" yang menghasilkan "20260707_v" -> skip diam-diam.
    assert date.fromisoformat(rec.entry_date) is not None


def test_arm_c_buy_to_ledger_hold_to_watchlist(tmp_path):
    out, tally = _run(tmp_path)
    # Verdict debate di-route apa adanya (sudah post-gate): BUY->ledger, HOLD->watchlist.
    assert tally["full_debate"]["recorded"] == 1
    assert tally["full_debate"]["watchlist"] == 1
    ledger = BacktestMemory(afr.ledger_path(out, "full_debate")).all_records()
    assert [r.ticker for r in ledger] == ["CCCC"]
    wl_lines = afr.watchlist_path(out, "full_debate").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["ticker"] == "DDDD" for line in wl_lines if line.strip())


def test_arm_b_routing_follows_gate(tmp_path):
    out, tally = _run(tmp_path)
    # HOLD single-agent (BBBB) selalu -> watchlist. BUY (AAAA) -> ledger HANYA jika
    # gate mengizinkan; routing harus setara dgn is_gated_buy (bukan asumsi lolos).
    buy_verdict = SingleAgentVerdict(**_single_wrapper("AAAA", "BUY")["verdict"])
    expect_buy_recorded = 1 if is_gated_buy(buy_verdict) else 0
    assert tally["single_gated"]["recorded"] == expect_buy_recorded
    ledger = BacktestMemory(afr.ledger_path(out, "single_gated")).all_records()
    assert len(ledger) == expect_buy_recorded
    # BBBB (HOLD) pasti di watchlist; AAAA menyusul bila gate menolak.
    wl_path = afr.watchlist_path(out, "single_gated")
    wl_tickers = {
        json.loads(line)["ticker"]
        for line in wl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert "BBBB" in wl_tickers


def test_arm_isolation_quant_ledger_has_only_its_own(tmp_path):
    out, _ = _run(tmp_path)
    quant = BacktestMemory(afr.ledger_path(out, "quant_only")).all_records()
    # Ledger quant_only TIDAK boleh bocor ticker arm lain (AAAA/CCCC).
    assert {r.ticker for r in quant} == {"EEEE"}


def test_dedup_second_pass_adds_nothing(tmp_path):
    out, _ = _run(tmp_path)
    single = tmp_path / "src" / "single_agent"
    debates = tmp_path / "src" / "debates"
    cands = tmp_path / "cands.json"
    before = {
        arm: len(BacktestMemory(afr.ledger_path(out, arm)).all_records())
        for arm in afr.ARMS
    }
    afr.run_record(
        single_dir=single,
        debates_dir=debates,
        candidates_path=cands,
        dirroot=out,
        run_id="20260707_111111",  # run_id beda, tapi entry+target+stop sama -> dedup
    )
    after = {
        arm: len(BacktestMemory(afr.ledger_path(out, arm)).all_records())
        for arm in afr.ARMS
    }
    assert before == after
