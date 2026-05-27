"""Small lexicon classifier for Stockbit sentiment persistence."""

from __future__ import annotations

import re


BULLISH_KEYWORDS = [
    "naik",
    "bullish",
    "beli",
    "buy",
    "mantap",
    "bagus",
    "cuan",
    "profit",
    "rekomendasi",
    "rebound",
    "breakout",
    "growth",
    "positif",
    "kuat",
    "rally",
    "uptrend",
    "potensi",
    "dividen",
    "untung",
    "meningkat",
    "strong",
    "target",
    "accumulate",
    "long",
]

BEARISH_KEYWORDS = [
    "turun",
    "bearish",
    "jual",
    "sell",
    "rugi",
    "loss",
    "negatif",
    "jelek",
    "anjlok",
    "koreksi",
    "downtrend",
    "lemah",
    "cut loss",
    "hindari",
    "bahaya",
    "nyangkut",
    "jeblok",
    "merosot",
    "drop",
    "weak",
    "support",
    "broken",
    "short",
    "collapse",
]

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def classify_sentiment_lexicon(text: str) -> tuple[str, float]:
    lowered = str(text or "").lower()
    tokens = _TOKEN_RE.findall(lowered)
    token_text = " ".join(tokens)

    bullish_count = _keyword_count(tokens, token_text, BULLISH_KEYWORDS)
    bearish_count = _keyword_count(tokens, token_text, BEARISH_KEYWORDS)
    total = bullish_count + bearish_count

    if bullish_count > bearish_count:
        return "BULLISH", min(1.0, bullish_count / (total + 1))
    if bearish_count > bullish_count:
        return "BEARISH", min(1.0, bearish_count / (total + 1))
    return "NEUTRAL", 0.5


def _keyword_count(tokens: list[str], token_text: str, keywords: list[str]) -> int:
    count = 0
    for keyword in keywords:
        normalized = " ".join(_TOKEN_RE.findall(keyword.lower()))
        if not normalized:
            continue
        if " " in normalized:
            count += len(
                re.findall(rf"(?<!\w){re.escape(normalized)}(?!\w)", token_text)
            )
        else:
            count += tokens.count(normalized)
    return count
