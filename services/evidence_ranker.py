"""Freshness-aware evidence selection for compact prompt injection."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.settings import settings
from services.context_pack_builder import ContextPack, build_context_pack


STALE_THRESHOLD_SECONDS = 86_400
MAX_CHUNKS_PER_BUNDLE = 12
MAX_BUNDLE_CHARS = 2_400
CHARS_PER_TOKEN = 4
DEFAULT_PATH = settings.rag_evidence_log_path

CATEGORY_WEIGHTS = {
    "fair_value": 1.0,
    "fundamental": 0.9,
    "technical": 0.85,
    "sentiment": 0.6,
    "exdate": 0.7,
    "metadata": 0.3,
}

CategoryName = Literal[
    "fundamental",
    "technical",
    "sentiment",
    "fair_value",
    "exdate",
    "metadata",
]

logger = logging.getLogger(__name__)


class EvidenceChunk(BaseModel):
    """One cited fact candidate selected from a context pack."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    ticker: str
    category: CategoryName
    content: str
    source: str
    fetched_at: str
    freshness_seconds: int | None
    content_hash: str = ""
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    is_stale: bool


class EvidenceBundle(BaseModel):
    """Selected evidence for one ticker and one prompt context."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    run_id: str
    query_context: str
    chunks: list[EvidenceChunk]
    total_chunks_considered: int
    total_chunks_selected: int
    has_stale_data: bool
    staleness_warning: str | None
    token_estimate: int
    selected_content_chars: int = 0
    rendered_char_count: int = 0
    created_at: str
    citation_ids: list[str] = Field(default_factory=list)


class EvidenceCitation(BaseModel):
    """Prompt-safe reference to a selected evidence chunk."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    category: CategoryName
    source: str
    relevance_score: float
    is_stale: bool


class CitationGuardReport(BaseModel):
    """Validation report for citations claimed against an evidence bundle."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    cited_chunks: list[EvidenceCitation]
    missing_citation_ids: list[str]
    stale_citation_ids: list[str]
    errors: list[str] = Field(default_factory=list)


class EvidenceRanker:
    """Select, score, and log compact evidence bundles from context packs."""

    def __init__(self, storage_path: str | Path = DEFAULT_PATH) -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def chunk_context_pack(self, pack: ContextPack, run_id: str) -> list[EvidenceChunk]:
        """Convert a normalized context pack into category-aware evidence chunks."""
        chunks: list[EvidenceChunk] = []
        category_counts: dict[CategoryName, int] = {}

        def append_chunk(category: CategoryName, content: str) -> None:
            clean_content = content.strip()
            if not clean_content:
                return
            category_index = category_counts.get(category, 0)
            category_counts[category] = category_index + 1
            source = _source_for_category(pack.data_sources, category)
            fetched_at_dt = _resolve_chunk_timestamp(pack, category, source)
            fetched_at = fetched_at_dt.isoformat()
            pack_time = _resolve_pack_timestamp(pack)
            freshness_seconds = _freshness_seconds(fetched_at_dt, pack_time)
            is_stale = (
                freshness_seconds is not None
                and freshness_seconds > STALE_THRESHOLD_SECONDS
            )
            chunks.append(
                EvidenceChunk(
                    chunk_id=(
                        f"{pack.ticker}_{_safe_id_part(run_id)}_"
                        f"{category}_{category_index}"
                    ),
                    ticker=pack.ticker,
                    category=category,
                    content=clean_content,
                    source=source,
                    fetched_at=fetched_at,
                    freshness_seconds=freshness_seconds,
                    content_hash=_content_hash(clean_content),
                    relevance_score=0.0,
                    is_stale=is_stale,
                )
            )

        if pack.price or pack.fair_value is not None:
            append_chunk("fair_value", _fair_value_content(pack))

        fundamentals_text = (
            _compact_json(pack.fundamentals) if pack.fundamentals else ""
        )
        for part in _split_text(fundamentals_text, max_chars=400):
            append_chunk("fundamental", part)

        technicals_text = _compact_json(pack.technicals) if pack.technicals else ""
        for part in _split_text(technicals_text, max_chars=400):
            append_chunk("technical", part)

        if pack.sentiment_summary:
            append_chunk("sentiment", pack.sentiment_summary)

        exdate = _extract_exdate(pack.fundamentals)
        if exdate is not None:
            append_chunk("exdate", f"Dividend Ex-Date: {exdate}")

        sources = ", ".join(pack.data_sources) if pack.data_sources else "unknown"
        timestamps = (
            _compact_json(pack.source_timestamps)
            if getattr(pack, "source_timestamps", None)
            else "unknown"
        )
        missing = ", ".join(pack.missing_fields) if pack.missing_fields else "none"
        append_chunk(
            "metadata",
            (
                f"Data Sources: {sources} | "
                f"Source Timestamps: {timestamps} | "
                f"Missing Fields: {missing}"
            ),
        )

        return chunks

    def score_chunks(
        self,
        chunks: list[EvidenceChunk],
        query_context: str,
    ) -> list[EvidenceChunk]:
        """Score chunks by category, query intent, and freshness."""
        query = query_context.lower()
        scored_chunks: list[EvidenceChunk] = []
        for chunk in chunks:
            score = CATEGORY_WEIGHTS.get(chunk.category, 0.0)
            score += _keyword_boost(chunk.category, query)
            if chunk.is_stale:
                score *= 0.5
            scored_chunks.append(
                chunk.model_copy(update={"relevance_score": _clamp_score(score)})
            )
        return sorted(
            scored_chunks, key=lambda item: item.relevance_score, reverse=True
        )

    def select_evidence(
        self,
        chunks: list[EvidenceChunk],
        query_context: str,
        max_chunks: int = MAX_CHUNKS_PER_BUNDLE,
    ) -> list[EvidenceChunk]:
        """Score and select the best chunks within the evidence character budget."""
        if max_chunks <= 0:
            return []

        scored = self.score_chunks(chunks, query_context)
        selected: list[EvidenceChunk] = []
        selected_ids: set[str] = set()

        fair_value_chunk = next(
            (chunk for chunk in scored if chunk.category == "fair_value"),
            None,
        )
        if fair_value_chunk is not None and _selection_fits(
            [fair_value_chunk],
            total_considered=len(chunks),
            query_context=query_context,
        ):
            selected.append(fair_value_chunk)
            selected_ids.add(fair_value_chunk.chunk_id)

        for chunk in scored:
            if len(selected) >= max_chunks:
                break
            if chunk.chunk_id in selected_ids:
                continue
            candidate = [*selected, chunk]
            if not _selection_fits(
                candidate,
                total_considered=len(chunks),
                query_context=query_context,
            ):
                continue
            selected.append(chunk)
            selected_ids.add(chunk.chunk_id)

        return selected

    def build_bundle(
        self,
        pack: ContextPack,
        run_id: str,
        query_context: str = "swing trade analysis",
    ) -> EvidenceBundle:
        """Build, log, and return a selected evidence bundle."""
        chunks = self.chunk_context_pack(pack, run_id)
        selected = self.select_evidence(chunks, query_context)
        bundle = _make_bundle(
            ticker=pack.ticker,
            run_id=run_id,
            query_context=query_context,
            selected=selected,
            total_considered=len(chunks),
        )
        while bundle.rendered_char_count > MAX_BUNDLE_CHARS and selected:
            selected = selected[:-1]
            bundle = _make_bundle(
                ticker=pack.ticker,
                run_id=run_id,
                query_context=query_context,
                selected=selected,
                total_considered=len(chunks),
            )
        self.log_bundle(bundle)
        return bundle

    def bundle_to_prompt_string(self, bundle: EvidenceBundle) -> str:
        """Render selected evidence as a compact prompt-ready text block."""
        return "\n".join(_render_bundle_lines(bundle))

    def log_bundle(self, bundle: EvidenceBundle) -> None:
        """Append a compact bundle summary to JSONL without affecting runtime flow."""
        record = {
            "ticker": bundle.ticker,
            "run_id": bundle.run_id,
            "total_considered": bundle.total_chunks_considered,
            "total_selected": bundle.total_chunks_selected,
            "selected_chunk_ids": bundle.citation_ids,
            "has_stale_data": bundle.has_stale_data,
            "staleness_warning": bundle.staleness_warning,
            "query_context": bundle.query_context,
            "token_estimate": bundle.token_estimate,
            "selected_content_chars": bundle.selected_content_chars,
            "rendered_char_count": bundle.rendered_char_count,
            "selected_chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "category": chunk.category,
                    "source": chunk.source,
                    "fetched_at": chunk.fetched_at,
                    "freshness_seconds": chunk.freshness_seconds,
                    "relevance_score": chunk.relevance_score,
                    "is_stale": chunk.is_stale,
                    "content_hash": chunk.content_hash,
                    "content_chars": len(chunk.content),
                    "content": chunk.content,
                }
                for chunk in bundle.chunks
            ],
            "created_at": bundle.created_at,
        }
        try:
            with self.storage_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.exception("Failed to write evidence bundle log.")


def citations_for_bundle(bundle: EvidenceBundle) -> list[EvidenceCitation]:
    """Return compact citations for every selected evidence chunk."""
    return [
        EvidenceCitation(
            chunk_id=chunk.chunk_id,
            category=chunk.category,
            source=chunk.source,
            relevance_score=chunk.relevance_score,
            is_stale=chunk.is_stale,
        )
        for chunk in bundle.chunks
    ]


def guard_evidence_citations(
    bundle: EvidenceBundle,
    cited_chunk_ids: list[str],
    *,
    min_citations: int = 1,
) -> CitationGuardReport:
    """Validate claimed evidence IDs against a selected evidence bundle."""
    return guard_evidence_citation_ids(
        citations_for_bundle(bundle),
        cited_chunk_ids,
        min_citations=min_citations,
    )


def guard_evidence_citation_ids(
    citations: list[EvidenceCitation],
    cited_chunk_ids: list[str],
    *,
    min_citations: int = 1,
) -> CitationGuardReport:
    """Validate claimed evidence IDs against selected citation metadata."""
    citation_map = {citation.chunk_id: citation for citation in citations}
    cited_unique = [chunk_id for chunk_id in dict.fromkeys(cited_chunk_ids) if chunk_id]
    cited_chunks = [
        citation_map[chunk_id] for chunk_id in cited_unique if chunk_id in citation_map
    ]
    missing = [chunk_id for chunk_id in cited_unique if chunk_id not in citation_map]
    stale = [citation.chunk_id for citation in cited_chunks if citation.is_stale]

    errors: list[str] = []
    if len(cited_chunks) < min_citations:
        errors.append(
            f"expected at least {min_citations} citation(s), got {len(cited_chunks)}"
        )
    for chunk_id in missing:
        errors.append(f"citation id not found in evidence bundle: {chunk_id}")

    return CitationGuardReport(
        valid=not errors,
        cited_chunks=cited_chunks,
        missing_citation_ids=missing,
        stale_citation_ids=stale,
        errors=errors,
    )


def _make_bundle(
    *,
    ticker: str,
    run_id: str,
    query_context: str,
    selected: list[EvidenceChunk],
    total_considered: int,
) -> EvidenceBundle:
    has_stale_data = any(chunk.is_stale for chunk in selected)
    selected_content_chars = sum(len(chunk.content) for chunk in selected)
    provisional = EvidenceBundle(
        ticker=ticker,
        run_id=run_id,
        query_context=query_context,
        chunks=selected,
        total_chunks_considered=total_considered,
        total_chunks_selected=len(selected),
        has_stale_data=has_stale_data,
        staleness_warning=(
            "Some selected evidence is older than 24 hours." if has_stale_data else None
        ),
        token_estimate=selected_content_chars // CHARS_PER_TOKEN,
        selected_content_chars=selected_content_chars,
        rendered_char_count=0,
        created_at=datetime.now(timezone.utc).isoformat(),
        citation_ids=[chunk.chunk_id for chunk in selected],
    )
    rendered_char_count = len("\n".join(_render_bundle_lines(provisional)))
    token_estimate = rendered_char_count // CHARS_PER_TOKEN
    bundle = provisional.model_copy(
        update={
            "token_estimate": token_estimate,
            "rendered_char_count": rendered_char_count,
        }
    )
    # Recompute once because the token-estimate footer may change length.
    return bundle.model_copy(
        update={"rendered_char_count": len("\n".join(_render_bundle_lines(bundle)))}
    )


def _render_bundle_lines(bundle: EvidenceBundle) -> list[str]:
    lines = [
        f"=== EVIDENCE BRIEF: {bundle.ticker} ===",
        f"Query: {bundle.query_context}",
    ]
    if bundle.has_stale_data:
        lines.append("⚠️ WARNING: Some data may be stale")
    lines.append("")

    for chunk in bundle.chunks:
        lines.extend(
            [
                f"[{chunk.category.upper()}]",
                f"Evidence ID: {chunk.chunk_id}",
                chunk.content,
                f"Source: {chunk.source} | Score: {chunk.relevance_score:.2f}",
            ]
        )
        if chunk.is_stale:
            lines.append("⚠️ STALE")
        lines.append("")

    lines.extend(
        [
            "---",
            (
                "Total evidence: "
                f"{bundle.total_chunks_selected}/{bundle.total_chunks_considered} "
                "chunks selected"
            ),
            f"Token estimate: ~{bundle.token_estimate}",
        ]
    )
    return lines


def _selection_fits(
    selected: list[EvidenceChunk],
    *,
    total_considered: int,
    query_context: str,
) -> bool:
    if not selected:
        return True
    bundle = _make_bundle(
        ticker=selected[0].ticker,
        run_id="selection",
        query_context=query_context,
        selected=selected,
        total_considered=total_considered,
    )
    return bundle.rendered_char_count <= MAX_BUNDLE_CHARS


def _resolve_chunk_timestamp(
    pack: ContextPack,
    category: CategoryName,
    source: str,
) -> datetime:
    timestamps = getattr(pack, "source_timestamps", {}) or {}
    candidates = (
        source,
        source.lower(),
        category,
        "market_data" if category in {"technical", "fair_value"} else "",
        "context",
    )
    for key in candidates:
        value = _timestamp_for_key(timestamps, key)
        if value is None:
            continue
        return _parse_timestamp(value)
    return _resolve_pack_timestamp(pack)


def _timestamp_for_key(timestamps: dict[str, str], key: str) -> str | None:
    if not key:
        return None
    key_lower = key.lower()
    for stored_key, value in timestamps.items():
        if stored_key.lower() == key_lower and value:
            return value
    return None


def _resolve_pack_timestamp(pack: ContextPack) -> datetime:
    value = getattr(pack, "generated_at", None) or pack.as_of
    return _parse_timestamp(value)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str) and value.strip():
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            timestamp = datetime.now(timezone.utc)
    else:
        timestamp = datetime.now(timezone.utc)

    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _freshness_seconds(
    fetched_at: datetime, reference_time: datetime | None = None
) -> int | None:
    try:
        ref = reference_time or datetime.now(timezone.utc)
        return _market_freshness_seconds(fetched_at, ref)
    except TypeError:
        return None


def _market_freshness_seconds(fetched_at: datetime, current_time: datetime) -> int:
    """
    Calculate the age of the data in seconds, excluding weekends and IDX holidays.
    If the period spans Saturday, Sunday, or a bursa holiday, those periods are excluded
    to prevent false positive staleness classifications during closures.
    """
    if fetched_at >= current_time:
        return 0

    total_seconds = int((current_time - fetched_at).total_seconds())

    # Indonesian Stock Exchange (IDX) Trading Holidays for 2026
    idx_holidays_2026 = {
        "2026-01-01",
        "2026-01-16",
        "2026-02-16",
        "2026-02-17",
        "2026-03-18",
        "2026-03-19",
        "2026-03-20",
        "2026-03-23",
        "2026-03-24",
        "2026-04-03",
        "2026-05-01",
        "2026-05-14",
        "2026-05-15",
        "2026-05-27",
        "2026-05-28",
        "2026-06-01",
        "2026-06-16",
        "2026-08-17",
        "2026-08-25",
        "2026-12-24",
        "2026-12-25",
        "2026-12-31",
    }

    # Parse custom/additional holidays from settings dynamically
    additional_holidays = set()
    if settings.IDX_ADDITIONAL_HOLIDAYS:
        try:
            val = settings.IDX_ADDITIONAL_HOLIDAYS.strip()
            if val.startswith("[") and val.endswith("]"):
                dates = json.loads(val)
            else:
                dates = [d.strip() for d in val.split(",") if d.strip()]
            for d in dates:
                if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                    additional_holidays.add(d)
        except Exception as e:
            logger.warning(f"Failed to parse IDX_ADDITIONAL_HOLIDAYS settings: {e}")

    all_holidays = idx_holidays_2026 | additional_holidays

    import datetime as dt_mod

    wib = dt_mod.timezone(dt_mod.timedelta(hours=7))

    start_local = fetched_at.astimezone(wib)
    end_local = current_time.astimezone(wib)

    start_date = start_local.date()
    end_date = end_local.date()

    if start_date == end_date:
        is_holiday = (
            start_date.weekday() in (5, 6) or start_date.isoformat() in all_holidays
        )
        if is_holiday:
            return 0
        return total_seconds

    closed_seconds = 0
    curr = start_date
    while curr <= end_date:
        is_closed = curr.weekday() in (5, 6) or curr.isoformat() in all_holidays
        if is_closed:
            day_start_local = dt_mod.datetime.combine(curr, dt_mod.time.min, tzinfo=wib)
            day_end_local = day_start_local + dt_mod.timedelta(days=1)

            overlap_start = max(start_local, day_start_local)
            overlap_end = min(end_local, day_end_local)
            if overlap_start < overlap_end:
                closed_seconds += int((overlap_end - overlap_start).total_seconds())
        curr += dt_mod.timedelta(days=1)

    return max(0, total_seconds - closed_seconds)


def _fair_value_content(pack: ContextPack) -> str:
    fair_value = (
        f"{pack.fair_value:.0f}" if pack.fair_value is not None else "INSUFFICIENT_DATA"
    )
    upside = "INSUFFICIENT_DATA"
    if pack.price and pack.fair_value is not None:
        upside = f"{((pack.fair_value - pack.price) / pack.price) * 100:.1f}%"
    return (
        f"Current Price: {pack.price:.0f} | Fair Value: {fair_value} | Upside: {upside}"
    )


def _source_for_category(sources: list[str], category: CategoryName) -> str:
    if not sources:
        return "unknown"

    preferred = {
        "fair_value": ("stockbit", "gemini"),
        "fundamental": ("stockbit", "gemini"),
        "technical": ("yfinance",),
        "sentiment": ("gemini",),
        "exdate": ("stockbit", "gemini"),
        "metadata": (),
    }
    lower_sources = [(source, source.lower()) for source in sources]
    for needle in preferred[category]:
        for source, lower_source in lower_sources:
            if needle in lower_source:
                return source
    if category == "metadata":
        return ", ".join(sources)
    return sources[0]


def _split_text(text: str, max_chars: int) -> list[str]:
    clean_text = text.strip()
    if not clean_text:
        return []
    if len(clean_text) <= max_chars:
        return [clean_text]

    chunks: list[str] = []
    start = 0
    while start < len(clean_text):
        end = min(start + max_chars, len(clean_text))
        if end < len(clean_text):
            split_at = clean_text.rfind(" ", start, end)
            if split_at > start:
                end = split_at
        chunks.append(clean_text[start:end].strip())
        start = end
    return [chunk for chunk in chunks if chunk]


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _safe_id_part(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "unknown")).strip("_")
    return clean or "unknown"


def _extract_exdate(fundamentals: dict) -> Any | None:
    for key in ("exdate", "ex_date", "ex-date", "dividend_ex_date"):
        value = fundamentals.get(key)
        if value not in (None, ""):
            return value
    return None


KEYWORD_MAP: dict[CategoryName, tuple[str, ...]] = {
    "fair_value": ("valuation", "fair value", "undervalued"),
    "technical": ("technical", "trend", "momentum", "rsi", "ma", "support"),
    "sentiment": ("sentiment", "social", "news"),
    "exdate": ("dividend", "exdate", "ex-date"),
    "fundamental": ("fundamental", "roe", "margin", "earnings"),
    "metadata": (),
}


def _keyword_boost(category: CategoryName, query: str) -> float:
    matches = 0
    for keyword in KEYWORD_MAP[category]:
        if _query_contains_keyword(query, keyword):
            matches += 1
    return min(matches * 0.1, 0.3)


def _query_contains_keyword(query: str, keyword: str) -> bool:
    if len(keyword) <= 3 and keyword.isalpha():
        return re.search(rf"\b{re.escape(keyword)}\b", query) is not None
    return keyword in query


def _clamp_score(score: float) -> float:
    return max(0.0, min(1.0, score))


def _load_debate_payload(ticker: str) -> dict[str, Any]:
    path = settings.debates_dir / ticker.upper() / "latest_debate.json"
    if not path.exists():
        raise FileNotFoundError(f"No latest_debate.json found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _build_dummy_pack_from_debate(ticker: str, payload: dict[str, Any]) -> ContextPack:
    raw_summary = str(payload.get("raw_data_summary") or "")
    verdict = payload.get("verdict") if isinstance(payload.get("verdict"), dict) else {}
    metadata = (
        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    )
    raw_data = {
        "ticker": ticker,
        "generated_at": metadata.get("generated_at"),
        "current_price": verdict.get("current_price"),
        "fair_value": verdict.get("fair_value"),
        "fundamentals": {"brief": _extract_summary_section(raw_summary, "fundamental")},
        "technicals": {"brief": _extract_summary_section(raw_summary, "technical")},
        "sentiment_summary": _extract_summary_section(raw_summary, "sentiment"),
        "data_sources": _extract_data_sources(raw_summary, metadata),
    }
    return build_context_pack(ticker, raw_data)


def _extract_summary_section(raw_summary: str, section: str) -> str:
    if not raw_summary:
        return ""

    patterns = {
        "fundamental": (
            r"Fundamental Brief:\s*(.*?)(?:\n\nSentiment Brief:|\n\n=== TECHNICALS ===|\Z)",
            r"=== FUNDAMENTALS ===\s*(.*?)(?:\n\n=== TECHNICALS ===|\Z)",
        ),
        "technical": (
            r"Technical Indicators:\s*(.*?)(?:\n\nFundamental Brief:|\Z)",
            r"=== TECHNICALS ===\s*(.*?)(?:\n\n=== SENTIMENT ===|\Z)",
        ),
        "sentiment": (
            r"Sentiment Brief:\s*(.*)\Z",
            r"=== SENTIMENT ===\s*(.*?)(?:\n\n=== DIVIDEND EX-DATE SCAN:|\Z)",
        ),
    }
    for pattern in patterns[section]:
        match = re.search(pattern, raw_summary, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return raw_summary.strip() if section == "fundamental" else ""


def _extract_data_sources(raw_summary: str, metadata: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    match = re.search(r"Data Sources:\s*(.+)", raw_summary)
    if match:
        sources.extend(
            part.strip() for part in match.group(1).split(",") if part.strip()
        )
    for key in ("market_data_source", "fundamental_source", "sentiment_source"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            sources.append(value.strip())

    unique_sources: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if source not in seen:
            seen.add(source)
            unique_sources.append(source)
    return unique_sources


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an evidence brief for a ticker."
    )
    parser.add_argument(
        "--ticker", required=True, help="Ticker with latest_debate.json output."
    )
    args = parser.parse_args()

    payload = _load_debate_payload(args.ticker)
    pack = _build_dummy_pack_from_debate(args.ticker.upper(), payload)
    run_id = str(payload.get("metadata", {}).get("run_id") or "manual-cli")
    bundle = DEFAULT_RANKER.build_bundle(pack, run_id=run_id)
    print(DEFAULT_RANKER.bundle_to_prompt_string(bundle))


DEFAULT_RANKER = EvidenceRanker()


if __name__ == "__main__":
    main()
