"""Tests for services/indobert_sentiment.py (task D1 / Gap P7 Tier 2).

The real model is never loaded here: the lazy pipeline getter is patched so
tests cover the label mapping, aggregation, prior-block formatting, and every
graceful-degradation path (disabled, no text, load failure, inference error).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import indobert_sentiment


@pytest.fixture(autouse=True)
def _reset_pipeline_singleton(monkeypatch):
    monkeypatch.setattr(indobert_sentiment, "_pipe", None)
    monkeypatch.setattr(indobert_sentiment, "_pipe_failed", False)


def _settings(enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        SENTIMENT_INDOBERT_ENABLED=enabled,
        SENTIMENT_INDOBERT_MODEL="fake/indobert-sentiment",
    )


def test_disabled_returns_empty_prior(monkeypatch):
    monkeypatch.setattr(
        indobert_sentiment, "get_settings", lambda: _settings(enabled=False)
    )

    block, stats = indobert_sentiment.sentiment_prior([{"content": "BBRI naik"}])

    assert block == ""
    assert stats == {}


def test_posts_without_text_return_empty_prior(monkeypatch):
    monkeypatch.setattr(indobert_sentiment, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        indobert_sentiment,
        "_get_pipeline",
        lambda: pytest.fail("pipeline must not be loaded when no post has text"),
    )

    block, stats = indobert_sentiment.sentiment_prior([{"like_count": 3}, {}])

    assert block == ""
    assert stats == {}


def test_load_failure_degrades_to_empty_prior(monkeypatch):
    monkeypatch.setattr(indobert_sentiment, "get_settings", lambda: _settings())
    monkeypatch.setattr(indobert_sentiment, "_get_pipeline", lambda: None)

    block, stats = indobert_sentiment.sentiment_prior([{"content": "UNVR turun"}])

    assert block == ""
    assert stats == {}


def test_inference_error_degrades_to_empty_prior(monkeypatch):
    def _boom(texts, **kwargs):
        raise RuntimeError("inference exploded")

    monkeypatch.setattr(indobert_sentiment, "get_settings", lambda: _settings())
    monkeypatch.setattr(indobert_sentiment, "_get_pipeline", lambda: _boom)

    block, stats = indobert_sentiment.sentiment_prior([{"content": "TLKM stabil"}])

    assert block == ""
    assert stats == {}


def test_prior_block_maps_labels_and_aggregates(monkeypatch):
    # Covers both label conventions: LABEL_i (mdhugol IndoBERT fine-tune)
    # and human-readable labels from alternative models.
    def _fake_pipe(texts, **kwargs):
        assert len(texts) == 3
        return [
            {"label": "LABEL_0", "score": 0.95},   # positive, high-confidence
            {"label": "LABEL_2", "score": 0.60},   # negative, low-confidence
            {"label": "neutral", "score": 0.80},   # readable label passthrough
        ]

    monkeypatch.setattr(indobert_sentiment, "get_settings", lambda: _settings())
    monkeypatch.setattr(indobert_sentiment, "_get_pipeline", lambda: _fake_pipe)

    posts = [
        {"content": "BBRI laba naik 20%"},
        {"message": "UNVR margin tertekan"},
        {"text": "TLKM sideways menunggu katalis"},
    ]
    block, stats = indobert_sentiment.sentiment_prior(posts)

    assert stats["classified"] == 3
    assert stats["positive"] == 1
    assert stats["neutral"] == 1
    assert stats["negative"] == 1
    assert stats["high_confidence"] == 2
    assert stats["mean_score"] == pytest.approx((0.95 + 0.60 + 0.80) / 3, abs=1e-3)
    assert "INDOBERT SENTIMENT PRIOR" in block
    assert "positive=1" in block
    assert '"label": "negative"' in block
    assert "fake/indobert-sentiment" in block


def test_extract_post_text_priority():
    post = {"body": "d", "text": "c", "message": "b", "content": "a"}

    assert indobert_sentiment.extract_post_text(post) == "a"
    assert indobert_sentiment.extract_post_text({"message": " b "}) == "b"
    assert indobert_sentiment.extract_post_text({"content": "   "}) == ""
    assert indobert_sentiment.extract_post_text({}) == ""
