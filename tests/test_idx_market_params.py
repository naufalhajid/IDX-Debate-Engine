from __future__ import annotations

from datetime import date

from core.idx_market_params import (
    damodaran_review_due,
    next_damodaran_review_date,
)


def test_next_damodaran_review_date_is_following_january():
    assert next_damodaran_review_date(date(2026, 4, 1)) == date(2027, 1, 15)


def test_damodaran_review_due_flips_after_january_review_date():
    assert damodaran_review_due(date(2027, 1, 14)) is False
    assert damodaran_review_due(date(2027, 1, 15)) is True
