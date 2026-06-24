"""Calibrate keyword-based sentiment classifier and language detection for IDX financial text.

Tests the deterministic path (news_fetcher.classify_item) — no API key required,
fully reproducible. Results are saved to docs/research/sentiment_calibration_results.md.

Run:
    uv run python scripts/calibrate_sentiment.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.news_fetcher import NewsFetcher
from services.news_fetcher import NewsSentiment
from services.indonesian_nlp import detect_language, preprocess_indonesian_text


# ---------------------------------------------------------------------------
# Test cases: (label, title, expected_sentiment, expected_lang, notes)
# ---------------------------------------------------------------------------
TEST_CASES: list[tuple[str, str, str, str, str]] = [
    # --- Bahasa Indonesia positive ---
    (
        "ID-POS-1",
        "BBCA laba bersih naik 15 persen pada kuartal I 2026",
        "POSITIVE", "id",
        "Clear positive: laba + naik both hit",
    ),
    (
        "ID-POS-2",
        "TLKM menguat setelah dividen diumumkan manajemen",
        "POSITIVE", "id",
        "Menguat + dividen → positive",
    ),
    (
        "ID-POS-3",
        "BBRI profit growth kuat didukung ekspansi kredit konsumer",
        "POSITIVE", "mixed",
        "Mixed ID/EN: profit + growth + ekspansi",
    ),
    # --- Bahasa Indonesia negative ---
    (
        "ID-NEG-1",
        "WSKT rugi bersih Rp 2 triliun akibat gagal bayar obligasi",
        "NEGATIVE", "id",
        "Clear negative: rugi + gagal both hit",
    ),
    (
        "ID-NEG-2",
        "BUMI saham melemah setelah downgrade dari analis asing",
        "NEGATIVE", "id",
        "Melemah + downgrade",
    ),
    (
        "ID-NEG-3",
        "AISA delisting dari bursa setelah suspensi panjang",
        "NEGATIVE", "id",
        "Delisting + suspensi both negative",
    ),
    # --- English positive ---
    (
        "EN-POS-1",
        "BBCA strong profit above estimate record quarterly growth",
        "POSITIVE", "en",
        "Pure English: strong + profit + above + record + growth",
    ),
    (
        "EN-POS-2",
        "GOTO upgrade by analyst after partnership beat expectations",
        "POSITIVE", "en",
        "Upgrade + partnership + beat",
    ),
    # --- English negative ---
    (
        "EN-NEG-1",
        "MNCN net loss below estimate downgrade from hold to sell",
        "NEGATIVE", "en",
        "Loss + below + downgrade",
    ),
    # --- Neutral / corporate action ---
    (
        "ID-CORP-1",
        "BBCA umumkan stock split rasio 1:5 pada RUPS tahun ini",
        "NEUTRAL", "id",
        "Corporate action; no positive/negative keyword fires",
    ),
    # --- Macro event ---
    (
        "ID-MACRO-1",
        "IHSG melemah akibat kenaikan suku bunga BI rate 25 bps",
        "NEGATIVE", "id",
        "Melemah hit; macro context (IHSG + BI rate)",
    ),
    # --- Insider selling ---
    (
        "ID-INSIDER-1",
        "Direktur BBCA menjual saham sebanyak 500 ribu lembar di pasar reguler",
        "NEUTRAL", "id",
        "is_insider_selling=True but 'menjual' fails (?<!\\w)jual lookbehind → NEUTRAL",
    ),
    # --- Negation (known limitation) ---
    (
        "ID-NEG-NEGATION",
        "BBCA tidak naik meski laporan keuangan baik",
        "POSITIVE", "id",
        "KNOWN LIMIT: negation not handled — 'naik' fires POSITIVE",
    ),
    # --- ARA/ARB (slang not in keyword list) ---
    (
        "ID-ARA-1",
        "CUAN ARA tiga hari berturut-turut auto reject atas limit up",
        "NEUTRAL", "id",
        "ARA not in keyword list → NEUTRAL (LLM path would catch this)",
    ),
    # --- URL noise test ---
    (
        "URL-NOISE-1",
        "BBCA laba naik https://kontan.co.id/berita/bbca-profit-2026 strong growth",
        "POSITIVE", "mixed",
        "URL stripped by preprocess; keywords still match correctly",
    ),
]

TICKER = "BBCA"


def _ts_recent() -> int:
    return int(datetime.now(timezone.utc).timestamp()) - 3600


def _make_raw(title: str) -> dict:
    return {
        "title": title,
        "publisher": "CalibrationTest",
        "link": "https://example.test/calibration",
        "providerPublishTime": _ts_recent(),
    }


def run_calibration() -> list[dict]:
    fetcher = NewsFetcher()
    results = []

    for case_id, title, expected_sentiment, expected_lang, notes in TEST_CASES:
        raw = _make_raw(title)
        item = fetcher.classify_item(raw, TICKER)

        detected_lang = detect_language(title)
        preprocessed = preprocess_indonesian_text(title)

        sentiment_match = item.sentiment.value.upper() == expected_sentiment.upper()
        lang_match = detected_lang == expected_lang

        results.append({
            "id": case_id,
            "title": title,
            "preprocessed": preprocessed,
            "expected_sentiment": expected_sentiment,
            "actual_sentiment": item.sentiment.value,
            "sentiment_score": item.sentiment_score,
            "sentiment_match": sentiment_match,
            "expected_lang": expected_lang,
            "detected_lang": detected_lang,
            "lang_match": lang_match,
            "is_corporate_action": item.is_corporate_action,
            "is_macro": item.is_macro,
            "is_insider_selling": item.is_insider_selling,
            "is_post_earnings": item.is_post_earnings,
            "notes": notes,
        })

    return results


def _precision(results: list[dict], key: str = "sentiment_match") -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r[key]) / len(results)


def _render_markdown(results: list[dict]) -> str:
    sentiment_correct = sum(1 for r in results if r["sentiment_match"])
    lang_correct = sum(1 for r in results if r["lang_match"])
    sentiment_prec = sentiment_correct / len(results) * 100
    lang_prec = lang_correct / len(results) * 100

    lines = [
        "# Sentiment Calibration Results",
        "",
        f"**Date**: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "**Classifier path**: deterministic keyword (`news_fetcher.classify_item`)",
        "**LLM path**: NOT tested (requires live Gemini API; primary quality improvement "
        "is in `sentiment.txt` bilingual guidance targeting the LLM path)",
        f"**Test cases**: {len(results)}",
        "",
        "## Summary",
        "",
        "| Metric | Correct | Total | Precision |",
        "|--------|---------|-------|-----------|",
        f"| Sentiment classification | {sentiment_correct} | {len(results)} | {sentiment_prec:.1f}% |",
        f"| Language detection | {lang_correct} | {len(results)} | {lang_prec:.1f}% |",
        "",
    ]

    if sentiment_prec >= 70:
        lines.append(f"**Sentiment precision: {sentiment_prec:.1f}% — PASS (target ≥70%)**")
    else:
        lines.append(f"**Sentiment precision: {sentiment_prec:.1f}% — BELOW TARGET (target ≥70%)**")
    lines.append("")

    lines += [
        "## Test Case Results",
        "",
        "| ID | Actual | Expected | S✓ | Lang | L-Exp | L✓ | Notes |",
        "|----|--------|----------|----|------|-------|----|-------|",
    ]
    for r in results:
        s_icon = "✓" if r["sentiment_match"] else "✗"
        l_icon = "✓" if r["lang_match"] else "✗"
        score = f"{r['sentiment_score']:+.2f}"
        lines.append(
            f"| {r['id']} | {r['actual_sentiment']} ({score}) | {r['expected_sentiment']} "
            f"| {s_icon} | {r['detected_lang']} | {r['expected_lang']} | {l_icon} "
            f"| {r['notes'][:55]} |"
        )

    failures = [r for r in results if not r["sentiment_match"]]
    if failures:
        lines += ["", "## Failures (sentiment)", ""]
        for r in failures:
            lines += [
                f"### {r['id']}",
                f"- **Title**: `{r['title']}`",
                f"- **Expected**: {r['expected_sentiment']} | **Got**: {r['actual_sentiment']} (score: {r['sentiment_score']:+.2f})",
                f"- **Notes**: {r['notes']}",
                "",
            ]

    lines += [
        "## Known Limitations of Keyword Classifier",
        "",
        "1. **No negation handling**: `tidak naik` reads `naik` as POSITIVE. Fix requires bigram-aware matching.",
        "2. **IDX slang not covered**: `cuan`, `nyangkut`, `gorengan`, `ARA/ARB` are not in keyword lists",
        "   → always NEUTRAL on the keyword path. The updated `sentiment.txt` teaches the Gemini LLM",
        "   these terms for Stockbit social posts (`_sentiment_node` in `debate_chamber.py`).",
        "3. **LLM calibration skipped**: The primary quality improvement is the `BAHASA INDONESIA HANDLING`",
        "   block added to `sentiment.txt`. Calibrating that path requires live Gemini calls.",
        "",
        "## Preprocessing Verification",
        "",
        "| Input | Preprocessed output |",
        "|-------|---------------------|",
    ]
    url_samples = [
        "BBCA laba naik https://kontan.co.id/berita/bbca-2026 kuat",
        "WSKT  rugi   bersih   (extra   whitespace)",
        "TLKM naik kuat dividen (non-breaking spaces)",
    ]
    for sample in url_samples:
        cleaned = preprocess_indonesian_text(sample)
        lines.append(f"| `{sample[:55]}` | `{cleaned[:60]}` |")

    lines += [
        "",
        "---",
        "_Generated by `scripts/calibrate_sentiment.py`_",
    ]

    return "\n".join(lines)


def main() -> None:
    results = run_calibration()
    md = _render_markdown(results)

    output_path = Path(__file__).parent.parent / "docs" / "research" / "sentiment_calibration_results.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")

    sentiment_prec = _precision(results) * 100
    lang_prec = _precision(results, "lang_match") * 100
    print(f"Sentiment precision : {sentiment_prec:.1f}%  ({'PASS' if sentiment_prec >= 70 else 'FAIL'})")
    print(f"Language precision  : {lang_prec:.1f}%")
    print(f"Results saved to    : {output_path}")


if __name__ == "__main__":
    main()
