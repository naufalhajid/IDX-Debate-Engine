from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.context_pack_builder import ContextPack
from services.evidence_ranker import (
    MAX_BUNDLE_CHARS,
    EvidenceBundle,
    EvidenceChunk,
    EvidenceRanker,
    citations_for_bundle,
    guard_evidence_citation_ids,
    guard_evidence_citations,
)


def _pack(
    *,
    as_of: datetime | None = None,
    source_timestamps: dict[str, str] | None = None,
) -> ContextPack:
    return ContextPack(
        ticker="BBCA",
        as_of=as_of or datetime.now(timezone.utc),
        price=6125.0,
        fair_value=10474.0,
        fundamentals={
            "brief": "ROE 22.4%, net margin 49.5%, dividend yield 5.49%.",
            "exdate": "30 Mar 26",
        },
        technicals={"rsi14": 43.7, "ma50": 6478.0, "ma200": 7389.0},
        sentiment_summary="INSUFFICIENT_DATA but no red flags.",
        data_sources=["stockbit", "yfinance", "gemini"],
        source_timestamps=source_timestamps or {},
        missing_fields=[],
        token_estimate=100,
    )


def _chunk(
    *,
    category: str,
    content: str = "evidence",
    is_stale: bool = False,
    chunk_id: str | None = None,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id or f"BBCA_{category}_0",
        ticker="BBCA",
        category=category,
        content=content,
        source="stockbit",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        freshness_seconds=172_800 if is_stale else 0,
        relevance_score=0.0,
        is_stale=is_stale,
    )


def test_chunk_context_pack_returns_chunks_for_non_empty_categories(
    tmp_path: Path,
) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")

    chunks = store.chunk_context_pack(_pack(), run_id="run-1")
    categories = {chunk.category for chunk in chunks}

    assert {
        "fair_value",
        "fundamental",
        "technical",
        "sentiment",
        "exdate",
        "metadata",
    }.issubset(categories)
    assert all(chunk.content_hash for chunk in chunks)
    assert all("_run_1_" in chunk.chunk_id for chunk in chunks)


def test_chunk_context_pack_uses_source_timestamp_for_staleness(
    tmp_path: Path,
) -> None:
    old_yfinance = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    store = EvidenceRanker(tmp_path / "evidence.jsonl")

    chunks = store.chunk_context_pack(
        _pack(source_timestamps={"yfinance": old_yfinance}),
        run_id="run-1",
    )

    technical_chunks = [chunk for chunk in chunks if chunk.category == "technical"]
    assert technical_chunks
    assert all(chunk.is_stale for chunk in technical_chunks)


def test_score_chunks_fair_value_scores_higher_than_metadata(tmp_path: Path) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    chunks = [
        _chunk(category="fair_value", chunk_id="BBCA_fair_value_0"),
        _chunk(category="metadata", chunk_id="BBCA_metadata_0"),
    ]

    scored = store.score_chunks(chunks, query_context="swing trade analysis")
    scores = {chunk.category: chunk.relevance_score for chunk in scored}

    assert scores["fair_value"] > scores["metadata"]


def test_score_chunks_stale_chunk_scores_lower_than_fresh_same_category(
    tmp_path: Path,
) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    chunks = [
        _chunk(category="technical", is_stale=False, chunk_id="fresh"),
        _chunk(category="technical", is_stale=True, chunk_id="stale"),
    ]

    scored = store.score_chunks(chunks, query_context="swing trade analysis")
    scores = {chunk.chunk_id: chunk.relevance_score for chunk in scored}

    assert scores["stale"] < scores["fresh"]


def test_score_chunks_rsi_keyword_boosts_technical_chunk(tmp_path: Path) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    technical = _chunk(category="technical")

    baseline = store.score_chunks([technical], query_context="swing trade analysis")[0]
    boosted = store.score_chunks([technical], query_context="RSI momentum setup")[0]

    assert boosted.relevance_score > baseline.relevance_score


def test_select_evidence_always_includes_fair_value_when_available(
    tmp_path: Path,
) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    chunks = [
        _chunk(category="fair_value", is_stale=True, chunk_id="fair"),
        _chunk(category="technical", content="RSI support", chunk_id="technical"),
    ]

    selected = store.select_evidence(chunks, query_context="RSI support", max_chunks=1)

    assert [chunk.category for chunk in selected] == ["fair_value"]


def test_select_evidence_total_content_does_not_exceed_max_bundle_chars(
    tmp_path: Path,
) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    chunks = [_chunk(category="fair_value", content="fair value", chunk_id="fair")]
    chunks.extend(
        _chunk(
            category="fundamental",
            content="x" * 500,
            chunk_id=f"fundamental-{index}",
        )
        for index in range(20)
    )

    selected = store.select_evidence(chunks, query_context="fundamental earnings")

    assert sum(len(chunk.content) for chunk in selected) <= MAX_BUNDLE_CHARS


def test_build_bundle_returns_consistent_counts(tmp_path: Path) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")

    bundle = store.build_bundle(_pack(), run_id="run-1")

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.total_chunks_selected <= bundle.total_chunks_considered
    assert bundle.citation_ids == [chunk.chunk_id for chunk in bundle.chunks]
    assert bundle.rendered_char_count <= MAX_BUNDLE_CHARS
    assert (tmp_path / "evidence.jsonl").exists()


def test_log_bundle_records_auditable_chunk_content(tmp_path: Path) -> None:
    log_path = tmp_path / "evidence.jsonl"
    store = EvidenceRanker(log_path)

    bundle = store.build_bundle(_pack(), run_id="run-1")
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])

    assert record["rendered_char_count"] == bundle.rendered_char_count
    assert record["selected_chunks"]
    first_chunk = record["selected_chunks"][0]
    assert first_chunk["chunk_id"] == bundle.citation_ids[0]
    assert first_chunk["content_hash"]
    assert first_chunk["content"]


def test_bundle_to_prompt_string_contains_ticker_and_header(tmp_path: Path) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    bundle = store.build_bundle(_pack(), run_id="run-1")

    prompt = store.bundle_to_prompt_string(bundle)

    assert "BBCA" in prompt
    assert "EVIDENCE BRIEF" in prompt
    assert "Evidence ID:" in prompt


def test_citations_for_bundle_returns_prompt_safe_references(tmp_path: Path) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    bundle = store.build_bundle(_pack(), run_id="run-1")

    citations = citations_for_bundle(bundle)

    assert [citation.chunk_id for citation in citations] == bundle.citation_ids
    assert all(citation.source for citation in citations)


def test_guard_evidence_citations_accepts_known_ids(tmp_path: Path) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    bundle = store.build_bundle(_pack(), run_id="run-1")

    report = guard_evidence_citations(bundle, [bundle.citation_ids[0]])

    assert report.valid is True
    assert report.errors == []
    assert report.cited_chunks[0].chunk_id == bundle.citation_ids[0]


def test_guard_evidence_citations_reports_missing_or_insufficient_ids(
    tmp_path: Path,
) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    bundle = store.build_bundle(_pack(), run_id="run-1")

    report = guard_evidence_citations(bundle, ["missing-id"], min_citations=2)

    assert report.valid is False
    assert report.missing_citation_ids == ["missing-id"]
    assert any("expected at least 2 citation" in error for error in report.errors)


def test_guard_evidence_citation_ids_works_from_metadata_citations(
    tmp_path: Path,
) -> None:
    store = EvidenceRanker(tmp_path / "evidence.jsonl")
    bundle = store.build_bundle(_pack(), run_id="run-1")
    citations = citations_for_bundle(bundle)

    report = guard_evidence_citation_ids(citations, [bundle.citation_ids[0]])

    assert report.valid is True
    assert report.cited_chunks[0].chunk_id == bundle.citation_ids[0]


def test_market_freshness_seconds_excludes_weekends_and_holidays(monkeypatch):
    from services.evidence_ranker import _market_freshness_seconds

    # WIB is UTC+7
    wib = timezone(timedelta(hours=7))

    # Friday 2026-05-15 16:00:00 WIB (is a holiday: Cuti Bersama Kenaikan Yesus Kristus)
    # Saturday 2026-05-16 (weekend)
    # Sunday 2026-05-17 (weekend)
    # Monday 2026-05-18 10:00:00 WIB

    start = datetime(2026, 5, 15, 16, 0, 0, tzinfo=wib)
    end = datetime(2026, 5, 18, 10, 0, 0, tzinfo=wib)

    # Raw elapsed duration is 66 hours (237,600 seconds)
    # Excluded duration:
    # - Friday 15 May (holiday): 8 hours (from 16:00 to 24:00)
    # - Saturday 16 May (weekend): 24 hours
    # - Sunday 17 May (weekend): 24 hours
    # Total excluded = 56 hours (201,600 seconds)
    # Market age should be 10 hours (36,000 seconds)

    age = _market_freshness_seconds(
        start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    )
    assert age == 10 * 3600
