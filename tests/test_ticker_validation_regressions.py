"""Regression contracts for centralized IDX ticker validation and containment."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routers.stocks import get_stock_detail
from app.api.schemas import DebateStreamRequest
from core.orchestrator import legacy as orchestrator_legacy
from core import provider_health
from run_debate import _save_timestamped_report, parse_args
from utils import market_snapshot as market_snapshot_module
from utils.market_snapshot import (
    MarketSnapshot,
    build_market_snapshot,
    persist_market_snapshots,
)
from utils.ticker import (
    InvalidIDXTicker,
    PathContainmentError,
    normalize_idx_ticker,
    normalize_idx_tickers,
    resolve_within_root,
)


@pytest.mark.parametrize(
    "ticker",
    [
        "../escape",
        r"..\escape",
        "/tmp/x",
        r"C:\tmp\x",
        "A/B",
        "A:B",
        "bbca.",
        "BBCA%2F..",
        "BBCA%5C..",
        "BBCA%252F..",
        "%2e%2e%2fescape",
        "%252e%252e%252fescape",
        "...",
        "BB\nCA",
        "\x00BBCA",
        "ＢＢＣＡ",
        "bbcı",  # Dotless i uppercases to ASCII I.
        "bbcſ",  # Long s uppercases to ASCII S.
        "ßß",  # Sharp-s expansion uppercases to four ASCII letters.
    ],
)
def test_normalize_idx_ticker_rejects_paths_encoding_and_unicode(
    ticker: str,
) -> None:
    with pytest.raises(InvalidIDXTicker):
        normalize_idx_ticker(ticker)


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [
        ("BBCA", "BBCA"),
        ("bbca", "BBCA"),
        ("BBCA.JK", "BBCA"),
        ("bbca.jk", "BBCA"),
        ("  bbca.jk  ", "BBCA"),
    ],
)
def test_normalize_idx_ticker_returns_canonical_base_symbol(
    ticker: str,
    expected: str,
) -> None:
    assert normalize_idx_ticker(ticker) == expected


def test_normalize_idx_tickers_deduplicates_after_canonicalization() -> None:
    assert normalize_idx_tickers(["bbca", "BBCA.JK", "bmri.jk", "BMRI"]) == [
        "BBCA",
        "BMRI",
    ]


def test_resolve_within_root_accepts_contained_path(tmp_path: Path) -> None:
    root = tmp_path / "output"

    resolved = resolve_within_root(root, "BBCA", "latest_debate.json")

    assert resolved == root.resolve() / "BBCA" / "latest_debate.json"


@pytest.mark.parametrize(
    "parts",
    [
        ("..", "escape.json"),
        ("BBCA", "..", "..", "escape.json"),
    ],
)
def test_resolve_within_root_rejects_traversal(
    tmp_path: Path,
    parts: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        resolve_within_root(tmp_path / "output", *parts)


def test_resolve_within_root_rejects_absolute_target(tmp_path: Path) -> None:
    root = tmp_path / "output"
    outside = tmp_path / "outside.json"

    with pytest.raises(ValueError):
        resolve_within_root(root, outside)


def test_direct_debate_parser_rejects_path_traversal_ticker() -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--tickers", "../escape"])

    assert exc_info.value.code == 2


def test_direct_debate_parser_normalizes_and_deduplicates_tickers() -> None:
    args = parse_args(["--tickers", "bbca", "BBCA.JK", "bmri.jk"])

    assert args.tickers == ["BBCA", "BMRI"]


@pytest.mark.parametrize(
    "ticker",
    ["../escape", r"..\escape", "/tmp/x", "A/B", "BBCA%2F..", "bbcı"],
)
def test_debate_stream_request_rejects_invalid_ticker(ticker: str) -> None:
    with pytest.raises(ValidationError):
        DebateStreamRequest(tickers=[ticker])


def test_debate_stream_request_normalizes_and_deduplicates_tickers() -> None:
    request = DebateStreamRequest(tickers=["bbca", "BBCA.JK", "bmri.jk"])

    assert request.tickers == ["BBCA", "BMRI"]


@pytest.mark.asyncio
async def test_stock_detail_rejects_invalid_ticker_before_database_access() -> None:
    class UnexpectedDatabase:
        async def scalars(self, *_args: object, **_kwargs: object) -> None:
            pytest.fail("invalid ticker reached database access")

    with pytest.raises(HTTPException) as exc_info:
        await get_stock_detail("../escape", db=UnexpectedDatabase())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_provider_health_rejects_invalid_ticker_before_probe_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_probe(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid ticker reached provider probe")

    monkeypatch.setattr(provider_health, "_check_stockbit", unexpected_probe)
    monkeypatch.setattr(provider_health, "_check_yfinance", unexpected_probe)

    with pytest.raises(InvalidIDXTicker):
        await provider_health.check_all_providers(["../escape"])


def test_candidate_intake_canonicalizes_and_deduplicates_suffixes() -> None:
    candidates = [
        {"Ticker": "bbca", "Current Price": 9_000, "Sektor Key": "bank"},
        {"Ticker": "BBCA.JK", "Current Price": 9_000, "Sektor Key": "bank"},
        {"ticker": "bmri.jk", "price": 6_000, "Sektor Key": "bank"},
    ]
    rejected: list[dict[str, object]] = []

    normalized = orchestrator_legacy._apply_candidate_intake(candidates, rejected)

    assert [candidate["Ticker"] for candidate in normalized] == ["BBCA", "BMRI"]
    assert normalized[1]["ticker"] == "BMRI"
    assert rejected == []
    assert orchestrator_legacy.parse_report(candidates=candidates) == ["BBCA", "BMRI"]
    assert orchestrator_legacy.parse_sector_map(candidates=candidates) == {
        "BBCA": "bank",
        "BMRI": "bank",
    }


def test_invalid_candidate_terminal_stays_batch_only_and_never_builds_ticker_path(
    tmp_path: Path,
) -> None:
    rejected: list[dict[str, object]] = []
    accepted = orchestrator_legacy._apply_candidate_intake(
        [{"Ticker": "../escape", "Current Price": 100}],
        rejected,
    )

    assert accepted == []
    assert len(rejected) == 1
    result = rejected[0]
    assert result["ticker"] is None
    assert result["verdict"]["ticker"] is None  # type: ignore[index]
    assert result["execution_status"] == "INSUFFICIENT_DATA"
    assert result["metadata"]["artifact_scope"] == "batch_only"  # type: ignore[index]
    assert result["metadata"]["raw_ticker"] == "../escape"  # type: ignore[index]

    output_dir = tmp_path / "requested-output"
    orchestrator_legacy.save_individual_debates_versioned(
        rejected,  # type: ignore[arg-type]
        "20260713_120000",
        output_dir=output_dir,
        record_backtest_memory=False,
    )

    assert not (tmp_path / "escape").exists()
    assert not list((output_dir / "debates").glob("**/*.json"))


@pytest.mark.parametrize("versioned", [False, True], ids=["flat", "versioned"])
def test_debate_persistence_rejects_cross_stock_identity_before_filesystem_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    versioned: bool,
) -> None:
    output_dir = tmp_path / "requested-output"
    mismatched = [
        {
            "ticker": "BBCA",
            "verdict": {"ticker": "BMRI", "rating": "BUY"},
        }
    ]

    def unexpected_filesystem_access(*_args: object, **_kwargs: object) -> None:
        pytest.fail("mismatched result reached filesystem access")

    with monkeypatch.context() as context:
        context.setattr(Path, "mkdir", unexpected_filesystem_access)
        context.setattr(Path, "write_text", unexpected_filesystem_access)
        with pytest.raises(InvalidIDXTicker, match="Conflicting result ticker"):
            if versioned:
                orchestrator_legacy.save_individual_debates_versioned(
                    mismatched,
                    "20260713_120000",
                    output_dir=output_dir,
                    record_backtest_memory=False,
                )
            else:
                orchestrator_legacy.save_individual_debates(
                    mismatched,
                    output_dir=output_dir,
                )

    assert not output_dir.exists()


def test_debate_persistence_writes_canonical_identity_to_path_and_payload(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "requested-output"
    orchestrator_legacy.save_individual_debates(
        [
            {
                "ticker": "bbca.jk",
                "verdict": {"ticker": "BBCA", "rating": "HOLD"},
                "risk_governor": {"ticker": "bbca.jk", "status": "reject"},
            }
        ],
        output_dir=output_dir,
    )

    artifact = output_dir / "debates" / "BBCA_debate.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["ticker"] == "BBCA"
    assert payload["verdict"]["ticker"] == "BBCA"
    assert payload["risk_governor"]["ticker"] == "BBCA"


@pytest.mark.asyncio
async def test_snapshot_absolute_path_is_rejected_before_path_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = {
        "Ticker": "BBCA",
        "snapshot_id": "BBCA-safe",
        "data_hash": "sha256-safe",
        "snapshot_path": "/outside/BBCA-safe.json.gz",
    }

    def unexpected_resolution(*_args: object, **_kwargs: object) -> None:
        pytest.fail("absolute candidate path reached filesystem resolution")

    monkeypatch.setattr(Path, "resolve", unexpected_resolution)

    with pytest.raises(PathContainmentError):
        await orchestrator_legacy._seed_candidate_market_snapshots(
            [candidate],
            output_dir=tmp_path / "output",
        )


def test_full_batch_writer_rejects_existing_symlink_outside_output_root(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "output"
    outside_file = tmp_path / "outside.json"
    output_root.mkdir()
    outside_file.write_text("outside-safe", encoding="utf-8")
    linked_file = output_root / "full_batch_results.json"
    try:
        linked_file.symlink_to(outside_file)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlink unavailable on this platform: {exc}")

    with pytest.raises(PathContainmentError):
        orchestrator_legacy.save_full_results(
            [{"ticker": "BBCA", "status": "success"}],
            linked_file,
        )

    assert outside_file.read_text(encoding="utf-8") == "outside-safe"


def test_save_timestamped_report_rejects_traversal_before_filesystem_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "requested-output"
    outside_dir = tmp_path / "escape"

    def unexpected_filesystem_access(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid ticker reached filesystem access")

    with monkeypatch.context() as context:
        context.setattr(Path, "resolve", unexpected_filesystem_access)
        context.setattr(Path, "mkdir", unexpected_filesystem_access)
        context.setattr(Path, "write_text", unexpected_filesystem_access)
        with pytest.raises(InvalidIDXTicker):
            _save_timestamped_report(
                {},
                output_dir,
                "../escape",
                "20260713_120000",
                "2026-07-13T12:00:00+07:00",
            )

    assert not output_dir.exists()
    assert not outside_dir.exists()


@pytest.mark.asyncio
async def test_orchestrator_override_rejects_traversal_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    side_effect_reached = False

    def unexpected_runtime_reset() -> None:
        nonlocal side_effect_reached
        side_effect_reached = True
        pytest.fail("invalid ticker reached orchestrator runtime setup")

    monkeypatch.setattr(
        orchestrator_legacy,
        "_reset_orchestrator_runtime_config",
        unexpected_runtime_reset,
    )

    with pytest.raises(InvalidIDXTicker):
        await orchestrator_legacy.main(
            tickers=["../escape"],
            output_dir=tmp_path / "orchestrator-output",
        )

    assert side_effect_reached is False
    assert not (tmp_path / "orchestrator-output").exists()


@pytest.mark.parametrize("versioned", [False, True], ids=["flat", "versioned"])
def test_debate_persistence_rejects_poisoned_ticker_before_output_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    versioned: bool,
) -> None:
    output_dir = tmp_path / ("versioned-output" if versioned else "flat-output")
    poisoned_results = [
        {
            "ticker": "../escape",
            "verdict": {"rating": "NO_TRADE"},
        }
    ]

    def unexpected_filesystem_access(*_args: object, **_kwargs: object) -> None:
        pytest.fail("poisoned result reached filesystem access")

    with monkeypatch.context() as context:
        context.setattr(Path, "mkdir", unexpected_filesystem_access)
        context.setattr(Path, "write_text", unexpected_filesystem_access)
        with pytest.raises(InvalidIDXTicker):
            if versioned:
                orchestrator_legacy.save_individual_debates_versioned(
                    poisoned_results,
                    "20260713_120000",
                    output_dir=output_dir,
                    record_backtest_memory=False,
                )
            else:
                orchestrator_legacy.save_individual_debates(
                    poisoned_results,
                    output_dir=output_dir,
                )

    assert not output_dir.exists()
    assert not (tmp_path / "escape").exists()


def _empty_market_snapshot() -> MarketSnapshot:
    return build_market_snapshot(
        "BBCA",
        pd.DataFrame(),
        requested_start=date(2025, 1, 1),
        requested_end=date(2026, 7, 13),
    )


@pytest.mark.parametrize(
    "poison",
    ["ticker", "snapshot_id"],
    ids=["malicious-ticker", "forged-snapshot-id"],
)
def test_persist_market_snapshots_rejects_poison_before_directory_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    poison: str,
) -> None:
    output_dir = tmp_path / "market-snapshots"
    snapshot = _empty_market_snapshot()
    if poison == "ticker":
        snapshot = replace(snapshot, ticker="../escape")
    else:
        # This still passes the expected ``BBCA-`` prefix check; containment
        # must reject its parent components independently.
        snapshot = replace(snapshot, snapshot_id="BBCA-safe/../../escape")

    def unexpected_filesystem_access(*_args: object, **_kwargs: object) -> None:
        pytest.fail("poisoned snapshot reached filesystem persistence")

    with monkeypatch.context() as context:
        context.setattr(Path, "mkdir", unexpected_filesystem_access)
        context.setattr(Path, "write_text", unexpected_filesystem_access)
        context.setattr(
            market_snapshot_module,
            "save_market_snapshot",
            unexpected_filesystem_access,
        )
        with pytest.raises((InvalidIDXTicker, PathContainmentError)):
            persist_market_snapshots({"BBCA": snapshot}, output_dir)

    assert not output_dir.exists()
    assert not (tmp_path / "escape.json.gz").exists()


def test_resolve_within_root_rejects_valid_name_symlink_to_outside(
    tmp_path: Path,
) -> None:
    root = tmp_path / "output"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    ticker_link = root / "BBCA"
    try:
        ticker_link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink unavailable on this platform: {exc}")

    with pytest.raises(PathContainmentError):
        resolve_within_root(root, "BBCA", "latest_debate.json")
