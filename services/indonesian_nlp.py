"""Indonesian NLP utilities for IDX financial text preprocessing and language detection."""
from __future__ import annotations

import re
import unicodedata

# Documented for reference only.
# Do NOT strip these before keyword matching — tokens like "saham" and "bursa"
# are load-bearing in news_fetcher.py INSIDER_SELLING and MACRO keyword lists.
INDONESIAN_FINANCIAL_STOPWORDS = frozenset([
    "dan", "yang", "di", "dengan", "untuk", "dari", "tidak", "ini",
    "adalah", "pada", "ke", "juga", "akan", "sudah", "atau", "dalam",
    "oleh", "tetapi", "karena", "bahwa", "setelah", "telah", "dapat",
    "jadi", "lebih", "sangat", "namun", "itu", "serta", "seperti",
    "kami", "kita", "mereka", "dia", "ia", "saat", "ketika", "hal",
])

# High-frequency tokens found predominantly in Bahasa Indonesia financial text.
# Chosen to not overlap with IDX tickers or critical keyword lists.
_ID_MARKERS = frozenset([
    "dan", "yang", "di", "dengan", "untuk", "dari", "tidak", "ini",
    "adalah", "pada", "ke", "juga", "akan", "sudah", "atau", "dalam",
    "oleh", "tetapi", "karena", "bahwa", "setelah", "telah", "dapat",
    "naik", "turun", "laba", "rugi", "bursa", "persen", "triwulan",
    "kuartal", "dividen", "kinerja", "bersih", "tbk", "pt", "emiten",
    "saham", "menguat", "melemah", "semester", "kenaikan", "penurunan",
    "perusahaan", "menyatakan", "mencatat", "umumkan", "berhasil",
    "menurut", "laporan", "pendapatan", "pertumbuhan", "meningkat",
])

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MULTI_WS_RE = re.compile(r"\s+")


def preprocess_indonesian_text(text: str) -> str:
    """URL removal and whitespace normalization for IDX financial text.

    Language-agnostic: applies to both Indonesian and English text. Removes
    URLs and Unicode artifacts that would pollute keyword matches without
    stripping any load-bearing tokens.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _URL_RE.sub(" ", text)
    text = _MULTI_WS_RE.sub(" ", text).strip()
    return text


def detect_language(text: str) -> str:
    """Heuristic language detection from IDX financial text.

    Returns 'id' (Bahasa Indonesia), 'en' (English), or 'mixed'.
    Uses ratio of Indonesian marker words — reliable for IDX news titles
    and Stockbit social posts which are predominantly Bahasa Indonesia.
    """
    words = text.lower().split()
    if not words:
        return "en"
    id_count = sum(1 for w in words if w in _ID_MARKERS)
    ratio = id_count / len(words)
    if ratio >= 0.15:
        return "id"
    if ratio >= 0.05:
        return "mixed"
    return "en"
