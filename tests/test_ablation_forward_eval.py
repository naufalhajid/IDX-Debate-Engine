"""Fase C — evaluator arm-agnostic, TANPA network (price_fetcher di-inject).

Bukti kunci: record Arm A/B (yang TAK punya artefak debate) TETAP ter-skor karena
`scripts/ablation_forward_eval` menetralkan `_matching_debate_artifact_exists` — persis
blocker yang ditemukan di verifikasi Fase A.2. Target-hit -> win, stop-hit -> loss.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import ablation_forward_eval as afe  # noqa: E402
import core.backtest_outcome_evaluator as ev  # noqa: E402
from core.backtest_memory import BacktestMemory, TradeOutcome  # noqa: E402


def _open_buy(ticker: str, entry: float, target: float, stop: float, entry_date: str) -> TradeOutcome:
    return TradeOutcome(
        run_id="t",
        ticker=ticker,
        verdict_rating="BUY",
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
        confidence_at_entry=0.7,
        notes="test",
    )


def _fetcher(high: float, low: float, close: float):
    """Fetcher palsu: 30 bar harian konstan sejak `start` (deterministik, nol network)."""

    def _f(ticker: str, start: date, end: date):
        return [
            ev.PriceBar(
                trade_date=start + timedelta(days=i), high=high, low=low, close=close
            )
            for i in range(30)
        ]

    return _f


def _write_ledger(ledger: Path, record: TradeOutcome) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    BacktestMemory(ledger).record(record)


def test_quant_arm_scored_without_debate_artifact(tmp_path):
    # Ledger quant_only TANPA folder debates sama sekali -> di produksi akan
    # di-skip missing_debate_artifact. Evaluator arm-agnostic HARUS tetap skor.
    ledger = afe.ledger_path(tmp_path, "quant_only")
    _write_ledger(ledger, _open_buy("EEEE", 1000.0, 1100.0, 920.0, "2026-06-01"))
    r = afe.evaluate_arm(
        ledger,
        today=date(2026, 7, 7),
        horizon=20,
        price_fetcher=_fetcher(high=1200.0, low=1010.0, close=1100.0),  # tembus target
    )
    assert r["closed"] == 1
    assert r["wins"] == 1
    assert r["losses"] == 0
    assert r["win_rate"] == 1.0
    assert r["avg_pnl_pct"] is not None and r["avg_pnl_pct"] > 0


def test_stop_hit_is_loss(tmp_path):
    ledger = afe.ledger_path(tmp_path, "single_gated")
    _write_ledger(ledger, _open_buy("FFFF", 1000.0, 1100.0, 920.0, "2026-06-01"))
    r = afe.evaluate_arm(
        ledger,
        today=date(2026, 7, 7),
        horizon=20,
        price_fetcher=_fetcher(high=1050.0, low=900.0, close=950.0),  # tembus stop
    )
    assert r["closed"] == 1
    assert r["losses"] == 1
    assert r["win_rate"] == 0.0
    assert r["avg_pnl_pct"] is not None and r["avg_pnl_pct"] < 0


def test_gate_restored_after_eval(tmp_path):
    # Netralisasi gate WAJIB sementara (context manager) — tak bocor ke proses.
    original = ev._matching_debate_artifact_exists
    ledger = afe.ledger_path(tmp_path, "quant_only")
    _write_ledger(ledger, _open_buy("EEEE", 1000.0, 1100.0, 920.0, "2026-06-01"))
    afe.evaluate_arm(ledger, today=date(2026, 7, 7), price_fetcher=_fetcher(1200.0, 1010.0, 1100.0))
    assert ev._matching_debate_artifact_exists is original


def test_missing_ledger_returns_zeros(tmp_path):
    r = afe.evaluate_arm(tmp_path / "nope" / "backtest" / "backtest_memory.jsonl")
    assert r["total"] == 0
    assert r["eligible"] == 0
    assert r["win_rate"] is None
    assert r["avg_pnl_pct"] is None


def test_evaluate_all_covers_three_arms(tmp_path):
    _write_ledger(
        afe.ledger_path(tmp_path, "quant_only"),
        _open_buy("EEEE", 1000.0, 1100.0, 920.0, "2026-06-01"),
    )
    report = afe.evaluate_all(
        tmp_path, today=date(2026, 7, 7), price_fetcher=_fetcher(1200.0, 1010.0, 1100.0)
    )
    assert set(report.keys()) == set(afe.ARMS)
    assert report["quant_only"]["wins"] == 1
    # Arm tanpa ledger -> nol, bukan error.
    assert report["full_debate"]["total"] == 0
