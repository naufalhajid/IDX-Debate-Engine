from __future__ import annotations

import gzip
import json
from datetime import date, datetime

import pandas as pd
import pytest

from utils.market_snapshot import (
    IDX_TIMEZONE,
    build_market_snapshot,
    build_market_snapshots,
    candidate_snapshot_provenance,
    download_market_snapshot,
    load_market_snapshot,
    persist_market_snapshots,
    resample_daily_to_weekly,
    snapshots_to_multiindex,
)


AS_OF = date(2026, 7, 10)
START = date(2024, 11, 17)
AFTER_CLOSE = datetime(2026, 7, 10, 17, 0, tzinfo=IDX_TIMEZONE)


def _frame(rows: int = 400, *, end: str = "2026-07-10") -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=rows)
    close = pd.Series(range(1000, 1000 + rows), index=index, dtype="float64")
    return pd.DataFrame(
        {
            "Open": close - 2,
            "High": close + 5,
            "Low": close - 5,
            "Close": close,
            "Volume": 1_000_000.0 + close,
        },
        index=index,
    )


def _snapshot(raw: pd.DataFrame, *, minimum: int = 400):
    return build_market_snapshot(
        "BBCA",
        raw,
        requested_start=START,
        requested_end=AS_OF,
        min_complete_bars=minimum,
        now=AFTER_CLOSE,
    )


def test_flat_and_both_batch_layouts_produce_identical_snapshot() -> None:
    flat = _frame()
    ticker_first = pd.concat({"BBCA.JK": flat}, axis=1)
    price_first = ticker_first.swaplevel(0, 1, axis=1).sort_index(axis=1)

    snapshots = [_snapshot(layout) for layout in (flat, ticker_first, price_first)]

    assert {snapshot.data_hash for snapshot in snapshots} == {snapshots[0].data_hash}
    assert {snapshot.snapshot_id for snapshot in snapshots} == {
        snapshots[0].snapshot_id
    }
    pd.testing.assert_frame_equal(snapshots[0].history, snapshots[1].history)
    pd.testing.assert_frame_equal(snapshots[0].history, snapshots[2].history)


def test_screener_price_and_seeded_snapshot_price_are_identical() -> None:
    """FIX 2 (price-basis consistency): the screener and the debate chamber
    must read the same "current price" for the same ticker + as-of date.

    core/quant_filter/pipeline.py's ticker analyzer does ONE bulk
    yfinance.download() for all candidates, then:
      1. build_market_snapshots(raw, tickers, ...) turns that bulk frame into
         one MarketSnapshot per ticker — these are the artifacts later
         persisted (persist_market_snapshots) and seeded into the debate
         chamber's session cache (_seed_candidate_market_snapshots).
      2. snapshots_to_multiindex(snapshots) reconstructs the working
         DataFrame FROM those same snapshots (not from the raw pre-snapshot
         frame) — and it is THIS reconstructed frame's Close.iloc[-1] that
         becomes the screener's "Current Price" (pipeline.py:769).
    So step 1's snapshot.history and step 2's per-ticker slice are built from
    the same MarketSnapshot object by construction, not by convention. This
    test proves that construction holds instead of assuming it.
    """
    raw = pd.concat({"BBCA.JK": _frame(), "BBRI.JK": _frame()}, axis=1)
    tickers = ["BBCA", "BBRI"]

    snapshots = build_market_snapshots(
        raw,
        tickers,
        requested_start=START,
        requested_end=AS_OF,
        min_complete_bars=400,
        now=AFTER_CLOSE,
    )
    # What the screener sees: pipeline.py:769 current_px = close.iloc[-1] on
    # the reconstructed per-ticker frame.
    reconstructed = snapshots_to_multiindex(snapshots, ready_only=False)

    for ticker in tickers:
        screener_price = float(reconstructed[f"{ticker}.JK"]["Close"].iloc[-1])
        # What gets seeded into the debate chamber's cache and later read via
        # derive_current_price(market_data) -- utils/market_data_cache.py:190.
        seeded_snapshot_price = float(snapshots[ticker].history["Close"].iloc[-1])
        assert screener_price == seeded_snapshot_price


def test_cleaning_keeps_last_duplicate_and_drops_incomplete_row() -> None:
    frame = _frame(10, end="2026-07-09")
    duplicate = frame.iloc[[-1]].copy()
    duplicate["Close"] = 9_999.0
    incomplete = frame.iloc[[-1]].copy()
    incomplete.index = pd.DatetimeIndex(["2026-07-10"])
    incomplete["High"] = float("nan")
    dirty = pd.concat([frame, duplicate, incomplete])

    snapshot = _snapshot(dirty, minimum=1)

    assert snapshot.row_count == 10
    assert snapshot.history.iloc[-1]["Close"] == 9_999.0
    assert snapshot.cleaning["dropped_duplicate_dates"] == 1
    assert snapshot.cleaning["dropped_incomplete_rows"] == 1


def test_current_session_bar_is_dropped_before_finalization() -> None:
    frame = _frame(5)
    snapshot = build_market_snapshot(
        "BBCA",
        frame,
        requested_start=START,
        requested_end=AS_OF,
        min_complete_bars=1,
        now=datetime(2026, 7, 10, 10, 0, tzinfo=IDX_TIMEZONE),
    )

    assert snapshot.row_count == 4
    assert snapshot.last_date == date(2026, 7, 9)
    assert snapshot.cleaning["dropped_current_session"] is True


def test_current_session_bar_is_kept_after_finalization() -> None:
    snapshot = _snapshot(_frame(5), minimum=1)

    assert snapshot.row_count == 5
    assert snapshot.last_date == AS_OF
    assert snapshot.cleaning["dropped_current_session"] is False


@pytest.mark.parametrize(
    ("rows", "expected_status"),
    [(399, "insufficient_data"), (400, "ready")],
)
def test_snapshot_requires_400_complete_bars(rows: int, expected_status: str) -> None:
    snapshot = _snapshot(_frame(rows))

    assert snapshot.status == expected_status
    assert snapshot.is_ready is (rows == 400)
    assert ("insufficient_complete_bars" in snapshot.reason_codes) is (rows < 400)


def test_downloader_uses_explicit_daily_adjusted_window() -> None:
    captured: dict = {}

    def fake_download(symbol: str, **kwargs):
        captured["symbol"] = symbol
        captured.update(kwargs)
        return _frame()

    snapshot = download_market_snapshot(
        "bbca",
        as_of=AS_OF,
        downloader=fake_download,
        now=AFTER_CLOSE,
    )

    assert snapshot.is_ready
    assert captured["symbol"] == "BBCA.JK"
    assert captured["start"] == "2024-10-18"
    assert captured["end"] == "2026-07-11"
    assert captured["interval"] == "1d"
    assert captured["auto_adjust"] is True
    assert "period" not in captured


def test_persist_load_round_trip_preserves_hash_and_bars(tmp_path) -> None:
    snapshot = _snapshot(_frame())
    paths = persist_market_snapshots({"BBCA": snapshot}, tmp_path)

    loaded = load_market_snapshot(
        paths["BBCA"],
        expected_snapshot_id=snapshot.snapshot_id,
        expected_data_hash=snapshot.data_hash,
    )

    assert loaded.provenance() == snapshot.provenance()
    pd.testing.assert_frame_equal(loaded.history, snapshot.history, check_freq=False)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshots"][0]["data_hash"] == snapshot.data_hash


def test_persist_load_empty_snapshot_keeps_auditable_artifact(tmp_path) -> None:
    snapshot = _snapshot(pd.DataFrame())

    path = persist_market_snapshots({"BBCA": snapshot}, tmp_path)["BBCA"]
    loaded = load_market_snapshot(path)

    assert loaded.status == "insufficient_data"
    assert loaded.reason_codes == (
        "empty_history",
        "insufficient_complete_bars",
    )
    assert loaded.row_count == 0
    assert loaded.history.empty
    assert loaded.data_hash == snapshot.data_hash


def test_load_rejects_tampered_snapshot(tmp_path) -> None:
    snapshot = _snapshot(_frame())
    path = persist_market_snapshots({"BBCA": snapshot}, tmp_path)["BBCA"]
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["bars"][-1]["Close"] += 1
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)

    with pytest.raises(ValueError, match="data hash mismatch"):
        load_market_snapshot(path)


def test_weekly_and_candidate_provenance_derive_from_same_snapshot() -> None:
    snapshot = _snapshot(_frame())
    weekly = resample_daily_to_weekly(snapshot.history)
    candidate = candidate_snapshot_provenance(
        snapshot,
        artifact_path="market_snapshots/BBCA.json.gz",
    )

    assert not weekly.empty
    assert weekly.iloc[-1]["Close"] == snapshot.history.iloc[-1]["Close"]
    assert candidate["snapshot_id"] == snapshot.snapshot_id
    assert candidate["data_hash"] == snapshot.data_hash
    assert candidate["market_snapshot"]["row_count"] == 400
