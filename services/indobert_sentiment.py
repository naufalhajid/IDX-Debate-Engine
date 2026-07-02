"""
IndoBERT sentiment prior for the sentiment scout (Gap P7 Tier 2, task D1).

Classifies Bahasa Indonesia Stockbit posts with a fine-tuned IndoBERT
sentiment model BEFORE the LLM judges overall sentiment, so the scout gets a
deterministic per-post prior instead of relying on zero-shot multilingual
classification. ID-SMSA benchmarking puts IndoBERT at ~85% F1 on Indonesian
financial text vs ~65-70% for zero-shot multilingual models
(docs/research/gap_analysis_report.md, GAP-07).

Opt-in via SENTIMENT_INDOBERT_ENABLED=True. First use downloads the model
(~500 MB) from HuggingFace and requires the optional dependencies
(`uv sync --extra sentiment`). Every failure mode — missing dependency,
download error, inference error — degrades to the pre-D1 LLM-only behavior
by returning an empty prior.

The gap report's pseudo-code loads indolem/indobert-base-uncased, which is a
BASE checkpoint without a sentiment head (its classification output would be
untrained noise); the default model here is an IndoBERT sentiment fine-tune
instead, and the name is configurable via SENTIMENT_INDOBERT_MODEL.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from loguru import logger

from core.settings import get_settings

# mdhugol/indonesia-bert-sentiment-classification emits LABEL_0/1/2 in this
# order; other Indonesian sentiment fine-tunes emit readable labels. Unknown
# labels pass through lowercased so a swapped model stays inspectable.
_LABEL_MAP = {
    "label_0": "positive",
    "label_1": "neutral",
    "label_2": "negative",
    "positive": "positive",
    "neutral": "neutral",
    "negative": "negative",
}
_MAX_POSTS = 40  # bound CPU latency; sentiment streams are paginated ~60 max
_MAX_CHARS = 512
_HIGH_CONFIDENCE = 0.70  # gap-report threshold: prior overridable below this

_pipe: Any = None
_pipe_failed = False
_pipe_lock = threading.Lock()


def _get_pipeline() -> Any:
    """Lazy singleton; returns None (and stops retrying) after a load failure."""
    global _pipe, _pipe_failed
    if _pipe is not None or _pipe_failed:
        return _pipe
    with _pipe_lock:
        if _pipe is not None or _pipe_failed:
            return _pipe
        model_name = get_settings().SENTIMENT_INDOBERT_MODEL
        try:
            from transformers import pipeline

            _pipe = pipeline(
                "text-classification",
                model=model_name,
                device=-1,  # CPU is enough for <=40 short posts per ticker
            )
            logger.info("[IndoBERT] model loaded: {}", model_name)
        except Exception as exc:
            _pipe_failed = True
            logger.warning(
                "[IndoBERT] disabled after load failure ({}: {}) — "
                "sentiment scout continues LLM-only",
                type(exc).__name__,
                exc,
            )
    return _pipe


def extract_post_text(post: dict[str, Any]) -> str:
    """Same content-key priority as _compact_stockbit_post_for_llm."""
    for key in ("content", "message", "text", "body"):
        value = post.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def sentiment_prior(posts: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Classify posts and format the deterministic prior block.

    Synchronous (call via asyncio.to_thread from async nodes). Returns
    ("", {}) when the feature is disabled, no post carries text, or the model
    is unavailable — callers can append the block text unconditionally.
    """
    settings = get_settings()
    if not settings.SENTIMENT_INDOBERT_ENABLED:
        return "", {}

    texts = []
    for post in posts[:_MAX_POSTS]:
        text = extract_post_text(post)
        if text:
            texts.append(text[:_MAX_CHARS])
    if not texts:
        return "", {}

    pipe = _get_pipeline()
    if pipe is None:
        return "", {}
    try:
        raw = pipe(texts, truncation=True, max_length=512)
    except Exception as exc:
        logger.warning("[IndoBERT] inference failed ({}): {}", type(exc).__name__, exc)
        return "", {}

    results: list[dict[str, Any]] = []
    for item in raw:
        label = _LABEL_MAP.get(str(item.get("label", "")).lower())
        if label is None:
            label = str(item.get("label", "unknown")).lower()
        try:
            score = float(item.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        results.append({"label": label, "score": score})
    if not results:
        return "", {}

    mean_score = sum(r["score"] for r in results) / len(results)
    stats = {
        "model": settings.SENTIMENT_INDOBERT_MODEL,
        "classified": len(results),
        "positive": sum(1 for r in results if r["label"] == "positive"),
        "neutral": sum(1 for r in results if r["label"] == "neutral"),
        "negative": sum(1 for r in results if r["label"] == "negative"),
        "mean_score": round(mean_score, 3),
        "high_confidence": sum(
            1 for r in results if r["score"] >= _HIGH_CONFIDENCE
        ),
    }

    per_post = json.dumps(
        [
            {"i": i + 1, "label": r["label"], "score": round(r["score"], 2)}
            for i, r in enumerate(results)
        ],
        ensure_ascii=False,
    )
    block = (
        "=== INDOBERT SENTIMENT PRIOR (deterministic, Python-computed) ===\n"
        f"Model: {stats['model']} | Posts classified: {stats['classified']}\n"
        f"Distribution: positive={stats['positive']}, "
        f"neutral={stats['neutral']}, negative={stats['negative']}\n"
        f"Mean confidence: {stats['mean_score']:.2f} | "
        f"High-confidence (>={_HIGH_CONFIDENCE:.2f}): "
        f"{stats['high_confidence']}/{stats['classified']}\n"
        f"Per-post labels (ordered like the posts JSON above): {per_post}\n"
        "Rule: IndoBERT is benchmarked stronger than multilingual models on "
        "Bahasa Indonesia financial text. Use these labels as the sentiment "
        f"prior; override a high-confidence (>={_HIGH_CONFIDENCE:.2f}) prior "
        "only with explicit contrary evidence from the post/news content."
    )
    return block, stats
