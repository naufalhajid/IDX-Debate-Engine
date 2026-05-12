from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.context_pack_builder import ContextPack
from services.rag_evidence_store import (
    MAX_BUNDLE_CHARS,
    EvidenceBundle,
    EvidenceChunk,
    RAGEvidenceStore,
)


def _pack(*, as_of: datetime | None = None) -> ContextPack:
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
    store = RAGEvidenceStore(tmp_path / "evidence.jsonl")

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


def test_score_chunks_fair_value_scores_higher_than_metadata(tmp_path: Path) -> None:
    store = RAGEvidenceStore(tmp_path / "evidence.jsonl")
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
    store = RAGEvidenceStore(tmp_path / "evidence.jsonl")
    chunks = [
        _chunk(category="technical", is_stale=False, chunk_id="fresh"),
        _chunk(category="technical", is_stale=True, chunk_id="stale"),
    ]

    scored = store.score_chunks(chunks, query_context="swing trade analysis")
    scores = {chunk.chunk_id: chunk.relevance_score for chunk in scored}

    assert scores["stale"] < scores["fresh"]


def test_score_chunks_rsi_keyword_boosts_technical_chunk(tmp_path: Path) -> None:
    store = RAGEvidenceStore(tmp_path / "evidence.jsonl")
    technical = _chunk(category="technical")

    baseline = store.score_chunks([technical], query_context="swing trade analysis")[0]
    boosted = store.score_chunks([technical], query_context="RSI momentum setup")[0]

    assert boosted.relevance_score > baseline.relevance_score


def test_select_evidence_always_includes_fair_value_when_available(
    tmp_path: Path,
) -> None:
    store = RAGEvidenceStore(tmp_path / "evidence.jsonl")
    chunks = [
        _chunk(category="fair_value", is_stale=True, chunk_id="fair"),
        _chunk(category="technical", content="RSI support", chunk_id="technical"),
    ]

    selected = store.select_evidence(chunks, query_context="RSI support", max_chunks=1)

    assert [chunk.category for chunk in selected] == ["fair_value"]


def test_select_evidence_total_content_does_not_exceed_max_bundle_chars(
    tmp_path: Path,
) -> None:
    store = RAGEvidenceStore(tmp_path / "evidence.jsonl")
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
    store = RAGEvidenceStore(tmp_path / "evidence.jsonl")

    bundle = store.build_bundle(_pack(), run_id="run-1")

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.total_chunks_selected <= bundle.total_chunks_considered
    assert (tmp_path / "evidence.jsonl").exists()


def test_bundle_to_prompt_string_contains_ticker_and_header(tmp_path: Path) -> None:
    store = RAGEvidenceStore(tmp_path / "evidence.jsonl")
    bundle = store.build_bundle(_pack(), run_id="run-1")

    prompt = store.bundle_to_prompt_string(bundle)

    assert "BBCA" in prompt
    assert "EVIDENCE BRIEF" in prompt
