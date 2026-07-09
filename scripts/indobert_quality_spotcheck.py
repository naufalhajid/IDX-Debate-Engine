"""
One-off diagnostic (V4.6a follow-up): capture (post text, IndoBERT label,
score) triples on real Stockbit posts.

The production ledger (output/ledger/execution_ledger.jsonl) only stores
aggregated counts per run — never the post text — so IndoBERT label quality
has never been directly inspectable. This script re-fetches real posts via
the same DebateChamber helpers the live sentiment node uses, then classifies
them the same way `services.indobert_sentiment.sentiment_prior` does, but
keeps text and label paired for manual review.

Read-only and side-effect-free: no LLM calls (Stockbit fetch + local
CPU inference only), nothing written to the production ledger/state.
Output: output/diagnostics/indobert_spotcheck_<timestamp>.json
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from services.debate_chamber import BASE_URL, DebateChamber
from services.indobert_sentiment import (
    _LABEL_MAP,
    _MAX_CHARS,
    _MAX_POSTS,
    _get_pipeline,
    extract_post_text,
)

TICKERS = ["BBCA", "ADRO", "MYOR", "MAPI", "TLKM"]
OUT_DIR = Path("output/diagnostics")


async def _fetch_combined_posts(chamber: DebateChamber, ticker: str) -> list[dict]:
    pinned_raw, ideas_posts, news_posts = await asyncio.gather(
        chamber._fetch_sentiment_endpoint(
            ticker, "pinned", f"{BASE_URL}/stream/v3/symbol/{ticker}/pinned"
        ),
        chamber._fetch_sentiment_stream_posts(ticker, "STREAM_CATEGORY_IDEAS"),
        chamber._fetch_sentiment_stream_posts(ticker, "STREAM_CATEGORY_NEWS"),
    )
    pinned_posts = DebateChamber._extract_stockbit_posts(pinned_raw)
    return DebateChamber._merge_stockbit_posts(
        pinned_posts, ideas_posts, news_posts, ticker=ticker
    )


async def main() -> None:
    chamber = DebateChamber()
    pipe = _get_pipeline()
    if pipe is None:
        print("IndoBERT pipeline failed to load — aborting")
        return

    rows: list[dict] = []
    for ticker in TICKERS:
        combined = await _fetch_combined_posts(chamber, ticker)
        texts: list[str] = []
        sources: list[dict] = []
        for post in combined[:_MAX_POSTS]:
            text = extract_post_text(post)
            if text:
                texts.append(text[:_MAX_CHARS])
                sources.append(post)
        if not texts:
            print(f"{ticker}: no text posts, skip")
            continue

        raw = pipe(texts, truncation=True, max_length=512)
        for post, text, item in zip(sources, texts, raw):
            label = _LABEL_MAP.get(
                str(item.get("label", "")).lower(), str(item.get("label", "unknown"))
            )
            score = float(item.get("score") or 0.0)
            rows.append(
                {
                    "ticker": ticker,
                    "stream_id": post.get("stream_id"),
                    "text": text,
                    "label": label,
                    "score": round(score, 3),
                }
            )
        print(f"{ticker}: classified {len(texts)} posts (of {len(combined)} fetched)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUT_DIR / f"indobert_spotcheck_{stamp}.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(rows)} (text, label, score) rows to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
