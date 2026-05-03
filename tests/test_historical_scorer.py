"""Tests untuk core/historical_scorer.py."""
import json
from pathlib import Path

import pytest

from core.historical_scorer import (
    apply_historical_adjustment,
    compute_historical_win_rate,
    load_debate_history,
    _WIN_RATE_HIGH_THRESHOLD,
    _WIN_RATE_LOW_THRESHOLD,
    _BONUS,
    _PENALTY,
)


def _make_record(ticker: str, rating: str, confidence: float) -> dict:
    return {
        "ticker": ticker,
        "verdict": {"rating": rating, "confidence": confidence},
        "debate_history": [],
    }


# ── load_debate_history ────────────────────────────────────────────────────────

def test_load_empty_dir(tmp_path: Path) -> None:
    """Dir tanpa debates/ mengembalikan list kosong tanpa error."""
    records = load_debate_history(tmp_path)
    assert records == []


def test_load_debate_files(tmp_path: Path) -> None:
    """JSON valid di debates/ harus ter-load."""
    debates_dir = tmp_path / "debates"
    debates_dir.mkdir()
    (debates_dir / "BBCA_debate.json").write_text(
        json.dumps({"ticker": "BBCA", "verdict": {}, "debate_history": []}),
        encoding="utf-8",
    )
    records = load_debate_history(tmp_path)
    assert len(records) == 1
    assert records[0]["ticker"] == "BBCA"


def test_load_skips_corrupt_files(tmp_path: Path) -> None:
    """File JSON rusak diabaikan, tidak raise exception."""
    debates_dir = tmp_path / "debates"
    debates_dir.mkdir()
    (debates_dir / "BAD_debate.json").write_text("not json{{{", encoding="utf-8")
    (debates_dir / "GOOD_debate.json").write_text(
        json.dumps({"ticker": "GOOD", "verdict": {}, "debate_history": []}),
        encoding="utf-8",
    )
    records = load_debate_history(tmp_path)
    assert len(records) == 1
    assert records[0]["ticker"] == "GOOD"


# ── compute_historical_win_rate ────────────────────────────────────────────────

def test_win_rate_insufficient_data() -> None:
    """Kurang dari MIN_RECORDS_FOR_ADJUSTMENT mengembalikan None."""
    records = [_make_record("BBCA", "BUY", 0.8)]
    result = compute_historical_win_rate("BBCA", records)
    assert result is None


def test_win_rate_all_wins() -> None:
    """3 records BUY confidence > 0.5 = win rate 1.0."""
    records = [_make_record("BBCA", "BUY", 0.8)] * 3
    result = compute_historical_win_rate("BBCA", records)
    assert result == pytest.approx(1.0)


def test_win_rate_all_losses() -> None:
    """3 records HOLD = win rate 0.0."""
    records = [_make_record("BBCA", "HOLD", 0.3)] * 3
    result = compute_historical_win_rate("BBCA", records)
    assert result == pytest.approx(0.0)


def test_win_rate_mixed() -> None:
    """2 BUY + 1 HOLD dari 3 records = 2/3 win rate."""
    records = [
        _make_record("BBCA", "BUY", 0.8),
        _make_record("BBCA", "BUY", 0.7),
        _make_record("BBCA", "HOLD", 0.3),
    ]
    result = compute_historical_win_rate("BBCA", records)
    assert result == pytest.approx(2 / 3)


def test_win_rate_low_confidence_not_counted() -> None:
    """BUY dengan confidence <= 0.5 tidak dihitung sebagai win."""
    records = [_make_record("BBCA", "BUY", 0.4)] * 3
    result = compute_historical_win_rate("BBCA", records)
    assert result == pytest.approx(0.0)


def test_win_rate_different_ticker() -> None:
    """Records ticker lain tidak mempengaruhi ticker yang dicari."""
    records = [_make_record("KLBF", "BUY", 0.9)] * 5
    result = compute_historical_win_rate("BBCA", records)
    assert result is None  # BBCA belum punya records


# ── apply_historical_adjustment ────────────────────────────────────────────────

def test_adjustment_none_win_rate() -> None:
    """win_rate=None tidak mengubah score."""
    score = 0.60
    result = apply_historical_adjustment(score, None)
    assert result == pytest.approx(score)


def test_adjustment_high_win_rate_bonus() -> None:
    """win_rate >= 0.70 mendapat bonus _BONUS."""
    score = 0.60
    result = apply_historical_adjustment(score, _WIN_RATE_HIGH_THRESHOLD)
    assert result == pytest.approx(score + _BONUS)


def test_adjustment_low_win_rate_penalty() -> None:
    """win_rate < 0.30 mendapat penalty _PENALTY."""
    score = 0.60
    result = apply_historical_adjustment(score, _WIN_RATE_LOW_THRESHOLD - 0.01)
    assert result == pytest.approx(score + _PENALTY)


def test_adjustment_neutral_zone_no_change() -> None:
    """win_rate di antara 0.30-0.70 tidak mengubah score."""
    score = 0.60
    result = apply_historical_adjustment(score, 0.50)
    assert result == pytest.approx(score)


def test_adjustment_clamps_to_zero() -> None:
    """Score tidak bisa di bawah 0.0 meski setelah penalty."""
    result = apply_historical_adjustment(0.02, 0.10)  # penalty -0.05
    assert result >= 0.0


def test_adjustment_clamps_to_one() -> None:
    """Score tidak bisa di atas 1.0 meski setelah bonus."""
    result = apply_historical_adjustment(0.98, 0.90)  # bonus +0.05
    assert result <= 1.0


# ── conviction weights validation ─────────────────────────────────────────────

def test_settings_weights_sum_to_one() -> None:
    """CONVICTION_WEIGHT_CONFIDENCE + CONVICTION_WEIGHT_RR_RATIO harus = 1.0."""
    from core.settings import settings
    total = settings.CONVICTION_WEIGHT_CONFIDENCE + settings.CONVICTION_WEIGHT_RR_RATIO
    assert abs(total - 1.0) < 1e-6, f"Weights sum = {total}, expected 1.0"
