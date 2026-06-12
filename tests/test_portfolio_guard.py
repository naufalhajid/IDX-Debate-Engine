"""Tests for core/portfolio_guard.py — P2.3 portfolio heat cap and drawdown kill-switch."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.portfolio_guard import (
    check_portfolio_allows_new_entry,
    compute_30d_drawdown,
    compute_portfolio_heat,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()


def test_heat_zero_when_file_absent(tmp_path: Path) -> None:
    assert compute_portfolio_heat(tmp_path / "nonexistent.jsonl") == 0.0


def test_heat_sums_open_stop_distances(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [
            {"outcome": "open", "entry_price": 1000, "stop_loss": 960},   # 4%
            {"outcome": "open", "entry_price": 500,  "stop_loss": 490},   # 2%
            {"outcome": "win",  "entry_price": 200,  "stop_loss": 190},   # closed — excluded
        ],
    )
    heat = compute_portfolio_heat(bt)
    assert abs(heat - 0.06) < 1e-6


def test_heat_excludes_closed_records(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [
            {"outcome": "loss", "entry_price": 1000, "stop_loss": 900},
            {"outcome": "win",  "entry_price": 500,  "stop_loss": 450},
        ],
    )
    assert compute_portfolio_heat(bt) == 0.0


def test_drawdown_zero_when_file_absent(tmp_path: Path) -> None:
    assert compute_30d_drawdown(tmp_path / "nonexistent.jsonl") == 0.0


def test_drawdown_averages_recent_closed_pnl(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [
            {"outcome": "loss", "exit_date": _days_ago(5),  "pnl_pct": -0.08},
            {"outcome": "loss", "exit_date": _days_ago(10), "pnl_pct": -0.12},
            {"outcome": "open", "exit_date": None,          "pnl_pct": None},
        ],
    )
    dd = compute_30d_drawdown(bt)
    assert abs(dd - (-0.10)) < 1e-9


def test_drawdown_ignores_old_closed_records(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(bt, [{"outcome": "loss", "exit_date": _days_ago(45), "pnl_pct": -0.20}])
    assert compute_30d_drawdown(bt) == 0.0


def test_heat_cap_blocks_entry(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [{"outcome": "open", "entry_price": 1000, "stop_loss": 979}] * 3,
    )
    allowed, reason = check_portfolio_allows_new_entry(bt, new_stop_dist_pct=0.02)
    assert not allowed
    assert "portfolio_heat" in reason


def test_heat_cap_allows_entry_under_threshold(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(bt, [{"outcome": "open", "entry_price": 1000, "stop_loss": 980}])
    allowed, reason = check_portfolio_allows_new_entry(bt, new_stop_dist_pct=0.03)
    assert allowed
    assert reason == "ok"


def test_drawdown_kill_switch_fires(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [
            {"outcome": "loss", "exit_date": _days_ago(i + 1), "pnl_pct": -0.16}
            for i in range(5)
        ],
    )
    allowed, reason = check_portfolio_allows_new_entry(bt, new_stop_dist_pct=0.02)
    assert not allowed
    assert "drawdown_kill_switch" in reason


def test_empty_file_allows_entry(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    bt.write_text("", encoding="utf-8")
    allowed, reason = check_portfolio_allows_new_entry(bt, new_stop_dist_pct=0.04)
    assert allowed
    assert reason == "ok"
