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
            {"outcome": "open", "entry_price": 1000, "stop_loss": 960},  # 4%
            {"outcome": "open", "entry_price": 500, "stop_loss": 490},  # 2%
            {
                "outcome": "win",
                "entry_price": 200,
                "stop_loss": 190,
            },  # closed — excluded
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
            {"outcome": "win", "entry_price": 500, "stop_loss": 450},
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
            {"outcome": "loss", "exit_date": _days_ago(5), "pnl_pct": -8.0},  # -8%
            {"outcome": "loss", "exit_date": _days_ago(10), "pnl_pct": -12.0},  # -12%
            {"outcome": "open", "exit_date": None, "pnl_pct": None},
        ],
    )
    dd = compute_30d_drawdown(bt)
    assert abs(dd - (-10.0)) < 1e-9


def test_drawdown_ignores_old_closed_records(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt, [{"outcome": "loss", "exit_date": _days_ago(45), "pnl_pct": -20.0}]
    )
    assert compute_30d_drawdown(bt) == 0.0


def test_heat_cap_blocks_entry(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [{"outcome": "open", "entry_price": 1000, "stop_loss": 979}] * 3,
    )
    # No position_size_pct on the stored records -> weight 1.0 each (worst-case
    # fallback), so raw heat alone (~6.3%) already exceeds the recalibrated 1.3%
    # cap regardless of the new entry's size.
    allowed, reason = check_portfolio_allows_new_entry(bt, new_stop_dist_pct=0.02)
    assert not allowed
    assert "portfolio_heat" in reason


def test_heat_cap_allows_entry_under_threshold(tmp_path: Path) -> None:
    # V4.3: cap is now 1.3% of *weighted* heat. Both the existing open record and
    # the candidate new entry must carry a realistic position_size_pct (e.g. a BUY
    # at 20% allocation) or the worst-case 1.0 fallback alone would exceed the cap.
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [
            {
                "outcome": "open",
                "entry_price": 1000,
                "stop_loss": 980,
                "position_size_pct": 0.20,
            }
        ],
    )
    allowed, reason = check_portfolio_allows_new_entry(
        bt, new_stop_dist_pct=0.03, new_position_size_pct=0.20
    )
    assert allowed
    assert reason == "ok"


def test_drawdown_kill_switch_fires(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [
            {"outcome": "loss", "exit_date": _days_ago(i + 1), "pnl_pct": -16.0}  # -16%
            for i in range(5)
        ],
    )
    # Realistic position size on the candidate entry so the heat gate (checked
    # first) passes and the drawdown kill-switch is the one that actually fires.
    allowed, reason = check_portfolio_allows_new_entry(
        bt, new_stop_dist_pct=0.02, new_position_size_pct=0.20
    )
    assert not allowed
    assert "drawdown_kill_switch" in reason


def test_empty_file_allows_entry(tmp_path: Path) -> None:
    bt = tmp_path / "bt.jsonl"
    bt.write_text("", encoding="utf-8")
    allowed, reason = check_portfolio_allows_new_entry(
        bt, new_stop_dist_pct=0.04, new_position_size_pct=0.20
    )
    assert allowed
    assert reason == "ok"


def test_heat_weighted_by_position_size(tmp_path: Path) -> None:
    """V4.3: a sized position contributes size_pct * stop_dist_pct, not the raw pct."""
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [
            {
                "outcome": "open",
                "entry_price": 1000,
                "stop_loss": 950,  # 5% stop distance
                "position_size_pct": 0.20,  # BUY-sized allocation
            }
        ],
    )
    # Weighted: 0.05 * 0.20 = 0.01, not the raw 0.05.
    assert abs(compute_portfolio_heat(bt) - 0.01) < 1e-9


def test_heat_mixes_weighted_and_legacy_unweighted_records(tmp_path: Path) -> None:
    """Records predating V4.3 (no position_size_pct) keep the old weight=1.0 fallback."""
    bt = tmp_path / "bt.jsonl"
    _write_jsonl(
        bt,
        [
            {
                "outcome": "open",
                "entry_price": 1000,
                "stop_loss": 950,  # 5% stop distance
                "position_size_pct": 0.20,
            },  # weighted contribution: 0.01
            {
                "outcome": "open",
                "entry_price": 1000,
                "stop_loss": 990,  # 1% stop distance, no position_size_pct at all
            },  # legacy fallback contribution: 0.01
        ],
    )
    assert abs(compute_portfolio_heat(bt) - 0.02) < 1e-9


def test_check_new_entry_weighted_allows_what_full_weight_would_block(
    tmp_path: Path,
) -> None:
    """A BUY-sized (20%) 5% stop passes the cap; the same stop at full weight would not."""
    bt = tmp_path / "bt.jsonl"
    bt.write_text("", encoding="utf-8")

    allowed_weighted, _ = check_portfolio_allows_new_entry(
        bt, new_stop_dist_pct=0.05, new_position_size_pct=0.20
    )
    allowed_full_weight, reason_full_weight = check_portfolio_allows_new_entry(
        bt, new_stop_dist_pct=0.05
    )

    assert allowed_weighted
    assert not allowed_full_weight
    assert "portfolio_heat" in reason_full_weight
